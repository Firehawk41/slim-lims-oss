"""Staff pages and JSON API for the prep book (slim-prep, P6). Staff only.

    /prep                           the prep book: batches, standards, stocks; new batch, make a standard
    /prep/new-batch                 ?method&analysis&code: the picker (open samples that ordered the
                                    analysis, P-23); &sample=…&sample=…: the grid, one row per cup
    POST /prep/batches              records the grid (slim-prep checks it against the rules and the book)
    /prep/batches/{code}            the batch with its figures (net, dilution, spike level), its versions,
                                    "Send to results" and the run list
    POST /prep/batches/{code}/send  registers it in slim-data, or sends its correction (P-18, P-20)
    POST /prep/batches/{code}/move-rules  rechecks it under the current rules, a recorded step (P-24)
    POST /prep/standards            makes a standard from a recipe step and its source (P-14, P-15)
    GET  /prep/run-list.csv         ?code=…&code=…: the planned run list for those batches (P-21)
    GET  /api/prep/batches/{code}   POST /api/prep/batches   POST /api/prep/batches/{code}/send
    POST /api/prep/batches/{code}/move-rules
    GET  /api/prep/standards        POST /api/prep/standards  GET /api/prep/run-list?code=…

Errors: the rules or the book refuse -> 422 with every problem; someone else saved first -> 409;
unknown batch or standard -> 404.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, ValidationError
from slim_prep.domain.prep import (
    Cup,
    CupKind,
    PrepBatch,
    PrepSample,
    SpikeLine,
    StepRecord,
    Weighing,
    figures,
)
from slim_prep.domain.problems import PrepRefused, Problem
from slim_prep.domain.run_list import run_list_csv
from slim_prep.domain.standards import StandardRef
from slim_prep.persistence import books
from slim_prep.persistence._rows import ConcurrentChange, NotFound
from slim_prep.persistence.rules import get_rules
from slim_prep.service.books import move_to_rules, next_batch_code, record_batch, record_standard
from slim_prep.service.handoff import sent_version
from slim_prep.service.run_list import run_list_for
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.api import get_reference
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.models import ROLE_STAFF, User
from slim_lims.prep import PREP_ANALYSES, TrSamples, prep_actor_for, refuse_blocked, rules, send
from slim_lims.reference import ReferenceData
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
api_router = APIRouter(prefix="/api/prep", tags=["prep"], dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]
Clock = Annotated[LabClock, Depends(lab_clock)]


async def _form(request: Request) -> dict[str, str]:
    """The whole form: the grid's fields are named per row and cup (Starlette caches the parse)."""
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


Form_ = Annotated[dict[str, str], Depends(_form)]


class Refused(Exception):
    def __init__(self, status: int, problems: list[str]) -> None:
        self.status, self.problems = status, problems


def _guard(db: Session, action):
    """Runs a write; commits, or rolls back and raises Refused with what to tell the person."""
    try:
        body = action()
    except PrepRefused as error:
        db.rollback()
        raise Refused(422, [str(p) for p in error.problems]) from None
    except ValidationError as error:
        db.rollback()
        raise Refused(422, [e["msg"].removeprefix("Value error, ") for e in error.errors()]) from None
    except (ValueError, LookupError) as error:
        db.rollback()
        raise Refused(404 if isinstance(error, NotFound) else 422, [str(error)]) from None
    except ConcurrentChange as error:
        db.rollback()
        raise Refused(409, [str(error)]) from None
    db.commit()
    return body


# ---------------------------------------------------------------- views of the books


def _figures_json(cups) -> list[dict[str, Any]]:
    return [
        {
            "sample": c.sample,
            "cup": c.cup,
            "kind": c.kind.name.lower(),
            "net": str(c.net),
            "solution": str(c.solution),
            "dilution": str(c.dilution),
            "added": [{"element": e, "amount": str(a)} for e, a in c.added],
        }
        for c in cups
    ]


