"""Add governance, holds and non-destructive retention evidence.

Revision ID: 0006_governance_retention
Revises: 0005_durable_pipeline
"""

from alembic import op


revision = "0006_governance_retention"
down_revision = "0005_durable_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = (
        """
        CREATE TABLE retention_policies (
            retention_class TEXT PRIMARY KEY,
            classification TEXT NOT NULL,
            policy_status TEXT NOT NULL DEFAULT 'proposed',
            archive_after_days INTEGER,
            delete_after_days INTEGER,
            export_expire_after_days INTEGER,
            destructive_deletion_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            production_approved BOOLEAN NOT NULL DEFAULT FALSE,
            approval_reference TEXT,
            configuration_checksum CHAR(64) NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_retention_policy_identity CHECK (
                btrim(retention_class) <> '' AND btrim(classification) <> ''
            ),
            CONSTRAINT ck_retention_policy_status CHECK (
                policy_status IN ('proposed', 'approved', 'retired')
            ),
            CONSTRAINT ck_retention_policy_windows CHECK (
                (archive_after_days IS NULL OR archive_after_days >= 1)
                AND (delete_after_days IS NULL OR delete_after_days >= 1)
                AND (export_expire_after_days IS NULL OR export_expire_after_days >= 1)
            ),
            CONSTRAINT ck_retention_policy_deletion_approval CHECK (
                NOT destructive_deletion_enabled OR (
                    production_approved
                    AND policy_status='approved'
                    AND approval_reference IS NOT NULL
                    AND delete_after_days IS NOT NULL
                )
            ),
            CONSTRAINT ck_retention_policy_checksum CHECK (
                configuration_checksum ~ '^[a-f0-9]{64}$'
            )
        )
        """,
        """
        INSERT INTO retention_policies (
            retention_class, classification, policy_status,
            destructive_deletion_enabled, production_approved,
            configuration_checksum
        ) VALUES (
            'exports', 'exports', 'proposed', FALSE, FALSE,
            repeat('0', 64)
        )
        """,
        """
        CREATE TABLE data_holds (
            data_hold_id UUID PRIMARY KEY,
            hold_type TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMPTZ,
            released_by TEXT,
            released_at TIMESTAMPTZ,
            release_reason TEXT,
            CONSTRAINT ck_data_hold_type CHECK (
                hold_type IN ('legal', 'incident')
            ),
            CONSTRAINT ck_data_hold_scope CHECK (
                btrim(scope_type) <> '' AND btrim(scope_id) <> ''
            ),
            CONSTRAINT ck_data_hold_reason CHECK (
                btrim(reason) <> '' AND btrim(created_by) <> ''
            ),
            CONSTRAINT ck_data_hold_status CHECK (
                status IN ('active', 'released', 'expired')
            ),
            CONSTRAINT ck_data_hold_release CHECK (
                (status='active' AND released_at IS NULL AND released_by IS NULL)
                OR (status IN ('released', 'expired')
                    AND released_at IS NOT NULL AND released_by IS NOT NULL)
            ),
            CONSTRAINT ck_data_hold_expiry CHECK (
                expires_at IS NULL OR expires_at > created_at
            )
        )
        """,
        """
        CREATE INDEX ix_data_holds_active_scope
        ON data_holds (scope_type, scope_id, hold_type, created_at DESC)
        WHERE status='active'
        """,
        """
        CREATE TABLE restore_verifications (
            restore_verification_id UUID PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            backup_reference TEXT NOT NULL,
            backup_checksum CHAR(64) NOT NULL,
            verification_status TEXT NOT NULL,
            verified_by TEXT NOT NULL,
            verified_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT ck_restore_verification_target CHECK (
                btrim(target_type) <> '' AND btrim(target_id) <> ''
            ),
            CONSTRAINT ck_restore_verification_backup CHECK (
                btrim(backup_reference) <> ''
                AND backup_checksum ~ '^[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_restore_verification_status CHECK (
                verification_status IN ('passed', 'failed')
            ),
            CONSTRAINT ck_restore_verification_actor CHECK (btrim(verified_by) <> '')
        )
        """,
        """
        CREATE TABLE deletion_manifests (
            deletion_manifest_id UUID PRIMARY KEY,
            retention_action_id UUID NOT NULL UNIQUE,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            retention_class TEXT NOT NULL,
            action_type TEXT NOT NULL,
            dry_run BOOLEAN NOT NULL DEFAULT TRUE,
            object_count BIGINT NOT NULL DEFAULT 0,
            record_count BIGINT NOT NULL DEFAULT 0,
            total_bytes BIGINT NOT NULL DEFAULT 0,
            target_checksums JSONB NOT NULL DEFAULT '[]'::jsonb,
            hold_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
            restore_verification_id UUID,
            authorised_by TEXT,
            manifest_checksum CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            manifest JSONB NOT NULL,
            CONSTRAINT fk_deletion_manifest_action FOREIGN KEY (retention_action_id)
                REFERENCES retention_actions(retention_action_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT fk_deletion_manifest_policy FOREIGN KEY (retention_class)
                REFERENCES retention_policies(retention_class)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT fk_deletion_manifest_restore FOREIGN KEY (restore_verification_id)
                REFERENCES restore_verifications(restore_verification_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT ck_deletion_manifest_target CHECK (
                btrim(target_type) <> '' AND btrim(target_id) <> ''
            ),
            CONSTRAINT ck_deletion_manifest_action CHECK (
                action_type IN ('report', 'archive', 'delete')
            ),
            CONSTRAINT ck_deletion_manifest_counts CHECK (
                object_count >= 0 AND record_count >= 0 AND total_bytes >= 0
            ),
            CONSTRAINT ck_deletion_manifest_checksum CHECK (
                manifest_checksum ~ '^[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_deletion_manifest_initial_safety CHECK (
                dry_run AND action_type <> 'delete'
            )
        )
        """,
        """
        CREATE TABLE data_subject_requests (
            data_subject_request_id UUID PRIMARY KEY,
            request_type TEXT NOT NULL,
            subject_reference_hash CHAR(64) NOT NULL,
            status TEXT NOT NULL DEFAULT 'received',
            identity_verified BOOLEAN NOT NULL DEFAULT FALSE,
            received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            due_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            decision TEXT,
            handled_by TEXT,
            audit_reference TEXT,
            CONSTRAINT ck_data_subject_request_type CHECK (
                request_type IN ('access', 'correction', 'deletion', 'restriction', 'objection')
            ),
            CONSTRAINT ck_data_subject_reference CHECK (
                subject_reference_hash ~ '^[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_data_subject_status CHECK (
                status IN ('received', 'identity_pending', 'in_review', 'held',
                           'completed', 'rejected', 'cancelled')
            ),
            CONSTRAINT ck_data_subject_completion CHECK (
                (status IN ('completed', 'rejected', 'cancelled')
                    AND completed_at IS NOT NULL AND decision IS NOT NULL)
                OR (status NOT IN ('completed', 'rejected', 'cancelled')
                    AND completed_at IS NULL)
            )
        )
        """,
        """
        ALTER TABLE retention_actions
            ADD COLUMN retention_class TEXT,
            ADD COLUMN action_type TEXT NOT NULL DEFAULT 'report',
            ADD COLUMN hold_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
            ADD COLUMN restore_verification_id UUID,
            ADD COLUMN manifest_checksum CHAR(64),
            ADD COLUMN production_approved BOOLEAN NOT NULL DEFAULT FALSE,
            ADD CONSTRAINT fk_retention_action_policy FOREIGN KEY (retention_class)
                REFERENCES retention_policies(retention_class)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            ADD CONSTRAINT fk_retention_action_restore FOREIGN KEY (restore_verification_id)
                REFERENCES restore_verifications(restore_verification_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            ADD CONSTRAINT ck_retention_action_type CHECK (
                action_type IN ('report', 'archive', 'delete')
            ),
            ADD CONSTRAINT ck_retention_action_manifest_checksum CHECK (
                manifest_checksum IS NULL OR manifest_checksum ~ '^[a-f0-9]{64}$'
            ),
            ADD CONSTRAINT ck_retention_action_destructive_safety CHECK (
                action_type <> 'delete' OR (
                    NOT dry_run AND production_approved
                    AND approved_by IS NOT NULL AND approved_at IS NOT NULL
                    AND restore_verification_id IS NOT NULL
                    AND cardinality(hold_ids)=0
                )
            )
        """,
        """
        ALTER TABLE export_jobs
            ADD COLUMN retention_class TEXT NOT NULL DEFAULT 'exports',
            ADD COLUMN hold_until TIMESTAMPTZ,
            ADD COLUMN expiration_reported_at TIMESTAMPTZ,
            ADD CONSTRAINT fk_export_retention_policy FOREIGN KEY (retention_class)
                REFERENCES retention_policies(retention_class)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            ADD CONSTRAINT ck_export_hold_until CHECK (
                hold_until IS NULL OR hold_until > requested_at
            )
        """,
        """
        CREATE FUNCTION forbid_governance_evidence_mutation() RETURNS TRIGGER
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'governance evidence is append-only';
        END;
        $$
        """,
        """
        CREATE TRIGGER trg_restore_verifications_immutable
        BEFORE UPDATE OR DELETE ON restore_verifications
        FOR EACH ROW EXECUTE FUNCTION forbid_governance_evidence_mutation()
        """,
        """
        CREATE TRIGGER trg_deletion_manifests_immutable
        BEFORE UPDATE OR DELETE ON deletion_manifests
        FOR EACH ROW EXECUTE FUNCTION forbid_governance_evidence_mutation()
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_deletion_manifests_immutable ON deletion_manifests")
    op.execute("DROP TRIGGER trg_restore_verifications_immutable ON restore_verifications")
    op.execute("DROP FUNCTION forbid_governance_evidence_mutation()")
    op.execute("ALTER TABLE export_jobs DROP CONSTRAINT ck_export_hold_until")
    op.execute("ALTER TABLE export_jobs DROP CONSTRAINT fk_export_retention_policy")
    for column in ("expiration_reported_at", "hold_until", "retention_class"):
        op.execute(f"ALTER TABLE export_jobs DROP COLUMN {column}")
    op.execute("ALTER TABLE retention_actions DROP CONSTRAINT ck_retention_action_destructive_safety")
    op.execute("ALTER TABLE retention_actions DROP CONSTRAINT ck_retention_action_manifest_checksum")
    op.execute("ALTER TABLE retention_actions DROP CONSTRAINT ck_retention_action_type")
    op.execute("ALTER TABLE retention_actions DROP CONSTRAINT fk_retention_action_restore")
    op.execute("ALTER TABLE retention_actions DROP CONSTRAINT fk_retention_action_policy")
    for column in (
        "production_approved",
        "manifest_checksum",
        "restore_verification_id",
        "hold_ids",
        "action_type",
        "retention_class",
    ):
        op.execute(f"ALTER TABLE retention_actions DROP COLUMN {column}")
    op.execute("DROP TABLE data_subject_requests")
    op.execute("DROP TABLE deletion_manifests")
    op.execute("DROP TABLE restore_verifications")
    op.execute("DROP TABLE data_holds")
    op.execute("DROP TABLE retention_policies")
