---
title: 'Story 1.14: Check install readiness with `nextron doctor`'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '3d2ac5993bfe05c3cc38435c4e0a04e14596ff08'
review_loop_iteration: 0
story_key: '1-14-check-install-readiness-with-nextron-doctor'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `nextron doctor` checks only five machine facts (SQLite, tzdata, OneDrive, a persistent `prod`, a free port) and prints PASS or FAIL. It can't say what an install still lacks: credentials, the `internal_notify` grant, required settings, a backup dir, the database revision. It has no `--env`, no warnings and no JSON, and it calls a running engine's port a failure. So Mac can't confirm a first install or an upgrade is green before real work starts.

Requirements: FR-56, AD-2, AD-14, AD-20, OP-1, OP-2.

**Approach:** A frozen doctor check registry, built in `main.py`, that later stories extend. It runs eleven checks, each giving an id, a status (`ok`, `warn` or `fail`) and a one-line message. Doctor prints them as a table, or as JSON with `--json`. `--env` overrides `NEXTRON_ENV` for the run. Doctor opens the database read-only, and takes no lock, builds no runner and writes nothing to the database.

The acceptance criteria are Story 1.14 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Results** (`nextron/startup_checks.py`).
  - `Status(StrEnum)`: `OK = "ok"`, `WARN = "warn"`, `FAIL = "fail"`.
  - `CheckResult(id, status, message)`, frozen. It replaces the old `(name, ok, detail)`.
  - `serve` keeps its behaviour and its stderr text (`startup check failed: <id>: <message>`). It stops on `status is Status.FAIL`.
  - `CheckFailed(Exception)`: a probe raises it with a message that is safe to print.
- **The registry** (`startup_checks.py`). The types are generic over the context `C`.
  - **`DoctorCheck[C](id, run: Callable[[C], CheckResult])`**, frozen. `id` must fullmatch `[a-z][a-z0-9_]{0,63}`, and `run` must be callable; otherwise `TypeError`.
  - **`DoctorChecks[C]`** has `register_check(check)`, `freeze()`, `frozen` and `checks` (a tuple in registration order).
    - A repeated id raises `ValueError`.
    - Registering after `freeze()` raises `RuntimeError`.
  - **`run_checks(checks, context) -> list[CheckResult]`** needs a frozen registry (`RuntimeError` otherwise) and runs every check in order.
    - A check that raises gives `fail` "the check crashed (<ExceptionType>)".
    - A result that isn't a `CheckResult` with the registered id gives `fail` "the check returned no result".
    - The other checks still run either way. Newlines in a message become spaces.
  - **`format_table(results)`** gives:
    - a header `status  check  message`, with `check` padded to the longest id;
    - one row per result;
    - the line `<n> ok, <n> warn, <n> fail`.
  - **`format_json(results)`** gives `json.dumps([{"id", "status", "message"}, ...], indent=2)`, ASCII-only.
- **Probes.** `Probes` gains two fields; `fake_probes` gives defaults of (3, 14, 8) and `None`.
  - `python_version: Callable[[], tuple[int, ...]]`, which is `sys.version_info[:3]`.
  - `file_age: Callable[[PurePath], float | None]`: seconds since the file's mtime, or `None` when the file is missing.
