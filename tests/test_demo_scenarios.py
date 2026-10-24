"""slim-data's named demo scenarios on real portal TRs (slim-data D-75)."""

from decimal import Decimal
import pytest

from hashlib import sha256

from slim_lims import reports
from slim_lims.reports import plain_renderer
from fastapi.testclient import TestClient
from infrastructure import audit as slim_audit
from slim_domain.domain.report_log.report_log_repository import _ReportGenerationLogRow
from slim_data.service.verify import verify
from sqlalchemy import text
from slim_data.persistence import migrate
from slim_data.persistence.results import issues_for_tr
from slim_data.service.views import batch_view, report_view

from slim_lims import cli
from slim_lims.audit import chain_check, install_audit_log
from slim_lims.demo_scenarios import seed_scenarios
from slim_lims.domain import RequestType, TRStatus
from slim_lims.personas import sync_personas
from slim_lims.reference import AnalysisRef, ChemicalRef, CustomerRef, ElementRef, StaticReferenceData
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import publish_demo_rules
from tests.conftest import make_settings, postgres_only

ELEMENTS = {26: "Fe", 29: "Cu", 30: "Zn", 28: "Ni", 24: "Cr", 25: "Mn", 9: "F", 17: "Cl"}
SPECS = {(2, 2, ""): {"Zn": 0.2, "Fe": 0.3}}  # customer B, IPA, as in the OSS demo data
TEN, THIRTY_SIX, ANIONS, ASSAY = 1, 3, 6, 17  # the OSS demo data's IDs


@pytest.fixture
def reference():
    return StaticReferenceData.of(
        customers=[CustomerRef(1, "A", "1 A Street"), CustomerRef(2, "B", "2 B Street")],
        chemicals=[ChemicalRef(2, "IPA"), ChemicalRef(3, "HNO3")],
        elements=[ElementRef(i, s, s) for i, s in ELEMENTS.items()],
        analyses=[AnalysisRef(id, name, code, group, request_types=frozenset({RequestType.CHEMICAL}))
                  for id, name, code, group in ((TEN, "10 Elements", "10_elements_icpms", "Metals"),
                                                (THIRTY_SIX, "36 Elements", "36_elements_icpms", "Metals"),
                                                (ANIONS, "5 Anions", "5_anions", "Anions"),
                                                (ASSAY, "Assay", "assay", "Assay & titration"))],
    )


def specs(customer, chemical, location):
    return SPECS.get((customer, chemical, location), {})


@pytest.fixture
def seeded(engine, db, reference):
    with engine.begin() as connection:
        migrate.downgrade(connection)
        migrate.upgrade(connection)
    publish_demo_rules(db, reference)
    sync_personas(db)
    trs = seed_scenarios(db, reference, specs, renderer=plain_renderer)
    db.commit()
    return trs


def test_every_scenario_runs_on_its_own_received_tr(db, seeded):
    assert list(seeded) == ["clean_pass", "cups_disagree", "scale_fault", "pooled_spike", "below_mdl",
                            "rerun_exception", "accepted", "amended", "line_by_line"]
    assert list(seeded.values()) == [f"TR{n:05d}" for n in range(1, 10)]
    tr = TRSubmissionRepository(db).get_by_tr_number(seeded["cups_disagree"])
    assert tr.customer_id == 2 and tr.samples[0].analysis_ids == (TEN,) and tr.samples[0].chemical_name == "IPA"
    analyses = {name: TRSubmissionRepository(db).get_by_tr_number(number).samples[0].analysis_ids
                for name, number in seeded.items()}  # D-78
    assert analyses["clean_pass"] == (THIRTY_SIX,) and analyses["below_mdl"] == (ANIONS,)
    assay = TRSubmissionRepository(db).get_by_tr_number(seeded["amended"])
    assert assay.samples[0].analysis_ids == (ASSAY,) and assay.samples[0].chemical_name == "HNO3"


def test_statuses_follow_d71(db, seeded):
    status = {name: TRSubmissionRepository(db).get_by_tr_number(tr).status for name, tr in seeded.items()}
    assert status["amended"] == TRStatus.COMPLETE_REPORT
    assert status["clean_pass"] == status["rerun_exception"] == TRStatus.INVOICED  # the demo's invoices (spec §13)
    assert status["scale_fault"] == status["line_by_line"] == status["accepted"] == TRStatus.IN_PROGRESS


