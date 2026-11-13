# Department review: Lab, 2026-11-12

The first of the four department reviews (`REVIEW_PLAN_2026-11-12.md`), with `REVIEW_CHECKLIST.md`. Scope:
check-in and bottles, prep, import and results, review and release, QC warnings, sign-off and issue, the
day's board; slim-samples, slim-prep, slim-data at the host's pins. Nothing was fixed. Verified by reading
one function at a time and by scratch tests on SQLite (not kept as files: each would be a failing test until
its fix; the steps are in the finding). Run: `tests/test_qc_warnings.py` (5 passed) and the scratch tests
only. Not run: Postgres, the full suite, the seeded demo in a browser.

Ranked. Ids L-1… are this review's; "plan L1" names the plan's prediction.

## Bugs (confirmed; each with the fix and its guard test)

**L-1 to L-4 fixed the same day** (Jamie: "Fix L-1 through L-4"), in the host only. L-2 was first built as a host
lookup (`prep.AlreadyHeld`); on 2026-11-13 it moved into slim-prep as P-29 (Jamie: "move it into the module"),
and the host pins slim-prep at `6ee244c`.
The table says what was found; what was built is in the Fix column.

| # | Where · lens | Finding | Fix | Guard |
|---|---|---|---|---|
| L-1 | QC warnings ↔ release · 3, 8 (plan L1) | **A clean rerun batch can't be released on screen.** `qc_warnings.for_release` reads the flags of *every* batch that holds the sample for the analysis; the batch page (`results_batch`) reads only its own. Tested: TR00001-01 on PB-1 (ACTION flag) then again on PB-2 (no flags): PB-2's page shows no QC panel and no reason field, the release is 409 "say why the result still stands", and the form has nowhere to say it. Through the API a reason is demanded for flags the reviewer was never shown. Same function also counts analytes that this release won't release (already released, or still waiting). | **Built:** `qc_warnings.for_release` answers for the page and the release alike: only results a release would take now (reviewed, cleared), each by the batch its current version was made from (slim-data's `version_batches` when more than one batch holds the sample). The form names the flags it asks about. | `test_qc_warnings.py::test_l1_a_clean_rerun_batch_releases_without_a_reason`, `…::test_l1_the_flagged_batch_asks_wherever_the_release_is_made` |
| L-2 | TR status ↔ prep · 3, 8 | **One cancelled (or invoiced) request freezes a shared prep batch.** slim-prep re-checks every sample's request on each save and each send (`sample_problems`, P-23). Tested: PREP-0001 holds TR00001-01 and TR00002-01, sent; TR00002 is cancelled; correcting a misread cup of TR00001-01 is 422 "TR00002-01: its request was cancelled". The correction can't be recorded or sent, so TR00001's results keep the wrong dilution. Invoiced is the same path (`SampleState.CLOSED`; read, not run): a prep correction after an invoice, the amended-report case, is refused. A blocking bottle condition noted later does the same through `refuse_blocked`. | **Built:** slim-prep P-29: a correction, a move to new rules and a correction's send check against their TR only the samples the batch didn't already hold; a sample new to it is checked as ever. In the host, `refuse_blocked` runs on a new batch and a first send only. A first send of a cancelled request's batch is still refused; a cancelled request's results stay unreleasable (`require_open_tr`). | `test_prep.py::test_l2_a_shared_batch_is_corrected_after_one_request_is_cancelled`; slim-prep `test_p29_…` |
| L-3 | Samples ↔ import · 8 (S-04) | **A blocking bottle stops the prep book but not an instrument file.** Tested: TR00001-01's only bottle is "unsafe to handle"; saving a prep batch is 422 (right); uploading a CSV that registers its own batch PB-1 for the sample is 201, evaluated. S-04 says no prep batch takes the sample until the lab acts; a file-registered batch (D-66) is one. | **Built:** `results.blocked_samples`: `_import` refuses (422, nothing kept) a file that puts a blocked sample on a batch that didn't hold it before, a new batch or an addition; readings for a batch that already held the sample still import. | `test_prep.py::test_l3_a_file_cant_register_a_batch_for_a_blocked_sample` |
| L-4 | Release form · UX (plan L8) | **A refused review or release loses what was typed.** `_back` redirects with a flash; note, solo and the QC reason are gone (tested: the note is not on the page after a 409). The solo wait ("n min to go") makes this the common refusal. | **Built:** a refused review or release renders the batch page again (its 409) with the reason and that form as posted, not a redirect. | `test_results.py::test_l4_a_refused_release_keeps_what_was_typed` |
| L-5 | TR status ↔ results · 3 | A cancelled request's drafts can still be reviewed with the batch and re-evaluated (tested: review 200 `reviewed: 2`, re-evaluate adds 2 versions; release is 409, right). `results.cancel_blocker` says they are "neither released nor decided on". | Not worth a code fix: a batch review signs the batch, and nothing reviewed can leave the lab. Correct the docstring to say what is true. | none |

