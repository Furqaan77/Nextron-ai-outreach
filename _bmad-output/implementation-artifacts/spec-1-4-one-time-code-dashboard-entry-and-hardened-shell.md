---
title: 'Story 1.4: Enter the dashboard through a one-time code with a hardened shell'
type: 'feature'
created: '2026-11-17'
status: 'done'
baseline_commit: '08aa1872a1cc7c7bab85ef01b08700668e0f8014'
route: 'dispatch'
review_loop_iteration: 0
story_key: '1-4-enter-the-dashboard-through-a-one-time-code-with-a-hardened'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The dashboard on `127.0.0.1` answers anyone: another website (through the browser), another local process, or a forged `Host` (DNS rebinding). It has no session, no security headers and no page layout, and the engine has no way to store a secret (AD-12, AD-13, AD-14, NFR-18, NFR-19).

**Approach:** Add the `SecretsPort` with its keyring adapter and an in-memory fake. `nextron open` writes a one-time code to keyring and opens the browser at `/enter?t=<code>`. The app consumes the code and sets an in-memory session cookie. One ASGI guard checks `Host`, then the session, and stamps the security headers on every response. Add a base layout with vendored HTMX, hardened through `<meta name="htmx-config">`. The acceptance criteria are Story 1.4 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **`SecretsPort`** (`nextron/ports/secrets.py`, `runtime_checkable`) has three methods:
  - `read(name) -> str | None`;
  - `write(name, value) -> None`;
  - `delete(name) -> None`, which does nothing when the entry is missing.

  The module also holds `MAX_SECRET_LENGTH = 1280`, `SECRET_TOO_LONG = "secret exceeds 1,280 characters"`, `secret_length(value)` (counted in UTF-16 code units) and `SECRET_NAME_PATTERN` (`^[a-z][a-z0-9_]*(/[A-Za-z0-9._@+-]+)*$`, at most 200 characters).
- **Keyring adapter** (`nextron/adapters/secrets/__init__.py`, `KeyringSecrets(env, *, backend=None)`).
  - **Service and backend:** the service is `nextron-engine/<env>`. The default backend is an explicitly constructed `keyring.backends.Windows.WinVaultKeyring()`, never `keyring.get_keyring()`.
  - **Validation:** it validates before any backend call, and every failure is `AdapterError(retryable=False)`:
    - a bad name: `secret_name_invalid`;
    - a value that isn't a non-empty `str`: `secret_invalid`;
    - `secret_length(value) > 1280`: `secret_too_long`, with the message exactly `SECRET_TOO_LONG`.
  - **Backend failures:** any backend exception becomes `AdapterError(False, "keyring_failed", "<operation> of secret <name> failed")`, never carrying the exception's text. `keyring.errors.PasswordDeleteError` on `delete` means the entry is missing, so it's a no-op.
  - **Fake:** `tests/fakes/secrets.py` `InMemorySecrets(env="test", store=None)` behaves the same, error codes included. Its `store` is keyed `(service, name)` and can be shared between instances.
- **One-time code, domain logic** (`nextron/domain/open_code.py`, stdlib only, takes `now`).
  - **Types and constants:** `OPEN_CODE_TTL = 5 min` and `CodeCheck(StrEnum)` = `accepted`, `absent`, `malformed`, `expired`, `mismatch`, `unavailable`.
  - **`encode(code, issued_at)`** returns compact JSON: `{"code": ..., "issued_at": "YYYY-MM-DDTHH:MM:SS.ffffffZ"}`.
  - **`check(stored, presented, now)`**, in this order:
    1. `None` → `absent`.
    2. Not exactly those two string fields, a code that isn't 43 base64url characters, or a bad time → `malformed`.
    3. `now < issued_at` or `now - issued_at > TTL` → `expired`. Exactly 5 minutes is still valid.
    4. Otherwise, `hmac.compare_digest` on the UTF-8 bytes (`errors="replace"`) → `accepted` or `mismatch`.

  A naive `now` or `issued_at` raises `ValueError`.
