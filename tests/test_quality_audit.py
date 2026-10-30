"""The vertical audit (slim-data D-113) and nonconformities (D-114) in the app, on the demo scenarios'
issued reports."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from slim_prep.persistence import migrate as prep_migrate
from slim_quality.domain.nonconformity import NcState
from slim_quality.persistence import store
from sqlalchemy import text

from slim_lims.demo_scenarios import seed_scenarios
from slim_lims.main import create_app
from slim_lims.personas import sync_personas
from slim_lims.quality import seed_demo_nonconformity
from slim_lims.reports import plain_renderer
from slim_lims.results import publish_demo_rules
from tests.conftest import make_settings
from tests.test_demo_scenarios import reference, specs  # noqa: F401 (fixture)
from tests.test_quality import continue_as, token_of


@pytest.fixture
def issued(engine, db, reference):  # noqa: F811
    with engine.begin() as connection:  # the audit reads the prep book too
        prep_migrate.downgrade(connection)
        prep_migrate.upgrade(connection)
    publish_demo_rules(db, reference)
    sync_personas(db)
    trs = seed_scenarios(db, reference, specs, renderer=plain_renderer)
    db.commit()
    return trs


@pytest.fixture
def visitor(engine, reference, issued):  # noqa: F811
    settings = make_settings(str(engine.url), demo_personas=True)
    app = create_app(
        settings, engine=engine, reference=reference, specs=specs, report_renderer=plain_renderer
    )
    with TestClient(app) as client:
        yield client


def as_persona(client, db, email) -> str:
    """Continues as a persona; returns the CSRF token of the new session (switching rotates it)."""
    continue_as(client, db, email)
    return token_of(client.get("/demo/personas").text)


def first_issue(client) -> str:
    page = client.get("/quality/audit?start=2000-01-01").text
    return re.search(r'/quality/audit/(\d+)"', page).group(1)


def test_only_quality_staff_audit(visitor, db):
    continue_as(visitor, db, "rob@demo.example")
    assert visitor.get("/quality/audit").status_code == 403
    assert visitor.get("/quality/ncs").status_code == 403


def test_a_random_report_and_its_whole_trail(visitor, db):
    continue_as(visitor, db, "uma@demo.example")
    picked = visitor.get("/quality/audit/random?start=2000-01-01", follow_redirects=False)
    assert picked.status_code == 303 and "/quality/audit/" in picked.headers["location"]
    empty = visitor.get("/quality/audit/random?start=2000-01-01&end=2000-01-02")
    assert "No report was issued in that period" in empty.text
    assert visitor.get("/quality/audit?start=nope").status_code == 422
    page = visitor.get(f"/quality/audit/{first_issue(visitor)}").text
    for seen in (
        "Summary",
        "The report file matches its hash",
        "Every result replays exactly",
        "Readings the engine saw",
        "Sign-offs",
        "Runs",
        "as the charts stood then",
        "Record this audit",
    ):
        assert seen in page, seen
    assert 'sev--ACTION">✗' not in page  # the demo's first report is sound
    link = re.search(r'href="[^"]*(/results/runs/\d+/file)"', page).group(1)
    raw = visitor.get(link)
    assert (
        raw.status_code == 200
        and raw.headers["content-disposition"].startswith("attachment;")
        and raw.content
    )
    assert visitor.get("/results/runs/99999/file").status_code == 404
    assert visitor.get("/quality/audit/99999").status_code == 404


def test_a_report_file_changed_after_issue_fails_its_check(visitor, db):
    issue = first_issue(visitor) if continue_as(visitor, db, "uma@demo.example") else None
    db.execute(text("UPDATE report_files SET html = html || ' '"))  # what an audit must catch
    db.commit()
    page = visitor.get(f"/quality/audit/{issue}").text
    row = page.split("The report file matches its hash")[0].rsplit("<tr>", 1)[1]
    assert "✗" in row


def test_recording_an_audit_and_raising_its_findings(visitor, db):
    token = as_persona(visitor, db, "uma@demo.example")
    issue = first_issue(visitor)
    ok = visitor.post(f"/quality/audit/{issue}", data={"csrf_token": token, "outcome": "1"})
    assert "Audit recorded: conforming" in ok.text
    assert (
        visitor.post(f"/quality/audit/{issue}", data={"csrf_token": token, "outcome": "2"}).status_code == 422
    )
    found = visitor.post(
        f"/quality/audit/{issue}", data={"csrf_token": token, "outcome": "2", "findings": "CCV lot missing"}
    )
    assert (
        "CCV lot missing" in found.text and "audit:" in found.text and "Raise a nonconformity" in found.text
    )
    assert visitor.post("/quality/audit/99999", data={"csrf_token": token, "outcome": "1"}).status_code == 404
    db.rollback()
    assert [a.outcome.name for a in store.audits(db)] == ["FINDINGS", "CONFORMING"]


def test_a_nonconformity_from_raised_to_closed(visitor, db):
    token = as_persona(visitor, db, "uma@demo.example")
    today = datetime.now(UTC).date()
    raised = visitor.post(
        "/quality/ncs",
        data={
            "csrf_token": token,
            "title": "Cones worn",
            "description": "ISTD low",
            "instrument": "ICP-MS 1",
            "start": str(today - timedelta(days=30)),
            "end": str(today),
        },
        follow_redirects=False,
    )
    assert raised.status_code == 303
    url = raised.headers["location"]
    nc_id = url.rsplit("/", 1)[1]
    page = visitor.get(url).text
    refs = re.findall(r'name="impact_ref" value="([^"]+)"', page)
    assert (
        refs == [] and "A quality manager records the assessment" in page
    )  # a quality user sees, can't assess
    entry = f"/quality/ncs/{nc_id}/entries"
    assert visitor.post(entry, data={"csrf_token": token, "kind": "1", "note": "x"}).status_code == 403
    assert (
        visitor.post(
            entry, data={"csrf_token": token, "kind": "2", "root_cause": "a", "action": "b", "owner": "QU"}
        ).status_code
        == 403
    )

    token = as_persona(visitor, db, "quinn@demo.example")
    page = visitor.get(url).text
    refs = re.findall(r'name="impact_ref" value="([^"]+)"', page)
    assert refs, "the scenarios' released results were measured on ICP-MS 1 in the window"
    partial = {
        "csrf_token": token,
        "kind": "1",
        "impact_ref": refs[:1],
        "impact_decision": ["1"],
        "impact_note": [""],
    }
    assert visitor.post(entry, data=partial).status_code == 422  # every touched result is decided
    forged = partial | {"impact_ref": ["TR99999-01|1|Cu|1"]}
    assert visitor.post(entry, data=forged).status_code == 422
    whole = {
        "csrf_token": token,
        "kind": "1",
        "impact_ref": refs,
        "impact_decision": ["2"] * len(refs),
        "impact_note": [""] * len(refs),
    }
    assert "assessed" in visitor.post(entry, data=whole).text.lower()
    visitor.post(
        entry,
        data={
            "csrf_token": token,
            "kind": "2",
            "root_cause": "worn cones",
            "action": "replaced",
            "owner": "QU",
            "due": str(today),
        },
    )
    assert "Flags on ICP-MS 1 since the action" in visitor.get(url).text
    visitor.post(entry, data={"csrf_token": token, "kind": "3", "effective": "1", "note": "clean since"})
    closed = visitor.post(entry, data={"csrf_token": token, "kind": "4", "note": "done"})
    assert "closed" in closed.text.lower()
    assert visitor.post(entry, data={"csrf_token": token, "kind": "5", "note": "late"}).status_code == 403
    db.rollback()
    nc = store.get_nc(db, int(nc_id))
    assert nc.state == NcState.CLOSED and len(nc.assessment.impact) == len(refs)
    assert all(line.label.startswith("TR") for line in nc.assessment.impact)  # built by the app, not the form


def test_raising_needs_a_whole_window_and_unknown_ncs_are_404(visitor, db):
    token = as_persona(visitor, db, "uma@demo.example")
    bad = {
        "csrf_token": token,
        "title": "x",
        "description": "y",
        "instrument": "ICP-MS 1",
        "start": "2026-10-10",
        "end": "2026-10-01",
    }
    assert visitor.post("/quality/ncs", data=bad).status_code == 422
    assert visitor.get("/quality/ncs/999").status_code == 404
    assert (
        visitor.post(
            "/quality/ncs/999/entries", data={"csrf_token": token, "kind": "5", "note": "x"}
        ).status_code
        == 404
    )


def test_the_demo_nonconformity_is_raised_once(db, issued):
    seed_demo_nonconformity(db, datetime.now(UTC))
    seed_demo_nonconformity(db, datetime.now(UTC))
    (nc,) = store.list_ncs(db)
    assert nc.state == NcState.OPEN and nc.instrument == "ICP-MS 1" and nc.analyte is None
