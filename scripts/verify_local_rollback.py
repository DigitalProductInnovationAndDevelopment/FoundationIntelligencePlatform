#!/usr/bin/env python3
"""Demonstrate reversible local dataset activation and restore the original target."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bff.database import DatabaseSettings
from migration.sqlite_to_postgres import rollback_dataset


TABLES = (
    "charities",
    "charity_registry_organizations",
    "grants",
    "grant_beneficiary_countries",
    "grant_programme_categories",
    "grant_overview_facts",
    "grant_source_funder_facts",
    "organization_registry_links",
)


async def _snapshot(connection: Any) -> dict[str, Any]:
    active = str(
        (
            await connection.execute(
                text(
                    "SELECT dataset_version FROM dataset_versions "
                    "WHERE is_active AND status='active'"
                )
            )
        ).scalar_one()
    )
    counts = {}
    for table in TABLES:
        counts[table] = int(
            (
                await connection.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE dataset_version=:version"),
                    {"version": active},
                )
            ).scalar_one()
        )
    materialization = bool(
        (
            await connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM materialization_versions
                        WHERE dataset_version=:version
                          AND materialization_name='dashboard_analytics'
                          AND is_active AND status='active'
                    )
                    """
                ),
                {"version": active},
            )
        ).scalar_one()
    )
    return {"dataset_version": active, "counts": counts, "materialization": materialization}


async def main() -> int:
    engine = create_async_engine(DatabaseSettings.from_env().sqlalchemy_url(), pool_pre_ping=True)
    original: str | None = None
    prior: str | None = None
    result: dict[str, Any] = {
        "status": "failed",
        "aws_actions_performed": False,
        "bidirectional_replay_performed": False,
    }
    try:
        async with engine.connect() as connection:
            active_jobs = int(
                (
                    await connection.execute(
                        text(
                            "SELECT COUNT(*) FROM job_runs "
                            "WHERE status IN ('queued','running','retrying')"
                        )
                    )
                ).scalar_one()
            )
            if active_jobs:
                raise RuntimeError("Local rollback proof requires all writers/jobs to be frozen")
            before = await _snapshot(connection)
            original = str(before["dataset_version"])
            prior_row = (
                await connection.execute(
                    text(
                        """
                        SELECT dataset_version FROM dataset_versions
                        WHERE dataset_version<>:current
                          AND status IN ('approved','rolled_back')
                        ORDER BY activated_at DESC NULLS LAST, created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"current": original},
                )
            ).scalar_one_or_none()
            if prior_row is None:
                raise RuntimeError("No approved prior dataset exists for rollback proof")
            prior = str(prior_row)

        to_prior = await rollback_dataset(prior)
        async with engine.connect() as connection:
            prior_snapshot = await _snapshot(connection)
        if prior_snapshot["dataset_version"] != prior:
            raise RuntimeError("Rollback did not activate the approved prior dataset")
        if not prior_snapshot["materialization"]:
            raise RuntimeError("Rollback target lacks an active dashboard materialization")
        if prior_snapshot["counts"] != before["counts"]:
            raise RuntimeError("Prior dataset count controls differ from the original dataset")

        to_original = await rollback_dataset(original)
        async with engine.connect() as connection:
            after = await _snapshot(connection)
        if after != before:
            raise RuntimeError("Original local dataset was not restored exactly")
        result.update(
            {
                "status": "passed",
                "writers_frozen": True,
                "original": original,
                "prior": prior,
                "rollback": to_prior,
                "restore": to_original,
                "active_after": after["dataset_version"],
                "counts": after["counts"],
                "materialization_active": after["materialization"],
            }
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        if original is not None:
            try:
                async with engine.connect() as connection:
                    active = (
                        await connection.execute(
                            text(
                                "SELECT dataset_version FROM dataset_versions "
                                "WHERE is_active AND status='active'"
                            )
                        )
                    ).scalar_one_or_none()
                if str(active or "") != original:
                    await rollback_dataset(original)
            finally:
                await engine.dispose()
        else:
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
