---
title: 'Story 1.11: Schedule periodic triggers and write the runner heartbeat'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: 'f2083e98c361044ead12fa2bbd33ffc1cb9cc305'
review_loop_iteration: 0
story_key: '1-11-schedule-periodic-triggers-and-write-the-runner-heartbeat'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing fires periodic work, and nothing proves the engine is alive.
- Every later polling stage (sends, mailbox syncs, unsubscribe polls, backups) needs a trigger. The trigger must catch up once after the PC sleeps, never once per missed firing.
- The supervisor (`run.ps1`, Story 1.21) restarts the engine only when `<data dir>\heartbeat` goes stale. A dead lane thread or a hung handler (Story 1.10 review item BH5) must therefore stop the heartbeat.

Requirements: AD-2, AD-10, AD-20, NFR-14.

**Approach:**
- **Triggers.** The closed AD-10 trigger enum and a frozen trigger registry (`nextron/jobs/triggers.py`), built empty in `main.py`.
- **Scheduler.** An APScheduler `AsyncIOScheduler(timezone=UTC)` wrapper (`nextron/jobs/scheduler.py`). It is created in the lifespan after the runner, and each firing only enqueues.
- **Heartbeat.** A `runner_heartbeat` table (owner `ops`, migration `0004_1_11_heartbeat`) and the `ops` heartbeat service. A heartbeat thread beats every 30 s while the runner, its jobs and the scheduler are healthy.

The acceptance criteria are Story 1.11 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Triggers** (`nextron/jobs/triggers.py`; imports only the domain and the stdlib).
  - **`Trigger(StrEnum)`** is the AD-10 table, in table order:
    - `sync_mailbox`, `send_due_messages`, `reconcile_sends`, `poll_unsubscribes`, `poll_ai_batches`;
    - `expire_approvals`, `refresh_due_queue`, `check_domains`, `recompute_tiers`, `recheck_leads`;
    - `recontact_due`, `apply_retention`, `purge_place_coords`, `purge_page_text`, `submit_bulk_ai`;
    - `daily_selection`, `backup_db`, `restore_test`, `check_recovery`.
  - **`Every(period, *, at_startup=False)`**, frozen.
    - It raises `TypeError` or `ValueError` unless `period` is a `timedelta` of whole seconds from 1 min to 31 days, and `at_startup` is a `bool`.
    - `grace` (the misfire grace) equals `period`.
  - **`TriggerSpec(trigger, schedule)`**, frozen. Its `job_type` is `trigger.value` (decision 1).
  - **`Triggers(job_types)`.** It raises `TypeError` unless given a `JobTypes`, and `RuntimeError` unless that registry is frozen.
    - **`register_trigger(trigger, schedule)`.** Each of these raises:
      - `TypeError` unless `trigger` is a `Trigger` member (a plain `str` is refused) and `schedule` is an `Every`;
      - `UnknownJobType` unless a job type named `trigger.value` is registered;
      - `ValueError` for a duplicate;
      - `RuntimeError` after `freeze()`.
    - It also has `freeze()`, `frozen` and `specs()` (in registration order).

- **The queue.** `JobQueue.enqueue_tick(job_type) -> Enqueued` is a new `@use_case`. In one transaction it calls `enqueue(job_type, scope="global", discriminator="tick", payload={"v": 1})`, with the type's default lane and `run_after` now. A live job with that key absorbs the firing (`created=False`).

- **Scheduler** (`nextron/jobs/scheduler.py`). It imports `apscheduler`, `nextron.app.jobqueue.api`, `nextron.jobs.triggers`, the domain and the stdlib.
  - **Constructor.** `Scheduler(units, queue, triggers, *, probe_timeout=10.0)`. It raises `TypeError` unless `triggers` is a `Triggers`, and `RuntimeError` unless that registry is frozen.
  - **`start()`.** It runs on the event loop (from the lifespan), once; a second call raises `RuntimeError`.
    1. Creates `AsyncIOScheduler(timezone=UTC)`.
    2. For each spec, calls `add_job(self.fire, IntervalTrigger(seconds=<period>, timezone=UTC), args=(trigger,), id=<name>, coalesce=True, misfire_grace_time=<period in s>, max_instances=1)`. With `at_startup`, it adds `next_run_time=<now>`.
    3. Starts the scheduler.
    4. Logs `scheduler_started` with `triggers` (the count).
  - **`fire(trigger)`.** It runs as a sync APScheduler job on the loop's default executor thread, never on the loop.
    - It opens one unit of work and calls `queue.enqueue_tick(trigger.value)`.
    - Any exception logs `trigger_failed` (ERROR, `trigger`, `code`, traceback) and isn't re-raised.
    - It runs no handler.
  - **`async stop()`.**
    1. Calls `shutdown(wait=False)`.
    2. Yields to the loop once, so APScheduler's deferred shutdown runs.
    3. Logs `scheduler_stopped`.

    It does nothing before `start()`, or when called again.
  - **`alive()`.** It is called off the loop's thread, and raises `RuntimeError` on it. It is true only when all of these hold:
    - the scheduler has started and hasn't stopped;
    - APScheduler is running;
    - the loop runs a `call_soon_threadsafe` probe within `probe_timeout`.
  - **`wakeup()`** (test hook): processes due triggers now, as APScheduler's timer does when the PC wakes.

