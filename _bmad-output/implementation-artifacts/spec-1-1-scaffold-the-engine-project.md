---
title: 'Story 1.1: Scaffold the engine project with layer contracts and a localhost health page'
type: 'feature'
created: '2026-11-15'
status: 'done'
baseline_commit: '192ab0168d8bc3573b19fb5ddd846aef60ddb771'
route: 'dispatch'
review_loop_iteration: 0
story_key: '1-1-scaffold-the-engine-project-with-layer-contracts-and-a-local'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** No code exists. Every later story needs one enforced package layout, pinned dependencies, startup safety checks, and one process per environment, or parallel builders will diverge.

**Approach:** Create the uv project `nextron-engine/` at the repo root, following the architecture spine's source tree (AD-1, AD-2, AD-14, Stack). Enforce the layers with import-linter contracts run as tests. Add a `python -m nextron` CLI with `serve` and `doctor`, a fail-fast startup check, a per-environment lock, and `GET /health` on 127.0.0.1. Acceptance criteria are Story 1.1 in `_bmad-output/planning-artifacts/epics.md`, verbatim.

## Boundaries & Constraints

**Always:**
- Python `>=3.14.8,<3.15` via uv, with every Stack-table package pinned exactly in `pyproject.toml` and `uv.lock` (pydantic-settings `~=2.16`; import-linter 2.16 and pytest 9.2.0 as dev deps).
- `domain` is stdlib-only.
- Startup checks are pure functions over injected probes, so tests use fakes.
- Bootstrap config reads only `NEXTRON_ENV`, data dir, backup dir, port and Playbook path. Defaults:
  - data dir: `%LOCALAPPDATA%\Nextron\<env>\`
  - backups: `%LOCALAPPDATA%\Nextron\backups\<env>\`
  - ports: 8765 prod, 8766 dev, 8767 test
  - Playbook: `../docs/playbook/sales-playbook.md`
- Uvicorn runs on `127.0.0.1` only with `workers=1`; `--reload` in `dev` only.
- `/health` carries no personal data.

**Never:**
- No database, migrations, settings table, logging framework, auth, CSRF or jobs. Those belong to Stories 1.2–1.11.
- No domain tables and no business logic.
- No network calls at startup.
- No `ulid-py` or `aiosqlite` in the lock. `pytz` may appear only as trafilatura's indirect dependency (trafilatura → htmldate → dateparser), and `nextron` code never imports it. Mac decided this on 2026-11-15, renegotiating the earlier "no pytz in the lock".
- Don't modify anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Env unset or invalid | `NEXTRON_ENV` missing or `staging` | stderr "NEXTRON_ENV must be set to dev, test or prod" | exit 2; creates no file or directory |
| Happy dev start | `NEXTRON_ENV=dev`, all checks pass | data dir + `logs\` exist; exclusive lock on `<data dir>\nextron.lock`; listens on 127.0.0.1:8766; `GET /health` → 200 `{env, version, uptime_s}` | N/A |
| Second instance | lock held for the same env | stderr "another Nextron instance is running for dev" | exit 3 before binding a port |
| Old SQLite | `sqlite_version` < 3.51.3 | message names the check | exit 2 |
| No tz data | `ZoneInfo("Europe/London")` raises | message names the check | exit 2 |
| OneDrive path | data dir under `%OneDrive%`, `%OneDriveCommercial%`, `%OneDriveConsumer%`, or a path segment starting with "OneDrive" | message names the check | exit 2 |
| Port busy | port already bound by another process | message names the check | exit 2 |
| Doctor | `python -m nextron doctor` | prints each check as PASS or FAIL; takes no lock; starts no server | exit 0 if no FAIL, else 1 |

</frozen-after-approval>

## Code Map

- `nextron-engine/` -- new; nothing exists yet. Repo root has `docs/`, `_bmad/`, `_bmad-output/`, `.claude/`, none of which change.
- The spine's Structural Seed (source tree, owner map) and AD-1 (the seven contract groups) are authoritative for layout and contracts.
- Owners for `app/` and `owners.py`: setup, discovery, research, scoring, sequencing, drafting, approval, sending, replies, pipeline, compliance, playbook, notify, jobqueue, ops, analytics.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/pyproject.toml`, `uv.lock`, `.python-version` -- project metadata; exact pins from the spine Stack table; `[project.scripts] nextron`; pytest config registering the `ai_eval` marker (skipped unless selected) and default suites `unit`, `app`, `contracts`; the `[tool.importlinter]` section -- one source of truth for pins and contracts.
- [x] `nextron-engine/nextron/{__init__,__main__,main}.py`, `domain/`, `ports/` (incl. `clock.py`), `db/{__init__,owners}.py`, `app/<owner>/{__init__,api}.py`, `app/queries/<owner>.py`, `adapters/`, `adapters/ai/`, `jobs/`, `web/` (+ `static/`, `templates/`), `seed/`, `workers/unsubscribe/.gitkeep`, `scripts/` -- skeleton per the spine; package docstrings name each layer's import rule.
- [x] `nextron-engine/nextron/bootstrap.py` -- pydantic-settings `BootstrapConfig`, env validation, per-env defaults -- AD-14.
- [x] `nextron-engine/nextron/startup_checks.py` -- pure checks (SQLite version, ZoneInfo, OneDrive, port free) over an injectable `Probes` -- AD-2.
- [x] `nextron-engine/nextron/lock.py` -- exclusive OS lock (msvcrt on Windows, fcntl elsewhere) on `nextron.lock` -- single instance.
- [x] `nextron-engine/nextron/web/app.py` -- FastAPI app factory with `GET /health` -- FR-58 seed.
- [x] `nextron-engine/nextron/main.py` + `__main__.py` -- CLI `serve` / `doctor`; ordered exits: env → checks → lock → serve; exit codes 2 and 3.
- [x] `nextron-engine/scripts/gen_owner_contracts.py` -- regenerates contract 7 from `owners.py` into `pyproject.toml`; supports `--check`.
- [x] `nextron-engine/nextron/web/static/htmx-2.0.11.min.js` + `tests/contracts/test_vendored_assets.py` -- vendored HTMX with its SHA-256 pinned in the test.
- [x] `nextron-engine/tests/fakes/clock.py` -- `FrozenClock` implementing `ports.clock.Clock`.
- [x] `nextron-engine/tests/contracts/` -- run `lint-imports`; check contract 7 is in sync; a live-proof test (temp `import anthropic` in `nextron.domain` makes lint fail); a lock-file ban test for `ulid-py` and `aiosqlite`.
- [x] `nextron-engine/tests/unit/` -- one test per I/O-matrix row, using fakes and a temp data dir.
- [x] `nextron-engine/README.md` -- install, `serve`, `doctor`, tests.

