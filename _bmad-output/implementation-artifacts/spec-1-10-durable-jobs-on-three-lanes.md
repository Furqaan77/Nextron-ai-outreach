---
title: 'Story 1.10: Run durable jobs on three lanes with leases and retries'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '0b14ddddfc4bd1e91ff178315e1d037280de5b31'
review_loop_iteration: 0
story_key: '1-10-run-durable-jobs-on-three-lanes-with-leases-and-retries'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Background work has nowhere durable to live. Every later pipeline stage (crawls, mailbox syncs, sends, AI calls) needs jobs that:
- survive a crash or a restart;
- never run twice at once;
- retry only what may succeed later;
- keep time-critical work off the lane that crawls.

Requirements: AD-3, AD-4, AD-10, AD-17.

**Approach:**
- **Storage.** A `jobs` table (owner `jobqueue`, migration `0003_1_10_jobs`) and the Job machine in `nextron/domain/job.py`.
- **Service.** A `JobQueue` service whose `enqueue` command joins the caller's transaction.
- **Job types.** A frozen job-type registry built in `main.py`, empty for now.
- **Runner** (`nextron/jobs/runner.py`):
  - worker threads per lane;
  - a keeper thread that renews leases, reaps expired ones and purges old `done` rows daily;
  - both started and stopped by the FastAPI lifespan.
- **Setting.** A `bulk_threads` setting.

The acceptance criteria are Story 1.10 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Shared machine types** (`nextron/domain/machines.py`, new; every later machine reuses it):
  - `Transition[S, C](to, cause)`, frozen.
  - `IllegalTransition(DomainError)`, code `illegal_transition`. Its message is `"<machine>: <event> is not allowed from <status>"`.

- **Job domain** (`nextron/domain/job.py`, stdlib only).
  - **Enums.**
    - `Lane`: `realtime`, `interactive`, `bulk`.
    - `JobStatus`: `queued`, `running`, `done`, `dead`. `LIVE_STATUSES` is `(queued, running)`.
    - `JobEvent`: one value per row of the table below.
    - `JobCause`: `enqueued`, `claimed`, `succeeded`, `retry_scheduled`, `failed`, `retries_exhausted`, `lease_expired`, `released`.
  - **Transitions.** `TRANSITIONS` is a read-only mapping, and `transition(current, event)` returns a `Transition` or raises `IllegalTransition`. The table is exactly this; a creation (no `from` status) has cause `enqueued`.

    | From | Event | To | Cause |
    |---|---|---|---|
    | queued | claim | running | claimed |
    | running | succeed | done | succeeded |
    | running | retry | queued | retry_scheduled |
    | running | fail | dead | failed |
    | running | exhaust | dead | retries_exhausted |
    | running | expire | queued | lease_expired |
    | running | expire_exhausted | dead | lease_expired |
    | running | release | queued | released |
    | running | release_exhausted | dead | released |

  - **Constants.**
    - Durations: `LEASE_MARGIN` = 60 s, `RENEW_INTERVAL` = 30 s, `FIRST_RETRY_DELAY` = 30 s, `MAX_RETRY_DELAY` = 30 min, `DONE_RETENTION` = 30 days.
    - Limits: `MAX_BULK_THREADS` = 2, `MAX_ERROR_LENGTH` = 500.
  - **Leases and backoff.**
    - `lease_until(now, max_runtime)` is `now + max_runtime + LEASE_MARGIN`.
    - `retry_delay(attempts)` is `min(30 s × 2^(attempts−1), 30 min)`.
  - **Outcomes.** `attempts` counts claims.
    - `after_failure(attempts, retry_limit, *, retryable)`, in order: not retryable gives `fail`; `attempts > retry_limit` gives `exhaust`; otherwise `retry`.
    - `after_interruption(attempts, retry_limit, *, released)`: `attempts > retry_limit` gives `release_exhausted` (released) or `expire_exhausted`; otherwise `release` or `expire` (decision 1).
  - **Idempotency keys.** `idempotency_key(job_type, scope, discriminator)` returns `<type>:<scope>:<discriminator>`, or raises `InvalidJob` unless:
    - the type has the job-type name shape;
    - the scope is a ULID, `inbox:<ULID>` or `global`;
    - the discriminator is `tick`, a real ISO date (`YYYY-MM-DD`), or a revision from `0` to `999999999` with no leading zeros (decision 6).
  - **Concurrency keys.** `is_concurrency_key(value)` accepts `inbox:<ULID>`, or `domain:<name>` where the name is 1–253 characters of lowercase letter-digit-hyphen labels joined by dots, with at least one dot.
  - **Payloads.** `encode_payload(payload) -> str` and `decode_payload(text) -> Mapping` (read-only) both raise `InvalidJob` unless the payload is a flat JSON object where:
    - keys fullmatch `[a-z][a-z0-9_]{0,31}`;
    - `v` is present, an int from 1 to 999;
    - each value is one of:
      - a ULID;
      - an `int` (not a `bool`) within signed 64 bits;
      - a `bool`;
      - a real ISO date (`YYYY-MM-DD`);
      - a real UTC time (`YYYY-MM-DDTHH:MM:SS[.ffffff]Z`);
      - a token fullmatching `[a-z0-9_:.-]{1,64}`;
    - nothing is `null`, a float, a list or an object;
    - there are at most 32 fields, and the encoded text is at most 4,096 UTF-8 bytes.

    The text is compact JSON with sorted keys. A string with `@` or a space fails every rule. Each refusal's reason names the field, never its value.
  - **Job types.** `JobType(name, default_lane, lanes, priority, max_runtime, retry_limit, handler)` is frozen. `__post_init__` raises `TypeError` or `ValueError` unless:
    - `name` fullmatches `[a-z][a-z0-9]*(?:_[a-z0-9]+)+` (`verb_noun`), at most 48 characters;
    - `lanes` is a non-empty `frozenset[Lane]` containing `default_lane`;
    - `priority` is an int from 0 to 100 (higher runs first);
    - `max_runtime` is a `timedelta` of whole seconds, from 1 s to 24 h;
    - `retry_limit` is an int from 0 to 20;
    - `handler` is callable.

    A `bool` is refused wherever an int is expected.
  - **The registry.** `JobTypes` has:
    - `register_job_type(job_type)`: a `JobType` only; a duplicate name, or a call after `freeze()`, raises;
    - `freeze()` and `frozen`;
    - `get(name)`, which raises `UnknownJobType` (code `unknown_job_type`);
    - `names()`.
  - **Other types.** `Job(id, type, lane, payload, attempts)`, frozen, is what a handler receives. `InvalidJob(reason)` has code `invalid_job`.

