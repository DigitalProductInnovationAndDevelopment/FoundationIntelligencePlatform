import os
import json
import re
import logging
from urllib.parse import urlparse

# Configure logger
logger = logging.getLogger("consolidate")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(sh)

# Simple currency conversion rate: 1 GBP = 1.2 EUR
GBP_TO_EUR_RATE = 1.2

def _extract_number(val_str):
    """
    Extracts a numeric float value from a string, ignoring currency symbols and years.
    Handles standard number formats (thousands separators, decimals).
    Reuses the logic from enrich_gemini.py.
    """
    if not val_str or "not publicly available" in val_str.lower() or "not disclosed" in val_str.lower():
        return None
    
    # Remove year patterns like (2024) or [2022] or (2023-24) to avoid matching the year as the number
    cleaned = re.sub(r'[\(\[\{]\d{4}.*?[\)\]\}]', '', val_str)
    
    # Extract digit sequences that might contain periods or commas
    numbers = re.findall(r'\d+(?:[.,]\d+)*', cleaned)
    if not numbers:
        return None
    
    num_str = numbers[0]
    
    # Standardize thousands separator vs decimal separator
    if ',' in num_str and '.' in num_str:
        num_str = num_str.replace(',', '')
    elif ',' in num_str:
        parts = num_str.split(',')
        if len(parts[-1]) == 2:
            num_str = num_str.replace(',', '.')
        else:
            num_str = num_str.replace(',', '')
    elif '.' in num_str:
        parts = num_str.split('.')
        if len(parts[-1]) != 2 and len(parts[-1]) != 1:
            num_str = num_str.replace('.', '')
            
    try:
        return float(num_str)
    except ValueError:
        return None

def convert_gbp_to_eur(val_str):
    """
    Detects if the value is in GBP (£) and converts it to EUR (€) using a fixed rate.
    Handles ranges (e.g. '£4,000 - £10,000') and single values.
    Preserves year/parenthesis comments if possible.
    """
    if not val_str or not isinstance(val_str, str):
        return val_str
        
    val_lower = val_str.lower()
    if '£' not in val_str and 'gbp' not in val_lower and 'pound' not in val_lower:
        return val_str
        
    # Check if it is a range (ignoring hyphens inside parenthesis/brackets like in years: 2023-24)
    cleaned_for_check = re.sub(r'[\(\[\{].*?[\)\]\}]', '', val_str)
    
    parts = []
    if ' - ' in cleaned_for_check:
        parts = val_str.split(' - ')
    elif '-' in cleaned_for_check:
        parts = val_str.split('-')
    elif ' to ' in cleaned_for_check.lower():
        parts = re.split(r'\s+to\s+', val_str, flags=re.IGNORECASE)
        
    if len(parts) == 2:
        def convert_single(part):
            num = _extract_number(part)
            if num is None:
                return part
            converted = num * GBP_TO_EUR_RATE
            return f"€{converted:,.0f}"
            
        p1 = convert_single(parts[0].strip())
        p2 = convert_single(parts[1].strip())
        
        year_match = re.search(r'[\(\[\{]\d{4}.*?[\)\]\}]', val_str)
        suffix = f" {year_match.group(0)}" if year_match else ""
        return f"{p1} - {p2}{suffix} (converted from GBP)"
    else:
        num = _extract_number(val_str)
        if num is None:
            return val_str
            
        converted = num * GBP_TO_EUR_RATE
        year_match = re.search(r'[\(\[\{]\d{4}.*?[\)\]\}]', val_str)
        suffix = f" {year_match.group(0)}" if year_match else ""
        return f"€{converted:,.0f}{suffix} (converted from GBP)"