- **Runner.** `Runner.overrunning() -> tuple[Claim, ...]` returns the held claims whose run has passed their `max_runtime` on the runner's monotonic clock, the rule `job_overran` uses (BH5).

- **The table.** `HeartbeatRecord` (`nextron/db/models/ops.py`), table `runner_heartbeat` (decision 7). Its columns:
  - `id`: always `00000000000000000000000000`;
  - `pid`: Integer;
  - `started_at` and `beat_at`: `UtcDateTime`.

  Checks: `ck_runner_heartbeat_singleton` (`id = '00000000000000000000000000'`) and `ck_runner_heartbeat_pid` (`pid > 0`).
  - **Migration** `versions/0004_1_11_heartbeat.py`, revising `0003_1_10_jobs`. Its `downgrade()` raises `NotImplementedError`.
  - **Maps.** Every column belongs to `ops` in `owners.py` and is `none` in `pii_registry.py`.
  - **Repository** (`nextron/db/repos/ops/heartbeat.py`): `HeartbeatRow`, `get()`, and `save(*, pid, started_at, beat_at)`, which updates the row or inserts it when it is missing.
  - **Query** (`nextron/app/queries/ops.py`): `heartbeat() -> HeartbeatRow | None`.

- **`ops.beat`.** `Heartbeat(clock, heartbeat_file)` in `nextron/app/ops/api.py` has `@use_case beat() -> None`.
  1. In one transaction, it saves `beat_at = now`, `pid = os.getpid()` and `started_at`, which is the time of this object's first beat (decision 8).
  2. After the commit, it touches `heartbeat_file`, creating it if it is missing (decision 6).

  Any failure raises to the caller.

- **The heartbeat writer** (`nextron/jobs/heartbeat.py`). It imports `nextron.app.ops.api` and the stdlib, and uses local Protocols for the runner and the scheduler.
  - **Constructor.** `HeartbeatWriter(units, heartbeat, runner, scheduler, *, interval=30.0)`.
  - **`start()`** runs one tick synchronously, then starts the daemon thread `nextron-job-heartbeat` in a fresh `contextvars.Context()`. That thread ticks every `interval` on a stop event. A second `start()` raises `RuntimeError`.
  - **`beat_once()`** is one tick, and also the test hook.
    - **Withholding.** The tick withholds the beat for the first reason that holds:
      1. `runner_down`: `not runner.alive()`;
      2. `job_overran`: `runner.overrunning()` is non-empty;
      3. `scheduler_down`: `not scheduler.alive()`.
    - **Beating.** Otherwise it calls `heartbeat.beat()` in one unit of work.
    - **Errors.** It never raises.
  - **Logs, written only when the outcome changes:**
    - `heartbeat_withheld` (WARNING, `reason`);
    - `heartbeat_failed` (ERROR, `code`, traceback);
    - `heartbeat_resumed` (INFO), on the first beat after either of them.
  - **`stop()`** sets the event and joins the thread (at most 20 s). It does nothing before `start()`, or when called again.

- **Composition** (`nextron/main.py`).
  - **The name.** `HEARTBEAT_FILE_NAME = "heartbeat"`.
  - **`build_triggers(job_types)`** builds the frozen, empty `Triggers`. It is the only code that constructs `Triggers` or calls `register_trigger`.
  - **`build_app`** builds the runner, the scheduler, `Heartbeat(clock, config.data_dir / HEARTBEAT_FILE_NAME)` and the writer, and passes `lifespan=engine_lifespan(runner, scheduler, writer)`. Building starts nothing.
  - **`engine_lifespan`** replaces `runner_lifespan`.
    - **Startup:** `to_thread(runner.start)`, then `scheduler.start()` on the loop, then `to_thread(writer.start)`.
    - **Shutdown** runs in reverse: `to_thread(writer.stop)`, `await scheduler.stop()`, `to_thread(runner.stop)`.
    - Each stop runs even when a later start or the server failed.
  - **Docs.** The module docstring covers triggers and the heartbeat.

