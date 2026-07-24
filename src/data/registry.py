"""Scalable Charity Commission registry storage and import utilities.

The registry is intentionally separate from the enriched ``charities`` table.  A
registry row describes an official registration record; it does not imply grant
activity, a beneficiary location, or an enriched organization profile.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


REGISTRY_TABLE = "charity_registry_organizations"
REGISTRY_LINK_TABLE = "organization_registry_links"
REGISTRY_FTS_TABLE = "charity_registry_fts"
REGISTRY_SOURCE_NAME = "Charity Commission for England and Wales"
DEFAULT_SOURCE_PATH = Path(__file__).resolve().parent / "raw" / "charity_commission_bulk" / "extracted" / "publicextract.charity.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_organization_name(value: Any) -> str:
    """Return a conservative, search-safe organization-name representation.

    Legal suffixes are removed only when they are trailing standalone tokens.  The
    resulting value is suitable for search and conservative exact-name matching;
    it is never used by itself as authority for a grant or score relationship.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[\u2010-\u2015/_.,;:()\[\]{}'\"`]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    suffixes = (
        "charitable incorporated organisation",
        "charitable incorporated organization",
        "community interest company",
        "limited liability partnership",
        "company limited by guarantee",
        "limited",
        "ltd",
        "plc",
        "cio",
        "cic",
    )
    for suffix in suffixes:
        if text.endswith(f" {suffix}"):
            text = text[: -len(suffix)].strip()
            break
    return text


def _fts_available(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (REGISTRY_FTS_TABLE,),
        ).fetchone()
    )


def migrate_registry_schema(conn: sqlite3.Connection, synchronize_fts: bool = False) -> bool:
    """Create the registry layer, link layer, indexes and FTS when supported.

    The migration is additive and safe to run repeatedly on the existing
    development database. ``synchronize_fts`` should be used by an explicit
    migration command; normal application startup relies on FTS triggers and
    avoids a full-table consistency scan. It returns whether FTS5 is available
    for queries.
    """
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {REGISTRY_TABLE} (
            registry_id TEXT PRIMARY KEY,
            charity_number TEXT NOT NULL,
            linked_charity_number TEXT,
            registered_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            registration_status TEXT,
            registration_date TEXT,
            removal_date TEXT,
            income REAL,
            expenditure REAL,
            financial_period_end_date TEXT,
            address_line_one TEXT,
            address_line_two TEXT,
            address_line_three TEXT,
            address_line_four TEXT,
            address_line_five TEXT,
            postcode TEXT,
            city TEXT,
            administrative_region TEXT,
            country_code TEXT,
            registered_latitude REAL,
            registered_longitude REAL,
            activity_text TEXT,
            source_name TEXT NOT NULL,
            source_record_updated_at TEXT,
            imported_at TEXT NOT NULL,
            is_current_source_record INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {REGISTRY_LINK_TABLE} (
            registry_id TEXT NOT NULL,
            enriched_organization_id INTEGER NOT NULL,
            match_status TEXT NOT NULL CHECK(match_status IN ('accepted', 'review_required', 'rejected', 'unmatched')),
            match_method TEXT NOT NULL,
            match_confidence REAL,
            match_reason TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (registry_id, enriched_organization_id),
            FOREIGN KEY (registry_id) REFERENCES {REGISTRY_TABLE}(registry_id),
            FOREIGN KEY (enriched_organization_id) REFERENCES charities(charity_id)
        )
        """
    )
    indexes = (
        f"CREATE INDEX IF NOT EXISTS idx_registry_charity_number ON {REGISTRY_TABLE}(charity_number)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_linked_charity_number ON {REGISTRY_TABLE}(linked_charity_number)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_normalized_name ON {REGISTRY_TABLE}(normalized_name, registry_id)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_status ON {REGISTRY_TABLE}(registration_status, registry_id)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_income ON {REGISTRY_TABLE}(income DESC, registry_id)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_expenditure ON {REGISTRY_TABLE}(expenditure DESC, registry_id)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_status_income ON {REGISTRY_TABLE}(registration_status, income DESC, registry_id)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_status_expenditure ON {REGISTRY_TABLE}(registration_status, expenditure DESC, registry_id)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_country_region ON {REGISTRY_TABLE}(country_code, administrative_region, registry_id)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_postcode ON {REGISTRY_TABLE}(postcode)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_links_profile ON {REGISTRY_LINK_TABLE}(enriched_organization_id, match_status)",
        f"CREATE INDEX IF NOT EXISTS idx_registry_links_status ON {REGISTRY_LINK_TABLE}(registry_id, match_status)",
    )
    for statement in indexes:
        conn.execute(statement)

    fts_ready = _fts_available(conn)
    fts_created = False
    if not fts_ready:
        try:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE {REGISTRY_FTS_TABLE}
                USING fts5(registry_id UNINDEXED, registered_name, normalized_name,
                           tokenize='unicode61 remove_diacritics 2')
                """
            )
            fts_ready = True
            fts_created = True
        except sqlite3.OperationalError:
            # FTS5 is optional. The repository falls back to indexed normalized-name
            # prefix search rather than an unbounded LIKE scan.
            fts_ready = False

    if fts_ready:
        conn.executescript(
            f"""
            CREATE TRIGGER IF NOT EXISTS charity_registry_ai
            AFTER INSERT ON {REGISTRY_TABLE}
            BEGIN
              INSERT INTO {REGISTRY_FTS_TABLE}(rowid, registry_id, registered_name, normalized_name)
              VALUES (new.rowid, new.registry_id, new.registered_name, new.normalized_name);
            END;

            CREATE TRIGGER IF NOT EXISTS charity_registry_ad
            AFTER DELETE ON {REGISTRY_TABLE}
            BEGIN
              DELETE FROM {REGISTRY_FTS_TABLE} WHERE rowid = old.rowid;
            END;

            CREATE TRIGGER IF NOT EXISTS charity_registry_au
            AFTER UPDATE OF registry_id, registered_name, normalized_name ON {REGISTRY_TABLE}
            BEGIN
              DELETE FROM {REGISTRY_FTS_TABLE} WHERE rowid = old.rowid;
              INSERT INTO {REGISTRY_FTS_TABLE}(rowid, registry_id, registered_name, normalized_name)
              VALUES (new.rowid, new.registry_id, new.registered_name, new.normalized_name);
            END;
            """
        )
        if synchronize_fts or fts_created:
            fts_count = conn.execute(f"SELECT COUNT(*) FROM {REGISTRY_FTS_TABLE}").fetchone()[0]
            registry_count = conn.execute(f"SELECT COUNT(*) FROM {REGISTRY_TABLE}").fetchone()[0]
            if fts_count != registry_count:
                conn.execute(f"DELETE FROM {REGISTRY_FTS_TABLE}")
                conn.execute(
                    f"""
                    INSERT INTO {REGISTRY_FTS_TABLE}(rowid, registry_id, registered_name, normalized_name)
                    SELECT rowid, registry_id, registered_name, normalized_name
                    FROM {REGISTRY_TABLE}
                    """
                )

    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("registry_schema_version", "1"),
    )
    conn.commit()
    return fts_ready


