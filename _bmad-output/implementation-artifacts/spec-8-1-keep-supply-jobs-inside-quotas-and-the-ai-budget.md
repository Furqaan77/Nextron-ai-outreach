---
title: "Story 8.1: Keep supply jobs inside quotas and the AI budget"
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
baseline_commit: 'a44bcf28134e74eed969460160b6d511de643ea8'
review_loop_iteration: 0
story_key: '8-1-keep-supply-jobs-inside-quotas-and-the-ai-budget'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-8-3-turn-places-candidates-into-deduplicated-contactable-leads.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Job registrations don't say which work the budget may stop, so nothing proves that sending, replies and unsubscribe polling are never blocked. Two seams from 8.2–8.4 leak: concurrent Places calls can each pass the per-call check at 999/1,000, and a quota-refused discovery job writes an empty `deferred` run row every hour.

**Approach:** Add `budget_class` (default `essential`) to `JobType`, mark the supply types `blockable`, and prove the split with the contract test `test_only_supply_work_is_blockable`. Make the Places per-call check atomic, check the budget before a discovery run row is created, and add a "Discovery stopped" health issue.

## Boundaries & Constraints

**Always:**
- `JobType(..., budget_class=BudgetClass.ESSENTIAL)` is a validated field. A registration that leaves it out is `essential`.
- These types are `blockable`: `enrich_lead`, `render_pages`, `extract_lead`, `check_legal_form`, `check_job_boards`, `recheck_event`, `discover_places`, `discover_registry` and `run_ai_test_sets`. Every other registered type stays `essential`.
- Each blockable job re-checks `budget.allow` against a fresh `meter.snapshot` before every provider call. The existing per-call checks stay.
- A refusal returns `DEFERRED`, which uses the runner's existing 1 h re-queue.
- **Places calls are atomic.** One process-wide lock covers `PlaceSearches.page`: the check transaction, the request and its usage record. Every Places call in the engine goes through it, so two calls can never both pass at 999/1,000.
- `page()` raises `RuntimeError` when it is called inside a transaction.
- **Pre-check at run start.** `PlaceDiscovery._start` and `RegistryDiscovery._start` run the same budget or quota check before they insert a `discovery_runs` row. When the check refuses, they write no row, `run` returns `False` and the handler returns `DEFERRED`.
- **Discovery health issue.** `queries.discovery.health_issues(assessment)` is registered in `main.build_health_issues` as source `discovery`, after `sending`. It raises `discovery_stopped` with subject `<quota name>` for each quota whose `stops` is `discovery` and that is `reached`, with the text "Discovery stopped: <label> quota reached". It raises subject `ai_budget` with the text "Discovery stopped: AI budget reached" when the AI budget is reached. A quota at 0 (`off`) is never an issue.
- Only the Operator's Settings form changes a `quota_*` setting.

**Never:**
- No migration and no new dependency.
- Don't change the runner's defer rules or the 1.13 threshold notices.
- Don't build `recheck_lead`, `discover_aggregator` or any aggregator code (8.6, 8.7).
- Don't register types that only exist on main.

### Decisions made overnight (pending user review)
1. **`run_ai_test_sets` stays `blockable`,** as Story 4.5's decision 3 says ("a gate run is deferrable spend"). The contract test names it as the only allowed exception outside the discovery, enrichment and re-check groups. Making it `essential` would let AI Test Set runs spend past the $20 budget.
2. **`recheck_event` (group Re-check) is `blockable`,** because it runs `JobBoards`' blockable check.
3. **`BudgetClass` moves to the new `nextron/domain/budget_class.py`,** and `budget.py` re-exports it. This is needed because `budget → health → job` would otherwise become an import cycle.
4. **The atomic check is an in-process lock,** not a reservation. The instance lock already keeps the engine to one process. Hunter and Prospeo calls (main-only `find_contact_email` and `verify_contact_email`) need the same lock at merge; that item is logged in deferred-work.
5. **Every essential type needs a sample payload in the contract test.** A type without one fails the test with its name, so each merged type has to be added on purpose.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Paging at quota edge | `quota_places_enterprise` 1000 and 999 recorded; the fake offers 3 pages | Exactly one request is made and page 2 is never requested. The run ends `deferred` and the job returns `DEFERRED`. |
| Concurrent edge | Two threads call `page()` at 999/1000, and the transport is slow | One request is made and the other call is refused. |
| Refused at start | AI spend ≥ budget, or the quota is reached; then `discover_places` or `discover_registry` runs | No `discovery_runs` row is written, no request is made, and the handler returns `DEFERRED`. Run again an hour later, the job adds no row. |
| Pro or aggregator at 0 | A Pro-mask `PlaceSearches` with `quota_places_pro` 0; `allow(..., quota="job_aggregator")` at 0 | Refused before the adapter is called. |
| 50 deferrals | Quota reached, then 50 `discover_places` jobs are deferred through the queue and `Budget.check` runs after each | Exactly one `quota_reached` notification. |
| Essential at 150% | AI spend at 150% and every quota exhausted; every essential handler runs with its sample payload | No handler returns `DEFERRED`, and none calls `allow` with `BLOCKABLE`. |