def _batch_json(db: Session, code: str) -> dict[str, Any]:
    recorded = books.get_batch(db, code)
    batch_rules = get_rules(db, recorded.rules_id).rules
    return {
        "code": code,
        "version": recorded.version,
        "rules_id": recorded.rules_id,
        "reason": recorded.reason,
        "by": recorded.created_by.display,
        "at": recorded.created_at.isoformat(),
        "sent_version": sent_version(db, code),
        "current_rules_id": rules(db).id,
        "batch": recorded.item.model_dump(mode="json"),
        "figures": _figures_json(figures(recorded.item, batch_rules)),
    }


def _standard_json(r) -> dict[str, Any]:
    s = r.item
    source = s.source
    return {
        "name": s.name,
        "prepared": s.prepared.isoformat(),
        "recipe": s.recipe,
        "concentration": str(s.concentration),
        "elements": list(s.elements),
        "expires": s.expires.isoformat(),
        "source": str(source),
        "analyst": r.created_by.display,
        "version": r.version,
    }


def _home(db: Session, reference: ReferenceData, today: date, form=None, problems=None) -> dict[str, Any]:
    current = rules(db)
    return {
        "rules": current.rules,
        "next_code": next_batch_code(db, current.rules) or "",
        "batches": [
            {
                "code": r.item.code,
                "method": r.item.method,
                "analysis": r.item.analysis,
                "version": r.version,
                "sent": sent_version(db, r.item.code),
                "at": r.created_at,
            }
            for r in books.list_batches(db)
        ],  # fmt: skip
        "standards": [_standard_json(r) for r in books.list_standards(db)],
        "stocks": [r.item for r in books.list_stocks(db)],
        "analyses": [(a.code, a.name) for a in reference.catalog_analyses() if a.code in PREP_ANALYSES],
        "today": today.isoformat(),
        "form": form or {},
        "problems": problems or [],
    }


@router.get("/prep", name="prep_home")
def prep_home(request: Request, user: RequireStaff, db: DB, reference: Reference, clock: Clock):
    return render(request, "prep/index.html", _home(db, reference, clock.today()))


@router.get("/prep/batches/{code}", name="prep_batch")
def prep_batch(request: Request, code: str, user: RequireStaff, db: DB):
    try:
        data = _batch_json(db, code)
    except NotFound:
        return render(request, "error.html", {"message": f"No prep batch {code}."}, 404)
    history = books.history_batch(db, code)
    return render(request, "prep/batch.html", {"b": data, "history": history})


@router.get("/prep/run-list.csv", name="prep_run_list")
def prep_run_list(user: RequireStaff, db: DB, reference: Reference, code: Annotated[list[str], Query()]):
    try:
        entries = _run_list(db, reference, code)
    except (PrepRefused, NotFound) as error:
        return JSONResponse({"detail": str(error)}, status_code=404 if isinstance(error, NotFound) else 422)
    headers = {"Content-Disposition": f'attachment; filename="run-list-{"-".join(code)}.csv"'}
    return Response(run_list_csv(entries), media_type="text/csv", headers=headers)


def _run_list(db: Session, reference: ReferenceData, codes: list[str]):
    """Each sample's matrix is its TR's matched chemical (P-23); the codes are recorded batches, so
    they are safe in the download's file name (P-26)."""
    return run_list_for(db, codes, TrSamples(db, reference), rules(db).id)


# ---------------------------------------------------------------- a new batch: the picker, the grid


def _staff(db: Session) -> list[User]:
    """Who can be named on a step (P-27): active staff, by initials."""
    return list(
        db.scalars(
            select(User)
            .where(User.role == ROLE_STAFF, User.is_active.is_(True), User.initials.is_not(None))
            .order_by(User.initials)
        )
    )


