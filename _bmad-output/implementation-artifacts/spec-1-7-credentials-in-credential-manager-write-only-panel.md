---
title: 'Story 1.7: Store external credentials in Windows Credential Manager from a write-only panel'
type: 'feature'
created: '2026-11-17'
status: 'done'
baseline_commit: '2bbd23b7e78ac0eed6be51714321a3af2d3ecdd5'
route: 'dispatch'
review_loop_iteration: 0
story_key: '1-7-store-external-credentials-in-windows-credential-manager-fro'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Mac has no way to enter the engine's API keys and tokens. Each later adapter would invent its own keyring name, and nothing shows which credentials are still missing (FR-56, AD-12, NFR-18, UX-DR27).

**Approach:**
- **One registry.** `nextron/domain/credentials.py` declares every external credential with its keyring name.
- **One service.** `Credentials` in `nextron/app/setup/api.py` saves, removes and reports credentials through `SecretsPort`. Each credential has a companion `meta/<name>` entry that holds only its last-updated time.
- **One write-only page.** `GET /credentials` lists the credentials. Each row saves or removes its own credential with a CSRF-checked POST. A stored value is never displayed.

The acceptance criteria are Story 1.7 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Registry** (`nextron/domain/credentials.py`, standard library only).
  - **`CredentialKey`** (StrEnum). The table below lists each key in order, with its label, its keyring name and whether it is required (**R**) or optional (**O**):

    | Key | Label | Keyring name | |
    |---|---|---|---|
    | `claude` | Claude API key | `anthropic/api_key` | R |
    | `google_places` | Google Places API key | `google_places/api_key` | O |
    | `hunter` | Hunter API key | `hunter/api_key` | R |
    | `prospeo` | Prospeo API key | `prospeo/api_key` | O |
    | `companies_house` | Companies House API key | `companies_house/api_key` | R |
    | `telegram_bot` | Telegram bot token | `telegram/bot_token` | R |
    | `unsubscribe_bearer` | Unsubscribe Endpoint bearer secret | `unsub/bearer` | R |
    | `backup_passphrase` | Backup passphrase | `backup/passphrase` | O |
    | `job_aggregator` | Job aggregator API key | `job_aggregator/api_key` | O |

  - **`Credential`** (frozen dataclass): `key`, `label`, `secret_name`, `required`, and the property `meta_name` (`"meta/" + secret_name`).
  - **Access.** `REGISTRY` (a Mapping in declaration order). `credential(key)` raises `UnknownCredential` (`unknown_credential`). `declared()` returns the credentials in order. Both read the module global at call time.
  - **Errors** (`DomainError` subclasses):
    - `UnknownCredential` (`unknown_credential`);
    - `InvalidCredentialValue` (`invalid_credential`), with `.key` and `.reason`. Its message is `"<key>: <reason>"` and never includes the value.
  - **Times.** `encode_updated_at(at)` writes `YYYY-MM-DDTHH:MM:SS.ffffffZ`; a naive time raises `ValueError`. `decode_updated_at(text)` returns an aware UTC time, or `None` for anything else.
- **Port helper.** `read_credential(secrets, key) -> str` is added to `nextron/ports/secrets.py`. It reads the credential's keyring entry. If there is none, it raises `AdapterError(False, "credential_not_set", "credential <key> is not set")`. Every adapter reads its credential this way, at call time, and never keeps it.
- **Service** (`nextron/app/setup/api.py`). `Credentials(secrets: SecretsPort, clock: Clock)` is not a `@use_case` and touches no database. One `threading.Lock` serialises its methods. Its types are:
  - `CredentialState` (StrEnum): `set`, `not set`, `unknown`.
  - `CredentialStatus(credential, state, updated_at: datetime | None)`.
  - `CredentialStoreFailed(Exception)`, with `.key`.

  Its methods are:
  - **`statuses()`** gives one status per declared credential; **`status(key)`** gives one.
    - The state comes from reading the credential's own entry: present is `set`, absent is `not set`. The value is dropped at once.
    - `updated_at` comes from decoding the `meta_name` entry, and only when the state is `set`.
    - Any `AdapterError` gives `unknown`, with no time.
  - **`save(key, text) -> CredentialStatus`.**
    1. Strip `text`. A blank result raises `InvalidCredentialValue` with reason `enter a value`. More than `MAX_SECRET_LENGTH` UTF-16 units (`secret_length`) raises it with reason `SECRET_TOO_LONG`, "secret exceeds 1,280 characters". Either way nothing is written.
    2. Write the stripped value, then `meta_name` = `encode_updated_at(clock.now())`.
    3. Log `credential_saved` with `credential=<key>`.
  - **`remove(key) -> CredentialStatus`.** Delete the entry, then `meta_name`, and log `credential_removed`.
  - **Failures.** An undeclared key raises `UnknownCredential` before any keyring call. An `AdapterError` while saving or removing logs `credential_store_failed` (`credential`, `operation`, `code`) and raises `CredentialStoreFailed`.
