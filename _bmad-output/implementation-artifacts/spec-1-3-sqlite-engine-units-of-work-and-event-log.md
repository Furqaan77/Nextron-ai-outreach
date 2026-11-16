---
title: 'Story 1.3: Persist through one configured SQLite engine, units of work and an append-only event log'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '5adc1aa8420a956dc77ccc79fdcd77aa40751934'
review_loop_iteration: 0
story_key: '1-3-persist-through-one-configured-sqlite-engine-units-of-work-a'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has no database. Every later story adds tables. Without one configured SQLite engine, one unit of work per entry point, an executable owner and personal-data map and one Alembic chain, stories would open their own connections, nest or split transactions, write each other's tables, put personal data into the audit trail or fork the schema (AD-2, AD-3, AD-4, AD-17, SC-3, SC-4, SC-8).

**Approach:** Build the persistence foundation under `nextron/db/`:
- `engine.py`, the only connection configurator;
- `uow.py`, with the context-local unit of work, the `@use_case` and `@command` markers and the closed notification bus;
- `eventlog.py` and the append-only `events` table;
- the model base and column types;
- the column-level `owners.py` and a new `pii_registry.py`;
- an Alembic script directory with migration `0001_1_3_persistence_foundation`, and a `serve` startup that refuses a DB ahead of the code and upgrades one behind it.

The 10 AD-3 notifications are pure payload types in `nextron/domain/notifications.py`, and `main.py` holds the subscriber registry (empty and frozen for now). Contract tests prove each rule. The acceptance criteria are Story 1.3 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" below says otherwise.

## Boundaries & Constraints

**Always:**
- **Engine (`nextron/db/engine.py`).**
  - `create_engine(db_path: Path)` handles file DBs only and passes `autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL` explicitly.
  - Its `connect` event sets `isolation_level = None`, then runs `journal_mode=WAL` (failing unless the result is `wal`), `busy_timeout=5000`, `synchronous=NORMAL` and `foreign_keys=ON`.
  - Its `begin` event emits `BEGIN IMMEDIATE` for every transaction, reads included.
  - Any other transaction-control SQL issued through SQLAlchemy raises: `BEGIN`, `COMMIT`, `END`, `ROLLBACK`, `SAVEPOINT` or `RELEASE`, after any leading whitespace and comments.
- **One configurator.** No other module under `nextron/` creates an SQLAlchemy engine, through `create_engine`, `engine_from_config`, `create_async_engine`, `async_engine_from_config`, `create_mock_engine` or `create_pool_from_url`. No other module calls `sqlite3.connect` under any alias. Alembic's `env.py` uses the connection that `nextron.db.migrations` hands it, and raises without one.
- **Unit of work (`nextron/db/uow.py`).**
  - **Opening.** `UnitOfWorkFactory(engine, subscribers).open()` is the entry points' context manager. It binds the UoW to a `ContextVar` and to the opening thread. A second `open()` in the same context raises. `current()` raises outside a UoW and from another thread.
  - **`@use_case`** marks the one use-case method an entry point calls. A second use-case call in the same UoW raises, and so does a use-case call inside a use case or a command.
  - **`uow.transaction()`.** Only the running use case may open it, and never from inside a command; nesting raises. It begins eagerly (`BEGIN IMMEDIATE`), commits on a clean exit and rolls back on any exception.
  - **`@command`** marks owner commands; every subscriber is one. A command runs only inside an open transaction, which it joins, and it can't open one. Any exception leaving a command dooms the transaction: the block rolls back at exit even when the use case caught the exception, and then raises `UnitOfWorkError`.
  - **`uow.session`** exists only inside a transaction. It is a guarded ORM `Session` proxy: `begin`, `begin_nested`, `commit`, `rollback`, `close`, `connection`, `get_bind`, `invalidate` and `reset` raise `UnitOfWorkError`. Session events also refuse a commit the UoW didn't start, and doom the transaction when it ends outside the UoW (for example through `object_session(obj)`).
