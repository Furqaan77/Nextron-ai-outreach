---
title: 'Story 1.5: Require CSRF-checked POSTs and render untrusted text safely'
type: 'feature'
created: '2026-11-17'
status: 'done'
baseline_commit: '9119d62007eacc5ec047dd8e6d4715aa4d3e786d'
route: 'dispatch'
review_loop_iteration: 0
story_key: '1-5-require-csrf-checked-posts-and-render-untrusted-text-safely'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A page on another port of `127.0.0.1` is same-site, so the browser sends it the `SameSite=Strict` session cookie, and nothing yet stops it POSTing to the dashboard. Nothing stops a GET handler writing either, and there are no helpers that keep crawled, Reply or AI text inert when it renders (AD-13, AD-18, NFR-19, UX-DR3, UX-DR4).

**Approach:**
- **Checked POSTs.** Every session gets a synchronizer CSRF token. The guard checks `Origin` and `Sec-Fetch-Site` on every unsafe method, and a `require_csrf` dependency checks the token on every POST route.
- **Read-only GETs.** A dashboard route class wraps every GET handler in a read-only unit of work, enforced down to SQLite's `query_only`.
- **Safe rendering.** Add the `plain`, `safe_link`, `csrf_field` and `untrusted_block` helpers, plus a `|safe`/`Markup` allowlist contract.

The acceptance criteria are Story 1.5 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Sessions** (`web/sessions.py`): `create()` also makes a `token_urlsafe(32)` CSRF token for that session, kept for its life. `csrf_token(session_token) -> str | None`.
- **Guard** (`web/guard.py`), for `http` scopes, in this order:
  1. Host (unchanged).
  2. Session (unchanged). The guard also puts the CSRF token of the first live `nextron_session` value into `scope["nextron.csrf_token"]`.
  3. For any method other than `GET` and `HEAD`:
     - **Origin:** there must be exactly one `Origin` header, equal byte for byte to `b"http://" + <the request's Host>`.
     - **Sec-Fetch-Site:** zero headers, or exactly one equal to `same-origin`.
     - **On failure:** 403 `text/plain; charset=utf-8` with `SECURITY_CHECK_FAILED = "Request refused: security check failed"` and every guard header, before routing. It logs `request_refused` (WARNING, `check` = `origin` or `fetch_site`), never the header values.
- **CSRF dependency** (`web/csrf.py`, `async def require_csrf(request) -> None`).
  - **Expected token:** `scope["nextron.csrf_token"]`.
  - **Presented tokens:**
    - every `X-CSRF-Token` header;
    - when the `Content-Type` media type is `application/x-www-form-urlencoded`, every `csrf_token` field of the body. The body is parsed with `urllib.parse.parse_qs` (ASCII only, `keep_blank_values=True`, `max_num_fields=1000`).
  - **Pass:** at least one token is presented, and every presented token matches. Each comparison is `hmac.compare_digest` on UTF-8 bytes (`errors="replace"`).
  - **Failure:** any failure, a malformed body included, raises `SecurityCheckFailed`. Its app exception handler returns the same 403 body with `Cache-Control: no-store` and logs `request_refused` with `check=csrf_token`.
  - **No side effects:** the handler never runs, so nothing changes.
- **Route rules** (`web/routing.py`):
  - **`Units` Protocol:** `open(*, read_only: bool = False) -> AbstractContextManager[object]`.
  - **`dashboard_route_class(units)`:** an `APIRoute` subclass that `create_app` sets as `app.router.route_class`. Methods must be exactly `{"GET"}` or `{"POST"}`; anything else raises `ValueError`.
    - **GET:** the endpoint is wrapped by `functools.wraps` (so `@personal_data` carries over). The wrapper runs the handler inside `units.open(read_only=True)` on the handler's own thread, and is marked as read-only. An `async def` or generator endpoint raises `TypeError`.
    - **POST:** gets `Depends(require_csrf)` first in `dependencies`.
  - **`route_rule_problems(app) -> list[str]`:** names each route that is any of these:
    - not an `APIRoute`;
    - a GET without the read-only mark;
    - a POST without `require_csrf` among `route.dependant.dependencies`;
    - any other method set.

    `require_route_rules(app)` raises `RuntimeError` naming them, and `create_app` calls it after `require_declared`.
