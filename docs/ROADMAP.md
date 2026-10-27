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
| **Lab** | Check-in, sample custody, prep, instrument data in, results, report sign-off | `slim-data-oss` | `slim-samples` (custody, storage, retention); `slim-prep` (bench prep); instrument readers in slim-data; signatory step |
| **Quality** | QC trends, nonconformities and corrective actions, complaints, proficiency testing, equipment, competence, audits, validation | slim-domain's audit log; slim-data's `verify` | `slim-quality` |
| **IT** | Reference data, logins, the app shell, operations | `slim-domain-oss`; the `slim-lims-oss` host | Password reset / MFA; VPS docker-compose, backups, monitoring |

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
  departments, with a demo persona for each role (customer, analyst, reviewer, supervisor; a quality
  manager to come).
- **Featured on their own:**
  1. **slim-data**: versioned rules engine, append-only records, time travel, `verify`.
  2. **Invoicing V1 → V5**: a design told across five versions, VBA to Python to a LIMS module.
  3. **slim-quality** (once built): control charts with Westgard rules, trends a quality manager reads at a glance.
- **Shown inside the sandbox story only:** slim-domain, slim-report-engine, slim-prep, slim-samples.

## Phases

| # | Department | Work | Why now |
|---|---|---|---|
| 0 | IT | ✅ **Jamie:** make slim-lims-oss public; delete the old Render service, `labportal-db` and the old Blueprint, then a new Blueprint from slim-lims-oss (database `slim-lims-db`); archive LabPortal; drop the slim-lims pair from any local "sync private → OSS" instruction. ✅ **Claude:** stale docs ("portal" → the app), invoicing in the README, ruff in slim-lims-oss CI (2026-10-26) | The live demo shows what's built |
| 1 | Lab | ◐ QC sample roles in slim-data ✅ (D-87: CHECK / CRM, `qc_check@1`). `slim-prep`: prep batches, weights and volumes (dilution calculated, not typed), spike amounts with standard lot and expiry, balance and pipette used, who and when, the instrument run list; hands the batch to slim-data's existing batch API. QC sample roles in slim-data (check standard, CRM). One or two instrument file readers | Quality trends need this data |
| 2 | Quality | `slim-quality` part 1, read-only: QC control charts (Westgard), trends per instrument, analyst, method (pooled precision → uncertainty, §7.6) and customer (turnaround, spec exceedances, a customer's own lot trend). Demo history: months of QC on two instruments with a planted drift, kept off the board. The software validation pack (§7.11) | The third standalone portfolio piece |
| 3 | Admin + IT | Landing page and architecture diagram (by department) for the sandbox | A visitor understands it in 30 seconds |
| 4 | Lab + Admin | `slim-samples`: condition on receipt, labels, storage, retention and disposal (§7.4); check-in moves here from the host. Report signatory (Lab), delivery by email and PDF (Admin) | Completes the sample's journey |
| 5 | Quality | `slim-quality` part 2: nonconformities and corrective actions (§7.10, §8.7, raised from exceptions and amendments), complaints (§7.9), proficiency testing (§7.7.2), equipment and reference materials (§6.4, §6.5), competence (§6.2), risks (§8.5), internal audits and the management review pack (§8.8, §8.9). Quality-manager persona | A quality manager's dream |
| 6 | Admin + IT | Quote screens (create, expire, customer requests a quote); reference-data upkeep (customers, chemicals, analyses, specs); password reset, MFA, login rate limiting | Day-to-day completeness |
| 7 | IT | Ecosystem docker-compose for the VPS (Caddy, Postgres, the app), backups, monitoring | Agreed to come last |

## Open questions

- ~~Samples and prep: one module or two?~~ Two, decided (slim-data D-86): `slim-prep` now (repo
  `slim-prep-oss`), `slim-samples` in phase 4.
- **Prep book layout:** Jamie describes the bench's real steps and columns; the OSS version is invented
  from that (D-86). slim-prep waits for it.
- **Landing page timing:** phase 3 as drafted; earlier if a recruiter may look soon.

## Where things are

| Repo | Department | What |
|---|---|---|
| `slim-lims-oss` | Admin + IT (host) | The sandbox app: screens, auth, composition; this roadmap |
| `slim-data-oss` | Lab | Results module; `docs/DECISIONS.md` is the ecosystem's decision log (D-01 …) |
| `SLIM-Invoicing-Program` | Admin | V1–V4 history; `V5-LIMS-Module` is the live library |
| `slim-report-engine-oss` | Admin | Report templates; also the OSS demo reference data (`demo/demo.py`) |
| `slim-domain-oss` | IT | Reference data, audit log |
| `LabPortal` | — | Folded into slim-lims-oss (D-82); to archive |
| private `Slim-LIMS`, `slim-domain`, `slim-raw-data` | — | Production. Never changed from here; never synced into OSS |
