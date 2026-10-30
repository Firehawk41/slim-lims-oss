from datetime import date, datetime
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

import slim_lims
from slim_lims.db import Base, make_engine

ALEMBIC_INI = Path(slim_lims.__file__).parent / "alembic.ini"


def test_migrations_match_models(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_migrations_leave_slim_tables_alone(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'shared.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE customers ("ID" INTEGER PRIMARY KEY, customer_name TEXT)')
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.downgrade(config, "base")
    with engine.connect() as connection:
        tables = {
            row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "customers" in tables


def test_0011_moves_each_payment_to_a_row_of_its_own(tmp_path):
    """R4 (D-119): an invoice paid before 0011 keeps its payment, now an append-only row."""
    engine = make_engine(f"sqlite:///{tmp_path / 'paid.db'}")
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0010")
        for tr in (1, 2):  # a bare TR for each invoice: every required column, any value
            columns = [c for c in inspect(connection).get_columns("tr_submissions") if not c["nullable"]]
            values = {c["name"]: _any(c["type"], tr) for c in columns}
            connection.execute(text(_insert("tr_submissions", values)), values)
        connection.exec_driver_sql(
            "INSERT INTO tr_invoices (id, submission_id, sent_on, sent_by, paid_on, paid_by, recorded_at) "
            "VALUES (1, 1, '2026-10-01', 'SA', '2026-10-20', 'SB', '2026-10-01 09:00:00'), "
            "(2, 2, '2026-10-02', 'SA', NULL, '', '2026-10-02 09:00:00')"
        )
        command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT invoice_id, paid_on, paid_by FROM tr_invoice_payments")
        assert [tuple(r) for r in rows] == [(1, "2026-10-20", "SB")]
        assert "paid_on" not in {c["name"] for c in inspect(connection).get_columns("tr_invoices")}
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0010")
        rows = connection.exec_driver_sql("SELECT id, paid_on, paid_by FROM tr_invoices ORDER BY id")
        assert [tuple(r) for r in rows] == [(1, "2026-10-20", "SB"), (2, None, "")]


def _any(column_type, n: int):
    python = column_type.python_type
    if python is int:
        return n
    if python is datetime:
        return datetime(2026, 10, 1)
    if python is date:
        return date(2026, 10, 1)
    if python is bool:
        return False
    return f"TR{n:05d}"


def _insert(table: str, values: dict) -> str:
    return f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"