def _grid(
    db: Session,
    clock: LabClock,
    method: str,
    analysis: str,
    code: str,
    samples: list[str],
    used: frozenset = frozenset(),
    lines: dict[str, int] | None = None,
    rules_id: int | None = None,
) -> dict[str, Any]:
    """The grid's context. Standards still in date are offered, and those the batch already used (a
    correction keeps them, whether or not they have expired since). A spike cup shows its recorded
    spike lines and one blank one (review B3): `lines` gives how many a cup has. A correction is shown
    under the batch's own rules (`rules_id`, P-24)."""
    current = get_rules(db, rules_id).rules if rules_id else rules(db).rules
    found = current.method(method)
    if found is None:
        raise LookupError(f"no prep method {method!r}")
    today = clock.today()
    standards = [r.item for r in books.list_standards(db) if r.item.expires >= today or r.item.ref in used]
    return {
        "method": found,
        "analysis": analysis,
        "code": code,
        "samples": samples,
        "cups": list(range(1, found.sample_cups + 1)),
        "spike_cups": list(range(found.sample_cups + 1, found.sample_cups + found.spike_cups + 1)),
        "lines": lines or {},
        "rules": current,
        "standards": standards,
        "staff": (staff := _staff(db)),
        "staff_ids": {str(u.id) for u in staff},
        "now": f"{clock.now():%Y-%m-%dT%H:%M}",
    }


@router.get("/prep/new-batch", name="prep_new_batch")
def prep_new_batch(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
    method: str = "",
    analysis: str = "",
    code: str = "",
    sample: Annotated[list[str] | None, Query()] = None,
):
    """Without samples: the picker, the open samples that ordered the analysis (P-23). With them: the grid."""
    form = {"method": method, "analysis": analysis, "code": code}
    try:
        if not code.strip():
            raise LookupError("give the batch a code")
        if not sample:
            return render(request, "prep/pick_samples.html", _picker(db, reference, form))
        grid = _grid(db, clock, method, analysis, code.strip(), list(dict.fromkeys(sample)))
    except LookupError as error:
        return render(
            request, "prep/index.html", _home(db, reference, clock.today(), form, [str(error)]), 422
        )
    return render(request, "prep/new_batch.html", {**grid, "values": {}, "problems": []})


def _picker(db: Session, reference: ReferenceData, form: dict[str, str]) -> dict[str, Any]:
    if form["analysis"] not in PREP_ANALYSES:
        raise LookupError(f"analysis {form['analysis']!r} can't take prep batches here")
    prepped: dict[str, list[str]] = {}
    for r in books.list_batches(db):
        if r.item.analysis == form["analysis"]:
            for s in r.item.samples:
                prepped.setdefault(s.sample, []).append(r.item.code)
    samples = TrSamples(db, reference).open_samples(form["analysis"])
    return {"form": form, "samples": samples, "prepped": prepped}


def _decimal(values: dict[str, str], key: str, problems: list[str], label: str) -> Decimal | None:
    text = values.get(key, "").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite():
        problems.append(f"{label}: not a number ({text!r})")
        return None
    return value


def _when(
    values: dict[str, str], key: str, clock: LabClock, problems: list[str], label: str
) -> datetime | None:
    """A datetime-local field, taken as the lab's time (P-25)."""
    text = values.get(key, "").strip()
    try:
        return clock.local(datetime.fromisoformat(text))
    except ValueError:
        problems.append(f"{label}: give a date and time")
        return None


def _local(at: datetime, clock: LabClock) -> str:
    return f"{clock.local(at):%Y-%m-%dT%H:%M}"


def _sample_ids(values: dict[str, str]) -> list[str]:
    """The grid's sample_0, sample_1, … in order; any other `sample_*` field is ignored (review B10)."""
    out = []
    while f"sample_{len(out)}" in values:
        out.append(values[f"sample_{len(out)}"])
    return out


def _spike_keys(values: dict[str, str], key: str) -> list[str]:
    """A spike cup's line fields, `<cup>_s0`, `<cup>_s1`, … while they last."""
    out = []
    while f"{key}_s{len(out)}_standard" in values:
        out.append(f"{key}_s{len(out)}")
    return out


