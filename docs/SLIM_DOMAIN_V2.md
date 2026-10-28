# SLIM domain v2 — Testing Request (TR) spec

Status: **draft, agreed in principle** (2026-10-08). Owner of the change: slim-lims.
Applies to: slim-lims now; slim-domain / slim-report-engine / slim-lims later.

## 1. Why this exists

slim-domain's `TRSubmission` / `TRSample` were shaped around the Excel Testing Request
form: cell positions, a file name as identity, sentinel dates, free-text payment cells.
slim-lims becomes the **primary** way submissions reach the lab; emailed `.xlsx`
forms stay supported for back-compatibility only.

This document defines the **v2** shape. The portal implements it now, with no runtime
dependency on slim-domain. Later, slim-domain adopts the same classes and tables, the
xlsx parsers are adapted to produce v2 objects, and the report engine consumes them.
Nothing in slim-domain, slim-report-engine or slim-lims changes as part of the portal work.

Design rule for the portal: everything under `slim_lims/domain/` imports only the
standard library and pydantic, so it can be lifted into slim-domain unchanged.

## 2. Ecosystem and data ownership

```
one Postgres
├── SLIM reference tables (owned by slim-domain; the portal only READS them)
│     customers, chemicals, elements, analyses
├── portal-owned tables that will move to slim-domain (same names, same columns)
│     tr_submissions, tr_samples, tr_sample_analyses, tr_sample_additional_elements,
│     tr_submission_emails, tr_status_events, tr_number_counter,
│     customer_profiles, analysis_catalog
└── portal-only tables (stay in the portal)
      users
```

- The portal **never writes** SLIM reference tables. Its Alembic migrations only manage
  the tables it owns.
- No foreign keys from portal tables into SLIM tables. IDs are validated in application
  code against the reference cache, so the two can sit in different databases if needed.
- Dev, test and the portfolio demo get the SLIM reference tables from the OSS repos:
  `scripts/seed_demo.py` runs `slim-report-engine-oss/demo/demo.py`'s own `seed()` (fictional
  data, slim-domain's own table definitions) — only when those tables are empty.

### Reference cache

Same strategy as slim-domain/slim-lims: in-memory dicts, keys lower-cased and trimmed,
built at startup.

- Refreshed every `REFERENCE_CACHE_TTL_SECONDS` (default 300) on next access, and on
  demand from the admin UI (`POST /admin/reference/refresh`).
- If a refresh fails, the last good cache stays in use and the failure is logged and
  shown on the admin dashboard.
- Holds: customers (`ID`, name, address fields, `parent_customer_id`, `is_group`),
  chemicals (`ID`, name), elements (`ID`, symbol, name), analyses (`ID`, name,
  description, `sort_order`) joined with `analysis_catalog` (§8).

## 3. Enums

All enums are `IntEnum` with stable integer values (stored in the database).

### RequestType — unchanged

| Value | Name |
|---|---|
| 1 | CHEMICAL |
| 2 | WATER |
| 3 | WAFER |

One request type per submission (as today).

### ProcessingTime — values, labels and working days unchanged

| Value | Name | Label (stamped on reports) | Working days | Chemical / Water | Wafer |
|---|---|---|---|---|---|
| 8 | FIVE_DAYS | Five Days | 5 | ✓ | ✓ |
| 7 | THREE_DAYS | Three Days | 3 | ✓ | ✓ |
| 6 | TWO_DAYS | Two Days | 2 | ✓ | ✓ |
| 2 | NEXT_DAY | Next Day | 1 | ✓ | — |
| 9 | NEXT_DAY_RUSH | Next Day RUSH | 1 | — | ✓ |
| 3 | TIME_LIMITED | Next Day Time Limited | 1 | ✓ | — |
| 4 | SAME_DAY_RUSH | Same Day RUSH | 0 | ✓ | ✓ |
| 5 | CALL_IN_RUSH | Call-in RUSH | 0 | ✓ | ✓ |
| 1 | EXTENDED_TIME | Extended Time | 3 | legacy (xlsx only) | legacy |

