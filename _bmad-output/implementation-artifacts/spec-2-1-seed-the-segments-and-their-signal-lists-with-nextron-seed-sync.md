---
title: 'Story 2.1: Seed the Segments and their Signal lists with `nextron seed sync`'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '3191abc6037e8f858729d45aa50713ffbfaa4245'
review_loop_iteration: 0
story_key: '2-1-seed-the-segments-and-their-signal-lists-with-nextron-seed-s'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has no Segments. Research, scoring and drafting (Epics 2–5) need the two v1 Segments, Law-PrivateAI and Ops-Automation, with their Signal types, weights, disqualifiers, decision-maker roles and discovery queries, as the PRD's FR-1 defines them. Until the Slice 3 editing screens ship, AD-12 says they come from `seed/*.yaml` through an idempotent `nextron seed sync`, and neither the file, the table nor the command exists.

Requirements: FR-1, AD-3, AD-4, AD-12, NFR-40.

**Approach:**
- `nextron-engine/seed/segments.yaml` holds both Segments.
- A strict, stdlib-only YAML-subset reader (decision 1) and a domain validator turn the file into typed `SegmentDef`s, or name the failing line or field.
- `nextron seed sync` upserts them by key into a new `segments` table (owner `setup`, migration `0007_2_1_segments`). Each created or changed Segment gets a new `version` and one `events` row with actor `seed`.
- A test compares the seeded Signal types with the PRD's FR-1 table, row for row.

