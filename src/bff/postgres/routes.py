"""Complete PostgreSQL-only organization and grant API surface."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from bff.postgres.analytics_repository import AnalyticsRepository
from bff.postgres.funder_repository import SourceFunderRepository
from bff.postgres.job_repository import PostgresJobRepository
from bff.postgres.organization_repository import OrganizationRepository
from bff.postgres.registry_repository import RegistryRepository
from bff.schemas import (
    CharityBase,
    CharityDetail,
    CharityStats,
    GrantListResponse,
    GrantMapResponse,
    GrantNetworkSummary,
    GrantThemesResponse,
    GrantTrendsResponse,
    PipelineStatus,
    RegistryDirectoryPage,
    RegistryOrganizationDetail,
    SankeyData,
    ScoreRequest,
    ScoreResponse,
    SourceFunderDetailResponse,
    SourceFunderEnrichmentRequest,
    SourceFunderListResponse,
    SourceFunderRelinkRequest,
)
from bff.security import Role, require_roles


router = APIRouter(
    prefix="/api/charities",
    tags=["Organization and Grant Data"],
    dependencies=[Depends(require_roles(Role.VIEWER, action="charity.read"))],
)


def _sessions(request: Request):
    return request.app.state.database.sessions()


def _organizations(request: Request) -> OrganizationRepository:
    return OrganizationRepository(_sessions(request))


def _registry(request: Request) -> RegistryRepository:
    return RegistryRepository(_sessions(request))


def _analytics(request: Request) -> AnalyticsRepository:
    return AnalyticsRepository(_sessions(request))


def _funders(request: Request) -> SourceFunderRepository:
    return SourceFunderRepository(_sessions(request))


def _jobs(request: Request) -> PostgresJobRepository:
    return PostgresJobRepository(_sessions(request))


def _split(value: Optional[str]) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _iso_date(value: Optional[str], field: str) -> Optional[str]:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be an ISO date (YYYY-MM-DD)",
        ) from exc


def _date_range(date_from: Optional[str], date_to: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    parsed_from = _iso_date(date_from, "date_from")
    parsed_to = _iso_date(date_to, "date_to")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    return parsed_from, parsed_to


def _actor(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.actor_id if principal else "unknown"


def _idempotency_key(request: Request) -> str:
    return str(request.headers.get("idempotency-key") or "").strip()


def _grant_filters(
    *,
    currency: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    beneficiary_geographies: Optional[str],
    programme_areas: Optional[str],
    donor: Optional[str],
    recipient: Optional[str],
    sources: Optional[str],
) -> dict[str, Any]:
    parsed_from, parsed_to = _date_range(date_from, date_to)
    return {
        "currency": currency,
        "date_from": parsed_from,
        "date_to": parsed_to,
        "beneficiary_geographies": _split(beneficiary_geographies),
        "programme_areas": _split(programme_areas),
        "donor": donor,
        "recipient": recipient,
        "sources": _split(sources) if sources is not None else None,
    }


@router.get("", response_model=list[CharityBase])
async def list_charities(
    search: Optional[str] = None,
    reg_status: Optional[str] = None,
    tag: Optional[str] = None,
    region: Optional[str] = None,
    size: Optional[str] = None,
    tags: Optional[str] = None,
    foundation_regions: Optional[str] = None,
    funding_regions: Optional[str] = None,
    sources: Optional[str] = Query(default=None, max_length=500),
    min_annual_giving: Optional[float] = None,
    max_annual_giving: Optional[float] = Query(default=None, ge=0),
    min_avg_grant_size: Optional[float] = None,
    max_avg_grant_size: Optional[float] = Query(default=None, ge=0),
    include_score: bool = Query(default=False),
    sort: str = Query(default="name_asc", pattern="^(score_desc|income_desc|name_asc)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    repository: OrganizationRepository = Depends(_organizations),
):
    return await repository.list(
        search=search,
        reg_status=reg_status,
        tag=tag,
        region=region,
        size=size,
        tags=_split(tags),
        foundation_regions=_split(foundation_regions),
        funding_regions=_split(funding_regions),
        sources=_split(sources) if sources is not None else None,
        min_annual_giving=min_annual_giving,
        max_annual_giving=max_annual_giving,
        min_avg_grant_size=min_avg_grant_size,
        max_avg_grant_size=max_avg_grant_size,
        include_score=include_score,
        sort=sort,
        skip=skip,
        limit=limit,
    )


@router.get("/stats", response_model=CharityStats)
async def charity_stats(repository: OrganizationRepository = Depends(_organizations)):
    return await repository.stats()


@router.get("/directory/organizations", response_model=RegistryDirectoryPage)
async def registry_page(
    query: Optional[str] = Query(default=None, max_length=160),
    charity_number: Optional[str] = Query(default=None, max_length=64),
    status_filter: Optional[str] = Query(default=None, alias="status", max_length=80),
    income_min: Optional[float] = Query(default=None, ge=0),
    income_max: Optional[float] = Query(default=None, ge=0),
    expenditure_min: Optional[float] = Query(default=None, ge=0),
    expenditure_max: Optional[float] = Query(default=None, ge=0),
    country: Optional[str] = Query(default=None, max_length=8),
    region: Optional[str] = Query(default=None, max_length=120),
    beneficiary_geography: Optional[str] = Query(default=None, max_length=120),
    has_enriched_profile: Optional[bool] = None,
    has_grant_data: Optional[bool] = None,
    cursor: Optional[str] = Query(default=None, max_length=500),
    limit: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default="name", pattern="^(name|income_desc|expenditure_desc)$"),
    repository: RegistryRepository = Depends(_registry),
):
    if income_min is not None and income_max is not None and income_min > income_max:
        raise HTTPException(status_code=400, detail="income_min cannot exceed income_max")
    if expenditure_min is not None and expenditure_max is not None and expenditure_min > expenditure_max:
        raise HTTPException(status_code=400, detail="expenditure_min cannot exceed expenditure_max")
    try:
        return await repository.page(
            query=query,
            charity_number=charity_number,
            status=status_filter,
            income_min=income_min,
            income_max=income_max,
            expenditure_min=expenditure_min,
            expenditure_max=expenditure_max,
            country=country,
            region=region,
            beneficiary_geography=beneficiary_geography,
            has_enriched_profile=has_enriched_profile,
            has_grant_data=has_grant_data,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/directory/organizations/{registry_id}",
    response_model=RegistryOrganizationDetail,
)
async def registry_detail(
    registry_id: str,
    repository: RegistryRepository = Depends(_registry),
):
    result = await repository.detail(registry_id)
    if not result:
        raise HTTPException(status_code=404, detail="Registry organization not found.")
    return result


@router.post(
    "/directory/organizations/enrich",
    response_model=PipelineStatus,
    dependencies=[Depends(require_roles(Role.OPERATOR, action="registry.enrich", idempotent=True))],
)
async def enrich_registry(
    payload: SourceFunderEnrichmentRequest,
    request: Request,
    jobs: PostgresJobRepository = Depends(_jobs),
):
    numbers = sorted({int(value) for value in payload.reg_numbers})
    if len(numbers) != 1:
        raise HTTPException(status_code=400, detail="Exactly one registry organization is required")
    job = await jobs.enqueue(
        "registry_enrichment",
        {"reg_numbers": numbers, "skip_contact_crawler": payload.skip_contact_crawler},
        actor_id=_actor(request),
        idempotency_key=_idempotency_key(request),
    )
    return {
        "status": "running",
        "started_at": job["requested_at"],
        "finished_at": None,
        "last_run_source": "registry_enrichment",
        "error": None,
        "job_id": job["job_id"],
    }


@router.get("/grants/beneficiary-geographies", response_model=list[str])
async def beneficiary_geographies(
    repository: AnalyticsRepository = Depends(_analytics),
):
    return await repository.beneficiary_geographies()


@router.get("/grants/map", response_model=GrantMapResponse)
async def grant_map(
    currency: Optional[str] = None,
    min_coverage: float = Query(default=0.30, ge=0, le=1),
    search: Optional[str] = None,
    tags: Optional[str] = None,
    foundation_regions: Optional[str] = None,
    funding_regions: Optional[str] = None,
    min_annual_giving: Optional[float] = None,
    min_avg_grant_size: Optional[float] = None,
    repository: AnalyticsRepository = Depends(_analytics),
):
    return await repository.map(
        currency=currency,
        min_coverage=min_coverage,
        donor=search,
        programme_areas=_split(tags),
        beneficiary_geographies=_split(funding_regions),
        foundation_regions=_split(foundation_regions),
        min_annual_giving=min_annual_giving,
        min_avg_grant_size=min_avg_grant_size,
    )


@router.get("/grants/overview", response_model=dict[str, Any])
async def grant_overview(
    currency: Optional[str] = Query(default=None, min_length=3, max_length=4),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    beneficiary_geographies: Optional[str] = Query(default=None, max_length=500),
    programme_areas: Optional[str] = Query(default=None, max_length=1000),
    donor: Optional[str] = Query(default=None, max_length=160),
    recipient: Optional[str] = Query(default=None, max_length=160),
    sources: Optional[str] = Query(default=None, max_length=500),
    granularity: str = Query(default="auto", pattern="^(auto|monthly|yearly)$"),
    include_connections: bool = False,
    repository: AnalyticsRepository = Depends(_analytics),
):
    filters = _grant_filters(
        currency=currency, date_from=date_from, date_to=date_to,
        beneficiary_geographies=beneficiary_geographies,
        programme_areas=programme_areas, donor=donor, recipient=recipient,
        sources=sources,
    )
    return await repository.overview(
        **filters,
        granularity=granularity,
        include_connections=include_connections,
    )


@router.get("/grants/overview/entity-suggestions", response_model=dict[str, Any])
async def grant_suggestions(
    sources: Optional[str] = Query(default=None, max_length=500),
    limit: int = Query(default=2500, ge=1, le=5000),
    repository: AnalyticsRepository = Depends(_analytics),
):
    return await repository.suggestions(
        sources=_split(sources) if sources is not None else None,
        limit=limit,
    )


@router.get("/grants/overview/trends", response_model=GrantTrendsResponse)
async def overview_trends(
    currency: Optional[str] = Query(default=None, min_length=3, max_length=4),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    beneficiary_geographies: Optional[str] = Query(default=None, max_length=500),
    programme_areas: Optional[str] = Query(default=None, max_length=1000),
    donor: Optional[str] = Query(default=None, max_length=160),
    recipient: Optional[str] = Query(default=None, max_length=160),
    sources: Optional[str] = Query(default=None, max_length=500),
    granularity: str = Query(default="auto", pattern="^(auto|monthly|yearly)$"),
    repository: AnalyticsRepository = Depends(_analytics),
):
    return await repository.trends(
        **_grant_filters(
            currency=currency, date_from=date_from, date_to=date_to,
            beneficiary_geographies=beneficiary_geographies,
            programme_areas=programme_areas, donor=donor, recipient=recipient,
            sources=sources,
        ),
        granularity=granularity,
    )


@router.get("/grants/overview/drilldown", response_model=dict[str, Any])
async def overview_drilldown(
    selection_type: str = Query(..., pattern="^(period|programme_area)$"),
    selection_value: str = Query(..., min_length=1, max_length=160),
    currency: Optional[str] = Query(default=None, min_length=3, max_length=4),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    beneficiary_geographies: Optional[str] = Query(default=None, max_length=500),
    programme_areas: Optional[str] = Query(default=None, max_length=1000),
    donor: Optional[str] = Query(default=None, max_length=160),
    recipient: Optional[str] = Query(default=None, max_length=160),
    sources: Optional[str] = Query(default=None, max_length=500),
    repository: AnalyticsRepository = Depends(_analytics),
):
    return await repository.drilldown(
        selection_type=selection_type,
        selection_value=selection_value,
        **_grant_filters(
            currency=currency, date_from=date_from, date_to=date_to,
            beneficiary_geographies=beneficiary_geographies,
            programme_areas=programme_areas, donor=donor, recipient=recipient,
            sources=sources,
        ),
    )


@router.post(
    "/grants/funders/enrich",
    response_model=PipelineStatus,
    dependencies=[Depends(require_roles(Role.OPERATOR, action="funder.enrich", idempotent=True))],
)
async def enrich_funders(
    payload: SourceFunderEnrichmentRequest,
    request: Request,
    jobs: PostgresJobRepository = Depends(_jobs),
):
    job = await jobs.enqueue(
        "source_funder_enrichment",
        payload.model_dump(),
        actor_id=_actor(request),
        idempotency_key=_idempotency_key(request),
    )
    return {
        "status": "running",
        "started_at": job["requested_at"],
        "finished_at": None,
        "last_run_source": "source_funder_enrichment",
        "error": None,
        "job_id": job["job_id"],
    }


@router.post(
    "/grants/funders/{source_funder_key}/reset-to-observed",
    dependencies=[Depends(require_roles(Role.ADMINISTRATOR, action="funder.reset", idempotent=True))],
)
async def reset_funder(
    source_funder_key: str,
    request: Request,
    repository: SourceFunderRepository = Depends(_funders),
):
    result = await repository.reset(source_funder_key, actor_id=_actor(request))
    if not result:
        raise HTTPException(status_code=404, detail="Source-funder entry not found.")
    return {"status": "observed_only", **result}


@router.post(
    "/grants/funders/{source_funder_key}/relink",
    dependencies=[Depends(require_roles(Role.ADMINISTRATOR, action="funder.relink", idempotent=True))],
)
async def relink_funder(
    source_funder_key: str,
    payload: SourceFunderRelinkRequest,
    request: Request,
    repository: SourceFunderRepository = Depends(_funders),
):
    try:
        result = await repository.relink(
            source_funder_key, payload.profile_id, actor_id=_actor(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Source-funder entry not found.")
    return {"status": "linked", **result}


@router.post(
    "/grants/funders/{source_funder_key}/profile-cache",
    dependencies=[Depends(require_roles(Role.OPERATOR, action="profile_cache.refresh", idempotent=True))],
)
async def queue_profile_cache(
    source_funder_key: str,
    request: Request,
    repository: SourceFunderRepository = Depends(_funders),
):
    result = await repository.queue_profile_cache(
        source_funder_key,
        actor_id=_actor(request),
        idempotency_key=_idempotency_key(request),
    )
    if not result:
        raise HTTPException(
            status_code=409,
            detail="This source funder has no active organization profile to hydrate.",
        )
    return result


@router.get("/grants/funders/{source_funder_key}/profile-cache")
async def profile_cache(
    source_funder_key: str,
    repository: SourceFunderRepository = Depends(_funders),
):
    result = await repository.profile_cache(source_funder_key)
    if not result:
        raise HTTPException(status_code=404, detail="No source-profile cache is available.")
    return result


@router.get("/grants/funders", response_model=SourceFunderListResponse)
async def source_funders(
    beneficiary_country: str = Query(..., min_length=2, max_length=2, pattern="^[A-Za-z]{2}$"),
    currency: Optional[str] = Query(default=None, min_length=3, max_length=4),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    beneficiary_geographies: Optional[str] = Query(default=None, max_length=500),
    programme_areas: Optional[str] = Query(default=None, max_length=1000),
    donor: Optional[str] = Query(default=None, max_length=160),
    recipient: Optional[str] = Query(default=None, max_length=160),
    sources: Optional[str] = Query(default=None, max_length=500),
    search: Optional[str] = Query(default=None, max_length=160),
    profile_status: str = Query(default="all", pattern="^(all|linked|observed_only)$"),
    sort: str = Query(default="largest_observed_funding", pattern="^(largest_observed_funding|most_grants|most_recently_active|most_active|most_recent)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    repository: SourceFunderRepository = Depends(_funders),
):
    filters = _grant_filters(
        currency=currency, date_from=date_from, date_to=date_to,
        beneficiary_geographies=beneficiary_geographies,
        programme_areas=programme_areas, donor=donor, recipient=recipient,
        sources=sources,
    )
    return await repository.list(
        beneficiary_country=beneficiary_country.upper(),
        **filters,
        search=search,
        profile_status=profile_status,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/grants/funders/{source_funder_key}",
    response_model=SourceFunderDetailResponse,
)
async def source_funder_detail(
    source_funder_key: str,
    beneficiary_country: str = Query(..., min_length=2, max_length=2, pattern="^[A-Za-z]{2}$"),
    currency: Optional[str] = Query(default=None, min_length=3, max_length=4),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    beneficiary_geographies: Optional[str] = Query(default=None, max_length=500),
    programme_areas: Optional[str] = Query(default=None, max_length=1000),
    donor: Optional[str] = Query(default=None, max_length=160),
    recipient: Optional[str] = Query(default=None, max_length=160),
    sources: Optional[str] = Query(default=None, max_length=500),
    detail_level: str = Query(default="full", pattern="^(summary|full)$"),
    repository: SourceFunderRepository = Depends(_funders),
):
    result = await repository.detail(
        source_funder_key,
        beneficiary_country=beneficiary_country.upper(),
        **_grant_filters(
            currency=currency, date_from=date_from, date_to=date_to,
            beneficiary_geographies=beneficiary_geographies,
            programme_areas=programme_areas, donor=donor, recipient=recipient,
            sources=sources,
        ),
        detail_level=detail_level,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Source funder not found in this scope.")
    return result


@router.get("/grants/summary", response_model=GrantNetworkSummary)
async def grant_summary(repository: AnalyticsRepository = Depends(_analytics)):
    return await repository.summary()


@router.get("/grants/trends", response_model=GrantTrendsResponse)
async def grant_trends(
    currency: Optional[str] = None,
    months: int = Query(default=24, ge=1, le=120),
    repository: AnalyticsRepository = Depends(_analytics),
):
    return await repository.trends(currency=currency, months=months)


@router.get("/grants/themes", response_model=GrantThemesResponse)
async def grant_themes(
    currency: Optional[str] = None,
    repository: AnalyticsRepository = Depends(_analytics),
):
    return await repository.themes(currency=currency)


@router.get("/{reg_charity_number}", response_model=CharityDetail)
async def charity_detail(
    reg_charity_number: int,
    repository: OrganizationRepository = Depends(_organizations),
):
    result = await repository.detail(reg_charity_number)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Charity registration number {reg_charity_number} not found.",
        )
    return result


@router.get("/{reg_charity_number}/grants", response_model=GrantListResponse)
async def charity_grants(
    reg_charity_number: int,
    role: str = Query(default="all", pattern="^(all|funder|recipient)$"),
    repository: OrganizationRepository = Depends(_organizations),
):
    if not await repository.detail(reg_charity_number):
        raise HTTPException(status_code=404, detail="Organization not found.")
    return await repository.grants(reg_charity_number, role)


@router.get("/{reg_charity_number}/sankey", response_model=SankeyData)
async def charity_sankey(
    reg_charity_number: int,
    currency: Optional[str] = None,
    limit: int = Query(default=30, ge=1, le=100),
    repository: OrganizationRepository = Depends(_organizations),
):
    if not await repository.detail(reg_charity_number):
        raise HTTPException(status_code=404, detail="Organization not found.")
    return await repository.sankey(
        reg_charity_number,
        currency=currency,
        limit=limit,
    )


@router.post(
    "/{reg_charity_number}/score",
    response_model=ScoreResponse,
    dependencies=[Depends(require_roles(Role.ANALYST, action="score.calculate"))],
)
async def charity_score(
    reg_charity_number: int,
    payload: ScoreRequest,
    repository: OrganizationRepository = Depends(_organizations),
):
    profile = payload.target_profile.model_dump(exclude_none=True) if payload.target_profile else None
    try:
        return await repository.score(reg_charity_number, profile)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Organization not found.") from exc
