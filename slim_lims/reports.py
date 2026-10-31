"""The report a customer gets (slim-data D-79, spec §13): an HTML document rendered from the released
results when a report is issued, stored, and identified by its sha256 (the hash D-73 planned for the
file). It is served back byte for byte, so a customer always sees exactly what was issued.

`engine_renderer` is the real one: slim-report-engine lays out the lab's report template (a sheet
per sample, a section per analysis, a row per analyte) and fills each released result under the
template's own Results / Recovery / MDL or QL columns. `plain_renderer` is a table of the same lines
for hosts without SLIM's report tables (the tests). Both are deterministic, and both get the same
notes block: the standard qualifiers (D-11), the spec exceedances the report must show with the half
delta where it bears on them (slim-data D-80), and a key that explains MDL, QL and half delta.
"""

from decimal import Decimal
from hashlib import sha256
from html import escape
from typing import Protocol

from slim_data.domain.enums import Qualifier
from slim_data.service.views import ReportLineView, ReportView
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.bridge import to_slim_v1
from slim_lims.domain import TRSubmission
from slim_lims.models import ReportFileRow
from slim_lims.reference import ReferenceData


class ReportRenderer(Protocol):
    def __call__(
        self, session: Session, submission: TRSubmission, view: ReportView, reference: ReferenceData
    ) -> str: ...


def _recovery(value: Decimal | None) -> str:
    return "" if value is None else f"{(value * 100).quantize(Decimal(1))}%"


