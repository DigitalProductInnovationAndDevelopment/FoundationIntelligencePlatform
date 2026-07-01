import os
import json
import re
import logging
from urllib.parse import urlparse

try:
    from preprocessing.quality import is_informative_value, is_placeholder_value
except ModuleNotFoundError:
    from quality import is_informative_value, is_placeholder_value

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
    """
    if not val_str or is_placeholder_value(val_str):
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
            
    val_lower = cleaned.lower()
    multiplier = 1.0
    if re.search(r'\bmillion\b|(?<=\d)m\b|\bm\b', val_lower):
        multiplier = 1_000_000.0
    elif re.search(r'\bbillion\b|(?<=\d)b\b|\bb\b', val_lower):
        multiplier = 1_000_000_000.0
    elif re.search(r'\bthousand\b|(?<=\d)k\b|\bk\b', val_lower):
        multiplier = 1_000.0

    try:
        return float(num_str) * multiplier
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
    web2 = m2.get("website", "")
    
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

def normalize_to_clean_schema(member, source_name):
    """
    Transforms any raw Philea or Hinchilla member dict into the clean unified schema.
    """
    p_info = member.get("philea_info", {})
    if not isinstance(p_info, dict):
        p_info = {}
        
    position = member.get("position", {})
    if not isinstance(position, dict):
        position = {}
        
    # Core fields
    name = member.get("name", "").strip()
    
    # Type handling
    m_type = member.get("type", {})
    if not m_type or not isinstance(m_type, dict):
        m_type = {
            "value": "foundation",
            "label": "Foundation and philanthropic organisation"
        }
        
    # Address and position flattening
    address = position.get("address", "") or member.get("address", "") or p_info.get("address", "") or ""
    city = position.get("city", "")
    state = position.get("state", "")
    country = position.get("country", "") or member.get("country", "") or ""
    
    # Coordinates
    latitude = position.get("lat", None)
    if latitude is not None:
        try:
            latitude = float(latitude)
        except (ValueError, TypeError):
            latitude = None
            
    longitude = position.get("lng", None)
    if longitude is not None:
        try:
            longitude = float(longitude)
        except (ValueError, TypeError):
            longitude = None
            
    # Standalone Hinchilla financial conversion (or general Hinchilla standardization)
    annual_giving = p_info.get("annual_giving", "")
    annual_income = p_info.get("annual_income", "")
    annual_expenditure = p_info.get("annual_expenditure", "")
    average_grant = p_info.get("average_grant", "")
    grant_range = p_info.get("grant_range", "")
    expenditure = p_info.get("expenditure", "")
    
    if source_name == "Hinchilla":
        annual_giving = convert_gbp_to_eur(annual_giving)
        annual_income = convert_gbp_to_eur(annual_income)
        annual_expenditure = convert_gbp_to_eur(annual_expenditure)
        average_grant = convert_gbp_to_eur(average_grant)
        grant_range = convert_gbp_to_eur(grant_range)
        expenditure = convert_gbp_to_eur(expenditure)
        
    funding_info = {
        "annual_giving": annual_giving,
        "annual_income": annual_income,
        "annual_expenditure": annual_expenditure,
        "average_grant": average_grant,
        "grant_range": grant_range,
        "funding_model": p_info.get("funding_model", ""),
        "application_details": p_info.get("application_details", ""),
        "success_rate": p_info.get("success_rate", ""),
        "decision_time": p_info.get("decision_time", ""),
        "expenditure": expenditure,
        "number_of_grants": p_info.get("number_of_grants", ""),
        "quick_stats": p_info.get("quick_stats", {}),
        "charity_number": p_info.get("charityNumber", "") or p_info.get("charity_number", ""),
        "application_portal": p_info.get("applicationPortal", "") or p_info.get("application_portal", ""),
        "sources": p_info.get("sources", [])
    }
    
    # Renaming tag & geo fields
    thematic_focus = member.get("tags_focus", []) or member.get("thematic_focus", [])
    geographic_focus = member.get("geo_locations", {}) or member.get("geographic_focus", {})
    
    return {
        "name": name,
        "source": source_name,
        "type": m_type,
        "website": member.get("website", "") or p_info.get("website", "") or "",
        "email": member.get("email", "") or p_info.get("email", "") or "",
        "address": address,
        "city": city,
        "state": state,
        "country": country,
        "latitude": latitude,
        "longitude": longitude,
        "funding_info": funding_info,
        "thematic_focus": sorted(list(set(thematic_focus))),
        "geographic_focus": geographic_focus
    }

def merge_members(p_member, h_member):
    """
    Merges a Philea member and Hinchilla member.
    Both members must be in the clean schema.
    Logs and records discrepancies under a hidden `_discrepancies` field.
    """
    # Create copy of Philea clean member as base
    merged = dict(p_member)
    merged["source"] = "Philea, Hinchilla"
    
    # Track discrepancies
    discrepancies = {}
    
    # Helper to resolve field value and check discrepancies
    def resolve_field(field_name, p_val, h_val, is_financial=False):
        if not is_informative_value(p_val):
            return h_val if is_informative_value(h_val) else p_val
        if not is_informative_value(h_val):
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
        
    # Resolve top level strings
    merged["website"] = resolve_field("website", merged.get("website", ""), h_member.get("website", ""))
    merged["address"] = resolve_field("address", merged.get("address", ""), h_member.get("address", ""))
    merged["email"] = resolve_field("email", merged.get("email", ""), h_member.get("email", ""))
    
    # Resolve coordinates
    merged["latitude"] = merged.get("latitude") if merged.get("latitude") is not None else h_member.get("latitude")
    merged["longitude"] = merged.get("longitude") if merged.get("longitude") is not None else h_member.get("longitude")
    
    # Merge funding info
    p_fund = merged.setdefault("funding_info", {})
    h_fund = h_member.get("funding_info", {})
    
    p_fund["annual_giving"] = resolve_field("annual_giving", p_fund.get("annual_giving", ""), h_fund.get("annual_giving", ""), is_financial=True)
    p_fund["average_grant"] = resolve_field("average_grant", p_fund.get("average_grant", ""), h_fund.get("average_grant", ""), is_financial=True)
    p_fund["grant_range"] = resolve_field("grant_range", p_fund.get("grant_range", ""), h_fund.get("grant_range", ""), is_financial=True)
    p_fund["funding_model"] = resolve_field("funding_model", p_fund.get("funding_model", ""), h_fund.get("funding_model", ""))
    p_fund["expenditure"] = resolve_field("expenditure", p_fund.get("expenditure", ""), h_fund.get("expenditure", ""), is_financial=True)
    
    # Union metadata
    for key in ["success_rate", "decision_time", "charity_number", "application_portal"]:
        if is_informative_value(h_fund.get(key)):
            p_fund[key] = h_fund[key]

    for key in ["annual_income", "annual_expenditure", "number_of_grants", "quick_stats"]:
        if is_informative_value(h_fund.get(key)) and not is_informative_value(p_fund.get(key)):
            p_fund[key] = h_fund[key]
            
    # Text merge (prefer longer)
    p_details = p_fund.get("application_details", "")
    h_details = h_fund.get("application_details", "")
    p_fund["application_details"] = h_details if len(h_details) > len(p_details) else p_details
    
    # Merge thematic focus (tags)
    p_tags = set(merged.get("thematic_focus", []))
    h_tags = set(h_member.get("thematic_focus", []))
    merged["thematic_focus"] = sorted(list(p_tags | h_tags))
    
    # Merge geolocations (geographic focus)
    p_geo = merged.get("geographic_focus", {})
    h_geo = h_member.get("geographic_focus", {})
    merged_geo = {}
    
    all_regions = set(p_geo.keys()) | set(h_geo.keys())
    for reg in all_regions:
        countries = set(p_geo.get(reg, [])) | set(h_geo.get(reg, []))
        merged_geo[reg] = sorted(list(countries))
    merged["geographic_focus"] = merged_geo
    
    # Merge sources URLs
    p_sources = set(p_fund.get("sources", []))
    h_sources = set(h_fund.get("sources", []))
    p_fund["sources"] = sorted(list(p_sources | h_sources))
    
    # Save discrepancies
    if discrepancies:
        merged["_discrepancies"] = discrepancies
        
    return merged

def consolidate_datasets(philea_members, hinchilla_members):
    """
    Consolidates Philea and Hinchilla datasets by matching members,
    merging their fields, tracking conflicts, and return the combined list.
    Every member is returned in the clean unified format.
    """
    logger.info(f"Starting consolidation of {len(philea_members)} Philea members and {len(hinchilla_members)} Hinchilla members...")
    
    # Normalize inputs to clean schema first
    philea_clean = [normalize_to_clean_schema(m, "Philea") for m in philea_members]
    hinchilla_clean = [normalize_to_clean_schema(m, "Hinchilla") for m in hinchilla_members]
    
    consolidated = []
    matched_hinchilla_indices = set()
    
    for p_member in philea_clean:
        matched_h = None
        matched_idx = -1
        
        for idx, h_member in enumerate(hinchilla_clean):
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
            consolidated.append(p_member)
            
    # Add unmatched Hinchilla members
    for idx, h_member in enumerate(hinchilla_clean):
        if idx not in matched_hinchilla_indices:
            logger.info(f"Adding unmatched Hinchilla member: '{h_member.get('name')}'")
            consolidated.append(h_member)
            
    logger.info(f"Consolidation complete. Resulting dataset has {len(consolidated)} members.")
    return consolidated
