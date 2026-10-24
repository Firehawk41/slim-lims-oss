"""Staff pages and JSON API for analytical results (slim-data, D-70). Staff only.

    /results                      upload a result file (preview or import) and list prep batches
    /results/batches/{code}       a batch's results as they stand: every cup, what was believed, the
                                  QC numbers (slim-data D-81), and the actions on them
    /results/batches/{code}/trace ?sample&analysis_id&analyte[&version]: every step the engine took
    POST /api/results/imports     multipart `file`; `?preview=true` checks without writing
    GET  /api/results/batches[/{code}]
    POST /api/results/batches/{code}/review | /re-evaluate
    POST /api/results/samples/{sample}/analyses/{analysis_id}/analytes/{analyte}/{action}
         action: accept | rerun | exception | reject | correct
    POST /api/results/samples/{sample}/analyses/{analysis_id}/release
    GET  /api/results/reason-categories   the policy's fixed lists for accept and exception (D-72)
    /results/reports/{tr}         the report as it stands, its issues, and "Issue report" (D-73)
    GET  /api/results/reports/{tr}   POST /api/results/reports/{tr}/issue

An import is all or nothing: the file's readings, then an evaluation of every batch it touches,
and received TRs moved to in progress (D-71), in one transaction. A release changes results only.
Errors: a refused file -> 422 with one entry per row error; an action the workflow or policy
doesn't allow (role, four-eyes, state) -> 409; a missing reason or a category the policy doesn't
list -> 422; unknown batch/result -> 404.
"""

from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from slim_data.persistence._rows import NotFound
from slim_data.engine.ruleset import RulesetError
from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.domain.enums import Action
from slim_data.domain.workflow import UnknownCategory, WorkflowError
from slim_data.persistence.rulesets import current_policy
from slim_data.service import workflow as wf
from slim_data.service.imports import ImportPlan, ImportRefused, preview_import
from slim_data.persistence.results import issues_for_tr, load_result
from slim_data.service.views import batch_view, batches, report_view

from slim_lims.api import Service, get_reference
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.reference import ReferenceData
from slim_lims.results import (
    ResultAction,
    StaffDirectory,
    act_on_result,
    actor_for,
    document_sha256,
    import_and_evaluate,
    issue_report,
    reference_lookup,
    report_complete,
    report_document,
    start_work,
)
from slim_lims.schemas import ReEvaluateIn, ResultActionIn, SignOffIn
from slim_lims.services import Conflict
from slim_lims.services import NotFound as TRNotFound
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
api_router = APIRouter(prefix="/api/results", tags=["results"], dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]


def _lookup(request: Request, db: Session, reference: ReferenceData):
    return reference_lookup(db, reference, request.app.state.specs)


def _plan_json(plan: ImportPlan) -> dict[str, Any]:
    return {
        "ok": plan.ok,
        "file": plan.source.name,
        "sha256": plan.source.sha256,
        "run": None if plan.run is None else {
            "run_code": plan.run.run_code, "instrument": plan.run.instrument,
            "analyst": plan.run.analyst.display, "analyzed_at": plan.run.analyzed_at.isoformat(),
        },
        "new_batches": [b.code for b in plan.new_batches],
        "batch_additions": [a.code for a in plan.batch_additions],
        "readings": len(plan.readings),
        "rejected": len(plan.rejected) + len(plan.rejected_stored),
        "skipped": plan.skipped,
        "errors": [{"line": e.line, "column": e.column, "message": e.message} for e in plan.errors],
    }


