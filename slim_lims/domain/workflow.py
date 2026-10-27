"""Status transitions (docs/SLIM_DOMAIN_V2.md §3, TRStatus). Pure functions over frozen objects."""

from datetime import date, datetime

from slim_lims.domain.enums import ActorType, RequestType, TRStatus
from slim_lims.domain.tr import Actor, TRReceipt, TRStatusEvent, TRSubmission

ALLOWED_TRANSITIONS: dict[TRStatus, frozenset[TRStatus]] = {
    TRStatus.SUBMITTED: frozenset({TRStatus.RECEIVED, TRStatus.CANCELLED}),
    TRStatus.RECEIVED: frozenset({TRStatus.IN_PROGRESS, TRStatus.CANCELLED}),
    TRStatus.IN_PROGRESS: frozenset({TRStatus.PARTIAL_REPORT, TRStatus.COMPLETE_REPORT, TRStatus.CANCELLED}),
    TRStatus.PARTIAL_REPORT: frozenset({TRStatus.PARTIAL_REPORT, TRStatus.COMPLETE_REPORT}),
    TRStatus.COMPLETE_REPORT: frozenset({TRStatus.INVOICED}),
    TRStatus.INVOICED: frozenset(),
    TRStatus.CANCELLED: frozenset(),
}


# What the LIMS (ActorType.SYSTEM, spec §12) may set: it moves work forward and issues reports.
# Receiving (check-in), cancelling and invoicing stay with lab staff.
SYSTEM_STATUSES = frozenset({TRStatus.IN_PROGRESS, TRStatus.PARTIAL_REPORT, TRStatus.COMPLETE_REPORT})


class TransitionNotAllowed(ValueError):
    pass


def allowed_next_statuses(current: TRStatus, actor_type: ActorType) -> tuple[TRStatus, ...]:
    if actor_type is ActorType.CUSTOMER_USER:
        return (TRStatus.CANCELLED,) if current is TRStatus.SUBMITTED else ()
    if actor_type is ActorType.SYSTEM:
        return tuple(sorted(ALLOWED_TRANSITIONS[current] & SYSTEM_STATUSES))
    return tuple(sorted(ALLOWED_TRANSITIONS[current]))


def transition(
    submission: TRSubmission,
    to_status: TRStatus,
    *,
    actor: Actor,
    at: datetime,
    note: str = "",
    date_received: date | None = None,
    received_by: str | None = None,
    today: date | None = None,
) -> TRSubmission:
    """Returns a new TRSubmission with the status changed and the event appended.

    Moving to RECEIVED records the receipt and needs `date_received` and `received_by`
    (technician initials). Pass `today` (the lab's local date) to refuse a receipt dated in
    the future — samples are received when they are physically in hand.
    """
    current = submission.status
    if to_status not in allowed_next_statuses(current, actor.type):
        raise TransitionNotAllowed(
            f"cannot move {submission.tr_number or 'submission'} from {current.label} to {to_status.label}"
        )

    receipt = submission.receipt
    if to_status is TRStatus.RECEIVED:
        unmatched = [
            str(s.position)
            for s in submission.samples
            if submission.request_type is RequestType.CHEMICAL and s.chemical_id is None
        ]
        if unmatched:
            raise TransitionNotAllowed(
                f"match the chemical for sample(s) {', '.join(unmatched)} before checking in"
            )
        if date_received is None or not received_by:
            raise TransitionNotAllowed("receiving samples needs date_received and received_by")
        if today is not None and date_received > today:
            raise TransitionNotAllowed(
                f"date_received {date_received} is in the future (lab date is {today})"
            )
        receipt = TRReceipt(
            date_received=date_received, received_by=received_by, recorded_at=at, recorded_by=actor
        )
    elif date_received is not None or received_by is not None:
        raise TransitionNotAllowed("date_received and received_by are only for receiving samples")

    event = TRStatusEvent(from_status=current, to_status=to_status, at=at, actor=actor, note=note)
    return replace(
        submission, status=to_status, receipt=receipt, status_history=(*submission.status_history, event)
    )


def replace(model, **changes):
    """Like model_copy(update=...), but re-validates, so the invariants still hold."""
    return type(model)(**{**dict(model), **changes})


def match_chemical(submission: TRSubmission, position: int, chemical_id: int) -> TRSubmission:
    """Links a Chemical sample's free-text chemical to a SLIM chemical. The customer's text is kept."""
    if submission.request_type is not RequestType.CHEMICAL:
        raise ValueError("only Chemical requests have chemicals to match")
    if not any(s.position == position for s in submission.samples):
        raise ValueError(f"sample {position} does not exist")
    samples = tuple(
        replace(s, chemical_id=chemical_id) if s.position == position else s for s in submission.samples
    )
    return replace(submission, samples=samples)


def check_in(
    submission: TRSubmission,
    *,
    actor: Actor,
    at: datetime,
    date_received: date,
    received_by: str,
    chemical_matches: dict[int, int] | None = None,
    note: str = "",
    today: date | None = None,
) -> TRSubmission:
    """Accepts a submitted request into the LIMS: applies the staff's chemical matches
    ({sample position: SLIM chemical ID}) and records the receipt, in one step. Fails unless
    every Chemical sample ends up matched."""
    for position, chemical_id in (chemical_matches or {}).items():
        submission = match_chemical(submission, position, chemical_id)
    return transition(
        submission,
        TRStatus.RECEIVED,
        actor=actor,
        at=at,
        note=note,
        date_received=date_received,
        received_by=received_by,
        today=today,
    )
