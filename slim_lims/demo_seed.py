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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text

import slim_lims
from slim_lims import slim_tables as t
from slim_lims.audit import install_audit_log
from slim_lims.catalog import SyncReport, sync_analysis_catalog
from slim_lims.db import make_engine, make_session_factory
from slim_lims.demo_prep import seed_prep_scenario
from slim_lims.demo_scenarios import seed_scenarios
from slim_lims.invoices import migrate_invoicing
from slim_lims.labs import ensure_schema
from slim_lims.labtime import LabClock
from slim_lims.oss_guard import assert_oss_database
from slim_lims.personas import sync_personas
from slim_lims.prep import migrate_slim_prep, publish_demo_prep
from slim_lims.quality import migrate_slim_quality, seed_demo_nonconformity, seed_quality_history
from slim_lims.reference_cache import load_reference
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import SpecReader, migrate_slim_data, publish_demo_rules

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
    qc_runs: int = 0


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
) -> SeedReport:
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
        migrate_slim_quality(portal_engine)
        migrate_invoicing(portal_engine)
        report.audited = install_audit_log(portal_engine)  # before the scenarios: their writes chained (D-77)
        clock = LabClock(ZoneInfo(lab_timezone))  # the demo's dates are the lab's (review A8)
        with make_session_factory(portal_engine)() as session:
            reference = load_reference(slim_engine, portal_engine)
            report.analyses = publish_demo_rules(session, reference)
            publish_demo_prep(session, reference, clock.today())
            report.personas = sync_personas(session)
            session.commit()
            if scenarios:
                specs = SpecReader(slim_engine)
                report.scenarios = seed_scenarios(session, reference, specs, clock.now())
                session.commit()
                report.prepped = seed_prep_scenario(session, reference, specs, clock.now())
                session.commit()
                report.qc_runs = seed_quality_history(session, reference, specs, clock.now())
                seed_demo_nonconformity(session, clock.now())
                session.commit()
    finally:
        portal_engine.dispose()
        slim_engine.dispose()
    return report


def upgrade_lab(database_url: str) -> None:
    """Every migration (the app's and each module's) and the audit log, on a lab that already exists: what
    a deploy adds reaches a visitor's lab (slim-data D-115). Nothing is seeded; running it twice changes
    nothing."""
    config = Config(str(Path(slim_lims.__file__).parent / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    engine = make_engine(database_url)
    try:
        migrate_slim_data(engine)
        migrate_slim_prep(engine)
        migrate_slim_quality(engine)
        migrate_invoicing(engine)
        install_audit_log(engine)
    finally:
        engine.dispose()