- **Persistence.**
  - **The model.** `JobRecord` (`nextron/db/models/jobqueue.py`), table `jobs`. Its columns:
    - `id`: a ULID;
    - `type`: String(48);
    - `payload`: Text;
    - `run_after`, `created_at` and `updated_at`: `UtcDateTime`;
    - `status` and `lane`: String(16);
    - `attempts` and `priority`: Integer;
    - `idempotency_key`: String(128);
    - `concurrency_key`: String(260), nullable;
    - `lease_until`: `UtcDateTime`, nullable;
    - `last_error`: Text, nullable.
  - **Indexes and checks**, the same in the model and the migration (naming convention names):
    - unique `uq_jobs_live_key` on `idempotency_key`, where `status IN ('queued','running')`;
    - `ix_jobs_claim` on (`status`, `lane`, `priority`, `run_after`);
    - `ix_jobs_lease` on (`status`, `lease_until`);
    - `ix_jobs_type_status` on (`type`, `status`, `updated_at`);
    - `ix_jobs_running_concurrency` on `concurrency_key`, where `status = 'running'`;
    - checks: `status` is one of the four, `lane` one of the three, `attempts >= 0`, `priority` from 0 to 100, and `(status = 'running') = (lease_until IS NOT NULL)`.
  - **Migration** `versions/0003_1_10_jobs.py`: revision `0003_1_10_jobs`, revising `0002_1_6_settings`. Its downgrade raises.
  - **Maps.** In `owners.py`, every `jobs` column belongs to `jobqueue`. In `pii_registry.py`, every column is `none` except `last_error`, which is `redact` (decision 10).
  - **Repository** (`nextron/db/repos/jobqueue/jobs.py`): the only SQL on `jobs`, through `current().session`, with `JobRow`, a frozen row of every column. It provides:
    - the live job for a key;
    - an insert;
    - the next claim candidate;
    - fenced status updates that return their row count;
    - the expired `running` rows;
    - the purge.

- **Service** (`JobQueue(clock, job_types)`, in `nextron/app/jobqueue/api.py`). Each call reads the clock once. Every status change goes through `transition` and appends an `events` row in the same transaction: entity `jobs`, actor `system:jobqueue`, `cause_ref` `None`.
  - **`@command enqueue(job_type, *, scope, discriminator, payload, run_after=None, lane=None, concurrency_key=None) -> Enqueued(job_id, created)`.**
    - **Refusals.** An unregistered type raises `UnknownJobType`. Each of these raises `InvalidJob`:
      - a lane outside the type's `lanes`;
      - a bad key, payload or concurrency key;
      - a naive `run_after`.

      Either error dooms the caller's transaction.
    - **Duplicates.** A live job with the same key gives `Enqueued(<its id>, False)` and writes nothing.
    - **Insert.** Otherwise it inserts the job `queued`, with 0 attempts, the type's priority and `run_after` (default: now), and appends the creation event.
  - **`@use_case claim(lane) -> Claim | None`**, where `Claim` is `(job, max_runtime)`. It runs in one transaction:
    1. **Pick.** The candidate is `queued`, on this lane, has `run_after <= now`, and has a `concurrency_key` that is null or held by no `running` job. Candidates are ordered `priority DESC, run_after ASC, id ASC`.
    2. **Claim.** `UPDATE … SET status='running', attempts=attempts+1, lease_until=<lease_until(now, max_runtime)>, updated_at=now WHERE id=? AND status='queued'` must change exactly one row; anything else raises.
    3. **Unrunnable jobs** (decision 7). A candidate whose type isn't registered, or whose payload no longer decodes, is claimed and moved straight to `dead` (`fail`). Its `last_error` reads `unknown_job_type: …` or `invalid_job: …`. The next candidate is tried, up to 10 per call.
  - **`@use_case complete(claim) -> bool`.** `running → done`, with `lease_until` null.
    - **The fence.** The update must match the `id`, `status = 'running'` and `attempts = claim.job.attempts`.
    - **A fence miss** changes nothing, logs `job_claim_lost` and returns `False`.
  - **`@use_case fail(claim, error) -> JobStatus | None`.**
    - **Classification.** The error is retryable when `isinstance(error, AdapterError) and error.retryable`; `after_failure` then picks the event.
    - **Outcome.** `retry` gives `queued` with `run_after = now + retry_delay(attempts)`; anything else gives `dead`. Either way `last_error = error_text(error)`.
    - **The fence** is the same as `complete`'s; a miss returns `None`.
    - **`error_text`.**
      - `AdapterError` and `DomainError` give `"<code>: <message>"`.
      - Anything else gives `"<type qualname>: <str(error)>"`.
      - The result is then passed through `redact` and cut to `MAX_ERROR_LENGTH` characters.
  - **`@use_case keep(held) -> Kept(lost, requeued, dead)`**, in one transaction:
    1. **Renew.** Each held claim's lease becomes `lease_until(now, claim.max_runtime)`, fenced. A miss is `lost`.
    2. **Reap.** Every other `running` job with `lease_until <= now` is handled by `after_interruption(released=False)`:
       - `queued`, with `run_after = now`; or
       - `dead`, with `last_error = "lease_expired: the lease expired before the job finished"`.

       An unregistered type counts as retry limit 0.
  - **`@use_case release(held) -> tuple[str, ...]`.**
    - Each held claim, fenced, follows `after_interruption(released=True)`.
    - A job that becomes `dead` gets `last_error = "released: the engine stopped before the job finished"`.
    - It returns the ids it changed.
  - **`@use_case purge_done() -> int`.**
    - It deletes `done` rows with `updated_at < now − 30 days`, except each type's newest `done` row (latest `updated_at`, then highest `id`).
    - It never deletes a `dead` row.
    - It returns how many rows it deleted.
  - **Logs.** Records carry IDs, the type, the lane, the attempts and `code` only, never a payload value or `last_error` text. `code` is the error's code, or else the exception's class name. The events are:
    - `job_retry_scheduled` (WARNING);
    - `job_dead` (ERROR);
    - `job_claim_lost`, `job_lease_expired` and `job_released` (WARNING);
    - `jobs_purged` (INFO, only when the count is above 0).
  - **Query** (`nextron/app/queries/jobqueue.py`): `job(job_id) -> JobRow | None`.

