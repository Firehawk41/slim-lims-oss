from datetime import date

import pytest

from slim_lab_portal.domain import (
    ALLOWED_TRANSITIONS,
    ActorType,
    TransitionNotAllowed,
    TRStatus,
    allowed_next_statuses,
    transition,
)
from tests.factories import CUSTOMER_ACTOR, NOW, STAFF_ACTOR, submission


def _receive(sub):
    return transition(sub, TRStatus.RECEIVED, actor=STAFF_ACTOR, at=NOW, date_received=date(2026, 10, 9), received_by="JM")


def test_full_happy_path():
    sub = _receive(submission())
    assert sub.receipt.received_by == "JM"
    for status in (TRStatus.IN_PROGRESS, TRStatus.PARTIAL_REPORT, TRStatus.PARTIAL_REPORT,
                   TRStatus.COMPLETE_REPORT, TRStatus.INVOICED):
        sub = transition(sub, status, actor=STAFF_ACTOR, at=NOW)
    assert sub.status is TRStatus.INVOICED
    assert [e.to_status for e in sub.status_history][-3:] == [TRStatus.PARTIAL_REPORT, TRStatus.COMPLETE_REPORT,
                                                              TRStatus.INVOICED]
    assert len(sub.status_history) == 7


def test_original_is_unchanged():
    sub = submission()
    _receive(sub)
    assert sub.status is TRStatus.SUBMITTED


def test_receiving_requires_date_and_initials():
    with pytest.raises(TransitionNotAllowed, match="date_received"):
        transition(submission(), TRStatus.RECEIVED, actor=STAFF_ACTOR, at=NOW)


def test_receipt_details_only_when_receiving():
    sub = _receive(submission())
    with pytest.raises(TransitionNotAllowed):
        transition(sub, TRStatus.IN_PROGRESS, actor=STAFF_ACTOR, at=NOW, date_received=date(2026, 10, 9))


@pytest.mark.parametrize("terminal", [TRStatus.INVOICED, TRStatus.CANCELLED])
def test_terminal_statuses_go_nowhere(terminal):
    assert ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_cannot_skip_receipt():
    with pytest.raises(TransitionNotAllowed):
        transition(submission(), TRStatus.IN_PROGRESS, actor=STAFF_ACTOR, at=NOW)


def test_customer_can_cancel_only_while_submitted():
    cancelled = transition(submission(), TRStatus.CANCELLED, actor=CUSTOMER_ACTOR, at=NOW, note="ordered twice")
    assert cancelled.status is TRStatus.CANCELLED
    assert cancelled.receipt is None
    with pytest.raises(TransitionNotAllowed):
        transition(_receive(submission()), TRStatus.CANCELLED, actor=CUSTOMER_ACTOR, at=NOW)


def test_customer_cannot_do_staff_transitions():
    with pytest.raises(TransitionNotAllowed):
        transition(submission(), TRStatus.RECEIVED, actor=CUSTOMER_ACTOR, at=NOW,
                   date_received=date(2026, 10, 9), received_by="JD")


def test_staff_cancel_after_receipt_keeps_receipt():
    sub = transition(_receive(submission()), TRStatus.CANCELLED, actor=STAFF_ACTOR, at=NOW)
    assert sub.receipt is not None


def test_allowed_next_statuses():
    assert allowed_next_statuses(TRStatus.SUBMITTED, ActorType.CUSTOMER_USER) == (TRStatus.CANCELLED,)
    assert allowed_next_statuses(TRStatus.RECEIVED, ActorType.CUSTOMER_USER) == ()
    assert allowed_next_statuses(TRStatus.SUBMITTED, ActorType.STAFF) == (TRStatus.RECEIVED, TRStatus.CANCELLED)


def test_receipt_cannot_be_dated_in_the_future():
    with pytest.raises(TransitionNotAllowed, match="future"):
        transition(submission(), TRStatus.RECEIVED, actor=STAFF_ACTOR, at=NOW,
                   date_received=date(2026, 10, 9), received_by="JM", today=date(2026, 10, 8))
    ok = transition(submission(), TRStatus.RECEIVED, actor=STAFF_ACTOR, at=NOW,
                    date_received=date(2026, 10, 8), received_by="JM", today=date(2026, 10, 8))
    assert ok.receipt.date_received == date(2026, 10, 8)