- `CALL_IN_RUSH` is work outside business hours. The portal shows it as
  "Call-in RUSH (outside business hours)"; the stored label is unchanged.
- `EXTENDED_TIME` overlaps `THREE_DAYS`. It is kept so old xlsx forms still parse, but
  is not offered in the portal.
- Allowed values per request type live in the domain (`ALLOWED_PROCESSING_TIMES`), not
  in parsers or the report engine. The table above is the portal default; to confirm
  with the lab.

### TRStatus — new

| Value | Name | Meaning |
|---|---|---|
| 1 | SUBMITTED | Customer has submitted; samples not yet at the lab |
| 2 | RECEIVED | Samples physically received; `receipt` recorded |
| 3 | IN_PROGRESS | Analysis under way |
| 4 | PARTIAL_REPORT | At least one partial report issued |
| 5 | COMPLETE_REPORT | Final report issued |
| 6 | INVOICED | Invoiced — terminal |
| 7 | CANCELLED | Cancelled — terminal |

Allowed transitions (`ALLOWED_TRANSITIONS`):

| From | To | Who |
|---|---|---|
| SUBMITTED | RECEIVED — **check-in** only: records the receipt and matches every chemical to SLIM in one step (`workflow.check_in`) | staff |
| SUBMITTED | CANCELLED | staff, or a user of the submitting customer |
| RECEIVED | IN_PROGRESS, CANCELLED | staff |
| IN_PROGRESS | PARTIAL_REPORT, COMPLETE_REPORT, CANCELLED | staff |
| PARTIAL_REPORT | PARTIAL_REPORT (another partial), COMPLETE_REPORT | staff |
| COMPLETE_REPORT | INVOICED | staff |

Every change appends a `TRStatusEvent`; the submission's `status` is the latest one.

### PaymentMethod — new

| Value | Name | Notes |
|---|---|---|
| 1 | PURCHASE_ORDER | `po_number` required (billing codes / cost centres go here too) |
| 2 | CREDIT_CARD | The lab calls the customer. **No card data is ever stored.** |

### WaferSize — new

| Value | Name | Label |
|---|---|---|
| 1 | MM_200 | 200 mm |
| 2 | MM_300 | 300 mm |

### ReportingUnit — new (Wafer only)

| Value | Name | Label |
|---|---|---|
| 1 | ATOMS_PER_CM2 | atoms/cm² |
| 2 | E10_ATOMS_PER_CM2 | 10¹⁰ atoms/cm² |

### WaterPackage — new, **not offered in the portal yet**

| Value | Name | Label |
|---|---|---|
| 1 | STANDARD | Standard package |
| 2 | PREMIUM | Premium package |

### SubmissionSource — new

| Value | Name |
|---|---|
| 1 | PORTAL |
| 2 | XLSX |

### ActorType — new

| Value | Name |
|---|---|
| 1 | STAFF |
| 2 | CUSTOMER_USER |
| 3 | SYSTEM |

## 4. Domain objects

Frozen pydantic models, tuples for collections, `datetime`s timezone-aware (UTC).

### Actor

Who did something. Replaces initials-only `created_by` for submissions.

| Field | Type | Notes |
|---|---|---|
| `type` | ActorType | |
| `id` | str | Portal user UUID; `""` for SYSTEM or xlsx |
| `display` | str | Staff initials, or the customer user's email |

### TRSample

| Field | Type | v1 field | Notes |
|---|---|---|---|
| `id` | int = 0 | `id` | DB id; 0 until stored |
| `position` | int | — | 1-based order within the submission |
| `sample_name` | str | `sample_name` | The customer's own sample ID, free text, max 100 |
| `chemical_id` | int \| None | `chemical_id` | SLIM chemical, once matched. Chemical: set automatically only on an exact (case-insensitive) name match, otherwise `None` until the lab matches it. Water: the "Water" chemical. Wafer: `None` |
| `chemical_name` | str | `form_chemical_name` | Chemical: the customer's free text (chemical / matrix), required. Water: "Water". Wafer: "" |
| `processing_time` | ProcessingTime | same | Must be allowed for the request type |
| `requested_time` | `time` \| None | `requested_time: str` | "Report by" time. Required iff `TIME_LIMITED`, otherwise `None` |
| `additional_notes` | str | same | Notes only — never scanned for analysis names |
| `analysis_ids` | tuple[int, ...] | same | At least one, or `additional_element_ids` non-empty. Each allowed for the request type (§8) |
| `additional_element_ids` | tuple[int, ...] | same | Element IDs, metals by another method |
| `wafer_size` | WaferSize \| None | (was in `form_chemical_name`) | Required iff Wafer |
| `reporting_unit` | ReportingUnit \| None | `reporting_units: str` | Required iff Wafer |
| `water_package` | WaterPackage \| None | `water_package: str` | Water only; not offered yet |