- **The checks, in this order.** Their ids are in the table below.

  | id | ok | warn | fail |
  |---|---|---|---|
  | `python_version` | ≥ 3.14.8 | — | older, or the probe raises |
  | `sqlite_version` | as today | — | as today |
  | `tzdata` | as today | — | as today |
  | `prod_env_not_persistent` | as today; the message names the env and its source (`--env` or `NEXTRON_ENV`) | — | as today |
  | `data_dir_outside_onedrive` | as today | — | as today |
  | `backup_dir_separate` | otherwise | — | the backup dir equals the data dir, either contains the other (casefolded path parts), or it is inside OneDrive (the same rules as the data dir) |
  | `port_free` | the port is free; or it is busy and `<data dir>\heartbeat` is under 120 s old ("held by this environment's running engine") | — | busy with no fresh heartbeat (a `file_age` error counts as none); or the port probe raises |
  | `required_credentials` | every `required` credential in `domain.credentials` is set, with at most `MAX_SECRET_LENGTH` UTF-16 units (each value is read through `SecretsPort` and dropped at once) | — | lists the labels that are not set, over 1,280 characters, or unreadable (`AdapterError`) |
  | `internal_notify_grant` | `GoogleAccess.access_token("internal_notify")` returns | the `AdapterError` is `retryable` (Google unreachable) | the client is not imported, the grant is missing, it needs re-consent (invalid_grant included), Credential Manager fails, or another error occurs (named by code) |
  | `required_settings` | every required setting has a value that parses | — | lists `<label> (<key>)` for each missing or invalid one; also fails when the settings can't be read (type name only) |
  | `database_revision` | the database is at head | no database file, an empty one, or one behind head (a known revision): "will migrate on next start" | a revision this code doesn't know ("newer than this code"); a `CheckFailed`, with its message; any other error, by type name |

- **The context** (`main.py`). `DoctorContext(config, env_source, probes, secrets, google, database)`, frozen.
- **Building the registry.** `build_doctor_checks() -> DoctorChecks[DoctorContext]` is the only code that constructs `DoctorChecks` or calls `register_check`. It registers the eleven checks above and freezes the registry.
- **The database** (`DoctorDatabase(path)`, `main.py`). It never creates, migrates or writes `nextron.db`.
  - **`exists()`**: whether the file is there.
  - **`revision()`**: `migrations.current_revision` on `db.engine.create_read_only_engine(path)`, built on first use. `DatabaseStateError` and `ConnectionSettingError` become `CheckFailed(str(exc))`.
  - **`required_unset()`**: every required key when there is no file. Otherwise it is `queries.setup.required_unset()`, read in `UnitOfWorkFactory(engine, build_subscribers()).open(read_only=True)`.
  - **`close()`** disposes the engine.
- **`db/engine.py`: `create_read_only_engine(db_path)`.**
  - It opens `db_path.as_uri() + "?mode=ro"` through `sqlite3.connect(..., uri=True, autocommit=LEGACY_TRANSACTION_CONTROL)` as the `creator`, with `NullPool` and `hide_parameters=True`.
  - On each connection:
    - `isolation_level = None`;
    - the journal mode must be `wal`, or the file empty (`page_count` 0); otherwise `ConnectionSettingError`;
    - `busy_timeout`.
  - The existing `begin` (`BEGIN IMMEDIATE`, AD-2) and statement-guard listeners apply. A missing file raises on connect and is never created.
- **`db/migrations.py`: `known_revisions()`.** Every revision id in the chain.
- **`queries/setup.py`: `required_unset() -> tuple[str, ...]`.** The required keys, in declaration order, that have no row or whose stored text fails `parse`.
- **Google** (`internal_notify_grant`). Doctor builds `GoogleAccess(secrets, clock, oauth=GoogleOAuthClient(meter=DoctorMeter()), listen=LoopbackListener, files=LocalClientFiles(), browser=<always False>, env=config.env, allowlist=tuple)`.
  - **`DoctorMeter`** (`main.py`) implements `UsageMeterPort`. It keeps each call in memory (`calls`), writes nothing, and `snapshot` returns an empty `Usage`.
  - Tests inject `google_oauth` and `secrets`.
- **`doctor(*, env=None, as_json=False, probes=None, secrets=None, google_oauth=None, clock=None, out=None, err=None) -> int`.** It loads `load_bootstrap(env)`, runs the registry, and prints the table or the JSON (nothing else) to `out`.
  - **Exit codes:** 0 when nothing is `fail`, 1 otherwise, and 2 on a `BootstrapError`, as before (message on `err`).
  - It never constructs `InstanceLock`, calls `configure_logging`, `ensure_current`, `open_database`, `connect_database` or `build_app`, or builds a runner, scheduler or heartbeat writer.
