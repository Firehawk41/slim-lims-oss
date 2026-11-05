"""Staff pages for slim-quality's batch 5c (slim-data D-134…D-137).

    /quality/risks                      the register (flagged first); POST raises one
    /quality/risks/{id}                 a risk and its entries; POST …/entries, POST …/nc
    /quality/internal-audits            the year's programme (?year=), clauses not yet planned; POST plans
                                        one (a quality manager)
    /quality/internal-audits/{id}       an audit; POST …/entries records the checklist, a new day, a
                                        cancellation or a note; POST …/nc raises an NC from a finding
    /quality/readiness                  one row per ISO 17025 clause, red / amber / green
    /quality/management-review          the pack for a period (?start=…&end=…, a GET: nothing is
                                        written) and the signed reviews; POST signs one (a manager)
    /quality/management-review/{id}     a signed review, as it was signed
    /quality/validation                 the §7.11 pack and its signature; /quality/validation.json
                                        downloads it; POST signs it (a quality manager)

The quality pages need a quality capability (403); refusals by the facts are 409, a bad form 422.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from slim_quality.domain.clauses import CLAUSES, clause_label, clause_order
from slim_quality.domain.competence import Person
from slim_quality.domain.internal_audits import (
    AuditEntry,
    AuditEntryKind,
    ChecklistLine,
    InternalAudit,
    LineOutcome,
)
from slim_quality.domain.management_review import INPUTS, WRITTEN, OutputKind, ReviewOutput
from slim_quality.domain.risks import Risk, RiskEntry, RiskEntryKind, RiskKind, RiskSource
from slim_quality.persistence import store
from slim_quality.service import internal_audits, risks
from slim_quality.service import quality as q
from slim_quality.service import readiness as rd
from sqlalchemy.orm import Session

from slim_lims.api import get_reference
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.quality_insights import staff_people
from slim_lims.quality_records_routes import _actor, _date, _write
from slim_lims.quality_system import (
    installed_versions,
    involved_in_clause,
    readiness_facts,
    readiness_inputs,
    validation_pack,
)
from slim_lims.reference import ReferenceData
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Clock = Annotated[LabClock, Depends(lab_clock)]
Reference = Annotated[ReferenceData, Depends(get_reference)]
Text = Annotated[str, Form(max_length=2000)]
Short = Annotated[str, Form(max_length=200)]
CHECKLIST_ROWS = 8  # blank lines the audit form offers (no script: CSP)
OUTPUT_ROWS = 6  # the management review's output lines

# The page each readiness line points at.
PAGES = {
    "internal_audits": "quality_internal_audits",
    "competence": "quality_competence",
    "equipment": "quality_equipment",
    "materials": "quality_materials",
    "uncertainty": "quality_uncertainty",
    "pt": "quality_pt",
    "trends": "quality_trends",
    "complaints": "quality_complaints",
    "ncs": "quality_ncs",
    "validation": "quality_validation",
    "risks": "quality_risks",
    "management_review": "quality_management_review",
}


def _score(text: str, what: str) -> int | None:
    if not text.strip():
        return None
    if text.strip() not in {"1", "2", "3", "4", "5"}:
        raise HTTPException(status_code=422, detail=f"{what}: 1 to 5")
    return int(text)


def _choice(value: int, enum, what: str):
    if value not in {int(v) for v in enum}:
        raise HTTPException(status_code=422, detail=f"{what}: choose one")
    return enum(value)


# ---------------------------------------------------------------- risks (D-134)


@router.get("/quality/risks", name="quality_risks", response_class=HTMLResponse)
def quality_risks(request: Request, user: RequireStaff, db: DB, clock: Clock, closed: str = ""):
    actor = _actor(db, user)
    today = clock.today()
    return render(
        request,
        "quality/risks.html",
        {
            "actor": actor,
            "rows": risks.register(db, today, include_closed=closed == "1"),
            "policy": q.current_profile(db).risk,
            "sources": list(RiskSource),
            "kinds": list(RiskKind),
            "today": today,
            "review_by": today + timedelta(days=180),
            "show_closed": closed == "1",
        },
    )


@router.post("/quality/risks", name="quality_risk_raise")
def quality_risk_raise(
    request: Request,
    user: RequireStaff,
    db: DB,
    title: Short,
    source: Annotated[int, Form()],
    owner: Annotated[str, Form(max_length=100)],
    review_by: Annotated[str, Form()],
    kind: Annotated[int, Form()] = int(RiskKind.RISK),
    description: Text = "",
    likelihood: Annotated[str, Form(max_length=2)] = "",
    severity: Annotated[str, Form(max_length=2)] = "",
):
    actor = _actor(db, user)
    risk_kind = _choice(kind, RiskKind, "Kind")
    scored = risk_kind == RiskKind.RISK

    def raise_it():
        return risks.raise_risk(
            db,
            Risk(
                kind=risk_kind,
                title=title,
                description=description,
                source=_choice(source, RiskSource, "Source"),
                owner=owner,
                likelihood=_score(likelihood, "Likelihood") if scored else None,
                severity=_score(severity, "Severity") if scored else None,
                review_by=_date(review_by, "Review by"),
                raised_by=actor,
            ),
        )

    found = _write(db, raise_it)
    flash(request, f"{found} registered.")
    return RedirectResponse(str(request.url_for("quality_risk", risk_id=found.id)), status_code=303)


@router.get("/quality/risks/{risk_id}", name="quality_risk", response_class=HTMLResponse)
def quality_risk(request: Request, risk_id: int, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    found = _write(db, lambda: store.get_risk(db, risk_id))
    policy = q.current_profile(db).risk
    return render(
        request,
        "quality/risk.html",
        {
            "actor": actor,
            "r": found,
            "band": found.band(policy),
            "flags": found.flags(clock.today(), policy),
            "kinds": RiskEntryKind,
            "today": clock.today(),
            "zone": clock.zone,
        },
    )


@router.post("/quality/risks/{risk_id}/entries", name="quality_risk_entry")
def quality_risk_entry(
    request: Request,
    risk_id: int,
    user: RequireStaff,
    db: DB,
    kind: Annotated[int, Form()],
    note: Text = "",
    likelihood: Annotated[str, Form(max_length=2)] = "",
    severity: Annotated[str, Form(max_length=2)] = "",
    action: Text = "",
    owner: Annotated[str, Form(max_length=100)] = "",
    due: Annotated[str, Form()] = "",
    action_no: Annotated[str, Form(max_length=5)] = "",
    review_by: Annotated[str, Form()] = "",
):
    actor = _actor(db, user)
    entry_kind = _choice(kind, RiskEntryKind, "Kind")

    def add():
        entry = RiskEntry(
            kind=entry_kind,
            note=note,
            likelihood=_score(likelihood, "Likelihood"),
            severity=_score(severity, "Severity"),
            action=action,
            owner=owner,
            due=_date(due, "Due", required=False),
            action_no=int(action_no) if action_no.strip().isdigit() else None,
            review_by=_date(review_by, "Next review", required=False),
            by=actor,
        )
        return risks.add_entry(db, risk_id, entry)

    found = _write(db, add)
    flash(request, f"{found.number}: {entry_kind.label.lower()} recorded.")
    return RedirectResponse(str(request.url_for("quality_risk", risk_id=risk_id)), status_code=303)


@router.post("/quality/risks/{risk_id}/nc", name="quality_risk_nc")
def quality_risk_nc(request: Request, risk_id: int, user: RequireStaff, db: DB):
    actor = _actor(db, user)
    nc = _write(db, lambda: risks.risk_nc(db, risk_id, actor))
    flash(request, f"{nc.number} raised.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc.id)), status_code=303)


# ---------------------------------------------------------------- internal audits (D-135)


def _clauses() -> list[tuple[str, str]]:
    return [(c, clause_label(c)) for c in sorted(CLAUSES, key=clause_order)]


@router.get("/quality/internal-audits", name="quality_internal_audits", response_class=HTMLResponse)
def quality_internal_audits(request: Request, user: RequireStaff, db: DB, clock: Clock, year: int = 0):
    actor = _actor(db, user)
    today = clock.today()
    year = year or today.year
    plans = internal_audits.programme(db, year)
    planned = {a.clause for a in plans if a.state.name != "CANCELLED"}
    return render(
        request,
        "quality/internal_audits.html",
        {
            "actor": actor,
            "year": year,
            "plans": plans,
            "unplanned": [(c, label) for c, label in _clauses() if c not in planned],
            "clauses": _clauses(),
            "people": sorted(staff_people(db).values(), key=lambda p: p.display),
            "today": today,
            "last_audited": internal_audits.last_audited(db),
        },
    )


@router.post("/quality/internal-audits", name="quality_internal_audit_plan")
def quality_internal_audit_plan(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    reference: Reference,
    clause: Annotated[str, Form(max_length=20)],
    planned_for: Annotated[str, Form()],
    auditor_id: Annotated[str, Form(max_length=100)],
    area: Short = "",
):
    actor = _actor(db, user)
    auditor: Person | None = staff_people(db).get(auditor_id)
    if auditor is None:
        raise HTTPException(status_code=422, detail="Auditor: choose a member of staff")

    def plan():
        audit = InternalAudit(
            clause=clause,
            area=area,
            planned_for=_date(planned_for, "Planned for"),
            auditor=auditor,
            planned_by=actor,
        )
        involved = involved_in_clause(db, reference, clock, audit.clause, audit.planned_for)
        return internal_audits.plan(db, audit, involved)

    found = _write(db, plan)
    flash(request, f"{found} planned.")
    return RedirectResponse(
        str(request.url_for("quality_internal_audit", audit_id=found.id)), status_code=303
    )


@router.get("/quality/internal-audits/{audit_id}", name="quality_internal_audit", response_class=HTMLResponse)
def quality_internal_audit(request: Request, audit_id: int, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    audit = _write(db, lambda: store.get_internal_audit(db, audit_id))
    without = {
        n for a, n in internal_audits.findings_without_nc(db).get(audit.clause, []) if a.id == audit.id
    }
    return render(
        request,
        "quality/internal_audit.html",
        {
            "actor": actor,
            "a": audit,
            "without_nc": without,
            "outcomes": list(LineOutcome),
            "kinds": AuditEntryKind,
            "rows": range(CHECKLIST_ROWS),
            "today": clock.today(),
            "zone": clock.zone,
        },
    )


@router.post("/quality/internal-audits/{audit_id}/entries", name="quality_internal_audit_entry")
def quality_internal_audit_entry(
    request: Request,
    audit_id: int,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    reference: Reference,
    kind: Annotated[int, Form()],
    note: Annotated[str, Form(max_length=4000)] = "",
    on: Annotated[str, Form()] = "",
    line_clause: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies the default)
    line_checked: Annotated[list[str], Form()] = [],  # noqa: B006
    line_outcome: Annotated[list[str], Form()] = [],  # noqa: B006
    line_evidence: Annotated[list[str], Form()] = [],  # noqa: B006
):
    actor = _actor(db, user)
    entry_kind = _choice(kind, AuditEntryKind, "Kind")
    if len(line_clause) > CHECKLIST_ROWS * 4:
        raise HTTPException(status_code=422, detail="Checklist: too many lines")

    def add():
        lines = []
        for n, checked in enumerate(line_checked):
            if not checked.strip():
                continue
            outcome = line_outcome[n] if n < len(line_outcome) else ""
            lines.append(
                ChecklistLine(
                    clause=(line_clause[n] if n < len(line_clause) else "").strip(),
                    checked=checked.strip()[:1000],
                    outcome=_choice(int(outcome) if outcome.isdigit() else 0, LineOutcome, f"Line {n + 1}"),
                    evidence=(line_evidence[n] if n < len(line_evidence) else "")[:2000],
                )
            )
        entry = AuditEntry(
            kind=entry_kind,
            on=_date(on, "On", required=entry_kind in (AuditEntryKind.RESCHEDULED, AuditEntryKind.CONDUCTED)),
            lines=tuple(lines),
            note=note,
            by=actor,
        )
        audit = store.get_internal_audit(db, audit_id)
        involved = involved_in_clause(db, reference, clock, audit.clause, entry.on or audit.due)
        return internal_audits.add_entry(db, audit_id, entry, involved)

    found = _write(db, add)
    flash(request, f"{found.number}: {entry_kind.label.lower()} recorded.")
    return RedirectResponse(
        str(request.url_for("quality_internal_audit", audit_id=audit_id)), status_code=303
    )


@router.post("/quality/internal-audits/{audit_id}/nc", name="quality_internal_audit_nc")
def quality_internal_audit_nc(
    request: Request, audit_id: int, user: RequireStaff, db: DB, line_no: Annotated[int, Form()]
):
    actor = _actor(db, user)
    nc = _write(db, lambda: internal_audits.finding_nc(db, audit_id, line_no, actor))
    flash(request, f"{nc.number} raised.")
    return RedirectResponse(str(request.url_for("quality_nc", nc_id=nc.id)), status_code=303)


# ---------------------------------------------------------------- readiness, management review (D-136)


@router.get("/quality/readiness", name="quality_readiness", response_class=HTMLResponse)
def quality_readiness(request: Request, user: RequireStaff, db: DB, clock: Clock, reference: Reference):
    actor = _actor(db, user)
    rows = rd.view(db, readiness_facts(db, reference, clock))
    return render(
        request,
        "quality/readiness.html",
        {
            "actor": actor,
            "rows": rows,
            "pages": PAGES,
            "policy": q.current_profile(db).readiness,
            "today": clock.today(),
        },
    )


def _period(clock: LabClock, start: str, end: str):
    today = clock.today()
    first = _date(start, "From", required=False) or today - timedelta(days=365)
    last = _date(end, "To", required=False) or today
    if first > last:
        raise HTTPException(status_code=422, detail="The period ends before it starts")
    return first, last


def _pack(db: Session, reference: ReferenceData, clock: LabClock, first, last):
    inputs = readiness_inputs(db, reference, clock)
    facts = rd.gather(db, clock.today(), versions=installed_versions(), **inputs)
    return rd.pack(db, first, last, facts, **inputs)


@router.get("/quality/management-review", name="quality_management_review", response_class=HTMLResponse)
def quality_management_review(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    reference: Reference,
    start: str = "",
    end: str = "",
):
    actor = _actor(db, user)
    first, last = _period(clock, start, end)
    return render(
        request,
        "quality/management_review.html",
        {
            "actor": actor,
            "pack": _pack(db, reference, clock, first, last),
            "reviews": store.management_reviews(db),
            "inputs": INPUTS,
            "written": WRITTEN,
            "outputs": list(OutputKind),
            "output_rows": range(OUTPUT_ROWS),
            "period": (first, last),
            "pages": PAGES,
            "zone": clock.zone,
        },
    )


@router.post("/quality/management-review", name="quality_management_review_sign")
def quality_management_review_sign(
    request: Request,
    user: RequireStaff,
    db: DB,
    clock: Clock,
    reference: Reference,
    start: Annotated[str, Form()],
    end: Annotated[str, Form()],
    attendees: Annotated[str, Form(max_length=1000)] = "",
    input_a: Text = "",
    input_b: Text = "",
    input_c: Text = "",
    input_g: Text = "",
    input_i: Text = "",
    input_k: Text = "",
    input_l: Text = "",
    output_kind: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies the default)
    output_text: Annotated[list[str], Form()] = [],  # noqa: B006
    output_owner: Annotated[list[str], Form()] = [],  # noqa: B006
    output_due: Annotated[list[str], Form()] = [],  # noqa: B006
):
    actor = _actor(db, user)
    first, last = _period(clock, start, end)
    written = {
        "a": input_a,
        "b": input_b,
        "c": input_c,
        "g": input_g,
        "i": input_i,
        "k": input_k,
        "l": input_l,
    }
    if len(output_text) > OUTPUT_ROWS * 4:
        raise HTTPException(status_code=422, detail="Outputs: too many lines")

    def sign():
        outputs = []
        for n, text in enumerate(output_text):
            if not text.strip():
                continue
            kind = output_kind[n] if n < len(output_kind) else ""
            outputs.append(
                ReviewOutput(
                    kind=_choice(int(kind) if kind.isdigit() else 0, OutputKind, f"Output {n + 1}"),
                    text=text.strip()[:2000],
                    owner=(output_owner[n] if n < len(output_owner) else "").strip()[:100],
                    due=_date(
                        output_due[n] if n < len(output_due) else "", f"Output {n + 1} due", required=False
                    ),
                )
            )
        pack = _pack(db, reference, clock, first, last)  # worked out again at signing, never from the form
        return rd.sign_review(db, pack, written, outputs, attendees, actor)

    found = _write(db, sign)
    flash(request, f"{found.number} signed.")
    return RedirectResponse(
        str(request.url_for("quality_management_review_signed", review_id=found.id)), status_code=303
    )


@router.get(
    "/quality/management-review/{review_id}",
    name="quality_management_review_signed",
    response_class=HTMLResponse,
)
def quality_management_review_signed(
    request: Request, review_id: int, user: RequireStaff, db: DB, clock: Clock
):
    actor = _actor(db, user)
    found = store.management_reviews(db, ids=(review_id,))
    if not found:
        raise HTTPException(status_code=404, detail=f"no management review {review_id}")
    return render(
        request,
        "quality/management_review_signed.html",
        {"actor": actor, "r": found[0], "pages": PAGES, "zone": clock.zone},
    )


# ---------------------------------------------------------------- validation (D-137)


@router.get("/quality/validation", name="quality_validation", response_class=HTMLResponse)
def quality_validation(request: Request, user: RequireStaff, db: DB, clock: Clock):
    actor = _actor(db, user)
    pack = validation_pack(db, clock)
    rag, signature = rd.validation(db, pack["versions"])
    return render(
        request,
        "quality/validation.html",
        {
            "actor": actor,
            "pack": pack,
            "rag": rag,
            "signature": signature,
            "signatures": store.validation_signatures(db),
            "zone": clock.zone,
        },
    )


@router.get("/quality/validation.json", name="quality_validation_json")
def quality_validation_json(user: RequireStaff, db: DB, clock: Clock):
    _actor(db, user)
    pack = validation_pack(db, clock)
    return JSONResponse(pack, headers={"Content-Disposition": 'attachment; filename="validation-pack.json"'})


@router.post("/quality/validation", name="quality_validation_sign")
def quality_validation_sign(request: Request, user: RequireStaff, db: DB, clock: Clock, note: Text):
    actor = _actor(db, user)
    pack = validation_pack(db, clock)  # worked out again at signing, never taken from the form
    _write(db, lambda: rd.sign_validation(db, pack["versions"], pack, note, actor))
    flash(request, "The validation pack is signed for the installed modules.")
    return RedirectResponse(str(request.url_for("quality_validation")), status_code=303)
