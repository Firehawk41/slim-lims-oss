"""Invoice sent / paid per TR (spec §13), until the invoicing module is rewired to read SLIM and
writes these itself. Sending an invoice also moves the TR to invoiced (the status slim-lims and the
board already know); payment is invoice data, never a TR status.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lab_portal.domain import TRStatus
from slim_lab_portal.models import TRInvoiceRow, User
from slim_lab_portal.schemas import StatusChangeIn
from slim_lab_portal.services import Conflict, Forbidden, SubmissionService


def latest_invoices(session: Session, submission_ids) -> dict[int, TRInvoiceRow]:
    """submission ID -> its newest invoice."""
    rows = session.scalars(select(TRInvoiceRow).where(TRInvoiceRow.submission_id.in_(list(submission_ids)))
                           .order_by(TRInvoiceRow.id)).all()
    return {row.submission_id: row for row in rows}


def mark_sent(session: Session, service: SubmissionService, tr_number: str, user: User) -> TRInvoiceRow:
    """Records the invoice for a complete TR and moves it to invoiced. Staff only."""
    if not user.is_staff:
        raise Forbidden("only lab staff record invoices")
    submission = service.get(tr_number, user)
    if submission.status is not TRStatus.COMPLETE_REPORT:
        raise Conflict(f"{tr_number} is {submission.status.label.lower()}: invoice a complete report")
    service.change_status(tr_number, StatusChangeIn(to_status=TRStatus.INVOICED, note="invoice sent"), user)
    row = TRInvoiceRow(submission_id=submission.id, sent_on=service.lab_today(), sent_by=user.initials or user.email)
    session.add(row)
    session.flush()
    return row


def mark_paid(session: Session, service: SubmissionService, tr_number: str, user: User) -> TRInvoiceRow:
    """Records payment of the TR's newest invoice. Staff only."""
    if not user.is_staff:
        raise Forbidden("only lab staff record payments")
    submission = service.get(tr_number, user)
    invoice = latest_invoices(session, [submission.id]).get(submission.id)
    if invoice is None:
        raise Conflict(f"{tr_number} has no invoice to pay")
    if invoice.paid_on is not None:
        raise Conflict(f"{tr_number}'s invoice was paid on {invoice.paid_on}")
    invoice.paid_on, invoice.paid_by = service.lab_today(), user.initials or user.email
    session.flush()
    return invoice