def extract_domain(url):
    """
    Extracts the base domain from a URL, e.g. 'http://www.womenwin.org/path' -> 'womenwin.org'.
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""

def normalize_name(name):
    """
    Normalizes organization name by lowercasing, removing punctuation, and filtering common filler words.
    """
    if not name or not isinstance(name, str):
        return []
    
    # Clean non-alphanumeric characters
    name_clean = re.sub(r'[^\w\s]', ' ', name.lower())
    
    # Common filler words to filter out
    filler_words = {
        "foundation", "stiftung", "charity", "trust", "association", "society",
        "fund", "global", "limited", "ltd", "co", "the", "e.v.", "gmbh", "v.",
        "corp", "inc", "und", "and", "de", "der", "die", "das"
    }
    
    tokens = [t.strip() for t in name_clean.split() if t.strip() and len(t.strip()) > 1 and t.strip() not in filler_words]
    return tokens

def jaccard_similarity(tokens1, tokens2):
    """
    Calculates Jaccard similarity between two token sets.
    """
    set1, set2 = set(tokens1), set(tokens2)
    if not set1 or not set2:
        return 0.0
    return len(set1 & set2) / len(set1 | set2)

def is_numeric(s):
    """
    Checks if a string consists entirely of digits or numeric ID format.
    """
    if not s or not isinstance(s, str):
        return False
    return s.strip().isdigit()

def match_members(m1, m2):
    """
    Determines if two members from Philea and Hinchilla refer to the same organization.
    Uses website domain match and name similarity.
    """
    # 1. Website domain match (strong indicator)
    web1 = m1.get("website", "")
    web2 = m2.get("philea_info", {}).get("website", "") if isinstance(m2.get("philea_info"), dict) else m2.get("website", "")
    
    dom1 = extract_domain(web1)
    dom2 = extract_domain(web2)
    
    if dom1 and dom2 and dom1 == dom2:
        return True
        
    # 2. Name matching
    name1 = m1.get("name", "").strip()
    name2 = m2.get("name", "").strip()
    
    # Avoid matching if names are numeric/IDs (like Hinchilla's "136193034")
    if is_numeric(name1) or is_numeric(name2):
        return False
        
    tokens1 = normalize_name(name1)
    tokens2 = normalize_name(name2)
    
    if not tokens1 or not tokens2:
        return False
        
    # Exact normalized token match
    if tokens1 == tokens2:
        return True
        
    # Jaccard similarity match
    sim = jaccard_similarity(tokens1, tokens2)
    if sim >= 0.8:
        return True
        
    # Substring match for longer names (e.g. "Toni Piëch" vs "Toni Piëch Foundation")
    str1 = "".join(tokens1)
    str2 = "".join(tokens2)
    if len(str1) >= 5 and len(str2) >= 5:
        if str1 in str2 or str2 in str1:
            return True
            
    return False

def merge_members(p_member, h_member):
    """
    Merges a Philea member and Hinchilla member.
    Logs and records discrepancies under a hidden `_discrepancies` field.
    """
    merged = dict(p_member)
    
    # Track discrepancies
    discrepancies = {}
    
    # Helper to resolve field value and check discrepancies
    def resolve_field(field_name, p_val, h_val, is_financial=False):
        if not p_val or p_val.lower() in ["not publicly available", "not disclosed", ""]:
            return h_val if h_val else p_val
        if not h_val or h_val.lower() in ["not publicly available", "not disclosed", ""]:
            return p_val
            
        # Both are non-empty. Compare.
        if is_financial:
            p_num = _extract_number(p_val)
            h_num = _extract_number(h_val)
            if p_num is not None and h_num is not None:
                # If they differ by more than 1%
                if abs(p_num - h_num) / max(p_num, 1.0) > 0.01:
                    discrepancies[field_name] = {
                        "philea_value": p_val,
                        "hinchilla_value": h_val,
                        "warning": "Financial statistics differ significantly."
                    }
                    logger.warning(
                        f"Financial stats discrepancy for {merged.get('name')} in '{field_name}': "
                        f"Philea={p_val} vs Hinchilla={h_val}"
                    )
            else:
                if p_val.strip().lower() != h_val.strip().lower():
                    discrepancies[field_name] = {
                        "philea_value": p_val,
                        "hinchilla_value": h_val,
                        "warning": "Statistics differ."
                    }
        else:
            # General string comparison
            if p_val.strip().lower() != h_val.strip().lower():
                # For website, check normalized domains
                if field_name == "website":
                    if extract_domain(p_val) != extract_domain(h_val):
                        discrepancies[field_name] = {
                            "philea_value": p_val,
                            "hinchilla_value": h_val,
                            "warning": "Website domains differ."
                        }
                        logger.warning(
                            f"Website discrepancy for {merged.get('name')}: "
                            f"Philea={p_val} vs Hinchilla={h_val}"
                        )
                else:
                    discrepancies[field_name] = {
                        "philea_value": p_val,
                        "hinchilla_value": h_val,
                        "warning": "Field values differ."
                    }
        return p_val # Default to Philea (primary source)

    # 1. Merge core fields
    merged["name"] = h_member.get("name") if is_numeric(merged.get("name")) and not is_numeric(h_member.get("name")) else merged.get("name")
    
    p_info = merged.setdefault("philea_info", {})
    h_info = h_member.get("philea_info", {})
    
    # Standardize Hinchilla financial stats from GBP to EUR before comparison
    h_annual_giving = convert_gbp_to_eur(h_info.get("annual_giving", ""))
    h_grant_range = convert_gbp_to_eur(h_info.get("grant_range", ""))
    h_average_grant = convert_gbp_to_eur(h_info.get("average_grant", ""))
    h_expenditure = convert_gbp_to_eur(h_info.get("expenditure", ""))
    
    # 2. Resolve fields and check conflicts
    merged["website"] = resolve_field("website", merged.get("website", ""), h_info.get("website", ""))
    merged["address"] = resolve_field("address", merged.get("address", ""), h_info.get("address", ""))
    merged["email"] = resolve_field("email", merged.get("email", ""), h_info.get("email", ""))
    
    p_info["annual_giving"] = resolve_field("annual_giving", p_info.get("annual_giving", ""), h_annual_giving, is_financial=True)
    p_info["grant_range"] = resolve_field("grant_range", p_info.get("grant_range", ""), h_grant_range, is_financial=True)
    p_info["average_grant"] = resolve_field("average_grant", p_info.get("average_grant", ""), h_average_grant, is_financial=True)
    p_info["funding_model"] = resolve_field("funding_model", p_info.get("funding_model", ""), h_info.get("funding_model", ""))
    
    # Detailed text merging (prefer longer/more detailed version)
    for txt_field in ["About", "Programme Areas", "Geographic Focus", "application_details"]:
        p_val = p_info.get(txt_field, "")
        h_val = h_info.get(txt_field, "")
        p_info[txt_field] = h_val if len(h_val) > len(p_val) else p_val

    # 3. Add Hinchilla-specific metadata
    for key in ["charityNumber", "areaOfOperation", "phone", "applicationPortal", "success_rate", "decision_time"]:
        if key in h_info and h_info[key]:
            p_info[key] = h_info[key]
    if h_expenditure:
        p_info["expenditure"] = h_expenditure

    # 4. Merge tags and locations (unions)
    p_tags = set(merged.get("tags_focus", []))
    h_tags = set(h_member.get("tags_focus", []))
    merged["tags_focus"] = sorted(list(p_tags | h_tags))
    
    p_geo = merged.get("geo_locations", {})
    h_geo = h_member.get("geo_locations", {})
    merged_geo = {}
    
    all_regions = set(p_geo.keys()) | set(h_geo.keys())
    for reg in all_regions:
        countries = set(p_geo.get(reg, [])) | set(h_geo.get(reg, []))
        merged_geo[reg] = sorted(list(countries))
    merged["geo_locations"] = merged_geo

    # 5. Union sources
    p_sources = set(p_info.get("sources", []))
    h_sources = set(h_info.get("sources", []))
    p_info["sources"] = sorted(list(p_sources | h_sources))

    # Save internal discrepancies metadata
    if discrepancies:
        merged["_discrepancies"] = discrepancies

    return merged

def consolidate_datasets(philea_members, hinchilla_members):
    """
    Consolidates Philea and Hinchilla datasets by matching members,
    merging their fields, tracking conflicts, and return the combined list.
    """
    logger.info(f"Starting consolidation of {len(philea_members)} Philea members and {len(hinchilla_members)} Hinchilla members...")
    
    consolidated = []
    matched_hinchilla_indices = set()
    
    for p_member in philea_members:
        matched_h = None
        matched_idx = -1
        
        for idx, h_member in enumerate(hinchilla_members):
            if idx in matched_hinchilla_indices:
                continue
            if match_members(p_member, h_member):
                matched_h = h_member
                matched_idx = idx
                break
                
        if matched_h:
            logger.info(f"Match found: '{p_member.get('name')}' (Philea) <==> '{matched_h.get('name')}' (Hinchilla)")
            merged = merge_members(p_member, matched_h)
            consolidated.append(merged)
            matched_hinchilla_indices.add(matched_idx)
        else:
            consolidated.append(dict(p_member))
            
    # Add unmatched Hinchilla members
    for idx, h_member in enumerate(hinchilla_members):
        if idx not in matched_hinchilla_indices:
            logger.info(f"Adding unmatched Hinchilla member: '{h_member.get('name')}'")
            # For unmatched Hinchilla members, ensure they conform to the preprocessed format
            cleaned_h = dict(h_member)
            h_info = cleaned_h.setdefault("philea_info", {})
            
            # Standardize GBP values for standalone Hinchilla records
            for field in ["annual_giving", "grant_range", "average_grant", "expenditure"]:
                if field in h_info:
                    h_info[field] = convert_gbp_to_eur(h_info[field])
                    
            # Sync root attributes with philea_info for consistency
            cleaned_h["website"] = h_info.get("website", "")
            cleaned_h["address"] = h_info.get("address", "")
            cleaned_h["email"] = h_info.get("email", "")
            
            consolidated.append(cleaned_h)
            
    logger.info(f"Consolidation complete. Resulting dataset has {len(consolidated)} members.")
    return consolidated
