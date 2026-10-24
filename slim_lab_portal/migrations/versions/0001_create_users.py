"""create users

Revision ID: 0001
Revises: 
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=100), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('initials', sa.String(length=10), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role <> 'customer' OR customer_id IS NOT NULL", name=op.f('ck_users_customer_has_customer_id')),
    sa.CheckConstraint("role <> 'staff' OR (initials IS NOT NULL AND initials <> '')", name=op.f('ck_users_staff_has_initials')),
    sa.CheckConstraint("role IN ('customer', 'staff')", name=op.f('ck_users_role_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_customer_id'), ['customer_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_customer_id'))

    op.drop_table('users')
