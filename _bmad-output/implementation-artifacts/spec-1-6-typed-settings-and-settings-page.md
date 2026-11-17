---
title: 'Story 1.6: Declare typed settings once and edit them on a Settings page'
type: 'feature'
created: '2026-11-17'
status: 'done'
baseline_commit: '4c1974497c92d957df6f6a7c348ba34f7ef94475'
route: 'dispatch'
review_loop_iteration: 0
story_key: '1-6-declare-typed-settings-once-and-edit-them-on-a-settings-page'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing holds operator configuration yet. Each later story would invent its own keys, and nothing refuses a bad value before it takes effect (AD-12, SC-5, NFR-7, NFR-40, UX-DR23).

**Approach:**
- **One schema.** `nextron/domain/settings_schema.py` declares every key with its type, default or `REQUIRED`, unit and validator. This story declares only `sender_timezone`.
- **One table and API.** A `settings` table (owner `setup`, migration `0002_1_6_settings`), the `Setup.set` and `Setup.save` use cases, and `queries.setup.get`.
- **One page.** `GET /settings` renders a form generated from the schema. `POST /settings` validates every field and stores all or nothing.

The acceptance criteria are Story 1.6 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Schema** (`nextron/domain/settings_schema.py`, standard library only):
  - **Enums.**
    - `SettingType` (StrEnum): `string`, `integer`, `boolean`, `time`, `timezone`, `duration`, `money`, `list`, `availability`.
    - `Section` (StrEnum, in page order; the value is the heading): `Footer and booking`, `Sender`, `Alerts and recipients`, `Sending`, `Budget and limits`, `Engine`.
    - `Key` (StrEnum): `SENDER_TIMEZONE = "sender_timezone"`.
    - `REQUIRED`: a sentinel.
  - **`Setting`** (frozen dataclass): `key`, `type`, `section`, `label`, `unit`, `default` (`REQUIRED` or a typed value), `rules: tuple[Rule, ...] = ()` and `timezone_key: str | None = None`.
  - **`Rule(name, message, check)`**, with the factories `minimum(bound, shown)`, `maximum(bound, shown)` and `max_length(n)` (`len` of a string or a list).
  - **`SCHEMA: Mapping[str, Setting]`**, in declaration order.
  - **`schema_problems(schema, keys=Key) -> list[str]`** runs at import and raises `TypeError` when the list isn't empty. It checks that:
    - each key matches `[a-z][a-z0-9_]{0,63}` and equals its mapping key;
    - the real schema's keys equal `Key`;
    - the label and unit aren't empty;
    - any default passes validation and is already in canonical form;
    - a `time` or `availability` setting names a `timezone_key` that is a declared `timezone` setting, and no other type names one.
  - **Access.** Other modules reach the schema only through `setting(key)` (which raises `UnknownSetting`) and `declared()` (in order). Both read the module global at call time, so tests can swap it with `monkeypatch.setattr(settings_schema, "SCHEMA", probe)`.
  - **Text forms.** The canonical text is what `encode` returns, and it is what the table stores:

    | Type | Value | Text |
    |---|---|---|
    | string | `str` | One line, stripped, with no `Cc`, `Cf`, `Zl` or `Zp` character, at most 1,000 characters. |
    | integer | `int` (not `bool`) | `-?[0-9]{1,18}` |
    | boolean | `bool` | `true` or `false` |
    | time | `datetime.time` with no seconds and no `tzinfo` | `HH:MM`, from 00:00 to 23:59 |
    | timezone | `str` | An exact member of `zoneinfo.available_timezones()`, except `Factory` |
    | duration | `timedelta`, at least zero, whole seconds | ISO 8601 `PnW` or `PnDTnHnMnS`: uppercase, each n at most 6 digits, no years or months. Canonical form: days, then the T part; zero is `PT0S`. |
    | money | `int` micro-dollars, at least zero | Dollars, `[0-9]{1,9}(\.[0-9]{1,6})?`; canonical form has at least 2 decimals |
    | list | `tuple[str, ...]` | One item per line, blank lines dropped. Each item is a valid string; at most 200 items; no duplicates. |
    | availability | `tuple[Slot, ...]`, where `Slot(weekday 0–6, start, end)` | One slot per line, such as `Mon 09:00-17:00` (weekday `Mon` to `Sun`, any case), with start before end, no overlaps within a day, at most 200 lines; canonical form is sorted |

  - **Converting.**
    - `parse(setting, text) -> value` reads a text form.
    - `encode(setting, value) -> str` checks the type, then the rules, then returns the canonical text.
    - Stored text is decoded with `parse`.
    - A failure raises `InvalidSettingValue` with `.key`, `.rule` and `.reason`. The message is `"<key>: <reason> (rule <name>)"` and never includes the value.
  - **Errors** (`DomainError` subclasses):
    - `UnknownSetting` (`unknown_setting`);
    - `InvalidSettingValue` (`invalid_setting`);
    - `InvalidSettings` (`invalid_settings`), with `.problems: Mapping[str, InvalidSettingValue]`;
    - `SettingNotSet` (`setting_not_set`).
  - **Declared key.** `sender_timezone` is a `timezone` in the `Sender` section, `REQUIRED`, with the unit "IANA timezone name, such as America/Los_Angeles".
