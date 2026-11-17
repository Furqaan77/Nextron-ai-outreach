---
title: 'Story 1.8: Keep dev and test away from real people with environment isolation and sink adapters'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '5da36ba0057162aa8bcbf39c6c31547ee91cd32e'
review_loop_iteration: 0
story_key: '1-8-keep-dev-and-test-away-from-real-people-with-environment-iso'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing yet stops a `dev` or `test` run from emailing a prospect or posting to a real Telegram chat. Nothing stops a test from building a live adapter, and nothing catches `NEXTRON_ENV=prod` left set for every process (AD-14, NFR-8, NFR-17).

**Approach:**
- **Ports.** Add `AlertPort` and a minimal `MailPort`.
- **Sink.** A sink adapter wraps each of them outside `prod`. It passes allowlisted recipients through and records everyone else in `<data dir>\sink\<YYYY-MM-DD>.jsonl`. The allowlist is the `sink_allowlist` setting.
- **Role registry.** `main.py` builds every `mail` or `alert` adapter through one registry that adds the sink.
- **Tests.** Every test runs as `NEXTRON_ENV=test`, with live constructors and non-loopback network blocked.
- **Doctor.** A new doctor check refuses a persistent `NEXTRON_ENV=prod`.

The acceptance criteria are Story 1.8 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Domain** (`nextron/domain/sink.py`, stdlib only):
  - `PROD = "prod"`; `sink_applies(env) -> bool` is `True` for anything except exactly `"prod"`.
  - `is_sink_recipient(text) -> bool`: a `str` that fully matches an ASCII email address (local part of atext and dots, `@`, two or more `[A-Za-z0-9-]` labels; at most 254 characters) or a numeric Telegram chat id `-?[1-9][0-9]{0,19}`.
  - `allowlisted(recipient, allowlist) -> bool`: `True` only when `recipient` is a valid sink recipient and equals a valid allowlist entry after ASCII-only lowercasing. Invalid or non-`str` entries are ignored, and nothing is stripped.
- **Setting** (`settings_schema.py`):
  - `Key.SINK_ALLOWLIST = "sink_allowlist"`, type `list`, section `Engine`, label "Dev and test allowlist".
  - Unit: "One email address or numeric Telegram chat id per line. Outside prod, email and alerts reach only these; everything else is written to the sink folder."
  - Default `()`, and rule `sink_recipient`: "each line must be an email address or a numeric Telegram chat id".
- **Ports.** `nextron/ports/alert.py` holds:
  - `AlertMessage(recipient, text)` and `AlertDelivery(message_id: str | None, sink: bool = False)`;
  - `@runtime_checkable AlertPort.send(message: AlertMessage) -> AlertDelivery`.

  `nextron/ports/mail.py` holds:
  - `MailMessage(recipient, subject, text)` and `MailDelivery(message_id, thread_id, sink=False)`;
  - `MailPort.send(message: MailMessage) -> MailDelivery`.

  These are all frozen, slotted dataclasses. Failures are `AdapterError`.
