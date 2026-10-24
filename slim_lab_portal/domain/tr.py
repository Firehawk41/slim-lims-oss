"""TR domain objects (docs/SLIM_DOMAIN_V2.md §4).

Pure pydantic + standard library: this module must stay liftable into slim-domain unchanged.
Rules that need reference data (IDs exist, analysis allowed for the request type, customer
locations) are NOT checked here — see the portal's submission service.
"""

import re
from datetime import date, time, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, computed_field, model_validator

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

TR_NUMBER_RE = re.compile(r"^TR\d{5,}$")
_RECEIPT_REQUIRED = frozenset(
    {TRStatus.RECEIVED, TRStatus.IN_PROGRESS, TRStatus.PARTIAL_REPORT, TRStatus.COMPLETE_REPORT, TRStatus.INVOICED}
)


def format_tr_number(n: int) -> str:
    if n < 1:
        raise ValueError("TR numbers start at 1")
    return f"TR{n:05d}"


def add_working_days(start: date, days: int) -> date:
    """Adds working days to start, skipping weekends (same rule as slim-domain v1)."""
    result = start
    for _ in range(days):
        result += timedelta(days=1)
        while result.weekday() >= 5:
            result += timedelta(days=1)
    return result


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Actor(_Frozen):
    type: ActorType
    id: str = ""
    display: str = Field(min_length=1, max_length=254)


class CustomerSnapshot(_Frozen):
    """Customer name/address as they stood when submitted. A record only — never a lookup key."""

    name: str
    address_line_1: str = ""
    address_line_2: str = ""


class TRSample(_Frozen):
    id: int = 0
    position: int = Field(ge=1)
    sample_name: str = Field(min_length=1, max_length=100)
    chemical_id: int | None = None
    chemical_name: str = Field(default="", max_length=200)
    processing_time: ProcessingTime
    requested_time: time | None = None
    additional_notes: str = Field(default="", max_length=2000)
    analysis_ids: tuple[int, ...] = ()
    additional_element_ids: tuple[int, ...] = ()
    wafer_size: WaferSize | None = None
    reporting_unit: ReportingUnit | None = None
    water_package: WaterPackage | None = None

    @model_validator(mode="after")
    def _check(self) -> "TRSample":
        if not self.sample_name.strip():
            raise ValueError("sample_name may not be blank")
        if (self.processing_time is ProcessingTime.TIME_LIMITED) != (self.requested_time is not None):
            raise ValueError("requested_time is required for Next Day Time Limited, and only allowed for it")
        if not self.analysis_ids and not self.additional_element_ids:
            raise ValueError("a sample needs at least one analysis or additional element")
        if len(set(self.analysis_ids)) != len(self.analysis_ids):
            raise ValueError("analysis_ids contains duplicates")
        if len(set(self.additional_element_ids)) != len(self.additional_element_ids):
            raise ValueError("additional_element_ids contains duplicates")
        return self


class TRReceipt(_Frozen):
    date_received: date
    received_by: str = Field(min_length=1, max_length=10)  # technician initials
    recorded_at: AwareDatetime
    recorded_by: Actor


class TRStatusEvent(_Frozen):
    from_status: TRStatus | None
    to_status: TRStatus
    at: AwareDatetime
    actor: Actor
    note: str = Field(default="", max_length=2000)


