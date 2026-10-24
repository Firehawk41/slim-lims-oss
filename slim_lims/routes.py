"""Server-rendered pages. Data changes go through SubmissionService, exactly like the JSON API;
the submission form itself is JavaScript (static/js/request_form.js) posting to /api/submissions."""

import io
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError
import slim_invoicing
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims import board, invoices
from slim_lims.api import Service, get_reference
from slim_lims.auth import CurrentUser, RequireStaff, RequireUser
from slim_lims.db import get_db
from slim_lims.domain import ActorType, RequestType, TRStatus, TRSubmission, allowed_next_statuses
from slim_lims.models import LAB_ROLE_ANALYST, LAB_ROLE_SUPERVISOR, LAB_ROLES, ROLE_CUSTOMER, ROLE_STAFF, User
from slim_lims.reference import ReferenceData
from slim_lims.results import issued_documents
from slim_lims.schemas import CheckInIn, ChemicalMatchIn, StatusChangeIn
from slim_lims.security import csrf_protect, hash_password
from slim_lims.services import Conflict, Forbidden, NotFound, SubmissionRejected
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
Reference = Annotated[ReferenceData, Depends(get_reference)]
DB = Annotated[Session, Depends(get_db)]
PER_PAGE = 25


def _redirect(request: Request, name: str, **params) -> RedirectResponse:
    return RedirectResponse(request.url_for(name, **params).path, status_code=303)


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value and value.strip().isdigit() else None


# ---------------------------------------------------------------- requests (customers and staff)

@router.get("/", name="index")
def index(request: Request, user: CurrentUser, db: DB, reference: Reference,
          customer: str = "", analysis: str = "", rush: str = "", mine: str = ""):
    """The board (spec §13): every live TR where it has got to. Customers see their own, read-only."""
    if user is None:
        return RedirectResponse("/login", status_code=303)
    filters = {"customer": _int_or_none(customer), "analysis": _int_or_none(analysis),
               "rush": rush == "1", "mine": mine == "1" and user.is_staff}
    columns = board.board(db, user, reference, customer_id=filters["customer"], analysis_id=filters["analysis"],
                          rush=filters["rush"], mine=filters["mine"])
    analyses = sorted({a for _, _, cards in columns for c in cards for s in c.tr.samples for a in s.analysis_ids})
    return render(request, "board.html", {
        "columns": columns,
        "filters": filters,
        "customers": reference.customer_choices() if user.is_staff else [],
        "analyses": [reference.analysis(a) for a in analyses if reference.analysis(a)],
        "paid_shown": board.PAID_SHOWN,
        "can_invoice": user.is_staff,
        "TRStatus": TRStatus,
    })


@router.post("/requests/{tr_number}/invoice", name="invoice_sent")
def invoice_sent(request: Request, tr_number: str, user: RequireStaff, service: Service, db: DB, reference: Reference):
    return _invoice(request, db, lambda: invoices.mark_sent(db, service, tr_number, user, reference),
                    f"{tr_number}: invoiced and sent.")


@router.post("/requests/{tr_number}/invoice/paid", name="invoice_paid")
def invoice_paid(request: Request, tr_number: str, user: RequireStaff, service: Service, db: DB):
    return _invoice(request, db, lambda: invoices.mark_paid(db, service, tr_number, user), f"{tr_number}: paid.")


def _invoice(request: Request, db: Session, action, done: str) -> RedirectResponse:
    try:
        action()
        db.commit()
        flash(request, done)
    except (Conflict, Forbidden, NotFound) as e:
        db.rollback()
        flash(request, str(e) or "Not found.", "error")
    return _redirect(request, "index")


@router.get("/requests/{tr_number}/reports", name="issued_reports")
def issued_reports(request: Request, tr_number: str, user: RequireUser, service: Service, db: DB,
                   reference: Reference):
    """Every report issued for a TR, newest first, each exactly as it was issued (spec §13): rebuilt as
    of its issue and checked against the hash recorded then (D-73). Customers see their own."""
    try:
        submission = service.get(tr_number, user)
    except NotFound:
        return render(request, "error.html", {"status": 404, "message": f"Testing Request {tr_number} not found."}, 404)
    return render(request, "requests/reports.html", {
        "s": submission, "reports": issued_documents(db, tr_number), "reference": reference,
    })


