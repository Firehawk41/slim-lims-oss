# Department review plan (2026-11-12)

Jamie asked for this before the walkthrough: each department examined on its own, boundaries first. It
starts with the big picture and ends with predicted small bugs at the edges. One fresh chat per
department, opened with:

> Review the **<Lab | Quality | Admin | IT>** department per `docs/REVIEW_PLAN_2026-11-12.md`.

Order: **Lab → Quality → Admin → IT** (most seams first; each later review can lean on the earlier ones).
Then Jamie's walkthrough, then one cross-department pass for anything left between them.

## How each session runs (token stewardship)

1. **Read only:** this plan's section for the department, both CLAUDE.md files (already loaded),
   `REVIEW_CHECKLIST.md`, and the previous findings in `REVIEW_2026-11-09.md` (so old holes aren't
   re-reported). Use the file map; open a file only to answer a named question; grep before reading;
   read functions, not whole modules.
2. **Concept pass (no code reading yet):** answer the department's *big-picture questions* below in a few
   lines each, from the docs and decisions. Is it coherent? Is a flow missing? Does a real ISO 17025 lab
   do it this way? Would a portfolio reviewer get stuck? These are **ideas for decision**, each with a
   recommendation, not built.
3. **Boundary pass:** for each listed *seam*, ask: what does each side trust? Who can change it
   underneath? What happens on a correction, cancel or rerun? Does history survive (as_of)?
4. **Edge pass:** take the *predicted bugs* list, add up to ~5 of your own, then **verify each cheaply**,
   in this order: grep or read one function → a scratch test of 10–20 lines (kept only if it finds a bug)
   → a page in the seeded demo only if the bug is visual. Mark each one *confirmed*, *refuted* (one line
   why) or *untested* (why).
5. **Stop rules:** at most ~15 findings, ranked. Run only the affected test files, never the full suite.
   No screenshots unless the finding is visual. Do not fix anything in the review session.
6. **Output:** `docs/REVIEW_2026-11-<dd>_<dept>.md` in the checklist's format: *For decision* (concept
   ideas), *Bugs* (confirmed, each with the fix and its guard test name), *Refuted / checked sound* (one
   line each). Add a diary line. Commit to the branch with no PR, and give Jamie a ranked summary of at most
   ~10 lines. Jamie picks; fixes are their own batch.

Finding ids: L-, Q-, A-, T- (IT) plus a number, so they read across reviews.

---

## Lab

**Scope:** check-in and bottles (`samples*.py`, `demo_samples.py`), prep (`prep*.py`, `demo_prep.py`),
import and results (`results*.py`), review and release, QC warnings (`qc_warnings.py`), sign-off and issue
(`reports.py`), the day's board (`today*.py`). Modules: slim-samples, slim-prep, slim-data.

**Big-picture questions**
- Can one sample be followed end to end on one screen, from receipt through bottles, prep, runs, results,
  release, report and retention? Where does a person have to *know* the next URL?
- Reruns: when a result asks for a rerun, does anything tell the prep book or the day's board? Or does the
  rerun live only in slim-data until someone notices?
- Corrections after release: amended results → reissued report → TR status (D-99). Is the customer told
  what changed, and does the old report stay readable?
- QC warnings (D-144): should acknowledging an ACTION flag offer "raise an NC", as spec changes do? Should
  the panel collapse repeats (latest per code, with a count; seen on PB-00010)?
- Four-eyes and the solo path: is the solo wait the lab's policy data everywhere, and is it visible to the
  reviewer?

**Seams:** samples ↔ prep (blocking conditions, S-04) · prep ↔ data (handoff, dilutions, D-93/D-105) ·
equipment ↔ prep and import (D-126) · data ↔ quality (QC history, D-107; warnings, D-144) · data ↔
reports (what the report showed) · TR status ↔ every module (D-99, cancel).

**Predicted bugs**
- L1 `qc_warnings.for_release` counts flags from *every* batch holding the sample, superseded rerun
  batches included: a clean rerun still asks for a reason.
- L2 `batch_warnings` compares `f.at > run.analyzed_at`: on SQLite a naive timestamp against an aware one
  raises TypeError (passed with the demo; check a file-imported run).
- L3 A bottle condition added *after* a prep batch exists: the batch isn't re-checked. Expected or a hole?
- L4 A TR is cancelled with a prep batch registered but nothing released: the prep book and bottles
  still hold live work, and it still shows on the day's board.
- L5 The equipment gate judges the instrument's status *now* at import, not at the run's `analyzed_at`. A
  run made before an out-of-service date is refused (or one made after is let through).
- L6 The day's board at the lab's midnight and on a DST change: "due today" and "late" by the lab's date or
  UTC's.
- L7 Re-evaluation (spec change, D-143) of a result already *reviewed* but not released: does the new
  version keep the review, silently, or need it again?
- L8 The release form keeps nothing typed when it is refused (qc_ack, note): a reviewer retypes.
- L9 A check-in through the API without containers: prep's blocking rule has no bottles to read. Allowed
  silently?

## Quality

**Scope:** `quality*.py` (charts, NCs, the vertical audit), `quality_records*` (equipment, lots, PT,
complaints), `quality_insights*` (uncertainty, competence, trends), `quality_system*` (risks, internal
audits, readiness, management review, validation), `spec_changes.py`. Module: slim-quality.

**Big-picture questions**
- Does each quality signal lead somewhere? An ACTION trend, a spec change that flips a released result, a
  failed PT, a complaint, an overdue balance: each should end in an NC, or a reason not to raise one. List
  the ones that dead-end.
- Is the management review pack fed by *every* record type now: QC acknowledgements (D-144), spec changes
  (D-143), account events?