The acceptance criteria are Story 2.1 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **The YAML-subset reader** (`nextron/domain/seed_yaml.py`, stdlib only).
  - **`parse(text: str) -> dict | list`.** A problem raises `SeedSyntaxError(line, reason)`: a `DomainError` with code `seed_syntax_error`, a 1-based `line` (0 when the problem isn't on one line), and the message `"line <n>: <reason>"`. The reason never repeats more than 40 characters of the input.
  - **What it accepts:**
    - One optional leading BOM.
    - `\n` or `\r\n` line ends. Any other `\r`, any tab, and any other `Cc` character are refused.
    - Comments: `#` at the start of a line's content, or after a space, outside quotes, running to the end of the line. Blank and comment-only lines are skipped.
    - Indentation by spaces. A nested block is indented more than its parent, and siblings share one column.
    - **Block mappings:** `key: value`, or `key:` followed by a more-indented block. A key fullmatches `[a-z][a-z0-9_]{0,63}`, and a repeated key in one mapping is refused.
    - **Block sequences,** indented under their key:
      - `- value`;
      - `- key: value`, which starts a mapping whose further keys sit at the column of the first key;
      - `-` alone, followed by a more-indented block.
    - **Flow sequences** of scalars on one line: `[a, 'b, c', "d"]`, with `[]` for an empty list. Nesting, an empty item and a trailing comma are refused.
    - **Single-quoted scalars**, where `''` is a quote, and **double-quoted scalars**, whose only escapes are `\\` and `\"`. Both are closed on the same line.
    - **Plain scalars,** trimmed:
      - one that fullmatches `-?(0|[1-9][0-9]*)` is an `int`;
      - `true` and `false` are `bool`;
      - anything else is a `str`.
  - **What it refuses,** naming the line and the fix ("write it in quotes" where quoting would help):
    - document markers (`---`, `...`);
    - anchors, aliases and tags (`&`, `*`, `!`);
    - flow mappings (`{`);
    - block scalars (`|`, `>`);
    - `?` keys;
    - a plain scalar containing `: `, ending in `:`, starting with `- `, or starting with any of ``[]{}&*!|>%@`,?:`` (in a flow list, an item also may not contain `[`, `]`, `{`, `}` or `,`);
    - multi-line scalars;
    - `key:` with no value and no nested block;
    - a top-level scalar, and an empty or comment-only file;
    - nesting deeper than 32 levels;
    - **any plain scalar another YAML reader could read as something other than a string,** apart from the subset's own ints and booleans:
      - a fullmatch of `[-+.]?[0-9][0-9A-Za-z_.:+-]*`;
      - `.inf` and `.nan` in any case or sign;
      - `y`, `n`, `yes`, `no`, `on`, `off`, `true`, `false`, `null` and `~`, in any case other than the accepted `true`/`false`.
  - **The subset never misreads:** any text it accepts gives the same value a YAML 1.1 or 1.2 reader gives.
- **Segment definitions** (`nextron/domain/segments.py`, stdlib only; frozen, slotted dataclasses).
  - **Enums (`StrEnum`):**

    | Enum | Values |
    |---|---|
    | `SignalClass` | `event`, `attribute` |
    | `EvidenceType` | `quote`, `absence`, `count` |
    | `SignalSource` | `careers_page`, `ats_board`, `signal_inbox`, `news_pages`, `team_pages`, `practice_pages`, `website`, `job_ads` |
    | `SizeUnit` | `lawyers`, `staff`, `usd_revenue` |
    | `SizeClue` | `lawyers_at_office`, `lawyers_firm_wide`, `staff`, `group_staff`, `locations` (Story 2.9's size clues) |
    | `DisqualifierKind` | `fewer_than` (clue < threshold), `more_than` (clue > threshold) |

  - **Types:**
    - `SizeRange(minimum, maximum, unit)`;
    - `Disqualifier(key, kind, size_clue, threshold, description)`;
    - `PreferredRoles(when_terms, roles)`;
    - `SignalType(key, name, signal_class, tier, evidence, sources, score_only, weight, match_terms, preferred_roles)`;
    - `DiscoveryQueries(places)`;
    - `SegmentDef(key, name, industry_keywords, size_range, decision_maker_roles, disqualifiers, signal_types, discovery_queries)`.

    Every list is a tuple. `decision_maker_roles` is a tuple of ranks; each rank is a tuple of equal titles, best rank first.
  - **The file shape.** Its field names are the YAML keys:
    - the top level is exactly `segments: [<segment>, ...]`, at least one;
    - a segment has exactly `key`, `name`, `industry_keywords`, `size_range` (`minimum`, `maximum`, `unit`), `decision_maker_roles`, `disqualifiers`, `signal_types` and `discovery_queries` (`places`);
    - a disqualifier has `key`, `kind`, `size_clue`, `threshold` and `description`;
    - a signal type has `key`, `name`, `class`, `tier`, `evidence`, `sources`, `score_only` and `weight`, plus optional `match_terms` and `preferred_roles` (each `when_terms` and `roles`).

    Missing and unknown fields are refused.
  - **`validate_segments(data) -> tuple[SegmentDef, ...]`**, in file order. It raises `InvalidSegments(field, reason)` for the first problem, in file order:
    - `DomainError`, code `invalid_segments`;
    - message `"<field>: <reason>"`;
    - `field` is a path such as `segments[ops_automation].signal_types[ops_dynamics_gp].weight`, with an item's `key` in the brackets when that key is valid, else its 0-based index;
    - the reason never repeats a value longer than 40 characters.
  - **Value rules:**
    - **Keys** fullmatch `[a-z][a-z0-9_]{0,30}`. Segment keys are unique in the file, and so are signal-type keys and disqualifier keys, each across all Segments.
    - **Texts** (`name`, `description`, keywords, terms, titles, queries) are a `str` that is non-blank, has no leading or trailing whitespace and no `Cc`, `Cf`, `Zl` or `Zp` character, and is at most 200 characters. Terms, titles, keywords and queries are at most 100.
    - **Lists** hold 1 to 50 items with no duplicates (case-insensitive for texts). `disqualifiers` may be empty, and so may the optional `match_terms` and `preferred_roles` when given.
    - **Integers are an `int`, never a `bool`:**
      - `tier` is 1, 2 or 3;
      - `weight` is 1 to 100;
      - `threshold` is at least 1;
      - `size_range` has 0 ≤ `minimum` ≤ `maximum`.
    - **`score_only`** is a `bool`.
  - **Rules (the AC's, plus what keeps later stories safe).** Each one fails naming its field:
    1. In each Segment, every Tier 1 weight is above every Tier 2 weight, and every Tier 2 weight above every Tier 3 weight (decision 8).
    2. An Event's `evidence` is exactly `[quote]`.
    3. An Event isn't `score_only`.
    4. A signal type whose `sources` include `careers_page` or `ats_board` (a hiring Signal) has non-empty `match_terms`.
    5. Each `preferred_roles` entry's `when_terms` are among the type's `match_terms` (case-insensitive).
    6. **No Signal type represents ABA Formal Opinion 512 or the SRA notice** (`represents_market_fact(text)`). Each type's `key`, `name` and every match term is lowercased, and each run of characters other than `[a-z0-9]` becomes one space. A text is refused when its words include `sra`, or contain the phrases `solicitors regulation authority` or `formal opinion`, or include both `aba` and `512`.
    7. Every title is unique across a Segment's ranks (case-insensitive).
  - **`SegmentDef.titles()`** is every decision-maker title: the ranked titles in rank order, then each preferred role not already ranked, in file order (decision 6).
  - **`definition_text(segment) -> str`** is the canonical JSON of the segment's file form: `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))`, with enums as their values and every optional field present (`match_terms` and `preferred_roles` as `[]`).
  - **`segment_from_text(text) -> SegmentDef`** is `json.loads` plus the same validation of one segment. It is the inverse of `definition_text`, and a text that no longer validates (a JSON error included) raises `InvalidSegments`.
- **Persistence.**
  - **Migration `0007_2_1_segments`** (`nextron/db/alembic/versions/0007_2_1_segments.py`, down `0006_1_13_metering`, `downgrade()` raises `NotImplementedError` like 0006) creates `segments`:
    - `id` ULID (`pk_segments`);
    - `key` `String(32)`, unique (`uq_segments_key`);
    - `definition` `Text`;
    - `version` `Integer` with `ck_segments_version` `version >= 1`;
    - `created_at` and `updated_at` as `UtcDateTime`;
    - every column NOT NULL.
  - **The model** is `SegmentRecord` in `db/models/setup.py`. It matches the migration, so the drift test passes.
  - **`owners.py`:** `segments` → `setup`, with all six columns.
  - **`pii_registry.py`:** all six columns are `none`. The definition holds role titles, keywords and rules, never a person.
  - **The repository** (`db/repos/setup/segments.py`, through `current().session`). `StoredSegment(id, key, definition, version, created_at, updated_at)` is frozen.
    - `all_segments()` returns every row, by key;
    - `by_key(key)` returns one row or `None`;
    - `insert(key, definition, at) -> str` creates version 1;
    - `update(segment_id, definition, version, at)`.

    It checks nothing.
  - **Keep the revision id in the existing `HEAD` constants** (track A renumbers it at merge):
    - `tests/contracts/test_persistence_contracts.py`, `HEAD`, plus its chain probes: `0007_*` becomes `0008_*`, `0008_*` becomes `0009_*`, and "revision 7" becomes "revision 8";
    - `tests/unit/test_migrations.py`, `HEAD`;
    - the `tests/app/test_serve_process.py` assert.
- **Setup** (`app/setup/api.py`).
  - **`@use_case Setup.sync_segments(segments: Sequence[SegmentDef]) -> SegmentSync`.**
    - It refuses a non-`SegmentDef` (`TypeError`) or a repeated key (`ValueError`) before any transaction.
    - It reads the clock once, then works in one transaction, in input order:
      - **No row:** `insert`, then `eventlog.append(entity="segments", entity_id=<id>, from_status=None, to_status="v1", cause="seed_sync", cause_ref=None, actor="seed", at=now)`.
      - **The stored `definition` differs from `definition_text(segment)`:** `update` to version n+1, then one `events` row from `v<n>` to `v<n+1>` (decision 4).
      - **Otherwise** nothing is written.
    - Stored keys missing from the input are never changed or deleted (decision 3).
    - It enqueues no job, publishes no notification and touches no other table, so a sync never re-scores a Lead.
    - It returns the frozen `SegmentSync(created: tuple[(key, version)], changed: tuple[(key, from, to)], unchanged: tuple[key], not_in_file: tuple[(key, version)])`. The first three are in input order, and `not_in_file` is by key.
    - After the commit it logs `segments_synced` (INFO) with the counts `created`, `changed`, `unchanged` and `not_in_file`.
- **Queries** (`app/queries/setup.py`).
  - `segments() -> tuple[SeededSegment, ...]`, by key;
  - `segment(key) -> SeededSegment | None`.

  `SeededSegment(id, key, version, definition: SegmentDef)` is frozen and decoded with `segment_from_text`. These are the read API Stories 2.2 onwards use.
- **The command** (`main.py`, `__main__.py`).
  - **The CLI** is `nextron seed sync [--env {dev,test,prod}]`: a `seed` command with a required `sync` subcommand.
  - **`seed_sync(*, env=None, seed_dir=None, clock=None, out=None, err=None) -> int`.** `seed_dir` defaults to `SEED_DIR = ENGINE_ROOT / "seed"` (`bootstrap.ENGINE_ROOT`). Steps, stopping at the first failure:
    1. `load_bootstrap(env)`. A `BootstrapError` exits 2 (the message on `err`).
    2. Read `<seed_dir>/segments.yaml` as UTF-8, at most 1 MiB. A missing, unreadable, oversized or non-UTF-8 file prints `seed/segments.yaml: <reason>` and exits 1.
    3. `seed_yaml.parse`, then `validate_segments`. A `SeedSyntaxError` or `InvalidSegments` prints `seed/segments.yaml: <message>` and exits 1, before any database access.
    4. **The database** `<data dir>\nextron.db` must exist and be at `head_revision()`. The revision is read with `current_revision` on `create_read_only_engine(path)`, as doctor does, and that engine is disposed. Only then does `connect_database(config)` open it for the write, never `open_database` or `ensure_current`. Otherwise it prints one of these and exits 1, creating and changing nothing (decision 2):
       - "the database doesn't exist yet: start the engine once (nextron serve), then run seed sync again";
       - "the database is at <rev>, not <head>: start the engine once (nextron serve) to migrate it, then run seed sync again";
       - the `DatabaseStateError` or `ConnectionSettingError` message.
    5. One unit of work: `Setup(clock).sync_segments(defs)`. Any other exception prints "seed sync failed (<ExceptionType>); nothing was changed" and exits 1, because the transaction rolled back.
    6. **Print the summary to `out` and exit 0.** The first line is `segments: <n> changed, <m> unchanged`, where n counts created plus changed. Then one line each:
       - `  <key>: created (version 1)`;
       - `  <key>: changed (version <a> -> <b>)`;
       - `  <key>: not in seed/segments.yaml, left as it is (version <v>)`.

       A second run on the same file prints `segments: 0 changed, 2 unchanged`.
  - **What it never does:** take the instance lock, configure logging (only `serve` opens `nextron.log`), read Credential Manager, or build an app, runner or scheduler. The engine is disposed in a `finally`.
- **The seed file** (`nextron-engine/seed/segments.yaml`) is exactly the content under Design Notes, with a header comment saying what the file is, that `nextron seed sync` loads it, and that it is a YAML subset (decision 1).
- **Tests** run as `test` with temporary data dirs. They make no keyring read and no network call.

**Never:**
- **No new parts beyond these:**
  - one table and migration;
  - two domain modules;
  - one repository;
  - one use case;
  - two queries;
  - one CLI command.

  No dependency (no PyYAML), adapter, port, route, page, setting, credential, job type or trigger.
- **No re-scoring, no job and no notification from a sync.** Nothing reads `events` to act.
- **No `segment_versions` table** (Story 11.5), **no `registry` or job-title discovery queries** (Stories 8.x), **no markets or other seed files** (their stories).
- **What not to edit:** the vendored HTMX, `serve`, `doctor` and `open` behaviour, and anything outside `nextron-engine/` except this spec, `epic-2-context.md`, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **No YAML library: a strict YAML-subset reader.** The spine's Stack table pins no YAML library, and adding PyYAML is a new pinned dependency that needs Mac's OK. The reader accepts only block mappings and sequences, one-line flow lists, quoted and plain scalars, ints and `true`/`false`. Everything else is refused with its line number, so nothing is ever misread. Swapping in PyYAML later changes no seed file.
2. **`seed sync` never creates or migrates the database.** It refuses unless `nextron.db` exists and is at head, and says to start the engine once. Only `serve` migrates, after its `pre_migrate` backup (AD-2, AD-20; Story 1.16).
3. **A Segment stored but missing from the file is left as it is,** listed as "not in seed/segments.yaml". Deleting one would orphan the Leads that point to it.
4. **Audit rows.** Segments have no status machine. Each creation or change appends one `events` row (entity `segments`, `from_status` `None` or `v<n>`, `to_status` `v<n+1>`, cause `seed_sync`, actor `seed`). An unchanged Segment writes nothing, matching Stories 3.1 and 4.1's "a second run appends no `events` row".
5. **The keys:**
   - Segment keys are `law_private_ai` and `ops_automation`; their names are the PRD's "Law-PrivateAI" and "Ops-Automation".
   - Signal-type and disqualifier keys are unique across the file, so a stored Signal or disqualification needs only its key.
   - Each Signal type's `name` is the PRD's FR-1 Signal text, verbatim.
6. **Decision-maker roles are ranks of equal titles:**
   - Law: Managing Partner, then Owner.
   - Ops: COO or VP Operations, then Owner or CEO.

   The finance roles (Controller, CFO, Finance Director) are not a rank. They are the Ops hiring type's `preferred_roles`, applied only when the posting matched an AP/AR term (`when_terms`: accounts payable, accounts receivable, AP/AR), because the PRD ties them to finance-role Events only. `titles()` includes them, so Story 2.10 keeps such people as Contacts.
7. **Disqualifier clues.** Both Law rules read `lawyers_firm_wide`: a firm with fewer than 5 lawyers, or an office of a firm with more than 50. Ops reads `staff` (fewer than 20) and `group_staff` (more than 1,000). The comparisons are strict, as the PRD's wording is.
8. **The Tier weight order is checked within each Segment,** because scores compare only a Segment's own types. Weights run from 1 to 100, since Signal and Fit Scores cap at 100.
9. **Seeded vocabulary that the PRD leaves open (editable seed data):**
   - **Industry keywords:**
     - Law: law firm, legal services, attorneys, solicitors.
     - Ops: distributor, wholesale, logistics, freight, light manufacturing.
   - **Places queries:**
     - Law: law firm, solicitors.
     - Ops: wholesale distributor, logistics company, freight company, manufacturer.
   - **Source codes**, mapped from the PRD's words: "Team and practice pages" is `team_pages` plus `practice_pages`; "News or press pages" is `news_pages`; "Job ads" is `job_ads`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First sync | DB at head, no segments; the shipped file | 2 rows at version 1; 2 `events` rows (`None` → `v1`, `seed_sync`, actor `seed`); "segments: 2 changed, 0 unchanged" plus 2 created lines; exit 0 | N/A |
| Second sync | same file | "segments: 0 changed, 2 unchanged"; no row, `updated_at` or `events` change | N/A |
| Changed | Ops Dynamics GP weight 30 → 25 | Ops at version 2, `updated_at` = now; one `events` row `v1` → `v2`; Law untouched; "1 changed, 1 unchanged" plus "ops_automation: changed (version 1 -> 2)" | N/A |
| Not in file | stored `old_segment` not in the file | listed "not in seed/segments.yaml, left as it is (version 1)"; row unchanged | N/A |
| Invalid rule | Tier 3 weight 70; Event with `[quote, absence]`; score-only Event; repeated key; type named "ABA Formal Opinion 512"; key `law_sra_notice` | exit 1, `seed/segments.yaml: <field path>: <reason>`; DB file SHA-256 unchanged | nothing written |
| Syntax | tab; `&anchor`; `{a: 1}`; `key: 1.5`; `key: yes`; duplicate key; unclosed quote | exit 1, `seed/segments.yaml: line <n>: <reason>` | nothing written |
| No DB / behind / foreign | no `nextron.db`; revision `0005_1_12_notify`; tables without a revision | exit 1 with the message; no file created; revision unchanged | nothing written |
| File | missing; 1 MiB + 1 byte; invalid UTF-8 | exit 1 naming `seed/segments.yaml` | N/A |
| Env | `NEXTRON_ENV` unset; `--env test` | exit 2 with the env error; `--env` picks that env's data dir | N/A |
| No re-score | a queued job and other tables populated before the sync | jobs and every table other than `segments` and `events` byte-identical (dump) | N/A |

</frozen-after-approval>

## Code Map

- **The CLI.** `nextron-engine/nextron/__main__.py`: `build_parser` and `main`, plus a docstring line for `seed sync` (it currently says "Later stories add ``seed sync``"). `tests/unit/test_doctor.py:873` (`test_cli_dispatches_doctor_with_its_flags`) is the dispatch-test pattern.
- **The composition root** (`nextron-engine/nextron/main.py`):
  - the constants at 301-317 (`DB_FILE_NAME`, `EXIT_*`);
  - `connect_database` (604, never migrates);
  - `doctor` (946), the CLI pattern: injectable `out`, `err` and `clock`, and `load_bootstrap(env)`;
  - the docstring, which gets a "Seed sync" paragraph.

  Import `current_revision` and `head_revision` from `db.migrations`, which `main` already imports from (233). Contract c4 means `main` may not import sqlalchemy.
- **Setup.** `nextron-engine/nextron/app/setup/api.py`: `Setup` (239). Its module docstring gets a `sync_segments` paragraph. The `_record` helpers in `nextron/app/notify/api.py:152` and `app/jobqueue/api.py:217` are the `eventlog.append` pattern. The import rule: domain, `db.uow`, `db.eventlog` and its own repositories.
- **Queries.** `nextron-engine/nextron/app/queries/setup.py`. Its import rule is domain plus `nextron.db.repos.setup` (`db.uow` is allowed too).
- **Persistence:**
  - `db/models/setup.py` (`SettingRecord` is the pattern: `ulid_pk()`, `UtcDateTime`);
  - `db/models/__init__.py` (export `SegmentRecord`);
  - `db/repos/setup/settings.py` (the repository pattern) and `db/repos/setup/__init__.py` (the docstring);
  - `db/owners.py`, `db/pii_registry.py`;
  - `db/alembic/versions/0002_1_6_settings.py` (the unique-key DDL pattern) and `0006_1_13_metering.py` (the header and the check constraint `op.f(...)` pattern).
- **The event log.** `nextron-engine/nextron/db/eventlog.py`. `actor="seed"` is already allowed, and `entity` must be in `owners.TABLES`.
- **Domain patterns:**
  - `nextron/domain/errors.py` (each `DomainError` declares a unique `code`; `tests/contracts/test_error_contracts.py` imports every domain module);
  - `nextron/domain/settings_schema.py` (refusals naming the field, never the value).
- **Migration tests to update:**
  - `tests/contracts/test_persistence_contracts.py:72` `HEAD`, and the probes at 670-687;
  - `tests/unit/test_migrations.py:37`;
  - `tests/app/test_serve_process.py:252`.

  Other tests use `head_revision()`, and `test_doctor.py`'s `0005_1_12_notify` "behind" case stays valid.
- **The source tree.** `tests/contracts/test_source_tree.py` `FILES`: add the new modules, the migration, the repository and `seed/segments.yaml`.
- **The planning-doc test pattern.** `tests/unit/test_triggers.py:33-91` keeps a hard-coded copy and parses the planning doc with `ENGINE_ROOT.parent`, skipping when the doc is absent. Use it for the PRD's FR-1 table: `_bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/prd.md`, the table under `#### FR-1: Segment definitions`, header `| Segment | Signal | Class | Signal Tier | Evidence | Source |`.
- **Test helpers.** `tests/migration_chains.py` (`engine_on`, `run_sql`, `stored_revisions`, `sha256`). `main.open_database(config)` creates a database at head for app tests. `tests/unit/test_doctor.py` has the `local_app_data` env fixture pattern.
- **Logging.** Event names stay under 32 characters (`tests/contracts/test_log_event_names.py`). Redaction masks any run of 32 or more `[A-Za-z0-9_-]` characters, which is why keys stop at 31.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/seed_yaml.py` -- `parse` and `SeedSyntaxError` -- the reader for every seed file (decision 1).
- [x] `nextron-engine/nextron/domain/segments.py` -- the enums, the types, `validate_segments`, `InvalidSegments`, `represents_market_fact`, `titles()`, `definition_text` and `segment_from_text`.
- [x] `nextron-engine/seed/segments.yaml` -- the Design Notes content.
- [x] `nextron-engine/nextron/db/models/setup.py`, `db/models/__init__.py`, `db/alembic/versions/0007_2_1_segments.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/setup/segments.py`, `db/repos/setup/__init__.py` -- the table, its mapping and its repository.
- [x] `nextron-engine/nextron/app/setup/api.py`, `app/queries/setup.py` -- `sync_segments`, `SegmentSync`, `segments()`, `segment(key)` and `SeededSegment`.
- [x] `nextron-engine/nextron/main.py`, `nextron/__main__.py` -- `SEED_DIR`, `seed_sync`, the `seed sync` command and the docstrings.
- [x] `nextron-engine/tests/unit/test_seed_yaml.py` -- every accepted form with its exact value; every refusal with its line number (the Syntax row and each rule in the reader's "refuses" list); BOM and CRLF; the reason truncating long input.
- [x] `nextron-engine/tests/unit/test_segments_domain.py`:
  - each validation rule and value rule failing with its exact field path (the Invalid rule row included);
  - unknown and missing fields;
  - `represents_market_fact` on each refused form, and on safe texts such as "Hiring intake…";
  - `titles()`;
  - the `definition_text` and `segment_from_text` round trip;
  - a stored text that no longer validates.
- [x] `nextron-engine/tests/unit/test_segment_seed.py` -- the shipped file:
  - it validates into exactly the Design Notes content;
  - the AC's sizes, roles and disqualifiers (with rule key, description and size clue);
  - the weight defaults by class and tier (60/35/30/15);
  - the match terms (Story 2.11's term lists) and preferred roles;
  - no market-fact type;
  - a hard-coded FR-1 transcription compared row for row with the seeded types (name, class, Signal Tier, Evidence, Sources, score-only), through a fixed PRD-word → code mapping;
  - the transcription compared with the PRD's own table, parsed from `prd.md` (skipped when absent), plus a parser proof on an inline sample.
- [x] `nextron-engine/tests/app/test_seed_sync.py` -- real units on a temporary data dir (`open_database` first): every I/O Matrix row through `main.seed_sync(env="test", seed_dir=...)` with captured `out` and `err`; `queries.setup.segments()` and `segment()`; the `segments_synced` record; `sync_segments` refusing a non-`SegmentDef` or a repeated key; and the CLI dispatch of `seed sync` and `seed sync --env prod` (monkeypatched `engine.seed_sync`), with `seed` alone exiting 2.
- [x] The migration and tree tests in the Code Map; `nextron-engine/README.md` -- a "Seeds" section: the file, the subset (decision 1), `nextron seed sync` and its output, the at-head rule (decision 2), and that a sync never re-scores.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes and every contract (29) is kept.
- Given the shipped seed file, when the tests compare it with the PRD's FR-1 table, then the ten rows match in order, and no Signal type represents ABA Formal Opinion 512 or the SRA notice.
- Given every model column, when the persistence contracts run, then `segments` is fully mapped in `owners.py` and `pii_registry.py`, and the migration matches the model.

## Implementation Notes

Choices made while building, where the spec left room or a detail had to give:

- **The `segments_synced` fields are `created_count`, `changed_count`, `unchanged_count` and `not_in_file_count`.** `created` is a built-in `LogRecord` attribute, so `extra={"created": ...}` raises `KeyError` whenever INFO is enabled (a test caught it). The other three take the same suffix for consistency.
- **The reader refuses a few more forms than the spec lists, all to keep "never misreads":**
  - `+.5` and `-.5` (YAML 1.1 floats that the spec's pattern misses: the pattern used is `[-+]?\.?[0-9][0-9A-Za-z_.:+-]*`, a superset);
  - a date followed by a space or `T` (a YAML 1.1 timestamp may hold spaces);
  - `=` and `<<` (YAML 1.1's value and merge keys);
  - `?` inside a flow item (PyYAML ends a plain flow scalar at `?`);
  - a key that YAML 1.1 reads as a boolean or null (`on:`, `no:`, `y:`, `null:`);
  - NEL, U+2028 and U+2029 (YAML 1.1 line breaks), surrogates, U+FFFE and U+FFFF, and a BOM after the start;
  - a plain value starting with `#` or equal to `-`;
  - a list at its key's own column (YAML's "indentless" list), with "indent the list under its key".
