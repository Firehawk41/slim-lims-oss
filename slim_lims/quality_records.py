"""slim-quality's registers and records on the host side (roadmap phase 5a, slim-data D-125…D-130): where
they meet the other modules. Modules never import each other; this is where the equipment register meets
slim-prep and slim-data, the lots meet slim-prep, PT meets slim-data's released results, and a complaint
learns who had a hand in its request.

* `RegisterStocks`: slim-prep's `StockLookup` over slim-quality's reference lots (P-28, D-127).
* `refuse_equipment(db, batch)` / `standard_equipment` / `run_equipment`: what the register says about
  the balance and pipettes of a prep batch or a standard, and the instrument of a run, on the day
  (D-126). A block raises; warnings come back as text for the screen.
* `released_from_prep(db, name, start, end)`: the released results of the prep batches that used a
  balance or pipette in a window: a failed calibration's impact (D-126). `touched` (quality.py) asks it.
* `pt_lab_values(db, round)`: the released results a PT round submits, as they stand (D-128).
* `involved_in(db, tr_number)`: the ids of everyone with a hand in a request's lab work (D-130).
* `seed_demo_register`, `seed_demo_records`: the demo's equipment, lots, PT rounds and complaints.
"""

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal

from slim_data.domain.enums import EventKind
from slim_data.persistence import observations
from slim_data.persistence import results as stored_results
from slim_data.persistence._rows import NotFound as DataNotFound
from slim_data.service.audit import ReleasedResult
from slim_data.service.views import report_view, value_text
from slim_prep.domain.prep import PrepBatch
from slim_prep.domain.problems import PrepRefused, Problem
from slim_prep.domain.standards import Stock
from slim_prep.domain.units import UNITS, Concentration
from slim_prep.persistence import books
from slim_quality.domain.actor import QualityActor
from slim_quality.domain.complaints import ComplaintEntry, ComplaintEntryKind, Party
from slim_quality.domain.enums import Effect, QualityRole
from slim_quality.domain.equipment import EquipmentEntry, EquipmentEntryKind, EquipmentKind
from slim_quality.domain.materials import LotEntry, LotEntryKind, ReferenceLot
from slim_quality.domain.pt import Assigned, LabValue, PtEntry, PtEntryKind, PtRound
from slim_quality.persistence import store
from slim_quality.service import complaints, pt, register
from sqlalchemy.orm import Session

SEED_QUALITY_ACTOR = QualityActor(id="seed", display="Demo seed", role=QualityRole.MANAGER)
PT_SCHEME = "Demo PT Metals"
PT_PROVIDER = "Z"  # the OSS demo's customer Z plays the PT provider (invented, D-128)
PT_ANALYTES = ("Cu", "Zn")
DEMO_INSTRUMENTS = ("ICP-MS 1", "ICP-MS 2", "IC 1", "Titrator 1")  # slim-data's and slim-quality's demo


# --- reference lots (D-127, P-28) ------------------------------------------------------------------------


class RegisterStocks:
    """slim-prep's `StockLookup`: a registered lot as a stock, its expiry the lot's effective expiry."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def stock(self, name: str, lot: str, on: date) -> Stock | str:
        found = register.lot_for_use(self._session, name, lot)
        if found is None:
            return "not in the reference-material register"
        if found.withdrawn_on is not None and found.withdrawn_on <= on:
            return f"withdrawn on {found.withdrawn_on}"
        if found.unit not in UNITS:
            return f"its unit {found.unit!r} isn't one the prep book uses ({', '.join(UNITS)})"
        return Stock(
            name=found.name,
            lot=found.lot,
            concentration=Concentration(value=found.value, unit=found.unit),
            elements=found.elements,
            expires=found.effective_expiry,  # expired: slim-prep refuses it with its own words (P-16)
        )


# --- equipment in use (D-126) ------------------------------------------------------------------------------


def _checked(session: Session, names: Iterable[str], day: date) -> list[str]:
    problems = register.equipment_use(session, [n for n in names if n], day)
    blocks = [p for p in problems if p.effect == Effect.BLOCK]
    if blocks:
        raise PrepRefused(
            [Problem(where=p.name, message=f"{p.message} (equipment register)") for p in blocks]
        )
    return [str(p) for p in problems if p.effect == Effect.WARN]


def batch_equipment(batch: PrepBatch) -> list[str]:
    names = [batch.balance]
    for sample in batch.samples:
        for cup in sample.cups:
            names += [cup.balance or "", *cup.pipettes, *(line.pipette for line in cup.spikes)]
    return list(dict.fromkeys(n for n in names if n))


def refuse_equipment(session: Session, batch: PrepBatch) -> list[str]:
    """The batch's balance and pipettes on its prep day: refused if one blocks; else the warnings."""
    return _checked(session, batch_equipment(batch), batch.prep.at.date())


