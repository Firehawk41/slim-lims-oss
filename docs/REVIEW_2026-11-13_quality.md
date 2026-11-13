# Department review: Quality, 2026-11-13

The second department review (`REVIEW_PLAN_2026-11-12.md`; Lab: `REVIEW_2026-11-12_lab.md`), with
`REVIEW_CHECKLIST.md`. Scope: `quality*.py`, `quality_records*`, `quality_insights*`, `quality_system*`,
`spec_changes.py`; slim-quality at the host's pin. Nothing was fixed. Verified by reading one function at a
time, and one timing on the seeded demo history (SQLite). No test file kept. Not run: Postgres, the full
suite, the demo in a browser.

Ranked. Ids Q-1… are this review's; "plan Q1" names the plan's prediction.

## Bugs (confirmed; each with the fix and its guard test)

| # | Where · lens | Finding | Fix | Guard |
|---|---|---|---|---|
| Q-1 | Spec limits ↔ slim-domain ↔ slim-data · 3, 8 (plan Q7, found nearby) | **A group's spec limits never judge a result.** slim-domain's rule is that a site with no limits of its own for a chemical uses its group's (`SpecificationService.limits_owner`). The host's `SpecReader` reads the exact customer only, and slim-data's lookup passes the sample's own customer, so evaluation never falls back to the group. D-143 lets quality propose a set on a group: approval re-evaluates the sites' batches (`_matching_samples` takes the group's sites), but they read their own, empty, limits and nothing changes. Meanwhile the decision's `now_differ` lists released results judged against the group's limits. The record says results would now read otherwise; evaluation never applies those limits. Confirmed by reading (`SpecReader.__call__`, `SlimReferenceLookup.limits_for`, `approve`); not run end to end. | `SpecReader` takes the owner the way slim-domain does: the site's own set if it has one for the chemical, else its group's (the parent from the reference data). `approve`'s `judged_by_the_proposal` gives the proposed limits to every customer whose owner is the set's customer. | `test_specs.py::test_q1_a_groups_limits_judge_its_sites_results` |
| Q-2 | Internal audits ↔ spec changes · 2 (plan Q1, partly) | **Whoever proposed or approved a spec change can audit §7.8.** Spec limits decide the report's statements of conformity (§7.8.6), but `involved_in_clause` reads only `lab_acts` (prep, enter, review, release, sign). `spec_proposals` and `spec_decisions` never count, so a quality manager who approved limits last month can be planned as the auditor of 7.8. QC acknowledgements: refuted, since the acknowledger is the releaser, already counted under RELEASE. | `involved_in_clause("7.8")` adds the proposers (`proposed_by_id`) and approvers of spec changes in the year (`spec_decisions.by` is an email: map it to the user's id). | `test_quality_system.py::test_q2_a_spec_approver_doesnt_audit_reporting` |
| Q-3 | Vertical audit · 4 (plan Q2, the other way round) | **On an amended report, an earlier version's QC acknowledgement shows under the new version.** `audit_page` takes every acknowledgement of the sample + analysis made before the issue. The predicted case (a later acknowledgement on an earlier report) is refuted by that time filter. The reverse holds: v1 released over an ACTION flag with a reason, corrected, v2 released clean; the amended report's audit shows v1's reason under v2, as if v2 needed one. Read, not run. | Keep, for each line, only acknowledgements made after the previous version's release and up to the release of the version on the report (the result's RELEASED events give both). No migration. | `test_quality_audit.py::test_q3_an_amended_reports_audit_shows_only_its_own_acknowledgement` |

## For decision (ideas, each with a recommendation; not built)

| # | Question | What is there now | Recommendation |
|---|---|---|---|
| Q-4 | Chart cost (plan Q4) | Timed on the seeded demo: 130 QC runs over 90 days draw 38 charts (2,496 points) in 0.15 s. The batch page draws once (D-144). The vertical audit draws once per distinct version time (`charts_then[r.made_at]`), so a report with ten lines made at ten moments draws ten times; at ten times the history, about 1.5 s per draw. | Cache the audit's draws by the newest QC reading recorded before each moment, not by the moment: lines made between the same two QC runs share one draw. Not worth more until the history is real. |
| Q-5 | Does every signal end somewhere? | A chart flag, an instrument event, a failed calibration, an unsatisfactory PT score, a complaint, a competence finding and a risk that happened each offer "raise an NC". Dead ends: an acknowledged ACTION flag at release (also Lab L-11); a spec change's "now judged otherwise" list offers one NC for all. A refused prep (overdue balance, expired lot) needs none: nothing was done. | Offer "raise an NC" from a QC acknowledgement (Lab L-11). |
| Q-6 | The management review pack | Readiness's `Facts` and the §8.9.2 pack are fed by slim-quality's records and the host's acts. They don't count QC acknowledgements (D-144), spec changes (D-143) or account events (D-142). | Add three host inputs to the pack: releases over an ACTION flag (and how many led to an NC), spec changes approved with the released results now judged otherwise, MFA resets and reset links. Needs a slim-quality change (`Facts`). |
| Q-7 | Competence (D-132) | slim-quality's `Act` has prep, enter, review, release and sign. Approving spec limits, editing reference data and acknowledging a QC flag aren't acts, so the register can't authorise or flag them. | Add an act "approve spec limits" in slim-quality; a QC acknowledgement stays under release; reference data stays a capability only. |
| Q-8 | PT after an amendment (plan Q3) | A round keeps the values it submitted, each with its result's version (`result_ref`): right, that is what the scheme received. Nothing says the result was amended since. | Show "amended since submitted (vN now)" on the round. No change to the record. |
| Q-9 | Uncertainty going stale (plan Q6) | Refuted as predicted: QC limits don't enter the estimate (precision from recoveries, bias from PT and recoveries). What can change after signing are its inputs: a QC reading excluded later, a PT round scored later in the window. | Show "records in its window changed since it was signed", as validation goes stale on a pin bump. |
| Q-10 | One quality home | `/quality` opens on the charts; what needs attention is spread over readiness, NCs, complaints, equipment and PT. | Open `/quality` with readiness's "needs attention" lines (overdue, open long, expired in use, unsatisfactory PT, today's ACTION flags), each linking to its page. |

## Refuted / checked sound

- **Plan Q1, QC acknowledgements:** the acknowledger is the releaser, already in `lab_acts` as RELEASE (spec changes: Q-2).
- **Plan Q2 as predicted:** an acknowledgement made after an issue is filtered out by the issue's time (the reverse is Q-3).
- **Plan Q3:** PT keeps the version it submitted (see Q-8).
- **Plan Q5:** a complaint's approver may not have released on the request (`involved_in` reads every result event); the acknowledger is the releaser.
- **Plan Q6:** refuted (see Q-9).
- **Plan Q7 as predicted:** group membership is read at approval, so the sites re-evaluated are the group's sites then. Accepted; the real gap is Q-1.
- Spec approval: four eyes, staleness and the one transaction (R7) hold; warning limits change nothing evaluated.
- Lenses 1, 5, 6, 7, 9, 10: nothing new in this department since 11-09.

Untested: Q-1 and Q-3 end to end (read only); everything on Postgres.
