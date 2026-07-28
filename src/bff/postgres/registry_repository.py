"""Deterministic PostgreSQL full-text and trigram registry search."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
