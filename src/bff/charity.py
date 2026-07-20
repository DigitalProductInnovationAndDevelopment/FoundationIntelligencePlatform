from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from bff.auth import get_current_user_token
from bff.schemas import CharityBase, CharityDetail, CharityStats, GrantMapItem, GrantDetail, SankeyData
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
    skip: int = 0,
    limit: int = 20,
    repo: CharityRepository = Depends(get_charity_repository)
):
    """
    Returns a paginated list of charities, filtered by name, registration status,
    thematic tag, geographic focus region, or annual giving size.
    Requires a valid session cookie/token.
    """
    return await repo.get_all(
        search=search, 
        reg_status=reg_status, 
        tag=tag, 
        region=region, 
        size=size,
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

@router.get("/grants/map", response_model=List[GrantMapItem])
async def get_grants_map(repo: CharityRepository = Depends(get_charity_repository)):
    """
    Returns aggregated grant financial and transaction details grouped by geographic region.
    Used for showing donation distributions on the dashboard map.
    Requires a valid session cookie/token.
    """
    return await repo.get_grants_map()

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

@router.get("/{reg_charity_number}/grants", response_model=List[GrantDetail])
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
    return await repo.get_grants_for_charity(reg_charity_number, role=role)

@router.get("/{reg_charity_number}/sankey", response_model=SankeyData)
async def get_charity_sankey(
    reg_charity_number: int,
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
    return await repo.get_sankey_data(reg_charity_number)