### TRReceipt (new)

Recorded once, on SUBMITTED → RECEIVED.

| Field | Type | v1 field |
|---|---|---|
| `date_received` | date | `TRSubmission.date_received` |
| `received_by` | str | `technician_initials` |
| `recorded_at` | datetime | (`download_date`, sort of) |
| `recorded_by` | Actor | — |

### CustomerSnapshot (new)

The customer's name and address **as they stood when submitted**. Kept as a record;
never used to identify the customer.

| Field | Type | v1 field |
|---|---|---|
| `name` | str | `form_customer_name` |
| `address_line_1` | str | `form_customer_address` |
| `address_line_2` | str | `form_customer_address_2` |

The portal fills it from the SLIM customer record. The xlsx parser fills it from C10–C12.

### TRStatusEvent (new)

| Field | Type |
|---|---|
| `from_status` | TRStatus \| None (None for the first event) |
| `to_status` | TRStatus |
| `at` | datetime |
| `actor` | Actor |
| `note` | str |

### TRSubmission

| Field | Type | v1 field | Notes |
|---|---|---|---|
| `id` | int = 0 | `id` | DB id; 0 until stored |
| `tr_number` | str | — (was the file name) | `TR00001`; assigned when stored (§6) |
| `source` | SubmissionSource | — | |
| `file_name` | str = "" | `file_name` | Original xlsx name; `""` for portal |
| `status` | TRStatus | — | Latest status event |
| `customer_id` | int | same | SLIM `customers.ID`; known at submit time on the portal |
| `location` | str = "" | (derived from file name) | e.g. `SX5N` / `SX5S`; must be one of the customer's locations if it has any |
| `request_type` | RequestType | same | |
| `submitted_at` | datetime | `date_submitted` (+ `download_date`) | |
| `submitted_by` | Actor | — | |
| `customer_snapshot` | CustomerSnapshot | `form_customer_*` | |
| `customer_contact` | str | same | |
| `customer_phone` | str | same | |
| `payment_method` | PaymentMethod | — | |
| `po_number` | str = "" | `po_information` | Required iff PURCHASE_ORDER |
| `expected_arrival_date` | date \| None | — | Optional customer estimate; today or later (lab date) at submission. Never copied into the receipt |
| `results_to` | tuple[EmailStr, ...] | `results_email_main` | At least one |
| `results_cc` | tuple[EmailStr, ...] | `results_email_cc` | |
| `invoice_to` | tuple[EmailStr, ...] | `invoice_email_main` | At least one |
| `invoice_cc` | tuple[EmailStr, ...] | `invoice_email_cc` | |
| `samples` | tuple[TRSample, ...] | same | At least one; positions 1..n |
| `receipt` | TRReceipt \| None | `date_received`, `technician_initials` | `None` until RECEIVED |
| `service_date_override` | date \| None | `service_date` | Replaces the sentinel and the `[YYYY-MM-DD]` file-name override |
| `status_history` | tuple[TRStatusEvent, ...] | — | Oldest first |

Removed from v1: `credit_card_information`, `download_date`, `SENTINEL_DATE`.

Computed:

- `effective_service_date -> date | None`: `None` until received; else the override if
  set; else `date_received` + the slowest sample's working days (weekends skipped),
  exactly as v1 computes it.
