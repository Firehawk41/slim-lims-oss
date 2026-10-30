"""slim-quality's host side (slim-data D-109): who acts in it, the QC data it reads, its migrations and
its demo history.

* `quality_actor(db, user)`: a staff user with a quality capability, as slim-quality sees them; None
  without one. Quality manager > quality (a manager also decides changes and signs QC reviews).
* `SlimDataQc`: slim-quality's `QcSource` over slim-data's QC history (D-107). Modules never import
  each other; this is where they meet.
* `seed_quality_history`: months of invented QC runs (slim-quality's demo history), imported through
  slim-data as Qtegra exports (qtegra/2, D-106) into QC-only batches (D-108): stored, verifiable, off
  the board. With the maintenance event and one open change, so a visitor has something to decide.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from slim_data.demo.qtegra_export import export, measurement
from slim_data.service.quality import qc_history
from slim_data.service.views import batches
from slim_quality.demo import history as demo
from slim_quality.domain.actor import QualityActor
from slim_quality.domain.enums import EventKind, QualityRole
from slim_quality.domain.qc import CalibrationRecord, QcRecord, to_series
from slim_quality.persistence import migrate as quality_migrate
from slim_quality.persistence import store
from slim_quality.service import quality as q
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from slim_lims import capabilities
from slim_lims.models import User
from slim_lims.reference import ReferenceData
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
        EventKind.MAINTENANCE,
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
