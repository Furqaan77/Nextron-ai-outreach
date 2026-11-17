---
title: 'Story 1.12: Send ops alerts by Telegram and show dashboard notifications'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '34b879aa2063776d7560f9e690d28d37edb85bd4'
review_loop_iteration: 0
story_key: '1-12-send-ops-alerts-by-telegram-and-show-dashboard-notifications'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A job can die, and nobody hears about it. The engine has no Telegram adapter, no `alerts` or `notifications` tables, and an empty notification area. An HTMX action the server refuses (a 403 CSRF refusal, say) shows nothing at all (Story 1.5, decision 4).

Requirements: AD-15, AD-20, INT-8, NFR-17, UX-DR5.

**Approach:**
- **Service.** The `notify` service owns `alerts` and `notifications` (migration `0005_1_12_notify`) and the Alert machine. `notify.alert(...)` queues each Telegram alert as a `send_alert` job.
- **Adapter.** A Telegram adapter (`adapters/telegram`) sits behind `AlertPort` and is wrapped by the sink outside `prod`.
- **`job_dead`.** An `ops` sweep, which the runner calls, raises the `job_dead` alert for every dead job.
- **Notification area.** The base layout loads the notifications, and Mac dismisses each with a POST. HTMX shows any error response's text in the area.

The acceptance criteria are Story 1.12 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise. The migration id is `0005_1_12_notify`, because revision ids allow no hyphens.

## Boundaries & Constraints

**Always:**

- **Domain** (`nextron/domain/alert.py`, stdlib only).
  - **The Alert machine.** It uses `AlertStatus` (`pending`, `sent`, `unknown`, `failed`), `AlertEvent` and `AlertCause`, plus a read-only `TRANSITIONS` table and `transition(current, event)`. `transition` uses the shared `Transition` and `IllegalTransition` (`domain/machines.py`).
    - **Creation:** to `pending`, cause `requested` (`CREATION`).
    - **The transitions** (event → status, cause):

      | From | `deliver` | `lose` | `fail` |
      |---|---|---|---|
      | `pending` | `sent`, `delivered` | `unknown`, `outcome_unknown` | `failed`, `failed` |
      | `unknown` | `sent`, `delivered` | — | `failed`, `failed` |

  - **Channels.** `Channel` (StrEnum) has `telegram` and `dashboard`.
    - `DELIVERY_CHANNELS = {telegram}`: these channels get an `alerts` row.
    - `RESENDS_UNKNOWN = {telegram}`: these channels re-send an `unknown` alert.
  - **Names.** `SEND_ALERT_JOB = "send_alert"`, `JOB_DEAD = "job_dead"` and `NOT_DELIVERED = "alert_not_delivered"`.
  - **Timing.** `RESEND_DELAY` is 60 s.
  - **Text.** `MAX_TEXT_UNITS = 4096` UTF-16 code units, Telegram's limit.
  - **Checks.** `check_alert(kind, subject, text, channels) -> frozenset[Channel]` raises `InvalidAlert(reason)` (a `DomainError`, code `invalid_alert`) unless:
    - `kind` fullmatches `[a-z][a-z0-9_]{0,31}`;
    - `subject` is a ULID or fullmatches `[a-z0-9_:.-]{1,64}`;
    - `text` is a `str`, not blank, at most `MAX_TEXT_UNITS`, with no `Cc`, `Cf`, `Cs`, `Zl` or `Zp` character except `\n`;
    - `channels` is a non-empty iterable of `Channel` members; a bare `str` is refused.

    The reason never includes a value.
  - **Chat ids.** `is_telegram_chat_id(value)` fullmatches `-?[1-9][0-9]{0,19}`.
  - **Text of a dead job.** `job_dead_text(job_type, job_id, attempts)` is exactly `"Job dead: <type> <ULID>, attempts: <n>"`.

- **Port** (`nextron/ports/alert.py`). It gains `DELIVERY_UNKNOWN = "alert_delivery_unknown"`, documented: an `AdapterError` with this code is never retryable and means the provider may have received the message. The `AlertMessage` and `AlertDelivery` fields are unchanged.

- **The Telegram adapter** (`nextron/adapters/telegram/__init__.py`). Its import rule: `nextron.ports`, `nextron.domain`, `httpx2` and the stdlib. Nothing in it logs.
  - **`default_http_client()`** is the live constructor: `httpx2.Client(timeout=httpx2.Timeout(15.0, connect=10.0), follow_redirects=False)`.
  - **`TelegramAlert(secrets, *, http=None)`** implements `AlertPort`. It builds the client on its first send through a module-level lookup, so a test can refuse it, under a lock.
  - **`send(message: AlertMessage) -> AlertDelivery`:**
    1. **Read the token** at call time with `read_credential(secrets, "telegram_bot")`. Its `AdapterError`s propagate.
    2. **Check the token and the chat.**
       - A token that doesn't fullmatch `[0-9]+:[A-Za-z0-9_-]+` raises `telegram_bad_token`. That shape is exactly what `redact()` masks.
       - A recipient that isn't `is_telegram_chat_id` raises `telegram_bad_chat_id`.

       Both are non-retryable.
    3. **Post.** POST `https://api.telegram.org/bot<token>/sendMessage` with JSON `{"chat_id": int(recipient), "text": text}`. There is never a `parse_mode` or any other field.
  - **Outcomes:**

    | Result | Outcome |
    |---|---|
    | 2xx with JSON `{"ok": true, "result": {"message_id": <int, not bool>}}` | `AlertDelivery(str(message_id))` |
    | any other 2xx | `DELIVERY_UNKNOWN` |
    | 429 or 5xx | retryable, `telegram_unavailable` |
    | any other status (400, 401, 403, 404, a 3xx) | non-retryable, `telegram_refused`; the message names the status only |
    | `ConnectError`, `ConnectTimeout`, `PoolTimeout`, `ProxyError`, `UnsupportedProtocol`, `LocalProtocolError`, `WriteError`, `WriteTimeout` (nothing reached Telegram) | retryable, `telegram_unavailable` |
    | `ReadTimeout`, `ReadError`, `RemoteProtocolError` or any other exception (the request may have been sent) | `DELIVERY_UNKNOWN` |

  - **Error hygiene.** Every raise is `from None`. No error, its `repr` or its traceback carries the token, the URL, the text, the chat id or a response body.

