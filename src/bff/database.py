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

from bff.utils.logging import logger


class DatabaseConfigurationError(RuntimeError):
    """Raised when PostgreSQL configuration is incomplete or unsafe."""


def _positive_int(value: Optional[str], default: int) -> int:
    parsed = int(value) if value is not None else default
    if parsed <= 0:
        raise DatabaseConfigurationError("Database numeric settings must be positive")
    return parsed


@dataclass(frozen=True)
class DatabaseSettings:
    url: Optional[str]
    host: Optional[str]
    port: int
    name: Optional[str]
    user: Optional[str]
    password_file: Optional[str]
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
            password_file=env.get("DATABASE_PASSWORD_FILE"),
            pool_size=_positive_int(env.get("DATABASE_POOL_SIZE"), 5),
            max_overflow=_positive_int(env.get("DATABASE_MAX_OVERFLOW"), 5),
            pool_timeout_seconds=_positive_int(env.get("DATABASE_POOL_TIMEOUT_SECONDS"), 5),
            connect_timeout_seconds=_positive_int(env.get("DATABASE_CONNECT_TIMEOUT_SECONDS"), 5),
            statement_timeout_ms=_positive_int(env.get("DATABASE_STATEMENT_TIMEOUT_MS"), 30_000),
        )

    @property
    def configured(self) -> bool:
        return bool(self.url or all((self.host, self.name, self.user, self.password_file)))

    def sqlalchemy_url(self) -> URL:
        if self.url:
            parsed = make_url(self.url)
            if parsed.get_backend_name() != "postgresql":
                raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL")
            if parsed.drivername in {"postgres", "postgresql"}:
                parsed = parsed.set(drivername="postgresql+asyncpg")
            if parsed.drivername != "postgresql+asyncpg":
                raise DatabaseConfigurationError("DATABASE_URL must use the asyncpg driver")
            return parsed

        if not self.configured:
            raise DatabaseConfigurationError("PostgreSQL connection settings are incomplete")
        password_path = Path(str(self.password_file))
        try:
            if password_path.stat().st_size > 4096:
                raise DatabaseConfigurationError("Database password file exceeds the safety bound")
            password = password_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DatabaseConfigurationError("Database password file is unavailable") from exc
        if not password:
            raise DatabaseConfigurationError("Database password file is empty")
        return URL.create(
            "postgresql+asyncpg",
            username=self.user,
            password=password,
            host=self.host,
            port=self.port,
            database=self.name,
        )


class DatabaseManager:
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings
        self._engine: Optional[AsyncEngine] = None

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def engine(self) -> AsyncEngine:
        if self._engine is None:
            url = self.settings.sqlalchemy_url()
            self._engine = create_async_engine(
                url,
                pool_size=self.settings.pool_size,
                max_overflow=self.settings.max_overflow,
                pool_timeout=self.settings.pool_timeout_seconds,
                pool_recycle=1800,
                pool_pre_ping=True,
                connect_args={
                    "command_timeout": self.settings.statement_timeout_ms / 1000,
                    "timeout": self.settings.connect_timeout_seconds,
                    "server_settings": {
                        "application_name": "foundation-intelligence-api",
                        "statement_timeout": str(self.settings.statement_timeout_ms),
                    },
                },
            )
        return self._engine

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
                async with self.engine().connect() as connection:
                    result = await connection.execute(text("SELECT 1"))
                    return result.scalar_one() == 1
        except Exception as exc:
            logger.warning("PostgreSQL readiness check failed; class=%s", exc.__class__.__name__)
            return False

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
