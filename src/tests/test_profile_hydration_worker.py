"""Focused contracts for durable source-funder profile hydration."""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bff.database import DatabaseSettings
from bff.postgres.funder_repository import SourceFunderRepository
from bff.postgres.job_repository import PostgresJobRepository
from pipelines.durable_worker import DurableWorker
from pipelines.worker_handlers import WorkerHandlers


class _FakeJobRepository:
    def __init__(self, job: dict) -> None:
        self.job = job
        self.succeeded: list[tuple[str, dict]] = []
        self.failed: list[tuple[str, dict]] = []

    async def claim(self, **_kwargs):
        job, self.job = self.job, None
        return job

    async def heartbeat(self, *_args, **_kwargs):
        return True

    async def succeed(self, job_id, **kwargs):
        self.succeeded.append((job_id, kwargs))
        return True

    async def fail(self, job_id, **kwargs):
        self.failed.append((job_id, kwargs))
        return "failed"


class TestProfileHydrationWorker(unittest.IsolatedAsyncioTestCase):
    async def test_hydration_job_is_registered_and_completes_without_unsupported_type(self):
        job_id = str(uuid.uuid4())
        repository = _FakeJobRepository(
            {
                "job_id": job_id,
                "job_type": "source_funder_profile_hydration",
                "input": {"source_funder_key": "gb-chc:fixture"},
                "timeout_seconds": 5,
                "attempt": 1,
            }
        )
        handlers = WorkerHandlers(
            object(),
            pipeline_settings=None,
            artifact_store=None,
            source_schema_version=None,
            code_revision=None,
        )
        hydrated = AsyncMock(
            return_value={
                "source_funder_key": "gb-chc:fixture",
                "profile_id": 123,
                "status": "ready",
            }
        )

        with patch(
            "pipelines.worker_handlers.SourceFunderRepository"
        ) as repository_type:
            repository_type.return_value.hydrate_profile_cache = hydrated
            result = await DurableWorker(
                repository,
                handlers.mapping,
                worker_id="worker-test",
            ).run_once()

        self.assertIn("source_funder_profile_hydration", handlers.mapping)
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(repository.failed)
        hydrated.assert_awaited_once_with(
            "gb-chc:fixture",
            job_id=job_id,
        )


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "RUN_POSTGRES_INTEGRATION=1 or TEST_DATABASE_URL is required",
)
class TestProfileHydrationPostgreSQLIntegration(unittest.TestCase):
    def test_success_and_terminal_failure_update_profile_cache(self):
        asyncio.run(self._exercise())

    @staticmethod
    def _database_url() -> str:
        return os.getenv("TEST_DATABASE_URL") or DatabaseSettings.from_env().sqlalchemy_url()

    async def _exercise(self) -> None:
        engine = create_async_engine(self._database_url(), pool_pre_ping=True)
        connection = await engine.connect()
        outer_transaction = await connection.begin()
        sessions = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with sessions() as session:
                identity = (
                    await session.execute(
                        text(
                            """
                            SELECT fact.source_funder_key, fact.linked_profile_id
                            FROM grant_source_funder_facts AS fact
                            JOIN dataset_versions AS dataset
                              ON dataset.dataset_version=fact.dataset_version
                             AND dataset.is_active
                            JOIN charities AS profile
                              ON profile.dataset_version=fact.dataset_version
                             AND profile.charity_id=fact.linked_profile_id
                            WHERE fact.linked_profile_id IS NOT NULL
                            ORDER BY fact.source_funder_key LIMIT 1
                            """
                        )
                    )
                ).one()
            key = str(identity[0])
            funders = SourceFunderRepository(sessions)
            handlers = WorkerHandlers(
                sessions,
                pipeline_settings=None,
                artifact_store=None,
                source_schema_version=None,
                code_revision=None,
            )

            queued = await funders.queue_profile_cache(
                key,
                actor_id="profile-hydration-test",
                idempotency_key=f"profile-success-{uuid.uuid4()}",
            )
            result = await handlers.source_funder_profile_hydration(
                {
                    "job_id": queued["job_id"],
                    "job_type": "source_funder_profile_hydration",
                    "input": {"source_funder_key": key},
                }
            )
            cached = await funders.profile_cache(key)
            self.assertEqual(result["accepted_count"], 1)
            self.assertEqual(cached["status"], "ready")
            self.assertIsInstance(cached["payload"], dict)
            self.assertIn("all_details", cached["payload"])

            failed_job = await funders.queue_profile_cache(
                key,
                actor_id="profile-hydration-test",
                idempotency_key=f"profile-failure-{uuid.uuid4()}",
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE job_runs SET status='running',
                            started_at=CURRENT_TIMESTAMP,
                            heartbeat_at=CURRENT_TIMESTAMP
                        WHERE job_run_id=:job_id
                        """
                    ),
                    {"job_id": uuid.UUID(failed_job["job_id"])},
                )
            status = await PostgresJobRepository(sessions).fail(
                failed_job["job_id"],
                worker_id="profile-hydration-test",
                error_class="UnexpectedHandlerError",
                failure_reason="redacted worker failure",
                retryable=False,
            )
            failed_cache = await funders.profile_cache(key)
            self.assertEqual(status, "failed")
            self.assertEqual(failed_cache["status"], "failed")
            self.assertIsNone(failed_cache["payload"])
            self.assertEqual(
                failed_cache["error"],
                "Profile hydration could not be completed.",
            )
        finally:
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