- `estimated_service_date -> date | None`: only until received — `expected_arrival_date` + the
  slowest sample's working days; `None` without an expected date. The UI labels it as an estimate.
- `is_rush -> bool`: any sample with 0 working days or `NEXT_DAY_RUSH`.

### Validation rules (invariants, enforced in the domain model)

1. Every sample's `processing_time` is in `ALLOWED_PROCESSING_TIMES[request_type]`.
2. `requested_time` is set iff `processing_time == TIME_LIMITED`.
3. Wafer samples: `wafer_size` and `reporting_unit` set, `chemical_id` is `None`,
   `water_package` is `None`. Non-wafer samples: `wafer_size` / `reporting_unit` are `None`.
4. Chemical and Water samples: `chemical_name` non-blank (`chemical_id` may be `None` until matched).
   Only Water samples may have a `water_package`.
5. `po_number` non-blank iff `payment_method == PURCHASE_ORDER`.
6. A Chemical request that is RECEIVED or later has every sample's `chemical_id` set (checked in).
7. `receipt` is `None` while SUBMITTED and set for RECEIVED through INVOICED. A CANCELLED
   submission has a receipt only if it was cancelled after being received.
8. Sample positions are 1..n with no gaps; sample names are unique within a submission
   (case-insensitive).

Rules that need a clock (checked in the workflow/service with the lab's local date, `LAB_TIMEZONE`):
`date_received` may not be after today; `expected_arrival_date` may not be before today at
submission. A receipt dated before the submission is allowed (samples can arrive first).

Rules that need reference data (analysis allowed for request type, IDs exist, customer
location valid) are checked in the portal's submission service, not in the frozen model.

## 5. Customers, users and actors

- A **customer** is a SLIM `customers` row. The lab creates customers in SLIM first; the
  portal can only pick existing ones.
- Many portal **users** belong to one customer (`users.customer_id`, required for
  customer users). Staff users have no customer and must have `initials`.
- **`customer_profiles`** (portal-owned now, moves to SLIM later): per-customer defaults
  that used to live on the portal `User` row — contact, phone, the four email lists,
  default payment method and PO number, and the customer's `locations` (e.g.
  `["SX5N", "SX5S"]`). Editable by staff and by that customer's users.

## 6. TR numbers

- Format `TR` + at least five digits: `TR00001` … `TR99999`, then `TR100000`.
- Assigned from a single-row counter table with one atomic statement in the **same
  transaction** that inserts the submission:
  `UPDATE tr_number_counter SET last_value = last_value + 1 WHERE id = 1 RETURNING last_value`.
  The row stays locked until commit, and a failed submission rolls the increment back, so
  numbers have **no gaps** and never collide (tested with 40 parallel submits on Postgres).
  Works on Postgres and SQLite ≥ 3.35.
- When SLIM starts storing submissions (including parsed xlsx ones), it takes over the
  same counter table, so both sources share one series.

## 7. Tables

Portable SQL (runs on Postgres and SQLite, as slim-domain does): no arrays, integer
identity keys, enums stored as their integer values.

```
tr_number_counter          id (=1) PK, last_value int
tr_submissions             id PK, tr_number unique, source, file_name, status,
                           customer_id, location, request_type,
                           submitted_at, submitted_by_type, submitted_by_id, submitted_by_display,
                           snapshot_name, snapshot_address_1, snapshot_address_2,
                           customer_contact, customer_phone, payment_method, po_number,
                           date_received, received_by, receipt_recorded_at,
                           receipt_recorded_by_type, receipt_recorded_by_id, receipt_recorded_by_display,
                           service_date_override
tr_samples                 id PK, submission_id FK, position, sample_name, chemical_id,
                           chemical_name, processing_time, requested_time, additional_notes,
                           wafer_size, reporting_unit, water_package
                           unique (submission_id, position)
tr_sample_analyses         sample_id FK, analysis_id, position  PK (sample_id, analysis_id)
tr_sample_additional_elements  sample_id FK, element_id, position  PK (sample_id, element_id)
tr_submission_emails       submission_id FK, kind (results_to|results_cc|invoice_to|invoice_cc),
                           position, email                      PK (submission_id, kind, position)
tr_status_events           id PK, submission_id FK, from_status, to_status, at,
                           actor_type, actor_id, actor_display, note
customer_profiles          customer_id PK, contact, phone, default_payment_method,
                           default_po_number, results_to, results_cc, invoice_to,
                           invoice_cc, locations   (lists stored as newline-separated text)
                           -- position columns keep the customer's order of analyses/elements
analysis_catalog           analysis_id PK, code unique, group_name, request_types
                           (comma-separated ints), portal_selectable bool, sort_order
users (portal-only)        id UUID PK, email unique, password_hash, role (customer|staff),
                           customer_id nullable, initials, is_active, created_at
```

