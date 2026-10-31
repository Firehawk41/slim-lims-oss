# SLIM roadmap

Drafted 2026-10-24 with Jamie (slim-data-oss D-85). The plan for the whole OSS ecosystem: how the
modules are organised, what the portfolio features, and what gets built in which order. Start a new
piece of work by reading this, then the module's own CLAUDE.md and decision log.

## Departments (labels only)

Four departments, the way a lab is run. They are **labels**, for keeping the system in one's head and
for explaining it (README map, architecture diagram, landing page). They are not code: no repo,
package or import follows them.

| Department | Covers | Modules today | Planned |
|---|---|---|---|
| **Admin** | Requests and the customer's view, quotes, invoicing, reports and their delivery | `slim-lims-oss` request and customer screens; invoicing V5 (`SLIM-Invoicing-Program/V5-LIMS-Module`); `slim-report-engine-oss` | Quote screens; report delivery (email) and PDF |
| **Lab** | Check-in, sample custody, prep, instrument data in, results, report sign-off | `slim-data-oss` (readers `qtegra/1`, `paste/1`, `generic_csv/1`); `slim-prep-oss` (the prep book); `slim-samples-oss` (bottles, custody, retention) | Signatory step |
| **Quality** | QC trends, nonconformities and corrective actions, complaints, proficiency testing, equipment, competence, audits, validation | slim-domain's audit log; slim-data's `verify`; `slim-quality-oss` part 1 (QC charts and tools) | `slim-quality` parts 2 and 3 |
| **IT** | Reference data, logins, the app shell, operations | `slim-domain-oss`; the `slim-lims-oss` host | Password reset / MFA; VPS docker-compose, backups, monitoring (login rate limiting done, D-101) |

Report sign-off is a Lab act (ISO 17025 §7.8) even though reports sit in Admin: the signatory step lives
with the lab, delivery with Admin.

## Module rules (against sprawl)

1. Every module is a library with its own tables and its own Alembic version table, on the shared
   database; portable schema (SQLite + Postgres), no foreign keys into another module's tables.
2. Modules never import each other. They may use slim-domain; the host composes them.
3. The host (`slim-lims-oss`) is the composition root and the screens. It does no department's work.
4. A new module, or a change to these rules, needs a decision record with the options weighed.
5. Demo data is invented, every module's own (slim-data D-05). OSS only: never a real lab's data.
6. Each module: CI on {SQLite, Postgres 16} × {3.11, 3.12}, ruff, tests before every merge.

## Portfolio

- **Crown jewel: the slim-lims sandbox.** One Testing Request end to end through all four
  departments, with a demo persona for each role (customer, analyst, reviewer, supervisor, quality
  manager, quality).
- **Featured on their own:**
  1. **slim-data**: versioned rules engine, append-only records, time travel, `verify`.
  2. **Invoicing V1 → V5**: a design told across five versions, VBA to Python to a LIMS module.
  3. **slim-quality**: a quality manager's toolbox: control charts, Westgard rules, EWMA, CUSUM, forecasts
     of when a series leaves its window, change control over the rules (slim-data D-109, D-110).
- **Shown inside the sandbox story only:** slim-domain, slim-report-engine, slim-prep, slim-samples.

## Phases

