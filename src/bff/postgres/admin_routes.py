"""PostgreSQL-backed pipeline administration without local subprocess state."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from bff.postgres.job_repository import PIPELINE_JOB_TYPES, PostgresJobRepository
from bff.postgres.pipeline_repository import PipelineRepository
from bff.schemas import PipelineStatus, PipelineTrigger, SourceScheduleUpdate
from bff.security import Role, require_roles
from bff.utils.logging import redact_text
from pipelines.durable import redacted_configuration


router = APIRouter(
    prefix="/api/admin",
    tags=["Administrative & Monitoring Data"],
    dependencies=[Depends(require_roles(Role.OPERATOR, action="administration.access"))],
)


def _jobs(request: Request) -> PostgresJobRepository:
    return PostgresJobRepository(request.app.state.database.sessions())


def _actor(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.actor_id if principal else "unknown"


def _pipelines(request: Request) -> PipelineRepository:
    return PipelineRepository(request.app.state.database.sessions())


@router.get("/pipeline/status", response_model=PipelineStatus)
async def get_pipeline_status(
    repository: PostgresJobRepository = Depends(_jobs),
):
    return await repository.latest_status()


@router.post(
    "/pipeline/trigger",
    response_model=PipelineStatus,
    dependencies=[Depends(require_roles(Role.OPERATOR, action="pipeline.trigger", idempotent=True))],
)
async def trigger_pipeline(
    payload: PipelineTrigger,
    request: Request,
    repository: PostgresJobRepository = Depends(_jobs),
):
    if payload.source not in PIPELINE_JOB_TYPES[:4]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported execution mode: {payload.source}",
        )
    job = await repository.enqueue(
        payload.source,
        payload.model_dump(exclude={"source"}),
        actor_id=_actor(request),
        idempotency_key=str(request.headers.get("idempotency-key") or "").strip(),
    )
    return {
        "status": job["status"],
        "started_at": job["requested_at"],
        "finished_at": None,
        "last_run_source": payload.source,
        "error": None,
        "job_id": job["job_id"],
    }


@router.get("/pipeline/jobs")
async def get_pipeline_jobs(
    limit: int = Query(default=50, ge=1, le=100),
    repository: PostgresJobRepository = Depends(_jobs),
):
    return {"jobs": await repository.history(limit=limit)}


@router.get(
    "/pipeline/logs",
    dependencies=[Depends(require_roles(Role.ADMINISTRATOR, action="pipeline.logs.read"))],
)
async def get_pipeline_logs(
    limit: int = Query(default=100, ge=1, le=100),
    repository: PostgresJobRepository = Depends(_jobs),
):
    events = await repository.events(limit=limit)
    encoded = redact_text(json.dumps(events, sort_keys=True, default=str))
    redacted_events = json.loads(encoded)
    return {"logs": encoded, "events": redacted_events}


@router.get("/pipeline/sources")
async def get_pipeline_sources(
    repository: PipelineRepository = Depends(_pipelines),
):
    return {
        "sources": [
            redacted_configuration(source) for source in await repository.sources()
        ]
    }


@router.put(
    "/pipeline/sources/{source_name}/schedule",
    dependencies=[
        Depends(
            require_roles(
                Role.ADMINISTRATOR,
                action="pipeline.schedule.update",
                idempotent=True,
            )
        )
    ],
)
async def update_pipeline_source_schedule(
    source_name: str,
    payload: SourceScheduleUpdate,
    repository: PipelineRepository = Depends(_pipelines),
):
    try:
        return await repository.set_source_enabled(source_name, enabled=payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