def migrate_registry_database(db_path: str) -> bool:
    """Run the additive registry migration against a database path."""
    conn = sqlite3.connect(db_path)
    try:
        return migrate_registry_schema(conn, synchronize_fts=True)
    finally:
        conn.close()


def iter_json_array(path: os.PathLike[str] | str, chunk_size: int = 1024 * 1024) -> Iterator[Dict[str, Any]]:
    """Stream a JSON array without loading the Charity Commission bulk file in RAM."""
    decoder = json.JSONDecoder()
    buffer = ""
    eof = False
    started = False
    source = Path(path)
    # The official daily extract is UTF-8 and currently begins with a BOM.
    with source.open("r", encoding="utf-8-sig") as handle:
        while True:
            if not eof and len(buffer) < chunk_size:
                chunk = handle.read(chunk_size)
                if chunk:
                    buffer += chunk
                else:
                    eof = True

            stripped = buffer.lstrip()
            if len(stripped) != len(buffer):
                buffer = stripped
            if not buffer:
                if eof:
                    break
                continue
            if not started:
                if buffer[0] != "[":
                    raise ValueError(f"Expected a JSON array in {source}")
                buffer = buffer[1:]
                started = True
                continue
            if buffer[0] == ",":
                buffer = buffer[1:]
                continue
            if buffer[0] == "]":
                return
            try:
                record, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError as exc:
                if eof:
                    raise ValueError(f"Malformed JSON in {source}: {exc.msg}") from exc
                continue
            if not isinstance(record, dict):
                raise ValueError(f"Expected object records in {source}, found {type(record).__name__}")
            buffer = buffer[end:]
            yield record


