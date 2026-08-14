"""Collect a small, resumable random sample of 360Giving publisher grant feeds.

This is a bounded discovery pilot for the Europe/DACH grant profile. It does not
alter the active database. The 360Giving organisation endpoint includes both
funders and recipients, so a 404/empty grants-made response is recorded as an
observed non-funder rather than treated as an error. Requests are serialised and
spaced at least 0.55 seconds apart, below the documented two-requests-per-second
limit.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests


API_BASE_URL = "https://api.threesixtygiving.org/api/v1"
REGISTRY_FEED_URL = "https://registry.threesixtygiving.org/data.json"
DEFAULT_MIN_INTERVAL_SECONDS = 0.55


def select_organisation_sample(
    organisations: Iterable[Mapping[str, Any]], size: int, seed: int
) -> list[dict[str, Any]]:
    """Return a deterministic random subset with only usable organization IDs."""
    by_id: dict[str, dict[str, Any]] = {}
    for organisation in organisations:
        org_id = str(organisation.get("org_id") or "").strip()
        if org_id:
            by_id[org_id] = dict(organisation)
    population = list(by_id.values())
    if size >= len(population):
        return sorted(population, key=lambda value: str(value["org_id"]))
    return random.Random(seed).sample(population, size)


def registry_publishers(datasets: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the Registry's dataset feed to unique publisher organisation IDs."""
    publishers: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        publisher = dataset.get("publisher")
        if not isinstance(publisher, Mapping):
            continue
        org_id = str(publisher.get("org_id") or "").strip()
        if not org_id:
            continue
        current = publishers.setdefault(org_id, {
            "org_id": org_id,
            "name": publisher.get("name"),
            "website": publisher.get("website"),
            "dataset_count": 0,
            "dataset_titles": [],
        })
        current["dataset_count"] += 1
        title = str(dataset.get("title") or "").strip()
        if title and title not in current["dataset_titles"]:
            current["dataset_titles"].append(title)
    return sorted(publishers.values(), key=lambda value: str(value["org_id"]))


class RateLimitedClient:
    """HTTP client applying a fixed minimum interval between requests."""
    def __init__(self, min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS, timeout: float = 30.0):
        """Create a client with the configured request interval and timeout."""
        self.min_interval_seconds = min_interval_seconds
        self.timeout = timeout
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "FoundationIntelligencePlatform/1.0 (bounded discovery pilot)",
        })

    def get_json(self, url: str, *, params: Mapping[str, Any] | None = None) -> tuple[int, Any]:
        """Fetch and decode JSON, respecting the configured rate limit."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        response = self.session.get(url, params=params, timeout=self.timeout)
        self._last_request_at = time.monotonic()
        if response.status_code == 404:
            return 404, None
        response.raise_for_status()
        return response.status_code, response.json()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a JSON payload atomically so a partial run leaves no corrupt file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    temporary.replace(path)


def _load_existing(path: Path) -> list[dict[str, Any]]:
    """Load prior results so a sampling run can resume."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    records = payload.get("records") if isinstance(payload, Mapping) else None
    return [dict(item) for item in records or [] if isinstance(item, Mapping)]