- **Runner** (`nextron/jobs/runner.py`). It imports only `nextron.app.jobqueue.api`, `nextron.app.queries.setup`, the domain and the stdlib.
  - **Constructor.** `Runner(units, queue, job_types, *, poll_interval=1.0, renew_interval=30.0, maintenance_interval=86_400.0, shutdown_grace=15.0, monotonic=time.monotonic)`. `units` is a local `Units` Protocol with `open(*, read_only=False)`.
  - **`start()`.**
    1. Reads `bulk_threads` once with `queries.setup.get(Key.BULK_THREADS)`, in a read-only unit of work that it closes before starting any thread. Any error means 1, and logs `bulk_threads_unreadable` (WARNING) (decision 9).
    2. Starts daemon threads:
       - `nextron-job-realtime-1` and `nextron-job-interactive-1`;
       - `nextron-job-bulk-1` to `nextron-job-bulk-<n>`;
       - `nextron-job-keeper`.
    3. Logs `job_runner_started` with `bulk_threads`.

    A second `start()` raises `RuntimeError`.
  - **Lane loop.** Until stopped, it calls `run_one(lane)`. When nothing ran, it waits `poll_interval` on the stop event. An exception escaping `run_one` (such as a database error) logs `job_claim_failed` with the traceback and waits `poll_interval`, and the thread keeps running.
  - **`run_one(lane) -> bool`** (also the test hook):
    1. **Claim** in its own unit of work (`units.open()`). Stop if there is nothing to claim.
    2. **Hold.** Add the claim to the held set (under a lock), with its monotonic start time.
    3. **Run.** Call `handler(job)` inside its own `units.open()`; the handler calls one use case (decision 12). A return value other than `None` counts as `InvalidJob("the handler returned a value")`.
    4. **Finish** in a third unit of work: `complete(claim)`, or `fail(claim, exc)` for any `Exception`. If finishing fails, log `job_finish_failed` and leave the lease to expire.
    5. **Drop.** Remove the claim from the held set in a `finally`. A `BaseException` that isn't an `Exception` re-raises and ends the thread.
  - **Keeper loop.** A tick runs at once and then every `renew_interval`:
    - **Renew and reap.** It calls `keep(<snapshot of the held set>)` and logs `job_lease_lost` (WARNING) for each lost claim.
    - **Overran.** It logs `job_overran` (WARNING) once per claim whose run has passed its `max_runtime` (decision 2).
    - **Maintenance.** `purge_done()` runs on the first tick, then each time `maintenance_interval` has passed (decision 8).
    - **Errors.** Any exception logs `job_keeper_failed`, and the loop goes on.
  - **`stop()`.**
    1. Set the stop event, so no thread claims again.
    2. Join the lane threads until `shutdown_grace` has passed (one deadline for all), then join the keeper.
    3. Call `release(<still held>)` in its own unit of work; an error logs `job_finish_failed`.
    4. Log `job_runner_stopped` with `released`.

    It does nothing before `start()`, or when called again.
  - **`alive() -> bool`**: started, not stopped, and every lane thread and the keeper alive. Story 1.11's heartbeat needs it.
  - **Test hooks.** `keep_once()` runs one keeper tick synchronously, and `maintain_once()` runs one purge.

- **Composition and settings.**
  - **The setting.** `settings_schema` gains `Key.BULK_THREADS`: type `INTEGER`, section `Section.ENGINE`, default 1, rules `minimum` 1 and `maximum` 2. Label: "Bulk worker threads". Unit: "1 or 2. Threads for crawls, enrichment and other bulk work; a change takes effect when the engine restarts."
  - **`main.py`.**
    - `build_job_types()` builds the frozen, empty `JobTypes`. It is the only code that constructs `JobTypes` or calls `register_job_type`.
    - `build_app` builds `JobQueue(clock, job_types)` and `Runner(units, queue, job_types)`, and passes `lifespan=runner_lifespan(runner)` to `create_app`. Building starts nothing.
    - `runner_lifespan(runner)` is an async context manager factory: `await asyncio.to_thread(runner.start)`, then yield, then `await asyncio.to_thread(runner.stop)`.
    - The module docstring gains a paragraph on the jobs.
  - **`web/app.py`.** `create_app(..., lifespan=None)` passes `lifespan` to `FastAPI(lifespan=...)`.

