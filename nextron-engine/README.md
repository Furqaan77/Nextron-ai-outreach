# Nextron engine

The Nextron Outreach Engine: a single-user engine that runs on one Windows 11 PC and serves a
dashboard on `127.0.0.1` only. The architecture lives in
`_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md`.

## Install

Needs [uv](https://docs.astral.sh/uv/) 0.13. uv installs Python 3.14 (3.14.8 or later, below
3.15) on its own.

```powershell
cd nextron-engine
uv sync --frozen
```

`--frozen` installs exactly what `uv.lock` records. Every runtime package is pinned in
`pyproject.toml`; change a pin there, run `uv lock`, and commit both files.

## Choose an environment

`NEXTRON_ENV` is required and has no default. Each environment has its own data dir, port and
lock, so `dev`, `test` and `prod` can run side by side. Only the logon task sets `prod`.

| Variable | Default |
|---|---|
| `NEXTRON_ENV` | none: `dev`, `test` or `prod` |
| `NEXTRON_DATA_DIR` | `%LOCALAPPDATA%\Nextron\<env>` (never under OneDrive) |
| `NEXTRON_BACKUP_DIR` | `%LOCALAPPDATA%\Nextron\backups\<env>` |
| `NEXTRON_PORT` | 8765 `prod`, 8766 `dev`, 8767 `test` |
| `NEXTRON_PLAYBOOK_PATH` | `../docs/playbook/sales-playbook.md` |

Relative paths resolve against this folder. Every other setting lives in the database.

```powershell
$env:NEXTRON_ENV = "dev"
```

## Check the machine

```powershell
uv run nextron doctor
```

Prints each startup check as PASS or FAIL: the SQLite version (3.51.3 or later), timezone data
(`Europe/London`), a data dir outside OneDrive, and a free port. Exits 0 when nothing fails,
1 when a check fails, and 2 when `NEXTRON_ENV` (or another variable above) is unset or invalid.
It takes no lock, creates nothing and starts no server.

## Run the engine

```powershell
uv run nextron serve            # or: uv run python -m nextron serve
uv run nextron serve --reload   # dev only: restart when the code changes
uv run nextron open             # in a second terminal: open the dashboard (see below)
```

