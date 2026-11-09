"""Reference data upkeep (slim-data D-141): customers added and edited and chemicals added through
slim-domain's own services (on its own tables), the analysis catalog edited, every change kept with its
reason; a catalog sync keeps what the app edited; each visitor's lab has its own reference cache."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from slim_lims import capabilities, refdata
from slim_lims.catalog import sync_analysis_catalog
from slim_lims.domain import RequestType
from slim_lims.labs import Labs
from slim_lims.models import LAB_ROLE_ANALYST, ROLE_STAFF, AnalysisCatalogRow, ReferenceChangeRow, User
from slim_lims.security import hash_password
from tests.conftest import PASSWORD, csrf_from, login
from tests.slim_fixtures import create_slim_tables

CUSTOMER = {
    "name": "New Co",
    "street_address": "9 Main St",
    "city": "Anytown",
    "state": "TX",
    "postal_code": "75001",
    "country": "USA",
}


@pytest.fixture
def slim_domain_tables(engine, db):
    """slim-domain's own customer and chemical tables (what its services write), with one customer."""
    import slim_domain.domain.chemical.chemical_repository  # noqa: F401 (registers the tables)
    import slim_domain.domain.customer.customer_repository  # noqa: F401
    import slim_domain.domain.element.element_repository  # noqa: F401
    from infrastructure.database import Base

    if engine.dialect.name == "postgresql":  # slim-domain's unique names are CITEXT there (as the seed does)
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(  # no explicit ID: on Postgres the SERIAL counter must stay in step
            text(
                "INSERT INTO customers (customer_name, street_address, city, state, postal_code, country,"
                " additional_elements_prep, is_group) VALUES ('A', '1 A Street', 'Anytown', 'TX', '75000',"
                " 'USA', '', false)"
            )
        )
        conn.execute(
            text("INSERT INTO elements (\"ID\", element_symbol, element_name) VALUES (26, 'Fe', 'Iron')")
        )
    yield engine
    db.rollback()  # SQLite: no read open while the tables are dropped
    Base.metadata.drop_all(engine)


@pytest.fixture
def analyst(db):
    user = User(
        email="ana@lab.example",
        password_hash=hash_password(PASSWORD),
        role=ROLE_STAFF,
        initials="AN",
        lab_role=LAB_ROLE_ANALYST,
    )
    db.add(user)
    db.commit()
    return user


def form(client, url="/reference"):
    return csrf_from(client.get(url).text)