- **`sync_segments` validates a non-empty input again** (`validate_segments` on the file form), after the `TypeError` and `ValueError` checks. A hand-built `SegmentDef` that breaks a rule raises `InvalidSegments` before any transaction. An empty input is allowed: it writes nothing and lists the stored keys as `not_in_file`.
- **Database states the spec doesn't name.** An empty file (no revision) gets "the database doesn't exist yet: …", because `serve` treats it like a new one. A revision this code doesn't know gets "database revision <rev> is newer than this code", the `DatabaseAheadError` wording. A file SQLite can't read gets "seed sync failed (<Type>); nothing was changed".
- **Rule 1 compares each Signal type with the earlier types of its Segment**, so the later type of a clashing pair is the one named. The Tier 3 weight-70 case names `law_size_document_heavy.weight`.
- **Field paths.** A problem with the whole file is `(top level)`. A `segment_from_text` problem uses `segments[<key>]`, or `definition` when the text isn't JSON or repeats a field. Fields are checked in the file's order: an unknown field is refused where it appears, then missing fields, then the rules.
- **The seed file** writes the long Ops `match_terms` as a block list: a flow list must start on its key's line. The Law size type's name is single-quoted for clarity only. The reader would accept it plain: it doesn't fullmatch the number pattern.

**Review fixes (pass 1, P1–P10):**
- **P1.** `sync_segments` now decodes each stored Segment the input leaves out (`segment_from_text`) inside the transaction, before any write. If the input reuses one of its signal-type or disqualifier keys, it raises `InvalidSegments`. The error names the input field, in file order (`segments[<key>].disqualifiers[<key>].key` or `segments[<key>].signal_types[<key>].key`: "is also a key of the stored Segment <key>, which is no longer in the file: rename one of them"). Nothing is written. `seed_sync` prints that as `seed/segments.yaml: <message>` (exit 1). The docs cover it: the `sync_segments` paragraph, `main.py`'s Seed sync step 5 and the README's Seeds section. Tests: the not-in-file test's `old_segment` now has distinct keys, and new tests cover a shared signal-type key and a shared disqualifier key (exit 1, message, dump unchanged), plus the use case refusing before any write.
- **P2.** The reader also refuses a fullmatch of the YAML 1.1 float `[-+]?([0-9][0-9_]*)?\.[0-9.]*([eE][-+][0-9]+)?` (`YAML11_FLOAT`). `.`, `-.`, `+.`, `..`, `.e+5` and `.E-1` are in the refused-scalar test, and the module docstring says so.
- **P3.** The `segments.py` docstring and the README now state rule 1 as the code applies it: within a Segment, every weight of a lower Tier number is above every weight of a higher Tier number, so Tier 1 is above Tier 3 even with no Tier 2.
- **P4.** The README says `disqualifiers`, `match_terms` and `preferred_roles` may be empty.
- **P5.** The `__main__.py` docstring lists a database ahead of the code ("newer than this code") among the exit-1 causes.
- **P6.** `main` dispatches `seed_sync` only for `seed_command == "sync"`, and `doctor` only for `doctor`. Anything else raises `AssertionError`. A new test proves an undispatched `seed` subcommand or command reaches neither.
- **P7.** `seed_yaml._KEY_LINE` and `_KEY_NO_SPACE` are built from `KEY_PATTERN.pattern`. `segments._FIELD_NAME` is gone; `_is_key` (`KEY_PATTERN`) decides whether an unknown field is shown by name.
- **P8.** A new test stores `zz_ops`, `aa_law` and `mm_law` in that order. It checks that `not_in_file` and `queries.setup.segments()` come back in key order.
- **P9.** A new test: `UPDATE segments SET version = 0` raises `sqlite3.IntegrityError` matching `ck_segments_version`.
- **P10.** The log test now has a run with created 0, changed 1, unchanged 1 and not-in-file 2, and checks all four counts on every run.

