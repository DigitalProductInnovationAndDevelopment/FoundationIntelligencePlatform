"""Collector for 360Giving publisher grant feeds.

Fetches organisations and their published grant transactions, caching raw responses so
that downstream consolidation can be re-run without touching the network. Requests are
retried with backoff and paced by a sleep interval to respect the upstream service.

Coverage is a **sample**, not the complete 360Giving corpus. Absence of a grant here
does not mean absence of funding, and consumers must not present the ingested set as
exhaustive.
"""

import os
import time
import json
import logging
import argparse
import requests
from urllib.parse import urlparse, parse_qs

# Load environment variables from .env file if it exists
def load_env():
    """Load local environment variables for optional live source access."""
    scrapers_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(scrapers_dir)
    workspace_root = os.path.dirname(src_dir)
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
                        os.environ.setdefault(key, val)

load_env()

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

class ThreeSixtyGivingAPI:
    """
    Client for interacting with the public 360Giving API.
    """
    BASE_URL = "https://api.threesixtygiving.org/api/v1"

    def __init__(self, timeout=10.0, max_retries=3, backoff_factor=2, user_agent=None):
        """Create a 360Giving client with bounded timeouts and retries."""
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        
        self.session = requests.Session()
        
        # Set User-Agent to be polite
        ua = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        self.session.headers.update({
            "User-Agent": ua,
            "Accept": "application/json"
        })

    def make_request(self, method, endpoint, **kwargs):
        """
        Make an HTTP request with automatic retries, exponential backoff, and timeout.
        """
        # If the endpoint is already a full URL, use it directly (e.g. from pagination link)
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            url = endpoint
        else:
            url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                # Retry on transient server errors or rate limits (e.g. 429, 5xx)
                if response.status_code in [429, 500, 502, 503, 504]:
                    logging.warning(
                        f"Transient HTTP status {response.status_code} for URL: {url}. "
                        f"Attempt {attempt + 1}/{self.max_retries}"
                    )
                    if attempt < self.max_retries - 1:
                        time.sleep(self.backoff_factor ** attempt)
                        continue
                return response
            except requests.exceptions.RequestException as e:
                logging.warning(
                    f"Request failed for URL: {url} (Error: {e}). "
                    f"Attempt {attempt + 1}/{self.max_retries}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff_factor ** attempt)
                    continue
                raise e
        raise requests.exceptions.RequestException(
            f"Failed to fetch {url} after {self.max_retries} attempts."
        )

    def get_organisations(self, limit=None, offset=None):
        """
        Returns a paginated list of all organisations.
        GET /org/
        """
        params = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        response = self.make_request("GET", "org/", params=params)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()

    def get_organisation_detail(self, org_id):
        """
        Returns details about a single organisation.
        GET /org/<org_id>/
        """
        # Ensure trailing slash is included in endpoint
        endpoint = f"org/{org_id}/"
        response = self.make_request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Organisation Detail for {org_id} not found (HTTP 404).")
            return None
        else:
            response.raise_for_status()

    def get_grants_made(self, org_id, limit=None, offset=None):
        """
        Returns a paginated list of grants made by a funding organisation.
        GET /org/<org_id>/grants_made/
        """
        endpoint = f"org/{org_id}/grants_made/"
        params = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        response = self.make_request("GET", endpoint, params=params)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Grants made by organisation {org_id} not found (HTTP 404).")
            return None
        else:
            response.raise_for_status()

    def get_grants_received(self, org_id, limit=None, offset=None):
        """
        Returns a paginated list of grants received by a recipient organisation.
        GET /org/<org_id>/grants_received/
        """
        endpoint = f"org/{org_id}/grants_received/"
        params = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        response = self.make_request("GET", endpoint, params=params)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Grants received by organisation {org_id} not found (HTTP 404).")
            return None
        else:
            response.raise_for_status()

    def iter_organisations(self, limit=1000, start_offset=0, max_results=None):
        """
        Generator yielding organisations from the API.
        Automatically paginates until no more pages remain or max_results is reached.
        """
        offset = start_offset
        yielded_count = 0

        while True:
            current_limit = limit
            if max_results is not None:
                remaining = max_results - yielded_count
                if remaining <= 0:
                    break
                current_limit = min(limit, remaining)

            logging.info(f"Fetching organisations: limit={current_limit}, offset={offset}")
            try:
                data = self.get_organisations(limit=current_limit, offset=offset)
            except Exception as e:
                logging.error(f"Error fetching organisations: {e}")
                break

            results = data.get("results", [])
            if not results:
                break

            for item in results:
                yield item
                yielded_count += 1
                if max_results is not None and yielded_count >= max_results:
                    return

            next_url = data.get("next")
            if not next_url:
                break

            # Parse next offset/limit from the returned URL (if any)
            parsed_url = urlparse(next_url)
            query_params = parse_qs(parsed_url.query)
            
            # Use offset from the next URL if available, else increment manually
            if "offset" in query_params:
                offset = int(query_params["offset"][0])
            else:
                offset += len(results)

    def iter_grants_made(self, org_id, limit=1000, start_offset=0, max_results=None):
        """
        Generator yielding grants made by a funding organisation.
        Automatically paginates until no more pages remain or max_results is reached.
        """
        offset = start_offset
        yielded_count = 0

        while True:
            current_limit = limit
            if max_results is not None:
                remaining = max_results - yielded_count
                if remaining <= 0:
                    break
                current_limit = min(limit, remaining)

            logging.info(f"Fetching grants made by {org_id}: limit={current_limit}, offset={offset}")
            try:
                data = self.get_grants_made(org_id, limit=current_limit, offset=offset)
            except Exception as e:
                logging.error(f"Error fetching grants made by {org_id}: {e}")
                break

            if not data:
                break

            results = data.get("results", [])
            if not results:
                break

            for item in results:
                yield item
                yielded_count += 1
                if max_results is not None and yielded_count >= max_results:
                    return

            next_url = data.get("next")
            if not next_url:
                break

            parsed_url = urlparse(next_url)
            query_params = parse_qs(parsed_url.query)
            if "offset" in query_params:
                offset = int(query_params["offset"][0])
            else:
                offset += len(results)

    def iter_grants_received(self, org_id, limit=1000, start_offset=0, max_results=None):
        """
        Generator yielding grants received by an organisation.
        Automatically paginates until no more pages remain or max_results is reached.
        """
        offset = start_offset
        yielded_count = 0

        while True:
            current_limit = limit
            if max_results is not None:
                remaining = max_results - yielded_count
                if remaining <= 0:
                    break
                current_limit = min(limit, remaining)

            logging.info(f"Fetching grants received by {org_id}: limit={current_limit}, offset={offset}")
            try:
                data = self.get_grants_received(org_id, limit=current_limit, offset=offset)
            except Exception as e:
                logging.error(f"Error fetching grants received by {org_id}: {e}")
                break

            if not data:
                break

            results = data.get("results", [])
            if not results:
                break

            for item in results:
                yield item
                yielded_count += 1
                if max_results is not None and yielded_count >= max_results:
                    return

            next_url = data.get("next")
            if not next_url:
                break

            parsed_url = urlparse(next_url)
            query_params = parse_qs(parsed_url.query)
            if "offset" in query_params:
                offset = int(query_params["offset"][0])
            else:
                offset += len(results)


# Module-level convenience functions
def get_organisations(limit=None, offset=None):
    """Fetch publisher organisations from the 360Giving API."""
    return ThreeSixtyGivingAPI().get_organisations(limit=limit, offset=offset)

def get_organisation_detail(org_id):
    """Fetch one organisation's detail from the 360Giving API."""
    return ThreeSixtyGivingAPI().get_organisation_detail(org_id)

def get_grants_made(org_id, limit=None, offset=None):
    """Fetch grants made by one organisation."""
    return ThreeSixtyGivingAPI().get_grants_made(org_id, limit=limit, offset=offset)

def get_grants_received(org_id, limit=None, offset=None):
    """Fetch grants received by one organisation."""
    return ThreeSixtyGivingAPI().get_grants_received(org_id, limit=limit, offset=offset)


def scrape(org_ids=None, all_organisations=False, scrape_grants=False, limit=None, sleep_time=1.0, timeout=10.0, completed_org_ids=None):
    """
    Scrapes data from the 360Giving API.
    Can either yield/fetch a bulk list of organisations or details for specific org IDs.
    """
    client = ThreeSixtyGivingAPI(timeout=timeout)
    results = []
    completed_org_ids = completed_org_ids or set()

    if org_ids:
        # Filter out completed ones
        org_ids = [o for o in org_ids if o not in completed_org_ids]
        # Fetch details for specific list of orgs
        logging.info(f"Fetching details for {len(org_ids)} specified organisation IDs.")
        for idx, org_id in enumerate(org_ids):
            if limit is not None and idx >= limit:
                break
            logging.info(f"[{idx+1}/{len(org_ids)}] Fetching details for org: {org_id}")
            org_data = {
                "org_id": org_id,
                "detail": None,
                "grants_made": [],
                "grants_received": []
            }
            try:
                org_data["detail"] = client.get_organisation_detail(org_id)
                time.sleep(sleep_time)
                
                if scrape_grants:
                    # Fetch grants made
                    org_data["grants_made"] = list(client.iter_grants_made(org_id, limit=100))
                    time.sleep(sleep_time)

                    # Fetch grants received
                    org_data["grants_received"] = list(client.iter_grants_received(org_id, limit=100))
                    time.sleep(sleep_time)

            except Exception as e:
                logging.error(f"Error scraping details for org {org_id}: {e}")
                org_data["error"] = str(e)
            
            results.append(org_data)

    elif all_organisations:
        logging.info("Listing all organisations...")
        try:
            # When scraping all orgs in bulk, fetch their basic info (up to 200 candidates to bypass completed cache)
            orgs_list = list(client.iter_organisations(limit=100, max_results=200))
            # Filter out completed ones
            orgs_list = [o for o in orgs_list if o.get("org_id") not in completed_org_ids]
            if limit:
                orgs_list = orgs_list[:limit]
            
            # If scrape_grants is set, fetch full details + grants for each discovered organisation
            if scrape_grants:
                logging.info(f"Scraping detailed data and grants for {len(orgs_list)} discovered organisations...")
                for idx, org_summary in enumerate(orgs_list):
                    org_id = org_summary.get("org_id")
                    if not org_id:
                        continue
                    logging.info(f"[{idx+1}/{len(orgs_list)}] Fetching details/grants for discovered org: {org_id}")
                    org_data = {
                        "org_id": org_id,
                        "summary": org_summary,
                        "detail": None,
                        "grants_made": [],
                        "grants_received": []
                    }
                    try:
                        org_data["detail"] = client.get_organisation_detail(org_id)
                        time.sleep(sleep_time)
                        
                        org_data["grants_made"] = list(client.iter_grants_made(org_id, limit=100))
                        time.sleep(sleep_time)

                        org_data["grants_received"] = list(client.iter_grants_received(org_id, limit=100))
                        time.sleep(sleep_time)
                    except Exception as e:
                        logging.error(f"Error scraping details for discovered org {org_id}: {e}")
                        org_data["error"] = str(e)
                    results.append(org_data)
            else:
                results = orgs_list
        except Exception as e:
            logging.error(f"Failed to scrape bulk organisations list: {e}")

    else:
        logging.error("Either --org-ids or --all-orgs must be specified to run.")
    
    return results


def save_data(data, path):
    """Write a fetched payload to the raw source cache."""
    logging.info(f"Saving scraped data to {path}...")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logging.info("Data saved successfully.")
    except Exception as e:
        logging.error(f"Failed to save data to {path}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape 360Giving API.")
    parser.add_argument(
        "--org-ids",
        type=str,
        nargs="+",
        help="List of organisation IDs to scrape (e.g. GB-CHC-1164883)"
    )
    parser.add_argument(
        "--all-orgs",
        action="store_true",
        help="Retrieve all known organisations (paginated list)"
    )
    parser.add_argument(
        "--scrape-grants",
        action="store_true",
        help="Fetch detail + grants made & received for each target organisation"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "../data/raw/threesixtygiving_results.json"),
        help="Path where the output JSON file should be saved."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of records to retrieve."
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
    args = parser.parse_args()

    if not args.org_ids and not args.all_orgs:
        parser.error("At least one of --org-ids or --all-orgs must be specified.")

    scraped_results = scrape(
        org_ids=args.org_ids,
        all_organisations=args.all_orgs,
        scrape_grants=args.scrape_grants,
        limit=args.limit,
        sleep_time=args.sleep,
        timeout=args.timeout
    )

    if scraped_results:
        save_data(scraped_results, args.output)
    else:
        logging.error("No data retrieved. File was not saved.")
