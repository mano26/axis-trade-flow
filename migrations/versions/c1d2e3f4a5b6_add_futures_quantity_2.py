"""add futures_quantity_2 to fill_counterparties

Revision ID: c1d2e3f4a5b6
Revises: b9c3d4e5f6a7
Create Date: 2026-10-30

Adds futures_quantity_2 to support per-leg futures quantity tracking in
multi-expiration trades (e.g., SFRZ7 BUY / SFRM8 SELL calendar spreads).
Previously all futures legs shared a single futures_quantity field, making
it impossible to record different quantities for each expiration.
"""

from alembic import op
import sqlalchemy as sa


revision = 'c1d2e3f4a5b6'
down_revision = 'b9c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'fill_counterparties',
        sa.Column(
            'futures_quantity_2',
            sa.Integer(),
            nullable=True,
            comment=(
                "Trader-chosen integer futures quantity for the SECOND futures leg "
                "in multi-expiration trades. NULL = auto-compute from leg_ratio."
            ),
        ),
    )


def downgrade():
    op.drop_column('fill_counterparties', 'futures_quantity_2')