@router.get("/requests/{tr_number}/invoice", name="invoice_page")
def invoice_page(request: Request, tr_number: str, user: RequireUser, service: Service, db: DB):
    """The sales order invoicing V5 made for a TR (slim-data D-83). Customers see their own."""
    try:
        submission = service.get(tr_number, user)
    except NotFound:
        return render(request, "error.html", {"status": 404, "message": f"Testing Request {tr_number} not found."}, 404)
    order = slim_invoicing.sales_order_for(db, tr_number)
    if order is None:
        return render(request, "error.html", {"status": 404, "message": f"{tr_number} has not been invoiced."}, 404)
    paid = invoices.latest_invoices(db, [submission.id]).get(submission.id)
    return render(request, "requests/invoice.html", {"s": submission, "order": order, "invoice": paid})


@router.get("/invoices", name="invoices_home")
def invoices_home(request: Request, user: RequireStaff, db: DB, reference: Reference):
    return render(request, "invoices.html", {"orders": slim_invoicing.sales_orders(db),
                                             "quotes": invoices.price_lists(db, reference), "reference": reference,
                                             "five_days": slim_invoicing.ProcessingTime.FIVE_DAYS})


@router.get("/invoices.csv", name="invoices_csv")
def invoices_csv(user: RequireStaff, db: DB):
    """Every sales order as the accounting import file (V4's columns)."""
    out = io.StringIO()
    slim_invoicing.write_csv(slim_invoicing.sales_orders(db), out)
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="sales_orders.csv"'})


@router.get("/requests", name="my_requests")
def my_requests(request: Request, user: RequireUser, service: Service, page: int = 1):
    if user.is_staff:
        return _redirect(request, "admin_home")
    result = service.list(user, page=max(page, 1), per_page=PER_PAGE)
    return render(request, "requests/list.html", {"result": result, "statuses": TRStatus, "filters": {}})


@router.get("/requests/new", name="new_request")
def new_request(request: Request, user: RequireUser):
    return render(request, "requests/new.html")


@router.get("/requests/{tr_number}", name="request_detail")
def request_detail(request: Request, tr_number: str, user: RequireUser, service: Service, reference: Reference):
    try:
        submission = service.get(tr_number, user)
    except NotFound:
        return render(request, "error.html", {"status": 404, "message": f"Testing Request {tr_number} not found."}, 404)
    actor_type = ActorType.STAFF if user.is_staff else ActorType.CUSTOMER_USER
    return render(request, "requests/detail.html", {
        "s": submission,
        "samples": _describe_samples(submission, reference),
        "next_statuses": allowed_next_statuses(submission.status, actor_type),
        "lab_today": service.lab_today(),
        "chemicals": reference.chemical_choices() if user.is_staff else [],
        "TRStatus": TRStatus,
    })


@router.post("/requests/{tr_number}/status", name="change_status_form")
def change_status_form(
    request: Request,
    tr_number: str,
    user: RequireUser,
    service: Service,
    db: DB,
    to_status: Annotated[int, Form()],
    note: Annotated[str, Form()] = "",
    date_received: Annotated[str, Form()] = "",
    received_by: Annotated[str, Form()] = "",
):
    try:
        data = StatusChangeIn(
            to_status=to_status,
            note=note,
            date_received=date.fromisoformat(date_received) if date_received else None,
            received_by=received_by or None,
        )
        submission = service.change_status(tr_number, data, user)
        db.commit()
        flash(request, f"{submission.tr_number} is now {submission.status.label.lower()}.")
    except (ValidationError, ValueError, Conflict) as e:
        db.rollback()
        flash(request, _first_error(e), "error")
    except NotFound:
        flash(request, f"Testing Request {tr_number} not found.", "error")
        return _redirect(request, "index")
    return _redirect(request, "request_detail", tr_number=tr_number)


@router.get("/requests/{tr_number}/check-in", name="check_in_page")
def check_in_page(request: Request, tr_number: str, user: RequireStaff, service: Service, reference: Reference):
    try:
        submission = service.get(tr_number, user)
    except NotFound:
        return render(request, "error.html", {"status": 404, "message": f"Testing Request {tr_number} not found."}, 404)
    if submission.status is not TRStatus.SUBMITTED:
        flash(request, f"{submission.tr_number} is already {submission.status.label.lower()}.", "error")
        return _redirect(request, "request_detail", tr_number=tr_number)
    return render(request, "requests/check_in.html", {
        "s": submission,
        "samples": _describe_samples(submission, reference),
        "chemicals": reference.chemical_choices(),
        "lab_today": service.lab_today(),
        "TRStatus": TRStatus,
    })


async def form_fields(request: Request) -> dict[str, str]:
    """The whole posted form (for forms with dynamic field names, e.g. chemical_<position>)."""
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


