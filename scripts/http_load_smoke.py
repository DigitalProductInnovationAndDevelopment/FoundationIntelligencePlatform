#!/usr/bin/env python3
"""Small authenticated HTTP smoke/load gate for an approved staging URL."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from time import perf_counter

import httpx


DEFAULT_PATHS = (
    "/health/ready",
    "/api/charities/stats",
    "/api/charities/grants/overview",
    "/api/charities/grants/map",
)


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("At least one timing is required")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(len(ordered) * quantile)) - 1]


async def run(base_url: str, token: str, samples: int, concurrency: int) -> dict[str, object]:
    semaphore = asyncio.Semaphore(concurrency)
    timings: dict[str, list[float]] = {path: [] for path in DEFAULT_PATHS}
    failures: list[dict[str, object]] = []
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(15)

    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        async def request(path: str) -> None:
            async with semaphore:
                started = perf_counter()
                try:
                    response = await client.get(path)
                except httpx.HTTPError as exc:
                    failures.append({"path": path, "error_class": exc.__class__.__name__})
                    return
                timings[path].append((perf_counter() - started) * 1000)
                if response.status_code != 200:
                    failures.append({"path": path, "status": response.status_code})

        await asyncio.gather(
            *(request(path) for path in DEFAULT_PATHS for _ in range(samples))
        )

    results = {
        path: {
            "samples": len(values),
            "p50_ms": round(percentile(values, 0.5), 2) if values else None,
            "p95_ms": round(percentile(values, 0.95), 2) if values else None,
        }
        for path, values in timings.items()
    }
    passed = not failures and all(
        values and percentile(values, 0.95) < 3000 for values in timings.values()
    )
    return {"status": "passed" if passed else "failed", "results": results, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    if not 2 <= args.samples <= 100:
        parser.error("--samples must be between 2 and 100")
    if not 1 <= args.concurrency <= 20:
        parser.error("--concurrency must be between 1 and 20")
    result = asyncio.run(
        run(args.base_url, args.token, args.samples, args.concurrency)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
