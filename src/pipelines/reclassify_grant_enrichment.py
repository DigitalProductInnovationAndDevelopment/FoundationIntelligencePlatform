"""Atomically reclassify stored grant enrichment with the active taxonomy.

Raw 360Giving facts remain untouched.  The pipeline updates only derived,
traceable enrichment fields and rebuilds the derived Overview indexes before a
validated staging database is atomically published.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from bff.repositories import rebuild_grant_overview_indexes
from data import db_loader
from preprocessing.enrichment import RULE_VERSION, enrich_grant


DEFAULT_SOURCES = ["360Giving", "Charity Commission for England and Wales", "Philea"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _grant_enrichment(row: Mapping[str, Any]) -> dict[str, Any]:
    """Run enrichment from stored source fields without changing source facts."""
    return enrich_grant({
        "description": row["description"],
        "programme_area_source": row["programme_area_source"],
        "beneficiary_geography": row["beneficiary_geography"],
    })


def _update_rows(connection: sqlite3.Connection) -> tuple[int, int]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT grant_id, description, programme_area_source, beneficiary_geography,
               programme_area_inferred
        FROM grants
        ORDER BY grant_id
        """
    )
    update_sql = """
        UPDATE grants
        SET programme_area_inferred = ?, programme_area_scores = ?,
            programme_area_method = ?, programme_area_evidence = ?,
            programme_area_review_required = ?,
            beneficiary_geography_normalized = ?, geographic_focus_inferred = ?,
            geography_method = ?, geography_confidence = ?, geography_evidence = ?,
            geography_review_required = ?, enrichment_rule_version = ?,
            enrichment_review_reasons = ?, insufficient_source_text = ?
        WHERE grant_id = ?
    """
    updates: list[tuple[Any, ...]] = []
    grant_count = 0
    programme_changed = 0
    for row in rows:
        enrichment = _grant_enrichment(row)
        programme_inferred = _json(enrichment["programme_area_inferred"])
        if programme_inferred != str(row["programme_area_inferred"] or "[]"):
            programme_changed += 1
        updates.append((
            programme_inferred,
            _json(enrichment["programme_area_scores"]),
            enrichment["programme_area_method"],
            _json(enrichment["programme_area_evidence"]),
            int(bool(enrichment["programme_area_review_required"])),
            _json(enrichment["beneficiary_geography_normalized"]),
            _json(enrichment["geographic_focus_inferred"]),
            enrichment["geography_method"],
            enrichment["geography_confidence"],
            _json(enrichment["geography_evidence"]),
            int(bool(enrichment["geography_review_required"])),
            enrichment["enrichment_rule_version"],
            _json(enrichment["enrichment_review_reasons"]),
            int(bool(enrichment["insufficient_source_text"])),
            row["grant_id"],
        ))
        grant_count += 1
        if len(updates) >= 2_000:
            connection.executemany(update_sql, updates)
            updates.clear()
    if updates:
        connection.executemany(update_sql, updates)
    connection.commit()
    return grant_count, programme_changed


def _tech_grant_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute(
        "SELECT COUNT(DISTINCT grant_id) FROM grant_programme_categories WHERE programme_area = ?",
        ("tech-enablement",),
    ).fetchone()[0])


def _prewarm_default_overview(database_path: Path) -> int:
    from bff.repositories import SQLiteCharityRepository

    repository = SQLiteCharityRepository(str(database_path))
    payload = asyncio.run(repository.get_grant_overview(sources=DEFAULT_SOURCES))
    return int(payload.get("kpis", {}).get("grants_monitored") or 0)


def reclassify_database(
    database_path: Path,
    report_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply active deterministic enrichment to every stored grant safely."""
    valid, reason = db_loader.validate_database(str(database_path))
    if not valid:
        raise ValueError(f"Refusing to modify invalid active database: {reason}")

    staging_path: str | None = None
    connection: sqlite3.Connection | None = None
    created_at = _utc_now()
    try:
        staging_path, connection = db_loader.create_staging_database(
            str(database_path), preserve_existing=True
        )
        tech_before = _tech_grant_count(connection)
        grants_reclassified, programme_changed = _update_rows(connection)
        index_result = rebuild_grant_overview_indexes(connection)
        tech_after = _tech_grant_count(connection)
        connection.close()
        connection = None
        if not dry_run:
            db_loader.publish_staging_database(staging_path, str(database_path))
            staging_path = None
    except Exception:
        if connection is not None:
            connection.close()
        if staging_path and os.path.exists(staging_path):
            os.unlink(staging_path)
        raise

    if staging_path and os.path.exists(staging_path):
        os.unlink(staging_path)

    prewarmed_grants = _prewarm_default_overview(database_path) if not dry_run else 0
    report = {
        "dataset_profile": "deterministic-grant-enrichment-reclassification-v1",
        "created_at": created_at,
        "database_path": str(database_path),
        "rule_version": RULE_VERSION,
        "dry_run": dry_run,
        "grants_reclassified": grants_reclassified,
        "programme_classifications_changed": programme_changed,
        "tech_enablement_grants_before": tech_before,
        "tech_enablement_grants_after": tech_after,
        "tech_enablement_grants_added": tech_after - tech_before,
        "overview_indexes": index_result,
        "overview_cache_prewarmed_grants": prewarmed_grants,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify stored grants with the active deterministic enrichment rules."
    )
    parser.add_argument("--database", type=Path, default=Path("src/data/charities.db"))
    parser.add_argument(
        "--report", type=Path,
        default=Path("src/data/processed/grant_enrichment_reclassification_report.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        reclassify_database(args.database, args.report, dry_run=args.dry_run),
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
