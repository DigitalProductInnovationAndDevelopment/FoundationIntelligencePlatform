"""Explicit local PostgreSQL release gate for runtime database principals."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from bff.database import DatabaseManager, DatabaseSettings
from bff.postgres.governance_repository import GovernanceRepository
from bff.postgres.job_repository import PostgresJobRepository
from bff.postgres.pipeline_repository import PipelineRepository
from migration.database_access import (
    READER_TABLES,
    WRITER_READ_TABLES,
    WRITER_TABLE_PRIVILEGES,
    bootstrap_runtime_configuration,
    configure_reader_role,
    configure_writer_role,
    connect_admin,
    grant_snapshot,
    verify_reader_role,
    verify_writer_role,
)
from pipelines.durable import load_source_configurations


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.getenv("RUN_DB_ACCESS_INTEGRATION") == "1",
    "RUN_DB_ACCESS_INTEGRATION=1 is required",
)
class TestDatabaseAccessIntegration(unittest.TestCase):
    def test_real_roles_startup_reads_and_representative_mutations(self):
        asyncio.run(self._exercise())

    @staticmethod
    def _environment() -> dict[str, str]:
        required = (
            "DATABASE_HOST",
            "DATABASE_PORT",
            "DATABASE_NAME",
            "DATABASE_ADMIN_USER",
            "DATABASE_ADMIN_PASSWORD",
            "DATABASE_READER_USER",
            "DATABASE_READER_PASSWORD",
            "DATABASE_WRITER_USER",
            "DATABASE_WRITER_PASSWORD",
        )
        environment = {name: os.environ.get(name, "") for name in required}
        environment["DATABASE_SSL_MODE"] = os.environ.get(
            "DATABASE_SSL_MODE", "disable"
        )
        missing = [name for name in required if not environment[name]]
        if missing:
            raise AssertionError(f"Missing integration settings: {', '.join(missing)}")
        if environment["DATABASE_HOST"] not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("Database access integration is restricted to localhost")
        return environment

    async def _exercise(self) -> None:
        environment = self._environment()
        admin = await connect_admin(environment)
        try:
            bootstrap = await bootstrap_runtime_configuration(admin)
            await configure_reader_role(admin, environment)
            await configure_writer_role(admin, environment)
            snapshot = await grant_snapshot(
                admin,
                (
                    environment["DATABASE_READER_USER"],
                    environment["DATABASE_WRITER_USER"],
                ),
            )
        finally:
            await admin.close()

        self.assertGreaterEqual(bootstrap["source_configurations_inserted"], 0)
        self.assertGreaterEqual(bootstrap["retention_policies_inserted"], 0)

        reader_result = await verify_reader_role(environment)
        writer_result = await verify_writer_role(environment)
        for check, passed in reader_result.items():
            if check == "tls_in_use" and environment["DATABASE_SSL_MODE"] == "disable":
                self.assertFalse(passed)
            else:
                self.assertTrue(passed, check)
        self.assertTrue(all(writer_result.values()), writer_result)

        table_grants = {
            (row["grantee"], row["table_name"], row["privilege_type"])
            for row in snapshot["table_privileges"]
        }
        reader = environment["DATABASE_READER_USER"]
        writer = environment["DATABASE_WRITER_USER"]
        expected_reader = {(reader, table, "SELECT") for table in READER_TABLES}
        expected_writer = {(writer, table, "SELECT") for table in WRITER_READ_TABLES}
        expected_writer.update(
            (writer, table, privilege)
            for table, privileges in WRITER_TABLE_PRIVILEGES.items()
            for privilege in privileges
        )
        self.assertEqual(
            {grant for grant in table_grants if grant[0] == reader}, expected_reader
        )
        self.assertEqual(
            {grant for grant in table_grants if grant[0] == writer}, expected_writer
        )
        self.assertEqual(snapshot["sequence_privileges"], [])

        writer_settings = DatabaseSettings.writer_from_env(
            {
                "DATABASE_HOST": environment["DATABASE_HOST"],
                "DATABASE_PORT": environment["DATABASE_PORT"],
                "DATABASE_NAME": environment["DATABASE_NAME"],
                "DATABASE_SSL_MODE": environment["DATABASE_SSL_MODE"],
                "DATABASE_WRITE_USER": environment["DATABASE_WRITER_USER"],
                "DATABASE_WRITE_PASSWORD": environment["DATABASE_WRITER_PASSWORD"],
            }
        )
        self.assertIsNotNone(writer_settings)
        writer_database = DatabaseManager(writer_settings)  # type: ignore[arg-type]
        try:
            sessions = writer_database.sessions()
            operator_job = await PostgresJobRepository(sessions).enqueue(
                "refresh_charities",
                {"mode": "refresh_charities"},
                actor_id="operator-integration",
                idempotency_key=f"operator-{uuid.uuid4()}",
            )
            self.assertEqual(operator_job["status"], "queued")

            governance = GovernanceRepository(sessions)
            hold = await governance.create_hold(
                hold_type="incident",
                scope_type="dataset",
                scope_id="db-access-integration",
                reason="database access integration",
                created_by="admin-integration",
            )
            released = await governance.release_hold(
                hold["data_hold_id"],
                released_by="admin-integration",
                release_reason="integration complete",
            )
            self.assertEqual(released["status"], "released")

            source_name = load_source_configurations()[0].source_name
            source = await PipelineRepository(sessions).set_source_enabled(
                source_name, enabled=False
            )
            self.assertFalse(source["enabled"])
        finally:
            await writer_database.close()

        self._assert_reader_only_runtime(environment)

    def _assert_reader_only_runtime(self, environment: dict[str, str]) -> None:
        script = """
from fastapi.testclient import TestClient
from bff.main import app

with TestClient(app) as client:
    assert app.state.database.writer_configured is False
    expected = {
        '/health/ready': 200,
        '/api/charities/stats': 200,
        '/api/charities/grants/map': 200,
        '/api/charities/directory/organizations': 200,
        '/api/scraper/status': 200,
    }
    observed = {path: client.get(path).status_code for path in expected}
    assert observed == expected, observed
"""
        process_environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("DATABASE_")
        }
        process_environment.update(
            {
                "APP_ENV": "demo",
                "DATA_RUNTIME_MODE": "postgresql",
                "AUTH_MODE": "public_readonly",
                "DEV_AUTH_ENABLED": "false",
                "CORE_PROXY_ENABLED": "false",
                "CORS_ORIGINS": "",
                "DATABASE_HOST": environment["DATABASE_HOST"],
                "DATABASE_PORT": environment["DATABASE_PORT"],
                "DATABASE_NAME": environment["DATABASE_NAME"],
                "DATABASE_USER": environment["DATABASE_READER_USER"],
                "DATABASE_PASSWORD": environment["DATABASE_READER_PASSWORD"],
                "DATABASE_SSL_MODE": environment["DATABASE_SSL_MODE"],
                "PYTHONPATH": str(ROOT / "src"),
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=process_environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
