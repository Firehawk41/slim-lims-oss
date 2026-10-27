"""Staff pages and JSON API for the prep book (slim-prep, P6). Staff only.

    /prep                           the prep book: batches, standards, stocks; new batch, make a standard
    /prep/new-batch                 ?method&analysis&code&samples: the grid for a new batch, one row per cup
    POST /prep/batches              records the grid (slim-prep checks it against the rules and the book)
    /prep/batches/{code}            the batch with its figures (net, dilution, spike level), its versions,
                                    "Send to results" and the run list
    POST /prep/batches/{code}/send  registers it in slim-data, or sends its correction (P-18, P-20)
    POST /prep/standards            makes a standard from a recipe step and its source (P-14, P-15)
    GET  /prep/run-list.csv         ?code=…&code=…: the planned run list for those batches (P-21)
    GET  /api/prep/batches/{code}   POST /api/prep/batches   POST /api/prep/batches/{code}/send
    GET  /api/prep/standards        POST /api/prep/standards  GET /api/prep/run-list?code=…

Errors: the rules or the book refuse -> 422 with every problem; someone else saved first -> 409;
unknown batch or standard -> 404.
"""

from datetime import UTC, date, datetime
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
from slim_prep.service.books import record_batch, record_standard
from slim_prep.service.handoff import sent_version
from slim_prep.service.run_list import run_list_for
from sqlalchemy.orm import Session

from slim_lims.api import get_reference
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.prep import PREP_ANALYSES, chemicals, prep_actor_for, rules, send
from slim_lims.reference import ReferenceData
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
api_router = APIRouter(prefix="/api/prep", tags=["prep"], dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]


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


def _home(db: Session, reference: ReferenceData, form=None, problems=None) -> dict[str, Any]:
    current = rules(db)
    return {
        "rules": current.rules,
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
        "today": date.today().isoformat(),
        "form": form or {},
        "problems": problems or [],
    }


@router.get("/prep", name="prep_home")
def prep_home(request: Request, user: RequireStaff, db: DB, reference: Reference):
    return render(request, "prep/index.html", _home(db, reference))


@router.get("/prep/batches/{code}", name="prep_batch")
def prep_batch(request: Request, code: str, user: RequireStaff, db: DB):
    try:
        data = _batch_json(db, code)
    except NotFound:
        return render(request, "error.html", {"message": f"No prep batch {code}."}, 404)
    history = books.history_batch(db, code)
    return render(request, "prep/batch.html", {"b": data, "history": history})


@router.get("/prep/run-list.csv", name="prep_run_list")
def prep_run_list(user: RequireStaff, db: DB, code: Annotated[list[str], Query()]):
    try:
        entries = _run_list(db, code)
    except (PrepRefused, NotFound) as error:
        return JSONResponse({"detail": str(error)}, status_code=404 if isinstance(error, NotFound) else 422)
    headers = {"Content-Disposition": f'attachment; filename="run-list-{"-".join(code)}.csv"'}
    return Response(run_list_csv(entries), media_type="text/csv", headers=headers)


def _run_list(db: Session, codes: list[str]):
    samples = [s.sample for c in codes for s in books.get_batch(db, c).item.samples]
    return run_list_for(db, codes, chemicals(db, samples), rules(db).id)


# ---------------------------------------------------------------- a new batch: the grid


def _grid(
    db: Session, method: str, analysis: str, code: str, samples: list[str], used: frozenset = frozenset()
) -> dict[str, Any]:
    """The grid's context. Standards still in date are offered, and those the batch already used (a
    correction keeps them, whether or not they have expired since)."""
    current = rules(db).rules
    found = current.method(method)
    if found is None:
        raise LookupError(f"no prep method {method!r}")
    standards = [
        r.item for r in books.list_standards(db) if r.item.expires >= date.today() or r.item.ref in used
    ]
    return {
        "method": found,
        "analysis": analysis,
        "code": code,
        "samples": samples,
        "cups": list(range(1, found.sample_cups + 1)),
        "spike_cups": list(range(found.sample_cups + 1, found.sample_cups + found.spike_cups + 1)),
        "rules": current,
        "standards": standards,
    }


@router.get("/prep/new-batch", name="prep_new_batch")
def prep_new_batch(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    method: str = "",
    analysis: str = "",
    code: str = "",
    samples: str = "",
):
    ids = [s.strip() for s in samples.replace(",", "\n").splitlines() if s.strip()]
    try:
        if not (code.strip() and ids):
            raise LookupError("give the batch a code and at least one sample")
        grid = _grid(db, method, analysis, code.strip(), ids)
    except LookupError as error:
        form = {"method": method, "analysis": analysis, "code": code, "samples": samples}
        return render(request, "prep/index.html", _home(db, reference, form, [str(error)]), 422)
    return render(request, "prep/new_batch.html", {**grid, "values": {}, "problems": []})


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


def _values(batch: PrepBatch) -> dict[str, str]:
    """A recorded batch as the grid's fields: what a correction starts from."""
    values = {"balance": batch.balance, "pour_out": batch.pour_out.analyst if batch.pour_out else "",
              "note": batch.note or ""}  # fmt: skip
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
            for line in cup.spikes[:1]:  # the grid has one spike line per cup
                values[f"{k}_standard"] = f"{line.standard.name}|{line.standard.prepared.isoformat()}"
                values[f"{k}_volume"] = str(line.volume_ul)
                values[f"{k}_pipette"] = line.pipette
    return values


