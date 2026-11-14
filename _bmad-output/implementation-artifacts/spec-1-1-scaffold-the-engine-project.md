---
title: 'Story 1.1: Scaffold the engine project with layer contracts and a localhost health page'
type: 'feature'
created: '2026-11-15'
status: 'draft'
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
- No `ulid-py`, `aiosqlite` or `pytz` in the lock.
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
- [ ] `nextron-engine/pyproject.toml`, `uv.lock`, `.python-version` -- project metadata; exact pins from the spine Stack table; `[project.scripts] nextron`; pytest config registering the `ai_eval` marker (skipped unless selected) and default suites `unit`, `app`, `contracts`; the `[tool.importlinter]` section -- one source of truth for pins and contracts.
- [ ] `nextron-engine/nextron/{__init__,__main__,main}.py`, `domain/`, `ports/` (incl. `clock.py`), `db/{__init__,owners}.py`, `app/<owner>/{__init__,api}.py`, `app/queries/<owner>.py`, `adapters/`, `adapters/ai/`, `jobs/`, `web/` (+ `static/`, `templates/`), `seed/`, `workers/unsubscribe/.gitkeep`, `scripts/` -- skeleton per the spine; package docstrings name each layer's import rule.
- [ ] `nextron-engine/nextron/bootstrap.py` -- pydantic-settings `BootstrapConfig`, env validation, per-env defaults -- AD-14.
- [ ] `nextron-engine/nextron/startup_checks.py` -- pure checks (SQLite version, ZoneInfo, OneDrive, port free) over an injectable `Probes` -- AD-2.
- [ ] `nextron-engine/nextron/lock.py` -- exclusive OS lock (msvcrt on Windows, fcntl elsewhere) on `nextron.lock` -- single instance.
- [ ] `nextron-engine/nextron/web/app.py` -- FastAPI app factory with `GET /health` -- FR-58 seed.
- [ ] `nextron-engine/nextron/main.py` + `__main__.py` -- CLI `serve` / `doctor`; ordered exits: env → checks → lock → serve; exit codes 2 and 3.
- [ ] `nextron-engine/scripts/gen_owner_contracts.py` -- regenerates contract 7 from `owners.py` into `pyproject.toml`; supports `--check`.
- [ ] `nextron-engine/nextron/web/static/htmx-2.0.11.min.js` + `tests/contracts/test_vendored_assets.py` -- vendored HTMX with its SHA-256 pinned in the test.
- [ ] `nextron-engine/tests/fakes/clock.py` -- `FrozenClock` implementing `ports.clock.Clock`.
- [ ] `nextron-engine/tests/contracts/` -- run `lint-imports`; check contract 7 is in sync; a live-proof test (temp `import anthropic` in `nextron.domain` makes lint fail); a lock-file ban test for `ulid-py` and `aiosqlite`.
- [ ] `nextron-engine/tests/unit/` -- one test per I/O-matrix row, using fakes and a temp data dir.
- [ ] `nextron-engine/README.md` -- install, `serve`, `doctor`, tests.

**Acceptance Criteria:**
- Given a clean folder, when `uv sync --frozen` then `uv run pytest` run, then all suites pass and `lint-imports` reports 7 contract groups kept.
- Given the tree, when inspected, then it matches the spine source tree, and each owner has `app/<owner>/api.py` and `app/queries/<owner>.py`.
- Given the forbidden contract for automation and time libraries, when any `nextron` module imports `pytz`, `dateutil.tz`, `linkedin_api`, `instaloader`, `instagrapi` or `selenium`, then `lint-imports` fails.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

Contracts 1–7 are written exactly as in AD-1. Contract 5 lists every adapter subpackage except `ai`. Contract 6 is a `layers` contract over `nextron.app` with containers. Because the owner packages are empty stubs now, the contracts pass trivially, but they must still be declared so that later stories inherit them. The live-proof test writes a temporary module, runs `lint-imports`, and removes the module in a `finally` block.

## Verification

**Commands:**
- `cd nextron-engine && uv sync --frozen && uv run pytest` -- expected: all pass; `ai_eval` skipped.
- `uv run lint-imports` -- expected: every contract KEPT.
- `NEXTRON_ENV=dev uv run python -m nextron doctor` -- expected: all PASS, exit 0.
- `NEXTRON_ENV=dev uv run python -m nextron serve`, then `curl http://127.0.0.1:8766/health` -- expected: 200 JSON.
