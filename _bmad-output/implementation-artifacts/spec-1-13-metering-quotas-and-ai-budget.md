---
title: 'Story 1.13: Meter every external call and stop non-essential work at quotas and the AI budget'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: 'be9dea60732fe678199d2cf159e0fc27009c4d03'
review_loop_iteration: 0
story_key: '1-13-meter-every-external-call-and-stop-non-essential-work-at-quo'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing counts what the engine spends. Telegram and Google OAuth calls go unrecorded, there is no `api_usage` or `ai_usage` table, no quota or AI-budget setting, no `domain.budget.allow`, and a handler can't say "not now" without failing its job. Discovery and enrichment (Epics 2 and 8) would therefore run past free tiers and the $20 AI budget, and nobody would see it.

Requirements: FR-57, AD-8, AD-9, NFR-10, NFR-11, NFR-12, UX-DR28.

**Approach:**
- **Metering.** `UsageMeterPort` (`record`, `snapshot`) is implemented by `nextron/db/metering.py`, which is the only writer of `api_usage` and `ai_usage` (migration `0006_1_13_metering`). Each record commits in its own unit of work. The Telegram and Google OAuth adapters record every request they attempt.
- **Budget rules.** `nextron/domain/budget.py` holds the call records, the quotas, `allow(budget_class, usage, limits)`, the assessment and the threshold notices. Seven settings carry the limits.
- **Deferral.** A handler may return `DEFERRED`. The job goes back to `queued` an hour later and gets its attempt back.
- **Notices.** After each record, the `ops` service `Budget` raises one dashboard notification per threshold, quota and month.
- **Panel.** A Quotas and cost page at `GET /quotas`.

The acceptance criteria are Story 1.13 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise. The migration id is `0006_1_13_metering`, because revision ids allow no hyphens.

## Boundaries & Constraints

**Always:**