| # | Department | Work | Why now |
|---|---|---|---|
| 0 | IT | ✅ **Jamie:** make slim-lims-oss public; delete the old Render service, `labportal-db` and the old Blueprint, then a new Blueprint from slim-lims-oss (database `slim-lims-db`); archive LabPortal; drop the slim-lims pair from any local "sync private → OSS" instruction. ✅ **Claude:** stale docs ("portal" → the app), invoicing in the README, ruff in slim-lims-oss CI (2026-10-26) | The live demo shows what's built |
| 1 | Lab | ✅ QC sample roles in slim-data (D-87: CHECK / CRM, `qc_check`). `slim-prep`: prep batches, weights and volumes (dilution calculated, not typed), spike amounts with standard lot and expiry, balance and pipette used, who and when, the instrument run list; hands the batch to slim-data (D-95, D-96, D-105). Instrument readers `qtegra/1` and `paste/1` (D-88, D-89) | Quality trends need this data |
| 2 | Admin + IT | ◐ Landing page (`/about`; structure, diagram and tests built, Jamie's words to come) and architecture diagram (by department) for the sandbox | A visitor understands it in 30 seconds; the sandbox is already public (swapped ahead of Quality, 2026-10-29) |
| 3 | Quality | ✅ `slim-quality` part 1 (D-109, D-110): QC series per instrument × analyte × mode; a toolbox the quality manager switches on and tunes (Westgard rules, EWMA, CUSUM, one-sided exceedance, time-to-limit forecast, shared shift); limits from a baseline or a certificate; change control (quality users propose, managers decide), backtest, events, notes, signed QC reviews. Demo history: 90 days of QC on two instruments with planted cone wear, in QC-only batches (D-108), off the board. slim-data keeps the plasma mode (`qtegra/2`, D-106) | The third standalone portfolio piece |
| 4 | Lab + Admin | ◐ **Done (2026-10-31):** `slim-samples` (S-01…S-11, D-120): the bottles a sample arrives in (kind, count, storage, condition on receipt, the analyses each serves), custody events, retention date with hold, disposal and return, labels; check-in logs the bottles in one transaction with the host's check-in (D-121); a condition can warn on the report or block prep. **To come:** report signatory (Lab), delivery by email and PDF (Admin) | Completes the sample's journey |
| 5 | Quality | ◐ `slim-quality` part 2, the audit tools. **Done (2026-10-30):** the vertical audit of a released report, random or by number, recorded (§7.5, §8.4; D-113); nonconformities with an impact assessment over released results, corrective action, effectiveness check and closure (§7.10, §8.7; D-114). **To come:** complaints (§7.9), proficiency testing with z-score trends (§7.7.2), uncertainty from pooled QC (§7.6), equipment and reference materials (§6.4, §6.5; maintenance events exist), competence: who acted outside their authorisation (§6.2), risks (§8.5), internal audits, a readiness view per clause and the management review pack (§8.8, §8.9), the software validation pack (§7.11); turnaround and customer trends. Part 3 (parked): the tools' warnings on the review screen before release. Also parked: where each sample should be against its turnaround (D-111) | A quality manager's dream |
| 6 | Admin + IT | Quote screens (create, expire, customer requests a quote); reference-data upkeep (customers, chemicals, analyses, specs); password reset, MFA (login rate limiting done, D-101) | Day-to-day completeness |
| 7 | IT | Ecosystem docker-compose for the VPS (Caddy, Postgres, the app), backups, monitoring | Agreed to come last |

## Open questions

- ~~Samples and prep: one module or two?~~ Two, decided (slim-data D-86): `slim-prep` now (repo
  `slim-prep-oss`), `slim-samples` in phase 4.
- ~~Prep book layout~~ Settled: slim-prep is built from Jamie's description of the bench (D-86).
- ~~Landing page timing~~ Phase 2, ahead of Quality (2026-10-29): the sandbox is already public.

## Where things are

| Repo | Department | What |
|---|---|---|
| `slim-lims-oss` | Admin + IT (host) | The sandbox app: screens, auth, composition; this roadmap |
| `slim-data-oss` | Lab | Results module; `docs/DECISIONS.md` is the ecosystem's decision log (D-01 …) |
| `SLIM-Invoicing-Program` | Admin | V1–V4 history; `V5-LIMS-Module` is the live library |
| `slim-report-engine-oss` | Admin | Report templates; also the OSS demo reference data (`demo/demo.py`) |
| `slim-domain-oss` | IT | Reference data, audit log |
| `slim-samples-oss` | Lab | Bottles per sample, custody events, retention, disposal (S-01…S-11, D-120) |
| `slim-quality-oss` | Quality | QC charts and tools, change control (D-109) |
| `LabPortal` | — | Folded into slim-lims-oss (D-82); to archive |
| private `Slim-LIMS`, `slim-domain`, `slim-raw-data` | — | Production. Never changed from here; never synced into OSS |
