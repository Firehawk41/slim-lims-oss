# Dev diary: who decided what

A running record of how decisions are split between Jamie (designer, domain expert) and Claude (builds,
proposes). `DECISIONS.md` in each repo says *what* was decided; this says *who* moved it and how. For
Jamie's writing on coding and design philosophy, so entries are short and honest, including where Claude
was wrong.

**Kinds of entry**
- **J** Jamie's call: his idea, or domain knowledge Claude didn't have.
- **C✓** Claude proposed, Jamie accepted as proposed.
- **C→J** Claude proposed, Jamie amended or overruled (what he changed, and why if he said).
- **J→C** Jamie disagreed or doubted, Claude's case changed his mind.
- **C→C** Claude changed its own mind on Jamie's information.
- **F** Claude found something unasked (a bug, a clash, a gap); Jamie decided what to do.

Tally and themes are updated now and then at the bottom.

## 2026-10-27/28: slim-prep, from nothing to merged

| Kind | What | Notes |
|---|---|---|
| J | **The module's purpose: "take the prep book out of Excel and into the LIMS."** | Jamie's framing; Claude laid out pros and cons. |
| J | The prep book's columns, from his Excel evaporation tab. | Pure domain input. |
| J | Dilution factors can be below 1: evaporation concentrates. | **Claude's earlier slim-data rule (dilution ≥ 1) was wrong**; became D-93. Domain knowledge Claude lacked. |
| C→J | MDL across cups: Claude's engine took the *larger* cup's MDL; Jamie: average the DF, and only when both cups are kept. | D-94. Domain intuition overruled a "cautious" default. |
| J | Cap weight is left out of the final weight ("lids get mixed up once evaporated"). | Bench knowledge; shaped Net = Gross − Tare − Cap. |
| J | Spike level = volume × concentration ÷ net mass; spike goes in its own cup. | Claude had to rephrase its question once before it landed. |
| J | Standards are "standardized": recipes 1000 ppm → 20 ppm → 50 ppb, saved as the lab's defaults. | Claude had asked where concentrations were typed; Jamie's answer changed the model to recipes (P-15). |
| C→J | Shelf life: Claude proposed fixed per standard, capped at the source; Jamie: per recipe step, the lab decides the cap, default capped. | P-16. |
| J | Raised the module boundary himself: "these sound like quality rules, but the lab does the prep: how do we keep boundaries but give each party access?" | Claude answered with rules-as-published-data behind a port (P-17); Jamie accepted. |
| C✓ | Design P-02…P-14 (methods as data, batch → sample → cup, append-only, handoff). | Accepted with "go until you need input". |
| C✓ | D-95 handoff (prep dilution beats the file's; a file's "1" means not given). | As proposed. |
| C✓ | D-96 corrections after handoff applied at evaluation, readings never edited. | As proposed. |
| J→C (self) | "What does slim-prep have to do with run order? Surely slim-data?" then, reading slim-raw-data, "I guess slim-prep builds what's loaded on the instrument." | Claude then split it: prep **plans** the run, data judges what **was** run (P-21). |
| J | Check-standard rule: between matrices and after 10 samples, count samples not cups, restart per matrix. | Lab practice, made a default, not code. |
| C→C | Claude read "average of elements ±25 %" as the lab's rule to build; Jamie: "I think judging each element is better science; that was an example of needed customisability." | Default became each-element; average an option (D-97). |
| J | An analyst may overrule a failed check with a reason, auditable; "a quality-section rule." | Became `RoleRule.flag` (D-97). |
| F | Table-name clash: slim-prep's `prep_batches` vs slim-data's in the shared DB. | Claude renamed to `prep_book_*` (P-19) before release. |
| F | `evaluate_batch` dropped run calibrations: `calibration@1` never judged in the app. | Jamie: "fix it first". D-98, replay-safe. |
| F | Architecture review (app and module). | Jamie reframed it: **a portfolio piece**, "not a product and not just a sandbox". |
| C→J | User admin: Claude proposed supervisors only; Jamie: supervisors by default, **but the lab customises it** (an analyst may be the LIMS specialist). | Customisability is a recurring Jamie theme. |
| C→J | Demo reset: Claude proposed a scheduled reset; Jamie: **fresh for every visitor** (an HR reviewer must never see the last visitor's rude words). | Portfolio audience drives the requirement. |
| J | Manual TR statuses are leftovers from before modules were wired; review after each module is wired. | Process rule from Jamie. |

## 2026-10-28: hardening after the review

| Kind | What | Notes |
|---|---|---|
| C→J | Fresh demo: Claude first proposed a reset; after Jamie's "fresh for every user", Claude pushed back on resetting at start (two visitors at once still share) and proposed a **lab per visitor**; Jamie agreed. | Pushback that changed the plan's mechanism, not its goal. |
| C✓ | D-99: statuses follow facts; cancel asks the modules. | Jamie's rule added: re-check hand-set statuses after each module is wired. |
| C→J | D-100: managing users is a capability, supervisors by default, grantable per person; nobody acts on a higher role. | Jamie's amendment (the lab customises) shaped it. |
| C✓ | D-101: login limits, the lab's clock for prep, DOCTYPE refused inside the parser, a report-escaping test. | |
| C→C | D-102 lab per visitor: Claude measured the seed (~3 s CPU, ~30 s on Render's free CPU) and added spare labs kept ready, and found public must hold no app tables. | Measuring changed the mechanism, not the plan. |
| J | Switch Render to labs and wipe the old demo database once (`empty-public --if-needed` on start). | |
| C✓ | D-103: listings take a Scope; a customer always gets their own, "everyone" is staff's, a lost customer ID refuses. | |
| C→C | D-104 TR history: Claude proposed change events, then built whole versions (as the modules keep theirs): no replay code to get wrong. | Logged as a change of Claude's own proposal. |
| F | Claude's review listed the Qtegra DOCTYPE as open; it was already refused (a byte check Claude wrote, which a UTF-16 file could pass). | The review was wrong; the fix still mattered. |

## 2026-10-28: slim-prep review items B1–B10

| Kind | What | Notes |
|---|---|---|
| C✓ | B1–B10 as one design turn: a TR port for slim-prep (P-23), corrections keep code / method / analysis / rules (P-24), entered and bounded prep times (P-25), a code series and safe codes (P-26), who with how the host knows (P-27); slim-data D-105. | Approved as proposed. Jamie asked for the B1 case first. |
| J | On B1: the weak point is the analyst saying what they prepped with no system eyes on it; an acceptable risk if slim-prep defers to the app. Barcodes would help but still trust the pour. | Claude split it: a wrong *record* (the port refuses it) vs a wrong *bottle* (only the bench's label check; barcodes later, slim-samples). |
| C✓ | A sample picker instead of typed IDs, from the same port. Jamie: "essential … win-win". | Removes typos instead of refusing them afterwards. |
| C→C | Building it: an invoiced TR needed its own state (CLOSED), and a self-review caught the API refusing a time without a zone, which it used to read as lab time. | Both fixed before the push. |

## 2026-10-28: how we work

| Kind | What | Notes |
|---|---|---|
| J | Asked why the hardening cost so many tokens, and invited criticism of his prompting. | |
| C✓ | Working defaults: settle design first, one approval per batch, one PR per repo, merge on green unwatched, quiet test runs, docs once per batch, a fresh session per batch. | Claude's overhead (PR event boilerplate, noisy logs, its own slips) was most of the cost; long "continue until input" runs and mid-build decisions the rest. |
| C✓ | `docs/REVIEW_CHECKLIST.md`: ten fixed lenses; a module review when wired, an app review every two or three modules; every fixed finding ends in a guard test. | Reviews look for new holes, not old ones. |
| C✓ | The three CLAUDE.md files say only what is true now; history stays in DECISIONS, the diary and git. | slim-lims's went from 22.7 kB to about 9 kB. |

## 2026-10-29: roadmap clean-up

| Kind | What | Notes |
|---|---|---|
| F | Phase 1 was done but still marked ◐; the prep-book and landing-page questions were stale; slim-data's CLAUDE.md still named the readers as next. | Claude first misread a stale local branch as "M2 part 2 next". |
| C✓ | Landing page and architecture diagram move ahead of slim-quality (now phase 2); phase 6 stays after Quality. | The sandbox is already public with no 30-second explanation; quotes, reference upkeep and password reset add little for a visitor (no email in the sandbox). |

## 2026-10-29: the landing page

| Kind | What | Notes |
|---|---|---|
| J | Audience: hiring managers and HR, even if it is later sent to LIMS companies. | Engineering points get a line and a link; no chemistry assumed. |
| J | Jamie writes every word of the page's prose: "we don't [want] AI slop in a portfolio piece". | Claude ships the structure, diagram and tests; the words sit in one file (`templates/about/_copy.html`) as short placeholders. Jamie then chose to merge with the placeholders and write later. |
| J | Names: SlimLIMS for the software, YourLab for the fictional lab running it. | Header "YourLab SlimLIMS"; footer "YourLab running SlimLIMS v0.1.0" (the app version; Jamie); the landing page is titled SlimLIMS. |

## 2026-10-29: slim-quality part 1

| Kind | What | Notes |
|---|---|---|
| J | Westgard is a real-time tool; Jamie's labs run many short runs on one calibration and report as they go. So: before release (the gate) and after release (trends) are separate jobs; part 1 is after release, the before-release warnings parked as part 3. | Jamie asked "before or after release?" until the split was plain. |
| J | ppt reality: contamination skews blanks and low checks; checks at 50–100 ppt, ±25 % window, cool vs hot plasma. | Shaped robust limits, one-sided blank tools, per-mode series. |
| C→J | Claude narrowed to one tool (EWMA and a forecast, since ±25 % makes Westgard nearly idle); Jamie: a quality manager's dream needs many tools, toggled and tuned, plus audit tools. | Became the profile and toolbox (D-110) and the part 2 audit list (D-109). |
| C→J | Claude added a channel model (isotopes, wavelengths, a reported-channel step), industry presets and mining tools; Jamie: "too deep in the weeds, keep it generic". | Kept: one optional `conditions` label (D-106). |
| F | `qtegra/1` drops the plasma mode; Jamie: "why should plasma mode be dropped?" | Claude hadn't proposed dropping it; it explained the existing loss and kept the mode (D-106). |
| J | A quality manager and quality users, not one quality role. | Two capabilities; managers decide, self-approval shown (D-109). |
| J | Not modelling Jamie's lab: the product must suit a mining lab as well as a semiconductor lab. | Series keys and limit sources are generic. |
| C✓ | Change control with propose/decide, backtest, limits set once from a baseline or certificate, SVG charts drawn on the server. | D-110. |
| C✓ | QC-only batches for the demo history, over an internal TR or history made up on the fly. | D-108; Claude found that slim-data refused a batch with no samples. |
| J | Where a sample should be against its turnaround: worth a view; parked. | D-111. |

## 2026-10-30: slim-quality review, then part 2's first slice

| Kind | What | Notes |
|---|---|---|
| J | Use the remaining budget well: Claude proposed finish → review → midnight fix → design turn; Jamie agreed. | |
| F | Module review: a stale profile approval undid a newer one; concurrent decisions were 500s; charts dated in UTC; bad series ids were 500s. | All fixed with guard tests (`REVIEW_2026-10-30.md`). |
| F | The prep demo and a prep test failed every night between midnight UTC and midnight in the lab. | Dates crossed midnight; fixed in the wiring PR. |
| C✓ | Part 2 starts with the vertical audit, then nonconformities; random pick or by number; no NC raised by a flag on its own; an impact assessment records decisions and never changes results. | D-112…D-114, "yes to all". |
| C→C | The app first trusted the impact labels and a non-manager's refused assessment read as a form error. | Labels now built by the app; permission asked before the form is read (`Nonconformity.allows`). |
| C→C | "Registered by an instrument file" was first shown as a failed audit check. | A legitimate path (D-24): a note, not a fault. |

## 2026-10-30: app review of the seams

| Kind | What | Notes |
|---|---|---|
| J | Review the boundaries between all modules for vulnerabilities and optimizations. | `REVIEW_2026-10-30b.md`. |
| F | Demo labs outlived deploys with old schemas: a visitor could claim a spare without the newest tables. | D-115: spares rebuilt, claimed labs upgraded at start. |
| F | The accounting CSV ran formulas typed on the request form (invoicing V5 doesn't guard what slim-prep does). | Guarded in the host; V5's own fix raised (R3). |
| F | slim-quality: backtest judged a cut history; one QC level split by how a file wrote it; no verify for its records; NC entries could race. | Fixed with guard tests (Q5–Q8). |
| F | The board read every batch whole, one by one. | One query (O1). |
| C→J? | Raised, not built: pin module versions (R1); a slim-data handoff API for slim-prep (R2); V5 fixing its CSV (R3); payments as append-only rows (R4). | For Jamie. |
| C✓ | Jamie: "1-4 please". R1–R4 built as recommended: D-116 (handoff API), D-117 (the host pins every module to a commit; libraries name each other unversioned), D-118 (V5's CSV safe at its source), D-119 (payments append-only rows; invoices append-only too). | Guards: `test_pins.py`, slim-prep's architecture test, V5's export test, `test_r4_…`. |

## 2026-10-31: slim-samples (roadmap phase 4, first half)

| Kind | What | Notes |
|---|---|---|
| J | "R1–R4 are merged." Parked, to log with this batch: **learning exercises (rebuild-it-small)** and **a quiz section that gauges where I'm up to.** | Parked, not scheduled. Not built; raise again when Jamie wants them. |
| C✓ | Phase 4 starts with `slim-samples`; the signatory step and report delivery (email, PDF) are the next batch. | Design turn first; Jamie: "we're good to start." |
| C→J | Claude's design: a "leaking" bottle blocks prep. Jamie: **leaking only warns on the report that the analysis may be compromised, for the analyses of that bottle** (sometimes several bottles for one sample: PFA, HDPE, glass). Lab-customisable; his default is a warning. | S-02, S-04. Claude's blocking default was wrong; "block" stays as a lab-set effect (e.g. unsafe to handle). Condition belongs to the bottle, so analyses are linked to bottles. |
| J | Retention: asked whether it was a "built-in timer" and a way to mark disposed or returned. | Claude: a due date, not a timer; a person records disposal or return (S-09). Jamie: "both sounds good." Claude added a hold flag for open complaints. |
| J | Storage: a **customizable, defined list** of locations. | S-03; so are kinds, conditions, disposal methods, retention. |
| J | "There should be a section in login to specify how many and what kind of bottle were received." | Check-in's bottles section (S-02, S-10); rows of kind, count, location, condition, analyses served. |
| C✓ | Not in this batch: the customer stating on the request form how many bottles they send. | Jamie: "not necessary." Parked (S-11). |
| F | The machine contract (spec §12) can't require bottles: the private slim-lims posts check-in without them. | S-10: the screen requires them; the API accepts them; `/samples/log/{tr}` logs them afterwards. |
| F | Claude couldn't create the new GitHub repo from the session; Jamie created `slim-samples-oss`. | Process note. |
| F | `test_q2_the_pages_show_the_labs_time` failed in Claude's sandbox. Cause found (Jamie asked): the fixture built the seed's `now` with `astimezone()` (the *machine's* zone), so the seeded run times moved with the machine; it passed in CI (UTC) and failed wherever the zone isn't UTC, including Jamie's own Chicago machine. | Fixed: `astimezone(UTC)`; passes in UTC, Chicago and Sydney. My earlier guess in this entry (the 1 Nov clock change) was wrong. |
| F | Review of slim-samples after merge (Jamie: "double check the module"): bottles logged *after* check-in took their retention date from the day they were logged, not the day the request was received. | Fixed; guard test `test_r3_…`. Also guarded: a refused bottle list undoes the check-in over the API (r1), a customer sees no bottle data (r2), a lab-typed condition label is escaped on the report (r4), a double-submitted log is a 409 not a 500. |
| F | Known and left: two people disposing of the same bottle at the same instant record two events; `cli verify` reports the second ("an event after it was finished"). | Detectable, not prevented; a unique "one final event" constraint is the fix if it ever matters. |

## 2026-11-02: signed reports, PDF, outbox (roadmap phase 4, second half)

| Kind | What | Notes |
|---|---|---|
| F | Jamie's opening message repeated the 2026-10-31 one (slim-samples next; park learning exercises and a quiz). Claude pointed out both were done and logged, then checked every branch when Jamie asked "didn't I already do that?" for the signatory and delivery work: nothing existed. | The parked items stay as logged on 2026-10-31. |
| C→J | Claude proposed "signatory ≠ releaser" as a lab setting. Jamie: why, if four-eyes; **shouldn't the signatory be the releaser?** | Claude withdrew it: four-eyes already stands at release (D-18, D-62); signing covers the whole report. D-122. |
| J | Asked how email delivery could be demoed: as what *would* be sent, sitting in an outbox? | Yes, as a delivery recorded and not transmitted, not "waiting to send". D-124. |
| J | Go-ahead to change slim-report-engine's template (the signature block), over a host-appended block. | slim-report-engine-oss #3. |
| C✓ | One act "Sign and issue"; the `signatory` capability; PDF made once at issue and kept by hash; the outbox as the only transport (SMTP parked). | D-122, D-123, D-124. |
| C→C | Caught while building: signing puts the day into the file, so the old "nothing has changed" check (same hash) would have let the same report be issued again the next day. | Compares the unsigned bodies; guard test `test_s3_…`. |
| C→C | The demo scenarios issue as Rob (reviewer), so Rob is a signatory in the demo too; otherwise the seeded reports would carry a signature by someone not allowed to sign. | `test_s7_…`. |

## 2026-11-02: slim-quality part 2, batch 5a (equipment, lots, PT, complaints)

| Kind | What | Notes |
|---|---|---|
| C✓ | Split phase 5's remaining ten items into three batches (5a records the lab keeps, 5b what the data shows, 5c the management system); 5a first. | Jamie: "5a as recommended". |
| C→C | The brief changed Claude's own discussion point: reading slim-prep's design (P-07, P-14) showed the registers were always meant to be slim-quality's, so lots come through a port instead of the host cross-checking typed ones. | D-127, P-28. |
| C✓ | Equipment by the names the modules already record; the effect per kind in the profile (change-controlled), out of service always blocks; a failed balance's impact through the prep batches that used it. | D-125, D-126. |
| C✓ | A PT sample is an ordinary TR; z-scores charted on fixed limits (0 ± 1) by the existing tools. | D-128, D-129. |
| C✓ | Complaints: the outcome refused to anyone with a hand in the request, composed by the host (§7.9.6). | D-130. |
| C→C | Caught while building: the profile form would have reset the PT z-score's fixed limits to a baseline on the next proposal; the form now keeps what it doesn't edit. Also `upgrade_lab` seeds the register in old labs, or their standards would be refused. | Guard tests in `tests/test_quality_records.py`. |

## 2026-11-04: slim-quality part 2, batch 5b (uncertainty, competence, trends)

| Kind | What | Notes |
|---|---|---|
| J | Asked for the big picture first: why should a lab care about each item? | Each answers an assessor's question from records SLIM already keeps; Claude ranked them competence, uncertainty, turnaround. |
| C✓ | Uncertainty the Nordtest way from check-standard recoveries plus CRM/PT bias; signed by a quality manager; not on the report (the ± stays ½Δ). | Jamie: "go, all three with your recommendations". D-131. |
| C✓ | An authorisation register in slim-quality; acts found after the fact, not blocked. | D-132. |
| C✓ | Turnaround against the app's own service date (Mon–Fri, no holidays, holds don't pause); customer trends with complaints. | D-133. |
| C→C | Caught while building: an authorisation recorded after the act it covers would have hidden the gap; it is a finding of its own ("backdated"). Days compare in the lab's zone, or an evening's grant reads as tomorrow. | Guard tests in slim-quality `tests/domain/test_phase5b.py`. |
| C→C | The demo plants a gap that teaches: Rob holds the app's signatory capability but nobody recorded his authorisation to sign, so his reports show up. | `test_the_demo_register_is_seeded_once_and_finds_robs_signatures`. |

## 2026-11-05: slim-quality part 2, batch 5c (risks, internal audits, readiness, management review, §7.11)

| Kind | What | Notes |
|---|---|---|
| J | "I don't understand what the first decision means or looks like." | Claude showed a demo risk register as a table (L × S, score, action, review date). Jamie: "Score's fine". D-134. |
| C✓ | Internal audits as a yearly programme with an independent auditor and a per-clause checklist; vertical audits count toward their clauses. | D-135. |
| C✓ | Readiness per clause (red / amber / green from profile rules); the management review pack frozen at signing, worked out again from the records, never from the form. | D-136. |
| C✓ | The §7.11 pack built by the host from what is installed; a signature covers exactly those versions and goes stale on a pin bump. | D-137. |
| C→C | Who is "involved" in a clause is split where the knowledge is: the host from the acts its modules recorded, slim-quality from who kept that clause's records in it (an NC keeper doesn't audit 7.10). | `test_whoever_kept_a_clauses_records_here_does_not_audit_it`. |
| C→C | The design said the pack shows "verify's last run", but no run is recorded anywhere: the pack runs every module's `verify` when shown (read-only). | `test_the_validation_pack_lists_the_installed_modules_and_is_signed_for_them`. |

## 2026-11-06: the day's board (what is due today, per section, on the lab's screens)

| Kind | What | Notes |
|---|---|---|
| J | A view apart from the board: what is due today and where it is, for a quick look or a screen left up in the lab; per section, set by each lab, so ICP-MS, IC and the wet lab each see only theirs. Open to other layouts than a kanban. | Unparks D-111's live view. |
| C✓ | Due per sample × analysis; late work stays until released; columns from the facts (prep book, run list, results); a card per request per section; analyses owned by one section each; a secret, revocable display link; a minute's reload; at-risk and forecasts left parked. | D-138. |
| C→J | Claude proposed screens that always hide customer names (visitors may see them). Jamie: also a named mode for when there are no visitors, or the screen is trivia, not useful. | Built as a lab-wide switch any staff member flips; never set means hidden. D-138. |
| C→C | Claude proposed a script polling every minute; a `meta refresh` does the same with no script at all (nothing for the CSP to allow, nothing to break on a TV browser). | `test_staff_see_the_day_with_names_and_set_up_sections_and_screens`. |

## 2026-11-06: quote screens (roadmap phase 6, first part)

| Kind | What | Notes |
|---|---|---|
| F | Claude read invoicing V5 before proposing: it already holds quotes (customers, lines, prices per processing time, bulk, dates), never edits them and ignores an expired one when pricing. So the batch is screens, requests and sending, not a quotes module. | D-139. |
| C✓ | Quotes stay in V5; Replace makes a new prefilled quote; no draft/accept states; customers ask and get an answer or a reason; expiring-soon list; a *pricing* capability; the PDF and the outbox as for reports. | D-139. |
| C✓ | Early withdrawal as a row added in V5, honoured by pricing. | Agreed, **not built**: attaching the invoicing repo was refused in the session. A replaced quote stays in force to its expiry; the page says so. |
| C→C | The request form's quote reference was to go on the TR; but the TR's JSON is a machine contract (spec §12). Kept beside it in `tr_quotes` instead. | `test_a_request_names_one_of_the_customers_quotes_in_force`. |

## 2026-11-09: quote withdrawal (D-139 #3, finished)

| Kind | What | Notes |
|---|---|---|
| J | Attached the invoicing repo so the agreed withdrawal could be built where pricing reads quotes. | D-140. |
| C✓ | A withdrawal row in V5 (never an edit), honoured by `valid_on`; Withdraw on the quote page; Replace withdraws the old quote from the new one's first day. | D-140; `test_a_withdrawn_quote_prices_nothing_from_its_day`, `test_replacing_withdraws_the_old_quote_from_the_new_ones_first_day`. |

## 2026-11-09: reference data upkeep (roadmap phase 6)

| Kind | What | Notes |
|---|---|---|
| F | Claude found slim-domain already writes customers and chemicals (validated, with initials), but not specs; so the batch needs no slim-domain change, and specs need one. | D-141. |
| C✓ | Writes only through slim-domain's services; customers, chemicals, the app's catalog; a *reference data* capability; a reason per change, kept; no deletes; specs parked with change control. | D-141. |
| F | Building it, Claude found the reference cache is one for the whole app: on the public demo a visitor's edit would not show, or would leak. Each lab got its own cache. Also: the catalog sync would have wiped edits made in the app; it keeps them now. | `test_each_visitors_lab_has_its_own_reference_cache`, `test_the_catalog_is_edited_with_a_reason_and_a_sync_keeps_it`. |

## 2026-11-09: accounts, password reset and MFA (roadmap phase 6, last part)

| Kind | What | Notes |
|---|---|---|
| C✓ | Reset links made by whoever manages users (shown once, in the outbox; no public form without a mail transport); change one's own password; TOTP with recovery codes; MFA required for supervisors and quality managers; a lost phone reset with a reason; the secret sealed; an append-only account log. | D-142. |
| F | Sessions are signed cookies, so "signed out everywhere" needed a per-user `auth_version` each session carries; and the MFA hold had to sit in `current_user`, since the board route takes an optional user. | `test_a_reset_link_sets_a_new_password_once_and_signs_the_user_out`, `test_a_supervisor_must_set_up_mfa_and_then_signs_in_with_a_code`. |

## 2026-11-09: spec limit changes

| Kind | What | Notes |
|---|---|---|
| J | Go-ahead to change slim-domain for spec limit writes. | D-143. |
| C→J | Claude proposed: reference-data holders propose, a quality manager approves. Jamie: quality staff and quality managers propose; a quality manager or a lab supervisor approves. | Four eyes kept: never the proposer. D-143. |
| C✓ | One limit set at a time; only approval writes, through slim-domain, if the set on file is still what was proposed; unreleased results re-evaluated; released ones listed when they'd now judge otherwise, one click to an NC. | D-143. |
| F | slim-data's "would read differently" ignores an INFO flag, so a pass that now fails wasn't listed; the host compares the reported value with the old and new limit itself. Also: spec limits are read per demo lab, as reference data is. | `test_a_change_is_proposed_approved_by_another_and_bears_on_the_results`. |
| F | slim-domain-oss is synced from a private repo: this change must be made there too. | Flagged to Jamie. |

## 2026-11-09: app review after slim-quality 5c

| Kind | What | Notes |
|---|---|---|
| J | "Do the app review." | `REVIEW_2026-11-09.md`. |
| F | A2: reset links readable by every staff member in the outbox (an account takeover); L2: a visitor's writes could fall back to the shared lab; S1: a spec approval half-done by one batch that couldn't be evaluated; A1: MFA a 500 after a key change; F1: quote prices silently dropped. All fixed, each with its guard. | `test_a2_…`, `test_l2_…`, `test_s1_…`, `test_a1_…`, `test_f1_…`. |
| F | For decision: R5 (the host reads V5's tables), R6 (slim-domain's private sync would drop `set_limits`), R7 (the spec write and its decision commit apart). | Raised with Jamie. |

## 2026-11-09: review findings R5 and R7

| Kind | What | Notes |
|---|---|---|
| J | "Do R5 and R7." | |
| C✓ | R5: V5's own `all_quotes` / `get_quote` instead of the host reading its tables; R7: a spec approval as one transaction where slim-domain shares the app's database, judged by the proposed limits before they're on file. | `test_r7_the_set_the_decision_and_the_new_versions_commit_together`. |

## 2026-11-12: QC trend warnings at review and release (slim-quality part 3)

| Kind | What | Notes |
|---|---|---|
| C✓ | A QC trends panel on the results batch page (each chart once, worst first) and a marker per row: slim-quality's flags on the run's instrument × analyte and its internal standard, 7 days before the run through it, the forecast included. Never a block; releasing over an ACTION flag needs a reason, kept host-side with a frozen copy of the flags (`qc_acknowledgements`), shown in the vertical audit. All reviewers see it. | D-144. |
| J | Before the walkthrough, review each department on its own (boundaries first, big picture then edge bugs), one fresh chat each, frugal with tokens. | `docs/REVIEW_PLAN_2026-11-12.md`. |
| F | An internal standard's flags bear on every analyte, so a per-analyte panel repeated them ten times; the panel groups by chart instead. | `test_an_action_flag_needs_a_reason_to_release_and_keeps_the_flags`. |

## 2026-11-12: department review, Lab

| Kind | What | Notes |
|---|---|---|
| J | "Review the Lab department per the plan." | `REVIEW_2026-11-12_lab.md`. Nothing fixed. |
| F | Bugs L-1…L-5: a clean rerun batch can't be released on screen (the release reads every batch's QC flags, the page its own); one cancelled or invoiced request freezes a shared prep batch's corrections; a blocking bottle stops the prep book but not an instrument file; a refused release loses what was typed. For decision L-6…L-12. | Raised with Jamie. |
| J | "Fix L-1 through L-4." | |
| C✓ | L-1: the release and the batch page ask one function, by the batch each result was made from; L-3: a file can't put a blocked sample on a batch; L-4: a refused review or release shows the form again as posted. Each with its guard test. | Host only. |
| C→J | L-2 built in the host (`prep.AlreadyHeld`: a correction's samples stay taken), not in slim-prep as the review proposed: no module change or pin bump, and a first send of a cancelled request's batch is still refused (P-23). | Say if it should move into slim-prep. |
| J | "Yes, move it into the module." (2026-11-13) | |
| C✓ | L-2 moved into slim-prep as P-29: a correction, a move to new rules and a correction's send check only samples new to the batch; the host's `AlreadyHeld` is gone and slim-prep is pinned at `6ee244c`. | slim-prep `test_p29_…`; host `test_l2_…` unchanged. |

## 2026-11-13: department review, Quality

| Kind | What | Notes |
|---|---|---|
| J | "Yes, open both PRs and merge on green." | slim-prep-oss #6 (P-29), slim-lims-oss #37 (Lab review and L-1…L-4): merged. |
| J | "Review the Quality department per the plan." | `REVIEW_2026-11-13_quality.md`. Nothing fixed. |
| F | Bugs Q-1…Q-3: a group's spec limits never judge a result (the host reads the exact customer, slim-domain falls back to the group); a spec proposer or approver can audit §7.8; an amended report's audit shows an earlier version's QC acknowledgement. For decision Q-4…Q-10. | Raised with Jamie. |
| J | "Fix Q-1 through Q-3." | |
| C✓ | Q-1: spec limits follow slim-domain's owner rule (a site's own, else its group's) in evaluation and in approval; Q-2: spec proposers and approvers are involved in §7.8; Q-3: a line's QC acknowledgements are its own version's release's. Each with its guard test. | Host only; Q-3 corrected an older test that relied on the bug. |

## 2026-11-13: department review, Admin

| Kind | What | Notes |
|---|---|---|
| J | "Review the Admin department per the plan." | `REVIEW_2026-11-13_admin.md`. Nothing fixed. |
| F | No confirmed bug; the plan's A1…A7 refuted. For decision A-1…A-5, first A-1: an invoice is priced on the day it is sent, so a request submitted under a quote that expires or is replaced before the report is complete is billed at the invoicing day's price, and nothing says so. | Raised with Jamie. |
| J | "Build A1 and 2." | |
| C✓ | A-1: an invoice is priced by the quotes in force the day the request was received; the invoice page says what priced each line and when the named quote priced none. A-2: an amended report to an invoiced request says the invoice is unchanged. | Host only; V5 unchanged. |

## 2026-11-13: department review, IT

| Kind | What | Notes |
|---|---|---|
| J | "Review the IT department per the plan." | `REVIEW_2026-11-13_it.md`. Nothing fixed. |
| F | Bug T-1: the login throttle's per-address limit takes the leftmost `X-Forwarded-For` entry (uvicorn with every proxy trusted), which the client writes: it can be dodged, or aimed at someone else's address. For decision T-2…T-6: an admin page, capability grants as events, a pin bump list, the demo lab's hour said up front, backups with the VPS. | Raised with Jamie. |
| J | "Fix T-1." | |
| C✓ | The login throttle takes the `X-Forwarded-For` entry the lab's own proxy appended (`TRUSTED_PROXY_HOPS`, 1 on Render), never one the client wrote. Guard runs behind uvicorn's proxy middleware as Render does. | Check the hop count on Render once. |

## 2026-11-13: cross-department pass

| Kind | What | Notes |
|---|---|---|
| J | "Do the cross-department pass per the plan." | `REVIEW_2026-11-13_cross.md`. Before Jamie's walkthrough, which the plan puts first. Nothing fixed. |
| F | Bugs X-1, X-2: a quote withdrawal may be dated in the past, and since A-1 that re-prices requests already received; a bottle can be disposed of while its request has an open complaint or a rerun is requested. For decision X-3 (a PT request is invoiced to the scheme), X-4. | Raised with Jamie. |
| J | "Fix x1 but for x2, what if someone accidentally disposes of a bottle? Fix x3-4, too." | X-2 held for Jamie's answer. |
| C✓ | X-1: no withdrawal or Replace reaches back before the lab's today. X-3: a PT request is closed as no charge (a fact, to invoiced; migration 0019), never invoiced. X-4: an open NC's "Hold their bottles". Each with its guard test. | Host only. |
| C→J | X-2: a disposal is final in slim-samples (S-05), so an accidental one can't be undone. Recommend X-2's refusal (for a bottle that is evidence) plus an "undo a disposal" event in slim-samples for a wrong click. | For Jamie. |

## Themes so far
- **Domain facts are Jamie's, and they overrule Claude's defaults:** DF < 1, MDL averaging, standards as
  recipes, spike placement. Claude's "cautious" engineering defaults were wrong more than once where
  bench practice knows better.
- **Customisability is Jamie's reflex:** whenever Claude proposes one rule, Jamie asks that the lab can set
  it (shelf life, expiry cap, check-standard judging, user admin).
- **Claude leads on structure and finds the faults:** layering, versioning, replay, boundaries' mechanics,
  bugs and clashes. Jamie mostly accepts these as proposed.
- **Jamie sets the frame:** what the thing is for (out of Excel; a portfolio piece reviewers click
  through), and who must never see what.

## Tally (2026-10-27/28)
J 14 · C✓ 5 · C→J 4 · J→C 1 (self-argued) · C→C 2 · F 3
