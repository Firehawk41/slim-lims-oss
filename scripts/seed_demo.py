"""Seeds a DEMO database with the fictional SLIM reference data from the OSS repos.

    python scripts/seed_demo.py [--oss-report-engine ../slim-report-engine-oss]

What it does, in order:
  1. Runs the portal's migrations.
  2. If SLIM's reference tables are missing or empty, creates and fills them by running
     slim-report-engine-oss's own `demo/demo.py` seed() — fictional customers "A".."Z",
     IPA, HNO3, Acetone, H2O2, PGMEA, "Water", the periodic table and the supported analyses. If they
     already hold data they are LEFT UNTOUCHED: this script never writes into a populated
     SLIM database.
  3. Syncs the portal-owned analysis_catalog from slim_lab_portal/data/analysis_catalog.csv.
  4. Runs slim-data's migrations and publishes its demo metals ruleset (bound to 10 Elements by
     catalog code, D-70) and demo policy. Both are content-hashed: re-running changes nothing.
  5. Creates the demo personas (data/demo_personas.csv); offered only when DEMO_PERSONAS is on.
  6. Runs slim-data's named demo scenarios on real TRs (slim-data D-75), once: skipped when results
     exist, or with --no-scenarios.

Requires slim-domain and slim-report-engine (the OSS packages) to be installed, e.g.
    pip install -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss
The target is SLIM_DATABASE_URL, else DATABASE_URL — point it at a demo database only.
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url

import slim_lab_portal
from slim_lab_portal import slim_tables as t
from slim_lab_portal.audit import install_audit_log
from slim_lab_portal.catalog import sync_analysis_catalog
from slim_lab_portal.config import load_settings
from slim_lab_portal.db import make_engine, make_session_factory
from slim_lab_portal.oss_guard import assert_oss_database
from slim_lab_portal.demo_scenarios import seed_scenarios
from slim_lab_portal.personas import sync_personas
from slim_lab_portal.reference_cache import load_reference
from slim_lab_portal.results import SpecReader, migrate_slim_data, publish_demo_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OSS_DIR = REPO_ROOT.parent / "slim-report-engine-oss"


def _load_oss_demo(oss_dir: Path):
    demo_path = oss_dir / "demo" / "demo.py"
    if not demo_path.is_file():
        raise SystemExit(f"OSS demo seed not found at {demo_path}; pass --oss-report-engine")
    spec = importlib.util.spec_from_file_location("slim_oss_demo", demo_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses in demo.py look their module up here
    try:
        spec.loader.exec_module(module)
    except ImportError as e:
        raise SystemExit(f"{e}\nInstall the OSS packages first: pip install -e ../slim-domain-oss[postgres] "
                         "-e ../slim-report-engine-oss") from e
    return module


def _slim_tables_have_data(engine) -> bool:
    existing = set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        for table in (t.customers, t.chemicals, t.elements, t.analyses):
            if table.name in existing and conn.scalar(select(func.count()).select_from(table)):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--oss-report-engine", type=Path,
                        default=Path(os.environ.get("SLIM_REPORT_ENGINE_OSS_DIR", DEFAULT_OSS_DIR)),
                        help="checkout of slim-report-engine-oss (default: %(default)s)")
    parser.add_argument("--no-scenarios", action="store_true", help="skip slim-data's demo scenarios")
    args = parser.parse_args(argv)

    settings = load_settings()
    slim_url = settings.slim_database_url or settings.database_url
    print(f"Portal database: {make_url(settings.database_url).render_as_string(hide_password=True)}")
    print(f"SLIM database:   {make_url(slim_url).render_as_string(hide_password=True)}")

    config = Config(str(Path(slim_lab_portal.__file__).parent / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.upgrade(config, "head")

    portal_engine = make_engine(settings.database_url)
    slim_engine = make_engine(slim_url) if slim_url != settings.database_url else portal_engine

    if _slim_tables_have_data(slim_engine):
        print("SLIM reference tables already hold data -- left untouched.")
    else:
        assert_oss_database(slim_url)  # demo.seed() opens its own engine, outside make_engine
        demo = _load_oss_demo(args.oss_report_engine.resolve())
        if slim_engine.dialect.name == "postgresql":
            with slim_engine.begin() as conn:  # slim-domain's unique names are CITEXT on Postgres
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        demo.seed(slim_url)
        print(f"Seeded SLIM demo reference data from {args.oss_report_engine}.")

    with make_session_factory(portal_engine)() as session:
        report = sync_analysis_catalog(slim_engine, session)
        session.commit()
    print(f"analysis_catalog: {report.matched} analyses offered or mapped.")
    if report.not_in_slim:
        print(f"  {len(report.not_in_slim)} catalog entries not in this SLIM database (skipped): "
              + ", ".join(report.not_in_slim))
    if report.not_in_catalog:
        print(f"  {len(report.not_in_catalog)} SLIM analyses with no catalog entry (not offered): "
              + ", ".join(report.not_in_catalog))

    migrate_slim_data(portal_engine)
    audited = install_audit_log(portal_engine)  # before the scenarios, so their writes are chained (D-77)
    with make_session_factory(portal_engine)() as session:
        reference = load_reference(slim_engine, portal_engine)
        analyses = publish_demo_rules(session, reference)
        personas = sync_personas(session)
        session.commit()
        scenarios = {} if args.no_scenarios else seed_scenarios(session, reference, SpecReader(slim_engine))
        session.commit()
    print("slim-data: demo rulesets published for " + ", ".join(code for code, _ in analyses.values())
          + "; demo policy.")
    print(f"Audit log on {len(audited)} tables." if audited else "No audit log (not Postgres, or no SLIM tables).")
    print(f"{personas} demo personas (offered when DEMO_PERSONAS is on).")
    if scenarios:
        print("Demo scenarios: " + ", ".join(f"{name} {tr}" for name, tr in scenarios.items()))
    elif not args.no_scenarios:
        print("Demo scenarios: results exist already -- left untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
