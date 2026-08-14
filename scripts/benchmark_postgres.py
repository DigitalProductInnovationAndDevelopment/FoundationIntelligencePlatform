#!/usr/bin/env python3
"""Bounded local PostgreSQL journey benchmark with percentile evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from time import perf_counter
from typing import Awaitable, Callable

from sqlalchemy import text

from bff.database import DatabaseManager, DatabaseSettings
from bff.postgres.analytics_repository import AnalyticsRepository
from bff.postgres.base import ANALYTICS_CACHE
from bff.postgres.funder_repository import SourceFunderRepository
from bff.postgres.organization_repository import OrganizationRepository
from bff.postgres.registry_repository import RegistryRepository


Journey = Callable[[], Awaitable[object]]


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


async def _measure(journey: Journey, samples: int) -> dict[str, float | int]:
    durations: list[float] = []
    errors = 0
    started = perf_counter()
    for _ in range(samples):
        sample_started = perf_counter()
        try:
            await journey()
        except Exception:
            errors += 1
        durations.append((perf_counter() - sample_started) * 1000)
    elapsed = perf_counter() - started
    return {
        "samples": samples,
        "p50_ms": round(_percentile(durations, 0.50), 2),
        "p95_ms": round(_percentile(durations, 0.95), 2),
        "p99_ms": round(_percentile(durations, 0.99), 2),
        "throughput_rps": round(samples / elapsed, 3) if elapsed else 0.0,
        "error_rate": round(errors / samples, 4),
    }


async def benchmark(samples: int, concurrency: int) -> dict[str, object]:
    database = DatabaseManager(DatabaseSettings.from_env())
    sessions = database.sessions()
    analytics = AnalyticsRepository(sessions)
    organizations = OrganizationRepository(sessions)
    registry = RegistryRepository(sessions)
    funders = SourceFunderRepository(sessions)
    try:
        async with sessions() as session:
            dataset_version = await session.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            exact_name = await session.scalar(
                text(
                    """
                    SELECT registered_name FROM charity_registry_organizations
                    WHERE dataset_version=:dataset_version AND is_current_source_record
                    ORDER BY registry_id LIMIT 1
                    """
                ),
                {"dataset_version": dataset_version},
            )
            text_name = str(exact_name).split()[0]
            funder_country = await session.scalar(
                text(
                    """
                    SELECT country_code FROM grant_source_funder_facts
                    WHERE dataset_version=:dataset_version AND country_code IS NOT NULL
                    GROUP BY country_code ORDER BY COUNT(*) DESC, country_code LIMIT 1
                    """
                ),
                {"dataset_version": dataset_version},
            )

        async def health():
            async with sessions() as session:
                return await session.scalar(text("SELECT 1"))

        async def primary_dashboard():
            return await asyncio.gather(
                organizations.stats(),
                analytics.map(),
                analytics.trends(months=24),
                analytics.themes(),
                analytics.summary(),
            )

        journeys: dict[str, Journey] = {
            "health": health,
            "organization_list": lambda: organizations.list(limit=20),
            "default_map": lambda: analytics.map(),
            "map_connections": lambda: analytics.map_connections(limit=250),
            "monthly_trends": lambda: analytics.trends(months=24),
            "yearly_trends": lambda: analytics.trends(
                months=120, granularity="yearly"
            ),
            "programme_themes": lambda: analytics.themes(),
            "network_summary": analytics.summary,
            "primary_dashboard": primary_dashboard,
            "registry_exact": lambda: registry.page(query=str(exact_name), limit=20),
            "registry_text": lambda: registry.page(query=text_name, limit=20),
            "funder_ranking": lambda: funders.list(
                beneficiary_country=str(funder_country), page_size=25
            ),
        }
        results: dict[str, object] = {}
        for name, journey in journeys.items():
            cold_samples = 20 if name == "primary_dashboard" else 1
            cold_durations = []
            for _ in range(cold_samples):
                await ANALYTICS_CACHE.clear()
                cold_started = perf_counter()
                await journey()
                cold_durations.append((perf_counter() - cold_started) * 1000)
            measured = await _measure(journey, samples)
            measured["cold_ms"] = round(cold_durations[0], 2)
            if cold_samples > 1:
                measured.update(
                    {
                        "cold_samples": cold_samples,
                        "cold_p50_ms": round(_percentile(cold_durations, 0.50), 2),
                        "cold_p95_ms": round(_percentile(cold_durations, 0.95), 2),
                        "cold_p99_ms": round(_percentile(cold_durations, 0.99), 2),
                    }
                )
            results[name] = measured

        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_primary():
            async with semaphore:
                await primary_dashboard()

        concurrent_started = perf_counter()
        concurrent_results = await asyncio.gather(
            *(bounded_primary() for _ in range(concurrency)),
            return_exceptions=True,
        )
        concurrent_elapsed = perf_counter() - concurrent_started
        results["primary_dashboard_concurrent"] = {
            "concurrency": concurrency,
            "elapsed_ms": round(concurrent_elapsed * 1000, 2),
            "throughput_rps": round(concurrency / concurrent_elapsed, 3),
            "errors": sum(isinstance(item, BaseException) for item in concurrent_results),
        }
        pool = database.engine().sync_engine.pool
        results["database_pool"] = {
            "configured_size": database.settings.pool_size,
            "configured_max_overflow": database.settings.max_overflow,
            "checked_out_after_run": pool.checkedout(),
            "checked_in_after_run": pool.checkedin(),
        }
        results["analytics_cache"] = {
            "hits": ANALYTICS_CACHE.hits,
            "misses": ANALYTICS_CACHE.misses,
            "hit_ratio": round(ANALYTICS_CACHE.hit_ratio, 4),
        }
        return {
            "dataset_version": dataset_version,
            "samples_per_journey": samples,
            "results": results,
        }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=5)
    arguments = parser.parse_args()
    if not 1 <= arguments.samples <= 100:
        parser.error("--samples must be between 1 and 100")
    if not 1 <= arguments.concurrency <= 20:
        parser.error("--concurrency must be between 1 and 20")
    print(
        json.dumps(
            asyncio.run(benchmark(arguments.samples, arguments.concurrency)),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
