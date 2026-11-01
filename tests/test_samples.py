"""The host side of slim-samples (S-02…S-11): bottles at check-in, the Samples pages, the report's
notes, the prep gate, the board's notice, and the lab's own lists."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from slim_data.service.views import ReportView  # noqa: F401  (the report's notes read this shape)
from slim_prep.domain.problems import PrepRefused
from slim_samples.domain.actor import Actor, Role
from slim_samples.service import custody as _custody

from slim_lims import reports
from slim_lims.prep import refuse_blocked
from slim_lims.samples import HOST_RULES, SEED_SAMPLES_ACTOR, publish_demo_samples
from tests.conftest import csrf_from, login, login_api
from tests.factories import BOTTLES
from tests.test_api_submissions import payload
from tests.test_pages import lab_today, submitted  # noqa: F401  (fixture)


class custody:  # noqa: N801  (reads like the module)
    """slim-samples' service, for a test's own session: each read ends its transaction, so the app's next
    write isn't locked out (SQLite)."""

    def __getattr__(self, name):
        raise AttributeError(name)

    @staticmethod
    def _end(db, value):
        db.rollback()
        return value

    @staticmethod
    def all_containers(db):
        return custody._end(db, _custody.all_containers(db))

    @staticmethod
    def blocked_for(db, sample):
        return custody._end(db, _custody.blocked_for(db, sample))

    dispose = staticmethod(_custody.dispose)


def check_in_form(client, data=None, **extra):
    page = client.get("/requests/TR00001/check-in").text
    return client.post(
        "/requests/TR00001/check-in",
        data={
            "csrf_token": csrf_from(page),
            "date_received": lab_today().isoformat(),
            "received_by": "JM",
            "chemical_1": "2",
            "chemical_2": "3",
            **({} if data is None else data),
            **extra,
        },
    )


def received(client, **bottle_fields):
    login(client, "staff@lab.example")
    response = check_in_form(client, {**BOTTLES, **bottle_fields})
    assert "TR00001 checked in" in response.text, response.text[:800]
    return client


def test_the_check_in_page_asks_for_the_bottles_of_each_sample(submitted):
    login(submitted, "staff@lab.example")
    page = submitted.get("/requests/TR00001/check-in").text
    assert "Bottles received" in page and 'name="b1_0_type"' in page and 'name="b2_0_type"' in page
    assert "PFA bottle" in page and "Leaking" in page and "Receiving bench" in page


def test_check_in_without_bottles_is_refused_and_records_nothing(submitted, db):
    login(submitted, "staff@lab.example")
    response = check_in_form(submitted)
    assert "log at least one container" in response.text
    assert "Check in" in submitted.get("/requests/TR00001").text  # still submitted
    assert custody.all_containers(db) == []


def test_check_in_logs_the_bottles_in_the_same_transaction(submitted, db):
    received(
        submitted,
        b1_0_type="pfa",
        b1_0_count="3",
        b1_0_location="fridge-1",
        b1_0_cond_leaking="1",
        b1_1_type="hdpe",
    )
    first, second, third = custody.all_containers(db)
    assert (first.sample, first.current_count, first.current_type[0], first.current_location) == (
        "TR00001-01",
        3,
        "pfa",
        "fridge-1",
    )
    assert [c.code for c in first.current_conditions] == ["leaking"]
    assert (second.sample, second.position) == ("TR00001-01", 2) and third.sample == "TR00001-02"
    assert first.received_on == lab_today() and first.retention_due == lab_today() + timedelta(days=90)
    page = submitted.get("/requests/TR00001").text
    assert "3 × PFA bottle" in page and "Leaking" in page and "Bottle labels" in page


def test_a_bad_row_names_the_sample_and_row_and_leaves_the_request_submitted(submitted, db):
    login(submitted, "staff@lab.example")
    response = check_in_form(
        submitted, {**BOTTLES, "b2_0_type": "bucket", "b1_1_type": "hdpe", "b1_1_count": "x"}
    )
    assert "sample 1, row 2" in response.text and "a number" in response.text
    assert custody.all_containers(db) == []
    response = check_in_form(submitted, {**BOTTLES, "b2_0_type": "bucket"})
    assert "unknown type" in response.text and custody.all_containers(db) == []


def test_the_machine_contract_is_unchanged_but_accepts_bottles(client, staff_user, customer_user, db):
    login_api(client, "jane@acme.example")
    assert client.post("/api/submissions", json=payload()).status_code == 201
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})
    login_api(client, "staff@lab.example")
    body = {"date_received": "2026-10-07", "chemicals": {"1": 2, "2": 3}}
    rows = {"container_type": "glass", "location": "shelf-a", "count": 2}
    refused = client.post(
        "/api/submissions/TR00001/check-in", json={**body, "containers": {"1": [rows], "2": []}}
    )
    assert refused.status_code == 422 and "containers" in refused.text
    assert custody.all_containers(db) == []
    ok = client.post(
        "/api/submissions/TR00001/check-in", json={**body, "containers": {"1": [rows], "2": [rows]}}
    )
    assert ok.status_code == 200, ok.text
    assert [c.current_count for c in custody.all_containers(db)] == [2, 2]
    got = client.get("/api/samples/TR00001-01").json()
    assert got["containers"][0]["type"] == "glass" and got["warnings"] == {}


