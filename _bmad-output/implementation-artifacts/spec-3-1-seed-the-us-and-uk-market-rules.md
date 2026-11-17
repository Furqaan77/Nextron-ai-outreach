---
title: 'Story 3.1: Seed the US and UK Market rules'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: 'cb63e0221eec2fd07f24529840430f77bb78d82b'
review_loop_iteration: 0
story_key: '3-1-seed-the-us-and-uk-market-rules'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has no Markets. Market resolution (3.2), Eligibility (3.6), the footer (4.11), drafting (4.15) and the send gate (6.9) all need the US and UK cold-email rules: legal forms, free webmail, timezones, English variant and the footer template, as FR-2 defines them. AD-12 says they come from `seed/markets.yaml` through `nextron seed sync`, which Story 2.1 built for Segments only.

Requirements: FR-2, AD-3, AD-12, NFR-2, NFR-5, NFR-7, NFR-40.

**Approach:**
- `nextron-engine/seed/markets.yaml` holds the US and UK Markets.
- The existing strict reader (`nextron/domain/seed_yaml.py`) parses the file. A new stdlib-only `nextron/domain/markets.py` validates it into `MarketDef`s and holds the Market functions: `market_for_country`, `is_free_webmail`, `cctld_country` and the footer-template renderer.
- **`nextron seed sync` is extended, not duplicated.** It now loads `segments.yaml`, then `markets.yaml`, and syncs both in **one transaction**. It upserts Markets by `code` into a new `markets` table (owner `setup`, migration `0009_3_1_markets`), with a `version` and one `events` row (actor `seed`) per created or changed Market.
- A new setting, `us_solicitation_label`, is blank by default (NFR-2, OQ-8). It is declared here, once.
- A contract test finds no POST route that touches a Market rule (NFR-7).

