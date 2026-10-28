"""Invoicing a TR (spec §13, slim-data D-83): the invoicing module V5 bills what was reported.

"Invoice" on a complete-report TR builds V5's `BillableRequest` from the TR and slim-data's report
issues, lets V5 price it into a sales order, moves the TR to invoiced and records the invoice as sent,
all in the caller's one transaction. Payment is invoice data (`tr_invoices`), never a TR status.
"""

import slim_invoicing
from slim_data.persistence.results import issues_for_tr
from slim_invoicing.persistence import migrate as invoicing_migrate
from slim_invoicing.persistence.repositories import default_quotes, quotes_for_customer
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from slim_lims.domain import PaymentMethod, TRStatus, TRSubmission
from slim_lims.models import TRInvoiceRow, User
from slim_lims.reference import ReferenceData
from slim_lims.schemas import StatusChangeIn
from slim_lims.services import Conflict, Forbidden, SubmissionService


def migrate_invoicing(engine: Engine) -> None:
    """V5's own tables, in the shared database, with its own Alembic version table."""
    with engine.begin() as connection:
        invoicing_migrate.upgrade(connection)


def billable(
    session: Session, submission: TRSubmission, reference: ReferenceData, today
) -> slim_invoicing.BillableRequest:
    """The TR as it was reported: every (sample, analysis) any report issue showed (amendments included)."""
    reported = {
        (line.sample.position, line.analyte.analysis_id)
        for issue in issues_for_tr(session, submission.tr_number)
        for line in issue.lines
    }
    analyses = {a for s in submission.samples for a in s.analysis_ids}
    return slim_invoicing.BillableRequest(
        tr_number=submission.tr_number,
        complete=submission.status is TRStatus.COMPLETE_REPORT,
        customer_id=submission.customer_id,
        customer_name=submission.customer_snapshot.name,
        payment_method=slim_invoicing.PaymentMethod(int(submission.payment_method)),
        po_number=submission.po_number if submission.payment_method is PaymentMethod.PURCHASE_ORDER else "",
        requester_name=submission.customer_contact,
        requester_email=submission.invoice_to[0],
        date_received=submission.receipt.date_received if submission.receipt else today,
        service_date=submission.service_date_override or today,
        samples=tuple(
            slim_invoicing.BillableSample(
                position=s.position,
                name=s.sample_name,
                processing_time=slim_invoicing.ProcessingTime(int(s.processing_time)),
                analysis_ids=s.analysis_ids,
            )
            for s in submission.samples
        ),
        reported=frozenset(reported),
        analysis_names={a: ref.name for a in analyses if (ref := reference.analysis(a))},
    )


def latest_invoices(session: Session, submission_ids) -> dict[int, TRInvoiceRow]:
    """submission ID -> its newest invoice."""
    rows = session.scalars(
        select(TRInvoiceRow)
        .where(TRInvoiceRow.submission_id.in_(list(submission_ids)))
        .order_by(TRInvoiceRow.id)
    ).all()
    return {row.submission_id: row for row in rows}


def mark_sent(
    session: Session, service: SubmissionService, tr_number: str, user: User, reference: ReferenceData
) -> TRInvoiceRow:
    """Invoices a complete TR (V5 prices it into a sales order), moves it to invoiced and records the
    invoice as sent. Staff only. Refused (Conflict) when V5 refuses: already invoiced, nothing
    reported, or an analysis no quote prices."""
    if not user.is_staff:
        raise Forbidden("only lab staff record invoices")
    submission = service.get(tr_number, user)
    if submission.status is not TRStatus.COMPLETE_REPORT:
        raise Conflict(f"{tr_number} is {submission.status.label.lower()}: invoice a complete report")
    try:
        slim_invoicing.invoice(
            session,
            billable(session, submission, reference, service.lab_today()),
            created_by=user.initials or user.email,
            on=service.lab_today(),
        )
    except slim_invoicing.InvoiceRefused as e:
        raise Conflict(str(e)) from e
    service.change_status(
        tr_number, StatusChangeIn(to_status=TRStatus.INVOICED, note="invoice sent"), user, fact=True
    )
    row = TRInvoiceRow(
        submission_id=submission.id, sent_on=service.lab_today(), sent_by=user.initials or user.email
    )
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


def price_lists(session: Session, reference: ReferenceData) -> list[slim_invoicing.Quote]:
    """Every quote: the default price list(s) first, then each customer's own (the Invoices page)."""
    quotes = {q.id: q for q in default_quotes(session)}
    for customer in reference.customer_choices():
        quotes.update((q.id, q) for q in quotes_for_customer(session, customer.id))
    return list(quotes.values())
