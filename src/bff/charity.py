import asyncio
import json
import os
import sqlite3
from datetime import date
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool
from typing import Any, Dict, List, Optional
from bff.auth import get_current_user_token
from bff.config import DB_PATH
from bff.schemas import (
    CharityBase,
    CharityDetail,
    CharityStats,
    GrantMapResponse,
    GrantListResponse,
    GrantNetworkSummary,
    SourceFunderDetailResponse,
    SourceFunderListResponse,
    GrantThemesResponse,
    GrantTrendsResponse,
    RegistryDirectoryPage,
    RegistryOrganizationDetail,
    SankeyData,
    ScoreRequest,
    ScoreResponse,
    PipelineStatus,
    SourceFunderEnrichmentRequest,
)
from bff.repositories import CharityRepository, get_charity_repository
from data.registry import REGISTRY_LINK_TABLE, REGISTRY_TABLE, normalize_organization_name
from preprocessing.enrichment import enrich_organization

router = APIRouter(
    prefix="/api/charities", 
    tags=["Organization and Grant Data"],
    dependencies=[Depends(get_current_user_token)]
)

@router.get("", response_model=List[CharityBase])
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
    skip: int = 0,
    limit: int = 20,
    repo: CharityRepository = Depends(get_charity_repository)
):
    """
    Returns a paginated list of charities, filtered by name, registration status,
    thematic tag, geographic focus region, or annual giving size.
    Requires a valid session cookie/token.
    """
    # Parse comma-separated strings to list of strings
    tags_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    foundation_regions_list = [r.strip() for r in foundation_regions.split(",") if r.strip()] if foundation_regions else None
    funding_regions_list = [r.strip() for r in funding_regions.split(",") if r.strip()] if funding_regions else None
    sources_list = [source.strip() for source in sources.split(",") if source.strip()] if sources is not None else None

    return await repo.get_all(
        search=search, 
        reg_status=reg_status, 
        tag=tag, 
        region=region, 
        size=size,
        tags=tags_list,
        foundation_regions=foundation_regions_list,
        funding_regions=funding_regions_list,
        sources=sources_list,
        min_annual_giving=min_annual_giving,
        max_annual_giving=max_annual_giving,
        min_avg_grant_size=min_avg_grant_size,
        max_avg_grant_size=max_avg_grant_size,
        include_score=include_score,
        sort=sort,
        skip=skip, 
        limit=limit
    )