- **Page** (`nextron/web/credentials_page.py` holds the view model; routes go in `create_app`, which gains `credentials: Credentials`). Every route is `@personal_data(False)` and answers through `page()`, which sends `no-store`.
  - **`GET /credentials`** (`credentials_page`) renders `credentials.html`, which extends `base.html`. It has an introduction and a link back to Settings, then includes `credential_row.html` once per credential, in registry order.
  - **Each row** (`<div class="credential" id="credential-<key>">`) shows:
    - the label and "required" or "optional";
    - its state: `Set, last updated YYYY-MM-DD HH:MM UTC` (or `Set, last update time unknown`), `Not set`, or `Unknown: Windows Credential Manager could not be read`.
  - **The save form** (`hx-post="/credentials/save" hx-target="#credential-<key>" hx-swap="outerHTML"`) holds:
    - `csrf_field()`;
    - a hidden `credential`;
    - `<input type="password" name="value" autocomplete="off">`, with no `value` attribute and no `maxlength`, and a hint: at most 1,280 characters, spaces at either end are dropped, the value is never shown;
    - a Save button.
  - **The remove form** appears only when the row is `set`. It posts to `/credentials/remove` with `credential` and a Remove button.
  - **`POST /credentials/save`** (`credential_save`). Leaving `csrf_token` aside, the fields must be exactly `credential` and `value`, each given once, and `credential` must be a declared key. Anything else raises `MalformedForm` (400).
    - Saved: the row, empty field, with `<p role="status">Saved.</p>`.
    - `InvalidCredentialValue`: 200, the row with its reason in `role="alert"` and the empty field.
    - `CredentialStoreFailed`: 200, the row as `status(key)` reads it now, with `role="alert"` "Windows Credential Manager failed. The row shows what is stored now."
  - **`POST /credentials/remove`** (`credential_remove`). The fields are `credential`, plus an optional `confirm` that must be exactly `yes`; anything else is `MalformedForm`.
    - Without `confirm`: the row in its confirming state. It asks, with `role="alert"`, whether to remove the label from Windows Credential Manager. A form posts `credential` with `confirm=yes` ("Yes, remove it"), and a plain link to `/credentials` cancels.
    - With `confirm=yes`: `remove`, then the row with "Removed."
    - `CredentialStoreFailed` is handled as on save.
  - **Settings link.** `settings.html` links to `/credentials` ("API keys and tokens are on the Credentials page").
- **Planted-secret test** (`tests/app/test_credential_secrecy.py`), on the real `units` with `Dashboard(units=units)`:
  - **The planted value** is about 20 characters, and no redaction rule would mask it.
  - **The flow:**
    1. Save it through `POST /credentials/save` for `hunter`, then `GET /credentials`.
    2. A test fake adapter reads it with `read_credential` and sends it as a bearer header to a fake transport. The transport records it, then fails, and the adapter raises an `AdapterError` and logs.
    3. Send each of these:
       - a too-long value that contains it;
       - a CSRF-refused save that carries it;
       - a malformed body that carries it (a field without `=`);
       - a repeated `value`.
    4. Remove it in two steps.
  - **The checks:**
    - The value appears in no HTTP response (status, headers and body).
    - It appears in no captured log record, checked before redaction: the message, args, every attribute and the formatted exception.
    - It appears in no value of any column of any table in the database (`sqlite_master`). This covers `jobs.payload` once Story 1.10 adds it.

**Never:**
- Don't add a dependency, a `Form()` parameter, `include_router`, an `async def` handler or a migration.
- Never put a credential value in a response, a log record, an exception message, the database, a file, a job payload or the `settings` table. Never echo a submitted value, even when refused.
- Never use `maxlength` on a credential input: the browser would cut a pasted value short without a word.
- **Out of scope:**
  - the Google client import and OAuth grant buttons (1.9);
  - the doctor's required-credentials check (1.14);
  - `k_tok`/`k_edge` (Epic 5);
  - any real adapter;
  - HTMX error notifications (1.12).
