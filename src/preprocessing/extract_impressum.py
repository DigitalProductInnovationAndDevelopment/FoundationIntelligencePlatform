import re
import argparse
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("extract_impressum")

# whitelisted generic prefixes (GDPR Compliance Guardrail)
GENERIC_PREFIXES = {"info", "kontakt", "contact", "office", "support", "mail", "sekretariat", "hello", "segreteria", "impressum", "impressum2", "stiftung"}

# keywords for discoverable pages (extended for common European translations)
KEYWORDS_LEGAL = [
    "impressum", "legal-notice", "legal", "mentions-legales", "mentions_legales", 
    "note-legali", "note_legali", "aviso-legal", "aviso_legal", "privacy"
]
KEYWORDS_ABOUT = [
    "about", "about-us", "about_us", "ueber-uns", "ueberuns", "ueber", "qui-sommes-nous", 
    "qui_sommes_nous", "chi-siamo", "chi_siamo", "quienes-somos", "quienes_somos", 
    "nosotros", "a-propos", "apropos", "storia", "associazione"
]
KEYWORDS_CONTACT = [
    "kontakt", "contact", "contact-us", "contact_us", "contatti", "contatto", 
    "contattaci", "contacto", "contactar", "contactanos", "nous-contacter", 
    "nous_contacter", "contactez-nous", "contactez_nous", "ansprechpartner"
]

# Combine all keywords for general matching
ALL_KEYWORDS = KEYWORDS_LEGAL + KEYWORDS_ABOUT + KEYWORDS_CONTACT

# Street name matchers partitioned into suffixes (German/Dutch/etc.) and standalone starting words (English/Italian/French/Spanish/etc.)
STREET_SUFFIXES = {
    "str.", "str", "straße", "strasse", "gasse", "weg", "platz", "pl.", "allee", "ring",
    "straat", "plein", "laan", "gracht", "singel", "kade", 
    "street", "st.", "st", "road", "rd.", "rd", "avenue", "ave.", "ave", 
    "lane", "ln", "boulevard", "blvd", "drive", "dr.", "dr", "way"
}

ORG_NAME_MARKERS = (
    "foundation", "stiftung", "fondation", "fondazione", "e.v.", "ev", "gmbh", "ag", "kg", "ug",
    "ltd", "limited", "inc", "corp", "co.", "association", "associazione", "asbl", "vzw", "verein",
    "ngo", "onlus", "trust", "society", "institute", "institut", "instituto"
)

STREET_WORDS = {
    "via", "viale", "piazza", "corso", "largo", "strada", "vicolo", "calata",
    "rue", "place", "route", "chemin", "quai", "faubourg", "passage", "impasse",
    "calle", "paseo", "ronda", "camino", "carretera", "pasaje"
}

def get_base_domain(url):
    """
    Extracts the hostname/netloc (without a leading www.) from a URL.
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""

def is_internal_link(base_url, link_url):
    """
    Checks if a link is internal relative to the base URL.
    """
    base_domain = get_base_domain(base_url)
    link_domain = get_base_domain(link_url)
    # If the link domain is empty or matches base domain, it is internal
    return not link_domain or link_domain == base_domain

def extract_links(base_url, html_content):
    """
    Extracts and categorizes internal links from HTML based on keywords.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    found_links = []
    
    # Store visited URLs to avoid duplicates
    seen_urls = set()
    
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
            
        full_url = urljoin(base_url, href)
        if not is_internal_link(base_url, full_url):
            continue
            
        # Avoid duplicate pages
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        
        # Determine priority by matching href or link text against keywords
        link_text = a.get_text().strip().lower()
        href_lower = href.lower()
        
        priority = 4  # Default lowest priority
        matched_kw = None
        
        # Check matching
        for kw in ALL_KEYWORDS:
            if kw in href_lower or kw in link_text:
                current_priority = 4
                if kw in KEYWORDS_LEGAL:
                    current_priority = 1
                elif kw in KEYWORDS_CONTACT:
                    current_priority = 2
                elif kw in KEYWORDS_ABOUT:
                    current_priority = 3
                
                if current_priority < priority:
                    priority = current_priority
                    matched_kw = kw
                
        found_links.append({
            "url": full_url,
            "priority": priority,
            "keyword": matched_kw
        })
        
    # Sort by priority ascending (1 = highest, 4 = lowest)
    return sorted(found_links, key=lambda x: x["priority"])

