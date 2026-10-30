"""slim-prep's demo scenario, seeded into the app (slim-prep P7). Demo databases only.

One evaporation batch, end to end, so a first visitor sees the prep book at work:
  1. the analyst persona makes the spike standards from the demo stock (1000 ppm -> 20 ppm -> 50 ppb);
  2. preps a real TR's two IPA samples by evaporation (dilution 0.06: concentrated), spiked from the
     50 ppb standard, and sends the batch to results (slim-data registers it with each cup's dilution);
  3. the run's readings come in, the check standard bracketing the samples, and are evaluated;
  4. the reviewer persona reviews and releases sample 1;
  5. the analyst finds a misread final weight and corrects the batch with a reason; the correction goes
     to results, slim-data re-evaluates, and the released result it changes waits for its correction
     (the batch page shows it). Nothing further is done: that is the visitor's to see.
Invented numbers (slim-data D-05). Runs once: skipped when the prep book has a batch.
"""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from slim_data.demo.scenarios import HEADER
from slim_data.domain.values import SampleRef
from slim_data.service import workflow as wf
from slim_prep.domain.prep import Cup, CupKind, PrepBatch, PrepSample, SpikeLine, StepRecord, Weighing
from slim_prep.persistence import books
from slim_prep.service.books import record_batch, record_standard
from sqlalchemy.orm import Session

from slim_lims.demo_scenarios import PortalDemoHost
from slim_lims.prep import TrSamples, prep_actor_for, rules, send
from slim_lims.reference import ReferenceData
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import import_and_evaluate, start_work
from slim_lims.services import SubmissionService

CODE = "PREP-0001"  # the demo rules' series (slim-prep P-26)
RECIPE = "multi-element spike"
D = Decimal


def seed_prep_scenario(
    session: Session, reference: ReferenceData, specs, now: datetime | None = None
) -> str | None:
    """Runs the scenario; returns its TR number, or None when the prep book has batches already. The
    caller commits. Needs the demo personas, the demo rules and `publish_demo_prep`."""
    if books.list_batches(session):
        return None
    now = now or datetime.now(UTC)
    made = (
        now - timedelta(hours=4)
    ).date()  # the standards, made before the batch (now − 3 h) on its day or earlier
    host = PortalDemoHost(session, reference, specs, now.date())
    analyst = host.users["analyst"]
    tr = host.new_tr("prepped_evaporation", 2, False, host.analyses["10_elements"][1])
    rules_id = rules(session).id
    stock = next(r.item for r in books.list_stocks(session))

    # 1. the spike standards, from the stock
    who = prep_actor_for(analyst)
    twenty = record_standard(session, rules_id, RECIPE, "20 ppm", (stock.name, stock.lot), made, who,
                             ("P1000-1",)).item  # fmt: skip
    fifty = record_standard(session, rules_id, RECIPE, "50 ppb", twenty.ref, made, who, ("P1000-1",)).item

    # 2. the prep batch, sent to results
    samples = TrSamples(session, reference)
    record_batch(session, _batch(tr, fifty, analyst, now, final="2.6000"), rules_id, who, samples, now)
    send(session, CODE, analyst, reference, specs)

    # 3. the run's readings (before dilution: the file leaves the factor at 1; the prep book gives it)
    import_and_evaluate(session, _readings(tr, host.analyst_code, now), "prep-demo-run.csv", host.analyst,
                        host.lookup, host.staff)  # fmt: skip
    service = SubmissionService(TRSubmissionRepository(session), reference)
    start_work(service, [tr], host.users["supervisor"])

    # 4. reviewed; sample 1 released
    wf.review_batch(session, CODE, host.reviewer)
    wf.release(session, SampleRef.parse(f"{tr}-01"), host.analyses["10_elements"][1], host.reviewer)

    # 5. a misread final weight, corrected with a reason, sent to results again
    corrected = _batch(tr, fifty, analyst, now, final="2.6000", cup_2_final="2.6600")
    reason = "Sample 1 cup 2: final weight misread (2.6600 g)"
    record_batch(session, corrected, None, who, samples, now, 1, reason)
    send(session, CODE, analyst, reference, specs)
    return tr


def _batch(tr: str, fifty, analyst, now: datetime, final: str, cup_2_final: str | None = None) -> PrepBatch:
    at = now - timedelta(hours=3)  # entered, never in the future (slim-prep P-25)
    step = StepRecord(analyst=analyst.initials, analyst_id=str(analyst.id), at=at)

    def cup(number, final_g, kind=CupKind.SAMPLE, spikes=()):
        weighing = Weighing(tare=D("2.0000"), cap=D("0.5000"), gross=D("12.5000"), final=D(final_g))
        return Cup(number=number, kind=kind, weighing=weighing, spikes=spikes)

    spike = SpikeLine.of(fifty, D(1000), "P1000-1")  # 1000 µL of 50 ppb into 10 g: 5 ppb
    samples = []
    for position in (1, 2):
        second = cup_2_final if position == 1 and cup_2_final else final
        cups = (cup(1, final), cup(2, second), cup(3, final, CupKind.SPIKE, (spike,)))
        samples.append(PrepSample(sample=f"{tr}-{position:02d}", cups=cups))
    return PrepBatch(
        code=CODE,
        method="evaporation",
        analysis="10_elements_icpms",
        balance="BAL-1",
        pour_out=step,
        prep=step,
        samples=tuple(samples),
        note="demo: evaporated 10 g to about 0.6 g",
    )


def _readings(tr: str, analyst: str, now: datetime) -> bytes:
    """Raw readings, in run order: a check standard, the samples, their spikes, a check standard."""
    stamp = datetime.combine(now.date(), time(14, 0), tzinfo=now.tzinfo).isoformat()
    rows = [("CHECK", "Cu", "CHECK", 1, "10.1", "", "10.0"), ("CHECK", "Fe", "CHECK", 1, "9.9", "", "10.0")]
    for position, (cu, fe) in ((1, ("100.0", "50.0")), (2, ("80.0", "40.0"))):
        s = f"{tr}-{position:02d}"
        for code, value, step in (("Cu", cu, D(1)), ("Fe", fe, D("0.5"))):
            rows += [
                (s, code, "CUP", 1, value, "0.05", ""),
                (s, code, "CUP", 2, str(D(value) + step), "0.05", ""),
            ]
    for position, (cu, fe) in ((1, ("183.0", "133.6")), (2, ("164.5", "123.9"))):
        s = f"{tr}-{position:02d}"
        rows += [(s, "Cu", "SPIKE", 1, cu, "0.05", ""), (s, "Fe", "SPIKE", 1, fe, "0.05", "")]
    rows += [
        ("BLANK", "Cu", "BLANK", 1, "0.010", "0.05", ""),
        ("BLANK", "Fe", "BLANK", 1, "0.010", "0.05", ""),
    ]
    rows += [("CHECK", "Cu", "CHECK", 2, "10.2", "", "10.0"), ("CHECK", "Fe", "CHECK", 2, "9.8", "", "10.0")]
    lines = [HEADER] + [
        f"{s},10_elements_icpms,{c},{role},{rep},{v},ppb,{mdl},1,RUN-PREP-1,ICP-MS 1,{analyst},{stamp},"
        f"{CODE},,,,,{expected},{'CCV mix A' if expected else ''},{'L-0425' if expected else ''}"
        for s, c, role, rep, v, mdl, expected in rows
    ]
    return ("\n".join(lines) + "\n").encode()
