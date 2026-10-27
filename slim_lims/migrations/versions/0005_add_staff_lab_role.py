"""add staff lab role; staff initials unique

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lab_role", sa.Integer(), nullable=True))
    # Existing staff start as analysts (least privilege); a supervisor promotes them (D-70).
    op.execute("UPDATE users SET lab_role = 1 WHERE role = 'staff'")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_users_staff_has_lab_role"), "role <> 'staff' OR lab_role IS NOT NULL"
        )
        batch_op.create_check_constraint(
            op.f("ck_users_lab_role_valid"), "lab_role IS NULL OR lab_role IN (1, 2, 3)"
        )
        batch_op.create_unique_constraint(batch_op.f("uq_users_initials"), ["initials"])


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_users_initials"), type_="unique")
        batch_op.drop_constraint(op.f("ck_users_lab_role_valid"), type_="check")
        batch_op.drop_constraint(op.f("ck_users_staff_has_lab_role"), type_="check")
        batch_op.drop_column("lab_role")