## For decision (ideas, each with a recommendation; not built)

| # | Question | What is there now | Recommendation |
|---|---|---|---|
| L-6 | One sample on one screen | The request page links to bottles, the report, the invoice and complaints, not to its prep batches or results batches. A prep batch page links to neither the request nor its results batch. The only end-to-end view is quality's vertical audit, and only for a released report. A person has to know `/prep/batches/{code}` and `/results/batches/{code}`. | A "lab trail" on the request page, one row per sample × analysis: bottles → prep batch → results batch and runs → release → report issue → retention date, from the indexes the day's board already reads. Link the prep batch and results batch pages to each other. |
| L-7 | Reruns | A rerun request lives in slim-data; the boards show a "Rerun requested" chip, and the day's board keeps the work under *In review*. The prep book is told nothing. | The day's board shows rerun-requested work as "to rerun" in the *On instrument* column; the prep home lists them. No new record: read from the result's events. |
| L-8 | Corrections after release | The old report stays readable (the stored file, by hash) and the sending says "replaces issue n". The amended line says only "Amended: corrects a result reported before": not the earlier value, not why. ISO 17025 §7.8.8 asks that the change is identified and, where appropriate, the reason. Also `amended` is permanent on a line, so every later issue of that request is "(amended)" and amends its predecessor even when it only adds results. Nothing flags a latest issue that was never sent. | The amended line names the value it replaces, the issue that showed it and the correction's reason (slim-data keeps it). An issue is "amended" only when it carries a version newer than the previous issue showed. A "not sent" marker on the latest issue. |
| L-9 | Cancel with work in the lab (plan L4) | The day's board drops a cancelled request (refuted there). The prep book still lists its batch with no mark; an unsent one can never be sent (guarded) and must be re-made without the sample. Bottles stay active with their retention dates and no mark. | Mark cancelled samples on the prep home, the prep batch page and the bottle pages ("request cancelled: return or dispose"). No new state. |
| L-10 | Check-in without bottles (plan L9, L3) | The machine contract allows it (documented). Prep then has no bottles to read and goes ahead silently, and no page lists received samples with no bottle logged (only `/samples/log/{tr}`, if you know the request). A condition noted after a batch is saved is re-checked at send, never after. | A "no bottles logged" mark on the day's board and the prep picker; a warning (not a block) when a prep batch takes such a sample. After sending, show the bottle's flag on the results batch page; no block (the work is done). |
| L-11 | QC warnings (D-144) | An acknowledged ACTION flag ends at the acknowledgement. The panel lists every flag of a chart. | Offer "raise an NC" from the acknowledgement, pre-filled with the flags, as a spec change does. Collapse repeats: latest per chart and code, with a count. |
| L-12 | The solo path | The wait is policy data (`solo_review_minutes`, 15 in the demo) everywhere. The page says "after the policy's wait" without the number; a reviewer learns it from the refusal. A releaser isn't shown that the review was solo (the vertical audit lists it afterwards). | Print the minutes and, per result, when the wait ends; mark solo-reviewed rows on the batch page. |

## Refuted / checked sound

- **Plan L2** (naive vs aware timestamp on SQLite): refuted. Both sides come through slim-data's `UtcDateTime`, aware on SQLite too; `test_qc_warnings_demo.py` makes the comparison on SQLite.
- **Plan L5** (equipment judged "now"): refuted. `run_equipment` judges the instrument on the run's day in the lab's zone. By the day, not the hour: an instrument taken out at 14:00 refuses that morning's run. Accepted.
- **Plan L6** (midnight, DST): refuted. Due and late compare dates; "today" is `LabClock.today()`; no instant is compared.
- **Plan L7** (re-evaluation of a reviewed result): refuted. A new version is a draft; review and acceptance belong to the version and are needed again; the chip returns to "to review".
- **Plan L3** as a bug: not one; see L-10 and L-2.
- A rerun measured again with identical values still makes a new version (tested): the request can't stick.
- A cancelled request: release 409; a cancel is refused once a result is released, so no report can follow.
- Prep's equipment check uses the prep day in the lab's zone; a refused import rolls back the run and the status change with it.
- Lenses 1, 2, 6, 7, 9, 10: nothing new in this department since 11-09.

Untested: the invoiced case of L-2 (read only); everything on Postgres.
