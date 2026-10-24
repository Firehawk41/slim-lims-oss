"""create tr_invoices (invoice sent / paid per TR, until the invoicing module is wired)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tr_invoices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('sent_on', sa.Date(), nullable=False),
        sa.Column('sent_by', sa.String(length=254), nullable=False),
        sa.Column('paid_on', sa.Date(), nullable=True),
        sa.Column('paid_by', sa.String(length=254), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['tr_submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('tr_invoices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tr_invoices_submission_id'), ['submission_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tr_invoices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tr_invoices_submission_id'))
    op.drop_table('tr_invoices')
