"""Input models: what the portal accepts over HTTP. They validate shape and types only; the
submission service turns them into domain objects and applies the business rules."""

from datetime import date, time
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from slim_lab_portal.domain import (
    PaymentMethod,
    ProcessingTime,
    ReportingUnit,
    RequestType,
    TRStatus,
    WaferSize,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True)]
EmailList = Annotated[list[EmailStr], Field(max_length=20)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SampleIn(_Input):
    sample_name: Annotated[Text, StringConstraints(min_length=1, max_length=100)]
    # Chemical: the customer's own words, free text (matched to SLIM later). Water: set by the
    # portal. Wafer: none.
    chemical_name: Annotated[Text, StringConstraints(max_length=200)] = ""
    processing_time: ProcessingTime
    requested_time: time | None = None  # "report by" time, Next Day Time Limited only
    additional_notes: Annotated[Text, StringConstraints(max_length=2000)] = ""
    analysis_ids: Annotated[list[int], Field(max_length=100)] = []
    additional_element_ids: Annotated[list[int], Field(max_length=120)] = []
    wafer_size: WaferSize | None = None
    reporting_unit: ReportingUnit | None = None


class SubmissionIn(_Input):
    customer_id: int | None = None  # staff submitting on a customer's behalf; customers omit it
    request_type: RequestType
    location: Annotated[Text, StringConstraints(max_length=20)] = ""
    expected_arrival_date: date | None = None  # optional; today or later (lab's local date)
    customer_contact: Annotated[Text, StringConstraints(min_length=1, max_length=200)]
    customer_phone: Annotated[Text, StringConstraints(min_length=1, max_length=50)]
    payment_method: PaymentMethod
    po_number: Annotated[Text, StringConstraints(max_length=100)] = ""
    results_to: Annotated[EmailList, Field(min_length=1)]
    results_cc: EmailList = []
    invoice_to: Annotated[EmailList, Field(min_length=1)]
    invoice_cc: EmailList = []
    samples: Annotated[list[SampleIn], Field(min_length=1, max_length=200)]


class CheckInIn(_Input):
    """Staff accept a submitted request into the LIMS (receipt + chemical matching)."""

    date_received: date
    received_by: Annotated[Text, StringConstraints(max_length=10)] | None = None  # defaults to the user's initials
    chemicals: dict[int, int] = {}  # sample position -> SLIM chemical ID
    note: Annotated[Text, StringConstraints(max_length=2000)] = ""


class ChemicalMatchIn(_Input):
    chemical_id: int  # SLIM chemicals."ID"


class StatusChangeIn(_Input):
    to_status: TRStatus
    note: Annotated[Text, StringConstraints(max_length=2000)] = ""
    date_received: date | None = None  # receiving only
    received_by: Annotated[Text, StringConstraints(max_length=10)] | None = None  # defaults to the staff user's initials


# ---------------------------------------------------------------- results (slim-data, D-70)

class SignOffIn(_Input):
    """Reviewing a batch or releasing a sample + analysis. `solo`: a chemist alone signing off their
    own results after the policy's delay, with `note` as the reason (slim-data D-68)."""
    note: Annotated[Text, StringConstraints(max_length=2000)] = ""
    solo: bool = False


class ResultActionIn(_Input):
    """Accept, rerun, exception, reject or correct one result. Accept and exception need a reason
    category (printed on the report as a standard qualifier, D-11); exception, reject and correct
    need a note (the reason)."""
    reason_category: Annotated[Text, StringConstraints(max_length=100)] = ""
    note: Annotated[Text, StringConstraints(max_length=2000)] = ""


class ReEvaluateIn(_Input):
    reason: Annotated[Text, StringConstraints(max_length=500)] = ""
