"""slim-quality's registers and records in the app (roadmap phase 5a, slim-data D-125…D-130): the guards
where the modules meet. One test per rule of the brief."""

import re
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from slim_data.persistence.results import issues_for_tr
from slim_data.service.views import report_view
from slim_prep.demo import DEMO_RULES
from slim_prep.domain.problems import PrepRefused
from slim_quality.domain.complaints import ComplaintState
from slim_quality.domain.equipment import EquipmentEntry, EquipmentEntryKind, EquipmentState
from slim_quality.domain.materials import LotEntry, LotEntryKind
from slim_quality.domain.nonconformity import NcSource
from slim_quality.domain.pt import PtState
from slim_quality.persistence import store
from slim_quality.service import register
from sqlalchemy import select

from slim_lims import capabilities
from slim_lims.main import create_app
from slim_lims.models import User, UserCapability
from slim_lims.prep import publish_demo_prep
from slim_lims.quality import seed_quality_records, touched
from slim_lims.quality_records import (
    DEMO_INSTRUMENTS,
    PT_SCHEME,
    SEED_QUALITY_ACTOR,
    RegisterStocks,
    instrument_problems,
    involved_in,
    pt_lab_values,
    refuse_equipment,
    released_from_prep,
    seed_demo_register,
)
from slim_lims.reports import plain_renderer
from tests.conftest import make_settings
from tests.test_demo_prep import prepped  # noqa: F401 (fixture)
from tests.test_demo_scenarios import reference, specs  # noqa: F401 (fixture)
from tests.test_quality import continue_as, persona, token_of
from tests.test_quality_audit import issued  # noqa: F401 (fixture)

TODAY = date.today()


@pytest.fixture
def records(db, issued, reference):  # noqa: F811
    publish_demo_prep(db, reference, TODAY)
    seed_demo_register(db, TODAY, DEMO_INSTRUMENTS, DEMO_RULES)
    found = seed_quality_records(db, reference, specs, datetime.now(UTC), issued["clean_pass"])
    db.commit()
    return found


@pytest.fixture
def visitor(engine, reference, records):  # noqa: F811
    settings = make_settings(str(engine.url), demo_personas=True)
    app = create_app(
        settings, engine=engine, reference=reference, specs=specs, report_renderer=plain_renderer
    )
    with TestClient(app) as client:
        yield client


def as_persona(client, db, email) -> str:
    continue_as(client, db, email)
    return token_of(client.get("/demo/personas").text)


def item(db, name):
    return next(i for i in store.list_equipment(db) if i.name == name)


def entry(kind, on, **kw):
    return EquipmentEntry(kind=kind, on=on, by=SEED_QUALITY_ACTOR, **kw)


def batch(balance="BAL-1", pipettes=(), on=TODAY):
    cup = SimpleNamespace(balance=None, pipettes=tuple(pipettes), spikes=())
    at = datetime.combine(on, datetime.min.time(), UTC)
    return SimpleNamespace(
        balance=balance, samples=(SimpleNamespace(cups=(cup,)),), prep=SimpleNamespace(at=at)
    )


# --- the demo ------------------------------------------------------------------------------------


def test_the_demo_register_pt_and_complaints_are_seeded_once(db, records, reference):  # noqa: F811
    names = {i.name for i in store.list_equipment(db)}
    assert (
        set(DEMO_INSTRUMENTS) | set(DEMO_RULES.balances) | set(DEMO_RULES.pipettes) | {"BAL-SPARE"} == names
    )
    assert item(db, "BAL-SPARE").state(TODAY) == EquipmentState.OVERDUE
    assert item(db, DEMO_RULES.pipettes[0]).state(TODAY) == EquipmentState.DUE
    opened = next(lot for lot in store.list_lots(db) if lot.opened_on)
    assert opened.effective_expiry == TODAY + timedelta(days=10)
    rounds = store.list_pt_rounds(db)
    assert (
        len(rounds) == 5 and rounds[0].tr_number == records["pt_tr"] and rounds[0].state == PtState.REGISTERED
    )
    customer_z = db.execute(select(User)).first()  # (any read; the TR is the repository's)
    assert customer_z is not None
    states = sorted(c.state for c in store.list_complaints(db))
    assert states == [ComplaintState.ACKNOWLEDGED, ComplaintState.CLOSED]
    assert seed_demo_register(db, TODAY, DEMO_INSTRUMENTS, DEMO_RULES) == 0
    assert seed_quality_records(db, reference, specs, datetime.now(UTC), None) == {}


# --- reference lots (D-127, P-28) -----------------------------------------------------------------------


