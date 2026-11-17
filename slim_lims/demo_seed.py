"""Seeds a DEMO database: the app's migrations and every module's, SLIM's fictional reference data,
the demo rules, personas and the named scenarios. Demo databases only. `scripts/seed_demo.py` runs it
on DATABASE_URL; `labs.Labs` runs it on each visitor's own lab (slim-data D-102).

Steps, in order:
  1. The app's migrations.
  2. If SLIM's reference tables are missing or empty, creates and fills them by running
     slim-report-engine-oss's own `demo/demo.py` seed(): fictional customers "A".."Z", IPA, HNO3,
     Acetone, H2O2, PGMEA, "Water", the periodic table and the supported analyses. If they already hold
     data they are LEFT UNTOUCHED: this never writes into a populated SLIM database.
  3. Syncs the app-owned analysis_catalog from slim_lims/data/analysis_catalog.csv.
  4. slim-data's, slim-prep's and invoicing's migrations; the audit log (Postgres); slim-data's demo
     rulesets and policy, slim-prep's demo rules (content-hashed: re-running changes nothing).
  5. The demo personas (data/demo_personas.csv); offered only when DEMO_PERSONAS is on.
  6. slim-data's named demo scenarios on real TRs (D-75) and slim-prep's, once: skipped when results
     exist, or with scenarios=False.
"""

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from slim_prep.demo import DEMO_RULES
from sqlalchemy import func, inspect, select, text

import slim_lims
from slim_lims import slim_tables as t
from slim_lims.audit import install_audit_log
from slim_lims.catalog import SyncReport, sync_analysis_catalog
from slim_lims.db import make_engine, make_session_factory
from slim_lims.demo_prep import seed_prep_scenario
from slim_lims.demo_samples import seed_demo_bottles
from slim_lims.demo_scenarios import seed_scenarios
from slim_lims.invoices import migrate_invoicing
from slim_lims.labs import ensure_schema
from slim_lims.labtime import LabClock
from slim_lims.oss_guard import assert_oss_database
from slim_lims.personas import sync_personas
from slim_lims.prep import migrate_slim_prep, publish_demo_prep
from slim_lims.quality import (
    migrate_slim_quality,
    seed_demo_nonconformity,
    seed_quality_history,
    seed_quality_records,
)
from slim_lims.quality_insights import seed_demo_authorisations
from slim_lims.quality_records import DEMO_INSTRUMENTS, seed_demo_register
from slim_lims.quality_system import seed_demo_quality_system
from slim_lims.quotes import seed_demo_quote_request
from slim_lims.reference_cache import load_reference
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import SpecReader, migrate_slim_data, publish_demo_rules
from slim_lims.samples import migrate_slim_samples, publish_demo_samples
from slim_lims.today import seed_demo_sections

DEFAULT_OSS_DIR = Path(slim_lims.__file__).resolve().parents[2] / "slim-report-engine-oss"


@dataclass
class SeedReport:
    slim_seeded: bool = False
    catalog: SyncReport | None = None
    analyses: dict = field(default_factory=dict)
    audited: list[str] = field(default_factory=list)
    personas: int = 0
    scenarios: dict[str, str] = field(default_factory=dict)
    prepped: str | None = None
    bottles: int = 0
    qc_runs: int = 0
    records: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # demo steps that failed and were left out


log = logging.getLogger(__name__)


def _demo_step(session, skipped: list[str], name: str, step, default=None):
    """One demo-content step, committed on its own. A failure is logged and the step left out, never the
    start-up stopped: demo data is optional, the site is not (Render outage 2026-11-17). Migrations and
    the audit log are not demo steps: without them the app can't run, so they stay fatal."""
    try:
        result = step()
        session.commit()
        return result
    except Exception:
        session.rollback()
        log.exception("demo seed: %s failed and was left out", name)
        skipped.append(name)
        return default


