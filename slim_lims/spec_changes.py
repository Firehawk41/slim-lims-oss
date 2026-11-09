"""Spec limit changes (slim-data D-143): one limit set (customer × chemical × site × kind) at a time,
proposed, then approved or rejected.

* Quality staff and quality managers propose (the *quality* or *quality manager* capability); the set
  as it stands and as proposed, with a reason.
* A quality manager or a lab supervisor who didn't propose it approves or rejects. Only approval writes,
  through slim-domain's `SpecificationService.set_limits` (one transaction), and only if the set on file
  is still what the proposal saw.
* On approval of spec limits (warning limits don't enter evaluation), every results batch with samples
  of that customer (or a site of that group) × chemical × site is re-evaluated: unreleased results get a
  new version judged by the new limits; released ones keep the limits they were judged by (slim-data
  snapshots them, D-04) and those that would now read differently are listed, to raise an NC for.
Proposals and decisions are append-only. Modules flush; routes commit.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from slim_data.domain.enums import ResultStatus
from slim_data.persistence.observations import batch_readings
from slim_data.persistence.observations import get_batch as results_batch
from slim_data.persistence.results import results_for_tr
from slim_data.service.views import samples_by_batch
from slim_data.service.workflow import evaluate_batch
from slim_domain.domain.specification.specification_service import SpecificationService
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims import capabilities
from slim_lims import slim_tables as t
from slim_lims.models import (
    LAB_ROLE_SUPERVISOR,
    SpecDecisionRow,
    SpecProposalRow,
    TRSampleRow,
    TRSubmissionRow,
    User,
)
from slim_lims.reference import ReferenceData

KINDS = {"specification": "Spec limits", "warning_limit": "Warning limits"}
APPROVED, REJECTED = "approved", "rejected"


class SpecRefused(ValueError):
    pass


def may_propose(db: Session, user: User) -> bool:
    held = capabilities.effective(db, user)
    return bool({capabilities.QUALITY, capabilities.QUALITY_MANAGER} & held)


def may_decide(db: Session, user: User) -> bool:
    if not user.is_staff:
        return False
    return user.lab_role == LAB_ROLE_SUPERVISOR or capabilities.QUALITY_MANAGER in capabilities.effective(
        db, user
    )


# ---------------------------------------------------------------- the sets on file


def current_set(
    slim: Session, customer_id: int, chemical_id: int, location: str, kind: str
) -> tuple[dict, str]:
    """(element -> value as text, unit) of one set on file; ({}, "") when there is none."""
    s = t.specifications
    rows = slim.execute(
        select(s.c.element_symbol, s.c.value, s.c.unit).where(
            s.c.kind == kind,
            s.c.customer_id == customer_id,
            s.c.chemical_id == chemical_id,
            s.c.location == (location or ""),
        )
    ).all()
    return {r.element_symbol: _text(r.value) for r in rows}, (rows[0].unit or "") if rows else ""


def _text(value: float) -> str:
    """A limit as the lab typed it: 0.5 not 0.5000000001."""
    return format(Decimal(repr(value)).normalize(), "f")


def sets_on_file(slim: Session) -> list[tuple[int, int, str, str, int]]:
    """Every (customer, chemical, location, kind) with limits on file, and how many elements."""
    s = t.specifications
    counts: dict[tuple, int] = {}
    for r in slim.execute(select(s.c.customer_id, s.c.chemical_id, s.c.location, s.c.kind)):
        if r.customer_id is None or r.chemical_id is None:
            continue
        key = (r.customer_id, r.chemical_id, r.location or "", r.kind)
        counts[key] = counts.get(key, 0) + 1
    return sorted((*k, n) for k, n in counts.items())


# ---------------------------------------------------------------- proposing


@dataclass(frozen=True)
class SetKey:
    customer_id: int
    chemical_id: int
    location: str
    kind: str


def parse_limits(lines: list[tuple[str, str]], known_elements: set[str]) -> dict[str, str]:
    """(symbol, value) pairs typed on the form -> element -> value (canonical text). Blank rows skip."""
    out: dict[str, str] = {}
    problems = []
    for symbol, value in lines:
        symbol, value = symbol.strip(), value.strip()
        if not symbol and not value:
            continue
        if symbol not in known_elements:
            problems.append(f"{symbol or '(blank)'}: not an element")
            continue
        if symbol in out:
            problems.append(f"{symbol} appears twice")
            continue
        try:
            number = Decimal(value)
        except InvalidOperation:
            problems.append(f"{symbol}: {value!r} isn't a number")
            continue
        if not number.is_finite() or number <= 0:
            problems.append(f"{symbol}: a limit is a positive number")
            continue
        out[symbol] = format(number.normalize(), "f")
    if problems:
        raise SpecRefused("; ".join(problems))
    return out


def propose(
    db: Session, slim: Session, key: SetKey, after: dict[str, str], unit: str, reason: str, user: User,
    reference: ReferenceData,
) -> SpecProposalRow:  # fmt: skip
    if not may_propose(db, user):
        raise SpecRefused("Proposing spec changes needs the quality or quality manager capability.")
    if key.kind not in KINDS:
        raise SpecRefused("Choose spec limits or warning limits.")
    if reference.customer(key.customer_id) is None or reference.chemical(key.chemical_id) is None:
        raise SpecRefused("Choose a customer and a chemical that exist.")
    reason = " ".join(reason.split())
    if not reason:
        raise SpecRefused("Say why the limits change.")
    unit = unit.strip()
    if after and not unit:
        raise SpecRefused("Give the unit.")
    before, _ = current_set(slim, key.customer_id, key.chemical_id, key.location, key.kind)
    if before == after:
        raise SpecRefused("Nothing changes: that's the set on file.")
    if pending_for(db, key) is not None:
        raise SpecRefused("This set already has a proposal waiting: decide that one first.")
    row = SpecProposalRow(
        customer_id=key.customer_id, chemical_id=key.chemical_id, location=key.location, kind=key.kind,
        unit=unit[:20], before=before, after=after, reason=reason[:2000], proposed_by=user.email,
        proposed_by_id=str(user.id),
    )  # fmt: skip
    db.add(row)
    db.flush()
    return row


def decision_of(db: Session, proposal_id: int) -> SpecDecisionRow | None:
    return db.scalars(select(SpecDecisionRow).where(SpecDecisionRow.proposal_id == proposal_id)).first()


def pending_for(db: Session, key: SetKey) -> SpecProposalRow | None:
    for row in db.scalars(
        select(SpecProposalRow).where(
            SpecProposalRow.customer_id == key.customer_id, SpecProposalRow.chemical_id == key.chemical_id,
            SpecProposalRow.location == key.location, SpecProposalRow.kind == key.kind,
        )
    ):  # fmt: skip
        if decision_of(db, row.id) is None:
            return row
    return None


def proposals(db: Session) -> list[tuple[SpecProposalRow, SpecDecisionRow | None]]:
    rows = db.scalars(select(SpecProposalRow).order_by(SpecProposalRow.id.desc())).all()
    return [(r, decision_of(db, r.id)) for r in rows]


# ---------------------------------------------------------------- deciding


def _matching_samples(db: Session, reference: ReferenceData, key: SetKey) -> set[tuple[str, int]]:
    """(TR number, position) of every sample of this customer (or a site of this group) × chemical × site."""
    customers = {key.customer_id} | {
        c.id
        for c in reference.customer_choices()
        if getattr(c, "parent_customer_id", None) == key.customer_id
    }
    return {
        (row.tr_number, row.position)
        for row in db.execute(
            select(TRSubmissionRow.tr_number, TRSampleRow.position)
            .join(TRSampleRow, TRSampleRow.submission_id == TRSubmissionRow.id)
            .where(
                TRSubmissionRow.customer_id.in_(customers),
                TRSampleRow.chemical_id == key.chemical_id,
                TRSubmissionRow.location == key.location,
            )
        )
    }


def _matching_batches(db: Session, wanted: set[tuple[str, int]]) -> list[str]:
    return sorted(
        code
        for code, samples in samples_by_batch(db).items()
        if any((s.tr_number, s.position) in wanted for s in samples)
    )


def _passes(value: Decimal, limit: str | None) -> bool:
    return limit is None or value <= Decimal(limit)


def judged_otherwise(db: Session, wanted: set[tuple[str, int]], before: dict, after: dict) -> list[str]:
    """Released results whose reported value passes one limit and not the other: what a customer was told
    against the old limit and would be told now. Only elements whose limit changed."""
    changed = {e for e in set(before) | set(after) if before.get(e) != after.get(e)}
    out = []
    for tr_number in sorted({tr for tr, _ in wanted}):
        for result in results_for_tr(db, tr_number):
            if (tr_number, result.sample.position) not in wanted or result.status != ResultStatus.RELEASED:
                continue
            element, reported = result.analyte.code, result.current.reported
            if element not in changed or reported is None:
                continue
            was, now = (
                _passes(reported.value, before.get(element)),
                _passes(reported.value, after.get(element)),
            )
            if was != now:
                out.append(
                    f"{result.sample} {element} {reported}: {'passes' if was else 'fails'} → "
                    f"{'passes' if now else 'fails'}"
                )
    return out


def approve(
    db: Session,
    slim: Session,
    proposal: SpecProposalRow,
    user: User,
    reference: ReferenceData,
    lookup,
    actor,
    release_reads=lambda: None,
) -> SpecDecisionRow:
    """Writes the set through slim-domain, re-evaluates what it bears on, records the decision.
    `release_reads` ends the caller's read before slim-domain writes on its own connection (the route
    passes its commit: SQLite lets a write through only while nobody else reads)."""
    _may_decide(db, proposal, user)
    key = SetKey(proposal.customer_id, proposal.chemical_id, proposal.location, proposal.kind)
    on_file, _ = current_set(slim, key.customer_id, key.chemical_id, key.location, key.kind)
    if on_file != proposal.before:
        raise SpecRefused("The set on file changed since this was proposed: reject it and propose again.")
    customer, chemical = reference.customer(key.customer_id), reference.chemical(key.chemical_id)
    proposal_id, before, after, unit = proposal.id, dict(proposal.before), dict(proposal.after), proposal.unit
    release_reads()
    slim.rollback()  # and its own read of the set, for the same reason
    try:
        SpecificationService(slim).set_limits(
            key.customer_id, key.chemical_id, key.location, key.kind,
            {k: float(v) for k, v in after.items()}, unit,
            customer.name if customer else str(key.customer_id),
            chemical.name if chemical else str(key.chemical_id),
        )  # fmt: skip
    except ValueError as refused:
        raise SpecRefused(str(refused)) from refused
    reevaluated, differ, skipped = [], [], []
    if key.kind == "specification":
        why = f"spec limits changed (proposal {proposal_id})"
        wanted = _matching_samples(db, reference, key)
        differ += judged_otherwise(db, wanted, before, after)
        for code in _matching_batches(db, wanted):
            if not batch_readings(db, results_batch(db, code).id):
                continue
            try:
                with db.begin_nested():  # one batch that can't be evaluated doesn't undo the others (S1)
                    receipt = evaluate_batch(db, code, actor, lookup, reason=why)
            except (LookupError, ValueError) as why_not:
                skipped.append(f"{code} ({why_not})")
                continue
            reevaluated.append(code)
            differ += [
                f"{k.sample} {k.analyte.code}: would read differently" for k in receipt.released_affected
            ]
    note = f"Not re-evaluated, do it by hand: {'; '.join(skipped)}" if skipped else ""
    row = SpecDecisionRow(
        proposal_id=proposal_id, outcome=APPROVED, by=user.email, reevaluated=reevaluated, reason=note,
        now_differ=list(dict.fromkeys(differ)),
    )  # fmt: skip
    db.add(row)
    db.flush()
    return row


def reject(db: Session, proposal: SpecProposalRow, user: User, reason: str) -> SpecDecisionRow:
    _may_decide(db, proposal, user)
    reason = " ".join(reason.split())
    if not reason:
        raise SpecRefused("Say why it's rejected: the proposer sees it.")
    row = SpecDecisionRow(proposal_id=proposal.id, outcome=REJECTED, by=user.email, reason=reason[:2000])
    db.add(row)
    db.flush()
    return row


def _may_decide(db: Session, proposal: SpecProposalRow, user: User) -> None:
    if not may_decide(db, user):
        raise SpecRefused("Approving spec changes needs a quality manager or a lab supervisor.")
    if proposal.proposed_by_id == str(user.id):
        raise SpecRefused("Someone other than the proposer decides (four eyes).")
    if decision_of(db, proposal.id) is not None:
        raise SpecRefused("This proposal was already decided.")