**Final verification (after pass 1).**
- `uv run pytest -q`, through the suite lock: 4560 passed and 2 skipped (`ai_eval` and the symlink test), in 260 s.
- `uv run lint-imports`: 29 kept, 0 broken.
- `NEXTRON_ENV=test uv run nextron seed sync --help`: usage, exit 0.
- Before the review fixes: 4547 passed, 2 skipped, and 29 kept.

Not verified here: `nextron seed sync` against the real dev or prod database (only temporary data dirs).

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (15 findings), EC = edge-case hunter (6), VG = verification gap (3 gaps, no other findings).
- **Counts:** 24 raw findings. BH11 makes two separate claims and BH12 three, so there are 27 claims, which make 23 entries after deduplication.
- **Outcomes:**
  - 10 entries patched (P1–P10): 1 medium and 3 low code fixes, 3 doc fixes and 3 test gaps;
  - 1 deferred (H);
  - 12 rejected: 2 as false.
- **No loopback.**
  - No finding is an intent gap.
  - Entry A is fixed in place: the clash it finds is a state the existing test builds, and the fix reuses `InvalidSegments` with no new surface.
  - Entry L's code is stricter than the frozen rule 1 (Tier 1 must beat Tier 3 even with no Tier 2), which FR-1's "Tier 1 above Tier 2 above Tier 3" asks for. The docs are corrected to say so.
