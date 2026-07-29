"""Queue/outbox worker lifecycle without API subprocesses or local lock files."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from bff.postgres.job_repository import PostgresJobRepository


JobHandler = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class QueuePublisher(Protocol):
    """SQS-compatible publishing boundary used by the transactional outbox."""

    async def publish(
        self, *, queue_name: str, message: Mapping[str, Any], deduplication_id: str
    ) -> str: ...


@dataclass(frozen=True)
class WorkerResult:
    status: str
    job_id: str | None


class DurableWorker:
    def __init__(
        self,
        repository: PostgresJobRepository,
        handlers: Mapping[str, JobHandler],
        *,
        worker_id: str,
        queue_name: str = "pipeline",
        lease_seconds: int = 60,
    ) -> None:
        self.repository = repository
        self.handlers = dict(handlers)
        self.worker_id = worker_id
        self.queue_name = queue_name
        self.lease_seconds = lease_seconds

    async def run_once(self) -> WorkerResult:
        job = await self.repository.claim(
            worker_id=self.worker_id,
            queue_name=self.queue_name,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return WorkerResult(status="idle", job_id=None)
        job_id = str(job["job_id"])
        handler = self.handlers.get(str(job["job_type"]))
        if handler is None:
            await self.repository.fail(
                job_id,
                worker_id=self.worker_id,
                error_class="UnsupportedJobType",
                failure_reason="No worker handler is registered for this job type",
                retryable=False,
            )
            return WorkerResult(status="failed", job_id=job_id)
        try:
            async with asyncio.timeout(int(job["timeout_seconds"])):
                result = await handler(job)
        except asyncio.TimeoutError:
            status = await self.repository.fail(
                job_id,
                worker_id=self.worker_id,
                error_class="JobTimeout",
                failure_reason="Worker execution exceeded the durable job timeout",
                retryable=True,
            )
            return WorkerResult(status=status, job_id=job_id)
        except Exception as exc:
            status = await self.repository.fail(
                job_id,
                worker_id=self.worker_id,
                error_class=exc.__class__.__name__,
                failure_reason="Worker handler failed; last-good data remains active",
                retryable=True,
            )
            return WorkerResult(status=status, job_id=job_id)
        succeeded = await self.repository.succeed(
            job_id,
            worker_id=self.worker_id,
            result=result,
        )
        return WorkerResult(status="succeeded" if succeeded else "lost_lease", job_id=job_id)


class OutboxDispatcher:
    def __init__(
        self,
        repository: PostgresJobRepository,
        publisher: QueuePublisher,
    ) -> None:
        self.repository = repository
        self.publisher = publisher

    async def run_once(self, *, limit: int = 100) -> dict[str, int]:
        published = 0
        failed = 0
        dead_lettered = 0
        for dispatch in await self.repository.due_dispatches(limit=limit):
            try:
                message_id = await self.publisher.publish(
                    queue_name=str(dispatch["queue_name"]),
                    message=dict(dispatch["message_body"]),
                    deduplication_id=str(dispatch["job_id"]),
                )
                await self.repository.mark_dispatch_published(
                    str(dispatch["outbox_id"]), queue_message_id=message_id
                )
                published += 1
            except Exception as exc:
                status = await self.repository.mark_dispatch_failed(
                    str(dispatch["outbox_id"]), error_class=exc.__class__.__name__
                )
                if status == "dead_lettered":
                    dead_lettered += 1
                else:
                    failed += 1
        return {
            "published": published,
            "failed": failed,
            "dead_lettered": dead_lettered,
        }
