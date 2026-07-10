import requests
from bs4 import BeautifulSoup, NavigableString
import pprint
import time
import json
import re
import argparse
import logging
import os

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

# 1. Alle Roh-Tags, die jemals in den Philea-Daten auftauchen können (inkl. Human/Civil Rights)
MASTER_TAGS = [
    "Citizenship, Social Justice & Public Affairs",
    "Civil society, Voluntarism & Non-Profit Sector",
    "Socio-economic Development, Poverty",
    "Socio-economic Development",
    "Food, Agriculture & Nutrition",
    "Recreation, Sport & Well-being",
    "Humanitarian & Disaster Relief",
    "Peace & Conflict Resolution",
    "Youth/Children Development",
    "Sciences & Research",
    "Employment/Workforce",
    "Environment/Climate",
    "Social/Human Services",
    "Arts & Culture",
    "Arts and Culture",
    "Policy development",
    "Education",
    "Health",
    "Animal-Related",
    "Water",
    "Nature",
    "Human/Civil Rights",  # FIX: War vorher vergessen
    "Technology",
    "Digital Transformation",
    "Scientific Research and Technology Transfer",
    "Innovation",
    "tech-enablement"
]

# 2. Die zentrale Mapping-Schmiede (Konsolidiert von ~24 auf 14 Hauptkategorien)
TAG_NORMALIZATION = {
    # Schreibweisen-Korrekturen
    "Arts and Culture": "Arts & Culture",
    "Socio-economic Development": "Socio-economic Development, Poverty",
    "Environment": "Environment/Climate",
    
    # Strategische Zusammenfassungen für ein sauberes Datenmodell
    "Nature": "Environment/Climate",
    "Water": "Environment/Climate",
    "Animal-Related": "Environment/Climate",
    "Employment/Workforce": "Socio-economic Development, Poverty",
    "Social/Human Services": "Socio-economic Development, Poverty",
    "Recreation, Sport & Well-being": "Health",
    "Policy development": "Citizenship, Social Justice & Public Affairs",
    
    # Technology / Digitalization Mapping to tech-enablement
    "Technology": "tech-enablement",
    "Digital Transformation": "tech-enablement",
    "Scientific Research and Technology Transfer": "tech-enablement",
    "Innovation": "tech-enablement",
    "tech-enablement": "tech-enablement"
}

# 3. Die Keyword-Kanten für den Freitext-Fallback (Exakt synchron zu den Normalisierungs-Targets)
KEYWORD_MAPPING = {
    "Environment/Climate": [
        r"climates?", r"emissions?", r"carbon", r"energy transition", r"fossil fuels?",
        r"biodiversity", r"nature conservation", r"planet", r"agroecology", r"built environment",
        r"plastic( pollution)?", r"petrochemical", r"economies of reuse", r"ocean economy", r"maritime", r"gardens?",
        r"animals?", r"wildlife", r"water security", r"water supply"
        # FIX: Fängt 'Animal-Related', 'Nature' & 'Water' ab
    ],
    "Education": [
        r"educations?", r"learnings?", r"schools?", r"scholarships?", r"students?", r"stem", r"teachers?", r"trainings?"
    ],
    "Arts & Culture": [
        r"arts?", r"culture?", r"cultural", r"museums?", r"exhibitions?", r"music", r"artists?", r"heritage",
        r"theatres?", r"villa"
    ],
    "Citizenship, Social Justice & Public Affairs": [
        r"democrac\w+", r"civil societ\w+", r"civic", r"citizenship", r"public affairs",
        r"press freedom", r"independent media", r"advocacy", r"journalism", r"newsrooms?",
        r"social cohesion", r"responsible leadership", r"criminal justice", r"social change", r"polycrisis",
        r"policy development"  # FIX: Fängt 'Policy development' ab
    ],
    "Human/Civil Rights": [
        r"human rights", r"civil rights", r"gender equality", r"women’s rights",
        r"lgbti\+", r"feminist", r"discrimination", r"gender justice"
    ],
    "Youth/Children Development": [
        r"children", r"youths?", r"child", r"young people", r"early childhood", r"infants?", r"neonatal",
        r"0-5 year olds"
    ],
    "Socio-economic Development, Poverty": [
        r"poverty", r"low-income", r"vulnerabilit\w+", r"marginalized", r"homeless\w*",
        r"social inclusion", r"disadvantaged", r"social justice", r"social innovation",
        r"economic justice", r"social leaders?", r"social development", r"economic development",
        r"employ\w+", r"workforce", r"jobs?", r"labour", r"social services?"
        # FIX: Fängt 'Employment' & 'Social Services' ab
    ],
    "Health": [
        r"health\w*", r"medical", r"diseases?", r"healthcare", r"illness\w*", r"pain therapy", r"sanitation",
        r"sports?", r"recreation", r"well[- ]being"  # FIX: Fängt 'Recreation, Sport & Well-being' ab
    ],
    "Sciences & Research": [
        r"research\w*", r"scientific", r"sciences?", r"phd", r"academia", r"universit\w+"
    ],
    "Food, Agriculture & Nutrition": [
        r"food", r"agriculture", r"nutrition", r"farming", r"diets?"
    ],
    "Humanitarian & Disaster Relief": [
        r"disaster relief", r"humanitarian", r"emergency response", r"refugees?", r"asylum seekers?", r"migration",
        r"foreign aid"
    ],
    "Civil society, Voluntarism & Non-Profit Sector": [
        r"philanthrop\w+", r"fundraising", r"donors?", r"grant-making", r"fiscal sponsorship"
    ],
    "Peace & Conflict Resolution": [
        r"peacebuilding", r"conflict sensitivity", r"peace work"
    ],
    "tech-enablement": [
        r"tech\w*", r"technolog\w*", r"digital\w*", r"software", r"data science", r"artificial intelligence", r"ai\b",
        r"\bit\b(?=\s+(systems?|services?|infrastructure|department|team|strategy|support))"
    ]
}

