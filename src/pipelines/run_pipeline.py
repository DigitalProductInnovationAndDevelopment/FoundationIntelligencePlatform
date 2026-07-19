import os
import sys
import argparse
import logging
import json

# Ensure src directory is in sys.path so imports work regardless of working directory
PIPELINES_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(PIPELINES_DIR)
PROJECT_ROOT = SRC_DIR
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("run_pipeline")

from scrapers.register_of_charities import scrape as scrape_cc, save_data as save_raw_cc
import importlib
giving = importlib.import_module("scrapers.360giving")
scrape_ts = giving.scrape
save_raw_ts = giving.save_data
from preprocessing.consolidate import consolidate_uk_datasets
from preprocessing.extract_impressum import crawl_impressum
import data.db_loader as db_loader

def load_existing_raw(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read existing raw data at {path}: {e}")
    return []

def run_pipeline(args):
    source = args.source.lower()
    logger.info("=========================================")
    logger.info(f"Starting {source.upper()} Data Pipeline Execution")
    logger.info("=========================================")

    # Setup Paths
    raw_cc_path = args.raw_cc_output or os.path.join(PROJECT_ROOT, "data/raw/register_of_charities_results.json")
    raw_ts_path = args.raw_ts_output or os.path.join(PROJECT_ROOT, "data/raw/threesixtygiving_results.json")
    charities_jsonl_path = os.path.join(PROJECT_ROOT, "data/preprocessed/charities.jsonl")
    grants_jsonl_path = os.path.join(PROJECT_ROOT, "data/preprocessed/grants.jsonl")
    db_file = os.path.join(PROJECT_ROOT, "data/charities.db")

    if source == "register_of_charities":
        existing = load_existing_raw(raw_cc_path)
        completed_numbers = {int(x["registered_charity_number"]) for x in existing if "registered_charity_number" in x}
        
        # Seed charity numbers if none are provided
        reg_numbers = args.reg_numbers
        search_term = args.search
        if not reg_numbers and not search_term:
            # Default Seed list: Oxfam (202918), British Red Cross (220949), Comic Relief (326568)
            reg_numbers = [202918, 220949, 326568]
            logger.info(f"No specific reg-numbers or search provided. Using default seeds: {reg_numbers}")

        if args.skip_scrape:
            logger.info(f"Skipping scraping phase. Loading existing raw data from: {raw_cc_path}")
            cc_records = existing
        else:
            logger.info("Step 1: Scraping Charity Commission API...")
            new_records = scrape_cc(
                registered_numbers=reg_numbers,
                search_name=search_term,
                limit=args.limit,
                sleep_time=args.sleep,
                timeout=args.timeout,
                completed_numbers=completed_numbers
            )
            
            # Merge and save
            merged_dict = {int(x["registered_charity_number"]): x for x in existing}
            for rec in new_records:
                merged_dict[int(rec["registered_charity_number"])] = rec
            
            cc_records = list(merged_dict.values())
            save_raw_cc(cc_records, raw_cc_path)
            logger.info(f"Charity Commission scraping complete. Raw records saved to: {raw_cc_path}")

    elif source == "360giving":
        existing = load_existing_raw(raw_ts_path)
        completed_org_ids = {x["org_id"] for x in existing if "org_id" in x}
        
        org_ids = args.org_ids
        all_orgs = args.all_orgs
        if not org_ids and not all_orgs:
            # Seed 360Giving orgs: Oxfam (GB-CHC-202918), Red Cross (GB-CHC-220949)
            org_ids = ["GB-CHC-202918", "GB-CHC-220949"]
            logger.info(f"No specific org IDs or all-orgs flag provided. Using default seeds: {org_ids}")

        if args.skip_scrape:
            logger.info(f"Skipping scraping phase. Loading existing raw data from: {raw_ts_path}")
            ts_records = existing
        else:
            logger.info("Step 1: Scraping 360Giving API...")
            new_records = scrape_ts(
                org_ids=org_ids,
                all_organisations=all_orgs,
                scrape_grants=True,
                limit=args.limit,
                sleep_time=args.sleep,
                timeout=args.timeout,
                completed_org_ids=completed_org_ids
            )
            
            # Merge and save
            merged_dict = {x["org_id"]: x for x in existing}
            for rec in new_records:
                merged_dict[rec["org_id"]] = rec
                
            ts_records = list(merged_dict.values())
            save_raw_ts(ts_records, raw_ts_path)
            logger.info(f"360Giving scraping complete. Raw records saved to: {raw_ts_path}")

    elif source == "consolidate":
        logger.info("Step 1: Loading raw scraper records...")
        cc_records = load_existing_raw(raw_cc_path)
        ts_records = load_existing_raw(raw_ts_path)

        if not cc_records and not ts_records:
            logger.error("No raw scraped records found. Please scrape data first.")
            sys.exit(1)

        logger.info(f"Loaded {len(cc_records)} Charity Commission and {len(ts_records)} 360Giving records.")
        logger.info("Step 2: Consolidating datasets & mapping UK charity numbers...")
        
        charities_list, grants_list = consolidate_uk_datasets(cc_records, ts_records)

        # Step 2.5: Optional Impressum enrichment for missing details
        if not args.skip_contact_crawler:
            logger.info("Step 2.5: Running website Impressum scraper for missing contact details...")
            for i, c in enumerate(charities_list, 1):
                email_missing = not c.get("email") or str(c.get("email")).strip() == ""
                address_missing = not c.get("address") or str(c.get("address")).strip() == ""
                
                if (email_missing or address_missing) and c.get("website"):
                    website = c["website"]
                    if not website.startswith(("http://", "https://")):
                        website = "https://" + website
                    logger.info(f"[{i}/{len(charities_list)}] Crawling missing contact info for {c['name']} ({website})")
                    try:
                        impressum = crawl_impressum(website, timeout=8)
                        if impressum:
                            if email_missing and impressum.get("generic_email"):
                                c["email"] = impressum["generic_email"]
                                logger.info(f"  -> Found email: {impressum['generic_email']}")
                            if address_missing and impressum.get("address"):
                                c["address"] = impressum["address"]
                                logger.info(f"  -> Found address: {impressum['address']}")
                    except Exception as e:
                        logger.warning(f"  -> Failed to crawl {c['name']}: {e}")

        # Step 3: Export flat JSON Lines files
        logger.info("Step 3: Exporting flat relational tables to JSONL...")
        os.makedirs(os.path.dirname(charities_jsonl_path), exist_ok=True)
        
        with open(charities_jsonl_path, "w", encoding="utf-8") as f:
            for item in charities_list:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info(f"Exported charities table to: {charities_jsonl_path}")

        with open(grants_jsonl_path, "w", encoding="utf-8") as f:
            for item in grants_list:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info(f"Exported grants table to: {grants_jsonl_path}")

        # Step 4: Loading SQLite DB
        logger.info("Step 4: Loading data into SQLite Database...")
        db_loader.main(db_path=db_file, preprocessed_dir=os.path.dirname(charities_jsonl_path))
        logger.info(f"SQLite database successfully loaded at: {db_file}")

    else:
        logger.error(f"Unsupported pipeline source: {source}")
        sys.exit(1)

    logger.info("=========================================")
    logger.info(f"Pipeline Source '{source.upper()}' Completed Successfully!")
    logger.info("=========================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate UK Charities and 360Giving Data Pipelines.")
    parser.add_argument(
        "--source",
        type=str,
        choices=["360giving", "register_of_charities", "consolidate"],
        default="consolidate",
        help="Pipeline phase to execute."
    )
    parser.add_argument(
        "--raw-cc-output",
        type=str,
        default=None,
        help="Path where the Charity Commission raw scraped JSON is stored."
    )
    parser.add_argument(
        "--raw-ts-output",
        type=str,
        default=None,
        help="Path where the 360Giving raw scraped JSON is stored."
    )
    parser.add_argument(
        "--reg-numbers",
        type=int,
        nargs="+",
        help="List of registered charity numbers to scrape."
    )
    parser.add_argument(
        "--search",
        type=str,
        help="Name search term to scrape charities matching the string."
    )
    parser.add_argument(
        "--org-ids",
        type=str,
        nargs="+",
        help="360Giving publisher/organisation IDs to scrape."
    )
    parser.add_argument(
        "--all-orgs",
        action="store_true",
        help="Instruct 360Giving scraper to query all known publishers."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of records scraped."
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Sleep time in seconds between API requests."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP request timeout in seconds."
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip API requests and reload from raw output file."
    )
    parser.add_argument(
        "--skip-contact-crawler",
        action="store_true",
        help="Skip crawling missing contact info (email/address) from websites."
    )
    args = parser.parse_args()
    run_pipeline(args)