- **Persistence** (`nextron/db/models/notify.py`; owner `notify` in `owners.py`; migration `versions/0005_1_12_notify.py`, which revises `0004_1_11_heartbeat`; `downgrade()` raises `NotImplementedError`).
  - **`AlertRecord`, table `alerts`.** Its columns:
    - `id`: ULID;
    - `kind`: String(32);
    - `subject`: String(64);
    - `channel`: String(16);
    - `text`: Text;
    - `status`: String(16);
    - `provider_message_id`: String(128), nullable;
    - `error_code`: String(64), nullable;
    - `resent_at`: nullable;
    - `created_at` and `updated_at`.

    Constraints: unique `uq_alerts_kind_subject_channel`; a status check; no channel check.
  - **`NotificationRecord`, table `notifications`.** Its columns:
    - `id`;
    - `kind`: String(32);
    - `subject`: String(64);
    - `text`: Text;
    - `created_at`;
    - `dismissed_at`: nullable.

    Constraints: unique `uq_notifications_kind_subject`, and `ix_notifications_open` on (`dismissed_at`, `created_at`).
  - **Personal data.** Both `text` columns are `redact`. Every other column is `none`.
  - **Repositories** (`db/repos/notify/alerts.py` and `notifications.py`): frozen row types, and the only SQL on these tables. Status updates are fenced on the status the caller saw and return their row count.

- **Service** (`nextron/app/notify/api.py`). `Notify(clock, queue, *, alert_ports: Mapping[Channel, Callable[[], AlertPort]])`. Each channel's port is built on its first send and kept, under a lock. Every status change appends an `events` row: entity `alerts`, actor `system:notify`.
  - **`@command alert(kind, subject, text, channels) -> Alerted(created: tuple[Channel, ...])`.**
    1. It runs `check_alert`.
    2. For each Telegram channel without a `(kind, subject, channel)` row, it inserts a `pending` row, appends the creation event, and enqueues `send_alert` with:
       - scope = the alert id;
       - discriminator `"0"`;
       - payload `{"v": 1, "alert_id": <id>}`;
       - the realtime lane.
    3. For `dashboard` without a `(kind, subject)` notification, it inserts one.
    4. An existing row is left untouched, so a repeat is a no-op.
  - **`@use_case send(alert_id) -> None`** is the `send_alert` job's use case. The port is never called inside a transaction (AD-3, and the deferred sink-allowlist item).
    - **Prepare** (one transaction).
      - **Nothing to do:** a missing alert, a status other than `pending`, or an `unknown` alert that was already re-sent or whose channel isn't in `RESENDS_UNKNOWN`. It returns.
      - **The chat id:** `queries.setup.get(Key.ALERT_CHAT_ID)`. A `SettingNotSet` or `InvalidSettingValue` fails the alert in this same transaction (see Not delivered), with the error's code, and returns.
    - **The call.** `port.send(AlertMessage(chat_id, text))`. Then:
      - a retryable `AdapterError` re-raises, so the job retries and the alert stays as it was;
      - `DELIVERY_UNKNOWN` is an unknown outcome;
      - any other `AdapterError` is a failure with its code;
      - anything else propagates.
    - **Apply** (one transaction, fenced on the prepared status).
      - **Delivered:** `deliver`, with `provider_message_id` (`NULL` for a sink delivery). Logs `alert_sent` (`alert_id`, `kind`, `channel`, `sink`).
      - **Unknown on the first send:** `lose`, `error_code`, and the resend job (discriminator `"1"`, `run_after` = now + `RESEND_DELAY`).
      - **Unknown on the resend:** the status stays `unknown`. `resent_at` = now, and Not delivered applies.
      - **Failure:** `fail`, `error_code`, and Not delivered.
      - A resend that reaches apply always sets `resent_at`.
      - **Logs:** `alert_unknown` and `alert_failed` (WARNING, `alert_id`, `kind`, `channel`, `code`).
    - **Not delivered.** Notification kind `alert_not_delivered`, subject the alert id. The text is `"Telegram alert not delivered: <kind> <alert ULID>, <code>"`, or for a second unknown, `"Telegram alert may not have been delivered: …"`. It goes to the dashboard only.
  - **`@use_case dismiss(notification_id) -> bool`** sets `dismissed_at` once. An unknown or already dismissed id is `False`, and nothing changes.
  - **Logs** never carry text, a chat id or a token.
- **Queries.**
  - `queries.notify`:
    - `alert(id)`;
    - `subjects(kind) -> frozenset[str]` (subjects with an alert or a notification of `kind`);
    - `open_notifications(limit) -> OpenNotifications(count, newest)`, undismissed, newest first.
  - `queries.jobqueue.dead_jobs() -> tuple[JobRow, ...]`.