- **Logging.** `logconfig.SDK_LOGGERS` gains `apscheduler`, kept at WARNING (decision 9).

- **Tests.** Simulate a sleep by moving APScheduler's clock forward, then calling `wakeup()`. Use `FrozenClock` and injected `monotonic` functions. Make no real wait longer than 0.05 s per step, polling at most 2 s.

**Never:**
- **Out of scope:**
  - real job types, handlers or trigger registrations;
  - daily, monthly or Operator-time schedules;
  - per-inbox fan-out;
  - `run.ps1` (1.20, 1.21);
  - ops alerts (1.12);
  - the health panel, and the 2-minute "running" rule (1.15).
- **No new parts.** Add no dependency, port or adapter. Triggers and the heartbeat read neither the keyring nor the network.
- **Personal data and audit rows.** The heartbeat row, the file and the logs hold no personal data. The heartbeat writes no `events` rows.
- **What not to edit.** Don't edit the vendored HTMX, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`. In `deferred-work.md`, remove only the Story 1.10 BH5 entry.

### Decisions made overnight (pending user review)

1. **A trigger enqueues the job type with its own name,** so `send_due_messages` fires the `send_due_messages` job. A trigger can be registered only after its job type. No job type exists yet, so the registry ships empty, and the scheduler runs with no triggers until a story adds one.
2. **A firing enqueues one global `tick` job.** While that job is still queued or running, a firing adds nothing. Per-inbox triggers (`sync_mailbox`) and data-dependent ones (`check_recovery`) add their own enqueue use case in their stories.
3. **Interval schedules only, for now.**
   - **The schedule.** `Every`, optionally firing at startup. Calendar schedules (daily at a time, monthly, Operator time) arrive with their first trigger, starting with Story 1.16's `backup_db`.
   - **Catch-up.** The misfire grace equals the period, so the one coalesced catch-up after a sleep always runs.
   - **Restarts.** Schedules are kept in memory, so a restart starts each interval afresh.
4. **The heartbeat has its own thread.** It beats once at startup, then every 30 s.
5. **The heartbeat is withheld when:**
   - any lane thread or the keeper is dead (the AC);
   - a running job has passed its `max_runtime` (BH5);
   - the scheduler stopped, or the event loop doesn't answer within 10 s.

   The supervisor restarts a hung engine about 5 minutes later.
6. **The row is written first, and the file touched after the commit.** A database that keeps failing therefore also leads to a restart.
7. **`runner_heartbeat` holds exactly one row,** with a fixed all-zero ULID id that a check enforces.
8. **`started_at` is when this process first beat:** the engine's start, within a second.
9. **APScheduler's own INFO lines are dropped,** because it logs two per firing. A successful firing logs nothing: the job's creation event already records it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Register | `Trigger.SEND_DUE_MESSAGES`, `Every(1 min)`, with type `send_due_messages` registered | in `specs()` | N/A |
| Bad register | `"send_due_messages"` as a plain str; a type not registered; a duplicate; after `freeze()`; `Every(30 s)` or `Every(32 days)` | refused | `TypeError`, `UnknownJobType`, `ValueError` or `RuntimeError` |
| Firing | a due trigger, with no live job for its key | one `queued` job, key `<type>:global:tick`, payload `{"v":1}`; no handler runs | N/A |
| Absorbed | the trigger fires while its job is `queued` | no second row | N/A |
| Sleep | a 1-min trigger; APScheduler's clock jumps 5 h | `fire` is called once; the next firing is due 1 min later | N/A |
| Startup | `Every(2 min, at_startup=True)` | fires right after `start()`, then every 2 min | N/A |
| Fire error | `enqueue_tick` raises | `trigger_failed` is logged; later firings still run | not re-raised |
| Beat | healthy runner and scheduler | the row is inserted, then updated (same `started_at`, new `beat_at`, this pid); the file is touched | N/A |
| Dead lane | a lane thread has ended | no beat; `heartbeat_withheld` `runner_down` is logged once | N/A |
| Hung handler | a claim held past its `max_runtime` | no beat (`job_overran`) until the handler returns, then `heartbeat_resumed` | N/A |
| Stuck loop | the event loop doesn't answer the probe within `probe_timeout` | no beat (`scheduler_down`) | N/A |
| Beat fails | the database is locked, or the file can't be touched | `heartbeat_failed` is logged once per streak; a failed row write touches no file | never raises |

