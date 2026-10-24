"""create report_files (issued reports, exactly as issued)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'report_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tr_number', sa.String(length=20), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('html', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sha256'),
    )
    with op.batch_alter_table('report_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_report_files_tr_number'), ['tr_number'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('report_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_report_files_tr_number'))
    op.drop_table('report_files')
