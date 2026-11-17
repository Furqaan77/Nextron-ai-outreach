---
title: 'Story 1.16: Back up the database in-process daily and before every migration'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '3191abc6037e8f858729d45aa50713ffbfaa4245'
review_loop_iteration: 0
story_key: '1-16-back-up-the-database-in-process-daily-and-before-every-migra'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing copies the database.
- A bad upgrade, a corrupted file or a damaged disk would lose the Suppression List and the Provenance Records.
- `serve` migrates with no copy taken first.
- The health panel can't show a backup.

Requirements: FR-58, AD-2, AD-20, OP-5, OP-6, NFR-16.

**Approach:** An `ops` service copies the database with SQLite's backup API into `<backup dir>\local\`, verifies the copy and records it in a new `backups` table:
- daily, from a calendar trigger;
- on demand, from a "Back up now" POST on the health panel;
- before every migration, in `serve`.

A failed backup raises a `backup_failed` ops alert and a health-panel error. Old local copies rotate after a successful backup.

The acceptance criteria are Story 1.16 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Domain** (`nextron/domain/backup.py`, stdlib only, takes `now`).
  - **Constants.**
    - `BACKUP_DB_JOB = "backup_db"`;
    - `BACKUP_FAILED = "backup_failed"`, the alert kind and the health-issue kind;
    - `KIND_FIELD = "kind"`;
    - `LOCAL_DIR_NAME = "local"`;
    - `COUNTED_TABLES = ("suppression_entries", "provenance_records")`;
    - `DEFAULT_BACKUP_TIME = time(10, 0)`;
    - `MIN_ROTATION_DAYS = 7`, `MAX_ROTATION_DAYS = 35` and `DEFAULT_ROTATION_DAYS = 35`.
  - **Kinds and statuses.**
    - `BackupKind(StrEnum)`: `daily`, `manual` and `pre_migrate`. `.label` is "daily", "manual" or "before a migration".
    - `JOB_KINDS = frozenset({DAILY, MANUAL})`.
    - `BackupStatus(StrEnum)`: `verified` and `failed`.
  - **Copy names.**
    - `copy_name(env, at)` is `nextron-<env>-<YYYYMMDDTHHMMSS.mmm>Z.db`, from the UTC time (`at` must be aware). Windows forbids `:` in a file name.
    - `is_copy_name(env, name)` fullmatches that shape.
  - **The schedule.**
    - `latest_slot(now, at, timezone) -> datetime` is the latest UTC instant ≤ `now` whose local time in `timezone` is `at`. It uses `datetime.combine` with `ZoneInfo`, then `.astimezone(UTC)`, stepping back one local date when today's time is still to come.
    - `missed_daily(newest_daily_started_at, now, at, timezone)` is `newest is not None and newest < latest_slot(...)`.
    - `BackupSchedule(at, timezone, missed)` is frozen.
  - **`BackupNotVerified(DomainError)`.** Its code is `backup_not_verified`, and its message is the reason.
  - **`backup_failed_text(kind, backup_id)`** is `"Backup failed: <kind> <ULID>"`: IDs and codes only.
- **Persistence.**
  - **Migration `0007_1_16_backups`** (`downgrade()` raises) creates `backups`, owned by `ops`, with these columns:
    - `id` (ULID PK);
    - `kind` and `status`, each with an `IN` check;
    - `started_at` and `finished_at`;
    - `path`: the copy's absolute path;
    - `size_bytes`, `sha256`, `db_revision`, `suppression_count` and `provenance_count`, all nullable. A count of `NULL` on a verified row means the table was absent from the copy;
    - `error` (nullable, at most 500 characters);
    - `local_deleted_at` (nullable).

    Its constraints:
    - `ck_backups_outcome`:
      - `verified` has a size, a SHA-256 and a revision, and no error;
      - `failed` has an error, no size and no SHA-256, and no `local_deleted_at`.
    - Non-negative checks on the size and the counts.
    - `length(sha256) = 64`.
    - `ix_backups_started_at`.

    Add the model to `db/models/ops.py`. Map every column in `owners.py` (`ops`). In `pii_registry.py`, `error` is `redact` and every other column is `none`.
  - **`db/engine.py`** stays the only module that calls `sqlite3.connect`.
    - **`copy_database(db_path, target)`** opens the live file read-only (`<file URI>?mode=ro`, `busy_timeout`). It runs `source.backup(destination)` into `target`, an empty file the caller has already created, and then closes both connections. It sends no statement to the destination, so the copy is one file whose header keeps WAL mode.
    - **`open_copy(path)`** is a context manager: `sqlite3.connect(<URI>?mode=ro&immutable=1)`. It never writes the copy and never creates `-wal` or `-shm` files.
  - **`db/migrations.py`.**
    - `_stored_revision` becomes public: `stored_revision(rows)`.
    - **`ensure_current(..., before_upgrade=None)`** calls `before_upgrade(before)` only when the database is non-empty and behind head. It does so after the "ahead" check and before `command.upgrade`, inside the same write transaction.
    - If the hook raises, nothing is migrated.
  - **`db/repos/ops/snapshot.py`** (the snapshot code).
    - **`Snapshot(path, size_bytes, sha256, db_revision, suppression_count, provenance_count)`** is frozen.
    - **`snapshot(db_path, target) -> Snapshot`.** In order:
      1. `mkdir` the parent;
      2. create `target` exclusively (`"xb"`), so an existing file is never overwritten;
      3. `copy_database`;
      4. in `open_copy`:
         - read `PRAGMA integrity_check(10)`. Any result except exactly `[("ok",)]` raises `BackupNotVerified("integrity_check: <first line, ≤200 chars>")`;
         - read the revision with `stored_revision`. `None` raises `BackupNotVerified("the copy has no Alembic revision")`;
         - count each `COUNTED_TABLES` table that `sqlite_master` lists, or `None` for an absent one;
      5. after the copy is closed, read the size and a streamed SHA-256 (1 MiB chunks).

      Any failure deletes `target` (`discard`) and then re-raises. It only ever returns a verified copy.
    - **`discard(path) -> bool`** deletes the file, `missing_ok`. It returns whether the file is gone. If the delete fails, it logs `backup_copy_not_deleted` (WARNING, `code`, `file` name) and returns `False`.
  - **`db/repos/ops/backups.py`.**
    - `BackupRow` (every column);
    - `insert(...) -> str`, which generates the ULID;
    - `newest(*, kind=None, status=None)`, ordered by `started_at`, then `id`, descending;
    - `failed_after(since)`: oldest first; `since=None` means all;
    - `rotation_candidates(before, keep_id)`: rows that are `verified`, have `local_deleted_at IS NULL` and `started_at < before`, and whose id isn't `keep_id`;
    - `mark_deleted(ids, at)`.