- **The environment.** `bootstrap.load_bootstrap(env: str | None = None)`: a given `env` overrides `NEXTRON_ENV`, and the other variables still apply.
- **The CLI** (`__main__.py`): `doctor [--env {dev,test,prod}] [--json]`.
- **No secret value, account address or exception text** (only the type name, or a `CheckFailed` message) appears in any message, in either format.
- **Tests** run as `test` with fakes. No test reads the real Credential Manager or makes a non-loopback call.

**Never:**
- **No new parts.** No migration, table, dependency, adapter, port, route, setting or credential.
- **What not to touch:** `serve`'s and `open`'s behaviour, the vendored HTMX, and anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.
- **Out of scope:**
  - the health panel's readiness list (1.15);
  - the off-PC backup, ledger, Worker, token-key, footer and inbox checks (their stories);
  - binding a data dir to its environment (deferred).

### Decisions made overnight (pending user review)

1. **`internal_notify_grant` makes one real token refresh with Google whenever Mac runs doctor and the client and grant are stored.**
   - It goes through `GoogleAccess.access_token`, so "refreshable" is proven, not guessed.
   - With no client or no grant, or a grant already marked for re-consent, nothing is sent.
   - Doctor never writes to the database, so its `DoctorMeter` records the call in memory only, and it is not on the Quotas page. `google_oauth` has no quota, so no limit is affected.
   - An `invalid_grant` answer marks the grant "needs re-consent" in Credential Manager, as the engine would.
   - Google being unreachable is `warn`, not `fail`.
   - Tests and this build never reach Google.
2. **"Held by this environment's running engine" means the port is busy and `<data dir>\heartbeat` is under 2 minutes old.** That is the health panel's "running" threshold. Doctor uses no lock probe and no HTTP call, so a hung engine shows as "in use".
3. **The backup dir also fails when nested in either direction, or inside OneDrive.** A plaintext database copy synced by OneDrive would leave the PC unencrypted.
4. **No database file is `warn` ("will be created and migrated on next start")**, and then every required setting counts as missing.
5. **The database is opened read-only (`mode=ro`) with AD-2's `BEGIN IMMEDIATE`.** Doctor can therefore wait up to 5 s while the engine writes. It may leave empty `nextron.db-wal` and `-shm` files, which the engine's next start removes.
6. **The existing ids `prod_env_not_persistent` and `port_free` are kept.** Exit 2 on invalid bootstrap stays (Story 1.1). `--env` takes only `dev`, `test` or `prod`.
7. **The console-script subprocess test now runs `nextron doctor --help`,** so no test process reads the real Credential Manager.
8. **A stored required setting that no longer parses counts as missing.** Unreadable Credential Manager entries `fail`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| All ok | fakes healthy; DB at head with both required settings; 5 required credentials; client and grant; refresh ok | 11 `ok` rows in order, header and `11 ok, 0 warn, 0 fail`; exit 0; DB file SHA-256 unchanged | N/A |
| Fresh install | no data dir, empty secrets | `required_credentials`, `internal_notify_grant`, `required_settings` fail; `database_revision` warn; exit 1; data dir still absent | N/A |
| Behind | `alembic_version` set to `0005_1_12_notify` | `database_revision` warn "…will migrate on next start"; revision unchanged | N/A |
| Ahead / foreign | `0099_future`; tables without revision; non-WAL file | fail ("newer than this code"; the `CheckFailed` message) | N/A |
| Port | busy + heartbeat 30 s / 300 s / missing | ok "held by this environment's running engine" / fail / fail | probe raises: fail |
| Backup dir | equal (other case), inside the data dir, containing it, under `%OneDrive%` | fail each; defaults ok | N/A |
| Credential | one missing; one 1,281 units; `read` failing | fail naming each label | N/A |
| Grant | ok; invalid_grant; meta needs re-consent; `google_unavailable`; no client; no grant | ok; fail and meta marked; fail with no call; warn; fail; fail | N/A |
| Settings | `alert_chat_id` unset; `sender_timezone` stored as `Mars/Base` | fail naming `alert_chat_id` / `sender_timezone` | N/A |
| `--env` | `NEXTRON_ENV=dev`, `--env prod` | prod's data dir, port 8765 and secrets namespace; message says `--env` | invalid value: argparse exit 2 |
| JSON | `--json` | stdout parses to the same list of `{id, status, message}` | N/A |
| Crash | a registered check raises | that row fail "the check crashed (RuntimeError)"; the rest run | N/A |
| Secrets | planted credential, refresh token, client secret, account | none in stdout or stderr, in either format | N/A |