- **Table** `settings`. The model is `SettingRecord` in `nextron/db/models/setup.py`; the migration is `0002_1_6_settings`.
  - **Columns:** `id` (Ulid primary key), `key` (String(64), unique), `value` (Text) and `updated_at` (UtcDateTime).
  - **Owner:** `setup`.
  - **PII:** `value` is `redact`; the other columns are `none`.
- **Repository** (`nextron/db/repos/setup/settings.py`, through `current().session`):
  - `stored_text(key)`;
  - `stored_texts()`;
  - `upsert(key, text, at)`, using SQLite `ON CONFLICT(key) DO UPDATE`;
  - `delete(key)`.
- **Use cases** (`nextron/app/setup/api.py`, `class Setup(clock)`). Each `@use_case` reads the clock once and opens one `uow.transaction()`.
  - **`set(key, value)`:** raises `UnknownSetting` for an unknown key, then `encode` (`InvalidSettingValue`), then upserts with `updated_at = now`.
  - **`save(texts: Mapping[str, str]) -> dict[str, str]`:**
    - An unknown key raises `UnknownSetting`.
    - Each text is stripped, then:
      - blank, on a key with a default: delete the row;
      - blank, on a required key that isn't set: no change;
      - blank, on a required key that is set: a problem with rule `required`;
      - otherwise: `parse`, and write only when the canonical text differs from the stored one.
    - Any problem raises `InvalidSettings` before anything is written.
    - It returns the stored texts after the save.
    - It logs `settings_changed` with `keys` (the changed names, comma-joined), never the values. `set` logs the same event.
- **Queries** (`nextron/app/queries/setup.py`):
  - `get(key)` returns the decoded stored value, else the default. An unset required key raises `SettingNotSet`.
  - `stored_texts()`.
- **Page** (`nextron/web/settings_page.py`, plus `create_app`, which gains `setup: Setup`; `page(request, template, **context)`):
  - **`GET /settings`** (`settings_page`, `@personal_data(True)`) renders `settings.html`, which extends `base.html` and includes `settings_form.html`.
  - **The form:**
    - The `<form>` tag: `method="post" hx-post="/settings" hx-target="this" hx-swap="outerHTML"`, plus `{{ csrf_field() }}`.
    - One `<fieldset>` with a `<legend>` per section that has fields, in `Section` order.
    - Each field has:
      - a `<label>`;
      - an input: a `textarea` for list and availability, a true/false `select` for boolean, otherwise `input type="text"`;
      - a hint: the unit, plus "required" or "default: <text>; leave blank to use it";
      - its value: the stored text, else the default text, else blank.
  - **`POST /settings`** (`settings_save`, `@personal_data(True)`):
    1. **Parse.** An `async` dependency, `form_fields` in `nextron/web/forms.py`, parses the cached urlencoded body. It needs exactly one `Content-Type` and an ASCII body, and calls `parse_qs` with `keep_blank_values=True, strict_parsing=True, max_num_fields=1000, encoding="utf-8", errors="strict"`.
    2. **Check the fields.** Leaving `csrf_token` aside, every field must be a declared key with exactly one value, and every declared key must be present. Anything else raises `MalformedForm`, answered with 400 `Request refused: malformed form` (`no-store`) and logged as `request_refused` with `check=form`.
    3. **Save.** `with units.open(): setup.save(texts)`.
       - **Success:** renders the form from the returned texts, with `<p role="status">Settings saved.</p>`.
       - **`InvalidSettings`:** answers 200 with the form re-rendered from the submitted texts. Each invalid field gets `aria-invalid="true"` and its rule's reason beside it, and the form says, with `role="alert"`, that nothing was saved.