def standard_equipment(session: Session, pipettes: Iterable[str], day: date) -> list[str]:
    return _checked(session, pipettes, day)


def run_equipment(session: Session, run_id: int | None, zone) -> tuple[list[str], list[str]]:
    """(blocks, warnings) for the instrument a stored run was measured on, on its day."""
    if run_id is None:
        return [], []
    run = observations.get_run(session, run_id)
    return instrument_problems(session, run.instrument, run.analyzed_at.astimezone(zone).date())


def instrument_problems(session: Session, instrument: str, day: date) -> tuple[list[str], list[str]]:
    problems = register.equipment_use(session, [instrument], day)
    return (
        [str(p) for p in problems if p.effect == Effect.BLOCK],
        [str(p) for p in problems if p.effect == Effect.WARN],
    )


# --- a failed balance or pipette: what it touched (D-126) ------------------------------------------------


def released_from_prep(session: Session, name: str, start: datetime, end: datetime) -> list[ReleasedResult]:
    """Every released result version made from a prep batch that used `name` (balance or pipette) and was
    prepped in the window, with the reports that printed it."""
    codes = {
        r.item.code
        for r in books.list_batches(session)
        if name in batch_equipment(r.item) and start <= r.item.prep.at <= end
    }
    found = []
    for code in sorted(codes):
        try:
            batch = observations.get_batch(session, code)
        except DataNotFound:
            continue  # recorded in the prep book, never sent to results
        for result in stored_results.results_for_batch(session, batch.id):
            made_from = stored_results.version_batches(session, result.sample, result.analyte)
            printed = stored_results.issues_showing(session, result.sample, result.analyte)
            for version in result.versions:
                released = [e for e in result.events_for(version.version) if e.kind == EventKind.RELEASED]
                if made_from.get(version.version) != code or not released:
                    continue
                found.append(
                    ReleasedResult(
                        sample=result.sample,
                        analyte=result.analyte,
                        version=version.version,
                        latest_version=len(result.versions),
                        value=value_text(version),
                        unit=None if version.reported is None else version.reported.unit,
                        released_at=released[0].at,
                        released_by=released[0].actor,
                        batch_code=code,
                        runs=(),
                        reports=tuple(printed.get(version.version, ())),
                    )
                )
    return sorted(found, key=lambda r: (r.sample.tr_number, r.sample.position, r.analyte.code, r.version))


# --- PT (D-128) ----------------------------------------------------------------------------------------


class NotReleased(ValueError):
    pass


