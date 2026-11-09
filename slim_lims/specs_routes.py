"""Spec limit changes (slim-data D-143): /specs lists the sets on file and the proposals; quality staff and
quality managers propose a change to one set; a quality manager or a lab supervisor who didn't propose it
approves (written through slim-domain; what it bears on re-evaluated) or rejects it."""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from slim_lims import refdata, spec_changes
from slim_lims.api import get_reference, slim_engine_for, specs_for
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.models import SpecProposalRow, User
from slim_lims.quotes_routes import FormLists, _one
from slim_lims.reference import ReferenceData
from slim_lims.results import actor_for, reference_lookup
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]
BLANK_ROWS = 5


def _allowed(db: Session, user: User) -> None:
    if not (spec_changes.may_propose(db, user) or spec_changes.may_decide(db, user)):
        raise HTTPException(
            status_code=403, detail="Spec limits: quality staff, quality managers, supervisors."
        )


def _key(customer: str, chemical: str, location: str, kind: str) -> spec_changes.SetKey:
    if not customer.isdigit() or not chemical.isdigit() or kind not in spec_changes.KINDS:
        raise HTTPException(
            status_code=422, detail="Choose a customer, a chemical and spec or warning limits."
        )
    return spec_changes.SetKey(int(customer), int(chemical), location.strip()[:20], kind)


@router.get("/specs", name="specs_home")
def specs_home(request: Request, user: RequireStaff, db: DB, reference: Reference):
    _allowed(db, user)
    with refdata.slim_session(slim_engine_for(request)) as slim:
        on_file = spec_changes.sets_on_file(slim)
    return render(
        request,
        "specs/index.html",
        {
            "on_file": on_file,
            "proposals": spec_changes.proposals(db),
            "customers": sorted(reference.customer_choices(), key=lambda c: c.name.casefold()),
            "chemicals": sorted(reference.chemical_choices(), key=lambda c: c.name.casefold()),
            "kinds": spec_changes.KINDS,
            "reference": reference,
            "may_propose": spec_changes.may_propose(db, user),
        },
    )


@router.get("/specs/edit", name="specs_edit")
def specs_edit(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    customer: str = "",
    chemical: str = "",
    location: str = "",
    kind: str = "specification",
):
    if not spec_changes.may_propose(db, user):
        raise HTTPException(
            status_code=403, detail="Proposing needs the quality or quality manager capability."
        )
    key = _key(customer, chemical, location, kind)
    with refdata.slim_session(slim_engine_for(request)) as slim:
        current, unit = spec_changes.current_set(
            slim, key.customer_id, key.chemical_id, key.location, key.kind
        )
    return render(
        request,
        "specs/edit.html",
        {
            "key": key,
            "rows": sorted(current.items()) + [("", "")] * BLANK_ROWS,
            "unit": unit or "ppb",
            "pending": spec_changes.pending_for(db, key),
            "reference": reference,
            "kinds": spec_changes.KINDS,
        },
    )


@router.post("/specs/proposals", name="specs_propose")
def specs_propose(request: Request, user: RequireStaff, db: DB, reference: Reference, form: FormLists):
    key = _key(_one(form, "customer"), _one(form, "chemical"), _one(form, "location"), _one(form, "kind"))
    symbols, values = form.get("element", []), form.get("value", [])
    known = {e.symbol for e in reference.element_choices()}
    back = (
        request.url_for("specs_edit").path
        + "?"
        + urlencode(
            {
                "customer": key.customer_id,
                "chemical": key.chemical_id,
                "location": key.location,
                "kind": key.kind,
            }
        )
    )
    try:
        after = spec_changes.parse_limits(list(zip(symbols, values, strict=False)), known)
        db.commit()  # SQLite: end this request's read before reading SLIM's tables on their own connection
        with refdata.slim_session(slim_engine_for(request)) as slim:
            row = spec_changes.propose(
                db, slim, key, after, _one(form, "unit"), _one(form, "reason"), user, reference
            )
    except spec_changes.SpecRefused as refused:
        db.rollback()
        flash(request, f"Not proposed: {refused}", "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, f"Proposal {row.id} made: a quality manager or a supervisor other than you decides it.")
    return RedirectResponse(request.url_for("specs_proposal", proposal_id=row.id).path, status_code=303)


@router.get("/specs/proposals/{proposal_id}", name="specs_proposal")
def specs_proposal(request: Request, proposal_id: int, user: RequireStaff, db: DB, reference: Reference):
    _allowed(db, user)
    row = db.get(SpecProposalRow, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such proposal.")
    decision = spec_changes.decision_of(db, row.id)
    elements = sorted(set(row.before) | set(row.after))
    nc_link = None
    if decision is not None and decision.now_differ:
        nc_link = (
            request.url_for("quality_ncs").path
            + "?"
            + urlencode(
                {
                    "title": f"Released results judged by changed spec limits (proposal {row.id})",
                    "description": "Released before the change, they would now read differently: "
                    + ", ".join(decision.now_differ),
                    "ref": f"spec-proposal:{row.id}",
                }
            )
        )
    return render(
        request,
        "specs/proposal.html",
        {
            "p": row,
            "d": decision,
            "elements": elements,
            "reference": reference,
            "kinds": spec_changes.KINDS,
            "may_decide": decision is None
            and spec_changes.may_decide(db, user)
            and row.proposed_by_id != str(user.id),
            "nc_link": nc_link,
        },
    )


@router.post("/specs/proposals/{proposal_id}/approve", name="specs_approve")
def specs_approve(request: Request, proposal_id: int, user: RequireStaff, db: DB, reference: Reference):
    row = db.get(SpecProposalRow, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such proposal.")
    lookup = reference_lookup(db, reference, specs_for(request))
    try:
        with refdata.slim_session(slim_engine_for(request)) as slim:
            decision = spec_changes.approve(
                db, slim, row, user, reference, lookup, actor_for(user), release_reads=db.commit
            )
    except spec_changes.SpecRefused as refused:
        db.rollback()
        flash(request, f"Not approved: {refused}", "error")
        return _back_to(request, proposal_id)
    db.commit()
    flash(
        request,
        f"Approved: the limits are on file. Re-evaluated {len(decision.reevaluated)} batch"
        f"{'' if len(decision.reevaluated) == 1 else 'es'}; {len(decision.now_differ)} released result"
        f"{'' if len(decision.now_differ) == 1 else 's'} would now read differently.",
    )
    return _back_to(request, proposal_id)


@router.post("/specs/proposals/{proposal_id}/reject", name="specs_reject")
def specs_reject(request: Request, proposal_id: int, user: RequireStaff, db: DB, form: FormLists):
    row = db.get(SpecProposalRow, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such proposal.")
    try:
        spec_changes.reject(db, row, user, _one(form, "reason"))
    except spec_changes.SpecRefused as refused:
        db.rollback()
        flash(request, f"Not rejected: {refused}", "error")
        return _back_to(request, proposal_id)
    db.commit()
    flash(request, "Rejected: the limits on file are unchanged.")
    return _back_to(request, proposal_id)


def _back_to(request: Request, proposal_id: int) -> RedirectResponse:
    return RedirectResponse(request.url_for("specs_proposal", proposal_id=proposal_id).path, status_code=303)
