"""slim-samples' demo data, seeded into the app (invented, demo databases only).

Every request the demo scenarios checked in gets its bottles, so the Samples page and each request show
the module at work. Three are planted so a visitor sees what it is for: a sample whose second bottle
arrived leaking (the report for it says the analysis may be compromised), a bottle moved to the fridge,
and a bottle on hold for a customer query (kept past its retention date, never listed as due).
Runs once: skipped when any bottle exists.
"""

from datetime import date

from slim_samples.domain.containers import ContainerIn
from slim_samples.service import custody
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.domain import TRStatus
from slim_lims.models import TRSubmissionRow
from slim_lims.reference import ReferenceData
from slim_lims.samples import SEED_SAMPLES_ACTOR, TrSampleLookup, rules

IN_LAB = (
    TRStatus.RECEIVED,
    TRStatus.IN_PROGRESS,
    TRStatus.PARTIAL_REPORT,
    TRStatus.COMPLETE_REPORT,
    TRStatus.INVOICED,
)


def seed_demo_bottles(session: Session, reference: ReferenceData, today: date) -> int:
    """Logs the bottles of every request in the lab; returns how many bottle rows. The caller commits."""
    if custody.all_containers(session):
        return 0
    published = rules(session)
    lookup = TrSampleLookup(session, reference)
    actor = SEED_SAMPLES_ACTOR
    requests = session.scalars(
        select(TRSubmissionRow)
        .where(TRSubmissionRow.status.in_([int(s) for s in IN_LAB]))
        .order_by(TRSubmissionRow.tr_number)
    ).all()
    made = 0
    for number, request in enumerate(requests):
        received_on = request.date_received or today
        for info in lookup.for_tr(request.tr_number):
            first = info.sample.endswith("-01")
            if number == 0 and first:
                items = [
                    ContainerIn(container_type="hdpe", count=2, location="shelf-a"),
                    ContainerIn(
                        container_type="pfa",
                        location="receiving",
                        conditions=frozenset({"leaking"}),
                        note="cap loose in transit",
                    ),
                ]
            else:
                items = [ContainerIn(container_type="hdpe", location="shelf-a")]
            bottles = custody.receive(
                session, lookup, info.sample, items, actor=actor, rules=published, received_on=received_on
            )
            made += len(bottles)
            if number == 1 and first:
                custody.hold(session, bottles[0].id, actor=actor, reason="customer query under review")
            if number == 2 and first:
                custody.move(
                    session, bottles[0].id, "fridge-1", actor=actor, rules=published, reason="keep cold"
                )
    return made