- **Tests.** Behaviour is tested through `run_one` and `keep_once` with `FrozenClock`. Threaded tests use small intervals and `Event`s, with no sleep and no wait longer than 0.05 s.

**Never:**
- **Out of scope:**
  - real job types and handlers;
  - triggers, APScheduler and the heartbeat (1.11);
  - ops alerts for dead jobs (1.12);
  - budget deferral (1.13);
  - dismissing dead jobs, and the health panel (1.15);
  - any dashboard route.
- **No new parts.** Add no adapter, port or dependency. The runner never reads the keyring or the network.
- **No personal data.** Never put personal data, secrets or free text in `payload` or `events`, and never log a payload value or `last_error` text.
- **Consent flows stay as they are.** Don't move `GoogleAccess`'s consent flows onto the job queue: they hold an in-memory secret (the PKCE verifier) that AD-17 keeps out of job payloads. `tests/dashboard.py`'s `run_jobs()` stays as it is.
- **What not to edit.** Don't edit the vendored HTMX, or anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

### Decisions made overnight (pending user review)

1. **An interrupted run counts as an attempt.** `attempts` counts claims. A run cut off by an expired lease or by a shutdown counts. When it was the last allowed attempt, the job becomes `dead` instead of running again, so a type with retry limit 0 never runs twice.
2. **A running job keeps its lease for as long as its handler runs.** The keeper renews every job this process is running, so a slow job is never claimed a second time. Python can't stop a thread, so a job past its `max_runtime` is only logged (`job_overran`). A hung handler is left to the heartbeat and the supervisor (1.11, 1.21).
3. **Only the keeper reaps expired leases.** It reaps right after renewing its own leases, at start and every 30 s; a claim never reaps. A job orphaned by a crash becomes claimable once its lease expires, at most `max_runtime` + 60 s after the crash. Nothing takes jobs over at startup.
4. **Finishing is fenced by `(id, attempts)`.** A run whose claim was lost (reaped or released) can't overwrite the job; it is logged as `job_claim_lost`.
5. **Shutdown waits 15 s,** then releases what is still running. Decision 1 applies.
6. **Payload and key shapes are strict,** as listed above:
   - flat scalars only, with `v` from 1 to 999;
   - discriminators `tick`, a date or a revision number;
   - concurrency keys `inbox:<ULID>` or `domain:<name>`.

   A later story widens them only by amending this module.
7. **A queued job the engine can no longer run is moved to `dead` by the claim.** That covers a type unregistered by an upgrade, and a payload that no longer decodes. The reason goes in `last_error`.
8. **Daily maintenance runs on the keeper,** at runner start and after every 24 h of uptime. It isn't an AD-10 trigger, because that list is closed.
9. **`bulk_threads`** is an Engine setting: 1 or 2, default 1, read once at start. If it can't be read, 1 is used.
10. **`last_error` is classified `redact`.** It is free text: redacted and at most 500 characters, but an exception's message could still name a person.
11. **Idle lanes poll every second.** Enqueuing doesn't wake a lane in-process. A claim with nothing to do writes nothing.
12. **The runner opens the handler's unit of work.** A handler calls one use case and returns `None`. Story 1.13 adds its `deferred` result; until then any other return value is a failure, so no handler completes by accident.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Enqueue | a registered type; scope a ULID; discriminator `3`; payload `{"v":1,"lead_id":<ULID>}` | one `queued` row in the caller's transaction, key `<type>:<ULID>:3`, and the creation event; if the caller rolls back, no row is left | N/A |
| Duplicate | same key while the job is `queued` or `running` | `Enqueued(<live id>, False)`; one live row | after `done` or `dead`: a new row |
| Bad enqueue | an unregistered type; a lane the type doesn't allow; a payload holding `"a@b.co"`, `"a b"`, `1.5`, `null` or `[1]`, or without `v`; scope `x`; discriminator `v2` | nothing written; the transaction is doomed | `UnknownJobType` or `InvalidJob` |
| Claim order | priority 10 and 50 jobs due; a priority 90 job whose `run_after` is in the future | the 50, then the 10; the 90 only once it is due | N/A |
| Concurrency | two queued `inbox:A` jobs on two lanes, one of them claimed | the other is skipped until the first finishes; an `inbox:B` job is still claimable | N/A |
| Success | the handler returns `None` | `done`, lease null; events `claimed` then `succeeded` | N/A |
| Retryable | `AdapterError(True, …)`, retry limit 3 | `queued` again after 30, 60 and 120 s; the fourth failure makes it `dead` (`retries_exhausted`) | `last_error` redacted |
| Fatal | `ValueError("x@y.com")` or `AdapterError(False, …)` | `dead` at once (`failed`); `last_error` is `ValueError: <email>` | N/A |
| Limit 0 | a retryable error, retry limit 0 | `dead` after the first run | N/A |
| Lease expiry | a `running` job not held, the clock past its `lease_until` | the keeper makes it `queued` (attempt counted) and claimable again; at the last attempt, `dead` | logged |
| Renewal | a held claim; a keeper tick 30 s later | `lease_until = now + max_runtime + 60 s`; never reaped while held | a fence miss is lost and logged |
| Lost claim | the job was reaped or released, then its handler finishes | nothing changes | `job_claim_lost` |
| Shutdown | a handler still running when the grace ends | released: `queued`, or `dead` at the last attempt; no thread claims again | N/A |
| Unrunnable | a queued job of an unregistered type, or with a garbled payload | claimed and made `dead` in the same claim; the next job is claimed | N/A |
| Purge | type A `done` rows 31 and 29 days old; type B's only `done` row, 40 days old; a 90-day-old `dead` row | only A's 31-day-old row is deleted | N/A |
| Threads | `bulk_threads` unset, 2, or unreadable | 1, 2 or 1 bulk threads, plus one realtime thread, one interactive thread and the keeper | unreadable: a warning |

