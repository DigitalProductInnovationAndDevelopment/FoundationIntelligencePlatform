"""Durable PostgreSQL job enqueue, status, history and event reads."""

from __future__ import annotations

import json
from typing import Any, Mapping
import uuid

from sqlalchemy import text

from bff.postgres.base import PostgresRepository, iso_value
from pipelines.durable import QueueEnvelope


PIPELINE_JOB_TYPES = (
    "quick_consolidate",
    "refresh_charities",
    "refresh_grants",
    "full_run",
    "registry_enrichment",
    "source_funder_enrichment",
)


class PostgresJobRepository(PostgresRepository):
    """Durable job enqueue, lease claiming, status, history and event reads."""
    async def enqueue(
        self,
        job_type: str,
        payload: Mapping[str, Any],
        *,
        actor_id: str,
        idempotency_key: str,
        queue_name: str = "pipeline",
        max_attempts: int = 3,
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Durably enqueue a job, deduplicating on the caller's idempotency key."""
        job_type = str(job_type).strip()
        if not job_type or len(job_type) > 120:
            raise ValueError("Invalid job type")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("A bounded idempotency key is required")
        queue_name = str(queue_name).strip()
        if not queue_name or len(queue_name) > 120:
            raise ValueError("A bounded queue name is required")
        if not 1 <= int(max_attempts) <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        if not 1 <= int(timeout_seconds) <= 86_400:
            raise ValueError("timeout_seconds must be between 1 and 86400")
        async with self.sessions() as session, session.begin():
            dataset_version = await self.active_dataset(session)
            existing = (
                await session.execute(
                    text(
                        """
                        SELECT job_run_id, status, requested_at
                        FROM job_runs
                        WHERE job_type=:job_type AND idempotency_key=:idempotency_key
                        """
                    ),
                    {"job_type": job_type, "idempotency_key": idempotency_key},
                )
            ).mappings().first()
            if existing:
                return {
                    "job_id": str(existing["job_run_id"]),
                    "status": str(existing["status"]),
                    "requested_at": iso_value(existing["requested_at"]),
                    "idempotent_noop": True,
                }
            job_id = uuid.uuid4()
            requested_at = await session.scalar(
                text(
                    """
                    INSERT INTO job_runs (
                        job_run_id, job_type, status, dataset_version,
                        idempotency_key, requested_by, input, queue_name,
                        max_attempts, timeout_seconds, last_good_dataset_version
                    ) VALUES (
                        :job_id, :job_type, 'queued', :dataset_version,
                        :idempotency_key, :actor_id, CAST(:payload AS jsonb),
                        :queue_name, :max_attempts, :timeout_seconds, :dataset_version
                    )
                    RETURNING requested_at
                    """
                ),
                {
                    "job_id": job_id,
                    "job_type": job_type,
                    "dataset_version": dataset_version,
                    "idempotency_key": idempotency_key,
                    "actor_id": actor_id,
                    "payload": json.dumps(payload, sort_keys=True, default=str),
                    "queue_name": queue_name,
                    "max_attempts": int(max_attempts),
                    "timeout_seconds": int(timeout_seconds),
                },
            )
            envelope = QueueEnvelope(
                schema_version="job-envelope-v1",
                job_id=job_id,
                job_type=job_type,
                queue_name=queue_name,
                requested_at=str(iso_value(requested_at)),
                attempt=1,
                max_attempts=int(max_attempts),
            )
            await session.execute(
                text(
                    """
                    INSERT INTO job_dispatch_outbox (
                        job_dispatch_outbox_id, job_run_id, queue_name, message_body
                    ) VALUES (
                        :outbox_id, :job_id, :queue_name, CAST(:message_body AS jsonb)
                    )
                    """
                ),
                {
                    "outbox_id": uuid.uuid4(),
                    "job_id": job_id,
                    "queue_name": queue_name,
                    "message_body": json.dumps(envelope.payload, sort_keys=True),
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO job_events (
                        job_event_id, job_run_id, sequence, event_type,
                        actor_id, details
                    ) VALUES (
                        :event_id, :job_id, 1, 'queued', :actor_id,
                        CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "event_id": uuid.uuid4(),
                    "job_id": job_id,
                    "actor_id": actor_id,
                    "details": json.dumps({"job_type": job_type}, sort_keys=True),
                },
            )
        return {
            "job_id": str(job_id),
            "status": "queued",
            "requested_at": iso_value(requested_at),
            "idempotent_noop": False,
        }

    async def claim(
        self,
        *,
        worker_id: str,
        queue_name: str = "pipeline",
        lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Claim the next due job under a time-bounded worker lease."""
        worker_id = str(worker_id).strip()
        queue_name = str(queue_name).strip()
        if not worker_id or len(worker_id) > 200:
            raise ValueError("A bounded worker ID is required")
        if not queue_name or len(queue_name) > 120:
            raise ValueError("A bounded queue name is required")
        if not 5 <= int(lease_seconds) <= 3600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        WITH candidate AS (
                            SELECT job_run_id
                            FROM job_runs
                            WHERE status='queued' AND queue_name=:queue_name
                            ORDER BY requested_at, job_run_id
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE job_runs AS run
                        SET status='running',
                            started_at=COALESCE(run.started_at, CURRENT_TIMESTAMP),
                            heartbeat_at=CURRENT_TIMESTAMP,
                            lease_expires_at=CURRENT_TIMESTAMP
                                + CAST(:lease_seconds AS integer) * INTERVAL '1 second'
                        FROM candidate
                        WHERE run.job_run_id=candidate.job_run_id
                        RETURNING run.job_run_id, run.job_type, run.input,
                                  run.dataset_version, run.requested_at,
                                  run.started_at, run.attempt, run.max_attempts,
                                  run.timeout_seconds, run.last_good_dataset_version
                        """
                    ),
                    {"queue_name": queue_name, "lease_seconds": int(lease_seconds)},
                )
            ).mappings().first()
            await session.execute(
                text(
                    """
                    INSERT INTO worker_heartbeats (
                        worker_id, queue_name, job_run_id, status
                    ) VALUES (
                        :worker_id, :queue_name, :job_run_id, :status
                    )
                    ON CONFLICT (worker_id) DO UPDATE
                    SET queue_name=EXCLUDED.queue_name,
                        job_run_id=EXCLUDED.job_run_id,
                        status=EXCLUDED.status,
                        heartbeat_at=CURRENT_TIMESTAMP
                    """
                ),
                {
                    "worker_id": worker_id,
                    "queue_name": queue_name,
                    "job_run_id": row["job_run_id"] if row else None,
                    "status": "running" if row else "idle",
                },
            )
            if not row:
                return None
            await self._event(
                session,
                row["job_run_id"],
                "claimed",
                worker_id,
                {"worker_id": worker_id, "attempt": row["attempt"]},
            )
        return self._job_row(row)

    async def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> bool:
        """Extend a worker's lease to show the claimed job is still progressing."""
        async with self.sessions() as session, session.begin():
            updated = await session.scalar(
                text(
                    """
                    UPDATE job_runs
                    SET heartbeat_at=CURRENT_TIMESTAMP,
                        lease_expires_at=CURRENT_TIMESTAMP
                            + CAST(:lease_seconds AS integer) * INTERVAL '1 second'
                    WHERE job_run_id=:job_id AND status='running'
                    RETURNING job_run_id
                    """
                ),
                {"job_id": uuid.UUID(str(job_id)), "lease_seconds": int(lease_seconds)},
            )
            if updated:
                await session.execute(
                    text(
                        """
                        UPDATE worker_heartbeats
                        SET heartbeat_at=CURRENT_TIMESTAMP, status='running',
                            job_run_id=:job_id
                        WHERE worker_id=:worker_id
                        """
                    ),
                    {"job_id": updated, "worker_id": worker_id},
                )
        return updated is not None

    async def succeed(
        self,
        job_id: str,
        *,
        worker_id: str,
        result: Mapping[str, Any],
    ) -> bool:
        """Mark a claimed job complete and record its result manifest."""
        async with self.sessions() as session, session.begin():
            updated = await session.scalar(
                text(
                    """
                    UPDATE job_runs
                    SET status='succeeded', completed_at=CURRENT_TIMESTAMP,
                        heartbeat_at=CURRENT_TIMESTAMP, lease_expires_at=NULL,
                        result=CAST(:result AS jsonb), error_class=NULL,
                        error_message=NULL, failure_reason=NULL
                    WHERE job_run_id=:job_id AND status='running'
                    RETURNING job_run_id
                    """
                ),
                {
                    "job_id": uuid.UUID(str(job_id)),
                    "result": json.dumps(result, sort_keys=True, default=str),
                },
            )
            if updated:
                await self._event(session, updated, "succeeded", worker_id, dict(result))
                await self._idle_worker(session, worker_id)
        return updated is not None

    async def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        error_class: str,
        failure_reason: str,
        retryable: bool,
    ) -> str:
        """Record a job failure, applying retry or dead-letter state."""
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT job_run_id, status, attempt, max_attempts
                        FROM job_runs WHERE job_run_id=:job_id
                        FOR UPDATE
                        """
                    ),
                    {"job_id": uuid.UUID(str(job_id))},
                )
            ).mappings().first()
            if not row or row["status"] != "running":
                raise ValueError("Job is not running")
            will_retry = retryable and row["attempt"] < row["max_attempts"]
            status_value = "queued" if will_retry else (
                "dead_lettered" if retryable else "failed"
            )
            await session.execute(
                text(
                    """
                    UPDATE job_runs
                    SET status=:status,
                        attempt=attempt + CAST(:attempt_increment AS integer),
                        completed_at=CASE WHEN :will_retry THEN NULL ELSE CURRENT_TIMESTAMP END,
                        heartbeat_at=NULL, lease_expires_at=NULL,
                        error_class=:error_class, error_message=:failure_reason,
                        failure_reason=:failure_reason
                    WHERE job_run_id=:job_id
                    """
                ),
                {
                    "job_id": row["job_run_id"],
                    "status": status_value,
                    "attempt_increment": 1 if will_retry else 0,
                    "will_retry": will_retry,
                    "error_class": str(error_class)[:200],
                    "failure_reason": str(failure_reason)[:2000],
                },
            )
            await self._event(
                session,
                row["job_run_id"],
                "retry_queued" if will_retry else status_value,
                worker_id,
                {"error_class": str(error_class)[:200], "retryable": retryable},
            )
            await self._idle_worker(session, worker_id)
        return status_value

    async def requeue_expired(self, *, queue_name: str = "pipeline") -> dict[str, int]:
        """Return jobs whose worker lease expired to the queue."""
        async with self.sessions() as session, session.begin():
            retry_rows = (
                await session.execute(
                    text(
                        """
                        UPDATE job_runs
                        SET status='queued', attempt=attempt + 1,
                            heartbeat_at=NULL, lease_expires_at=NULL,
                            error_class='WorkerLeaseExpired',
                            error_message='Worker heartbeat lease expired',
                            failure_reason='worker_lease_expired'
                        WHERE queue_name=:queue_name AND status='running'
                          AND lease_expires_at < CURRENT_TIMESTAMP
                          AND attempt < max_attempts
                        RETURNING job_run_id
                        """
                    ),
                    {"queue_name": queue_name},
                )
            ).scalars().all()
            dead_rows = (
                await session.execute(
                    text(
                        """
                        UPDATE job_runs
                        SET status='dead_lettered', completed_at=CURRENT_TIMESTAMP,
                            heartbeat_at=NULL, lease_expires_at=NULL,
                            error_class='WorkerLeaseExpired',
                            error_message='Worker heartbeat lease expired after final attempt',
                            failure_reason='worker_lease_expired'
                        WHERE queue_name=:queue_name AND status='running'
                          AND lease_expires_at < CURRENT_TIMESTAMP
                          AND attempt >= max_attempts
                        RETURNING job_run_id
                        """
                    ),
                    {"queue_name": queue_name},
                )
            ).scalars().all()
            for job_id in retry_rows:
                await self._event(
                    session, job_id, "retry_queued", "lease-reaper", {"reason": "lease_expired"}
                )
            for job_id in dead_rows:
                await self._event(
                    session, job_id, "dead_lettered", "lease-reaper", {"reason": "lease_expired"}
                )
        return {"requeued": len(retry_rows), "dead_lettered": len(dead_rows)}

    async def due_dispatches(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return outbox rows ready to be published to the delivery transport."""
        bounded_limit = min(max(int(limit), 1), 100)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT job_dispatch_outbox_id, job_run_id, queue_name,
                               message_body, publish_attempts, created_at
                        FROM job_dispatch_outbox
                        WHERE status IN ('pending', 'failed')
                          AND next_attempt_at <= CURRENT_TIMESTAMP
                        ORDER BY created_at, job_dispatch_outbox_id
                        LIMIT :limit
                        """
                    ),
                    {"limit": bounded_limit},
                )
            ).mappings()
        return [self._dispatch_row(row) for row in rows]

    async def mark_dispatch_published(
        self, outbox_id: str, *, queue_message_id: str
    ) -> bool:
        """Mark an outbox row delivered to the transport."""
        async with self.sessions() as session, session.begin():
            updated = await session.scalar(
                text(
                    """
                    UPDATE job_dispatch_outbox
                    SET status='published', publish_attempts=publish_attempts + 1,
                        queue_message_id=:queue_message_id,
                        published_at=CURRENT_TIMESTAMP, last_error_class=NULL
                    WHERE job_dispatch_outbox_id=:outbox_id
                      AND status IN ('pending', 'failed')
                    RETURNING job_dispatch_outbox_id
                    """
                ),
                {
                    "outbox_id": uuid.UUID(str(outbox_id)),
                    "queue_message_id": str(queue_message_id)[:200],
                },
            )
        return updated is not None

    async def mark_dispatch_failed(
        self,
        outbox_id: str,
        *,
        error_class: str,
        retry_delay_seconds: int = 30,
        maximum_attempts: int = 5,
    ) -> str:
        """Record an outbox delivery failure and its retry state."""
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT publish_attempts FROM job_dispatch_outbox
                        WHERE job_dispatch_outbox_id=:outbox_id
                        FOR UPDATE
                        """
                    ),
                    {"outbox_id": uuid.UUID(str(outbox_id))},
                )
            ).mappings().first()
            if not row:
                raise ValueError("Dispatch outbox record does not exist")
            attempts = int(row["publish_attempts"]) + 1
            status_value = "dead_lettered" if attempts >= maximum_attempts else "failed"
            await session.execute(
                text(
                    """
                    UPDATE job_dispatch_outbox
                    SET status=:status, publish_attempts=:attempts,
                        next_attempt_at=CURRENT_TIMESTAMP
                            + CAST(:retry_delay_seconds AS integer) * INTERVAL '1 second',
                        last_error_class=:error_class
                    WHERE job_dispatch_outbox_id=:outbox_id
                    """
                ),
                {
                    "outbox_id": uuid.UUID(str(outbox_id)),
                    "status": status_value,
                    "attempts": attempts,
                    "retry_delay_seconds": max(1, int(retry_delay_seconds)),
                    "error_class": str(error_class)[:200],
                },
            )
        return status_value

    @staticmethod
    async def _event(session, job_id, event_type, actor_id, details) -> None:
        """Append one structured job event in sequence."""
        await session.execute(
            text(
                """
                INSERT INTO job_events (
                    job_event_id, job_run_id, sequence, event_type, actor_id, details
                ) VALUES (
                    :event_id, :job_id,
                    (SELECT COALESCE(MAX(sequence), 0) + 1
                     FROM job_events WHERE job_run_id=:job_id),
                    :event_type, :actor_id, CAST(:details AS jsonb)
                )
                """
            ),
            {
                "event_id": uuid.uuid4(),
                "job_id": job_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "details": json.dumps(details, sort_keys=True, default=str),
            },
        )

    @staticmethod
    async def _idle_worker(session, worker_id: str) -> None:
        """Report whether a worker heartbeat has gone stale."""
        await session.execute(
            text(
                """
                UPDATE worker_heartbeats
                SET status='idle', job_run_id=NULL, heartbeat_at=CURRENT_TIMESTAMP
                WHERE worker_id=:worker_id
                """
            ),
            {"worker_id": worker_id},
        )

    @staticmethod
    def _job_row(row: Mapping[str, Any]) -> dict[str, Any]:
        """Project a job row into the API job shape."""
        result = dict(row)
        result["job_id"] = str(result.pop("job_run_id"))
        for field in ("requested_at", "started_at", "completed_at"):
            if field in result:
                result[field] = iso_value(result[field])
        return result

    @staticmethod
    def _dispatch_row(row: Mapping[str, Any]) -> dict[str, Any]:
        """Project an outbox row into the dispatch shape."""
        result = dict(row)
        result["outbox_id"] = str(result.pop("job_dispatch_outbox_id"))
        result["job_id"] = str(result.pop("job_run_id"))
        result["created_at"] = iso_value(result["created_at"])
        return result

    async def latest_status(self) -> dict[str, Any]:
        """Return the most recent job's state."""
        async with self.sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT job_run_id, job_type, status, requested_at,
                               started_at, completed_at, error_message
                        FROM job_runs
                        WHERE job_type=ANY(CAST(:types AS text[]))
                        ORDER BY requested_at DESC, job_run_id DESC LIMIT 1
                        """
                    ),
                    {"types": list(PIPELINE_JOB_TYPES)},
                )
            ).mappings().first()
        if not row:
            return {
                "status": "idle",
                "started_at": None,
                "finished_at": None,
                "last_run_source": None,
                "error": None,
                "job_id": None,
            }
        mapped_status = {
            "created": "running",
            "queued": "running",
            "running": "running",
            "succeeded": "success",
            "failed": "failed",
            "cancelled": "failed",
            "timed_out": "failed",
            "dead_lettered": "failed",
        }.get(str(row["status"]), str(row["status"]))
        return {
            "status": mapped_status,
            "started_at": iso_value(row["started_at"] or row["requested_at"]),
            "finished_at": iso_value(row["completed_at"]),
            "last_run_source": row["job_type"],
            "error": row["error_message"],
            "job_id": str(row["job_run_id"]),
        }

    async def history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return a bounded window of recent job runs."""
        limit = min(max(int(limit), 1), 100)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT job_run_id, job_type, status, dataset_version,
                               requested_by, requested_at, started_at, completed_at,
                               attempt, max_attempts, input, result,
                               error_class, error_message, queue_name,
                               heartbeat_at, lease_expires_at, timeout_seconds,
                               failure_reason, last_good_dataset_version
                        FROM job_runs
                        ORDER BY requested_at DESC, job_run_id DESC LIMIT :limit
                        """
                    ),
                    {"limit": limit},
                )
            ).mappings()
        return [
            {
                **dict(row),
                "job_run_id": str(row["job_run_id"]),
                "requested_at": iso_value(row["requested_at"]),
                "started_at": iso_value(row["started_at"]),
                "completed_at": iso_value(row["completed_at"]),
                "heartbeat_at": iso_value(row["heartbeat_at"]),
                "lease_expires_at": iso_value(row["lease_expires_at"]),
            }
            for row in rows
        ]

    async def events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return a bounded window of recent structured job events."""
        limit = min(max(int(limit), 1), 100)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT event.job_event_id, event.job_run_id, event.sequence,
                               event.event_type, event.occurred_at, event.actor_id,
                               event.details, run.job_type
                        FROM job_events AS event
                        JOIN job_runs AS run USING (job_run_id)
                        ORDER BY event.occurred_at DESC, event.job_event_id DESC
                        LIMIT :limit
                        """
                    ),
                    {"limit": limit},
                )
            ).mappings()
        return [
            {
                **dict(row),
                "job_event_id": str(row["job_event_id"]),
                "job_run_id": str(row["job_run_id"]),
                "occurred_at": iso_value(row["occurred_at"]),
            }
            for row in rows
        ]