</frozen-after-approval>

## Code Map

- **The runner** (`nextron-engine/nextron/jobs/runner.py`). `_Held` (`started`, `claim.max_runtime`) and `_log_overruns` already compute overruns; `overrunning()` reuses that rule under `self._lock`. `alive()` stays as it is.
- **The queue** (`nextron-engine/nextron/app/jobqueue/api.py`): `JobQueue.enqueue` is a `@command`, so `enqueue_tick` calls it inside its own `current().transaction()`. `GLOBAL_SCOPE` and `TICK` are in `nextron/domain/job.py`.
- **Units of work** (`nextron-engine/nextron/db/uow.py`). A unit is bound to its ContextVar and its thread. APScheduler's `run_in_executor` threads don't copy the context, which is fine because `fire` opens and closes its own unit.
- **APScheduler 3.11.3** (`.venv/Lib/site-packages/apscheduler/`):
  - `AsyncIOScheduler.start` calls `asyncio.get_running_loop()`.
  - `shutdown` and `wakeup` are deferred through `call_soon_threadsafe`.
  - `AsyncIOExecutor` runs a sync job with `run_in_executor(None, …)`.
  - The time sources are the `datetime.now` calls in `schedulers/base.py`, `executors/base.py` and `triggers/interval.py`. Tests patch those modules' `datetime` names with a subclass that reads a `FrozenClock`.
  - `run_job` loops over every run time it is given, so count `fire` calls, not job rows: the live key absorbs duplicates. A proof with `coalesce=False` (set with `modify_job`) shows that the 5-hour jump spans several firings.
  - On Windows, `time.monotonic` is QueryPerformanceCounter, which counts sleep, so loop timers fire promptly after a wake.
- **Patterns to copy:**
  - the model, `db/models/jobqueue.py` (checks through `CheckConstraint`, named by convention);
  - the migration, `db/alembic/versions/0003_1_10_jobs.py` (`op.f(...)` for check names; `downgrade` raises `NotImplementedError`);
  - the repository, `db/repos/jobqueue/jobs.py` (`current().session`, a frozen row);
  - the service, `app/setup/api.py`.

  Also add `models/__init__.py`'s import, and update the `db/repos/ops/__init__.py` and `app/queries/ops.py` docstrings.
- **The registry contract** (`nextron-engine/tests/contracts/test_job_contracts.py`): copy the `build_job_types` AST check and its proofs (`tests/contracts/support.py`) for `Triggers` and `register_trigger`, and extend the import check to `scheduler.py`, `heartbeat.py` and `triggers.py`. None of them may import `nextron.ports` or `nextron.db`, and only `scheduler.py` may import `apscheduler`. The schema-SQL test there already covers every table.
- **The lifespan** (`nextron-engine/tests/unit/test_runner_lifespan.py`): `drive_lifespan` and the fake runner (extend it with scheduler and heartbeat fakes). `build_app` tests need the data dir to exist (`load_bootstrap().data_dir.mkdir(parents=True)`).
- **Revision literals that move to `0004_1_11_heartbeat`:**
  - `HEAD` in `tests/contracts/test_persistence_contracts.py:72` and `tests/unit/test_migrations.py:36`;
  - the assert in `tests/app/test_serve_process.py:211`.

  The chain probes at `test_persistence_contracts.py:670-682` use `0004_*` and `0005_b`, so shift them up by one.
- **Other existing tests:**
  - `tests/contracts/test_source_tree.py` `FILES`;
  - `tests/unit/test_logconfig.py:241` (the `SDK_LOGGERS` set);
  - `tests/unit/test_dashboard.py:617` (the lifespan passthrough) must still pass.