- **Queries** (`app/queries/ops.py`, still domain + `db.repos.ops` only).
  - `last_backup()` is the newest row of any kind and status.
  - `newest_backup(kind)`.
  - `health_issues()` gives each failed backup newer than the newest verified one, oldest first: `HealthIssue(BACKUP_FAILED, id, backup_failed_text(kind, id), detail="Error: " + redact(error))`.
- **ops** (`app/ops/api.py`).
  - **`TakenBackup(kind, path, started_at, finished_at, snapshot | None, error | None, code | None)`.**
  - **`LocalBackups(clock, *, env, local_dir, snapshot: Callable[[Path], Snapshot])`:**
    - **`take(kind)`** makes no database access. It reads the clock, names the copy `local_dir / copy_name(env, now)` and calls `snapshot`. It catches every `Exception` into `error=error_text(exc)` and `code=error_code(exc)` (from jobqueue), then reads the clock again for `finished_at`.
    - **`insert(taken) -> str`** runs inside the caller's transaction.
    - **`rotate() -> int`** is called from a running use case and runs in this order:
      1. In one transaction, it reads `backup_rotation_days`. A value that doesn't parse skips rotation and logs `backup_rotation_skipped`.
      2. In the same transaction, it reads the newest verified row (the one kept) and the candidates older than `now - days`.
      3. Outside the transaction, it calls `discard` for each candidate whose path's parent is `local_dir` (compared with normcase and abspath) and whose name passes `is_copy_name(env, …)`. Any other candidate is skipped and logged (`backup_rotation_skipped`, WARNING, `backup_id`, `reason`).
      4. In a second transaction, it marks every gone file with `mark_deleted`.
      5. It logs `backups_rotated` (INFO, `count`) when the count is above 0.
    - **`@use_case record_pre_migrate(taken) -> str`** needs a verified `pre_migrate` (`ValueError` otherwise). It inserts the row, logs `backup_completed`, then calls `rotate()`.
  - **`Backups(clock, local, *, notify, queue)`:**
    - **`@use_case back_up(kind) -> str`** accepts a `JOB_KINDS` kind only (`ValueError` otherwise). Under one `threading.Lock`, so backups never overlap:
      1. It calls `take(kind)`.
      2. In one transaction it calls `insert`. When the backup failed, it also calls `notify.alert(BACKUP_FAILED, id, backup_failed_text(kind, id), (telegram, dashboard))`.
      3. If that transaction raises, it discards a verified copy and re-raises, so the job retries.
      4. After the commit it logs `backup_completed` (INFO: `backup_id`, `kind`, `size_bytes`, `file`) or `backup_failed` (ERROR: `backup_id`, `kind`, `code`). A verified backup then calls `rotate()`.

      A failed copy never raises, so the job never retries it (decision 3).
    - **`@use_case request_backup() -> Enqueued`** runs `queue.enqueue(BACKUP_DB_JOB, scope="global", discriminator=<UTC date of now>, payload={"v": 1, "kind": "manual"})` in one transaction. After the commit it logs `backup_requested` (INFO, `job_id`, `created`).
    - **`schedule() -> BackupSchedule`** is a plain read for a read-only unit:
      - `at` is `backup_time`;
      - `timezone` is `sender_timezone`, or `"UTC"` on `SettingNotSet`;
      - `missed` is `missed_daily(newest daily row's started_at, now, at, timezone)`.
  - **`EngineHealth.report()`** also reads `ops_queries.last_backup()` after readiness. `HealthReport` gains a last field, `backup` (a `BackupRow` or `None`).
- **Jobs.**
  - **`handlers.backup_db(backups)`.** Its kind is `job.payload.get("kind", "daily")`. An unknown kind, or `pre_migrate`, raises `InvalidJob`. Otherwise it calls `backups().back_up(kind)`.
  - **`triggers.py`.**
    - **`DailyRun(at, timezone, catch_up=False)`:**
      - `at` is a naive `time` in whole minutes;
      - `timezone` must load with `ZoneInfo`;
      - `catch_up` is a `bool`;
      - each rule raises `TypeError` or `ValueError`.
    - **`DailyAt(when: Callable[[], DailyRun], *, fallback: DailyRun)`** has a `grace` of 24 h.
    - `TriggerSpec` and `register_trigger` accept `Every` or `DailyAt`. The message becomes "the schedule must be an Every or a DailyAt".
  - **`scheduler.py`.** For a `DailyAt`, `start()` calls `when()`.
    - If it raises, or returns a non-`DailyRun`, the scheduler logs `trigger_schedule_failed` (WARNING, `trigger`, `code`) and uses `fallback`.
    - It adds `CronTrigger(hour, minute, timezone=ZoneInfo(tz))` with `misfire_grace_time=86400`, `coalesce=True` and `max_instances=1`. A run with `catch_up` also gets `next_run_time=now`.
    - It logs `daily_trigger_scheduled` (INFO: `trigger`, `at`, `timezone`, `catch_up`).
