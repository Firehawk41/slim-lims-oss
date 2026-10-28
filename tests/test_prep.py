"""The prep book in the app (slim-prep P6): standards, the batch grid, send to results, the run list,
and a correction after the readings came in."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from slim_data.domain.enums import ReadingRole
from slim_data.domain.values import SampleRef
from slim_data.persistence.observations import evaluation_input
from slim_data.persistence.observations import get_batch as results_batch
from slim_prep.persistence import migrate as prep_migrate
from sqlalchemy import select

from slim_lims.models import User
from slim_lims.prep import publish_demo_prep
from tests.conftest import csrf_from, login, login_api
from tests.test_results import HEADER, lab, reference, upload  # noqa: F401 (fixtures)

TODAY = date.today()


@pytest.fixture
def app(engine, reference):  # noqa: F811
    from slim_data.persistence import migrate

    from slim_lims.main import create_app
    from slim_lims.reports import plain_renderer
    from tests.conftest import make_settings

    with engine.begin() as connection:  # fresh module tables (Postgres keeps them otherwise)
        prep_migrate.downgrade(connection)
        migrate.downgrade(connection)
        migrate.upgrade(connection)
        prep_migrate.upgrade(connection)
    return create_app(
        make_settings(str(engine.url)),
        engine=engine,
        reference=reference,
        specs=lambda *_: {},
        report_renderer=plain_renderer,
    )


@pytest.fixture
def prep(lab, reference):  # noqa: F811
    publish_demo_prep(lab, reference, TODAY)
    lab.commit()
    return lab


def _standards(client, token):
    for step, source in (
        ("multi-element spike|20 ppm", "stock|Multi-element stock A|DEMO-0001"),
        ("multi-element spike|50 ppb", f"standard|20 ppm|{TODAY.isoformat()}"),
    ):
        made = client.post(
            "/prep/standards",
            data={"csrf_token": token, "step": step, "source": source, "prepared": TODAY.isoformat(),
                  "pipettes": "P1000-1"},
        )  # fmt: skip
        assert made.status_code == 200 and "Standard recorded." in made.text, made.text


def _staff_id(session, initials="AN") -> str:
    found = str(session.scalar(select(User.id).where(User.initials == initials)))
    session.rollback()  # the read ends before the app writes (SQLite locks)
    return found


def _grid(token, client, pour_out, gross="12.5000", final="2.6000", samples=("TR00001-01", "TR00001-02")):
    """The grid as the screen posts it: prepped an hour ago (lab time), poured out by `pour_out` (an id)."""
    at = f"{client.app.state.lab_clock.now() - timedelta(hours=1):%Y-%m-%dT%H:%M}"
    values = {"csrf_token": token, "code": "PREP-0001", "method": "evaporation",
              "analysis": "10_elements_icpms", "balance": "BAL-1", "pour_out": pour_out, "pour_out_at": at,
              "prep_at": at}  # fmt: skip
    for i, sample in enumerate(samples):
        values[f"sample_{i}"] = sample
        for n in (1, 2, 3):
            k = f"c_{i}_{n}"
            values |= {f"{k}_tare": "2.0000", f"{k}_cap": "0.5000", f"{k}_gross": gross, f"{k}_final": final}
        values |= {f"c_{i}_3_s0_standard": f"50 ppb|{TODAY.isoformat()}", f"c_{i}_3_s0_volume": "10",
                   f"c_{i}_3_s0_pipette": "P100-1"}  # fmt: skip
    return values


def _readings(analyst="AN") -> bytes:
    """Raw readings (before dilution) for the prepped batch: the file leaves the dilution at 1."""
    rows = [
        *(
            ("TR00001-01", role, rep, v)
            for role, rep, v in (("CUP", 1, "100.0"), ("CUP", 2, "101.0"), ("SPIKE", 1, "101.3"))
        ),
        *(
            ("TR00001-02", role, rep, v)
            for role, rep, v in (("CUP", 1, "50.0"), ("CUP", 2, "50.5"), ("SPIKE", 1, "51.3"))
        ),
        ("BLANK", "BLANK", 1, "0.010"),
    ]
    lines = [HEADER] + [
        f"{s},10_elements_icpms,Fe,{role},{rep},{value},ppb,0.05,1,RUN-P1,ICP-MS 1,{analyst},"
        "2026-10-10T09:00:00+00:00,PREP-0001,,,,"
        for s, role, rep, value in rows
    ]
    return ("\n".join(lines) + "\n").encode()


def test_standards_then_a_batch_then_send_then_readings_then_a_correction(client, prep):
    login(client, "staff@lab.example")
    page = client.get("/prep")
    assert page.status_code == 200 and "Prep book" in page.text and "Multi-element stock A" in page.text
    token = csrf_from(page.text)
    _standards(client, token)
    assert "50 ppb" in client.get("/prep").text

    query = {"code": "PREP-0001", "method": "evaporation", "analysis": "10_elements_icpms"}
    picker = client.get("/prep/new-batch", params=query)  # P-23: the open samples that ordered it
    assert picker.status_code == 200 and 'value="TR00001-02"' in picker.text
    assert "TR00002" not in picker.text and "TR00003" not in picker.text  # not received; another analysis
    grid = client.get("/prep/new-batch", params={**query, "sample": ["TR00001-01", "TR00001-02"]})
    assert grid.status_code == 200 and "TR00001-02" in grid.text and "50 ppb" in grid.text

    saved = client.post("/prep/batches", data=_grid(token, client, _staff_id(prep)))
    assert saved.status_code == 200 and "Prep batch PREP-0001 recorded." in saved.text
    assert "0.06" in saved.text and "Send to results" in saved.text  # (2.6 − 2) ÷ (12.5 − 2 − 0.5)

    sent = client.post("/prep/batches/PREP-0001/send", data={"csrf_token": token})
    assert sent.status_code == 200 and "PREP-0001 sent to results." in sent.text
    registered = results_batch(prep, "PREP-0001")
    assert registered.prep_method == "evaporation"
    assert registered.prep_dilution(SampleRef.parse("TR00001-01"), ReadingRole.CUP, 1) == Decimal("0.06")
    assert registered.spikes[0].added.value == Decimal("0.05") and registered.spikes[0].added.unit == "ppb"
    prep.rollback()  # the test's read ends before the app writes again (SQLite locks)

    run_list = client.get("/prep/run-list.csv", params={"code": "PREP-0001"})
    assert run_list.status_code == 200 and run_list.headers["content-type"].startswith("text/csv")
    assert "TR00001-01 1,cup,PREP-0001,TR00001-01,1,0.06" in run_list.text

    login_api(client, "staff@lab.example")
    imported = upload(client, _readings())
    assert imported.status_code == 201, imported.json()
    readings = evaluation_input(prep, "PREP-0001").readings
    assert {r.dilution for r in readings if r.sample is not None} == {Decimal("0.06")}
    prep.rollback()

    # A weighing was misread: correct the batch, send the correction; slim-data re-evaluates.
    batch = client.get("/api/prep/batches/PREP-0001").json()
    body = batch["batch"]
    body["samples"][0]["cups"][1]["weighing"]["final"] = "2.7000"
    body["prep"]["at"] = body["prep"]["at"][:16]  # a time without a zone is the lab's
    fixed = client.post(
        "/api/prep/batches", json={"batch": body, "expected_version": 1, "reason": "cup 2 final misread"}
    )
    assert fixed.status_code == 201 and fixed.json()["version"] == 2
    resent = client.post("/api/prep/batches/PREP-0001/send")
    assert resent.status_code == 200, resent.json()
    assert resent.json()["changed"] == 1 and resent.json()["evaluated"]["new_versions"] >= 1
    later = evaluation_input(prep, "PREP-0001").readings
    cup2 = next(
        r
        for r in later
        if r.sample == SampleRef.parse("TR00001-01") and r.replicate == 2 and r.role == ReadingRole.CUP
    )
    assert cup2.dilution == Decimal("0.07")


def test_a_grid_the_rules_refuse_is_shown_again_with_every_problem(client, prep):
    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    refused = client.post(
        "/prep/batches", data=_grid(token, client, _staff_id(prep), gross="2.1000")
    )  # gross below tare + cap
    assert refused.status_code == 422 and "Nothing was recorded" in refused.text
    assert "gross must exceed tare and cap" in refused.text and 'value="2.1000"' in refused.text
    assert client.get("/api/prep/batches/PREP-0001").status_code == 404


def test_the_api_makes_standards_and_refuses_an_expired_source(client, prep):
    login_api(client, "staff@lab.example")
    made = client.post("/api/prep/standards", json={"recipe": "multi-element spike", "step": "20 ppm",
                                                    "stock": ["Multi-element stock A", "DEMO-0001"],
                                                    "prepared": TODAY.isoformat()})  # fmt: skip
    assert made.status_code == 201 and made.json()["concentration"] == "20 ppm"
    twenty = {"name": "20 ppm", "prepared": TODAY.isoformat()}
    late_body = {
        "recipe": "multi-element spike",
        "step": "50 ppb",
        "standard": twenty,
        "prepared": "2099-01-01",
    }
    late = client.post("/api/prep/standards", json=late_body)
    assert late.status_code == 422 and any("expired" in p for p in late.json()["problems"])
    assert [s["name"] for s in client.get("/api/prep/standards").json()] == ["20 ppm"]
    assert client.get("/api/prep/run-list", params={"code": "PB-X"}).status_code == 404


def test_prep_is_staff_only(client, prep, customer_user):
    login_api(client, customer_user.email)
    assert client.get("/api/prep/standards").status_code == 403


def test_a_batch_is_corrected_on_screen_with_a_reason_then_the_correction_is_sent(client, prep):
    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    client.post("/prep/batches", data=_grid(token, client, _staff_id(prep)))
    client.post("/prep/batches/PREP-0001/send", data={"csrf_token": token})

    edit = client.get("/prep/batches/PREP-0001/edit")
    assert edit.status_code == 200 and "Correct prep batch PREP-0001 (version 1)" in edit.text
    assert (
        'name="c_0_2_final" value="2.6000"' in edit.text and 'name="expected_version" value="1"' in edit.text
    )

    fixed = _grid(token, client, _staff_id(prep)) | {"expected_version": "1", "c_0_2_final": "2.7000"}
    no_reason = client.post("/prep/batches", data=fixed)
    assert no_reason.status_code == 422 and "a correction needs a reason" in no_reason.text

    done = client.post("/prep/batches", data=fixed | {"reason": "cup 2 final misread"})
    assert done.status_code == 200 and "PREP-0001 corrected (version 2)" in done.text
    assert "Send the correction to results" in done.text and "cup 2 final misread" in done.text
    batch = client.get("/api/prep/batches/PREP-0001").json()
    assert batch["version"] == 2 and batch["batch"]["prep"]["analyst"] == "JM"  # who prepped it stays

    stale = client.post("/prep/batches", data=fixed | {"reason": "again"})
    assert stale.status_code == 409 and "the latest is 2" in stale.text

    sent = client.post("/prep/batches/PREP-0001/send", data={"csrf_token": token})
    assert "PREP-0001 correction sent (1 changed)." in sent.text


def test_editing_an_unknown_batch_is_404(client, prep):
    login(client, "staff@lab.example")
    assert client.get("/prep/batches/PB-X/edit").status_code == 404


def test_a_cancelled_requests_batch_isnt_sent(client, prep):  # D-99
    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    client.post("/prep/batches", data=_grid(token, client, _staff_id(prep)))
    login_api(client, "staff@lab.example")
    assert client.post("/api/submissions/TR00001/status", json={"to_status": 7}).status_code == 200
    refused = client.post("/api/prep/batches/PREP-0001/send")
    assert refused.status_code == 422 and refused.json()["problems"] == [
        "TR00001-01: its request was cancelled",
        "TR00001-02: its request was cancelled",
    ]


def test_an_evenings_batch_is_dated_by_the_lab_not_the_server(client, prep):  # review A8
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from slim_lims.labtime import LabClock

    evening = datetime(2026, 10, 29, 4, 30, tzinfo=UTC)  # 23:30 on the 28th in Chicago
    client.app.state.lab_clock = LabClock(ZoneInfo("America/Chicago"), lambda: evening)
    assert client.app.state.lab_clock.today() == date(2026, 10, 28)
    login(client, "staff@lab.example")
    page = client.get("/prep")
    assert 'value="2026-10-28"' in page.text  # the standards form's default prep date
    _standards(client, csrf_from(page.text))
    client.post("/prep/batches", data=_grid(csrf_from(page.text), client, _staff_id(prep)))
    at = client.get("/api/prep/batches/PREP-0001").json()["batch"]["prep"]["at"]
    assert at.startswith("2026-10-28T22:30") and at.endswith("-05:00")  # entered an hour before, lab time


# --- the 2026-10-28 review, module findings (docs/REVIEW_2026-10-28.md B1–B10; slim-prep P-23…P-27) ----


def test_b1_a_sample_not_checked_in_or_without_the_analysis_is_refused(client, prep):
    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    values = _grid(token, client, _staff_id(prep), samples=("TR00002-01", "TR00003-01", "TR09999-01"))
    refused = client.post("/prep/batches", data=values)
    assert refused.status_code == 422
    for problem in (
        "TR00002-01: its request isn&#39;t checked in yet",
        "TR00003-01: 10_elements_icpms wasn&#39;t ordered for it",
        "TR09999-01: no such sample",
    ):
        assert problem in refused.text


def test_b10_crafted_grid_fields_never_give_a_500(client, prep):
    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    good = _grid(token, client, _staff_id(prep))
    for crafted in (
        {"sample_x": "TR00001-01"},
        {"expected_version": "x"},
        {"prep_at": "yesterday"},
        {"c_0_1_gross": "NaN"},
        {"c_0_3_s0_standard": "nope|2026-13-45"},
        {"method": "no such method"},
    ):
        response = client.post("/prep/batches", data=good | crafted)
        assert response.status_code in (200, 409, 422), (crafted, response.status_code)  # never a 500
    assert client.get("/prep/new-batch", params={"code": "X", "analysis": "nope"}).status_code == 422


def test_b4_and_b7_times_are_entered_and_pour_out_comes_from_the_staff_list(client, prep):
    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    tomorrow = f"{client.app.state.lab_clock.now() + timedelta(days=1):%Y-%m-%dT%H:%M}"
    future = client.post("/prep/batches", data=_grid(token, client, _staff_id(prep)) | {"prep_at": tomorrow})
    assert future.status_code == 422 and "is in the future" in future.text
    stranger = client.post("/prep/batches", data=_grid(token, client, "ZZ"))
    assert stranger.status_code == 422 and "choose who from the staff list" in stranger.text
    client.post("/prep/batches", data=_grid(token, client, _staff_id(prep)))
    batch = client.get("/api/prep/batches/PREP-0001").json()["batch"]
    assert batch["pour_out"]["analyst"] == "AN" and batch["pour_out"]["analyst_id"] == _staff_id(prep)
    assert batch["prep"]["analyst_id"] == _staff_id(prep, "JM")


def test_b2_and_b3_a_correction_keeps_the_analysis_and_shows_every_spike_line(client, prep):
    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    client.post("/prep/batches", data=_grid(token, client, _staff_id(prep)))
    login_api(client, "staff@lab.example")
    body = client.get("/api/prep/batches/PREP-0001").json()["batch"]
    moved = client.post(
        "/api/prep/batches",
        json={"batch": body | {"analysis": "36_elements_icpms"}, "expected_version": 1, "reason": "x"},
    )
    assert moved.status_code == 422 and any(
        "keeps the batch's analysis" in p for p in moved.json()["problems"]
    )
    body["samples"][0]["cups"][2]["spikes"].append(_mercury_line(prep))  # a second line, from another stock
    added = client.post("/api/prep/batches", json={"batch": body, "expected_version": 1, "reason": "x"})
    assert added.status_code == 201, added.json()
    edit = client.get("/prep/batches/PREP-0001/edit").text
    assert 'name="c_0_3_s1_volume" value="5"' in edit and 'name="c_0_3_s2_standard"' in edit  # + a blank


def _mercury_line(session) -> dict:
    """A spike line from a mercury standard (its own stock lot): an API batch can carry two lines a cup."""
    from slim_prep.domain.prep import SpikeLine
    from slim_prep.domain.standards import Stock
    from slim_prep.domain.units import Concentration
    from slim_prep.persistence import books
    from slim_prep.service.books import record_standard

    from slim_lims.prep import SEED_PREP_ACTOR, rules

    thousand = Concentration(value=1000, unit="ppm")
    mercury = Stock(name="Multi-element stock A", lot="DEMO-HG", concentration=thousand, elements=("Hg",),
                    expires=TODAY + timedelta(days=365))  # fmt: skip
    books.save_stock(session, mercury, SEED_PREP_ACTOR)
    yesterday = TODAY - timedelta(days=1)
    made = record_standard(session, rules(session).id, "multi-element spike", "20 ppm",
                           (mercury.name, mercury.lot), yesterday, SEED_PREP_ACTOR).item  # fmt: skip
    session.commit()
    return SpikeLine.of(made, Decimal(5), "P100-1").model_dump(mode="json")


def test_b9_a_batch_moves_to_new_rules_only_as_a_recorded_step(client, prep):
    from slim_prep.demo import DEMO_RULES
    from slim_prep.persistence.rules import publish_rules

    from slim_lims.prep import SEED_PREP_ACTOR

    login(client, "staff@lab.example")
    token = csrf_from(client.get("/prep").text)
    _standards(client, token)
    client.post("/prep/batches", data=_grid(token, client, _staff_id(prep)))
    assert "Move to the current prep rules" not in client.get("/prep/batches/PREP-0001").text
    publish_rules(prep, DEMO_RULES.model_copy(update={"pipettes": (*DEMO_RULES.pipettes, "P10-1")}),
                  SEED_PREP_ACTOR)  # fmt: skip
    prep.commit()
    page = client.get("/prep/batches/PREP-0001").text
    assert "Move to the current prep rules" in page
    moved = client.post(
        "/prep/batches/PREP-0001/move-rules",
        data={"csrf_token": token, "expected_version": "1", "reason": "new pipette list"},
    )
    assert "is under the current rules (version 2)" in moved.text
    assert "Move to the current prep rules" not in moved.text


def test_b6_the_next_code_in_the_labs_series_is_offered(client, prep):
    login(client, "staff@lab.example")
    page = client.get("/prep")
    assert 'value="PREP-0001"' in page.text
    _standards(client, csrf_from(page.text))
    client.post("/prep/batches", data=_grid(csrf_from(page.text), client, _staff_id(prep)))
    assert 'value="PREP-0002"' in client.get("/prep").text
    off_series = _grid(csrf_from(page.text), client, _staff_id(prep)) | {"code": "PB-1"}
    refused = client.post("/prep/batches", data=off_series)
    assert refused.status_code == 422 and "prep batch codes are PREP-0001" in refused.text
