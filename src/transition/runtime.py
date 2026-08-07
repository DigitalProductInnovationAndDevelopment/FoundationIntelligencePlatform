"""Fail-closed configuration for the temporary storage transition."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
from typing import Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "runtime-transition.json"


class RuntimeMode(str, Enum):
    POSTGRESQL = "postgresql"
    SQLITE_MIGRATION_SOURCE = "sqlite_migration_source"
    SHADOW_COMPARE = "shadow_compare"


class TransitionConfigurationError(RuntimeError):
    """Raised when a transition mode could serve from an unsafe source."""


@dataclass(frozen=True)
class TransitionSettings:
    mode: RuntimeMode
    app_environment: str
    migration_source_path: Path
    shadow_sqlite_path: Optional[Path]
    maximum_pending_comparisons: int
    maximum_response_bytes: int
    shadow_timeout_seconds: float
    maximum_recorded_differences: int
    approved_unordered_paths: tuple[str, ...]
    ignored_operational_paths: tuple[str, ...]
    journeys: tuple[str, ...]

    @property
    def postgresql_authoritative(self) -> bool:
        return self.mode in {RuntimeMode.POSTGRESQL, RuntimeMode.SHADOW_COMPARE}

    @property
    def shadow_enabled(self) -> bool:
        return self.mode is RuntimeMode.SHADOW_COMPARE

    def validate(self) -> None:
        if self.app_environment in {"staging", "production"} and (
            self.mode is RuntimeMode.SQLITE_MIGRATION_SOURCE
        ):
            raise TransitionConfigurationError(
                "SQLite migration-source mode is forbidden in staging/production"
            )
        if self.shadow_enabled:
            if self.shadow_sqlite_path is None:
                raise TransitionConfigurationError(
                    "SHADOW_SQLITE_PATH is required for shadow comparison"
                )
            if not self.shadow_sqlite_path.is_file():
                raise TransitionConfigurationError("Shadow SQLite snapshot is unavailable")
            if self.shadow_sqlite_path.resolve() == self.migration_source_path.resolve():
                raise TransitionConfigurationError(
                    "Shadow comparison requires a separate coherent SQLite snapshot"
                )
        if self.maximum_pending_comparisons < 1:
            raise TransitionConfigurationError("Shadow queue bound must be positive")
        if self.maximum_response_bytes < 1024:
            raise TransitionConfigurationError("Shadow response bound is too small")
        if self.shadow_timeout_seconds <= 0:
            raise TransitionConfigurationError("Shadow timeout must be positive")
        if self.maximum_recorded_differences < 1:
            raise TransitionConfigurationError("Difference bound must be positive")


def load_transition_settings(
    environ: Optional[Mapping[str, str]] = None,
    path: Path = DEFAULT_CONFIG_PATH,
) -> TransitionSettings:
    env = os.environ if environ is None else environ
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("configuration_version") != "1":
        raise TransitionConfigurationError("Unsupported transition configuration version")
    app_environment = env.get("APP_ENV", "development").strip().lower()
    implicit_mode = str(payload["default_operational_mode"])
    try:
        mode = RuntimeMode(env.get("DATA_RUNTIME_MODE", implicit_mode).strip().lower())
    except ValueError as exc:
        raise TransitionConfigurationError("Unsupported DATA_RUNTIME_MODE") from exc
    shadow_path_value = env.get("SHADOW_SQLITE_PATH")
    shadow = payload["shadow"]
    settings = TransitionSettings(
        mode=mode,
        app_environment=app_environment,
        migration_source_path=Path(
            env.get("DB_PATH", str(PROJECT_ROOT / "src/data/charities.db"))
        ),
        shadow_sqlite_path=Path(shadow_path_value) if shadow_path_value else None,
        maximum_pending_comparisons=int(shadow["maximum_pending_comparisons"]),
        maximum_response_bytes=int(shadow["maximum_response_bytes"]),
        shadow_timeout_seconds=float(shadow["timeout_seconds"]),
        maximum_recorded_differences=int(shadow["maximum_recorded_differences"]),
        approved_unordered_paths=tuple(shadow["approved_unordered_paths"]),
        ignored_operational_paths=tuple(shadow["ignored_operational_paths"]),
        journeys=tuple(payload["journeys"]),
    )
    settings.validate()
    return settings