</frozen-after-approval>

## Code Map

- `nextron/domain/job.py:535` `JobType`. Add the field after `group` and check that it is a `BudgetClass`. Update the docstring at about line 66.
- `nextron/domain/budget.py:116` `BudgetClass`. Move it to `domain/budget_class.py` and re-export it. Keep the existing `from nextron.domain.budget import BudgetClass` imports working.
- `nextron/main.py:1305` `build_job_types`. Add `budget_class=` to the 9 registrations above. Also set `app.state.job_types = job_types` near line 1252, for the contract test.
- `nextron/main.py:1614` `build_health_issues`. Register `IssueSource("discovery", lambda: discovery_queries.health_issues(budget.status()))`.
- `nextron/app/discovery/places.py:81` `page()`:
  - add a module lock around the whole method;
  - extract `refusal() -> str | None` (`quota`, `ai_budget` or `None`) for `_start` to reuse;
  - keep `search_page` and the log events.
- `nextron/app/discovery/place_ingest.py:317` `_start`. Run the pre-check (`place_searches.refusal()`) inside its transaction, before `runs_repo.insert`. A refusal returns a "deferred" sentinel, and `run` then returns `False`.
- `nextron/app/discovery/registry_ingest.py:280` `run`/`_start`, and `_may_continue` at line 356. Use the same pre-check with plain `allow(BLOCKABLE, ...)`.
- `nextron/app/queries/discovery.py`. Add `health_issues(assessment)`, using `domain.budget` `QUOTAS`, `QuotaState`, `AiState` and `HealthIssue`.
- `nextron/web/quotas_page.py:44`. It already shows "Non-essential work stopped: …"; only add a test for it.
- `nextron/app/ops/api.py:669` `Budget.check`. It already raises each threshold once a month; reuse it in the 50-deferral test.
- `tests/contracts/test_health_contracts.py:40` `SOURCES`. Append `"discovery"`, and update any other test that lists the sources.
- Tests to copy patterns from:
  - `tests/app/test_place_searches.py`: 999/1000 through a real `GooglePlaces` on a mock transport with the real `UsageMeter`;
  - `tests/app/test_place_coords_purge.py`: building job types;
  - `tests/fakes/__init__.py`: `PORT_FAKES`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/budget_class.py` (new), `budget.py`, `job.py` -- the field, its validation and the docstrings -- AC 1
- [x] `nextron/main.py` -- the registrations, `app.state.job_types`, and the health source -- AC 1, AC 6
- [x] `nextron/app/discovery/places.py`, `place_ingest.py`, `registry_ingest.py` -- the lock, `refusal()`, and the pre-check before the row insert -- matrix rows 1–3
- [x] `nextron/app/queries/discovery.py` -- `health_issues` -- AC 6
- [x] `tests/contracts/test_budget_classes.py` (new) -- `test_only_supply_work_is_blockable`:
  - (a) Every blockable type is in group DISCOVERY, ENRICHMENT or RECHECK, or in `ALLOWED_EXCEPTIONS = {"run_ai_test_sets": reason}`.
  - (b) `EXPECTED_BLOCKABLE` holds the whole AC list (`discover_places`, `discover_registry`, `discover_aggregator`, `recheck_lead`, `process_signal_item`, `enrich_lead`, `render_pages`, `extract_lead`, `check_job_boards`, `check_legal_form`, `find_contact_email`, `verify_contact_email`) plus `recheck_event` and `run_ai_test_sets`. Every one of those names that is registered must be blockable, and `poll_unsubscribes`, `daily_selection`, `recontact_due` and `send_alert` must be essential when present.
  - (c) The 150% essential run. Use `build_app` with `PORT_FAKES` on a temporary migrated DB. Spy on `allow` in `nextron.domain.budget` and in every loaded `nextron.*` module bound to it. Run each essential handler with `ESSENTIAL_SAMPLES[name]`; a missing sample fails the test with its name.
  - Add self-test proofs that the spy and the group check catch a planted violation.
- [x] `tests/app/test_supply_budget.py` (new) -- matrix rows 1–5, the quota panel's "Non-essential work stopped: Places Enterprise quota reached" text, the health issues (reached, AI, off, ok), last-run groups listing `discover_places`, `discover_registry` and `recheck_event`, and a `JobType` field default and validation unit test.
- [x] `tests/contracts/test_quota_settings.py` (new) -- AST: `Key.QUOTA_*` and `"quota_…"` setting literals appear only in `domain/settings_schema.py` and `domain/budget.py`, with a planted-violation proof.
- [x] Contract and source-tree upkeep -- `test_source_tree` `FILES`, `test_health_contracts` `SOURCES`, and the module docstrings in `main.py` and `handlers.py`.
- [x] `nextron-engine/README.md` -- a Story 8.1 section.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the 1.13, 8.2 and 8.3 items closed by 8.1, and add the Hunter/Prospeo lock item.

**Acceptance Criteria:**
- Given the registry, when it is built, then each type has a `budget_class`, exactly the listed types are blockable, and a `JobType` built without the field is `essential`.
- Given quota settings, when the codebase is scanned, then no code path outside the schema and the budget spec names a quota setting.
- Given a reached discovery quota or the AI budget, when the health panel is read, then it lists "Discovery stopped: …" and the Discovery and Re-check groups' last successful runs.

## Implementation Notes

Built as specified (no migration, no dependency). Implementer choices and flags for review:

- **`BudgetClass`** moved to `nextron/domain/budget_class.py` (stdlib only); `nextron.domain.budget`
  re-exports it (`from ... import BudgetClass as BudgetClass`), so every existing import works.
  `JobType.budget_class` (after `group`, default `ESSENTIAL`) raises `TypeError` naming the type
  for anything that isn't a `BudgetClass` (a plain `"blockable"` string included).
- **Registrations.** `main.build_job_types` passes `budget_class=BudgetClass.BLOCKABLE` to the nine
  listed types; `build_app` sets `app.state.job_types`. `build_health_issues` registers
  `IssueSource("discovery", lambda: discovery_queries.health_issues(budget.status()))` after
  `sending`. Docstrings updated in `main.py`, `handlers.py`, `domain/job.py`, `domain/budget.py`.
- **Places lock.** `nextron.app.discovery.places.PLACES_LOCK` (a module `threading.Lock`) is held
  for the whole of `PlaceSearches.page`: the check transaction, `text_search` and the adapter's
  usage record (the adapter meters before returning). `page()` raises `RuntimeError` first when
  `in_transaction()`. `refusal() -> "quota" | "ai_budget" | None` reads inside the caller's open
  transaction; `page` and `PlaceDiscovery._start` share it. A mutation check (lock replaced by
  `nullcontext`) makes the concurrent test fail.
- **Pre-check at start.** `PlaceDiscovery._start` calls `refusal()` and `RegistryDiscovery._start`
  calls the new `_budget_allows()` (also used by `_may_continue`), inside the start transaction,
  after the stale-row recovery and only when the Campaign may run (a Campaign that can't run still
  writes its `stopped` row, as in 8.3/8.4: no spend is involved). A refusal logs
  `places_run_deferred` (`reason`) / `registry_run_deferred` (`campaign_id`) and `_start` returns
  `RunStatus.DEFERRED`; `run` returns `False` with no row.
- **Health issues.** `queries.discovery.health_issues(assessment)` is pure: AI issue first
  (subject `ai_budget`), then each `stops == "discovery"` quota in `REACHED` state in `QUOTAS`
  order (subject = quota name), kind `discovery_stopped`. `off` quotas and enrichment quotas
  (Hunter, Prospeo) are never listed. The existing `budget` source's `quota_reached` /
  `ai_budget_reached` issues stay, so a reached Places quota shows both lines.
- **`recheck_event` is `blockable` but its use case has no budget check** (it makes no metered
  or AI call; its handler never returns `DEFERRED`). I did not add one: blocking it under the
  budget would leave an Event that closed unrechecked, and nothing in the spec's matrix asks for
  it. Flag for review against "each blockable job re-checks `budget.allow` ... before every
  provider call".
- **Contract test (`tests/contracts/test_budget_classes.py`).** (a)+(b) via `class_problems`
  (blockable outside the three groups and `ALLOWED_EXCEPTIONS`; blockable but not in
  `EXPECTED_BLOCKABLE`; an expected type registered essential; a `MUST_BE_ESSENTIAL` type
  blockable), plus the exact registered blockable set; four planted-violation cases. (c)
  `build_app` with every port faked on the `units` fixture's migrated DB, AI spend 150% and every
  quota at 1,000,000 units recorded through a real `UsageMeter`; the `allow` spy replaces the
  function in `nextron.domain.budget` and every loaded `nextron.*` module bound to it; the ten
  essential types run their `ESSENTIAL_SAMPLES` (`backup_db` uses `kind: manual`, data dir in
  `tmp_path`); a spy self-test proves a module-bound `allow` call is recorded.
- **Tests.** `tests/app/test_supply_budget.py` (21): matrix rows 1-5 (row 1 and row 3 on
  `tests/app/test_place_discovery.py`'s and `test_registry_discovery.py`'s worlds, imported as
  modules; row 2 on a real `GooglePlaces`/`MockTransport` and the real `UsageMeter` in two
  threads; row 5 claims, handles and defers through the `JobQueue` 50 times with `Budget.check`
  after each), the Quotas page line, the health issues (reached, AI, enrichment/off/ok) and the
  registry's `discovery` source, the Discovery/Re-check last-run groups, `page()` inside a
  transaction, and the `JobType` field. `tests/contracts/test_quota_settings.py` (AST, with a
  planted module). Updated: `test_health_contracts` (`SOURCES`, the registry test reads the
  `discovery` source and renamed from "five owner sources"), `test_source_tree` `FILES`,
  `test_place_discovery::test_the_handler_runs_the_queued_job_and_defers_on_a_refusal` (a run
  refused at its start now writes no row).
- **Docs.** `nextron-engine/README.md`: a "Supply jobs inside the quotas and the AI budget (Story
  8.1)" subsection under Metering, plus the Places/Companies House deferral text and the health
  sources list. `deferred-work.md`: the 1.13, 8.2 and 8.3 items marked resolved (8.2's
  `LegalFormChecks` note: no quota, only the AI budget, so no lock), and the Hunter/Prospeo lock
  item added.
- **Review patches.** The `run` docstrings of `PlaceDiscovery` and `RegistryDiscovery` now say
  `False` also means the budget refused the run at its start (no row written) (#4).
  `places_run_deferred` logs `campaign_id` and `reason`; `registry_run_deferred` logs
  `campaign_id` and `reason` (`ai_budget`, `places.REASON_AI_BUDGET`) (#5). In
  `test_supply_budget`, the two-calls test swaps `PLACES_LOCK` for a spy that flags the second
  thread's acquire attempt, waits (30 s bound) until that flag is set while the first request
  holds the lock, then releases. With the lock removed the test fails (#9). The aggregator test
  now passes `quota="job_aggregator"` on the essential assertion too, and the Pro test asserts
  `quota_places_pro` is 0 first (#10). A new parametrised test covers a paused Campaign with the
  budget refused (Places with the AI budget and with the quota; registry with the AI budget):
  `run()` is `True` and exactly one `stopped` row is written (#16).
  `test_budget_classes.spy_on_allow` patches every `nextron.*` module attribute that `is` the
  real `budget.allow`, whatever its name. The new self-test
  `test_the_spy_catches_an_aliased_import` plants an aliased binding (#14).

- **Gate fix.** The first full-suite gate failed `test_restore_tests::test_the_health_report_shows_the_last_restore_test`:
  its `NoBudget` fake had no `status()`, so the new `discovery` source raised a `health_check_failed` issue. The
  `NoBudget` fakes in `test_restore_tests.py`, `test_backups.py` and `test_recovery.py` now return an assessment with
  nothing reached. At merge, any other fake `Budget` passed to `build_health_issues` needs a `status()` too.

## Spec Change Log

## Review Triage Log

Review pass 1: 3 layers (blind, edge, verification gap), 21 raw findings, 17 after dedup; 6 patched, 0 deferred, 11 rejected (1 false).

| # | Layer | Finding | Verdict | Route |
|---|---|---|---|---|
| 1 | blind | each stop now shows twice: budget `quota_reached` plus `discovery_stopped` | low | reject: the AC requires the "Discovery stopped" issue, and the budget lines date from 1.13 |
| 2 | blind | "Discovery stopped: AI budget reached" doesn't mention enrichment or re-checks | low | reject: the AC wording names this exact text |
| 3 | blind | production code never reads `budget_class`, and nothing proves blockable handlers check per call | low | reject: the AC defines the class as a declaration plus a contract, and the per-call checks are existing tested code |
| 4 | blind | the `PlaceDiscovery.run` and `RegistryDiscovery.run` docstrings don't mention the start-of-run refusal | low | patch |
| 5 | blind | `places_run_deferred` lacks `campaign_id`; `registry_run_deferred` lacks `reason` | low | patch |
| 6 | blind, edge | the registry start check ignores the Places quota, so lookups get refused one by one | low | reject: Companies House discovery works without Places (8.4 leaves `website_lookup_at` NULL and keeps the Operator form); the retry of undecided lookups is already deferred (8.4 #12) |
| 7 | blind | `PLACES_LOCK` has no acquire timeout | low | reject: the adapter's HTTP timeout bounds the hold, and `with` releases on exceptions |
| 8 | blind | `_start` returns a three-way union with a `RunStatus` sentinel | low | reject: cosmetic; only `DEFERRED` is ever returned |
| 9 | blind, verif, edge | the two-thread test relies on a 0.2 s join, so it could pass without the lock under load | medium | patch: add a deterministic signal that thread two reached the lock while thread one held it |
| 10 | blind | the aggregator test's essential assert passes no `quota=`; the Pro test assumes the default of 0 | low | patch |
| 11 | blind | the health-group test only checks `LastRun(name, None)` | low | reject: 1.15's tests cover the last-success mechanics; this test checks only group membership |
| 12 | blind | `DISCOVERY_STOPS` repeats the `"discovery"` literal | low | reject: no named harm; the health tests would catch a change |
| 13 | blind | the contract breaks at merge without main's samples and blockable types | low | reject: by design (Decision 5); listed in the handback report for the merge |
| 14 | blind, edge | the spy misses `allow` imported under an alias | low | patch: patch every module attribute that is identical to `budget.allow` |
| 15 | blind | the spec is missing from the review diff | false | the spec was withheld on purpose as the claims file |
| 16 | verif | no test covers an inactive Campaign while the budget is exhausted (it should end `stopped`, not defer) | medium | patch: add Places and registry cases |
| 17 | edge | the quota-setting AST scan misses f-strings, `getattr` and aliased `Key` | low | reject: unlikely patterns; the scan covers the attribute and literal forms the codebase uses |

## Verification

**Commands:**
- `cd e:/Nextron-epic1/nextron-engine && uv run pytest -q -n 2 tests/contracts tests/app/test_supply_budget.py tests/app/test_place_searches.py tests/app/test_place_discovery*.py tests/app/test_registry*.py tests/unit/test_job*.py` -- expected: pass
- `uv run lint-imports` -- expected: every contract kept
