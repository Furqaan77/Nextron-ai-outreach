---
title: "Story 6.4: Record warm-up and ramp each inbox's Daily Cap under a total ceiling"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '2bc36d85b44b2a32081567988717ab1d32cf63c6'
review_loop_iteration: 0
story_key: '6-4-record-warm-up-and-ramp-each-inbox-s-daily-cap-under-a-total'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A new Sending Inbox must not send cold email before 14 days of recorded warm-up, or ramp faster than about 5 a week to 30 a day, and warm-up tool sends must count toward a total daily ceiling (FR-33, FR-34, FR-19, NFR-22, AD-11). Today nothing records warm-up, and no cold Daily Cap exists for the send gate (6.9) or Epic 4's capacity (4.8, 4.10) to read.

**Approach:** Add warm-up and ramp columns to `sending_inboxes`, a `total_daily_ceiling` setting, a pure `nextron/domain/sending_policy.py` (`daily_cap`, the tool-send figure, the ceiling-limited cold cap, `capacity_forecast`, local-date helpers), a `Sending.record_warmup` command, `queries.sending.cold_daily_caps(local_date)`, and a warm-up form plus cap facts per inbox in the Sending Inboxes panel.

## Boundaries & Constraints

**Always:**
- **Migration** `0037_6_4_inbox_warmup` (down `0036_2_12_legal_form`) adds to `sending_inboxes`: `warmup_started_on` (date, NULL), `warmup_method` (text, NULL: `manual` or a tool name), `warm_level` (int, NULL), `warmup_tool_daily` (int NOT NULL default 0), `ramp_step` (int NOT NULL default 5), `ramp_target` (int NOT NULL default 30). CHECKs: the three warm-up record columns are all NULL or all set; ranges as below. Every column in `owners.py` (sending) and `pii_registry.py` (class `none`).
- **Ranges** (`domain` constants, used by the form, the command and the CHECKs): `warm_level` 0–100, `warmup_tool_daily` 0–100, `ramp_step` 1–10, `ramp_target` 1–50; `warmup_method` is `manual` (any case, stored lowercase) or a tool name of 1–40 characters on one line (the label rule); `warmup_started_on` an ISO date not after today in `sender_timezone`.
- **Setting** `total_daily_ceiling`: integer, Section `Sending`, default 50, 0–100.
- **`domain.sending_policy`** (stdlib + `nextron.domain` only, pure, no clock): `WarmupPlan(warmup_started_on, warm_level, warmup_tool_daily, ramp_step, ramp_target)`; `local_date(now, tz_name)`; `warmup_days(plan, local_date)` (`(local_date − started_on).days`, `None` when unrecorded); `daily_cap(plan, local_date)` = 0 when unrecorded or under 14 days, else `min(target, min(warm_level, target) + step × ((days − 14) // 7))`; `tool_send_figure(plan, other_sent=0)` = `max(warmup_tool_daily, other_sent)` (6.8 passes `other_sent`); `cold_cap(plan, local_date, ceiling)` = `max(0, min(daily_cap, ceiling − tool_send_figure))`; `capacity_forecast(plan, local_date, *, ceiling, follow_ups_due=0)` = `max(0, cold_cap − follow_ups_due)`.
- **`Sending.record_warmup(inbox_id, form values…)`** `@use_case`: validates (needs `sender_timezone`; unset → refusal `timezone_unset`), refuses an unknown or `removed` inbox, updates the six columns in one `BEGIN IMMEDIATE` transaction, logs `sending_warmup_recorded` (inbox id only). Refusals raise a typed `WarmupRefused(refusal)` with a fixed message naming the field, never the value.
- **`queries.sending.cold_daily_caps(local_date) -> dict[str, int]`**: every listed (non-`removed`) inbox id, oldest first → `cold_cap(plan, local_date, ceiling)` for `active` inboxes, 0 for any other status. It reads the ceiling through `queries.setup.get(Key.TOTAL_DAILY_CEILING)`. Also `inbox_caps(local_date)` views for the panel.
- **Panel:** each inbox row gets its warm-up facts (Recorded warm-up: N days / Not recorded; Today's cap; Cap used; Ceiling) and a form (`method="post"`, `hx-post="/inboxes/warmup"`, `{{ csrf_field() }}`, hidden `inbox_id`, the six fields prefilled from storage). `POST /inboxes/warmup` (`@personal_data(True)`) returns the whole `inboxes_panel.html` with `Warm-up saved.` or the refusal (200, `role="alert"`, typed values kept for that inbox). A malformed form is 400.
- A contract test proves no code path sends warm-up exchanges: no job kind, trigger, port method or module under `nextron/` that runs or sends warm-up mail.

**Never:** send any email; read or write another owner's repositories; add a gate predicate (6.9 does); count sends (no `outbound_messages` exists yet); store secrets.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ramp | start 2026-11-01, level 12, step 5, target 30 | cap 0 on 11-14, 12 on 11-15, 17 on 11-22, 22 on 11-29, 27 on 12-06, 30 on 12-13 and after | N/A |
| Warm above target | level 40, target 30, day 14 | 30 | N/A |
| Unrecorded | no start date | `daily_cap` 0, days `None` | N/A |
| Ceiling | cap 30, tool 25, ceiling 50, `active` | `cold_daily_caps` 25 | N/A |
| Ceiling below tool | tool 60 > ceiling 50 (or ceiling 0) | 0, never negative | N/A |
| Not active | `connecting` inbox, fully warmed | 0 | N/A |
| Day boundary | 2026-11-14T23:30Z, tz `Asia/Kolkata` | local date 2026-11-15 | N/A |
| Future start | start tomorrow in `sender_timezone` | refused, nothing stored | `WarmupRefused(future_start)` |
| Bad level | warm level 101 or "abc" | refused | `WarmupRefused(invalid_warm_level)` |
| Forecast | cap 30, follow-ups 35 | 0 | N/A |

</frozen-after-approval>

## Code Map

- `nextron/domain/sending_inbox.py` -- add the warm-up refusals, ranges and `check_warmup(...)` (text → typed values); reuse `check_label`'s character rule for the method.
- `nextron/domain/sending_policy.py` (new) -- the pure policy above; model `ZoneInfo` use on `domain/backup.py:249`.
- `nextron/domain/settings_schema.py` -- `Key.TOTAL_DAILY_CEILING` and its `Setting` (mirror `UNSUB_ACTIVE_KID`, Section.SENDING); update `tests/contracts/test_settings_contracts.py` and any test listing every key.
- `nextron/db/models/sending.py`, `db/alembic/versions/0037_6_4_inbox_warmup.py` (mirror `0036_2_12_legal_form.py` for adding columns with CHECKs), `db/owners.py`, `db/pii_registry.py`; `Mapped[date]` with `Date` as `db/models/playbook.py:427`.
- `nextron/db/repos/sending/inboxes.py` -- `StoredInbox` gains the six fields; add `update_warmup(inbox_id, ...)` and `status_of(inbox_id)`.
- `nextron/app/sending/api.py` -- `record_warmup`; `nextron/app/queries/sending.py` -- `SendingInboxView` gains the warm-up fields, `cold_daily_caps`, `inbox_caps`; update both docstrings (the queries one notes its `queries.setup` read).
- `nextron/web/inboxes_page.py`, `templates/inboxes_panel.html`, `nextron/web/app.py` (route after `inbox_register`, docstring route list), `static/app.css` (byte-preserving only), `tests/contracts/test_web_contracts.py`.
- Migration bookkeeping (track rules §3): `HEAD` in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`; chain probes; `test_source_tree.FILES`.
- Tests to extend: `tests/unit/test_sending_inbox.py`, `tests/app/test_sending_inboxes.py`, `tests/unit/test_inboxes_page.py`; new `tests/unit/test_sending_policy.py`, `tests/contracts/test_warmup_contracts.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/sending_policy.py`, `nextron/domain/sending_inbox.py`, `nextron/domain/settings_schema.py` -- the policy, validation and setting.
- [x] `nextron/db/models/sending.py`, migration, `owners.py`, `pii_registry.py`, `db/repos/sending/inboxes.py` -- storage.
- [x] `nextron/app/sending/api.py`, `nextron/app/queries/sending.py` -- command and reads.
- [x] `nextron/web/inboxes_page.py`, `inboxes_panel.html`, `web/app.py`, `app.css` -- the panel and route.
- [x] Tests: every I/O row; the 6.4 AC examples (13 days → 0, day 14 → 12, 30/25/50 → 25); validation edges (today allowed, tomorrow refused, 0 and 100, 101, blank, non-integer); `record_warmup` stores and refuses with nothing stored; CHECKs raise `IntegrityError`; route success, refusal, 400s, 403 without CSRF, escaping; the warm-up contract test.
- [x] `nextron-engine/README.md` -- a "Warm-up and Daily Caps" section and the migrations row.

**Acceptance Criteria:**
- Given the full suite through the suite lock and `uv run lint-imports`, then everything passes and every contract is kept.
- Given the persistence contracts, then every new column is mapped and classified and the migration matches the model.

## Implementation Notes

- **Migration** `0037_6_4_inbox_warmup` (down `0036_2_12_legal_form`) rebuilds `sending_inboxes`
  with `batch_alter_table(recreate="always", copy_from=<0018's exact table>)`, adds the six
  columns (`warmup_started_on` DATE, `warmup_method` VARCHAR(40), `warm_level` INTEGER, all
  nullable; `warmup_tool_daily`/`ramp_step`/`ramp_target` INTEGER NOT NULL with server defaults
  0/5/30) and two CHECKs: `ck_sending_inboxes_warmup_record` (all three record columns NULL or
  all set) and `ck_sending_inboxes_warmup_ranges` (method length 1-40, level 0-100, tool 0-100,
  step 1-10, target 1-50). Model `SendingInboxRecord` mirrors it, building the range CHECK from
  the `sending_policy` constants. `owners.py` (sending) and `pii_registry.py` (`none`) cover all
  six. HEAD constants, the chain probes (now `0038_*`/`0039_*`) and `test_source_tree.FILES`
  are bumped.
- **`nextron/domain/sending_policy.py`** (new, stdlib only): the range and default constants,
  `WarmupPlan` (with `.recorded`), `local_date`, `warmup_days`, `daily_cap`, `tool_send_figure`,
  `cold_cap(plan, local_date, ceiling, other_sent=0)` (the optional `other_sent` is for 6.8),
  `capacity_forecast`.
- **`nextron/domain/sending_inbox.py`**: `WarmupRefusal` (`timezone_unset`, `unknown_inbox`,
  `inbox_removed`, `invalid_start`, `future_start`, `invalid_method`, `invalid_warm_level`,
  `invalid_tool_daily`, `invalid_ramp_step`, `invalid_ramp_target`), `WARMUP_MESSAGES` (fixed,
  name the field), `WarmupRefused` (code `warmup_refused`), `WarmupRecord`,
  `check_warmup(started_on, method, warm_level, tool_daily, ramp_step, ramp_target, *, today)`;
  `check_label`'s character rule is factored into `_one_line(text, max_length)` and reused for
  the method (`MAX_METHOD_LENGTH` 40). Integers are ASCII digits only (`[0-9]{1,6}`); the date
  must match `YYYY-MM-DD` before `date.fromisoformat`.
- **Setting** `Key.TOTAL_DAILY_CEILING` (`total_daily_ceiling`, integer, Section Sending, label
  "Total daily ceiling per inbox", default 50, rules 0-100), declared last in `SCHEMA`.
- **Repo** `repos.sending.inboxes`: `StoredInbox` gains the six fields; new `status_of` and
  `update_warmup`.
- **`Sending.record_warmup`** (`@use_case`, one transaction): reads `sender_timezone`
  (`SettingNotSet`/`InvalidSettingValue` -> `timezone_unset`), computes today with
  `local_date(clock.now(), tz)`, runs `check_warmup`, then the inbox check (non-ULID or missing
  -> `unknown_inbox`, `removed` -> `inbox_removed`), then `update_warmup`. Logs
  `sending_warmup_recorded` (`inbox_id`) or `sending_warmup_refused` (`refusal`, plus `inbox_id` when it is a ULID). No `events`
  row. **`Sending.inbox_caps() -> InboxCaps(local_date, caps)`**: a plain read for the panel
  (today in `sender_timezone`, UTC when unset or unreadable).
- **`queries.sending`**: `SendingInboxView` gains the six fields and a `.plan` property;
  `ceiling()` (through `queries.setup.get`; an unparseable stored value reads as 0),
  `cold_daily_caps(local_date) -> dict[str, int]` (listed inboxes oldest first; `cold_cap` for
  `active`, 0 otherwise), `inbox_caps(local_date) -> tuple[InboxCapView, ...]`
  (`inbox_id, warmup_days, daily_cap, tool_sends, cold_cap, cap_used=0, ceiling`). Docstring
  notes the `queries.setup` read.
- **Web**: `inboxes_panel.html` adds, after the inbox table, one
  `<section class="inbox-warmup">` per listed inbox: heading "Warm-up: <label>", a `<dl
  class="warmup-facts">` (Recorded warm-up / Today's cap / Cap used / Ceiling) and the warm-up
  form (`method="post"`, `hx-post="/inboxes/warmup"`, `hx-target="#inboxes-panel"`,
  `hx-swap="outerHTML"`, `hx-sync="this:drop"`, `{{ csrf_field() }}`, hidden `inbox_id`, six
  text fields prefilled from storage). The table itself is unchanged. `inboxes_page.py` adds
  `WarmupForm`, `stored_warmup`, `warmup_facts`, `submitted_warmup` (exact fields, each once,
  ULID `inbox_id`, else `MalformedForm` 400), `warmup_saved`; `InboxRow` gains `id`, `facts`
  and `warmup`; `panel_context` takes `caps` and `typed_warmup`. `web/app.py` adds
  `POST /inboxes/warmup` (`inbox_warmup`, `@personal_data(True)`) after `inbox_register`;
  `inboxes_answer` passes `sending.inbox_caps().caps`. `app.css` gained the
  `.inbox-warmup`/`.warmup-facts` rules (byte-preserving append in the LF region).
- **Contract** `tests/contracts/test_warmup_contracts.py`: no registered job type or `Trigger`
  (declared or registered) mentions `warm`; no name (module, class, def, arg, attribute,
  import) in `nextron/ports`, `nextron/adapters`, `nextron/jobs` or `main.py` mentions `warm`;
  no file under `nextron/` is named for warm-up except migrations; every function under
  `nextron/` whose name mentions `warm` is in `ALLOWED_WARM_FUNCTIONS` (record/read/check only);
  plus a probe proving the checks catch violations.
- **Tests**: new `tests/unit/test_sending_policy.py`; `check_warmup` cases in
  `tests/unit/test_sending_inbox.py`; warm-up and caps in `tests/app/test_sending_inboxes.py`;
  the warm-up section and route in `tests/unit/test_inboxes_page.py` (its `Panel` parser now
  reads `<dl>` facts and its `value()` reads the registration form only); a 0037 upgrade test
  in `tests/unit/test_migrations.py`; the new setting in `test_settings_schema.py`,
  `test_settings_contracts.py`, `test_settings_page.py` and `test_telegram_secrecy.py`; the
  route in `test_web_contracts.py`.
- **README**: "Warm-up and Daily Caps" section under Sending Inboxes, and the `0037` migrations
  row.

## Spec Change Log

## Review Triage Log

Pass 1: 3 reviewers (blind hunter BH1-BH14, edge-case hunter EC1-EC6, verification gap VG1 + 2 others). Raw 22; deduped 18 (BH1=EC1, BH14=EC5=VG-o2, BH10=VG-o1).

| ID | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| VG1 | No test for a stored `sender_timezone` that no longer parses (`inbox_caps` UTC fallback, `record_warmup` refusal) | medium | Filed pre-verified; only the never-set case is tested; dropping `InvalidSettingValue` from either except would 500 the panel unnoticed | patch |
| BH1/EC1 | A start date far in the past (typo) or backdated skips real warm-up, with no audit row | low | Real, but the Operator's own record is the intent's source of truth; inboxes may warm before registration so no `created_at` bound is right; the panel shows the day count at once; spec decision says no events row | reject (spec decision) |
| EC2 | `warmup_days` negative after the timezone moves west on the start day | low | Real but needs a same-day timezone change; cap stays 0 | reject (unlikely) |
| EC3/EC4 | Negative `follow_ups_due`, `ceiling` or `other_sent` | false | No caller passes them; the ceiling setting is 0-100 and the count inputs are non-negative by construction | reject |
| BH14/EC5/VG-o2 | `update_warmup` return value ignored | false | `status_of` and the update run in one `BEGIN IMMEDIATE` transaction, so 0 rows can't follow a found status | reject |
| EC6 | Ranges allow a cap of 50 on day 14 | false | FR-33 says the ramp is editable; the ranges are the recorded overnight decision and the ceiling still applies | reject |
| BH2 | `capacity_forecast` and `cold_daily_caps` have no production caller | false | Stories 4.8, 4.10 and 6.9 (the callers) are backlog; the spec builds them as pure reads for those stories | reject |
| BH3 | The no-warm-up contract will flag read-only names in `jobs/` or `main.py` | low | Speculative; the gate (6.9) lives in domain and app, not jobs; a later story extends the allowlist deliberately | reject |
| BH4 | README overstates the name-based no-warm-up guarantee | low | Direct wording correction | patch |
| BH5 | An unreadable ceiling silently zeroes caps with no log | low | Fail-closed by design; rare; a log or health issue adds new surface | reject |
| BH6 | Panel doesn't say when cold sending starts | low | UX nicety beyond the AC's facts (days, today's cap, cap used, ceiling) | reject |
| BH7 | Warm level above target clipped silently | low | The I/O matrix specifies that behaviour | reject |
| BH8 | Template range hints hard-coded | low | Drift needs a constant change; fix adds context plumbing | reject |
| BH9 | No test that the model CHECK text equals the migration's | low | `test_migrations` exercises the migrated CHECKs by inserting out-of-range rows; drift needs a constant change | reject |
| BH10/VG-o1 | Stale "Story 6.2 alters these tables ... warm-up" docstring in `db/models/sending.py` | low | Direct correction | patch |
| BH11 | `sending_warmup_refused` log lacks the inbox id | low | Direct addition of a non-personal id, like the success line | patch |
| BH12 | "Cap used 0" shown as fact | false | No send path exists before 6.10, so 0 is true; deferred-work entry covers counting | reject |
| BH13 | Panel silently uses UTC when the timezone is unset | low | `sender_timezone` is a required setting flagged by doctor; display only; saving refuses | reject |

## Design Notes

### Decisions made overnight (pending user review)
- The ceiling is per inbox (cold + its warm-up tool sends), as the AC's `cold_daily_caps` formula states.
- `cold_daily_caps` keeps the AC's one-argument signature, so `queries.sending` reads the ceiling through `queries.setup`; the spine's import table lists only domain and own repos for a queries module, but no lint contract forbids it and the alternative changes the AC's signature.
- Ranges not given by the planning docs: ramp step 1–10, ramp target 1–50, tool daily 0–100, ceiling 0–100, tool name 1–40 characters.
- "Cap used" shows 0 until Story 6.10 creates sent messages to count; Epic 4 (4.8, 4.10) is not built, so there is no placeholder to delete and `capacity_forecast` takes `follow_ups_due` as an input for 4.8.
- Warm-up days and cap use on the Health panel (UX-DR29) are deferred to the health story; this story covers the inbox panel.
- Recording warm-up appends no `events` row: it changes no status.
- Blank start date, method and warm level together clear the warm-up record (stored NULL, cap 0,
  the safe direction); one of the three blank while another is filled is that field's refusal.
- Refusal codes beyond those the spec names: `invalid_start`, `invalid_method`,
  `invalid_tool_daily`, `invalid_ramp_step`, `invalid_ramp_target`, `unknown_inbox` (also for an
  id that isn't a ULID) and `inbox_removed`. Field checks run before the inbox check.
- An unparseable stored `total_daily_ceiling` reads as 0 (cold sends stop rather than exceed it).
- The panel's "today" uses `sender_timezone`, or UTC when it is unset or unreadable (display
  only; `record_warmup` refuses without a timezone).
- The warm-up facts and form sit in a per-inbox section under the inbox table rather than as
  extra table columns, so the table and the registration form are unchanged.
- `cold_cap` takes an optional `other_sent=0` so Story 6.8 can pass the synced count without a
  signature change.

## Verification

**Commands:**
- `uv run pytest -q tests/unit/test_sending_policy.py tests/unit/test_sending_inbox.py tests/app/test_sending_inboxes.py tests/unit/test_inboxes_page.py tests/contracts` -- all pass
- `uv run lint-imports` -- every contract kept
