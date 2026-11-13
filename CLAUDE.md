# slim-lims-oss — Claude Context

> ## ⚠️ OSS ONLY — read first
> A **portfolio demo** on the **open-source** SLIM repos only (`slim-domain-oss`, `slim-report-engine-oss`,
> `slim-data-oss`, `slim-prep-oss`, `slim-samples-oss`, `slim-quality-oss`) and their fictional demo data. **Never connect it
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
slim-prep (the prep book), slim-samples (bottles, custody, retention), slim-quality (QC charts and tools, change control), invoicing V5
(`slim_invoicing`), slim-domain (SLIM tables, audit log), slim-report-engine (the report template).
**Pinned (D-117):** each is installed at a merged commit, the same in `pyproject.toml`, CI and `render.yaml`
(`tests/test_pins.py`); bump a pin in a host PR. Each keeps its own tables and Alembic version table in the
shared database; `cli migrate` and the seed run them all.

**Open:** deeper report-engine wiring (spec §13; changes other repos, needs the go-ahead); ecosystem
docker-compose for the VPS (**deferred, do not start**); the landing page (`/about`) is live with
placeholders; Jamie writes its words in `templates/about/_copy.html`. slim-quality parts 1 and 2 are in
(phases 3 and 5: vertical audit, NCs; 5a equipment, lots, PT, complaints, D-125…D-130; 5b uncertainty,
competence, trends, D-131…D-133; 5c risks, internal audits, readiness, management review, the §7.11 pack,
D-134…D-137); phase 4 is done (slim-samples; signed reports, their PDF, the outbox). Phase 6 is done: quote
screens (D-139, D-140), reference data upkeep (D-141), accounts (D-142); spec limit changes since (D-143);
slim-quality part 3, QC trend warnings at release (D-144). Next: phase 7
(`docs/ROADMAP.md`, the VPS: deferred, do not start). Parked: an SMTP
transport (D-124); report settings (assay spec text, report note, display label).