def test_a_registered_lot_is_the_stock_slim_prep_gets(db, records):
    lot = next(lot for lot in store.list_lots(db) if lot.opened_on)
    stocks = RegisterStocks(db)
    found = stocks.stock(lot.name, lot.lot, TODAY)
    assert found.expires == lot.effective_expiry and found.concentration.value == lot.value
    assert stocks.stock(lot.name, "NOPE", TODAY) == "not in the reference-material register"
    register.add_lot_entry(
        db, lot.id, LotEntry(kind=LotEntryKind.WITHDRAWN, on=TODAY, note="dropped", by=SEED_QUALITY_ACTOR)
    )
    assert stocks.stock(lot.name, lot.lot, TODAY) == f"withdrawn on {TODAY}"
    assert not isinstance(stocks.stock(lot.name, lot.lot, TODAY - timedelta(days=1)), str)  # before it


def test_the_prep_book_refuses_a_standard_from_an_unregistered_lot(visitor, db):
    token = as_persona(visitor, db, "ana@demo.example")
    made = visitor.post(
        "/prep/standards",
        data={"csrf_token": token, "step": "multi-element spike|20 ppm",
              "source": "stock|Multi-element stock A|NOPE", "prepared": TODAY.isoformat(),
              "pipettes": "P1000-1"},
    )  # fmt: skip
    assert made.status_code == 422 and "not in the reference-material register" in made.text
    assert "DEMO-0001" in visitor.get("/prep").text  # the registered lot is offered


# --- equipment in use (D-126) ---------------------------------------------------------------------------


def test_an_overdue_balance_blocks_a_prep_batch_and_an_unregistered_one_warns(db, records):
    with pytest.raises(PrepRefused, match="BAL-SPARE"):
        refuse_equipment(db, batch("BAL-SPARE"))
    assert refuse_equipment(db, batch("BAL-1", ("P100-1",))) == []
    assert refuse_equipment(db, batch("BAL-9")) == ["BAL-9: not in the register"]


def test_out_of_service_blocks_whatever_the_kinds_setting(db, records):
    """An instrument overdue only warns (the profile's default), but out of service blocks."""
    icp = item(db, "ICP-MS 2")
    assert instrument_problems(db, "ICP-MS 2", TODAY) == ([], [])
    register.add_equipment_entry(
        db, icp.id, entry(EquipmentEntryKind.OUT_OF_SERVICE, TODAY, note="torch cracked")
    )
    assert instrument_problems(db, "ICP-MS 2", TODAY) == (["ICP-MS 2: out of service"], [])
    assert instrument_problems(db, "ICP-MS 2", TODAY - timedelta(days=1)) == ([], [])  # a run from before
    overdue = item(db, "Titrator 1")
    register.add_equipment_entry(
        db, overdue.id, entry(EquipmentEntryKind.CALIBRATION, TODAY - timedelta(days=500), passed=True,
                              provider="x", next_due=TODAY - timedelta(days=1))
    )  # fmt: skip
    blocks, warnings = instrument_problems(db, "Titrator 1", TODAY + timedelta(days=200))
    assert blocks == [] and warnings and "overdue" in warnings[0]


def test_a_failed_balance_nc_touches_only_the_batches_that_used_it(db, prepped, reference):  # noqa: F811
    """D-126: the window runs from the last pass; the impact is the released results of the prep batches
    that used the balance in it (the prep scenario's PREP-0001 used BAL-1)."""
    seed_demo_register(db, TODAY, DEMO_INSTRUMENTS, DEMO_RULES)
    start, end = datetime.now(UTC) - timedelta(days=30), datetime.now(UTC) + timedelta(days=1)
    hit = released_from_prep(db, "BAL-1", start, end)
    assert [(str(r.sample), r.analyte.code) for r in hit if r.analyte.code == "Cu"] == [("TR00001-01", "Cu")]
    assert all(r.batch_code == "PREP-0001" for r in hit)
    assert released_from_prep(db, "BAL-2", start, end) == []  # never used
    assert released_from_prep(db, "BAL-1", end, end + timedelta(days=1)) == []  # outside the window
    bal = item(db, "BAL-1")
    failed = register.add_equipment_entry(
        db, bal.id, entry(EquipmentEntryKind.CALIBRATION, TODAY + timedelta(days=1), passed=False,
                          provider="in house", note="0.4 mg high")
    )  # fmt: skip
    nc = register.failure_nc(db, bal.id, len(failed.entries) - 1, SEED_QUALITY_ACTOR, UTC)
    assert nc.source == NcSource.EQUIPMENT and nc.window_from.date() == TODAY - timedelta(days=200)
    assert {(str(r.sample), r.analyte.code) for r in touched(db, nc)} >= {("TR00001-01", "Cu")}


