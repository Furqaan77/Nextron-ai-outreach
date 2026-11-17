---
title: 'Story 4.7: Register the Sending Inboxes that Sequences bind to'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '8fac7b67e48d332a4f4fcb4ba26f2b11e5631d7d'
review_loop_iteration: 0
story_key: '4-7-register-the-sending-inboxes-that-sequences-bind-to'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Enrollment (4.8) must bind every new Sequence to one Sending Inbox from the start (AD-21), but no inbox can be recorded yet. The `sending` owner is an empty stub, and nothing enforces the hard rule that cold email never leaves from `nextronai.com` or a role address (FR-32), or that only allowlisted test inboxes exist outside prod (AD-14).

**Approach:** Give `sending` its first two tables (`sending_domains`, `sending_inboxes`), the complete AD-4 SendingInbox machine, and one use case, `register_inbox(address, label)`. It normalises the address with `domain.identity`, refuses what FR-32 and AD-14 forbid, and stores the inbox at `connecting`. Add a Sending Inboxes panel to add and list inboxes, and `queries.sending.inboxes_for_binding()` for enrollment.

## Boundaries & Constraints

**Always:**
- **Machine** (`nextron/domain/sending_inbox.py`, standard library only; mirror `pipeline_stage.py` and `machines.py`). `MACHINE = "sending_inbox"`.
  - `InboxStatus`: `connecting`, `active`, `paused`, `auth_failed`, `removed`.
  - `PauseCause`: `operator`, `bounce_rate`, `policy_bounce`, `blocklist`, `spam_rate`.
  - `InboxEvent`: `connect`, one `pause_<cause>` per pause cause (`PAUSE_EVENTS` maps each `PauseCause` to its event), `resume`, `auth_fail`, `reconsent`, `remove`.
  - `InboxCause`: `registered`, `connected`, the five pause-cause values, `resumed`, `invalid_grant`, `reconsented`, `operator_removed`.
  - `TRANSITIONS` (a `MappingProxyType`):
    - `connect`: `connecting` → `active` (`connected`);
    - each `pause_<c>`: `active` → `paused` (cause `<c>`);
    - `resume`: `paused` → `active` (`resumed`);
    - `auth_fail`: `active` or `paused` → `auth_failed` (`invalid_grant`);
    - `reconsent`: `auth_failed` → `active` (`reconsented`);
    - `remove`: every status except `removed` → `removed` (`operator_removed`).
  - `transition(current, event)` returns the row or raises `IllegalTransition`.
  - Also `CREATION = Transition(connecting, registered)`, `TERMINAL_STATUSES = (removed,)` and `BINDABLE_STATUSES = (connecting, active)`.
  - This story applies only `CREATION`. Later stories add the commands for the other moves, and they add no status.
- **Registration rules** (same module):
  - `check_address(text) -> InboxAddress(address, domain)`:
    1. the address is `identity.normalize_email(text)`, and it must be all ASCII;
    2. `domain` is `identity.registrable_domain(<the part after @>)`;
    3. a `domain` equal to `BRAND_DOMAIN` (`"nextronai.com"`) is refused, so subdomains are caught too;
    4. a local part whose text before the first `+` is in `ROLE_LOCAL_PARTS` is refused.

    The brand check comes before the role check.
  - `check_label(text) -> str`: the label stripped; 1 to 80 characters; no `Cc`, `Cf`, `Cs`, `Zl` or `Zp` character.
  - `allowlist_refusal(address, env, allowlist)`: uses `domain.sink.sink_applies` and `allowlisted`.
  - `crowded_domains(domains)`: each domain occurring more than `MAX_INBOXES_PER_DOMAIN` (3) times, sorted.
  - Every refusal raises `InboxRefused` (a new `DomainError`, code `inbox_refused`) with `.refusal` (a `Refusal` StrEnum), and its message is exactly `REFUSAL_MESSAGES[refusal]`:

    | Refusal | Message |
    |---|---|
    | `invalid_address` | Enter a valid email address, such as furqaan@yourdomain.com |
    | `invalid_label` | Enter a label of 1 to 80 characters on one line |
    | `brand_domain` | Cold email never goes from nextronai.com |
    | `role_address` | Use a personal address in the Sender's name |
    | `not_allowlisted` | Outside prod, only allowlisted test inboxes can be registered |
    | `already_registered` | Inbox already registered |
    | `sender_name_unset` | Set the Sender's name on the Settings page first |

  - No message or log line ever holds the address or the label. A non-`str` argument raises `TypeError`.