def _values(batch: PrepBatch, clock: LabClock) -> tuple[dict[str, str], dict[str, int]]:
    """A recorded batch as the grid's fields: what a correction starts from; and each cup's spike lines."""
    values = {"balance": batch.balance, "note": batch.note or "", "prep_at": _local(batch.prep.at, clock)}
    if batch.pour_out:
        values["pour_out"] = batch.pour_out.analyst_id or batch.pour_out.analyst
        values["pour_out_at"] = _local(batch.pour_out.at, clock)
    lines = {}
    for i, sample in enumerate(batch.samples):
        values[f"sample_{i}"] = sample.sample
        for cup in sample.cups:
            k = f"c_{i}_{cup.number}"
            w = cup.weighing
            for name in ("tare", "cap", "gross", "final"):
                value = getattr(w, name)
                values[f"{k}_{name}"] = "" if value is None else str(value)
            values[f"{k}_balance"] = cup.balance or ""
            values[f"{k}_note"] = cup.note or ""
            lines[k] = len(cup.spikes)
            for j, line in enumerate(cup.spikes):  # every line (review B3)
                values[f"{k}_s{j}_standard"] = f"{line.standard.name}|{line.standard.prepared.isoformat()}"
                values[f"{k}_s{j}_volume"] = str(line.volume_ul)
                values[f"{k}_s{j}_pipette"] = line.pipette
    return values, lines


def _used(batch: PrepBatch) -> frozenset:
    return frozenset(line.standard for s in batch.samples for c in s.cups for line in c.spikes)


@router.get("/prep/batches/{code}/edit", name="prep_edit_batch")
def prep_edit_batch(request: Request, code: str, user: RequireStaff, db: DB, clock: Clock):
    try:
        recorded = books.get_batch(db, code)
    except NotFound:
        return render(request, "error.html", {"message": f"No prep batch {code}."}, 404)
    b = recorded.item
    values, lines = _values(b, clock)
    samples = [s.sample for s in b.samples]
    grid = _grid(db, clock, b.method, b.analysis, code, samples, _used(b), lines, recorded.rules_id)
    context = {**grid, "values": values, "problems": [], "version": recorded.version}
    return render(request, "prep/new_batch.html", context)


def _batch_from_grid(values: dict[str, str], db: Session, user: User, clock: LabClock) -> PrepBatch:
    """The grid's fields -> a PrepBatch, or PrepRefused with every problem. Times are entered (P-25); who
    poured out is chosen from the staff list (P-27). A correction keeps who prepped it: its own author
    and reason go on the version."""
    recorded = books.get_batch(db, values.get("code", "")) if _expected(values) else None
    before = recorded.item if recorded else None
    # a correction keeps the batch's method and rules (P-24); a new batch's come from the form and now
    if recorded is not None:
        method = get_rules(db, recorded.rules_id).rules.method(recorded.item.method)
    else:
        method = rules(db).rules.method(values.get("method", ""))
    if method is None:
        raise LookupError(f"no prep method {values.get('method')!r}")
    by_ref = {f"{s.name}|{s.prepared.isoformat()}": s for s in (r.item for r in books.list_standards(db))}
    problems: list[str] = []
    samples = []
    for i, sample_id in enumerate(_sample_ids(values)):
        cups = []
        for n in range(1, method.sample_cups + method.spike_cups + 1):
            key = f"c_{i}_{n}"
            label = f"{sample_id} cup {n}"
            w = {
                f: _decimal(values, f"{key}_{f}", problems, f"{label} {f}")
                for f in ("tare", "cap", "gross", "final")
            }
            if w["gross"] is None or w["final"] is None:
                problems.append(f"{label}: gross and final are needed")
                continue
            kind = CupKind.SAMPLE if n <= method.sample_cups else CupKind.SPIKE
            spikes = _spike_lines(values, key, label, by_ref, problems) if kind == CupKind.SPIKE else ()
            if spikes is None:
                continue
            cups.append(
                Cup(
                    number=n,
                    kind=kind,
                    weighing=Weighing(**w),
                    spikes=spikes,
                    balance=values.get(f"{key}_balance") or None,
                    note=values.get(f"{key}_note") or None,
                )
            )
        if cups:  # else its problems are listed
            samples.append(PrepSample(sample=sample_id, cups=tuple(cups)))
    prep_at = _when(values, "prep_at", clock, problems, "prepped at")
    poured = None
    if method.pour_out_step:
        who = next((u for u in _staff(db) if str(u.id) == values.get("pour_out")), None)
        if before is not None and before.pour_out and values.get("pour_out") == before.pour_out.analyst:
            who = before.pour_out  # recorded before P-27: initials only, kept as they were
        at = _when(values, "pour_out_at", clock, problems, "poured out at")
        if who is None:
            problems.append("poured out by: choose who from the staff list")
        elif at is not None:
            poured = (
                who.model_copy(update={"at": at})
                if isinstance(who, StepRecord)
                else StepRecord(analyst=who.initials, analyst_id=str(who.id), at=at)
            )
    if problems:
        raise PrepRefused([Problem(where="", message=p) for p in problems])
    by = (
        before.prep
        if before
        else StepRecord(analyst=user.initials or user.email, analyst_id=str(user.id), at=prep_at)
    )
    return PrepBatch(
        code=values.get("code", ""),
        method=method.name,
        analysis=before.analysis if before else values.get("analysis", ""),
        balance=values.get("balance", ""),
        pour_out=poured,
        prep=by.model_copy(update={"at": prep_at}),
        samples=tuple(samples),
        note=values.get("note") or None,
    )


