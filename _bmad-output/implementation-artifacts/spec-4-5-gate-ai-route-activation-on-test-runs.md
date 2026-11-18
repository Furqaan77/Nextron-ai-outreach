---
title: 'Story 4.5: Gate AI route activation on test runs'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '429194c067c1e1dd4e71d641ad88e21ba3a46b86'
review_loop_iteration: 0
story_key: '4-5-gate-ai-route-activation-on-test-runs'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-4-4-snapshot-the-sales-playbook-and-pin-it-in-route-tuples.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 4.4 records every candidate route tuple, but nothing tests one. The three extraction routes serve tuples that were bootstrap-activated without a run, and no code can activate anything else. So a new prompt, model setting or Playbook version could go live untested, and the Operator can't see whether a live tuple meets its AD-12 pass bar (FR-73, FR-71, AD-8, AD-12, NFR-26, UX-DR30).

**Approach:**
- **Migration `0023_4_5_ai_test_runs`** adds `ai_test_cases` and `ai_test_runs` (owner `playbook`), and a foreign key from `ai_route_activations.test_run_id` to `ai_test_runs.id`.
- **A bulk job** `run_ai_test_sets:<tuple id>:<set>` runs a tuple's AI Test Set through the real `LlmPort`, scores it against the AD-12 bar, stores the run, and activates the tuple when it passes.
- **`playbook.activate`** activates only a tuple with a passing run.
- **The AI content view** shows the runs, the failing cases, "ungated" marks and rollback.

The acceptance criteria are Story 4.5 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Domain `nextron/domain/ai_test_gate.py`** (standard library only).
  - **`TestSet` (`StrEnum`):** `reply`, `grounding`, `drafting_smoke` and `extraction`. Its `label` is "Reply set", "Grounding set", "Drafting smoke set" or "Extraction fixtures".
  - **`ROUTE_SETS`** maps a route to its set:
    - `classify_reply` → `reply`;
    - `grounding_check` → `grounding`;
    - `draft_sequence`, `draft_reply`, `draft_channel`, `call_prep` → `drafting_smoke`;
    - the three extraction routes → `extraction`.
  - **`BOOTSTRAP_ROUTES`** is those three extraction routes.
  - **`MIN_CASES`** is reply 50, grounding 30, drafting_smoke 10, extraction 1. It counts the live cases of the set whose `route` is the tuple's route.
  - **Metrics.** Each is `Metric(name, label, numerator, denominator, bar, passed)`. A share's bar is "at least p%"; a count's bar is "at most n".
    - **`extraction_metrics(route, outcomes)`:**
      - `quotes`: the proposed quotes that pass the substring check; bar 100%; nothing proposed passes;
      - `recall`: the labelled Signals found, or the labelled people for `extract_people`; bar 80%;
      - `injections`: the forbidden Signal types proposed; bar at most 0;
      - `answers`: the cases whose call returned `LlmOk`; bar 100%.
    - **`reply_metrics`:** `unsubscribe_recall` (bar 100%) and `accuracy` (bar 90%).
    - **`grounding_metrics`:** `banned_patterns` (bar 100%), `planted_claims` (bar 95%) and `clean_controls` (not flagged; bar 100%).
    - **`drafting_metrics`:** `zero_flags` (bar 100%).
    - **No denominator.** `recall`, `unsubscribe_recall`, `banned_patterns`, `planted_claims` and `clean_controls` fail when there is nothing to measure. `quotes` passes with nothing proposed. The other shares count cases, so they always have a denominator.
  - **`CaseResult(case_key, passed, expected, got)`.** `expected` and `got` are short texts of at most 300 characters, cut with "…".
  - **`RUN_AI_TEST_SETS_JOB`** is `"run_ai_test_sets"`. **`RunError`** is `set_too_small` or `unknown_route`.
- **Domain `nextron/domain/ai_test_cases.py`** (standard library and `json`).
  - **`TestCase(case_key, test_set, route, input, expected, covers)`** is frozen. `input` and `expected` are JSON objects; `covers` is tokens `[a-z0-9_:.-]{1,64}`. `sha256` is taken over its canonical JSON.
  - **`InvalidTestCases(DomainError)`** has code `invalid_test_cases`. Its message names the file and the field, never a case's text.
  - **`extraction_case(name, data)`** parses Story 2.8's format (`tests/ai_eval/extraction.py`'s `load_case` rules):
    - `case_key` is `case`;
    - `input` is `{segment, blocks}`;
    - `expected` is `expect`;
    - `covers` is `[segment, injection]`, or `[segment, "clean"]`.
  - **`extraction_inputs(case, number, segments)`** gives `(blocks, urgency)`, as the harness's `case_blocks` does.
  - **`score_extraction(case, result)`** gives `(ExtractionOutcome, CaseResult)`, ported from the harness's `evaluate`. A case fails on any of these:
    - a failing quote;
    - a missed label;
    - a forbidden type;
    - a result that isn't `LlmOk`. Its `got` is then the result type.
- **Job keys.** `domain/job.py`'s discriminator also accepts a `TestSet` value. Its docstring says so.
- **Migration `0023_4_5_ai_test_runs`** (down revision `0022_4_4_playbook_snapshots`; `downgrade()` raises). Every column is NOT NULL unless marked otherwise.
  - **`ai_test_cases`:**
    - columns: `id`, `case_key` (64), `test_set` (16), `route` (64), `input`, `expected`, `covers` (Text JSON), `sha256` (64), `loaded_at`, and `retired_at` (nullable);
    - checks on `test_set` and `sha256`;
    - partial unique `uq_ai_test_cases_live_key` on `(test_set, case_key)` `WHERE retired_at IS NULL`.
  - **`ai_test_runs`:**
    - columns: `id`, `tuple_id` (FK → `ai_route_tuples.id`), `test_set`, `outcome` (`passed`/`failed`), `error` (nullable `RunError`, set only when failed), `case_count`, `passed_count`, `metrics` and `results` (Text JSON), `cost_micros`, `started_at` and `finished_at`;
    - checks on every enum, on the counts (≥ 0, passed ≤ cases), and on "error only when failed";
    - index `ix_ai_test_runs_tuple_id_test_set`.
  - **`ai_route_activations`** is rebuilt with `copy_from` set to **0022's exact table**, adding `fk_ai_route_activations_test_run_id_ai_test_runs`. Every existing check, key and the partial index survive, and so do the rows.
  - **Registration.** Every column is mapped to `playbook` in `owners.py` and is `none` in `pii_registry.py`. Runs and cases are never updated, except `retired_at`.
