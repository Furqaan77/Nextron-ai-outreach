---
title: 'Story 9.4: Prompt for and record each Call Outcome'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '71cd0a559b78ecf8729c4a470beef46d9e7d75ae'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-9-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-9-1-move-leads-through-the-later-pipeline-stages-by-hand-with-de.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A booked call (Story 9.1's `call_outcomes` row) is never followed up: nothing asks Mac how the call went, nothing records held/no-show, qualification, audit proposal, price reaction or objections, and `call_held` can never be reached (FR-69, FR-45).

**Approach:** Migration `0038_9_4_call_outcomes` adds the outcome columns plus `recorded_at`/`recorded_by` to `call_outcomes`. The notification area lists "Call Outcome due: <Lead name>" for each due booking, worked out at render time. A Call Outcome form in the Lead page's Pipeline panel records (or corrects) the outcome through `Pipeline.record_call_outcome` / `correct_call_outcome` in one unit of work, moving the stage per the AD-4 machine.

### Decisions made overnight (pending user review)
1. **Due** = a booking with no `superseded_at` and no `recorded_at`, whose Lead is a live survivor at `call_booked`, and `scheduled_at + call_outcome_prompt_delay <= now`. A closed Lead doesn't nag (9.1: `close` leaves bookings pending). New setting `call_outcome_prompt_delay` (type `duration`, section Engine, default `PT60M`).
2. **The form** shows on the Lead panel (anchor `#call-outcome`, the due line links to `/leads/<id>#call-outcome`) for the oldest unsuperseded, unrecorded booking whose `scheduled_at <= now`, on any stage; otherwise, for the newest recorded booking as a prefilled "Correct Call Outcome" form. POST `/leads/{lead_id}/outcome` with a hidden `booking` id.
3. **Required fields:** `outcome` (`held`|`no_show`). For held: `qualified` (yes/no); `reason` when no; `audit_proposed` (yes/no); `audit_price` when yes (whole USD, 9.1's `parse_deal_value` rules); `price_reaction`. Optional: objections (`ObjectionType` codes, multi-select, stored comma-joined sorted, `""` for none), `next_step` text and `next_step_on` date (`YYYY-MM-DD`). Fields that don't apply (all held fields for a no-show; reason when qualified; price when not proposed) are ignored and stored NULL.
4. **Text:** `reason` and `next_step` are plain text, CRLF→LF, at most 500 characters, PII class `redact`; `Pipeline.erase_pii` also sets them to `[erased]` on every `call_outcomes` row of the erased Contact's Lead and Leads merged into it. All other new columns are `none`.
5. **Events:** recording appends entity `call_outcomes`, `booked → held|no_show`, cause `call_outcome`, actor `operator`. A correction appends one row per changed field: `<outcome> → <outcome>` (the stored outcome before/after), cause `corrected_<field>`, no `cause_ref`; no change = no write, notice "Nothing changed.".

## Boundaries & Constraints

**Always:** Every check runs before the first write (typed refusal, as 9.1); a refused form re-renders with the typed values and the error next to its field, storing nothing. `@personal_data(True)`, CSRF, `hx-post`; text renders `|plain` inside `hx-disable`. Money as integer micro-dollars. Stage changes only via `domain.pipeline_stage.transition` (`HOLD_CALL`, `NO_SHOW`). Events hold no values.

**Never:** Send email, Telegram or alerts; create a rebook Draft (Story 9.5); add a job or stored "due" flag; recompute the stage on correction; weaken a contract.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Due prompt | pending booking, Lead `call_booked`, scheduled 61 min ago | area shows "Call Outcome due: Acme Ltd" linking to `/leads/<id>#call-outcome` | 59 min ago or Lead closed: not shown |
| Held | Lead `call_booked`, valid held form | row saved, `recorded_at/by` set; Lead `call_held`, events cause `call_outcome`, `cause_ref` = row id; prompt clears | N/A |
| No-show | Lead `call_booked` (or `call_held`) | row saved; stage stays `call_booked` (or `call_held → call_booked`, cause `call_outcome`) | N/A |
| Past/closed | Lead `audit_proposed` or `lost` | row saved, stage unchanged | N/A |
| Missing field | held without `qualified`, or not-qualified without reason, or audit proposed without price, bad price/date, text > 500 | form re-rendered with the field's error; nothing stored | N/A |
| Correction | recorded row, changed fields | row updated; one events row per changed field; stage unchanged | N/A |
| Malformed | unknown/repeated field, unknown code, booking not this Lead's or superseded | 400 "Request refused: malformed form"; unknown Lead 404 | `MalformedForm` |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/domain/call_outcomes.py` (new) -- `CallResult` (`held`, `no_show`), `PriceReaction`, `OutcomeRefusal` + `OutcomeRefused(field, reason)`, `OutcomeForm`/`Outcome` value, `check_outcome(...)` (decision 3/4 rules), `CORRECTABLE_FIELDS`, `correction_cause(field)`. Reuse `manual_stages.parse_deal_value`, `reply_classes.ObjectionType`.
- `nextron-engine/nextron/domain/settings_schema.py` -- `Key.CALL_OUTCOME_PROMPT_DELAY`, `Setting(type=DURATION, section=ENGINE, default=timedelta(minutes=60))`; check `tests/unit/test_settings_page.py`/doctor pins.
- `nextron-engine/nextron/db/alembic/versions/0038_9_4_call_outcomes.py` -- `batch_alter_table("call_outcomes")` `add_column` ×11 (outcome, qualified, reason, next_step, next_step_on, audit_proposed, audit_price, objections, price_reaction, recorded_at, recorded_by), all nullable, no CHECK (as 0037). Downgrade raises.
- `nextron-engine/nextron/db/models/pipeline.py` -- new mapped columns; `owners.py` (pipeline), `pii_registry.py` (decision 4).
- `nextron-engine/nextron/db/repos/pipeline/bookings.py` -- extend `_COLUMNS`/`StoredBooking`; `get(id)`, `record(id, outcome, at, by)`, `update_outcome(id, outcome)`, `oldest_open_due(lead_id, now)`, `latest_recorded(lead_id)`, `due(cutoff)` (join `leads`: stage `call_booked`, not merged), `redact_text(lead_ids, REDACTED)`. `about()` exports the new columns automatically.
- `nextron-engine/nextron/app/pipeline/api.py` -- `@command record_call_outcome(booking_id, outcome, *, actor)`, `@command correct_call_outcome(booking_id, outcome, *, actor)`, `@use_case operator_record_call_outcome(lead_id, booking_id, outcome)` (record or correct by `recorded_at`), read `call_outcomes_due()` (uses `self._clock` + `queries.setup.get`); extend `erase_pii`, `ERASURE` (`call_outcomes` tombstoned).
- `nextron-engine/nextron/app/queries/pipeline.py` -- `lead_pipeline` gains the outcome-form booking; `call_outcomes_due(cutoff)` → (lead_id, lead name).
- `nextron-engine/nextron/web/app.py:~1608/1720` -- `notification_list` passes due items; `POST /leads/{lead_id}/outcome` (`lead_outcome`) near `lead_call`.
- `nextron-engine/nextron/web/lead_pipeline.py` + new `templates/lead_call_outcome.html` (included by `lead_pipeline.html`) -- parsing, `MalformedForm`, field errors, labels; `notifications.html` -- due lines (`|plain`, `hx-disable`), rendered even when nothing else shows.
- Contracts: `HEAD` in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`; chain probes; `test_source_tree` `FILES`; `test_web_contracts`; erasure contract `COVERAGE` (`call_outcomes`); export contract counts if affected.
- Reuse: 9.1's panel pattern (`panel_context(..., typed_*)`, `REFUSALS`, `pipeline_answer`), `tests/app/test_leads.py` `queue_on(clock)`.

## Tasks & Acceptance

**Execution:**
- [x] domain module + setting -- unit tests for every decision-3 rule and correction causes.
- [x] migration + model + owners + PII -- migration/persistence tests.
- [x] repos + `Pipeline` commands/use case/due read + erasure -- app tests for each matrix row at the service level, events contents (no values), atomic refusal, erasure redacts reason/next_step.
- [x] queries + web form + route + notification area -- web tests for each matrix row, CSRF, no-store, `|plain` in `hx-disable`, prompt shows/clears.
- [x] `nextron-engine/README.md` -- document; `deferred-work.md` -- note anything deferred.

**Acceptance Criteria:**
- Given a due booking, when any dashboard page loads its notification area, then the due line shows, and after the outcome is saved it no longer does, with no job or stored flag involved.
- Given any outcome write, then exactly the stated `events` rows are appended in the same transaction and a refusal leaves no row changed.
- Given an erased Contact, then every `reason`/`next_step` on its Lead's (and merged Leads') `call_outcomes` reads `[erased]`.

## Implementation Notes

- **Domain.** New `nextron/domain/call_outcomes.py`: `CallResult`, `PriceReaction`, `OutcomeRefusal` (`missing`, `bad_price`, `bad_date`, `too_long`) + `OutcomeRefused(field, reason)`, `OutcomeForm` (typed strings), `Outcome` (a value that checks its own invariants in `__post_init__`: no-show holds no held field, reason exactly when not qualified, price exactly when proposed, objections a sorted distinct tuple; `columns()`/`from_columns()` for storage), `check_outcome` (first refusal in `CORRECTABLE_FIELDS` order; an unknown code raises `ValueError`, which the web layer pre-empts as malformed), `changed_fields`, `correction_cause`. The audit price reuses `manual_stages.parse_deal_value`. The next step and its date apply to either result (not a "held field"), so a no-show may carry "rebook Tuesday".
- **Setting.** `Key.CALL_OUTCOME_PROMPT_DELAY` (duration, Engine, default 60 minutes) is the last `Key`/`SCHEMA` entry (they must share order). Pins updated in `test_settings_schema` (key list, declared, two "not declared" lists), `test_settings_contracts`, `test_settings_page` (Engine field order); a new schema test for the setting.
- **Migration `0038_9_4_call_outcomes`** (down `0037_9_1_manual_stages`): 11 nullable `add_column`s, no CHECK. `reason`/`next_step` are `redact`, the rest `none`; `owners.py` maps all to `pipeline`. Contracts bumped: `HEAD` ×3, chain probes (0039/0040), `test_source_tree` FILES (domain, migration, template), `test_web_contracts` (route + template), erasure `COVERAGE` (`call_outcomes` tombstoned), export contract personal-data table count 17 → 18, `test_erasure`'s planted-value test (plants a recorded booking on the merged Lead). `test_migrations` 0037 test now checks the first 8 `call_outcomes` columns; new 0038 test.
- **Pending means unrecorded too.** `bookings.pending` now also requires `recorded_at IS NULL`, so a no-show at `call_booked` no longer shows "Call booked for …" and a later rebooking never supersedes (or writes `booked → superseded` for) a recorded call. `latest_recorded` excludes superseded rows.
- **Service.** `record_call_outcome` / `correct_call_outcome` (commands, check before writing), `operator_record_call_outcome` (use case: unknown, another Lead's or superseded booking raises `NotThisLeadsBooking` → web 400; returns `OutcomeSaved` recorded/corrected/unchanged), `call_outcomes_due()` and `lead_panel(lead_id)` (reads that supply the clock's now, since the web layer has no clock). The `call_outcomes` recording event has no `cause_ref` (the entity id is the booking); the stage row's `cause_ref` is the booking id. `erase_pii` also runs `bookings.redact_text(lead_ids, [erased])`.
- **Queries.** `lead_pipeline(lead_id, *, now=None)` gains `outcome_booking` (only when `now` is given); `call_outcomes_due(cutoff)` → `DueBooking(booking_id, lead_id, lead_name)`, one per due booking.
- **Web.** `POST /leads/{lead_id}/outcome` (`lead_outcome`); `lead_pipeline.submitted_outcome` treats absent single fields as blank (so radio-like/blank selects are field errors, not malformed), unknown codes/repeated fields/duplicate objections/non-ULID booking as `MalformedForm`. New `lead_call_outcome.html` (selects for codes, checkboxes for objections, texts in `hx-disable` wrappers via `|plain`, `field-error` + `aria-invalid` next to the refused field). The panel alert on refusal is "The Call Outcome wasn't saved."; saved notices "Call Outcome recorded.", "Call Outcome corrected.", "Nothing changed.". `notifications.html` renders the due list (`<ul class="outcome-due" hx-disable>`, names `|plain`) independent of the notifications count. A booking scheduled in the future can be recorded by a direct POST (the form only shows once its time has passed); not refused, since the spec defines no such refusal.
- **Tests.** `tests/unit/test_call_outcome_rules.py`, `tests/app/test_call_outcomes.py`, `tests/unit/test_call_outcome_page.py`.
- **Review patches (triage 1-10).** `call_outcome_prompt_delay` gains `maximum(timedelta(days=7), "7 days")` (P8D refused, P7D accepted; unit text says so). `operator_record_call_outcome(lead_id, booking_id, form: OutcomeForm)` now checks the booking first (`NotThisLeadsBooking` → 400 whatever the fields), then runs `check_outcome` inside the transaction before any write; the web route no longer calls `check_outcome` and its unreachable `except LookupError` is gone. A refused form re-renders with the posted booking id (`typed_booking_id` through `pipeline_answer`/`pipeline_context`/`panel_context`/`outcome_view`). A saved outcome answers with `HX-Trigger: call-outcome-saved` (`OUTCOME_SAVED_EVENT`) and `base.html`'s `#notification-list` uses `hx-trigger="load, call-outcome-saved from:body"` (pins updated in `test_dashboard`, `test_notifications_area`, `test_google_page`, `test_untrusted_rendering`). The corrected notice is "Call Outcome corrected. The stage wasn't changed; move it by hand if needed.". `ANSWER_LABELS`/`typed_from` use `YES`/`NO`; a test pins `set(OBJECTION_LABELS) == set(ObjectionType)`. `ERASURE["call_outcomes"]` and the README say the redaction covers every booking of the Lead and its merged Leads, whichever Contact the call was with. New tests: missing `audit_proposed`, 501-char `reason`, refused correction re-render, web no-show storing held fields NULL, foreign/superseded booking with an incomplete form (service + web, events and rows unchanged), `test_a_due_rebooking_takes_the_form_over_a_recorded_no_show` (service) and its web twin (hidden `booking` is the rebooking, heading "Call Outcome", a stale refused form keeps its posted booking).

- **Pin updated after the full suite:** `tests/app/test_telegram_secrecy.py` posts the whole Settings form, so it now includes `call_outcome_prompt_delay`.

## Spec Change Log

## Review Triage Log

Raw findings 24 (blind 14, edge-case 7 + 2 claims, verification-gap 1 gap); 20 after merging duplicates (prompt-delay overflow: blind + edge; foreign booking with an incomplete form: edge + claim; due line not refreshed after save: edge + claim; `LookupError` branch: blind + edge). 10 patch, 0 defer, 10 rejected.

| # | Source | Finding | Verdict | Route |
|---|--------|---------|---------|-------|
| 1 | blind + edge | `call_outcome_prompt_delay` has no bounds: a huge delay (up to the codec's 999,999 days) makes `now - delay` raise `OverflowError`, so every page's notification area answers 500; a large one silently disables the prompt. | medium: an Operator setting can break every page. | patch: add a `maximum` rule (7 days) to the setting, with a test. |
| 2 | edge + claim | A posted booking that is another Lead's, superseded or unknown, sent with an incomplete form, gets a 200 field refusal instead of the matrix's 400, because `check_outcome` runs before the booking check. | low: matrix deviation; a direct reordering. | patch: check the booking before the fields (the use case takes the form and runs `check_outcome` after the booking check, or equivalent). |
| 3 | edge | A refused form re-renders the typed values under the panel's current `outcome_booking`, which can differ from the posted booking (stale form), so a resubmission would record the wrong booking. | low: stale-tab only, but the fix is passing the posted id back. | patch: the re-rendered form keeps the posted booking id. |
| 4 | edge + claim | After saving an outcome, the HTMX panel swap doesn't refresh the notification area, so the due line stays until the next page load. | low: AC1 says the count clears once saved. | patch: answer with an `HX-Trigger` event and have `#notification-list` re-fetch on it. |
| 5 | blind + edge | `lead_outcome`'s `except LookupError` is unreachable (the use case raises `NotThisLeadsBooking` first; Leads are never deleted) and would mask programming errors as 404. | low: dead code that hides bugs. | patch: delete the branch. |
| 6 | blind | A correction leaves the stage as it was (by design), but the notice doesn't tell Mac the stage may need moving by hand. | low: one notice text. | patch: the corrected notice says the stage wasn't changed. |
| 7 | blind | `OBJECTION_LABELS` lists the three `ObjectionType` members by hand; a new type would get no checkbox and a correction would silently drop it. | low: direct correction. | patch: build or assert the labels from `ObjectionType`. |
| 8 | blind | Erasure blanks `reason`/`next_step` on every booking of the Lead (decision 4), but `ERASURE`'s description and the README don't say it covers bookings with other Contacts too. | low: documentation of an erasure scope. | patch: say so in both. |
| 9 | blind | Missing tests: `audit_proposed` missing, a too-long `reason`, a refused correction re-render, no-show ignoring held fields at the web level; the foreign-booking refusal test checks only one count. | gap. | patch: add those cases and assert the events count is unchanged. |
| 10 | vg | Nothing tests that a due pending booking takes the form over a recorded no-show (the `or` order in `lead_pipeline`). | gap (pre-verified). | patch: add `test_a_due_rebooking_takes_the_form_over_a_recorded_no_show` plus a web assertion on the hidden `booking`. |
| 11 | blind | Spec still `in-progress`, triage log empty, sprint key `in-progress`. | false: the review runs before those steps; they are set at finishing. | reject. |
| 12 | blind | `deferred-work.md` unchanged despite the ticked task. | false: the task says note anything deferred, and nothing was deferred. | reject. |
| 13 | blind | A direct POST can record a future call as held and move the Lead to `call_held`. | low: forged input only (the form appears only after the call time); the fix adds a refusal reason and message the spec doesn't define. | reject (would change the approved spec). |
| 14 | blind | The due lines have no cap or "and N more". | low: a backlog of many unrecorded calls is unlikely for one Operator; the fix adds a cap and a branch. | reject. |
| 15 | blind | One Lead can show several identical due lines. | low: needs several pending bookings on one Lead, which only a merge produces (9.1 triage #14). | reject. |
| 16 | blind | Import order and a 130-character docstring line would fail ruff. | false: no ruff or line-length linter is configured (9.1 triage #21). | reject. |
| 17 | blind | `redact_text` overcounts on a second erasure. | low: the count is unused. | reject. |
| 18 | blind | `next_step_on` without `next_step`, or dated before the call, is accepted. | low: decision 3 makes both optional with no such rule. | reject (would change the approved spec). |
| 19 | edge | A stale "Call Outcome" form posted after another tab recorded the booking is applied as a correction. | low: two tabs, one Operator; the fix adds an intent field. | reject. |
| 20 | edge | A refused `next_step_on` echoes non-ISO text into a `type=date` input, which the browser blanks. | false: a browser date input only ever posts an ISO date or blank, so only a forged POST reaches this. | reject. |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q -n 2 tests/contracts <new and touched test files>` -- expected: pass
- `cd nextron-engine && uv run lint-imports` -- expected: all contracts kept