# --- PT (D-128) -----------------------------------------------------------------------------------------


def test_a_pt_round_waits_for_release_then_submits_a_snapshot(visitor, db, records, issued):  # noqa: F811
    token = as_persona(visitor, db, "uma@demo.example")
    current = store.list_pt_rounds(db)[0]
    page = visitor.get(f"/quality/pt/{current.id}")
    assert page.status_code == 200 and "Waiting for the lab" in page.text
    refused = visitor.post(f"/quality/pt/{current.id}/submit", data={"csrf_token": token})
    assert refused.status_code == 409 and "not released" in refused.text
    tr = issued["clean_pass"]
    line = report_view(db, tr).lines[0]
    sample = f"{tr}-{line.sample.position:02d}"
    db.rollback()
    made = visitor.post(
        "/quality/pt",
        data={"csrf_token": token, "scheme": PT_SCHEME, "round_code": "T-1", "sample": sample,
              "analytes": line.analyte.code, "due": TODAY.isoformat()},
    )  # fmt: skip
    assert made.status_code == 200 and "registered" in made.text
    round_id = int(re.search(r"/quality/pt/(\d+)/submit", made.text).group(1))
    assert visitor.post(f"/quality/pt/{round_id}/submit", data={"csrf_token": token}).status_code == 200
    sent = store.get_pt_round(db, round_id).submission.values[0]
    assert sent.value == line.reported.value and sent.result_ref.endswith(f"|{line.version}")
    db.rollback()
    again = visitor.post(f"/quality/pt/{round_id}/submit", data={"csrf_token": token})
    assert again.status_code == 409  # submitted once: what was sent stays as sent
    scored = visitor.post(
        f"/quality/pt/{round_id}/scores",
        data={"csrf_token": token, "report_date": TODAY.isoformat(), "analyte": line.analyte.code,
              "assigned": str(line.reported.value * 2), "sigma": str(line.reported.value / 10),
              "provider_z": ""},
    )  # fmt: skip
    assert scored.status_code == 200 and "Unsatisfactory" in scored.text
    nc = visitor.post(f"/quality/pt/{round_id}/nc", data={"csrf_token": token, "analyte": line.analyte.code})
    assert nc.status_code == 200 and "unsatisfactory (z = -10.0)" in nc.text


def test_pt_values_need_a_lab_sample_id(db, records):
    bad = store.list_pt_rounds(db)[0].model_copy(update={"sample": "nonsense"})
    with pytest.raises(ValueError, match="not a lab sample ID"):
        pt_lab_values(db, bad)


# --- complaints (D-130) ---------------------------------------------------------------------------------


def test_involved_is_everyone_with_a_hand_in_the_request(db, issued):  # noqa: F811
    tr = issued["clean_pass"]
    ids = involved_in(db, tr)
    rob, ana, quinn = (
        str(persona(db, e).id) for e in ("rob@demo.example", "ana@demo.example", "quinn@demo.example")
    )
    assert {rob, ana} <= ids and quinn not in ids
    assert issues_for_tr(db, tr)[0].created_by.id in ids  # who signed it (D-122)


def test_a_customer_raises_a_complaint_and_sees_only_what_is_meant_for_them(visitor, db, records, issued):  # noqa: F811
    tr = issued["clean_pass"]
    token = as_persona(visitor, db, "customer@demo.example")
    page = visitor.get(f"/requests/{tr}/complaints")
    assert page.status_code == 200 and "Delivered a day late" not in page.text
    assert "the delivery went out late" not in page.text  # the closed one's investigation is internal
    assert "You were right" in page.text  # its outcome is the customer's
    raised = visitor.post(f"/requests/{tr}/complaints", data={"csrf_token": token, "text": "Sample 2 too"})
    assert raised.status_code == 200 and "received" in raised.text
    assert store.list_complaints(db, tr_number=tr)[0].raised_by.display
    other = issued["cups_disagree"]  # customer B's
    assert visitor.get(f"/requests/{other}/complaints").status_code == 404
    assert (
        visitor.post(f"/requests/{other}/complaints", data={"csrf_token": token, "text": "x"}).status_code
        == 404
    )
    assert visitor.get("/quality/complaints").status_code == 403


