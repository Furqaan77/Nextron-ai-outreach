---
title: "Story 4.10: Show today's capacity and pick Leads to draft"
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '43f9c1b2afe7291f31cc49648341ba4ccf0259a3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Enrollment (4.8) and Draft slots (4.9) exist, but nothing tells Mac how many new Sequences the inboxes can carry today, and nothing lets him pick Leads to start them (FR-19 S1, FR-59, UX-DR20). Unused capacity is never recorded as signal shortfall.

**Approach:** A pure capacity formula in `domain.sending_policy` and a pure "Ready to draft" order in `domain.scoring`. A `daily_capacity` table (owner `scoring`, migration 0043). A `min_lead_score` setting. A new `Selection` service in `app/scoring/`: a read-only list and today's capacity, plus `@use_case pick(lead_ids)`, which enrolls through 4.8's `enroll` up to the remaining capacity. A "Ready to draft" page at `/leads/ready`, and a "signal shortfall: n" line in the notification area.

## Boundaries & Constraints

**Always:**
- **Capacity.** `new_sequence_capacity(total_cold, follow_ups_due, steps)` = `min(capacity_forecast(total_cold, follow_ups_due), total_cold // steps)`, never negative. Ints are checked; `steps` must be at least 1.
  - Unit tests: 3 inboxes at 30 with 61 due and 4 Steps → 22; with 80 due → 10; all caps 0 → 0.
- **Inputs, computed live at each computation.** The date is `local_date(now, sender_timezone)`, or UTC while the timezone is unset or unreadable (as `_local_date` does).
  - `total` = sum of `queries.sending.cold_daily_caps(date)`.
  - `due` = the sum over inboxes of the follow-ups due that date, read through a new `queries.sequencing.follow_ups_due(local_date, zone)`. 4.9's `_follow_ups_due` moves there and the sequencing service delegates to it, so the behaviour is unchanged.
  - `steps` = the largest Step count among stored Sequences. With none stored, capacity is 0.
  - **Remaining today** = `max(0, capacity − picked)`, where `picked` comes from today's `daily_capacity` row (0 without one).
- **`daily_capacity`** (owner `scoring`, PII `none`). Columns: `id` (ULID PK), `local_date` (unique), `total_cold_capacity`, `follow_ups_reserved`, `new_sequence_capacity`, `picked` (default 0), `signal_shortfall` (NULL until finalised) and `computed_at`, with non-negative CHECKs.
  - `Selection.record_capacity()` is a `@command` that runs at each pick and once a day. It upserts today's row with the live figures. It also finalises every earlier row whose `signal_shortfall` is NULL, setting it to `max(0, new_sequence_capacity − picked)`.
  - `pick` adds its enrolled count to `picked`.
  - Nothing ever fills unused capacity automatically.
