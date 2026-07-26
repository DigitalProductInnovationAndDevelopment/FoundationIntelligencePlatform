"""Extend a 360Giving publisher pilot with overflow pages, safely and resumably.

The initial publisher collector retains the first 1,000 grants per publisher so
that discovery remains bounded.  This utility retrieves one or more subsequent
pages for a deterministic spread of publishers already marked as truncated.
It leaves the active SQLite database untouched; use the append-only importer
afterwards to publish a selected set of observed grant IDs.
"""

from __future__ import annotations

import argparse
import json
import random
import requests
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pipelines.curate_europe_tech_grants import grant_id
from pipelines.sample_360giving_publishers import API_BASE_URL, RateLimitedClient


def _read_pilot(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(f"{path} is not a 360Giving publisher-pilot payload")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    temporary.replace(path)


def _record_grant_ids(record: Mapping[str, Any]) -> set[str]:
    return {
        identifier
        for item in record.get("grants_made") or []
        if isinstance(item, Mapping)
        for identifier in [grant_id(item)]
        if identifier
    }


def extend_overflow_pages(
    input_path: Path,
    report_path: Path,
    *,
    target_additional_grants: int,
    seed: int = 20260726,
    max_pages_per_publisher: int = 1,
    checkpoint_every: int = 10,
    timeout: float = 30.0,
    client: RateLimitedClient | None = None,
) -> dict[str, Any]:
    """Retrieve a bounded, diverse set of overflow grant pages.

    Each eligible publisher begins at the first offset not yet stored in its
    pilot record.  A seeded shuffle spreads the collection across publishers.
    Grant IDs are deduplicated globally before checkpointing.
    """
    if target_additional_grants < 1:
        raise ValueError("target_additional_grants must be at least 1")
    if max_pages_per_publisher < 1:
        raise ValueError("max_pages_per_publisher must be at least 1")
    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be at least 1")

    payload = _read_pilot(input_path)
    records = [record for record in payload["records"] if isinstance(record, dict)]
    payload["records"] = records
    known_ids = set().union(*(_record_grant_ids(record) for record in records)) if records else set()
    candidates = [
        record for record in records
        if record.get("grants_made_truncated") and record.get("org_id")
        and len(record.get("grants_made") or []) < int(record.get("grants_made_count_reported") or 0)
    ]
    random.Random(seed).shuffle(candidates)
    client = client or RateLimitedClient(timeout=timeout)

    counters: Counter[str] = Counter()
    pages_since_checkpoint = 0
    for record in candidates:
        if counters["unique_grants_added"] >= target_additional_grants:
            break
        org_id = str(record["org_id"])
        offset = len(record.get("grants_made") or [])
        for _ in range(max_pages_per_publisher):
            try:
                status, response = client.get_json(
                    f"{API_BASE_URL}/org/{org_id}/grants_made/",
                    params={"limit": 1000, "offset": offset},
                )
            except requests.RequestException:
                # The collector is intentionally resumable. A transient public
                # API timeout must preserve the current checkpoint and let the
                # rest of the bounded publisher sample continue; this record
                # remains marked as truncated and will be retried on a later
                # run from the same offset.
                counters["request_errors"] += 1
                _write_json(input_path, payload)
                break
            if status == 404:
                counters["publisher_not_found"] += 1
                record["grants_made_truncated"] = False
                break
            page = [item for item in (response or {}).get("results", []) if isinstance(item, Mapping)]
            counters["pages_fetched"] += 1
            counters["raw_grants_retrieved"] += len(page)
            existing_for_record = _record_grant_ids(record)
            new_items = []
            for item in page:
                identifier = grant_id(item)
                if identifier and (identifier in known_ids or identifier in existing_for_record):
                    counters["duplicate_grant_ids"] += 1
                    continue
                new_items.append(item)
                if identifier:
                    known_ids.add(identifier)
                    existing_for_record.add(identifier)
            record.setdefault("grants_made", []).extend(new_items)
            counters["unique_grants_added"] += len(new_items)
            offset += len(page)
            reported_count = int((response or {}).get("count") or 0)
            if reported_count:
                record["grants_made_count_reported"] = max(
                    int(record.get("grants_made_count_reported") or 0), reported_count
                )
            record["grants_made_truncated"] = bool((response or {}).get("next"))
            pages_since_checkpoint += 1
            if pages_since_checkpoint >= checkpoint_every:
                _write_json(input_path, payload)
                pages_since_checkpoint = 0
            if not page or not record["grants_made_truncated"] or counters["unique_grants_added"] >= target_additional_grants:
                break

    _write_json(input_path, payload)
    report = {
        "dataset_profile": "360giving-publisher-overflow-extension-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_path": str(input_path),
        "seed": seed,
        "target_additional_grants": target_additional_grants,
        "max_pages_per_publisher": max_pages_per_publisher,
        "eligible_publishers": len(candidates),
        **dict(counters),
    }
    _write_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extend a 360Giving publisher pilot with overflow grant pages.")
    parser.add_argument("--input", type=Path, default=Path("src/data/processed/360giving_registry_publisher_pilot.json"))
    parser.add_argument("--report", type=Path, default=Path("src/data/processed/360giving_registry_publisher_overflow_report.json"))
    parser.add_argument("--target-additional-grants", type=int, required=True)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--max-pages-per-publisher", type=int, default=1)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    print(json.dumps(extend_overflow_pages(
        args.input, args.report,
        target_additional_grants=args.target_additional_grants,
        seed=args.seed,
        max_pages_per_publisher=args.max_pages_per_publisher,
        checkpoint_every=args.checkpoint_every,
        timeout=args.timeout,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
