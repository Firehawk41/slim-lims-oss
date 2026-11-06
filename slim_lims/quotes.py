"""Quotes, the host side (slim-data D-139). The quotes themselves are invoicing V5's: what a customer (or
everyone, for the default price list) pays per set of analyses and processing time, from one day to
another. V5 never edits a quote and prices invoices from the ones valid on the day; an expired quote just
stops pricing and the default list prices the rest.

The host adds what V5 doesn't hold:
* the screens: list (in force, expiring soon, expired), make one (or replace one: a new quote prefilled
  from the old), view and print;
* a customer's request for a quote (`quote_requests`) and what became of it, answered with a quote or
  declined (`quote_request_events`; both append-only);
* each quote's PDF, made once, and its sendings to the customer, kept in the outbox (D-123, D-124);
* the quote a TR named (`tr_quotes`), for reference only.

Withdrawing a quote before its expiry needs V5 to record it (D-139 #3): not built here.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
from pathlib import Path

import slim_invoicing
from slim_invoicing.persistence import tables as inv_tables
from slim_invoicing.persistence.repositories import default_quotes, quotes_for_customer, save_quote
from slim_report_engine.lab_identity import LAB_HEADER_LINES
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.models import (
    QuoteDeliveryRow,
    QuotePdfRow,
    QuoteRequestEventRow,
    QuoteRequestRow,
    TRQuoteRow,
    User,
)
from slim_lims.reference import ReferenceData

PT = slim_invoicing.ProcessingTime
OUTBOX = "outbox"  # stored, not transmitted (D-124)
ANSWERED, DECLINED = "answered", "declined"
MONEY = re.compile(r"^\d{1,7}(\.\d{1,2})?$")
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class QuoteRefused(ValueError):
    """The form or the action can't stand; `problems` says where and why."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def number(quote_id: int) -> str:
    return f"Q{quote_id:05d}"


# ---------------------------------------------------------------- reading quotes


def all_quotes(session: Session) -> list[slim_invoicing.Quote]:
    """Every quote, newest first. V5 reads quotes per customer, so ask it for every customer one names."""
    found = {q.id: q for q in default_quotes(session)}
    for customer_id in set(session.scalars(select(inv_tables.quote_customers.c.customer_id))):
        found.update((q.id, q) for q in quotes_for_customer(session, customer_id))
    return sorted(found.values(), key=lambda q: q.id, reverse=True)


def quote(session: Session, quote_id: int) -> slim_invoicing.Quote | None:
    return next((q for q in all_quotes(session) if q.id == quote_id), None)


def visible_to(q: slim_invoicing.Quote, user: User) -> bool:
    """Staff see every quote; a customer their own and the default price list."""
    return user.is_staff or q.is_default or user.customer_id in q.customer_ids


def state(q: slim_invoicing.Quote, today: date, warn_days: int) -> str:
    if q.expire_date < today:
        return "expired"
    if q.effective_date > today:
        return "not_yet"
    return "expiring" if q.expire_date <= today + timedelta(days=warn_days) else "in_force"


STATE_LABELS = {
    "in_force": "In force",
    "expiring": "Expiring soon",
    "not_yet": "Not yet in force",
    "expired": "Expired",
}


def own_in_force(session: Session, customer_id: int, today: date) -> list[slim_invoicing.Quote]:
    """A customer's own quotes valid today: what a request form may name."""
    return [q for q in quotes_for_customer(session, customer_id) if q.valid_on(today)]


# ---------------------------------------------------------------- making a quote from the form


@dataclass
class LineIn:
    analysis_ids: list[int] = field(default_factory=list)
    bulk_min: str = ""
    prices: dict[int, tuple[str, str]] = field(default_factory=dict)  # PT value -> (regular, bulk)


@dataclass
class QuoteForm:
    """The form as typed (strings), so a refused form shows again as it was."""

    customers: list[int] = field(default_factory=list)
    is_default: bool = False
    effective: str = ""
    expire: str = ""
    payment_method: int = int(slim_invoicing.PaymentMethod.PURCHASE_ORDER)
    payment_terms: str = ""
    comments: str = ""
    lines: list[LineIn] = field(default_factory=list)
    request_id: int | None = None
    replaces: int | None = None


