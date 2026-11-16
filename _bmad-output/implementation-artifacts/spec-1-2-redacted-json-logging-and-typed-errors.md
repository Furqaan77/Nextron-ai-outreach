---
title: 'Story 1.2: Log structured, redacted JSON lines and raise typed errors'
type: 'feature'
created: '2026-11-16'
status: 'done'
baseline_commit: 'a50852390f9ca29bf8971d21f3a8999712c33cf9'
route: 'dispatch'
review_loop_iteration: 0
story_key: '1-2-log-structured-redacted-json-lines-and-raise-typed-errors'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has no logging or error conventions. Without them, later stories will write secrets, email addresses and tokens into logs and stored error strings (violating NFR-18 and AD-17), and they will raise ad-hoc or raw SDK exceptions that jobs can't classify as retryable.

**Approach:** Add three pieces:
- a pure `nextron.domain.redaction.redact()`;
- a composition-root logging setup that writes redacted JSON lines to `<data dir>\logs\nextron.log` (rotating at 10 MB, 10 files);
- the typed error bases `nextron.domain.errors.DomainError` and `nextron.ports.errors.AdapterError(retryable, code, message)`.

Contract tests prove redaction and the adapter error rule. The acceptance criteria are Story 1.2 in `_bmad-output/planning-artifacts/epics.md`, verbatim.

## Boundaries & Constraints

**Always:**
- **`redact(text: str) -> str` is pure, stdlib-only and idempotent.** It must handle:
  - URL query strings: removed, keeping scheme/host/path;
  - `/bot<id>:<token>`: becomes `/bot<redacted>`;
  - email addresses: become `<email>`;
  - `Bearer <token>` (any case): becomes `Bearer <redacted>`;
  - `sk-ant-…` keys and any 32+ character run of `[A-Za-z0-9_-]`: become `<key>`.
- **26-character ULIDs survive.**
- **Every handler on the root logger carries the `RedactingFilter`,** as well as the root logger itself. Python skips ancestor-logger filters for records that propagate from child loggers.
- **The filter redacts after %-formatting** (message plus args), and redacts the formatted exception text and every string `extra` value.
- **JSON line fields:**
  - `ts`: UTC ISO 8601 with milliseconds and `Z`;
  - `level`, `logger`, `event` (the redacted message);
  - scalar `extra` fields (ID fields);
  - `exc`: redacted traceback, when present.

  Non-scalar extras are dropped. Lines are UTF-8.
- **Rotation:** `RotatingFileHandler` with `maxBytes=10 MiB`, `backupCount=9` (10 files total), `delay=True`.
- **Logging setup runs in `serve` only after the instance lock is held.** A second instance (exit 3) and `doctor` never touch the log file.
- **Uvicorn runs with `log_config=None`,** so its records go through the root handlers.
- **The default level is INFO.** The setup function takes a level argument for Story 1.6 to supply.
- **SDK loggers stay at WARNING** whatever the root level: `googleapiclient`, `google_auth_oauthlib`, `httplib2`, `httpx2`, `anthropic`.
- **`DomainError` subclasses each declare a unique, stable `code`** matching `^[a-z][a-z0-9_]*$`.
- **`AdapterError` stores `retryable: bool`, `code: str` and `message: str`,** and redacts `message` at construction.

