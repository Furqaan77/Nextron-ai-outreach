---
title: 'Story 1.19: Restore a backup and hold sending in recovery mode'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '4b1a7c8b06bb179cdd2194314cf7ce5bab00a3fc'
review_loop_iteration: 0
story_key: '1-19-restore-a-backup-and-hold-sending-in-recovery-mode'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-18-prove-backups-restore-with-a-monthly-restore-test.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing can put a backup back. After a lost or damaged database Mac has verified, encrypted copies but no safe way to restore one, and a restored database would forget every opt-out, erasure and send made after the copy was taken.

Requirements: AD-2, AD-5, AD-17, AD-20, OP-8.

**Approach:** A new CLI command, `nextron restore <file> [--yes]`, runs only while the engine is stopped. It checks the copy, saves the current database to `<backup dir>\pre_restore\`, upgrades the copy, writes a `recovery_state` row with recovery mode on and four `pending` steps, and swaps the copy in. While recovery mode is on, an engine start runs each registered step's handler: `startup` steps before the runner, the others from a `check_recovery` trigger every minute. Unregistered steps become `not_applicable`, and `check_recovery` turns recovery mode off once every step is `done` or `not_applicable`. Meanwhile the notification area shows a banner, the health panel lists the steps, and `queries.ops.recovery_mode()` is the gate every later send path must check.

The acceptance criteria are Story 1.19 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Domain** (`nextron/domain/recovery.py`, new, stdlib only).
  - **Constants.**
    - `CHECK_RECOVERY_JOB = "check_recovery"`, the job type (and `Trigger.CHECK_RECOVERY`'s name);
    - `CHECK_PERIOD = timedelta(minutes=1)`;
    - `PRE_RESTORE_DIR_NAME = "pre_restore"`, under the backup dir;
    - `RESTORE_WORK_DIR_NAME = "restore"`, under the data dir;
    - `RECOVERY_BANNER = "Recovery mode is on: sending is held until every recovery step is done."`.
  - **Enums.**
    - `RecoveryMode`: `on`, `off` and `superseded`.
    - `RecoveryStep`, in this order: `reapply_erasure_ledger`, `mark_oms_unknown`, `reingest_mailboxes` and `repoll_unsubscribes`. `.label` is "Reapply the erasure ledger", "Mark unfinished sends unknown", "Re-ingest the mailboxes" or "Re-poll the unsubscribe Worker".
    - `StepStatus`: `pending`, `done` and `not_applicable`. `.label` is "pending", "done" or "not applicable".
  - **`RecoveryContext(recovery_id, backup_at, restored_at)`** is frozen. Both times are aware UTC.
  - **`RecoveryHandler(step, owner, run, startup)`** is frozen and validated (`TypeError`/`ValueError`):
    - `step` is a `RecoveryStep`;
    - `owner` is a non-empty `str`;
    - `run` is a callable `(RecoveryContext) -> bool`, where `True` means the step is done;
    - `startup` is a `bool`.
  - **`RecoverySteps`** is the registry.
    - `register_step(handler)` raises `RuntimeError` after `freeze()`, `TypeError` for a non-`RecoveryHandler`, and `ValueError` for a step already registered.
    - It also has `freeze()`, `frozen`, `get(step) -> RecoveryHandler | None` and `registered() -> frozenset[RecoveryStep]`.
  - **`settled(statuses) -> bool`** is true when every status is `done` or `not_applicable`.
  - **`copy_format(name) -> "plain" | "encrypted" | None`**, compared casefolded: a name ending `.db.enc` is encrypted, a name ending `.db` is plain, and any other name is `None`.
  - **`copy_env(name) -> str | None`** is the environment of a name shaped `nextron-<env>-<stamp>.db` or `….db.enc` (the 1.16 and 1.17 shapes), and **`copy_stamp(name)`** is its UTC stamp. Both are `None` for any other name.
  - **`backup_time(name, newest_event, restored_at) -> datetime`** is the earliest of `copy_stamp(name)`, `newest_event` and `restored_at`; a missing value is skipped.
- **Persistence.**
  - **Migration `0010_1_19_recovery`** (`downgrade()` raises) creates `recovery_state`, owned by `ops`. Its columns:
    - `id` (ULID PK);
    - `mode` (String 16, `IN ('on','off','superseded')`);
    - `restored_at` and `backup_at` (not null);
    - `source_name` (String 255, not null): the restored file's name, never a path;
    - `pre_restore_name` (String 255, nullable): the safety copy's name, `NULL` when there was no current database;
    - `ended_at` (nullable);
    - one column per step, named as the step (String 16, not null, `IN ('pending','done','not_applicable')`).

    Its constraints:
    - `ck_recovery_state_outcome`:
      - `on` has no `ended_at`;
      - `superseded` has an `ended_at`;
      - `off` has an `ended_at`, and every step is `done` or `not_applicable`.
    - A partial unique index, `uq_recovery_state_on` on `mode` `WHERE mode = 'on'`, so there is at most one `on` row.
    - `ix_recovery_state_restored_at`.

    Add `RecoveryStateRecord` to `db/models/ops.py`, `owners.py` (`ops`) and `pii_registry.py` (every column `none`).
  - **`db/repos/ops/recovery.py`** (new).
    - `RecoveryRow` has every column, plus `steps -> tuple[tuple[RecoveryStep, StepStatus], ...]` in step order.
    - `insert(...) -> str`.
    - `active()`: the `on` row or `None`.
    - `newest()`: by `restored_at`, then id.
    - `supersede_active(at) -> RecoveryRow | None`: the row it superseded.
    - `set_step(recovery_id, step, status) -> int`: only from `pending`, only on the `on` row.
    - `end(recovery_id, at) -> int`: from `on` to `off`.
  - **`db/engine.py`.** `copy_database(db_path, target, *, immutable_source=False)`. With `True`, the source is opened as `open_copy` opens it (`mode=ro&immutable=1`), so nothing is created beside it.
  - **`db/repos/ops/restore.py`** (new): the restore's file work. Every function reads or writes through `db/engine.py` only.
    - `copy_candidate(source, target)`: creates `target` exclusively, then `copy_database(source, target, immutable_source=True)`. A failure discards `target` and re-raises.
    - `newest_event_at(path) -> datetime | None`: `max(events.at)` through `open_copy`. `None` with no `events` table or no row.
    - `save_current(db_path, target)`: `mkdir` the parent, create `target` exclusively, then `copy_database(db_path, target)`. A failure discards `target` and re-raises.
    - `swap_in(candidate, db_path)`: creates an empty `db_path` when it is missing, then `copy_database(candidate, db_path)`.
    - `restored_check(db_path) -> RestoredCheck(integrity, revision, active_ids)`, read through `create_read_only_engine`, which reads the WAL:
      - `integrity` is the first line of `PRAGMA integrity_check(10)`;
      - `revision` is `stored_revision(...)`;
      - `active_ids` are the ids of the `on` rows in `recovery_state`.
- **Queries** (`app/queries/ops.py`).
  - `recovery_mode() -> bool` is true while an `on` row exists. This is the gate: every later send path calls it in its own transaction and holds its sends while it is true.
  - `recovery_state() -> RecoveryRow | None` is the newest row.
- **ops** (`app/ops/api.py`).
  - **`Recovery(clock)`.**
    - **`@use_case begin(*, backup_at, source_name, pre_restore_name) -> RecoveryRow`.** It reads the clock once (`restored_at`). In one transaction:
      1. it supersedes the `on` row, if any (`ended_at` = now). Its `backup_at` then lowers this row's;
      2. it inserts the `on` row with all four steps `pending`, with `backup_at` = the earlier of the given time and `restored_at`.

      It logs `recovery_mode_started` (INFO: `recovery_id`, `superseded`).
    - **`@use_case pending(registered: frozenset[RecoveryStep]) -> RecoveryPass | None`.** `None` when no row is `on`. Otherwise, in one transaction, it marks each `pending` step that isn't in `registered` as `not_applicable` (logging `recovery_step_not_applicable`: `recovery_id`, `step`). It returns `RecoveryPass(context, pending)`, where `pending` lists the steps still pending, in order.
    - **`@use_case mark_done(recovery_id, step) -> bool`.** It logs `recovery_step_done`.
    - **`@use_case finish() -> bool`.** With an `on` row whose steps are `settled`, it ends it (`off`, `ended_at`) and logs `recovery_mode_ended` (INFO, `recovery_id`). It returns whether recovery mode is still on.
  - **`HealthReport`** gains a last field, `recovery` (`RecoveryRow | None`), which `EngineHealth.report()` reads with `recovery_state()`.
  - **The work-folder helpers.** `RestoreTests._sweep` and `_remove` become module-level functions, `clear_work_folders(parent, prefix, event)` and `remove_work_folder(folder, event)`, with unchanged behaviour (never through a link or a junction, never raising). `RestoreTests` and the restore command both use them.
- **Jobs.**
  - **`Every`** gains `active: Callable[[], bool] | None = None` (keyword-only; anything but `None` or a callable is a `TypeError`).
    - `Scheduler.fire` first calls `active()` for such a trigger, before opening its unit. It enqueues only when the result is exactly `True`; otherwise it does nothing and logs nothing.
    - An exception from `active()` is a `trigger_failed`, like any other failed firing.
    - Update the docstrings of `triggers.py` and `scheduler.py`.
  - **`nextron/jobs/recovery.py`** (new; domain and stdlib only, with a `Protocol` for `Recovery`): **`RecoveryRunner(units, recovery, steps)`**. Each unit of work it opens runs in a fresh `contextvars.Context()`, as `check_usage` does, so it works inside the runner's job unit.
    - **`startup()`** runs `pending(steps.registered())`. When a row is `on`, it logs `recovery_mode_on` (WARNING: `recovery_id`, `pending`), then runs each pending `startup` handler.
    - **`check() -> bool`** runs `pending(...)`, then each pending non-`startup` handler, then returns `finish()`.
    - **Running a handler.** It calls `handler.run(context)` in a unit of work of its own, which the handler uses for exactly one use case. `True` is followed by `mark_done` in another unit. `False` leaves the step `pending`. An exception, or a result that isn't a `bool`, logs `recovery_step_failed` (ERROR: `recovery_id`, `step`, `code`, traceback) and leaves the step `pending`. Errors from `Recovery` itself propagate.
  - **`handlers.check_recovery(checker)`** calls `checker().check()` once.
  - **The `check_recovery` job type:**
    - realtime lane only;
    - priority 85;
    - `max_runtime` 10 min;
    - retry limit 2;
    - `group=JobGroup.BACKUP`.
  - **`Trigger.CHECK_RECOVERY`** is `Every(CHECK_PERIOD, at_startup=True, active=recovery_active)`, registered after `restore_test`.
- **Composition** (`main.py`).
  - **`build_recovery_steps() -> RecoverySteps`** is the recovery step registry. It is the only code that constructs `RecoverySteps` or calls `register_step`. It ships empty and frozen; Stories 3.12, 3.16 and 6.12 register their handlers there.
  - **`read_recovery_mode(units) -> bool`** reads `recovery_mode()` in a read-only unit inside a fresh `contextvars.Context()`.
  - **`build_app(..., recovery_steps: RecoverySteps | None = None)`** builds one `Recovery(clock)` and one `RecoveryRunner`. It passes the following to `build_job_types(..., recovery=lambda: recovery_runner)` and `build_triggers(..., recovery_active=functools.partial(read_recovery_mode, units))`.
  - **`engine_lifespan(runner, scheduler, writer, recovery_startup)`** runs `await asyncio.to_thread(recovery_startup)` before `runner.start`. If it raises, startup fails: Uvicorn exits 3, and `serve` reports `server_start_failed` and exits 2.
  - **`restore(file, *, yes=False, secrets=None, targets=None, clock=None, probes=None, ask=input, out=None, err=None) -> int`.** Exit codes: 0 restored; 1 refused, cancelled or failed; 2 for an invalid bootstrap config or a failed environment check. Its steps, in order:
    1. bootstrap and `environment_checks` (exit 2);
    2. `mkdir` the data dir and `logs`, then take `<data dir>\nextron.lock`. If the lock is held, it prints `stop the engine before restoring` and exits 1, with no log record and no other change;
    3. `configure_logging`;
    4. **the file.** It must be an existing regular file whose `copy_format` isn't `None` and whose `copy_env`, when it has one, is `config.env`. A plain `.db` must have no non-empty `<file>-wal` beside it;
    5. **the work folder.** `clear_work_folders(<data dir>\restore, "nextron-<env>-", …)`, then `tempfile.mkdtemp(prefix="nextron-<env>-", dir=...)`. It is always removed at the end with `remove_work_folder`;
    6. **the candidate.** `<work>\candidate.db` is filled by `targets(file.parent).get(file.name, …)` for an encrypted copy, or by `copy_candidate(file, …)` for a plain one. The given file is never changed;
    7. **the checks.** `inspect_copy(candidate)` must show integrity `ok`, a revision that isn't `None`, and `revision_known`;
    8. **`backup_at`** is `backup_time(file.name, newest_event_at(candidate), now)`;
    9. **without `--yes`**, it prints a summary and asks `Type yes to restore: `. Only `yes` (stripped, any case) continues. Anything else, `EOFError` or `KeyboardInterrupt` prints `restore cancelled; nothing was changed` and exits 1;
    10. when `<data dir>\nextron.db` exists, `save_current` into `<backup dir>\pre_restore\<copy_name(env, now)>`, logging `pre_restore_backup_taken` (`file`);
    11. `ensure_current(create_engine(candidate))`, logging `restore_candidate_migrated` (`from_revision`, `to_revision`) when it upgraded;
    12. `Recovery(clock).begin(...)` in a unit of work on the candidate's own `UnitOfWorkFactory`, then dispose its engine;
    13. `swap_in(candidate, <data dir>\nextron.db)`;
    14. `restored_check` must show `ok`, the head revision, and exactly the new row as the one `on` row;
    15. it prints the result and logs `database_restored` (INFO: `file`, `from_revision`, `to_revision`, `recovery_id`, `pre_restore`), then exits 0.

    Any refusal or failure in steps 4–13 prints one line ending `nothing was changed` (or, at step 13, `the database is unchanged`), logs `restore_refused` (WARNING, `reason`) or `restore_failed` (ERROR, `stage`, `code`), and exits 1. A failure at step 14 prints `the restored database could not be verified …`, naming the `pre_restore` copy's full path. The lock is always released.
- **CLI** (`__main__.py`): a `restore` command with a positional `file` and `--yes`. Update the module docstring, which still says "Later stories add … restore".
- **Web.**
  - **The notification area.** `area_context(..., recovery=False)`, and `notification_list` passes `ops_queries.recovery_mode()`. While recovery mode is on, `notifications.html` starts with `<p class="recovery-banner" role="alert"><a href="/health/panel">{{ RECOVERY_BANNER }}</a></p>`. That also covers every page, because each page loads the area.
  - **The health panel.** A "Recovery" section after "Restore test", shown only when `recovery_state()` has a row.
    - While it is `on`: "Recovery mode is on since <restored_at>: sending is held. Restored from <source_name>, backed up at <backup_at>.", then a table (Step, Status) with each step's label and status label, in order.
    - Otherwise: "Last restore: <restored_at>, from <source_name>; recovery mode ended at <ended_at>.".
    - Times use the panel's UTC format.
  - Add the styles to `app.css`. Its bytes must be preserved: it is `-text` with mixed line endings, so append with a byte-exact edit.
- **Tests** run as `test`, in temporary folders only, with `ScryptParams(10, 8, 1)`, `InMemorySecrets` and an injected clock and `ask`.

**Never:**
- **No automatic restore.** Nothing but `nextron restore` replaces the database. It never runs while the lock is held, and never without `--yes` or a typed `yes`.
- **No change to the given file**, nor any file beside it, and no plaintext outside `<data dir>\restore\` and `<backup dir>\pre_restore\`.
- **No copy except through the backup API.** The copy contract in `test_backup_contracts.py` stays unchanged: no rename or file copy of a database, and `.backup(` only in `db/engine.py`.
- **No send path is invented.** Sending doesn't exist yet (Epic 6). This story builds only the gate and its contract.
- **No secret** in a row, log, message or file. No full path in a log (file names only).
- **Out of scope:**
  - reapplying the erasure ledger (Story 3.16);
  - marking OMs `unknown` and re-ingesting mail (6.12);
  - re-polling the Worker (3.12);
  - rotating `pre_restore` copies;
  - restoring the ledger file;
  - a Telegram alert, a new setting or a doctor check.
- **What not to edit.** Don't change the vendored HTMX, the spine or the planning docs, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **"Swaps the candidate in" is a backup-API copy into the live file** (`copy_database(candidate, nextron.db)`), not a rename. It is atomic (one write transaction), it works over a stale `-wal` left by a crash, and it keeps the copy contract. The candidate is copied or decrypted first into a private work folder, `<data dir>\restore\`, where it is upgraded and gets its recovery row. The file Mac names is never changed.
2. **The confirmation comes after the checks** and before anything changes. Mac must type `yes`. Anything else, including no input, cancels.
3. **The `pre_restore` safety copy.**
   - It isn't integrity-checked, because the current database may be the damaged one.
   - It gets no `backups` row and isn't rotated. That is logged in `deferred-work.md`, like Story 1.16's row-less copies.
   - If it can't be made, the restore refuses and tells Mac to move a damaged `nextron.db` (and its `-wal` and `-shm`) aside himself.
   - With no current database (a new PC) there is none.
4. **`backup_at` is the earliest of the name's stamp, the newest `events` time in the copy and the restore time.** A renamed file, or a copy taken while the engine was idle, therefore re-ingests more, never less.
5. **Refused copies.**
   - A copy named for another environment (a `prod` copy into `dev`, or a `dev` copy into `prod`).
   - A `.db` with a non-empty `-wal` beside it, which is a live database rather than a backup copy.
   - A copy that isn't `.db` or `.db.enc`.
6. **A backup taken during recovery mode** holds an `on` row. Restoring it supersedes that row, and the new `backup_at` is the earlier of the two, so mail the first recovery hadn't re-ingested isn't lost.
7. **The erasure ledger clause is deferred to Story 3.16.** The step registry ships empty, so in Epic 1 all four steps become `not_applicable` at the first start, and `check_recovery` ends recovery mode within seconds.
   - Story 3.16 owns three things: reapplying the ledger, the torn-last-line rule from Story 1.17's deferred item, and decrypting the off-PC ledger copy onto a new PC.
   - Story 6.12 owns `mark_oms_unknown` and `reingest_mailboxes`. Story 3.12 owns `repoll_unsubscribes`.
8. **When steps run.**
   - A `startup` step runs only at an engine start, before the runner. A step that failed or wasn't done is retried at the next start, never after the runner is up.
   - Non-`startup` steps run at every `check_recovery` until done, so they must be idempotent and quick (enqueue work, don't do it).
9. **`check_recovery` fires only while recovery mode is on.** A per-firing `active` read means no job row is made each minute once recovery is over. It also fires at startup. Its lane is realtime and its group is Backup.
10. **No `events` row** (recovery isn't an AD-4 machine), **no Telegram alert**, **no setting and no doctor check.** The banner and the panel are the signals.
11. **The restore logs to `nextron.log` only after taking the lock.** A refusal because the engine is running writes no log, because the running engine owns the log file.
12. **A failure to read or write `recovery_state` at engine start stops the start** (exit 2), which fails closed.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Engine running | lock held; any file | exit 1, "stop the engine before restoring"; DB bytes, folders and log unchanged | N/A |
| Plain restore | stopped; `local\nextron-test-<stamp>.db` at head; `--yes` | `pre_restore\nextron-test-<now>.db` (the old DB); live DB = copy plus one `on` row with four `pending` steps, `backup_at` ≤ stamp; given file's bytes unchanged, nothing beside it; work folder gone; exit 0 | N/A |
| Encrypted | `.db.enc` from `FolderBackupTarget.put`; passphrase set | decrypted and restored as above | wrong or missing passphrase, tampered: exit 1, nothing changed |
| Behind head | a copy at an older known revision | upgraded in the work folder, then swapped; live at head | upgrade fails: exit 1, nothing changed |
| Refused | integrity fails; revision unknown (ahead); no revision; another env's name; a `.db` with a `-wal`; another suffix; missing file | exit 1, one line, nothing changed (the live DB's bytes equal) | N/A |
| Confirm | no `--yes`; `ask` returns `yes` / `no` / raises `EOFError` | restores / "restore cancelled; nothing was changed", exit 1 / same | N/A |
| New PC | no `nextron.db` | no safety copy, `pre_restore_name` NULL; DB created by the swap | N/A |
| Copy in recovery | the copy holds an `on` row with an older `backup_at` | it becomes `superseded`; the new row's `backup_at` is the older one | N/A |
| Stale WAL | the live DB has a crash-left `-wal` | the result equals the candidate; no `-wal` left | N/A |
| Start, no handlers | engine starts with an `on` row | all four steps `not_applicable` before the runner; the at-startup `check_recovery` sets `off` and `ended_at` | N/A |
| Start, handlers | a registered `startup` handler returning `True`; a non-startup one returning `False`, then `True` | the startup one is `done` before `runner.start`; the other stays `pending`, then is `done` at a later check; then `off` | a handler raises: `recovery_step_failed`, still `pending`, recovery stays on |
| Trigger | recovery off | `check_recovery` makes no job row | `active()` raises: `trigger_failed` |
| Gate and UI | recovery on / off | `recovery_mode()` true / false; banner shown / absent; panel steps table / "Last restore: …" / no section without rows | N/A |

</frozen-after-approval>

## Code Map

- **Patterns.** Story 1.18's `RestoreTests` and `inspect_copy`, and Story 1.16's `snapshot`, `discard`, `PreMigrateBackup` and the `serve` steps (`nextron/main.py` 945-1045: lock, logging, exit codes, messages) are the patterns to follow. The `ops` module docstring (`app/ops/api.py`) is binding documentation: add a "Restore and recovery mode" paragraph. The main.py docstring needs a "Restore (Story 1.19)" paragraph and updates to Jobs, Triggers, the lifespan and Health.
- **Domain.** `nextron/domain/backup.py`: `ENV_PATTERN`, `_STAMP` and `_STAMP_FORMAT`, `copy_name`, `is_env_name`, `offpc_stamp` and `_check_aware` (reuse them; don't duplicate the stamp format). `domain/health.py` (`HealthIssues`) is the registry pattern for `RecoverySteps`.
- **Persistence.**
  - `db/alembic/versions/0009_1_18_restore_tests.py` is the create-table pattern (down revision `0009_1_18_restore_tests`).
  - `db/models/ops.py`: `CODE_LENGTH`. Check how other partial unique indexes are declared (for example on `jobs`) so the model and the migration match (`tests/contracts/test_job_contracts.schema_differences`).
  - `db/owners.py` `TABLES`, `db/pii_registry.py` `PII` and the `db/repos/ops/__init__.py` docstring.
  - `db/repos/ops/restore_tests.py` is the row and repository shape.
- **Engine.** `db/engine.py`: `copy_database`, `_file_uri`, `open_copy` and `create_read_only_engine`. `db/migrations.py`: `ensure_current`, `stored_revision` and `head_revision`. `db/repos/ops/snapshot.py`: `discard`, `_first_line` and `inspect_copy`.
- **Jobs.** `jobs/triggers.py` (`Every`, `Trigger.CHECK_RECOVERY` exists already), `jobs/scheduler.py` (`fire`, line 180) and `jobs/handlers.py` (the pattern; update its docstring). `main.check_usage` is the fresh-context pattern.
- **Composition.** In `main.py`: `build_app` (445), `build_job_types` (562), `build_triggers` (628), `engine_lifespan` (745) and `connect_database`/`build_subscribers`. Use the `InstanceLock` and `LockHeldError` imports. Credentials: `FolderBackupTarget` with `KeyringSecrets(config.env)` by default.
- **Web.**
  - `web/app.py`: `area_context` (223) and `notification_list` (534).
  - `web/templates/notifications.html`: update its comment.
  - `web/health_page.py`: `panel_context` and the restore-test helpers are the pattern.
  - `web/templates/health_panel.html`.
- **Pins to update.**
  - **Migration head.**
    - `HEAD` in `tests/contracts/test_persistence_contracts.py:72` and `tests/app/test_serve_process.py:265`;
    - the chain probes at `test_persistence_contracts.py:670-682` (0010 → 0011, 0011 → 0012, "revision 11");
    - `tests/unit/test_serve.py:732,746` (`0010_more` → `0011_more`);
    - `tests/app/test_restore_tests.py:1157,1200`, which hard-code `0009_1_18_restore_tests` as the head: use `head_revision()`;
    - `tests/contracts/test_source_tree.py` `FILES`, with the new modules.
  - **Callers of `engine_lifespan`, `build_job_types`, `build_triggers` and `HealthReport(...)`:** `tests/unit/test_runner_lifespan.py`, `tests/dashboard.py`, `tests/contracts/test_job_contracts.py`, `tests/contracts/test_backup_contracts.py`, and `tests/app/test_engine_health.py`, `test_backups.py`, `test_budget.py`, `test_dead_job_alerts.py`, `test_notify.py` and `test_restore_tests.py`.
  - **Every test that pins the job-type or trigger lists**, or counts `scheduler_started` triggers.
- **README** (`nextron-engine/README.md`): a "Restore and recovery mode" part after "The restore test" (1038), plus "Jobs" (713), "Triggers and the heartbeat" (1113), "Engine health" (1383) and the migrations table. Say that on a new PC Mac stores the Backup passphrase first (start the engine once, use Credentials, stop it), and that the supervisor must be stopped before a restore.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/recovery.py`: the constants, enums, `RecoveryContext`, `RecoveryHandler`, `RecoverySteps`, `settled`, `copy_format`, `copy_env`, `copy_stamp` and `backup_time`.
- [x] `nextron-engine/nextron/db/alembic/versions/0010_1_19_recovery.py`, `db/models/ops.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/ops/recovery.py`, `db/repos/ops/restore.py`, `db/repos/ops/__init__.py` and `db/engine.py`: the table, the repository, `immutable_source` and the restore's file functions.
- [x] `nextron-engine/nextron/app/queries/ops.py` and `app/ops/api.py`: `recovery_mode`, `recovery_state`, `Recovery`, `HealthReport.recovery` and the shared work-folder helpers.
- [x] `nextron-engine/nextron/jobs/triggers.py`, `jobs/scheduler.py`, `jobs/recovery.py` and `jobs/handlers.py`: `Every.active`, `RecoveryRunner` and the handler.
- [x] `nextron-engine/nextron/main.py` and `nextron/__main__.py`: `build_recovery_steps`, `read_recovery_mode`, the job type, the trigger, the lifespan step, `build_app`'s wiring, `restore` and the CLI command.
- [x] `nextron-engine/nextron/web/app.py`, `web/health_page.py`, `web/templates/notifications.html`, `web/templates/health_panel.html` and `web/static/app.css`: the banner and the Recovery section.
- [x] `nextron-engine/tests/unit/test_recovery_domain.py`: the enums and labels, the registry's rules, `settled`, the name functions (every shape, wrong env, `.part`, case) and `backup_time` (each source missing, the earliest wins, clamped to `restored_at`).
- [x] `nextron-engine/tests/app/test_recovery.py`, with real units: `begin` (supersede, `backup_at`), `pending`, `mark_done` and `finish`; `recovery_mode` and `recovery_state`; raw-SQL violations of `ck_recovery_state_outcome` and of the one-`on` index, each refused by name.
- [x] `nextron-engine/tests/unit/test_recovery_runner.py`: `startup` runs only startup handlers and marks unregistered steps; `check` runs the others and ends recovery when settled; a raising or non-bool handler logs `recovery_step_failed` and stays pending; each handler runs in its own unit (one use case).
- [x] `nextron-engine/tests/unit/test_restore_command.py`: every restore row of the matrix through `main.restore`, with the live DB's bytes compared for "nothing changed". Assert that the given file and its folder are unchanged, that no work folder is left, that the lock is released, and the log records. Make the `.db.enc` with the real `FolderBackupTarget`, and the stale `-wal` with a real crash-like copy (an open connection with `wal_autocheckpoint=0`).
- [x] `nextron-engine/tests/unit/test_triggers.py` and `test_scheduler.py`: `active` validation, a firing skipped, a firing enqueued, and `active` raising.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py`, with the real `build_app`: an `on` row with no handlers ends `off` after the start, and an injected `recovery_steps` with a startup handler runs it before `runner.start`.
- [x] `nextron-engine/tests/unit/test_notifications_area.py` (the banner on and off) and `tests/unit/test_health_page.py` (the Recovery section's three forms).
- [x] `nextron-engine/tests/contracts/test_recovery_contracts.py`, each source check with a probe:
  - `RecoverySteps` is constructed and `register_step` called only in `main.build_recovery_steps`;
  - `queries.ops.recovery_mode` exists with signature `() -> bool`;
  - `Trigger.CHECK_RECOVERY` is an `Every(CHECK_PERIOD, at_startup=True)` with `active` set, on a realtime-only job type;
  - `RecoveryStep` is exactly the AC's four, in order.
- [x] `nextron-engine/tests/unit/test_restore_command.py` also covers the `restore` parser in `__main__.build_parser` (`file`, `--yes`), as `test_open_command.py` does for `open`.
- [x] The pins in the Code Map, `nextron-engine/README.md`, and a `deferred-work.md` entry for `pre_restore` rotation (decision 3).

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q -n 6 --dist loadgroup` and `uv run lint-imports` run, then everything passes and all 29 contracts are kept.
- Given `tests/contracts/test_backup_contracts.py`, when it scans the new code, then the copy check passes unchanged.
- Given a `dev` engine data dir with a real backup, when `nextron restore <copy> --yes` runs with the engine stopped and the engine then starts, then recovery mode turns off within a minute and the panel reads "Last restore: …". This manual check is for Mac.

## Implementation Notes

- **Where the code is.**
  - **Domain:** `nextron/domain/recovery.py`. It holds the constants; `RecoveryMode`, `RecoveryStep` and `StepStatus` with their labels; `RecoveryContext`, `RecoveryHandler`, `RecoveryPass` and `RecoverySteps`; and the functions `settled`, `copy_format`, `copy_env`, `copy_stamp` and `backup_time`. `RecoveryPass` lives in the domain because `jobs/recovery.py` may import only the domain.
  - **Persistence.**
    - Migration `0010_1_19_recovery` (down revision `0009_1_18_restore_tests`). It has six checks: `ck_recovery_state_mode`, one `ck_recovery_state_<step>` per step and `ck_recovery_state_outcome`. It also has `uq_recovery_state_on`, partial on `mode = 'on'`, and `ix_recovery_state_restored_at`.
    - `RecoveryStateRecord` in `db/models/ops.py`.
    - `owners.py` lists the table under `ops`; `pii_registry.py` marks every column `none`.
    - `db/repos/ops/recovery.py`: `RecoveryRow`, with `steps`, plus `insert`, `active`, `newest`, `supersede_active`, `set_step` and `end`.
    - `db/repos/ops/restore.py`: `copy_candidate`, `newest_event_at`, `save_current`, `swap_in` and `restored_check`.
    - `copy_database(..., immutable_source=False)` in `db/engine.py`.
  - **Queries and service.**
    - `queries.ops.recovery_mode()` (the gate) and `recovery_state()`.
    - In `app/ops/api.py`: `Recovery` (`begin`, `pending`, `mark_done`, `finish`) and `HealthReport.recovery`. The work-folder helpers `clear_work_folders` and `remove_work_folder` are now module-level, and `RestoreTests` uses them. The module docstring has the "Restore and recovery mode" paragraph.
  - **Jobs.**
    - `Every(..., active=None)` in `jobs/triggers.py`. `Scheduler.fire` checks `active()` before opening a unit.
    - `jobs/recovery.py` holds `RecoveryRunner`, with `startup` and `check`.
    - `handlers.check_recovery`.
  - **Composition** (`main.py`):
    - the `check_recovery` job type (realtime, priority 85, 10 min, retry limit 2, group Backup);
    - `Trigger.CHECK_RECOVERY` (`Every(CHECK_PERIOD, at_startup=True, active=...)`);
    - `build_recovery_steps` and `read_recovery_mode`;
    - `build_app(recovery_steps=)`;
    - `engine_lifespan(..., recovery_startup)`;
    - `restore(...)` with `_Restore`, `RestoreRefused` and `RestoreFailed`.

    `__main__.py` has the `restore` command (`file`, `--yes`).
  - **Web.**
    - `area_context(..., recovery=)` and the banner in `notifications.html`.
    - `recovery_text`, `recovery_on` and `recovery_steps` in `health_page.py`, and the Recovery section in `health_panel.html`.
    - `.recovery-banner` and `.health-recovery*` in `app.css`, appended byte-exactly.
- **Small additions beyond the spec.**
  - **Two more step-4 refusals:** the live `nextron.db` itself (`live_database`), and a file inside `<data dir>\restore\` (`in_work_folder`), which the step-5 sweep would delete.
  - **`copy_env` and `copy_stamp` match any case** and return the environment casefolded. An impossible stamp (month 13) still gives an environment but no time.
  - **How the restore logs its failures.**
    - A decrypt failure is `restore_refused`, with the adapter's code as the reason. A cancel is `restore_refused` with reason `cancelled`.
    - The `restore_failed` stages are `file`, `work_folder`, `candidate`, `backup_time`, `pre_restore`, `migrate`, `recovery`, `swap` and `verify`.
    - The verify codes are `integrity_failed`, `revision_mismatch`, `recovery_row_mismatch`, or the code of the error raised.
  - **An `OSError` while preparing the data dir or `logs` exits 1**, ends "nothing was changed" and logs nothing.
  - **`Recovery.begin` checks its inputs.** A naive `backup_at`, or a path in `source_name` or `pre_restore_name` (`\`, `/` or `:`), raises `TypeError` or `ValueError`.
  - **Log field shapes.** `recovery_mode_on.pending` is a comma-separated string, because the JSON formatter drops lists. `recovery_mode_started.superseded` is the superseded row's ULID or `None`.
- **Pins updated.**
  - `HEAD` (three places), the chain probes (0011/0012, "revision 11") and `test_serve.py` (`0011_more`).
  - `test_restore_tests.py`, which now uses `HEAD`/`OLD_REVISION` rather than the literal head.
  - `test_source_tree.py` `FILES`, and the trigger count (2 → 3).
  - The job-type lists, including the Backup group in `test_engine_health.py`, and `test_job_contracts.py`'s `JOBS_IMPORTS` rule for `jobs/recovery.py`.
  - Every `build_job_types` caller passes `recovery=`, using a `no_recovery` helper where no such job runs.
- **The Stale WAL row ("no `-wal` left").** The crash's frames are gone after the swap (`copy_database` reads them, then checkpoints and deletes the `-wal` when its connection closes). The step-14 read through `create_read_only_engine` can leave an empty `-wal` and `-shm`, which SQLite treats as no WAL and deletes at the engine's next clean stop. The test asserts the `-wal` is absent or empty, and that the WAL-only row isn't in the restored database.

Review fixes (pass 1, P1–P9):
- **P1.** `test_a_file_that_is_not_a_database_is_refused` (`test_restore_command.py`): non-SQLite bytes in a copy-named `.db` give exit 1, "restore failed: the copy could not be read (DatabaseError); nothing was changed" and `restore_failed` with stage `candidate`. The live bytes, the given file and `pre_restore` are unchanged, and no work folder is left.
- **P2.** `test_a_restored_database_that_cannot_be_verified_names_the_safety_copy` is parametrized over five cases: `integrity_failed`, `revision_mismatch`, no `on` row, another `on` row, and a raising `restored_check` (its code). It also adds `test_on_a_new_pc_a_failed_verification_says_to_restore_again`.
- **P3.** `recovery_repo.newest()` orders the `on` row first, then `restored_at`, then id, so `queries.ops.recovery_state()` is "the `on` row, else the newest". Test: `test_the_on_row_is_the_recovery_state_even_when_the_clock_went_back` (`tests/app/test_recovery.py`).
- **P4.** A failed step 14 now prints `the restored database could not be verified (<code>); the database from before the restore is at <path>; do not start the engine: run nextron restore again, or restore the safety copy`. With no safety copy it says `there was no database before the restore; do not start the engine: run nextron restore again`.
- **P5.** `swap_in` deletes a `nextron.db` it created itself when the copy fails, so a failed swap on a new PC leaves no empty database.
- **P6.** `RestoreFailed` gains an `outcome`.
  - A failure at `migrate` or `recovery` discards the `pre_restore` copy, so "nothing was changed" is true.
  - A swap failure keeps the copy and prints `the database is unchanged; its safety copy is at <path>`.
  - Only a swap or verify failure leaves a safety copy.
  - Tests: the failed-upgrade test, the new `test_a_failed_recovery_row_changes_nothing`, and the failed-swap test, which now asserts the exact line.
- **P7.** `tests/unit/test_restore_files.py` (8 tests) covers `copy_candidate` (nothing beside the source, no target after a non-database source, never overwrites), `save_current` (makes its folder, no target after a failure) and `swap_in` (creates a missing database, leaves no `db_path` after a failed swap onto a missing database, leaves the live bytes after a failed swap onto a live one).
- **P8.** The README and `main.py` step 4 say the environment guard applies only to files that keep a backup copy's name. A renamed file isn't checked, so keep the backup names.
- **P9.** The New PC, Copy in recovery and Stale WAL tests also assert that the given file is unchanged, that no work folder is left and that the lock is released.

**Verification before review** (§4a, targeted): the new and touched test files plus `tests/contracts` gave 1415 passed, 1 skipped, and `uv run lint-imports` 29 kept. After the patches, the touched files plus `tests/contracts` gave 1056 passed, 1 skipped, and `lint-imports` 29 kept. The implementer's earlier full-suite runs (before §4a) failed only on the known job-runner flake (`test_a_second_start_raises_and_stop_is_idempotent`), which this tree has because it predates `496e2ba`.

**Final verification** (after pass 1, through the shared suite lock, on the final tree): `uv run pytest -q -n 6 --dist loadgroup` gave 4876 passed, 2 skipped (`ai_eval` and the symlink test), and `uv run lint-imports` 29 kept, 0 broken. The known flake didn't occur.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:**
  - BH = blind hunter: 16 findings, with BH4 making five claims and BH16 three;
  - EC = edge-case hunter: 9;
  - VG = verification gap: 3 gaps and 1 other finding.
- **Counts:** 29 raw findings make 35 claims, which give 27 entries after deduplication.
- **Outcomes:**
  - 9 entries patched (P1–P9);
  - 2 deferred (D1, D2);
  - 16 rejected.
- **No loopback.** No finding is an intent gap or a bad spec.
- **Rejected only because the fix would change the approved spec:**
  - K (decision 9, the realtime lane);
  - O (decision 8, startup steps retried only at the next start);
  - the content-based environment guard in H (decision 5 guards by name);
  - X (decision 4, the `restored_at` fallback).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | VG1, BH4 (candidate) | No test feeds `nextron restore` a `.db` that SQLite can't read, so the `candidate` stage (`_Restore._candidate`'s `except Exception`) is unpinned. Narrowing that `except` would let a traceback escape. | medium | Pre-verified by VG, which probed the real path: exit 1, "restore failed: the copy could not be read (DatabaseError); nothing was changed". The fix is a direct test addition: non-SQLite bytes named as a copy. Assert exit 1, the line, `restore_failed` with stage `candidate`, the live bytes and the given file unchanged, and no work folder. | P1 |
| B | VG3, BH4 (verify) | Step 14 tests only `recovery_row_mismatch`. `integrity_failed`, `revision_mismatch` and a raising `restored_check` are untested, so deleting either comparison passes every test. | medium | Pre-verified by VG (the grep finds no `revision_mismatch`). The fix is a direct test change: parametrize `test_a_restored_database_that_cannot_be_verified_names_the_safety_copy` over the four problems. | P2 |
| C | VG (other), EC5 | `recovery_repo.newest()` orders by `restored_at`. If the clock reads earlier than a superseded row's `restored_at` (a new PC with its clock behind), the panel shows the superseded row as "Last restore … ended" with no steps, while the banner says recovery mode is on. | low | Verified: `begin` inserts the new `on` row with `restored_at` = now, and `newest()` sorts it below the superseded one. The gate (`active()`) is right; only the panel is wrong. The fix is a direct correction: order `on` rows first. | P3 |
| D | BH1, EC2 | When the post-swap check fails, the swapped-in database stays live, and the message names the safety copy without saying not to start the engine. Without an `on` row the next start wouldn't hold sending. | low | The copy holds the `on` row before the swap, and the swap is SQLite's backup API, so a missing row after it isn't shown reachable. The failure is already loud (exit 1). The real gap is the instruction, so the fix is a direct correction of the message: add "do not start the engine; run nextron restore again, or restore the safety copy". | P4 |
| E | BH2, EC3 | With no live database, `swap_in` creates an empty `nextron.db` and leaves it there when `copy_database` fails, yet the message says "the database is unchanged". The next `serve` then migrates the empty file. | low | Verified: `swap_in` makes the file with `open("xb")` and has no cleanup. The fix is trivial: delete the file it created when the copy raises. | P5 |
| F | BH3 | After step 10 a failure at `migrate` or `recovery` keeps the `pre_restore` copy and still prints "nothing was changed". Each retry leaves another full copy of the database that is never rotated (AD-17). | medium | Verified: `_save_current` runs before `_prepare`, and no failure path removes the target. The live database is untouched until step 13, so the smallest fix discards the safety copy on a failure before the swap. A swap failure keeps the copy, and the message names its path. The steps stay in the AC's order. | P6 |
| G | BH4 (discard) | No test exercises "a failure discards `target`" in `copy_candidate` or `save_current`, and `nextron/db/repos/ops/restore.py` has no unit test file. | low | Verified by grep: `test_a_failed_safety_copy…` replaces `save_current` outright. The fix is a direct test addition (`tests/unit/test_restore_files.py`): a non-database source leaves no target for both functions, and `swap_in`'s cleanup (E) is covered there. | P7 |
| H | BH5, EC1 | The environment guard reads only the file name, so a renamed copy skips it, yet the README says "a prod copy never lands in dev or the reverse". | low | Verified: `copy_env` is `None` for any other name. Decision 5 guards by name on purpose, and no environment marker exists inside a database (the deferred "bind each data dir to its environment" item). A content-based guard would change the spec and is rejected. The README overclaims, which is a direct correction: say the guard applies to copy names only. | P8 |
| I | EC9 | The New PC, Copy in recovery and Stale WAL tests don't assert that the given file is unchanged, that no work folder is left or that the lock is released, though the spec's task asks every restore row to. | low | Verified by reading `test_restore_command.py:670-750`. The fix is a direct test addition: `Given(copy).assert_unchanged()`, `place.assert_no_work_folder()` and `place.assert_lock_released()` in each. | P9 |
| J | VG2, BH4 (swap) | `test_a_failed_swap_leaves_the_database_unchanged` replaces `swap_in`, so the "one write transaction, unchanged on failure" property has no test of its own. | medium | Pre-verified by VG. The property rests on SQLite's backup API, which copies all pages in one step and one destination transaction. The probe during planning showed it replaces a live database that has a stale `-wal`. A deterministic mid-copy failure can't be provoked without instrumenting SQLite. VG's disposition is defer. | D1 |
| L | BH6 | After a restore, local copies taken after the restored copy have no `backups` row, so the row-based rotation never deletes them. They hold personal data past 35 days. | medium | Verified: `LocalBackups.rotate` works only from rows. This is Story 1.16's known limitation ("a copy that has no row is never deleted", rejected there as spec-changing), and a restore exposes it. Off-PC rotation is by name and unaffected. | D2 |
| M | BH4 (stages) | The `backup_time`, `work_folder` and `restore_cleanup_failed` paths have no test. | low | True. Each needs an injected filesystem failure, and each path prints the standard line and exits 1 (or only logs a warning). Unlikely in use, and adding the tests needs fakes for `tempfile` and `newest_event_at`. | reject |
| N | BH7 | A crash mid-restore leaves a plaintext candidate in `<data dir>\restore\` until the next restore. | low | True, but it needs a crash during a rare manual command, and the copy sits beside the live database in the data dir. The fix adds a new sweep to `serve` (new behaviour). | reject |
| O | BH9 | A `startup` step that isn't done waits for a restart nobody is told about. | low | No handler exists in Epic 1, so no step can wait. Decision 8 retries startup steps only at the next start. The owner stories (3.16's AC has its own health issue) report why a step waits. | reject |
| P | BH8 | A failing step shows only "pending" on the panel, and `recovery_step_failed` logs every minute. | low | No handler exists in Epic 1. Each owner reports its own health issue (Story 3.16's AC: "Erasure ledger not found; sending stays blocked"). Throttling the log is new design. | reject |
| Q | BH10 | Startup handlers have no time limit, so a long one delays the first heartbeat. Story 1.21's supervisor could then kill the engine. | low | No handler exists yet. This is noted for Stories 1.21 and 3.16 in the handoff: the supervisor must allow a start grace, and startup steps must stay bounded. | reject |
| K | BH11 | `check_recovery` shares the one realtime thread with `send_alert`. | low | Specified by decision 9. The handler is two short transactions in Epic 1, and later handlers must "enqueue work, don't do it". | reject |
| R | BH12 | `restore()` has no catch-all, so an unexpected exception escapes as a traceback with no `restore_failed` record. | false | `configure_logging` runs first and installs the `sys.excepthook` that logs any uncaught exception to `nextron.log`. Every named stage is wrapped, and `finally` still removes the work folder and releases the lock. | reject |
| S | BH13 | `_candidate` labels `backup_target_unavailable` and `backup_copy_missing` as "could not be decrypted", and a non-SQLite `.db` is an ERROR failure rather than a WARNING refusal. | low | Cosmetic: the message carries the code, and both exit 1 with "nothing was changed". | reject |
| T | BH14 | The notification area doesn't fail safe when `recovery_state` can't be read. | low | It is the same database every other read uses, so the area's GET fails visibly. A send path's gate read failing fails closed (no send). | reject |
| U | BH15 | Nothing will catch a future send path that skips the gate. | low | No send path exists (the intent forbids inventing one). Story 6.12's AC requires dispatch to hold while `recovery_mode()` is true; the handoff notes the enforcement for Epic 6. | reject |
| V | BH16 (pages) | `test_every_page_loads_the_area_that_holds_the_banner` ignores its `path`. | false | It GETs each `path` and asserts that page's `div#notification-list` loads `/notifications` (`test_notifications_area.py:446-452`), then that the area holds the banner. | reject |
| W | BH16 (copy-paste), BH16 (style) | The `no_recovery` helper is copied into several test modules, and some imports are unsorted or lines over-long. | low | Cosmetic. Ruff isn't part of the suite. | reject |
| Y | EC4 | CPython's `backup()` retries `SQLITE_BUSY` forever, so another process writing the live database would hang the restore. | low | Verified in CPython's loop. With the engine stopped and the lock held, only an outside tool writing `nextron.db` could cause it, and the fix needs a progress-callback deadline. | reject |
| Z | EC6 | If `Recovery` keeps raising, `check_recovery` dies every few minutes, each death raising a `job_dead` alert. | low | It needs a persistently failing database, when every job fails. One alert per dead job is the jobqueue's existing design (AD-10). | reject |
| X | EC7 | A renamed copy with no events gets `backup_at` = now. | low | A database with no `events` row has never run a job: no send, sync or poll. The fallback is decision 4. | reject |
| AA | EC8 | A name holding `:` (an NTFS stream) passes step 4 but fails at `Recovery.begin`, after the safety copy. | low | `Path.name` on Windows can't hold `\`, and `x.db:stream` is a contrived stream path. With F's patch, the safety copy is discarded on that failure anyway. | reject |

## Design Notes

```text
nextron restore F [--yes]        (engine stopped; lock held throughout)
  F -> <data dir>\restore\nextron-<env>-*\candidate.db   (decrypt, or backup-API copy, immutable source)
  inspect_copy(candidate)        integrity ok, revision known      else exit 1
  ask "yes"                      unless --yes
  nextron.db -> <backup dir>\pre_restore\nextron-<env>-<now>.db   (backup API)
  ensure_current(candidate); Recovery.begin(...) on the candidate  (on, 4x pending)
  copy_database(candidate -> nextron.db)                           (backup API, atomic)
serve -> lifespan: RecoveryRunner.startup()  -> runner.start -> scheduler (check_recovery, active only while on)
```

`RecoveryRunner` lives in the jobs layer because it opens units of work (one per handler, so each handler calls one use case). The `ops` `Recovery` service owns only `recovery_state`. Owners outside `ops` never call `ops`: a step is marked done only by the runner, from the handler's `True`.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q -n 6 --dist loadgroup` -- expected: all pass. Run the full suite only through the suite lock.
- `uv run lint-imports` -- expected: 29 contracts KEPT.