- **Read-only unit of work** (`db/uow.py`, `UnitOfWorkFactory.open(*, read_only=False)`, `UnitOfWork.read_only`).
  - **Refused, raising `UnitOfWorkError`:** `@use_case`, `transaction()`, `@command` and `publish`.
  - **`uow.session`:** works anywhere in a read-only unit of work. On first use it begins one read transaction through `engine.read_only_connection` and keeps it until the unit closes.
  - **The guarded session** also refuses `add`, `add_all`, `delete`, `merge`, `flush`, `bulk_save_objects`, `bulk_insert_mappings` and `bulk_update_mappings`. A `before_flush` event refuses any flush. A clean exit with pending `new`/`dirty`/`deleted` objects raises after the rollback.
  - **Write units of work are unchanged.**
- **`engine.read_only_connection(engine)`** (the one place `query_only` is set):
  1. Connect, then begin (`BEGIN IMMEDIATE`, as AD-2 requires). Only then set `PRAGMA query_only=ON` and read it back, because `BEGIN IMMEDIATE` fails under `query_only`.
  2. While the connection is flagged, `before_cursor_execute` refuses, with `ReadOnlySqlError`, any statement whose leading keyword isn't `SELECT` or `WITH`. That closes `PRAGMA query_only=OFF`.
  3. On exit: clear the flag, roll back, set `query_only=OFF` and read it back; any failure invalidates the connection.
- **Templating** (`web/templating.py`, installed by `template_environment()`):
  - **`plain` filter:** `None` → empty; otherwise `str()` of the value (dropping any `__html__`), escaped, so `Markup` input is escaped too.
  - **`safe_link(url, text=None)`:** strip surrounding ASCII whitespace, then make a link only when all of these hold:
    - the URL has no `\x00-\x20`, `\x7f` or `\`;
    - `urlsplit` gives a scheme of `http` or `https` (case-insensitive) and a hostname;
    - the port parses.

    A link is `<a href="…" rel="noopener noreferrer">…</a>`. Otherwise it renders `plain(text or url)`.
  - **`csrf_field()`** (`pass_context`): `<input type="hidden" name="csrf_token" value="…">`. A missing `csrf_token` raises.
  - **`templates/macros.html`:** `untrusted_block(text=None)` renders `<div class="untrusted" hx-disable>`, containing `caller()` when it is called with `{% call %}`, else `text|plain`. `app.css` adds `.untrusted { white-space: pre-wrap; overflow-wrap: anywhere }`.
- **App** (`web/app.py`):
  - `create_app(…, units: Units, …)` adds the route class, the exception handler and `app.state.templates`.
  - `page()` passes `csrf_token`.
  - `base.html` `<body hx-headers='{{ {"X-CSRF-Token": csrf_token}|tojson }}'>`.
  - **`/enter`:** if any `Sec-Fetch-Site` is present and not exactly one `none`, it refuses with the locked page (401) before `consume_code`, logging `dashboard_entry_refused` with `reason=cross_site`.
- **Main:**
  - `connect_database(config) -> UnitOfWorkFactory` (no migration); `open_database` uses it.
  - `build_app(config, *, units, secrets=None, clock=None)`.
  - `ServerRunner` takes `(config, reload, units)`. `run_uvicorn` passes `units` to `build_app`; the reload worker (`create_app_from_environment`) uses `connect_database`.
- **Contracts** (`tests/contracts/test_web_contracts.py`), each with a live proof:
  - **Route rules:** `route_rule_problems` is empty.
  - **Forms:** every `<form>` in the templates has `method="post"` and calls `csrf_field()` before `</form>`.
  - **Safe allowlist:** outside `tests/contracts/safe_allowlist.txt`, the check fails on:
    - in templates: `|safe`, `filter safe` or `autoescape false`;
    - in `nextron/web` Python, by AST: any `Markup` name, attribute or import alias, any `__html__` def, or a string containing `|safe`.

    Allowlist lines are `<posix path>: <stripped source line>`, with `#` comments. A stale entry fails too.

**Never:**
- Don't add a dependency (no `python-multipart`), CORS, a GET with a side effect, or `async def` handlers.
- Never put a token or session value in logs or URLs.
- Work owned by later stories is out of scope: Settings forms and form-field handling for handlers (1.6), HTMX error notifications (1.12), and server-side HTML-to-text conversion (crawler and replies stories).
- Don't edit the vendored HTMX, or anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

