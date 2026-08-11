"""Sanitized customer-visible scraper status without operational controls."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from bff.postgres.job_repository import PostgresJobRepository
from bff.postgres.pipeline_repository import PipelineRepository
from bff.security import Role, require_roles


class PublicSourceStatus(BaseModel):
    name: str
    enabled: bool
    freshness: str
    last_success_at: Optional[str] = None
    record_count: Optional[int] = None


class PublicScraperStatus(BaseModel):
    status: str
    last_run: Optional[str] = None
    last_successful_run: Optional[str] = None
    sources: list[PublicSourceStatus]


router = APIRouter(
    prefix="/api/scraper",
    tags=["Scraper Status"],
    dependencies=[Depends(require_roles(Role.CUSTOMER, action="scraper.status.read"))],
)


def _jobs(request: Request) -> PostgresJobRepository:
    return PostgresJobRepository(request.app.state.database.sessions())


def _pipelines(request: Request) -> PipelineRepository:
    return PipelineRepository(request.app.state.database.sessions())


def _freshness(last_success_at: Optional[str], sla_hours: int, enabled: bool) -> str:
    if not enabled:
        return "disabled"
    if not last_success_at:
        return "never"
    try:
        observed = datetime.fromisoformat(last_success_at.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() / 3600
    except (TypeError, ValueError):
        return "unknown"
    return "fresh" if age_hours <= sla_hours else "stale"


@router.get("/status", response_model=PublicScraperStatus)
async def scraper_status(
    jobs: PostgresJobRepository = Depends(_jobs),
    pipelines: PipelineRepository = Depends(_pipelines),
):
    latest = await jobs.latest_status()
    sources = await pipelines.public_source_statuses()
    successful = [source["last_success_at"] for source in sources if source["last_success_at"]]
    return {
        "status": latest["status"],
        "last_run": latest.get("finished_at") or latest.get("started_at"),
        "last_successful_run": max(successful) if successful else None,
        "sources": [
            {
                "name": source["name"],
                "enabled": source["enabled"],
                "freshness": _freshness(
                    source["last_success_at"],
                    source["freshness_sla_hours"],
                    source["enabled"],
                ),
                "last_success_at": source["last_success_at"],
                "record_count": source["record_count"],
            }
            for source in sources
        ],
    }


__all__ = ["router"]