def test_samples_checked_in_without_bottles_can_log_them_afterwards(client, staff_user, customer_user, db):
    login_api(client, "jane@acme.example")
    client.post("/api/submissions", json=payload())
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})
    login_api(client, "staff@lab.example")
    done = client.post(
        "/api/submissions/TR00001/check-in",
        json={"date_received": "2026-10-07", "chemicals": {"1": 2, "2": 3}},
    )
    assert done.status_code == 200 and custody.all_containers(db) == []
    assert "No bottles logged." in client.get("/requests/TR00001").text
    page = client.get("/samples/log/TR00001").text
    response = client.post(
        "/samples/log/TR00001", data={"csrf_token": csrf_from(page), "b1_0_type": "hdpe", "b2_0_type": "pfa"}
    )
    assert "bottle row(s) logged" in response.text
    assert "Every sample of TR00001 has its bottles logged." in client.get("/samples/log/TR00001").text


def test_the_pages_and_api_are_staff_only(submitted):
    login_api(submitted, "jane@acme.example")
    for path in (
        "/samples",
        "/samples/rules",
        "/samples/labels/TR00001",
        "/api/samples/due",
        "/api/samples/TR00001-01",
    ):
        assert submitted.get(path).status_code == 403, path


def test_hold_blocks_disposal_and_everything_is_in_the_history(submitted, db):
    received(submitted)
    container = custody.all_containers(db)[0]
    url = f"/samples/containers/{container.id}"
    page = submitted.get(url).text
    assert "Dispose" in page and "Hold" in page
    act = lambda name, **data: submitted.post(  # noqa: E731
        f"{url}/{name}", data={"csrf_token": csrf_from(submitted.get(url).text), **data}
    )
    assert "hold recorded" in act("hold", reason="customer query").text
    assert "on hold: customer query" in act("dispose", method="Vendor pickup", reason="x").text
    assert "Lift hold" in submitted.get(url).text
    assert "release recorded" in act("release", reason="query answered").text
    assert "move recorded" in act("move", location="fridge-1").text
    assert (
        "Not done" in act("dispose", method="Vendor pickup").text
    )  # before the retention date: needs a reason
    assert "dispose recorded" in act("dispose", method="Vendor pickup", reason="customer asked").text
    page = submitted.get(url).text
    assert (
        "Disposed of" in page and "Put on hold" in page and "Hold lifted" in page and "customer asked" in page
    )
    assert "Not done" in act("move", location="shelf-a").text  # a finished bottle takes nothing more


def test_the_board_says_when_bottles_are_past_their_retention_date(submitted):
    login(submitted, "staff@lab.example")
    check_in_form(submitted, {**BOTTLES, "date_received": (lab_today() - timedelta(days=100)).isoformat()})
    board = submitted.get("/").text
    assert "past their retention date" in board
    assert "show=due" in board and "Due for disposal" in submitted.get("/samples?show=due").text


def test_the_lab_sets_its_own_lists_through_the_api(client, staff_user, db):
    login_api(client, "staff@lab.example")
    body = HOST_RULES.model_dump(mode="json")
    body["retention_days"] = 30
    body["conditions"].append({"code": "frozen", "label": "Arrived frozen", "effect": 3})
    response = client.put("/api/samples/rules", json=body)
    assert response.status_code == 200, response.text
    assert "Arrived frozen" in client.get("/samples/rules").text
    renamed = client.put("/api/samples/rules", json={**body, "name": "other"})
    assert renamed.status_code == 422 and "keep the name" in renamed.text
    broken = client.put("/api/samples/rules", json={**body, "locations": []})
    assert broken.status_code == 422


def test_only_quality_or_a_supervisor_publishes_the_lists(client, db):
    from slim_lims.models import LAB_ROLE_ANALYST, ROLE_STAFF, User
    from slim_lims.security import hash_password
    from tests.conftest import PASSWORD

    db.add(
        User(
            email="analyst@lab.example",
            password_hash=hash_password(PASSWORD),
            role=ROLE_STAFF,
            initials="AN",
            lab_role=LAB_ROLE_ANALYST,
        )
    )
    db.commit()
    login_api(client, "analyst@lab.example")
    response = client.put("/api/samples/rules", json=HOST_RULES.model_dump(mode="json"))
    assert response.status_code == 403
    assert "A supervisor publishes" in client.get("/samples/rules").text


# ---------------------------------------------------------------- what the other modules see


def line(position: int, analysis_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        sample=SimpleNamespace(position=position),
        analyte=SimpleNamespace(analysis_id=analysis_id, code="Fe"),
        notes=(),
        shown_flags=(),
        half_delta=None,
        amended=False,
    )