GEO_TAXONOMY = {
    "Worldwide": {
        "Global": r"global\w*",
        "Worldwide": r"worldwide",
        "International": r"international\w*",
        "World": r"\bworld\b"
    },
    "Global South / Majority World": {
        "Global South": r"global south",
        "Majority World": r"majority world",
        "Developing Countries": r"developing world|developing countr\w+|low and middle income"
    },
    "Europe (Western / General)": {
        "Europe": r"europ\w+",
        "European Union": r"european union|\beu\b",
        "United Kingdom": r"\buk\b|united kingdom|great britain|london|scotland|west midlands|english|england|wales|wirral|merseyside|hillingdon|oxfordshire|cambridgeshire|essex|hertfordshire|norfolk|suffolk|cambridge|hayes|harlington|cheshire|warrington|buckland|cornwall|dorset|somerset|wiltshire|devon|hampshire",
        "Ireland": r"ireland|irish",
        "France": r"franc\w+",
        "Germany": r"german\w+",
        "Switzerland": r"switzerland|swiss",
        "Austria": r"austria\w*",
        "Luxembourg": r"luxembourg\w*",
        "Belgium": r"belgium\w*|belgian\w*|brussels",
        "Netherlands": r"netherlands|dutch|the hague|delft|zoetermeer|leiden|noordwijk",
        "Monaco": r"monaco\w*",
        "Liechtenstein": r"liechtenstein\w*",
        "Andorra": r"andorra\w*"
    },
    "Europe (Nordic Region)": {
        "Nordic Region": r"nordic",
        "Denmark": r"denmark|danish",
        "Finland": r"finland|finnish|herlin", # fängt Herlin-Stiftung ab
        "Sweden": r"sweden|swedish|\bse\b",
        "Norway": r"norway|norwegian|kristiansand",
        "Iceland": r"iceland\w*",
        "Greenland": r"greenland",
        "Faroe Islands": r"faroe islands"
    },
    "Europe (Southern / Mediterranean)": {
        "Spain": r"spain|spanish|galicia",
        "Italy": r"ital\w+|sicily|sardinia|piedmont|aosta valley|modena|parma|padua|rovigo|tuscany|florence|grosseto|arezzo|cuneo|alto adige|lucca|lombardy|torino|bologna",
        "Greece": r"gree\w+",
        "Portugal": r"portug\w+",
        "Turkey": r"turk\w+|türkiye",
        "Malta": r"malta\w*",
        "Cyprus": r"cyprus|cypriot\w*",
        "San Marino": r"san marino",
        "Vatican City": r"vatican|holy see"
    },
    "Europe (Central & Eastern / Balkans)": {
        "Balkans": r"balkans?|western balkans|serbian?|croatian?|slovenian?|bosnia\w*",
        "Central & Eastern Europe": r"cee\b|eastern europe|central and eastern europe|baltic\w*",
        "Slovakia": r"slovakia\w*",
        "Bulgaria": r"bulgari\w*",
        "Kosovo": r"kosovo\w*",
        "Croatia": r"croatia\w*",
        "Slovenia": r"slovenia\w*",
        "Ukraine": r"ukrain\w*",
        "Estonia": r"estonia\w*",
        "Lithuania": r"lithuania\w*",
        "Poland": r"\bpoland\b|polish\w*|fundacja",
        "Latvia": r"latvia\w*",
        "Georgia": r"georgia\w*",
        "Czech Republic": r"czech\w*",
        "Romania": r"romani\w*",
        "Hungary": r"hungar\w*",
        "Belarus": r"belarus\w*",
        "Moldova": r"moldova\w*",
        "Russia": r"russia\w*",
        "Albania": r"albania\w*",
        "Bosnia and Herzegovina": r"bosnia\w*",
        "North Macedonia": r"macedonia\w*",
        "Montenegro": r"montenegro\w*",
        "Serbia": r"serbia\w*",
        "Armenia": r"armenia\w*",
        "Azerbaijan": r"azerbaijan\w*"
    },
    "North America": {
        "United States": r"united states|\busa\b|\bus\b|america\w*|flint|michigan",
        "Canada": r"canada\w*"
    },
    "Latin America & Caribbean": {
        "Latin America": r"latin america|south america|central america",
        "Caribbean": r"caribbean",
        "Brazil": r"brazil\w*",
        "Mexico": r"mexico\w*",
        "Colombia": r"colombia\w*",
        "Peru": r"peru\w*",
        "Bolivia": r"bolivia\w*",
        "Ecuador": r"ecuador\w*",
        "Guyana": r"guyana\w*",
        "El Salvador": r"el salvador",
        "Argentina": r"argentina\w*",
        "Chile": r"chile\w*|chili\w*",
        "Venezuela": r"venezuela\w*",
        "Paraguay": r"paraguay\w*",
        "Uruguay": r"uruguay\w*",
        "Suriname": r"suriname\w*",
        "Panama": r"panama\w*",
        "Costa Rica": r"costa rica\w*",
        "Nicaragua": r"nicaragua\w*",
        "Honduras": r"honduras\w*",
        "Guatemala": r"guatemala\w*",
        "Belize": r"belize\w*",
        "Cuba": r"cuba\w*",
        "Dominican Republic": r"dominican republic",
        "Haiti": r"haiti\w*",
        "Jamaica": r"jamaica\w*",
        "Bahamas": r"bahamas\w*",
        "Trinidad and Tobago": r"trinidad\w*",
        "Barbados": r"barbados\w*",
        "Saint Lucia": r"saint lucia",
        "Grenada": r"grenada\w*",
        "Saint Vincent and the Grenadines": r"saint vincent",
        "Antigua and Barbuda": r"antigua\w*",
        "Dominica": r"\bdominica\b",
        "Saint Kitts and Nevis": r"saint kitts"
    },
    "Africa / Sub-Saharan Africa": {
        "Africa": r"afric\w+",
        "Sub-Saharan Africa": r"sub-saharan",
        "Tanzania": r"tanzania\w*",
        "Kenya": r"kenya\w*",
        "Ethiopia": r"ethiopia\w*",
        "Uganda": r"uganda\w*",
        "Malawi": r"malawi\w*",
        "Ghana": r"ghana\w*",
        "Burkina Faso": r"burkina faso",
        "Zambia": r"zambia\w*",
        "Sierra Leone": r"sierra leone",
        "Madagascar": r"madagascar\w*",
        "Rwanda": r"rwanda\w*",
        "Zimbabwe": r"zimbabwe\w*",
        "South Africa": r"south africa",
        "Botswana": r"botswana\w*",
        "Namibia": r"namibia\w*",
        "Senegal": r"senegal\w*",
        "Gambia": r"gambia\w*",
        "Togo": r"togo\w*",
        "Benin": r"benin\w*",
        "Mali": r"mali\w*",
        "Angola": r"angola\w*",
        "Nigeria": r"nigeria\w*",
        "Cameroon": r"cameroon\w*",
        "Ivory Coast": r"ivory coast|côte d\.ivoire",
        "Mozambique": r"mozambique\w*",
        "Burundi": r"burundi\w*",
        "South Sudan": r"south sudan",
        "Somalia": r"somalia\w*",
        "Eritrea": r"eritrea\w*",
        "Djibouti": r"djibouti\w*",
        "Central African Republic": r"central african republic",
        "Chad": r"\bchad\b",
        "Congo": r"(?<!democratic republic of )(?<!democratic republic of the )\bcongo\w*",
        "Democratic Republic of the Congo": r"democratic republic of (?:the )?congo|\bdrc\b",
        "Gabon": r"gabon\w*",
        "Equatorial Guinea": r"equatorial guinea",
        "Sao Tome and Principe": r"sao tome",
        "Cabo Verde": r"cabo verde|cape verde",
        "Guinea": r"\bguinea\b(?!-bissau)",
        "Guinea-Bissau": r"guinea-bissau",
        "Liberia": r"liberia\w*",
        "Mauritania": r"mauritania\w*",
        "Niger": r"\bniger\b",
        "Sudan": r"\bsudan\w*",
        "Lesotho": r"lesotho\w*",
        "Eswatini": r"eswatini|swaziland",
        "Seychelles": r"seychelles\w*",
        "Mauritius": r"mauritius\w*",
        "Comoros": r"comoros\w*"
    },
    "Asia & Pacific": {
        "Asia": r"asia\w*",
        "Pacific": r"pacific",
        "India": r"india\w*",
        "China": r"china\w*",
        "Vietnam": r"vietnam\w*",
        "Cambodia": r"cambodia\w*",
        "Laos": r"laos\w*",
        "Myanmar": r"myanmar\w*",
        "Thailand": r"thailand\w*",
        "Nepal": r"nepal\w*",
        "Sri Lanka": r"sri lanka",
        "Indonesia": r"indonesia\w*",
        "Bangladesh": r"bangladesh\w*",
        "Philippines": r"philippines?",
        "Afghanistan": r"afghanistan\w*",
        "Australia": r"australia\w*|singapore",
        "Japan": r"japan\w*",
        "South Korea": r"south korea|korean?",
        "North Korea": r"north korea",
        "Taiwan": r"taiwan\w*",
        "Pakistan": r"pakistan\w*",
        "Bhutan": r"bhutan\w*",
        "Maldives": r"maldives\w*",
        "Singapore": r"singapore\w*",
        "Brunei": r"brunei\w*",
        "East Timor": r"east timor|timor-leste",
        "Kazakhstan": r"kazakhstan\w*",
        "Uzbekistan": r"uzbekistan\w*",
        "Turkmenistan": r"turkmenistan\w*",
        "Kyrgyzstan": r"kyrgyzstan\w*",
        "Tajikistan": r"tajikistan\w*",
        "Mongolia": r"mongolia\w*",
        "New Zealand": r"new zealand",
        "Papua New Guinea": r"papua new guinea",
        "Solomon Islands": r"solomon islands",
        "Vanuatu": r"vanuatu\w*",
        "Fiji": r"fiji\w*",
        "Samoa": r"\bsamoa\b",
        "Tonga": r"tonga\w*",
        "Tuvalu": r"tuvalu\w*",
        "Kiribati": r"kiribati\w*",
        "Nauru": r"nauru\w*",
        "Marshall Islands": r"marshall islands",
        "Micronesia": r"micronesia\w*",
        "Palau": r"palau\w*"
    },
    "Middle East & North Africa (MENA)": {
        "Middle East": r"middle east",
        "MENA": r"mena\b",
        "Arab World": r"arab world",
        "Israel": r"israel\w*",
        "Palestine": r"palestin\w+",
        "Yemen": r"yemen\w*",
        "Jordan": r"jordan\w*",
        "Egypt": r"egypt\w*",
        "Lebanon": r"leban\w+",
        "Syria": r"syria\w*",
        "Iraq": r"iraq\w*",
        "Iran": r"iran\w*|persia\w*",
        "Saudi Arabia": r"saudi\w*",
        "Oman": r"oman\w*",
        "United Arab Emirates": r"united arab emirates|\buae\b",
        "Qatar": r"qatar\w*",
        "Bahrain": r"bahrain\w*",
        "Kuwait": r"kuwait\w*",
        "Morocco": r"moroc\w+",
        "Algeria": r"algeria\w*",
        "Tunisia": r"tunisia\w*",
        "Libya": r"libya\w*"
    }
}

