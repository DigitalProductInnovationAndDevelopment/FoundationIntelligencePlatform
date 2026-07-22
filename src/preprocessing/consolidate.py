import os
import json
import re
import logging
from urllib.parse import urlparse
from datetime import datetime, timezone

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

def normalize_thematic_focus(tags_list):
    """
    Deduplicates and sorts thematic focus tags.
    Accepts both list of strings (legacy/tests) and list of dicts with {"tag": ..., "source": ...}.
    If a tag appears multiple times, prefers "exact_match" over "regex_fallback".
    Returns a sorted list of dicts in the new schema format.
    """
    if not tags_list:
        return []
    
    unique_tags = {}
    for item in tags_list:
        if isinstance(item, dict):
            tag = item.get("tag")
            source = item.get("source", "regex_fallback")
        else:
            tag = str(item)
            source = "exact_match"  # Default for plain strings in legacy code / tests
            
        if not tag:
            continue
            
        # If tag already exists, prefer 'exact_match'
        if tag in unique_tags:
            if source == "exact_match":
                unique_tags[tag] = source
        else:
            unique_tags[tag] = source
            
    # Convert back to list of dicts and sort by tag name
    result = [{"tag": tag, "source": src} for tag, src in unique_tags.items()]
    return sorted(result, key=lambda x: x["tag"])

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
        "thematic_focus": normalize_thematic_focus(thematic_focus),
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
    p_tags = merged.get("thematic_focus", [])
    h_tags = h_member.get("thematic_focus", [])
    merged["thematic_focus"] = normalize_thematic_focus(p_tags + h_tags)
    
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


