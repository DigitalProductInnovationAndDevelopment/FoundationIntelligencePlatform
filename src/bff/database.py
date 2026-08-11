"""Bounded async PostgreSQL engine and readiness contract."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping, Optional

from sqlalchemy import URL, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from bff.utils.logging import logger


class DatabaseConfigurationError(RuntimeError):
    """Raised when PostgreSQL configuration is incomplete or unsafe."""


def _positive_int(value: Optional[str], default: int) -> int:
    parsed = int(value) if value is not None else default
    if parsed <= 0:
        raise DatabaseConfigurationError("Database numeric settings must be positive")
    return parsed


def _ssl_mode(environment: Mapping[str, str]) -> str:
    """Resolve one unambiguous asyncpg SSL mode from env or DATABASE_URL."""
    explicit = environment.get("DATABASE_SSL_MODE")
    url = environment.get("DATABASE_URL")
    query_modes: set[str] = set()
    query: Mapping[str, str | tuple[str, ...]] = {}
    if url:
        try:
            query = make_url(url).query
        except Exception:
            query = {}
        for key in ("ssl", "sslmode"):
            value = query.get(key)
            if value is None:
                continue
            values = value if isinstance(value, tuple) else (value,)
            query_modes.update(str(item).strip().lower() for item in values)
    if len(query_modes) > 1:
        raise DatabaseConfigurationError(
            "DATABASE_URL contains conflicting PostgreSQL SSL modes"
        )
    query_mode = next(iter(query_modes), None)
    if explicit is not None:
        mode = explicit.strip().lower()
        if query_mode is not None and query_mode != mode:
            raise DatabaseConfigurationError(
                "DATABASE_SSL_MODE conflicts with DATABASE_URL SSL configuration"
            )
    else:
        mode = query_mode or "disable"
    if mode not in {"disable", "require"}:
        raise DatabaseConfigurationError(
            "DATABASE_SSL_MODE must be disable or require"
        )
    return mode


@dataclass(frozen=True)
class DatabaseSettings:
    url: Optional[str]
    host: Optional[str]
    port: int
    name: Optional[str]
    user: Optional[str]
    password: Optional[str]
    password_file: Optional[str]
    ssl_mode: str
    pool_size: int
    max_overflow: int
    pool_timeout_seconds: int
    connect_timeout_seconds: int
    statement_timeout_ms: int

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "DatabaseSettings":
        env = os.environ if environ is None else environ
        return cls(
            url=env.get("DATABASE_URL"),
            host=env.get("DATABASE_HOST"),
            port=_positive_int(env.get("DATABASE_PORT"), 5432),
            name=env.get("DATABASE_NAME"),
            user=env.get("DATABASE_USER"),
            password=env.get("DATABASE_PASSWORD"),
            password_file=env.get("DATABASE_PASSWORD_FILE"),
            ssl_mode=_ssl_mode(env),
            pool_size=_positive_int(env.get("DATABASE_POOL_SIZE"), 5),
            max_overflow=_positive_int(env.get("DATABASE_MAX_OVERFLOW"), 5),
            pool_timeout_seconds=_positive_int(env.get("DATABASE_POOL_TIMEOUT_SECONDS"), 5),
            connect_timeout_seconds=_positive_int(env.get("DATABASE_CONNECT_TIMEOUT_SECONDS"), 5),
            statement_timeout_ms=_positive_int(env.get("DATABASE_STATEMENT_TIMEOUT_MS"), 30_000),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.url
            or (
                all((self.host, self.name, self.user))
                and (self.password is not None or self.password_file)
            )
        )

    def sqlalchemy_url(self) -> URL:
        if self.url:
            parsed = make_url(self.url)
            if parsed.get_backend_name() != "postgresql":
                raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL")
            if parsed.drivername in {"postgres", "postgresql"}:
                parsed = parsed.set(drivername="postgresql+asyncpg")
            if parsed.drivername != "postgresql+asyncpg":
                raise DatabaseConfigurationError("DATABASE_URL must use the asyncpg driver")
            parsed = parsed.difference_update_query(["ssl", "sslmode"])
            if self.ssl_mode == "require":
                parsed = parsed.update_query_dict({"ssl": "require"})
            return parsed

        if not self.configured:
            raise DatabaseConfigurationError("PostgreSQL connection settings are incomplete")
        if self.password is not None:
            password = self.password.strip()
        else:
            password_path = Path(str(self.password_file))
            try:
                if password_path.stat().st_size > 4096:
                    raise DatabaseConfigurationError(
                        "Database password file exceeds the safety bound"
                    )
                password = password_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise DatabaseConfigurationError(
                    "Database password file is unavailable"
                ) from exc
        if not password:
            raise DatabaseConfigurationError("Database password file is empty")
        url = URL.create(
            "postgresql+asyncpg",
            username=self.user,
            password=password,
            host=self.host,
            port=self.port,
            database=self.name,
        )
        if self.ssl_mode == "require":
            url = url.update_query_dict({"ssl": "require"})
        return url

    def raw_sqlalchemy_url(self) -> str:
        """Render a normal URL for direct SQLAlchemy/asyncpg consumption.

        The returned value is deliberately not escaped for ConfigParser. Callers
        crossing a real ConfigParser boundary must perform that escaping there.
        """
        return self.sqlalchemy_url().render_as_string(hide_password=False)


class DatabaseManager:
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings
        self._engine: Optional[AsyncEngine] = None
        self._health_engine: Optional[AsyncEngine] = None

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def engine(self) -> AsyncEngine:
        if self._engine is None:
            url = self.settings.sqlalchemy_url()
            connect_args = {
                "command_timeout": self.settings.statement_timeout_ms / 1000,
                "timeout": self.settings.connect_timeout_seconds,
                "server_settings": {
                    "application_name": "foundation-intelligence-api",
                    "statement_timeout": str(self.settings.statement_timeout_ms),
                },
            }
            self._engine = create_async_engine(
                url,
                pool_size=self.settings.pool_size,
                max_overflow=self.settings.max_overflow,
                pool_timeout=self.settings.pool_timeout_seconds,
                pool_recycle=1800,
                pool_pre_ping=True,
                connect_args=connect_args,
            )
        return self._engine

    def health_engine(self) -> AsyncEngine:
        """Use independent connections so analytical pool pressure cannot block health."""
        if self._health_engine is None:
            connect_args = {
                "command_timeout": min(self.settings.statement_timeout_ms / 1000, 5),
                "timeout": self.settings.connect_timeout_seconds,
                "server_settings": {
                    "application_name": "foundation-intelligence-health",
                    "statement_timeout": "5000",
                },
            }
            self._health_engine = create_async_engine(
                self.settings.sqlalchemy_url(),
                poolclass=NullPool,
                connect_args=connect_args,
            )
        return self._health_engine

    def sessions(self) -> async_sessionmaker[AsyncSession]:
        """Create sessions with explicit transactions and no implicit expiry."""
        return async_sessionmaker(
            self.engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def check(self) -> bool:
        if not self.configured:
            return False
        try:
            async with asyncio.timeout(self.settings.connect_timeout_seconds + 1):
                async with self.health_engine().connect() as connection:
                    result = await connection.execute(text("SELECT 1"))
                    return result.scalar_one() == 1
        except Exception as exc:
            logger.warning("PostgreSQL readiness check failed; class=%s", exc.__class__.__name__)
            return False

    async def readiness(
        self,
        *,
        expected_schema_version: str,
        require_critical_configuration: bool = True,
    ) -> dict[str, object]:
        unavailable = {
            "ready": False,
            "checks": {
                "postgresql": "unavailable",
                "schema_version": "unknown",
                "active_dataset": "unknown",
                "critical_configuration": "unknown",
                "queue": "unknown",
            },
        }
        if not self.configured:
            return unavailable
        try:
            async with asyncio.timeout(self.settings.connect_timeout_seconds + 1):
                async with self.health_engine().connect() as connection:
                    row = (
                        await connection.execute(
                            text(
                                """
                                SELECT
                                    (SELECT version_num FROM alembic_version) AS schema_version,
                                    (SELECT dataset_version FROM dataset_versions
                                     WHERE is_active AND status='active') AS active_dataset,
                                    (SELECT COUNT(*) FROM source_configurations) AS source_count,
                                    (SELECT COUNT(*) FROM retention_policies) AS policy_count,
                                    to_regclass('job_dispatch_outbox') IS NOT NULL AS queue_available,
                                    COALESCE((
                                        SELECT EXTRACT(EPOCH FROM (
                                            CURRENT_TIMESTAMP - MIN(created_at)
                                        )) FROM job_dispatch_outbox
                                        WHERE status IN ('pending', 'failed')
                                    ), 0) AS queue_age_seconds,
                                    (SELECT COUNT(*) FROM job_runs
                                     WHERE status='dead_lettered') AS dead_letter_count
                                """
                            )
                        )
                    ).mappings().one()
            schema_healthy = str(row["schema_version"]) == expected_schema_version
            dataset_healthy = bool(row["active_dataset"])
            configuration_healthy = int(row["source_count"]) > 0 and int(row["policy_count"]) > 0
            queue_healthy = bool(row["queue_available"])
            ready = (
                schema_healthy
                and dataset_healthy
                and queue_healthy
                and (configuration_healthy or not require_critical_configuration)
            )
            return {
                "ready": ready,
                "checks": {
                    "postgresql": "healthy",
                    "schema_version": "healthy" if schema_healthy else "mismatch",
                    "active_dataset": "healthy" if dataset_healthy else "missing",
                    "critical_configuration": (
                        "healthy"
                        if configuration_healthy
                        else (
                            "not_required"
                            if not require_critical_configuration
                            else "missing"
                        )
                    ),
                    "queue": "healthy" if queue_healthy else "unavailable",
                },
                "metadata": {
                    "schema_version": str(row["schema_version"]),
                    "active_dataset": str(row["active_dataset"] or ""),
                    "source_count": int(row["source_count"]),
                    "policy_count": int(row["policy_count"]),
                    "queue_age_seconds": float(row["queue_age_seconds"]),
                    "dead_letter_count": int(row["dead_letter_count"]),
                },
            }
        except Exception as exc:
            logger.warning(
                "PostgreSQL readiness contract failed",
                extra={"operation": "readiness", "error_class": exc.__class__.__name__},
            )
            return unavailable

    def pool_status(self) -> dict[str, float]:
        if self._engine is None:
            return {"checked_out": 0.0, "capacity": 0.0, "utilization_ratio": 0.0}
        pool = self._engine.sync_engine.pool
        checked_out = float(pool.checkedout()) if hasattr(pool, "checkedout") else 0.0
        size = float(pool.size()) if hasattr(pool, "size") else 0.0
        overflow = float(pool.overflow()) if hasattr(pool, "overflow") else 0.0
        capacity = max(size + max(overflow, 0.0), size, 1.0)
        return {
            "checked_out": checked_out,
            "capacity": capacity,
            "utilization_ratio": checked_out / capacity,
        }

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
        if self._health_engine is not None:
            await self._health_engine.dispose()
            self._health_engine = None
