"""Preserve original grant award-date timestamp precision.

Revision ID: 0003_grant_award_timestamp
Revises: 0002_exchange_rate_period
"""

from alembic import op


revision = "0003_grant_award_timestamp"
down_revision = "0002_exchange_rate_period"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE grants
        ALTER COLUMN award_date TYPE TEXT
        USING award_date::text
        """
    )
    op.execute(
        """
        ALTER TABLE grants
        ADD CONSTRAINT ck_grants_award_date_iso
        CHECK (
            award_date IS NULL
            OR award_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE grants DROP CONSTRAINT ck_grants_award_date_iso")
    op.execute(
        """
        ALTER TABLE grants
        ALTER COLUMN award_date TYPE DATE
        USING substring(award_date FROM 1 FOR 10)::date
        """
    )