- Don't edit the vendored HTMX, the `htmx-config`, `NAV_SECTIONS`, or anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

### Decisions made overnight (pending user review)

1. **The panel is its own page, `/credentials`, linked from the Settings page.** The top navigation keeps UX-DR1's six sections.
2. **Keyring names** are the ones in the registry table. `backup/passphrase` is the name Story 1.17 already fixes. The backup passphrase is optional, as the acceptance criteria list it; the doctor check in Story 1.17 covers it.
3. **"Set" is read from the credential's own entry, and only the time from `meta/<name>`.** The row can never say "set" for a credential that is gone. A missing or unreadable meta entry shows "last update time unknown".
4. **Removing asks for confirmation on the server.** The first Remove click only shows the question. Deleting needs a second POST with `confirm=yes`.
5. **Spaces at either end are dropped before storing.** A blank value is refused ("enter a value") and never deletes anything. Removing is a separate action.
6. **Times are shown in UTC,** because the page can't assume `sender_timezone` is set.
7. **The value field uses `autocomplete="new-password"`, not `off` (added at review).** Chrome and Edge ignore `off` on password fields, so they could fill a saved password into a row or offer to save an API key in the browser's password manager. MDN documents `new-password` as the way to stop that autofill. The page also tells Mac to decline any browser offer to save or fill a password there.
8. **Keyring calls are serialised within one process (added at review).** WinVault's write and delete are read-then-write steps on one shared target. Two operations at once on different names could lose or delete an entry. `nextron open` runs in its own process, which this lock doesn't cover.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Panel | Fresh fake secrets; then `hunter` saved at T | 9 rows in order: required and optional flags as the table says, all `Not set`; then `hunter` reads `Set, last updated <T> UTC`. No value appears anywhere in the HTML. | N/A |
| Save | `"  abc  "` for `claude` at T | Entry `anthropic/api_key` = `abc`; `meta/anthropic/api_key` = T's text; the row says Saved with an empty field; `credential_saved` is logged | N/A |
| Replace | Save again at T2 | The value and meta are replaced; the row shows T2 | N/A |
| Refused | `"   "`; 1,281 units (or 641 non-BMP characters) | `enter a value`; `secret exceeds 1,280 characters`; nothing written; exactly 1,280 units is accepted | A 200 row with an alert |
| Remove | Remove, then remove with `confirm=yes` | Step 1 deletes nothing and asks; step 2 deletes the entry and `meta/...`, and the row says `Not set` | N/A |
| Store fails | The fake has `failing={"write"}`, `{"delete"}` or `{"read"}` | Save or remove gives the alert and the row as read now; a failing read shows `Unknown`; `credential_store_failed` is logged without the value | `CredentialStoreFailed` |
| Meta drift | The entry exists with meta missing or `garbage`; meta exists with no entry | `Set, last update time unknown`; `Not set` | N/A |
| Malformed | An unknown `credential`, a missing or extra field, a repeated `value`, `confirm=no` | 400 `Request refused: malformed form`; nothing written | `request_refused` with `check=form` |
| Unchecked | No CSRF token or a wrong one; no session | 403 or 401 (Stories 1.4 and 1.5); nothing written | Existing |
| Adapter read | `read_credential` for a set key, and for an unset one | The value; `AdapterError` `credential_not_set` | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/ports/secrets.py` -- `SecretsPort`, `secret_length`, `MAX_SECRET_LENGTH`, `SECRET_TOO_LONG` (reuse; don't duplicate the length rule in the domain). Add `read_credential` and `CREDENTIAL_NOT_SET`. Ports may import the domain (contract 1 layers).
- `nextron-engine/nextron/app/ops/api.py` -- `DashboardEntry(secrets, clock)`: the pattern to mirror for an injected service that holds a lock and has no unit of work.
- `nextron-engine/nextron/domain/open_code.py` -- `TIME_FORMAT` and `TIME_PATTERN`: the same `…ffffffZ` text the meta entry uses. Don't import it; credentials own their format.
- `nextron-engine/nextron/domain/errors.py` -- `DomainError` with a unique `code`. `tests/contracts/test_error_contracts.py` picks up new domain modules by itself.
- `nextron-engine/nextron/web/app.py` -- `create_app`:
  - add `credentials: Credentials`;
  - add the three routes next to the Settings routes;
  - reuse `page()` for the page and the row partials;
  - the docstring's route list.
- `nextron-engine/nextron/web/forms.py` -- `FormFields`, `MalformedForm`, `WRONG_FIELDS`, `REPEATED_FIELD`. An unknown credential is `WRONG_FIELDS`.
- `nextron-engine/nextron/web/settings_page.py` -- the view-model pattern (`submitted_texts`) that `credentials_page.py` mirrors. Web imports only `nextron.app.*.api` and the domain, not `nextron.ports` (the convention in `nextron/web/__init__.py`).
- `nextron-engine/nextron/web/templates/settings.html`, `settings_form.html`, `static/app.css` -- the markup and style conventions:
  - no inline style;
  - `.notice` and `.notice-refused` exist;
  - add `.credential` row styles and a secondary button style.
- `nextron-engine/nextron/main.py:128-152` -- `build_app` makes one `secrets` (`KeyringSecrets(config.env)` unless injected) and shares it, with the one clock, between `build_entry` and `Credentials`. Update the module docstring.
- `nextron-engine/tests/dashboard.py` -- `Dashboard` adds `credentials = Credentials(self.secrets, self.clock)` and passes it to `create_app`.
- `nextron-engine/tests/fakes/secrets.py` -- `InMemorySecrets` (`store`, `failing`, `calls`).
- **Existing tests to update:**
  - `tests/contracts/test_web_contracts.py:143` (the route paths), `:254-256` and `:414-416` (the undeclared and rule-breaking route names, which may stay as they are), and `:503` (the template list gains `credential_row.html` and `credentials.html`);
  - the `FILES` list in `tests/contracts/test_source_tree.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/credentials.py` -- the registry, errors and time text -- AD-12.
- [x] `nextron-engine/nextron/ports/secrets.py` -- `read_credential`.
- [x] `nextron-engine/nextron/app/setup/api.py` -- `Credentials`, `CredentialState`, `CredentialStatus` and `CredentialStoreFailed`; update the docstring.
- [x] `nextron-engine/nextron/web/credentials_page.py`, `nextron/web/app.py`, `nextron/web/templates/credentials.html`, `nextron/web/templates/credential_row.html`, `nextron/web/templates/settings.html`, `nextron/web/static/app.css`, `nextron/web/__init__.py` -- the page -- UX-DR27.
- [x] `nextron-engine/nextron/main.py`, `tests/dashboard.py` -- wiring.
- [x] `nextron-engine/tests/unit/test_credentials.py` -- the registry (the table above, verbatim), the times, `read_credential`, and the Save, Replace, Refused, Remove, Store fails and Meta drift rows at service level with `InMemorySecrets` and `FrozenClock`.
- [x] `nextron-engine/tests/unit/test_credentials_page.py` -- the Panel, Save, Refused, Remove, Store fails, Malformed and Unchecked rows through `Dashboard`, parsing the HTML. Every form follows the rules; no password input has a `value` or `maxlength`; Settings links to `/credentials`.
- [x] `nextron-engine/tests/contracts/test_credential_contracts.py` -- every keyring name and `meta_name` passes `is_secret_name`; names are unique; none starts with `meta/` or `session/`. Include a live proof on a probe registry.
- [x] `nextron-engine/tests/app/test_credential_secrecy.py` -- the planted-secret test.
- [x] The existing tests listed in the Code Map -- updates.
- [x] `nextron-engine/README.md` -- a Credentials section under Secrets: the page, the registry table, `meta/<name>`, `read_credential`, and decisions 1–6 as plain rules.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given the real Credential Manager under `nextron-engine/test`, when a one-off script (not a test) saves, reads the status of and removes `hunter` through `Credentials(KeyringSecrets("test"), SystemClock())`, then the status goes from `not set` to `set` with a time and back to `not set`. Afterwards no `hunter/api_key` or `meta/hunter/api_key` entry remains, and the value is never printed.

## Implementation Notes

Choices made where the spec left room (none changes the intent):
- **An unreadable meta entry is `set` with no time, not `unknown`.** "Any `AdapterError` gives `unknown`" is applied to the read of the credential's own entry; a failure reading `meta/<name>` follows decision 3 ("a missing or unreadable meta entry shows 'last update time unknown'"). Tested in `test_an_unreadable_meta_entry_leaves_the_credential_set_with_an_unknown_time`.
- **`save` returns the status it just wrote** (`set`, with the time decoded from the stamp it stored) instead of reading the value back from Credential Manager, so a save never pulls the value into memory a second time.
- **`CredentialStoreFailed` is raised `from None`**, so no chained adapter or backend exception travels with it; the log record carries the `code`. The service also accepts only `str` text (anything else is refused as "enter a value").
- **The confirming row shows only the question,** "Yes, remove it" and the Cancel link (no save form), so the second step is unambiguous.
- **A refusal reads "Nothing was saved: <reason>."** (for example "Nothing was saved: secret exceeds 1,280 characters.").
- **The registry checks itself at import** (`_declare`): a duplicate key, a key that isn't a `CredentialKey`, or an undeclared member raises `TypeError`.
- `test_web_contracts.py:254-256` and `:414-416` were extended to name the three new routes as well.

Review fixes (pass 1, P1–P9; see the Review Triage Log):
- **P1 (decision 8).** `KeyringSecrets` runs every backend call under one module-level `_BACKEND_LOCK`, because `WinVaultKeyring` moves entries through the shared target on write and delete. The lock covers one process only (`nextron open` writes from its own). This is tested in `test_secrets.py::test_two_instances_never_call_the_backend_at_once`.
- **P2 (decision 7).** The value input uses `autocomplete="new-password"` instead of the frozen `off`, because Chrome and Edge ignore `off` on password fields. The introduction also asks Mac to decline any browser offer to save or fill a password.
- **P4.** The introduction names the environment the values are stored for (`env` is passed to `credentials.html`).
- The hint says a character outside the Basic Multilingual Plane counts as 2.
- `Credentials._status` logs `credential_read_failed` (`credential`, `entry` = `value`/`meta`, `code`) on each read failure.
- Tests tightened:
  - the lock test now has two competing reads;
  - the contracts check `KEY_PATTERN` and unique labels;
  - the page tests check the Remove form's exact named controls and the confirm form's `method`/`hx-target`/`hx-swap`, and submit each rendered form as a browser would.

**Final verification (after pass 1).** `uv run pytest -q`: 1960 passed, 1 skipped (`ai_eval`). `uv run lint-imports`: 29 kept, 0 broken. Before the review fixes the counts were 1953 passed, 1 skipped, and 29 kept. The one-off real Credential Manager script (`Credentials(KeyringSecrets("test"), SystemClock())`, kept out of the repo) printed `not set` → `set` with `updated_at` 2026-11-17 00:36:51 UTC → `not set`, and neither `hunter/api_key` nor `meta/hunter/api_key` remained (confirmed with `cmdkey /list`); the value was never printed. A mutation check (making the save handler log the submitted text) made `test_credential_secrecy.py` fail as expected.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (13 findings), EC = edge-case hunter (6), VG = verification gap (2 gaps and 1 other finding).
- **Counts:** 22 raw findings, 17 after deduplication. Rows 1–5 merge identical claims.
- **Outcomes:** 9 rows patched (P1–P9), 0 deferred, 8 rejected.
- **No loopback.**
  - Row 2 changes a frozen detail (`autocomplete="off"`). Only one reading keeps the intent, that values never land outside Credential Manager. It goes the conservative way under Mac's standing instruction, as overnight decision 7, as in the Story 1.6 precedent.
  - Row 1's fix is in the Story 1.4 adapter, which this story exposes. It is recorded as decision 8.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | EC, VG | WinVault's `set_password` and `delete_password` are read-then-write steps on the one shared target. `Credentials` and `DashboardEntry` share one `KeyringSecrets` but hold separate locks, so a save beside `/enter`'s code delete, or two writes at once, can lose or delete an entry while the row says "Saved." | medium | Verified in `keyring/backends/Windows.py`. `set_password` moves the target to `<user>@<service>`, then overwrites the target. `delete_password` deletes whatever is at the target after its read. Silent loss of a credential. A process-wide lock in the adapter is the smallest fix. | P1 (decision 8) |
| 2 | EC, BH | Chrome and Edge ignore `autocomplete="off"` on password inputs. They may autofill a saved password into a row (and Save overwrites a real key), or offer to save the API key in the browser's password store. | medium | True of current Chromium behaviour; MDN documents `new-password` to stop autofill. A save prompt that Mac accepts puts a key outside Credential Manager (NFR-18). An attribute change plus one sentence. | P2 (decision 7) |
| 3 | EC, BH | A failed `meta` write during a replace leaves the old time beside the failure alert, so the old value looks kept. | low | True, but it needs Credential Manager to fail between two writes, and the alert already prompts a retry, which fixes it. Both values are Mac's own. The fix adds a delete call and a branch. | reject |
| 4 | EC, BH | Remove posted for a credential that isn't set (from a stale tab) asks, then logs `credential_removed` and says "Removed." | low | True only from a stale tab or a crafted POST. The row then truthfully shows "Not set", and deleting a missing entry does nothing. The fix adds a state branch. | reject |
| 5 | EC, BH | An empty stored entry counts as "set", and `read_credential` returns `""`. | maybe-false | The engine never writes an empty value (`check_secret_value`). Someone would have to hand-create that exact target with an empty blob. Settling it needs a check that Credential Manager can hold an empty generic blob. Even if true it would be low: the adapter fails loudly at the provider. | reject |
| 6 | EC | A pasted value with CR, LF, NUL or other control characters is stored, and a later HTTP library could reject the header and echo it. | low | Browsers strip CR and LF from password inputs, so NUL or tab arrive only from a crafted POST or an odd paste. Redaction rule 4 masks `Bearer` values. Refusing them adds a rule beyond the frozen AC (non-empty and at most 1,280). | reject |
| 7 | BH | Partial saves and removes are logged only as `credential_store_failed`, without which entry failed. | low | True, but it needs a failure between the two writes, and the record names the credential, the operation and the code. Tracking the step adds state for a rare case. | reject |
| 8 | BH | A failed Credential Manager read in `_status` is never logged. | low | Verified: both `except AdapterError` branches return silently, so a page reading "Unknown" leaves nothing to diagnose. One log call per branch. | P3 |
| 9 | BH | Focus is lost after each HTMX swap. | low | Cosmetic for a mouse-driven single operator. The notices carry `role` status or alert. This is the same call as Story 1.6's row 18. | reject |
| 10 | BH | The page doesn't say which environment it writes to. | low | Verified: neither `credentials.html` nor `base.html` names the environment, yet entries are per environment, and keys typed into a `dev` dashboard leave `prod` unset. `env` is already in `create_app`, so it is one context variable and one sentence. | P4 |
| 11 | BH | Nothing summarises which required credentials are missing. | low | Each row shows "required" or "optional" and "Not set". The readiness list belongs to `nextron doctor` and the health panel (Stories 1.14 and 1.15). A summary adds UI and code. | reject |
| 12 | BH | `secrets.most == 1` in the concurrency test can never fail. | low | Verified: the second thread runs `save`, which never calls `read`. Only `blocked` proves the lock. Making the second call read-path is one argument. | P5 |
| 13 | BH | The secrecy test's file check never sees a log file. | false | Every byte of `nextron.log` comes from a `LogRecord`. The test checks every captured record before redaction (message, args, attributes, exception), which is stricter than the formatted file. | reject |
| 14 | BH | The contract doesn't check that keys are safe in `id`/`hx-target` selectors, or that labels are unique. | low | True: a future per-`kid` key with `.` or `/` would break `#credential-<key>`. One check in the contract test. | P6 |
| 15 | BH | The hint says "characters", but the limit counts UTF-16 units (an emoji counts 2). | low | True: the README explains it and the page doesn't. A direct text fix. | P7 |
| 16 | VG | The first-step Remove form's fields are never checked exactly. An extra hidden `confirm=yes` makes the first click delete, and every test still passes (mutation-demonstrated). | medium | Pre-verified gap. Decision 4 (remove asks first) could silently become a one-click delete. The fix is test-only. | P8 |
| 17 | VG | The confirm form's `method`, `hx-target` and `hx-swap` are never checked. Removing them still passes every test (mutation-demonstrated), and "Removed." would land inside the old row. | low | Pre-verified gap. The fix is test-only. | P9 |

## Design Notes

```python
status = credentials.save(CredentialKey.HUNTER, form_text)    # InvalidCredentialValue | CredentialStoreFailed
key = read_credential(secrets, CredentialKey.HUNTER)          # in an adapter, at call time
```

**Choices the user won't notice:**
- **A refusal answers 200,** so HTMX swaps the row in, as on the Settings page.
- **Each row is its own small form.** One save never touches another credential, and a stale row can't overwrite the others.
- **The service reads the value only to learn whether it exists.** Credential Manager has no existence-only call. The value never leaves `status()`.
- **The secret is written before its meta entry.** A failed meta write can only understate the time, never claim a write that didn't happen.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass, with `ai_eval` skipped.
- `uv run lint-imports` -- expected: every contract (29) KEPT.
