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
| F | Claude's review listed the Qtegra DOCTYPE as open; it was already refused (a byte check Claude wrote, which a UTF-16 file could pass). | The review was wrong; the fix still mattered. |

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
J 13 · C✓ 3 · C→J 4 · J→C 1 (self-argued) · C→C 1 · F 3
