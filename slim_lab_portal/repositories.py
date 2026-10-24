"""TRSubmissionRepository — the only code that converts TR domain objects to and from rows."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from slim_lab_portal.domain import (
    Actor,
    ActorType,
    CustomerSnapshot,
    PaymentMethod,
    ProcessingTime,
    ReportingUnit,
    RequestType,
    SubmissionSource,
    TRReceipt,
    TRSample,
    TRStatus,
    TRStatusEvent,
    TRSubmission,
    WaferSize,
    WaterPackage,
    format_tr_number,
)
from slim_lab_portal.models import (
    TRNumberCounter,
    TRSampleAdditionalElementRow,
    TRSampleAnalysisRow,
    TRSampleRow,
    TRStatusEventRow,
    TRSubmissionEmailRow,
    TRSubmissionRow,
)

EMAIL_KINDS = ("results_to", "results_cc", "invoice_to", "invoice_cc")


class StaleSubmission(Exception):
    """The submission's status changed since it was loaded (someone else updated it)."""


@dataclass(frozen=True)
class SubmissionPage:
    items: list[TRSubmission]
    total: int
    page: int
    per_page: int


class TRSubmissionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------ writes

    def add(self, submission: TRSubmission) -> TRSubmission:
        """Stores a new submission, assigning its TR number. Flushes; the caller commits.

        The counter UPDATE row-locks until commit, and rolls back with a failed insert, so
        TR numbers have no gaps.
        """
        if submission.id or submission.tr_number:
            raise ValueError("submission is already stored")
        tr_number = self._next_tr_number()
        row = _to_row(submission, tr_number)
        self.session.add(row)
        self.session.flush()
        return self._load_row(row.id)

    def record_transition(self, before: TRSubmission, after: TRSubmission) -> TRSubmission:
        """Persists a workflow.transition() / check_in() result (status, receipt, chemical matches,
        new events). Raises StaleSubmission if the stored status
        is no longer `before.status` (a concurrent update won)."""
        new_events = after.status_history[len(before.status_history):]
        if before.id != after.id or not new_events:
            raise ValueError("`after` must be a transition of `before`")
        values = {"status": int(after.status)}
        if after.receipt is not None and before.receipt is None:
            values.update(_receipt_columns(after.receipt))
        result = self.session.execute(
            update(TRSubmissionRow)
            .where(TRSubmissionRow.id == before.id, TRSubmissionRow.status == int(before.status))
            .values(**values)
        )
        if result.rowcount != 1:
            raise StaleSubmission(before.tr_number)
        old_matches = {s.position: s.chemical_id for s in before.samples}
        for sample in after.samples:
            if sample.chemical_id != old_matches.get(sample.position):
                self.session.execute(
                    update(TRSampleRow)
                    .where(TRSampleRow.submission_id == before.id, TRSampleRow.position == sample.position)
                    .values(chemical_id=sample.chemical_id)
                )
        for event in new_events:
            self.session.add(_event_row(before.id, event))
        self.session.flush()
        self.session.expire_all()
        return self._load_row(before.id)

    def record_chemical_match(self, submission_id: int, position: int, chemical_id: int) -> TRSubmission:
        """Persists a workflow.match_chemical() result for one sample. The caller commits."""
        result = self.session.execute(
            update(TRSampleRow)
            .where(TRSampleRow.submission_id == submission_id, TRSampleRow.position == position)
            .values(chemical_id=chemical_id)
        )
        if result.rowcount != 1:
            raise ValueError(f"sample {position} not found")
        self.session.flush()
        self.session.expire_all()
        return self._load_row(submission_id)

    def _next_tr_number(self) -> str:
        value = self.session.execute(
            update(TRNumberCounter)
            .where(TRNumberCounter.id == 1)
            .values(last_value=TRNumberCounter.last_value + 1)
            .returning(TRNumberCounter.last_value)
        ).scalar_one()
        return format_tr_number(value)

    # ------------------------------------------------------------ reads

    def get(self, submission_id: int) -> TRSubmission | None:
        row = self.session.scalars(_select_full().where(TRSubmissionRow.id == submission_id)).first()
        return _from_row(row) if row else None

    def get_by_tr_number(self, tr_number: str) -> TRSubmission | None:
        row = self.session.scalars(_select_full().where(TRSubmissionRow.tr_number == tr_number.upper())).first()
        return _from_row(row) if row else None

    def list(
        self,
        *,
        customer_id: int | None = None,
        status: TRStatus | None = None,
        request_type: RequestType | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> SubmissionPage:
        filters = []
        if customer_id is not None:
            filters.append(TRSubmissionRow.customer_id == customer_id)
        if status is not None:
            filters.append(TRSubmissionRow.status == int(status))
        if request_type is not None:
            filters.append(TRSubmissionRow.request_type == int(request_type))
        total = self.session.scalar(select(func.count()).select_from(TRSubmissionRow).where(*filters))
        rows = self.session.scalars(
            _select_full().where(*filters)
            .order_by(TRSubmissionRow.submitted_at.desc(), TRSubmissionRow.id.desc())
            .limit(per_page).offset((page - 1) * per_page)
        ).all()
        return SubmissionPage([_from_row(r) for r in rows], total or 0, page, per_page)

    def _load_row(self, submission_id: int) -> TRSubmission:
        submission = self.get(submission_id)
        assert submission is not None
        return submission


# ---------------------------------------------------------------- mapping

def _select_full():
    return select(TRSubmissionRow).options(
        selectinload(TRSubmissionRow.samples).selectinload(TRSampleRow.analyses),
        selectinload(TRSubmissionRow.samples).selectinload(TRSampleRow.additional_elements),
        selectinload(TRSubmissionRow.emails),
        selectinload(TRSubmissionRow.status_events),
    )


def _utc(value: datetime) -> datetime:
    """SQLite drops the time zone; everything is stored in UTC, so put it back."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _to_row(s: TRSubmission, tr_number: str) -> TRSubmissionRow:
    row = TRSubmissionRow(
        tr_number=tr_number,
        source=int(s.source),
        file_name=s.file_name,
        status=int(s.status),
        customer_id=s.customer_id,
        location=s.location,
        request_type=int(s.request_type),
        submitted_at=_utc(s.submitted_at),
        submitted_by_type=int(s.submitted_by.type),
        submitted_by_id=s.submitted_by.id,
        submitted_by_display=s.submitted_by.display,
        snapshot_name=s.customer_snapshot.name,
        snapshot_address_1=s.customer_snapshot.address_line_1,
        snapshot_address_2=s.customer_snapshot.address_line_2,
        customer_contact=s.customer_contact,
        customer_phone=s.customer_phone,
        payment_method=int(s.payment_method),
        po_number=s.po_number,
        expected_arrival_date=s.expected_arrival_date,
        service_date_override=s.service_date_override,
        **(_receipt_columns(s.receipt) if s.receipt else {}),
    )
    row.samples = [_sample_row(sample) for sample in s.samples]
    row.emails = [
        TRSubmissionEmailRow(kind=kind, position=i, email=email)
        for kind in EMAIL_KINDS
        for i, email in enumerate(getattr(s, kind), start=1)
    ]
    row.status_events = [_event_row(None, event) for event in s.status_history]
    return row


def _receipt_columns(receipt: TRReceipt) -> dict:
    return {
        "date_received": receipt.date_received,
        "received_by": receipt.received_by,
        "receipt_recorded_at": _utc(receipt.recorded_at),
        "receipt_recorded_by_type": int(receipt.recorded_by.type),
        "receipt_recorded_by_id": receipt.recorded_by.id,
        "receipt_recorded_by_display": receipt.recorded_by.display,
    }


def _sample_row(s: TRSample) -> TRSampleRow:
    return TRSampleRow(
        position=s.position,
        sample_name=s.sample_name,
        chemical_id=s.chemical_id,
        chemical_name=s.chemical_name,
        processing_time=int(s.processing_time),
        requested_time=s.requested_time,
        additional_notes=s.additional_notes,
        wafer_size=_int_or_none(s.wafer_size),
        reporting_unit=_int_or_none(s.reporting_unit),
        water_package=_int_or_none(s.water_package),
        analyses=[TRSampleAnalysisRow(analysis_id=a, position=i) for i, a in enumerate(s.analysis_ids, start=1)],
        additional_elements=[
            TRSampleAdditionalElementRow(element_id=e, position=i)
            for i, e in enumerate(s.additional_element_ids, start=1)
        ],
    )


def _event_row(submission_id: int | None, e: TRStatusEvent) -> TRStatusEventRow:
    row = TRStatusEventRow(
        from_status=_int_or_none(e.from_status),
        to_status=int(e.to_status),
        at=_utc(e.at),
        actor_type=int(e.actor.type),
        actor_id=e.actor.id,
        actor_display=e.actor.display,
        note=e.note,
    )
    if submission_id is not None:
        row.submission_id = submission_id
    return row


def _int_or_none(value) -> int | None:
    return None if value is None else int(value)


def _enum_or_none(enum, value):
    return None if value is None else enum(value)


def _from_row(row: TRSubmissionRow) -> TRSubmission:
    emails = {kind: [] for kind in EMAIL_KINDS}
    for e in row.emails:
        emails[e.kind].append((e.position, e.email))
    receipt = None
    if row.date_received is not None:
        receipt = TRReceipt(
            date_received=row.date_received,
            received_by=row.received_by,
            recorded_at=_utc(row.receipt_recorded_at),
            recorded_by=Actor(
                type=ActorType(row.receipt_recorded_by_type),
                id=row.receipt_recorded_by_id or "",
                display=row.receipt_recorded_by_display,
            ),
        )
    return TRSubmission(
        id=row.id,
        tr_number=row.tr_number,
        source=SubmissionSource(row.source),
        file_name=row.file_name,
        status=TRStatus(row.status),
        customer_id=row.customer_id,
        location=row.location,
        request_type=RequestType(row.request_type),
        submitted_at=_utc(row.submitted_at),
        submitted_by=Actor(type=ActorType(row.submitted_by_type), id=row.submitted_by_id,
                           display=row.submitted_by_display),
        customer_snapshot=CustomerSnapshot(name=row.snapshot_name, address_line_1=row.snapshot_address_1,
                                           address_line_2=row.snapshot_address_2),
        customer_contact=row.customer_contact,
        customer_phone=row.customer_phone,
        payment_method=PaymentMethod(row.payment_method),
        po_number=row.po_number,
        **{kind: tuple(email for _, email in sorted(values)) for kind, values in emails.items()},
        samples=tuple(_sample_from_row(s) for s in row.samples),
        expected_arrival_date=row.expected_arrival_date,
        receipt=receipt,
        service_date_override=row.service_date_override,
        status_history=tuple(
            TRStatusEvent(
                from_status=_enum_or_none(TRStatus, e.from_status),
                to_status=TRStatus(e.to_status),
                at=_utc(e.at),
                actor=Actor(type=ActorType(e.actor_type), id=e.actor_id, display=e.actor_display),
                note=e.note,
            )
            for e in row.status_events
        ),
    )


def _sample_from_row(row: TRSampleRow) -> TRSample:
    return TRSample(
        id=row.id,
        position=row.position,
        sample_name=row.sample_name,
        chemical_id=row.chemical_id,
        chemical_name=row.chemical_name,
        processing_time=ProcessingTime(row.processing_time),
        requested_time=row.requested_time,
        additional_notes=row.additional_notes,
        analysis_ids=tuple(a.analysis_id for a in row.analyses),
        additional_element_ids=tuple(e.element_id for e in row.additional_elements),
        wafer_size=_enum_or_none(WaferSize, row.wafer_size),
        reporting_unit=_enum_or_none(ReportingUnit, row.reporting_unit),
        water_package=_enum_or_none(WaterPackage, row.water_package),
    )
