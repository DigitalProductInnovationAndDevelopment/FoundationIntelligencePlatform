"""PostgreSQL governance, holds and non-destructive retention evidence."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Iterable, Mapping
import uuid

from sqlalchemy import text

from bff.postgres.base import PostgresRepository, iso_value
from governance.retention import (
    ClassificationPolicy,
    DataHold,
    GovernanceConfiguration,
    RetentionPlanEntry,
)


class GovernanceRepository(PostgresRepository):
    async def synchronize_policies(self, configuration: GovernanceConfiguration) -> int:
        configuration.validate()
        records = [
            policy.database_record(policy_status=configuration.policy_status)
            for policy in configuration.policies
        ]
        async with self.sessions() as session, session.begin():
            for record in records:
                await session.execute(
                    text(
                        """
                        INSERT INTO retention_policies (
                            retention_class, classification, policy_status,
                            archive_after_days, delete_after_days,
                            export_expire_after_days,
                            destructive_deletion_enabled, production_approved,
                            configuration_checksum
                        ) VALUES (
                            :retention_class, :classification, :policy_status,
                            :archive_after_days, :delete_after_days,
                            :export_expire_after_days,
                            :destructive_deletion_enabled, :production_approved,
                            :configuration_checksum
                        )
                        ON CONFLICT (retention_class) DO UPDATE SET
                            classification=EXCLUDED.classification,
                            policy_status=EXCLUDED.policy_status,
                            archive_after_days=EXCLUDED.archive_after_days,
                            delete_after_days=EXCLUDED.delete_after_days,
                            export_expire_after_days=EXCLUDED.export_expire_after_days,
                            destructive_deletion_enabled=EXCLUDED.destructive_deletion_enabled,
                            production_approved=EXCLUDED.production_approved,
                            configuration_checksum=EXCLUDED.configuration_checksum,
                            updated_at=CURRENT_TIMESTAMP
                        """
                    ),
                    record,
                )
        return len(records)

    async def policies(self) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT retention_class, classification, policy_status,
                               archive_after_days, delete_after_days,
                               export_expire_after_days,
                               destructive_deletion_enabled, production_approved,
                               approval_reference, configuration_checksum, updated_at
                        FROM retention_policies ORDER BY retention_class
                        """
                    )
                )
            ).mappings()
        return [
            {**dict(row), "updated_at": iso_value(row["updated_at"])}
            for row in rows
        ]

    async def create_hold(
        self,
        *,
        hold_type: str,
        scope_type: str,
        scope_id: str,
        reason: str,
        created_by: str,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        if hold_type not in {"legal", "incident"}:
            raise ValueError("Hold type must be legal or incident")
        if any(not str(value).strip() for value in (scope_type, scope_id, reason, created_by)):
            raise ValueError("Hold scope, reason and actor are required")
        hold_id = uuid.uuid4()
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO data_holds (
                            data_hold_id, hold_type, scope_type, scope_id,
                            reason, created_by, expires_at
                        ) VALUES (
                            :hold_id, :hold_type, :scope_type, :scope_id,
                            :reason, :created_by, :expires_at
                        )
                        RETURNING data_hold_id, hold_type, scope_type, scope_id,
                                  reason, status, created_by, created_at, expires_at
                        """
                    ),
                    {
                        "hold_id": hold_id,
                        "hold_type": hold_type,
                        "scope_type": scope_type,
                        "scope_id": scope_id,
                        "reason": reason,
                        "created_by": created_by,
                        "expires_at": expires_at,
                    },
                )
            ).mappings().one()
        return self._hold_row(row)

    async def release_hold(
        self,
        hold_id: str,
        *,
        released_by: str,
        release_reason: str,
    ) -> dict[str, Any]:
        if not released_by.strip() or not release_reason.strip():
            raise ValueError("Hold release actor and reason are required")
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        UPDATE data_holds
                        SET status='released', released_by=:released_by,
                            released_at=CURRENT_TIMESTAMP,
                            release_reason=:release_reason
                        WHERE data_hold_id=:hold_id AND status='active'
                        RETURNING data_hold_id, hold_type, scope_type, scope_id,
                                  reason, status, created_by, created_at, expires_at,
                                  released_by, released_at, release_reason
                        """
                    ),
                    {
                        "hold_id": uuid.UUID(str(hold_id)),
                        "released_by": released_by,
                        "release_reason": release_reason,
                    },
                )
            ).mappings().first()
            if not row:
                raise ValueError("Active hold does not exist")
        return self._hold_row(row)

    async def active_holds(self) -> tuple[DataHold, ...]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT data_hold_id, hold_type, scope_type, scope_id,
                               status, expires_at
                        FROM data_holds
                        WHERE status='active'
                          AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                        ORDER BY created_at, data_hold_id
                        """
                    )
                )
            ).mappings()
        return tuple(
            DataHold(
                hold_id=str(row["data_hold_id"]),
                hold_type=str(row["hold_type"]),
                scope_type=str(row["scope_type"]),
                scope_id=str(row["scope_id"]),
                status=str(row["status"]),
                expires_at=row["expires_at"],
            )
            for row in rows
        )

    async def record_retention_plan(
        self,
        entries: Iterable[RetentionPlanEntry],
        *,
        requested_by: str,
    ) -> list[str]:
        action_ids: list[str] = []
        async with self.sessions() as session, session.begin():
            for entry in entries:
                action_id = uuid.uuid4()
                manifest_id = uuid.uuid4()
                hold_ids = [uuid.UUID(value) for value in entry.hold_ids]
                manifest = entry.manifest
                await session.execute(
                    text(
                        """
                        INSERT INTO retention_actions (
                            retention_action_id, target_type, target_id, status,
                            dry_run, legal_hold, reason, requested_by, manifest,
                            retention_class, action_type, hold_ids,
                            manifest_checksum, production_approved
                        ) VALUES (
                            :action_id, :target_type, :target_id, :status,
                            TRUE, :has_hold, :reason, :requested_by,
                            CAST(:manifest AS jsonb), :retention_class,
                            :action_type, :hold_ids, :manifest_checksum, FALSE
                        )
                        """
                    ),
                    {
                        "action_id": action_id,
                        "target_type": entry.target_type,
                        "target_id": entry.target_id,
                        "status": entry.status,
                        "has_hold": bool(hold_ids),
                        "reason": entry.reason,
                        "requested_by": requested_by,
                        "manifest": json.dumps(manifest, sort_keys=True, default=str),
                        "retention_class": entry.retention_class,
                        "action_type": entry.action_type,
                        "hold_ids": hold_ids,
                        "manifest_checksum": entry.manifest_checksum,
                    },
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO deletion_manifests (
                            deletion_manifest_id, retention_action_id,
                            target_type, target_id, retention_class,
                            action_type, dry_run, object_count, record_count,
                            total_bytes, target_checksums, hold_ids,
                            manifest_checksum, manifest
                        ) VALUES (
                            :manifest_id, :action_id, :target_type, :target_id,
                            :retention_class, :action_type, TRUE, :object_count,
                            :record_count, :total_bytes,
                            CAST(:target_checksums AS jsonb), :hold_ids,
                            :manifest_checksum, CAST(:manifest AS jsonb)
                        )
                        """
                    ),
                    {
                        "manifest_id": manifest_id,
                        "action_id": action_id,
                        "target_type": entry.target_type,
                        "target_id": entry.target_id,
                        "retention_class": entry.retention_class,
                        "action_type": entry.action_type,
                        "object_count": entry.object_count,
                        "record_count": entry.record_count,
                        "total_bytes": entry.total_bytes,
                        "target_checksums": json.dumps(entry.target_checksums),
                        "hold_ids": hold_ids,
                        "manifest_checksum": entry.manifest_checksum,
                        "manifest": json.dumps(manifest, sort_keys=True, default=str),
                    },
                )
                action_ids.append(str(action_id))
        return action_ids

    async def record_restore_verification(
        self,
        *,
        target_type: str,
        target_id: str,
        backup_reference: str,
        backup_checksum: str,
        verification_status: str,
        verified_by: str,
        evidence: Mapping[str, Any],
    ) -> str:
        if verification_status not in {"passed", "failed"}:
            raise ValueError("Restore verification status must be passed or failed")
        verification_id = uuid.uuid4()
        async with self.sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO restore_verifications (
                        restore_verification_id, target_type, target_id,
                        backup_reference, backup_checksum, verification_status,
                        verified_by, evidence
                    ) VALUES (
                        :verification_id, :target_type, :target_id,
                        :backup_reference, :backup_checksum, :verification_status,
                        :verified_by, CAST(:evidence AS jsonb)
                    )
                    """
                ),
                {
                    "verification_id": verification_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "backup_reference": backup_reference,
                    "backup_checksum": backup_checksum,
                    "verification_status": verification_status,
                    "verified_by": verified_by,
                    "evidence": json.dumps(evidence, sort_keys=True, default=str),
                },
            )
        return str(verification_id)

    async def expired_exports(self) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT export_job_id, dataset_version, export_type, status,
                               requested_at, completed_at, object_key, checksum,
                               row_count, expires_at, hold_until, retention_class
                        FROM export_jobs
                        WHERE expires_at <= CURRENT_TIMESTAMP
                          AND (hold_until IS NULL OR hold_until <= CURRENT_TIMESTAMP)
                          AND status='succeeded'
                        ORDER BY expires_at, export_job_id
                        """
                    )
                )
            ).mappings()
        return [
            {
                **dict(row),
                "export_job_id": str(row["export_job_id"]),
                "requested_at": iso_value(row["requested_at"]),
                "completed_at": iso_value(row["completed_at"]),
                "expires_at": iso_value(row["expires_at"]),
                "hold_until": iso_value(row["hold_until"]),
            }
            for row in rows
        ]

    async def create_data_subject_request(
        self,
        *,
        request_type: str,
        subject_reference_hash: str,
        due_at: datetime | None,
    ) -> str:
        request_id = uuid.uuid4()
        async with self.sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO data_subject_requests (
                        data_subject_request_id, request_type,
                        subject_reference_hash, status, due_at
                    ) VALUES (
                        :request_id, :request_type, :subject_reference_hash,
                        'identity_pending', :due_at
                    )
                    """
                ),
                {
                    "request_id": request_id,
                    "request_type": request_type,
                    "subject_reference_hash": subject_reference_hash,
                    "due_at": due_at,
                },
            )
        return str(request_id)

    @staticmethod
    def _hold_row(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["data_hold_id"] = str(result["data_hold_id"])
        for field in ("created_at", "expires_at", "released_at"):
            if field in result:
                result[field] = iso_value(result[field])
        return result
