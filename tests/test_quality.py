"""slim-quality in the app (slim-data D-109): who may use it, the demo history, the pages."""

import re
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from slim_data.service.views import batches
from slim_quality.domain.enums import Measure, QualityRole
from slim_quality.persistence import store
from sqlalchemy import select

from slim_lims import capabilities
from slim_lims.domain import RequestType
from slim_lims.main import create_app
from slim_lims.models import LAB_ROLE_ANALYST, ROLE_STAFF, User, UserCapability
from slim_lims.personas import sync_personas
from slim_lims.quality import SlimDataQc, quality_actor, seed_quality_history
from slim_lims.reference import AnalysisRef, ChemicalRef, CustomerRef, ElementRef, StaticReferenceData
from slim_lims.reports import plain_renderer
from slim_lims.results import publish_demo_rules
from tests.conftest import make_settings

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
TEN = 1


@pytest.fixture
def reference():
    return StaticReferenceData.of(
        customers=[CustomerRef(1, "A", "1 A Street")],
        chemicals=[ChemicalRef(2, "IPA"), ChemicalRef(3, "HNO3")],
        elements=[ElementRef(i, s, s) for i, s in {26: "Fe", 30: "Zn", 11: "Na", 29: "Cu"}.items()],
        analyses=[
            AnalysisRef(id, name, code, "Metals", request_types=frozenset({RequestType.CHEMICAL}))
            for id, name, code in (
                (TEN, "10 Elements", "10_elements_icpms"),
                (3, "36 Elements", "36_elements_icpms"),
                (6, "5 Anions", "5_anions"),
                (17, "Assay", "assay"),
            )
        ],
    )


@pytest.fixture
def seeded(db, reference):
    publish_demo_rules(db, reference)
    sync_personas(db)
    db.commit()
    runs = seed_quality_history(db, reference, lambda *_: {}, NOW.astimezone(), days=30)
    db.commit()
    return runs


def persona(db, email):
    return db.scalars(select(User).where(User.email == email)).one()


def persona_id(db, email) -> str:
    found = str(persona(db, email).id)
    db.rollback()  # end the read: on SQLite an open one would lock the app's writes
    return found


@pytest.fixture
def visitor(engine, reference, seeded):
    app = create_app(
        make_settings(str(engine.url), demo_personas=True),
        engine=engine,
        reference=reference,
        report_renderer=plain_renderer,
    )
    with TestClient(app) as client:
        yield client


def continue_as(client, db, email):
    page = client.get("/demo/personas").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    client.post("/demo/continue-as", data={"csrf_token": token, "user_id": persona_id(db, email)})
    return token


