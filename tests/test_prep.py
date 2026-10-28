"""The prep book in the app (slim-prep P6): standards, the batch grid, send to results, the run list,
and a correction after the readings came in."""

from datetime import date
from decimal import Decimal

import pytest
from slim_data.domain.enums import ReadingRole
from slim_data.domain.values import SampleRef
from slim_data.persistence.observations import evaluation_input
from slim_data.persistence.observations import get_batch as results_batch
from slim_prep.persistence import migrate as prep_migrate

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


def _grid(token, gross="12.5000", final="2.6000"):
    values = {"csrf_token": token, "code": "PB-P1", "method": "evaporation", "analysis": "10_elements_icpms",
              "balance": "BAL-1", "pour_out": "AN"}  # fmt: skip
    for i, sample in enumerate(("TR00001-01", "TR00001-02")):
        values[f"sample_{i}"] = sample
        for n in (1, 2, 3):
            k = f"c_{i}_{n}"
            values |= {f"{k}_tare": "2.0000", f"{k}_cap": "0.5000", f"{k}_gross": gross, f"{k}_final": final}
        values |= {f"c_{i}_3_standard": f"50 ppb|{TODAY.isoformat()}", f"c_{i}_3_volume": "10",
                   f"c_{i}_3_pipette": "P100-1"}  # fmt: skip
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
        "2026-10-10T09:00:00+00:00,PB-P1,,,,"
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

    query = {"code": "PB-P1", "method": "evaporation", "analysis": "10_elements_icpms"}
    grid = client.get("/prep/new-batch", params={**query, "samples": "TR00001-01\nTR00001-02"})
    assert grid.status_code == 200 and "TR00001-02" in grid.text and "50 ppb" in grid.text

    saved = client.post("/prep/batches", data=_grid(token))
    assert saved.status_code == 200 and "Prep batch PB-P1 recorded." in saved.text
    assert "0.06" in saved.text and "Send to results" in saved.text  # (2.6 − 2) ÷ (12.5 − 2 − 0.5)

    sent = client.post("/prep/batches/PB-P1/send", data={"csrf_token": token})
    assert sent.status_code == 200 and "PB-P1 sent to results." in sent.text
    registered = results_batch(prep, "PB-P1")
    assert registered.prep_method == "evaporation"
    assert registered.prep_dilution(SampleRef.parse("TR00001-01"), ReadingRole.CUP, 1) == Decimal("0.06")
    assert registered.spikes[0].added.value == Decimal("0.05") and registered.spikes[0].added.unit == "ppb"
    prep.rollback()  # the test's read ends before the app writes again (SQLite locks)

    run_list = client.get("/prep/run-list.csv", params={"code": "PB-P1"})
    assert run_list.status_code == 200 and run_list.headers["content-type"].startswith("text/csv")
    assert "TR00001-01 1,cup,PB-P1,TR00001-01,1,0.06" in run_list.text

    login_api(client, "staff@lab.example")
    imported = upload(client, _readings())
    assert imported.status_code == 201, imported.json()
    readings = evaluation_input(prep, "PB-P1").readings
    assert {r.dilution for r in readings if r.sample is not None} == {Decimal("0.06")}
    prep.rollback()

    # A weighing was misread: correct the batch, send the correction; slim-data re-evaluates.
    batch = client.get("/api/prep/batches/PB-P1").json()
    body = batch["batch"]
    body["samples"][0]["cups"][1]["weighing"]["final"] = "2.7000"
    fixed = client.post(
        "/api/prep/batches", json={"batch": body, "expected_version": 1, "reason": "cup 2 final misread"}
    )
    assert fixed.status_code == 201 and fixed.json()["version"] == 2
    resent = client.post("/api/prep/batches/PB-P1/send")
    assert resent.status_code == 200, resent.json()
    assert resent.json()["changed"] == 1 and resent.json()["evaluated"]["new_versions"] >= 1
    later = evaluation_input(prep, "PB-P1").readings
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
    refused = client.post("/prep/batches", data=_grid(token, gross="2.1000"))  # gross below tare + cap
    assert refused.status_code == 422 and "Nothing was recorded" in refused.text
    assert "gross must exceed tare and cap" in refused.text and 'value="2.1000"' in refused.text
    assert client.get("/api/prep/batches/PB-P1").status_code == 404


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
    client.post("/prep/batches", data=_grid(token))
    client.post("/prep/batches/PB-P1/send", data={"csrf_token": token})

    edit = client.get("/prep/batches/PB-P1/edit")
    assert edit.status_code == 200 and "Correct prep batch PB-P1 (version 1)" in edit.text
    assert (
        'name="c_0_2_final" value="2.6000"' in edit.text and 'name="expected_version" value="1"' in edit.text
    )

    fixed = _grid(token) | {"expected_version": "1", "c_0_2_final": "2.7000"}
    no_reason = client.post("/prep/batches", data=fixed)
    assert no_reason.status_code == 422 and "a correction needs a reason" in no_reason.text

    done = client.post("/prep/batches", data=fixed | {"reason": "cup 2 final misread"})
    assert done.status_code == 200 and "PB-P1 corrected (version 2)" in done.text
    assert "Send the correction to results" in done.text and "cup 2 final misread" in done.text
    batch = client.get("/api/prep/batches/PB-P1").json()
    assert batch["version"] == 2 and batch["batch"]["prep"]["analyst"] == "JM"  # who prepped it stays

    stale = client.post("/prep/batches", data=fixed | {"reason": "again"})
    assert stale.status_code == 409 and "the latest is 2" in stale.text

    sent = client.post("/prep/batches/PB-P1/send", data={"csrf_token": token})
    assert "PB-P1 correction sent (1 changed)." in sent.text


def test_editing_an_unknown_batch_is_404(client, prep):
    login(client, "staff@lab.example")
    assert client.get("/prep/batches/PB-X/edit").status_code == 404