- **Domain** (`nextron/domain/budget.py`, stdlib and `nextron.domain` only; it doesn't import `settings_schema`).
  - **Budget classes.** `BudgetClass(StrEnum)`: `blockable`, `essential`.
  - **`ApiCall(provider, operation, *, ok, error_code=None, sku=None, count=1)`**, frozen. `__post_init__` raises `TypeError` or `ValueError`, naming the field and never its value, unless:
    - `provider`, `operation` and a non-`None` `sku` fullmatch `[a-z][a-z0-9_]{0,31}`;
    - `ok` is a `bool`;
    - `error_code` is `None` when `ok` is true, and otherwise fullmatches `[a-z][a-z0-9_]{0,63}`;
    - `count` is an `int`, not a `bool`, from 0 to 1,000,000.

    Its `units` property is `count × UNIT_WEIGHTS.get((provider, operation), 1)`. `UNIT_WEIGHTS` holds Hunter's doubled credits: `("hunter", "find")` 2, `("hunter", "domain_search")` 2 and `("hunter", "verify")` 1.
  - **`AiCall(route, route_tuple, served_model, *, input_tokens, output_tokens, cache_read_tokens=0, cache_write_tokens=0, iterations=1, batch=False, cost_micros, refused=False, error_code=None)`**, frozen. It is checked the same way:
    - `route` fullmatches `[a-z][a-z0-9_]{0,63}`;
    - `route_tuple` and `served_model` fullmatch `[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}`;
    - the four token counts, `iterations` and `cost_micros` are `int`s (not `bool`s) from 0 to 10^12;
    - `batch` and `refused` are `bool`s;
    - `error_code` is `None` or a code.

    No field can hold a space, so no free text or address fits.
  - **Months.** `month_of(now) -> "YYYY-MM"` and `month_bounds(now) -> (start, end)` take the UTC calendar month of an aware `now`. A naive `now` raises `ValueError`.
  - **Usage.** `ApiUse(provider, operation, sku, calls, failed, units)` is one line of a month. `Usage(month, api, ai_calls, ai_cost_micros)` holds the lines sorted by (provider, operation, sku). `usage.units(provider, sku=None)` sums the units of the provider's lines, or of only the lines with that SKU when `sku` is given.
  - **Quotas.** `QuotaSpec(name, label, provider, sku, setting, scale, unit, stops)` and `QUOTAS`, in this order:

    | name | label | provider | sku | setting | scale | unit | stops |
    |---|---|---|---|---|---|---|---|
    | `hunter` | Hunter | `hunter` | — | `quota_hunter_credits` | 2 | credits | enrichment |
    | `prospeo` | Prospeo | `prospeo` | — | `quota_prospeo_credits` | 1 | credits | enrichment |
    | `places_enterprise` | Places Enterprise | `places` | `enterprise` | `quota_places_enterprise` | 1 | requests | discovery |
    | `places_pro` | Places Pro | `places` | `pro` | `quota_places_pro` | 1 | requests | discovery |
    | `job_aggregator` | Job aggregator | `job_aggregator` | — | `quota_job_aggregator` | 1 | requests | discovery |

  - **Limits.** `Limits(quotas, ai_budget_micros, ai_alert_pct)` stores each quota in stored units: the setting value × `scale`.
  - **`allow(budget_class, usage, limits, *, quota=None) -> bool`.**
    - `essential` is always `True`.
    - `blockable` is `False` when the AI spend is at least the budget, or when `quota` is named and its usage is at least its limit. A limit of 0 is therefore always refused.
    - An unknown quota name raises `UnknownQuota` (a `DomainError`, code `unknown_quota`).
  - **`assess(usage, limits) -> Assessment(month, ai, quotas, api, stopped)`.**
    - Each `QuotaLine(spec, used, limit, state)` is in one of three states:
      - `off` when the limit is 0;
      - `reached` when used ≥ limit;
      - `ok` otherwise.
    - The `AiLine(spent, budget, alert_pct, calls, state)` is checked in this order:
      - `off` when the budget is 0;
      - `reached` when spent ≥ budget;
      - `warning` when spent × 100 ≥ budget × pct;
      - `ok` otherwise.
    - `stopped` lists "AI budget reached" first, then "<label> quota reached" for each `reached` quota.
  - **`thresholds(assessment) -> tuple[Threshold(kind, subject, text), ...]`.** The notices, with `<month>` the assessment's month:

    | When | kind | subject | text |
    |---|---|---|---|
    | AI `warning` or `reached` | `ai_budget_warning` | `ai_budget:<month>` | `AI spend at <pct>% of <budget>: <spent> spent in <month>` |
    | AI `reached` | `ai_budget_reached` | `ai_budget:<month>` | `AI budget reached: discovery, enrichment and re-checks stopped (<spent> of <budget> spent in <month>)` |
    | a quota `reached` | `quota_reached` | `<quota name>:<month>` | `<label> quota reached: <stops> stopped (<used> of <limit> <unit> used in <month>)` |

  - **Formatting.**
    - `dollars(micros)` gives `$20` for whole dollars, and otherwise dollars and cents with the cents truncated (`$16.05`).
    - `quantity(units, scale)` gives the units ÷ scale with thousands separators. A half shows as `.5`, so 5 Hunter units are `2.5`.

- **Port** (`nextron/ports/usage_meter.py`). `UsageMeterPort`, `runtime_checkable`:
  - **`record(call: ApiCall | AiCall) -> None`** writes one row in its own transaction, which commits on its own. It never raises. It must be called outside any transaction (AD-3).
  - **`snapshot(now) -> Usage`** gives the UTC month of `now`. It reads through the caller's current unit of work: inside a transaction, or in a read-only unit.

  `nextron/db` implements it structurally and never imports `nextron.ports`.

- **Persistence.**
  - **Models** (`nextron/db/models/metering.py`).
    - **`ApiUsageRecord`, table `api_usage`.** Its columns:
      - `id`: ULID;
      - `provider` and `operation`: String(32);
      - `sku`: String(32), nullable;
      - `units`: Integer;
      - `ok`: Boolean;
      - `error_code`: String(64), nullable;
      - `at`: `UtcDateTime`.

      Checks: `units >= 0`, `ok IN (0, 1)`, and `(ok = 1) = (error_code IS NULL)`. Index `ix_api_usage_at`.
    - **`AiUsageRecord`, table `ai_usage`.** Its columns:
      - `id`;
      - `route`: String(64);
      - `route_tuple` and `served_model`: String(128);
      - `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `iterations` and `cost_micros`: Integer;
      - `batch` and `refused`: Boolean;
      - `error_code`: String(64), nullable;
      - `at`.

      Checks: every count `>= 0`, and `batch` and `refused` in (0, 1). Index `ix_ai_usage_at`.
  - **Migration** `versions/0006_1_13_metering.py`, revising `0005_1_12_notify`. Checks go through `op.f(...)`, and `downgrade()` raises `NotImplementedError`.
  - **Maps.**
    - `owners.py`: `SHARED_WRITERS` gains `db.metering`, which owns every column of both tables.
    - `pii_registry.py`: every column of both tables is `none`.
  - **`nextron/db/uow.py`** gains `in_transaction() -> bool`: whether a unit of work open in this context, on this thread, holds a transaction (a use case's, or a read-only unit's read).

- **The meter** (`nextron/db/metering.py`). `UsageMeter(units, clock, *, after_record=None)`, where `units` is a local Protocol with `open(*, read_only=False)`.
  - **`record(call)`.** Any failure logs `usage_record_failed` (ERROR, with `provider` or `route`, `operation` when there is one, and `code`), and nothing is raised.
    1. Inside the caller's transaction (`in_transaction()`), it logs with code `inside_transaction`, writes nothing and returns at once, without waiting for the lock.
    2. Otherwise, in a fresh `contextvars.Context()`, it opens `units.open()` and calls a module `@use_case` that inserts the row (`at` = `clock.now()`, `units` = `call.units` for an `ApiCall`) in one transaction.
    3. After that unit closes, still in the fresh context, it calls `after_record()` if one is set. Any error there logs `usage_alerts_failed` (ERROR, traceback) and is not raised.
  - **`snapshot(now)`** runs two `SELECT`s through `current().session` over `[month start, month end)`:
    - `api_usage` grouped by provider, operation and sku: the row count, the failed count and the sum of units;
    - `ai_usage`: the row count and the sum of `cost_micros`.

    No other module has SQL on these tables.

- **Adapters.** Both take a required keyword-only `meter: UsageMeterPort` and record exactly one `ApiCall` for every HTTP request they attempt, whatever its outcome:
  - `ok=True` when the method returns;
  - otherwise `ok=False` with the `AdapterError`'s code, recorded before it is re-raised.

  A refusal before any request is made (credential not set, bad token or chat id) records nothing. The adapters:
  - **`TelegramAlert(secrets, *, meter, http=None)`**: provider `telegram`, operation `send_message`.
  - **`GoogleOAuthClient(*, meter, http=None)`**: provider `google_oauth`, operations `code_exchange`, `token_refresh` and `token_revocation`. A revoke answered by `invalid_token` is `ok`.

- **Jobs.**
  - **The domain** (`domain/job.py`).
    - `JobEvent.DEFER` and `JobCause.DEFERRED`, with the transition `running` --`defer`--> `queued` (`deferred`).
    - `DEFER_DELAY` = 1 h.
    - `HandlerResult(StrEnum)` with `DEFERRED = "deferred"`, and the module constant `DEFERRED`.
    - `type Handler = Callable[[Job], HandlerResult | None]`.
  - **The repository** (`db/repos/jobqueue/jobs.py`) gains `defer_running(job_id, attempts, *, run_after, at) -> int`. It is fenced on (`id`, `running`, `attempts`) and sets `status='queued'`, `attempts = attempts - 1`, `lease_until = NULL`, `run_after` and `updated_at`.
  - **`@use_case JobQueue.defer(claim) -> bool`** moves the job back to `queued` with `run_after` = now + `DEFER_DELAY`, gives the attempt back, and appends the `deferred` event. A fence miss logs `job_claim_lost` and returns `False`. On success it logs `job_deferred` (INFO, the job fields).
  - **Runner.** A handler result that `is DEFERRED` finishes with `defer`. `None` completes. Anything else (the plain string `"deferred"` included) is still `InvalidJob(HANDLER_RETURNED_A_VALUE)`. A deferral never makes a job `dead`.

- **Settings** (`domain/settings_schema.py`, section `Budget and limits`, in this order):

  | Key | Type | Default | Rules | Label |
  |---|---|---|---|---|
  | `quota_hunter_credits` | integer | 50 | 0–100,000 | Hunter credits a month |
  | `quota_prospeo_credits` | integer | 100 | 0–100,000 | Prospeo credits a month |
  | `quota_places_enterprise` | integer | 1,000 | 0–1,000 | Places Enterprise requests a month |
  | `quota_places_pro` | integer | 0 | 0–5,000 | Places Pro requests a month |
  | `quota_job_aggregator` | integer | 0 | 0–100,000 | Job-aggregator requests a month |
  | `ai_budget_monthly` | money | 20,000,000 (μ$) | 0–$100 | AI budget a month |
  | `ai_budget_alert_pct` | integer | 80 | 1–100 | AI spend alert at (%) |

  Each unit text says it counts the UTC calendar month and what stops at the limit. The Places units also say that the maximum is Google's free monthly allowance, so paid use never starts.
  - `queries.setup.budget_limits() -> Limits` reads all seven settings (each quota × its `scale`).

- **`ops` service** (`app/ops/api.py`). `Budget(clock, meter, notify)`:
  - **`status() -> Assessment`** is a plain read with no use case or transaction, so a dashboard GET can call it. It reads the clock once, then returns `assess(meter.snapshot(now), setup_queries.budget_limits())`.
  - **`@use_case check() -> tuple[Threshold, ...]`.** In one transaction it calls `notify.alert(kind, subject, text, {Channel.DASHBOARD})` for each of `thresholds(status())`. It returns the ones whose `Alerted.created` is non-empty, and logs `usage_alert_raised` (INFO, `kind`, `subject`) for each of them after the commit.

- **Composition** (`main.py`).
  - `AdapterContext(config, secrets, clock, meter)`, and the telegram build becomes `TelegramAlert(c.secrets, meter=c.meter)`.
  - **`build_app` builds:**
    - `meter = UsageMeter(units, clock, after_record=lambda: check_usage(units, budget))`;
    - `GoogleOAuthClient(meter=meter)` when no `google_oauth` is injected;
    - `budget = Budget(clock, meter, notify)`;
    - `create_app(..., budget=budget)`.
  - **`check_usage(units, budget)`** runs `budget.check()` in its own unit in a fresh context.

  Building the app still builds no adapter or client, and records nothing.

- **Web.**
  - **`GET /quotas`** (`quotas_page`, `@personal_data(False)`) renders `quotas.html` from `budget.status()` through `nextron/web/quotas_page.py`. The page, in order:
    - the note "Sending, reply handling and unsubscribe polling are never blocked.";
    - "Non-essential work stopped: <stopped joined by '; '>", or "Non-essential work is running.";
    - the month (UTC);
    - **AI spend:** "<spent> of <budget>", the call count, and the state's line (warning, reached or off);
    - **a quotas table:** Quota, Used, Monthly quota and State, where the state is `OK`, `Reached: <stops> stopped` or `Off (quota 0)`, and Hunter shows credits with halves;
    - **a usage table:** Provider, Operation, SKU, Calls, Failed and Units, with Hunter's units in credits. With no lines, "No external calls recorded this month."
  - **Settings link.** `settings.html` links to it: "Quotas and AI spend are on the <a href="/quotas">Quotas and cost page</a>."

- **Tests** run as `test`, make no network call and no real wait over 0.05 s per step, and never contact Anthropic, Hunter, Google or Telegram.

**Never:**
- **Out of scope:**
  - AI calls and `pricing.py` (2.7);
  - per-job-type `budget_class` and the blockable job types (8.1);
  - health-panel issues (1.15);
  - a keeper sweep of thresholds;
  - Telegram alerts for thresholds;
  - quotas reset other than by month;
  - Companies House or Worker counters;
  - doctor checks.
- **The budget never blocks essential work.** Never block sending, replies, unsubscribe polling or any `essential` work on the budget.
- **Usage rows hold no personal data,** and the meter writes no `events` row and logs no field values beyond tokens and codes.
- **No new parts.** Add no dependency, adapter package, trigger or notification type. No code path raises a quota setting.
- **What not to edit.** Don't edit the vendored HTMX, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **Every attempted request is one row.** That holds whatever the outcome, but a refusal before any request (no token, bad chat id) is not a call. `units` = `count` × weight. An adapter passes `count=0` for a call the provider doesn't bill, and every other failure counts, which errs toward stopping early.
2. **The meter writes in its own unit of work, in a fresh context,** like the sink's allowlist read. Inside the caller's open transaction it refuses at once (logged), instead of stalling every writer for 5 s.
3. **A usage row that can't be written is logged and never fails the call.** The provider has already done (and billed) the work; failing would retry it and pay twice.
4. **Threshold notices are raised right after each record,** on the dashboard only (no Telegram, per the spine's alert list). There is one per kind and subject: the AI warning, AI reached, and each quota reached, per UTC month. A jump past both 80% and 100% gives both. Raising the budget mid-month doesn't repeat a notice.
5. **A limit of 0 means off.** Blockable work on it is always refused, the panel shows "Off", and it raises no notice and isn't listed as "stopped". So the default 0 for Places Pro and the job aggregator stays quiet.
6. **Places quotas are capped at Google's free monthly allowance:** Enterprise 1,000 and Pro 5,000 (March 2025 Maps pricing). No setting value can start paid Places use (FR-5, NFR-12). Mac should re-check these against his billing account. The AI budget is capped at $100, the whole-engine ceiling (SM-9).
7. **A deferral gives the attempt back** (`attempts − 1`). That is how a deferred job never uses a retry or becomes `dead`, and the fence stays sound, because the deferring run has already finished.
8. **The panel is `/quotas`, linked from Settings.** The navigation's sections are fixed by UX-DR, so it isn't added there.
9. **A Hunter call the table doesn't weight counts 1 per `count`.** The Hunter story (2.13) passes `count=0` for free calls.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Record API | `ApiCall("hunter", "find", ok=True)` outside any transaction | one `api_usage` row, units 2, `at` = clock; `verify` gives 1, `domain_search` 2; Places SKUs `pro` and `enterprise` kept apart | N/A |
| Record AI | an `AiCall` with tokens, `cost_micros` 1,234 and `refused=True` | one `ai_usage` row with every field | N/A |
| Own commit | prepare, record, then an apply transaction that raises | the usage row stays | N/A |
| Inside a transaction | `record` while the caller's transaction is open | no row; `usage_record_failed` with `inside_transaction`; returns at once | never raises |
| Bad call | provider `Hunter`; operation `a b`; `error_code` set with `ok=True`; `count=-1`; a model with `@`; `count=True` | refused at construction | `TypeError` or `ValueError` |
| Allow | each quota and the budget at 79%, 80%, 99% and 100% | `blockable` refused only at 100% (and at any usage with limit 0); `essential` always allowed, even at 150% | `UnknownQuota` for a bad name |
| Defer | a handler returns `DEFERRED`, retry limit 0, 30 times | each time `queued`, `run_after` +1 h, attempts unchanged, a `deferred` event; never `dead` | a plain `"deferred"` string fails the job |
| AI 80% | budget $20, spend reaches $16 | one notification "AI spend at 80% of $20: $16 spent in 2026-11"; a further record adds none | N/A |
| Quota reached | Hunter quota 50, 100 units recorded | "Hunter quota reached: enrichment stopped (50 of 50 credits used in 2026-11)", once; next month again | N/A |
| Quiet zero | Places Pro quota 0, nothing used | no notification; panel "Off (quota 0)"; `allow(blockable, quota="places_pro")` is `False` | N/A |
| Hook error | `after_record` raises | the row is kept | `usage_alerts_failed` logged |
| Adapters | Telegram 200, 429, connect error, `ReadTimeout`; Google refresh 200, 400 `invalid_grant`, revoke `invalid_token` | exactly one record each, with the right `ok` and code; a missing token or bad chat id records nothing | N/A |
| Panel | Hunter 5 units, AI $16.05 of $20, Places Enterprise 1,000 of 1,000 | "2.5" credits; the warning line; "Reached: discovery stopped"; "Non-essential work stopped: Places Enterprise quota reached"; the never-blocked note | N/A |

</frozen-after-approval>

## Code Map

- **The composition root** (`nextron-engine/nextron/main.py`).
  - **What's there:** `build_app` (lines 249-299), `AdapterContext` (359-366), `ROLE_ADAPTERS` (393-395), `read_sink_allowlist` (the fresh-context pattern to copy for `check_usage`), and the module docstring.
  - **What changes:** add a "Metering and the budget" paragraph. The `budget` lambda breaks the cycle: meter → hook → budget → meter.
- **The jobs.**
  - **The runner** (`nextron-engine/nextron/jobs/runner.py`). `_run_handler` (305-315) returns `Exception | None` today; make it return the outcome. `_finish` (317-329) gains the defer branch. Update the module docstring's `run_one` paragraph.
  - **The queue** (`nextron/app/jobqueue/api.py`). Copy `complete` (351-361) for `defer`. `_move_running` can't pass `attempts` in its changes, because `update_running`'s positional parameter is also named `attempts`, so add the repository function.
  - **The domain** (`nextron/domain/job.py`): the docstring table, `TRANSITIONS` (167-193), and `Handler` (454).
- **Persistence.**
  - **Units of work** (`nextron-engine/nextron/db/uow.py`). `UnitOfWork._tx` holds the open transaction (line 258), and `_CURRENT` is the ContextVar.
  - **The event log** (`db/eventlog.py`) is the pattern for a shared writer inserting through `current().session`.
  - **Patterns to copy:** the model `db/models/notify.py`, and the migration `db/alembic/versions/0005_1_12_notify.py`. Also add the import to `models/__init__.py`.
  - **Maps:** `owners.py`, where the `SHARED_WRITERS` docstring wording needs a touch, and `pii_registry.py`.
- **The adapters.** `nextron/adapters/telegram/__init__.py`: `send` does the pre-checks, then the POST at lines 135-147. `nextron/adapters/google_oauth/__init__.py`: `exchange`, `refresh` and `revoke` (201-240), with `_post` and `_check`. Update both module docstrings and the `adapters/__init__.py` docstring.
- **Settings** (`nextron/domain/settings_schema.py`).
  - **What's there:** `Section.BUDGET_AND_LIMITS` (no setting yet), the `money` type (micro-dollars, `MICROS_PER_DOLLAR`), and `minimum` and `maximum`.
  - **Tests that list keys:**
    - `tests/unit/test_settings_schema.py`;
    - `tests/contracts/test_settings_contracts.py` (the `Key members not declared:` probe message);
    - the Settings page tests that post the whole form (`real_body`).
  - **The query** (`nextron/app/queries/setup.py`). Its import rule is domain and `repos.setup` only.
- **`ops`** (`nextron/app/ops/api.py`). `DeadJobAlerts` is the pattern for `notify.alert` in a transaction and for logging after the commit. Update the module docstring.
- **Web** (`nextron/web/app.py`).
  - **What's there:** `create_app` (228-251), `page()`, and the module docstring's route list. `settings_page.py` is the pattern for a page helper module. `templates/settings.html` gets the link.
  - **`tests/dashboard.py`** builds `Budget(clock, InMemoryUsageMeter(clock), notify)` and passes it.
  - **`tests/contracts/test_web_contracts.py`** (140-158 and 268-279): the ordered paths gain `/quotas` and `GET /quotas`.
- **Fakes.** Add `tests/fakes/usage_meter.py` (`InMemoryUsageMeter(clock=None)`, with `records`, each stamped with its clock's time; `snapshot` filters by month). Register it in `tests/fakes/__init__.py` `PORT_FAKES`. `tests/contracts/test_test_isolation.py:133-142` (the port name set) gains `UsageMeterPort`. Fake parameter names must equal the port's.
- **Constructor call sites to update with `meter=`:**
  - `TelegramAlert(`: `tests/app/test_notify.py`, `test_telegram_secrecy.py`, `tests/contracts/test_adapter_boundary.py`, `test_sink_contracts.py` and `tests/unit/test_telegram_adapter.py`;
  - `GoogleOAuthClient(`: `test_adapter_boundary.py`, `test_test_isolation.py`, `tests/live_guard.py` (docstring) and `test_google_oauth_adapter.py`;
  - `AdapterContext(`: `test_notify.py`, `test_sink_contracts.py`, `test_test_isolation.py`, `tests/unit/test_role_adapters.py` and `test_runner_lifespan.py`.
- **The adapter boundary** (`tests/contracts/test_adapter_boundary.py`). It builds every adapter class whose `__init__` takes `http`. Pass a fake meter. Add the assertion that each attempted request records exactly one `ApiCall`, and the coverage check that each such class takes a required keyword `meter`.
- **Revision literals moving to `0006_1_13_metering`:**
  - `tests/contracts/test_persistence_contracts.py:72` (`HEAD`), with the chain probes at 670-684 shifted up by one (`0006`→`0007`, `0007`→`0008`, "revision 6"→"revision 7");
  - `tests/unit/test_migrations.py:36`;
  - `tests/app/test_serve_process.py:241`;
  - `tests/contracts/test_source_tree.py` `FILES` (the new files).
- **The job contracts** (`tests/contracts/test_job_contracts.py`). The exact transition table lives in `tests/unit/test_job_domain.py`. The registered types are unchanged.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/budget.py` -- the call records, months, usage, quotas, limits, `allow`, `assess`, `thresholds` and formatting -- the budget rules in one pure place.
- [x] `nextron-engine/nextron/ports/usage_meter.py`, `ports/__init__.py` docstring -- `UsageMeterPort`.
- [x] `nextron-engine/nextron/db/models/metering.py`, `models/__init__.py`, `owners.py`, `pii_registry.py`, `alembic/versions/0006_1_13_metering.py`, `db/uow.py` (`in_transaction`), `db/metering.py`, `db/__init__.py` docstring -- the tables and the meter.
- [x] `nextron-engine/nextron/domain/job.py`, `db/repos/jobqueue/jobs.py`, `app/jobqueue/api.py`, `jobs/runner.py`, `jobs/__init__.py` docstring -- `DEFERRED` and `defer`.
- [x] `nextron-engine/nextron/domain/settings_schema.py`, `app/queries/setup.py` -- the seven settings and `budget_limits()`.
- [x] `nextron-engine/nextron/adapters/telegram/__init__.py`, `adapters/google_oauth/__init__.py`, `adapters/__init__.py` docstring -- record every attempted request.
- [x] `nextron-engine/nextron/app/ops/api.py` -- `Budget`.
- [x] `nextron-engine/nextron/main.py` -- the meter, `check_usage`, `AdapterContext.meter`, `GoogleOAuthClient(meter=...)` and `Budget`.
- [x] `nextron-engine/nextron/web/app.py`, `web/quotas_page.py`, `templates/quotas.html`, `templates/settings.html`, `static/app.css` (if needed) -- the panel and the link.
- [x] `nextron-engine/tests/fakes/usage_meter.py`, `tests/fakes/__init__.py` -- the fake.
- [x] `nextron-engine/tests/unit/test_budget_domain.py` -- every Bad call refusal; the weights; months at the edges (the last microsecond of a month, 1 January); `allow` at 79%, 80%, 99% and 100% for each quota and the budget, `essential` at 150%, and limit 0; `assess` states; the exact `thresholds` texts; `dollars` and `quantity`.
- [x] `nextron-engine/tests/app/test_metering.py` -- on a temporary database: Record API, Record AI, Own commit, Inside a transaction, snapshot grouping and month bounds, the hook order (after the commit), and Hook error.
- [x] `nextron-engine/tests/app/test_budget.py` -- `Budget.check` through a real `UsageMeter` and `Notify`: AI 80%, both thresholds at once, Quota reached once per month, Quiet zero, and a mid-month budget raise; plus `status()` in a read-only unit.
- [x] `nextron-engine/tests/app/test_jobqueue.py`, `tests/unit/test_job_runner.py`, `tests/unit/test_job_domain.py` -- the Defer row: the events, the fence miss, the 30 deferrals with retry limit 0, and the string refused.
- [x] `nextron-engine/tests/unit/test_telegram_adapter.py`, `test_google_oauth_adapter.py` -- the Adapters row.
- [x] `nextron-engine/tests/unit/test_quotas_page.py` -- the Panel row through `Dashboard(units=units)`, the empty state, the Settings link, and no-store.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py` -- the real `build_app` wiring: a Telegram send through the built `Notify` writes one `api_usage` row, and a record over a quota adds its notification. Each is checked to fail when the wiring breaks.
- [x] `nextron-engine/tests/contracts/test_metering_contracts.py` -- only `db/metering.py` (and the models and migration) names the two tables or their records (AST, with a probe); no field of `ApiCall` or `AiCall` accepts `@` or a space; every `QUOTAS` setting is a declared integer setting; the Places quota maximums equal the allowances in decision 6; and no other setting names Places.
- [x] The existing tests in the Code Map, and `nextron-engine/README.md`: a "Metering, quotas and the AI budget" section (the tables, units, quotas, `allow`, notices, the panel, and never blocking essential work), "Deferring a job" in Jobs, and migration `0006` under Database.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `serve`, when it starts, then the database is at `0006_1_13_metering`, no adapter or HTTP client has been built, and no usage row exists.

## Implementation Notes

- **`@` in model names (spec conflict resolved toward privacy).** The `AiCall` bullet gives `route_tuple` and `served_model` the pattern `[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}`, but the Bad call row ("a model with `@`" is refused), the prose ("no address fits") and the contract task ("no field of `ApiCall` or `AiCall` accepts `@` or a space") all refuse `@`. The code drops `@` from that character class (`MODEL_PATTERN = [A-Za-z0-9][A-Za-z0-9._:/-]{0,127}`), so all three hold. Anthropic's own model ids (`claude-opus-5-5`) need no `@`; a Vertex-style `model@version` id would need this revisited in Story 2.7.
- **Adapters record through `Metered` (in `nextron/ports/usage_meter.py`).** `tests/contracts/test_error_contracts.py` forbids a bare `raise` in an adapter, so "record, then re-raise" can't be written as `except AdapterError: record; raise`. `Metered(meter, provider, operation)` is a small context manager next to the port: its `__exit__` records exactly one `ApiCall` (`ok`, or the `AdapterError`'s code) before the error propagates, and never suppresses it. The block may set `count` (0 for an unbilled call) or `sku`, ready for Hunter and Places.
- **`Limits` holds exactly the `QUOTAS` names**, already scaled (`budget_limits()` multiplies by `scale`). `allow` returns `True` for `essential` before looking at the quota name, so a typo can never block essential work; for `blockable` an unknown name raises `UnknownQuota`.
- **AI budget 0** shows "Off (budget $0): discovery, enrichment and re-checks don't run." on the panel; like a quota of 0 it raises no notice and isn't listed as stopped (decision 5), while `allow(blockable, ...)` refuses.
- **New contract in `test_metering_contracts.py`:** besides the specified checks, only `nextron/main.py` may import `nextron.db.metering`, so services and delivery layers record only through the port.

Review fixes (pass 1, P1–P5; see the Review Triage Log):
- **P1.** `tests/unit/test_settings_schema.py` pins each new setting's type, section, default and exact rule list. It also refuses "-1" for each quota (`minimum`), and "0" (`minimum`) and "101" (`maximum`) for `ai_budget_alert_pct`.
- **P2.** `test_in_transaction_is_false_outside_any_unit_and_on_another_thread` (`tests/app/test_metering.py`) now runs `in_transaction` in a copied context:
  - on a second thread, where it is `False`;
  - on the same thread, where it is `True`.

  So the thread clause is what gives the `False`.
- **P3.** The `domain/job.py` docstring and the README (Retries and backoff, Deferring a job) now say three things:
  - `attempts` counts claims, except deferred ones;
  - a retry-limit-0 type runs again after each `DEFERRED`;
  - a handler returns `DEFERRED` only before its use case has done any work, with the budget check first, because a deferred job runs again from the start.
- **P4.** The usage table names the unit for a scaled provider (`quotas_page.usage_units`: Hunter shows "1.5 credits"). Other providers stay plain numbers.
- **P5.** `NEVER_BLOCKED` ("Sending, reply handling and unsubscribe polling are never blocked.") and `MAX_PERCENT` now live only in `domain/budget.py`. `settings_schema.py` and `web/quotas_page.py` import them, so the Settings hints and the panel use one wording.

**Final verification (after pass 1).**
- `uv run pytest -q`: 3641 passed, 2 skipped (`ai_eval` and the symlink test), in 193 s.
- `uv run lint-imports`: 29 kept, 0 broken.
- Before the review fixes: 3627 passed, 2 skipped, and 29 kept. The `job_runner` thread test that was flaky in Story 1.12 passed in all three full runs.

Not verified here:
- a real metered Telegram or Google request;
- any caller of `allow` or `DEFERRED` (Epics 2 and 8 add the first);
- the Places free-allowance caps against Mac's billing account.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (13 findings), EC = edge-case hunter (11), VG = verification gap (1 gap and 2 other findings).
- **Counts:** 27 raw findings, 20 entries after deduplication. BH7 makes two separate claims, so it appears in two entries.
- **Outcomes:**
  - 5 entries patched (P1–P5): 2 doc or wording fixes, 1 display fix and 2 test gaps;
  - 1 deferred: G2, for Story 8.1;
  - 14 rejected, 2 of them as false.
- **No loopback.** No finding is an intent gap or a bad spec. G1, G4, BH11, EC3, EC4 and EC11 are rejected only because their fixes would change the approved spec: the AC's 1-hour deferral, the frozen panel and notice texts, and decisions 1, 2 and 9. Mac can revisit them with the overnight decisions.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | VG1 (gap) | The seven new settings' range rules are untested. Deleting `minimum(0)` from a quota, or `maximum(100)` from `ai_budget_alert_pct`, leaves the suite green, and the next `budget_limits()` raises (`Limits` refuses a negative quota or a percentage over 100), so `/quotas` returns 500 and notices stop. | low | Pre-verified. Only four refusals are tested (`test_settings_page.py:683`), with no rule-list test like `bulk_threads`'s. The fix is test-only. | P1 |
| B | VG-other1 | `test_in_transaction_is_false_outside_any_unit_and_on_another_thread` never uses another thread, so `in_transaction()`'s thread clause is untested. | low | Verified at `tests/app/test_metering.py:255`: only a fresh context on the same thread. The fix is test-only. | P2 |
| C | BH6 | The `domain/job.py` docstring (lines 23 and 33) and the README (around line 708) still say "`attempts` counts claims" and "retry limit 0 never runs twice", which a deferral (the attempt given back) makes untrue. Nothing says a handler must return `DEFERRED` before doing any work. | low | Verified: `defer_running` sets `attempts - 1`, so a retry-limit-0 job runs again after each deferral. The doc fix is direct. | P3 |
| D | BH12 | The usage table's Units column shows Hunter in credits ("1.5") and the other providers in raw units, with no unit named. | low | Verified in `quotas_page.page_context` (`quantity(line.units, display_scale(...))`, no unit word); the quotas table says "2.5 credits". The fix is direct: name the unit on scaled rows. | P4 |
| E | BH13 | Two copies have drifted: the settings hints' `NEVER_BLOCKED` ("never stopped", "replies") differs from the page note ("never blocked", "reply handling"), and `MAX_PERCENT` is defined in both `settings_schema.py` and `budget.py`. | low | Verified. The Settings hints and the panel tell Mac the rule in two wordings, and the next edit would have two places to change. The fix is direct: one copy in `domain/budget.py`, imported by both. | P5 |
| F | BH2, EC10 (claim) | "Paid Places use never starts" is not enforced end to end. `allow()` checks a snapshot with no reservation, the quota maximum equals the free allowance with no margin, two bulk threads can both pass at 999, and one use case can make several requests after one check. | low | True for future callers: no Places caller exists yet. Story 8.1's AC already re-checks a fresh snapshot before each request. The overshoot is one request per concurrent thread (cents), and a reservation is new public surface. Recorded so 8.1 also serialises Places calls under one concurrency key. | defer |
| G | BH1, EC6 | A 1-hour `DEFER_DELAY` re-claims a stopped job 24 times a day until the month resets (forever at a limit of 0), each cycle adding two permanent `events` rows. | low | True, but the 1-hour re-queue is the AC ("re-queued with `run_after` one hour later"), and Story 8.1 relies on it ("a quota of 0 is a hard stop"). No job type defers yet. Changing the delay changes the approved spec. | reject |
| H | BH3, EC5 | Quotas match on free-form provider, operation and SKU strings, so a future adapter that misspells one (or records Places with `sku=None`) counts toward no quota, silently. | low | No Hunter, Prospeo or Places adapter exists yet. The exact strings are in `QUOTAS` and the spec, which Stories 2.13 and 8.2 will follow. Domain-owned constants or a per-provider SKU rule are new surface guarding state not yet shown. 2.13 and 8.2 should add a test that their records match `QUOTAS`. | reject |
| I | EC4 | A billed Hunter operation missing from `UNIT_WEIGHTS` counts as weight 1, half a credit. | low | Decision 9: unweighted Hunter calls count 1 per `count`. Hunter's billed operations (find, Domain Search, verify) are all weighted. The fix changes the approved decision. | reject |
| J | BH4 | Lowering a quota in Settings below this month's usage raises no "reached" notice until another call is recorded. | low | True: notices are raised only after a record (decision 4, and the AC's "When usage is recorded"). Mac made the change himself, and the panel and `allow` reflect it at once. The fix adds a cross-service call after a save. | reject |
| K | BH5, EC9 | With the AI budget at $0, the panel's headline says "Non-essential work is running." while `allow` refuses every blockable call. | low | True. The same page says "Off (budget $0): discovery, enrichment and re-checks don't run." The frozen spec defines `stopped` as reached lines only, and the headline as exactly two texts (decision 5: off isn't listed as stopped). Mac sets $0 only on purpose. The fix edits the approved spec. | reject |
| L | BH7 (part), EC2 | A non-`AdapterError` leaving a `Metered` block after the request went out records no row, undercounting a billed request. | false | Every HTTP adapter maps every exception after the request to `AdapterError` (`except Exception`), and `tests/contracts/test_adapter_boundary.py` proves for each one that only `AdapterError` leaves, garbage 200s included. | reject |
| M | BH7 (part), EC1, VG-other2 | An invalid `count` or `sku` set inside a `Metered` block makes `__exit__` raise `ValueError` out of the adapter, failing a billed call. | low | No adapter sets `count` or `sku` today, and both are code constants or checked values in the adapters to come. The fix is a guard for a state not shown, and a bad value fails loudly in that adapter's own tests. | reject |
| N | EC3 | Failed requests (429, 5xx, connect errors) still count units, so failures use up a quota. | low | Decision 1: every failure counts unless the adapter passes `count=0`, which errs toward stopping early. `Metered` lets an adapter set `count = 0` for an unbilled failure. The fix changes the approved decision. | reject |
| O | EC7 | If `JobQueue.defer` itself fails (database locked), the lease expires and the reap counts the attempt, so a retry-limit-0 job can die. | low | True. It is the same path as a failed `complete` (pre-existing since Story 1.10: `job_finish_failed`, the lease left to expire), and needs a 5 s lock timeout. Retrying database errors was rejected in Stories 1.10 and 1.12. | reject |
| P | BH8, EC8 | A stored budget setting that no longer passes its rules makes `budget_limits()` raise, so `/quotas` returns 500 and checks log `usage_alerts_failed`. | low | It needs a hand edit, or a code change that lowers a maximum below a saved value. The Settings page then shows the stored problem (`stored_problems`), and every other settings consumer raises the same way. The fix adds error handling to the page. | reject |
| Q | BH9 | `InMemoryUsageMeter` always records, hiding the real meter's `inside_transaction` refusal from service tests. | false | Only adapter unit tests use the meter fake, and they run outside any unit of work. Service tests replace the adapter itself with its port fake, so the meter is never reached there, whatever the fake does. | reject |
| R | BH10 | The threshold check runs after every record, Telegram and Google revokes included (a revoke runs under `GoogleAccess._store_lock`): one more transaction each, and up to 5 s more when the database is busy. | low | Decision 4 ("right after each record"). A Telegram record can also raise a quota notice after a lowered limit (J). The cost is one short transaction per external call, and the busy case needs a 5 s lock timeout. | reject |
| S | BH11 | "AI spend at 80% of $20: $21 spent" says 80% after a jump past 100%. | low | The text is exactly the frozen thresholds table, `AI spend at <pct>% of <budget>: <spent> spent in <month>`, and it states the spend. The fix edits the approved spec. | reject |
| T | EC11 (claim) | A record made inside a transaction is dropped, so that billed call never counts. | low | Decision 2: refusing at once is chosen over a 5 s stall, and AD-3 forbids a port call inside a transaction. It logs `usage_record_failed` (`inside_transaction`), and tests pin it. The fix changes the approved decision. | reject |

## Design Notes

```python
def record(self, call: ApiCall | AiCall) -> None:      # db/metering.py; never raises
    if in_transaction():                                # AD-3: a port call is never inside one
        _failed(call, INSIDE_TRANSACTION); return
    contextvars.Context().run(self._write_then_hook, call)

def _write_then_hook(self, call):
    try:
        with self._units.open(): _insert(call, self._clock.now())   # @use_case, own commit
    except Exception: _failed(call, ...); return
    if self._after_record is not None:
        try: self._after_record()                       # main.check_usage -> Budget.check
        except Exception: log.exception("usage_alerts_failed")
```

**Why the meter, not the adapter, holds the hook:** every adapter records the same way, and the threshold check needs `notify`, which neither `db` nor an adapter may import. `main` hands the meter a callable, as it hands the sink its allowlist reader.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` and the symlink test skipped).
- `uv run lint-imports` -- expected: every contract (29) KEPT.
