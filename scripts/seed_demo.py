"""Seeds a DEMO database with the fictional SLIM reference data from the OSS repos.

    python scripts/seed_demo.py [--oss-report-engine ../slim-report-engine-oss]

What it does, in order:
  1. Runs the app's migrations.
  2. If SLIM's reference tables are missing or empty, creates and fills them by running
     slim-report-engine-oss's own `demo/demo.py` seed() — fictional customers "A".."Z",
     IPA, HNO3, Acetone, H2O2, PGMEA, "Water", the periodic table and the supported analyses. If they
     already hold data they are LEFT UNTOUCHED: this script never writes into a populated
     SLIM database.
  3. Syncs the app-owned analysis_catalog from slim_lims/data/analysis_catalog.csv.
  4. Runs slim-data's migrations and publishes its demo metals ruleset (bound to 10 Elements by
     catalog code, D-70) and demo policy. Both are content-hashed: re-running changes nothing.
  5. Creates the demo personas (data/demo_personas.csv); offered only when DEMO_PERSONAS is on.
  6. Runs slim-data's named demo scenarios on real TRs (slim-data D-75), once: skipped when results
     exist, or with --no-scenarios.

Requires slim-domain and slim-report-engine (the OSS packages) to be installed, e.g.
    pip install -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss
The target is SLIM_DATABASE_URL, else DATABASE_URL — point it at a demo database only.
The work is `slim_lims.demo_seed.seed_demo` (also run per visitor lab, slim-data D-102).
"""

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from slim_lims.config import load_settings
from slim_lims.demo_seed import DEFAULT_OSS_DIR, seed_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--oss-report-engine",
        type=Path,
        default=Path(os.environ.get("SLIM_REPORT_ENGINE_OSS_DIR", DEFAULT_OSS_DIR)),
        help="checkout of slim-report-engine-oss (default: %(default)s)",
    )
    parser.add_argument("--no-scenarios", action="store_true", help="skip slim-data's demo scenarios")
    args = parser.parse_args(argv)

    settings = load_settings()
    slim_url = settings.slim_database_url or settings.database_url
    print(f"Database: {make_url(settings.database_url).render_as_string(hide_password=True)}")
    print(f"SLIM database:   {make_url(slim_url).render_as_string(hide_password=True)}")
    try:
        report = seed_demo(
            settings.database_url,
            settings.slim_database_url,
            args.oss_report_engine,
            settings.lab_timezone,
            scenarios=not args.no_scenarios,
        )
    except (FileNotFoundError, ImportError) as e:
        raise SystemExit(
            f"{e}\nPass --oss-report-engine, and install the OSS packages: "
            "pip install -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss"
        ) from e
    print(
        f"Seeded SLIM demo reference data from {args.oss_report_engine}."
        if report.slim_seeded
        else "SLIM reference tables already hold data -- left untouched."
    )
    catalog = report.catalog
    print(f"analysis_catalog: {catalog.matched} analyses offered or mapped.")
    if catalog.not_in_slim:
        print(
            f"  {len(catalog.not_in_slim)} catalog entries not in this SLIM database (skipped): "
            + ", ".join(catalog.not_in_slim)
        )
    if catalog.not_in_catalog:
        print(
            f"  {len(catalog.not_in_catalog)} SLIM analyses with no catalog entry (not offered): "
            + ", ".join(catalog.not_in_catalog)
        )
    print(
        "slim-data: demo rulesets published for "
        + ", ".join(code for code, _ in report.analyses.values())
        + "; demo policy."
    )
    print(
        f"Audit log on {len(report.audited)} tables."
        if report.audited
        else "No audit log (not Postgres, or no SLIM tables)."
    )
    print("slim-prep: demo prep rules, stock and the metals rules under its prep methods.")
    if report.prepped:
        print(
            f"slim-prep demo: an evaporation batch prepped, run, released and corrected on {report.prepped}."
        )
    print(f"{report.personas} demo personas (offered when DEMO_PERSONAS is on).")
    if report.scenarios:
        print("Demo scenarios: " + ", ".join(f"{name} {tr}" for name, tr in report.scenarios.items()))
    elif not args.no_scenarios:
        print("Demo scenarios: results exist already -- left untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
