"""Quote screens (slim-data D-139): /quotes for staff and customers, making and replacing a quote (the
*pricing* capability), viewing and printing one, sending it to the outbox, and customers' quote requests.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from slim_lims import capabilities, quotes
from slim_lims.api import get_reference
from slim_lims.auth import RequireStaff, RequireUser
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.models import QuoteRequestRow, User
from slim_lims.reference import ReferenceData
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render, templates

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]
Clock = Annotated[LabClock, Depends(lab_clock)]
BLANK_LINES = 3
PT_CHOICES = list(quotes.PT)


async def form_lists(request: Request) -> dict[str, list[str]]:
    """Every field as a list of its values (a multi-select sends several)."""
    form = await request.form()
    out: dict[str, list[str]] = {}
    for key, value in form.multi_items():
        if isinstance(value, str):
            out.setdefault(key, []).append(value)
    return out


FormLists = Annotated[dict[str, list[str]], Depends(form_lists)]


def _one(form: dict[str, list[str]], key: str) -> str:
    return (form.get(key) or [""])[-1]


def _ints(values: list[str]) -> list[int]:
    return [int(v) for v in values if v.strip().isdigit()]


def _pricing(db: Session, user: User) -> None:
    if not capabilities.can(db, user, capabilities.PRICING):
        raise HTTPException(status_code=403, detail="Making and sending quotes needs the pricing capability.")


def _quote_or_404(db: Session, quote_id: int, user: User):
    found = quotes.quote(db, quote_id)
    if found is None or not quotes.visible_to(found, user):
        raise HTTPException(status_code=404, detail="No such quote.")
    return found


def _document(q, reference: ReferenceData) -> str:
    """The quote as the customer receives it (HTML; the PDF is made from it)."""
    return templates.get_template("quotes/_document.html").render(
        q=q, number=quotes.number(q.id), reference=reference, lab=quotes.LAB_HEADER_LINES
    )


# ---------------------------------------------------------------- the list


@router.get("/quotes", name="quotes_home")
def quotes_home(request: Request, user: RequireUser, db: DB, reference: Reference, clock: Clock):
    today, warn = clock.today(), request.app.state.settings.quote_expiry_warning_days
    shown = [q for q in quotes.all_quotes(db) if quotes.visible_to(q, user)]
    rows = [(q, quotes.state(q, today, warn)) for q in shown]
    return render(
        request,
        "quotes/index.html",
        {
            "rows": rows,
            "expiring": [(q, s) for q, s in rows if s == "expiring"],
            "labels": quotes.STATE_LABELS,
            "warn_days": warn,
            "requests": quotes.requests(db, customer_id=None if user.is_staff else user.customer_id),
            "can_price": user.is_staff and capabilities.can(db, user, capabilities.PRICING),
            "number": quotes.number,
            "reference": reference,
        },
    )


# ---------------------------------------------------------------- making one


def _form_page(request: Request, form: quotes.QuoteForm, reference: Reference, problems=(), status=200):
    lines = form.lines + [quotes.LineIn() for _ in range(BLANK_LINES)]
    return render(
        request,
        "quotes/new.html",
        {
            "form": form,
            "lines": lines,
            "problems": list(problems),
            "customers": reference.customer_choices(),
            "analyses": reference.catalog_analyses(),
            "times": PT_CHOICES,
            "methods": list(quotes.slim_invoicing.PaymentMethod),
            "number": quotes.number,
        },
        status,
    )


@router.get("/quotes/new", name="new_quote")
def new_quote(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
    replaces: str = "",
):
    _pricing(db, user)
    today = clock.today()
    asked = request.query_params.get("request", "")
    if replaces.isdigit():
        form = quotes.form_from(_quote_or_404(db, int(replaces), user), today)
    elif asked.isdigit() and (row := db.get(QuoteRequestRow, int(asked))) is not None:
        form = quotes.form_for_request(row, today)
    else:
        form = quotes.QuoteForm(effective=today.isoformat(), expire=date(today.year, 12, 31).isoformat())
    return _form_page(request, form, reference)


def _parse(form: dict[str, list[str]]) -> quotes.QuoteForm:
    lines = []
    n = 0
    while f"l{n}_present" in form:
        lines.append(
            quotes.LineIn(
                analysis_ids=_ints(form.get(f"l{n}_analyses", [])),
                bulk_min=_one(form, f"l{n}_bulk_min"),
                prices={
                    int(pt): (_one(form, f"l{n}_p{int(pt)}"), _one(form, f"l{n}_b{int(pt)}"))
                    for pt in PT_CHOICES
                },
            )
        )
        n += 1
    method = _one(form, "payment_method")
    request_id, replaces = _one(form, "request_id"), _one(form, "replaces")
    return quotes.QuoteForm(
        customers=_ints(form.get("customers", [])),
        is_default=_one(form, "is_default") == "1",
        effective=_one(form, "effective"),
        expire=_one(form, "expire"),
        payment_method=int(method)
        if method in {str(int(m)) for m in quotes.slim_invoicing.PaymentMethod}
        else 1,
        payment_terms=_one(form, "payment_terms"),
        comments=_one(form, "comments")[:2000],
        lines=[li for li in lines if li.analysis_ids or any(r or b for r, b in li.prices.values())],
        request_id=int(request_id) if request_id.isdigit() else None,
        replaces=int(replaces) if replaces.isdigit() else None,
    )


@router.post("/quotes", name="create_quote")
def create_quote(request: Request, user: RequireStaff, db: DB, reference: Reference, form: FormLists):
    _pricing(db, user)
    typed = _parse(form)
    try:
        saved, stops = quotes.save(db, typed, reference, user)
    except quotes.QuoteRefused as refused:
        db.rollback()
        return _form_page(request, typed, reference, refused.problems, 422)
    db.commit()
    what = f"Quote {quotes.number(saved.id)} saved"
    if stops is not None:
        what += f"; {quotes.number(typed.replaces)} is withdrawn from {stops:%d %b %Y}"
    flash(request, what + ".")
    return RedirectResponse(request.url_for("quote_detail", quote_id=saved.id).path, status_code=303)


# ---------------------------------------------------------------- customers ask


@router.get("/quotes/ask", name="ask_quote")
def ask_quote(request: Request, user: RequireUser, reference: Reference):
    if user.is_staff:
        raise HTTPException(status_code=403, detail="Customers ask for quotes; staff make them.")
    return render(
        request,
        "quotes/ask.html",
        {"analyses": [a for a in reference.catalog_analyses() if a.portal_selectable], "times": PT_CHOICES},
    )


@router.post("/quotes/ask", name="ask_quote_form")
def ask_quote_form(request: Request, user: RequireUser, db: DB, reference: Reference, form: FormLists):
    try:
        quotes.add_request(
            db,
            user,
            _ints(form.get("analyses", [])),
            _ints(form.get("times", [])),
            _one(form, "samples_estimate"),
            _one(form, "note"),
            reference,
        )
    except quotes.QuoteRefused as refused:
        flash(request, " ".join(refused.problems), "error")
        return RedirectResponse(request.url_for("ask_quote").path, status_code=303)
    db.commit()
    flash(request, "Thank you: the lab will send you a quote, or tell you why not.")
    return RedirectResponse(request.url_for("quotes_home").path, status_code=303)


@router.get("/quotes/requests", name="quote_requests")
def quote_requests(request: Request, user: RequireStaff, db: DB, reference: Reference):
    return render(
        request,
        "quotes/requests.html",
        {
            "requests": quotes.requests(db),
            "reference": reference,
            "can_price": capabilities.can(db, user, capabilities.PRICING),
            "number": quotes.number,
            "times": {int(p): p.label for p in PT_CHOICES},
        },
    )


@router.post("/quotes/requests/{request_id}/decline", name="decline_quote_request")
def decline_quote_request(
    request: Request,
    request_id: int,
    user: RequireStaff,
    db: DB,
    reason: Annotated[str, Form(max_length=2000)] = "",
):
    _pricing(db, user)
    try:
        quotes.decline(db, request_id, reason, user)
    except quotes.QuoteRefused as refused:
        flash(request, " ".join(refused.problems), "error")
    else:
        db.commit()
        flash(request, f"Quote request {request_id} declined; the customer sees why.")
    return RedirectResponse(request.url_for("quote_requests").path, status_code=303)


# ---------------------------------------------------------------- one quote


@router.get("/quotes/{quote_id}", name="quote_detail")
def quote_detail(
    request: Request, quote_id: int, user: RequireUser, db: DB, reference: Reference, clock: Clock
):
    q = _quote_or_404(db, quote_id, user)
    can_price = user.is_staff and capabilities.can(db, user, capabilities.PRICING)
    return render(
        request,
        "quotes/detail.html",
        {
            "q": q,
            "number": quotes.number(q.id),
            "document": _document(q, reference),
            "state": quotes.state(q, clock.today(), request.app.state.settings.quote_expiry_warning_days),
            "labels": quotes.STATE_LABELS,
            "can_price": can_price,
            "recipients": ", ".join(quotes.recipients(db, q)) if can_price and not q.is_default else "",
            "sent": quotes.sent_on(db, q.id) if user.is_staff else [],
            "today": clock.today(),
        },
    )


@router.post("/quotes/{quote_id}/withdraw", name="withdraw_quote")
def withdraw_quote(
    request: Request,
    quote_id: int,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    from_day: Annotated[str, Form(alias="from")] = "",
    reason: Annotated[str, Form(max_length=2000)] = "",
):
    """Withdraws a quote before its expiry (V5 keeps it as a row added): it prices nothing from that day."""
    _pricing(db, user)
    q = _quote_or_404(db, quote_id, user)
    try:
        day = date.fromisoformat(from_day) if from_day.strip() else clock.today()
    except ValueError:
        flash(request, "From: give a date.", "error")
        return RedirectResponse(request.url_for("quote_detail", quote_id=q.id).path, status_code=303)
    try:
        quotes.withdraw(db, q, day, reason, user)
    except quotes.QuoteRefused as refused:
        flash(request, " ".join(refused.problems), "error")
    else:
        db.commit()
        flash(
            request,
            f"Quote {quotes.number(q.id)} is withdrawn from {day:%d %b %Y}: it prices nothing from then.",
        )
    return RedirectResponse(request.url_for("quote_detail", quote_id=q.id).path, status_code=303)


@router.get("/quotes/{quote_id}/pdf", name="quote_pdf")
def quote_pdf(request: Request, quote_id: int, user: RequireUser, db: DB, reference: Reference):
    q = _quote_or_404(db, quote_id, user)
    row = quotes.pdf_for(db, q, _document(q, reference))
    db.commit()
    return Response(
        row.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{quotes.number(q.id)}.pdf"'},
    )


@router.post("/quotes/{quote_id}/send", name="send_quote")
def send_quote(
    request: Request,
    quote_id: int,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    to: Annotated[str, Form(max_length=2000)] = "",
):
    _pricing(db, user)
    q = _quote_or_404(db, quote_id, user)
    try:
        row = quotes.send(db, q, _document(q, reference), quotes.parse_addresses(to), user)
    except quotes.QuoteRefused as refused:
        flash(request, " ".join(refused.problems), "error")
    else:
        db.commit()
        flash(
            request,
            f"Quote {quotes.number(q.id)} is in the outbox for {', '.join(row.to_addresses)} "
            "(not transmitted).",
        )
    return RedirectResponse(request.url_for("quote_detail", quote_id=q.id).path, status_code=303)