def test_results_and_issues_are_the_portals(db, seeded):
    lines = {line.analyte.code: line for line in report_view(db, seeded["cups_disagree"]).lines}
    assert lines["Zn"].shown_flags == ("ABOVE_SPEC",) and lines["Zn"].half_delta is None
    assert lines["Fe"].half_delta == Decimal("0.010")  # within ½Δ of B's 0.3 IPA spec (slim-data D-80)
    issues = issues_for_tr(db, seeded["amended"])
    assert [i.amends for i in issues] == [None, issues[0].id]
    # Each issue's hash is the stored report file's (slim-data D-79): served back exactly as issued.
    for issue in issues:
        html = reports.stored(db, issue.file_sha256)
        assert html is not None and sha256(html.encode()).hexdigest() == issue.file_sha256
    assert "(amended)" in reports.stored(db, issues[-1].file_sha256)
    analysts = {r.analyst_ids for r in batch_view(db, f"PB-{seeded['clean_pass'][2:]}").results}
    assert len(analysts) == 1  # the analyst persona (file analyst and uploader)


def test_runs_once(db, seeded, reference):
    assert seed_scenarios(db, reference, specs, renderer=plain_renderer) == {}


def test_a_visitor_sees_the_history(engine, reference, seeded):
    from slim_lims.main import create_app

    app = create_app(make_settings(str(engine.url), demo_personas=True), engine=engine, reference=reference,
                     specs=specs, report_renderer=plain_renderer)
    with TestClient(app) as client:
        page = client.get("/demo/personas").text
        rob = page.split('name="user_id" value="')[2].split('"')[0]  # customer, ana, rob, sue
        token = page.split('name="csrf_token" value="')[1].split('"')[0]
        client.post("/demo/continue-as", data={"csrf_token": token, "user_id": rob})
        assert "PB-00009" in client.get("/results").text
        report = client.get(f"/results/reports/{seeded['amended']}").text
        assert "Amended" in report and "(amends" in report


def test_every_scenario_replays_and_cli_verify_says_so(engine, seeded, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    assert cli.main(["verify"]) == 0
    out = capsys.readouterr().out
    assert "result versions: all match." in out
    if engine.dialect.name == "sqlite":
        assert install_audit_log(engine) == [] and chain_check(engine) is None
        assert "Audit chain: not checked" in out


def _remove_audit_log(engine):
    with engine.begin() as connection:
        for table in slim_audit.audited_tables(connection):
            connection.execute(text(f'DROP TRIGGER IF EXISTS zz_audit ON "{table}"'))
            connection.execute(text(f'DROP TRIGGER IF EXISTS zz_audit_truncate ON "{table}"'))
        for table in slim_audit.LOG_TABLES:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))


@pytest.fixture
def audited(engine, db):
    """slim-domain's audit log over every table, removed afterwards so other tests' tables stay plain.
    It needs SLIM's report log, which the demo seed's SLIM data brings; here the test creates it."""
    _remove_audit_log(engine)  # left by an interrupted run
    assert install_audit_log(engine) == []  # no SLIM tables yet: nothing installed
    _ReportGenerationLogRow.__table__.create(engine)
    yield install_audit_log(engine)
    db.rollback()  # end the test's transaction: dropping triggers waits for its locks
    _remove_audit_log(engine)


@postgres_only
def test_on_postgres_the_scenarios_are_chained_and_verify_checks_the_chain(engine, db, reference, audited):
    with engine.begin() as connection:  # slim-data's tables again, then the log over them too (D-77)
        migrate.downgrade(connection)
        migrate.upgrade(connection)
    install_audit_log(engine)
    publish_demo_rules(db, reference)
    sync_personas(db)
    seed_scenarios(db, reference, specs, renderer=plain_renderer)
    db.commit()
    assert {"tr_submissions", "users"} <= set(audited)
    logged = db.execute(text("SELECT count(*) FROM audit_log WHERE table_name = 'result_versions'")).scalar()
    report = verify(db, chain_check(engine))
    assert logged and report.chain_checked and report.ok and report.broken_entries == ()
