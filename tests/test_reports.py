"""The real report (slim-data D-79): the demo scenarios issued through slim-report-engine's own
template on the fictional OSS demo data, results filled under Results / Recovery / MDL or QL.
Skipped without the slim-report-engine-oss checkout (its demo seed), like the bridge tests."""

import importlib.util
import sys
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("slim_report_engine")

from slim_data.domain.enums import Qualifier  # noqa: E402
from slim_data.domain.values import AnalyteRef, SampleRef  # noqa: E402
from slim_data.persistence import migrate  # noqa: E402
from slim_data.persistence.results import issues_for_tr  # noqa: E402
from slim_data.service.views import ReportLineView, ReportView, report_view  # noqa: E402
from slim_invoicing.persistence import migrate as invoicing_migrate  # noqa: E402
from slim_samples.persistence import migrate as samples_migrate  # noqa: E402

from slim_lims import reports  # noqa: E402
from slim_lims.catalog import sync_analysis_catalog  # noqa: E402
from slim_lims.db import Base, make_engine, make_session_factory  # noqa: E402
from slim_lims.demo_scenarios import seed_scenarios  # noqa: E402
from slim_lims.personas import sync_personas  # noqa: E402
from slim_lims.reference_cache import load_reference  # noqa: E402
from slim_lims.reports import plain_renderer  # noqa: E402
from slim_lims.repositories import TRSubmissionRepository  # noqa: E402
from slim_lims.results import SpecReader, publish_demo_rules  # noqa: E402

OSS_DEMO = Path(__file__).resolve().parents[2] / "slim-report-engine-oss" / "demo" / "demo.py"


@pytest.fixture(scope="module")
def issued(tmp_path_factory):
    if not OSS_DEMO.is_file():
        pytest.skip(f"OSS demo not found at {OSS_DEMO}")
    url = f"sqlite:///{tmp_path_factory.mktemp('reports') / 'demo.db'}"
    spec = importlib.util.spec_from_file_location("slim_oss_demo_for_reports", OSS_DEMO)
    demo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = demo
    spec.loader.exec_module(demo)
    demo.seed(url)  # SLIM's fictional reference data
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        migrate.upgrade(connection)
        invoicing_migrate.upgrade(connection)
        samples_migrate.upgrade(connection)  # the report reads the bottles' conditions
    with make_session_factory(engine)() as session:
        sync_analysis_catalog(engine, session)
        session.commit()
        reference = load_reference(engine, engine)
        publish_demo_rules(session, reference)
        sync_personas(session)
        session.commit()
        trs = seed_scenarios(session, reference, SpecReader(engine), renderer=reports.engine_renderer)
        session.commit()
        files = {
            name: [reports.stored(session, i.file_sha256) for i in issues_for_tr(session, tr)]
            for name, tr in trs.items()
        }
        hashes = {name: [i.file_sha256 for i in issues_for_tr(session, tr)] for name, tr in trs.items()}
        # below_mdl and cups_disagree are released but never issued: render what their reports would be
        for name in ("below_mdl", "cups_disagree"):
            tr = TRSubmissionRepository(session).get_by_tr_number(trs[name])
            files[name] = [
                reports.engine_renderer(session, tr, report_view(session, tr.tr_number), reference)
            ]
    engine.dispose()
    return files, hashes


def row(html, symbol):
    """The table row of an analyte, by its symbol cell."""
    return html.split(f'<td class="c3">{symbol}</td>')[1].split("</tr>")[0]


def test_the_lab_template_carries_the_released_results_and_mdl(issued):
    files, hashes = issued
    clean = files["clean_pass"][0]  # 36 Elements, IPA
    assert "EXAMPLE LAB" in clean and "Results report TR00001" in clean and "Analysis by ICPMS" in clean
    cu = row(clean, "Cu")
    assert '<td class="c4 value">10.100</td>' in cu and '<td class="c5 value">99%</td>' in cu
    assert '<td class="c6 value">0.050</td>' in cu  # the MDL, not ±0.100 (D-79)
    assert "value" not in row(clean, "Pb")  # not measured: blank, as on the template
    assert sha256(clean.encode()).hexdigest() == hashes["clean_pass"][0]
    assert "<dt>MDL</dt>" in clean and "<dt>QL</dt>" not in clean  # the key explains what is printed (D-80)
    assert "half delta" not in clean.lower() and "Decision rule" not in clean  # no spec on this TR


