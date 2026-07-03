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

# Load environment variables from .env file if it exists
def load_env():
    workspace_root = os.path.dirname(PROJECT_ROOT)
    env_path = os.path.join(workspace_root, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key:
                        os.environ[key] = val

load_env()

from scrapers.philea import scrape, save_data as save_raw_data
from preprocessing.extract_geo_topic import extract_tags, extract_geo, save_data as save_preprocessed_data, load_data

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

def run_pipeline(args):
    source = args.source.lower()
    logging.info("=========================================")
    logging.info(f"Starting {source.title()} Data Pipeline Execution")
    logging.info("=========================================")
    
    if source == "consolidate":
        philea_input = args.philea_input or os.path.join(PROJECT_ROOT, "data/preprocessed/philea_members_preprocessed.json")
        hinchilla_input = args.hinchilla_input or os.path.join(PROJECT_ROOT, "data/preprocessed/hinchilla_members_preprocessed.json")
        consolidated_output = args.preprocessed_output or os.path.join(PROJECT_ROOT, "data/preprocessed/consolidated_members_preprocessed.json")

        logging.info(f"Loading Philea preprocessed data from: {philea_input}")
        logging.info(f"Loading Hinchilla preprocessed data from: {hinchilla_input}")

        if not os.path.exists(philea_input):
            logging.error(f"Philea preprocessed input file not found: {philea_input}")
            sys.exit(1)
        if not os.path.exists(hinchilla_input):
            logging.error(f"Hinchilla preprocessed input file not found: {hinchilla_input}")
            sys.exit(1)

        try:
            with open(philea_input, "r", encoding="utf-8") as f:
                philea_data = json.load(f)
            with open(hinchilla_input, "r", encoding="utf-8") as f:
                hinchilla_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load input files: {e}")
            sys.exit(1)

        from preprocessing.consolidate import consolidate_datasets
        try:
            consolidated = consolidate_datasets(philea_data, hinchilla_data)
            
            # Optional Gemini Enrichment on the consolidated dataset
            if args.enrich:
                logging.info("Enriching consolidated organization data using Gemini and Google Search...")
                from preprocessing.enrich_gemini import enrich_organizations
                
                def save_consolidated(m_list, path):
                    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(m_list, f, ensure_ascii=False, indent=4)
                
                consolidated = enrich_organizations(
                    consolidated,
                    save_path=consolidated_output,
                    save_fn=save_consolidated,
                    sleep_time=args.sleep
                )
                

            
            # Save output
            os.makedirs(os.path.dirname(os.path.abspath(consolidated_output)), exist_ok=True)
            with open(consolidated_output, "w", encoding="utf-8") as f:
                json.dump(consolidated, f, ensure_ascii=False, indent=4)
            logging.info(f"Consolidated data successfully saved to: {consolidated_output}")
        except Exception as e:
            logging.error(f"Consolidation process failed: {e}")
            sys.exit(1)
            
        logging.info("=========================================")
        logging.info("Consolidation Pipeline Completed Successfully!")
        logging.info("=========================================")
        return

    # Dynamically import scraper for selected source
    if source == "philea":
        from scrapers.philea import scrape, save_data as save_raw_data
        load_existing_data = lambda path: []
    elif source == "hinchilla":
        from scrapers.hinchilla import scrape, save_data as save_raw_data, load_existing_data
    else:
        logging.error(f"Unknown source: {source}")
        sys.exit(1)

    # Dynamic defaults for paths if not provided
    raw_output = args.raw_output
    if not raw_output:
        raw_output = os.path.join(PROJECT_ROOT, f"data/raw/{source}_members.json")
        
    preprocessed_output = args.preprocessed_output
    if not preprocessed_output:
        preprocessed_output = os.path.join(PROJECT_ROOT, f"data/preprocessed/{source}_members_preprocessed.json")

    members = []
    
    # Step 1: Scrape
    if args.skip_scrape:
        logging.info(f"Skipping scraping phase. Loading existing raw data from: {raw_output}")
        try:
            members = load_data(raw_output)
        except Exception as e:
            logging.error(f"Cannot skip scrape: Raw input file does not exist or is corrupted: {e}")
            sys.exit(1)
    else:
        logging.info(f"Step 1: Scraping organization details from {source.title()} directory...")
        
        # Load existing data to support resuming
        existing_members = load_existing_data(raw_output)
        completed_slugs = set()
        for m in existing_members:
            link = m.get("link", "")
            slug = link.split("/")[-1].strip()
            if slug and "Error" not in m.get("philea_info", {}):
                completed_slugs.add(slug)
                
        if completed_slugs:
            logging.info(f"Resuming: skipping {len(completed_slugs)} already successfully scraped members.")
            
        try:
            new_members = scrape(
                limit=args.limit, 
                sleep_time=args.sleep, 
                timeout=args.timeout, 
                completed_slugs=completed_slugs
            )
        except TypeError:
            # Fallback if scraper doesn't support completed_slugs
            new_members = scrape(
                limit=args.limit, 
                sleep_time=args.sleep, 
                timeout=args.timeout
            )
            
        if not new_members and not completed_slugs:
            logging.error("Scraping failed or returned no data. Terminating pipeline.")
            sys.exit(1)
            
        # Merge new members with existing ones, overwriting on overlap
        merged_dict = {m["link"]: m for m in existing_members}
        for m in new_members:
            merged_dict[m["link"]] = m
            
        members = list(merged_dict.values())
        save_raw_data(members, raw_output)
        logging.info(f"Step 1 Complete. Raw data stored in: {raw_output}")
        
    # Step 1.5: Optional Gemini Enrichment
    if args.enrich:
        logging.info("Step 1.5: Enriching organization data using Gemini and Google Search...")
        try:
            from preprocessing.enrich_gemini import enrich_organizations
            members = enrich_organizations(
                members,
                save_path=args.raw_output,
                save_fn=save_raw_data,
                sleep_time=args.sleep
            )
            save_raw_data(members, args.raw_output)
            logging.info(f"Step 1.5 Complete. Enriched data stored in: {args.raw_output}")
        except Exception as e:
            logging.error(f"Enrichment stage failed: {e}")
            sys.exit(1)
            
    # Step 2: Preprocess
    logging.info("Step 2: Processing content to extract thematic focus tags and geographic locations...")
    try:
        if source == "philea" and not args.skip_contact_crawler:
            logging.info("Step 2.1: Checking for missing addresses and emails to enrich...")
            from preprocessing.extract_impressum import crawl_impressum
            for i, member in enumerate(members, 1):
                email_missing = not member.get("email") or str(member.get("email")).strip() == ""
                address_missing = not member.get("address") or str(member.get("address")).strip() == ""
                
                if (email_missing or address_missing) and member.get("website"):
                    logging.info(f"[{i}/{len(members)}] Crawling missing contact info for: {member['name']} ({member['website']})")
                    try:
                        impressum = crawl_impressum(member["website"], timeout=8)
                        if impressum:
                            if email_missing and impressum.get("generic_email"):
                                member["email"] = impressum["generic_email"]
                                logging.info(f"  -> Found email: {impressum['generic_email']}")
                            if address_missing and impressum.get("address"):
                                member["address"] = impressum["address"]
                                logging.info(f"  -> Found address: {impressum['address']}")
                    except Exception as e:
                        logging.warning(f"  -> Failed to crawl {member['name']}: {e}")
                        
        extract_tags(members)
        extract_geo(members)
        save_preprocessed_data(members, preprocessed_output)
        logging.info(f"Step 2 Complete. Preprocessed data stored in: {preprocessed_output}")
    except Exception as e:
        logging.error(f"Preprocessing stage failed: {e}")
        sys.exit(1)
        
    logging.info("=========================================")
    logging.info(f"{source.title()} Pipeline Completed Successfully!")
    logging.info("=========================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate Scraper and Preprocessing Pipeline.")
    parser.add_argument(
        "--source",
        type=str,
        choices=["philea", "hinchilla", "consolidate"],
        default="philea",
        help="The source directory to scrape and preprocess (choices: philea, hinchilla, consolidate)."
    )
    parser.add_argument(
        "--philea-input",
        type=str,
        default=None,
        help="Path to preprocessed Philea data (used for consolidation)."
    )
    parser.add_argument(
        "--hinchilla-input",
        type=str,
        default=None,
        help="Path to preprocessed Hinchilla data (used for consolidation)."
    )
    parser.add_argument(
        "--raw-output",
        type=str,
        default=None,
        help="Path where the raw scraped JSON data should be read/written (defaults based on source)."
    )
    parser.add_argument(
        "--preprocessed-output",
        type=str,
        default=None,
        help="Path where the preprocessed final JSON data should be written (defaults based on source)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of organizations to scrape (useful for testing)."
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
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip the web scraping phase and run preprocessing on the existing raw data file."
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Enrich scraped organization data using Gemini and Google Search grounding."
    )
    parser.add_argument(
        "--skip-contact-crawler",
        action="store_true",
        help="Skip crawling missing contact info (email/address) from websites."
    )
    
    args = parser.parse_args()
    run_pipeline(args)
