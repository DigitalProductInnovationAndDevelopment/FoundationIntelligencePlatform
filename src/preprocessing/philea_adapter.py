"""Normalize and conservatively integrate cached Philea organization records."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from preprocessing.enrichment import enrich_organization


PHILEA_SOURCE = "Philea"
CHARITY_COMMISSION_SOURCE = "Charity Commission for England and Wales"
FUZZY_AUTO_MERGE_THRESHOLD = 0.92
FUZZY_REVIEW_THRESHOLD = 0.82
GENERIC_NAMES = {"foundation", "trust", "association", "network", "fund", "charity"}


def _utc_now():
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_organization_name(value: Any) -> str:
    """Normalize an organization name for comparison and deduplication."""
    text = str(value or "").strip()
    if not text or text.isdigit():
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    text = re.sub(r"\b(the|limited|ltd|incorporated|inc|gmbh|ev|e\.v)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_domain(value: Any) -> str:
    """Normalize a website domain for cross-source comparison."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def stable_philea_organization_id(source_record_id: Any) -> int:
    """Derive the deterministic negative local ID for a Philea record."""
    try:
        numeric = int(str(source_record_id))
        if numeric > 0:
            return -numeric
    except (TypeError, ValueError):
        pass
    digest = hashlib.sha256(f"Philea|{source_record_id}".encode("utf-8")).hexdigest()
    return -(1_000_000_000 + int(digest[:10], 16) % 1_000_000_000)


def map_philea_organization_type(raw_type: Any) -> str:
    """Map a Philea type onto the platform's organization types."""
    value = raw_type.get("value") if isinstance(raw_type, Mapping) else raw_type
    normalized = str(value or "").strip().casefold()
    return {
        "foundation": "foundation",
        "affiliate": "philanthropy infrastructure organization",
        "member": "membership organization",
    }.get(normalized, "unknown")


def normalize_philea_record(member: Mapping[str, Any], ingestion_timestamp: str | None = None):
    """Normalize one cached Philea record, retaining its provenance."""
    source_id = member.get("id")
    name = str(member.get("name") or "").strip()
    normalized_name = normalize_organization_name(name)
    if not normalized_name:
        raise ValueError("Philea record has no usable non-numeric organization name")
    local_id = stable_philea_organization_id(source_id)
    position = member.get("position") if isinstance(member.get("position"), Mapping) else {}
    organization_type = map_philea_organization_type(member.get("type"))
    source_url = str(member.get("link") or "").strip() or None
    timestamp = ingestion_timestamp or _utc_now()
    country = str(position.get("country") or member.get("country") or "").strip()
    raw_api_record = {
        "registered_charity_number": local_id,
        "suffix": 0,
        "link": source_url,
        "all_details": {
            "organisation_number": local_id,
            "reg_charity_number": local_id,
            "group_subsid_suffix": 0,
            "charity_name": name,
            "charity_type": organization_type,
            "reg_status": "DIRECTORY",
            "reporting_status": "Philea directory record",
            "address_line_one": position.get("address") or member.get("address"),
            "address_line_three": position.get("state"),
            "address_line_four": position.get("city"),
            "address_line_five": country,
            "address_post_code": position.get("post_code"),
            "email": member.get("email") or None,
            "web": member.get("website") or None,
        },
        "assets_liabilities": [],
        "primary_grants": None,
        "who_what_how": [],
        "financial_history": [],
    }
    flat = {
        "charity_id": local_id,
        "name": name,
        "normalized_name": normalized_name,
        "type": organization_type,
        "organization_type": organization_type,
        "website": member.get("website") or "",
        "normalized_domain": normalize_domain(member.get("website")),
        "email": member.get("email") or "",
        "address": position.get("address") or member.get("address") or "",
        "city": position.get("city") or "",
        "state": position.get("state") or "",
        "country": country,
        "latitude": position.get("lat"),
        "longitude": position.get("lng"),
        "annual_income": None,
        "annual_expenditure": None,
        "thematic_focus": "[]",
        "geographic_focus": "{}",
        "raw_cc_data": raw_api_record,
        "primary_source": PHILEA_SOURCE,
        "source_names": [PHILEA_SOURCE],
        "source_record_id": str(source_id) if source_id is not None else None,
        "source_url": source_url,
        "source_records": [{
            "source": PHILEA_SOURCE,
            "source_record_id": str(source_id) if source_id is not None else None,
            "source_url": source_url,
            "ingestion_timestamp": timestamp,
            "source_organization_type": member.get("type"),
            "raw_record": dict(member),
        }],
        "ingestion_timestamp": timestamp,
        "transaction_coverage": "organization_level_only",
        "deduplication_status": "source_only",
        "deduplication_candidates": [],
    }
    flat.update(enrich_organization({**flat, "raw_cc_data": member}))
    return flat