- **Settings**, in Section Engine after `bulk_threads`:
  - **`backup_time`** (TIME, `timezone_key=sender_timezone`, default `DEFAULT_BACKUP_TIME`), label "Daily backup time". Its unit text says it is in the sender's timezone (UTC until that is set), that a missed time is made up once when the engine next starts, and that a change takes effect when the engine restarts.
  - **`backup_rotation_days`** (INTEGER, 7–35, default 35), label "Keep local backups for (days)". Its unit text says copies older than this are deleted after each successful backup, the newest verified copy is always kept, and 35 days is the most because erased data must age out of backups.
- **Composition** (`main.py`).
  - **`build_job_types(*, alerts, backups)`** registers `backup_db`:
    - bulk lane only;
    - priority 80;
    - `max_runtime` 30 min;
    - retry limit 2;
    - `handlers.backup_db(backups)`;
    - `group=JobGroup.BACKUP`.
  - **`build_triggers(job_types, *, backup_schedule)`** registers `Trigger.BACKUP_DB` as `DailyAt(backup_schedule, fallback=DailyRun(DEFAULT_BACKUP_TIME, "UTC"))`.
  - **`build_health_issues`** appends `IssueSource("backups", ops_queries.health_issues)`.
  - **`build_local_backups(config, clock)`** builds `LocalBackups(clock, env=config.env, local_dir=config.backup_dir / LOCAL_DIR_NAME, snapshot=functools.partial(snapshot, config.data_dir / DB_FILE_NAME))`.
  - **`read_backup_schedule(units, backups) -> DailyRun`** runs `backups.schedule()` in a read-only unit, inside a fresh `contextvars.Context()`.
  - **`build_app`** wires `Backups` (with `lambda: backups` breaking the cycle, as `notify` does) and passes `backups=` to `create_app`.
  - **`serve`, step 6.**
    1. `open_database(config, before_upgrade=hook)`, where the hook is a `PreMigrateBackup(build_local_backups(config, SystemClock()))`. The hook calls `take(PRE_MIGRATE)`, keeps the result and raises `PreMigrateBackupFailed(<error>)` when it failed.
    2. **The backup fails:** exit 2 without migrating. It prints `startup check failed: pre_migrate_backup: …; the database was not migrated` and logs `pre_migrate_backup_failed` (ERROR, `code`).
    3. **The migration fails after a copy:** exit 2. The message names the copy's full path and `nextron restore <path>`. It logs `database_migration_failed` with `file`.
    4. **After a successful upgrade:** `with units.open(): local.record_pre_migrate(taken)`. A failure there logs `backup_record_failed` (ERROR) and startup continues (decision 8).
- **Web.**
  - **The panel's new "Backups" section** sits after Worker:
    - "No backup yet." or "Last backup: <YYYY-MM-DD HH:MM:SS UTC>, <status> (<kind label>)", using `started_at`. A failed one gets the `health-backup-failed` class;
    - an optional notice, `<p class="health-backup-notice" role="status">`;
    - `<form method="post" hx-post="/health/backup" hx-target="#health-panel" hx-swap="outerHTML">{{ csrf_field() }}<button type="submit" class="secondary" id="backup-now">Back up now</button></form>`.
  - **`POST /health/backup`** (`health_backup_now`, `@personal_data(True)`).
    - Its only field is `csrf_token`; anything else is `MalformedForm` (`submitted_backup`).
    - In its own unit it calls `backups.request_backup()`, then renders the panel in a read-only unit.
    - The notice reads "Backup queued; its result shows here when it finishes." when the job was created, else "A backup is already queued or running.".
  - **`create_app`** gains `backups: Backups`.
- **Tests** run as `test`, in temporary dirs only, with no wait longer than 0.05 s per step (short polling is allowed).

**Never:**
- **No copy except through the backup API.**
  - Nothing copies, links or reads the live database file's bytes (`shutil`, `Path.copy`/`copy_into`, `VACUUM INTO` or opening `nextron.db` as a file).
  - `.backup(` appears only in `db/engine.py`.
- **No new parts beyond these.** No port, adapter, dependency, credential, doctor check or notification type.
- **Out of scope:**
  - encryption and off-PC copies, and the erasure ledger (1.17);
  - the restore test (1.18);
  - `nextron restore` and recovery mode (1.19).
- **No side effect from a GET**, no secret in any file or row, and no full path in a log (file names only).
- **What not to edit.** Don't change the vendored HTMX, the spine or the planning docs, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The daily time is read when the engine starts.**
   - A change to `backup_time` or `sender_timezone` takes effect at the next restart, as `bulk_threads` already does.
   - With `sender_timezone` unset, backups run at `backup_time` UTC.
   - If the schedule can't be read, the trigger uses 10:00 UTC and logs a warning.
2. **A missed daily time is made up once, at the next engine start.** This happens when the newest daily backup started before the latest scheduled time.
   - It covers a reboot or shutdown across 10:00. APScheduler's 24-h grace only covers sleep.
   - A fresh database with no daily backup waits for its first time (or "Back up now"), so a new install or a test engine never backs up at start.
