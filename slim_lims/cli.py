"""Command-line admin tasks.

    python -m slim_lims.cli migrate
    python -m slim_lims.cli create-staff --email lab@example.com --initials JM --lab-role supervisor
    python -m slim_lims.cli create-customer-user --email jane@acme.example --customer-id 3
    python -m slim_lims.cli sync-analysis-catalog
    python -m slim_lims.cli sync-personas        # demo sandbox only (DEMO_PERSONAS)
    python -m slim_lims.cli verify               # replay every result + check the audit chain
    python -m slim_lims.cli empty-public --yes [--if-needed]  # demo labs only: public keeps no app tables

The password comes from the PORTAL_PASSWORD environment variable, or an interactive prompt.
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from slim_lims.audit import chain_check, install_audit_log
from slim_lims.catalog import sync_analysis_catalog
from slim_lims.config import load_settings
from slim_lims.db import make_engine, make_session_factory
from slim_lims.invoices import migrate_invoicing
from slim_lims.labs import ensure_schema, without_schema
from slim_lims.models import LAB_ROLES, ROLE_CUSTOMER, ROLE_STAFF, User
from slim_lims.personas import sync_personas
from slim_lims.prep import migrate_slim_prep
from slim_lims.quality import migrate_slim_quality
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import migrate_slim_data
from slim_lims.security import hash_password


def _password() -> str:
    password = os.environ.get("PORTAL_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters.")
    return password


def _create_user(
    email: str, role: str, *, initials: str | None, customer_id: int | None, lab_role: int | None = None
) -> None:
    settings = load_settings()
    session_factory = make_session_factory(make_engine(settings.database_url))
    email = email.strip().lower()
    with session_factory() as db:
        if db.scalars(select(User).where(User.email == email)).first():
            raise SystemExit(f"A user with email {email} already exists.")
        if initials and db.scalars(select(User).where(User.initials == initials)).first():
            raise SystemExit(f"Initials {initials} are taken: they identify the analyst in result files.")
        db.add(
            User(
                email=email,
                password_hash=hash_password(_password()),
                role=role,
                initials=initials,
                customer_id=customer_id,
                lab_role=lab_role,
            )
        )
        db.commit()
    print(f"Created {role} user {email}.")


def _sync_catalog() -> int:
    settings = load_settings()
    portal_engine = make_engine(settings.database_url)
    slim_engine = make_engine(settings.slim_database_url) if settings.slim_database_url else portal_engine
    with make_session_factory(portal_engine)() as db:
        report = sync_analysis_catalog(slim_engine, db)
        db.commit()
    print(
        f"{report.matched} analyses mapped; {len(report.not_in_slim)} catalog entries not in SLIM; "
        f"{len(report.not_in_catalog)} SLIM analyses without a catalog entry."
    )
    return 0


def _sync_personas() -> int:
    settings = load_settings()
    with make_session_factory(make_engine(settings.database_url))() as db:
        count = sync_personas(db)
        db.commit()
    print(f"{count} demo personas in place (offered only when DEMO_PERSONAS is on).")
    return 0


def _migrate() -> int:
    """The app's migrations, then slim-data's and invoicing's (each its own tables and version table),
    then the audit log over every table on Postgres (slim-data D-77)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    import slim_lims

    settings = load_settings()
    ensure_schema(settings.database_url)  # DATABASE_SCHEMA's lab (D-102); nothing otherwise
    config = Config(str(Path(slim_lims.__file__).parent / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.upgrade(config, "head")
    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session:  # TRs from before their history (D-104)
        backfilled = TRSubmissionRepository(session).backfill_versions()
        session.commit()
    if backfilled:
        print(f"{backfilled} requests given their current state as version 1.")
    migrate_slim_data(engine)
    migrate_slim_prep(engine)
    migrate_slim_quality(engine)
    migrate_invoicing(engine)
    audited = install_audit_log(engine)
    print(
        "slim-lims, slim-data, slim-prep and invoicing migrations applied."
        + (
            f" Audit log on {len(audited)} tables."
            if audited
            else " No audit log (not Postgres, or no SLIM tables yet)."
        )
    )
    return 0


def _verify() -> int:
    """slim-data's verify (D-76), slim-prep's (P-22) and slim-quality's (app review 2026-10-30b Q7): exit 1
    if a result doesn't replay, the audit chain is broken, the prep book or the quality records don't hold."""
    from slim_data.service.verify import verify
    from slim_prep.service.verify import verify as verify_prep
    from slim_quality.service.verify import verify as verify_quality

    engine = make_engine(load_settings().database_url)
    with make_session_factory(engine)() as session:
        report = verify(session, chain_check(engine))
        prep = verify_prep(session)
        quality = verify_quality(session)
    print(
        f"Replayed {report.versions} result versions: "
        + ("all match." if not report.mismatches else f"{len(report.mismatches)} don't match:")
    )
    for mismatch in report.mismatches:
        print(f"  {mismatch}")
    if not report.chain_checked:
        print("Audit chain: not checked (no audit log on this database).")
    elif report.broken_entries:
        print(f"Audit chain: BROKEN, entries {', '.join(map(str, report.broken_entries))}.")
    else:
        print("Audit chain: intact.")
    print(
        f"Prep book: {prep.standards} standard and {prep.batches} batch versions, {prep.sent} sent: "
        + ("all hold." if prep.ok else f"{len(prep.problems)} problems:")
    )
    for problem in prep.problems:
        print(f"  {problem}")
    print(
        f"Quality: {quality.profiles} profile versions, {quality.limit_sets} limit sets, "
        f"{quality.nonconformities} nonconformities: "
        + ("all hold." if quality.ok else f"{len(quality.problems)} problems:")
    )
    for problem in quality.problems:
        print(f"  {problem}")
    return 0 if report.ok and prep.ok and quality.ok else 1


def _empty_public(confirmed: bool, if_needed: bool = False) -> int:
    """Demo labs (D-102): drops and recreates the public schema, so no lab sees an old app table
    through its search path. Everything in public is lost: the shared lab lives in DATABASE_SCHEMA.
    `if_needed`: only while public holds app tables (Render runs it on every start; the first wipes)."""
    from sqlalchemy import inspect, text

    settings = load_settings()
    engine = make_engine(without_schema(settings.database_url))  # OSS demo databases only
    if engine.dialect.name != "postgresql":
        print("empty-public is for Postgres demo labs only.", file=sys.stderr)
        return 1
    tables = inspect(engine).get_table_names(schema="public")
    if if_needed and not set(tables) - {"demo_labs"}:
        print("public holds no app tables: nothing to empty.")
        return 0
    if not confirmed:
        print(f"Would drop the public schema and its {len(tables)} tables. Run again with --yes.")
        return 1
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext SCHEMA public"))
    print(f"public emptied ({len(tables)} tables dropped).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slim_lims.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    staff = sub.add_parser("create-staff", help="Create a lab staff (admin) user")
    staff.add_argument("--email", required=True)
    staff.add_argument("--initials", required=True)
    staff.add_argument("--lab-role", choices=[r.lower() for r in LAB_ROLES.values()], default="analyst")

    customer = sub.add_parser("create-customer-user", help="Create a user for an existing SLIM customer")
    customer.add_argument("--email", required=True)
    customer.add_argument("--customer-id", required=True, type=int)

    sub.add_parser("sync-analysis-catalog", help="Refill analysis_catalog from data/analysis_catalog.csv")
    sub.add_parser("migrate", help="Apply the app's, slim-data's and invoicing's database migrations")
    sub.add_parser("sync-personas", help="Create/update the demo personas from data/demo_personas.csv")
    sub.add_parser("verify", help="Replay every stored result and check the audit chain (slim-data D-76)")
    empty = sub.add_parser("empty-public", help="Demo labs: drop and recreate the public schema (D-102)")
    empty.add_argument("--yes", action="store_true", help="really drop it")
    empty.add_argument("--if-needed", action="store_true", help="only while public holds app tables")

    args = parser.parse_args(argv)
    if args.command == "sync-analysis-catalog":
        return _sync_catalog()
    if args.command == "migrate":
        return _migrate()
    if args.command == "sync-personas":
        return _sync_personas()
    if args.command == "empty-public":
        return _empty_public(args.yes, args.if_needed)
    if args.command == "verify":
        return _verify()
    if args.command == "create-staff":
        lab_role = next(k for k, v in LAB_ROLES.items() if v.lower() == args.lab_role)
        _create_user(
            args.email,
            ROLE_STAFF,
            initials=args.initials.strip().upper(),
            customer_id=None,
            lab_role=lab_role,
        )
    else:
        _create_user(args.email, ROLE_CUSTOMER, initials=None, customer_id=args.customer_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
