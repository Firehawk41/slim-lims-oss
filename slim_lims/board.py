"""The lab board (spec §13): every live TR as a card in the column of where it has got to, with one
chip per sample × analysis saying what its results need. Read-only: every action on a card is a link
to the page that already does it, so the rules stay where they are.

Columns follow what happened, never a hand-set position (cards don't drag):

    expected          submitted, not checked in
    received          checked in, no readings yet
    in_analysis       readings in, results being decided
    ready_to_report   released results not on an issued report yet
    partial_report    a partial report went out; the rest is still in the lab
    to_invoice        complete report issued
    invoice_sent      invoiced, not paid yet
    paid              the most recently paid

Payment is invoice data (invoices.py), never a TR status. Until the invoicing module is rewired to
read SLIM, staff record "Invoice sent" and "Paid" on the card.
"""

from dataclasses import dataclass, field

from slim_data.domain.enums import ResultStatus, Verdict
from slim_data.domain.results import Result
from slim_data.domain.values import SampleRef
from slim_data.persistence.observations import get_batch
from slim_data.persistence.results import issues_for_tr, results_for_tr
from slim_data.service.views import batches
from sqlalchemy.orm import Session

from slim_lims.domain import TRStatus, TRSubmission
from slim_lims.invoices import latest_invoices
from slim_lims.models import LAB_ROLE_ANALYST, LAB_ROLE_REVIEWER, LAB_ROLE_SUPERVISOR, TRInvoiceRow, User
from slim_lims.reference import ReferenceData
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.scope import Scope

COLUMNS = (
    ("expected", "Expected"),
    ("received", "Received"),
    ("in_analysis", "In analysis"),
    ("ready_to_report", "Ready to report"),
    ("partial_report", "Partial report"),
    ("to_invoice", "To invoice"),
    ("invoice_sent", "Invoice sent"),
    ("paid", "Paid"),
)
# Customers see where their request is, not the lab's internal stages.
CUSTOMER_COLUMNS = (
    ("expected", "Not arrived yet", ("expected",)),
    ("at_lab", "At the lab", ("received", "in_analysis", "ready_to_report")),
    ("published", "Results published", ("partial_report", "to_invoice")),
    ("invoice_sent", "Invoice sent", ("invoice_sent",)),
    ("paid", "Paid", ("paid",)),
)
PAID_SHOWN = 10  # the paid column is a tail, not an archive

# What a sample × analysis needs, worst first. (key, label, who acts on it)
CHIP_STATES = {
    "rerun": ("Rerun or exception", LAB_ROLE_SUPERVISOR),
    "rerun_requested": ("Rerun requested", LAB_ROLE_ANALYST),
    "needs_supervisor": ("Needs supervisor", LAB_ROLE_SUPERVISOR),
    "correcting": ("Being corrected", LAB_ROLE_ANALYST),
    "to_review": ("To review", LAB_ROLE_REVIEWER),
    "to_release": ("To release", LAB_ROLE_REVIEWER),
    "awaiting": ("Awaiting results", LAB_ROLE_ANALYST),
    "released": ("Released", None),
    "reported": ("Reported", None),
    "expected": ("Expected", None),
}
# Which columns a role is needed in, beyond its chips ("Needs me").
COLUMN_OWNERS = {
    "expected": LAB_ROLE_SUPERVISOR,
    "ready_to_report": LAB_ROLE_SUPERVISOR,
    "to_invoice": LAB_ROLE_SUPERVISOR,
    "invoice_sent": LAB_ROLE_SUPERVISOR,  # chase the payment
}


@dataclass
class Chip:
    sample: int
    analysis: str
    state: str
    batch: str | None = None  # where its results are worked on

    @property
    def label(self) -> str:
        return CHIP_STATES[self.state][0]

    @property
    def owner(self) -> int | None:
        return CHIP_STATES[self.state][1]


@dataclass
class Card:
    tr: TRSubmission
    column: str
    chips: list[Chip] = field(default_factory=list)
    issued: bool = False  # a report exists to open
    invoice: TRInvoiceRow | None = None

    @property
    def batches(self) -> list[str]:
        return sorted({c.batch for c in self.chips if c.batch})

    def needs(self, lab_role: int | None) -> bool:
        """Whether someone with this lab role has something to do on the card (supervisors on all)."""
        if lab_role is None:
            return False
        roles = {c.owner for c in self.chips} | {COLUMN_OWNERS.get(self.column)}
        if lab_role == LAB_ROLE_SUPERVISOR:
            return bool(roles - {None})
        return lab_role in roles


