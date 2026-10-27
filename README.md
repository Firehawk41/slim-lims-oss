# slim-lims

[![CI](https://github.com/Firehawk41/slim-lims-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/Firehawk41/slim-lims-oss/actions/workflows/ci.yml)

**SLIM**, a LIMS for an ISO 17025 accredited analytical chemistry lab: the open-source demo of the
whole host app. It uses [slim-domain](https://github.com/Firehawk41/slim-domain-oss),
[slim-report-engine](https://github.com/Firehawk41/slim-report-engine-oss),
[slim-data](https://github.com/Firehawk41/slim-data-oss) and invoicing V5
([SLIM-Invoicing-Program](https://github.com/Firehawk41/SLIM-Invoicing-Program)) as libraries, on
fictional demo data.

**Live demo:** https://slim-lims-oss.onrender.com/demo/personas (free plan: the first visit wakes it,
about a minute). **Roadmap:** [`docs/ROADMAP.md`](docs/ROADMAP.md).

Customers log in and submit Testing Requests; lab staff receive the samples, run the results through
slim-data's rules engine, review and release them, issue reports and track each request to invoice on
one board. Submissions are stored as Testing Request domain objects in the shape defined in
[`docs/SLIM_DOMAIN_V2.md`](docs/SLIM_DOMAIN_V2.md), so the rest of the LIMS can consume them
directly — no Excel in between.

> **History:** began as a standalone Flask form ("LabPortal"), rebuilt on FastAPI, then folded into
> this repo (2026-10-24). The VBA-facing report API this repo used to mirror from the lab's private
> slim-lims is retired here; it stays in git history.

## What it does

- **Customers** submit Testing Requests: one request type (Chemical / Water / Wafer), a
  spreadsheet-like sample table (paste a column of IDs, Ctrl+D fills down), free-text chemical /
  matrix, and one analysis panel that applies to the ticked samples or to all of them. Each request
  gets a TR number (`TR00001`…) and its status is visible to the customer.
- **The lab board** (`/`, everyone's home page) shows each request as a card in the column for where it
  stands, from expected to paid, with a chip per sample × analysis. Customers see their own requests
  and their issued reports.
- **Lab staff** receive samples, move requests through submitted → received → in progress →
  partial report → complete report → invoiced (or cancelled), match free-text chemicals to SLIM,
  and manage users.
- **SLIM reference data** (customers, chemicals, elements, analyses) is read from the shared
  database through a periodically refreshed cache.
- **Results** (staff, `/results`), through slim-data: bring in a run as a CSV, a Thermo Qtegra export
  (samples named in Qtegra's Comment) or a table pasted from Excel (preview, then all or nothing),
  and a versioned rules engine decides each result, with suggestions from the instrument's own
  evidence (replicate RSD, internal standard recovery) and the run's calibration. Then review the batch, accept, send to
  rerun, record exceptions, release, and issue the report. Four-eyes and role rules come from slim-data's
  policy, and the request's status follows what actually happened: first readings → in progress,
  issued report → partial / complete report. Batches carry method blanks, check standards and CRMs.
- **Invoicing** through invoicing V5: a reported request is priced from the customer's own quote or the
  default price list into a sales order (`/requests/{tr}/invoice`); `/invoices` lists them, with an
  accounting CSV, and payment is recorded on the invoice.
- **Demo personas** (`DEMO_PERSONAS=true`): "Continue as..." a customer, analyst, reviewer or
  supervisor. Their actions are recorded as *asserted*, never as logged in. The demo seed pre-runs ten
  result scenarios on real requests, so a first visitor already sees history.
- **Audit:** on Postgres, slim-domain's hash-chained change log covers every table.
  `python -m slim_lims.cli verify` replays every stored result through its ruleset and checks
  the chain. It exits with 1 on any mismatch.
- **JSON API** at `/api/docs`; every request is also available as the TR domain object.

## OSS only

This is a portfolio module. It connects **only** to the open-source SLIM repos and their fictional
demo data — never to a real lab's systems. `slim_lims/oss_guard.py` enforces it: database URLs
mentioning "precilab" are always refused, and only local/demo hosts are allowed unless explicitly
listed in `PORTAL_ALLOWED_DB_HOSTS`.

## Tech stack

| Layer | Technology |
|---|---|
| Web | Python 3.11+, FastAPI, Jinja2 (server-rendered pages) |
| Domain | pydantic v2 (frozen models, shared shape with slim-domain) |
| Database | PostgreSQL (shared with SLIM) or SQLite, SQLAlchemy 2, Alembic |
| Auth | Signed-cookie sessions, bcrypt, CSRF tokens |
| Frontend | Server-rendered pages + vanilla JS (no frameworks, no inline scripts) |

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Fictional SLIM reference data from the OSS repos (checked out next to this one):
pip install -e ../slim-domain-oss[postgres] -e ../slim-report-engine-oss
python scripts/seed_demo.py
PORTAL_PASSWORD='at-least-12-chars' python -m slim_lims.cli create-staff --email you@lab.example --initials JM --lab-role supervisor
uvicorn --factory slim_lims.main:create_app --reload
```

Open http://127.0.0.1:8000 (API docs at `/api/docs`). Set `DEMO_PERSONAS=true` to pick a persona
instead of logging in, and run `python -m slim_lims.cli verify` to check the stored results. Create a customer user with
`python -m slim_lims.cli create-customer-user --email you@customer.example --customer-id 1`.
Run the tests with `pytest`; `e2e/request_flow.js` is a Playwright run-through of the form.

With Docker (Postgres shared the way the SLIM ecosystem shares it; expects the OSS repos next to
this one): `docker compose up --build`, then create a staff user with
`docker compose exec portal python -m slim_lims.cli create-staff ...`.

## Cloud demo (Render)

`render.yaml` is a Render Blueprint: a free Postgres (`slim-lims-db`) plus the app. Each start runs the
demo seed (migrations, the audit log, SLIM's fictional reference data, the demo rulesets and prices,
personas and the ten result scenarios; each step is skipped once it's done), then serves the app with personas on. Go to
**New → Blueprint** in Render, pick this repo, and open `/demo/personas` on the URL it gives you.
On the free plan the app sleeps when idle (the first visit takes about a minute) and the free
database expires after a while: recreate it and the next start seeds it again.

## Tests

`pytest` (SQLite), or `TEST_DATABASE_URL=postgresql+psycopg2://... pytest` (Postgres). CI runs both
on Python 3.11 and 3.12, with slim-data, slim-domain, invoicing V5 and the report engine installed from GitHub.

## Security

- Content-Security-Policy without `'unsafe-inline'`, `X-Frame-Options: DENY`, `nosniff`,
  strict referrer policy; HSTS in production.
- CSRF tokens on every state-changing route (enforced by a test).
- `SameSite=Lax`, `HttpOnly` session cookies (`Secure` in production); session rotated on login.
- 1 MB request body cap.
- Append-only results (slim-data's triggers) and a hash-chained change log (slim-domain) on Postgres:
  edits are refused, and an edit by the table owner is detected by `verify`.