</frozen-after-approval>

## Code Map

- **Doctor today** (`nextron-engine/nextron/main.py:640-660`). `doctor()` calls `all_checks` and prints PASS/FAIL. `serve()` (559-637) uses `environment_checks` and `check_port_free`, and `_report_failures` (552) prints `startup check failed: <name>: <detail>`.
  - **Constants:** `HOST`, `DB_FILE_NAME`, `HEARTBEAT_FILE_NAME` and `DATABASE_REVISION_CHECK` (reuse it as the id).
  - **To rewrite:** the module docstring's doctor paragraph (103-105).
- **`build_app` (265-319)** is the pattern for `GoogleAccess` construction. `connect_database` and `build_subscribers` are reused.
- **`nextron-engine/nextron/startup_checks.py`.**
  - **What's there:** `CheckResult` (52), `Probes` (61-80), the check functions, `environment_checks`, and `all_checks` (253), which is removed in favour of the registry.
  - **What changes:** the module docstring gets a "Doctor" section.
  - **Its imports:** it is a composition-root helper and may import `nextron.domain`, `nextron.ports` and the standard library. Keep it free of `nextron.db` and `nextron.app` imports: those reach the checks through callables that `main` wires.
- **`nextron-engine/nextron/__main__.py`** (doctor subparser, line 39; dispatch, 52) and its docstring.
- **`nextron-engine/nextron/bootstrap.py`: `load_bootstrap` (85).** Pass `env` as an init kwarg: pydantic-settings gives init kwargs priority, and `_apply_environment_defaults` sees the merged dict.
- **`nextron-engine/nextron/db/engine.py`.** Reuse `_on_begin`, `_refuse_transaction_sql`, `ConnectionSettingError` and `BUSY_TIMEOUT_MS`. `read_only_connection` already works on a `mode=ro` connection: it was probed live, and `BEGIN IMMEDIATE` succeeds there while writes fail. Add the function to the docstring.
- **`nextron-engine/nextron/db/migrations.py`.** `current_revision`, `head_revision` and `_script` exist; add `known_revisions`.
- **`nextron-engine/nextron/app/queries/setup.py`.** Its import rule is the domain and `repos.setup` only. Use `settings_schema.declared`, `parse` and `InvalidSettingValue`.
- **Google.**
  - `nextron/app/setup/api.py` `GoogleAccess.access_token` (913-949) raises:
    - `google_client_not_imported`, `google_not_granted` and `google_needs_reconsent` (no call);
    - on invalid_grant, it marks the grant, then raises `google_needs_reconsent`;
    - other port errors propagate.
  - The codes are in `nextron/ports/google_oauth.py`, and `GRANTS` in `nextron/domain/google_oauth.py`.
  - `GoogleOAuthClient(meter=...)` builds its HTTP client lazily.
- **Credentials.** `nextron/domain/credentials.py`: `declared()` and `.required`. There are 5 required credentials: claude, hunter, companies_house, telegram_bot and unsubscribe_bearer. `nextron/ports/secrets.py` has `secret_length`, `MAX_SECRET_LENGTH` and `KEYRING_FAILED`.
- **Usage.** `nextron/domain/budget.py`: `Usage(month, api, ai_calls, ai_cost_micros)` and `month_of`.
- **Tests to update.**
  - **`tests/fakes/probes.py`:** the new fields.
  - **`tests/unit/test_startup_checks.py`:** `.ok`, `.detail` and `.name` become `.status` and `.message`.
  - **`tests/unit/test_doctor.py`:** rewrite it around `run_doctor(...)`, which injects `InMemorySecrets("dev")`, `FakeGoogleOAuth`, `FrozenClock` and `fake_probes`. Keep:
    - the persistent-env rows;
    - no lock;
    - serve not reading the persistent env;
    - exit 2;
    - CLI dispatch, which is now called with `env` and `as_json`.
  - **`tests/unit/test_logconfig.py:515`** (doctor never opens the log file): inject the fakes.
  - **`tests/app/test_serve_process.py:272`** (the console script): run `nextron doctor --help`, and assert exit 0 and `--json` and `--env` in stdout.
