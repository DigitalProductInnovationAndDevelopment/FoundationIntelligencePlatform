"""Create a traceable Europe-first, tech-enablement 360Giving grant subset.

This is deliberately a *selection* step, not a new data source or a replacement
for the application database.  It takes a JSON export of 360Giving grant records,
keeps only grants with an explicit beneficiary country in EU/EEA or Switzerland,
and writes both the selected records and a coverage report.  The report makes a
shortfall against the target visible rather than padding the result with inferred
locations or weak thematic matches.

The script accepts the API-shaped cache used by this repository (a list of
publisher objects with ``grants_made`` / ``grants_received`` arrays) as well as a
flat JSON list of 360Giving grant objects.  It is therefore suitable for a future
filtered GrantNav/Datastore export without changing the selection rules.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import pycountry

from preprocessing.enrichment import classify_programme_fields


# EU-27 plus the three non-EU EEA states. Switzerland is intentionally added
# separately because it is not an EEA member. The UK is intentionally absent.
EU_MEMBER_COUNTRY_CODES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
})
EEA_EFTA_COUNTRY_CODES = frozenset({"IS", "LI", "NO"})
ELIGIBLE_COUNTRY_CODES = EU_MEMBER_COUNTRY_CODES | EEA_EFTA_COUNTRY_CODES | {"CH"}
DACH_COUNTRY_CODES = frozenset({"DE", "AT", "CH"})

COUNTRY_ALIASES = {
    "czech republic": "CZ",
    "czechia": "CZ",
    "greece": "GR",
    "österreich": "AT",
    "republic of ireland": "IE",
    "schweiz": "CH",
    "slovak republic": "SK",
    "suisse": "CH",
    "swiss confederation": "CH",
}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _grant_data(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("data")
    return value if isinstance(value, Mapping) else record


def iter_grants(payload: Any) -> Iterator[dict[str, Any]]:
    """Yield grant records from supported cache/export shapes exactly once."""
    entries: Iterable[Any]
    if isinstance(payload, Mapping):
        entries = payload.get("grants") or payload.get("results") or payload.get("records") or []
    elif isinstance(payload, list):
        entries = payload
    else:
        return

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        nested = False
        for key in ("grants_made", "grants_received"):
            values = entry.get(key)
            if isinstance(values, list):
                nested = True
                for grant in values:
                    if isinstance(grant, Mapping):
                        yield dict(grant)
        # A publisher-pilot row with an error is a transport-status record, not a
        # grant. Do not let it inflate the missing-grant-ID coverage counter.
        if not nested and "org_id" in entry and "summary" in entry:
            continue
        if not nested:
            yield dict(entry)


def resolve_country_code(value: Any) -> str | None:
    """Return a real ISO alpha-2 country code from an explicit source value."""
    text = str(value or "").strip()
    if not text:
        return None
    upper = text.upper().replace(".", "")
    if len(upper) == 2 and pycountry.countries.get(alpha_2=upper):
        return upper
    if len(upper) == 3:
        country = pycountry.countries.get(alpha_3=upper)
        if country:
            return country.alpha_2
    alias = COUNTRY_ALIASES.get(text.casefold())
    if alias:
        return alias
    try:
        return pycountry.countries.lookup(text).alpha_2
    except LookupError:
        return None


def beneficiary_country_codes(record: Mapping[str, Any]) -> list[str]:
    """Use only explicit beneficiary locations; never fall back to HQ or recipient."""
    codes: set[str] = set()
    data = _grant_data(record)
    for location in _as_list(data.get("beneficiaryLocation")):
        values: list[Any]
        if isinstance(location, Mapping):
            values = [location.get("countryCode"), location.get("geoCode"), location.get("name")]
        else:
            values = [location]
        for value in values:
            code = resolve_country_code(value)
            if code:
                codes.add(code)
                break
    return sorted(codes)


def grant_id(record: Mapping[str, Any]) -> str | None:
    data = _grant_data(record)
    value = record.get("grant_id") or data.get("id") or data.get("grant_id")
    value = str(value or "").strip()
    return value or None


def award_date(record: Mapping[str, Any]) -> str:
    data = _grant_data(record)
    return str(data.get("awardDate") or data.get("date") or "")


def programme_sources(record: Mapping[str, Any]) -> list[Any]:
    data = _grant_data(record)
    result: list[Any] = []
    for item in _as_list(data.get("grantProgramme")):
        if isinstance(item, Mapping):
            result.append(item.get("title") or item.get("name"))
        else:
            result.append(item)
    return [item for item in result if item]


def tech_classification(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the existing deterministic programme classification for a grant."""
    data = _grant_data(record)
    text = " ".join(
        str(value) for value in (data.get("title"), data.get("description")) if value
    )
    result = classify_programme_fields({"description": text}, programme_sources(record))
    score = result["scores"].get("tech-enablement", 0.0)
    if "tech-enablement" in result["source_categories"]:
        score = 1.0
    return {
        "is_tech_enablement": score >= 0.8,
        "score": score,
        "method": result["method"],
        "evidence": [
            item for item in result["source_evidence"] + result["evidence"]
            if item.get("target_category") == "tech-enablement" and item.get("accepted")
        ],
    }