### Decisions made overnight (pending user review)

1. **`/enter` refuses cross-site navigation.** It accepts only a missing `Sec-Fetch-Site` or `none`, which is what browsers send when `nextron open` launches them. A link from another website (`cross-site`), or from a server on another `127.0.0.1` port (`same-site`), gets the locked page and leaves the pending code untouched.
2. **`Origin` is required on every POST.** AD-13 conditions only `Sec-Fetch-Site` on being present. Every current browser sends `Origin` on POST, so a POST without it is refused.
3. **Where the CSRF token comes from.** It is read from the `X-CSRF-Token` header or a urlencoded `csrf_token` field. A plain (non-HTMX) multipart form can't carry it, so such a form is refused until a story needs one.
4. **A refused POST is a plain-text 403.** HTMX doesn't swap error responses, so an HTMX control shows nothing. The error notification belongs to Story 1.12.
5. **No `<form>` uses GET yet.** Every form must be `method="post"` with `csrf_field()`. A future GET filter form must amend this contract.
6. **Forms submit through HTMX (added at review).** Under Story 1.4's `Referrer-Policy: no-referrer`, a browser's native form submission sends `Origin: null`, which the AC's `Origin` check refuses. Every `<form>` must therefore also carry `hx-post`, so the request goes out as an XHR, which keeps the real `Origin`. If the HTMX script fails to load, the native fallback is refused (403): it fails closed. The alternatives were rejected because each breaks a frozen AC:
   - accepting `Origin: null` together with `Sec-Fetch-Site: same-origin` breaks this story's `Origin` rule;
   - changing `Referrer-Policy` breaks Story 1.4.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| POST ok | session; `Origin: http://127.0.0.1:8766`; header token; or urlencoded field token; or both; `Sec-Fetch-Site` absent or `same-origin`; `localhost` Host plus `localhost` Origin | handler runs once | N/A |
