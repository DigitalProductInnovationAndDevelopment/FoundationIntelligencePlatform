"""Deterministic read-only SQLite to versioned PostgreSQL migration command."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any, Callable, Iterable, Optional, Sequence
from urllib.parse import quote
import uuid

import asyncpg

from bff.database import DatabaseSettings
from observability.metrics import load_observability_configuration


GIB = 1024**3
MIGRATION_SCHEMA_VERSION = load_observability_configuration().expected_schema_version
EXPECTED_COUNTS = {
    "charities": 373,
    "charity_registry_organizations": 397_469,
    "grants": 302_546,
    "organization_registry_links": 345,
}
EXPECTED_CONTROLS = {
    "distinct_mapped_grants": 104_191,
    "classified_grants": 134_554,
    "overview_total_eur": "22435986707.70",
    "duplicate_source_identity_groups": 0,
    "missing_eur_conversions": 432,
    "negative_grants": 2,
    "zero_grants": 2_101,
    "future_dated_grants": 1,
    "business_key_duplicate_groups": 4_271,
    "duplicate_charity_number_groups": 9_073,
    "foreign_key_violations": 0,
}
VERSIONED_TABLES = {
    "charities",
    "charity_registry_organizations",
    "grants",
    "grant_beneficiary_countries",
    "grant_beneficiary_terms",
    "grant_programme_categories",
    "grant_overview_facts",
    "grant_source_funder_facts",
    "organization_registry_links",
    "source_funder_profile_cache",
}
LOAD_ORDER = (
    "charities",
    "charity_registry_organizations",
    "grants",
    "grant_beneficiary_countries",
    "grant_beneficiary_terms",
    "grant_programme_categories",
    "grant_overview_facts",
    "grant_source_funder_facts",
    "organization_registry_links",
    "source_funder_link_overrides",
    "source_funder_profile_cache",
    "exchange_rates",
)
GLOBAL_KEYS = {
    "source_funder_link_overrides": ("source_namespace", "source_organization_id"),
    "exchange_rates": ("currency", "rate_date"),
}
RENAMES = {
    ("charities", "raw_cc_data"): "raw_source_data",
    ("grants", "date"): "award_date",
}
JSON_COLUMNS = {
    ("charities", name)
    for name in (
        "raw_cc_data",
        "programme_areas_source",
        "programme_areas_inferred",
        "programme_area_scores",
        "programme_area_evidence",
        "geographic_focus_source",
        "geographic_focus_inferred",
        "geography_evidence",
        "enrichment_review_reasons",
        "source_names",
        "source_records",
        "deduplication_candidates",
    )
} | {
    ("grants", name)
    for name in (
        "tags",
        "raw_grant_data",
        "programme_area_scores",
        "programme_area_evidence",
        "geography_evidence",
        "enrichment_review_reasons",
    )
} | {("source_funder_profile_cache", "payload")}
BOOLEAN_COLUMNS = {
    ("charities", "programme_area_review_required"),
    ("charities", "geography_review_required"),
    ("charities", "insufficient_source_text"),
    ("charity_registry_organizations", "is_current_source_record"),
    ("grants", "programme_area_review_required"),
    ("grants", "geography_review_required"),
    ("grants", "insufficient_source_text"),
    ("grant_overview_facts", "invalid_source_label"),
    ("grant_overview_facts", "low_confidence_inference"),
}
DATE_COLUMNS = {
    ("charity_registry_organizations", name)
    for name in ("registration_date", "removal_date", "financial_period_end_date")
} | {("grant_overview_facts", "award_date")} | {
    ("grant_source_funder_facts", "award_date")
} | {("exchange_rates", "rate_date")}
TIMESTAMP_COLUMNS = {
    ("charities", "ingestion_timestamp"),
    ("charity_registry_organizations", "source_record_updated_at"),
    ("charity_registry_organizations", "imported_at"),
    ("grants", "ingestion_timestamp"),
    ("organization_registry_links", "reviewed_at"),
    ("organization_registry_links", "created_at"),
    ("organization_registry_links", "updated_at"),
    ("source_funder_link_overrides", "updated_at"),
    ("source_funder_profile_cache", "updated_at"),
    ("exchange_rates", "retrieved_at"),
}
NUMERIC_COLUMNS = {
    ("charities", name)
    for name in (
        "latitude",
        "longitude",
        "annual_income",
        "annual_expenditure",
        "geography_confidence",
    )
} | {
    ("charity_registry_organizations", name)
    for name in (
        "income",
        "expenditure",
        "registered_latitude",
        "registered_longitude",
    )
} | {
    ("grants", name)
    for name in (
        "amount",
        "amount_eur",
        "recipient_latitude",
        "recipient_longitude",
        "geography_confidence",
        "exchange_rate",
    )
} | {("organization_registry_links", "match_confidence")} | {
    ("exchange_rates", "eur_reference_rate")
}
UUID_COLUMNS = {("source_funder_profile_cache", "job_token")}
MONTH_COLUMNS = {("grants", "exchange_rate_date")}
RAW_DATE_COLUMNS = {("grants", "date")}
CLASSIFICATION_METHODS = {
    "deterministic_regex",
    "source_normalization",
    "source_normalization+deterministic_regex",
    "unavailable",
}
SOURCE_ID_COLUMNS = {
    "charities": ("charity_id",),
    "charity_registry_organizations": ("registry_id",),
    "grants": ("grant_id",),
    "grant_beneficiary_countries": ("grant_id", "country_code"),
    "grant_beneficiary_terms": ("grant_id", "term"),
    "grant_programme_categories": ("grant_id", "programme_area"),
    "grant_overview_facts": ("grant_id",),
    "grant_source_funder_facts": ("grant_id", "country_code"),
    "organization_registry_links": ("registry_id", "enriched_organization_id"),
    "source_funder_link_overrides": ("source_namespace", "source_organization_id"),
    "source_funder_profile_cache": ("source_funder_key",),
    "exchange_rates": ("currency", "rate_date"),
}


class MigrationError(RuntimeError):
    """Base class for a migration failure that must never activate data."""


class PreflightError(MigrationError):
    """Raised before PostgreSQL mutation when source or capacity is unsafe."""


class ValueConversionError(MigrationError):
    def __init__(self, column: str, value: Any, message: str):
        super().__init__(message)
        self.column = column
        self.value = value


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


@dataclass(frozen=True)
class CapacityEstimate:
    source_bytes: int
    estimated_postgres_and_indexes_bytes: int
    estimated_wal_and_temp_bytes: int
    safety_margin_bytes: int
    minimum_free_bytes: int
    available_bytes: int

    @property
    def sufficient(self) -> bool:
        return self.available_bytes >= self.minimum_free_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "sufficient": self.sufficient,
        }


@dataclass(frozen=True)
class SourcePreflight:
    checksum: str
    schema_version: str
    metadata: dict[str, str]
    counts: dict[str, int]
    capacity: CapacityEstimate


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def capacity_estimate(
    source_path: Path,
    *,
    remote_postgres: bool = False,
) -> CapacityEstimate:
    source_bytes = source_path.stat().st_size
    if remote_postgres:
        # An ECS migration task streams the read-only SQLite source to RDS. Its
        # local filesystem never stores PostgreSQL data or WAL, so retain a
        # bounded 4 GiB margin for the image, reports and SQLite temporary work.
        postgres_and_indexes = 0
        wal_and_temp = 0
        safety_margin = 4 * GIB
    else:
        postgres_and_indexes = source_bytes * 3
        wal_and_temp = source_bytes * 2
        safety_margin = 10 * GIB
    return CapacityEstimate(
        source_bytes=source_bytes,
        estimated_postgres_and_indexes_bytes=postgres_and_indexes,
        estimated_wal_and_temp_bytes=wal_and_temp,
        safety_margin_bytes=safety_margin,
        minimum_free_bytes=source_bytes + postgres_and_indexes + wal_and_temp + safety_margin,
        available_bytes=shutil.disk_usage(source_path.parent).free,
    )


def open_sqlite_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    uri = f"file:{quote(str(resolved))}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def source_preflight(
    source_path: Path,
    expected_checksum: str,
    expected_schema_version: str,
    *,
    enforce_capacity: bool = True,
    remote_postgres: bool = False,
) -> SourcePreflight:
    if not source_path.is_file():
        raise PreflightError("SQLite source does not exist")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_checksum):
        raise PreflightError("Expected checksum must be a lowercase SHA-256 value")
    actual_checksum = sha256_file(source_path)
    if actual_checksum != expected_checksum:
        raise PreflightError("SQLite source checksum does not match the approved value")
    capacity = capacity_estimate(source_path, remote_postgres=remote_postgres)
    if enforce_capacity and not capacity.sufficient:
        raise PreflightError(
            f"Insufficient disk capacity: requires {capacity.minimum_free_bytes} bytes, "
            f"has {capacity.available_bytes} bytes"
        )
    with open_sqlite_read_only(source_path) as source:
        integrity_rows = [row[0] for row in source.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            raise PreflightError("SQLite integrity_check did not return ok")
        metadata = {
            str(row[0]): str(row[1])
            for row in source.execute("SELECT key, value FROM metadata ORDER BY key")
        }
        schema_version = metadata.get("schema_version", "")
        if schema_version != expected_schema_version:
            raise PreflightError(
                f"SQLite schema version {schema_version!r} does not match "
                f"{expected_schema_version!r}"
            )
        source_tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
        missing = sorted(set(LOAD_ORDER) - source_tables)
        if missing:
            raise PreflightError(f"SQLite source is missing tables: {', '.join(missing)}")
        counts = {
            table: int(source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in LOAD_ORDER
        }
    return SourcePreflight(
        checksum=actual_checksum,
        schema_version=schema_version,
        metadata=metadata,
        counts=counts,
        capacity=capacity,
    )


def _json_value(table: str, column: str, value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        parsed = value
    else:
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueConversionError(column, value, "invalid JSON source value") from exc
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _date_value(column: str, value: Any) -> Optional[date]:
    if value is None or str(value).strip() == "":
        return None
    candidate = str(value).strip()[:10]
    try:
        return date.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueConversionError(column, value, "invalid ISO date source value") from exc


def _timestamp_value(column: str, value: Any) -> Optional[datetime]:
    if value is None or str(value).strip() == "":
        return None
    candidate = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueConversionError(column, value, "invalid ISO timestamp source value") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal_value(column: str, value: Any) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueConversionError(column, value, "invalid numeric source value") from exc
    if not parsed.is_finite():
        raise ValueConversionError(column, value, "non-finite numeric source value")
    return parsed


def _uuid_value(column: str, value: Any) -> Optional[uuid.UUID]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValueConversionError(column, value, "invalid UUID source value") from exc


def _month_value(column: str, value: Any) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    candidate = str(value).strip()
    try:
        date.fromisoformat(f"{candidate}-01")
    except ValueError as exc:
        raise ValueConversionError(column, value, "invalid ISO month source value") from exc
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", candidate):
        raise ValueConversionError(column, value, "invalid ISO month source value")
    return candidate


def _raw_date_value(column: str, value: Any) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    candidate = str(value)
    try:
        date.fromisoformat(candidate[:10])
    except ValueError as exc:
        raise ValueConversionError(column, value, "invalid ISO date source value") from exc
    return candidate


def convert_value(table: str, column: str, value: Any) -> Any:
    identity = (table, column)
    converted: Any
    if identity in JSON_COLUMNS:
        converted = _json_value(table, column, value)
    elif identity in BOOLEAN_COLUMNS:
        if value in (None, ""):
            converted = False
        else:
            if value not in (0, 1, False, True):
                raise ValueConversionError(column, value, "invalid boolean source value")
            converted = bool(value)
    elif identity in DATE_COLUMNS:
        converted = _date_value(column, value)
    elif identity in TIMESTAMP_COLUMNS:
        converted = _timestamp_value(column, value)
    elif identity in NUMERIC_COLUMNS:
        converted = _decimal_value(column, value)
    elif identity in UUID_COLUMNS:
        converted = _uuid_value(column, value)
    elif identity in MONTH_COLUMNS:
        converted = _month_value(column, value)
    elif identity in RAW_DATE_COLUMNS:
        converted = _raw_date_value(column, value)
    else:
        converted = value

    if converted is not None and column == "currency" and not re.fullmatch(
        r"[A-Z]{3}", str(converted)
    ):
        raise ValueConversionError(column, value, "invalid ISO-style currency code")
    if converted is not None and column in {"country_code", "origin_country_code"}:
        if not re.fullmatch(r"[A-Z]{2}", str(converted)):
            raise ValueConversionError(column, value, "invalid ISO-style country code")
    if column in {"programme_area_method", "geography_method"}:
        if converted is not None and converted not in CLASSIFICATION_METHODS:
            raise ValueConversionError(column, value, "unknown classification method")
    if column in {"geography_confidence", "match_confidence"} and converted is not None:
        if Decimal(converted) < 0 or Decimal(converted) > 1:
            raise ValueConversionError(column, value, "confidence outside [0,1]")
    return converted


def _source_identity(table: str, row: sqlite3.Row) -> str:
    return "|".join(str(row[column]) for column in SOURCE_ID_COLUMNS[table])


def _target_columns(table: str, source_columns: Sequence[str]) -> tuple[str, ...]:
    columns = tuple(RENAMES.get((table, column), column) for column in source_columns)
    return (("dataset_version",) + columns) if table in VERSIONED_TABLES else columns


def _converted_record(
    table: str,
    source_columns: Sequence[str],
    row: sqlite3.Row,
    dataset_version: str,
) -> tuple[Any, ...]:
    values = tuple(convert_value(table, column, row[column]) for column in source_columns)
    return ((dataset_version,) + values) if table in VERSIONED_TABLES else values


async def _connect_postgres(
    settings: DatabaseSettings | None = None,
) -> asyncpg.Connection:
    settings = settings or DatabaseSettings.from_env()
    url = settings.sqlalchemy_url()
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        ssl=settings.ssl_mode,
        command_timeout=None,
    )
    await connection.execute("SET statement_timeout = 0")
    await connection.execute("SET lock_timeout = '10s'")
    return connection


async def _ensure_schema(connection: asyncpg.Connection) -> None:
    revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    if revision != MIGRATION_SCHEMA_VERSION:
        raise PreflightError(
            f"PostgreSQL schema revision {revision!r} is not {MIGRATION_SCHEMA_VERSION!r}"
        )


async def _delete_candidate_payload(
    connection: asyncpg.Connection,
    dataset_version: str,
) -> None:
    for table in reversed(LOAD_ORDER):
        if table in VERSIONED_TABLES:
            await connection.execute(
                f'DELETE FROM "{table}" WHERE dataset_version=$1',
                dataset_version,
            )


async def _prepare_candidate(
    connection: asyncpg.Connection,
    preflight: SourcePreflight,
    dataset_version: str,
    migration_run_id: uuid.UUID,
    code_revision: str,
    actor_id: str,
    actor_type: str,
) -> str:
    existing = await connection.fetchrow(
        "SELECT status, is_active FROM dataset_versions WHERE dataset_version=$1",
        dataset_version,
    )
    if existing and existing["is_active"] and existing["status"] == "active":
        return "already_active"
    async with connection.transaction():
        if existing:
            await connection.execute(
                "DELETE FROM data_quality_issues WHERE dataset_version=$1",
                dataset_version,
            )
            await _delete_candidate_payload(connection, dataset_version)
            await connection.execute(
                "DELETE FROM migration_runs WHERE target_dataset_version=$1",
                dataset_version,
            )
            await connection.execute(
                "DELETE FROM dataset_versions WHERE dataset_version=$1",
                dataset_version,
            )
        previous = await connection.fetchval(
            "SELECT dataset_version FROM dataset_versions WHERE is_active"
        )
        await connection.execute(
            """
            INSERT INTO dataset_versions (
                dataset_version, status, is_active, source_checksum,
                source_schema_version, code_revision, previous_dataset_version,
                metadata
            ) VALUES ($1, 'loading', FALSE, $2, $3, $4, $5, $6::jsonb)
            """,
            dataset_version,
            preflight.checksum,
            preflight.schema_version,
            code_revision,
            previous,
            json.dumps({"source_metadata": preflight.metadata}, sort_keys=True),
        )
        await connection.execute(
            """
            INSERT INTO migration_runs (
                migration_run_id, target_dataset_version,
                source_database_checksum, source_schema_version,
                source_fact_version, target_schema_version, code_revision,
                status, source_counts, actor_id, actor_type
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, 'loading', $8::jsonb, $9, $10
            )
            """,
            migration_run_id,
            dataset_version,
            preflight.checksum,
            preflight.schema_version,
            preflight.metadata.get("grant_overview_schema_version", "unknown"),
            MIGRATION_SCHEMA_VERSION,
            code_revision,
            json.dumps(preflight.counts, sort_keys=True),
            actor_id,
            actor_type,
        )
    return "prepared"


async def _record_quality_issue(
    connection: asyncpg.Connection,
    dataset_version: str,
    migration_run_id: uuid.UUID,
    table: str,
    record_identity: str,
    error: ValueConversionError,
) -> None:
    await connection.execute(
        """
        INSERT INTO data_quality_issues (
            data_quality_issue_id, dataset_version, migration_run_id,
            source_table, source_record_id, field_name, original_value,
            reason, rule, status
        ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, 'quarantined')
        """,
        uuid.uuid4(),
        dataset_version,
        migration_run_id,
        table,
        record_identity,
        error.column,
        json.dumps(error.value, ensure_ascii=False),
        str(error),
        "target_type_and_constraint_validation",
    )


async def _upsert_global_records(
    connection: asyncpg.Connection,
    table: str,
    columns: Sequence[str],
    records: Sequence[tuple[Any, ...]],
) -> None:
    if not records:
        return
    key_columns = GLOBAL_KEYS[table]
    records_to_write = list(records)
    if table == "source_funder_link_overrides":
        column_list = ", ".join(f'"{column}"' for column in columns)
        key_indexes = tuple(columns.index(column) for column in key_columns)
        key_predicate = " AND ".join(
            f'"{column}"=${index}' for index, column in enumerate(key_columns, 1)
        )
        revision_index = columns.index("revision")
        records_to_write = []
        for record in records:
            existing = await connection.fetchrow(
                f'SELECT {column_list} FROM "{table}" WHERE {key_predicate}',
                *(record[index] for index in key_indexes),
            )
            if existing is None:
                records_to_write.append(record)
                continue
            existing_values = tuple(existing[column] for column in columns)
            if existing_values == record:
                continue
            if record[revision_index] != existing_values[revision_index] + 1:
                raise MigrationError(
                    "Conflicting source-funder override must increment revision "
                    "by exactly one"
                )
            records_to_write.append(record)
    if not records_to_write:
        return
    update_columns = tuple(column for column in columns if column not in key_columns)
    placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
    assignments = ", ".join(f'"{column}"=EXCLUDED."{column}"' for column in update_columns)
    sql = (
        f'INSERT INTO "{table}" ({", ".join(f"{column}" for column in columns)}) '
        f"VALUES ({placeholders}) ON CONFLICT ({', '.join(key_columns)}) "
        f"DO UPDATE SET {assignments}"
    )
    await connection.executemany(sql, records_to_write)


async def load_table(
    source: sqlite3.Connection,
    target: asyncpg.Connection,
    table: str,
    dataset_version: str,
    migration_run_id: uuid.UUID,
    batch_size: int,
    staged_global_records: dict[str, list[tuple[Any, ...]]],
    staged_global_columns: dict[str, tuple[str, ...]],
) -> int:
    table_info = source.execute(f'PRAGMA table_info("{table}")').fetchall()
    source_columns = tuple(str(row[1]) for row in table_info)
    primary_key_columns = tuple(
        str(row[1]) for row in sorted(table_info, key=lambda item: int(item[5])) if row[5]
    )
    order = ", ".join(f'"{column}"' for column in primary_key_columns)
    query = f'SELECT * FROM "{table}" ORDER BY {order}'
    cursor = source.execute(query)
    target_columns = _target_columns(table, source_columns)
    loaded = 0
    while rows := cursor.fetchmany(batch_size):
        records: list[tuple[Any, ...]] = []
        for row in rows:
            try:
                records.append(_converted_record(table, source_columns, row, dataset_version))
            except ValueConversionError as exc:
                await _record_quality_issue(
                    target,
                    dataset_version,
                    migration_run_id,
                    table,
                    _source_identity(table, row),
                    exc,
                )
                raise MigrationError(
                    f"Quarantined invalid value in {table}.{exc.column}; activation stopped"
                ) from exc
        if table in GLOBAL_KEYS:
            staged_global_columns.setdefault(table, target_columns)
            staged_global_records.setdefault(table, []).extend(records)
        else:
            async with target.transaction():
                await target.copy_records_to_table(
                    table,
                    records=records,
                    columns=target_columns,
                )
        loaded += len(records)
    return loaded


async def _target_counts(
    connection: asyncpg.Connection,
    dataset_version: str,
    loaded_counts: dict[str, int],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in LOAD_ORDER:
        if table in VERSIONED_TABLES:
            counts[table] = int(
                await connection.fetchval(
                    f'SELECT COUNT(*) FROM "{table}" WHERE dataset_version=$1',
                    dataset_version,
                )
            )
        else:
            counts[table] = loaded_counts[table]
    return counts


async def _control_values(
    connection: asyncpg.Connection,
    dataset_version: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    values["distinct_mapped_grants"] = int(
        await connection.fetchval(
            """
            SELECT COUNT(DISTINCT grant_id)
            FROM grant_beneficiary_countries WHERE dataset_version=$1
            """,
            dataset_version,
        )
    )
    values["classified_grants"] = int(
        await connection.fetchval(
            """
            SELECT COUNT(*) FROM grant_overview_facts
            WHERE dataset_version=$1
              AND programme_provenance <> 'unclassified'
              AND original_amount_status NOT IN ('negative', 'invalid', 'missing')
              AND eur_amount_status NOT IN ('missing', 'invalid')
            """,
            dataset_version,
        )
    )
    overview_minor = await connection.fetchval(
        """
        SELECT COALESCE(SUM(eur_amount_minor), 0)
        FROM grant_overview_facts
        WHERE dataset_version=$1
          AND original_amount_status NOT IN ('negative', 'invalid', 'missing')
          AND eur_amount_status NOT IN ('missing', 'invalid')
        """,
        dataset_version,
    )
    values["overview_total_eur"] = format(Decimal(overview_minor) / 100, ".2f")
    values["duplicate_source_identity_groups"] = int(
        await connection.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT source, source_record_id
                FROM grants
                WHERE dataset_version=$1 AND source IS NOT NULL
                  AND source_record_id IS NOT NULL
                GROUP BY source, source_record_id HAVING COUNT(*) > 1
            ) duplicates
            """,
            dataset_version,
        )
    )
    status_counts = await connection.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE eur_amount_status='missing') AS missing,
            COUNT(*) FILTER (WHERE original_amount_status='negative') AS negative,
            COUNT(*) FILTER (WHERE original_amount_status='zero') AS zero,
            COUNT(*) FILTER (WHERE award_date > CURRENT_DATE) AS future
        FROM grant_overview_facts WHERE dataset_version=$1
        """,
        dataset_version,
    )
    values["missing_eur_conversions"] = int(status_counts["missing"])
    values["negative_grants"] = int(status_counts["negative"])
    values["zero_grants"] = int(status_counts["zero"])
    values["future_dated_grants"] = int(status_counts["future"])
    values["business_key_duplicate_groups"] = int(
        await connection.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT funding_name, recipient_name, amount, currency,
                       award_date, description
                FROM grants WHERE dataset_version=$1
                GROUP BY funding_name, recipient_name, amount, currency,
                         award_date, description
                HAVING COUNT(*) > 1
            ) duplicates
            """,
            dataset_version,
        )
    )
    values["duplicate_charity_number_groups"] = int(
        await connection.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT charity_number
                FROM charity_registry_organizations WHERE dataset_version=$1
                GROUP BY charity_number HAVING COUNT(*) > 1
            ) duplicates
            """,
            dataset_version,
        )
    )
    constraints = await connection.fetch(
        """
        SELECT
            child_namespace.nspname AS child_schema,
            child_table.relname AS child_table,
            parent_namespace.nspname AS parent_schema,
            parent_table.relname AS parent_table,
            array_agg(child_column.attname ORDER BY child_key.ordinality) AS child_columns,
            array_agg(parent_column.attname ORDER BY child_key.ordinality) AS parent_columns
        FROM pg_constraint AS constraint_definition
        JOIN pg_class AS child_table
          ON child_table.oid=constraint_definition.conrelid
        JOIN pg_namespace AS child_namespace
          ON child_namespace.oid=child_table.relnamespace
        JOIN pg_class AS parent_table
          ON parent_table.oid=constraint_definition.confrelid
        JOIN pg_namespace AS parent_namespace
          ON parent_namespace.oid=parent_table.relnamespace
        JOIN LATERAL unnest(constraint_definition.conkey) WITH ORDINALITY
          AS child_key(attnum, ordinality) ON TRUE
        JOIN LATERAL unnest(constraint_definition.confkey) WITH ORDINALITY
          AS parent_key(attnum, ordinality)
          ON parent_key.ordinality=child_key.ordinality
        JOIN pg_attribute AS child_column
          ON child_column.attrelid=child_table.oid
         AND child_column.attnum=child_key.attnum
        JOIN pg_attribute AS parent_column
          ON parent_column.attrelid=parent_table.oid
         AND parent_column.attnum=parent_key.attnum
        WHERE constraint_definition.contype='f'
          AND child_namespace.nspname=current_schema()
        GROUP BY child_namespace.nspname, child_table.relname,
                 parent_namespace.nspname, parent_table.relname,
                 constraint_definition.conname
        ORDER BY child_table.relname, constraint_definition.conname
        """
    )
    foreign_key_violations = 0
    for constraint in constraints:
        child_table = ".".join(
            _quote_identifier(part)
            for part in (constraint["child_schema"], constraint["child_table"])
        )
        parent_table = ".".join(
            _quote_identifier(part)
            for part in (constraint["parent_schema"], constraint["parent_table"])
        )
        child_columns = [
            _quote_identifier(column)
            for column in constraint["child_columns"]
        ]
        parent_columns = [
            _quote_identifier(column)
            for column in constraint["parent_columns"]
        ]
        present = " AND ".join(f"child.{column} IS NOT NULL" for column in child_columns)
        matches = " AND ".join(
            f"parent.{parent} = child.{child}"
            for child, parent in zip(child_columns, parent_columns)
        )
        foreign_key_violations += int(
            await connection.fetchval(
                f"""
                SELECT COUNT(*) FROM {child_table} AS child
                WHERE {present}
                  AND NOT EXISTS (
                      SELECT 1 FROM {parent_table} AS parent WHERE {matches}
                  )
                """
            )
        )
    values["foreign_key_violations"] = foreign_key_violations
    return values


def _reconciliation(
    source_counts: dict[str, int],
    target_counts: dict[str, int],
    controls: dict[str, Any],
    expected_counts: Optional[dict[str, int]],
    expected_controls: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for table, source_count in source_counts.items():
        target_count = target_counts[table]
        results[f"count.{table}"] = {
            "status": "pass" if source_count == target_count else "fail",
            "source": source_count,
            "target": target_count,
            "difference": target_count - source_count,
            "reason": None,
            "approval_reference": None,
        }
    for name, expected in (expected_counts or {}).items():
        actual = target_counts.get(name)
        results[f"baseline_count.{name}"] = {
            "status": "pass" if actual == expected else "fail",
            "source": expected,
            "target": actual,
            "difference": (actual - expected) if actual is not None else None,
            "reason": None,
            "approval_reference": None,
        }
    for name, expected in (expected_controls or {}).items():
        actual = controls.get(name)
        results[f"control.{name}"] = {
            "status": "pass" if actual == expected else "fail",
            "source": expected,
            "target": actual,
            "difference": None,
            "reason": None,
            "approval_reference": None,
        }
    return results


async def _activate(
    connection: asyncpg.Connection,
    dataset_version: str,
    migration_run_id: uuid.UUID,
    counts: dict[str, int],
    reconciliation: dict[str, dict[str, Any]],
    staged_global_records: dict[str, list[tuple[Any, ...]]],
    staged_global_columns: dict[str, tuple[str, ...]],
) -> tuple[Optional[str], int]:
    failures = [name for name, result in reconciliation.items() if result["status"] == "fail"]
    if failures:
        await connection.execute(
            """
            UPDATE dataset_versions SET status='rejected', rejected_at=CURRENT_TIMESTAMP
            WHERE dataset_version=$1 AND NOT is_active
            """,
            dataset_version,
        )
        await connection.execute(
            """
            UPDATE migration_runs
            SET status='rejected', completed_at=CURRENT_TIMESTAMP,
                target_counts=$2::jsonb, reconciliation_results=$3::jsonb
            WHERE migration_run_id=$1
            """,
            migration_run_id,
            json.dumps(counts, sort_keys=True),
            json.dumps(reconciliation, sort_keys=True),
        )
        raise MigrationError(f"Reconciliation failed: {', '.join(failures)}")
    async with connection.transaction():
        for table in LOAD_ORDER:
            if table in GLOBAL_KEYS:
                records = staged_global_records.get(table, [])
                if records:
                    await _upsert_global_records(
                        connection,
                        table,
                        staged_global_columns[table],
                        records,
                    )
        await connection.fetchval(
            "SELECT refresh_analytics_materializations($1)", dataset_version
        )
        previous = await connection.fetchval(
            "SELECT dataset_version FROM dataset_versions WHERE is_active FOR UPDATE"
        )
        retargeted = await connection.execute(
            """
            UPDATE source_funder_link_overrides AS override
            SET target_dataset_version=$1, revision=override.revision + 1,
                updated_at=CURRENT_TIMESTAMP
            WHERE override.link_mode='link_profile'
              AND override.target_dataset_version<>$1
              AND EXISTS (
                  SELECT 1 FROM charities
                  WHERE dataset_version=$1
                    AND charity_id=override.target_profile_id
              )
            """,
            dataset_version,
        )
        if previous:
            await connection.execute(
                """
                UPDATE dataset_versions
                SET is_active=FALSE, status='rolled_back'
                WHERE dataset_version=$1
                """,
                previous,
            )
        await connection.execute(
            """
            UPDATE dataset_versions
            SET is_active=TRUE, status='active', approved_at=CURRENT_TIMESTAMP,
                activated_at=CURRENT_TIMESTAMP
            WHERE dataset_version=$1 AND NOT is_active
            """,
            dataset_version,
        )
        await connection.execute(
            """
            UPDATE migration_runs
            SET status='active', completed_at=CURRENT_TIMESTAMP,
                target_counts=$2::jsonb, reconciliation_results=$3::jsonb
            WHERE migration_run_id=$1
            """,
            migration_run_id,
            json.dumps(counts, sort_keys=True),
            json.dumps(reconciliation, sort_keys=True),
        )
    return previous, int(retargeted.rsplit(" ", 1)[-1])


async def migrate(
    source_path: Path,
    expected_checksum: str,
    expected_schema_version: str,
    dataset_version: str,
    code_revision: str,
    actor_id: str,
    actor_type: str,
    output_directory: Path,
    *,
    batch_size: int = 10_000,
    enforce_baseline: bool = True,
    enforce_capacity: bool = True,
    remote_postgres: bool = False,
    database_settings: DatabaseSettings | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{40}", code_revision):
        raise PreflightError("Code revision must be a full lowercase Git SHA")
    if not dataset_version or len(dataset_version) > 200:
        raise PreflightError("Dataset version must contain 1 to 200 characters")
    preflight = source_preflight(
        source_path,
        expected_checksum,
        expected_schema_version,
        enforce_capacity=enforce_capacity,
        remote_postgres=remote_postgres,
    )
    migration_run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"foundation-intelligence:{preflight.checksum}:{dataset_version}:{code_revision}",
    )
    target = await _connect_postgres(database_settings)
    started_at = datetime.now(timezone.utc)
    try:
        await _ensure_schema(target)
        preparation = await _prepare_candidate(
            target,
            preflight,
            dataset_version,
            migration_run_id,
            code_revision,
            actor_id,
            actor_type,
        )
        if preparation == "already_active":
            return {
                "migration_run_id": str(migration_run_id),
                "dataset_version": dataset_version,
                "activation_status": "active",
                "idempotent_noop": True,
            }
        staged_global_records: dict[str, list[tuple[Any, ...]]] = {}
        staged_global_columns: dict[str, tuple[str, ...]] = {}
        with open_sqlite_read_only(source_path) as source:
            loaded_counts = {}
            for table in LOAD_ORDER:
                loaded_counts[table] = await load_table(
                    source,
                    target,
                    table,
                    dataset_version,
                    migration_run_id,
                    batch_size,
                    staged_global_records,
                    staged_global_columns,
                )
        target_counts = await _target_counts(target, dataset_version, loaded_counts)
        controls = await _control_values(target, dataset_version)
        reconciliation = _reconciliation(
            preflight.counts,
            target_counts,
            controls,
            EXPECTED_COUNTS if enforce_baseline else None,
            EXPECTED_CONTROLS if enforce_baseline else None,
        )
        previous, retargeted_overrides = await _activate(
            target,
            dataset_version,
            migration_run_id,
            target_counts,
            reconciliation,
            staged_global_records,
            staged_global_columns,
        )
        completed_at = datetime.now(timezone.utc)
        report: dict[str, Any] = {
            "migration_run_id": str(migration_run_id),
            "source_database_checksum": preflight.checksum,
            "source_schema_version": preflight.schema_version,
            "source_fact_version": preflight.metadata.get(
                "grant_overview_schema_version", "unknown"
            ),
            "target_schema_version": MIGRATION_SCHEMA_VERSION,
            "dataset_version": dataset_version,
            "code_revision": code_revision,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "source_counts": preflight.counts,
            "target_counts": target_counts,
            "controls": controls,
            "reconciliation_results": reconciliation,
            "activation_status": "active",
            "rollback_dataset_version": previous,
            "retargeted_overrides": retargeted_overrides,
            "errors": [],
            "actor": {"id": actor_id, "type": actor_type},
            "capacity": preflight.capacity.as_dict(),
            "idempotent_noop": False,
        }
        write_reports(output_directory, report)
        return report
    except Exception as exc:
        try:
            await target.execute(
                """
                UPDATE migration_runs
                SET status='failed', completed_at=CURRENT_TIMESTAMP,
                    errors=$2::jsonb
                WHERE migration_run_id=$1 AND status NOT IN ('active', 'rejected')
                """,
                migration_run_id,
                json.dumps(
                    [{"code": exc.__class__.__name__, "message": str(exc)[:1000]}],
                    sort_keys=True,
                ),
            )
            await target.execute(
                """
                UPDATE dataset_versions SET status='failed'
                WHERE dataset_version=$1 AND NOT is_active
                  AND status NOT IN ('rejected', 'active')
                """,
                dataset_version,
            )
        except Exception:
            pass
        raise
    finally:
        await target.close()


async def rollback_dataset(target_dataset_version: str) -> dict[str, Optional[str]]:
    connection = await _connect_postgres()
    try:
        await _ensure_schema(connection)
        async with connection.transaction():
            current = await connection.fetchval(
                "SELECT dataset_version FROM dataset_versions WHERE is_active FOR UPDATE"
            )
            target = await connection.fetchrow(
                "SELECT status FROM dataset_versions WHERE dataset_version=$1 FOR UPDATE",
                target_dataset_version,
            )
            if not target:
                raise MigrationError("Rollback target dataset does not exist")
            if current == target_dataset_version:
                return {"from": current, "to": target_dataset_version}
            if target["status"] not in ("approved", "rolled_back"):
                raise MigrationError("Rollback target is not an approved prior dataset")
            materialized = await connection.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM materialization_versions
                    WHERE dataset_version=$1
                      AND materialization_name='dashboard_analytics'
                      AND is_active
                )
                """,
                target_dataset_version,
            )
            if not materialized:
                await connection.fetchval(
                    "SELECT refresh_analytics_materializations($1)",
                    target_dataset_version,
                )
            if current:
                await connection.execute(
                    """
                    UPDATE dataset_versions SET is_active=FALSE, status='rolled_back'
                    WHERE dataset_version=$1
                    """,
                    current,
                )
            await connection.execute(
                """
                UPDATE dataset_versions
                SET is_active=TRUE, status='active', activated_at=CURRENT_TIMESTAMP
                WHERE dataset_version=$1
                """,
                target_dataset_version,
            )
        return {"from": current, "to": target_dataset_version}
    finally:
        await connection.close()


