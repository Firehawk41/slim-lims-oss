"""Staff pages for slim-quality (D-109): for those with a quality capability.

    /quality                          every series at a glance, worst first; open changes; last review
    /quality/series?id=…              the chart, its limits, flags, events and notes; note, event, limits
    POST /quality/notes               a note on a point
    POST /quality/events              maintenance, a new lot, a repair: drawn on the instrument's charts
    POST /quality/limits              propose limits: from a baseline (dates, method) or entered
    /quality/profile                  the tools, on or off, and their settings; backtest or propose
    POST /quality/profile             action=backtest (nothing stored) or action=propose
    /quality/changes                  open changes and the history; POST /quality/changes/{id}/decide
    /quality/reviews                  signed QC reviews; POST signs one (quality manager)
    /quality/audit                    reports issued in a period; "audit a random one" (D-113)
    /quality/audit/{issue}            the vertical audit of one report; POST records it (findings → an NC)
    /quality/ncs                      the nonconformity register; POST raises one (D-114)
    /quality/ncs/{id}                 one NC: what its window touched, its entries; POST …/entries adds one

Without a quality capability: 403. Deciding and signing need the manager's (slim-quality refuses too).
"""

import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, get_args

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from slim_data.persistence._rows import NotFound as DataNotFound
from slim_data.persistence.results import get_issue
from slim_data.service.audit import issues_between
from slim_quality.domain.audits import AuditOutcome
from slim_quality.domain.changes import NotAllowed
from slim_quality.domain.enums import EventKind, LimitMethod, Measure, Severity
from slim_quality.domain.limits import Limits
from slim_quality.domain.nonconformity import (
    EntryKind,
    ImpactDecision,
    ImpactLine,
    NcEntry,
    NcSource,
    NcState,
)
from slim_quality.domain.profile import MeasurePolicy, Profile, ToolSetting, differences
from slim_quality.domain.series import SeriesKey
from slim_quality.monitors.registry import TOOLS
from slim_quality.monitors.runner import ProfileError
from slim_quality.persistence import store
from slim_quality.service import quality as q
from slim_quality.service.svg import render as render_svg
from sqlalchemy.orm import Session

from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.quality import (
    SlimDataQc,
    audit_page,
    impact_label,
    quality_actor,
    random_issue,
    result_ref,
    touched,
)
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Clock = Annotated[LabClock, Depends(lab_clock)]


def _actor(db: Session, user):
    actor = quality_actor(db, user)
    if actor is None:
        raise HTTPException(status_code=403, detail="The quality pages need a quality capability.")
    return actor


def _back(request: Request, name: str, **query) -> RedirectResponse:
    url = str(request.url_for(name))
    if query:
        from urllib.parse import urlencode

        url += "?" + urlencode(query)
    return RedirectResponse(url, status_code=303)


def _chart(db: Session, slug: str):
    """The series a form names, or 404: ids come from the page but are typed by whoever posts."""
    try:
        SeriesKey.from_slug(slug)
        return q.chart(db, SlimDataQc(db), slug)
    except (ValueError, store.NotFound):
        raise HTTPException(status_code=404, detail="No such series.") from None