- **Playbook** (`app/playbook/api.py`).
  - **`sync_prompts`** bootstrap-activates only defaults in `BOOTSTRAP_ROUTES` that don't use the Playbook.
  - **`@use_case sync_test_cases(cases)`:**
    - an item that isn't a `TestCase` raises `TypeError`; a repeated `(test_set, case_key)` raises `ValueError`;
    - in one transaction, a new key is inserted; a live row with another SHA is retired and the new one inserted; a live row missing from the input is retired;
    - it logs `test_cases_synced` with counts.
  - **`@use_case activate(tuple_id, *, trigger)`.** `ActivationTrigger` is `test_passed` (actor `system:playbook`) or `rollback` (actor `operator`).
    - **Refusals** raise `ActivationRefused(DomainError)`, write nothing and leave the previous tuple serving:
      - an unknown tuple: "No such tuple";
      - a route without a set, or no `passed` run of that tuple on its set: "No passing test run for this tuple".
    - **No change** when the active activation has the tuple's identity and isn't bootstrap.
    - **Otherwise, in one transaction:**
      1. it deactivates the active activation, with an event `active` → `inactive` and the trigger as cause;
      2. it inserts an activation copied from the tuple row (`playbook_sha256` included), with `bootstrap` false, `test_run_id` set to the newest passing run, and the trigger's actor;
      3. it appends an event `None` → `active`.
    - It logs `route_activated`.
- **`AiTestRuns(clock, meter, llm, queue)`** (`app/playbook/api.py`).
  - **`@use_case queue_runs() -> int`.** In one transaction, every tuple row whose route has a set and has no run on that set gets a `run_ai_test_sets` job:
    - scope: the tuple id;
    - discriminator: the set;
    - payload: `{v, tuple_id, test_set}`;
    - lane: bulk.

    A live job with the same key absorbs it. It returns how many jobs were created and logs `ai_test_runs_queued`.
  - **`@use_case run(tuple_id, test_set, *, activate=True) -> bool`.**
    1. **First transaction.**
       - If `allow(BLOCKABLE, meter.snapshot(now), budget_limits())` is false, it returns `False`.
       - An unknown tuple, or a set that isn't the route's, logs `ai_test_run_skipped` and returns `True`.
       - It builds the `RouteTuple` from the snapshots, checked as `active_tuple` checks them.
       - It reads the live cases by `case_key` and the stored Segments. A case naming an unstored Segment raises `DomainError` before any call.
    2. **The calls.** Fewer cases than `MIN_CASES` makes a `failed` run with `set_too_small` and no calls. Otherwise each case's `llm.run` runs with no transaction open.
       - A retryable `AdapterError` propagates, and nothing is stored.
       - `llm_unknown_route` ends the run as `failed` with `unknown_route`.
       - Any other `AdapterError` propagates, and nothing is stored.
       - A set with no scorer raises `LookupError`. Only `extraction` has a scorer now.
    3. **Last transaction.**
       - It inserts the run, with the summed cost.
       - When the run passed, `activate` is true and the tuple is the route's newest tuple row or the active tuple, it activates with `test_passed`.
       - It logs `ai_test_run_finished` with ids, codes and counts only.
- **The job.** `run_ai_test_sets`:
  - bulk only; priority 30; `max_runtime` 2 h; `retry_limit` 3; group `other`;
  - handler `handlers.run_ai_test_sets(runs)`. `tuple_id` must be a ULID and `test_set` a `TestSet`, else `InvalidJob`. A `False` return is `DEFERRED`.
- **Queries** (`queries/playbook.py`).
  - **`route_tuples()`** adds:
    - to `ActiveTuple`: `tuple_id` (the matching row, or `None`), `ungated` (= bootstrap) and `run`;
    - to `CandidateTuple`: `run` and `passing`.

    `run` is the newest `RunSummary` on the route's set: id, set, outcome, error, counts, metrics, failing `CaseResult`s, cost and `finished_at`.
  - **`health_issues()`** adds, per route whose active tuple's newest run failed: `HealthIssue("ai_tuple_failed", <route>, "<route>: active tuple failed its test set", detail=...)`.
- **`main`.**
  - **`AI_SETS_DIR`** is `ENGINE_ROOT / "tests" / "ai_sets"`.
  - **`load_test_cases(folder=AI_SETS_DIR)`** reads `extraction/*.json` by name. A missing folder or a bad file raises `InvalidTestCases`.
  - **`serve(load_test_cases=...)`.** The prompt step also syncs the cases in its unit of work. `InvalidTestCases` exits 2 with `startup check failed: ai_sets: <message>`.
  - **`build_app(llm=None)`.** The default is `ClaudeGateway(secrets, meter=meter)`, imported inside the function.
    - It registers the job.
    - The lifespan startup runs `queue_contact_markets`, then `queue_runs`, each in its own unit of work and context. Either can fail without stopping the other.
    - `create_app` gets `test_runs`.
