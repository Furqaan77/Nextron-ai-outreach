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
curl.exe http://127.0.0.1:8766/health   # curl.exe: in PowerShell 5.1, curl is Invoke-WebRequest
```

`serve` validates `NEXTRON_ENV`, runs the startup checks, creates the data dir and its `logs`
folder, takes an exclusive lock on `<data dir>\nextron.lock`, checks the port, then runs Uvicorn
on `127.0.0.1` with one worker. `GET /health` returns `{"env", "version", "uptime_s"}` and
nothing else.

| Exit code | Meaning |
|---|---|
| 0 | stopped normally |
| 1 | unexpected error (a Python traceback) |
| 2 | `NEXTRON_ENV` unset or invalid, another invalid setting, a failed startup check (the message names it), a data dir that can't be created, or a server that failed to start |
| 3 | another instance is running for this environment |

The supervisor (`scripts/run.ps1`, a later story) restarts the engine on any exit except 3.

## Test

```powershell
uv run pytest               # the unit, app and contracts suites; ai_eval is skipped
uv run pytest -m ai_eval    # the opt-in AI evaluation suite
uv run lint-imports         # the import contracts on their own
```

- `tests/unit`: pure functions and the composition root, with fakes and temporary folders.
- `tests/app`: the engine as a real process against temporary folders.
- `tests/contracts`: import contracts, the source tree, pins, the lock file and vendored assets.
- `tests/fakes`: one in-memory fake per port (`FrozenClock` for `Clock` so far).
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
