"""add cp_house to fill_counterparties

Revision ID: a3f2b1c4d5e6
Revises: f87a239a4794
Create Date: 2026-06-05 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a3f2b1c4d5e6'
down_revision = 'f87a239a4794'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('fill_counterparties', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('cp_house', sa.String(length=50), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('fill_counterparties', schema=None) as batch_op:
        batch_op.drop_column('cp_house')