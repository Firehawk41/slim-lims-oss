"""The day's board (slim-data D-138, the live view D-111 parked): what is due today in a section, or the
whole lab, and where each piece of work has got to. For a quick look and for a screen left up in the lab.

The unit is a sample × analysis. Its due date is the request's service date override if set, else
receipt + that sample's processing time in working days (Mon–Fri, as `TrDates`); before receipt, the
expected arrival + the same (an estimate). Shown: everything due on the day, and anything due earlier
not released yet (late). Its stage follows the facts, never a hand-set position:

    not_arrived     the request is not checked in
    received        checked in; in no prep batch, on no run list
    in_prep         in a slim-prep batch not yet sent to results
    on_instrument   on a results batch (the run list), no results yet
    in_review       results being decided (the board's chip says what they need)
    released        every live result released: done for the bench

A card is one request's work in the section, in the column of its least advanced piece. Sections are
the lab's own (`lab_sections`); an analysis in no section shows only on the whole lab's board.
Whether the screens show customer names is a lab-wide switch (`display_modes`; none set: hidden).
Read-only, like the board: no action here changes a fact.
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime

from slim_data.domain.enums import ResultStatus
from slim_data.domain.results import Result
from slim_data.domain.values import SampleRef
from slim_data.persistence.results import issues_for_tr, results_for_tr
from slim_prep.persistence import books
from slim_samples.service import custody
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims import board
from slim_lims.domain import TRStatus, TRSubmission, add_working_days
from slim_lims.models import DisplayLinkRow, DisplayModeRow, LabSectionAnalysisRow, LabSectionRow
from slim_lims.reference import ReferenceData
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.scope import Scope

STAGES = (
    ("not_arrived", "Not arrived"),
    ("received", "Received"),
    ("in_prep", "In prep"),
    ("on_instrument", "On instrument"),
    ("in_review", "In review"),
    ("released", "Released"),
)
_ORDER = {key: n for n, (key, _) in enumerate(STAGES)}
_LIVE = [s for s in TRStatus if s != TRStatus.CANCELLED]


@dataclass(frozen=True)
class Work:
    """One sample × analysis due."""

    sample: int
    analysis_id: int
    analysis: str
    due: date
    stage: str
    state: str  # the board's chip state (board.CHIP_STATES)
    late: bool
    blocked: bool = False  # a bottle condition stops prep (slim-samples S-04)

    @property
    def label(self) -> str:
        if self.blocked and self.stage in ("received", "in_prep"):
            return "Blocked"
        return board.CHIP_STATES[self.state][0] if self.stage == "in_review" else dict(STAGES)[self.stage]


@dataclass
class DayCard:
    tr: TRSubmission
    work: list[Work] = field(default_factory=list)

    @property
    def stage(self) -> str:
        return min((w.stage for w in self.work), key=_ORDER.__getitem__)

    @property
    def late(self) -> bool:
        return any(w.late for w in self.work)

    @property
    def due(self) -> date:
        return min(w.due for w in self.work)

    @property
    def done(self) -> int:
        return sum(w.stage == "released" for w in self.work)


@dataclass
class DayBoard:
    day: date
    section: LabSectionRow | None
    columns: list[tuple[str, str, list[DayCard]]]

    @property
    def work(self) -> list[Work]:
        return [w for _, _, cards in self.columns for c in cards for w in c.work]

    @property
    def total(self) -> int:
        return len(self.work)

    @property
    def released(self) -> int:
        return sum(w.stage == "released" for w in self.work)

    @property
    def left(self) -> int:
        return self.total - self.released

    @property
    def late(self) -> int:
        return sum(w.late for w in self.work)

    @property
    def rush(self) -> int:
        return sum(1 for _, _, cards in self.columns for c in cards if c.tr.is_rush)


# ---------------------------------------------------------------- due dates and stages


def due_date(tr: TRSubmission, processing_days: int) -> date | None:
    """When one sample's work is due: the override, else receipt (or expected arrival) + its days."""
    if tr.receipt is not None:
        if tr.service_date_override is not None:
            return tr.service_date_override
        return add_working_days(tr.receipt.date_received, processing_days)
    if tr.expected_arrival_date is None:
        return None
    return add_working_days(tr.expected_arrival_date, processing_days)


