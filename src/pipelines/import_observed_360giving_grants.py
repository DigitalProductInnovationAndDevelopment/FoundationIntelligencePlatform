"""Atomically append observed 360Giving grants to the active presentation DB.

The importer is intentionally append-only: an already stored grant ID is not
replaced, so importing a larger discovery sample cannot degrade enrichment or
source links from an existing record. It accepts the resumable publisher-pilot
JSON and can freeze an exact prefix of its publisher records for reproducible
imports.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from data import db_loader
from preprocessing.enrichment import enrich_grant
from pipelines.curate_europe_tech_grants import grant_id, iter_grants


def _source_data(record: Mapping[str, Any]) -> Mapping[str, Any]:
    data = record.get("data")
    return data if isinstance(data, Mapping) else record


def _first_organisation(data: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = data.get(field)
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0]
    if isinstance(value, Mapping):
        return value
    return {}


def _programme_titles(data: Mapping[str, Any]) -> list[str]:
    values = data.get("grantProgramme")
    if not isinstance(values, list):
        return []
    return [
        str(item.get("title") or item.get("name")).strip()
        for item in values
        if isinstance(item, Mapping) and (item.get("title") or item.get("name"))
    ]


def to_database_grant(record: Mapping[str, Any], ingestion_timestamp: str) -> dict[str, Any] | None:
    """Map one 360Giving API/Registry grant to the existing grant schema."""
    identifier = grant_id(record)
    if not identifier:
        return None
    data = _source_data(record)
    funder = _first_organisation(data, "fundingOrganization")
    recipient = _first_organisation(data, "recipientOrganization")
    raw_amount = data.get("amountAwarded")
    if raw_amount is None:
        raw_amount = data.get("amount")
    try:
        amount = float(raw_amount) if raw_amount is not None else None
    except (TypeError, ValueError):
        amount = None
    currency = str(data.get("currency") or "GBP").upper()
    publisher = record.get("publisher") if isinstance(record.get("publisher"), Mapping) else {}
    source_url = str(data.get("dataSource") or publisher.get("self") or "")
    mapped = {
        "grant_id": identifier,
        # The Registry pilot establishes observed grants, but it is not a
        # canonical organisation-directory import. Keep unmatched IDs null.
        "funding_charity_id": None,
        "funding_name": str(funder.get("name") or "Unknown donor"),
        "funding_org_source_id": funder.get("id"),
        "recipient_name": str(recipient.get("name") or "Unknown recipient"),
        "recipient_charity_id": None,
        "recipient_org_source_id": recipient.get("id") or recipient.get("charityNumber"),
        "amount": amount,
        "amount_eur": amount if currency == "EUR" else None,
        "exchange_rate": 1.0 if currency == "EUR" else None,
        "exchange_rate_date": None,
        "exchange_rate_source": "source currency is EUR" if currency == "EUR" else None,
        "conversion_status": "native_eur" if currency == "EUR" else None,
        "currency": currency,
        "description": str(data.get("description") or data.get("title") or ""),
        "date": str(data.get("awardDate") or data.get("date") or ""),
        "recipient_latitude": None,
        "recipient_longitude": None,
        "recipient_region": "",
        "beneficiary_geography": json.dumps(data.get("beneficiaryLocation") or [], ensure_ascii=False),
        "project_geography": json.dumps(data.get("projectLocation") or data.get("location") or [], ensure_ascii=False),
        "programme_area_source": json.dumps(_programme_titles(data), ensure_ascii=False),
        "source": "360Giving",
        "source_record_id": identifier,
        "source_url": source_url,
        "ingestion_timestamp": ingestion_timestamp,
        "raw_grant_data": dict(record),
    }
    mapped.update(enrich_grant(mapped))
    return mapped


def prepare_grants(payload: Any, publisher_record_limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """Return unique mapped grants and the number of raw grant records read."""
    if publisher_record_limit is not None and publisher_record_limit < 1:
        raise ValueError("publisher_record_limit must be at least 1 when provided")
    if publisher_record_limit is not None and isinstance(payload, Mapping) and isinstance(payload.get("records"), list):
        payload = {**payload, "records": payload["records"][:publisher_record_limit]}
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    unique: dict[str, dict[str, Any]] = {}
    raw_count = 0
    for raw_record in iter_grants(payload):
        raw_count += 1
        mapped = to_database_grant(raw_record, timestamp)
        if mapped and mapped["grant_id"] not in unique:
            unique[mapped["grant_id"]] = mapped
    return list(unique.values()), raw_count


def _existing_ids(connection: sqlite3.Connection, grant_ids: Iterable[str]) -> set[str]:
    identifiers = list(grant_ids)
    found: set[str] = set()
    for start in range(0, len(identifiers), 900):
        batch = identifiers[start:start + 900]
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"SELECT grant_id FROM grants WHERE grant_id IN ({placeholders})", batch
        ).fetchall()
        found.update(row[0] for row in rows)
    return found


def append_new_grants(connection: sqlite3.Connection, grants: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Insert only absent IDs, preserving current rows with the same grant ID."""
    records = [dict(grant) for grant in grants]
    existing = _existing_ids(connection, (record["grant_id"] for record in records))
    new_records = [record for record in records if record["grant_id"] not in existing]
    if new_records:
        db_loader.insert_grants(connection, new_records)
    return {"already_present": len(existing), "inserted": len(new_records)}