def form_from(q: slim_invoicing.Quote, today: date) -> QuoteForm:
    """A new quote's form prefilled from `q` (Replace): its prices, from today, for the same span."""
    span = q.expire_date - q.effective_date
    return QuoteForm(
        customers=list(q.customer_ids),
        is_default=q.is_default,
        effective=today.isoformat(),
        expire=(today + span).isoformat(),
        payment_method=int(q.payment_method),
        payment_terms=str(q.payment_terms or ""),
        comments=q.comments,
        lines=[
            LineIn(
                analysis_ids=list(li.analysis_ids),
                bulk_min=str(li.bulk_price_min or ""),
                prices={
                    int(p.processing_time): (
                        f"{p.regular_price:.2f}",
                        "" if p.bulk_price is None else f"{p.bulk_price:.2f}",
                    )
                    for p in li.prices
                },
            )
            for li in q.line_items
        ],
        replaces=q.id,
    )


def form_for_request(r: QuoteRequestRow, today: date) -> QuoteForm:
    """A new quote's form from a customer's request: their analyses as one line, their processing times."""
    return QuoteForm(
        customers=[r.customer_id],
        effective=today.isoformat(),
        expire=(today + timedelta(days=365)).isoformat(),
        lines=[LineIn(analysis_ids=list(r.analysis_ids), prices={pt: ("", "") for pt in r.processing_times})],
        request_id=r.id,
    )


def _date(text: str, what: str, problems: list[str]) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        problems.append(f"{what}: give a date")
        return None


def _money(text: str, where: str, problems: list[str]) -> Decimal | None:
    text = text.strip().lstrip("$")
    if not text:
        return None
    if not MONEY.match(text):
        problems.append(f"{where}: a price like 120 or 120.50")
        return None
    try:
        return Decimal(text)
    except InvalidOperation:  # pragma: no cover (the pattern already refuses it)
        problems.append(f"{where}: a price like 120 or 120.50")
        return None


def build(form: QuoteForm, reference: ReferenceData, by: str) -> slim_invoicing.Quote:
    """V5's Quote from the form; QuoteRefused lists every problem at once."""
    problems: list[str] = []
    effective = _date(form.effective, "Valid from", problems)
    expire = _date(form.expire, "Valid until", problems)
    if effective and expire and expire < effective:
        problems.append("Valid until: on or after the day it takes effect")
    known_customers = {c.id for c in reference.customer_choices()}
    if not form.is_default and not form.customers:
        problems.append("Customers: choose at least one, or make it the default price list")
    if any(c not in known_customers for c in form.customers):
        problems.append("Customers: unknown customer")
    terms = form.payment_terms.strip()
    if terms and (not terms.isdigit() or int(terms) > 365):
        problems.append("Payment terms: days, 0 to 365")
    known_analyses = {a.id for a in reference.catalog_analyses()}
    lines = []
    for n, line in enumerate(form.lines, 1):
        if not line.analysis_ids:
            continue
        where = f"Line {n}"
        if any(a not in known_analyses for a in line.analysis_ids):
            problems.append(f"{where}: unknown analysis")
        bulk_min = line.bulk_min.strip()
        if bulk_min and (not bulk_min.isdigit() or int(bulk_min) < 2):
            problems.append(f"{where}: bulk from 2 samples or more, or leave it blank")
        prices = []
        for pt_value, (regular_text, bulk_text) in sorted(line.prices.items()):
            label = f"{where}, {PT(pt_value).label}"
            regular = _money(regular_text, label, problems)
            bulk = _money(bulk_text, f"{label} (bulk)", problems)
            if regular is None:
                if bulk is not None:
                    problems.append(f"{label}: a bulk price needs a regular price")
                continue
            if bulk is not None and not bulk_min:
                problems.append(f"{label}: a bulk price needs the bulk minimum")
            prices.append(
                slim_invoicing.QuotePrice(
                    processing_time=PT(pt_value), regular_price=regular, bulk_price=bulk
                )
            )
        if not prices:
            problems.append(f"{where}: price at least one processing time")
            continue
        lines.append(
            slim_invoicing.QuoteLineItem(
                analysis_ids=tuple(sorted(set(line.analysis_ids))),
                bulk_price_min=int(bulk_min) if bulk_min else 0,
                prices=tuple(prices),
            )
        )
    if not lines and not problems:
        problems.append("Lines: at least one line with analyses and a price")
    if problems:
        raise QuoteRefused(problems)
    return slim_invoicing.Quote(
        effective_date=effective,
        expire_date=expire,
        payment_method=slim_invoicing.PaymentMethod(form.payment_method),
        payment_terms=int(terms) if terms else 0,
        is_default=form.is_default,
        customer_ids=() if form.is_default else tuple(sorted(set(form.customers))),
        line_items=tuple(lines),
        created_by=by,
        comments=form.comments.strip(),
    )


