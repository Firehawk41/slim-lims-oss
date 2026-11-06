"""The day's board (slim-data D-138): what is due on a day and where it has got to, per section; the
lab's screens, by link and without a login, with customer names only while the lab says no visitors."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from slim_lims import capabilities
from slim_lims import today as t
from slim_lims.domain import ProcessingTime, TRStatus, add_working_days
from slim_lims.models import DisplayModeRow, LabSectionAnalysisRow, User
from slim_lims.personas import sync_personas
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.scope import Scope
from tests.conftest import csrf_from, demo_reference, login, make_settings
from tests.factories import sample, submission
from tests.test_demo_prep import prepped  # noqa: F401 (fixture)
from tests.test_demo_scenarios import TEN, reference, seeded, specs  # noqa: F401 (fixtures)

ALL = Scope(customer_id=None)


def due_day(db, seeded):
    """The day the seeded scenarios are due (all received on one day, three working days)."""
    return TRSubmissionRepository(db).get_by_tr_number(seeded["scale_fault"]).effective_service_date


def where(day_board, tr_number):
    return next(
        (key, card) for key, _, cards in day_board.columns for card in cards if card.tr.tr_number == tr_number
    )


def section(db, reference, name, *codes):
    row = t.add_section(db, name)
    t.set_analyses(db, row, {a.id for a in reference.catalog_analyses() if a.code in codes})
    db.commit()
    return row


# ---------------------------------------------------------------- due dates, stages, columns


def test_due_date_is_receipt_plus_each_samples_working_days_or_the_override():
    received = submission(
        samples=(sample(1, processing_time=ProcessingTime.NEXT_DAY), sample(2)),
        expected_arrival_date=date(2026, 11, 6),  # a Friday
    )
    assert t.due_date(received, ProcessingTime.NEXT_DAY.days) == date(2026, 11, 9)  # an estimate: Monday
    assert t.due_date(received, ProcessingTime.THREE_DAYS.days) == date(2026, 11, 11)
    assert t.due_date(submission(), 3) is None  # no arrival expected: due nobody knows when
    assert t.next_working_day(date(2026, 11, 6)) == date(2026, 11, 9)


def test_the_days_work_by_stage_counts_what_is_left(db, seeded, reference):
    day = due_day(db, seeded)
    board = t.day_board(db, reference, day, ALL)
    assert where(board, seeded["scale_fault"])[0] == "in_review"
    assert where(board, seeded["qc_failed"])[1].work[0].label == "Rerun or exception"  # the board's chip
    line = where(board, seeded["line_by_line"])  # a card sits where its least advanced work is
    assert (
        line[0] == "in_review"
        and line[1].done == 1
        and [w.stage for w in line[1].work]
        == [
            "released",
            "in_review",
        ]
    )
    assert where(board, seeded["clean_pass"])[0] == "released"  # reported and invoiced: done today
    assert (board.total, board.released, board.left, board.late) == (14, 11, 3, 0)
    assert t.day_board(db, reference, day, ALL, day=day - timedelta(days=1)).total == 0  # nothing due before


def test_late_work_stays_until_released_and_done_work_drops_off(db, seeded, reference):
    later = add_working_days(due_day(db, seeded), 1)
    board = t.day_board(db, reference, later, ALL)
    assert {c.tr.tr_number for _, _, cards in board.columns for c in cards} == {
        seeded["scale_fault"],
        seeded["line_by_line"],
        seeded["qc_failed"],
    }  # released work due before the day is not the day's work
    assert board.late == board.total == 3 and all(c.late for _, _, cards in board.columns for c in cards)
    assert [w.stage for w in where(board, seeded["line_by_line"])[1].work] == ["in_review"]


def test_not_arrived_requests_show_on_their_estimated_day(db, seeded, reference):
    day = due_day(db, seeded)
    new = TRSubmissionRepository(db).add(
        submission(
            samples=(sample(1, analysis_ids=(TEN,), processing_time=ProcessingTime.NEXT_DAY),),
            expected_arrival_date=day - timedelta(days=1) if day.weekday() else day - timedelta(days=3),
        )
    )
    db.commit()
    key, card = where(t.day_board(db, reference, day, ALL), new.tr_number)
    assert key == "not_arrived" and card.work[0].label == "Not arrived"


def test_stages_follow_the_facts():
    received, submitted = SimpleNamespace(status=TRStatus.RECEIVED), submission()
    assert t._stage(submitted, [], False, False) == "not_arrived"
    assert t._stage(received, [], False, False) == "received"
    assert t._stage(received, [], False, True) == "in_prep"
    assert t._stage(received, [], True, True) == "on_instrument"  # sent to results: on the run list
    blocked = t.Work(1, 1, "pH", date(2026, 11, 6), "in_prep", "awaiting", False, blocked=True)
    assert blocked.label == "Blocked"


def test_the_prep_book_says_what_is_in_prep(db, prepped):
    assert (f"{prepped}-01", "10_elements_icpms") in t._prep_index(db)


def test_a_section_shows_only_its_analyses(db, seeded, reference):
    day = due_day(db, seeded)
    assay = section(db, reference, "Wet lab", "assay")
    board = t.day_board(db, reference, day, ALL, section=assay)
    assert {c.tr.tr_number for _, _, cards in board.columns for c in cards} == {
        seeded["line_by_line"],
        seeded["amended"],
    }
    assert {w.analysis for w in board.work} == {"Assay"}
    icpms = section(db, reference, "ICP-MS", "assay", "10_elements_icpms")  # takes assay from Wet lab
    assert {w.analysis for w in t.day_board(db, reference, day, ALL, section=icpms).work} == {
        "Assay",
        "10 Elements",
    }
    db.refresh(assay)
    assert assay.analyses == [] and t.section_of(db) == {a.analysis_id: icpms.id for a in icpms.analyses}


def test_sections_refuse_a_blank_or_repeated_name_and_removing_one_frees_its_analyses(db, reference):
    with pytest.raises(t.SectionRefused):
        t.add_section(db, "   ")
    icpms = section(db, reference, "ICP-MS", "10_elements_icpms")
    with pytest.raises(t.SectionRefused):
        t.add_section(db, " ICP-MS ")
    t.add_link(db, "", icpms, "sue@demo.example")
    t.remove_section(db, icpms)
    db.commit()
    assert (
        t.sections(db) == [] and db.scalars(select(LabSectionAnalysisRow)).all() == [] and t.links(db) == []
    )


def test_the_demo_has_three_sections_once(db):
    reference = demo_reference()
    assert t.seed_demo_sections(db, reference) == 3 and t.seed_demo_sections(db, reference) == 0
    by_name = {s.name: {a.analysis_id for a in s.analyses} for s in t.sections(db)}
    assert by_name == {"ICP-MS": {1, 4}, "IC": set(), "Wet lab": {2}}  # TOC (Organics) is in none


# ---------------------------------------------------------------- the names switch and links


def test_names_are_hidden_until_the_lab_says_no_visitors_and_the_switch_is_append_only(engine, db):
    assert t.names_shown(db) is None
    t.set_names_shown(db, True, "sue@demo.example")
    t.set_names_shown(db, False, "rob@demo.example")
    db.commit()
    assert t.names_shown(db).names_shown is False and t.names_shown(db).set_by == "rob@demo.example"
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(update(DisplayModeRow).values(names_shown=True))


def test_a_link_keeps_only_its_hash_and_stops_when_revoked(db):
    link, token = t.add_link(db, "  Lab   TV ", None, "sue@demo.example")
    db.commit()
    assert link.label == "Lab TV" and token not in link.token_sha256 and t.link_for(db, token) == link
    assert (
        t.link_for(db, token + "x") is None
        and t.link_for(db, "") is None
        and t.link_for(db, "a" * 101) is None
    )
    t.revoke_link(db, link, "sue@demo.example", datetime.now(ZoneInfo("UTC")))
    db.commit()
    assert t.link_for(db, token) is None


def test_supervisors_and_whoever_is_granted_set_up_screens(db):
    sync_personas(db)
    sue = db.scalars(select(User).where(User.email == "sue@demo.example")).one()
    ana = db.scalars(select(User).where(User.email == "ana@demo.example")).one()
    assert capabilities.can(db, sue, capabilities.LAB_DISPLAYS)
    assert not capabilities.can(db, ana, capabilities.LAB_DISPLAYS)


# ---------------------------------------------------------------- the pages


class FixedClock:
    def __init__(self, day: date) -> None:
        self.day, self.zone = day, ZoneInfo("America/Chicago")

    def today(self) -> date:
        return self.day

    def now(self) -> datetime:
        return datetime.combine(self.day, time(10, 30), tzinfo=self.zone)

    def local(self, at: datetime) -> datetime:
        return at.astimezone(self.zone)


@pytest.fixture
def day_app(engine, db, seeded, reference):
    from slim_lims.main import create_app

    app = create_app(make_settings(str(engine.url), demo_personas=True), engine=engine, reference=reference)
    app.state.lab_clock = FixedClock(due_day(db, seeded))
    db.rollback()  # SQLite: the app writes; this session must not hold a transaction open
    return app


def choose(client: TestClient, db, email: str) -> None:
    persona = db.scalars(select(User).where(User.email == email)).one()
    token = csrf_from(client.get("/demo/personas").text)
    response = client.post("/demo/continue-as", data={"user_id": str(persona.id), "csrf_token": token})
    db.rollback()
    assert response.status_code in (200, 303)


def test_staff_see_the_day_with_names_and_set_up_sections_and_screens(day_app, db, seeded):
    with TestClient(day_app) as client:
        choose(client, db, "sue@demo.example")
        page = client.get("/today")
        assert page.status_code == 200 and "3 left" in page.text and "11 of 14 released" in page.text
        assert seeded["scale_fault"] in page.text and "A · " in page.text  # staff always see the customer
        assert "Sections and screens" in page.text and "hide customer names" in page.text
        csrf = csrf_from(page.text)
        assert client.get("/today?section=99").status_code == 404
        setup = client.get("/today/setup")
        assert setup.status_code == 200 and "No sections yet" in setup.text
        client.post("/today/sections", data={"name": "Wet lab", "csrf_token": csrf})
        wet = t.sections(db)[0]
        wet_id = wet.id
        db.rollback()
        assay = next(a.id for a in day_app.state.reference.catalog_analyses() if a.code == "assay")
        client.post(
            f"/today/sections/{wet_id}/analyses", data={"analysis": [str(assay), "x"], "csrf_token": csrf}
        )
        page = client.get(f"/today?section={wet_id}")
        assert "Wet lab: due today" in page.text and seeded["scale_fault"] not in page.text
        assert seeded["line_by_line"] in page.text
        tomorrow = client.get(f"/today?section={wet_id}&day=next").text  # today's unfinished work carries on
        assert "1 left" in tomorrow and "0 of 1 released" in tomorrow
        made = client.post(
            "/today/links", data={"section": str(wet_id), "label": "Wet lab TV", "csrf_token": csrf}
        )
        flashed = made.text
        url = flashed.split("Screen link Wet lab TV: ")[1].split(" ")[0]
        token = url.rsplit("/", 1)[1]

    with TestClient(day_app) as screen:  # a screen: no login, no persona
        shown = screen.get(f"/display/{token}")
        assert shown.status_code == 200 and shown.headers["cache-control"] == "no-store"
        assert 'http-equiv="refresh" content="60"' in shown.text and "customer names hidden" in shown.text
        assert seeded["line_by_line"] in shown.text and "A · " not in shown.text  # anonymous by default
        assert "<form" not in shown.text and "/requests/" not in shown.text  # nothing to click
        assert screen.get("/display/not-a-token").status_code == 404

    with TestClient(day_app) as client:
        choose(client, db, "ana@demo.example")  # any staff member flips the switch; only setup needs more
        csrf = csrf_from(client.get("/today").text)
        assert client.get("/today/setup").status_code == 403
        assert client.post("/today/sections", data={"name": "IC", "csrf_token": csrf}).status_code == 403
        client.post("/today/names", data={"shown": "1", "csrf_token": csrf})
        with TestClient(day_app) as screen:
            assert "A · " in screen.get(f"/display/{token}").text  # no visitors: names on the screen
        client.post("/today/names", data={"shown": "0", "csrf_token": csrf})
        with TestClient(day_app) as screen:
            assert "A · " not in screen.get(f"/display/{token}").text

    with TestClient(day_app) as client:
        choose(client, db, "sue@demo.example")
        csrf = csrf_from(client.get("/today").text)
        link_id = t.links(db)[0].id
        db.rollback()
        client.post(f"/today/links/{link_id}/revoke", data={"csrf_token": csrf})
        assert client.post("/today/links/999/revoke", data={"csrf_token": csrf}).status_code == 404
        assert "Revoked" in client.get("/today/setup").text
        client.post(f"/today/sections/{wet_id}/delete", data={"csrf_token": csrf})
        assert t.sections(db) == []
    with TestClient(day_app) as screen:
        assert screen.get(f"/display/{token}").status_code == 404


def test_customers_never_see_the_days_board(client, customer_user):
    assert login(client, "jane@acme.example").status_code == 303
    assert client.get("/today").status_code == 403
    assert client.get("/today/setup").status_code == 403


def test_the_tables_are_in_the_migrations(engine):
    with engine.connect() as connection:
        for table in ("lab_sections", "lab_section_analyses", "display_links", "display_modes"):
            connection.execute(text(f"SELECT COUNT(*) FROM {table}"))
