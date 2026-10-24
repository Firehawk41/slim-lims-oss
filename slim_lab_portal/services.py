"""Submission service: turns validated input into TR domain objects, applies the rules that need
reference data (spec §4, last paragraph) and access rules, and stores through the repository.
Callers commit."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from slim_lab_portal.domain import (
    Actor,
    ActorType,
    CustomerSnapshot,
    RequestType,
    SubmissionSource,
    TransitionNotAllowed,
    TRSample,
    TRStatus,
    TRStatusEvent,
    TRSubmission,
    check_in,
    match_chemical,
    transition,
)
from slim_lab_portal.models import User
from slim_lab_portal.reference import WATER_CHEMICAL_NAME, ReferenceData
from slim_lab_portal.repositories import StaleSubmission, SubmissionPage, TRSubmissionRepository
from slim_lab_portal.schemas import CheckInIn, ChemicalMatchIn, SampleIn, StatusChangeIn, SubmissionIn


@dataclass(frozen=True)
class FieldError:
    loc: tuple[str | int, ...]
    msg: str


class SubmissionRejected(Exception):
    """The input is well-formed but breaks a business rule. Maps to 422."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("; ".join(e.msg for e in errors))
        self.errors = errors


class NotFound(Exception):
    """Unknown TR number, or one the user may not see. Maps to 404."""


class Forbidden(Exception):
    """Maps to 403."""


class Conflict(Exception):
    """The status change is not allowed now, or someone else changed it first. Maps to 409."""


