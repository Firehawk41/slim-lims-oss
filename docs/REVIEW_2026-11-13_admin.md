# Department review: Admin, 2026-11-13

The third department review (`REVIEW_PLAN_2026-11-12.md`; before it Lab `REVIEW_2026-11-12_lab.md`, Quality
`REVIEW_2026-11-13_quality.md`), with `REVIEW_CHECKLIST.md`. Scope: requests and the customer's view
(`routes.py`, `services.py`, `repositories.py`, `scope.py`, `api.py`), quotes (`quotes*.py`), invoices
(`invoices.py`, `demo_prices.py`), report delivery (`delivery.py`, the outbox), `about.py`; invoicing V5 at
the host's pin. Nothing was fixed. Verified by reading one function at a time; no test was run or kept.

**No confirmed bug this time.** The plan's seven predictions are refuted below. The department's real gap is a
decision nobody has made yet (A-1): which day an invoice is priced on. Ids A-1… are this review's; "plan A1"
names the plan's prediction.

## For decision (ideas, each with a recommendation)

**A-1 and A-2 built the same day** (Jamie: "Build A1 and 2"), host only, V5 unchanged. Guards:
`test_invoicing.py::test_a1_a_request_is_priced_by_the_quotes_in_force_when_it_was_received`,
`…::test_a1_the_invoice_page_says_what_priced_it`,
`test_signatory_delivery.py::test_a2_an_amended_report_after_the_invoice_says_the_invoice_is_unchanged`.
A-3…A-5 are not built.

| # | Question | What is there now | Recommendation |
|---|---|---|---|
| A-1 | Which day prices an invoice? (big picture: money in a flow) | `invoices.mark_sent` prices on the day the invoice is sent (`on=service.lab_today()`); V5's `invoice` takes the customer's quotes valid that day. The quote a request named (`tr_quotes`) is shown on the request page and never read again (D-139: "for reference"). So a request submitted under a quote that expires, or is replaced at a new price, before the report is complete is billed at the price of the invoicing day: the default list, or the new quote. Nothing on the invoice page says the named quote didn't price it. D-139 and D-140 don't say which day prices. | **Built:** `invoices.priced_on`: V5 prices on the day the request was received (the quotes in force then). The invoice page has a *Priced by* column (the quote, or the price list, from each line's price id: `invoices.priced_by`), says which day priced it, and says so when the quote the request named priced no line. |
| A-2 | An amended report after the invoice (big picture) | The status stays *invoiced* (forward only, D-99). The customer gets the amended report when staff send it (D-124). The invoice isn't touched: V5 refuses a second invoice, and every ordered analysis was already reported before invoicing. | **Built:** an amended report's message to an invoiced request adds "Your invoice for this request is unchanged." (`delivery.message_for`). |
| A-3 | The outbox (big picture) | Every staff member reads every entry: report sendings, quote sendings, account messages (reset links are never kept, review 11-09 A2). Customers read none. | Accepted: one lab's outbox, staff only. Revisit when a real transport is plugged in (D-124). |
| A-4 | The customer's journey (big picture) | Quote request → answer (a quote or a reason) → a request naming it → status in the customer's columns → reports and their PDFs → the invoice page → complaints: each is on the customer's own pages, scoped. The status words are the board's customer columns. | Accepted. Optional: show the named quote, and A-1's "priced by", on the customer's invoice page. |
| A-5 | Group quotes | Groups are invoicing umbrellas (`reference.py`), but quotes are made only for customers that submit (`customer_choices`), and V5 prices from the request's own customer. A group-wide price agreement means one quote per site. Spec limits fall back to the group (Quality Q-1); prices don't. | Ask the lab whether group price lists exist. If they do, V5 prices a site's request from its own quotes, then its group's, then the default list. |

## Refuted / checked sound

- **Plan A1** (routes without `Scope`): refuted. Every route that takes a TR number (complaints, reports, their PDF, the invoice page, versions, status, check-in, the samples log) goes through `SubmissionService.get`, which 404s another customer's TR (`_can_see` → `Scope.for_user`). Quotes go through `_quote_or_404` (`visible_to`); quote requests and their answers are the customer's own; staff routes are `RequireStaff`.
- **Plan A2** (quote validity by UTC's date): refuted. Every quote state, `own_in_force` and invoicing date use the lab's day (`clock.today()`, `service.lab_today()`).
- **Plan A3** (Replace on the same day): refuted. The old quote is withdrawn from the new one's first day and V5's `valid_on` treats the withdrawal day as not valid, so on that day only the new quote is in force. A request submitted that morning, before the Replace, keeps the old quote's name; what prices it is A-1.
- **Plan A4** (customer text in file names): refuted. Download names are built from a TR number already found in the database, a batch code (P-26's safe characters), a quote number or a fixed name; an instrument file's name is reduced to `[A-Za-z0-9._-]`. Customer text reaches only autoescaped templates and the PDFs made from them.
- **Plan A5** (gapless TR numbers under concurrency): refuted. The counter `UPDATE` row-locks until commit and rolls back with a failed insert (`repositories.add`); `test_repository.py::test_concurrent_submissions_get_unique_contiguous_numbers` runs it on Postgres.
- **Plan A6** (partial then complete report invoiced): refuted. Only a complete report is invoiced (host and V5), V5 refuses a second invoice, and `billable` counts every analysis any issue showed.
- **Plan A7** (reset links in the outbox): refuted. Fixed on 11-09 (A2): the link is never kept; guard `test_accounts.py::test_a2_the_outbox_never_holds_a_reset_link`.
- A named quote is checked against the customer's own quotes in force on the lab's day, for staff submitting on a customer's behalf too.
- Lenses 1, 2, 5, 6, 7, 9: nothing new. Lens 3: see A-1. Lenses 4 and 10: nothing new in this department.
