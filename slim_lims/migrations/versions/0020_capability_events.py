"""capability_events: the history of capability grants (review T-3)

Granted / revoked, whose, by whom, when. Append-only. The grants on file when the history begins are
written as granted, at the time and by the person their row records.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from slim_lims.models import append_only

    op.create_table(
        "capability_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("by", sa.String(length=254), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_capability_events_user_id", "capability_events", ["user_id"])
    op.execute(
        "INSERT INTO capability_events (user_id, capability, kind, by, at) "
        "SELECT c.user_id, c.capability, 'granted', COALESCE(u.email, ''), c.created_at "
        "FROM user_capabilities c LEFT JOIN users u ON u.id = c.granted_by"
    )
    append_only(op.get_bind(), "capability_events")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS capability_events_append_only() CASCADE")
    else:
        for operation in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS capability_events_no_{operation}")
    op.drop_index("ix_capability_events_user_id", table_name="capability_events")
    op.drop_table("capability_events")
