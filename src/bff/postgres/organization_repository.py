"""Async PostgreSQL organization, grant-history, Sankey and score queries."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text

from bff.postgres.base import PostgresRepository, iso_value, json_value, number_value, row_dict, utc_now
from scoring.engine import load_score_configuration, score_relevance


def _registered_number(row: Mapping[str, Any], raw: Mapping[str, Any]) -> int:
    """Extract an organization's registered charity number from a row."""
    value = raw.get("registered_charity_number", row["charity_id"])
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(row["charity_id"])


def _base_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project an organization row into the shared list-item shape."""
    raw = json_value(row.get("raw_source_data"), {})
    details = json_value(raw.get("all_details"), {})
    return {
        "registered_charity_number": _registered_number(row, raw),
        "suffix": int(raw.get("suffix") or 0),
        "link": raw.get("link") or row.get("source_url"),
        "charity_name": str(row["name"]),
        "reg_status": str(details.get("reg_status") or "Unknown"),
        "reporting_status": details.get("reporting_status"),
        "removal_reason": details.get("removal_reason"),
        "latest_income": number_value(
            details.get("latest_income", row.get("annual_income"))
        ),
        "latest_expenditure": number_value(
            details.get("latest_expenditure", row.get("annual_expenditure"))
        ),
        "programme_areas_source": json_value(row.get("programme_areas_source"), []),
        "programme_areas_inferred": json_value(row.get("programme_areas_inferred"), []),
        "geographic_focus_source": json_value(row.get("geographic_focus_source"), []),
        "geographic_focus_inferred": json_value(row.get("geographic_focus_inferred"), []),
        "headquarters_country": row.get("headquarters_country"),
        "headquarters_region": row.get("headquarters_region"),
        "programme_area_review_required": bool(row.get("programme_area_review_required")),
        "geography_review_required": bool(row.get("geography_review_required")),
        "enrichment_rule_version": row.get("enrichment_rule_version"),
        "organization_type": str(row.get("organization_type") or "unknown"),
        "primary_source": row.get("primary_source"),
        "source_names": json_value(row.get("source_names"), []),
        "source_record_id": row.get("source_record_id"),
        "source_url": row.get("source_url"),
        "transaction_coverage": str(row.get("transaction_coverage") or "unknown"),
    }


class OrganizationRepository(PostgresRepository):
    """Async organization, grant-history, Sankey and score queries."""
    async def list(
        self,
        *,
        search: Optional[str] = None,
        reg_status: Optional[str] = None,
        tag: Optional[str] = None,
        region: Optional[str] = None,
        size: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        foundation_regions: Optional[Sequence[str]] = None,
        funding_regions: Optional[Sequence[str]] = None,
        sources: Optional[Sequence[str]] = None,
        min_annual_giving: Optional[float] = None,
        max_annual_giving: Optional[float] = None,
        min_avg_grant_size: Optional[float] = None,
        max_avg_grant_size: Optional[float] = None,
        include_score: bool = False,
        sort: str = "name_asc",
        skip: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search, filter, sort and paginate organization profiles."""
        limit = min(max(int(limit), 1), 100)
        skip = min(max(int(skip), 0), 1_000_000)
        conditions = ["charity.dataset_version=:dataset_version"]
        parameters: dict[str, Any] = {
            "search": f"%{str(search or '').strip()}%",
            "reg_status": reg_status,
            "tags": list(dict.fromkeys([*(tags or []), *([tag] if tag else [])])),
            "foundation_regions": list(foundation_regions or []),
            "funding_regions": list(funding_regions or []),
            "sources": list(sources or []),
            "min_annual_giving": min_annual_giving,
            "max_annual_giving": max_annual_giving,
            "min_avg_grant_size": min_avg_grant_size,
            "max_avg_grant_size": max_avg_grant_size,
            "limit": 500 if sort == "score_desc" else limit,
            "offset": 0 if sort == "score_desc" else skip,
        }
        if search and search.strip():
            conditions.append("(charity.name ILIKE :search OR charity.normalized_name ILIKE :search)")
        if reg_status:
            conditions.append(
                "COALESCE(charity.raw_source_data #>> '{all_details,reg_status}', 'Unknown')=:reg_status"
            )
        if parameters["tags"]:
            conditions.append(
                """
                EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(
                        COALESCE(charity.programme_areas_source, '[]'::jsonb)
                        || COALESCE(charity.programme_areas_inferred, '[]'::jsonb)
                    ) AS category(value)
                    WHERE category.value=ANY(CAST(:tags AS text[]))
                )
                """
            )
        if region:
            parameters["region"] = region
            conditions.append(
                "(charity.headquarters_region=:region OR charity.headquarters_country=:region)"
            )
        if parameters["foundation_regions"]:
            conditions.append(
                "charity.headquarters_region=ANY(CAST(:foundation_regions AS text[]))"
            )
        if parameters["sources"]:
            conditions.append("charity.primary_source=ANY(CAST(:sources AS text[]))")
        if size:
            ranges = {
                "small": (0, 100_000),
                "medium": (100_000, 1_000_000),
                "large": (1_000_000, None),
            }
            if size in ranges:
                low, high = ranges[size]
                parameters.update({"size_low": low, "size_high": high})
                conditions.append("COALESCE(charity.annual_income, 0)>=:size_low")
                if high is not None:
                    conditions.append("COALESCE(charity.annual_income, 0)<:size_high")
        giving_expression = "COALESCE(grant_stats.annual_giving, 0)"
        average_expression = "grant_stats.average_grant"
        if min_annual_giving is not None:
            conditions.append(f"{giving_expression}>=:min_annual_giving")
        if max_annual_giving is not None:
            conditions.append(f"{giving_expression}<=:max_annual_giving")
        if min_avg_grant_size is not None:
            conditions.append(f"{average_expression}>=:min_avg_grant_size")
        if max_avg_grant_size is not None:
            conditions.append(f"{average_expression}<=:max_avg_grant_size")
        if parameters["funding_regions"]:
            conditions.append(
                """
                EXISTS (
                    SELECT 1 FROM grants AS funding_grant
                    JOIN grant_beneficiary_countries AS funding_country
                      ON funding_country.dataset_version=funding_grant.dataset_version
                     AND funding_country.grant_id=funding_grant.grant_id
                    WHERE funding_grant.dataset_version=charity.dataset_version
                      AND funding_grant.funding_charity_id=charity.charity_id
                      AND funding_country.country_name=ANY(CAST(:funding_regions AS text[]))
                )
                """
            )
        order = {
            "name_asc": "charity.name ASC, charity.charity_id ASC",
            "income_desc": "charity.annual_income DESC NULLS LAST, charity.charity_id ASC",
            "score_desc": "charity.name ASC, charity.charity_id ASC",
        }.get(sort, "charity.name ASC, charity.charity_id ASC")
        sql = text(
            f"""
            SELECT charity.*, grant_stats.annual_giving, grant_stats.average_grant,
                   grant_stats.grant_count
            FROM charities AS charity
            LEFT JOIN LATERAL (
                SELECT SUM(CASE WHEN grant_row.amount_eur>0 THEN grant_row.amount_eur ELSE 0 END)
                           AS annual_giving,
                       AVG(grant_row.amount_eur) FILTER (WHERE grant_row.amount_eur>0)
                           AS average_grant,
                       COUNT(*) AS grant_count
                FROM grants AS grant_row
                WHERE grant_row.dataset_version=charity.dataset_version
                  AND grant_row.funding_charity_id=charity.charity_id
            ) AS grant_stats ON TRUE
            WHERE {' AND '.join(conditions)}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """
        )
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            rows = [dict(row) for row in (await session.execute(sql, parameters)).mappings()]
        items = [_base_item(row) for row in rows]
        if include_score or sort == "score_desc":
            config = load_score_configuration()
            for item, row in zip(items, rows):
                score = score_relevance(
                    {
                        **item,
                        "annual_expenditure": item["latest_expenditure"],
                    },
                    config.example_target_profile,
                    grant_statistics={
                        "currency": "EUR",
                        "average_amount": number_value(row.get("average_grant")),
                        "grant_count": int(row.get("grant_count") or 0),
                    },
                    configuration=config,
                )
                item.update(
                    {
                        "relevance_score": score["score"],
                        "score_confidence": score["confidence"],
                        "score_completeness": score["data_completeness"],
                        "score_target": score["score_target"],
                        "score_version": score["score_version"],
                        "score_configuration_status": score["configuration_status"],
                    }
                )
            if sort == "score_desc":
                items.sort(
                    key=lambda item: (
                        -(item.get("relevance_score") or -1),
                        item["charity_name"].casefold(),
                        item["registered_charity_number"],
                    )
                )
                items = items[skip : skip + limit]
        return items

    async def detail(self, organization_id: int) -> Optional[dict[str, Any]]:
        """Return one organization with provenance and enrichment evidence."""
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            row = (
                await session.execute(
                    text(
                        """
                        SELECT * FROM charities
                        WHERE dataset_version=:dataset_version
                          AND (
                              charity_id=:organization_id
                              OR raw_source_data->>'registered_charity_number'=:registered
                          )
                        ORDER BY CASE WHEN charity_id=:organization_id THEN 0 ELSE 1 END
                        LIMIT 1
                        """
                    ),
                    {
                        "dataset_version": dataset_version,
                        "organization_id": organization_id,
                        "registered": str(organization_id),
                    },
                )
            ).mappings().first()
        if not row:
            return None
        raw_row = dict(row)
        raw = json_value(raw_row.get("raw_source_data"), {})
        base = _base_item(raw_row)
        all_details = json_value(raw.get("all_details"), {})
        all_details = {
            "organisation_number": int(all_details.get("organisation_number") or raw_row["charity_id"]),
            "reg_charity_number": int(
                all_details.get("reg_charity_number") or base["registered_charity_number"]
            ),
            "group_subsid_suffix": int(all_details.get("group_subsid_suffix") or base["suffix"]),
            "charity_name": str(all_details.get("charity_name") or raw_row["name"]),
            "reg_status": str(all_details.get("reg_status") or base["reg_status"]),
            **all_details,
        }
        return {
            "registered_charity_number": base["registered_charity_number"],
            "suffix": base["suffix"],
            "link": base["link"],
            "all_details": all_details,
            "assets_liabilities": json_value(raw.get("assets_liabilities"), []),
            "primary_grants": raw.get("primary_grants"),
            "who_what_how": json_value(raw.get("who_what_how"), []),
            "financial_history": json_value(raw.get("financial_history"), []),
            "programme_areas_source": base["programme_areas_source"],
            "programme_areas_inferred": base["programme_areas_inferred"],
            "programme_area_scores": json_value(raw_row.get("programme_area_scores"), {}),
            "programme_area_method": raw_row.get("programme_area_method"),
            "programme_area_evidence": json_value(raw_row.get("programme_area_evidence"), []),
            "programme_area_review_required": base["programme_area_review_required"],
            "geographic_focus_source": base["geographic_focus_source"],
            "geographic_focus_inferred": base["geographic_focus_inferred"],
            "headquarters_country": base["headquarters_country"],
            "headquarters_region": base["headquarters_region"],
            "geography_method": raw_row.get("geography_method"),
            "geography_confidence": number_value(raw_row.get("geography_confidence")),
            "geography_evidence": json_value(raw_row.get("geography_evidence"), []),
            "geography_review_required": base["geography_review_required"],
            "enrichment_rule_version": base["enrichment_rule_version"],
            "organization_type": base["organization_type"],
            "primary_source": base["primary_source"],
            "source_names": base["source_names"],
            "source_record_id": base["source_record_id"],
            "source_url": base["source_url"],
            "source_records": json_value(raw_row.get("source_records"), []),
            "ingestion_timestamp": iso_value(raw_row.get("ingestion_timestamp")),
            "transaction_coverage": base["transaction_coverage"],
            "deduplication_status": raw_row.get("deduplication_status"),
            "deduplication_candidates": json_value(raw_row.get("deduplication_candidates"), []),
        }

    async def stats(self) -> dict[str, Any]:
        """Return dataset KPIs, source counts and organization-type counts."""
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            row = (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(*) AS total_charities,
                               COUNT(*) FILTER (
                                   WHERE COALESCE(raw_source_data #>> '{all_details,reg_status}', '')
                                         NOT ILIKE '%removed%'
                               ) AS active_charities,
                               COUNT(*) FILTER (
                                   WHERE COALESCE(raw_source_data #>> '{all_details,reg_status}', '')
                                         ILIKE '%removed%'
                               ) AS removed_charities,
                               COALESCE(AVG(annual_income), 0) AS average_income,
                               COALESCE(AVG(annual_expenditure), 0) AS average_expenditure
                        FROM charities WHERE dataset_version=:dataset_version
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
            ).mappings().one()
            grant_count = await session.scalar(
                text("SELECT COUNT(*) FROM grants WHERE dataset_version=:dataset_version"),
                {"dataset_version": dataset_version},
            )
            sources = (
                await session.execute(
                    text(
                        """
                        SELECT COALESCE(primary_source, 'Unknown') AS source, COUNT(*) AS count
                        FROM charities WHERE dataset_version=:dataset_version
                        GROUP BY COALESCE(primary_source, 'Unknown') ORDER BY source
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
            ).all()
            types = (
                await session.execute(
                    text(
                        """
                        SELECT COALESCE(organization_type, 'unknown') AS type, COUNT(*) AS count
                        FROM charities WHERE dataset_version=:dataset_version
                        GROUP BY COALESCE(organization_type, 'unknown') ORDER BY type
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
            ).all()
        return {
            **row_dict(row),
            "total_grants": int(grant_count or 0),
            "data_mode": "postgresql_active_dataset",
            "source": [str(item[0]) for item in sources],
            "source_counts": {str(item[0]): int(item[1]) for item in sources},
            "organization_type_counts": {str(item[0]): int(item[1]) for item in types},
        }

    @staticmethod
    def _grant_item(row: Mapping[str, Any]) -> dict[str, Any]:
        """Project a grant row into the API grant shape, preserving source facts."""
        return {
            "grant_id": str(row["grant_id"]),
            "funding_charity_id": row.get("funding_charity_id"),
            "funding_name": row.get("funding_name"),
            "funding_org_source_id": row.get("funding_org_source_id"),
            "recipient_name": str(row.get("recipient_name") or "Unknown recipient"),
            "recipient_charity_id": row.get("recipient_charity_id"),
            "recipient_org_source_id": row.get("recipient_org_source_id"),
            "amount": number_value(row.get("amount")),
            "amount_eur": number_value(row.get("amount_eur")),
            "exchange_rate": number_value(row.get("exchange_rate")),
            "exchange_rate_date": row.get("exchange_rate_date"),
            "exchange_rate_source": row.get("exchange_rate_source"),
            "conversion_status": row.get("conversion_status"),
            "currency": str(row.get("currency") or "UNK"),
            "description": str(row.get("description") or ""),
            "date": str(row.get("award_date") or ""),
            "recipient_region": row.get("recipient_region"),
            "beneficiary_geography": [row["beneficiary_geography"]]
            if row.get("beneficiary_geography") else [],
            "tags": json_value(row.get("tags"), []),
            "source": row.get("source"),
            "source_record_id": row.get("source_record_id"),
            "source_url": row.get("source_url"),
            "programme_area_source": [row["programme_area_source"]]
            if row.get("programme_area_source") else [],
            "programme_area_inferred": [row["programme_area_inferred"]]
            if row.get("programme_area_inferred") else [],
            "programme_area_scores": json_value(row.get("programme_area_scores"), {}),
            "programme_area_method": row.get("programme_area_method"),
            "programme_area_evidence": json_value(row.get("programme_area_evidence"), []),
            "programme_area_review_required": bool(row.get("programme_area_review_required")),
            "beneficiary_geography_normalized": json_value(row.get("countries"), []),
            "geographic_focus_inferred": [row["geographic_focus_inferred"]]
            if row.get("geographic_focus_inferred") else [],
            "geography_method": row.get("geography_method"),
            "geography_confidence": number_value(row.get("geography_confidence")),
            "geography_evidence": json_value(row.get("geography_evidence"), []),
            "geography_review_required": bool(row.get("geography_review_required")),
            "enrichment_rule_version": row.get("enrichment_rule_version"),
        }

    async def grants(self, organization_id: int, role: str) -> dict[str, Any]:
        """Return observed transactions and explicit coverage status for one organization."""
        conditions = {
            "all": "(grant_row.funding_charity_id=:organization_id OR grant_row.recipient_charity_id=:organization_id)",
            "funder": "grant_row.funding_charity_id=:organization_id",
            "recipient": "grant_row.recipient_charity_id=:organization_id",
        }
        if role not in conditions:
            raise ValueError("role must be all, funder, or recipient")
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            result = await session.execute(
                text(
                    f"""
                    SELECT grant_row.*,
                           COALESCE(
                               jsonb_agg(DISTINCT jsonb_build_object(
                                   'code', country.country_code,
                                   'name', country.country_name
                               )) FILTER (WHERE country.country_code IS NOT NULL),
                               '[]'::jsonb
                           ) AS countries
                    FROM grants AS grant_row
                    LEFT JOIN grant_beneficiary_countries AS country
                      ON country.dataset_version=grant_row.dataset_version
                     AND country.grant_id=grant_row.grant_id
                    WHERE grant_row.dataset_version=:dataset_version
                      AND {conditions[role]}
                    GROUP BY grant_row.dataset_version, grant_row.grant_id
                    ORDER BY grant_row.award_date DESC, grant_row.grant_id
                    LIMIT 5000
                    """
                ),
                {"dataset_version": dataset_version, "organization_id": organization_id},
            )
            rows = [dict(row) for row in result.mappings()]
        currencies = sorted({str(row["currency"]) for row in rows if row.get("currency")})
        return {
            "status": "available" if rows else "no_transactions_found",
            "organization_id": organization_id,
            "role": role,
            "transaction_coverage": "observed_transactions_only",
            "grant_count": len(rows),
            "currencies": currencies,
            "grants": [self._grant_item(row) for row in rows],
            "metadata": {
                "data_mode": "postgresql_active_dataset",
                "source": sorted({str(row["source"]) for row in rows if row.get("source")}),
                "generated_at": utc_now(),
                "record_count": len(rows),
                "derivation": "stored grant transactions",
                "limitations": ["Response is bounded to 5000 transactions." ]
                if len(rows) == 5000 else [],
            },
        }

    @staticmethod
    def _entity_id(role: str, charity_id: Any, source_id: Any, name: Any) -> str:
        """Derive a stable Sankey node identifier for a funder or recipient."""
        if charity_id is not None:
            return f"organization:{int(charity_id)}"
        material = f"{role}|{source_id or ''}|{str(name or '').casefold()}"
        return f"source:{role}:{sha256(material.encode()).hexdigest()[:20]}"

    async def sankey(
        self,
        organization_id: int,
        *,
        currency: Optional[str] = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Return bounded donor-to-recipient flows for one organization."""
        selected_currency = str(currency or "EUR").upper()
        amount_column = "amount_eur" if currency is None or selected_currency == "EUR" else "amount"
        currency_condition = "" if amount_column == "amount_eur" else "AND currency=:currency"
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            f"""
                            SELECT grant_id, funding_charity_id, funding_name,
                                   funding_org_source_id, recipient_charity_id,
                                   recipient_name, recipient_org_source_id,
                                   {amount_column} AS selected_amount,
                                   currency, source
                            FROM grants
                            WHERE dataset_version=:dataset_version
                              AND (funding_charity_id=:organization_id
                                   OR recipient_charity_id=:organization_id)
                              AND {amount_column}>0 {currency_condition}
                            ORDER BY {amount_column} DESC, grant_id
                            LIMIT 10000
                            """
                        ),
                        {
                            "dataset_version": dataset_version,
                            "organization_id": organization_id,
                            "currency": selected_currency,
                        },
                    )
                ).mappings()
            ]
        aggregated: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"value": 0.0, "grant_count": 0}
        )
        nodes: dict[str, dict[str, Any]] = {}
        for row in rows:
            source = self._entity_id(
                "donor", row.get("funding_charity_id"),
                row.get("funding_org_source_id"), row.get("funding_name"),
            )
            target = self._entity_id(
                "recipient", row.get("recipient_charity_id"),
                row.get("recipient_org_source_id"), row.get("recipient_name"),
            )
            if source == target:
                continue
            nodes.setdefault(source, {"id": source, "label": row.get("funding_name") or "Unnamed donor", "role": "donor"})
            nodes.setdefault(target, {"id": target, "label": row.get("recipient_name") or "Unnamed recipient", "role": "recipient"})
            aggregate = aggregated[(source, target)]
            aggregate["value"] += float(row["selected_amount"])
            aggregate["grant_count"] += 1
        links = [
            {
                "source": source,
                "target": target,
                "value": round(values["value"], 2),
                "currency": selected_currency,
                "grant_count": values["grant_count"],
            }
            for (source, target), values in aggregated.items()
        ]
        links.sort(key=lambda item: (-item["value"], item["source"], item["target"]))
        truncated = len(links) > limit
        links = links[:limit]
        node_ids = {item["source"] for item in links} | {item["target"] for item in links}
        included = sum(item["grant_count"] for item in links)
        return {
            "status": "available" if links else "no_transactions_found",
            "nodes": [nodes[node_id] for node_id in sorted(node_ids)],
            "links": links,
            "metadata": {
                "source": sorted({str(row["source"]) for row in rows if row.get("source")}),
                "generated_at": utc_now(),
                "grant_count": len(rows),
                "included_grant_count": included,
                "excluded_grant_count": max(0, len(rows) - included),
                "excluded_reasons": {"truncated": max(0, len(rows) - included)} if truncated else {},
                "included_value": round(sum(item["value"] for item in links), 2),
                "currencies": [selected_currency],
                "selected_currency": selected_currency,
                "conversion_method": "ecb_historic_reference_rate" if amount_column == "amount_eur" else "none",
                "filters_applied": {"organization_id": organization_id, "limit": limit},
                "truncation_applied": truncated,
            },
        }

    async def score(
        self,
        organization_id: int,
        target_profile: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Calculate the experimental relevance score against a target profile."""
        organization = await self.detail(organization_id)
        if not organization:
            raise KeyError(organization_id)
        config = load_score_configuration()
        profile = dict(target_profile or config.example_target_profile)
        requested_currency = str(profile.get("currency") or "").upper()
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            if requested_currency == "EUR":
                grant_row = (
                    await session.execute(
                        text(
                            """
                            SELECT 'EUR' AS currency, AVG(amount_eur) AS average_amount,
                                   COUNT(*) AS grant_count
                            FROM grants WHERE dataset_version=:dataset_version
                              AND funding_charity_id=:organization_id AND amount_eur>0
                            """
                        ),
                        {"dataset_version": dataset_version, "organization_id": organization_id},
                    )
                ).mappings().one()
            else:
                grant_row = (
                    await session.execute(
                        text(
                            """
                            SELECT currency, AVG(amount) AS average_amount, COUNT(*) AS grant_count
                            FROM grants WHERE dataset_version=:dataset_version
                              AND funding_charity_id=:organization_id AND amount>0
                              AND (:currency='' OR currency=:currency)
                            GROUP BY currency ORDER BY COUNT(*) DESC, currency LIMIT 1
                            """
                        ),
                        {
                            "dataset_version": dataset_version,
                            "organization_id": organization_id,
                            "currency": requested_currency,
                        },
                    )
                ).mappings().first()
            programme_rows = await session.execute(
                text(
                    """
                    SELECT DISTINCT category.programme_area
                    FROM grants AS grant_row
                    JOIN grant_programme_categories AS category
                      ON category.dataset_version=grant_row.dataset_version
                     AND category.grant_id=grant_row.grant_id
                    WHERE grant_row.dataset_version=:dataset_version
                      AND grant_row.funding_charity_id=:organization_id
                    """
                ),
                {"dataset_version": dataset_version, "organization_id": organization_id},
            )
            geography_rows = await session.execute(
                text(
                    """
                    SELECT DISTINCT country.country_name
                    FROM grants AS grant_row
                    JOIN grant_beneficiary_countries AS country
                      ON country.dataset_version=grant_row.dataset_version
                     AND country.grant_id=grant_row.grant_id
                    WHERE grant_row.dataset_version=:dataset_version
                      AND grant_row.funding_charity_id=:organization_id
                    """
                ),
                {"dataset_version": dataset_version, "organization_id": organization_id},
            )
        score_input = {
            **organization,
            "annual_expenditure": organization["all_details"].get("latest_expenditure"),
            "observed_grant_programme_areas": [row[0] for row in programme_rows],
            "observed_beneficiary_geographies": [row[0] for row in geography_rows],
        }
        grant_statistics = (
            {
                "currency": grant_row["currency"],
                "average_amount": number_value(grant_row["average_amount"]),
                "grant_count": int(grant_row["grant_count"] or 0),
            }
            if grant_row and grant_row["average_amount"] is not None
            else {}
        )
        return score_relevance(
            score_input,
            profile,
            grant_statistics=grant_statistics,
            configuration=config,
        )
