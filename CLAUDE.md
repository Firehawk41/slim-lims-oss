# slim-lims-oss — Claude Context

> ## ⚠️ OSS ONLY — read first
> This app is a **portfolio demo** that uses the **open-source** SLIM repos only:
> `slim-domain-oss`, `slim-report-engine-oss`, `slim-data-oss` and their fictional demo data.
> **Never connect it to PreciLab (or any real lab) production databases, shares or services.**
> Enforced in code: `slim_lims/oss_guard.py` refuses any database URL containing
> "precilab" (no override) and any host other than localhost / 127.0.0.1 / `db` / `postgres`
> unless listed in `PORTAL_ALLOWED_DB_HOSTS`. Every connection goes through `db.make_engine`;
> `tests/test_oss_guard.py` fails if anything calls `create_engine` elsewhere. `.env` is read
> from the current directory only. Do not weaken any of this.
>
> **Scope:** modular but narrow. Don't modify slim-domain / slim-report-engine yet, and never the
> private slim-lims (production) — this app must fit them, not the other way round. Ideas worth having later go in the spec's
> "Parked ideas" list (docs/SLIM_DOMAIN_V2.md §11) and get raised with the user, not built.

**Roadmap:** `docs/ROADMAP.md` (departments Admin · Lab · Quality · IT, phases, portfolio). Read it first.

## What This Is

SLIM's host app (OSS demo) for an ISO 17025 accredited analytical chemistry lab: the composition
root and the screens for the modules (slim-domain, slim-data, slim-report-engine, invoicing V5).
Customers log in and submit Testing Requests (TRs); staff check samples in, upload readings, review
and release results, issue reports and invoice; the lab board (`/`) shows it all. Its request form
replaces the Excel Testing Request form as the primary intake path; emailed `.xlsx` forms stay
supported by slim-domain's parsers. The app never produces Excel — it produces TR domain objects.

**The domain spec is `docs/SLIM_DOMAIN_V2.md`. Read it before changing anything under
`slim_lims/domain/`, the TR tables, enums or statuses.**

## Rebuild status