**Never:**
- No logging library outside the stdlib.
- No new bootstrap variables (the log level is not configurable yet).
- No database or settings work.
- Don't log personal data deliberately: the logs hold IDs only, and redaction is the safety net.
- Don't modify anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Normal record | `getLogger("nextron.x").info("lead_enriched", extra={"lead_id": ULID})` | One JSON line in `<data dir>\logs\nextron.log` with `ts` (…Z), `level`, `logger`, `event`, `lead_id` | N/A |
| Query string | message holds `https://h/p?key=abc&e=a@b.co` | `https://h/p`, with no query text in the file | N/A |
| Telegram token | `https://api.telegram.org/bot123:AAx…/sendMessage` | `…/bot<redacted>/sendMessage` | N/A |
| Email, bearer, key | an email, `Authorization: Bearer xyz`, `sk-ant-…`, a 40-character key | masked as `<email>`, `Bearer <redacted>`, `<key>` | N/A |
| Secret in args or traceback | `log.error("failed %s", secret)` and `log.exception(...)` with a secret in the exception message | secret absent from `event` and `exc` | N/A |
| Child logger | secret logged on `nextron.app.setup.api` | masked (handler filter applies) | N/A |
| ULID kept | 26-character ULID in `extra` and in the message | unchanged | N/A |
| SDK noise | root at DEBUG; `httpx2` logs DEBUG and INFO | not written; WARNING is written | N/A |
| Rotation | file exceeds 10 MiB | rolls over; at most 10 files exist | N/A |
| Lock held | second `serve` for the same env | no log file opened or written | exit 3 (unchanged) |
| Domain error | a `DomainError` subclass without `code`, or with a duplicate | contract test fails | test failure |
| Adapter re-raise | a module under `nextron.adapters` with a bare `raise`, `raise SomeSdkError`, or `AdapterError(..., str(exc))` | contract test fails | test failure |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/main.py` -- `serve()` flow (env → checks → mkdir → lock → port → Uvicorn); add the logging setup right after `lock.acquire()` succeeds; `run_uvicorn` gets `log_config=None`.
- `nextron-engine/nextron/{bootstrap,lock,startup_checks}.py` -- precedent for composition-root helpers; they're fenced by `c1-composition-root-helpers` in `pyproject.toml`.
- `nextron-engine/pyproject.toml` `[tool.importlinter]` -- 28 contracts. `tests/contracts/test_contract_coverage.py` fails if a new top-level module is missing from contracts 4 and 5.
- `nextron-engine/tests/contracts/support.py` -- `lint_copy_with_probe()`: live-proof pattern on a `tmp_path` copy. Reuse it; never write probes into the real tree.
- `nextron-engine/tests/contracts/test_domain_stdlib_only.py` -- covers `domain` and `ports`, which the new modules must satisfy.
- `nextron-engine/nextron/ports/clock.py` -- existing port style to match.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/redaction.py` -- `redact()` with the ordered regex set from Boundaries -- the single redaction rule.
- [x] `nextron-engine/nextron/domain/errors.py` -- `DomainError(Exception)`: `code: ClassVar[str]`, `__init__(message)`; subclasses define `code` -- AD conventions.
- [x] `nextron-engine/nextron/ports/errors.py` -- `AdapterError(Exception)` with `retryable`, `code`, `message` (redacted) -- the port-boundary error.
- [x] `nextron-engine/nextron/logconfig.py` -- `RedactingFilter`, `JsonLineFormatter`, `configure_logging(logs_dir, level=INFO)`: root handlers (rotating file, plus stderr at WARNING), SDK loggers at WARNING, idempotent on repeat calls -- composition-root helper.
- [x] `nextron-engine/nextron/main.py` -- call `configure_logging` after the lock is taken; `log_config=None` for Uvicorn (both branches); log `engine_started`/`engine_stopped` with `env` and `version`.
- [x] `nextron-engine/pyproject.toml` -- add `nextron.logconfig` to `c1-composition-root-helpers` and wherever contract coverage requires.
- [x] `nextron-engine/tests/unit/test_redaction.py` -- every pattern, idempotence, ULID survival, `/bot` paths, mixed text.
- [x] `nextron-engine/tests/unit/test_logconfig.py` -- matrix rows for JSON fields, child loggers, args, exceptions, SDK levels, rotation (small `maxBytes` injected), and the lock-held case (via `serve` with a fake lock-held path).
- [x] `nextron-engine/tests/contracts/test_log_redaction.py` -- plants every secret kind through real `configure_logging` on a temp dir and asserts none reaches the file.
- [x] `nextron-engine/tests/contracts/test_error_contracts.py` -- the `DomainError` subclass code check (imports every `nextron.domain` module), and an AST check over `nextron/adapters/**` that allows only `raise AdapterError(...)` (or its subclasses), optionally `from exc`, with no `str(exc)`/f-string of the caught exception in its arguments, plus a live proof on a tmp copy.
- [x] `nextron-engine/README.md` -- a short "Logs" section: location, rotation, redaction.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `NEXTRON_ENV=dev`, when `serve` starts and `/health` is requested, then `nextron.log` contains `engine_started` and Uvicorn's records as JSON lines.

## Implementation Notes