def _import(request, db, reference, service, user, content: bytes, filename: str):
    """(status, body): 201 with the receipt, or 422 with the reason; commits only on success."""
    lookup = _lookup(request, db, reference)
    try:
        receipt, evaluations, trs = import_and_evaluate(
            db, content, filename, actor_for(user), lookup, StaffDirectory(db)
        )
        started = start_work(service, trs, user)
    except ImportRefused as refused:
        db.rollback()
        return 422, {"detail": "The file was not imported.", "plan": _plan_json(refused.plan)}
    except (NotFound, RulesetError) as error:  # e.g. no published ruleset for the batch's analysis
        db.rollback()
        return 422, {"detail": f"The file was not imported: {error}."}
    except Conflict as error:  # a TR changed under us while starting work
        db.rollback()
        return 409, {"detail": f"The file was not imported: {error}."}
    db.commit()
    return 201, {
        "run_id": receipt.run_id,
        "readings": receipt.readings,
        "skipped": receipt.skipped,
        "rejected": receipt.rejected,
        "batches_registered": list(receipt.batches_registered),
        "evaluated": {
            code: {"new_versions": len(e.new_versions), "unchanged": e.unchanged,
                   "released_affected": [str(k) for k in e.released_affected]}
            for code, e in evaluations.items()
        },
        "started": started,
    }