def _candidate_pool(client: RateLimitedClient, count: int, seed: int, page_size: int) -> tuple[int, list[dict[str, Any]]]:
    """Build the pool of publishers eligible for sampling."""
    _, first_page = client.get_json(f"{API_BASE_URL}/org/", params={"limit": 1, "offset": 0})
    total = int((first_page or {}).get("count") or 0)
    if total <= 0:
        return 0, []

    page_count = max(1, min(5, (count + page_size - 1) // page_size + 1))
    max_offset = max(total - page_size, 0)
    rng = random.Random(seed)
    offsets = sorted({rng.randint(0, max_offset) for _ in range(page_count * 2)})[:page_count]
    pool: list[dict[str, Any]] = []
    for offset in offsets:
        _, page = client.get_json(f"{API_BASE_URL}/org/", params={"limit": page_size, "offset": offset})
        pool.extend(item for item in (page or {}).get("results", []) if isinstance(item, Mapping))
    return total, pool


def _registry_publisher_pool(client: RateLimitedClient) -> tuple[int, list[dict[str, Any]]]:
    """Build the publisher pool from stored registry identities."""
    _, payload = client.get_json(REGISTRY_FEED_URL)
    datasets = payload if isinstance(payload, list) else []
    return len(datasets), registry_publishers(datasets)


def collect_sample(
    output_path: Path,
    report_path: Path,
    *,
    count: int = 100,
    seed: int = 20260724,
    max_grants_per_organisation: int = 1000,
    resume: bool = False,
    source: str = "registry",
    attempt_limit: int | None = None,
    timeout: float = 15.0,
    client: RateLimitedClient | None = None,
) -> dict[str, Any]:
    """Collect a bounded, resumable random sample of publisher grant feeds."""
    if count < 1:
        raise ValueError("count must be at least 1")
    if max_grants_per_organisation < 1:
        raise ValueError("max_grants_per_organisation must be at least 1")
    if attempt_limit is not None and attempt_limit < 1:
        raise ValueError("attempt_limit must be at least 1 when provided")
    client = client or RateLimitedClient(timeout=timeout)
    existing = _load_existing(output_path) if resume else []
    completed = {str(item.get("org_id")) for item in existing if item.get("org_id")}
    if source == "registry":
        organisation_count, pool = _registry_publisher_pool(client)
    elif source == "organisations":
        organisation_count, pool = _candidate_pool(client, count, seed, page_size=1000)
    else:
        raise ValueError("source must be 'registry' or 'organisations'")
    chosen = select_organisation_sample(pool, count, seed)

    records = list(existing)
    counters: Counter[str] = Counter()
    attempts_this_run = 0
    for index, organisation in enumerate(chosen, start=1):
        org_id = str(organisation["org_id"])
        if org_id in completed:
            counters["already_completed"] += 1
            continue
        if attempt_limit is not None and attempts_this_run >= attempt_limit:
            break
        attempts_this_run += 1
        try:
            status, payload = client.get_json(
                f"{API_BASE_URL}/org/{org_id}/grants_made/",
                params={"limit": max_grants_per_organisation, "offset": 0},
            )
        except requests.RequestException as exc:
            records.append({"org_id": org_id, "summary": organisation, "error": str(exc)})
            counters["request_errors"] += 1
        else:
            if status == 404:
                records.append({"org_id": org_id, "summary": organisation, "grants_made": []})
                counters["not_a_funder"] += 1
            else:
                grants = [item for item in (payload or {}).get("results", []) if isinstance(item, Mapping)]
                records.append({
                    "org_id": org_id,
                    "summary": organisation,
                    "grants_made": grants,
                    "grants_made_count_reported": int((payload or {}).get("count") or len(grants)),
                    "grants_made_truncated": bool((payload or {}).get("next")),
                })
                counters["funder_responses"] += 1
                counters["grants_retrieved"] += len(grants)
                if (payload or {}).get("next"):
                    counters["truncated_funder_responses"] += 1
        completed.add(org_id)
        if attempts_this_run % 10 == 0 or index == len(chosen):
            _write_json(output_path, {
                "dataset_profile": "360giving-random-publisher-pilot-v1",
                "seed": seed,
                "records": records,
            })

    report = {
        "dataset_profile": "360giving-random-publisher-pilot-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "seed": seed,
        "population_source": source,
        "population_entries_reported": organisation_count,
        "candidate_pool_size": len(pool),
        "sample_requested": count,
        "sample_selected": len(chosen),
        "attempts_this_run": attempts_this_run,
        "attempt_limit": attempt_limit,
        "max_grants_per_organisation": max_grants_per_organisation,
        "rate_limit_seconds": client.min_interval_seconds,
        "counts": dict(counters),
        "output_path": str(output_path),
    }
    _write_json(output_path, {
        "dataset_profile": "360giving-random-publisher-pilot-v1",
        "seed": seed,
        "records": records,
    })
    _write_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect a bounded random 360Giving publisher sample.")
    parser.add_argument("--count", type=int, default=100, help="number of randomly sampled organisations")
    parser.add_argument("--seed", type=int, default=20260724, help="stable random seed")
    parser.add_argument(
        "--max-grants-per-organisation", type=int, default=1000,
        help="first-page grant cap per publisher; truncated publishers are labelled",
    )
    parser.add_argument(
        "--attempt-limit", type=int, default=None,
        help="maximum unattempted publishers to process in this run; useful with --resume",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    parser.add_argument(
        "--output", type=Path,
        default=Path("src/data/processed/360giving_registry_publisher_pilot.json"),
    )
    parser.add_argument(
        "--report", type=Path,
        default=Path("src/data/processed/360giving_registry_publisher_pilot_report.json"),
    )
    parser.add_argument(
        "--source", choices=["registry", "organisations"], default="registry",
        help="sample official Registry publishers (default) or the much larger all-organisation endpoint",
    )
    parser.add_argument("--resume", action="store_true", help="retain successfully attempted organisations")
    args = parser.parse_args()
    report = collect_sample(
        args.output, args.report, count=args.count, seed=args.seed,
        max_grants_per_organisation=args.max_grants_per_organisation, resume=args.resume,
        source=args.source, attempt_limit=args.attempt_limit, timeout=args.timeout,
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
