"""Durable PostgreSQL job enqueue, status, history and event reads."""

from __future__ import annotations

import json
from typing import Any, Mapping
import uuid

from sqlalchemy import text

from bff.postgres.base import PostgresRepository, iso_value


PIPELINE_JOB_TYPES = (
    "quick_consolidate",
    "refresh_charities",
    "refresh_grants",
    "full_run",
    "registry_enrichment",
    "source_funder_enrichment",
)


class PostgresJobRepository(PostgresRepository):
    async def enqueue(
        self,
        job_type: str,
        payload: Mapping[str, Any],
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        job_type = str(job_type).strip()
        if not job_type or len(job_type) > 120:
            raise ValueError("Invalid job type")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("A bounded idempotency key is required")
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
                        idempotency_key, requested_by, input
                    ) VALUES (
                        :job_id, :job_type, 'queued', :dataset_version,
                        :idempotency_key, :actor_id, CAST(:payload AS jsonb)
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

    async def latest_status(self) -> dict[str, Any]:
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
        limit = min(max(int(limit), 1), 100)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT job_run_id, job_type, status, dataset_version,
                               requested_by, requested_at, started_at, completed_at,
                               attempt, max_attempts, input, result,
                               error_class, error_message
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
            }
            for row in rows
        ]

    async def events(self, *, limit: int = 100) -> list[dict[str, Any]]:
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
