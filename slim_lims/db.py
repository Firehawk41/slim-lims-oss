"""SQLAlchemy 2 engine, session factory and the declarative base for app-owned tables.

SLIM reference tables (customers, chemicals, ...) are NOT declared on `Base`: the app
only reads them, and Alembic must never create or alter them.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from slim_lims.oss_guard import assert_oss_database

# Deterministic constraint names so Alembic migrations are portable between SQLite and Postgres.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(url: str, **kwargs) -> Engine:
    """The only way the app connects to a database. OSS demo databases only (see oss_guard)."""
    assert_oss_database(url)
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    else:
        kwargs.setdefault("pool_pre_ping", True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_connect)
        event.listen(engine, "begin", _sqlite_begin)
    return engine


def _sqlite_connect(dbapi_connection, _record) -> None:
    # pysqlite starts transactions itself and breaks SAVEPOINT, which slim-data's all-or-nothing
    # import needs; let SQLAlchemy emit BEGIN instead (the documented recipe, as slim-data does).
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _sqlite_begin(connection) -> None:
    connection.exec_driver_sql("BEGIN")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request. Callers commit explicitly."""
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
