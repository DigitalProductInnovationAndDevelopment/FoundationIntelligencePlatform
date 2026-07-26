import json
import os
import sqlite3
import hashlib
import re
import base64
import binascii
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import List, Optional, Dict, Any, Mapping, Tuple
from urllib.parse import urlsplit

import pycountry

from bff.config import DATA_PATH, DB_PATH
from bff.utils.logging import logger
from data.db_loader import REQUIRED_SCHEMA, migrate_grant_overview_schema
from data.registry import (
    REGISTRY_FTS_TABLE,
    REGISTRY_LINK_TABLE,
    REGISTRY_TABLE,
    migrate_registry_schema,
    normalize_organization_name,
)
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


def _score_summary(score: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the small, list-safe part of an explainable relevance score."""
    return {
        "relevance_score": score.get("score"),
        "score_confidence": score.get("confidence"),
        "score_completeness": score.get("data_completeness"),
        "score_target": score.get("score_target"),
        "score_version": score.get("score_version"),
        "score_configuration_status": score.get("configuration_status"),
    }


def _safe_external_url(value: Any) -> Optional[str]:
    """Return a user-clickable URL without ever resolving or fetching it."""
    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate) > 2_048
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
    ):
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return candidate


def _evidence_link_type(kind: str, url: str) -> str:
    """Classify the stored destination without opening or probing it."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "website"
    host = parsed.netloc.casefold()
    path = parsed.path.casefold()
    if (
        kind.endswith("_record")
        or host.startswith("api.")
        or "/api/" in path
        or path.endswith(".json")
    ):
        return "json"
    return "website"


def _source_evidence_links(
    raw_grant_data: Any,
    source_url: Any,
    *,
    funder_name: Optional[str] = None,
    recipient_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract typed links already present in a stored source record.

    External organization JSON endpoints are exposed as evidence links only.
    The BFF deliberately does not fetch, proxy, preflight, or enrich them.
    """
    raw = _json_dict(raw_grant_data)
    data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
    role_names = {
        "funder": str(funder_name or "").strip(),
        "recipient": str(recipient_name or "").strip(),
    }
    role_names_by_id: Dict[str, Dict[str, str]] = {"funder": {}, "recipient": {}}
    for role, collection in (
        ("funder", data.get("fundingOrganization")),
        ("recipient", data.get("recipientOrganization")),
    ):
        for item in collection if isinstance(collection, list) else []:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or item.get("legalName") or "").strip()
            if name:
                role_names[role] = name
                for identifier in (item.get("id"), item.get("org_id"), item.get("charityNumber")):
                    normalized_identifier = str(identifier or "").strip()
                    if normalized_identifier:
                        role_names_by_id[role][normalized_identifier] = name

    publisher_name = role_names["funder"] or "Publisher"
    candidates: List[Tuple[str, str, str, Any]] = [
        ("publisher_grant_data", "publisher", publisher_name, source_url),
        ("publisher_grant_data", "publisher", publisher_name, data.get("dataSource")),
    ]
    for role, collection in (("funder", raw.get("funders")), ("recipient", raw.get("recipients"))):
        for item in collection if isinstance(collection, list) else []:
            if isinstance(item, Mapping):
                organization_id = str(item.get("org_id") or item.get("id") or "").strip()
                candidates.append((
                    f"360giving_{role}_record",
                    role,
                    role_names_by_id[role].get(organization_id) or role_names[role] or role.title(),
                    item.get("self"),
                ))
    for role, collection in (
        ("funder", data.get("fundingOrganization")),
        ("recipient", data.get("recipientOrganization")),
    ):
        for item in collection if isinstance(collection, list) else []:
            if isinstance(item, Mapping):
                candidates.append((
                    f"observed_{role}_website",
                    role,
                    role_names[role] or role.title(),
                    item.get("url"),
                ))
    links: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for kind, role, organization_name, value in candidates:
        url = _safe_external_url(value)
        marker = (kind, url or "")
        if not url or marker in seen:
            continue
        seen.add(marker)
        link_type = _evidence_link_type(kind, url)
        role_label = "published grant data" if role == "publisher" else f"{role} {'record' if link_type == 'json' else 'website'}"
        links.append({
            "kind": kind,
            "label": f"{organization_name} · {role_label}",
            "role": role,
            "organization_name": organization_name,
            "link_type": link_type,
            "url": url,
            "origin": "stored_source_record",
        })
    return links


REGISTRY_MAX_PAGE_SIZE = 100
REGISTRY_DEFAULT_PAGE_SIZE = 50
REGISTRY_SORTS = {"name", "income_desc", "expenditure_desc"}


def _encode_registry_cursor(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_registry_cursor(cursor: Optional[str], sort: str) -> Optional[Dict[str, Any]]:
    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode((cursor + padding).encode("ascii")))
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("Invalid directory cursor.") from exc
    if not isinstance(payload, dict) or payload.get("sort") != sort:
        raise ValueError("Directory cursor does not match the selected sort order.")
    if not isinstance(payload.get("registry_id"), str):
        raise ValueError("Directory cursor is missing its registry identifier.")
    return payload


def _fts_query(value: str) -> str:
    """Create a token-prefix FTS expression from a normalized query."""
    tokens = [token for token in normalize_organization_name(value).split() if token]
    if not tokens:
        return ""
    return " AND ".join(f'"{token.replace(chr(34), "")}"*' for token in tokens)


def _prefix_upper_bound(value: str) -> str:
    # The fallback remains indexable (normalized_name >= prefix AND < prefix+sentinel)
    # and intentionally avoids a table-scanning %query% expression.
    return value + "\uffff"


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


def _overview_programme_classification(
    source_raw: Any, inferred_raw: Any, scores_raw: Any,
) -> tuple[List[str], str, bool, bool]:
    """Resolve programme categories once for the serving-side fact table."""
    source_values = _json_list(source_raw)
    source_categories, _ = normalize_programme_sources(source_values)
    invalid_source_label = bool(source_values and not source_categories)
    inferred_values = [
        category for category in _json_list(inferred_raw)
        if category in PROGRAMME_TAXONOMY
    ]
    scores = _json_dict(scores_raw)
    accepted_inferred: List[str] = []
    for category in inferred_values:
        try:
            confidence = float(scores.get(category, 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence >= DEFAULT_REVIEW_THRESHOLD:
            accepted_inferred.append(category)
    if source_categories:
        return sorted(set(source_categories)), "source", invalid_source_label, False
    if accepted_inferred:
        return sorted(set(accepted_inferred)), "inferred", invalid_source_label, False
    return ["Unclassified"], "unclassified", invalid_source_label, bool(inferred_values)


OVERVIEW_INDEX_REVISION_KEY = "grant_overview_index_revision"
OVERVIEW_SCHEMA_VERSION_KEY = "grant_overview_schema_version"
OVERVIEW_SCHEMA_VERSION = "2026-07-overview-facts-v5"
OVERVIEW_CACHE_MAX_ENTRIES = 64
OVERVIEW_ORGANIZATION_ONLY_SOURCES = {
    "charity commission for england and wales",
    "philea",
}
# Bump whenever a presentation aggregation changes without changing source
# records, so persisted Overview payloads cannot retain stale semantics.
OVERVIEW_AGGREGATION_VERSION = "2026-07-lazy-map-connections-v1"


def _grant_overview_data_revision(connection: sqlite3.Connection) -> str:
    """Return a compact fingerprint of facts that influence Overview results."""
    row = connection.execute(
        """
        SELECT COUNT(*), MAX(grant_id), MAX(COALESCE(ingestion_timestamp, '')),
               MAX(COALESCE(exchange_rate_date, '')),
               COUNT(amount_eur), COALESCE(SUM(amount_eur), 0)
        FROM grants
        """
    ).fetchone()
    return json.dumps(list(row or (0, "", "", "", 0, 0)), separators=(",", ":"), default=str)


def _beneficiary_index_terms(normalized_raw: Any, source_raw: Any) -> set[str]:
    terms = {
        str(country.get("country_name") or "").strip().casefold()
        for country in _beneficiary_countries(normalized_raw, source_raw)
        if str(country.get("country_name") or "").strip()
    }
    terms.update({
        str(country.get("country_code") or "").strip().casefold()
        for country in _beneficiary_countries(normalized_raw, source_raw)
        if str(country.get("country_code") or "").strip()
    })
    for location in _json_list(normalized_raw) + _json_list(source_raw):
        if isinstance(location, Mapping):
            values = [location.get(key) for key in ("name", "macro_region", "country", "countryCode", "code")]
        else:
            values = [location]
        terms.update(str(value).strip().casefold() for value in values if str(value or "").strip())
    return terms


def rebuild_grant_overview_indexes(connection: sqlite3.Connection) -> Dict[str, int]:
    """Rebuild derived filter indexes from grant facts in one transaction."""
    migrate_grant_overview_schema(connection)
    cursor = connection.cursor()
    cursor.execute("DELETE FROM grant_beneficiary_terms")
    cursor.execute("DELETE FROM grant_beneficiary_countries")
    cursor.execute("DELETE FROM grant_programme_categories")
    cursor.execute("DELETE FROM grant_source_funder_facts")
    cursor.execute("DELETE FROM grant_overview_facts")
    revision = _grant_overview_data_revision(connection)
    valid_profile_ids = {
        int(row[0]) for row in connection.execute("SELECT charity_id FROM charities")
    }
    rows = connection.execute(
        """
        SELECT g.grant_id, g.beneficiary_geography_normalized, g.beneficiary_geography,
               g.programme_area_source, g.programme_area_inferred, g.programme_area_scores,
               g.source, g.funding_org_source_id, g.funding_name, g.funding_charity_id,
               g.recipient_org_source_id, g.recipient_name, g.date, g.currency, g.amount,
               g.amount_eur, g.conversion_status, g.source_url, g.source_record_id,
               g.raw_grant_data, c.headquarters_country
        FROM grants AS g
        LEFT JOIN charities AS c ON c.charity_id = g.funding_charity_id
        ORDER BY g.grant_id
        """
    )
    country_rows: list[tuple[str, str, str]] = []
    term_rows: list[tuple[str, str]] = []
    programme_rows: list[tuple[str, str]] = []
    source_funder_rows: list[tuple[Any, ...]] = []
    overview_fact_rows: list[tuple[Any, ...]] = []
    grants_indexed = 0
    source_funder_facts = 0
    for (
        grant_id, normalized_raw, source_raw, programme_source, programme_inferred,
        programme_scores, source, funder_source_id, funder_name, funder_profile_id,
        recipient_source_id, recipient_name, award_date_raw, currency, amount,
        amount_eur, conversion_status, source_url, source_record_id,
        raw_grant_data, directory_headquarters,
    ) in rows:
        identifier = str(grant_id)
        countries = _beneficiary_countries(normalized_raw, source_raw)
        programme_categories, programme_provenance, invalid_source_label, low_confidence_inference = (
            _overview_programme_classification(
                programme_source, programme_inferred, programme_scores
            )
        )
        country_rows.extend(
            (identifier, str(country["country_code"]), str(country["country_name"]))
            for country in countries
        )
        term_rows.extend(
            (identifier, term)
            for term in _beneficiary_index_terms(normalized_raw, source_raw)
        )
        programme_rows.extend(
            (identifier, category)
            for category in programme_categories
        )
        original_status, original_minor = _money_minor_units(amount)
        eur_status, eur_minor = _money_minor_units(amount_eur)
        award_date = CharityRepository._overview_award_date(award_date_raw)
        award_date_status = (
            "valid" if award_date else
            "missing" if award_date_raw is None or not str(award_date_raw).strip() else
            "invalid"
        )
        source_name = str(source or "source").strip() or "source"
        display_name = _display_source_entity_name(
            funder_name, "Unnamed source funder"
        )
        recipient_display = _display_source_entity_name(
            recipient_name, "Unnamed recipient"
        )
        origin, origin_source = _funder_headquarters_country(
            raw_grant_data, directory_headquarters
        )
        overview_fact_rows.append((
            identifier,
            source_name,
            award_date,
            award_date_status,
            str(currency or "").strip().upper() or None,
            original_minor,
            original_status,
            eur_minor,
            eur_status,
            str(conversion_status or "").strip() or None,
            display_name,
            normalize_organization_name(display_name),
            recipient_display,
            normalize_organization_name(recipient_display),
            len(countries),
            len(programme_categories),
            programme_provenance,
            int(invalid_source_label),
            int(low_confidence_inference),
            origin.get("country_code") if origin else None,
            origin.get("country_name") if origin else None,
            origin_source,
            revision,
        ))
        has_funder_identity = bool(
            str(funder_source_id or "").strip() or str(funder_name or "").strip()
        )
        if countries and has_funder_identity:
            funder_key, identity_method = _source_entity_identity(
                role="funder",
                source=source,
                source_id=funder_source_id,
                name=funder_name,
            )
            recipient_key, _ = _source_entity_identity(
                role="recipient",
                source=source,
                source_id=recipient_source_id,
                name=recipient_name,
            )
            raw_profile_id: Optional[int]
            try:
                raw_profile_id = int(funder_profile_id) if funder_profile_id is not None else None
            except (TypeError, ValueError):
                raw_profile_id = None
            linked_profile_id = (
                raw_profile_id if raw_profile_id in valid_profile_ids else None
            )
            normalized_fallback = (
                normalize_organization_name(funder_name)
                if identity_method == "normalized_name_fallback" else None
            )
            country_count = len(countries)
            for country in countries:
                source_funder_rows.append((
                    identifier,
                    str(country["country_code"]),
                    str(country["country_name"]),
                    source_name,
                    funder_key,
                    identity_method,
                    str(funder_source_id).strip() if funder_source_id is not None else None,
                    normalized_fallback,
                    display_name,
                    recipient_key,
                    recipient_display,
                    award_date,
                    str(currency or "").strip().upper() or None,
                    original_minor,
                    original_status,
                    eur_minor,
                    eur_status,
                    str(conversion_status or "").strip() or None,
                    country_count,
                    linked_profile_id,
                    str(source_url or "").strip() or None,
                    str(source_record_id or "").strip() or None,
                    revision,
                ))
                source_funder_facts += 1
        grants_indexed += 1
        if grants_indexed % 2_000 == 0:
            cursor.executemany(
                "INSERT OR IGNORE INTO grant_beneficiary_countries (grant_id, country_code, country_name) VALUES (?, ?, ?)",
                country_rows,
            )
            cursor.executemany(
                "INSERT OR IGNORE INTO grant_beneficiary_terms (grant_id, term) VALUES (?, ?)",
                term_rows,
            )
            cursor.executemany(
                "INSERT OR IGNORE INTO grant_programme_categories (grant_id, programme_area) VALUES (?, ?)",
                programme_rows,
            )
            cursor.executemany(
                """
                INSERT OR REPLACE INTO grant_source_funder_facts (
                    grant_id, country_code, country_name, source_namespace,
                    source_funder_key, identity_method, source_organization_id,
                    normalized_name_fallback, display_name, recipient_key,
                    recipient_name, award_date, currency, original_amount_minor,
                    original_amount_status, eur_amount_minor, eur_amount_status,
                    conversion_status, country_count, linked_profile_id,
                    publisher_source_url, source_record_id, data_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                source_funder_rows,
            )
            cursor.executemany(
                """
                INSERT OR REPLACE INTO grant_overview_facts (
                    grant_id, source_namespace, award_date, award_date_status, currency,
                    original_amount_minor, original_amount_status,
                    eur_amount_minor, eur_amount_status, conversion_status,
                    funding_name, funding_name_normalized,
                    recipient_name, recipient_name_normalized, country_count,
                    programme_category_count, programme_provenance,
                    invalid_source_label, low_confidence_inference,
                    origin_country_code, origin_country_name, origin_source,
                    data_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                overview_fact_rows,
            )
            country_rows.clear()
            term_rows.clear()
            programme_rows.clear()
            source_funder_rows.clear()
            overview_fact_rows.clear()
    if country_rows:
        cursor.executemany(
            "INSERT OR IGNORE INTO grant_beneficiary_countries (grant_id, country_code, country_name) VALUES (?, ?, ?)",
            country_rows,
        )
    if term_rows:
        cursor.executemany(
            "INSERT OR IGNORE INTO grant_beneficiary_terms (grant_id, term) VALUES (?, ?)", term_rows
        )
    if programme_rows:
        cursor.executemany(
            "INSERT OR IGNORE INTO grant_programme_categories (grant_id, programme_area) VALUES (?, ?)",
            programme_rows,
        )
    if source_funder_rows:
        cursor.executemany(
            """
            INSERT OR REPLACE INTO grant_source_funder_facts (
                grant_id, country_code, country_name, source_namespace,
                source_funder_key, identity_method, source_organization_id,
                normalized_name_fallback, display_name, recipient_key,
                recipient_name, award_date, currency, original_amount_minor,
                original_amount_status, eur_amount_minor, eur_amount_status,
                conversion_status, country_count, linked_profile_id,
                publisher_source_url, source_record_id, data_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            source_funder_rows,
        )
    if overview_fact_rows:
        cursor.executemany(
            """
            INSERT OR REPLACE INTO grant_overview_facts (
                grant_id, source_namespace, award_date, award_date_status, currency,
                original_amount_minor, original_amount_status,
                eur_amount_minor, eur_amount_status, conversion_status,
                funding_name, funding_name_normalized,
                recipient_name, recipient_name_normalized, country_count,
                programme_category_count, programme_provenance,
                invalid_source_label, low_confidence_inference,
                origin_country_code, origin_country_name, origin_source,
                data_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            overview_fact_rows,
        )
    cursor.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (OVERVIEW_INDEX_REVISION_KEY, revision),
    )
    cursor.execute("DELETE FROM grant_overview_cache")
    connection.commit()
    return {
        "grants_indexed": grants_indexed,
        "source_funder_facts": source_funder_facts,
        "overview_facts": grants_indexed,
    }


def _top_counter_items(counter: Counter, limit: int = 3) -> List[Dict[str, Any]]:
    return [
        {"name": str(name), "count": count}
        for name, count in sorted(
            counter.items(), key=lambda item: (-item[1], str(item[0]).casefold())
        )[:limit]
    ]


def _top_labeled_counter_items(
    counter: Counter, labels: Mapping[str, str], limit: int = 3,
) -> List[Dict[str, Any]]:
    return [
        {"name": labels.get(str(key), str(key)), "count": count}
        for key, count in sorted(
            counter.items(), key=lambda item: (-item[1], labels.get(str(item[0]), str(item[0])).casefold())
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


def _source_entity_identity(*, role: str, source: object, source_id: object, name: object) -> tuple[str, str]:
    """Return a stable identity for an entity described only by a grant source.

    The identity intentionally ignores a linked Charity Commission profile. A
    source funder remains the same source funder when enrichment later adds (or
    removes) a verified organisation link.
    """

    namespace = re.sub(r"[^a-z0-9]+", "", str(source or "source").lower()) or "source"
    raw_source_id = str(source_id or "").strip()
    if raw_source_id:
        material = f"{namespace}\0{role}\0source-id\0{raw_source_id.casefold()}"
        identity_method = "source_id"
    else:
        normalized_name = normalize_organization_name(str(name or ""))
        normalized_name = normalized_name or str(name or "").strip().casefold()
        material = f"{namespace}\0{role}\0normalized-name\0{normalized_name}"
        identity_method = "normalized_name_fallback"

    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{namespace}:{role}:{identity_method}:{digest}", identity_method


def _display_source_entity_name(value: object, fallback: str) -> str:
    name = str(value or "").strip()
    return name or fallback

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
        sources: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        max_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        max_avg_grant_size: Optional[float] = None,
        include_score: bool = False,
        sort: str = "name_asc",
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        pass

    async def get_registry_page(
        self,
        query: Optional[str] = None,
        charity_number: Optional[str] = None,
        status: Optional[str] = None,
        income_min: Optional[float] = None,
        income_max: Optional[float] = None,
        expenditure_min: Optional[float] = None,
        expenditure_max: Optional[float] = None,
        country: Optional[str] = None,
        region: Optional[str] = None,
        beneficiary_geography: Optional[str] = None,
        has_enriched_profile: Optional[bool] = None,
        has_grant_data: Optional[bool] = None,
        cursor: Optional[str] = None,
        limit: int = REGISTRY_DEFAULT_PAGE_SIZE,
        sort: str = "name",
    ) -> Dict[str, Any]:
        """Fallback contract for the optional scalable registry layer."""
        return {
            "results": [],
            "next_cursor": None,
            "has_more": False,
            "applied_filters": {},
            "page_size": min(max(limit, 1), REGISTRY_MAX_PAGE_SIZE),
            "registry_count": None,
            "search_strategy": "registry_unavailable",
        }

    async def get_registry_detail(self, registry_id: str) -> Optional[Dict[str, Any]]:
        return None

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
        sources: Optional[List[str]] = None,
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

    async def get_grant_overview(
        self,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
        granularity: str = "auto",
        include_connections: bool = False,
    ) -> Dict[str, Any]:
        """Return a single, consistently filtered grant-analysis payload."""
        return {
            "status": "data_unavailable",
            "kpis": {},
            "map": {},
            "trends": {},
            "themes": {},
            "available_date_range": {"from": None, "to": None},
            "applied_filters": {},
        }

    async def get_grant_entity_suggestions(
        self,
        *,
        sources: Optional[List[str]] = None,
        limit: int = 2_500,
    ) -> Dict[str, Any]:
        """Return cached observed donor and recipient names for local filtering.

        The UI loads this compact index once for a selected source set, then
        filters it in the browser while the user types. Repositories without
        normalized transaction facts remain explicitly unavailable.
        """
        return {
            "status": "data_unavailable",
            "donors": [],
            "recipients": [],
        }

    async def get_grant_overview_trends(
        self,
        *,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
        granularity: str = "auto",
    ) -> Dict[str, Any]:
        """Fallback for a trend-only request in non-transaction repositories."""
        return await self.get_grant_trends(currency=currency)

    async def get_grant_overview_drilldown(
        self,
        *,
        selection_type: str,
        selection_value: str,
        **_filters: Any,
    ) -> Dict[str, Any]:
        """Fallback for a bounded Overview chart drill-down."""
        return {
            "status": "data_unavailable",
            "selection": {"type": selection_type, "value": selection_value, "label": selection_value},
            "summary": {},
            "funders": [],
            "recipients": [],
            "countries": [],
            "grants": [],
            "metadata": {"data_mode": "transaction_data_unavailable"},
        }

    async def get_source_funders(
        self,
        *,
        beneficiary_country: str,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
        search: Optional[str] = None,
        profile_status: str = "all",
        sort: str = "largest_observed_funding",
        page: int = 1,
        page_size: int = 25,
    ) -> Dict[str, Any]:
        """Fallback for repositories without source-funder support."""
        return {
            "status": "data_unavailable",
            "country": {"code": str(beneficiary_country or "").upper(), "name": "Unavailable"},
            "summary": {
                "matching_funder_count": 0,
                "matching_grant_count": 0,
                "source_only_funder_count": 0,
                "linked_directory_funder_count": 0,
            },
            "items": [],
            "pagination": {"page": page, "page_size": page_size, "total_items": 0, "total_pages": 0},
            "available_date_range": {"from": None, "to": None},
            "available_currencies": [],
            "applied_filters": {
                "beneficiary_country": beneficiary_country,
                "search": str(search or "").strip() or None,
                "profile_status": profile_status,
            },
            "metadata": {"data_mode": "transaction_data_unavailable"},
        }

    async def get_source_funder_detail(
        self,
        source_funder_key: str,
        **_filters: Any,
    ) -> Optional[Dict[str, Any]]:
        """Fallback for repositories without source-funder support."""
        return None

    @staticmethod
    def _overview_award_date(value: Any) -> Optional[str]:
        """Return a strict ISO award date without accepting partial/invalid dates."""
        if value is None:
            return None
        candidate = str(value).strip()[:10]
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _overview_period_labels(start_date: str, end_date: str, granularity: str) -> List[str]:
        start_year, start_month = (int(part) for part in start_date[:7].split("-"))
        end_year, end_month = (int(part) for part in end_date[:7].split("-"))
        month_count = (end_year - start_year) * 12 + end_month - start_month + 1
        if granularity == "yearly":
            return [str(year) for year in range(start_year, end_year + 1)]
        return [_month_offset(f"{start_year:04d}-{start_month:02d}", offset) for offset in range(month_count)]

    def _overview_cache_key(
        self,
        *,
        currency: Optional[str], date_from: Optional[str], date_to: Optional[str],
        beneficiary_geographies: Optional[List[str]], programme_areas: Optional[List[str]],
        donor: Optional[str], recipient: Optional[str], sources: Optional[List[str]], granularity: str,
        include_connections: bool,
    ) -> str:
        selected_sources = sources if sources is not None else ["360Giving"]
        grant_sources = {
            str(value).strip().casefold()
            for value in selected_sources
            if str(value).strip()
            and str(value).strip().casefold() not in OVERVIEW_ORGANIZATION_ONLY_SOURCES
        }
        payload = {
            "aggregation_version": OVERVIEW_AGGREGATION_VERSION,
            "currency": str(currency or "auto").strip().upper(),
            "date_from": date_from or "",
            "date_to": date_to or "",
            "beneficiary_geographies": sorted({str(value).strip().casefold() for value in beneficiary_geographies or [] if str(value).strip()}),
            "programme_areas": sorted({str(value).strip().casefold() for value in programme_areas or [] if str(value).strip()}),
            "donor": str(donor or "").strip().casefold(),
            "recipient": str(recipient or "").strip().casefold(),
            # Organization-only sources cannot change a grant aggregation and
            # therefore must not create expensive duplicate cache entries.
            "sources": sorted(grant_sources),
            "granularity": granularity,
            "include_connections": bool(include_connections),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _ensure_overview_indexes(self, conn: sqlite3.Connection) -> str:
        cached_revision = getattr(self, "_overview_revision", None)
        if cached_revision:
            # Writers invalidate this cheap metadata marker transactionally.
            # Do not trust only the in-process value: the enrichment pipeline
            # atomically publishes a new SQLite file while the BFF keeps this
            # repository instance alive.  Returning the stale value here made
            # freshly linked organization profiles remain "observed only".
            indexed_revision = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (OVERVIEW_INDEX_REVISION_KEY,),
            ).fetchone()
            if indexed_revision and indexed_revision[0] == cached_revision:
                return cached_revision
            self._overview_revision = None
        # Apply additive lookup-index migrations once per database schema
        # version. Re-running CREATE INDEX on each BFF process start is
        # surprisingly expensive for a 200k-grant database.
        schema_row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (OVERVIEW_SCHEMA_VERSION_KEY,)
        ).fetchone()
        schema_current = bool(
            schema_row and schema_row[0] == OVERVIEW_SCHEMA_VERSION
        )
        if not schema_current:
            migrate_grant_overview_schema(conn)
            # A schema-version change can add a new derived structure while the
            # immutable grant fingerprint remains identical. Force one
            # reproducible rebuild so no newly added fact table stays empty.
            conn.execute(
                "DELETE FROM metadata WHERE key = ?", (OVERVIEW_INDEX_REVISION_KEY,)
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (OVERVIEW_SCHEMA_VERSION_KEY, OVERVIEW_SCHEMA_VERSION),
            )
            conn.commit()
        else:
            # Supported data writers invalidate this key transactionally.
            # Reusing it avoids a full SUM/COUNT scan of the 1.3 GB grants
            # table on every fresh BFF process.
            indexed_revision = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (OVERVIEW_INDEX_REVISION_KEY,),
            ).fetchone()
            if indexed_revision and indexed_revision[0]:
                self._overview_revision = str(indexed_revision[0])
                return self._overview_revision
        revision = _grant_overview_data_revision(conn)
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (OVERVIEW_INDEX_REVISION_KEY,)
        ).fetchone()
        if not row or row[0] != revision:
            rebuild_grant_overview_indexes(conn)
        self._overview_revision = revision
        return revision

    @staticmethod
    def _load_overview_cache(conn: sqlite3.Connection, cache_key: str, revision: str) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            "SELECT payload FROM grant_overview_cache WHERE cache_key = ? AND data_revision = ?",
            (cache_key, revision),
        ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[0])
            return payload if isinstance(payload, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _store_overview_cache(
        conn: sqlite3.Connection, cache_key: str, revision: str, payload: Mapping[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO grant_overview_cache (cache_key, data_revision, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (cache_key, revision, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), _utc_now()),
        )
        conn.execute(
            """
            DELETE FROM grant_overview_cache
            WHERE cache_key NOT IN (
                SELECT cache_key FROM grant_overview_cache ORDER BY created_at DESC LIMIT ?
            )
            """,
            (OVERVIEW_CACHE_MAX_ENTRIES,),
        )
        conn.commit()

    def _overview_source_metadata(
        self,
        conn: sqlite3.Connection,
        sources: Optional[List[str]] = None,
        *,
        revision: Optional[str] = None,
    ) -> Dict[str, Any]:
        selected_sources = (
            [str(source).strip() for source in sources if str(source).strip()]
            if sources is not None else ["360Giving"]
        )
        if not selected_sources:
            return {"date_from": None, "date_to": None, "currencies": []}
        metadata_cache_key = (
            str(revision or ""),
            tuple(sorted({source.casefold() for source in selected_sources})),
        )
        metadata_cache = getattr(self, "_overview_source_metadata_cache", {})
        cached_metadata = metadata_cache.get(metadata_cache_key)
        if cached_metadata is not None:
            return {
                "date_from": cached_metadata["date_from"],
                "date_to": cached_metadata["date_to"],
                "currencies": list(cached_metadata["currencies"]),
            }
        placeholders = ", ".join("?" for _ in selected_sources)
        dates = conn.execute(
            f"""
            SELECT MIN(award_date), MAX(award_date)
            FROM grant_overview_facts
            WHERE source_namespace IN ({placeholders})
              AND award_date IS NOT NULL
            """,
            selected_sources,
        ).fetchone()
        currencies = conn.execute(
            f"""
            SELECT DISTINCT currency
            FROM grant_overview_facts
            WHERE source_namespace IN ({placeholders})
              AND currency IS NOT NULL
              AND LENGTH(currency) = 3
            ORDER BY currency
            """,
            selected_sources,
        ).fetchall()
        result = {
            "date_from": dates[0] if dates else None,
            "date_to": dates[1] if dates else None,
            "currencies": [row[0] for row in currencies],
        }
        metadata_cache[metadata_cache_key] = result
        self._overview_source_metadata_cache = metadata_cache
        return {**result, "currencies": list(result["currencies"])}

    def _remember_overview_source_metadata(
        self,
        sources: Optional[List[str]],
        revision: str,
        overview_payload: Mapping[str, Any],
    ) -> None:
        """Warm source metadata from a persisted Overview response cache."""
        date_range = overview_payload.get("available_date_range")
        map_payload = overview_payload.get("map")
        if not isinstance(date_range, Mapping) or not isinstance(map_payload, Mapping):
            return
        currencies = map_payload.get("currencies")
        if not isinstance(currencies, list):
            return
        selected_sources = (
            [str(source).strip() for source in sources if str(source).strip()]
            if sources is not None else ["360Giving"]
        )
        metadata_cache_key = (
            revision,
            tuple(sorted({source.casefold() for source in selected_sources})),
        )
        metadata_cache = getattr(self, "_overview_source_metadata_cache", {})
        metadata_cache[metadata_cache_key] = {
            "date_from": date_range.get("from"),
            "date_to": date_range.get("to"),
            "currencies": [str(value) for value in currencies],
        }
        self._overview_source_metadata_cache = metadata_cache

    def _overview_source_rows(
        self,
        conn: sqlite3.Connection,
        sources: Optional[List[str]] = None,
        *,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        beneficiary_country_code: Optional[str] = None,
        include_connections: bool = False,
        include_evidence: bool = False,
    ) -> List[Dict[str, Any]]:
        """Fetch only the transaction rows in the indexed Overview scope."""
        selected_sources = (
            [str(source).strip() for source in sources if str(source).strip()]
            if sources is not None else ["360Giving"]
        )
        if not selected_sources:
            return []
        conn.row_factory = sqlite3.Row
        placeholders = ", ".join("?" for _ in selected_sources)
        connection_columns = (
            # Connection origins are materialized in grant_overview_facts.
            # Directory funder scopes only need the linked profile name here;
            # selecting raw_grant_data used to pull hundreds of MB per request.
            "NULL AS raw_grant_data, c.headquarters_country, c.name AS linked_funder_name"
            if include_connections
            else "g.raw_grant_data, NULL AS headquarters_country, NULL AS linked_funder_name"
            if include_evidence
            else "NULL AS raw_grant_data, NULL AS headquarters_country, NULL AS linked_funder_name"
        )
        connection_join = (
            "LEFT JOIN charities AS c ON c.charity_id = g.funding_charity_id"
            if include_connections else ""
        )
        scope_params: list[Any] = []
        scope_from = "FROM grants AS g"
        scope_joins: list[str] = []
        selected_regions = sorted({str(value).strip().casefold() for value in beneficiary_geographies or [] if str(value).strip()})
        if selected_regions:
            terms = ", ".join("?" for _ in selected_regions)
            scope_from = f"""
                FROM (
                    SELECT DISTINCT grant_id
                    FROM grant_beneficiary_terms
                    WHERE term IN ({terms})
                ) AS beneficiary_term
                JOIN grants AS g ON g.grant_id = beneficiary_term.grant_id
            """
            scope_params.extend(selected_regions)
        selected_country_code = str(beneficiary_country_code or "").strip().upper()
        if selected_country_code:
            country_scope = """
                SELECT grant_id
                FROM grant_beneficiary_countries INDEXED BY idx_grant_beneficiary_countries_code
                WHERE country_code = ?
            """
            if scope_from == "FROM grants AS g":
                scope_from = f"""
                    FROM ({country_scope}) AS beneficiary_country
                    JOIN grants AS g ON g.grant_id = beneficiary_country.grant_id
                """
            else:
                scope_joins.append(
                    f"JOIN ({country_scope}) AS beneficiary_country ON beneficiary_country.grant_id = g.grant_id"
                )
            scope_params.append(selected_country_code)
        selected_programmes = sorted({str(value).strip().casefold() for value in programme_areas or [] if str(value).strip()})
        if selected_programmes:
            values = ", ".join("?" for _ in selected_programmes)
            programme_scope = f"""
                SELECT DISTINCT grant_id
                FROM grant_programme_categories
                WHERE programme_area COLLATE NOCASE IN ({values})
            """
            if scope_from == "FROM grants AS g":
                scope_from = f"""
                    FROM ({programme_scope}) AS programme_category
                    JOIN grants AS g ON g.grant_id = programme_category.grant_id
                """
            else:
                scope_joins.append(
                    f"JOIN ({programme_scope}) AS programme_category ON programme_category.grant_id = g.grant_id"
                )
            scope_params.extend(selected_programmes)
        query = f"""
            SELECT g.grant_id, g.amount, g.amount_eur, g.exchange_rate,
                   g.exchange_rate_date, g.exchange_rate_source, g.conversion_status,
                   g.currency, g.date,
                   g.beneficiary_geography_normalized, g.beneficiary_geography,
                   g.programme_area_source, g.programme_area_inferred,
                   g.programme_area_scores, g.funding_name, g.funding_org_source_id,
                   g.funding_charity_id, g.recipient_name, g.recipient_org_source_id,
                   g.recipient_charity_id, g.source, g.source_url, g.source_record_id,
                   g.description, {connection_columns}
            {scope_from}
            {' '.join(scope_joins)}
            {connection_join}
            WHERE g.source IN ({placeholders})
        """
        params: list[Any] = [*scope_params, *selected_sources]
        requested_currency = str(currency or "").strip().upper()
        if requested_currency and requested_currency != "AUTO":
            query += " AND UPPER(TRIM(g.currency)) = ?"
            params.append(requested_currency)
        if date_from:
            query += " AND g.date >= ?"
            params.append(date_from)
        if date_to:
            # The source stores ISO dates and occasional ISO timestamps. An
            # exclusive next-day boundary retains all times on the chosen end
            # date while allowing SQLite to use (source, date).
            exclusive_end = (datetime.strptime(date_to, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
            query += " AND g.date < ?"
            params.append(exclusive_end)
        if donor and str(donor).strip():
            query += " AND LOWER(COALESCE(g.funding_name, '')) LIKE ?"
            params.append(f"%{str(donor).strip().casefold()}%")
        if recipient and str(recipient).strip():
            query += " AND LOWER(COALESCE(g.recipient_name, '')) LIKE ?"
            params.append(f"%{str(recipient).strip().casefold()}%")
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _get_grant_overview_from_facts(
        self,
        conn: sqlite3.Connection,
        *,
        currency: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
        beneficiary_geographies: Optional[List[str]],
        programme_areas: Optional[List[str]],
        donor: Optional[str],
        recipient: Optional[str],
        sources: Optional[List[str]],
        granularity: str,
        include_connections: bool,
        source_metadata: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Aggregate the dashboard from narrow, pre-normalized grant facts.

        The previous request path materialized every raw grant as a Python dict
        and repeatedly decoded its geography/programme JSON. This path scans a
        compact derived table once, then asks SQLite only for bounded grouped
        result sets (countries, periods and programme categories).
        """
        selected_sources = (
            [str(source).strip() for source in sources if str(source).strip()]
            if sources is not None else ["360Giving"]
        )
        requested_currency = str(currency or "").strip().upper() or None
        auto_converted_eur = requested_currency in {None, "AUTO"}
        source_currency_filter = None if auto_converted_eur else requested_currency
        selected_currency = "EUR" if auto_converted_eur else source_currency_filter
        available_currencies = list(source_metadata.get("currencies") or [])
        status = "available"
        if source_currency_filter and source_currency_filter not in available_currencies:
            status = "unsupported_currency"
        elif not available_currencies:
            status = "no_qualifying_records"
            selected_currency = None

        where: List[str] = []
        params: List[Any] = []
        if selected_sources:
            placeholders = ", ".join("?" for _ in selected_sources)
            where.append(f"f.source_namespace IN ({placeholders})")
            params.extend(selected_sources)
        else:
            where.append("0")
        if source_currency_filter:
            where.append("f.currency = ?")
            params.append(source_currency_filter)
        if date_from:
            where.append("f.award_date >= ?")
            params.append(date_from)
        if date_to:
            where.append("f.award_date <= ?")
            params.append(date_to)
        donor_key = normalize_organization_name(donor)
        if donor_key:
            where.append("INSTR(f.funding_name_normalized, ?) > 0")
            params.append(donor_key)
        recipient_key = normalize_organization_name(recipient)
        if recipient_key:
            where.append("INSTR(f.recipient_name_normalized, ?) > 0")
            params.append(recipient_key)
        selected_regions = sorted({
            str(value).strip().casefold()
            for value in beneficiary_geographies or [] if str(value).strip()
        })
        if selected_regions:
            placeholders = ", ".join("?" for _ in selected_regions)
            where.append(f"""EXISTS (
                SELECT 1 FROM grant_beneficiary_terms AS term
                WHERE term.grant_id = f.grant_id AND term.term IN ({placeholders})
            )""")
            params.extend(selected_regions)
        selected_programmes = sorted({
            str(value).strip().casefold()
            for value in programme_areas or [] if str(value).strip()
        })
        if selected_programmes:
            placeholders = ", ".join("?" for _ in selected_programmes)
            where.append(f"""EXISTS (
                SELECT 1 FROM grant_programme_categories AS category
                WHERE category.grant_id = f.grant_id
                  AND category.programme_area COLLATE NOCASE IN ({placeholders})
            )""")
            params.extend(selected_programmes)

        if auto_converted_eur:
            monetary_minor = "f.eur_amount_minor"
            monetary_status = "f.eur_amount_status"
            monetary_eligible = "(f.conversion_status IN ('native_eur','ecb_award_date','ecb_previous_business_day') AND f.eur_amount_status <> 'missing')"
        else:
            monetary_minor = "f.original_amount_minor"
            monetary_status = "f.original_amount_status"
            monetary_eligible = "1"

        conn.execute("DROP TABLE IF EXISTS temp.grant_overview_scope")
        conn.execute(
            f"""
            CREATE TEMP TABLE grant_overview_scope AS
            SELECT f.*,
                   {monetary_minor} AS monetary_minor,
                   {monetary_status} AS monetary_status,
                   CASE WHEN {monetary_eligible} THEN 1 ELSE 0 END AS monetary_eligible
            FROM grant_overview_facts AS f
            WHERE {' AND '.join(where)}
            """,
            params,
        )
        conn.execute(
            "CREATE UNIQUE INDEX temp.idx_grant_overview_scope_grant ON grant_overview_scope(grant_id)"
        )
        conn.execute(
            "CREATE INDEX temp.idx_grant_overview_scope_date ON grant_overview_scope(award_date)"
        )

        summary = conn.execute(
            """
            SELECT COUNT(*) AS total_scoped,
                   COALESCE(SUM(country_count > 0), 0) AS known_count,
                   COALESCE(SUM(country_count), 0) AS association_count,
                   COALESCE(SUM(country_count > 1), 0) AS multi_country_count,
                   COALESCE(SUM(monetary_eligible = 0), 0) AS conversion_excluded_count,
                   COALESCE(SUM(CASE WHEN monetary_eligible = 1
                                          AND monetary_status IN ('valid','zero')
                                     THEN monetary_minor ELSE 0 END), 0) AS valid_minor,
                   COALESCE(SUM(CASE WHEN country_count > 1
                                          AND monetary_eligible = 1
                                          AND monetary_status IN ('valid','zero')
                                     THEN 1 ELSE 0 END), 0) AS excluded_multi_count,
                   COALESCE(SUM(CASE WHEN country_count > 1
                                          AND monetary_eligible = 1
                                          AND monetary_status IN ('valid','zero')
                                     THEN monetary_minor ELSE 0 END), 0) AS excluded_multi_minor,
                   COALESCE(SUM(CASE WHEN country_count = 1
                                          AND monetary_eligible = 1
                                          AND monetary_status NOT IN ('valid','zero')
                                     THEN 1 ELSE 0 END), 0) AS invalid_country_amount_count
            FROM grant_overview_scope
            """
        ).fetchone()
        total_scoped = int(summary["total_scoped"] or 0)
        known_count = int(summary["known_count"] or 0)
        unknown_count = total_scoped - known_count
        association_count = int(summary["association_count"] or 0)
        multi_country_count = int(summary["multi_country_count"] or 0)
        conversion_excluded_count = int(summary["conversion_excluded_count"] or 0)
        country_coverage = round(known_count / total_scoped * 100, 2) if total_scoped else 0.0

        country_rows = conn.execute(
            """
            SELECT country.country_code, country.country_name,
                   COUNT(*) AS grant_count,
                   COUNT(DISTINCT CASE WHEN facts.source_funder_key IS NOT NULL
                                       THEN facts.source_funder_key END) AS distinct_funders,
                   COUNT(DISTINCT CASE WHEN scope.recipient_name <> 'Unnamed recipient'
                                       THEN scope.recipient_name END) AS distinct_recipients,
                   COALESCE(SUM(CASE WHEN scope.country_count = 1
                                          AND scope.monetary_eligible = 1
                                          AND scope.monetary_status IN ('valid','zero')
                                     THEN scope.monetary_minor ELSE 0 END), 0) AS total_minor,
                   COALESCE(SUM(CASE WHEN scope.country_count = 1
                                          AND scope.monetary_eligible = 1
                                          AND scope.monetary_status IN ('valid','zero')
                                     THEN 1 ELSE 0 END), 0) AS funding_grant_count,
                   COALESCE(SUM(CASE WHEN scope.country_count > 1
                                          AND scope.monetary_eligible = 1
                                     THEN 1 ELSE 0 END), 0) AS excluded_multi,
                   COALESCE(SUM(CASE WHEN scope.country_count = 1
                                          AND scope.monetary_eligible = 1
                                          AND scope.monetary_status NOT IN ('valid','zero')
                                     THEN 1 ELSE 0 END), 0) AS excluded_invalid
            FROM grant_overview_scope AS scope
            JOIN grant_beneficiary_countries AS country ON country.grant_id = scope.grant_id
            LEFT JOIN grant_source_funder_facts AS facts
              ON facts.grant_id = scope.grant_id AND facts.country_code = country.country_code
            GROUP BY country.country_code, country.country_name
            """
        ).fetchall()

        def ranked_by_country(sql: str) -> Dict[str, List[Dict[str, Any]]]:
            ranked: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for row in conn.execute(sql).fetchall():
                ranked[str(row["country_code"])].append({
                    "name": str(row["name"]), "count": int(row["item_count"]),
                })
            return ranked

        top_funders = ranked_by_country(
            """
            WITH grouped AS (
              SELECT country.country_code, facts.source_funder_key AS item_key,
                     MAX(facts.display_name) AS name, COUNT(*) AS item_count
              FROM grant_overview_scope AS scope
              JOIN grant_beneficiary_countries AS country ON country.grant_id = scope.grant_id
              JOIN grant_source_funder_facts AS facts
                ON facts.grant_id = scope.grant_id AND facts.country_code = country.country_code
              GROUP BY country.country_code, facts.source_funder_key
            ), ranked AS (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY country_code ORDER BY item_count DESC, LOWER(name)
              ) AS item_rank FROM grouped
            )
            SELECT country_code, name, item_count FROM ranked WHERE item_rank <= 3
            ORDER BY country_code, item_rank
            """
        )
        top_recipients = ranked_by_country(
            """
            WITH grouped AS (
              SELECT country.country_code, scope.recipient_name AS name,
                     COUNT(*) AS item_count
              FROM grant_overview_scope AS scope
              JOIN grant_beneficiary_countries AS country ON country.grant_id = scope.grant_id
              WHERE scope.recipient_name <> 'Unnamed recipient'
              GROUP BY country.country_code, scope.recipient_name
            ), ranked AS (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY country_code ORDER BY item_count DESC, LOWER(name)
              ) AS item_rank FROM grouped
            )
            SELECT country_code, name, item_count FROM ranked WHERE item_rank <= 3
            ORDER BY country_code, item_rank
            """
        )
        top_programmes = ranked_by_country(
            """
            WITH grouped AS (
              SELECT country.country_code, category.programme_area AS name,
                     COUNT(*) AS item_count
              FROM grant_overview_scope AS scope
              JOIN grant_beneficiary_countries AS country ON country.grant_id = scope.grant_id
              JOIN grant_programme_categories AS category ON category.grant_id = scope.grant_id
              GROUP BY country.country_code, category.programme_area
            ), ranked AS (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY country_code ORDER BY item_count DESC, LOWER(name)
              ) AS item_rank FROM grouped
            )
            SELECT country_code, name, item_count FROM ranked WHERE item_rank <= 3
            ORDER BY country_code, item_rank
            """
        )

        map_items = []
        for row in country_rows:
            code = str(row["country_code"])
            funding_grant_count = int(row["funding_grant_count"] or 0)
            map_items.append({
                "region_or_country_code": code,
                "region_or_country_name": str(row["country_name"]),
                "grant_count": int(row["grant_count"]),
                "total_amount": _minor_units_to_amount(int(row["total_minor"] or 0)) if funding_grant_count else None,
                "currency": selected_currency,
                "distinct_funders": int(row["distinct_funders"] or 0),
                "distinct_recipients": int(row["distinct_recipients"] or 0),
                "top_programme_areas": top_programmes.get(code, []),
                "top_funders": top_funders.get(code, []),
                "top_recipients": top_recipients.get(code, []),
                "original_geographies": [str(row["country_name"])],
                "funding_grant_count": funding_grant_count,
                "excluded_multi_country_grant_count": int(row["excluded_multi"] or 0),
                "excluded_invalid_amount_grant_count": int(row["excluded_invalid"] or 0),
            })
        map_items.sort(key=lambda item: (-item["grant_count"], item["region_or_country_name"]))

        connections: List[Dict[str, Any]] = []
        connection_grant_count = connection_no_headquarters_count = connection_same_country_count = 0
        if include_connections:
            connection_counts = conn.execute(
                """
                SELECT COUNT(DISTINCT CASE WHEN scope.origin_country_code IS NOT NULL
                                                AND scope.origin_country_code <> country.country_code
                                           THEN scope.grant_id END) AS connected,
                       COUNT(DISTINCT CASE WHEN scope.origin_country_code IS NULL
                                           THEN scope.grant_id END) AS no_origin,
                       COUNT(DISTINCT CASE WHEN scope.origin_country_code = country.country_code
                                           THEN scope.grant_id END) AS same_country
                FROM grant_overview_scope AS scope
                JOIN grant_beneficiary_countries AS country ON country.grant_id = scope.grant_id
                """
            ).fetchone()
            connection_grant_count = int(connection_counts["connected"] or 0)
            connection_no_headquarters_count = int(connection_counts["no_origin"] or 0)
            connection_same_country_count = int(connection_counts["same_country"] or 0)
            connection_rows = conn.execute(
                """
                SELECT scope.origin_country_code, MAX(scope.origin_country_name) AS origin_country_name,
                       country.country_code AS destination_country_code,
                       MAX(country.country_name) AS destination_country_name,
                       COUNT(DISTINCT scope.grant_id) AS grant_count,
                       MAX(scope.origin_source) AS origin_source
                FROM grant_overview_scope AS scope
                JOIN grant_beneficiary_countries AS country ON country.grant_id = scope.grant_id
                WHERE scope.origin_country_code IS NOT NULL
                  AND scope.origin_country_code <> country.country_code
                GROUP BY scope.origin_country_code, country.country_code
                ORDER BY grant_count DESC, origin_country_name, destination_country_name
                """
            ).fetchall()
            connection_funders: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
            for row in conn.execute(
                """
                SELECT scope.origin_country_code, country.country_code AS destination_country_code,
                       facts.display_name, COUNT(*) AS item_count
                FROM grant_overview_scope AS scope
                JOIN grant_beneficiary_countries AS country ON country.grant_id = scope.grant_id
                JOIN grant_source_funder_facts AS facts
                  ON facts.grant_id = scope.grant_id AND facts.country_code = country.country_code
                WHERE scope.origin_country_code IS NOT NULL
                  AND scope.origin_country_code <> country.country_code
                GROUP BY scope.origin_country_code, country.country_code, facts.source_funder_key, facts.display_name
                """
            ).fetchall():
                connection_funders[(str(row["origin_country_code"]), str(row["destination_country_code"]))][str(row["display_name"])] = int(row["item_count"])
            for row in connection_rows:
                key = (str(row["origin_country_code"]), str(row["destination_country_code"]))
                connections.append({
                    "origin_country_code": key[0],
                    "origin_country_name": str(row["origin_country_name"]),
                    "destination_country_code": key[1],
                    "destination_country_name": str(row["destination_country_name"]),
                    "grant_count": int(row["grant_count"]),
                    "top_funders": _top_counter_items(connection_funders[key]),
                    "origin_sources": [str(row["origin_source"])] if row["origin_source"] else [],
                })

        applied_filters = {
            "currency": "auto" if auto_converted_eur else source_currency_filter,
            "currency_mode": "auto_converted_eur" if auto_converted_eur else "source_currency",
            "display_currency": selected_currency,
            "date_from": date_from, "date_to": date_to,
            "beneficiary_geographies": beneficiary_geographies or [],
            "programme_areas": programme_areas or [],
            "donor": str(donor or "").strip() or None,
            "recipient": str(recipient or "").strip() or None,
            "sources": sources if sources is not None else ["360Giving"],
            "granularity": granularity,
            "include_connections": include_connections,
        }
        map_limitations = [GRANT_SCOPE_NOTE]
        if auto_converted_eur:
            map_limitations.append(
                "Auto converts eligible source amounts to EUR using stored ECB daily reference rates. "
                "The original source amount and currency remain unchanged."
            )
            if conversion_excluded_count:
                map_limitations.append(
                    f"{conversion_excluded_count} grants are excluded from EUR funding totals because no valid "
                    "ECB conversion is available for their source amount and award date."
                )
        map_status = "available" if total_scoped else "no_data"
        if total_scoped and not known_count:
            map_status = "no_geography"
        map_payload = {
            "status": map_status, "geographic_dimension": "beneficiary_location", "items": map_items,
            "known_geography_count": known_count, "unknown_geography_count": unknown_count,
            "coverage_percentage": country_coverage, "currencies": available_currencies,
            "selected_currency": selected_currency,
            "funding_status": status if status != "available" else "available",
            "funding_mode_available": any(item["funding_grant_count"] for item in map_items),
            "grant_country_association_count": association_count,
            "multi_country_grant_count": multi_country_count,
            "funding_excluded_multi_country_count": int(summary["excluded_multi_count"] or 0),
            "funding_excluded_multi_country_amount": _minor_units_to_amount(int(summary["excluded_multi_minor"] or 0)),
            "funding_excluded_currency_count": conversion_excluded_count,
            "funding_excluded_invalid_amount_count": int(summary["invalid_country_amount_count"] or 0),
            "connections": connections, "connection_grant_count": connection_grant_count,
            "connection_excluded_no_headquarters_count": connection_no_headquarters_count,
            "connection_same_country_count": connection_same_country_count,
            "minimum_coverage_threshold": 0.0,
            "metadata": {
                "data_mode": "derived_from_cached_source", "source": ["360Giving"],
                "generated_at": _utc_now(), "record_count": total_scoped,
                "coverage": country_coverage / 100 if total_scoped else None,
                "derivation": "Filtered stored 360Giving grants grouped only by grant beneficiary geography.",
                "limitations": map_limitations, "filters_applied": applied_filters,
            },
        }

        dated = conn.execute(
            """
            SELECT MIN(award_date) AS first_date, MAX(award_date) AS last_date
            FROM grant_overview_scope
            WHERE monetary_eligible = 1 AND award_date IS NOT NULL
            """
        ).fetchone()
        if dated and dated["first_date"] and dated["last_date"]:
            trend_start = date_from or str(dated["first_date"])
            trend_end = date_to or str(dated["last_date"])
            months_in_range = (
                (int(trend_end[:4]) - int(trend_start[:4])) * 12
                + int(trend_end[5:7]) - int(trend_start[5:7]) + 1
            )
            resolved_granularity = (
                "monthly" if granularity == "auto" and months_in_range <= 24
                else "yearly" if granularity == "auto" else granularity
            )
            period_expression = "SUBSTR(award_date, 1, 4)" if resolved_granularity == "yearly" else "SUBSTR(award_date, 1, 7)"
            period_rows = {
                str(row["period_key"]): row for row in conn.execute(
                    f"""
                    SELECT {period_expression} AS period_key, COUNT(*) AS source_count,
                           COALESCE(SUM(monetary_status IN ('valid','zero')), 0) AS grant_count,
                           COALESCE(SUM(CASE WHEN monetary_status IN ('valid','zero')
                                             THEN monetary_minor ELSE 0 END), 0) AS total_minor,
                           COALESCE(SUM(CASE WHEN monetary_status IN ('valid','zero') AND country_count > 0
                                             THEN 1 ELSE 0 END), 0) AS mapped,
                           COALESCE(SUM(CASE WHEN monetary_status IN ('valid','zero') AND country_count = 0
                                             THEN 1 ELSE 0 END), 0) AS unmapped
                    FROM grant_overview_scope
                    WHERE monetary_eligible = 1 AND award_date IS NOT NULL
                    GROUP BY period_key
                    """
                ).fetchall()
            }
            trend_items = []
            for label in self._overview_period_labels(trend_start, trend_end, resolved_granularity):
                values = period_rows.get(label)
                if not values:
                    trend_items.append({"month": label, "grant_count": None, "source_record_count": 0, "total_amount": None, "coverage_status": "unknown", "mapped_grant_count": 0, "unmapped_grant_count": 0})
                elif int(values["grant_count"] or 0):
                    trend_items.append({"month": label, "grant_count": int(values["grant_count"]), "source_record_count": int(values["source_count"]), "total_amount": _minor_units_to_amount(int(values["total_minor"] or 0)), "coverage_status": "observed", "mapped_grant_count": int(values["mapped"] or 0), "unmapped_grant_count": int(values["unmapped"] or 0)})
                else:
                    trend_items.append({"month": label, "grant_count": None, "source_record_count": int(values["source_count"]), "total_amount": None, "coverage_status": "partial", "mapped_grant_count": 0, "unmapped_grant_count": 0})
            trend_excluded = conn.execute(
                """
                SELECT COALESCE(SUM(award_date_status = 'missing'), 0) AS missing_date,
                       COALESCE(SUM(award_date_status = 'invalid'), 0) AS invalid_date,
                       COALESCE(SUM(award_date IS NOT NULL AND monetary_status = 'missing'), 0) AS missing_amount,
                       COALESCE(SUM(award_date IS NOT NULL AND monetary_status = 'invalid'), 0) AS invalid_amount,
                       COALESCE(SUM(award_date IS NOT NULL AND monetary_status = 'negative'), 0) AS negative_amount,
                       COALESCE(SUM(award_date IS NOT NULL AND monetary_status = 'zero'), 0) AS zero_amount,
                       MAX(CASE WHEN monetary_status IN ('valid','zero') THEN monetary_minor END) AS maximum_minor
                FROM grant_overview_scope WHERE monetary_eligible = 1
                """
            ).fetchone()
            trends_payload = {
                "status": "available" if any(item["grant_count"] is not None for item in trend_items) else "no_qualifying_records",
                "currency": selected_currency, "available_currencies": available_currencies,
                "date_basis": "award_date", "granularity": resolved_granularity,
                "period": {"from": trend_start[:7], "to": trend_end[:7], "months": months_in_range, "anchor": "selected_filter_range"},
                "items": trend_items,
                "excluded": {"missing_date": int(trend_excluded["missing_date"] or 0), "invalid_date": int(trend_excluded["invalid_date"] or 0), "outside_period": 0, "unsupported_currency": 0, "currency_filtered": 0, "unsupported_source": 0, "missing_amount": int(trend_excluded["missing_amount"] or 0), "invalid_amount": int(trend_excluded["invalid_amount"] or 0), "negative_amount": int(trend_excluded["negative_amount"] or 0)},
                "zero_amount_count": int(trend_excluded["zero_amount"] or 0),
                "latest_award_date": str(dated["last_date"]), "last_refreshed_at": None,
                "source": ["360Giving"], "data_mode": "derived_from_cached_source",
                "amount_policy": _amount_policy(trend_excluded["maximum_minor"]),
                "scope": {"coverage_note": GRANT_SCOPE_NOTE},
            }
        else:
            resolved_granularity = "monthly" if granularity == "auto" else granularity
            trends_payload = {
                "status": "no_qualifying_records", "currency": selected_currency,
                "available_currencies": available_currencies, "date_basis": "award_date",
                "granularity": resolved_granularity, "period": None, "items": [],
                "excluded": {"missing_date": 0, "invalid_date": 0, "missing_amount": 0, "invalid_amount": 0, "negative_amount": 0, "unsupported_currency": 0, "currency_filtered": 0, "unsupported_source": 0, "outside_period": 0},
                "zero_amount_count": 0, "latest_award_date": None, "last_refreshed_at": None,
                "source": ["360Giving"], "data_mode": "derived_from_cached_source",
                "amount_policy": _amount_policy(), "scope": {"coverage_note": GRANT_SCOPE_NOTE},
            }

        theme_summary = conn.execute(
            """
            SELECT COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero')), 0) AS qualifying,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero') AND programme_provenance <> 'unclassified'), 0) AS classified,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero') AND programme_provenance = 'unclassified'), 0) AS unclassified,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero') AND programme_provenance = 'source'), 0) AS source_classified,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero') AND programme_provenance = 'inferred'), 0) AS inferred_classified,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero') AND programme_category_count > 1), 0) AS multiple_categories,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero') AND invalid_source_label = 1), 0) AS invalid_source_labels,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status IN ('valid','zero') AND low_confidence_inference = 1), 0) AS low_confidence,
                   COALESCE(SUM(CASE WHEN monetary_eligible = 1 AND monetary_status IN ('valid','zero') THEN monetary_minor ELSE 0 END), 0) AS qualifying_minor,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status = 'zero'), 0) AS zero_amount,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status = 'missing'), 0) AS missing_amount,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status = 'invalid'), 0) AS invalid_amount,
                   COALESCE(SUM(monetary_eligible = 1 AND monetary_status = 'negative'), 0) AS negative_amount,
                   MAX(CASE WHEN monetary_eligible = 1 AND monetary_status IN ('valid','zero') THEN monetary_minor END) AS maximum_minor
            FROM grant_overview_scope
            """
        ).fetchone()
        theme_rows = conn.execute(
            """
            WITH ranked AS (
              SELECT scope.grant_id, scope.monetary_minor, scope.programme_category_count,
                     scope.programme_provenance, category.programme_area,
                     ROW_NUMBER() OVER (PARTITION BY scope.grant_id ORDER BY category.programme_area) AS category_rank
              FROM grant_overview_scope AS scope
              JOIN grant_programme_categories AS category ON category.grant_id = scope.grant_id
              WHERE scope.monetary_eligible = 1 AND scope.monetary_status IN ('valid','zero')
            )
            SELECT programme_area, COUNT(*) AS distinct_count,
                   SUM(1.0 / programme_category_count) AS weighted_count,
                   SUM(CAST(monetary_minor / programme_category_count AS INTEGER)
                       + CASE WHEN category_rank <= (monetary_minor % programme_category_count) THEN 1 ELSE 0 END) AS allocated_minor,
                   COALESCE(SUM(programme_provenance = 'source'), 0) AS source_count,
                   COALESCE(SUM(programme_provenance = 'inferred'), 0) AS inferred_count,
                   COALESCE(SUM(programme_provenance = 'unclassified'), 0) AS unclassified_count
            FROM ranked GROUP BY programme_area
            """
        ).fetchall()
        theme_items = [{
            "programme_area": str(row["programme_area"]),
            "distinct_grant_count": int(row["distinct_count"]),
            "weighted_grant_count": float(Decimal(str(row["weighted_count"] or 0)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            "allocated_amount": _minor_units_to_amount(int(row["allocated_minor"] or 0)),
            "source_classified_grant_count": int(row["source_count"] or 0),
            "inferred_classified_grant_count": int(row["inferred_count"] or 0),
            "unclassified_grant_count": int(row["unclassified_count"] or 0),
        } for row in theme_rows]
        theme_items.sort(key=lambda item: (-item["allocated_amount"], item["programme_area"]))
        qualifying = int(theme_summary["qualifying"] or 0)
        classified = int(theme_summary["classified"] or 0)
        source_classified = int(theme_summary["source_classified"] or 0)
        inferred_classified = int(theme_summary["inferred_classified"] or 0)
        denominator = qualifying or 1
        allocated_minor = sum(int(row["allocated_minor"] or 0) for row in theme_rows)
        theme_payload = {
            "status": "available" if qualifying else "no_qualifying_records",
            "currency": selected_currency, "available_currencies": available_currencies,
            "allocation_method": "equal_split_across_available_categories",
            "classification_precedence": ["valid_source_category", "accepted_inferred_category", "unclassified"],
            "inference_confidence_threshold": DEFAULT_REVIEW_THRESHOLD, "items": theme_items,
            "classification_coverage": {
                "qualifying_grant_count": qualifying, "classified_grant_count": classified,
                "unclassified_grant_count": int(theme_summary["unclassified"] or 0),
                "classified_percentage": round(classified / denominator * 100, 2),
                "source_classified_grant_count": source_classified,
                "inferred_classified_grant_count": inferred_classified,
                "source_percentage": round(source_classified / denominator * 100, 2),
                "inferred_percentage": round(inferred_classified / denominator * 100, 2),
                "multiple_programme_area_grant_count": int(theme_summary["multiple_categories"] or 0),
                "invalid_source_label_count": int(theme_summary["invalid_source_labels"] or 0),
                "low_confidence_inference_count": int(theme_summary["low_confidence"] or 0),
            },
            "qualifying_amount": _minor_units_to_amount(int(theme_summary["qualifying_minor"] or 0)),
            "allocated_amount": _minor_units_to_amount(allocated_minor),
            "excluded": {"missing_amount": int(theme_summary["missing_amount"] or 0), "invalid_amount": int(theme_summary["invalid_amount"] or 0), "negative_amount": int(theme_summary["negative_amount"] or 0)},
            "zero_amount_count": int(theme_summary["zero_amount"] or 0), "last_refreshed_at": None,
            "source": ["360Giving"], "data_mode": "derived_from_cached_source",
            "amount_policy": _amount_policy(theme_summary["maximum_minor"]),
            "scope": {"coverage_note": GRANT_SCOPE_NOTE},
        }
        result_status = status if status != "available" else ("available" if total_scoped else "no_data")
        return {
            "status": result_status,
            "kpis": {
                "awarded_funding": _minor_units_to_amount(int(summary["valid_minor"] or 0)) if selected_currency else None,
                "currency": selected_currency, "grants_monitored": total_scoped,
                "country_coverage_percentage": country_coverage,
                "mapped_grant_count": known_count, "unmapped_grant_count": unknown_count,
                "programme_coverage_percentage": theme_payload["classification_coverage"]["classified_percentage"],
                "classified_grant_count": classified, "qualifying_programme_grant_count": qualifying,
            },
            "map": map_payload, "trends": trends_payload, "themes": theme_payload,
            "available_date_range": {"from": source_metadata.get("date_from"), "to": source_metadata.get("date_to")},
            "applied_filters": applied_filters,
        }

    async def get_grant_overview(
        self,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
        granularity: str = "auto",
        include_connections: bool = False,
    ) -> Dict[str, Any]:
        """Aggregate every Overview card from one server-side grant population.

        This intentionally keeps organization-directory metrics out of the result:
        beneficiary, programme, donor, recipient and award-date filters describe
        grants, not annual organization income or expenditure.
        """
        if granularity not in {"auto", "monthly", "yearly"}:
            raise ValueError("granularity must be auto, monthly, or yearly")
        cache_key = self._overview_cache_key(
            currency=currency, date_from=date_from, date_to=date_to,
            beneficiary_geographies=beneficiary_geographies, programme_areas=programme_areas,
            donor=donor, recipient=recipient, sources=sources, granularity=granularity,
            include_connections=include_connections,
        )
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            revision = self._ensure_overview_indexes(conn)
            cached = self._load_overview_cache(conn, cache_key, revision)
            if cached is not None:
                # Older cached payloads deliberately withheld country values
                # below 30% coverage. Recalculate just those scopes now that
                # the map always shows the observed mapped countries.
                cached_map = cached.get("map") if isinstance(cached, Mapping) else None
                if not isinstance(cached_map, Mapping) or cached_map.get("status") != "low_coverage":
                    displayed_sources = sources if sources is not None else ["360Giving"]
                    if isinstance(cached.get("applied_filters"), dict):
                        cached["applied_filters"]["sources"] = displayed_sources
                    if isinstance(cached_map, dict):
                        metadata = cached_map.get("metadata")
                        if isinstance(metadata, dict) and isinstance(metadata.get("filters_applied"), dict):
                            metadata["filters_applied"]["sources"] = displayed_sources
                    self._remember_overview_source_metadata(sources, revision, cached)
                    return cached
            source_metadata = self._overview_source_metadata(conn, sources, revision=revision)
            fact_count = conn.execute("SELECT COUNT(*) FROM grant_overview_facts").fetchone()[0]
            grant_count = conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
            if fact_count == grant_count:
                result = self._get_grant_overview_from_facts(
                    conn,
                    currency=currency, date_from=date_from, date_to=date_to,
                    beneficiary_geographies=beneficiary_geographies,
                    programme_areas=programme_areas, donor=donor, recipient=recipient,
                    sources=sources, granularity=granularity,
                    include_connections=include_connections,
                    source_metadata=source_metadata,
                )
                try:
                    self._store_overview_cache(conn, cache_key, revision, result)
                except sqlite3.Error as exc:
                    logger.warning("Overview result was returned without caching: %s", exc)
                return result
            source_rows = self._overview_source_rows(
                conn, sources, currency=currency, date_from=date_from, date_to=date_to,
                beneficiary_geographies=beneficiary_geographies, programme_areas=programme_areas,
                donor=donor, recipient=recipient, include_connections=include_connections,
            )
        finally:
            conn.close()

        available_date_range = {
            "from": source_metadata["date_from"],
            "to": source_metadata["date_to"],
        }
        available_currencies = source_metadata["currencies"]
        requested_currency = str(currency or "").strip().upper() or None
        # No filter is the product's Auto mode: source values are compared only
        # through their stored EUR conversion. A concrete selector value still
        # means "original source currency only" (including native EUR).
        auto_converted_eur = requested_currency in {None, "AUTO"}
        source_currency_filter = None if auto_converted_eur else requested_currency
        selected_currency = "EUR" if auto_converted_eur else source_currency_filter
        status = "available"
        if source_currency_filter and source_currency_filter not in available_currencies:
            status = "unsupported_currency"
        elif not available_currencies:
            status = "no_qualifying_records"
            selected_currency = None

        selected_regions = {str(value).strip().casefold() for value in (beneficiary_geographies or []) if str(value).strip()}
        selected_programmes = {str(value).strip().casefold() for value in (programme_areas or []) if str(value).strip()}
        donor_query = str(donor or "").strip().casefold()
        recipient_query = str(recipient or "").strip().casefold()
        scoped_rows: List[Dict[str, Any]] = []
        date_excluded = 0
        for row in source_rows:
            award_date = self._overview_award_date(row["date"])
            row["award_date"] = award_date
            countries = _beneficiary_countries(
                row["beneficiary_geography_normalized"], row["beneficiary_geography"]
            )
            row["beneficiary_countries"] = countries
            row["programme_categories"] = _accepted_programme_categories(
                row["programme_area_source"], row["programme_area_inferred"], row["programme_area_scores"]
            )
            if donor_query and donor_query not in str(row["funding_name"] or "").casefold():
                continue
            if recipient_query and recipient_query not in str(row["recipient_name"] or "").casefold():
                continue
            if selected_regions and not _matches_funding_regions(
                row["beneficiary_geography_normalized"], row["beneficiary_geography"], countries, selected_regions
            ):
                continue
            if selected_programmes and not selected_programmes.intersection(
                str(category).casefold() for category in row["programme_categories"]
            ):
                continue
            if date_from or date_to:
                if not award_date:
                    date_excluded += 1
                    continue
                if date_from and award_date < date_from:
                    date_excluded += 1
                    continue
                if date_to and award_date > date_to:
                    date_excluded += 1
                    continue
            if source_currency_filter and str(row["currency"] or "").strip().upper() != source_currency_filter:
                continue
            scoped_rows.append(row)

        applied_filters = {
            "currency": "auto" if auto_converted_eur else source_currency_filter,
            "currency_mode": "auto_converted_eur" if auto_converted_eur else "source_currency",
            "display_currency": selected_currency,
            "date_from": date_from,
            "date_to": date_to,
            "beneficiary_geographies": beneficiary_geographies or [],
            "programme_areas": programme_areas or [],
            "donor": str(donor or "").strip() or None,
            "recipient": str(recipient or "").strip() or None,
            "sources": sources if sources is not None else ["360Giving"],
            "granularity": granularity,
            "include_connections": include_connections,
        }

        # Shared map aggregation: beneficiary geography remains grant-side only.
        # Auto uses only already backfilled ECB-derived EUR amounts; a native
        # currency mode never mixes source denominations.
        if status != "available":
            scoped_rows = []
        for row in scoped_rows:
            row["monetary_amount"] = row["amount_eur"] if auto_converted_eur else row["amount"]
            row["monetary_eligible"] = bool(
                row["amount_eur"] is not None
                and str(row.get("conversion_status") or "")
                in {"native_eur", "ecb_award_date", "ecb_previous_business_day"}
            ) if auto_converted_eur else True
        monetary_rows = [row for row in scoped_rows if row["monetary_eligible"]]
        conversion_excluded_count = len(scoped_rows) - len(monetary_rows) if auto_converted_eur else 0

        map_aggregates: Dict[str, Dict[str, Any]] = {}
        connection_aggregates: Dict[Tuple[str, str], Dict[str, Any]] = {}
        known_count = 0
        association_count = 0
        multi_country_count = 0
        excluded_multi_minor_units = 0
        excluded_multi_ids: set[str] = set()
        excluded_invalid_amount_ids: set[str] = set()
        connection_grant_ids: set[str] = set()
        connection_no_headquarters: set[str] = set()
        connection_same_country: set[str] = set()
        for row in scoped_rows:
            countries = row["beneficiary_countries"]
            if not countries:
                continue
            known_count += 1
            association_count += len(countries)
            multi_country = len(countries) > 1
            if multi_country:
                multi_country_count += 1
            if row["monetary_eligible"]:
                amount_status, minor_units = _money_minor_units(row["monetary_amount"])
                if multi_country and amount_status in {"valid", "zero"}:
                    if str(row["grant_id"]) not in excluded_multi_ids:
                        excluded_multi_ids.add(str(row["grant_id"]))
                        excluded_multi_minor_units += minor_units or 0
                elif not multi_country and amount_status not in {"valid", "zero"}:
                    excluded_invalid_amount_ids.add(str(row["grant_id"]))
            origin = None
            origin_source = None
            if include_connections:
                origin, origin_source = _funder_headquarters_country(
                    row["raw_grant_data"], row["headquarters_country"]
                )
                if not origin:
                    connection_no_headquarters.add(str(row["grant_id"]))
            for country in countries:
                code = country["country_code"]
                current = map_aggregates.setdefault(code, {
                    "country_name": country["country_name"], "grant_ids": set(), "funders": Counter(),
                    "funder_labels": {},
                    "recipients": Counter(), "programme_areas": Counter(), "original_geographies": Counter(),
                    "total_minor_units": 0, "funding_grant_ids": set(), "excluded_multi": set(), "excluded_invalid": set(),
                })
                current["grant_ids"].add(row["grant_id"])
                if str(row["funding_name"] or "").strip() or str(row.get("funding_org_source_id") or "").strip():
                    funder_key, _ = _source_entity_identity(
                        role="funder",
                        source=row.get("source"),
                        source_id=row.get("funding_org_source_id"),
                        name=row.get("funding_name"),
                    )
                    current["funders"][funder_key] += 1
                    current["funder_labels"].setdefault(
                        funder_key,
                        _display_source_entity_name(row.get("funding_name"), "Unnamed source funder"),
                    )
                if str(row["recipient_name"] or "").strip():
                    current["recipients"][str(row["recipient_name"]).strip()] += 1
                for category in row["programme_categories"]:
                    current["programme_areas"][category] += 1
                for original in country["original_geographies"]:
                    current["original_geographies"][original] += 1
                if row["monetary_eligible"]:
                    if multi_country:
                        current["excluded_multi"].add(row["grant_id"])
                    elif amount_status in {"valid", "zero"}:
                        current["funding_grant_ids"].add(row["grant_id"])
                        current["total_minor_units"] += minor_units or 0
                    else:
                        current["excluded_invalid"].add(row["grant_id"])
                if origin:
                    if origin["country_code"] == code:
                        connection_same_country.add(str(row["grant_id"]))
                    else:
                        connection = connection_aggregates.setdefault((origin["country_code"], code), {
                            "origin_country_name": origin["country_name"],
                            "destination_country_name": country["country_name"],
                            "grant_ids": set(), "funders": Counter(), "origin_sources": set(),
                        })
                        connection["grant_ids"].add(row["grant_id"])
                        connection_grant_ids.add(str(row["grant_id"]))
                        if str(row["funding_name"] or "").strip():
                            connection["funders"][str(row["funding_name"]).strip()] += 1
                        if origin_source:
                            connection["origin_sources"].add(origin_source)

        total_scoped = len(scoped_rows)
        unknown_count = total_scoped - known_count
        country_coverage = round(known_count / total_scoped * 100, 2) if total_scoped else 0.0
        map_items = [
            {
                "region_or_country_code": code,
                "region_or_country_name": values["country_name"],
                "grant_count": len(values["grant_ids"]),
                "total_amount": _minor_units_to_amount(values["total_minor_units"]) if values["funding_grant_ids"] else None,
                "currency": selected_currency,
                "distinct_funders": len(values["funders"]), "distinct_recipients": len(values["recipients"]),
                "top_programme_areas": _top_counter_items(values["programme_areas"]),
                "top_funders": _top_labeled_counter_items(values["funders"], values["funder_labels"]),
                "top_recipients": _top_counter_items(values["recipients"]),
                "original_geographies": [item["name"] for item in _top_counter_items(values["original_geographies"], 8)],
                "funding_grant_count": len(values["funding_grant_ids"]),
                "excluded_multi_country_grant_count": len(values["excluded_multi"]),
                "excluded_invalid_amount_grant_count": len(values["excluded_invalid"]),
            }
            for code, values in map_aggregates.items()
        ]
        map_items.sort(key=lambda item: (-item["grant_count"], item["region_or_country_name"]))
        connections = [
            {
                "origin_country_code": origin, "origin_country_name": values["origin_country_name"],
                "destination_country_code": destination, "destination_country_name": values["destination_country_name"],
                "grant_count": len(values["grant_ids"]), "top_funders": _top_counter_items(values["funders"]),
                "origin_sources": sorted(values["origin_sources"]),
            }
            for (origin, destination), values in connection_aggregates.items()
        ]
        connections.sort(key=lambda item: (-item["grant_count"], item["origin_country_name"], item["destination_country_name"]))
        map_status = "available" if total_scoped else "no_data"
        if total_scoped and not known_count:
            map_status = "no_geography"
        map_limitations = [GRANT_SCOPE_NOTE]
        if auto_converted_eur:
            map_limitations.append(
                "Auto converts eligible source amounts to EUR using stored ECB daily reference rates. "
                "The original source amount and currency remain unchanged."
            )
            if conversion_excluded_count:
                map_limitations.append(
                    f"{conversion_excluded_count} grants are excluded from EUR funding totals because no valid "
                    "ECB conversion is available for their source amount and award date."
                )
        map_payload = {
            "status": map_status, "geographic_dimension": "beneficiary_location", "items": map_items,
            "known_geography_count": known_count, "unknown_geography_count": unknown_count,
            "coverage_percentage": country_coverage, "currencies": available_currencies,
            "selected_currency": selected_currency,
            "funding_status": status if status != "available" else "available",
            "funding_mode_available": bool(any(item["funding_grant_count"] for item in map_items)),
            "grant_country_association_count": association_count, "multi_country_grant_count": multi_country_count,
            "funding_excluded_multi_country_count": len(excluded_multi_ids),
            "funding_excluded_multi_country_amount": _minor_units_to_amount(excluded_multi_minor_units),
            "funding_excluded_currency_count": conversion_excluded_count,
            "funding_excluded_invalid_amount_count": len(excluded_invalid_amount_ids),
            "connections": connections, "connection_grant_count": len(connection_grant_ids),
            "connection_excluded_no_headquarters_count": len(connection_no_headquarters),
            "connection_same_country_count": len(connection_same_country), "minimum_coverage_threshold": 0.0,
            "metadata": {
                "data_mode": "derived_from_cached_source", "source": ["360Giving"], "generated_at": _utc_now(),
                "record_count": total_scoped, "coverage": country_coverage / 100 if total_scoped else None,
                "derivation": "Filtered stored 360Giving grants grouped only by grant beneficiary geography.",
                "limitations": map_limitations, "filters_applied": applied_filters,
            },
        }

        valid_dated_rows = [row for row in monetary_rows if row["award_date"]]
        if valid_dated_rows:
            trend_start = date_from or min(row["award_date"] for row in valid_dated_rows)
            trend_end = date_to or max(row["award_date"] for row in valid_dated_rows)
            months_in_range = (
                (int(trend_end[:4]) - int(trend_start[:4])) * 12
                + int(trend_end[5:7]) - int(trend_start[5:7]) + 1
            )
            resolved_granularity = (
                "monthly" if granularity == "auto" and months_in_range <= 24
                else "yearly" if granularity == "auto" else granularity
            )
            period_rows: Dict[str, Dict[str, Any]] = {}
            max_minor_units: Optional[int] = None
            zero_amount_count = 0
            amount_exclusions = {"missing_amount": 0, "invalid_amount": 0, "negative_amount": 0}
            for row in valid_dated_rows:
                period_key = row["award_date"][:4] if resolved_granularity == "yearly" else row["award_date"][:7]
                values = period_rows.setdefault(period_key, {"source": 0, "grant_count": 0, "minor_units": 0, "mapped": 0, "unmapped": 0})
                values["source"] += 1
                amount_status, minor_units = _money_minor_units(row["monetary_amount"])
                if amount_status in amount_exclusions:
                    amount_exclusions[f"{amount_status}_amount"] += 1
                    continue
                if amount_status == "zero":
                    zero_amount_count += 1
                values["grant_count"] += 1
                values["minor_units"] += minor_units or 0
                max_minor_units = max(max_minor_units or minor_units or 0, minor_units or 0)
                if row["beneficiary_countries"]:
                    values["mapped"] += 1
                else:
                    values["unmapped"] += 1
            labels = self._overview_period_labels(trend_start, trend_end, resolved_granularity)
            trend_items = []
            for label in labels:
                values = period_rows.get(label)
                if not values:
                    trend_items.append({"month": label, "grant_count": None, "source_record_count": 0, "total_amount": None, "coverage_status": "unknown", "mapped_grant_count": 0, "unmapped_grant_count": 0})
                elif values["grant_count"]:
                    trend_items.append({"month": label, "grant_count": values["grant_count"], "source_record_count": values["source"], "total_amount": _minor_units_to_amount(values["minor_units"]), "coverage_status": "observed", "mapped_grant_count": values["mapped"], "unmapped_grant_count": values["unmapped"]})
                else:
                    trend_items.append({"month": label, "grant_count": None, "source_record_count": values["source"], "total_amount": None, "coverage_status": "partial", "mapped_grant_count": 0, "unmapped_grant_count": 0})
            trends_payload = {
                "status": "available" if any(item["grant_count"] is not None for item in trend_items) else "no_qualifying_records",
                "currency": selected_currency, "available_currencies": available_currencies,
                "date_basis": "award_date", "granularity": resolved_granularity,
                "period": {"from": trend_start[:7], "to": trend_end[:7], "months": months_in_range, "anchor": "selected_filter_range"},
                "items": trend_items,
                "excluded": {"missing_date": sum(1 for row in monetary_rows if row["date"] is None or str(row["date"]).strip() == ""), "invalid_date": sum(1 for row in monetary_rows if row["date"] is not None and str(row["date"]).strip() and not row["award_date"]), "outside_period": date_excluded, "unsupported_currency": 0, "currency_filtered": 0, "unsupported_source": 0, **amount_exclusions},
                "zero_amount_count": zero_amount_count, "latest_award_date": max(row["award_date"] for row in valid_dated_rows),
                "last_refreshed_at": None, "source": ["360Giving"], "data_mode": "derived_from_cached_source",
                "amount_policy": _amount_policy(max_minor_units), "scope": {"coverage_note": GRANT_SCOPE_NOTE},
            }
        else:
            resolved_granularity = "monthly" if granularity == "auto" else granularity
            trends_payload = {
                "status": "no_qualifying_records", "currency": selected_currency, "available_currencies": available_currencies,
                "date_basis": "award_date", "granularity": resolved_granularity, "period": None, "items": [],
                "excluded": {"missing_date": 0, "invalid_date": 0, "missing_amount": 0, "invalid_amount": 0, "negative_amount": 0, "unsupported_currency": 0, "currency_filtered": 0, "unsupported_source": 0, "outside_period": date_excluded},
                "zero_amount_count": 0, "latest_award_date": None, "last_refreshed_at": None, "source": ["360Giving"], "data_mode": "derived_from_cached_source", "amount_policy": _amount_policy(), "scope": {"coverage_note": GRANT_SCOPE_NOTE},
            }

        theme_aggregates: Dict[str, Dict[str, Any]] = {}
        qualifying_grants = classified_grants = unclassified_grants = source_classified = inferred_classified = 0
        multiple_categories = invalid_source_labels = low_confidence_inferences = 0
        qualifying_minor_units = 0
        theme_zero_count = 0
        maximum_theme_minor_units: Optional[int] = None
        theme_exclusions = {"missing_amount": 0, "invalid_amount": 0, "negative_amount": 0}
        for row in monetary_rows:
            amount_status, minor_units = _money_minor_units(row["monetary_amount"])
            if amount_status in theme_exclusions:
                theme_exclusions[f"{amount_status}_amount"] += 1
                continue
            if amount_status == "zero":
                theme_zero_count += 1
            qualifying_grants += 1
            qualifying_minor_units += minor_units or 0
            maximum_theme_minor_units = max(maximum_theme_minor_units or minor_units or 0, minor_units or 0)
            source_values = _json_list(row["programme_area_source"])
            source_categories, _ = normalize_programme_sources(source_values)
            if source_values and not source_categories:
                invalid_source_labels += 1
            inferred_values = _json_list(row["programme_area_inferred"])
            scores = _json_dict(row["programme_area_scores"])
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
                categories, provenance = sorted(set(source_categories)), "source"
                classified_grants += 1
                source_classified += 1
            elif accepted_inferred:
                categories, provenance = sorted(set(accepted_inferred)), "inferred"
                classified_grants += 1
                inferred_classified += 1
            else:
                categories, provenance = ["Unclassified"], "unclassified"
                unclassified_grants += 1
                if inferred_candidates:
                    low_confidence_inferences += 1
            if len(categories) > 1:
                multiple_categories += 1
            base_share, remainder = divmod(minor_units or 0, len(categories))
            weight = Decimal(1) / Decimal(len(categories))
            for index, category in enumerate(categories):
                values = theme_aggregates.setdefault(category, {"distinct": 0, "weighted": Decimal(0), "minor_units": 0, "source": 0, "inferred": 0, "unclassified": 0})
                values["distinct"] += 1
                values["weighted"] += weight
                values["minor_units"] += base_share + (1 if index < remainder else 0)
                values[provenance] += 1
        theme_items = [
            {"programme_area": category, "distinct_grant_count": values["distinct"], "weighted_grant_count": float(values["weighted"].quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)), "allocated_amount": _minor_units_to_amount(values["minor_units"]), "source_classified_grant_count": values["source"], "inferred_classified_grant_count": values["inferred"], "unclassified_grant_count": values["unclassified"]}
            for category, values in theme_aggregates.items()
        ]
        theme_items.sort(key=lambda item: (-item["allocated_amount"], item["programme_area"]))
        denominator = qualifying_grants or 1
        theme_payload = {
            "status": "available" if qualifying_grants else "no_qualifying_records", "currency": selected_currency,
            "available_currencies": available_currencies, "allocation_method": "equal_split_across_available_categories",
            "classification_precedence": ["valid_source_category", "accepted_inferred_category", "unclassified"],
            "inference_confidence_threshold": DEFAULT_REVIEW_THRESHOLD, "items": theme_items,
            "classification_coverage": {"qualifying_grant_count": qualifying_grants, "classified_grant_count": classified_grants, "unclassified_grant_count": unclassified_grants, "classified_percentage": round(classified_grants / denominator * 100, 2), "source_classified_grant_count": source_classified, "inferred_classified_grant_count": inferred_classified, "source_percentage": round(source_classified / denominator * 100, 2), "inferred_percentage": round(inferred_classified / denominator * 100, 2), "multiple_programme_area_grant_count": multiple_categories, "invalid_source_label_count": invalid_source_labels, "low_confidence_inference_count": low_confidence_inferences},
            "qualifying_amount": _minor_units_to_amount(qualifying_minor_units), "allocated_amount": _minor_units_to_amount(sum(values["minor_units"] for values in theme_aggregates.values())),
            "excluded": theme_exclusions, "zero_amount_count": theme_zero_count, "last_refreshed_at": None,
            "source": ["360Giving"], "data_mode": "derived_from_cached_source", "amount_policy": _amount_policy(maximum_theme_minor_units), "scope": {"coverage_note": GRANT_SCOPE_NOTE},
        }
        total_valid_minor_units = sum(
            (minor_units or 0) for status_value, minor_units in (_money_minor_units(row["monetary_amount"]) for row in monetary_rows)
            if status_value in {"valid", "zero"}
        )
        result = {
            "status": status if status != "available" else ("available" if scoped_rows else "no_data"),
            "kpis": {"awarded_funding": _minor_units_to_amount(total_valid_minor_units) if selected_currency else None, "currency": selected_currency, "grants_monitored": total_scoped, "country_coverage_percentage": country_coverage, "mapped_grant_count": known_count, "unmapped_grant_count": unknown_count, "programme_coverage_percentage": theme_payload["classification_coverage"]["classified_percentage"], "classified_grant_count": classified_grants, "qualifying_programme_grant_count": qualifying_grants},
            "map": map_payload, "trends": trends_payload, "themes": theme_payload,
            "available_date_range": available_date_range, "applied_filters": applied_filters,
        }
        try:
            cache_conn = self._get_conn()
            try:
                self._store_overview_cache(cache_conn, cache_key, revision, result)
            finally:
                cache_conn.close()
        except sqlite3.Error as exc:
            logger.warning("Overview result was returned without caching: %s", exc)
        return result

    async def get_grant_overview_drilldown(
        self,
        *,
        selection_type: str,
        selection_value: str,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return a bounded, evidence-aware detail view for one chart selection.

        The response intentionally remains a grant-data exploration surface. A
        linked organization profile is returned only where the stored grant has
        an explicit profile identifier; name matching is never inferred here.
        """
        selection_type = str(selection_type or "").strip().lower()
        selection_value = str(selection_value or "").strip()
        if selection_type not in {"period", "programme_area"}:
            raise ValueError("selection_type must be period or programme_area.")
        if not selection_value:
            raise ValueError("selection_value is required.")

        selection_label = selection_value
        effective_date_from = date_from
        effective_date_to = date_to
        effective_programmes = programme_areas
        if selection_type == "period":
            if re.fullmatch(r"\d{4}", selection_value):
                start = datetime.strptime(f"{selection_value}-01-01", "%Y-%m-%d").date()
                end = datetime.strptime(f"{selection_value}-12-31", "%Y-%m-%d").date()
            elif re.fullmatch(r"\d{4}-\d{2}", selection_value):
                start = datetime.strptime(f"{selection_value}-01", "%Y-%m-%d").date()
                next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
                end = next_month - timedelta(days=1)
            else:
                raise ValueError("A period selection must be YYYY or YYYY-MM.")
            selection_label = start.strftime("%B %Y") if len(selection_value) == 7 else selection_value
            effective_date_from = max(date_from, start.isoformat()) if date_from else start.isoformat()
            effective_date_to = min(date_to, end.isoformat()) if date_to else end.isoformat()
        else:
            # Selecting one visible category deliberately narrows the current
            # programme scope to that category instead of taking a broad union.
            effective_programmes = [selection_value]

        cache_key = "drilldown:" + selection_type + ":" + selection_value + ":" + self._overview_cache_key(
            currency=currency,
            date_from=effective_date_from,
            date_to=effective_date_to,
            beneficiary_geographies=beneficiary_geographies,
            programme_areas=effective_programmes,
            donor=donor,
            recipient=recipient,
            sources=sources,
            granularity="auto",
            include_connections=False,
        )
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            revision = self._ensure_overview_indexes(conn)
            cached = self._load_overview_cache(conn, cache_key, revision)
            if cached is not None:
                return cached
            rows = self._overview_source_rows(
                conn,
                sources,
                currency=currency,
                date_from=effective_date_from,
                date_to=effective_date_to,
                beneficiary_geographies=beneficiary_geographies,
                programme_areas=effective_programmes,
                donor=donor,
                recipient=recipient,
            )
        finally:
            conn.close()

        requested_currency = str(currency or "").strip().upper() or None
        auto_converted_eur = requested_currency in {None, "AUTO"}
        display_currency = "EUR" if auto_converted_eur else requested_currency
        valid_conversion_statuses = {
            "native_eur", "ecb_award_date", "ecb_previous_business_day",
        }
        selected_rows: List[Dict[str, Any]] = []
        for row in rows:
            award_date = self._overview_award_date(row.get("date"))
            if not award_date:
                continue
            categories = _accepted_programme_categories(
                row.get("programme_area_source"),
                row.get("programme_area_inferred"),
                row.get("programme_area_scores"),
            )
            if selection_type == "period":
                if award_date < str(effective_date_from) or award_date > str(effective_date_to):
                    continue
            elif selection_value not in categories:
                continue
            row["award_date"] = award_date
            row["programme_categories"] = categories
            selected_rows.append(row)

        if not selected_rows:
            return {
                "status": "no_data",
                "selection": {"type": selection_type, "value": selection_value, "label": selection_label},
                "summary": {
                    "grant_count": 0, "funding_total": None, "currency": display_currency,
                    "funder_count": 0, "recipient_count": 0, "country_count": 0,
                    "amount_excluded_grant_count": 0,
                },
                "funders": [], "recipients": [], "countries": [], "grants": [],
                "metadata": {"data_mode": "derived_from_cached_source", "data_revision": revision},
            }

        profile_ids = {
            int(value) for row in selected_rows
            for value in (row.get("funding_charity_id"), row.get("recipient_charity_id"))
            if value is not None
        }
        profile_names: Dict[int, str] = {}
        if profile_ids:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            try:
                placeholders = ", ".join("?" for _ in profile_ids)
                profile_names = {
                    int(row["charity_id"]): str(row["name"])
                    for row in conn.execute(
                        f"SELECT charity_id, name FROM charities WHERE charity_id IN ({placeholders})",
                        sorted(profile_ids),
                    ).fetchall()
                }
            finally:
                conn.close()

        def empty_entity(name: str) -> Dict[str, Any]:
            return {"name_counts": Counter(), "grant_ids": set(), "minor_units": 0, "included": 0, "profile_ids": set()}

        funders: Dict[str, Dict[str, Any]] = {}
        recipients: Dict[str, Dict[str, Any]] = {}
        countries: Dict[str, Dict[str, Any]] = {}
        included_minor_units = 0
        included_grants = 0
        amount_excluded_grants = 0
        grant_rows = []

        for row in selected_rows:
            funder_key, _ = _source_entity_identity(
                role="funder", source=row.get("source"), source_id=row.get("funding_org_source_id"), name=row.get("funding_name"),
            )
            funder_name = _display_source_entity_name(row.get("funding_name"), "Unnamed source funder")
            recipient_key, _ = _source_entity_identity(
                role="recipient", source=row.get("source"), source_id=row.get("recipient_org_source_id"), name=row.get("recipient_name"),
            )
            recipient_name = _display_source_entity_name(row.get("recipient_name"), "Unnamed recipient")
            funder = funders.setdefault(funder_key, empty_entity(funder_name))
            recipient_item = recipients.setdefault(recipient_key, empty_entity(recipient_name))
            for item, name, profile_id in (
                (funder, funder_name, row.get("funding_charity_id")),
                (recipient_item, recipient_name, row.get("recipient_charity_id")),
            ):
                item["name_counts"][name] += 1
                item["grant_ids"].add(str(row["grant_id"]))
                if profile_id is not None and int(profile_id) in profile_names:
                    item["profile_ids"].add(int(profile_id))

            monetary_amount = row.get("amount_eur") if auto_converted_eur else row.get("amount")
            conversion_available = (
                str(row.get("conversion_status") or "") in valid_conversion_statuses
                if auto_converted_eur else True
            )
            amount_status, minor_units = _money_minor_units(monetary_amount)
            amount_included = conversion_available and amount_status in {"valid", "zero"}
            if amount_included:
                minor = int(minor_units or 0)
                included_minor_units += minor
                included_grants += 1
                funder["minor_units"] += minor
                funder["included"] += 1
                recipient_item["minor_units"] += minor
                recipient_item["included"] += 1
            else:
                amount_excluded_grants += 1

            for country in _beneficiary_countries(
                row.get("beneficiary_geography_normalized"), row.get("beneficiary_geography"),
            ):
                code = str(country.get("country_code") or "")
                country_name = str(country.get("country_name") or "Unknown geography")
                item = countries.setdefault(code, {"country_code": code, "country_name": country_name, "grant_ids": set()})
                item["grant_ids"].add(str(row["grant_id"]))

            grant_rows.append({
                "grant_id": str(row["grant_id"]), "award_date": row["award_date"],
                "funder_name": funder_name, "recipient_name": recipient_name,
                "amount": _minor_units_to_amount(int(minor_units or 0)) if amount_included else None,
                "currency": display_currency, "original_amount": row.get("amount"),
                "original_currency": row.get("currency"), "description": row.get("description"),
                "_source_url": row.get("source_url"),
            })

        def serialise_entities(items: Dict[str, Dict[str, Any]], key_name: str) -> List[Dict[str, Any]]:
            result = []
            for key, item in items.items():
                profile_id = next(iter(item["profile_ids"])) if len(item["profile_ids"]) == 1 else None
                result.append({
                    key_name: key,
                    "name": _top_counter_items(item["name_counts"], 1)[0]["name"],
                    "grant_count": len(item["grant_ids"]),
                    "funding_total": _minor_units_to_amount(item["minor_units"]) if item["included"] else None,
                    "currency": display_currency,
                    "profile": {"id": profile_id, "name": profile_names[profile_id]} if profile_id is not None else None,
                })
            result.sort(key=lambda item: (item["name"].casefold(), str(item[key_name])))
            result.sort(key=lambda item: item["grant_count"], reverse=True)
            result.sort(key=lambda item: item["funding_total"] if item["funding_total"] is not None else -1, reverse=True)
            return result[:8]

        grant_rows.sort(key=lambda item: (item["award_date"] or "", item["grant_id"]), reverse=True)
        grant_sample = grant_rows[:20]
        raw_grant_data: Dict[str, Any] = {}
        if grant_sample:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            try:
                placeholders = ", ".join("?" for _ in grant_sample)
                raw_grant_data = {
                    str(row["grant_id"]): row["raw_grant_data"]
                    for row in conn.execute(
                        f"SELECT grant_id, raw_grant_data FROM grants WHERE grant_id IN ({placeholders})",
                        [item["grant_id"] for item in grant_sample],
                    ).fetchall()
                }
            finally:
                conn.close()
        for grant in grant_sample:
            grant["evidence_links"] = _source_evidence_links(
                raw_grant_data.get(grant["grant_id"]), grant.pop("_source_url", None),
                funder_name=grant["funder_name"], recipient_name=grant["recipient_name"],
            )[:4]
        country_items = [
            {"country_code": item["country_code"], "country_name": item["country_name"], "grant_count": len(item["grant_ids"])}
            for item in countries.values()
        ]
        country_items.sort(key=lambda item: (item["country_name"].casefold(), item["country_code"]))
        country_items.sort(key=lambda item: item["grant_count"], reverse=True)
        result = {
            "status": "available",
            "selection": {"type": selection_type, "value": selection_value, "label": selection_label},
            "summary": {
                "grant_count": len(selected_rows),
                "funding_total": _minor_units_to_amount(included_minor_units) if included_grants else None,
                "currency": display_currency,
                "funder_count": len(funders), "recipient_count": len(recipients), "country_count": len(countries),
                "amount_excluded_grant_count": amount_excluded_grants,
            },
            "funders": serialise_entities(funders, "funder_key"),
            "recipients": serialise_entities(recipients, "recipient_key"),
            "countries": country_items[:6], "grants": grant_sample,
            "metadata": {
                "data_mode": "derived_from_cached_source", "data_revision": revision,
                "grant_sample_limit": 20,
                "profile_link_policy": "Only direct stored charity identifiers are linked to organization profiles.",
                "external_link_policy": "Stored HTTP(S) links only; no server-side fetch or proxy.",
            },
        }
        try:
            cache_conn = self._get_conn()
            try:
                self._store_overview_cache(cache_conn, cache_key, revision, result)
            finally:
                cache_conn.close()
        except sqlite3.Error as exc:
            logger.warning("Overview drill-down result was returned without caching: %s", exc)
        return result

    async def get_grant_overview_trends(
        self,
        *,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
        granularity: str = "auto",
    ) -> Dict[str, Any]:
        """Return only the filtered time series, without rebuilding map/themes.

        Granularity is a chart-level presentation choice. Keeping this request
        separate avoids re-aggregating geography and programme allocations when
        a user switches between monthly and yearly award periods.
        """
        if granularity not in {"auto", "monthly", "yearly"}:
            raise ValueError("granularity must be auto, monthly, or yearly")
        overview = await self.get_grant_overview(
            currency=currency, date_from=date_from, date_to=date_to,
            beneficiary_geographies=beneficiary_geographies,
            programme_areas=programme_areas, donor=donor, recipient=recipient,
            sources=sources, granularity=granularity, include_connections=False,
        )
        return dict(overview["trends"])
        cache_key = f"trend:{self._overview_cache_key(
            currency=currency, date_from=date_from, date_to=date_to,
            beneficiary_geographies=beneficiary_geographies, programme_areas=programme_areas,
            donor=donor, recipient=recipient, sources=sources, granularity=granularity,
            include_connections=False,
        )}"
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            revision = self._ensure_overview_indexes(conn)
            cached = self._load_overview_cache(conn, cache_key, revision)
            if cached is not None:
                return cached
            source_metadata = self._overview_source_metadata(conn, sources, revision=revision)
            source_rows = self._overview_source_rows(
                conn,
                sources,
                currency=currency,
                date_from=date_from,
                date_to=date_to,
                beneficiary_geographies=beneficiary_geographies,
                programme_areas=programme_areas,
                donor=donor,
                recipient=recipient,
            )
        finally:
            conn.close()

        available_currencies = source_metadata["currencies"]
        requested_currency = str(currency or "").strip().upper() or None
        auto_converted_eur = requested_currency in {None, "AUTO"}
        source_currency_filter = None if auto_converted_eur else requested_currency
        selected_currency = "EUR" if auto_converted_eur else source_currency_filter
        status = "available"
        if source_currency_filter and source_currency_filter not in available_currencies:
            status = "unsupported_currency"
        elif not available_currencies:
            status = "no_qualifying_records"
            selected_currency = None

        selected_regions = {
            str(value).strip().casefold()
            for value in (beneficiary_geographies or []) if str(value).strip()
        }
        selected_programmes = {
            str(value).strip().casefold()
            for value in (programme_areas or []) if str(value).strip()
        }
        donor_query = str(donor or "").strip().casefold()
        recipient_query = str(recipient or "").strip().casefold()
        scoped_rows: List[Dict[str, Any]] = []
        date_excluded = 0
        for row in source_rows:
            award_date = self._overview_award_date(row["date"])
            countries = _beneficiary_countries(
                row["beneficiary_geography_normalized"], row["beneficiary_geography"]
            )
            if donor_query and donor_query not in str(row["funding_name"] or "").casefold():
                continue
            if recipient_query and recipient_query not in str(row["recipient_name"] or "").casefold():
                continue
            if selected_regions and not _matches_funding_regions(
                row["beneficiary_geography_normalized"], row["beneficiary_geography"], countries, selected_regions
            ):
                continue
            if selected_programmes:
                categories = _accepted_programme_categories(
                    row["programme_area_source"], row["programme_area_inferred"], row["programme_area_scores"]
                )
                if not selected_programmes.intersection(str(category).casefold() for category in categories):
                    continue
            if date_from or date_to:
                if not award_date or (date_from and award_date < date_from) or (date_to and award_date > date_to):
                    date_excluded += 1
                    continue
            if source_currency_filter and str(row["currency"] or "").strip().upper() != source_currency_filter:
                continue
            row["award_date"] = award_date
            row["has_mapped_country"] = bool(countries)
            scoped_rows.append(row)

        if status != "available":
            scoped_rows = []
        for row in scoped_rows:
            row["monetary_amount"] = row["amount_eur"] if auto_converted_eur else row["amount"]
            row["monetary_eligible"] = bool(
                row["amount_eur"] is not None
                and str(row.get("conversion_status") or "")
                in {"native_eur", "ecb_award_date", "ecb_previous_business_day"}
            ) if auto_converted_eur else True
        monetary_rows = [row for row in scoped_rows if row["monetary_eligible"]]
        valid_dated_rows = [row for row in monetary_rows if row["award_date"]]
        amount_exclusions = {"missing_amount": 0, "invalid_amount": 0, "negative_amount": 0}
        zero_amount_count = 0
        maximum_minor_units: Optional[int] = None

        if valid_dated_rows:
            trend_start = date_from or min(row["award_date"] for row in valid_dated_rows)
            trend_end = date_to or max(row["award_date"] for row in valid_dated_rows)
            months_in_range = (
                (int(trend_end[:4]) - int(trend_start[:4])) * 12
                + int(trend_end[5:7]) - int(trend_start[5:7]) + 1
            )
            resolved_granularity = (
                "monthly" if granularity == "auto" and months_in_range <= 24
                else "yearly" if granularity == "auto" else granularity
            )
            period_rows: Dict[str, Dict[str, int]] = {}
            for row in valid_dated_rows:
                period_key = row["award_date"][:4] if resolved_granularity == "yearly" else row["award_date"][:7]
                values = period_rows.setdefault(period_key, {
                    "source": 0, "grant_count": 0, "minor_units": 0, "mapped": 0, "unmapped": 0,
                })
                values["source"] += 1
                amount_status, minor_units = _money_minor_units(row["monetary_amount"])
                if amount_status in amount_exclusions:
                    amount_exclusions[f"{amount_status}_amount"] += 1
                    continue
                if amount_status == "zero":
                    zero_amount_count += 1
                values["grant_count"] += 1
                values["minor_units"] += minor_units or 0
                maximum_minor_units = max(maximum_minor_units or minor_units or 0, minor_units or 0)
                if row["has_mapped_country"]:
                    values["mapped"] += 1
                else:
                    values["unmapped"] += 1
            labels = self._overview_period_labels(trend_start, trend_end, resolved_granularity)
            items = []
            for label in labels:
                values = period_rows.get(label)
                if not values:
                    items.append({"month": label, "grant_count": None, "source_record_count": 0, "total_amount": None, "coverage_status": "unknown", "mapped_grant_count": 0, "unmapped_grant_count": 0})
                elif values["grant_count"]:
                    items.append({"month": label, "grant_count": values["grant_count"], "source_record_count": values["source"], "total_amount": _minor_units_to_amount(values["minor_units"]), "coverage_status": "observed", "mapped_grant_count": values["mapped"], "unmapped_grant_count": values["unmapped"]})
                else:
                    items.append({"month": label, "grant_count": None, "source_record_count": values["source"], "total_amount": None, "coverage_status": "partial", "mapped_grant_count": 0, "unmapped_grant_count": 0})
            response = {
                "status": "available" if any(item["grant_count"] is not None for item in items) else "no_qualifying_records",
                "currency": selected_currency, "available_currencies": available_currencies,
                "date_basis": "award_date", "granularity": resolved_granularity,
                "period": {"from": trend_start[:7], "to": trend_end[:7], "months": months_in_range, "anchor": "selected_filter_range"},
                "items": items,
                "excluded": {
                    "missing_date": sum(1 for row in monetary_rows if row["date"] is None or str(row["date"]).strip() == ""),
                    "invalid_date": sum(1 for row in monetary_rows if row["date"] is not None and str(row["date"]).strip() and not row["award_date"]),
                    "outside_period": date_excluded, "unsupported_currency": 0, "currency_filtered": 0, "unsupported_source": 0,
                    **amount_exclusions,
                },
                "zero_amount_count": zero_amount_count, "latest_award_date": max(row["award_date"] for row in valid_dated_rows),
                "last_refreshed_at": None, "source": ["360Giving"], "data_mode": "derived_from_cached_source",
                "amount_policy": _amount_policy(maximum_minor_units), "scope": {"coverage_note": GRANT_SCOPE_NOTE},
            }
        else:
            response = {
                "status": status if status != "available" else "no_qualifying_records",
                "currency": selected_currency, "available_currencies": available_currencies,
                "date_basis": "award_date", "granularity": "monthly" if granularity == "auto" else granularity,
                "period": None, "items": [],
                "excluded": {"missing_date": 0, "invalid_date": 0, "missing_amount": 0, "invalid_amount": 0, "negative_amount": 0, "unsupported_currency": 0, "currency_filtered": 0, "unsupported_source": 0, "outside_period": date_excluded},
                "zero_amount_count": 0, "latest_award_date": None, "last_refreshed_at": None,
                "source": ["360Giving"], "data_mode": "derived_from_cached_source",
                "amount_policy": _amount_policy(), "scope": {"coverage_note": GRANT_SCOPE_NOTE},
            }
        try:
            cache_conn = self._get_conn()
            try:
                self._store_overview_cache(cache_conn, cache_key, revision, response)
            finally:
                cache_conn.close()
        except sqlite3.Error as exc:
            logger.warning("Trend-only result was returned without caching: %s", exc)
        return response

    def _source_funder_scope(
        self,
        *,
        beneficiary_country: str,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Build one canonical, country-scoped population of source funders.

        Grant activity follows the same beneficiary-country association as the
        Overview map. Monetary country totals deliberately exclude a whole
        multi-country award, so a single award is never attributed in full to
        several countries.
        """

        country_code = str(beneficiary_country or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country_code):
            raise ValueError("beneficiary_country must be an ISO 3166-1 alpha-2 code.")

        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            revision = self._ensure_overview_indexes(conn)
            country_rows = conn.execute(
                """
                SELECT DISTINCT country_name
                FROM grant_beneficiary_countries
                WHERE country_code = ?
                ORDER BY country_name
                """,
                (country_code,),
            ).fetchall()
            if not country_rows:
                raise ValueError(
                    f"No mapped beneficiary country is available for ISO code '{country_code}'."
                )
            source_metadata = self._overview_source_metadata(conn, sources, revision=revision)
            source_rows = self._overview_source_rows(
                conn,
                sources,
                currency=currency,
                date_from=date_from,
                date_to=date_to,
                beneficiary_geographies=beneficiary_geographies,
                programme_areas=programme_areas,
                donor=donor,
                recipient=recipient,
                beneficiary_country_code=country_code,
                include_connections=True,
            )
        finally:
            conn.close()

        requested_currency = str(currency or "").strip().upper() or None
        auto_converted_eur = requested_currency in {None, "AUTO"}
        source_currency_filter = None if auto_converted_eur else requested_currency
        available_currencies = source_metadata["currencies"]
        availability_status = "available"
        if source_currency_filter and source_currency_filter not in available_currencies:
            availability_status = "unsupported_currency"
        elif not available_currencies:
            availability_status = "no_qualifying_records"

        selected_regions = {
            str(value).strip().casefold()
            for value in (beneficiary_geographies or [])
            if str(value).strip()
        }
        selected_programmes = {
            str(value).strip().casefold()
            for value in (programme_areas or [])
            if str(value).strip()
        }
        donor_query = str(donor or "").strip().casefold()
        recipient_query = str(recipient or "").strip().casefold()
        scoped_rows: List[Dict[str, Any]] = []
        for row in source_rows:
            award_date = self._overview_award_date(row["date"])
            countries = _beneficiary_countries(
                row["beneficiary_geography_normalized"], row["beneficiary_geography"]
            )
            programme_categories = _accepted_programme_categories(
                row["programme_area_source"],
                row["programme_area_inferred"],
                row["programme_area_scores"],
            )
            if not any(country["country_code"] == country_code for country in countries):
                continue
            if donor_query and donor_query not in str(row["funding_name"] or "").casefold():
                continue
            if recipient_query and recipient_query not in str(row["recipient_name"] or "").casefold():
                continue
            if selected_regions and not _matches_funding_regions(
                row["beneficiary_geography_normalized"],
                row["beneficiary_geography"],
                countries,
                selected_regions,
            ):
                continue
            if selected_programmes and not selected_programmes.intersection(
                str(category).casefold() for category in programme_categories
            ):
                continue
            if date_from or date_to:
                if not award_date or (date_from and award_date < date_from) or (date_to and award_date > date_to):
                    continue
            if source_currency_filter and str(row["currency"] or "").strip().upper() != source_currency_filter:
                continue
            row["award_date"] = award_date
            row["beneficiary_countries"] = countries
            row["programme_categories"] = programme_categories
            scoped_rows.append(row)

        groups: Dict[str, Dict[str, Any]] = {}
        valid_auto_statuses = {"native_eur", "ecb_award_date", "ecb_previous_business_day"}
        for row in scoped_rows if availability_status == "available" else []:
            if not str(row.get("funding_name") or "").strip() and not str(row.get("funding_org_source_id") or "").strip():
                # There is no transparent source-funder identity for this row;
                # the map applies the same exclusion to its funder count.
                continue
            funder_name = _display_source_entity_name(row.get("funding_name"), "Unnamed source funder")
            funder_key, identity_method = _source_entity_identity(
                role="funder",
                source=row.get("source"),
                source_id=row.get("funding_org_source_id"),
                name=row.get("funding_name"),
            )
            group = groups.setdefault(
                funder_key,
                {
                    "key": funder_key,
                    "identity_method": identity_method,
                    "names": Counter(),
                    "source_ids": set(),
                    "sources": set(),
                    "linked_directory_ids": set(),
                    "linked_names": Counter(),
                    "grant_ids": set(),
                    "recipient_ids": set(),
                    "recipients": Counter(),
                    "recipient_labels": {},
                    "recipient_names": Counter(),
                    "programme_areas": Counter(),
                    "programme_area_provenance": {},
                    "first_award_date": None,
                    "latest_award_date": None,
                    "included_minor_units": 0,
                    "included_grant_ids": set(),
                    "multi_country_grant_ids": set(),
                    "multi_country_minor_units": 0,
                    "conversion_excluded_ids": set(),
                    "missing_amount_ids": set(),
                    "invalid_amount_ids": set(),
                    "negative_amount_ids": set(),
                    "source_urls": set(),
                    "records": [],
                },
            )
            group["names"][funder_name] += 1
            raw_source_id = str(row.get("funding_org_source_id") or "").strip()
            if raw_source_id:
                group["source_ids"].add(raw_source_id)
            source_name = str(row.get("source") or "source").strip() or "source"
            group["sources"].add(source_name)
            linked_id = row.get("funding_charity_id") if row.get("linked_funder_name") else None
            if linked_id is not None:
                group["linked_directory_ids"].add(int(linked_id))
                group["linked_names"][_display_source_entity_name(row.get("linked_funder_name"), funder_name)] += 1
            grant_id = str(row["grant_id"])
            group["grant_ids"].add(grant_id)
            recipient_name = _display_source_entity_name(row.get("recipient_name"), "Unnamed recipient")
            recipient_key, _ = _source_entity_identity(
                role="recipient",
                source=row.get("source"),
                source_id=row.get("recipient_org_source_id"),
                name=row.get("recipient_name"),
            )
            group["recipient_ids"].add(recipient_key)
            group["recipients"][recipient_key] += 1
            group["recipient_labels"].setdefault(recipient_key, recipient_name)
            group["recipient_names"][recipient_name] += 1
            source_categories, _ = normalize_programme_sources(_json_list(row["programme_area_source"]))
            category_provenance = "source" if source_categories else (
                "inferred" if row["programme_categories"] != ["Unclassified"] else "unclassified"
            )
            for category in row["programme_categories"]:
                group["programme_areas"][category] += 1
                group["programme_area_provenance"].setdefault(category, Counter())[category_provenance] += 1
            award_date = row["award_date"]
            if award_date:
                group["first_award_date"] = min(group["first_award_date"] or award_date, award_date)
                group["latest_award_date"] = max(group["latest_award_date"] or award_date, award_date)
            source_url = str(row.get("source_url") or "").strip()
            if source_url:
                group["source_urls"].add(source_url)

            monetary_eligible = (
                row.get("amount_eur") is not None
                and str(row.get("conversion_status") or "") in valid_auto_statuses
            ) if auto_converted_eur else True
            monetary_amount = row.get("amount_eur") if auto_converted_eur else row.get("amount")
            if not monetary_eligible:
                original_amount_status, _ = _money_minor_units(row.get("amount"))
                if original_amount_status == "missing":
                    group["missing_amount_ids"].add(grant_id)
                elif original_amount_status == "negative":
                    group["negative_amount_ids"].add(grant_id)
                elif original_amount_status == "invalid":
                    group["invalid_amount_ids"].add(grant_id)
                else:
                    group["conversion_excluded_ids"].add(grant_id)
            else:
                amount_status, minor_units = _money_minor_units(monetary_amount)
                if amount_status in {"valid", "zero"}:
                    if len(row["beneficiary_countries"]) > 1:
                        if grant_id not in group["multi_country_grant_ids"]:
                            group["multi_country_grant_ids"].add(grant_id)
                            group["multi_country_minor_units"] += minor_units or 0
                    elif grant_id not in group["included_grant_ids"]:
                        group["included_grant_ids"].add(grant_id)
                        group["included_minor_units"] += minor_units or 0
                else:
                    if amount_status == "missing":
                        group["missing_amount_ids"].add(grant_id)
                    elif amount_status == "negative":
                        group["negative_amount_ids"].add(grant_id)
                    else:
                        group["invalid_amount_ids"].add(grant_id)
            group["records"].append(row)

        all_recipient_ids = set().union(*(group["recipient_ids"] for group in groups.values())) if groups else set()
        all_funder_grant_ids = set().union(*(group["grant_ids"] for group in groups.values())) if groups else set()
        all_included_grant_ids = set().union(*(group["included_grant_ids"] for group in groups.values())) if groups else set()
        all_multi_country_ids = set().union(*(group["multi_country_grant_ids"] for group in groups.values())) if groups else set()
        all_conversion_excluded_ids = set().union(*(group["conversion_excluded_ids"] for group in groups.values())) if groups else set()
        all_missing_amount_ids = set().union(*(group["missing_amount_ids"] for group in groups.values())) if groups else set()
        all_invalid_amount_ids = set().union(*(group["invalid_amount_ids"] for group in groups.values())) if groups else set()
        all_negative_amount_ids = set().union(*(group["negative_amount_ids"] for group in groups.values())) if groups else set()
        return {
            "country": {"code": country_code, "name": country_rows[0][0]},
            "source_metadata": source_metadata,
            "availability_status": availability_status,
            "auto_converted_eur": auto_converted_eur,
            "source_currency_filter": source_currency_filter,
            "display_currency": "EUR" if auto_converted_eur else source_currency_filter,
            "groups": groups,
            "scoped_grant_count": len(scoped_rows) if availability_status == "available" else 0,
            "source_funder_grant_count": len(all_funder_grant_ids),
            "distinct_recipient_count": len(all_recipient_ids),
            "included_grant_ids": all_included_grant_ids,
            "included_minor_units": sum(group["included_minor_units"] for group in groups.values()),
            "multi_country_grant_ids": all_multi_country_ids,
            "multi_country_minor_units": sum(group["multi_country_minor_units"] for group in groups.values()),
            "conversion_excluded_ids": all_conversion_excluded_ids,
            "missing_amount_ids": all_missing_amount_ids,
            "invalid_amount_ids": all_invalid_amount_ids,
            "negative_amount_ids": all_negative_amount_ids,
        }

    @staticmethod
    def _source_funder_item(group: Mapping[str, Any], display_currency: Optional[str]) -> Dict[str, Any]:
        linked_ids = sorted(group["linked_directory_ids"])
        name = _top_counter_items(group["names"], 1)[0]["name"] if group["names"] else "Unnamed source funder"
        linked_name = _top_counter_items(group["linked_names"], 1)[0]["name"] if group["linked_names"] else None
        leading_programme_areas = _top_counter_items(group["programme_areas"], 3)
        for item in leading_programme_areas:
            provenance = group["programme_area_provenance"].get(item["name"], Counter())
            item["provenance"] = _top_counter_items(provenance, 1)[0]["name"] if provenance else "unclassified"
        return {
            "source_funder_key": group["key"],
            "display_name": name,
            "identity_method": group["identity_method"],
            "source_ids": sorted(group["source_ids"]),
            "sources": sorted(group["sources"]),
            "source_only": not linked_ids,
            "linked_directory_profile": {
                "charity_id": linked_ids[0], "name": linked_name,
            } if len(linked_ids) == 1 else None,
            "activity": {
                "grant_count": len(group["grant_ids"]),
                "distinct_recipient_count": len(group["recipient_ids"]),
                "first_award_date": group["first_award_date"],
                "latest_award_date": group["latest_award_date"],
            },
            "observed_funding": {
                "amount": _minor_units_to_amount(group["included_minor_units"])
                if group["included_grant_ids"] else None,
                "currency": display_currency,
                "included_grant_count": len(group["included_grant_ids"]),
                "excluded_multi_country_grant_count": len(group["multi_country_grant_ids"]),
                "excluded_multi_country_amount": _minor_units_to_amount(group["multi_country_minor_units"]),
                "excluded_conversion_grant_count": len(group["conversion_excluded_ids"]),
                "excluded_missing_amount_grant_count": len(group["missing_amount_ids"]),
                "excluded_invalid_amount_grant_count": len(group["invalid_amount_ids"]),
                "excluded_negative_amount_grant_count": len(group["negative_amount_ids"]),
            },
            "leading_programme_areas": leading_programme_areas,
            "representative_source_url": sorted(group["source_urls"])[0] if group["source_urls"] else None,
        }

    @staticmethod
    def _source_funder_relationship_flow(
        group: Mapping[str, Any],
        *,
        display_currency: Optional[str],
        auto_converted_eur: bool,
        limit: int = 15,
    ) -> Dict[str, Any]:
        """Build a donor-to-recipient flow from one canonical source identity.

        A country selection identifies a beneficiary geography, not necessarily
        the recipient's registered location. Therefore a full multi-country
        award is deliberately excluded from the monetary flow, matching the
        map's country-attributable amount policy. Activity still remains
        available elsewhere in the source-funder detail.
        """

        valid_auto_statuses = {
            "native_eur", "ecb_award_date", "ecb_previous_business_day",
        }
        flow_currency = display_currency or ("EUR" if auto_converted_eur else None)
        donor_label = (
            _top_counter_items(group["names"], 1)[0]["name"]
            if group["names"] else "Unnamed source funder"
        )
        donor_id = str(group["key"])
        recipient_labels: Dict[str, str] = {}
        aggregates: Dict[Tuple[str, str], Dict[str, int]] = {}
        excluded_reasons: Counter[str] = Counter()
        included_grant_ids: set[str] = set()

        for row in group["records"]:
            grant_id = str(row["grant_id"])
            if len(row.get("beneficiary_countries") or []) > 1:
                excluded_reasons["multi_country_award"] += 1
                continue

            amount_value = row.get("amount_eur") if auto_converted_eur else row.get("amount")
            if auto_converted_eur and str(row.get("conversion_status") or "") not in valid_auto_statuses:
                excluded_reasons["conversion_unavailable"] += 1
                continue
            amount_status, minor_units = _money_minor_units(amount_value)
            if amount_status == "missing":
                excluded_reasons["missing_amount"] += 1
                continue
            if amount_status == "invalid":
                excluded_reasons["invalid_amount"] += 1
                continue
            if amount_status == "negative":
                excluded_reasons["negative_amount"] += 1
                continue
            if amount_status == "zero":
                excluded_reasons["non_positive_amount"] += 1
                continue

            recipient_id, _ = _source_entity_identity(
                role="recipient",
                source=row.get("source"),
                source_id=row.get("recipient_org_source_id"),
                name=row.get("recipient_name"),
            )
            recipient_labels.setdefault(
                recipient_id,
                _display_source_entity_name(row.get("recipient_name"), "Unnamed recipient"),
            )
            aggregate = aggregates.setdefault(
                (donor_id, recipient_id), {"minor_units": 0, "grant_count": 0},
            )
            aggregate["minor_units"] += minor_units or 0
            aggregate["grant_count"] += 1
            included_grant_ids.add(grant_id)

        links = [
            {
                "source": source,
                "target": target,
                "value": _minor_units_to_amount(values["minor_units"]),
                "currency": flow_currency,
                "grant_count": values["grant_count"],
            }
            for (source, target), values in aggregates.items()
        ]
        links.sort(key=lambda item: (item["value"], item["grant_count"], item["target"]), reverse=True)
        if len(links) > limit:
            excluded_reasons["truncated"] += sum(item["grant_count"] for item in links[limit:])
            links = links[:limit]

        retained_grant_count = sum(item["grant_count"] for item in links)
        retained_node_ids = {donor_id}
        retained_node_ids.update(item["target"] for item in links)
        nodes = []
        if links:
            nodes.append({"id": donor_id, "label": donor_label, "role": "donor"})
            nodes.extend(
                {
                    "id": recipient_id,
                    "label": recipient_labels[recipient_id],
                    "role": "recipient",
                }
                for recipient_id in sorted(retained_node_ids - {donor_id})
            )

        total_records = len(group["records"])
        if links:
            status = "available"
        elif excluded_reasons.get("multi_country_award"):
            status = "no_country_attributable_transactions"
        elif total_records:
            status = "no_monetary_transactions"
        else:
            status = "no_transactions_found"
        return {
            "status": status,
            "nodes": nodes,
            "links": links,
            "metadata": {
                "identity_basis": "canonical source funder identity",
                "country_amount_policy": (
                    "Only single-country observed awards are included so a full award "
                    "is not attributed to more than one beneficiary country."
                ),
                "currency": flow_currency,
                "grant_count": total_records,
                "included_grant_count": retained_grant_count,
                "included_value": round(sum(item["value"] for item in links), 2),
                "excluded_grant_count": total_records - retained_grant_count,
                "excluded_reasons": dict(excluded_reasons),
                "limit": limit,
                "truncation_applied": "truncated" in excluded_reasons,
            },
        }

    @staticmethod
    def _source_funder_profile_link(
        profile_ids: List[int],
        profile_name: Optional[str],
        registry_links: Mapping[int, List[Mapping[str, Any]]],
    ) -> Dict[str, Any]:
        if not profile_ids:
            return {"status": "none"}
        if len(profile_ids) > 1:
            return {
                "status": "multiple",
                "candidate_count": len(profile_ids),
                "candidate_profile_ids": profile_ids,
            }
        profile_id = profile_ids[0]
        accepted = list(registry_links.get(profile_id, []))
        if not accepted:
            registry_link: Dict[str, Any] = {"status": "none"}
        elif len(accepted) == 1:
            link = accepted[0]
            registry_link = {
                "status": "accepted",
                "registry_id": link["registry_id"],
                "charity_number": link.get("charity_number"),
                "method": link["match_method"],
                "confidence": link.get("match_confidence"),
            }
        else:
            registry_link = {
                "status": "multiple",
                "candidate_count": len(accepted),
            }
        return {
            "status": "single",
            "profile_id": profile_id,
            "profile_name": profile_name or f"Profile {profile_id}",
            "method": "direct_grant_profile_id",
            "confidence": None,
            "registry_link": registry_link,
        }

    @staticmethod
    def _accepted_registry_links(
        conn: sqlite3.Connection, profile_ids: List[int],
    ) -> Dict[int, List[Dict[str, Any]]]:
        if not profile_ids:
            return {}
        placeholders = ", ".join("?" for _ in profile_ids)
        rows = conn.execute(
            f"""
            SELECT link.enriched_organization_id, link.registry_id,
                   registry.charity_number, link.match_method,
                   link.match_confidence
            FROM {REGISTRY_LINK_TABLE} AS link
            JOIN {REGISTRY_TABLE} AS registry
              ON registry.registry_id = link.registry_id
            WHERE link.enriched_organization_id IN ({placeholders})
              AND link.match_status = 'accepted'
              AND registry.is_current_source_record = 1
            ORDER BY link.enriched_organization_id, link.registry_id
            """,
            profile_ids,
        ).fetchall()
        result: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(int(row[0]), []).append({
                "registry_id": row[1],
                "charity_number": row[2],
                "match_method": row[3],
                "match_confidence": row[4],
            })
        return result

    @staticmethod
    def _source_funder_fact_filters(
        *,
        beneficiary_country: str,
        currency: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
        beneficiary_geographies: Optional[List[str]],
        programme_areas: Optional[List[str]],
        donor: Optional[str],
        recipient: Optional[str],
        sources: Optional[List[str]],
    ) -> Tuple[List[str], List[Any], List[str]]:
        selected_sources = (
            [str(value).strip() for value in sources if str(value).strip()]
            if sources is not None else ["360Giving"]
        )
        where = ["fact.country_code = ?"]
        params: List[Any] = [beneficiary_country]
        if selected_sources:
            placeholders = ", ".join("?" for _ in selected_sources)
            where.append(f"fact.source_namespace IN ({placeholders})")
            params.extend(selected_sources)
        else:
            where.append("1 = 0")
        requested_currency = str(currency or "").strip().upper()
        if requested_currency and requested_currency != "AUTO":
            where.append("fact.currency = ?")
            params.append(requested_currency)
        if date_from:
            where.append("fact.award_date >= ?")
            params.append(date_from)
        if date_to:
            where.append("fact.award_date <= ?")
            params.append(date_to)
        regions = sorted({
            str(value).strip().casefold()
            for value in beneficiary_geographies or [] if str(value).strip()
        })
        if regions:
            placeholders = ", ".join("?" for _ in regions)
            where.append(
                "EXISTS ("
                "SELECT 1 FROM grant_beneficiary_terms AS geography "
                "WHERE geography.grant_id = fact.grant_id "
                f"AND geography.term IN ({placeholders})"
                ")"
            )
            params.extend(regions)
        programmes = sorted({
            str(value).strip()
            for value in programme_areas or [] if str(value).strip()
        })
        if programmes:
            placeholders = ", ".join("?" for _ in programmes)
            where.append(
                "EXISTS ("
                "SELECT 1 FROM grant_programme_categories AS programme "
                "WHERE programme.grant_id = fact.grant_id "
                f"AND programme.programme_area COLLATE NOCASE IN ({placeholders})"
                ")"
            )
            params.extend(programmes)
        donor_value = str(donor or "").strip().casefold()
        if donor_value:
            where.append("LOWER(fact.display_name) LIKE ?")
            params.append(f"%{donor_value}%")
        recipient_value = str(recipient or "").strip().casefold()
        if recipient_value:
            where.append("LOWER(fact.recipient_name) LIKE ?")
            params.append(f"%{recipient_value}%")
        return where, params, selected_sources

    def _source_funder_list_from_facts(
        self,
        *,
        beneficiary_country: str,
        currency: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
        beneficiary_geographies: Optional[List[str]],
        programme_areas: Optional[List[str]],
        donor: Optional[str],
        recipient: Optional[str],
        sources: Optional[List[str]],
        search: Optional[str],
        profile_status: str,
        sort: str,
        page: int,
        page_size: int,
    ) -> Dict[str, Any]:
        country_code = str(beneficiary_country or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country_code):
            raise ValueError("beneficiary_country must be an ISO 3166-1 alpha-2 code.")
        if profile_status not in {"all", "linked", "observed_only"}:
            raise ValueError("profile_status must be all, linked, or observed_only.")
        where, params, selected_sources = self._source_funder_fact_filters(
            beneficiary_country=country_code,
            currency=currency,
            date_from=date_from,
            date_to=date_to,
            beneficiary_geographies=beneficiary_geographies,
            programme_areas=programme_areas,
            donor=donor,
            recipient=recipient,
            sources=sources,
        )
        requested_currency = str(currency or "").strip().upper() or None
        auto_converted_eur = requested_currency in {None, "AUTO"}
        display_currency = "EUR" if auto_converted_eur else requested_currency
        eligible = (
            "fact.eur_amount_status IN ('valid', 'zero') "
            "AND fact.conversion_status IN "
            "('native_eur', 'ecb_award_date', 'ecb_previous_business_day')"
            if auto_converted_eur
            else "fact.original_amount_status IN ('valid', 'zero')"
        )
        amount_column = (
            "fact.eur_amount_minor" if auto_converted_eur
            else "fact.original_amount_minor"
        )
        if sort == "largest_observed_funding":
            order = (
                "included_minor IS NULL, included_minor DESC, grant_count DESC, "
                "latest_award_date DESC, LOWER(display_name), source_funder_key"
            )
        elif sort == "most_grants":
            order = (
                "grant_count DESC, included_minor DESC, latest_award_date DESC, "
                "LOWER(display_name), source_funder_key"
            )
        else:
            order = (
                "latest_award_date DESC, grant_count DESC, included_minor DESC, "
                "LOWER(display_name), source_funder_key"
            )
        search_value = str(search or "").strip().casefold()
        search_clause = ""
        search_params: List[Any] = []
        if search_value:
            search_clause = """
                WHERE LOWER(preferred.display_name) LIKE ?
                   OR LOWER(COALESCE(preferred.all_display_names, '')) LIKE ?
                   OR LOWER(COALESCE(preferred.source_ids, '')) LIKE ?
                   OR (
                       preferred.profile_count = 1
                       AND LOWER(COALESCE(preferred.profile_names, '')) LIKE ?
                   )
            """
            search_params = [f"%{search_value}%"] * 4
        status_clause = {
            "all": "",
            "linked": "WHERE profile_count = 1",
            "observed_only": "WHERE profile_count <> 1",
        }[profile_status]
        offset = (page - 1) * page_size
        sql = f"""
            WITH scoped AS (
                SELECT fact.*, profile.name AS linked_profile_name
                FROM grant_source_funder_facts AS fact
                LEFT JOIN charities AS profile
                  ON profile.charity_id = fact.linked_profile_id
                WHERE {' AND '.join(where)}
            ),
            name_counts AS (
                SELECT source_funder_key, display_name, COUNT(*) AS occurrences
                FROM scoped
                GROUP BY source_funder_key, display_name
            ),
            preferred_names AS (
                SELECT source_funder_key, display_name
                FROM (
                    SELECT source_funder_key, display_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY source_funder_key
                               ORDER BY occurrences DESC, LOWER(display_name)
                           ) AS name_rank
                    FROM name_counts
                )
                WHERE name_rank = 1
            ),
            grouped AS (
                SELECT
                    fact.source_funder_key,
                    MIN(fact.identity_method) AS identity_method,
                    MIN(fact.source_namespace) AS source_namespace,
                    GROUP_CONCAT(DISTINCT fact.source_organization_id) AS source_ids,
                    MIN(fact.normalized_name_fallback) AS normalized_name_fallback,
                    GROUP_CONCAT(DISTINCT fact.display_name) AS all_display_names,
                    COUNT(DISTINCT fact.grant_id) AS grant_count,
                    COUNT(DISTINCT fact.recipient_key) AS recipient_count,
                    MIN(fact.award_date) AS first_award_date,
                    MAX(fact.award_date) AS latest_award_date,
                    SUM(CASE
                        WHEN fact.country_count = 1 AND {eligible}
                        THEN {amount_column}
                    END) AS included_minor,
                    COUNT(DISTINCT CASE
                        WHEN fact.country_count = 1 AND {eligible}
                        THEN fact.grant_id
                    END) AS included_grant_count,
                    COUNT(DISTINCT CASE
                        WHEN fact.country_count > 1 THEN fact.grant_id
                    END) AS multi_country_grant_count,
                    COALESCE(SUM(CASE
                        WHEN fact.country_count > 1 AND {eligible}
                        THEN {amount_column}
                    END), 0) AS multi_country_minor,
                    COUNT(DISTINCT CASE
                        WHEN fact.country_count = 1
                         AND fact.original_amount_status IN ('valid', 'zero')
                         AND NOT ({eligible})
                        THEN fact.grant_id
                    END) AS conversion_excluded_count,
                    COUNT(DISTINCT CASE
                        WHEN fact.country_count = 1
                         AND fact.original_amount_status IN ('valid', 'zero')
                         AND NOT ({eligible})
                        THEN fact.currency
                    END) AS conversion_original_currency_count,
                    MIN(CASE
                        WHEN fact.country_count = 1
                         AND fact.original_amount_status IN ('valid', 'zero')
                         AND NOT ({eligible})
                        THEN fact.currency
                    END) AS conversion_original_currency,
                    SUM(CASE
                        WHEN fact.country_count = 1
                         AND fact.original_amount_status IN ('valid', 'zero')
                         AND NOT ({eligible})
                        THEN fact.original_amount_minor
                    END) AS conversion_original_minor,
                    COUNT(DISTINCT CASE
                        WHEN fact.original_amount_status = 'missing'
                        THEN fact.grant_id
                    END) AS missing_amount_count,
                    COUNT(DISTINCT CASE
                        WHEN fact.original_amount_status = 'invalid'
                        THEN fact.grant_id
                    END) AS invalid_amount_count,
                    COUNT(DISTINCT CASE
                        WHEN fact.original_amount_status = 'negative'
                        THEN fact.grant_id
                    END) AS negative_amount_count,
                    COUNT(DISTINCT fact.linked_profile_id) AS profile_count,
                    GROUP_CONCAT(DISTINCT fact.linked_profile_id) AS profile_ids,
                    GROUP_CONCAT(DISTINCT fact.linked_profile_name) AS profile_names,
                    MIN(fact.publisher_source_url) AS representative_source_url
                FROM scoped AS fact
                GROUP BY fact.source_funder_key
            ),
            identified AS (
                SELECT grouped.*, preferred.display_name
                FROM grouped
                JOIN preferred_names AS preferred
                  ON preferred.source_funder_key = grouped.source_funder_key
            ),
            searched AS (
                SELECT * FROM identified AS preferred
                {search_clause}
            ),
            status_counts AS (
                SELECT
                    COUNT(*) AS all_count,
                    SUM(CASE WHEN profile_count = 1 THEN 1 ELSE 0 END) AS linked_count,
                    SUM(CASE WHEN profile_count <> 1 THEN 1 ELSE 0 END) AS observed_only_count
                FROM searched
            ),
            filtered AS (
                SELECT * FROM searched
                {status_clause}
            ),
            filtered_summary AS (
                SELECT
                    COUNT(*) AS filtered_count,
                    COALESCE(SUM(grant_count), 0) AS matching_grant_count,
                    COALESCE(SUM(included_minor), 0) AS included_minor_total,
                    COALESCE(SUM(included_grant_count), 0) AS included_grant_total,
                    COALESCE(SUM(multi_country_grant_count), 0) AS multi_country_grant_total,
                    COALESCE(SUM(multi_country_minor), 0) AS multi_country_minor_total,
                    COALESCE(SUM(conversion_excluded_count), 0) AS conversion_excluded_total,
                    COALESCE(SUM(missing_amount_count), 0) AS missing_amount_total,
                    COALESCE(SUM(invalid_amount_count), 0) AS invalid_amount_total,
                    COALESCE(SUM(negative_amount_count), 0) AS negative_amount_total
                FROM filtered
            ),
            selected_recipients AS (
                SELECT COUNT(DISTINCT scoped.recipient_key) AS distinct_recipient_count
                FROM scoped
                JOIN filtered
                  ON filtered.source_funder_key = scoped.source_funder_key
            ),
            ranked AS (
                SELECT filtered.*,
                       ROW_NUMBER() OVER (ORDER BY {order}) AS result_rank
                FROM filtered
            ),
            paged AS (
                SELECT *
                FROM ranked
                WHERE result_rank > ? AND result_rank <= ?
            )
            SELECT paged.*,
                   status_counts.all_count, status_counts.linked_count,
                   status_counts.observed_only_count,
                   filtered_summary.filtered_count,
                   filtered_summary.matching_grant_count,
                   filtered_summary.included_minor_total,
                   filtered_summary.included_grant_total,
                   filtered_summary.multi_country_grant_total,
                   filtered_summary.multi_country_minor_total,
                   filtered_summary.conversion_excluded_total,
                   filtered_summary.missing_amount_total,
                   filtered_summary.invalid_amount_total,
                   filtered_summary.negative_amount_total,
                   selected_recipients.distinct_recipient_count
            FROM status_counts
            CROSS JOIN filtered_summary
            CROSS JOIN selected_recipients
            LEFT JOIN paged ON 1 = 1
            ORDER BY paged.result_rank
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            revision = self._ensure_overview_indexes(conn)
            country = conn.execute(
                """
                SELECT country_name
                FROM grant_beneficiary_countries
                WHERE country_code = ?
                ORDER BY country_name
                LIMIT 1
                """,
                (country_code,),
            ).fetchone()
            if not country:
                raise ValueError(
                    f"No mapped beneficiary country is available for ISO code '{country_code}'."
                )
            source_placeholders = ", ".join("?" for _ in selected_sources)
            if selected_sources:
                metadata = conn.execute(
                    f"""
                    SELECT MIN(award_date), MAX(award_date),
                           GROUP_CONCAT(DISTINCT currency)
                    FROM grant_source_funder_facts
                    WHERE source_namespace IN ({source_placeholders})
                    """,
                    selected_sources,
                ).fetchone()
            else:
                metadata = (None, None, None)
            query_params = [
                *params, *search_params, offset, offset + page_size,
            ]
            rows = conn.execute(sql, query_params).fetchall()
            item_rows = [row for row in rows if row["source_funder_key"] is not None]
            single_profile_ids = sorted({
                int(row["profile_ids"])
                for row in item_rows
                if int(row["profile_count"] or 0) == 1 and row["profile_ids"]
            })
            registry_links = self._accepted_registry_links(conn, single_profile_ids)
            profile_name_map = {
                int(row[0]): str(row[1])
                for row in conn.execute(
                    f"SELECT charity_id, name FROM charities WHERE charity_id IN ({', '.join('?' for _ in single_profile_ids)})",
                    single_profile_ids,
                ).fetchall()
            } if single_profile_ids else {}
            page_keys = [str(row["source_funder_key"]) for row in item_rows]
            programme_map: Dict[str, List[Dict[str, Any]]] = {}
            if page_keys:
                key_placeholders = ", ".join("?" for _ in page_keys)
                programme_rows = conn.execute(
                    f"""
                    SELECT fact.source_funder_key, programme.programme_area,
                           COUNT(DISTINCT fact.grant_id) AS grant_count
                    FROM grant_source_funder_facts AS fact
                    JOIN grant_programme_categories AS programme
                      ON programme.grant_id = fact.grant_id
                    WHERE {' AND '.join(where)}
                      AND fact.source_funder_key IN ({key_placeholders})
                    GROUP BY fact.source_funder_key, programme.programme_area
                    ORDER BY fact.source_funder_key, grant_count DESC,
                             LOWER(programme.programme_area)
                    """,
                    [*params, *page_keys],
                ).fetchall()
                for row in programme_rows:
                    values = programme_map.setdefault(str(row[0]), [])
                    if len(values) < 3:
                        values.append({
                            "name": row[1],
                            "count": int(row[2]),
                            "provenance": "source_or_inferred",
                        })
        finally:
            conn.close()
        summary_row = rows[0]
        available_currencies = sorted({
            value.strip().upper()
            for value in str(metadata[2] or "").split(",") if value.strip()
        })
        availability_status = "available"
        if requested_currency not in {None, "AUTO"} and requested_currency not in available_currencies:
            availability_status = "unsupported_currency"
        items: List[Dict[str, Any]] = []
        for row in item_rows:
            profile_ids = sorted({
                int(value) for value in str(row["profile_ids"] or "").split(",")
                if value.strip()
            })
            profile_link = self._source_funder_profile_link(
                profile_ids,
                profile_name_map.get(profile_ids[0]) if len(profile_ids) == 1 else None,
                registry_links,
            )
            linked_profile = (
                {
                    "charity_id": profile_link["profile_id"],
                    "name": profile_link["profile_name"],
                }
                if profile_link["status"] == "single" else None
            )
            included_minor = row["included_minor"]
            fallback_original_amount = None
            fallback_original_currency = None
            fallback_original_grant_count = 0
            if (
                auto_converted_eur
                and int(row["conversion_excluded_count"] or 0) > 0
                and int(row["conversion_original_currency_count"] or 0) == 1
                and row["conversion_original_currency"]
                and row["conversion_original_minor"] is not None
            ):
                fallback_original_amount = _minor_units_to_amount(
                    int(row["conversion_original_minor"])
                )
                fallback_original_currency = str(
                    row["conversion_original_currency"]
                )
                fallback_original_grant_count = int(
                    row["conversion_excluded_count"]
                )
            identity = {
                "key": row["source_funder_key"],
                "namespace": row["source_namespace"],
                "method": row["identity_method"],
                "source_organization_id": (
                    str(row["source_ids"]).split(",")[0]
                    if row["source_ids"] else None
                ),
                "normalized_name_fallback": row["normalized_name_fallback"],
            }
            items.append({
                "rank": int(row["result_rank"]),
                "kind": "source_funder",
                "identity": identity,
                "source_funder_key": row["source_funder_key"],
                "display_name": row["display_name"],
                "identity_method": row["identity_method"],
                "source_ids": sorted({
                    value for value in str(row["source_ids"] or "").split(",")
                    if value
                }),
                "sources": [row["source_namespace"]],
                "evidence_sources": [row["source_namespace"]],
                "source_only": profile_link["status"] != "single",
                "linked_directory_profile": linked_profile,
                "profile_link": profile_link,
                "activity": {
                    "grant_count": int(row["grant_count"]),
                    "distinct_recipient_count": int(row["recipient_count"]),
                    "first_award_date": row["first_award_date"],
                    "latest_award_date": row["latest_award_date"],
                },
                "observed_activity": {
                    "grant_count": int(row["grant_count"]),
                    "recipient_count": int(row["recipient_count"]),
                    "latest_grant_date": row["latest_award_date"],
                    "observed_funding": (
                        _minor_units_to_amount(int(included_minor))
                        if included_minor is not None else None
                    ),
                    "displayed_currency": display_currency,
                    "programme_areas": programme_map.get(str(row["source_funder_key"]), []),
                },
                "observed_funding": {
                    "amount": (
                        _minor_units_to_amount(int(included_minor))
                        if included_minor is not None else None
                    ),
                    "currency": display_currency,
                    "included_grant_count": int(row["included_grant_count"]),
                    "excluded_multi_country_grant_count": int(row["multi_country_grant_count"]),
                    "excluded_multi_country_amount": _minor_units_to_amount(
                        int(row["multi_country_minor"])
                    ),
                    "excluded_conversion_grant_count": int(row["conversion_excluded_count"]),
                    "excluded_missing_amount_grant_count": int(row["missing_amount_count"]),
                    "excluded_invalid_amount_grant_count": int(row["invalid_amount_count"]),
                    "excluded_negative_amount_grant_count": int(row["negative_amount_count"]),
                    "fallback_original_amount": fallback_original_amount,
                    "fallback_original_currency": fallback_original_currency,
                    "fallback_original_grant_count": fallback_original_grant_count,
                },
                "amount_policy": {
                    "mode": (
                        "automatic_eur" if auto_converted_eur
                        else "original_currency"
                    ),
                    "converted_grant_count": int(row["included_grant_count"]),
                    "unconverted_grant_count": int(row["conversion_excluded_count"]),
                    "multi_country_amount_excluded": _minor_units_to_amount(
                        int(row["multi_country_minor"])
                    ),
                },
                "leading_programme_areas": programme_map.get(
                    str(row["source_funder_key"]), []
                ),
                "representative_source_url": _safe_external_url(
                    row["representative_source_url"]
                ),
            })
        total_items = int(summary_row["filtered_count"] or 0)
        total_pages = (total_items + page_size - 1) // page_size
        return {
            "status": (
                "available" if items or total_items
                else availability_status
                if availability_status != "available"
                else "no_matching_funders"
            ),
            "country": {"code": country_code, "name": country[0]},
            "summary": {
                "matching_funder_count": total_items,
                "matching_grant_count": int(summary_row["matching_grant_count"] or 0),
                "unattributed_funder_grant_count": 0,
                "distinct_recipient_count": int(summary_row["distinct_recipient_count"] or 0),
                "source_only_funder_count": int(summary_row["observed_only_count"] or 0),
                "linked_directory_funder_count": int(summary_row["linked_count"] or 0),
                "status_counts": {
                    "all": int(summary_row["all_count"] or 0),
                    "linked": int(summary_row["linked_count"] or 0),
                    "observed_only": int(summary_row["observed_only_count"] or 0),
                },
                "monetary": {
                    "currency_mode": (
                        "auto_converted_eur" if auto_converted_eur
                        else "source_currency"
                    ),
                    "display_currency": display_currency,
                    "included_funding_total": (
                        _minor_units_to_amount(int(summary_row["included_minor_total"]))
                        if int(summary_row["included_grant_total"] or 0) else None
                    ),
                    "included_grant_count": int(summary_row["included_grant_total"] or 0),
                    "excluded_multi_country_grant_count": int(summary_row["multi_country_grant_total"] or 0),
                    "excluded_multi_country_amount": _minor_units_to_amount(
                        int(summary_row["multi_country_minor_total"] or 0)
                    ),
                    "excluded_conversion_grant_count": int(summary_row["conversion_excluded_total"] or 0),
                    "excluded_missing_amount_grant_count": int(summary_row["missing_amount_total"] or 0),
                    "excluded_invalid_amount_grant_count": int(summary_row["invalid_amount_total"] or 0),
                    "excluded_negative_amount_grant_count": int(summary_row["negative_amount_total"] or 0),
                },
            },
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
            },
            "available_date_range": {
                "from": metadata[0],
                "to": metadata[1],
            },
            "available_currencies": available_currencies,
            "available_sort_modes": [
                "largest_observed_funding", "most_grants", "most_recently_active",
            ],
            "applied_filters": {
                "beneficiary_country": {"code": country_code, "name": country[0]},
                "currency": "auto" if auto_converted_eur else requested_currency,
                "currency_mode": (
                    "auto_converted_eur" if auto_converted_eur
                    else "source_currency"
                ),
                "display_currency": display_currency,
                "date_from": date_from,
                "date_to": date_to,
                "beneficiary_geographies": beneficiary_geographies or [],
                "programme_areas": programme_areas or [],
                "donor": str(donor or "").strip() or None,
                "recipient": str(recipient or "").strip() or None,
                "sources": selected_sources,
                "search": search_value or None,
                "profile_status": profile_status,
                "sort": sort,
            },
            "metadata": {
                "data_mode": "derived_source_funder_facts",
                "data_revision": revision,
                "identity_policy": (
                    "source namespace plus source funder id; normalized name fallback"
                ),
                "country_amount_policy": (
                    "Multi-country awards count once for activity but are excluded "
                    "from country-attributable funding totals."
                ),
                "scope_note": GRANT_SCOPE_NOTE,
                "generated_at": _utc_now(),
            },
        }

    async def get_source_funders(
        self,
        *,
        beneficiary_country: str,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
        search: Optional[str] = None,
        profile_status: str = "all",
        sort: str = "largest_observed_funding",
        page: int = 1,
        page_size: int = 25,
    ) -> Dict[str, Any]:
        sort = {
            "most_active": "most_grants",
            "most_recent": "most_recently_active",
        }.get(sort, sort)
        if sort not in {"largest_observed_funding", "most_grants", "most_recently_active"}:
            raise ValueError("sort must be largest_observed_funding, most_grants, or most_recently_active.")
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        return self._source_funder_list_from_facts(
            beneficiary_country=beneficiary_country,
            currency=currency,
            date_from=date_from,
            date_to=date_to,
            beneficiary_geographies=beneficiary_geographies,
            programme_areas=programme_areas,
            donor=donor,
            recipient=recipient,
            sources=sources,
            search=search,
            profile_status=profile_status,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    async def get_source_funder_detail(
        self,
        source_funder_key: str,
        **filters: Any,
    ) -> Optional[Dict[str, Any]]:
        detail_level = str(filters.get("detail_level") or "full").strip().lower()
        if detail_level not in {"summary", "full"}:
            raise ValueError("detail_level must be summary or full.")
        country_code = str(filters.get("beneficiary_country") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country_code):
            raise ValueError("beneficiary_country must be an ISO 3166-1 alpha-2 code.")
        where, params, _ = self._source_funder_fact_filters(
            beneficiary_country=country_code,
            currency=filters.get("currency"),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
            beneficiary_geographies=filters.get("beneficiary_geographies"),
            programme_areas=filters.get("programme_areas"),
            donor=filters.get("donor"),
            recipient=filters.get("recipient"),
            sources=filters.get("sources"),
        )
        where.append("fact.source_funder_key = ?")
        params.append(source_funder_key)
        requested_currency = str(filters.get("currency") or "").strip().upper() or None
        auto_converted_eur = requested_currency in {None, "AUTO"}
        display_currency = "EUR" if auto_converted_eur else requested_currency
        valid_conversion_statuses = {
            "native_eur", "ecb_award_date", "ecb_previous_business_day",
        }
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            revision = self._ensure_overview_indexes(conn)
            country = conn.execute(
                """
                SELECT country_name
                FROM grant_beneficiary_countries
                WHERE country_code = ?
                ORDER BY country_name
                LIMIT 1
                """,
                (country_code,),
            ).fetchone()
            if not country:
                raise ValueError(
                    f"No mapped beneficiary country is available for ISO code '{country_code}'."
                )
            rows = conn.execute(
                f"""
                SELECT fact.*
                FROM grant_source_funder_facts AS fact
                WHERE {' AND '.join(where)}
                ORDER BY fact.award_date DESC, fact.grant_id
                """,
                params,
            ).fetchall()
            if not rows:
                return None
            programme_rows = conn.execute(
                f"""
                SELECT programme.programme_area, COUNT(DISTINCT fact.grant_id)
                FROM grant_source_funder_facts AS fact
                JOIN grant_programme_categories AS programme
                  ON programme.grant_id = fact.grant_id
                WHERE {' AND '.join(where)}
                GROUP BY programme.programme_area
                ORDER BY COUNT(DISTINCT fact.grant_id) DESC,
                         LOWER(programme.programme_area)
                """,
                params,
            ).fetchall()
            sample_rows = [] if detail_level == "summary" else conn.execute(
                f"""
                SELECT fact.grant_id, fact.recipient_name, fact.award_date,
                       fact.currency, fact.original_amount_minor,
                       fact.eur_amount_minor, fact.original_amount_status,
                       fact.eur_amount_status, fact.conversion_status,
                       grant.amount, grant.description, grant.source_url,
                       grant.raw_grant_data
                FROM grant_source_funder_facts AS fact
                JOIN grants AS grant ON grant.grant_id = fact.grant_id
                WHERE {' AND '.join(where)}
                ORDER BY fact.award_date DESC, fact.grant_id
                LIMIT 50
                """,
                params,
            ).fetchall()
            profile_ids = sorted({
                int(row["linked_profile_id"])
                for row in rows if row["linked_profile_id"] is not None
            })
            profile_rows: Dict[int, sqlite3.Row] = {}
            if profile_ids:
                placeholders = ", ".join("?" for _ in profile_ids)
                profile_rows = {
                    int(row["charity_id"]): row
                    for row in conn.execute(
                        f"""
                        SELECT charity_id, name, website, source_url
                        FROM charities
                        WHERE charity_id IN ({placeholders})
                        """,
                        profile_ids,
                    ).fetchall()
                }
            registry_links = self._accepted_registry_links(conn, profile_ids)
        finally:
            conn.close()

        names = Counter(str(row["display_name"]) for row in rows)
        display_name = _top_counter_items(names, 1)[0]["name"]
        recipient_aggregates: Dict[str, Dict[str, Any]] = {}
        included_minor = 0
        included_grants = 0
        multi_country_minor = 0
        exclusions: Counter[str] = Counter()
        first_award_date: Optional[str] = None
        latest_award_date: Optional[str] = None
        for row in rows:
            award_date = row["award_date"]
            if award_date:
                first_award_date = min(first_award_date or award_date, award_date)
                latest_award_date = max(latest_award_date or award_date, award_date)
            amount_status = (
                row["eur_amount_status"] if auto_converted_eur
                else row["original_amount_status"]
            )
            amount_minor = (
                row["eur_amount_minor"] if auto_converted_eur
                else row["original_amount_minor"]
            )
            conversion_available = (
                str(row["conversion_status"] or "") in valid_conversion_statuses
                if auto_converted_eur else True
            )
            recipient = recipient_aggregates.setdefault(str(row["recipient_key"]), {
                "name_counts": Counter(),
                "grant_count": 0,
                "included_grant_count": 0,
                "minor_units": 0,
                "latest_award_date": None,
            })
            recipient["name_counts"][str(row["recipient_name"])] += 1
            recipient["grant_count"] += 1
            recipient["latest_award_date"] = max(
                recipient["latest_award_date"] or award_date or "",
                award_date or "",
            ) or None
            if int(row["country_count"] or 0) > 1:
                exclusions["multi_country_award"] += 1
                if conversion_available and amount_status in {"valid", "zero"}:
                    multi_country_minor += int(amount_minor or 0)
                continue
            if not conversion_available:
                exclusions["conversion_unavailable"] += 1
                continue
            if amount_status not in {"valid", "zero"}:
                exclusions[f"{amount_status}_amount"] += 1
                continue
            included_grants += 1
            included_minor += int(amount_minor or 0)
            recipient["included_grant_count"] += 1
            recipient["minor_units"] += int(amount_minor or 0)
            if int(amount_minor or 0) <= 0:
                exclusions["non_positive_amount"] += 1

        programme_areas = [
            {"name": row[0], "count": int(row[1]), "provenance": "source_or_inferred"}
            for row in programme_rows
        ]
        recipient_items = []
        for recipient_key, aggregate in recipient_aggregates.items():
            # In Auto/EUR mode, an award with no valid official conversion must
            # not look like a real €0 award. It remains represented in the
            # activity and exclusion metadata, but is omitted from money-ranked
            # recipient lists until a valid ECB conversion is available.
            if auto_converted_eur and not aggregate["included_grant_count"]:
                continue
            recipient_name = _top_counter_items(aggregate["name_counts"], 1)[0]["name"]
            recipient_items.append({
                "recipient_key": recipient_key,
                "name": recipient_name,
                "grant_count": int(aggregate["grant_count"]),
                "included_grant_count": int(aggregate["included_grant_count"]),
                "observed_funding": _minor_units_to_amount(aggregate["minor_units"]),
                "currency": display_currency,
                "latest_award_date": aggregate["latest_award_date"],
            })
        recipient_items.sort(key=lambda item: item["name"].casefold())
        recipient_items.sort(key=lambda item: item["latest_award_date"] or "", reverse=True)
        recipient_items.sort(key=lambda item: item["grant_count"], reverse=True)
        recipient_items.sort(key=lambda item: item["observed_funding"], reverse=True)
        top_recipients = recipient_items[:50]

        retained_recipients = [
            item for item in recipient_items if item["observed_funding"] > 0
        ][:15]
        relationships = {
            "status": "available" if retained_recipients else "no_monetary_transactions",
            "nodes": ([{
                "id": source_funder_key,
                "label": display_name,
                "role": "donor",
            }] + [{
                "id": item["recipient_key"],
                "label": item["name"],
                "role": "recipient",
            } for item in retained_recipients]) if retained_recipients else [],
            "links": [{
                "source": source_funder_key,
                "target": item["recipient_key"],
                "value": item["observed_funding"],
                "currency": display_currency,
                "grant_count": item["included_grant_count"],
            } for item in retained_recipients],
            "metadata": {
                "identity_basis": "canonical source funder identity",
                "country_amount_policy": (
                    "Only single-country observed awards are included; full "
                    "multi-country awards are not duplicated across countries."
                ),
                "currency": display_currency,
                "grant_count": len(rows),
                "included_grant_count": sum(
                    item["included_grant_count"] for item in retained_recipients
                ),
                "included_value": round(sum(
                    item["observed_funding"] for item in retained_recipients
                ), 2),
                "excluded_grant_count": len(rows) - sum(
                    item["included_grant_count"] for item in retained_recipients
                ),
                "excluded_reasons": dict(exclusions),
                "limit": 15,
                "truncation_applied": len(recipient_items) > 15,
            },
        }

        profile_name = None
        if len(profile_ids) == 1 and profile_ids[0] in profile_rows:
            profile_name = profile_rows[profile_ids[0]]["name"]
        profile_link = self._source_funder_profile_link(
            profile_ids, profile_name, registry_links,
        )
        if profile_link["status"] == "single":
            profile = profile_rows.get(profile_link["profile_id"])
            if profile and detail_level == "full":
                profile_link["website"] = _safe_external_url(profile["website"])
                profile_link["source_url"] = _safe_external_url(profile["source_url"])
        linked_profile = (
            {
                "charity_id": profile_link["profile_id"],
                "name": profile_link["profile_name"],
            }
            if profile_link["status"] == "single" else None
        )
        identity = {
            "key": source_funder_key,
            "namespace": rows[0]["source_namespace"],
            "method": rows[0]["identity_method"],
            "source_organization_id": rows[0]["source_organization_id"],
            "normalized_name_fallback": rows[0]["normalized_name_fallback"],
        }
        item = {
            "kind": "source_funder",
            "identity": identity,
            "source_funder_key": source_funder_key,
            "display_name": display_name,
            "identity_method": rows[0]["identity_method"],
            "source_ids": sorted({
                str(row["source_organization_id"])
                for row in rows if row["source_organization_id"]
            }),
            "sources": sorted({str(row["source_namespace"]) for row in rows}),
            "evidence_sources": sorted({str(row["source_namespace"]) for row in rows}),
            "source_only": profile_link["status"] != "single",
            "linked_directory_profile": linked_profile,
            "profile_link": profile_link,
            "activity": {
                "grant_count": len(rows),
                "distinct_recipient_count": len(recipient_aggregates),
                "first_award_date": first_award_date,
                "latest_award_date": latest_award_date,
            },
            "observed_activity": {
                "grant_count": len(rows),
                "recipient_count": len(recipient_aggregates),
                "latest_grant_date": latest_award_date,
                "observed_funding": (
                    _minor_units_to_amount(included_minor) if included_grants else None
                ),
                "displayed_currency": display_currency,
                "programme_areas": programme_areas[:3],
            },
            "observed_funding": {
                "amount": _minor_units_to_amount(included_minor) if included_grants else None,
                "currency": display_currency,
                "included_grant_count": included_grants,
                "excluded_multi_country_grant_count": exclusions["multi_country_award"],
                "excluded_multi_country_amount": _minor_units_to_amount(multi_country_minor),
                "excluded_conversion_grant_count": exclusions["conversion_unavailable"],
                "excluded_missing_amount_grant_count": exclusions["missing_amount"],
                "excluded_invalid_amount_grant_count": exclusions["invalid_amount"],
                "excluded_negative_amount_grant_count": exclusions["negative_amount"],
            },
            "amount_policy": {
                "mode": "automatic_eur" if auto_converted_eur else "original_currency",
                "converted_grant_count": included_grants,
                "unconverted_grant_count": exclusions["conversion_unavailable"],
                "multi_country_amount_excluded": _minor_units_to_amount(multi_country_minor),
            },
            "leading_programme_areas": programme_areas[:3],
            "representative_source_url": next((
                row["publisher_source_url"] for row in rows
                if row["publisher_source_url"]
            ), None),
        }

        source_evidence: List[Dict[str, Any]] = []
        evidence_seen: set[Tuple[str, str]] = set()
        grant_sample = []
        for row in sample_rows:
            evidence_links = _source_evidence_links(
                row["raw_grant_data"], row["source_url"],
                funder_name=display_name,
                recipient_name=str(row["recipient_name"] or ""),
            )
            for evidence in evidence_links:
                marker = (evidence["kind"], evidence["url"])
                if marker not in evidence_seen:
                    evidence_seen.add(marker)
                    source_evidence.append(evidence)
            amount_minor = (
                row["eur_amount_minor"] if auto_converted_eur
                else row["original_amount_minor"]
            )
            amount_status = (
                row["eur_amount_status"] if auto_converted_eur
                else row["original_amount_status"]
            )
            conversion_available = (
                str(row["conversion_status"] or "") in valid_conversion_statuses
                if auto_converted_eur else True
            )
            grant_sample.append({
                "grant_id": str(row["grant_id"]),
                "recipient_name": str(row["recipient_name"]),
                "award_date": row["award_date"],
                "amount": (
                    _minor_units_to_amount(int(amount_minor or 0))
                    if conversion_available and amount_status in {"valid", "zero"}
                    else None
                ),
                "currency": display_currency,
                "original_amount": row["amount"],
                "original_currency": row["currency"],
                "source_url": _safe_external_url(row["source_url"]),
                "description": row["description"],
                "evidence_links": evidence_links,
            })
        if profile_link.get("website"):
            source_evidence.append({
                "kind": "profile_website",
                "label": f"{profile_link['profile_name']} · funder website",
                "role": "funder",
                "organization_name": profile_link["profile_name"],
                "link_type": "website",
                "url": profile_link["website"],
                "origin": "enriched_profile",
            })
        if profile_link.get("source_url"):
            profile_source_url = profile_link["source_url"]
            source_evidence.append({
                "kind": "profile_source",
                "label": f"{profile_link['profile_name']} · profile source",
                "role": "funder",
                "organization_name": profile_link["profile_name"],
                "link_type": _evidence_link_type("profile_source", profile_source_url),
                "url": profile_source_url,
                "origin": "enriched_profile",
            })
        return {
            "status": "available",
            "country": {"code": country_code, "name": country[0]},
            "funder": item,
            "top_recipients": top_recipients if detail_level == "full" else [],
            "relationships": relationships if detail_level == "full" else {
                "status": "lazy",
                "nodes": [],
                "links": [],
                "metadata": {
                    "grant_count": len(rows),
                    "recipient_count": len(recipient_aggregates),
                    "currency": display_currency,
                },
            },
            "grant_sample": grant_sample,
            "source_evidence": source_evidence[:40],
            "relationship_summary": {
                "recipient_count": len(recipient_aggregates),
                "country_attributable_recipient_count": len([
                    item for item in recipient_items if item["observed_funding"] > 0
                ]),
                "detail_available": True,
            },
            "metadata": {
                "detail_type": "source_funder",
                "detail_level": detail_level,
                "data_mode": "derived_source_funder_facts",
                "data_revision": revision,
                "grant_sample_limit": 50,
                "external_link_policy": (
                    "Stored HTTP(S) links only; no server-side fetch, proxy, or preflight."
                ),
                "scope_note": GRANT_SCOPE_NOTE,
            },
        }

    async def get_beneficiary_geography_options(
        self, sources: Optional[List[str]] = None,
    ) -> List[str]:
        """Fallback for non-SQL repositories without a derived country index."""
        overview = await self.get_grant_overview(sources=sources)
        return sorted({
            str(item.get("region_or_country_name") or "").strip()
            for item in overview.get("map", {}).get("items", [])
            if str(item.get("region_or_country_name") or "").strip()
        })

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
        sources: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        max_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        max_avg_grant_size: Optional[float] = None,
        include_score: bool = False,
        sort: str = "name_asc",
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        filtered = self._data

        def enrichment(item):
            return enrich_organization(item)

        if sources is not None:
            selected_sources = {str(source).strip().casefold() for source in sources if str(source).strip()}
            if not selected_sources:
                filtered = []
            else:
                filtered = [
                    item for item in filtered
                    if selected_sources.intersection({
                        str(item.get("primary_source") or "Charity Commission for England and Wales").casefold(),
                        *{
                            str(value).casefold()
                            for value in (item.get("source_names") or [])
                        },
                    })
                ]

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
        if max_annual_giving is not None:
            filtered = [
                c for c in filtered
                if (self._get_financials(c)[1] or 0.0) <= max_annual_giving
            ]
        if min_annual_giving is None and max_annual_giving is None and size:
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
        if max_avg_grant_size is not None:
            filtered = [
                c for c in filtered
                if (self._get_financials(c)[1] or 0.0) <= max_avg_grant_size
            ]

        # Global score ordering has to happen before pagination. The JSON
        # repository is only a fallback data source, so mapping the filtered
        # set here is acceptable and keeps every page globally consistent.
        score_config = load_score_configuration() if include_score or sort == "score_desc" else None
        results = []
        for c in filtered:
            income, expenditure = self._get_financials(c)
            all_details = c.get("all_details", {})
            enriched = enrichment(c)
            result = {
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
            }
            if score_config:
                result.update(_score_summary(score_relevance(
                    {**result, "annual_expenditure": expenditure},
                    score_config.example_target_profile,
                    grant_statistics={},
                    configuration=score_config,
                )))
            results.append(result)
        if sort == "score_desc":
            results.sort(key=lambda item: (
                item.get("relevance_score") is None,
                -(float(item["relevance_score"]) if item.get("relevance_score") is not None else 0.0),
                str(item.get("charity_name") or "").casefold(),
            ))
        elif sort == "income_desc":
            results.sort(key=lambda item: (
                item.get("latest_income") is None,
                -(float(item["latest_income"]) if item.get("latest_income") is not None else 0.0),
                str(item.get("charity_name") or "").casefold(),
            ))
        else:
            results.sort(key=lambda item: str(item.get("charity_name") or "").casefold())
        return results[skip : skip + limit]

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

    async def get_grant_overview(
        self,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[List[str]] = None,
        programme_areas: Optional[List[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[List[str]] = None,
        granularity: str = "auto",
        include_connections: bool = False,
    ) -> Dict[str, Any]:
        """Keep the offline JSON fallback explicit and safely non-transactional.

        The full Overview aggregation relies on normalized SQLite grant rows.
        Returning an unavailable payload here prevents the fallback repository
        from attempting to open a SQLite connection that it does not own.
        """
        return {
            "status": "transaction_data_unavailable",
            "kpis": {
                "awarded_funding": None,
                "currency": currency.upper() if currency else None,
                "grants_monitored": 0,
                "country_coverage_percentage": 0.0,
                "mapped_grant_count": 0,
                "unmapped_grant_count": 0,
                "programme_coverage_percentage": 0.0,
                "classified_grant_count": 0,
                "qualifying_programme_grant_count": 0,
            },
            "map": await self.get_grants_map(currency=currency),
            "trends": await self.get_grant_trends(currency=currency),
            "themes": await self.get_grant_themes(currency=currency),
            "available_date_range": {"from": None, "to": None},
            "applied_filters": {
                "currency": currency.upper() if currency else None,
                "date_from": date_from,
                "date_to": date_to,
                "beneficiary_geographies": beneficiary_geographies or [],
                "programme_areas": programme_areas or [],
                "donor": donor or None,
                "recipient": recipient or None,
                "granularity": granularity,
            },
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
        self._overview_revision: Optional[str] = None
        self._overview_source_metadata_cache: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
        self._grant_entity_suggestion_cache: Dict[Tuple[str, Tuple[str, ...], int], Dict[str, Any]] = {}
        # Additive migration: existing enriched profiles and grants are left intact.
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                overview_tables = {
                    "grant_beneficiary_terms", "grant_beneficiary_countries",
                    "grant_programme_categories", "grant_source_funder_facts",
                    "grant_overview_facts",
                    "grant_overview_cache",
                }
                if not overview_tables.issubset(tables):
                    migrate_grant_overview_schema(conn)
                # The importer keeps the Registry FTS synchronized through
                # triggers. Only older databases missing this layer need a
                # migration; avoiding repeated DDL keeps BFF startup quick.
                if not {REGISTRY_TABLE, REGISTRY_LINK_TABLE, REGISTRY_FTS_TABLE}.issubset(tables):
                    migrate_registry_schema(conn, synchronize_fts=False)
                conn.commit()
            finally:
                conn.close()
        logger.info(f"SQLite Charity Repository initialized at: {self.db_path}")
        
    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    async def get_beneficiary_geography_options(
        self, sources: Optional[List[str]] = None,
    ) -> List[str]:
        conn = self._get_conn()
        try:
            self._ensure_overview_indexes(conn)
            selected_sources = (
                [str(source).strip() for source in sources if str(source).strip()]
                if sources is not None else ["360Giving"]
            )
            if not selected_sources:
                return []
            placeholders = ", ".join("?" for _ in selected_sources)
            rows = conn.execute(
                f"""
                SELECT DISTINCT country.country_name
                FROM grant_beneficiary_countries AS country
                JOIN grants AS grant_row ON grant_row.grant_id = country.grant_id
                WHERE grant_row.source IN ({placeholders})
                ORDER BY country.country_name COLLATE NOCASE
                """,
                selected_sources,
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    async def get_grant_entity_suggestions(
        self,
        *,
        sources: Optional[List[str]] = None,
        limit: int = 2_500,
    ) -> Dict[str, Any]:
        """Build an in-memory name index from already-derived grant facts.

        This is intentionally unfiltered by the active drawer fields. It is a
        source-scoped autocomplete cache, not another grant-analysis request;
        selecting a suggestion only changes the local draft until Apply.
        """
        selected_sources = (
            [str(source).strip() for source in sources if str(source).strip()]
            if sources is not None else ["360Giving"]
        )
        bounded_limit = min(max(int(limit), 1), 5_000)
        if not selected_sources:
            return {"status": "available", "donors": [], "recipients": []}

        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            revision = self._ensure_overview_indexes(conn)
            cache_key = (
                revision,
                tuple(sorted({source.casefold() for source in selected_sources})),
                bounded_limit,
            )
            cached = self._grant_entity_suggestion_cache.get(cache_key)
            if cached is not None:
                return {
                    "status": "available",
                    "donors": list(cached["donors"]),
                    "recipients": list(cached["recipients"]),
                }

            placeholders = ", ".join("?" for _ in selected_sources)
            donor_rows = conn.execute(
                f"""
                SELECT display_name AS name, COUNT(DISTINCT grant_id) AS grant_count
                FROM grant_source_funder_facts
                WHERE source_namespace IN ({placeholders})
                  AND TRIM(display_name) <> ''
                  AND display_name <> 'Unnamed source funder'
                GROUP BY source_funder_key, display_name
                ORDER BY grant_count DESC, LOWER(display_name)
                LIMIT ?
                """,
                [*selected_sources, bounded_limit],
            ).fetchall()
            recipient_rows = conn.execute(
                f"""
                SELECT recipient_name AS name, COUNT(DISTINCT grant_id) AS grant_count
                FROM grant_source_funder_facts
                WHERE source_namespace IN ({placeholders})
                  AND TRIM(recipient_name) <> ''
                  AND recipient_name <> 'Unnamed recipient'
                GROUP BY recipient_key, recipient_name
                ORDER BY grant_count DESC, LOWER(recipient_name)
                LIMIT ?
                """,
                [*selected_sources, bounded_limit],
            ).fetchall()
        finally:
            conn.close()

        result = {
            "status": "available",
            "donors": [
                {"name": str(row["name"]), "grant_count": int(row["grant_count"])}
                for row in donor_rows
            ],
            "recipients": [
                {"name": str(row["name"]), "grant_count": int(row["grant_count"])}
                for row in recipient_rows
            ],
        }
        self._grant_entity_suggestion_cache[cache_key] = result
        return {
            "status": "available",
            "donors": list(result["donors"]),
            "recipients": list(result["recipients"]),
        }

    @staticmethod
    def _registry_grant_exists_sql(registry_alias: str = "registry") -> str:
        return f"""
            EXISTS (
              SELECT 1
              FROM {REGISTRY_LINK_TABLE} AS grant_link
              JOIN grants AS grant
                ON grant.funding_charity_id = grant_link.enriched_organization_id
                OR grant.recipient_charity_id = grant_link.enriched_organization_id
              WHERE grant_link.registry_id = {registry_alias}.registry_id
                AND grant_link.match_status = 'accepted'
            )
        """

    @staticmethod
    def _registry_philea_exists_sql(registry_alias: str = "registry") -> str:
        return f"""
            EXISTS (
              SELECT 1
              FROM {REGISTRY_LINK_TABLE} AS profile_link
              JOIN charities AS profile
                ON profile.charity_id = profile_link.enriched_organization_id
              WHERE profile_link.registry_id = {registry_alias}.registry_id
                AND profile_link.match_status = 'accepted'
                AND (
                  profile.primary_source = 'Philea'
                  OR EXISTS (
                    SELECT 1 FROM json_each(profile.source_names)
                    WHERE value = 'Philea'
                  )
                )
            )
        """

    async def get_registry_page(
        self,
        query: Optional[str] = None,
        charity_number: Optional[str] = None,
        status: Optional[str] = None,
        income_min: Optional[float] = None,
        income_max: Optional[float] = None,
        expenditure_min: Optional[float] = None,
        expenditure_max: Optional[float] = None,
        country: Optional[str] = None,
        region: Optional[str] = None,
        beneficiary_geography: Optional[str] = None,
        has_enriched_profile: Optional[bool] = None,
        has_grant_data: Optional[bool] = None,
        cursor: Optional[str] = None,
        limit: int = REGISTRY_DEFAULT_PAGE_SIZE,
        sort: str = "name",
    ) -> Dict[str, Any]:
        """Search registry records in SQL with deterministic keyset pagination."""
        if sort not in REGISTRY_SORTS:
            raise ValueError(f"Unsupported directory sort '{sort}'.")
        if not 1 <= limit <= REGISTRY_MAX_PAGE_SIZE:
            raise ValueError(f"Directory limit must be between 1 and {REGISTRY_MAX_PAGE_SIZE}.")
        query = (query or "").strip()
        charity_number = (charity_number or "").strip()
        if len(query) > 160 or len(charity_number) > 64:
            raise ValueError("Directory search input is too long.")
        decoded_cursor = _decode_registry_cursor(cursor, sort)
        conn = self._get_conn()
        try:
            fts_available = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (REGISTRY_FTS_TABLE,),
                ).fetchone()
            )
            joins: List[str] = []
            where: List[str] = ["registry.is_current_source_record = 1"]
            params: List[Any] = []
            search_strategy = "none"

            if charity_number:
                where.append("(registry.charity_number = ? OR registry.linked_charity_number = ?)")
                params.extend([charity_number, charity_number])
                search_strategy = "exact_charity_number"
            elif query:
                normalized_query = normalize_organization_name(query)
                if not normalized_query:
                    raise ValueError("Directory search query contains no searchable characters.")
                fts_expression = _fts_query(query)
                if fts_available and fts_expression:
                    joins.append(f"JOIN {REGISTRY_FTS_TABLE} AS registry_fts ON registry_fts.registry_id = registry.registry_id")
                    where.append(f"{REGISTRY_FTS_TABLE} MATCH ?")
                    params.append(fts_expression)
                    search_strategy = "fts5"
                else:
                    where.append("registry.normalized_name >= ? AND registry.normalized_name < ?")
                    params.extend([normalized_query, _prefix_upper_bound(normalized_query)])
                    search_strategy = "indexed_prefix"

            if status:
                where.append("registry.registration_status = ? COLLATE NOCASE")
                params.append(status.strip())
            if income_min is not None:
                where.append("registry.income >= ?")
                params.append(income_min)
            if income_max is not None:
                where.append("registry.income <= ?")
                params.append(income_max)
            if expenditure_min is not None:
                where.append("registry.expenditure >= ?")
                params.append(expenditure_min)
            if expenditure_max is not None:
                where.append("registry.expenditure <= ?")
                params.append(expenditure_max)
            if country:
                where.append("registry.country_code = ? COLLATE NOCASE")
                params.append(country.strip().upper())
            if region:
                where.append("registry.administrative_region = ? COLLATE NOCASE")
                params.append(region.strip())

            if beneficiary_geography:
                # This is deliberately grant-side geography. Registry office fields
                # are never consulted for a map-to-directory handoff.
                where.append(
                    f"""EXISTS (
                        SELECT 1
                        FROM {REGISTRY_LINK_TABLE} AS beneficiary_link
                        JOIN grants AS beneficiary_grant
                          ON beneficiary_grant.funding_charity_id = beneficiary_link.enriched_organization_id
                          OR beneficiary_grant.recipient_charity_id = beneficiary_link.enriched_organization_id
                        JOIN json_each(beneficiary_grant.beneficiary_geography_normalized) AS beneficiary_location
                        WHERE beneficiary_link.registry_id = registry.registry_id
                          AND beneficiary_link.match_status = 'accepted'
                          AND (
                            json_extract(beneficiary_location.value, '$.name') = ? COLLATE NOCASE
                            OR json_extract(beneficiary_location.value, '$.macro_region') = ? COLLATE NOCASE
                          )
                    )"""
                )
                params.extend([beneficiary_geography.strip(), beneficiary_geography.strip()])

            enriched_exists = f"EXISTS (SELECT 1 FROM {REGISTRY_LINK_TABLE} AS profile_link WHERE profile_link.registry_id = registry.registry_id AND profile_link.match_status = 'accepted')"
            grant_exists = self._registry_grant_exists_sql("registry")
            if has_enriched_profile is not None:
                where.append(enriched_exists if has_enriched_profile else f"NOT ({enriched_exists})")
            if has_grant_data is not None:
                where.append(grant_exists if has_grant_data else f"NOT ({grant_exists})")

            cursor_select = "registry.normalized_name AS cursor_value, 0 AS cursor_null"
            order_by = "registry.normalized_name ASC, registry.registry_id ASC"
            if sort == "income_desc":
                cursor_select = "registry.income AS cursor_value, CASE WHEN registry.income IS NULL THEN 1 ELSE 0 END AS cursor_null"
                # SQLite sorts NULL values after numeric values for DESC. Keeping
                # the expression out of ORDER BY lets its financial indexes serve
                # the common first-page and keyset queries without a temp sort.
                order_by = "registry.income DESC, registry.registry_id ASC"
            elif sort == "expenditure_desc":
                cursor_select = "registry.expenditure AS cursor_value, CASE WHEN registry.expenditure IS NULL THEN 1 ELSE 0 END AS cursor_null"
                order_by = "registry.expenditure DESC, registry.registry_id ASC"

            if decoded_cursor:
                if sort == "name":
                    cursor_value = decoded_cursor.get("value")
                    if not isinstance(cursor_value, str):
                        raise ValueError("Invalid name-sort directory cursor.")
                    where.append(
                        "(registry.normalized_name > ? OR (registry.normalized_name = ? AND registry.registry_id > ?))"
                    )
                    params.extend([cursor_value, cursor_value, decoded_cursor["registry_id"]])
                else:
                    cursor_null = decoded_cursor.get("is_null")
                    cursor_value = decoded_cursor.get("value")
                    if cursor_null not in {0, 1} or (cursor_null == 0 and not isinstance(cursor_value, (int, float))):
                        raise ValueError("Invalid financial-sort directory cursor.")
                    field = "registry.income" if sort == "income_desc" else "registry.expenditure"
                    null_expression = f"CASE WHEN {field} IS NULL THEN 1 ELSE 0 END"
                    if cursor_null == 1:
                        where.append(f"({null_expression} = 1 AND registry.registry_id > ?)")
                        params.append(decoded_cursor["registry_id"])
                    else:
                        where.append(
                            f"({null_expression} > 0 OR ({null_expression} = 0 AND ({field} < ? OR ({field} = ? AND registry.registry_id > ?))))"
                        )
                        params.extend([cursor_value, cursor_value, decoded_cursor["registry_id"]])

            sql = f"""
                SELECT
                    registry.registry_id, registry.charity_number, registry.registered_name,
                    registry.registration_status, registry.income, registry.expenditure,
                    registry.city, registry.administrative_region, registry.country_code,
                    registry.source_record_updated_at,
                    {enriched_exists} AS has_enriched_profile,
                    {grant_exists} AS has_grant_data,
                    {self._registry_philea_exists_sql('registry')} AS has_philea_data,
                    {cursor_select}
                FROM {REGISTRY_TABLE} AS registry
                {' '.join(joins)}
                WHERE {' AND '.join(where)}
                ORDER BY {order_by}
                LIMIT ?
            """
            rows = conn.execute(sql, [*params, limit + 1]).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            results = [
                {
                    "registry_id": row[0],
                    "charity_number": row[1],
                    "registered_name": row[2],
                    "registration_status": row[3],
                    "income": row[4],
                    "expenditure": row[5],
                    "city": row[6],
                    "administrative_region": row[7],
                    "country_code": row[8],
                    "source_record_updated_at": row[9],
                    "has_enriched_profile": bool(row[10]),
                    "has_grant_data": bool(row[11]),
                    "has_philea_data": bool(row[12]),
                }
                for row in rows
            ]
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = _encode_registry_cursor(
                    {
                        "sort": sort,
                        "registry_id": last[0],
                        "value": last[13],
                        "is_null": int(last[14]),
                    }
                )
            return {
                "results": results,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "applied_filters": {
                    "query": query or None,
                    "charity_number": charity_number or None,
                    "status": status or None,
                    "income_min": income_min,
                    "income_max": income_max,
                    "expenditure_min": expenditure_min,
                    "expenditure_max": expenditure_max,
                    "country": country or None,
                    "region": region or None,
                    "beneficiary_geography": beneficiary_geography or None,
                    "has_enriched_profile": has_enriched_profile,
                    "has_grant_data": has_grant_data,
                    "sort": sort,
                },
                "page_size": limit,
                "registry_count": None,
                "search_strategy": search_strategy,
            }
        finally:
            conn.close()

    async def get_registry_detail(self, registry_id: str) -> Optional[Dict[str, Any]]:
        """Load one registry record and an accepted enriched link on demand only."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                f"""
                SELECT registry_id, charity_number, linked_charity_number, registered_name,
                       registration_status, registration_date, removal_date, income, expenditure,
                       financial_period_end_date, address_line_one, address_line_two,
                       address_line_three, address_line_four, address_line_five, postcode, city,
                       administrative_region, country_code, activity_text, source_name,
                       source_record_updated_at, imported_at, is_current_source_record
                FROM {REGISTRY_TABLE}
                WHERE registry_id = ?
                """,
                (registry_id,),
            ).fetchone()
            if not row:
                return None
            link = conn.execute(
                f"""
                SELECT link.enriched_organization_id, profile.name, link.match_status,
                       link.match_method, link.match_confidence, link.match_reason,
                       {self._registry_grant_exists_sql('registry')},
                       {self._registry_philea_exists_sql('registry')}
                FROM {REGISTRY_TABLE} AS registry
                JOIN {REGISTRY_LINK_TABLE} AS link
                  ON link.registry_id = registry.registry_id AND link.match_status = 'accepted'
                JOIN charities AS profile ON profile.charity_id = link.enriched_organization_id
                WHERE registry.registry_id = ?
                ORDER BY link.match_confidence DESC, link.enriched_organization_id ASC
                LIMIT 1
                """,
                (registry_id,),
            ).fetchone()
            enriched_profile = None
            has_grant_data = False
            if link:
                has_grant_data = bool(link[6])
                enriched_profile = {
                    "enriched_organization_id": link[0],
                    "organization_name": link[1],
                    "match_status": link[2],
                    "match_method": link[3],
                    "match_confidence": link[4],
                    "match_reason": link[5],
                    "has_grant_data": has_grant_data,
                    "has_philea_data": bool(link[7]),
                }
            address_lines = [value for value in row[10:15] if value]
            return {
                "registry_id": row[0],
                "charity_number": row[1],
                "linked_charity_number": row[2],
                "registered_name": row[3],
                "registration_status": row[4],
                "registration_date": row[5],
                "removal_date": row[6],
                "income": row[7],
                "expenditure": row[8],
                "financial_period_end_date": row[9],
                "address_lines": address_lines,
                "postcode": row[15],
                "city": row[16],
                "administrative_region": row[17],
                "country_code": row[18],
                "activity_text": row[19],
                "source_name": row[20],
                "source_record_updated_at": row[21],
                "imported_at": row[22],
                "is_current_source_record": bool(row[23]),
                "observed_grant_data_message": (
                    "Observed 360Giving grant data is linked to the accepted enriched profile."
                    if has_grant_data
                    else "Registry entry available. No observed grant data is currently linked to this organization."
                ),
                "enriched_profile": enriched_profile,
            }
        finally:
            conn.close()

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
        sources: Optional[List[str]] = None,
        min_annual_giving: Optional[float] = None,
        max_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        max_avg_grant_size: Optional[float] = None,
        include_score: bool = False,
        sort: str = "name_asc",
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

        if sources is not None:
            selected_sources = [str(source).strip() for source in sources if str(source).strip()]
            if not selected_sources:
                query += " AND 0"
            else:
                source_conditions = []
                for source in selected_sources:
                    source_conditions.append("""(
                        primary_source = ? COLLATE NOCASE
                        OR EXISTS (
                            SELECT 1 FROM json_each(COALESCE(charities.source_names, '[]'))
                            WHERE value = ? COLLATE NOCASE
                        )
                        OR EXISTS (
                            SELECT 1 FROM grants AS source_grant
                            WHERE source_grant.source = ? COLLATE NOCASE
                              AND (
                                source_grant.funding_charity_id = charities.charity_id
                                OR source_grant.recipient_charity_id = charities.charity_id
                              )
                        )
                    )""")
                    params.extend([source, source, source])
                query += " AND (" + " OR ".join(source_conditions) + ")"
            
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
        if max_annual_giving is not None:
            query += " AND annual_expenditure <= ?"
            params.append(max_annual_giving)
        if min_annual_giving is None and max_annual_giving is None and size == "small":
            query += " AND annual_expenditure < 1000000"
        elif min_annual_giving is None and max_annual_giving is None and size == "medium":
            query += " AND annual_expenditure >= 1000000 AND annual_expenditure <= 10000000"
        elif min_annual_giving is None and max_annual_giving is None and size == "large":
            query += " AND annual_expenditure > 10000000"

        if (min_avg_grant_size is not None and min_avg_grant_size > 0) or max_avg_grant_size is not None:
            average_grant_conditions = ["1 = 1"]
            if min_avg_grant_size is not None and min_avg_grant_size > 0:
                average_grant_conditions.append("AVG(amount_eur) >= ?")
                params.append(min_avg_grant_size)
            if max_avg_grant_size is not None:
                average_grant_conditions.append("AVG(amount_eur) <= ?")
                params.append(max_avg_grant_size)
            query += f""" AND charity_id IN (
                SELECT funding_charity_id
                FROM grants
                WHERE funding_charity_id IS NOT NULL
                  AND amount_eur IS NOT NULL
                  AND conversion_status IN (
                    'native_eur', 'ecb_award_date', 'ecb_previous_business_day'
                  )
                GROUP BY funding_charity_id
                HAVING {' AND '.join(average_grant_conditions)}
            )"""

        # Score ordering is computed from every matching profile before the
        # page is sliced. That prevents a "top 50 of the first 50" result.
        # The other orderings stay inside SQLite and remain inexpensive.
        if sort == "score_desc":
            pass
        elif sort == "income_desc":
            query += " ORDER BY annual_income IS NULL, annual_income DESC, name COLLATE NOCASE ASC LIMIT ? OFFSET ?"
            params.extend([limit, skip])
        else:
            query += " ORDER BY name COLLATE NOCASE ASC LIMIT ? OFFSET ?"
            params.extend([limit, skip])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        score_config = load_score_configuration() if (include_score or sort == "score_desc") and rows else None
        score_statistics: Dict[int, Dict[str, Any]] = {}
        if score_config:
            profile = score_config.example_target_profile
            charity_ids = [int(row[0]) for row in rows]
            requested_currency = str(profile.get("currency") or "").upper()
            grouped_statistics: Dict[int, List[Tuple[str, float, int]]] = {}
            # SQLite has a bounded number of SQL parameters. Batching keeps
            # full-population score sorting valid as the directory grows.
            for start in range(0, len(charity_ids), 900):
                id_batch = charity_ids[start : start + 900]
                placeholders = ",".join("?" for _ in id_batch)
                statistics_query = f"""
                    SELECT funding_charity_id, UPPER(currency), AVG(amount), COUNT(*)
                    FROM grants
                    WHERE funding_charity_id IN ({placeholders})
                      AND amount > 0 AND currency IS NOT NULL
                """
                statistics_params: List[Any] = list(id_batch)
                if requested_currency:
                    statistics_query += " AND UPPER(currency) = ?"
                    statistics_params.append(requested_currency)
                statistics_query += " GROUP BY funding_charity_id, UPPER(currency)"
                cursor.execute(statistics_query, statistics_params)
                for charity_id, currency, average_amount, grant_count in cursor.fetchall():
                    grouped_statistics.setdefault(int(charity_id), []).append((currency, average_amount, grant_count))
            for charity_id, values in grouped_statistics.items():
                if requested_currency or len(values) == 1:
                    currency, average_amount, grant_count = values[0]
                    score_statistics[charity_id] = {
                        "currency": currency,
                        "average_amount": average_amount,
                        "grant_count": grant_count,
                    }
        conn.close()
        
        results = []
        for r in rows:
            raw_cc = json.loads(r[15]) if r[15] else {}
            all_details = raw_cc.get("all_details") or {}
            
            result = {
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
            }
            if score_config:
                result.update(_score_summary(score_relevance(
                    {**result, "annual_expenditure": r[12]},
                    score_config.example_target_profile,
                    grant_statistics=score_statistics.get(int(r[0]), {}),
                    configuration=score_config,
                )))
            results.append(result)
        if sort == "score_desc":
            results.sort(key=lambda item: (
                item.get("relevance_score") is None,
                -(float(item["relevance_score"]) if item.get("relevance_score") is not None else 0.0),
                str(item.get("charity_name") or "").casefold(),
            ))
            return results[skip : skip + limit]
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
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transaction_coverage, source_names FROM charities WHERE charity_id = ?",
            (charity_id,),
        )
        organization_row = cursor.fetchone()
        
        query = """
            SELECT grant_id, funding_charity_id, funding_name, funding_org_source_id,
                   recipient_name, recipient_charity_id, recipient_org_source_id,
                   amount, amount_eur, exchange_rate, exchange_rate_date, exchange_rate_source,
                   conversion_status, currency, description, date, recipient_region,
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
                "grant_id": r["grant_id"],
                "funding_charity_id": r["funding_charity_id"],
                "funding_name": r["funding_name"],
                "funding_org_source_id": r["funding_org_source_id"],
                "recipient_name": r["recipient_name"],
                "recipient_charity_id": r["recipient_charity_id"],
                "recipient_org_source_id": r["recipient_org_source_id"],
                "amount": r["amount"],
                "amount_eur": r["amount_eur"],
                "exchange_rate": r["exchange_rate"],
                "exchange_rate_date": r["exchange_rate_date"],
                "exchange_rate_source": r["exchange_rate_source"],
                "conversion_status": r["conversion_status"],
                "currency": r["currency"] or "UNKNOWN",
                "description": r["description"] or "",
                "date": r["date"] or "",
                "recipient_region": r["recipient_region"],
                "beneficiary_geography": _json_list(r["beneficiary_geography"]),
                "tags": _json_list(r["tags"]),
                "source": r["source"],
                "source_record_id": r["source_record_id"],
                "source_url": r["source_url"],
                "programme_area_source": _json_list(r["programme_area_source"]),
                "programme_area_inferred": _json_list(r["programme_area_inferred"]),
                "programme_area_scores": json.loads(r["programme_area_scores"]) if r["programme_area_scores"] else {},
                "programme_area_method": r["programme_area_method"],
                "programme_area_evidence": _json_list(r["programme_area_evidence"]),
                "programme_area_review_required": bool(r["programme_area_review_required"]),
                "beneficiary_geography_normalized": _json_list(r["beneficiary_geography_normalized"]),
                "geographic_focus_inferred": _json_list(r["geographic_focus_inferred"]),
                "geography_method": r["geography_method"],
                "geography_confidence": r["geography_confidence"],
                "geography_evidence": _json_list(r["geography_evidence"]),
                "geography_review_required": bool(r["geography_review_required"]),
                "enrichment_rule_version": r["enrichment_rule_version"],
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
                   amount, amount_eur, conversion_status, currency, source
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
                    "selected_currency": "EUR" if not currency or currency.upper() == "AUTO" else currency.upper(),
                    "conversion_method": "ecb_historic_reference_rate" if not currency or currency.upper() == "AUTO" else "none",
                    "filters_applied": {"organization_id": charity_id, "limit": limit},
                    "truncation_applied": False,
                },
            }

        currencies = sorted({str(row[10]).upper() for row in rows if row[10]})
        transaction_sources = sorted({str(row[11]) for row in rows if row[11]})
        requested_currency = str(currency or "").strip().upper() or None
        auto_converted_eur = requested_currency in {None, "AUTO"}
        selected_currency = "EUR" if auto_converted_eur else requested_currency
        excluded_reasons = {}

        nodes_by_id = {}
        aggregated = {}
        for row in rows:
            (_, donor_id, donor_name, donor_source_id, recipient_id, recipient_name,
             recipient_source_id, source_amount, amount_eur, conversion_status,
             row_currency, row_source) = row
            normalized_currency = str(row_currency or "").upper()
            if not auto_converted_eur and normalized_currency != selected_currency:
                excluded_reasons["currency_filtered"] = excluded_reasons.get("currency_filtered", 0) + 1
                continue
            # Invalid source values stay source-data exclusions even in Auto;
            # they are not misreported as an FX availability problem.
            if source_amount is None:
                excluded_reasons["missing_amount"] = excluded_reasons.get("missing_amount", 0) + 1
                continue
            if source_amount <= 0:
                excluded_reasons["non_positive_amount"] = excluded_reasons.get("non_positive_amount", 0) + 1
                continue
            if auto_converted_eur and str(conversion_status or "") not in {
                "native_eur", "ecb_award_date", "ecb_previous_business_day"
            }:
                excluded_reasons["conversion_unavailable"] = excluded_reasons.get("conversion_unavailable", 0) + 1
                continue
            amount = amount_eur if auto_converted_eur else source_amount
            if amount is None:
                excluded_reasons["conversion_unavailable"] = excluded_reasons.get("conversion_unavailable", 0) + 1
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
            key = (source_id, target_id, selected_currency)
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
                "conversion_method": "ecb_historic_reference_rate" if auto_converted_eur else "none",
                "filters_applied": {
                    "organization_id": charity_id, "limit": limit,
                    "currency": "auto" if auto_converted_eur else selected_currency,
                },
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


_repository_cache: tuple[tuple[str, int, int] | None, CharityRepository] | None = None


def _has_compatible_repository_schema(path: str) -> tuple[bool, str]:
    """Perform a cheap, read-only schema check for the serving process.

    Full ``PRAGMA quick_check`` validation deliberately remains part of staging
    database publication. Running it again when the BFF starts scans the whole
    100k-grant database and delays the first page even though atomic publication
    has already verified that exact file.
    """
    if not path or not os.path.isfile(path):
        return False, "database file does not exist"
    if os.path.getsize(path) == 0:
        return False, "database file is empty"

    derived_tables = {
        "grant_beneficiary_terms",
        "grant_beneficiary_countries",
        "grant_programme_categories",
        "grant_source_funder_facts",
        "grant_overview_facts",
        "grant_overview_cache",
    }
    try:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            expected = {
                table: columns
                for table, columns in REQUIRED_SCHEMA.items()
                if table not in derived_tables
            }
            missing_tables = sorted(set(expected) - tables)
            if missing_tables:
                return False, f"missing required tables: {', '.join(missing_tables)}"
            for table, required_columns in expected.items():
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                missing_columns = sorted(required_columns - columns)
                if missing_columns:
                    return False, f"table '{table}' is missing required columns"
            return True, "compatible"
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return False, f"cannot open as compatible SQLite: {exc}"


def _repository_signature(path: str) -> tuple[str, int, int] | None:
    try:
        details = os.stat(path)
        return (os.path.abspath(path), details.st_mtime_ns, details.st_size)
    except OSError:
        return None


def get_charity_repository() -> CharityRepository:
    """Reuse the active repository until the atomically published DB changes."""
    global _repository_cache
    signature = _repository_signature(DB_PATH)
    if _repository_cache and _repository_cache[0] == signature:
        return _repository_cache[1]
    is_valid, reason = _has_compatible_repository_schema(DB_PATH)
    if is_valid:
        repository: CharityRepository = SQLiteCharityRepository()
    elif os.path.exists(DB_PATH):
        logger.warning(f"Ignoring unusable SQLite database at {DB_PATH}: {reason}")
        repository = JSONCharityRepository()
    else:
        repository = JSONCharityRepository()
    # Additive SQLite migrations can update the file timestamp even when no
    # source data changed. Capture the signature after repository setup so the
    # next request reuses this initialized instance.
    _repository_cache = (_repository_signature(DB_PATH), repository)
    return repository
