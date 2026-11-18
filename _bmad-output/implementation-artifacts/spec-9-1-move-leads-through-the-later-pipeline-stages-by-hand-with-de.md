---
title: 'Story 9.1: Move Leads through the later pipeline stages by hand, with deal value and notes'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '0739418e519de72040d4277885df9016a491772c'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-9-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Mac can see a Lead's Pipeline Stage but cannot move it through the Operator stages (book a call, audit, build, won, close, reopen) or record a deal value and notes, so the pipeline cannot show where engaged Leads really stand (FR-45, FR-37, FR-63).

**Approach:** Add a "Pipeline" panel to the Lead page with the legal Operator moves only, a book/rebook call form, and a deal value + notes form. Every write goes through the `Pipeline` service and the AD-4 stage machine in one unit of work with an `events` row (actor `operator`). Migration `0037_9_1_manual_stages` adds `leads.deal_value`/`leads.notes` (pipeline group) and the `call_outcomes` table. The 9.7 board shows each group's deal-value total.

### Decisions made overnight (pending user review)
1. **No Enrollments or Drafts here.** `enrollments` (Story 4.8, on `main`) and `drafts` aren't in this tree. `close` keeps main's shape for the merge: `close(lead_id, stage, cause_ref, *, cause=None, actor=None)` exactly as Story 2.15 defines it; Story 4.8's Enrollment stop joins at merge. Cancelling pending Drafts is left to the story that adds `drafts` (deferred-work entry). AD-16 already blocks a closed Lead (`eligibility` reason `lead_closed`).
2. **Lead notes are personal data (`redact`).** Free text may name a person. `pipeline` gains `ERASURE` and `erase_pii(ContactErased)`, which sets the erased Contact's Lead's `notes` to `"[erased]"`. The erasure contract's `owner_of` becomes column-group aware (the single owner of a table's personal-data columns) so the shared `leads` table can be declared; single-owner tables are checked exactly as before.
3. **Deal value input:** whole US dollars, digits only (surrounding spaces allowed), 0 to 1,000,000,000; blank clears it (NULL). Stored ×1,000,000. Notes: CRLF normalized to LF, at most 5,000 characters, blank clears. One form saves both and appends one `events` row (entity `leads`, from = to = current stage, cause `deal_details_saved`).
4. **Stage-entry time ignores same-stage rows.** The board's "In stage since" and date filters use only rows where `from_status` is NULL or differs from `to_status`, so a deal save never resets it.
5. **Booking rows' events:** entity `call_outcomes`, `None → booked` (cause `operator_move`) and `booked → superseded` (cause `rebooked`), actor `operator`. A Contact must belong to the Lead and not be erased. Blank timezone uses the Contact's `tz`; neither present refuses. An invalid IANA zone refuses "Enter a valid timezone, such as Europe/London". A local time that doesn't exist (DST gap) refuses with the date/time message.
6. **"Opted out: no outreach on any channel"** shows on the panel when the Lead's latest close `events` row has a `cause_ref` naming a suppression entry whose source is in `OPT_OUT_SOURCES` (read via `queries.compliance.entries`), whether the Lead is still closed or reopened.
7. **Reopen** targets the `from_status` of the Lead's latest `events` row whose `to_status` is `lost`/`disqualified`; when that is missing or not open/parked, reopen is refused ("This Lead can't be reopened") and nothing changes.

## Boundaries & Constraints

**Always:** Every POST has CSRF (`method="post"`, `hx-post`, `{{ csrf_field() }}`), `@personal_data(True)`, and returns the panel (200 for refusals, so HTMX swaps it in). Stage writes only through `nextron.app.pipeline.api.Pipeline` using `domain.pipeline_stage.transition`/`reopen`. Events never hold amounts, note text, dates or timezones. Notes render through `|plain` inside `hx-disable`. Money is integer micro-dollars. Reopening never touches `suppression_entries`.

