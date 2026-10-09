"""add keypunch fields: stamp times on fills, order override

Revision ID: b9c3d4e5f6a7
Revises: a3f2b1c4d5e6
Create Date: 2026-10-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9c3d4e5f6a7'
down_revision = 'a3f2b1c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    # Fill: add stamp_time_in and stamp_time_out
    op.add_column(
        'fills',
        sa.Column('stamp_time_in', sa.String(length=10), nullable=True),
    )
    op.add_column(
        'fills',
        sa.Column('stamp_time_out', sa.String(length=10), nullable=True),
    )

    # Order: add keypunch_order_override
    op.add_column(
        'orders',
        sa.Column('keypunch_order_override', sa.String(length=50), nullable=True),
    )


def downgrade():
    op.drop_column('fills', 'stamp_time_in')
    op.drop_column('fills', 'stamp_time_out')
    op.drop_column('orders', 'keypunch_order_override')
