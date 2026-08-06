"""Source-funder reads, explicit link overrides and profile-cache jobs."""

from __future__ import annotations

import json
import math
from typing import Any, Optional, Sequence
import uuid

from sqlalchemy import text

from bff.postgres.base import PostgresRepository, iso_value, number_value, utc_now


class SourceFunderRepository(PostgresRepository):
    """Source-funder reads, explicit link overrides and profile-cache jobs."""
    @staticmethod
    def _can_use_materialization(filters: dict[str, Any]) -> bool:
        """Report whether the applied filters permit reading the narrow fact table."""
        return not any(
            filters.get(name)
            for name in (
                "date_from",
                "date_to",
                "beneficiary_geographies",
                "programme_areas",
                "donor",
                "recipient",
                "sources",
            )
        )

    @staticmethod
    def _item(row: dict[str, Any], rank: Optional[int] = None) -> dict[str, Any]:
        """Project a source-funder row into the API list-item shape."""
        linked_profile = row.get("effective_profile_id")
        link_status = "linked" if linked_profile is not None else "observed_only"
        amount = number_value(row.get("selected_amount"))
        return {
            "rank": rank,
            "kind": "source_funder",
            "identity": {
                "source_namespace": row.get("source_namespace"),
                "source_organization_id": row.get("source_organization_id"),
            },
            "source_funder_key": str(row["source_funder_key"]),
            "display_name": str(row.get("display_name") or "Unknown source funder"),
            "identity_method": str(row.get("identity_method") or "unknown"),
            "source_ids": list(row.get("source_ids") or []),
            "sources": list(row.get("sources") or []),
            "source_only": linked_profile is None,
            "linked_directory_profile": (
                {
                    "charity_id": int(linked_profile),
                    "name": row.get("profile_name"),
                }
                if linked_profile is not None else None
            ),
            "profile_link": {
                "status": link_status,
                "mode": row.get("override_mode") or "source_observed",
                "revision": int(row.get("override_revision") or 0),
            },
            "evidence_sources": list(row.get("sources") or []),
            "activity": {
                "grant_count": int(row.get("grant_count") or 0),
                "distinct_recipient_count": int(row.get("recipient_count") or 0),
                "first_award_date": iso_value(row.get("first_award_date")),
                "latest_award_date": iso_value(row.get("latest_award_date")),
            },
            "observed_activity": {
                "beneficiary_country": row.get("country_code"),
            },
            "observed_funding": {
                "amount": amount,
                "currency": row.get("selected_currency"),
                "included_grant_count": int(row.get("included_count") or 0),
                "excluded_multi_country_grant_count": int(row.get("multi_country_count") or 0),
                "excluded_multi_country_amount": 0.0,
                "excluded_conversion_grant_count": int(row.get("conversion_excluded") or 0),
                "excluded_missing_amount_grant_count": int(row.get("missing_count") or 0),
                "excluded_invalid_amount_grant_count": int(row.get("invalid_count") or 0),
                "excluded_negative_amount_grant_count": int(row.get("negative_count") or 0),
                "fallback_original_amount": number_value(row.get("fallback_amount")),
                "fallback_original_currency": row.get("fallback_currency"),
                "fallback_original_grant_count": int(row.get("fallback_count") or 0),
            },
            "amount_policy": {
                "basis": "eur_converted" if row.get("selected_currency") == "EUR" else "original_currency",
                "multi_country": "counted_once_per_selected_country_scope",
                "negative": "excluded_and_reported",
            },
            "leading_programme_areas": [],
            "representative_source_url": row.get("publisher_source_url"),
        }

    @staticmethod
    def _conditions(filters: dict[str, Any]) -> tuple[str, dict[str, Any], str, str]:
        """Translate the applied grant scope into SQL conditions."""
        currency = str(filters.get("currency") or "").upper() or None
        conditions = [
            "fact.dataset_version=:dataset_version",
            "fact.country_code=:beneficiary_country",
        ]
        parameters: dict[str, Any] = {
            "beneficiary_country": str(filters["beneficiary_country"]).upper(),
            "currency": currency,
            "date_from": filters.get("date_from"),
            "date_to": filters.get("date_to"),
            "beneficiary_geographies": list(filters.get("beneficiary_geographies") or []),
            "programme_areas": list(filters.get("programme_areas") or []),
            "donor": f"%{str(filters.get('donor') or '').strip()}%",
            "recipient": f"%{str(filters.get('recipient') or '').strip()}%",
            "sources": list(filters.get("sources") or []),
            "search": f"%{str(filters.get('search') or '').strip()}%",
        }
        if currency:
            conditions.append("fact.currency=:currency")
            amount = "fact.original_amount_minor"
            valid = "fact.original_amount_status NOT IN ('negative','invalid','missing')"
            selected_currency = currency
        else:
            amount = "fact.eur_amount_minor"
            valid = "fact.eur_amount_status NOT IN ('missing','invalid')"
            selected_currency = "EUR"
        if parameters["date_from"]:
            conditions.append("fact.award_date>=CAST(:date_from AS date)")
        if parameters["date_to"]:
            conditions.append("fact.award_date<=CAST(:date_to AS date)")
        if parameters["beneficiary_geographies"]:
            conditions.append(
                "fact.country_name=ANY(CAST(:beneficiary_geographies AS text[]))"
            )
        if parameters["programme_areas"]:
            conditions.append(
                """
                EXISTS (
                    SELECT 1 FROM grant_programme_categories AS category
                    WHERE category.dataset_version=fact.dataset_version
                      AND category.grant_id=fact.grant_id
                      AND category.programme_area=ANY(CAST(:programme_areas AS text[]))
                )
                """
            )
        if filters.get("donor"):
            conditions.append("fact.display_name ILIKE :donor")
        if filters.get("recipient"):
            conditions.append("fact.recipient_name ILIKE :recipient")
        if parameters["sources"]:
            conditions.append("fact.source_namespace=ANY(CAST(:sources AS text[]))")
        if filters.get("search"):
            conditions.append(
                "(fact.display_name ILIKE :search OR fact.source_funder_key ILIKE :search)"
            )
        return " AND ".join(conditions), parameters, amount, selected_currency

    async def list(
        self,
        *,
        beneficiary_country: str,
        currency: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        beneficiary_geographies: Optional[Sequence[str]] = None,
        programme_areas: Optional[Sequence[str]] = None,
        donor: Optional[str] = None,
        recipient: Optional[str] = None,
        sources: Optional[Sequence[str]] = None,
        search: Optional[str] = None,
        profile_status: str = "all",
        sort: str = "largest_observed_funding",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """Return the filtered, paginated observed-donor ranking."""
        if self._can_use_materialization(locals()):
            return await self._materialized_list(
                beneficiary_country=beneficiary_country,
                currency=currency,
                search=search,
                profile_status=profile_status,
                sort=sort,
                page=page,
                page_size=page_size,
            )
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 100)
        filters = locals().copy()
        conditions, parameters, amount, selected_currency = self._conditions(filters)
        valid = (
            "fact.original_amount_status NOT IN ('negative','invalid','missing')"
            if currency else "fact.eur_amount_status NOT IN ('missing','invalid')"
        )
        profile_condition = {
            "all": "TRUE",
            "linked": "effective_profile_id IS NOT NULL",
            "observed_only": "effective_profile_id IS NULL",
        }.get(profile_status)
        if not profile_condition:
            raise ValueError("Unsupported profile status")
        order = {
            "largest_observed_funding": "selected_amount DESC NULLS LAST, source_funder_key",
            "most_grants": "grant_count DESC, source_funder_key",
            "most_recently_active": "latest_award_date DESC NULLS LAST, source_funder_key",
            "most_active": "grant_count DESC, latest_award_date DESC NULLS LAST, source_funder_key",
            "most_recent": "latest_award_date DESC NULLS LAST, source_funder_key",
        }.get(sort)
        if not order:
            raise ValueError("Unsupported source-funder sort")
        parameters.update(
            {
                "limit": page_size,
                "offset": (page - 1) * page_size,
                "selected_currency": selected_currency,
            }
        )
        cte = f"""
            WITH scoped AS (
                SELECT fact.*,
                       COALESCE(
                           NULLIF(fact.source_organization_id, ''),
                           'source-funder-key:' || fact.source_funder_key
                       ) AS override_identifier
                FROM grant_source_funder_facts AS fact
                WHERE {conditions}
            ), aggregated AS (
                SELECT source_funder_key,
                       MIN(source_namespace) AS source_namespace,
                       MIN(source_organization_id) AS source_organization_id,
                       MIN(display_name) AS display_name,
                       MIN(identity_method) AS identity_method,
                       array_agg(DISTINCT source_organization_id)
                           FILTER (WHERE source_organization_id IS NOT NULL) AS source_ids,
                       array_agg(DISTINCT source_namespace) AS sources,
                       MIN(country_code) AS country_code,
                       COUNT(DISTINCT grant_id) AS grant_count,
                       COUNT(DISTINCT recipient_key) AS recipient_count,
                       MIN(award_date) AS first_award_date,
                       MAX(award_date) AS latest_award_date,
                       SUM(CASE WHEN {valid} THEN {amount} ELSE 0 END) / 100.0
                           AS selected_amount,
                       COUNT(*) FILTER (WHERE {valid}) AS included_count,
                       COUNT(*) FILTER (WHERE country_count>1) AS multi_country_count,
                       COUNT(*) FILTER (
                           WHERE conversion_status IS NULL OR conversion_status='missing'
                       ) AS conversion_excluded,
                       COUNT(*) FILTER (WHERE original_amount_status='missing') AS missing_count,
                       COUNT(*) FILTER (WHERE original_amount_status='invalid') AS invalid_count,
                       COUNT(*) FILTER (WHERE original_amount_status='negative') AS negative_count,
                       SUM(original_amount_minor) FILTER (
                           WHERE original_amount_status NOT IN ('negative','invalid','missing')
                       ) / 100.0 AS fallback_amount,
                       MIN(currency) AS fallback_currency,
                       COUNT(*) FILTER (
                           WHERE original_amount_status NOT IN ('negative','invalid','missing')
                       ) AS fallback_count,
                       MAX(linked_profile_id) AS observed_profile_id,
                       MIN(publisher_source_url) AS publisher_source_url,
                       MIN(override_identifier) AS override_identifier
                FROM scoped AS fact GROUP BY source_funder_key
            ), effective AS (
                SELECT aggregated.*,
                       override.link_mode AS override_mode,
                       override.revision AS override_revision,
                       CASE
                           WHEN override.link_mode IN ('observed_only','unlink','blocked') THEN NULL
                           WHEN override.link_mode='link_profile' THEN override.target_profile_id
                           ELSE aggregated.observed_profile_id
                       END AS effective_profile_id,
                       :selected_currency AS selected_currency
                FROM aggregated
                LEFT JOIN source_funder_link_overrides AS override
                  ON override.source_namespace=aggregated.source_namespace
                 AND override.source_organization_id=aggregated.override_identifier
            )
        """
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            total = await session.scalar(
                text(f"{cte} SELECT COUNT(*) FROM effective WHERE {profile_condition}"),
                parameters,
            )
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            f"""
                            {cte}
                            SELECT effective.*, profile.name AS profile_name
                            FROM effective
                            LEFT JOIN charities AS profile
                              ON profile.dataset_version=:dataset_version
                             AND profile.charity_id=effective.effective_profile_id
                            WHERE {profile_condition}
                            ORDER BY {order} LIMIT :limit OFFSET :offset
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
            range_row = (
                await session.execute(
                    text(
                        f"""
                        SELECT MIN(fact.award_date), MAX(fact.award_date)
                        FROM grant_source_funder_facts AS fact WHERE {conditions}
                        """
                    ),
                    parameters,
                )
            ).one()
            currency_rows = await session.execute(
                text(
                    """
                    SELECT DISTINCT currency FROM grant_source_funder_facts
                    WHERE dataset_version=:dataset_version AND currency IS NOT NULL
                    ORDER BY currency
                    """
                ),
                {"dataset_version": dataset_version},
            )
            country_name = await session.scalar(
                text(
                    """
                    SELECT MIN(country_name) FROM grant_source_funder_facts
                    WHERE dataset_version=:dataset_version AND country_code=:country
                    """
                ),
                {"dataset_version": dataset_version, "country": beneficiary_country.upper()},
            )
        items = [self._item(row, parameters["offset"] + index + 1) for index, row in enumerate(rows)]
        total_items = int(total or 0)
        linked_count = sum(1 for item in items if not item["source_only"])
        return {
            "status": "available" if total_items else "no_transactions_found",
            "country": {
                "code": beneficiary_country.upper(),
                "name": str(country_name or beneficiary_country.upper()),
            },
            "summary": {
                "matching_funder_count": total_items,
                "matching_grant_count": sum(item["activity"]["grant_count"] for item in items),
                "source_only_funder_count": len(items) - linked_count,
                "linked_directory_funder_count": linked_count,
            },
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": math.ceil(total_items / page_size) if total_items else 0,
            },
            "available_date_range": {
                "from": iso_value(range_row[0]),
                "to": iso_value(range_row[1]),
            },
            "available_currencies": [str(row[0]) for row in currency_rows],
            "available_sort_modes": [
                "largest_observed_funding", "most_grants", "most_recently_active",
                "most_active", "most_recent",
            ],
            "applied_filters": {
                "beneficiary_country": beneficiary_country.upper(),
                "currency": currency,
                "search": search,
                "profile_status": profile_status,
                "sort": sort,
            },
            "metadata": {
                "data_mode": "postgresql_source_funder_facts",
                "identity_boundary": "source funders remain distinct from directory profiles",
            },
        }

    async def _materialized_list(
        self,
        *,
        beneficiary_country: str,
        currency: Optional[str],
        search: Optional[str],
        profile_status: str,
        sort: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Serve the donor ranking from the narrow source-funder fact table."""
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 100)
        basis = "original" if currency else "eur_converted"
        selected_currency = str(currency or "EUR").upper()
        profile_condition = {
            "all": "TRUE",
            "linked": "effective_profile_id IS NOT NULL",
            "observed_only": "effective_profile_id IS NULL",
        }.get(profile_status)
        if not profile_condition:
            raise ValueError("Unsupported profile status")
        order = {
            "largest_observed_funding": (
                "selected_amount_minor DESC, source_funder_key"
            ),
            "most_grants": "grant_count DESC, source_funder_key",
            "most_recently_active": (
                "latest_award_date DESC NULLS LAST, source_funder_key"
            ),
            "most_active": (
                "grant_count DESC, latest_award_date DESC NULLS LAST, "
                "source_funder_key"
            ),
            "most_recent": (
                "latest_award_date DESC NULLS LAST, source_funder_key"
            ),
        }.get(sort)
        if not order:
            raise ValueError("Unsupported source-funder sort")
        search_condition = (
            "AND (ranking.display_name ILIKE :search "
            "OR ranking.source_funder_key ILIKE :search)"
            if search else ""
        )
        parameters = {
            "beneficiary_country": beneficiary_country.upper(),
            "basis": basis,
            "currency": selected_currency,
            "search": f"%{str(search or '').strip()}%",
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        cte = f"""
            WITH effective AS (
                SELECT ranking.*,
                       override.link_mode AS override_mode,
                       override.revision AS override_revision,
                       CASE
                           WHEN override.link_mode IN ('observed_only','unlink','blocked')
                               THEN NULL
                           WHEN override.link_mode='link_profile'
                               THEN override.target_profile_id
                           ELSE ranking.observed_profile_id
                       END AS effective_profile_id
                FROM analytics_country_funder_rankings AS ranking
                LEFT JOIN source_funder_link_overrides AS override
                  ON override.source_namespace=ranking.source_namespace
                 AND override.source_organization_id=ranking.override_identifier
                WHERE ranking.dataset_version=:dataset_version
                  AND ranking.amount_basis=:basis AND ranking.currency=:currency
                  AND ranking.country_code=:beneficiary_country
                  {search_condition}
            )
        """
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            materialized = await session.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM materialization_versions
                        WHERE dataset_version=:dataset_version
                          AND materialization_name='dashboard_analytics'
                          AND is_active AND status='active'
                    )
                    """
                ),
                parameters,
            )
            if not materialized:
                raise RuntimeError("Active dashboard materialization is unavailable")
            total = await session.scalar(
                text(f"{cte} SELECT COUNT(*) FROM effective WHERE {profile_condition}"),
                parameters,
            )
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            f"""
                            {cte}
                            SELECT effective.*,
                                   effective.selected_amount_minor / 100.0
                                       AS selected_amount,
                                   effective.fallback_amount_minor / 100.0
                                       AS fallback_amount,
                                   effective.currency AS selected_currency,
                                   profile.name AS profile_name
                            FROM effective
                            LEFT JOIN charities AS profile
                              ON profile.dataset_version=:dataset_version
                             AND profile.charity_id=effective.effective_profile_id
                            WHERE {profile_condition}
                            ORDER BY {order} LIMIT :limit OFFSET :offset
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
            range_row = (
                await session.execute(
                    text(
                        f"""
                        {cte}
                        SELECT MIN(first_award_date), MAX(latest_award_date)
                        FROM effective
                        """
                    ),
                    parameters,
                )
            ).one()
            currency_rows = await session.execute(
                text(
                    """
                    SELECT value FROM analytics_filter_values
                    WHERE dataset_version=:dataset_version AND dimension='currency'
                    ORDER BY value
                    """
                ),
                parameters,
            )
            country_name = await session.scalar(
                text(
                    """
                    SELECT MIN(country_name) FROM analytics_country_funder_rankings
                    WHERE dataset_version=:dataset_version
                      AND country_code=:beneficiary_country
                    """
                ),
                parameters,
            )
        items = [
            self._item(row, parameters["offset"] + index + 1)
            for index, row in enumerate(rows)
        ]
        total_items = int(total or 0)
        linked_count = sum(1 for item in items if not item["source_only"])
        return {
            "status": "available" if total_items else "no_transactions_found",
            "country": {
                "code": beneficiary_country.upper(),
                "name": str(country_name or beneficiary_country.upper()),
            },
            "summary": {
                "matching_funder_count": total_items,
                "matching_grant_count": sum(
                    item["activity"]["grant_count"] for item in items
                ),
                "source_only_funder_count": len(items) - linked_count,
                "linked_directory_funder_count": linked_count,
            },
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": math.ceil(total_items / page_size) if total_items else 0,
            },
            "available_date_range": {
                "from": iso_value(range_row[0]),
                "to": iso_value(range_row[1]),
            },
            "available_currencies": [str(row[0]) for row in currency_rows],
            "available_sort_modes": [
                "largest_observed_funding",
                "most_grants",
                "most_recently_active",
                "most_active",
                "most_recent",
            ],
            "applied_filters": {
                "beneficiary_country": beneficiary_country.upper(),
                "currency": currency,
                "search": search,
                "profile_status": profile_status,
                "sort": sort,
            },
            "metadata": {
                "data_mode": "postgresql_versioned_country_funder_rankings",
                "identity_boundary": (
                    "source funders remain distinct from directory profiles"
                ),
            },
        }

    async def detail(
        self,
        source_funder_key: str,
        **filters: Any,
    ) -> Optional[dict[str, Any]]:
        """Return one source funder's detail, or None when the key is unknown."""
        key = str(source_funder_key or "").strip()
        if not key or len(key) > 500:
            raise ValueError("source_funder_key must be a non-empty canonical key")
        response = await self.list(
            beneficiary_country=filters["beneficiary_country"],
            currency=filters.get("currency"),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
            beneficiary_geographies=filters.get("beneficiary_geographies"),
            programme_areas=filters.get("programme_areas"),
            donor=filters.get("donor"),
            recipient=filters.get("recipient"),
            sources=filters.get("sources"),
            search=key,
            profile_status="all",
            page=1,
            page_size=100,
        )
        item = next((candidate for candidate in response["items"] if candidate["source_funder_key"] == key), None)
        if not item:
            return None
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            basis = "original" if filters.get("currency") else "eur_converted"
            selected_currency = str(filters.get("currency") or "EUR").upper()
            parameters = {
                "dataset_version": dataset_version,
                "key": key,
                "country": str(filters["beneficiary_country"]).upper(),
                "basis": basis,
                "currency": selected_currency,
            }
            recipient_rows = await session.execute(
                text(
                    """
                    SELECT recipient_name, grant_count,
                           total_amount_minor / 100.0 AS amount
                    FROM analytics_funder_relationships
                    WHERE dataset_version=:dataset_version
                      AND amount_basis=:basis AND currency=:currency
                      AND source_funder_key=:key AND country_code=:country
                    ORDER BY rank_within_funder LIMIT 25
                    """
                ),
                parameters,
            )
            grant_rows = await session.execute(
                text(
                    """
                    SELECT fact.grant_id, fact.recipient_name, fact.award_date,
                           fact.eur_amount_minor / 100.0 AS amount, 'EUR' AS currency,
                           fact.original_amount_minor / 100.0 AS original_amount,
                           fact.currency AS original_currency, fact.publisher_source_url,
                           grant_row.description
                    FROM grant_source_funder_facts AS fact
                    JOIN grants AS grant_row USING (dataset_version, grant_id)
                    WHERE fact.dataset_version=:dataset_version
                      AND fact.source_funder_key=:key AND fact.country_code=:country
                    ORDER BY fact.award_date DESC NULLS LAST, fact.grant_id LIMIT 50
                    """
                ),
                parameters,
            )
        recipients = [
            {
                "name": str(row[0]),
                "grant_count": int(row[1]),
                "amount": number_value(row[2]),
                "currency": selected_currency,
            }
            for row in recipient_rows
        ]
        funder_node = f"funder:{key}"
        nodes = [{"id": funder_node, "label": item["display_name"], "role": "funder"}]
        links = []
        for index, recipient in enumerate(recipients):
            node_id = f"recipient:{index}"
            nodes.append({"id": node_id, "label": recipient["name"], "role": "recipient"})
            links.append(
                {
                    "source": funder_node,
                    "target": node_id,
                    "value": float(recipient["amount"] or 0),
                    "currency": selected_currency,
                    "grant_count": recipient["grant_count"],
                }
            )
        sample = [
            {
                "grant_id": str(row[0]),
                "recipient_name": str(row[1]),
                "award_date": iso_value(row[2]),
                "amount": number_value(row[3]),
                "currency": row[4],
                "original_amount": number_value(row[5]),
                "original_currency": row[6],
                "source_url": row[7],
                "description": row[8],
                "evidence_links": (
                    [{
                        "kind": "grant_source",
                        "label": "Publisher source record",
                        "role": "funder",
                        "organization_name": item["display_name"],
                        "link_type": "website",
                        "url": row[7],
                        "origin": "publisher_source_url",
                    }] if row[7] else []
                ),
            }
            for row in grant_rows
        ]
        return {
            "status": "available",
            "country": response["country"],
            "funder": item,
            "top_recipients": recipients,
            "relationships": {
                "status": "available" if links else "no_transactions_found",
                "nodes": nodes,
                "links": links,
                "metadata": {"data_mode": "postgresql_source_funder_facts"},
            },
            "grant_sample": sample,
            "source_evidence": [],
            "relationship_summary": {
                "recipient_count": len(recipients),
                "sample_grant_count": len(sample),
            },
            "metadata": {"detail_level": filters.get("detail_level", "full")},
        }

    async def _identity(self, session, key: str) -> Optional[dict[str, Any]]:
        """Derive a funder's deterministic key from its source namespace and ID."""
        dataset_version = await self.active_dataset(session)
        row = (
            await session.execute(
                text(
                    """
                    SELECT source_namespace,
                           COALESCE(NULLIF(source_organization_id, ''),
                                    'source-funder-key:' || source_funder_key)
                               AS override_identifier,
                           MAX(linked_profile_id) OVER () AS linked_profile_id,
                           dataset_version
                    FROM grant_source_funder_facts
                    WHERE dataset_version=:dataset_version AND source_funder_key=:key
                    ORDER BY grant_id LIMIT 1
                    """
                ),
                {"dataset_version": dataset_version, "key": key},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def reset(
        self, source_funder_key: str, *, actor_id: str
    ) -> Optional[dict[str, Any]]:
        """Discard a curated link override, returning the funder to source state."""
        key = str(source_funder_key or "").strip()
        async with self.sessions() as session, session.begin():
            identity = await self._identity(session, key)
            if not identity:
                return None
            revision = await session.scalar(
                text(
                    """
                    SELECT revision FROM source_funder_link_overrides
                    WHERE source_namespace=:namespace AND source_organization_id=:identifier
                    FOR UPDATE
                    """
                ),
                {"namespace": identity["source_namespace"], "identifier": identity["override_identifier"]},
            )
            new_revision = int(revision) + 1 if revision is not None else 0
            await session.execute(
                text(
                    """
                    INSERT INTO source_funder_link_overrides (
                        source_namespace, source_organization_id, link_mode,
                        target_profile_id, target_dataset_version, reason,
                        updated_by, updated_at, revision
                    ) VALUES (
                        :namespace, :identifier, 'observed_only', NULL, NULL,
                        'explicit administrator reset', :actor_id,
                        CURRENT_TIMESTAMP, :revision
                    )
                    ON CONFLICT (source_namespace, source_organization_id)
                    DO UPDATE SET link_mode='observed_only', target_profile_id=NULL,
                        target_dataset_version=NULL,
                        reason='explicit administrator reset', updated_by=:actor_id,
                        updated_at=CURRENT_TIMESTAMP, revision=:revision
                    """
                ),
                {
                    "namespace": identity["source_namespace"],
                    "identifier": identity["override_identifier"],
                    "actor_id": actor_id,
                    "revision": new_revision,
                },
            )
            await session.execute(
                text(
                    """
                    DELETE FROM source_funder_profile_cache
                    WHERE dataset_version=:dataset_version AND source_funder_key=:key
                    """
                ),
                {"dataset_version": identity["dataset_version"], "key": key},
            )
        return {"source_funder_key": key, "revision": new_revision}

    async def relink(
        self,
        source_funder_key: str,
        profile_id: int,
        *,
        actor_id: str,
    ) -> Optional[dict[str, Any]]:
        """Point a source funder at an explicit profile, advancing the override revision."""
        key = str(source_funder_key or "").strip()
        async with self.sessions() as session, session.begin():
            identity = await self._identity(session, key)
            if not identity:
                return None
            profile_name = await session.scalar(
                text(
                    """
                    SELECT name FROM charities
                    WHERE dataset_version=:dataset_version AND charity_id=:profile_id
                    """
                ),
                {"dataset_version": identity["dataset_version"], "profile_id": profile_id},
            )
            if profile_name is None:
                raise ValueError("The requested active-dataset profile does not exist")
            revision = await session.scalar(
                text(
                    """
                    SELECT revision FROM source_funder_link_overrides
                    WHERE source_namespace=:namespace AND source_organization_id=:identifier
                    FOR UPDATE
                    """
                ),
                {"namespace": identity["source_namespace"], "identifier": identity["override_identifier"]},
            )
            new_revision = int(revision) + 1 if revision is not None else 0
            await session.execute(
                text(
                    """
                    INSERT INTO source_funder_link_overrides (
                        source_namespace, source_organization_id, link_mode,
                        target_profile_id, target_dataset_version, reason,
                        updated_by, updated_at, revision
                    ) VALUES (
                        :namespace, :identifier, 'link_profile', :profile_id,
                        :dataset_version, 'explicit administrator relink',
                        :actor_id, CURRENT_TIMESTAMP, :revision
                    )
                    ON CONFLICT (source_namespace, source_organization_id)
                    DO UPDATE SET link_mode='link_profile', target_profile_id=:profile_id,
                        target_dataset_version=:dataset_version,
                        reason='explicit administrator relink', updated_by=:actor_id,
                        updated_at=CURRENT_TIMESTAMP, revision=:revision
                    """
                ),
                {
                    "namespace": identity["source_namespace"],
                    "identifier": identity["override_identifier"],
                    "profile_id": profile_id,
                    "dataset_version": identity["dataset_version"],
                    "actor_id": actor_id,
                    "revision": new_revision,
                },
            )
            await session.execute(
                text(
                    """
                    DELETE FROM source_funder_profile_cache
                    WHERE dataset_version=:dataset_version AND source_funder_key=:key
                    """
                ),
                {"dataset_version": identity["dataset_version"], "key": key},
            )
        return {
            "source_funder_key": key,
            "profile_id": profile_id,
            "profile_name": str(profile_name),
            "revision": new_revision,
        }

    async def _effective_profile(self, session, key: str) -> Optional[dict[str, Any]]:
        """Resolve the profile link currently in force for a source funder."""
        dataset_version = await self.active_dataset(session)
        row = (
            await session.execute(
                text(
                    """
                    WITH identity AS (
                        SELECT source_namespace,
                               COALESCE(NULLIF(source_organization_id, ''),
                                        'source-funder-key:' || source_funder_key)
                                   AS override_identifier,
                               MAX(linked_profile_id) OVER () AS observed_profile_id
                        FROM grant_source_funder_facts
                        WHERE dataset_version=:dataset_version AND source_funder_key=:key
                        ORDER BY grant_id LIMIT 1
                    )
                    SELECT CASE
                               WHEN override.link_mode IN ('observed_only','unlink','blocked')
                                   THEN NULL
                               WHEN override.link_mode='link_profile'
                                   THEN override.target_profile_id
                               ELSE identity.observed_profile_id
                           END AS profile_id,
                           COALESCE(override.revision, 0) AS link_revision
                    FROM identity
                    LEFT JOIN source_funder_link_overrides AS override
                      ON override.source_namespace=identity.source_namespace
                     AND override.source_organization_id=identity.override_identifier
                    """
                ),
                {"dataset_version": dataset_version, "key": key},
            )
        ).mappings().first()
        if not row or row["profile_id"] is None:
            return None
        return {
            "dataset_version": dataset_version,
            "profile_id": int(row["profile_id"]),
            "link_revision": int(row["link_revision"]),
        }

    async def queue_profile_cache(
        self,
        source_funder_key: str,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> Optional[dict[str, Any]]:
        """Enqueue a durable job that rebuilds one funder's cached profile."""
        key = str(source_funder_key or "").strip()
        async with self.sessions() as session, session.begin():
            linked = await self._effective_profile(session, key)
            if not linked:
                return None
            job_id = uuid.uuid4()
            existing = (
                await session.execute(
                    text(
                        """
                        SELECT job_run_id, status FROM job_runs
                        WHERE job_type='source_funder_profile_hydration'
                          AND idempotency_key=:idempotency_key
                        """
                    ),
                    {"idempotency_key": idempotency_key},
                )
            ).mappings().first()
            if existing:
                job_id = existing["job_run_id"]
            else:
                await session.execute(
                    text(
                        """
                        INSERT INTO job_runs (
                            job_run_id, job_type, status, dataset_version,
                            idempotency_key, requested_by, input
                        ) VALUES (
                            :job_id, 'source_funder_profile_hydration', 'queued',
                            :dataset_version, :idempotency_key, :actor_id,
                            CAST(:input AS jsonb)
                        )
                        """
                    ),
                    {
                        "job_id": job_id,
                        "dataset_version": linked["dataset_version"],
                        "idempotency_key": idempotency_key,
                        "actor_id": actor_id,
                        "input": json.dumps({"source_funder_key": key}, sort_keys=True),
                    },
                )
            await session.execute(
                text(
                    """
                    INSERT INTO source_funder_profile_cache (
                        dataset_version, source_funder_key, profile_id, status,
                        payload, error, updated_at, job_token, link_revision
                    ) VALUES (
                        :dataset_version, :key, :profile_id, 'pending', NULL, NULL,
                        CURRENT_TIMESTAMP, :job_id, :link_revision
                    )
                    ON CONFLICT (dataset_version, source_funder_key)
                    DO UPDATE SET profile_id=:profile_id, status='pending', payload=NULL,
                        error=NULL, updated_at=CURRENT_TIMESTAMP, job_token=:job_id,
                        link_revision=:link_revision
                    """
                ),
                {
                    **linked,
                    "key": key,
                    "job_id": job_id,
                },
            )
        return {
            "source_funder_key": key,
            "profile_id": linked["profile_id"],
            "status": "pending",
            "job_id": str(job_id),
            "updated_at": utc_now(),
            "link_revision": linked["link_revision"],
        }

    async def profile_cache(self, source_funder_key: str) -> Optional[dict[str, Any]]:
        """Return a funder's cached profile payload, or None when not yet built."""
        key = str(source_funder_key or "").strip()
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            row = (
                await session.execute(
                    text(
                        """
                        SELECT profile_id, status, payload, error, updated_at,
                               job_token, link_revision
                        FROM source_funder_profile_cache
                        WHERE dataset_version=:dataset_version AND source_funder_key=:key
                        """
                    ),
                    {"dataset_version": dataset_version, "key": key},
                )
            ).mappings().first()
        if not row:
            return None
        return {
            "source_funder_key": key,
            "profile_id": int(row["profile_id"]),
            "status": str(row["status"]),
            "payload": row["payload"] if row["status"] == "ready" else None,
            "error": row["error"],
            "updated_at": iso_value(row["updated_at"]),
            "job_id": str(row["job_token"]) if row["job_token"] else None,
            "link_revision": int(row["link_revision"]),
        }
