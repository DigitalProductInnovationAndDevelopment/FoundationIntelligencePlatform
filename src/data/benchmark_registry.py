"""Reproducible local performance check for the scalable registry directory."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from bff.repositories import SQLiteCharityRepository
from data.registry import REGISTRY_FTS_TABLE, REGISTRY_TABLE


async def benchmark(db_path: str, query: str, charity_number: str) -> Dict[str, Any]:
    repository = SQLiteCharityRepository(db_path)
    started = time.perf_counter()
    name_page = await repository.get_registry_page(query=query, limit=50)
    name_latency = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    number_page = await repository.get_registry_page(charity_number=charity_number, limit=50)
    number_latency = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    filtered_page = await repository.get_registry_page(status="Registered", income_min=100_000, limit=50, sort="income_desc")
    filtered_latency = (time.perf_counter() - started) * 1000
    conn = sqlite3.connect(db_path)
    try:
        registry_count = conn.execute(f"SELECT COUNT(*) FROM {REGISTRY_TABLE}").fetchone()[0]
        number_plan = conn.execute(
            f"EXPLAIN QUERY PLAN SELECT registry_id FROM {REGISTRY_TABLE} WHERE charity_number = ?",
            (charity_number,),
        ).fetchall()
        filtered_plan = conn.execute(
            f"EXPLAIN QUERY PLAN SELECT registry_id FROM {REGISTRY_TABLE} WHERE registration_status = ? AND income >= ? ORDER BY income DESC, registry_id ASC LIMIT 50",
            ("Registered", 100_000),
        ).fetchall()
        fts_available = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (REGISTRY_FTS_TABLE,)
        ).fetchone())
    finally:
        conn.close()
    return {
        "database_path": db_path,
        "database_size_bytes": os.path.getsize(db_path),
        "registry_row_count": registry_count,
        "fts_available": fts_available,
        "name_search": {"query": query, "latency_ms": round(name_latency, 3), "result_count": len(name_page["results"]), "strategy": name_page["search_strategy"]},
        "exact_charity_number": {"charity_number": charity_number, "latency_ms": round(number_latency, 3), "result_count": len(number_page["results"])},
        "filtered_query": {"latency_ms": round(filtered_latency, 3), "result_count": len(filtered_page["results"])},
        "query_plans": {
            "charity_number": [row[3] for row in number_plan],
            "registered_income": [row[3] for row in filtered_plan],
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the local Charity Commission registry directory.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--query", default="foundation")
    parser.add_argument("--charity-number", default="200027")
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        raise FileNotFoundError(args.db)
    print(json.dumps(asyncio.run(benchmark(args.db, args.query, args.charity_number)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