def _position(sample: str) -> int:
    try:
        return int(sample.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        raise NotReleased(f"{sample!r} is not a lab sample ID") from None


def pt_lab_values(session: Session, pt_round: PtRound) -> tuple[LabValue, ...]:
    """The round's analytes as released on its sample, now: refused until each one is released."""
    position = _position(pt_round.sample)
    lines = {
        line.analyte.code: line
        for line in report_view(session, pt_round.tr_number).lines
        if line.sample.position == position
    }
    missing = [a for a in pt_round.analytes if a not in lines or lines[a].reported is None]
    if missing:
        raise NotReleased(f"not released (with a value) on {pt_round.sample} yet: {', '.join(missing)}")
    values = []
    for code in pt_round.analytes:
        line = lines[code]
        ref = f"{line.sample}|{line.analyte.analysis_id}|{code}|{line.version}"
        values.append(
            LabValue(analyte=code, value=line.reported.value, unit=line.reported.unit or "", result_ref=ref)
        )
    return tuple(values)


# --- complaints (D-130) ----------------------------------------------------------------------------------


def involved_in(session: Session, tr_number: str) -> frozenset[str]:
    """Everyone with a hand in the request's lab work: entered, reviewed, accepted or released any of its
    results (slim-data), prepped any of its samples (slim-prep), or signed any of its reports (D-122)."""
    ids: set[str] = set()
    for result in stored_results.results_for_tr(session, tr_number):
        for version in result.versions:
            ids |= set(version.analyst_ids)
        ids |= {e.actor.id for e in result.events}
    ids |= {issue.created_by.id for issue in stored_results.issues_for_tr(session, tr_number)}
    prefix = f"{tr_number}-"
    for recorded in books.list_batches(session):
        batch = recorded.item
        if any(s.sample.startswith(prefix) for s in batch.samples):
            ids |= {step.analyst_id for step in (batch.prep, batch.pour_out) if step and step.analyst_id}
            ids.add(recorded.created_by.id)
    return frozenset(ids)


# --- the demo ------------------------------------------------------------------------------------------


def _calibration(on: date, due: date, provider: str = "in house") -> EquipmentEntry:
    return EquipmentEntry(
        kind=EquipmentEntryKind.CALIBRATION,
        on=on,
        passed=True,
        provider=provider,
        certificate=f"DEMO-CAL-{on:%y%m%d}",
        next_due=due,
        by=SEED_QUALITY_ACTOR,
    )


def seed_demo_register(session: Session, today: date, instruments: Iterable[str], prep_rules) -> int:
    """The demo's equipment register and reference lots (invented). Idempotent: once the register holds
    anything it is left alone. Every item has passed its calibration, except: one pipette is due in 6
    days (a tile shows it) and a spare balance no demo batch uses is overdue (a balance blocks: try it on
    a prep batch). The demo stock is registered with its certificate; one opened lot expires in 10 days.
    Returns the items registered."""
    if store.list_equipment(session) or store.list_lots(session):
        return 0
    year_ago = today - timedelta(days=200)
    items = [(name, EquipmentKind.INSTRUMENT, "Instrument (demo)") for name in instruments]
    items += [(name, EquipmentKind.BALANCE, "Analytical balance (demo)") for name in prep_rules.balances]
    items += [(name, EquipmentKind.PIPETTE, "Pipette (demo)") for name in prep_rules.pipettes]
    for name, kind, description in items:
        item = register.register_equipment(session, name, kind, SEED_QUALITY_ACTOR, description)
        due = today + timedelta(days=6) if name == prep_rules.pipettes[0] else today + timedelta(days=165)
        provider = "Demo Calibration Services" if kind == EquipmentKind.INSTRUMENT else "in house"
        register.add_equipment_entry(session, item.id, _calibration(year_ago, due, provider))
    spare = register.register_equipment(
        session, "BAL-SPARE", EquipmentKind.BALANCE, SEED_QUALITY_ACTOR, "Spare balance (demo): overdue"
    )
    register.add_equipment_entry(
        session, spare.id, _calibration(today - timedelta(days=400), today - timedelta(days=35))
    )
    for stock in books.list_stocks(session):
        s = stock.item
        register.register_lot(
            session,
            ReferenceLot(
                name=s.name,
                lot=s.lot,
                supplier="Demo Standards Ltd",
                certificate=f"CERT-{s.lot}",
                traceability="Traceable to a national reference (demo)",
                value=s.concentration.value,
                unit=s.concentration.unit,
                uncertainty=s.concentration.value * Decimal("0.005"),
                elements=s.elements,
                expires=s.expires,
                shelf_life_opened_days=365,
                registered_by=SEED_QUALITY_ACTOR,
            ),
        )
    if books.list_stocks(session):
        s = books.list_stocks(session)[0].item
        opened = register.register_lot(
            session,
            ReferenceLot(
                name=s.name,
                lot="DEMO-0000",
                supplier="Demo Standards Ltd",
                certificate="CERT-DEMO-0000",
                traceability="Traceable to a national reference (demo)",
                value=s.concentration.value,
                unit=s.concentration.unit,
                elements=s.elements,
                expires=today + timedelta(days=300),
                shelf_life_opened_days=90,
                registered_by=SEED_QUALITY_ACTOR,
            ),
        )
        register.add_lot_entry(
            session,
            opened.id,
            LotEntry(kind=LotEntryKind.OPENED, on=today - timedelta(days=80), by=SEED_QUALITY_ACTOR),
        )
    return len(items) + 1


# Four past rounds (z 0.4, −1.1, 2.4 questionable, 0.8 on Cu; Zn steady), invented.
PAST_ROUNDS = (
    (("10.4", "10.0", "1.0"), ("20.3", "20.0", "1.0")),
    (("8.9", "10.0", "1.0"), ("19.6", "20.0", "1.0")),
    (("12.4", "10.0", "1.0"), ("20.8", "20.0", "1.0")),
    (("10.8", "10.0", "1.0"), ("19.9", "20.0", "1.0")),
)


def seed_demo_records(session: Session, host, today: date, users: dict, issued_tr: str | None) -> dict:
    """The demo's PT history, the current round (a received TR from the PT provider, for a visitor to
    carry through) and two complaints: one closed with its outcome, one open on a report Rob signed.
    `host` is the scenarios' PortalDemoHost (it makes TRs); `users` maps "manager" / "user" to quality
    actors. Once: skipped when any PT round exists."""
    if store.list_pt_rounds(session):
        return {}
    manager, quality = users["manager"], users["user"]
    for i, values in enumerate(PAST_ROUNDS):
        report_day = today - timedelta(days=90 * (len(PAST_ROUNDS) - i))
        r = pt.register_round(
            session, PT_SCHEME, f"DPT-{i + 1:02d}", "HISTORY", f"HISTORY-{i + 1:02d}", PT_ANALYTES,
            report_day - timedelta(days=30), quality,
        )  # fmt: skip
        sent = tuple(
            LabValue(analyte=a, value=Decimal(v[0]), unit="ppb", result_ref=f"history:{i + 1}:{a}")
            for a, v in zip(PT_ANALYTES, values, strict=True)
        )
        assigned = tuple(
            Assigned(analyte=a, assigned=Decimal(v[1]), sigma=Decimal(v[2]))
            for a, v in zip(PT_ANALYTES, values, strict=True)
        )
        pt.add_entry(session, r.id, PtEntry(kind=PtEntryKind.SUBMITTED, values=sent, by=quality))
        pt.add_entry(
            session,
            r.id,
            PtEntry(kind=PtEntryKind.SCORED, assigned=assigned, report_date=report_day, by=quality),
        )
    analysis_id = host.analyses["10_elements"][1]
    tr_number = host.new_tr("pt_round", 1, False, analysis_id, customer=PT_PROVIDER)
    pt.register_round(
        session, PT_SCHEME, f"DPT-{len(PAST_ROUNDS) + 1:02d}", tr_number, f"{tr_number}-01", PT_ANALYTES,
        today + timedelta(days=21), quality,
    )  # fmt: skip
    found = {"pt_tr": tr_number}
    if issued_tr is None:
        return found
    submission = host.repo.get_by_tr_number(issued_tr)
    customer = users.get("customer")
    party = Party(id=str(customer.id), display=customer.full_name or customer.email) if customer else None
    party = party or Party(id="demo-customer", display="Casey (customer A)")
    closed = complaints.raise_complaint(
        session, issued_tr, str(submission.customer_id),
        "The report reached us a day after you said it would.", party,
    )  # fmt: skip
    outcome = "You were right: the report was sent a day late. We now send it the hour it is issued."
    steps = (
        (
            ComplaintEntryKind.ACKNOWLEDGED,
            "Thank you: we have your complaint and are looking into it.",
            quality,
        ),
        (ComplaintEntryKind.INVESTIGATED, "Issued on the day promised; the delivery went out late.", quality),
        (ComplaintEntryKind.OUTCOME, outcome, manager),
        (ComplaintEntryKind.CLOSED, "Closed with the customer's agreement.", quality),
    )
    for kind, note, by in steps:
        entry = ComplaintEntry(
            kind=kind,
            note=note,
            to_customer=kind != ComplaintEntryKind.INVESTIGATED,
            valid=True if kind == ComplaintEntryKind.INVESTIGATED else None,
            by=by,
        )
        complaints.add_entry(session, closed.id, entry, involved_in(session, issued_tr))
    issues = stored_results.issues_for_tr(session, issued_tr)
    still_open = complaints.raise_complaint(
        session, issued_tr, str(submission.customer_id),
        "The Cu on sample 1 looks high against our own figure. Can you check it?", party,
        issue_id=issues[-1].id if issues else None,
    )  # fmt: skip
    complaints.add_entry(
        session,
        still_open.id,
        ComplaintEntry(
            kind=ComplaintEntryKind.ACKNOWLEDGED,
            note="Thank you: we have your complaint and will reply within five working days.",
            to_customer=True,
            by=quality,
        ),
    )
    found["complaints"] = (closed.id, still_open.id)
    return found