- **The view.**
  - **Per tuple:** the run's outcome, set, counts, cost and time. A metrics table shows each metric's label, value as "p% (n of d)" or a count, its bar, and pass or fail. The failing cases show case id, expected and got, inside an `hx-disable` container, autoescaped.
  - **Marks.** "Ungated" on a bootstrap active tuple, and "No test run yet" when a tuple has no run.
  - **Rollback.** Each `passing` candidate gets a "Roll back to this tuple" form that POSTs `/ai/rollback` (`ai_content_rollback`, `@personal_data(False)`), with the fields `csrf_token` and `tuple_id`. Any other field, or a non-ULID tuple id, is a 400.
    - It runs `activate(tuple_id, trigger=ROLLBACK)` and re-renders the panel.
    - **Success:** the notice "Rolled back <route> to tuple <id>.".
    - **Refusal:** an alert with the refusal text.
  - **Check for changes** also calls `queue_runs()` after the sync.
- **The `ai_eval` test.** `tests/ai_eval/test_gate_eval.py` (marker `ai_eval`; it skips without `ANTHROPIC_API_KEY`):
  - it runs `AiTestRuns.run(..., activate=False)` with the real `ClaudeGateway` on a temporary database for the three extraction tuples;
  - it prints the metrics;
  - it asserts that `ai_route_activations` is unchanged.
- **The child guard.** `tests/child_guard/sitecustomize.py` installs `guard_network`. `tests/live_guard.child_env(variables)` puts that folder and the engine root on `PYTHONPATH`. Every test that starts `python -m nextron` uses it: `test_serve_process.py` and `test_environment_isolation.py`.
- **The schema lock.** `tests/contracts/test_schema_lock.py` pins the SHA-256 of each shipped output schema's `json_schema`, by `(route, schema_version)`. A change fails with "a used schema version never changes: add a new version".
- **No test calls the real Claude API.** Tests use `ScriptedLlm` and temporary data dirs.

**Never:**
- Activate a tuple without a passing run, relax `ck_ai_route_activations_tested`, or bootstrap any route outside `BOOTSTRAP_ROUTES`.
- Add a route, prompt, schema, candidate, setting, dependency, adapter, port or navigation section.
- Load the Reply, Grounding or drafting-smoke sets, add `add_override_case`, or add coverage tests. Those are Story 4.6.
- Put case or prompt text in a log, event, error or job payload.
- Run a test set from a dashboard button.

### Decisions made overnight (pending user review)

1. **The set is the key's discriminator.** The AC names `run_ai_test_sets:<tuple id>:<set>`, so AD-10's discriminator list (`tick`, a date, a revision) gains the four set names.
2. **The cases load at every `serve` start** from the engine's `tests/ai_sets/extraction/`, not through `seed sync`. A bootstrap tuple then has its fixtures at first start. A broken fixture stops `serve` like a broken prompt does. Story 4.6 adds the other sets.
3. **Test runs are `blockable`**, whatever the route's class: a gate run is deferrable spend.
4. **Runs are queued by a backfill,** `queue_runs`, at each engine start and after Check for changes, not inside the sync's transaction (the `queue_backfill` precedent). A job that died is queued again at the next start.
5. **A passing run auto-activates only the route's newest tuple, or re-qualifies its active tuple.** An older tuple that passed is offered as a rollback target. A bootstrap tuple that passes gets a new tested activation of the same tuple, which clears "ungated".
6. **Bars beyond AD-12's text, all fail-closed:**
   - zero injection violations;
   - every call answered;
   - `extract_people` recall is over labelled people;
   - a recall bar with nothing labelled fails;
   - extraction needs at least 1 case per route;
   - the Grounding set's clean controls must not be flagged.
7. **Each future route brings its own scorer.** The Reply, Grounding and drafting metrics are pure functions now. The scorers that turn each route's output into outcomes ship with the route (Stories 4.14, 4.15 and 7.x). A contract test requires every shipped route to have a set and a scorer.
8. **`AdapterError`s:**
   - a retryable one retries the whole job;
   - `llm_unknown_route` is a stored failed run;
   - any other (`credential_not_set`, say) stores nothing, so the job dies and is queued again at the next start.
