"""slim-quality's batch 5b in the app (slim-data D-132, D-133): the acts the modules recorded checked against
the authorisation register, and the request dates the trends read. One test per rule of the brief."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from slim_quality.domain.competence import Act
from slim_quality.domain.nonconformity import NcSource
from slim_quality.persistence import store
from slim_quality.service import competence

from slim_lims.domain import TRStatus
from slim_lims.labtime import LabClock
from slim_lims.main import create_app
from slim_lims.models import TRSubmissionRow
from slim_lims.quality_insights import DEMO_AUTHORISATIONS, TrDates, lab_acts, seed_demo_authorisations
from slim_lims.reports import plain_renderer
from tests.conftest import make_settings
from tests.test_demo_prep import prepped  # noqa: F401 (fixture)
from tests.test_demo_scenarios import reference, specs  # noqa: F401 (fixture)
from tests.test_quality import persona
from tests.test_quality_audit import issued  # noqa: F401 (fixture)
from tests.test_quality_records import as_persona

TODAY = date.today()
CLOCK = LabClock(__import__("zoneinfo").ZoneInfo("America/Chicago"))


@pytest.fixture
def authorised(db, issued):  # noqa: F811
    count = seed_demo_authorisations(db, CLOCK.today())
    db.commit()
    return count


@pytest.fixture
def visitor(engine, reference, authorised):  # noqa: F811
    settings = make_settings(str(engine.url), demo_personas=True)
    app = create_app(
        settings, engine=engine, reference=reference, specs=specs, report_renderer=plain_renderer
    )
    with TestClient(app) as client:
        yield client


def findings(db, reference):  # noqa: F811
    return competence.findings(db, lab_acts(db, reference, CLOCK), day_of=lambda at: CLOCK.local(at).date())


def test_the_demo_register_is_seeded_once_and_finds_robs_signatures(db, authorised, reference):  # noqa: F811
    assert authorised == sum(len(acts) for acts in DEMO_AUTHORISATIONS.values())
    assert seed_demo_authorisations(db, TODAY) == 0
    acts = lab_acts(db, reference, CLOCK)
    assert {a.act for a in acts} >= {Act.ENTER, Act.REVIEW, Act.RELEASE, Act.SIGN}
    rob = str(persona(db, "rob@demo.example").id)
    found = findings(db, reference)
    assert found and all(f.act.act == Act.SIGN and f.act.person.id == rob for f in found)
    assert found[0].act.person.display == "Rob Example"  # named as he signs
    assert found[0].reason == "Rob Example holds no authorisation to sign reports"


def test_prep_batches_are_acts_with_their_analysis(db, prepped, reference):  # noqa: F811
    preps = [a for a in lab_acts(db, reference, CLOCK) if a.act == Act.PREP]
    assert preps and all(a.method and a.ref.startswith("prep:") for a in preps)
    assert all(a.person.display == "Ana Example" for a in preps)


def test_the_competence_page_raises_grants_and_withdraws(visitor, db, reference):  # noqa: F811
    token = as_persona(visitor, db, "uma@demo.example")
    page = visitor.get("/quality/competence").text
    assert "holds no authorisation to sign reports" in page and "Authorise</button>" not in page
    ref = findings(db, reference)[0].act.ref
    db.rollback()
    nc = visitor.post("/quality/competence/nc", data={"csrf_token": token, "ref": ref})
    assert nc.status_code == 200 and "sign reports outside authorisation" in nc.text
    assert store.list_ncs(db)[0].source == NcSource.COMPETENCE
    db.rollback()
    rob = str(persona(db, "rob@demo.example").id)
    db.rollback()
    start = (TODAY - timedelta(days=365)).isoformat()
    form = {"csrf_token": token, "person_id": rob, "act": "5", "method": "", "valid_from": start,
            "evidence": "Signatory training 2025"}  # fmt: skip
    r = visitor.post("/quality/competence", data=form)
    assert r.status_code == 409, r.text[-600:]  # Uma isn't a manager
    token = as_persona(visitor, db, "quinn@demo.example")
    granted = visitor.post("/quality/competence", data=form | {"csrf_token": token})
    assert granted.status_code == 200 and "Authorised: Rob Example: Sign reports" in granted.text
    assert "Every recorded act was authorised" in granted.text
    assert visitor.post("/quality/competence/nc", data={"csrf_token": token, "ref": ref}).status_code == 409
    signing = next(a for a in competence.register(db) if a.act == Act.SIGN and a.person.id == rob)
    db.rollback()
    gone = visitor.post(
        f"/quality/competence/{signing.id}/entries", data={"csrf_token": token, "kind": "1", "note": "left"}
    )
    assert gone.status_code == 200 and "withdrawn recorded" in gone.text
    again = visitor.post(
        f"/quality/competence/{signing.id}/entries", data={"csrf_token": token, "kind": "1", "note": "x"}
    )
    assert again.status_code == 409
    bad = {"csrf_token": token, "kind": "9", "note": "x"}
    assert visitor.post(f"/quality/competence/{signing.id}/entries", data=bad).status_code == 422
    assert (
        visitor.post("/quality/competence", data=form | {"csrf_token": token, "person_id": "x"}).status_code
        == 422
    )
    assert (
        visitor.post("/quality/competence", data=form | {"csrf_token": token, "act": "9"}).status_code == 422
    )
    themselves = form | {"csrf_token": token, "person_id": str(persona(db, "quinn@demo.example").id)}
    db.rollback()
    assert visitor.post("/quality/competence", data=themselves).status_code == 409


def test_request_dates_follow_receipt_and_the_report_statuses(db, issued, reference):  # noqa: F811
    found = {r.tr_number: r for r in TrDates(db, reference, CLOCK).requests()}
    received = db.query(TRSubmissionRow).filter(TRSubmissionRow.date_received.isnot(None)).all()
    assert set(found) == {row.tr_number for row in received if row.status != TRStatus.CANCELLED}
    complete = found[issued["clean_pass"]]
    assert complete.complete_report is not None and complete.first_report is not None
    assert complete.due >= complete.received and complete.customer and complete.analyses
    assert TrDates(db, reference, CLOCK).requests(since=TODAY + timedelta(days=30)) == []


def test_the_trends_page_shows_turnaround_and_customers(visitor, db):
    as_persona(visitor, db, "uma@demo.example")
    page = visitor.get("/quality/trends")
    assert (
        page.status_code == 200 and "Turnaround and customers" in page.text and "Customer trends" in page.text
    )
    assert visitor.get("/quality/trends?since=2020-01-01").status_code == 200
    assert visitor.get("/quality/trends?since=nope").status_code == 422


def test_the_5b_pages_need_a_quality_capability(visitor, db):
    as_persona(visitor, db, "ana@demo.example")
    for path in ("/quality/uncertainty", "/quality/competence", "/quality/trends"):
        assert visitor.get(path).status_code == 403
