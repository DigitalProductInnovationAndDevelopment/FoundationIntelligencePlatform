"""SQLite shadow adapter used only with an explicit, separate snapshot copy."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional
from urllib.parse import parse_qs, unquote

from transition.shadow import ShadowRequest


def _first(query: dict[str, list[str]], key: str, default: Optional[str] = None) -> Optional[str]:
    values = query.get(key)
    return values[-1] if values else default


def _integer(query: dict[str, list[str]], key: str, default: int) -> int:
    return int(_first(query, key, str(default)) or default)


def _number(query: dict[str, list[str]], key: str) -> Optional[float]:
    value = _first(query, key)
    return float(value) if value not in {None, ""} else None


def _boolean(query: dict[str, list[str]], key: str) -> Optional[bool]:
    value = _first(query, key)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split(query: dict[str, list[str]], key: str) -> Optional[list[str]]:
    value = _first(query, key)
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


class SQLiteShadowReader:
    """Execute legacy reads off the event loop; never return them to the user."""

    def __init__(self, snapshot_path: str):
        # Lazy import preserves the PostgreSQL-only runtime import boundary.
        from bff.repositories import SQLiteCharityRepository

        self.repository = SQLiteCharityRepository(snapshot_path)

    async def _call(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(lambda: asyncio.run(method(*args, **kwargs)))

    async def read(self, request: ShadowRequest) -> Any:
        if request.method != "GET":
            raise ValueError("Only read-only GET journeys can use the SQLite shadow adapter")
        query = parse_qs(request.query_string, keep_blank_values=True)
        path = request.path
        repository = self.repository

        if path == "/api/charities":
            return await self._call(
                repository.get_all,
                search=_first(query, "search"),
                reg_status=_first(query, "reg_status"),
                tag=_first(query, "tag"),
                region=_first(query, "region"),
                size=_first(query, "size"),
                tags=_split(query, "tags"),
                foundation_regions=_split(query, "foundation_regions"),
                funding_regions=_split(query, "funding_regions"),
                sources=_split(query, "sources"),
                min_annual_giving=_number(query, "min_annual_giving"),
                max_annual_giving=_number(query, "max_annual_giving"),
                min_avg_grant_size=_number(query, "min_avg_grant_size"),
                max_avg_grant_size=_number(query, "max_avg_grant_size"),
                include_score=bool(_boolean(query, "include_score")),
                sort=_first(query, "sort", "name_asc") or "name_asc",
                skip=_integer(query, "skip", 0),
                limit=_integer(query, "limit", 20),
            )
        if path == "/api/charities/stats":
            return await self._call(repository.get_stats)
        if path == "/api/charities/grants/beneficiary-geographies":
            return await self._call(
                repository.get_beneficiary_geography_options,
                sources=_split(query, "sources") or ["360Giving"],
            )
        if path == "/api/charities/directory/organizations":
            return await self._call(
                repository.get_registry_page,
                query=_first(query, "query"),
                charity_number=_first(query, "charity_number"),
                status=_first(query, "status"),
                income_min=_number(query, "income_min"),
                income_max=_number(query, "income_max"),
                expenditure_min=_number(query, "expenditure_min"),
                expenditure_max=_number(query, "expenditure_max"),
                country=_first(query, "country"),
                region=_first(query, "region"),
                beneficiary_geography=_first(query, "beneficiary_geography"),
                has_enriched_profile=_boolean(query, "has_enriched_profile"),
                has_grant_data=_boolean(query, "has_grant_data"),
                cursor=_first(query, "cursor"),
                limit=_integer(query, "limit", 50),
                sort=_first(query, "sort", "name") or "name",
            )
        registry_match = re.fullmatch(r"/api/charities/directory/organizations/([^/]+)", path)
        if registry_match:
            return await self._call(
                repository.get_registry_detail, unquote(registry_match.group(1))
            )
        if path == "/api/charities/grants/map":
            return await self._call(
                repository.get_grants_map,
                currency=_first(query, "currency"),
                min_coverage=_number(query, "min_coverage") or 0.30,
                search=_first(query, "search"),
                tags=_split(query, "tags"),
                foundation_regions=_split(query, "foundation_regions"),
                funding_regions=_split(query, "funding_regions"),
                min_annual_giving=_number(query, "min_annual_giving"),
                min_avg_grant_size=_number(query, "min_avg_grant_size"),
            )
        if path == "/api/charities/grants/map/connections":
            result = await self._call(
                repository.get_grants_map,
                currency=_first(query, "currency"),
                min_coverage=0.0,
            )
            limit = min(_integer(query, "limit", 100), 250)
            connections = result.get("connections", [])[:limit]
            return {
                "status": "available" if connections else "no_transactions_found",
                "connections": connections,
                "connection_grant_count": sum(
                    int(item.get("grant_count", 0)) for item in connections
                ),
                "selected_currency": result.get("selected_currency") or "EUR",
                "limit": limit,
                "metadata": {
                    "data_mode": "sqlite_shadow_snapshot",
                    "loading_mode": "lazy_bounded_secondary_request",
                },
            }
        if path in {
            "/api/charities/grants/overview",
            "/api/charities/grants/overview/trends",
        }:
            filters = {
                "currency": _first(query, "currency"),
                "date_from": _first(query, "date_from"),
                "date_to": _first(query, "date_to"),
                "beneficiary_geographies": _split(query, "beneficiary_geographies"),
                "programme_areas": _split(query, "programme_areas"),
                "donor": _first(query, "donor"),
                "recipient": _first(query, "recipient"),
                "sources": _split(query, "sources"),
                "granularity": _first(query, "granularity", "auto") or "auto",
            }
            method = (
                repository.get_grant_overview
                if path.endswith("/overview")
                else repository.get_grant_overview_trends
            )
            if path.endswith("/overview"):
                filters["include_connections"] = bool(
                    _boolean(query, "include_connections")
                )
            return await self._call(method, **filters)
        if path == "/api/charities/grants/overview/entity-suggestions":
            return await self._call(
                repository.get_grant_entity_suggestions,
                sources=_split(query, "sources"),
                limit=_integer(query, "limit", 2500),
            )
        if path == "/api/charities/grants/overview/drilldown":
            return await self._call(
                repository.get_grant_overview_drilldown,
                selection_type=_first(query, "selection_type") or "",
                selection_value=_first(query, "selection_value") or "",
                currency=_first(query, "currency"),
                date_from=_first(query, "date_from"),
                date_to=_first(query, "date_to"),
                beneficiary_geographies=_split(query, "beneficiary_geographies"),
                programme_areas=_split(query, "programme_areas"),
                donor=_first(query, "donor"),
                recipient=_first(query, "recipient"),
                sources=_split(query, "sources"),
            )
        if path == "/api/charities/grants/funders":
            return await self._call(
                repository.get_source_funders,
                beneficiary_country=(_first(query, "beneficiary_country") or "").upper(),
                currency=_first(query, "currency"),
                date_from=_first(query, "date_from"),
                date_to=_first(query, "date_to"),
                beneficiary_geographies=_split(query, "beneficiary_geographies"),
                programme_areas=_split(query, "programme_areas"),
                donor=_first(query, "donor"),
                recipient=_first(query, "recipient"),
                sources=_split(query, "sources"),
                search=_first(query, "search"),
                profile_status=_first(query, "profile_status", "all") or "all",
                sort=_first(query, "sort", "largest_observed_funding") or "largest_observed_funding",
                page=_integer(query, "page", 1),
                page_size=_integer(query, "page_size", 25),
            )
        funder_match = re.fullmatch(r"/api/charities/grants/funders/([^/]+)", path)
        if funder_match:
            return await self._call(
                repository.get_source_funder_detail,
                unquote(funder_match.group(1)),
                beneficiary_country=(_first(query, "beneficiary_country") or "").upper(),
                currency=_first(query, "currency"),
                date_from=_first(query, "date_from"),
                date_to=_first(query, "date_to"),
                beneficiary_geographies=_split(query, "beneficiary_geographies"),
                programme_areas=_split(query, "programme_areas"),
                donor=_first(query, "donor"),
                recipient=_first(query, "recipient"),
                sources=_split(query, "sources"),
                detail_level=_first(query, "detail_level", "full") or "full",
            )
        if path == "/api/charities/grants/summary":
            return await self._call(repository.get_grant_summary)
        if path == "/api/charities/grants/trends":
            return await self._call(
                repository.get_grant_trends,
                currency=_first(query, "currency"),
                months=_integer(query, "months", 24),
            )
        if path == "/api/charities/grants/themes":
            return await self._call(
                repository.get_grant_themes, currency=_first(query, "currency")
            )
        profile_match = re.fullmatch(r"/api/charities/(-?\d+)", path)
        if profile_match:
            return await self._call(repository.get_by_id, int(profile_match.group(1)))
        grants_match = re.fullmatch(r"/api/charities/(-?\d+)/grants", path)
        if grants_match:
            return await self._call(
                repository.get_grants_for_charity,
                int(grants_match.group(1)),
                role=_first(query, "role", "all") or "all",
            )
        sankey_match = re.fullmatch(r"/api/charities/(-?\d+)/sankey", path)
        if sankey_match:
            return await self._call(
                repository.get_sankey_data,
                int(sankey_match.group(1)),
                currency=_first(query, "currency"),
                limit=_integer(query, "limit", 30),
            )
        raise ValueError(f"No SQLite shadow adapter for {path}")


def resolve_shadow_journey(method: str, path: str, query_string: str = "") -> Optional[str]:
    if method != "GET" or not path.startswith("/api/charities"):
        return None
    query = parse_qs(query_string, keep_blank_values=True)
    if path == "/api/charities/grants/overview":
        if _first(query, "date_from") or _first(query, "date_to"):
            return "date_filters"
        if _first(query, "beneficiary_geographies"):
            return "country_filters"
        if _first(query, "programme_areas"):
            return "programme_filters"
        if _first(query, "donor"):
            return "donor_filters"
        if _first(query, "recipient"):
            return "recipient_filters"
        return "dashboard"
    if path == "/api/charities/grants/map":
        return "map"
    if path == "/api/charities/grants/map/connections":
        return "map_relationships"
    if path == "/api/charities/grants/overview/trends":
        return "monthly_trends" if _first(query, "granularity") == "monthly" else "yearly_trends"
    if path == "/api/charities/grants/overview/drilldown":
        return "drill_down"
    if path == "/api/charities/grants/funders":
        return "donor_ranking"
    if path == "/api/charities/directory/organizations":
        return "registry_search"
    if re.fullmatch(r"/api/charities/(-?\d+)/grants", path):
        return "grant_list"
    if re.fullmatch(r"/api/charities/(-?\d+)/sankey", path):
        return "sankey"
    if re.fullmatch(r"/api/charities/(-?\d+)", path) or path.startswith(
        "/api/charities/directory/organizations/"
    ):
        return "profile_detail"
    return "dashboard"
