"""PostgreSQL-only API surface enabled for staging and production.

The remaining domain routes are ported in Phase 5. Until then they are absent
from staging/production rather than silently falling back to SQLite.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from bff.postgres.registry_repository import RegistrySearchRepository
from bff.schemas import RegistryDirectoryPage
from bff.security import Role, require_roles


router = APIRouter(
    prefix="/api/charities",
    tags=["Organization and Grant Data"],
    dependencies=[Depends(require_roles(Role.VIEWER, action="charity.read"))],
)


def _registry_repository(request: Request) -> RegistrySearchRepository:
    return RegistrySearchRepository(request.app.state.database.sessions())


@router.get("/directory/organizations", response_model=RegistryDirectoryPage)
async def search_registry_organizations(
    query: str = Query(min_length=2, max_length=160),
    status: Optional[str] = Query(default=None, max_length=80),
    cursor: Optional[str] = Query(default=None, max_length=2048),
    limit: int = Query(default=50, ge=1, le=100),
    repository: RegistrySearchRepository = Depends(_registry_repository),
):
    """Search the active registry dataset with deterministic cursor ordering."""
    try:
        result = await repository.search(
            query,
            registration_status=status,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    items = result["items"]
    return {
        "results": items,
        "next_cursor": result["next_cursor"],
        "has_more": result["next_cursor"] is not None,
        "applied_filters": {"query": query, "status": status},
        "page_size": len(items),
        "search_strategy": "postgresql_tsvector_trigram",
    }