| POST token refused | none; wrong; another session's; one of two headers wrong; header ok but field wrong; malformed or non-ASCII body; JSON body with the token only in the body | 403 plain body, handler never ran | `request_refused` `csrf_token` |
| POST origin refused | `Origin` missing, `null`, another port, `https`, trailing `/`, `localhost` on a 127.0.0.1 Host, or two `Origin` headers; `Sec-Fetch-Site` `cross-site`, `same-site`, `none` or doubled | 403 before routing; an unknown path or `PUT` is also 403 | `request_refused` `origin`/`fetch_site` |
| No session | cross-site POST (no cookie) | 401 locked page | N/A |
| GET | any route | handler inside one read-only unit, same thread | N/A |
| GET writes | `eventlog.append`; `INSERT`/`PRAGMA query_only=OFF` via `session.execute`; `session.add`; a dirty object; a use case; `transaction()`; `publish` | raises, 500, `events` unchanged; the pooled connection then writes normally | N/A |
| Enter | `Sec-Fetch-Site` `cross-site`, `same-site`, `same-origin`, or two headers / absent or `none` | 401, code kept / normal entry | `reason=cross_site` |
| Render | `<script>`, `onerror=`, `hx-post=` text; `Markup("<b>")`; `javascript:`, `data:`, `JavaScript:`, ` java\tscript:`, `//evil`, `http:\\evil`, `https://x:99999` URLs | escaped text; only http(s) links, each with `rel="noopener noreferrer"`; container `hx-disable` | N/A |
| Routes | a `PUT`, `GET`+`POST`, or `HEAD` route; an async GET; a plain `APIRoute` GET or POST | add-time `ValueError`/`TypeError`; `route_rule_problems` names it | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/web/guard.py` -- `Guard._http` (Host → session → app), `_has_session`, `_respond`, `_secured`; insert the CSRF scope key and unsafe-method checks; `_has_session` becomes a lookup returning the CSRF token.
- `nextron-engine/nextron/web/sessions.py` -- `SessionStore` (digest set) becomes digest → CSRF token.
- `nextron-engine/nextron/web/app.py` -- `create_app`, `page()`, `template_environment()` (contract tests monkeypatch it: keep it here), `/enter`; `NO_STORE`.
- `nextron-engine/nextron/web/routing.py` -- `personal_data`, `undeclared_routes`, `require_declared`; add the route class and rule checks.
- `nextron-engine/nextron/db/uow.py` -- `UnitOfWork` (`_check_open`, `_refuse`, `_start_use_case`, `_start_command`, `transaction`, `session`, `publish`, `_close`), `GuardedSession` (checks `uow._tx is self._transaction`; generalise to the read transaction), `UnitOfWorkFactory.open`.
- `nextron-engine/nextron/db/engine.py` -- `_refuse_transaction_sql`, `leading_keyword`, `fk_off_connection` (the pattern to mirror), `_BEGINNING`.
- `nextron-engine/nextron/main.py` -- `build_app`, `open_database`, `create_app_from_environment`, `run_uvicorn`, `ServerRunner`, `serve` (calls `run_server(config, reload)`).
- `nextron-engine/tests/asgi.py` -- `request()` sends an empty body; add `body: bytes = b""`.
- `nextron-engine/tests/dashboard.py` -- `Dashboard` builds `create_app`; add `units` (default `tests/fakes/units.py` `FakeUnits` recording `(read_only, thread id)`), `post(...)`, and `csrf_token(cookie)` that reads it from `GET /`'s `<body hx-headers>`.
- `nextron-engine/tests/conftest.py` -- `db_engine` and `units` fixtures (real database at head) for the read-only tests.
- `nextron-engine/tests/contracts/test_web_contracts.py` -- the existing checks. `test_the_run_time_check_catches_an_async_handler` and `test_undeclared_routes_names_an_undeclared_route` add routes the new route class refuses: append a plain `fastapi.routing.APIRoute` to `app.router.routes` instead. The template list assertion gains `macros.html`. `test_the_base_layout_escapes_what_it_is_given` renders `base.html` with `nav` only, so it must also pass `csrf_token`.
- `nextron-engine/tests/unit/test_serve.py` -- the `run_server` fakes (`ServerSpy.__call__`, `fail_to_start`, `crash`, `server`) take a third `units` argument; the reload test calls the factory.
- `nextron-engine/tests/unit/test_health.py:58`, `tests/contracts/test_source_tree.py` (`FILES`), `tests/unit/test_uow.py`, `tests/unit/test_db_engine.py` -- update and extend.
- `.venv/.../fastapi/routing.py` -- `APIRouter.add_api_route` uses `route_class_override or self.route_class`. `include_router` keeps each route's own class, so `require_route_rules` catches a router built without the dashboard class. FastAPI adds no `HEAD` to GET routes (405 today).

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/db/engine.py`, `nextron/db/uow.py` -- `read_only_connection`, `ReadOnlySqlError`, the read-only unit of work, and docstrings -- AD-2, AD-3.
- [x] `nextron-engine/nextron/web/sessions.py`, `web/guard.py`, `web/csrf.py`, `web/routing.py`, `web/templating.py`, `web/app.py`, `web/templates/base.html`, `web/templates/macros.html`, `web/static/app.css`, `web/__init__.py` -- tokens, checks, route class, helpers -- AD-13.
- [x] `nextron-engine/nextron/main.py` -- `connect_database`, `build_app(units=…)`, runner wiring.
- [x] `nextron-engine/tests/asgi.py`, `tests/dashboard.py`, `tests/fakes/units.py` -- test support.
- [x] `nextron-engine/tests/unit/test_uow.py`, `tests/unit/test_db_engine.py` -- the read-only rows, including that the pooled connection writes normally afterwards.
- [x] `nextron-engine/tests/unit/test_csrf.py` -- the POST, origin, no-session, enter and route rows, through `Dashboard` with test POST routes that record calls.
- [x] `nextron-engine/tests/app/test_untrusted_rendering.py`, `tests/fixtures/templates/untrusted_page.html` -- a fixture page (extends `base.html`, a POST form with `csrf_field()`, `untrusted_block` and `safe_link` on hostile values) served by a test route on a `Dashboard` app, using `app.state.templates` with the fixture folder prepended to its loader. Parse the HTML to assert the Render row, `hx-headers` and the hidden field equal to the session's token, and a real-units GET that writes gets 500.
- [x] `nextron-engine/tests/contracts/test_web_contracts.py`, `tests/contracts/safe_allowlist.txt` -- the route, form and allowlist contracts with live proofs; adapt the existing tests per the Code Map.
- [x] `nextron-engine/tests/unit/test_serve.py`, `tests/unit/test_health.py`, `tests/unit/test_dashboard.py`, `tests/contracts/test_source_tree.py` -- updates per the Code Map.
- [x] `nextron-engine/README.md` -- CSRF, the read-only GET units, the rendering helpers, the allowlist, and decisions 1–4.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `serve` running in `dev`, when `curl.exe -i -X POST http://127.0.0.1:8766/ -H "Origin: http://evil.example"` runs, then it returns 401 (no session). With a valid session cookie, it returns 403 with the plain refusal body.