def test_staff_with_the_capability_add_and_edit_customers_through_slim_domain(
    client, db, staff_user, slim_domain_tables
):
    login(client, "staff@lab.example")
    csrf = form(client)
    client.post("/reference/customers", data={**CUSTOMER, "reason": "", "csrf_token": csrf})  # no reason
    client.post("/reference/customers", data={**CUSTOMER, "city": " ", "reason": "new", "csrf_token": csrf})
    db.rollback()
    assert refdata.changes(db) == []
    db.rollback()
    page = client.post(
        "/reference/customers", data={**CUSTOMER, "reason": "first request coming", "csrf_token": csrf},
        follow_redirects=True,
    )  # fmt: skip
    assert "Customer New Co added." in page.text
    dup = client.post(
        "/reference/customers", data={**CUSTOMER, "name": "new co", "reason": "again", "csrf_token": csrf},
        follow_redirects=True,
    )  # fmt: skip
    assert "already exists" in dup.text  # slim-domain's own check, case-insensitive
    db.rollback()
    with refdata.slim_session(slim_domain_tables) as slim:
        made = next(c for c in refdata.customers(slim) if c.name == "New Co")
    [added] = refdata.changes(db, refdata.CUSTOMER)
    assert (added.ref_id, added.action, added.before, added.reason) == (
        made.id,
        "added",
        None,
        "first request coming",
    )
    db.rollback()

    edit = client.get(f"/reference/customers/{made.id}")
    assert 'value="9 Main St"' in edit.text
    client.post(
        f"/reference/customers/{made.id}", data={**CUSTOMER, "reason": "no change", "csrf_token": csrf}
    )  # nothing changed: refused, nothing kept
    saved = client.post(
        f"/reference/customers/{made.id}",
        data={**CUSTOMER, "street_address": "10 Main St", "reason": "they moved", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert "keep the name and address they had" in saved.text and "9 Main St → 10 Main St" in saved.text
    db.rollback()
    with refdata.slim_session(slim_domain_tables) as slim:
        assert refdata.customer(slim, made.id).street_address == "10 Main St"
    edited = refdata.changes(db, refdata.CUSTOMER, made.id)[0]
    assert edited.before["street_address"] == "9 Main St" and edited.after["street_address"] == "10 Main St"
    assert len(refdata.changes(db)) == 2
    db.rollback()
    assert client.get("/reference/customers/999").status_code == 404


def test_chemicals_are_added_with_slim_domains_prep_lists(client, db, staff_user, slim_domain_tables):
    login(client, "staff@lab.example")
    csrf = form(client)
    good = {
        "name": "Solvent 9",
        "metals_prep": "Evaporation",
        "silicon_prep": "N/A",
        "ions_prep": "Dilute and Shoot",
        "metals_instrument": "ICPOES",
        "ked": ["26", "999"],
        "reason": "new product",
        "csrf_token": csrf,
    }
    refused = client.post(
        "/reference/chemicals", data={**good, "metals_prep": "Boiling"}, follow_redirects=True
    )
    assert "Chemical not added" in refused.text
    client.post("/reference/chemicals", data={**good, "metals_instrument": "XRF"})
    page = client.post("/reference/chemicals", data=good, follow_redirects=True)
    assert "Chemical Solvent 9 added" in page.text
    db.rollback()
    with refdata.slim_session(slim_domain_tables) as slim:
        [chem] = refdata.chemicals(slim)
    assert (chem.metals_prep, chem.metals_instrument, chem.ked_element_ids) == ("Evaporation", "ICPOES", {26})
    [change] = refdata.changes(db, refdata.CHEMICAL)
    assert change.after["ked_element_ids"] == [26] and change.by == "staff@lab.example"


def test_the_catalog_is_edited_with_a_reason_and_a_sync_keeps_it(engine, client, db, staff_user, tmp_path):
    create_slim_tables(engine)
    sync_analysis_catalog(engine, db)
    db.commit()
    ph = db.get(AnalysisCatalogRow, 2)
    assert ph.portal_selectable
    db.rollback()
    login(client, "staff@lab.example")
    csrf = form(client, "/reference/analyses/2")
    body = {
        "group_name": "Physical",
        "request_types": ["2"],
        "reason": "not offered for now",
        "csrf_token": csrf,
    }
    client.post("/reference/analyses/2", data={**body, "portal_selectable": "1", "request_types": []})
    db.expire_all()
    assert db.get(AnalysisCatalogRow, 2).portal_selectable  # offered with no request type: refused
    db.rollback()
    saved = client.post("/reference/analyses/2", data=body, follow_redirects=True)
    assert "Saved" in saved.text and "portal_selectable: True → False" in saved.text
    db.expire_all()
    row = db.get(AnalysisCatalogRow, 2)
    assert (row.portal_selectable, row.request_types) == (False, "2")
    sync_analysis_catalog(engine, db)  # the CSV says it's offered; the app's edit stands
    db.commit()
    db.expire_all()
    row = db.get(AnalysisCatalogRow, 2)
    assert (row.portal_selectable, row.request_types) == (False, "2")
    assert db.get(AnalysisCatalogRow, 1).portal_selectable  # untouched rows follow the CSV
    db.rollback()
    assert client.get("/reference/analyses/999").status_code == 404
    with pytest.raises(refdata.RefRefused, match="Nothing changed"):
        refdata.edit_catalog(
            db, 2, refdata.CatalogEdit("Physical", frozenset({RequestType(2)}), False), "x", staff_user
        )


def test_only_the_capability_edits_reference_data(client, db, analyst, customer_user):
    login(client, "ana@lab.example")
    assert client.get("/reference").status_code == 403
    csrf = csrf_from(client.get("/").text)
    assert (
        client.post("/reference/customers", data={**CUSTOMER, "reason": "x", "csrf_token": csrf}).status_code
        == 403
    )
    client.cookies.clear()
    login(client, "jane@acme.example")
    assert client.get("/reference").status_code == 403


def test_supervisors_hold_it_and_the_record_is_append_only(engine, db, staff_user):
    assert capabilities.can(db, staff_user, capabilities.REFERENCE_DATA)
    db.add(ReferenceChangeRow(kind="customer", ref_id=1, action="added", after={}, reason="x", by="x"))
    db.commit()
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(update(ReferenceChangeRow).values(reason="y"))
    assert db.scalars(select(ReferenceChangeRow.reason)).all() == ["x"]


def test_each_visitors_lab_has_its_own_reference_cache(engine):
    """D-141: a lab's SLIM tables are its own, so are its cached copy; dropped with the lab."""
    labs = Labs.__new__(Labs)
    labs._engines, labs._references, labs._factories, labs._seen = {"lab_a": engine}, {}, {}, {}
    import threading

    labs._lock = threading.Lock()
    mine = labs.reference_for("lab_a", ttl_seconds=60)
    assert mine is labs.reference_for("lab_a") and labs.reference_for("lab_b") is None
    assert labs.engine_for("lab_a") is engine and labs.engine_for("lab_b") is None
    assert timedelta(seconds=mine._ttl) == timedelta(seconds=60)


def test_l2_a_visitors_writes_never_fall_back_to_the_shared_lab():
    """The visitor's lab is entered if no dependency did yet; one that has gone fails closed (409)."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from slim_lims.api import slim_engine_for, specs_for

    lab_engine, shared = object(), object()

    class FakeLabs:
        def __init__(self, alive):
            self.alive, self.engines = alive, {}

        def engine_for(self, schema):
            return self.engines.get(schema)

        def enter(self, schema):
            if not self.alive:
                return None
            self.engines[schema] = lab_engine
            return object()

    def request(labs, lab="lab_abc"):
        state = SimpleNamespace(labs=labs, slim_engine=shared, specs="the app's reader")
        return SimpleNamespace(app=SimpleNamespace(state=state), session={"lab": lab} if lab else {})

    assert slim_engine_for(request(FakeLabs(alive=True))) is lab_engine  # entered here, not the shared lab
    assert slim_engine_for(request(FakeLabs(alive=True), lab=None)) is shared  # a password login: SLIM's
    for gone in (slim_engine_for, specs_for):
        with pytest.raises(HTTPException) as refused:
            gone(request(FakeLabs(alive=False)))
        assert refused.value.status_code == 409