def extract_tags(members):
    def extract_tags_final(raw_text):
        if not raw_text:
            return []
        
        found_tags = set()
        text_lower = raw_text.lower()
        
        for tag, keywords in KEYWORD_MAPPING.items():
            for kw in keywords:
                # Use lookarounds instead of word boundaries to correctly match lgbti+ and similar:
                # (?<![a-zA-Z0-9]) ensure no alphanumeric prefix.
                # (?![a-zA-Z0-9]) ensure no alphanumeric suffix.
                pattern = r'(?<![a-zA-Z0-9])(?:' + kw + r')(?![a-zA-Z0-9])'
                if re.search(pattern, text_lower):
                    found_tags.add(tag)
        
        return sorted(list(found_tags))
    
    def extract_tags_robust(raw_text):
        if not raw_text:
            return []
        
        # Tags stehen bei Philea IMMER ganz am Anfang vor dem Fließtext.
        # Wir untersuchen daher nur die ersten 600 Zeichen (Sicherheitsfenster).
        zone = raw_text[:600].lower()
        
        # Text-Normalisierung: Zeilenumbrüche und alle Arten von Bindestrichen/Bulletpoints
        # durch einfache Leerzeichen ersetzen. Kommas bleiben als Trenner erhalten.
        zone_clean = re.sub(r'[\s\–\—\-]+', ' ', zone)
        
        found_tags = set()
        
        # Wichtig: Wir sortieren nach Länge (absteigend), damit lange Phrasen wie
        # "Socio-economic Development, Poverty" vor "Education" oder "Health" gematched werden.
        sorted_master_tags = sorted(MASTER_TAGS, key=len, reverse=True)
        
        for original_tag in sorted_master_tags:
            tag_clean = original_tag.lower()
            tag_clean = re.sub(r'[\s\–\—\-]+', ' ', tag_clean)
            
            if tag_clean in zone_clean:
                # Sicherheits-Check für sehr kurze Tags (z.B. "Health"), damit sie nicht
                # fälschlicherweise in Wörtern wie "Healthcare" oder "Healthy" matchen.
                if len(tag_clean) <= 10:
                    # Regex prüft, ob vor und nach dem Tag kein Buchstabe (a-z) steht
                    pattern = r'(?<![a-z])' + re.escape(tag_clean) + r'(?![a-z])'
                    if re.search(pattern, zone_clean):
                        found_tags.add(original_tag)
                        # Löschen, um Doppel-Treffer zu vermeiden
                        zone_clean = zone_clean.replace(tag_clean, " ")
                else:
                    found_tags.add(original_tag)
                    zone_clean = zone_clean.replace(tag_clean, " ")
        
        # Normalisieren (z.B. "Arts and Culture" -> "Arts & Culture")
        final_tags = [TAG_NORMALIZATION.get(t, t) for t in found_tags]
        
        return sorted(list(set(final_tags)))
    
    parsed_results = {}
    for member in members:
        member_name = member.get("name", "Unknown")
        info = member.get("philea_info", {})
        if not isinstance(info, dict):
            info = {}
            
        tags = extract_tags_robust(info.get("Programme Areas", ""))
        if not tags:  # Fallback auf die Freitext-Analyse, wenn keine Tags gefunden wurden
            tags = extract_tags_final(info.get("Programme Areas", ""))
        if not tags:  # Letzte Chance: Manchmal stehen die Tags nicht unter "Programme Areas", sondern nur im allgemeinen "About"-Text
            tags = extract_tags_final(info.get("About", ""))
        if not tags:
            tags = extract_tags_final(info.get("Mission", ""))
            
        parsed_results[member_name] = tags
    
    logging.info(f"Total processed members for tags: {len(parsed_results)}")
    empty_count = sum(1 for tags in parsed_results.values() if not tags)
    logging.info(f"Number of members without tags: {empty_count}")
    
    for member in members:
        member_name = member.get("name", "Unknown")
        member["tags_focus"] = sorted(parsed_results.get(member_name, []))
        
