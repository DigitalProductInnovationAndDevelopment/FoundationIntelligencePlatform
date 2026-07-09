from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


SRC_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CHARITY_COMMISSION_PATH = SRC_DIR / "data" / "raw" / "register_of_charities_results.json"
DEFAULT_CHARITY_COMMISSION_DATABASE = (
    SRC_DIR / "data" / "processed" / "charity_commission_register.sqlite3"
)
SOURCE_LABEL = "Charity Commission API"


def _is_present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        for key in ("data", "results", "records", "items", "values", "history"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
        return [value]
    return [value]


def _first(sources: list[dict[str, Any]], *keys: str, default: Any = None) -> Any:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if _is_present(value):
                return value
    return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(item for item in (_text(entry) for entry in value) if item)
    if isinstance(value, dict):
        return ", ".join(item for item in (_text(entry) for entry in value.values()) if item)
    return str(value).strip()


def _unique(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_records(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return None
    for key in ("records", "results", "data", "charities", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    record_markers = {
        "all_details",
        "charity_details",
        "registered_charity_number",
        "reg_charity_number",
        "organisation_number",
    }
    if record_markers.intersection(payload):
        return [payload]
    return None


def load_charity_commission_cache(
    path: Path = DEFAULT_CHARITY_COMMISSION_PATH,
) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {
            "state": "missing",
            "message": f"Local Charity Commission sample/cache not found at {path}.",
            "records": [],
        }
    except json.JSONDecodeError as exc:
        return {
            "state": "malformed",
            "message": f"Local Charity Commission sample/cache is malformed JSON: {exc.msg}.",
            "records": [],
        }
    except OSError as exc:
        return {
            "state": "unreadable",
            "message": f"Local Charity Commission sample/cache could not be read: {exc}.",
            "records": [],
        }

    records = _extract_records(payload)
    if records is None:
        return {
            "state": "invalid",
            "message": "Local Charity Commission sample/cache does not contain a supported record list.",
            "records": [],
        }
    return {"state": "ok", "message": "", "records": records}


def _nested_items(sources: list[dict[str, Any]], *keys: str) -> list[Any]:
    values: list[Any] = []
    for source in sources:
        for key in keys:
            if key in source:
                values.extend(_as_list(source.get(key)))
    return values


def _classification_values(items: list[Any]) -> dict[str, list[str]]:
    grouped: dict[str, list[Any]] = {"who": [], "what": [], "how": [], "other": []}
    for item in items:
        if isinstance(item, dict):
            classification_type = _text(
                _first([item], "classification_type", "type", "category", default="")
            ).casefold()
            description = _first(
                [item],
                "classification_desc",
                "description",
                "name",
                "value",
                default="",
            )
            if "who" in classification_type:
                grouped["who"].append(description)
            elif "what" in classification_type:
                grouped["what"].append(description)
            elif "how" in classification_type:
                grouped["how"].append(description)
            else:
                grouped["other"].append(description)
            for key in ("who", "what", "how"):
                if _is_present(item.get(key)):
                    grouped[key].append(item[key])
        else:
            grouped["other"].append(item)
    return {key: _unique(values) for key, values in grouped.items()}


def _area_values(sources: list[dict[str, Any]]) -> dict[str, list[str]]:
    country_items = _nested_items(
        sources,
        "CharityAoOCountryContinent",
        "charity_aoo_country_continent",
        "countries",
    )
    region_items = _nested_items(
        sources,
        "CharityAoORegion",
        "charity_aoo_region",
        "regions",
    )
    local_items = _nested_items(
        sources,
        "CharityAoOLocalAuthority",
        "charity_aoo_local_authority",
        "local_authorities",
    )
    explicit_areas = _nested_items(
        sources,
        "area_of_operation",
        "areas_of_operation",
        "CharityAreaOfOperation",
    )

    countries: list[Any] = []
    continents: list[Any] = []
    for item in country_items:
        if isinstance(item, dict):
            countries.append(_first([item], "country", "country_name", "name", default=""))
            continents.append(_first([item], "continent", "continent_name", default=""))
        else:
            countries.append(item)

    regions: list[Any] = []
    for item in region_items:
        regions.append(
            _first([item], "region", "region_name", "name", default="")
            if isinstance(item, dict)
            else item
        )

    local_authorities: list[Any] = []
    for item in local_items:
        local_authorities.append(
            _first([item], "local_authority", "name", default="")
            if isinstance(item, dict)
            else item
        )

    areas: list[Any] = []
    for item in explicit_areas:
        areas.append(
            _first([item], "area_of_operation", "name", "description", default="")
            if isinstance(item, dict)
            else item
        )
    areas.extend(countries)
    areas.extend(regions)
    areas.extend(local_authorities)

    return {
        "countries": _unique(countries),
        "continents": _unique(continents),
        "regions": _unique(regions),
        "local_authorities": _unique(local_authorities),
        "areas_of_operation": _unique(areas),
    }


def _asset_profile(value: Any) -> dict[str, Any]:
    entries = [item for item in _as_list(value) if isinstance(item, dict)]
    latest = entries[0] if entries else {}
    sources = [latest]
    assets = _number(
        _first(sources, "assets", "total_assets", "assets_total", "net_assets", default=None)
    )
    if assets is None and latest:
        components = [
            _number(latest.get("assets_own_use")),
            _number(latest.get("assets_long_term_investment")),
            _number(latest.get("assets_other_assets")),
            _number(latest.get("defined_net_assets_pension")),
        ]
        present_components = [amount for amount in components if amount is not None]
        assets = sum(present_components) if present_components else None
    liabilities = _number(
        _first(
            sources,
            "liabilities",
            "total_liabilities",
            "assets_total_liabilities",
            default=None,
        )
    )
    return {
        "assets": assets,
        "liabilities": liabilities,
        "period_end": _text(
            _first(sources, "fin_period_end_date", "financial_period_end_date", default="")
        ),
        "raw": entries,
    }


def _grant_maker_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        for item in value:
            flag = _grant_maker_flag(item)
            if flag is not None:
                return flag
        return None
    if isinstance(value, dict):
        for key in (
            "primary_purpose_grant_making",
            "primary_grants",
            "is_grant_maker",
            "grant_maker",
        ):
            if key in value:
                return _grant_maker_flag(value[key])
        return None
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "y", "1"}:
            return True
        if normalized in {"false", "no", "n", "0"}:
            return False
    return None


def _financial_history(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "financial_year": _text(
                    _first(
                        [item],
                        "financial_period_end_date",
                        "fin_period_end_date",
                        "reporting_period_year_end",
                        "ar_cycle_reference",
                        "year",
                        default="",
                    )
                ),
                "income": _number(_first([item], "income", "latest_income", default=None)),
                "expenditure": _number(
                    _first([item], "expenditure", "latest_expenditure", default=None)
                ),
                "assets": _number(
                    _first([item], "assets", "total_assets", "net_assets", default=None)
                ),
                "liabilities": _number(
                    _first([item], "liabilities", "total_liabilities", default=None)
                ),
            }
        )
    rows.sort(key=lambda row: row.get("financial_year", ""), reverse=True)
    return rows[:5]


def normalize_charity_commission_record(
    record: dict[str, Any],
    source_file: str = "register_of_charities_results.json",
) -> dict[str, Any]:
    details = _as_dict(
        _first(
            [record],
            "all_details",
            "charity_details",
            "details",
            "allCharityDetails",
            default={},
        )
    )
    sources = [record, details]
    areas = _area_values(sources)
    classification_items = _nested_items(
        sources,
        "who_what_how",
        "who_what_where",
        "classifications",
    )
    classifications = _classification_values(classification_items)
    asset_payload = _first(
        sources,
        "assets_liabilities",
        "assetsAndLiabilities",
        "asset_liability",
        default=[],
    )
    assets = _asset_profile(asset_payload)
    history = _financial_history(
        _first(sources, "financial_history", "financialHistory", default=[])
    )
    primary_grants = _first(
        sources,
        "primary_grants",
        "check_primary_grants",
        "primary_purpose_grant_making",
        default=None,
    )
    grant_maker_flag = _grant_maker_flag(primary_grants)

    registered_number = _text(
        _first(
            sources,
            "registered_charity_number",
            "reg_charity_number",
            "registeredCharityNumber",
            "charity_number",
            default="",
        )
    )
    organisation_number = _text(
        _first(sources, "organisation_number", "organization_number", default="")
    )
    raw_reg_status = _text(_first(sources, "reg_status", "registration_status", default=""))
    reporting_status = _text(_first(sources, "reporting_status", default=""))
    removal_date = _text(_first(sources, "date_of_removal", "removal_date", default=""))
    normalized_status = "Unknown"
    if raw_reg_status.upper() == "R":
        normalized_status = "Active"
    elif raw_reg_status.upper() == "RM" or reporting_status.casefold() == "removed" or removal_date:
        normalized_status = "Removed"
    elif reporting_status:
        normalized_status = reporting_status

    address_parts = [
        _first(sources, "address", "contact_address", default=""),
        *[
            _first(sources, f"address_line_{word}", default="")
            for word in ("one", "two", "three", "four", "five")
        ],
        _first(sources, "address_post_code", "postcode", "postal_code", default=""),
    ]
    address = ", ".join(_unique(address_parts))
    latest_income = _number(_first(sources, "latest_income", default=None))
    latest_expenditure = _number(_first(sources, "latest_expenditure", default=None))
    if latest_income is None and history:
        latest_income = history[0].get("income")
    if latest_expenditure is None and history:
        latest_expenditure = history[0].get("expenditure")

    who = classifications["who"]
    what = classifications["what"]
    how = classifications["how"]
    other_classifications = classifications["other"]
    classification_search = _unique(who + what + how + other_classifications)

    result = {
        "charity_name": _text(
            _first(sources, "charity_name", "name", "official_registered_name", default="Unnamed charity")
        )
        or "Unnamed charity",
        "registered_charity_number": registered_number,
        "organisation_number": organisation_number,
        "suffix": _text(_first(sources, "suffix", "group_subsid_suffix", default="0")),
        "registration_status": normalized_status,
        "registration_status_raw": raw_reg_status,
        "reporting_status": reporting_status,
        "registration_date": _text(
            _first(sources, "date_of_registration", "registration_date", default="")
        ),
        "removal_date": removal_date,
        "removal_reason": _text(_first(sources, "removal_reason", default="")),
        "charity_type": _text(_first(sources, "charity_type", "type", default="")),
        "website": _text(_first(sources, "web", "website", default="")),
        "register_link": _text(_first(sources, "link", "register_link", default="")),
        "email": _text(_first(sources, "email", default="")),
        "phone": _text(_first(sources, "phone", "telephone", default="")),
        "address": address,
        "countries": areas["countries"],
        "continents": areas["continents"],
        "regions": areas["regions"],
        "local_authorities": areas["local_authorities"],
        "areas_of_operation": areas["areas_of_operation"],
        "who_classifications": who,
        "what_classifications": what,
        "how_classifications": how,
        "other_classifications": other_classifications,
        "all_classifications": classification_search,
        "latest_income": latest_income,
        "latest_expenditure": latest_expenditure,
        "latest_financial_year_end": _text(
            _first(sources, "latest_acc_fin_year_end_date", "latest_financial_year_end", default="")
        ),
        "assets": assets["assets"],
        "liabilities": assets["liabilities"],
        "assets_period_end": assets["period_end"],
        "assets_liabilities_raw": assets["raw"],
        "financial_history": history,
        "primary_purpose_grant_making": grant_maker_flag,
        "source_label": SOURCE_LABEL,
        "source_file": source_file,
        "raw_record": record,
    }
    result.update(
        {
            "has_website": bool(result["website"]),
            "has_email": bool(result["email"]),
            "has_phone": bool(result["phone"]),
            "has_address": bool(result["address"]),
            "has_latest_income": result["latest_income"] is not None,
            "has_latest_expenditure": result["latest_expenditure"] is not None,
            "has_financial_history": bool(result["financial_history"]),
            "has_assets_liabilities": result["assets"] is not None
            or result["liabilities"] is not None
            or bool(result["assets_liabilities_raw"]),
            "has_grant_maker_flag": result["primary_purpose_grant_making"] is not None,
            "has_geography": bool(result["areas_of_operation"]),
            "has_classifications": bool(result["all_classifications"]),
        }
    )
    return result


def normalize_charity_commission_records(
    records: list[dict[str, Any]],
    source_file: str = "register_of_charities_results.json",
) -> list[dict[str, Any]]:
    return [normalize_charity_commission_record(record, source_file) for record in records]


def summarize_charity_commission_records(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(records),
        "active": sum(record.get("registration_status") == "Active" for record in records),
        "removed": sum(record.get("registration_status") == "Removed" for record in records),
        "with_website": sum(bool(record.get("has_website")) for record in records),
        "with_email": sum(bool(record.get("has_email")) for record in records),
        "with_phone": sum(bool(record.get("has_phone")) for record in records),
        "with_address": sum(bool(record.get("has_address")) for record in records),
        "with_latest_income": sum(bool(record.get("has_latest_income")) for record in records),
        "with_latest_expenditure": sum(bool(record.get("has_latest_expenditure")) for record in records),
        "with_financial_history": sum(bool(record.get("has_financial_history")) for record in records),
        "with_assets_liabilities": sum(bool(record.get("has_assets_liabilities")) for record in records),
        "with_grant_maker_flag": sum(bool(record.get("has_grant_maker_flag")) for record in records),
        "primary_grant_makers": sum(
            record.get("primary_purpose_grant_making") is True for record in records
        ),
        "with_geography": sum(bool(record.get("has_geography")) for record in records),
        "with_classifications": sum(bool(record.get("has_classifications")) for record in records),
    }


def _matches_boolean_filter(record: dict[str, Any], field: str, selected: str) -> bool:
    if selected == "All":
        return True
    return bool(record.get(field)) is (selected == "Yes")


def filter_charity_commission_records(
    records: list[dict[str, Any]],
    *,
    status: str = "All",
    has_website: str = "All",
    has_email: str = "All",
    has_latest_income: str = "All",
    has_financial_history: str = "All",
    has_grant_maker_flag: str = "All",
    grant_maker_value: str = "All",
    geography: str = "All",
    search: str = "",
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    search_term = search.strip().casefold()
    for record in records:
        if status != "All" and record.get("registration_status") != status:
            continue
        if not _matches_boolean_filter(record, "has_website", has_website):
            continue
        if not _matches_boolean_filter(record, "has_email", has_email):
            continue
        if not _matches_boolean_filter(record, "has_latest_income", has_latest_income):
            continue
        if not _matches_boolean_filter(record, "has_financial_history", has_financial_history):
            continue
        if not _matches_boolean_filter(record, "has_grant_maker_flag", has_grant_maker_flag):
            continue
        if grant_maker_value != "All":
            expected = grant_maker_value == "Yes"
            if record.get("primary_purpose_grant_making") is not expected:
                continue
        if geography != "All":
            geo_values = record.get("areas_of_operation", [])
            if geography not in geo_values:
                continue
        if search_term:
            searchable = " ".join(
                _text(record.get(field))
                for field in (
                    "charity_name",
                    "registered_charity_number",
                    "organisation_number",
                    "areas_of_operation",
                    "all_classifications",
                )
            ).casefold()
            if search_term not in searchable:
                continue
        filtered.append(record)
    return filtered


def normalize_identifier(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    compact = re.sub(r"[\s\-]", "", text)
    if re.fullmatch(r"\d{5,7}", compact):
        return compact.lstrip("0") or "0"
    return text.casefold()


def build_charity_commission_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        for field in ("registered_charity_number", "organisation_number"):
            key = normalize_identifier(record.get(field))
            if key:
                index.setdefault(key, record)
    return index


def find_charity_commission_match(
    organization: dict[str, Any],
    index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    funding_info = _as_dict(organization.get("funding_info"))
    candidates = [
        organization.get("registered_charity_number"),
        organization.get("organisation_number"),
        organization.get("charity_number"),
        funding_info.get("charity_number"),
    ]
    for candidate in candidates:
        key = normalize_identifier(candidate)
        if key and key in index:
            return index[key]
    return None


def charity_commission_database_available(
    path: Path = DEFAULT_CHARITY_COMMISSION_DATABASE,
) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _database_connection(path: Path = DEFAULT_CHARITY_COMMISSION_DATABASE) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _json_string_list(value: Any) -> list[str]:
    if not _is_present(value):
        return []
    if isinstance(value, list):
        return _unique(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return _unique(value.split(","))
        return _unique(_as_list(parsed))
    return _unique(_as_list(value))


def normalize_charity_commission_bulk_row(row: dict[str, Any]) -> dict[str, Any]:
    status = _text(row.get("charity_registration_status"))
    reg_status = "R" if status.casefold() == "registered" else "RM" if status.casefold() == "removed" else status
    countries = _json_string_list(row.get("countries"))
    regions = _json_string_list(row.get("regions"))
    areas = _json_string_list(row.get("areas_of_operation"))
    who = _json_string_list(row.get("who_classifications"))
    what = _json_string_list(row.get("what_classifications"))
    how = _json_string_list(row.get("how_classifications"))
    classifications = [
        *({"classification_type": "Who", "classification_desc": value} for value in who),
        *({"classification_type": "What", "classification_desc": value} for value in what),
        *({"classification_type": "How", "classification_desc": value} for value in how),
    ]
    details = {
        "organisation_number": row.get("organisation_number"),
        "reg_charity_number": row.get("registered_charity_number"),
        "charity_name": row.get("charity_name"),
        "charity_type": row.get("charity_type"),
        "reg_status": reg_status,
        "reporting_status": row.get("charity_reporting_status"),
        "date_of_registration": row.get("date_of_registration"),
        "date_of_removal": row.get("date_of_removal"),
        "latest_acc_fin_year_start_date": row.get("latest_acc_fin_period_start_date"),
        "latest_acc_fin_year_end_date": row.get("latest_acc_fin_period_end_date"),
        "latest_income": row.get("latest_income"),
        "latest_expenditure": row.get("latest_expenditure"),
        "address_line_one": row.get("charity_contact_address1"),
        "address_line_two": row.get("charity_contact_address2"),
        "address_line_three": row.get("charity_contact_address3"),
        "address_line_four": row.get("charity_contact_address4"),
        "address_line_five": row.get("charity_contact_address5"),
        "address_post_code": row.get("charity_contact_postcode"),
        "phone": row.get("charity_contact_phone"),
        "email": row.get("charity_contact_email"),
        "web": row.get("charity_contact_web"),
        "countries": countries,
        "regions": regions,
        "areas_of_operation": areas,
        "who_what_where": classifications,
    }
    raw_record = {
        "registered_charity_number": row.get("registered_charity_number"),
        "link": (
            "https://register-of-charities.charitycommission.gov.uk/charity-details/"
            f"?regid={row.get('registered_charity_number')}&subid={row.get('linked_charity_number') or 0}"
        ),
        "all_details": details,
        "assets_liabilities": [
            {
                "assets": row.get("assets"),
                "liabilities": row.get("liabilities"),
                "fin_period_end_date": row.get("latest_acc_fin_period_end_date"),
            }
        ]
        if row.get("assets") is not None or row.get("liabilities") is not None
        else [],
        "primary_grants": {
            "primary_purpose_grant_making": row.get("primary_purpose_grant_making")
        },
        "financial_history": row.get("financial_history", []),
    }
    record = normalize_charity_commission_record(
        raw_record,
        source_file="Official daily public register extract",
    )
    record["has_financial_history"] = bool(row.get("has_financial_history"))
    record.update(
        {
            "charity_activities": _text(row.get("charity_activities")),
            "gift_aid": row.get("charity_gift_aid"),
            "has_land": row.get("charity_has_land"),
            "insolvent": row.get("charity_insolvent"),
            "in_administration": row.get("charity_in_administration"),
            "company_registration_number": _text(row.get("charity_company_registration_number")),
            "grant_expenditure": _number(row.get("expenditure_grants_institution")),
            "charitable_expenditure": _number(row.get("expenditure_charitable_expenditure")),
            "volunteer_count": _number(row.get("count_volunteers")),
            "employee_count": _number(row.get("count_employees")),
            "has_governing_document": bool(row.get("has_governing_document")),
            "has_event_history": bool(row.get("has_event_history")),
            "has_published_report": bool(row.get("has_published_report")),
        }
    )
    record["raw_record"] = dict(row)
    return record


def charity_commission_bulk_summary(
    path: Path = DEFAULT_CHARITY_COMMISSION_DATABASE,
) -> dict[str, int]:
    with _database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(charity_registration_status = 'Registered') AS active,
                   SUM(charity_registration_status = 'Removed') AS removed,
                   SUM(COALESCE(TRIM(charity_contact_web), '') <> '') AS with_website,
                   SUM(COALESCE(TRIM(charity_contact_email), '') <> '') AS with_email,
                   SUM(COALESCE(TRIM(charity_contact_phone), '') <> '') AS with_phone,
                   SUM(COALESCE(TRIM(charity_contact_address1), '') <> '') AS with_address,
                   SUM(latest_income IS NOT NULL) AS with_latest_income,
                   SUM(latest_expenditure IS NOT NULL) AS with_latest_expenditure,
                   SUM(has_financial_history = 1) AS with_financial_history,
                   SUM(assets IS NOT NULL OR liabilities IS NOT NULL) AS with_assets_liabilities,
                   SUM(primary_purpose_grant_making IS NOT NULL) AS with_grant_maker_flag,
                   SUM(CAST(primary_purpose_grant_making AS INTEGER) = 1) AS primary_grant_makers,
                   SUM(areas_of_operation IS NOT NULL AND areas_of_operation <> '[]') AS with_geography,
                   SUM(all_classifications IS NOT NULL AND all_classifications <> '[]') AS with_classifications
            FROM charity_enrichment
            """
        ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def charity_commission_bulk_geographies(
    path: Path = DEFAULT_CHARITY_COMMISSION_DATABASE,
) -> list[str]:
    with _database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT geographic_area_description
            FROM charity_area_of_operation
            WHERE geographic_area_description IS NOT NULL
              AND TRIM(geographic_area_description) <> ''
            ORDER BY geographic_area_description
            """
        ).fetchall()
    return [str(row[0]) for row in rows]


def query_charity_commission_bulk(
    *,
    status: str = "All",
    has_website: str = "All",
    has_email: str = "All",
    has_latest_income: str = "All",
    has_financial_history: str = "All",
    has_grant_maker_flag: str = "All",
    grant_maker_value: str = "All",
    geography: str = "All",
    search: str = "",
    limit: int = 500,
    path: Path = DEFAULT_CHARITY_COMMISSION_DATABASE,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    parameters: list[Any] = []

    if status in {"Active", "Removed"}:
        clauses.append("e.charity_registration_status = ?")
        parameters.append("Registered" if status == "Active" else "Removed")
    elif status == "Unknown":
        clauses.append(
            "(e.charity_registration_status IS NULL "
            "OR e.charity_registration_status NOT IN ('Registered', 'Removed'))"
        )

    boolean_filters = [
        (has_website, "COALESCE(TRIM(e.charity_contact_web), '') <> ''"),
        (has_email, "COALESCE(TRIM(e.charity_contact_email), '') <> ''"),
        (has_latest_income, "e.latest_income IS NOT NULL"),
        (has_financial_history, "e.has_financial_history = 1"),
        (has_grant_maker_flag, "e.primary_purpose_grant_making IS NOT NULL"),
    ]
    for selection, expression in boolean_filters:
        if selection == "Yes":
            clauses.append(expression)
        elif selection == "No":
            clauses.append(f"NOT ({expression})")

    if grant_maker_value == "Yes":
        clauses.append("CAST(e.primary_purpose_grant_making AS INTEGER) = 1")
    elif grant_maker_value == "No":
        clauses.append("CAST(e.primary_purpose_grant_making AS INTEGER) = 0")

    if geography != "All":
        clauses.append(
            "EXISTS (SELECT 1 FROM charity_area_of_operation area "
            "WHERE area.organisation_number = e.organisation_number "
            "AND area.geographic_area_description = ?)"
        )
        parameters.append(geography)

    search_term = search.strip()
    if search_term:
        like = f"%{search_term}%"
        clauses.append(
            "(e.charity_name LIKE ? COLLATE NOCASE "
            "OR CAST(e.registered_charity_number AS TEXT) LIKE ? "
            "OR CAST(e.organisation_number AS TEXT) LIKE ? "
            "OR COALESCE(e.areas_of_operation, '') LIKE ? COLLATE NOCASE "
            "OR COALESCE(e.all_classifications, '') LIKE ? COLLATE NOCASE)"
        )
        parameters.extend([like, like, like, like, like])

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _database_connection(path) as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM charity_enrichment e{where}",
                parameters,
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT e.*
            FROM charity_enrichment e
            {where}
            ORDER BY CASE e.charity_registration_status WHEN 'Registered' THEN 0 ELSE 1 END,
                     e.charity_name
            LIMIT ?
            """,
            [*parameters, max(1, min(int(limit), 2_000))],
        ).fetchall()
    return [normalize_charity_commission_bulk_row(dict(row)) for row in rows], total


def get_charity_commission_bulk_record(
    organisation_number: Any,
    path: Path = DEFAULT_CHARITY_COMMISSION_DATABASE,
) -> dict[str, Any] | None:
    key = normalize_identifier(organisation_number)
    if not key:
        return None
    with _database_connection(path) as connection:
        row = connection.execute(
            "SELECT * FROM charity_enrichment WHERE organisation_number = ? LIMIT 1",
            (key,),
        ).fetchone()
        if row is None:
            return None
        history = [
            dict(item)
            for item in connection.execute(
                """
                SELECT h.fin_period_end_date AS financial_period_end_date,
                       h.total_gross_income AS income,
                       h.total_gross_expenditure AS expenditure,
                       b.assets_total_assets_and_liabilities AS assets,
                       b.assets_total_liabilities AS liabilities
                FROM charity_annual_return_history h
                LEFT JOIN charity_annual_return_partb b
                  ON b.organisation_number = h.organisation_number
                 AND b.fin_period_end_date = h.fin_period_end_date
                WHERE h.organisation_number = ?
                ORDER BY h.fin_period_end_date DESC
                LIMIT 5
                """,
                (key,),
            ).fetchall()
        ]
        supplements = {
            "other_names": [
                dict(item)
                for item in connection.execute(
                    "SELECT charity_name, charity_name_type FROM charity_other_names WHERE organisation_number = ?",
                    (key,),
                ).fetchall()
            ],
            "other_regulators": [
                dict(item)
                for item in connection.execute(
                    "SELECT regulator_name, regulator_web_url FROM charity_other_regulators WHERE organisation_number = ?",
                    (key,),
                ).fetchall()
            ],
            "policies": [
                item[0]
                for item in connection.execute(
                    "SELECT policy_name FROM charity_policy WHERE organisation_number = ? ORDER BY policy_name",
                    (key,),
                ).fetchall()
            ],
            "published_reports": [
                dict(item)
                for item in connection.execute(
                    "SELECT report_name, report_location, date_published FROM charity_published_report WHERE organisation_number = ?",
                    (key,),
                ).fetchall()
            ],
            "governing_documents": [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT governing_document_description, charitable_objects, area_of_benefit
                    FROM charity_governing_document
                    WHERE organisation_number = ?
                    """,
                    (key,),
                ).fetchall()
            ],
            "event_history": [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT event_type, date_of_event, reason, assoc_charity_name
                    FROM charity_event_history
                    WHERE organisation_number = ?
                    ORDER BY date_of_event DESC
                    LIMIT 25
                    """,
                    (key,),
                ).fetchall()
            ],
        }
    source = dict(row)
    source["financial_history"] = history
    record = normalize_charity_commission_bulk_row(source)
    record.update(supplements)
    return record


def find_charity_commission_bulk_match(
    organization: dict[str, Any],
    path: Path = DEFAULT_CHARITY_COMMISSION_DATABASE,
) -> dict[str, Any] | None:
    funding_info = _as_dict(organization.get("funding_info"))
    candidates = [
        ("organisation_number", organization.get("organisation_number")),
        ("registered_charity_number", organization.get("registered_charity_number")),
        ("registered_charity_number", organization.get("charity_number")),
        ("registered_charity_number", funding_info.get("charity_number")),
    ]
    with _database_connection(path) as connection:
        for field, candidate in candidates:
            key = normalize_identifier(candidate)
            if not key or not key.isdigit():
                continue
            row = connection.execute(
                f"""
                SELECT organisation_number
                FROM charity_enrichment
                WHERE CAST({field} AS TEXT) = ?
                ORDER BY CASE charity_registration_status WHEN 'Registered' THEN 0 ELSE 1 END,
                         CASE COALESCE(linked_charity_number, 0) WHEN 0 THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (key,),
            ).fetchone()
            if row:
                return get_charity_commission_bulk_record(row[0], path)
    return None