## How to run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss
cp .env.example .env                     # DATABASE_URL defaults to sqlite:///slim_lims.db
python scripts/seed_demo.py              # every migration + OSS demo data + rules + personas + scenarios
PORTAL_PASSWORD='at-least-12-chars' python -m slim_lims.cli create-staff --email you@lab.example --initials JM --lab-role supervisor --name 'Jo Example'
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
├── models.py        # User (full_name, job_title: how they sign), TR tables, tr_versions (D-104), invoices + payments (D-119),
│                    #   report_pdfs, report_deliveries, display_modes, quote_requests (+ events), quote_pdfs,
│                    #   quote_deliveries, tr_quotes, reference_changes, account_events, account_messages,
│                    #   spec_proposals, spec_decisions, qc_acknowledgements (all append-only), user_capabilities, report_files,
│                    #   user_mfa, mfa_recovery_codes, password_resets, lab_sections (+ lab_section_analyses),
│                    #   display_links
├── repositories.py  # TRSubmissionRepository: the only TR writer; gapless numbers; versions / as_of (D-104)
├── scope.py         # Scope.for_user: whose TRs a listing shows; fails closed (D-103)
├── services.py      # SubmissionService: submit, check-in, status (facts only, D-99), history
├── capabilities.py  # manage users, signatory (D-122): supervisors by default, grantable per person (D-100)
├── security.py      # bcrypt, CSRF, LoginThrottle (D-101), headers + body-size middleware
├── labtime.py       # LabClock: the lab's now / today (D-101)
├── labs.py          # a lab per visitor: Labs (claim, spares, sweep), with_schema (D-102)
├── demo_seed.py     # seed_demo(url): the whole demo seed (scripts/seed_demo.py and each lab); upgrade_lab (D-115)
├── auth.py          # current_user (holds MFA-required users for setup, D-142), require_*; /login (+ /login/code),
│                    #   /logout, /demo/personas, fresh lab
├── accounts.py, account_routes.py, totp.py  # D-142: reset links, password change, MFA (TOTP + recovery codes,
│                    #   the sealed secret); /account, /account/mfa, /reset/{token}; the Users page's resets
├── reference.py, reference_cache.py, slim_tables.py, catalog.py  # SLIM reference data (read-only) + analysis catalog
├── bridge.py        # to_slim_v1: TR -> slim-domain v1 TRSubmission
├── results.py, results_routes.py  # slim-data host side; /results, /api/results (staff)
├── prep.py, prep_routes.py        # slim-prep host side (TrSamples: its TR port); /prep, /api/prep (staff);
│                                  #   refuse_blocked: a blocking bottle condition stops a prep batch (S-04)
├── samples.py, samples_routes.py  # slim-samples host side (TrSampleLookup: its TR port); check-in composed with the
│                                  #   bottles (S-10); condition_notes for the report; /samples, /api/samples (staff)
├── demo_samples.py                # the demo's bottles: a leaking one, one on hold, one in the fridge
├── quality.py, quality_routes.py  # slim-quality host side: QcSource over slim-data, the vertical audit (composed
│                                  #   from every module, D-113), NCs (D-114), demo QC history and NC; /quality
├── quality_records.py, quality_records_routes.py  # 5a where modules meet: RegisterStocks (slim-prep's lots,
│                                  #   P-28), equipment gates on prep and import (D-126), a balance's impact,
│                                  #   PT values from released results, involved_in (D-130); /quality/equipment,
│                                  #   /materials, /pt, /complaints; the customer's /requests/{tr}/complaints
├── quality_insights.py, quality_insights_routes.py  # 5b: TrDates (slim-quality's TurnaroundSource),
│                                  #   lab_acts (every module's acts for the competence check, D-132), the
│                                  #   demo register; /quality/uncertainty, /competence, /trends
├── quality_system.py, quality_system_routes.py  # 5c: who acted in a clause (an auditor's independence,
│                                  #   D-135), the §7.11 pack from what is installed (D-137), readiness's
│                                  #   host inputs, the demo risks and audits; /quality/risks,
│                                  #   /internal-audits, /readiness, /management-review, /validation
├── invoices.py, demo_prices.py    # invoicing V5; /invoices; the accounting CSV (V5 writes formula cells as text, D-118)
├── quotes.py, quotes_routes.py    # quote screens over V5's quotes (D-139): list by state, make / replace, the document
│                                  #   and its PDF, sending (outbox), customers' requests; a TR's quote (tr_quotes)
├── qc_warnings.py   # D-144: slim-quality's flags on a batch's runs (7 days before, through the run); an
│                    #   ACTION flag needs a reason at release (qc_acknowledgements)
├── spec_changes.py, specs_routes.py  # spec limits (D-143): proposed (quality), approved (QM or supervisor, not the
│                                  #   proposer) through slim-domain's set_limits; re-evaluation; /specs
├── refdata.py, refdata_routes.py  # reference data upkeep (D-141): customers, chemicals via slim-domain's services;
│                                  #   the analysis catalog; reference_changes; /reference
├── reports.py       # issued reports: engine_renderer / plain_renderer, report_files; signing (D-122), the PDF (D-123)
├── delivery.py      # sending an issued report: report_deliveries, the outbox (/outbox; not transmitted, D-124)
├── board.py         # the lab board (spec §13)
├── today.py, today_routes.py  # the day's board (D-138): what is due today per section; sections, screen links,
│                    #   the names switch; /today, /today/setup, /display/{token} (no login, read-only)
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
- **Data:** the app writes SLIM's reference tables only through slim-domain's services (D-141), never with its
  own SQL; it never declares them on `db.Base`, no FKs into them.
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
- Check-in logs the bottles of every sample in the same transaction (D-121); the screen requires them, the
  machine contract (spec §12) does not (`POST /api/submissions/{tr}/check-in` takes `containers` but works
  without; `/samples/log/{tr}` logs them later). A condition on a bottle can warn on the report for the
  analyses it serves, or block prep; the lab's lists are `/samples/rules`.
- Statuses: submitted → received → in progress → partial report → complete report → invoiced; cancelled.
  They follow the facts (D-99): report statuses only by issuing a report, invoiced only by sending the
  invoice; a cancel is refused once a report was issued or a result released. **Each time a module is
  wired, re-check which statuses are still set by hand.**
- Payment: PO number or credit card (the lab calls; no card data stored). RUSH = outside business hours.
- Every change to a TR is kept whole in `tr_versions` (D-104); a payment is a row added, never an edit (D-119).
- Equipment and lots (D-125…D-127): a prep batch or standard is refused when its balance or pipette is out of
  service or overdue (the profile says per kind; instruments warn); an import is refused when its instrument is
  out of service. Standards take their lot from slim-quality's register. A complaint's outcome is approved by a
  quality manager with no hand in the request (D-130).