def test_the_report_warns_only_for_the_analyses_the_leaking_bottle_served(reference):
    view = SimpleNamespace(lines=[line(1, 5), line(1, 6), line(2, 5)])
    conditions = {(1, 5): ("Leaking: bottle 1: 2 × HDPE bottle",)}
    html = reports._notes(view, reference, conditions)
    assert html.count("may be compromised") == 1
    assert "Sample 1" in html and "Leaking: bottle 1: 2 × HDPE bottle" in html
    assert reports._notes(view, reference, {(3, 5): ("x",)}) == ""  # a sample not on the report adds nothing
    assert reports._notes(SimpleNamespace(lines=[]), reference) == ""


def test_a_blocking_condition_refuses_prep_while_the_bottle_is_in_the_lab(db):
    from slim_samples.domain.containers import ContainerIn, SampleInfo

    class Lookup:
        def sample(self, sample_id):
            return SampleInfo(sample=sample_id, analyses=frozenset({"icpms", "ic"}), customer="1")

    published = publish_demo_samples(db)
    unsafe = ContainerIn(
        container_type="pfa",
        location="receiving",
        conditions=frozenset({"unsafe"}),
        analyses=frozenset({"icpms"}),
    )
    fine = ContainerIn(container_type="hdpe", location="receiving", analyses=frozenset({"ic"}))
    bottle, _ = _custody.receive(
        db,
        Lookup(),
        "TR00001-01",
        [unsafe, fine],
        actor=SEED_SAMPLES_ACTOR,
        rules=published,
        received_on=lab_today(),
    )
    icpms = SimpleNamespace(analysis="icpms", samples=[SimpleNamespace(sample="TR00001-01")])
    ic = SimpleNamespace(analysis="ic", samples=[SimpleNamespace(sample="TR00001-01")])
    with pytest.raises(PrepRefused, match="blocks icpms"):
        refuse_blocked(db, icpms)
    refuse_blocked(db, ic)  # the other bottle serves it and is fine: only its own analyses are blocked
    actor = Actor(id="x", display="X", roles=frozenset({Role.ANALYST}))
    _custody.dispose(
        db, bottle.id, actor=actor, rules=published, method="Vendor pickup", today=bottle.retention_due
    )
    refuse_blocked(db, icpms)  # gone from the lab: nothing blocks


def test_the_seed_actor_is_quality():
    assert Role.QUALITY in SEED_SAMPLES_ACTOR.roles


# ---------------------------------------------------------------- review guards (2026-10-31)


def test_r1_a_refused_api_check_in_leaves_the_request_submitted(client, staff_user, customer_user, db):
    """The bottles and the check-in are one transaction: refusing the bottles undoes the receipt too."""
    login_api(client, "jane@acme.example")
    client.post("/api/submissions", json=payload())
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})
    login_api(client, "staff@lab.example")
    body = {"date_received": "2026-10-07", "chemicals": {"1": 2, "2": 3}}
    row = {"container_type": "glass", "location": "shelf-a"}
    refused = client.post("/api/submissions/TR00001/check-in", json={**body, "containers": {"1": [row]}})
    assert refused.status_code == 422
    assert client.get("/api/submissions/TR00001").json()["status"] == 1  # still submitted
    assert client.post(
        "/api/submissions/TR00001/check-in", json={**body, "containers": {"1": [row], "2": [row]}}
    )


def test_r2_a_customer_sees_no_bottles_on_their_request(submitted, db):
    received(submitted, b1_0_cond_leaking="1")
    submitted.post("/logout", data={"csrf_token": csrf_from(submitted.get("/requests/TR00001").text)})
    login(submitted, "jane@acme.example")
    page = submitted.get("/requests/TR00001").text
    assert "TR00001" in page and "Leaking" not in page and "bottle" not in page.lower()


def test_r3_bottles_logged_later_keep_the_date_the_request_was_received(
    client, staff_user, customer_user, db
):
    """Retention runs from receipt, not from the day someone got round to logging the bottles."""
    login_api(client, "jane@acme.example")
    client.post("/api/submissions", json=payload())
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})
    login_api(client, "staff@lab.example")
    week_ago = (lab_today() - timedelta(days=7)).isoformat()
    client.post(
        "/api/submissions/TR00001/check-in", json={"date_received": week_ago, "chemicals": {"1": 2, "2": 3}}
    )
    page = client.get("/samples/log/TR00001").text
    client.post(
        "/samples/log/TR00001", data={"csrf_token": csrf_from(page), "b1_0_type": "hdpe", "b2_0_type": "hdpe"}
    )
    first = custody.all_containers(db)[0]
    assert first.received_on.isoformat() == week_ago
    assert first.retention_due == lab_today() - timedelta(days=7) + timedelta(days=90)


def test_r4_a_condition_label_typed_by_the_lab_is_escaped_in_the_report(reference):
    view = SimpleNamespace(lines=[line(1, 5)])
    html = reports._notes(view, reference, {(1, 5): ('<img src=x onerror=alert(1)> "Leaking"',)})
    assert "<img" not in html and "&lt;img" in html