Audit: in production, slim-domain's hash-chained `audit_log` triggers (`infrastructure/audit.py`)
are installed over these tables too; the portal's `tr_status_events` is the business history,
the audit log is the tamper-evident one. The old portal `audit_log` table is dropped — its
name collided with SLIM's.

## 8. Analysis catalog and codes

SLIM's `analyses` table has no stable code, and which analyses suit which request type
lives in the report engine (`wafer_submission_builder._RECOGNIZED_SELECTIONS`). v2 adds
both to the domain as `analyses.code` and an `analysis_request_types` table. Until
slim-domain has them, the portal keeps them in its own `analysis_catalog` table, keyed by
SLIM `analyses.ID`. The table below lives in `slim_lims/data/analysis_catalog.csv` and is
loaded with `python -m slim_lims.cli sync-analysis-catalog`, matching rows to SLIM by
`analysis_name` (case-insensitive). Catalog rows SLIM doesn't have are skipped; SLIM analyses with
no catalog row are never offered.

Code mapping (portal code ← SLIM `analysis_name`). C = Chemical, W = Water, F = Wafer.

| Code | SLIM name | Group | Types | Portal |
|---|---|---|---|---|
| `10_elements_icpms` | 10 Elements | Metals | C W F | ✓ |
| `10_elements_icpoes` | 10 Elements (ICP-OES) | Metals | C W | ✓ |
| `26_elements_icpms` | 26 Elements | Metals | C W F | ✓ |
| `26_elements_icpoes` | 26 Elements (ICP-OES) | Metals | C W | ✓ |
| `36_elements_icpms` | 36 Elements | Metals | C W F | ✓ |
| `36_elements_icpoes` | 36 Elements (ICP-OES) | Metals | C W | ✓ |
| `36_elements_microwave` | 36 Elements (Microwave Digestion) | Metals | C | ✓ |
| `36_elements_organic_matrix` | 36 Elements (Organic Matrix) | Metals | C | ✓ |
| `36_elements_replicates` | 36 Elements (with reported replicates) | Metals | C W | ✓ |
| `67_elements_icpms` | 67 Elements | Metals | C W F | ✓ |
| `usp_elements_icpms` | USP Elements | Metals | C W | ✓ |
| `list2_36_elements` | List #2 36 Elements | Metals | F | ✓ |
| `hardness` | Hardness | Physical | C W | ✓ |
| `additional_element_icpms` | Additional Element | Metals | C W F | — (use the element picker) |
| `additional_element_icpoes` | Additional Element (ICP-OES) | Metals | C W | — |
| `additional_element_microwave` | Additional Element (Microwave Digestion) | Metals | C | — |
| `additional_element_organic_matrix` | Additional Element (Organic Matrix) | Metals | C | — |
| `4_anions` | 4 Anions | Anions | C W F | ✓ |
| `5_anions` | 5 Anions | Anions | C W F | ✓ |
| `7_anions` | 7 Anions | Anions | C W F | ✓ |
| `anions` | Anions | Anions | C W | ✓ |
| `4_anions_ms` | 4 Anions + MS Confirmation | Anions | C W | ✓ |
| `5_anions_ms` | 5 Anions + MS Confirmation | Anions | C W | ✓ |
| `7_anions_ms` | 7 Anions + MS Confirmation | Anions | C W | ✓ |
| `anions_ms` | Anions + MS Confirmation | Anions | C W | ✓ |
| `anions_organic` | Anions + Organic Anions | Anions | C W | ✓ |
| `anions_organic_ms` | Anions + Organic Anions + MS Confirmation | Anions | C W | ✓ |
| `cl` | Cl | Anions | C W | ✓ |
| `organic_anions` | Organic Anions | Anions | C W | ✓ |
| `acetate_formate` | Acetate and Formate | Anions | C W | ✓ |
| `6_cations` | 6 Cations | Cations | C W | ✓ |
| `cations` | Cations | Cations | C W | ✓ |
| `nh4` | NH4 | Cations | C W | ✓ |
| `methylamines` | Methylamines | Cations | C W | ✓ |
| `gbp` | GBP | Cations | C W | ✓ |
| `gbp_ms` | GBP + MS Confirmation | Cations | C W | ✓ |
| `toc` | TOC | Organics | W | ✓ |
| `tic` | TIC | Organics | W | ✓ |
| `gc_fid` | GC-FID | Organics | C W | ✓ |
| `gc_ms` | GC-MS | Organics | C W | ✓ |
| `conductivity` | Conductivity | Physical | C W | ✓ |
| `density` | Density | Physical | C W | ✓ |
| `liquid_particle_count` | Liquid Particle Count | Physical | C W | ✓ |
| `ph` | pH | Physical | C W | ✓ |
| `alkalinity` | Alkalinity | Physical | C W | ✓ |
| `apha_color` | APHA Color | Physical | C W | ✓ |
| `uv_vis` | UV-Vis | Physical | C W | ✓ |
| `moisture` | Moisture | Physical | C W | ✓ |
| `moisture_kf` | Moisture (Karl Fischer) | Physical | C W | ✓ |
| `total_silicon` | Total Silicon | Silicon | C W | ✓ |
| `dissolved_silicon` | Dissolved Silicon | Silicon | C W | ✓ |
| `dissolved_total_silicon` | Dissolved and Total Si | Silicon | C W | ✓ |
| `bacteria_count` | Bacteria Count | Microbiology | W | ✓ |
| `assay` | Assay | Assay & titration | C W | ✓ |
| `mixed_acid_assay` | Mixed Acid Assay | Assay & titration | C | ✓ |
| `sn2_iodine_titration` | Sn+2 Iodine Titration | Assay & titration | C | ✓ |
| `free_acid` | Determination of Free Acid | Assay & titration | C | ✓ |
| `ts_slg` | Analysis of TS-SLG | Assay & titration | C | ✓ |
| `h2o2_testing` | H2O2 Testing | Assay & titration | C | ✓ |

