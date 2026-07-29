"""Performance, cache, plan and concurrency gates for PostgreSQL serving."""

from __future__ import annotations

import asyncio
import os
from time import perf_counter
import unittest

from sqlalchemy import text

from bff.database import DatabaseManager, DatabaseSettings
from bff.postgres.analytics_repository import AnalyticsRepository
from bff.postgres.base import ANALYTICS_CACHE, VersionedTTLCache
from bff.postgres.organization_repository import OrganizationRepository
from bff.postgres.registry_repository import RegistryRepository, _SEARCH_SQL


def _plan_nodes(plan: dict) -> list[dict]:
    nodes = [plan]
    for child in plan.get("Plans", []):
        nodes.extend(_plan_nodes(child))
    return nodes


class TestVersionedCache(unittest.IsolatedAsyncioTestCase):
    async def test_cache_is_single_flight_copy_safe_and_dataset_scoped(self):
        cache = VersionedTTLCache(ttl_seconds=60, max_entries=4)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"items": [1]}

        first, second = await asyncio.gather(
            cache.get_or_create(("dataset-a", "map"), loader),
            cache.get_or_create(("dataset-a", "map"), loader),
        )
        self.assertEqual(calls, 1)
        first["items"].append(2)
        self.assertEqual(second, {"items": [1]})
        self.assertEqual(
            await cache.get_or_create(("dataset-a", "map"), loader),
            {"items": [1]},
        )
        self.assertGreater(cache.hit_ratio, 0)
        await cache.retain_dataset("dataset-b")
        await cache.get_or_create(("dataset-b", "map"), loader)
        self.assertEqual(calls, 2)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "RUN_POSTGRES_INTEGRATION=1 or TEST_DATABASE_URL is required",
)
class TestPostgreSQLPerformanceIntegration(unittest.TestCase):
    def test_materialization_controls_and_bounded_relationships(self):
        asyncio.run(self._materialization_controls())

    def test_query_plans_use_versioned_facts_and_search_indexes(self):
        asyncio.run(self._query_plans())

    def test_cold_warm_concurrency_and_heavy_query_isolation(self):
        asyncio.run(self._concurrency_and_isolation())

    def test_timeout_cancels_database_work_and_connection_recovers(self):
        asyncio.run(self._timeout_and_cancellation())

    @staticmethod
    def _manager() -> DatabaseManager:
        return DatabaseManager(DatabaseSettings.from_env())

    async def _materialization_controls(self):
        database = self._manager()
        sessions = database.sessions()
        analytics = AnalyticsRepository(sessions)
        try:
            async with sessions() as session:
                dataset_version = await session.scalar(
                    text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                )
                raw = (
                    await session.execute(
                        text(
                            """
                            SELECT COUNT(*) AS grant_count,
                                   COALESCE(SUM(eur_amount_minor) FILTER (
                                       WHERE eur_amount_status NOT IN (
                                           'missing','invalid','negative'
                                       )
                                   ), 0) AS amount_minor
                            FROM grant_overview_facts
                            WHERE dataset_version=:dataset_version
                            """
                        ),
                        {"dataset_version": dataset_version},
                    )
                ).one()
                aggregate = (
                    await session.execute(
                        text(
                            """
                            SELECT total_grants, total_amount_minor
                            FROM analytics_scope_totals
                            WHERE dataset_version=:dataset_version
                              AND amount_basis='eur_converted' AND currency='EUR'
                            """
                        ),
                        {"dataset_version": dataset_version},
                    )
                ).one()
                materialization = (
                    await session.execute(
                        text(
                            """
                            SELECT status, is_active, row_count
                            FROM materialization_versions
                            WHERE dataset_version=:dataset_version
                              AND materialization_name='dashboard_analytics'
                            """
                        ),
                        {"dataset_version": dataset_version},
                    )
                ).one()
                granularities = set(
                    (
                        await session.execute(
                            text(
                                """
                                SELECT DISTINCT granularity
                                FROM analytics_period_aggregates
                                WHERE dataset_version=:dataset_version
                                """
                            ),
                            {"dataset_version": dataset_version},
                        )
                    ).scalars()
                )
            self.assertEqual(tuple(raw), tuple(aggregate))
            self.assertEqual(materialization[0:2], ("active", True))
            self.assertGreater(materialization[2], 0)
            self.assertEqual(granularities, {"monthly", "yearly"})
            connections = await analytics.map_connections(limit=250)
            self.assertLessEqual(len(connections["connections"]), 250)
            self.assertEqual(
                connections["metadata"]["loading_mode"],
                "lazy_bounded_secondary_request",
            )
        finally:
            await database.close()

    async def _query_plans(self):
        database = self._manager()
        sessions = database.sessions()
        try:
            async with sessions() as session:
                dataset_version = await session.scalar(
                    text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                )
                exact_name = await session.scalar(
                    text(
                        """
                        SELECT normalized_name FROM charity_registry_organizations
                        WHERE dataset_version=:dataset_version
                          AND is_current_source_record
                        ORDER BY registry_id LIMIT 1
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
                text_query = str(exact_name).split()[0]
                map_plan = await session.scalar(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                        SELECT * FROM analytics_country_aggregates
                        WHERE dataset_version=:dataset_version
                          AND amount_basis='eur_converted' AND currency='EUR'
                        ORDER BY total_amount_minor DESC LIMIT 500
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
                exact_plan = await session.scalar(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                        SELECT registry_id FROM charity_registry_organizations
                        WHERE dataset_version=:dataset_version
                          AND is_current_source_record
                          AND normalized_name=:normalized_name LIMIT 21
                        """
                    ),
                    {
                        "dataset_version": dataset_version,
                        "normalized_name": exact_name,
                    },
                )
                search_plan = await session.scalar(
                    text(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                        + str(_SEARCH_SQL)
                    ),
                    {
                        "query": text_query,
                        "normalized_query": text_query.casefold(),
                        "registration_status": None,
                        "cursor_rank": None,
                        "cursor_registry_id": "",
                        "limit": 21,
                    },
                )
            map_document = map_plan[0]
            map_relations = {
                node.get("Relation Name") for node in _plan_nodes(map_document["Plan"])
            }
            self.assertIn("analytics_country_aggregates", map_relations)
            self.assertNotIn("grant_overview_facts", map_relations)
            exact_indexes = {
                node.get("Index Name") for node in _plan_nodes(exact_plan[0]["Plan"])
            }
            self.assertIn("ix_registry_current_normalized_name", exact_indexes)
            search_indexes = {
                node.get("Index Name") for node in _plan_nodes(search_plan[0]["Plan"])
            }
            self.assertTrue(
                {
                    "ix_registry_search_vector",
                    "ix_registry_normalized_name_trgm",
                }
                & search_indexes
            )
            self.assertLess(exact_plan[0]["Execution Time"], 300)
            # EXPLAIN instrumentation is intentionally allowed more headroom than
            # the separately measured warm API p95 service-level objective.
            self.assertLess(search_plan[0]["Execution Time"], 2000)
        finally:
            await database.close()

    async def _concurrency_and_isolation(self):
        database = self._manager()
        sessions = database.sessions()
        analytics = AnalyticsRepository(sessions)
        organizations = OrganizationRepository(sessions)

        async def dashboard():
            return await asyncio.gather(
                organizations.stats(),
                analytics.map(),
                analytics.trends(months=24),
                analytics.themes(),
                analytics.summary(),
            )

        async def health_duration() -> float:
            started = perf_counter()
            async with sessions() as session:
                self.assertEqual(await session.scalar(text("SELECT 1")), 1)
            return (perf_counter() - started) * 1000

        try:
            cold_durations = []
            for _ in range(20):
                await ANALYTICS_CACHE.clear()
                cold_started = perf_counter()
                await dashboard()
                cold_durations.append((perf_counter() - cold_started) * 1000)
            cold_p95 = sorted(cold_durations)[18]
            self.assertLess(cold_p95, 3000)

            warm_durations = []
            for _ in range(10):
                started = perf_counter()
                await dashboard()
                warm_durations.append((perf_counter() - started) * 1000)
            self.assertLess(sorted(warm_durations)[-1], 3000)

            concurrent_started = perf_counter()
            results = await asyncio.gather(*(dashboard() for _ in range(5)))
            self.assertEqual(len(results), 5)
            self.assertLess((perf_counter() - concurrent_started) * 1000, 3000)

            async def heavy_query():
                async with sessions() as session:
                    await session.execute(text("SELECT pg_sleep(1)"))

            heavy = asyncio.create_task(heavy_query())
            await asyncio.sleep(0.05)
            health_durations = await asyncio.gather(
                *(health_duration() for _ in range(5))
            )
            await heavy
            self.assertLess(sorted(health_durations)[-1], 100)
            self.assertGreater(ANALYTICS_CACHE.hit_ratio, 0.5)
            self.assertEqual(database.engine().sync_engine.pool.checkedout(), 0)
        finally:
            await database.close()

    async def _timeout_and_cancellation(self):
        database = self._manager()
        sessions = database.sessions()
        try:
            started = perf_counter()
            async with sessions() as session:
                with self.assertRaises(TimeoutError):
                    async with asyncio.timeout(0.05):
                        await session.execute(text("SELECT pg_sleep(2)"))
                await session.rollback()
            self.assertLess((perf_counter() - started) * 1000, 1000)
            async with sessions() as session:
                self.assertEqual(await session.scalar(text("SELECT 1")), 1)
        finally:
            await database.close()
