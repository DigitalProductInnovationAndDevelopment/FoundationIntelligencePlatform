"""Administrator-only governance and non-destructive retention routes."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from bff.postgres.governance_repository import GovernanceRepository
from bff.schemas import (
    DataHoldRelease,
    DataHoldRequest,
    DataSubjectRequestCreate,
    RetentionDryRunRequest,
)
from bff.security import Role, require_roles
from governance.retention import (
    RetentionCandidate,
    RetentionPlanner,
    load_governance_configuration,
)


router = APIRouter(
    prefix="/api/admin/governance",
    tags=["Governance & Retention"],
    dependencies=[
        Depends(require_roles(Role.ADMINISTRATOR, action="governance.access"))
    ],
)
GOVERNANCE_CONFIGURATION = load_governance_configuration()
RETENTION_PLANNER = RetentionPlanner(GOVERNANCE_CONFIGURATION)


def _repository(request: Request) -> GovernanceRepository:
    return GovernanceRepository(request.app.state.database.sessions())


def _actor(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.actor_id if principal else "unknown"


@router.get("/retention/policies")
async def retention_policies(
    repository: GovernanceRepository = Depends(_repository),
):
    return {
        "destructive_deletion_enabled": False,
        "production_activation_approved": False,
        "policies": await repository.policies(),
    }


@router.post(
    "/retention/dry-run",
    dependencies=[
        Depends(
            require_roles(
                Role.ADMINISTRATOR,
                action="retention.dry_run",
                idempotent=True,
            )
        )
    ],
)
async def retention_dry_run(
    payload: RetentionDryRunRequest,
    request: Request,
    repository: GovernanceRepository = Depends(_repository),
):
    candidate = RetentionCandidate(
        target_type=payload.target_type,
        target_id=payload.target_id,
        retention_class=payload.retention_class,
        last_modified_at=payload.last_modified_at,
        object_count=payload.object_count,
        record_count=payload.record_count,
        total_bytes=payload.total_bytes,
        target_checksums=tuple(payload.target_checksums),
    )
    try:
        entries = RETENTION_PLANNER.plan(
            [candidate], await repository.active_holds()
        )
        action_ids = await repository.record_retention_plan(
            entries, requested_by=_actor(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "dry_run": True,
        "destructive_deletion_enabled": False,
        "actions": [
            {
                **entry.manifest,
                "manifest_checksum": entry.manifest_checksum,
                "retention_action_id": action_id,
            }
            for entry, action_id in zip(entries, action_ids)
        ],
    }


@router.get("/holds")
async def active_holds(
    repository: GovernanceRepository = Depends(_repository),
):
    return {"holds": [asdict(hold) for hold in await repository.active_holds()]}


@router.post(
    "/holds",
    dependencies=[
        Depends(
            require_roles(
                Role.ADMINISTRATOR,
                action="governance.hold.create",
                idempotent=True,
            )
        )
    ],
)
async def create_hold(
    payload: DataHoldRequest,
    request: Request,
    repository: GovernanceRepository = Depends(_repository),
):
    try:
        return await repository.create_hold(
            hold_type=payload.hold_type,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            reason=payload.reason,
            created_by=_actor(request),
            expires_at=payload.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/holds/{hold_id}/release",
    dependencies=[
        Depends(
            require_roles(
                Role.ADMINISTRATOR,
                action="governance.hold.release",
                idempotent=True,
            )
        )
    ],
)
async def release_hold(
    hold_id: str,
    payload: DataHoldRelease,
    request: Request,
    repository: GovernanceRepository = Depends(_repository),
):
    try:
        return await repository.release_hold(
            hold_id,
            released_by=_actor(request),
            release_reason=payload.reason,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="Active hold does not exist") from exc


@router.get("/exports/expiration-report")
async def export_expiration_report(
    repository: GovernanceRepository = Depends(_repository),
):
    return {
        "dry_run": True,
        "destructive_deletion_enabled": False,
        "exports": await repository.expired_exports(),
    }


@router.post(
    "/data-subject-requests",
    dependencies=[
        Depends(
            require_roles(
                Role.ADMINISTRATOR,
                action="governance.data_subject_request.create",
                idempotent=True,
            )
        )
    ],
)
async def create_data_subject_request(
    payload: DataSubjectRequestCreate,
    repository: GovernanceRepository = Depends(_repository),
):
    request_id = await repository.create_data_subject_request(
        request_type=payload.request_type,
        subject_reference_hash=payload.subject_reference_hash,
        due_at=payload.due_at,
    )
    return {"data_subject_request_id": request_id, "status": "identity_pending"}
