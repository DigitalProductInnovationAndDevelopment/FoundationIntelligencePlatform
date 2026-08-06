"""Structured security audit events with a replaceable durable sink."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import List, Optional, Protocol

from bff.utils.logging import logger


@dataclass(frozen=True)
class AuditEvent:
    """One security audit event with actor, action, target and outcome."""
    actor_id: str
    actor_role: str
    action: str
    target: str
    reason: str
    timestamp: str
    request_id: str
    result: str
    http_status: int
    error_class: Optional[str]
    dataset_version: Optional[str]


class AuditSink(Protocol):
    """Contract for durable audit event sinks."""

    def record(self, event: AuditEvent) -> object:
        """Append one audit event."""
        ...


class StructuredLogAuditSink:
    """Audit sink writing events to the structured log."""
    def record(self, event: AuditEvent) -> None:
        """Append one audit event to the structured log."""
        logger.info("security_audit %s", json.dumps(asdict(event), sort_keys=True, separators=(",", ":")))


class MemoryAuditSink:
    """Deterministic test sink; never selected by the application runtime."""

    def __init__(self) -> None:
        """Create an in-memory sink for deterministic tests."""
        self.events: List[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        """Append one audit event to memory."""
        self.events.append(event)

    def clear(self) -> None:
        """Discard all recorded events."""
        self.events.clear()


def event_from_request(request, status_code: int, error_class: Optional[str] = None) -> AuditEvent:
    """Build an audit event from the request state set by the route dependency."""
    principal = getattr(request.state, "principal", None)
    result = "success" if status_code < 400 else "denied" if status_code in {401, 403, 429} else "failed"
    reason = request.headers.get("x-action-reason", "not_provided").strip()[:500] or "not_provided"
    return AuditEvent(
        actor_id=principal.actor_id if principal else "anonymous",
        actor_role=principal.primary_role if principal else "none",
        action=getattr(request.state, "audit_action", "unclassified"),
        target=getattr(request.state, "audit_target", request.url.path),
        reason=reason,
        timestamp=datetime.now(timezone.utc).isoformat(),
        request_id=getattr(request.state, "request_id", "unavailable"),
        result=result,
        http_status=status_code,
        error_class=error_class,
        dataset_version=getattr(request.state, "dataset_version", None),
    )
