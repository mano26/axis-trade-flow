"""catch up two columns that were added to production by hand

Revision ID: e1f2a3b4c5d6
Revises: c1d2e3f4a5b6
Create Date: 2026-11-06

Commit 1fe071b (2026-05-26, "fix for fractional futures odd delta") added
two columns to the models without a migration:

    order_legs.cvd_raw_volume            FLOAT    NULL
    fill_counterparties.futures_quantity INTEGER  NULL

Production already has both (they were added directly to the database), so
on production this migration does nothing. On any database built from
migrations alone (a new staging environment, a restore, local dev) it
creates them, so order detail, EOD summary and CVD counterparty entry work.

Each column is only added if it is missing, so this is safe to run anywhere.
"""

from alembic import op
import sqlalchemy as sa


revision = 'e1f2a3b4c5d6'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


MISSING = [
    ("order_legs", sa.Column(
        "cvd_raw_volume", sa.Float(), nullable=True,
        comment="Unrounded CVD futures volume (order_qty * delta%). NULL for non-CVD legs.")),
    ("fill_counterparties", sa.Column(
        "futures_quantity", sa.Integer(), nullable=True,
        comment="Trader-chosen integer futures qty for CVD fills. NULL = auto-compute.")),
]


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table, column in MISSING:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column.name not in existing:
            op.add_column(table, column)


def downgrade():
    # Deliberately a no-op: production had these columns before this
    # migration existed, and the application cannot run without them.
    # Dropping them on downgrade would destroy live CVD data.
    pass
