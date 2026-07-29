"""Async PostgreSQL grant aggregates, map facts, trends and drill-downs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import text

from bff.postgres.base import PostgresRepository, iso_value, number_value, utc_now


VALID_ORIGINAL = "fact.original_amount_status NOT IN ('negative','invalid','missing')"
VALID_EUR = "fact.eur_amount_status NOT IN ('missing','invalid')"


def _amount_policy(maximum: Optional[float] = None) -> dict[str, Any]:
    return {
        "monetary_precision": "minor_units_2_decimal_places",
        "rounding": "ROUND_HALF_UP",
        "zero_amounts": "included_when_source_value_is_numeric_zero",
        "negative_amounts": "excluded_and_reported",
        "upper_bound": "no_unapproved_implausibility_threshold_applied",
        "maximum_observed_amount": maximum,
    }


def _scope() -> dict[str, str]:
    return {
        "coverage_note": "Available PostgreSQL active-dataset grant records only.",
        "market_scope": "available cached 360Giving records",
    }


class AnalyticsRepository(PostgresRepository):
    @staticmethod
    def _filtered_scope(filters: dict[str, Any]) -> tuple[str, dict[str, Any], str, str]:
        conditions = ["fact.dataset_version=:dataset_version"]
        parameters: dict[str, Any] = {
            "currency": str(filters.get("currency") or "").upper() or None,
            "date_from": filters.get("date_from"),
            "date_to": filters.get("date_to"),
            "beneficiary_geographies": list(filters.get("beneficiary_geographies") or []),
            "programme_areas": list(filters.get("programme_areas") or []),
            "donor": f"%{str(filters.get('donor') or '').strip()}%",
            "recipient": f"%{str(filters.get('recipient') or '').strip()}%",
            "sources": list(filters.get("sources") or []),
        }
        if parameters["currency"]:
            conditions.append("fact.currency=:currency")
            amount_minor = "fact.original_amount_minor"
            amount_valid = VALID_ORIGINAL
            selected_currency = str(parameters["currency"])
        else:
            amount_minor = "fact.eur_amount_minor"
            amount_valid = VALID_EUR
            selected_currency = "EUR"
        if parameters["date_from"]:
            conditions.append("fact.award_date>=CAST(:date_from AS date)")
        if parameters["date_to"]:
            conditions.append("fact.award_date<=CAST(:date_to AS date)")
        if parameters["beneficiary_geographies"]:
            conditions.append(
                """
                EXISTS (
                    SELECT 1 FROM grant_beneficiary_countries AS scope_country
                    WHERE scope_country.dataset_version=fact.dataset_version
                      AND scope_country.grant_id=fact.grant_id
                      AND scope_country.country_name=ANY(
                          CAST(:beneficiary_geographies AS text[])
                      )
                )
                """
            )
        if parameters["programme_areas"]:
            conditions.append(
                """
                EXISTS (
                    SELECT 1 FROM grant_programme_categories AS scope_category
                    WHERE scope_category.dataset_version=fact.dataset_version
                      AND scope_category.grant_id=fact.grant_id
                      AND scope_category.programme_area=ANY(
                          CAST(:programme_areas AS text[])
                      )
                )
                """
            )
        if filters.get("donor"):
            conditions.append("fact.funding_name ILIKE :donor")
        if filters.get("recipient"):
            conditions.append("fact.recipient_name ILIKE :recipient")
        if parameters["sources"]:
            conditions.append("fact.source_namespace=ANY(CAST(:sources AS text[]))")
        return " AND ".join(conditions), parameters, amount_minor, selected_currency

    async def beneficiary_geographies(self) -> list[str]:
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            rows = await session.execute(
                text(
                    """
                    SELECT DISTINCT country_name
                    FROM grant_beneficiary_countries
                    WHERE dataset_version=:dataset_version
                    ORDER BY country_name
                    """
                ),
                {"dataset_version": dataset_version},
            )
        return [str(row[0]) for row in rows if row[0]]

    async def map(self, **filters: Any) -> dict[str, Any]:
        conditions, parameters, amount_minor, selected_currency = self._filtered_scope(filters)
        valid = VALID_ORIGINAL if parameters["currency"] else VALID_EUR
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            f"""
                            SELECT country.country_code, country.country_name,
                                   COUNT(DISTINCT fact.grant_id) AS grant_count,
                                   SUM(
                                       CASE WHEN {valid}
                                            THEN {amount_minor}::numeric
                                                 / NULLIF(fact.country_count, 0)
                                            ELSE 0 END
                                   ) / 100 AS total_amount,
                                   COUNT(DISTINCT fact.funding_name) AS distinct_funders,
                                   COUNT(DISTINCT fact.recipient_name) AS distinct_recipients,
                                   COUNT(*) AS association_count,
                                   COUNT(*) FILTER (WHERE fact.country_count>1)
                                       AS multi_country_count
                            FROM grant_overview_facts AS fact
                            JOIN grant_beneficiary_countries AS country
                              ON country.dataset_version=fact.dataset_version
                             AND country.grant_id=fact.grant_id
                            WHERE {conditions}
                            GROUP BY country.country_code, country.country_name
                            ORDER BY total_amount DESC NULLS LAST,
                                     country.country_name, country.country_code
                            LIMIT 500
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
            totals = (
                await session.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS total_grants,
                               COUNT(*) FILTER (
                                   WHERE EXISTS (
                                       SELECT 1 FROM grant_beneficiary_countries AS country
                                       WHERE country.dataset_version=fact.dataset_version
                                         AND country.grant_id=fact.grant_id
                                   )
                               ) AS known_grants,
                               COUNT(*) FILTER (WHERE fact.country_count>1) AS multi_country,
                               COUNT(*) FILTER (WHERE NOT ({valid})) AS invalid_amount
                        FROM grant_overview_facts AS fact WHERE {conditions}
                        """
                    ),
                    parameters,
                )
            ).mappings().one()
            currencies = await session.execute(
                text(
                    """
                    SELECT DISTINCT currency FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version AND currency IS NOT NULL
                    ORDER BY currency
                    """
                ),
                {"dataset_version": dataset_version},
            )
            sources = await session.execute(
                text(
                    """
                    SELECT DISTINCT source_namespace FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version ORDER BY source_namespace
                    """
                ),
                {"dataset_version": dataset_version},
            )
        total_grants = int(totals["total_grants"] or 0)
        known_grants = int(totals["known_grants"] or 0)
        items = [
            {
                "region_or_country_code": row.get("country_code"),
                "region_or_country_name": str(row.get("country_name") or row.get("country_code")),
                "grant_count": int(row["grant_count"]),
                "total_amount": number_value(row.get("total_amount")),
                "currency": selected_currency,
                "distinct_funders": int(row["distinct_funders"] or 0),
                "distinct_recipients": int(row["distinct_recipients"] or 0),
                "top_programme_areas": [],
                "top_funders": [],
                "top_recipients": [],
                "original_geographies": [str(row.get("country_name") or "")],
                "funding_grant_count": int(row["grant_count"]),
                "excluded_multi_country_grant_count": int(row["multi_country_count"] or 0),
                "excluded_invalid_amount_grant_count": int(totals["invalid_amount"] or 0),
            }
            for row in rows
        ]
        return {
            "status": "available" if items else "no_transactions_found",
            "geographic_dimension": "beneficiary_country",
            "items": items,
            "known_geography_count": known_grants,
            "unknown_geography_count": max(0, total_grants - known_grants),
            "coverage_percentage": round(known_grants / total_grants * 100, 2) if total_grants else 0.0,
            "currencies": [str(row[0]) for row in currencies],
            "selected_currency": selected_currency,
            "funding_status": "available",
            "funding_mode_available": True,
            "grant_country_association_count": sum(int(row["association_count"]) for row in rows),
            "multi_country_grant_count": int(totals["multi_country"] or 0),
            "funding_excluded_multi_country_count": 0,
            "funding_excluded_multi_country_amount": 0.0,
            "funding_excluded_currency_count": 0,
            "funding_excluded_invalid_amount_count": int(totals["invalid_amount"] or 0),
            "connections": [],
            "connection_grant_count": 0,
            "connection_excluded_no_headquarters_count": 0,
            "connection_same_country_count": 0,
            "minimum_coverage_threshold": float(filters.get("min_coverage") or 0.30),
            "metadata": {
                "data_mode": "postgresql_materialized_grant_facts",
                "source": [str(row[0]) for row in sources],
                "generated_at": utc_now(),
                "record_count": total_grants,
                "derivation": "versioned grant_overview_facts and grant_beneficiary_countries",
                "coverage": known_grants / total_grants if total_grants else 0.0,
                "limitations": ["Connection-heavy relationships are loaded separately."],
            },
        }

    async def suggestions(
        self,
        *,
        sources: Optional[Sequence[str]],
        limit: int,
    ) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 5000)
        source_condition = (
            "AND source_namespace=ANY(CAST(:sources AS text[]))" if sources else ""
        )
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters = {
                "dataset_version": dataset_version,
                "sources": list(sources or []),
                "limit": limit,
            }
            donors = await session.execute(
                text(
                    f"""
                    SELECT funding_name, COUNT(*) AS count
                    FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version
                      AND funding_name IS NOT NULL {source_condition}
                    GROUP BY funding_name ORDER BY count DESC, funding_name LIMIT :limit
                    """
                ),
                parameters,
            )
            recipients = await session.execute(
                text(
                    f"""
                    SELECT recipient_name, COUNT(*) AS count
                    FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version
                      AND recipient_name IS NOT NULL {source_condition}
                    GROUP BY recipient_name ORDER BY count DESC, recipient_name LIMIT :limit
                    """
                ),
                parameters,
            )
        return {
            "status": "available",
            "donors": [{"name": str(row[0]), "grant_count": int(row[1])} for row in donors],
            "recipients": [{"name": str(row[0]), "grant_count": int(row[1])} for row in recipients],
            "metadata": {"data_mode": "postgresql_active_dataset", "bounded_limit": limit},
        }

    async def trends(self, **filters: Any) -> dict[str, Any]:
        months = min(max(int(filters.get("months") or 120), 1), 120)
        conditions, parameters, amount_minor, selected_currency = self._filtered_scope(filters)
        valid = VALID_ORIGINAL if parameters["currency"] else VALID_EUR
        if not any(filters.get(key) for key in ("date_from", "date_to")) and "months" in filters:
            conditions += (
                " AND fact.award_date >= CURRENT_DATE "
                "- CAST(:months AS integer) * INTERVAL '1 month'"
            )
            parameters["months"] = months
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            f"""
                            SELECT to_char(date_trunc('month', fact.award_date), 'YYYY-MM') AS month,
                                   COUNT(*) AS source_record_count,
                                   COUNT(*) FILTER (WHERE {valid}) AS grant_count,
                                   SUM(CASE WHEN {valid} THEN {amount_minor} ELSE 0 END) / 100.0
                                       AS total_amount,
                                   COUNT(*) FILTER (WHERE fact.country_count>0) AS mapped,
                                   COUNT(*) FILTER (WHERE fact.country_count=0) AS unmapped
                            FROM grant_overview_facts AS fact
                            WHERE {conditions} AND fact.award_date IS NOT NULL
                            GROUP BY date_trunc('month', fact.award_date)
                            ORDER BY date_trunc('month', fact.award_date)
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
            summary = (
                await session.execute(
                    text(
                        f"""
                        SELECT MIN(fact.award_date) AS first_date,
                               MAX(fact.award_date) AS latest_date,
                               COUNT(*) FILTER (WHERE fact.award_date IS NULL) AS missing_date,
                               COUNT(*) FILTER (WHERE fact.original_amount_status='negative') AS negative,
                               COUNT(*) FILTER (WHERE fact.original_amount_status='zero') AS zero,
                               COUNT(*) FILTER (WHERE NOT ({valid})) AS invalid,
                               MAX(CASE WHEN {valid} THEN {amount_minor} END) / 100.0 AS maximum
                        FROM grant_overview_facts AS fact WHERE {conditions}
                        """
                    ),
                    parameters,
                )
            ).mappings().one()
            currencies = await session.execute(
                text(
                    """
                    SELECT DISTINCT currency FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version AND currency IS NOT NULL
                    ORDER BY currency
                    """
                ),
                {"dataset_version": dataset_version},
            )
            sources = await session.execute(
                text(
                    """
                    SELECT DISTINCT source_namespace FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version ORDER BY source_namespace
                    """
                ),
                {"dataset_version": dataset_version},
            )
        first_date = iso_value(summary["first_date"])
        latest_date = iso_value(summary["latest_date"])
        return {
            "status": "available" if rows else "no_transactions_found",
            "currency": selected_currency,
            "available_currencies": [str(row[0]) for row in currencies],
            "date_basis": "award_date",
            "granularity": "monthly",
            "period": {
                "from": str(first_date)[:7] if first_date else "unknown",
                "to": str(latest_date)[:7] if latest_date else "unknown",
                "months": len(rows),
                "anchor": "observed_award_dates",
            } if rows else None,
            "items": [
                {
                    "month": row["month"],
                    "grant_count": int(row["grant_count"] or 0),
                    "source_record_count": int(row["source_record_count"] or 0),
                    "total_amount": number_value(row["total_amount"]),
                    "coverage_status": "observed",
                    "mapped_grant_count": int(row["mapped"] or 0),
                    "unmapped_grant_count": int(row["unmapped"] or 0),
                }
                for row in rows
            ],
            "excluded": {
                "missing_date": int(summary["missing_date"] or 0),
                "invalid_date": 0,
                "missing_amount": int(summary["invalid"] or 0),
                "invalid_amount": int(summary["invalid"] or 0),
                "negative_amount": int(summary["negative"] or 0),
                "unsupported_currency": 0,
                "currency_filtered": 0,
                "unsupported_source": 0,
                "outside_period": 0,
            },
            "zero_amount_count": int(summary["zero"] or 0),
            "latest_award_date": latest_date,
            "last_refreshed_at": utc_now(),
            "source": [str(row[0]) for row in sources],
            "data_mode": "postgresql_materialized_grant_facts",
            "amount_policy": _amount_policy(number_value(summary["maximum"])),
            "scope": _scope(),
        }

    async def themes(self, *, currency: Optional[str] = None, **filters: Any) -> dict[str, Any]:
        filters = {**filters, "currency": currency}
        conditions, parameters, amount_minor, selected_currency = self._filtered_scope(filters)
        valid = VALID_ORIGINAL if parameters["currency"] else VALID_EUR
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            f"""
                            SELECT category.programme_area,
                                   COUNT(DISTINCT fact.grant_id) AS distinct_grants,
                                   SUM(1.0 / NULLIF(fact.programme_category_count, 0))
                                       AS weighted_grants,
                                   SUM(CASE WHEN {valid}
                                            THEN {amount_minor}::numeric
                                                 / NULLIF(fact.programme_category_count, 0)
                                            ELSE 0 END) / 100 AS allocated_amount,
                                   COUNT(DISTINCT fact.grant_id) FILTER (
                                       WHERE fact.programme_provenance='source'
                                   ) AS source_count,
                                   COUNT(DISTINCT fact.grant_id) FILTER (
                                       WHERE fact.programme_provenance='inferred'
                                   ) AS inferred_count
                            FROM grant_overview_facts AS fact
                            JOIN grant_programme_categories AS category
                              ON category.dataset_version=fact.dataset_version
                             AND category.grant_id=fact.grant_id
                            WHERE {conditions}
                            GROUP BY category.programme_area
                            ORDER BY allocated_amount DESC, category.programme_area
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
            coverage = (
                await session.execute(
                    text(
                        f"""
                        SELECT COUNT(*) FILTER (WHERE {valid}) AS qualifying,
                               COUNT(*) FILTER (
                                   WHERE {valid} AND fact.programme_category_count>0
                               ) AS classified,
                               COUNT(*) FILTER (
                                   WHERE {valid} AND fact.programme_category_count=0
                               ) AS unclassified,
                               COUNT(*) FILTER (WHERE fact.programme_provenance='source')
                                   AS source_count,
                               COUNT(*) FILTER (WHERE fact.programme_provenance='inferred')
                                   AS inferred_count,
                               COUNT(*) FILTER (WHERE fact.programme_category_count>1)
                                   AS multiple_count,
                               COUNT(*) FILTER (WHERE fact.invalid_source_label) AS invalid_label,
                               COUNT(*) FILTER (WHERE fact.low_confidence_inference) AS low_confidence,
                               SUM(CASE WHEN {valid} THEN {amount_minor} ELSE 0 END) / 100.0
                                   AS qualifying_amount,
                               COUNT(*) FILTER (WHERE fact.original_amount_status='negative') AS negative,
                               COUNT(*) FILTER (WHERE fact.original_amount_status='zero') AS zero,
                               MAX(CASE WHEN {valid} THEN {amount_minor} END) / 100.0 AS maximum
                        FROM grant_overview_facts AS fact WHERE {conditions}
                        """
                    ),
                    parameters,
                )
            ).mappings().one()
            currencies = await session.execute(
                text(
                    """
                    SELECT DISTINCT currency FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version AND currency IS NOT NULL
                    ORDER BY currency
                    """
                ),
                {"dataset_version": dataset_version},
            )
            sources = await session.execute(
                text(
                    """
                    SELECT DISTINCT source_namespace FROM grant_overview_facts
                    WHERE dataset_version=:dataset_version ORDER BY source_namespace
                    """
                ),
                {"dataset_version": dataset_version},
            )
        qualifying = int(coverage["qualifying"] or 0)
        classified = int(coverage["classified"] or 0)
        source_count = int(coverage["source_count"] or 0)
        inferred_count = int(coverage["inferred_count"] or 0)
        items = [
            {
                "programme_area": row["programme_area"],
                "distinct_grant_count": int(row["distinct_grants"]),
                "weighted_grant_count": round(float(row["weighted_grants"] or 0), 4),
                "allocated_amount": round(float(row["allocated_amount"] or 0), 2),
                "source_classified_grant_count": int(row["source_count"] or 0),
                "inferred_classified_grant_count": int(row["inferred_count"] or 0),
                "unclassified_grant_count": 0,
            }
            for row in rows
        ]
        allocated = round(sum(item["allocated_amount"] for item in items), 2)
        return {
            "status": "available" if qualifying else "no_transactions_found",
            "currency": selected_currency,
            "available_currencies": [str(row[0]) for row in currencies],
            "allocation_method": "equal_split_across_available_categories",
            "classification_precedence": ["source", "inferred", "unclassified"],
            "inference_confidence_threshold": 0.65,
            "items": items,
            "classification_coverage": {
                "qualifying_grant_count": qualifying,
                "classified_grant_count": classified,
                "unclassified_grant_count": int(coverage["unclassified"] or 0),
                "classified_percentage": round(classified / qualifying * 100, 2) if qualifying else 0.0,
                "source_classified_grant_count": source_count,
                "inferred_classified_grant_count": inferred_count,
                "source_percentage": round(source_count / qualifying * 100, 2) if qualifying else 0.0,
                "inferred_percentage": round(inferred_count / qualifying * 100, 2) if qualifying else 0.0,
                "multiple_programme_area_grant_count": int(coverage["multiple_count"] or 0),
                "invalid_source_label_count": int(coverage["invalid_label"] or 0),
                "low_confidence_inference_count": int(coverage["low_confidence"] or 0),
            },
            "qualifying_amount": number_value(coverage["qualifying_amount"]) or 0.0,
            "allocated_amount": allocated,
            "excluded": {
                "missing_date": 0,
                "invalid_date": 0,
                "missing_amount": 0,
                "invalid_amount": 0,
                "negative_amount": int(coverage["negative"] or 0),
                "unsupported_currency": 0,
                "currency_filtered": 0,
                "unsupported_source": 0,
                "outside_period": 0,
            },
            "zero_amount_count": int(coverage["zero"] or 0),
            "last_refreshed_at": utc_now(),
            "source": [str(row[0]) for row in sources],
            "data_mode": "postgresql_materialized_grant_facts",
            "amount_policy": _amount_policy(number_value(coverage["maximum"])),
            "scope": _scope(),
        }

    async def summary(self) -> dict[str, Any]:
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters = {"dataset_version": dataset_version}
            total = await session.scalar(
                text("SELECT COUNT(*) FROM grants WHERE dataset_version=:dataset_version"),
                parameters,
            )
            currencies = await session.execute(
                text(
                    """
                    SELECT DISTINCT currency FROM grants
                    WHERE dataset_version=:dataset_version AND currency IS NOT NULL
                    ORDER BY currency
                    """
                ),
                parameters,
            )
            donor_rows = await session.execute(
                text(
                    """
                    SELECT funding_charity_id, funding_name, currency,
                           SUM(amount) AS total_amount, COUNT(*) AS grant_count
                    FROM grants WHERE dataset_version=:dataset_version
                      AND amount>0 AND currency IS NOT NULL
                    GROUP BY funding_charity_id, funding_name, currency
                    ORDER BY total_amount DESC NULLS LAST, funding_name LIMIT 10
                    """
                ),
                parameters,
            )
            recipient_rows = await session.execute(
                text(
                    """
                    SELECT recipient_charity_id, recipient_name, currency,
                           SUM(amount) AS total_amount, COUNT(*) AS grant_count
                    FROM grants WHERE dataset_version=:dataset_version
                      AND amount>0 AND currency IS NOT NULL
                    GROUP BY recipient_charity_id, recipient_name, currency
                    ORDER BY total_amount DESC NULLS LAST, recipient_name LIMIT 10
                    """
                ),
                parameters,
            )
        ranking = lambda rows: [
            {
                "organization_id": row[0],
                "organization_name": str(row[1] or "Unknown"),
                "total_amount": float(row[3] or 0),
                "currency": str(row[2]),
                "grant_count": int(row[4]),
            }
            for row in rows
        ]
        return {
            "status": "available" if total else "no_transactions_found",
            "total_grant_count": int(total or 0),
            "currencies": [str(row[0]) for row in currencies],
            "largest_donors": ranking(donor_rows),
            "largest_recipients": ranking(recipient_rows),
            "metadata": {
                "data_mode": "postgresql_active_dataset",
                "source": ["360Giving"],
                "generated_at": utc_now(),
                "record_count": int(total or 0),
                "derivation": "stored grant transactions",
                "limitations": ["Rankings are currency-separated."],
            },
        }

    async def overview(self, **filters: Any) -> dict[str, Any]:
        conditions, parameters, amount_minor, selected_currency = self._filtered_scope(filters)
        valid = VALID_ORIGINAL if parameters["currency"] else VALID_EUR
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            kpis = (
                await session.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS grant_count,
                               COUNT(DISTINCT fact.funding_name) AS funder_count,
                               COUNT(DISTINCT fact.recipient_name) AS recipient_count,
                               SUM(CASE WHEN {valid} THEN {amount_minor} ELSE 0 END) / 100.0
                                   AS total_amount,
                               MIN(fact.award_date) AS date_from,
                               MAX(fact.award_date) AS date_to
                        FROM grant_overview_facts AS fact WHERE {conditions}
                        """
                    ),
                    parameters,
                )
            ).mappings().one()
        map_payload = await self.map(**filters)
        trend_payload = await self.trends(**filters)
        theme_payload = await self.themes(currency=filters.get("currency"), **{
            key: value for key, value in filters.items() if key != "currency"
        })
        return {
            "status": "available" if int(kpis["grant_count"] or 0) else "no_transactions_found",
            "kpis": {
                "grant_count": int(kpis["grant_count"] or 0),
                "funder_count": int(kpis["funder_count"] or 0),
                "recipient_count": int(kpis["recipient_count"] or 0),
                "total_amount": number_value(kpis["total_amount"]) or 0.0,
                "currency": selected_currency,
            },
            "map": map_payload,
            "trends": trend_payload,
            "themes": theme_payload,
            "available_date_range": {
                "from": iso_value(kpis["date_from"]),
                "to": iso_value(kpis["date_to"]),
            },
            "applied_filters": filters,
            "metadata": {"data_mode": "postgresql_materialized_grant_facts"},
        }

    async def drilldown(
        self,
        *,
        selection_type: str,
        selection_value: str,
        **filters: Any,
    ) -> dict[str, Any]:
        conditions, parameters, amount_minor, selected_currency = self._filtered_scope(filters)
        valid = VALID_ORIGINAL if parameters["currency"] else VALID_EUR
        parameters["selection_value"] = selection_value
        if selection_type == "period":
            conditions += " AND to_char(fact.award_date, 'YYYY-MM')=:selection_value"
        elif selection_type == "programme_area":
            conditions += (
                " AND EXISTS (SELECT 1 FROM grant_programme_categories AS selection_category "
                "WHERE selection_category.dataset_version=fact.dataset_version "
                "AND selection_category.grant_id=fact.grant_id "
                "AND selection_category.programme_area=:selection_value)"
            )
        else:
            raise ValueError("Unsupported drill-down selection type")
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            f"""
                            SELECT fact.grant_id, fact.funding_name, fact.recipient_name,
                                   fact.award_date, fact.currency,
                                   CASE WHEN {valid} THEN {amount_minor} / 100.0 END AS amount
                            FROM grant_overview_facts AS fact WHERE {conditions}
                            ORDER BY amount DESC NULLS LAST, fact.grant_id LIMIT 250
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
        funders: dict[str, dict[str, Any]] = {}
        recipients: dict[str, dict[str, Any]] = {}
        for row in rows:
            amount = number_value(row.get("amount")) or 0.0
            for target, name in ((funders, row.get("funding_name")), (recipients, row.get("recipient_name"))):
                label = str(name or "Unknown")
                item = target.setdefault(label, {"name": label, "grant_count": 0, "total_amount": 0.0})
                item["grant_count"] += 1
                item["total_amount"] += amount
        ranked = lambda values: sorted(
            values.values(), key=lambda item: (-item["total_amount"], item["name"])
        )[:25]
        return {
            "status": "available" if rows else "no_transactions_found",
            "selection": {"type": selection_type, "value": selection_value, "label": selection_value},
            "summary": {
                "grant_count": len(rows),
                "total_amount": round(sum(number_value(row.get("amount")) or 0 for row in rows), 2),
                "currency": selected_currency,
            },
            "funders": ranked(funders),
            "recipients": ranked(recipients),
            "countries": [],
            "grants": [
                {
                    "grant_id": row["grant_id"],
                    "funding_name": row.get("funding_name"),
                    "recipient_name": row.get("recipient_name"),
                    "award_date": iso_value(row.get("award_date")),
                    "amount": number_value(row.get("amount")),
                    "currency": selected_currency,
                }
                for row in rows
            ],
            "metadata": {
                "data_mode": "postgresql_materialized_grant_facts",
                "bounded_grant_limit": 250,
            },
        }
