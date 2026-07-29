#!/usr/bin/env python3
"""Compare SQLite source facts with the active PostgreSQL dataset and write goldens."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bff.database import DatabaseSettings
from scoring.engine import load_score_configuration, score_relevance
from transition.shadow import ComparisonPolicy, compare_payloads


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "src/data/charities.db"
DEFAULT_GOLDEN = PROJECT_ROOT / "config/golden/transition-domain.json"


@dataclass(frozen=True)
class QueryPair:
    name: str
    sqlite: str
    postgresql: str
    parameters: Mapping[str, Any]


QUERIES = (
    QueryPair(
        "overview_totals",
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor,
               SUM(CASE WHEN country_count>0 THEN 1 ELSE 0 END) AS mapped_grants,
               SUM(CASE WHEN programme_category_count>0 THEN 1 ELSE 0 END)
                   AS classified_grants
        FROM grant_overview_facts
        """,
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor,
               COUNT(*) FILTER (WHERE country_count>0) AS mapped_grants,
               COUNT(*) FILTER (WHERE programme_category_count>0) AS classified_grants
        FROM grant_overview_facts WHERE dataset_version=:dataset_version
        """,
        {},
    ),
    QueryPair(
        "country_totals",
        """
        SELECT country.country_code, MIN(country.country_name) AS country_name,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.country_count=1
                          AND fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS single_country_eur_minor
        FROM grant_beneficiary_countries AS country
        JOIN grant_overview_facts AS fact ON fact.grant_id=country.grant_id
        GROUP BY country.country_code ORDER BY grant_count DESC, country.country_code
        """,
        """
        SELECT country.country_code, MIN(country.country_name) AS country_name,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.country_count=1
                          AND fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS single_country_eur_minor
        FROM grant_beneficiary_countries AS country
        JOIN grant_overview_facts AS fact
          ON fact.dataset_version=country.dataset_version AND fact.grant_id=country.grant_id
        WHERE fact.dataset_version=:dataset_version
        GROUP BY country.country_code ORDER BY grant_count DESC, country.country_code
        """,
        {},
    ),
    QueryPair(
        "programme_totals",
        """
        SELECT category.programme_area,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS represented_eur_minor
        FROM grant_programme_categories AS category
        JOIN grant_overview_facts AS fact ON fact.grant_id=category.grant_id
        GROUP BY category.programme_area ORDER BY grant_count DESC, category.programme_area
        """,
        """
        SELECT category.programme_area,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS represented_eur_minor
        FROM grant_programme_categories AS category
        JOIN grant_overview_facts AS fact
          ON fact.dataset_version=category.dataset_version AND fact.grant_id=category.grant_id
        WHERE fact.dataset_version=:dataset_version
        GROUP BY category.programme_area ORDER BY grant_count DESC, category.programme_area
        """,
        {},
    ),
    QueryPair(
        "donor_rankings",
        """
        SELECT COALESCE(CAST(grant_row.funding_charity_id AS TEXT),
                        'name:' || fact.funding_name_normalized) AS identity,
               MIN(fact.funding_name) AS name,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grants AS grant_row ON grant_row.grant_id=fact.grant_id
        GROUP BY identity ORDER BY total_eur_minor DESC, identity LIMIT 25
        """,
        """
        SELECT COALESCE(CAST(grant_row.funding_charity_id AS TEXT),
                        'name:' || fact.funding_name_normalized) AS identity,
               MIN(fact.funding_name) AS name,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grants AS grant_row
          ON grant_row.dataset_version=fact.dataset_version AND grant_row.grant_id=fact.grant_id
        WHERE fact.dataset_version=:dataset_version
        GROUP BY identity ORDER BY total_eur_minor DESC, identity LIMIT 25
        """,
        {},
    ),
    QueryPair(
        "recipient_rankings",
        """
        SELECT COALESCE(CAST(grant_row.recipient_charity_id AS TEXT),
                        'name:' || fact.recipient_name_normalized) AS identity,
               MIN(fact.recipient_name) AS name,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grants AS grant_row ON grant_row.grant_id=fact.grant_id
        GROUP BY identity ORDER BY total_eur_minor DESC, identity LIMIT 25
        """,
        """
        SELECT COALESCE(CAST(grant_row.recipient_charity_id AS TEXT),
                        'name:' || fact.recipient_name_normalized) AS identity,
               MIN(fact.recipient_name) AS name,
               COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grants AS grant_row
          ON grant_row.dataset_version=fact.dataset_version AND grant_row.grant_id=fact.grant_id
        WHERE fact.dataset_version=:dataset_version
        GROUP BY identity ORDER BY total_eur_minor DESC, identity LIMIT 25
        """,
        {},
    ),
    QueryPair(
        "date_filter",
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts WHERE award_date BETWEEN :date_from AND :date_to
        """,
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts
        WHERE dataset_version=:dataset_version
          AND award_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
        """,
        {"date_from": "2020-01-01", "date_to": "2024-12-31"},
    ),
    QueryPair(
        "country_filter",
        """
        SELECT COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.country_count=1
                          AND fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grant_beneficiary_countries AS country ON country.grant_id=fact.grant_id
        WHERE country.country_code=:country
        """,
        """
        SELECT COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.country_count=1
                          AND fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grant_beneficiary_countries AS country
          ON country.dataset_version=fact.dataset_version AND country.grant_id=fact.grant_id
        WHERE fact.dataset_version=:dataset_version AND country.country_code=:country
        """,
        {"country": "GB"},
    ),
    QueryPair(
        "programme_filter",
        """
        SELECT COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grant_programme_categories AS category ON category.grant_id=fact.grant_id
        WHERE category.programme_area=:programme
        """,
        """
        SELECT COUNT(DISTINCT fact.grant_id) AS grant_count,
               SUM(CASE WHEN fact.eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts AS fact
        JOIN grant_programme_categories AS category
          ON category.dataset_version=fact.dataset_version AND category.grant_id=fact.grant_id
        WHERE fact.dataset_version=:dataset_version AND category.programme_area=:programme
        """,
        {"programme": "tech-enablement"},
    ),
    QueryPair(
        "donor_filter",
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts WHERE funding_name_normalized LIKE '%' || :term || '%'
        """,
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts
        WHERE dataset_version=:dataset_version
          AND funding_name_normalized LIKE '%' || :term || '%'
        """,
        {"term": "trust"},
    ),
    QueryPair(
        "recipient_filter",
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts WHERE recipient_name_normalized LIKE '%' || :term || '%'
        """,
        """
        SELECT COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts
        WHERE dataset_version=:dataset_version
          AND recipient_name_normalized LIKE '%' || :term || '%'
        """,
        {"term": "university"},
    ),
    QueryPair(
        "monthly_trends",
        """
        SELECT SUBSTR(award_date, 1, 7) AS period, COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts WHERE award_date IS NOT NULL
        GROUP BY period ORDER BY period
        """,
        """
        SELECT TO_CHAR(award_date, 'YYYY-MM') AS period, COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts
        WHERE dataset_version=:dataset_version AND award_date IS NOT NULL
        GROUP BY period ORDER BY period
        """,
        {},
    ),
    QueryPair(
        "yearly_trends",
        """
        SELECT SUBSTR(award_date, 1, 4) AS period, COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts WHERE award_date IS NOT NULL
        GROUP BY period ORDER BY period
        """,
        """
        SELECT TO_CHAR(award_date, 'YYYY') AS period, COUNT(*) AS grant_count,
               SUM(CASE WHEN eur_amount_status NOT IN ('missing','invalid','negative')
                        THEN eur_amount_minor ELSE 0 END) AS total_eur_minor
        FROM grant_overview_facts
        WHERE dataset_version=:dataset_version AND award_date IS NOT NULL
        GROUP BY period ORDER BY period
        """,
        {},
    ),
    QueryPair(
        "registry_search",
        """
        SELECT registry_id, charity_number, registered_name, registration_status,
               country_code FROM charity_registry_organizations
        WHERE normalized_name LIKE '%' || :term || '%'
        ORDER BY normalized_name, registry_id LIMIT 10
        """,
        """
        SELECT registry_id, charity_number, registered_name, registration_status,
               country_code FROM charity_registry_organizations
        WHERE dataset_version=:dataset_version
          AND normalized_name LIKE '%' || :term || '%'
        ORDER BY normalized_name, registry_id LIMIT 10
        """,
        {"term": "trust"},
    ),
    QueryPair(
        "organisation_details",
        """
        SELECT charity_id, name, organization_type, primary_source,
               headquarters_country, transaction_coverage
        FROM charities ORDER BY charity_id LIMIT 10
        """,
        """
        SELECT charity_id, name, organization_type, primary_source,
               headquarters_country, transaction_coverage
        FROM charities WHERE dataset_version=:dataset_version
        ORDER BY charity_id LIMIT 10
        """,
        {},
    ),
    QueryPair(
        "grant_list",
        """
        SELECT grant_id, funding_name, recipient_name, currency,
               CAST(ROUND(amount * 100) AS INTEGER) AS amount_minor
        FROM grants ORDER BY grant_id LIMIT 20
        """,
        """
        SELECT grant_id, funding_name, recipient_name, currency,
               CAST(ROUND(amount * 100) AS BIGINT) AS amount_minor
        FROM grants WHERE dataset_version=:dataset_version
        ORDER BY grant_id LIMIT 20
        """,
        {},
    ),
    QueryPair(
        "sankey",
        """
        SELECT funding_name, recipient_name, currency, COUNT(*) AS grant_count,
               CAST(ROUND(SUM(amount) * 100) AS INTEGER) AS amount_minor
        FROM grants WHERE funding_name IS NOT NULL
        GROUP BY funding_name, recipient_name, currency
        ORDER BY grant_count DESC, funding_name, recipient_name LIMIT 25
        """,
        """
        SELECT funding_name, recipient_name, currency, COUNT(*) AS grant_count,
               CAST(ROUND(SUM(amount) * 100) AS BIGINT) AS amount_minor
        FROM grants WHERE dataset_version=:dataset_version AND funding_name IS NOT NULL
        GROUP BY funding_name, recipient_name, currency
        ORDER BY grant_count DESC, funding_name, recipient_name LIMIT 25
        """,
        {},
    ),
    QueryPair(
        "map_relationships",
        """
        SELECT fact.origin_country_code,
               MIN(fact.origin_country_name) AS origin_country_name,
               country.country_code AS destination_country_code,
               MIN(country.country_name) AS destination_country_name,
               COUNT(DISTINCT fact.grant_id) AS grant_count
        FROM grant_overview_facts AS fact
        JOIN grant_beneficiary_countries AS country ON country.grant_id=fact.grant_id
        WHERE fact.origin_country_code IS NOT NULL
          AND fact.origin_country_code<>country.country_code
        GROUP BY fact.origin_country_code, country.country_code
        ORDER BY grant_count DESC, fact.origin_country_code, country.country_code LIMIT 100
        """,
        """
        SELECT fact.origin_country_code,
               MIN(fact.origin_country_name) AS origin_country_name,
               country.country_code AS destination_country_code,
               MIN(country.country_name) AS destination_country_name,
               COUNT(DISTINCT fact.grant_id) AS grant_count
        FROM grant_overview_facts AS fact
        JOIN grant_beneficiary_countries AS country
          ON country.dataset_version=fact.dataset_version AND country.grant_id=fact.grant_id
        WHERE fact.dataset_version=:dataset_version
          AND fact.origin_country_code IS NOT NULL
          AND fact.origin_country_code<>country.country_code
        GROUP BY fact.origin_country_code, country.country_code
        ORDER BY grant_count DESC, fact.origin_country_code, country.country_code LIMIT 100
        """,
        {},
    ),
    QueryPair(
        "currency_statuses",
        """
        SELECT COALESCE(currency, 'UNKNOWN') AS currency,
               COALESCE(conversion_status, 'missing') AS conversion_status,
               COUNT(*) AS grant_count
        FROM grants GROUP BY currency, conversion_status
        ORDER BY currency, conversion_status
        """,
        """
        SELECT COALESCE(currency, 'UNKNOWN') AS currency,
               COALESCE(conversion_status, 'missing') AS conversion_status,
               COUNT(*) AS grant_count
        FROM grants WHERE dataset_version=:dataset_version
        GROUP BY currency, conversion_status ORDER BY currency, conversion_status
        """,
        {},
    ),
)


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _sqlite_rows(connection: sqlite3.Connection, query: QueryPair) -> list[dict[str, Any]]:
    cursor = connection.execute(query.sqlite, dict(query.parameters))
    columns = [str(item[0]) for item in cursor.description]
    return [
        {column: _normalize(value) for column, value in zip(columns, row)}
        for row in cursor.fetchall()
    ]


