"""slim-quality's batch 5b on the host side (slim-data D-131…D-133): where it meets the other modules.

* `TrDates`: slim-quality's `TurnaroundSource` over the app's TR tables. The promise is the app's own
  service date (working days Mon–Fri from receipt, the slowest sample; an override wins); the first and
  complete report are the days the TR's status first reached them (set by issuing a report, D-99).
* `lab_acts(db, reference, clock)`: every act the modules recorded, for the competence check (D-132):
  readings entered, reviews (an acceptance or exception counts as one) and releases (slim-data), prep
  batches (slim-prep), signed reports (D-122); each on the lab's day, its method the analysis code.
* `staff_people(db)`: the staff an authorisation can name.
* `seed_demo_authorisations`: the demo's register, with one gap planted (D-132).
"""

from datetime import date, timedelta

from slim_data.domain.enums import EventKind
from slim_data.persistence import results as stored_results
from slim_prep.persistence import books
from slim_quality.domain.competence import Act, ActRecord, Authorisation, Person
from slim_quality.domain.turnaround import RequestDates
from slim_quality.service import competence
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from slim_lims.domain import ProcessingTime, TRStatus, add_working_days
from slim_lims.labtime import LabClock
from slim_lims.models import TRSampleRow, TRSubmissionRow, User
from slim_lims.quality_records import SEED_QUALITY_ACTOR
from slim_lims.reference import ReferenceData

_REPORTED = (int(TRStatus.PARTIAL_REPORT), int(TRStatus.COMPLETE_REPORT))
_REVIEW = {EventKind.REVIEWED: Act.REVIEW, EventKind.ACCEPTED: Act.REVIEW, EventKind.EXCEPTION: Act.REVIEW}
_ACTS = {**_REVIEW, EventKind.RELEASED: Act.RELEASE}


def _analysis_code(reference: ReferenceData, analysis_id: int) -> str:
    found = reference.analysis(analysis_id)
    return found.code if found is not None else str(analysis_id)


def _submissions(session: Session):
    return session.scalars(
        select(TRSubmissionRow)
        .options(
            selectinload(TRSubmissionRow.samples).selectinload(TRSampleRow.analyses),
            selectinload(TRSubmissionRow.status_events),
        )
        .order_by(TRSubmissionRow.id)
    ).all()


class TrDates:
    """slim-quality's `TurnaroundSource`: each received, uncancelled TR's dates."""

    def __init__(self, session: Session, reference: ReferenceData, clock: LabClock) -> None:
        self._session, self._reference, self._clock = session, reference, clock

    def requests(self, since: date | None = None) -> list[RequestDates]:
        found = []
        for row in _submissions(self._session):
            received = row.date_received
            if received is None or row.status == TRStatus.CANCELLED or not row.samples:
                continue
            if since is not None and received < since:
                continue
            times = [ProcessingTime(s.processing_time) for s in row.samples]
            due = row.service_date_override or add_working_days(received, max(t.days for t in times))
            first = complete = None
            for event in row.status_events:
                day = self._clock.local(event.at).date()
                if event.to_status in _REPORTED and first is None:
                    first = day
                if event.to_status == TRStatus.COMPLETE_REPORT and complete is None:
                    complete = day
            analyses = sorted(
                {_analysis_code(self._reference, a.analysis_id) for s in row.samples for a in s.analyses}
            )
            found.append(
                RequestDates(
                    tr_number=row.tr_number,
                    customer_id=str(row.customer_id),
                    customer=row.snapshot_name or f"Customer {row.customer_id}",
                    analyses=tuple(analyses),
                    samples=len(row.samples),
                    rush=any(t.is_rush for t in times),
                    received=received,
                    due=due,
                    first_report=None if first is None else max(first, received),
                    complete_report=None if complete is None else max(complete, received),
                )
            )
        return found


