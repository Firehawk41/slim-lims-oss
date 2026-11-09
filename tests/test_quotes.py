"""Quote screens (slim-data D-139): quotes are invoicing V5's, never edited; the host makes them from a form,
lists them by state, prints and sends them (the outbox), takes customers' requests and answers or declines
them, and keeps the quote a TR named."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import slim_invoicing
from slim_invoicing.persistence.repositories import save_quote
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from slim_lims import capabilities
from slim_lims import quotes as qs
from slim_lims.models import (
    LAB_ROLE_ANALYST,
    ROLE_STAFF,
    QuoteRequestEventRow,
    QuoteRequestRow,
    User,
)
from slim_lims.security import hash_password
from tests.conftest import PASSWORD, csrf_from, demo_reference, login, login_api
from tests.test_api_submissions import messages, payload

TODAY = date.today()
FIVE = int(slim_invoicing.ProcessingTime.FIVE_DAYS)


def a_quote(db, customers=(1,), start=TODAY, days=365, default=False, price="100"):
    return save_quote(
        db,
        slim_invoicing.Quote(
            effective_date=start,
            expire_date=start + timedelta(days=days),
            is_default=default,
            customer_ids=() if default else customers,
            line_items=(
                slim_invoicing.QuoteLineItem(
                    analysis_ids=(1,),
                    prices=(slim_invoicing.QuotePrice(processing_time=FIVE, regular_price=Decimal(price)),),
                ),
            ),
            created_by="JM",
        ),
    )


def form(**overrides) -> qs.QuoteForm:
    values = dict(
        customers=[1],
        effective=TODAY.isoformat(),
        expire=(TODAY + timedelta(days=90)).isoformat(),
        lines=[qs.LineIn(analysis_ids=[1, 2], bulk_min="10", prices={FIVE: ("120.50", "100")})],
    )
    values.update(overrides)
    return qs.QuoteForm(**values)


def fields(**overrides) -> dict:
    """The new-quote form as posted: one line, five-day prices."""
    body = {
        "customers": ["1"],
        "effective": TODAY.isoformat(),
        "expire": (TODAY + timedelta(days=90)).isoformat(),
        "payment_method": "1",
        "l0_present": "1",
        "l0_analyses": ["1", "2"],
        "l0_bulk_min": "10",
        f"l0_p{FIVE}": "120.50",
        f"l0_b{FIVE}": "100",
        "l1_present": "1",  # a blank line: ignored
    }
    body.update(overrides)
    return body


@pytest.fixture
def analyst(db):
    user = User(
        email="ana@lab.example",
        password_hash=hash_password(PASSWORD),
        role=ROLE_STAFF,
        initials="AN",
        lab_role=LAB_ROLE_ANALYST,
    )
    db.add(user)
    db.commit()
    return user


# ---------------------------------------------------------------- the form, the states


def test_a_form_becomes_v5s_quote_and_every_problem_is_listed_at_once(db):
    reference = demo_reference()
    built = qs.build(form(), reference, "JM")
    line = built.line_items[0]
    assert built.customer_ids == (1,) and line.analysis_ids == (1, 2) and line.bulk_price_min == 10
    assert line.prices[0].regular_price == Decimal("120.50") and line.prices[0].bulk_price == Decimal("100")
    with pytest.raises(qs.QuoteRefused) as refused:
        qs.build(
            form(
                customers=[],
                expire=(TODAY - timedelta(days=1)).isoformat(),
                payment_terms="lots",
                lines=[
                    qs.LineIn(analysis_ids=[99], bulk_min="1", prices={FIVE: ("12.345", "")}),
                    qs.LineIn(analysis_ids=[1], prices={FIVE: ("", "50")}),
                    qs.LineIn(analysis_ids=[2], prices={FIVE: ("50", "40")}),
                ],
            ),
            reference,
            "JM",
        )
    assert refused.value.problems == [
        "Valid until: on or after the day it takes effect",
        "Customers: choose at least one, or make it the default price list",
        "Payment terms: days, 0 to 365",
        "Line 1: unknown analysis",
        "Line 1: bulk from 2 samples or more, or leave it blank",
        "Line 1, Five Days: a price like 120 or 120.50",
        "Line 1: price at least one processing time",
        "Line 2, Five Days: a bulk price needs a regular price",
        "Line 2: price at least one processing time",
        "Line 3, Five Days: a bulk price needs the bulk minimum",
    ]
    with pytest.raises(qs.QuoteRefused, match="at least one line"):
        qs.build(form(lines=[]), reference, "JM")
    assert qs.build(form(customers=[], is_default=True), reference, "JM").customer_ids == ()


def test_states_follow_the_dates_and_replace_prefills_from_today(db):
    q = a_quote(db, days=20)
    assert qs.state(q, TODAY, 30) == "expiring" and qs.state(q, TODAY, 10) == "in_force"
    assert qs.state(q, TODAY + timedelta(days=21), 30) == "expired"
    assert qs.state(q, TODAY - timedelta(days=1), 30) == "not_yet"
    later = TODAY + timedelta(days=5)
    again = qs.form_from(q, later)
    assert again.replaces == q.id and again.effective == later.isoformat()
    assert again.expire == (later + timedelta(days=20)).isoformat() and again.lines[0].prices == {
        FIVE: ("100.00", "")
    }
    assert qs.number(q.id) == f"Q{q.id:05d}" and qs.all_quotes(db) == [q] and qs.quote(db, 999) is None


# ---------------------------------------------------------------- the pages


def test_pricing_staff_make_view_print_and_send_a_quote(client, db, staff_user, customer_user):
    login(client, "staff@lab.example")
    page = client.get("/quotes/new")
    assert page.status_code == 200
    csrf = csrf_from(page.text)
    refused = client.post("/quotes", data=fields(l0_p8="cheap", csrf_token=csrf))
    assert (
        refused.status_code == 422 and "a price like 120" in refused.text and 'value="cheap"' in refused.text
    )
    made = client.post("/quotes", data=fields(csrf_token=csrf), follow_redirects=False)
    assert made.status_code == 303
    q = qs.all_quotes(db)[0]
    db.rollback()  # SQLite: end this read before the app writes
    detail = client.get(made.headers["location"])
    assert qs.number(q.id) in detail.text and "$120.50" in detail.text and "from 10 samples" in detail.text
    assert "jane@acme.example" in detail.text  # the customer's users, by default
    pdf = client.get(f"/quotes/{q.id}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get(f"/quotes/{q.id}/pdf").content == pdf.content  # made once
    client.post(f"/quotes/{q.id}/send", data={"to": "not-an-address", "csrf_token": csrf})
    assert qs.deliveries(db) == []
    db.rollback()  # SQLite: end this read before the app writes
    client.post(f"/quotes/{q.id}/send", data={"to": "jane@acme.example", "csrf_token": csrf})
    db.expire_all()
    sent = qs.deliveries(db)
    assert len(sent) == 1 and sent[0].to_addresses == ["jane@acme.example"] and sent[0].transport == "outbox"
    db.rollback()  # SQLite: end this read before the app writes
    outbox = client.get("/outbox").text
    assert f"Quote {qs.number(q.id)} from" in outbox and f"{qs.number(q.id)}.pdf" in outbox
    listing = client.get("/quotes").text
    assert "In force" in listing and "0 open quote requests" in listing


def test_the_default_price_list_is_never_sent(client, db, staff_user):
    default = a_quote(db, default=True)
    db.commit()
    login(client, "staff@lab.example")
    csrf = csrf_from(client.get("/quotes").text)
    client.post(f"/quotes/{default.id}/send", data={"to": "x@y.example", "csrf_token": csrf})
    assert qs.deliveries(db) == []


def test_replacing_withdraws_the_old_quote_from_the_new_ones_first_day(client, db, staff_user):
    old = a_quote(db, start=TODAY - timedelta(days=30))
    db.commit()
    login(client, "staff@lab.example")
    page = client.get(f"/quotes/new?replaces={old.id}")
    assert f"replacing {qs.number(old.id)}" in page.text and 'value="100.00"' in page.text
    starts = TODAY + timedelta(days=7)
    made = client.post(
        "/quotes",
        data=fields(replaces=str(old.id), effective=starts.isoformat(), csrf_token=csrf_from(page.text)),
        follow_redirects=True,
    )
    assert f"{qs.number(old.id)} is withdrawn from {starts:%d %b %Y}" in made.text
    db.rollback()
    replaced = qs.quote(db, old.id)
    assert replaced.withdrawn_from == starts and replaced.valid_on(TODAY) and not replaced.valid_on(starts)
    assert qs.state(replaced, starts, 30) == "withdrawn" and qs.state(replaced, TODAY, 30) == "in_force"
    db.rollback()
    again = client.post(  # replacing it twice withdraws nothing more
        "/quotes", data=fields(replaces=str(old.id), csrf_token=csrf_from(page.text)), follow_redirects=True
    )
    assert "is withdrawn from" not in again.text and qs.quote(db, old.id).withdrawn_from == starts


def test_a_quote_is_withdrawn_early_with_a_reason_and_then_prices_nothing(
    client, db, staff_user, customer_user
):
    q = a_quote(db, (1,))
    db.commit()
    login(client, "staff@lab.example")
    page = client.get(f"/quotes/{q.id}")
    assert "Withdraw before it expires" in page.text
    csrf = csrf_from(page.text)
    client.post(
        f"/quotes/{q.id}/withdraw", data={"from": TODAY.isoformat(), "reason": "", "csrf_token": csrf}
    )
    assert qs.quote(db, q.id).withdrawn_from is None
    db.rollback()
    early = (TODAY - timedelta(days=1)).isoformat()
    refused = client.post(
        f"/quotes/{q.id}/withdraw",
        data={"from": early, "reason": "x", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert "takes effect on" in refused.text
    done = client.post(
        f"/quotes/{q.id}/withdraw",
        data={"from": TODAY.isoformat(), "reason": "prices revised", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert f"withdrawn from {TODAY:%d %b %Y}" in done.text and "Withdraw before it expires" not in done.text
    assert "Withdrawn" in client.get("/quotes").text
    db.rollback()
    assert qs.own_in_force(db, 1, TODAY) == []  # a request can't name it any more
    db.rollback()
    client.cookies.clear()
    login_api(client, "jane@acme.example")
    refused = client.post("/api/submissions", json=payload(quote_number=qs.number(q.id)))
    assert refused.status_code == 422


def test_only_pricing_staff_make_quotes_and_customers_see_only_theirs(client, db, analyst, customer_user):
    mine, theirs, default = a_quote(db, (1,)), a_quote(db, (2,)), a_quote(db, default=True)
    db.commit()
    login(client, "ana@lab.example")
    assert client.get("/quotes/new").status_code == 403
    assert client.get(f"/quotes/{theirs.id}").status_code == 200  # staff see every quote
    assert "New quote" not in client.get("/quotes").text
    client.cookies.clear()
    login(client, "jane@acme.example")
    assert client.get("/quotes/new").status_code == 403
    assert client.get(f"/quotes/{mine.id}").status_code == 200
    assert client.get(f"/quotes/{default.id}").status_code == 200
    assert client.get(f"/quotes/{theirs.id}").status_code == 404
    assert client.get(f"/quotes/{theirs.id}/pdf").status_code == 404
    listing = client.get("/quotes").text
    assert qs.number(mine.id) in listing and qs.number(theirs.id) not in listing
    assert "Ask for a quote" in listing


def test_a_customer_asks_and_the_lab_answers_or_declines(client, db, staff_user, customer_user):
    login(client, "jane@acme.example")
    csrf = csrf_from(client.get("/quotes/ask").text)
    client.post("/quotes/ask", data={"analyses": ["1"], "csrf_token": csrf})  # no processing time
    assert db.scalars(select(QuoteRequestRow)).all() == []
    db.rollback()  # SQLite: end this read before the app writes
    asked = {
        "analyses": ["1", "3"],
        "times": [str(FIVE)],
        "samples_estimate": "20 a month",
        "csrf_token": csrf,
    }
    client.post("/quotes/ask", data=asked)
    client.post("/quotes/ask", data=asked)
    first, second = db.scalars(select(QuoteRequestRow).order_by(QuoteRequestRow.id)).all()
    assert first.customer_id == 1 and first.analysis_ids == [1, 3] and first.processing_times == [FIVE]
    first, second = SimpleNamespace(id=first.id), SimpleNamespace(id=second.id)
    db.rollback()  # SQLite: end this read before the app writes
    assert "waiting for the lab" in client.get("/quotes").text

    client.cookies.clear()
    login(client, "staff@lab.example")
    queue = client.get("/quotes/requests").text
    assert "20 a month" in queue and "Answer with a quote" in queue
    page = client.get(f"/quotes/new?request={first.id}")
    assert f"answering request {first.id}" in page.text
    csrf = csrf_from(page.text)
    wrong = fields(customers=["2"], request_id=str(first.id), csrf_token=csrf)
    assert "for the customer who asked" in client.post("/quotes", data=wrong).text
    client.post("/quotes", data=fields(request_id=str(first.id), csrf_token=csrf))
    q = qs.all_quotes(db)[0]
    assert qs.request_state(db, first.id) == "answered" and qs.recipients(db, q) == ["jane@acme.example"]
    db.rollback()  # SQLite: end this read before the app writes
    again = client.post("/quotes", data=fields(request_id=str(first.id), csrf_token=csrf))
    assert "not open any more" in again.text
    client.post(f"/quotes/requests/{second.id}/decline", data={"reason": "", "csrf_token": csrf})
    assert qs.request_state(db, second.id) == "open"
    db.rollback()  # SQLite: end this read before the app writes
    client.post(
        f"/quotes/requests/{second.id}/decline", data={"reason": "No TOC at this lab.", "csrf_token": csrf}
    )
    assert qs.request_state(db, second.id) == "declined"
    db.rollback()  # SQLite: end this read before the app writes
    client.post(f"/quotes/requests/{second.id}/decline", data={"reason": "again", "csrf_token": csrf})
    assert len(db.scalars(select(QuoteRequestEventRow)).all()) == 2

    client.cookies.clear()
    login(client, "jane@acme.example")
    mine = client.get("/quotes").text
    assert "answered with" in mine and qs.number(q.id) in mine and "declined: No TOC at this lab." in mine
    assert client.get("/quotes/requests").status_code == 403


def test_a_request_names_one_of_the_customers_quotes_in_force(client, db, customer_user):
    mine, theirs = a_quote(db, (1,)), a_quote(db, (2,))
    expired = a_quote(db, (1,), start=TODAY - timedelta(days=30), days=10)
    db.commit()
    login_api(client, "jane@acme.example")
    for wrong in (qs.number(theirs.id), qs.number(expired.id), "Q-twelve"):
        refused = client.post("/api/submissions", json=payload(quote_number=wrong))
        assert refused.status_code == 422 and any("quote" in m for m in messages(refused))
    made = client.post("/api/submissions", json=payload(quote_number=qs.number(mine.id).lower()))
    assert made.status_code == 201 and "quote_number" not in made.json()  # the contract is unchanged
    assert qs.named_by(db, made.json()["id"]) == mine.id
    db.rollback()  # SQLite: end this read before the app writes
    detail = client.get(f"/requests/{made.json()['tr_number']}")
    assert qs.number(mine.id) in detail.text
    plain = client.post("/api/submissions", json=payload())
    assert plain.status_code == 201 and qs.named_by(db, plain.json()["id"]) is None


def test_expiring_quotes_are_listed_for_staff(client, db, staff_user):
    soon = a_quote(db, days=10)
    a_quote(db, days=300)
    db.commit()
    login(client, "staff@lab.example")
    page = client.get("/quotes").text
    assert "Expiring within 30 days" in page and page.count("Expiring soon") == 1
    assert f"{qs.number(soon.id)}</a>" in page


def test_the_hosts_quote_records_are_append_only(engine, db, customer_user):
    request = QuoteRequestRow(
        customer_id=1, requested_by="jane@acme.example", analysis_ids=[1], processing_times=[FIVE]
    )
    db.add(request)
    db.flush()
    db.add(QuoteRequestEventRow(request_id=request.id, kind="declined", reason="no", by="x"))
    db.commit()
    for statement in (
        update(QuoteRequestEventRow).values(reason="yes"),
        update(QuoteRequestRow).values(note="x"),
    ):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(statement)


def test_supervisors_price_and_the_demo_has_an_open_request(db, staff_user, customer_user):
    assert capabilities.can(db, staff_user, capabilities.PRICING)
    reference = demo_reference()
    assert qs.seed_demo_quote_request(db, reference) == 1 and qs.seed_demo_quote_request(db, reference) == 0
    assert [r.state for r in qs.requests(db, open_only=True)] == ["open"]
