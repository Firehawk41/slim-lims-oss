"""A request closed without an invoice (review X-3)

tr_invoices.no_charge_reason: set when a request is closed as no charge (a proficiency testing sample, which
the lab doesn't bill the scheme for) instead of invoiced; the row still says who closed it and when.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tr_invoices", sa.Column("no_charge_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    # A plain DROP COLUMN (SQLite 3.35+): batch mode would rebuild tr_invoices, which payments reference.
    op.execute("ALTER TABLE tr_invoices DROP COLUMN no_charge_reason")