- **Sink** (`nextron/adapters/sink/__init__.py`):
  - **Classes.** `SinkRecorder(folder, clock)`, `AlertSink(wrapped, allowlist, recorder)` and `MailSink(wrapped, allowlist, recorder)`. `allowlist` is a `Callable[[], Iterable[str]]`, called on every send. `.wrapped` is exposed.
  - **Allowlisted** (per `allowlisted`): the sink calls `wrapped.send(message)` and returns its result unchanged, so its errors propagate. It logs `sink_passed` (`port`).
  - **Anyone else:** the sink appends one ASCII JSON line, `{"time", "port", "recipient", ("subject",) "text"}`, to `<folder>\<UTC date>.jsonl`. `time` is `YYYY-MM-DDTHH:MM:SS.ffffffZ`, and the folder is created when missing. The sink then returns a delivery with every id `None` and `sink=True`, never calls `wrapped`, and logs `sink_recorded` (`port`).
  - **One module lock** serialises file writes.
  - **An allowlist call that raises** counts as an empty allowlist, and the sink logs `sink_allowlist_unreadable` (`port`, `error` = the exception's type name) as a WARNING.
  - **A failed write** raises `AdapterError(False, "sink_write_failed", ...)`, and `wrapped` is not called.
  - **No log record** carries a recipient or any text.
- **Composition** (`nextron/main.py`):
  - **Roles.** `SinkRole` (StrEnum: `mail`, `alert`).
  - **Context.** `AdapterContext(config, secrets, clock)`.
  - **Registry entries.** `RoleAdapter(name, role, adapter_type, build: Callable[[AdapterContext], object])`.
  - **The registry.** `ROLE_ADAPTERS: tuple[RoleAdapter, ...] = ()`. Stories 1.12, 6.x and 7.9 add the entries.
  - **Reading the allowlist.** `read_sink_allowlist(units)` returns `queries.setup.get(Key.SINK_ALLOWLIST)`, read in its own read-only unit of work inside a fresh `contextvars.Context()`. Port calls happen inside the caller's unit of work, between its transactions.
  - **Building.** `build_role_adapter(name, context, *, units)`:
    1. Looks the name up; an unknown name raises `LookupError`.
    2. Builds the adapter and checks its `adapter_type`.
    3. In `prod`, returns the adapter.
    4. Otherwise wraps it in the role's sink, with `SinkRecorder(data_dir / "sink", clock)` and `read_sink_allowlist` bound to `units`.
- **Doctor** (`startup_checks.py`):
  - **Probe.** `Probes` gains `persistent_variable: Callable[[str], Mapping[str, str]]`, which reports the `user` and `machine` scopes. `read_persistent_variable(name)` reads `HKCU\Environment` and `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment` with `winreg`, imported lazily. A missing key or value counts as absent.
  - **Check** `prod_env_not_persistent`. It runs in `all_checks` (doctor) only, after OneDrive and before the port.
    - FAIL when any scope's value, stripped and casefolded, is `prod`. The detail starts with `NEXTRON_ENV=prod must only be set by the logon task` and names the scope or scopes.
    - FAIL when the probe raises.
    - PASS otherwise.
- **Tests:**
  - **The environment.** The autouse conftest fixture also sets `NEXTRON_ENV=test`. The data dir defaults to `<temp LOCALAPPDATA>\Nextron\test`.
  - **The live guard** (`tests/live_guard.py`). The autouse fixture patches every live constructor (`nextron.adapters.secrets.default_backend`, and each `ROLE_ADAPTERS` build) to raise `LiveAdapterInTests`. `allow_live_constructor(monkeypatch, module, name)` restores one constructor for a test that has neutralised the backend.
    - Every adapter package is listed exactly once:
      - **Guarded:** `secrets`;
      - **No external system:** `clock` and `sink`, each with its reason;
      - **Not yet built:** `ai`, which must define nothing.
  - **The network guard.** `socket.getaddrinfo` and `socket.socket.connect`, `connect_ex` and `sendto` refuse anything that isn't loopback or `localhost`.
  - **Fakes.** `tests/fakes/__init__.py` maps every port Protocol to its fake in `PORT_FAKES`: `FrozenClock`, `InMemorySecrets`, `InMemoryAlert` and `InMemoryMail`.

**Never:**
- Don't add a migration, a dependency, a route, a live mail or alert adapter (Telegram is Story 1.12), or a real network call or Credential Manager write in any test or check.
- Never let a recipient or text reach a log record. Never deliver when the allowlist can't be read.
- Don't wire role adapters into any service, or add serve-time checks.
- Don't edit the vendored HTMX, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **A minimal `MailPort` exists now**, so the `mail` role has a sink. Epic 6 may extend `MailMessage`. A contract test pins its fields, so any new field forces a review of the sink's recipient check.
2. **Recipients match after ASCII-only lowercasing,** never Unicode casefolding: Unicode would make, for example, `ß` equal `ss`. Telegram ids must be numeric, because usernames can be reassigned.
3. **The allowlist is empty by default.** Outside `prod`, nothing is delivered until Mac lists his own test inboxes and chats.
4. **An unreadable allowlist sinks the message,** and the delivery still reports `sink=true`.
5. **The persistent-`prod` check runs only in `doctor`, as the acceptance criteria say.** `serve` can't tell a persistent value from the logon task's own.
6. **Tests also block non-loopback network,** as a safety net under the live-constructor guard.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Allowlisted | `dev`; allowlist `Mac@Example.com`; alert to `mac@example.com` | wrapped `send` called once; its delivery returned (`sink=False`); `sink_passed` logged | wrapped errors propagate |
| Other recipient | `test`; alert to `-100123` not listed; T = 2026-11-17 23:59:59Z | `<data dir>\sink\2026-11-17.jsonl` gains `{"time":"2026-11-17T23:59:59.000000Z","port":"alert","recipient":"-100123","text":...}`; `AlertDelivery(None, sink=True)`; wrapped never called | N/A |
| Mail | `MailSink`, recipient not listed | line also has `subject`; `MailDelivery(None, None, sink=True)` | N/A |
| Near miss | `a@x.com ` / `a@x.com, b@y.com` / `straße@x.com` vs entry `strasse@x.com` | not allowlisted, so recorded | N/A |
| Allowlist fails | the provider raises | recorded; WARNING `sink_allowlist_unreadable` | none raised |
| Write fails | the folder can't be created or written | `AdapterError` `sink_write_failed`; wrapped not called | raised |
| prod | `build_role_adapter` in `prod` | the live adapter itself, unwrapped | N/A |
| Setting | `a@b.co` and `123`; then `@chan`, `a@b`, `12a` | stored; then refused with rule `sink_recipient` | `InvalidSettingValue` |
| Doctor | user `NEXTRON_ENV=prod`; machine `PROD `; only `dev`; none; probe raises | FAIL, FAIL, PASS, PASS, FAIL (exit 1 when any fails) | N/A |
| Test run | any test | `NEXTRON_ENV=test`; `KeyringSecrets("test")` and `build_app` without injected secrets raise `LiveAdapterInTests`; a connect to `192.0.2.1:443` or a lookup of `example.com` is refused | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/main.py` -- the composition root.
  - `build_app`, `build_entry` and the module docstring: add a "Role adapters and the sink (AD-14)" paragraph.
  - Import `nextron.app.queries.setup` and `Key` for `read_sink_allowlist`.
  - Layer contract 1 allows `main` to import anything.
- `nextron-engine/nextron/db/uow.py` -- `UnitOfWorkFactory.open(read_only=True)`. A second `open()` in the same context raises, hence the fresh `contextvars.Context()`. The read-only unit takes `BEGIN IMMEDIATE` only on its first session use.
- `nextron-engine/nextron/app/queries/setup.py` -- `get(key)` reads the typed value; a default list setting with no row gives `()`.
- `nextron-engine/nextron/domain/settings_schema.py`:
  - `Key`, `SCHEMA`, `Rule` and `Section.ENGINE`;
  - list items are already stripped one-line strings;
  - `check_schema` runs at import.
- `nextron-engine/nextron/startup_checks.py` -- add the `Probes` field, `read_persistent_variable`, the check and its constants, and add it to `all_checks`, not to `environment_checks`.
- `nextron-engine/tests/fakes/probes.py` -- `fake_probes` gains a default `persistent_variable` that returns `{}`.
- `nextron-engine/nextron/adapters/secrets/__init__.py` -- `default_backend` is the live constructor to guard. `KeyringSecrets(env, backend=...)` with an injected backend isn't live.
- `nextron-engine/nextron/adapters/clock/__init__.py` -- the style of a minimal adapter package: a docstring that states the import rule.
- `nextron-engine/nextron/ports/secrets.py`, `ports/errors.py` -- the docstring and `AdapterError` conventions the new ports follow.
- `nextron-engine/pyproject.toml` -- contract `c5-anthropic-only-in-ai` gains `nextron.adapters.sink`. `test_contract_coverage.py` enforces this.
- `nextron-engine/tests/conftest.py`:
  - **`local_app_data`** (autouse) strips `NEXTRON_*` and sets a temp `LOCALAPPDATA`; also set `NEXTRON_ENV=test` here, and add the guards as autouse fixtures.
  - **Tests that need `NEXTRON_ENV` unset** (env refusal in `test_bootstrap.py`, `test_serve.py`, `test_doctor.py` and `test_open_command.py`) gain `monkeypatch.delenv("NEXTRON_ENV")`.
  - **Tests that build the live keyring** must inject or patch `main.KeyringSecrets`: `test_serve.py`'s reload-worker test (`create_app_from_environment`), and possibly others that surface. `test_secrets.py::test_the_default_backend_is_win_vault_constructed_only` uses `allow_live_constructor`.
- `nextron-engine/tests/unit/test_secrets.py:32` -- the `DictKeyring` pattern, for a recording backend in the isolation app test.
- `nextron-engine/tests/app/test_serve_process.py` -- the subprocess helpers (`free_port`, `engine_env`, the startup wait) to mirror for side-by-side `dev` and `test` engines.
- `nextron-engine/tests/unit/test_health.py:56-80` -- `build_app` with `load_bootstrap()` and the test port's `Host`.
- `nextron-engine/tests/contracts/test_settings_contracts.py:131` -- a probe message that lists the missing `Key` members, which now include `sink_allowlist`.
- `nextron-engine/tests/contracts/test_source_tree.py` -- add each new file to `FILES`, and `adapters/sink` to the import-rule list.
- `nextron-engine/tests/contracts/support.py` -- `ENGINE_ROOT`, `PACKAGE` and `copy_with_probe`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/sink.py` -- `PROD`, `sink_applies`, `is_sink_recipient`, `allowlisted` -- AD-14.
- [x] `nextron-engine/nextron/domain/settings_schema.py` -- `SINK_ALLOWLIST` and its rule.
- [x] `nextron-engine/nextron/ports/alert.py`, `nextron/ports/mail.py` -- the ports and their messages and deliveries.
- [x] `nextron-engine/nextron/adapters/sink/__init__.py`, `pyproject.toml` (c5), `nextron/adapters/__init__.py` docstring (a new live adapter registers its constructor in `tests/live_guard.py`) -- the sink.
- [x] `nextron-engine/nextron/main.py` -- `SinkRole`, `AdapterContext`, `RoleAdapter`, `ROLE_ADAPTERS`, `read_sink_allowlist`, `build_role_adapter`, docstring.
- [x] `nextron-engine/nextron/startup_checks.py`, `tests/fakes/probes.py` -- the persistent-`prod` check.
- [x] `nextron-engine/tests/live_guard.py`, `tests/conftest.py`, `tests/fakes/__init__.py`, `tests/fakes/alert.py`, `tests/fakes/mail.py` -- the test environment, the guards and the fakes.
- [x] `nextron-engine/tests/unit/test_sink.py` -- the domain rules, and every matrix row for both sinks: exact JSON lines, a file per UTC day, the allowlist read on each send, no recipient or text in `caplog`.
- [x] `nextron-engine/tests/unit/test_settings_schema.py` (or `test_setup_settings.py`) -- the setting row.
- [x] `nextron-engine/tests/unit/test_startup_checks.py`, `tests/unit/test_doctor.py` -- the doctor rows. `read_persistent_variable` is tested against a fake `winreg`-like registry.
- [x] `nextron-engine/tests/unit/test_role_adapters.py`:
  - with real `units`: `read_sink_allowlist` returns `()`, then the stored list, including while a unit is open in the caller's context;
  - `build_role_adapter` end to end, for a probe registration on `dev`: an allowlisted recipient is delivered, and another lands in `<data dir>\sink`;
  - an unknown name, and a wrong `adapter_type`.
- [x] `nextron-engine/tests/contracts/test_sink_contracts.py` -- AC 1, each part with a live proof on a probe:
  - in `dev` and `test`, every registered role is returned wrapped in its sink, and in `prod` unwrapped;
  - every class under `nextron.adapters` whose `send` takes an `AlertMessage` or `MailMessage` (sinks aside) is registered with that role;
  - in `main.py` such classes, or their modules, are referenced only inside the `ROLE_ADAPTERS` assignment, and `ROLE_ADAPTERS` is read only in `build_role_adapter`;
  - `AlertMessage` and `MailMessage` have exactly the fields the sink reads.
- [x] `nextron-engine/tests/contracts/test_test_isolation.py` -- AC 6:
  - the env and the temp data dir;
  - `PORT_FAKES` covers every Protocol in `nextron/ports`, and each fake satisfies its port;
  - live constructors raise;
  - every adapter package is classified, and `ai` is empty;
  - the network guard refuses non-loopback and allows loopback.
- [x] `nextron-engine/tests/app/test_environment_isolation.py` -- AC 4:
  - real `dev` and `test` engines started side by side, on one temp `LOCALAPPDATA`, with free ports, both answering `/health`. Each data dir holds only its own `nextron.db`, lock and log, the log records carry only their env, and nothing else exists under `Nextron\`.
  - In-process: two `build_app`s on one recording `KeyringBackend`, through the real `KeyringSecrets`. Saving `hunter` in `dev` leaves `test` "Not set", and every backend call uses the app's own `nextron-engine/<env>`.
- [x] Existing tests -- updates from running the suite: the env unset where required; the live keyring injected; settings probes and the page with the new setting.
- [x] `nextron-engine/tests/contracts/test_source_tree.py` -- the new files.
- [x] `nextron-engine/README.md`:
  - "Choose an environment": the persistent-`prod` rule;
  - a new "Dev and test sink" section;
  - the doctor check;
  - the Test section: `NEXTRON_ENV=test`, the live and network guards, `PORT_FAKES`.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- the sink files hold recipient addresses and text: add retention or erasure in the retention story.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `NEXTRON_ENV=dev`, when `uv run nextron doctor` runs on this PC, then it lists `prod_env_not_persistent` with PASS (assuming no persistent value), alongside the existing checks.

## Implementation Notes

- **Additions the spec allows but doesn't name** (all small, all tested):
  - `build_role_adapter` also refuses an adapter that doesn't implement its role's port
    (`TypeError`) and a name registered more than once (`LookupError`); `RoleAdapter` checks its
    own fields. `main.ROLE_PORTS` and `main.ROLE_SINKS` map each role to its port and sink.
  - `AlertMessage` and `MailMessage` leave `recipient`, `subject` and `text` out of their
    `repr`, so a stray log call can't print them.
  - `allowlisted` treats a bare `str` or `bytes` given as the whole allowlist as empty (iterating
    it would match single characters such as `"1"`), and so does the sink (P1).
  - `read_persistent_variable(name, *, registry=None)` takes a `winreg` stand-in for tests; a
    registry value that isn't a string counts as absent (Windows builds the environment from
    string values only).
  - The live guard's stand-ins remember what they replaced (`is_refused`), so
    `allow_live_constructor` also works for a role adapter a test registers itself, and
    guarding twice is harmless. The isolation contract also refuses an adapter package
    classified as reaching no external system that defines a mail or alert sender, in any of its
    modules (the sink classes themselves are never counted as senders).
- **Existing tests changed:** the env-unset rows call `monkeypatch.delenv("NEXTRON_ENV")`
  (`test_bootstrap`, `test_serve`, `test_doctor`, `test_open_command`); three `test_serve` tests
  that run the real `build_app` patch `main.KeyringSecrets` with in-memory secrets;
  `test_secrets`' default-backend test uses `allow_live_constructor`; the doctor lists now have
  five checks; the real-schema Settings page tests post the new `sink_allowlist` field.
- **Deferred** (`deferred-work.md`): sink-file retention and erasure; the allowlist read's
  `BEGIN IMMEDIATE` when a caller holds a transaction (fails closed); the `ai_eval` exemption
  from the network guard for Epic 4.
  Review pass 1 deferred two more: guards for engines that tests start as subprocesses, and
  binding each data dir to its environment.

Review fixes (pass 1, P1–P9; see the Review Triage Log):
- **P1.** `_entries` in the sink counts a provider that returns a bare `str` or `bytes` as
  unreadable: it logs `sink_allowlist_unreadable` with `error` = `str` or `bytes`, and the
  allowlist is empty.
- **P2.** The network guard also refuses non-loopback calls through:
  - `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr` and `getnameinfo`;
  - `socket.socket.sendmsg`, where it exists;
  - on Windows, asyncio's `IocpProactor.connect` and `sendto`.

  The `conftest.py` docstring and the README list exactly what is guarded.
- **P3.** `guard_live_constructors` also makes `WinVaultKeyring`'s `get_password`,
  `set_password`, `delete_password` and `get_credential` raise (`CREDENTIAL_MANAGER_CALLS`).
  `allow_live_constructor` never lifts that.
- **P4–P6.** `test_sink_contracts.py`:
  - **P4.** `registry_problems` scans every module under `nextron/`. Only `main.py`'s
    `ROLE_ADAPTERS` assignment may refer to a sender module, and an adapter package may refer
    only to its own.
  - **P5.** `unclear_senders` reports a `send` whose message parameter is unannotated, `Any`,
    `object` or unresolvable. `role_of_send` no longer matches annotation text.
  - **P6.** `sender_modules` counts any module that defines or re-exports a sender, and its
    parent packages. `stray_references` follows aliased and relative imports and flags star
    imports.
- **P7.** `classification_problems` looks for senders in every submodule, checks not-yet-built
  packages recursively, and has no `sink` exemption.
- **P8.** The README and the `startup_checks` docstring give the real reason the check runs
  only in `doctor`: the logon task's own `prod` start inherits any persistent value. The README
  warns about `prod` set in a PowerShell or terminal profile, which `doctor` can't see.
- **P9.** `test_environment_isolation.py` reserves both ports together (`free_ports`) and
  starts the engines inside the fixture's `try`.

**Final verification (after pass 1).**
- `uv run pytest -q`: 2190 passed, 1 skipped (`ai_eval`).
- `uv run lint-imports`: 29 kept, 0 broken.
- `NEXTRON_ENV=dev uv run nextron doctor`: all 5 checks PASS on this PC, `prod_env_not_persistent` included.
- Before the review fixes: 2176 passed, 1 skipped, and 29 kept.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (12 findings), EC = edge-case hunter (13), VG = verification gap (2 gaps and 3 other findings).
- **Counts:** 30 raw findings, 20 after deduplication. Groups A–J merge findings that share one root cause.
- **Outcomes:**
  - 9 groups patched (P1–P9);
  - 2 deferred: group D, and BH7;
  - 9 rejected: BH9 with EC5, BH10, BH11, BH12, EC2, EC3, EC4, EC6 and EC7.
- **No loopback:** there is no intent gap and no bad spec. Every patch is test-side or a direct correction, except P1, which adds a two-line guard in the sink to make a documented rule hold.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC1 | `_entries` calls `tuple(allowlist())`, so a provider that returns a bare `str` such as `"123"` is split into characters, and `"1"` gets delivered. This defeats `allowlisted`'s own bare-`str` guard. | low | Verified: `AlertSink(fake, lambda: "123", ...)` delivered `AlertMessage("1", ...)` with `sink=False`. The real provider (`read_sink_allowlist`) always returns a tuple, so this needs a buggy provider. The fix is a direct correction that makes the documented guard true. | P1 |
| B | BH2, VG3, EC10 | The test network guard misses `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr`, `getnameinfo`, `sendmsg`, and asyncio's Proactor path. The Proactor path uses `IocpProactor.connect` → `ConnectEx` and skips `getaddrinfo` for IP literals. | medium | Verified: only 4 functions are patched, and `IocpProactor.connect` uses `ConnectEx`. The conftest docstring promises that any non-loopback lookup or connection raises. This is decision 6's safety net. | P2 |
| C | BH3 | Credential Manager is guarded only through the `default_backend` factory. A test that builds `WinVaultKeyring()` directly, or calls `keyring.set_password`, reaches the real Credential Manager. | medium | Verified in `tests/live_guard.py`: only `default_backend` is patched. The boundary says there is never a Credential Manager write in any test. WinVault shuffles entries (the Story 1.7 item), so a stray test write could clobber Mac's real credentials. Patching the 4 backend methods is test-only. | P3 |
| D | BH4, VG5, EC11 | The guards are in-process only. The engines started as subprocesses (`test_environment_isolation.py`, `test_serve_process.py`) build the real `KeyringSecrets` (now in `dev` too) and run with no guard. | low | True, but nothing reaches Credential Manager or the network today: startup and an unauthenticated `/health` never read the keyring (`/enter` does, and isn't called). The gap has existed since Story 1.1's `test` subprocesses. Fixing it needs new infrastructure: a guard installed in each child. | defer |
| E | BH5, VG4 | The "built only through the registry" check scans only `main.py` and `__main__.py`. `bootstrap`, `lock`, `logconfig`, `startup_checks` and sibling adapter packages can import and build a sender with every contract green. | medium | Verified: c1 layers leave those helpers outside the layers, c3 forbids adapters only from app and db, and `stray_references` reads two files. AC 1's "no live mail or alert adapter can be obtained outside prod" has a structural hole. The fix is test-only. | P4 |
| F | BH6, VG2 (gap), EC12 | `role_of_send` recognises a sender only from `send`'s annotation. An unannotated, `Any` or unresolvable `send` escapes registration, the `main.py` check and the isolation classification. Nothing runs a type checker. | medium | VG2 is pre-verified: an unannotated probe gave `senders_in == {}`. This is the hole a live Telegram adapter could ship through, unwrapped. The fix is test-only. BH6's second half, that `isinstance` can't tell `MailPort` from `AlertPort`, is EC4 below. | P5 |
| G | VG1 (gap), EC13 | `sender_modules()` holds only each class's defining `__module__`. A sender re-exported by its package (`from nextron.adapters.telegram import TelegramAlert`) is not flagged, and star and relative imports are skipped. | medium | VG1 is pre-verified: a probe that re-exports from `.client` gave `stray_references(...) == []`. The fix is test-only. | P6 |
| H | EC9 | `classification_problems` looks for senders only in each package's `__init__`. It checks `NotYetBuilt` folders non-recursively, and exempts the whole `sink` package by name. | medium | Verified: it calls `senders_in([module])` on the package module only, and checks `glob("*.py")` at the top level only. A sender in `telegram/client.py` classified `NoExternalSystem` would escape the live guard. `senders_in` already skips the two sink classes, so the name exemption isn't needed. | P7 |
| I | BH8 | The README and `startup_checks` say `serve` "can't tell" a persistent value. It could read the registry: the real reason is that refusing at `serve` would also stop the logon task's own `prod` start. Profile-based settings (`$PROFILE`, terminal profiles) are also invisible to the check. | low | Verified wording at `README.md:64` and `startup_checks.py:11`. Profiles aren't "persistent environment variables", so they are outside the acceptance criterion, but a direct doc correction warns about them. | P8 |
| J | EC8 | `free_port()` is called once per engine, and each call binds and releases a port, so the two can collide. `started` is built outside `try`, so a failed second start leaks the first engine. | low | Verified in the fixture. The fix is a direct correction: reserve both ports together, and start inside `try`. | P9 |
| — | BH7 | `NEXTRON_DATA_DIR` can point a `dev` or `test` engine at prod's data dir. Nothing marks a data dir with its environment. | medium | Real, and present since Story 1.1's bootstrap. Mail and alerts would still be sunk (the env is `dev`), but `dev` would migrate and write the prod database. Not caused by this story. | defer |
| — | BH9, EC5 | A sink write that fails partway leaves a fragment, and the next record is appended onto it. | low | Possible only on a partial disk-full or locked-file write in `dev`. The fix adds a check-and-repair branch. | reject |
| — | BH10 | Sink records and logs don't name the adapter. | low | The recipient's form already tells a Telegram id from an email. The fix adds a parameter and a field: a debugging nicety. | reject |
| — | BH11 | No test sends while the caller holds a write transaction. | low | The fail-closed path is unit-tested for any exception (`test_an_unreadable_allowlist_records_the_message_with_a_warning`). A real `OperationalError` is just another exception, and the behaviour is already in deferred work. The test would take 5 s. | reject |
| — | BH12 | `sprint-status.yaml` says `in-progress` while the spec is `in-review`. | false | The workflow sets the story to `review` in step 5, and `in-progress` is correct during review. | reject |
| — | EC2 | A subclass of `MailMessage` or `AlertMessage` could add a CC field that the sink never checks. | low | Hypothetical: the port docstrings say to extend the dataclass, and the field contract pins it. The fix adds a type guard to both sinks. | reject |
| — | EC3 | `.wrapped` is public, so a holder can bypass the sink. | low | The spec's frozen block requires `.wrapped` to be exposed. Ports don't declare it, so app code typed against `AlertPort` can't reach it without casting. The fix would edit the spec. | reject |
| — | EC4 | `isinstance(adapter, ROLE_PORTS[role])` can't tell `MailPort` from `AlertPort`, so a misregistered adapter gets the wrong sink. | low | Verified that `isinstance(InMemoryAlert(), MailPort)` is `True`. But the result is a loud `AttributeError` and never a delivery, and once P5 makes every sender annotated, the registration contract catches a role mismatch. The fix adds a check. | reject |
| — | EC6 | A sink write failure is `retryable=False` even when it is transient. | low | The frozen spec fixes `AdapterError(False, "sink_write_failed", ...)`. The fix would edit the spec. | reject |
| — | EC7 | A `REG_EXPAND_SZ` `NEXTRON_ENV` of `%X%`, where `X` is `prod`, passes doctor. | low | An absurd configuration. The fix adds an expansion call. | reject |

## Design Notes

```python
alert = build_role_adapter("telegram", AdapterContext(config, secrets, clock), units=units)
delivery = alert.send(AlertMessage(chat_id, text))   # dev/test: AlertSink → delivered or recorded
```

**Choices the user won't notice:**
- **The allowlist is read on every send.** A change on the Settings page applies at once, with no cache to invalidate.
- **The sink folder is per environment.** It inherits the data dir's isolation and is never under OneDrive.
- **The JSON lines are pure ASCII** (`ensure_ascii`). A Unicode line separator in the text can never split a record.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass, with `ai_eval` skipped.
- `uv run lint-imports` -- expected: every contract (29) KEPT.
- `$env:NEXTRON_ENV="dev"; uv run nextron doctor` -- expected: `prod_env_not_persistent` PASS.
