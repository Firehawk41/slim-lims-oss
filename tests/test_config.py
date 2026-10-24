import pytest

from slim_lims.config import _database_url


@pytest.mark.parametrize("raw", ["postgres://u:p@dpg-x-a/demo", "postgresql://u:p@dpg-x-a/demo"])
def test_hosted_postgres_urls_name_the_installed_driver(raw):
    # Render hands out driverless URLs; SQLAlchemy would refuse postgres:// or pick psycopg 3.
    assert _database_url(raw) == "postgresql+psycopg2://u:p@dpg-x-a/demo"


@pytest.mark.parametrize("raw", ["postgresql+psycopg2://u:p@db/slim", "sqlite:///portal.db"])
def test_other_urls_are_left_alone(raw):
    assert _database_url(raw) == raw
