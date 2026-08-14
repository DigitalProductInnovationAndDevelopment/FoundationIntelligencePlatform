"""Add durable pipeline coordination and immutable object manifests.

Revision ID: 0005_durable_pipeline
Revises: 0004_versioned_analytics
"""

from alembic import op


revision = "0005_durable_pipeline"
down_revision = "0004_versioned_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = (
        """
        CREATE TABLE source_configurations (
            source_name TEXT PRIMARY KEY,
            source_owner TEXT NOT NULL,
            technical_owner TEXT NOT NULL,
            legal_status TEXT NOT NULL,
            licence_status TEXT NOT NULL,
            terms_url TEXT,
            rate_limit_per_minute INTEGER NOT NULL,
            user_agent TEXT NOT NULL,
            freshness_sla_hours INTEGER NOT NULL,
            schedule_expression TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            governance_blocked BOOLEAN NOT NULL DEFAULT TRUE,
            last_success_at TIMESTAMPTZ,
            watermark TEXT,
            classification TEXT NOT NULL,
            retention_class TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            credentials_reference TEXT,
            retry_limit INTEGER NOT NULL,
            timeout_seconds INTEGER NOT NULL,
            maximum_pages INTEGER NOT NULL,
            maximum_records BIGINT NOT NULL,
            configuration_checksum CHAR(64) NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_source_configuration_name CHECK (btrim(source_name) <> ''),
            CONSTRAINT ck_source_configuration_owners CHECK (
                btrim(source_owner) <> '' AND btrim(technical_owner) <> ''
            ),
            CONSTRAINT ck_source_configuration_legal CHECK (
                legal_status IN ('approved', 'unresolved', 'restricted', 'prohibited')
                AND licence_status IN ('approved', 'unresolved', 'restricted', 'prohibited')
            ),
            CONSTRAINT ck_source_configuration_limits CHECK (
                rate_limit_per_minute >= 1
                AND freshness_sla_hours >= 1
                AND retry_limit BETWEEN 0 AND 20
                AND timeout_seconds BETWEEN 1 AND 86400
                AND maximum_pages >= 1
                AND maximum_records >= 1
            ),
            CONSTRAINT ck_source_configuration_schedule CHECK (
                btrim(schedule_expression) <> ''
                AND btrim(user_agent) <> ''
                AND btrim(classification) <> ''
                AND btrim(retention_class) <> ''
                AND btrim(schema_version) <> ''
            ),
            CONSTRAINT ck_source_configuration_governance CHECK (
                NOT enabled OR (
                    NOT governance_blocked
                    AND legal_status = 'approved'
                    AND licence_status = 'approved'
                )
            ),
            CONSTRAINT ck_source_configuration_checksum CHECK (
                configuration_checksum ~ '^[a-f0-9]{64}$'
            )
        )
        """,
        """
        CREATE TABLE storage_objects (
            storage_object_id UUID PRIMARY KEY,
            zone TEXT NOT NULL,
            bucket_alias TEXT NOT NULL,
            object_key TEXT NOT NULL,
            object_version TEXT NOT NULL,
            checksum CHAR(64) NOT NULL,
            content_length BIGINT NOT NULL,
            content_type TEXT NOT NULL,
            source_name TEXT,
            source_ingestion_run_id UUID,
            immutable BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT uq_storage_object_version UNIQUE (
                zone, bucket_alias, object_key, object_version
            ),
            CONSTRAINT fk_storage_object_ingestion FOREIGN KEY (source_ingestion_run_id)
                REFERENCES source_ingestion_runs(source_ingestion_run_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT ck_storage_object_zone CHECK (
                zone IN ('raw', 'validated', 'curated', 'export')
            ),
            CONSTRAINT ck_storage_object_location CHECK (
                btrim(bucket_alias) <> ''
                AND btrim(object_key) <> ''
                AND btrim(object_version) <> ''
                AND btrim(content_type) <> ''
            ),
            CONSTRAINT ck_storage_object_checksum CHECK (
                checksum ~ '^[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_storage_object_length CHECK (content_length >= 0),
            CONSTRAINT ck_storage_object_raw_immutable CHECK (
                zone <> 'raw' OR immutable
            )
        )
        """,
        """
        CREATE INDEX ix_storage_objects_source_created
        ON storage_objects (source_name, created_at DESC, storage_object_id)
        """,
        """
        CREATE TABLE ingestion_run_manifests (
            ingestion_run_manifest_id UUID PRIMARY KEY,
            source_ingestion_run_id UUID NOT NULL UNIQUE,
            schema_version TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_version TEXT,
            dataset_version TEXT NOT NULL,
            raw_object_id UUID,
            validated_object_id UUID,
            curated_object_id UUID,
            watermark_before TEXT,
            watermark_after TEXT,
            record_count BIGINT NOT NULL,
            accepted_count BIGINT NOT NULL,
            rejected_count BIGINT NOT NULL,
            quarantined_count BIGINT NOT NULL,
            retry_count INTEGER NOT NULL,
            manifest_checksum CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            manifest JSONB NOT NULL,
            CONSTRAINT fk_ingestion_manifest_run FOREIGN KEY (source_ingestion_run_id)
                REFERENCES source_ingestion_runs(source_ingestion_run_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT fk_ingestion_manifest_dataset FOREIGN KEY (dataset_version)
                REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT fk_ingestion_manifest_raw FOREIGN KEY (raw_object_id)
                REFERENCES storage_objects(storage_object_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT fk_ingestion_manifest_validated FOREIGN KEY (validated_object_id)
                REFERENCES storage_objects(storage_object_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT fk_ingestion_manifest_curated FOREIGN KEY (curated_object_id)
                REFERENCES storage_objects(storage_object_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT ck_ingestion_manifest_identity CHECK (
                btrim(schema_version) <> '' AND btrim(source_name) <> ''
            ),
            CONSTRAINT ck_ingestion_manifest_counts CHECK (
                record_count >= 0
                AND accepted_count >= 0
                AND rejected_count >= 0
                AND quarantined_count >= 0
                AND retry_count >= 0
                AND accepted_count + rejected_count + quarantined_count <= record_count
            ),
            CONSTRAINT ck_ingestion_manifest_checksum CHECK (
                manifest_checksum ~ '^[a-f0-9]{64}$'
            )
        )
        """,
        """
        CREATE FUNCTION forbid_immutable_pipeline_record_mutation() RETURNS TRIGGER
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'immutable pipeline records cannot be changed or deleted';
        END;
        $$
        """,
        """
        CREATE TRIGGER trg_ingestion_manifests_immutable
        BEFORE UPDATE OR DELETE ON ingestion_run_manifests
        FOR EACH ROW EXECUTE FUNCTION forbid_immutable_pipeline_record_mutation()
        """,
        """
        CREATE FUNCTION protect_immutable_storage_object() RETURNS TRIGGER
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.immutable OR OLD.zone = 'raw' THEN
                RAISE EXCEPTION 'immutable storage objects cannot be changed or deleted';
            END IF;
            RETURN OLD;
        END;
        $$
        """,
        """
        CREATE TRIGGER trg_storage_objects_immutable
        BEFORE UPDATE OR DELETE ON storage_objects
        FOR EACH ROW EXECUTE FUNCTION protect_immutable_storage_object()
        """,
        """
        CREATE TABLE job_dispatch_outbox (
            job_dispatch_outbox_id UUID PRIMARY KEY,
            job_run_id UUID NOT NULL UNIQUE,
            queue_name TEXT NOT NULL,
            message_body JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            publish_attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            queue_message_id TEXT,
            published_at TIMESTAMPTZ,
            last_error_class TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_job_dispatch_run FOREIGN KEY (job_run_id)
                REFERENCES job_runs(job_run_id)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CONSTRAINT ck_job_dispatch_queue CHECK (btrim(queue_name) <> ''),
            CONSTRAINT ck_job_dispatch_status CHECK (
                status IN ('pending', 'publishing', 'published', 'failed', 'dead_lettered')
            ),
            CONSTRAINT ck_job_dispatch_attempts CHECK (publish_attempts >= 0),
            CONSTRAINT ck_job_dispatch_published CHECK (
                (status = 'published' AND published_at IS NOT NULL AND queue_message_id IS NOT NULL)
                OR status <> 'published'
            )
        )
        """,
        """
        CREATE INDEX ix_job_dispatch_due
        ON job_dispatch_outbox (status, next_attempt_at, created_at)
        """,
        """
        CREATE TABLE worker_heartbeats (
            worker_id TEXT PRIMARY KEY,
            queue_name TEXT NOT NULL,
            job_run_id UUID,
            status TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT fk_worker_heartbeat_job FOREIGN KEY (job_run_id)
                REFERENCES job_runs(job_run_id)
                ON UPDATE CASCADE ON DELETE SET NULL,
            CONSTRAINT ck_worker_heartbeat_identity CHECK (
                btrim(worker_id) <> '' AND btrim(queue_name) <> ''
            ),
            CONSTRAINT ck_worker_heartbeat_status CHECK (
                status IN ('idle', 'running', 'draining', 'stopped')
            )
        )
        """,
        """
        CREATE INDEX ix_worker_heartbeats_queue_time
        ON worker_heartbeats (queue_name, heartbeat_at DESC, worker_id)
        """,
        """
        ALTER TABLE job_runs
            ADD COLUMN queue_name TEXT NOT NULL DEFAULT 'pipeline',
            ADD COLUMN heartbeat_at TIMESTAMPTZ,
            ADD COLUMN lease_expires_at TIMESTAMPTZ,
            ADD COLUMN timeout_seconds INTEGER NOT NULL DEFAULT 3600,
            ADD COLUMN failure_reason TEXT,
            ADD COLUMN last_good_dataset_version TEXT,
            ADD CONSTRAINT fk_job_runs_last_good_dataset FOREIGN KEY (
                last_good_dataset_version
            ) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE SET NULL,
            ADD CONSTRAINT ck_job_runs_queue CHECK (btrim(queue_name) <> ''),
            ADD CONSTRAINT ck_job_runs_timeout CHECK (
                timeout_seconds BETWEEN 1 AND 86400
            ),
            ADD CONSTRAINT ck_job_runs_lease CHECK (
                lease_expires_at IS NULL OR heartbeat_at IS NOT NULL
            )
        """,
        """
        ALTER TABLE source_ingestion_runs
            ADD COLUMN accepted_count BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN rejected_count BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN quarantined_count BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN failure_reason TEXT,
            ADD COLUMN watermark_before TEXT,
            ADD COLUMN watermark_after TEXT,
            ADD COLUMN last_good_dataset_version TEXT,
            ADD CONSTRAINT fk_ingestion_last_good_dataset FOREIGN KEY (
                last_good_dataset_version
            ) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE SET NULL,
            ADD CONSTRAINT ck_ingestion_result_counts CHECK (
                accepted_count >= 0
                AND rejected_count >= 0
                AND quarantined_count >= 0
                AND retry_count >= 0
                AND (
                    record_count IS NULL
                    OR accepted_count + rejected_count + quarantined_count <= record_count
                )
            )
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE source_ingestion_runs DROP CONSTRAINT ck_ingestion_result_counts")
    op.execute("ALTER TABLE source_ingestion_runs DROP CONSTRAINT fk_ingestion_last_good_dataset")
    for column in (
        "last_good_dataset_version",
        "watermark_after",
        "watermark_before",
        "failure_reason",
        "retry_count",
        "quarantined_count",
        "rejected_count",
        "accepted_count",
    ):
        op.execute(f"ALTER TABLE source_ingestion_runs DROP COLUMN {column}")
    op.execute("ALTER TABLE job_runs DROP CONSTRAINT ck_job_runs_lease")
    op.execute("ALTER TABLE job_runs DROP CONSTRAINT ck_job_runs_timeout")
    op.execute("ALTER TABLE job_runs DROP CONSTRAINT ck_job_runs_queue")
    op.execute("ALTER TABLE job_runs DROP CONSTRAINT fk_job_runs_last_good_dataset")
    for column in (
        "last_good_dataset_version",
        "failure_reason",
        "timeout_seconds",
        "lease_expires_at",
        "heartbeat_at",
        "queue_name",
    ):
        op.execute(f"ALTER TABLE job_runs DROP COLUMN {column}")
    op.execute("DROP TABLE worker_heartbeats")
    op.execute("DROP TABLE job_dispatch_outbox")
    op.execute("DROP TRIGGER trg_ingestion_manifests_immutable ON ingestion_run_manifests")
    op.execute("DROP TRIGGER trg_storage_objects_immutable ON storage_objects")
    op.execute("DROP FUNCTION protect_immutable_storage_object()")
    op.execute("DROP FUNCTION forbid_immutable_pipeline_record_mutation()")
    op.execute("DROP TABLE ingestion_run_manifests")
    op.execute("DROP TABLE storage_objects")
    op.execute("DROP TABLE source_configurations")