- **The `job_dead` sweep** (`nextron/app/ops/api.py`). `DeadJobAlerts(notify)` has `@use_case alert_dead_jobs() -> int`.
  - In one transaction, it calls `notify.alert(JOB_DEAD, job.id, job_dead_text(...), channels)` for every dead job whose id isn't in `subjects(JOB_DEAD)`.
  - The channels are `{telegram, dashboard}`, or `{dashboard}` for a `send_alert` job.
  - It logs `dead_jobs_alerted` (`count`) when the count is above 0.
- **Runner.** `Runner(..., dead_jobs=None)` takes a local Protocol with `alert_dead_jobs() -> int`.
  - It calls the sweep in its own unit of work after a `fail` returns `dead`, and on every keeper tick.
  - A sweep error logs `dead_job_alerts_failed` (ERROR, traceback), and nothing stops.
  - The runner's imports don't change.
- **Handler** (`nextron/jobs/handlers.py`, stdlib and domain only). `send_alert(alerts: Callable[[], AlertSending]) -> handler`. The handler calls `alerts().send(job.payload["alert_id"])`, once, and returns `None`.
- **Setting.** `Key.ALERT_CHAT_ID = "alert_chat_id"`.
  - Type `string`, section Alerts and recipients, default `REQUIRED`, rule `telegram_chat_id` (`is_telegram_chat_id`).
  - Label: "Telegram alert chat id".
  - Unit: "The numeric id of the Telegram chat that receives ops alerts, such as -1001234567890; user names (@name) aren't accepted."
- **Composition** (`nextron/main.py`).
  - **The adapter.** `ROLE_ADAPTERS = (RoleAdapter("telegram", SinkRole.ALERT, TelegramAlert, lambda c: TelegramAlert(c.secrets)),)`.
  - **The job type.** `build_job_types(*, alerts)` registers `JobType("send_alert", realtime, {realtime}, priority 90, max_runtime 3 min, retry_limit 5, handlers.send_alert(alerts))`, then freezes. It is still the only registrar.
  - **`build_app`:**
    - `job_types = build_job_types(alerts=lambda: notify)`, with `notify` bound below it;
    - `Notify(clock, queue, alert_ports={telegram: partial(build_role_adapter, "telegram", AdapterContext(config, secrets, clock), units=units)})`;
    - `Runner(..., dead_jobs=DeadJobAlerts(notify))`;
    - `create_app(..., notify=notify)`.

    Building still builds no adapter, no client, and nothing that starts.
  - **Triggers** stay empty.
- **Web.**
  - **`GET /notifications`** (`notifications_area`, `@personal_data(True)`) renders `notifications.html` through `page()`. With nothing undismissed, it is empty. Otherwise:
    - "Notifications (<count>)";
    - the newest 5, each with its text (`|plain`), `YYYY-MM-DD HH:MM UTC`, and a Dismiss form (`method="post"`, `hx-post="/notifications/dismiss"`, `hx-target="#notification-list"`, `{{ csrf_field() }}`, hidden `notification`);
    - "and <n> more" when there are more.
  - **`POST /notifications/dismiss`** (`notification_dismiss`, `@personal_data(True)`).
    - Apart from `csrf_token`, the fields must be exactly one `notification` holding a ULID. Anything else is `MalformedForm`.
    - It calls `notify.dismiss`, then renders the area again in a read-only unit of work.
  - **`base.html`.** Inside `section#notifications` go `<p id="request-error" class="request-error" role="alert"></p>` and `<div id="notification-list" hx-get="/notifications" hx-trigger="load"></div>`.
  - **`htmx-config`** adds `"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},{"code":"[45]..","swap":true,"error":true,"target":"#request-error","swapOverride":"textContent"}]`. Every error body therefore lands as text, never parsed as HTML.
  - **The guard.** A request with exactly one `HX-Request: true` header and no live session gets a plain-text 401: "The dashboard is locked: run nextron open in a terminal to open it again." Other requests still get the HTML locked page.
  - **CSS.** Empty notices take no space, and are never `display: none`.
- **Tests** run as `test`, never post to a real Telegram chat, and make no real wait over 0.05 s per step.

**Never:**
- **Out of scope:**
  - internal email, Hot Lead, LinkedIn and call-prep alerts, and per-kind recipients;
  - other ops alert kinds (paused inboxes, backups, `unknown` OMs, polling);
  - `deleteMessage`;
  - metering (1.13);
  - the health panel and dead-job dismissal (1.15);
  - the doctor's required-setting check (1.14);
  - notification polling, and any script file.
- **No secrets anywhere they don't belong.** Never put the bot token or a chat id in a payload, an `events` row, a log record, an error or a response. Ops alert text carries IDs and counts only.
- **No new dependency, port or trigger.** jobqueue never imports notify (AD-3 DAG), and no notification type is added.
- **What not to edit.** Don't edit the vendored HTMX, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`. In `deferred-work.md`, remove only the Story 1.2 runtime adapter-boundary entry and the Story 1.8 sink-allowlist-read entry.

### Decisions made overnight (pending user review)

1. **The dashboard is an alert channel.** `notify.alert(..., channels)` takes `telegram` and `dashboard`. Telegram gets an `alerts` row and a job, and the dashboard gets a `notifications` row; each is unique per kind and subject. Internal email joins with its own story.
2. **The Telegram chat** is a new required setting, `alert_chat_id` (numeric only), read at send time.
3. **What each outcome does:**
   - **Retry:** 429, 5xx and connection failures before the request went out. The alert stays `pending`.
   - **Failed:** every other 4xx, plus a missing or malformed token or chat id.
   - **Unknown:** a timeout or a dropped connection after the request was sent, or a 2xx without a message id. Then one resend 60 s later. If the resend is also unknown, the alert stays `unknown`, and there is no third try.