def _day(text: str, clock: LabClock, end: bool = False) -> datetime:
    try:
        day = date.fromisoformat(text.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Not a date: {text!r}") from None
    return datetime.combine(day, time.max if end else time.min, clock.zone)


def _number(text: str, what: str) -> Decimal | None:
    if not text.strip():
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        raise HTTPException(status_code=422, detail=f"{what}: not a number ({text!r})") from None


# ---------------------------------------------------------------- overview and series


@router.get("/quality", name="quality_home", response_class=HTMLResponse)
def quality_home(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    instrument: str = "",
    measure: str = "",
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    actor = _actor(db, user)
    charts = q.charts(db, SlimDataQc(db))
    statuses = q.overview(charts, clock.now(), days)
    instruments = sorted({s.chart.series.key.instrument for s in statuses})
    if instrument:
        statuses = [s for s in statuses if s.chart.series.key.instrument == instrument]
    if measure:
        statuses = [s for s in statuses if str(int(s.chart.series.key.measure)) == measure]
    reviews = store.reviews(db)
    return render(
        request,
        "quality/index.html",
        {
            "actor": actor,
            "statuses": statuses,
            "instruments": instruments,
            "measures": list(Measure),
            "filters": {"instrument": instrument, "measure": measure, "days": days},
            "profile": q.current_profile(db),
            "open_changes": store.list_changes(db, open_only=True),
            "last_review": reviews[0] if reviews else None,
        },
    )


@router.get("/quality/series", name="quality_series", response_class=HTMLResponse)
def quality_series(request: Request, user: RequireStaff, db: DB, clock: Clock, id: str = ""):
    actor = _actor(db, user)
    chart = _chart(db, id)
    key = chart.series.key
    events = [e for e in store.events(db, key.instrument) if e.analyte in (None, key.analyte)]
    points = chart.series.points
    return render(
        request,
        "quality/series.html",
        {
            "actor": actor,
            "chart": chart,
            "key": key,
            "svg": render_svg(chart, events, clock.zone),
            "zone": clock.zone,
            "flags": list(reversed(chart.flags)),
            "points": points,
            "events": events,
            "notes": store.notes(db, key.slug),
            "limit_sets": store.limit_sets(db, series=key.slug),
            "methods": list(LimitMethod),
            "event_kinds": list(EventKind),
            "first_day": points[0].at.astimezone(clock.zone).date() if points else clock.today(),
            "window_days": timedelta(days=14),  # a flag's NC looks back two weeks by default
            "today": clock.today(),
        },
    )


@router.post("/quality/notes", name="quality_note")
def quality_note(
    request: Request,
    user: RequireStaff,
    db: DB,
    id: Annotated[str, Form()],
    ref: Annotated[str, Form()],
    note: Annotated[str, Form(max_length=1000)],
):
    actor = _actor(db, user)
    if not note.strip():
        raise HTTPException(status_code=422, detail="Write the note.")
    if ref not in {p.ref for p in _chart(db, id).series.points}:
        raise HTTPException(status_code=422, detail="That point is not on this chart.")
    q.add_note(db, id, ref, note.strip(), actor)
    db.commit()
    flash(request, "Note added.")
    return _back(request, "quality_series", id=id)


@router.post("/quality/events", name="quality_event")
def quality_event(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    kind: Annotated[int, Form()],
    instrument: Annotated[str, Form(max_length=100)],
    on: Annotated[str, Form()],
    note: Annotated[str, Form(max_length=1000)],
    analyte: Annotated[str, Form(max_length=50)] = "",
    id: Annotated[str, Form()] = "",
):
    actor = _actor(db, user)
    try:
        q.record_event(
            db, EventKind(kind), instrument.strip(), _day(on, clock), note.strip(), actor, analyte.strip()
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=f"The event: {error}") from None
    db.commit()
    flash(request, "Event recorded.")
    return _back(request, "quality_series", id=id) if id else _back(request, "quality_home")


@router.post("/quality/limits", name="quality_limits")
def quality_limits(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    id: Annotated[str, Form()],
    method: Annotated[int, Form()],
    reason: Annotated[str, Form(max_length=1000)],
    effective: Annotated[str, Form()],
    start: Annotated[str, Form()] = "",
    end: Annotated[str, Form()] = "",
    center: Annotated[str, Form()] = "",
    sd: Annotated[str, Form()] = "",
    upper: Annotated[str, Form()] = "",
    basis: Annotated[str, Form(max_length=300)] = "",
):
    actor = _actor(db, user)
    chart = _chart(db, id)
    try:
        if LimitMethod(method) == LimitMethod.CERTIFICATE:
            c, s = _number(center, "Centre"), _number(sd, "SD")
            if c is None or s is None:
                raise ValueError("entered limits need a centre and an SD")
            limits = Limits(
                method=LimitMethod.CERTIFICATE, center=c, sd=s, upper=_number(upper, "Upper"), basis=basis
            )
        else:
            limits = q.baseline_limits(
                chart.series, LimitMethod(method), _day(start, clock), _day(end, clock, True)
            )
        change = q.propose_limits(
            db, chart.series.key, limits, _day(effective, clock), reason.strip() or "-", actor
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=f"The limits: {error}") from None
    db.commit()
    flash(request, f"Limits proposed (change {change.id}): a quality manager decides.")
    return _back(request, "quality_series", id=id)


# ---------------------------------------------------------------- the profile


def _param_fields(tool) -> list[dict[str, Any]]:
    fields = []
    for name, info in tool.Params.model_fields.items():
        options = None
        args = get_args(info.annotation)
        if args and getattr(info.annotation, "__origin__", None) is frozenset:
            options = list(get_args(args[0]))
        fields.append({"name": name, "options": options})
    return fields


def _summary(tool) -> str:
    """The first line of the tool's module docstring: what it is for."""
    doc = sys.modules[type(tool).__module__].__doc__ or ""
    return doc.strip().split("\n")[0]


def _profile_context(db: Session, profile: Profile) -> dict[str, Any]:
    tools = []
    for key, tool in TOOLS.items():
        setting = profile.setting(key) or ToolSetting(tool=key, enabled=False)
        params = tool.Params.model_validate(setting.params).model_dump(mode="json")
        tools.append(
            {
                "key": key,
                "title": tool.title,
                "doc": _summary(tool),
                "setting": setting,
                "params": params,
                "fields": _param_fields(tool),
                "codes": {
                    code: setting.severities.get(code, default) for code, default in tool.codes.items()
                },
            }
        )
    return {
        "profile": profile,
        "tools": tools,
        "measures": list(Measure),
        "policies": {m: profile.policy(m) for m in Measure},
        "methods": [LimitMethod.HISTORY, LimitMethod.ROBUST],
        "severities": list(Severity),
    }


def profile_from_form(
    enabled: list[str],
    measure: list[str],
    rule: list[str],
    param_key: list[str],
    param_value: list[str],
    sev_key: list[str],
    sev_value: list[str],
    pol_measure: list[str],
    pol_method: list[str],
    pol_points: list[str],
    pol_low: list[str],
    pol_high: list[str],
) -> Profile:
    """The profile page's form as a Profile. Lists come in the page's order (every text input and
    select is always sent), so keys and values pair up; checkboxes send only what is ticked."""
    settings = []
    for key, tool in TOOLS.items():
        params: dict[str, Any] = {}
        for k, v in zip(param_key, param_value, strict=False):
            tool_key, _, name = k.partition("|")
            if tool_key == key and v.strip():
                params[name] = v.strip()
        for field in _param_fields(tool):
            if field["options"] is not None:
                params[field["name"]] = [r.partition("|")[2] for r in rule if r.partition("|")[0] == key]
        severities = {
            k.partition("|")[2]: Severity[v]
            for k, v in zip(sev_key, sev_value, strict=False)
            if k.partition("|")[0] == key
        }
        measures = frozenset(Measure(int(m.partition("|")[2])) for m in measure if m.partition("|")[0] == key)
        settings.append(
            ToolSetting(
                tool=key, enabled=key in enabled, measures=measures, params=params, severities=severities
            )
        )
    policies = {}
    for m, method, points, low, high in zip(
        pol_measure, pol_method, pol_points, pol_low, pol_high, strict=False
    ):
        policies[Measure(int(m))] = MeasurePolicy(
            method=LimitMethod(int(method)),
            baseline_points=int(points),
            window_low=_number(low, "Window low"),
            window_high=_number(high, "Window high"),
        )
    return Profile(tools=tuple(settings), measures=policies)


@router.get("/quality/profile", name="quality_profile", response_class=HTMLResponse)
def quality_profile(request: Request, user: RequireStaff, db: DB):
    actor = _actor(db, user)
    return render(
        request, "quality/profile.html", {"actor": actor, **_profile_context(db, q.current_profile(db))}
    )


@router.post("/quality/profile", name="quality_profile_post", response_class=HTMLResponse)
def quality_profile_post(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    action: Annotated[str, Form()],
    reason: Annotated[str, Form(max_length=1000)] = "",
    enabled: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies defaults)
    measure: Annotated[list[str], Form()] = [],  # noqa: B006
    rule: Annotated[list[str], Form()] = [],  # noqa: B006
    param_key: Annotated[list[str], Form()] = [],  # noqa: B006
    param_value: Annotated[list[str], Form()] = [],  # noqa: B006
    sev_key: Annotated[list[str], Form()] = [],  # noqa: B006
    sev_value: Annotated[list[str], Form()] = [],  # noqa: B006
    pol_measure: Annotated[list[str], Form()] = [],  # noqa: B006
    pol_method: Annotated[list[str], Form()] = [],  # noqa: B006
    pol_points: Annotated[list[str], Form()] = [],  # noqa: B006
    pol_low: Annotated[list[str], Form()] = [],  # noqa: B006
    pol_high: Annotated[list[str], Form()] = [],  # noqa: B006
):
    actor = _actor(db, user)
    try:
        candidate = profile_from_form(
            enabled,
            measure,
            rule,
            param_key,
            param_value,
            sev_key,
            sev_value,
            pol_measure,
            pol_method,
            pol_points,
            pol_low,
            pol_high,
        )
        if action == "propose":
            if not reason.strip():
                raise ValueError("say why: the reason goes on the change")
            change = q.propose_profile(db, candidate, reason.strip(), actor)
            db.commit()
            flash(request, f"Profile change {change.id} proposed: a quality manager decides.")
            return _back(request, "quality_changes")
        since = clock.now() - timedelta(days=180)
        rows = q.backtest(db, SlimDataQc(db), candidate, since)
    except (ValueError, ValidationError, ProfileError, KeyError) as error:
        context = {"actor": actor, **_profile_context(db, q.current_profile(db)), "error": str(error)}
        return render(request, "quality/profile.html", context, 422)
    context = {"actor": actor, **_profile_context(db, candidate), "backtest": rows, "reason": reason}
    return render(request, "quality/profile.html", context)


# ---------------------------------------------------------------- changes and reviews


@router.get("/quality/changes", name="quality_changes", response_class=HTMLResponse)
def quality_changes(request: Request, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    changes = store.list_changes(db)
    diffs = {
        c.id: differences(q.current_profile(db, as_of=c.proposed_at), c.profile) for c in changes if c.profile
    }
    return render(
        request,
        "quality/changes.html",
        {
            "zone": clock.zone,
            "diffs": diffs,
            "actor": actor,
            "open": [c for c in changes if c.decision is None],
            "decided": [c for c in changes if c.decision],
        },
    )


@router.post("/quality/changes/{change_id}/decide", name="quality_decide")
def quality_decide(
    request: Request,
    change_id: int,
    user: RequireStaff,
    db: DB,
    approve: Annotated[str, Form()],
    note: Annotated[str, Form(max_length=1000)] = "",
):
    actor = _actor(db, user)
    try:
        change = q.decide(db, change_id, approve == "1", note.strip(), actor)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="No such change.") from None
    except NotAllowed as refused:
        raise HTTPException(status_code=403, detail=str(refused)) from None
    except q.ChangeConflict as conflict:
        raise HTTPException(status_code=409, detail=str(conflict)) from None
    db.commit()
    flash(request, f"Change {change.id} {change.status.name.lower()}.")
    return _back(request, "quality_changes")


@router.get("/quality/reviews", name="quality_reviews", response_class=HTMLResponse)
def quality_reviews(request: Request, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    today = clock.today()
    first = today.replace(day=1)
    previous_end = first - timedelta(days=1)
    return render(
        request,
        "quality/reviews.html",
        {
            "actor": actor,
            "reviews": store.reviews(db),
            "zone": clock.zone,
            "suggested": (previous_end.replace(day=1), previous_end),
        },
    )


@router.post("/quality/reviews", name="quality_sign_review")
def quality_sign_review(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    period_from: Annotated[str, Form()],
    period_to: Annotated[str, Form()],
    comment: Annotated[str, Form(max_length=4000)],
):
    actor = _actor(db, user)
    try:
        start, end = date.fromisoformat(period_from), date.fromisoformat(period_to)
        q.sign_review(db, SlimDataQc(db), start, end, comment.strip(), actor, clock.zone)
    except NotAllowed as refused:
        raise HTTPException(status_code=403, detail=str(refused)) from None
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=f"The review: {error}") from None
    db.commit()
    flash(request, "QC review signed.")
    return _back(request, "quality_reviews")


# ---------------------------------------------------------------- the vertical audit (D-113)


def _period(clock: LabClock, start: str, end: str) -> tuple[date, date]:
    today = clock.today()
    try:
        return (
            date.fromisoformat(start) if start else today - timedelta(days=90),
            date.fromisoformat(end) if end else today,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates as YYYY-MM-DD.") from None


@router.get("/quality/audit", name="quality_audits", response_class=HTMLResponse)
def quality_audits(
    request: Request, user: RequireStaff, db: DB, clock: Clock, start: str = "", end: str = ""
):
    actor = _actor(db, user)
    first, last = _period(clock, start, end)
    issues = list(reversed(issues_between(db, _day(str(first), clock), _day(str(last), clock, True))))
    records = store.audits(db)
    latest = {}
    for r in reversed(records):
        latest[r.subject_ref] = r
    return render(
        request,
        "quality/audits.html",
        {
            "actor": actor,
            "issues": issues,
            "latest": latest,
            "records": records[:20],
            "period": (first, last),
            "zone": clock.zone,
        },
    )


@router.get("/quality/audit/random", name="quality_audit_random")
def quality_audit_random(
    request: Request, user: RequireStaff, db: DB, clock: Clock, start: str = "", end: str = ""
):
    _actor(db, user)
    first, last = _period(clock, start, end)
    picked = random_issue(db, _day(str(first), clock), _day(str(last), clock, True))
    if picked is None:
        flash(request, "No report was issued in that period.")
        return _back(request, "quality_audits", start=str(first), end=str(last))
    return RedirectResponse(str(request.url_for("quality_audit", issue_id=picked)), status_code=303)


@router.get("/quality/audit/{issue_id}", name="quality_audit", response_class=HTMLResponse)
def quality_audit(request: Request, issue_id: int, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    try:
        page = audit_page(db, issue_id)
    except DataNotFound:
        raise HTTPException(status_code=404, detail="No such report issue.") from None
    return render(
        request,
        "quality/audit.html",
        {
            "actor": actor,
            "page": page,
            "zone": clock.zone,
            "records": store.audits(db, f"issue:{issue_id}"),
            "outcomes": list(AuditOutcome),
        },
    )


@router.post("/quality/audit/{issue_id}", name="quality_audit_record")
def quality_audit_record(
    request: Request,
    issue_id: int,
    user: RequireStaff,
    db: DB,
    outcome: Annotated[int, Form()],
    findings: Annotated[str, Form(max_length=4000)] = "",
):
    actor = _actor(db, user)
    try:
        issue = get_issue(db, issue_id)
        record = q.record_audit(
            db,
            f"Report {issue.id} ({issue.tr_number})",
            f"issue:{issue.id}",
            AuditOutcome(outcome),
            findings.strip(),
            actor,
        )
    except DataNotFound:
        raise HTTPException(status_code=404, detail="No such report issue.") from None
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=f"The audit: {error}") from None
    db.commit()
    if record.outcome == AuditOutcome.FINDINGS:
        flash(request, "Audit recorded with findings: raise a nonconformity for them below.")
        return _back(
            request,
            "quality_ncs",
            title=f"Audit of report {issue.id} ({issue.tr_number})",
            description=record.findings,
            source=str(int(NcSource.AUDIT)),
            ref=f"audit:{record.id}",
        )
    flash(request, "Audit recorded: conforming.")
    return _back(request, "quality_audits")


# ---------------------------------------------------------------- nonconformities (D-114)


@router.get("/quality/ncs", name="quality_ncs", response_class=HTMLResponse)
def quality_ncs(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    title: str = "",
    description: str = "",
    source: int = int(NcSource.MANUAL),
    ref: str = "",
    instrument: str = "",
    analyte: str = "",
    start: str = "",
    end: str = "",
):
    actor = _actor(db, user)
    ncs = store.list_ncs(db)
    instruments = sorted({s.key.instrument for s in SlimDataQc(db).series()})
    return render(
        request,
        "quality/ncs.html",
        {
            "actor": actor,
            "open": [n for n in ncs if n.state != NcState.CLOSED],
            "closed": [n for n in ncs if n.state == NcState.CLOSED],
            "instruments": instruments,
            "sources": list(NcSource),
            "form": {
                "title": title,
                "description": description,
                "source": source,
                "ref": ref,
                "instrument": instrument,
                "analyte": analyte,
                "start": start,
                "end": end or str(clock.today()),
            },
            "zone": clock.zone,
        },
    )


@router.post("/quality/ncs", name="quality_nc_raise")
def quality_nc_raise(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    title: Annotated[str, Form(max_length=200)],
    description: Annotated[str, Form(max_length=4000)],
    source: Annotated[int, Form()] = int(NcSource.MANUAL),
    ref: Annotated[str, Form(max_length=200)] = "",
    instrument: Annotated[str, Form(max_length=100)] = "",
    analyte: Annotated[str, Form(max_length=50)] = "",
    start: Annotated[str, Form()] = "",
    end: Annotated[str, Form()] = "",
):
    actor = _actor(db, user)
    window = None
    if start.strip() or end.strip():
        window = (_day(start, clock), _day(end, clock, True))
    try:
        nc = q.raise_nc(
            db,
            title.strip(),
            description.strip(),
            actor,
            NcSource(source),
            ref.strip(),
            instrument.strip(),
            analyte.strip(),
            window,
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=f"The nonconformity: {error}") from None
    db.commit()
    flash(request, f"{nc.number} raised.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc.id)), status_code=303)


@router.get("/quality/ncs/{nc_id}", name="quality_nc", response_class=HTMLResponse)
def quality_nc(request: Request, nc_id: int, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    try:
        nc = store.get_nc(db, nc_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="No such nonconformity.") from None
    action = nc.latest_action
    evidence = []
    if action is not None and nc.instrument:
        evidence = q.flags_since(q.charts(db, SlimDataQc(db)), nc.instrument, nc.analyte, action.at)
    return render(
        request,
        "quality/nc.html",
        {
            "actor": actor,
            "nc": nc,
            "touched": touched(db, nc),
            "result_ref": result_ref,
            "impact_label": impact_label,
            "decisions": list(ImpactDecision),
            "evidence": evidence,
            "zone": clock.zone,
            "today": clock.today(),
        },
    )


@router.post("/quality/ncs/{nc_id}/entries", name="quality_nc_entry")
def quality_nc_entry(
    request: Request,
    nc_id: int,
    user: RequireStaff,
    db: DB,
    kind: Annotated[int, Form()],
    note: Annotated[str, Form(max_length=4000)] = "",
    root_cause: Annotated[str, Form(max_length=2000)] = "",
    action: Annotated[str, Form(max_length=2000)] = "",
    owner: Annotated[str, Form(max_length=100)] = "",
    due: Annotated[str, Form()] = "",
    effective: Annotated[str, Form()] = "",
    impact_ref: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies defaults)
    impact_decision: Annotated[list[str], Form()] = [],  # noqa: B006
    impact_note: Annotated[list[str], Form()] = [],  # noqa: B006
):
    actor = _actor(db, user)
    try:
        nc = store.get_nc(db, nc_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="No such nonconformity.") from None
    try:
        entry_kind = EntryKind(kind)
        nc.allows(entry_kind, actor)  # a refusal first, before the form is read
        expected = {result_ref(r): r for r in touched(db, nc)} if entry_kind == EntryKind.ASSESSED else {}
        impact = []
        for ref, decision, line_note in zip(impact_ref, impact_decision, impact_note, strict=False):
            if ref not in expected:
                raise ValueError(f"{ref} is not a result this window touched")
            impact.append(
                ImpactLine(
                    ref=ref,
                    label=impact_label(expected[ref]),
                    decision=ImpactDecision(int(decision)),
                    note=line_note.strip(),
                )
            )
        if expected and set(expected) - {line.ref for line in impact}:
            raise ValueError("decide every result the window touched")
        entry = NcEntry(
            kind=entry_kind,
            note=note.strip(),
            impact=tuple(impact),
            root_cause=root_cause.strip(),
            action=action.strip(),
            owner=owner.strip(),
            due=date.fromisoformat(due) if due.strip() else None,
            effective=None if entry_kind != EntryKind.EFFECTIVENESS else effective == "1",
            by=actor,
        )
        nc = q.add_nc_entry(db, nc_id, entry)
    except NotAllowed as refused:
        raise HTTPException(status_code=403, detail=str(refused)) from None
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=f"The entry: {error}") from None
    db.commit()
    flash(request, f"{nc.number}: {nc.state.label.lower()}.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc_id)), status_code=303)