- **Contract pattern.** `tests/contracts/test_job_contracts.py` has `Registry` and `registration_problems` (lines 57-140). Reuse them for `Registry("build_doctor_checks", "DoctorChecks", "nextron.startup_checks", "register_check")`.
- **Helpers.** `tests/migration_chains.py` has `run_sql`, `sha256` and `stored_revisions`. The `db_engine` fixture is at head. In `tests/fakes/google_oauth.py`, `token_set()` and `FakeGoogleOAuth.refreshes` let a test queue an `AdapterError`.
- **What to leave alone:** no revision literal changes, because there is no migration. `test_contract_coverage` is unaffected, because there is no new top-level module.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/startup_checks.py` -- `Status`, the renamed `CheckResult`, `CheckFailed`, the new `Probes` fields, the six new or changed checks, `DoctorCheck`, `DoctorChecks`, `run_checks`, `format_table`, `format_json`; drop `all_checks`; update the docstring -- the checks and the registry type in one composition-root helper.
- [x] `nextron-engine/nextron/db/engine.py`, `db/migrations.py`, `app/queries/setup.py` -- `create_read_only_engine`, `known_revisions`, `required_unset` -- read-only access for doctor.
- [x] `nextron-engine/nextron/bootstrap.py`, `nextron/__main__.py` -- `load_bootstrap(env)` and the `--env` and `--json` flags.
- [x] `nextron-engine/nextron/main.py` -- `DoctorContext`, `DoctorDatabase`, `DoctorMeter`, `build_doctor_checks`, the new `doctor`, and `serve`'s status filter; update the docstring -- the registry and the command.
- [x] `nextron-engine/tests/fakes/probes.py`, `tests/unit/test_startup_checks.py` -- the fields; unit rows for each new check (Port, Backup dir, Credential, Grant, Settings and Behind/Ahead through injected callables), the registry rules (bad id, duplicate, after freeze, run before freeze), `run_checks` crash, wrong-id and newline handling, and the exact `format_table` and `format_json` output.
- [x] `nextron-engine/tests/unit/test_doctor.py` -- every matrix row end to end through `main.doctor` with fakes and a real temporary database. Also check:
  - doctor never calls `InstanceLock`, `ensure_current`, `build_app`, `Runner` or `configure_logging` (each patched to raise);
  - the DB SHA-256 is unchanged and the `api_usage` row count is 0 after a refresh through the real `GoogleOAuthClient` wiring on an `httpx2.MockTransport` (`main.GoogleOAuthClient` patched to inject `http`), with exactly one call in that run's `DoctorMeter`.
- [x] `nextron-engine/tests/unit/test_db_engine.py`, `tests/unit/test_migrations.py`, `tests/app/test_setup_settings.py`, `tests/unit/test_bootstrap.py` -- the read-only engine:
  - a missing file is not created;
  - reads work while another connection holds an open read;
  - an `INSERT` is refused, and the file is unchanged;
  - non-WAL is refused.

  Also `known_revisions`, `required_unset` (none, unset, invalid) and the `load_bootstrap(env)` override.
- [x] `nextron-engine/tests/contracts/test_doctor_contracts.py` -- only `build_doctor_checks` builds or fills `DoctorChecks` (with a probe proof); the registry ships frozen with exactly the eleven ids in order.
- [x] `nextron-engine/tests/unit/test_logconfig.py`, `tests/app/test_serve_process.py` -- as in the Code Map.
- [x] `nextron-engine/README.md` -- rewrite "Check the machine" as "Check readiness": the eleven checks, statuses, the table, `--json`, `--env`, exit codes, read-only database, the Google refresh (decision 1), and what doctor never does.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `NEXTRON_ENV=dev` on this PC, with no `internal_notify` grant stored for `dev`, when `uv run nextron doctor` runs, then it prints the eleven checks with no call to Google and exits 0 or 1 by the rule.

## Implementation Notes

- **One port check, two callers.** `check_port_free(host, port, probes, *, heartbeat=None)`:
  - `serve` passes no heartbeat, so a busy port still fails as before;
  - doctor passes `<data dir>\heartbeat`, and a busy port is `ok` while the heartbeat is fresh (`HEARTBEAT_FRESH_S` = 120 s).
- **Signatures the spec left open.**
  - `check_required_credentials(read)` takes `SecretsPort.read`, so each value is dropped inside the check.
  - `check_prod_env_not_persistent(probes, env=None, env_source=None)` adds "; this run is <env>, from <source>" only when doctor passes them, so `serve`'s callers are unchanged.
- **Messages hold only type names.** A failing SQLite-version or port probe now shows only its exception's type (it used to show `{exc!r}`), to keep the "no exception text" rule. `serve`'s stderr format (`startup check failed: <id>: <message>`) is unchanged.
- **The database.** `DoctorDatabase.exists()` is `Path.exists()`. `create_read_only_engine` passes the file URI (`Path.absolute().as_uri() + "?mode=ro"`) through a `creator`, so spaces and `#` in a path work; there is a test for both. `known_revisions()` and `head_revision()` are read in the `database_revision` lambda.
- **Test helpers.** `fake_probes` also takes a probe callable for `sqlite_version` and `python_version`, so a test can make one raise. The console-script test runs `nextron doctor --help` (decision 7).
- **The dev database on this PC** is still at `0001_1_3_persistence_foundation`, from before the `settings` table. Doctor therefore shows `required_settings` as "the settings could not be read (OperationalError)" until a dev `serve` migrates it. See Review Triage Log entry E.