def token_of(page: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def test_the_quality_personas_hold_their_capability_and_nothing_else(db, seeded):
    quinn, uma, sue = (persona(db, e) for e in ("quinn@demo.example", "uma@demo.example", "sue@demo.example"))
    assert capabilities.effective(db, quinn) == {capabilities.QUALITY_MANAGER}
    assert capabilities.effective(db, uma) == {capabilities.QUALITY}
    assert capabilities.effective(db, sue) == frozenset()  # personas never manage users
    assert (
        quality_actor(db, quinn).role == QualityRole.MANAGER
        and quality_actor(db, uma).role == QualityRole.USER
    )
    assert quality_actor(db, sue) is None and quality_actor(db, None) is None


def test_whoever_manages_users_grants_the_quality_roles(db):
    supervisor = User(email="s@lab.example", password_hash="x", role=ROLE_STAFF, initials="SS", lab_role=3)
    analyst = User(
        email="a@lab.example", password_hash="x", role=ROLE_STAFF, initials="AA", lab_role=LAB_ROLE_ANALYST
    )
    db.add_all([supervisor, analyst])
    db.flush()
    assert capabilities.may_grant(supervisor, capabilities.QUALITY_MANAGER)
    assert not capabilities.may_grant(analyst, capabilities.QUALITY)
    db.add(UserCapability(user_id=analyst.id, capability=capabilities.QUALITY, granted_by=supervisor.id))
    db.flush()
    assert quality_actor(db, analyst).role == QualityRole.USER


def test_the_history_is_stored_as_qc_runs_off_the_board_and_results(db, seeded):
    assert seeded > 30
    summaries = batches(db)
    assert summaries and all(b.qc_only for b in summaries)
    series = SlimDataQc(db).series()
    keys = {
        (s.key.instrument, s.key.analyte, s.key.conditions)
        for s in series
        if s.key.measure == Measure.CHECK_RECOVERY
    }
    assert ("ICP-MS 1", "Na", "Cool") in keys and ("ICP-MS 2", "Fe", "KED") in keys
    assert {s.key.measure for s in series} >= {Measure.BLANK_LEVEL, Measure.ISTD_RECOVERY, Measure.CAL_BEC}
    assert len(store.list_changes(db, open_only=True)) == 1 and len(store.events(db)) == 1
    assert seed_quality_history(db, None, None, NOW) == 0  # once


def test_staff_without_quality_get_403(visitor, db):
    continue_as(visitor, db, "ana@demo.example")
    assert visitor.get("/quality").status_code == 403
    assert "/quality" not in visitor.get("/").text.split("<main")[0]


def test_a_quality_user_reads_charts_and_proposes_but_cannot_decide(visitor, db):
    continue_as(visitor, db, "uma@demo.example")
    home = visitor.get("/quality")
    assert home.status_code == 200 and "ICP-MS 1" in home.text and ">Quality<" in home.text
    assert "sev--" in home.text and "forecast" in home.text.lower()
    slug = next(s.key.slug for s in SlimDataQc(db).series() if s.key.measure == Measure.ISTD_RECOVERY)
    db.rollback()
    page = visitor.get(f"/quality/series?id={quote(slug)}")
    assert page.status_code == 200 and "<svg" in page.text and "<script" not in page.text.split("<main")[1]
    token = token_of(page.text)
    ref = re.search(r'<option value="(reading:\d+)"', page.text).group(1)
    assert (
        visitor.post(
            "/quality/notes", data={"csrf_token": token, "id": slug, "ref": ref, "note": "bubble"}
        ).status_code
        == 200
    )
    event = {
        "csrf_token": token,
        "id": slug,
        "instrument": "ICP-MS 1",
        "kind": "1",
        "on": "2026-10-20",
        "note": "pump tubing",
    }
    assert "pump tubing" in visitor.post("/quality/events", data=event).text
    limits = {
        "csrf_token": token,
        "id": slug,
        "method": "2",
        "start": "2026-09-28",
        "end": "2026-10-10",
        "effective": "2026-09-28",
        "reason": "baseline",
    }
    assert "Limits proposed" in visitor.post("/quality/limits", data=limits).text
    changes = visitor.get("/quality/changes").text
    assert "Waiting for a quality manager" in changes and "Approve" not in changes
    change = store.list_changes(db, open_only=True)[0]
    db.rollback()
    refused = visitor.post(f"/quality/changes/{change.id}/decide", data={"csrf_token": token, "approve": "1"})
    assert refused.status_code == 403
    assert visitor.get("/quality/series?id=nope").status_code == 404


def profile_form(page: str) -> list[tuple[str, str]]:
    """What a browser sends from the profile page as it stands: every text input and select, and the
    ticked checkboxes."""
    form = re.split(r'action="[^"]*/quality/profile"', page)[1].split("</form>")[0]
    data = [("csrf_token", token_of(page))]
    for tag in re.findall(r"<input[^>]*>", form):
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if not name or name.group(1) == "csrf_token":
            continue
        if 'type="checkbox"' in tag and "checked" not in tag:
            continue
        data.append((name.group(1), value.group(1) if value else ""))
    for name, body in re.findall(r'<select name="([^"]+)">(.*?)</select>', form, re.S):
        chosen = re.search(r'<option value="([^"]*)" selected', body) or re.search(
            r'<option value="([^"]*)"', body
        )
        data.append((name, chosen.group(1)))
    return data


def form(pairs) -> dict[str, list[str]]:
    """Pairs as httpx sends repeated fields: a dict of lists."""
    found: dict[str, list[str]] = {}
    for k, v in pairs:
        found.setdefault(k, []).append(v)
    return found


def test_profile_backtest_then_propose_and_a_manager_approves(visitor, db):
    continue_as(visitor, db, "uma@demo.example")
    page = visitor.get("/quality/profile").text
    assert "Westgard rules" in page and "westgard@1|1_2s" in page
    data = [(k, v) for k, v in profile_form(page) if (k, v) != ("enabled", "ewma@1")]  # EWMA off
    tested = visitor.post("/quality/profile", data=form(data + [("action", "backtest")]))
    assert tested.status_code == 200 and "Backtest: the last 6 months" in tested.text
    assert (
        visitor.post("/quality/profile", data=form(data + [("action", "propose")])).status_code == 422
    )  # no reason
    proposed = visitor.post(
        "/quality/profile", data=form(data + [("action", "propose"), ("reason", "EWMA too chatty")])
    )
    assert "proposed" in proposed.text
    bad = [(k, "x" if k == "param_value" else v) for k, v in data]
    assert visitor.post("/quality/profile", data=form(bad + [("action", "backtest")])).status_code == 422

    continue_as(visitor, db, "quinn@demo.example")
    changes = visitor.get("/quality/changes").text
    change = max(c.id for c in store.list_changes(db, open_only=True))
    db.rollback()
    token = token_of(changes)
    approved = visitor.post(f"/quality/changes/{change}/decide", data={"csrf_token": token, "approve": "1"})
    assert approved.status_code == 200
    db.expire_all()
    from slim_quality.service.quality import current_profile

    assert current_profile(db).version == 1 and not current_profile(db).setting("ewma@1").enabled
    assert (
        visitor.post("/quality/changes/999/decide", data={"csrf_token": token, "approve": "1"}).status_code
        == 404
    )


def test_a_manager_signs_the_qc_review(visitor, db):
    continue_as(visitor, db, "quinn@demo.example")
    page = visitor.get("/quality/reviews").text
    assert "Sign the review" in page
    data = {
        "csrf_token": token_of(page),
        "period_from": "2026-10-01",
        "period_to": "2026-10-28",
        "comment": "ICP-MS 1 drifting: cones booked",
    }
    signed = visitor.post("/quality/reviews", data=data)
    assert signed.status_code == 200 and "cones booked" in signed.text and "ACTION" in signed.text
    assert visitor.post("/quality/reviews", data=data | {"period_to": "2026-09-01"}).status_code == 422
    continue_as(visitor, db, "uma@demo.example")
    assert (
        visitor.post(
            "/quality/reviews", data=data | {"csrf_token": token_of(visitor.get("/quality").text)}
        ).status_code
        == 403
    )
