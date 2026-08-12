"""Phase-8 durable pipeline, queue and immutable-storage gates."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bff.database import DatabaseSettings
from bff.postgres.idempotency_repository import PostgresIdempotencyStore
from bff.postgres.job_repository import PostgresJobRepository
from bff.postgres.pipeline_repository import PipelineRepository
from bff.security import IdempotencyConflict
from pipelines.durable import (
    InMemoryObjectStore,
    IngestionManifest,
    QueueEnvelope,
    StorageObject,
    load_source_configurations,
    object_key,
    require_sources,
    sha256_bytes,
)
from pipelines.durable_worker import DurableWorker, OutboxDispatcher


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPOSITORY_ROOT / "alembic" / "versions" / "0005_durable_pipeline.py"
WORKER_MIGRATION_PATH = REPOSITORY_ROOT / "alembic" / "versions" / "0007_worker_execution.py"
REQUIRED_SOURCES = {
    "360giving",
    "charity_commission",
    "philea",
    "hinchilla",
    "ecb",
    "google_news_rss",
    "article_content",
    "anthropic_news_summary",
}


class TestDurablePipelineContracts(unittest.IsolatedAsyncioTestCase):
    def test_source_register_is_complete_and_fail_closed(self):
        configurations = load_source_configurations()
        require_sources(configurations, REQUIRED_SOURCES)
        self.assertEqual({source.source_name for source in configurations}, REQUIRED_SOURCES)
        for source in configurations:
            self.assertFalse(source.enabled)
            self.assertTrue(source.governance_blocked)
            self.assertEqual(source.legal_status, "unresolved")
            self.assertEqual(source.licence_status, "unresolved")
            self.assertEqual(len(source.configuration_checksum), 64)
        with self.assertRaisesRegex(ValueError, "Governance-blocked"):
            replace(configurations[0], enabled=True).validate()

    def test_migration_contains_queue_storage_worker_and_immutability_contracts(self):
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        for table_name in (
            "source_configurations",
            "storage_objects",
            "ingestion_run_manifests",
            "job_dispatch_outbox",
            "worker_heartbeats",
        ):
            self.assertIn(f"CREATE TABLE {table_name}", source)
        self.assertIn("FOR EACH ROW EXECUTE FUNCTION protect_immutable_storage_object", source)
        self.assertIn("NOT enabled OR", source)
        self.assertIn("last_good_dataset_version", source)

    async def test_immutable_object_and_manifest_contracts(self):
        payload = b'{"source":"fixture","records":[1,2]}'
        checksum = sha256_bytes(payload)
        run_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
        descriptor = StorageObject(
            storage_object_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
            zone="raw",
            bucket_alias="raw",
            object_key=object_key(
                source_name="360giving",
                run_id=run_id,
                zone="raw",
                checksum=checksum,
                extension="json",
            ),
            object_version="fixture-v1",
            checksum=checksum,
            content_length=len(payload),
            content_type="application/json",
            source_name="360giving",
            source_ingestion_run_id=run_id,
        )
        store = InMemoryObjectStore()
        await store.put_if_absent(descriptor, payload)
        await store.put_if_absent(descriptor, payload)
        self.assertEqual(await store.get(descriptor), payload)
        with self.assertRaisesRegex(ValueError, "checksum"):
            await store.put_if_absent(descriptor, b"x" * len(payload))

        manifest = IngestionManifest.create(
            source_ingestion_run_id=run_id,
            schema_version="fixture-v1",
            source_name="360giving",
            source_version="2026-07-29",
            dataset_version="fixture-dataset",
            raw_object_id=descriptor.storage_object_id,
            validated_object_id=None,
            curated_object_id=None,
            watermark_before="100",
            watermark_after="200",
            record_count=10,
            accepted_count=7,
            rejected_count=2,
            quarantined_count=1,
            retry_count=0,
            generated_at="2026-07-29T00:00:00+00:00",
        )
        self.assertEqual(len(manifest.checksum), 64)
        self.assertEqual(manifest.checksum, manifest.checksum)
        with self.assertRaisesRegex(ValueError, "exceed"):
            replace(manifest, accepted_count=11).validate()

    def test_queue_envelope_is_bounded_and_contains_no_job_payload(self):
        envelope = QueueEnvelope(
            schema_version="job-envelope-v1",
            job_id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
            job_type="quick_consolidate",
            queue_name="pipeline",
            requested_at="2026-07-29T00:00:00+00:00",
            attempt=1,
            max_attempts=3,
        )
        self.assertEqual(envelope.payload["job_id"], str(envelope.job_id))
        self.assertNotIn("input", envelope.payload)
        with self.assertRaisesRegex(ValueError, "attempt"):
            replace(envelope, attempt=4).validate()

    async def test_worker_executes_handlers_outside_the_api_lifecycle(self):
        class Repository:
            def __init__(self):
                self.failed = None
                self.succeeded = None

            async def claim(self, **_kwargs):
                return {
                    "job_id": "44444444-4444-4444-8444-444444444444",
                    "job_type": "fixture",
                    "input": {"bounded": True},
                    "timeout_seconds": 5,
                }

            async def succeed(self, job_id, **kwargs):
                self.succeeded = (job_id, kwargs)
                return True

            async def fail(self, job_id, **kwargs):
                self.failed = (job_id, kwargs)
                return "queued"

        repository = Repository()

        async def handler(job):
            return {"accepted": 1, "job_type": job["job_type"]}

        result = await DurableWorker(
            repository,
            {"fixture": handler},
            worker_id="phase8-worker",
        ).run_once()
        self.assertEqual(result.status, "succeeded")
        self.assertIsNotNone(repository.succeeded)
        self.assertIsNone(repository.failed)

    async def test_outbox_dispatcher_uses_queue_contract_and_deduplication_id(self):
        class Repository:
            def __init__(self):
                self.published = None

            async def due_dispatches(self, *, limit):
                self.asserted_limit = limit
                return [
                    {
                        "outbox_id": "55555555-5555-4555-8555-555555555555",
                        "job_id": "66666666-6666-4666-8666-666666666666",
                        "queue_name": "pipeline",
                        "message_body": {"schema_version": "job-envelope-v1"},
                    }
                ]

            async def mark_dispatch_published(self, outbox_id, *, queue_message_id):
                self.published = (outbox_id, queue_message_id)

            async def mark_dispatch_failed(self, *_args, **_kwargs):
                raise AssertionError("success path must not mark failure")

        class Publisher:
            async def publish(self, *, queue_name, message, deduplication_id):
                self.call = (queue_name, message, deduplication_id)
                return "local-message-id"

        repository = Repository()
        publisher = Publisher()
        result = await OutboxDispatcher(repository, publisher).run_once(limit=10)
        self.assertEqual(result, {"published": 1, "failed": 0, "dead_lettered": 0})
        self.assertEqual(publisher.call[2], "66666666-6666-4666-8666-666666666666")
        self.assertEqual(repository.published[1], "local-message-id")

    def test_production_pipeline_code_has_no_local_lock_or_subprocess_coordination(self):
        paths = [
            REPOSITORY_ROOT / "src" / "bff" / "postgres" / "admin_routes.py",
            REPOSITORY_ROOT / "src" / "bff" / "postgres" / "job_repository.py",
            REPOSITORY_ROOT / "src" / "pipelines" / "durable_worker.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for forbidden in (
            "LOCK_FILE",
            "import fcntl",
            "import subprocess",
            "subprocess.",
            "Popen(",
            "pipeline_run.lock",
        ):
            self.assertNotIn(forbidden, combined)

    def test_worker_migration_adds_active_dedupe_without_rewriting_job_history(self):
        source = WORKER_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("ALTER TABLE job_runs ADD COLUMN active_dedupe_key", source)
        self.assertIn("CREATE UNIQUE INDEX uq_job_runs_active_dedupe", source)
        self.assertIn("status IN ('queued', 'running')", source)
        for forbidden in ("DROP TABLE JOB_RUNS", "TRUNCATE", "DELETE FROM JOB_RUNS"):
            self.assertNotIn(forbidden, source.upper())


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "RUN_POSTGRES_INTEGRATION=1 or TEST_DATABASE_URL is required",
)
class TestDurablePipelinePostgresIntegration(unittest.TestCase):
    def test_jobs_idempotency_ingestion_and_last_good_preservation(self):
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
        queue_name = f"phase8-{unique}"
        try:
            dataset_version = await connection.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            self.assertIsNotNone(dataset_version)
            jobs = PostgresJobRepository(sessions)
            first = await jobs.enqueue(
                "quick_consolidate",
                {"fixture": True},
                actor_id="phase8-integration",
                idempotency_key=f"phase8-job-{unique}",
                queue_name=queue_name,
                max_attempts=2,
                timeout_seconds=60,
            )
            duplicate = await jobs.enqueue(
                "quick_consolidate",
                {"fixture": True},
                actor_id="phase8-integration",
                idempotency_key=f"phase8-job-{unique}",
            )
            self.assertEqual(first["job_id"], duplicate["job_id"])
            self.assertTrue(duplicate["idempotent_noop"])
            dispatches = await jobs.due_dispatches()
            self.assertIn(first["job_id"], {row["job_id"] for row in dispatches})

            claimed = await jobs.claim(
                worker_id=f"worker-{unique}", queue_name=queue_name
            )
            self.assertEqual(claimed["job_id"], first["job_id"])
            self.assertTrue(
                await jobs.heartbeat(first["job_id"], worker_id=f"worker-{unique}")
            )
            self.assertEqual(
                await jobs.fail(
                    first["job_id"],
                    worker_id=f"worker-{unique}",
                    error_class="FixtureFailure",
                    failure_reason="last-good preservation fixture",
                    retryable=True,
                ),
                "queued",
            )
            retried = await jobs.claim(
                worker_id=f"worker-{unique}", queue_name=queue_name
            )
            self.assertEqual(retried["attempt"], 2)
            self.assertTrue(
                await jobs.succeed(
                    first["job_id"],
                    worker_id=f"worker-{unique}",
                    result={"accepted_count": 1},
                )
            )

            durable_keys = PostgresIdempotencyStore(sessions, ttl_seconds=60)
            key = await durable_keys.reserve(
                "phase8-actor", "phase8.action", unique, "a" * 64
            )
            with self.assertRaises(IdempotencyConflict):
                await durable_keys.reserve(
                    "phase8-actor", "phase8.action", unique, "a" * 64
                )
            await durable_keys.release(key)
            await durable_keys.reserve(
                "phase8-actor", "phase8.action", unique, "b" * 64
            )

            pipelines = PipelineRepository(sessions)
            configurations = load_source_configurations()
            self.assertEqual(
                await pipelines.synchronize_sources(configurations), len(configurations)
            )
            with self.assertRaisesRegex(ValueError, "unresolved governance"):
                await pipelines.set_source_enabled("360giving", enabled=True)

            ingestion_id = await pipelines.start_ingestion(
                source_name="360giving",
                dataset_version=str(dataset_version),
                job_id=first["job_id"],
                source_version=f"fixture-{unique}",
                source_uri="s3://raw/fixture",
                watermark_before="100",
            )
            raw_payload = b'{"fixture":true}'
            raw_checksum = sha256_bytes(raw_payload)
            raw_id = uuid.uuid4()
            raw = StorageObject(
                storage_object_id=raw_id,
                zone="raw",
                bucket_alias="raw",
                object_key=object_key(
                    source_name="360giving",
                    run_id=uuid.UUID(ingestion_id),
                    zone="raw",
                    checksum=raw_checksum,
                    extension="json",
                ),
                object_version=f"fixture-{unique}",
                checksum=raw_checksum,
                content_length=len(raw_payload),
                content_type="application/json",
                source_name="360giving",
                source_ingestion_run_id=uuid.UUID(ingestion_id),
            )
            await pipelines.record_object(raw)
            manifest = IngestionManifest.create(
                source_ingestion_run_id=uuid.UUID(ingestion_id),
                schema_version="fixture-v1",
                source_name="360giving",
                source_version=f"fixture-{unique}",
                dataset_version=str(dataset_version),
                raw_object_id=raw_id,
                validated_object_id=None,
                curated_object_id=None,
                watermark_before="100",
                watermark_after="101",
                record_count=1,
                accepted_count=1,
                rejected_count=0,
                quarantined_count=0,
                retry_count=1,
                generated_at="2026-07-29T00:00:00+00:00",
            )
            await pipelines.complete_ingestion(manifest)

            with self.assertRaises(DBAPIError):
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE storage_objects SET object_key='changed' "
                            "WHERE storage_object_id=:object_id"
                        ),
                        {"object_id": raw_id},
                    )

            active_after = await connection.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            self.assertEqual(active_after, dataset_version)
            last_good = await connection.scalar(
                text(
                    "SELECT last_good_dataset_version FROM job_runs "
                    "WHERE job_run_id=:job_id"
                ),
                {"job_id": uuid.UUID(first["job_id"])},
            )
            self.assertEqual(last_good, dataset_version)
        finally:
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