@router.get("/grants/beneficiary-geographies", response_model=List[str])
async def list_beneficiary_geographies(
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return every beneficiary country currently represented in observed grants.

    This keeps the Directory's beneficiary filter aligned with the actual map
    taxonomy instead of maintaining a short, stale front-end list.
    """
    return await repo.get_beneficiary_geography_options(sources=["360Giving"])


@router.get("/directory/organizations", response_model=RegistryDirectoryPage)
async def list_registry_organizations(
    query: Optional[str] = Query(default=None, max_length=160),
    charity_number: Optional[str] = Query(default=None, max_length=64),
    status: Optional[str] = Query(default=None, max_length=80),
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
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return a bounded, cursor-paginated official registry directory page.

    The response is intentionally summary-only. Registry presence never implies
    observed funding data, and no grant histories are serialized here.
    """
    if income_min is not None and income_max is not None and income_min > income_max:
        raise HTTPException(status_code=400, detail="income_min cannot exceed income_max")
    if expenditure_min is not None and expenditure_max is not None and expenditure_min > expenditure_max:
        raise HTTPException(status_code=400, detail="expenditure_min cannot exceed expenditure_max")
    try:
        return await repo.get_registry_page(
            query=query,
            charity_number=charity_number,
            status=status,
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


@router.get("/directory/organizations/{registry_id}", response_model=RegistryOrganizationDetail)
async def get_registry_organization_detail(
    registry_id: str,
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Load one registry record lazily, plus only an accepted enriched link."""
    organization = await repo.get_registry_detail(registry_id)
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registry organization not found.")
    return organization

@router.get("/stats", response_model=CharityStats)
async def get_charity_stats(repo: CharityRepository = Depends(get_charity_repository)):
    """
    Returns aggregated dashboard statistics for charities in the register.
    Requires a valid session cookie/token.
    """
    return await repo.get_stats()

@router.get("/grants/map", response_model=GrantMapResponse)
async def get_grants_map(
    currency: Optional[str] = None,
    min_coverage: float = 0.30,
    search: Optional[str] = None,
    tags: Optional[str] = None,
    foundation_regions: Optional[str] = None,
    funding_regions: Optional[str] = None,
    min_annual_giving: Optional[float] = None,
    min_avg_grant_size: Optional[float] = None,
    repo: CharityRepository = Depends(get_charity_repository),
):
    """
    Returns stored grant transactions grouped by normalized beneficiary geography.
    Directory-style filters can scope the grant rows. Headquarters remain excluded from
    beneficiary geography and are used only for separately disclosed illustrative connections.
    Requires a valid session cookie/token.
    """
    if min_coverage < 0 or min_coverage > 1:
        raise HTTPException(status_code=400, detail="min_coverage must be between 0 and 1")
    tags_list = [value.strip() for value in tags.split(",") if value.strip()] if tags else None
    foundation_regions_list = (
        [value.strip() for value in foundation_regions.split(",") if value.strip()]
        if foundation_regions else None
    )
    funding_regions_list = (
        [value.strip() for value in funding_regions.split(",") if value.strip()]
        if funding_regions else None
    )
    return await repo.get_grants_map(
        currency=currency,
        min_coverage=min_coverage,
        search=search,
        tags=tags_list,
        foundation_regions=foundation_regions_list,
        funding_regions=funding_regions_list,
        min_annual_giving=min_annual_giving,
        min_avg_grant_size=min_avg_grant_size,
    )


@router.get("/grants/overview", response_model=Dict[str, Any])
async def get_filtered_grant_overview(
    currency: Optional[str] = Query(default=None, min_length=3, max_length=4),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    beneficiary_geographies: Optional[str] = Query(default=None, max_length=500),
    programme_areas: Optional[str] = Query(default=None, max_length=1000),
    donor: Optional[str] = Query(default=None, max_length=160),
    recipient: Optional[str] = Query(default=None, max_length=160),
    sources: Optional[str] = Query(default=None, max_length=500),
    granularity: str = Query(default="auto", pattern="^(auto|monthly|yearly)$"),
    include_connections: bool = Query(default=False),
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return map, KPIs, trend and programme allocation from one grant scope.

    This is deliberately separate from organization-directory filters. Every
    field describes a stored 360Giving grant, and all aggregation happens in the
    BFF rather than by sending transaction rows to the browser.
    """
    def parse_iso(value: Optional[str], field: str) -> Optional[str]:
        if value is None or not value.strip():
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field} must be an ISO date (YYYY-MM-DD)") from exc

    parsed_from = parse_iso(date_from, "date_from")
    parsed_to = parse_iso(date_to, "date_to")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    split_values = lambda value: [item.strip() for item in (value or "").split(",") if item.strip()]
    try:
        return await run_in_threadpool(
            lambda: asyncio.run(repo.get_grant_overview(
                currency=currency,
                date_from=parsed_from,
                date_to=parsed_to,
                beneficiary_geographies=split_values(beneficiary_geographies),
                programme_areas=split_values(programme_areas),
                donor=donor,
                recipient=recipient,
                sources=split_values(sources) if sources is not None else None,
                granularity=granularity,
                include_connections=include_connections,
            ))
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/grants/overview/entity-suggestions", response_model=Dict[str, Any])
async def get_grant_entity_suggestions(
    sources: Optional[str] = Query(default=None, max_length=500),
    limit: int = Query(default=2_500, ge=1, le=5_000),
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return a source-scoped cache of observed donor and recipient names.

    The browser filters this response locally as the user types. No Overview
    aggregation is refreshed until the user explicitly applies their draft.
    """
    return await repo.get_grant_entity_suggestions(
        sources=_split_grant_filter(sources) if sources is not None else None,
        limit=limit,
    )


@router.get("/grants/overview/trends", response_model=GrantTrendsResponse)
async def get_filtered_grant_overview_trends(
    currency: Optional[str] = Query(default=None, min_length=3, max_length=4),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    beneficiary_geographies: Optional[str] = Query(default=None, max_length=500),
    programme_areas: Optional[str] = Query(default=None, max_length=1000),
    donor: Optional[str] = Query(default=None, max_length=160),
    recipient: Optional[str] = Query(default=None, max_length=160),
    sources: Optional[str] = Query(default=None, max_length=500),
    granularity: str = Query(default="auto", pattern="^(auto|monthly|yearly)$"),
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return a filtered trend without recalculating the map and theme cards."""
    def parse_iso(value: Optional[str], field: str) -> Optional[str]:
        if value is None or not value.strip():
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field} must be an ISO date (YYYY-MM-DD)") from exc

    parsed_from = parse_iso(date_from, "date_from")
    parsed_to = parse_iso(date_to, "date_to")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    split_values = lambda value: [item.strip() for item in (value or "").split(",") if item.strip()]
    try:
        return await run_in_threadpool(
            lambda: asyncio.run(repo.get_grant_overview_trends(
                currency=currency,
                date_from=parsed_from,
                date_to=parsed_to,
                beneficiary_geographies=split_values(beneficiary_geographies),
                programme_areas=split_values(programme_areas),
                donor=donor,
                recipient=recipient,
                sources=split_values(sources) if sources is not None else None,
                granularity=granularity,
            ))
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/grants/overview/drilldown", response_model=Dict[str, Any])
async def get_grant_overview_drilldown(
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
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return a bounded funder, recipient, and grant slice for one chart value."""
    parsed_from = _parse_grant_date(date_from, "date_from")
    parsed_to = _parse_grant_date(date_to, "date_to")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    try:
        return await repo.get_grant_overview_drilldown(
            selection_type=selection_type,
            selection_value=selection_value,
            currency=currency,
            date_from=parsed_from,
            date_to=parsed_to,
            beneficiary_geographies=_split_grant_filter(beneficiary_geographies),
            programme_areas=_split_grant_filter(programme_areas),
            donor=donor,
            recipient=recipient,
            sources=_split_grant_filter(sources) if sources is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_grant_date(value: Optional[str], field: str) -> Optional[str]:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be an ISO date (YYYY-MM-DD)") from exc


def _split_grant_filter(value: Optional[str]) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _official_charity_url(charity_number: int) -> str:
    """Return the stable public Charity Commission detail URL for a record."""
    return (
        "https://register-of-charities.charitycommission.gov.uk/"
        f"en/charity-search/-/charity-details/{charity_number}"
    )


def _fast_link_confirmed_profiles(reg_numbers: List[int]) -> List[Dict[str, Any]]:
    """Create lightweight profiles from cached official records and link known grants.

    This intentionally performs no network calls, database clone, currency
    backfill, or overview-index rebuild.  It only turns a confirmed cached
    Charity Commission record into an Organization Research profile and links
    grants whose publisher supplied the exact ``GB-CHC-<number>`` identifier.
    The operation is one small SQLite transaction so the directory can open a
    profile immediately after the user confirms it.
    """
    if not os.path.exists(DB_PATH):
        raise RuntimeError("The local organization database is unavailable.")

    conn = sqlite3.connect(DB_PATH, timeout=8)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 8000")
        conn.execute("BEGIN IMMEDIATE")
        profiles: List[Dict[str, Any]] = []
        now = datetime.now().astimezone().isoformat()

        for requested_number in reg_numbers:
            registry = conn.execute(
                f"""
                SELECT registry_id, charity_number, registered_name,
                       registration_status, income, expenditure,
                       address_line_one, address_line_two, address_line_three,
                       address_line_four, address_line_five, postcode, city,
                       administrative_region, country_code, activity_text,
                       source_name, source_record_updated_at
                FROM {REGISTRY_TABLE}
                WHERE charity_number = ? OR linked_charity_number = ?
                ORDER BY CASE WHEN charity_number = ? THEN 0 ELSE 1 END,
                         registry_id
                LIMIT 1
                """,
                (str(requested_number), str(requested_number), str(requested_number)),
            ).fetchone()
            if not registry:
                raise ValueError(
                    f"No cached Charity Commission record was found for #{requested_number}."
                )

            try:
                charity_number = int(str(registry["charity_number"]).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"The cached Charity Commission record for #{requested_number} has no usable charity number."
                ) from exc

            existing = conn.execute(
                "SELECT charity_id, name FROM charities WHERE charity_id = ?",
                (charity_number,),
            ).fetchone()
            if not existing:
                address_parts = [
                    registry[key]
                    for key in (
                        "address_line_one", "address_line_two", "address_line_three",
                        "address_line_four", "address_line_five", "postcode",
                    )
                    if registry[key]
                ]
                source_url = _official_charity_url(charity_number)
                raw_cc_data = {
                    "registered_charity_number": charity_number,
                    "link": source_url,
                    "all_details": {
                        "charity_name": registry["registered_name"],
                        "reg_status": registry["registration_status"],
                        "latest_income": registry["income"],
                        "latest_expenditure": registry["expenditure"],
                        "activities": registry["activity_text"],
                        "address_country": registry["country_code"],
                    },
                    "description": registry["activity_text"],
                    "registry_id": registry["registry_id"],
                }
                profile_seed: Dict[str, Any] = {
                    "charity_id": charity_number,
                    "name": registry["registered_name"],
                    "type": "Charity",
                    "address": ", ".join(str(value) for value in address_parts),
                    "city": registry["city"],
                    "state": registry["administrative_region"],
                    "country": registry["country_code"],
                    "annual_income": registry["income"],
                    "annual_expenditure": registry["expenditure"],
                    "raw_cc_data": raw_cc_data,
                }
                enrichment = enrich_organization(profile_seed)
                conn.execute(
                    """
                    INSERT INTO charities (
                        charity_id, name, type, address, city, state, country,
                        annual_income, annual_expenditure, raw_cc_data,
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
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        charity_number,
                        registry["registered_name"],
                        "Charity",
                        profile_seed["address"],
                        registry["city"],
                        registry["administrative_region"],
                        registry["country_code"],
                        registry["income"],
                        registry["expenditure"],
                        json.dumps(raw_cc_data, ensure_ascii=False),
                        json.dumps(enrichment["programme_areas_source"], ensure_ascii=False),
                        json.dumps(enrichment["programme_areas_inferred"], ensure_ascii=False),
                        json.dumps(enrichment["programme_area_scores"], ensure_ascii=False),
                        enrichment["programme_area_method"],
                        json.dumps(enrichment["programme_area_evidence"], ensure_ascii=False),
                        int(bool(enrichment["programme_area_review_required"])),
                        json.dumps(enrichment["geographic_focus_source"], ensure_ascii=False),
                        json.dumps(enrichment["geographic_focus_inferred"], ensure_ascii=False),
                        enrichment["headquarters_country"],
                        enrichment["headquarters_region"],
                        enrichment["geography_method"],
                        enrichment["geography_confidence"],
                        json.dumps(enrichment["geography_evidence"], ensure_ascii=False),
                        int(bool(enrichment["geography_review_required"])),
                        enrichment["enrichment_rule_version"],
                        json.dumps(enrichment["enrichment_review_reasons"], ensure_ascii=False),
                        int(bool(enrichment["insufficient_source_text"])),
                        normalize_organization_name(registry["registered_name"]),
                        "charity",
                        registry["source_name"],
                        json.dumps([registry["source_name"]], ensure_ascii=False),
                        registry["registry_id"],
                        source_url,
                        json.dumps([{
                            "source": registry["source_name"],
                            "record_id": registry["registry_id"],
                            "url": source_url,
                            "record_updated_at": registry["source_record_updated_at"],
                        }], ensure_ascii=False),
                        now,
                        "source_without_transactions",
                    ),
                )

            source_identifier = f"GB-CHC-{charity_number}"
            linked_grants = conn.execute(
                """
                UPDATE grants
                   SET funding_charity_id = ?
                 WHERE funding_charity_id IS NULL
                   AND funding_org_source_id = ?
                """,
                (charity_number, source_identifier),
            ).rowcount
            linked_facts = conn.execute(
                """
                UPDATE grant_source_funder_facts
                   SET linked_profile_id = ?
                 WHERE linked_profile_id IS NULL
                   AND source_organization_id = ?
                """,
                (charity_number, source_identifier),
            ).rowcount
            # Facts can occasionally predate the source-ID projection. Repair
            # only rows whose underlying grant now has this exact profile link.
            linked_facts += conn.execute(
                """
                UPDATE grant_source_funder_facts
                   SET linked_profile_id = ?
                 WHERE linked_profile_id IS NULL
                   AND grant_id IN (
                       SELECT grant_id
                       FROM grants
                       WHERE funding_charity_id = ?
                   )
                """,
                (charity_number, charity_number),
            ).rowcount
            observed_grant_count = conn.execute(
                "SELECT COUNT(*) FROM grants WHERE funding_charity_id = ?",
                (charity_number,),
            ).fetchone()[0]
            conn.execute(
                """
                UPDATE charities
                   SET transaction_coverage = ?
                 WHERE charity_id = ?
                """,
                (
                    "observed_grants_linked" if observed_grant_count else "source_without_transactions",
                    charity_number,
                ),
            )
            conn.execute(
                f"""
                INSERT INTO {REGISTRY_LINK_TABLE} (
                    registry_id, enriched_organization_id, match_status,
                    match_method, match_confidence, match_reason, reviewed_at,
                    created_at, updated_at
                ) VALUES (?, ?, 'accepted', 'exact_identifier', 1.0, ?, ?, ?, ?)
                ON CONFLICT(registry_id, enriched_organization_id) DO UPDATE SET
                    match_status = 'accepted',
                    match_method = 'exact_identifier',
                    match_confidence = 1.0,
                    match_reason = excluded.match_reason,
                    reviewed_at = excluded.reviewed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    registry["registry_id"],
                    charity_number,
                    "Confirmed Charity Commission number selected by the user.",
                    now,
                    now,
                    now,
                ),
            )
            profiles.append({
                "charity_id": charity_number,
                "name": str(registry["registered_name"]),
                "linked_grants": int(observed_grant_count),
                "newly_linked_grants": int(linked_grants),
                "linked_source_facts": int(linked_facts),
            })
        conn.commit()
        return profiles
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _start_confirmed_profile_enrichment(
    payload: SourceFunderEnrichmentRequest,
    *,
    run_source: str,
) -> Dict[str, Any]:
    """Immediately create confirmed local links without a blocking source scrape.

    A full source refresh is deliberately *not* launched from this interaction:
    the user-selected profile already has a locally cached official record and
    current observed grants. Keeping the refresh as an explicit pipeline action
    avoids a database-wide ECB backfill and index rebuild on every profile click.
    """
    reg_numbers = sorted({int(value) for value in payload.reg_numbers})
    if not reg_numbers:
        raise HTTPException(status_code=400, detail="Select at least one Charity Commission number.")
    if len(reg_numbers) > 5:
        raise HTTPException(status_code=400, detail="A donor-directory enrichment run may include at most five organizations.")
    try:
        profiles = await run_in_threadpool(_fast_link_confirmed_profiles, reg_numbers)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local profile database is busy. Please try again in a moment.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    now = datetime.now().astimezone().isoformat()
    return {
        "status": "success",
        "started_at": now,
        "finished_at": now,
        "last_run_source": run_source,
        "error": None,
        "profiles": profiles,
    }


@router.post("/grants/funders/enrich", response_model=PipelineStatus)
async def enrich_source_funders(
    payload: SourceFunderEnrichmentRequest,
):
    """Link up to five confirmed cached Charity Commission records immediately."""
    return await _start_confirmed_profile_enrichment(
        payload,
        run_source="directory_enrichment",
    )


@router.post("/directory/organizations/enrich", response_model=PipelineStatus)
async def enrich_registry_organization(
    payload: SourceFunderEnrichmentRequest,
):
    """Create one cached official profile and attach already observed grants."""
    if len({int(value) for value in payload.reg_numbers}) != 1:
        raise HTTPException(
            status_code=400,
            detail="The registry detail action can enrich exactly one organization at a time.",
        )
    return await _start_confirmed_profile_enrichment(
        payload,
        run_source="registry_enrichment",
    )


@router.get("/grants/funders", response_model=SourceFunderListResponse)
async def list_source_funders(
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
    profile_status: str = Query(
        default="all", pattern="^(all|linked|observed_only)$",
    ),
    sort: str = Query(default="largest_observed_funding", pattern="^(largest_observed_funding|most_grants|most_recently_active|most_active|most_recent)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Rank source-reported funders active in one map beneficiary country.

    This is intentionally not the verified Organisation Directory. Results may
    be source-only entities; a directory link appears only when one already
    exists and has not been inferred by this endpoint.
    """
    parsed_from = _parse_grant_date(date_from, "date_from")
    parsed_to = _parse_grant_date(date_to, "date_to")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    try:
        return await repo.get_source_funders(
            beneficiary_country=beneficiary_country.upper(),
            currency=currency,
            date_from=parsed_from,
            date_to=parsed_to,
            beneficiary_geographies=_split_grant_filter(beneficiary_geographies),
            programme_areas=_split_grant_filter(programme_areas),
            donor=donor,
            recipient=recipient,
            sources=_split_grant_filter(sources) if sources is not None else None,
            search=search,
            profile_status=profile_status,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/grants/funders/{source_funder_key}", response_model=SourceFunderDetailResponse)
async def get_source_funder_detail(
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
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return source-funder activity detail without treating it as a profile."""
    parsed_from = _parse_grant_date(date_from, "date_from")
    parsed_to = _parse_grant_date(date_to, "date_to")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    try:
        detail = await repo.get_source_funder_detail(
            source_funder_key,
            beneficiary_country=beneficiary_country.upper(),
            currency=currency,
            date_from=parsed_from,
            date_to=parsed_to,
            beneficiary_geographies=_split_grant_filter(beneficiary_geographies),
            programme_areas=_split_grant_filter(programme_areas),
            donor=donor,
            recipient=recipient,
            sources=_split_grant_filter(sources) if sources is not None else None,
            detail_level=detail_level,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source funder not found in this grant scope.")
    return detail


@router.get("/grants/summary", response_model=GrantNetworkSummary)
async def get_grants_summary(repo: CharityRepository = Depends(get_charity_repository)):
    """Return currency-separated transaction totals and leading organizations."""
    return await repo.get_grant_summary()


@router.get("/grants/trends", response_model=GrantTrendsResponse)
async def get_grant_trends(
    currency: Optional[str] = None,
    months: int = Query(default=24, ge=1, le=120),
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Aggregate cached 360Giving awards by award-date month without filling unknown coverage."""
    return await repo.get_grant_trends(currency=currency, months=months)


@router.get("/grants/themes", response_model=GrantThemesResponse)
async def get_grant_themes(
    currency: Optional[str] = None,
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Allocate cached grant amounts across auditable normalized programme areas."""
    return await repo.get_grant_themes(currency=currency)

@router.get("/{reg_charity_number}", response_model=CharityDetail)
async def get_charity_detail(
    reg_charity_number: int,
    repo: CharityRepository = Depends(get_charity_repository)
):
    """
    Returns full details for a specific charity by registration number,
    including financial history, assets/liabilities, and native classifications.
    Requires a valid session cookie/token.
    """
    charity = await repo.get_by_id(reg_charity_number)
    if not charity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Charity registration number {reg_charity_number} not found."
        )
    return charity

@router.get("/{reg_charity_number}/grants", response_model=GrantListResponse)
async def get_charity_grants(
    reg_charity_number: int,
    role: str = "all",
    repo: CharityRepository = Depends(get_charity_repository)
):
    """
    Returns a list of all grants made or received by a specific charity.
    Filter by role: 'funder' (grants made), 'recipient' (grants received), or 'all'.
    Requires a valid session cookie/token.
    """
    charity = await repo.get_by_id(reg_charity_number)
    if not charity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Charity registration number {reg_charity_number} not found."
        )
    if role.lower() not in {"all", "funder", "recipient"}:
        raise HTTPException(status_code=400, detail="role must be all, funder, or recipient")
    return await repo.get_grants_for_charity(reg_charity_number, role=role)

@router.get("/{reg_charity_number}/sankey", response_model=SankeyData)
async def get_charity_sankey(
    reg_charity_number: int,
    currency: Optional[str] = None,
    limit: int = 30,
    repo: CharityRepository = Depends(get_charity_repository)
):
    """
    Returns donor-to-recipient nodes and links built only from stored grant transactions.
    It does not infer operating expenses, reserves, or unrecorded donations.
    Requires a valid session cookie/token.
    """
    charity = await repo.get_by_id(reg_charity_number)
    if not charity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Charity registration number {reg_charity_number} not found."
        )
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return await repo.get_sankey_data(reg_charity_number, currency=currency, limit=limit)


@router.post("/{reg_charity_number}/score", response_model=ScoreResponse)
async def score_charity_relevance(
    reg_charity_number: int,
    request: ScoreRequest,
    repo: CharityRepository = Depends(get_charity_repository),
):
    """Return an explainable experimental target-profile relevance score."""
    charity = await repo.get_by_id(reg_charity_number)
    if not charity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {reg_charity_number} not found.",
        )
    profile = request.target_profile.model_dump(exclude_none=True) if request.target_profile else None
    return await repo.get_score(reg_charity_number, target_profile=profile)
