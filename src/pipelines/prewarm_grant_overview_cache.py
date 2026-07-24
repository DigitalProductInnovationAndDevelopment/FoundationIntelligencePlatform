"""Build derived Overview indexes and prewarm the default presentation payload."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from bff.repositories import SQLiteCharityRepository


DEFAULT_SOURCES = ["360Giving", "Charity Commission for England and Wales", "Philea"]


async def prewarm(database: Path, sources: list[str]) -> dict[str, object]:
    repository = SQLiteCharityRepository(str(database))
    payload = await repository.get_grant_overview(sources=sources)
    return {
        "database": str(database),
        "sources": sources,
        "status": payload.get("status"),
        "grants_monitored": payload.get("kpis", {}).get("grants_monitored"),
        "country_count": len(payload.get("map", {}).get("items", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prewarm indexed Foundation Intelligence Platform Overview data.")
    parser.add_argument("--database", type=Path, default=Path("src/data/charities.db"))
    parser.add_argument(
        "--sources", default=",".join(DEFAULT_SOURCES),
        help="comma-separated data-source selection used by the default UI",
    )
    args = parser.parse_args()
    sources = [source.strip() for source in args.sources.split(",") if source.strip()]
    print(json.dumps(asyncio.run(prewarm(args.database, sources)), ensure_ascii=False))


if __name__ == "__main__":
    main()