</frozen-after-approval>

## Code Map

- **Units of work** (`nextron-engine/nextron/db/uow.py`): `use_case`, `command`, `current` and read-only units. An entry point calls one use case per unit of work, so the runner opens a new unit for each claim, each handler run and each finish.
- **Event log** (`nextron-engine/nextron/db/eventlog.py`): `append(...)`. Its `entity` must be in `owners.TABLES`; the actor here is `system:jobqueue`.
- **Model base** (`nextron-engine/nextron/db/models/base.py`): `Base` (with its naming convention), `ulid_pk`, `Ulid`, `UtcDateTime` and `new_id`. `UtcDateTime` stores fixed-width text, so SQL `<=` compares times correctly. `models/__init__.py` must import the new module.
- **Patterns to copy:**
  - the model, `db/models/setup.py`;
  - the repository, `db/repos/setup/settings.py`;
  - the query, `app/queries/setup.py`;
  - the migration, `db/alembic/versions/0002_1_6_settings.py`.

  Partial indexes need `sqlite_where` in both the model and the migration. The drift test (`tests/contracts/test_persistence_contracts.py:737`) compares them.
- **Maps** (`nextron-engine/nextron/db/owners.py`, `pii_registry.py`): add `jobs`.
- **The service shape** (`nextron-engine/nextron/app/setup/api.py`, `Setup`): a class holding `clock`, whose `@use_case` methods read the clock once and open `current().transaction()`.
- **Errors and helpers:**
  - `nextron/ports/errors.py`: `AdapterError`;
  - `nextron/domain/errors.py`: `DomainError` and its unique codes;
  - `nextron/domain/redaction.py`: `redact`;
  - `nextron/domain/ids.py`: `is_ulid`.
- **Settings schema** (`nextron-engine/nextron/domain/settings_schema.py`): `Key`, `SCHEMA`, `minimum`, `maximum` and `Section.ENGINE`. Tests that list the declared keys:
  - `tests/unit/test_settings_schema.py:504`;
  - `tests/contracts/test_settings_contracts.py:131`;
  - any Settings page test that counts fields.
- **Composition root** (`nextron-engine/nextron/main.py`): `build_app`, `build_subscribers` (the registry pattern to follow) and the module docstring. `create_app_from_environment` (the reload worker) also goes through `build_app`, so the runner runs in whichever process serves the app.
- **App factory** (`nextron-engine/nextron/web/app.py`): `create_app` gains `lifespan`. `web/guard.py` already passes lifespan scopes through, and `tests/unit/test_dashboard.py:617` (lifespan passthrough) must keep passing.
- **Delivery rules** (`nextron-engine/nextron/jobs/__init__.py`, docstring). The jobs layer imports `app.*.api` and `app.queries.*` only, never `nextron.ports`, `nextron.db` or an adapter (contract `c2-delivery`). So the runner never names `AdapterError`; `JobQueue.fail` classifies errors.
- **Test fixtures:**
  - `tests/conftest.py`: the `db_engine` and `units` fixtures;
  - `tests/fakes/clock.py`: `FrozenClock` (`set`, `advance`);
  - `tests/unit/test_dashboard.py:617`: how to drive an ASGI lifespan by hand.
- **Contract tests:**
  - `tests/contracts/test_persistence_contracts.py:235-335`: the AST check that only `main.build_subscribers` touches `Subscribers`. Copy it for `JobTypes` and `register_job_type`, with live proofs built on `tests/contracts/support.py`.
  - `tests/contracts/test_source_tree.py`: `FILES`.
  - `tests/contracts/test_log_event_names.py`: event names under 32 characters.
- **Subprocess tests** (`tests/app/test_serve_process.py`, `test_environment_isolation.py`): the real `serve` processes these start now run an idle runner. It reads neither the keyring nor the network, so the deferred subprocess-guard item stays deferred.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/machines.py`, `nextron/domain/job.py` -- the shared machine types and the job domain, as in Boundaries.
- [x] `nextron-engine/nextron/db/models/jobqueue.py`, `models/__init__.py`, `owners.py`, `pii_registry.py`, `alembic/versions/0003_1_10_jobs.py` -- the table, its maps and its migration.
- [x] `nextron-engine/nextron/db/repos/jobqueue/jobs.py`, and the package docstring -- the repository.
- [x] `nextron-engine/nextron/app/jobqueue/api.py`, `nextron/app/queries/jobqueue.py` -- the service and the query.
- [x] `nextron-engine/nextron/jobs/runner.py`, and the `nextron/jobs/__init__.py` docstring -- the runner.
- [x] `nextron-engine/nextron/domain/settings_schema.py` -- the `bulk_threads` setting.
- [x] `nextron-engine/nextron/web/app.py`, `nextron/main.py` -- the lifespan and the wiring.
- [x] `nextron-engine/tests/unit/test_job_domain.py` -- the domain:
  - the transition table exactly, and illegal pairs;
  - keys, payloads (every refusal row, `@` and a space refused) and concurrency keys;
  - the `JobType` and `JobTypes` rules;
  - backoff values (30, 60, 120 … capped at 1800 s), `after_failure` and `after_interruption`.
- [x] `nextron-engine/tests/app/test_jobqueue.py` -- the service against a temporary DB and `FrozenClock`:
  - every matrix row from Enqueue to Purge;
  - the `events` row of each transition;
  - the partial unique index: a raw second live insert fails, and a live row next to a `done` one works;
  - the fences.
- [x] `nextron-engine/tests/unit/test_job_runner.py` -- the runner:
  - `run_one`'s paths through real units of work;
  - threaded start and stop: thread names and counts for each `bulk_threads` value;
  - handlers run neither on the event loop nor on the thread that started the runner;
  - `stop` releases a blocked handler's job after a short grace, and that handler's later finish logs `job_claim_lost`;
  - `alive()` is false once a lane thread ends;
  - keeper renewal, reaping, and the purge on the first tick;
  - `job_overran` logged once;
  - a claim error leaves the lane alive.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py` -- `main.build_app` on a real DB, with the lifespan driven by hand: startup starts the named threads, shutdown stops them, and building alone starts none.
