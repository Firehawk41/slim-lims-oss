"""quotes, the host side (slim-data D-139)

quote_requests and quote_request_events: a customer's request for a quote and what became of it.
quote_pdfs and quote_deliveries: each quote's PDF and its sendings (the outbox). tr_quotes: the quote a
TR named. All append-only. The quotes themselves are invoicing V5's.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("quote_requests", "quote_request_events", "quote_pdfs", "quote_deliveries", "tr_quotes")


def upgrade() -> None:
    from slim_lims.models import append_only

    op.create_table(
        "quote_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=254), nullable=False),
        sa.Column("analysis_ids", sa.JSON(), nullable=False),
        sa.Column("processing_times", sa.JSON(), nullable=False),
        sa.Column("samples_estimate", sa.String(length=100), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quote_requests_customer_id", "quote_requests", ["customer_id"])
    op.create_table(
        "quote_request_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("by", sa.String(length=254), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["quote_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quote_request_events_request_id", "quote_request_events", ["request_id"])
    op.create_table(
        "quote_pdfs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("pdf", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_table(
        "quote_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("to_addresses", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("sent_by", sa.String(length=254), nullable=False),
        sa.Column("transport", sa.String(length=20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quote_deliveries_quote_id", "quote_deliveries", ["quote_id"])
    op.create_table(
        "tr_quotes",
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["tr_submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("submission_id"),
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
    op.drop_table("tr_quotes")
    op.drop_index("ix_quote_deliveries_quote_id", table_name="quote_deliveries")
    op.drop_table("quote_deliveries")
    op.drop_table("quote_pdfs")
    op.drop_index("ix_quote_request_events_request_id", table_name="quote_request_events")
    op.drop_table("quote_request_events")
    op.drop_index("ix_quote_requests_customer_id", table_name="quote_requests")
    op.drop_table("quote_requests")
