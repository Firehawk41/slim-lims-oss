"""slim-domain's hash-chained change log on the shared database (slim-data D-77). Postgres only.

`install_audit_log` attaches it to every public table (SLIM's, the app's, slim-data's); re-run
after migrations so new tables are covered. It also locks SLIM's `report_generation_log`, so it
waits until SLIM's tables exist (the demo seed creates them first); the app never creates them.
`chain_check` is what `cli verify` hands slim-data's `verify` (D-76):
None when the database has no log, so the report says "not checked".
"""

import threading
from unittest.mock import patch

from infrastructure import audit as slim_audit
from sqlalchemy import Engine, inspect, text

_installing = threading.Lock()


def install_audit_log(engine: Engine) -> list[str]:
    """The audited table names; nothing on SQLite, or before SLIM's tables exist."""
    if engine.dialect.name != "postgresql" or not inspect(engine).has_table("report_generation_log"):
        return []
    with _installing, engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        if schema == "public":
            return slim_audit.install(connection)
        # A visitor's lab (D-102): slim-domain lists public's tables; give it this schema's instead.
        # (Parked: slim-domain could list current_schema()'s itself.)
        with patch.object(slim_audit, "audited_tables", lambda conn: _tables_of(conn, schema)):
            return slim_audit.install(connection)


def _tables_of(connection, schema: str) -> list[str]:
    rows = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :s ORDER BY tablename"), {"s": schema}
    )
    return [t for t in rows.scalars() if t not in slim_audit.LOG_TABLES]


def chain_check(engine: Engine):
    if engine.dialect.name != "postgresql" or not inspect(engine).has_table(slim_audit.AUDIT_TABLE):
        return None
    return slim_audit.verify_chain
