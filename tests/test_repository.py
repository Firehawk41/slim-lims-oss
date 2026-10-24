from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from slim_lab_portal.db import make_session_factory
from slim_lab_portal.domain import RequestType, TRStatus, transition
from slim_lab_portal.models import TRSubmissionRow
from slim_lab_portal.repositories import StaleSubmission, TRSubmissionRepository
from tests.conftest import postgres_only
from tests.factories import NOW, STAFF_ACTOR, TIME_LIMITED, sample, submission, wafer_sample


@pytest.fixture
def repo(db):
    return TRSubmissionRepository(db)


def test_add_assigns_sequential_tr_numbers(repo, db):
    first = repo.add(submission())
    second = repo.add(submission())
    db.commit()
    assert (first.tr_number, second.tr_number) == ("TR00001", "TR00002")
    assert first.id and second.id


def test_round_trip_is_lossless(repo, db):
    original = submission(
        results_cc=("cc1@acme.example", "cc2@acme.example"),
        invoice_cc=("billing@acme.example",),
        location="SX5N",
        samples=(
            sample(1, analysis_ids=(7, 3, 5), additional_element_ids=(26, 3), additional_notes="handle cold"),
            sample(2, **TIME_LIMITED),
        ),
    )
    stored = repo.add(original)
    db.commit()
    db.expire_all()
    loaded = repo.get_by_tr_number("tr00001")
    assert loaded == stored
    expected = original.model_dump(exclude={"id", "tr_number", "samples"})
    assert loaded.model_dump(exclude={"id", "tr_number", "samples"}) == expected
    assert loaded.samples[0].analysis_ids == (7, 3, 5)  # customer's order kept
    assert loaded.samples[1].requested_time == TIME_LIMITED["requested_time"]
    assert loaded.submitted_at.tzinfo is not None


def test_wafer_round_trip(repo, db):
    stored = repo.add(submission(request_type=RequestType.WAFER, samples=(wafer_sample(1),)))
    db.commit()
    assert repo.get(stored.id).samples[0].wafer_size == wafer_sample(1).wafer_size


def test_failed_insert_does_not_burn_a_number(repo, db):
    repo.add(submission())
    db.commit()
    repo.add(submission())
    db.rollback()  # e.g. the request failed after the insert
    assert repo.add(submission()).tr_number == "TR00002"


def test_tr_numbers_are_unique_in_the_database(repo, db):
    repo.add(submission())
    db.commit()
    db.add(TRSubmissionRow(**{c.name: getattr(db.get(TRSubmissionRow, 1), c.name)
                              for c in TRSubmissionRow.__table__.columns if c.name != "id"}))
    with pytest.raises(IntegrityError):
        db.flush()


def test_cannot_add_twice(repo, db):
    stored = repo.add(submission())
    with pytest.raises(ValueError):
        repo.add(stored)


def test_record_transition_with_receipt(repo, db):
    stored = repo.add(submission())
    db.commit()
    received = transition(stored, TRStatus.RECEIVED, actor=STAFF_ACTOR, at=NOW,
                          date_received=date(2026, 10, 9), received_by="JM", note="2 bottles")
    saved = repo.record_transition(stored, received)
    db.commit()
    db.expire_all()
    loaded = repo.get(stored.id)
    assert loaded == saved
    assert loaded.status is TRStatus.RECEIVED
    assert loaded.receipt.received_by == "JM"
    assert loaded.status_history[-1].note == "2 bottles"


def test_stale_transition_is_refused(repo, db):
    stored = repo.add(submission())
    db.commit()
    repo.record_transition(stored, transition(stored, TRStatus.CANCELLED, actor=STAFF_ACTOR, at=NOW))
    db.commit()
    late = transition(stored, TRStatus.RECEIVED, actor=STAFF_ACTOR, at=NOW,
                      date_received=date(2026, 10, 9), received_by="JM")
    with pytest.raises(StaleSubmission):
        repo.record_transition(stored, late)


def test_list_filters_and_paginates(repo, db):
    for _ in range(3):
        repo.add(submission())
    repo.add(submission(customer_id=2))
    repo.add(submission(customer_id=2, request_type=RequestType.WAFER, samples=(wafer_sample(1),)))
    db.commit()
    assert repo.list(customer_id=2).total == 2
    assert repo.list(request_type=RequestType.WAFER).total == 1
    assert repo.list(status=TRStatus.CANCELLED).total == 0
    page = repo.list(per_page=2, page=2)
    assert page.total == 5
    assert [s.tr_number for s in page.items] == ["TR00003", "TR00002"]


@postgres_only
def test_concurrent_submissions_get_unique_contiguous_numbers(engine):
    """Postgres row-locks the counter until commit; parallel submits must not collide."""
    factory = make_session_factory(engine)

    def submit_one(_):
        with factory() as session:
            number = TRSubmissionRepository(session).add(submission()).tr_number
            session.commit()
            return number

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(submit_one, range(40)))
    assert sorted(numbers) == [f"TR{n:05d}" for n in range(1, 41)]