def test_the_outcome_is_refused_to_anyone_with_a_hand_in_the_request(visitor, db, records, issued):  # noqa: F811
    tr = issued["clean_pass"]
    rob = persona(db, "rob@demo.example")
    db.add(UserCapability(user_id=rob.id, capability=capabilities.QUALITY_MANAGER, granted_by=rob.id))
    db.commit()
    open_one = next(c for c in store.list_complaints(db, tr_number=tr) if c.state != ComplaintState.CLOSED)
    db.rollback()
    token = as_persona(visitor, db, "uma@demo.example")
    investigated = visitor.post(
        f"/quality/complaints/{open_one.id}/entries",
        data={"csrf_token": token, "kind": "3", "note": "Rechecked the Cu: as reported.", "valid": "1"},
    )
    assert investigated.status_code == 200
    token = as_persona(visitor, db, "rob@demo.example")
    page = visitor.get(f"/quality/complaints/{open_one.id}")
    assert "You had a hand in" in page.text
    refused = visitor.post(
        f"/quality/complaints/{open_one.id}/entries", data={"csrf_token": token, "kind": "4", "note": "fine"}
    )
    assert refused.status_code == 409 and "§7.9.6" in refused.text
    token = as_persona(visitor, db, "quinn@demo.example")
    approved = visitor.post(
        f"/quality/complaints/{open_one.id}/entries",
        data={"csrf_token": token, "kind": "4", "note": "As reported."},
    )
    assert (
        approved.status_code == 200
        and store.get_complaint(db, open_one.id).state == ComplaintState.OUTCOME_APPROVED
    )


# --- the pages ---------------------------------------------------------------------------------------------


def test_the_quality_pages_show_the_registers(visitor, db, records):
    as_persona(visitor, db, "uma@demo.example")
    home = visitor.get("/quality").text
    assert "1 overdue or out of service, 1 due soon" in home and "1 expiring within 30 days" in home
    assert "BAL-SPARE" in visitor.get("/quality/equipment").text
    spare = item(db, "BAL-SPARE")
    db.rollback()
    assert "<strong>Overdue</strong>" in visitor.get(f"/quality/equipment/{spare.id}").text
    assert "CERT-DEMO-0000" in visitor.get("/quality/materials").text
    pt = visitor.get("/quality/pt").text
    assert "Demo PT Metals · Cu · PT z-score" in pt and "Questionable" in pt
    assert "CP-0002" in visitor.get("/quality/complaints").text
    assert visitor.get("/quality/equipment/999").status_code == 404


def test_registering_and_recording_on_the_equipment_page(visitor, db, records):
    token = as_persona(visitor, db, "uma@demo.example")
    made = visitor.post(
        "/quality/equipment",
        data={"csrf_token": token, "name": "P10-1", "kind": "3", "calibration_required": "1"},
    )
    assert made.status_code == 200 and "P10-1 registered" in made.text
    again = visitor.post("/quality/equipment", data={"csrf_token": token, "name": "P10-1", "kind": "3"})
    assert again.status_code == 409
    new = item(db, "P10-1")
    db.rollback()
    failed = visitor.post(
        f"/quality/equipment/{new.id}/entries",
        data={"csrf_token": token, "kind": "1", "on": TODAY.isoformat(), "passed": "0",
              "provider": "in house", "note": "reads 3 % low"},
    )  # fmt: skip
    assert failed.status_code == 200 and "Out of service" in failed.text and "Raise an NC" in failed.text
    nc = visitor.post(f"/quality/equipment/{new.id}/nc", data={"csrf_token": token, "index": "0"})
    assert nc.status_code == 200 and "P10-1: calibration failed" in nc.text
    bad = visitor.post(
        f"/quality/equipment/{new.id}/entries", data={"csrf_token": token, "kind": "1", "on": "x"}
    )
    assert bad.status_code == 422
    lot = visitor.post(
        "/quality/materials",
        data={"csrf_token": token, "name": "Single Cu", "lot": "CU-1", "supplier": "Demo", "certificate": "C",
              "value": "1000", "unit": "ppm", "elements": "Cu",
              "expires": (TODAY + timedelta(days=90)).isoformat()},
    )  # fmt: skip
    assert lot.status_code == 200 and "Single Cu lot CU-1 registered" in lot.text


def test_profile_changes_carry_the_equipment_settings_and_keep_the_fixed_z_limits(visitor, db, records):
    token = as_persona(visitor, db, "uma@demo.example")
    form = visitor.get("/quality/profile").text
    assert "fixed at 0 ± 1 SD" in form and 'name="eq_kind"' in form
    from slim_quality.service import quality as q

    from slim_lims.quality_routes import profile_from_form

    base = q.current_profile(db)
    candidate = profile_from_form(
        [t.tool for t in base.tools if t.enabled], [], [], [], [], [], [], [], [], [], [], [],
        base=base, equipment={"kinds": ["1"], "effects": ["3"]}, pt_cutoffs=("2.5", ""),
    )  # fmt: skip
    assert candidate.policy(9).fixed is not None  # the PT z-score's limits are kept, not reset
    assert candidate.equipment.effect(1).name == "BLOCK" and candidate.pt.questionable == 2.5
    assert token
