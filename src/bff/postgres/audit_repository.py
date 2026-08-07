"""Append-only PostgreSQL security audit sink."""

from __future__ import annotations

from datetime import datetime
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bff.audit import AuditEvent
from bff.utils.logging import logger


class PostgresAuditSink:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def record(self, event: AuditEvent) -> None:
        outcome = {
            "success": "succeeded",
            "denied": "denied",
            "failed": "failed",
        }.get(event.result, "failed")
        actor_role = event.actor_role if event.actor_role != "none" else "anonymous"
        async with self._sessions() as session, session.begin():
            dataset_version = event.dataset_version or await session.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            await session.execute(
                text(
                    """
                    INSERT INTO audit_events (
                        audit_event_id, request_id, actor_id, actor_role,
                        action, target, reason, outcome, http_status,
                        dataset_version, error_class, occurred_at, details
                    ) VALUES (
                        :event_id, :request_id, :actor_id, :actor_role,
                        :action, :target, :reason, :outcome, :http_status,
                        :dataset_version, :error_class, :occurred_at,
                        CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "event_id": uuid.uuid4(),
                    "request_id": event.request_id,
                    "actor_id": event.actor_id,
                    "actor_role": actor_role,
                    "action": event.action,
                    "target": event.target,
                    "reason": event.reason,
                    "outcome": outcome,
                    "http_status": event.http_status,
                    "dataset_version": dataset_version,
                    "error_class": event.error_class,
                    "occurred_at": datetime.fromisoformat(event.timestamp),
                    "details": json.dumps({"runtime": "postgresql"}, sort_keys=True),
                },
            )
        logger.info(
            "security_audit_persisted request_id=%s action=%s outcome=%s",
            event.request_id,
            event.action,
            outcome,
        )