- [x] `nextron-engine/tests/contracts/test_job_contracts.py` -- the job contracts:
  - only `main.build_job_types` constructs `JobTypes` or calls `register_job_type` (AST, with live proofs);
  - `build_job_types()` is frozen and empty;
  - `nextron/jobs/runner.py` imports nothing from `nextron.ports` or `nextron.db`.
- [x] `nextron-engine/tests/app/test_serve_process.py` -- the running engine's log holds `job_runner_started`.
- [x] Existing tests -- the declared-settings lists, the source-tree `FILES`, and any Settings page field count.
- [x] `nextron-engine/README.md` -- a "Jobs" section, plus migration `0003` under Database. The section covers lanes and threads, keys, payload rules, retries and backoff, leases, shutdown, maintenance and `bulk_threads`.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `serve` in any environment, when it starts, then the database is at `0003_1_10_jobs` and `nextron.log` holds `job_runner_started` with `bulk_threads`, and no handler runs on the event loop.

## Implementation Notes

- **Check names in the migration go through `op.f(...)`.** Alembic applies the model's naming
  convention to `op.create_table`, so `name="ck_jobs_status"` became `ck_jobs_ck_jobs_status`.
  The README's "Adding a migration" now says so.
- **The drift test sees neither a partial index's `WHERE` nor a check** (verified: a database
  with `uq_jobs_live_key` rebuilt without its `WHERE` still shows no drift). So
  `test_job_contracts.py` compares the SQL of `jobs`' indexes and checks at head with the
  model's compiled DDL, with proofs.
- **Choices where the spec is silent:**
  - `release` leaves `run_after` unchanged, so a released job keeps its place in the queue
    (only the reap sets `run_after = now`, as specified).
  - Every reaped job logs `job_lease_expired`, and every released job `job_released`; a job that
    becomes `dead` logs `job_dead` as well, so `job_dead` marks every move to `dead`.
  - The discriminator must be text: an `int` revision is refused (`str(rev)`).
  - A claim the keeper finds lost is logged once (`job_lease_lost`) and not renewed or released
    again. `run_one` claims nothing once `stop()` has begun.
  - `stop()` skips `release` when nothing is held (no empty write transaction), and joins the
    keeper for at most 10 s (it waits on the stop event, so it ends at once unless mid-tick).
  - Runner threads start in a fresh `contextvars.Context()`.
- **Existing tests changed beyond the lists:** the forked-revision probes (`0002_fork` is now
  `0099_fork`, since it must sort after the new head) and the chain-check probe ids (`0003_*` to
  `0004_*`); the Settings page saves now send `bulk_threads` (`real_body`).
- **`test_environment_isolation.py` retries the revision read after killing the engines.** The
  runner's threads hold pooled SQLite connections, and Windows releases a killed process's file
  locks a moment after it exits, so a read straight away failed with "disk I/O error" (it
  succeeds 0.1 s later). A supervisor restart straight after a crash could hit the same
  transient error at startup (exit 2, then another restart); Story 1.21 should know.
- **Graceful stop in a real process** (`CTRL_BREAK`, tried by hand): the runner stops and logs
  `job_runner_stopped`, but the process exits 3 without `engine_stopped`, exactly as at the
  baseline commit (the deferred Windows clean-stop path, Story 1.21).

Review fixes (pass 1, P1–P10; see the Review Triage Log):
- **P1.** `Runner`'s `renew_interval` defaults to `RENEW_INTERVAL.total_seconds()`.
  `test_the_runner_timing_defaults` pins all four defaults (1 s, 30 s, 24 h, 15 s).
- **P2.** `test_stop_logs_a_failed_release_and_still_returns`: `stop()` returns, logs
  `job_finish_failed` with a traceback and `job_runner_stopped` with `released == 0`.
- **P3.** `test_the_shutdown_grace_is_one_deadline_for_every_lane` blocks two lanes and checks that
  `stop()` takes about one grace, not two, and releases both jobs.
- **P4.** `live_job` filters with the model's literal `LIVE_WHERE`, so SQLite uses
  `uq_jobs_live_key`; a test runs `EXPLAIN QUERY PLAN` on the real statement.
- **P5.** README "Declare a job type": handlers must be idempotent, because a run cut off by a
  crash or a shutdown is re-run from the start (unless it was the last attempt) and may already
  have committed. Work that must never repeat uses retry limit 0 plus its own state guard (AD-5).
- **P6.** `maintain_once` records the purge time only after `purge_done()` returns, so a failed
  purge is tried again on the next keeper tick.
- **P7.** `run_one` drops the claim from the held set as soon as the handler returns, before
  finishing, so a keeper tick after the finish commits no longer logs a false `job_lease_lost`.
  The lease still has at least `max_runtime` + 30 s left, and a failed finish still leaves it to
  expire. The new test fails with the old order.
- **P8.** `test_job_contracts.py` compares the index and check SQL of every table in
  `Base.metadata` with the database at head, not only `jobs`; the README says so.