- **One-time code, app service** (`nextron/app/ops/api.py`): `OPEN_CODE_SECRET = "session/open_code"` and `DashboardEntry(secrets, clock, *, new_code=lambda: secrets.token_urlsafe(32))`.
  - `issue_code()` writes `encode(code, clock.now())` and returns the code; an `AdapterError` propagates.
  - `withdraw_code()` deletes the entry.
  - `consume_code(presented) -> CodeCheck` holds a `threading.Lock`:
    - A failed read is `unavailable`.
    - `accepted` deletes first; if that delete fails, the result is `unavailable` and no session is created.
    - `expired` and `malformed` delete on a best-effort basis.
    - `mismatch` and `absent` delete nothing.

  These are not `@use_case`s, because they touch no database.
- **Clock adapter:** `nextron/adapters/clock/__init__.py` `SystemClock.now()` returns `datetime.now(UTC)`. Both new adapter packages join contract 5's `source_modules`.
- **`nextron open`** (`main.open_dashboard(*, secrets=None, clock=None, engine_running=startup_checks.engine_is_running, browser=webbrowser.open, out=None, err=None) -> int`):
  1. Bootstrap error → exit 2.
  2. If `engine_running(HOST, port)` (a TCP connect with a 1 s timeout) is false: stderr `engine not running`, exit 1, and no secrets object is built or touched.
  3. `issue_code()`. On `AdapterError`: stderr `could not store the one-time code in Windows Credential Manager`, exit 1.
  4. Call `browser("http://127.0.0.1:<port>/enter?t=<code>")`. If it returns false or raises: `withdraw_code()` (best effort), stderr `could not open the default browser`, exit 1.
  5. Otherwise: stdout `opened the dashboard in your browser; the link works once, within 5 minutes`, exit 0.

  It never prints or logs the code. `nextron/__main__.py` gains the `open` subcommand.
- **Web app.** `create_app(*, env, version, port, entry, monotonic=time.monotonic)`; `main.build_app(config, *, secrets=None, clock=None)` builds the `DashboardEntry` from `KeyringSecrets(config.env)` and `SystemClock()` by default.
  - **Routes**, every one a sync `def`:
    - `GET /enter` (name `enter`):
      - exactly one `t` and `consume_code` returns `accepted`: create a session, then 303 to `/` with `Set-Cookie: nextron_session=<token>; HttpOnly; Path=/; SameSite=Strict` and `Cache-Control: no-store`;
      - anything else: 401 with the locked page.
    - `GET /` (`home`): the base layout.
    - `GET /health` (`health`): unchanged JSON.
    - `GET /static/{name}` (`static`): only `htmx-2.0.11.min.js` (`text/javascript; charset=utf-8`) and `app.css` (`text/css; charset=utf-8`), read once at creation; any other name is 404.
  - **Logging:** `/enter` logs `dashboard_session_opened` (INFO) or `dashboard_entry_refused` (WARNING, `reason` = the `CodeCheck` value), never the code or the token.
- **Sessions** (`nextron/web/sessions.py`): `SESSION_COOKIE = "nextron_session"` and `SessionStore` (thread-safe; `create()` returns a `token_urlsafe(32)`; it stores SHA-256 digests only). The store is created inside `create_app`, so a restart drops every session. There's no idle expiry.
- **Guard** (`nextron/web/guard.py`, a pure ASGI middleware added with `app.add_middleware`):
  - **Order, for `http` scopes:**
    1. There must be exactly one `Host` header, equal byte for byte to `127.0.0.1:<port>` or `localhost:<port>`. Otherwise: 400 `text/plain` `Bad request: unexpected Host header`, before routing.
    2. A path other than exactly `/enter` without a valid `nextron_session` cookie: 401 with the locked page.
    3. Otherwise the app runs.
  - **Headers on every response through it** (set, replacing any the handler set):
    - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'`;
    - `X-Frame-Options: DENY`;
    - `Referrer-Policy: no-referrer`;
    - `X-Content-Type-Options: nosniff`.

    It removes every `access-control-*` header. It adds `Cache-Control: no-store` unless the matched endpoint declared `personal_data=False`, so undeclared, unmatched, 400, 401, 404 and 500 responses all get it.
  - **Errors and other scopes:** an exception raised before the response starts produces a 500 `text/plain` `Internal error` carrying every header, then the exception is re-raised so Uvicorn logs it. `websocket` scopes are closed; `lifespan` scopes pass through.