def _decide(db: Session, action) -> tuple[int, dict[str, Any]]:
    """Runs one decision; (200, body) and commits, or (status, reason) and rolls back."""
    try:
        body = action()
    except wf.BatchReviewRefused as error:
        db.rollback()
        return 409, {"detail": "Nothing was reviewed.", "problems": error.problems}
    except wf.NothingReleased as error:
        db.rollback()
        return 409, {"detail": "Nothing was released.", "waiting": [f"{c}: {why}" for c, why in error.waiting]}
    except UnknownCategory as error:
        db.rollback()
        return 422, {"detail": str(error)}
    except WorkflowError as error:
        db.rollback()
        return 409, {"detail": str(error)}
    except (NotFound, TRNotFound) as error:
        db.rollback()
        return 404, {"detail": str(error)}
    except Conflict as error:  # the TR changed under us
        db.rollback()
        return 409, {"detail": str(error)}
    except ValidationError as error:  # e.g. an exception without a note
        db.rollback()
        return 422, {"detail": "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors())}
    db.commit()
    return 200, body


def _report(db: Session, service, tr_number: str, user, renderer, reference) -> dict[str, Any]:
    """The report as it stands now, rendered as "Issue report" would issue it, and every issue so
    far (raises the portal's NotFound)."""
    submission = service.get(tr_number, user)
    view = report_view(db, tr_number)
    document = report_document(view)
    html = renderer(db, submission, view, reference) if view.lines else None
    return {
        **document,
        "waiting": list(view.waiting),
        "complete": report_complete(view, submission),
        "sha256": sha256(html.encode()).hexdigest() if html else None,
        "html": html,
        "issues": [
            {"id": i.id, "sha256": i.file_sha256, "amends": i.amends, "lines": len(i.lines),
             "by": i.created_by.display, "at": i.created_at.isoformat() if i.created_at else None}
            for i in issues_for_tr(db, tr_number)
        ],
    }


def _categories(db: Session) -> dict[str, list[str]]:
    """The reason categories the published policy lists (D-72); empty before one is published."""
    published = current_policy(db)
    policy = published.policy if published else None
    return {
        "accept": list(policy.categories(Action.ACCEPT)) if policy else [],
        "exception": list(policy.categories(Action.RECORD_EXCEPTION)) if policy else [],
    }


def _result_key(sample: str, analysis_id: int, analyte: str) -> tuple[SampleRef, AnalyteRef]:
    try:
        return SampleRef.parse(sample), AnalyteRef(analysis_id=analysis_id, code=analyte)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No result {sample} {analyte}.") from None


def _review(db, user, code: str, data: SignOffIn):
    return _decide(db, lambda: {"reviewed": wf.review_batch(db, code, actor_for(user), data.note, data.solo)})


def _re_evaluate(request, db, reference, user, code: str, data: ReEvaluateIn):
    def run():
        receipt = wf.evaluate_batch(db, code, actor_for(user), _lookup(request, db, reference),
                                    **({"reason": data.reason} if data.reason else {}))
        return {"new_versions": len(receipt.new_versions), "unchanged": receipt.unchanged,
                "released_affected": [str(k) for k in receipt.released_affected]}
    return _decide(db, run)


def _act(db, user, sample: SampleRef, analyte: AnalyteRef, action: ResultAction, data: ResultActionIn):
    def run():
        event = act_on_result(db, action, sample, analyte, actor_for(user), data.reason_category, data.note)
        return {"result": f"{sample} {analyte.code}", "event": event.kind.name.lower(), "version": event.version}
    return _decide(db, run)


def _release(db, user, sample: SampleRef, analysis_id: int, data: SignOffIn):
    def run():
        released = wf.release(db, sample, analysis_id, actor_for(user), data.note, data.solo)
        return {"sample": str(sample), "analysis_id": analysis_id, "released": list(released.released),
                "waiting": [f"{c}: {why}" for c, why in released.waiting]}
    return _decide(db, run)


def _batch_rows(reference: ReferenceData, db: Session) -> list[dict[str, Any]]:
    rows = []
    for b in batches(db):
        analysis = reference.analysis(b.analysis_id)
        rows.append({"code": b.code, "analysis_id": b.analysis_id,
                     "analysis": analysis.name if analysis else f"Analysis #{b.analysis_id}",
                     "prep_method": b.prep_method, "registered_at": b.registered_at.isoformat()})
    return rows


# ---------------------------------------------------------------- pages

@router.get("/results", name="results_home")
def results_home(request: Request, user: RequireStaff, db: DB, reference: Reference):
    return render(request, "results/index.html", {"batches": _batch_rows(reference, db)})


@router.post("/results/upload", name="results_upload")
def results_upload(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    service: Service,
    file: Annotated[UploadFile, File()],
    action: Annotated[str, Form()] = "preview",
):
    content, filename = file.file.read(), file.filename or "upload.csv"  # already spooled by FastAPI
    if action == "import":
        status, body = _import(request, db, reference, service, user, content, filename)
        if status == 201:
            evaluated = ", ".join(body["evaluated"]) or "none"
            started = f" Started work on {', '.join(body['started'])}." if body["started"] else ""
            flash(request, f"Imported {filename}: {body['readings']} readings "
                           f"({body['skipped']} already imported); batches evaluated: {evaluated}.{started}")
            return RedirectResponse(request.url_for("results_home").path, status_code=303)
        context = {"batches": _batch_rows(reference, db), "error": body["detail"], "plan": body.get("plan")}
        return render(request, "results/index.html", context, 422)
    plan = preview_import(db, content, filename, actor_for(user), _lookup(request, db, reference), StaffDirectory(db))
    db.rollback()  # a preview writes nothing
    return render(request, "results/index.html", {"batches": _batch_rows(reference, db), "plan": _plan_json(plan)})


@router.get("/results/batches/{code}", name="results_batch")
def results_batch(request: Request, code: str, user: RequireStaff, db: DB, reference: Reference):
    try:
        view = batch_view(db, code)
    except NotFound:
        return render(request, "error.html", {"status": 404, "message": f"Prep batch {code} not found."}, 404)
    release_groups = sorted({(r.sample, r.analyte.analysis_id) for r in view.results
                             if r.status.name not in ("RELEASED", "REJECTED")},
                            key=lambda k: (k[0].tr_number, k[0].position, k[1]))
    return render(request, "results/batch.html", {
        "view": view, "reference": reference, "release_groups": release_groups, "actions": ResultAction,
        "categories": _categories(db),
    })


@router.get("/results/batches/{code}/trace", name="results_trace")
def results_trace(request: Request, code: str, sample: str, analysis_id: int, analyte: str, user: RequireStaff,
                  db: DB, reference: Reference, version: int | None = None):
    """One result's story: its versions, who did what, and the engine's trace step by step (D-16)."""
    sample_ref, analyte_ref = _result_key(sample, analysis_id, analyte)
    result = load_result(db, sample_ref, analyte_ref)
    if result is None or (version is not None and not 1 <= version <= len(result.versions)):
        return render(request, "error.html", {"status": 404, "message": f"No result {sample} {analyte}."}, 404)
    shown = result.versions[(version or len(result.versions)) - 1]
    return render(request, "results/trace.html", {
        "code": code, "result": result, "shown": shown, "analysis": reference.analysis(analysis_id),
        "events": result.events_for(shown.version),
    })


def _back(request: Request, code: str, outcome: tuple[int, dict[str, Any]], done: str) -> RedirectResponse:
    status, body = outcome
    if status == 200:
        flash(request, done.format(**body))
    else:
        details = body.get("problems") or body.get("waiting") or []
        flash(request, " ".join([str(body["detail"]), *details]), "error")
    return RedirectResponse(request.url_for("results_batch", code=code).path, status_code=303)


@router.post("/results/batches/{code}/review", name="results_review")
def results_review(request: Request, code: str, user: RequireStaff, db: DB,
                   note: Annotated[str, Form(max_length=2000)] = "", solo: Annotated[bool, Form()] = False):
    outcome = _review(db, user, code, SignOffIn(note=note, solo=solo))
    return _back(request, code, outcome, "Reviewed {reviewed} result(s).")


@router.post("/results/batches/{code}/re-evaluate", name="results_re_evaluate")
def results_re_evaluate(request: Request, code: str, user: RequireStaff, db: DB, reference: Reference,
                        reason: Annotated[str, Form(max_length=500)] = ""):
    outcome = _re_evaluate(request, db, reference, user, code, ReEvaluateIn(reason=reason))
    return _back(request, code, outcome, "Re-evaluated: {new_versions} new version(s), {unchanged} unchanged.")


@router.post("/results/batches/{code}/act", name="results_act")
def results_act(
    request: Request, code: str, user: RequireStaff, db: DB,
    sample: Annotated[str, Form()], analysis_id: Annotated[int, Form()], analyte: Annotated[str, Form()],
    action: Annotated[ResultAction, Form()],
    reason_category: Annotated[str, Form(max_length=100)] = "", note: Annotated[str, Form(max_length=2000)] = "",
):
    sample_ref, analyte_ref = _result_key(sample, analysis_id, analyte)
    data = ResultActionIn(reason_category=reason_category, note=note)
    return _back(request, code, _act(db, user, sample_ref, analyte_ref, action, data), "{result}: {event}.")


@router.post("/results/batches/{code}/release", name="results_release")
def results_release(
    request: Request, code: str, user: RequireStaff, db: DB,
    sample: Annotated[str, Form()], analysis_id: Annotated[int, Form()],
    note: Annotated[str, Form(max_length=2000)] = "", solo: Annotated[bool, Form()] = False,
):
    sample_ref, _ = _result_key(sample, analysis_id, "X")
    outcome = _release(db, user, sample_ref, analysis_id, SignOffIn(note=note, solo=solo))
    return _back(request, code, outcome, "Released {sample}: {released}.")


# ---------------------------------------------------------------- JSON API

@api_router.post("/imports", summary="Import a result file (generic_csv/1), or preview it with ?preview=true")
def api_import(
    request: Request,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    service: Service,
    file: Annotated[UploadFile, File()],
    preview: bool = False,
):
    content, filename = file.file.read(), file.filename or "upload.csv"
    if preview:
        plan = preview_import(db, content, filename, actor_for(user), _lookup(request, db, reference),
                              StaffDirectory(db))
        db.rollback()
        return _plan_json(plan)
    status, body = _import(request, db, reference, service, user, content, filename)
    return JSONResponse(body, status_code=status)


@api_router.get("/batches", summary="Prep batches, newest first")
def api_batches(user: RequireStaff, db: DB, reference: Reference):
    return _batch_rows(reference, db)


@api_router.get("/batches/{code}", summary="A prep batch's results as they stand")
def api_batch(code: str, user: RequireStaff, db: DB):
    try:
        return batch_view(db, code).model_dump(mode="json")
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Prep batch {code} not found.") from None


@api_router.post("/batches/{code}/review", summary="Review every draft of a batch (or none)")
def api_review(request: Request, code: str, data: SignOffIn, user: RequireStaff, db: DB):
    status, body = _review(db, user, code, data)
    return JSONResponse(body, status_code=status)


@api_router.post("/batches/{code}/re-evaluate", summary="Evaluate a batch again (new versions only where changed)")
def api_re_evaluate(request: Request, code: str, data: ReEvaluateIn, user: RequireStaff, db: DB,
                    reference: Reference):
    status, body = _re_evaluate(request, db, reference, user, code, data)
    return JSONResponse(body, status_code=status)


@api_router.post("/samples/{sample}/analyses/{analysis_id}/analytes/{analyte}/{action}",
                 summary="Accept, rerun, exception, reject or correct one result")
def api_act(sample: str, analysis_id: int, analyte: str, action: ResultAction, data: ResultActionIn,
            user: RequireStaff, db: DB):
    sample_ref, analyte_ref = _result_key(sample, analysis_id, analyte)
    status, body = _act(db, user, sample_ref, analyte_ref, action, data)
    return JSONResponse(body, status_code=status)


@api_router.post("/samples/{sample}/analyses/{analysis_id}/release",
                 summary="Release the ready results of a sample + analysis; the rest wait")
def api_release(sample: str, analysis_id: int, data: SignOffIn, user: RequireStaff, db: DB):
    sample_ref, _ = _result_key(sample, analysis_id, "X")
    status, body = _release(db, user, sample_ref, analysis_id, data)
    return JSONResponse(body, status_code=status)


@api_router.get("/reason-categories", summary="The reason categories accept and exception may give (D-72)")
def api_reason_categories(user: RequireStaff, db: DB):
    return _categories(db)


@router.get("/results/reports/{tr_number}", name="results_report")
def results_report(request: Request, tr_number: str, user: RequireStaff, db: DB, service: Service,
                   reference: Reference):
    try:
        report = _report(db, service, tr_number, user, request.app.state.report_renderer, reference)
    except TRNotFound:
        return render(request, "error.html", {"status": 404, "message": f"Testing Request {tr_number} not found."}, 404)
    return render(request, "results/report.html", {"r": report, "reference": reference})


@router.post("/results/reports/{tr_number}/issue", name="results_issue")
def results_issue(request: Request, tr_number: str, user: RequireStaff, db: DB, service: Service, reference: Reference):
    status, body = _decide(db, lambda: issue_report(db, service, tr_number, user, request.app.state.report_renderer, reference))
    if status == 200:
        amended = f" (amends issue {body['amends']})" if body["amended"] else ""
        flash(request, f"Report issue {body['issue_id']} recorded{amended}: {body['lines']} result(s).")
    else:
        flash(request, str(body["detail"]), "error")
    return RedirectResponse(request.url_for("results_report", tr_number=tr_number).path, status_code=303)


@api_router.get("/reports/{tr_number}", summary="The report as it stands (released results) and its issues")
def api_report(request: Request, tr_number: str, user: RequireStaff, db: DB, service: Service, reference: Reference):
    try:
        report = _report(db, service, tr_number, user, request.app.state.report_renderer, reference)
        report.pop("html")  # the API gives the facts; the page shows the rendered report
        return report
    except TRNotFound:
        raise HTTPException(status_code=404, detail=f"Testing Request {tr_number} not found.") from None


@api_router.post("/reports/{tr_number}/issue", summary="Record a report issue; moves the TR to partial / complete report")
def api_issue(request: Request, tr_number: str, user: RequireStaff, db: DB, service: Service, reference: Reference):
    status, body = _decide(db, lambda: issue_report(db, service, tr_number, user, request.app.state.report_renderer, reference))
    return JSONResponse(body, status_code=201 if status == 200 else status)