4. **Alerts that weren't delivered show on the dashboard** (`alert_not_delivered`), because Telegram is the channel that failed.
5. **`job_dead` comes from a sweep, not from jobqueue.** jobqueue sits below notify in the AD-3 DAG, and the notification list is closed. The `ops` sweep runs right after a handler's job dies and on every 30 s keeper tick. It therefore covers all four ways a job dies, and a crash between the two commits, with each job alerted once.
6. **A dead `send_alert` job alerts only the dashboard,** so a broken Telegram can't feed itself.
7. **A `send_alert` job:**
   - realtime lane, priority 90;
   - `max_runtime` 3 min (HTTP 10 s connect and 15 s per phase, plus 5 s per database wait);
   - retry limit 5 (about 15 minutes of backoff).

   Delivery is at least once: a crash between Telegram's answer and the commit re-sends.
8. **The notification area loads once per page view** (no polling, to avoid the AD-2 read-lock item). It shows the count and the newest 5.
9. **Error notices need no script.** HTMX's `responseHandling` puts any 4xx or 5xx body, as plain text, in the area, and an expired session gets a readable plain-text 401. A request that gets no answer (the engine is down) shows nothing, and a notice stays until the page reloads.
10. **The ops alert text is exactly "Job dead: <type> <ULID>, attempts: <n>",** with no environment name.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Raise | `alert("job_dead", <ULID>, text, {telegram, dashboard})` in a transaction | one `pending` alert and its `requested` event; one `send_alert` job (`send_alert:<alert id>:0`, realtime, payload `{"v":1,"alert_id":…}`); one notification; all gone if the caller rolls back | N/A |
| Repeat | the same kind and subject again | no new row or job; a channel not yet present is added | N/A |
| Bad alert | kind `Job-Dead`; subject `a b`; blank text; 4,097 units; no channels; channels `"telegram"` | nothing written; transaction doomed | `InvalidAlert` |
| Sent | chat id set; Telegram 200 with `message_id` 42 | `sent`, `provider_message_id` `42`, a `delivered` event; the POST body is exactly `chat_id` and `text` | N/A |
| Sink | `test`, chat id not allowlisted | sink file line, `sent`, message id `NULL`, `alert_sent` with `sink=true`; no HTTP | N/A |
| Rate limit | 429, or a connect error | alert still `pending`; job `queued` after 30 s | job dead after 6 runs: dashboard-only `job_dead` |
| Refused | 400, 401, 403 or 404 | `failed`, `telegram_refused`; an `alert_not_delivered` notification | N/A |
| Unknown | `ReadTimeout` | `unknown`; resend job `:1` at +60 s; resend 200 → `sent`; resend timeout → stays `unknown`, `resent_at` set, notification, no further job | N/A |
| Not configured | `alert_chat_id` unset; token unset; token `abc` | `failed` (`setting_not_set`, `credential_not_set`, `telegram_bad_token`); no HTTP call for the first two | N/A |
| Dead job | a handler raises `ValueError` | Telegram alert and notification "Job dead: <type> <ULID>, attempts: 1"; a dead `send_alert` job gets the notification only | N/A |
| Sweep | dead by reap, by an unrunnable claim, or at release; or an alert lost to a crash | alerted on the next keeper tick, once | a sweep error is logged; the keeper goes on |
| Area | 7 undismissed, 2 dismissed | "Notifications (7)", the newest 5, "and 2 more"; Dismiss → 6 | bad id → 400 |
| HTMX error | a CSRF-refused `hx-post`; a 500; no session | the body's text lands in `#request-error`; no session gets the plain-text 401 | N/A |
| Token | every send and failure path | the token is in no log record, error, `last_error`, DB value or response | N/A |

</frozen-after-approval>

## Code Map

- **The registries and the wiring** (`nextron-engine/nextron/main.py`).
  - **What's there:** `ROLE_ADAPTERS` (empty), `build_role_adapter`, `AdapterContext`, `build_job_types()` (empty, no arguments) and `build_app`. The module docstring has the Jobs, Triggers and Role adapters paragraphs.
  - **What changes:** add a "Notify and alerts" paragraph, and update the Role adapters and Jobs paragraphs.
  - **The cycle.** The registry needs the handler, the handler needs `Notify`, `Notify` needs the queue, and the queue needs the registry. `lambda: notify` breaks it.
- **The jobqueue** (`nextron-engine/nextron/app/jobqueue/api.py`). `enqueue` is a `@command`. `fail` returns `JobStatus.DEAD`. The `job_dead` log lines stay.
  - **The repository** (`db/repos/jobqueue/jobs.py`): add `dead()`.
  - **The query** (`app/queries/jobqueue.py`): add `dead_jobs()`.
- **The runner** (`nextron-engine/nextron/jobs/runner.py`). `_finish` and `keep_once` are where the sweep goes. `tests/contracts/test_job_contracts.py:69-79`:
  - `JOBS_IMPORTS` gains `nextron/jobs/handlers.py: (("nextron.domain",), ())`, and the parametrized case at `:386-398` gains a row;
  - `:258-264` (`build_job_types()` is empty) becomes `send_alert` with its settings.

  Keep `handlers` a module: `:346` and `:366` `mkdir` `nextron/jobs/handlers` without `exist_ok`.
- **Patterns to copy:**
  - the model, `db/models/jobqueue.py` (checks and indexes; `models/__init__.py` imports each module);
  - the migration, `db/alembic/versions/0004_1_11_heartbeat.py` (`op.f(...)` for check names);
  - the repository, `db/repos/jobqueue/jobs.py` (fenced updates);
  - the machine, `domain/job.py`;
  - the service's events, `app/jobqueue/api.py` `_record`.