- Internal audits (D-135): nobody audits a clause they acted in over the past year (the host's acts) or kept
  records of in slim-quality. A management review's pack and a validation signature are frozen when signed;
  a pin bump makes the validation signature stale (D-136, D-137).
- Competence (D-132): the app's capabilities decide who may act; slim-quality's authorisation register is the
  competence record, checked after the fact against every act (never blocks). Uncertainty (D-131) is signed
  in slim-quality and not on the report.
- The day's board (D-138): `/today` shows each sample × analysis due today (plus late work) by stage, for
  the whole lab or a section (the lab's own; one per analysis; the *lab displays* capability sets them up).
  A screen opens a display link (secret, revocable, no login, reloads every minute). Screens show customer
  names only while the lab-wide switch says no visitors; never set, they hide them.
- Quotes (D-139) are V5's, never edited; the host makes them (*pricing* capability), lists them by state,
  sends their PDF to the outbox and answers customers' requests. A request may name the customer's own
  quote in force (`tr_quotes`; the TR's JSON contract is unchanged); pricing still takes the cheapest valid
  line. A quote is withdrawn early as a row added in V5 (D-140); Replace withdraws the old one from the new
  one's first day.
- Reference data (D-141): `/reference` (*reference data* capability) adds and edits customers and adds chemicals
  through slim-domain, and edits the analysis catalog; a reason per change (`reference_changes`); no deletes;
  a catalog sync keeps app edits; each demo lab has its own reference cache.
- Spec limits (D-143): one set (customer or group × chemical × site × kind) at a time; quality staff or a quality
  manager proposes, a quality manager or a supervisor (never the proposer) approves; only approval writes, through
  slim-domain's `set_limits`, if the set is unchanged since; in one transaction where SLIM shares the app's database.
  Approval re-evaluates unreleased results (judged by the proposed limits); released ones
  keep their limits and are listed when they'd now pass/fail otherwise (one click to an NC). Demo labs read their own.
- Accounts (D-142): reset links come from whoever manages users (shown once, in the outbox; 24 h, one use); a
  user changes their own password on `/account`; either signs older sessions out (`users.auth_version`). MFA is
  TOTP + 10 recovery codes, required for supervisors and quality managers (`REQUIRE_MFA`, default on; off in
  tests unless asked), never for personas or the API token; a lost phone is reset with a reason.
- Reports: "Sign and issue" needs the `signatory` capability (supervisors by default) and a name; the signatory may
  be the releaser (four-eyes stands at release). Its PDF is made at issue (WeasyPrint; needs Pango). Sending
  records a delivery in the outbox, never transmitted on the demo; it changes no status (D-122…D-124).
- QC trends (D-144): the results batch page shows slim-quality's flags on each run's instrument × analyte and
  internal standard, 7 days before the run through it. Never a block; releasing over an ACTION flag needs a
  reason, kept with the flags as shown (`qc_acknowledgements`) and shown in the vertical audit. The flags that
  bear on a release are those of the batch each result was made from (`qc_warnings.for_release`: the page and
  the release ask it alike; Lab review L-1).
- Lab review 2026-11-12 (`docs/REVIEW_2026-11-12_lab.md`): a prep correction keeps the samples its batch
  already held, whatever became of their request (slim-prep P-29, L-2); a blocking bottle refuses a file
  that puts the sample on a batch, as it refuses the prep book (`results.blocked_samples`, L-3); a refused
  review or release shows the batch page again with the form as posted (L-4). L-6…L-12 await Jamie.
- Quality review 2026-11-13 (`docs/REVIEW_2026-11-13_quality.md`): a site with no spec limits of its own for a
  chemical is judged by its group's, as slim-domain says (`results.limits_owner`; evaluation and approval, Q-1);
  whoever proposed or decided a spec change is involved in §7.8 for internal audits (Q-2); the vertical audit
  shows a QC acknowledgement under the version whose release it was made at (Q-3). Q-4…Q-10 await Jamie.
- Admin review 2026-11-13 (`docs/REVIEW_2026-11-13_admin.md`): an invoice is priced by the quotes in force the
  day the request was received (`invoices.priced_on`); the invoice page shows what priced each line and when
  the quote the request named priced none (A-1); an amended report to an invoiced request says the invoice
  is unchanged (A-2). A-3…A-5 await Jamie.
- IT review 2026-11-13 (`docs/REVIEW_2026-11-13_it.md`): the login throttle counts the address the lab's own
  proxy appended to `X-Forwarded-For` (`TRUSTED_PROXY_HOPS`: 1 on Render, 0 without a proxy), never one the
  client wrote (T-1). T-2…T-6 await Jamie.
