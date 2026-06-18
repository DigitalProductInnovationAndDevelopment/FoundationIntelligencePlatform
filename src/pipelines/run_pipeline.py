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
    logging.info("=========================================")
    logging.info("Starting Philea Data Pipeline Execution")
    logging.info("=========================================")
    
    members = []
    
    # Step 1: Scrape
    if args.skip_scrape:
        logging.info(f"Skipping scraping phase. Loading existing raw data from: {args.raw_output}")
        try:
            members = load_data(args.raw_output)
        except Exception as e:
            logging.error(f"Cannot skip scrape: Raw input file does not exist or is corrupted: {e}")
            sys.exit(1)
    else:
        logging.info("Step 1: Scraping organization details from Philea directory...")
        members = scrape(limit=args.limit, sleep_time=args.sleep, timeout=args.timeout)
        if not members:
            logging.error("Scraping failed or returned no data. Terminating pipeline.")
            sys.exit(1)
            
        save_raw_data(members, args.raw_output)
        logging.info(f"Step 1 Complete. Raw data stored in: {args.raw_output}")
        
    # Step 2: Preprocess
    logging.info("Step 2: Processing content to extract thematic focus tags and geographic locations...")
    try:
        extract_tags(members)
        extract_geo(members)
        save_preprocessed_data(members, args.preprocessed_output)
        logging.info(f"Step 2 Complete. Preprocessed data stored in: {args.preprocessed_output}")
    except Exception as e:
        logging.error(f"Preprocessing stage failed: {e}")
        sys.exit(1)
        
    logging.info("=========================================")
    logging.info("Philea Pipeline Completed Successfully!")
    logging.info("=========================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate Philea Scraper and Preprocessing Pipeline.")
    parser.add_argument(
        "--raw-output",
        type=str,
        default=os.path.join(PROJECT_ROOT, "data/raw/philea_members.json"),
        help="Path where the raw scraped JSON data should be read/written."
    )
    parser.add_argument(
        "--preprocessed-output",
        type=str,
        default=os.path.join(PROJECT_ROOT, "data/preprocessed/philea_members_preprocessed.json"),
        help="Path where the preprocessed final JSON data should be written."
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
