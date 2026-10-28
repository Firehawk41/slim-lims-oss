"""create tr_versions (every change to a TR, kept whole; append-only, slim-data D-104)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PG_FUNCTION = """
CREATE OR REPLACE FUNCTION tr_versions_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tr_versions is append-only';
END $$
"""


def upgrade() -> None:
    op.create_table(
        "tr_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change", sa.String(length=100), nullable=False),
        sa.Column("actor_display", sa.String(length=254), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["tr_submissions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "version"),
    )
    op.create_index("ix_tr_versions_submission_id", "tr_versions", ["submission_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_PG_FUNCTION)
        op.execute(
            "CREATE TRIGGER tr_versions_append_only BEFORE UPDATE OR DELETE ON tr_versions "
            "FOR EACH ROW EXECUTE FUNCTION tr_versions_append_only()"
        )
        op.execute(
            "CREATE TRIGGER tr_versions_no_truncate BEFORE TRUNCATE ON tr_versions "
            "FOR EACH STATEMENT EXECUTE FUNCTION tr_versions_append_only()"
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER tr_versions_no_{operation.lower()} BEFORE {operation} ON tr_versions "
                "BEGIN SELECT RAISE(ABORT, 'tr_versions is append-only'); END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS tr_versions_append_only() CASCADE")
    op.drop_index("ix_tr_versions_submission_id", table_name="tr_versions")
    op.drop_table("tr_versions")
