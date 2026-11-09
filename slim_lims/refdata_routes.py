"""Reference data upkeep (slim-data D-141): /reference, for whoever holds the *reference data*
capability. Customers are added and edited and chemicals added through slim-domain's services; the
analysis catalog is the app's own. Each change needs a reason and is kept in `reference_changes`."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from slim_lims import capabilities, refdata
from slim_lims.api import get_reference, slim_engine_for
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.domain import RequestType
from slim_lims.models import AnalysisCatalogRow, User
from slim_lims.quotes_routes import FormLists, _ints, _one
from slim_lims.reference import ReferenceData
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]


def _allowed(db: Session, user: User) -> None:
    if not capabilities.can(db, user, capabilities.REFERENCE_DATA):
        raise HTTPException(
            status_code=403, detail="Editing reference data needs the reference data capability."
        )


def _fresh(reference) -> None:
    """The cached copy is reloaded at once, so the change shows on the next page (D-141 #6)."""
    if hasattr(reference, "refresh"):
        reference.refresh()


def _quiet(db: Session) -> None:
    """Ends this request's read before slim-domain writes on its own connection (SQLite allows a write
    only while nobody reads; the app's sessions keep their objects across a commit)."""
    db.commit()


def _back(request: Request, name: str = "reference_home", **params) -> RedirectResponse:
    return RedirectResponse(request.url_for(name, **params).path, status_code=303)


@router.get("/reference", name="reference_home")
def reference_home(request: Request, user: RequireStaff, db: DB, reference: Reference):
    _allowed(db, user)
    with refdata.slim_session(slim_engine_for(request)) as slim:
        customers, chemicals = refdata.customers(slim), refdata.chemicals(slim)
    catalog = {r.analysis_id: r for r in db.query(AnalysisCatalogRow)}
    return render(
        request,
        "reference/index.html",
        {
            "customers": customers,
            "chemicals": chemicals,
            "analyses": sorted(
                (a for a in reference.catalog_analyses() if a.id in catalog), key=lambda a: (a.group, a.name)
            ),
            "elements": reference.element_choices(),
            "preps": refdata.PREP_OPTIONS,
            "instruments": refdata.INSTRUMENTS,
            "changes": refdata.changes(db)[:30],
        },
    )


@router.post("/reference/customers", name="add_customer")
def add_customer(request: Request, user: RequireStaff, db: DB, reference: Reference, form: FormLists):
    _allowed(db, user)
    fields = {f: _one(form, f) for f in refdata.CUSTOMER_FIELDS}
    _quiet(db)
    with refdata.slim_session(slim_engine_for(request)) as slim:
        try:
            made = refdata.add_customer(db, slim, fields, _one(form, "reason"), user)
        except refdata.RefRefused as refused:
            db.rollback()
            flash(request, f"Customer not added: {refused}", "error")
            return _back(request)
    db.commit()
    _fresh(reference)
    flash(request, f"Customer {made.name} added.")
    return _back(request)


@router.get("/reference/customers/{customer_id}", name="edit_customer")
def edit_customer_page(request: Request, customer_id: int, user: RequireStaff, db: DB):
    _allowed(db, user)
    with refdata.slim_session(slim_engine_for(request)) as slim:
        found = refdata.customer(slim, customer_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such customer.")
    return render(
        request,
        "reference/customer.html",
        {
            "c": found,
            "fields": refdata.CUSTOMER_FIELDS,
            "changes": refdata.changes(db, refdata.CUSTOMER, customer_id),
        },
    )


@router.post("/reference/customers/{customer_id}", name="save_customer")
def save_customer(
    request: Request, customer_id: int, user: RequireStaff, db: DB, reference: Reference, form: FormLists
):
    _allowed(db, user)
    fields = {f: _one(form, f) for f in refdata.CUSTOMER_FIELDS}
    _quiet(db)
    with refdata.slim_session(slim_engine_for(request)) as slim:
        try:
            saved = refdata.edit_customer(db, slim, customer_id, fields, _one(form, "reason"), user)
        except refdata.RefRefused as refused:
            db.rollback()
            flash(request, f"Not saved: {refused}", "error")
            return _back(request, "edit_customer", customer_id=customer_id)
    db.commit()
    _fresh(reference)
    flash(request, f"Customer {saved.name} saved. Requests already made keep the name and address they had.")
    return _back(request, "edit_customer", customer_id=customer_id)


@router.post("/reference/chemicals", name="add_chemical")
def add_chemical(request: Request, user: RequireStaff, db: DB, reference: Reference, form: FormLists):
    _allowed(db, user)
    known = {e.id for e in reference.element_choices()}
    fields = {k: _one(form, k) for k in ("name", "metals_instrument", *refdata.PREP_OPTIONS)}
    _quiet(db)
    with refdata.slim_session(slim_engine_for(request)) as slim:
        try:
            made = refdata.add_chemical(
                db, slim, fields, set(_ints(form.get("ked", []))) & known, _one(form, "reason"), user
            )
        except refdata.RefRefused as refused:
            db.rollback()
            flash(request, f"Chemical not added: {refused}", "error")
            return _back(request)
    db.commit()
    _fresh(reference)
    flash(request, f"Chemical {made.name} added: staff can now match requests to it at check-in.")
    return _back(request)


@router.get("/reference/analyses/{analysis_id}", name="edit_analysis")
def edit_analysis_page(request: Request, analysis_id: int, user: RequireStaff, db: DB, reference: Reference):
    _allowed(db, user)
    row = db.get(AnalysisCatalogRow, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That analysis is not in the catalog.")
    return render(
        request,
        "reference/analysis.html",
        {
            "row": row,
            "analysis": reference.analysis(analysis_id),
            "types": list(RequestType),
            "chosen": {int(t) for t in row.request_types.split(",") if t},
            "changes": refdata.changes(db, refdata.ANALYSIS, analysis_id),
        },
    )


@router.post("/reference/analyses/{analysis_id}", name="save_analysis")
def save_analysis(
    request: Request, analysis_id: int, user: RequireStaff, db: DB, reference: Reference, form: FormLists
):
    _allowed(db, user)
    types = {
        RequestType(t) for t in _ints(form.get("request_types", [])) if t in RequestType._value2member_map_
    }
    edit = refdata.CatalogEdit(
        _one(form, "group_name"), frozenset(types), _one(form, "portal_selectable") == "1"
    )
    try:
        refdata.edit_catalog(db, analysis_id, edit, _one(form, "reason"), user)
    except refdata.RefRefused as refused:
        db.rollback()
        flash(request, f"Not saved: {refused}", "error")
        return _back(request, "edit_analysis", analysis_id=analysis_id)
    db.commit()
    _fresh(reference)
    flash(request, "Saved: the request form shows it from now on.")
    return _back(request, "edit_analysis", analysis_id=analysis_id)