3. **A failed copy isn't retried automatically.** The `failed` row, the Telegram alert and the panel error report it, and the next daily time or "Back up now" tries again. Job retries cover only a failure to record the row.
4. **`verified`/`failed` is written once, with no state machine and no `events` rows.** Backups aren't in AD-4's machine list.
5. **Rotation goes by row.**
   - It deletes only copies with a `verified` row, inside `<backup dir>\local\` and named for this environment.
   - A file with no row (a crash mid-copy, or a `pre_migrate` copy whose migration failed) is never deleted.
   - A rotation setting that can't be read skips rotation.
   - The range is 7–35 days. 35 is the most because AD-17 says erased data ages out within the 35-day rotation; 7 keeps a week of history.
6. **Copy names carry milliseconds** (`20261117T100000.123Z`). A `pre_migrate` copy and the catch-up daily copy can start in the same second. An existing name is never overwritten: that backup fails instead.
7. **The copy keeps WAL mode in its header.** The engine refuses a non-WAL file, so Story 1.19 can swap it in unchanged. It is verified through an `immutable` read-only connection, which leaves no side files and doesn't change the bytes that were hashed. The source is a read-only connection to the live file.
8. **The `pre_migrate` copy is taken inside the migration's write transaction**, so nothing can change between the copy and the upgrade.
   - If recording its row fails after a successful upgrade, startup continues and logs `backup_record_failed`; the copy stays on disk.
   - A `pre_migrate` backup also rotates.
9. **"Back up now" uses the key `backup_db:global:<UTC date>`.** A click while a manual backup is queued or running is absorbed, and the panel says so.
10. **The panel lists each failed backup newer than the newest verified one.** The list clears when a later backup verifies; there is no Dismiss.
11. **No doctor check is added.** The AC asks for none, `backup_dir_separate` already exists, and Story 1.17 adds the off-PC warning.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Daily | `backup_db` tick on a head database | `local\nextron-test-<stamp>.db`; a `verified` `daily` row with size, a SHA-256 equal to the file's, `db_revision` head, both counts `NULL` (absent) and times; `backup_completed` | N/A |
| Manual | POST `/health/backup`; again while live | job `backup_db:global:<date>`, `kind=manual`; "Backup queued…"; then "already queued or running"; the runner gives a `manual` row | extra field: 400 |
| Not verified | the copy is malformed, the integrity check isn't `ok`, or the disk errors | a `failed` row with an error; no file left; alert `backup_failed <ULID>` (telegram and dashboard); a panel issue until a later backup verifies | logged |
| Record fails | the insert raises | copy deleted; the use case raises (job retry) | N/A |
| Rotation (35 days) | verified rows 40, 36 and 34 days old and one new | the 40- and 36-day files deleted and marked; 34 and new kept; the newest verified is kept even when 40 days old | outside `local` or misnamed: skipped and logged; file locked: retried next time |
| Schedule | start; newest daily before the latest slot, after it, or none | catch-up now; next slot; next slot. Cron at `backup_time` in `sender_timezone` (UTC unset), grace 86400 | `when()` raises: 10:00 UTC, logged |
| Pre-migrate | database at an older revision | a `pre_migrate` copy before the upgrade; its row recorded after | N/A |
| Pre-migrate fails | snapshot fails | exit 2; revision unchanged; no copy | stderr and log |
| Migration fails | upgrade raises after the copy | exit 2; the message names the copy and `nextron restore <path>`; copy kept | N/A |
| No pre-migrate | new file; at head | no copy | N/A |
| Panel | no rows; verified daily; failed manual; pre_migrate | "No backup yet."; "Last backup: …, verified (daily)"; "…, failed (manual)"; "…, verified (before a migration)" | N/A |

</frozen-after-approval>

## Code Map

- **Composition** (`nextron-engine/nextron/main.py`).
  - **What's there:**
    - `build_app` (349-423);
    - `build_job_types` (438), which uses the `lambda: notify` cycle pattern;
    - `build_health_issues` (459);
    - `build_triggers` (470), which ships empty;
    - `read_sink_allowlist` (556), the pattern for a fresh-context read-only unit;
    - `open_database` (616);
    - `serve`, step 6 (714-728).
  - **Update** the module docstring: Jobs, Triggers, Health and serve's step 6. `main` may not import sqlalchemy.
- **SQLite** (`nextron-engine/nextron/db/engine.py`). `create_read_only_engine` (183) is the URI and `LEGACY_TRANSACTION_CONTROL` pattern. Update the module docstring.
  - Verified on this PC with SQLite 3.53.1:
    - a `mode=ro` source copies uncommitted WAL frames, and stays consistent under concurrent commits;
    - the destination closes with no side files;
    - `immutable=1` leaves the copy's hash unchanged;
    - a corrupted b-tree page raises `DatabaseError` ("malformed").
- **Migrations** (`nextron-engine/nextron/db/migrations.py`).
  - `ensure_current` (137-165) gains the hook between the `before == head` return and `command.upgrade`.
  - Rename `_stored_revision` (91).
  - Rewrite the docstring's line 28.
  - The test helpers for older or failing chains are in `tests/migration_chains.py`.
- **Models and registries.**
  - `nextron-engine/nextron/db/models/ops.py`; the heartbeat model is the pattern, using `ulid_pk` and `UtcDateTime`.
  - `db/models/__init__.py` imports every model module.
  - `db/owners.py` `TABLES` and `db/pii_registry.py` `PII`.
  - `alembic/versions/0004_1_11_heartbeat.py` is the migration pattern: `op.f()` names and the `downgrade` text.
- **Repositories** (`nextron-engine/nextron/db/repos/ops/heartbeat.py`): the frozen row with `_COLUMNS` and `current().session`.
- **ops** (`nextron-engine/nextron/app/ops/api.py`). `DeadJobAlerts` is the pattern for an alert in a use case. `ALL_CHANNELS` is (telegram, dashboard), and `error_code` is already imported. `EngineHealth.report` is at 344.
- **jobqueue.** `error_text` and `error_code` are in `nextron/app/jobqueue/api.py`. `JobQueue.enqueue` is a `@command` and returns `Enqueued(job_id, created)`.
- **Jobs.** `nextron-engine/nextron/jobs/triggers.py` (`Every`, `TriggerSpec`, `Triggers`), `scheduler.py` (`start`, 101-129) and `handlers.py` (`send_alert` pattern; it imports the domain only).
- **Settings** (`nextron-engine/nextron/domain/settings_schema.py`). `Key` (127) and `SCHEMA` (821). `bulk_threads` is the pattern for the "restart" wording. `queries.setup.get` raises `SettingNotSet` and `InvalidSettingValue`.
- **Web.**
  - `nextron-engine/nextron/web/health_page.py`: `panel_context` and `submitted_job` (the strict-parser pattern).
  - `templates/health_panel.html`.
  - `web/app.py`: `create_app` (262) and the health routes (543-565). Update the module docstring's route list.
  - `static/app.css`.
- **Tests to update** (pins found by the survey):
  - **Migration head and chain.**
    - `HEAD` in `tests/contracts/test_persistence_contracts.py:72` and `tests/unit/test_migrations.py:37`;
    - `tests/app/test_serve_process.py:252`;
    - the chain probes in `test_persistence_contracts.py:670-682`: the numbers shift to 0008/0009, "revision 8".
  - **Job types and triggers.**
    - The names `("send_alert", "backup_db")` in `test_job_contracts.py:286` and `test_runner_lifespan.py:143`.
    - `build_job_types(alerts=…)` callers, which gain `backups=`:
      - `tests/app/test_budget.py:50`;
      - `test_dead_job_alerts.py:119`;
      - `test_engine_health.py:176,268`;
      - `test_notify.py:154`;
      - `test_job_contracts.py:282,301,305`;
      - `tests/dashboard.py:178`.
    - `test_job_contracts.py:304-312`: the trigger registry now holds `backup_db`.
    - The scheduler's trigger count, now 1: `test_runner_lifespan.py:628` and `test_serve_process.py:236-241`.
    - `match="must be an Every"` in `test_triggers.py:182-189,235-240`.
  - **Health.**
    - `test_health_contracts.py:40,124`: sources `("jobqueue", "budget", "backups")`. Keep exactly one `group=JobGroup.OTHER,` line.
    - `test_engine_health.py:265-277`: the groups gain Backup.
    - `tests/unit/test_health_page.py`: the groups, the form counts, `hx-post` count 1 → 2, and the text indexes.
  - **Settings.** `tests/unit/test_settings_schema.py:504-530`, and `test_settings_page.py:520-543` (field order).
  - **Web and dashboard.**
    - `tests/contracts/test_web_contracts.py:141-163`: routes. Add `/health/backup` after `/health/jobs/dismiss`, and to the POST list.
    - `tests/dashboard.py`: `Backups` and `create_app(backups=…)`.
  - **Source tree.** `tests/contracts/test_source_tree.py` `FILES`.
  - **Docs.** `README.md`:
    - the migrations table (620);
    - lines 593-594 and 624 (backups "later");
    - triggers (844-858);
    - settings;
    - Engine health (1092-1131).
- **Fakes.** `tests/fakes/clock.py` `FrozenClock` (`advance` it between backups, because names include milliseconds). `tests/app/test_engine_health.py` shows the pattern for real units on a temporary database.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/backup.py`, `domain/settings_schema.py`: the backup domain and the two settings.
- [x] `nextron-engine/nextron/db/engine.py`, `db/migrations.py`: `copy_database`, `open_copy`, `stored_revision` and the `before_upgrade` hook.
- [x] `nextron-engine/nextron/db/alembic/versions/0007_1_16_backups.py`, `db/models/ops.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/ops/snapshot.py`, `db/repos/ops/backups.py`, `db/repos/ops/__init__.py`: the table and the snapshot code.
- [x] `nextron-engine/nextron/app/queries/ops.py`, `app/ops/api.py`: the queries, `LocalBackups`, `Backups`, and `HealthReport.backup`.
- [x] `nextron-engine/nextron/jobs/handlers.py`, `jobs/triggers.py`, `jobs/scheduler.py`: the handler, `DailyRun` and `DailyAt`, and the cron scheduling.
- [x] `nextron-engine/nextron/main.py`: the registrations, `build_local_backups`, `read_backup_schedule`, the `build_app` wiring, and `serve`'s `pre_migrate`.
- [x] `nextron-engine/nextron/web/health_page.py`, `web/app.py`, `templates/health_panel.html`, `static/app.css`: the panel section and the POST.
- [x] `nextron-engine/tests/unit/test_backup_domain.py`:
  - `copy_name` and `is_copy_name`;
  - `latest_slot`, before, at and after the slot, across a DST change in `America/Los_Angeles`, and in UTC;
  - `missed_daily`;
  - the labels;
  - the texts.
