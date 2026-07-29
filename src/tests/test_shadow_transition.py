from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from transition.runtime import (
    RuntimeMode,
    TransitionConfigurationError,
    load_transition_settings,
)
from transition.shadow import (
    ComparisonPolicy,
    ShadowComparisonCoordinator,
    ShadowRequest,
    compare_payloads,
)
from transition.sqlite_source import resolve_shadow_journey


ROOT = Path(__file__).resolve().parents[2]


class _Sink:
    def __init__(self):
        self.results = []

    def record(self, result):
        self.results.append(result)


class _Reader:
    def __init__(self, payload, delay=0.0):
        self.payload = payload
        self.delay = delay

    async def read(self, request):
        await asyncio.sleep(self.delay)
        return self.payload


class TestTransitionConfiguration(unittest.TestCase):
    def test_operational_environments_default_to_postgresql(self):
        settings = load_transition_settings({"APP_ENV": "production"})
        self.assertEqual(settings.mode, RuntimeMode.POSTGRESQL)
        self.assertTrue(settings.postgresql_authoritative)
        self.assertFalse(settings.shadow_enabled)

    def test_production_cannot_select_sqlite_migration_source(self):
        with self.assertRaises(TransitionConfigurationError):
            load_transition_settings(
                {"APP_ENV": "production", "DATA_RUNTIME_MODE": "sqlite_migration_source"}
            )

    def test_shadow_requires_a_separate_snapshot(self):
        with self.assertRaises(TransitionConfigurationError):
            load_transition_settings(
                {"APP_ENV": "development", "DATA_RUNTIME_MODE": "shadow_compare"}
            )
        with tempfile.NamedTemporaryFile(suffix=".db") as snapshot:
            settings = load_transition_settings(
                {
                    "APP_ENV": "development",
                    "DATA_RUNTIME_MODE": "shadow_compare",
                    "SHADOW_SQLITE_PATH": snapshot.name,
                    "DB_PATH": str(ROOT / "src/data/charities.db"),
                }
            )
            self.assertTrue(settings.shadow_enabled)
            self.assertTrue(settings.postgresql_authoritative)

    def test_shadow_cannot_alias_a_custom_migration_source(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as source:
            with self.assertRaises(TransitionConfigurationError):
                load_transition_settings(
                    {
                        "APP_ENV": "development",
                        "DATA_RUNTIME_MODE": "shadow_compare",
                        "SHADOW_SQLITE_PATH": source.name,
                        "DB_PATH": source.name,
                    }
                )


class TestShadowComparison(unittest.TestCase):
    def test_only_approved_list_paths_ignore_order(self):
        policy = ComparisonPolicy(unordered_paths=frozenset({"$.currencies"}))
        match = compare_payloads(
            {"currencies": ["GBP", "EUR"]},
            {"currencies": ["EUR", "GBP"]},
            journey="dashboard",
            policy=policy,
        )
        difference = compare_payloads(
            {"rankings": ["A", "B"]},
            {"rankings": ["B", "A"]},
            journey="donor_ranking",
            policy=policy,
        )
        self.assertEqual(match.status, "match")
        self.assertEqual(difference.status, "difference")

    def test_difference_evidence_contains_fingerprints_not_values(self):
        result = compare_payloads(
            {"profile": {"email": "sensitive@example.invalid"}},
            {"profile": {"email": "different@example.invalid"}},
            journey="profile_detail",
        )
        serialized = json.dumps([item.__dict__ for item in result.differences])
        self.assertEqual(result.difference_count, 1)
        self.assertNotIn("sensitive@example.invalid", serialized)
        self.assertIn("primary_fingerprint", serialized)

    def test_submission_never_waits_for_the_shadow_read(self):
        async def exercise():
            sink = _Sink()
            coordinator = ShadowComparisonCoordinator(
                _Reader({"value": 1}, delay=0.05),
                sink,
                policy=ComparisonPolicy(),
                timeout_seconds=1,
                maximum_pending=2,
            )
            request = ShadowRequest(
                journey="dashboard",
                method="GET",
                path="/api/charities/grants/overview",
                query_string="",
                primary_payload={"value": 1},
            )
            started = time.perf_counter()
            accepted = coordinator.submit(request)
            elapsed = time.perf_counter() - started
            self.assertTrue(accepted)
            self.assertLess(elapsed, 0.02)
            self.assertEqual(sink.results, [])
            await coordinator.drain()
            self.assertEqual(sink.results[0].status, "match")

        asyncio.run(exercise())

    def test_queue_bound_drops_work_without_changing_primary_authority(self):
        async def exercise():
            sink = _Sink()
            coordinator = ShadowComparisonCoordinator(
                _Reader({}, delay=0.05),
                sink,
                policy=ComparisonPolicy(),
                timeout_seconds=1,
                maximum_pending=1,
            )
            request = ShadowRequest("map", "GET", "/map", "", {})
            self.assertTrue(coordinator.submit(request))
            self.assertFalse(coordinator.submit(request))
            self.assertEqual(sink.results[0].status, "dropped_queue_full")
            self.assertTrue(sink.results[0].primary_authoritative)
            await coordinator.drain()

        asyncio.run(exercise())

    def test_all_required_journeys_are_versioned_and_routed(self):
        config = json.loads((ROOT / "config/runtime-transition.json").read_text())
        self.assertEqual(len(config["journeys"]), 21)
        cases = {
            "dashboard": ("/api/charities/grants/overview", ""),
            "date_filters": ("/api/charities/grants/overview", "date_from=2024-01-01"),
            "country_filters": ("/api/charities/grants/overview", "beneficiary_geographies=Germany"),
            "programme_filters": ("/api/charities/grants/overview", "programme_areas=Education"),
            "donor_filters": ("/api/charities/grants/overview", "donor=Trust"),
            "recipient_filters": ("/api/charities/grants/overview", "recipient=School"),
            "map": ("/api/charities/grants/map", ""),
            "map_relationships": ("/api/charities/grants/map/connections", ""),
            "monthly_trends": ("/api/charities/grants/overview/trends", "granularity=monthly"),
            "yearly_trends": ("/api/charities/grants/overview/trends", "granularity=yearly"),
            "donor_ranking": ("/api/charities/grants/funders", "beneficiary_country=GB"),
            "registry_search": ("/api/charities/directory/organizations", "query=trust"),
            "profile_detail": ("/api/charities/1", ""),
            "grant_list": ("/api/charities/1/grants", ""),
            "drill_down": ("/api/charities/grants/overview/drilldown", "selection_type=period&selection_value=2024"),
            "sankey": ("/api/charities/1/sankey", ""),
        }
        for expected, (path, query) in cases.items():
            self.assertEqual(resolve_shadow_journey("GET", path, query), expected)
        for contract_only in {
            "recipient_ranking", "score", "news", "pipeline_status",
            "manual_refresh_permissions",
        }:
            self.assertIn(contract_only, config["journeys"])


if __name__ == "__main__":
    unittest.main()
