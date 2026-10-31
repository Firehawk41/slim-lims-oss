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
| F | `tests/test_quality.py::test_q2_the_pages_show_the_labs_time` failed in Claude's sandbox (also on untouched code) but passes in CI. | Likely the sandbox's own timezone (the test builds the seed's `now` with `astimezone()`); not investigated. A test that depends on the machine's zone is worth fixing. |

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