def extract_geo(members):
    # HILFS-STRUKTUREN AUTOMATISCH GENERIERT (Keine doppelte Pflege nötig!)
    ALL_COUNTRIES = []
    COUNTRY_TO_MACRO = {}
    for macro_region, country_dict in GEO_TAXONOMY.items():
        for country_name in country_dict.keys():
            ALL_COUNTRIES.append(country_name)
            COUNTRY_TO_MACRO[country_name] = macro_region
    
    # Sortierung nach Länge (absteigend) schützt "Sub-Saharan Africa" vor "Africa"
    SORTED_COUNTRIES = sorted(ALL_COUNTRIES, key=len, reverse=True)
    
    def extract_geos_robust(raw_text):
        """ Stufe 1: Sucht nach exakten Begriffen im Header-Bereich """
        if not raw_text:
            return {}
        
        zone = raw_text.lower()
        zone_clean = re.sub(r'[\s\–\—\-]+', ' ', zone)
        found = {}
        
        for country in SORTED_COUNTRIES:
            c_clean = country.lower()
            if c_clean in zone_clean:
                is_match = False
                if len(c_clean) <= 5:  # Für kurze Token wie UK, US, Spain
                    # Safe lookaround matching:
                    pattern = r'(?<![a-zA-Z0-9])' + re.escape(c_clean) + r'(?![a-zA-Z0-9])'
                    if re.search(pattern, zone_clean):
                        is_match = True
                else:
                    is_match = True
                
                if is_match:
                    macro = COUNTRY_TO_MACRO[country]
                    if macro not in found:
                        found[macro] = set()
                    found[macro].add(country)
                    zone_clean = zone_clean.replace(c_clean, " ")  # Konsumieren
        
        return {k: sorted(list(v)) for k, v in found.items()}
    
    def extract_geos_final(raw_text):
        """ Stufe 2: Tiefe Regex-Suche im gesamten Freitext """
        if not raw_text:
            return {}
        
        found = {}
        text_lower = raw_text.lower()
        
        for macro, country_dict in GEO_TAXONOMY.items():
            for country_name, pattern in country_dict.items():
                # FIX: Wrap pattern in a non-capturing group and use alphanumeric lookarounds
                # This fixes the precedence bugs with A|B|C and boundaries:
                regex_pattern = r'(?<![a-zA-Z0-9])(?:' + pattern + r')(?![a-zA-Z0-9])'
                if re.search(regex_pattern, text_lower):
                    if macro not in found:
                        found[macro] = set()
                    found[macro].add(country_name)
        
        return {k: sorted(list(v)) for k, v in found.items()}
    
    parsed_geo_results = {}
    for member in members:
        member_name = member.get("name", "Unknown")
        info = member.get("philea_info", {})
        if not isinstance(info, dict):
            info = {}
            
        raw_geo_text = info.get("Geographic Focus", "")
        geos = {}
        if raw_geo_text and raw_geo_text.strip() and raw_geo_text.strip() != "$e":
            robust = extract_geos_robust(raw_geo_text)
            final_geos = extract_geos_final(raw_geo_text)
            geos = dict(robust)
            for macro, countries in final_geos.items():
                if macro not in geos:
                    geos[macro] = []
                geos[macro] = sorted(list(set(geos[macro]) | set(countries)))
        
        # If no geolocations could be extracted from Geographic Focus, fall back to other fields
        if not geos:
            addr = member.get("address", "") or info.get("address", "") or ""
            country = member.get("country", "") or member.get("position", {}).get("country", "") or ""
            aop = info.get("areaOfOperation", "") or ""
            about = info.get("About", "") or ""
            name = member.get("name", "")
            fallback_text = f"{addr} {country} {aop} {about} {name}".strip()
            if fallback_text:
                robust = extract_geos_robust(fallback_text)
                final_geos = extract_geos_final(fallback_text)
                geos = dict(robust)
                for macro, countries in final_geos.items():
                    if macro not in geos:
                        geos[macro] = []
                    geos[macro] = sorted(list(set(geos[macro]) | set(countries)))
        
        parsed_geo_results[member_name] = geos
    
    # Zurückschreiben in dein Haupt-Objekt
    for member in members:
        member_name = member.get("name", "Unknown")
        member["geo_locations"] = parsed_geo_results.get(member_name, {})
        
def save_data(members, path):
    logging.info(f"Saving preprocessed data to {path}...")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(members, f, ensure_ascii=False, indent=4)
        logging.info("Preprocessed data saved successfully.")
    except Exception as e:
        logging.error(f"Failed to save data to {path}: {e}")
        
def load_data(path):
    logging.info(f"Loading raw data from {path}...")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load data from {path}: {e}")
        raise e
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preprocess Philea organization data to extract tags and geographic focus.")
    parser.add_argument(
        "--input", 
        type=str, 
        default=os.path.join(os.path.dirname(__file__), "../data/raw/philea_members.json"),
        help="Path to the raw JSON file containing scraped member data."
    )
    parser.add_argument(
        "--output", 
        type=str, 
        default=os.path.join(os.path.dirname(__file__), "../data/preprocessed/philea_members_preprocessed.json"),
        help="Path where the preprocessed JSON file should be saved."
    )
    args = parser.parse_args()

    try:
        members = load_data(args.input)
        extract_tags(members)
        extract_geo(members)
        save_data(members, args.output)
    except Exception as e:
        logging.error(f"Preprocessing pipeline failed: {e}")