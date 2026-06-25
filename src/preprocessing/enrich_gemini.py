import os
import json
import logging
import time
import re
from typing import List
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# Configure logger if not already configured
logger = logging.getLogger("enrich_gemini")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(sh)

class FunderEnrichment(BaseModel):
    annual_giving: str = Field(description="The total annual giving/expenditure for grants, e.g. '€90,636 (2024)' or 'Not publicly available'")
    average_grant: str = Field(description="The average grant size distributed, e.g. '€6,000' or 'Not publicly available'")
    grant_range: str = Field(description="The range of grants distributed (min to max), e.g. '€4,000 - €10,000' or 'Not publicly available'")
    funding_model: str = Field(description="The funding model of the charity, e.g. 'Open applications', 'Invitation only', 'Partnership-based', or 'Not publicly available'")
    application_details: str = Field(description="Brief description of the application process, deadlines, or how they fund")
    sources: List[str] = Field(description="List of URLs of source pages used to find this information")

def _extract_number(val_str):
    """
    Extracts a numeric float value from a string, ignoring currency symbols and years.
    Handles standard number formats (thousands separators, decimals).
    """
    if not val_str or "not publicly available" in val_str.lower():
        return None
    
    import re
    # Remove year patterns like (2024) or [2022] to avoid matching the year as the number
    cleaned = re.sub(r'[\(\[\{]\d{4}[\)\]\}]', '', val_str)
    
    # Extract digit sequences that might contain periods or commas
    numbers = re.findall(r'\d+(?:[.,]\d+)*', cleaned)
    if not numbers:
        return None
    
    num_str = numbers[0]
    
    # Standardize thousands separator vs decimal separator
    if ',' in num_str and '.' in num_str:
        num_str = num_str.replace(',', '')
    elif ',' in num_str:
        parts = num_str.split(',')
        if len(parts[-1]) == 2:
            num_str = num_str.replace(',', '.')
        else:
            num_str = num_str.replace(',', '')
    elif '.' in num_str:
        parts = num_str.split('.')
        if len(parts[-1]) != 2 and len(parts[-1]) != 1:
            num_str = num_str.replace('.', '')
            
    try:
        return float(num_str)
    except ValueError:
        return None

def _generate_content_with_retry(client, model, contents, config, max_retries=3, initial_delay=2.0):
    """
    Wraps client.models.generate_content in a retry mechanism with exponential backoff
    to handle rate limiting and transient connection errors.
    """
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to generate content after {max_retries} attempts: {e}")
                raise e
            logger.warning(
                f"Gemini API attempt {attempt + 1}/{max_retries} failed: {e}. "
                f"Retrying in {delay} seconds..."
            )
            time.sleep(delay)
            delay *= 2

def _ensure_eur(val_str):
    """
    Post-processing check to ensure that values are converted to EUR.
    If they are USD, GBP, or CHF, performs programmatic conversion.
    """
    if not val_str or not isinstance(val_str, str):
        return val_str
        
    val_lower = val_str.lower()
    if "not publicly available" in val_lower or "not disclosed" in val_lower or not val_lower.strip():
        return val_str
        
    # If it already contains EUR/€, do nothing
    if '€' in val_str or 'eur' in val_lower:
        return val_str
        
    # Parse numeric value
    num = _extract_number(val_str)
    if num is None:
        return val_str
        
    rate = None
    curr_name = ""
    if '£' in val_str or 'gbp' in val_lower or 'pound' in val_lower:
        rate = 1.2
        curr_name = "GBP"
    elif '$' in val_str or 'usd' in val_lower or 'dollar' in val_lower:
        rate = 0.92
        curr_name = "USD"
    elif 'chf' in val_lower or 'franc' in val_lower:
        rate = 1.05
        curr_name = "CHF"
        
    if rate is not None:
        converted = num * rate
        # Extract year suffix
        year_match = re.search(r'[\(\[\{]\d{4}.*?[\)\]\}]', val_str)
        suffix = f" {year_match.group(0)}" if year_match else ""
        converted_str = f"€{converted:,.0f}{suffix} (converted from {curr_name})"
        logger.info(f"Robustness check: Converted non-EUR value '{val_str}' to '{converted_str}'")
        return converted_str
        
    return val_str