Notes:

- Wafer anions: the old portal catalog excluded Wafer from 4/5/7 Anions, but the report
  engine accepts them for Wafer. This table follows the report engine.
- "Additional Element" rows are not offered as checkboxes; the portal uses an element
  picker that fills `additional_element_ids`, which is what the report engine reads.
- Rows marked for types/groups not previously in the portal catalog (Microwave, Cations,
  GBP, titrations, H2O2) are best guesses — **to confirm with the lab**.
- The report engine still dispatches by name. Switching it to `code` is a later
  slim-report-engine change.

## 9. How slim-domain adopts this later (not part of the portal work)

1. Move `slim_lims/domain/*` into `slim_domain/domain/tr/` (v2), alongside v1 until
   the report engine is switched. slim-domain then needs `pydantic[email]` (for `EmailStr`).
2. Move the tables in §7 (except `users`) under slim-domain's repositories; the portal
   switches to slim-domain's repository classes.
3. Adapt the xlsx parsers to produce v2 objects: `source=XLSX`, `file_name` kept,
   `location` from the file name (SX5 only), `customer_snapshot` from C10–C12, the form
   text resolved to IDs as today, PO / card cells → `payment_method` + `po_number`
   (card numbers dropped), `date_received` / initials → `receipt`, `[YYYY-MM-DD]` →
   `service_date_override`, wafer size → `wafer_size`.
4. Add `analyses.code` and `analysis_request_types`; move `analysis_catalog` data there.
5. Report engine: read `wafer_size`, `location`, `tr_number` instead of deriving them from
   `form_chemical_name` and the file name; dispatch analyses by code.

