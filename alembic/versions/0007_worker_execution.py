"""Add concurrency-safe active-job deduplication for the PostgreSQL worker.

Revision ID: 0007_worker_execution
Revises: 0006_governance_retention
"""

from alembic import op


revision = "0007_worker_execution"
down_revision = "0006_governance_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE job_runs ADD COLUMN active_dedupe_key TEXT")
    op.execute(
        """
        ALTER TABLE job_runs
        ADD CONSTRAINT ck_job_runs_active_dedupe_key
        CHECK (
            active_dedupe_key IS NULL
            OR (btrim(active_dedupe_key) <> '' AND length(active_dedupe_key) <= 200)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_job_runs_active_dedupe
        ON job_runs (active_dedupe_key)
        WHERE active_dedupe_key IS NOT NULL
          AND status IN ('queued', 'running')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_job_runs_active_dedupe")
    op.execute(
        "ALTER TABLE job_runs DROP CONSTRAINT IF EXISTS ck_job_runs_active_dedupe_key"
    )
    op.execute("ALTER TABLE job_runs DROP COLUMN IF EXISTS active_dedupe_key")