@router.post("/requests/{tr_number}/check-in", name="check_in_form")
def check_in_form(
    request: Request,
    tr_number: str,
    user: RequireStaff,
    service: Service,
    db: DB,
    form: Annotated[dict[str, str], Depends(form_fields)],
):
    try:
        matches = {int(k.removeprefix("chemical_")): int(v)
                   for k, v in form.items() if k.startswith("chemical_") and v.strip().isdigit()}
        data = CheckInIn(
            date_received=date.fromisoformat(form.get("date_received", "")),
            received_by=form.get("received_by") or None,
            chemicals=matches,
            note=form.get("note", ""),
        )
        submission = service.check_in(tr_number, data, user)
        db.commit()
        flash(request, f"{submission.tr_number} checked in: {len(submission.samples)} sample(s) logged into the LIMS.")
        return _redirect(request, "request_detail", tr_number=tr_number)
    except (ValidationError, ValueError, Conflict, SubmissionRejected) as e:
        db.rollback()
        flash(request, _first_error(e) if isinstance(e, ValidationError) else str(e), "error")
    except NotFound:
        flash(request, f"Testing Request {tr_number} not found.", "error")
        return _redirect(request, "admin_home")
    return _redirect(request, "check_in_page", tr_number=tr_number)


@router.post("/requests/{tr_number}/samples/{position}/chemical", name="match_chemical_form")
def match_chemical_form(
    request: Request,
    tr_number: str,
    position: int,
    user: RequireStaff,
    service: Service,
    db: DB,
    chemical_id: Annotated[str, Form()] = "",
):
    try:
        if not chemical_id.isdigit():
            raise ValueError("Choose a SLIM chemical.")
        submission = service.match_chemical(tr_number, position, ChemicalMatchIn(chemical_id=int(chemical_id)), user)
        db.commit()
        sample = next(s for s in submission.samples if s.position == position)
        flash(request, f"Sample {position} ({sample.sample_name}) matched.")
    except (SubmissionRejected, Forbidden, ValueError) as e:
        db.rollback()
        flash(request, str(e), "error")
    except NotFound:
        flash(request, f"Testing Request {tr_number} not found.", "error")
        return _redirect(request, "admin_home")
    return _redirect(request, "request_detail", tr_number=tr_number)


def _first_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        err = e.errors()[0]
        return f"{err['loc'][-1] if err['loc'] else 'input'}: {err['msg']}"
    return str(e)


def _describe_samples(submission: TRSubmission, reference: ReferenceData) -> list[dict]:
    """Names for the IDs a sample stores, for display only."""
    described = []
    for s in submission.samples:
        chemical = reference.chemical(s.chemical_id) if s.chemical_id is not None else None
        analyses = [(reference.analysis(a).name if reference.analysis(a) else f"Analysis #{a}") for a in s.analysis_ids]
        elements = [(reference.element(e).symbol if reference.element(e) else f"#{e}") for e in s.additional_element_ids]
        described.append({"sample": s, "analyses": analyses, "elements": elements,
                          "slim_chemical": chemical.name if chemical else None})
    return described


# ---------------------------------------------------------------- staff

@router.get("/admin", name="admin_home")
def admin_home(
    request: Request,
    user: RequireStaff,
    service: Service,
    status: str = "",
    request_type: str = "",
    page: int = 1,
):
    filters = {"status": _int_or_none(status), "request_type": _int_or_none(request_type)}
    result = service.list(
        user,
        status=TRStatus(filters["status"]) if filters["status"] in TRStatus._value2member_map_ else None,
        request_type=(RequestType(filters["request_type"])
                      if filters["request_type"] in RequestType._value2member_map_ else None),
        page=max(page, 1),
        per_page=PER_PAGE,
    )
    reference = request.app.state.reference
    reference_status = None
    if hasattr(reference, "status"):
        reference.snapshot()  # the cache loads lazily; make sure the page reports a real load
        reference_status = reference.status()
    return render(request, "admin/home.html", {
        "result": result, "filters": filters, "statuses": TRStatus, "request_types": RequestType,
        "reference_status": reference_status,
    })


@router.post("/admin/reference/refresh", name="refresh_reference")
def refresh_reference(request: Request, user: RequireStaff):
    reference = request.app.state.reference
    if not hasattr(reference, "refresh"):
        flash(request, "This reference data source cannot be refreshed.", "error")
    elif reference.refresh():
        flash(request, "Reference data reloaded from SLIM.")
    else:
        flash(request, "Could not reload reference data; still using the previous copy.", "error")
    return _redirect(request, "admin_home")


