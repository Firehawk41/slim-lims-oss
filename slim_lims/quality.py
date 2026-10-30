"""slim-quality's host side (slim-data D-109): who acts in it, the QC data it reads, its migrations and
its demo history.

* `quality_actor(db, user)`: a staff user with a quality capability, as slim-quality sees them; None
  without one. Quality manager > quality (a manager also decides changes and signs QC reviews).
* `SlimDataQc`: slim-quality's `QcSource` over slim-data's QC history (D-107). Modules never import
  each other; this is where they meet.
* `audit_page`: the vertical audit of one released report, composed from every module's read API
  (D-113); `random_issue` picks one. `touched`: what a nonconformity's window touched (D-114).
* `seed_quality_history`: months of invented QC runs (slim-quality's demo history), imported through
  slim-data as Qtegra exports (qtegra/2, D-106) into QC-only batches (D-108): stored, verifiable, off
  the board. With the maintenance event and one open change, so a visitor has something to decide.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from random import SystemRandom
from zoneinfo import ZoneInfo

from slim_data.demo.qtegra_export import export, measurement
from slim_data.domain.enums import EventKind
from slim_data.domain.results import ReportIssue
from slim_data.service.audit import ResultAudit, issues_between, released_in_window, report_audit
from slim_data.service.quality import qc_history
from slim_data.service.views import batches
from slim_prep.persistence import books
from slim_prep.persistence._rows import NotFound as PrepNotFound
from slim_quality.demo import history as demo
from slim_quality.domain.actor import QualityActor
from slim_quality.domain.enums import EventKind as QualityEventKind
from slim_quality.domain.enums import QualityRole, Severity
from slim_quality.domain.nonconformity import NcSource
from slim_quality.domain.qc import CalibrationRecord, QcRecord, to_series
from slim_quality.persistence import migrate as quality_migrate
from slim_quality.persistence import store
from slim_quality.service import quality as q
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from slim_lims import capabilities, reports
from slim_lims.audit import chain_check
from slim_lims.models import User
from slim_lims.reference import ReferenceData
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import (
    StaffDirectory,
    actor_for,
    demo_analyses,
    import_and_evaluate,
    reference_lookup,
)

QC_PREP_METHOD = "QC run (no prep)"
QUALITY_PERSONAS = {"manager": "quinn@demo.example", "user": "uma@demo.example"}


def migrate_slim_quality(engine: Engine) -> None:
    """slim-quality's own tables, in the shared database, with its own Alembic version table."""
    with engine.begin() as connection:
        quality_migrate.upgrade(connection)


def quality_actor(db: Session, user: User | None) -> QualityActor | None:
    if user is None or not user.is_staff:
        return None
    held = capabilities.effective(db, user)
    if capabilities.QUALITY_MANAGER in held:
        role = QualityRole.MANAGER
    elif capabilities.QUALITY in held:
        role = QualityRole.USER
    else:
        return None
    return QualityActor(id=str(user.id), display=user.initials or user.email, role=role)


