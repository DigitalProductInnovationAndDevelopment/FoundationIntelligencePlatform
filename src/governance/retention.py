"""Configurable, hold-aware and non-destructive retention planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


GOVERNANCE_CONFIGURATION_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "data-governance.json"
)
REQUIRED_CLASSIFICATIONS = frozenset(
    {
        "public_organisation_data",
        "contact_data",
        "personal_email_addresses",
        "postal_addresses",
        "raw_source_evidence",
        "article_metadata",
        "article_content",
        "pipeline_logs",
        "exports",
        "audit_events",
        "derived_classifications",
        "enriched_profiles",
        "credentials",
        "user_identities",
    }
)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClassificationPolicy:
    classification: str
    retention_class: str
    exposure: str
    archive_after_days: int | None
    delete_after_days: int | None
    export_expire_after_days: int | None

    def validate(self) -> None:
        if not self.classification.strip() or not self.retention_class.strip():
            raise ValueError("Classification and retention class are required")
        if self.exposure not in {"authenticated", "restricted", "internal", "never"}:
            raise ValueError(f"Invalid exposure for {self.classification}")
        for value in (
            self.archive_after_days,
            self.delete_after_days,
            self.export_expire_after_days,
        ):
            if value is not None and value < 1:
                raise ValueError(f"Retention windows must be positive for {self.classification}")

    @property
    def configuration_checksum(self) -> str:
        return checksum(asdict(self))

    def database_record(self, *, policy_status: str) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "policy_status": policy_status,
            "destructive_deletion_enabled": False,
            "production_approved": False,
            "configuration_checksum": self.configuration_checksum,
        }


@dataclass(frozen=True)
class GovernanceConfiguration:
    policy_status: str
    destructive_deletion_enabled: bool
    production_activation_approved: bool
    restore_before_delete_required: bool
    policies: tuple[ClassificationPolicy, ...]
    data_owners: Mapping[str, str]
    field_exposure_policies: Mapping[str, tuple[str, ...]]
    log_redaction: Mapping[str, Any]
    privacy_checklist: Mapping[str, Any]
    backup_policy: Mapping[str, Any]
    pitr_policy: Mapping[str, Any]
    service_recovery: Mapping[str, Any]
    data_subject_workflow: tuple[str, ...]

    def validate(self) -> None:
        if self.policy_status not in {"proposed", "approved", "retired"}:
            raise ValueError("Invalid governance policy status")
        if self.destructive_deletion_enabled:
            raise ValueError("Initial governance configuration must disable destructive deletion")
        if self.production_activation_approved:
            raise ValueError("Production retention activation has not been approved")
        classifications = {policy.classification for policy in self.policies}
        missing = sorted(REQUIRED_CLASSIFICATIONS - classifications)
        if missing:
            raise ValueError(f"Missing classifications: {', '.join(missing)}")
        retention_classes = [policy.retention_class for policy in self.policies]
        if len(retention_classes) != len(set(retention_classes)):
            raise ValueError("Retention classes must be unique")
        for policy in self.policies:
            policy.validate()
            if policy.delete_after_days is not None:
                raise ValueError("Initial destructive deletion windows must remain unset")
        required_owners = {
            "business_owner",
            "data_owner",
            "privacy_owner",
            "legal_owner",
            "security_owner",
            "technical_owner",
        }
        if required_owners - set(self.data_owners):
            raise ValueError("Governance owner register is incomplete")
        if not self.restore_before_delete_required:
            raise ValueError("Restore verification must precede any future deletion")
        if not self.field_exposure_policies:
            raise ValueError("Explicit field exposure policies are required")
        if not self.data_subject_workflow:
            raise ValueError("Data-subject workflow is required")

    @property
    def by_retention_class(self) -> dict[str, ClassificationPolicy]:
        return {policy.retention_class: policy for policy in self.policies}


def load_governance_configuration(
    path: Path = GOVERNANCE_CONFIGURATION_PATH,
) -> GovernanceConfiguration:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("configuration_version") != "1":
        raise ValueError("Unsupported governance configuration version")
    configuration = GovernanceConfiguration(
        policy_status=str(payload["policy_status"]),
        destructive_deletion_enabled=bool(payload["destructive_deletion_enabled"]),
        production_activation_approved=bool(payload["production_activation_approved"]),
        restore_before_delete_required=bool(payload["restore_before_delete_required"]),
        policies=tuple(
            ClassificationPolicy(**entry) for entry in payload.get("classifications", [])
        ),
        data_owners=dict(payload.get("data_owners", {})),
        field_exposure_policies={
            name: tuple(fields)
            for name, fields in payload.get("field_exposure_policies", {}).items()
        },
        log_redaction=dict(payload.get("log_redaction", {})),
        privacy_checklist=dict(payload.get("privacy_checklist", {})),
        backup_policy=dict(payload.get("backup_policy", {})),
        pitr_policy=dict(payload.get("pitr_policy", {})),
        service_recovery=dict(payload.get("service_recovery", {})),
        data_subject_workflow=tuple(payload.get("data_subject_workflow", [])),
    )
    configuration.validate()
    return configuration


@dataclass(frozen=True)
class RetentionCandidate:
    target_type: str
    target_id: str
    retention_class: str
    last_modified_at: datetime
    object_count: int = 0
    record_count: int = 0
    total_bytes: int = 0
    target_checksums: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.target_type.strip() or not self.target_id.strip():
            raise ValueError("Retention target identity is required")
        if self.last_modified_at.tzinfo is None:
            raise ValueError("Retention timestamps must be timezone-aware")
        if min(self.object_count, self.record_count, self.total_bytes) < 0:
            raise ValueError("Retention counts cannot be negative")
        for value in self.target_checksums:
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("Target checksums must be lowercase SHA-256")


@dataclass(frozen=True)
class DataHold:
    hold_id: str
    hold_type: str
    scope_type: str
    scope_id: str
    status: str
    expires_at: datetime | None = None

    def applies(self, candidate: RetentionCandidate, now: datetime) -> bool:
        active = self.status == "active" and (
            self.expires_at is None or self.expires_at > now
        )
        exact = self.scope_type == candidate.target_type and self.scope_id == candidate.target_id
        global_scope = self.scope_type == "all" and self.scope_id == "*"
        class_scope = (
            self.scope_type == "retention_class"
            and self.scope_id == candidate.retention_class
        )
        return active and (exact or global_scope or class_scope)


@dataclass(frozen=True)
class RetentionPlanEntry:
    target_type: str
    target_id: str
    retention_class: str
    action_type: str
    status: str
    dry_run: bool
    reason: str
    hold_ids: tuple[str, ...]
    object_count: int
    record_count: int
    total_bytes: int
    target_checksums: tuple[str, ...]
    generated_at: str

    @property
    def manifest(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def manifest_checksum(self) -> str:
        return checksum(self.manifest)


class RetentionPlanner:
    def __init__(self, configuration: GovernanceConfiguration):
        configuration.validate()
        self.configuration = configuration

    def plan(
        self,
        candidates: Iterable[RetentionCandidate],
        holds: Iterable[DataHold],
        *,
        now: datetime | None = None,
    ) -> tuple[RetentionPlanEntry, ...]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Planner timestamp must be timezone-aware")
        available_holds = tuple(holds)
        entries: list[RetentionPlanEntry] = []
        policies = self.configuration.by_retention_class
        for candidate in candidates:
            candidate.validate()
            policy = policies.get(candidate.retention_class)
            if policy is None:
                raise ValueError(f"Unknown retention class: {candidate.retention_class}")
            active_holds = tuple(
                hold.hold_id
                for hold in available_holds
                if hold.applies(candidate, current)
            )
            archive_due = (
                policy.archive_after_days is not None
                and candidate.last_modified_at
                <= current - timedelta(days=policy.archive_after_days)
            )
            if active_holds:
                action_type = "report"
                status = "held"
                reason = "active_legal_or_incident_hold"
            elif archive_due:
                action_type = "archive"
                status = "reported"
                reason = "archive_dry_run_only"
            else:
                action_type = "report"
                status = "reported"
                reason = "retention_window_not_reached"
            entries.append(
                RetentionPlanEntry(
                    target_type=candidate.target_type,
                    target_id=candidate.target_id,
                    retention_class=candidate.retention_class,
                    action_type=action_type,
                    status=status,
                    dry_run=True,
                    reason=reason,
                    hold_ids=active_holds,
                    object_count=candidate.object_count,
                    record_count=candidate.record_count,
                    total_bytes=candidate.total_bytes,
                    target_checksums=candidate.target_checksums,
                    generated_at=current.isoformat(),
                )
            )
        return tuple(entries)

    def assert_deletion_authorized(
        self,
        *,
        retention_class: str,
        actor_role: str,
        restore_verified: bool,
        active_hold_ids: Iterable[str],
    ) -> None:
        policy = self.configuration.by_retention_class.get(retention_class)
        if policy is None:
            raise PermissionError("Unknown retention class")
        if not self.configuration.destructive_deletion_enabled:
            raise PermissionError("Destructive deletion is globally disabled")
        if not self.configuration.production_activation_approved:
            raise PermissionError("Production retention activation is not approved")
        if actor_role != "administrator":
            raise PermissionError("Administrator role is required")
        if self.configuration.restore_before_delete_required and not restore_verified:
            raise PermissionError("Successful restore verification is required")
        if tuple(active_hold_ids):
            raise PermissionError("Active holds override deletion")
        if policy.delete_after_days is None:
            raise PermissionError("No destructive retention window is configured")


def export_lifecycle_status(
    *, expires_at: datetime | None, hold_until: datetime | None, now: datetime | None = None
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Lifecycle timestamp must be timezone-aware")
    if hold_until is not None and hold_until > current:
        return "held"
    if expires_at is not None and expires_at <= current:
        return "expiration_report_due"
    return "active"
