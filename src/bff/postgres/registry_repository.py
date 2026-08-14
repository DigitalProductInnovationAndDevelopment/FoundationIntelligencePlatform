"""Deterministic PostgreSQL full-text and trigram registry search."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bff.postgres.base import (
    ANALYTICS_CACHE,
    PostgresRepository,
    iso_value,
    number_value,
)
from data.normalization import normalize_organization_name
from preprocessing.enrichment import enrich_organization


_SEARCH_SQL = text(
    """
    WITH active_dataset AS (
        SELECT dataset_version
        FROM dataset_versions
        WHERE is_active
    ),
    ranked AS (
        SELECT
            registry.registry_id,
            registry.charity_number,
            registry.linked_charity_number,
            registry.registered_name,
            registry.registration_status,
            registry.income,
            registry.expenditure,
            registry.city,
            registry.administrative_region,
            registry.country_code,
            round((
                ts_rank_cd(
                    registry.search_vector,
                    websearch_to_tsquery('simple'::regconfig, :query)
                )
                + similarity(registry.normalized_name, :normalized_query)
            )::numeric, 8) AS search_rank
        FROM charity_registry_organizations AS registry
        JOIN active_dataset
          ON active_dataset.dataset_version = registry.dataset_version
        WHERE (
            registry.search_vector
                @@ websearch_to_tsquery('simple'::regconfig, :query)
            OR registry.normalized_name % :normalized_query
        )
          AND registry.is_current_source_record
          AND (CAST(:registration_status AS text) IS NULL
               OR registry.registration_status = CAST(:registration_status AS text))
    )
    SELECT *
    FROM ranked
    WHERE (
        CAST(:cursor_rank AS numeric) IS NULL
        OR search_rank < CAST(:cursor_rank AS numeric)
        OR (search_rank = CAST(:cursor_rank AS numeric)
            AND registry_id > :cursor_registry_id)
    )
    ORDER BY search_rank DESC, registry_id ASC
    LIMIT :limit
    """
)


@dataclass(frozen=True)
class SearchCursor:
    """Opaque stable cursor ordered by rank descending then registry ID."""

    rank: Decimal
    registry_id: str

    def encode(self) -> str:
        payload = json.dumps(
            {"rank": format(self.rank, "f"), "registry_id": self.registry_id},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "SearchCursor":
        if not value or len(value) > 2048:
            raise ValueError("Invalid search cursor")
        try:
            padding = "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(value + padding))
            rank = Decimal(str(payload["rank"]))
            registry_id = str(payload["registry_id"])
        except (ValueError, KeyError, TypeError, InvalidOperation, json.JSONDecodeError) as exc:
            raise ValueError("Invalid search cursor") from exc
        if not rank.is_finite() or not registry_id or len(registry_id) > 500:
            raise ValueError("Invalid search cursor")
        return cls(rank=rank, registry_id=registry_id)


class RegistrySearchRepository:
    """Execute bounded, index-backed registry searches with stable pagination."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def search(
        self,
        query: str,
        *,
        registration_status: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        normalized_query = " ".join(str(query or "").casefold().split())
        if len(normalized_query) < 2 or len(normalized_query) > 200:
            raise ValueError("Search query must contain between 2 and 200 characters")
        if limit < 1 or limit > 100:
            raise ValueError("Search limit must be between 1 and 100")
        decoded = SearchCursor.decode(cursor) if cursor else None
        parameters = {
            "query": str(query).strip(),
            "normalized_query": normalized_query,
            "registration_status": registration_status,
            "cursor_rank": format(decoded.rank, "f") if decoded else None,
            "cursor_registry_id": decoded.registry_id if decoded else "",
            "limit": limit + 1,
        }
        async with self._sessions() as session:
            result = await session.execute(_SEARCH_SQL, parameters)
            rows = [dict(row) for row in result.mappings().all()]
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            final_row = page[-1]
            next_cursor = SearchCursor(
                rank=Decimal(str(final_row["search_rank"])),
                registry_id=str(final_row["registry_id"]),
            ).encode()
        return {"items": page, "next_cursor": next_cursor}


