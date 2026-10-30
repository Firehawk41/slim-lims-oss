"""payments as append-only rows (slim-data D-119; app review 2026-10-30b R4)

tr_invoice_payments holds each payment; tr_invoices loses paid_on / paid_by (moved across) and both
tables refuse UPDATE and DELETE, as tr_versions does (0010).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("tr_invoices", "tr_invoice_payments")


def _append_only(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE OR REPLACE FUNCTION {table}_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ "
            f"BEGIN RAISE EXCEPTION '{table} is append-only'; END $$"
        )
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_append_only()"
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
            )


def upgrade() -> None:
    op.create_table(
        "tr_invoice_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("paid_by", sa.String(length=254), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tr_invoice_payments_invoice_id", "tr_invoice_payments", ["invoice_id"])
    op.execute(
        "INSERT INTO tr_invoice_payments (invoice_id, paid_on, paid_by, recorded_at) "
        "SELECT id, paid_on, paid_by, recorded_at FROM tr_invoices WHERE paid_on IS NOT NULL ORDER BY id"
    )
    with op.batch_alter_table("tr_invoices") as batch:
        batch.drop_column("paid_on")
        batch.drop_column("paid_by")
    # The key goes on last: on SQLite, dropping the columns rebuilds tr_invoices, which a row
    # referencing it would refuse.
    with op.batch_alter_table("tr_invoice_payments") as batch:
        batch.create_foreign_key("fk_tr_invoice_payments_invoice_id", "tr_invoices", ["invoice_id"], ["id"])
    for table in _TABLES:
        _append_only(table)


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if bind.dialect.name == "postgresql":
            op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only() CASCADE")
        else:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation}")
    with op.batch_alter_table("tr_invoice_payments") as batch:
        batch.drop_constraint("fk_tr_invoice_payments_invoice_id", type_="foreignkey")
    with op.batch_alter_table("tr_invoices") as batch:
        batch.add_column(sa.Column("paid_on", sa.Date(), nullable=True))
        batch.add_column(sa.Column("paid_by", sa.String(length=254), nullable=False, server_default=""))
    op.execute(
        "UPDATE tr_invoices SET paid_on = (SELECT MIN(p.paid_on) FROM tr_invoice_payments p "
        "WHERE p.invoice_id = tr_invoices.id), paid_by = COALESCE((SELECT MIN(p.paid_by) FROM "
        "tr_invoice_payments p WHERE p.invoice_id = tr_invoices.id), '')"
    )
    op.drop_index("ix_tr_invoice_payments_invoice_id", table_name="tr_invoice_payments")
    op.drop_table("tr_invoice_payments")