- **Personal-data declaration** (`nextron/web/routing.py`): the `@personal_data(shows: bool)` decorator, placed under the route decorator, plus `undeclared_routes(app) -> list[str]`. `create_app` raises `RuntimeError` naming any undeclared route. The four routes declare `False`.
- **Layout** (`nextron/web/templates/`, a Jinja2 `Environment` with `FileSystemLoader` and `autoescape=True`).
  - **`base.html`:**
    - **Head:** the `htmx-config` meta with exactly `{"allowEval":false,"allowScriptTags":false,"selfRequestsOnly":true,"historyCacheSize":0,"includeIndicatorStyles":false}`, before `<script src="/static/htmx-2.0.11.min.js" defer></script>`, and `<link rel="stylesheet" href="/static/app.css">`.
    - **Body:** a header whose brand links to `/`; a `<nav>` built from `NAV_SECTIONS` = (`queue_page` Queue, `signal_inbox_page` Signal Inbox, `pipeline_page` Leads/Pipeline, `funnel_page` Funnel, `settings_page` Settings, `health_page` Health), showing each section only when the app has a GET route of that name, in this order; the notification area `<section id="notifications" aria-live="polite">`; and `<main>`.
  - **`home.html`** extends the base layout.
  - **`locked.html`** is a standalone 401 page: "Run `nextron open` in a terminal to open the dashboard." It has no assets and no data.
  - **No inline code:** no template has an inline `<script>`, a `<style>`, a `style=` or an `on*=` attribute. `app.css` has no `@import` and no external `url()`.

**Never:**
- None of the work owned by later stories: CSRF tokens, `Origin` and `Sec-Fetch-Site` checks, POST routes, the read-only unit of work, or the `|safe` allowlist (1.5); settings (1.6); the Credentials panel or `meta/<name>` (1.7); sink adapters (1.8).
- No database tables, migrations or units of work in handlers.
- No CORS middleware, CDN or third-party assets, or `async def` route handlers. An ASGI `async def __call__` is fine.
- Tests never touch the real Windows Credential Manager or open a real browser, and nothing runs `nextron open` against a live engine.
- Don't edit the vendored HTMX file, or anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

### Decisions made overnight (pending user review)