- **Redaction rules beyond the Boundaries list (from review).** `redact` applies nine rules in this order: query strings and fragments, URL userinfo (`//user:pass@` becomes `//<redacted>@`), `/bot` tokens, `Bearer` and `Basic` credentials, credential fields, emails, `sk-ant-` keys, standard base64, and the 32+ rule. It repeats them until a pass changes nothing; at most 3 passes were seen in 400,000 fuzz inputs, and `_MAX_PASSES = 8`.
  - **Query strings.** In a whitespace-delimited token, the rule acts at the first `?` or `#` that has a `/` before it, or directly follows a dotted host name (with an optional `:port`). It drops everything from there to the next whitespace, quotes included. A `?` or `#` that ends a token is left alone, so prose such as `done?` is kept.
  - **Credential fields** are `api_key`, `api-key`, `apikey`, `token`, `secret`, `password`, `client_secret`, `access_token` and `refresh_token`, in any case, also at the end of a longer name (`x-api-key`, `id_token`), followed by `=` or `:` and an optional quote. The value is masked at any length and the name is kept.
  - **Emails** use Unicode word characters and also match `%40`. The local part is capped at 64 characters to keep the scan linear; a second pattern absorbs any local-part characters left in front of `<email>`.
  - **Base64.** A `[A-Za-z0-9+/]{32,}={0,2}` run is masked only when it contains `+` or ends with `=`. A run whose only other character is `/` is treated as a path and left to the 32+ rule segment by segment, so `/leads/<ULID>/evidence/<ULID>` and URL paths with ULIDs survive. A base64 value with `/` but no `+` or `=` can therefore leak when every segment between slashes is under 32 characters.
  - **UUIDs** in text, or written as text from an `extra`, are 36 characters of hex and `-`, so the 32+ rule masks them as `<key>`.
- **Run time.** `test_redaction.py` fuzzes 20,000 random mixtures for idempotence and times 100–200 KB pathological inputs, each under about a second.
- **Filter mechanics.** `RedactingFilter` formats the message with its args, redacts it and clears `args`. It renders the traceback into `exc_text`, redacts it and sets `exc_info = None`. It also redacts `stack_info` and every string extra, then marks the record. If the message can't be formatted, it never raises into the caller.
  - `JsonLineFormatter` redacts `event`, `exc`, `stack` and string extras again.
  - It converts ULID, UUID, Enum (to `.value` when that is a scalar), `PurePath`, `date` and `datetime` extras to text, then redacts them. Other non-scalars are dropped, as are Uvicorn's `color_message` and any extra named like a JSON field.
  - It escapes U+2028, U+2029 and U+0085 as `\uXXXX`. Lone surrogates are written as `\udcXX` escapes (the file handler uses `errors="backslashreplace"`), so the line still parses.
- **`configure_logging(logs_dir, level=INFO, *, max_bytes=MAX_BYTES)`** replaces every root handler (closing only its own) and installs a JSON file handler and a stderr text handler at WARNING. The stderr handler reads `sys.stderr` when a record arrives. One shared `RedactingFilter` goes on both handlers and on the root logger.
  - It holds 11 SDK loggers at WARNING: `googleapiclient`, `google_auth_oauthlib`, `google.auth`, `google_auth_httplib2`, `httplib2`, `httpx2`, `httpcore2`, `urllib3`, `requests_oauthlib`, `oauthlib`, `anthropic`.
  - It sets `sys.excepthook` (`engine_crashed`, CRITICAL; `KeyboardInterrupt` goes to `sys.__excepthook__`) and `threading.excepthook` (`thread_crashed`, ERROR; `SystemExit` is ignored), and calls `logging.captureWarnings(True)`.
  - Importing `nextron.logconfig` replaces `logging.lastResort` with a redacting stderr handler at WARNING. This covers `doctor`, records logged before setup and the `serve --reload` worker; a subprocess test checks it.
- **`serve`.** Logging is configured right after `lock.acquire()`. If the port check then fails, `serve` logs `startup_check_failed` (`check`). It logs `engine_started` (`env`, `version`) before the server and `server_start_failed` (`exit_code`) on a non-zero Uvicorn `SystemExit`. On any other exception it logs `engine_crashed` with the traceback and marks the exception, so the excepthook doesn't log it twice, then re-raises. `engine_stopped` is logged in a `finally` with `exit_code` (0, 2, or 1 for a crash). Startup failures are still printed on stderr as before.
- **Errors.**
  - `DomainError.__init_subclass__` refuses a subclass whose own `code` is missing, malformed or already used by another class. Defining the same `module.qualname` again is allowed, as a reload does. An intermediate base passes `abstract=True` and declares no code.
  - `DomainError` itself and abstract bases refuse to be instantiated, and `nextron.domain.errors.is_abstract()` tells them apart. The contract test still imports every domain module and re-checks codes, skipping abstract classes, to catch codes changed after definition.
  - `AdapterError` raises `TypeError` unless `retryable` is a `bool` and `code` and `message` are `str`, and `ValueError` unless `code` matches `CODE_PATTERN`.