- **"Ready to draft" list** (read-only). It shows Leads that meet all of these:
  - scored (`tier` set);
  - not merged;
  - stage `new`, `queued` or `contacted`;
  - a Primary Contact that isn't erased;
  - no live Enrollment.

  **Order** is `domain.scoring.ready_order`:
  - Tier A by newest live Event first (the smallest `domain.signals.age_days` over the Lead's stored `event` factors' Signals that aren't retracted; ties go to the higher Lead Score);
  - then Tier B by Lead Score;
  - then Tier C by Lead Score;
  - final tie-break by Lead id.

  **Each row** shows name, Segment, Market (the Contact's), Tier, Lead Score, the Primary Contact's role and the live Eligibility (`compliance.evaluate_eligibility`). Tier C carries a "generic copy" badge. Lead Score below `min_lead_score` carries a "below minimum score" badge. Both can still be picked.

  **Header:** "Remaining today: n of N". When `total` is 0: "No cold capacity yet: inboxes need 14 days of recorded warm-up".
- **`pick(lead_ids)`** runs in one transaction (`BEGIN IMMEDIATE`) and works in the given order. It returns one `PickOutcome(lead_id, enrollment_id | None, reason | None)` per Lead.
  - The reasons, in order: `unknown_lead` (unknown or merged) → `no_primary_contact` → `no_campaign` (no Campaign for the Lead's Segment and the Contact's Market) → `over_capacity` (when remaining is 0) → else `Sequencing.enroll(contact, campaign, operator_pick, all Steps)`. A refusal returns its `RefusalCode` value.
  - **Campaign choice:** the first Campaign by key that `campaign_active` allows, else the first by key, so `enroll` answers `campaign_not_active`.
  - Then it calls `record_capacity` and adds the enrolled count to `picked`.
  - It logs `leads_picked` with counts only.
- **Setting `min_lead_score`:** integer, Engine section, label "Minimum Lead Score", default 50, range 0–100.
- **Notification area:** shows `signal shortfall: n` (n = today's remaining) when n > 0.
- **Routes:**
  - `GET /leads/ready` and `POST /leads/ready/pick`, both `@personal_data(True)`, registered before `/leads/{lead_id}`.
  - The form posts the checked `lead` ULIDs in page order. Each must be a ULID, distinct, with 1–200 of them; otherwise it is `MalformedForm` (400).
  - The POST re-renders the list with a per-Lead result table: "Enrolled", or "Refused: <plain reason>".
  - The Leads list links to the new page; the nav is unchanged.
- **AD-16:** a `scoring.pick` manifest entry with a scenario. On `Allowed`, the gate runs before the first write. On `Blocked`, no guarded table gains a row; the `daily_capacity` write is allowed.

**Never:**
- send email, call AI or enqueue jobs from `pick` (4.9's slots only);
- automatic selection (8.9);
- a new trigger;
- write another owner's tables;
- store the Eligibility Decision;
- change the nav;
- add a new dependency.

### Decisions made overnight (pending user review)
1. **"Enriched" means scored** (`tier` set; 2.15 scores only after a successful extraction). The list is limited to stages `enroll` accepts (`new`, `queued`, `contacted`), since the rest would always be refused.
2. **Recording.** GETs are read-only, so rows are written by `pick` and by the daily `recompute_tiers` run, which calls `record_capacity` after re-tiering. That way a day with no picks still gets a row. AD-10 has no Slice 1 capacity trigger.
3. **Shortfall line** = the current day's live remaining capacity, as the AC words it.
4. **Steps** = the largest Sequence's Step count (the conservative choice). **Selection order** = the order of the posted fields (page order).
5. **Follow-ups due** counts unsent ones only. Epic 6 must keep follow-ups already sent today counted (logged in `deferred-work.md`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Capacity | caps 30,30,30; 61 due; 4 Steps | 22 (80 due → 10; caps 0 → 0) | N/A |
| No warm-up | every cap 0 | "No cold capacity yet…"; picks `over_capacity` | N/A |
| Pick within capacity | remaining 2; picks A,B,C eligible | A,B enrolled (4.9 slots), C `over_capacity`; `picked` +2 | N/A |
| Refused | ineligible / blocked Contact | `not_eligible` / `blocked`; nothing written for it; capacity not consumed | N/A |
| Next day | yesterday's row unfinalised | first computation sets its `signal_shortfall` | N/A |
| Bad form | non-ULID, duplicate, 0 or >200 | 400 | `MalformedForm` |

</frozen-after-approval>

## Code Map

- `nextron/domain/sending_policy.py` -- `capacity_forecast`, `_count`, `local_date`: add `new_sequence_capacity` here (single definition; Epic 8 reuses it).
- `nextron/domain/scoring.py` -- add `ReadyCandidate(lead_id, tier, lead_score, newest_event_age)` and `ready_order`, plus `below_minimum(score, minimum)`. `Tier`, `Factor`/`FactorKind.EVENT` (`signal_id`). Use `domain.signals.age_days`.
- `nextron/app/sequencing/api.py:235-251` -- `_local_date`, `_sender_zone` and `_follow_ups_due`. Move the body to `queries.sequencing.follow_ups_due` and keep `_picked_inbox` behaviour.
- `nextron/app/queries/sending.py` -- `cold_daily_caps(local_date)`. `queries/setup.py` -- `get`, `sequences()`, `campaigns()` (by key), `campaign_active`, `segment_by_id`, `market`. `queries/research.contacts`/`signals`. `queries/sequencing.live_enrollment`. `queries/scoring.stored`.
- `nextron/db/repos/scoring/scores.py` -- precedent `retier_candidates` (reads `pipeline_stage`/`merged_into` read-only). Add `ready_candidates()` and a new `db/repos/scoring/capacity.py` (upsert today, add picked, finalise earlier, read by date).
- `nextron/app/scoring/` -- new `selection.py`, holding `Selection(clock, sequencing, compliance)` (`ready_list()`, `today()`, `record_capacity`, `pick`, `PickOutcome`, `ReadyRow`, `TodayCapacity`). `lead_scores.py`: `LeadScores(..., *, selection=None)`; `recompute_tiers` calls `selection.record_capacity()` in its own transaction after the loop.
- `nextron/db/alembic/versions/0043_4_10_daily_capacity.py` (down `0042_4_9_drafts`). Model in `db/models/` (scoring), `models/__init__.py`, `db/owners.py`, `db/pii_registry.py`.
- `nextron/domain/settings_schema.py:~1323` -- next to the Lead Score weights, add `Key.MIN_LEAD_SCORE`. Update the settings tests the way 6.4 did (`test_settings_schema.py`, `test_settings_contracts.py`, `test_settings_page.py`, `test_telegram_secrecy.py`).
- `nextron/web/app.py` -- `create_app(..., selection=)` (required). `area_context(..., shortfall=)` and `notifications.html`. Routes go before `/leads/{lead_id}`. Callers: `tests/dashboard.py` and the 3 test files calling `create_app`. New `web/ready_page.py` and `templates/leads_ready.html` (copy the `leads_page.py`/`eligibility_row` style). `leads.html` gets a "Ready to draft" link.
- `nextron/main.py` -- wiring: `Selection`, `LeadScores(selection=)`, `create_app(selection=)`.
- Tests to update: `tests/contracts/test_ad16_callers.py` (manifest plus scenario), HEAD in `test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py` and chain probes, `test_source_tree.FILES`, `test_web_contracts.py` routes.

## Tasks & Acceptance

**Execution:**
- [x] migration + model + owners + PII + HEAD/probes/FILES
- [x] domain: `new_sequence_capacity`, `ready_order`, `below_minimum` + unit tests (the AC numbers)
- [x] `queries.sequencing.follow_ups_due` move; repos; `Selection`; `LeadScores` hook; setting
- [x] web page, form, results, area line, Leads-list link; `main.py` wiring
- [x] tests: `tests/unit/test_sending_policy.py`, `tests/unit/test_ready_order.py`, `tests/app/test_selection.py` (list filter/order/badges, pick matrix, row upsert/finalise, recompute hook), `tests/unit/test_ready_page.py` (render, header texts, 400s, area line), AD-16 scenario
- [x] `nextron-engine/README.md` -- Story 4.10 section and the 0043 row

**Acceptance Criteria:**
- Given the migration at head, then `daily_capacity` exists, with every column mapped to `scoring` and classified `none`.
- Given a pick, then each enrolled Lead has a live Enrollment with origin `operator_pick` and 4.9's Draft slots, and no job, AI call or email results.

## Implementation Notes

- **Migration** `0043_4_10_daily_capacity.py` (revision `0043_4_10_daily_capacity`, down `0042_4_9_drafts`): `daily_capacity` with `uq_daily_capacity_local_date` and `ck_daily_capacity_counts`. Model `DailyCapacityRecord` in the new `db/models/scoring.py`; every column owned by `scoring`, PII `none`. HEAD, the chain probes (now 0044/0045) and `test_source_tree.FILES` bumped.
- **Domain:** `sending_policy.new_sequence_capacity`; `scoring.ReadyCandidate`, `ready_order` (a Tier A Lead without a live Event sorts after those with one), `below_minimum`, `DEFAULT_MIN_LEAD_SCORE = 50`. Setting `min_lead_score` (Engine, "Minimum Lead Score", 0�100).
- **Queries/repos:** `queries.sequencing.follow_ups_due(local_date, zone)` holds 4.9's logic and `sequencing.api._follow_ups_due` delegates to it. `scores.ready_candidates()` (via `queries.scoring`) filters tier, score, merged and stage in SQL. New `db/repos/scoring/capacity.py` (`by_date`, `upsert`, `add_picked`, `finalise_before` with SQL `max(0, �)`).
- **`app/scoring/selection.py`:** `Selection(clock, sequencing, compliance)`; `today()` (capacity 0 while no Sequence decodes), `ready_list()`, `@command record_capacity()`, `@use_case pick()`. `pick` writes nothing before the first `enroll`; after the loop it finalises earlier days, upserts today and adds `picked`. An undecodable Campaign Sequence gives `no_campaign`. `LeadScores(..., selection=)` calls `record_capacity` in its own transaction after re-tiering.
- **Web:** `web/ready_page.py`, `leads_ready.html` + `leads_ready_list.html` (the POST answers the list fragment, swapped by HTMX). Plain-English refusal texts live in `ready_page.REASON_TEXTS`. `area_context(shortfall=)` and the `signal shortfall: n` line link to `/leads/ready`. `leads.html` links to the page; nav unchanged. `app.css` gets `.ready-badge` and `.signal-shortfall`.
- **Wiring:** `main.py` and `tests/dashboard.py` build one `Selection` on the dashboard's `Compliance`; `create_app(selection=)` is required.
- **AD-16:** manifest entry `scoring.pick` with its scenario in `test_ad16_callers.py`.
- **Tests:** `tests/unit/test_ready_order.py`, `tests/unit/test_ready_page.py`, `tests/app/test_selection.py`, new cases in `tests/unit/test_sending_policy.py`. The `warm()` helper pins `ramp_target` to the level so the weekly ramp can't lift the cap.

## Spec Change Log

## Review Triage Log

Raw findings 23 (blind hunter 12, edge-case hunter 8, verification gap 3); 20 after merging duplicates. Fixed 4 (all patch), deferred 0, rejected 16.

| # | Source | Finding | Verdict | Route / evidence |
|---|---|---|---|---|
| 1 | VG | No test has `picked` above the recomputed capacity, so dropping the `max(0, �)` floor in `finalise_before` would pass and then break the CHECK the next day | medium | patch: test added (capacity falls below picks, next day finalises to 0, remaining 0) |
| 2 | VG; BH tests list | `today().reserved` is never checked under a non-UTC `sender_timezone` | medium | patch: test added (Los Angeles, Step 2 due 03:00 UTC next day counts) |
| 3 | VG | The `queued` stage of the list filter is never asserted | low | patch: one queued Lead added to `test_the_list_holds_only_ready_leads` |
| 4 | ECH | `_steps_of` lets `InvalidSequences` from `sequence_by_id` escape, so one undecodable Sequence turns the whole pick POST into a 500 (`_largest_sequence` already catches it) | low | patch: caught, gives `()` and so `no_campaign` |
| 5 | BH, ECH (merged) | `pick` enrolls a Lead the list wouldn't show (unscored, other stage) from a hand-made POST | low | rejected: the spec's pick reasons are fixed; `enroll` still runs every gate (stage, Eligibility, AD-16); a change would edit the approved spec |
| 6 | BH | Notification area computes capacity on every page with no fallback | low | rejected: `today()` already falls back on unreadable settings and Sequences; the reads are the same ones `enroll` relies on; single-operator scale |
| 7 | BH | "signal shortfall" shows unused capacity; the finalised column has no reader | low | rejected: Decision 3 and the AC define the line; the column is history for Epic 8 |
| 8 | BH, ECH (merged) | Header reads "0 of 0" without reason when steps is 0 or follow-ups use everything; warm-up text also shows for paused inboxes | low | rejected: the header texts are fixed by the spec |
| 9 | BH | Empty or >200 pick gives a bare 400 | low | rejected: the spec requires `MalformedForm` 400 for 0 or >200 |
| 10 | BH | `ready_list` is unbounded with several reads per Lead | low | rejected: single operator, small Lead counts; no named harm at this scale |
| 11 | BH, ECH (merged) | Shortfall can be stale; a `sender_timezone` change can move the day back and forth; `add_picked` may hit a finalised row | low | rejected: needs a mid-day timezone change; the spec defines finalisation from the last stored row |
| 12 | BH, ECH (merged) | A `record_capacity` failure fails `recompute_tiers` after its re-tiers committed | low | rejected: re-tiering is idempotent, a retry is harmless; the failure stays visible as a failed job |
| 13 | BH | `_sender_zone` fallback duplicated in selection and sequencing | low | rejected: both implement the same spec rule ("as `_local_date` does"); no caller diverges today |
| 14 | BH | `over_capacity` hides a lasting refusal; fallback to an inactive Campaign | low | rejected: the spec fixes the reason order and the Campaign choice |
| 15 | BH | Link inside the checkbox `<label>` is invalid HTML | false | `<a>` is not a labelable element, so the HTML content model allows it inside `<label>` |
| 16 | BH | Missing tests: steps 0, missing Sequence, no-Market Contact, shortfall line vanishing, `event_date` Signal, merged Lead name | low | rejected: each path is a one-line fallback already covered by its unit (`age_days`, `header_text`); the timezone item is #2 |
| 17 | BH | README omits steps-0, inactive-Campaign fallback, shortfall meaning, 200 limit | low | rejected: README states the shortfall line and picking; one Campaign per Segment x Market (`uq_campaigns_segment_id_market_id`) makes "first active" accurate |
| 18 | ECH | `enroll` raising LookupError/RuntimeError aborts the batch with a 500 | false | "no Sending Inbox to bind" is unreachable (empty list refused first); the gate RuntimeError is AD-16's intended whole-transaction abort |
| 19 | ECH | `reserved` drops as follow-ups are sent, so capacity rises during the day | low | rejected: Decision 5 already records it and logs it to `deferred-work.md` for Epic 6 |
| 20 | VG | Campaign preference ("first active, else first") untested | false | dropped by the reviewer itself: one Campaign per Segment x Market |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- pass
- `uv run pytest -q -n 2 tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
