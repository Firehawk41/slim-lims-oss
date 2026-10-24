from datetime import date, time

import pytest
from pydantic import ValidationError

from slim_lab_portal.domain import (
    ALLOWED_PROCESSING_TIMES,
    PaymentMethod,
    ProcessingTime,
    RequestType,
    SubmissionSource,
    TRReceipt,
    TRStatus,
    TRStatusEvent,
    WaterPackage,
    add_working_days,
    format_tr_number,
)
from tests.factories import NOW, STAFF_ACTOR, TIME_LIMITED, sample, submission, wafer_sample


def test_valid_submission_builds():
    sub = submission()
    assert sub.status is TRStatus.SUBMITTED
    assert sub.effective_service_date is None
    assert sub.is_rush is False


def test_objects_are_frozen():
    sub = submission()
    with pytest.raises(ValidationError):
        sub.customer_id = 2


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        sample(1, colour="blue")


@pytest.mark.parametrize("n, expected", [(1, "TR00001"), (42, "TR00042"), (99999, "TR99999"), (100000, "TR100000")])
def test_tr_number_format(n, expected):
    assert format_tr_number(n) == expected


def test_bad_tr_number_is_rejected():
    with pytest.raises(ValidationError, match="TR00001"):
        submission(tr_number="TR42")


def test_stored_submission_needs_tr_number():
    with pytest.raises(ValidationError, match="tr_number"):
        submission(id=5)


def test_working_days_skip_weekends():
    friday = date(2026, 10, 9)
    assert add_working_days(friday, 1) == date(2026, 10, 12)
    assert add_working_days(friday, 3) == date(2026, 10, 14)
    assert add_working_days(friday, 0) == friday


def _received(sub, day=date(2026, 10, 9)):
    receipt = TRReceipt(date_received=day, received_by="JM", recorded_at=NOW, recorded_by=STAFF_ACTOR)
    event = TRStatusEvent(from_status=TRStatus.SUBMITTED, to_status=TRStatus.RECEIVED, at=NOW, actor=STAFF_ACTOR)
    return submission(**{**dict(sub), "status": TRStatus.RECEIVED, "receipt": receipt,
                         "status_history": (*sub.status_history, event)})


def test_service_date_uses_slowest_sample():
    sub = submission(samples=(sample(1, processing_time=ProcessingTime.TWO_DAYS),
                              sample(2, processing_time=ProcessingTime.FIVE_DAYS)))
    assert _received(sub).effective_service_date == date(2026, 10, 16)


def test_service_date_override_wins():
    sub = _received(submission(service_date_override=date(2026, 12, 1)))
    assert sub.effective_service_date == date(2026, 12, 1)


def test_rush_detection():
    assert submission(samples=(sample(1, processing_time=ProcessingTime.CALL_IN_RUSH),)).is_rush


def test_requested_time_required_for_time_limited():
    with pytest.raises(ValidationError, match="requested_time"):
        sample(1, processing_time=ProcessingTime.TIME_LIMITED)
    assert sample(1, **TIME_LIMITED).requested_time == time(15, 0)


def test_requested_time_only_for_time_limited():
    with pytest.raises(ValidationError, match="requested_time"):
        sample(1, requested_time=time(15, 0))


def test_sample_needs_analysis_or_element():
    with pytest.raises(ValidationError, match="at least one analysis"):
        sample(1, analysis_ids=())
    assert sample(1, analysis_ids=(), additional_element_ids=(26,))


def test_duplicate_analysis_ids_rejected():
    with pytest.raises(ValidationError, match="duplicates"):
        sample(1, analysis_ids=(1, 1))


def test_blank_sample_name_rejected():
    with pytest.raises(ValidationError):
        sample(1, sample_name="   ")


def test_sample_names_unique_case_insensitive():
    with pytest.raises(ValidationError, match="unique"):
        submission(samples=(sample(1, sample_name="abc"), sample(2, sample_name="ABC ")))


def test_sample_positions_must_be_contiguous():
    with pytest.raises(ValidationError, match="positions"):
        submission(samples=(sample(1), sample(3)))


def test_needs_a_sample():
    with pytest.raises(ValidationError, match="at least one sample"):
        submission(samples=())