def _spike_lines(values, key: str, label: str, by_ref, problems: list[str]) -> tuple[SpikeLine, ...] | None:
    """A spike cup's lines; a blank line (no standard, no volume) is skipped. None after a problem."""
    lines, ok = [], True
    for j, line_key in enumerate(_spike_keys(values, key), start=1):
        chosen = values.get(f"{line_key}_standard", "")
        volume = _decimal(values, f"{line_key}_volume", problems, f"{label} spike {j} volume")
        if not chosen and volume is None:
            continue
        standard = by_ref.get(chosen)
        if standard is None or volume is None:
            problems.append(f"{label} spike {j}: a spike needs a standard and a volume")
            ok = False
            continue
        lines.append(SpikeLine.of(standard, volume, values.get(f"{line_key}_pipette", "")))
    if ok and not lines:
        problems.append(f"{label}: a spike cup needs at least one spike")
        ok = False
    return tuple(lines) if ok else None


@router.post("/prep/batches", name="prep_save_batch")
def prep_save_batch(
    request: Request, user: RequireStaff, db: DB, values: Form_, clock: Clock, reference: Reference
):
    expected = _expected(values)

    def save():
        batch = _batch_from_grid(values, db, user, clock)
        refuse_blocked(db, batch)
        reason = values.get("reason") or None
        rules_id = None if expected else rules(db).id  # a correction keeps its own (P-24)
        samples = TrSamples(db, reference)
        return record_batch(db, batch, rules_id, prep_actor_for(user), samples, clock.now(), expected, reason)

    try:
        recorded, _ = _guard(db, save)
    except Refused as refused:
        code = values.get("code", "")
        try:
            was = books.get_batch(db, code) if expected else None
        except NotFound:
            was = None
        used = _used(was.item) if was else frozenset()
        lines = {k[: -len("_s0_standard")]: 0 for k in values if k.endswith("_s0_standard")}
        for k in lines:
            lines[k] = len(_spike_keys(values, k)) - 1  # the blank line comes back on its own
        try:
            method, analysis = (
                (was.item.method, was.item.analysis)
                if was
                else (values.get("method", ""), values.get("analysis", ""))
            )
            grid = _grid(
                db,
                clock,
                method,
                analysis,
                code,
                _sample_ids(values),
                used,
                lines,
                was.rules_id if was else None,
            )
        except LookupError as error:
            return render(request, "error.html", {"message": str(error)}, 422)
        context = {**grid, "values": values, "problems": refused.problems}
        if expected:
            context["version"] = expected
        return render(request, "prep/new_batch.html", context, refused.status)
    what = f"corrected (version {recorded.version})" if expected else "recorded"
    flash(request, f"Prep batch {recorded.item.code} {what}.", "success")
    return RedirectResponse(request.url_for("prep_batch", code=recorded.item.code), status_code=303)