- **`Sending(clock, *, env)` and `@use_case register_inbox(address, label) -> RegisteredInbox(inbox_id, sending_domain_id, domain, domain_created)`:**
  1. `check_address` and `check_label` run before any transaction.
  2. Then one transaction does, in order:
     - outside prod, the `sink_allowlist` check (an allowlist that can't be read counts as empty);
     - the duplicate-address check, which covers every status;
     - `from_name` = `queries.setup.get(Key.SENDER_NAME)` (`SettingNotSet` or `InvalidSettingValue` means `sender_name_unset`);
     - get or insert the `sending_domains` row;
     - insert the inbox at `connecting`;
     - append one `events` row (entity `sending_inboxes`, `from_status` `None`, `to_status` `connecting`, cause `registered`, `cause_ref` `None`, actor `operator`).
  3. Any refusal rolls the whole transaction back.
  4. After it, log `sending_inbox_registered` (IDs and `domain_created` only) or `sending_inbox_refused` (`refusal` only).
- **Schema** (migration `0014_4_7_sending_inboxes`, owner `sending`):
  - `sending_domains`: `id`, `domain` String(253), `created_at`. Unique index `uq_sending_domains_domain`; `ck_sending_domains_not_brand` (`domain <> 'nextronai.com'`).
  - `sending_inboxes`:
    - columns: `id`; `address` String(254); `label` String(80); `sending_domain_id` (FK `fk_sending_inboxes_sending_domain_id_sending_domains`, no cascade); `from_name` String(100); `status` String(16), server default `'connecting'`; `created_at`;
    - unique index `uq_sending_inboxes_address`;
    - `ck_sending_inboxes_status`: the five statuses;
    - `ck_sending_inboxes_not_brand`: `address NOT LIKE '%@nextronai.com' AND address NOT LIKE '%.nextronai.com'`.
  - Every column is NOT NULL, mapped in `owners.py` and classified `none` in `pii_registry.py`.
- **Reads** (`nextron/app/queries/sending.py`). Both return `SendingInboxView(id, address, label, sending_domain_id, domain, from_name, status, created_at)` ordered by `created_at`, then `id`:
  - `inboxes()`: every inbox that isn't `removed`;
  - `inboxes_for_binding()`: only `BINDABLE_STATUSES`.
- **Panel:**
  - `GET /inboxes` (`inboxes_page`) and `POST /inboxes` (`inbox_register`), both `@personal_data(True)`.
  - The form carries exactly the fields `address` and `label`, with `method="post"`, `hx-post` and `{{ csrf_field() }}`. Any other field set raises `MalformedForm`.
  - The panel shows:
    - after a refusal, its message (`role="alert"`), with the typed values kept in the form;
    - after a success, `Inbox registered.` (`role="status"`), with the form cleared;
    - one warning per crowded domain: "More than 3 inboxes on <domain> raises deliverability risk";
    - a table with Label, Address, Sending Domain and Status;
    - for a `connecting` inbox, the note "Gmail connection and warm-up not set up yet; nothing can be sent from it";
    - with no inboxes, "No Sending Inboxes registered yet."
  - The Settings page links to the panel.
- **Setting** `sender_name`: section Sender, label "Sender's name", type `string`, default `REQUIRED`, `max_length(100)`, and a `display_name` rule (at least one letter; only letters, combining marks, spaces, `'`, `’`, `-` and `.`).

**Never:**
- A Gmail call, an OAuth grant, warm-up, ramps, caps, authentication checks, pause, resume or remove commands, `pause_cause` or any other Epic 6 column, `InboxRemoved` publishing, or enrollment binding (4.8).
- A second email or domain normaliser.
- `|safe` on inbox text.
- Writes to another owner's tables.

### Decisions made overnight (pending user review)
1. **`sender_name` is declared here as a required setting,** because no earlier story declared one and FR-32 needs it for the From name. While it's unset, `nextron doctor`'s `required_settings` fails and registration is refused, so no inbox ever carries a made-up From name. The allowed characters keep it a truthful personal name (CAN-SPAM, NFR-1).
2. **`from_name` is copied when the inbox is registered.** Story 6.2 sets it again from the setting when the inbox connects.
3. **The role list** is the 8 local parts in the AC plus `abuse`, `billing`, `careers`, `enquiries`, `help`, `hostmaster`, `inquiries`, `jobs`, `marketing`, `no-reply`, `noreply`, `postmaster`, `press` and `webmaster`. The check ignores a `+tag`, so `info+x@` is refused. Story 6.2 says "at least team, info, sales", so a longer list fits it.
4. **Inbox addresses must be ASCII** (a Gmail mailbox's local part is ASCII). An IDN domain is accepted in its punycode form.
5. **The nextronai.com rule is also a CHECK on both tables,** as defence in depth.
6. **Every new column is PII class `none`.** These columns are Nextron's own sender identity, not a data subject's, and Contact erasure must never rewrite the send identity. (Settings values are `redact`, but those rows are free text.)
7. **`removed` inboxes** are left out of the panel, the crowding count and `inboxes()`. Their address still counts as already registered.
8. **Unreadable data fails safe:** an unreadable allowlist counts as empty, and an unset or invalid Sender's name refuses registration.
9. **Deferred clause.** "When enrollment asks" is proven by calling `inboxes_for_binding()` directly; 4.8 wires `pick_inbox` to it. UX-DR26's connect button, pause cause, warm-up form, authentication results and Daily Cap use belong to Epic 6 (6.2–6.4 and the cap stories).
10. **The panel's place:** it lives at `/inboxes`, linked from the Settings page. It isn't added to the navigation, whose sections UX-DR1 fixes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Register | `Furqaan@Nextron-AI.com`, label `Main`, `sender_name` set, prod | Row `furqaan@nextron-ai.com` at `connecting`, `from_name` = setting; domain `nextron-ai.com` created; 1 `events` row | N/A |
| Same domain again | `mac@nextron-ai.com` | Reuses the domain row (`domain_created=False`) | N/A |
| Fourth inbox on a domain | 3 already on `nextron-ai.com` | Stored; the panel shows the crowding warning | N/A |
| Brand | `x@nextronai.com`, `x@mail.NextronAI.com`, `team@nextronai.com` | `brand_domain` | Nothing stored |
| Role | `info@d.com`, `Sales@d.com`, `info+cold@d.com` | `role_address` | Nothing stored |
| Duplicate | `FURQAAN@nextron-ai.com` after the first | `already_registered` | Nothing stored |
| Not allowlisted | env `dev`/`test`, address not on `sink_allowlist` (or the setting unreadable) | `not_allowlisted` | Nothing stored |
| Allowlisted | env `test`, address on the list (any ASCII case) | Registered | N/A |
| Bad address or label | `not-an-email`, `a@co.uk`, `ü@d.com`, blank or 81-character label, label with a newline | `invalid_address` / `invalid_label` | Nothing stored |
| Sender's name unset | No `sender_name` row | `sender_name_unset` | Nothing stored |
| Binding candidates | Inboxes in each of the five statuses | Only `connecting` and `active`, oldest first | N/A |
| Bad form | Extra or repeated field | 400 `MalformedForm` | Nothing stored |

</frozen-after-approval>

## Code Map

- **The machine pattern.**
  - `nextron/domain/pipeline_stage.py`: `MACHINE`, StrEnums, `CREATION`, `_rows`, `TRANSITIONS`, `transition`.
  - `nextron/domain/machines.py`: `Transition`, `IllegalTransition`.
  - Its test, `tests/unit/test_pipeline_stage.py`, checks every (status, event) pair.
- **Reuse; don't copy:**
  - `nextron/domain/identity.py`: `normalize_email`, `registrable_domain`, `IdentityError`;
  - `nextron/domain/sink.py`: `sink_applies`, `allowlisted`;
  - `nextron/domain/errors.py`: `DomainError`, which needs a unique `code` (`tests/contracts/test_error_contracts.py`).
- **Settings.**
  - `nextron/domain/settings_schema.py`: the `Key` enum (137), `Rule` (219), `max_length` (238), `SCHEMA` (840). Put `sender_name` first in the Sender section, before `sender_timezone`.
  - Tests that list the required settings need `sender_name` added in front:
    - `tests/app/test_setup_settings.py:341,347`;
    - `tests/unit/test_doctor.py:81,951`;
    - `tests/unit/test_settings_page.py`, `tests/unit/test_settings_schema.py` and `tests/contracts/test_settings_contracts.py`, wherever they enumerate settings.
- **Service pattern.**
  - `nextron/app/discovery/api.py` is the class-with-clock, `@command` and docstring pattern.
  - `nextron/app/notify/api.py` (235 `@use_case send`, 374 `@use_case dismiss`) is the `@use_case` pattern.
  - `nextron/db/uow.py` (`use_case`, `current().transaction()`).
  - `nextron/db/eventlog.py` `append` (actor `operator`).
  - `nextron/app/queries/setup.py` `get` (raises `SettingNotSet`).
  - Rewrite the stub docstrings in `app/sending/api.py`, `app/queries/sending.py` and `db/repos/sending/__init__.py`. The `sending` import rules already exist (c6 and c7 in `pyproject.toml`); don't edit the contracts.
- **Persistence patterns.**
  - `nextron/db/models/leads.py`: a CHECK built from the enum; `Index(..., unique=True)`; `ulid_pk`, `Ulid`, `UtcDateTime` from `db/models/base.py`.
  - `db/models/__init__.py` must import the new module.
  - `db/alembic/versions/0011_2_2_leads.py`: the header, the hard-coded SQL and the raising `downgrade`.
  - `db/owners.py` `TABLES`; `db/pii_registry.py` `PII`.
  - `db/repos/discovery/leads.py`: the repository style.
- **Migration bookkeeping.**
  - Set `HEAD` to `0014_4_7_sending_inboxes` in `tests/contracts/test_persistence_contracts.py:72`, `tests/unit/test_migrations.py:39` and `tests/app/test_serve_process.py:265`.
  - Bump the chain probes in `test_persistence_contracts.py` (around 668-690: `0014_*` → `0015_*`, `0015_*` → `0016_*`, revision 14 → 15).
  - Add the new modules, migration, repositories and templates to `tests/contracts/test_source_tree.py` `FILES` (around 61-142).
- **Web.**
  - `nextron/web/app.py`:
    - add `sending: Sending` to `create_app` (277), next to its docstring;
    - add the two routes in the pattern of `health_restore_test_now` (608): the POST writes in `units.open()`, then renders in `units.open(read_only=True)`;
    - update the module docstring's route list.
  - `nextron/web/health_page.py`: `submitted_job` (223) is the form-parsing pattern, using `MalformedForm`, `WRONG_FIELDS` and `REPEATED_FIELD`.
  - `nextron/web/credentials_page.py` `Notice(role, text)` (78).
  - Templates: `health.html` and `health_panel.html` (page plus partial); `google_client_row.html` (form markup); `settings.html` (add the link).
  - `tests/contracts/test_web_contracts.py` (around 159, 277, 458 and 571): add the routes and templates.
- **Wiring.** `nextron/main.py` `create_app(...)` (601): `sending=Sending(clock, env=config.env)`, and its docstring. `tests/dashboard.py:268` builds `Sending(self.clock, env=self.env)` and documents it. The default env is `dev`, so panel tests must set `sink_allowlist` through `setup.save`.
- **Test helpers.**
  - `tests/conftest.py` `db_engine`;
  - `tests/fakes/clock.py` `FrozenClock`;
  - `tests/app/test_leads.py`: units built as `UnitOfWorkFactory(db_engine, main.build_subscribers(...))`;
  - `tests/unit/test_health_page.py`: the panel tests with `Dashboard(units=units)`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/sending_inbox.py` -- the machine, registration rules, `Refusal`, `REFUSAL_MESSAGES`, `InboxRefused`, `InboxAddress`, `crowding_warning(domain)`, `CONNECTING_NOTE`, with a module docstring holding the transition table -- the one SendingInbox module.
- [x] `nextron-engine/nextron/domain/settings_schema.py` -- `Key.SENDER_NAME`, the `display_name` rule, and the declaration -- decision 1.
- [x] `nextron-engine/nextron/db/models/sending.py`, `db/models/__init__.py`, `db/alembic/versions/0014_4_7_sending_inboxes.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/sending/{__init__,domains,inboxes}.py` -- the tables and their repositories.
- [x] `nextron-engine/nextron/app/sending/api.py`, `app/queries/sending.py` -- `Sending`, `RegisteredInbox`, `register_inbox`; `SendingInboxView`, `inboxes`, `inboxes_for_binding`.
- [x] `nextron-engine/nextron/web/inboxes_page.py`, `web/templates/inboxes.html`, `web/templates/inboxes_panel.html`, `web/app.py`, `web/templates/settings.html`, `nextron/main.py`, `tests/dashboard.py` -- the panel and the wiring.
- [x] `nextron-engine/tests/unit/test_sending_inbox.py` (stdlib only):
  - every (status, event) pair is its table row or raises;
  - the exact AD-4 status and pause-cause sets, `PAUSE_EVENTS`, `CREATION`, `BINDABLE_STATUSES` and `TERMINAL_STATUSES`;
  - every refusal row of the I/O Matrix at domain level, and that no message holds the input;
  - the role list with a `+tag`;
  - `allowlist_refusal` in prod, dev and test;
  - `crowded_domains`;
  - the `display_name` rule.
- [x] `nextron-engine/tests/app/test_sending_inboxes.py` -- every I/O Matrix row through `register_inbox` on a migrated temporary DB with a `FrozenClock`, asserting rows and `events`, and that nothing is stored after each refusal. Also:
  - the env `prod` vs `test` paths;
  - an invalid stored allowlist;
  - the binding query over rows raw-inserted in each status;
  - the CHECKs, unique indexes and FK, each raising `IntegrityError` on a raw write (the brand CHECK included).
- [x] `nextron-engine/tests/unit/test_inboxes_page.py` -- through `Dashboard(units=units)`:
  - GET lists the inboxes with the connecting note;
  - POST success, and each refusal message with the values kept;
  - the crowding warning;
  - the empty state;
  - malformed and repeated fields (400) and a POST without CSRF (403);
  - `Cache-Control: no-store`;
  - a label with `<script>` is escaped;
  - the Settings page link.
- [x] Migration, settings and contract test updates from the Code Map; `nextron-engine/README.md` -- a "Sending Inboxes" section (rules, the refusals, the panel, the reads, the machine table), the migrations-table row and the `sender_name` setting.

**Acceptance Criteria:**
- Given the full suite, when it runs through the suite lock and `uv run lint-imports` runs, then everything passes and every contract is kept.
- Given the persistence contracts, when they run, then both tables are fully mapped (owner `sending`) and classified, and the migration matches the models.
- Given `grep` over `nextron/` for `normalize_email`, `registrable_domain` or `idna`, when it runs, then the new code only calls `domain.identity`.

## Implementation Notes

- **Machine and rules** (`nextron/domain/sending_inbox.py`). `TRANSITIONS` is built with `_rows` as in `pipeline_stage.py`; the pause rows come from `PAUSE_EVENTS` (cause = `InboxCause(<pause cause>)`), and `remove` covers every status not in `TERMINAL_STATUSES`. `transition` maps an unhashable argument to `IllegalTransition`, as `pipeline_stage.transition` does. `check_address` calls only `identity.normalize_email` and `identity.registrable_domain`, and after the ASCII check also requires the local part to be an RFC 5322 dot-atom (private `_DOT_ATOM` regex: atext runs separated by single dots), so quoted or commented local parts (`"info"@`, `info(x)@`), commas and `..` are `invalid_address` and can't bypass the role check; an `IdentityError` becomes `invalid_address` raised `from None`, so no exception chain carries the input. `allowlist_refusal` returns `Refusal | None` (the use case raises), mirroring `google_oauth.account_problem`. `crowding_warning(domain)` and `CONNECTING_NOTE` live here too. The label check uses its own `REFUSED_LABEL_CATEGORIES` (`Cc`, `Cf`, `Cs`, `Zl`, `Zp`).
- **Setting.** `Key.SENDER_NAME` is first in `Key` and in `SCHEMA` (Sender section, before `sender_timezone`), with `max_length(100)` and `DISPLAY_NAME_RULE` (`is_display_name`): letters (`L*`), combining marks (`M*`), the ASCII space, `'`, U+2019, `-` and `.`, at least one letter. "Spaces" was read as the ASCII space only, so a no-break space is refused.
- **Schema.** `nextron/db/models/sending.py` builds the status CHECK from `InboxStatus` and the brand CHECKs from `BRAND_DOMAIN`; the migration hard-codes the same SQL. Both unique keys are `Index(..., unique=True)` / `op.create_index`, as for `leads`. `label` is `String(MAX_LABEL_LENGTH)` (80), `from_name` `String(100)`.
- **Repositories.** `repos/sending/domains.py` (`by_domain`, `insert`) and `repos/sending/inboxes.py` (`id_by_address`, `insert`, `listed(statuses)`, which joins `sending_domains` for `domain` and orders by `created_at`, then `id`).
- **Use case.** `Sending.register_inbox` runs `check_address` and `check_label` before the transaction, then `_register` in one transaction in the spec's order. The allowlist is read only when `sink_applies(env)`; only `InvalidSettingValue` counts as unreadable (a database error propagates and rolls back). The inbox is inserted with `status=CREATION.to`. Each `InboxRefused`, before or inside the transaction, logs `sending_inbox_refused` (`refusal`) and re-raises; success logs `sending_inbox_registered` (`inbox_id`, `sending_domain_id`, `domain_created`). A `TypeError` isn't logged.
- **Reads.** `queries.sending.inboxes()` reads `LISTED_STATUSES` (every status not in `TERMINAL_STATUSES`); `inboxes_for_binding()` reads `BINDABLE_STATUSES`. Both return tuples of `SendingInboxView` with `status` as `InboxStatus`.
- **Panel.** `nextron/web/inboxes_page.py` (`submitted_inbox`, `panel_context`, `inbox_row`, `registered`, `refused`), reusing `credentials_page.Notice` and `Role`. `inboxes.html` extends `base.html` and includes `inboxes_panel.html` (`<div id="inboxes-panel">`; the form posts with `hx-target="#inboxes-panel" hx-swap="outerHTML" hx-sync="this:drop"`, so a double click sends one POST). The Status cell shows the raw status value, with the connecting note in a `<p class="inbox-note">`; warnings are `<p class="inbox-warning">`. Small `app.css` rules were added for the warning, table and note. The inputs have no `maxlength`, so the server's refusal is what the Operator sees. Settings links to `/inboxes`; the navigation is unchanged.
- **Wiring.** `create_app(..., sending=...)`; `main.build_app` passes `Sending(clock, env=config.env)`; `tests/dashboard.py` builds `Sending(self.clock, env=self.env)`.
- **Allowlist wording.** The `sink_allowlist` unit text and the README's sink section say that, outside prod, only addresses on it can be registered as Sending Inboxes (the two tests that assert the unit text are updated). The README says each inbox keeps the From name it was registered with.
- **Wiring test.** `tests/unit/test_inboxes_page.py` checks that `main.build_app` builds `Sending` with its clock and `env=config.env`.
- **Tests updated for the new required setting:** `test_setup_settings.py`, `test_doctor.py`, `test_settings_page.py`, `test_settings_schema.py`, `test_settings_contracts.py`. Migration bookkeeping: `HEAD` in the three files, chain probes bumped to `0015`/`0016`/revision 15, `test_source_tree.FILES`, and the routes and templates in `test_web_contracts.py`.

**Review fixes (pass 1)** (the Review Triage Log's patch rows, already folded into the notes above):
- **P1, dot-atom local part.** `check_address` refuses any local part that isn't a dot-atom, with `invalid_address`.
  - Refused: `a,b@`, `"mac"@`, `mac<x>@`, `a..b@`, `.mac@`, `mac.@`, `mac;x@` and `mac[x]@`.
  - It also closes the role-check bypass through `"info"@` and `info(x)@`.
  - Covered in `tests/unit/test_sending_inbox.py` and in `tests/app/test_sending_inboxes.py`, which also checks that nothing is stored.
- **P2, wiring test.** `test_build_app_builds_sending_on_its_clock_and_environment` proves `build_app` passes its own clock and `env=config.env`.
- **P3, the From name.** A README sentence: each inbox keeps the From name it was registered with until 6.2 refreshes it on connect.
- **P4, allowlist wording.** The `sink_allowlist` unit text and the README's sink section say the allowlist now also limits which inboxes can be registered. `tests/unit/test_settings_page.py` and `tests/unit/test_settings_schema.py` (`SINK_UNIT`) are updated to match.
- **P5, new tests.**
  - `test_a_refusal_inside_the_transaction_is_logged_with_its_code_only` covers `already_registered`, `not_allowlisted` and `sender_name_unset`.
  - `test_a_removed_inbox_is_left_out_of_the_crowding_count` covers three live inboxes plus one `removed`, and expects no warning.
- **P6, `hx-sync="this:drop"` on the form.** It's asserted through the page test's `PANEL_FORM` attributes.

**Verification after the fixes.**
- The targeted run, over the three new files, `tests/contracts`, and the settings, doctor, migrations and credentials-page tests: 1,729 passed, 1 skipped.
- `uv run lint-imports`: 29 kept.
- **The first full-suite run had one failure:** 7,646 passed, 1 failed and 2 skipped. `tests/app/test_telegram_secrecy.py` posts every field of the Settings form, so it was refused as a malformed form once `sender_name` existed.
  - The fix adds `sender_name=""` to that form.
  - The test file then passed (3 tests), and the suite was run again (result below).
- **The final full suite through the suite lock (track-E):** 7,647 passed and 2 skipped (`ai_eval` and the symlink test) in 204 s. `lint-imports`: 29 kept, 0 broken. The known job-runner flake didn't occur.

## Spec Change Log

## Review Triage Log

**Pass 1** (blind hunter BH, edge-case hunter EC, verification gap VG). 20 raw findings, 14 entries after dedupe: 6 patched, 1 deferred, 7 rejected.

| # | Finding (layers) | Verdict and evidence | Route |
|---|---|---|---|
| 1 | `check_address` accepts local parts that aren't a dot-atom: `a,b@`, `"mac"@`, `mac<x>@`, `a..b@`, `.mac@` (BH1, EC1). Through the same gap, the role check is bypassed by `"info"@d.com` and `info(x)@d.com` (EC5). | **medium.** `identity.normalize_email` keeps the local part as typed, apart from whitespace and `Cc`/`Cf`, and the only extra check is `isascii()`. In prod there is no allowlist, so these are stored, and they put header-structural characters into the future `From`. The I/O Matrix's `invalid_address` is "Enter a valid email address". | patch P1 |
| 2 | `mac+a@d.com` and `mac+b@d.com` register as two inboxes of one mailbox (BH2, EC2). | **low.** Real: the duplicate check is an exact match. But Mac registers his own inboxes by hand. Story 6.2's identity check also refuses a connect when `getProfile`'s address differs from the requested one, so a plus-alias can never become `active`. Any fix adds a refusal rule the spec doesn't have. | reject (would change the approved spec) |
| 3 | Free-mail domains such as `gmail.com` are accepted as Sending Domains (BH3). | **medium**, a policy gap. FR-32 and the AC don't refuse them, and dev test inboxes may well be Gmail addresses. A refusal needs a new rule and message, which only Mac can decide. Logged in `deferred-work.md` for Story 6.2 / FR-64. | reject (would change the approved spec) |
| 4 | `ck_sending_domains_not_brand` (`domain <> 'nextronai.com'`) is case-sensitive, and the unique indexes are case-sensitive (VG other, BH4, EC3). | **low.** True for raw SQL writes only. The app path always lowercases the address and stores the registrable domain, and the frozen spec gives this exact CHECK. | reject (would change the approved spec) |
| 5 | Labels aren't unique, but Hot Lead alerts will name an inbox by its label (BH5). | **low.** Ambiguity would need two inboxes with the same label. The fix is a new refusal that the spec doesn't have; the alerts story can show the label with the domain. | reject (would change the approved spec) |
| 6 | The copied `from_name` goes stale silently and doesn't show on the panel (BH6). | **low.** Real (decision 2). The panel columns are fixed by the spec, so only the docs are fixed: the README says each inbox keeps the From name it was registered with until 6.2 refreshes it on connect. | patch P3 (docs only) |
| 7 | `sink_allowlist`'s unit text and the README's dev and test sink section don't say the allowlist now also gates inbox registration (BH7). | **low.** Real. It's a direct text correction. | patch P4 |
| 8 | The role list misses `do-not-reply`, `no_reply`, `mailer-daemon`, `newsletter` and others, plus compound forms such as `sales-uk@` (BH8). | **low.** Real, but decision 3 fixes the list in the frozen spec, and Mac registers personal addresses by hand. | reject (would change the approved spec) |
| 9 | The `display_name` rule accepts company names, a lone `M`, doubled spaces, stacked combining marks, mixed scripts and U+3164 HANGUL FILLER (BH9, EC4). | **low.** Real, but the rule's exact character classes are in the frozen spec, and the setting is typed by Mac himself. | reject (would change the approved spec) |
| 10 | A double click on "Add inbox" sends a second POST, whose "Inbox already registered" replaces the success notice; focus is lost after the swap (BH10). | **low.** Real: htmx 2's default queue strategy for an element with a request in flight is `last`, so the second submit runs after the first. The fix is a single `hx-sync="this:drop"` attribute. The focus part is rejected: every panel in the app swaps the same way. | patch P6 |
| 11 | A `removed` inbox's address can never be registered again, and before Epic 6 a mis-registered inbox can't be corrected or removed (BH11). | **low.** Real, but the intent excludes remove commands ("this story only creates rows at connecting"), and decision 7 is deliberate. Story 6.3 owns removal. | defer (`deferred-work.md`) |
| 12 | Untested: the refusal log for refusals raised inside the transaction, and a removed inbox left out of the crowding count (BH12). | **low.** Real gaps. The fix is adding tests. | patch P5 |
| 13 | `sending_inboxes.sending_domain_id` has no index (BH13). | **low.** The tables hold a handful of rows, so a scan costs nothing. An index is schema beyond the spec. | reject (negligible) |
| 14 | No test proves `main.build_app` builds `Sending` with `env=config.env`, so a wrong value would silently turn off the AD-14 allowlist on the real panel (VG gap). | **medium**, pre-verified. Every route test builds its own `Sending` through the harness. | patch P2 |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_sending_inbox.py tests/app/test_sending_inboxes.py tests/unit/test_inboxes_page.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
- The full suite through `suite_lock.py` (orchestrator only) -- expected: all pass.