- **Notifications.**
  - The 10 types are frozen dataclasses in `nextron/domain/notifications.py`, listed in AD-3 order in `NOTIFICATIONS`.
  - Fields may hold only canonical 26-character ULID strings (fields named `*_id`), `int`, `bool`, `None`, or lowercase snake_case codes of at most 64 characters. Anything else raises at construction.
  - `uow.publish(n)` requires an open transaction and `type(n)` exactly in `NOTIFICATIONS`; otherwise it raises. It then runs that type's subscribers synchronously, in registration order, in the same transaction.
- **Subscribers.** `Subscribers.register_subscriber(type, command)` accepts only listed types and `@command` callables. `freeze()` ends registration, and `UnitOfWorkFactory` requires a frozen registry. Only `nextron/main.py` (`build_subscribers()`) calls `register_subscriber` or constructs `Subscribers`.
- **Event log.** `db.eventlog.append(*, entity, entity_id, from_status, to_status, cause, cause_ref, actor, at) -> str` writes through the current transaction's session and returns the new ULID. It checks every argument before writing and raises `ValueError` on any mismatch:
  - `entity`: a table in `owners.TABLES`;
  - `entity_id`: a ULID; `cause_ref`: a ULID or `None`;
  - `to_status`, `cause` and `from_status` (`None` for a creation): lowercase snake_case, at most 64 characters;
  - `actor`: fullmatches `operator`, `sender`, `seed`, `system:<an owner in OWNERS>` or `ai:<route>@<version>`, where the route matches `[a-z][a-z0-9_]{0,63}` and the version `[A-Za-z0-9][A-Za-z0-9._-]{0,31}`;
  - `at`: an aware datetime.
- **`events` table.** Columns `id`, `entity`, `entity_id`, `from_status`, `to_status`, `cause`, `cause_ref`, `actor` and `at`, with indexes on `(entity, entity_id)` and on `at`. SQLite triggers abort `UPDATE`, `DELETE`, and an `INSERT` that reuses an existing `id` (this stops `INSERT OR REPLACE`). Every column is classified `none`.
- **Model base.** `Base` has a constraint naming convention.
  - Each table's primary key is exactly one `id` column of type `Ulid`. `Ulid` stores python-ulid's canonical text, accepts a `ULID` or a `str`, normalises case and rejects anything else.
  - Natural keys are unique columns.
  - Datetimes use `UtcDateTime`. It rejects naive and non-datetime values, stores UTC as fixed-width `YYYY-MM-DDTHH:MM:SS.ffffffZ` and returns an aware UTC datetime. No column uses SQLAlchemy `DateTime`.
- **Maps.** `owners.TABLES` becomes `table -> {writer: (columns...)}`. Writers come from `OWNERS` or from `SHARED_WRITERS = ("db.eventlog",)`, and `events` belongs to `db.eventlog`. `pii_registry.PII` maps `table -> {column: PiiClass}`, where the class is `none`, `redact`, `null` or `keep_hash`. Both maps must match `Base.metadata` exactly: every column mapped to exactly one writer and classified exactly once, with no stale entries and every `events` column `none`.
- **Alembic.**
  - The script directory is `nextron/db/alembic/` (`env.py` and `versions/`), with `render_as_batch=True` and `target_metadata=Base.metadata`.
  - It is one linear chain. Each revision id equals its file name stem, fullmatches `^\d{4}_[a-z0-9_]+$` and has at most 31 characters, and its `NNNN` equals its 1-based position. No branches, labels or dependencies.
  - Migrations are forward-only: `downgrade()` raises `NotImplementedError`.
- **Migration runner (`nextron/db/migrations.py`).** `ensure_current(engine)` reads the DB's revision:
  - unknown to this code: raise `DatabaseAheadError` ("database revision <rev> is newer than this code");
  - several revisions, or tables but no revision: refuse with a named error;
  - behind, or a new file: run `alembic upgrade head` in one transaction. The connection has `foreign_keys` off for the duration, and `PRAGMA foreign_key_check` must return nothing before commit. `foreign_keys` is back on before the connection returns to the pool; if that fails, the connection is invalidated.

  The PRAGMA toggling lives in `engine.py`.