Review fixes (pass 1, P1–P4; see the Review Triage Log):
- **P1.** The README no longer says doctor "can wait up to 5 seconds". On a read-only file `BEGIN IMMEDIATE` opens a read transaction, so a read doesn't wait for the engine's writes.
- **P2.** The README and the `db/engine.py` docstring describe the leftover empty `-wal`/`-shm` files correctly: the engine reuses them, and SQLite deletes them when the engine's last connection closes at a clean stop.
- **P3.** A heartbeat counts as fresh only when `abs(age) < HEARTBEAT_FRESH_S`. A heartbeat dated in the future (the clock set back) is no longer fresh however far ahead it is. Tests: -5 s gives `ok`, -300 s gives `fail`.
- **P4.** The `port_free` fail message takes its minutes from `HEARTBEAT_FRESH_S` and reads "...: <heartbeat> is missing or not within the last 2 minutes (a hung engine shows the same)".

**Final verification (after pass 1).**
- `uv run pytest -q`: 3815 passed, 2 skipped (`ai_eval` and the symlink test), in 220 s.
- `uv run lint-imports`: 29 kept, 0 broken.
- Before the review fixes: 3813 passed, 2 skipped, and 29 kept.

**Live check on this PC (2026-11-17).**
- `cmdkey /list` shows no `nextron-engine` entry, so no Google client or grant is stored.
- `NEXTRON_ENV=dev uv run nextron doctor` printed the 11 rows (`7 ok, 1 warn, 3 fail`) and exited 1. `--json` gave the same list. Nothing reached Google, and no secret was printed.
- The failures:
  - `required_credentials` lists the 5 labels;
  - `internal_notify_grant` says the client is not imported;
  - `required_settings` is entry E.
- `database_revision` warns: `0001…`, behind `0006_1_13_metering`.

Not verified here:
- a real token refresh with Google, because no grant exists;
- `nextron doctor --env prod` on this PC.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (15 findings), EC = edge-case hunter (5), VG = verification gap (0 gaps, 1 other finding).
- **Counts:** 21 raw findings, 20 entries after deduplication. BH2, BH6 and BH9 each make separate claims, so each is split across rows.
- **Outcomes:**
  - 4 entries patched (P1–P4): 2 doc fixes, 1 freshness fix and 1 message fix;
  - none deferred;
  - 16 rejected, 2 of them as false.
