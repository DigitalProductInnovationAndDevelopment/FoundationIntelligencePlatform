"""Administrator-only local telemetry evidence and definitions."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from bff.security import Role, require_roles


router = APIRouter(
    prefix="/api/admin/observability",
    tags=["Observability"],
    dependencies=[
        Depends(require_roles(Role.OPERATOR, action="observability.read"))
    ],
)


@router.get("/metrics")
async def metrics(request: Request):
    configuration = request.app.state.observability_configuration
    registry = request.app.state.metrics_registry
    return {
        "service": configuration.service,
        "metrics": [asdict(definition) for definition in configuration.metrics],
        "alarms": [asdict(definition) for definition in configuration.alarms],
        "local_snapshot": registry.snapshot(),
        "cloudwatch_execution": "not_tested",
    }