- **Contract tests** (`tests/contracts/test_settings_contracts.py`), each with a live proof on a probe schema:
  - `schema_problems(SCHEMA) == []`.
  - No key name or label mentions a protected feature while being `boolean` or holding an off token.
    - Protected-feature stems: `suppress`, `eligib`, `footer`, `opt_out`, `optout`, `unsub`, `market`.
    - Off tokens: `disable(d)`, `off`, `skip`, `bypass`, `ignore`, `override`, `without`, `no`, `not`, `never`, `allow`, `enable(d)`, `toggle`, `pause(d)`, `exempt`, `waive`.
  - No key name names a secret: `secret`, `password`, `passphrase`, `token`, `api_key`, `bearer` or `credential`. Secrets live only in keyring (AD-12).

**Never:**
- Don't add a dependency (`python-multipart` included), a `Form()` parameter, `include_router` or an `async def` handler.
- Never put a setting value in logs, `events` or error messages, or a secret in the `settings` table.
- No key may disable Suppression, Eligibility, the footer, opt-out handling or Market rules.
- **Out of scope:** other keys (later stories declare their own), the doctor's required-settings check (1.14), HTMX error notifications (1.12), credentials (1.7) and `FooterChanged`.
- Don't edit the vendored HTMX, the `htmx-config` in `base.html`, or anything outside `nextron-engine/` except this spec and `sprint-status.yaml`.

### Decisions made overnight (pending user review)

1. **Blank fields.**
   - A blank field with a default resets that setting to its default (the row is deleted).
   - A blank required field that was never set stays unset and isn't an error, so Mac can save other settings before he has every value (the footer postal address is still pending).
   - A required value that has been set can't be cleared (rule `required`).
2. **An unset required setting fails closed.** `queries.setup.get` raises `SettingNotSet` instead of returning `None`, so no consumer can run on a missing value by accident.
3. **Durations have no years or months,** because those have no fixed length. A later setting counted in months (retention) declares an `integer` with the unit "months".
4. **Input formats.**
   - Money is entered and shown in US dollars (up to 6 decimals) and held as integer micro-dollars.
   - Lists take one item per line.
   - Weekly availability takes one `Mon 09:00-17:00` slot per line, in the timezone its `timezone_key` names.
