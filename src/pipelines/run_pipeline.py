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

from scrapers.register_of_charities import scrape as scrape_cc, save_data as save_raw_cc, search_charity_name
import importlib
import re
giving = importlib.import_module("scrapers.360giving")
scrape_ts = giving.scrape
save_raw_ts = giving.save_data

def parse_charity_number(org_id):
    if not org_id or not isinstance(org_id, str):
        return None
    match = re.search(r'GB-CHC-(\d+)', org_id)
    if match:
        num = int(match.group(1))
        if 100000 <= num <= 9999999:
            return num
    match = re.search(r'\b(\d+)\b', org_id)
    if match:
        num = int(match.group(1))
        if 100000 <= num <= 9999999:
            return num
    return None


from preprocessing.consolidate import consolidate_uk_datasets
from preprocessing.enrichment import build_enrichment_report
from preprocessing.extract_impressum import crawl_impressum
import data.db_loader as db_loader
from data.db_loader import insert_charities, insert_grants
import sqlite3

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
        existing = [] if args.fresh else load_existing_raw(raw_cc_path)
        completed_numbers = set() if args.fresh else {int(x["registered_charity_number"]) for x in existing if "registered_charity_number" in x}
        
        # Seed charity numbers if none are provided
        reg_numbers = args.reg_numbers
        search_term = args.search
        
        target_tuples = []
        
        if reg_numbers:
            # User provided specific registered numbers
            for item in reg_numbers:
                if isinstance(item, tuple):
                    target_tuples.append((int(item[0]), int(item[1])))
                else:
                    target_tuples.append((int(item), 0))
                    
        elif not search_term:
            # Dynamic seeding from 360Giving with fallback name search
            logger.info("No specific reg-numbers or search provided. Seeding from 360Giving...")
            
            # 1. Load existing or auto-generate 360Giving results
            ts_records = []
            if os.path.exists(raw_ts_path):
                ts_records = load_existing_raw(raw_ts_path)
            
            if not ts_records:
                logger.info(f"360Giving data at {raw_ts_path} is missing or empty. Auto-generating organisation list...")
                try:
                    ts_records = scrape_ts(
                        all_organisations=True,
                        scrape_grants=False,
                        limit=None,
                        sleep_time=args.sleep,
                        timeout=args.timeout
                    )
                    if ts_records:
                        save_raw_ts(ts_records, raw_ts_path)
                        logger.info(f"Successfully auto-generated and saved 360Giving list to {raw_ts_path}")
                except Exception as e:
                    logger.error(f"Failed to auto-generate 360Giving list: {e}")
            
            # 2. Extract charity numbers from 360Giving records
            ts_numbers = []
            for record in ts_records:
                org_id = record.get("org_id") or (record.get("summary", {}).get("org_id") if isinstance(record.get("summary"), dict) else None)
                if org_id:
                    num = parse_charity_number(org_id)
                    if num is not None:
                        ts_numbers.append(num)
            
            # Deduplicate while preserving order
            seen_nums = set()
            deduped_ts_numbers = []
            for num in ts_numbers:
                if num not in seen_nums:
                    seen_nums.add(num)
                    deduped_ts_numbers.append(num)
            
            # Filter against already completed numbers
            new_ts_numbers = [num for num in deduped_ts_numbers if num not in completed_numbers]
            logger.info(f"Found {len(deduped_ts_numbers)} unique charity numbers in 360Giving data ({len(new_ts_numbers)} are new/unscraped).")
            
            # Add to target_tuples up to the limit
            if args.limit is not None:
                seeded_nums = new_ts_numbers[:args.limit]
                remaining_limit = args.limit - len(seeded_nums)
            else:
                seeded_nums = new_ts_numbers
                remaining_limit = None
                
            for num in seeded_nums:
                target_tuples.append((num, 0))
                
            # 3. If limit allows or no limit set, query the fallback name search
            if remaining_limit is None or remaining_limit > 0:
                fallback_term = "foundation"
                logger.info(f"Running fallback search for term '{fallback_term}' to find additional candidates...")
                try:
                    search_results = search_charity_name(fallback_term)
                    if search_results:
                        discovered_tuples = []
                        for item in search_results:
                            reg_no = item.get("reg_charity_number") or item.get("registeredCharityNumber") or item.get("charityNumber") or item.get("regno")
                            suffix = item.get("group_subsid_suffix") if item.get("group_subsid_suffix") is not None else (item.get("suffix") or 0)
                            if reg_no:
                                discovered_tuples.append((int(reg_no), int(suffix)))
                                
                        # Filter discovered numbers to avoid duplicates with seeded numbers or completed numbers
                        new_discovered = []
                        for t in discovered_tuples:
                            if t[0] not in completed_numbers and t[0] not in seen_nums:
                                if t not in new_discovered:
                                    new_discovered.append(t)
                                    
                        logger.info(f"Fallback search discovered {len(discovered_tuples)} charities ({len(new_discovered)} are new/unscraped/non-seeded).")
                        
                        if remaining_limit is not None:
                            target_tuples.extend(new_discovered[:remaining_limit])
                        else:
                            target_tuples.extend(new_discovered)
                except Exception as e:
                    logger.error(f"Failed fallback search: {e}")
                    
        else:
            # User provided specific search term (but not reg-numbers)
            logger.info(f"Searching for charities with name matching: '{search_term}'")
            try:
                search_results = search_charity_name(search_term)
                if search_results:
                    for item in search_results:
                        reg_no = item.get("reg_charity_number") or item.get("registeredCharityNumber") or item.get("charityNumber") or item.get("regno")
                        suffix = item.get("group_subsid_suffix") if item.get("group_subsid_suffix") is not None else (item.get("suffix") or 0)
                        if reg_no:
                            target_tuples.append((int(reg_no), int(suffix)))
            except Exception as e:
                logger.error(f"Failed to search for charity name '{search_term}': {e}")

        if args.skip_scrape:
            logger.info(f"Skipping scraping phase. Loading existing raw data from: {raw_cc_path}")
            cc_records = existing
        else:
            logger.info("Step 1: Scraping Charity Commission API...")
            new_records = scrape_cc(
                registered_numbers=target_tuples,
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
        existing = [] if args.fresh else load_existing_raw(raw_ts_path)
        completed_org_ids = set() if args.fresh else {x["org_id"] for x in existing if "org_id" in x}
        
        org_ids = args.org_ids
        all_orgs = args.all_orgs
        if not org_ids and not all_orgs:
            all_orgs = True
            logger.info("No specific org IDs or all-orgs flag provided. Defaulting to all-orgs dynamic publisher list to discover candidates.")

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

        enrichment_report = build_enrichment_report(charities_list, grants_list)
        enrichment_report_path = os.path.join(PROJECT_ROOT, "data/preprocessed/enrichment_report.json")
        with open(enrichment_report_path, "w", encoding="utf-8") as f:
            json.dump(enrichment_report, f, ensure_ascii=False, indent=2)
        logger.info(f"Exported enrichment coverage report to: {enrichment_report_path}")

        # Step 4: Loading SQLite DB
        logger.info("Step 4: Loading data into SQLite Database...")
        db_loader.main(db_path=db_file, preprocessed_dir=os.path.dirname(charities_jsonl_path))
        logger.info(f"SQLite database successfully loaded at: {db_file}")

    elif source == "full_run":
        # 1. Work against a staging database so a failed run cannot damage the active DB.
        active_db_valid, validation_reason = db_loader.validate_database(db_file)
        if os.path.exists(db_file) and not active_db_valid:
            logger.warning(f"Existing database will not be reused: {validation_reason}")
        staging_db_file, conn = db_loader.create_staging_database(
            db_file,
            preserve_existing=active_db_valid and not args.fresh,
        )
        completed_numbers = set()
        
        if args.fresh:
            logger.info("Fresh flag set. Using a clean, fully initialized staging database...")
        else:
            # The staging helper always creates the schema, including on a first run.
            if conn:
                try:
                    if active_db_valid:
                        cursor = conn.cursor()
                        cursor.execute("SELECT charity_id FROM charities;")
                        completed_numbers = {int(row[0]) for row in cursor.fetchall()}
                        logger.info(f"Loaded {len(completed_numbers)} completed charity numbers from SQLite database cache.")
                except Exception as e:
                    logger.warning(f"Could not read completed numbers from database: {e}")

        # 2. Build target list of (reg_no, suffix) to scrape
        target_tuples = []
        reg_numbers = args.reg_numbers
        search_term = args.search
        
        if reg_numbers:
            # User provided specific registered numbers
            for item in reg_numbers:
                if isinstance(item, tuple):
                    target_tuples.append((int(item[0]), int(item[1])))
                else:
                    target_tuples.append((int(item), 0))
                    
        else:
            # No specific reg-numbers, so use search (fallback to "foundation" if search_term is empty)
            term_to_search = search_term or "foundation"
            if not search_term:
                logger.info(f"No specific reg-numbers or search provided. Seeding from Charity Commission for term '{term_to_search}'...")
            else:
                logger.info(f"Searching for charities with name matching: '{term_to_search}'")
                
            try:
                search_results = search_charity_name(term_to_search)
                if search_results:
                    discovered_tuples = []
                    for item in search_results:
                        reg_no = item.get("reg_charity_number") or item.get("registeredCharityNumber") or item.get("charityNumber") or item.get("regno")
                        suffix = item.get("group_subsid_suffix") if item.get("group_subsid_suffix") is not None else (item.get("suffix") or 0)
                        if reg_no:
                            discovered_tuples.append((int(reg_no), int(suffix)))
                            
                    seen_nums = set()
                    deduped_tuples = []
                    for t in discovered_tuples:
                        if t[0] not in seen_nums:
                            seen_nums.add(t[0])
                            deduped_tuples.append(t)
                            
                    new_discovered = [t for t in deduped_tuples if t[0] not in completed_numbers]
                    logger.info(f"Search discovered {len(discovered_tuples)} charities ({len(new_discovered)} are new/unscraped).")
                    
                    if args.limit is not None:
                        target_tuples = new_discovered[:args.limit]
                    else:
                        target_tuples = new_discovered
            except Exception as e:
                logger.error(f"Failed search for term '{term_to_search}': {e}")

        # 3. Process candidates foundation-by-foundation sequentially
        if not target_tuples:
            logger.info("No foundations to process.")
            if conn:
                conn.close()
            db_loader.publish_staging_database(staging_db_file, db_file)
            return
            
        logger.info(f"Found {len(target_tuples)} target foundations to process.")
        
        # Load raw files once to merge with
        raw_cc_existing = load_existing_raw(raw_cc_path)
        raw_ts_existing = load_existing_raw(raw_ts_path)
        
        cc_raw_map = {int(x["registered_charity_number"]): x for x in raw_cc_existing if "registered_charity_number" in x}
        ts_raw_map = {x["org_id"]: x for x in raw_ts_existing if "org_id" in x}
        
        for idx, (reg_no, suffix) in enumerate(target_tuples, 1):
            if reg_no in completed_numbers and not args.fresh:
                logger.info(f"[{idx}/{len(target_tuples)}] Skipping Charity {reg_no} (already processed in database).")
                continue
                
            logger.info("--------------------------------------------------")
            logger.info(f"[{idx}/{len(target_tuples)}] Processing Charity {reg_no} (Suffix: {suffix})")
            logger.info("--------------------------------------------------")
            
            # Step A: Scrape Charity Commission
            logger.info(f"[{idx}/{len(target_tuples)}] Step A: Fetching Charity Commission details...")
            cc_records = scrape_cc(
                registered_numbers=[(reg_no, suffix)],
                sleep_time=args.sleep,
                timeout=args.timeout
            )
            
            # Step B: Scrape 360Giving details/grants
            logger.info(f"[{idx}/{len(target_tuples)}] Step B: Fetching 360Giving grants...")
            ts_records = scrape_ts(
                org_ids=[f"GB-CHC-{reg_no}"],
                scrape_grants=True,
                sleep_time=args.sleep,
                timeout=args.timeout
            )
            
            # If no data at all was scraped, skip to next
            if not cc_records and not ts_records:
                logger.warning(f"[{idx}/{len(target_tuples)}] Scrapers returned no data for Charity {reg_no}. Skipping.")
                continue
                
            # Step C: Consolidate
            logger.info(f"[{idx}/{len(target_tuples)}] Step C: Consolidating and mapping datasets...")
            charities_list, grants_list = consolidate_uk_datasets(cc_records, ts_records)
            
            if not charities_list:
                logger.warning(f"[{idx}/{len(target_tuples)}] Consolidation produced no charity profiles for {reg_no}. Skipping.")
                continue
                
            # Step D: Impressum Crawler
            if not args.skip_contact_crawler:
                logger.info(f"[{idx}/{len(target_tuples)}] Step D: Checking website Impressum...")
                for c in charities_list:
                    email_missing = not c.get("email") or str(c.get("email")).strip() == ""
                    address_missing = not c.get("address") or str(c.get("address")).strip() == ""
                    
                    if (email_missing or address_missing) and c.get("website"):
                        website = c["website"]
                        if not website.startswith(("http://", "https://")):
                            website = "https://" + website
                        logger.info(f"Crawling missing contact info for {c['name']} ({website})")
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

            # Step E: Insert into SQLite immediately
            if conn:
                logger.info(f"[{idx}/{len(target_tuples)}] Step E: Writing records incrementally to SQLite database...")
                try:
                    insert_charities(conn, charities_list)
                    insert_grants(conn, grants_list)
                except Exception as e:
                    logger.error(f"Failed to write to database for charity {reg_no}: {e}")
                    conn.close()
                    if os.path.exists(staging_db_file):
                        os.unlink(staging_db_file)
                    raise RuntimeError(f"Database insertion failed for charity {reg_no}") from e
                    
            # Step F: Update raw cache files in background to preserve sync
            for rec in cc_records:
                if "registered_charity_number" in rec:
                    cc_raw_map[int(rec["registered_charity_number"])] = rec
            for rec in ts_records:
                if "org_id" in rec:
                    ts_raw_map[rec["org_id"]] = rec
                    
            save_raw_cc(list(cc_raw_map.values()), raw_cc_path)
            save_raw_ts(list(ts_raw_map.values()), raw_ts_path)
            
            logger.info(f"[{idx}/{len(target_tuples)}] Foundation {reg_no} successfully integrated.")
            
        if conn:
            conn.close()
            logger.info("Database connection closed.")
        db_loader.publish_staging_database(staging_db_file, db_file)

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
        choices=["360giving", "register_of_charities", "consolidate", "full_run"],
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
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Run scraping from scratch, clearing the completed cache."
    )
    args = parser.parse_args()
    run_pipeline(args)