def _expected(values: dict[str, str]) -> int:
    """The version a correction was made from; 0 for a new batch."""
    text = values.get("expected_version", "0").strip()
    return int(text) if text.isascii() and text.isdigit() else 0


@router.post("/prep/batches/{code}/send", name="prep_send")
def prep_send(request: Request, code: str, user: RequireStaff, db: DB, reference: Reference):
    try:
        body = _guard(db, lambda: send(db, code, user, reference, request.app.state.specs))
    except Refused as refused:
        flash(request, "Not sent: " + "; ".join(refused.problems), "error")
    else:
        what = (
            "sent to results" if body["changed"] is None else f"correction sent ({body['changed']} changed)"
        )
        flash(request, f"Prep batch {code} {what}.", "success")
    return RedirectResponse(request.url_for("prep_batch", code=code), status_code=303)


def _move(
    db: Session, code: str, user: User, reference: ReferenceData, clock: LabClock, version: int, reason: str
):
    """The batch, rechecked and recorded under the lab's current rules (P-24; review B9)."""
    if not reason.strip():
        raise ValueError("moving a batch to new rules needs a reason")
    samples = TrSamples(db, reference)
    recorded, _ = move_to_rules(
        db, code, rules(db).id, prep_actor_for(user), samples, clock.now(), version, reason.strip()
    )
    return recorded.version


@router.post("/prep/batches/{code}/move-rules", name="prep_move_rules")
def prep_move_rules(
    request: Request,
    code: str,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
    expected_version: Annotated[int, Form(ge=1)],
    reason: Annotated[str, Form(max_length=1000)] = "",
):
    try:
        version = _guard(db, lambda: _move(db, code, user, reference, clock, expected_version, reason))
    except Refused as refused:
        flash(request, "Not moved: " + "; ".join(refused.problems), "error")
    else:
        flash(request, f"Prep batch {code} is under the current rules (version {version}).", "success")
    return RedirectResponse(request.url_for("prep_batch", code=code), status_code=303)


@router.post("/prep/standards", name="prep_make_standard")
def prep_make_standard(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
    step: Annotated[str, Form(max_length=210)] = "",
    source: Annotated[str, Form(max_length=200)] = "",
    prepared: Annotated[str, Form(max_length=10)] = "",
    pipettes: Annotated[list[str] | None, Form()] = None,
):
    form = {"step": step, "source": source, "prepared": prepared}
    try:
        _guard(db, lambda: _make_standard(db, user, step, source, prepared, tuple(pipettes or ())))
    except Refused as refused:
        return render(
            request,
            "prep/index.html",
            _home(db, reference, clock.today(), form, refused.problems),
            refused.status,
        )
    flash(request, "Standard recorded.", "success")
    return RedirectResponse(request.url_for("prep_home"), status_code=303)


def _make_standard(db: Session, user, step: str, source: str, prepared: str, pipettes: tuple[str, ...]):
    """`step` is "recipe|step"; `source` is "stock|name|lot" or "standard|name|YYYY-MM-DD"."""
    try:
        recipe, step_name = step.split("|", 1)
        kind, name, key = source.split("|", 2)
        day = date.fromisoformat(prepared)
    except ValueError:
        raise ValueError("choose a recipe step, a source and the prep date") from None
    found = StandardRef(name=name, prepared=date.fromisoformat(key)) if kind == "standard" else (name, key)
    return record_standard(db, rules(db).id, recipe, step_name, found, day, prep_actor_for(user), pipettes)


# ---------------------------------------------------------------- JSON API


