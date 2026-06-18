import os
import sys
import argparse
import logging

# Ensure src directory is in sys.path so imports work regardless of working directory
PIPELINES_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(PIPELINES_DIR)
PROJECT_ROOT = SRC_DIR
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

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
        
    # Step 2: Preprocess
    logging.info("Step 2: Processing content to extract thematic focus tags and geographic locations...")
    try:
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
        choices=["philea", "hinchilla"],
        default="philea",
        help="The source directory to scrape and preprocess (choices: philea, hinchilla)."
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
    
    args = parser.parse_args()
    run_pipeline(args)