**Never:** Offer or accept a POST to `new`, `queued`, `contacted`, `replied`, `interested`, `not_now` or `call_held`. Add `recontact_on` (Story 8.8). Send any email or alert. Weaken any contract.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Moves offered | stage `call_held` | buttons: Audit proposed, Audit won, Build proposed, Won, Close lost, Close disqualified | N/A |
| Forged automatic | POST `move=interested` (or any automatic stage) | "This stage is set automatically"; nothing written | N/A |
| Illegal move | POST `move=won` at `replied` | "That move isn't available at this stage"; nothing written | N/A |
| Book call | at `replied`, Contact + date + time (+tz) | `call_outcomes` row (UTC `scheduled_at`, `prospect_tz`, `booked_at`, `booked_by=operator`); stage `call_booked`; 2 events rows | missing field: "Enter the call's date, time and Contact" |
| Rebook | at `call_booked` with pending row | old row `superseded_at` set; new row; stage stays | N/A |
| Close | any open/parked stage, `move=lost` | stage `lost`, events row actor `operator` | already closed: notice, no write |
| Reopen | `lost` after `interested` | stage `interested`, events cause `reopened`; suppression entries unchanged | refused per decision 7 |
| Deal save | `12000`, notes | `deal_value=12000000000`, notes stored, one events row | `-5`/`abc`/`1.5`: "Enter the deal value in whole US dollars"; notes > 5,000: "Notes can be at most 5,000 characters" |
| Malformed | unknown/repeated field, unknown `move`, bad Lead id | 400 "Request refused: malformed form" / 404 for an unknown Lead | `MalformedForm` |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/domain/pipeline_stage.py` -- add `AUTOMATIC_STAGES`, `OPERATOR_EVENTS` (target stage → `StageEvent`), `operator_moves(current)` (legal targets from `TRANSITIONS`, plus reopen when closed), and causes `deal_details_saved`, `rebooked` (event cause codes). Keep docstring table in sync.
- `nextron-engine/nextron/app/pipeline/api.py` -- `Pipeline`: `close` gets Story 2.15's exact keyword-only `cause`/`actor` (copy from commit `d128e7c`); add `move(lead_id, target, *, actor)`, `book_call(lead_id, contact_id, scheduled_at, prospect_tz, *, actor)` (books or rebooks), `reopen(lead_id, *, actor)`, `save_deal(lead_id, deal_value, notes, *, actor)`; `ERASURE` + `erase_pii`. Typed refusals (e.g. `StageRefused` with a reason enum) for the web layer.
- `nextron-engine/nextron/db/repos/pipeline/` -- `stages.py` (deal/notes read+write, latest close row), new `bookings.py` (`call_outcomes` insert/supersede/pending). `board.py`: `_entered_at` filters same-stage rows (decision 4); `stage_counts` also sums `deal_value`.
- `nextron-engine/nextron/db/models/leads.py` -- `deal_value` (BigInteger, nullable), `notes` (Text, nullable), no DB CHECK (added in place like `0036`; the code enforces the limits); new `CallOutcomeRecord` (likely in a new `models/pipeline.py`; export via `models/__init__`).
- `nextron-engine/nextron/db/alembic/versions/0037_9_1_manual_stages.py` -- add_column ×2 via `batch_alter_table`, as `0036` does. Create `call_outcomes` (id ULID pk, lead_id FK, contact_id FK, scheduled_at, prospect_tz, booked_at, booked_by, superseded_at nullable; index on lead_id). Downgrade raises.
- `nextron-engine/nextron/db/owners.py` / `pii_registry.py` -- `leads.pipeline` group adds `deal_value` (none), `notes` (redact); `call_outcomes` owner `pipeline`, all `none`.
- `nextron-engine/nextron/app/queries/pipeline.py` -- `lead_pipeline(lead_id)` → stage, deal_value, notes, pending booking, offered moves, latest close `cause_ref`; `StageGroup.deal_value` filled.
- `nextron-engine/nextron/main.py:1806` -- register `Pipeline(chosen).erase_pii` for `ContactErased` after research's.
- `nextron-engine/nextron/web/app.py:1608` -- `lead_page` adds the panel context; new `POST /leads/{lead_id}/stage`, `/leads/{lead_id}/call`, `/leads/{lead_id}/deal` (register near `lead_make_primary`).
- `nextron-engine/nextron/web/` -- new `lead_pipeline.py` (form parsing, `MalformedForm`, messages, view) + `templates/lead_pipeline.html` included by `lead.html`; `pipeline_page.py` renders "Deal value: $12,000" (none → "—").
- `tests/contracts/test_erasure_contracts.py` -- `owner_of` column-group aware (decision 2).
- Contracts to bump: `HEAD` in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`; chain probes; `test_source_tree` `FILES`; `test_web_contracts` routes/templates.
- Reuse: `tests/app/test_leads.py` `queue_on(clock)`; `nextron/web/leads_page.py` `MalformedForm`; `compliance_queries.entries`, `OPT_OUT_SOURCES`.