5. **Saving the page unchanged doesn't pin the defaults (added at review).** The page shows an unset setting's default as its value. When that value comes back unchanged, it stays unset, with no row, so a default changed in a later engine version still reaches it. This is the only reading that keeps the frozen page rule ("the stored text, else the default text") and decision 1's "leave blank to use it" true together.
6. **A timezone must be a region name (added at review).** The `timezone` type also refuses the fixed-offset and legacy names in `available_timezones()`: every name without a `/` except `UTC` (such as `EST`, `MST`, `HST`, `EST5EDT`, `GB` or `Zulu`), and every `Etc/` name (such as `Etc/GMT+5`, which means UTC−5). `EST` never moves for daylight saving, so a Sender who typed it would be an hour off half the year. Region links such as `US/Pacific` still work.
7. **Setting names are at most 31 characters (added at review).** Log redaction masks any run of 32 or more name characters as `<key>`, so a longer name would vanish from the `settings_changed` log line. This is the same limit as Alembic revision ids.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Set ok | `set(Key.SENDER_TIMEZONE, "Europe/London")` at clock time T, then again with another zone at T2 | One row: the value, and `updated_at` T, then T2, with the same `id`. `get` returns the value. | N/A |
| Set refused | key `"nope"`; values `"europe/london"`, `"Factory"`, `"Mars/Base"`, `5`, `None` | `UnknownSetting`; `InvalidSettingValue` with rule `timezone`; nothing written | The message names the key and the rule, never the value |
| Get | No row for a key with a default; no row for a required key; stored text that no longer parses | The default; `SettingNotSet`; `InvalidSettingValue` | N/A |
| Save ok | A probe schema of at least 3 keys; texts for all of them | Changed keys upserted; unchanged keys keep their `updated_at`; a blank field with a default deletes its row; returns the stored texts | N/A |
| Save refused | One invalid among valid fields; a blank required field that is set; an unknown key | `InvalidSettings` naming each invalid key and its rule, or `UnknownSetting`; the table is unchanged | N/A |
| Types | integer `42`/`-3` ok, `4.2`/`1_000`/`+1`/`٣` refused; time `07:00` ok, `7:00`/`24:00`/`07:00:00` refused; duration `P1W`→`P7D`, `P1DT2H` ok, `P1M`/`P1Y`/`PT`/`-PT1M`/`pt2m`/`PT1.5S` refused; money `20`→`20.00`, `0.000001` ok, `-1`/`1,000`/`$20`/`1.1234567` refused; list `a\r\n\r\nb`→`("a","b")`, a duplicate refused; availability `mon 09:00-12:00` → `Mon 09:00-12:00`, `12:00-09:00`/an overlap/`Monday` refused; `encode` refuses `True` for an integer, `1` for a boolean, a `time` with seconds, a negative `timedelta` | The canonical text or value, or `InvalidSettingValue` with the type's rule | N/A |
| Page GET | A session; a probe schema in 2 sections | Sections in `Section` order; labels; unit hints; stored, default or blank values; a `csrf_field` equal to the session's token; `hx-post`; `no-store`; the nav shows Settings | N/A |
| Page POST ok | All fields valid | 200: the form with "Settings saved." and canonical values; the database is updated | N/A |
| Page POST invalid | One field invalid, or holding `"><script>` | 200: the form with the submitted values escaped, the reason beside the invalid field, `aria-invalid`, and "nothing was saved"; the database is unchanged | N/A |
| Page POST malformed | A declared field missing, an unknown field, a repeated field, a multipart or JSON body, `%FF` | 400 plain refusal; the database is unchanged | `request_refused` with `check=form` |
| Page POST unchecked | No CSRF token, or a wrong one | 403 (Story 1.5); the database is unchanged | `request_refused` with `csrf_token` |
| Contract | Probe keys `suppression_off`, `skip_eligibility`, `footer_enabled` (boolean), `disable_opt_out`, `market_rules_bypass`, `unsubscribe_ignore`, `telegram_bot_token`; against `footer_postal_address` (string) | Each probe key is caught by name; the string footer key passes | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/domain/errors.py` -- `DomainError`: each subclass declares a unique `code`. `tests/contracts/test_error_contracts.py` imports every domain module and checks the codes.
- `nextron-engine/nextron/db/models/base.py` -- `ulid_pk()`, `UtcDateTime`, `NAMING_CONVENTION` (`unique=True` names the constraint `uq_settings_key`). `models/__init__.py` must import the new `setup` module, or a contract test fails.
- `nextron-engine/nextron/db/models/events.py`, `nextron/db/eventlog.py` -- the model pattern, and the write pattern (`current().session.execute(insert(...))`) to mirror.
- `nextron-engine/nextron/db/alembic/versions/0001_1_3_persistence_foundation.py` -- the migration pattern: explicit `pk_`/`uq_` names, `String(26)`/`String(27)` for ULID and time columns, and the `downgrade()` message.
- `nextron-engine/nextron/db/owners.py`, `nextron/db/pii_registry.py` -- add `settings`; the coverage contract fails on any unmapped column.
- `nextron-engine/nextron/db/repos/setup/__init__.py`, `nextron/app/setup/api.py`, `nextron/app/queries/setup.py` -- their docstrings say "Empty until a story adds…"; fill them.
- `nextron-engine/nextron/db/uow.py` -- `use_case` works on methods. `current()` and `transaction()`: a read-only unit's `session` works with no transaction (the GET path); a write unit's works only inside `transaction()`. An exception inside `transaction()` rolls back and re-raises.
- `nextron-engine/nextron/app/ops/api.py` -- `DashboardEntry(secrets, clock)` is the injected-service pattern that `Setup(clock)` mirrors.
- `nextron-engine/nextron/ports/clock.py` -- `Clock`. Only app services hold one, and they read it once per use case.
- `nextron-engine/nextron/web/app.py` -- `create_app`:
  - add `setup: Setup`;
  - `page()` takes `**context` and keeps `no-store`;
  - add the two routes and the `MalformedForm` handler;
  - `NAV_SECTIONS` already holds `settings_page`.
- `nextron-engine/nextron/web/csrf.py` -- `_presented_tokens` is the body-parsing pattern (Starlette caches the body after `require_csrf` reads it), `security_check_failed` is the refusal-response pattern, and `CSRF_FIELD`.
- `nextron-engine/nextron/web/routing.py` -- the route class prepends `require_csrf` to a POST's route dependencies, so a handler parameter `Annotated[..., Depends(form_fields)]` comes after it and the route rules still hold.
- `nextron-engine/nextron/web/templates/base.html`, `nextron/web/static/app.css` -- the layout. Add styles for fieldsets, hints, errors and notices; no inline style.
- `nextron-engine/nextron/main.py:128-152` -- `build_entry` and `build_app`: build the clock once and pass `setup=Setup(clock)`.
- `nextron-engine/tests/dashboard.py` -- `Dashboard` passes `setup=Setup(self.clock)`. Add a `form(**fields)`/urlencode helper for urlencoded POSTs. `tests/conftest.py` `units` gives real units of work at head.
- **Existing tests to update:**
  - `tests/contracts/test_web_contracts.py:143` (the route list) and `:494` (the template list);
  - `tests/unit/test_dashboard.py:707-728`: the nav now lists Settings, and the probe `/settings` route duplicates the real one;
  - `HEAD` in `tests/unit/test_migrations.py:36` and `tests/contracts/test_persistence_contracts.py:72`, plus that file's probe-chain rows: the `0002_*` probes become `0003_*`, and the message becomes "is revision 3";
  - the `FILES` list in `tests/contracts/test_source_tree.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/settings_schema.py` -- the schema, types, rules, `parse`/`encode`, errors and `sender_timezone` -- AD-12.
- [x] `nextron-engine/nextron/db/models/setup.py`, `nextron/db/models/__init__.py`, `nextron/db/alembic/versions/0002_1_6_settings.py`, `nextron/db/owners.py`, `nextron/db/pii_registry.py` -- the table -- AD-2, AD-3, AD-17.
- [x] `nextron-engine/nextron/db/repos/setup/settings.py` (+ `__init__.py` docstring) -- the repository.
- [x] `nextron-engine/nextron/app/setup/api.py`, `nextron/app/queries/setup.py` -- `Setup`, `get` and `stored_texts`.
- [x] `nextron-engine/nextron/web/forms.py`, `nextron/web/settings_page.py`, `nextron/web/app.py`, `nextron/web/templates/settings.html`, `nextron/web/templates/settings_form.html`, `nextron/web/static/app.css`, `nextron/web/__init__.py` -- the page -- UX-DR23.
- [x] `nextron-engine/nextron/main.py`, `tests/dashboard.py` -- wiring.
- [x] `nextron-engine/tests/unit/test_settings_schema.py` -- the Types row, the declaration checks and the `sender_timezone` declaration; no message contains the value.
- [x] `nextron-engine/tests/app/test_setup_settings.py` -- the Set, Get and Save rows, on the real `units` with a `FrozenClock` and a monkeypatched probe schema.
- [x] `nextron-engine/tests/unit/test_settings_page.py` -- the Page rows, through `Dashboard(units=units)`, parsing the HTML.
- [x] `nextron-engine/tests/contracts/test_settings_contracts.py` -- the Contract row and the schema check, with live proofs.
- [x] The existing tests listed in the Code Map -- updates.
- [x] `nextron-engine/README.md` -- a Settings section: how to declare a key, `get`, the page, and decisions 1–4.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run, then everything passes and every contract is kept.
- Given `serve` running in `dev` and a session, when Mac opens `/settings`, then the Sender section shows "Sender's timezone" as required. Saving `Mars/Base` shows the timezone rule's reason and stores nothing; saving `America/Los_Angeles` shows "Settings saved.", and `queries.setup.get(Key.SENDER_TIMEZONE)` returns it.

## Implementation Notes

- **Layout.** The two routes are defined in `create_app` (`nextron/web/app.py`), so the existing route-rule and declaration proofs cover them. `nextron/web/settings_page.py` holds the view model: `form_context`, `shown_values`, `hint` and `submitted_texts`. `csrf._media_type` became the public `media_type` so `forms.py` can reuse it.
- **Import-time check.** `check_schema(schema, keys=Key)` raises the `TypeError`; the module calls `check_schema(SCHEMA)` as its last statement. A unit test confirms that call from the source (AST). Re-executing the module under a test would register duplicate `DomainError` codes.
- **Choices the spec left open.**
  - A stored text is never blank. Blank on the page means "use the default", so `parse` and `encode` refuse blank text or a blank canonical value with rule `required`. Defaults may still be blank (`""`, `()`); the hint then shows "default: none".
  - `encode` normalises what has one meaning: it strips strings and list items, sorts availability slots and accepts a list in place of a tuple. `schema_problems` still flags any default that isn't already canonical, exact type included.
  - `encode` checks the type before the blank rule. So `set(Key.SENDER_TIMEZONE, "")` breaks rule `timezone`, not `required`.
  - The string type also refuses `Cs`: a lone surrogate would otherwise fail the SQLite write with a 500.
  - Durations must stay under 1,000,000 days, so the canonical day count fits 6 digits. Money must stay under $1,000,000,000.
  - `UnknownSetting` repeats the key only when it matches the key pattern; otherwise it says "(not a setting name)".
  - `settings_changed` is logged only when at least one key changed. Keys are in submission order, comma-joined.
  - `save` also refuses a value that isn't a `str`, as a problem with the type's rule.
  - The boolean `<select>` has a blank first option, "not set" or "use the default", so a blank can be submitted.
  - The textarea emits a newline right after its start tag, because browsers drop one leading newline.
- **Pinned defaults (resolved by P1).** The page pre-fills an unset defaulted field with its default text. `Setup.save` now compares a key without a row against its default text, so saving the page unchanged stores nothing for those keys (decision 5).
- **Contract test details.** Key names and labels are lowercased, and each run of other characters becomes `_` ("Opt-out" becomes `opt_out`). Stems match as substrings and off tokens as whole words. The secret check (after P5) matches its words as substrings and `key`, `pass`, `pwd` and `passwd` as whole words, in the key name and the label, so a future `max_tokens` key would be refused.
- **Existing tests.** One probe in `test_the_coverage_check_names_stale_doubled_and_unknown_entries` now starts from the real `TABLES`/`PII`, so the new `settings` table isn't reported as unmapped.
- **Review patches (pass 1).**
  - **P1, no pinned defaults.** A key without a row whose canonical text equals its default text isn't written or logged (decision 5).
  - **P2, region timezones only.** `timezone_names()` drops every name without a `/` except `UTC`, and every `Etc/` name. `US/Pacific` still works (decision 6).
  - **P3, reason wording.** `TIMEZONE_REASON` asks for a region name in exact capitals, such as America/Los_Angeles, with no "as listed".
  - **P4, stale stored values.** On GET, `stored_problems` parses each stored text. A failure flags its field (`aria-invalid` and the reason), without the "nothing was saved" alert; `form_context` takes `refused` explicitly.
  - **P5, wider secret check.** It now matches `private` anywhere and `key`, `pass`, `pwd` and `passwd` as whole words, in the key name and the label.
  - **P6, logged reason.** `MalformedForm` carries a fixed `reason` code (`not_urlencoded`, `bad_body`, `wrong_fields` or `repeated_field`), and `request_refused` logs it.
  - **P7, accurate docstring.** `forms.py` says `require_csrf` answers 403 first for a repeated `Content-Type`, a non-ASCII urlencoded body or more than 1,000 fields.
  - **P8, test.** A blank default renders "default: none".
  - **P9, test.** The blank boolean option reads "use the default" or "not set".
  - **P10, 31-character keys.** `KEY_PATTERN` is `[a-z][a-z0-9_]{0,30}` (decision 7). The `String(64)` column stays.
  - **P11, docs.** The README section is retitled as settled rules and covers decisions 5–7.
- **Final verification (after pass 1).** `uv run pytest -q`: 1825 passed, 1 skipped (`ai_eval`). `uv run lint-imports`: 29 contracts kept, 0 broken.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (18 findings), EC = edge-case hunter (9), VG = verification gap (2 gaps and 2 other findings).
- **Counts:** 31 raw findings, 23 after deduplication. Rows 1, 2, 7, 9, 11, 12 and 13 merge identical claims.
- **Outcomes:** 11 rows patched (P1–P11), 0 deferred, 12 rejected.
- **No loopback.**
  - Row 1 has only one reading compatible with the frozen page rule and decision 1, so it is a patch, recorded as overnight decision 5.
  - Rows 3 and 22 tighten a frozen detail in the conservative direction, under Mac's standing instruction to decide intent gaps overnight. They are recorded as decisions 6 and 7.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH, VG, EC | Saving the page unchanged writes the default for every unset defaulted key, so later default changes never reach them and "leave blank to use it" is false | medium | Verified: `shown_values` pre-fills the default text, and `Setup.save` writes whenever `canonical != before`, with `before is None` when there is no row. A one-condition fix in `save`. | P1 |
| 2 | BH, EC | A rule whose bound has the wrong type, on a `REQUIRED` setting, passes the import check and raises `TypeError` at parse time (500) | low | True (BH reproduced it), but it is a developer's declaration error that fails loudly on the first parse, and the declaring story's tests would hit it. Turning it into `InvalidSettingValue` would show Mac a rule he can never satisfy; probing rules needs a sample value per type. | reject |
| 3 | BH | `timezone` accepts `EST`, `MST`, `HST`, `EST5EDT`, `Etc/GMT+5`, `Zulu` and others: fixed offsets or reversed signs, which give the wrong local time half the year | medium | Verified: `available_timezones()` holds 45 names without `/` and 35 `Etc/` names. Excluding them is one filter in `timezone_names()`. | P2 (decision 6) |
| 4 | BH | The timezone reason says "exactly as listed", but the page lists nothing | low | True; the wording points at a list Mac can't see. A one-string correction. | P3 |
| 5 | BH | A `time` or `availability` hint never names its timezone | low | No such setting exists yet, and the declaring story writes the unit text, which can name the zone ("HH:MM, Sender's time"). A hint branch adds code for nothing today. | reject |
| 6 | BH | `Setup.set` upserts and logs even when the value is unchanged | low | True, but the frozen spec says `set` upserts with `updated_at = now`, and the AC says a valid value "is stored with `updated_at`". Skipping it adds a read and a branch for no user-visible gain. | reject |
| 7 | BH, EC | `GET /settings` shows a stored text that no longer parses with no flag, while every `get` raises | medium | Verified: the GET renders `shown_values(stored_texts())` without parsing. That state arises whenever a later story tightens a rule, and consumers then fail at run time. A few lines in the view. | P4 |
| 8 | BH | "Settings saved." appears while required settings are still unset | low | True by decision 1. Each such field's hint says "required", and the required-settings list belongs to `nextron doctor` and the health panel (Stories 1.14 and 1.15). | reject |
| 9 | BH, EC | A stale tab's save reverts a newer change | low | True, but there is one operator, and the fix is an optimistic-concurrency token per field (new hidden fields and checks). | reject |
| 10 | BH | Rows of removed keys stay forever, and a re-declared key can inherit old text | low | True, but no key is removed in v1. A story that removes a key adds a migration deleting its row. With P4, an inherited bad text is flagged on the page. | reject |
| 11 | BH, EC | The protected-feature contract checks names only, so a value such as `suppression_window_days = 0` could switch a feature off | low | The AC defines the check as key names. Value semantics are covered by each later story's own NFR-7 AC (for example 2.x Markets and 6.x Suppression). No machine check settles "means off". | reject |
| 12 | BH, EC | The secret check misses `hunter_key`, `smtp_pass`, `pwd`, `private_key` and labels | low | Verified: it only does substring checks on `SECRET_WORDS` over key names. Extending the words, adding whole-token `key`/`pass`/`pwd`/`passwd`, and checking labels is a direct correction. | P5 |
| 13 | BH, EC | Availability can't end at 24:00 or span midnight | low | True, but the Sender's booking hours never end at midnight in practice, and a `24:00` end needs a sentinel through parse, encode and compare. | reject |
| 14 | BH | `request_refused` with `check=form` drops the `MalformedForm` reason | low | True: the reasons are fixed strings with no values. Logging them is one extra field. | P6 |
| 15 | BH | The body is parsed twice, by `require_csrf` (lenient) and `form_fields` (strict) | low | They can only disagree in the strict direction: a body `form_fields` refuses gets 400, and one `require_csrf` refuses never reaches it. Nothing is stored either way. Sharing a parser couples CSRF to form handling. | reject |
| 16 | EC | `forms.py` documents 400 for a non-ASCII body, a repeated `Content-Type` or over 1,000 fields, but `require_csrf` refuses those first with 403 | low | Verified in `csrf._presented_tokens`: each raises `SecurityCheckFailed`. Correcting the docstring is direct. | P7 |
| 17 | BH | List items and strings aren't NFC-normalised, and list duplicates are case-sensitive (email addresses) | low | No list setting exists yet. A recipients list would get its own item rule (email syntax, case) in its story. Browsers send composed text. | reject |
| 18 | BH | After the swap, focus isn't restored or moved to the first invalid field | low | Cosmetic for a mouse-driven single operator. The `role="alert"` notice is announced. `autofocus` on the first invalid field needs loop state in the template. | reject |
| 19 | BH | The README carries a "Decisions made overnight… (pending Mac's review)" block that will go stale | low | True. The 1.5 README states its decisions as plain rules. Rewording the heading and adding decisions 5–7 as rules is direct. | P11 |
| 20 | VG | No test shows the "default: none" hint for a blank default | low | Pre-verified gap: no rendered probe has a blank default. | P8 |
| 21 | VG | No test checks the blank boolean option's text ("not set" or "use the default") | low | Pre-verified gap: only the option values are asserted. | P9 |
| 22 | VG | A key of 32 or more characters is logged as `<key>` by redaction | low | Verified: `redaction.py` masks 32-character runs, and `KEY_PATTERN` allows 64. Plausible keys reach 31 or more (`unsubscribe_failure_alert_after`). A one-number fix. | P10 (decision 7) |
| 23 | EC | Blank can't express "empty" for a list or string with a non-empty default | low | True by decision 1, but no such setting exists. A story that needs "none" expressible declares an empty default. A separate "empty" control adds UI. | reject |

## Design Notes

```python
SCHEMA = _declare(
    Setting(key=Key.SENDER_TIMEZONE, type=SettingType.TIMEZONE, section=Section.SENDER,
            label="Sender's timezone",
            unit="IANA timezone name, such as America/Los_Angeles", default=REQUIRED),
)
zone = ZoneInfo(queries.setup.get(Key.SENDER_TIMEZONE))   # SettingNotSet until Mac sets it
```

**Choices the user won't notice:**
- **An invalid save answers 200.** HTMX swaps only 2xx responses by default, and the alternative means changing `htmx-config`, which Story 1.12 owns.
- **Both routes declare `personal_data(True)`, and `value` is `redact`.** Later keys hold Nextron staff addresses and the postal address.
- **The table stores the canonical text,** and the typed value is derived when it is read. Storing the text keeps one format, and the type comes from the schema.
- **The page requires every declared field.** A stale or crafted form gets 400 instead of a partial save. `Setup.save` itself accepts a subset.
- **Sections come from UX-DR23 up front,** so later stories pick one instead of inventing headings.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass, with `ai_eval` skipped.
- `uv run lint-imports` -- expected: every contract (29) KEPT.
