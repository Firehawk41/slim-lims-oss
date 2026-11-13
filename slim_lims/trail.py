"""The lab trail of a request (review L-6): each sample × analysis followed on one screen, from the bottles it
came in, through prep, the instrument runs and release, to the reports that printed it and how long the
bottles are kept. Composed from every module's read API on every view; nothing is stored here."""

from dataclasses import dataclass, field
from datetime import date, datetime

from slim_data.domain.enums import EventKind, ResultStatus
from slim_data.persistence.observations import batch_readings, get_batch, get_run
from slim_data.persistence.results import issues_for_tr, results_for_tr
from slim_data.service.views import batches, samples_by_batch
from slim_prep.persistence import books
from slim_samples.service import custody
from sqlalchemy.orm import Session

from slim_lims.domain import TRSubmission
from slim_lims.reference import ReferenceData


@dataclass
class TrailRow:
    position: int
    analysis: str
    bottles: list = field(default_factory=list)  # (title, where or what became of it)
    prep: list = field(default_factory=list)  # prep batch codes
    results: list = field(default_factory=list)  # results batch codes
    runs: list = field(default_factory=list)  # run codes
    state: str = "waiting"  # what its results stand at
    released: datetime | None = None
    reports: list = field(default_factory=list)  # report issue ids that printed it
    keep_until: date | None = None


def lab_trail(db: Session, reference: ReferenceData, submission: TRSubmission) -> list[TrailRow]:
    tr = submission.tr_number
    codes = {a.id: a.code for a in reference.catalog_analyses()}
    bottles: dict[str, list] = {}
    for c in custody.containers_with_prefix(db, f"{tr}-"):
        bottles.setdefault(c.sample, []).append(c)
    prepped = [r.item for r in books.list_batches(db)]
    members = samples_by_batch(db)
    result_batches = [b for b in batches(db) if not b.qc_only]
    results = results_for_tr(db, tr)
    issues = issues_for_tr(db, tr)
    run_codes: dict[str, list[str]] = {}
    rows = []
    for sample in submission.samples:
        sample_id = f"{tr}-{sample.position:02d}"
        for analysis_id in sample.analysis_ids:
            ref = reference.analysis(analysis_id)
            code = codes.get(analysis_id, "")
            row = TrailRow(sample.position, ref.name if ref else f"Analysis #{analysis_id}")
            for c in bottles.get(sample_id, []):
                if code in c.current_analyses:
                    fate = c.current_location if c.active else (c.outcome.name.lower() if c.outcome else "")
                    row.bottles.append((c.title, fate))
                    row.keep_until = max(filter(None, (row.keep_until, c.retention_due)))
            row.prep = [
                b.code
                for b in prepped
                if b.analysis == code and any(s.sample == sample_id for s in b.samples)
            ]
            row.results = [
                b.code
                for b in result_batches
                if b.analysis_id == analysis_id and any(str(s) == sample_id for s in members.get(b.code, ()))
            ]
            for batch_code in row.results:
                if batch_code not in run_codes:
                    ids = {r.run_id for r in batch_readings(db, get_batch(db, batch_code).id) if r.run_id}
                    run_codes[batch_code] = sorted(get_run(db, i).run_code for i in ids)
                row.runs += [r for r in run_codes[batch_code] if r not in row.runs]
            mine = [r for r in results if str(r.sample) == sample_id and r.analyte.analysis_id == analysis_id]
            live = [r for r in mine if r.status != ResultStatus.REJECTED]
            if live:
                done = all(r.status == ResultStatus.RELEASED for r in live)
                row.state = "released" if done else "in review"
                if done:
                    row.released = max(
                        e.at
                        for r in live
                        for e in r.events
                        if e.kind == EventKind.RELEASED and e.at is not None
                    )
            row.reports = [
                i.id
                for i in issues
                if any(
                    str(li.sample) == sample_id and li.analyte.analysis_id == analysis_id for li in i.lines
                )
            ]
            rows.append(row)
    return rows