def extract_generic_emails(text):
    """
    Extracts whitelisted generic email addresses from text and excludes personalized ones.
    """
    # Regex matching general email addresses
    raw_emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
    generic_emails = []
    for email in raw_emails:
        local_part, domain = email.split("@", 1)
        local_lower = local_part.lower()
        # GDPR Guardrails: local part must be whitelisted prefix, no dots or plus signs
        if local_lower in GENERIC_PREFIXES and "." not in local_lower and "+" not in local_lower:
            # Avoid duplicate generic email additions
            if email not in generic_emails:
                generic_emails.append(email)
    return generic_emails

def clean_address_line(line):
    """
    Extracts only the address segment from a line, stripping leading filler text.
    """
    line_lower = line.lower()
    best_idx = len(line)
    matched_type = None
    
    # Try matching standalone words (with word boundary, capitalized or uppercase in original line)
    for w in STREET_WORDS:
        pattern = rf'\b(?:{re.escape(w.capitalize())}|{re.escape(w.upper())})\b'
        match = re.search(pattern, line)
        if match and match.start() < best_idx:
            best_idx = match.start()
            matched_type = "word"
            
    # If not found, try matching suffixes case-insensitively
    for s in STREET_SUFFIXES:
        if len(s) <= 2:
            match = re.search(rf'\b{re.escape(s)}\b', line_lower)
            if match and match.start() < best_idx:
                best_idx = match.start()
                matched_type = "suffix"
        else:
            idx = line_lower.find(s)
            if idx != -1 and idx < best_idx:
                best_idx = idx
                matched_type = "suffix"
                
    if best_idx == len(line):
        return line
        
    kw_idx = best_idx
    
    # If matched starting word (e.g. Via, Rue, Calle), slice directly at it
    if matched_type == "word":
        return line[kw_idx:]
        
    # Suffix logic: look backwards to capture street name
    before_part = line[:kw_idx]
    words = before_part.split()
    if not words:
        return line
        
    street_words = []
    stop_words = {"at", "is", "in", "located", "on", "are", "our", "us", "we", "the", "a", "an", "here"}
    for word in reversed(words):
        word_clean = word.strip(",. ")
        if not word_clean:
            continue
        if word_clean[0].isupper() or word_clean.isdigit():
            if word_clean.lower() in stop_words:
                break
            street_words.insert(0, word)
        else:
            break
            
    if not street_words:
        return line[kw_idx:]
        
    start_word = street_words[0]
    start_idx = line.find(start_word)
    if start_idx == -1:
        return line
    return line[start_idx:]

