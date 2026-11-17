---
title: 'Story 1.15: See engine health on one panel'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '1014eddc0c3b65dd562bfcbbc5662675765c051d'
review_loop_iteration: 0
story_key: '1-15-see-engine-health-on-one-panel'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Mac can't see whether the engine is healthy without a terminal.
- The heartbeat, each job type's last success and the dead jobs are in the database, and the doctor readiness list is in `main.py`, but no page shows any of them.
- A dead job stays forever, because nothing can dismiss it.
- The navigation's Health link has no page behind it.

So a stalled worker, a failing job type or a missing credential goes unnoticed until work stops.

Requirements: FR-58, AD-10, AD-20, OP-9, UX-DR5, UX-DR29.

**Approach:** An `ops` service `EngineHealth` assembles one report:
- the worker state from `runner_heartbeat`;
- each registered job type's last success, by group;
- the error list from a new frozen health-issue registry in `main.py`;
- the doctor readiness list, run in-process, read-only and with no network.

The dashboard serves it at `GET /health/panel` (the nav's `health_page`), and HTMX re-fetches it every 30 s. A Dismiss POST deletes a dead job. The notification area shows the error count.

The acceptance criteria are Story 1.15 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Domain** (stdlib and `nextron.domain` only).
  - **`domain/readiness.py`.** `Status` and `CheckResult` move here unchanged. `startup_checks` imports them, so `from nextron.startup_checks import CheckResult, Status` still works. It also holds `tally(results) -> (ok, warn, fail)`.
  - **`domain/health.py`.**
    - **The heartbeat rule.** `HEARTBEAT_FRESH = timedelta(minutes=2)`. `startup_checks.HEARTBEAT_FRESH_S` becomes `HEARTBEAT_FRESH.total_seconds()`, so the constant has one source. `worker_running(beat_at, now)` is `abs(now - beat_at) < HEARTBEAT_FRESH` (Story 1.14's P3 rule).
    - **`HealthIssue(kind, subject, text, detail=None)`**, frozen. Unless these hold it raises `TypeError` or `ValueError`, naming the field and never its value:
      - `kind` fullmatches `[a-z][a-z0-9_]{0,31}`;
      - `subject` is a ULID or fullmatches `[a-z0-9_:.-]{1,64}`;
      - `text` is a non-blank `str`;
      - `detail` is `None` or a `str`.
    - **`IssueSource(name, read)`**, frozen. `name` fullmatches `[a-z][a-z0-9_]{0,31}`, and `read() -> Iterable[HealthIssue]` is callable.
    - **`HealthIssues`** is the registry: `register_source` (a repeated name raises `ValueError`, and registering after `freeze()` raises `RuntimeError`), `freeze()`, `frozen` and `sources`.
    - **`job_group_runs(job_types, last) -> tuple[GroupRuns(group, runs: tuple[LastRun(job_type, succeeded_at | None), ...]), ...]`.** `job_types` is `(name, group)` pairs in registration order. Groups come in `JobGroup` order, and only those with a type.
  - **`domain/job.py`.**
    - **`JobGroup(StrEnum)`**, with these values and labels (`.label`):
      - `discovery` "Discovery";
      - `enrichment` "Enrichment";
      - `recheck` "Re-check";
      - `sending` "Sending";
      - `reply_check` "Reply check";
      - `unsubscribe_poll` "Unsubscribe Endpoint poll";
      - `backup` "Backup";
      - `other` "Other".
    - **`JobType` gains a last field `group: JobGroup = JobGroup.OTHER`.** A non-`JobGroup` raises `TypeError`.
  - **`domain/budget.py`.** `budget_issues(assessment) -> tuple[HealthIssue, ...]` holds `thresholds(assessment)` as issues (`kind`, `subject`, `text`), except that `ai_budget_warning` is dropped when `ai_budget_reached` is present. A `0` limit (`off`) is never an issue.
- **Persistence.** No migration.
  - **`db/repos/jobqueue/jobs.py`:**
    - `last_done() -> dict[str, datetime]` is `MAX(updated_at)` of the `done` rows per type;
    - `delete_dead(job_id) -> int` deletes the row only while it is `dead`.
  - **`db/migrations.py`.** `current_revision_in_unit()` reads the revision through `current().session`, with the same rules as `current_revision`. Both share one implementation.
- **Queries** (`app/queries/jobqueue.py`).
  - `last_successes()` returns `last_done()` as a read-only mapping.
  - `health_issues()` gives, for each dead job, oldest first, `HealthIssue("job_dead", id, job_dead_text(type, id, attempts), detail="Last error: " + redact(last_error))`. `detail` is `None` with no `last_error`.
- **jobqueue.** `@use_case JobQueue.dismiss(job_id) -> bool`. In one transaction it reads the job and calls `delete_dead`.
  - It returns `True` only when a `dead` row was deleted.
  - A missing job, or one that isn't `dead`, is `False`, and nothing changes.
  - After the commit it logs `job_dismissed` (INFO, the job fields).
  - It writes no `events` row.
- **Google** (`app/setup/api.py`). `GoogleAccess.check_stored_grant(grant) -> None` raises exactly as `access_token`'s checks do before any refresh: client not imported, not granted, needs re-consent, or a Credential Manager `AdapterError`. It never calls Google. `access_token` reuses the same helper, and its behaviour is unchanged.
- **`ops`** (`app/ops/api.py`).
  - **`Budget.health_issues()`** is `budget_issues(self.status())`.
  - **`EngineHealth(clock, job_types, *, issues: HealthIssues, readiness: Callable[[], Sequence[CheckResult]])`.** Its methods are plain reads, for a GET's read-only unit of work:
    - **`issues()`** runs each source in order. A source that raises, or yields a non-`HealthIssue`, gives one `HealthIssue("health_check_failed", <source name>, "The <name> health check could not be read (<ExceptionType>)")` and logs `health_check_failed` (ERROR, `source`, `code`, traceback). The other sources still run.
    - **`issue_count()`** is `len(issues())`.
    - **`report() -> HealthReport(checked_at, worker, groups, issues, readiness)`.** It reads the clock once and runs `readiness()` first, before any database read (decision 4). `worker` is `Worker(running, beat_at)` from `queries.ops.heartbeat()`; `beat_at` is `None` before the first beat.
- **Doctor in the engine** (`startup_checks.py`, `main.py`).
  - **`check_port_free(..., in_engine=False)`.** With `in_engine` it probes nothing and returns `ok` "<host>:<port> is held by this engine, which serves this page".
  - **`check_internal_notify_grant(access, *, refresh=True)`.** With `refresh=False`, `ok` reads "internal_notify is granted; the health panel doesn't refresh it (nextron doctor does)".
  - **`DoctorContext` gains `in_engine: bool = False`.** With it set, the `port_free` and `internal_notify_grant` registrations use the two forms above, through `c.google.check_stored_grant`.
  - **`EngineDatabase(path)`** (`main.py`) shares the `exists`/`revision`/`required_unset` shape with `DoctorDatabase` through a `ReadinessDatabase` Protocol:
    - `exists()` checks the file;
    - `revision()` is `current_revision_in_unit()`;
    - `required_unset()` is `queries.setup.required_unset()`, read in the caller's unit.
  - The ids and their order don't change. `doctor` is unchanged.
- **Composition** (`main.py`).
  - **`build_health_issues(*, budget)`** is the only code that constructs `HealthIssues` or calls `register_source`. It registers `IssueSource("jobqueue", queries.jobqueue.health_issues)`, then `IssueSource("budget", budget.health_issues)`, then freezes.
  - **`send_alert`** registers with `group=JobGroup.OTHER`.
  - **`build_app(..., probes=None)`** builds `EngineHealth(clock, job_types, issues=build_health_issues(budget=budget), readiness=functools.partial(run_checks, build_doctor_checks(), context))`.
    - `context` is `DoctorContext(config, ENV_SOURCE_VARIABLE, probes or Probes.system(), secrets, google, EngineDatabase(...), in_engine=True)`.
    - It passes `health=` and `queue=` to `create_app`.
    - Building still runs no check.
- **Web.**
  - **`GET /health/panel`** (`health_page`, `@personal_data(True)`) renders `health.html` (the layout and `{% include "health_panel.html" %}`).
  - **`GET /health/panel/live`** (`health_panel`, `@personal_data(True)`) renders only `health_panel.html`.
  - **`POST /health/jobs/dismiss`** (`health_job_dismiss`, `@personal_data(True)`).
    - Apart from `csrf_token`, the fields must be exactly one `job` holding a ULID; anything else is `MalformedForm`.
    - It calls `queue.dismiss` in its own unit of work, then renders `health_panel.html` in a read-only one.
  - **`health_panel.html`** is `<div id="health-panel" hx-get="/health/panel/live" hx-trigger="every 30s" hx-swap="outerHTML">`. In order:
    1. "Checked at <YYYY-MM-DD HH:MM:SS> UTC; refreshes every 30 seconds.";
    2. **Worker**: "Running (last beat <time> UTC)", "Not running: last beat <time> UTC" or "Not running: no heartbeat yet";
    3. **Errors needing attention (<n>)**: each issue's `text|plain` and `detail|plain`. A `job_dead` issue also gets a Dismiss form (`method="post"`, `hx-post="/health/jobs/dismiss"`, `hx-target="#health-panel"`, `hx-swap="outerHTML"`, `{{ csrf_field() }}`, hidden `job`). With none: "No errors need attention.";
    4. **Last successful runs**: one table per group (Job type, Last success), "never" when there is none;
    5. **Readiness (nextron doctor)**: a table (Status, Check, Message) and "<n> ok, <n> warn, <n> fail".
  - **The notification area.** `notifications.html` starts with `<p class="health-count"><a href="/health/panel">Engine health: <n> error(s) need(s) attention</a></p>` when `health.issue_count()` > 0. The area renders nothing when there are no notifications and no errors.
  - **The guard.** Its plain-text 401 for an HTMX `GET` adds `HX-Refresh: true`. HTMX POSTs are unchanged.
  - **`create_app`** gains `health: EngineHealth` and `queue: JobQueue`.
- **Tests** run as `test` with fakes: no keyring, no network, and no real wait over 0.05 s per step.

**Never:**
- **No new parts.** No migration, table, dependency, adapter, port, setting, credential or trigger.
- **No side effect from a GET.** It makes no Google refresh, no write and no usage record, and never calls a port inside a transaction.
- **Out of scope:**
  - inbox status, warm-up and Daily Cap;
  - the last backup and restore test (1.16 and 1.18);
  - `unknown` OM resolution;
  - recovery mode;
  - the other owners' issues (their stories);
  - Telegram for budget issues;
  - polling the notification area.
- **What not to edit.** Don't change the AD-2 read-only `BEGIN IMMEDIATE` (decision 4), `/health`'s JSON, the vendored HTMX, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **Dismiss deletes the dead job row.** AD-10 says "`dead` rows stay until the Operator dismisses them", alongside the 30-day `done` purge. The deletion is logged (`job_dismissed`), its history stays in `events`, and no event is written: it is not a Job-machine transition, like the purge.
   - The job's `job_dead` notification is separate and has its own Dismiss. AD-3's owner map lets no service call both jobqueue and notify for this.
   - The sweep never re-alerts the job, because the row is gone.
2. **The panel never refreshes the `internal_notify` grant.** A refresh would be a GET side effect: a Google call, an `api_usage` row, and possibly marking the grant for re-consent.
   - The panel checks only what is stored (client imported, grant present, not marked for re-consent), and says `nextron doctor` tests the refresh.
   - In the engine, `port_free` is `ok` without probing, because the engine serving the page holds the port.
3. **Job types are grouped through a new `JobType.group`.** `send_alert` is "Other", because the AC's groups have no alert group. A contract requires every registration to name its group.
4. **The 30 s poll keeps AD-2's `BEGIN IMMEDIATE` for reads.** The Story 1.5 amendment needs Mac.
   - The poll's cost stays small: one GET every 30 s while the page is open, holding the lock for a few short SELECTs.
   - The readiness checks that reach Credential Manager or the registry run before the first database read, so they never hold the lock.
5. **After the session is lost (an engine restart included),** the guard's HTMX `GET` 401 adds `HX-Refresh: true`. HTMX then reloads the page, the reload shows the locked page, and no stale health data stays on screen. HTMX POSTs keep the plain-text notice, so typed input isn't lost.
6. **The error count is the error list's length:** dead jobs, AI spend at the warning percentage (`ai_budget_alert_pct`, 80% by default) or more, quotas reached, and any health check that fails to read. The worker state is shown at the top of the panel and isn't counted. The count updates on each page view.
7. **Budget issues come from `Budget.health_issues()` (the `ops` service), not a `queries` module.** Usage is readable only through `UsageMeterPort` (Story 1.13: only `main` imports `db.metering`).
8. **The panel lives at `/health/panel`,** because `/health` keeps its Story 1.1 JSON. Both panel routes declare `personal_data(True)`, because `last_error` is `redact`-class free text, so every answer is `no-store`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Running | beat 119 s ago; 5 s in the future | "Running (last beat …)" | N/A |
| Stale | beat 120 s ago; 300 s in the future; no row | "Not running: last beat …"; "Not running: no heartbeat yet" | N/A |
| Last success | `send_alert` done at T1 and T2, one dead, one queued | Other: `send_alert` T2; no done row: "never" | N/A |
| Dead job | dead `send_alert`, 6 attempts, `last_error` holding an email | "Job dead: send_alert <ULID>, attempts: 6"; detail redacted; Dismiss form; area "Engine health: 1 error needs attention" | N/A |
| Dismiss | POST `job=<dead ULID>` | row gone; `job_dismissed`; panel re-rendered without it; next sweep alerts nothing | N/A |
| Dismiss no-op | queued, done or unknown ULID; twice | `False`, nothing changes; panel 200 | bad or extra field: 400 |
| Budget | AI $16 of $20; AI $21; Hunter at quota; Places Pro 0 | warning text; reached text only; Hunter reached text; nothing | N/A |
| Source crash | a source raises `RuntimeError` | "The <name> health check could not be read (RuntimeError)"; other issues listed | logged |
| Readiness | panel with no grant; grant stored; marked re-consent | eleven rows in order; `port_free` ok in-engine; grant fail / ok "doesn't refresh" / fail; no Google call, no `api_usage` row | a crashing check: its row fails |
| Poll | `GET /health/panel/live` | the fragment alone, with `every 30s`; `no-store` | N/A |
| Session lost | HTMX GET and POST, no session | GET: plain-text 401 + `HX-Refresh: true`; POST: 401 without it | N/A |

</frozen-after-approval>

## Code Map

- **The composition root** (`nextron-engine/nextron/main.py`).
  - **What's there:** `build_app` (315-369), `build_job_types` (384), `DoctorDatabase` (705-750), `DoctorContext` (753), `build_doctor_checks` (767) and `doctor` (820).
  - **What changes:** add docstring paragraphs for "Health" and the in-engine doctor. `main` may not import sqlalchemy (contract c4), so the session SQL goes in `db/migrations.py`.
- **The checks** (`nextron-engine/nextron/startup_checks.py`).
  - **What's there:** `Status`/`CheckResult` (140-155), `HEARTBEAT_FRESH_S` (109), `check_port_free` (384), `check_internal_notify_grant` (506) and `count` (699).
  - **Its import rule:** it may import only domain, ports and the stdlib, never `nextron.app`.
- **Google** (`nextron-engine/nextron/app/setup/api.py`). `access_token` (~line 700): the cache check, then `_client()`, the token read, the meta re-consent check, and `refresh`. Factor the middle three into a helper shared with `check_stored_grant`.
- **jobqueue.**
  - `nextron-engine/nextron/db/repos/jobqueue/jobs.py`: copy `purge_done` and `dead()`. Index `ix_jobs_type_status` (type, status, updated_at) covers `last_done`.
  - `app/jobqueue/api.py`: copy `purge_done` and the `_fields` logs, and update the module docstring.
  - `app/queries/jobqueue.py`: its import rule is domain plus its own repos.
- **ops** (`nextron-engine/nextron/app/ops/api.py`). `Budget.status()` is the plain-read pattern, and `DeadJobAlerts` the logging pattern. `queries.ops.heartbeat()` exists.
- **Migrations** (`nextron-engine/nextron/db/migrations.py:83-115`). `_stored_revision(connection)` uses `exec_driver_sql`. Generalise it to a `rows(sql)` callable.
  - The read-only connection allows `SELECT` only.
  - The unit's `Session` refuses `.connection`, so use `session.execute(text(sql))`.
- **Web.**
  - **`nextron-engine/nextron/web/app.py`:**
    - `create_app` (232);
    - `page()`;
    - `notification_list` (481);
    - `submitted_notification` (195), the pattern for `job`;
    - `NAV_SECTIONS` already holds `health_page`;
    - the module docstring's route list.
  - **`web/quotas_page.py`** is the pattern for a `health_page.py` context helper.
  - **`web/guard.py:171-176`** is the HTMX 401 branch.
  - **Templates:** `templates/notifications.html` and `static/app.css`.
  - **Vendored HTMX 2.0.11** processes `HX-Refresh` before `responseHandling` (checked in the source), so a 401 with it reloads the page.
- **Tests to update.**
  - **`tests/dashboard.py`:** build `EngineHealth(self.clock, self.job_types, issues=main.build_health_issues(budget=self.budget), readiness=...)`, with a `readiness` field that defaults to a fixed list. Pass `health` and `queue`.
  - **`tests/contracts/test_web_contracts.py`:**
    - the route paths (146-160): add `/health/panel`, `/health/panel/live` and `/health/jobs/dismiss`;
    - the GET and POST lists (267, 444);
    - the templates list (557): add `health.html` and `health_panel.html`.
  - **Nav pins, now Settings and Health:** `tests/unit/test_dashboard.py:726-745`, `test_credentials_page.py:343-346` and `test_settings_page.py:399`.
  - **`tests/contracts/test_job_contracts.py:274-298`:** `send_alert.group`.
  - **`tests/contracts/test_source_tree.py` `FILES`:** the new files.
- **Contract helpers.** `tests/contracts/test_job_contracts.py` has `Registry` and `registration_problems` (58-140). `test_doctor_contracts.py` is the pattern for a registry contract with a probe.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/readiness.py`, `domain/health.py`, `domain/job.py`, `domain/budget.py` -- the types, the heartbeat rule, the registry, `JobGroup`, `job_group_runs` and `budget_issues`.
- [x] `nextron-engine/nextron/startup_checks.py` -- import the moved types and `HEARTBEAT_FRESH`; `in_engine` and `refresh` forms; docstring.
- [x] `nextron-engine/nextron/db/migrations.py`, `db/repos/jobqueue/jobs.py`, `app/queries/jobqueue.py`, `app/jobqueue/api.py` -- `current_revision_in_unit`, `last_done`, `delete_dead`, the two queries and `dismiss`.
- [x] `nextron-engine/nextron/app/setup/api.py`, `app/ops/api.py` -- `check_stored_grant`; `Budget.health_issues`; `EngineHealth`.
- [x] `nextron-engine/nextron/main.py` -- `JobGroup` on `send_alert`, `in_engine`, `ReadinessDatabase`, `EngineDatabase`, `build_health_issues`, and the `build_app` wiring.
- [x] `nextron-engine/nextron/web/app.py`, `web/health_page.py`, `web/guard.py`, `templates/health.html`, `templates/health_panel.html`, `templates/notifications.html`, `static/app.css` -- the routes, the panel, the count and `HX-Refresh`.
- [x] `nextron-engine/tests/unit/test_health_domain.py`:
  - `worker_running` at 119, 120, -5 and -300 s;
  - every `HealthIssue` and `IssueSource` refusal;
  - the registry rules;
  - `job_group_runs` order;
  - the `JobGroup` labels and the `JobType.group` check;
  - `budget_issues` (the Budget row);
  - `tally`.
- [x] `nextron-engine/tests/app/test_engine_health.py` -- real units on a temporary database:
  - the Running, Stale and Last success rows;
  - `last_done` across types;
  - the Dead job issue (redacted detail);
  - the Source crash row;
  - `dismiss` (the Dismiss and Dismiss no-op rows, the sweep afterwards);
  - `current_revision_in_unit` at head inside a read-only unit;
  - `report()` running readiness before the first session use (a readiness callable that asserts `in_transaction()` is false).
- [x] `nextron-engine/tests/unit/test_health_page.py` -- through `Dashboard(units=units)`:
  - the page, the fragment and the exact `hx-*` attributes;
  - each section's text;
  - Dismiss and its refusals;
  - the area count (singular and plural, and absent at 0);
  - `no-store`;
  - the nav link;
  - the guard's `HX-Refresh` on a GET only;
  - `last_error` with `<script>` escaped.
- [x] `nextron-engine/tests/unit/test_startup_checks.py`, `tests/unit/test_google_access.py` -- the `in_engine` and `refresh=False` forms; `check_stored_grant` raising each code with no `FakeGoogleOAuth` call; `access_token` unchanged.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py` -- on the real `build_app` (injected secrets, fake probes and `FakeGoogleOAuth`), `GET /health/panel` shows the eleven readiness rows, `port_free` ok and the dead-job issue. No refresh is made, no `api_usage` row exists, and nothing is written to the database. The test fails when the wiring breaks.
- [x] `nextron-engine/tests/contracts/test_health_contracts.py` -- only `build_health_issues` builds or fills `HealthIssues` (with a probe proof); it ships frozen with exactly `("jobqueue", "budget")`; every `JobType(` in `build_job_types` passes `group=` (AST, with a probe); `HEARTBEAT_FRESH_S` derives from `HEARTBEAT_FRESH`.
- [x] The existing tests in the Code Map; `nextron-engine/README.md` -- an "Engine health" section: the panel, the poll, the errors, Dismiss, the in-engine readiness (decision 2) and the area count.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and every contract (29) is kept.
- Given `serve`, when it starts, then building the app runs no readiness check, and no `HealthIssues` registration exists outside `build_health_issues`.

## Implementation Notes

- `Worker` and `HealthReport` are frozen dataclasses in `app/ops/api.py`, beside `EngineHealth`. `EngineHealth` also refuses a non-`JobTypes`, a non-`HealthIssues` (`TypeError`) and an unfrozen registry (`RuntimeError`).
- `create_app` binds its `health` parameter to `engine_health` first: the `GET /health` handler's `def health()` rebinds the name inside `create_app`.
- `startup_checks.count` is gone; `format_table` counts through `tally`.
- The guard's `_respond` takes `extra` headers. `HX-Refresh` applies to every HTMX GET without a session, so the notification area's load and the Google grant row's 2 s poll reload to the locked page too.
- `tests/dashboard.py` exports `READINESS` (the eleven doctor ids, all `ok`), the `Dashboard.readiness` default.
- `test_runner_lifespan.py` opens the engine's own database (`main.open_database`) so `database_revision` reads `ok` through `EngineDatabase`, and proves the wiring with a broken-wiring parametrisation (`in_engine=False` probes the port and refreshes at Google).

Review fixes (pass 1, P1–P7; see the Review Triage Log):
- **P1.** The `ops` module docstring and `EngineHealth.report()` now say readiness runs before the report's own database reads, and that readiness ends with the two database checks (`required_settings`, `database_revision`), which begin the read transaction. `build_doctor_checks` states the order rule:
  - any check that reads Credential Manager, the registry or the network goes before `required_settings`;
  - a check that calls the network also needs an in-engine form (`c.in_engine`).

  The real-wiring test in `test_runner_lifespan.py` records `in_transaction()` at each `secrets.read` during the panel GET, and asserts that reads happened and none ran inside a transaction.
- **P2.** `startup_checks.count` is deleted.
- **P3.** `test_the_engine_database_fails_a_foreign_database_with_its_reason` (`tests/app/test_engine_health.py`) runs `EngineDatabase.revision()` on a WAL file with a table and no Alembic revision. It raises `CheckFailed` with the `DatabaseStateError` message, and `check_database_revision` gives `fail` with that message. The setup moved into a shared `foreign_database()` helper.
- **P4.** `domain/health.py` and the README now say "a beat 2 minutes or more in the future (the clock was set back) isn't fresh either".
- **P5 and P6.** Each Dismiss button has `id="dismiss-<job ULID>"`, so HTMX keeps keyboard focus across the 30-s swap, and `aria-label="Dismiss dead job <job ULID>"`. `test_health_page.py` asserts both.
- **P7.** `web/health_page.py` imports `CSRF_FIELD` from `nextron.web.csrf` instead of redefining it.

**Final verification (after pass 1).**
- `uv run pytest -q`: 3968 passed, 2 skipped (`ai_eval` and the symlink test), in 289 s.
- `uv run lint-imports`: 29 kept, 0 broken.
- Before the review fixes: 3967 passed, 2 skipped, and 29 kept.

Not verified here:
- the panel and its 30-s poll in a real browser (only the exact `hx-*` attributes and the answers are tested);
- the `HX-Refresh` reload after a real engine restart;
- the readiness list against the real Credential Manager and registry.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (18 findings), EC = edge-case hunter (10), VG = verification gap (1 gap and 1 other finding).
- **Counts:** 30 raw findings. BH5, BH7, BH10, BH12, BH15 and BH18 each make two separate claims, so there are 36 claims, which make 29 entries after deduplication.
- **Outcomes:**
  - 7 entries patched (P1–P7): 3 doc or wording fixes, 1 deletion, 2 accessibility attributes and 1 constant reuse, plus 2 tests (in P1 and P3);
  - none deferred;
  - 22 rejected: 4 as false and 1 as maybe-false.
- **No loopback.** No finding is an intent gap or a bad spec.
- **Rejected only because the fix would change the approved spec:** I (decision 4 and the AC's 30-s refresh), M (Story 1.12 decision 9), N (decision 6), O (the AC's dead-job fields and the frozen detail text) and Y (the AC's 80% error and decision 6).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC10 | `report()`'s docstring says readiness runs "before any database read". The real readiness ends with its own database checks. The Credential Manager and registry reads stay outside the lock only because of registration order, so a check appended later would run them under the lock, and no test would notice. | low | Verified. `required_settings` and `database_revision` are registered last in `build_doctor_checks`, so today no keyring or registry read happens under the lock. But `test_the_report_runs_readiness_before_its_first_database_read` uses a fake readiness, and later stories append checks (inbox grants, the Worker). The fix: correct the docstrings, state the order rule in `build_doctor_checks`, and assert in the real-wiring test that no secret read happens inside a transaction. | P1 |
| B | BH13, EC9, VG-other1 | `startup_checks.count()` is dead code, a second copy of `tally`'s rule. | low | Verified: nothing in `nextron/` or `tests/` calls it. The fix is a direct deletion. | P2 |
| C | VG1 (gap) | No test runs `EngineDatabase.revision()`'s conversion of `DatabaseStateError` to `CheckFailed`. Dropping it would turn the panel's reason into "could not be read (DatabaseStateError)" unseen. | low | Pre-verified. It is reachable only if the file changes under a running engine (startup refuses such a database), but the fix is one test. | P3 |
| D | BH11 | `domain/health.py` and the README say "a beat from the future isn't fresh", but a beat less than 2 minutes ahead counts as fresh. | low | Verified against `worker_running` and the -5 s and -119 s test rows. The doc fix is direct. | P4 |
| E | BH7 (part) | The 30-s `outerHTML` swap drops a keyboard user's focus on a Dismiss button. | low | Verified: HTMX 2.0.11 refocuses only an element with an `id` (`document.getElementById` after the swap), and the buttons had none. The fix is direct: an `id` per button. | P5 |
| F | BH7 (part) | Every button reads only "Dismiss", so a screen reader can't tell which job each one dismisses. | low | Verified in `health_panel.html`. The fix is direct: an `aria-label` naming the job. | P6 |
| G | BH12 (part) | `health_page.py` redefines `CSRF_FIELD_NAME` instead of importing `nextron.web.csrf.CSRF_FIELD`. | low | Verified. `credentials_page` and `google_page` import the constant, so a renamed field would diverge only here. The fix is direct. | P7 |
| H | BH12 (part) | `submitted_job` copies `submitted_notification`. | low | True, but each page owns its form parser (`credentials_page.submitted_save`, `google_page.submitted_path` and `submitted_grant`): that is the established pattern. A shared helper would refactor existing code. | reject |
| I | BH2 | All eleven checks run on every 30-s poll, reading every required secret from Credential Manager. | low | True by design. The AC refreshes the panel, readiness list included, every 30 s, and decision 4 accepted the cost: each value is read and dropped at once, as on the Credentials page. Showing readiness only once would change the approved spec. | reject |
| J | BH3, EC6 | `health_check_failed` logs an ERROR traceback on every read while a source stays broken. | low | True. A source stays broken only when a stored budget setting no longer parses (a hand edit; Story 1.13 entry P). Logging only on change needs per-source state. | reject |
| K | BH4, EC3 | The dead-job list is unbounded, `issue_count()` loads every row, and there is no bulk dismiss. | low | True, but dead jobs are few (only `send_alert` exists) and are read in milliseconds. A capped list with a count query, or a bulk dismiss, is new surface. Revisit when Epic 8's bulk job types arrive. | reject |
| L | BH5 (part) | A down engine leaves the last panel on screen, still saying "Running". | low | True: HTMX swaps nothing when a poll gets no answer (Story 1.12 decision 9), and the "Checked at" line shows the age. A visible marker needs a script asset, which is new surface. | reject |
| M | BH5 (part), EC7 | A poll's transient 4xx or 5xx text stays in `#request-error` after later polls succeed. | low | True, and specified by Story 1.12 decision 9 ("a notice stays until the page reloads"), already rejected there (its entry O). | reject |
| N | BH6 | After a Dismiss or a poll, the area's count disagrees with the panel until the page reloads. | low | True. Decision 6 says the count updates on each page view. An out-of-band swap needs a placeholder in the area on every page. | reject |
| O | BH8 | Dead-job issues show no time of death. | low | True. The AC lists type, ULID, attempts and the redacted `last_error`, and the frozen spec fixes the detail as "Last error: …". | reject |
| P | BH9 | The panel doesn't flag a type whose last success is overdue, or one that keeps retrying. | low | Beyond the AC, which asks for each type's last successful run time. A cadence check is a new feature. | reject |
| Q | BH10 (part) | `EngineDatabase.exists()` checks the configured path while `revision()` reads the injected units: two sources of truth. | false | `build_app`'s callers both open `config.data_dir / DB_FILE_NAME`: `serve` through `open_database(config)`, and the reload worker through `connect_database(config)`. Only tests inject other units. | reject |
| R | BH10 (part) | In the engine, `database_revision` could say "will migrate on next start" or "no database file yet". | false | Those need a database that is missing, empty or behind while the engine serves from it. The engine migrated it to head at startup and holds it open, and Windows refuses to delete an open file. | reject |
| S | BH14 | The eleven doctor ids are hard-coded in `tests/dashboard.py`, `test_runner_lifespan.py`, the README and the docstrings. | low | Deliberate pinning, as in Story 1.14 (its entry P): a story that adds a check updates them on purpose. `READINESS` is a fixed fake list. | reject |
| T | BH15 (part) | `test_the_panel_reads_in_one_read_only_unit_of_work` only compares job ids, so a write elsewhere would go unseen. | false | Every GET runs in a read-only unit, where any write raises, and the test's `get()` asserts 200. The real-wiring test also compares the whole database dump. | reject |
| U | BH15 (part) | The "no `api_usage` row" assertion can't see a refresh made through `FakeGoogleOAuth`. | false | The real branch also asserts `oauth.calls == []`, which does see a refresh (the doctor-form branch proves it). The row count guards against a metered client. | reject |
| V | BH16, EC1 | A failure in `heartbeat()` or `last_successes()` (the database locked past 5 s) makes the panel and its poll answer 500. | low | True, as for every dashboard GET under a 5-s lock. The poll's 500 shows as a notice, and the next poll retries. Fallbacks for each read add branches. Precedent: Story 1.13 entry P. | reject |
| W | BH17 | Only `job_dead` issues can offer Dismiss, so a later owner's issue can't. | low | The AC gives Dismiss to dead jobs only. A later story that needs another action adds it with its own route. | reject |
| X | EC5 | A source emitting kind `job_dead` with a non-ULID subject would show a Dismiss button whose POST is refused. | low | Only `queries.jobqueue.health_issues` emits `job_dead`, always with the job's ULID. The guard would cover a state never shown. | reject |
| Y | BH18 (part) | AI spend at 80% shows "Engine health: 1 error needs attention" on every page until the month ends, and nothing acknowledges it. | low | Specified: the AC lists AI spend at 80% or more as an error, and decision 6 counts it. | reject |
| Z | BH18 (part) | `readiness-warn` and `readiness-fail` rows look the same. | low | In the engine no check can be `warn`: the revision is at head, and the grant check makes no call. So the style never shows, and a warn colour would be a new design token. | reject |
| AA | EC2 | `head_revision()` and `known_revisions()` parse the Alembic scripts on every poll while the read lock is held. | low | Measured on this PC: about 5–6 ms per poll, every 30 s. Caching would add state for a negligible gain. | reject |
| AB | EC4 | A poll in flight during a Dismiss can overwrite the fresh panel, so the dismissed job reappears for up to 30 s. | maybe-false | It depends on whether HTMX 2.0.11 swaps a late response into a panel that has already been replaced, within a poll's few-ms window. Even if true it would be low: the dismissal is committed, and the next poll corrects the panel. | reject |
| AC | EC8 | A `JobType` built outside `build_job_types`'s body, then registered there, escapes the `group=` contract. | low | True, but every registration is inline, and contract tests guard against mistakes, not evasion (Story 1.10 EC11). A required field would break every test that builds a `JobType`. | reject |

## Design Notes

```python
def report(self) -> HealthReport:                 # ops.EngineHealth; a GET's read-only unit
    now = self._clock.now()
    readiness = tuple(self._readiness())          # keyring/registry checks, then its DB checks
    beat = ops_queries.heartbeat()                # the read transaction is already open here
    worker = Worker(beat is not None and worker_running(beat.beat_at, now),
                    None if beat is None else beat.beat_at)
    pairs = [(n, self._job_types.get(n).group) for n in self._job_types.names()]
    groups = job_group_runs(pairs, jobqueue_queries.last_successes())
    return HealthReport(now, worker, groups, self.issues(), readiness)
```

The doctor registry stays the single list. The engine binds it to an in-engine context in which only `port_free` and `internal_notify_grant` take their no-probe, no-network forms.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` and the symlink test skipped).
- `uv run lint-imports` -- expected: 29 contracts KEPT.
