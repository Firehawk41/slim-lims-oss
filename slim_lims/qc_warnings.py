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

from slim_data.domain.enums import ResultStatus
from slim_data.domain.results import Result
from slim_data.domain.values import SampleRef
from slim_data.persistence.observations import batch_readings, get_batch, get_run
from slim_data.persistence.results import results_for_tr, version_batches
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
    """What a request reads once, however many runs, analytes and releases ask: slim-quality's charts,
    each batch's runs, and which batches hold a sample for an analysis."""

    def __init__(self, db: Session) -> None:
        self._db, self._found, self._held = db, None, None
        self._runs: dict[str, list] = {}

    def get(self):
        if self._found is None:
            from slim_lims.quality import SlimDataQc

            self._found = q.charts(self._db, SlimDataQc(self._db))
        return self._found

    def runs(self, code: str) -> list:
        if code not in self._runs:
            self._runs[code] = _runs(self._db, code)
        return self._runs[code]

    def batches_holding(self, sample: SampleRef, analysis_id: int) -> list[str]:
        if self._held is None:
            members = samples_by_batch(self._db)
            self._held = {}
            for b in batches(self._db):
                for s in members.get(b.code, ()):
                    self._held.setdefault((s, b.analysis_id), []).append(b.code)
        return self._held.get((sample, analysis_id), [])


def _runs(db: Session, code: str) -> list:
    ids = {r.run_id for r in batch_readings(db, get_batch(db, code).id) if r.run_id is not None}
    return sorted((get_run(db, i) for i in ids), key=lambda r: r.analyzed_at)


def batch_warnings(
    db: Session, code: str, analytes: set[str], charts: Charts | None = None
) -> dict[str, list]:
    """analyte code -> its warnings across the batch's runs, worst first; analytes with none are left out."""
    charts = charts or Charts(db)
    runs = charts.runs(code)
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


def _ready(result: Result) -> bool:
    """What a release would take now: reviewed, its verdict dealt with, nothing asked of it."""
    return (
        result.status == ResultStatus.REVIEWED
        and result.is_cleared
        and not result.rerun_requested
        and not result.correction_started
    )


def for_release(
    db: Session,
    sample: SampleRef,
    analysis_id: int,
    charts: Charts | None = None,
    results: list[Result] | None = None,
) -> list:
    """The warnings bearing on releasing one sample + analysis now: for each result the release would
    take, the runs of the batch its current version was made from. An older batch a rerun replaced, and
    analytes already released or still waiting, bear on nothing (review L-1). The batch page and the
    release both ask here, so the form asks for a reason exactly when the release would.
    `results`: the request's, when the caller has read them."""
    charts = charts or Charts(db)
    if results is None:
        results = results_for_tr(db, sample.tr_number)
    held = charts.batches_holding(sample, analysis_id)
    by_batch: dict[str, set[str]] = {}
    for r in results:
        if r.sample != sample or r.analyte.analysis_id != analysis_id or not _ready(r):
            continue
        code = held[0] if len(held) == 1 else version_batches(db, r.sample, r.analyte).get(r.current.version)
        if code is not None:
            by_batch.setdefault(code, set()).add(r.analyte.code)
    found: list = []
    for code, analytes in sorted(by_batch.items()):
        for ws in batch_warnings(db, code, analytes, charts).values():
            found += ws
    return found


def action_flags(warnings: list) -> list:
    """The ACTION-severity ones, each chart's code once: what a release must answer for."""
    seen = {(w.slug, w.code): w for w in warnings if w.severity == Severity.ACTION}
    return list(seen.values())


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