- **The adapter pattern** (`nextron-engine/nextron/adapters/google_oauth/__init__.py`). Copy `default_http_client`, the lazy `_client()` and the `except Exception: raise AdapterError(...) from None` boundary. httpx2 2.16.0 has the httpx exception names, and the `httpx2` logger stays at WARNING (`logconfig.SDK_LOGGERS`). `redact()` masks `/bot<digits>:<token>` (`domain/redaction.py:58`).
- **The sink** (`nextron-engine/nextron/adapters/sink/__init__.py`). `AlertSink` reads the allowlist through `main.read_sink_allowlist` in a fresh context, so it must be called between transactions.
- **Credentials** (`nextron-engine/nextron/ports/secrets.py:99`): `read_credential`. The credential key `telegram_bot` already exists (`domain/credentials.py`).
- **Settings** (`nextron-engine/nextron/domain/settings_schema.py`): `Key`, `SCHEMA`, `Rule`, `Section.ALERTS_AND_RECIPIENTS` (no setting yet) and `REQUIRED`. A blank save of an unset required key changes nothing (`app/setup/api.py:17`). Tests that list keys or post the whole settings form need the new key (`tests/unit/test_settings_schema.py`, `tests/contracts/test_settings_contracts.py:131`, and the Settings page tests that use `real_body`).
- **Web.**
  - **The routes** (`nextron-engine/nextron/web/app.py`): `page()`; `form_fields` and `MalformedForm` (`web/forms.py`). Every GET runs read-only through the route class (`web/routing.py`). `create_app` gains `notify`.
  - **The guard** (`web/guard.py:157-162`): the 401 branch.
  - **The layout** (`templates/base.html`) and `static/app.css` (`#notifications:not(:empty)`).
  - **Tests to update:**
    - `tests/dashboard.py` (`Dashboard.__post_init__` builds a `Notify`; a fake queue or registry is fine);
    - `tests/contracts/test_web_contracts.py:140-158` (the ordered paths gain `/notifications` and `/notifications/dismiss`);
    - `tests/unit/test_dashboard.py` (the exact meta JSON, and the `section#notifications` checks).
- **Test isolation.**
  - **`tests/live_guard.py`:** `ADAPTER_PACKAGES["telegram"] = Guarded(("default_http_client",))`, and `LIVE_CONSTRUCTORS` gains `("nextron.adapters.telegram", "default_http_client")`.
  - **`tests/contracts/test_test_isolation.py`:** `:398` (the package set) gains `telegram`, and the unclassified probe at `:411-447` moves to another name (`gmail`).
  - **`pyproject.toml` c5** gains `nextron.adapters.telegram`.
  - **`tests/fakes/alert.py`** (`InMemoryAlert`): extend it with queued outcomes as needed, and keep `.sent` and `.failure`.
- **Sink contracts.** `tests/contracts/test_sink_contracts.py:306-310` becomes `{TelegramAlert: SinkRole.ALERT}`, and `tests/unit/test_role_adapters.py:311-314` becomes the one `telegram` entry. Neither jobs, web, app nor `startup_checks` may reference the telegram package (`:514-519`).
- **The error contract** (`tests/contracts/test_error_contracts.py:351-406`, `_RaiseChecker`/`_leaks`): the taint tracking goes here.
- **Revision literals moving to `0005_1_12_notify`:**
  - `tests/contracts/test_persistence_contracts.py:72`, with the chain probes at `:670-684` shifted up by one;
  - `tests/unit/test_migrations.py:36`;
  - `tests/app/test_serve_process.py:241`;
  - `tests/contracts/test_source_tree.py` `FILES` (the new files) and its `layers` (`adapters/telegram`).