## Implementation Notes

- **Code Map correction.** FastAPI 0.162.7's `include_router` doesn't copy routes into the app. It adds one opaque `_IncludedRouter` route, so `route_rule_problems` refuses any included router as "not an APIRoute", even one built with the dashboard route class. Dashboard routes are added to the app itself (`@app.get`/`@app.post`/`add_api_route`), and `_label` names a pathless route `<TypeName>`.
- **Sessions.** `SessionStore` maps the SHA-256 digest of each session token to that session's CSRF token. `is_valid` was removed: nothing used it once `csrf_token()` existed.
- **The read-only mark** is self-referential: the wrapper's `__nextron_read_only__` names the wrapper itself. A function that only copied the wrapper's attributes (through `functools.wraps`) therefore doesn't pass.
- **Choices the spec left open.**
  - A POST with more than one `Content-Type` header is refused as malformed.
  - `units.open(read_only=…)` with a value that isn't a bool raises `TypeError`.
  - `safe_link` with no `text` shows the stripped URL as its label.
  - A POST to a GET-only route that passes the guard gets FastAPI's 405 JSON: routing runs before dependencies, and the handler never runs.
- **Rendering helpers.**
  - `nextron/web/templating.py` imports `markupsafe` directly. It is installed with jinja2 but not declared in `pyproject.toml`; no package was added.
  - The safe allowlist holds three lines, all in `templating.py`.
- **Review patches (pass 1).**
  - **P1, forms post through HTMX** (decision 6). The form contract requires `hx-post` on every `<form>`. `test_a_native_form_submission_is_refused` shows a browser-style navigation POST (`Origin: null`) gets 403.
  - **P2, pages aren't cached.** `page()` sends `Cache-Control: no-store`, because every page carries the session's CSRF token.
  - **P3, `require_csrf` must come first.** The route rules require it to be the first dependency of a POST.
  - **P4, accurate wording.** The README and `routing.py` docstring now promise "no GET writes to the database" and name `GET /enter` as the one GET that changes state by design.
  - **P5, a stricter allowlist scan.**
    - Templates are scanned as whole text.
    - It catches any `{% autoescape X %}` whose argument isn't literally `true`, and quoted `'safe'`/`"safe"` names inside Jinja tags.
    - Python string constants get the same template scan.
    - `__html__` is caught as a def, a name or an attribute.
  - **P6, a stricter fake.** `FakeUnits` refuses a non-bool `read_only` and nesting on one thread (`tests/unit/test_units_fake.py`).
  - **P7, traceable refusals.** `request_refused` also logs `method` and `path`, never the query string.
  - **P8, no deceptive links.** `safe_link` refuses userinfo URLs and any Unicode `Cc`/`Cf`/`Zs`/`Zl`/`Zp` character.
  - **P9, the served app's factory.** A test shows the non-reload `run_uvicorn` serves `serve`'s own factory.
  - **P10, no callable objects.** The route class refuses a GET endpoint that isn't a plain function or method.
  - **P11, cleanup survives `BaseException`.** `read_only_connection` invalidates in a `finally` when the restore didn't finish.
  - **P12, older tabs.** The README tells Mac to reload an older tab after a second `nextron open`.
  - **P13, type hint.** `build_app` types `units` as `nextron.web.routing.Units`.
- **Live check.** During implementation, `serve` ran in `dev` with a scratch data dir. A cross-origin POST without a session gave 401; with a session it gave 403 (`no-store`); a `same-site` POST gave 403; a cross-site `/enter` gave 401. It wrote one one-time code to Credential Manager under `nextron-engine/dev`, which `/enter` consumed, so nothing is left behind.
- **For Story 1.6.**
  - **Form bodies.** `python-multipart` isn't in the Stack, so FastAPI refuses `Form()` parameters. Parse an urlencoded body in an `async` dependency with `await request.body()` (Starlette caches it after `require_csrf` read it), as `csrf.py` does with `parse_qs`.
  - **Forms** need `method="post"`, `hx-post` and `{{ csrf_field() }}`.
  - **`page()`** takes no extra context yet; extend it.
  - **GET handlers** render inside their body. They read through `nextron.app.queries.*`, which use `current().session` in the read-only unit.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (14 findings), EC = edge-case hunter (19), VG = verification gap (1 gap and 2 other findings).