def select_random_new_grants(
    connection: sqlite3.Connection,
    grants: Iterable[Mapping[str, Any]],
    target_new_grants: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Choose an exact, reproducible sample from grant IDs absent in the active DB."""
    if target_new_grants < 1:
        raise ValueError("target_new_grants must be at least 1")
    records = [dict(grant) for grant in grants]
    existing = _existing_ids(connection, (record["grant_id"] for record in records))
    candidates = [record for record in records if record["grant_id"] not in existing]
    if target_new_grants > len(candidates):
        raise ValueError(
            f"Requested {target_new_grants} new grants but only {len(candidates)} unseen grant IDs are available"
        )
    return random.Random(seed).sample(candidates, target_new_grants), {
        "eligible_new_grants": len(candidates),
        "already_present_in_input": len(existing),
    }


def import_file(
    input_path: Path,
    database_path: Path,
    report_path: Path,
    publisher_record_limit: int | None = None,
    target_new_grants: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    valid, reason = db_loader.validate_database(str(database_path))
    if not valid:
        raise ValueError(f"Refusing to modify invalid active database: {reason}")
    with input_path.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    grants, raw_count = prepare_grants(payload, publisher_record_limit)

    staging_path: str | None = None
    connection: sqlite3.Connection | None = None
    try:
        staging_path, connection = db_loader.create_staging_database(
            str(database_path), preserve_existing=True
        )
        before_count = connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
        selection: dict[str, Any] = {}
        selected_grants = grants
        if target_new_grants is not None:
            if seed is None:
                raise ValueError("seed is required when target_new_grants is set")
            selected_grants, selection = select_random_new_grants(
                connection, grants, target_new_grants, seed
            )
        append_result = append_new_grants(connection, selected_grants)
        after_count = connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
        connection.close()
        connection = None
        db_loader.publish_staging_database(staging_path, str(database_path))
    except Exception:
        if connection is not None:
            connection.close()
        if staging_path and os.path.exists(staging_path):
            os.unlink(staging_path)
        raise

    report = {
        "dataset_profile": "360giving-registry-publisher-pilot-import-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_path": str(input_path),
        "publisher_record_limit": publisher_record_limit,
        "target_new_grants": target_new_grants,
        "selection_seed": seed if target_new_grants is not None else None,
        **selection,
        "raw_grant_records_read": raw_count,
        "unique_grant_ids": len(grants),
        "existing_grant_rows_before": before_count,
        **append_result,
        "active_grant_rows_after": after_count,
        "database_path": str(database_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Atomically append observed 360Giving publisher-pilot grants.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--database", type=Path, default=Path("src/data/charities.db"))
    parser.add_argument(
        "--publisher-record-limit", type=int, default=None,
        help="freeze the first N publisher records from a resumable pilot file",
    )
    parser.add_argument(
        "--target-new-grants", type=int, default=None,
        help="append exactly this many randomly selected, previously unseen grant IDs",
    )
    parser.add_argument(
        "--seed", type=int, default=20260724,
        help="stable selection seed used with --target-new-grants",
    )
    parser.add_argument(
        "--report", type=Path,
        default=Path("src/data/processed/360giving_registry_publisher_pilot_import.json"),
    )
    args = parser.parse_args()
    print(json.dumps(import_file(
        args.input, args.database, args.report, args.publisher_record_limit,
        args.target_new_grants, args.seed,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