9. **Schema pinning (Story 2.8's deferred item) is a contract-test lock, not a database column.** An in-place edit of a used schema version fails the suite, the same protection `manifest.lock` gives prompts.
   - **Risk:** someone could edit the model and its pin together.
   - Adding `schema_sha256` to tuples and activations needs an AD-8 amendment that only Mac can approve.
10. **Engines started by tests get the network guard** (part of Story 1.8's deferred item). This story adds the first automatic background Claude call, and a test-started `dev` engine reads Mac's real `dev` keyring. The keyring swap stays deferred.
11. **The rollback POST is `/ai/rollback`.** The refusal text is exactly "No passing test run for this tuple".

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First start | 4.4 DB, 3 bootstrap tuples, 11 fixtures | 11 cases stored; 3 jobs `run_ai_test_sets:<id>:extraction`; view marks 3 "ungated" | N/A |
| Restart | jobs live or runs stored | no new job | N/A |
| Pass (bootstrap) | scripted labelled answers | run `passed`; new tested activation of the same tuple; not ungated; no health issue | N/A |
| Fail (bootstrap) | an invented quote | run `failed`, failing case listed; bootstrap keeps serving; health issue | N/A |
| Pass (newest candidate) | candidate B newer than active A | B activated (`test_passed`), A inactive | N/A |
| Too small | 0 live cases for the route | `failed`, `set_too_small`, 0 calls | N/A |
| Over budget | spend ≥ budget | 0 calls, nothing stored | job `DEFERRED` |
| API down | `AdapterError(True)` | nothing stored | job retried |
| No key | `credential_not_set` | nothing stored | job dead; queued again next start |
| Activate untested | no passing run | nothing written; previous serves | `ActivationRefused` "No passing test run for this tuple" |
| Rollback | POST a passing older tuple | it serves; notice | refused → alert; bad form → 400 |
| No candidate | a set's route without tuples | not run | N/A |
| ai_eval | `run(..., activate=False)` | runs stored, activations unchanged | skipped without a key |
| Fixture edited | a case's SHA changed | old row retired, new row live | malformed → exit 2 |

</frozen-after-approval>

## Code Map

- **The service** is `nextron/app/playbook/api.py`.
  - `Playbook.sync_prompts`, with its bootstrap loop in `_sync` (lines 330–360), and the module docstring.
  - Keep the 4.4 tests passing. The bootstrap of a probe non-extraction route now does nothing.
  - `activate` uses a private `_activate(...)`, called inside an open transaction, so `AiTestRuns.run` can call it in its last transaction without nesting use cases.
  - The pattern for budget, then transaction, then I/O, then transaction is `nextron/app/research/api.py:600` (`WebsiteCrawls.enrich_lead`). The enqueue precedent is `ContactMarkets.queue_backfill`, at about line 500.
- **The harness to port** is `tests/ai_eval/extraction.py`: `load_case`, `case_blocks`, `_proposed_quotes`, `_proposed_types`, `_found_types`, `_proposed_names`, `_passes` and `evaluate`. Leave the harness and its tests unchanged.
- **Repositories.**
  - New: `nextron/db/repos/playbook/test_cases.py`, with `live(test_set, route)`, `all_live()`, `insert` and `retire`.
  - New: `test_runs.py`, with `insert`, `newest(tuple_id, test_set)`, `newest_passed(...)` and `every()` for the view.
  - `route_tuples.py` gains `get(tuple_id)`. `activations.py` gains `deactivate(id, at)`.
- **Models and migration.**
  - The models are in `nextron/db/models/playbook.py`. The drift test compares them with the migrations; a partial index uses `sqlite_where`.
  - `versions/0022_4_4_playbook_snapshots.py` gives `_activations_0014()`, the rebuild pattern, and 0022's activation shape for `copy_from`.
  - **Head bumps** to `0023_4_5_ai_test_runs`:
    - `tests/contracts/test_persistence_contracts.py` `HEAD` and its chain probes (`0023_*` → `0024_*`);
    - `tests/unit/test_migrations.py`;
    - `tests/app/test_serve_process.py:324`;
    - `tests/unit/test_serve.py` (`0023_more` → `0024_more`).
  - Add a migration test: an activation survives, and the new key and the old checks are present.
- **Jobs.**
  - `nextron/domain/job.py:363` (`_is_discriminator`) and its tests;
  - `nextron/jobs/handlers.py` (pattern: `enrich_lead`);
  - `nextron/main.py:944` `build_job_types` (add `test_runs`);
  - `tests/contracts/test_health_contracts.py` (groups) and `test_job_contracts.py`.
- **Reads:** `nextron/app/queries/playbook.py`, at `route_tuples` (275) and `health_issues` (317). Its import rule is `nextron.domain` and `nextron.db.repos.playbook`.
- **`main`:**
  - `sync_prompts` step (1493) and `serve` (1531);
  - `build_app` (774);
  - `queue_contact_markets` (920) and `engine_lifespan` (1234);
  - `bootstrap.ENGINE_ROOT`.
  - The test `test_the_composition_root_imports_neither_the_gateway_nor_the_sdk` (`tests/unit/test_ai_gateway.py:~1020`) must still pass.
- **The web.**
  - `nextron/web/ai_content_page.py`, `templates/ai_content_panel.html`, and `web/app.py` from 687 (the `ai_content_check` pattern);
  - `tests/dashboard.py` (`Dashboard` gains `test_runs` over a `ScriptedLlm`);
  - `tests/contracts/test_web_contracts.py` (the route lists);
  - `app.css`, edited byte-preserving.
- **Fakes:** `tests/fakes/llm.py` (`ScriptedLlm`) and `tests/fakes/usage_meter.py`. The `queue_on(clock)` helper is in `tests/app/test_leads.py`.
- **Guards:**
  - `tests/live_guard.py` (`guard_network`, at 352);
  - `tests/conftest.py`;
  - `tests/contracts/test_test_isolation.py` (add the child-guard contract);
  - `tests/contracts/test_ai_eval_opt_in.py`.
- **Other contracts:**
  - `test_source_tree.FILES` (every new file);
  - `test_log_event_names` (event names under 32 characters);
  - `test_domain_stdlib_only`;
  - `test_prompt_manifest.py` (`SHIPPED_CANDIDATES` is unchanged; add the check that every route has a set and a scorer).
- **Docs:** `nextron-engine/README.md`, a new "AI test gate" section after the Story 4.4 section.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/ai_test_gate.py`, `ai_test_cases.py`, `job.py` -- the sets, bars, metrics, case parsing, extraction scoring, and the discriminator.
- [x] `nextron/db/alembic/versions/0023_4_5_ai_test_runs.py`, `db/models/playbook.py`, `models/__init__.py`, `owners.py`, `pii_registry.py`, and the repositories -- the tables, the key and the reads and writes.
- [x] `nextron/app/playbook/api.py`, `app/queries/playbook.py` -- the narrowed bootstrap, `sync_test_cases`, `activate`, `AiTestRuns`, the view data and the health issue.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py` -- the job, `load_test_cases`, the `serve` step, `build_app` and the startup step.
- [x] `nextron/web/ai_content_page.py`, `web/app.py`, `templates/ai_content_panel.html`, `static/app.css` -- runs, metrics, failing cases, ungated marks and rollback.
- [x] `tests/child_guard/sitecustomize.py`, `tests/live_guard.py`, `test_serve_process.py`, `test_environment_isolation.py` -- the child network guard. `test_serve_process` also asserts the 3 job keys.
- [x] Tests:
  - `tests/unit/test_ai_test_gate.py` (every bar, denominator 0, cut texts);
  - `tests/unit/test_ai_test_cases.py` (parsing, inputs, scoring of every fixture shape);
  - `tests/app/test_ai_test_runs.py` (every matrix row);
  - `tests/app/test_ai_content_page.py` (runs, rollback, 400);
  - `test_playbook_prompts.py` and `test_playbook_snapshots.py` (bootstrap narrowed);
  - `test_serve.py` (startup step, cases, exit 2);
  - the migration test;
  - the contracts: schema lock, child guard, sets and scorers, heads and `FILES`;
  - `tests/ai_eval/test_gate_eval.py`.
- [x] `nextron-engine/README.md` -- the docs.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes, both `ai_eval` tests stay skipped, every contract is kept, and no test reaches the Claude API.
- Given a fresh `serve` in a test, when it starts, then 3 `run_ai_test_sets` jobs exist, no activation is tested, and no non-loopback connection is possible from the child.

## Implementation Notes

Built as specified. What the spec left open, and where the code had to choose:

- **`nextron/domain/ai_test_gate.py`** imports no other domain module: `domain.job` imports it
  (a set is a key's discriminator) and `domain.budget` imports `domain.job` through
  `domain.health`, so any domain import here is a cycle. A metric's name has its own
  `NAME_PATTERN`.
  - **Values beyond the spec's list.** `RunOutcome` (`passed`, `failed`); `RunError.text` ("set
    too small", or "the AI gateway knows no route candidate for this tuple"); `set_of(value)`;
    `TUPLE_ID_FIELD` and `TEST_SET_FIELD` (the job's payload fields); the outcome values the
    metric functions take: `ExtractionOutcome(answered, quotes_proposed, quotes_passed,
    signals_labelled, signals_found, people_labelled, people_found, violations)`,
    `ReplyOutcome(expected_unsubscribe, got_unsubscribe, correct)`,
    `GroundingOutcome(kind: GroundingCase, flagged)` (`banned_pattern`, `planted_claim`,
    `clean_control`) and `DraftingOutcome(flags)`.
  - **Metrics.** A share has an `int` denominator and a count `None`; `bar` is the lowest
    passing percentage or the highest passing count. `share(...)` and `count(...)` build them.
    A share passes when `numerator * 100 >= bar * denominator`. `percent` is rounded down, so a
    value under its bar never reads as at it. Every share other than `quotes` fails with a
    denominator of 0. `run_passed(metrics)` needs at least one metric, all passed.
  - `CaseResult` cuts `expected` and `got` in `__post_init__` (299 characters and "…" when
    longer than 300). `metrics_json`/`metrics_from_json` and `results_json`/`results_from_json`
    store canonical JSON lists.
- **`nextron/domain/ai_test_cases.py`.**
  - **`score_extraction(case, result)`** takes the call's parsed answer (a `Mapping`: an
    `LlmOk`'s `parsed`) or, for any other result, the result's type name (a `str`). The domain
    never imports the port, so `AiTestRuns` passes `result.parsed` or `type(result).__name__`.
  - **`TestCase`.** `case_key` fullmatches `[a-z0-9][a-z0-9_.-]{0,63}`, and `route` must be a
    route of `test_set`. `input` and `expected` are kept as their canonical JSON read back (a
    caller's later change is never seen), and the case is unhashable (dict fields). `__test__ =
    False` keeps pytest from collecting it (and `TestSet`, `TestCaseSync`). `as_json()` is what
    `sha256` hashes. `stored_case(...)` rebuilds a stored row (`InvalidTestCases` "stored test
    case <key>: doesn't read back"). `canonical_json` is shared with the repository.
  - **`extraction_case(name, data)`** also requires `case` to be the file's stem (the
    harness's docstring rule), so the keys of one folder can't repeat, and `segment` to fit the
    `covers` token pattern. Messages are `<file>: <path> <reason>`, such as
    `ops_inbox_job_ad.json: blocks[0].source must be a non-empty string`.
  - **`check_segments(cases, segments)`** raises `InvalidTestCases("test case <key>", "its
    Segment isn't stored; run nextron seed sync, then restart the engine")`: the
    `DomainError` the spec names. The job then dies (`job_dead` alert) and is queued again at
    the next start.
  - **The case texts.** `expected` is `Signals: <labelled types>[; never: <forbidden types>];
    every quote in its block` (`People: <names>; ...` for `extract_people`). `got` is `Signals:
    <proposed types>[ under Segment <segment>]; <n> of <m> quotes in their block` (`People:
    <names>; ...`), or the result's type name. Lists are sorted, "none" when empty.
- **The migration.** `ck_ai_test_runs_counts` also requires `cost_micros >= 0`. The "error only
  when failed" check is `ck_ai_test_runs_error_when_failed`. The test-run foreign key is
  `fk_ai_test_runs_tuple_id_ai_route_tuples`. The models are `AiTestCaseRecord` and
  `AiTestRunRecord`; `AiRouteActivationRecord.test_run_id` gains its foreign key.
- **Repositories.** `test_cases`: `live(test_set, route)` (by `case_key`), `all_live()`,
  `insert(case, at)` and `retire(case_id, at)`; `StoredCase` keeps the JSON text. `test_runs`:
  `insert`, `newest`, `newest_passed` (by `finished_at`, then `id`) and `every()` (by tuple,
  set, then newest first). `route_tuples.get(tuple_id)`; `activations.deactivate(id, at)`
  returns whether it was active.
- **One use case per unit of work.** A unit of work runs one use case, so `serve`'s prompt step
  runs `sync_test_cases` in a second unit of work straight after `sync_prompts`'s (the files
  are read before either, so a broken fixture stores nothing), and Check for changes runs
  `queue_runs` in its own unit of work after the sync's.
- **`Playbook.sync_test_cases`** returns `TestCaseSync(added, changed, retired, unchanged)`.
  An empty input touches no table and changes nothing, as `sync_prompts` does with no input
  (so the toy migration chains in `test_serve.py` still start). The consequence: an emptied
  `extraction` folder leaves the stored cases live (a missing folder stops `serve`).
- **`Playbook.activate`** returns `Activation(route, tuple_id, run_id, changed)`.
  `ActivationRefused` has code `activation_refused`; a text that isn't a ULID is "No such
  tuple". Both events carry the qualifying run as `cause_ref`. `route_activated` logs `route`,
  `tuple_id`, `run_id`, `trigger` and `changed`.
- **`AiTestRuns`.**
  - **Scorers.** `SCORERS` maps a set to a `Scorer(check, inputs, score, metrics)`; only
    `extraction` has one. A scorer is looked up only when the set is big enough, so a Reply
    tuple before Story 4.6 stores a `set_too_small` run.
  - **The run row.** `case_count` is the live cases found (on `set_too_small` too). A run that
    ended unscored (`set_too_small`, `unknown_route`) stores `metrics` and `results` as `[]`.
    `passed_count` counts the passing case results, and every case's result is stored.
  - `UNKNOWN_ROUTE_CODE` is `"llm_unknown_route"`, because the app can't import the gateway.
    A test pins it to `nextron.adapters.ai.LLM_UNKNOWN_ROUTE`.
  - **Logs.** A skip logs `ai_test_run_skipped` with `reason` `unknown_tuple` or
    `not_the_routes_set`. `ai_test_run_finished` adds `activated`.
- **Queries.** `tuple_of(tuple_id)` shares `_built` with `active_tuple`, so the two check the
  snapshots the same way; for a tuple row its error texts say "the tuple of …" (the active
  tuple's are unchanged). `RunSummary(id, test_set, outcome, error, case_count, passed_count, metrics, failing,
  cost_micros, finished_at)`. An active tuple's `tuple_id` is the row with its model, effort,
  prompt version, schema version and Playbook. The health issue's `detail` is "It keeps serving
  until a tuple that passes replaces it. The failing cases are on the AI content page (/ai)."
- **`main`.**
  - **`load_test_cases(folder)`** reads at most `MAX_CASE_FILE_BYTES` (1 MiB) + 1 bytes per
    file, as strict UTF-8 JSON. Its messages name `extraction/<file>` ("the file couldn't be
    read", "the file is larger than 1 MiB", "the file isn't UTF-8 JSON"), or `extraction: the
    folder doesn't exist`. `TestCaseLoader` is its type, and `AI_SETS_CHECK` is `ai_sets`.
  - **The startup step** is `run_startup_steps(((name, step), ...))`. It runs
    `queue_contact_markets`, then `queue_test_runs`; a step that raises logs
    `startup_step_failed` with `step` and `error`, and the next step still runs.
  - **`build_job_types`** takes a required `test_runs`; every caller passes one.
- **The view.**
  - **A run's line** reads `Test run: Passed|Failed[ (<error>)]. <set label>, <p> of <n>
    cases passed, cost $<dollars, 4 decimals>, finished <time>.`
  - **A share of nothing** shows "none (0 of 0)".
  - **The ungated mark** reads "Ungated: this tuple serves without a passing test run."
  - **The rollback form** takes `csrf_token` (optional in the body, as the CSRF check also
    reads the header) and exactly one `tuple_id`. A missing, repeated or non-ULID id, or an
    extra field, is a 400.
  - **`app.css`** gains `.ai-mark`, `.ai-ungated`, `.ai-run`, `.ai-run-failed`, `.ai-metrics`
    and `.ai-cases`, appended byte-preservingly.
  - **The `/ai` intro** now says that tuples serve only once their run passed.
- **The child guard.** `tests/child_guard/sitecustomize.py` fails closed: if it can't install
  `guard_network` (on a `pytest.MonkeyPatch()` never undone), it ends the child with exit code
  97, because Python would otherwise only print a `sitecustomize` error and run unguarded.
  `child_env` keeps an existing `PYTHONPATH` after the guard folder and the engine root.
- **Tests.**
  - **New:** `tests/unit/test_ai_test_gate.py`, `tests/unit/test_ai_test_cases.py` (checked
    against the harness's `load_case` and `case_blocks` for every fixture),
    `tests/app/test_ai_test_runs.py`, `tests/contracts/test_schema_lock.py` and
    `tests/ai_eval/test_gate_eval.py`.
  - **Extended:** `test_ai_content_page.py`, `test_serve.py`, `test_runner_lifespan.py`,
    `test_playbook_prompts.py`, `test_migrations.py` (0023), `test_job_contracts.py`,
    `test_prompt_manifest.py` (sets and scorers), `test_test_isolation.py` (the child guard),
    `test_ai_eval_opt_in.py`, `test_web_contracts.py` and `test_job_domain.py`.
  - **Story 2.8 and 4.4 tests that needed changes.**
    - Their raw tested activations now name a stored run: the new foreign key refuses an
      invented `test_run_id`.
    - The 0022 migration test stops at 0022: its synthetic tested row names no run.
    - The `ActiveTuple` equality gains the new fields.
  - **`test_serve_process`** waits until the three `run_ai_test_sets` jobs are dead. A fresh
    test database has no Segment (seed sync never runs), so each run stops before any call with
    `invalid_test_cases`. The test then checks that no run, no tested activation and no AI
    usage were stored. Their `job_dead` alerts fail on the unset Telegram chat id
    (`setting_not_set`), so no adapter is built and the sink stays untouched.
  - **The scenarios a newer candidate needs** are faked by monkeypatching
    `routes.CANDIDATES` with an extra effort, so `ScriptedLlm`, like the gateway, finds it.
    Without that, the same tuple is the `unknown_route` scenario.

**Implementation verification** (implementer, targeted, rules section 4a): the spec's
verification set, every test file touched and every module that builds the dashboard, the app
or the job registry. That was 3,510 passed and 3 skipped (the two `ai_eval` tests and the
`ai_eval` opt-in probe), with 3 failures in the health panel's job-group lists, since fixed
(`run_ai_test_sets` is in group Other). A re-run of `tests/contracts`, `tests/ai_eval` and the
new and edited test files gave 1,381 passed and 3 skipped. `lint-imports`: 29 kept, 0 broken.
The full suite has not been run.

**Review fixes (pass 1):**

- **The active tuple re-qualifies beside a newer candidate.** A `test_ai_test_runs.py` test
  records a newer `extract_people` candidate, then runs the bootstrap tuple. A pass gives a
  `system:playbook` tested activation of the same old tuple, no longer ungated, with
  `route_tuples()`'s `active.tuple_id` still the old row. A failure keeps the bootstrap
  serving, ungated, with `ai_tuple_failed` and the run `FAILED`.
- **A route's first activation** deactivates nothing. A test stores a passing reply run for a
  `classify_reply` tuple through the repository, then activates it: one `None` to `active`
  event, and `active_tuple` serves it.
- **The bootstrap's `uses_playbook` guard is tested.** With `extract_people`'s default set to
  `uses_playbook=True`, only the other two extraction routes are bootstrapped
  (`test_playbook_prompts.py`).
- **Every quoted list counts.** `test_ai_test_cases.py` ports the harness's
  `test_every_quoted_list_counts`. An invented `size_clues` quote fails its case and the
  `quotes` metric.
- **The panel's "none (0 of 0)" is tested.** An `extract_signals` run whose answers propose no
  quote renders the `quotes` row as "none (0 of 0) at least 100% Pass"
  (`test_ai_content_page.py`).
- **`main`'s step 7 docstring** says only the three extraction routes (`BOOTSTRAP_ROUTES`) are
  bootstrapped. It also says a failed case sync, which comes after the prompt sync committed,
  leaves the prompt sync's rows stored, and the server still never starts.
- **The `ai_tuple_failed` detail** is now "It keeps serving until a tuple passes its test set
  and is activated. The AI content page (/ai) shows the run's metrics, its failing cases or why
  it failed." The new text is pinned by a test and is in the queries docstring and the README.
- **Check for changes announces the queued runs.**
  - `check_notice(result, queued)` adds " Queued <n> test run(s)." ("run" for one) when the
    check queued any.
  - The POST passes `queue_runs()`'s count.
  - Two existing notice assertions now include it, and a test covers the singular.
  - The docstrings and the README say so.
- **A rollback to the tuple already serving** says "<route> already runs tuple <id>." instead of
  "Rolled back …" (`Activation.changed` false). A test covers it, and the docstrings and the
  README say so.
- **Fix verification:** `test_ai_test_runs.py`, `test_playbook_prompts.py`,
  `test_ai_test_cases.py` and `test_ai_content_page.py` gave 189 passed.

**Final verification**, on the final tree after the review fixes:
- The orchestrator's targeted re-run (the patched files, `test_playbook_snapshots.py`, `test_serve.py` and `tests/contracts`) gave 1,445 passed and 1 skipped.
- The full suite through `suite_lock.py` (track-C) gave 10,769 passed and 4 skipped: the three `ai_eval` tests and the symlink test.
- `lint-imports`: 29 kept, 0 broken.
- The known runner flake did not occur.
- No test called the Claude API: gate tests use `ScriptedLlm`, and engines started by tests run under the child network guard.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers reported 28 raw findings:
- Blind Hunter (B): 14;
- Edge Case Hunter (E): 10;
- Verification Gap (V): 4 gaps (pre-verified), with no other findings.

Merging duplicates leaves 24: B1+E1+B2, B5+E4 and B9+E9.
- **Routing:** 9 patch, 2 defer and 13 reject. One rejection (B1+E1+B2) changes the approved spec, so it is also recorded in `deferred-work.md`.
- **Loopbacks:** there are no `intent_gap` or `bad_spec` entries, so nothing loops back.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | V1: a passing (or failing) run of an active tuple that isn't its route's newest row is never tested: `_may_activate`'s active branch and `_active_row`. | gap (pre-verified) | Every passing `run` in the tests is on a route's only or newest row. Dropping the branch fails no test. | patch (record a newer `extract_people` candidate. Then: a passing run of the bootstrap tuple gives a tested activation of the same tuple, and `active.tuple_id` is the old row; a failing one gives `ai_tuple_failed` and `active.run.outcome` FAILED) |
| 2 | V2: the `spec.uses_playbook` half of the bootstrap guard is unobservable. `draft_probe` is excluded by its route name alone. | gap (pre-verified) | Deleting `spec.uses_playbook or` fails no test. | patch (on an empty database, sync with `extract_people`'s default set to `uses_playbook=True`. That route gets no activation; the other two are bootstrapped) |
| 3 | V3: the port's quote count over `profile_facts`, `size_clues` and `job_postings` is untested. | gap (pre-verified) | The harness has `test_every_quoted_list_counts`; the port has no equivalent. | patch (port that test, plus an invented quote in `size_clues` that fails the case) |
| 4 | V4: the panel's "none (0 of 0)" share text is never asserted. | gap (pre-verified) | No test greps it. | patch (assert the `quotes` row of a run with no proposed quote) |
| 5 | B4: the first activation of a route with no active activation (the `active is None` branch, the only path for every future gated route) is untested. | medium | Real: every `activate` test starts from an active bootstrap. The branch is three lines, but it is the path Stories 4.14, 4.15 and 7.x depend on. | patch (activate a tuple of a route with no activation and a stored passing run. Expect exactly one `None → active` event, no deactivation, and `active_tuple` serving it) |
| 6 | B10: `main`'s docstring step 7 still says the step "bootstrap-activates each non-Playbook route's default" and "Either way the transaction rolled back, so nothing was stored". `sync_test_cases` runs in a second unit of work after the prompt sync committed. | low | Real (`main.py` lines 49–69). A direct docstring correction. | patch (only the extraction routes are bootstrapped; a failure in the case sync leaves the prompt sync stored, and the start still stops) |
| 7 | B11: the `ai_tuple_failed` detail says "The failing cases are on the AI content page", but a `set_too_small` or `unknown_route` run has none. | low | Real: `TUPLE_FAILED_DETAIL` is fixed text, and such runs store `results` as `[]`. A direct text correction. | patch (detail: it keeps serving until a tuple passes its test set and is activated; the AI content page (/ai) shows the run's metrics, its failing cases or why it failed) |
| 8 | B14: Check for changes never says it queued paid test runs. | low | Real: `ai_content_check` discards `queue_runs()`'s count. It shows on every click that creates a tuple, and the fix is a sentence in the notice. | patch (append "Queued <n> test run(s)." to the notice when n > 0) |
| 9 | E8: a rollback to the tuple already serving says "Rolled back …", though nothing changed. | low | Real: `rollback_notice` ignores `Activation.changed`. It is reachable by a double submit. A direct correction. | patch ("<route> already runs tuple <id>." when nothing changed) |
| 10 | B1+E1+B2: a run isn't bound to the cases it measured. A tuple with a stored run, failed included, is never queued again: after a transient failure, after a fixture edit, or after Story 4.6 grows the sets (a `set_too_small` run stays failed). No case-set fingerprint is stored, so a pass on retired cases still qualifies. | medium | Real: `queue_runs` skips any `(tuple, set)` with a run, as the approved spec says ("has no run on that set"). A bootstrap tuple with one bad run stays "failed its test set" until a new tuple appears. The fix (a case-set fingerprint, and re-queueing when it changes) changes the frozen rule and the schema. | reject: it changes the approved spec. Recorded in `deferred-work.md` for Story 4.6, which changes the sets |
| 11 | B7: the gate has no tuple for `grounding_check`'s Opus fallback. A tuple carries one model, and the sync records only each route's default candidate. | maybe-false (medium if true) | `grounding_check` and its fallback don't exist yet. Story 4.14 decides how the fallback is a candidate. | defer (Story 4.14) |
| 12 | B13: AD-12's Operator override cases (pseudonymised personal data) will join `ai_test_cases`, classed `none` and never deleted. | maybe-false (medium if true) | No override exists. Story 4.6 adds `add_override_case`. | defer (Story 4.6: the classification and erasure) |
| 13 | B3: activation accepts any passing run while the view shows the newest, so a re-run that failed still offers rollback. | low | A second run needs a crash between the handler and `complete`. The approved spec says any `passed` run qualifies. | reject |
| 14 | B5+E4: the budget is checked once per run, so a run can overspend by the rest of its set; a retry bills again; `cost_micros` counts the last attempt only. | low | Real, but bounded by one set: cents for Haiku extraction. `allow()` has no reservation anywhere (the Story 1.13 deferral). A mid-run deferral adds a branch. `ai_usage` still records every call. | reject |
| 15 | B6: test-run calls are metered as ordinary route usage in `ai_usage`. | low | Real; one set per new tuple. Telling them apart needs a new `AiCall` and `LlmPort` field. | reject (noted for Story 4.21, which sizes cost per route) |
| 16 | B8: `test_the_pins_are_add_only` forces a pin to be deleted when its candidate stops shipping. | false | A shipped candidate never goes away (`SHIPPED_CANDIDATES`, an add-only contract), so the shipped set never shrinks. The subset check only refuses a stale pin. | reject |
| 17 | B9+E9: the child-guard contract looks for `child_env(` per module and the literal `"-m", "nextron"`. | low | Real tripwire limits. A test-env child reads an empty test keyring, so it can't call Claude. An AST check adds complexity. | reject (flagged for the merge with Story 1.21's supervisor tests) |
| 18 | B12: `runs_repo.every()` reads every run's JSON for `queue_runs` and every `/ai` render. | low | With one run per tuple per set, the table stays small. | reject |
| 19 | E2: an emptied `extraction/` folder leaves its stored cases live. | low | Real (the empty-input early return), but the fixtures are shipped files. A guard adds a branch. | reject |
| 20 | E3: a fixture block id or source that fails `UntrustedBlock`'s pattern, or a lone surrogate, fails mid-run after paid calls, or at `serve` as `prompt_sync_failed`. | low | Real, but the shipped fixtures pass. The fix adds checks in `extraction_case`. | reject (noted for Story 4.6's new fixtures) |
| 21 | E5: `queue_runs` raising after a committed check is a 500. | low | It needs a database error after a committed sync, and the next start queues the runs. A guard adds a branch. | reject |
| 22 | E6: a newer candidate whose run was still queued can, by passing, replace a tuple the Operator just rolled back to. | low | Narrow: a candidate pending across a rollback. The view shows the activation. A guard adds state. | reject |
| 23 | E7: two tuple rows of one route created in one sync could rank the backfilled active row "newest". | false | The backfill adds a row only for an activation with no row that differs from its default. No database has one: the shipped defaults never changed. Even then the outcome is fail-safe (a rollback target). | reject |
| 24 | E10: `build_app` without `llm=` builds the real `ClaudeGateway` in tests. | low | It is built but never reaches Claude: injected fake secrets, and the live and network guards. Every gate test injects `ScriptedLlm`. | reject |

## Design Notes

```python
# AiTestRuns.run, in outline
with uow.transaction():                      # budget, tuple, cases, Segments
    if not allow(BLOCKABLE, meter.snapshot(now), limits): return False
results = [score(case, call(llm, route_tuple, case)) for case in cases]  # no transaction
with uow.transaction():
    run_id = test_runs_repo.insert(...)
    if passed and activate and (is_newest or is_active): _activate(tuple_id, TEST_PASSED, now)
```

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_ai_test_gate.py tests/unit/test_ai_test_cases.py tests/app/test_ai_test_runs.py tests/app/test_ai_content_page.py tests/app/test_playbook_prompts.py tests/app/test_playbook_snapshots.py tests/unit/test_serve.py tests/unit/test_migrations.py tests/unit/test_job*.py tests/app/test_serve_process.py tests/app/test_environment_isolation.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
- The full suite through `suite_lock.py` (orchestrator only) -- expected: pytest and `lint-imports` both pass.