- **Adapter raise check (`test_error_contracts.py`)** is AST-only. The raised name must be imported from `nextron.ports.errors` (`AdapterError` or a subclass defined there, under any alias, or through the module as in `errors.AdapterError`), or be a class in the same file that derives from one. A subclass imported from another adapter module is therefore refused; shared subclasses belong in `nextron/ports/errors.py`.
  - The cause must be a bare name or `None`.
  - In the arguments, an expression rooted at a caught exception may only be a plain attribute chain ending in `status_code`, `errno`, `code` or `retry_after`. Any `traceback.*`, `sys.exc_info()`, `sys.exception()` or bare `format_exc`-style call fails.
  - The check is static: text copied into a local variable first (`msg = str(exc)`) isn't caught, and `AdapterError`'s redaction is the backstop.
- **Event names.** `tests/contracts/test_log_event_names.py` fails when a literal event name of 32 or more characters is passed to a logging call under `nextron/`. It recognises receivers named `log`, `logger`, `logging`, `*_log`, `*_logger` and `getLogger(...)`, and uses the second argument for `.log()`.
- **Contracts.** `nextron.logconfig` joins `c1-composition-root-helpers` and the sources of the five "every package except" contracts and contract 5. It imports `ulid` (python-ulid, already a Stack dependency), which no contract forbids. `tests/contracts/support.py` gained `copy_with_probe()`.
- **Test isolation.** An autouse fixture in `tests/conftest.py` restores, after every test:
  - the root logger's handlers, filters and level;
  - the SDK logger levels;
  - `sys.excepthook`, `threading.excepthook` and `logging.lastResort`;
  - `captureWarnings(False)`.

  It also closes any handler the test added.