- **`serve`** opens `<data dir>\nextron.db` after the port check and before `engine_started`:
  - a DB that is ahead or refused: a message on stderr, `startup_check_failed` (`check=database_revision`), exit 2, nothing changed;
  - a failed migration: `database_migration_failed` with the traceback, exit 2;
  - an upgrade: `database_migrated` (`from_revision`, `to_revision`).

  `engine_started` gains `db_revision`. The engine is disposed when `serve` returns.
- **Import rule.** A new contract, `c1-app-db-surface`, stops `nextron.app` from importing `nextron.db.engine`, `.migrations`, `.models`, `.owners` and `.pii_registry` (indirect imports allowed). The app reaches the DB only through `uow`, `eventlog` and its own repositories.

**Never:**
- No domain tables, repositories or owner commands. No subscriber registrations: the registry ships empty.
- None of the work owned by later stories: the `pre_migrate` backup (1.16), a `doctor` DB check (1.14), the `settings` table (1.6), or wiring the UoW factory into web handlers or jobs.
- No `aiosqlite`, no async engine, no `ON DELETE CASCADE`, and no copying of the DB file.
- No `alembic.ini`, no Alembic CLI path and no `script.py.mako`.
- Don't modify anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

### Decisions made overnight (pending user review)

1. **Revision ids use underscores.** Alembic 1.20.1 rejects `-`, `@`, `+` and `:` in revision ids, so `0001_1-3-persistence-foundation` can't load. Revision ids replace the story key's hyphens with underscores: this story's migration is `0001_1_3_persistence_foundation`, and the contract regex is `^\d{4}_[a-z0-9_]+$` instead of `^\d{4}_[a-z0-9-]+$`. The rejected options were a slug with no separators (it meets the literal regex but is unreadable) and patching Alembic (it breaks Alembic's relative-revision syntax).
2. **Migrations are forward-only.** `downgrade()` raises. A downgrade could drop the append-only `events` audit trail. Recovery from a bad upgrade is restoring the `pre_migrate` backup (AD-2, AD-20).
3. **Every `ConnectionSettingError` counts as a refused database.** `serve` reports it as `startup check failed: database_revision`, exit 2. The intended case is a non-empty file this engine didn't create, which isn't in WAL mode. Two rare failures land in the same bucket: an empty file that won't switch to WAL, and a failed `foreign_keys` toggle. A subclass that reports them as `database_migration_failed` was rejected as unneeded: the exit code is 2 either way, and the message names the actual setting.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Connection settings | any new pooled connection | WAL, 5000, NORMAL (1), FK on, `isolation_level None`; `begin()` emits `BEGIN IMMEDIATE` and holds the write lock | N/A |
| Raw transaction SQL | `uow.session.execute(text("COMMIT"))`, `"/* x */ rollback"`, `SAVEPOINT a` | nothing committed or rolled back by it | raises |
| Second UoW | `open()` inside `open()` in one context | | raises `UnitOfWorkError` |
| Two threads | each thread opens a UoW at once | independent UoWs, both commit | N/A |
| Command misuse | a `@command` calls `transaction()` or `session.commit()/rollback()/begin()` | | raises; transaction doomed |
| Invariant violation | a command raises a `DomainError` after the use case wrote rows | the whole transaction rolled back | the error propagates |
| Swallowed failure | the use case catches a command's error and exits the block normally | rolled back | the block raises `UnitOfWorkError` |
| Prepare, call, apply | two sequential `transaction()` blocks in one use case | each commits; `uow.session` between them raises | N/A |
| Command outside a transaction | a `@command` is called when no transaction is open | | raises |
| Publish | a listed type with two subscribers | both run in order, in the same transaction; if the second fails, nothing commits | the failure propagates |
| Bad publish | an unlisted type, a subclass of a listed type, or no open transaction | | raises |
| Bad registration | a type not listed, a callable that isn't a `@command`, or a call after `freeze()` | | raises |
| Event row | valid `append` | one row; the ULID is returned | N/A |
| Bad actor | `system:cron`, `Operator`, `ai:x`, `ai:r@`, `a@b.co` | nothing written | `ValueError` |
| Append-only | `UPDATE`, `DELETE`, `INSERT OR REPLACE` with an existing `id` | the row is unchanged | aborted by the trigger |
| Datetimes | naive; aware non-UTC; a `date` | rejected; stored as UTC `…Z`; rejected | `ValueError` or `TypeError` |
| Coverage | a dummy column added to a copy of the metadata | the coverage check names it as unmapped and unclassified | test failure |
| Fresh data dir | `serve`, no `nextron.db` | DB created and at head before `run_server` | N/A |
| DB behind | `serve` against a DB at an older revision (unit tests use a temporary two-revision chain) | upgraded before `run_server`; `database_migrated` logged | N/A |
| DB ahead | `alembic_version` = `0099_future` | stderr shows "database revision 0099_future is newer than this code"; the DB file's SHA-256 is unchanged | exit 2 |
| Foreign DB | tables but no `alembic_version`, or two revision rows | file unchanged | exit 2, named message |
| Migration fails | a migration raises, or `foreign_key_check` finds rows | rolled back; revision unchanged | exit 2, `database_migration_failed` |
| Lock held / port taken | second instance; port busy | the DB is never opened | exit 3 / exit 2 (unchanged) |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/main.py` -- `serve()` steps: env → checks → mkdir → lock → logging → port → **(new) database** → `engine_started` → Uvicorn. Add `DB_FILE_NAME = "nextron.db"`, `build_subscribers()` and `open_database(config) -> UnitOfWorkFactory`. Keep the `ServerRunner` signature, `_report_failures` and the exit-code constants. Update the module docstring's step list.
- `nextron-engine/nextron/db/owners.py` -- `OWNERS` must stay a tuple of the 16 names (`scripts/gen_owner_contracts.py` and `tests/contracts/test_source_tree.py` read it). Reshape `TABLES` and add `SHARED_WRITERS`, then update the docstring.
- `nextron-engine/nextron/db/__init__.py`, `nextron-engine/nextron/app/__init__.py` -- docstrings: the import rule (add alembic) and the `@use_case`/`@command` convention.
- `nextron-engine/pyproject.toml` -- add `c1-app-db-surface` after `c1-composition-root-helpers`. `c4-sqlalchemy-only-in-db` already lets `nextron.db` import sqlalchemy and alembic, and lets `main` reach them indirectly; `main` must import no sqlalchemy name directly, except under `TYPE_CHECKING`.
- `nextron-engine/tests/contracts/support.py` -- `copy_with_probe()` / `lint_copy_with_probe()`. Use them for every live proof; never write probes into the real tree.
- `nextron-engine/tests/contracts/test_error_contracts.py` -- the precedent for AST contract checks with a live proof on a tmp copy.
- `nextron-engine/tests/contracts/test_import_contracts.py` -- asserts every contract is KEPT and all seven groups exist; the new contract joins group 1.
- `nextron-engine/tests/contracts/test_source_tree.py` -- the `FILES` list; add the new modules.
- `nextron-engine/tests/unit/test_serve.py` -- `ServerSpy`, the `dev` fixture and `fake_probes()`; extend the spy to record the DB revision at server start.
- `nextron-engine/tests/app/test_serve_process.py` -- the `running_engine` fixture; assert that `nextron.db` exists at head.
- `nextron-engine/tests/conftest.py` -- add `db_engine` (temp file, migrated, disposed) and `units` (empty frozen `Subscribers`) fixtures.
- `nextron-engine/nextron/domain/errors.py` -- `DomainError` (tests define a subclass with a unique code for the invariant case).
- `nextron-engine/nextron/startup_checks.py` -- imports `sqlite3` for `sqlite_version` only; the new AST check must still pass it.
- `nextron-engine/tests/contracts/test_log_event_names.py` -- new event names must stay under 32 characters.
- `nextron-engine/.venv/Lib/site-packages/sqlalchemy/dialects/sqlite/base.py` (lines ~240–280) -- the documented `isolation_level=None` plus `begin` recipe.
- `nextron-engine/.venv/Lib/site-packages/alembic/script/revision.py:43` -- the illegal revision characters.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/ids.py` -- `ULID_PATTERN`, `is_ulid()` (canonical uppercase Crockford, first char 0–7) -- stdlib ULID shape for the domain.
- [x] `nextron-engine/nextron/domain/notifications.py` -- `Notification` base with field validation; the 10 types (see Design Notes); `NOTIFICATIONS` in AD-3 order -- the closed list.
- [x] `nextron-engine/nextron/db/engine.py` -- `create_engine`, connect/begin events, transaction-SQL guard, `fk_off_connection(engine)` context manager -- the one configurator.
- [x] `nextron-engine/nextron/db/models/__init__.py`, `base.py`, `events.py` -- `Base`, `Ulid`, `UtcDateTime`, `new_id()`, `ulid_pk()`; the `EventRecord` model (table `events`); `__init__` imports every model module -- complete metadata.
- [x] `nextron-engine/nextron/db/owners.py`, `nextron-engine/nextron/db/pii_registry.py` -- the column maps for `events` -- SC-4.
- [x] `nextron-engine/nextron/db/uow.py` -- `UnitOfWorkError`, `UnitOfWorkFactory`, `UnitOfWork` (`transaction`, `session`, `publish`), `current`, `use_case`, `command`, `Subscribers` -- AD-3.
- [x] `nextron-engine/nextron/db/eventlog.py` -- `append` -- AD-4.
- [x] `nextron-engine/nextron/db/alembic/env.py`, `nextron-engine/nextron/db/alembic/versions/0001_1_3_persistence_foundation.py` -- the connection-only env, and the migration creating `events`, its indexes and its three triggers. No `__init__.py` in the script directory.
- [x] `nextron-engine/nextron/db/migrations.py` -- `alembic_config()` (programmatic, with `path_separator` set; no Alembic warnings), `head_revision()`, `current_revision()`, `ensure_current(engine, *, script_location=...)` returning `(before, after)`, `DatabaseAheadError`, `DatabaseStateError` -- the startup migration.
- [x] `nextron-engine/nextron/main.py` -- the database step in `serve`, `build_subscribers()`, `open_database()`, disposal, log events -- composition.
- [x] `nextron-engine/pyproject.toml` -- `c1-app-db-surface` -- the layer table.
- [x] `nextron-engine/tests/unit/test_db_engine.py` -- the six settings, the write lock held by an idle `begin()`, the SQL guard, FK-off restore and invalidation.
- [x] `nextron-engine/tests/unit/test_uow.py` -- every UoW, publish and registry row of the matrix, with a scratch table created by raw DDL in the temp DB.
- [x] `nextron-engine/tests/unit/test_eventlog.py`, `tests/unit/test_model_types.py`, `tests/unit/test_notifications.py` -- the append and actor rows, the triggers, `Ulid` and `UtcDateTime`, payload validation.
- [x] `nextron-engine/tests/unit/test_migrations.py` -- fresh, behind (temporary two-revision script dir), ahead, foreign, several revisions, a failing migration, an FK-check failure, file hash unchanged on refusal.
- [x] `nextron-engine/tests/unit/test_serve.py`, `tests/app/test_serve_process.py` -- `serve` rows of the matrix.
- [x] `nextron-engine/tests/contracts/test_persistence_contracts.py` -- these checks, each with a live proof:
  - the AST check that only `engine.py` creates engines or calls `sqlite3.connect`;
  - the AST check that only `main.py` registers subscribers or constructs `Subscribers`;
  - owner and PII coverage (the dummy-column proof), and `events` all `none`;
  - the metadata rules (ULID `id` primary keys, no `DateTime`);
  - Alembic: a single head, the id rules and linear order, forward-only downgrades, and `render_as_batch=True` in `env.py`;
  - model–migration drift (`compare_metadata` at head is empty);
  - the `events` triggers present at head.
