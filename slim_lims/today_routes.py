"""The day's board (slim-data D-138): /today for staff, /today/setup for whoever sets up the lab's
sections and screens (the *lab displays* capability), and /display/{token}: a screen's page, no login.

A screen shows customer names only while the lab-wide switch says there are no visitors; staff on
/today always see them. A display page is read-only and reloads itself every minute (no script).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from slim_lims import capabilities, today
from slim_lims.api import get_reference
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.models import DisplayLinkRow, LabSectionRow, User
from slim_lims.reference import ReferenceData
from slim_lims.scope import Scope
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]
Clock = Annotated[LabClock, Depends(lab_clock)]
REFRESH_SECONDS = 60


def _section(db: Session, section_id: str | int | None) -> LabSectionRow | None:
    if section_id in (None, ""):
        return None
    found = db.get(LabSectionRow, int(section_id)) if str(section_id).isdigit() else None
    if found is None:
        raise HTTPException(status_code=404, detail="No such section.")
    return found


def _setup_allowed(db: Session, user: User) -> None:
    if not capabilities.can(db, user, capabilities.LAB_DISPLAYS):
        raise HTTPException(status_code=403, detail="Setting up screens needs the lab displays capability.")


def _to_setup(request: Request) -> RedirectResponse:
    return RedirectResponse(request.url_for("today_setup").path, status_code=303)


@router.get("/today", name="today")
def today_page(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
    section: str = "",
    day: str = "",
):
    """What is due today (or the next working day) and where it has got to; the whole lab or a section."""
    chosen = _section(db, section)
    now = clock.today()
    when = today.next_working_day(now) if day == "next" else now
    return render(
        request,
        "today/index.html",
        {
            "board": today.day_board(db, reference, now, Scope.for_user(user), section=chosen, day=when),
            "sections": today.sections(db),
            "chosen": chosen,
            "next_day": day == "next",
            "names_shown": _names(db),
            "show_names": True,
            "can_set_up": capabilities.can(db, user, capabilities.LAB_DISPLAYS),
        },
    )


def _names(db: Session) -> bool:
    setting = today.names_shown(db)
    return setting is not None and setting.names_shown


@router.post("/today/names", name="today_names")
def set_names(
    request: Request,
    user: RequireStaff,
    db: DB,
    shown: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
):
    """The lab-wide switch: visitors in the lab (names hidden) or none (names shown). Any staff member."""
    today.set_names_shown(db, shown == "1", user.email)
    db.commit()
    flash(request, f"The lab's screens now {'show' if shown == '1' else 'hide'} customer names.")
    target = request.url_for("today_setup" if back == "setup" else "today").path
    return RedirectResponse(target, status_code=303)


# ---------------------------------------------------------------- setup


@router.get("/today/setup", name="today_setup")
def setup_page(request: Request, user: RequireStaff, db: DB, reference: Reference):
    _setup_allowed(db, user)
    owner = today.section_of(db)
    return render(
        request,
        "today/setup.html",
        {
            "sections": today.sections(db),
            "analyses": reference.catalog_analyses(),
            "owner": owner,
            "links": today.links(db),
            "names_shown": _names(db),
        },
    )


@router.post("/today/sections", name="add_section")
def add_section(
    request: Request, user: RequireStaff, db: DB, name: Annotated[str, Form(max_length=200)] = ""
):
    _setup_allowed(db, user)
    try:
        row = today.add_section(db, name)
    except today.SectionRefused as refused:
        flash(request, str(refused), "error")
        return _to_setup(request)
    db.commit()
    flash(request, f"Section {row.name} added: tick the analyses it does.")
    return _to_setup(request)


@router.post("/today/sections/{section_id}/analyses", name="section_analyses")
def section_analyses(
    request: Request,
    section_id: int,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    analysis: Annotated[list[str] | None, Form()] = None,
):
    _setup_allowed(db, user)
    section = _section(db, section_id)
    known = {a.id for a in reference.catalog_analyses()}
    ticked = {int(v) for v in analysis or () if v.isdigit()} & known
    today.set_analyses(db, section, ticked)
    db.commit()
    flash(request, f"{section.name} now does {len(ticked)} analys{'is' if len(ticked) == 1 else 'es'}.")
    return _to_setup(request)


@router.post("/today/sections/{section_id}/delete", name="remove_section")
def remove_section(request: Request, section_id: int, user: RequireStaff, db: DB):
    _setup_allowed(db, user)
    section = _section(db, section_id)
    name = section.name
    today.remove_section(db, section)
    db.commit()
    flash(request, f"Section {name} removed, with its screen links.")
    return _to_setup(request)


@router.post("/today/links", name="add_display_link")
def add_link(
    request: Request,
    user: RequireStaff,
    db: DB,
    section: Annotated[str, Form()] = "",
    label: Annotated[str, Form(max_length=200)] = "",
):
    _setup_allowed(db, user)
    row, token = today.add_link(db, label, _section(db, section), user.email)
    db.commit()
    url = str(request.url_for("display", token=token))
    flash(request, f"Screen link {row.label}: {url} (shown once; copy it to the screen now).")
    return _to_setup(request)


@router.post("/today/links/{link_id}/revoke", name="revoke_display_link")
def revoke_link(request: Request, link_id: int, user: RequireStaff, db: DB, clock: Clock):
    _setup_allowed(db, user)
    link = db.get(DisplayLinkRow, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="No such screen link.")
    today.revoke_link(db, link, user.email, clock.now())
    db.commit()
    flash(request, f"Screen link {link.label} revoked: that screen now shows nothing.")
    return _to_setup(request)


# ---------------------------------------------------------------- a screen in the lab


@router.get("/display/{token}", name="display")
def display(request: Request, token: str, db: DB, reference: Reference, clock: Clock):
    """A screen's page: no login, read-only. Unknown or revoked: 404, nothing about the lab."""
    link = today.link_for(db, token)
    if link is None:
        raise HTTPException(status_code=404, detail="This screen link is not valid.")
    section = db.get(LabSectionRow, link.section_id) if link.section_id is not None else None
    now = clock.today()
    response = render(
        request,
        "today/display.html",
        {
            "board": today.day_board(db, reference, now, Scope(customer_id=None), section=section),
            "link": link,
            "show_names": _names(db),
            "refresh": REFRESH_SECONDS,
            "updated": clock.now(),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response
