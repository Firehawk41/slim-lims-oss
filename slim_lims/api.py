"""JSON API for Testing Requests. Responses are the TR domain objects as JSON (spec §4) —
the shape the rest of the SLIM ecosystem will consume."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from slim_lims import quotes
from slim_lims.auth import RequireUser
from slim_lims.db import LAB_SESSION_KEY, get_db
from slim_lims.domain import (
    ALLOWED_PROCESSING_TIMES,
    PaymentMethod,
    ProcessingTime,
    ReportingUnit,
    RequestType,
    TRStatus,
    TRSubmission,
    WaferSize,
)
from slim_lims.reference import ReferenceData
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import cancel_blocker
from slim_lims.samples import check_in_with_bottles
from slim_lims.schemas import CheckInIn, ChemicalMatchIn, StatusChangeIn, SubmissionIn
from slim_lims.scope import ScopeError
from slim_lims.security import csrf_protect
from slim_lims.services import (
    Conflict,
    FieldError,
    Forbidden,
    NotFound,
    SubmissionRejected,
    SubmissionService,
)

router = APIRouter(prefix="/api/submissions", tags=["submissions"], dependencies=[Depends(csrf_protect)])


def _visitors_lab(request: Request):
    """(the visitor's lab schema or None, its engine or None). The lab is entered here if no dependency did
    yet (review 2026-11-09 L2), so its engine never depends on the order dependencies resolve in."""
    labs = getattr(request.app.state, "labs", None)
    schema = request.session.get(LAB_SESSION_KEY) if labs is not None else None
    if not schema:
        return None, None
    engine = labs.engine_for(schema)
    if engine is None and labs.enter(schema) is not None:
        engine = labs.engine_for(schema)
    return schema, engine


def get_reference(request: Request) -> ReferenceData:
    """The lab's reference data: a visitor's own lab's when they have one (D-102, D-141), else the app's."""
    schema, engine = _visitors_lab(request)
    if engine is not None:
        own = request.app.state.labs.reference_for(
            schema, request.app.state.settings.reference_cache_ttl_seconds
        )
        if own is not None:
            return own
    return request.app.state.reference


def specs_for(request: Request):
    """The spec limits evaluation reads: a visitor's own lab's (they may change them there, D-143), else
    the app's reader. A visitor whose lab has gone gets no other lab's (fails closed, L2)."""
    schema, engine = _visitors_lab(request)
    if schema is not None:
        if engine is None:
            raise HTTPException(status_code=409, detail="Your demo lab has gone: choose a persona again.")
        from slim_lims.results import SpecReader

        return SpecReader(engine, get_reference(request))
    return request.app.state.specs


def slim_engine_for(request: Request):
    """Where slim-domain writes the lab's reference data: a visitor's own lab, else SLIM's database. Never
    the shared lab for a visitor (fails closed, L2)."""
    schema, engine = _visitors_lab(request)
    if schema is not None:
        if engine is None:
            raise HTTPException(status_code=409, detail="Your demo lab has gone: choose a persona again.")
        return engine
    return request.app.state.slim_engine


def get_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    reference: Annotated[ReferenceData, Depends(get_reference)],
) -> SubmissionService:
    return SubmissionService(
        TRSubmissionRepository(db),
        reference,
        lab_timezone=request.app.state.settings.lab_timezone,
        cancel_check=lambda tr_number: cancel_blocker(db, tr_number),
    )


Service = Annotated[SubmissionService, Depends(get_service)]
DB = Annotated[Session, Depends(get_db)]


def _named_quote(db: Session, data: SubmissionIn, user, service: SubmissionService) -> int | None:
    """The quote a request names (D-139): the customer's own, in force today; refused otherwise."""
    typed = data.quote_number.strip().upper().removeprefix("Q")
    if not typed:
        return None
    customer_id = data.customer_id if user.is_staff else user.customer_id
    own = {q.id for q in quotes.own_in_force(db, customer_id, service.lab_today())} if customer_id else set()
    if not typed.isdigit() or int(typed) not in own:
        raise SubmissionRejected(
            [FieldError(("quote_number",), "not one of this customer's quotes in force today")]
        )
    return int(typed)


def _dump(submission: TRSubmission) -> dict[str, Any]:
    return submission.model_dump(mode="json")


@router.post("", status_code=201, summary="Submit a Testing Request")
def create_submission(data: SubmissionIn, user: RequireUser, service: Service, db: DB):
    quote_id = _named_quote(db, data, user, service)
    submission = service.submit(data, user)
    if quote_id is not None:
        quotes.name_on_tr(db, submission.id, quote_id)
    db.commit()
    return _dump(submission)


@router.get("", summary="List Testing Requests (customers see their own company's only)")
def list_submissions(
    user: RequireUser,
    service: Service,
    status: TRStatus | None = None,
    request_type: RequestType | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
):
    result = service.list(user, status=status, request_type=request_type, page=page, per_page=per_page)
    return {
        "items": [_dump(s) for s in result.items],
        "total": result.total,
        "page": result.page,
        "per_page": result.per_page,
    }


@router.get("/{tr_number}", summary="Get one Testing Request")
def get_submission(tr_number: str, user: RequireUser, service: Service):
    return _dump(service.get(tr_number, user))


@router.post("/{tr_number}/status", summary="Change status (in progress, report, invoice, cancel)")
def change_status(tr_number: str, data: StatusChangeIn, user: RequireUser, service: Service, db: DB):
    submission = service.change_status(tr_number, data, user)
    db.commit()
    return _dump(submission)


@router.post("/{tr_number}/check-in", summary="Check in: accept a submitted request into the LIMS (staff)")
def check_in_submission(
    tr_number: str,
    data: CheckInIn,
    user: RequireUser,
    service: Service,
    db: DB,
    reference: Annotated[ReferenceData, Depends(get_reference)],
):
    if data.containers:  # the bottles are part of the same transaction (S-10)
        submission = check_in_with_bottles(db, service, reference, tr_number, data, data.containers, user)
    else:
        submission = service.check_in(tr_number, data, user)
    db.commit()
    return _dump(submission)


@router.post("/{tr_number}/samples/{position}/chemical", summary="Match a sample's chemical to SLIM (staff)")
def match_sample_chemical(
    tr_number: str, position: int, data: ChemicalMatchIn, user: RequireUser, service: Service, db: DB
):
    submission = service.match_chemical(tr_number, position, data, user)
    db.commit()
    return _dump(submission)


def register_error_handlers(app) -> None:
    @app.exception_handler(SubmissionRejected)
    async def _rejected(_request: Request, exc: SubmissionRejected):
        detail = [{"loc": ["body", *e.loc], "msg": e.msg, "type": "business_rule"} for e in exc.errors]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, exc: NotFound):
        return JSONResponse({"detail": f"Testing Request {exc} not found."}, status_code=404)

    @app.exception_handler(Forbidden)
    async def _forbidden(_request: Request, exc: Forbidden):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(ScopeError)
    async def _no_scope(_request: Request, exc: ScopeError):  # fail closed (review A6)
        return JSONResponse({"detail": "This account can't see any requests."}, status_code=403)

    @app.exception_handler(Conflict)
    async def _conflict(_request: Request, exc: Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)