- **Secrecy.** `tests/app/test_credential_secrecy.py` (`database_leaks`, `FakeHunterAdapter`) is the pattern for the token test. Its DB scan covers the new tables automatically.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/alert.py`, `nextron/ports/alert.py` -- the machine, the channels, the checks, the texts and `DELIVERY_UNKNOWN`.
- [x] `nextron-engine/nextron/domain/settings_schema.py` -- `ALERT_CHAT_ID` and its rule.
- [x] `nextron-engine/nextron/db/models/notify.py`, `models/__init__.py`, `owners.py`, `pii_registry.py`, `alembic/versions/0005_1_12_notify.py`, `db/repos/notify/{alerts,notifications}.py` -- the tables, their maps, the migration and the repositories.
- [x] `nextron-engine/nextron/app/notify/api.py`, `app/queries/notify.py`, `app/queries/jobqueue.py`, `db/repos/jobqueue/jobs.py`, `app/ops/api.py` -- `Notify`, the queries and `DeadJobAlerts`.
- [x] `nextron-engine/nextron/adapters/telegram/__init__.py`, `adapters/__init__.py` docstring, `pyproject.toml` (c5) -- the adapter.
- [x] `nextron-engine/nextron/jobs/handlers.py`, `jobs/runner.py`, `jobs/__init__.py` docstring -- the handler and the sweep calls.
- [x] `nextron-engine/nextron/main.py` -- the registrations and the wiring.
- [x] `nextron-engine/nextron/web/app.py`, `web/guard.py`, `templates/base.html`, `templates/notifications.html`, `static/app.css` -- the area, dismiss, `responseHandling` and the plain-text 401.
- [x] `nextron-engine/tests/unit/test_alert_domain.py` -- the transition table exactly, and every `check_alert` refusal.
- [x] `nextron-engine/tests/unit/test_telegram_adapter.py` -- every outcome row, through `httpx2.MockTransport`:
  - the exact URL path and JSON body, with no `parse_mode`;
  - the token read on each send;
  - client construction refused in tests.
- [x] `nextron-engine/tests/app/test_notify.py` -- every matrix row from Raise to Not configured, on a temporary database with `FrozenClock` and the fake port. Include:
  - the events;
  - the unique indexes refusing raw duplicates;
  - idempotent re-runs of both jobs;
  - a send through a real `AlertSink` with real units and an allowlisted chat that is delivered at once, with no `sink_allowlist_unreadable`;
  - the port never called inside a transaction (assert `current()` holds no transaction during the call).
- [x] `nextron-engine/tests/app/test_dead_job_alerts.py` -- the Dead job and Sweep rows, through a real `Runner` (`run_one`, `keep_once`) with real job types and the registered `send_alert`.
- [x] `nextron-engine/tests/unit/test_notifications_area.py` -- the Area row and the dismiss refusals, through `Dashboard(units=units)`. Also:
  - the base layout's elements;
  - the exact meta JSON;
  - the guard's plain-text 401 for `HX-Request`, and HTML otherwise.
- [x] `nextron-engine/tests/contracts/test_adapter_boundary.py` -- the deferred per-port contract. For every adapter class whose `__init__` takes `http` (Telegram and Google OAuth), a `MockTransport` raises each `httpx2` exception class, `OSError`, `ssl.SSLError`, `ValueError` and `RuntimeError`, and answers 200 garbage, 400, 401, 403, 404, 429, 500 and 503. Only `AdapterError` may come out. A planted token appears in no `str`, `repr` or formatted traceback. A coverage check finds every such class.
- [x] `nextron-engine/tests/contracts/test_error_contracts.py` -- taint tracking. A local assigned from caught-exception text (`m = str(exc)`, `f"{exc}"`, `exc.args`, `repr(exc)`), directly or through another tainted local, and then passed into the error is a problem. Add probes for both, and for allowed attributes.
- [x] `nextron-engine/tests/app/test_telegram_secrecy.py` -- the planted bot token, saved through `Credentials`, sent and failed on every path. It reaches no log record (before redaction), error, `last_error`, DB value, file or response.
- [x] The existing tests in the Code Map; `nextron-engine/README.md`: an "Alerts and notifications" section (setup: the bot token, `alert_chat_id`, the allowlist outside prod; the outcomes; `job_dead`; the area and error notices), and migration `0005` under Database.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- remove the two entries this story closes.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `serve`, when it starts, then the database is at `0005_1_12_notify`, `send_alert` is registered, no adapter or HTTP client has been built, and no Telegram request is made.

## Implementation Notes

Choices the spec left open, made during implementation:

- **Resends.** A resend that fails in prepare (the chat id unset or unreadable) fails the alert without setting `resent_at`. Only a resend that reaches apply sets it.
- **The runner.** `Runner.sweep_dead_jobs()` is public, so tests can drive it. It runs in its own unit of work, does nothing without `dead_jobs`, and logs `dead_job_alerts_failed` on any error.
- **Notify.**
  - `Alerted.created` lists the channels it added, in `Channel` order.
  - Logs are written after each transaction commits.
  - A non-ULID `alert_id` is nothing to do.
- **The domain.** `domain/alert.py` also holds:
  - `text_units` (UTF-16 code units);
  - `is_alert_subject`;
  - `not_delivered_text(kind, alert_id, code, maybe=False)`.

  `check_alert` refuses a bare `Channel` member too, because it is a `str`.
- **The Telegram adapter.**
  - `NOT_SENT` is the tuple of httpx2 failures that mean nothing reached Telegram.
  - A 3xx is `telegram_refused`.
  - The client (10 s connect, 15 s per other phase, no redirects) is built under a lock on the first send.
- **The dashboard.**
  - The area lists `AREA_LIMIT` (5) items, with times as `%Y-%m-%d %H:%M UTC`.
  - `submitted_notification` raises `MalformedForm` (`wrong_fields` or `repeated_field`).
  - The dismiss form targets `#notification-list` with HTMX's default `innerHTML` swap.
- **Tests.**
  - `InMemoryAlert` gains `outcomes` (scripted deliveries or exceptions), `attempts` and an `observer` hook. The observer is how the fence-miss and "no transaction during the call" tests act during the port call.
  - `tests/dashboard.py` builds a real `Notify` on the real job types, with an `InMemoryAlert` as its Telegram port.
- **Existing tests changed beyond the Code Map:**
  - `tests/app/test_untrusted_rendering.py`, `tests/unit/test_credentials_page.py`, `tests/unit/test_google_page.py` and `tests/unit/test_settings_page.py`, for the layout's new `#request-error` and `#notification-list`;
  - `tests/app/test_serve_process.py` now also asserts that a served engine raises no alert and creates no sink folder;
  - `tests/conftest.py` and `tests/live_guard.py` docstrings;
  - in `test_test_isolation.py`, the unclassified probe is renamed `gmail` and the stale-constructor probe `gmail_api`.
- **httpx2 logs the request URL, bot token included, at INFO.** `serve` keeps the `httpx2` logger at WARNING (`logconfig.SDK_LOGGERS`), and `redact()` masks `/bot<token>`. The secrecy test sets the SDK logger levels as `serve` does.

Review fixes (pass 1, P1–P7; see the Review Triage Log):
- **P1 and P2.** `tests/unit/test_runner_lifespan.py` gains two tests on the real `main.build_app`, both checked to fail when the wiring is broken:
  - **P1.** A dead job (a queued job of an unregistered type, claimed) gets its `job_dead` notification and a pending Telegram alert from the built runner's keeper tick.
  - **P2.** The built `Notify` builds its port once, through `build_role_adapter("telegram", AdapterContext(config, secrets, clock), units=units)`, on the first send and not at build time. The message is delivered, and the alert ends `sent`.
