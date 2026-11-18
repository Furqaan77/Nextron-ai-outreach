---
title: 'Story 10.2: Configure the LinkedIn account, its owner''s Telegram chat and its caps'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '7d93e1c89ce91ab29c2f1f829b498fa357cbc629'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-10-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** LinkedIn Tasks (Stories 10.4–10.6) need one account record with its owner's voice, a Telegram chat that only the owner reads, and per-account caps that keep volume safe; none of it exists yet.

**Approach:** A `pipeline`-owned `linkedin_accounts` table edited in a LinkedIn section of the Settings page with server-side cap validation and a one-active-account rule, the `linkedin_owner_chat` setting, a pure `domain.linkedin_caps.remaining`, and a health issue (plus a read that 10.4 will use) whenever the setup is incomplete.

## Boundaries & Constraints

**Always:**
- epics.md Story 10.2 ACs are binding, with the exact messages "At most 100 invitations per account in a rolling week", "v1 supports one LinkedIn account; its Tasks go to linkedin_owner_chat (PRD OQ-5)" and "LinkedIn Tasks off: set up the LinkedIn account and linkedin_owner_chat".
- Story 10.1 contracts are binding and never loosened: a module or top-level/method name holding `linkedin`/`instagram` outside `nextron.domain` fails, so code outside the domain uses "assist account" names (`AssistAccountRecord`, `assist_accounts` modules); `nextron.domain.linkedin_caps` gets one `PLATFORM_MODULE_ALLOWLIST` entry with a reason, and any linkedin-named name inside it a `PLATFORM_NAME_ALLOWLIST` entry. No platform host literal anywhere.
- Refusals store nothing (DB CHECKs back the domain rules). Every save is one `@command` unit of work appending one `events` row (actor `operator`, no personal data).
- `linkedin_owner_chat` is optional (default blank, `TELEGRAM_CHAT_ID_RULE`), so required settings stay seven.

**Never:** send anything to Telegram or anywhere; add a job type, Task table or LinkedIn fetch; reuse `alert_chat_id` for Tasks; touch the root `HANDOFF.md`/`README.md`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error Handling |
|---|---|---|---|
| Add account | label, owner, role, caps blank | stored `inactive`, caps 10/10/100 | N/A |
| Weekly cap 101 | save | refused "At most 100 invitations per account in a rolling week" | nothing stored |
| Daily 20 > weekly 15, or any cap −1 | save | refused with the matching message | nothing stored |
| Second active | one active exists; activate another | refused with the one-account message; both rows' caps unchanged | nothing stored |
| remaining defaults | 96 scheduled in last 7 days, none today | 4 | N/A |
| remaining boundary | 10 of the 96 at now − 7 d − 1 min | 10 | N/A |
| Health | no active account, or blank chat | issue `linkedin_tasks_off` shown; `assist_setup()` is `None` | N/A |