## Tasks & Acceptance

**Execution:**
- [x] domain -- stage helpers + causes; unit tests for `operator_moves` across all 14 stages.
- [x] migration + models + owners + PII -- `0037_9_1_manual_stages`; migration and persistence tests.
- [x] repos + `Pipeline` commands + `erase_pii` + erasure contract -- app tests for every matrix row at the service level, events contents (no amounts/text), suppression untouched by reopen, `assert_contactable` still `Blocked` after reopening an opt-out Lead, erasure redacts notes.
- [x] queries + web panel + routes + `lead.html` -- web tests for each matrix row, CSRF, no-store, autoescaped notes in `hx-disable`.
- [x] board deal totals + same-stage filter -- update `test_pipeline_board.py`.
- [x] `nextron-engine/README.md` -- document; `deferred-work.md` -- trim the 9.7 entry's deal-value part (keep `recontact_on`/`lead_score`), add the Drafts-cancel entry.

**Acceptance Criteria:**
- Given any Lead, when the panel renders, then it offers exactly `operator_moves(stage)` and never an automatic stage.
- Given a book, move, close, reopen or deal save, then exactly the stated `events` rows are appended in the same transaction, with actor `operator`, and a failure leaves no row.
- Given a closed opt-out Lead reopened, then its suppression entries are unchanged, the panel shows "Opted out: no outreach on any channel", and `assert_contactable` returns `Blocked`.
- Given Leads with deal values, when the board renders, then each group shows its total in whole dollars.

## Implementation Notes

