# Review checklist

Used for every review, app or module (Jamie, 2026-10-28). Bounded: answer each lens for the thing under
review, list findings ranked, stop. **Every fixed finding ends in a guard test**, named below, so later
reviews look for new holes, not old ones. First full review: `REVIEW_2026-10-28.md`.

**When:** a module review when a module is wired in; an app review (the seams between modules) every
two or three modules. **Output:** `docs/REVIEW_<date>.md`, findings only, each with the fix and its test.

| # | Lens | Ask | Guards so far |
|---|---|---|---|
| 1 | **Who may see it** | Does every listing and lookup take an explicit scope? Can a customer reach another's data by ID, filter or a missing ID? | `test_scope.py`; customer 404s in `test_api_submissions.py`, `test_history.py` |
| 2 | **Who may do it** | Is every action checked against role / capability, four-eyes, and "not above your own role"? Personas? The bearer token? | `test_results.py` (user admin), `test_personas.py`, `test_lims_contract.py` |
| 3 | **State says the truth** | Can a status or flag be set apart from the fact it stands for? Does a cancel or correction reach every module that holds work for it? | D-99 tests in `test_api_submissions.py`, `test_results.py`, `test_prep.py` |
| 4 | **History is kept** | Is every record append-only or versioned, readable `as_of`? Does the newest version equal the current state? | `test_history.py`; slim-data / slim-prep append-only tests; `verify` |
| 5 | **Whose clock** | Is "today" the lab's (`LabClock`)? Are instants stored as instants and dated in the lab's zone? | `test_prep.py` (evening batch) |
| 6 | **Untrusted input** | Files (CSV, XML, paste), form fields, JSON bodies: size, encoding, DOCTYPE, formula cells, odd numbers, crafted field names → 422, never 500 or silent loss. | `test_qtegra.py` (DOCTYPE), body cap in `test_app.py` |
| 7 | **Output escaping** | Is everything typed by people escaped in HTML, CSV and file names? CSP still without `unsafe-inline`? | `test_reports.py` (escaping), CSP in `test_app.py` |
| 8 | **Module seams** | What does one module trust from another (IDs, codes, versions)? Can either side change it underneath? Does a correction keep what must not change? | (slim-prep B1, B2, B5, B6, B9: fixed 2026-10-28) |
| 9 | **Sessions and auth** | Login limits, session rotation, CSRF on every state change, open redirects. | `test_auth.py`, CSRF sweep in `test_app.py` |
| 10 | **Demo isolation** | Can one visitor see or change another's lab? Does anything reach a non-demo database? | `test_labs.py`, `test_oss_guard.py` |

A lens with nothing new is written as "nothing new" in one line. A finding not worth fixing says why.