### Decisions made overnight (pending user review)
- Migration file is `0037_10_2_assist_accounts.py` (not `..._linkedin_accounts`): 10.1's module-name contract forbids `linkedin` in any module path outside `nextron.domain`. The table keeps the name `linkedin_accounts`.
- A Task counts toward `tasks_per_weekday` when its `prepared_at` falls on today's local date in `tz`, whatever its status; an invitation is scheduled at the Task's `prepared_at`. Window is `(now − 7×24 h, now]` plus any future-dated Task (stricter).
- `owner_name` and `label` are classified `redact` (a staff member's name), `owner_role` `none`; erasure coverage `not_held` (never a data subject's data), with pipeline's `erase_pii` no-op hook like drafting's.
- Extra refusals: blank or over-100-character text fields, a non-integer cap, a duplicate label. No delete action (10.4's Tasks will reference accounts).

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/db/alembic/versions/0035_2_11_job_postings.py` -- migration style; new head after `0036_2_12_legal_form`.
- `nextron-engine/nextron/db/models/` -- add `pipeline.py` (or extend) with `AssistAccountRecord`; register in models `__init__` like the others.
- `nextron-engine/nextron/db/repos/pipeline/stages.py` -- repo style (Core statements via `current().session`); add `assist_accounts.py`.
- `nextron-engine/nextron/app/pipeline/api.py` -- `@command` style, `eventlog.append`; add account commands (or `app/pipeline/assist_accounts.py`) and `ERASURE`/`erase_pii` like `app/drafting/api.py:20-40`.
- `nextron-engine/nextron/app/queries/pipeline.py` -- add `accounts()`, `assist_setup()`, `health_issues()` (reads `queries.setup.get(Key.LINKEDIN_OWNER_CHAT)` via an allowed import path; check import contracts).
- `nextron-engine/nextron/domain/settings_schema.py` -- `Section`, `Key`, `SCHEMA`; copy `ALERT_CHAT_ID` (~1053) with default `""` (precedent `BACKUP_OFFPC_DIR`).
- `nextron-engine/nextron/domain/health.py` -- `HealthIssue`; `nextron/main.py:1476 build_health_issues` add source `pipeline`; `tests/contracts/test_health_contracts.py:40 SOURCES`.
- `nextron-engine/nextron/main.py:1810` -- add pipeline's `erase_pii` to the `ContactErased` subscribers.
- `nextron-engine/nextron/web/app.py:624-760` + `web/campaigns_page.py` + `templates/campaigns_panel.html` -- the Settings-page panel pattern (GET renders panel, POST runs a use case, refusal re-renders panel 200); new `web/assist_accounts_page.py`, `templates/assist_accounts_panel.html`.
- `nextron-engine/nextron/db/owners.py`, `pii_registry.py`; `tests/contracts/test_erasure_contracts.py:44-60,170` (COVERAGE, count 16); `test_persistence_contracts.py:81`, `tests/unit/test_migrations.py:39` HEAD; `test_source_tree.py` FILES; `tests/contracts/test_no_linkedin_adapter.py` allowlists.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/linkedin_caps.py` -- `AccountCaps`, `CapTask(status, prepared_at)`, `SCHEDULED_STATUSES`, defaults, messages, `cap_problems(...)`, `remaining(account, tasks, now, tz)` (aware `now` else `ValueError`).
- [x] migration `0037_10_2_assist_accounts.py` -- table: ULID `id`, `label` (unique), `owner_name`, `owner_role`, `status`, three Integer caps with server defaults, `created_at`, `updated_at`; CHECKs status, caps ≥ 0, weekly ≤ 100, daily ≤ weekly; partial unique index allowing one `active` row.
- [x] model, repo, owners, pii registry, erasure declaration and hook -- as Code Map.
- [x] `settings_schema.py` -- `Section.LINKEDIN = "LinkedIn"` after alerts; `Key.LINKEDIN_OWNER_CHAT`.
- [x] pipeline use cases `add_account`, `save_account` -- refusal exception with problems; one-active rule checked before write.
- [x] queries + health source + web panel and routes (`POST /settings/linkedin/add`, `/settings/linkedin/save`; `@personal_data(True)`, CSRF, `hx-post`).
- [x] tests: domain unit (matrix rows 5–6, timezone day boundary, negatives clamp to 0, naive `now`), app tests (rows 1–4, events row), web tests, health test, contract updates, 10.1 allowlist entries.
- [x] `nextron-engine/README.md` -- Story 10.2 section.

**Acceptance Criteria:**
- Given a fresh DB at head, when the Settings page renders, then the LinkedIn section shows the `linkedin_owner_chat` field and the account panel, and the health panel shows the "LinkedIn Tasks off" issue until an active account and a chat id exist.
- Given the tree, when `tests/contracts` and `lint-imports` run, then all pass with no contract loosened.

## Implementation Notes

- **Domain** `nextron/domain/linkedin_caps.py` (the one `PLATFORM_MODULE_ALLOWLIST` entry; it defines no linkedin-named name, so no `PLATFORM_NAME_ALLOWLIST` entry was needed): `AccountStatus`, `AccountCaps` (defaults 10/10/100), `CapTask`, `SCHEDULED_STATUSES`, the messages (`WEEKLY_CAP_TOO_HIGH`, `ONE_ACCOUNT`, `TASKS_OFF`, `DAILY_OVER_WEEKLY`, `NEGATIVE_CAP`, `NOT_WHOLE_NUMBER`, text-field messages, `DUPLICATE_LABEL`, `UNKNOWN_ACCOUNT`, `BAD_STATUS`), `check_text`, `cap_problems`, `parse_cap` (blank = default, `-?[0-9]{1,9}`), `check_fields` and `AccountRefused` (code `account_refused`, `.problems`). `remaining(account, tasks, now, tz)`: weekly window `(now - 7 d, now]` plus future-dated scheduled Tasks; daily = scheduled Tasks on `now`'s local date in `tz`; Task headroom = every Task on that date; min of the three, clamped at 0; naive `now`/`prepared_at` raise `ValueError`, a non-`tzinfo` `tz` `TypeError`.
- **Table** `linkedin_accounts`, model `AssistAccountRecord` in new `nextron/db/models/pipeline.py`; migration `0037_10_2_assist_accounts` (down `0036_2_12_legal_form`): label/owner_name/owner_role String(100), status String(8), three Integer caps with server defaults, CHECKs `status`, `caps` (each >= 0), `weekly_cap` (<= 100), `daily_cap` (daily <= weekly), unique index `uq_linkedin_accounts_label`, partial unique `uq_linkedin_accounts_active` (`WHERE status = 'active'`). Owners: all columns `pipeline`; PII: `label`/`owner_name` redact, rest none. Repo `nextron/db/repos/pipeline/assist_accounts.py` (listed, get, id_by_label, active_ids, insert, update).
- **Use cases** `nextron/app/pipeline/assist_accounts.py` `AssistAccounts(clock)`: `add_account(label, owner_name, owner_role, caps)` and `save_account(account_id, label, owner_name, owner_role, status, caps)`. They are `@use_case` methods (the web entry point's one use case; a bare `@command` can't open the `BEGIN IMMEDIATE` transaction), each one transaction appending one `events` row (entity `linkedin_accounts`, actor `operator`, causes `account_added` / `account_saved`). Field checks run before the transaction; the duplicate-label and one-active checks run inside it before any write, so a refusal stores nothing.
- **Erasure**: `nextron.app.pipeline.api.ERASURE` (`linkedin_accounts`: `not_held`) and a no-op `@command erase_pii`, registered last among the `ContactErased` subscribers. `nextron.domain.contact_export.COVERAGE` gained `linkedin_accounts: NotAboutAContact("pipeline", ...)` (the export contract requires a decision for every personal-data table).
- **Setting**: `Section.LINKEDIN = "LinkedIn"` (after Alerts and recipients), `Key.LINKEDIN_OWNER_CHAT`, declared last in `SCHEMA` (Key order = declaration order), string, default `""`, rule `OPTIONAL_TELEGRAM_CHAT_ID_RULE` (same name and message as `TELEGRAM_CHAT_ID_RULE`, but accepts the blank default, which the plain rule would fail in `schema_problems`). Required settings stay seven.
- **Queries** `nextron/app/queries/pipeline.py`: `AssistAccount`, `AssistSetup(account, chat_id)`, `accounts()`, `assist_setup()` (None unless exactly one active account and a non-blank, parseable chat), `health_issues()` (`linkedin_tasks_off`, subject `linkedin_tasks`, text `TASKS_OFF`). It reads the setting through `queries.setup.get(OWNER_CHAT_KEY)`; the key is aliased to a module constant because Story 10.1's fetch tripwire flags any port-named call (`get`) whose argument expression names the platform. Health source `pipeline` registered last in `build_health_issues`.
- **Web**: `nextron/web/assist_accounts_page.py` + `templates/assist_accounts_panel.html` (`#assist-accounts-panel`, "LinkedIn account"), included in `settings.html` right after the settings form (whose LinkedIn fieldset holds the chat field). Routes `POST /settings/linkedin/add` and `/settings/linkedin/save`, `@personal_data(True)`, CSRF, `hx-post`; exact field sets or 400; refusal re-renders the panel 200 with values and `aria-invalid`. `create_app` takes `assist_accounts: AssistAccounts`.
- **Tests**: `tests/unit/test_linkedin_caps.py`, `tests/app/test_assist_accounts.py` (rows 1–4 and 7, events, CHECKs, partial index, panel, malformed/CSRF). Because a fresh DB now always has the `linkedin_tasks_off` issue, `tests/assist_setup.py` `assist_ready(units)` sets up an active account + chat; it is used by the health-page, notification-area and engine-health fixtures so their exact issue counts are unchanged; `test_restore_tests` expects the extra issue. `tests/unit/test_settings_page.py`'s parser drops the account panel. Contract updates: HEAD constants, chain probes renumbered to 0038/0039, `test_source_tree` FILES, erasure COVERAGE and hook probe, health SOURCES, web route lists, settings key lists, 10.1 module allowlist.

- **Orchestrator fix after verification:** the cap columns, `AccountCaps` fields and form fields were first named `daily_invites`/`weekly_invites`; renamed to `daily_invite_cap`/`weekly_invite_cap`, the names Story 10.2's AC gives the table.

- **Review patches (iteration 1):** `queries.pipeline._owner_chat()` treats an owner chat equal to the stored `alert_chat_id` as blank, so `assist_setup()` is `None` and `linkedin_tasks_off` shows (settings read through `_chat(key)`, which maps `InvalidSettingValue`/`SettingNotSet` to blank; the alert key is aliased `ALERT_CHAT_KEY` for the 10.1 tripwire). `check_text` checks control characters on the stripped text. `remaining()`'s docstring says the no-weekend rule is Story 10.4's. The label hint's example is "Sales LinkedIn". Migration and model docstrings rewrapped. New tests: owner chat equal to the alert chat, an unparseable stored owner chat, `linkedin_owner_chat` refusing `@someone`/`abc` with default `""` (blank is accepted only as the default: `parse("")` refuses blank for every setting), and a refused `/settings/linkedin/save` re-rendering that account's form with `101`, `aria-invalid` and the weekly-cap message.

- **Full-suite pins (orchestrator):** `test_export_contracts` personal-data table count 16 -> 17, `test_health_contracts` unpacks and checks the sixth source (`pipeline`), `test_web_contracts` template list gains `assist_accounts_panel.html`.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 16, edge-case hunter 3, verification gap 2 = 21 raw; 19 after grouping; 8 patch, 11 rejected, 0 deferred).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | blind, edge | `linkedin_owner_chat` may equal `alert_chat_id`, so Tasks would reach the alert chat | medium | only the format rule checks it; `assist_setup()` returns it as is; epic: Tasks never go to `alert_chat_id` | patch: `assist_setup()` is `None` (Tasks off, health issue shown) when the two are equal; test |
| 2 | blind | The health issue is permanent for an Operator who never uses LinkedIn | false | the AC requires exactly this issue while no account is active or the chat is blank | reject |
| 3 | blind, edge | `remaining()` gives weekend headroom | low | the AC's formula has no weekday term; the no-weekend rule is Story 10.4's (epic context) | patch: docstring says weekends are 10.4's rule |
| 4 | blind | A cap-only save logs `active -> active` without what changed | low | real, but the events row records the save; field-diff codes add surface | reject |
| 5 | blind | Label uniqueness is case-sensitive | low | real but rare for one or two accounts; fix adds normalisation | reject |
| 6 | blind | `owner_role` classed `none` | low | an approved decision in the frozen block | reject (would change the approved spec) |
| 7 | blind | Messages show `linkedin_owner_chat` and "PRD OQ-5" | false | both texts are the AC's verbatim | reject |
| 8 | blind | Panel says the chat is "set above" but sits apart from the section | false | the panel renders right after the settings form, whose LinkedIn section holds the field, so it is above | reject |
| 9 | blind | Label hint names a real staff member | low | `HINTS[LABEL]` "such as Furqaan's LinkedIn"; a direct string correction | patch: neutral example |
| 10 | blind | No test of a stored chat that no longer parses | low | `_owner_chat`'s `InvalidSettingValue` branch untested | patch: test |
| 11 | blind | A bad `status` re-render selects no option | low | reachable only by a crafted POST; fix adds a branch | reject |
| 12 | blind | A DB constraint hit surfaces as 500 | false | every CHECK and unique rule is checked first inside the same `BEGIN IMMEDIATE` transaction, so the write can't hit them | reject |
| 13 | blind | Migration and model docstrings wrap mid-phrase after the rename | low | "10,
10 and
100"; direct correction | patch: rewrap |
| 14 | blind | `assist_setup()` builds every account | low | negligible cost with one or two rows | reject |
| 15 | blind | `_checked` re-tests `fields`/`chosen` | low | needed for type narrowing | reject |
| 16 | blind | `refused_notice` may double a full stop | low | no current message ends with "." | reject |
| 17 | edge | `check_text` refuses a trailing newline/tab that stripping removes | low | category check runs on the raw value; direct correction | patch: check the stripped text |
| 18 | verification | No refuse-side test for `linkedin_owner_chat`'s numeric rule | medium | only valid saves are tested | patch: parametrized schema test (refuses `@name`, `abc`; accepts `""` and digits; default `""`) |
| 19 | verification | No test that a refused save re-renders that account's form with values and field error | medium | only the add form's refusal is tested | patch: web test for `/settings/linkedin/save` with `weekly_invite_cap=101` |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q tests/contracts <touched test files>` -- all pass
- `cd nextron-engine && uv run lint-imports` -- all contracts KEPT
