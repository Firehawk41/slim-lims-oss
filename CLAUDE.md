# slim-lims-oss — Claude Context

> ## ⚠️ OSS ONLY — read first
> A **portfolio demo** on the **open-source** SLIM repos only (`slim-domain-oss`, `slim-report-engine-oss`,
> `slim-data-oss`, `slim-prep-oss`, `slim-quality-oss`) and their fictional demo data. **Never connect it
> to PreciLab (or any real lab) production databases, shares or services.** Enforced: `slim_lims/oss_guard.py` refuses any URL
> containing "precilab" and any host but localhost / 127.0.0.1 / `db` / `postgres` unless listed in
> `PORTAL_ALLOWED_DB_HOSTS`; every connection goes through `db.make_engine` (`tests/test_oss_guard.py`);
> `.env` is read from the current directory only. Do not weaken any of this.
>
> **Scope:** don't modify slim-domain / slim-report-engine without Jamie's explicit go-ahead, and never the
> private slim-lims (production); never sync production code here. Ideas for later go in the spec's
> "Parked ideas" (docs/SLIM_DOMAIN_V2.md §11) and are raised with Jamie, not built.

Jamie is the **designer**; Claude builds and explains. **Short replies; Jamie is token-constrained.**

## Documents

| File | What |
|---|---|
| `docs/ROADMAP.md` | Departments (Admin · Lab · Quality · IT), phases. Read first. |
| `docs/SLIM_DOMAIN_V2.md` | The domain spec. Read before touching `slim_lims/domain/`, TR tables, enums or statuses. |
| slim-data-oss `docs/DECISIONS.md` | Decisions D-01…, host ones included. |
| `docs/DEV_DIARY.md` | Who decided what, across every SLIM repo. Add a line per decision made, amended or argued. |
| `docs/REVIEW_CHECKLIST.md` | The lenses every review uses; past reviews in `docs/REVIEW_<date>.md`. |

## Working defaults (Jamie, 2026-10-28)

- **Batches:** settle design in a short turn first; then one approval covers the batch: build, one PR per
  repo, merge on green. Check CI once when it should be done; don't subscribe to PR events.
- **Quiet runs:** `pytest -q -p no:warnings`, summary lines only; silence migration logs in seeds.
- **Docs once per batch:** DECISIONS, diary, CLAUDE.md at the end, not per change.
- **Fresh session per batch.** CLAUDE.md says what is true now; history lives in DECISIONS, the diary, git.
- **Reviews:** a module review when a module is wired in, an app review every two or three modules, with
  `docs/REVIEW_CHECKLIST.md`. Every fixed finding ends in a guard test.

## What this is

SLIM's host app for an ISO 17025 analytical chemistry lab: the composition root and the screens for the
modules. Customers submit Testing Requests (TRs); staff check samples in, prep, import readings, review
and release results, issue reports and invoice; the lab board (`/`) shows it all. The request form
replaces the Excel TR form; emailed `.xlsx` forms stay supported by slim-domain's parsers.

**Modules (required libraries):** slim-data (results: import, rules engine, review, release, reports),
slim-prep (the prep book), slim-quality (QC charts and tools, change control), invoicing V5
(`slim_invoicing`), slim-domain (SLIM tables, audit log), slim-report-engine (the report template). Each keeps its own tables and Alembic version table in the
shared database; `cli migrate` and the seed run them all.

**Open:** deeper report-engine wiring (spec §13; changes other repos, needs the go-ahead); ecosystem
docker-compose for the VPS (**deferred, do not start**); the landing page (`/about`) is live with
placeholders; Jamie writes its words in `templates/about/_copy.html`. slim-quality part 1 is in (phase 3), and
part 2 has begun (vertical audit, nonconformities); roadmap phase 4 or the rest of phase 5 next (`docs/ROADMAP.md`).