- [x] `nextron-engine/tests/contracts/test_source_tree.py` -- the new files.
- [x] `nextron-engine/README.md` -- a "Database" section: file location, startup migration and refusal, forward-only migrations, how to add one (hand-written `versions/NNNN_<story_key>.py`; the drift test checks it), and a UoW usage example.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run, then everything passes and every contract is kept, including `c1-app-db-surface`.
- Given `NEXTRON_ENV=dev` and no DB, when `serve` starts and `/health` is requested, then `%LOCALAPPDATA%\Nextron\dev\nextron.db` exists at revision `0001_1_3_persistence_foundation`, and `nextron.log` holds `database_migrated` and `engine_started` with `db_revision`.

## Implementation Notes

- **Engine.** `create_engine` also passes `hide_parameters=True`, so bound values (which can hold personal data) never appear in SQLAlchemy error messages or logs. The transaction-SQL guard uses a linear keyword scanner (`leading_keyword`), not a regex, to avoid catastrophic backtracking on long whitespace; the `begin` event's own `BEGIN IMMEDIATE` is let through by a `Connection.info` flag.
- **Unit of work.** The session joins the UoW's connection with `join_transaction_mode="rollback_only"`; the UoW flushes and commits the connection itself. Besides the session events, the UoW's connection gets `commit` (refused) and `rollback` (dooms) listeners, which close the `object_session(obj).connection().commit()` path. Every refused misuse inside an open transaction (a guarded session method, `transaction()` from a command, a use case inside a command, a bad publish) dooms the transaction even if the caller catches the `UnitOfWorkError`. A refused connection-level commit leaves SQLAlchemy's connection needing a rollback, so later statements in that block fail with `PendingRollbackError`; nothing commits either way. `TransactionSqlError` from the engine guard does not doom (nothing ran). `@use_case` and `@command` refuse async and generator functions. Every refused `uow.session` name, `bind` included, raises as soon as it is read.
- **Errors.** `DatabaseAheadError` subclasses `DatabaseStateError`; `serve` maps both to `startup_check_failed` (`check=database_revision`) and prints `startup check failed: database_revision: <message>`. A foreign-key-check failure raises `ForeignKeyCheckError` and maps, like any other migration failure, to `database_migration_failed`. A non-empty file not in WAL mode raises `ConnectionSettingError` at connect; `serve` maps every `ConnectionSettingError` to `startup_check_failed` (`check=database_revision`), exit 2 (see overnight decision 3). Any other failure to open the database or migrate it is `database_migration_failed`, with the stderr message "database startup failed: it could not be opened, or a migration failed and was rolled back; ...". `open_database` returns `(UnitOfWorkFactory, revision)`, so `serve` reads the revision once. Registration errors are `TypeError` (bad type or callable) or `UnitOfWorkError` (frozen); publish errors are `UnitOfWorkError`; a notification's required field refuses `None` (`TypeError`).
- **"File unchanged" on refusal.** The `connect` event reads `journal_mode` first and switches only an empty file to WAL; a non-empty file in another journal mode is refused untouched. The refusal tests check the SHA-256 for WAL and rollback-journal files alike. The transaction-SQL guard skips leading `;` (empty statements) as well as whitespace and comments.
- **Logs.** Alembic's own INFO lines (`Context impl SQLiteImpl.`, `Running upgrade ...`) reach `nextron.log` on a migrating start; two Story 1.2 tests in `tests/unit/test_logconfig.py` now filter to `nextron.main` records.
- **Extra contract checks** beyond the list: no `ON DELETE CASCADE`, `nextron/db/models/__init__.py` imports every model module, `env.py` configures the handed-over connection, and the script directory holds no `alembic.ini`, `script.py.mako` or `__init__.py`.
- **Test helpers.** `tests/migration_chains.py` builds temporary Alembic chains (the real `env.py` plus hand-written revisions) for the behind and failing-migration cases; `serve` tests point `nextron.db.migrations.SCRIPT_LOCATION` at one.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).** Sources: BH = blind hunter, EC = edge-case hunter, VG = verification gap. Grouped as patches P1–P13. No intent_gap or bad_spec entries, so there is no loopback.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH, EC | A leading `;` (`"; COMMIT"`, `"/* x */ ; ROLLBACK"`) gets past the transaction-SQL guard and commits mid-transaction | medium | Probed: `leading_keyword("; COMMIT")` returns `""`; the COMMIT runs, and the row survives a later rollback. | P1 |
| 2 | BH | DDL sent through `uow.session` (`DROP TRIGGER`) can switch off the append-only triggers at runtime | low | Real, but it needs deliberate DDL in engine code; refusing DDL outside migrations is a new guard. | reject |
| 3 | BH | A PRAGMA sent through a unit of work (`busy_timeout=0`) stays on the pooled connection | low | Real, but no code sends PRAGMAs; re-asserting the settings on checkout is a new hook. | reject |
| 4 | BH, EC | `uow.session.bind` passes through and hands out the raw `Connection`, while `connection` and `get_bind` are refused | low | `GuardedSession.__getattr__` refuses only the listed names; adding `bind` is a direct correction. | P2 |
| 5 | BH, EC, VG | Every non-`DatabaseStateError` failure of the database step (a non-SQLite file, a lock timeout, a WAL failure) is reported as "migration failed and was rolled back" | medium | `main.py` `except Exception` prints that text whatever failed; the reviewer checked a non-SQLite file (`DatabaseError: file is not a database`). | P3 |
| 6 | BH, EC | `serve` reads the revision a second time (`current_revision`), outside the handlers that log `engine_crashed` and `engine_stopped` | low | `open_database` discards `after`; a failure of the second read escapes with no `engine_stopped`. | P4 |
| 7 | BH | `eventlog.append` accepts `entity="events"` | low | Meaningless but harmless; excluding shared-writer tables is a new guard. | reject |
| 8 | BH | `Ulid` accepts lowercase, but `append` and notifications reject it | low | The domain requires canonical text by design (`ids.py`), and a lowercase id fails loudly with `ValueError`. | reject |
| 9 | BH | `events` has no CHECK constraints for writers outside the engine | low | Only outside tools could write malformed rows; the CHECKs would add DDL and drift handling. | reject |
| 10 | BH | The docs say revision ids are `NNNN_<story key>`, capped at 31 characters, but story keys are about 64 characters | low | The README and the `migrations.py` docstring contradict the cap; a direct doc correction. | P5 |
| 11 | BH | `publish` has no guard against recursion | low | A notification cycle raises `RecursionError`, which leaves the command and dooms the transaction: loud, rolled back. | reject |
| 12 | BH | `register_subscriber` accepts the same command twice | low | Unlikely (one line in `build_subscribers`); refusing it is a new guard. | reject |
| 13 | BH | `_on_connect` reads back only `journal_mode` | false | At connect time no transaction is open, so `foreign_keys=ON` and the other PRAGMAs take effect; `test_every_connection_gets_the_six_settings` reads all of them back on a real connection. | reject |
| 14 | BH | The `db_engine` fixture leaks the engine when its setup fails | low | Happens only when setup has already failed the test; test-only. | reject |
| 15 | BH | Nothing says where a web entry point may open its unit of work | low | The unit of work is bound to its thread, and FastAPI can run a sync `yield` dependency's setup and teardown on different threads; one sentence fixes it. | P6 |
| 16 | EC | A failed `BEGIN IMMEDIATE` leaves SQLAlchemy's in-begin flag set on the `Connection` | false | Confirmed that `dispatch.begin` sits outside SQLAlchemy's try/finally, but every caller drops the `Connection` when begin raises: `UnitOfWork._begin` closes it, and `ensure_current` and `current_revision` leave their `with`. | reject |
| 17 | EC | `GuardedSession` lacks `__contains__` and `__iter__`, and refuses attribute assignment | low | Fails loudly (`TypeError`/`AttributeError`) on rare idioms; `no_autoflush` works. | reject |
| 18 | EC | `@command` and `@use_case` accept a sync generator function, whose body runs after the wrapper exits | low | Python semantics; a direct extension of the existing async refusal. | P7 |
| 19 | EC | `open_database` builds the registry after `ensure_current` has committed, outside the dispose guard | low | A registry error would be reported as a rolled-back migration and would leak the engine; a direct reorder. | P8 |
| 20 | EC | Foreign-key violations already in the DB make every later migration fail | low | Needs rows written from outside with `foreign_keys` off; the refusal is loud; a baseline diff adds logic. | reject |
| 21 | EC | A required notification field accepts `None` (`FooterChanged(market_id=None)`) | low | `_problem` allows `None` for every field; plausible once publishers read nullable columns; one condition. | P9 |
| 22 | EC | A notification field named exactly `id` would be checked as a code | false | No notification has an `id` field; a future one fails loudly at construction, in its own story's tests. | reject |
| 23 | EC | `sqlite3.Connection(path)` gets past the one-configurator AST check | low | Nobody constructs it directly; widening the check would also flag type annotations. | reject |
| 24 | EC | The connect hook switches a foreign rollback-journal file to WAL before refusing it, against the matrix's "file unchanged"; the tests pre-set WAL | low | Confirmed by the implementer's own note and the tests; the fix reads `journal_mode` and `page_count` before changing anything. | P10 |
| 25 | VG | No test shows that a clean block exit flushes pending ORM objects | medium | Gap filed with a mutation: without `flush()`, `session.add` rows are silently dropped and the suite stays green. | P11 |
| 26 | VG | The connection's return after a timed-out BEGIN or a failed commit is untested | medium | Gap filed with mutations: removing the cleanup leaks a checked-out connection, and the suite stays green. | P12 |
| 27 | VG | Disposal of the engine when the DB is refused or a migration fails is untested | low | Gap filed with a mutation (the suite stays green); the process exits on return today, but P4 and P8 touch this code. | P13 |

