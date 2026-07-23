from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from bff.auth import get_current_user_token
from bff.schemas import (
    CharityBase,
    CharityDetail,
    CharityStats,
    GrantMapResponse,
    GrantListResponse,
    GrantNetworkSummary,
    SankeyData,
    ScoreRequest,
    ScoreResponse,
)
from bff.repositories import CharityRepository, get_charity_repository

router = APIRouter(
    prefix="/api/charities", 
    tags=["Charity Commission Data"],
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
    min_annual_giving: Optional[float] = None,
    min_avg_grant_size: Optional[float] = None,
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

    return await repo.get_all(
        search=search, 
        reg_status=reg_status, 
        tag=tag, 
        region=region, 
        size=size,
        tags=tags_list,
        foundation_regions=foundation_regions_list,
        funding_regions=funding_regions_list,
        min_annual_giving=min_annual_giving,
        min_avg_grant_size=min_avg_grant_size,
        skip=skip, 
        limit=limit
    )

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
    repo: CharityRepository = Depends(get_charity_repository),
):
    """
    Returns aggregated grant financial and transaction details grouped by geographic region.
    Used for showing donation distributions on the dashboard map.
    Requires a valid session cookie/token.
    """
    if min_coverage < 0 or min_coverage > 1:
        raise HTTPException(status_code=400, detail="min_coverage must be between 0 and 1")
    return await repo.get_grants_map(currency=currency, min_coverage=min_coverage)


@router.get("/grants/summary", response_model=GrantNetworkSummary)
async def get_grants_summary(repo: CharityRepository = Depends(get_charity_repository)):
    """Return currency-separated transaction totals and leading organizations."""
    return await repo.get_grant_summary()

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
    Returns structured nodes and links for a financial flow Sankey diagram for a specific charity.
    Calculates inflows (grants, donations) and outflows (grants made, operational expenses, reserves additions/drawdowns).
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
