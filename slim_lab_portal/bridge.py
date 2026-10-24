"""Bridge: portal TR (spec v2) -> slim-domain-oss's current TRSubmission (v1).

Lets the existing OSS report engine build a report from a portal submission without changing
slim-domain or slim-report-engine (CLAUDE.md step A). Needs the OSS `slim-domain` package; the
import is local so the rest of the portal never depends on it. Pure conversion — no database
access, so the OSS-only guard is not involved here.

v1 has no explicit fields for some v2 data, so they are encoded the way v1 expects:
  * location (SX5 North/South) and the TR number go into `file_name`, which v1 reads them from;
  * wafer size goes into `form_chemical_name`; wafer `chemical_id` is 0 (the wafer path ignores it);
  * payment becomes v1's free-text PO / credit-card cells (never card data).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from slim_lab_portal.domain import PaymentMethod, ReportingUnit, RequestType, TRSubmission, WaferSize

# Exact strings v1 / the report engine expect (wafer_report_builder.py).
V1_REPORTING_UNITS = {
    ReportingUnit.ATOMS_PER_CM2: "atoms/cm^2",
    ReportingUnit.E10_ATOMS_PER_CM2: "atoms/cm^2 x 10^10",
}
V1_WAFER_SIZES = {WaferSize.MM_200: "200mm", WaferSize.MM_300: "300mm"}
CREDIT_CARD_TEXT = "Credit card - lab to call customer"
_LOCATION_WORDS = {"SX5N": "North", "SX5S": "South"}


class NotReadyForReport(ValueError):
    """Only received submissions can become v1 objects: v1 requires a receipt date."""


def v1_file_name(submission: TRSubmission, lab_timezone: str = "America/Chicago") -> str:
    """e.g. '100926-A Chemical TR00042.xlsx' or '100926-SX5 South Water TR00043.xlsx'."""
    submitted = submission.submitted_at.astimezone(ZoneInfo(lab_timezone)).date()
    parts = [f"{submitted:%m%d%y}-{submission.customer_snapshot.name}"]
    if submission.location:
        parts.append(_LOCATION_WORDS.get(submission.location, submission.location))
    parts += [submission.request_type.label, submission.tr_number]
    return " ".join(parts) + ".xlsx"


def to_slim_v1(submission: TRSubmission, lab_timezone: str = "America/Chicago"):
    """Returns a slim_domain.domain.tr.tr_submission.TRSubmission (v1) for a received submission."""
    from slim_domain.domain.tr import enums as v1_enums
    from slim_domain.domain.tr.tr_sample import TRSample as V1Sample
    from slim_domain.domain.tr.tr_submission import SENTINEL_DATE
    from slim_domain.domain.tr.tr_submission import TRSubmission as V1Submission

    if submission.receipt is None:
        raise NotReadyForReport(f"{submission.tr_number or 'submission'} has not been received yet")
    unmatched = [s.sample_name for s in submission.samples
                 if submission.request_type is not RequestType.WAFER and s.chemical_id is None]
    if unmatched:
        raise NotReadyForReport(
            f"{submission.tr_number}: chemical not yet matched to SLIM for sample(s) {', '.join(unmatched)}")
    tz = ZoneInfo(lab_timezone)
    wafer = submission.request_type is RequestType.WAFER

    samples = tuple(
        V1Sample(
            id=s.id,
            sample_name=s.sample_name,
            form_chemical_name=V1_WAFER_SIZES[s.wafer_size] if wafer else s.chemical_name,
            processing_time=v1_enums.ProcessingTime(int(s.processing_time)),
            additional_notes=s.additional_notes,
            requested_time=s.requested_time.strftime("%H:%M") if s.requested_time else "",
            chemical_id=0 if wafer else s.chemical_id,
            analysis_ids=s.analysis_ids,
            additional_element_ids=s.additional_element_ids,
            reporting_units=V1_REPORTING_UNITS[s.reporting_unit] if wafer else "",
            water_package=s.water_package.label if s.water_package else "",
        )
        for s in submission.samples
    )
    card = submission.payment_method is PaymentMethod.CREDIT_CARD
    return V1Submission(
        id=submission.id,
        customer_id=submission.customer_id,
        date_submitted=submission.submitted_at.astimezone(tz).date(),
        date_received=submission.receipt.date_received,
        request_type=v1_enums.RequestType(int(submission.request_type)),
        customer_contact=submission.customer_contact,
        customer_phone=submission.customer_phone,
        po_information="" if card else submission.po_number,
        credit_card_information=CREDIT_CARD_TEXT if card else "",
        file_name=v1_file_name(submission, lab_timezone),
        service_date=submission.service_date_override or SENTINEL_DATE,
        download_date=_naive_local(submission.receipt.recorded_at, tz),
        form_customer_name=submission.customer_snapshot.name,
        form_customer_address=submission.customer_snapshot.address_line_1,
        form_customer_address_2=submission.customer_snapshot.address_line_2,
        technician_initials=submission.receipt.received_by,
        samples=samples,
        results_email_main=tuple(submission.results_to),
        results_email_cc=tuple(submission.results_cc),
        invoice_email_main=tuple(submission.invoice_to),
        invoice_email_cc=tuple(submission.invoice_cc),
    )


def _naive_local(value: datetime, tz: ZoneInfo) -> datetime:
    # v1 stores the xlsx file's mtime, a naive local datetime.
    return value.astimezone(tz).replace(tzinfo=None)


__all__ = ["NotReadyForReport", "to_slim_v1", "v1_file_name"]
