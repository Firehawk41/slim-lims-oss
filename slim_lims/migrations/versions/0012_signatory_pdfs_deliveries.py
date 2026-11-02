"""signed reports, report PDFs and deliveries (slim-data D-122, D-123, D-124)

users gain full_name and job_title (printed under a signed report); report_pdfs keeps each issued
report as PDF; report_deliveries records each sending. Both new tables are append-only, as
tr_versions is (0010).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("report_pdfs", "report_deliveries")


def upgrade() -> None:
    from slim_lims.models import append_only

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("full_name", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("job_title", sa.String(length=100), nullable=True))
    op.create_table(
        "report_pdfs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=False),
        sa.Column("html_sha256", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("pdf", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("html_sha256"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_index("ix_report_pdfs_tr_number", "report_pdfs", ["tr_number"])
    op.create_table(
        "report_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("to_addresses", sa.JSON(), nullable=False),
        sa.Column("cc_addresses", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("sent_by", sa.String(length=254), nullable=False),
        sa.Column("transport", sa.String(length=20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_deliveries_tr_number", "report_deliveries", ["tr_number"])
    op.create_index("ix_report_deliveries_issue_id", "report_deliveries", ["issue_id"])
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
    op.drop_index("ix_report_deliveries_issue_id", table_name="report_deliveries")
    op.drop_index("ix_report_deliveries_tr_number", table_name="report_deliveries")
    op.drop_table("report_deliveries")
    op.drop_index("ix_report_pdfs_tr_number", table_name="report_pdfs")
    op.drop_table("report_pdfs")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("job_title")
        batch.drop_column("full_name")