- **`serve --reload` (dev only)** still serves from a worker process that does not write `nextron.log`. The worker's WARNING and above go to stderr, redacted by the last-resort handler. The README and the `main.py` docstring say so.
- Secret-shaped test values are assembled at run time (for example `"sk-ant-" + "api03-" + ...`), so no literal in the repo matches a secret-scanner pattern.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).** Sources: BH = blind hunter, EC = edge-case hunter, VG = verification gap. Grouped as patches P1–P13 and deferrals D1–D3. The spec's redaction list is a floor: the intent says "never a secret, email address or token", so added rules are patches, not spec changes.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH, EC | Redaction misses `%40` emails and non-ASCII emails | medium | The `_EMAIL` regex needs a literal `@` and an ASCII class. Uvicorn access paths and Gmail `users/{id}` URLs carry `%40`. | P1 |
| 2 | BH, EC | No rule for `Basic` auth, `x-api-key`, or `api_key/token/secret/password/client_secret=` pairs under 32 characters | medium | There is no such rule in `redaction.py`. Short credentials reach logs. | P1 |
| 3 | BH, EC | Standard base64 secrets (`+ / =`) split into runs under 32 characters and survive | medium | The rule-6 class excludes `+/=`; tested with an AWS-style key. | P1 |
| 4 | BH, EC | Query kept for a host without a path, and quoted query values survive | medium | `_strip_query` requires `/` before `?`, and `_TOKEN` stops at quotes; both tested. | P1 |
| 5 | EC | URL userinfo password survives; a local part over 64 characters leaves a prefix | low | The regex shapes allow it. Both have direct one-rule fixes. | P1 |
| 6 | BH | Docstring claims "personal data", but phone numbers aren't masked | low | Direct doc correction. A phone rule belongs with AD-15 Reply-text redaction (Epic 7). | P12, D3 |
| 7 | BH, EC, VG | Unredacted stderr paths: uncaught exceptions (`sys.excepthook`), thread exceptions, warnings, `logging.lastResort` (doctor, pre-config, reload worker) | medium | No excepthook or captureWarnings anywhere (grep). Python's last-resort handler has no filter. | P2 |
| 8 | BH, EC, VG | Startup failures and crashes leave no cause in `nextron.log`, and `engine_stopped` has no exit code | medium | Port fail and Uvicorn exit print to stderr only; a crash traceback never reaches the file. | P3 |
| 9 | BH, EC | SDK loggers that log tokens and requests at DEBUG are not capped (`requests_oauthlib`, `oauthlib`, `google.auth`, `google_auth_httplib2`, `urllib3`, `httpcore2`) | medium | All are in `uv.lock`, and `SDK_LOGGERS` lists five. OAuth token bodies could reach the log at DEBUG. | P4 |
| 10 | BH | An SDK can reset its own logger to DEBUG (`ANTHROPIC_LOG`), defeating `setLevel` | maybe-false | Not checked against anthropic 2.8.0. It would be settled by reading the SDK's logging setup. | D1 |
| 11 | BH, EC | `_scalar` drops ULID, UUID, Enum, Path and datetime extras | medium | Tested. Python-ulid `ULID` objects are the project's ID type (SC-8), so IDs vanish silently. | P5 |
| 12 | BH, EC | `stack_info` is redacted but never written to the file | low | Tested: the JSON line has no stack. Direct field addition. | P6 |
| 13 | EC | U+2028, U+2029 and U+0085 in values split lines for `splitlines()` readers | low | `json.dumps(ensure_ascii=False)` leaves them raw. Direct escape. | P6 |
| 14 | VG | The lone-surrogate (`backslashreplace`) behavior is untested | medium | VG mutation removing it leaves the suite green, and records then vanish. | P7 |
| 15 | VG | `engine_stopped` after a non-SystemExit crash is untested | medium | VG mutation leaves the suite green. | P7 |
| 16 | BH, EC | `AdapterError` doesn't validate `retryable`, `code` or `message` types, or `CODE_PATTERN` | medium | Tested: swapped arguments are accepted, and truthy `retryable` means bad retries. | P8 |
| 17 | EC | `AdapterError` subclass with another `__init__` breaks pickling | low | No subclass exists; an unlikely future case with a non-trivial fix. | reject |
| 18 | BH, EC | `DomainError` code only checked for imported domain modules; raising the base or a codeless subclass gives an AttributeError at runtime | medium | The check is import-order dependent. `__init_subclass__` is a direct fix. | P9 |
| 19 | BH, EC | Adapter raise check passes `exc.response.json()`, `traceback.format_exc()`, `sys.exc_info()`, and an `sdk.AdapterError` lookalike | medium | Tested on a probe copy. Cheap AST additions close these. | P10 |
| 20 | BH, EC | Adapter check misses tainted locals (`msg = str(exc)`) and adapters with no try at all | medium | Needs taint analysis or per-port runtime contracts. `AdapterError` redaction is the backstop. | D2 |
| 21 | BH, EC | No end-to-end planted secret through real Uvicorn, and `wait_for_log_line` can fail on a partly written line | medium | The process test requests `/health` with no query, and `json.loads` runs on every line. | P11 |
| 22 | BH, EC | Event names of 32+ characters are masked as `<key>`, making records unsearchable | low | Tested with a 44-character name. An AST check of literal event names is a direct fix. | P12 |
| 23 | EC | UUID-shaped IDs are masked | low | The project uses ULIDs. Masking an external ID errs safe. | reject |
| 24 | BH, VG, EC | README and `main.py` claim `--reload` logs to `nextron.log`, but the worker has no handlers | low | It's dev only. P2's redacting lastResort covers secrets; the docs need the caveat. | P12 |
| 25 | EC | Rollover `PermissionError` on Windows when another process holds the file | maybe-false | BH tested `Get-Content -Wait`: the rename succeeded. Settle with a handle opened without FILE_SHARE_DELETE. | D1 |
| 26 | EC | A handler on a non-root logger bypasses the filter | low | No such handler exists, and P2 covers lastResort. | reject |

## Design Notes

**Ordering in `redact`:**
1. Strip query strings first (they may contain emails and keys).
2. Then `/bot` tokens.
3. Then bearer tokens.
4. Then emails.
5. Then `sk-ant` keys.
6. Then the generic 32+ rule.

The generic rule must not match inside `<redacted>`-style placeholders, so the result is a fixed point. Example: `GET https://x.io/a?t=1 by ann@x.co key sk-ant-api03-ZZ…` becomes `GET https://x.io/a by <email> key <key>`.

## Verification

**Commands:**
- `cd nextron-engine && uv sync --frozen && uv run pytest` -- expected: all pass, `ai_eval` skipped.
- `uv run lint-imports` -- expected: all KEPT.
- `NEXTRON_ENV=dev uv run python -m nextron serve`, then `curl.exe http://127.0.0.1:8766/health` -- expected: 200; `%LOCALAPPDATA%\Nextron\dev\logs\nextron.log` holds JSON lines including `engine_started` and Uvicorn's access line.