1. **`/health` needs a session too.** The AC covers "any route other than `/enter`", and AD-13 says "every request". So `curl.exe http://127.0.0.1:8766/health` now returns 401. The supervisor uses the heartbeat file, not `/health`.
2. **The navigation shows only sections whose page exists.** None exist yet, so it is empty, and no link leads to a 404. Each later story names its page route (`settings_page`, …) and its link appears.
3. **The "dashboard home" is `/` for now.** UX-DR2 says the redirect goes to the Queue, which doesn't exist yet. The Queue story makes `/` show the Queue.
4. **Sessions end only when the engine restarts** (or when the browser drops its session cookie). There is no idle timeout.
5. **A wrong code leaves the pending code in place,** so a junk `/enter` from another website can't cancel Mac's link. Expired and malformed codes are deleted.
6. **If the browser can't be opened, the code is withdrawn and never printed.** Mac reruns `nextron open`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Write a secret | 1,280 BMP chars; 1,281; 640 emoji (1,280 units) / 641 | stored / refused / stored / refused | `secret_too_long`, nothing written |
| Namespaces | prod writes `x`; dev reads `x` (shared backend) | `None` | N/A |
| Backend fails | backend raises `KeyringError`, `OSError`, `RuntimeError` | `AdapterError` `keyring_failed`, without the exception's text | N/A |
| Open, engine down | nothing listening on the port | stderr `engine not running`, exit 1, fake secrets untouched | N/A |
| Open, engine up | probe true | `session/open_code` holds a 43-char code and `issued_at`; browser gets `http://127.0.0.1:<port>/enter?t=<code>` | browser false: code withdrawn, exit 1 |
| Enter | fresh code, age ≤ 5:00 | 303 to `/`, cookie set, code deleted | N/A |
| Enter, refused | reused, expired (5:00.000001), future-dated, wrong, non-ASCII, two `t`, no `t`, keyring down | 401 locked page, no `Set-Cookie`, no session | wrong: code kept |
| Race | two threads consume one code | exactly one `accepted` | N/A |
| No session | `/`, `/health`, `/static/app.css`, `/nope`; bad or old cookie | 401 locked page, no data | N/A |
| Restart | cookie from app instance A sent to instance B | 401 | N/A |
| Host | `evil.example`, `127.0.0.1`, `127.0.0.1:1`, `localhost.:<p>`, `[::1]:<p>`, missing, two `Host` | 400, and the handler never ran (spy) | N/A |
| Headers | 200, 303, 400, 401, 404, 500; a request with `Origin` | the four headers once each, exact; no `access-control-*` | N/A |
| no-store | declared `True` / `False` / undeclared test routes; 401/404 | yes / no / yes; yes | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/web/app.py` -- the current factory (`/health`, docs off); extend it with the routes, guard, templates and declarations, and keep the `monotonic` injection.
- `nextron-engine/nextron/main.py` -- `build_app`, `create_app_from_environment` (the reload worker) and `run_uvicorn`; add `open_dashboard` and update the docstring. Keep `serve`/`doctor` unchanged.
- `nextron-engine/nextron/__main__.py` -- the argparse CLI; add `open`.
- `nextron-engine/nextron/startup_checks.py` -- add `engine_is_running(host, port)` (connect, don't bind: a bind probe could steal the port from a starting engine).
- `nextron-engine/nextron/ports/clock.py`, `ports/errors.py` -- the Protocol style and `AdapterError(retryable, code, message)`.
- `nextron-engine/nextron/app/ops/api.py`, `app/ops/__init__.py` -- empty stubs; `ops` (an entry service) may import `nextron.ports` and `nextron.domain`.
- `nextron-engine/pyproject.toml` -- contract 5 (`c5-anthropic-only-in-ai`): add `nextron.adapters.clock` and `nextron.adapters.secrets`, and refresh its comment. `keyring` is already allowed only in adapters (c4).
- `nextron-engine/.venv/Lib/site-packages/keyring/backends/Windows.py` -- `WinVaultKeyring`; `delete_password` raises `PasswordDeleteError` when the entry is missing; the blob is UTF-16 (2,560 bytes = 1,280 units).
- `nextron-engine/tests/asgi.py` -- the in-process ASGI driver (no httpx). Generalise it to custom headers, a query string and a header list (`get` stays).
- `nextron-engine/tests/unit/test_health.py`, `tests/unit/test_serve.py:181` -- they call `/health` without a session; enter first, or expect 401 (reload factory).
- `nextron-engine/tests/app/test_serve_process.py` -- the `running_engine` readiness loop treats any HTTP answer (`HTTPError` included) as up; `/health` now returns 401.
- `nextron-engine/tests/contracts/support.py` (`copy_with_probe`), `test_error_contracts.py` (the adapter `raise` rules the keyring adapter must pass), `test_contract_coverage.py` (contract 5 lists every non-AI adapter), `test_source_tree.py` (`FILES`), `test_log_event_names.py` (fewer than 32 characters).
- `nextron-engine/.venv/Lib/site-packages/starlette/routing.py:262,702` -- the router sets `scope["endpoint"]` and `scope["route"]`, which the guard reads when the response starts.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/ports/secrets.py`, `nextron/adapters/secrets/__init__.py`, `nextron/adapters/clock/__init__.py`, `tests/fakes/secrets.py`, `pyproject.toml` (contract 5) -- the port, the adapters and the fake -- AD-9, AD-12.
- [x] `nextron-engine/nextron/domain/open_code.py`, `nextron/app/ops/api.py` -- `encode`/`check`/`CodeCheck`; `DashboardEntry` -- the one-time code.
- [x] `nextron-engine/nextron/web/sessions.py`, `web/routing.py`, `web/guard.py`, `web/app.py`, `web/templates/base.html`, `home.html`, `locked.html`, `web/static/app.css`, `web/__init__.py` (docstring) -- the hardened shell -- AD-13.
- [x] `nextron-engine/nextron/main.py`, `nextron/__main__.py`, `nextron/startup_checks.py` -- `build_app` wiring, `open_dashboard`, `open`, `engine_is_running`.
- [x] `nextron-engine/tests/unit/test_secrets.py` -- the port behaviours parametrised over `KeyringSecrets` (a test `KeyringBackend` in a dict) and `InMemorySecrets`; dev/prod isolation; the default backend is `WinVaultKeyring` with service `nextron-engine/<env>` (constructed only, never called).
- [x] `nextron-engine/tests/unit/test_open_code.py`, `tests/unit/test_dashboard_entry.py`, `tests/unit/test_open_command.py` -- the matrix rows for the code and the command, with the CLI parser.
- [x] `nextron-engine/tests/unit/test_dashboard.py` -- the enter, session, Host, header, no-store, restart and layout rows (the meta JSON exact; every `script`/`link` local; nav order with a dummy `settings_page` route; the notification area present).
- [x] `nextron-engine/tests/contracts/test_web_contracts.py` -- each with a live proof: no `async def` route handler (runtime over `app.routes`, plus AST over `nextron/web`); `undeclared_routes` empty; the templates have no inline script or style; autoescape is on.
- [x] `nextron-engine/tests/asgi.py`, `tests/unit/test_health.py`, `tests/unit/test_serve.py`, `tests/app/test_serve_process.py`, `tests/contracts/test_source_tree.py` -- updates per the Code Map. Process test: `/health` with no session gives 401 and the headers; a foreign `Host` gives 400; `nextron open` on a free port gives exit 1 `engine not running`.
- [x] `nextron-engine/README.md` -- "Open the dashboard" (`nextron open`, the 5-minute single-use link, sessions end at restart), `/health` now needs a session, and secrets in Credential Manager under `nextron-engine/<env>`.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `NEXTRON_ENV=dev` and `serve` running, when `curl.exe -i http://127.0.0.1:8766/health` runs, then it returns 401 with the locked page and the four security headers; `-H "Host: evil.example"` gives 400.