def _text(record: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _number(record: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = record.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def registry_row_from_source(record: Dict[str, Any], imported_at: str) -> Optional[Tuple[Any, ...]]:
    """Convert an official bulk-export record to a lightweight registry row."""
    organization_number = _text(record, "organisation_number", "organization_number")
    charity_number = _text(record, "registered_charity_number", "reg_charity_number")
    name = _text(record, "charity_name", "registered_name")
    if not organization_number or not charity_number or not name:
        return None
    registry_id = f"cc:{organization_number}"
    return (
        registry_id,
        charity_number,
        _text(record, "linked_charity_number"),
        name,
        normalize_organization_name(name),
        _text(record, "charity_registration_status", "reg_status", "registration_status"),
        _text(record, "date_of_registration", "registration_date"),
        _text(record, "date_of_removal", "removal_date"),
        _number(record, "latest_income", "income"),
        _number(record, "latest_expenditure", "expenditure"),
        _text(record, "latest_acc_fin_period_end_date", "financial_period_end_date"),
        _text(record, "charity_contact_address1", "address_line_one"),
        _text(record, "charity_contact_address2", "address_line_two"),
        _text(record, "charity_contact_address3", "address_line_three"),
        _text(record, "charity_contact_address4", "address_line_four"),
        _text(record, "charity_contact_address5", "address_line_five"),
        _text(record, "charity_contact_postcode", "address_post_code", "postcode"),
        _text(record, "charity_contact_address4", "city", "locality"),
        _text(record, "charity_contact_address5", "administrative_region", "region"),
        "GB",
        _number(record, "registered_latitude", "latitude"),
        _number(record, "registered_longitude", "longitude"),
        _text(record, "charity_activities", "activity_text", "charitable_purposes"),
        REGISTRY_SOURCE_NAME,
        _text(record, "date_of_extract", "last_modified_time", "updated_at"),
        imported_at,
        1,
    )


REGISTRY_COLUMNS = (
    "registry_id", "charity_number", "linked_charity_number", "registered_name", "normalized_name",
    "registration_status", "registration_date", "removal_date", "income", "expenditure",
    "financial_period_end_date", "address_line_one", "address_line_two", "address_line_three",
    "address_line_four", "address_line_five", "postcode", "city", "administrative_region",
    "country_code", "registered_latitude", "registered_longitude", "activity_text", "source_name",
    "source_record_updated_at", "imported_at", "is_current_source_record",
)


def _write_batch(conn: sqlite3.Connection, rows: Sequence[Tuple[Any, ...]]) -> Tuple[int, int]:
    if not rows:
        return 0, 0
    ids = [row[0] for row in rows]
    placeholders = ",".join("?" for _ in ids)
    existing = {
        row[0]
        for row in conn.execute(
            f"SELECT registry_id FROM {REGISTRY_TABLE} WHERE registry_id IN ({placeholders})", ids
        )
    }
    assignments = ", ".join(f"{column}=excluded.{column}" for column in REGISTRY_COLUMNS[1:])
    placeholders = ", ".join("?" for _ in REGISTRY_COLUMNS)
    conn.executemany(
        f"""
        INSERT INTO {REGISTRY_TABLE} ({', '.join(REGISTRY_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT(registry_id) DO UPDATE SET {assignments}
        """,
        rows,
    )
    return len(rows) - len(existing), len(existing)


def refresh_exact_registry_links(conn: sqlite3.Connection) -> Dict[str, int]:
    """Create only high-confidence identifier links to enriched profiles.

    No fuzzy or name-only link is accepted here. Those profiles remain deliberately
    unmatched until a reviewed/manual matching workflow is added.
    """
    now = utc_now()
    conn.execute(
        f"""
        INSERT INTO {REGISTRY_LINK_TABLE} (
            registry_id, enriched_organization_id, match_status, match_method,
            match_confidence, match_reason, reviewed_at, created_at, updated_at
        )
        SELECT registry.registry_id, enriched.charity_id, 'accepted', 'exact_identifier',
               1.0, 'Charity Commission registration number equals enriched organization ID.',
               ?, ?, ?
        FROM {REGISTRY_TABLE} AS registry
        JOIN charities AS enriched
          ON CAST(enriched.charity_id AS TEXT) = registry.charity_number
        ON CONFLICT(registry_id, enriched_organization_id) DO UPDATE SET
            match_status='accepted',
            match_method='exact_identifier',
            match_confidence=1.0,
            match_reason='Charity Commission registration number equals enriched organization ID.',
            updated_at=excluded.updated_at
        """,
        (now, now, now),
    )
    accepted = conn.execute(
        f"SELECT COUNT(*) FROM {REGISTRY_LINK_TABLE} WHERE match_status = 'accepted'"
    ).fetchone()[0]
    unresolved = conn.execute(
        f"""
        SELECT COUNT(*) FROM {REGISTRY_TABLE} AS registry
        WHERE NOT EXISTS (
          SELECT 1 FROM {REGISTRY_LINK_TABLE} AS link
          WHERE link.registry_id = registry.registry_id AND link.match_status = 'accepted'
        )
        """
    ).fetchone()[0]
    conn.commit()
    return {"accepted_matches": accepted, "unresolved_matches": unresolved}


def import_charity_commission_registry(
    db_path: str,
    source_path: str | os.PathLike[str] = DEFAULT_SOURCE_PATH,
    batch_size: int = 1000,
) -> Dict[str, Any]:
    """Idempotently stream an official Charity Commission export into the registry layer."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(
            f"Charity Commission bulk source not found: {source}. "
            "Expected the extracted publicextract.charity.json file."
        )
    started = datetime.now(timezone.utc)
    imported_at = started.isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        fts_available = migrate_registry_schema(conn)
        stats: Dict[str, Any] = {
            "source_path": str(source),
            "records_read": 0,
            "records_inserted": 0,
            "records_updated": 0,
            "records_skipped": 0,
            "invalid_records": 0,
            "active_organizations": 0,
            "inactive_organizations": 0,
            "rows_with_income": 0,
            "rows_with_expenditure": 0,
            "rows_with_usable_geographic_fields": 0,
            "fts_available": fts_available,
        }
        batch: List[Tuple[Any, ...]] = []
        for record in iter_json_array(source):
            stats["records_read"] += 1
            row = registry_row_from_source(record, imported_at)
            if row is None:
                stats["invalid_records"] += 1
                stats["records_skipped"] += 1
                continue
            status = str(row[5] or "").casefold()
            if status in {"registered", "r"}:
                stats["active_organizations"] += 1
            else:
                stats["inactive_organizations"] += 1
            if row[8] is not None:
                stats["rows_with_income"] += 1
            if row[9] is not None:
                stats["rows_with_expenditure"] += 1
            if any(row[index] for index in (12, 13, 14, 15, 16, 17, 18)):
                stats["rows_with_usable_geographic_fields"] += 1
            batch.append(row)
            if len(batch) >= batch_size:
                inserted, updated = _write_batch(conn, batch)
                stats["records_inserted"] += inserted
                stats["records_updated"] += updated
                conn.commit()
                batch.clear()
        inserted, updated = _write_batch(conn, batch)
        stats["records_inserted"] += inserted
        stats["records_updated"] += updated
        conn.commit()

        # A source record absent from a successfully completed import is retained
        # for auditability but marked non-current instead of silently deleted.
        stale = conn.execute(
            f"""
            UPDATE {REGISTRY_TABLE}
            SET is_current_source_record = 0
            WHERE source_name = ? AND imported_at != ? AND is_current_source_record = 1
            """,
            (REGISTRY_SOURCE_NAME, imported_at),
        ).rowcount
        conn.commit()
        stats["records_marked_noncurrent"] = max(stale, 0)
        stats.update(refresh_exact_registry_links(conn))
        stats["registry_row_count"] = conn.execute(
            f"SELECT COUNT(*) FROM {REGISTRY_TABLE}"
        ).fetchone()[0]
        stats["duration_seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 3)
        return stats
    finally:
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import the official Charity Commission bulk register into the scalable directory layer."
    )
    parser.add_argument("--db", required=True, help="Path to the active Foundation Intelligence SQLite database")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_PATH), help="Extracted publicextract.charity.json path")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Create or reconcile the additive registry schema without importing source rows",
    )
    args = parser.parse_args(argv)
    if args.migrate_only:
        if not Path(args.db).is_file():
            raise FileNotFoundError(
                f"Active Foundation Intelligence database not found: {args.db}. "
                "Initialize the normal application database before applying the additive registry migration."
            )
        print(json.dumps({"database_path": args.db, "fts_available": migrate_registry_database(args.db)}, indent=2))
        return 0
    result = import_charity_commission_registry(args.db, args.source, args.batch_size)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
