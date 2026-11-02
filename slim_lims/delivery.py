"""Sending an issued report to the customer (D-124): the message, the PDF it attaches, kept in the outbox.

A delivery is a row added (append-only), never a status: issuing a report already moved the TR (D-99),
and sending it changes no fact about the results. Only the latest issue of a TR may be sent (an older
one has been replaced); sending it again is another row. Recipients are the request's results_to and
results_cc lists (spec §5).

The transport is a port. The demo's, and this batch's only, is `OUTBOX`: the message is stored and
shown on /outbox, not transmitted: the public demo must not mail addresses visitors type, and its
addresses are fictional. A real mail transport would plug in here and record what the server said.
"""

import uuid
from dataclasses import dataclass

from slim_data.persistence.results import issues_for_tr
from slim_report_engine.lab_identity import LAB_HEADER_LINES
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims import reports
from slim_lims.domain import TRSubmission
from slim_lims.models import ReportDeliveryRow, User

OUTBOX = "outbox"  # stored, not transmitted


class NotSendable(Exception):
    """No such issue, a superseded one, or no PDF to attach."""


@dataclass(frozen=True)
class Message:
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str


def message_for(submission: TRSubmission, issue_id: int, amends: int | None, signatory: str) -> Message:
    lab = LAB_HEADER_LINES[0]
    what = "Amended results report" if amends else "Results report"
    subject = f"{submission.tr_number}: {what.lower()}, issue {issue_id}"
    if amends:
        subject += f" (replaces issue {amends})"
    lines = [
        "Hello,",
        "",
        f"Please find attached the {what.lower()} for your Testing Request {submission.tr_number} "
        f"(report issue {issue_id}).",
    ]
    if amends:
        lines.append(f"It replaces report issue {amends}, which should no longer be used.")
    lines += ["", f"The report is signed by {signatory}.", "", f"Kind regards,\n{lab}"]
    return Message(
        to=tuple(str(a) for a in submission.results_to),
        cc=tuple(str(a) for a in submission.results_cc),
        subject=subject,
        body="\n".join(lines),
    )


def signatory_name(session: Session, actor) -> str:
    """Who signed an issue (its issuer, D-122), by name where the app knows it."""
    try:
        user = session.get(User, uuid.UUID(actor.id))
    except ValueError:
        user = None
    return (user.full_name if user is not None and user.full_name else None) or actor.display


def send_report(session: Session, submission: TRSubmission, issue_id: int, user: User) -> ReportDeliveryRow:
    """Records a delivery of report issue `issue_id` (the TR's latest) to the customer; flushes."""
    issues = issues_for_tr(session, submission.tr_number)
    issue = next((i for i in issues if i.id == issue_id), None)
    if issue is None:
        raise NotSendable(f"{submission.tr_number} has no report issue {issue_id}.")
    if issue is not issues[-1]:
        raise NotSendable(f"Report issue {issue_id} was replaced by issue {issues[-1].id}: send that one.")
    pdf = reports.pdf_for(session, issue.file_sha256)
    if pdf is None:
        raise NotSendable(f"Report issue {issue_id} has no PDF (issued before PDFs were made).")
    message = message_for(submission, issue.id, issue.amends, signatory_name(session, issue.created_by))
    row = ReportDeliveryRow(
        tr_number=submission.tr_number,
        issue_id=issue.id,
        to_addresses=list(message.to),
        cc_addresses=list(message.cc),
        subject=message.subject,
        body=message.body,
        pdf_sha256=pdf.sha256,
        sent_by=user.email,
        transport=OUTBOX,
    )
    session.add(row)
    session.flush()
    return row


def deliveries(session: Session, tr_number: str | None = None, limit: int = 200) -> list[ReportDeliveryRow]:
    """Newest first; one TR's, or the whole outbox."""
    query = select(ReportDeliveryRow).order_by(ReportDeliveryRow.id.desc()).limit(limit)
    if tr_number is not None:
        query = query.where(ReportDeliveryRow.tr_number == tr_number)
    return list(session.scalars(query))
