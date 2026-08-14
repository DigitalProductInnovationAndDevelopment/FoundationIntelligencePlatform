"""Collector for the Philea member directory.

Fetches European foundation member records, caching raw responses for reproducible
downstream consolidation. Requests are retried with backoff and paced by a sleep
interval.

Philea supplies **organization metadata only** — there are no grant transactions in this
source. Records derived from it are marked ``organization_level_only`` downstream so the
UI reports absent transaction data rather than zero activity, and no funding activity is
ever inferred from membership.
"""

import json
import requests
import time
import argparse
import logging
import os
from bs4 import BeautifulSoup, NavigableString

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

def scrape(limit=None, sleep_time=3.0, timeout=10.0):
    url_members = "https://philea.eu/wp-admin/admin-ajax.php"
    payload = {"action": "phileamap"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    logging.info(f"Retrieving members list from {url_members}...")
    try:
        response = make_request("POST", url_members, data=payload, headers=headers, timeout=timeout)
        if response.status_code != 200:
            logging.error(f"Failed to retrieve data: HTTP {response.status_code}")
            return []
        members = response.json()["data"]
    except Exception as e:
        logging.error(f"Critical error fetching members list: {e}")
        return []
        
    if limit:
        members = members[:limit]
        
    todo = len(members)
    logging.info(f"Successfully retrieved {todo} members. Starting scraping...")
    
    counter = 0
    scraped_successfully = 0
    
    for member in members:
        counter += 1
        
        member_name = member.get("name", "Unknown Name")
        member_link = member.get("link")
        
        logging.info(f"Processing {counter}/{todo}: {member_name}")
        
        philea_data = {}
        if not member_link:
            logging.warning(f"No link found for member: {member_name}. Skipping details.")
            member["philea_info"] = philea_data
            continue
            
        try:
            response = make_request("GET", member_link, headers=headers, timeout=timeout)
            soup = BeautifulSoup(response.content, "html.parser")
            content = soup.find("div", {"class": "article-body"})
            
            if content:
                first_p = content.find("p")
                philea_data["About"] = first_p.text.strip() if first_p else ""
                h3s = content.find_all("h3")
                for h3 in h3s:
                    key = h3.text.strip()
                    value_parts = []
                    
                    # Wir wandern von der Überschrift aus vorwärts
                    current = h3.next_sibling
                    
                    while current:
                        # Wenn wir auf die NÄCHSTE Überschrift stoßen, stoppen wir sofort
                        if current.name == "h3":
                            break
                        
                        # Wenn es sich um reinen Text (ohne HTML-Tag) handelt
                        if isinstance(current, NavigableString):
                            text = current.strip()
                            if text:  # Verhindert das Aufnehmen von leeren Zeilenumbrüchen
                                value_parts.append(text)
                        
                        # Wenn es ein HTML-Tag ist (p, ul, div, etc.), holen wir uns den Text
                        elif current.name:
                            text = current.text.strip()
                            if text:
                                value_parts.append(text)
                        
                        # Gehe zum nächsten Geschwister-Element
                        current = current.next_sibling
                    
                    # Füge alle gefundenen Textteile zusammen
                    philea_data[key] = " ".join(value_parts)
                scraped_successfully += 1
            else:
                logging.warning(f"Could not find 'article-body' div for member: {member_name}")
                philea_data["About"] = ""
                
        except Exception as e:
            logging.error(f"Error scraping details for {member_name} ({member_link}): {e}")
            philea_data["About"] = ""
            philea_data["Error"] = str(e)
            
        member["philea_info"] = philea_data
        time.sleep(sleep_time)
        
    logging.info(f"Finished scraping. Success rate: {scraped_successfully}/{counter}")
    return members
      
def save_data(members, path):
    logging.info(f"Saving scraped data to {path}...")
    try:
        # Auto-create output directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(members, f, ensure_ascii=False, indent=4)
        logging.info("Data saved successfully.")
    except Exception as e:
        logging.error(f"Failed to save data to {path}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Philea organization details.")
    parser.add_argument(
        "--output", 
        type=str, 
        default=os.path.join(os.path.dirname(__file__), "../data/raw/philea_members.json"),
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

    members = scrape(limit=args.limit, sleep_time=args.sleep, timeout=args.timeout)
    if members:
        save_data(members, args.output)
    else:
        logging.error("No data scraped. File was not saved.")