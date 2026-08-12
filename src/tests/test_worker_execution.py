"""Focused contracts for the long-running PostgreSQL worker execution path."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from pipelines.durable_worker import DurableWorker
from pipelines.worker_handlers import (
    LocalPipelineArtifactStore,
    WorkerConfigurationError,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[2]


class FakeJobRepository:
    def __init__(self, jobs: list[dict] | None = None) -> None:
        self.jobs = list(jobs or [])
        self.claims = 0
        self.heartbeats = 0
        self.succeeded: list[tuple[str, dict]] = []
        self.failed: list[tuple[str, dict]] = []

    async def claim(self, **_kwargs):
        self.claims += 1
        return self.jobs.pop(0) if self.jobs else None

    async def heartbeat(self, *_args, **_kwargs):
        self.heartbeats += 1
        return True

    async def succeed(self, job_id, **kwargs):
        self.succeeded.append((job_id, kwargs))
        return True

    async def fail(self, job_id, **kwargs):
        self.failed.append((job_id, kwargs))
        return "failed"


def job(job_id: str, job_type: str = "fixture", timeout: int = 5) -> dict:
    return {
        "job_id": job_id,
        "job_type": job_type,
        "input": {"fixture": True},
        "timeout_seconds": timeout,
        "attempt": 1,
    }


class TestWorkerExecution(unittest.IsolatedAsyncioTestCase):
    async def test_queued_job_dispatches_and_succeeds_only_after_handler(self):
        repository = FakeJobRepository([job("job-success")])
        domain_change = asyncio.Event()

        async def handler(_job):
            domain_change.set()
            return {"accepted_count": 1}

        result = await DurableWorker(
            repository, {"fixture": handler}, worker_id="worker-a"
        ).run_once()

        self.assertTrue(domain_change.is_set())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(repository.succeeded[0][0], "job-success")
        self.assertFalse(repository.failed)

    async def test_handler_exception_fails_job_and_worker_processes_next_job(self):
        repository = FakeJobRepository([job("job-failed"), job("job-next")])
        attempts = 0

        async def handler(_job):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("password=must-not-survive")
            return {"accepted_count": 1}

        worker = DurableWorker(
            repository, {"fixture": handler}, worker_id="worker-a"
        )
        failed = await worker.run_once()
        succeeded = await worker.run_once()

        self.assertEqual(failed.status, "failed")
        self.assertEqual(succeeded.status, "succeeded")
        self.assertFalse(repository.failed[0][1]["retryable"])
        self.assertNotIn(
            "must-not-survive", repository.failed[0][1]["failure_reason"]
        )

    async def test_unsupported_type_and_timeout_are_terminal_failures(self):
        unsupported = FakeJobRepository([job("job-unsupported", "unknown")])
        result = await DurableWorker(
            unsupported, {}, worker_id="worker-a"
        ).run_once()
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            unsupported.failed[0][1]["error_class"], "UnsupportedJobType"
        )

        async def blocked(_job):
            await asyncio.Event().wait()
            return {}

        timed = FakeJobRepository([job("job-timeout", timeout=0.001)])
        result = await DurableWorker(
            timed, {"fixture": blocked}, worker_id="worker-a"
        ).run_once()
        self.assertEqual(result.status, "failed")
        self.assertEqual(timed.failed[0][1]["error_class"], "JobTimeout")

    async def test_idle_claim_returns_without_dispatch(self):
        repository = FakeJobRepository()
        result = await DurableWorker(
            repository, {}, worker_id="worker-a"
        ).run_once()
        self.assertEqual(result.status, "idle")
        self.assertEqual(repository.claims, 1)


class TestWorkerStorageAndContracts(unittest.TestCase):
    def test_local_snapshot_store_verifies_and_atomically_reuses_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fallback = root / "fallback.db"
            current = root / "current.db"
            destination = root / "work" / "charities.db"
            fallback.write_bytes(b"verified-baseline")
            fallback_checksum = sha256_file(fallback)
            store = LocalPipelineArtifactStore(current, fallback, fallback_checksum)

            self.assertEqual(store.download_baseline(destination), fallback_checksum)
            destination.write_bytes(b"new-snapshot")
            new_checksum = sha256_file(destination)
            store.publish_snapshot(destination, checksum=new_checksum, job_id="job-1")
            destination.unlink()
            self.assertEqual(store.download_baseline(destination), new_checksum)

            store.checksum_path.write_text("0" * 64, encoding="utf-8")
            with self.assertRaisesRegex(
                WorkerConfigurationError, "checksum verification"
            ):
                store.download_baseline(destination)

    def test_postgres_claim_and_active_dedupe_are_database_enforced(self):
        repository = (ROOT / "src/bff/postgres/job_repository.py").read_text(
            encoding="utf-8"
        )
        migration = (ROOT / "alembic/versions/0007_worker_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("FOR UPDATE SKIP LOCKED", repository)
        self.assertIn("status IN ('queued', 'running')", migration)
        self.assertIn("CREATE UNIQUE INDEX uq_job_runs_active_dedupe", migration)

    def test_worker_loop_has_backoff_and_signal_shutdown(self):
        source = (ROOT / "src/pipelines/worker.py").read_text(encoding="utf-8")
        self.assertIn("asyncio.wait_for(stop.wait(), timeout=idle_seconds)", source)
        self.assertIn("signal.SIGTERM", source)
        self.assertIn("fail_expired", source)

    def test_backlog_retirement_is_explicit_terminal_and_preserves_rows(self):
        source = (ROOT / "src/pipelines/job_admin.py").read_text(encoding="utf-8")
        self.assertIn("WHERE job_run_id=ANY($1::uuid[])", source)
        self.assertIn("status='cancelled'", source)
        self.assertIn("PreWorkerDeploymentRetirement", source)
        self.assertIn("INSERT INTO job_events", source)
        self.assertNotIn("DELETE FROM job_runs", source)
        self.assertNotIn("TRUNCATE", source)


if __name__ == "__main__":
    unittest.main()
