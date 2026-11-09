"""reference data upkeep: the app's record of each change (slim-data D-141)

reference_changes: a customer added or edited, a chemical added (through slim-domain's services), an
analysis's catalog entry edited; before, after, why, who, when. Append-only.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from slim_lims.models import append_only

    op.create_table(
        "reference_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("by", sa.String(length=254), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reference_changes_kind", "reference_changes", ["kind"])
    op.create_index("ix_reference_changes_ref_id", "reference_changes", ["ref_id"])
    append_only(op.get_bind(), "reference_changes")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS reference_changes_append_only() CASCADE")
    else:
        for operation in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS reference_changes_no_{operation}")
    op.drop_index("ix_reference_changes_ref_id", table_name="reference_changes")
    op.drop_index("ix_reference_changes_kind", table_name="reference_changes")
    op.drop_table("reference_changes")
