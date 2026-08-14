"""Preserve the monthly precision of grant exchange-rate periods.

Revision ID: 0002_exchange_rate_period
Revises: 0001_postgresql_foundation
"""

from alembic import op


revision = "0002_exchange_rate_period"
down_revision = "0001_postgresql_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE grants
        ALTER COLUMN exchange_rate_date TYPE VARCHAR(7)
        USING to_char(exchange_rate_date, 'YYYY-MM')
        """
    )
    op.execute(
        """
        ALTER TABLE grants
        ADD CONSTRAINT ck_grants_exchange_rate_period
        CHECK (
            exchange_rate_date IS NULL
            OR exchange_rate_date ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE grants DROP CONSTRAINT ck_grants_exchange_rate_period"
    )
    op.execute(
        """
        ALTER TABLE grants
        ALTER COLUMN exchange_rate_date TYPE DATE
        USING (exchange_rate_date || '-01')::date
        """
    )
