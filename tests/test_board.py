"""The lab board (spec §13), on the seeded demo scenarios: columns follow what happened, chips say
what each sample × analysis needs, and each role sees less than the supervisor."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from slim_lims import board as b
from slim_lims.domain import TRStatus
from slim_lims.models import User
from slim_lims.reports import plain_renderer
from slim_lims.repositories import TRSubmissionRepository
from tests.conftest import make_settings
from tests.factories import sample, submission
from tests.test_demo_scenarios import TEN, reference, seeded, specs  # noqa: F401 (fixtures)


def persona(db, email):
    return db.scalars(select(User).where(User.email == email)).one()


def where(columns, tr_number):
    return next((key, card) for key, _, cards in columns for card in cards if card.tr.tr_number == tr_number)


def test_columns_follow_what_happened(db, seeded, reference):
    TRSubmissionRepository(db).add(submission(samples=(sample(1, analysis_ids=(TEN,)),)))  # TR00010: expected
    db.commit()
    columns = b.board(db, persona(db, "sue@demo.example"), reference)
    assert where(columns, "TR00010")[0] == "expected"
    assert where(columns, "TR00010")[1].chips[0].state == "expected"
    assert where(columns, seeded["scale_fault"])[0] == "in_analysis"  # nothing reviewed yet
    assert {c.state for c in where(columns, seeded["scale_fault"])[1].chips} == {"to_review"}
    assert where(columns, seeded["cups_disagree"])[0] == "ready_to_report"  # released, never issued
    assert where(columns, seeded["line_by_line"])[0] == "ready_to_report"  # sample 1 could go out now
    states = [c.state for c in where(columns, seeded["line_by_line"])[1].chips]
    assert states == ["released", "to_review"]
    clean = where(columns, seeded["clean_pass"])  # the demo invoices it and records payment
    assert clean[0] == "paid" and {c.state for c in clean[1].chips} == {"reported"}
    assert clean[1].batches == [f"PB-{seeded['clean_pass'][2:]}"] and clean[1].invoice.paid_on is not None
    assert where(columns, seeded["rerun_exception"])[0] == "invoice_sent"
    assert where(columns, seeded["amended"])[0] == "to_invoice"


def test_needs_me_narrows_by_lab_role(db, seeded, reference):
    def mine(email):
        return {card.tr.tr_number for _, _, cards in b.board(db, persona(db, email), reference, mine=True)
                for card in cards}

    assert seeded["scale_fault"] in mine("rob@demo.example")  # reviews it
    assert seeded["amended"] not in mine("rob@demo.example")
    # to invoice, payment to chase, review; a paid TR needs nobody
    assert {seeded["amended"], seeded["rerun_exception"], seeded["scale_fault"]} <= mine("sue@demo.example")
    assert seeded["clean_pass"] not in mine("sue@demo.example")
    assert mine("ana@demo.example") == set()  # nothing awaits readings in the demo


def test_supervisor_board_has_every_action_and_records_invoice_and_payment(engine, db, seeded, reference):
    from slim_lims.main import create_app

    app = create_app(make_settings(str(engine.url), demo_personas=True), engine=engine, reference=reference,
                     specs=specs, report_renderer=plain_renderer)
    sue = str(persona(db, "sue@demo.example").id)
    db.rollback()  # end this session's read: SQLite would lock the app's write
    with TestClient(app) as client:
        page = client.get("/demo/personas").text
        token = page.split('name="csrf_token" value="')[1].split('"')[0]
        client.post("/demo/continue-as", data={"csrf_token": token, "user_id": sue})
        board = client.get("/").text
        for label in ("Lab board", "Issue report", "Reports", "Invoice sent", "Mark paid", "Upload results", "To review"):
            assert label in board, label
        token = board.split('name="csrf_token" value="')[1].split('"')[0]
        tr = seeded["amended"]
        response = client.post(f"/requests/{tr}/invoice", data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
        assert client.post(f"/requests/{tr}/invoice/paid", data={"csrf_token": token}).status_code == 200
        again = client.post(f"/requests/{tr}/invoice/paid", data={"csrf_token": token}).text
        assert "was paid on" in again  # refused, flashed
    db.expire_all()
    assert TRSubmissionRepository(db).get_by_tr_number(tr).status == TRStatus.INVOICED  # payment isn't a status
    assert where(b.board(db, persona(db, "sue@demo.example"), reference), tr)[0] == "paid"


def test_customers_see_their_own_requests_in_plain_columns(engine, db, seeded, reference):
    from slim_lims.main import create_app

    app = create_app(make_settings(str(engine.url), demo_personas=True), engine=engine, reference=reference,
                     specs=specs, report_renderer=plain_renderer)
    customer = db.scalars(select(User).where(User.is_persona.is_(True), User.customer_id.is_not(None))).one()
    columns = b.board(db, customer, reference)
    assert [title for _, title, _ in columns] == ["Not arrived yet", "At the lab", "Results published",
                                                  "Invoice sent", "Paid"]
    assert where(columns, seeded["amended"])[0] == "published" and where(columns, seeded["clean_pass"])[0] == "paid"
    shown = {card.tr.tr_number for _, _, cards in columns for card in cards}
    assert seeded["cups_disagree"] not in shown  # customer B's
    assert seeded["clean_pass"] in shown
    with TestClient(app) as client:
        page = client.get("/demo/personas").text
        token = page.split('name="csrf_token" value="')[1].split('"')[0]
        client.post("/demo/continue-as", data={"csrf_token": token, "user_id": str(customer.id)})
        board = client.get("/").text
        assert "My requests" in board and "Mark paid" not in board and "chip--" not in board
        assert "Upload results" not in board and seeded["cups_disagree"] not in board
        reports = client.get(f"/requests/{seeded['amended']}/reports").text  # their published reports
        assert "Report " in reports and "amends report" in reports and "verified: matches what was issued" in reports
        assert reports.count("verified: matches") == 2  # each issue, exactly as issued
        assert client.get(f"/requests/{seeded['cups_disagree']}/reports").status_code == 404  # not theirs
        assert client.post(f"/requests/{seeded['amended']}/invoice",
                           data={"csrf_token": board.split('name="csrf_token" value="')[1].split('"')[0]},
                           follow_redirects=False).status_code == 403