`serve` validates `NEXTRON_ENV`, runs the startup checks, creates the data dir and its `logs`
folder, takes an exclusive lock on `<data dir>\nextron.lock`, checks the port, opens the
database (see [Database](#database)), then runs Uvicorn on `127.0.0.1` with one worker.
`GET /health` returns `{"env", "version", "uptime_s"}` and nothing else, but only to a browser
with a dashboard session: like every other route, it answers 401 without one.

```powershell
curl.exe -i http://127.0.0.1:8766/health                           # 401 and the locked page
curl.exe -i -H "Host: evil.example" http://127.0.0.1:8766/health   # 400
```

(`curl.exe`: in PowerShell 5.1, `curl` is `Invoke-WebRequest`.)

| Exit code | Meaning |
|---|---|
| 0 | stopped normally |
| 1 | unexpected error (a Python traceback) |
| 2 | `NEXTRON_ENV` unset or invalid, another invalid setting, a failed startup check (the message names it, `database_revision` included), a data dir that can't be created, a failed database migration, or a server that failed to start |
| 3 | another instance is running for this environment |

The supervisor (`scripts/run.ps1`, a later story) restarts the engine on any exit except 3.
It watches the heartbeat file, not `/health`.

## Open the dashboard

```powershell
$env:NEXTRON_ENV = "dev"
uv run nextron open
```

With the engine running, `nextron open` writes a one-time code to Windows Credential Manager
and opens the default browser at `http://127.0.0.1:<port>/enter?t=<code>`. The link works once,
within 5 minutes: the dashboard deletes the code, starts a session, sets the `nextron_session`
cookie (`HttpOnly; Path=/; SameSite=Strict`) and shows the dashboard home. Run `nextron open`
again for a new link. The code is never printed or logged.

| Exit code | Meaning |
|---|---|
| 0 | the browser was opened |
| 1 | `engine not running` (nothing was written), the code couldn't be stored in Credential Manager, or the browser couldn't be opened (the code is then withdrawn) |
| 2 | `NEXTRON_ENV` unset or invalid, or another invalid setting |

Sessions live in the engine's memory, so they end when the engine restarts (or when the browser
drops its cookie); there is no idle timeout. A request without a session, `GET` included, gets
401 with a page saying to run `nextron open`; so does a reused, expired or wrong code. A wrong
code leaves the pending one in place, so a junk `/enter` from another website can't cancel your
link. Browsers don't scope cookies by port, so the browser sends the `nextron_session` cookie
to every web server on `127.0.0.1` it visits, not only to the engine, and the environments
share that one cookie: opening one environment's dashboard ends the browser's session with the
other. Run `nextron open` again to switch back.

These protections keep out other websites and other Windows users' processes, not your own:
any process running as your Windows user can read Credential Manager, and with it the pending
code.

**What the dashboard enforces (AD-13):**

- It answers only `Host: 127.0.0.1:<port>` or `localhost:<port>`; anything else gets 400 before
  any handler runs, which stops DNS rebinding.
- Every response carries the strict `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`, and no CORS header.
- Every route declares whether it shows personal data, with `@personal_data(True|False)` under
  the route decorator. Unless it declared `False`, its responses get `Cache-Control: no-store`,
  and `create_app` refuses to start with an undeclared route.
- Handlers are sync `def`. Pages load only the vendored HTMX and `app.css`, with no inline
  script or style. HTMX is configured through `<meta name="htmx-config">` (`allowEval:false`,
  `allowScriptTags:false`, `selfRequestsOnly:true`, `historyCacheSize:0`,
  `includeIndicatorStyles:false`).
- The navigation shows each section (Queue, Signal Inbox, Leads/Pipeline, Funnel, Settings,
  Health) once its page route exists.

`tests/contracts/test_web_contracts.py` checks the handler, declaration, inline-code and
autoescaping rules.

## Secrets

Secrets live only in Windows Credential Manager, through `SecretsPort`
(`nextron/adapters/secrets`), under the service `nextron-engine/<env>`, so `dev` never reads a
`prod` entry. A value holds at most 1,280 characters; a longer one is refused with "secret
exceeds 1,280 characters". The adapter always uses Credential Manager (`WinVaultKeyring`),
whatever `PYTHON_KEYRING_BACKEND` or a keyringrc file says, and keeps entries on this machine
(they don't roam with a roaming profile). To inspect or remove an entry by hand, open
Credential Manager > Windows Credentials: keyring keeps one secret at the target
`nextron-engine/<env>` and every other one at `<name>@nextron-engine/<env>` (for example
`session/open_code@nextron-engine/dev`). Tests use the in-memory fake `tests/fakes/secrets.py`
and never touch Credential Manager.

## Database

One SQLite file per environment: `<data dir>\nextron.db`, for example
`%LOCALAPPDATA%\Nextron\dev\nextron.db`. Only `nextron/db/engine.py` opens it: WAL,
`busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`, and `BEGIN IMMEDIATE` for every
transaction, reads included, so keep transactions short. Transaction SQL sent any other way
(`COMMIT`, `ROLLBACK`, `SAVEPOINT` and so on) raises. Nothing copies the file; backups arrive in
a later story.

**Startup migration.** After the port check, `serve` creates the file if it is missing and runs
`alembic upgrade head` in one transaction when the database is behind the code, logging
`database_migrated` with `from_revision` and `to_revision`. `engine_started` carries
`db_revision`. `serve` refuses, with exit 2 and `startup_check_failed`
(`check=database_revision`), and changes nothing, when:

- the database's revision is unknown to this code: "database revision <rev> is newer than this
  code" (run the newer code, or restore a backup);
- it has several revisions, or tables but no revision, or it is a non-empty file not in WAL
  mode (it isn't this engine's database; the engine switches only an empty file to WAL).

A file that can't be opened (not SQLite, locked too long), or a migration that fails or leaves
foreign-key violations (rolled back), ends with exit 2 and `database_migration_failed` with the
traceback in `nextron.log`.

**Migrations are forward-only.** Every `downgrade()` raises `NotImplementedError`; a downgrade
could drop the append-only `events` audit trail. Recover from a bad upgrade by restoring the
`pre_migrate` backup (a later story).

**Adding a migration.** Alembic is configured in code (`nextron/db/migrations.py`); there is no
`alembic.ini` and no `alembic` command. A story that changes the schema:

1. Changes the models in `nextron/db/models/` (a new module is imported in its `__init__.py`),
   using `id: Mapped[str] = ulid_pk()` for the key, `UtcDateTime` for times and unique columns for
   natural keys.
2. Maps every new column to its writer in `nextron/db/owners.py` (`TABLES`) and classifies it in
   `nextron/db/pii_registry.py` (`none`, `redact`, `null` or `keep_hash`).
3. Hand-writes `nextron/db/alembic/versions/NNNN_<epic>_<story>_<short slug>.py`, for example
   `0001_1_3_persistence_foundation.py`: `NNNN` is the next number, underscores only (Alembic
   rejects `-`), the id is at most 31 characters and equals the file name, `down_revision` is
   the previous head, and `downgrade()` raises `NotImplementedError`. `env.py` runs it in batch mode, so `op.alter_column` and friends
   rebuild SQLite tables; a rebuild of `events` must create its triggers again.

`tests/contracts/test_persistence_contracts.py` checks all of it: one linear chain, the id rules,
forward-only downgrades, owner and PII coverage, the key and datetime rules, the `events`
triggers, and that the models match a database at head (the drift test).

**Units of work.** Each entry point (a web handler, a job handler or a CLI command) opens one
unit of work and calls one use case. Only the use case opens transactions; owner commands join
the open one and never begin, commit or roll back:

```python
from nextron.db import eventlog
from nextron.db.uow import command, current, use_case

with units.open():                        # the entry point; main.py builds `units`
    approval_api.approve(draft_id, rev, shown_hash)

@use_case                                 # nextron/app/approval/api.py
def approve(draft_id: str, rev: int, shown_hash: str) -> None:
    uow = current()
    with uow.transaction():               # BEGIN IMMEDIATE ... COMMIT; rolls back on error
        ...                               # own repositories use uow.session
        eventlog.append(entity="drafts", entity_id=draft_id, from_status="ready",
                        to_status="approved", cause="operator_approved", cause_ref=None,
                        actor="operator", at=now)
        sending_api.enqueue(draft_id)     # an @command: joins this transaction
        uow.publish(MessageSent(...))     # subscribers (registered in main.py) run here
```

No transaction spans a network call: prepare in one `uow.transaction()` block, make the port
call, then apply in a second block. An exception that leaves a command rolls the whole
transaction back, even when the use case catches it. A web entry point opens its unit of work in
the sync handler body, not in a `Depends` or a middleware: FastAPI may run a dependency's setup
and teardown on other threadpool threads than the endpoint, and a unit of work is bound to the
thread that opened it.

## Logs

`serve` writes `<data dir>\logs\nextron.log`, for example
`%LOCALAPPDATA%\Nextron\dev\logs\nextron.log`. Logging starts only once `serve` holds the lock,
so a second instance and `doctor` never open the file. Records at WARNING and above also go to
stderr. Startup failures (`startup_check_failed`, `database_migration_failed`,
`server_start_failed`), crashes (`engine_crashed`, `thread_crashed`) and Python warnings are
logged too, and `engine_stopped` carries the exit code. A first start also logs Alembic's own
`Running upgrade ...` lines.

`serve --reload` (dev only) is the exception: Uvicorn serves from a worker process that does
not write `nextron.log`, so only the reloader's own records reach the file. The worker's
WARNING and above go to stderr, redacted.

- **Format:** one JSON object per line, UTF-8, with `ts` (UTC, ISO 8601 with milliseconds and
  `Z`), `level`, `logger`, `event`, any scalar ID fields passed as `extra`, `exc` (the
  traceback) when there is one, and `stack` with `stack_info=True`. ULIDs, UUIDs, enums, paths
  and dates in `extra` are written as text. Uvicorn's records, the access log included, are
  written the same way. Keep event names under 32 characters; a contract test checks literal
  ones.
- **Rotation:** at 10 MiB the file rolls over to `nextron.log.1`, and so on up to
  `nextron.log.9`: 10 files at most.
- **Level:** INFO. The loggers of `googleapiclient`, `google_auth_oauthlib`, `google.auth`,
  `google_auth_httplib2`, `httplib2`, `httpx2`, `httpcore2`, `urllib3`, `requests_oauthlib`,
  `oauthlib` and `anthropic` stay at WARNING.
- **Redaction:** logs hold IDs only. As a safety net, every message, argument, traceback and
  string field passes through `nextron.domain.redaction.redact`. It removes URL query strings
  and fragments and masks URL userinfo, `/bot<token>` paths, `Bearer` and `Basic` credentials,
  the values of credential fields (`api_key`, `x-api-key`, `token`, `secret`, `password` and
  similar), email addresses (also `%40`-encoded and Unicode ones), `sk-ant-` keys, base64 blobs
  and any run of 32 or more key-like characters. 26-character ULIDs survive. It does not mask
  phone numbers, names or other free text. Use `redact` for any error text you store, too.
  `AdapterError` redacts its message when it is raised.

```powershell
Get-Content "$env:LOCALAPPDATA\Nextron\dev\logs\nextron.log" -Tail 20 -Wait
```

## Test

```powershell
uv run pytest               # the unit, app and contracts suites; ai_eval is skipped
uv run pytest -m ai_eval    # the opt-in AI evaluation suite
uv run lint-imports         # the import contracts on their own
```

- `tests/unit`: pure functions, the persistence layer and the composition root, with fakes,
  temporary folders and temporary database files (the `db_engine` and `units` fixtures in
  `tests/conftest.py`; temporary Alembic chains in `tests/migration_chains.py`).
- `tests/app`: the engine as a real process against temporary folders.
- `tests/contracts`: import contracts, the source tree, pins, the lock file and vendored assets.
- `tests/fakes`: one in-memory fake per port (`FrozenClock` for `Clock`, `InMemorySecrets` for
  `SecretsPort`).
- `tests/ai_sets`: synthetic AI test cases only.

## Layers and import contracts

`nextron/` is split into `domain` (stdlib only), `ports`, `db`, `app/<owner>` with
`app/queries/<owner>.py`, then `web`, `jobs` and `adapters`, with `main.py` as the composition
root. Each package docstring states what it may import. The `[tool.importlinter]` section of
`pyproject.toml` enforces the seven contract groups of AD-1, and `tests/contracts` runs them on
every test run.

Contract group 7 (each owner uses only its own repositories) is generated from
`nextron/db/owners.py`. After changing the owners, regenerate it:

```powershell
uv run python scripts/gen_owner_contracts.py          # rewrite pyproject.toml
uv run python scripts/gen_owner_contracts.py --check  # what the tests run
```

When you add an adapter under `nextron/adapters/`, add it to contract 5 and give its port a fake
in `tests/fakes`; a contract test fails until contract 5 lists it.
