"""Staff pages for slim-quality's batch 5b (slim-data D-131…D-133).

    /quality/uncertainty               the signed estimates; ?series=…&start=…&end=…&u_cref=… works one
                                       out (a GET: nothing is written); POST signs it (a quality manager)
    /quality/competence                the authorisation register and the acts outside it; POST grants one
                                       (a quality manager); POST …/{id}/entries withdraws or notes; POST
                                       /quality/competence/nc raises an NC from a finding
    /quality/trends                    turnaround by month, customer and analysis, open and late, and
                                       each customer's trend with their complaints

The quality pages need a quality capability (403); refusals by the facts are 409, a bad form 422.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from slim_quality.domain.base import NotAllowed
from slim_quality.domain.competence import Act, Authorisation, AuthorisationEntry, AuthorisationEntryKind
from slim_quality.domain.uncertainty import UncertaintyEstimate
from slim_quality.service import competence, trends
from slim_quality.service import quality as q
from slim_quality.service import uncertainty as unc
from sqlalchemy.orm import Session

from slim_lims.api import get_reference
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.quality import SlimDataQc
from slim_lims.quality_insights import TrDates, lab_acts, staff_people
from slim_lims.quality_records_routes import _actor, _date, _number, _write
from slim_lims.reference import ReferenceData
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Clock = Annotated[LabClock, Depends(lab_clock)]
Reference = Annotated[ReferenceData, Depends(get_reference)]
Text = Annotated[str, Form(max_length=2000)]


# ---------------------------------------------------------------- uncertainty (D-131)


def _window(clock: LabClock, start: str, end: str) -> tuple[datetime, datetime]:
    """The lab's days, whole: from the first's midnight to the last's end."""
    first, last = _date(start, "From"), _date(end, "To")
    return (
        datetime.combine(first, time(0), clock.zone),
        datetime.combine(last + timedelta(days=1), time(0), clock.zone) - timedelta(microseconds=1),
    )


def _fraction(text: str) -> Decimal:
    """u(Cref), typed as a percentage."""
    value = _number(text, "u(Cref) %")
    if value < 0 or value >= 100:
        raise HTTPException(status_code=422, detail="u(Cref) %: between 0 and 100")
    return value / 100


@router.get("/quality/uncertainty", name="quality_uncertainty", response_class=HTMLResponse)
def quality_uncertainty(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    series: str = "",
    start: str = "",
    end: str = "",
    u_cref: str = "",
    basis: str = "",
):
    actor = _actor(db, user)
    source = SlimDataQc(db)
    today = clock.today()
    draft: UncertaintyEstimate | None = None
    refused = None
    if series:
        try:
            window = _window(clock, start, end)
            draft = unc.estimate(db, source, series, *window, _fraction(u_cref or "0"), basis)
        except NotAllowed as error:
            refused = str(error)
        except HTTPException as error:
            refused = error.detail
    return render(
        request,
        "quality/uncertainty.html",
        {
            "actor": actor,
            "current": sorted(unc.current(db).values(), key=lambda e: e.key.label),
            "candidates": unc.precision_series(source.series()),
            "policy": q.current_profile(db).uncertainty,
            "draft": draft,
            "refused": refused,
            "form": {
                "series": series,
                "start": start or (today - timedelta(days=90)).isoformat(),
                "end": end or today.isoformat(),
                "u_cref": u_cref or "0.5",
                "basis": basis,
            },
            "pct": UncertaintyEstimate.percent,
        },
    )


@router.post("/quality/uncertainty", name="quality_uncertainty_sign")
def quality_uncertainty_sign(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    series: Annotated[str, Form(max_length=400)],
    start: Annotated[str, Form()],
    end: Annotated[str, Form()],
    u_cref: Annotated[str, Form(max_length=20)],
    basis: Text = "",
):
    actor = _actor(db, user)
    window = _window(clock, start, end)
    cref = _fraction(u_cref)
    found = _write(db, lambda: unc.sign(db, SlimDataQc(db), series, *window, cref, basis, actor))
    flash(request, f"{found.key.label}: U = {found.shown} % (k = {found.k}) signed.")
    return RedirectResponse(str(request.url_for("quality_uncertainty")), status_code=303)