- **Subprocess tests** (`tests/app/test_serve_process.py`, `engine_env` with the data dir `tmp_path/data`): the engine now writes `heartbeat` there. It reads no keyring or network, so the deferred subprocess guard stays deferred.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/db/models/ops.py`, `models/__init__.py`, `owners.py`, `pii_registry.py`, `alembic/versions/0004_1_11_heartbeat.py` -- the table, its maps and its migration.
- [x] `nextron-engine/nextron/db/repos/ops/heartbeat.py`, `nextron/app/queries/ops.py`, `nextron/app/ops/api.py` -- the repository, the query and `Heartbeat.beat`.
- [x] `nextron-engine/nextron/app/jobqueue/api.py` -- `enqueue_tick`.
- [x] `nextron-engine/nextron/jobs/triggers.py`, `jobs/scheduler.py`, `jobs/heartbeat.py`, `jobs/runner.py` (`overrunning`), and the `jobs/__init__.py` docstring.
- [x] `nextron-engine/nextron/main.py`, `nextron/logconfig.py` -- the wiring, `engine_lifespan`, `build_triggers`, and the quiet `apscheduler` logger.
- [x] `nextron-engine/tests/unit/test_triggers.py` -- the enum against a hard-coded AD-10 list, and against the spine's AD-10 table parsed from `../_bmad-output/.../ARCHITECTURE-SPINE.md` (skipped only when that file is absent); the `Every` and `Triggers` rules.
- [x] `nextron-engine/tests/unit/test_scheduler.py` -- with a real `AsyncIOScheduler`, a patched APScheduler clock and a recording queue:
  - the job settings (UTC, interval, `coalesce`, grace, `max_instances`);
  - the Sleep, Startup and Fire-error rows;
  - `fire` runs off the loop's thread;
  - with the real `JobQueue` on a temporary database, the Firing and Absorbed rows;
  - `alive()` before start, after stop, while the loop is blocked, and on the loop's thread;
  - `stop()` is idempotent.
- [x] `nextron-engine/tests/app/test_heartbeat.py` -- the ops service on a temporary database:
  - the Beat row;
  - the file is created, and its mtime moves;
  - the row stays committed when the touch fails;
  - a raw second row, or a `pid` of 0, is refused;
  - the query.
- [x] `nextron-engine/tests/unit/test_heartbeat_writer.py` -- with fakes, every withheld reason in its order, the logs only on change, failure and resumption, the thread ticking at once and then every interval, and `stop`. With a real `Runner`, the Dead-lane and Hung-handler rows.
- [x] `nextron-engine/tests/unit/test_job_runner.py`, `tests/app/test_jobqueue.py` -- `overrunning()` (injected `monotonic`), and `enqueue_tick`.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py` -- the start order runner → scheduler (on the loop's thread) → writer, the stop in reverse, the stops after a failed start, and the real `build_app` startup: the threads include `nextron-job-heartbeat`, and the row and the file exist.
- [x] `nextron-engine/tests/contracts/test_job_contracts.py` -- `build_triggers()` is frozen and empty; the registry AST check and its proofs; the import checks.
- [x] `nextron-engine/tests/app/test_serve_process.py` -- the running engine logs `scheduler_started`, writes `<data dir>\heartbeat`, and holds one `runner_heartbeat` row.
- [x] The existing tests in the Code Map, plus `nextron-engine/README.md`: a "Triggers and the heartbeat" section, and migration `0004` under Database.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- remove the Story 1.10 BH5 entry (resolved here).

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `serve`, when it starts, then the database is at `0004_1_11_heartbeat`, `nextron.log` holds `job_runner_started` and then `scheduler_started`, and `<data dir>\heartbeat` and the `runner_heartbeat` row are fresh within a second of startup.

## Implementation Notes

Choices the spec left open, made during implementation:

- **`Scheduler.wakeup()` is `async`** and runs on the loop. It calls APScheduler's `wakeup()` (deferred through `call_soon_threadsafe`) and yields once, so the due triggers have been submitted when it returns. Before `start()` or after `stop()` it raises `RuntimeError`.
- **`alive()` knows the loop's thread** as the thread that ran `start()`. Before `start()` it returns `False` from any thread.
- **`at_startup`** uses `next_run_time=datetime.now(UTC)` in `scheduler.py`, so the tests patch that module's `datetime` along with APScheduler's three.
- **`Scheduler.start()`** keeps the `AsyncIOScheduler` before starting it, so `stop()` can always shut it down.
- **`heartbeat_failed`'s `code`** is the error's class name. The writer imports only `nextron.app.ops.api` and the stdlib, so it can't read domain error codes. A health check that raises (for example `scheduler.alive()` on the loop's thread) counts as a failure, not a withheld beat.
- **The outcome that gates the logs** is `beat`, `failed` or the withheld reason. A change of reason (say `runner_down` to `job_overran`) logs `heartbeat_withheld` again. Failures with different errors are one streak.
- **`started_at`** is the time of the object's first `beat()` call, even when that call failed.
- **`Runner.overrunning()`** covers every held claim, a lost one included (as `job_overran` does), oldest run first. `_overran` holds the rule both use.
- **`engine_lifespan`** uses an `AsyncExitStack`. Each stop is registered only once its start returned, so a part whose own start raised isn't stopped, and a stop that raises doesn't stop the others running.
- **`JobQueue.enqueue_tick`** passes a read-only `{"v": 1}` payload (`TICK_PAYLOAD`).
- **Tests and `max_instances=1`.** A firing frees its slot only when its future's callback runs on the loop. The scheduler tests therefore wait for APScheduler's `EVENT_JOB_EXECUTED` (a public listener) before the next `wakeup()`, rather than for the `enqueue_tick` call. Otherwise a firing could be skipped as "maximum number of running instances reached".
- **The subprocess test** checks that the row's `pid` is positive, not that it equals `Popen.pid`. The venv's `python.exe` on Windows is a launcher that starts the real interpreter as a child.

