"""QC warnings at release: acknowledgements (slim-data D-144)

qc_acknowledgements: a sample + analysis released despite an ACTION-severity QC trend flag, with the
flags as shown then, the reason, who and when. Append-only.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from slim_lims.models import append_only

    op.create_table(
        "qc_acknowledgements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample", sa.String(length=20), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("by", sa.String(length=254), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qc_acknowledgements_sample", "qc_acknowledgements", ["sample"])
    append_only(op.get_bind(), "qc_acknowledgements")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS qc_acknowledgements_append_only() CASCADE")
    else:
        for operation in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS qc_acknowledgements_no_{operation}")
    op.drop_index("ix_qc_acknowledgements_sample", table_name="qc_acknowledgements")
    op.drop_table("qc_acknowledgements")
