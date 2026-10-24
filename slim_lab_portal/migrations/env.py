"""Alembic environment: migrates only the tables declared on slim_lab_portal.db.Base.

SLIM reference tables (customers, chemicals, elements, analyses) live in the same database
but belong to slim-domain; `include_object` keeps autogenerate from ever touching them.
"""

from logging.config import fileConfig

from alembic import context

from slim_lab_portal import models  # noqa: F401 -- registers tables on Base.metadata
from slim_lab_portal.config import load_settings
from slim_lab_portal.db import Base, make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in target_metadata.tables
    return True


def _url() -> str:
    return config.attributes.get("database_url") or load_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = make_engine(_url())
    with engine.connect() as connection:
        _run(connection)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=True,  # SQLite needs batch mode for ALTERs
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
