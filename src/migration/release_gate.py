"""Fail-closed PostgreSQL release/reconciliation gate for one-off ECS tasks."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from sqlalchemy import text

from bff.database import DatabaseManager, DatabaseSettings
from observability.metrics import load_observability_configuration


EXPECTED_SCHEMA_VERSION = load_observability_configuration().expected_schema_version


def evaluate_release_state(row: Mapping[str, Any]) -> dict[str, Any]:
    reconciliation = dict(row.get("reconciliation_results") or {})
    reconciliation_failures = sorted(
        name
        for name, result in reconciliation.items()
        if not isinstance(result, Mapping) or result.get("status") != "pass"
    )
    checks = {
        "schema": str(row.get("schema_version")) == EXPECTED_SCHEMA_VERSION,
        "single_active_dataset": int(row.get("active_dataset_count") or 0) == 1,
        "migration_active": row.get("migration_status") == "active",
        "reconciliation": bool(reconciliation) and not reconciliation_failures,
        "materialization": bool(row.get("materialization_active")),
        "active_quality_blocks": int(row.get("active_quality_blocks") or 0) == 0,
        "dead_letter_queue": int(row.get("dead_letter_count") or 0) == 0,
    }
    return {
        "ready": all(checks.values()),
        "dataset_version": str(row.get("dataset_version") or ""),
        "checks": checks,
        "reconciliation_failures": reconciliation_failures,
    }


async def release_state(database: DatabaseManager) -> dict[str, Any]:
    sessions = database.sessions()
    async with sessions() as session:
        row = (
            await session.execute(
                text(
                    """
                    WITH active AS (
                        SELECT dataset_version
                        FROM dataset_versions
                        WHERE is_active AND status='active'
                    )
                    SELECT
                        (SELECT version_num FROM alembic_version) AS schema_version,
                        (SELECT COUNT(*) FROM active) AS active_dataset_count,
                        (SELECT dataset_version FROM active) AS dataset_version,
                        migration.status AS migration_status,
                        migration.reconciliation_results,
                        EXISTS (
                            SELECT 1 FROM materialization_versions materialization
                            WHERE materialization.dataset_version=active.dataset_version
                              AND materialization.materialization_name='dashboard_analytics'
                              AND materialization.status='active'
                              AND materialization.is_active
                        ) AS materialization_active,
                        (
                            SELECT COUNT(*) FROM data_quality_issues quality
                            WHERE quality.dataset_version=active.dataset_version
                              AND quality.status IN ('open', 'quarantined')
                        ) AS active_quality_blocks,
                        (
                            SELECT COUNT(*) FROM job_runs
                            WHERE status='dead_lettered'
                        ) AS dead_letter_count
                    FROM active
                    LEFT JOIN migration_runs migration
                      ON migration.target_dataset_version=active.dataset_version
                    """
                )
            )
        ).mappings().one_or_none()
    if row is None:
        return evaluate_release_state({})
    return evaluate_release_state(row)


async def _main() -> int:
    database = DatabaseManager(DatabaseSettings.from_env())
    try:
        result = await release_state(database)
    finally:
        await database.close()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 1


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
