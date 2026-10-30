"""Invoicing through V5 (slim-data D-83, D-84), on the seeded demo scenarios: a complete report becomes
one sales order priced from the demo price list, the TR moves to invoiced, and the CSV carries it."""

import csv
import io
from decimal import Decimal

import pytest
import slim_invoicing
from fastapi.testclient import TestClient
from sqlalchemy import select

from slim_lims import invoices
from slim_lims.demo_prices import BASE, seed_demo_quotes
from slim_lims.domain import TRStatus
from slim_lims.models import User
from slim_lims.reports import plain_renderer
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.services import Conflict, SubmissionService
from tests.conftest import make_settings
from tests.test_demo_scenarios import THIRTY_SIX, reference, seeded, specs  # noqa: F401 (fixtures)


def sue(db):
    return db.scalars(select(User).where(User.email == "sue@demo.example")).one()


def test_the_demo_invoices_are_sales_orders_priced_from_the_price_list(db, seeded):
    order = slim_invoicing.sales_order_for(db, seeded["clean_pass"])  # 36 Elements, one sample
    assert order is not None and order.created_by == sue(db).initials
    [line] = order.line_items
    assert line.quantity == 1 and line.item.startswith("36 Elements - ")
    assert line.rate >= BASE["Metals"] * Decimal(
        "0.9"
    )  # customer A's price: 10 % off the five-day base or more
    assert {o.tr_number for o in slim_invoicing.sales_orders(db)} == {
        seeded["clean_pass"],
        seeded["rerun_exception"],
    }
    assert not seed_demo_quotes(db, None, None)  # the price list is published once


def test_a_complete_report_is_invoiced_once_and_only_what_was_reported(db, seeded, reference):
    service = SubmissionService(TRSubmissionRepository(db), reference)
    tr = seeded["amended"]  # an assay, reported, then amended: billed once
    invoices.mark_sent(db, service, tr, sue(db), reference)
    order = slim_invoicing.sales_order_for(db, tr)
    assert sum(li.quantity for li in order.line_items) == 1 and order.total > Decimal(0)
    assert service.get(tr, sue(db)).status is TRStatus.INVOICED
    with pytest.raises(Conflict, match="invoice a complete report"):  # invoiced now
        invoices.mark_sent(db, service, tr, sue(db), reference)
    with pytest.raises(Conflict, match="invoice a complete report"):
        invoices.mark_sent(db, service, seeded["scale_fault"], sue(db), reference)  # still in progress


def test_billable_requests_carry_only_reported_analyses(db, seeded, reference):
    service = SubmissionService(TRSubmissionRepository(db), reference)
    released = service.get(seeded["line_by_line"], sue(db))  # released, but no report issued yet
    request = invoices.billable(db, released, reference, service.lab_today())
    assert not request.complete and request.reported == frozenset() and request.billable_samples() == ()
    amended = service.get(seeded["amended"], sue(db))  # its original and its amendment show one result
    assert invoices.billable(db, amended, reference, service.lab_today()).reported == {
        (s.position, a) for s in amended.samples for a in s.analysis_ids
    }


def test_invoice_pages_and_the_accounting_csv(engine, db, seeded, reference):
    from slim_lims.main import create_app

    app = create_app(
        make_settings(str(engine.url), demo_personas=True),
        engine=engine,
        reference=reference,
        specs=specs,
        report_renderer=plain_renderer,
    )
    staff = str(sue(db).id)
    customer = db.scalars(select(User).where(User.is_persona.is_(True), User.customer_id.is_not(None))).one()
    customer_id = str(customer.id)
    db.rollback()  # end this session's read: SQLite would lock the app's writes

    def as_persona(client, user_id):
        token = client.get("/demo/personas").text.split('name="csrf_token" value="')[1].split('"')[0]
        client.post("/demo/continue-as", data={"csrf_token": token, "user_id": user_id})

    with TestClient(app) as client:
        as_persona(client, staff)
        listing = client.get("/invoices").text
        assert seeded["clean_pass"] in listing and "Download CSV" in listing
        page = client.get(f"/requests/{seeded['clean_pass']}/invoice").text
        assert "Invoice for" in page and "36 Elements" in page and "Paid " in page
        assert "Default price list" in listing and "Price lists" in listing
        assert client.get(f"/requests/{seeded['amended']}/invoice").status_code == 404  # not invoiced
        response = client.get("/invoices.csv")
        assert response.headers["content-type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0][0] == "Customer" and {r[-1] for r in rows[1:]} == {
            seeded["clean_pass"],
            seeded["rerun_exception"],
        }
    with TestClient(app) as client:
        as_persona(client, customer_id)
        assert client.get(f"/requests/{seeded['clean_pass']}/invoice").status_code == 200  # their own
        assert client.get(f"/requests/{seeded['cups_disagree']}/invoice").status_code == 404  # customer B's
        assert client.get("/invoices", follow_redirects=False).status_code == 403
        assert client.get("/invoices.csv", follow_redirects=False).status_code == 403


def test_customer_as_own_quote_comes_first_and_sets_its_terms(db, seeded, reference):
    """Slice 3: customer A's negotiated price (10 % off) and 45-day terms beat the default price list."""
    order = slim_invoicing.sales_order_for(db, seeded["clean_pass"])  # customer A, 36 Elements
    [line] = order.line_items
    assert order.payment_terms == 45
    default, *own = invoices.price_lists(db, reference)
    assert default.is_default and [q.customer_ids for q in own] == [(order.customer_id,)]
    listed = next(
        p.regular_price
        for li in default.line_items
        if li.analysis_ids == (THIRTY_SIX,)
        for p in li.prices
        if p.processing_time == line.processing_time
    )
    assert line.rate < listed  # its own price, not the list's


REQUESTER, PO = "Name of Requester", "PO Number"


def test_i1_a_formula_typed_on_the_request_form_reaches_accounting_as_text(db, seeded):
    """App review 2026-10-30b I1: the requester and PO number are the customer's own words."""
    from sqlalchemy import text

    formula = '=HYPERLINK("http://x","x")'
    db.execute(
        text("UPDATE inv_sales_orders SET requester_name = :r, po_number = :p"),
        {"r": formula, "p": "@SUM(1)"},
    )
    db.commit()
    rows = list(csv.reader(io.StringIO(invoices.accounting_csv(db))))
    header, body = rows[0], rows[1:]
    requester, po = header.index(REQUESTER), header.index(PO)
    assert body and all(r[requester] == "'" + formula and r[po] == "'@SUM(1)" for r in body)
    assert invoices._as_text("-12.5") == "-12.5" and invoices._as_text("-x") == "'-x"
    assert invoices._as_text("ok") == "ok"