@router.get("/admin/users", name="admin_users")
def admin_users(request: Request, user: RequireStaff, db: DB, reference: Reference):
    users = db.scalars(select(User).order_by(User.role.desc(), User.email)).all()
    customers = {c.id: c.name for c in reference.customer_choices()}
    return render(request, "admin/users.html", {"users": users, "customers": customers, "lab_roles": LAB_ROLES})


@router.post("/admin/users", name="create_user")
def create_user(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    email: Annotated[str, Form()],
    role: Annotated[str, Form()],
    password: Annotated[str, Form()],
    initials: Annotated[str, Form()] = "",
    customer_id: Annotated[str, Form()] = "",
    lab_role: Annotated[str, Form()] = "",
):
    email = email.strip().lower()
    initials = initials.strip().upper()
    error = None
    cid = _int_or_none(customer_id)
    role_id = _int_or_none(lab_role) or LAB_ROLE_ANALYST
    if user.is_persona:
        error = "Demo personas can't manage users."
    elif role not in (ROLE_CUSTOMER, ROLE_STAFF):
        error = "Choose a role."
    elif "@" not in email:
        error = "Enter a valid email address."
    elif len(password) < 12:
        error = "Passwords must be at least 12 characters."
    elif role == ROLE_STAFF and not initials:
        error = "Staff users need initials."
    elif role == ROLE_STAFF and role_id not in LAB_ROLES:
        error = "Choose a lab role."
    elif role == ROLE_STAFF and role_id != LAB_ROLE_ANALYST and user.lab_role != LAB_ROLE_SUPERVISOR:
        error = "Only a supervisor can give a lab role above analyst."
    elif role == ROLE_STAFF and db.scalars(select(User).where(User.initials == initials)).first():
        error = f"Initials {initials} are taken: they identify the analyst in result files."
    elif role == ROLE_CUSTOMER and (cid is None or reference.customer(cid) is None):
        error = "Choose the customer (it must already exist in SLIM)."
    elif db.scalars(select(User).where(User.email == email)).first():
        error = f"A user with email {email} already exists."
    if error:
        flash(request, error, "error")
        return _redirect(request, "admin_users")
    try:
        password_hash = hash_password(password)
    except ValueError as e:
        flash(request, str(e), "error")
        return _redirect(request, "admin_users")
    db.add(User(
        email=email, password_hash=password_hash, role=role,
        initials=initials if role == ROLE_STAFF else None,
        lab_role=role_id if role == ROLE_STAFF else None,
        customer_id=cid if role == ROLE_CUSTOMER else None,
    ))
    db.commit()
    flash(request, f"Created {role} user {email}.")
    return _redirect(request, "admin_users")


@router.post("/admin/users/{user_id}/active", name="toggle_user_active")
def toggle_user_active(request: Request, user_id: str, user: RequireStaff, db: DB):
    try:
        target = db.get(User, uuid.UUID(user_id))
    except ValueError:
        target = None
    if user.is_persona:
        flash(request, "Demo personas can't manage users.", "error")
    elif target is None:
        flash(request, "User not found.", "error")
    elif target.id == user.id:
        flash(request, "You cannot deactivate yourself.", "error")
    else:
        target.is_active = not target.is_active
        db.commit()
        flash(request, f"{target.email} is now {'active' if target.is_active else 'deactivated'}.")
    return _redirect(request, "admin_users")


@router.post("/admin/users/{user_id}/lab-role", name="set_lab_role")
def set_lab_role(request: Request, user_id: str, user: RequireStaff, db: DB, lab_role: Annotated[int, Form()]):
    """Supervisors only: lab roles decide who may review, accept and release results (D-70)."""
    try:
        target = db.get(User, uuid.UUID(user_id))
    except ValueError:
        target = None
    if user.is_persona:
        flash(request, "Demo personas can't change lab roles.", "error")
    elif user.lab_role != LAB_ROLE_SUPERVISOR:
        flash(request, "Only a supervisor can change lab roles.", "error")
    elif target is None or not target.is_staff:
        flash(request, "Staff user not found.", "error")
    elif lab_role not in LAB_ROLES:
        flash(request, "Choose a lab role.", "error")
    elif target.id == user.id:
        flash(request, "You cannot change your own lab role.", "error")
    else:
        target.lab_role = lab_role
        db.commit()
        flash(request, f"{target.email} is now {LAB_ROLES[lab_role].lower()}.")
    return _redirect(request, "admin_users")


@router.get("/api/me", name="api_me")
def api_me(user: RequireUser):
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "customer_id": user.customer_id,
        "initials": user.initials,
        "lab_role": getattr(user, "lab_role", None),
    }