def staff_people(session: Session) -> dict[str, Person]:
    """Staff by id (as the modules record them), named as they sign (full name, else initials)."""
    users = session.scalars(select(User).where(User.role == "staff").order_by(User.initials)).all()
    return {str(u.id): Person(id=str(u.id), display=u.full_name or u.initials or u.email) for u in users}


def lab_acts(session: Session, reference: ReferenceData, clock: LabClock) -> list[ActRecord]:
    people = staff_people(session)

    def person(actor_id: str, display: str) -> Person:
        return people.get(actor_id) or Person(id=actor_id, display=display or actor_id)

    def day(at) -> date:
        return clock.local(at).date()

    acts: list[ActRecord] = []
    for row in _submissions(session):
        tr = row.tr_number
        for result in stored_results.results_for_tr(session, tr):
            method = _analysis_code(reference, result.analyte.analysis_id)
            label = f"{result.sample} {result.analyte.code}"
            entered: set[str] = set()
            for version in result.versions:
                for analyst in sorted(version.analyst_ids - entered):
                    entered.add(analyst)
                    acts.append(
                        ActRecord(
                            person=person(analyst, analyst), act=Act.ENTER, method=method,
                            on=day(version.created_at), label=label, tr_number=tr,
                            ref=f"result:{result.id}:v{version.version}:{analyst}",
                        )
                    )  # fmt: skip
            for n, event in enumerate(result.events):
                if event.kind in _ACTS:
                    acts.append(
                        ActRecord(
                            person=person(event.actor.id, event.actor.display), act=_ACTS[event.kind],
                            method=method, on=day(event.at), ref=f"result:{result.id}:event:{n}",
                            label=label, tr_number=tr,
                        )
                    )  # fmt: skip
        for issue in stored_results.issues_for_tr(session, tr):
            acts.append(
                ActRecord(
                    person=person(issue.created_by.id, issue.created_by.display), act=Act.SIGN, method=None,
                    on=day(issue.created_at), ref=f"issue:{issue.id}", label=f"{tr} report {issue.id}",
                    tr_number=tr,
                )
            )  # fmt: skip
    for recorded in books.list_batches(session):
        batch = recorded.item
        for name, step in (("prep", batch.prep), ("pour-out", batch.pour_out)):
            if step is None:
                continue
            who = step.analyst_id or recorded.created_by.id
            acts.append(
                ActRecord(
                    person=person(who, step.analyst), act=Act.PREP, method=batch.analysis, on=day(step.at),
                    ref=f"prep:{batch.code}:{name}", label=f"prep {batch.code} ({name})",
                )
            )  # fmt: skip
    return acts


# --- the demo ------------------------------------------------------------------------------------------

# Who holds what (invented). Rob signs reports in the app (the signatory capability, D-122), but nobody
# recorded his authorisation to sign in the register: the gap the check finds (D-132).
DEMO_AUTHORISATIONS = {
    "ana@demo.example": (Act.PREP, Act.ENTER),
    "rob@demo.example": (Act.PREP, Act.ENTER, Act.REVIEW, Act.RELEASE),
    "sue@demo.example": (Act.PREP, Act.ENTER, Act.REVIEW, Act.RELEASE, Act.SIGN),
}


def seed_demo_authorisations(session: Session, today: date) -> int:
    """The demo's register, every method, from a year back; once (skipped when any exists)."""
    if competence.register(session):
        return 0
    count = 0
    for email, acts in DEMO_AUTHORISATIONS.items():
        user = session.scalars(select(User).where(User.email == email)).first()
        if user is None:
            continue
        who = Person(id=str(user.id), display=user.full_name or user.initials or email)
        for act in acts:
            competence.grant(
                session,
                Authorisation(
                    person=who, act=act, valid_from=today - timedelta(days=365),
                    evidence="Training record and a witnessed run (demo)", granted_by=SEED_QUALITY_ACTOR,
                ),
            )  # fmt: skip
            count += 1
    return count