# ---------------------------------------------------------------- competence (D-132)


def _findings(db: Session, reference: ReferenceData, clock: LabClock):
    return competence.findings(db, lab_acts(db, reference, clock), day_of=lambda at: clock.local(at).date())


@router.get("/quality/competence", name="quality_competence", response_class=HTMLResponse)
def quality_competence(request: Request, user: RequireStaff, db: DB, clock: Clock, reference: Reference):
    actor = _actor(db, user)
    methods = sorted({a.code for a in reference.catalog_analyses()})
    return render(
        request,
        "quality/competence.html",
        {
            "actor": actor,
            "register": competence.register(db),
            "findings": _findings(db, reference, clock),
            "people": sorted(staff_people(db).values(), key=lambda p: p.display),
            "acts": list(Act),
            "methods": methods,
            "today": clock.today(),
        },
    )


@router.post("/quality/competence", name="quality_competence_grant")
def quality_competence_grant(
    request: Request,
    user: RequireStaff,
    db: DB,
    person_id: Annotated[str, Form(max_length=100)],
    act: Annotated[int, Form()],
    valid_from: Annotated[str, Form()],
    evidence: Text,
    method: Annotated[str, Form(max_length=50)] = "",  # "": every method
    valid_until: Annotated[str, Form()] = "",
):
    actor = _actor(db, user)
    person = staff_people(db).get(person_id)
    if person is None:
        raise HTTPException(status_code=422, detail="Person: choose a member of staff")
    if act not in {int(a) for a in Act}:
        raise HTTPException(status_code=422, detail="Act: choose one")

    def grant():
        return competence.grant(
            db,
            Authorisation(
                person=person,
                act=Act(act),
                method=method.strip() or None,
                valid_from=_date(valid_from, "From"),
                valid_until=_date(valid_until, "Until", required=False),
                evidence=evidence,
                granted_by=actor,
            ),
        )

    found = _write(db, grant)
    flash(request, f"Authorised: {found}.")
    return RedirectResponse(str(request.url_for("quality_competence")), status_code=303)


@router.post("/quality/competence/{authorisation_id}/entries", name="quality_competence_entry")
def quality_competence_entry(
    request: Request,
    authorisation_id: int,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    kind: Annotated[int, Form()],
    note: Text,
    on: Annotated[str, Form()] = "",
):
    actor = _actor(db, user)
    if kind not in {int(k) for k in AuthorisationEntryKind}:
        raise HTTPException(status_code=422, detail="Kind: withdrawn or a note")

    def add():
        day = _date(on, "On", required=False) or clock.today()
        entry = AuthorisationEntry(kind=AuthorisationEntryKind(kind), on=day, note=note, by=actor)
        return competence.add_entry(db, authorisation_id, entry)

    found = _write(db, add)
    flash(request, f"{found}: {AuthorisationEntryKind(kind).label.lower()} recorded.")
    return RedirectResponse(str(request.url_for("quality_competence")), status_code=303)


@router.post("/quality/competence/nc", name="quality_competence_nc")
def quality_competence_nc(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    reference: Reference,
    ref: Annotated[str, Form(max_length=100)],
):
    actor = _actor(db, user)
    finding = next((f for f in _findings(db, reference, clock) if f.act.ref == ref), None)
    if finding is None:
        raise HTTPException(status_code=409, detail="That act is no longer outside an authorisation.")
    nc = _write(db, lambda: competence.finding_nc(db, finding, actor))
    flash(request, f"{nc.number} raised.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc.id)), status_code=303)


# ---------------------------------------------------------------- trends (D-133)


@router.get("/quality/trends", name="quality_trends", response_class=HTMLResponse)
def quality_trends(
    request: Request, user: RequireStaff, db: DB, clock: Clock, reference: Reference, since: str = ""
):
    actor = _actor(db, user)
    today = clock.today()
    start: date | None = _date(since, "Since", required=False) if since else today - timedelta(days=365)
    source = TrDates(db, reference, clock)
    return render(
        request,
        "quality/trends.html",
        {
            "actor": actor,
            "report": trends.turnaround_report(source, today, start),
            "customers": trends.customers(db, source, today, start),
            "since": start,
        },
    )
