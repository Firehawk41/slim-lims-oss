"""the day's board: lab sections, display links, the names switch (slim-data D-138)

lab_sections and lab_section_analyses: the lab's own sections, each owning some catalog analyses (one
section per analysis). display_links: a screen's link, by its token's sha256. display_modes: whether
the screens show customer names, append-only (the latest row wins).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from slim_lims.models import append_only

    op.create_table(
        "lab_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "lab_section_analyses",
        sa.Column("analysis_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["section_id"], ["lab_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("analysis_id"),
    )
    op.create_index("ix_lab_section_analyses_section_id", "lab_section_analyses", ["section_id"])
    op.create_table(
        "display_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=254), nullable=True),
        sa.ForeignKeyConstraint(["section_id"], ["lab_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_sha256"),
    )
    op.create_table(
        "display_modes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("names_shown", sa.Boolean(), nullable=False),
        sa.Column("set_by", sa.String(length=254), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    append_only(op.get_bind(), "display_modes")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS display_modes_append_only() CASCADE")
    else:
        for operation in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS display_modes_no_{operation}")
    op.drop_table("display_modes")
    op.drop_table("display_links")
    op.drop_index("ix_lab_section_analyses_section_id", table_name="lab_section_analyses")
    op.drop_table("lab_section_analyses")
    op.drop_table("lab_sections")
