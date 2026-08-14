"""Shared PostgreSQL repository primitives without domain query ownership."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ActiveDatasetUnavailable(RuntimeError):
    """Raised when a serving query has no approved active dataset."""


class MaterializationUnavailable(RuntimeError):
    """Raised when an active dataset lacks its validated serving aggregates."""


class VersionedTTLCache:
    """Small async single-flight cache whose keys start with dataset version."""

    def __init__(self, *, ttl_seconds: float = 30.0, max_entries: int = 256):
        """Create a bounded cache keyed by dataset version with a per-entry TTL."""
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[Any, ...], tuple[float, Any]] = OrderedDict()
        self._inflight: dict[tuple[Any, ...], asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get_or_create(
        self,
        key: tuple[Any, ...],
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Return the cached value for a key, building and storing it on a miss."""
        now = monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                self.hits += 1
                self._entries.move_to_end(key)
                return deepcopy(entry[1])
            if entry:
                self._entries.pop(key, None)
            task = self._inflight.get(key)
            if task is None:
                self.misses += 1
                task = asyncio.create_task(loader())
                self._inflight[key] = task
            else:
                self.hits += 1
        try:
            value = await asyncio.shield(task)
        finally:
            async with self._lock:
                if task.done() and self._inflight.get(key) is task:
                    self._inflight.pop(key, None)
        if not task.cancelled() and task.exception() is None:
            async with self._lock:
                self._entries[key] = (monotonic() + self._ttl_seconds, deepcopy(value))
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
        return deepcopy(value)

    async def retain_dataset(self, dataset_version: str) -> None:
        """Drop every entry that does not belong to the given dataset version."""
        async with self._lock:
            stale = [key for key in self._entries if not key or key[0] != dataset_version]
            for key in stale:
                self._entries.pop(key, None)

    async def clear(self) -> None:
        """Discard all cached entries and reset hit accounting."""
        async with self._lock:
            self._entries.clear()
            self._inflight.clear()
            self.hits = 0
            self.misses = 0

    @property
    def hit_ratio(self) -> float:
        """Return the observed cache hit ratio for observability reporting."""
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


ANALYTICS_CACHE = VersionedTTLCache()


class PostgresRepository:
    """Base class supplying a session factory and active-dataset resolution."""
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        """Bind the repository to an async session factory."""
        self.sessions = sessions

    @staticmethod
    async def active_dataset(session: AsyncSession) -> str:
        """Return the single active dataset version, failing closed if absent."""
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
    """Decode a JSON column value, returning the fallback when absent or invalid."""
    return value if isinstance(value, type(fallback)) else fallback


def iso_value(value: Any) -> Any:
    """Render a date or datetime column as an ISO string, or None."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def number_value(value: Any) -> float | None:
    """Coerce a numeric column to float, returning None rather than raising."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return float(str(value))


def utc_now() -> str:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a SQLAlchemy result row into a plain dictionary."""
    return {
        key: iso_value(number_value(value) if isinstance(value, Decimal) else value)
        for key, value in row.items()
    }
