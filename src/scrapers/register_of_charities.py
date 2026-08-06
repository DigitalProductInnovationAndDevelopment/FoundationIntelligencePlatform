import os
import time
import json
import logging
import argparse
import requests
from urllib.parse import quote

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

class CharityCommissionAPI:
    """
    Client for interacting with the official Charity Commission for England and Wales API.
    """
    BASE_URL = "https://api.charitycommission.gov.uk/register/api"

    def __init__(self, api_key=None, timeout=10.0, max_retries=3, backoff_factor=2):
        # Read API key from parameter or environment variables
        """Create a Charity Commission client using CHARITY_COMMISSION_API_KEY."""
        self.api_key = api_key if api_key is not None else os.environ.get("CHARITY_COMMISSION_API_KEY")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        
        self.session = requests.Session()
        self.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "application/json"
            })
        if self.api_key:
            self.session.headers.update({
                "Ocp-Apim-Subscription-Key": self.api_key,
                "Cache-Control": "no-cache"
            })
        else:
            logging.warning("No API key provided. Requests to the Charity Commission API may fail with HTTP 401 Unauthorized.")

    def make_request(self, method, endpoint, **kwargs):
        """
        Make an HTTP request with automatic retries, exponential backoff, and timeout.
        """
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        
        # Ensure timeout is set
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

    def all_charity_details(self, registered_number, suffix=0):
        """
        Retrieves comprehensive details about a charity (V2 endpoint).
        URL: allcharitydetailsV2/{RegisteredNumber}/{suffix}
        """
        endpoint = f"allcharitydetailsV2/{registered_number}/{suffix}"
        response = self.make_request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Charity {registered_number} (suffix {suffix}) not found.")
            return None
        else:
            response.raise_for_status()

    def charity_assets_liabilities(self, registered_number, suffix=0):
        """
        Retrieves asset and liability information for a charity.
        URL: charityassetsliabilities/{RegisteredNumber}/{suffix}
        """
        endpoint = f"charityassetsliabilities/{registered_number}/{suffix}"
        response = self.make_request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Charity assets/liabilities not found for {registered_number} (suffix {suffix}).")
            return None
        else:
            response.raise_for_status()

    def check_primary_grants(self, registered_number, suffix=0):
        """
        Checks primary grants information for a charity.
        URL: checkprimarygrants/{RegisteredNumber}/{suffix}
        """
        endpoint = f"checkprimarygrants/{registered_number}/{suffix}"
        response = self.make_request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Primary grants not found for {registered_number} (suffix {suffix}).")
            return None
        else:
            response.raise_for_status()

    def charity_who_what_how(self, registered_number, suffix=0):
        """
        Retrieves who, what, and how information for a charity.
        URL: charitywhowhathow/{RegisteredNumber}/{suffix}
        """
        endpoint = f"charitywhowhathow/{registered_number}/{suffix}"
        response = self.make_request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Who/What/How info not found for {registered_number} (suffix {suffix}).")
            return None
        else:
            response.raise_for_status()

    def charity_financial_history(self, registered_number, suffix=0):
        """
        Retrieves financial history for a charity.
        URL: charityfinancialhistory/{RegisteredNumber}/{suffix}
        """
        endpoint = f"charityfinancialhistory/{registered_number}/{suffix}"
        response = self.make_request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logging.warning(f"Financial history not found for {registered_number} (suffix {suffix}).")
            return None
        else:
            response.raise_for_status()

    def search_charity_name(self, charity_name):
        """
        Searches for a charity by name.
        URL: searchCharityName/{charityname}
        """
        endpoint = f"searchCharityName/{quote(str(charity_name), safe='')}"
        response = self.make_request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()


# Module-level convenience functions
def get_all_charity_details(registered_number, suffix=0, api_key=None):
    """Fetch one charity's full registration detail."""
    client = CharityCommissionAPI(api_key=api_key)
    return client.all_charity_details(registered_number, suffix)

def get_charity_assets_liabilities(registered_number, suffix=0, api_key=None):
    """Fetch one charity's reported assets and liabilities."""
    client = CharityCommissionAPI(api_key=api_key)
    return client.charity_assets_liabilities(registered_number, suffix)

def get_check_primary_grants(registered_number, suffix=0, api_key=None):
    """Fetch the charity's primary grant-making indicators."""
    client = CharityCommissionAPI(api_key=api_key)
    return client.check_primary_grants(registered_number, suffix)

def get_charity_who_what_how(registered_number, suffix=0, api_key=None):
    """Fetch the charity's declared purposes, beneficiaries and activities."""
    client = CharityCommissionAPI(api_key=api_key)
    return client.charity_who_what_how(registered_number, suffix)