- **No loopback.** No finding is an intent gap or a bad spec.
- **Rejected only because the fix would change the approved spec:** BH6a, BH12 and EC5 (decision 2, the exit-2 rule and "the other variables still apply").

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | VG-other1, BH9 (part) | The README says doctor "can wait up to 5 seconds while the engine writes". | low | Verified. `test_reads_work_while_the_engine_holds_the_write_lock` reads through the read-only engine, with no wait, while another connection holds `BEGIN IMMEDIATE` and an uncommitted insert. On a `mode=ro` file, `BEGIN IMMEDIATE` opens a read transaction. The doc fix is direct. | P1 |
| B | BH9 (part) | The README and the `engine.py` docstring say the leftover empty `-wal`/`-shm` files are removed by "the engine's next start". | low | Verified: no code removes them. The engine reuses them, and SQLite deletes them when the last connection closes cleanly. A live probe on 2026-11-17 showed this: after a writer's last close, only the `.db` file was left. The doc fix is direct. | P2 |
| C | BH5, EC1 | A heartbeat with a future mtime (clock set back) gives a negative age, which counts as fresh however far ahead it is. A stale heartbeat plus a foreign process on the port then reads as "held by this environment's running engine". | low | Verified at `check_port_free`: `fresh = age is not None and age < HEARTBEAT_FRESH_S`. The fix is direct: `abs(age) < HEARTBEAT_FRESH_S`, which also tolerates a few seconds of clock correction while the engine runs. | P3 |
| D | BH6 (part) | The port fail message hard-codes "more than 2 minutes" instead of deriving it from `HEARTBEAT_FRESH_S`. | low | Verified. The text would drift from the constant. The fix is direct. | P4 |
| E | BH1, BH2 (part), EC2 | An empty database file, or one from before `0002_1_6_settings` (no `settings` table), makes `required_settings` say "the settings could not be read (OperationalError)" instead of listing the missing settings. | low | True, and the status is the correct `fail` either way. An empty file can't arise in normal use, because the engine writes the WAL header on first connect. A pre-Story-1.6 database exists only as a stale dev copy (this PC's dev database is at `0001`), and the next `serve` migrates it. A prod database is created at head. The fix adds revision-dependent branches. | reject |
| F | BH2 (part) | For a foreign or non-WAL file, `required_settings` shows only "(ConnectionSettingError)". | low | True, and harmless: `database_revision` fails on the same run with the full reason. The fix adds error mapping for a state that already fails twice. | reject |
| G | BH3 | No import-linter contract enforces "`startup_checks` never imports `nextron.db` or `nextron.app`". | low | True. Breaking the rule breaks no layer, because `startup_checks` is a composition-root helper that AD-1 lets reach any layer. Only a code-organisation preference would erode. A new contract changes the contract set (29). | reject |
| H | BH4 | `python_version` passes 3.15, which `requires-python` (`<3.15`) excludes. | low | The check is the AC's literal "Python ≥ 3.14.8". uv refuses a 3.15 interpreter for this project, so reaching it means bypassing `uv run`. The fix adds a branch. | reject |
| I | BH6 (part) | Port ownership is inferred from heartbeat freshness alone, so a different `NEXTRON_PORT` in the engine's shell, or a foreign holder while the engine runs, gives a false ok. | low | This is decision 2, which accepted the limitation and said so. Recording the port or pid in the heartbeat changes Story 1.11's heartbeat (new surface). | reject |
| J | BH6 (part) | `HEARTBEAT_FRESH_S` claims to be the health panel's threshold, but nothing shares it, so Story 1.15 starts with a second source of truth. | false | No other definition exists: grepping `nextron/` finds no other 2-minute heartbeat threshold. Story 1.15 builds the panel and owns its constant. | reject |
| K | BH7, EC3 | Redirected table output crashes with `UnicodeEncodeError` (cp1252) when a printed path has a character outside the code page; nothing outside `run_checks` is guarded. | low | True for Python 3.14 on Windows. Every printed path derives from this PC's ASCII profile path (`C:\Users\mac\...`) or a variable Mac sets, and the old doctor printed the same data-dir path. The fix adds encoding handling. | reject |
| L | BH8 | With no logging configured, warnings (such as `google_grant_needs_reconsent`) reach stderr through `logging.lastResort`, bypassing redaction. | false | `nextron.logconfig` replaces `logging.lastResort` at import with a redacting stderr handler at WARNING (`logconfig.py:164`), and `main` imports it. The docstring names doctor as a case it covers. | reject |
| M | BH10 | The revision classification is duplicated (`ensure_current` builds `known` inline, and the "newer than this code" wording appears twice), and the Alembic scripts are walked twice even with no database. | low | The two wordings agree today. The extra walk costs milliseconds once per doctor run. The fix refactors existing startup-migration code. | reject |
| N | BH11 | `DoctorDatabase.exists()` uses `Path.exists()`: an unstat-able data dir reads as "no database yet", and a directory named `nextron.db` counts as a database. | low | True, but both need a broken or deliberately odd data dir. The run still exits 1, because every required setting is then listed as missing. Explicit stat-error handling adds branches. | reject |
| O | BH12 | `--json` prints nothing on stdout on exit 2. | low | This is the specified behaviour: the bootstrap error goes to stderr with exit 2, "as before". The README documents exit 2. The fix changes the approved behaviour. | reject |
| P | BH13 | The eleven ids are listed in five places (two test lists, the README, two docstrings). | low | The pinning is deliberate, like the registered job types in `test_job_contracts.py`. The README and docstrings document the list for Mac. A later story adding a check updates them on purpose. | reject |
| Q | BH14 | "over 1,280 characters" is wrong for non-BMP characters, which count as 2 UTF-16 units. | low | It is the project's wording everywhere (`SECRET_TOO_LONG`, "secret exceeds 1,280 characters"), and the AC's. API keys and tokens are ASCII. | reject |
| R | BH15 | `_on_read_only_connect` copies `_on_connect`'s journal-mode guard. | low | True. The WAL rule is fixed by AD-2, and both copies are tested (the non-WAL refusal in each engine's tests). The fix refactors the existing configurator. | reject |
| S | EC4 | The backup-dir check is lexical, so a junction, symlink, 8.3 name or `subst` drive into the data dir passes. | low | It needs a deliberately crafted `NEXTRON_BACKUP_DIR`, and it is the same lexical approach as the existing OneDrive check. Resolving paths adds filesystem calls and branches. | reject |
| T | EC5 | `--env prod` in a shell that sets `NEXTRON_DATA_DIR`, `NEXTRON_PORT` or `NEXTRON_BACKUP_DIR` for dev checks prod secrets against dev's paths. | low | This is the spec's rule ("the other variables still apply"). The default setup sets only `NEXTRON_ENV`, and the messages print the data and backup dirs, so a mismatch is visible. The fix changes the approved behaviour. | reject |

## Design Notes

The checks stay pure functions in `startup_checks.py` over injected callables. `main` binds them to the real parts, so Story 1.15 can bind the same registry to the running engine's parts:

```python
DoctorCheck(REQUIRED_SETTINGS, lambda c: check_required_settings(c.database.required_unset))
DoctorCheck(INTERNAL_NOTIFY_GRANT, lambda c: check_internal_notify_grant(c.google.access_token))
DoctorCheck(DATABASE_REVISION, lambda c: check_database_revision(
    c.database.exists(), c.database.revision, head=head_revision(), known=known_revisions()))
```

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` and the symlink test skipped).
- `uv run lint-imports` -- expected: 29 contracts KEPT.
- `cmdkey /list | findstr nextron-engine/dev/google` -- expected: no `internal_notify` entry. Run this before the next command.
- `$env:NEXTRON_ENV="dev"; uv run nextron doctor` and `uv run nextron doctor --json` -- expected: 11 rows, no secret printed.
