"""add ui_theme to tenants

Revision ID: d4e5f6a7b8c9
Revises: e1f2a3b4c5d6
Create Date: 2026-11-06

Adds a per-company screen design switch. Every existing company gets
'classic' via the server default, so nothing changes for current users until
a super admin explicitly switches a company to 'confirmengine'.
"""

from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'tenants',
        sa.Column(
            'ui_theme',
            sa.String(length=20),
            nullable=False,
            server_default='classic',
            comment="Screen design: 'classic' or 'confirmengine'. Visual only.",
        ),
    )


def downgrade():
    op.drop_column('tenants', 'ui_theme')