@dataclass(frozen=True)
class RegistryPageCursor:
    offset: int
    signature: str

    def encode(self) -> str:
        payload = json.dumps(
            {"offset": self.offset, "signature": self.signature},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str, signature: str) -> "RegistryPageCursor":
        if not value or len(value) > 500:
            raise ValueError("Invalid registry cursor")
        try:
            padding = "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(value + padding))
            cursor = cls(offset=int(payload["offset"]), signature=str(payload["signature"]))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid registry cursor") from exc
        if cursor.offset < 0 or cursor.offset > 10_000_000 or cursor.signature != signature:
            raise ValueError("Registry cursor does not match the requested filters")
        return cursor


class RegistryRepository(PostgresRepository):
    """Bounded directory filters and lazy record detail for the active dataset."""

    async def link_exact_profile(
        self, charity_number: int, *, actor_id: str
    ) -> dict[str, Any] | None:
        """Create/link one exact cached registry profile and attach observed grants."""
        async with self.sessions() as session, session.begin():
            dataset_version = await self.active_dataset(session)
            row = (
                await session.execute(
                    text(
                        """
                        SELECT registry.registry_id, registry.charity_number,
                               registry.registered_name, registry.registration_status,
                               registry.income, registry.expenditure,
                               registry.address_line_one, registry.address_line_two,
                               registry.address_line_three, registry.address_line_four,
                               registry.address_line_five, registry.postcode,
                               registry.city, registry.administrative_region,
                               registry.country_code, registry.registered_latitude,
                               registry.registered_longitude, registry.activity_text,
                               registry.source_name, registry.source_record_updated_at
                        FROM charity_registry_organizations AS registry
                        WHERE registry.dataset_version=:dataset_version
                          AND registry.is_current_source_record
                          AND (
                              registry.charity_number=:charity_number
                              OR registry.linked_charity_number=:charity_number
                          )
                        ORDER BY CASE WHEN registry.charity_number=:charity_number
                                      THEN 0 ELSE 1 END,
                                 registry.registry_id
                        LIMIT 1
                        """
                    ),
                    {
                        "dataset_version": dataset_version,
                        "charity_number": str(int(charity_number)),
                    },
                )
            ).mappings().first()
            if row is None:
                return None
            try:
                profile_id = int(str(row["charity_number"]).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError("The cached registry record has no usable charity number") from exc

            address = ", ".join(
                str(row[field])
                for field in (
                    "address_line_one", "address_line_two", "address_line_three",
                    "address_line_four", "address_line_five", "postcode",
                )
                if row[field]
            )
            source_url = (
                "https://register-of-charities.charitycommission.gov.uk/"
                f"en/charity-search/-/charity-details/{profile_id}"
            )
            raw_source_data = {
                "registered_charity_number": profile_id,
                "link": source_url,
                "all_details": {
                    "charity_name": row["registered_name"],
                    "reg_status": row["registration_status"],
                    "latest_income": row["income"],
                    "latest_expenditure": row["expenditure"],
                    "activities": row["activity_text"],
                    "address_country": row["country_code"],
                },
                "description": row["activity_text"],
                "registry_id": row["registry_id"],
            }
            profile_seed: dict[str, Any] = {
                "charity_id": profile_id,
                "name": row["registered_name"],
                "type": "Charity",
                "address": address,
                "city": row["city"],
                "state": row["administrative_region"],
                "country": row["country_code"],
                "annual_income": row["income"],
                "annual_expenditure": row["expenditure"],
                "raw_cc_data": raw_source_data,
            }
            enrichment = enrich_organization(profile_seed)
            source_records = [{
                "source": row["source_name"],
                "record_id": row["registry_id"],
                "url": source_url,
                "record_updated_at": iso_value(row["source_record_updated_at"]),
            }]
            await session.execute(
                text(
                    """
                    INSERT INTO charities (
                        dataset_version, charity_id, name, type, address, city,
                        state, country, latitude, longitude, annual_income,
                        annual_expenditure, raw_source_data,
                        programme_areas_source, programme_areas_inferred,
                        programme_area_scores, programme_area_method,
                        programme_area_evidence, programme_area_review_required,
                        geographic_focus_source, geographic_focus_inferred,
                        headquarters_country, headquarters_region,
                        geography_method, geography_confidence, geography_evidence,
                        geography_review_required, enrichment_rule_version,
                        enrichment_review_reasons, insufficient_source_text,
                        normalized_name, organization_type, primary_source,
                        source_names, source_record_id, source_url, source_records,
                        ingestion_timestamp, transaction_coverage
                    ) VALUES (
                        :dataset_version, :profile_id, :name, 'Charity', :address,
                        :city, :state, :country, :latitude, :longitude, :income,
                        :expenditure, CAST(:raw_source_data AS jsonb),
                        CAST(:programme_areas_source AS jsonb),
                        CAST(:programme_areas_inferred AS jsonb),
                        CAST(:programme_area_scores AS jsonb), :programme_area_method,
                        CAST(:programme_area_evidence AS jsonb), :programme_review,
                        CAST(:geographic_focus_source AS jsonb),
                        CAST(:geographic_focus_inferred AS jsonb),
                        :headquarters_country, :headquarters_region, :geography_method,
                        :geography_confidence, CAST(:geography_evidence AS jsonb),
                        :geography_review, :enrichment_rule_version,
                        CAST(:enrichment_review_reasons AS jsonb), :insufficient_text,
                        :normalized_name, 'charity', :source_name,
                        CAST(:source_names AS jsonb), :registry_id, :source_url,
                        CAST(:source_records AS jsonb), CURRENT_TIMESTAMP,
                        'source_without_transactions'
                    )
                    ON CONFLICT (dataset_version, charity_id) DO NOTHING
                    """
                ),
                {
                    "dataset_version": dataset_version,
                    "profile_id": profile_id,
                    "name": str(row["registered_name"]),
                    "address": address,
                    "city": row["city"],
                    "state": row["administrative_region"],
                    "country": row["country_code"],
                    "latitude": row["registered_latitude"],
                    "longitude": row["registered_longitude"],
                    "income": row["income"],
                    "expenditure": row["expenditure"],
                    "raw_source_data": json.dumps(raw_source_data, default=str),
                    "programme_areas_source": json.dumps(enrichment["programme_areas_source"]),
                    "programme_areas_inferred": json.dumps(enrichment["programme_areas_inferred"]),
                    "programme_area_scores": json.dumps(enrichment["programme_area_scores"]),
                    "programme_area_method": enrichment["programme_area_method"],
                    "programme_area_evidence": json.dumps(enrichment["programme_area_evidence"]),
                    "programme_review": bool(enrichment["programme_area_review_required"]),
                    "geographic_focus_source": json.dumps(enrichment["geographic_focus_source"]),
                    "geographic_focus_inferred": json.dumps(enrichment["geographic_focus_inferred"]),
                    "headquarters_country": enrichment["headquarters_country"],
                    "headquarters_region": enrichment["headquarters_region"],
                    "geography_method": enrichment["geography_method"],
                    "geography_confidence": enrichment["geography_confidence"],
                    "geography_evidence": json.dumps(enrichment["geography_evidence"]),
                    "geography_review": bool(enrichment["geography_review_required"]),
                    "enrichment_rule_version": enrichment["enrichment_rule_version"],
                    "enrichment_review_reasons": json.dumps(enrichment["enrichment_review_reasons"]),
                    "insufficient_text": bool(enrichment["insufficient_source_text"]),
                    "normalized_name": normalize_organization_name(row["registered_name"]),
                    "source_name": row["source_name"],
                    "source_names": json.dumps([row["source_name"]]),
                    "registry_id": row["registry_id"],
                    "source_url": source_url,
                    "source_records": json.dumps(source_records),
                },
            )
            source_identifier = f"GB-CHC-{profile_id}"
            linked_grants = (
                await session.execute(
                    text(
                        """
                        UPDATE grants SET funding_charity_id=:profile_id
                        WHERE dataset_version=:dataset_version
                          AND funding_charity_id IS NULL
                          AND funding_org_source_id=:source_identifier
                        RETURNING grant_id
                        """
                    ),
                    {
                        "dataset_version": dataset_version,
                        "profile_id": profile_id,
                        "source_identifier": source_identifier,
                    },
                )
            ).scalars().all()
            linked_facts = (
                await session.execute(
                    text(
                        """
                        UPDATE grant_source_funder_facts AS facts
                        SET linked_profile_id=:profile_id
                        WHERE facts.dataset_version=:dataset_version
                          AND facts.linked_profile_id IS NULL
                          AND (
                              facts.source_organization_id=:source_identifier
                              OR EXISTS (
                                  SELECT 1 FROM grants
                                  WHERE grants.dataset_version=facts.dataset_version
                                    AND grants.grant_id=facts.grant_id
                                    AND grants.funding_charity_id=:profile_id
                              )
                          )
                        RETURNING facts.grant_id
                        """
                    ),
                    {
                        "dataset_version": dataset_version,
                        "profile_id": profile_id,
                        "source_identifier": source_identifier,
                    },
                )
            ).scalars().all()
            observed_grants = int(
                await session.scalar(
                    text(
                        """
                        SELECT COUNT(*) FROM grants
                        WHERE dataset_version=:dataset_version
                          AND funding_charity_id=:profile_id
                        """
                    ),
                    {"dataset_version": dataset_version, "profile_id": profile_id},
                )
                or 0
            )
            await session.execute(
                text(
                    """
                    UPDATE charities
                    SET transaction_coverage=:coverage
                    WHERE dataset_version=:dataset_version AND charity_id=:profile_id
                    """
                ),
                {
                    "coverage": (
                        "observed_grants_linked" if observed_grants
                        else "source_without_transactions"
                    ),
                    "dataset_version": dataset_version,
                    "profile_id": profile_id,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO organization_registry_links (
                        dataset_version, registry_id, enriched_organization_id,
                        match_status, match_method, match_confidence, match_reason,
                        reviewed_at, created_at, updated_at
                    ) VALUES (
                        :dataset_version, :registry_id, :profile_id,
                        'accepted', 'exact_identifier', 1,
                        :reason, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (dataset_version, registry_id, enriched_organization_id)
                    DO UPDATE SET match_status='accepted',
                        match_method='exact_identifier', match_confidence=1,
                        match_reason=:reason, reviewed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    """
                ),
                {
                    "dataset_version": dataset_version,
                    "registry_id": row["registry_id"],
                    "profile_id": profile_id,
                    "reason": f"worker-confirmed exact identifier by {actor_id}"[:500],
                },
            )
            persisted = await session.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM organization_registry_links
                    WHERE dataset_version=:dataset_version
                      AND registry_id=:registry_id
                      AND enriched_organization_id=:profile_id
                      AND match_status='accepted'
                    """
                ),
                {
                    "dataset_version": dataset_version,
                    "registry_id": row["registry_id"],
                    "profile_id": profile_id,
                },
            )
            if int(persisted or 0) != 1:
                raise RuntimeError("Registry profile link was not persisted")
        return {
            "registry_id": str(row["registry_id"]),
            "profile_id": profile_id,
            "profile_name": str(row["registered_name"]),
            "linked_grants": observed_grants,
            "newly_linked_grants": len(linked_grants),
            "linked_source_facts": len(linked_facts),
        }

    @staticmethod
    def _filter_signature(filters: dict[str, Any]) -> str:
        payload = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:20]

    async def _registry_count(self, dataset_version: str) -> int:
        async def load() -> int:
            async with self.sessions() as session:
                count = await session.scalar(
                    text(
                        """
                        SELECT COUNT(*) FROM charity_registry_organizations
                        WHERE dataset_version=:dataset_version
                          AND is_current_source_record
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
            return int(count or 0)

        return await ANALYTICS_CACHE.get_or_create(
            (dataset_version, "registry_count"), load
        )

    async def page(
        self,
        *,
        query: Optional[str] = None,
        charity_number: Optional[str] = None,
        status: Optional[str] = None,
        income_min: Optional[float] = None,
        income_max: Optional[float] = None,
        expenditure_min: Optional[float] = None,
        expenditure_max: Optional[float] = None,
        country: Optional[str] = None,
        region: Optional[str] = None,
        beneficiary_geography: Optional[str] = None,
        has_enriched_profile: Optional[bool] = None,
        has_grant_data: Optional[bool] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
        sort: str = "name",
    ) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 100)
        normalized_query = " ".join(str(query or "").casefold().split())
        if normalized_query and len(normalized_query) < 2:
            raise ValueError("Search query must contain at least two characters")
        filters = {
            "query": normalized_query or None,
            "charity_number": charity_number,
            "status": status,
            "income_min": income_min,
            "income_max": income_max,
            "expenditure_min": expenditure_min,
            "expenditure_max": expenditure_max,
            "country": country.upper() if country else None,
            "region": region,
            "beneficiary_geography": beneficiary_geography,
            "has_enriched_profile": has_enriched_profile,
            "has_grant_data": has_grant_data,
            "sort": sort,
        }
        advanced_filters = any(
            value is not None
            for value in (
                charity_number,
                income_min,
                income_max,
                expenditure_min,
                expenditure_max,
                country,
                region,
                beneficiary_geography,
                has_enriched_profile,
                has_grant_data,
            )
        )
        if normalized_query and not advanced_filters and sort == "name":
            return await self._search_page(
                query=str(query),
                normalized_query=normalized_query,
                status=status,
                cursor=cursor,
                limit=limit,
                filters=filters,
            )
        signature = self._filter_signature(filters)
        decoded = RegistryPageCursor.decode(cursor, signature) if cursor else None
        offset = decoded.offset if decoded else 0
        conditions = ["registry.dataset_version=:dataset_version", "registry.is_current_source_record"]
        parameters: dict[str, Any] = {
            **filters,
            "query_text": str(query or "").strip(),
            "normalized_query": normalized_query,
            "limit": limit + 1,
            "offset": offset,
        }
        if normalized_query:
            conditions.append(
                """
                (registry.search_vector @@ websearch_to_tsquery(
                    'simple'::regconfig, :query_text
                 ) OR registry.normalized_name % :normalized_query)
                """
            )
        if charity_number:
            conditions.append("registry.charity_number=:charity_number")
        if status:
            conditions.append("registry.registration_status=:status")
        if income_min is not None:
            conditions.append("registry.income>=:income_min")
        if income_max is not None:
            conditions.append("registry.income<=:income_max")
        if expenditure_min is not None:
            conditions.append("registry.expenditure>=:expenditure_min")
        if expenditure_max is not None:
            conditions.append("registry.expenditure<=:expenditure_max")
        if country:
            conditions.append("registry.country_code=:country")
        if region:
            conditions.append("registry.administrative_region=:region")
        if beneficiary_geography:
            conditions.append(
                """
                EXISTS (
                    SELECT 1 FROM organization_registry_links AS link
                    JOIN grants AS grant_row
                      ON grant_row.dataset_version=link.dataset_version
                     AND grant_row.funding_charity_id=link.enriched_organization_id
                    JOIN grant_beneficiary_countries AS geography
                      ON geography.dataset_version=grant_row.dataset_version
                     AND geography.grant_id=grant_row.grant_id
                    WHERE link.dataset_version=registry.dataset_version
                      AND link.registry_id=registry.registry_id
                      AND link.match_status='accepted'
                      AND geography.country_name=:beneficiary_geography
                )
                """
            )
        enriched_expression = (
            "EXISTS (SELECT 1 FROM organization_registry_links AS link "
            "WHERE link.dataset_version=registry.dataset_version "
            "AND link.registry_id=registry.registry_id AND link.match_status='accepted')"
        )
        grant_expression = (
            "EXISTS (SELECT 1 FROM grants AS grant_row WHERE "
            "grant_row.dataset_version=registry.dataset_version AND ("
            "grant_row.funding_org_source_id='GB-CHC-' || registry.charity_number OR "
            "grant_row.recipient_org_source_id='GB-CHC-' || registry.charity_number))"
        )
        if has_enriched_profile is not None:
            conditions.append(f"{enriched_expression}=:has_enriched_profile")
        if has_grant_data is not None:
            conditions.append(f"{grant_expression}=:has_grant_data")
        order = {
            "name": "registry.registered_name ASC, registry.registry_id ASC",
            "income_desc": "registry.income DESC NULLS LAST, registry.registry_id ASC",
            "expenditure_desc": "registry.expenditure DESC NULLS LAST, registry.registry_id ASC",
        }.get(sort)
        if not order:
            raise ValueError("Unsupported registry sort")
        sql = text(
            f"""
            SELECT registry.registry_id, registry.charity_number,
                   registry.registered_name, registry.registration_status,
                   registry.income, registry.expenditure, registry.city,
                   registry.administrative_region, registry.country_code,
                   registry.source_record_updated_at,
                   {enriched_expression} AS has_enriched_profile,
                   {grant_expression} AS has_grant_data,
                   EXISTS (
                       SELECT 1 FROM organization_registry_links AS philea_link
                       JOIN charities AS profile
                         ON profile.dataset_version=philea_link.dataset_version
                        AND profile.charity_id=philea_link.enriched_organization_id
                       WHERE philea_link.dataset_version=registry.dataset_version
                         AND philea_link.registry_id=registry.registry_id
                         AND philea_link.match_status='accepted'
                         AND profile.primary_source='Philea'
                   ) AS has_philea_data
            FROM charity_registry_organizations AS registry
            WHERE {' AND '.join(conditions)}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """
        )
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            parameters["dataset_version"] = dataset_version
            rows = [
                dict(row)
                for row in (await session.execute(sql, parameters)).mappings()
            ]
        registry_count = await self._registry_count(dataset_version)
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        results = [
            {
                **row,
                "income": number_value(row.get("income")),
                "expenditure": number_value(row.get("expenditure")),
                "source_record_updated_at": iso_value(row.get("source_record_updated_at")),
            }
            for row in page_rows
        ]
        next_cursor = (
            RegistryPageCursor(offset=offset + limit, signature=signature).encode()
            if has_more else None
        )
        return {
            "results": results,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "applied_filters": filters,
            "page_size": len(results),
            "registry_count": int(registry_count or 0),
            "search_strategy": (
                "postgresql_tsvector_trigram" if normalized_query else "postgresql_indexed_filters"
            ),
        }

    async def _search_page(
        self,
        *,
        query: str,
        normalized_query: str,
        status: Optional[str],
        cursor: Optional[str],
        limit: int,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 100)
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            exact_rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT registry_id, charity_number, linked_charity_number,
                                   registered_name, registration_status, income,
                                   expenditure, city, administrative_region,
                                   country_code, 1.0::numeric AS search_rank
                            FROM charity_registry_organizations
                            WHERE dataset_version=:dataset_version
                              AND is_current_source_record
                              AND normalized_name=:normalized_query
                              AND (CAST(:status AS text) IS NULL
                                   OR registration_status=CAST(:status AS text))
                            ORDER BY registry_id LIMIT :limit
                            """
                        ),
                        {
                            "dataset_version": dataset_version,
                            "normalized_query": normalized_query,
                            "status": status,
                            "limit": limit + 1,
                        },
                    )
                ).mappings()
            ]
        if exact_rows and cursor:
            raise ValueError("Exact registry results do not accept a continuation cursor")
        if exact_rows:
            candidates = exact_rows[:limit]
            next_cursor = None
            has_more = len(exact_rows) > limit
            strategy = "postgresql_exact_normalized_name"
        else:
            search_result = await RegistrySearchRepository(self.sessions).search(
                query,
                registration_status=status,
                cursor=cursor,
                limit=limit,
            )
            candidates = list(search_result["items"])
            next_cursor = search_result["next_cursor"]
            has_more = next_cursor is not None
            strategy = "postgresql_tsvector_trigram_ranked"
        candidate_ids = [str(row["registry_id"]) for row in candidates]
        registry_count = await self._registry_count(dataset_version)
        if not candidate_ids:
            return {
                "results": [],
                "next_cursor": None,
                "has_more": False,
                "applied_filters": filters,
                "page_size": 0,
                "registry_count": registry_count,
                "search_strategy": strategy,
            }
        async with self.sessions() as session:
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT registry.registry_id, registry.charity_number,
                                   registry.registered_name,
                                   registry.registration_status, registry.income,
                                   registry.expenditure, registry.city,
                                   registry.administrative_region,
                                   registry.country_code,
                                   registry.source_record_updated_at,
                                   EXISTS (
                                       SELECT 1 FROM organization_registry_links AS link
                                       WHERE link.dataset_version=registry.dataset_version
                                         AND link.registry_id=registry.registry_id
                                         AND link.match_status='accepted'
                                   ) AS has_enriched_profile,
                                   EXISTS (
                                       SELECT 1 FROM grants AS grant_row
                                       WHERE grant_row.dataset_version=registry.dataset_version
                                         AND (
                                           grant_row.funding_org_source_id=
                                               'GB-CHC-' || registry.charity_number
                                           OR grant_row.recipient_org_source_id=
                                               'GB-CHC-' || registry.charity_number
                                         )
                                   ) AS has_grant_data,
                                   EXISTS (
                                       SELECT 1 FROM organization_registry_links AS link
                                       JOIN charities AS profile
                                         ON profile.dataset_version=link.dataset_version
                                        AND profile.charity_id=link.enriched_organization_id
                                       WHERE link.dataset_version=registry.dataset_version
                                         AND link.registry_id=registry.registry_id
                                         AND link.match_status='accepted'
                                         AND profile.primary_source='Philea'
                                   ) AS has_philea_data
                            FROM charity_registry_organizations AS registry
                            WHERE registry.dataset_version=:dataset_version
                              AND registry.registry_id=ANY(CAST(:ids AS text[]))
                            """
                        ),
                        {"dataset_version": dataset_version, "ids": candidate_ids},
                    )
                ).mappings()
            ]
        by_id = {str(row["registry_id"]): row for row in rows}
        results = []
        for registry_id in candidate_ids:
            row = by_id[registry_id]
            results.append(
                {
                    **row,
                    "income": number_value(row.get("income")),
                    "expenditure": number_value(row.get("expenditure")),
                    "source_record_updated_at": iso_value(
                        row.get("source_record_updated_at")
                    ),
                }
            )
        return {
            "results": results,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "applied_filters": filters,
            "page_size": len(results),
            "registry_count": int(registry_count or 0),
            "search_strategy": strategy,
        }

    async def detail(self, registry_id: str) -> Optional[dict[str, Any]]:
        async with self.sessions() as session:
            dataset_version = await self.active_dataset(session)
            row = (
                await session.execute(
                    text(
                        """
                        SELECT registry.*,
                               link.enriched_organization_id,
                               profile.name AS organization_name,
                               link.match_status, link.match_method,
                               link.match_confidence, link.match_reason,
                               EXISTS (
                                   SELECT 1 FROM grants AS grant_row
                                   WHERE grant_row.dataset_version=registry.dataset_version
                                     AND (grant_row.funding_charity_id=link.enriched_organization_id
                                          OR grant_row.recipient_charity_id=link.enriched_organization_id)
                               ) AS linked_has_grant_data,
                               (profile.primary_source='Philea') AS linked_has_philea_data
                        FROM charity_registry_organizations AS registry
                        LEFT JOIN organization_registry_links AS link
                          ON link.dataset_version=registry.dataset_version
                         AND link.registry_id=registry.registry_id
                         AND link.match_status='accepted'
                        LEFT JOIN charities AS profile
                          ON profile.dataset_version=link.dataset_version
                         AND profile.charity_id=link.enriched_organization_id
                        WHERE registry.dataset_version=:dataset_version
                          AND registry.registry_id=:registry_id
                        ORDER BY link.updated_at DESC NULLS LAST
                        LIMIT 1
                        """
                    ),
                    {"dataset_version": dataset_version, "registry_id": registry_id},
                )
            ).mappings().first()
        if not row:
            return None
        item = dict(row)
        address_lines = [
            str(item[name]).strip()
            for name in (
                "address_line_one", "address_line_two", "address_line_three",
                "address_line_four", "address_line_five",
            )
            if item.get(name) and str(item[name]).strip()
        ]
        enriched = None
        if item.get("enriched_organization_id") is not None:
            enriched = {
                "enriched_organization_id": int(item["enriched_organization_id"]),
                "organization_name": str(item.get("organization_name") or "Unknown"),
                "match_status": str(item.get("match_status") or "accepted"),
                "match_method": str(item.get("match_method") or "unknown"),
                "match_confidence": number_value(item.get("match_confidence")),
                "match_reason": item.get("match_reason"),
                "has_grant_data": bool(item.get("linked_has_grant_data")),
                "has_philea_data": bool(item.get("linked_has_philea_data")),
            }
        return {
            "registry_id": str(item["registry_id"]),
            "charity_number": str(item["charity_number"]),
            "linked_charity_number": item.get("linked_charity_number"),
            "registered_name": str(item["registered_name"]),
            "registration_status": item.get("registration_status"),
            "registration_date": iso_value(item.get("registration_date")),
            "removal_date": iso_value(item.get("removal_date")),
            "income": number_value(item.get("income")),
            "expenditure": number_value(item.get("expenditure")),
            "financial_period_end_date": iso_value(item.get("financial_period_end_date")),
            "address_lines": address_lines,
            "postcode": item.get("postcode"),
            "city": item.get("city"),
            "administrative_region": item.get("administrative_region"),
            "country_code": item.get("country_code"),
            "activity_text": item.get("activity_text"),
            "source_name": str(item["source_name"]),
            "source_record_updated_at": iso_value(item.get("source_record_updated_at")),
            "imported_at": iso_value(item["imported_at"]),
            "is_current_source_record": bool(item["is_current_source_record"]),
            "observed_grant_data_message": (
                "Observed grant data is available for the accepted enriched profile."
                if enriched and enriched["has_grant_data"]
                else "Registry presence does not imply observed grant data."
            ),
            "enriched_profile": enriched,
        }
