from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from src.pipelines.download_charity_commission_bulk import DATASETS, DEFAULT_OUTPUT_DIR


DEFAULT_EXTRACT_DIR = DEFAULT_OUTPUT_DIR / "extracted"
DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "processed"
    / "charity_commission_register.sqlite3"
)
SQLITE_SEPARATOR = "\x1f"


def iter_json_array(handle: TextIO, chunk_size: int = 1024 * 1024) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    buffer = ""
    started = False
    finished = False

    while not finished:
        chunk = handle.read(chunk_size)
        if chunk:
            buffer += chunk
        elif not buffer.strip():
            break

        while True:
            buffer = buffer.lstrip("\ufeff\r\n\t ,")
            if not started:
                if not buffer:
                    break
                if buffer[0] != "[":
                    raise ValueError("Expected a top-level JSON array")
                buffer = buffer[1:]
                started = True
                continue
            buffer = buffer.lstrip("\r\n\t ,")
            if buffer.startswith("]"):
                finished = True
                buffer = buffer[1:]
                break
            if not buffer:
                break
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if chunk:
                    break
                raise
            if isinstance(value, dict):
                yield value
            buffer = buffer[end:]

        if not chunk:
            if not finished and buffer.strip():
                raise ValueError("JSON array ended unexpectedly")
            break


