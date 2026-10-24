"""Builders for valid domain objects; tests override only what they care about."""

from datetime import datetime, time, timezone

from slim_lab_portal.domain import (
    Actor,
    ActorType,
    CustomerSnapshot,
    PaymentMethod,
    ProcessingTime,
    ReportingUnit,
    RequestType,
    SubmissionSource,
    TRSample,
    TRStatus,
    TRStatusEvent,
    TRSubmission,
    WaferSize,
)

NOW = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)
CUSTOMER_ACTOR = Actor(type=ActorType.CUSTOMER_USER, id="u-1", display="jane@acme.example")
STAFF_ACTOR = Actor(type=ActorType.STAFF, id="u-2", display="JM")


def sample(position: int = 1, **overrides) -> TRSample:
    values = dict(
        position=position,
        sample_name=f"S-{position:03d}",
        chemical_id=2,
        chemical_name="Chemical 01",
        processing_time=ProcessingTime.THREE_DAYS,
        analysis_ids=(1, 2),
    )
    values.update(overrides)
    return TRSample(**values)


def wafer_sample(position: int = 1, **overrides) -> TRSample:
    values = dict(
        chemical_id=None,
        chemical_name="",
        processing_time=ProcessingTime.NEXT_DAY_RUSH,
        wafer_size=WaferSize.MM_300,
        reporting_unit=ReportingUnit.E10_ATOMS_PER_CM2,
    )
    values.update(overrides)
    return sample(position, **values)


def submission(**overrides) -> TRSubmission:
    values = dict(
        source=SubmissionSource.PORTAL,
        status=TRStatus.SUBMITTED,
        customer_id=1,
        request_type=RequestType.CHEMICAL,
        submitted_at=NOW,
        submitted_by=CUSTOMER_ACTOR,
        customer_snapshot=CustomerSnapshot(name="A", address_line_1="1 A Street", address_line_2="Anytown, TX 75000, USA"),
        customer_contact="Jane Doe",
        customer_phone="555-1234",
        payment_method=PaymentMethod.PURCHASE_ORDER,
        po_number="PO-9876",
        results_to=("jane@acme.example",),
        invoice_to=("ap@acme.example",),
        samples=(sample(1), sample(2)),
        status_history=(TRStatusEvent(from_status=None, to_status=TRStatus.SUBMITTED, at=NOW, actor=CUSTOMER_ACTOR),),
    )
    values.update(overrides)
    return TRSubmission(**values)


TIME_LIMITED = dict(processing_time=ProcessingTime.TIME_LIMITED, requested_time=time(15, 0))