## How to run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss
cp .env.example .env                     # DATABASE_URL defaults to sqlite:///slim_lims.db
python scripts/seed_demo.py              # every migration + OSS demo data + rules + personas + scenarios
PORTAL_PASSWORD='at-least-12-chars' python -m slim_lims.cli create-staff --email you@lab.example --initials JM --lab-role supervisor
uvicorn --factory slim_lims.main:create_app --reload   # http://127.0.0.1:8000, API docs at /api/docs
python -m slim_lims.cli verify           # replay every result, recheck the prep book, audit chain (Postgres)
pytest -q -p no:warnings                 # TEST_DATABASE_URL=postgresql+psycopg2://... for Postgres
ruff check . && ruff format --check .
```

`docker compose up --build`: Postgres 16 + a one-shot seed + the app with personas. **Render**
(`render.yaml`): a lab per visitor (`DEMO_LABS`, D-102): public is emptied once (`cli empty-public --yes
--if-needed`), the shared lab is `DATABASE_SCHEMA=lab_shared`, each visitor's first persona claims a
seeded schema of their own; a restart (a deploy) drops the spares and upgrades every claimed lab (D-115).
The service is `slim-lims-oss`, the database `slim-lims-db` (renaming it makes
Render create a second; the free plan allows one). CI: {SQLite, Postgres 16} × {3.11, 3.12}.

## File map

```
slim_lims/
├── main.py          # create_app: middleware, routers, error handlers, /health; app.state (labs, lab_clock, throttle)
├── config.py        # Settings from env (DATABASE_URL, DATABASE_SCHEMA, DEMO_PERSONAS, DEMO_LABS, LAB_TIMEZONE, ...)
├── db.py            # Base (app tables only), make_engine (the only way to connect), get_db (the visitor's lab)
├── oss_guard.py     # OSS-only guard (called by make_engine)
├── domain/          # spec §3–4: enums, tr (TRSubmission, ...), workflow (transitions); pure
├── models.py        # User, TR tables, tr_versions (append-only, D-104), user_capabilities, invoices, report_files
├── repositories.py  # TRSubmissionRepository: the only TR writer; gapless numbers; versions / as_of (D-104)
├── scope.py         # Scope.for_user: whose TRs a listing shows; fails closed (D-103)
├── services.py      # SubmissionService: submit, check-in, status (facts only, D-99), history
├── capabilities.py  # manage users: supervisors by default, grantable per person (D-100)
├── security.py      # bcrypt, CSRF, LoginThrottle (D-101), headers + body-size middleware
├── labtime.py       # LabClock: the lab's now / today (D-101)
├── labs.py          # a lab per visitor: Labs (claim, spares, sweep), with_schema (D-102)
├── demo_seed.py     # seed_demo(url): the whole demo seed (scripts/seed_demo.py and each lab); upgrade_lab (D-115)
├── auth.py          # current_user, require_*; /login, /logout, /demo/personas, fresh lab
├── reference.py, reference_cache.py, slim_tables.py, catalog.py  # SLIM reference data (read-only) + analysis catalog
├── bridge.py        # to_slim_v1: TR -> slim-domain v1 TRSubmission
├── results.py, results_routes.py  # slim-data host side; /results, /api/results (staff)
├── prep.py, prep_routes.py        # slim-prep host side (TrSamples: its TR port); /prep, /api/prep (staff)
├── quality.py, quality_routes.py  # slim-quality host side: QcSource over slim-data, the vertical audit (composed
│                                  #   from every module, D-113), NCs (D-114), demo QC history and NC; /quality
├── invoices.py, demo_prices.py    # invoicing V5; /invoices; the accounting CSV, formula cells as text
├── reports.py       # issued reports: engine_renderer / plain_renderer, report_files
├── board.py         # the lab board (spec §13)
├── about.py         # the landing page's facts (/about; its words: templates/about/_copy.html, Jamie's)
├── personas.py, demo_scenarios.py, demo_prep.py  # demo personas (quality ones hold a capability) and scenarios
├── api.py           # /api/submissions; service errors -> 422/403/404/409
├── routes.py, web.py, templates/, static/        # pages
├── audit.py         # slim-domain's audit log (Postgres; per lab schema)
├── cli.py           # migrate, verify, create-staff, create-customer-user, sync-*, empty-public
└── migrations/      # Alembic, app tables only
scripts/seed_demo.py; tests/ (SQLite per test, or Postgres via TEST_DATABASE_URL)
```

## Architecture rules

- **Layers:** `domain/` pure (liftable into slim-domain) ← input models ← repositories, the only code that
  touches TR tables. Modules flush; routes commit.
- **Data:** the app only reads SLIM's reference tables, never declares them on `db.Base`, no FKs into them.
  Portable schema (SQLite + Postgres): no ARRAY, enums as ints; TR tables integer keys, `users` UUIDs.
- **Access:** every listing takes a `Scope` (D-103); customers see only their customer's TRs (others 404).
  Roles: `customer` (needs `customer_id`) and `staff` (needs `initials`, `lab_role`). Managing users is a
  capability (D-100). Personas (`DEMO_PERSONAS`) are chosen, not logged in: ASSERTED, refused by /login.
  Staff act in modules as `results.actor_for(user)` / `prep.prep_actor_for(user)` /
  `quality.quality_actor(db, user)` (needs the *quality* or *quality manager* capability, D-109).
- **Machine access:** `Authorization: Bearer $LIMS_API_TOKEN` (spec §12), only `auth.LIMS_ROUTES`, no CSRF,
  no session. Keep `tests/test_lims_contract.py` passing.
- **CSRF:** every router has `dependencies=[Depends(csrf_protect)]`; forms send `csrf_token`, fetch sends
  `X-CSRF-Token`. `tests/test_app.py` sweeps every state-changing route.
- **CSP:** no `'unsafe-inline'`, no inline `<script>`; data via `data-*`. Script hosts: code.jquery.com,
  cdn.jsdelivr.net.
- **Errors:** 422 per field; HTML routes render `error.html`, `/api/*` JSON; unauthenticated HTML → 303
  `/login?next=`, API → 401; non-staff on staff routes → 403.
- **Dates:** "today" is the lab's (`LabClock`, `LAB_TIMEZONE`). **Routes are plain `def`.** 1 MB body cap.
- **Demo data only:** the seed never writes into SLIM tables that hold data.

## Key decisions (detail in the spec and DECISIONS)

- One request type (Chemical / Water / Wafer) per TR. Chemicals are customer free text, matched to SLIM by
  staff; check-in (receipt + every chemical matched) is the only way to *received*.
- Form UX: samples table + one grouped analysis panel for the ticked rows (the matrix layout was rejected).
- TR numbers `TR00001`… from a gapless counter locked in the submit transaction.
- Statuses: submitted → received → in progress → partial report → complete report → invoiced; cancelled.
  They follow the facts (D-99): report statuses only by issuing a report, invoiced only by sending the
  invoice; a cancel is refused once a report was issued or a result released. **Each time a module is
  wired, re-check which statuses are still set by hand.**
- Payment: PO number or credit card (the lab calls; no card data stored). RUSH = outside business hours.
- Every change to a TR is kept whole in `tr_versions` (D-104).
