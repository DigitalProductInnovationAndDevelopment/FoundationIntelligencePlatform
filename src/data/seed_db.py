import os
import sys
import sqlite3
import json
import random
import datetime
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_db")

# Add project src path to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import data.db_loader as db_loader

CHARITIES_SEED_DATA = [
    (213890, "Save the Children Fund", "Charity", "https://www.savethechildren.org.uk", "supportercare@savethechildren.org.uk", "London", 300000000.0, 290000000.0, '[{"tag": "Youth/Children Development", "source": "exact_match"}, {"tag": "Humanitarian & Disaster Relief", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"], "Worldwide": ["Global"]}'),
    (1089464, "Cancer Research UK", "Charity", "https://www.cancerresearchuk.org", "supporter@cancerresearch.org", "London", 650000000.0, 620000000.0, '[{"tag": "Sciences & Research", "source": "exact_match"}, {"tag": "Health", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"]}'),
    (205846, "The National Trust for Places of Historic Interest or Natural Beauty", "Charity", "https://www.nationaltrust.org.uk", "enquiries@nationaltrust.org.uk", "Swindon", 680000000.0, 640000000.0, '[{"tag": "Environment/Climate", "source": "exact_match"}, {"tag": "Arts & Culture", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"]}'),
    (209603, "Royal National Lifeboat Institution", "Charity", "https://rnli.org", "enquiries@rnli.org.uk", "Poole", 200000000.0, 190000000.0, '[{"tag": "Health", "source": "exact_match"}, {"tag": "Humanitarian & Disaster Relief", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"], "Ireland": ["Ireland"]}'),
    (261017, "Macmillan Cancer Support", "Charity", "https://www.macmillan.org.uk", "questions@macmillan.org.uk", "London", 230000000.0, 220000000.0, '[{"tag": "Health", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"]}'),
    (216250, "Barnardo's", "Charity", "https://www.barnardos.org.uk", "info@barnardos.org.uk", "London", 310000000.0, 305000000.0, '[{"tag": "Youth/Children Development", "source": "exact_match"}, {"tag": "Socio-economic Development, Poverty", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"]}'),
    (202918, "Oxfam GB", "Charity", "https://www.oxfam.org.uk", "oxfam@oxfam.org.uk", "Oxford", 400000000.0, 395000000.0, '[{"tag": "Socio-economic Development, Poverty", "source": "exact_match"}, {"tag": "Humanitarian & Disaster Relief", "source": "exact_match"}]', '{"Worldwide": ["Global"], "Europe (Western / General)": ["United Kingdom"]}'),
    (220949, "The British Red Cross Society", "Charity", "https://www.redcross.org.uk", "information@redcross.org.uk", "London", 270000000.0, 260000000.0, '[{"tag": "Humanitarian & Disaster Relief", "source": "exact_match"}, {"tag": "Health", "source": "exact_match"}]', '{"Worldwide": ["Global"], "Europe (Western / General)": ["United Kingdom"]}'),
    (326568, "Comic Relief", "Funder", "https://www.comicrelief.com", "grantsinfo@comicrelief.com", "London", 80000000.0, 75000000.0, '[{"tag": "Civil society, Voluntarism & Non-Profit Sector", "source": "exact_match"}, {"tag": "Socio-economic Development, Poverty", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"], "Worldwide": ["Global"]}'),
    (1128267, "Age UK", "Charity", "https://www.ageuk.org.uk", "contact@ageuk.org.uk", "London", 160000000.0, 155000000.0, '[{"tag": "Socio-economic Development, Poverty", "source": "exact_match"}]', '{"Europe (Western / General)": ["United Kingdom"]}')
]

REGIONS = ["London", "North West", "South East", "South West", "East of England", "West Midlands", "East Midlands", "Yorkshire and the Humber", "Scotland", "Wales", "Northern Ireland"]

GRANT_DESCRIPTIONS = [
    "Support for local community resilience projects.",
    "Emergency humanitarian assistance fund.",
    "Research project funding for healthcare solutions.",
    "Thematic digital transformation enablement grants.",
    "Developing youth engagement and leadership programs.",
    "Support for refugee and integration activities.",
    "Climate transition and environmental awareness campaigns.",
    "Food security and basic livelihood enhancement support.",
    "Social inclusion and diversity awareness workshops.",
    "Capacity building for regional volunteer coordination."
]