- Competence (D-132) and auditor independence (D-135) read "every module's acts": are the newest acts
  (QC acknowledgements, spec proposals and approvals, reference changes) included?
- Is there one quality home page that says what needs attention today?

**Seams:** quality ↔ data (QC source, audit read API) · quality ↔ prep (lots, P-28) · quality ↔ equipment
gates · quality ↔ host acts (`lab_acts`, who acted in a clause) · spec changes ↔ slim-domain (R6, R7) ·
complaints ↔ customer screens.

**Predicted bugs**
- Q1 `lab_acts` and `quality_system`'s clause acts omit `qc_acknowledgements`, `spec_proposals` and
  `spec_decisions`: someone who approved a spec change can audit that clause.
- Q2 The vertical audit lists acknowledgements by sample and analysis, not by the result's version:
  an acknowledgement on a later release shows on an earlier report's audit (filtered by issue time only).
- Q3 PT values come from released results: after an amendment, which version does the PT record keep?
- Q4 Charts are drawn over the whole history on every batch page and audit view. Time it on the seeded
  demo ×10 runs, and decide whether to cache per request only or by the last QC run.
- Q5 A complaint's independent approver: do the involvement checks include the releaser's QC
  acknowledgement on that TR?
- Q6 Uncertainty signed in slim-quality: does a later QC limit change make the signature stale, as a pin
  bump does for validation?
- Q7 Spec proposals for a set whose customer group changes membership between proposal and approval.

## Admin

**Scope:** requests and the customer's view (`routes.py`, `services.py`, `repositories.py`, `scope.py`,
`api.py`), quotes (`quotes*.py`), invoices (`invoices.py`, `demo_prices.py`), report delivery
(`delivery.py`, the outbox), `about.py`. Modules: invoicing V5, slim-report-engine.

**Big-picture questions**
- The customer's journey: quote request → quote → TR naming it → status → report → invoice → complaint. Is
  every step visible to them, and in their words, not the lab's?
- Money in a flow: the quote named on the TR vs the cheapest valid line (D-139). Can the invoice ever
  differ from what the customer was quoted, and is that shown to staff?
- An amended report after the invoice was sent: what happens to the status, and does the customer get it?
- The outbox as a whole: reports, quotes, reset links. Who may read which entries?

**Seams:** host ↔ V5 (quotes, invoices, pins, D-117) · TR ↔ quote (`tr_quotes`) · reports ↔ delivery ·
the customer scope (D-103) on every route added since 11-09.

**Predicted bugs**
- A1 Routes added since the last scope sweep (quote requests, `tr_quotes`, complaints on
  `/requests/{tr}/complaints`) take a TR number without `Scope`, so another customer's TR is 403 not 404,
  or leaks its existence.
- A2 A quote valid "until" a date is judged by UTC's date, not the lab's (evening requests).
- A3 Replace withdraws the old quote "from the new one's first day": a TR submitted that same day names
  either one.
- A4 Customer-typed text (chemical free text, quote request notes) in PDF file names or the
  `Content-Disposition` header: quotes and CR/LF.
- A5 Gapless TR numbers under two concurrent submits on Postgres (lock held?); a rolled-back submit leaves
  no gap.
- A6 Invoicing a TR with a partial report, then a complete one: two invoices or one, and the status?
- A7 The outbox shows reset links to anyone who manages users, including links for higher roles (lens 2).

## IT

**Scope:** `main.py`, `config.py`, `db.py`, `oss_guard.py`, `security.py`, `auth.py`, `accounts*`,
`totp.py`, `labs.py`, `demo_seed.py`, `cli.py`, `migrations/`, `refdata*`, `reference*`, `capabilities.py`,
`personas.py`, the pins, `render.yaml`, Docker. Module: slim-domain.

**Big-picture questions**
- Operating it: what does a lab admin need that isn't there, such as a health page beyond `/health`, a list
  of sessions, an audit of capability grants, backups, a log of who changed what in reference data?
  (Ideas only; the VPS stays deferred.)
- Demo labs: what does a visitor see on the first click, how long is a lab kept, and what happens to a
  visitor mid-deploy?
- Pins: does a pin bump have a checklist (upgrade_lab, the validation signature going stale, CI)?

**Seams:** host ↔ every module's migrations (one database, many version tables) · labs ↔ schemas
(search_path) · reference cache ↔ refdata edits per lab · slim-domain sync from private (R6) · personas
↔ MFA · bearer token ↔ `LIMS_ROUTES`.

**Predicted bugs**
- T1 `upgrade_lab` on deploy: does it run app migration 0018 (new table plus append-only trigger) in every
  claimed lab schema, with the trigger created in that schema and not public?
- T2 `cli empty-public` and the lab sweep: do they cover the new tables (`qc_acknowledgements`,
  `spec_*`, `account_*`)?
- T3 Login throttle behind Render's proxy: keyed on the proxy's IP (everyone locked out together) or on a
  spoofable `X-Forwarded-For`.
- T4 Persona switching mid-MFA-setup or a supervisor persona: is MFA truly skipped only for ASSERTED
  sessions?
- T5 A reference cache refresh after a refdata edit in one visitor's lab reaching the shared lab's cache
  (D-141, lens 10).
- T6 Migration downgrade 0018 → 0017 on Postgres with the trigger present.
- T7 `SECRET_KEY` unset in development: sealed MFA secrets, CSRF and sessions all survive a restart?

---

## Afterwards

Jamie's walkthrough comments, then one cross-department pass: the TR traced end to end through all four,
with only the seams no department review owned. Update `REVIEW_CHECKLIST.md` with the new guard tests.