- **Rejected only because the fix would change the approved spec:**
  - B (decision 3 keeps a Segment that left the file, with no retire flag);
  - S (rule 6's exact normalisation).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC1 | A stored Segment no longer in the file keeps its signal-type and disqualifier keys, and the file's Segments can reuse them. `sync_segments` validates only its input, so after a Segment key is renamed in the YAML, two stored Segments carry `ops_hiring_manual_entry`. That breaks decision 5's "a stored Signal or disqualification needs only its key". | medium | Verified: `test_a_stored_segment_not_in_the_file_is_left_as_it_is` builds exactly this state (`old_segment` is Law with every key unchanged), and the sync succeeds. The fix: before any write in the transaction, refuse an input signal-type or disqualifier key that a stored not-in-file Segment holds, with `InvalidSegments` naming the input field. The CLI prints that message like a file problem. | P1 |
| B | BH2, EC2 | Nothing can retire a Segment: one removed from the file is still returned by `segments()` and `segment()`, so later stories keep using it. | medium | True, but decision 3 (frozen) chose to leave such a Segment as it is, listed as "not in seed/segments.yaml". A retire flag is a new column and new behaviour. Flagged for Story 2.2 and Mac's review of decision 3. | reject |
| C | BH3, EC5 | `_seeded` never checks that the decoded definition's `key` equals the row's `key`. | low | Only `sync_segments` writes rows, always `definition_text(segment)` under `segment.key`, so they can differ only after a hand edit of the database. The guard is a new branch for a state the program can't reach. | reject |
| D | BH4 | One corrupt stored definition breaks every `segments()` read, and the error ("definition: is not valid JSON") names no row. | low | True, but only for a definition written outside `sync_segments` (corruption). Naming the row means wrapping every decode in a new branch. | reject |
| E | BH5 | After the Slice 3 screens ship, a stray `seed sync` would overwrite Mac's edits. | false | Story 11.5's AC changes `seed sync` to stop writing Segments ("Segments are managed in the dashboard; seed/segments.yaml ignored"). Until then the seed file is the only writer (AD-12). | reject |
| F | BH6 | The reader accepts `.`, `-.`, `+.`, `..`, `.e+5` and `.E-1` as text, which the YAML 1.1 float pattern `[-+]?([0-9][0-9_]*)?\.[0-9.]*([eE][-+][0-9]+)?` types as floats. That breaks the documented "never misreads". | low | Verified with `parse('k: .')` and the other forms: all are returned as `str`. The fix is direct: also refuse a fullmatch of that pattern. | P2 |
| G | BH7 | Texts aren't Unicode-normalised: NFC and NFD spellings count as distinct items, a re-saved file could bump versions, and a no-break or doubled space inside a term is accepted. | low | True, but Windows editors keep the encoding and normalisation of an existing file, and the shipped terms are plain ASCII words. Refusing non-NFC text and odd spaces adds two new rules. | reject |
| H | BH8 | Nothing maps a size unit to the size clue that measures it, and `usd_revenue` has no clue at all, so Story 2.15's size fit can't place an Ops Lead in range. | medium | True, and not caused by this story. The PRD's own review (R-18) records that there is no revenue source for the Ops Fit Score, and Story 2.9's size clues list none. The mapping belongs to Story 2.15, the first reader. | defer |
| I | BH9 | The `segments_synced` INFO record is never written in practice, because the CLI configures no logging. | low | True by design. The spec requires both the log record and a CLI that never opens `nextron.log` (only `serve` does). The CLI prints the summary, and the `events` rows keep every change. | reject |
| J | BH10 | A change shows only "changed (version 1 -> 2)", and the old definition is overwritten with no history. | low | Story 11.5 adds `segment_versions`, which keeps each version. Printing a field-level diff is a new feature beyond the AC. | reject |
| K | BH11 (part) | No test runs a sync while another connection holds the write lock, and a busy timeout prints only "seed sync failed (OperationalError)". | low | The engine's transactions are milliseconds against a 5-s `busy_timeout`, and the message already says nothing changed, so a retry is safe. A special message is a new branch. | reject |
| L | BH12 (part) | The module docstring and the README describe rule 1 as adjacent pairs, but `_check_tier_order` compares every lower Tier with every higher one, so Tier 1 must beat Tier 3 even with no Tier 2. | low | Verified in `_check_tier_order`. The stricter rule is FR-1's ranking (and the AC's, by transitivity, whenever Tier 2 exists), so the docs are corrected to state it. | P3 |
| M | BH12 (part) | The README says "1 to 50 distinct items per list" without saying that `disqualifiers`, `match_terms` and `preferred_roles` may be empty. | low | Verified in the README's Validation paragraph. The doc fix is direct. | P4 |
| N | BH12 (part) | The `__main__` docstring's exit-1 list leaves out a database ahead of the code, which `main.py` and the README include. | low | Verified. The doc fix is direct. | P5 |
| O | BH13 | `__main__.main` sends any `seed` command to `seed_sync` without reading `args.seed_command`, so a later seed subcommand would silently run the Segment sync. | low | Verified at `__main__.py:89`. The fix is direct: dispatch on `seed_command == "sync"`. | P6 |
| P | BH14 | `seed_yaml.KEY_PATTERN` is never used, `_KEY_LINE` and `_KEY_NO_SPACE` repeat its pattern, and `segments._FIELD_NAME` repeats `segments.KEY_PATTERN`. | low | Verified by grep: there is no use of `seed_yaml.KEY_PATTERN` anywhere. A change to one copy would leave the others behind. The fix is direct: build the line patterns from one key pattern, and use `KEY_PATTERN` for field names. | P7 |
| Q | BH15 | 51 new test lines are longer than 100 columns. | false | No formatter or line-length rule is configured (`pyproject.toml` has none), and existing tests already exceed 100 columns (for example `test_google_page.py`, 7 lines). No named harm. | reject |
| R | EC3 | A hand-built `SegmentDef` with a `str` where a tuple belongs (`industry_keywords="lawyers"`) is stored as seven one-letter keywords. | low | The only caller, `seed_sync`, passes the validator's own tuples, so the state is unreachable. Guarding every tuple field adds checks for it. | reject |
| S | EC4 | Rule 6 misses `law_aba512_notice`, `law_sra2024` and "S.R.A. notice". | low | True, but rule 6 is the frozen spec's exact normalisation, which the code implements. The shipped file satisfies the AC, and contract-style guards target mistakes, not evasion (Story 1.10 EC11). Extending the rule changes the approved spec. | reject |
| T | VG1 | Dropping the `ORDER BY key` from `all_segments()` passes every test, because the tests insert Segments in key order. | low | Pre-verified with a mutation probe. The fix is a test that stores Segments out of key order and checks `not_in_file` and `segments()` order. | P8 |
| U | VG2 | No test exercises `ck_segments_version`. The drift test ignores CHECK constraints. | low | Pre-verified by probe. The fix is a raw `UPDATE segments SET version = 0` expecting `IntegrityError` matching `ck_segments_version`, as the heartbeat tests do. | P9 |
| V | VG3 | The log test only ever sees `changed_count` and `not_in_file_count` at 0, so swapping or zeroing them passes. | low | Pre-verified. The fix is a logged run with distinct non-zero changed and not-in-file counts. | P10 |
| W | BH11 (part), EC6 | The revision is read on a separate read-only engine, so a database deleted or replaced before `connect_database` would get an empty file created. | low | That needs the file removed in the milliseconds between two statements of one command, and Windows refuses to delete a database the engine holds open. Opening the write connection differently adds a new path to the shared `connect_database`. | reject |