@pytest.mark.parametrize("request_type", list(RequestType))
def test_processing_time_must_be_offered_for_request_type(request_type):
    not_offered = [pt for pt in ProcessingTime if pt not in ALLOWED_PROCESSING_TIMES[request_type]]
    make = wafer_sample if request_type is RequestType.WAFER else sample
    for pt in not_offered:
        extra = {"requested_time": time(15, 0)} if pt is ProcessingTime.TIME_LIMITED else {}
        with pytest.raises(ValidationError, match="not offered"):
            submission(request_type=request_type, samples=(make(1, processing_time=pt, **extra),))


def test_extended_time_accepted_from_xlsx_only():
    legacy = (sample(1, processing_time=ProcessingTime.EXTENDED_TIME),)
    with pytest.raises(ValidationError, match="not offered"):
        submission(samples=legacy)
    assert submission(source=SubmissionSource.XLSX, file_name="091526-A Chemical.xlsx", samples=legacy)


def test_wafer_sample_rules():
    assert submission(request_type=RequestType.WAFER, samples=(wafer_sample(1),))
    with pytest.raises(ValidationError, match="wafer_size and reporting_unit"):
        submission(request_type=RequestType.WAFER, samples=(wafer_sample(1, wafer_size=None),))
    with pytest.raises(ValidationError, match="no chemical"):
        submission(request_type=RequestType.WAFER, samples=(wafer_sample(1, chemical_id=5),))


def test_chemical_sample_rules():
    with pytest.raises(ValidationError, match="wafer samples only"):
        submission(samples=(sample(1, wafer_size=1, reporting_unit=1),))
    assert submission(samples=(sample(1, chemical_id=None, chemical_name="IPA, unlisted"),))  # unmatched is fine
    with pytest.raises(ValidationError, match="chemical_name is required"):
        submission(samples=(sample(1, chemical_id=None, chemical_name=" "),))


def test_water_package_only_on_water():
    assert submission(request_type=RequestType.WATER, samples=(sample(1, water_package=WaterPackage.PREMIUM),))
    with pytest.raises(ValidationError, match="water samples only"):
        submission(samples=(sample(1, water_package=WaterPackage.PREMIUM),))


def test_po_number_rules():
    with pytest.raises(ValidationError, match="po_number"):
        submission(po_number="  ")
    with pytest.raises(ValidationError, match="po_number"):
        submission(payment_method=PaymentMethod.CREDIT_CARD)
    assert submission(payment_method=PaymentMethod.CREDIT_CARD, po_number="")


def test_emails_required_and_validated():
    with pytest.raises(ValidationError, match="results_to"):
        submission(results_to=())
    with pytest.raises(ValidationError):
        submission(invoice_to=("not-an-email",))


def test_naive_datetimes_rejected():
    with pytest.raises(ValidationError):
        submission(submitted_at=NOW.replace(tzinfo=None))


def test_status_must_match_history():
    with pytest.raises(ValidationError, match="latest status event"):
        submission(status=TRStatus.CANCELLED)


def test_received_status_requires_receipt():
    sub = submission()
    event = TRStatusEvent(from_status=TRStatus.SUBMITTED, to_status=TRStatus.RECEIVED, at=NOW, actor=STAFF_ACTOR)
    with pytest.raises(ValidationError, match="requires a receipt"):
        submission(status=TRStatus.RECEIVED, status_history=(*sub.status_history, event))


def test_json_dump_round_trips():
    sub = _received(submission())
    data = sub.model_dump(mode="json")
    assert data["effective_service_date"] == "2026-10-14"
    rebuilt = type(sub).model_validate({k: v for k, v in data.items() if k not in {"effective_service_date", "estimated_service_date", "is_rush"}})
    assert rebuilt == sub


def test_estimated_service_date_until_received():
    sub = submission(expected_arrival_date=date(2026, 10, 9),
                     samples=(sample(1, processing_time=ProcessingTime.TWO_DAYS),))
    assert sub.estimated_service_date == date(2026, 10, 13)  # Fri + 2 working days
    assert sub.effective_service_date is None
    received = _received(sub, day=date(2026, 10, 12))
    assert received.estimated_service_date is None  # the real one takes over
    assert received.effective_service_date == date(2026, 10, 14)


def test_no_estimate_without_expected_date():
    assert submission().estimated_service_date is None
