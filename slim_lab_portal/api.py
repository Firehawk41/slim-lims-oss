"""JSON API for Testing Requests. Responses are the TR domain objects as JSON (spec §4) —
the shape the rest of the SLIM ecosystem will consume."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from slim_lab_portal.auth import RequireUser
from slim_lab_portal.db import get_db
from slim_lab_portal.domain import (
    ALLOWED_PROCESSING_TIMES,
    PaymentMethod,
    ProcessingTime,
    ReportingUnit,
    RequestType,
    TRStatus,
    TRSubmission,
    WaferSize,
)
from slim_lab_portal.reference import ReferenceData
from slim_lab_portal.repositories import TRSubmissionRepository
from slim_lab_portal.schemas import CheckInIn, ChemicalMatchIn, StatusChangeIn, SubmissionIn
from slim_lab_portal.security import csrf_protect
from slim_lab_portal.services import Conflict, Forbidden, NotFound, SubmissionRejected, SubmissionService

router = APIRouter(prefix="/api/submissions", tags=["submissions"], dependencies=[Depends(csrf_protect)])


def get_reference(request: Request) -> ReferenceData:
    return request.app.state.reference


def get_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    reference: Annotated[ReferenceData, Depends(get_reference)],
) -> SubmissionService:
    return SubmissionService(TRSubmissionRepository(db), reference,
                             lab_timezone=request.app.state.settings.lab_timezone)


Service = Annotated[SubmissionService, Depends(get_service)]
DB = Annotated[Session, Depends(get_db)]


def _dump(submission: TRSubmission) -> dict[str, Any]:
    return submission.model_dump(mode="json")


@router.post("", status_code=201, summary="Submit a Testing Request")
def create_submission(data: SubmissionIn, user: RequireUser, service: Service, db: DB):
    submission = service.submit(data, user)
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
    return {"items": [_dump(s) for s in result.items], "total": result.total, "page": result.page,
            "per_page": result.per_page}


@router.get("/{tr_number}", summary="Get one Testing Request")
def get_submission(tr_number: str, user: RequireUser, service: Service):
    return _dump(service.get(tr_number, user))


@router.post("/{tr_number}/status", summary="Change status (in progress, report, invoice, cancel)")
def change_status(tr_number: str, data: StatusChangeIn, user: RequireUser, service: Service, db: DB):
    submission = service.change_status(tr_number, data, user)
    db.commit()
    return _dump(submission)


@router.post("/{tr_number}/check-in", summary="Check in: accept a submitted request into the LIMS (staff)")
def check_in_submission(tr_number: str, data: CheckInIn, user: RequireUser, service: Service, db: DB):
    submission = service.check_in(tr_number, data, user)
    db.commit()
    return _dump(submission)


@router.post("/{tr_number}/samples/{position}/chemical", summary="Match a sample's chemical to SLIM (staff)")
def match_sample_chemical(tr_number: str, position: int, data: ChemicalMatchIn, user: RequireUser,
                          service: Service, db: DB):
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

    @app.exception_handler(Conflict)
    async def _conflict(_request: Request, exc: Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)


# ---------------------------------------------------------------- reference data for the form

reference_router = APIRouter(prefix="/api/reference", tags=["reference"], dependencies=[Depends(csrf_protect)])


def _choice(enum_member, label: str | None = None) -> dict[str, Any]:
    return {"value": int(enum_member), "label": label or enum_member.label}


@reference_router.get("", summary="Choices for the submission form, from the SLIM reference cache")
def reference_data(user: RequireUser, reference: Annotated[ReferenceData, Depends(get_reference)]):
    request_types = []
    for rt in RequestType:
        request_types.append({
            **_choice(rt),
            "processing_times": [_choice(pt, pt.portal_label) for pt in ALLOWED_PROCESSING_TIMES[rt]],
            "analyses": [
                {"id": a.id, "code": a.code, "name": a.name, "group": a.group, "description": a.description}
                for a in reference.analyses_for(rt)
            ],
        })
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
        body["customers"] = [{"id": c.id, "name": c.name, "locations": list(reference.locations(c.id))}
                             for c in reference.customer_choices()]
    else:
        body["locations"] = list(reference.locations(user.customer_id))
    return body