def candidate_from_grant(record: Mapping[str, Any]) -> dict[str, Any] | None:
    identifier = grant_id(record)
    if not identifier:
        return None
    country_codes = beneficiary_country_codes(record)
    eligible_codes = [code for code in country_codes if code in ELIGIBLE_COUNTRY_CODES]
    tech = tech_classification(record)
    return {
        "grant_id": identifier,
        "record": record,
        "beneficiary_country_codes": country_codes,
        "eligible_country_codes": eligible_codes,
        "has_dach_beneficiary": any(code in DACH_COUNTRY_CODES for code in eligible_codes),
        "award_date": award_date(record),
        "tech": tech,
    }


def select_candidates(
    candidates: Iterable[Mapping[str, Any]], target: int, dach_share: float
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select high-confidence tech grants with a best-effort DACH share."""
    unique: dict[str, dict[str, Any]] = {}
    counters: Counter[str] = Counter()
    for candidate in candidates:
        identifier = str(candidate["grant_id"])
        if identifier in unique:
            counters["duplicates_removed"] += 1
            continue
        unique[identifier] = dict(candidate)

    eligible = [
        candidate for candidate in unique.values()
        if candidate["eligible_country_codes"] and candidate["tech"]["is_tech_enablement"]
    ]
    dach = [candidate for candidate in eligible if candidate["has_dach_beneficiary"]]
    non_dach = [candidate for candidate in eligible if not candidate["has_dach_beneficiary"]]

    # Preserve a stable, explainable preference order: strongest rule match,
    # newest award date, then source grant ID. No location is inferred to fill a
    # DACH target.
    for collection in (dach, non_dach):
        collection.sort(key=lambda item: item["grant_id"])
        collection.sort(key=lambda item: item["award_date"], reverse=True)
        collection.sort(key=lambda item: item["tech"]["score"], reverse=True)

    requested_dach = math.ceil(target * dach_share)
    selected = dach[:requested_dach]
    remaining = target - len(selected)
    for candidate in non_dach + dach[requested_dach:]:
        if remaining <= 0:
            break
        selected.append(candidate)
        remaining -= 1

    counters.update({
        "unique_grants_seen": len(unique),
        "eligible_high_confidence_tech_grants": len(eligible),
        "eligible_dach_grants": len(dach),
        "eligible_non_dach_grants": len(non_dach),
        "requested_dach_grants": requested_dach,
        "selected_dach_grants": sum(1 for item in selected if item["has_dach_beneficiary"]),
        "selected_grants": len(selected),
    })
    return selected, dict(counters)


def curated_record(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "grant_id": candidate["grant_id"],
        "selection": {
            "dataset_profile": "eu-eea-switzerland-tech-enablement-dach-prioritized-v1",
            "beneficiary_country_codes": candidate["beneficiary_country_codes"],
            "eligible_country_codes": candidate["eligible_country_codes"],
            "dach_priority": candidate["has_dach_beneficiary"],
            "tech_enablement_score": candidate["tech"]["score"],
            "tech_enablement_method": candidate["tech"]["method"],
            "tech_enablement_evidence": candidate["tech"]["evidence"],
        },
        "raw_360giving_grant": candidate["record"],
    }


def curate_file(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    target: int = 10_000,
    dach_share: float = 0.60,
) -> dict[str, Any]:
    if target < 1:
        raise ValueError("target must be at least 1")
    if not 0 <= dach_share <= 1:
        raise ValueError("dach_share must be between 0 and 1")

    with input_path.open("r", encoding="utf-8") as source:
        payload = json.load(source)

    observed: list[dict[str, Any]] = []
    screening = Counter()
    for record in iter_grants(payload):
        screening["grant_records_read"] += 1
        candidate = candidate_from_grant(record)
        if not candidate:
            screening["missing_grant_id"] += 1
            continue
        if not candidate["beneficiary_country_codes"]:
            screening["no_explicit_beneficiary_country"] += 1
        elif not candidate["eligible_country_codes"]:
            screening["beneficiary_outside_eligible_region"] += 1
        else:
            screening["explicit_eligible_beneficiary_country"] += 1
        if not candidate["tech"]["is_tech_enablement"]:
            screening["not_high_confidence_tech_enablement"] += 1
        else:
            screening["high_confidence_tech_enablement"] += 1
        observed.append(candidate)

    selected, selection_counts = select_candidates(observed, target, dach_share)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for candidate in selected:
            output.write(json.dumps(curated_record(candidate), ensure_ascii=False) + "\n")

    country_counts = Counter(
        code for candidate in selected for code in candidate["eligible_country_codes"]
    )
    report = {
        "dataset_profile": "eu-eea-switzerland-tech-enablement-dach-prioritized-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_path": str(input_path),
        "selection_policy": {
            "target_grants": target,
            "dach_target_share": dach_share,
            "eligible_beneficiary_country_codes": sorted(ELIGIBLE_COUNTRY_CODES),
            "dach_country_codes": sorted(DACH_COUNTRY_CODES),
            "location_rule": "explicit 360Giving beneficiaryLocation country only",
            "technology_rule": "existing deterministic tech-enablement taxonomy at confidence >= 0.8",
            "uk_included": False,
        },
        "screening_counts": dict(screening),
        "selection_counts": selection_counts,
        "selected_beneficiary_country_associations": dict(sorted(country_counts.items())),
        "target_met": len(selected) >= target,
        "shortfall": max(target - len(selected), 0),
        "output_path": str(output_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a Europe-first, DACH-prioritised tech-enablement grant subset."
    )
    parser.add_argument("--input", required=True, type=Path, help="360Giving JSON cache or flat JSON export")
    parser.add_argument(
        "--output", type=Path,
        default=Path("src/data/processed/eu_tech_dach_grants.jsonl"),
        help="JSON Lines output retaining the raw grant and selection evidence",
    )
    parser.add_argument(
        "--report", type=Path,
        default=Path("src/data/processed/eu_tech_dach_report.json"),
        help="coverage and selection report output",
    )
    parser.add_argument("--target", type=int, default=10_000, help="maximum selected grant count")
    parser.add_argument(
        "--dach-share", type=float, default=0.60,
        help="best-effort share reserved for DE/AT/CH grants (default: 0.60)",
    )
    args = parser.parse_args()
    report = curate_file(args.input, args.output, args.report, args.target, args.dach_share)
    print(json.dumps({
        "selected_grants": report["selection_counts"]["selected_grants"],
        "target": report["selection_policy"]["target_grants"],
        "target_met": report["target_met"],
        "shortfall": report["shortfall"],
        "report": report["output_path"].replace(".jsonl", ".json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
