"""Backward-compatible entry points for deterministic enrichment.

The active taxonomies and regex configuration live in :mod:`enrichment`.
These mutating wrappers retain the historical ``tags_focus`` and
``geo_locations`` output expected by existing callers.
"""

from __future__ import annotations

import argparse
import json
import logging

from preprocessing.enrichment import (
    GEOGRAPHY_TAXONOMY,
    PROGRAMME_SOURCE_ALIASES,
    PROGRAMME_TAXONOMY,
    classify_geography_fields,
    classify_programme_fields,
)


logger = logging.getLogger(__name__)

# Compatibility exports. They now point to the centralized configuration.
MASTER_TAGS = list(PROGRAMME_TAXONOMY)
TAG_NORMALIZATION = {
    alias: targets if isinstance(targets, str) else targets[0]
    for alias, targets in PROGRAMME_SOURCE_ALIASES.items()
}
KEYWORD_MAPPING = {}
NATIVE_CLASSIFICATIONS_TO_TAGS = PROGRAMME_SOURCE_ALIASES
GEO_TAXONOMY = GEOGRAPHY_TAXONOMY


def _details(member):
    value = member.get("all_details")
    return value if isinstance(value, dict) else {}


def _philea(member):
    value = member.get("philea_info")
    return value if isinstance(value, dict) else {}


def _classification_values(value):
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        for key in ("classification_desc", "name", "title", "value"):
            if value.get(key):
                return [value[key]]
        return []
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_classification_values(item))
        return result
    return [str(value)]


def extract_tags(members):
    """Populate historical tags from source classifications and traceable regexes."""
    for member in members:
        details = _details(member)
        philea = _philea(member)
        sources = []
        sources.extend(_classification_values(member.get("who_what_how")))
        sources.extend(_classification_values(details.get("who_what_where")))
        sources.extend(_classification_values(philea.get("Programme Areas")))
        result = classify_programme_fields(
            {
                "programme_area_text": philea.get("Programme Areas"),
                "about": philea.get("About"),
                "mission": philea.get("Mission"),
                "description": member.get("description"),
                "activities": details.get("activities") or details.get("charitable_objects"),
            },
            sources,
        )
        source_categories = set(result["source_categories"])
        categories = source_categories | set(result["categories"])
        member["tags_focus"] = [
            {"tag": category, "source": "exact_match" if category in source_categories else "regex_fallback"}
            for category in sorted(categories)
        ]
        member["programme_area_evidence"] = result["source_evidence"] + result["evidence"]
        member["programme_area_review_required"] = result["review_required"]


def extract_geo(members):
    """Populate historical macro-region output without using names as evidence."""
    for member in members:
        details = _details(member)
        philea = _philea(member)
        focus = philea.get("Geographic Focus")
        area = philea.get("areaOfOperation")
        fields = {
            "stated_geographic_focus": focus,
            "area_of_operation": area,
            "about": philea.get("About"),
            "description": member.get("description"),
        }
        # Legacy behavior used an address fallback. Keep it only in this wrapper;
        # the canonical enrichment stores headquarters separately and never treats
        # the address as a funding destination.
        if not focus or not str(focus).strip():
            fields["headquarters_fallback"] = member.get("address")
        result = classify_geography_fields(fields)

        ambiguous_targets = {
            item["target_category"]
            for item in result["evidence"]
            if item.get("ambiguous")
        }
        grouped = {}
        for target in result["categories"]:
            if target in ambiguous_targets:
                continue
            taxonomy = GEOGRAPHY_TAXONOMY.get(target)
            if not taxonomy:
                continue
            macro = taxonomy["macro_region"]
            legacy_name = "Global" if target == "Worldwide" else target
            grouped.setdefault(macro, set()).add(legacy_name)
        member["geo_locations"] = {
            macro: sorted(values) for macro, values in sorted(grouped.items())
        }
        member["geography_evidence"] = result["source_evidence"] + result["evidence"]
        member["geography_review_required"] = result["review_required"]


def main():
    parser = argparse.ArgumentParser(description="Enrich organization JSON with deterministic taxonomy rules")
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    with open(args.input, "r", encoding="utf-8") as source:
        members = json.load(source)
    extract_tags(members)
    extract_geo(members)
    with open(args.output, "w", encoding="utf-8") as target:
        json.dump(members, target, ensure_ascii=False, indent=2)
    logger.info("Enriched %s records into %s", len(members), args.output)


if __name__ == "__main__":
    main()