- **Existing test changed beyond the Code Map:** `tests/app/test_environment_isolation.py`. Its list of files a running engine may create now includes `heartbeat`.
- **A real engine run** (as `dev`, on a throwaway data dir and port 18766) migrated to `0004_1_11_heartbeat`, logged `job_runner_started` and then `scheduler_started`, and wrote the heartbeat row and file about 2 ms later.

Review fixes (pass 1, P1–P4; see the Review Triage Log):
- **P1.** `test_by_default_the_thread_ticks_every_30_seconds` builds the writer with no interval, as `main.build_app` does, and checks that its thread waits exactly 30.0 s. `Rig` now uses the writer's default unless a test passes an interval.
- **P2.** `test_start_ticks_at_once_…` resets the `MARK` ContextVar it sets (`MARK.reset(token)`), so no later test depends on test order.
- **P3.** The jobs import-rule check walks `nextron/jobs` with `rglob`. `nextron/jobs/__init__.py` now has an empty rule, and relative imports resolve against each module's own package. `rule_coverage` reports both modules with no rule and rules whose module is gone. Two proofs cover a nested `handlers` package and nested relative imports.
- **P4.** The README "Triggers and the heartbeat" section and the `scheduler.py` and `triggers.py` docstrings were corrected and extended (docs only):
  - after a sleep, the next firing is the first slot after the wake (0 to one period later), and a wake just before a slot can log the catch-up as missed while that slot fires at once;
  - the wake mechanism: the loop's clock counts sleep, and Uvicorn wakes the loop every 0.1 s. This has not yet been observed on a real PC sleep (Story 1.20's spike);
  - an `Every` restarts from zero at each engine start, so long periods need `at_startup=True` or a calendar schedule;
  - a periodic job waiting out a retry backoff absorbs firings;
  - restart timing: about `max_runtime` + 5 minutes after a hung job started, and the restart interrupts every lane;
  - after a sleep, the heartbeat file looks stale until the next beat (the `run.ps1` side is deferred).
- **Also:** `logconfig.py`'s docstring was re-wrapped to the 100-column width.

**Final verification (after pass 1).**
- `uv run pytest -q`: 3134 passed, 2 skipped (`ai_eval`, the symlink test), in 141 s.
- `uv run lint-imports`: 29 kept, 0 broken.
- **Not verified here:**
  - a real PC sleep and wake;
  - a supervisor restart on a stale heartbeat (`run.ps1` is Stories 1.20 and 1.21);
  - a real trigger firing in production. No job type or trigger is registered yet, so the scheduler runs with none.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (13 findings), EC = edge-case hunter (9), VG = verification gap (1 gap and 2 other findings).
- **Counts:** 25 raw findings, 23 after deduplication (BH3 with EC2, and BH8 with EC7).
- **Outcomes:**
  - 9 findings patched, in 4 groups (P1–P4): 3 test fixes and 1 group of doc corrections (6 findings);
  - 1 deferred: BH1, the supervisor's wake race, which belongs to `run.ps1` (Stories 1.20 and 1.21);
  - 13 rejected.