def _source_record_for_existing(item: Mapping[str, Any], timestamp: str):
    """Build the source-record entry appended to an existing organization."""
    raw = item.get("raw_cc_data") if isinstance(item.get("raw_cc_data"), Mapping) else {}
    source_id = item.get("charity_id")
    source_url = raw.get("link") if isinstance(raw, Mapping) else None
    return {
        "source": CHARITY_COMMISSION_SOURCE,
        "source_record_id": str(source_id) if source_id is not None else None,
        "source_url": source_url,
        "ingestion_timestamp": timestamp,
        "raw_record": raw,
    }


def prepare_existing_organization(item: Mapping[str, Any], timestamp: str | None = None):
    """Prepare an existing organization to receive a Philea source record."""
    timestamp = timestamp or _utc_now()
    result = dict(item)
    result.setdefault("normalized_name", normalize_organization_name(item.get("name")))
    result.setdefault("normalized_domain", normalize_domain(item.get("website")))
    result.setdefault("organization_type", item.get("type") or "charity")
    result.setdefault("primary_source", CHARITY_COMMISSION_SOURCE)
    result.setdefault("source_names", [CHARITY_COMMISSION_SOURCE])
    result.setdefault("source_record_id", str(item.get("charity_id")))
    result.setdefault("source_url", (
        item.get("raw_cc_data", {}).get("link")
        if isinstance(item.get("raw_cc_data"), Mapping) else None
    ))
    result.setdefault("source_records", [_source_record_for_existing(item, timestamp)])
    result.setdefault("ingestion_timestamp", timestamp)
    result.setdefault("transaction_coverage", "unknown")
    result.setdefault("deduplication_status", "single_source")
    result.setdefault("deduplication_candidates", [])
    return result


def _merge_into_existing(existing: dict[str, Any], philea: Mapping[str, Any], method: str):
    """Append Philea provenance to an existing organization without overwriting facts."""
    for field in ("website", "email", "address", "city", "state", "country", "latitude", "longitude"):
        if not existing.get(field) and philea.get(field):
            existing[field] = philea[field]
    existing["source_names"] = sorted(set(existing.get("source_names", [])) | {PHILEA_SOURCE})
    existing["source_records"] = list(existing.get("source_records", [])) + list(philea["source_records"])
    for field in (
        "programme_areas_source", "programme_areas_inferred", "geographic_focus_inferred",
        "enrichment_review_reasons",
    ):
        left = existing.get(field, [])
        if isinstance(left, str):
            try:
                left = json.loads(left)
            except json.JSONDecodeError:
                left = []
        existing[field] = sorted(set(left or []) | set(philea.get(field, []) or []))
    for field in ("programme_area_evidence", "geography_evidence", "geographic_focus_source"):
        left = existing.get(field, [])
        if isinstance(left, str):
            try:
                left = json.loads(left)
            except json.JSONDecodeError:
                left = []
        combined = list(left or []) + list(philea.get(field, []) or [])
        unique = {json.dumps(item, sort_keys=True, ensure_ascii=False): item for item in combined}
        existing[field] = list(unique.values())
    scores = existing.get("programme_area_scores", {})
    if isinstance(scores, str):
        try:
            scores = json.loads(scores)
        except json.JSONDecodeError:
            scores = {}
    for category, value in (philea.get("programme_area_scores") or {}).items():
        scores[category] = max(float(scores.get(category, 0)), float(value))
    existing["programme_area_scores"] = scores
    existing["programme_area_review_required"] = bool(
        existing.get("programme_area_review_required")
        or philea.get("programme_area_review_required")
    )
    existing["geography_review_required"] = bool(
        existing.get("geography_review_required") or philea.get("geography_review_required")
    )
    existing["deduplication_status"] = f"merged:{method}"
    # A match does not imply transaction coverage for the Philea source; existing
    # observed transactions, if any, are determined from the grants table.
    return existing


