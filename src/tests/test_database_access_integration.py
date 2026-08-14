"""Explicit local PostgreSQL release gate for runtime database principals."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

import asyncpg

from bff.database import DatabaseManager, DatabaseSettings
from bff.postgres.governance_repository import GovernanceRepository
from bff.postgres.job_repository import PostgresJobRepository
from bff.postgres.pipeline_repository import PipelineRepository
from migration.database_access import (
    READER_TABLES,
    WRITER_READ_TABLES,
    WRITER_TABLE_PRIVILEGES,
    DatabaseAccessConfigurationError,
    bootstrap_runtime_configuration,
    configure_reader_role,
    configure_writer_role,
    connect_admin,
    default_configuration_snapshot,
    default_integrity_evidence,
    grant_equality_evidence,
    grant_snapshot,
    role_security_evidence,
    verify_reader_role,
    verify_writer_role,
)
from governance.retention import load_governance_configuration
from pipelines.durable import load_source_configurations


ROOT = Path(__file__).resolve().parents[2]


class _ExecutingRecordingConnection:
    """Record SQL while delegating every operation to a real PostgreSQL connection."""

    def __init__(self, connection: asyncpg.Connection):
        self.connection = connection
        self.statements: list[str] = []

    async def fetchrow(self, statement: str, *args):
        self.statements.append(statement)
        return await self.connection.fetchrow(statement, *args)

    async def fetchval(self, statement: str, *args):
        self.statements.append(statement)
        return await self.connection.fetchval(statement, *args)

    async def fetch(self, statement: str, *args):
        self.statements.append(statement)
        return await self.connection.fetch(statement, *args)

    async def execute(self, statement: str, *args):
        self.statements.append(statement)
        return await self.connection.execute(statement, *args)


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
            "DATABASE_CLUSTER_ADMIN_USER",
            "DATABASE_CLUSTER_ADMIN_PASSWORD",
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
            provisioning_attributes = await admin.fetchrow(
                "SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname=current_user"
            )
            self.assertIsNotNone(provisioning_attributes)
            self.assertFalse(provisioning_attributes["rolsuper"])
            self.assertTrue(provisioning_attributes["rolcreaterole"])
            has_reader_admin_option = await admin.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_auth_members AS membership
                    JOIN pg_roles AS parent ON parent.oid=membership.roleid
                    JOIN pg_roles AS member ON member.oid=membership.member
                    WHERE parent.rolname=$1 AND member.rolname=current_user
                      AND membership.admin_option
                )
                """,
                environment["DATABASE_READER_USER"],
            )
            self.assertTrue(has_reader_admin_option)

            await admin.execute(
                """
                INSERT INTO dataset_versions (
                    dataset_version, status, is_active, activated_at
                ) VALUES (
                    'db-access-integration', 'active', TRUE, CURRENT_TIMESTAMP
                )
                ON CONFLICT (dataset_version) DO NOTHING
                """
            )
            await admin.execute(
                """
                INSERT INTO materialization_versions (
                    materialization_version_id, dataset_version,
                    materialization_name, revision, status, is_active,
                    row_count, activated_at
                ) VALUES (
                    '00000000-0000-4000-8000-000000000001',
                    'db-access-integration', 'dashboard_analytics', 1,
                    'active', TRUE, 0, CURRENT_TIMESTAMP
                )
                ON CONFLICT (
                    dataset_version, materialization_name, revision
                ) DO NOTHING
                """
            )
            await admin.execute(
                """
                INSERT INTO analytics_scope_totals (
                    dataset_version, amount_basis, currency,
                    total_grants, known_geography_grants, multi_country_grants,
                    invalid_amount_grants, missing_date_grants,
                    negative_amount_grants, zero_amount_grants,
                    classified_grants, unclassified_grants,
                    source_classified_grants, inferred_classified_grants,
                    multiple_programme_grants, invalid_source_label_grants,
                    low_confidence_grants, total_amount_minor
                ) VALUES (
                    'db-access-integration', 'eur_converted', 'EUR',
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
                )
                ON CONFLICT (dataset_version, amount_basis, currency) DO NOTHING
                """
            )

            sources = tuple(load_source_configurations())
            governance = load_governance_configuration()
            defaults_before = await default_configuration_snapshot(admin)
            bootstrap = await bootstrap_runtime_configuration(
                admin, sources=sources, governance=governance
            )
            defaults_after = await default_configuration_snapshot(admin)
            default_evidence = default_integrity_evidence(
                before=defaults_before,
                after=defaults_after,
                expected_default_ids={
                    "source_configurations": (
                        source.source_name for source in sources
                    ),
                    "retention_policies": (
                        policy.retention_class for policy in governance.policies
                    ),
                },
            )

            recording = _ExecutingRecordingConnection(admin)
            await configure_reader_role(recording, environment)  # type: ignore[arg-type]
            await configure_writer_role(recording, environment)  # type: ignore[arg-type]
            # Exercise the existing-role path again with the same non-superuser.
            await configure_reader_role(recording, environment)  # type: ignore[arg-type]
            await configure_writer_role(recording, environment)  # type: ignore[arg-type]
            role_statements = [
                statement
                for statement in recording.statements
                if statement.lstrip().upper().startswith(("ALTER ROLE", "CREATE ROLE"))
            ]
            self.assertTrue(role_statements)
            self.assertTrue(
                all("SUPERUSER" not in statement.upper() for statement in role_statements),
                role_statements,
            )
            snapshot = await grant_snapshot(
                admin,
                (
                    environment["DATABASE_READER_USER"],
                    environment["DATABASE_WRITER_USER"],
                ),
            )
            grant_equality = grant_equality_evidence(
                snapshot,
                reader=environment["DATABASE_READER_USER"],
                writer=environment["DATABASE_WRITER_USER"],
                database_name=environment["DATABASE_NAME"],
            )
            role_evidence = {
                "reader": await role_security_evidence(
                    admin,
                    username=environment["DATABASE_READER_USER"],
                    database_name=environment["DATABASE_NAME"],
                ),
                "writer": await role_security_evidence(
                    admin,
                    username=environment["DATABASE_WRITER_USER"],
                    database_name=environment["DATABASE_NAME"],
                ),
            }
        finally:
            await admin.close()

        self.assertGreaterEqual(bootstrap["source_configurations_inserted"], 0)
        self.assertGreaterEqual(bootstrap["retention_policies_inserted"], 0)
        self.assertTrue(
            default_evidence["source_configurations"]["existing_ids_preserved"]
        )
        self.assertTrue(
            default_evidence["retention_policies"]["existing_checksums_unchanged"]
        )
        self.assertTrue(
            grant_equality["all_grants_equal_allowlist"], grant_equality
        )
        for evidence in role_evidence.values():
            self.assertFalse(evidence["rolsuper"])
            self.assertFalse(evidence["rolcreaterole"])
            self.assertFalse(evidence["rolcreatedb"])
            self.assertFalse(evidence["rolreplication"])
            self.assertFalse(evidence["rolbypassrls"])
            self.assertEqual(evidence["memberships"], [])
            self.assertEqual(sum(evidence["owned_objects"].values()), 0)

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
        await self._assert_dangerous_role_fails_closed(environment)

    async def _assert_dangerous_role_fails_closed(
        self, environment: dict[str, str]
    ) -> None:
        dangerous_role = f"fip_dangerous_{uuid.uuid4().hex[:16]}"
        dangerous_password = uuid.uuid4().hex + uuid.uuid4().hex
        cluster_admin = await asyncpg.connect(
            host=environment["DATABASE_HOST"],
            port=int(environment["DATABASE_PORT"]),
            user=environment["DATABASE_CLUSTER_ADMIN_USER"],
            password=environment["DATABASE_CLUSTER_ADMIN_PASSWORD"],
            database=environment["DATABASE_NAME"],
            ssl=environment["DATABASE_SSL_MODE"],
        )
        try:
            await cluster_admin.execute(
                f'CREATE ROLE "{dangerous_role}" LOGIN CREATEDB'
            )
            await cluster_admin.execute(
                f'GRANT "{dangerous_role}" TO '
                f'"{environment["DATABASE_ADMIN_USER"]}" WITH ADMIN OPTION'
            )
            release = await connect_admin(environment)
            try:
                recording = _ExecutingRecordingConnection(release)
                dangerous_environment = dict(environment)
                dangerous_environment["DATABASE_READER_USER"] = dangerous_role
                dangerous_environment["DATABASE_READER_PASSWORD"] = dangerous_password
                with self.assertRaises(DatabaseAccessConfigurationError):
                    await configure_reader_role(  # type: ignore[arg-type]
                        recording, dangerous_environment
                    )
                self.assertFalse(
                    any(
                        statement.lstrip().upper().startswith("ALTER ROLE")
                        for statement in recording.statements
                    )
                )
            finally:
                await release.close()
        finally:
            await cluster_admin.execute(
                f'REVOKE "{dangerous_role}" FROM '
                f'"{environment["DATABASE_ADMIN_USER"]}"'
            )
            await cluster_admin.execute(f'DROP ROLE "{dangerous_role}"')
            await cluster_admin.close()

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
