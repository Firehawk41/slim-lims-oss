"""slim-quality's batch 5c in the app (slim-data D-134…D-137): risks, internal audits with an independent
auditor, readiness per clause, the management review pack and the software validation pack."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from slim_quality.domain.clauses import CLAUSES
from slim_quality.domain.internal_audits import AuditState, LineOutcome
from slim_quality.domain.nonconformity import NcSource
from slim_quality.persistence import store
from slim_quality.service import internal_audits as ia

from slim_lims.labtime import LabClock
from slim_lims.main import create_app
from slim_lims.quality_system import (
    MODULES,
    NEVER_AUDITED,
    installed_versions,
    involved_in_clause,
    readiness_facts,
    seed_demo_quality_system,
    validation_pack,
)
from slim_lims.reports import plain_renderer
from tests.conftest import make_settings
from tests.test_demo_scenarios import reference, specs  # noqa: F401 (fixture)
from tests.test_quality import persona
from tests.test_quality_audit import issued  # noqa: F401 (fixture)
from tests.test_quality_records import as_persona

CLOCK = LabClock(__import__("zoneinfo").ZoneInfo("America/Chicago"))
TODAY = CLOCK.today()


@pytest.fixture
def seeded(db, issued):  # noqa: F811
    count = seed_demo_quality_system(db, TODAY)
    db.commit()
    return count


@pytest.fixture
def visitor(engine, reference, seeded):  # noqa: F811
    settings = make_settings(str(engine.url), demo_personas=True)
    app = create_app(
        settings, engine=engine, reference=reference, specs=specs, report_renderer=plain_renderer
    )
    with TestClient(app) as client:
        yield client


def test_the_demo_risks_and_programme_are_seeded_once(db, seeded):
    assert seeded == 8 + 24 and seed_demo_quality_system(db, TODAY) == 0
    assert len(store.list_risks(db)) == 4
    plans = store.list_internal_audits(db)
    upcoming = [a for a in plans if a.state == AuditState.PLANNED]
    assert sorted(a.clause for a in upcoming) == ["6.2", "6.4", "8.5"]
    assert {a.clause for a in plans} == set(CLAUSES) - set(NEVER_AUDITED)
    reporting = next(a for a in plans if a.clause == "7.8" and a.lines_of(LineOutcome.FINDING))
    assert reporting.state == AuditState.CONDUCTED and list(ia.findings_without_nc(db)) == ["7.8"]


def test_whoever_signed_or_released_in_a_clause_does_not_audit_it(db, issued, reference):  # noqa: F811
    rob = str(persona(db, "rob@demo.example").id)
    assert rob in involved_in_clause(db, reference, CLOCK, "7.8", TODAY)
    assert involved_in_clause(db, reference, CLOCK, "7.8", TODAY + timedelta(days=400)) == frozenset()
    assert involved_in_clause(db, reference, CLOCK, "8.5", TODAY) == frozenset()


def test_risk_pages_register_record_and_raise_an_nc(visitor, db):
    token = as_persona(visitor, db, "uma@demo.example")
    page = visitor.get("/quality/risks")
    assert page.status_code == 200 and "R-001" in page.text and "high, with no action planned" in page.text
    form = {
        "csrf_token": token,
        "title": "Argon supply runs out",
        "source": "1",
        "owner": "Sue",
        "likelihood": "2",
        "severity": "3",
        "review_by": (TODAY + timedelta(days=30)).isoformat(),
    }
    made = visitor.post("/quality/risks", data=form)
    assert made.status_code == 200 and "R-005 Argon supply runs out registered" in made.text
    assert visitor.post("/quality/risks", data=form | {"likelihood": "9"}).status_code == 422
    assert visitor.post("/quality/risks", data=form | {"source": "99"}).status_code == 422
    opportunity = visitor.post("/quality/risks", data=form | {"kind": "2", "title": "A new customer"})
    assert opportunity.status_code == 200 and "O-006" in opportunity.text
    risk = store.list_risks(db)[4]
    db.rollback()
    url = f"/quality/risks/{risk.id}"
    plan = {"csrf_token": token, "kind": "2", "action": "Second supplier", "owner": "Sue"}
    assert "action planned recorded" in visitor.post(f"{url}/entries", data=plan).text
    assert (
        "assessed recorded"
        in visitor.post(
            f"{url}/entries", data={"csrf_token": token, "kind": "1", "likelihood": "3", "severity": "4"}
        ).text
    )
    done = {"csrf_token": token, "kind": "3", "action_no": "1", "note": "signed"}
    assert "action done recorded" in visitor.post(f"{url}/entries", data=done).text
    assert visitor.post(f"{url}/entries", data=done).status_code == 409
    closing = {"csrf_token": token, "kind": "5", "note": "gone"}
    assert visitor.post(f"{url}/entries", data=closing).status_code == 409  # Uma isn't a manager
    assert visitor.post(f"{url}/entries", data={"csrf_token": token, "kind": "9"}).status_code == 422
    nc = visitor.post(f"{url}/nc", data={"csrf_token": token})
    assert nc.status_code == 200 and "R-005 happened" in nc.text
    assert store.list_ncs(db)[0].source == NcSource.RISK
    db.rollback()
    assert visitor.get("/quality/risks/999").status_code == 404
    assert "Show closed" in visitor.get("/quality/risks").text
    assert visitor.get("/quality/risks?closed=1").status_code == 200


def test_the_programme_refuses_an_auditor_of_their_own_work_and_records_a_checklist(visitor, db):
    token = as_persona(visitor, db, "quinn@demo.example")
    page = visitor.get("/quality/internal-audits")
    assert page.status_code == 200 and "7.8 Reporting of results (issued reports)" in page.text
    assert (
        "Not yet in the" in page.text
        and visitor.get(f"/quality/internal-audits?year={TODAY.year - 1}").status_code == 200
    )
    rob, sue = str(persona(db, "rob@demo.example").id), str(persona(db, "sue@demo.example").id)
    db.rollback()
    form = {"csrf_token": token, "clause": "7.8", "planned_for": TODAY.isoformat(), "auditor_id": rob}
    refused = visitor.post("/quality/internal-audits", data=form)
    assert refused.status_code == 409 and "doesn&#39;t audit their own work" in refused.text
    assert visitor.post("/quality/internal-audits", data=form | {"auditor_id": "x"}).status_code == 422
    assert visitor.post("/quality/internal-audits", data=form | {"clause": "9.9"}).status_code == 422
    made = visitor.post("/quality/internal-audits", data=form | {"clause": "7.3", "auditor_id": sue})
    assert made.status_code == 200 and "7.3 Sampling planned" in made.text
    audit = next(a for a in store.list_internal_audits(db) if a.clause == "7.3")
    db.rollback()
    url = f"/quality/internal-audits/{audit.id}"
    lines = {
        "line_clause": ["7.3", "7.3.1", "7.3"],
        "line_checked": ["Sampling plan", "Records", ""],
        "line_outcome": ["1", "3", "1"],
        "line_evidence": ["", "No plan for TR00003", ""],
    }
    record = {"csrf_token": token, "kind": "3", "on": TODAY.isoformat(), "note": "One finding"} | lines
    done = visitor.post(f"{url}/entries", data=record)
    assert done.status_code == 200 and "conducted recorded" in done.text and "Raise an NC" in done.text
    assert visitor.post(f"{url}/entries", data=record).status_code == 409
    nc = visitor.post(f"{url}/nc", data={"csrf_token": token, "line_no": "2"})
    assert nc.status_code == 200 and "finding: Records" in nc.text
    assert visitor.post(f"{url}/nc", data={"csrf_token": token, "line_no": "2"}).status_code == 409
    assert visitor.post(f"{url}/nc", data={"csrf_token": token, "line_no": "1"}).status_code == 404
    later = next(
        a for a in store.list_internal_audits(db) if a.clause == "8.5" and a.state == AuditState.PLANNED
    )
    db.rollback()
    moved = {"csrf_token": token, "kind": "1", "on": (TODAY + timedelta(days=60)).isoformat()}
    assert (
        "rescheduled recorded"
        in visitor.post(f"/quality/internal-audits/{later.id}/entries", data=moved).text
    )
    bad_line = {
        "csrf_token": token,
        "kind": "3",
        "on": TODAY.isoformat(),
        "note": "x",
        "line_clause": ["8.5"],
        "line_checked": ["Register"],
        "line_outcome": ["7"],
        "line_evidence": [""],
    }
    assert visitor.post(f"/quality/internal-audits/{later.id}/entries", data=bad_line).status_code == 422
    cancel = {"csrf_token": token, "kind": "2", "note": "merged into 8.8"}
    assert (
        "cancelled recorded" in visitor.post(f"/quality/internal-audits/{later.id}/entries", data=cancel).text
    )
    assert visitor.get("/quality/internal-audits/999").status_code == 404


def test_readiness_shows_every_clause_with_what_the_records_hold(visitor, db, reference):  # noqa: F811
    as_persona(visitor, db, "uma@demo.example")
    facts = readiness_facts(db, reference, CLOCK)
    db.rollback()
    assert facts.risks_open == 4 and facts.competence_findings is not None and facts.validation is None
    page = visitor.get("/quality/readiness")
    assert page.status_code == 200 and "8.9</strong> Management reviews" in page.text
    assert (
        "no management review signed" in page.text
        and "the software validation pack was never signed" in page.text
    )
    assert "1 audit finding with no NC raised" in page.text
    home = visitor.get("/quality")
    assert "readiness per clause" in home.text and "1 overdue" in home.text


def test_the_management_review_is_signed_by_a_manager_and_frozen(visitor, db):
    token = as_persona(visitor, db, "uma@demo.example")
    page = visitor.get("/quality/management-review")
    assert page.status_code == 200 and "(e) Outcome of recent internal audits" in page.text
    assert "A quality manager signs the review" in page.text
    start = (TODAY - timedelta(days=365)).isoformat()
    form = {
        "csrf_token": token,
        "start": start,
        "end": TODAY.isoformat(),
        "input_a": "A new customer",
        "output_kind": ["3", "1"],
        "output_text": ["A second ICP-MS", ""],
        "output_owner": ["Sue", ""],
        "output_due": [(TODAY + timedelta(days=90)).isoformat(), ""],
    }
    assert visitor.post("/quality/management-review", data=form).status_code == 409
    token = as_persona(visitor, db, "quinn@demo.example")
    form["csrf_token"] = token
    assert (
        visitor.post("/quality/management-review", data=form | {"output_text": ["", ""]}).status_code == 409
    )
    assert (
        visitor.post("/quality/management-review", data=form | {"output_kind": ["9", "1"]}).status_code == 422
    )
    assert (
        visitor.post(
            "/quality/management-review", data=form | {"start": "2027-01-01", "end": "2026-01-01"}
        ).status_code
        == 422
    )
    signed = visitor.post("/quality/management-review", data=form)
    assert signed.status_code == 200 and "MR-001 signed" in signed.text and "A second ICP-MS" in signed.text
    assert "A new customer" in signed.text and "As signed" in signed.text
    review = store.management_reviews(db)[0]
    db.rollback()
    assert review.pack.period_from == date.fromisoformat(start) and len(review.pack.readiness) == 27
    again = visitor.get("/quality/management-review").text
    assert "MR-001" in again and "Resources needed: A second ICP-MS (Sue, due" in again
    assert visitor.get("/quality/management-review/999").status_code == 404
    assert visitor.get("/quality/management-review?start=nope").status_code == 422
    assert "last management review" in visitor.get("/quality/readiness").text


def test_the_validation_pack_lists_the_installed_modules_and_is_signed_for_them(visitor, db):
    versions = installed_versions()
    assert set(versions) == set(MODULES)
    pack = validation_pack(db, CLOCK)
    db.rollback()
    assert (
        pack["versions"] == versions
        and "cup_shape@1" in pack["steps"]
        and "westgard@1" in pack["quality_tools"]
    )
    assert "alembic_version_slim_quality" in pack["migrations"] and pack["verify"]["slim-quality"]["ok"]
    token = as_persona(visitor, db, "uma@demo.example")
    page = visitor.get("/quality/validation")
    assert page.status_code == 200 and "never signed" in page.text and "slim-quality" in page.text
    download = visitor.get("/quality/validation.json")
    assert (
        download.headers["content-disposition"].startswith("attachment")
        and download.json()["versions"] == versions
    )
    assert (
        visitor.post("/quality/validation", data={"csrf_token": token, "note": "CI green"}).status_code == 409
    )
    token = as_persona(visitor, db, "quinn@demo.example")
    signed = visitor.post("/quality/validation", data={"csrf_token": token, "note": "CI green; verify holds"})
    assert signed.status_code == 200 and "signed</span> for these versions" in signed.text
    assert store.validation_signatures(db)[0].versions == versions
    db.rollback()
    assert "signed 20" in visitor.get("/quality/readiness").text


def test_the_5c_pages_need_a_quality_capability(visitor, db):
    as_persona(visitor, db, "ana@demo.example")
    for path in (
        "/quality/risks",
        "/quality/internal-audits",
        "/quality/readiness",
        "/quality/management-review",
        "/quality/validation",
        "/quality/validation.json",
    ):
        assert visitor.get(path).status_code == 403, path


def test_q2_a_spec_approver_doesnt_audit_reporting(db, issued, reference):  # noqa: F811
    """Review Q-2: spec limits decide the report's statements of conformity (§7.8.6): whoever proposed or
    decided a change in the year is involved in 7.8, and in no lab clause besides."""
    from slim_lims.models import SpecDecisionRow, SpecProposalRow

    uma, sue = persona(db, "uma@demo.example"), persona(db, "sue@demo.example")
    proposal = SpecProposalRow(
        customer_id=2, chemical_id=2, location="", kind="specification", unit="ppb", before={},
        after={"Fe": "0.3"}, reason="rev. C", proposed_by=uma.email, proposed_by_id=str(uma.id),
    )  # fmt: skip
    db.add(proposal)
    db.flush()
    db.add(SpecDecisionRow(proposal_id=proposal.id, outcome="approved", by=sue.email))
    db.commit()
    involved = involved_in_clause(db, reference, CLOCK, "7.8", TODAY)
    assert {str(uma.id), str(sue.id)} <= involved
    assert str(uma.id) not in involved_in_clause(db, reference, CLOCK, "7.2", TODAY)
    assert involved_in_clause(db, reference, CLOCK, "7.8", TODAY + timedelta(days=400)) == frozenset()