TAGS = ["tech-enablement", "Diversity & Inclusion", "Education", "Health", "Environment/Climate", "Socio-economic Development, Poverty", "Youth/Children Development", "Sciences & Research", "Humanitarian & Disaster Relief"]

def seed_synthetic_data(conn):
    """Inserts realistic mock records if no flat data files are available."""
    cursor = conn.cursor()
    logger.info("Generating synthetic seed data...")

    # 1. Insert Charities
    for row in CHARITIES_SEED_DATA:
        cursor.execute(
            """
            INSERT OR REPLACE INTO charities (
                charity_id, name, type, website, email, address, city, state, country,
                latitude, longitude, annual_income, annual_expenditure, thematic_focus,
                geographic_focus, raw_cc_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'United Kingdom', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                f"12 Main St, {row[5]}",
                row[5],
                "England",
                51.5074 + random.uniform(-0.5, 0.5), # Realistic UK coords
                -0.1278 + random.uniform(-0.5, 0.5),
                row[6],
                row[7],
                row[8],
                row[9],
                json.dumps({
                    "all_details": {
                        "charity_name": row[1],
                        "reg_status": "R",
                        "latest_income": row[6],
                        "latest_expenditure": row[7],
                        "web": row[3],
                        "email": row[4],
                        "phone": "020 7946 0192"
                    }
                })
            )
        )
    logger.info(f"Seeded {len(CHARITIES_SEED_DATA)} synthetic charities.")

    # 2. Insert Grants (100 synthetic grant transactions)
    grant_count = 0
    start_date = datetime.date(2024, 1, 1)
    
    for i in range(100):
        # Pick random funder (either Funder or rich charity)
        funder = random.choice(CHARITIES_SEED_DATA)
        # Pick random recipient (distinct from funder)
        recipient = random.choice([c for c in CHARITIES_SEED_DATA if c[0] != funder[0]])
        
        amount = round(random.uniform(5000, 150000), 2)
        grant_date = start_date + datetime.timedelta(days=random.randint(0, 800))
        desc = random.choice(GRANT_DESCRIPTIONS)
        grant_region = random.choice(REGIONS)
        grant_tags = [random.choice(TAGS), random.choice(TAGS)]
        # Deduplicate tags
        grant_tags = list(set(grant_tags))
        
        grant_id = f"360G-SEED-{i+1:05d}"
        
        cursor.execute(
            """
            INSERT OR REPLACE INTO grants (
                grant_id, funding_charity_id, recipient_name, recipient_charity_id,
                amount_eur, currency, description, date, recipient_latitude,
                recipient_longitude, recipient_region, tags, geographic_focus
            ) VALUES (?, ?, ?, ?, ?, 'GBP', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grant_id,
                funder[0],
                recipient[1],
                recipient[0],
                amount * 1.20, # Converted to EUR
                desc,
                grant_date.isoformat(),
                51.5074 + random.uniform(-0.4, 0.4),
                -0.1278 + random.uniform(-0.4, 0.4),
                grant_region,
                json.dumps(grant_tags),
                json.dumps({"Europe (Western / General)": ["United Kingdom"]})
            )
        )
        grant_count += 1
        
    conn.commit()
    logger.info(f"Seeded {grant_count} synthetic grants.")

def main():
    db_file = os.path.join(src_dir, "data", "charities.db")
    prep_dir = os.path.join(src_dir, "data", "preprocessed")
    charities_jsonl = os.path.join(prep_dir, "charities.jsonl")
    grants_jsonl = os.path.join(prep_dir, "grants.jsonl")

    conn = db_loader.create_connection(db_file)
    if not conn:
        logger.error("Failed to connect to SQLite database.")
        sys.exit(1)

    try:
        db_loader.create_tables(conn)
        
        # Check if actual preprocessed jsonl files exist
        if os.path.exists(charities_jsonl) and os.path.exists(grants_jsonl):
            logger.info("Actual preprocessed data found. Loading from jsonl files...")
            db_loader.load_jsonl_to_db(conn, charities_jsonl, grants_jsonl)
        else:
            logger.info("Preprocessed JSONL files not found. Seeding with realistic synthetic data...")
            seed_synthetic_data(conn)
            
        logger.info("Database successfully seeded.")
    except Exception as e:
        logger.error(f"Failed to seed database: {e}")
        sys.exit(1)
    finally:
        conn.close()
        logger.info("Database connection closed.")

if __name__ == "__main__":
    main()