def extract_physical_address(text):
    """
    Heuristically extracts physical addresses from text block.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    
    # 4 to 5 digit ZIP code pattern (covers DE/CH/AT, IT, and UK postcodes)
    zip_pattern = re.compile(r'\b(\d{4,5}|[A-Z]{1,2}[0-9R][0-9A-Z]?\s*[0-9][A-Z]{2})\b', re.IGNORECASE)
    
    for i, line in enumerate(lines):
        # Guardrail: street lines are not long paragraphs
        if len(line) > 100:
            continue
            
        line_lower = line.lower()
        
        # Verify suffixes (and exclude false positives for "ring" and "weg" inside English words)
        has_suffix = False
        for s in STREET_SUFFIXES:
            if len(s) <= 2:
                match = re.search(rf'\b{re.escape(s)}\b', line_lower)
                if match:
                    has_suffix = True
                    break
            elif s in line_lower:
                if s in ["ring", "weg"]:
                    words = re.findall(r'\b\w+\b', line_lower)
                    found_invalid = False
                    invalid_words = {
                        "gathering", "engineering", "during", "spring", "bring", "hearing", 
                        "sharing", "caring", "clearing", "manufacturing", "monitoring", 
                        "mentoring", "partnering", "sponsoring", "offering", "governing", 
                        "transferring", "registering", "fostering", "empowering", "pioneering", 
                        "delivering", "rendering", "filtering", "centering", "securing", 
                        "recovering", "discovering"
                    }
                    for word in words:
                        if s in word and word in invalid_words:
                            found_invalid = True
                            break
                    if found_invalid:
                        continue
                has_suffix = True
                break
        
        # Standalone words require case-sensitive capitalized or uppercase start
        has_word = False
        for w in STREET_WORDS:
            pattern = rf'\b(?:{re.escape(w.capitalize())}|{re.escape(w.upper())})\b'
            if re.search(pattern, line):
                has_word = True
                break
                
        has_street = has_suffix or has_word
        has_number = bool(re.search(r'\d+', line))
        
        # Impressum addresses usually list street name and number first
        if has_street and has_number:
            candidate = ""
            # Clean up the street line to strip prepended filler text
            clean_street = clean_address_line(line)
            
            # Extract previous line as potential organization name if it is short and clean
            org_name = ""
            if i > 0:
                prev_line = lines[i-1]
                forbidden_words = [
                    # Technical contact stop words
                    "tel", "fax", "e-mail", "email", "@", "http", "www.", 
                    # English menu/structure words
                    "contact", "kontakt", "address", "adresse", "anschrift", "location", 
                    "impressum", "headquarters", "details", "office", "head office", "home", 
                    "news", "menu", "history", "team", "careers", "staff", "partners", "projects", 
                    "events", "press", "publications", "blog", "resources", "gallery", "faq", 
                    "search", "links", "site", "map", "sitemap", "privacy", "legal", "cookies", "terms",
                    "submissions", "submission", "required", "optional",
                    # Italian menu/structure words
                    "indirizzo", "telefono", "téléphone", "correo", "pec", "web", "sito", 
                    "come raggiungerci", "chi siamo", "storia", "organi", "eventi", 
                    "pubblicazioni", "staff", "novità", "breve", "in breve", "progetti",
                    # Spanish menu/structure words
                    "dirección", "direccion", "teléfono", "quienes somos", "equipo", "empleo", 
                    "socios", "noticias", "aviso legal", "inicio",
                    # French menu/structure words
                    "accueil", "a propos", "équipe", "equipe", "carrières", "carrieres", 
                    "partenaires", "actualités", "actualites", "mentions légales", "mentions legales"
                ]
                prev_line_lower = prev_line.lower()
                has_org_marker = any(marker in prev_line_lower for marker in ORG_NAME_MARKERS)
                if len(prev_line) < 60 and has_org_marker and not any(k in prev_line_lower for k in forbidden_words):
                    org_name = prev_line
            
            # Case 1: ZIP code is in the same line (e.g. Musterstraße 42, 80333 München)
            if zip_pattern.search(clean_street):
                candidate = clean_street
            # Case 2: ZIP code is in the next line
            elif i + 1 < len(lines):
                next_line = lines[i+1]
                if len(next_line) < 60 and zip_pattern.search(next_line):
                    candidate = f"{clean_street}, {next_line}"
                    # Optional: Include the country on the line after next if present
                    if i + 2 < len(lines):
                        third_line = lines[i+2]
                        if len(third_line) < 40 and not any(k in third_line.lower() for k in ["tel", "fax", "e-mail", "email", "@", "kvk"]):
                            candidate += f", {third_line}"
            
            if candidate:
                if org_name:
                    candidate = f"{org_name}, {candidate}"
                # Clean candidate (remove newlines and collapse whitespace)
                candidate = re.sub(r'\s+', ' ', candidate).strip()
                # Clean phone number suffixes or prefix labels
                candidate = re.sub(r'(?i)\b(tel|phone|telefon|fax|mobil|mobile|t\b|f\b)[:\s]*\+?[\d\s\-\(\)/]+', '', candidate).strip()
                candidate = re.sub(r"[.;:!?]+$", "", candidate).strip()
                # Filter out standard keywords and mail symbols
                if "@" not in candidate and "http" not in candidate.lower() and len(candidate) > 5:
                    return candidate
                    
    return ""

def crawl_impressum(base_url, timeout=10):
    """
    Crawls base URL and prioritizes subpages to extract generic contact information.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    logger.info(f"Scraping landing page: {base_url}")
    try:
        response = requests.get(base_url, headers=headers, timeout=timeout, allow_redirects=True)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch base URL: HTTP {response.status_code}")
            return {
                "organization_url": base_url,
                "extracted_at": now_utc,
                "source_page_used": base_url,
                "email_source_page": "",
                "address_source_page": "",
                "generic_email": "",
                "address": ""
            }
        base_html = response.text
    except Exception as e:
        logger.error(f"Error fetching base URL: {e}")
        return {
            "organization_url": base_url,
            "extracted_at": now_utc,
            "source_page_used": base_url,
            "email_source_page": "",
            "address_source_page": "",
            "generic_email": "",
            "address": ""
        }
        
    # Analyze the landing page first
    soup = BeautifulSoup(base_html, "html.parser")
    base_text = soup.get_text("\n")
    
    emails = extract_generic_emails(base_text)
    address = extract_physical_address(base_text)
    
    email_res = emails[0] if emails else ""
    email_source_page = base_url if email_res else ""
    address_source_page = base_url if address else ""
    
    # If we found everything on the landing page, return early
    if email_res and address:
        return {
            "organization_url": base_url,
            "extracted_at": now_utc,
            "source_page_used": base_url,
            "email_source_page": base_url,
            "address_source_page": base_url,
            "generic_email": email_res,
            "address": address
        }
        
    # Extract links and prioritize contact pages
    prioritized_links = extract_links(base_url, base_html)
    
    # Visit up to 4 prioritized subpages (priorities 1, 2, 3)
    target_pages = [link for link in prioritized_links if link["priority"] < 4][:4]
    
    logger.info(f"Identified {len(target_pages)} prioritized subpages to crawl.")
    
    for page in target_pages:
        target_url = page["url"]
        logger.info(f"Scraping priority subpage ({page['keyword']}): {target_url}")
        
        try:
            sub_resp = requests.get(target_url, headers=headers, timeout=timeout, allow_redirects=True)
            if sub_resp.status_code != 200:
                continue
                
            sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
            sub_text = sub_soup.get_text("\n")
            
            sub_emails = extract_generic_emails(sub_text)
            sub_address = extract_physical_address(sub_text)
            
            # Fill missing data
            if sub_emails and not email_res:
                email_res = sub_emails[0]
                email_source_page = target_url
            if sub_address and not address:
                address = sub_address
                address_source_page = target_url
                
            # If both are populated, stop crawling subpages
            if email_res and address:
                break
        except Exception as e:
            logger.warning(f"Error fetching subpage {target_url}: {e}")
            continue
            
    if email_source_page and address_source_page:
        source_page_used = (
            email_source_page
            if email_source_page == address_source_page
            else f"email:{email_source_page}|address:{address_source_page}"
        )
    else:
        source_page_used = email_source_page or address_source_page or base_url

    return {
        "organization_url": base_url,
        "extracted_at": now_utc,
        "source_page_used": source_page_used,
        "email_source_page": email_source_page,
        "address_source_page": address_source_page,
        "generic_email": email_res,
        "address": address
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract impressum and generic contact info from organization URLs.")
    parser.add_argument("--url", type=str, required=True, help="Base landing page URL of the organization.")
    args = parser.parse_args()
    
    result = crawl_impressum(args.url)
    import pprint
    pprint.pprint(result)
