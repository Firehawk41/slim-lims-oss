"""Signed reports, their PDFs and the outbox (slim-data D-122, D-123, D-124; roadmap phase 4, second half)."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from slim_lims import capabilities
from slim_lims.labtime import LabClock
from slim_lims.models import ReportDeliveryRow, ReportPdfRow, User
from slim_lims.personas import load_personas
from tests.conftest import csrf_from, login_api
from tests.test_results import SAMPLE_2, app, lab, reference, released, staff_api  # noqa: F401 (fixtures)

ISSUE = "/api/results/reports/TR00001/issue"


def _issue(client) -> dict:
    response = client.post(ISSUE)
    assert response.status_code == 201, response.text
    return response.json()


def test_s1_only_a_signatory_signs_and_issues(app, lab):
    """D-122: issuing is signing; an analyst (no signatory capability) is refused, and their page says why."""
    released(app, 1)
    with staff_api(app, "AN") as an:
        refused = an.post(ISSUE)
        assert refused.status_code == 403 and "signatory" in refused.json()["detail"]
        page = an.get("/results/reports/TR00001").text
        assert "Only a signatory may sign and issue a report." in page
        assert "Sign and issue</button>" in page and "disabled>Sign and issue" in page
    with staff_api(app, "RV") as rv:
        assert rv.get("/api/results/reports/TR00001").json()["issues"] == []  # nothing was recorded


def test_s2_a_signatory_without_a_name_cannot_sign(app, lab, db):
    released(app, 1)
    rv = db.scalars(select(User).where(User.initials == "RV")).one()
    rv.full_name = None
    db.commit()
    with staff_api(app, "RV") as client:
        refused = client.post(ISSUE)
        assert refused.status_code == 403 and "no name to sign with" in refused.json()["detail"]


def test_s3_the_issued_report_ends_with_the_signature_and_resigning_is_no_new_issue(app, lab):
    """The block names the signatory, their title and the lab's day of issue, last in the file; the same
    report signed again another day (or by someone else) is refused as unchanged."""
    released(app, 1)
    with staff_api(app, "RV") as rv:
        first = _issue(rv)
        html = rv.get("/requests/TR00001/reports").text
        assert "Authorised by" in html and "RV Example" in html and "Senior Chemist" in html
        assert f"Date of issue: {app.state.lab_clock.today().isoformat()}" in html
        assert first["signed_by"] == "RV Example"
    zone = app.state.lab_clock.zone
    app.state.lab_clock = LabClock(zone, clock=lambda: datetime.now(UTC) + timedelta(days=3))
    with staff_api(app, "JM") as jm:
        again = jm.post(ISSUE)
        assert again.status_code == 409 and "nothing has changed" in again.json()["detail"]
        jm.post(f"{SAMPLE_2}/release", json={})
        second = _issue(jm)  # a changed report: signed by its own signatory (a supervisor, by role)
        assert second["signed_by"] == "Jamie Example"
        page = jm.get("/requests/TR00001/reports").text  # newest first
        assert page.index("Jamie Example") < page.index("RV Example")
        assert "<p>Supervisor</p>" in page  # no title set: the lab role


def test_s4_the_pdf_is_made_at_issue_and_only_its_customer_downloads_it(app, lab, db, customer_user):
    released(app, 1)
    with staff_api(app, "RV") as rv:
        issued = _issue(rv)
        url = f"/requests/TR00001/reports/{issued['issue_id']}.pdf"
        pdf = rv.get(url)
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF") and sha256(pdf.content).hexdigest() == issued["pdf_sha256"]
        assert "TR00001-report" in pdf.headers["content-disposition"]
        assert rv.get("/requests/TR00001/reports/999.pdf").status_code == 404
    row = db.scalars(select(ReportPdfRow)).one()
    assert row.html_sha256 == issued["sha256"] and row.sha256 == issued["pdf_sha256"]
    with TestClient(app) as customer:
        login_api(customer, "jane@acme.example")
        assert customer.get(url).content == pdf.content  # their own request: byte for byte
        assert "Download PDF" in customer.get("/requests/TR00001/reports").text


def test_s4b_another_customer_gets_no_pdf(app, lab, db):
    from slim_lims.models import ROLE_CUSTOMER
    from slim_lims.security import hash_password
    from tests.conftest import PASSWORD

    db.add(
        User(
            email="bob@other.example",
            password_hash=hash_password(PASSWORD),
            role=ROLE_CUSTOMER,
            customer_id=2,
        )
    )
    db.commit()
    released(app, 1)
    with staff_api(app, "RV") as rv:
        issued = _issue(rv)
    with TestClient(app) as other:
        login_api(other, "bob@other.example")
        assert other.get(f"/requests/TR00001/reports/{issued['issue_id']}.pdf").status_code == 404


def test_s5_sending_puts_the_latest_issue_in_the_outbox(app, lab, customer_user):
    """D-124: to the request's results_to / results_cc, the PDF attached by hash, not transmitted; no
    status change; a superseded issue is refused; sending again is another row."""
    released(app, 1)
    with staff_api(app, "RV") as rv:
        first = _issue(rv)
        status = rv.get("/api/submissions/TR00001").json()["status"]
        sent = rv.post(f"/api/results/reports/TR00001/issues/{first['issue_id']}/send")
        assert sent.status_code == 201, sent.text
        body = sent.json()
        assert body["to"] == ["jane@acme.example"] and body["transport"] == "outbox"
        assert body["pdf_sha256"] == first["pdf_sha256"] and "TR00001" in body["subject"]
        assert rv.get("/api/submissions/TR00001").json()["status"] == status  # a delivery is no status

        rv.post(f"{SAMPLE_2}/release", json={})
        second = _issue(rv)
        stale = rv.post(f"/api/results/reports/TR00001/issues/{first['issue_id']}/send")
        assert stale.status_code == 409 and "replaced" in stale.json()["detail"]
        assert rv.post("/api/results/reports/TR00001/issues/999/send").status_code == 409
        rv.post(f"/api/results/reports/TR00001/issues/{second['issue_id']}/send")
        rv.post(f"/api/results/reports/TR00001/issues/{second['issue_id']}/send")  # again: another row

        outbox = rv.get("/outbox").text
        assert outbox.count("demo: not transmitted") == 3 and "jane@acme.example" in outbox
        report = rv.get("/api/results/reports/TR00001").json()
        assert [len(i["sent"]) for i in report["issues"]] == [1, 2]
    with TestClient(app) as customer:
        login_api(customer, "jane@acme.example")
        assert (
            customer.post(f"/api/results/reports/TR00001/issues/{second['issue_id']}/send").status_code == 403
        )
        assert customer.get("/outbox").status_code == 403


def test_s5b_the_screen_sends_with_csrf_and_says_it_was_not_transmitted(app, lab):
    released(app, 1)
    with staff_api(app, "RV") as rv:
        issued = _issue(rv)
        page = rv.get("/results/reports/TR00001").text
        assert "Send to customer" in page and ">not sent</span>" in page  # review L-8
        done = rv.post(
            f"/results/reports/TR00001/issues/{issued['issue_id']}/send", data={"csrf_token": csrf_from(page)}
        )
        assert "in the outbox" in done.text and "not transmitted" in done.text and "Send again" in done.text
        assert ">not sent</span>" not in done.text


def test_s6_pdfs_and_deliveries_are_append_only(app, lab, db):
    released(app, 1)
    with staff_api(app, "RV") as rv:
        issued = _issue(rv)
        rv.post(f"/api/results/reports/TR00001/issues/{issued['issue_id']}/send")
    for table in (ReportPdfRow.__tablename__, ReportDeliveryRow.__tablename__):
        for statement in (f"UPDATE {table} SET tr_number = 'X'", f"DELETE FROM {table}"):
            with pytest.raises(DBAPIError, match="append-only"):
                db.execute(text(statement))
            db.rollback()


def test_s7_the_demo_signatories():
    """Sue (supervisor) and Rob (reviewer, who the scenarios issue as) sign; nobody else does."""
    personas = {p.email.split("@")[0]: p for p in load_personas()}
    signs = {name for name, p in personas.items() if capabilities.SIGNATORY in p.capabilities}
    assert signs == {"sue", "rob"}
    assert all(personas[n].full_name and personas[n].job_title for n in signs)


def test_s8_user_managers_set_the_name_a_person_signs_with(app, lab, db):
    an_id = db.scalar(select(User.id).where(User.initials == "AN"))
    db.rollback()  # the read ends before the app writes (SQLite locks)
    with staff_api(app, "JM") as jm:
        page = jm.get("/admin/users").text
        done = jm.post(
            f"/admin/users/{an_id}/name",
            data={"csrf_token": csrf_from(page), "full_name": " Ana Lyst ", "job_title": "Chemist"},
        )
        assert "signs as Ana Lyst, Chemist" in done.text
    with staff_api(app, "RV") as rv:  # not a user manager: would clear both, if allowed
        rv.post(f"/admin/users/{an_id}/name", data={"csrf_token": csrf_from(rv.get("/").text)})
    an = db.get(User, an_id)
    assert (an.full_name, an.job_title) == ("Ana Lyst", "Chemist")


def test_a2_an_amended_report_after_the_invoice_says_the_invoice_is_unchanged():
    """Review A-2: the amended report's message to an invoiced request's customer says the invoice stands;
    a first report, or an amendment before invoicing, says nothing about it."""
    from types import SimpleNamespace

    from slim_lims.delivery import message_for
    from slim_lims.domain import TRStatus

    def tr(status):
        return SimpleNamespace(tr_number="TR00007", status=status, results_to=("a@x.example",), results_cc=())

    unchanged = "Your invoice for this request is unchanged."
    assert unchanged in message_for(tr(TRStatus.INVOICED), 3, 2, "Sue Example").body
    assert unchanged not in message_for(tr(TRStatus.COMPLETE_REPORT), 3, 2, "Sue Example").body
    assert unchanged not in message_for(tr(TRStatus.INVOICED), 2, None, "Sue Example").body