def actor_for(user: User) -> Actor:
    if user.is_service:
        return Actor(type=ActorType.SYSTEM, id=user.id, display=user.name)
    if user.is_staff:
        return Actor(type=ActorType.STAFF, id=str(user.id), display=user.initials)
    return Actor(type=ActorType.CUSTOMER_USER, id=str(user.id), display=user.email)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SubmissionService:
    def __init__(
        self,
        repo: TRSubmissionRepository,
        reference: ReferenceData,
        clock: Callable[[], datetime] = _utcnow,
        lab_timezone: str = "America/Chicago",
    ) -> None:
        self.repo = repo
        self.reference = reference
        self.clock = clock
        self.lab_tz = ZoneInfo(lab_timezone)

    def lab_today(self) -> date:
        return self.clock().astimezone(self.lab_tz).date()

    # ------------------------------------------------------------ submit

    def submit(self, data: SubmissionIn, user: User) -> TRSubmission:
        customer_id = self._customer_id_for(data, user)
        errors: list[FieldError] = []

        customer = self.reference.customer(customer_id)
        if customer is None:
            raise SubmissionRejected([FieldError(("customer_id",), f"unknown customer {customer_id}")])
        if customer.is_group:
            errors.append(FieldError(("customer_id",), "a customer group cannot submit; choose one of its sites"))

        if data.expected_arrival_date is not None and data.expected_arrival_date < self.lab_today():
            errors.append(FieldError(("expected_arrival_date",), "expected arrival cannot be in the past"))

        locations = self.reference.locations(customer_id)
        if locations and data.location not in locations:
            errors.append(FieldError(("location",), f"choose one of: {', '.join(locations)}"))
        if not locations and data.location:
            errors.append(FieldError(("location",), "this customer has no locations"))

        samples = []
        for i, sample_in in enumerate(data.samples):
            sample_errors, chemical_id, chemical_name = self._check_sample(sample_in, data.request_type)
            errors += [FieldError(("samples", i, *e.loc), e.msg) for e in sample_errors]
            samples.append((i, sample_in, chemical_id, chemical_name))
        if errors:
            raise SubmissionRejected(errors)

        now = self.clock()
        actor = actor_for(user)
        try:
            submission = TRSubmission(
                source=SubmissionSource.PORTAL,
                status=TRStatus.SUBMITTED,
                customer_id=customer_id,
                location=data.location,
                request_type=data.request_type,
                submitted_at=now,
                submitted_by=actor,
                customer_snapshot=CustomerSnapshot(
                    name=customer.name,
                    address_line_1=customer.address_line_1,
                    address_line_2=customer.address_line_2,
                ),
                customer_contact=data.customer_contact,
                customer_phone=data.customer_phone,
                payment_method=data.payment_method,
                po_number=data.po_number,
                expected_arrival_date=data.expected_arrival_date,
                results_to=tuple(data.results_to),
                results_cc=tuple(data.results_cc),
                invoice_to=tuple(data.invoice_to),
                invoice_cc=tuple(data.invoice_cc),
                samples=tuple(
                    _to_sample(i + 1, sample_in, chemical_id, chemical_name)
                    for i, sample_in, chemical_id, chemical_name in samples
                ),
                status_history=(TRStatusEvent(from_status=None, to_status=TRStatus.SUBMITTED, at=now, actor=actor),),
            )
        except ValidationError as e:
            raise SubmissionRejected(_field_errors(e)) from e
        return self.repo.add(submission)

    def _customer_id_for(self, data: SubmissionIn, user: User) -> int:
        if user.is_staff:
            if data.customer_id is None:
                raise SubmissionRejected([FieldError(("customer_id",), "staff must choose the customer")])
            return data.customer_id
        if data.customer_id is not None and data.customer_id != user.customer_id:
            raise Forbidden("customers can only submit for their own company")
        return user.customer_id

    def _check_sample(self, s: SampleIn, request_type: RequestType) -> tuple[list[FieldError], int | None, str]:
        errors: list[FieldError] = []
        chemical_id, chemical_name = None, ""

        if request_type is RequestType.CHEMICAL:
            # Free text as the customer writes it. Linked to a SLIM chemical only on an exact
            # (case-insensitive) name match; otherwise the lab matches or creates it later.
            chemical_name = s.chemical_name
            if not chemical_name:
                errors.append(FieldError(("chemical_name",), "enter the chemical or matrix"))
            else:
                match = self.reference.chemical_by_name(chemical_name)
                chemical_id = match.id if match else None
        elif request_type is RequestType.WATER:
            water = self.reference.chemical_by_name(WATER_CHEMICAL_NAME)
            if water is None:
                errors.append(FieldError(("chemical_id",), "the lab's Water chemical is not set up; contact the lab"))
            else:
                chemical_id, chemical_name = water.id, water.name

        if request_type is RequestType.WAFER and s.chemical_name:
            errors.append(FieldError(("chemical_name",), "wafer samples have no chemical"))
        for j, analysis_id in enumerate(s.analysis_ids):
            analysis = self.reference.analysis(analysis_id)
            if analysis is None or not analysis.portal_selectable:
                errors.append(FieldError(("analysis_ids", j), f"unknown analysis {analysis_id}"))
            elif request_type not in analysis.request_types:
                errors.append(FieldError(("analysis_ids", j),
                                         f"{analysis.name} is not offered for {request_type.label} samples"))
        for j, element_id in enumerate(s.additional_element_ids):
            if self.reference.element(element_id) is None:
                errors.append(FieldError(("additional_element_ids", j), f"unknown element {element_id}"))
        return errors, chemical_id, chemical_name

    # ------------------------------------------------------------ read

    def get(self, tr_number: str, user: User) -> TRSubmission:
        submission = self.repo.get_by_tr_number(tr_number)
        if submission is None or not _can_see(user, submission):
            raise NotFound(tr_number)
        return submission

    def list(self, user: User, **filters) -> SubmissionPage:
        if not user.is_staff:
            filters["customer_id"] = user.customer_id
        return self.repo.list(**filters)

    # ------------------------------------------------------------ status

    def check_in(self, tr_number: str, data: CheckInIn, user: User) -> TRSubmission:
        """Staff accept a submitted request into the LIMS: receipt + chemical matching, one step."""
        if not user.is_staff:
            raise Forbidden("only lab staff can check in requests")
        before = self.get(tr_number, user)
        unknown = [cid for cid in data.chemicals.values() if self.reference.chemical(cid) is None]
        if unknown:
            raise SubmissionRejected([FieldError(("chemicals",), f"unknown chemical {cid}") for cid in unknown])
        try:
            after = check_in(
                before,
                actor=actor_for(user),
                at=self.clock(),
                date_received=data.date_received,
                received_by=data.received_by or user.initials,
                chemical_matches=data.chemicals,
                note=data.note,
                today=self.lab_today(),
            )
            return self.repo.record_transition(before, after)
        except TransitionNotAllowed as e:
            raise Conflict(str(e)) from e
        except ValueError as e:
            raise SubmissionRejected([FieldError(("chemicals",), str(e))]) from e
        except StaleSubmission as e:
            raise Conflict(f"{tr_number} was changed by someone else; reload and try again") from e

    def change_status(self, tr_number: str, data: StatusChangeIn, user: User) -> TRSubmission:
        if data.to_status is TRStatus.RECEIVED:
            raise Conflict("requests are received by checking them in (POST .../check-in)")
        before = self.get(tr_number, user)
        received_by = data.received_by
        if data.to_status is TRStatus.RECEIVED and not received_by and user.is_staff:
            received_by = user.initials
        try:
            after = transition(
                before,
                data.to_status,
                actor=actor_for(user),
                at=self.clock(),
                note=data.note,
                date_received=data.date_received,
                received_by=received_by,
                today=self.lab_today(),
            )
            return self.repo.record_transition(before, after)
        except TransitionNotAllowed as e:
            raise Conflict(str(e)) from e
        except StaleSubmission as e:
            raise Conflict(f"{tr_number} was changed by someone else; reload and try again") from e


    # ------------------------------------------------------------ chemical matching (staff)

    def match_chemical(self, tr_number: str, position: int, data: ChemicalMatchIn, user: User) -> TRSubmission:
        """Links one Chemical sample's free-text chemical to a SLIM chemical (staff only)."""
        if not user.is_staff:
            raise Forbidden("only lab staff can match chemicals")
        submission = self.get(tr_number, user)
        if self.reference.chemical(data.chemical_id) is None:
            raise SubmissionRejected([FieldError(("chemical_id",), f"unknown chemical {data.chemical_id}")])
        try:
            match_chemical(submission, position, data.chemical_id)  # validates the change
        except ValueError as e:
            raise SubmissionRejected([FieldError(("chemical_id",), str(e))]) from e
        return self.repo.record_chemical_match(submission.id, position, data.chemical_id)


def _can_see(user: User, submission: TRSubmission) -> bool:
    return user.is_staff or submission.customer_id == user.customer_id


def _to_sample(position: int, s: SampleIn, chemical_id: int | None, chemical_name: str) -> TRSample:
    return TRSample(
        position=position,
        sample_name=s.sample_name,
        chemical_id=chemical_id,
        chemical_name=chemical_name,
        processing_time=s.processing_time,
        requested_time=s.requested_time,
        additional_notes=s.additional_notes,
        analysis_ids=tuple(s.analysis_ids),
        additional_element_ids=tuple(s.additional_element_ids),
        wafer_size=s.wafer_size,
        reporting_unit=s.reporting_unit,
    )


def _field_errors(e: ValidationError) -> list[FieldError]:
    """Domain validation errors → field errors. Model-level rules carry several '; '-joined messages."""
    errors = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        loc = tuple(err["loc"])
        errors += [FieldError(loc, part) for part in msg.split("; ")]
    return errors