async def _postgres_rows(connection: Any, query: QueryPair, dataset_version: str) -> list[dict[str, Any]]:
    parameters = {**query.parameters, "dataset_version": dataset_version}
    for key in ("date_from", "date_to"):
        if isinstance(parameters.get(key), str):
            parameters[key] = date.fromisoformat(parameters[key])
    result = await connection.execute(text(query.postgresql), parameters)
    return [_normalize(dict(row)) for row in result.mappings()]


def _source_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def verify(source_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    source_uri = f"file:{source_path.resolve()}?mode=ro&immutable=1"
    sqlite_connection = sqlite3.connect(source_uri, uri=True)
    engine = create_async_engine(DatabaseSettings.from_env().sqlalchemy_url(), pool_pre_ping=True)
    source: dict[str, Any] = {}
    target: dict[str, Any] = {}
    try:
        async with engine.connect() as postgres_connection:
            dataset_version = str(
                (
                    await postgres_connection.execute(
                        text(
                            "SELECT dataset_version FROM dataset_versions "
                            "WHERE is_active AND status='active'"
                        )
                    )
                ).scalar_one()
            )
            for query in QUERIES:
                source[query.name] = _sqlite_rows(sqlite_connection, query)
                target[query.name] = await _postgres_rows(
                    postgres_connection, query, dataset_version
                )
            score_configuration = load_score_configuration()
            score = score_relevance(
                {
                    "programme_areas_source": ["Education", "Health"],
                    "programme_areas_inferred": [],
                    "geographic_focus_source": ["United Kingdom", "Ghana"],
                    "geographic_focus_inferred": [],
                    "annual_expenditure": 2_000_000,
                    "organization_type": "foundation",
                },
                score_configuration.example_target_profile,
                {"average_amount": 60_000, "currency": "EUR", "grant_count": 4},
                score_configuration,
            )
            score_fixture = {
                "score": score["score"],
                "confidence": score["confidence"],
                "data_completeness": score["data_completeness"],
                "score_version": score["score_version"],
                "configuration_status": score["configuration_status"],
                "components": {
                    name: {
                        key: component[key]
                        for key in (
                            "score", "weight", "weighted_score", "confidence", "available"
                        )
                    }
                    for name, component in sorted(score["components"].items())
                },
            }
            source["score_components"] = score_fixture
            target["score_components"] = score_fixture
    finally:
        sqlite_connection.close()
        await engine.dispose()
    return source, target, dataset_version


def _build_golden(source: dict[str, Any], source_path: Path) -> dict[str, Any]:
    return {
        "fixture_version": "1",
        "source_sha256": _source_checksum(source_path),
        "semantic_contract": source,
        "non_database_journeys": {
            "news": "live_network_calls_disabled_in_local_acceptance",
            "pipeline_status": "durable_postgresql_job_status_contract",
            "manual_refresh_permissions": "operator_role_and_idempotency_required",
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--write-golden", action="store_true")
    arguments = parser.parse_args()
    source, target, dataset_version = await verify(arguments.source)
    result = compare_payloads(
        source,
        target,
        journey="local_sqlite_postgresql_semantic_projection",
        request_id="phase13-local",
        policy=ComparisonPolicy(maximum_differences=100),
    )
    golden = _build_golden(source, arguments.source)
    if arguments.write_golden:
        arguments.golden.parent.mkdir(parents=True, exist_ok=True)
        arguments.golden.write_text(
            json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    elif arguments.golden.exists():
        expected = json.loads(arguments.golden.read_text(encoding="utf-8"))
        golden_result = compare_payloads(
            expected,
            golden,
            journey="transition_golden_fixture",
            request_id="phase13-local",
        )
        if golden_result.status != "match":
            print(json.dumps({"status": "failed", "golden": _normalize(golden_result.__dict__)}))
            return 1
    report = {
        "status": "passed" if result.status == "match" else "failed",
        "dataset_version": dataset_version,
        "source_sha256": golden["source_sha256"],
        "semantic_projections": len(QUERIES),
        "difference_count": result.difference_count,
        "differences": [_normalize(item.__dict__) for item in result.differences],
        "golden_written": arguments.write_golden,
        "aws_actions_performed": False,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if result.status == "match" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
