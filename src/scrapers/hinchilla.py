import json
import requests
import time
import argparse
import logging
import os
import re
from bs4 import BeautifulSoup

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

def make_request(method, url, max_retries=3, backoff_factor=2, timeout=10, **kwargs):
    """
    Make an HTTP request with automatic retries, exponential backoff, and timeout.
    """
    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            # Retry on transient server errors or rate limits
            if response.status_code in [429, 500, 502, 503, 504]:
                logging.warning(
                    f"Transient HTTP status {response.status_code} for URL: {url}. "
                    f"Attempt {attempt + 1}/{max_retries}"
                )
                if attempt < max_retries - 1:
                    time.sleep(backoff_factor ** attempt)
                    continue
            return response
        except requests.exceptions.RequestException as e:
            logging.warning(
                f"Request failed for URL: {url} (Error: {e}). "
                f"Attempt {attempt + 1}/{max_retries}"
            )
            if attempt < max_retries - 1:
                time.sleep(backoff_factor ** attempt)
                continue
            raise e
    raise requests.exceptions.RequestException(
        f"Failed to fetch {url} after {max_retries} attempts."
    )

def parse_rsc_payload(content):
    """
    Parse a Next.js App Router Server Components RSC stream payload.
    Correctly decodes length-prefixed text blocks (T[hex],) and normal line-based blocks.
    """
    pointer = 0
    parsed_blocks = {}
    
    while pointer < len(content):
        # Match a key at current position, ignoring any leading newlines/whitespace
        match = re.match(r'^[\r\n]*([0-9a-fA-F\w]+):', content[pointer:])
        if not match:
            # Advance if no match (e.g. whitespace, trailing characters)
            pointer += 1
            continue
            
        key = match.group(1)
        pointer += match.end()
        
        # Check if it is a text block format: T[hex],
        text_match = re.match(r'^T([0-9a-fA-F]+),', content[pointer:])
        if text_match:
            hex_len = text_match.group(1)
            length = int(hex_len, 16)
            pointer += text_match.end()
            
            val = content[pointer:pointer+length]
            parsed_blocks[key] = {
                "type": "text",
                "content": val
            }
            pointer += length
        else:
            # Standard line-based segment (usually JSON payload, HL, or I)
            newline_idx = content.find("\n", pointer)
            if newline_idx == -1:
                val = content[pointer:]
                pointer = len(content)
            else:
                val = content[pointer:newline_idx]
                pointer = newline_idx + 1
                
            parsed_blocks[key] = {
                "type": "json_or_other",
                "content": val
            }
            
    return parsed_blocks