## 10. Open items

- Confirm the processing-time lists per request type (§3).
- Confirm types/groups of analyses new to the portal catalog (§8).
- Water packages: contents and whether customers may pick them.
- Matching free-text chemicals: staff match `chemical_name` to a SLIM chemical on the request page
  (`workflow.match_chemical`; the customer's text is kept). New chemicals are created in SLIM, then
  matched after a reference reload. The bridge refuses a TR until every Chemical sample is matched.

## 11. Parked ideas (raise with the user; don't build without a go-ahead)

The portal is a portfolio demo: modular but narrow. These came up and are deliberately not built:

- Customer/staff can update `expected_arrival_date` while SUBMITTED (with history).
- Expected arrival *time* for Same Day / Call-in RUSH; making the date required for rush samples.
- Staff "incoming" (expected today/this week) and "overdue" (expected passed, not received) views.
- ~~Hash-chained audit over the TR tables~~: built (slim-data D-77, `audit.py`, every table on Postgres).
- **Anchor the audit chain.** Its hashes use no secret, so a table owner could recompute every hash after
  an edit and the chain would still check out. Copy the latest hash somewhere the owner can't rewrite
  (e.g. printed on each report issue, or stored outside the database); `verify` compares against it.
- **Database role split.** Migrations run as the table owner; the app connects as a login with only
  INSERT / SELECT, so it can't disable a trigger or edit the log. Deployment work: belongs with (C).
- **Repeat a previous request**: copy TR00042 into a new form and change only the sample IDs — the
  improvement over Excel for customers who send the same panel regularly.
- Upload sample IDs from a CSV/XLSX column (beyond pasting).
- slim-domain's audit log lists `current_schema()`'s tables instead of public's (the host works around it for
  demo labs, slim-data D-102).
- SRI hashes / self-hosted CDN assets; file attachments; email notifications.
- Quality layer: pooled precision per element and matrix (steadier than one sample's ½Δ), control charts
  and trends across batches (slim-data D-81 leaves these out).

## 12. Integration contract: portal ↔ slim-lims

What slim-lims may rely on when it is connected. `tests/test_lims_contract.py` fails if the
portal breaks any of it; change this section, the test and slim-lims together.

**Setup.** One Postgres holds SLIM's tables and the portal's (`docker-compose.yml`). slim-lims
reads TRs through the portal's API, not the TR tables, until slim-domain owns them (§9).

**Auth.** `Authorization: Bearer <LIMS_API_TOKEN>` (portal env var, ≥ 32 chars; unset = no
machine access). No session, no CSRF token. A token request never falls back to a session
cookie; a wrong token is 401. The token acts as `Actor(type=SYSTEM, id="slim-lims")` and sees
every customer's TRs.

**Endpoints** (anything else → 403):

| Call | Use |
|---|---|
| `GET /api/submissions?status=2[&request_type=&page=&per_page=≤100]` | TRs checked in and ready for work |
| `GET /api/submissions/{tr_number}` | one TR |
| `POST /api/submissions/{tr_number}/status` `{"to_status": n, "note": "..."}` | report progress |

**Payload.** `TRSubmission.model_dump(mode="json")` (§4): enums as ints, dates ISO,
`status_history` oldest first. Every sample of a RECEIVED (or later) Chemical TR has a
`chemical_id` (§4 rule 6). List responses are `{"items", "total", "page", "per_page"}`.
Keys may be added; none are removed or renamed without changing this section.

**Statuses slim-lims may set:** IN_PROGRESS (3), PARTIAL_REPORT (4, repeatable), COMPLETE_REPORT (5),
following §3's transitions. Receiving (check-in), cancelling and invoicing stay with lab staff.
A disallowed or out-of-order change is 409 — re-read the TR and carry on. Put the report
reference in `note`.

**Converting to slim-domain v1** (until §9): `slim_lims/bridge.py` `to_slim_v1` shows the
mapping; it moves to slim-domain or slim-lims when they are connected.

## 13. Lab board (Jamie, 2026-10-23)

The home page for everyone: each live TR is a card in the column of where it has got to, so the lab
sees at a glance what is expected, what is in but not analysed, what is being decided, what can be
reported and what is waiting to be invoiced. `slim_lims/board.py`, `templates/board.html`.

- **Columns follow what happened** (cards don't drag): Expected (submitted) · Received (checked in,
  no readings) · In analysis · Ready to report (released results not on an issued report) · Partial
  report · To invoice (complete report) · Invoice sent · Paid (the latest 10). Cancelled TRs are left off.
- **One card per TR**, with one chip per sample × analysis saying what its results need: rerun or
  exception, rerun requested, needs supervisor, being corrected, to review, to release, awaiting
  results, released, reported. A chip links to its prep batch.
- **Supervisor first; roles take away.** The supervisor's board has every action (check in, upload
  results, open the batch, issue or open the report, mark invoiced); the backend's rules decide what a
  click may do. "Needs me" narrows to the cards a lab role acts on (a supervisor: all of them).
  Customers see only their own TRs, read-only, in plain columns (Not arrived yet · At the lab ·
  Results published · Invoice sent · Paid) and open **every report issued** for them
  (`/requests/{tr}/reports`): each rebuilt as of its issue and checked against the hash recorded then.
