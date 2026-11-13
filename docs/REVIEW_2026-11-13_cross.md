# Cross-department pass, 2026-11-13

The plan's last step (`REVIEW_PLAN_2026-11-12.md`, "Afterwards"): one TR traced end to end through all four
departments, looking only at the seams no department review owned. The plan puts Jamie's walkthrough before
this pass; **the walkthrough hasn't happened yet**, so its comments are not in here. Nothing was fixed.
Verified by reading one function at a time (slim-samples and invoicing V5 at the host's pins); no test was
run or kept.

The trace: a quote request and its quote (Admin) → a TR naming it → check-in and bottles → prep, import, QC
flags, review, release, report (Lab) → a spec change and a complaint (Quality) → an amended report, its
sending and the invoice (Admin) → retention and disposal (Lab) → accounts, capabilities and demo labs (IT)
around all of it. Ids X-1….

## Bugs (confirmed; each with the fix and its guard test)

**X-1, X-3 and X-4 built the same day** (Jamie: "fix x1 but for x2, what if someone accidentally disposes of
a bottle? Fix x3-4, too"). X-2 was then built as Jamie redirected it (below, under X-2). X-3 is built as recommended, with the *no charge* fact.

| # | Seam · lens | Finding | Fix | Guard |
|---|---|---|---|---|
| X-1 | Admin A-1 × quote withdrawal (D-140) · 3 | **A quote can be withdrawn from a day in the past, and that now re-prices requests already received under it.** Since A-1 an invoice is priced by the quotes in force on the day the request was received. `withdraw_quote` takes any "from" day the form gives, down to the quote's start date (V5 checks only start and expiry). A withdrawal dated last month makes every request received since then, not yet invoiced, price at the default list or another quote. Replace does the same when the new quote's first day is typed in the past (it withdraws the old one from `max(new start, old start)`). Read, not run. | **Built:** `quotes.withdraw` refuses a day before the lab's today ("requests received under it keep its prices"); Replace withdraws the old quote from the latest of the new one's first day, the old one's and today. | `test_quotes.py::test_x1_a_withdrawal_never_reaches_back_over_requests_received` (also an older withdrawal test now expects this refusal for a day before today) |
| X-2 | Lab bottles × Quality complaints, Lab reruns · 3 | **A bottle can be disposed of while a complaint on its request is open, or while one of its results waits for a rerun.** `custody.dispose` refuses only a bottle on hold, and a hold is placed by hand: the hold form even suggests "complaint, open nonconformity" as a reason. Nothing places one when a customer raises a complaint (D-130) or a reviewer requests a rerun. Past the retention date no reason is needed, so the item a complaint is about can go in the bin the day the complaint arrives. Read, not run. | The host refuses a disposal while the request has an open complaint, or while a result of the sample + analysis the bottle serves has a rerun requested; the refusal names it. The hand-placed hold stays for everything else. | Not built (see below). |

**X-2, built differently** (Jamie, after the first answer: "what if a bottle was supposed to be held and
someone accidentally disposed of it irl … if someone 'disposed' of the bottle in software but not irl, then
that's a problem. There needs to be an undo button to reflect reality"). The record follows what happened,
both ways, so the refusal proposed above was the wrong fix:

- *Needed, and really gone.* A bottle is needed while it is on hold, its request has an open complaint, or a
  rerun is requested on a result of an analysis it serves (`samples.needed_as_evidence`). Its page says so.
  Disposing of it is allowed, but only with a reason, and it raises a nonconformity in the same transaction
  (`samples.disposal_nc`, by whoever recorded it, the reasons and their words in it). A held bottle is
  recorded as gone *despite the hold*: a supervisor or quality, with a reason (slim-samples S-13).
- *Recorded as gone, but still here.* "Undo: it is still here" on a disposed or returned bottle's page: a
  supervisor or quality, a reason and where it is, within the lab's `undo_days` (demo 7). The bottle is active
  again; the disposal and the undo stay in its history (slim-samples S-12).

Guards: slim-samples `test_s12_…`, `test_s13_…`; host `test_samples.py::test_x2_a_needed_bottle_gone_all_the_same_is_recorded_with_a_reason_and_raises_an_nc`,
`…::test_s12_a_disposal_recorded_by_mistake_is_undone_from_the_bottle_page`. slim-samples is pinned at `88f25b8`.

## For decision (ideas, each with a recommendation; not built)

| # | Question | What is there now | Recommendation |
|---|---|---|---|
| X-3 **Built** | A PT sample at invoicing (Quality × Admin) | A PT sample comes in as an ordinary request from the scheme (the demo's customer Z, D-128) and a PT round points at it. Invoicing knows nothing of PT: once its report is complete it waits in *to invoice* on the board, and sending the invoice bills the scheme at the default list. The lab pays the scheme, not the reverse. | A request a PT round names is "not billed": the board shows it under *complete*, and the invoice button says why instead of invoicing. Closing it needs a status decision: either a *no charge* fact that moves it to *invoiced* (D-99: statuses follow facts), or leaving it at *complete report* for good. Recommend the *no charge* fact, recorded like an invoice row. **Built:** `invoices.close_no_charge` (staff; only a complete request a PT round names): it moves to *invoiced* with the reason on the invoice row (`tr_invoices.no_charge_reason`, migration 0019); invoicing such a request is refused; the board offers "PT sample: close, no charge" and shows it under *No charge*. Guard: `test_invoicing.py::test_x3_a_pt_sample_is_closed_as_no_charge_never_invoiced`. |
| X-4 **Built** | Open nonconformities and bottles | An NC's impact lists released results, not requests, so X-2's rule can't reach it directly. | When an NC's impact assessment names results, offer "hold their bottles" on the NC page. **Built:** a quality manager's "Hold their bottles" on an open NC holds every bottle still in the lab that served a touched result, the NC as the reason; lifted one by one as ever. Guard: `test_quality_audit.py::test_x4_an_ncs_touched_results_bottles_go_on_hold`. |

## Refuted / checked sound

- **A customer's group** (Quality Q-1 now reads it) can't be changed from `/reference`: only an analysis's display group is editable there, so the spec four-eyes (D-143) can't be bypassed by moving a site to another group.
- **A display link** (Lab, D-138) made by someone whose account is later deactivated (IT) keeps working. Accepted: it is a lab screen, not a personal credential, and it stays revocable.
- **Cancel × quotes and invoices:** a cancelled request names its quote for reference only and can't be invoiced (only a complete report is).
- **A spec approval** (Quality) re-evaluates the unreleased results of a cancelled request too: harmless, as Lab L-5 (nothing can be released).
- **An upheld complaint → a correction → an amended report → its sending:** the amended report replaces the old issue, and the message says the invoice is unchanged (A-2).
- **An instrument taken out of service after results were released:** the equipment NC's impact lists the released results in its window (D-126), whichever department reads it.
- **Personas acting across departments** are ASSERTED in every module's record, and the four-eyes checks (release, spec approval, complaint outcome) compare user ids, so one persona can't approve its own act.

## The checklist

`REVIEW_CHECKLIST.md` now names the department reviews and the new guards under their lenses (L-1, L-2,
L-3, Q-1, Q-2, Q-3, A-1, T-1). Not under a lens, being neither: L-4 (a refused form keeps what was typed) and
A-2 (a sentence in a message); their guards are named in their reviews.
