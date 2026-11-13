# Review checklist

Used for every review, app or module (Jamie, 2026-10-28). Bounded: answer each lens for the thing under
review, list findings ranked, stop. **Every fixed finding ends in a guard test**, named below, so later
reviews look for new holes, not old ones. First full review: `REVIEW_2026-10-28.md`; slim-quality:
`REVIEW_2026-10-30.md`;
seams between modules: `REVIEW_2026-10-30b.md`; after slim-quality 5c: `REVIEW_2026-11-09.md`; by department (plan:
`REVIEW_PLAN_2026-11-12.md`): Lab `REVIEW_2026-11-12_lab.md`, Quality `REVIEW_2026-11-13_quality.md`.

**When:** a module review when a module is wired in; an app review (the seams between modules) every
two or three modules. **Output:** `docs/REVIEW_<date>.md`, findings only, each with the fix and its test.

| # | Lens | Ask | Guards so far |
|---|---|---|---|
| 1 | **Who may see it** | Does every listing and lookup take an explicit scope? Can a customer reach another's data by ID, filter or a missing ID? | `test_scope.py`; customer 404s in `test_api_submissions.py`, `test_history.py` |
| 2 | **Who may do it** | Is every action checked against role / capability, four-eyes, and "not above your own role"? Personas? The bearer token? | `test_results.py` (user admin), `test_personas.py`, `test_lims_contract.py`; `test_q2_a_spec_approver_doesnt_audit_reporting` (auditor independence); `test_a2_…` (reset links in the outbox) |
| 3 | **State says the truth** | Can a status or flag be set apart from the fact it stands for? Does a cancel or correction reach every module that holds work for it? | D-99 tests in `test_api_submissions.py`, `test_results.py`, `test_prep.py`; `test_q1_approving_a_stale_profile_proposal_is_409`; slim-quality `test_q5_…`; `test_s1_…` (spec approval vs. a batch that can't be evaluated); `test_l2_a_shared_batch_is_corrected_after_one_request_is_cancelled`; `test_q1_a_groups_limits_judge_its_sites_results` |
| 4 | **History is kept** | Is every record append-only or versioned, readable `as_of`? Does the newest version equal the current state? | `test_history.py`; slim-data / slim-prep append-only tests; `verify`; slim-quality `test_q4_…` (concurrent decisions); `test_q3_an_amended_reports_audit_shows_only_its_own_acknowledgement`; slim-quality `test_q7_…`, `test_q8_…`; `test_q7_cli_verify_…` |
| 5 | **Whose clock** | Is "today" the lab's (`LabClock`)? Are instants stored as instants and dated in the lab's zone? | `test_prep.py` (evening batch); `test_q2_the_pages_show_the_labs_time` |
| 6 | **Untrusted input** | Files (CSV, XML, paste), form fields, JSON bodies: size, encoding, DOCTYPE, formula cells, odd numbers, crafted field names → 422, never 500 or silent loss. | `test_qtegra.py` (DOCTYPE), body cap in `test_app.py`; `test_q3_…` (bad series ids); `test_f1_…` (quote prices without analyses) |
| 7 | **Output escaping** | Is everything typed by people escaped in HTML, CSV and file names? CSP still without `unsafe-inline`? | `test_reports.py` (escaping), CSP in `test_app.py`; `test_i1_…` (accounting CSV) |
| 8 | **Module seams** | What does one module trust from another (IDs, codes, versions)? Can either side change it underneath? Does a correction keep what must not change? | (slim-prep B1, B2, B5, B6, B9: fixed 2026-10-28); slim-quality `test_q6_…`; slim-data `test_o1_…`; `test_l1_…` (QC flags by the batch a result was made from), `test_l3_…` (a blocked bottle vs. a file's own batch) |
| 9 | **Sessions and auth** | Login limits, session rotation, CSRF on every state change, open redirects. | `test_auth.py`, CSRF sweep in `test_app.py`; `test_a1_…` (MFA after a key change) |
| 10 | **Demo isolation** | Can one visitor see or change another's lab? Does anything reach a non-demo database? | `test_labs.py`, `test_oss_guard.py`; `test_l1_…` (labs after a deploy); `test_l2_…` (a visitor's writes never reach the shared lab) |

A lens with nothing new is written as "nothing new" in one line. A finding not worth fixing says why.
