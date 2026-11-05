"""Staff pages for slim-quality's registers and records (roadmap phase 5a, slim-data D-125…D-130), and the
customer's complaint page.

    /quality/equipment                 the register, worst state first; POST registers an item (D-125)
    /quality/equipment/{id}            its entries; POST …/entries adds one; POST …/nc raises an NC from a
                                       failed calibration or check, its window from the last pass (D-126)
    /quality/materials                 reference lots by effective expiry; POST registers one; POST
                                       /quality/materials/{id}/entries: opened, withdrawn, a note (D-127)
    /quality/pt                        PT rounds; POST registers one (D-128)
    /quality/pt/{id}                   its scores; POST …/submit (from the released results), …/scores
                                       (the provider's report), …/nc (an unsatisfactory score)
    /quality/complaints                open and closed; POST logs one taken by phone (D-130)
    /quality/complaints/{id}           its entries and who may approve the outcome; POST …/entries, …/nc
    /requests/{tr}/complaints          the customer's: raise one, and read the entries meant for them

The quality pages need a quality capability (403); refusals by the facts are 409, a bad form 422.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from slim_data.persistence.results import issues_for_tr
from slim_quality.domain.base import NotAllowed
from slim_quality.domain.complaints import (
    ALWAYS_TO_CUSTOMER,
    ComplaintEntry,
    ComplaintEntryKind,
    ComplaintState,
    Party,
)
from slim_quality.domain.enums import Measure
from slim_quality.domain.equipment import EquipmentEntry, EquipmentEntryKind, EquipmentKind, EquipmentState
from slim_quality.domain.materials import LotEntry, LotEntryKind, ReferenceLot
from slim_quality.domain.pt import Assigned, PtClass, PtEntry, PtEntryKind, PtState
from slim_quality.persistence import store
from slim_quality.service import complaints, pt, register
from slim_quality.service import quality as q
from sqlalchemy.orm import Session

from slim_lims.api import Service
from slim_lims.auth import RequireStaff, RequireUser
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.quality import SlimDataQc, quality_actor
from slim_lims.quality_records import NotReleased, involved_in, pt_lab_values
from slim_lims.security import csrf_protect
from slim_lims.services import NotFound as TRNotFound
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Clock = Annotated[LabClock, Depends(lab_clock)]
Text = Annotated[str, Form(max_length=4000)]


def _actor(db: Session, user):
    actor = quality_actor(db, user)
    if actor is None:
        raise HTTPException(status_code=403, detail="The quality pages need a quality capability.")
    return actor


def _date(text: str, what: str, required: bool = True) -> date | None:
    if not text.strip():
        if required:
            raise HTTPException(status_code=422, detail=f"{what}: give a date")
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{what}: not a date ({text!r})") from None


def _number(text: str, what: str, required: bool = True) -> Decimal | None:
    if not text.strip():
        if required:
            raise HTTPException(status_code=422, detail=f"{what}: give a number")
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        raise HTTPException(status_code=422, detail=f"{what}: not a number ({text!r})") from None


def _write(db: Session, action):
    """Runs a write; commits, or maps the refusal: the facts 409, the form 422, a missing record 404.
    Nothing is committed on a refusal: the request's session closes without it (and isn't rolled back
    here, so the error page can still read who is signed in)."""
    try:
        found = action()
    except store.NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except (NotAllowed, register.AlreadyRegistered, NotReleased) as refused:
        raise HTTPException(status_code=409, detail=str(refused)) from None
    except (ValueError, ValidationError) as error:
        detail = error.errors()[0]["msg"] if isinstance(error, ValidationError) else str(error)
        raise HTTPException(status_code=422, detail=detail.removeprefix("Value error, ")) from None
    db.commit()
    return found


def _get(loader, what: str):
    try:
        return loader()
    except store.NotFound:
        raise HTTPException(status_code=404, detail=f"No such {what}.") from None


# ---------------------------------------------------------------- the quality home's tiles


def tiles(db: Session, today: date) -> dict:
    """What the quality home shows at a glance (D-125…D-130, D-134, D-135)."""
    due_within = q.current_profile(db).equipment.due_within_days
    states = [item.state(today, due_within) for item in store.list_equipment(db)]
    soon = today + timedelta(days=30)
    lots = [lot for lot in store.list_lots(db) if lot.withdrawn_on is None]
    policy = q.current_profile(db).pt
    rounds = store.list_pt_rounds(db)
    return {
        "equipment_bad": sum(s in (EquipmentState.OVERDUE, EquipmentState.OUT_OF_SERVICE) for s in states),
        "equipment_due": sum(s == EquipmentState.DUE for s in states),
        "lots_expiring": sum(lot.effective_expiry <= soon for lot in lots),
        "pt_open": sum(r.state != PtState.SCORED for r in rounds),
        "pt_flagged": sum((r.worst(policy) or PtClass.SATISFACTORY) > PtClass.SATISFACTORY for r in rounds),
        "complaints_open": sum(c.state != ComplaintState.CLOSED for c in store.list_complaints(db)),
        "risks_flagged": sum(
            bool(r.flags(today, q.current_profile(db).risk)) for r in store.list_risks(db) if not r.closed
        ),
        "audits_overdue": sum(a.overdue(today) for a in store.list_internal_audits(db)),
    }


# ---------------------------------------------------------------- equipment (D-125, D-126)


@router.get("/quality/equipment", name="quality_equipment", response_class=HTMLResponse)
def quality_equipment(request: Request, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    today = clock.today()
    policy = q.current_profile(db).equipment
    rows = [(item, item.state(today, policy.due_within_days)) for item in store.list_equipment(db)]
    rows.sort(key=lambda row: (-row[1], row[0].name))
    return render(
        request,
        "quality/equipment.html",
        {"actor": actor, "rows": rows, "kinds": list(EquipmentKind), "policy": policy, "today": today},
    )


@router.post("/quality/equipment", name="quality_equipment_register")
def quality_equipment_register(
    request: Request,
    user: RequireStaff,
    db: DB,
    name: Annotated[str, Form(max_length=100)],
    kind: Annotated[int, Form()],
    description: Annotated[str, Form(max_length=500)] = "",
    calibration_required: Annotated[str, Form()] = "",
):
    actor = _actor(db, user)
    item = _write(
        db,
        lambda: register.register_equipment(
            db, name, EquipmentKind(kind), actor, description, calibration_required == "1"
        ),
    )
    flash(request, f"{item.name} registered.")
    return RedirectResponse(str(request.url_for("quality_equipment_item", item_id=item.id)), status_code=303)


@router.get("/quality/equipment/{item_id}", name="quality_equipment_item", response_class=HTMLResponse)
def quality_equipment_item(request: Request, item_id: int, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    item = _get(lambda: store.get_equipment(db, item_id), "equipment")
    today = clock.today()
    policy = q.current_profile(db).equipment
    return render(
        request,
        "quality/equipment_item.html",
        {
            "actor": actor,
            "item": item,
            "state": item.state(today, policy.due_within_days),
            "effect": policy.effect(item.kind),
            "events": store.events(db, instrument=item.name),
            "kinds": EquipmentEntryKind,
            "today": today,
            "zone": clock.zone,
        },
    )


@router.post("/quality/equipment/{item_id}/entries", name="quality_equipment_entry")
def quality_equipment_entry(
    request: Request,
    item_id: int,
    user: RequireStaff,
    db: DB,
    kind: Annotated[int, Form()],
    on: Annotated[str, Form()] = "",
    passed: Annotated[str, Form()] = "",
    provider: Annotated[str, Form(max_length=200)] = "",
    certificate: Annotated[str, Form(max_length=100)] = "",
    next_due: Annotated[str, Form()] = "",
    note: Text = "",
):
    actor = _actor(db, user)
    entry_kind = EquipmentEntryKind(kind)
    tested = entry_kind in (EquipmentEntryKind.CALIBRATION, EquipmentEntryKind.CHECK)

    def add():
        entry = EquipmentEntry(
            kind=entry_kind,
            on=_date(on, "Day"),
            passed=(passed == "1") if tested else None,
            provider=provider.strip(),
            certificate=certificate.strip(),
            next_due=_date(next_due, "Next due", required=False),
            note=note.strip(),
            by=actor,
        )
        return register.add_equipment_entry(db, item_id, entry)

    item = _write(db, add)
    flash(request, f"{item.name}: {entry_kind.label.lower()} recorded.")
    return RedirectResponse(str(request.url_for("quality_equipment_item", item_id=item_id)), status_code=303)


@router.post("/quality/equipment/{item_id}/nc", name="quality_equipment_nc")
def quality_equipment_nc(
    request: Request, item_id: int, user: RequireStaff, db: DB, clock: Clock, index: Annotated[int, Form()]
):
    actor = _actor(db, user)
    nc = _write(db, lambda: register.failure_nc(db, item_id, index, actor, clock.zone))
    flash(request, f"{nc.number} raised.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc.id)), status_code=303)


# ---------------------------------------------------------------- reference lots (D-127)


@router.get("/quality/materials", name="quality_materials", response_class=HTMLResponse)
def quality_materials(request: Request, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    today = clock.today()
    lots = sorted(store.list_lots(db), key=lambda lot: (lot.withdrawn_on is not None, lot.effective_expiry))
    return render(
        request,
        "quality/materials.html",
        {
            "actor": actor,
            "lots": lots,
            "today": today,
            "soon": today + timedelta(days=30),
            "kinds": LotEntryKind,
        },
    )


@router.post("/quality/materials", name="quality_lot_register")
def quality_lot_register(
    request: Request,
    user: RequireStaff,
    db: DB,
    name: Annotated[str, Form(max_length=100)],
    lot: Annotated[str, Form(max_length=50)],
    supplier: Annotated[str, Form(max_length=200)],
    certificate: Annotated[str, Form(max_length=100)],
    value: Annotated[str, Form(max_length=30)],
    unit: Annotated[str, Form(max_length=20)],
    elements: Annotated[str, Form(max_length=500)],
    expires: Annotated[str, Form()],
    traceability: Annotated[str, Form(max_length=500)] = "",
    uncertainty: Annotated[str, Form(max_length=30)] = "",
    shelf_life_opened_days: Annotated[str, Form(max_length=5)] = "",
):
    actor = _actor(db, user)

    def add():
        days = shelf_life_opened_days.strip()
        found = ReferenceLot(
            name=name.strip(),
            lot=lot.strip(),
            supplier=supplier.strip(),
            certificate=certificate.strip(),
            traceability=traceability.strip(),
            value=_number(value, "Certified value"),
            unit=unit.strip(),
            uncertainty=_number(uncertainty, "Uncertainty", required=False),
            elements=tuple(
                dict.fromkeys(e.strip() for e in elements.replace(";", ",").split(",") if e.strip())
            ),
            expires=_date(expires, "Expires"),
            shelf_life_opened_days=int(days) if days.isdigit() else None,
            registered_by=actor,
        )
        return register.register_lot(db, found)

    found = _write(db, add)
    flash(request, f"{found} registered.")
    return RedirectResponse(str(request.url_for("quality_materials")), status_code=303)


@router.post("/quality/materials/{lot_id}/entries", name="quality_lot_entry")
def quality_lot_entry(
    request: Request,
    lot_id: int,
    user: RequireStaff,
    db: DB,
    kind: Annotated[int, Form()],
    on: Annotated[str, Form()] = "",
    note: Text = "",
):
    actor = _actor(db, user)
    entry_kind = LotEntryKind(kind)
    found = _write(
        db,
        lambda: register.add_lot_entry(
            db, lot_id, LotEntry(kind=entry_kind, on=_date(on, "Day"), note=note.strip(), by=actor)
        ),
    )
    flash(request, f"{found}: {entry_kind.label.lower()} recorded.")
    return RedirectResponse(str(request.url_for("quality_materials")), status_code=303)


# ---------------------------------------------------------------- proficiency testing (D-128, D-129)


@router.get("/quality/pt", name="quality_pt", response_class=HTMLResponse)
def quality_pt(request: Request, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    policy = q.current_profile(db).pt
    rounds = store.list_pt_rounds(db)
    charts = [c for c in q.charts(db, SlimDataQc(db)) if c.series.key.measure == Measure.PT_Z]
    return render(
        request,
        "quality/pt.html",
        {
            "actor": actor,
            "rows": [(r, r.worst(policy)) for r in rounds],
            "charts": charts,
            "policy": policy,
            "today": clock.today(),
        },
    )


@router.post("/quality/pt", name="quality_pt_register")
def quality_pt_register(
    request: Request,
    user: RequireStaff,
    db: DB,
    scheme: Annotated[str, Form(max_length=100)],
    round_code: Annotated[str, Form(max_length=50)],
    sample: Annotated[str, Form(max_length=30)],
    analytes: Annotated[str, Form(max_length=500)],
    due: Annotated[str, Form()],
):
    actor = _actor(db, user)
    sample = sample.strip().upper()
    tr_number = sample.rsplit("-", 1)[0]

    def add():
        codes = tuple(dict.fromkeys(a.strip() for a in analytes.replace(";", ",").split(",") if a.strip()))
        return pt.register_round(db, scheme, round_code, tr_number, sample, codes, _date(due, "Due"), actor)

    found = _write(db, add)
    flash(request, f"{found.scheme} {found.round_code} registered on {found.sample}.")
    return RedirectResponse(str(request.url_for("quality_pt_round", round_id=found.id)), status_code=303)


@router.get("/quality/pt/{round_id}", name="quality_pt_round", response_class=HTMLResponse)
def quality_pt_round(request: Request, round_id: int, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    found = _get(lambda: store.get_pt_round(db, round_id), "PT round")
    ready, waiting = None, ""
    if found.state == PtState.REGISTERED:
        try:
            ready = pt_lab_values(db, found)
        except NotReleased as error:
            waiting = str(error)
    return render(
        request,
        "quality/pt_round.html",
        {
            "actor": actor,
            "round": found,
            "scores": pt.scores(db, found),
            "ready": ready,
            "waiting": waiting,
            "today": clock.today(),
            "zone": clock.zone,
        },
    )


@router.post("/quality/pt/{round_id}/submit", name="quality_pt_submit")
def quality_pt_submit(request: Request, round_id: int, user: RequireStaff, db: DB):
    actor = _actor(db, user)

    def submit():
        found = store.get_pt_round(db, round_id)
        values = pt_lab_values(db, found)
        return pt.add_entry(db, round_id, PtEntry(kind=PtEntryKind.SUBMITTED, values=values, by=actor))

    found = _write(db, submit)
    flash(request, f"{found.round_code}: the lab's results recorded as submitted.")
    return RedirectResponse(str(request.url_for("quality_pt_round", round_id=round_id)), status_code=303)


@router.post("/quality/pt/{round_id}/scores", name="quality_pt_scores")
def quality_pt_scores(
    request: Request,
    round_id: int,
    user: RequireStaff,
    db: DB,
    report_date: Annotated[str, Form()],
    analyte: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies defaults)
    assigned: Annotated[list[str], Form()] = [],  # noqa: B006
    sigma: Annotated[list[str], Form()] = [],  # noqa: B006
    provider_z: Annotated[list[str], Form()] = [],  # noqa: B006
):
    actor = _actor(db, user)

    def score():
        lines = tuple(
            Assigned(
                analyte=a.strip(),
                assigned=_number(x, f"{a}: assigned value"),
                sigma=_number(s, f"{a}: σ_pt"),
                provider_z=_number(z, f"{a}: provider's z", required=False),
            )
            for a, x, s, z in zip(analyte, assigned, sigma, provider_z, strict=False)
        )
        entry = PtEntry(
            kind=PtEntryKind.SCORED, assigned=lines, report_date=_date(report_date, "Report date"), by=actor
        )
        return pt.add_entry(db, round_id, entry)

    found = _write(db, score)
    flash(request, f"{found.round_code} scored.")
    return RedirectResponse(str(request.url_for("quality_pt_round", round_id=round_id)), status_code=303)


@router.post("/quality/pt/{round_id}/nc", name="quality_pt_nc")
def quality_pt_nc(
    request: Request, round_id: int, user: RequireStaff, db: DB, analyte: Annotated[str, Form()]
):
    actor = _actor(db, user)
    nc = _write(db, lambda: pt.unsatisfactory_nc(db, round_id, analyte, actor))
    flash(request, f"{nc.number} raised.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc.id)), status_code=303)


# ---------------------------------------------------------------- complaints (D-130)


@router.get("/quality/complaints", name="quality_complaints", response_class=HTMLResponse)
def quality_complaints(request: Request, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    found = store.list_complaints(db)
    return render(
        request,
        "quality/complaints.html",
        {
            "actor": actor,
            "open": [c for c in found if c.state != ComplaintState.CLOSED],
            "closed": [c for c in found if c.state == ComplaintState.CLOSED],
            "zone": clock.zone,
        },
    )


@router.get("/quality/complaints/{complaint_id}", name="quality_complaint", response_class=HTMLResponse)
def quality_complaint(request: Request, complaint_id: int, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    found = _get(lambda: store.get_complaint(db, complaint_id), "complaint")
    involved = involved_in(db, found.tr_number)
    may_approve = actor.is_manager and actor.id not in involved
    return render(
        request,
        "quality/complaint.html",
        {
            "actor": actor,
            "c": found,
            "kinds": ComplaintEntryKind,
            "always": ALWAYS_TO_CUSTOMER,
            "involved": involved,
            "may_approve": may_approve,
            "zone": clock.zone,
        },
    )


@router.post("/quality/complaints/{complaint_id}/entries", name="quality_complaint_entry")
def quality_complaint_entry(
    request: Request,
    complaint_id: int,
    user: RequireStaff,
    db: DB,
    kind: Annotated[int, Form()],
    note: Text = "",
    to_customer: Annotated[str, Form()] = "",
    valid: Annotated[str, Form()] = "",
):
    actor = _actor(db, user)
    entry_kind = ComplaintEntryKind(kind)

    def add():
        found = store.get_complaint(db, complaint_id)
        involved = involved_in(db, found.tr_number)
        found.allows(entry_kind, actor, involved)  # a refusal first, before the form is read
        entry = ComplaintEntry(
            kind=entry_kind,
            note=note.strip(),
            to_customer=entry_kind in ALWAYS_TO_CUSTOMER
            or (entry_kind == ComplaintEntryKind.PROGRESS and to_customer == "1"),
            valid=(valid == "1") if entry_kind == ComplaintEntryKind.INVESTIGATED else None,
            by=actor,
        )
        return complaints.add_entry(db, complaint_id, entry, involved)

    found = _write(db, add)
    flash(request, f"{found.number}: {found.state.label.lower()}.")
    return RedirectResponse(
        str(request.url_for("quality_complaint", complaint_id=complaint_id)), status_code=303
    )


@router.post("/quality/complaints/{complaint_id}/nc", name="quality_complaint_nc")
def quality_complaint_nc(request: Request, complaint_id: int, user: RequireStaff, db: DB):
    actor = _actor(db, user)
    nc = _write(db, lambda: complaints.complaint_nc(db, complaint_id, actor))
    flash(request, f"{nc.number} raised.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc.id)), status_code=303)


# ---------------------------------------------------------------- the customer's side


def _tr(service, tr_number: str, user):
    try:
        return service.get(tr_number, user)  # customers see only their customer's (others 404, D-103)
    except TRNotFound:
        raise HTTPException(status_code=404, detail=f"Testing Request {tr_number} not found.") from None


@router.get("/requests/{tr_number}/complaints", name="request_complaints", response_class=HTMLResponse)
def request_complaints(
    request: Request, tr_number: str, user: RequireUser, service: Service, db: DB, clock: Clock
):
    submission = _tr(service, tr_number, user)
    return render(
        request,
        "requests/complaints.html",
        {
            "s": submission,
            "complaints": store.list_complaints(db, tr_number=submission.tr_number),
            "issues": issues_for_tr(db, submission.tr_number),
            "zone": clock.zone,
        },
    )


@router.post("/requests/{tr_number}/complaints", name="request_complaint_raise")
def request_complaint_raise(
    request: Request,
    tr_number: str,
    user: RequireUser,
    service: Service,
    db: DB,
    text: Text,
    issue_id: Annotated[str, Form()] = "",
):
    """A customer raises a complaint on their request; staff log one taken by phone, on their behalf."""
    submission = _tr(service, tr_number, user)
    issues = {i.id for i in issues_for_tr(db, submission.tr_number)}
    issue = int(issue_id) if issue_id.isdigit() else None
    if issue is not None and issue not in issues:
        raise HTTPException(status_code=422, detail="That report wasn't issued on this request.")
    party = Party(id=str(user.id), display=user.full_name or user.email)
    found = _write(
        db,
        lambda: complaints.raise_complaint(
            db, submission.tr_number, str(submission.customer_id), text, party, user.is_staff, issue
        ),
    )
    flash(request, f"Complaint {found.number} received: we will acknowledge it shortly.")
    return RedirectResponse(str(request.url_for("request_complaints", tr_number=tr_number)), status_code=303)
