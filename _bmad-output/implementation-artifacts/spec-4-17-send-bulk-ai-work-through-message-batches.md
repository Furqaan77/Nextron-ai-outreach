---
title: "Story 4.17: Send bulk AI work through Message Batches"
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '564a027c53cce4b1f1455ed982fbbe938e5870b8'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every AI call is a direct Messages call, so bulk work (research extraction) pays full price, and `LlmPort` has no way to submit, collect or delete a Message Batch (AD-8, INT-7, FR-57).

**Approach:** Add `submit_batch` / `fetch_batch` / `delete_batch` to the port, the Claude gateway and the fake; add an `ai_batches` / `ai_batch_items` store shared by row owner; make `bulk` extraction queue pending items that the daily `submit_bulk_ai` job groups into one batch per owner and route; the 15-minute `poll_ai_batches` job folds ended results back through research's `apply_<route>_result` (fingerprint, need and contactability re-checked, else `stale`), re-runs lost items directly at `soon`, and deletes the batch once every item is settled.

## Boundaries & Constraints

**Always:**
- **Port** (`nextron/ports/llm.py`): `BatchRequest(custom_id, inputs, framing=None)` (frozen; custom_id checked by the domain); `submit_batch(route_tuple, items) -> str` (provider batch id); `fetch_batch(batch_id) -> BatchInProgress | BatchEnded`; `BatchEnded.results: Mapping[custom_id, BatchItemResult]` where `BatchItemResult` = `BatchSucceeded(result)` (`result` an `LlmOk` / `LlmRefused` / `LlmTruncated` / `LlmInvalid`) | `BatchLost(kind)` with kind ∈ {`errored`, `canceled`, `expired`}; `delete_batch(batch_id) -> None`. None waits: each is one request (plus the results stream for an ended batch). Retryable failures (429, 5xx, connection) raise `AdapterError(True, "llm_unavailable")`; other 4xx `AdapterError(False, "llm_rejected")`; `delete_batch` on a 404 returns (idempotent). Route checks as `run` (unknown tuple, batchable route only, `ValueError` for >1 subject per item, an empty list, or a repeated `custom_id`).
- **Gateway** (`adapters/ai`): `submit_batch` → `client.messages.batches.create(requests=[{custom_id, params}])`, params from a new `prompting.build_batch_params(...)`: same as `build_request` (system/prefix, framing, user turn, `output_config.effort` + `format`) but `cache_control` `{"type": "ephemeral", "ttl": "1h"}` on the prefix's last block, `max_tokens` = the candidate's, and **never** `extra_headers`/`extra_body`/`fallbacks`. `fetch_batch` → `batches.retrieve`; `processing_status` `ended` → iterate `batches.results`, each `succeeded` message read by the same outcome rules as `run` (no retry) and metered once as an `AiCall` with `batch=True` and cost at the batch rate (`pricing.cost_micros(..., batch=True)`: every rate halved, rounded up); `errored`/`canceled`/`expired` → `BatchLost` (errored is metered with zero tokens and `error_code` `llm_batch_errored`). Submit and delete record nothing. No text in errors or logs.
- **Fake** (`tests/fakes/llm.py`, `ScriptedLlm`): same caller checks; `submit_batch` records `(route_tuple, items)` in `batches_submitted` and returns `msgbatch_fake_<n>`; `fetch_batch` returns `BatchInProgress()` until a test calls `end_batch(batch_id, {custom_id: BatchItemResult})`; `delete_batch` records `deleted`; an unknown id raises `AdapterError(False, "llm_rejected")`.
- **Domain** `nextron/domain/ai_batch.py` (pure): `BatchOwner` {`drafting`, `research`}; batch status {`submitted`, `ended`, `deleted`}; item status {`pending`, `submitted`, `applied`, `stale`, `rerun`, `failed`} (terminal = the last four); `custom_id(route, target_ulid, rev)` = `<route>_<ULID>_<rev>`, fullmatch `[A-Za-z0-9_-]{1,64}`, rev a non-negative int, else `ValueError`, and `parse_custom_id`; `Fingerprint` (route tuple key + sorted `(id, version-or-hash)` pairs) with a canonical JSON encoding.
- **Migration** `0049_4_17_ai_batches` (down `0048_4_15_draft_failed`): `ai_batches` (id ULID, `owner` CHECK ∈ {drafting, research}, `provider_batch_id` unique, `route`, `route_tuple_key`, `status`, `submitted_at`, `ended_at`, `deleted_at`) and `ai_batch_items` (id ULID, `owner`, `batch_row_id` FK nullable while `pending`, `route`, `custom_id`, `target_id`, `rev`, `context_id` nullable ULID, `fingerprint` nullable JSON text, `status`, `created_at`, `updated_at`; unique `(owner, route, target_id, rev)`; unique `(batch_row_id, custom_id)`; CHECKs for both status sets). Written only by a new shared writer `nextron/db/ai_batches.py` (added to `SHARED_WRITERS`; every write takes a `BatchOwner` and touches only that owner's rows); PII `none`. A contract test asserts app code under `app/research/` passes only `BatchOwner.RESEARCH` (and `app/drafting/` only `DRAFTING`).
- **Research producer:** `SignalExtractions.extract_lead(lead_id, run_id, *, lane=None)` and `PeopleExtractions.extract(..., *, lane=None)` take the job's lane (handler passes `job.lane`); urgency = `domain.ai.chain_urgency(lane)` (`soon` on the interactive lane, else `bulk`). At `bulk` each step, after its existing budget check and unknown/merged-Lead skips, inserts one `pending` item (owner research, its route, target the Lead, rev = the crawl run count, `context_id` the run) instead of calling; at `soon` it calls directly as today.
- **`submit_bulk_ai`** (trigger daily at setting `bulk_ai_submit_at`, type time, default 14:00, in `sender_timezone`): per owner and route — blockable route: `domain.budget.allow` first, refused → items stay `pending`, no batch; `essential` routes skip the check. Older pending items of the same (route, target) become `stale`. Each remaining item's request is rebuilt now from the step's prepare (no tuple / no pages → its normal `no_activation` / `no_pages` run is recorded and the item is `stale`), with its fingerprint (tuple key + sent page ids and `content_hash`es). No requests → no batch. Else `submit_batch` outside any transaction, then one transaction writes the batch row and marks the items `submitted`.
- **`poll_ai_batches`** (every 15 minutes, `at_startup`): for each `submitted` batch, `fetch_batch`; in progress → nothing. Ended → `ended_at`; each `submitted` item in its own transaction:
  - `BatchSucceeded(LlmOk)` → `research.apply_extract_signals_result` / `apply_extract_people_result`: applies through the direct path's AD-18 apply only if the Lead isn't merged, no `ok` run of that route for the Lead started after the item was submitted, every fingerprinted page still has the same `content_hash` (and text), the tuple is unchanged, and the Lead passes `compliance_api.assert_contactable(NewLeadSubject(...), "email", "cold")` → `applied`; else `stale`, nothing written.
  - `BatchSucceeded(LlmInvalid)` → the run's `invalid` outcome recorded, item `failed`.
  - `LlmRefused`, `LlmTruncated`, `BatchLost`, or no result for the item → that step re-run directly at `soon` (its own budget check; refused → `failed`) → `rerun`.
  - Once every item of the batch is terminal → `delete_batch`, then `deleted_at` and status `deleted`.
- **Jobs:** `submit_bulk_ai` and `poll_ai_batches`, bulk lane, concurrency key `ai_batches`, retry 3, group `other`; handlers call each registered batch owner (`build_job_types(..., ai_batches=None)` provider of owners; only research's `ResearchBatches` now).
- **Contract tests:** every `submit_batch` request body has no `fallbacks`/beta header, `ttl` `1h` on the cached prefix and `output_config.format`, one subject per item, while a direct Opus `draft_sequence` request still carries the fallbacks; custom_id length test with the longest name in `routes.CANDIDATES` and rev 10^12−1; AD-16 caller `research.batch_apply` with a scenario.

**Never:** a call that waits or sleeps on a batch; storing prompt, page or response text (only ids, hashes, codes); drafting batch items or `apply_draft_sequence_result` and `draft_deadline` (4.18); fallbacks on a batch request; cancelling batches; a new dependency; a test reaching Claude.

### Decisions made overnight (pending user review)
1. A `pending` item status is added (items queued before the daily submit); requests are rebuilt at submit time, so the fingerprint is the one sent.
2. Research's "still contactable" is the Lead's domain gate (`NewLeadSubject`); people items keep `add_contact`'s per-Contact gate.
3. A refused or truncated succeeded item is re-run directly (the direct path has the doubled `max_tokens` retry); an invalid one is `failed`, as the direct path stores nothing for it.
4. A re-run whose budget check refuses is `failed` (no call), so a batch can still be deleted.
5. A crash between `fetch_batch` and the item updates may meter an ended batch twice on the next poll (over-counting errs toward the budget); a crash after `submit_batch` but before its row is written leaves an unrecorded batch, logged by nothing — accepted, as the window is one local write.
6. Extraction urgency follows the job lane via `chain_urgency`, matching Story 2.16 on main.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Queue | `extract_lead` on bulk lane | 2 pending items, no call | budget refused → deferred, no item |
| Direct | interactive lane | direct call at `soon`, no item | as today |
| Submit none | no pending | no batch, no port call | N/A |
| Budget | AI spend at 100% | items stay pending | N/A |
| Applied | ended, `LlmOk`, page unchanged | Evidence stored, `applied` | N/A |
| Stale | page hash changed / Lead merged / domain suppressed | nothing written, `stale` | N/A |
| Lost | errored/canceled/expired/refused | direct call at `soon`, `rerun` | AdapterError → job retries, item stays `submitted` |
| Settled | every item terminal | `delete_batch`, `deleted_at` | 404 → treated as deleted |
| Re-poll | same ended batch again | settled items untouched | N/A |

</frozen-after-approval>

## Code Map

- `nextron/ports/llm.py` -- port + result types; `tests/fakes/llm.py` `ScriptedLlm`.
- `nextron/adapters/ai/__init__.py` (`ClaudeGateway.run`, `_Tally`, `_parsed`, `_status_failure` to reuse), `prompting.py` (`build_request`, `FALLBACK_*`), `pricing.py` (`cost_micros`), `routes.py` (`find`, `CANDIDATES`, `batchable`). SDK 2.8.0: `client.messages.batches.{create,retrieve,results,delete}`; results entries `.custom_id`, `.result.type`, `.result.message`.
- `nextron/domain/ai.py` -- add `chain_urgency(lane)` exactly as main's Story 2.16 (`Urgency.SOON if lane == Lane.INTERACTIVE else Urgency.BULK`); `check_blocks`, `RouteTuple.key`; `nextron/domain/budget.py` `AiCall(batch=...)`, `BudgetClass`, `allow`.
- `nextron/app/research/extraction.py` (`extract_lead` :255, `_prepare`, `_apply`, `_finished`, `_record`) and `people.py` (`extract` and its prepare/apply) -- split the call step so batch submit (prepare without recording), batch apply (`apply_<route>_result`) and the direct rerun reuse the same code. `nextron/jobs/handlers.py:381` `extract_lead` passes `lane=job.lane`. Tests to keep passing: `tests/app/test_signal_extraction.py`, `test_people_extraction.py` (direct-path tests move to the interactive lane; add bulk-lane tests).
- New `nextron/app/research/batches.py` `ResearchBatches(clock, meter, llm, extractions, people)` (`submit_bulk()`, `poll()`).
- `nextron/db/owners.py` (`SHARED_WRITERS`, `TABLES`), `pii_registry.py`, `db/models/` (new `ai_batches.py` model module), migration `nextron/db/alembic/versions/0049_4_17_ai_batches.py`, `nextron/db/ai_batches.py`; c1 contract in `pyproject.toml` must allow app → `nextron.db.ai_batches` (like `eventlog`).
- `nextron/domain/settings_schema.py` (`bulk_ai_submit_at`, pattern of `backup_time`), `nextron/jobs/triggers.py` (`POLL_AI_BATCHES`, `SUBMIT_BULK_AI` exist), `nextron/main.py` (`build_job_types`, `build_triggers` ~:1953 `DailyAt` pattern, service wiring, `enqueue_only_job_types`).
- Pins: HEAD ×3 (`tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`), chain probes, `test_source_tree` `FILES`, `tests/contracts/test_job_contracts.py`, `tests/contracts/test_ad16_callers.py`, trigger/health job-type lists, `tests/contracts/test_lockfile.py` untouched.

## Tasks & Acceptance

**Execution:**
- [x] Domain `ai_batch.py` + `chain_urgency` + unit tests (custom_id bounds, parse, fingerprint, statuses).
- [x] Port types, gateway `submit_batch`/`fetch_batch`/`delete_batch`, `build_batch_params`, batch pricing; fake; unit tests with a fake SDK client (`tests/unit/test_ai_gateway.py` style).
- [x] Migration, models, shared writer, owners/PII, row-owner contract test.
- [x] Research: lane-aware steps, pending items, `apply_extract_signals_result`, `apply_extract_people_result`, direct rerun; `ResearchBatches`.
- [x] Setting, job types, handlers, triggers, main wiring; pins.
- [x] `tests/app/test_ai_batches.py` -- the I/O matrix end to end with `ScriptedLlm`; AD-16 scenario; request-body contract test.
- [x] `nextron-engine/README.md` -- Story 4.17 section.

**Acceptance Criteria:**
- Given an ended batch with mixed results, when `poll_ai_batches` runs twice, then each item is settled exactly once, `ai_usage` holds batch-flagged rows at half price, and the batch is deleted.
- Given any batch request body, then it has no `fallbacks`, a 1-hour cached prefix and the schema in `output_config.format`.

## Implementation Notes

**What was built (files).**
- Domain: `nextron/domain/ai_batch.py` (new: `BatchOwner`, `BatchStatus`, `ItemStatus`, `TERMINAL`, `custom_id`/`parse_custom_id`, `Fingerprint` with canonical JSON and `prompt_version`, `DEFAULT_BULK_AI_SUBMIT_AT`, the two job names and `CONCURRENCY_KEY`); `nextron/domain/ai.py` (`chain_urgency(lane)`, verbatim from main's Story 2.16); `nextron/domain/job.py` (`GLOBAL_CONCURRENCY_KEYS = {"ai_batches"}` accepted by `is_concurrency_key`; `JobType.concurrency_key`); `nextron/domain/settings_schema.py` (`bulk_ai_submit_at`, TIME, Engine section, default 14:00, `timezone_key` sender_timezone, appended last in `Key` and `SCHEMA`).
- Port/adapter: `nextron/ports/llm.py` (`BatchRequest`, `BatchLostKind`, `BatchSucceeded`, `BatchLost`, `BatchInProgress`, `BatchEnded`, `check_batch_items`, `check_batch_id`/`BATCH_ID_PATTERN`, three Protocol methods); `nextron/adapters/ai/__init__.py` (`submit_batch`, `fetch_batch`, `delete_batch`, `_Tally(batch=True)`, `_batch_answer`, `_BatchErrors`, `LLM_BATCH_ERRORED`; `run` now builds its client through `_anthropic()`); `prompting.py` (`_params` shared by `build_request` and the new `build_batch_params`, `BATCH_CACHE_CONTROL` ttl 1h, never fallback keys); `pricing.py` (`cost_micros(..., batch=False)`: `ceil(total / 2e6)` for a batch); `tests/fakes/llm.py` (batch methods, `end_batch`, `batches_submitted`/`fetched`/`deleted`, error queues).
- DB: migration `0049_4_17_ai_batches` (down `0048_4_15_draft_failed`), `nextron/db/models/ai_batches.py` (`AiBatchRecord`, `AiBatchItemRecord`; extra checks `ck_ai_batches_times`, `ck_ai_batch_items_batch` and `ck_ai_batch_items_rev`), shared writer `nextron/db/ai_batches.py` (every function takes a `BatchOwner`; `queue_item` is insert-if-absent on `(owner, route, target, rev)`; `settle` only moves a `pending`/`submitted` item, so a re-poll can't settle twice), `owners.py` (`SHARED_WRITERS` + both tables), `pii_registry.py` (all `none`), pyproject c1 comment.
- Research: `extraction.py` (`extract_lead(..., lane=None)` -> `signals_step(lead, run, urgency)` returning a `Step`; bulk queues the item after the budget check and unknown/merged skip; `batch_request`, `apply_extract_signals_result`, `record_invalid`, `rerun`, `_restore`, `_call`; shared helpers `queue_item`, `log_queued`, `still_contactable`, `restore_pages`, `fingerprint_of`, `Settled`, `BatchPlan`), `people.py` (same shape: `people_step`, `apply_extract_people_result`, ...), new `batches.py` (`ResearchBatches.submit_bulk/poll/schedule`, `BulkAiSchedule`), `queries/playbook.py` (`tuple_by_key(route, key)`), `api.py` docstring pointer.
- Jobs/main: `handlers.py` (`extract_lead` passes `lane=job.lane`; `BatchingAi`, `submit_bulk_ai`, `poll_ai_batches`), `main.py` (two job types: bulk only, priority 30, 30 min / 2 h, retry 3, `other`, concurrency key `ai_batches`; `build_job_types(..., ai_batches=None)` -> `no_ai_batches` raising like the other unwired services; `build_triggers(..., bulk_ai_schedule=None)`: `POLL_AI_BATCHES` `Every(15 min, at_startup=True)` then `SUBMIT_BULK_AI` `DailyAt(..., fallback 14:00 UTC)`; `read_bulk_ai_schedule(units, owners)`; `build_app` wires `(ResearchBatches(clock, meter, llm, extractions, people),)`).
- Tests: new `tests/unit/test_ai_batch_domain.py`, `tests/unit/test_ai_batch_gateway.py` (MockTransport: request bodies, the Opus direct-vs-batch fallbacks contrast, errors, outcomes, batch-rate metering, 404 delete, the fake), `tests/app/test_ai_batches.py` (the I/O matrix with `ScriptedLlm`, plus the acceptance criterion through the real `ClaudeGateway` and the real `UsageMeter` into `ai_usage`), `tests/contracts/test_ai_batch_owners.py` (row-owner scan with probes); AD-16 manifest entry `research.batch_apply` with its scenario; pins (HEAD x3, chain probes 0050/0051, `test_source_tree` FILES, job-type lists in `test_job_contracts`, `test_engine_health`, `test_runner_lifespan`, `test_health_page`, trigger list, settings key lists, and the full Settings form post in `tests/app/test_telegram_secrecy.py`, found by the full-suite gate). Existing direct-path tests in `test_signal_extraction.py`/`test_people_extraction.py`/`test_ad16_callers.py` now run on the interactive lane (urgency `soon`).
- `nextron-engine/README.md`: "Bulk AI work through Message Batches".

**Behaviour notes.**
- A queued (bulk) people step enqueues no next chain stage: `check_job_boards` follows the run the item later records (`applied` via `_apply`, `failed` via `record_invalid`, a re-run, or the `no_pages`/`no_activation` run recorded at submit).
- The batch's `submitted_at` (submit-time now) is the recorded run's `started_at`, so the next extraction's "changed since the last ok run" and the "no newer ok run" re-check line up.
- An item's apply re-reads the row inside its own transaction and passes over one settled meanwhile.
- `ResearchBatches` groups requests by tuple key, so one route normally makes one batch.

### Decisions made overnight (implementation time, pending user review)
7. `fetch_batch` takes the batch's route tuple as well (`fetch_batch(batch_id, route_tuple)`; port, gateway and fake): reading a result "by the same outcome rules as run" needs the candidate's output schema, and metering needs the tuple key, neither of which a batch id carries. The poll finds the tuple by the key its row stores (`queries.playbook.tuple_by_key`: the active tuple, else a stored tuple row); when none builds any more, every open item is `failed` (logged `ai_batch_tuple_missing`) and the row is marked `deleted` locally with no provider call (review #3).
8. A `stale` people item writes nothing, so it doesn't advance the research chain either; the newer work that made it stale (a newer run or a recrawl's item) carries the chain on. A tuple change while a batch is in flight therefore stalls that Lead's selection until its next crawl.
9. The `ai_batches` concurrency key is a new global key in `domain.job` (`GLOBAL_CONCURRENCY_KEYS`) carried by the job type (`JobType.concurrency_key`), since trigger ticks are enqueued without one; `JobQueue.enqueue` uses the type's key when the caller names none.
10. Submit priority/runtime were unspecified: 30, 30 minutes (submit) and 2 hours (poll, which may re-run many lost items directly).
11. The `submit_bulk_ai` catch-up (`DailyRun.catch_up`) is "a pending item was queued before the latest daily slot", mirroring the backup and domain-check schedules.
12. A batch request's `custom_id` is the item's stored one; `rev` is the crawl run's own ordinal among the Lead's crawl runs (`crawl_repo.run_ordinal`, by `started_at` then id; review #15), so a second `extract_lead` for the same crawl adds no item (insert-if-absent) and a recrawl adds a newer one.
13. An invalid result whose pages or tuple changed still records its `invalid` run (pages_sent from the fingerprint, actor from the key's prompt version, pages_truncated 0).

14. The "route is batchable" caller check lives in the port (`nextron.ports.llm.check_batchable`, `ValueError`) because adapters may raise only `AdapterError` (`test_error_contracts`); gateway and fake both call it.
15. `ScriptedLlm` takes a keyword `batch_prefix` (default `msgbatch_fake_`) so two fakes on one database (the AD-16 scenario's two runs) don't collide on the unique `provider_batch_id`.

**Review patches (pass 1).**
- #1: `fetch_batch(..., *, metered=True)` on port, gateway (`_Tally(metered=False)` records no `AiCall`) and fake (`fetched_metered`); `_poll` passes `metered=False` once the batch row is `ended`. Test through the real gateway + `UsageMeter`: one batch `ai_usage` row per result after a retryable re-run error and a second poll.
- #2: `_settle` catches an `AdapterError` from `rerun`: non-retryable -> item `failed`, logged `ai_batch_rerun_failed` (`item_id`, `route`, `error_code`); retryable still propagates. Test.
- #3: a missing tuple fails the open items and marks the row `deleted` locally with no fetch/delete (logged `ai_batch_tuple_missing`). Tests: the stored-tuple fallback after another tuple is activated (fetched, result `stale`), and an unresolvable key (items `failed`, no provider call).
- #4: gateway `fetch_batch` returns `BatchEnded({})` on HTTP 404 from retrieve (`_BatchErrors.missing`); `poll()` logs a non-retryable `AdapterError` per batch (`ai_batch_poll_failed`: `batch_row_id`, `error_code`) and polls the next; retryable propagates. Tests (gateway 404; one failing batch doesn't block the next; retryable still raises).
- #8: index `ix_ai_batch_items_batch_row_id` added in migration 0049 (in place) and the model; no index pin test existed.
- #11: `people.py` docstring says Story 4.17's batch steps; the adapter uses `nextron.ports.llm.BATCH_ID_PATTERN` (its own `BATCH_ID` removed); `main.read_bulk_ai_schedule` takes `Sequence[ScheduledBatching]` (a new `main` protocol with `schedule() -> BulkAiSchedule`).
- #15: `queue_item`'s `rev` is `crawl_repo.run_ordinal(lead, run)` (new repo query; falls back to `run_count` when the run isn't the Lead's). Test: two crawl runs before either job -> two items with revs 1 and 2 and their own context ids; the submit keeps only rev 2.
- #17: `test_jobqueue` tick of a type with `concurrency_key="ai_batches"` stores that key; `"ai_batches"` added to the valid-key list in `test_job_domain`.
- #18: test: an invalid result after the pages changed records `invalid` runs with the fingerprint's actor and page count, and the people item enqueues one `check_job_boards`.
- #19: test: `read_bulk_ai_schedule` with stub owners returns the time and timezone, `catch_up=True` when one owner missed, and raises with no owner.

**Verification (implementer, targeted only).** `tests/app/test_signal_extraction.py`, `test_people_extraction.py`, `test_contact_form_only.py`, `test_job_boards.py`, `test_primary_contact.py`, `tests/unit/test_ai_gateway.py`: 292 passed. `tests/app/test_ai_batches.py` (26), `tests/unit/test_ai_batch_gateway.py`, `tests/unit/test_ai_batch_domain.py`, `test_job_domain`, `test_jobqueue`, `test_engine_health`, `test_ai_domain`, `test_website_render`, `test_health_page`, `test_settings_page`, `test_settings_schema`: 1114 passed. `test_serve_process`, `test_step_drafting`, `test_ai_test_runs`, `test_ai_prompting`, `test_ai_grounding`: all pass (after the serve-process trigger pins). `test_runner_lifespan`, `test_triggers`: 110 passed. `tests/contracts`: 1949 passed, 7 skipped. `lint-imports`: 29 kept, 0 broken. The full suite was not run (the orchestrator's gate).

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 29 raw, 19 after dedup; 10 patched, 3 deferred, 6 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1+EC1+EC10+VG-o1: an ended batch is re-metered on every re-poll while any item is still `submitted` (e.g. after a retryable re-run error) | medium | `_poll` re-fetches whenever an item is open; `fetch_batch` meters every entry on each fetch; decision 5 covers only the crash window | patch: `fetch_batch(..., *, metered=True)`; the poll passes `metered=False` once the batch row is `ended`; test through the real gateway + meter |
| 2 | BH2+EC2+EC11+VG-o2: a non-retryable `AdapterError` in a re-run leaves the item `submitted` forever (repeated paid calls, batch never deleted) | medium | `_settle` lets every `AdapterError` propagate | patch: non-retryable → item `failed` (logged); retryable still propagates; test |
| 3 | BH3+VG2: tuple-missing path calls `delete_batch` on a batch that may still be processing (400 every tick); `tuple_by_key` fallback and the missing branch untested | medium | `_poll` deletes without fetching | patch: on a missing tuple, fail the items and mark the row deleted locally with no provider call (logged); tests for the stored-tuple fallback after re-activation and for the missing branch |
| 4 | BH4+EC3+EC4: one failing batch (incl. `retrieve` 404 after results expire) blocks every newer batch | medium | `poll()` sums `_poll` with no isolation; 404 on retrieve → `llm_rejected` each tick | patch: `fetch_batch` 404 → `BatchEnded({})` (items re-run); `poll()` logs a non-retryable `AdapterError` per batch and continues; tests |
| 5 | BH5: `_BatchErrors` turns local DB/metering failures into `llm_unexpected` | false | `UsageMeter.record` never raises (module contract: it logs and returns) | reject |
| 6 | BH6+EC6: one `allow` per route lets a large pending set overshoot the budget; in-flight batch cost isn't counted | medium | true; the spec's rule is "allow before submitting" only; a cost estimate is new design | defer |
| 7 | BH7: `submit_bulk` rebuilds every request inside one write transaction | low | once a day, tens of Leads; reads plus small writes | reject |
| 8 | BH8: no index on `ai_batch_items.batch_row_id`; no retention for settled rows | low | `items_of` scans by the owner/status index; tables grow slowly | patch: add `ix_ai_batch_items_batch_row_id` (migration + model); retention deferred (with #9) |
| 9 | BH9: nothing operator-visible for a stuck batch, long-pending items or a missing tuple | medium | log lines only; no health source | defer |
| 10 | BH10: a `stale` people item with no newer work leaves that Lead's chain stalled until its next crawl (decision 8) | medium | true; a re-run/next-stage policy for stale items is a design change | defer |
| 11 | BH11: wrong story in `people.py` docstring; duplicate `BATCH_ID` regex in the adapter; `read_bulk_ai_schedule` typed to `ResearchBatches` | low | direct corrections | patch |
| 12 | BH12: pending items never expire; catch-up fires every start while the budget refuses | low | the refused submit costs nothing | reject |
| 13 | EC5: crash after `submit_batch` before the row is written leaves an orphan billed batch | low | accepted in decision 5 | reject (would change the approved spec) |
| 14 | EC7: crash after a re-run's commit but before settle re-runs the item again | low | one-off crash window, one extra call | reject |
| 15 | EC8: `rev` = the Lead's current crawl run count, so two runs whose jobs run late collide and the item names the older run | low | `_queue` computes rev at job time; insert-if-absent drops the second | patch: rev = the run's own ordinal among the Lead's crawl runs; test |
| 16 | EC9: a re-run can race a live `extract_lead` on the same Lead | low | rare; apply re-checks | reject |
| 17 | VG1: the type-level `ai_batches` concurrency key reaching the job row is untested | medium | reverting the fallback fails no test | patch: jobqueue test via `enqueue_tick`; add `ai_batches` to the valid-key list |
| 18 | VG3: `record_invalid`'s changed-pages/tuple branch untested (people next-stage enqueue) | medium | dropping `_enqueue_next_stage` fails no test | patch: test |
| 19 | VG4: `read_bulk_ai_schedule` wiring untested (setting, timezone, catch-up) | medium | the serve test sees only the fallback values | patch: unit test with stub owners |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- all pass
- `uv run pytest -q -n 2 tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept
