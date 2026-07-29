"""Shared PostgreSQL repository primitives without domain query ownership."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ActiveDatasetUnavailable(RuntimeError):
    """Raised when a serving query has no approved active dataset."""


class PostgresRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    @staticmethod
    async def active_dataset(session: AsyncSession) -> str:
        dataset_version = await session.scalar(
            text(
                """
                SELECT dataset_version
                FROM dataset_versions
                WHERE is_active AND status='active'
                """
            )
        )
        if not dataset_version:
            raise ActiveDatasetUnavailable("No approved PostgreSQL dataset is active")
        return str(dataset_version)


def json_value(value: Any, fallback: Any) -> Any:
    return value if isinstance(value, type(fallback)) else fallback


def iso_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def number_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return float(str(value))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: iso_value(number_value(value) if isinstance(value, Decimal) else value)
        for key, value in row.items()
    }