class SlimDataQc:
    """slim-quality's `QcSource` over slim-data's QC history; analytes by code, the analysis left out
    (a series is the instrument's, whatever analysis the run was for)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def series(self, since: datetime | None = None, as_of: datetime | None = None):
        found = qc_history(self._session, since, as_of)
        records = [
            QcRecord(
                kind=r.role.name.lower(),
                ref=f"reading:{r.reading_id}",
                instrument=r.instrument,
                at=r.analyzed_at,
                run_code=r.run_code,
                sequence=r.sequence,
                analyte=r.analyte.code,
                conditions=r.conditions,
                measured=r.measured,
                unit=r.unit,
                expected=r.expected,
                material=r.material,
                rsd=r.rsd,
                istd_recovery=r.istd_recovery,
                batch=r.batch_code,
                analyst=r.analyst_id,
                excluded=r.excluded,
            )
            for r in found.readings
        ]
        calibrations = [
            CalibrationRecord(
                instrument=c.instrument,
                at=c.analyzed_at,
                run_code=c.run_code,
                analyte=c.analyte.code,
                conditions=c.conditions,
                unit=c.unit,
                correlation=c.correlation,
                lod=c.lod,
                bec=c.bec,
            )
            for c in found.calibrations
        ]
        return to_series(records, calibrations)


# --- demo history -------------------------------------------------------------------------------


def _export(run: demo.DemoRun, zone: ZoneInfo) -> bytes:
    """One demo run as a Qtegra export: calibration standards, a CCV, the method blank, a CCV."""
    local = run.at.astimezone(zone)
    stamp = [f"{local + timedelta(minutes=m):%Y-%m-%dT%H:%M:%S}.0000000" for m in range(8)]
    isotopes = {"Fe": "56Fe", "Zn": "66Zn", "Na": "23Na"}
    cal = {isotopes[e]: tuple(str(v) for v in run.calibration[e]) for e in demo.ELEMENTS}
    rows = [
        measurement(
            "BLK", "Cal blank", values={i: "0" for i in isotopes.values()}, istd="100", start=stamp[0]
        ),
        measurement(
            "STD",
            "Std 5",
            standard="5 ppb",
            values={i: "5" for i in isotopes.values()},
            calibration=cal,
            start=stamp[1],
        ),
    ]
    for n, check in enumerate(run.checks):
        if n == 1:
            rows.append(
                measurement(
                    "UNKNOWN",
                    "Prep blank",
                    comment="BLANK",
                    values={isotopes[e]: str(v) for e, v in run.blank.items()},
                    istd=str(run.blank_istd),
                    start=stamp[3],
                )
            )
        rows.append(
            measurement(
                "QC",
                "CCV",
                qc_action="CCV",
                standard=demo.CHECK_MATERIAL,
                values={isotopes[e]: str(v) for e, v in check.values.items()},
                rsd={isotopes[e]: str(v) for e, v in check.rsd.items()},
                reference={i: str(demo.CHECK_LEVEL) for i in isotopes.values()},
                istd=str(check.istd),
                start=stamp[2 + 2 * n],
            )
        )
    content = export(
        run=tuple(rows),
        elements=tuple(isotopes.values()),
        modes={isotopes[e]: m for e, m in demo.ELEMENTS.items()},
    )
    return content.replace(b"iCAPevoRQ", run.instrument.encode())


def seed_quality_history(
    session: Session, reference: ReferenceData, specs, now: datetime, days: int = 90
) -> int:
    """Imports the demo history once (skipped when any QC-only batch exists); returns the runs imported."""
    if any(b.qc_only for b in batches(session)):
        return 0
    from slim_data.pipeline.qtegra import Qtegra2

    people = {
        role: session.scalars(select(User).where(User.email == email, User.is_persona.is_(True))).first()
        for role, email in {"analyst": "ana@demo.example", **QUALITY_PERSONAS}.items()
    }
    if None in people.values():
        raise LookupError("the demo personas are missing; run `cli sync-personas` first")
    zone = now.tzinfo if isinstance(now.tzinfo, ZoneInfo) else ZoneInfo("UTC")
    analysis_code = demo_analyses(reference)["10_elements"][0]
    analyst = people["analyst"]
    lookup = reference_lookup(session, reference, specs)
    staff = StaffDirectory(session)
    runs = demo.history(now, days=days)
    for run in runs:
        reader = Qtegra2(
            prep_batch=run.code,
            analysis_code=analysis_code,
            analyst_id=analyst.initials,
            timezone=zone,
            prep_method=QC_PREP_METHOD,
            run_code=run.code,
        )
        import_and_evaluate(
            session, _export(run, zone), f"{run.code}.xml", actor_for(analyst), lookup, staff, reader
        )
    manager = quality_actor(session, people["manager"])
    user = quality_actor(session, people["user"])
    q.record_event(
        session,
        QualityEventKind.MAINTENANCE,
        demo.INSTRUMENTS[0],
        demo.maintenance_at(now),
        "Cones cleaned, torch aligned",
        user,
    )
    cusum = tuple(
        t.model_copy(update={"enabled": True}) if t.tool == "cusum@1" else t for t in q.STARTER.tools
    )
    q.propose_profile(
        session,
        q.STARTER.model_copy(update={"tools": cusum}),
        "Turn on CUSUM to catch slow bias earlier",
        user,
    )
    assert manager is not None and store.list_changes(session, open_only=True)
    return len(runs)


# --- the vertical audit (D-113) -----------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One line of the audit's summary: True passed, False failed, None a note (not checked, or a fact to
    look at that isn't a fault, such as a batch registered by an instrument file)."""

    what: str
    ok: bool | None
    detail: str = ""