- **P3.** `test_a_fence_miss_changes_nothing` (`tests/app/test_notify.py`) covers delivered, refused, first unknown and resend unknown. No event, notification or job is added, and `resent_at` stays `NULL`.
- **P4.** `test_adapter_boundary._exception` passes the planted message to every class that takes `*args`. A new test asserts every raised exception carries it, except the four httpx2 stream errors that take no argument (`NO_TEXT`).
- **P5.** The taint check seeds a nested scope with the enclosing caught and tainted names (`_Taint(known=...)`). The `NESTED_TAINT` probe gives exactly one problem.
- **P6.** README step 2:
  - which chats have positive, shorter negative or `-100` ids;
  - an upgraded group gets a new id;
  - a PowerShell `Read-Host` and `getUpdates` recipe that keeps the token out of a browser.
- **P7.** The README says a database error in a send is not retried: the job dies (a dashboard-only `job_dead`), and the alert stays `pending`.

**Final verification (after pass 1).**
- `uv run pytest -q`: 3416 passed, 2 skipped (`ai_eval` and the symlink test), in 173 s.
- `uv run lint-imports`: 29 kept, 0 broken.
- Before the review fixes: 3409 passed, 2 skipped, and 29 kept.
- **One flaky run.** The first full run on the patched tree had one failure: the existing `test_job_runner.py::test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper[1-1]`, which compares every live `nextron-job-*` thread. That file then passed 38 of 38 three times on its own, and the next full run passed everything. A thread from an earlier test that hadn't finished yet is the likely cause, and it is unconfirmed. The run gave no traceback.

Not verified here:
- a real Telegram send (no bot token exists yet);
- the HTMX `responseHandling` notice in a real browser (only the exact config JSON and the plain-text error bodies are tested).

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (14 findings), EC = edge-case hunter (9), VG = verification gap (3 gaps).
- **Counts:** 26 raw findings, 22 after deduplication (BH2 with EC1, BH4 with EC7, BH5 with EC6, BH12 with EC3).
- **Outcomes:**
  - 7 entries patched (P1–P7), covering 8 raw findings: 5 test gaps and 2 doc corrections;
  - 1 deferred: BH6, retention of the new tables;
  - 14 rejected.
