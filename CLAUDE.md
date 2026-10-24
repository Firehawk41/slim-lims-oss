# slim-lab-portal — Claude Context

> ## ⚠️ OSS ONLY — read first
> This portal is a **portfolio demo** that attaches to the **open-source** SLIM repos only:
> `slim-domain-oss`, `slim-report-engine-oss`, `slim-lims-oss` and their fictional demo data.
> **Never connect it to PreciLab (or any real lab) production databases, shares or services.**
> Enforced in code: `slim_lab_portal/oss_guard.py` refuses any database URL containing
> "precilab" (no override) and any host other than localhost / 127.0.0.1 / `db` / `postgres`
> unless listed in `PORTAL_ALLOWED_DB_HOSTS`. Every connection goes through `db.make_engine`;
> `tests/test_oss_guard.py` fails if anything calls `create_engine` elsewhere. `.env` is read
> from the current directory only. Do not weaken any of this.
>
> **Scope:** modular but narrow. Don't modify slim-domain / slim-report-engine / slim-lims yet —
> the portal must fit them, not the other way round. Ideas worth having later go in the spec's
> "Parked ideas" list (docs/SLIM_DOMAIN_V2.md §11) and get raised with the user, not built.

## What This Is

The sample submission portal for the SLIM LIMS ecosystem (slim-domain, slim-report-engine,
slim-lims), built for an ISO 17025 accredited analytical chemistry lab. Customers log in and
submit Testing Requests (TRs); lab staff receive samples and move TRs through their statuses.
The portal replaces the Excel Testing Request form as the primary intake path; emailed `.xlsx`
forms stay supported by slim-domain's parsers for back-compatibility. The portal never produces
Excel — it produces TR domain objects.

**The domain spec is `docs/SLIM_DOMAIN_V2.md`. Read it before changing anything under
`slim_lab_portal/domain/`, the TR tables, enums or statuses.**

## Rebuild status (branch `claude/slim-lab-portal-rebuild`)

The repo was a Flask app ("LabPortal"); it is being rebuilt on FastAPI. Old code is in git
history (`main` before the rebuild) — e.g. `git show main:app/templates/form.html`.

1. ✅ Spec: `docs/SLIM_DOMAIN_V2.md`
2. ✅ FastAPI skeleton: config, SQLAlchemy 2, Alembic, session auth, CSRF, security headers, pytest
3. ✅ Domain objects (pydantic, per spec §3–4), input models, repository, TR number counter, JSON API
4. ✅ Reference-data cache over SLIM tables, analysis catalog, `scripts/seed_demo.py` (OSS demo data)
5. ✅ (A) Bridge: portal-side adapter TRSubmission v2 -> slim-domain-oss v1 TRSubmission, plus a
   test feeding it to slim-report-engine-oss's report builder on OSS demo data. Portal only.
   `slim_lab_portal/bridge.py` (`to_slim_v1`, received TRs only); `tests/test_bridge.py` renders
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
8. ⏸ (C) Ecosystem docker-compose (portal + slim-lims + Postgres + Caddy) for the portfolio VPS.
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
14. ⏸ Invoicing, integral to the system (Jamie): rewire SLIM-Invoicing-Program to read SLIM (TRs + slim-data report
   issues) instead of CSV, driven from the board's To invoice column. Comes after D. Needs the user's go-ahead.

Agreed order: A, B, link-ready (9) and the board (13) done. Next D when approved, then invoicing (14);
C last. The user is token-constrained: small, focused steps,
short replies.