def _used(batch: PrepBatch) -> frozenset:
    return frozenset(line.standard for s in batch.samples for c in s.cups for line in c.spikes)


@router.get("/prep/batches/{code}/edit", name="prep_edit_batch")
def prep_edit_batch(request: Request, code: str, user: RequireStaff, db: DB):
    try:
        recorded = books.get_batch(db, code)
    except NotFound:
        return render(request, "error.html", {"message": f"No prep batch {code}."}, 404)
    b = recorded.item
    grid = _grid(db, b.method, b.analysis, code, [s.sample for s in b.samples], _used(b))
    context = {**grid, "values": _values(b), "problems": [], "version": recorded.version}
    return render(request, "prep/new_batch.html", context)


def _batch_from_grid(values: dict[str, str], db: Session, user) -> PrepBatch:
    """The grid's fields -> a PrepBatch, or PrepRefused with every problem. A correction keeps who
    prepped it and when (and who poured out, unless the initials changed): its own author and reason
    go on the version."""
    current = rules(db).rules
    method = current.method(values.get("method", ""))
    if method is None:
        raise LookupError(f"no prep method {values.get('method')!r}")
    by_ref = {f"{s.name}|{s.prepared.isoformat()}": s for s in (r.item for r in books.list_standards(db))}
    problems: list[str] = []
    now = datetime.now(UTC)
    samples = []
    i = 0
    while f"sample_{i}" in values:
        sample_id = values[f"sample_{i}"]
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
            spikes = ()
            kind = CupKind.SAMPLE if n <= method.sample_cups else CupKind.SPIKE
            if kind == CupKind.SPIKE:
                standard = by_ref.get(values.get(f"{key}_standard", ""))
                volume = _decimal(values, f"{key}_volume", problems, f"{label} spike volume")
                if standard is None or volume is None:
                    problems.append(f"{label}: a spike needs a standard and a volume")
                    continue
                spikes = (SpikeLine.of(standard, volume, values.get(f"{key}_pipette", "")),)
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
        i += 1
    if problems:
        raise PrepRefused([Problem(where="", message=p) for p in problems])
    pour_out = values.get("pour_out", "").strip()
    prep = StepRecord(analyst=user.initials or user.email, at=now)
    poured = StepRecord(analyst=pour_out, at=now) if pour_out else None
    if _expected(values):
        before = books.get_batch(db, values.get("code", "")).item
        prep = before.prep
        if before.pour_out is not None and poured is not None and before.pour_out.analyst == pour_out:
            poured = before.pour_out
    return PrepBatch(
        code=values.get("code", ""),
        method=method.name,
        analysis=values.get("analysis", ""),
        balance=values.get("balance", ""),
        pour_out=poured,
        prep=prep,
        samples=tuple(samples),
        note=values.get("note") or None,
    )


@router.post("/prep/batches", name="prep_save_batch")
def prep_save_batch(request: Request, user: RequireStaff, db: DB, values: Form_):
    expected = _expected(values)

    def save():
        batch = _batch_from_grid(values, db, user)
        reason = values.get("reason") or None
        return record_batch(db, batch, rules(db).id, prep_actor_for(user), expected, reason)

    try:
        recorded, _ = _guard(db, save)
    except Refused as refused:
        ids = [
            values[k]
            for k in sorted((k for k in values if k.startswith("sample_")), key=lambda k: int(k[7:]))
        ]
        try:
            used = _used(books.get_batch(db, values.get("code", "")).item) if expected else frozenset()
        except NotFound:
            used = frozenset()
        grid = _grid(
            db, values.get("method", ""), values.get("analysis", ""), values.get("code", ""), ids, used
        )
        context = {**grid, "values": values, "problems": refused.problems}
        if expected:
            context["version"] = expected
        return render(request, "prep/new_batch.html", context, refused.status)
    what = f"corrected (version {recorded.version})" if expected else "recorded"
    flash(request, f"Prep batch {values.get('code')} {what}.", "success")
    return RedirectResponse(request.url_for("prep_batch", code=values.get("code", "")), status_code=303)


def _expected(values: dict[str, str]) -> int:
    """The version a correction was made from; 0 for a new batch."""
    text = values.get("expected_version", "0").strip()
    return int(text) if text.isdigit() else 0


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


@router.post("/prep/standards", name="prep_make_standard")
def prep_make_standard(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
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
            request, "prep/index.html", _home(db, reference, form, refused.problems), refused.status
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
def api_save_batch(data: BatchIn, user: RequireStaff, db: DB):
    def save():
        batch = PrepBatch.model_validate(data.batch)
        record_batch(db, batch, rules(db).id, prep_actor_for(user), data.expected_version, data.reason)
        return batch.code

    try:
        code = _guard(db, save)
    except Refused as refused:
        return JSONResponse({"detail": "Refused.", "problems": refused.problems}, status_code=refused.status)
    return JSONResponse(_batch_json(db, code), status_code=201)


@api_router.post("/batches/{code}/send")
def api_send(request: Request, code: str, user: RequireStaff, db: DB, reference: Reference):
    return _api(db, lambda: send(db, code, user, reference, request.app.state.specs))


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
def api_run_list(user: RequireStaff, db: DB, code: Annotated[list[str], Query()]):
    try:
        entries = _run_list(db, code)
    except (PrepRefused, NotFound) as error:
        return JSONResponse({"detail": str(error)}, status_code=404 if isinstance(error, NotFound) else 422)
    return [e.model_dump(mode="json") for e in entries]
