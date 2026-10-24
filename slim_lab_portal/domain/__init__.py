"""SLIM domain v2 TR objects (docs/SLIM_DOMAIN_V2.md). Imports only pydantic and the standard library."""

from slim_lab_portal.domain.enums import (
    ALLOWED_PROCESSING_TIMES,
    ActorType,
    PaymentMethod,
    ProcessingTime,
    ReportingUnit,
    RequestType,
    SubmissionSource,
    TRStatus,
    WaferSize,
    WaterPackage,
)
from slim_lab_portal.domain.tr import (
    Actor,
    CustomerSnapshot,
    TRReceipt,
    TRSample,
    TRStatusEvent,
    TRSubmission,
    add_working_days,
    format_tr_number,
)
from slim_lab_portal.domain.workflow import (
    ALLOWED_TRANSITIONS,
    TransitionNotAllowed,
    allowed_next_statuses,
    check_in,
    match_chemical,
    transition,
)

__all__ = [
    "ALLOWED_PROCESSING_TIMES", "ALLOWED_TRANSITIONS", "Actor", "ActorType", "CustomerSnapshot",
    "PaymentMethod", "ProcessingTime", "ReportingUnit", "RequestType", "SubmissionSource",
    "TRReceipt", "TRSample", "TRStatus", "TRStatusEvent", "TRSubmission", "TransitionNotAllowed",
    "WaferSize", "WaterPackage", "add_working_days", "allowed_next_statuses", "format_tr_number",
    "check_in", "match_chemical", "transition",
]