- **Domain.** `pipeline_stage` gains `AUTOMATIC_STAGES` (new, queued, contacted, replied, interested, not_now, call_held), `OPERATOR_EVENTS` (call_booked, audit_proposed, audit_won, build_proposed, won, lost, disqualified → event), `REOPEN = "reopen"`, `operator_moves(current)` and the causes `deal_details_saved`, `rebooked`. New `nextron/domain/manual_stages.py` holds `StageRefusal`/`StageRefused` (the typed refusals), `parse_deal_value`, `clean_notes`, `check_deal`, `check_timezone` and `call_time` (a DST-gap time is `bad_date_time`, an ambiguous one its first occurrence). Messages live in `nextron/web/lead_pipeline.py` (`REFUSALS`); `bad_date_time` shares "Enter the call's date, time and Contact" (decision 5's "date/time message").
- **Service.** `Pipeline.close` has Story 2.15's exact keyword-only `cause`/`actor`. Commands `move`, `book_call`, `reopen`, `save_deal`, `erase_pii`; the dashboard calls three `@use_case` wrappers (`operator_move`, `operator_book_call`, `operator_save_deal`, actor `operator`), because a `StageRefused` leaving a command dooms the transaction — every check runs before the first write. A forged `move=call_booked` is refused `booking_incomplete` (only the call form books). `reparent_lead` also moves the merged Lead's `call_outcomes` rows to the survivor (needed by the persistence contract's reparent check for the new `lead_id` FK).
- **Same-stage rows.** Decision 4's filter (`from_status` NULL or ≠ `to_status`) applies to the board's entry time and also to `stages.last_close` (reopen's target and the opt-out `close_ref`), so a deal save on a closed Lead is never taken for its close.
- **Board totals** sum `deal_value // 1_000_000` (whole dollars) in SQL and multiply back in Python, so SQLite's 64-bit `SUM` can't overflow; `StageGroup.deal_value` is micro-dollars, the page shows "Deal value: $12,000".
- **Web.** `POST /leads/{lead_id}/stage|call|deal` (`lead_stage`, `lead_call`, `lead_deal`): parse the form (malformed → 400), check the Lead is a live survivor (else 404 "That Lead doesn't exist."), run the use case, answer the panel 200 with a notice. The call form reads the Contact in a read-only unit first (blank zone → the Contact's `tz`). The notes' `<textarea>` sits in an `hx-disable` wrapper inside its form (HTMX posts it through `FormData`); the notes display and Contact names render `|plain` inside `hx-disable`. Panel context keys are prefixed (`pipeline_lead_id`, `pipeline_notice`) so they don't collide with the Lead page's.
- **Subject access export (genuine gap, conservative choice).** The Story 3.15 export contract requires coverage for any table with a personal-data column or a FK to `contacts.id`. `contact_export.COVERAGE` adds `leads` → `Exported("pipeline", "lead_notes", shown_as={"notes": "notes_excerpts"})` (only the lines of the Lead's notes that mention the Contact, like crawled pages) and `call_outcomes` → `Exported("pipeline", "call_bookings")`; `queries.pipeline.export_pii(contact_id, lead_id, terms)` and `Compliance.export_contact` provide them. The export contract's count assertion became 17 personal-data tables / 18 coverage entries.
- **Erasure contract.** `owner_of(table, tables, pii)` returns the single owner of a table's personal-data columns when several owners write it; single-writer tables are unchanged. `COVERAGE` adds `leads: (pipeline, tombstoned)`; `Pipeline.erase_pii` is registered after research's.
- **Review patches.** `call_time` turns an `OverflowError` (a local time whose UTC lies outside year 1–9999) into `bad_date_time`. The "Call booked for …" line shows only while the Lead is at `call_booked`. `Pipeline.erase_pii` redacts, and `queries.pipeline.export_pii(contact_id, lead_ids, terms)` exports, the notes of the Contact's Lead and of every Lead merged into it (`queries.discovery.merged_lead_ids`; `Compliance.export_contact` passes the ids). A refused call or deal form renders back what was typed (`panel_context(..., typed_call=, typed_deal=)`; notes still `|plain` in `hx-disable`). Won, Close lost and Close disqualified carry `hx-confirm` (`lead_pipeline.CONFIRMS`). The call form's Contact, date and time are `required`. The board uses `lead_pipeline.deal_line`, the single micro-dollar formatter. Tests added for each.
- **Pins updated after the full suite:** `test_contact_export` (the `pipeline` export sections and their document order), `test_leads_page` (the Lead page's new "Pipeline" heading), `test_pipeline_stage` (the causes `deal_details_saved` and `rebooked`), and `test_lead_provenance_page` (the Pipeline panel's forms post only to `/leads/<id>/stage|call|deal`; every other HTMX action is still pinned).
- **Deferred:** Drafts/Enrollments stop on close; what a merge does with the merged Lead's deal value and notes (both in `deferred-work.md`).

## Spec Change Log

## Review Triage Log

Raw findings 29 (edge-case 8, blind 15, verification-gap 4 gaps + 2 other); 23 after merging duplicates (merged-Lead notes: blind + vg-other; past booking: blind + edge; booking left on close: blind + edge; two pending bookings after merge: blind + edge; opt-out line: edge ×2 + blind). 11 patch, 0 defer, 12 rejected.

| # | Source | Finding | Verdict | Route |
|---|--------|---------|---------|-------|
| 1 | blind | `call_time` raises `OverflowError` (500) for a date at the ends of the range, e.g. `0001-01-01` in `Asia/Tokyo`; only `ValueError` is caught. | low: forged input only, but a direct correction. | patch: catch `OverflowError` as `bad_date_time`. |
| 2 | blind + edge | Closing or moving a Lead at `call_booked` leaves its pending booking; the panel keeps showing "Call booked for ...". | medium: a closed Lead advertises a live call. | patch: the panel shows the booking line only while the Lead is at `call_booked`. (Stories 9.3/9.4 must also check the stage; reported.) |
| 3 | blind + vg-other | `erase_pii` and the subject access export read only `contact.lead_id`'s notes; a merged Lead's notes that name the Contact survive erasure and are missing from the export. | medium: AD-17 erasure gap. | patch: erasure and export also cover every Lead merged (transitively) into the Contact's Lead. |
| 4 | blind | A refused deal or call form re-renders with stored values, losing up to 5,000 typed characters and the typed date/time/zone. | low: real, everyday on a typo; the fix only passes submitted values back (escaped). | patch. |
| 5 | blind | Close lost, Close disqualified and Won are one-click with no confirmation; Won is irreversible from the panel. | low: misclick risk; one attribute. | patch: `hx-confirm` on those buttons. |
| 6 | blind | Micro-dollar formatting is duplicated in `pipeline_page` and `lead_pipeline`. | low: two sources of truth for "$12,000". | patch: one helper. |
| 7 | blind | The call form's Contact, date and time fields lack `required`. | low: one attribute each. | patch. |
| 8 | vg | `test_an_unknown_or_merged_lead_is_404` omits the call route. | gap (pre-verified). | patch: add `call`. |
| 9 | vg | Nothing checks the notes textarea is pre-filled; dropping it would wipe notes on the next save. | gap (pre-verified). | patch: assert the prefill and a round-trip save. |
| 10 | vg | The call form's Contact options (erased excluded, Primary/booked pre-selected, `hx-disable`) are untested. | gap (pre-verified). | patch: a panel test. |
| 11 | vg | `close`'s `cause` keyword and new argument checks have no test in this tree. | gap (pre-verified; filed defer). | patch: a small service test (cheap; main's tests also cover it after the merge). |
| 12 | blind | `book_call` doesn't call `assert_contactable`. | false: booking records a call and sends nothing; AD-16 gates outreach, and Story 9.3 gates the call-sheet delivery (epic context). | reject. |
| 13 | blind + edge | A call can be booked in the past. | low: the spec defines no such refusal or message; the fix adds a refusal reason. | reject (would change the approved spec). |
| 14 | blind + edge | A merge can leave the survivor two pending bookings (panel shows the newest). | low: needs both merged Leads to hold bookings; fix adds an invariant and branches. | reject. |
| 15 | edge ×2 + blind | The opt-out line depends only on the latest close (misses re-close by hand, merge-closed survivors, already-lost Leads). | medium: but decision 6 defines it exactly this way. | reject (would change the approved spec); reported. |
| 16 | edge | A Lead merged between the read-only `live_lead` check and the write unit is written to. | low: single-Operator dashboard, millisecond window; fix adds a guard. | reject. |
| 17 | edge | A wrong-case zone such as `europe/london` is stored non-canonically on a case-insensitive filesystem. | false: `ZoneInfo('europe/london')` raises `ZoneInfoNotFoundError` on this PC, so it is refused. | reject. |
| 18 | edge | `pipeline_answer` raises `LookupError` (500) if the Lead vanishes after a POST. | false: Leads are never deleted and every route checks `live_lead` first. | reject. |
| 19 | blind | Erasure replaces the whole notes text, while the export takes only lines naming the Contact. | low: decision 2 chooses whole-notes redaction (the conservative side). | reject (would change the approved spec). |
| 20 | blind | No conflict check: a stale tab can overwrite newer notes, even `[erased]`. | low: needs two tabs across an erasure; fix adds a version field. | reject. |
| 21 | blind | Some new lines exceed 100 characters. | false: no line-length linter is configured or installed (`ruff` absent); cosmetic. | reject. |
| 22 | blind | `call_outcomes.contact_id` has no index. | low: a handful of rows per Lead; fix edits the migration. | reject. |
| 23 | vg-other | `stages.last_close` may pick the wrong row on an `at` tie. | false: the same-stage filter excludes deal-save rows, so only real closes compete. | reject. |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q tests/contracts <new and touched test files>` -- expected: pass
- `cd nextron-engine && uv run lint-imports` -- expected: all contracts kept