# ---------------------------------------------------------------- reference data for the form

reference_router = APIRouter(
    prefix="/api/reference", tags=["reference"], dependencies=[Depends(csrf_protect)]
)


def _choice(enum_member, label: str | None = None) -> dict[str, Any]:
    return {"value": int(enum_member), "label": label or enum_member.label}


@reference_router.get("", summary="Choices for the submission form, from the SLIM reference cache")
def reference_data(user: RequireUser, reference: Annotated[ReferenceData, Depends(get_reference)]):
    request_types = []
    for rt in RequestType:
        request_types.append(
            {
                **_choice(rt),
                "processing_times": [_choice(pt, pt.portal_label) for pt in ALLOWED_PROCESSING_TIMES[rt]],
                "analyses": [
                    {
                        "id": a.id,
                        "code": a.code,
                        "name": a.name,
                        "group": a.group,
                        "description": a.description,
                    }
                    for a in reference.analyses_for(rt)
                ],
            }
        )
    body: dict[str, Any] = {
        "request_types": request_types,
        "chemicals": [{"id": c.id, "name": c.name} for c in reference.chemical_choices()],
        "elements": [{"id": e.id, "symbol": e.symbol, "name": e.name} for e in reference.element_choices()],
        "wafer_sizes": [_choice(w) for w in WaferSize],
        "reporting_units": [_choice(u) for u in ReportingUnit],
        "payment_methods": [_choice(p) for p in PaymentMethod],
        "processing_time_requiring_requested_time": int(ProcessingTime.TIME_LIMITED),
    }
    if user.is_staff:
        body["customers"] = [
            {"id": c.id, "name": c.name, "locations": list(reference.locations(c.id))}
            for c in reference.customer_choices()
        ]
    else:
        body["locations"] = list(reference.locations(user.customer_id))
    return body