The acceptance criteria are Story 3.1 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **`nextron/domain/markets.py`** (stdlib and `nextron.domain` only; frozen, slotted dataclasses).
  - **Types:**
    - `EnglishVariant` (`StrEnum`): `en_us`, `en_gb`.
    - `FooterPlaceholder` (`StrEnum`): `sender_signature`, `legal_name`, `postal_address`, `optout_line`, `privacy_line`, `us_solicitation_label`.
    - `SendWindow(start: time, end: time)`.
    - `MarketDef`: `code`, `name`, `enabled`, `country_code`, `cctlds`, `cold_email_allowed`, `eligible_legal_forms` (`tuple[str, ...] | None`, where `None` means not checked), `free_webmail_blocked`, `default_timezone`, `unknown_timezone`, `unknown_send_window` (`SendWindow | None`), `english_variant`, `footer_template` (`tuple[str, ...]`, one item per line) and `privacy_line_required`.
  - **The file shape.** The top level is exactly `markets: [<market>, ...]`, with 1 to 50 items. Each item has exactly the `MarketDef` field names as keys. `unknown_send_window` is optional; every other key is required, and unknown keys are refused.
  - **`validate_markets(data) -> tuple[MarketDef, ...]`**, in file order. The first problem raises `InvalidMarkets(field, reason)`:
    - a `DomainError` with code `invalid_markets` and the message `"<field>: <reason>"`;
    - `field` is `markets[<code or index>].<key>[...]`, with the same path conventions as `InvalidSegments`;
    - the reason never repeats a value longer than 40 characters.
  - **Value rules:**
    - **Identifiers:**
      - `code` fullmatches `[a-z][a-z0-9_]{0,15}` and is unique in the file.
      - `country_code` fullmatches `[A-Z]{2}` and is unique in the file.
      - `cctlds` holds 1 to 10 items, each `[a-z]{2}`, with no duplicates and none shared with another Market. For each item `t`, `cctld_country("x." + t)` equals the Market's `country_code`.
    - **Texts.** `name` is a text (2.1's text rules) of at most 100 characters.
    - **Booleans.** The four flags and `enabled` are `bool`.
    - **Legal forms.** `eligible_legal_forms` is either the plain string `not_checked`, or a list of 1 to 50 items, each `[a-z][a-z0-9-]{0,63}`, with no duplicates.
    - **Timezones.** Both timezone fields are members of `settings_schema.timezone_names()`.
    - **Send Window.** `unknown_send_window` is `'HH:MM-HH:MM'`, with times from 00:00 to 23:59 and start before end.
    - **English variant.** `english_variant` is a member of `EnglishVariant`.
  - **Footer template rules.** `footer_template` holds 1 to 20 lines, and each line is a text of at most 200 characters.
    - **Placeholders** are `{<name>}`, with the name a member of `FooterPlaceholder`. Any other `{` or `}` is refused, and each placeholder appears at most once.
    - **Required in every template:** `sender_signature`, `legal_name`, `postal_address` and `optout_line` (FR-23).
    - **`privacy_line`** appears if and only if `privacy_line_required` is true.
    - **`us_solicitation_label`** appears if and only if `country_code` is `US` (NFR-2).
    - **Optional lines.** `privacy_line` and `us_solicitation_label` each stand alone on their line.
  - **Compliance floor** (decision 3). Each of these fails naming its field:
    - `cold_email_allowed` is true only when `country_code` is `US` or `GB` (NFR-5);
    - a `GB` Market has `free_webmail_blocked` true, `privacy_line_required` true, and `eligible_legal_forms` as a list.
  - **`market_form(market) -> dict`** gives the file form back (`not_checked` for `None`; `unknown_send_window` only when set). **`market_from_form(form) -> MarketDef`** validates one item, so `market_from_form(market_form(m)) == m`.
  - **`market_for_country(code: str | None, markets: Iterable[MarketDef]) -> MarketDef | None`.** It strips and uppercases `code`. It returns the Market with that `country_code` only when that Market is `enabled`. Anything else returns `None`: a code that isn't `[A-Z]{2}`, `None` or an unknown code.
  - **`cctld_country(domain: str) -> str | None`.**
    1. Lowercase and strip the input. If it holds an `@`, keep only the part after the last `@`. Drop one trailing `.`.
    2. With no `.` left, return `None`.
    3. Otherwise read the last label. If it fullmatches `[a-z]{2}`, return it uppercased, except that `uk` gives `GB`. Any other label is generic and gives `None`.
    - Examples: `firm.co.uk` → `GB`, `firm.com` → `None`, `firm.de` → `DE`, `firm.io` → `IO`.
  - **`is_free_webmail(domain: str) -> bool`.** It normalises the input the same way, then returns true when the domain, or any parent at a label boundary, is in the vendored `FREE_WEBMAIL_DOMAINS` (a frozenset). The list includes at least the AC's twelve domains (Design Notes).
  - **`render_footer_template(template: Sequence[str], values: Mapping[str, str]) -> str`.**
    - Every placeholder used in the template needs a `str` in `values`; a missing one raises `ValueError` naming the placeholder, never a value.
    - Each placeholder is replaced once. Values are never re-scanned.
    - After the replacement, a blank line is dropped, so an optional line with a blank value disappears.
    - The lines are joined with `\n`.
- **The setting.** `Key.US_SOLICITATION_LABEL = "us_solicitation_label"` is appended after `AI_BUDGET_ALERT_PCT`. Its declaration:
  - type `STRING`, in `Section.FOOTER_AND_BOOKING`;
  - label "US solicitation label";
  - default `""`, with `max_length(200)`;
  - unit: "Blank by default. When set, every US email's footer shows this text on its own line (CAN-SPAM; decided with counsel before the first US send, OQ-8)."
- **Persistence.**
  - **Migration `0009_3_1_markets`** has down revision `0008_2_1_segments`, and its `downgrade()` raises like 0008. It creates `markets`:

    | Column | Type | Notes |
    |---|---|---|
    | `id` | ULID | `pk_markets` |
    | `code` | `String(16)` | `uq_markets_code` |
    | `name` | `String(100)` | |
    | `enabled`, `cold_email_allowed`, `free_webmail_blocked`, `privacy_line_required` | `Boolean` | |
    | `country_code` | `String(2)` | `uq_markets_country_code` |
    | `cctlds`, `footer_template` | `Text` | canonical JSON lists |
    | `eligible_legal_forms` | `Text` | **nullable**; JSON list, NULL means not checked |
    | `default_timezone`, `unknown_timezone` | `String(64)` | |
    | `unknown_send_window` | `String(11)` | **nullable** |
    | `english_variant` | `String(5)` | |
    | `version` | `Integer` | `ck_markets_version`: `version >= 1` |
    | `created_at`, `updated_at` | `UtcDateTime` | |

    Every other column is NOT NULL.
  - **The model** is `MarketRecord` in `db/models/setup.py`, matching the migration.
  - **`owners.py`:** `markets` → `setup`, with every column.
  - **`pii_registry.py`:** every column is `none`. Templates hold placeholders and rules, never a person.
  - **The repository** (`db/repos/setup/markets.py`) works like `segments.py`:
    - `all_markets()` returns every row, by code;
    - `by_code(code)` returns one row or `None`;
    - `insert(market, at) -> id` creates version 1;
    - `update(market_id, market, version, at)`.

    It returns `StoredMarket(id, code, version, created_at, updated_at, market: MarketDef)`, decoded through `market_from_form`. A row that no longer validates raises `InvalidMarkets`.
  - **Migration test updates.** Bump the existing `HEAD` constants and the chain probes (`0009_*` → `0010_*`, "revision 9" → "revision 10"). Add the new files to `test_source_tree` `FILES`.
- **Setup** (`app/setup/api.py`).
  - **`@use_case sync_markets(markets) -> MarketSync`** mirrors `sync_segments`, keyed by `code`.
    - **Before any transaction** it raises `TypeError` for an item that isn't a `MarketDef` and `ValueError` for a repeated code. When the input isn't empty, it re-validates the file form (`validate_markets`).
    - **In one transaction:**
      - It refuses an input `country_code` or ccTLD that a stored Market left out of the input still uses. This raises `InvalidMarkets` on `markets[<code>].country_code` or `markets[<code>].cctlds`, with the reason "…of the stored Market <code>, which is no longer in the file: …".
      - **No row:** insert, then one `events` row: entity `markets`, `None` → `v1`, cause `seed_sync`, actor `seed`.
      - **A changed `MarketDef`:** update to version n+1, then one `events` row from `v<n>` to `v<n+1>`.
      - **Otherwise** nothing is written.
    - It never changes or deletes a stored Market that is missing from the input.
    - It returns `MarketSync(created, changed, unchanged, not_in_file)`, shaped like `SegmentSync`.
    - After the commit it logs `markets_synced` with the four `*_count` fields.
  - **`@use_case sync_seed(segments, markets) -> SeedSync(segments, markets)`** runs every pre-transaction check of both, then both syncs in **one** transaction (Segments first), then logs both records.
    - Refactor `sync_segments` into a checks helper and an in-transaction helper. Its behaviour and its 2.1 tests stay as they are.
- **Queries** (`app/queries/setup.py`). `markets()` returns `tuple[SeededMarket, ...]`, by code. `market(code)` returns `SeededMarket | None`. `SeededMarket(id, code, version, definition: MarketDef)` is frozen.
- **The command** (`main.py`). `seed_sync` reads, parses and validates `segments.yaml`, then `markets.yaml` (`MARKETS_FILE_NAME`, `MARKETS_FILE_LABEL = "seed/markets.yaml"`), each with 2.1's read rules and messages under its own label. Both files are required, and any problem exits 1 before the database is touched.
  - Then it runs 2.1's database check, and one unit of work runs `Setup.sync_seed`. `InvalidSegments` prints under the segments label and `InvalidMarkets` under the markets label (exit 1). Any other failure is "seed sync failed (<Type>); nothing was changed".
  - `format_seed_sync(result: SeedSync)` prints 2.1's segments block, then the markets block:
    - `markets: <n> changed, <m> unchanged`;
    - then `  <code>: created (version 1)`, `  <code>: changed (version <a> -> <b>)` and `  <code>: not in seed/markets.yaml, left as it is (version <v>)`.
  - Update the docstrings of `main.py`, `__main__.py`, `app/setup/api.py` and `queries/setup.py`.
- **The 2.1 tests (`tests/app/test_seed_sync.py`).** Change only what the second file forces:
  - the `seed_dir()` helper also copies the shipped `markets.yaml`;
  - the exact-output assertions (`SUMMARY_FIRST` and the "0 changed" lines) include the markets block.
- **Tests run as `test`** with temporary data dirs. They read no keyring and make no network call. The domain tests import only the stdlib, pytest and `nextron.domain`.

**Never:**
- No YAML library, no second loader or command, and no reader change unless a test proves it's needed.
- No new dependency, adapter, port, route, page, job type, trigger or credential. The only new setting is `us_solicitation_label`.
- **No Market column on any Lead table.** Leads don't exist yet. The Market is derived, never stored (AC).
- **Leave the other Story 4.11 footer settings, `footer_snapshots` and `domain.render` to Story 4.11.** That covers `sender_signature`, `legal_name`, `postal_address`, `optout_line` and `privacy_notice_url`. The same goes for 3.2's Contact Market and timezone resolution and 3.6's Eligibility.
- No editing of anything outside `nextron-engine/` except this spec, `epic-3-context.md`, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The file shape.** The field names follow the AC:
   - `cctlds`;
   - `unknown_timezone` plus an optional `unknown_send_window` (the US's `'11:00-17:00'`);
   - `english_variant`;
   - `footer_template`, which is a **list of lines**, because the YAML subset has no multi-line scalars.
2. **"Not checked" is explicit.** The US has `eligible_legal_forms: not_checked`, stored as NULL. An empty list is refused, so an empty list can never be misread as "anything goes".
   - **The UK list** holds eight Companies House `company_type` values:
     - `ltd`, `plc`;
     - `private-limited-guarant-nsc`, `private-limited-guarant-nsc-limited-exemption`, `private-limited-shares-section-30-exemption`;
     - `private-unlimited`, `private-unlimited-nsc`;
     - `llp`.
   - **Excluded:** `old-public-company`, partnerships other than LLPs, overseas entities and every other type. A narrower list is the compliance-safe side.
3. **A compliance floor in code.** A seed edit can't loosen the rules:
   - cold email is allowed only in US and GB Markets (NFR-5);
   - a GB Market must block free webmail, require the privacy line and check legal forms (PECR).

   This matches 11.6's "Set in code (compliance)" for these fields.
4. **The footer template language.**
   - **Placeholders** are the 4.11 setting names plus `privacy_line`, which 4.11 computes from the Provenance source.
   - **Four are required.** `privacy_line` appears if and only if it is required, and `us_solicitation_label` if and only if the Market is US. The optional placeholders stand alone on their line.
   - **Blank lines are dropped.** So a blank `us_solicitation_label` omits its line, and a non-blank one renders as its own line with no code change (NFR-2).
   - **The setting.** `us_solicitation_label` is declared here, blank by default, in "Footer and booking". Story 4.11 reuses it and must not declare it again.
5. **Storage and references.** `markets` uses typed columns and a `version`, like Segments, so each change has an audit row.
   - `code` (`us`, `uk`) is the seed's natural key.
   - Other tables reference the ULID **`markets.id`** as `market_id`, as the spine's `market_id` snapshots and 4.11's `footer_snapshots.market_id` do.
6. **One transaction for the whole sync.** Both files are required and validated before the database is touched, and Segments and Markets commit together, so "nothing was changed" stays true on any failure.
7. **A stored Market missing from the file is left as it is,** as Story 2.1 does for Segments.
8. **`cctld_country`.** Any two-letter last label counts as a country code: uppercased, with `uk` as GB. So `.eu` and `.io` block too, the conservative side. Internationalised ccTLDs (`xn--…`) count as generic. That is a known limit for Story 3.2.
9. **Deferral.** The "Lead's Market is derived and never stored on the Lead" clause has no Lead on this track. This story ships `market_for_country` and a contract test that fails if a `leads` table ever gets a column naming `market`. That test is vacuous until Story 2.2. The call site comes with Stories 3.2 and 3.6.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First sync | DB at head; the shipped files | Segments as in 2.1. `markets` gets `us` and `uk` at version 1, plus 2 `events` rows (`markets`, `None` → `v1`, `seed_sync`, `seed`). The output is the segments block, then "markets: 2 changed, 0 unchanged" and 2 created lines. Exit 0. | N/A |
| Second sync | the same files | "markets: 0 changed, 2 unchanged". No row, `updated_at` or `events` changes. | N/A |
| Changed | the UK name or a template line is edited | `uk` goes to version 2, with one `events` row `v1` → `v2`; `us` is untouched | N/A |
| CA added | a disabled `CA` Market with every field | It loads (created, version 1). `market_for_country("CA", markets)` is `None`. Once enabled with `cold_email_allowed: false`, it returns CA. | N/A |
| Not in file | a stored `ca` is left out | listed "not in seed/markets.yaml, left as it is (version 1)"; the row is unchanged | N/A |
| Invalid | a duplicate country; an empty legal-form list; a CA with `cold_email_allowed: true`; a GB with webmail allowed; a template without `{optout_line}`; a US template without `{us_solicitation_label}`; `{privacy_line}` mid-line; an unknown placeholder; a bad timezone; a ccTLD of another country | Exit 1 with `seed/markets.yaml: <field>: <reason>`. The DB file's SHA-256 is unchanged, and segments are untouched too. | nothing written |
| File | `markets.yaml` missing, larger than 1 MiB, or not UTF-8 | exit 1, naming `seed/markets.yaml` | nothing written |
| Atomic | a failure while recording a Market, after the Segments were written | "seed sync failed (…); nothing was changed"; no `segments`, `markets` or `events` rows | rollback |
| Footer | the stored US template; the setting blank, then "Advertisement" | blank: 4 lines with no label line; set: 5 lines, the last being "Advertisement" | N/A |
| Domain | `firm.co.uk`, `firm.com`, `firm.de`, `Bob@Mail.GMAIL.com.`, `x.btinternet.com` | GB, `None`, DE; free webmail true, true | N/A |

</frozen-after-approval>

## Code Map

- **The CLI** (`nextron-engine/nextron/main.py`):
  - the docstring's "Seed sync" paragraph (around line 227);
  - the constants at 408-416 (`SEED_DIR`, `SEGMENTS_FILE_*`, `MAX_SEED_FILE_BYTES`);
  - `_read_seed_file`, `_seed_database_problem`, `format_seed_sync` and `seed_sync` (1255-1373).

  Reuse them; don't copy them. `nextron/__main__.py` needs only its docstring.
- **Setup** (`nextron-engine/nextron/app/setup/api.py`):
  - the docstring paragraph on `sync_segments` (around line 31);
  - the constants at 100-107 (`SEGMENTS_ENTITY`, `SEED_SYNC_CAUSE`, `SEED_ACTOR`);
  - `SegmentSync` (275);
  - `Setup.sync_segments` (357-403);
  - `_record_segment` (436), which is the events pattern.

  A unit of work allows **one** `@use_case` call (`db/uow.py:26`), which is why `sync_seed` exists.
- **Queries.** `nextron-engine/nextron/app/queries/setup.py` (`SeededSegment`, `segments()` and `segment()` are the pattern).
- **Persistence:**
  - `db/models/setup.py` (`SegmentRecord`) and `db/models/__init__.py`;
  - `db/alembic/versions/0008_2_1_segments.py` (the pattern) and `0006_1_13_metering.py` (`sa.Boolean()`);
  - `db/owners.py:62` and `db/pii_registry.py:46` (the `segments` entries);
  - `db/repos/setup/segments.py` and `db/repos/setup/__init__.py`.
- **Domain patterns:**
  - `nextron/domain/segments.py` (validation, field paths, text rules, `KEY_PATTERN` and `InvalidSegments`);
  - `nextron/domain/seed_yaml.py` (`parse`);
  - `nextron/domain/settings_schema.py` (`timezone_names()` at 416, `Key` at 133, `SCHEMA`, `max_length` at 234, `Section.FOOTER_AND_BOOKING`).
- **Tests that list the setting keys** (append `us_solicitation_label`):
  - `tests/unit/test_settings_schema.py` (505-534, around 968);
  - `tests/contracts/test_settings_contracts.py` (around 132);
  - `tests/unit/test_settings_page.py` (around 540: the page field order puts the Footer section first).
- **Migration tests:**
  - `tests/contracts/test_persistence_contracts.py:72` `HEAD`, and the probes at 670-687;
  - `tests/unit/test_migrations.py:38`;
  - `tests/contracts/test_source_tree.py` `FILES` (around 58, 84, 87 and 138).

  `test_serve_process.py` already uses `head_revision()`.
- **Test helpers:**
  - `tests/app/test_seed_sync.py` (the fixtures `config`, `database` and `units`; `seed_dir`, `sync`, `query`, `table_dump` and `event_rows`);
  - `tests/dashboard.py` `Dashboard` (the built app, used in `tests/contracts/test_web_contracts.py:241`);
  - `tests/migration_chains.py` (`sha256`);
  - `tests/unit/test_segment_seed.py` (the shipped-file test pattern).
- **Contracts that apply:**
  - `test_domain_stdlib_only.py`;
  - `test_error_contracts.py` (a unique `invalid_markets` code);
  - `test_log_event_names.py` (under 32 characters);
  - import-linter c7 (setup uses only its own repositories).

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/markets.py` -- the types, `validate_markets`, `InvalidMarkets`, `market_form`/`market_from_form`, `market_for_country`, `cctld_country`, `is_free_webmail` (`FREE_WEBMAIL_DOMAINS`) and `render_footer_template` -- the one Market module.
- [x] `nextron-engine/nextron/domain/settings_schema.py` -- `Key.US_SOLICITATION_LABEL` and its `Setting` -- NFR-2.
- [x] `nextron-engine/seed/markets.yaml` -- the Design Notes content, with a header comment.
- [x] `nextron-engine/nextron/db/models/setup.py`, `db/models/__init__.py`, `db/alembic/versions/0009_3_1_markets.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/setup/markets.py`, `db/repos/setup/__init__.py` -- the table, its mapping and its repository.
- [x] `nextron-engine/nextron/app/setup/api.py`, `app/queries/setup.py` -- `sync_markets`, `sync_seed`, `MarketSync`, `SeedSync`, `markets()`, `market(code)` and `SeededMarket`.
- [x] `nextron-engine/nextron/main.py`, `nextron/__main__.py` -- the second file, `sync_seed`, the markets summary block and the docstrings.
- [x] `nextron-engine/tests/unit/test_markets_domain.py` -- stdlib-only:
  - every value, footer and floor rule failing with its exact field path;
  - unknown and missing fields;
  - the round trip;
  - `market_for_country` (enabled, disabled CA, lowercase, blank, `None`, unknown);
  - `cctld_country` and `is_free_webmail` (the Domain row, the AC's twelve domains, a subdomain, `notgmail.com` false, no dot, empty);
  - `render_footer_template` (the Footer row, a missing value, a value holding `{legal_name}` that isn't re-scanned).
- [x] `nextron-engine/tests/unit/test_market_seed.py` -- the shipped file validates into exactly the Design Notes values, field for field.
- [x] `nextron-engine/tests/app/test_market_sync.py` -- every I/O Matrix row through `main.seed_sync(env="test", seed_dir=...)` on a temporary data dir. Also:
  - `queries.setup.markets()` and `market()`;
  - the Footer row, through `Setup.set` and `queries.setup.get` on real units;
  - the `markets_synced` record;
  - `sync_markets` refusing bad input before any transaction;
  - the shared country and ccTLD refusal;
  - `ck_markets_version`.
- [x] `nextron-engine/tests/app/test_seed_sync.py` -- only the helper and exact-output changes named above.
- [x] `nextron-engine/tests/contracts/test_market_contracts.py` -- NFR-7 and the never-stored rule, each with a probe proving the check catches a violation:
  - **(a)** No POST route of `Dashboard().app` has `market` (in any case) in its path, its route name or its endpoint's `__module__` or `__qualname__`. A probe POST `/markets/uk/disable` is named.
  - **(b)** No module under `nextron/web` names `sync_markets`, `sync_seed` or `nextron.db.repos.setup.markets` (AST). A probe file is caught.
  - **(c)** A `leads` table in `Base.metadata`, if one exists, has no column whose name contains `market`. A probe `MetaData` is caught.
- [x] The setting-key tests and migration and tree tests from the Code Map, and `nextron-engine/README.md`:
  - the "Seeds" section: `markets.yaml`, the fields, the floor, the placeholders and the summary;
  - the "Settings" section: `us_solicitation_label`.

**Acceptance Criteria:**
- Given the full suite, when it runs through the suite lock and `uv run lint-imports` runs, then everything passes and every contract is kept.
- Given every model column, when the persistence contracts run, then `markets` is fully mapped in `owners.py` (owner `setup`) and `pii_registry.py` (`none`), and the migration matches the model.
- Given the Settings schema, when Epic 1's settings contract runs, then no key disables Market rules, and `us_solicitation_label` is a blank-default string.

## Implementation Notes

**What was built**
- `nextron/domain/markets.py`: the types, `validate_markets`/`InvalidMarkets`, `market_form`/`market_from_form`, `market_for_country`, `cctld_country`, `is_free_webmail` (`FREE_WEBMAIL_DOMAINS` is the Design Notes list plus the review's additions below), `render_footer_template`, plus two small public helpers: `send_window_text(window)` (the `HH:MM-HH:MM` form) and `placeholders(template)` (the `{name}`s a template holds). Rules run fields first (file order), then missing fields, then the ccTLD-country rule, the compliance floor and the footer rules, in that order. It imports only `nextron.domain.errors` and `nextron.domain.settings_schema` (`timezone_names`, `TIMEZONE_REASON`).
- `seed/markets.yaml`: the Design Notes content with a header comment; flow lists kept (the reader needs no block lists).
- `markets` table: migration `0009_3_1_markets` (down `0008_2_1_segments`, `downgrade()` raises), `MarketRecord`, `owners.py` (`setup`, every column), `pii_registry.py` (every column `none`). Columns follow `MarketDef` field order, then `version`, `created_at`, `updated_at`.
- `db/repos/setup/markets.py`: stores each Market's columns from `market_form` (canonical JSON lists, `ensure_ascii=False`, no spaces; NULL for `not_checked` and an unset window) and decodes a row back through `market_from_form`; a list column that isn't JSON raises `InvalidMarkets` on `markets[<code>].<column>`.
- `app/setup/api.py`: `sync_segments` refactored into `_segment_inputs` (pre-transaction checks) and `_sync_segments` (in-transaction); `sync_markets` = `_market_inputs` + `_sync_markets`; `sync_seed` runs both input checks, reads the clock once and runs both in one `uow.transaction()`, then logs `segments_synced` and `markets_synced` (shared `_log_sync`). `MarketSync`, `SeedSync`, `_refuse_shared_markets`, `_record_market`.
- `app/queries/setup.py`: `SeededMarket`, `markets()`, `market(code)`.
- `main.py`: `MARKETS_FILE_NAME`/`MARKETS_FILE_LABEL`, `_load_seed` (read, parse and validate one file under its label; reuses `_read_seed_file`), `_sync_block` + `format_seed_sync(SeedSync)`, `seed_sync` running `Setup.sync_seed`; `InvalidMarkets` prints under the markets label. `__main__.py`: docstring and the `seed sync` help text name both files.
- Setting `us_solicitation_label` (STRING, Footer and booking, default `""`, `max_length(200)`) appended after `ai_budget_alert_pct`. The Settings page now shows a "Footer and booking" fieldset first.
- README: "Settings" (the label), "Seeds" (both files, the two-block summary, one transaction) with a new "Markets" subsection (fields table, floor, placeholders, validation, reads), the exit-code row, and the migrations table row for `0009_3_1_markets`.

**Choices made inside the spec's latitude**
- `market_for_country` accepts only an ASCII code (fullwidth letters are `None`) and returns `None` when more than one given Market claims the country (impossible for validated Markets; the conservative answer for hand-built lists).
- `market_form` leaves a window that isn't two naive whole-minute `time`s as it is, so validation refuses it rather than silently rounding it.
- `sync_markets` stores and compares the Markets that `validate_markets` returns from their file form (canonical tuples and enums), so a hand-built `MarketDef` with a list or a plain string compares equal to its stored row. It reads the stored Markets once (`all_markets()`) rather than `by_code` per Market.
- The GB floor follows `country_code` whatever the code or `enabled` (a disabled GB Market must still block webmail, require the privacy line and list legal forms).
- `FREE_WEBMAIL_DOMAINS` was extended in review (P1) within the spec's "at least": btopenworld.com, talk21.com, virgin.net, orange.net, wanadoo.co.uk, plus.com, freeserve.co.uk, aim.com, gmx.net, gmx.de, web.de, protonmail.ch, tuta.io, tuta.com, tutanota.de, zoho.com, zohomail.com.
- In review, the GB floor also refuses any `eligible_legal_forms` item outside decision 2's eight Companies House types (`markets[<code>].eligible_legal_forms[<index>]`: "isn't a corporate-subscriber company type a GB Market may list (PECR)"); a narrower list stays valid, and other Markets' lists are unaffected. Contract (d) in `tests/contracts/test_market_contracts.py` scans every module under `nextron/` and allows the markets repository's `insert`/`update` only inside `_sync_markets` of `app/setup/api.py`.

**Known limits (for review)**
- The ccTLD half of the stored-Market refusal is unreachable through valid rows: a ccTLD names its Market's country, so a shared ccTLD means a shared country and the `country_code` refusal fires first. It is tested by calling `_refuse_shared_markets` with a hand-built row.
- A stored row that no longer validates (say, after a rule is tightened) raises `InvalidMarkets` when read; `seed sync` then exits 1 under `seed/markets.yaml` naming the stored row's field and writes nothing, so it can't repair such a row itself (a migration would).
- Swapping two file Markets' country codes in one sync fails on `uq_markets_country_code` ("seed sync failed (IntegrityError); nothing was changed"): safe, and rare.

**Tests changed beyond the Code Map** (each forced by the second file or the new setting)
- `tests/app/test_telegram_secrecy.py` posts every declared setting key on `/settings` (the form must hold exactly the declared keys): `us_solicitation_label=""` appended.
- `tests/app/test_seed_sync.py`, besides `seed_dir()` and the `SUMMARY_FIRST`/"0 changed" lines: the first-sync event check compares `event_rows(...)[:2]` (the Markets' events follow), `test_each_change_adds_a_version` filters to `segments` events, the not-in-file test expects `SEGMENTS_FIRST + <old_segment line> + MARKETS_FIRST` and 4 new events, and the changed-definition output gains the markets block.
- `tests/unit/test_settings_page.py`: the legends and field order start with "Footer and booking" / `us_solicitation_label`.

**Verification so far:** `uv run pytest -n 4 --dist loadgroup tests/unit/test_markets_domain.py tests/unit/test_market_seed.py tests/app/test_market_sync.py tests/app/test_seed_sync.py tests/contracts tests/unit/test_settings_schema.py tests/unit/test_settings_page.py tests/unit/test_migrations.py` (1591 passed, 1 skipped), plus the other settings-touching files and `tests/app/test_serve_process.py`; `uv run lint-imports`: 29 kept, 0 broken; `NEXTRON_ENV=test uv run nextron seed sync --help`: usage, exit 0. The full suite through the lock is left to the track.

**Final verification (after review pass 1, P1–P6).** Full suite through `suite_lock.py` (track-C), twice: 5122 passed, 2 skipped, and 2 failed each time. Both failures are only the known flaky `tests/unit/test_job_runner.py::test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper` (a bulk thread outlives the 0.05 s `shutdown_grace`; it also fails intermittently when run alone, and this story touches no runner code; the fix is being made separately on `main`). `uv run lint-imports`: 29 kept, 0 broken.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers reviewed the diff since the baseline: blind hunter (BH, 13 findings), edge-case hunter (EC, 6) and verification gap (VG, 1). That is 20 raw findings, which deduplicate into 14 groups:
- 6 patched (P1–P6);
- 0 deferred;
- 8 rejected (R1–R8).

| # | Finding(s) | Verdict | Evidence | Route |
|---|---|---|---|---|
| P1 | BH3, EC6: `FREE_WEBMAIL_DOMAINS` misses common UK ISP and free providers (`btopenworld.com`, `virgin.net`, `talk21.com`, `gmx.net`, `web.de`, `protonmail.ch`, `tuta.io`…) | medium | **Real:** `is_free_webmail("x@btopenworld.com")` is false, so a UK individual subscriber on such a domain passes the free-webmail block (PECR). The spec asks for "at least" the AC's twelve, so extending the list stays inside it. | patch: add the domains and update the equality test |
| P2 | BH4, EC5: a seed edit can widen the GB legal forms (`limited-partnership`, `other`, …), and the floor still passes | medium | **Real:** `_check_compliance_floor` only checks that a GB Market's forms are a list, and `_legal_forms` accepts any `[a-z][a-z0-9-]` item. Decision 2 settles the GB set (eight types), and decision 3 says a seed edit can't loosen the GB rules. | patch: a GB Market's forms must be a subset of decision 2's eight (a private constant), naming the item |
| P3 | BH6: contract (b) only blocks two names, so a future writer with another name (`Setup.set_market_enabled` called from a POST `/rules/uk/save`) evades contracts (a) and (b) | low | **Real for a future change:** the checks match names only. The fix adds a test, not product code. | patch: a structural contract that `markets_repo.insert`/`update` are called only from `app/setup/api.py`'s `_sync_markets`, with a probe |
| P4 | BH10: the update path of the nullable columns is untested (window removed, legal forms list ↔ `not_checked`) | low | **Real gap:** the "changed" tests cover only a name, a footer line and `enabled`. The code looks right (`form.get` gives `None`, which is NULL). The fix is a test. | patch: a test that flips both and checks the NULL columns and the decoded round trip |
| P5 | BH13: a README paragraph in "What a sync does" isn't re-wrapped (README.md:422) | low | **Real:** cosmetic, and the fix is a direct correction. | patch |
| P6 | VG1: the `us_solicitation_label` declaration (type, section, default, `max_length(200)`) has no declaration test | low (gap, pre-verified) | **Filed evidence:** dropping the rule fails no test. Every sibling setting has a declaration test. | patch: a sibling-pattern test with the 200/201-character boundary |
| R1 | BH1, EC3: `render_footer_template` drops a required line whose value is blank (opt-out, legal name, address, UK privacy line) | low | **Spec behaviour.** The frozen Boundaries say "After the replacement, a blank line is dropped". Raising on a blank required value would break Story 4.11's AC ("drafting still proceeds" with an incomplete footer). Sending is gated by 4.11's `footer_complete` (6.9's `footer_complete` predicate), and `optout_line` has a default that a blank can't clear. Note for 4.11: `footer_complete` must cover every required placeholder. | rejected: it would change the approved spec |
| R2 | BH2, EC4: `cctld_country` and `is_free_webmail` let malformed input through (`Bob <bob@gmail.com>`, `bob@ gmail.com`, `gmail.com..`, fullwidth or zero-width characters) | low | **Spec contract.** The frozen Boundaries spell out the normalisation (strip, cut after `@`, drop one trailing dot), and the code does exactly that. Callers (3.2, 3.6) pass stored addresses, already normalised by Epic 2's `domain.identity`. Failing closed on non-hostnames would change the spec'd contract. Note for 3.2: pass normalised addresses. | rejected: it would change the approved spec |
| R3 | BH5: contract (a) checks only POST, so PUT, PATCH and DELETE could edit a rule | false | The dashboard route class refuses any method other than an exact GET or POST when a route is added, and `create_app` refuses such an app (`test_the_route_class_refuses_other_methods_when_the_route_is_added`, `route_rule_problems`). No non-POST writer can exist. | rejected |
| R4 | BH7, EC2: a stored row that no longer validates blocks every sync, and the message names `seed/markets.yaml` | low | **Frozen spec behaviour:** "A row that no longer validates raises `InvalidMarkets`". It needs a manual DB edit, or a rule tightened in code without a migration. It fails closed with exit 1 and nothing written. Listed as a known limit. Overwriting without decoding would change the repository contract. | rejected: it would change the approved spec |
| R5 | BH8, EC1: swapping two Markets' country codes in one sync gives "seed sync failed (IntegrityError)" | low | **Real but rare:** `uq_markets_country_code` rolls the whole transaction back and nothing changes. The fix adds checks against every stored row. Listed as a known limit. | rejected |
| R6 | BH9: nothing checks that a Market's timezones and English variant belong to its country | low | It needs a wrong seed edit. Its effect is a Send Window offset, not a compliance rule. The fix needs a tz-country table and new rules. | rejected |
| R7 | BH11: nothing warns while `markets` is empty after an upgrade | low | It fails closed: no Market means nothing is eligible. Segments behave the same since 2.1. The README says to run `seed sync` after `serve`. The fix is a new doctor or health check. | rejected |
| R8 | BH12: the audit trail keeps no prior Market definitions (only `v<n>` → `v<n+1>` events) | low | Decision 5 chose a `version` plus events. `seed/markets.yaml` is version-controlled, so each prior rule set is in git history. A versions table would change the approved spec. | rejected: it would change the approved spec |

## Design Notes

**The seed file** (`seed/markets.yaml`, after a header comment saying what it is, that `nextron seed sync` loads it, and that it is a YAML subset):

```yaml
markets:
  - code: us
    name: United States
    enabled: true
    country_code: US
    cctlds: [us]
    cold_email_allowed: true
    eligible_legal_forms: not_checked
    free_webmail_blocked: false
    default_timezone: America/New_York
    unknown_timezone: America/New_York
    unknown_send_window: '11:00-17:00'
    english_variant: en_us
    footer_template: ['{sender_signature}', '{legal_name}', '{postal_address}', '{optout_line}', '{us_solicitation_label}']
    privacy_line_required: false
  - code: uk
    name: United Kingdom
    enabled: true
    country_code: GB
    cctlds: [uk]
    cold_email_allowed: true
    eligible_legal_forms:
      - ltd
      - plc
      - private-limited-guarant-nsc
      - private-limited-guarant-nsc-limited-exemption
      - private-limited-shares-section-30-exemption
      - private-unlimited
      - private-unlimited-nsc
      - llp
    free_webmail_blocked: true
    default_timezone: Europe/London
    unknown_timezone: Europe/London
    english_variant: en_gb
    footer_template: ['{sender_signature}', '{legal_name}', '{postal_address}', '{optout_line}', '{privacy_line}']
    privacy_line_required: true
```

(A block list may replace the long flow lists if the reader's line rules need it; the values must stay the same.)

**`FREE_WEBMAIL_DOMAINS`:**
- **Google:** gmail.com, googlemail.com.
- **Microsoft:** outlook.com, hotmail.com, hotmail.co.uk, live.com, live.co.uk, msn.com.
- **Yahoo:** yahoo.com, yahoo.co.uk, ymail.com, rocketmail.com.
- **Apple:** icloud.com, me.com, mac.com.
- **AOL:** aol.com, aol.co.uk.
- **Proton:** protonmail.com, proton.me, pm.me.
- **GMX and Mail.com:** gmx.com, gmx.co.uk, mail.com.
- **Yandex:** yandex.com.
- **UK providers:** btinternet.com, sky.com, virginmedia.com, ntlworld.com, talktalk.net, blueyonder.co.uk, tiscali.co.uk.
- **Others:** fastmail.com, hushmail.com, tutanota.com.

**The footer render**, US template, with the four required values and the label blank:

```
<signature>
<legal name>
<postal address>
<opt-out line>
```

With the label set to `Advertisement`, a fifth line `Advertisement` follows.

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_markets_domain.py tests/unit/test_market_seed.py tests/app/test_market_sync.py tests/app/test_seed_sync.py tests/contracts` -- expected: all pass.
- the full suite through `suite_lock.py` (`track-C`) -- expected: exit 0, all pass, every import contract kept.
- `NEXTRON_ENV=test uv run nextron seed sync --help` -- expected: usage, exit 0.