def _in_lab_time(body: dict[str, Any], clock: LabClock) -> PrepBatch:
    """The batch with its times in the lab's zone (a time without a zone is the lab's): slim-prep dates
    the prep (a standard's expiry) by them and needs every time zoned (P-25)."""
    steps = {}
    for name in ("prep", "pour_out"):
        step = body.get(name)
        if isinstance(step, dict) and isinstance(step.get("at"), str):
            try:
                steps[name] = step | {"at": clock.local(datetime.fromisoformat(step["at"])).isoformat()}
            except ValueError:
                pass  # validation says what is wrong with it
    return PrepBatch.model_validate(body | steps)


class BatchIn(BaseModel):
    batch: dict[str, Any]
    expected_version: int = Field(default=0, ge=0)
    reason: str | None = Field(default=None, max_length=1000)


class StandardIn(BaseModel):
    recipe: str
    step: str
    stock: tuple[str, str] | None = None  # (name, lot)
    standard: StandardRef | None = None
    prepared: date
    pipettes: tuple[str, ...] = ()


def _api(db: Session, action, status: int = 200):
    try:
        return JSONResponse(_guard(db, action), status_code=status)
    except Refused as refused:
        return JSONResponse({"detail": "Refused.", "problems": refused.problems}, status_code=refused.status)


@api_router.get("/batches/{code}")
def api_batch(code: str, user: RequireStaff, db: DB):
    try:
        return _batch_json(db, code)
    except NotFound as error:
        return JSONResponse({"detail": str(error)}, status_code=404)


@api_router.post("/batches", status_code=201)
def api_save_batch(data: BatchIn, user: RequireStaff, db: DB, clock: Clock, reference: Reference):
    def save():
        batch = _in_lab_time(data.batch, clock)
        refuse_blocked(db, batch)
        rules_id = None if data.expected_version else rules(db).id  # a correction keeps its own (P-24)
        record_batch(
            db, batch, rules_id, prep_actor_for(user), TrSamples(db, reference), clock.now(),
            data.expected_version, data.reason,
        )  # fmt: skip
        return batch.code

    try:
        code = _guard(db, save)
    except Refused as refused:
        return JSONResponse({"detail": "Refused.", "problems": refused.problems}, status_code=refused.status)
    return JSONResponse(_batch_json(db, code), status_code=201)


@api_router.post("/batches/{code}/send")
def api_send(request: Request, code: str, user: RequireStaff, db: DB, reference: Reference):
    return _api(db, lambda: send(db, code, user, reference, request.app.state.specs))


class MoveIn(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(max_length=1000)


@api_router.post("/batches/{code}/move-rules")
def api_move_rules(code: str, data: MoveIn, user: RequireStaff, db: DB, reference: Reference, clock: Clock):
    def move():
        _move(db, code, user, reference, clock, data.expected_version, data.reason)
        return _batch_json(db, code)

    return _api(db, move)


@api_router.get("/standards")
def api_standards(user: RequireStaff, db: DB):
    return [_standard_json(r) for r in books.list_standards(db)]


@api_router.post("/standards", status_code=201)
def api_make_standard(data: StandardIn, user: RequireStaff, db: DB):
    def make():
        source = data.standard if data.standard is not None else data.stock
        if source is None:
            raise ValueError("give a stock (name, lot) or a standard")
        recorded = record_standard(
            db,
            rules(db).id,
            data.recipe,
            data.step,
            source,
            data.prepared,
            prep_actor_for(user),
            data.pipettes,
        )
        return _standard_json(recorded)

    return _api(db, make, 201)


@api_router.get("/run-list")
def api_run_list(user: RequireStaff, db: DB, reference: Reference, code: Annotated[list[str], Query()]):
    try:
        entries = _run_list(db, reference, code)
    except (PrepRefused, NotFound) as error:
        return JSONResponse({"detail": str(error)}, status_code=404 if isinstance(error, NotFound) else 422)
    return [e.model_dump(mode="json") for e in entries]