def save(session: Session, form: QuoteForm, reference: ReferenceData, user: User) -> slim_invoicing.Quote:
    """Stores the quote in V5; answers the request it came from, if any. Flushes."""
    request = None
    if form.request_id is not None:
        request = session.get(QuoteRequestRow, form.request_id)
        if request is None or request_state(session, request.id) != "open":
            raise QuoteRefused(["That quote request is not open any more."])
        if form.is_default or request.customer_id not in form.customers:
            raise QuoteRefused(["Customers: a quote answering a request is for the customer who asked"])
    saved = save_quote(session, build(form, reference, user.initials or user.email))
    if request is not None:
        session.add(
            QuoteRequestEventRow(request_id=request.id, kind=ANSWERED, quote_id=saved.id, by=user.email)
        )
        session.flush()
    return saved


# ---------------------------------------------------------------- requests


def add_request(
    session: Session,
    user: User,
    analysis_ids: list[int],
    processing_times: list[int],
    samples_estimate: str,
    note: str,
    reference: ReferenceData,
) -> QuoteRequestRow:
    if user.is_staff or user.customer_id is None:
        raise QuoteRefused(["Only a customer asks for a quote."])
    problems = []
    known = {a.id for a in reference.catalog_analyses() if a.portal_selectable}
    analyses = sorted({a for a in analysis_ids if a in known})
    if not analyses:
        problems.append("Analyses: choose at least one")
    times = sorted({t for t in processing_times if t in {int(p) for p in PT}})
    if not times:
        problems.append("Processing times: choose at least one")
    if problems:
        raise QuoteRefused(problems)
    row = QuoteRequestRow(
        customer_id=user.customer_id,
        requested_by=user.email,
        analysis_ids=analyses,
        processing_times=times,
        samples_estimate=samples_estimate.strip()[:100],
        note=note.strip()[:2000],
    )
    session.add(row)
    session.flush()
    return row


def _events(session: Session, request_id: int) -> list[QuoteRequestEventRow]:
    return list(
        session.scalars(
            select(QuoteRequestEventRow)
            .where(QuoteRequestEventRow.request_id == request_id)
            .order_by(QuoteRequestEventRow.id)
        )
    )


def request_state(session: Session, request_id: int) -> str:
    events = _events(session, request_id)
    return events[-1].kind if events else "open"


@dataclass(frozen=True)
class RequestView:
    row: QuoteRequestRow
    state: str
    event: QuoteRequestEventRow | None


def requests(session: Session, customer_id: int | None = None, open_only: bool = False) -> list[RequestView]:
    """Newest first; one customer's, or everyone's (staff)."""
    query = select(QuoteRequestRow).order_by(QuoteRequestRow.id.desc())
    if customer_id is not None:
        query = query.where(QuoteRequestRow.customer_id == customer_id)
    out = []
    for row in session.scalars(query):
        events = _events(session, row.id)
        view = RequestView(row, events[-1].kind if events else "open", events[-1] if events else None)
        if not open_only or view.state == "open":
            out.append(view)
    return out


def decline(session: Session, request_id: int, reason: str, user: User) -> None:
    row = session.get(QuoteRequestRow, request_id)
    if row is None or request_state(session, request_id) != "open":
        raise QuoteRefused(["That quote request is not open."])
    if not reason.strip():
        raise QuoteRefused(["Say why it is declined: the customer sees it."])
    session.add(
        QuoteRequestEventRow(
            request_id=request_id, kind=DECLINED, reason=reason.strip()[:2000], by=user.email
        )
    )
    session.flush()


# ---------------------------------------------------------------- the TR's quote


def name_on_tr(session: Session, submission_id: int, quote_id: int) -> None:
    session.add(TRQuoteRow(submission_id=submission_id, quote_id=quote_id))
    session.flush()


def named_by(session: Session, submission_id: int) -> int | None:
    return session.scalar(select(TRQuoteRow.quote_id).where(TRQuoteRow.submission_id == submission_id))


# ---------------------------------------------------------------- the document, its PDF, sending it


_CSS = Path(__file__).parent / "static" / "css" / "quotes.css"
_PAGE_CSS = """
@page { size: A4; margin: 15mm;
  @bottom-right { content: "Page " counter(page) " of " counter(pages);
    font: 8pt Arial, sans-serif; color: #555; }
}
body { margin: 0; font-family: Calibri, Arial, "DejaVu Sans", sans-serif; font-size: 10pt; }
.quote-doc { padding: 0; margin: 0; }
"""