- **No loopback.** No finding is an intent gap or a bad spec. Four rejections (BH3/EC2, BH4, BH8/EC7, EC4) are judgement calls Mac can revisit with the overnight decisions.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | VG1 (gap) | Nothing pins the heartbeat writer's 30 s default interval, which `main.build_app` uses. `Rig` passes its own 30.0, so a slip to 300 s passes every test, and the supervisor would then restart a healthy engine. | low | Pre-verified: `DEFAULT_INTERVAL` appears in no test, and the interval test only proves the interval is over 0.05 s. The fix is test-only. | P1 |
| B | EC9 | `test_start_ticks_at_once_…` sets the `MARK` ContextVar on the main thread and never resets it, so a later test that reads `MARK` there fails depending on test order. | low | Verified at `tests/unit/test_heartbeat_writer.py:261`: `MARK.set("caller")` without a token reset. pytest runs the tests on one thread, so a reordered run (`-k`, or a plugin) fails `test_a_healthy_tick_…`. The fix is a direct correction. | P2 |
| C | BH11 | The check that every jobs module has an import rule uses `glob("*.py")`, so a future `nextron/jobs/handlers/` package escapes the per-module import rule and the "only scheduler imports apscheduler" check. | low | Verified in `test_job_contracts.py`. The spine's source tree puts handlers under `jobs/`. The fix is a direct correction (`rglob`). | P3 |
| D | VG-o1 | The README says the next firing after a sleep is "one period after" the catch-up. APScheduler sets it to the last missed slot plus one period: the first slot after the wake, 0 to one period later. | low | Verified at `apscheduler/schedulers/base.py:1226` (`get_next_fire_time(run_times[-1], now)`). The test's exact 5 h jump lands on a slot, so it can't tell the two readings apart. The doc fix is direct. | P4 |
| E | VG-o2 | "That one catch-up always runs" is overstated: a wake a few milliseconds before a slot can push the catch-up past its grace in `run_job`, and APScheduler logs it as missed. | low | Verified in `executors/base.py` `run_job`, which re-reads the clock. Harmless, because that slot fires at once instead. The doc fix is direct. | P4 |
| F | BH9 | The claim that a trigger fires once on wake rests on an unstated mechanism and is untested on the real wake path. | low | True as far as the docs go. The real mechanism: the loop's clock (QueryPerformanceCounter) counts sleep, and Uvicorn's server loop wakes the event loop every 0.1 s (`uvicorn/server.py:248`), so the overdue APScheduler timer fires within a moment of wake. Not yet observed on a real PC sleep. The doc fix is direct; checking it on the PC goes with Story 1.20's spike (deferred entry for BH1). | P4 |
| G | BH2 | `Every` accepts periods up to 31 days, but schedules restart from zero at each engine start. On a PC shut down nightly, `Every(1 day)` without `at_startup` never fires. | low | True, but no trigger is registered yet. Decision 3 sends daily work to the calendar schedule its story adds, and the 31-day range is in the approved spec. The README and the `triggers.py` docstring now warn that a long period needs `at_startup=True` (with a handler that skips work already done) or a calendar schedule. Direct doc fix. | P4 |
| H | BH10 | The README says a hung engine restarts "about 5 minutes after it hangs". For a hung handler that is `max_runtime` + about 5 minutes after the job started, and the restart interrupts every lane. | low | Verified: the beat is withheld only once the claim passes `max_runtime` (decision 5). The doc fix is direct. | P4 |
| I | EC3 | A periodic job waiting out a retry backoff is still `queued`, so every firing is absorbed and a 1-minute trigger can wait up to 30 minutes. | low | True, and intended: AD-10's backoff applies to a failing job, and a live key's duplicate enqueue returns the live job (Story 1.10). The README now says a failing periodic job retries on its backoff, not every period, so later stories pick retry limits accordingly. Direct doc fix. | P4 |
| J | BH1 | After a sleep longer than 5 minutes, the heartbeat file is as old as the sleep until the next beat (up to 30 s after wake, since relative waits don't count sleep). `run.ps1`'s "older than 5 minutes" rule would kill a healthy engine on wake. | medium | True in the AD-20 rule as written, and it contradicts NFR-14 ("recovers unaided from sleep"). The fix is the supervisor's: judge staleness by its own awake time. `run.ps1` doesn't exist yet. The README now describes the behaviour (P4). | defer |
| K | BH3, EC2 | `register_trigger` accepts `sync_mailbox` (per inbox) and `check_recovery` (only while recovery mode is on), which the global tick can't serve as AD-10 describes. | low | No trigger is registered, and decision 2 already says these add their own enqueue use case. A global `check_recovery` tick whose handler does nothing outside recovery mode is a valid design, and so is a `sync_mailbox` fan-out in the job, so a guard now would pre-empt Epic 6's design. A developer meets this only when building those stories, and the fix adds a guard. | reject |
| L | BH4 | `build_triggers(job_types)` can't express the schedules its next users need (env-dependent `poll_unsubscribes`, settings-driven `backup_db`). | low | True by design: decision 3 and the spec's Never list leave calendar, env and setting schedules to their stories, which extend `build_triggers` (the composition root) as they need. Doing it now would change the approved scope. | reject |
| M | BH5 | `scheduler.py` reads the wall clock (`datetime.now(UTC)`) for `at_startup` instead of an injected clock. | false | No bad outcome: the `at_startup` instant must agree with APScheduler's own clock, which is that same wall clock, and the tests patch both together. An injected clock could disagree with APScheduler. | reject |
| N | BH6 | `heartbeat_withheld` for `job_overran` doesn't say which job. | low | The runner's keeper logs `job_overran` with `job_id`, `job_type`, `lane` and `attempts` on its own 30 s tick, the same window in which the writer withholds the beat, so the job is named next to it in the log. | reject |
| O | BH7 | The writer logs no start or stop, and a join that times out is silent. | low | No named harm: the first beat writes the row and the file at startup, and withholding and failures are logged. A tick takes at most about 15 s (10 s probe plus the 5 s busy timeout), under the 20 s join. | reject |
| P | BH8, EC7 | `engine_lifespan` registers each stop only after its start returns, so a start that raises partway leaves what it started running. | low | It needs `Thread.start()` or APScheduler's start to raise; the server then exits and the daemon threads die. Same as Story 1.10's EC2, and `runner_lifespan` behaved the same. Registering `runner.stop` first would make it join lane threads that never started, which raises. | reject |
| Q | BH12 | APScheduler's "skipped: maximum number of running instances" and "missed by" warnings reach the log as free text, not event names. | low | They still reach `nextron.log` (redacted) at WARNING, and a dropped firing is followed by the next one within a period. A listener that maps them adds code for a rare event. | reject |
| R | BH13 | The spec says `in-review` while `sprint-status.yaml` says `in-progress`. | false | That is mid-workflow: step 5 sets the spec to `done` and the story to `review`. | reject |
| S | EC1 | `stop()` during a firing cancels its executor future, so APScheduler logs "Error running job" at ERROR on a clean shutdown. | low | Verified in `executors/base.py` (`_run_job_error`). It needs a stop inside a firing's few-millisecond window, and the enqueue still completes on its thread. The fix adds in-flight tracking. | reject |
| T | EC4 | A job running while the PC sleeps counts the sleep toward its `max_runtime` (monotonic counts sleep), so the beat is withheld on wake. | low | True, but it heals itself: the beat resumes when the handler returns (network calls fail fast after a wake), and a restart follows only if the handler is still blocked 5 minutes later, as for any hung handler. Handlers are idempotent. The fix adds per-claim accounting. The README now mentions it (P4). | reject |
| U | EC5 | `HeartbeatWriter` doesn't validate `interval` (≤ 0, or ≥ the supervisor's 300 s). | low | Only `main.build_app` builds it, with the default, which P1 now pins. The fix adds a guard. | reject |
| V | EC6 | `Scheduler` doesn't validate `probe_timeout` (≤ 0 or NaN). | low | Only `main.build_app` builds it, with the 10 s default. The fix adds a guard. | reject |
| W | EC8 | The registry contract misses an aliased relative import (`from .triggers import Triggers as T`). | low | Contract tests guard against mistakes, not evasion (Story 1.10's EC11), and the engine uses absolute imports throughout. The fix adds AST cases. | reject |

## Design Notes

```python
def beat_once(self) -> None:
    reason = self._withheld_reason()     # runner_down, job_overran, scheduler_down or None
    if reason is None:
        try:
            with self._units.open():
                self._heartbeat.beat()   # row in one transaction, then the file
            outcome = OK
        except Exception as exc:         # logged once per streak, never raised
            outcome = failed(exc)
    else:
        outcome = withheld(reason)
    self._log_change(outcome)
```

**Why a separate thread checks the scheduler:** if the heartbeat came only from the runner, a stuck event loop would leave triggers and the dashboard dead while the file stayed fresh. The probe asks the loop itself.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` and the symlink test skipped).
- `uv run lint-imports` -- expected: every contract (29) KEPT.
