import os
import re

import pytest
from fastapi.testclient import TestClient
from slim_data.persistence import migrate as slim_data_migrate
from slim_invoicing.persistence import migrate as invoicing_migrate
from slim_quality.persistence import migrate as quality_migrate

from slim_lims.config import Settings
from slim_lims.db import Base, make_engine, make_session_factory
from slim_lims.domain import RequestType
from slim_lims.main import create_app
from slim_lims.models import LAB_ROLE_SUPERVISOR, ROLE_CUSTOMER, ROLE_STAFF, User
from slim_lims.reference import AnalysisRef, ChemicalRef, CustomerRef, ElementRef, StaticReferenceData
from slim_lims.security import hash_password
from slim_lims.slim_tables import slim_metadata

PASSWORD = "correct horse battery staple"


def make_settings(database_url: str, **overrides) -> Settings:
    values = dict(
        env="test",
        database_url=database_url,
        secret_key="test-secret",
        session_cookie_secure=False,
        session_max_age_seconds=3600,
        max_body_bytes=1024 * 1024,
    )
    values.update(overrides)
    return Settings(**values)


# Tests run on SQLite by default. Set TEST_DATABASE_URL to a throwaway Postgres database to
# run them on Postgres too (its tables are dropped and recreated for every test).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
postgres_only = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs TEST_DATABASE_URL (Postgres)")


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(TEST_DATABASE_URL or f"sqlite:///{tmp_path / 'test.db'}")
    slim_metadata.drop_all(engine)  # SLIM test tables some tests create (tests/slim_fixtures.py)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:  # slim-data's tables (a required library): fresh for every test
        slim_data_migrate.downgrade(connection)
        slim_data_migrate.upgrade(connection)
        invoicing_migrate.downgrade(connection)  # and invoicing's (slim-data D-83)
        invoicing_migrate.upgrade(connection)
        quality_migrate.downgrade(connection)  # and slim-quality's (D-109)
        quality_migrate.upgrade(connection)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    with make_session_factory(engine)() as session:
        yield session


@pytest.fixture
def reference():
    return demo_reference()


@pytest.fixture
def app(engine, reference):
    return create_app(make_settings(str(engine.url)), engine=engine, reference=reference)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def staff_user(db):
    user = User(
        email="staff@lab.example",
        password_hash=hash_password(PASSWORD),
        role=ROLE_STAFF,
        initials="JM",
        lab_role=LAB_ROLE_SUPERVISOR,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def customer_user(db):
    user = User(
        email="jane@acme.example", password_hash=hash_password(PASSWORD), role=ROLE_CUSTOMER, customer_id=1
    )
    db.add(user)
    db.commit()
    return user


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf_token field in page"
    return match.group(1)


def login_api(client: TestClient, email: str) -> None:
    """Logs in and sets the X-CSRF-Token header for later JSON requests."""
    client.headers.pop("X-CSRF-Token", None)  # a stale header would override the form token
    assert login(client, email).status_code == 303
    client.headers["X-CSRF-Token"] = csrf_from(client.get("/").text)


def login(client: TestClient, email: str, password: str = PASSWORD):
    token = csrf_from(client.get("/login").text)
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token, "next": "/"},
        follow_redirects=False,
    )


def demo_reference() -> StaticReferenceData:
    """Small fictional reference set: customers 1 (A), 2 (B, two sites), 7 (G, a group)."""
    C, W, F = RequestType.CHEMICAL, RequestType.WATER, RequestType.WAFER
    return StaticReferenceData.of(
        customers=[
            CustomerRef(1, "A", "1 A Street", "Anytown", "TX", "75000", "USA"),
            CustomerRef(2, "B", "2 B Street", "Anytown", "TX", "75000", "USA"),
            CustomerRef(7, "G", is_group=True),
        ],
        chemicals=[ChemicalRef(1, "Water"), ChemicalRef(2, "Chemical 01"), ChemicalRef(3, "Chemical 02")],
        elements=[ElementRef(3, "Li", "Lithium"), ElementRef(26, "Fe", "Iron")],
        analyses=[
            AnalysisRef(1, "36 Elements", "36_elements_icpms", "Metals", request_types=frozenset({C, W, F})),
            AnalysisRef(2, "pH", "ph", "Physical", request_types=frozenset({C, W})),
            AnalysisRef(3, "TOC", "toc", "Organics", request_types=frozenset({W})),
            AnalysisRef(
                4, "Additional Element", "additional_element_icpms", "Metals", portal_selectable=False
            ),
        ],
        customer_locations={2: ("SX5N", "SX5S")},
    )