def next_working_day(day: date) -> date:
    return add_working_days(day, 1)


def _prep_index(session: Session) -> set[tuple[str, str]]:
    """(lab sample ID, analysis code) in any prep batch, in one read."""
    return {(s.sample, r.item.analysis) for r in books.list_batches(session) for s in r.item.samples}


def _stage(tr: TRSubmission, results: list[Result], on_results_batch: bool, in_prep: bool) -> str:
    if tr.status == TRStatus.SUBMITTED:
        return "not_arrived"
    live = [r for r in results if r.status != ResultStatus.REJECTED]
    if live:
        return "released" if all(r.status == ResultStatus.RELEASED for r in live) else "in_review"
    if on_results_batch:
        return "on_instrument"
    return "in_prep" if in_prep else "received"


def day_board(
    session: Session,
    reference: ReferenceData,
    today: date,
    scope: Scope,
    *,
    section: LabSectionRow | None = None,
    day: date | None = None,
) -> DayBoard:
    """The board for `day` (default today): what is due then, plus what is late as of `today`. `scope` is
    a staff member's, or the whole lab's for a display link (one a staff member made)."""
    day = day or today
    in_section = None if section is None else {a.analysis_id for a in section.analyses}
    codes = {a.id: a.code for a in reference.catalog_analyses()}
    on_batch, in_prep = board.batch_index(session), _prep_index(session)
    repo = TRSubmissionRepository(session)
    cards: list[DayCard] = []
    for status in _LIVE:
        for tr in reversed(repo.list(scope, status=status, per_page=500).items):
            card = _card(session, tr, reference, codes, today, day, in_section, on_batch, in_prep)
            if card is not None:
                cards.append(card)
    cards.sort(key=lambda c: (not c.late, not c.tr.is_rush, c.due, c.tr.tr_number))
    columns = [(key, title, [c for c in cards if c.stage == key]) for key, title in STAGES]
    return DayBoard(day=day, section=section, columns=columns)


def _card(session, tr, reference, codes, today, day, in_section, on_batch, in_prep) -> DayCard | None:
    wanted = []
    for sample in tr.samples:
        due = due_date(tr, sample.processing_time.days)
        if due is None or due > day:
            continue
        for analysis_id in sample.analysis_ids:
            if in_section is None or analysis_id in in_section:
                wanted.append((sample, analysis_id, due))
    if not wanted:
        return None
    results: dict[tuple[int, int], list[Result]] = {}
    on_last_issue: set = set()
    if tr.status != TRStatus.SUBMITTED:
        for result in results_for_tr(session, tr.tr_number):
            results.setdefault((result.sample.position, result.analyte.analysis_id), []).append(result)
        issues = issues_for_tr(session, tr.tr_number)
        on_last_issue = {(li.sample, li.analyte, li.version) for li in issues[-1].lines} if issues else set()
    blocked: dict[int, set[str]] = {}
    card = DayCard(tr)
    for sample, analysis_id, due in wanted:
        ref = SampleRef(tr_number=tr.tr_number, position=sample.position)
        found = results.get((sample.position, analysis_id), [])
        code = codes.get(analysis_id, "")
        stage = _stage(tr, found, (ref, analysis_id) in on_batch, (str(ref), code) in in_prep)
        if due < day and stage == "released":
            continue  # done before the day: not the day's work, nor late
        if stage in ("received", "in_prep") and sample.position not in blocked:
            blocked[sample.position] = set(custody.blocked_for(session, str(ref)))
        analysis = reference.analysis(analysis_id)
        card.work.append(
            Work(
                sample=sample.position,
                analysis_id=analysis_id,
                analysis=analysis.name if analysis else f"Analysis #{analysis_id}",
                due=due,
                stage=stage,
                state="expected" if stage == "not_arrived" else board.chip_state(found, on_last_issue),
                late=due < today and stage != "released",
                blocked=code in blocked.get(sample.position, ()),
            )
        )
    return card if card.work else None


# ---------------------------------------------------------------- sections


def sections(session: Session) -> list[LabSectionRow]:
    return list(session.scalars(select(LabSectionRow).order_by(LabSectionRow.sort_order, LabSectionRow.name)))