def _limit(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def _title(submission: TRSubmission, view: ReportView) -> str:
    amended = " (amended)" if any(line.amended for line in view.lines) else ""
    return f"Results report {submission.tr_number}{amended}"


def _conditions(session: Session, reference: ReferenceData, tr_number: str):
    """What was wrong with the bottles that served each analysis (slim-samples S-04). Imported here:
    the samples module reads the results module, which issues these reports."""
    from slim_lims.samples import condition_notes

    if session is None:  # the renderers are also called without a database (their unit tests)
        return {}
    return condition_notes(session, reference, tr_number)


def _notes(
    view: ReportView,
    reference: ReferenceData,
    conditions: dict[tuple[int, int], tuple[str, ...]] | None = None,
) -> str:
    """The Notes section: what the lines carry, then what was wrong with the bottles that served an
    analysis on receipt (slim-samples S-04): the lab says so on the report."""
    rows = []
    for line in view.lines:
        texts = [*line.notes, *(_FLAG_TEXT.get(f, f) for f in line.shown_flags)]
        if line.half_delta is not None:
            if not line.shown_flags:  # in spec, but within half delta of a limit
                texts.append("close to the customer's specification")
            texts.append(f"half delta {line.half_delta}")
        if line.amended:
            texts.append("Amended: corrects a result reported before")
        if texts:
            analysis = reference.analysis(line.analyte.analysis_id)
            name = analysis.name if analysis else line.analyte.analysis_id
            what = f"Sample {line.sample.position}, {name} {line.analyte.code}"
            rows.append(f"<li>{escape(what)}: {escape('; '.join(texts))}</li>")
    reported = {(line.sample.position, line.analyte.analysis_id) for line in view.lines}
    for (position, analysis_id), texts in sorted((conditions or {}).items()):
        if (position, analysis_id) not in reported:
            continue
        analysis = reference.analysis(analysis_id)
        name = analysis.name if analysis else analysis_id
        what = f"Sample {position}, {name}"
        rows.append(
            f"<li>{escape(what)}: condition on receipt, {escape('; '.join(texts))}. "
            "The result for this analysis may be compromised.</li>"
        )
    return (
        f'<section class="slim-report__notes"><h2>Notes</h2><ul>{"".join(rows)}</ul></section>'
        if rows
        else ""
    )


_TERMS = {
    "MDL": "Method detection limit: the lowest concentration the method tells apart from a blank. "
    'A result printed "< MDL" was not detected.',
    "QL": "Quantitation limit: the lowest concentration the method measures with acceptable precision.",
    "Half delta": "Half the difference between the two measurements of the sample. Shown only where a "
    "result is outside your specification, or close enough to it for this difference to matter.",
    "Decision rule": "Simple acceptance: a result meets your specification when its reported value is "
    "within the limit. Half delta is given for information and does not change that.",
}


def _key(view: ReportView, columns: set[str]) -> str:
    """Explains every term the report uses (slim-data D-80): the limit columns it prints, half delta, and the
    decision rule wherever the report says how a result stands against a spec (ISO 17025 §7.1.3)."""
    used = {
        "MDL": "MDL" in columns and any(line.mdl for line in view.lines),
        "QL": "QL" in columns and any(line.ql for line in view.lines),
        "Half delta": any(line.half_delta is not None for line in view.lines),
        "Decision rule": any(line.shown_flags or line.half_delta is not None for line in view.lines),
    }
    items = "".join(f"<dt>{term}</dt><dd>{escape(_TERMS[term])}</dd>" for term, on in used.items() if on)
    return f'<section class="slim-report__key"><h2>Key</h2><dl>{items}</dl></section>' if items else ""


def _template_columns(sheets) -> set[str]:
    return {
        str(v).strip()
        for sheet in sheets
        for section in sheet.sections
        for row in section.rows
        if row.style_name == "ColumnHeader"
        for v in row.values.values()
    }


_FLAG_TEXT = {
    "ABOVE_SPEC": "above the customer's specification",
    "BELOW_SPEC": "below the customer's specification",
}


def engine_renderer(
    session: Session, submission: TRSubmission, view: ReportView, reference: ReferenceData
) -> str:
    """The lab's report template, filled with the released results (slim-report-engine-oss)."""
    from slim_domain.domain.analysis.analysis_service import AnalysisService
    from slim_domain.domain.chemical.chemical_service import ChemicalService
    from slim_domain.domain.customer.customer_service import CustomerService
    from slim_domain.domain.element.element_service import ElementService
    from slim_domain.domain.specification.specification_service import SpecificationService
    from slim_report_engine.reporting.html_report import ResultCell, render_html
    from slim_report_engine.reporting.submission_report_builder import build_submission_sheets

    engine = session.get_bind()
    v1 = to_slim_v1(submission)
    sheets = build_submission_sheets(
        v1,
        CustomerService(session, engine=engine).load_customer(v1.customer_id),
        ChemicalService(session, engine=engine),
        AnalysisService(session, engine=engine),
        ElementService(session),
        SpecificationService(session),
    )
    if len(sheets) != len(submission.samples):
        raise ValueError(f"{submission.tr_number}: expected a report sheet per sample, got {len(sheets)}")
    by_sample: list[dict] = [{} for _ in submission.samples]
    positions = [s.position for s in submission.samples]
    for line in view.lines:
        analysis = reference.analysis(line.analyte.analysis_id)
        if analysis is None or line.sample.position not in positions:
            continue
        by_sample[positions.index(line.sample.position)][(analysis.name, line.analyte.code)] = ResultCell(
            text=line.text,
            mdl=_limit(line.mdl),
            ql=_limit(line.ql),
            recovery=_recovery(line.recovery),
            below_mdl=line.qualifier == Qualifier.BELOW_MDL,
        )
    return (
        render_html(sheets, by_sample, title=_title(submission, view))
        + _notes(view, reference, _conditions(session, reference, submission.tr_number))
        + _key(view, _template_columns(sheets))
    )


def plain_renderer(
    session: Session, submission: TRSubmission, view: ReportView, reference: ReferenceData
) -> str:
    """The same lines as a plain table, for hosts without SLIM's report tables."""

    def row(line: ReportLineView) -> str:
        analysis = reference.analysis(line.analyte.analysis_id)
        cells = (
            str(line.sample),
            analysis.name if analysis else str(line.analyte.analysis_id),
            line.analyte.code,
            line.text,
            line.reported.unit if line.reported else "",
            _recovery(line.recovery),
            _limit(line.mdl),
        )
        return "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in cells) + "</tr>"

    head = "".join(
        f"<th>{h}</th>" for h in ("Sample", "Analysis", "Analyte", "Result", "Unit", "Recovery", "MDL")
    )
    return (
        f'<div class="slim-report"><h1 class="slim-report__title">{escape(_title(submission, view))}</h1>'
        f'<table class="slim-report__table"><thead><tr>{head}</tr></thead><tbody>'
        + "".join(row(line) for line in view.lines)
        + "</tbody></table></div>"
        + _notes(view, reference, _conditions(session, reference, submission.tr_number))
        + _key(view, {"MDL"})
    )


def store(session: Session, tr_number: str, html: str) -> str:
    """Keeps an issued report's file (once per content) and returns its sha256."""
    digest = sha256(html.encode()).hexdigest()
    if session.scalar(select(ReportFileRow.id).where(ReportFileRow.sha256 == digest)) is None:
        session.add(ReportFileRow(tr_number=tr_number, sha256=digest, html=html))
        session.flush()
    return digest


def stored(session: Session, digest: str) -> str | None:
    return session.scalar(select(ReportFileRow.html).where(ReportFileRow.sha256 == digest))