def _chip_state(results: list[Result], on_last_issue: set) -> str:
    live = [r for r in results if r.status != ResultStatus.REJECTED]
    if not live:
        return "awaiting"
    pending = [r for r in live if r.status != ResultStatus.RELEASED]
    if any(r.correction_started for r in live):
        return "correcting"
    if any(r.rerun_requested for r in pending):
        return "rerun_requested"
    if any(r.current.verdict == Verdict.RERUN and not r.is_cleared for r in pending):
        return "rerun"
    if any(r.current.verdict == Verdict.NEEDS_SUPERVISOR and not r.is_cleared for r in pending):
        return "needs_supervisor"
    if any(r.status == ResultStatus.DRAFT for r in pending):
        return "to_review"
    if pending:
        return "to_release"
    if all((r.sample, r.analyte, r.current.version) in on_last_issue for r in live):
        return "reported"
    return "released"


def _batch_index(session: Session) -> dict[tuple[SampleRef, int], str]:
    """(sample, analysis ID) -> its newest prep batch. One pass over the batches per board."""
    index = {}
    for summary in reversed(batches(session)):  # oldest first, so the newest batch wins
        for sample in get_batch(session, summary.code).samples:
            index[(sample, summary.analysis_id)] = summary.code
    return index


def card_for(
    session: Session,
    tr: TRSubmission,
    reference: ReferenceData,
    batch_of,
    invoice: TRInvoiceRow | None = None,
) -> Card:
    issues = issues_for_tr(session, tr.tr_number)
    on_last_issue = {(li.sample, li.analyte, li.version) for li in issues[-1].lines} if issues else set()
    results: dict[tuple[int, int], list[Result]] = {}
    if tr.status != TRStatus.SUBMITTED:
        for result in results_for_tr(session, tr.tr_number):
            results.setdefault((result.sample.position, result.analyte.analysis_id), []).append(result)
    chips = []
    for sample in tr.samples:
        for analysis_id in sample.analysis_ids:
            analysis = reference.analysis(analysis_id)
            state = (
                "expected"
                if tr.status == TRStatus.SUBMITTED
                else _chip_state(results.get((sample.position, analysis_id), []), on_last_issue)
            )
            ref = SampleRef(tr_number=tr.tr_number, position=sample.position)
            chips.append(
                Chip(
                    sample.position,
                    analysis.name if analysis else f"Analysis #{analysis_id}",
                    state,
                    batch_of.get((ref, analysis_id)),
                )
            )
    return Card(tr, _column(tr, chips, invoice), chips, issued=bool(issues), invoice=invoice)


def _column(tr: TRSubmission, chips: list[Chip], invoice: TRInvoiceRow | None) -> str:
    match tr.status:
        case TRStatus.SUBMITTED:
            return "expected"
        case TRStatus.RECEIVED:
            return "received"
        case TRStatus.COMPLETE_REPORT:
            return "to_invoice"
        case TRStatus.INVOICED:  # invoiced by hand before invoices were recorded counts as sent
            return "paid" if invoice is not None and invoice.paid_on is not None else "invoice_sent"
    if any(c.state == "released" for c in chips):
        return "ready_to_report"
    return "partial_report" if tr.status == TRStatus.PARTIAL_REPORT else "in_analysis"


def board(
    session: Session,
    user: User,
    reference: ReferenceData,
    *,
    customer_id: int | None = None,
    analysis_id: int | None = None,
    rush: bool = False,
    mine: bool = False,
) -> list[tuple[str, str, list[Card]]]:
    """(column key, title, cards oldest first), in board order. Customers only ever see their own TRs,
    in CUSTOMER_COLUMNS."""
    scope = Scope.for_user(user, customer_id)  # a customer's own, whatever was asked (review A6)
    mine = mine and user.is_staff
    repo, batch_of = TRSubmissionRepository(session), _batch_index(session)
    columns: dict[str, list[Card]] = {key: [] for key, _ in COLUMNS}
    for status in TRStatus:
        if status == TRStatus.CANCELLED:
            continue
        trs = repo.list(scope, status=status, per_page=500).items  # newest first
        invoices = latest_invoices(session, [t.id for t in trs]) if status == TRStatus.INVOICED else {}
        for tr in reversed(trs):
            if analysis_id is not None and not any(analysis_id in s.analysis_ids for s in tr.samples):
                continue
            if rush and not tr.is_rush:
                continue
            card = card_for(session, tr, reference, batch_of, invoices.get(tr.id))
            if mine and not card.needs(user.lab_role):
                continue
            columns[card.column].append(card)
    columns["paid"] = sorted(columns["paid"], key=lambda c: (c.invoice.paid_on, c.invoice.id))[-PAID_SHOWN:]
    if not user.is_staff:
        return [
            (key, title, [c for k in parts for c in columns[k]]) for key, title, parts in CUSTOMER_COLUMNS
        ]
    return [(key, title, columns[key]) for key, title in COLUMNS]
