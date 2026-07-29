"""PostgreSQL-backed request idempotency for horizontally scaled runtimes."""

from __future__ import annotations

from datetime import timedelta
from typing import Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bff.security import IdempotencyConflict


RecordKey = Tuple[str, str, str]


class PostgresIdempotencyStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        ttl_seconds: int = 86_400,
    ) -> None:
        self.sessions = sessions
        self.ttl_seconds = int(timedelta(seconds=ttl_seconds).total_seconds())

    async def reserve(
        self,
        actor_id: str,
        action: str,
        key: str,
        fingerprint: str,
    ) -> RecordKey:
        record_key = (actor_id, action, key)
        async with self.sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    DELETE FROM idempotency_records
                    WHERE actor_id=:actor_id AND action=:action
                      AND idempotency_key=:idempotency_key
                      AND expires_at <= CURRENT_TIMESTAMP
                    """
                ),
                {
                    "actor_id": actor_id,
                    "action": action,
                    "idempotency_key": key,
                },
            )
            inserted = await session.scalar(
                text(
                    """
                    INSERT INTO idempotency_records (
                        actor_id, action, idempotency_key, request_hash,
                        status, expires_at
                    ) VALUES (
                        :actor_id, :action, :idempotency_key, :request_hash,
                        'reserved', CURRENT_TIMESTAMP
                            + CAST(:ttl_seconds AS integer) * INTERVAL '1 second'
                    )
                    ON CONFLICT (actor_id, action, idempotency_key) DO NOTHING
                    RETURNING request_hash
                    """
                ),
                {
                    "actor_id": actor_id,
                    "action": action,
                    "idempotency_key": key,
                    "request_hash": fingerprint,
                    "ttl_seconds": self.ttl_seconds,
                },
            )
            if inserted is None:
                existing = await session.scalar(
                    text(
                        """
                        SELECT request_hash FROM idempotency_records
                        WHERE actor_id=:actor_id AND action=:action
                          AND idempotency_key=:idempotency_key
                        """
                    ),
                    {
                        "actor_id": actor_id,
                        "action": action,
                        "idempotency_key": key,
                    },
                )
                raise IdempotencyConflict(different_request=existing != fingerprint)
        return record_key

    async def complete(self, record_key: RecordKey) -> None:
        actor_id, action, key = record_key
        async with self.sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    UPDATE idempotency_records
                    SET status='completed', completed_at=CURRENT_TIMESTAMP
                    WHERE actor_id=:actor_id AND action=:action
                      AND idempotency_key=:idempotency_key
                      AND status='reserved'
                    """
                ),
                {"actor_id": actor_id, "action": action, "idempotency_key": key},
            )

    async def release(self, record_key: RecordKey) -> None:
        actor_id, action, key = record_key
        async with self.sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    DELETE FROM idempotency_records
                    WHERE actor_id=:actor_id AND action=:action
                      AND idempotency_key=:idempotency_key
                      AND status='reserved'
                    """
                ),
                {"actor_id": actor_id, "action": action, "idempotency_key": key},
            )