**Acceptance Criteria:**
- Given a clean folder, when `uv sync --frozen` then `uv run pytest` run, then all suites pass and `lint-imports` reports 7 contract groups kept.
- Given the tree, when inspected, then it matches the spine source tree, and each owner has `app/<owner>/api.py` and `app/queries/<owner>.py`.
- Given the forbidden contract for automation and time libraries, when any `nextron` module imports `pytz`, `dateutil.tz`, `linkedin_api`, `instaloader`, `instagrapi` or `selenium`, then `lint-imports` fails.

## Implementation Notes

- **Conflict to resolve: `pytz` is in `uv.lock`.** The exact pin `trafilatura==2.3.1` pulls `trafilatura → htmldate 1.11.0 → dateparser 1.5.0 → pytz 2026.6`. "Every Stack package pinned exactly" and "no `pytz` in the lock" can't both hold. Removing pytz with a uv override would break dateparser at runtime, and htmldate falls back to it during extensive date search, so the crawler stories would inherit a latent failure. What is built instead: `pytz` is banned from every `nextron` module by import contract (group 4); `test_lockfile.py` bans `ulid-py` and `aiosqlite` from the lock, as the epics AC states; and `test_pytz_arrives_only_through_dateparser` fails if pytz becomes a direct dependency or gains another dependent. A human should choose: accept pytz as a transitive dependency, or drop or replace trafilatura.
- **`dateutil.tz` is banned as `dateutil`.** import-linter 2.16 rejects subpackages of external packages as forbidden modules ("subpackages of external packages are not valid"), and grimp records `import dateutil.tz` as an import of `dateutil`. The ban contract therefore forbids all of `dateutil`, which is stricter. Live-proof tests show `import dateutil.tz`, `from dateutil import tz` and `from dateutil.tz import gettz` all break lint.
- **The port check runs after the lock.** A second instance of the same environment finds its port held by the first. If the port check ran before the lock, that instance would exit 2 instead of 3, and the supervisor would restart it forever. `serve` runs: env → SQLite, tzdata and OneDrive checks (before any folder exists) → create the data dir and `logs` → lock (exit 3) → port check (exit 2) → Uvicorn. The unit test makes the port look busy and asserts exit 3 with no port probe.
- **`doctor` with an invalid `NEXTRON_ENV` exits 2** with the same message as `serve`, because it can't know which data dir and port to check. Story 1.14 owns the full doctor. While an engine runs, `doctor` reports `port_free` as FAIL.
- **Contract layout.** There are 28 contracts in the 7 AD-1 groups; ids start `c<group>-`. Group 1 adds `c1-composition-root-helpers`: no layer below `main` imports `bootstrap`, `lock` or `startup_checks`. Group 4 has six contracts: SDKs only in adapters; the time and automation ban over every `nextron` module; and AD-1's third-party placement (`sqlalchemy`/`alembic` only in `db`, `apscheduler` only in `jobs`, `pydantic_settings` only in `main`/`bootstrap`, `fastapi`/`starlette`/`uvicorn` only in `web`/`main`). The rule that a service reaches another owner only through its `api` module is documented as a convention, not yet enforced. Contracts 4 and 5 use `allow_indirect_imports = true`, because the composition root reaches SDKs through the adapters it wires; contracts 1 and 2 stop every other path to adapters. No non-AI adapter exists yet, so contract 5's sources are the non-adapter packages. `test_contract_coverage.py` fails when a new top-level module or adapter subpackage is missing from contracts 4 and 5.
- **`nextron/db/repos/<owner>/` stub packages were added** (they are in the spine tree). import-linter requires internal forbidden modules to exist, so contract 7 can't be declared without them.
- **No `httpx` dependency.** Starlette's `TestClient` needs `httpx`, which is not in the Stack, so the unit tests drive the ASGI app directly. `tests/app/test_serve_process.py` runs `python -m nextron serve` as a real process: `/health`, the second instance's exit 3, and an invalid env creating nothing.
- **HTMX provenance.** The file is `dist/htmx.min.js` from the npm tarball `htmx.org-2.0.11.tgz`. The tarball's sha512 matched the registry integrity, and jsDelivr serves the same bytes. `nextron-engine/.gitattributes` marks `nextron/web/static/**` `-text`, because this repo has `core.autocrlf=true`, which would otherwise change the pinned SHA-256 on checkout.
- The port probe binds with `SO_EXCLUSIVEADDRUSE` on Windows, so it detects a holder that set `SO_REUSEADDR` (Uvicorn does). It does not report a port as busy because of `TIME_WAIT` connections left by a previous run.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-15).** Sources: BH = blind hunter, EC = edge-case hunter, VG = verification gap. Grouped as patches P1–P15 and deferrals D1–D2.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH, EC | Uvicorn exits 3 on bind/lifespan failure, which collides with exit 3 "lock held" | high | `uvicorn/config.py:81 STARTUP_FAILURE = 3`, and `sys.exit(STARTUP_FAILURE)` at config.py:437/518/563/579/606 and main.py:618. `serve()` doesn't catch SystemExit, so the supervisor reads "another instance" and stops restarting. | P1 |
| 2 | BH, EC | mkdir of data dir/logs and the lock's `os.open` raise OSError uncaught: traceback, exit 1 | medium | main.py:112-113 and lock.py:58 sit outside any try. A file at the data-dir path gives exit 1, which the supervisor loops on. | P2 |
| 3 | BH, EC | lock.py maps every OSError to LockHeldError (exit 3) | medium | lock.py:59-63. ENOLCK/EBADF are reported as "another instance", and the supervisor never restarts. | P3 |
| 4 | BH, VG, EC | Docs say doctor exits 0/1, but it exits 2 on a bad NEXTRON_ENV; the serve table omits 1 | low | README:46-47, `__main__.py:7` and main.py:14/135 contradict `test_doctor_with_env_unset_exits_2`. Direct doc fix. | P4 |
| 5 | BH | README `curl` is Invoke-WebRequest in PowerShell 5.1 | low | The documented command fails on the target shell. Direct doc fix (`curl.exe`). | P4 |
| 6 | BH, VG, EC | Composition-root helpers (bootstrap, lock, startup_checks) aren't fenced by any contract | medium | VG probe: web/app/jobs importing them leaves 23 contracts kept. AD-14 bypass with lint green. | P5 |
| 7 | BH | AD-1 third-party placement (sqlalchemy/alembic→db, apscheduler→jobs, pydantic_settings→main, fastapi/uvicorn→web) not enforced; ports has no stdlib-only test | medium | `pyproject.toml` has no such contracts (grep). AD-1 Rule states the placement, so web could query tables directly and bypass contract 7. | P6 |
| 8 | BH, VG, EC | "Only the owner's `api`" and web/jobs-no-ports rules are documented as enforced but aren't | medium | VG probe: 23 kept with `app/setup/service.py` imported cross-owner. AD-1's contract types can't express "api submodule only", which needs an architecture amendment. Docstrings overclaim. | P7 (reword), D1 |
| 9 | BH | Contract tests write probe modules into the real source tree | medium | A killed pytest leaves a stray module that breaks lint/tree tests; `--reload` restarts on each probe. | P8 |
| 10 | BH | The "OS drops the lock after a crash" claim is untested | low | The test holder releases cleanly. A direct test addition. | P9 |
| 11 | BH | Clean stop (exit 0) untested; Windows stop mechanism undefined | low | `terminate()` is a hard kill on Windows. The stop protocol belongs to the supervisor (Story 1.21). | D2 |
| 12 | VG | Reload branch port not asserted | medium | VG mutation: deleting `port=` keeps the suite green, so `serve --reload` would start on 8000. | P10 |
| 13 | VG | Reload factory string never resolved or called | medium | VG mutation: renaming the factory keeps the suite green, so the dev reload worker dies. | P10 |
| 14 | VG | `Probes.system()` OneDrive/port wiring unverified | medium | VG mutation: `environ={}` and always-free port gives 162 passed, so prod could disable both checks. | P11 |
| 15 | VG | The `nextron` console script is never run | medium | VG: a broken entry point leaves the suite green, and the README's primary commands break. | P12 |
| 16 | BH, EC | `%VAR%` and `~` in path settings aren't expanded, so a literal folder is created in the repo | low | bootstrap.py:79 only normpaths. A plausible misconfiguration with a direct one-line fix. | P13 |
| 17 | EC | The contract-coverage test misses namespace packages (no `__init__`) | low | test_contract_coverage.py:18. A future adapter subpackage could escape contracts 4/5. Direct fix. | P14 |
| 18 | EC | The test's `write_text` uses locale encoding on Windows | low | test_owner_contracts.py:64 against the generator's utf-8 read. Direct fix. | P15 |
| 19 | BH | doctor reports the running engine's port as FAIL | low | It happens only when doctor runs while serving, and doctor is the pre-start check. The fix needs a lock probe, which the matrix forbids ("takes no lock"). Story 1.14 owns doctor. | reject |
| 20 | BH | doctor doesn't print resolved bootstrap values or check playbook/backup dirs | low | Story 1.14 ("Check install readiness with nextron doctor") owns the full check list. This adds checks, not a correction. | reject |
| 21 | BH | uv.lock and htmx missing from the diff | false | Both exist on disk and pass `uv sync --frozen` plus the SHA test. They were excluded from the review diff on purpose as generated/vendored files. | reject |
| 22 | BH | `.python-version` 3.14 vs requires-python >=3.14.8 | low | Only uv-managed 3.14.8 exists on this single PC. Unlikely to bite, and pinning a patch has its own churn. | reject |
| 23 | BH | noqa/type-ignore markers without ruff/mypy configured | low | Cosmetic, with no user or developer harm today. | reject |
| 24 | BH | The lock file doesn't record PID/env | low | An enhancement. The exit-3 message already names the env. | reject |
| 25 | BH | lint-imports runs 11 times per pytest | low | The whole suite takes 11 s. Negligible. | reject |
| 26 | BH, EC | Junction/symlink into OneDrive passes the check | low | Rare on this PC. The fix needs realpath of the nearest ancestor (new complexity). | reject |
| 27 | BH | backup_dir may equal or sit inside data_dir | low | Requires a deliberate odd override. Backups are Story 1.16, which can validate placement. | reject |
| 28 | BH, EC | Two environments overridden onto one data dir give a misleading exit 3 | low | Requires setting NEXTRON_DATA_DIR user-wide for every env. Unlikely, and the fix needs marker files. | reject |
| 29 | EC | LOCALAPPDATA and home unset: Path.home() raises | low | Doesn't occur in a Windows user session. | reject |
| 30 | EC | A drive-relative `C:data` resolves against the cwd | low | Unlikely input; the fix adds validation branches. | reject |
| 31 | EC | A non-editable install moves ENGINE_ROOT into site-packages | low | The engine runs from source via uv (editable). Unlikely. | reject |
| 32 | EC | POSIX TIME_WAIT probe lacks SO_REUSEADDR | low | The target is Windows (SO_EXCLUSIVEADDRUSE path). It doesn't occur on the target. | reject |
| 33 | EC | `python -OO` makes `__doc__` None in the generator | low | The generator never runs under -OO. Negligible. | reject |
| 34 | EC | Lock is per data dir, not per env, so two prod engines could run with distinct overrides | low | Requires deliberate overrides of both data dir and port. Unlikely. | reject |
| 35 | EC | A busy-port exit 2 still creates the data dir, logs and lock | false | The matrix requires "no file or directory" only for an invalid env. The busy-port row requires exit 2 naming the check, which holds. Lock-before-port order is deliberate so exit 3 stays correct. | reject |
| 36 | EC | Dynamic `importlib` imports bypass lint | low | An inherent limit of static analysis. No practical fix in scope. | reject |
| 37 | BH | Ports layer is documented stdlib-only but untested | low | Folded into finding 7's fix (extend the stdlib-only test to ports). | P6 |

## Design Notes

Contracts 1–7 are written exactly as in AD-1. Contract 5 lists every adapter subpackage except `ai`. Contract 6 is a `layers` contract over `nextron.app` with containers. Because the owner packages are empty stubs now, the contracts pass trivially, but they must still be declared so that later stories inherit them. The live-proof test writes a temporary module, runs `lint-imports`, and removes the module in a `finally` block.

## Verification

**Commands:**
- `cd nextron-engine && uv sync --frozen && uv run pytest` -- expected: all pass; `ai_eval` skipped.
- `uv run lint-imports` -- expected: every contract KEPT.
- `NEXTRON_ENV=dev uv run python -m nextron doctor` -- expected: all PASS, exit 0.
- `NEXTRON_ENV=dev uv run python -m nextron serve`, then `curl http://127.0.0.1:8766/health` -- expected: 200 JSON.
