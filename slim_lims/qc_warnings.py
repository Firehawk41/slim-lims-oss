"""QC warnings at review and release (slim-quality part 3, slim-data D-144).

slim-quality's tools judge each QC series over its whole history (Westgard rules, EWMA, CUSUM, one-sided
limits, the time-to-limit forecast as `LIMIT_AHEAD`). Here those flags reach the people deciding results:
for every run in a results batch, the flags on that instrument × analyte (and its internal standard) from
7 days before the run through the run, as known now.

Advice, never a block: slim-data's rules decide a result's validity (as the competence check never blocks,
D-132). Only releasing a sample + analysis with an ACTION-severity flag asks for a reason, kept with a
frozen copy of the flags as shown (`qc_acknowledgements`, append-only).
"""

from dataclasses import dataclass
from datetime import datetime

from slim_data.domain.values import SampleRef
from slim_data.persistence.observations import batch_readings, get_batch, get_run
from slim_data.service.views import batches, samples_by_batch
from slim_quality.domain.enums import Severity
from slim_quality.service import quality as q
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.models import QcAcknowledgementRow, User

DAYS_BEFORE = 7
SEVERITY_LABELS = {Severity.INFO: "info", Severity.WARN: "warn", Severity.ACTION: "action"}


@dataclass(frozen=True)
class Warning:
    instrument: str
    analyte: str  # the result's analyte code
    series: str  # the chart's label
    slug: str  # the chart's id, for its page
    tool: str
    code: str
    severity: Severity
    message: str
    at: datetime

    @property
    def level(self) -> str:
        return SEVERITY_LABELS[self.severity]

    def frozen(self) -> dict:
        return {
            "instrument": self.instrument, "series": self.series, "tool": self.tool, "code": self.code,
            "severity": self.level, "message": self.message, "at": self.at.isoformat(),
        }  # fmt: skip


class Charts:
    """slim-quality's charts, drawn once per request however many runs and analytes ask."""

    def __init__(self, db: Session) -> None:
        self._db, self._found = db, None

    def get(self):
        if self._found is None:
            from slim_lims.quality import SlimDataQc

            self._found = q.charts(self._db, SlimDataQc(self._db))
        return self._found


def _runs(db: Session, code: str) -> list:
    ids = {r.run_id for r in batch_readings(db, get_batch(db, code).id) if r.run_id is not None}
    return sorted((get_run(db, i) for i in ids), key=lambda r: r.analyzed_at)


def batch_warnings(
    db: Session, code: str, analytes: set[str], charts: Charts | None = None
) -> dict[str, list]:
    """analyte code -> its warnings across the batch's runs, worst first; analytes with none are left out."""
    charts = charts or Charts(db)
    runs = _runs(db, code)
    if not runs or not analytes:
        return {}
    found = charts.get()
    out: dict[str, dict] = {}
    for run in runs:
        for analyte in analytes:
            for line in q.context_at(found, run.instrument, analyte, run.analyzed_at, days=DAYS_BEFORE):
                key = line.chart.series.key
                for f in line.flags:
                    if f.at > run.analyzed_at:
                        continue  # through the run, not after it
                    w = Warning(
                        instrument=run.instrument, analyte=analyte, series=key.label, slug=key.slug,
                        tool=f.tool, code=f.code, severity=f.severity, message=f.message, at=f.at,
                    )  # fmt: skip
                    out.setdefault(analyte, {})[(key.slug, f.index, f.code)] = w
    return {a: sorted(ws.values(), key=lambda w: (-int(w.severity), w.at)) for a, ws in sorted(out.items())}


@dataclass(frozen=True)
class SeriesWarnings:
    label: str
    slug: str
    warnings: list  # one per flag, worst first
    analytes: list  # the batch's analytes it bears on

    @property
    def level(self) -> str:
        return self.warnings[0].level


def by_series(warnings: dict[str, list]) -> list[SeriesWarnings]:
    """The panel's view: each chart once (an internal standard's flags bear on every analyte), worst first."""
    flags: dict[str, dict] = {}
    analytes: dict[str, set] = {}
    labels: dict[str, str] = {}
    for analyte, ws in warnings.items():
        for w in ws:
            flags.setdefault(w.slug, {})[(w.code, w.at)] = w
            analytes.setdefault(w.slug, set()).add(analyte)
            labels[w.slug] = w.series
    out = [
        SeriesWarnings(
            labels[s], s, sorted(f.values(), key=lambda w: (-int(w.severity), w.at)), sorted(analytes[s])
        )
        for s, f in flags.items()
    ]
    return sorted(out, key=lambda g: (-int(g.warnings[0].severity), g.label))


def worst(warnings: list) -> Severity | None:
    return max((w.severity for w in warnings), default=None)


def for_release(db: Session, sample: SampleRef, analysis_id: int, charts: Charts | None = None) -> list:
    """The warnings bearing on one sample + analysis: its batch's runs, its own analytes."""
    from slim_data.persistence.results import results_for_tr

    analytes = {
        r.analyte.code
        for r in results_for_tr(db, sample.tr_number)
        if r.sample == sample and r.analyte.analysis_id == analysis_id
    }
    members = samples_by_batch(db)
    codes = [
        b.code for b in batches(db) if b.analysis_id == analysis_id and sample in members.get(b.code, ())
    ]
    charts = charts or Charts(db)
    found: list = []
    for code in codes:
        for ws in batch_warnings(db, code, analytes, charts).values():
            found += ws
    return found


class AcknowledgementNeeded(ValueError):
    """An ACTION-severity flag bears on the release and no reason was given."""


def acknowledge_if_needed(db: Session, sample: SampleRef, analysis_id: int, note: str, user: User) -> None:
    """Called inside the release's transaction: refuses (AcknowledgementNeeded) an ACTION flag without a
    reason; keeps the reason and the flags as shown when one is given."""
    action = [w for w in for_release(db, sample, analysis_id) if w.severity == Severity.ACTION]
    if not action:
        return
    note = " ".join(note.split())
    if not note:
        raise AcknowledgementNeeded(
            f"QC trend flags at action level bear on {sample}: say why the result still stands "
            f"({'; '.join(f'{w.series}: {w.code}' for w in action[:3])})."
        )
    db.add(
        QcAcknowledgementRow(
            sample=str(sample), analysis_id=analysis_id, flags=[w.frozen() for w in action], note=note[:2000],
            by=user.email,
        )
    )  # fmt: skip
    db.flush()


def acknowledgements(
    db: Session, sample: SampleRef, analysis_id: int | None = None
) -> list[QcAcknowledgementRow]:
    query = select(QcAcknowledgementRow).where(QcAcknowledgementRow.sample == str(sample))
    if analysis_id is not None:
        query = query.where(QcAcknowledgementRow.analysis_id == analysis_id)
    return list(db.scalars(query.order_by(QcAcknowledgementRow.id)))