## Implementation Notes

- **Shared validation.** `nextron/ports/secrets.py` also holds `is_secret_name`, `check_secret_name` and `check_secret_value` (raising the spec's `AdapterError`s) plus the four error-code constants, so `KeyringSecrets` and `InMemorySecrets` validate identically. The fake adds `failing` (operations that raise `keyring_failed`) and `calls`, for the keyring-down and "untouched" rows.
- **Zero or two `t`.** `/enter` doesn't call `consume_code`, so it touches neither keyring nor the pending code, and logs `dashboard_entry_refused` with `reason=mismatch`, the closest `CodeCheck` value.
- **`no-store` on handler responses.** `/enter` (303 and 401) and the static 404 set `Cache-Control: no-store` themselves, because those endpoints declare `False` and the guard won't add it.
- **Set-Cookie** is written by hand, so it reads exactly `nextron_session=<token>; HttpOnly; Path=/; SameSite=Strict` (Starlette's `set_cookie` would write `SameSite=strict`).
- **Session cookie lookup.** The guard accepts the request when any `nextron_session` value across its `Cookie` headers is a live session.
- **Helpers.** `main.build_entry` builds the `DashboardEntry` for `build_app` and `open_dashboard`. `web.app.template_environment()` and `nav_links()`, and `web.routing.require_declared()`, exist so the contract tests can prove each rule. Test helpers: `tests/asgi.py` `request()`/`Reply`, plus `tests/dashboard.py`.
- **Known limitations, documented in the README.**
  - Cookies aren't isolated by port, so `dev` and `prod` share the one `nextron_session` cookie on `127.0.0.1`. Opening one environment's dashboard ends the browser's session with the other.
  - The browser sends that cookie to every web server on `127.0.0.1`, not only to the engine.
  - Any process running as the same Windows user can read Credential Manager, and so the pending code.
- **Keyring persistence (review P3).** `adapters.secrets.default_backend()` builds `WinVaultKeyring()` with `persist = "local machine"` (`CRED_PERSIST_LOCAL_MACHINE`), so secrets don't roam with a roaming profile; an injected backend is left alone. `WinVaultKeyring` keeps one name at the target `nextron-engine/<env>` and the others at `<name>@nextron-engine/<env>`.
- **Contract checks (review P5, P6).**
  - The template checker flags `hx-on*`/`data-hx-on*` attributes and `js:`/`javascript:` values in `hx-vals`, `hx-vars` and `hx-headers`.
  - It requires every `src`, `srcset` candidate, `data` and `poster` URL to be a local path, and strips tab, newline and CR before matching URLs.
  - The async-handler check reads the decorators of every `async def` and covers `add_api_websocket_route`.
- **Other review fixes.**
  - `tests/unit/test_system_clock.py` checks that `SystemClock` is UTC-aware.
  - `app.css` styles `#notifications:not(:empty)`, so the `aria-live` region is never hidden.
  - The `nextron open` process test holds its port (exclusive, not listening) for the whole call.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).** Sources: BH = blind hunter, EC = edge-case hunter, VG = verification gap. 23 raw findings, 21 after deduplication (rows 2 and 13 each merge two), grouped as patches P1–P8. No intent_gap or bad_spec entries, so there is no loopback.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | VG | `SystemClock.now()` has no test, so a naive-time regression would break `nextron open` and every `/enter` while the suite stays green | medium | Filed gap, pre-verified: the reviewer patched `now()` to return a naive datetime and the full suite still passed (1145). | P1 |
| 2 | VG, EC | The repo-root `README.md:29` and `HANDOFF.md:77` still show `curl.exe .../health` as a working check; it now returns 401 | low | Confirmed at both lines. Both files are outside `nextron-engine/`, and `HANDOFF.md` is agent context; the orchestrator's handoff commit updates them. | defer |
| 3 | BH | The README says entries sit under `nextron-engine/<env>`, but `WinVaultKeyring` keeps one name at that target and the others at `<name>@nextron-engine/<env>`; rotation also leaves the old value at the compound target; the test doubles model a flat dict | low | Confirmed in `keyring/backends/Windows.py:123-156`. The README text is wrong, which is a direct correction. Traced read/write/delete sequences: keyring's `get` always returns the latest value and `delete` removes both targets, so the doubles match the observable behaviour. Delete-before-write to clear the stale value adds a failure window: rejected. | P2 |
| 4 | BH | Names that differ only in case could collide, because Credential Manager targets may be case-insensitive | maybe-false | Case-insensitive `CredRead` matching is unverified. Even if true, the impact is low: Story 1.7's `gmail/<address>` names come from `normalize_email` (lowercase). The fix changes the frozen `SECRET_NAME_PATTERN`. Rejected; noted for Story 1.7. | reject |
| 5 | BH | The default `WinVaultKeyring` keeps keyring's `CRED_PERSIST_ENTERPRISE`, so secrets roam with a roaming profile | low | Confirmed: `Windows.py:32` defaults to ENTERPRISE (`0x3`). Setting `persist = "local machine"` on the default backend is a one-line correction, and it matches "secrets stay on the PC". | P3 |
| 6 | BH | No document says the "local processes" guarantee excludes processes of the same Windows user, which can read Credential Manager | low | True: Credential Manager has no per-application access control. A README sentence is a direct correction. | P4 |
| 7 | BH | The session cookie goes to every server on `127.0.0.1` whatever its port, not only to the engine | low | True: cookies aren't scoped by port. A per-port cookie name contradicts the AC's literal `nextron_session`, so only the documentation changes. | P4 |
| 8 | BH | Sessions can't be revoked; each `nextron open` adds one | low | True, but retiring earlier sessions contradicts overnight decision 4 (frozen). | reject |
| 9 | BH | HTMX requests that lose their session fail silently on the 401 | low | True for HTMX 2 defaults, but no template issues an `hx-*` request yet. Adding `HX-Refresh` adds a branch. Noted for Story 1.15 (the 30 s health poll). | reject |
| 10 | BH | The no-inline-code contract misses `hx-on*`/`data-hx-on*` attributes and `js:`/`javascript:` values in `hx-vals`/`hx-vars`/`hx-headers` | low | Confirmed: the checker only flags `name.startswith("on")`. Extending the attribute check is a direct correction. | P5 |
| 11 | BH | The AST async-handler check keys async defs by name, so a later same-named `async def` hides a decorated handler; `add_api_websocket_route` is missing | low | Confirmed: `async_defs = {node.name: node ...}`. Iterating the nodes is a direct correction. | P6 |
| 12 | BH | `#notifications:empty { display: none }` takes the `aria-live` region out of the accessibility tree, so screen readers can miss the first notice; the empty `<nav>` renders | low | Confirmed in `app.css`. Deleting the rule is a direct deletion. The empty `<nav>` stays, because the layout test and the AC require navigation to be present. | P7 |
| 13 | BH, EC | `engine_is_running` only proves that something accepts TCP connections, so a foreign listener gets the code and `open` still prints "opened" | low | True, but the frozen spec chose the TCP connect, and the code is useless to a listener that holds the engine's port (the engine can't start). Rejected; an identity probe is a possible later hardening. | reject |
| 14 | BH | Any website can fire unlimited `/enter?t=x` requests, which read Credential Manager and log WARNINGs | low | True (the Host is genuine), but it needs a page that targets this engine. Throttling or a `Sec-Fetch-Site` gate is a new guard. Noted for Story 1.5. | reject |
| 15 | BH | Refusal logging folds no `t` and two `t` into `mismatch` and doesn't say which keyring operation failed | low | True, but the frozen spec sets `reason` to the `CodeCheck` value; diagnostic only. | reject |
| 16 | BH | `test_open_with_no_engine_running_exits_1` frees its port before `nextron open` runs; a new listener there would make the test write to the real Credential Manager and open a browser | low | Confirmed: `free_port()` closes its socket. Holding an exclusive, non-listening socket on the port for the call is a small test-only correction, and it protects a Never rule. | P8 |
| 17 | BH | `sprint-status.yaml` says `in-progress` while the spec says `in-review` | false | Step 5 of the workflow moves the story to `review`; the mismatch is the in-flight state at review time. | reject |
| 18 | EC | A second `nextron open` process can write a code between `consume_code`'s read and delete, so the delete erases the fresh code | low | True: the lock is per process. The window is milliseconds, and Mac reruns `open`. Compare-and-delete is still racy and adds a read. | reject |
| 19 | EC | `NEXTRON_PORT=80` makes browsers omit the port from `Host`, so every request gets 400 | low | True, but it fails loudly on a configuration nobody uses; a special case adds a branch. | reject |
| 20 | EC | The template contract misses external URLs in `src`, `srcset`, `data` and `poster` on `img`, `iframe`, `source`, `object` and `video` | low | Confirmed: only `<script src>` and `<link href>` are checked for locality. Same checker gap as #10. | P5 |
| 21 | EC | `href="java&#9;script:..."` passes, because the parser unescapes it to a tab that browsers strip from URLs | low | Confirmed: `HTMLParser` yields `'java\tscript:go()'`, which `INLINE_URL` doesn't match. Same checker gap as #10. | P5 |

## Design Notes

```python
@app.get("/", name="home")
@personal_data(False)                 # under the route decorator; undeclared routes fail create_app
def home(request: Request) -> HTMLResponse: ...
```

**Choices the user won't notice:**
- `DashboardEntry` lives in `app.ops`, the entry service for engine operations, so the web layer imports only `nextron.app.ops.api`, never ports or adapters.
- The 1,280 limit counts UTF-16 code units, because Credential Manager stores 2,560 bytes of UTF-16. That count equals the number of characters for every practical secret.
- `WinVaultKeyring` is pinned so that `PYTHON_KEYRING_BACKEND` or a keyringrc file can't send secrets to a plaintext file backend.
- Static assets also need a session, because the AC is literal; the locked page uses none.
- `includeIndicatorStyles:false` stops HTMX injecting an inline `<style>`, which the CSP would block.
- A keyring failure on `/enter` shows the locked page with a 401 and is logged as `reason=unavailable`.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` skipped).
- `uv run lint-imports` -- expected: every contract KEPT (29 or more).