def section_of(session: Session) -> dict[int, int]:
    """analysis ID -> its section ID."""
    return {r.analysis_id: r.section_id for r in session.scalars(select(LabSectionAnalysisRow))}


class SectionRefused(ValueError):
    pass


def add_section(session: Session, name: str) -> LabSectionRow:
    name = " ".join(name.split())
    if not name or len(name) > 60:
        raise SectionRefused("A section needs a name of up to 60 characters.")
    if session.scalars(select(LabSectionRow).where(LabSectionRow.name == name)).first() is not None:
        raise SectionRefused(f"There is already a section called {name}.")
    last = max((s.sort_order for s in sections(session)), default=0)
    row = LabSectionRow(name=name, sort_order=last + 1)
    session.add(row)
    session.flush()
    return row


def set_analyses(session: Session, section: LabSectionRow, analysis_ids: set[int]) -> None:
    """The section owns exactly these analyses; an analysis ticked here leaves its old section."""
    for row in list(section.analyses):
        if row.analysis_id not in analysis_ids:
            section.analyses.remove(row)
    session.flush()
    have = {r.analysis_id for r in section.analyses}
    for analysis_id in sorted(analysis_ids - have):
        elsewhere = session.get(LabSectionAnalysisRow, analysis_id)
        if elsewhere is not None:
            session.delete(elsewhere)
            session.flush()
        section.analyses.append(LabSectionAnalysisRow(analysis_id=analysis_id))
    session.flush()
    session.expire(section, ["analyses"])


def remove_section(session: Session, section: LabSectionRow) -> None:
    """Its analyses go back to no section; its display links go with it."""
    for link in session.scalars(select(DisplayLinkRow).where(DisplayLinkRow.section_id == section.id)):
        session.delete(link)
    session.delete(section)
    session.flush()


# ---------------------------------------------------------------- display links and the names switch


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def add_link(
    session: Session, label: str, section: LabSectionRow | None, by: str
) -> tuple[DisplayLinkRow, str]:
    """A new screen link and its token (shown once; only its hash is kept)."""
    label = " ".join(label.split())[:60] or (section.name if section else "Whole lab")
    token = secrets.token_urlsafe(24)
    row = DisplayLinkRow(
        token_sha256=_hash(token), label=label, section_id=section.id if section else None, created_by=by
    )
    session.add(row)
    session.flush()
    return row, token


def links(session: Session) -> list[DisplayLinkRow]:
    return list(session.scalars(select(DisplayLinkRow).order_by(DisplayLinkRow.id)))


def link_for(session: Session, token: str) -> DisplayLinkRow | None:
    """The live link a token opens, or None (unknown or revoked)."""
    if not token or len(token) > 100:
        return None
    row = session.scalars(select(DisplayLinkRow).where(DisplayLinkRow.token_sha256 == _hash(token))).first()
    return row if row is not None and row.revoked_at is None else None


def revoke_link(session: Session, link: DisplayLinkRow, by: str, at: datetime) -> None:
    if link.revoked_at is None:
        link.revoked_at, link.revoked_by = at, by
        session.flush()


def names_shown(session: Session) -> DisplayModeRow | None:
    """The latest setting of the switch, or None: never set, so names are hidden."""
    return session.scalars(select(DisplayModeRow).order_by(DisplayModeRow.id.desc()).limit(1)).first()


def set_names_shown(session: Session, shown: bool, by: str) -> DisplayModeRow:
    row = DisplayModeRow(names_shown=shown, set_by=by)
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------- the demo's sections (invented)

DEMO_SECTIONS = (  # (name, catalog groups, codes left out)
    ("ICP-MS", ("Metals",), "icpoes"),
    ("IC", ("Anions", "Cations"), None),
    ("Wet lab", ("Physical", "Assay & titration"), None),
)


def seed_demo_sections(session: Session, reference: ReferenceData) -> int:
    """Three sections over the demo catalog; the rest of it is in none. Once: skipped if any exists."""
    if sections(session):
        return 0
    for name, groups, left_out in DEMO_SECTIONS:
        row = add_section(session, name)
        ids = {
            a.id
            for a in reference.catalog_analyses()
            if a.group in groups and not (left_out and left_out in a.code)
        }
        set_analyses(session, row, ids)
    return len(DEMO_SECTIONS)