def test_anions_print_the_ql_and_below_mdl_in_blue(issued):
    files, _ = issued
    anions = files["below_mdl"][0]  # 5 Anions, two samples: a sheet each
    assert 'href="#sheet-2"' in anions
    f = row(anions, "F")
    assert "&lt; 0.050" in f and "below-mdl" in f and '<td class="c6 value below-mdl">0.167</td>' in f
    assert "<dt>QL</dt>" in anions and "<dt>MDL</dt>" not in anions  # the ion panels print the QL


def test_half_delta_is_printed_beside_the_spec_it_bears_on(issued):
    files, _ = issued
    report = files["cups_disagree"][0]  # Fe 0.295 against B's 0.3 IPA spec (slim-data D-80)
    assert (
        "Fe: close to the customer&#x27;s specification; half delta 0.010" in report
        and "<dt>Half delta</dt>" in report
    )
    assert "<dt>Decision rule</dt><dd>Simple acceptance" in report


def test_the_assay_report_and_its_amendment(issued):
    files, _ = issued
    first, amended = files["amended"]
    assert '<td class="c4 value">69.74</td>' in first and "(amended)" not in first
    assert '<td class="c4 value">69.55</td>' in amended and "Results report TR00008 (amended)" in amended
    assert "Amended: corrects a result reported before" in amended


def test_half_delta_is_noted_beside_the_spec_and_explained():
    """slim-data D-80: a line carries ½Δ only near a spec limit; the report then says what it is."""
    line = ReportLineView(
        sample=SampleRef(tr_number="TR00001", position=1),
        analyte=AnalyteRef(analysis_id=5, code="Fe"),
        version=1,
        text="10.050",
        reported=None,
        qualifier=Qualifier.DETECTED,
        uncertainty=Decimal("0.050"),
        recovery=None,
        mdl=Decimal("0.050"),
        half_delta=Decimal("0.050"),
        shown_flags=("ABOVE_SPEC",),
    )
    html = plain_renderer(
        None,
        SimpleNamespace(tr_number="TR00001"),
        ReportView(tr_number="TR00001", lines=(line,)),
        SimpleNamespace(analysis=lambda _id: None),
    )
    assert "Fe: above the customer&#x27;s specification; half delta 0.050" in html
    assert "<dt>Half delta</dt><dd>Half the difference between the two measurements" in html
    assert "<dt>MDL</dt>" in html


HOSTILE = '<script>alert("x")</script>'


def test_both_renderers_escape_what_people_typed():  # review A7: the CSP is the backstop, not the guard
    from slim_report_engine.reporting.html_report import render_html

    row = SimpleNamespace(values={1: HOSTILE}, notes={2: HOSTILE}, limits=None, merges=[], style_name="x")
    sheet = SimpleNamespace(name=HOSTILE, sections=[SimpleNamespace(id=1, rows=[row])])
    engine_html = render_html([sheet, sheet], title=HOSTILE)
    line = ReportLineView(
        sample=SampleRef(tr_number="TR00001", position=1),
        analyte=AnalyteRef(analysis_id=5, code="Fe"),
        version=1,
        text=HOSTILE,
        reported=None,
        qualifier=Qualifier.DETECTED,
        uncertainty=None,
        recovery=None,
        mdl=None,
        half_delta=None,
        shown_flags=(),
    )
    plain_html = plain_renderer(
        None,
        SimpleNamespace(tr_number="TR00001"),
        ReportView(tr_number="TR00001", lines=(line,)),
        SimpleNamespace(analysis=lambda _id: SimpleNamespace(name=HOSTILE)),
    )
    for html in (engine_html, plain_html):
        assert "<script" not in html and "&lt;script&gt;" in html
