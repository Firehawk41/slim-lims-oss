from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

import slim_lab_portal
from slim_lab_portal.db import Base, make_engine

ALEMBIC_INI = Path(slim_lab_portal.__file__).parent / "alembic.ini"


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
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "customers" in tables