def load_oss_demo(oss_dir: Path):
    """slim-report-engine-oss's demo/demo.py, loaded once."""
    if "slim_oss_demo" in sys.modules:
        return sys.modules["slim_oss_demo"]
    demo_path = oss_dir / "demo" / "demo.py"
    if not demo_path.is_file():
        raise FileNotFoundError(f"OSS demo seed not found at {demo_path}")
    spec = importlib.util.spec_from_file_location("slim_oss_demo", demo_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses in demo.py look their module up here
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


def slim_tables_have_data(engine) -> bool:
    existing = set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        for table in (t.customers, t.chemicals, t.elements, t.analyses):
            if table.name in existing and conn.scalar(select(func.count()).select_from(table)):
                return True
    return False


def seed_demo(
    database_url: str,
    slim_database_url: str | None = None,
    oss_dir: Path = DEFAULT_OSS_DIR,
    lab_timezone: str = "America/Chicago",
    scenarios: bool = True,
    leave_out: frozenset[str] = frozenset(),
) -> SeedReport:
    """``leave_out`` names demo steps not to run: the tests seed a database as an older seed left it."""
    report = SeedReport()
    slim_url = slim_database_url or database_url
    ensure_schema(database_url)  # a lab's schema (D-102); nothing otherwise
    config = Config(str(Path(slim_lims.__file__).parent / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")

    portal_engine = make_engine(database_url)
    slim_engine = make_engine(slim_url) if slim_url != database_url else portal_engine
    try:
        if not slim_tables_have_data(slim_engine):
            assert_oss_database(slim_url)  # demo.seed() opens its own engine, outside make_engine
            demo = load_oss_demo(oss_dir.resolve())
            if slim_engine.dialect.name == "postgresql":
                with slim_engine.begin() as conn:  # slim-domain's unique names are CITEXT on Postgres
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            demo.seed(slim_url)
            report.slim_seeded = True

        with make_session_factory(portal_engine)() as session:
            report.catalog = sync_analysis_catalog(slim_engine, session)
            TRSubmissionRepository(session).backfill_versions()  # TRs from before their history (D-104)
            session.commit()

        migrate_slim_data(portal_engine)
        migrate_slim_prep(portal_engine)
        migrate_slim_samples(portal_engine)
        migrate_slim_quality(portal_engine)
        migrate_invoicing(portal_engine)
        report.audited = install_audit_log(portal_engine)  # before the scenarios: their writes chained (D-77)
        clock = LabClock(ZoneInfo(lab_timezone))  # the demo's dates are the lab's (review A8)
        with make_session_factory(portal_engine)() as session:
            reference = load_reference(slim_engine, portal_engine)
            today, now = clock.today(), clock.now()

            def step(name, fn, default=None):
                if name in leave_out:
                    return default
                return _demo_step(session, report.skipped, name, fn, default)

            report.analyses = step("results rules", lambda: publish_demo_rules(session, reference), {}) or {}
            step("prep rules", lambda: publish_demo_prep(session, reference, today))
            step("bottle rules", lambda: publish_demo_samples(session))
            report.personas = step("personas", lambda: sync_personas(session), 0) or 0
            step(
                "equipment register", lambda: seed_demo_register(session, today, DEMO_INSTRUMENTS, DEMO_RULES)
            )
            step(
                "authorisations", lambda: seed_demo_authorisations(session, today)
            )  # D-132: before anyone acts
            step("risks and audits", lambda: seed_demo_quality_system(session, today))  # D-134, D-135
            step("the day's board", lambda: seed_demo_sections(session, reference))  # D-138
            step("a quote request", lambda: seed_demo_quote_request(session, reference))  # D-139
            if scenarios:
                specs = SpecReader(slim_engine, reference)
                report.scenarios = (
                    step("scenarios", lambda: seed_scenarios(session, reference, specs, now), {}) or {}
                )
                report.prepped = step(
                    "prep scenario", lambda: seed_prep_scenario(session, reference, specs, now)
                )
                report.bottles = step("bottles", lambda: seed_demo_bottles(session, reference, today), 0) or 0
                report.qc_runs = (
                    step("QC history", lambda: seed_quality_history(session, reference, specs, now), 0) or 0
                )
                step("a nonconformity", lambda: seed_demo_nonconformity(session, now))
                issued = report.scenarios.get("clean_pass")
                report.records = (
                    step(
                        "quality records",
                        lambda: seed_quality_records(session, reference, specs, now, issued),
                        {},
                    )
                    or {}
                )
    finally:
        portal_engine.dispose()
        slim_engine.dispose()
    return report


def upgrade_lab(database_url: str, lab_timezone: str = "America/Chicago") -> None:
    """Every migration (the app's and each module's) and the audit log, on a lab that already exists: what
    a deploy adds reaches a visitor's lab (slim-data D-115). Nothing is seeded but the equipment register
    and reference lots when a lab has none (D-127: making a standard needs a registered lot), and the
    authorisations, risks and audit programme likewise, and the day's board's sections (D-138); running
    it twice changes nothing."""
    config = Config(str(Path(slim_lims.__file__).parent / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    engine = make_engine(database_url)
    try:
        migrate_slim_data(engine)
        migrate_slim_prep(engine)
        migrate_slim_samples(engine)
        migrate_slim_quality(engine)
        migrate_invoicing(engine)
        install_audit_log(engine)
        with make_session_factory(engine)() as session:
            today, skipped = LabClock(ZoneInfo(lab_timezone)).today(), []
            # A lab from before each of these gets it; a failure leaves it out, never drops the lab.
            _demo_step(
                session,
                skipped,
                "equipment register",
                lambda: seed_demo_register(session, today, DEMO_INSTRUMENTS, DEMO_RULES),
            )
            _demo_step(
                session, skipped, "authorisations", lambda: seed_demo_authorisations(session, today)
            )  # D-132
            _demo_step(
                session, skipped, "risks and audits", lambda: seed_demo_quality_system(session, today)
            )  # D-134
            _demo_step(
                session,
                skipped,
                "the day's board",
                lambda: seed_demo_sections(session, load_reference(engine, engine)),
            )
    finally:
        engine.dispose()
