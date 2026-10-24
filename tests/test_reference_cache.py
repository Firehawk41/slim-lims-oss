import pytest
from sqlalchemy import delete

from slim_lab_portal import slim_tables as t
from slim_lab_portal.catalog import CATALOG_CSV, read_catalog, sync_analysis_catalog
from slim_lab_portal.domain import RequestType
from slim_lab_portal.reference import StaticReferenceData
from slim_lab_portal.reference_cache import ReferenceCache, load_reference
from tests.slim_fixtures import create_slim_tables


@pytest.fixture
def slim(engine, db):
    create_slim_tables(engine)
    sync_analysis_catalog(engine, db)
    db.commit()
    return engine


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- catalog file and sync

def test_catalog_csv_is_consistent():
    entries = read_catalog(CATALOG_CSV)
    assert len({e.code for e in entries}) == len(entries)
    assert len({e.slim_name.lower() for e in entries}) == len(entries)
    assert all(e.request_types for e in entries)
    wafer = {e.slim_name for e in entries if RequestType.WAFER in e.request_types and e.portal_selectable}
    # The report engine's Wafer allow-list (wafer_submission_builder._RECOGNIZED_SELECTIONS).
    assert wafer == {"10 Elements", "26 Elements", "36 Elements", "67 Elements", "List #2 36 Elements",
                     "4 Anions", "5 Anions", "7 Anions"}


def test_sync_matches_names_case_insensitively(engine, db):
    create_slim_tables(engine)
    report = sync_analysis_catalog(engine, db)
    assert report.matched == 3  # 36 Elements, pH, and "additional element" (not selectable)
    assert report.not_in_catalog == ["brand new test"]
    assert "UV-Vis" in report.not_in_slim


def test_sync_replaces_previous_rows(engine, db):
    create_slim_tables(engine)
    sync_analysis_catalog(engine, db)
    db.commit()
    with engine.begin() as conn:
        conn.execute(delete(t.analyses).where(t.analyses.c.id == 2))
    assert sync_analysis_catalog(engine, db).matched == 2


# ---------------------------------------------------------------- loading

def test_load_reference(slim):
    data = load_reference(slim, slim)
    assert data.customer(1).address_line_2 == "Anytown, TX 75000, USA"
    assert [c.name for c in data.customer_choices()] == ["A"]  # group G excluded
    assert [c.name for c in data.chemical_choices()] == ["Chemical 01"]  # Water excluded
    assert data.chemical_by_name(" water ").id == 1
    metals = data.analysis(1)
    assert (metals.code, metals.group, metals.description) == ("36_elements_icpms", "Metals", "ICP-MS panel")
    assert metals.request_types == frozenset(RequestType)
    assert [a.name for a in data.analyses_for(RequestType.WAFER)] == ["36 Elements"]
    assert [a.name for a in data.analyses_for(RequestType.WATER)] == ["36 Elements", "pH"]


def test_analysis_without_catalog_entry_is_never_offered(slim):
    data = load_reference(slim, slim)
    new = data.analysis(3)
    assert new.name == "Brand New Test" and not new.portal_selectable
    assert not data.analysis(4).portal_selectable  # Additional Element: element picker instead
    assert all(a.id not in (3, 4) for rt in RequestType for a in data.analyses_for(rt))


# ---------------------------------------------------------------- cache behaviour

def test_cache_loads_lazily_and_refreshes_after_ttl(slim):
    clock = FakeClock()
    calls = []

    def loader(a, b):
        calls.append(1)
        return load_reference(a, b)

    cache = ReferenceCache(slim, ttl_seconds=300, clock=clock, loader=loader)
    assert calls == []
    assert cache.customer(1).name == "A"
    cache.chemical(2)
    assert len(calls) == 1
    clock.now += 299
    cache.customer(1)
    assert len(calls) == 1
    clock.now += 2
    cache.customer(1)
    assert len(calls) == 2


def test_cache_picks_up_changes_on_refresh(slim):
    cache = ReferenceCache(slim)
    assert cache.chemical(3) is None
    with slim.begin() as conn:
        conn.execute(t.chemicals.insert().values(id=3, chemical_name="Chemical 02"))
    assert cache.chemical(3) is None  # still within the TTL
    assert cache.refresh() is True
    assert cache.chemical(3).name == "Chemical 02"


def test_failed_refresh_keeps_last_good_copy_and_backs_off(slim):
    clock = FakeClock()
    state = {"fail": False, "calls": 0}

    def loader(a, b):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("database is down")
        return load_reference(a, b)

    cache = ReferenceCache(slim, ttl_seconds=300, retry_seconds=30, clock=clock, loader=loader)
    assert cache.customer(1).name == "A"
    state["fail"] = True
    clock.now += 301
    assert cache.customer(1).name == "A"  # served from the previous snapshot
    status = cache.status()
    assert status.last_error == "RuntimeError: database is down"
    assert not status.healthy
    calls = state["calls"]
    clock.now += 10
    cache.customer(1)
    assert state["calls"] == calls  # waits retry_seconds before trying again
    state["fail"] = False
    clock.now += 21
    cache.customer(1)
    assert cache.status().healthy


def test_first_load_failure_serves_empty_data(engine):
    cache = ReferenceCache(engine)  # no SLIM tables at all
    assert cache.customer(1) is None
    assert cache.status().loaded_at is None
    assert "customers" in cache.status().last_error


def test_static_and_cache_expose_same_interface():
    public = {name for name in dir(StaticReferenceData) if not name.startswith("_") and name != "of"}
    fields = {"customers", "chemicals", "elements", "analyses", "customer_locations"}
    missing = [name for name in public - fields if not hasattr(ReferenceCache, name)]
    assert missing == []
