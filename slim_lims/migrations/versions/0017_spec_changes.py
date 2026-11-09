"""spec limit changes: proposals and decisions (slim-data D-143)

spec_proposals: a proposed change to one limit set, before and after. spec_decisions: approved (written
through slim-domain) or rejected, with what was re-evaluated and which released results now judge
differently. Both append-only.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("spec_proposals", "spec_decisions")


def upgrade() -> None:
    from slim_lims.models import append_only

    op.create_table(
        "spec_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("chemical_id", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.String(length=254), nullable=False),
        sa.Column("proposed_by_id", sa.String(length=64), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_spec_proposals_customer_id", "spec_proposals", ["customer_id"])
    op.create_table(
        "spec_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("by", sa.String(length=254), nullable=False),
        sa.Column("reevaluated", sa.JSON(), nullable=False),
        sa.Column("now_differ", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["spec_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id"),
    )
    for table in _TABLES:
        append_only(op.get_bind(), table)


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if bind.dialect.name == "postgresql":
            op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only() CASCADE")
        else:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation}")
    op.drop_table("spec_decisions")
    op.drop_index("ix_spec_proposals_customer_id", table_name="spec_proposals")
    op.drop_table("spec_proposals")