## Design Notes

**The seed content** (`seed/segments.yaml`). The YAML layout is free within the subset; the values are binding.

- **`law_private_ai`, "Law-PrivateAI":**
  - **Size:** 5–50 `lawyers`.
  - **Ranks:** `[Managing Partner]`, then `[Owner]`.
  - **Disqualifiers:**
    - `law_fewer_than_5_lawyers`: `fewer_than`, `lawyers_firm_wide`, 5, "Fewer than 5 lawyers";
    - `law_office_of_large_firm`: `more_than`, `lawyers_firm_wide`, 50, "Office of a firm with more than 50 lawyers".
- **`ops_automation`, "Ops-Automation":**
  - **Size:** 10000000–200000000 `usd_revenue`.
  - **Ranks:** `[COO, VP Operations]`, then `[Owner, CEO]`.
  - **Disqualifiers:**
    - `ops_fewer_than_20_staff`: `fewer_than`, `staff`, 20, "Fewer than 20 staff";
    - `ops_group_over_1000_staff`: `more_than`, `group_staff`, 1000, "Part of a group with more than 1,000 staff".

Keywords and queries are in decision 9.

| Segment | key | name (verbatim FR-1) | class | tier | evidence | sources | score_only | weight | match_terms / preferred_roles |
|---|---|---|---|---|---|---|---|---|---|
| Law | `law_hiring_legal_ops` | Hiring intake, legal-ops, knowledge-management, or innovation staff | event | 1 | quote | careers_page, ats_board, signal_inbox | false | 60 | intake, legal operations, knowledge management, innovation |
| Law | `law_ai_confidentiality_post` | Post about AI or client confidentiality | event | 1 | quote | signal_inbox | false | 60 | — |
| Law | `law_expansion` | New office, merger, or practice-area expansion | event | 2 | quote | news_pages, signal_inbox | false | 35 | — |
| Law | `law_size_document_heavy` | 5–50 attorneys; document-heavy practice | attribute | 3 | count, quote | team_pages, practice_pages | false | 15 | — |
| Law | `law_contact_form_only` | Contact form only (no chat, booking, or after-hours line) | attribute | 3 | absence | website | true | 15 | — |
| Ops | `ops_hiring_manual_entry` | Hiring data-entry, order-entry, AP/AR, or dispatch staff | event | 1 | quote | careers_page, ats_board, signal_inbox | false | 60 | data entry, order entry, accounts payable, accounts receivable, AP/AR, dispatch; preferred `when_terms` [accounts payable, accounts receivable, AP/AR] → roles [Controller, CFO, Finance Director] |
| Ops | `ops_rekeying_post` | Post about manual re-keying or ERP pain | event | 1 | quote | signal_inbox | false | 60 | — |
| Ops | `ops_expansion` | New facility, acquisition, or expansion | event | 2 | quote | news_pages, signal_inbox | false | 35 | — |
| Ops | `ops_dynamics_gp` | Dynamics GP still in use | attribute | 2 | quote | job_ads, website | false | 30 | — |
| Ops | `ops_manual_ordering` | Ordering by PDF or order form with no portal; "advanced Excel" in job ads | attribute | 3 | quote, absence | website, job_ads | false | 15 | — |

The `—` cells mean `match_terms: []` and `preferred_roles: []` (or the fields left out). "5–50" uses an en dash, as the PRD does. The last name holds straight double quotes, so write it single-quoted.

```python
@use_case
def sync_segments(self, segments: Sequence[SegmentDef]) -> SegmentSync:   # Setup
    now = self._clock.now()
    with current().transaction():
        for seg in segments:
            text = definition_text(seg)
            stored = segments_repo.by_key(seg.key)
            if stored is None:
                _record(segments_repo.insert(seg.key, text, now), None, 1, now)
            elif stored.definition != text:
                segments_repo.update(stored.id, text, stored.version + 1, now)
                _record(stored.id, stored.version, stored.version + 1, now)
```

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass (`ai_eval` and the symlink test skipped). Run through the suite lock.
- `uv run lint-imports` -- expected: 29 contracts KEPT.
- `NEXTRON_ENV=test uv run nextron seed sync --help` -- expected: usage text, exit 0.
