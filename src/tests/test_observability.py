import asyncio
from dataclasses import replace
import io
import json
import logging
import os
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from bff.database import DatabaseManager, DatabaseSettings
from bff.main import app
from bff.utils.logging import JsonFormatter, pseudonymous_actor_id
from observability.metrics import (
    REQUIRED_ALARMS,
    REQUIRED_METRICS,
    MetricsRegistry,
    load_observability_configuration,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _ExplodingDatabase:
    @staticmethod
    def pool_status():
        return {"checked_out": 0.0, "capacity": 0.0, "utilization_ratio": 0.0}

    async def readiness(self, *, expected_schema_version):
        raise AssertionError("liveness must not query PostgreSQL")


class TestObservabilityContracts(unittest.TestCase):
    def test_configuration_covers_required_metrics_alarms_and_runbooks(self):
        configuration = load_observability_configuration()
        self.assertTrue(REQUIRED_METRICS.issubset({item.name for item in configuration.metrics}))
        self.assertTrue(REQUIRED_ALARMS.issubset({item.name for item in configuration.alarms}))

        runbooks = (REPOSITORY_ROOT / "docs/remediation/observability-runbooks.md").read_text(
            encoding="utf-8"
        )
        for alarm in configuration.alarms:
            self.assertIn(f"## {alarm.runbook}", runbooks)

    def test_local_registry_records_bounded_typed_values(self):
        registry = MetricsRegistry(load_observability_configuration())
        dimensions = {
            "service": "foundation-intelligence-api",
            "environment": "test",
        }
        registry.increment(
            "api_errors_total",
            operation="GET /health/ready",
            error_class="http_503",
            **dimensions,
        )
        registry.set_gauge("readiness_success", 1, **dimensions)
        registry.observe(
            "api_request_duration_ms",
            12.5,
            operation="GET /health/ready",
            status="200",
            **dimensions,
        )
        registry.observe(
            "api_request_duration_ms",
            7.5,
            operation="GET /health/ready",
            status="200",
            **dimensions,
        )

        snapshot = {item["name"]: item for item in registry.snapshot()}
        self.assertEqual(snapshot["api_errors_total"]["value"], 1.0)
        self.assertEqual(snapshot["readiness_success"]["value"], 1.0)
        self.assertEqual(snapshot["api_request_duration_ms"]["count"], 2.0)
        self.assertEqual(snapshot["api_request_duration_ms"]["sum"], 20.0)

        with self.assertRaises(ValueError):
            registry.increment("unknown_metric")
        with self.assertRaises(ValueError):
            registry.set_gauge("readiness_success", 1, unbounded="value")

    def test_json_formatter_redacts_sensitive_fields(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("observability-contract-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        logger.info(
            "contact analyst@example.org token=secret-value",
            extra={
                "request_id": "request-1",
                "trace_id": "trace-1",
                "actor_id": pseudonymous_actor_id("analyst@example.org"),
                "operation": "GET /api/charities/{reg_charity_number}",
                "status": 200,
            },
        )
        payload = json.loads(stream.getvalue())
        serialized = json.dumps(payload)
        self.assertEqual(payload["service"], "foundation-intelligence-api")
        self.assertEqual(payload["request_id"], "request-1")
        self.assertEqual(payload["trace_id"], "trace-1")
        self.assertTrue(payload["actor_id"].startswith("sha256:"))
        self.assertNotIn("analyst@example.org", serialized)
        self.assertNotIn("secret-value", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_liveness_is_independent_of_database_readiness(self):
        original = app.state.database
        app.state.database = _ExplodingDatabase()
        try:
            client = TestClient(app)
            legacy = client.get("/health")
            live = client.get("/health/live", headers={"X-Trace-ID": "trace-local"})
            self.assertEqual(legacy.status_code, 200)
            self.assertEqual(live.status_code, 200)
            self.assertEqual(live.headers["X-Trace-ID"], "trace-local")
        finally:
            app.state.database = original

    def test_observability_route_is_admin_only_and_marks_cloudwatch_unexecuted(self):
        matching = [
            route
            for route in app.routes
            if getattr(route, "path", "") == "/api/admin/observability/metrics"
        ]
        if os.environ.get("APP_ENV", "development") not in {"staging", "production"}:
            self.assertEqual(matching, [])
            return
        self.assertEqual(len(matching), 1)
        source = (REPOSITORY_ROOT / "src/bff/postgres/observability_routes.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Role.ADMINISTRATOR", source)
        self.assertIn('"cloudwatch_execution": "not_tested"', source)


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1",
    "requires the explicitly enabled local PostgreSQL integration environment",
)
class TestPostgresObservabilityIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_bypasses_an_exhausted_application_pool(self):
        settings = replace(
            DatabaseSettings.from_env(),
            pool_size=1,
            max_overflow=0,
            pool_timeout_seconds=1,
        )
        manager = DatabaseManager(settings)
        held_connection = await manager.engine().connect()
        try:
            with self.assertRaises(SQLAlchemyTimeoutError):
                async with asyncio.timeout(1.5):
                    async with manager.engine().connect():
                        pass

            result = await manager.readiness(
                expected_schema_version="0006_governance_retention"
            )
            self.assertTrue(result["ready"])
            self.assertEqual(result["checks"]["postgresql"], "healthy")
            self.assertEqual(result["checks"]["schema_version"], "healthy")
            self.assertGreaterEqual(result["metadata"]["source_count"], 8)
            self.assertGreaterEqual(result["metadata"]["policy_count"], 14)
            self.assertIsNot(manager.engine(), manager.health_engine())
        finally:
            await held_connection.close()
            await manager.close()


if __name__ == "__main__":
    unittest.main()