def write_reports(output_directory: Path, report: dict[str, Any]) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    stem = f"migration-{report['dataset_version']}"
    json_path = output_directory / f"{stem}.json"
    markdown_path = output_directory / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    failures = [
        name
        for name, result in report["reconciliation_results"].items()
        if result["status"] != "pass"
    ]
    lines = [
        f"# Migration report: {report['dataset_version']}",
        "",
        f"- Run: `{report['migration_run_id']}`",
        f"- Source checksum: `{report['source_database_checksum']}`",
        f"- Target schema: `{report['target_schema_version']}`",
        f"- Activation: `{report['activation_status']}`",
        f"- Rollback dataset: `{report['rollback_dataset_version']}`",
        f"- Reconciliation failures: `{len(failures)}`",
        "",
        "## Counts",
        "",
        "| Table | Source | Target |",
        "|---|---:|---:|",
    ]
    for table in LOAD_ORDER:
        lines.append(
            f"| `{table}` | {report['source_counts'][table]} | "
            f"{report['target_counts'][table]} |"
        )
    lines.extend(["", "## Controls", "", "| Control | Value |", "|---|---:|"])
    for name, value in sorted(report["controls"].items()):
        lines.append(f"| `{name}` | {value} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    migrate_parser = subparsers.add_parser("migrate")
    for command_parser in (preflight_parser, migrate_parser):
        command_parser.add_argument("--source", type=Path, required=True)
        command_parser.add_argument("--expected-checksum", required=True)
        command_parser.add_argument("--expected-schema-version", required=True)
    migrate_parser.add_argument("--dataset-version", required=True)
    migrate_parser.add_argument("--code-revision", required=True)
    migrate_parser.add_argument("--actor-id", required=True)
    migrate_parser.add_argument(
        "--actor-type", choices=("human", "service", "ci"), required=True
    )
    migrate_parser.add_argument("--output-directory", type=Path, required=True)
    migrate_parser.add_argument("--batch-size", type=int, default=10_000)
    migrate_parser.add_argument("--fixture", action="store_true")
    migrate_parser.add_argument(
        "--remote-postgres-capacity",
        action="store_true",
        help=(
            "Size local capacity for a read-only SQLite source streamed to remote "
            "PostgreSQL; PostgreSQL data and WAL are not counted as local files."
        ),
    )
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--dataset-version", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "preflight":
            result = source_preflight(
                arguments.source,
                arguments.expected_checksum,
                arguments.expected_schema_version,
            )
            print(json.dumps({
                "checksum": result.checksum,
                "schema_version": result.schema_version,
                "counts": result.counts,
                "capacity": result.capacity.as_dict(),
            }, indent=2, sort_keys=True))
            return 0
        if arguments.command == "rollback":
            print(json.dumps(asyncio.run(rollback_dataset(arguments.dataset_version))))
            return 0
        if arguments.batch_size < 1 or arguments.batch_size > 100_000:
            raise PreflightError("Batch size must be between 1 and 100000")
        report = asyncio.run(
            migrate(
                arguments.source,
                arguments.expected_checksum,
                arguments.expected_schema_version,
                arguments.dataset_version,
                arguments.code_revision,
                arguments.actor_id,
                arguments.actor_type,
                arguments.output_directory,
                batch_size=arguments.batch_size,
                enforce_baseline=not arguments.fixture,
                enforce_capacity=not arguments.fixture,
                remote_postgres=arguments.remote_postgres_capacity,
            )
        )
        print(json.dumps({
            "migration_run_id": report["migration_run_id"],
            "dataset_version": report["dataset_version"],
            "activation_status": report["activation_status"],
            "idempotent_noop": report.get("idempotent_noop", False),
        }, sort_keys=True))
        return 0
    except MigrationError as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
