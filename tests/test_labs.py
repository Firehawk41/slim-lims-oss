"""A lab per visitor (slim-data D-102): Postgres only, in a database of its own (public must hold no app
tables). The housekeeping runs on a stand-in seed; the visitor flow on the real demo seed, skipped
without the slim-report-engine-oss checkout."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from slim_lims.db import make_engine
from slim_lims.labs import READY, Labs, ensure_schema, schema_of, with_schema, without_schema
from tests.conftest import TEST_DATABASE_URL, csrf_from, make_settings, postgres_only

pytestmark = postgres_only


@pytest.fixture(scope="module")
def url():
    """A fresh database beside the test database: labs need public to themselves."""
    base = make_url(TEST_DATABASE_URL)
    name = f"{base.database}_labs"
    admin = make_engine(
        base.set(database="postgres").render_as_string(hide_password=False), poolclass=NullPool
    )
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield base.set(database=name).render_as_string(hide_password=False)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _stand_in_seed(lab_url: str) -> None:
    engine = make_engine(lab_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE marker (n integer)"))
    engine.dispose()


def _schemas(url: str) -> set[str]:
    engine = make_engine(url, poolclass=NullPool)
    try:
        return {s for s in inspect(engine).get_schema_names() if s.startswith("lab_")}
    finally:
        engine.dispose()


def test_urls_name_the_lab_first_and_refuse_odd_names():
    lab = with_schema("postgresql+psycopg2://u:p@localhost/db", "lab_0123456789abcdef")
    assert schema_of(lab) == "lab_0123456789abcdef" and schema_of(without_schema(lab)) is None
    with pytest.raises(ValueError):
        with_schema("postgresql+psycopg2://u:p@localhost/db", 'lab_x"; drop')


def test_spares_claims_idle_labs_and_the_cap(url):
    now = [datetime(2026, 10, 28, 12, tzinfo=UTC)]
    labs = Labs(url, _stand_in_seed, spares=2, cap=3, clock=lambda: now[0], background=False)
    labs.check()
    assert labs.refill() == 2 and len(_schemas(url)) == 2

    mine = labs.claim()  # a ready spare: no seeding
    session = labs.enter(mine)()
    assert session.execute(text("SELECT count(*) FROM marker")).scalar() == 0
    session.close()
    theirs = labs.claim()
    assert labs.refill() == 1  # the cap (3) leaves room for one spare beside the two claimed
    assert len(_schemas(url)) == 3

    now[0] += timedelta(minutes=59)
    assert labs.enter(mine) is not None  # a visit keeps it
    now[0] += timedelta(minutes=2)
    assert labs.sweep() == [theirs]  # idle an hour
    assert labs.enter(theirs) is None and theirs not in _schemas(url)

    labs.drop(mine)
    assert labs.enter(mine) is None
    labs.start()  # nothing half-made to drop
    for schema in _schemas(url):
        labs.drop(schema)


def test_a_half_seeded_lab_is_dropped(url):
    def broken(lab_url):
        _stand_in_seed(lab_url)
        raise RuntimeError("seed failed")

    labs = Labs(url, broken, spares=1)
    labs.check()
    before = _schemas(url)
    with pytest.raises(RuntimeError):
        labs._make(READY)
    assert _schemas(url) == before


def test_labs_refuse_a_public_schema_with_app_tables(url):
    engine = make_engine(url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE public.users (id integer)"))
    try:
        with pytest.raises(RuntimeError, match="public schema without app tables; found users"):
            Labs(url, _stand_in_seed).check()
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE public.users"))
        engine.dispose()


# ---------------------------------------------------------------- the real thing


@pytest.fixture(scope="module")
def demo(url):
    from slim_lims.demo_seed import DEFAULT_OSS_DIR, seed_demo

    if not (DEFAULT_OSS_DIR / "demo" / "demo.py").is_file():
        pytest.skip(f"OSS demo not found at {DEFAULT_OSS_DIR}")
    shared = with_schema(url, "lab_shared")
    ensure_schema(shared)
    seed_demo(shared)  # password logins and the CLI
    labs = Labs(url, seed=lambda lab_url: seed_demo(lab_url), spares=0, background=False)
    labs.check()
    return shared, labs


def _visitor(demo):
    from slim_lims.main import create_app

    shared, labs = demo
    settings = make_settings(shared, demo_personas=True)
    return TestClient(create_app(settings, labs=labs))


def _continue_as(client, label="Ana (analyst)"):
    page = client.get("/demo/personas")
    persona = page.text.split(f">{label}")[0].rsplit('name="user_id" value="', 1)[1].split('"')[0]
    return client.post(
        "/demo/continue-as",
        data={"csrf_token": csrf_from(page.text), "user_id": persona},
        follow_redirects=False,
    )


def _lab_of(labs, user_id: str) -> str:
    """The lab whose users include this id (each lab's personas have their own ids)."""
    found = []
    for schema in _schemas(labs.url):
        engine = make_engine(with_schema(labs.url, schema), poolclass=NullPool)
        with engine.connect() as conn:
            has_users = conn.scalar(text("SELECT to_regclass(:t)"), {"t": f"{schema}.users"})
            if has_users and conn.execute(text("SELECT 1 FROM users WHERE id = :i"), {"i": user_id}).first():
                found.append(schema)
        engine.dispose()
    assert len(found) == 1, found
    return found[0]


def test_each_visitor_gets_a_seeded_lab_of_their_own(demo):
    shared, labs = demo
    with _visitor(demo) as one, _visitor(demo) as two:
        assert _continue_as(one).status_code == 303 and _continue_as(two).status_code == 303
        board = one.get("/")
        assert board.status_code == 200 and "TR00001" in board.text  # the seeded scenarios
        lab_one = _lab_of(labs, one.get("/api/me").json()["id"])
        lab_two = _lab_of(labs, two.get("/api/me").json()["id"])
        assert len({lab_one, lab_two, "lab_shared"}) == 3

        # Switching persona keeps the lab; a fresh lab drops it, and the next persona gets another.
        assert _continue_as(one, "Rob (reviewer)").status_code == 303
        assert _lab_of(labs, one.get("/api/me").json()["id"]) == lab_one
        one.post("/demo/fresh-lab", data={"csrf_token": csrf_from(one.get("/demo/personas").text)})
        assert lab_one not in _schemas(labs.url)
        assert one.get("/", follow_redirects=False).status_code == 303  # signed out with it
        _continue_as(one)
        assert _lab_of(labs, one.get("/api/me").json()["id"]) not in {lab_one, lab_two, "lab_shared"}


def test_a_lab_is_audited_and_public_stays_empty(demo):
    shared, labs = demo
    schema = labs.claim()
    engine = make_engine(with_schema(labs.url, schema), poolclass=NullPool)
    with engine.connect() as conn:
        triggers = conn.execute(
            text(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s AND t.tgname = 'zz_audit'"
            ),
            {"s": schema},
        ).scalar()
        logged = conn.execute(text("SELECT count(*) FROM audit_log")).scalar()
    engine.dispose()
    assert triggers > 50 and logged > 0
    public = make_engine(labs.url, poolclass=NullPool)
    assert inspect(public).get_table_names(schema="public") == ["demo_labs"]
    public.dispose()
    labs.drop(schema)
