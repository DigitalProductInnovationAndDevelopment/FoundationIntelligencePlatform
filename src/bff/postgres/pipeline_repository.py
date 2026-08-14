"""PostgreSQL persistence for source controls and immutable ingestion evidence."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping
import uuid

from sqlalchemy import text

from bff.postgres.base import PostgresRepository, iso_value
from pipelines.durable import IngestionManifest, SourceConfiguration, StorageObject


class PipelineRepository(PostgresRepository):
    async def public_source_statuses(self) -> list[dict[str, Any]]:
        """Return the intentionally small, credential-free customer status view."""
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT configuration.source_name,
                               configuration.enabled,
                               configuration.last_success_at,
                               configuration.freshness_sla_hours,
                               latest.completed_at AS latest_completed_at,
                               latest.record_count
                        FROM source_configurations AS configuration
                        LEFT JOIN LATERAL (
                            SELECT completed_at, record_count
                            FROM source_ingestion_runs
                            WHERE source_namespace=configuration.source_name
                              AND status='loaded'
                            ORDER BY completed_at DESC NULLS LAST, started_at DESC
                            LIMIT 1
                        ) AS latest ON TRUE
                        ORDER BY configuration.source_name
                        """
                    )
                )
            ).mappings()
        return [
            {
                "name": str(row["source_name"]),
                "enabled": bool(row["enabled"]),
                "last_success_at": iso_value(
                    row["last_success_at"] or row["latest_completed_at"]
                ),
                "freshness_sla_hours": int(row["freshness_sla_hours"]),
                "record_count": (
                    int(row["record_count"])
                    if row["record_count"] is not None
                    else None
                ),
            }
            for row in rows
        ]

    async def synchronize_sources(
        self, configurations: Iterable[SourceConfiguration]
    ) -> int:
        records = [configuration.database_record() for configuration in configurations]
        async with self.sessions() as session, session.begin():
            for record in records:
                await session.execute(
                    text(
                        """
                        INSERT INTO source_configurations (
                            source_name, source_owner, technical_owner, legal_status,
                            licence_status, terms_url, rate_limit_per_minute,
                            user_agent, freshness_sla_hours, schedule_expression,
                            enabled, governance_blocked, last_success_at, watermark,
                            classification, retention_class, schema_version,
                            credentials_reference, retry_limit, timeout_seconds,
                            maximum_pages, maximum_records, configuration_checksum
                        ) VALUES (
                            :source_name, :source_owner, :technical_owner, :legal_status,
                            :licence_status, :terms_url, :rate_limit_per_minute,
                            :user_agent, :freshness_sla_hours, :schedule_expression,
                            :enabled, :governance_blocked, CAST(:last_success AS timestamptz),
                            :watermark, :classification, :retention_class, :schema_version,
                            :credentials_reference, :retry_limit, :timeout_seconds,
                            :maximum_pages, :maximum_records, :configuration_checksum
                        )
                        ON CONFLICT (source_name) DO UPDATE SET
                            source_owner=EXCLUDED.source_owner,
                            technical_owner=EXCLUDED.technical_owner,
                            legal_status=EXCLUDED.legal_status,
                            licence_status=EXCLUDED.licence_status,
                            terms_url=EXCLUDED.terms_url,
                            rate_limit_per_minute=EXCLUDED.rate_limit_per_minute,
                            user_agent=EXCLUDED.user_agent,
                            freshness_sla_hours=EXCLUDED.freshness_sla_hours,
                            schedule_expression=EXCLUDED.schedule_expression,
                            enabled=EXCLUDED.enabled,
                            governance_blocked=EXCLUDED.governance_blocked,
                            classification=EXCLUDED.classification,
                            retention_class=EXCLUDED.retention_class,
                            schema_version=EXCLUDED.schema_version,
                            credentials_reference=EXCLUDED.credentials_reference,
                            retry_limit=EXCLUDED.retry_limit,
                            timeout_seconds=EXCLUDED.timeout_seconds,
                            maximum_pages=EXCLUDED.maximum_pages,
                            maximum_records=EXCLUDED.maximum_records,
                            configuration_checksum=EXCLUDED.configuration_checksum,
                            updated_at=CURRENT_TIMESTAMP
                        """
                    ),
                    record,
                )
        return len(records)

    async def sources(self) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT source_name, source_owner, technical_owner,
                               legal_status, licence_status, terms_url,
                               rate_limit_per_minute, user_agent,
                               freshness_sla_hours, schedule_expression,
                               enabled, governance_blocked, last_success_at,
                               watermark, classification, retention_class,
                               schema_version, credentials_reference, retry_limit,
                               timeout_seconds, maximum_pages, maximum_records,
                               configuration_checksum, updated_at
                        FROM source_configurations ORDER BY source_name
                        """
                    )
                )
            ).mappings()
        return [
            {
                **dict(row),
                "last_success_at": iso_value(row["last_success_at"]),
                "updated_at": iso_value(row["updated_at"]),
            }
            for row in rows
        ]

    async def set_source_enabled(self, source_name: str, *, enabled: bool) -> dict[str, Any]:
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT legal_status, licence_status, governance_blocked
                        FROM source_configurations
                        WHERE source_name=:source_name
                        FOR UPDATE
                        """
                    ),
                    {"source_name": source_name},
                )
            ).mappings().first()
            if not row:
                raise ValueError("Unknown source configuration")
            if enabled and (
                row["legal_status"] != "approved"
                or row["licence_status"] != "approved"
                or row["governance_blocked"]
            ):
                raise ValueError("Source schedule is blocked by unresolved governance")
            updated = (
                await session.execute(
                    text(
                        """
                        UPDATE source_configurations
                        SET enabled=:enabled, updated_at=CURRENT_TIMESTAMP
                        WHERE source_name=:source_name
                        RETURNING source_name, enabled, governance_blocked,
                                  legal_status, licence_status, schedule_expression,
                                  updated_at
                        """
                    ),
                    {"source_name": source_name, "enabled": enabled},
                )
            ).mappings().one()
        return {**dict(updated), "updated_at": iso_value(updated["updated_at"])}

    async def start_ingestion(
        self,
        *,
        source_name: str,
        dataset_version: str,
        job_id: str | None,
        source_version: str | None,
        source_uri: str | None,
        watermark_before: str | None,
    ) -> str:
        run_id = uuid.uuid4()
        async with self.sessions() as session, session.begin():
            active = await self.active_dataset(session)
            await session.execute(
                text(
                    """
                    INSERT INTO source_ingestion_runs (
                        source_ingestion_run_id, source_namespace, dataset_version,
                        job_run_id, status, source_version, source_uri,
                        watermark_before, last_good_dataset_version
                    ) VALUES (
                        :run_id, :source_name, :dataset_version, :job_id,
                        'created', :source_version, :source_uri,
                        :watermark_before, :last_good_dataset_version
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "source_name": source_name,
                    "dataset_version": dataset_version,
                    "job_id": uuid.UUID(job_id) if job_id else None,
                    "source_version": source_version,
                    "source_uri": source_uri,
                    "watermark_before": watermark_before,
                    "last_good_dataset_version": active,
                },
            )
        return str(run_id)

    async def record_object(
        self, descriptor: StorageObject, *, metadata: Mapping[str, Any] | None = None
    ) -> None:
        descriptor.validate()
        async with self.sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO storage_objects (
                        storage_object_id, zone, bucket_alias, object_key,
                        object_version, checksum, content_length, content_type,
                        source_name, source_ingestion_run_id, immutable, metadata
                    ) VALUES (
                        :storage_object_id, :zone, :bucket_alias, :object_key,
                        :object_version, :checksum, :content_length, :content_type,
                        :source_name, :source_ingestion_run_id, :immutable,
                        CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (zone, bucket_alias, object_key, object_version)
                    DO NOTHING
                    """
                ),
                {
                    **descriptor.__dict__,
                    "metadata": json.dumps(metadata or {}, sort_keys=True, default=str),
                },
            )

    async def complete_ingestion(
        self,
        manifest: IngestionManifest,
        *,
        status: str = "loaded",
    ) -> str:
        manifest.validate()
        manifest_id = uuid.uuid4()
        payload = manifest.payload
        async with self.sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO ingestion_run_manifests (
                        ingestion_run_manifest_id, source_ingestion_run_id,
                        schema_version, source_name, source_version, dataset_version,
                        raw_object_id, validated_object_id, curated_object_id,
                        watermark_before, watermark_after, record_count,
                        accepted_count, rejected_count, quarantined_count,
                        retry_count, manifest_checksum, manifest
                    ) VALUES (
                        :manifest_id, :run_id, :schema_version, :source_name,
                        :source_version, :dataset_version, :raw_object_id,
                        :validated_object_id, :curated_object_id, :watermark_before,
                        :watermark_after, :record_count, :accepted_count,
                        :rejected_count, :quarantined_count, :retry_count,
                        :manifest_checksum, CAST(:manifest AS jsonb)
                    )
                    """
                ),
                {
                    "manifest_id": manifest_id,
                    "run_id": manifest.source_ingestion_run_id,
                    **payload,
                    "raw_object_id": manifest.raw_object_id,
                    "validated_object_id": manifest.validated_object_id,
                    "curated_object_id": manifest.curated_object_id,
                    "manifest_checksum": manifest.checksum,
                    "manifest": json.dumps(payload, sort_keys=True),
                },
            )
            updated = await session.scalar(
                text(
                    """
                    UPDATE source_ingestion_runs
                    SET status=:status, completed_at=CURRENT_TIMESTAMP,
                        object_checksum=:manifest_checksum,
                        record_count=:record_count,
                        accepted_count=:accepted_count,
                        rejected_count=:rejected_count,
                        quarantined_count=:quarantined_count,
                        retry_count=:retry_count,
                        watermark_after=:watermark_after,
                        metrics=CAST(:metrics AS jsonb)
                    WHERE source_ingestion_run_id=:run_id
                      AND status IN ('created', 'fetching', 'validating')
                    RETURNING source_ingestion_run_id
                    """
                ),
                {
                    "status": status,
                    "manifest_checksum": manifest.checksum,
                    "record_count": manifest.record_count,
                    "accepted_count": manifest.accepted_count,
                    "rejected_count": manifest.rejected_count,
                    "quarantined_count": manifest.quarantined_count,
                    "retry_count": manifest.retry_count,
                    "watermark_after": manifest.watermark_after,
                    "metrics": json.dumps(
                        {
                            "accepted": manifest.accepted_count,
                            "rejected": manifest.rejected_count,
                            "quarantined": manifest.quarantined_count,
                        },
                        sort_keys=True,
                    ),
                    "run_id": manifest.source_ingestion_run_id,
                },
            )
            if updated is None:
                raise ValueError("Ingestion run cannot transition to completed")
        return str(manifest_id)
