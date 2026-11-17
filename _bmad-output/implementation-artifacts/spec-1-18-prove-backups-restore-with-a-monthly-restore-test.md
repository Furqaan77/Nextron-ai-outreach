---
title: 'Story 1.18: Prove backups restore with a monthly restore test'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '30c6b981cc275478c72a1846493feaec483f1577'
review_loop_iteration: 0
story_key: '1-18-prove-backups-restore-with-a-monthly-restore-test'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-17-encrypt-each-backup-and-copy-it-off-the-pc.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing proves the encrypted off-PC copies can be restored. A wrong passphrase, a damaged copy or a copy missing Suppression or Provenance rows would only be found during a real disaster.

Requirements: FR-58, AD-20, OP-7, NFR-16.

**Approach:** A new `ops` service, `RestoreTests`, runs once a month and on demand. It fetches and decrypts the newest off-PC copy into a temporary folder under the backup dir, then opens it read-only and checks it. Each check's result and an overall `pass` or `fail` are recorded in a new `restore_tests` table, and the temporary folder is deleted. A failure raises a `restore_test_failed` ops alert and a health-panel error. The panel shows the last restore test and offers "Run restore test now".

The acceptance criteria are Story 1.18 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Domain** (`nextron/domain/restore_test.py`, new, stdlib only, takes `now`).
  - **Constants.**
    - `RESTORE_TEST_JOB = "restore_test"`: the job type and the AD-10 trigger;
    - `RESTORE_TEST_FAILED = "restore_test_failed"`: the alert kind and the health-issue kind;
    - `RESTORE_DIR_NAME = "restore_test"`: the folder under the backup dir;
    - `RESTORE_TEST_DELAY = timedelta(hours=1)`;
    - `DEFAULT_RESTORE_TEST_TIME = time(11, 0)`.
  - **Enums.**
    - `RestoreTestKind`: `monthly` and `manual`. `.label` is "monthly" or "manual".
    - `RestoreResult`: `pass` and `fail`.
    - `CheckOutcome`: `pass`, `fail` and `skipped`.
    - `RestoreCheck`, in order: `fetch`, `integrity`, `revision`, `suppression`, `provenance`. `.label` is "fetch and decrypt", "integrity check", "Alembic revision", "Suppression List count" or "Provenance Records count".
    - `RestoreFailure`, the reasons:
      - fetch: `no_offpc_copy`, `offpc_not_configured`, `copy_missing`, `target_unavailable`, `passphrase_not_set`, `wrong_passphrase`, `tampered`, `truncated`, `bad_format`, `fetch_failed` and `checksum_mismatch`;
      - the other checks: `integrity_failed`, `revision_behind`, `revision_unknown`, `suppression_fewer` and `provenance_fewer`.
  - **`RestoreOutcome`** (frozen): `result`, `checks` (one `CheckOutcome` per `RestoreCheck`, in order), `reason` (the first failing check's `RestoreFailure`, `None` on a pass), `detail` (`str | None`, at most 500 characters), `copy_revision`, `suppression_count` and `provenance_count`.
  - **`fetch_failure(reason, detail=None) -> RestoreOutcome`:** `fetch` fails and the other checks are `skipped`.
  - **`judge(*, integrity, revision, head, revision_known, counts, recorded) -> RestoreOutcome`.** `fetch` passes. Then:
    - `integrity` passes only for exactly `"ok"`. Otherwise it fails with `integrity_failed` and detail `integrity_check: <text>`, and the other three checks are `skipped`.
    - `revision` passes when it equals `head`. A known revision that isn't head gives `revision_behind`, and anything else (`None` included) gives `revision_unknown`. The detail is `copy revision <rev or none>; code head <head>`.
    - For each counted table: a recorded count of `None` (absent at backup time) gives `skipped`. A copy count that is `None` or below the recorded one fails, with detail `<table>: copy <n or absent>, backup <m>`. Otherwise it passes.
    - `result` is `pass` when no check failed. The detail joins the failing details with `"; "`, cut to 500 characters.
  - **The schedule.**
    - `restore_test_time(backup_time) -> time`: `backup_time + RESTORE_TEST_DELAY`, wrapping past midnight.
    - `restore_test_due(newest_monthly_started_at, now, timezone) -> bool`: `None`, or the newest started in an earlier calendar month than `now`, both read in `timezone`.
    - `missed_restore_test(newest, now, at, timezone) -> bool`: `newest is not None`, the test is due, and `backup.latest_slot(now, at, timezone)` falls in `now`'s calendar month in `timezone`. A brand-new database is never missed.
    - `RestoreTestSchedule(at, timezone, missed)` is frozen.
  - **`restore_test_failed_text(kind, test_id, reason)`** is exactly `"Restore test failed: <kind> <ULID>, <reason>"`: IDs and codes only.
- **Persistence.**
  - **Migration `0009_1_18_restore_tests`** (`downgrade()` raises) creates `restore_tests`, owned by `ops`. Its columns:
    - `id` (ULID PK);
    - `kind` and `result` (String 16, each with an `IN` check);
    - `started_at` and `finished_at`;
    - `backup_id` (String 26, nullable, no foreign key);
    - `check_fetch`, `check_integrity`, `check_revision`, `check_suppression` and `check_provenance` (String 16, each `IN ('pass','fail','skipped')`);
    - `reason` (String 32, nullable);
    - `copy_revision` (String 64, nullable);
    - `suppression_count` and `provenance_count` (nullable, ≥ 0);
    - `detail` (String 500, nullable).

    Its constraints:
    - `ck_restore_tests_outcome`:
      - a `pass` has no reason and no detail, a `backup_id`, `pass` for fetch, integrity and revision, and `pass` or `skipped` for both counts;
      - a `fail` has a reason and at least one check that is `fail`.
    - `ck_restore_tests_counts`.
    - `ix_restore_tests_started_at`.

    Add the `RestoreTestRecord` model, `owners.py` (`ops`) and `pii_registry.py`. `detail` is `redact`; every other column is `none`.
  - **`db/repos/ops/restore_tests.py`** (new): `RestoreTestRow` (every column), `insert(...) -> str`, `newest(*, kind=None, result=None)` and `failed_after(since)` (oldest first; `None` means all).
  - **`db/repos/ops/backups.py`:** `newest_offpc_copy()` is the newest row (by `started_at`, then id) with `offpc_status = 'copied'` and `offpc_deleted_at IS NULL`.
  - **`db/repos/ops/snapshot.py`.** `inspect_copy(path) -> CopyInspection(integrity, revision, head, revision_known, suppression_count, provenance_count)` reads the copy only through `open_copy` (`mode=ro&immutable=1`).
    - `integrity` is `"ok"`, the first line of `PRAGMA integrity_check(10)` (at most 200 characters), or `error: <class name>` when opening or reading the copy raises. When `integrity` isn't `"ok"`, it stops there, with the revision and counts `None`.
    - `revision` is `stored_revision(...)`, or `None` when that raises `DatabaseStateError`.
    - `head` is `head_revision()`, and `revision_known` is `revision in known_revisions()`.
    - The counts are as in `snapshot`.
    - It never writes, migrates or attaches to the copy.
- **Queries** (`app/queries/ops.py`).
  - `last_restore_test()` and `newest_restore_test(kind)`.
  - `newest_offpc_copy()`.
  - `health_issues()` appends one `HealthIssue(RESTORE_TEST_FAILED, id, restore_test_failed_text(kind, id, reason), detail="Error: " + redact(detail or reason))` per failed test newer than the newest passed one, oldest first. The registry is unchanged: the issues come through the existing `backups` source.
- **ops** (`app/ops/api.py`).
  - **`OffpcBackups.fetch(folder, name, target) -> FetchedCopy`:** `self._targets(folder).get(name, target)`.
  - **`RestoreTests(clock, *, env, backup_dir, offpc: OffpcBackups, inspect: Callable[[Path], CopyInspection], notify, queue)`:**
    - **`@use_case run(kind) -> str | None`.** Under one `threading.Lock`:
      1. In one transaction:
         - for `monthly`, it reads `sender_timezone` (`UTC` on `SettingNotSet`) and the newest monthly test. When `restore_test_due` is false, it logs `restore_test_not_due` (INFO) and returns `None`, with no row;
         - it reads `newest_offpc_copy()` and `offpc.destination()`. An `InvalidSettingValue` gives `offpc_not_configured`, with detail `invalid_setting_value`.
      2. Outside any transaction it runs `_check`, which never raises an `Exception`:
         - no row gives `no_offpc_copy`;
         - a destination problem gives `offpc_not_configured` (detail: the problem);
         - otherwise it prepares the work folder (below) and calls `fetch(folder, row.offpc_name, <work>\copy.db)`;
         - an `AdapterError` maps its code to a reason (`backup_copy_missing`, `backup_target_unavailable`, `credential_not_set`, `backup_wrong_passphrase`, `backup_tampered`, `backup_truncated` and `backup_bad_format`; anything else is `fetch_failed`, with the code as detail). Any other `Exception` (`MemoryError` or `ValueError` from scrypt included) is `fetch_failed`, with detail `error_code(exc)`;
         - a `plain_sha256` that differs from `row.sha256` gives `checksum_mismatch`;
         - otherwise `judge(...)` runs on `inspect(copy)` against the row's recorded counts.
      3. **The work folder.**
         - It is `<backup dir>\restore_test\`, created with `mkdir(parents=True, exist_ok=True)`.
         - First, every leftover `nextron-<env>-*` folder there is deleted: the files directly inside, then the folder.
         - Then it makes `tempfile.mkdtemp(prefix="nextron-<env>-", dir=...)`.
         - In a `finally`, the work folder is deleted the same way. A failed delete logs `restore_test_cleanup_failed` (WARNING, `code`) and leaves the result as it is.
         - No `shutil`.
      4. In one transaction it `insert`s the row. For a `fail` it also calls `notify.alert(RESTORE_TEST_FAILED, id, restore_test_failed_text(...), ALL_CHANNELS)`. If that transaction raises, it logs `restore_test_record_failed` (ERROR, `code`) and raises the retryable `AdapterError(True, "restore_test_not_recorded", …)`.
      5. After the commit it logs `restore_test_passed` (INFO: `test_id`, `kind`, `backup_id`) or `restore_test_failed` (ERROR: `test_id`, `kind`, `reason`), then returns the id.
    - **`@use_case request_restore_test() -> Enqueued`** enqueues `restore_test` (scope `global`, discriminator the UTC date, payload `{"v": 1, "kind": "manual"}`) and logs `restore_test_requested` (`job_id`, `job_created`).
    - **`schedule() -> RestoreTestSchedule`** is a plain read:
      - `at` is `restore_test_time(backup_time)`;
      - `timezone` is `sender_timezone`, or `"UTC"`;
      - `missed` is `missed_restore_test(newest monthly started_at, …)`.
  - **`HealthReport`** gains a last field, `restore_test`, which `EngineHealth.report()` reads with `last_restore_test()`.
- **Jobs.**
  - **`handlers.restore_test(tests)`.** The kind is `payload.get("kind", "monthly")`. An unknown kind raises `InvalidJob`. Otherwise it calls `tests().run(kind)`.
  - **The `restore_test` job type:**
    - bulk lane only;
    - priority 70;
    - `max_runtime` 30 min;
    - retry limit 2;
    - `group=JobGroup.BACKUP`.
  - **`Trigger.RESTORE_TEST`** is `DailyAt(restore_test_schedule, fallback=DailyRun(DEFAULT_RESTORE_TEST_TIME, "UTC"))`, registered after `backup_db`.
- **Composition** (`main.py`).
  - `build_job_types(..., restore_tests)` and `build_triggers(..., restore_test_schedule)`.
  - `build_restore_tests(config, clock, offpc, notify, queue)` passes `inspect=snapshot.inspect_copy`.
  - `read_restore_test_schedule(units, tests)` works like `read_backup_schedule`.
  - `build_app` passes `restore_tests=` to `create_app`.
- **Web.**
  - **A "Restore test" section** sits after Backups:
    - "No restore test yet." or "Last restore test: <YYYY-MM-DD HH:MM:SS UTC>, <result> (<kind label>)". A fail adds `: <reason>; failing checks: <labels, comma-separated>` and the `health-restore-failed` class;
    - an optional notice, `<p class="health-restore-notice" role="status">`;
    - the form `method="post" hx-post="/health/restore-test"` with `{{ csrf_field() }}` and `<button … id="restore-test-now">Run restore test now</button>`.
  - **`POST /health/restore-test`** (`health_restore_test_now`, `@personal_data(True)`).
    - Its only field is `csrf_token`; anything else is `MalformedForm`.
    - It queues the test in its own unit, then renders the panel read-only.
    - The notice is "Restore test queued; its result shows here when it finishes." when the job was created, else "A restore test is already queued or running.".
- **Tests** run as `test`, in temporary folders only, with `ScryptParams(10, 8, 1)`.

**Never:**
- **The live database stays untouched.** The test never opens, copies, replaces or migrates it.
- **Nothing is started against the copy.** No SQLAlchemy engine, unit of work, Alembic command, adapter, runner or scheduler. The copy is read only through `open_copy`.
- **No plaintext outside the work folder.** The plaintext never goes to `%TEMP%`, the data dir or the off-PC folder, and no secret goes into a row, log or response.
- **No side effect from a GET.** The copy contract (`test_backup_contracts.py`) isn't narrowed.
- **Out of scope:**
  - `nextron restore` and recovery mode (1.19);
  - checking the ledger copy;
  - a new setting or doctor check;
  - a Dismiss for restore-test issues.
- **What not to edit.** Don't change the vendored HTMX, the spine or the planning docs, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **"Monthly" means once per calendar month.**
   - The trigger fires daily, one hour after `backup_time`, in `sender_timezone` (UTC until it is set). The job runs a test only when no monthly test has started this calendar month, so it usually tests that morning's copy.
   - If the engine was off when this month's slot passed, the test runs once at the next start. A new database waits for its first slot.
   - The daily check is itself a run of the `restore_test` job, so "Last successful runs" shows the check. The Restore test section shows the last real test.
2. **The copy under test** is the newest `backups` row that is `copied` and not rotated out, fetched from the folder configured now. If the folder changed since, the result is `copy_missing` until the next daily copy.
3. **The fetch check also compares the plaintext SHA-256** with the row's (`checksum_mismatch`).
4. **The work folder is `<backup dir>\restore_test\`, not `%TEMP%`.** The plaintext holds personal data, and that folder already holds the local plaintext copies. A crash's leftovers are deleted at the next test.
5. **The copy is opened with `open_copy`** (`mode=ro&immutable=1`), stricter than the AC's `mode=ro`, so SQLite never creates `-wal` or `-shm` files beside it.
6. **A failed integrity check skips the other checks.** A revision behind head (`revision_behind`) is told apart from an unknown one. After an upgrade, a test before the next daily copy fails with `revision_behind`, as the AC requires.
7. **Manual tests don't count toward the monthly test.**
8. **Each failed test newer than the newest passed one is a panel issue** until a later test passes, with no Dismiss, as for backups.
9. **A failed restore test isn't retried.** Only a failure to record its row retries the job (and reruns the test).
10. **The row is written once.** There is no state machine and no `events` row.
11. **No new setting or doctor check.** The doctor stays at twelve checks.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Pass | newest copied row; folder and passphrase set; the copy at head with counts ≥ recorded | row `pass`, five checks `pass` (or a count `skipped` when recorded absent), `backup_id`, `copy_revision`, counts; work folder gone; `restore_test_passed` | N/A |
| No copy | no `copied` row | `fail`, `no_offpc_copy`, fetch `fail`, the rest `skipped`; alert `restore_test_failed` (telegram and dashboard) "Restore test failed: monthly <ULID>, no_offpc_copy"; a panel issue | N/A |
| Not configured | folder blank or in the data or backup dir | `fail`, `offpc_not_configured` | N/A |
| Fetch errors | missing file; folder offline; no passphrase; wrong passphrase; tampered; truncated; bad format; `MemoryError` | `copy_missing`; `target_unavailable`; `passphrase_not_set`; `wrong_passphrase`; `tampered`; `truncated`; `bad_format`; `fetch_failed` | no work folder left |
| Checksum | fetched SHA-256 ≠ row | `checksum_mismatch` | N/A |
| Integrity | the copy isn't a database, or fails `integrity_check` | `integrity_failed`, detail `integrity_check: …`; revision and counts `skipped` | N/A |
| Revision | the copy at an older known revision; at an unknown one | `revision_behind`; `revision_unknown` | N/A |
| Counts | a copy with fewer suppression or provenance rows than recorded | `suppression_fewer` / `provenance_fewer`; both fail, the reason is the first | N/A |
| Monthly | a tick when a monthly test exists this month | returns `None`, no row, `restore_test_not_due` | N/A |
| Schedule | start; newest monthly last month and today's slot passed this month; or none | catch-up now; none for a new DB | `when()` raises: 11:00 UTC |
| Leftovers | an old `nextron-test-*` folder in `restore_test\` | deleted before the test | delete fails: logged |
| Record fails | the insert raises | retryable `AdapterError`; job retried | logged |
| Panel | none; pass; fail | "No restore test yet."; "Last restore test: …, pass (monthly)"; "…, fail (manual): revision_behind; failing checks: Alembic revision" (failed class) | N/A |

</frozen-after-approval>

## Code Map

- **The patterns to follow** are 1.16 and 1.17's `Backups`, `OffpcBackups`, `request_backup`, `schedule` and `_copy_off_pc` in `nextron-engine/nextron/app/ops/api.py`. The module docstring is binding documentation: add a "Restore tests" paragraph. `ALL_CHANNELS`, `error_code` and `error_text` are already imported.
- **Domain.** `nextron/domain/backup.py`: `latest_slot`, `missed_daily`, `BackupSchedule`, `backup_failed_text` and `_check_aware` are the patterns, and `latest_slot` is reused. `domain/readiness.CheckResult` already exists, so don't name anything `CheckResult`.
- **Persistence.**
  - `db/alembic/versions/0007_1_16_backups.py` is the create-table pattern; the down revision is `0008_1_17_offpc`.
  - `db/models/ops.py`: `CODE_LENGTH` (16), `REVISION_LENGTH`, `ERROR_LENGTH` and the CHECK constants. The model's CHECK text must equal the migration's character for character (`tests/contracts/test_job_contracts.schema_differences`).
  - `db/owners.py` `TABLES`, `db/pii_registry.py` `PII` and `db/repos/ops/__init__.py` (its docstring).
  - `db/repos/ops/backups.py`: `_COLUMNS` comes from `BackupRow`'s fields. Copy that shape for `RestoreTestRow`.
  - `db/repos/ops/snapshot.py`: `_first_line`, `INTEGRITY_ROWS` and the counts loop. `nextron/db/migrations.py`: `head_revision`, `known_revisions`, `stored_revision` and `DatabaseStateError`.
- **Ports and fakes.** `nextron/ports/backup_target.py` (the codes and `FetchedCopy`) and `nextron/ports/secrets.CREDENTIAL_NOT_SET`. `tests/fakes/backup_target.InMemoryBackupTarget` takes `failures["get"] = MemoryError()` and similar.
- **Jobs.** `nextron/jobs/handlers.py` (`backup_db` is the pattern; update the docstring) and `nextron/jobs/triggers.py` (`Trigger.RESTORE_TEST` already exists).
- **`nextron/main.py`:**
  - `build_job_types` (532), `build_triggers` (583), `build_offpc_backups` (608), `read_backup_schedule` (623) and `build_app` (422-517);
  - the docstring's Triggers and Backups paragraphs.
- **Web.**
  - `nextron/web/health_page.py`: `backup_text`, `backup_failed`, `panel_context` and `submitted_backup`, plus the `BACKUP_QUEUED` constants.
  - `web/templates/health_panel.html`.
  - `web/app.py`: `health_backup_now` (578), the `create_app` signature (270-300) and the module docstring (60).
- **Pins to update.**
  - **Migration head.**
    - `HEAD` in `tests/contracts/test_persistence_contracts.py:72`, `tests/unit/test_migrations.py:39` and `tests/app/test_serve_process.py:263`;
    - the chain probes at `test_persistence_contracts.py:670-682` (0009 → 0010, 0010 → 0011, "revision 10");
    - `tests/unit/test_serve.py:732,746` (`0009_more` → `0010_more`);
    - `tests/contracts/test_source_tree.py` `FILES`, with the new modules.
  - **Two daily triggers now log `daily_trigger_scheduled`:**
    - `tests/app/test_serve_process.py:238`;
    - `tests/unit/test_runner_lifespan.py:654,957,998`.

    Select the `backup_db` record by its `trigger` field.
  - **Callers of `build_job_types`, `build_triggers`, `create_app`, `HealthReport(...)` and `panel_context`:** `tests/dashboard.py`, `tests/contracts/test_job_contracts.py`, `tests/contracts/test_backup_contracts.py`, `tests/unit/test_runner_lifespan.py`, `tests/app/test_engine_health.py`, `tests/unit/test_health_page.py` and the other `tests/app/*` files the grep lists.
  - **Trigger and job-type lists**, if any test pins the registered names.
- **README** (`nextron-engine/README.md`): "Backups" (858) gains a "Restore test" part, and also update "Engine health" (1304), the migrations table and the jobs/triggers list.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/restore_test.py`: the constants, enums, `RestoreOutcome`, `fetch_failure`, `judge`, the schedule functions and the text.
- [x] `nextron-engine/nextron/db/alembic/versions/0009_1_18_restore_tests.py`, `db/models/ops.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/ops/restore_tests.py`, `db/repos/ops/backups.py`, `db/repos/ops/snapshot.py` and `db/repos/ops/__init__.py`: the table, the repository, `newest_offpc_copy` and `inspect_copy`.
- [x] `nextron-engine/nextron/app/queries/ops.py` and `app/ops/api.py`: the queries, the issues, `OffpcBackups.fetch`, `RestoreTests` and `HealthReport.restore_test`.
- [x] `nextron-engine/nextron/jobs/handlers.py` and `main.py`: the handler, the job type, the trigger, `build_restore_tests`, `read_restore_test_schedule` and the wiring.
- [x] `nextron-engine/nextron/web/health_page.py`, `web/templates/health_panel.html` and `web/app.py`: the section, the route and the notice.
- [x] `nextron-engine/tests/unit/test_restore_test_domain.py`: every `judge` branch, `fetch_failure`, the reason order, the detail cut, the schedule functions (month and timezone boundaries, the midnight wrap, `None`) and the text.
- [x] `nextron-engine/tests/app/test_restore_tests.py`, with real units:
  - every matrix row, using the real `FolderBackupTarget` on a temporary folder for the pass, wrong-passphrase, tampered and truncated rows, and the fake for scripted failures;
  - the live database's bytes and revision unchanged, and no work folder or `-wal`/`-shm` left;
  - spies proving no `ensure_current`, `command.upgrade` or `create_engine` call during `run`;
  - raw-SQL violations of `ck_restore_tests_outcome`, each refused by name, and valid rows;
  - `health_issues`;
  - `request_restore_test` absorption.
- [x] `nextron-engine/tests/unit/test_snapshot.py` (or the existing snapshot tests): `inspect_copy` on a good copy, a non-database file, a copy at an older revision and one with an absent table. The file's SHA-256 is unchanged afterwards.
- [x] `nextron-engine/tests/contracts/test_backup_contracts.py`: `restore_test` is bulk-only with group Backup, and its trigger is a `DailyAt` on its registered job type. Each check has a probe.
- [x] `nextron-engine/tests/unit/test_health_page.py`: each restore line, the failed class, the notice and the form.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py`, with the real `build_app`, `InMemorySecrets` holding a passphrase and `backup_offpc_dir` set to a temporary folder. POST `/health/backup`, then POST `/health/restore-test`. The result is a `pass` row, the panel reads "pass (manual)", and no work folder is left.
- [x] The pins in the Code Map, and `nextron-engine/README.md`.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q -n 6 --dist loadgroup` and `uv run lint-imports` run, then everything passes and all 29 contracts are kept.
- Given `tests/contracts/test_backup_contracts.py`, when it scans the new code, then the copy check passes unchanged.

## Implementation Notes

- **Where the code is.**
  - Domain: `nextron/domain/restore_test.py` (the constants, the five enums, `FETCH_FAILURES`,
    `RestoreOutcome`, `fetch_failure`, `judge`, `restore_test_time`, `restore_test_due`,
    `missed_restore_test`, `RestoreTestSchedule`, `restore_test_failed_text`).
  - Persistence: migration `0009_1_18_restore_tests` (down revision `0008_1_17_offpc`);
    `RestoreTestRecord` in `db/models/ops.py` (the nine named checks, `ix_restore_tests_started_at`;
    `backup_id` is a `Ulid` column, `String(26)`, no foreign key); `owners.py` (`ops`) and
    `pii_registry.py` (`detail` is `redact`, the rest `none`); `db/repos/ops/restore_tests.py`
    (`RestoreTestRow`, with a `checks` property, `insert`, `newest`, `failed_after`);
    `newest_offpc_copy` in `db/repos/ops/backups.py`; `CopyInspection` and `inspect_copy` in
    `db/repos/ops/snapshot.py` (its counts loop is now `_counts`, shared with `snapshot`).
  - Queries: `last_restore_test`, `newest_restore_test`, `newest_offpc_copy` and the restore
    issues appended by `health_issues` in `app/queries/ops.py`.
  - Service: `OffpcBackups.fetch`, `RestoreTests` (with `FETCH_REASONS`, the code-to-reason
    map) and `HealthReport.restore_test` in `app/ops/api.py`; the module docstring has the
    "Restore tests" paragraph.
  - Jobs and composition: `handlers.restore_test` (`RestoreTesting` protocol); in `main.py` the
    `restore_test` job type, `Trigger.RESTORE_TEST`, `build_restore_tests`,
    `read_restore_test_schedule`, and `build_app` now builds one `OffpcBackups` shared by
    `Backups` and `RestoreTests`.
  - Web: `restore_test_text`, `restore_test_failed`, `submitted_restore_test` and the
    `RESTORE_TEST_QUEUED` constants in `web/health_page.py` (`panel_context` gains
    `restore_notice`); the Restore test section in `health_panel.html`; `POST
    /health/restore-test` (`health_restore_test_now`) in `web/app.py` (`create_app` gains
    `restore_tests`); `.health-restore*` styles in `app.css`.
- **Small additions beyond the spec.**
  - `_check` also turns any other `Exception` from the fetch-and-judge step into `fetch_failed`
    (detail `error_code`), and an `inspect` that raises into an integrity failure
    (`integrity_check: error: <code>`), so it never raises.
  - A backup dir where `restore_test` can't be made (a file in its place) is `fetch_failed`
    with the error's code, and the work folder isn't made.
  - Cleanup deletes each file it can even after one fails, never follows a link or a junction
    when clearing leftovers, and logs one `restore_test_cleanup_failed` with the first code.
  - `restore_test_record_failed` also carries `kind`; `restore_test_not_due` carries `kind`.
  - `RestoreOutcome` refuses an inconsistent outcome (`ValueError`), and has `failing` and
    `check(name)`.
  - `inspect_copy` also reports a copy with several revisions as `None` (it is
    `DatabaseStateError` too) and a missing file as `error: OperationalError`.
- **Pins.** `HEAD` (three places), the chain probes (0010/0011, "revision 10"), `test_serve.py`
  (`0010_more`), `test_source_tree.py` `FILES`, the route list in `test_web_contracts.py`, the
  job-type and trigger lists (`test_job_contracts.py`, `test_backup_contracts.py`,
  `test_runner_lifespan.py`, `test_engine_health.py`), the two `daily_trigger_scheduled`
  records (`test_serve_process.py`, `test_runner_lifespan.py`, selected by `trigger`), and the
  panel's texts, tables, forms and buttons in `test_health_page.py`. Every `build_job_types`
  caller passes `restore_tests=` (a `no_restore_tests` helper where none runs).
- **Test notes.** The real-adapter tests use `ScryptParams(10, 8, 1)` (the real-app test passes
  `backup_targets` with them to `build_app`). The corrupted copy is made by changing one of the
  two stored copies of a marker value (row or index entry), which `integrity_check` reports as
  `row 1 missing from index ...`; SQLite 3.53.1's `integrity_check` doesn't report rows that
  break a CHECK, so that route wasn't used.

**Verification** (through the suite lock): `uv run pytest -q -n 6 --dist loadgroup` gave
4634 passed, 2 skipped, and `uv run lint-imports` 29 kept, 0 broken.

Not verified here: a real network share or second drive, a real passphrase in Credential
Manager, and a production-size copy (only temporary local folders and a test database).

Review fixes (pass 1, P1–P5):
- **P1.** `test_a_leftover_that_is_a_junction_is_never_followed` (`test_restore_tests.py`): a
  `nextron-test-linked` junction in `restore_test\` (made with `_winapi.CreateJunction`,
  skipped where it can't be made) points at a folder holding a marker file; a passing test
  leaves the marker and the junction. Removing `not _not_followed(leftover)` fails it.
- **P2.** `test_two_threads_never_test_at_once`: two threads each run a manual test through an
  `inspect` that counts concurrent entries (0.05 s sleep); at most one is ever inside, both rows
  `pass`, and no `restore_test_cleanup_failed`.
- **P3.** `RestoreTests._sweep` (the leftover sweep, now shared with `_work_folder`) also runs at
  the start of every `run`, under the lock and before the due check, when `restore_test\`
  exists; it never raises (failures are `restore_test_cleanup_failed`). So a daily check that
  isn't due, and a `no_offpc_copy` or `offpc_not_configured` run, clear a leftover too. Test:
  `test_every_run_clears_leftover_work_folders_first` (a not-due monthly tick, a no-copy run).
  The module docstring's step 3 and the README say so.
- **P4.** `OLD_REVISION` in `test_snapshot.py` and `test_restore_tests.py` is
  `max(known_revisions() - {head})`, not the literal `0008_1_17_offpc`, so a renumbered chain at
  merge still works.
- **P5.** `test_the_scheduler_reads_the_stored_time_zone_and_a_missed_daily_backup`
  (`test_runner_lifespan.py`) also asserts the `restore_test` `daily_trigger_scheduled` record:
  `08:30`, `America/New_York`, no catch-up, so `build_app` wiring the wrong schedule fails.

**Final verification** (after pass 1, through the shared suite lock): `uv run pytest -q -n 6 --dist loadgroup`
gave 4638 passed, 2 skipped (`ai_eval` and the symlink test), and `uv run lint-imports` 29 kept, 0 broken.
The first post-patch run failed only on the known flaky
`test_job_runner.py::test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper[None-1]`
(4637 passed); the re-run passed.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (12 findings), EC = edge-case hunter (10), VG = verification gap (2 gaps, no other findings).
- **Counts:** 24 raw findings. BH8 makes two claims, so there are 25 claims, which make 21 entries after deduplication.
- **Outcomes:**
  - 5 entries patched (P1–P5): 1 code fix and 4 test gaps;
  - 0 deferred;
  - 16 rejected.
- **No loopback.** No finding is an intent gap or a bad spec.
- **Rejected only because the fix would change the approved spec:**
  - L (decision 1);
  - M (`restore_test_due` as specified, with decisions 7 and 9).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | VG1, BH8 (part) | Nothing tests that the leftover sweep never follows a link or a junction. Removing `and not _not_followed(leftover)` passes every test, and VG's probe showed that a `nextron-test-*` junction makes `_remove` delete the files in its target. | medium | Pre-verified by VG: the probe was a junction to a folder holding `nextron.db`, which got deleted. The README and the module docstring both promise "never through a link or a junction". The fix is a direct test addition: a junction made with `_winapi.CreateJunction` (skip where unavailable) to a folder with a marker file; after `run()` the marker remains and the test passes. | P1 |
| B | VG2, BH8 (part) | Nothing tests that `RestoreTests`' lock keeps two tests apart. The tick (`…:tick`) and a manual job (`…:<date>`) have different keys, so with two bulk threads the lock is the only guard. Without it, the second test's sweep would delete the first's live work folder. | medium | Pre-verified by VG: removing `with self._lock:` fails no test. The sibling `test_two_threads_never_copy_at_once` (`test_backups.py:1054`) pins the backup lock. The fix is a direct test addition mirroring it: a watched `inspect` counting concurrent entries, and two threads running `run(MANUAL)`. Assert at most one is ever inside, two `pass` rows, and no `restore_test_cleanup_failed`. | P2 |
| C | BH1, EC4 | A work folder whose delete failed keeps its plaintext copy until a later test reaches `_work_folder`. Daily not-due ticks return before the sweep, and `no_offpc_copy` and `offpc_not_configured` return before it too. So the copy (personal data, erased rows included) can outlive AD-17's 35 days. | medium | Verified: `run` returns `None` before `_check` when not due, and `_check` returns before `_work_folder` for those reasons. The sweep is the only cleanup of `restore_test\`, and local rotation doesn't cover it. The smallest fix keeps the spec's step-3 sweep and also runs the same sweep at the start of every `run()`, under the lock and before the due check, so each daily check clears leftovers. BH1's "raise an issue for a leftover" would be new surface, so it is not part of the fix. | P3 |
| D | BH5 | The tests hard-code `OLD_REVISION = "0008_1_17_offpc"` (`test_snapshot.py:21` and `test_restore_tests.py:73`) and expect it to be a known revision. At merge, `main`'s Story 2.1 holds `0008`, so 1.17 is renumbered and these two tests fail. | medium | Verified: the `revision_behind` cases depend on `known_revisions()` containing that literal. The fix is a direct test correction: take the older revision from the chain, `max(known_revisions() - {head_revision()})` (the ids are zero-padded). | P4 |
| E | BH9 | No test checks that `build_app` wires the restore-test schedule to the trigger. `test_runner_lifespan`'s scheduler test now filters to `backup_db`, so a swapped `functools.partial` would pass. | low | Verified: `read_restore_test_schedule` is unit-tested, and the contract test checks only the fallback. The fix is a direct test addition in the same lifespan test: with `backup_time` 07:30 and `America/New_York` stored, the `restore_test` `daily_trigger_scheduled` record has `at` 08:30 in that zone. | P5 |
| F | BH2, EC6 | `restore_tests.reason` has no `IN` check. `_restore_issues` parses it with `RestoreFailure(...)`, so a bad value would make the whole `backups` issue source fail. | low | True in principle. Only `RestoreTests._insert` writes the column, and it always writes a `RestoreFailure` value or `NULL`, so everyday use never meets it. The fix adds a guard (a new constraint). | reject |
| G | BH3 | A "Run restore test now" click during the monthly test isn't absorbed (different keys), and the second job waits on the lock while holding a bulk thread. | low | True, and the same pattern as `backup_db`'s tick and manual keys (Story 1.16, decision 9). The wait is the length of one test (seconds on this database). The fix needs a concurrency key or a shared key, which is new design. | reject |
| H | EC9 | A click just after UTC midnight while yesterday's manual job is still queued makes a second job. | low | True. It is the same discriminator rule as "Back up now" (Story 1.16, decision 9), and the result is a harmless second test. | reject |
| I | EC5 | While the second job waits on the lock, its lease runs out and the keeper requeues it, so the test runs twice. | false | `JobQueue.keep(held)` renews every held claim's lease on each keeper tick (`app/jobqueue/api.py:424-439`), so a held job is never reaped. Only a run past `max_runtime` (30 min) withholds the heartbeat, which would need two tests of more than 15 minutes each. | reject |
| J | BH4 | `head_revision()` or `known_revisions()` raising inside `inspect_copy` is recorded as `integrity_failed`, which blames the copy. | low | True as written, but unreachable in practice: `serve` reads the same script directory (`ensure_current`) before the engine can run any job. The fix adds a branch and a reason. | reject |
| K | BH6 | The panel and the alert never say which backup was tested or how old it is, and the detail falls back to repeating the reason. | low | The AC's "date, result and failing checks" is met, and `backup_id` is recorded. A newer off-PC copy that failed or wasn't configured raises its own alert and panel issue. Showing the backup's age needs a new query and field. | reject |
| L | BH7 | "Last successful runs" shows a fresh `restore_test` success every day, including the not-due check and failed tests. | low | Specified by decision 1: the daily check is a run of the job, and the Restore test section shows the last real test. | reject |
| M | BH10 | One transient failure (drive unplugged at the slot) uses up the month's test, and nothing retries it automatically. | medium | True, and specified: `restore_test_due` counts the newest monthly test "of either result" (the frozen block, with decisions 7 and 9). The failure raises a Telegram alert and a panel issue, and "Run restore test now" retries. | reject |
| N | BH11 | `submitted_restore_test` duplicates `submitted_backup`, and `restore_test._check_aware` duplicates `backup._check_aware`. | low | True and cosmetic. Each is a few lines with a named purpose, and nothing diverges. | reject |
| O | BH12 | The new `record_restore_test(...)` calls in `test_health_page.py` use hanging indents. | low | Cosmetic. Ruff isn't part of the suite, and the tests read clearly. | reject |
| Q | EC1 | A stored `sender_timezone` that no longer parses (or a zone missing from tzdata) makes `run()` raise in step 1, so the job dies daily with no row. | low | True, but it needs a stored value invalidated after saving. The dead job raises the `job_dead` alert (Telegram and the dashboard), so it isn't silent. `Backups` and the senders read the same setting the same way. | reject |
| R | EC2 | If `<backup dir>\restore_test` is itself a junction, `mkdir(exist_ok=True)` follows it, and the plaintext lands where it points. | low | Only Mac could make that junction, inside his own backup dir. The fix adds a guard. | reject |
| S | EC3 | A local write failure (disk full on the backup drive) inside `get` is the adapter's `backup_target_unavailable`, so the reason blames the off-PC drive. | low | True: Story 1.17's adapter maps every `OSError` on `get`'s target to that code. It is rare, and the fix needs a new adapter error code (public surface). | reject |
| T | EC7 | A fail and a pass with the same `started_at`, the fail with the greater id, hide the fail from the issues (`started_at > since`). | low | Tests run one at a time under the lock, and each reads `started_at` after the previous one finished, so the values only tie if the wall clock steps back. `backups.failed_after` uses the same rule. | reject |
| U | EC8 | `integrity_check` returns `ok`, but reading `alembic_version` raises `sqlite3.DatabaseError`, which is recorded as `integrity_failed` rather than an unreadable revision. | low | A copy that passed `integrity_check` and then fails a one-row read is very unlikely, and the outcome is still `fail` with an alert. The fix adds a branch. | reject |
| V | EC10 | The contract test's `DailyAt` assertion has no probe. | low | That assertion is a direct `isinstance` on the real registry, as in Story 1.16, not a scanning check function. The probes cover the check functions (`backup_job_problems`) and the registration order. | reject |

## Design Notes

```python
# RestoreTests.run(kind), on a bulk-lane thread, inside self._lock
with uow.transaction():                      # read: due?, newest copied row, destination
    ...
outcome = self._check(row, folder)           # fetch into <backup dir>\restore_test\nextron-<env>-*,
                                             # inspect via open_copy, judge; finally delete folder
with uow.transaction():                      # record once; alert on fail
    test_id = restore_tests_repo.insert(...)
```

The domain decides pass and fail (`judge`), the db layer only reads the copy (`inspect_copy`), and `ops` moves bytes and records. Story 1.19 can reuse `inspect_copy` to check a restore candidate.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q -n 6 --dist loadgroup` -- expected: all pass (`ai_eval` and the symlink test skipped). Run the full suite only through the suite lock.
- `uv run lint-imports` -- expected: 29 contracts KEPT.