@dataclass(frozen=True)
class TrVersion:
    version: int
    recorded_at: datetime
    change: str
    actor_display: str


@dataclass(frozen=True)
class AuditLine:
    result: ResultAudit
    prep: object | None  # slim-prep's Recorded[PrepBatch] as it stood, or None (registered by a file)
    prep_sample: object | None  # that batch's entry for this sample
    qc: list  # slim-quality ContextLines, per run: (run, lines)


@dataclass(frozen=True)
class AuditPage:
    issue: ReportIssue
    tr_versions: list
    file_matches: bool | None  # None: the file isn't kept
    lines: list[AuditLine]
    checks: list[Check]
    chain_broken: tuple[int, ...] | None  # None: not checked (SQLite, or no audit log)


def audit_page(db: Session, issue_id: int) -> AuditPage:
    """A released report, end to end, from every module's read API (slim-data D-112, D-113)."""
    found = report_audit(db, issue_id)
    issue = found.issue
    html = reports.stored(db, issue.file_sha256)
    file_matches = None if html is None else sha256(html.encode()).hexdigest() == issue.file_sha256
    repo = TRSubmissionRepository(db)
    tr = repo.get_by_tr_number(issue.tr_number)
    versions = []
    for v in [] if tr is None else repo.versions(tr.id):
        at = v.recorded_at if v.recorded_at.tzinfo else v.recorded_at.replace(tzinfo=UTC)  # SQLite: naive UTC
        if at <= issue.created_at:
            versions.append(TrVersion(v.version, at, v.change, v.actor_display))
    charts_then: dict = {}
    lines = []
    for r in found.lines:
        prep = prep_sample = None
        try:
            prep = books.get_batch(db, r.batch_code, as_of=r.made_at)
            prep_sample = next((s for s in prep.item.samples if s.sample == str(r.sample)), None)
        except PrepNotFound:
            pass
        if r.made_at not in charts_then:
            charts_then[r.made_at] = q.charts(db, SlimDataQc(db), as_of=r.made_at)
        qc = [
            (run, q.context_at(charts_then[r.made_at], run.instrument, r.analyte.code, run.analyzed_at))
            for run in r.runs
        ]
        lines.append(AuditLine(result=r, prep=prep, prep_sample=prep_sample, qc=qc))
    checker = chain_check(db.get_bind())
    broken = None if checker is None else tuple(checker(db.connection()))
    return AuditPage(
        issue=issue,
        tr_versions=versions,
        file_matches=file_matches,
        lines=lines,
        checks=_checks(file_matches, lines, broken),
        chain_broken=broken,
    )


