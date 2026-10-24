"""create analysis catalog

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('analysis_catalog',
    sa.Column('analysis_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('group_name', sa.String(length=60), nullable=False),
    sa.Column('request_types', sa.String(length=20), nullable=False),
    sa.Column('portal_selectable', sa.Boolean(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('analysis_id', name=op.f('pk_analysis_catalog')),
    sa.UniqueConstraint('code', name=op.f('uq_analysis_catalog_code'))
    )


def downgrade() -> None:
    op.drop_table('analysis_catalog')
