import json
import os
import sqlite3
import hashlib
import re
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Optional, Dict, Any, Mapping

import pycountry

from bff.config import DATA_PATH, DB_PATH
from bff.utils.logging import logger
from data.db_loader import validate_database
from preprocessing.enrichment import (
    DEFAULT_REVIEW_THRESHOLD,
    PROGRAMME_TAXONOMY,
    enrich_organization,
    normalize_programme_sources,
)
from scoring.engine import load_score_configuration, score_relevance


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_list(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _json_dict(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


MONEY_QUANTUM = Decimal("0.01")
GRANT_SCOPE_NOTE = (
    "Results reflect available cached 360Giving records and are not a complete "
    "representation of the UK, DACH, European, global, or wider funding market."
)
STRICT_GRANT_DATE_SQL = (
    "DATE(date) IS NOT NULL "
    "AND DATE(date) = SUBSTR(TRIM(CAST(date AS TEXT)), 1, 10)"
)

UK_CONSTITUENT_CODES = {"GB-ENG", "GB-SCT", "GB-WLS", "GB-NIR"}
UK_CONSTITUENT_NAMES = {"england", "scotland", "wales", "northern ireland"}
COUNTRY_CODE_ALIASES = {"UK": "GB"}
COUNTRY_NAME_ALIASES = {
    "britain": "GB",
    "great britain": "GB",
    "uk": "GB",
    "u.k.": "GB",
    "united kingdom": "GB",
}
COUNTRY_DISPLAY_NAMES = {
    "BO": "Bolivia",
    "BN": "Brunei",
    "CD": "Democratic Republic of the Congo",
    "CG": "Republic of the Congo",
    "CI": "Côte d’Ivoire",
    "GB": "United Kingdom",
    "IR": "Iran",
    "KR": "South Korea",
    "LA": "Laos",
    "MD": "Moldova",
    "MK": "North Macedonia",
    "PS": "Palestine",
    "RU": "Russia",
    "SY": "Syria",
    "TZ": "Tanzania",
    "US": "United States",
    "VE": "Venezuela",
    "VN": "Vietnam",
}


def _country_from_code(value: Any) -> Optional[Dict[str, str]]:
    """Resolve a genuine ISO country code, rolling UK constituents up to GB."""
    code = str(value or "").strip().upper().replace("_", "-")
    if not code:
        return None
    if code in UK_CONSTITUENT_CODES:
        code = "GB"
    code = COUNTRY_CODE_ALIASES.get(code, code)
    country = None
    if len(code) == 2:
        country = pycountry.countries.get(alpha_2=code)
    elif len(code) == 3:
        country = pycountry.countries.get(alpha_3=code)
    if not country:
        return None
    alpha_2 = country.alpha_2
    return {
        "country_code": alpha_2,
        "country_name": COUNTRY_DISPLAY_NAMES.get(alpha_2, country.name),
    }


def _country_from_name(value: Any) -> Optional[Dict[str, str]]:
    """Resolve an explicit country name without geocoding cities or broad regions."""
    name = str(value or "").strip()
    if not name:
        return None
    folded = name.casefold()
    if folded in UK_CONSTITUENT_NAMES:
        return _country_from_code("GB")
    alias = COUNTRY_NAME_ALIASES.get(folded)
    if alias:
        return _country_from_code(alias)
    if folded in {"global", "international", "multi", "multiple", "various", "worldwide"}:
        return None
    try:
        country = pycountry.countries.lookup(name)
    except LookupError:
        return None
    return _country_from_code(country.alpha_2)


def _beneficiary_countries(normalized_raw: Any, source_raw: Any) -> List[Dict[str, Any]]:
    """Return only countries explicitly evidenced by beneficiary-geography fields.

    `beneficiary_geography_normalized` is authoritative. The raw source field is
    used only for explicit ISO country codes or explicit country names that the
    current, deliberately small enrichment taxonomy has not normalized yet.
    Headquarters and text-inferred operating geographies are never consulted.
    """
    normalized = _json_list(normalized_raw)
    source = _json_list(source_raw)
    countries: Dict[str, Dict[str, Any]] = {}

    def add_country(resolved: Dict[str, str], original: Any) -> None:
        code = resolved["country_code"]
        current = countries.setdefault(
            code, {**resolved, "original_geographies": []}
        )
        label = str(original or "").strip()
        if label and label not in current["original_geographies"]:
            current["original_geographies"].append(label)

    for location in normalized:
        if not isinstance(location, Mapping):
            continue
        scope = str(location.get("scope") or "").strip().casefold()
        if scope and scope not in {"country", "constituent_country"}:
            continue
        resolved = (
            _country_from_code(location.get("code"))
            or _country_from_name(location.get("name"))
        )
        if resolved:
            add_country(resolved, location.get("name") or location.get("code"))

    for location in source:
        if isinstance(location, Mapping):
            resolved = _country_from_code(location.get("countryCode"))
            if not resolved and str(location.get("geoCodeType") or "").upper() == "CTRY":
                resolved = _country_from_name(location.get("name"))
            if not resolved and not location.get("geoCodeType"):
                resolved = _country_from_name(location.get("name") or location.get("country"))
        else:
            resolved = _country_from_code(location) or _country_from_name(location)
        if resolved:
            if isinstance(location, Mapping):
                original = (
                    location.get("name")
                    or location.get("country")
                    or location.get("countryCode")
                )
            else:
                original = location
            add_country(resolved, original)
    return sorted(countries.values(), key=lambda item: item["country_code"])


def _funder_headquarters_country(
    raw_grant_data: Any, directory_headquarters: Any
) -> tuple[Optional[Dict[str, str]], Optional[str]]:
    """Resolve an explicit funder address country, then the directory HQ fallback.

    The returned country is an organization-location proxy. It is deliberately kept
    separate from beneficiary geography and must not be described as a verified
    payment origin.
    """
    raw_record = _json_dict(raw_grant_data)
    grant_data = raw_record.get("data") if isinstance(raw_record.get("data"), Mapping) else raw_record
    funders = grant_data.get("fundingOrganization") if isinstance(grant_data, Mapping) else []
    if isinstance(funders, Mapping):
        funders = [funders]
    if not isinstance(funders, list):
        funders = []

    for funder in funders:
        if not isinstance(funder, Mapping):
            continue
        resolved = _country_from_name(funder.get("addressCountry"))
        if not resolved:
            locations = funder.get("location")
            if isinstance(locations, Mapping):
                locations = [locations]
            for location in locations if isinstance(locations, list) else []:
                if not isinstance(location, Mapping):
                    continue
                resolved = (
                    _country_from_code(location.get("countryCode"))
                    or _country_from_name(location.get("country") or location.get("name"))
                )
                if resolved:
                    break
        if resolved:
            return resolved, "360Giving funding-organization address"

    resolved = _country_from_name(directory_headquarters)
    if resolved:
        return resolved, "Organization-directory registered location"
    return None, None


def _matches_funding_regions(
    normalized_raw: Any,
    source_raw: Any,
    countries: List[Dict[str, Any]],
    selected_regions: set[str],
) -> bool:
    """Mirror the directory beneficiary-geography filter against grant rows."""
    if not selected_regions:
        return True
    candidates = {
        str(country.get("country_name") or "").strip().casefold()
        for country in countries
        if country.get("country_name")
    }
    for location in _json_list(normalized_raw) + _json_list(source_raw):
        if isinstance(location, Mapping):
            for key in ("name", "macro_region", "country", "countryCode"):
                value = str(location.get(key) or "").strip().casefold()
                if value:
                    candidates.add(value)
        else:
            value = str(location or "").strip().casefold()
            if value:
                candidates.add(value)
    return bool(candidates.intersection(selected_regions))


def _accepted_programme_categories(
    source_raw: Any, inferred_raw: Any, scores_raw: Any
) -> List[str]:
    """Apply the same source-first taxonomy rule used by programme allocation."""
    source_categories, _ = normalize_programme_sources(_json_list(source_raw))
    if source_categories:
        return sorted(set(source_categories))
    scores = _json_dict(scores_raw)
    accepted = []
    for category in _json_list(inferred_raw):
        if category not in PROGRAMME_TAXONOMY:
            continue
        try:
            confidence = float(scores.get(category, 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence >= DEFAULT_REVIEW_THRESHOLD:
            accepted.append(category)
    return sorted(set(accepted)) or ["Unclassified"]


def _top_counter_items(counter: Counter, limit: int = 3) -> List[Dict[str, Any]]:
    return [
        {"name": str(name), "count": count}
        for name, count in sorted(
            counter.items(), key=lambda item: (-item[1], str(item[0]).casefold())
        )[:limit]
    ]


def _money_minor_units(value):
    """Return a validation status and deterministic two-decimal minor units."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    if isinstance(value, bool):
        return "invalid", None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "invalid", None
    if not amount.is_finite():
        return "invalid", None
    quantized = amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    minor_units = int(quantized * 100)
    if quantized < 0:
        return "negative", minor_units
    if quantized == 0:
        return "zero", 0
    return "valid", minor_units


def _minor_units_to_amount(value: int) -> float:
    return float(Decimal(value) / Decimal(100))


def _month_offset(month: str, offset: int) -> str:
    year, month_number = (int(part) for part in month.split("-"))
    absolute = year * 12 + month_number - 1 + offset
    resolved_year, resolved_month = divmod(absolute, 12)
    return f"{resolved_year:04d}-{resolved_month + 1:02d}"


def _month_range(from_month: str, months: int) -> list[str]:
    return [_month_offset(from_month, offset) for offset in range(months)]


def _amount_policy(maximum_minor_units: Optional[int] = None):
    return {
        "monetary_precision": "minor_units_2_decimal_places",
        "rounding": "ROUND_HALF_UP",
        "zero_amounts": "included_when_source_value_is_numeric_zero",
        "negative_amounts": "excluded_and_reported",
        "upper_bound": "no_unapproved_implausibility_threshold_applied",
        "maximum_observed_amount": (
            _minor_units_to_amount(maximum_minor_units)
            if maximum_minor_units is not None else None
        ),
    }


def _empty_classification_coverage():
    return {
        "qualifying_grant_count": 0,
        "classified_grant_count": 0,
        "unclassified_grant_count": 0,
        "classified_percentage": 0.0,
        "source_classified_grant_count": 0,
        "inferred_classified_grant_count": 0,
        "source_percentage": 0.0,
        "inferred_percentage": 0.0,
        "multiple_programme_area_grant_count": 0,
        "invalid_source_label_count": 0,
        "low_confidence_inference_count": 0,
    }


def _stable_party_id(role, charity_id, source_id, name, country="", source="360Giving"):
    if charity_id is not None:
        return f"organization:{charity_id}"
    namespace = re.sub(r"[^a-z0-9]+", "", (source or "source").lower()) or "source"
    if source_id:
        return f"{namespace}:{role}:{source_id}"
    normalized = re.sub(r"[^a-z0-9]+", "-", (name or "unnamed").lower()).strip("-")
    digest_input = f"{source}|{role}|{normalized}|{country}".encode("utf-8")
    digest = hashlib.sha256(digest_input).hexdigest()[:16]
    return f"{namespace}:{role}:fallback:{digest}"

class CharityRepository(ABC):
    """
    Abstract Base Class for Charity data access.
    Allows swapping storage backends (e.g. JSON to SQL Database) without changing API layer.
    """
    @abstractmethod
    async def get_all(
        self, 
        search: Optional[str] = None, 
        reg_status: Optional[str] = None, 
        tag: Optional[str] = None,
        region: Optional[str] = None,
        size: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grants_map(
        self,
        currency: Optional[str] = None,
        min_coverage: float = 0.30,
        search: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grant_summary(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grant_trends(
        self, currency: Optional[str] = None, months: int = 24
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grant_themes(
        self, currency: Optional[str] = None
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_sankey_data(
        self, charity_id: int, currency: Optional[str] = None, limit: int = 30
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_score(
        self, charity_id: int, target_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        pass


class JSONCharityRepository(CharityRepository):
    """
    JSON File-backed implementation of CharityRepository.
    Loads and caches JSON records in memory.
    """
    def __init__(self, data_path: str = DATA_PATH):
        self.data_path = data_path
        self._data: List[Dict[str, Any]] = []
        self.load_data()

    def load_data(self):
        """Loads data from the JSON file path. Handles missing/corrupted files gracefully."""
        if not os.path.exists(self.data_path):
            logger.warning(f"Data file not found at {self.data_path}. Initializing with empty list.")
            self._data = []
            return

        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info(f"Loaded {len(self._data)} charity records from {self.data_path}")
        except Exception as e:
            logger.error(f"Failed to parse JSON data file at {self.data_path}: {e}")
            self._data = []

    def _get_financials(self, charity: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
        """
        Helper to extract latest income and expenditure, fallback to financial_history if needed.
        """
        all_details = charity.get("all_details", {})
        income = all_details.get("latest_income")
        expenditure = all_details.get("latest_expenditure")

        # Fallback to financial history if direct fields are missing
        if (income is None or expenditure is None) and charity.get("financial_history"):
            history = charity["financial_history"]
            sorted_history = sorted(
                history, 
                key=lambda x: x.get("financial_period_end_date", ""), 
                reverse=True
            )
            if sorted_history:
                latest_period = sorted_history[0]
                if income is None:
                    income = latest_period.get("income")
                if expenditure is None:
                    expenditure = latest_period.get("expenditure")

        return income, expenditure

    async def get_all(
        self, 
        search: Optional[str] = None, 
        reg_status: Optional[str] = None, 
        tag: Optional[str] = None,
        region: Optional[str] = None,
        size: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        filtered = self._data

        def enrichment(item):
            return enrich_organization(item)

        # Filter by search string (case-insensitive name search)
        if search:
            search_lower = search.lower()
            filtered = [
                c for c in filtered 
                if search_lower in c.get("all_details", {}).get("charity_name", "").lower()
            ]

        # Filter by reg_status (e.g. 'R' for active, 'RM' for removed)
        if reg_status:
            reg_status_upper = reg_status.upper()
            filtered = [
                c for c in filtered 
                if c.get("all_details", {}).get("reg_status", "").upper() == reg_status_upper
            ]

        # Filter by tags (multiple choice, match ANY)
        if tags:
            selected = {t.casefold() for t in tags}
            filtered = [
                c for c in filtered
                if selected.intersection({
                    value.casefold()
                    for value in (
                        enrichment(c)["programme_areas_source"]
                        + enrichment(c)["programme_areas_inferred"]
                    )
                })
            ]
        elif tag:
            selected = tag.casefold()
            filtered = [
                c for c in filtered
                if selected in {
                    value.casefold()
                    for value in (
                        enrichment(c)["programme_areas_source"]
                        + enrichment(c)["programme_areas_inferred"]
                    )
                }
            ]

        # Filter by foundation regions (multiple choice, match ANY)
        if foundation_regions:
            selected = {r.casefold() for r in foundation_regions}
            filtered = [
                c for c in filtered
                if selected.intersection({
                    str(enrichment(c).get("headquarters_country") or "").casefold(),
                    str(enrichment(c).get("headquarters_region") or "").casefold(),
                })
            ]

        # Filter by funding regions (multiple choice, match ANY)
        if funding_regions:
            # JSON fallback has no normalized grant transactions or destinations.
            filtered = []

        # Filter by legacy region
        if not foundation_regions and not funding_regions and region:
            region_lower = region.lower()
            filtered = [
                c for c in filtered
                if any(region_lower in r.lower() for r in c.get("geo_locations", {}).keys())
            ]

        # Filter by size
        if min_annual_giving is not None:
            filtered = [
                c for c in filtered
                if (self._get_financials(c)[1] or 0.0) >= min_annual_giving
            ]
        elif size:
            size_lower = size.lower()
            temp = []
            for c in filtered:
                income, expenditure = self._get_financials(c)
                exp = expenditure or 0.0
                if size_lower == "small" and exp < 1000000:
                    temp.append(c)
                elif size_lower == "medium" and exp >= 1000000 and exp <= 10000000:
                    temp.append(c)
                elif size_lower == "large" and exp > 10000000:
                    temp.append(c)
            filtered = temp

        # Filter by average grant size
        if min_avg_grant_size is not None and min_avg_grant_size > 0:
            # Simple heuristic for JSON repo mock filtering: if expenditure is non-zero, let's assume it matches
            filtered = [
                c for c in filtered
                if (self._get_financials(c)[1] or 0.0) >= min_avg_grant_size
            ]

        # Map to baseline models
        results = []
        for c in filtered[skip : skip + limit]:
            income, expenditure = self._get_financials(c)
            all_details = c.get("all_details", {})
            enriched = enrichment(c)
            results.append({
                "registered_charity_number": c.get("registered_charity_number"),
                "suffix": c.get("suffix", 0),
                "link": c.get("link"),
                "charity_name": all_details.get("charity_name", ""),
                "reg_status": all_details.get("reg_status", "RM"),
                "reporting_status": all_details.get("reporting_status"),
                "removal_reason": all_details.get("removal_reason"),
                "latest_income": income,
                "latest_expenditure": expenditure,
                "programme_areas_source": enriched["programme_areas_source"],
                "programme_areas_inferred": enriched["programme_areas_inferred"],
                "geographic_focus_source": enriched["geographic_focus_source"],
                "geographic_focus_inferred": enriched["geographic_focus_inferred"],
                "headquarters_country": enriched["headquarters_country"],
                "headquarters_region": enriched["headquarters_region"],
                "programme_area_review_required": enriched["programme_area_review_required"],
                "geography_review_required": enriched["geography_review_required"],
                "enrichment_rule_version": enriched["enrichment_rule_version"],
                "organization_type": all_details.get("charity_type") or "charity",
                "primary_source": "Charity Commission for England and Wales",
                "source_names": ["Charity Commission for England and Wales"],
                "source_record_id": str(c.get("registered_charity_number")),
                "source_url": c.get("link"),
                "transaction_coverage": "source_without_transactions",
            })
        return results

    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        for c in self._data:
            if c.get("registered_charity_number") == reg_charity_number:
                return {
                    **c,
                    **enrich_organization(c),
                    "organization_type": c.get("all_details", {}).get("charity_type") or "charity",
                    "primary_source": "Charity Commission for England and Wales",
                    "source_names": ["Charity Commission for England and Wales"],
                    "source_record_id": str(c.get("registered_charity_number")),
                    "source_url": c.get("link"),
                    "source_records": [],
                    "transaction_coverage": "source_without_transactions",
                }
        return None

    async def get_stats(self) -> Dict[str, Any]:
        total = len(self._data)
        active = 0
        removed = 0
        incomes = []
        expenditures = []

        for c in self._data:
            all_details = c.get("all_details", {})
            status = all_details.get("reg_status", "").upper()
            if status == "R":
                active += 1
            else:
                removed += 1

            inc, exp = self._get_financials(c)
            if inc is not None:
                incomes.append(inc)
            if exp is not None:
                expenditures.append(exp)

        avg_income = sum(incomes) / len(incomes) if incomes else 0.0
        avg_exp = sum(expenditures) / len(expenditures) if expenditures else 0.0

        return {
            "total_charities": total,
            "active_charities": active,
            "removed_charities": removed,
            "average_income": avg_income,
            "average_expenditure": avg_exp,
            "total_grants": None,
            "data_mode": "cached_source_without_transactions",
            "source": ["Charity Commission for England and Wales"],
            "source_counts": {"Charity Commission for England and Wales": total},
            "organization_type_counts": {"charity": total},
        }

    async def get_grants_map(
        self,
        currency: Optional[str] = None,
        min_coverage: float = 0.30,
        search: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
    ) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "geographic_dimension": "beneficiary_location",
            "items": [],
            "known_geography_count": 0,
            "unknown_geography_count": 0,
            "coverage_percentage": 0.0,
            "currencies": [],
            "selected_currency": currency.upper() if currency else None,
            "funding_status": "transaction_data_unavailable",
            "funding_mode_available": False,
            "grant_country_association_count": 0,
            "multi_country_grant_count": 0,
            "funding_excluded_multi_country_count": 0,
            "funding_excluded_multi_country_amount": 0.0,
            "funding_excluded_currency_count": 0,
            "funding_excluded_invalid_amount_count": 0,
            "connections": [],
            "connection_grant_count": 0,
            "connection_excluded_no_headquarters_count": 0,
            "connection_same_country_count": 0,
            "minimum_coverage_threshold": min_coverage,
            "metadata": {
                "data_mode": "cached_source_without_transactions",
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "record_count": 0,
                "derivation": "No grant aggregation is available in JSON fallback mode.",
                "coverage": 0.0,
                "limitations": ["Build a valid SQLite database to enable transaction geography."],
            },
        }

    async def get_grant_summary(self) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "total_grant_count": 0,
            "currencies": [],
            "largest_donors": [],
            "largest_recipients": [],
            "metadata": {
                "data_mode": "cached_source_without_transactions",
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "record_count": 0,
                "limitations": ["The JSON fallback does not contain normalized grant transactions."],
            },
        }

    async def get_grant_trends(
        self, currency: Optional[str] = None, months: int = 24
    ) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "currency": currency.upper() if currency else None,
            "available_currencies": [],
            "date_basis": "award_date",
            "period": None,
            "items": [],
            "excluded": {},
            "zero_amount_count": 0,
            "latest_award_date": None,
            "last_refreshed_at": None,
            "source": [],
            "data_mode": "cached_source_without_transactions",
            "amount_policy": _amount_policy(),
            "scope": {"coverage_note": GRANT_SCOPE_NOTE},
        }

    async def get_grant_themes(
        self, currency: Optional[str] = None
    ) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "currency": currency.upper() if currency else None,
            "available_currencies": [],
            "allocation_method": "equal_split_across_available_categories",
            "classification_precedence": [
                "valid_source_category", "accepted_inferred_category", "unclassified"
            ],
            "inference_confidence_threshold": DEFAULT_REVIEW_THRESHOLD,
            "items": [],
            "classification_coverage": _empty_classification_coverage(),
            "qualifying_amount": 0.0,
            "allocated_amount": 0.0,
            "excluded": {},
            "zero_amount_count": 0,
            "last_refreshed_at": None,
            "source": [],
            "data_mode": "cached_source_without_transactions",
            "amount_policy": _amount_policy(),
            "scope": {"coverage_note": GRANT_SCOPE_NOTE},
        }

    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "organization_id": charity_id,
            "role": role,
            "transaction_coverage": "not_loaded",
            "grant_count": 0,
            "currencies": [],
            "grants": [],
            "metadata": {
                "data_mode": "cached_source_without_transactions",
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "record_count": 0,
                "limitations": ["The JSON fallback does not expose 360Giving transactions."],
            },
        }

    async def get_sankey_data(
        self, charity_id: int, currency: Optional[str] = None, limit: int = 30
    ) -> Dict[str, Any]:
        return {
            "status": "transaction_data_unavailable",
            "nodes": [],
            "links": [],
            "metadata": {
                "source": ["Charity Commission for England and Wales"],
                "generated_at": _utc_now(),
                "grant_count": 0,
                "included_grant_count": 0,
                "excluded_grant_count": 0,
                "excluded_reasons": {},
                "included_value": 0.0,
                "currencies": [],
                "selected_currency": currency,
                "conversion_method": "none",
                "filters_applied": {"organization_id": charity_id, "limit": limit},
                "truncation_applied": False,
            },
        }

    async def get_score(
        self, charity_id: int, target_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        organization = await self.get_by_id(charity_id)
        if not organization:
            raise KeyError(charity_id)
        config = load_score_configuration()
        _, expenditure = self._get_financials(organization)
        score_input = {
            **organization,
            "annual_expenditure": expenditure,
        }
        return score_relevance(
            score_input,
            target_profile or config.example_target_profile,
            grant_statistics={},
            configuration=config,
        )


class SQLiteCharityRepository(CharityRepository):
    """
    SQLite database-backed implementation of CharityRepository.
    Loads and queries structured charity commission and grant details.
    """
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info(f"SQLite Charity Repository initialized at: {self.db_path}")
        
    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    async def get_all(
        self, 
        search: Optional[str] = None, 
        reg_status: Optional[str] = None, 
        tag: Optional[str] = None,
        region: Optional[str] = None,
        size: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = """
            SELECT charity_id, name, type, website, email, address, city, state, country, 
                   latitude, longitude, annual_income, annual_expenditure, thematic_focus, 
                   geographic_focus, raw_cc_data, programme_areas_source,
                   programme_areas_inferred, geographic_focus_source,
                   geographic_focus_inferred, headquarters_country, headquarters_region,
                   programme_area_review_required, geography_review_required,
                   enrichment_rule_version, organization_type, primary_source, source_names,
                   source_record_id, source_url, transaction_coverage
            FROM charities 
            WHERE 1=1
        """
        params = []
        
        if search:
            query += """ AND (
                name LIKE ?
                OR charity_id IN (
                    SELECT funding_charity_id FROM grants
                    WHERE funding_name LIKE ?
                )
            )"""
            params.extend([f"%{search}%", f"%{search}%"])
            
        if reg_status:
            query += " AND json_extract(raw_cc_data, '$.all_details.reg_status') = ?"
            params.append(reg_status.upper())
            
        if tags:
            tag_conds = []
            for t in tags:
                tag_conds.append("""(
                    EXISTS (SELECT 1 FROM json_each(charities.programme_areas_source) WHERE value = ?)
                    OR EXISTS (SELECT 1 FROM json_each(charities.programme_areas_inferred) WHERE value = ?)
                )""")
                params.extend([t, t])
            query += " AND (" + " OR ".join(tag_conds) + ")"
        elif tag:
            query += """ AND (
                EXISTS (SELECT 1 FROM json_each(charities.programme_areas_source) WHERE value = ?)
                OR EXISTS (SELECT 1 FROM json_each(charities.programme_areas_inferred) WHERE value = ?)
            )"""
            params.extend([tag, tag])
            
        if foundation_regions:
            fr_conds = []
            for r in foundation_regions:
                fr_conds.append("(headquarters_country = ? OR headquarters_region = ?)")
                params.extend([r, r])
            query += " AND (" + " OR ".join(fr_conds) + ")"

        if funding_regions:
            selected_funding_regions = {
                str(value).strip().casefold()
                for value in funding_regions
                if str(value).strip()
            }
            cursor.execute("""
                SELECT funding_charity_id, beneficiary_geography_normalized,
                       beneficiary_geography
                FROM grants
                WHERE funding_charity_id IS NOT NULL
            """)
            matching_funder_ids = set()
            for funder_id, normalized_locations, source_locations in cursor.fetchall():
                countries = _beneficiary_countries(
                    normalized_locations, source_locations
                )
                if _matches_funding_regions(
                    normalized_locations,
                    source_locations,
                    countries,
                    selected_funding_regions,
                ):
                    matching_funder_ids.add(funder_id)
            if matching_funder_ids:
                placeholders = ",".join("?" for _ in matching_funder_ids)
                query += f" AND charity_id IN ({placeholders})"
                params.extend(sorted(matching_funder_ids))
            else:
                query += " AND 0"

        if not foundation_regions and not funding_regions and region:
            query += """ AND (
                headquarters_country = ?
                OR headquarters_region = ?
                OR EXISTS (SELECT 1 FROM json_each(charities.geographic_focus_inferred) WHERE value = ?)
                OR charity_id IN (
                    SELECT funding_charity_id FROM grants
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(grants.beneficiary_geography_normalized)
                        WHERE json_extract(value, '$.name') = ?
                           OR json_extract(value, '$.macro_region') = ?
                    )
                )
            )"""
            params.extend([region, region, region, region, region])

        if min_annual_giving is not None:
            query += " AND annual_expenditure >= ?"
            params.append(min_annual_giving)
        elif size == "small":
            query += " AND annual_expenditure < 1000000"
        elif size == "medium":
            query += " AND annual_expenditure >= 1000000 AND annual_expenditure <= 10000000"
        elif size == "large":
            query += " AND annual_expenditure > 10000000"

        if min_avg_grant_size is not None and min_avg_grant_size > 0:
            query += """ AND charity_id IN (
                SELECT funding_charity_id 
                FROM grants 
                GROUP BY funding_charity_id 
                HAVING COUNT(DISTINCT currency) = 1 AND AVG(amount) >= ?
            )"""
            params.append(min_avg_grant_size)
            
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, skip])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            raw_cc = json.loads(r[15]) if r[15] else {}
            all_details = raw_cc.get("all_details") or {}
            
            results.append({
                "registered_charity_number": r[0],
                "suffix": all_details.get("group_subsid_suffix", 0),
                "link": raw_cc.get("link", f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regid={r[0]}&subid=0"),
                "charity_name": r[1],
                "reg_status": all_details.get("reg_status", "UNKNOWN"),
                "reporting_status": all_details.get("reporting_status"),
                "removal_reason": all_details.get("removal_reason"),
                "latest_income": r[11],
                "latest_expenditure": r[12],
                "programme_areas_source": _json_list(r[16]),
                "programme_areas_inferred": _json_list(r[17]),
                "geographic_focus_source": _json_list(r[18]),
                "geographic_focus_inferred": _json_list(r[19]),
                "headquarters_country": r[20],
                "headquarters_region": r[21],
                "programme_area_review_required": bool(r[22]),
                "geography_review_required": bool(r[23]),
                "enrichment_rule_version": r[24],
                "organization_type": r[25] or r[2] or "unknown",
                "primary_source": r[26],
                "source_names": _json_list(r[27]),
                "source_record_id": r[28],
                "source_url": r[29],
                "transaction_coverage": r[30] or "unknown",
            })
        return results

    async def get_by_id(self, reg_charity_number: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT raw_cc_data, programme_areas_source, programme_areas_inferred,
                   programme_area_scores, programme_area_method, programme_area_evidence,
                   programme_area_review_required, geographic_focus_source,
                   geographic_focus_inferred, headquarters_country, headquarters_region,
                   geography_method, geography_confidence, geography_evidence,
                   geography_review_required, enrichment_rule_version, organization_type,
                   primary_source, source_names, source_record_id, source_url,
                   source_records, ingestion_timestamp, transaction_coverage,
                   deduplication_status, deduplication_candidates
            FROM charities WHERE charity_id = ?
        """, (reg_charity_number,))
        row = cursor.fetchone()
        cursor.execute("""
            SELECT name, type, website, email, address, city, state, country,
                   annual_income, annual_expenditure
            FROM charities WHERE charity_id = ?
        """, (reg_charity_number,))
        profile_row = cursor.fetchone()
        conn.close()

        if row and profile_row:
            raw = _json_dict(row[0])
            all_details = raw.get("all_details")
            if not isinstance(all_details, dict):
                all_details = {}
            all_details.setdefault("organisation_number", reg_charity_number)
            all_details.setdefault("reg_charity_number", reg_charity_number)
            all_details.setdefault("group_subsid_suffix", 0)
            all_details.setdefault("charity_name", profile_row[0])
            all_details.setdefault("charity_type", row[16] or profile_row[1])
            all_details.setdefault("reg_status", "UNKNOWN")
            all_details.setdefault("latest_income", profile_row[8])
            all_details.setdefault("latest_expenditure", profile_row[9])
            all_details.setdefault("address_line_one", profile_row[4] or None)
            all_details.setdefault("address_line_three", profile_row[6] or None)
            all_details.setdefault("address_line_four", profile_row[5] or None)
            all_details.setdefault("email", profile_row[3] or None)
            all_details.setdefault("web", profile_row[2] or None)
            raw.setdefault("registered_charity_number", reg_charity_number)
            raw.setdefault("suffix", all_details.get("group_subsid_suffix", 0))
            raw.setdefault("link", row[20])
            raw["all_details"] = all_details
            raw.setdefault("assets_liabilities", [])
            raw.setdefault("financial_history", [])
            raw.setdefault("who_what_how", [])
            raw.update({
                "programme_areas_source": _json_list(row[1]),
                "programme_areas_inferred": _json_list(row[2]),
                "programme_area_scores": json.loads(row[3]) if row[3] else {},
                "programme_area_method": row[4],
                "programme_area_evidence": _json_list(row[5]),
                "programme_area_review_required": bool(row[6]),
                "geographic_focus_source": _json_list(row[7]),
                "geographic_focus_inferred": _json_list(row[8]),
                "headquarters_country": row[9],
                "headquarters_region": row[10],
                "geography_method": row[11],
                "geography_confidence": row[12],
                "geography_evidence": _json_list(row[13]),
                "geography_review_required": bool(row[14]),
                "enrichment_rule_version": row[15],
                "organization_type": row[16] or "unknown",
                "primary_source": row[17],
                "source_names": _json_list(row[18]),
                "source_record_id": row[19],
                "source_url": row[20],
                "source_records": _json_list(row[21]),
                "ingestion_timestamp": row[22],
                "transaction_coverage": row[23] or "unknown",
                "deduplication_status": row[24],
                "deduplication_candidates": _json_list(row[25]),
            })
            return raw
        return None

    async def get_stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM charities")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM charities WHERE json_extract(raw_cc_data, '$.all_details.reg_status') = 'R'")
        active = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM charities WHERE json_extract(raw_cc_data, '$.all_details.reg_status') = 'RM'")
        removed = cursor.fetchone()[0]
        
        cursor.execute("SELECT AVG(annual_income) FROM charities WHERE annual_income IS NOT NULL")
        avg_income = cursor.fetchone()[0] or 0.0
        
        cursor.execute("SELECT AVG(annual_expenditure) FROM charities WHERE annual_expenditure IS NOT NULL")
        avg_exp = cursor.fetchone()[0] or 0.0

        cursor.execute("SELECT COUNT(*) FROM grants")
        total_grants = cursor.fetchone()[0]

        cursor.execute("SELECT DISTINCT source FROM grants WHERE source IS NOT NULL AND source != ''")
        grant_sources = sorted(row[0] for row in cursor.fetchall())

        cursor.execute("""
            SELECT source.value, COUNT(*)
            FROM charities, json_each(charities.source_names) AS source
            GROUP BY source.value
        """)
        source_counts = {str(row[0]): row[1] for row in cursor.fetchall()}
        cursor.execute("""
            SELECT COALESCE(NULLIF(organization_type, ''), 'unknown'), COUNT(*)
            FROM charities GROUP BY COALESCE(NULLIF(organization_type, ''), 'unknown')
        """)
        organization_type_counts = {str(row[0]): row[1] for row in cursor.fetchall()}
        
        conn.close()
        return {
            "total_charities": total,
            "active_charities": active,
            "removed_charities": removed,
            "average_income": avg_income,
            "average_expenditure": avg_exp,
            "total_grants": total_grants,
            "data_mode": "derived_from_cached_source",
            "source": sorted(set(source_counts) | set(grant_sources)),
            "source_counts": source_counts,
            "organization_type_counts": organization_type_counts,
        }

    async def get_grants_map(
        self,
        currency: Optional[str] = None,
        min_coverage: float = 0.30,
        search: Optional[str] = None,
        tags: Optional[List[str]] = None,
        foundation_regions: Optional[List[str]] = None,
        funding_regions: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT g.grant_id, g.amount, g.currency,
                   g.beneficiary_geography_normalized, g.beneficiary_geography,
                   g.funding_name, g.recipient_name, g.programme_area_source,
                   g.programme_area_inferred, g.programme_area_scores,
                   g.funding_charity_id, g.raw_grant_data,
                   c.name, c.headquarters_country, c.headquarters_region,
                   c.annual_expenditure, c.programme_areas_source,
                   c.programme_areas_inferred
            FROM grants AS g
            LEFT JOIN charities AS c ON c.charity_id = g.funding_charity_id
            WHERE g.source = ?
            ORDER BY g.grant_id
        """, ("360Giving",))
        source_rows = cursor.fetchall()
        cursor.execute("""
            SELECT funding_charity_id, AVG(amount)
            FROM grants
            WHERE source = ? AND funding_charity_id IS NOT NULL
            GROUP BY funding_charity_id
            HAVING COUNT(DISTINCT UPPER(TRIM(currency))) = 1
        """, ("360Giving",))
        average_grants = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        selected_tags = {str(value).casefold() for value in (tags or [])}
        selected_foundation_regions = {
            str(value).casefold() for value in (foundation_regions or [])
        }
        selected_funding_regions = {
            str(value).casefold() for value in (funding_regions or [])
        }
        search_value = str(search or "").strip().casefold()
        prepared_rows = []
        for row in source_rows:
            countries = _beneficiary_countries(row[3], row[4])
            if search_value and search_value not in str(row[5] or "").casefold() \
                    and search_value not in str(row[12] or "").casefold():
                continue
            if selected_tags:
                organization_tags = {
                    str(value).casefold()
                    for value in _json_list(row[16]) + _json_list(row[17])
                }
                if not selected_tags.intersection(organization_tags):
                    continue
            if selected_foundation_regions:
                organization_regions = {
                    str(row[13] or "").casefold(),
                    str(row[14] or "").casefold(),
                }
                if not selected_foundation_regions.intersection(organization_regions):
                    continue
            if not _matches_funding_regions(
                row[3], row[4], countries, selected_funding_regions
            ):
                continue
            if min_annual_giving is not None:
                try:
                    annual_giving = float(row[15])
                except (TypeError, ValueError):
                    continue
                if annual_giving < min_annual_giving:
                    continue
            if min_avg_grant_size is not None and min_avg_grant_size > 0:
                average_grant = average_grants.get(row[10])
                if average_grant is None or average_grant < min_avg_grant_size:
                    continue
            prepared_rows.append((row, countries))

        currencies = sorted({
            str(row[2]).upper() for row, _countries in prepared_rows if row[2]
        })
        requested_currency = str(currency or "").strip().upper() or None
        selected_currency = (
            requested_currency
            if requested_currency in currencies
            else currencies[0] if not requested_currency and len(currencies) == 1
            else None
        )
        funding_status = "available" if selected_currency else (
            "unsupported_currency" if requested_currency else "currency_selection_required"
        )
        applied_filters = {
            "organization_search": str(search or "").strip() or None,
            "programme_areas": tags or [],
            "foundation_regions": foundation_regions or [],
            "funding_regions": funding_regions or [],
            "min_annual_giving": min_annual_giving,
            "min_avg_grant_size": min_avg_grant_size,
        }
        base_metadata = {
            "data_mode": "derived_from_cached_source",
            "source": ["360Giving", "Organization directory"],
            "generated_at": _utc_now(),
            "record_count": len(prepared_rows),
            "derivation": (
                "Country associations from beneficiary_geography_normalized, with an explicit "
                "ISO-code fallback to the raw beneficiary_geography source field. Organization "
                "filters use the matched funder record. Connection origins use an explicit "
                "360Giving funder address country or the registered organization-directory "
                "headquarters country."
            ),
            "coverage": None,
            "limitations": [
                GRANT_SCOPE_NOTE,
                (
                    "Connection arrows link a registered funder location to a stated beneficiary "
                    "country. They are illustrative associations, not verified financial routes."
                ),
            ],
            "filters_applied": applied_filters,
        }

        aggregates: Dict[str, Dict[str, Any]] = {}
        connection_aggregates: Dict[tuple[str, str], Dict[str, Any]] = {}
        known_count = 0
        grant_country_associations = 0
        multi_country_count = 0
        excluded_multi_country_count = 0
        excluded_multi_country_minor_units = 0
        excluded_currency_count = 0
        excluded_invalid_amount_count = 0
        connection_grant_ids = set()
        connection_no_headquarters_grant_ids = set()
        connection_same_country_grant_ids = set()

        for row, countries in prepared_rows:
            (
                grant_id,
                amount,
                row_currency,
                _normalized_locations,
                _source_locations,
                funding_name,
                recipient_name,
                programme_source,
                programme_inferred,
                programme_scores,
                _funding_charity_id,
                raw_grant_data,
                _directory_name,
                headquarters_country,
                _headquarters_region,
                _annual_expenditure,
                _organization_programme_source,
                _organization_programme_inferred,
            ) = row
            if not countries:
                continue
            known_count += 1
            grant_country_associations += len(countries)
            multi_country = len(countries) > 1
            if multi_country:
                multi_country_count += 1
            categories = _accepted_programme_categories(
                programme_source, programme_inferred, programme_scores
            )
            row_currency_code = str(row_currency or "").strip().upper()
            amount_status, minor_units = _money_minor_units(amount)

            if selected_currency:
                if row_currency_code != selected_currency:
                    excluded_currency_count += 1
                elif multi_country and amount_status in {"valid", "zero"}:
                    excluded_multi_country_count += 1
                    excluded_multi_country_minor_units += minor_units or 0
                elif not multi_country and amount_status not in {"valid", "zero"}:
                    excluded_invalid_amount_count += 1

            origin, origin_source = _funder_headquarters_country(
                raw_grant_data, headquarters_country
            )
            if not origin:
                connection_no_headquarters_grant_ids.add(grant_id)

            for country in countries:
                code = country["country_code"]
                current = aggregates.setdefault(code, {
                    "country_name": country["country_name"],
                    "grant_ids": set(),
                    "funders": Counter(),
                    "recipients": Counter(),
                    "programme_areas": Counter(),
                    "original_geographies": Counter(),
                    "total_minor_units": 0,
                    "funding_grant_ids": set(),
                    "excluded_multi_country_grant_ids": set(),
                    "excluded_invalid_amount_grant_ids": set(),
                })
                current["grant_ids"].add(grant_id)
                if str(funding_name or "").strip():
                    current["funders"][str(funding_name).strip()] += 1
                if str(recipient_name or "").strip():
                    current["recipients"][str(recipient_name).strip()] += 1
                for category in categories:
                    current["programme_areas"][category] += 1
                for original in country["original_geographies"]:
                    current["original_geographies"][original] += 1

                if origin:
                    if origin["country_code"] == code:
                        connection_same_country_grant_ids.add(grant_id)
                    else:
                        key = (origin["country_code"], code)
                        connection = connection_aggregates.setdefault(key, {
                            "origin_country_name": origin["country_name"],
                            "destination_country_name": country["country_name"],
                            "grant_ids": set(),
                            "funders": Counter(),
                            "origin_sources": set(),
                        })
                        connection["grant_ids"].add(grant_id)
                        connection_grant_ids.add(grant_id)
                        if str(funding_name or "").strip():
                            connection["funders"][str(funding_name).strip()] += 1
                        if origin_source:
                            connection["origin_sources"].add(origin_source)

                if not selected_currency or row_currency_code != selected_currency:
                    continue
                if multi_country:
                    current["excluded_multi_country_grant_ids"].add(grant_id)
                elif amount_status in {"valid", "zero"}:
                    current["funding_grant_ids"].add(grant_id)
                    current["total_minor_units"] += minor_units or 0
                else:
                    current["excluded_invalid_amount_grant_ids"].add(grant_id)

        total_selected = len(prepared_rows)
        unknown_count = total_selected - known_count
        coverage = known_count / total_selected if total_selected else 0.0
        base_metadata["coverage"] = coverage
        if multi_country_count:
            base_metadata["limitations"].append(
                f"{multi_country_count} grants are associated with more than one country. "
                "They count once in each associated country, but their amounts are not "
                "allocated to countries."
            )
        if selected_currency and excluded_invalid_amount_count:
            base_metadata["limitations"].append(
                f"{excluded_invalid_amount_count} single-country grants in {selected_currency} "
                "had missing, invalid, or negative amounts and are excluded from funding totals."
            )
        if not selected_currency and len(currencies) > 1:
            base_metadata["limitations"].append(
                "Multiple currencies are present. Country counts remain available, but a currency "
                "must be selected before awarded funding can be shown."
            )
        if requested_currency and requested_currency not in currencies:
            base_metadata["limitations"].append(
                f"{requested_currency} is not available in the filtered grant records."
            )

        status_value = "available"
        items = [
            {
                "region_or_country_code": code,
                "region_or_country_name": values["country_name"],
                "grant_count": len(values["grant_ids"]),
                "total_amount": (
                    _minor_units_to_amount(values["total_minor_units"])
                    if selected_currency and values["funding_grant_ids"] else None
                ),
                "currency": selected_currency,
                "distinct_funders": len(values["funders"]),
                "distinct_recipients": len(values["recipients"]),
                "top_programme_areas": _top_counter_items(values["programme_areas"]),
                "top_funders": _top_counter_items(values["funders"]),
                "top_recipients": _top_counter_items(values["recipients"]),
                "original_geographies": [
                    item["name"]
                    for item in _top_counter_items(values["original_geographies"], limit=8)
                ],
                "funding_grant_count": len(values["funding_grant_ids"]),
                "excluded_multi_country_grant_count": len(
                    values["excluded_multi_country_grant_ids"]
                ),
                "excluded_invalid_amount_grant_count": len(
                    values["excluded_invalid_amount_grant_ids"]
                ),
            }
            for code, values in aggregates.items()
        ]
        items.sort(key=lambda item: (-item["grant_count"], item["region_or_country_name"]))
        connections = [
            {
                "origin_country_code": origin_code,
                "origin_country_name": values["origin_country_name"],
                "destination_country_code": destination_code,
                "destination_country_name": values["destination_country_name"],
                "grant_count": len(values["grant_ids"]),
                "top_funders": _top_counter_items(values["funders"]),
                "origin_sources": sorted(values["origin_sources"]),
            }
            for (origin_code, destination_code), values in connection_aggregates.items()
        ]
        connections.sort(key=lambda item: (
            -item["grant_count"], item["origin_country_name"], item["destination_country_name"]
        ))
        if not total_selected:
            status_value = "no_data"
            funding_status = "no_data"
        elif not known_count:
            status_value = "no_geography"
        elif coverage < min_coverage:
            status_value = "low_coverage"
            items = []
            connections = []
            base_metadata["limitations"].append(
                "Coverage is below the configured display threshold; aggregation is withheld."
            )

        return {
            "status": status_value,
            "geographic_dimension": "beneficiary_location",
            "items": items,
            "known_geography_count": known_count,
            "unknown_geography_count": unknown_count,
            "coverage_percentage": round(coverage * 100, 2),
            "currencies": currencies,
            "selected_currency": selected_currency,
            "funding_status": funding_status,
            "funding_mode_available": bool(
                selected_currency and any(item["funding_grant_count"] for item in items)
            ),
            "grant_country_association_count": grant_country_associations,
            "multi_country_grant_count": multi_country_count,
            "funding_excluded_multi_country_count": excluded_multi_country_count,
            "funding_excluded_multi_country_amount": _minor_units_to_amount(
                excluded_multi_country_minor_units
            ),
            "funding_excluded_currency_count": excluded_currency_count,
            "funding_excluded_invalid_amount_count": excluded_invalid_amount_count,
            "connections": connections,
            "connection_grant_count": len(connection_grant_ids),
            "connection_excluded_no_headquarters_count": len(
                connection_no_headquarters_grant_ids
            ),
            "connection_same_country_count": len(connection_same_country_grant_ids),
            "minimum_coverage_threshold": min_coverage,
            "metadata": base_metadata,
        }

    async def get_grant_summary(self) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM grants")
        total_count = cursor.fetchone()[0]
        cursor.execute("SELECT DISTINCT currency FROM grants WHERE currency IS NOT NULL AND currency != ''")
        currencies = sorted(row[0].upper() for row in cursor.fetchall())
        cursor.execute("""
            SELECT funding_charity_id, COALESCE(NULLIF(funding_name, ''), 'Unknown donor'),
                   currency, SUM(amount), COUNT(*)
            FROM grants
            WHERE amount > 0 AND currency IS NOT NULL
            GROUP BY funding_charity_id, funding_name, currency
            ORDER BY SUM(amount) DESC LIMIT 10
        """)
        donors = [
            {
                "organization_id": row[0], "organization_name": row[1],
                "currency": row[2], "total_amount": round(row[3], 2), "grant_count": row[4]
            }
            for row in cursor.fetchall()
        ]
        cursor.execute("""
            SELECT recipient_charity_id, COALESCE(NULLIF(recipient_name, ''), 'Unknown recipient'),
                   currency, SUM(amount), COUNT(*)
            FROM grants
            WHERE amount > 0 AND currency IS NOT NULL
            GROUP BY recipient_charity_id, recipient_name, currency
            ORDER BY SUM(amount) DESC LIMIT 10
        """)
        recipients = [
            {
                "organization_id": row[0], "organization_name": row[1],
                "currency": row[2], "total_amount": round(row[3], 2), "grant_count": row[4]
            }
            for row in cursor.fetchall()
        ]
        conn.close()
        return {
            "status": "available" if total_count else "no_data",
            "total_grant_count": total_count,
            "currencies": currencies,
            "largest_donors": donors,
            "largest_recipients": recipients,
            "metadata": {
                "data_mode": "derived_from_cached_source",
                "source": ["360Giving"],
                "generated_at": _utc_now(),
                "record_count": total_count,
                "derivation": "Currency-separated sums of stored source grant amounts.",
                "limitations": ["Rankings do not combine values across currencies."],
            },
        }

    def _grant_aggregation_context(self, conn, currency: Optional[str]):
        """Resolve cached 360Giving source/currency scope shared by overview charts."""
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM grants")
        total_records = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM grants WHERE source = ?", ("360Giving",))
        source_records = cursor.fetchone()[0]
        cursor.execute("""
            SELECT DISTINCT UPPER(TRIM(currency))
            FROM grants
            WHERE source = ?
              AND currency IS NOT NULL
              AND LENGTH(TRIM(currency)) = 3
              AND TRIM(currency) NOT GLOB '*[^A-Za-z]*'
            ORDER BY UPPER(TRIM(currency))
        """, ("360Giving",))
        available_currencies = [row[0] for row in cursor.fetchall()]
        cursor.execute("""
            SELECT COUNT(*) FROM grants
            WHERE source = ? AND (
                currency IS NULL OR TRIM(CAST(currency AS TEXT)) = ''
                OR LENGTH(TRIM(CAST(currency AS TEXT))) != 3
                OR TRIM(CAST(currency AS TEXT)) GLOB '*[^A-Za-z]*'
            )
        """, ("360Giving",))
        unsupported_currency = cursor.fetchone()[0]
        cursor.execute(
            "SELECT MAX(ingestion_timestamp) FROM grants WHERE source = ?",
            ("360Giving",),
        )
        last_refreshed_at = cursor.fetchone()[0]

        requested = str(currency or "").strip().upper() or None
        selected = requested
        status = None
        if not source_records:
            status = "no_data"
            selected = requested
        elif requested and requested not in available_currencies:
            status = "unsupported_currency"
        elif not requested and len(available_currencies) == 1:
            selected = available_currencies[0]
        elif not requested and len(available_currencies) > 1:
            status = "currency_selection_required"
        elif not available_currencies:
            status = "no_qualifying_records"

        currency_filtered = 0
        if selected and selected in available_currencies:
            cursor.execute("""
                SELECT COUNT(*) FROM grants
                WHERE source = ?
                  AND currency IS NOT NULL
                  AND LENGTH(TRIM(currency)) = 3
                  AND TRIM(currency) NOT GLOB '*[^A-Za-z]*'
                  AND UPPER(TRIM(currency)) != ?
            """, ("360Giving", selected))
            currency_filtered = cursor.fetchone()[0]

        return {
            "status": status,
            "selected_currency": selected,
            "available_currencies": available_currencies,
            "last_refreshed_at": last_refreshed_at,
            "source_records": source_records,
            "excluded": {
                "unsupported_currency": unsupported_currency,
                "currency_filtered": currency_filtered,
                "unsupported_source": total_records - source_records,
            },
        }

    @staticmethod
    def _empty_grant_trends(context, months):
        return {
            "status": context["status"] or "no_qualifying_records",
            "currency": context["selected_currency"],
            "available_currencies": context["available_currencies"],
            "date_basis": "award_date",
            "period": None,
            "items": [],
            "excluded": context["excluded"],
            "zero_amount_count": 0,
            "latest_award_date": None,
            "last_refreshed_at": context["last_refreshed_at"],
            "source": ["360Giving"] if context["source_records"] else [],
            "data_mode": "derived_from_cached_source",
            "amount_policy": _amount_policy(),
            "scope": {"coverage_note": GRANT_SCOPE_NOTE},
        }

    async def get_grant_trends(
        self, currency: Optional[str] = None, months: int = 24
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        context = self._grant_aggregation_context(conn, currency)
        selected = context["selected_currency"]
        if context["status"] or not selected:
            conn.close()
            return self._empty_grant_trends(context, months)

        cursor = conn.cursor()
        base_params = ("360Giving", selected)
        base_filter = "source = ? AND UPPER(TRIM(currency)) = ?"
        cursor.execute(f"""
            SELECT
                SUM(CASE WHEN date IS NULL OR TRIM(CAST(date AS TEXT)) = '' THEN 1 ELSE 0 END),
                SUM(CASE WHEN date IS NOT NULL AND TRIM(CAST(date AS TEXT)) != ''
                              AND NOT ({STRICT_GRANT_DATE_SQL}) THEN 1 ELSE 0 END),
                SUM(CASE WHEN amount IS NULL OR (TYPEOF(amount) = 'text' AND TRIM(amount) = '')
                         THEN 1 ELSE 0 END),
                SUM(CASE WHEN amount IS NOT NULL
                              AND NOT (TYPEOF(amount) = 'text' AND TRIM(amount) = '')
                              AND TYPEOF(amount) NOT IN ('integer', 'real') THEN 1 ELSE 0 END),
                SUM(CASE WHEN TYPEOF(amount) IN ('integer', 'real') AND amount < 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN TYPEOF(amount) IN ('integer', 'real') AND amount = 0 THEN 1 ELSE 0 END),
                MAX(CASE WHEN TYPEOF(amount) IN ('integer', 'real') AND amount >= 0
                         THEN CAST(ROUND(amount * 100, 0) AS INTEGER) END),
                MAX(CASE WHEN {STRICT_GRANT_DATE_SQL} THEN DATE(date) END)
            FROM grants WHERE {base_filter}
        """, base_params)
        quality = cursor.fetchone()
        excluded = {
            **context["excluded"],
            "missing_date": quality[0] or 0,
            "invalid_date": quality[1] or 0,
            "missing_amount": quality[2] or 0,
            "invalid_amount": quality[3] or 0,
            "negative_amount": quality[4] or 0,
        }
        zero_amount_count = quality[5] or 0
        maximum_minor_units = quality[6]
        latest_award_date = quality[7]
        if not latest_award_date:
            conn.close()
            response = self._empty_grant_trends(context, months)
            response["excluded"] = excluded
            response["zero_amount_count"] = zero_amount_count
            response["amount_policy"] = _amount_policy(maximum_minor_units)
            return response

        anchor_month = latest_award_date[:7]
        from_month = _month_offset(anchor_month, -(months - 1))
        calendar_months = _month_range(from_month, months)

        cursor.execute(f"""
            SELECT STRFTIME('%Y-%m', date), COUNT(*)
            FROM grants
            WHERE {base_filter} AND {STRICT_GRANT_DATE_SQL}
              AND STRFTIME('%Y-%m', date) BETWEEN ? AND ?
            GROUP BY STRFTIME('%Y-%m', date)
        """, (*base_params, from_month, anchor_month))
        source_records_by_month = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute(f"""
            SELECT STRFTIME('%Y-%m', date), COUNT(*),
                   SUM(CAST(ROUND(amount * 100, 0) AS INTEGER))
            FROM grants
            WHERE {base_filter} AND {STRICT_GRANT_DATE_SQL}
              AND TYPEOF(amount) IN ('integer', 'real') AND amount >= 0
              AND STRFTIME('%Y-%m', date) BETWEEN ? AND ?
            GROUP BY STRFTIME('%Y-%m', date)
            ORDER BY STRFTIME('%Y-%m', date)
        """, (*base_params, from_month, anchor_month))
        awards_by_month = {
            row[0]: {"grant_count": row[1], "minor_units": row[2] or 0}
            for row in cursor.fetchall()
        }
        cursor.execute(f"""
            SELECT COUNT(*) FROM grants
            WHERE {base_filter} AND {STRICT_GRANT_DATE_SQL}
              AND TYPEOF(amount) IN ('integer', 'real') AND amount >= 0
              AND STRFTIME('%Y-%m', date) NOT BETWEEN ? AND ?
        """, (*base_params, from_month, anchor_month))
        excluded["outside_period"] = cursor.fetchone()[0]
        conn.close()

        items = []
        for month in calendar_months:
            award = awards_by_month.get(month)
            source_record_count = source_records_by_month.get(month, 0)
            if award:
                items.append({
                    "month": month,
                    "grant_count": award["grant_count"],
                    "source_record_count": source_record_count,
                    "total_amount": _minor_units_to_amount(award["minor_units"]),
                    "coverage_status": "observed",
                })
            elif source_record_count:
                items.append({
                    "month": month,
                    "grant_count": None,
                    "source_record_count": source_record_count,
                    "total_amount": None,
                    "coverage_status": "partial",
                })
            else:
                items.append({
                    "month": month,
                    "grant_count": None,
                    "source_record_count": 0,
                    "total_amount": None,
                    "coverage_status": "unknown",
                })

        return {
            "status": "available" if awards_by_month else "no_qualifying_records",
            "currency": selected,
            "available_currencies": context["available_currencies"],
            "date_basis": "award_date",
            "period": {
                "from": from_month,
                "to": anchor_month,
                "months": months,
                "anchor": "latest_available_award_month",
            },
            "items": items,
            "excluded": excluded,
            "zero_amount_count": zero_amount_count,
            "latest_award_date": latest_award_date,
            "last_refreshed_at": context["last_refreshed_at"],
            "source": ["360Giving"],
            "data_mode": "derived_from_cached_source",
            "amount_policy": _amount_policy(maximum_minor_units),
            "scope": {"coverage_note": GRANT_SCOPE_NOTE},
        }

    async def get_grant_themes(
        self, currency: Optional[str] = None
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        context = self._grant_aggregation_context(conn, currency)
        selected = context["selected_currency"]
        base_response = {
            "status": context["status"] or "no_qualifying_records",
            "currency": selected,
            "available_currencies": context["available_currencies"],
            "allocation_method": "equal_split_across_available_categories",
            "classification_precedence": [
                "valid_source_category", "accepted_inferred_category", "unclassified"
            ],
            "inference_confidence_threshold": DEFAULT_REVIEW_THRESHOLD,
            "items": [],
            "classification_coverage": _empty_classification_coverage(),
            "qualifying_amount": 0.0,
            "allocated_amount": 0.0,
            "excluded": context["excluded"],
            "zero_amount_count": 0,
            "last_refreshed_at": context["last_refreshed_at"],
            "source": ["360Giving"] if context["source_records"] else [],
            "data_mode": "derived_from_cached_source",
            "amount_policy": _amount_policy(),
            "scope": {"coverage_note": GRANT_SCOPE_NOTE},
        }
        if context["status"] or not selected:
            conn.close()
            return base_response

        cursor = conn.cursor()
        cursor.execute("""
            SELECT grant_id, amount, programme_area_source, programme_area_inferred,
                   programme_area_scores
            FROM grants
            WHERE source = ? AND UPPER(TRIM(currency)) = ?
            ORDER BY grant_id
        """, ("360Giving", selected))

        aggregates = {}
        qualifying_grants = 0
        classified_grants = 0
        unclassified_grants = 0
        source_classified = 0
        inferred_classified = 0
        multiple_categories = 0
        invalid_source_labels = 0
        low_confidence_inferences = 0
        qualifying_minor_units = 0
        maximum_minor_units = None
        zero_amount_count = 0
        amount_exclusions = {"missing_amount": 0, "invalid_amount": 0, "negative_amount": 0}

        for _, amount, source_raw, inferred_raw, scores_raw in cursor:
            amount_status, minor_units = _money_minor_units(amount)
            if amount_status == "missing":
                amount_exclusions["missing_amount"] += 1
                continue
            if amount_status == "invalid":
                amount_exclusions["invalid_amount"] += 1
                continue
            if amount_status == "negative":
                amount_exclusions["negative_amount"] += 1
                continue
            if amount_status == "zero":
                zero_amount_count += 1

            qualifying_grants += 1
            qualifying_minor_units += minor_units
            maximum_minor_units = (
                minor_units if maximum_minor_units is None
                else max(maximum_minor_units, minor_units)
            )
            source_values = _json_list(source_raw)
            source_categories, _ = normalize_programme_sources(source_values)
            if source_values and not source_categories:
                invalid_source_labels += 1

            inferred_values = _json_list(inferred_raw)
            scores = _json_dict(scores_raw)
            accepted_inferred = []
            inferred_candidates = []
            for category in inferred_values:
                if category not in PROGRAMME_TAXONOMY:
                    continue
                inferred_candidates.append(category)
                try:
                    confidence = float(scores.get(category, 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence >= DEFAULT_REVIEW_THRESHOLD:
                    accepted_inferred.append(category)

            if source_categories:
                categories = sorted(set(source_categories))
                provenance = "source"
                source_classified += 1
                classified_grants += 1
            elif accepted_inferred:
                categories = sorted(set(accepted_inferred))
                provenance = "inferred"
                inferred_classified += 1
                classified_grants += 1
            else:
                categories = ["Unclassified"]
                provenance = "unclassified"
                unclassified_grants += 1
                if inferred_candidates:
                    low_confidence_inferences += 1

            if len(categories) > 1:
                multiple_categories += 1
            base_share, remainder = divmod(minor_units, len(categories))
            weight = Decimal(1) / Decimal(len(categories))
            for index, category in enumerate(categories):
                share = base_share + (1 if index < remainder else 0)
                current = aggregates.setdefault(category, {
                    "distinct_grant_count": 0,
                    "weighted_grant_count": Decimal(0),
                    "allocated_minor_units": 0,
                    "source_classified_grant_count": 0,
                    "inferred_classified_grant_count": 0,
                    "unclassified_grant_count": 0,
                })
                current["distinct_grant_count"] += 1
                current["weighted_grant_count"] += weight
                current["allocated_minor_units"] += share
                if provenance == "source":
                    current["source_classified_grant_count"] += 1
                elif provenance == "inferred":
                    current["inferred_classified_grant_count"] += 1
                else:
                    current["unclassified_grant_count"] += 1

        conn.close()
        allocated_minor_units = sum(
            values["allocated_minor_units"] for values in aggregates.values()
        )
        items = [
            {
                "programme_area": category,
                "distinct_grant_count": values["distinct_grant_count"],
                "weighted_grant_count": float(
                    values["weighted_grant_count"].quantize(
                        Decimal("0.000001"), rounding=ROUND_HALF_UP
                    )
                ),
                "allocated_amount": _minor_units_to_amount(values["allocated_minor_units"]),
                "source_classified_grant_count": values["source_classified_grant_count"],
                "inferred_classified_grant_count": values["inferred_classified_grant_count"],
                "unclassified_grant_count": values["unclassified_grant_count"],
            }
            for category, values in aggregates.items()
        ]
        items.sort(key=lambda item: (-item["allocated_amount"], item["programme_area"]))
        denominator = qualifying_grants or 1
        coverage = {
            "qualifying_grant_count": qualifying_grants,
            "classified_grant_count": classified_grants,
            "unclassified_grant_count": unclassified_grants,
            "classified_percentage": round(classified_grants / denominator * 100, 2),
            "source_classified_grant_count": source_classified,
            "inferred_classified_grant_count": inferred_classified,
            "source_percentage": round(source_classified / denominator * 100, 2),
            "inferred_percentage": round(inferred_classified / denominator * 100, 2),
            "multiple_programme_area_grant_count": multiple_categories,
            "invalid_source_label_count": invalid_source_labels,
            "low_confidence_inference_count": low_confidence_inferences,
        }
        return {
            **base_response,
            "status": "available" if qualifying_grants else "no_qualifying_records",
            "items": items,
            "classification_coverage": coverage,
            "qualifying_amount": _minor_units_to_amount(qualifying_minor_units),
            "allocated_amount": _minor_units_to_amount(allocated_minor_units),
            "excluded": {**context["excluded"], **amount_exclusions},
            "zero_amount_count": zero_amount_count,
            "amount_policy": _amount_policy(maximum_minor_units),
        }

    async def get_grants_for_charity(self, charity_id: int, role: str = "all") -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transaction_coverage, source_names FROM charities WHERE charity_id = ?",
            (charity_id,),
        )
        organization_row = cursor.fetchone()
        
        query = """
            SELECT grant_id, funding_charity_id, funding_name, funding_org_source_id,
                   recipient_name, recipient_charity_id, recipient_org_source_id,
                   amount, amount_eur, currency, description, date, recipient_region,
                   beneficiary_geography, tags, source, source_record_id, source_url,
                   programme_area_source, programme_area_inferred, programme_area_scores,
                   programme_area_method, programme_area_evidence,
                   programme_area_review_required, beneficiary_geography_normalized,
                   geographic_focus_inferred, geography_method, geography_confidence,
                   geography_evidence, geography_review_required, enrichment_rule_version
            FROM grants
            WHERE 1=1
        """
        params = []
        role_lower = role.lower()
        if role_lower == "funder":
            query += " AND funding_charity_id = ?"
            params.append(charity_id)
        elif role_lower == "recipient":
            query += " AND recipient_charity_id = ?"
            params.append(charity_id)
        else:
            query += " AND (funding_charity_id = ? OR recipient_charity_id = ?)"
            params.extend([charity_id, charity_id])
            
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            results.append({
                "grant_id": r[0],
                "funding_charity_id": r[1],
                "funding_name": r[2],
                "funding_org_source_id": r[3],
                "recipient_name": r[4],
                "recipient_charity_id": r[5],
                "recipient_org_source_id": r[6],
                "amount": r[7],
                "amount_eur": r[8],
                "currency": r[9] or "UNKNOWN",
                "description": r[10] or "",
                "date": r[11] or "",
                "recipient_region": r[12],
                "beneficiary_geography": _json_list(r[13]),
                "tags": _json_list(r[14]),
                "source": r[15],
                "source_record_id": r[16],
                "source_url": r[17],
                "programme_area_source": _json_list(r[18]),
                "programme_area_inferred": _json_list(r[19]),
                "programme_area_scores": json.loads(r[20]) if r[20] else {},
                "programme_area_method": r[21],
                "programme_area_evidence": _json_list(r[22]),
                "programme_area_review_required": bool(r[23]),
                "beneficiary_geography_normalized": _json_list(r[24]),
                "geographic_focus_inferred": _json_list(r[25]),
                "geography_method": r[26],
                "geography_confidence": r[27],
                "geography_evidence": _json_list(r[28]),
                "geography_review_required": bool(r[29]),
                "enrichment_rule_version": r[30],
            })
        currencies = sorted({item["currency"] for item in results})
        stored_coverage = organization_row[0] if organization_row else "unknown"
        source_names = _json_list(organization_row[1]) if organization_row else []
        if results:
            response_status = "available"
            coverage_status = "observed_transactions"
        elif stored_coverage == "organization_level_only":
            response_status = "organization_level_only"
            coverage_status = "organization_level_only"
        else:
            response_status = "no_transactions_found"
            coverage_status = "no_transactions_found"
        return {
            "status": response_status,
            "organization_id": charity_id,
            "role": role_lower if role_lower in {"funder", "recipient"} else "all",
            "transaction_coverage": coverage_status,
            "grant_count": len(results),
            "currencies": currencies,
            "grants": results,
            "metadata": {
                "data_mode": "cached_source",
                "source": sorted({item["source"] for item in results if item["source"]}) or source_names,
                "generated_at": _utc_now(),
                "record_count": len(results),
                "limitations": ["Absence of a transaction does not prove that no grant exists."],
            },
        }

    async def get_sankey_data(
        self, charity_id: int, currency: Optional[str] = None, limit: int = 30
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transaction_coverage, source_names FROM charities WHERE charity_id = ?",
            (charity_id,),
        )
        organization_row = cursor.fetchone()
        cursor.execute("""
            SELECT grant_id, funding_charity_id, funding_name, funding_org_source_id,
                   recipient_charity_id, recipient_name, recipient_org_source_id,
                   amount, currency, source
            FROM grants
            WHERE funding_charity_id = ? OR recipient_charity_id = ?
        """, (charity_id, charity_id))
        rows = cursor.fetchall()
        conn.close()

        if not rows and organization_row and organization_row[0] == "organization_level_only":
            return {
                "status": "organization_level_only",
                "nodes": [],
                "links": [],
                "metadata": {
                    "source": _json_list(organization_row[1]), "generated_at": _utc_now(),
                    "grant_count": 0, "included_grant_count": 0,
                    "excluded_grant_count": 0, "excluded_reasons": {},
                    "included_value": 0.0, "currencies": [],
                    "selected_currency": currency, "conversion_method": "none",
                    "filters_applied": {"organization_id": charity_id, "limit": limit},
                    "truncation_applied": False,
                },
            }

        currencies = sorted({str(row[8]).upper() for row in rows if row[8]})
        transaction_sources = sorted({str(row[9]) for row in rows if row[9]})
        selected_currency = currency.upper() if currency else (currencies[0] if len(currencies) == 1 else None)
        excluded_reasons = {}
        if rows and selected_currency is None:
            return {
                "status": "mixed_currency_requires_filter",
                "nodes": [],
                "links": [],
                "metadata": {
                    "source": transaction_sources, "generated_at": _utc_now(),
                    "grant_count": len(rows), "included_grant_count": 0,
                    "excluded_grant_count": len(rows),
                    "excluded_reasons": {"mixed_currency_requires_filter": len(rows)},
                    "included_value": 0.0, "currencies": currencies,
                    "selected_currency": None, "conversion_method": "none",
                    "filters_applied": {"organization_id": charity_id, "limit": limit},
                    "truncation_applied": False,
                },
            }

        nodes_by_id = {}
        aggregated = {}
        for row in rows:
            (_, donor_id, donor_name, donor_source_id, recipient_id, recipient_name,
             recipient_source_id, amount, row_currency, row_source) = row
            normalized_currency = str(row_currency or "").upper()
            if selected_currency and normalized_currency != selected_currency:
                excluded_reasons["currency_filtered"] = excluded_reasons.get("currency_filtered", 0) + 1
                continue
            if amount is None:
                excluded_reasons["missing_amount"] = excluded_reasons.get("missing_amount", 0) + 1
                continue
            if amount <= 0:
                excluded_reasons["non_positive_amount"] = excluded_reasons.get("non_positive_amount", 0) + 1
                continue
            source_id = _stable_party_id(
                "donor", donor_id, donor_source_id, donor_name, source=row_source or "360Giving"
            )
            target_id = _stable_party_id(
                "recipient", recipient_id, recipient_source_id, recipient_name,
                source=row_source or "360Giving",
            )
            if source_id == target_id:
                excluded_reasons["self_link"] = excluded_reasons.get("self_link", 0) + 1
                continue
            nodes_by_id.setdefault(source_id, {
                "id": source_id, "label": donor_name or "Unnamed donor", "role": "donor"
            })
            nodes_by_id.setdefault(target_id, {
                "id": target_id, "label": recipient_name or "Unnamed recipient", "role": "recipient"
            })
            key = (source_id, target_id, normalized_currency)
            aggregate = aggregated.setdefault(key, {"value": 0.0, "grant_count": 0})
            aggregate["value"] += float(amount)
            aggregate["grant_count"] += 1

        links = [
            {
                "source": source, "target": target, "currency": row_currency,
                "value": round(values["value"], 2), "grant_count": values["grant_count"]
            }
            for (source, target, row_currency), values in aggregated.items()
        ]
        links.sort(key=lambda item: item["value"], reverse=True)
        truncation_applied = len(links) > limit
        if truncation_applied:
            removed = links[limit:]
            excluded_reasons["truncated"] = sum(item["grant_count"] for item in removed)
            links = links[:limit]
        retained_node_ids = {item["source"] for item in links} | {item["target"] for item in links}
        nodes = [nodes_by_id[node_id] for node_id in sorted(retained_node_ids)]
        retained_grants = sum(item["grant_count"] for item in links)
        excluded_count = len(rows) - retained_grants
        return {
            "status": "available" if links else "no_transactions_found",
            "nodes": nodes,
            "links": links,
            "metadata": {
                "source": transaction_sources,
                "generated_at": _utc_now(),
                "grant_count": len(rows),
                "included_grant_count": retained_grants,
                "excluded_grant_count": excluded_count,
                "excluded_reasons": excluded_reasons,
                "included_value": round(sum(item["value"] for item in links), 2),
                "currencies": currencies,
                "selected_currency": selected_currency,
                "conversion_method": "none",
                "filters_applied": {"organization_id": charity_id, "limit": limit},
                "truncation_applied": truncation_applied,
            },
        }

    async def get_score(
        self, charity_id: int, target_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        organization = await self.get_by_id(charity_id)
        if not organization:
            raise KeyError(charity_id)
        config = load_score_configuration()
        profile = target_profile or config.example_target_profile
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT annual_expenditure, organization_type FROM charities WHERE charity_id = ?",
            (charity_id,),
        )
        row = cursor.fetchone()
        requested_currency = str(profile.get("currency") or "").upper()
        cursor.execute("""
            SELECT UPPER(currency), AVG(amount), COUNT(*)
            FROM grants
            WHERE funding_charity_id = ? AND amount > 0 AND currency IS NOT NULL
            GROUP BY UPPER(currency)
        """, (charity_id,))
        grant_rows = cursor.fetchall()
        conn.close()
        selected = None
        if requested_currency:
            selected = next((item for item in grant_rows if item[0] == requested_currency), None)
        elif len(grant_rows) == 1:
            selected = grant_rows[0]
        score_input = {
            **organization,
            "annual_expenditure": row[0] if row else None,
            "organization_type": row[1] if row else organization.get("organization_type"),
        }
        grant_statistics = {
            "currency": selected[0],
            "average_amount": selected[1],
            "grant_count": selected[2],
        } if selected else {}
        return score_relevance(
            score_input,
            profile,
            grant_statistics=grant_statistics,
            configuration=config,
        )


def get_charity_repository() -> CharityRepository:
    """Prefer a structurally valid SQLite DB and otherwise fall back safely to JSON."""
    is_valid, reason = validate_database(DB_PATH)
    if is_valid:
        return SQLiteCharityRepository()
    if os.path.exists(DB_PATH):
        logger.warning(f"Ignoring unusable SQLite database at {DB_PATH}: {reason}")
    return JSONCharityRepository()