def _checks(file_matches, lines: list[AuditLine], broken) -> list[Check]:
    results = [line.result for line in lines]
    replays = [f"{r.sample} {r.analyte.code}: {r.replay}" for r in results if r.replay]
    four_eyes, solo = [], []
    for r in results:
        entered = (
            set(r.analyst_ids) | {run.analyst.id for run in r.runs} | {run.imported_by.id for run in r.runs}
        )
        for e in r.events:
            if e.kind in (EventKind.REVIEWED, EventKind.RELEASED):
                if e.solo:
                    solo.append(f"{r.sample} {r.analyte.code} {e.kind.name.lower()} solo: {e.note}")
                elif e.actor.id in entered:
                    four_eyes.append(f"{r.sample} {r.analyte.code}: {e.kind.name.lower()} by who entered it")
    files = [run.run_code for r in results for run in r.runs if not run.file_kept]
    unprepped = sorted({line.result.batch_code for line in lines if line.prep is None})
    actions = [
        f"{run.run_code}: {c.chart.series.key.label} {f.code}"
        for line in lines
        for run, context in line.qc
        for c in context
        for f in c.flags
        if f.severity == Severity.ACTION
    ]
    amended = [
        f"{r.sample} {r.analyte.code} v{r.version} (now v{r.latest_version})"
        for r in results
        if r.latest_version > r.version
    ]
    return [
        Check(
            "The report file matches its hash",
            file_matches,
            "" if file_matches is not None else "the file isn't kept",
        ),
        Check("Every result replays exactly through its ruleset", not replays, "; ".join(replays)),
        Check("Nobody reviewed or released results they entered", not four_eyes, "; ".join(four_eyes)),
        Check("Solo sign-offs", not solo, "; ".join(solo) or "none"),
        Check("Every run's raw file is kept", not files, ", ".join(files)),
        Check(
            "Every batch is in the prep book",
            None if unprepped else True,  # a file may register a batch (D-24): a note, not a fault
            f"registered by an instrument file: {', '.join(unprepped)}" if unprepped else "",
        ),
        Check("No ACTION flags on the QC around the runs", not actions, "; ".join(dict.fromkeys(actions))),
        Check("No result on it has been amended since", not amended, "; ".join(amended)),
        Check(
            "The audit log's chain is intact",
            None if broken is None else not broken,
            "not checked (no audit log here)"
            if broken is None
            else (f"{len(broken)} entries broken" if broken else ""),
        ),
    ]


def random_issue(db: Session, start: datetime, end: datetime) -> int | None:
    found = issues_between(db, start, end)
    return SystemRandom().choice(found).id if found else None


# --- nonconformities (D-114) ----------------------------------------------------------------------


def result_ref(r) -> str:
    return f"{r.sample}|{r.analyte.analysis_id}|{r.analyte.code}|{r.version}"


def impact_label(r) -> str:
    """How a touched result reads on the assessment (built here, never taken from the form)."""
    unit = f" {r.unit}" if r.unit else ""
    return f"{r.sample} {r.analyte.code} v{r.version}: {r.value}{unit}"


def touched(db: Session, nc) -> list:
    """The released results the nonconformity's window may have touched (slim-data D-112)."""
    if nc.instrument is None or nc.window_from is None:
        return []
    return released_in_window(db, nc.instrument, nc.analyte, nc.window_from, nc.window_to)


def seed_demo_nonconformity(db: Session, now: datetime) -> None:
    """The planted cone wear, raised from its flag by the quality user, left open for a visitor."""
    if store.list_ncs(db):
        return
    user = quality_actor(db, db.scalars(select(User).where(User.email == QUALITY_PERSONAS["user"])).first())
    if user is None:
        return
    q.raise_nc(
        db,
        "Internal standard drifting low on ICP-MS 1",
        "The internal standard's EWMA and Westgard 4_1s flag a steady fall on ICP-MS 1 over five weeks; "
        "the check standards creep up while still inside ±25 %. Suspect cone wear. Which released "
        "results does it touch?",
        user,
        NcSource.FLAG,
        "ICP-MS 1 internal standard recovery",
        demo.INSTRUMENTS[0],
        None,
        (now - timedelta(days=demo.WEAR_DAYS), now),
    )