def consolidate_uk_datasets(charity_records, threesixty_records):
    """
    Consolidates Charity Commission (Register of Charities) and 360Giving datasets.
    Aligns them based on their official UK charity registration number.
    Returns:
        (charities_list, grants_list) in flat relational schema.
    """
    import re
    from preprocessing.extract_geo_topic import extract_tags, extract_geo
    ingestion_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def parse_charity_number_from_org_id(org_id):
        if not org_id or not isinstance(org_id, str):
            return None
        # Match GB-CHC-XXXXX or numeric sequences
        match = re.search(r'GB-CHC-(\d+)', org_id)
        if match:
            return int(match.group(1))
        match = re.search(r'\b(\d+)\b', org_id)
        if match:
            return int(match.group(1))
        return None

    # Step 1: Group 360Giving records by charity number
    ts_by_number = {}
    for ts in threesixty_records:
        org_id = ts.get("org_id")
        charity_num = parse_charity_number_from_org_id(org_id)
        if charity_num:
            ts_by_number[charity_num] = ts

    # Step 2: Build consolidated charities map
    consolidated_charities = {}
    grants_by_id = {}

    def parse_grant(g, default_funder_id=None, default_recipient_id=None):
        if not isinstance(g, dict):
            return None
        g_data = g.get("data") if isinstance(g.get("data"), dict) else g
        grant_id = g.get("grant_id") or g_data.get("id") or g_data.get("grant_id")
        if not grant_id:
            return None
            
        # Funder
        fund_orgs = g_data.get("fundingOrganization")
        fund_org = fund_orgs[0] if isinstance(fund_orgs, list) and fund_orgs else g_data.get("fundingOrg") or {}
        funding_source_id = fund_org.get("id")
        funding_id = parse_charity_number_from_org_id(funding_source_id) or default_funder_id
        funding_name = fund_org.get("name") or "Unknown Donor"
        
        # Recipient
        rec_orgs = g_data.get("recipientOrganization")
        rec_org = rec_orgs[0] if isinstance(rec_orgs, list) and rec_orgs else g_data.get("recipientOrg") or {}
        recipient_source_id = rec_org.get("id") or rec_org.get("charityNumber")
        recipient_id = parse_charity_number_from_org_id(recipient_source_id) or default_recipient_id
        recipient_name = rec_org.get("name") or "Unknown Recipient"
        
        # Financials
        raw_amount = g_data.get("amountAwarded") or g_data.get("amount") or 0.0
        currency = g_data.get("currency") or "GBP"
        try:
            amount = float(raw_amount)
        except (ValueError, TypeError):
            amount = None

        # Preserve the source amount and currency. Only source-EUR values populate
        # amount_eur; no undocumented exchange rate is applied here.
        amount_eur = amount if currency.upper() == "EUR" else None
            
        desc = g_data.get("description") or g_data.get("title") or ""
        date_str = g_data.get("awardDate") or g_data.get("date") or ""
        
        # Location
        recipient_lat = None
        recipient_lng = None
        recipient_loc = g_data.get("recipientLocation") or {}
        if isinstance(recipient_loc, dict):
            recipient_lat = recipient_loc.get("latitude") or recipient_loc.get("lat")
            recipient_lng = recipient_loc.get("longitude") or recipient_loc.get("lng")
        elif isinstance(recipient_loc, list) and len(recipient_loc) >= 2:
            recipient_lat = recipient_loc[0]
            recipient_lng = recipient_loc[1]
            
        region = g_data.get("recipientRegion", {}).get("name") if isinstance(g_data.get("recipientRegion"), dict) else g_data.get("recipientRegion") or ""
        beneficiary_locations = g_data.get("beneficiaryLocation") or []
        project_locations = g_data.get("projectLocation") or g_data.get("location") or []
        programme_areas = [
            item.get("title")
            for item in (g_data.get("grantProgramme") or [])
            if isinstance(item, dict) and item.get("title")
        ]
        publisher = g.get("publisher") if isinstance(g.get("publisher"), dict) else {}
        source_url = g_data.get("dataSource") or publisher.get("self") or ""
        
        return {
            "grant_id": grant_id,
            "funding_charity_id": funding_id,
            "funding_name": funding_name,
            "funding_org_source_id": funding_source_id,
            "recipient_name": recipient_name,
            "recipient_charity_id": recipient_id,
            "recipient_org_source_id": recipient_source_id,
            "amount": amount,
            "amount_eur": amount_eur,
            "currency": currency,
            "description": desc,
            "date": date_str,
            "recipient_latitude": recipient_lat,
            "recipient_longitude": recipient_lng,
            "recipient_region": region,
            "beneficiary_geography": json.dumps(beneficiary_locations, ensure_ascii=False),
            "project_geography": json.dumps(project_locations, ensure_ascii=False),
            "programme_area_source": json.dumps(programme_areas, ensure_ascii=False),
            "source": "360Giving",
            "source_record_id": grant_id,
            "source_url": source_url,
            "ingestion_timestamp": ingestion_timestamp,
            "raw_grant_data": g,
        }

    # Process all official Charity Commission records
    for cc in charity_records:
        reg_no = cc.get("registered_charity_number")
        if not reg_no:
            continue
        reg_no = int(reg_no)
        suffix = cc.get("suffix", 0)

        # Get details
        all_details = cc.get("all_details") or {}
        charity_name = all_details.get("charity_name", f"Charity {reg_no}")
        website = all_details.get("web") or ""
        email = all_details.get("email") or ""
        phone = all_details.get("phone") or ""
        
        # Flatten address
        address_parts = [
            all_details.get("address_line_one"),
            all_details.get("address_line_two"),
            all_details.get("address_line_three"),
            all_details.get("address_line_four"),
            all_details.get("address_line_five"),
            all_details.get("address_post_code")
        ]
        address = ", ".join([p for p in address_parts if p]).strip()

        income = all_details.get("latest_income")
        expenditure = all_details.get("latest_expenditure")

        # Fallback to financial history if direct fields are missing
        if (income is None or expenditure is None) and cc.get("financial_history"):
            history = cc["financial_history"]
            sorted_history = sorted(
                history,
                key=lambda x: x.get("financial_period_end_date", ""),
                reverse=True
            )
            if sorted_history:
                latest_period = sorted_history[0]
                if income is None:
                    income = latest_period.get("income")
                if expenditure is None:
                    expenditure = latest_period.get("expenditure")

        # Map details to flat schema
        charity_profile = {
            "charity_id": reg_no,
            "name": charity_name,
            "type": all_details.get("charity_type") or "Charity",
            "website": website,
            "email": email,
            "address": address,
            "city": all_details.get("address_line_four") or "",
            "state": all_details.get("address_line_three") or "",
            "country": "United Kingdom",
            "latitude": None,
            "longitude": None,
            "annual_income": income,
            "annual_expenditure": expenditure,
            "thematic_focus": [],
            "geographic_focus": {},
            "raw_cc_data": cc
        }

        # Check if we have 360Giving info for this charity (funder details/grants)
        if reg_no in ts_by_number:
            ts = ts_by_number[reg_no]
            ts_detail = ts.get("detail") or {}
            
            # Enrich fields if missing
            if not charity_profile["website"] and ts_detail.get("website"):
                charity_profile["website"] = ts_detail.get("website")
            if not charity_profile["email"] and ts_detail.get("email"):
                charity_profile["email"] = ts_detail.get("email")

            # Extract grants made by this charity
            grants_made = ts.get("grants_made") or []
            for g in grants_made:
                parsed = parse_grant(g, default_funder_id=reg_no)
                if parsed:
                    grants_by_id[parsed["grant_id"]] = parsed

            # Extract grants received by this charity
            grants_received = ts.get("grants_received") or []
            for g in grants_received:
                parsed = parse_grant(g, default_recipient_id=reg_no)
                if parsed:
                    grants_by_id[parsed["grant_id"]] = parsed

        consolidated_charities[reg_no] = charity_profile

    # Process remaining 360Giving organisations that are NOT in the Charity Commission dataset
    for reg_no, ts in ts_by_number.items():
        if reg_no not in consolidated_charities:
            ts_detail = ts.get("detail") or {}
            charity_name = ts_detail.get("name") or ts.get("name") or f"Charity {reg_no}"
            
            charity_profile = {
                "charity_id": reg_no,
                "name": charity_name,
                "type": "Funder",
                "website": ts_detail.get("website") or "",
                "email": ts_detail.get("email") or "",
                "address": ts_detail.get("address") or "",
                "city": "",
                "state": "",
                "country": "United Kingdom",
                "latitude": None,
                "longitude": None,
                "annual_income": None,
                "annual_expenditure": None,
                "thematic_focus": [],
                "geographic_focus": {},
                "raw_cc_data": {}
            }

            # Extract grants made
            grants_made = ts.get("grants_made") or []
            for g in grants_made:
                parsed = parse_grant(g, default_funder_id=reg_no)
                if parsed:
                    grants_by_id[parsed["grant_id"]] = parsed

            # Extract grants received
            grants_received = ts.get("grants_received") or []
            for g in grants_received:
                parsed = parse_grant(g, default_recipient_id=reg_no)
                if parsed:
                    grants_by_id[parsed["grant_id"]] = parsed

            consolidated_charities[reg_no] = charity_profile

    grants_list = list(grants_by_id.values())

    # Step 3: Run classification on the consolidated list of charities
    charities_list = list(consolidated_charities.values())
    extract_tags(charities_list)
    extract_geo(charities_list)

    # Serialize complex fields to match database format
    # In SQLite, we store thematic_focus as JSON arrays and geographic_focus as JSON objects
    for c in charities_list:
        c["thematic_focus"] = json.dumps(c.get("tags_focus", []))
        c["geographic_focus"] = json.dumps(c.get("geo_locations", {}))
        c["latitude"] = c.get("raw_cc_data", {}).get("position", {}).get("lat") if c.get("raw_cc_data") else None
        c["longitude"] = c.get("raw_cc_data", {}).get("position", {}).get("lng") if c.get("raw_cc_data") else None
        if c["latitude"] is not None:
            try:
                c["latitude"] = float(c["latitude"])
            except (ValueError, TypeError):
                c["latitude"] = None
        if c["longitude"] is not None:
            try:
                c["longitude"] = float(c["longitude"])
            except (ValueError, TypeError):
                c["longitude"] = None

    # Step 4: Classify the grants as well
    for g in grants_list:
        g_mock = {
            "name": g["recipient_name"],
            "description": g["description"]
        }
        extract_tags([g_mock])
        extract_geo([g_mock])
        
        g_tags = [t["tag"] for t in g_mock.get("tags_focus", [])]
        g["tags"] = json.dumps(g_tags)
        
        g_geo = g_mock.get("geo_locations", {})
        g["geographic_focus"] = json.dumps(g_geo)

    return charities_list, grants_list