## How to Run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                     # DATABASE_URL defaults to sqlite:///slim_lab_portal.db
pip install -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss   # OSS repos, for the demo seed
python scripts/seed_demo.py              # migrations (portal + slim-data) + OSS demo SLIM data + catalog + demo ruleset
PORTAL_PASSWORD='at-least-12-chars' python -m slim_lab_portal.cli create-staff --email you@lab.example --initials JM --lab-role supervisor
uvicorn --factory slim_lab_portal.main:create_app --reload   # http://127.0.0.1:8000, API docs at /api/docs
python -m slim_lab_portal.cli verify     # replay every result + check the audit chain (Postgres)
pytest                                   # TEST_DATABASE_URL=postgresql+psycopg2://... pytest  to run on Postgres
```

Or `docker compose up --build`: Postgres 16 shared like the ecosystem (SLIM demo tables + portal
tables), a one-shot `seed` service (needs `../slim-domain-oss` and `../slim-report-engine-oss`), and the
portal on :8000 with demo personas on (`/demo/personas`). Set `LIMS_API_TOKEN` to let slim-lims call the API.
Cloud demo: `render.yaml` (Blueprint; the seed runs on every start and is idempotent). CI:
`.github/workflows/ci.yml` ({SQLite, Postgres 16} × {3.11, 3.12}).

## File Map

```
slim_lab_portal/
├── main.py          # create_app(settings, engine): middleware, routers, exception handlers, /health
├── config.py        # Settings from env (APP_ENV, SECRET_KEY, DATABASE_URL, ...)
├── db.py            # Base (portal-owned tables only), make_engine (the only way to connect), get_db
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
├── bridge.py        # to_slim_v1: portal TR -> slim-domain-oss v1 TRSubmission (location/TR no. in file_name)
├── results.py       # slim-data host side: TrSampleTables, SpecReader, StaffDirectory, actor_for, demo rules
├── results_routes.py  # /results (upload, batches with every cup, trace, reports) and /api/results — staff only
├── personas.py      # demo personas (DEMO_PERSONAS): data/demo_personas.csv -> users.is_persona rows
├── demo_scenarios.py  # slim-data's demo scenarios on real TRs (PortalDemoHost, seed_scenarios); demo seed only
├── api.py           # /api/submissions JSON API; maps service errors to 422/403/404/409
├── security.py      # bcrypt hashing, CSRF (csrf_token / csrf_protect), headers + body-size middleware
├── auth.py          # current_user / require_user / require_staff deps; /login, /logout
├── board.py         # the lab board (spec §13): TR cards by stage, chips per sample × analysis
├── invoices.py      # invoice sent / paid per TR (tr_invoices) until the invoicing module writes them
├── reports.py       # the issued report: engine_renderer (lab template as HTML) / plain_renderer, report_files
├── routes.py        # Pages: / (board), /requests (customer list), /requests/new|{tr}, status form, /admin, /admin/users
├── web.py           # Jinja2 templates, render(), flash()
├── audit.py         # slim-domain's audit log: install_audit_log (Postgres), chain_check for verify
├── cli.py           # migrate (portal + slim-data + audit log), verify, create-staff, create-customer-user, sync-analysis-catalog, sync-personas
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
- **Data ownership:** one Postgres shared with SLIM. The portal only READS SLIM reference tables
  (customers, chemicals, elements, analyses) and never declares them on `db.Base`; Alembic must
  never create/alter them. No FKs from portal tables into SLIM tables.
- **Portable schema:** must run on SQLite and Postgres (as slim-domain does) — no ARRAY columns,
  enums stored as ints. Portal-owned TR tables use integer keys; `users` uses UUIDs.
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
  that already hold data. Never point the portal at the lab's production database from here.
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
  the portal's equivalent of the VBA/manual log-in. "Reject" = cancel with a reason.
- Statuses: submitted → received → in progress → partial report (repeatable) → complete report
  → invoiced (terminal, staff only); cancelled (customer may cancel only while submitted).
- Payment: purchase order (PO number, also used for billing codes) or credit card (lab calls;
  no card data stored).
- Call-in RUSH = outside business hours. Wafer sizes 200 / 300 mm; units atoms/cm² or
  10¹⁰ atoms/cm². Water packages exist in the shape but are not offered yet.
- Hosting target: portfolio VPS running the whole ecosystem via Docker Compose. `render.yaml`
  names the web service `slim-lims-oss` (the demo URL) and keeps the database name `labportal-db`
  (renaming it would make Render create a second database; the free plan allows one).

## Backlog

See "Parked ideas" in docs/SLIM_DOMAIN_V2.md §11.