- **P9.** `test_concurrent_lanes_never_run_a_job_or_a_concurrency_key_twice_at_once` runs four lane
  threads over 12 jobs that share two `inbox:` keys and finds no overlap.
- **P10.** README Leases: a job left by a crash is claimable again at most `max_runtime` + 90 s
  after the crash, once the engine runs again (the reap waits for the keeper's next 30 s tick).
  Overnight decision 3's "+ 60 s" is about 30 s short; the frozen text is left as approved.
- **Deferred:** BH5 (a hung handler keeps `alive()` true), for Story 1.11's heartbeat.

**Final verification (after pass 1).**
- `uv run pytest -q`: 2985 passed, 2 skipped (`ai_eval`, the symlink test), in 131 s.
- `uv run lint-imports`: 29 kept, 0 broken.
- **Not verified here:** a clean Windows stop of the whole engine (exit 0 with `engine_stopped`),
  which stays with Story 1.21; no job type exists yet, so no real handler has run.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (16 findings), EC = edge-case hunter (13), VG = verification gap (3 gaps and 1 other finding).
- **Counts:** 33 raw findings, 29 after deduplication.
- **Outcomes:**
  - 10 groups patched (P1–P10): 4 code or doc fixes, 6 test gaps (two of them with a code fix);
  - 1 deferred: BH5;
  - 18 rejected.
- **No loopback.** No finding is an intent gap or a bad spec. Four findings (BH3, BH6, VG4, EC12) are rejected only because their fixes would change the approved spec; Mac can revisit them when he reviews the overnight decisions. Decision 3's "at most `max_runtime` + 60 s" is about 30 s short (EC10); the README now gives the right bound, and the frozen text is left as approved.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | VG1 (gap), BH11 | `RENEW_INTERVAL` is read by nothing, and the runner hard-codes its timing defaults (poll 1 s, renew 30 s, maintenance 24 h, grace 15 s); no test checks them, so a drift (say a 0 s grace) passes. | low | Pre-verified by VG: every runner test passes its own intervals, and `test_the_constants` checks a constant the runner never reads. The fix is a direct correction (the default comes from the constant) and a test. | P1 |
| B | VG2 (gap) | `stop()` when `JobQueue.release` raises is untested; dropping the `try` would let a database error escape the lifespan shutdown with no `job_runner_stopped`. | low | Pre-verified: the only `job_finish_failed` test covers `_finish`. The fix is test-only. | P2 |
| C | VG3 (gap) | Nothing proves the shutdown grace is one deadline for all lanes; a per-thread join would pass both stop tests. | low | Pre-verified: both stop tests block one lane only. The fix is test-only. | P3 |
| D | VG4 | A transient "database is locked" inside a handler's use case makes the job `dead` at once instead of retrying. | low | True: only an `AdapterError` with `retryable` retries. Transactions are short (AD-3: none spans a network call), so a 5 s busy timeout is rare. The AC says any other exception makes the job dead at once, so retrying would change the approved spec. | reject |
| E | BH1 | `live_job` filters `status IN (?, ?)` with bound parameters, so SQLite never uses the partial index `uq_jobs_live_key` and scans every live job on each enqueue. | low | Verified with EXPLAIN QUERY PLAN: bound parameters give `ix_jobs_lease (status=?)`, the literal `status IN ('queued','running')` gives `uq_jobs_live_key (idempotency_key=?)`. The fix is a direct correction. | P4 |
| F | BH2 | `ix_jobs_claim` is ascending, but the claim orders `priority DESC`, so SQLite sorts the lane's top-priority group (`USE TEMP B-TREE FOR LAST 2 TERMS`). | low | Verified with EXPLAIN QUERY PLAN. At this engine's scale (at most a few hundred live jobs on one PC) the sort costs well under a millisecond per poll. A descending index needs expression-index handling in the Alembic drift test and the schema-SQL contract, so it's more than a direct correction. | reject |
| G | BH3 | A dead job's handler traceback is recorded nowhere: `fail` logs only `code`, and `last_error` keeps 500 characters. | low | True. The frozen log rule says records carry IDs, the type, the lane, the attempts and `code` only, so adding a frame field changes the approved spec. `last_error` still names the exception and its message. | reject |
| H | BH4 | A database that keeps failing makes every lane log a `job_claim_failed` traceback each second, with no backoff. | low | True, but the log rotates at 10 MB × 10 files, so the disk can't fill, and every record shows the same cause. It needs a persistent database failure, and the fix adds backoff state. | reject |
| I | BH5 | `alive()` can't see a hung handler: its thread stays alive and its lease renewed, so Story 1.11's heartbeat would never let the supervisor restart a blocked realtime lane. | medium | True: decision 2 leaves hung handlers to the heartbeat, but 1.11's AC writes the heartbeat while every lane thread is alive. No handler exists yet, and the heartbeat is 1.11's to define, so the signal belongs there (`deferred-work.md`). | defer |
| J | BH6 | After a crash, orphaned `running` rows hold their concurrency keys until their leases expire, delaying that inbox's or domain's jobs. | low | True, by decision 3 ("Nothing takes jobs over at startup"). The fix would reverse a frozen decision. | reject |
| K | BH7 | A duplicate enqueue that differs (a new payload, an earlier `run_after`) is dropped silently. | low | That is the AC ("a duplicate enqueue returns the live job"), and the README says a live key's enqueue "returns that job and writes nothing". No caller exists yet. | reject |
| L | BH8 | The README never says handlers must be idempotent, though a run cut off by a crash or a shutdown (after its handler committed) is re-run. Also: `run_one` checks the stop event only before claiming. | low | Verified: "Declare a job type" says nothing about re-runs. The doc fix is direct. The `run_one` half shares EC1's root cause and is judged there. | P5 |
| M | BH9, EC8 | `maintain_once` stamps `_last_maintenance` before purging, so a failed purge waits 24 h for its next try. | low | Verified at `runner.py` (`maintain_once`). The fix moves one line. | P6 |
| N | BH10, EC7 | A keeper tick landing after a lane's `complete` commits but before `_drop` sees a fence miss on a claim still held and logs a false `job_lease_lost`. | low | Verified: the claim is held through `_finish`, and `_renew` only skips entries already dropped. The fix is a direct reorder: drop the claim once the handler returns. | P7 |
| O | BH12 | Shutdown can take about 30 s (lane grace, keeper join, release busy wait), though the docs say 15 s. | false | The README and the `main.py` docstring say stop "waits up to 15 s for running handlers", which holds. Nothing claims the whole stop fits in 15 s, and the keeper waits on the stop event, so it ends at once unless mid-tick. | reject |
| P | BH13, EC5 | The claim doesn't check a job's stored lane against its type's current `lanes`, so after an upgrade a job may run once on a lane its type no longer allows. | low | True, but it needs an upgrade that narrows a type's lanes while its jobs are queued. Each such job runs once and is done. The fix adds a branch, and making the job `dead` would be worse. | reject |
| Q | BH14 | `last_error` goes stale: a requeue after a lease expiry or release doesn't set it, and `complete` doesn't clear it. | low | Every move to `dead` writes its own text, and dead rows are the only ones the health panel (1.15) shows. On `queued` and `done` rows it stays the latest recorded failure, as the model docstring says. Not met in everyday use. | reject |
| R | BH15 | The index-and-check SQL contract hard-codes `jobs`, and the README tells the next author to copy it by hand, though the drift test can't see partial `WHERE`s or checks on any table. | low | Verified in `test_job_contracts.py` (`JobRecord.__table__`, `tbl_name = 'jobs'`). Later tables add partial unique indexes (outbound messages, enrollments). The fix is test-only. | P8 |
| S | BH16 | "Never runs twice at once" has no concurrent test: every claim test runs on one thread. | low | Verified. The guarantee rests on `BEGIN IMMEDIATE` and the conditional update, which sequential tests cover, but no test runs lanes at once. The fix is test-only. | P9 |
| T | EC1, BH8 (part) | `stop()` can set the stop event while a lane is inside `claim()`, so that handler starts after shutdown began. | low | True, but the claim is held before the handler runs, so it finishes within the grace or is released, like any job running at stop. The window is one claim transaction, a few milliseconds. The fix adds a branch. | reject |
| U | EC2 | If `start()` fails after some threads started, the lifespan never calls `stop()`, and `stop()` would raise joining an unstarted thread. | low | Needs `Thread.start()` to fail (out of OS resources); the server then exits and the daemon threads die. The fix adds guards. | reject |
| V | EC3 | An expired `running` row that can't be read (attempts 0, a non-ULID id, a bad time) makes every keeper tick roll back, renewals included. | low | Every path that writes `running` increments attempts and writes canonical ids and times. Such a row needs a hand edit of the database. The fix adds a check and per-row isolation. | reject |
| W | EC4 | A queued row poisoned beyond the two caught cases (non-ULID id, bad stored time) stops its lane: the claim fails on every poll. | low | Same as V: only a hand edit can make one. The fix widens the error handling. | reject |
| X | EC6 | `JobQueue` and `Runner` accept an unfrozen `JobTypes`; `UnitOfWorkFactory` refuses an unfrozen registry. | low | Only `main.build_job_types` builds the registry (contract-checked), and it freezes it. The fix adds a guard. | reject |
| Y | EC9 | Python 3.14's `fromisoformat` accepts `T24:00:00`, so a payload time can name one instant two ways. | low | Verified: `fromisoformat("2026-11-17T24:00:00")` gives the next midnight. No caller writes such times, and payloads aren't compared. The fix adds a check. | reject |
| Z | EC10 | The README's crash bound ("claimable again at most `max_runtime` + 60 s after the crash") ignores that reaping happens on the keeper's 30 s ticks. | low | Verified: the lease runs from the last renewal, and a reap waits for the next tick, so the bound is `max_runtime` + 90 s once the engine runs again. The README fix is direct. Decision 3 carries the same wording, but it is frozen. | P10 |
| AA | EC11 | The registry contract misses an assignment alias (`T = JobTypes; T()`). | low | True. Contract tests guard against mistakes, not evasion, and the subscriber check has the same limit. The fix adds AST cases. | reject |
| AB | EC12 | With `serve --reload` in `dev`, `job_runner_started` never reaches `nextron.log`, against the AC "serve in any environment". | low | True: the reload worker writes no log file (Story 1.3), which is unchanged here. The fix would change this spec's AC. | reject |
| AC | EC13 | `stop()` releases jobs whose handlers still run; an engine started after the lock is released could claim one while the old handler runs. | low | The process exits milliseconds after `serve` releases the lock, and `run.ps1` waits at least 5 s before a restart (AD-20). The old handler's finish is fenced. | reject |

## Design Notes

```python
def run_one(self, lane: Lane) -> bool:
    with self._units.open():
        claim = self._queue.claim(lane)
    if claim is None:
        return False
    self._hold(claim)
    try:
        error = self._run_handler(claim)       # own unit of work; None or the Exception
    finally:
        self._drop(claim)                      # before finishing (review P7); lease still valid
    self._finish(claim, error)                 # own unit: complete() or fail(); logs on error
    return True
```

**Why the keeper, not the claim, reaps:** every lease this process holds is renewed in the same transaction, just before the reap, so a job still running here (after a PC sleep, for example) is never reaped and run twice.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass, with `ai_eval` and the symlink test skipped.
- `uv run lint-imports` -- expected: every contract (29) KEPT.