This repo is SLIM's host app, open-source demo (Jamie, 2026-10-24): LabPortal (a Flask form, rebuilt
on FastAPI) was folded into `slim-lims-oss`, and its package renamed `slim_lims`. LabPortal's history is
merged in. The VBA-facing report API this repo used to mirror from the lab's **private, production**
slim-lims is retired here (git history only); never sync production code into this repo again, and
never change the private slim-lims from here. Env vars keep their `PORTAL_` names (Render's settings use them).

1. ✅ Spec: `docs/SLIM_DOMAIN_V2.md`
2. ✅ FastAPI skeleton: config, SQLAlchemy 2, Alembic, session auth, CSRF, security headers, pytest
3. ✅ Domain objects (pydantic, per spec §3–4), input models, repository, TR number counter, JSON API
4. ✅ Reference-data cache over SLIM tables, analysis catalog, `scripts/seed_demo.py` (OSS demo data)
5. ✅ (A) Bridge: portal-side adapter TRSubmission v2 -> slim-domain-oss v1 TRSubmission, plus a
   test feeding it to slim-report-engine-oss's report builder on OSS demo data. Portal only.
   `slim_lims/bridge.py` (`to_slim_v1`, received TRs only); `tests/test_bridge.py` renders
   Chemical/Water/Wafer reports with the engine's own writer (skipped without the OSS packages).
6. ✅ (B) Screens, reviewed with the user: customer form (per-request-type fields, choices from
   /api/reference), request list/detail, staff receive + status changes + chemical matching, users,
   JSON view. routes.py pages, templates/requests/*, admin/home + users, static/js/request_form.js
   (vanilla JS), static/css/portal.css, e2e/request_flow.js. Not done (parked): customer profiles
   (saved defaults, SX5-style locations) — the form pre-fills from the customer's last request.
7. ◐ (D) Deeper connectivity with the other repos. Done: real reports (spec §13 "The report itself", slim-data
   D-79): `reports.py` renders slim-report-engine's template as HTML filled with released results (MDL, not ±),
   stored in `report_files`, issue hash = file hash; ½Δ only near a spec + a key (D-80); the batch page shows
   every cup and links each result's trace (D-81). Still open (report engine accepts a submission object,
   slim-lims "generate report for TR", slim-domain adopts v2, status flows back). Modifies other
   repos — **needs the user's explicit go-ahead.** Includes: the report engine should print actual
   results from the DB instead of a blank report, generated once results exist ("data is in") —
   to be designed in a separate conversation.
8. ⏸ (C) Ecosystem docker-compose (the app + Postgres + Caddy) for the portfolio VPS.
   **DEFERRED — do not start.** Waits until the other modules are ready to bolt in, so the demo
   launches with several modules and can take one submission end to end: submission → receipt →
   analysis/report → delivery → invoice.
9. ✅ Link-ready (portal only): shared-Postgres `docker-compose.yml` with a seed step; slim-lims bearer
   token (`LIMS_API_TOKEN`, `auth.ServiceClient`, `ActorType.SYSTEM`, limited to list/get/status and to
   in progress / partial / complete report); contract in spec §12 + `tests/test_lims_contract.py`.
10. ✅ slim-data (results module) wiring — slim-data's M6 part 2, decisions in slim-data-oss
   `docs/DECISIONS.md` D-70, three slices. Slice 1 ✅: `results.py` (ReferenceLookup / StaffDirectory over
   portal + SLIM tables, actor_for, demo ruleset bound to `10_elements_icpms` by code), `results_routes.py`
   (/results upload preview/import → evaluate, batch page, /api/results), staff `lab_role` (supervisor sets
   roles), `cli migrate`. Slice 2 ✅: batch page + API actions (review batch, accept, rerun, exception,
   reject, correct, re-evaluate, release per sample + analysis; 409 when role / four-eyes / state refuse);
   first readings move a received TR to in progress (D-71; a release never moves the TR); reason categories
   from the policy's list (D-72). Slice 3: /results/reports/{tr} + "Issue report" ✅ (D-73: sha256 of the
   canonical report JSON until the report engine is wired; TR → partial / complete report). Personas ✅
   (D-74: `personas.py`, `users.is_persona`, `DEMO_PERSONAS`, /demo/personas; ASSERTED; no user admin).
   Demo scenarios ✅ (D-75: `demo_scenarios.py` is slim-data's DemoHost; the seed runs the nine scenarios
   once on real TRs). Walkthrough ✅.
11. slim-data M7: `audit.py` installs slim-domain's hash-chained audit log over every table on Postgres
   (`cli migrate` and the seed, once SLIM's tables exist; slim-data D-77; slim-domain is now required);
   `cli verify` runs slim-data's `verify` with slim-domain's `verify_chain` (D-76), exit 1 on a problem. Walkthrough ✅.
12. slim-data D-78: four demo analyses. `results.DEMO_ANALYSES` binds slim-data's demo rulesets by catalog
   code (10 and 36 Elements share the metals rules; anions on 5 Anions; acid assay, RSD < 1 %), and
   `OTHER_ANALYTES` lets NO3 / SO4 / Assay through the lookup. The OSS demo chemicals are now IPA, HNO3,
   Acetone, H2O2, PGMEA (slim-report-engine-oss); the demo's assay TRs are HNO3, the rest IPA.
13. ✅ Lab board (spec §13): `board.py` + `templates/board.html` is `/` for everyone. Columns follow TR status +
   slim-data results; chips per sample × analysis; supervisor-first actions; customers read-only, plain columns,
   and their issued reports (`/requests/{tr}/reports`). Invoice sent / paid: `invoices.py` + `tr_invoices` (payment is
   invoice data, not a TR status); the invoicing module will write those rows (item 14).
14. ✅ Invoicing, integral to the system (Jamie): SLIM-Invoicing-Program's V5 (`V5-LIMS-Module`, package
   `slim_invoicing`, slim-data D-83/D-84) is a required library. Slice 1 ✅ the library; slice 2 ✅ here:
   `invoices.mark_sent` builds V5's `BillableRequest` (TR + report issues) and V5 prices it into a sales order;
   `/requests/{tr}/invoice`, `/invoices` + `/invoices.csv`; `demo_prices.py` (the seed's default price list);
   `migrate_invoicing` in `cli migrate` / the seed. Slice 3 ✅: customer A's own quote in the demo (10 % off,
   45-day terms) and the price lists on `/invoices` (`invoices.price_lists`); "Mark paid" stays a portal row.

15. slim-data D-88 / D-89, instrument data in: /results takes a CSV, a Thermo Qtegra export (prep batch, analysis
   and prep method from the form; the importer is the analyst) or a table pasted from Excel
   (`results.READERS`, `make_reader`, `import_analyses`); the preview lists what was left out on purpose.
   The API takes the same choice (`kind`, `prep_batch`, `analysis`, `prep_method` query parameters).

16. ◐ slim-prep (the prep book, `slim-prep-oss` P6), a required library: `prep.py` (host side: `prep_actor_for`,
   `publish_demo_prep` = demo prep rules + stock + the metals rules under each prep method, `chemicals` for the
   run list, `send` = register or send the correction, then re-evaluate) and `prep_routes.py`: /prep (batches,
   standards book, make a standard), /prep/new-batch (the grid: one row per cup), /prep/batches/{code} (figures,
   versions, "Send to results", run list CSV), /api/prep/*. Nav: Prep. `cli migrate` and the seed run its
   migrations. Slice 1 ✅; next: correcting a batch on screen (the API does it), demo scenarios.
   slim-data and slim-prep are pinned to `claude/intelligent-planck-fhq1bc` until merged, then `@main`.

Done: A, B, link-ready (9), the board (13), invoicing (14), instrument data in (15). What comes next follows `docs/ROADMAP.md`;
C last. The user is token-constrained: small, focused steps,
short replies.

## How to Run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                     # DATABASE_URL defaults to sqlite:///slim_lims.db
pip install -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss   # OSS repos, for the demo seed
python scripts/seed_demo.py              # migrations (app + slim-data + invoicing) + OSS demo SLIM data + catalog + demo ruleset
PORTAL_PASSWORD='at-least-12-chars' python -m slim_lims.cli create-staff --email you@lab.example --initials JM --lab-role supervisor
uvicorn --factory slim_lims.main:create_app --reload   # http://127.0.0.1:8000, API docs at /api/docs
python -m slim_lims.cli verify     # replay every result + check the audit chain (Postgres)
pytest                                   # TEST_DATABASE_URL=postgresql+psycopg2://... pytest  to run on Postgres
```

Or `docker compose up --build`: Postgres 16 shared like the ecosystem (SLIM demo tables + the app's
tables), a one-shot `seed` service (needs `../slim-domain-oss` and `../slim-report-engine-oss`), and the
app on :8000 with demo personas on (`/demo/personas`). Set `LIMS_API_TOKEN` to let slim-lims call the API.
Cloud demo: `render.yaml` (Blueprint; the seed runs on every start and is idempotent). CI:
`.github/workflows/ci.yml` ({SQLite, Postgres 16} × {3.11, 3.12}).

## File Map

```
slim_lims/
├── main.py          # create_app(settings, engine): middleware, routers, exception handlers, /health
├── config.py        # Settings from env (APP_ENV, SECRET_KEY, DATABASE_URL, ...)
├── db.py            # Base (app-owned tables only), make_engine (the only way to connect), get_db
├── oss_guard.py     # OSS-ONLY guard: refuses "precilab" URLs and non-demo hosts (called by make_engine)
├── domain/          # Spec §3–4: enums.py, tr.py (TRSubmission, TRSample, ...), workflow.py (transitions)
├── models.py        # SQLAlchemy models: User + TR tables (tr_submissions, tr_samples, ...)
├── repositories.py  # TRSubmissionRepository: domain <-> rows, gapless TR numbers, stale-update check
├── schemas.py       # Input models (SubmissionIn, SampleIn, StatusChangeIn) — shape/type validation only
├── services.py      # SubmissionService: reference-data rules, access rules, submit/list/get/change_status
├── reference.py     # ReferenceData protocol, StaticReferenceData (snapshot + form choices)
├── reference_cache.py  # ReferenceCache: loads SLIM tables + analysis_catalog, TTL refresh, keeps last good copy
├── slim_tables.py   # Read-only Core defs of SLIM's customers/chemicals/elements/analyses (own MetaData)
├── catalog.py       # sync_analysis_catalog: data/analysis_catalog.csv -> analysis_catalog, matched by SLIM name
├── data/analysis_catalog.csv  # Spec §8 code/group/request-type table
├── bridge.py        # to_slim_v1: TR -> slim-domain-oss v1 TRSubmission (location/TR no. in file_name)
├── results.py       # slim-data host side: TrSampleTables, SpecReader, StaffDirectory, actor_for, demo rules
├── results_routes.py  # /results (upload, batches with every cup, trace, reports) and /api/results — staff only
├── prep.py          # slim-prep host side: prep_actor_for, publish_demo_prep, chemicals, send (+ re-evaluate)
├── prep_routes.py   # /prep (batches, standards book, grid, send, run list CSV) and /api/prep — staff only
├── personas.py      # demo personas (DEMO_PERSONAS): data/demo_personas.csv -> users.is_persona rows
├── demo_scenarios.py  # slim-data's demo scenarios on real TRs (PortalDemoHost, seed_scenarios); demo seed only
├── api.py           # /api/submissions JSON API; maps service errors to 422/403/404/409
├── security.py      # bcrypt hashing, CSRF (csrf_token / csrf_protect), headers + body-size middleware
├── auth.py          # current_user / require_user / require_staff deps; /login, /logout
├── board.py         # the lab board (spec §13): TR cards by stage, chips per sample × analysis
├── invoices.py      # Invoice: TR + report issues -> invoicing V5's sales order; sent / paid (tr_invoices)
├── demo_prices.py   # the demo's default price list for invoicing V5 (invented); demo seed only
├── reports.py       # the issued report: engine_renderer (lab template as HTML) / plain_renderer, report_files
├── routes.py        # Pages: / (board), /requests (customer list), /requests/new|{tr}, status form, /admin, /admin/users
├── web.py           # Jinja2 templates, render(), flash()
├── audit.py         # slim-domain's audit log: install_audit_log (Postgres), chain_check for verify
├── cli.py           # migrate (app + slim-data + invoicing + audit log), verify, create-staff, create-customer-user, sync-analysis-catalog, sync-personas
├── alembic.ini, migrations/   # Alembic; only manages tables on db.Base
├── templates/       # base, _macros, login, error, requests/{list,new,detail}, admin/{home,users}
└── static/          # css/style.css, admin.css (.admin-theme), portal.css (components), js/request_form.js
scripts/seed_demo.py # Demo DB only: SLIM demo data if empty, catalog, slim-data rules, personas, demo scenarios
tests/               # pytest; SQLite per test, or Postgres via TEST_DATABASE_URL; factories.py builds domain objects
docs/SLIM_DOMAIN_V2.md
```

## Architecture Rules

- **Layers:** `domain/` (pure pydantic + stdlib, liftable into slim-domain unchanged) ← input
  models validate HTTP input ← repositories are the only code that touches the DB for TRs.
- **Data ownership:** one Postgres shared with SLIM. The app only READS SLIM reference tables
  (customers, chemicals, elements, analyses) and never declares them on `db.Base`; Alembic must
  never create/alter them. No FKs from the app's tables into SLIM tables.
- **Portable schema:** must run on SQLite and Postgres (as slim-domain does) — no ARRAY columns,
  enums stored as ints. App-owned TR tables use integer keys; `users` uses UUIDs.
- **slim-data:** a required library (git dependency). Its tables share the database, migrated by its own
  Alembic (`alembic_version_slim_data`; `cli migrate` / seed run both). It flushes; routes commit. Staff
  act as `results.actor_for(user)` (UUID id, initials, `lab_role`); result files name the analyst by
  initials (unique). SQLite engines use the explicit-BEGIN recipe so its savepoints work.
- **Auth:** Starlette signed-cookie sessions (`SameSite=Lax`, `Secure` in production), bcrypt.
  Roles: `customer` (must have `customer_id`, a SLIM customer ID) and `staff` (must have
  `initials` and a `lab_role`: analyst / reviewer / supervisor). No self-registration; staff create users. Customers must already exist in SLIM.
  Demo personas (`is_persona`, only with `DEMO_PERSONAS`) are chosen, not logged in: ASSERTED in slim-data,
  refused by /login, no user or role admin.
- **Machine access:** slim-lims uses `Authorization: Bearer $LIMS_API_TOKEN` (spec §12). Token
  requests skip CSRF and never use the session; only `auth.LIMS_ROUTES` are allowed. Keep
  `tests/test_lims_contract.py` passing — slim-lims is written against it.
- **CSRF:** every router is created with `dependencies=[Depends(csrf_protect)]`. Forms send
  `csrf_token`; fetch/JSON sends the `X-CSRF-Token` header (token in `<meta name="csrf-token">`).
  `tests/test_app.py` fails if any state-changing route accepts a request without a token.
- **CSP:** no `'unsafe-inline'` — never add inline `<script>`; pass server data via `data-*`
  attributes. Allowed script hosts: code.jquery.com, cdn.jsdelivr.net.
- **Errors:** invalid input → 422 with per-field detail (FastAPI default). HTML routes render
  `error.html`; `/api/*` and `Accept: application/json` get JSON. Unauthenticated: HTML → 303 to
  `/login?next=...`, API → 401. Non-staff on staff routes → 403.
- **API:** `POST /api/submissions` (201), `GET /api/submissions[?status&request_type&page]`,
  `GET /api/submissions/{tr_number}`, `POST /api/submissions/{tr_number}/status`,
  `POST /api/submissions/{tr_number}/samples/{position}/chemical` (staff: match to SLIM). Responses are
  `TRSubmission.model_dump(mode="json")` — enums as ints. Customers only see their own customer's
  TRs (others → 404). Business-rule failures → 422 with `loc` pointing at the field; disallowed or
  concurrent status changes → 409.
- **Reference data:** `app.state.reference` is a `ReferenceCache` (TTL `REFERENCE_CACHE_TTL_SECONDS`,
  default 300 s; staff can force a reload on /admin). A failed refresh keeps the previous snapshot.
  SLIM analyses with no `analysis_catalog` row are never offered. `GET /api/reference` gives the
  form its choices (customers only for staff).
- **OSS only:** see the banner at the top. `PORTAL_ALLOWED_DB_HOSTS` is for other *demo* hosts.
- **Dates:** "today" means the lab's local date (`LAB_TIMEZONE`, default America/Chicago). Expected
  arrival (optional, customer) can't be in the past; `date_received` (staff) can't be in the future.
- **Demo data only:** dev, tests and the portfolio use the OSS repos' fictional data
  (`slim-report-engine-oss/demo/demo.py`). `scripts/seed_demo.py` never writes into SLIM tables
  that already hold data. Never point the app at the lab's production database from here.
- **Routes are plain `def`** (sync SQLAlchemy), not `async def`.
- 1 MB request cap (`MAX_BODY_BYTES`), enforced with or without Content-Length.

## Key Decisions (see the spec for detail)

- One request type (Chemical / Water / Wafer) per submission; per-type sample fields.
- Chemical / matrix is free text from the customer; linked to a SLIM chemical only on an exact name
  match, otherwise matched (or created in SLIM) by the lab later. The bridge refuses unmatched TRs.
- Form UX (user-approved): samples table + one grouped analysis panel that applies to the ticked
  rows (or all rows when none are ticked); Gmail-style selection bar. The analysis-matrix layout
  was tried and rejected — too many analysis combinations for per-analysis columns.
- TR numbers `TR00001`… from a gapless counter row locked in the submit transaction.
- **Check-in** (staff, `/requests/{tr}/check-in`, `POST /api/submissions/{tr}/check-in`) is the only
  way to RECEIVED: receipt (date, initials, note) + every chemical matched to SLIM, one step —
  the app's equivalent of the VBA/manual log-in. "Reject" = cancel with a reason.
- Statuses: submitted → received → in progress → partial report (repeatable) → complete report
  → invoiced (terminal, staff only); cancelled (customer may cancel only while submitted).
- Payment: purchase order (PO number, also used for billing codes) or credit card (lab calls;
  no card data stored).
- Call-in RUSH = outside business hours. Wafer sizes 200 / 300 mm; units atoms/cm² or
  10¹⁰ atoms/cm². Water packages exist in the shape but are not offered yet.
- Hosting target: portfolio VPS running the whole ecosystem via Docker Compose. `render.yaml`
  names the web service `slim-lims-oss` (the demo URL) and the database `slim-lims-db` (Postgres
  database and user `slim_lims`). Renaming the database makes Render create a second one, and the
  free plan allows one: delete the old database first.

## Backlog

See "Parked ideas" in docs/SLIM_DOMAIN_V2.md §11.