- **Counts:** 36 raw findings, 28 after deduplication. Rows 4, 5, 8, 13, 17 and 20 merge identical claims.
- **Outcomes:** 16 rows patched (P1–P13), 1 deferred, 11 rejected.
- **No loopback:** there are no intent_gap or bad_spec entries. Row 1 has only one reading compatible with the frozen ACs, so it is a patch, recorded as overnight decision 6.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH | A plain `<form method="post">` is refused in real browsers: under `Referrer-Policy: no-referrer` a navigation POST sends `Origin: null` | medium | Fetch spec, "append a request `Origin` header": a POST whose mode isn't `cors` gets `null` under `no-referrer`. HTMX's XHR is `cors`-mode and keeps the origin. The form contract and README tell authors to write exactly the form that fails. | P1 |
| 2 | BH | `base.html` silently renders `"X-CSRF-Token": null` when the token is missing | false | Verified: a missing `csrf_token` raises `TypeError` (Undefined isn't JSON-serialisable). `None` comes only from `page()` on a request without a session, which the guard never lets reach a page route; `/enter` never renders `page()`. | reject |
| 3 | BH | Pages carrying the session's CSRF token are cacheable (`/` declares `personal_data(False)`) | low | True: no `Cache-Control` on `/`. A stale cached or back-forward page also carries a dead token. One header in `page()` fixes it. | P2 |
| 4 | BH, EC | `route_rule_problems` accepts a POST whose `require_csrf` isn't the first dependency | low | True: it checks `require_csrf in calls`, so a plain `APIRoute` with an earlier side-effecting dependency passes. `calls[:1]` is a direct correction. | P3 |
| 5 | BH, EC | Nothing makes a GET render inside its unit: ORM rows, a `StreamingResponse` or a lazy iterator are evaluated after the unit closed | low | True, but it fails loudly (detached instance, or a closed or wrong-thread unit raises), and the README states the rule. Refusing non-`Response` results adds a branch and breaks `/health`'s dict. | reject |
| 6 | BH | "No GET has a side effect" overclaims: the guarantee covers database writes only, and `GET /enter` consumes the code and creates a session | low | True for the README and the `routing.py` docstring. A wording fix. | P4 |
| 7 | BH | Read-only GETs take `BEGIN IMMEDIATE`, so reads serialise with each other and with writers, and a same-site page can make cookie-carrying GETs hold the lock | low | True, but AD-2 and Story 1.3's decision bind every transaction to `BEGIN IMMEDIATE`. The lock lasts milliseconds today, and no GET reads yet. A deferred read begin needs an AD-2 amendment. | defer |
| 8 | BH, VG, EC | The template allowlist scan misses `{% autoescape 0 / none / flag %}`, `\|safe` split across lines, and `map('safe')` | low | Verified by VG's probe: the per-line regex matches only literal `autoescape false` and same-line `\|safe`. Scanning the whole text and broadening the patterns are direct corrections. | P5 |
| 9 | BH | Unescaped HTML without `Markup`/`\|safe` passes: `HTMLResponse(f"…")`, `jinja2.Template`, a second `Environment(autoescape=False)` | low | True, but the AC scopes the contract to `\|safe` and `Markup`, and covering these needs a new contract. | reject |
| 10 | BH | `FakeUnits` accepts nesting and non-bool `read_only`, so a GET that opens a write unit passes dashboard tests | low | True: the fake has no checks, and every `Dashboard` test uses it by default. Two checks mirror the real factory. | P6 |
| 11 | BH | A write unit can run `PRAGMA query_only=ON` and return a read-only connection to the pool | low | True (`PRAGMA` isn't a transaction keyword), but no code path does this, and the fix adds a guard branch. | reject |
| 12 | BH, EC | The unsaved-changes check uses optimistic `session.dirty`, so assigning an unchanged value fails the GET | low | True, but intended strictness: assigning an ORM attribute in a GET is a write attempt, which the AC says raises. | reject |
| 13 | BH | `request_refused` logs only `check`, so a refusal can't be traced to a route | low | True; method and path aren't secrets (ULIDs, no query string). Adding two log fields is direct. The `Sec-Fetch-Site` value in `dashboard_entry_refused` stays unlogged, because it is attacker-chosen. | P7 |
| 14 | BH | `safe_link` links userinfo URLs (`https://nextronai.com@evil.example/`) | low | True: `urlsplit` gives host `evil.example` and a link renders. Refusing `username`/`password` is one condition. | P8 |
| 15 | EC | `safe_link` links URLs holding bidi, format or C1 characters (U+202E), which can spoof the visible text | low | True: only `\x00-\x20\x7f\\` is refused. A Unicode-category check is direct. | P8 |
| 16 | VG | The non-reload `run_uvicorn` path is never shown to pass `serve`'s factory to `build_app` | low | Pre-verified gap. Spying `build_app` with `uvicorn.run` patched closes it. | P9 |
| 17 | VG, EC | When FastAPI has parsed a body for `Form()` params, `require_csrf`'s `request.body()` raises "Stream consumed" | low | Verified unreachable: FastAPI refuses to register a `Form()` route without `python-multipart` (`RuntimeError`), and the Stack doesn't include it. Noted for the story that adds form parsing. | reject |
| 18 | EC | A Python string holding `{% autoescape false %}` or `{% filter safe %}` passes the allowlist (only `\|safe` is matched) | low | True: `STRING_SAFE` checks only `\|safe`. Reusing the template pattern is direct. | P5 |
| 19 | EC | `__html__` defined by assignment passes the allowlist | low | True: only `def __html__` is flagged. Flagging `__html__` names and attributes is direct. | P5 |
| 20 | EC | An async-callable object as a GET endpoint escapes `_refuse_async` and runs on the event loop outside the read-only unit | low | Verified: such a route answered 200 with `FakeUnits.open_now == []`. Requiring a plain function or method is one condition. | P10 |
| 21 | EC | GET dependencies and background tasks run outside the read-only unit | low | True, but no route has them, and forbidding them would also forbid legitimate ones; it adds a rule branch. | reject |
| 22 | EC | A `BaseException` during the `query_only` restore skips `invalidate()` | low | True (`except Exception`). Moving `invalidate()` into a `finally` is direct. | P11 |
| 23 | EC | The dev reload worker serves an old schema after a new migration | low | Dev only, and it fails loudly with missing table or column errors; restart `serve`. A revision check adds a branch. | reject |
| 24 | EC | `Markup`/`__html__` outside `nextron/web` isn't scanned | low | The AC scopes the contract to "the templates and `nextron/web`". | reject |
| 25 | EC | `safe_link(url, "")` or `0` shows the URL instead of the given text | false | The spec's frozen rule is `plain(text or url)`; an empty label would make an invisible link. | reject |
| 26 | EC | `csrf_field()` in a macro imported without context raises a 500 | low | It fails loudly, and the message names the fix (pass `csrf_token`); correct behaviour for misuse. | reject |
| 27 | EC | After a second `nextron open`, an older tab's HTMX POSTs carry the old token and are refused silently | low | True: the cookie jar now holds the new session. Mac will meet it; a README note is the direct fix until Story 1.12's notification. | P12 |
| 28 | EC | `build_app` annotates `units: UnitOfWorkFactory` while tests pass `FakeUnits` | low | True; a one-line annotation change to `Units`. | P13 |

## Design Notes

```python
@app.post("/settings", name="settings_save")      # gets Depends(require_csrf) from the route class
@personal_data(False)
def settings_save(request: Request) -> Response:
    with units.open():                            # write unit in the handler body (AD-3)
        setup_api.save(...)
```

**Choices the user won't notice:**
- Header checks sit in the guard, so they run before routing and also cover unknown paths and wrong methods. The token check is a dependency, so the route table can prove it, and it can read the body.
- GET handlers must render inside the handler. The unit closes before FastAPI serialises the return value.
- A read transaction holds `BEGIN IMMEDIATE` from its first read until the handler returns. The unit is lazy: routes that never read (`/static`, `/enter`, `/health`) never touch the database.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` skipped).
- `uv run lint-imports` -- expected: every contract KEPT (29).