def integrate_philea_organizations(
    existing_organizations: Sequence[Mapping[str, Any]],
    raw_members: Sequence[Mapping[str, Any]],
    ingestion_timestamp: str | None = None,
):
    """Return organizations plus deterministic integration statistics.

    Fuzzy matches are auto-merged only at >= 0.92 *and* with matching non-empty
    headquarters countries. Scores >= 0.82 without sufficient support are stored
    as review candidates and remain separate.
    """
    timestamp = ingestion_timestamp or _utc_now()
    organizations = [prepare_existing_organization(item, timestamp) for item in existing_organizations]
    stats = {
        "philea_input_count": len(raw_members),
        "philea_added_count": 0,
        "philea_merged_count": 0,
        "philea_rejected_count": 0,
        "ambiguous_candidate_count": 0,
        "match_methods": {},
        "fuzzy_auto_merge_threshold": FUZZY_AUTO_MERGE_THRESHOLD,
        "fuzzy_review_threshold": FUZZY_REVIEW_THRESHOLD,
    }

    for raw_member in raw_members:
        try:
            philea = normalize_philea_record(raw_member, timestamp)
        except ValueError:
            stats["philea_rejected_count"] += 1
            continue

        match_index = None
        match_method = None
        philea_domain = philea["normalized_domain"]
        philea_name = philea["normalized_name"]

        def is_cross_source_candidate(existing):
            return PHILEA_SOURCE not in set(existing.get("source_names", []))

        # A Philea source ID is not comparable to a Charity Commission ID. Only an
        # explicitly supplied UK charity number would be a legitimate cross-source ID.
        philea_info = raw_member.get("philea_info") if isinstance(raw_member.get("philea_info"), Mapping) else {}
        cross_source_id = philea_info.get("charity_number") or philea_info.get("charityNumber")
        if cross_source_id and str(cross_source_id).isdigit():
            for index, existing in enumerate(organizations):
                if is_cross_source_candidate(existing) and int(cross_source_id) == existing.get("charity_id"):
                    match_index, match_method = index, "stable_cross_source_id"
                    break

        if match_index is None and philea_domain:
            matches = [
                index for index, existing in enumerate(organizations)
                if is_cross_source_candidate(existing)
                and existing.get("normalized_domain") == philea_domain
            ]
            if len(matches) == 1:
                match_index, match_method = matches[0], "exact_domain"

        if match_index is None and philea_name:
            matches = [
                index for index, existing in enumerate(organizations)
                if is_cross_source_candidate(existing)
                and existing.get("normalized_name") == philea_name
            ]
            tokens = philea_name.split()
            if len(matches) == 1 and (len(tokens) > 1 or philea_name not in GENERIC_NAMES):
                match_index, match_method = matches[0], "exact_normalized_name"

        review_candidates = []
        if match_index is None and philea_name:
            for index, existing in enumerate(organizations):
                if not is_cross_source_candidate(existing):
                    continue
                existing_name = existing.get("normalized_name") or ""
                if not existing_name:
                    continue
                similarity = SequenceMatcher(None, philea_name, existing_name).ratio()
                if similarity < FUZZY_REVIEW_THRESHOLD:
                    continue
                same_country = bool(
                    philea.get("country") and existing.get("country")
                    and str(philea["country"]).casefold() == str(existing["country"]).casefold()
                )
                candidate = {
                    "organization_id": existing.get("charity_id"),
                    "organization_name": existing.get("name"),
                    "name_similarity": round(similarity, 4),
                    "same_country": same_country,
                }
                if similarity >= FUZZY_AUTO_MERGE_THRESHOLD and same_country and match_index is None:
                    match_index, match_method = index, "conservative_fuzzy_name_and_country"
                    break
                review_candidates.append(candidate)

        if match_index is not None:
            organizations[match_index] = _merge_into_existing(
                organizations[match_index], philea, match_method
            )
            stats["philea_merged_count"] += 1
            stats["match_methods"][match_method] = stats["match_methods"].get(match_method, 0) + 1
        else:
            if review_candidates:
                philea["deduplication_status"] = "review_required"
                philea["deduplication_candidates"] = review_candidates
                stats["ambiguous_candidate_count"] += 1
            organizations.append(philea)
            stats["philea_added_count"] += 1

    return organizations, stats
