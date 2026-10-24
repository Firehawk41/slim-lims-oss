"""The portal attaches to the OSS demo ecosystem only (slim_lab_portal/oss_guard.py)."""

import re
from pathlib import Path

import pytest

import slim_lab_portal
from slim_lab_portal.config import load_settings
from slim_lab_portal.db import make_engine
from slim_lab_portal.oss_guard import NotAnOssDatabase, assert_oss_database

REPO = Path(slim_lab_portal.__file__).resolve().parent.parent


@pytest.mark.parametrize("url", [
    "sqlite:///demo.db",
    "sqlite://",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/slim",
    "postgresql+psycopg2://postgres@127.0.0.1:5433/slim_demo",
    "postgresql+psycopg2://postgres:postgres@db:5432/slim",  # docker-compose service
])
def test_demo_databases_are_allowed(url):
    assert_oss_database(url)


@pytest.mark.parametrize("url", [
    "postgresql+psycopg2://u:p@precilab-prod.example.com/slim",
    "postgresql+psycopg2://u:p@localhost/PreciLab_production",
    "sqlite:///C:/PreciLab/slim.db",
    "postgresql+psycopg2://precilab_app:p@localhost/slim",
])
def test_precilab_is_always_refused(url, monkeypatch):
    monkeypatch.setenv("PORTAL_ALLOWED_DB_HOSTS", "precilab-prod.example.com")  # no override exists
    with pytest.raises(NotAnOssDatabase, match="precilab"):
        assert_oss_database(url)


def test_unknown_hosts_are_refused_unless_listed(monkeypatch):
    url = "postgresql+psycopg2://u:p@lab-server.internal:5432/slim"
    with pytest.raises(NotAnOssDatabase, match="lab-server.internal"):
        assert_oss_database(url)
    monkeypatch.setenv("PORTAL_ALLOWED_DB_HOSTS", "demo-db.example.dev, lab-server.internal")
    assert_oss_database(url)


def test_make_engine_enforces_the_guard():
    with pytest.raises(NotAnOssDatabase):
        make_engine("postgresql+psycopg2://u:p@lab-server.internal/slim")


def test_nothing_bypasses_make_engine():
    """Every connection must go through db.make_engine (where the guard lives)."""
    offenders = []
    for path in [*REPO.joinpath("slim_lab_portal").rglob("*.py"), *REPO.joinpath("scripts").rglob("*.py")]:
        if path.name == "db.py":
            continue
        if re.search(r"\bcreate_engine\s*\(", path.read_text()):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_env_file_is_read_from_current_directory_only(tmp_path, monkeypatch):
    parent_env = tmp_path / ".env"
    parent_env.write_text("DATABASE_URL=postgresql+psycopg2://u:p@precilab-prod/slim\n")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert "precilab" not in load_settings().database_url
