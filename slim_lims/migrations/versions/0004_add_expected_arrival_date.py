"""add expected arrival date

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tr_submissions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('expected_arrival_date', sa.Date(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('tr_submissions', schema=None) as batch_op:
        batch_op.drop_column('expected_arrival_date')