class TRSubmission(_Frozen):
    id: int = 0
    tr_number: str = ""  # assigned when stored
    source: SubmissionSource
    file_name: str = ""
    status: TRStatus
    customer_id: int
    location: str = Field(default="", max_length=20)
    request_type: RequestType
    submitted_at: AwareDatetime
    submitted_by: Actor
    customer_snapshot: CustomerSnapshot
    customer_contact: str = Field(min_length=1, max_length=200)
    customer_phone: str = Field(min_length=1, max_length=50)
    payment_method: PaymentMethod
    po_number: str = Field(default="", max_length=100)
    results_to: tuple[EmailStr, ...]
    results_cc: tuple[EmailStr, ...] = ()
    invoice_to: tuple[EmailStr, ...]
    invoice_cc: tuple[EmailStr, ...] = ()
    samples: tuple[TRSample, ...]
    expected_arrival_date: date | None = None  # customer's estimate; the receipt records the fact
    receipt: TRReceipt | None = None
    service_date_override: date | None = None
    status_history: tuple[TRStatusEvent, ...]

    @computed_field
    @property
    def effective_service_date(self) -> date | None:
        if self.receipt is None:
            return None
        if self.service_date_override is not None:
            return self.service_date_override
        return add_working_days(self.receipt.date_received, self._slowest_days())

    def _slowest_days(self) -> int:
        return max(sample.processing_time.days for sample in self.samples)

    @computed_field
    @property
    def estimated_service_date(self) -> date | None:
        """Until the samples are received: expected arrival + the slowest sample's working days."""
        if self.receipt is not None or self.expected_arrival_date is None:
            return None
        return add_working_days(self.expected_arrival_date, self._slowest_days())

    @computed_field
    @property
    def is_rush(self) -> bool:
        return any(sample.processing_time.is_rush for sample in self.samples)

    @model_validator(mode="after")
    def _check(self) -> "TRSubmission":
        errors: list[str] = []
        if self.tr_number and not TR_NUMBER_RE.match(self.tr_number):
            errors.append(f"tr_number {self.tr_number!r} is not of the form TR00001")
        if self.id and not self.tr_number:
            errors.append("a stored submission must have a tr_number")
        if not self.results_to:
            errors.append("results_to needs at least one email address")
        if not self.invoice_to:
            errors.append("invoice_to needs at least one email address")
        if (self.payment_method is PaymentMethod.PURCHASE_ORDER) != bool(self.po_number.strip()):
            errors.append("po_number is required for purchase orders, and only allowed for them")
        errors += self._check_samples()
        errors += self._check_status()
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def _check_samples(self) -> list[str]:
        errors: list[str] = []
        if not self.samples:
            return ["a submission needs at least one sample"]
        if [s.position for s in self.samples] != list(range(1, len(self.samples) + 1)):
            errors.append("sample positions must be 1..n in order")
        names = [s.sample_name.strip().casefold() for s in self.samples]
        if len(set(names)) != len(names):
            errors.append("sample names must be unique within a submission")

        allowed = set(ALLOWED_PROCESSING_TIMES[self.request_type])
        if self.source is SubmissionSource.XLSX:
            allowed.add(ProcessingTime.EXTENDED_TIME)
        wafer = self.request_type is RequestType.WAFER
        for s in self.samples:
            where = f"sample {s.position}"
            if s.processing_time not in allowed:
                errors.append(f"{where}: {s.processing_time.label} is not offered for {self.request_type.label}")
            if wafer:
                if s.wafer_size is None or s.reporting_unit is None:
                    errors.append(f"{where}: wafer samples need wafer_size and reporting_unit")
                if s.chemical_id is not None or s.chemical_name:
                    errors.append(f"{where}: wafer samples have no chemical")
            else:
                if s.wafer_size is not None or s.reporting_unit is not None:
                    errors.append(f"{where}: wafer_size and reporting_unit are for wafer samples only")
                if not s.chemical_name.strip():
                    errors.append(f"{where}: chemical_name is required")
            if s.water_package is not None and self.request_type is not RequestType.WATER:
                errors.append(f"{where}: water_package is for water samples only")
        return errors

    def _check_status(self) -> list[str]:
        errors: list[str] = []
        if not self.status_history:
            return ["status_history needs at least the initial event"]
        if self.status_history[0].from_status is not None:
            errors.append("the first status event must start from nothing")
        for prev, event in zip(self.status_history, self.status_history[1:]):
            if event.from_status is not prev.to_status:
                errors.append("status_history events must chain")
                break
        if self.status_history[-1].to_status is not self.status:
            errors.append("status must equal the latest status event")
        if self.status is TRStatus.SUBMITTED and self.receipt is not None:
            errors.append("a submission that is not yet received cannot have a receipt")
        if self.status in _RECEIPT_REQUIRED and self.receipt is None:
            errors.append(f"status {self.status.label} requires a receipt")
        if (self.status in _RECEIPT_REQUIRED and self.request_type is RequestType.CHEMICAL
                and any(s.chemical_id is None for s in self.samples)):
            errors.append("every chemical must be matched to SLIM once the request is checked in")
        return errors