- [x] `nextron-engine/tests/unit/test_db_engine.py`:
  - the copy is a single file with the WAL header, and the live file's bytes are unchanged;
  - `open_copy` leaves no side files and the hash stays the same;
  - a missing source raises and creates nothing.
- [x] `nextron-engine/tests/app/test_backups.py`, with real units on a temporary database at head and a temporary backup dir:
  - every Matrix row for `back_up`, `request_backup`, rotation, `schedule()`, `record_pre_migrate`, the lock (two threads never copy at once) and `health_issues`;
  - the failure cases:
    - a real malformed copy (corrupt the source with a planted bad page, or patch `copy_database` to write a damaged file);
    - an injected snapshot that raises;
    - a non-`ok` integrity check.
- [x] `nextron-engine/tests/unit/test_serve.py`, through the `migration_chains` helpers:
  - the Pre-migrate, Pre-migrate fails, Migration fails and No pre-migrate rows;
  - the record failure, which continues.
- [x] `nextron-engine/tests/unit/test_triggers.py`, `tests/unit/test_scheduler.py`:
  - `DailyRun` and `DailyAt` refusals;
  - the cron job's fields and timezone, and the 86400 grace;
  - `catch_up` gives `next_run_time` now;
  - the fallback when `when()` raises.
- [x] `nextron-engine/tests/unit/test_health_page.py`:
  - each backup line;
  - the exact form attributes and the button `id`;
  - the POST: created, absorbed, extra field (400), no CSRF (403), `no-store`.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py`, with the real `build_app` on `open_database(config)`:
  - the scheduler registers the `backup_db` cron trigger;
  - a POST `/health/backup`, run by the real runner, leaves a `verified` `manual` row and a copy under `<backup dir>\local` (polling up to a few seconds);
  - a failed backup appears in the panel's error list.
- [x] `nextron-engine/tests/contracts/test_backup_contracts.py`, each with a probe proof:
  - the copy rules: no `shutil` import, no `.copy_into(`, no `VACUUM INTO`, and `.backup(` only in `db/engine.py`;
  - `backup_db` is bulk-only with group Backup;
  - the trigger is registered after its job type;
  - `MAX_ROTATION_DAYS == 35` and the setting's maximum.
- [x] The pins in the Code Map, and `nextron-engine/README.md`: a "Backups" section (daily, catch-up, Back up now, rotation, `pre_migrate`, file names, what isn't done yet), and the updates listed in the Code Map.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and all 29 contracts are kept.
- Given `serve` on a new database or one already at head, when it starts, then no `pre_migrate` copy is taken. On a new database (no daily row yet) the scheduler doesn't catch up either, so the backup dir stays empty.

## Implementation Notes

- **Where the code is.**
  - Domain: `nextron/domain/backup.py`.
  - Copy primitives: `nextron/db/engine.py` (`copy_database`, `open_copy`, `_file_uri`).
  - Snapshot code: `nextron/db/repos/ops/snapshot.py` (`snapshot`, `discard`, `Snapshot`).
  - Table repository: `nextron/db/repos/ops/backups.py`.
  - Services: `nextron/app/ops/api.py` (`TakenBackup`, `LocalBackups`, `Backups`, `HealthReport.backup`).
  - Queries: `nextron/app/queries/ops.py` (`last_backup`, `newest_backup`, `health_issues`).
  - Jobs: `handlers.backup_db`, `triggers.DailyRun`/`DailyAt`, and the scheduler's `CronTrigger`.
  - Wiring in `main.py`: `build_local_backups`, `read_backup_schedule`, `PreMigrateBackup` and `serve`'s step 6.
  - Panel and POST: `web/health_page.py`, `web/app.py` and `templates/health_panel.html`.
  - Migration: `0007_1_16_backups`.
- **Small departures and additions:**
  - `backup_requested` logs `job_created`, not `created`, because Python's logging reserves `created`.
  - `LocalBackups.insert` is an `@command`: it joins the use case's transaction.
  - `TakenBackup.verified`, and the domain's `is_env_name`/`ENV_PATTERN`. `LocalBackups` refuses a bad env, a non-`Path` `local_dir` or a non-callable snapshot.
  - Rotation's skip reasons are `outside_local_dir`, `not_a_copy_name` and `setting_unreadable`.
  - `db_revision` is `String(64)`. `copy_database` opens the destination with a `mode=rw` URI, so it never creates the target. `snapshot` reads `PRAGMA integrity_check(10)`.
  - The daily time is read on the event loop when the scheduler starts. It is one short read, which can wait up to the 5-s busy timeout.
  - The copy contract flags any `.copy(...)` call with arguments, as a stand-in for `Path.copy`, and reads only files under `nextron/`.
- **Fixed during step 3** (the matrix row "Record fails … (job retry)").
  - jobqueue retries only a retryable `AdapterError`. So when recording the row fails, `Backups.back_up` deletes the copy, logs `backup_record_failed` (ERROR, `kind`, `code`, `file`) and raises `AdapterError(True, "backup_not_recorded", …)` from the error.
  - `test_a_backup_job_whose_row_cannot_be_recorded_is_retried` shows the job going back to `queued`.
- **`app.css`.** The build rewrote it with CRLF throughout. It is a `-text` file with mixed line endings, so the original bytes were restored with only the new 19-line block inserted.

Review fixes (pass 1, P1–P10; see the Review Triage Log):
- **P1.** `_rotate_after(local, backup_id)` wraps `rotate()` in `back_up` and `record_pre_migrate`. A failure logs `backup_rotation_failed` (ERROR, `backup_id`, `code`, traceback) and never fails the backup. Tests:
  - through the job, rotation failing in its first transaction and in its second (the job ends `done`);
  - `record_pre_migrate`;
  - `serve` (exit 0, the row recorded, no `backup_record_failed`).
- **P2.** `_file_uri` rewrites a UNC URI (`file://server/…`) to `file:////server/…`, with its percent-encoding kept. It has Windows-only unit tests for a drive path and a UNC path, each with a space and `#`. On this PC, `file:////localhost/C$/…` opened with SQLite 3.53.1 and `file://nas/…` raised "invalid uri authority". Doctor's `create_read_only_engine` keeps its own `as_uri()`; it is pre-existing and was outside the finding.
- **P3.** The failed-migration hint is `nextron restore "<path>"`, quoted.
- **P4.** The README names exactly what `test_backup_contracts.py` refuses:
  - a `shutil` import;
  - `.copy(…)` with arguments;
  - `.copy_into(`, `hardlink_to(`, `symlink_to(`, `os.link(` and `os.symlink(`;
  - `VACUUM INTO`;
  - `.backup(` outside `db/engine.py`.
- **P5.** `test_build_local_backups_copies_the_data_dirs_database_into_backup_dir_local` calls `main.build_local_backups(load_bootstrap(), clock)` and takes a real copy.
- **P6 and P7.** `test_request_backup_queues_one_manual_job_at_a_time_and_absorbs_a_click_while_it_is_live` also shows a click after the job finished creating a new job.
- **P8.** `test_runner_lifespan.py::test_the_scheduler_reads_the_stored_time_zone_and_a_missed_daily_backup` runs the real app with 07:30, America/New_York and a missed daily row. It asserts `catch_up` True, the cron fields and zone, and `next_run_time` at start, and the runner records the catch-up backup.
- **P9.** A parametrized raw-SQL test: 11 invalid rows, each refused naming its constraint, plus a valid verified row and a valid failed row.
- **P10.** The 35-day rotation test takes another backup a minute later: that rotation returns 0, logs no `backups_rotated` and leaves the marks unchanged.

**Final verification** (after pass 1):
- `uv run pytest -q`: 4161 passed, 2 skipped (`ai_eval` and the symlink test), in 249 s.
- `uv run lint-imports`: 29 kept, 0 broken.

Both ran through the shared suite lock.

Before the review fixes: 4142 passed, 2 skipped, and 29 kept.

Not verified here:
- a real PC sleep or reboot across the daily time (Story 1.20's spike);
- a backup dir on a real network share (only the `\\localhost\C$` URI probe);
- the "Back up now" button in a real browser (only the exact `hx-*` attributes and the answers are tested);
- a large production-size database's copy time.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (12 findings), EC = edge-case hunter (10), VG = verification gap (3 gaps and 3 other findings).
- **Counts:** 28 raw findings. BH12 makes five separate claims (a–e), so there are 32 claims, which make 22 entries after deduplication.
- **Outcomes:**
  - 10 entries patched (P1–P10): 3 code fixes, 2 doc or test-name fixes and 5 test gaps;
  - none deferred;
  - 12 rejected.
- **No loopback.** No finding is an intent gap or a bad spec.
- **Rejected only because the fix would change the approved spec:**
  - B (decision 5);
  - C, Q and S (the AC's rotation trigger, its "newest verified copy is never deleted" and its "exits 2 without migrating");
  - F (decision 9);
  - J (decision 1).
- **Before review (step 3).** The matrix row "Record fails … (job retry)" wasn't met: jobqueue retries only a retryable `AdapterError`, so the job went `dead`. `back_up` now raises `AdapterError(True, "backup_not_recorded")` from the error and logs `backup_record_failed`. Tests cover the retry, and the implementer's `deferred-work.md` entry was withdrawn.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC1, VG-other1, VG-other2, VG-other3, BH12d | `rotate()` runs after the row commits, unguarded. A rotation error (a locked database while reading the setting, or `mark_deleted`) makes a verified backup's job `dead` with a false `job_dead`, and `serve` logs `backup_record_failed` for a recorded row. | medium | Verified in `Backups.back_up` and `LocalBackups.record_pre_migrate`. The error leaves the use case, and `JobQueue.fail` treats a non-`AdapterError` as `fail`. That contradicts the README's "only a failure to record the row raises". The fix: catch the error, log `backup_rotation_failed` (ERROR, `backup_id`, `code`) and keep the backup's success. Add a test on both paths. | P1 |
| B | BH2, EC8 | A copy with no row (a failed migration's `pre_migrate` copy, a crash mid-copy, a failed record, a failed `discard`) or a row outside `local` is never deleted or reported, so erased data can outlive AD-17's 35 days. | low | True, by decision 5: "a file with no row … is never deleted". Each case is rare. The migration-failure copy is named to the Operator on stderr. Flagging or sweeping such files would change the approved decision. | reject |
| C | BH3 | Rotation runs only after a verified backup, so while backups keep failing old copies outlive the window. | low | True, as the AC states: "When a backup succeeds, Then they are deleted". Every failure raises a Telegram alert and a panel error meanwhile. | reject |
| D | BH4 | There's no free-space check, and the default backup dir shares a volume with the data dir, so a copy could fill the disk under the live database. | low | Verified for the defaults in `bootstrap.py`. But the database is small (one copy a day, 35 kept), and a free-space guard is new behaviour beyond the AC. Story 1.17 adds the off-PC copy and its volume warning. | reject |
| E | BH5, EC3, BH12e (part) | A UNC backup dir (`\\nas\share`) fails every backup and every upgrade's `pre_migrate`: `Path.as_uri()` gives `file://nas/share/…`, which SQLite refuses ("invalid uri authority"). | medium | Verified on this PC with SQLite 3.53.1. `file://nas/…` raises "invalid uri authority", and the four-slash form `file:////localhost/C$/…` opens. The fix: `_file_uri` writes UNC paths with an empty authority, with a unit test. | P2 |
| F | BH6, EC6 | "Back up now" doesn't see a live daily tick (another key). The key's date absorbs only while live, a queued job across midnight UTC isn't absorbed, and clicks are unlimited. | low | True, and specified by decision 9 (`backup_db:global:<UTC date>`, absorbed while queued or running). A duplicate is a second full copy, serialised by the lock, and rotated like any other. | reject |
| G | BH7, BH12e (part) | The failed-migration hint `nextron restore <path>` is printed unquoted, so a path with a space (a Windows user name) splits when pasted. | low | Verified at `main.py:878`. A direct correction: quote the path. Update the `test_serve.py` assertion. | P3 |
| H | BH8 | The panel shows only the newest backup of any status: no "last verified" time while failing, and no queued or running state. | low | The AC asks for "the last backup's time, status and kind", which is shown. The failure list names each failed backup since the last verified one. Another line is a new feature. | reject |
| I | BH9 | Nothing alerts when backups simply stop running (no row is written). | low | The panel's "Last backup: <time>" shows the age at a glance. AD-20's monitoring list names failed backups, not overdue ones. Story 1.18's monthly restore test fails on a missing copy. A staleness source is a new feature. | reject |
| J | BH10, EC5 | When the startup read fails, the fallback (10:00 UTC, no catch-up) is silent apart from a WARNING, and the read runs on the event loop. | low | Specified by decision 1 ("uses 10:00 UTC and logs a warning"). The read is one short `SELECT` at startup. The likely cause, a hand-edited setting, also shows on the Settings page. | reject |
| K | BH11 | The README says nothing else "copies, links or reads the file's bytes (`tests/contracts/test_backup_contracts.py`)", but the contract doesn't check `open()` or `read_bytes` of `nextron.db`, `os.replace`, or a subprocess copy. | low | Verified: the contract covers `shutil`, `.copy(…)`, `.copy_into(`, links, `VACUUM INTO` and `.backup(` only, which are the spec's task list. The fix is a direct README correction naming what the contract checks. | P4 |
| L | BH12a | `test_the_partial_snapshot_is_bound_to_the_database` builds its own `functools.partial` and never calls `main.build_local_backups`, so a wrong binding passes. | low | Verified at `tests/app/test_backups.py:931`. The fix: call `main.build_local_backups(config, clock)` and assert `local_dir` and the bound database path. | P5 |
| M | BH12b | `test_request_backup_queues_one_manual_job_a_day_…` claims "one a day", but the behaviour is one at a time. | low | Verified: the key absorbs only while the job is live. A direct rename. | P6 |
| N | BH12c | No test checks that a click after the manual job finishes queues a new one. | low | Verified: no such test. A direct addition to the same test. | P7 |
| O | EC2 | A `DatabaseStateError` after a verified `pre_migrate` copy prints no copy or restore hint. | low | After the hook, only the post-upgrade revision read can raise it, and that needs several revisions, which the linear chain can't produce. `ForeignKeyCheckError` and migration errors take the branch that names the copy. Reaching it needs a broken migration. | reject |
| P | EC4 | A row stamped in the future (the clock was ahead, then corrected) hides later failures, sticks the last-backup line and blocks the catch-up. | low | True only after a clock set forward and then back. The next backup past that time clears it. Filters on `now` add branches to every read. Precedent: Story 1.15 P4 (a future heartbeat). | reject |
| Q | EC7 | When every backup fails for more than 35 days, the kept newest verified copy outlives AD-17's window. | low | Required by the AC: "except the newest verified copy is never deleted". Each failure meanwhile raises a Telegram alert and a panel error. | reject |
| R | EC9 | The failure list grows by one entry per failed backup until one verifies. | low | One failure a day, plus clicks. The list clears on the next verified backup. Capping it needs a summary issue. Precedent: Story 1.15 entry K. | reject |
| S | EC10 | A failed `pre_migrate` copy only prints and logs before exit 2: no row, no Telegram alert and no panel error. | low | Specified by the AC ("startup exits 2 without migrating"). The engine isn't running, so it has no alert channel, and `backups` may not exist before the migration. The supervisor (Story 1.21) sees exit 2. | reject |
| T | VG1 (gap) | `read_backup_schedule`'s mapping (`catch_up=missed`, the time and the zone) is never tested through the real app. The only real-app test uses a new database whose values equal the fallback. | medium | Pre-verified: a hard-coded `DailyRun(time(10, 0), "UTC")` passes every test. Add a `test_runner_lifespan.py` test with a missed `daily` row, `backup_time` 07:30 and `sender_timezone` `America/New_York`, asserting `catch_up` True, `next_run_time` now and the cron fields. | P8 |
| U | VG2 (gap) | `ck_backups_outcome`, `ck_backups_counts` and `ck_backups_sha256` aren't pinned by any test. | low | Pre-verified: deleting them fails nothing, because the drift check doesn't compare CHECKs. Add raw-SQL violations, each refused naming its constraint, in the style of `test_heartbeat.py`. | P9 |
| V | VG3 (gap) | Rotation's `local_deleted_at IS NULL` filter is never observed by a second rotation. | low | Pre-verified: dropping the filter makes every later rotation re-count old rows, and no test notices. Extend the 35-day test with a later backup whose rotation returns 0 and logs no `backups_rotated`. | P10 |

## Design Notes

```python
@use_case
def back_up(self, kind: BackupKind) -> str:            # ops.Backups, on a bulk-lane thread
    with self._lock:                                    # one backup at a time
        taken = self._local.take(kind)                  # copy, verify, hash; no DB; never raises
        try:
            with current().transaction():
                backup_id = self._local.insert(taken)
                if taken.error is not None:
                    self._notify.alert(BACKUP_FAILED, backup_id,
                                       backup_failed_text(kind, backup_id), ALL_CHANNELS)
        except BaseException:
            if taken.snapshot is not None:
                discard(taken.path)                     # never a copy without a row
            raise
        ...log...
        if taken.snapshot is not None:
            self._local.rotate()
        return backup_id
```

The scheduler stays generic. `DailyAt` only knows "a time, a timezone and whether to fire now". The backup rules (`missed_daily`) live in the domain and are read through `Backups.schedule()`.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` and the symlink test skipped).
- `uv run lint-imports` -- expected: 29 contracts KEPT.