def _ensure_eur_range(val_str):
    """
    Handles range formats for ensure_eur, e.g., '£4,000 - £10,000'.
    """
    if not val_str or not isinstance(val_str, str):
        return val_str
        
    val_lower = val_str.lower()
    if "not publicly available" in val_lower or "not disclosed" in val_lower or not val_lower.strip():
        return val_str
        
    if '€' in val_str or 'eur' in val_lower:
        return val_str
        
    # Check if it is a range (ignoring hyphens inside parenthesis/brackets like in years: 2023-24)
    cleaned_for_check = re.sub(r'[\(\[\{].*?[\)\]\}]', '', val_str)
    
    # Split range if contains "-" or "to"
    parts = []
    if ' - ' in cleaned_for_check:
        parts = val_str.split(' - ')
    elif '-' in cleaned_for_check:
        parts = val_str.split('-')
    elif ' to ' in cleaned_for_check.lower():
        parts = re.split(r'\s+to\s+', val_str, flags=re.IGNORECASE)
        
    if len(parts) == 2:
        val1 = _ensure_eur(parts[0].strip())
        val2 = _ensure_eur(parts[1].strip())
        
        # Clean conversion suffixes to avoid doubling
        val1_clean = re.sub(r'\s*\(converted from.*?\)', '', val1)
        val2_clean = re.sub(r'\s*\(converted from.*?\)', '', val2)
        
        if val1 != parts[0] or val2 != parts[1]:
            curr_match = re.search(r'converted from (\w+)', val1 + val2)
            curr_name = curr_match.group(1) if curr_match else "foreign currency"
            year_match = re.search(r'[\(\[\{]\d{4}.*?[\)\]\}]', val_str)
            suffix = f" {year_match.group(0)}" if year_match else ""
            return f"{val1_clean} - {val2_clean}{suffix} (converted from {curr_name})"
        else:
            return val_str
    else:
        return _ensure_eur(val_str)

