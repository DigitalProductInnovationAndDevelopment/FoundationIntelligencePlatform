from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from bff.auth import get_current_user_token
from bff.schemas import CharityBase, CharityDetail, CharityStats
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
    skip: int = 0,
    limit: int = 20,
    repo: CharityRepository = Depends(get_charity_repository)
):
    """
    Returns a paginated list of charities, filtered by name and/or registration status.
    Requires a valid session cookie.
    """
    return await repo.get_all(search=search, reg_status=reg_status, skip=skip, limit=limit)

@router.get("/stats", response_model=CharityStats)
async def get_charity_stats(repo: CharityRepository = Depends(get_charity_repository)):
    """
    Returns aggregated dashboard statistics for charities in the register.
    Requires a valid session cookie.
    """
    return await repo.get_stats()

@router.get("/{reg_charity_number}", response_model=CharityDetail)
async def get_charity_detail(
    reg_charity_number: int,
    repo: CharityRepository = Depends(get_charity_repository)
):
    """
    Returns full details for a specific charity by registration number,
    including financial history and assets/liabilities.
    Requires a valid session cookie.
    """
    charity = await repo.get_by_id(reg_charity_number)
    if not charity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Charity registration number {reg_charity_number} not found."
        )
    return charity