def resolve_rsc_references(blocks, entry_json_str):
    """
    Recursively resolves $key references inside parsed RSC JSON data blocks.
    """
    try:
        data = json.loads(entry_json_str)
    except Exception as e:
        logging.warning(f"Failed to parse entry JSON: {e}")
        return None
        
    def resolve_refs(obj):
        if isinstance(obj, str):
            if obj.startswith("$") and len(obj) > 1:
                ref_key = obj[1:]
                if ref_key in blocks:
                    ref_info = blocks[ref_key]
                    if ref_info["type"] == "text":
                        return ref_info["content"]
                    else:
                        # Try parsing as JSON first, if not return raw
                        try:
                            return json.loads(ref_info["content"])
                        except:
                            return ref_info["content"]
        elif isinstance(obj, dict):
            return {k: resolve_refs(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve_refs(x) for x in obj]
        return obj

    return resolve_refs(data)

def find_sections_container(resolved_data):
    """
    Finds the dict that contains 'sections' and 'data' keys in the resolved RSC payload.
    """
    if isinstance(resolved_data, dict) and "sections" in resolved_data:
        return resolved_data
        
    if isinstance(resolved_data, dict):
        for v in resolved_data.values():
            res = find_sections_container(v)
            if res:
                return res
    elif isinstance(resolved_data, list):
        for x in resolved_data:
            res = find_sections_container(x)
            if res:
                return res
    return None

def parse_quick_stats(text):
    """
    Parses bullet points or markdown tables in Quick Stats text block.
    """
    stats = {}
    if not text:
        return stats
        
    # 1. Matches bullet points: - **Key**: Value
    matches = re.findall(r'-\s*\*\*(.*?)\*\*:\s*(.*)', text)
    for k, v in matches:
        stats[k.strip()] = v.strip()
        
    # 2. Matches markdown table rows: | Key | Value |
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 2:
                key, val = parts[0], parts[1]
                # Skip header rows and separators
                if key.lower() in ["metric", "key", "---"] or "---" in key or "---" in val:
                    continue
                stats[key] = val
    return stats

def get_case_insensitive(d, key_options):
    for opt in key_options:
        for k, v in d.items():
            if k.lower() == opt.lower():
                return v
    return ""

def scrape(limit=None, sleep_time=1.0, timeout=10.0, completed_slugs=None):
    url_directory = "https://www.hinchilla.com/funder-directory"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    logging.info(f"Retrieving members list from {url_directory}...")
    try:
        response = make_request("GET", url_directory, headers=headers, timeout=timeout)
        if response.status_code != 200:
            logging.error(f"Failed to retrieve data: HTTP {response.status_code}")
            return []
        soup = BeautifulSoup(response.content, "html.parser")
    except Exception as e:
        logging.error(f"Critical error fetching directory page: {e}")
        return []
        
    # Extract unique funder links
    links = soup.find_all("a", href=True)
    funder_slugs = []
    seen = set()
    
    for a in links:
        href = a["href"]
        if "/funder-directory/" in href and not href.endswith("/funder-directory") and not href.endswith("/funder-directory/"):
            # Normalize to extract slug
            slug = href.split("/funder-directory/")[-1].strip()
            if slug and slug not in seen:
                seen.add(slug)
                funder_slugs.append(slug)
                
    # Filter completed slugs before counting or applying limit
    if completed_slugs:
        funder_slugs = [s for s in funder_slugs if s not in completed_slugs]
                
    todo = len(funder_slugs)
    logging.info(f"Successfully identified {todo} remaining funder pages to scrape.")
    
    if limit:
        funder_slugs = funder_slugs[:limit]
        todo = len(funder_slugs)
        logging.info(f"Limiting scrape run to {todo} pages.")
        
    members = []
    counter = 0
    scraped_successfully = 0
    
    for slug in funder_slugs:
        counter += 1
        detail_url = f"https://www.hinchilla.com/funder-directory/{slug}.txt"
        logging.info(f"Processing {counter}/{todo}: {slug} -> {detail_url}")
        
        member = {
            "name": slug.replace("-", " ").title(),
            "link": f"https://www.hinchilla.com/funder-directory/{slug}",
            "philea_info": {}
        }
        
        try:
            resp = make_request("GET", detail_url, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                logging.warning(f"Failed to retrieve detail page for {slug}: HTTP {resp.status_code}")
                member["philea_info"]["Error"] = f"HTTP {resp.status_code}"
                members.append(member)
                time.sleep(sleep_time)
                continue
                
            # Parse Next.js RSC payload
            blocks = parse_rsc_payload(resp.text)
            
            # Find the main entry JSON block (contains the sections data)
            sections_json_str = None
            for key, info in blocks.items():
                if info["type"] == "json_or_other" and '"sections"' in info["content"]:
                    sections_json_str = info["content"]
                    break
                    
            if not sections_json_str:
                logging.warning(f"No sections payload found in RSC for {slug}")
                member["philea_info"]["Error"] = "No sections payload found"
                members.append(member)
                time.sleep(sleep_time)
                continue
                
            resolved_data = resolve_rsc_references(blocks, sections_json_str)
            container = find_sections_container(resolved_data)
            
            if not container:
                logging.warning(f"Could not find sections container in resolved RSC payload for {slug}")
                member["philea_info"]["Error"] = "No sections container in resolved payload"
                members.append(member)
                time.sleep(sleep_time)
                continue
                
            # Extract header data dict
            meta_data = container.get("data", {})
            name = meta_data.get("name", member["name"])
            member["name"] = name
            
            # Build sections dict
            sections_list = container.get("sections", [])
            sections_dict = {sec.get("title"): sec.get("content", "") for sec in sections_list}
            
            # Parse Quick Stats bullet points
            quick_stats_text = sections_dict.get("Quick Stats", "")
            quick_stats = parse_quick_stats(quick_stats_text)
            
            # Map sections and extract data
            funding_priorities = sections_dict.get("Funding Priorities", "")
            overview = sections_dict.get("Overview", "")
            area_of_operation = meta_data.get("areaOfOperation", "")
            
            # Geographic Focus includes areaOfOperation, Funding Priorities, and Quick Stats info
            geo_focus_combined = f"Area of Operation: {area_of_operation}\n\n{funding_priorities}\n\n{quick_stats_text}"
            
            member["philea_info"] = {
                # Normal fields expected by extract_geo_topic.py
                "About": overview,
                "Programme Areas": funding_priorities,
                "Geographic Focus": geo_focus_combined,
                
                # Metadata fields
                "charityNumber": meta_data.get("charityNumber", ""),
                "areaOfOperation": area_of_operation,
                "expenditure": meta_data.get("expenditure", ""),
                "website": meta_data.get("website", ""),
                "phone": meta_data.get("phone", ""),
                "email": meta_data.get("email", ""),
                "address": meta_data.get("address", ""),
                "applicationPortal": meta_data.get("applicationPortal", ""),
                
                # Financial stats from Quick Stats
                "annual_giving": get_case_insensitive(quick_stats, ["Annual Giving", "Annual giving"]),
                "success_rate": get_case_insensitive(quick_stats, ["Success Rate", "Success rate"]),
                "decision_time": get_case_insensitive(quick_stats, ["Decision Time", "Decision time"]),
                "grant_range": get_case_insensitive(quick_stats, ["Grant Range", "Grant range", "Average Grant", "Average grant"]),
                "funding_model": get_case_insensitive(quick_stats, ["Funding Model", "Funding model", "Application Method", "Application method"]),
            }
            scraped_successfully += 1
            
        except Exception as e:
            logging.error(f"Error scraping details for {slug}: {e}")
            member["philea_info"] = {
                "About": "",
                "Programme Areas": "",
                "Geographic Focus": "",
                "Error": str(e)
            }
            
        members.append(member)
        time.sleep(sleep_time)
        
    logging.info(f"Finished Hinchilla scraping. Success rate: {scraped_successfully}/{counter}")
    return members

def load_existing_data(path):
    """
    Loads existing raw data from the output path.
    On AWS, this function can be modified to fetch the JSON from an S3 bucket instead.
    """
    if not path or not os.path.exists(path):
        return []
    logging.info(f"Loading existing data from {path}...")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"Could not load existing data: {e}")
        return []

def save_data(members, path):
    """
    Saves the list of members to the output path.
    On AWS, this function can be modified to write the JSON back to an S3 bucket instead.
    """
    logging.info(f"Saving scraped data to {path}...")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(members, f, ensure_ascii=False, indent=4)
        logging.info("Data saved successfully.")
    except Exception as e:
        logging.error(f"Failed to save data to {path}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Hinchilla organization details.")
    parser.add_argument(
        "--output", 
        type=str, 
        default=os.path.join(os.path.dirname(__file__), "../data/raw/hinchilla_members.json"),
        help="Path where the raw JSON file should be saved."
    )
    parser.add_argument(
        "--limit", 
        type=int, 
        default=None, 
        help="Limit the number of organizations to scrape (for testing)."
    )
    parser.add_argument(
        "--sleep", 
        type=float, 
        default=1.0, 
        help="Sleep time in seconds between requests to avoid rate limits."
    )
    parser.add_argument(
        "--timeout", 
        type=float, 
        default=10.0, 
        help="HTTP request timeout in seconds."
    )
    args = parser.parse_args()

    # Load existing data to support resuming
    existing_members = load_existing_data(args.output)
    completed_slugs = set()
    for m in existing_members:
        link = m.get("link", "")
        slug = link.split("/")[-1].strip()
        # Only count as completed if it succeeded without errors
        if slug and "Error" not in m.get("philea_info", {}):
            completed_slugs.add(slug)
            
    if completed_slugs:
        logging.info(f"Resuming: skipping {len(completed_slugs)} already successfully scraped members.")

    new_members = scrape(
        limit=args.limit, 
        sleep_time=args.sleep, 
        timeout=args.timeout, 
        completed_slugs=completed_slugs
    )
    
    # Merge new members with existing ones, overwriting on overlap
    merged_dict = {m["link"]: m for m in existing_members}
    for m in new_members:
        merged_dict[m["link"]] = m
        
    merged_members = list(merged_dict.values())
    
    if new_members or completed_slugs:
        save_data(merged_members, args.output)
    else:
        logging.error("No data scraped and no existing data. File was not saved.")
