"""slim-domain's hash-chained change log on the shared database (slim-data D-77). Postgres only.

`install_audit_log` attaches it to every public table (SLIM's, the portal's, slim-data's); re-run
after migrations so new tables are covered. It also locks SLIM's `report_generation_log`, so it
waits until SLIM's tables exist (the demo seed creates them first); the portal never creates them.
`chain_check` is what `cli verify` hands slim-data's `verify` (D-76):
None when the database has no log, so the report says "not checked".
"""

from infrastructure import audit as slim_audit
from sqlalchemy import Engine, inspect


def install_audit_log(engine: Engine) -> list[str]:
    """The audited table names; nothing on SQLite, or before SLIM's tables exist."""
    if engine.dialect.name != "postgresql" or not inspect(engine).has_table("report_generation_log"):
        return []
    with engine.begin() as connection:
        return slim_audit.install(connection)


def chain_check(engine: Engine):
    if engine.dialect.name != "postgresql" or not inspect(engine).has_table(slim_audit.AUDIT_TABLE):
        return None
    return slim_audit.verify_chain