- **Invoicing is part of the system**, not an add-on (Jamie, option A): **payment is invoice data,
  never a TR status.** `tr_invoices` (portal-owned, `invoices.py`) holds each invoice sent for a TR
  and when it was paid; "Invoice sent" also moves the TR to invoiced (the status slim-lims knows).
  **Invoice** on a To invoice card calls invoicing V5 (`slim_invoicing`, SLIM-Invoicing-Program
  `V5-LIMS-Module`; slim-data D-83, D-84): `invoices.billable` hands it the TR and every (sample,
  analysis) a report issue showed; V5 prices that from the quotes into one sales order (bulk on the
  merged line, the cheaper of bundle or parts), and the TR moves to invoiced, in one transaction.
  Refused (flashed) when V5 refuses: already invoiced, nothing reported, an analysis no quote prices.
  `/requests/{tr}/invoice` shows the sales order (customers see their own), `/invoices` lists them
  (staff) with the accounting CSV (`/invoices.csv`, V4's columns). "Mark paid" stays a portal row.
  The demo seed publishes one default price list for every catalog analysis and customer A's own quote
  (`demo_prices.py`, invented); `/invoices` shows every price list.
  Rejected: a PAID status (puts accounting in the TR lifecycle; breaks when one invoice covers several
  TRs or a TR is billed twice).
- The old request list stays (`/admin` for staff, `/requests` for customers).
- **The report itself** (D, first part; slim-data D-79): "Issue report" renders an HTML document
  with slim-report-engine's own template (lab header, a sheet per sample, a section per analysis, a
  row per analyte) filled with the released results under Results / Recovery / MDL (QL on ion
  panels): the MDL, not ±. It is stored (`report_files`) and the issue records its sha256, so the
  Reports page serves each issue byte for byte and re-checks the hash. Staff see "What the customer
  will get" before issuing. HTML now; PDF later. `reports.py`: `engine_renderer` (the app),
  `plain_renderer` (tests without SLIM's report tables).
  Half delta (½Δ, half the difference between the two cups; slim-data D-80) is printed only beside a
  spec limit it bears on: the result is outside it, or within ½Δ of it ("above the customer's
  specification; half delta 0.100"). The report ends with a key explaining MDL, QL and half delta,
  whichever it prints, and the decision rule (simple acceptance; ½Δ is for information, ISO 17025
  §7.1.3) wherever it compares a result with a spec. Staff screens label it ½Δ and always show it.
- **The cups** (slim-data D-81): the batch page shows, per result, the dilution-corrected cups (red
  when they disagreed), the cup believed and why, recovery and its basis, blank, MDL / QL and ½Δ,
  beside the actions that override it. Each result links to its trace page
  (`/results/batches/{code}/trace`): versions, flags, every engine step, and who did what.