def pdf_bytes(html: str, title: str) -> bytes:
    """The quote as an A4 PDF, with its own stylesheet (WeasyPrint, as reports, D-123)."""
    from weasyprint import CSS, HTML

    document = (
        f"<!doctype html><html><head><meta charset=utf-8><title>{escape(title)}</title></head>"
        f"<body>{html}</body></html>"
    )
    return HTML(string=document).write_pdf(stylesheets=[CSS(filename=str(_CSS)), CSS(string=_PAGE_CSS)])


def pdf_for(session: Session, q: slim_invoicing.Quote, html: str) -> QuotePdfRow:
    """The quote's PDF, made once and kept (a quote never changes)."""
    existing = session.scalar(select(QuotePdfRow).where(QuotePdfRow.quote_id == q.id))
    if existing is not None:
        return existing
    pdf = pdf_bytes(html, f"Quote {number(q.id)}")
    row = QuotePdfRow(quote_id=q.id, sha256=sha256(pdf).hexdigest(), pdf=pdf)
    session.add(row)
    session.flush()
    return row


def recipients(session: Session, q: slim_invoicing.Quote) -> list[str]:
    """Who a quote goes to by default: whoever asked for it, else the customer's active users."""
    asked = session.scalars(
        select(QuoteRequestRow.requested_by)
        .join(QuoteRequestEventRow, QuoteRequestEventRow.request_id == QuoteRequestRow.id)
        .where(QuoteRequestEventRow.quote_id == q.id)
    ).all()
    if asked:
        return sorted(set(asked))
    users = session.scalars(
        select(User.email).where(User.customer_id.in_(q.customer_ids), User.is_active.is_(True))
    ).all()
    return sorted(set(users))


def parse_addresses(text: str) -> list[str]:
    found = [a.strip() for a in re.split(r"[,;\s]+", text) if a.strip()]
    bad = [a for a in found if not _EMAIL.match(a)]
    if bad or not found:
        raise QuoteRefused(
            [f"To: not an email address: {', '.join(bad)}" if bad else "To: at least one address"]
        )
    return found[:20]


def send(session: Session, q: slim_invoicing.Quote, html: str, to: list[str], user: User) -> QuoteDeliveryRow:
    """Records a sending of the quote (into the outbox, not transmitted); flushes."""
    if q.is_default:
        raise QuoteRefused(["The default price list is not sent: customers see it on their Quotes page."])
    pdf = pdf_for(session, q, html)
    lab = LAB_HEADER_LINES[0]
    body = "\n".join(
        [
            "Hello,",
            "",
            f"Please find attached our quote {number(q.id)}, valid from {q.effective_date:%d %b %Y} "
            f"until {q.expire_date:%d %b %Y}.",
            "You may name it on your Testing Request form.",
            "",
            f"Kind regards,\n{lab}",
        ]
    )
    row = QuoteDeliveryRow(
        quote_id=q.id,
        to_addresses=to,
        subject=f"Quote {number(q.id)} from {lab}",
        body=body,
        pdf_sha256=pdf.sha256,
        sent_by=user.email,
        transport=OUTBOX,
    )
    session.add(row)
    session.flush()
    return row


def deliveries(session: Session) -> list[QuoteDeliveryRow]:
    return list(session.scalars(select(QuoteDeliveryRow).order_by(QuoteDeliveryRow.id.desc())))


def sent_on(session: Session, quote_id: int) -> list[datetime]:
    return list(
        session.scalars(
            select(QuoteDeliveryRow.sent_at)
            .where(QuoteDeliveryRow.quote_id == quote_id)
            .order_by(QuoteDeliveryRow.id)
        )
    )


# ---------------------------------------------------------------- the demo (invented)


def seed_demo_quote_request(session: Session, reference: ReferenceData) -> int:
    """One open request from the demo customer, so the queue isn't empty. Once: skipped if any exists."""
    if session.scalar(select(QuoteRequestRow.id).limit(1)) is not None:
        return 0
    customer = session.scalars(
        select(User).where(User.role == "customer", User.customer_id.is_not(None)).order_by(User.email)
    ).first()
    analyses = [a.id for a in reference.catalog_analyses() if a.portal_selectable][:2]
    if customer is None or not analyses:
        return 0
    session.add(
        QuoteRequestRow(
            customer_id=customer.customer_id,
            requested_by=customer.email,
            analysis_ids=analyses,
            processing_times=[int(PT.FIVE_DAYS), int(PT.NEXT_DAY)],
            samples_estimate="about 20 a month",
            note="A new product line from January; we'd like a price for regular testing.",
        )
    )
    session.flush()
    return 1