def iter_json_file(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from iter_json_array(handle)


def _sqlite_type(value: Any) -> str:
    if isinstance(value, bool) or isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _merge_type(current: str | None, value: Any) -> str:
    if value is None:
        return current or ""
    candidate = _sqlite_type(value)
    if not current:
        return candidate
    if current == "TEXT":
        return current
    if current == candidate:
        return current
    if {current, candidate} <= {"INTEGER", "REAL"}:
        return "REAL"
    return "TEXT"


def _quote(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f'"{identifier}"'


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _dataset_file(extract_dir: Path, dataset: str) -> Path:
    return extract_dir / f"publicextract.{dataset}.json"


def import_dataset(
    connection: sqlite3.Connection,
    dataset: str,
    path: Path,
    *,
    sample_size: int = 500,
    batch_size: int = 10_000,
) -> int:
    table = _quote(dataset)
    iterator = iter_json_file(path)
    sample: list[dict[str, Any]] = []
    columns: list[str] = []
    types: dict[str, str] = {}
    seen: set[str] = set()

    for _ in range(sample_size):
        try:
            record = next(iterator)
        except StopIteration:
            break
        sample.append(record)
        for key, value in record.items():
            if key not in seen:
                seen.add(key)
                columns.append(key)
            types[key] = _merge_type(types.get(key), value)

    if not columns:
        logging.warning("%s: no records found", dataset)
        return 0

    connection.execute(f"DROP TABLE IF EXISTS {table}")
    column_sql = ", ".join(
        f"{_quote(column)} {types.get(column) or 'TEXT'}" for column in columns
    )
    connection.execute(f"CREATE TABLE {table} ({column_sql})")
    placeholders = ",".join("?" for _ in columns)
    insert_sql = (
        f"INSERT INTO {table} ({','.join(_quote(column) for column in columns)}) "
        f"VALUES ({placeholders})"
    )

    count = 0
    batch: list[tuple[Any, ...]] = []
    started = time.monotonic()

    def add(record: dict[str, Any]) -> None:
        nonlocal count
        unknown = set(record).difference(seen)
        if unknown:
            raise ValueError(f"{dataset}: fields appeared after schema sampling: {sorted(unknown)}")
        batch.append(tuple(_sqlite_value(record.get(column)) for column in columns))
        count += 1

    for record in sample:
        add(record)
    for record in iterator:
        add(record)
        if len(batch) >= batch_size:
            connection.executemany(insert_sql, batch)
            connection.commit()
            batch.clear()
            if count % 100_000 < batch_size:
                logging.info("%s: imported %s records", dataset, f"{count:,}")
    if batch:
        connection.executemany(insert_sql, batch)
        connection.commit()

    elapsed = time.monotonic() - started
    logging.info("%s: imported %s records in %.1fs", dataset, f"{count:,}", elapsed)
    table_columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    for column in ("organisation_number", "registered_charity_number"):
        if column in table_columns:
            connection.execute(
                f"CREATE INDEX idx_{dataset}_{column} ON {table} ({_quote(column)})"
            )
    connection.commit()
    return count


def _build_enrichment_table(connection: sqlite3.Connection) -> None:
    logging.info("Building compact enrichment table for the dashboard")
    connection.executescript(
        f"""
        DROP TABLE IF EXISTS cc_latest_parta;
        CREATE TABLE cc_latest_parta AS
        SELECT * FROM (
            SELECT p.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY organisation_number
                       ORDER BY COALESCE(latest_fin_period_submitted_ind, 0) DESC,
                                fin_period_end_date DESC,
                                fin_period_order_number ASC
                   ) AS _rank
            FROM charity_annual_return_parta p
        ) WHERE _rank = 1;
        CREATE UNIQUE INDEX idx_cc_latest_parta_org ON cc_latest_parta (organisation_number);

        DROP TABLE IF EXISTS cc_latest_partb;
        CREATE TABLE cc_latest_partb AS
        SELECT * FROM (
            SELECT p.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY organisation_number
                       ORDER BY COALESCE(latest_fin_period_submitted_ind, 0) DESC,
                                fin_period_end_date DESC,
                                fin_period_order_number ASC
                   ) AS _rank
            FROM charity_annual_return_partb p
        ) WHERE _rank = 1;
        CREATE UNIQUE INDEX idx_cc_latest_partb_org ON cc_latest_partb (organisation_number);

        DROP TABLE IF EXISTS cc_area_agg;
        CREATE TABLE cc_area_agg AS
        SELECT organisation_number,
               json_group_array(DISTINCT geographic_area_description)
                   FILTER (WHERE geographic_area_description IS NOT NULL) AS all_areas,
               json_group_array(DISTINCT geographic_area_description)
                   FILTER (WHERE lower(geographic_area_type) = 'country') AS countries,
               json_group_array(DISTINCT geographic_area_description)
                   FILTER (WHERE lower(geographic_area_type) = 'region') AS regions
        FROM charity_area_of_operation
        GROUP BY organisation_number;
        CREATE UNIQUE INDEX idx_cc_area_agg_org ON cc_area_agg (organisation_number);

        DROP TABLE IF EXISTS cc_classification_agg;
        CREATE TABLE cc_classification_agg AS
        SELECT organisation_number,
               json_group_array(DISTINCT classification_description)
                   FILTER (WHERE classification_description IS NOT NULL) AS all_classifications,
               json_group_array(DISTINCT classification_description)
                   FILTER (WHERE lower(classification_type) = 'who') AS who_classifications,
               json_group_array(DISTINCT classification_description)
                   FILTER (WHERE lower(classification_type) = 'what') AS what_classifications,
               json_group_array(DISTINCT classification_description)
                   FILTER (WHERE lower(classification_type) = 'how') AS how_classifications
        FROM charity_classification
        GROUP BY organisation_number;
        CREATE UNIQUE INDEX idx_cc_classification_agg_org ON cc_classification_agg (organisation_number);

        DROP TABLE IF EXISTS charity_enrichment;
        CREATE TABLE charity_enrichment AS
        SELECT c.*,
               p.grant_making_is_main_activity AS primary_purpose_grant_making,
               p.total_gross_income AS annual_return_income,
               p.total_gross_expenditure AS annual_return_expenditure,
               p.count_volunteers,
               b.assets_total_assets_and_liabilities AS assets,
               b.assets_total_liabilities AS liabilities,
               b.expenditure_grants_institution,
               b.expenditure_charitable_expenditure,
               b.count_employees,
               a.countries,
               a.regions,
               a.all_areas AS areas_of_operation,
               k.who_classifications,
               k.what_classifications,
               k.how_classifications,
               k.all_classifications,
               EXISTS(
                   SELECT 1 FROM charity_annual_return_history h
                   WHERE h.organisation_number = c.organisation_number
               ) AS has_financial_history,
               EXISTS(
                   SELECT 1 FROM charity_governing_document g
                   WHERE g.organisation_number = c.organisation_number
               ) AS has_governing_document,
               EXISTS(
                   SELECT 1 FROM charity_event_history e
                   WHERE e.organisation_number = c.organisation_number
               ) AS has_event_history,
               EXISTS(
                   SELECT 1 FROM charity_published_report r
                   WHERE r.organisation_number = c.organisation_number
               ) AS has_published_report
        FROM charity c
        LEFT JOIN cc_latest_parta p USING (organisation_number)
        LEFT JOIN cc_latest_partb b USING (organisation_number)
        LEFT JOIN cc_area_agg a USING (organisation_number)
        LEFT JOIN cc_classification_agg k USING (organisation_number);

        CREATE UNIQUE INDEX idx_cc_enrichment_org ON charity_enrichment (organisation_number);
        CREATE INDEX idx_cc_enrichment_registered ON charity_enrichment (registered_charity_number);
        CREATE INDEX idx_cc_enrichment_status ON charity_enrichment (charity_registration_status);
        CREATE INDEX idx_cc_enrichment_grantmaker ON charity_enrichment (primary_purpose_grant_making);
        """
    )
    connection.commit()
    logging.info("Dashboard enrichment table contains %s records", f"{connection.execute('SELECT COUNT(*) FROM charity_enrichment').fetchone()[0]:,}")


def build_database(
    extract_dir: Path = DEFAULT_EXTRACT_DIR,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> dict[str, int]:
    missing = [dataset for dataset in DATASETS if not _dataset_file(extract_dir, dataset).exists()]
    if missing:
        raise FileNotFoundError(f"Missing extracted datasets: {', '.join(missing)}")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-262144")
    counts: dict[str, int] = {}
    try:
        for dataset in DATASETS:
            counts[dataset] = import_dataset(
                connection,
                dataset,
                _dataset_file(extract_dir, dataset),
            )
        _build_enrichment_table(connection)
        connection.execute("ANALYZE")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a local SQLite index from official Charity Commission JSON extracts."
    )
    parser.add_argument("--extract-dir", type=Path, default=DEFAULT_EXTRACT_DIR)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    counts = build_database(args.extract_dir, args.database)
    logging.info(
        "Built %s from %d datasets and %s source records",
        args.database,
        len(counts),
        f"{sum(counts.values()):,}",
    )


if __name__ == "__main__":
    main()