def enrich_organizations(members, api_key=None, model='gemini-2.5-flash', sleep_time=2.0, save_path=None, save_fn=None):
    """
    Enriches the list of members by querying Gemini API with Google Search grounding
    to fetch financial details.
    """
    effective_api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not effective_api_key:
        logger.error("GEMINI_API_KEY is not set. Please set the environment variable or pass the api_key.")
        raise ValueError("Missing GEMINI_API_KEY environment variable.")

    logger.info(f"Initializing Gemini Client with model '{model}'...")
    client = genai.Client(api_key=effective_api_key)

    todo_members = []
    for m in members:
        info = m.get("philea_info", {})
        giving = info.get("annual_giving", "")
        # Check if already has valid financial data (not empty, and not a placeholder)
        if giving and giving.strip() and "not publicly available" not in giving.lower() and "not disclosed" not in giving.lower():
            logger.info(f"Skipping {m.get('name')} - already has financial data.")
            continue
        todo_members.append(m)

    total = len(todo_members)
    if total == 0:
        logger.info("All members are already enriched. Nothing to do.")
        return members

    logger.info(f"Starting Gemini Google Search enrichment for {total} members...")
    
    counter = 0
    for m in todo_members:
        counter += 1
        name = m.get("name", "Unknown Name")
        website = m.get("website", "")
        address = m.get("address", "")
        country = m.get("position", {}).get("country", "")

        logger.info(f"Processing {counter}/{total}: {name}")

        research_prompt = f"""
        Research the financial statistics and application details for the following philanthropic organization:
        Name: {name}
        Website: {website}
        Country: {country}
        Address: {address}

        Please find:
        - The total annual giving/expenditure for grants.
        - The average grant size distributed.
        - The range of grants distributed.
        - The funding model of the charity (e.g. open applications, invitation only, etc.).
        - Brief description of the application process/details.
        - The URLs of pages you used to find this information.
        
        Use Google Search to find this information and write a detailed report of your findings, including the source URLs.
        """

        try:
            # Step 1: Research with Google Search Grounding (with retry logic)
            research_response = _generate_content_with_retry(
                client=client,
                model=model,
                contents=research_prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            research_text = research_response.text or ""
            if not research_text.strip():
                raise ValueError("Research stage returned empty response.")

            # Step 2: Parse into structured JSON using the schema
            parse_prompt = f"""
            You are a data extraction assistant. Extract the financial and funding details from the research text below.
            
            CRITICAL INSTRUCTION:
            Ensure all currency values (annual_giving, average_grant, grant_range) are converted and formatted in Euros (€). 
            If the research text lists values in USD ($), GBP (£), CHF, or other currencies, convert them to EUR (€) using standard/recent exchange rates.
            
            If a field is not mentioned or not publicly available, set its value to "Not publicly available".

            Research text:
            {research_text}
            """

            response = _generate_content_with_retry(
                client=client,
                model=model,
                contents=parse_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=FunderEnrichment
                )
            )

            # Parse structured output
            data = json.loads(response.text)

            annual_giving = data.get("annual_giving", "Not publicly available")
            average_grant = data.get("average_grant", "Not publicly available")
            grant_range = data.get("grant_range", "Not publicly available")
            funding_model = data.get("funding_model", "Not publicly available")
            application_details = data.get("application_details", "")
            sources = data.get("sources", [])

            # Post-processing Currency Validation
            annual_giving = _ensure_eur(annual_giving)
            average_grant = _ensure_eur(average_grant)
            grant_range = _ensure_eur_range(grant_range)

            # Plausibility Check: Average grant must be smaller than annual giving
            annual_val = _extract_number(annual_giving)
            avg_val = _extract_number(average_grant)
            if annual_val is not None and avg_val is not None:
                if avg_val > annual_val:
                    logger.warning(
                        f"Plausibility check failed for {name}: average grant ({average_grant}) "
                        f"is greater than annual giving ({annual_giving}). Resetting average_grant to 'Not publicly available'."
                    )
                    average_grant = "Not publicly available"
                    avg_val = None

            # Plausibility Check: Parse and check range limits
            min_val = None
            max_val = None
            if grant_range and "not publicly available" not in grant_range.lower():
                range_numbers = []
                for p in re.split(r'[-\s|to]+', grant_range):
                    num = _extract_number(p)
                    if num is not None:
                        range_numbers.append(num)
                if len(range_numbers) >= 2:
                    min_val = min(range_numbers[:2])
                    max_val = max(range_numbers[:2])
                    if range_numbers[0] > range_numbers[1]:
                        logger.warning(f"Plausibility check: Grant range '{grant_range}' was out of order for {name}. Re-ordering.")
                        year_match = re.search(r'[\(\[\{]\d{4}.*?[\)\]\}]', grant_range)
                        suffix = f" {year_match.group(0)}" if year_match else ""
                        grant_range = f"€{min_val:,.0f} - €{max_val:,.0f}{suffix}"
                elif len(range_numbers) == 1:
                    max_val = range_numbers[0]

            # Verify max_val vs annual_val
            if annual_val is not None and max_val is not None:
                if max_val > annual_val:
                    logger.warning(
                        f"Plausibility check: Max grant limit ({max_val}) is greater than annual giving ({annual_val}) for {name}. "
                        f"Resetting grant_range to 'Not publicly available'."
                    )
                    grant_range = "Not publicly available"
                    min_val = None
                    max_val = None

            # Verify average vs range
            if avg_val is not None and min_val is not None and max_val is not None:
                if avg_val < min_val or avg_val > max_val:
                    logger.warning(
                        f"Plausibility check: Average grant ({avg_val}) is outside the grant range ({min_val} - {max_val}) for {name}. "
                        f"Resetting average_grant to 'Not publicly available'."
                    )
                    average_grant = "Not publicly available"

            # Populate philea_info
            info = m.setdefault("philea_info", {})
            info["annual_giving"] = annual_giving
            info["average_grant"] = average_grant
            info["grant_range"] = grant_range
            info["funding_model"] = funding_model
            info["application_details"] = application_details
            info["sources"] = sources

            logger.info(f"Enriched {name}: Annual Giving = {info['annual_giving']}, Funding Model = {info['funding_model']}")

        except Exception as e:
            logger.error(f"Failed to enrich {name}: {e}")
            # Ensure keys exist even on failure to avoid issues, or mark as failed
            info = m.setdefault("philea_info", {})
            info.setdefault("annual_giving", "")
            info.setdefault("average_grant", "")
            info.setdefault("grant_range", "")
            info.setdefault("funding_model", "")
            info.setdefault("application_details", "")
            info.setdefault("sources", [])

        # Save progress incrementally
        if save_path and save_fn:
            try:
                save_fn(members, save_path)
                logger.info(f"Saved intermediate progress to {save_path}")
            except Exception as e:
                logger.warning(f"Failed to save intermediate progress: {e}")

        # Sleep to avoid rapid API requests
        if counter < total:
            time.sleep(sleep_time)

    logger.info("Enrichment process completed.")
    return members