- **No loopback.** No finding is an intent gap or a bad spec. BH1, BH3, BH7 and BH9 are rejected only because their fixes would change the approved spec (overnight decisions 3, 4 and 9, and the outcome table). Mac can revisit them with the overnight decisions.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | VG1 (gap) | Nothing checks that the runner `main.build_app` builds gets the dead-job sweep. Dropping `dead_jobs=DeadJobAlerts(notify)` leaves the suite green, and a served engine would never raise `job_dead`. | medium | Pre-verified: every `Runner(..., dead_jobs=...)` in tests is built by the test itself, and no `build_app` test creates a dead job. The fix is test-only. | P1 |
| B | VG2 (gap) | Nothing checks that `build_app`'s `Notify` routes Telegram to `build_role_adapter("telegram", <its context>, units=units)`. A wrong or missing factory leaves the suite green, and served alerts would never be delivered. | medium | Pre-verified: `test_runner_lifespan.py` only asserts the factory is never called, and every other test builds its own `Notify`. The fix is test-only. | P2 |
| C | VG3 (gap) | Only the delivered branch of the fenced apply has a fence-miss test. The refused, first-unknown (resend job) and resend-unknown (notice) branches don't, so a fence miss could write a false event, notice or resend unseen. | low | Pre-verified: `test_a_fence_miss_changes_nothing` drives only a delivery. The fix is test-only. | P3 |
| D | EC8 | `test_adapter_boundary._exception` builds `OSError`, `ssl.SSLError`, `ValueError`, `RuntimeError` (and the httpx2 stream errors) with no message, because their `__init__` has no `message` parameter. Their secrecy check is vacuous. | low | Verified: the signature is `(self, /, *args, **kwargs)`, so the `"message" not in parameters` branch returns `cls()`. The spec requires the planted secret in what the transport raises. The fix is test-only and direct. | P4 |
| E | EC9 | The taint check scans a nested function with an empty caught list, so `except OSError as exc: def later(): detail = str(exc); raise AdapterError(..., detail)` is not flagged. The docstring says nested functions are covered. | low | Verified in `_RaiseChecker._scope`: `tainted_locals(node.body)` starts with no caught or enclosing tainted names. The fix is test-only and direct: seed the nested scan with the enclosing caught and tainted names. | P5 |
| F | BH13 | The README's chat-id step says "a group's starts with -100", which is true only for supergroups and channels. It doesn't say how to find the id without putting the token in a browser URL, or that a group upgraded to a supergroup gets a new id. | low | Verified in the README's "Alerts and notifications" step 2. Basic groups have shorter negative ids, and `getUpdates` in a browser keeps the token in history. The doc fix is direct. | P6 |
| G | BH2, EC1 | A database error (`database is locked`) in `Notify.send`'s prepare or apply ends the `send_alert` job at once. `JobQueue.fail` retries only a retryable `AdapterError`, so the alert stays `pending`, even if Telegram already took it. The README's "Delivery is at least once" is overstated for that case. | low | Verified. Transactions are short and the busy timeout is 5 s, so it is rare. The job's death still shows a `job_dead` notice. Retrying database errors is the same trade-off rejected in Story 1.10 (VG4); the AC says any other exception makes the job dead. The README gets a direct correction naming the case. | P7 |
| H | BH6 | `alerts` and `notifications` (both `text` columns `redact`) and dead jobs have no retention. If notifications are ever purged while their dead jobs remain, the sweep alerts those jobs again. | low | Real but future: retention (8.12) and erasure (3.14) are their stories' work. Erasure already reaches `redact` columns through the PII registry. Recorded so the retention story keeps `job_dead` subjects as long as their dead jobs. | defer |
| I | BH1 | An alert whose `send_alert` job dies stays `pending`, and the dead job's notice names the job, not the alert. | low | True, and specified: decision 3 and the Rate limit row. The dead job's dashboard notice and its payload's `alert_id` remain. Marking the alert `failed` on job death adds a cross-service path the spec doesn't have. | reject |
| J | BH3 | Every refused status (400, 401, 403, 404) is the one code `telegram_refused`, so Mac can't tell a bad token from a bad chat id. | low | True. The spec's outcome table fixes `telegram_refused` for every other status, and the logs carry the code only. Per-status codes would edit the approved spec. | reject |
| K | BH4, EC7 | No grouping or flood control: a burst of dead jobs sends one Telegram message each, and 429's `retry_after` is ignored. | low | A burst needs many dead jobs at once (only `send_alert` exists today). The single realtime thread sends one at a time, and backoff spreads the retries over about 15 minutes. The fix adds aggregation or header handling. | reject |
| L | BH5, EC6 | The sweep reads every dead job (all columns) and every `job_dead` subject every 30 s, and dead jobs are never purged. | low | True, but dead rows are few on one PC, and dismissal (Story 1.15) removes them. A cross-owner anti-join isn't allowed (AD-3), so the fix is more than a direct correction. | reject |
| M | BH7 | Until `alert_chat_id` is set, each dead job adds two notices (`job_dead` and `alert_not_delivered`), and there is no test-alert action. | low | True by decisions 4 and 5. The second notice says `setting_not_set`, which is what Mac needs to fix. A test-alert action is a new feature. | reject |
| N | BH8 | The list loads into the `aria-live` section on every page, so a screen reader re-reads it, and a `role=alert` sits inside a polite region. | low | The `aria-live` section is Story 1.4's pinned layout. No screen-reader user is known, and the fix restructures the area. | reject |
| O | BH9 | An old error notice stays after a later success. | low | Decision 9 ("a notice stays until the page reloads"). Clearing it needs script or an out-of-band swap on every response. | reject |
| P | BH10 | The taint check misses `parts.append(str(exc))` and attribute storage (`self._x = str(exc)`, which also taints `self` for the function). | low | Contract tests guard against mistakes, not evasion (Story 1.10 EC11). The assignment forms the deferred item named are covered, and a false positive on `self` fails loudly. The fix adds call tracking. | reject |
| Q | BH11 | If the first send job re-runs after a crash in the ms window after its apply commit, it re-sends at once, skipping the 60 s delay. | low | True. There is still exactly one resend (`resent_at`), so only the delay is lost, after a crash in a millisecond window. The fix adds a discriminator check. | reject |
| R | BH12, EC3 | `Notify` doesn't check that every delivery channel has a port factory. | low | Only `main.build_app` and the test helper build it, both with Telegram, and a missing factory fails loudly (the job dies). Precedent: Story 1.11 EC5 and EC6. | reject |
| S | BH14 | `raise ... from None` leaves `__context__` holding the httpx2 error, whose request URL has the token. | low | Nothing in the engine renders a suppressed context: `logging.Formatter.formatException` and the excepthook honour `__suppress_context__`, and no code reads `__context__`. `redact()` masks `/bot<token>` anyway. Fixing it restructures both HTTP adapters. | reject |
| T | EC2 | `default_http_client()` raising (a bad proxy environment variable) escapes `send` as a non-`AdapterError`. | low | It needs a broken install. The job dies loudly with a `job_dead` notice and the next send retries the build. Google OAuth has the same shape (pre-existing). Wrapping it would also turn the tests' `LiveAdapterInTests` into an `AdapterError`. | reject |
| U | EC4 | An `alerts.channel` value outside `Channel` raises `ValueError` in prepare. | low | Only a hand edit of the database writes one: `Notify.alert` writes `Channel` values only. Precedent: Story 1.10 EC3 and EC4. | reject |
| V | EC5 | One dead job whose alert raises (`InvalidAlert` from its type) dooms every sweep. | false | `jobs.type` is always a registered `verb_noun` name (`enqueue` looks it up), at most 48 characters, and the id a ULID, so `check_alert` passes the `job_dead` text. Only a hand edit could break it. | reject |

## Design Notes

```python
@use_case
def send(self, alert_id: str) -> None:
    with current().transaction():          # prepare: what to send, and to whom
        plan = self._prepare(alert_id)     # None: nothing to do, or failed already
    if plan is None:
        return
    outcome = self._deliver(plan)          # port call between transactions; retryable re-raises
    with current().transaction():          # apply: fenced on plan.status
        self._apply(plan, outcome)         # sent | unknown (+ resend job) | failed (+ notice)
```

**Why a sweep instead of a callback from jobqueue:** a job dies in four places (`fail`, the reap in `keep`, an unrunnable `claim`, and `release`). jobqueue can't call notify (AD-3). The sweep reads only committed `dead` rows and relies on the alerts' uniqueness, so it needs no new return values, survives a crash between commits, and alerts each job once.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` and the symlink test skipped).
- `uv run lint-imports` -- expected: every contract (29) KEPT.