def get_charity_financial_history(registered_number, suffix=0, api_key=None):
    """Fetch the charity's reported financial history."""
    client = CharityCommissionAPI(api_key=api_key)
    return client.charity_financial_history(registered_number, suffix)

def search_charity_name(charity_name, api_key=None):
    """Search the register by charity name."""
    client = CharityCommissionAPI(api_key=api_key)
    return client.search_charity_name(charity_name)


def scrape(registered_numbers=None, search_name=None, limit=None, sleep_time=1.0, timeout=10.0, api_key=None, completed_numbers=None):
    """
    Scrapes data for one or more charities.
    Can either search for a charity name or fetch details for specific registered numbers.
    """
    client = CharityCommissionAPI(api_key=api_key, timeout=timeout)
    results = []
    completed_numbers = completed_numbers or set()
    
    if search_name:
        logging.info(f"Searching for charities with name matching: '{search_name}'")
        try:
            search_results = client.search_charity_name(search_name)
            if not search_results:
                logging.info("No matching charities found.")
                return []
            
            reg_numbers = []
            for item in search_results:
                reg_no = item.get("reg_charity_number") or item.get("registeredCharityNumber") or item.get("charityNumber") or item.get("regno")
                suffix = item.get("group_subsid_suffix") if item.get("group_subsid_suffix") is not None else (item.get("suffix") or 0)
                if reg_no:
                    reg_numbers.append((int(reg_no), int(suffix)))
        except Exception as e:
            logging.error(f"Failed to search for charity name '{search_name}': {e}")
            return []
    elif registered_numbers:
        reg_numbers = []
        for item in registered_numbers:
            if isinstance(item, tuple):
                reg_numbers.append((int(item[0]), int(item[1])))
            elif isinstance(item, (int, str)):
                reg_numbers.append((int(item), 0))
    else:
        logging.error("Either registered_numbers or search_name must be provided to scrape.")
        return []

    # Filter out completed numbers
    reg_numbers = [(r, s) for r, s in reg_numbers if r not in completed_numbers]

    if limit:
        reg_numbers = reg_numbers[:limit]

    total = len(reg_numbers)
    logging.info(f"Starting scraping details for {total} charity records...")

    for i, (reg_no, suffix) in enumerate(reg_numbers):
        logging.info(f"[{i+1}/{total}] Fetching details for Charity Registration: {reg_no}, Suffix: {suffix}")
        charity_data = {
            "registered_charity_number": reg_no,
            "suffix": suffix,
            "link": f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regid={reg_no}&subid={suffix}"
        }
        
        try:
            charity_data["all_details"] = client.all_charity_details(reg_no, suffix)
            time.sleep(sleep_time)
            
            charity_data["assets_liabilities"] = client.charity_assets_liabilities(reg_no, suffix)
            time.sleep(sleep_time)
            
            charity_data["primary_grants"] = client.check_primary_grants(reg_no, suffix)
            time.sleep(sleep_time)
            
            charity_data["who_what_how"] = client.charity_who_what_how(reg_no, suffix)
            time.sleep(sleep_time)
            
            charity_data["financial_history"] = client.charity_financial_history(reg_no, suffix)
            time.sleep(sleep_time)
            
        except Exception as e:
            logging.error(f"Error scraping details for charity {reg_no} (suffix {suffix}): {e}")
            charity_data["error"] = str(e)
            
        results.append(charity_data)
        
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
    parser = argparse.ArgumentParser(description="Scrape Charity Commission API.")
    parser.add_argument(
        "--reg-numbers",
        type=str,
        nargs="+",
        help="List of registered charity numbers to scrape (e.g. 219907 283322)"
    )
    parser.add_argument(
        "--search",
        type=str,
        help="Name search term to find and scrape charities"
    )
    parser.add_argument(
        "--suffix",
        type=int,
        default=0,
        help="Suffix for the registered numbers (defaults to 0)"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="Charity Commission API subscription key (defaults to environment variable)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "../data/raw/register_of_charities_results.json"),
        help="Path where the output JSON file should be saved."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of charities to scrape."
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

    # Determine registered numbers format
    registered_numbers = None
    if args.reg_numbers:
        registered_numbers = [(int(num), args.suffix) for num in args.reg_numbers]

    if not registered_numbers and not args.search:
        parser.error("At least one of --reg-numbers or --search must be specified.")

    results = scrape(
        registered_numbers=registered_numbers,
        search_name=args.search,
        limit=args.limit,
        sleep_time=args.sleep,
        timeout=args.timeout,
        api_key=args.api_key
    )

    if results:
        save_data(results, args.output)
    else:
        logging.error("No data retrieved. File was not saved.")