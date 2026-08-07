"""Phase-9 governance, exposure, retention and hold gates."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bff.database import DatabaseSettings
from bff.postgres.governance_repository import GovernanceRepository
from bff.utils.logging import redact_text
from governance.exposure import redact_for_logs, serialize_exposed_fields
from governance.retention import (
    DataHold,
    RetentionCandidate,
    RetentionPlanner,
    export_lifecycle_status,
    load_governance_configuration,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPOSITORY_ROOT / "alembic" / "versions" / "0006_governance_retention.py"
)
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


class TestGovernanceRetentionContracts(unittest.TestCase):
    def setUp(self):
        self.configuration = load_governance_configuration()
        self.planner = RetentionPlanner(self.configuration)

    def test_configuration_is_complete_honest_and_non_destructive(self):
        self.configuration.validate()
        self.assertFalse(self.configuration.destructive_deletion_enabled)
        self.assertFalse(self.configuration.production_activation_approved)
        self.assertTrue(self.configuration.restore_before_delete_required)
        self.assertTrue(
            all(policy.delete_after_days is None for policy in self.configuration.policies)
        )
        self.assertEqual(self.configuration.data_owners["data_owner"], "unassigned")
        self.assertEqual(self.configuration.service_recovery["rto_hours"], None)
        self.assertEqual(self.configuration.service_recovery["rpo_hours"], None)

    def test_field_exposure_is_allowlist_only(self):
        source = {
            "id": 7,
            "name": "Example Foundation",
            "reg_status": "registered",
            "email": "person@example.org",
            "postal_address": "1 Private Street",
            "secret": "must-not-escape",
            "database_column_added_later": "must-not-escape",
        }
        exposed = serialize_exposed_fields(
            source,
            policy_name="organization_summary",
            configuration=self.configuration,
        )
        self.assertEqual(
            exposed,
            {"id": 7, "name": "Example Foundation", "reg_status": "registered"},
        )
        with self.assertRaisesRegex(ValueError, "Unknown field exposure"):
            serialize_exposed_fields(
                source,
                policy_name="automatic_database_columns",
                configuration=self.configuration,
            )

    def test_log_redaction_covers_credentials_and_personal_fields(self):
        payload = {
            "authorization": "Bearer sensitive-token",
            "email": "person@example.org",
            "postal_address": "1 Private Street",
            "nested": {
                "database_url": "postgresql://user:password@example.invalid/db",
                "message": "Contact person@example.org",
            },
            "safe": "retained",
        }
        redacted = redact_for_logs(payload, self.configuration)
        self.assertEqual(redacted["authorization"], "[REDACTED]")
        self.assertEqual(redacted["email"], "[REDACTED]")
        self.assertEqual(redacted["postal_address"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["database_url"], "[REDACTED]")
        self.assertNotIn("person@example.org", redacted["nested"]["message"])
        self.assertEqual(redacted["safe"], "retained")
        self.assertNotIn("person@example.org", redact_text("email=person@example.org"))

    def test_archive_plan_is_dry_run_and_has_immutable_checksum(self):
        candidate = RetentionCandidate(
            target_type="storage_object",
            target_id="raw/example",
            retention_class="raw_source_evidence",
            last_modified_at=NOW - timedelta(days=31),
            object_count=1,
            record_count=10,
            total_bytes=500,
            target_checksums=("a" * 64,),
        )
        entry = self.planner.plan([candidate], [], now=NOW)[0]
        self.assertTrue(entry.dry_run)
        self.assertEqual(entry.action_type, "archive")
        self.assertEqual(entry.status, "reported")
        self.assertEqual(len(entry.manifest_checksum), 64)
        self.assertNotEqual(entry.action_type, "delete")

    def test_legal_and_incident_holds_override_retention(self):
        candidate = RetentionCandidate(
            target_type="dataset",
            target_id="v1",
            retention_class="raw_source_evidence",
            last_modified_at=NOW - timedelta(days=365),
        )
        for hold_type in ("legal", "incident"):
            hold = DataHold(
                hold_id=str(uuid.uuid4()),
                hold_type=hold_type,
                scope_type="dataset",
                scope_id="v1",
                status="active",
            )
            entry = self.planner.plan([candidate], [hold], now=NOW)[0]
            self.assertEqual(entry.status, "held")
            self.assertEqual(entry.action_type, "report")
            self.assertEqual(entry.hold_ids, (hold.hold_id,))

    def test_deletion_authorisation_is_fail_closed(self):
        for role, restored, holds in (
            ("viewer", False, []),
            ("administrator", False, []),
            ("administrator", True, ["legal-hold"]),
            ("administrator", True, []),
        ):
            with self.assertRaisesRegex(PermissionError, "globally disabled"):
                self.planner.assert_deletion_authorized(
                    retention_class="exports",
                    actor_role=role,
                    restore_verified=restored,
                    active_hold_ids=holds,
                )

    def test_export_expiration_reports_without_deleting(self):
        self.assertEqual(
            export_lifecycle_status(
                expires_at=NOW - timedelta(seconds=1),
                hold_until=None,
                now=NOW,
            ),
            "expiration_report_due",
        )
        self.assertEqual(
            export_lifecycle_status(
                expires_at=NOW - timedelta(days=1),
                hold_until=NOW + timedelta(days=1),
                now=NOW,
            ),
            "held",
        )

    def test_migration_and_routes_have_no_destructive_endpoint(self):
        migration = MIGRATION_PATH.read_text(encoding="utf-8")
        for table in (
            "retention_policies",
            "data_holds",
            "restore_verifications",
            "deletion_manifests",
            "data_subject_requests",
        ):
            self.assertIn(f"CREATE TABLE {table}", migration)
        self.assertIn("dry_run AND action_type <> 'delete'", migration)
        route_source = (
            REPOSITORY_ROOT / "src" / "bff" / "postgres" / "governance_routes.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Role.ADMINISTRATOR", route_source)
        self.assertNotIn('@router.delete(', route_source)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "RUN_POSTGRES_INTEGRATION=1 or TEST_DATABASE_URL is required",
)
class TestGovernanceRetentionPostgresIntegration(unittest.TestCase):
    def test_holds_manifests_restore_exports_and_subject_workflow(self):
        asyncio.run(self._exercise())

    @staticmethod
    def _database_url():
        return os.getenv("TEST_DATABASE_URL") or DatabaseSettings.from_env().sqlalchemy_url()

    async def _exercise(self):
        engine = create_async_engine(self._database_url(), pool_pre_ping=True)
        connection = await engine.connect()
        outer_transaction = await connection.begin()
        sessions = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        unique = uuid.uuid4().hex
        configuration = load_governance_configuration()
        planner = RetentionPlanner(configuration)
        repository = GovernanceRepository(sessions)
        try:
            dataset_version = await connection.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            self.assertEqual(
                await repository.synchronize_policies(configuration),
                len(configuration.policies),
            )
            self.assertTrue(
                all(
                    not policy["destructive_deletion_enabled"]
                    for policy in await repository.policies()
                )
            )

            hold = await repository.create_hold(
                hold_type="incident",
                scope_type="dataset",
                scope_id=str(dataset_version),
                reason="phase9 integration fixture",
                created_by="phase9-integration",
            )
            candidate = RetentionCandidate(
                target_type="dataset",
                target_id=str(dataset_version),
                retention_class="raw_source_evidence",
                last_modified_at=NOW - timedelta(days=365),
                record_count=1,
                target_checksums=("b" * 64,),
            )
            held_entry = planner.plan(
                [candidate], await repository.active_holds(), now=NOW
            )[0]
            self.assertEqual(held_entry.status, "held")
            held_action = (
                await repository.record_retention_plan(
                    [held_entry], requested_by="phase9-integration"
                )
            )[0]
            self.assertIsNotNone(held_action)

            await repository.release_hold(
                hold["data_hold_id"],
                released_by="phase9-integration",
                release_reason="fixture complete",
            )
            archive_entry = planner.plan(
                [candidate], await repository.active_holds(), now=NOW
            )[0]
            self.assertEqual(archive_entry.action_type, "archive")
            archive_action = (
                await repository.record_retention_plan(
                    [archive_entry], requested_by="phase9-integration"
                )
            )[0]

            manifest_id = await connection.scalar(
                text(
                    "SELECT deletion_manifest_id FROM deletion_manifests "
                    "WHERE retention_action_id=:action_id"
                ),
                {"action_id": uuid.UUID(archive_action)},
            )
            with self.assertRaises(DBAPIError):
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE deletion_manifests SET target_id='changed' "
                            "WHERE deletion_manifest_id=:manifest_id"
                        ),
                        {"manifest_id": manifest_id},
                    )

            restore_id = await repository.record_restore_verification(
                target_type="dataset",
                target_id=str(dataset_version),
                backup_reference="local://phase9-fixture",
                backup_checksum="c" * 64,
                verification_status="passed",
                verified_by="phase9-integration",
                evidence={"local_fixture": True},
            )
            with self.assertRaises(DBAPIError):
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE restore_verifications SET target_id='changed' "
                            "WHERE restore_verification_id=:restore_id"
                        ),
                        {"restore_id": uuid.UUID(restore_id)},
                    )

            await connection.execute(
                text(
                    """
                    INSERT INTO export_jobs (
                        export_job_id, dataset_version, export_type, status,
                        requested_by, completed_at, object_key, checksum,
                        row_count, expires_at, retention_class
                    ) VALUES (
                        :export_id, :dataset_version, 'phase9-fixture', 'succeeded',
                        'phase9-integration', CURRENT_TIMESTAMP,
                        'exports/phase9-fixture', :checksum, 1,
                        CURRENT_TIMESTAMP - INTERVAL '1 day', 'exports'
                    )
                    """
                ),
                {
                    "export_id": uuid.uuid4(),
                    "dataset_version": dataset_version,
                    "checksum": "d" * 64,
                },
            )
            self.assertTrue(
                any(
                    export["export_type"] == "phase9-fixture"
                    for export in await repository.expired_exports()
                )
            )
            subject_request = await repository.create_data_subject_request(
                request_type="access",
                subject_reference_hash="e" * 64,
                due_at=NOW + timedelta(days=30),
            )
            self.assertIsNotNone(subject_request)
            active_after = await connection.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            self.assertEqual(active_after, dataset_version)
        finally:
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