## Design Notes

**Entry point, use case and command:**

```python
with units.open():                          # entry point (web, job or CLI), wired by main.py
    approval_api.approve(draft_id, rev, shown_hash)

@use_case                                   # nextron/app/approval/api.py
def approve(draft_id: str, rev: int, shown_hash: str) -> None:
    uow = current()
    with uow.transaction():                 # BEGIN IMMEDIATE ... COMMIT
        ...                                 # own repositories use uow.session
        sending_api.enqueue(draft_id)       # @command: joins this transaction
        uow.publish(MessageSent(...))       # subscribers run here, in order
```

**Notification payloads** (each publishing story may add fields under the same rules):
- `SubjectSuppressed(suppression_entry_id, source, contact_id=None, lead_id=None)`
- `ContactErased(contact_id, cause)`
- `LeadsMerged(survivor_lead_id, merged_lead_id)`
- `MessageSent(om_id, draft_id, kind, step_no=None)`
- `InboxRemoved(inbox_id)`
- `Enrolled(enrollment_id, lead_id)`
- `DraftRejected(draft_id, step_no=None, bad_fit=False)`
- `EvidenceChanged(lead_id, evidence_id)`
- `EventClosed(signal_id, lead_id)`
- `FooterChanged(market_id)`

**Choices the user won't notice:**
- The model class is `EventRecord`, because "Event" is a PRD Glossary noun for a Signal.
- The DB file is `nextron.db`.
- Every transaction, including read-only ones, uses `BEGIN IMMEDIATE`, as AD-2 states. Keep them short.
- Revision ids stay at 31 characters or fewer, so log redaction's 32-character rule never masks them, and so they fit Alembic's default `VARCHAR(32)`.
- The startup upgrade runs with `foreign_keys` off, following Alembic's SQLite batch guidance, so a batch rebuild of a parent table doesn't fail on its children. `foreign_key_check` keeps that safe.
- A batch rebuild of `events` would silently drop its triggers; the triggers-at-head contract test catches it.

## Verification

**Commands:**
- `cd nextron-engine; uv sync --frozen; uv run pytest` -- expected: all pass, `ai_eval` skipped.
- `uv run lint-imports` -- expected: every contract KEPT.
- `$env:NEXTRON_ENV="dev"; uv run python -m nextron serve`, then `curl.exe http://127.0.0.1:8766/health` -- expected: 200; `%LOCALAPPDATA%\Nextron\dev\nextron.db` exists, and `nextron.log` shows `database_migrated` on the first start only and `engine_started` with `db_revision`.
