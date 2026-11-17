---
title: 'Story 4.1: Seed the default Sequence and Step briefs'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '3adcbcacf0f0229812ffa888e0473884fcf7958e'
review_loop_iteration: 0
story_key: '4-1-seed-the-default-sequence-and-step-briefs'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has no Sequence. Campaigns (4.2), Draft slots and due times (4.9), the grounding rules (4.13) and drafting (4.15, 4.18) all need the default four-Step Sequence and its Step briefs as PRD FR-4 defines them. AD-12 says they come from `seed/sequences.yaml` through `nextron seed sync`, which today loads only Segments (2.1) and Markets (3.1).

Requirements: FR-4, FR-71, AD-3, AD-12, NFR-9.

**Approach:**
- `nextron-engine/seed/sequences.yaml` holds the default Sequence. The strict reader (`nextron/domain/seed_yaml.py`) parses it. A new stdlib-only `nextron/domain/sequences.py` validates it into `SequenceDef`s, enforcing a compliance floor in code.
- **`nextron seed sync` is extended, not duplicated.** It loads `segments.yaml`, `markets.yaml`, then `sequences.yaml`, and syncs all three in **one transaction**. It upserts Sequences by `key` into new tables `sequences` and `step_briefs` (owner `setup`, migration `0010_4_1_sequences`), with a `version` and one `events` row (actor `seed`) per created or changed Sequence.

The acceptance criteria are Story 4.1 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **`nextron/domain/sequences.py`** (stdlib and `nextron.domain` only; frozen, slotted dataclasses; every list a tuple).
  - **Types:**
    - `WordComparison` (`StrEnum`): `under` (count < limit), `at_most` (count ≤ limit).
    - `LinkRule` (`StrEnum`): `footer_only` (no link in the body; the signature and footer hold the only links).
    - `SubjectRule(word_limit, word_comparison, guidance)`.
    - `SegmentAngle(segment, market, angle)`: a Segment key, a Market code and the angle text.
    - `StepBrief(step, delay_days, purpose, word_limit, word_comparison, allowed_content, ask, link_rule, no_price, no_audit, subject_rule, segment_angles)`. `step` is the 1-based position; `subject_rule` is `SubjectRule | None`.
    - `SequenceDef(key, name, steps)`.
  - **`max_words(word_limit, comparison) -> int`**: `limit - 1` for `under`, `limit` for `at_most`.
  - **The file shape.** The top level is exactly `sequences: [<sequence>, ...]`, with 1 to 20 items. A sequence has exactly `key`, `name` and `steps` (1 to 6 items). A Step has `delay_days`, `purpose`, `word_limit`, `word_comparison`, `allowed_content`, `ask`, `link_rule`, `no_price` and `no_audit`, plus optional `subject_rule` (exactly `word_limit`, `word_comparison`, `guidance`) and optional `segment_angles` (each exactly `segment`, `market`, `angle`). Missing and unknown keys are refused. Steps carry no number: a Step's number is its position.
  - **`validate_sequences(data) -> tuple[SequenceDef, ...]`**, in file order. The first problem, in file order (each mapping's fields in the file's order, then its missing fields, then its rules), raises `InvalidSequences(field, reason)`:
    - a `DomainError` with code `invalid_sequences` and the message `"<field>: <reason>"`;
    - `field` is a path such as `sequences[default].steps[2].ask`. A sequence's brackets hold its `key` when valid and not a repeat, else its 0-based index. **A Step's brackets hold its Step number** (1-based). Other list items use their 0-based index;
    - the reason never repeats a value longer than 40 characters.
  - **Value rules:**
    - `key` fullmatches `[a-z][a-z0-9_]{0,30}` and is unique in the file. `segment` fullmatches the same pattern; `market` fullmatches `[a-z][a-z0-9_]{0,15}` (3.1's code pattern).
    - **Texts** follow 2.1's text rules (a non-blank `str`, no leading or trailing whitespace, no `Cc`, `Cf`, `Zl` or `Zp` character). `name` is at most 100 characters; `purpose`, `ask`, each `allowed_content` item, each `guidance` item and `angle` at most 200.
    - **Lists:** `allowed_content` and `guidance` hold 1 to 10 items with no duplicates (case-insensitive). `segment_angles` holds 0 to 20 items with no repeated (`segment`, `market`) pair. An absent `segment_angles` is empty.
    - **Integers are an `int`, never a `bool`:** `delay_days` is 0 to 365; `word_limit` is 1 to 150; a subject rule's `word_limit` is 1 to 20.
    - `word_comparison` is a `WordComparison`, `link_rule` a `LinkRule`, and `no_price` and `no_audit` are `bool`.
  - **Rules** (the AC's, then the compliance floor, decision 4). Each fails naming its field:
    1. Step 1's `delay_days` is 0.
    2. Each later Step's `delay_days` is greater than the previous Step's.
    3. `no_price` and `no_audit` are true on every Step (FR-4).
    4. Step 1's `link_rule` is `footer_only` (FR-4).
    5. Step 1 has a `subject_rule`, and `max_words` of it is at most 6 (fewer than 7 words, FR-4). A later Step has no `subject_rule`: follow-ups reply in Step 1's thread (AD-22).
    6. An `angle` that `segments.mentions_aba_512` is true for names a Market whose country is `US`. One that `segments.mentions_sra_notice` is true for names a Market whose country is `GB`. Checked in `check_sequence_references`.
    7. No `name`, `purpose`, `ask`, `allowed_content` item or `guidance` item mentions ABA Formal Opinion 512 or the SRA notice. A Market's regulatory fact goes only in a `segment_angles` entry for that Market.
  - **`check_sequence_references(sequences, segment_keys, market_countries) -> None`** (`market_countries` maps a Market code to its `country_code`). In file order, it raises `InvalidSequences` for an angle's:
    - `segment` that isn't in `segment_keys`;
    - `market` that isn't in `market_countries`;
    - a broken rule 6.
  - **`sequence_form(sequence) -> dict`** gives the file form back, with `subject_rule` only when set and `segment_angles` only when non-empty. **`sequence_from_form(form) -> SequenceDef`** validates one item, so `sequence_from_form(sequence_form(s)) == s`.
- **`nextron/domain/segments.py`.**
  - Add `mentions_aba_512(text)` (after `represents_market_fact`'s normalising: its words include both `aba` and `512`, or it contains the phrase `formal opinion`) and `mentions_sra_notice(text)` (its words include `sra`, or it contains the phrase `solicitors regulation authority`).
  - `represents_market_fact(text)` becomes `mentions_aba_512(text) or mentions_sra_notice(text)`, with behaviour unchanged.
- **Persistence.** **Migration `0010_4_1_sequences`** has down revision `0009_3_1_markets`, and `downgrade()` raises like 0009.
  - **It creates `sequences`:**
    - `id` ULID (`pk_sequences`);
    - `key` `String(32)` (`uq_sequences_key`);
    - `name` `String(100)`;
    - `version` `Integer` (`ck_sequences_version`: `version >= 1`);
    - `created_at`, `updated_at`.
  - **It creates `step_briefs`:**
    - `id` ULID (`pk_step_briefs`);
    - `sequence_id` `String(26)`, a foreign key to `sequences.id` (`fk_step_briefs_sequence_id_sequences`, no cascade);
    - `step` `Integer` (`ck_step_briefs_step`: `step BETWEEN 1 AND 6`), with `uq_step_briefs_sequence_id_step` on (`sequence_id`, `step`);
    - `delay_days` `Integer` (`ck_step_briefs_delay_days`: `delay_days >= 0`);
    - `purpose` and `ask` `String(200)`;
    - `word_limit` `Integer` (`ck_step_briefs_word_limit`: `word_limit BETWEEN 1 AND 150`);
    - `word_comparison` `String(8)`;
    - `link_rule` `String(16)`;
    - `no_price`, `no_audit` `Boolean`;
    - `allowed_content` and `segment_angles` `Text` (canonical JSON, `[]` for no angles);
    - `subject_rule` `Text`, **nullable** (canonical JSON object);
    - `created_at`, `updated_at`.

    Every other column is NOT NULL.
  - **Canonical JSON** is `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
  - **Models.** `SequenceRecord` and `StepBriefRecord` in `db/models/setup.py` match the migration.
  - **Registries.** `owners.py` maps both tables to `setup`, with every column. `pii_registry.py` classifies every column `none`.
  - **The repository** (`db/repos/setup/sequences.py`) works like `markets.py`:
    - `all_sequences()` returns every Sequence by key; `by_key(key)` returns one or `None`.
    - `insert(sequence, at) -> id` creates version 1 and one brief row per Step.
    - `update(sequence_id, sequence, version, at)` sets `name`, `version` and `updated_at`. It updates each brief row whose content differs (`updated_at` = `at`), inserts rows for new Steps and deletes rows for Steps no longer in the Sequence. Equal rows are untouched.

    It returns `StoredSequence(id, key, version, created_at, updated_at, sequence: SequenceDef)`, decoded through `sequence_from_form`. A row that doesn't decode raises `InvalidSequences`.
  - **Migration test updates.** Bump the `HEAD` constants and the chain probes (`0010_*` → `0011_*`, "revision 10" → "revision 11"). Add the new files to `test_source_tree` `FILES`.
- **Setup** (`app/setup/api.py`).
  - **`SequenceSync(created, changed, unchanged, not_in_file)`** is shaped like `MarketSync`, keyed by `key`. `SeedSync` gains `sequences`.
  - **`_sequence_inputs(sequences, segment_keys, market_countries)`** runs before any transaction:
    - `TypeError` for an item that isn't a `SequenceDef`;
    - `ValueError` for a repeated key;
    - for a non-empty input, it re-validates the file form (`validate_sequences`), then runs `check_sequence_references`.
  - **`_sync_sequences(chosen, now)`** runs in the transaction:
    - **No row:** insert, then one `events` row: entity `sequences`, `None` → `v1`, cause `seed_sync`, actor `seed`.
    - **A changed `SequenceDef`:** update to version n+1, then one `events` row from `v<n>` to `v<n+1>`.
    - **Otherwise** nothing is written.
    - A stored Sequence missing from the input is never changed or deleted.
  - **`sync_seed(segments, markets, sequences)`** (the new argument is required) runs every pre-transaction check, then Segments, Markets and Sequences in **one** transaction.
    - The references are checked against this call's Segments and Markets.
    - After the commit it logs `segments_synced`, `markets_synced` and `sequences_synced`, each with the four `*_count` fields.
  - There is no standalone `sync_sequences`.
- **Queries** (`app/queries/setup.py`). `sequences()` returns `tuple[SeededSequence, ...]`, by key. `sequence(key)` returns `SeededSequence | None`. `SeededSequence(id, key, version, definition: SequenceDef)` is frozen.
- **The command** (`main.py`). `SEQUENCES_FILE_NAME = "sequences.yaml"` and `SEQUENCES_FILE_LABEL = "seed/sequences.yaml"`.
  - `seed_sync` loads the third file through `_load_seed` (`InvalidSequences` is added to its caught errors). All three files are required.
  - It runs `Setup.sync_seed(segments, markets, sequences)`. `InvalidSequences` from it prints under the sequences label (exit 1).
  - `format_seed_sync` appends `_sync_block("sequences", SEQUENCES_FILE_LABEL, result.sequences)`.
  - Update the docstrings and help text of `main.py`, `__main__.py`, `app/setup/api.py` and `queries/setup.py`.
- **The 2.1 and 3.1 tests** (`tests/app/test_seed_sync.py`, `tests/app/test_market_sync.py`). Change only what the third file forces:
  - `seed_dir()` copies the shipped `sequences.yaml`;
  - the exact-output assertions gain the sequences block;
  - the event counts and slices gain the new event;
  - the direct `sync_seed` calls pass the shipped Sequences.
- **Tests run as `test`** with temporary data dirs. They read no keyring and make no network call. The domain tests import only the stdlib, pytest and `nextron.domain`.

**Never:**
- No YAML library, no second loader or command, and no reader change.
- No new dependency, adapter, port, route, page, setting, job type, trigger or credential.
- No `sequence_versions` table (Story 11.7), and no Campaign, Offer or Variant (4.2).
- No grounding rule or `step_due_at` (4.13 and 4.9 consume these briefs).
- No editing outside `nextron-engine/`, except this spec, `epic-4-context.md`, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The file shape.** One list of Sequences, each with a `key` (the shipped one is `default`) and its Steps in order. A Step's number is its position, so it can't disagree with the order.
   - Story 8.8's `recontact` brief can be added later as a one-Step Sequence.
   - `allowed_content` and `guidance` are lists of short items, because the YAML subset has no multi-line strings (3.1's convention).
2. **Brief texts come from the PRD FR-4 table.**
   - Each `ask` is the PRD's Ask cell, word for word.
   - `purpose` is the label before the colon of the Content cell ("New angle", "Proof", "Respectful close"). Step 1's cell has no label, so it takes Playbook §3's "Event-led cold email".
   - `allowed_content` is the rest of the Content cell, split into items.
   - The Words column becomes the limit and comparison: "Under 80" is (80, `under`), and "70 or fewer" is (70, `at_most`).
3. **The Law-PrivateAI Step 2 angle is one entry per Market.** ABA Formal Opinion 512 is the `us` entry and the SRA notice is the `uk` entry. So drafting can hand a UK prompt only the SRA angle, and a US prompt only ABA 512.
4. **A compliance floor in code,** so a seed edit can't loosen the PRD's fixed rules (as 3.1 did; these match the fields 11.7 shows read-only):
   - `no_price` and `no_audit` are always true;
   - Step 1 has day 0, `footer_only` links and a subject rule of fewer than 7 words;
   - only Step 1 has a subject rule;
   - ABA 512 is cited only on a US Market's angle and the SRA notice only on a GB Market's angle;
   - no shared brief text names either regulation;
   - every angle names a Segment and a Market of the same sync.
5. **One link rule, `footer_only`, on every Step.**
   - The PRD says this for Steps 1 and 3.
   - Steps 2 and 4 follow Playbook §2: the audit and the booking link appear only in Reply Drafts, and the footer holds the only other Settings links.
   - A looser value would need a planning change.
6. **Ranges** match Story 11.7 (1 to 6 Steps, word limits 1 to 150). The other bounds are sanity limits: delays 0 to 365 days, subject limits 1 to 20 words, and 1 to 20 Sequences.
7. **Storage.**
   - A Sequence's `version` and its `events` row cover its briefs. There is no event per brief row.
   - Briefs are typed columns keyed (`sequence_id`, `step`).
   - A changed Sequence updates, adds or deletes its brief rows. Nothing references a brief row until 11.7's `sequence_versions`, and git keeps every earlier file (3.1's R8 reasoning).
   - A stored Sequence missing from the file is left as it is.
8. **Only `sync_seed` syncs Sequences.** The angles are checked against the same sync's Segments and Markets, so a typo can't silently drop the Law angle. No standalone use case is needed.
9. **No deferral.** Nothing in this story needs Leads, Campaigns, Drafts or the gateway. Campaigns pick the `default` Sequence in 4.2.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First sync | DB at head; the shipped files | Segments and Markets as in 3.1. `sequences` gets `default` at version 1 and `step_briefs` 4 rows (delays 0, 3, 7, 14), plus 1 `events` row (`sequences`, `None` → `v1`, `seed_sync`, `seed`). The output is the segments and markets blocks, then "sequences: 1 changed, 0 unchanged" and "  default: created (version 1)". Exit 0. | N/A |
| Second sync | the same files | "sequences: 0 changed, 1 unchanged". No row, `updated_at` or `events` changes. | N/A |
| Changed | the Step 3 ask is edited | `default` goes to version 2, with one `events` row `v1` → `v2`. Only Step 3's brief row changes (`updated_at` too). | N/A |
| Grown, shrunk | a Step 5 (delay 21) is added, then removed | version 2 with 5 rows, then version 3 with 4 rows | N/A |
| Not in file | a stored `old` Sequence is left out | listed "not in seed/sequences.yaml, left as it is (version 1)"; its rows are unchanged | N/A |
| Invalid | a Step missing its `ask`; delays 0, 7, 7; Step 1 delay 3; `word_limit: 0`; `no_price: false`; Step 1 `link_rule` other; Step 1 without a subject rule; a Step 2 subject rule; an ABA 512 angle on `uk`; "SRA" in a purpose; an angle naming segment `law_privateai` or market `ca`; a 7th Step | Exit 1 with `seed/sequences.yaml: <field>: <reason>` (such as `sequences[default].steps[2].ask: ...`). The DB file's SHA-256 is unchanged. | nothing written |
| File | `sequences.yaml` missing, larger than 1 MiB or not UTF-8 | exit 1, naming `seed/sequences.yaml` | nothing written |
| Atomic | a failure while recording a Sequence, after Segments and Markets were written | "seed sync failed (…); nothing was changed"; no `segments`, `markets`, `sequences`, `step_briefs` or `events` rows | rollback |

</frozen-after-approval>

## Code Map

- **The CLI** (`nextron-engine/nextron/main.py`):
  - the docstring's "Seed sync" paragraph (227-258);
  - the constants (420-431: `SEGMENTS_FILE_*`, `MARKETS_FILE_*`);
  - `_sync_block`, `format_seed_sync`, `_load_seed` and `seed_sync` (1323-1409). Reuse them.

  `nextron/__main__.py` needs its docstring (around 19-23) and help text (around 76).
- **Setup** (`nextron-engine/nextron/app/setup/api.py`):
  - the docstring (around 77-81);
  - the constants (296-301);
  - `MarketSync` and `SeedSync` (323-339);
  - `sync_seed` (433-446) and `_log_sync` (449);
  - `_market_inputs`, `_sync_markets` and `_record_market` (558-645): the pattern to mirror.

  A unit of work allows **one** `@use_case` call (`db/uow.py`).
- **Queries.** `nextron/app/queries/setup.py`: `SeededMarket`, `markets()` and `market()` (131-154) are the pattern.
- **Persistence:**
  - `db/models/setup.py` (`MarketRecord`), `db/models/__init__.py`;
  - `db/alembic/versions/0009_3_1_markets.py` (the pattern);
  - `db/models/base.py` (`NAMING_CONVENTION`, `fk_…`): this is the first foreign key, and no `ON DELETE CASCADE` is allowed (persistence contract);
  - `db/owners.py:65`, `db/pii_registry.py:56` (`markets` entries);
  - `db/repos/setup/markets.py` (JSON columns, decoding, `FIRST_VERSION`), `db/repos/setup/__init__.py`.
- **Domain patterns.** `nextron/domain/markets.py` (`_Validator`, field paths, text rules, `InvalidMarkets`, `market_form`/`market_from_form`). Mirror its private helpers; don't import them. `nextron/domain/segments.py:213-231` (`represents_market_fact`, `_NOT_WORD`). `nextron/domain/seed_yaml.py` (`parse`).
- **Migration tests.**
  - `tests/contracts/test_persistence_contracts.py:72` `HEAD`, with the probes at 668-690;
  - `tests/unit/test_migrations.py:38`;
  - `tests/app/test_serve_process.py:263`;
  - `tests/contracts/test_source_tree.py` `FILES` (around 61, 86, 90 and 142).
- **Test helpers.**
  - `tests/app/test_seed_sync.py`: fixtures `config`, `database`, `units`; `seed_dir` (72), `sync`, `query`, `table_dump`, `event_rows`; `SEGMENTS_FIRST`/`MARKETS_FIRST`/`SUMMARY_FIRST` (136-148).
  - `tests/app/test_market_sync.py`: `seed_dir` (114), the summary constants (46-55) and the direct `sync_seed` calls (640, 845).
  - `tests/unit/test_segment_seed.py`: the shipped-file and PRD-table pattern (`PRD`, `fr1_table`, 36-43 and 520-560).
  - `tests/contracts/test_market_contracts.py`: contract (d), `ALLOWED_WRITER` (239 on), is the writer-confinement pattern.
- **Contracts that apply:**
  - `test_domain_stdlib_only.py`;
  - `test_error_contracts.py` (a unique `invalid_sequences` code);
  - `test_log_event_names.py`;
  - import-linter c7 (setup uses only its own repositories).

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/segments.py` -- `mentions_aba_512` and `mentions_sra_notice`; `represents_market_fact` composed of them -- one definition of the regulation detectors.
- [x] `nextron-engine/nextron/domain/sequences.py` -- the types, `max_words`, `validate_sequences`, `InvalidSequences`, `check_sequence_references`, `sequence_form`/`sequence_from_form` -- the one Sequence module.
- [x] `nextron-engine/seed/sequences.yaml` -- the Design Notes content, with a header comment (what it is, that `nextron seed sync` loads it, a YAML subset, a `# Step n, day d` comment per Step).
- [x] `nextron-engine/nextron/db/models/setup.py`, `db/models/__init__.py`, `db/alembic/versions/0010_4_1_sequences.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/setup/sequences.py`, `db/repos/setup/__init__.py` -- the tables, their mapping and their repository.
- [x] `nextron-engine/nextron/app/setup/api.py`, `app/queries/setup.py` -- `SequenceSync`, `SeedSync.sequences`, `_sequence_inputs`, `_sync_sequences`, `_record_sequence`, the three-argument `sync_seed`, `sequences()`, `sequence(key)`, `SeededSequence`.
- [x] `nextron-engine/nextron/main.py`, `nextron/__main__.py` -- the third file, the sequences block, the docstrings and the help text.
- [x] `nextron-engine/tests/unit/test_sequences_domain.py` -- stdlib-only:
  - every value rule and rules 1-7 failing with their exact field path;
  - unknown and missing fields;
  - the round trip;
  - `max_words`;
  - `check_sequence_references` (each failure, and a valid pass);
  - `mentions_aba_512` and `mentions_sra_notice`, positive and negative.
- [x] `nextron-engine/tests/unit/test_sequence_seed.py` -- the shipped file validates into exactly the Design Notes values, field for field. Also:
  - a hard-coded transcription of the PRD FR-4 table equals the table parsed from `prd.md` (skipped only when the PRD isn't there);
  - each Step's `delay_days` equals the Day cell, its `ask` equals the Ask cell, and its limit and comparison equal the Words cell (decision 2);
  - every `purpose` (except Step 1's) and every `allowed_content` item appears, case-insensitively, in the Content cell;
  - Step 1's ask holds "Worth a look?" and no "20-minute";
  - the two Law angles are as in the Design Notes;
  - the shipped references pass against the shipped Segments and Markets.
- [x] `nextron-engine/tests/app/test_sequence_sync.py` -- every I/O Matrix row through `main.seed_sync(env="test", seed_dir=...)` on a temporary data dir. Also:
  - `queries.setup.sequences()` and `sequence()`;
  - the `sequences_synced` record;
  - `_sequence_inputs` refusing bad input (a `TypeError`, a `ValueError`, an `InvalidSequences`) before any transaction;
  - a stored row that no longer decodes;
  - the check constraints and the foreign key.
- [x] `nextron-engine/tests/app/test_seed_sync.py`, `tests/app/test_market_sync.py` -- only the forced changes listed above.
- [x] `nextron-engine/tests/contracts/test_sequence_contracts.py` -- the sequences repository's `insert`/`update` are called only inside `_sync_sequences` of `app/setup/api.py`, with a probe proving the check catches another caller (contract (d)'s pattern).
- [x] The migration and tree tests from the Code Map, and `nextron-engine/README.md`: the "Seeds" section (the three files, the summary's three blocks) and a new "Sequences" subsection (fields, the floor, validation, reads), plus the migrations table row.

**Acceptance Criteria:**
- Given the full suite, when it runs through the suite lock and `uv run lint-imports` runs, then everything passes and every contract is kept.
- Given every model column, when the persistence contracts run, then `sequences` and `step_briefs` are fully mapped in `owners.py` (owner `setup`) and `pii_registry.py` (`none`), and the migration matches the models.
- Given Story 2.1's tests of `represents_market_fact`, when they run unchanged, then they pass.

## Implementation Notes

**What was built**
- **`nextron/domain/segments.py`.** Adds `mentions_aba_512` and `mentions_sra_notice`, both on a shared private `_normal_words`. `represents_market_fact` is now their `or`, with 2.1's tests unchanged.
- **`nextron/domain/sequences.py`.**
  - The types: `WordComparison`, a one-value `LinkRule`, `SubjectRule`, `SegmentAngle`, `StepBrief` and `SequenceDef`.
  - `max_words`, and `InvalidSequences` (code `invalid_sequences`).
  - `validate_sequences`: a `_Validator` runs the fields in file order, then the missing fields, then the rules, using the spec's paths (a Sequence by key or index, a Step by its 1-based number).
  - `check_sequence_references` (segment, then market, then rule 6, naming `.segment`, `.market` or `.angle`).
  - `sequence_form`/`sequence_from_form`.
  - Its own text, list and int helpers, mirroring `markets.py`.
- **`seed/sequences.yaml`.** The Design Notes content, with a header comment and a `# Step n, day d` comment per Step.
- **The `sequences` and `step_briefs` tables.**
  - Migration `0010_4_1_sequences`: down `0009_3_1_markets`, `downgrade()` raises. It adds the first foreign key, `fk_step_briefs_sequence_id_sequences`, with no cascade.
  - Models `SequenceRecord` and `StepBriefRecord`.
  - `owners.py` maps both to `setup` with every column; `pii_registry.py` classifies every column `none`.
- **`db/repos/setup/sequences.py`.**
  - It stores each brief's columns from `sequence_form`: canonical JSON for `allowed_content`, `segment_angles` (`[]`) and `subject_rule` (NULL when unset).
  - It decodes a Sequence's rows in Step order back through `sequence_from_form`. `all_sequences()` reads every brief in one query.
  - `update` compares each existing row's content columns (the Step number, the scalars and the three JSON texts). It rewrites only rows that differ, inserts new Steps and deletes Steps that are gone.
- **`app/setup/api.py`.**
  - `SequenceSync`, and `SeedSync.sequences`.
  - `_sequence_inputs`: the type, repeat, re-validation and reference checks, with the references taken from this call's chosen Segments and Markets.
  - `_sync_sequences` and `_record_sequence`.
  - `sync_seed(segments, markets, sequences)`: one transaction, then logs `segments_synced`, `markets_synced` and `sequences_synced`.
- **`app/queries/setup.py`.** `SeededSequence`, `sequences()` and `sequence(key)`.
- **`main.py`.** `SEQUENCES_FILE_NAME`/`SEQUENCES_FILE_LABEL`; the third `_load_seed` (catching `InvalidSequences`); `InvalidSequences` from the use case printed under the sequences label; and the third `_sync_block`.
- **`__main__.py`.** The docstring and the `seed sync` help. The subparser also gets `description=` so `seed sync --help` names the three files.
- **README.** "Seeds" now covers three files and three blocks. A new "Sequences" subsection covers the fields, the floor, validation and reads, and the migrations table gains a row for 0010.

**Choices made inside the spec's latitude**
- **Rule 4 can't fire through a file today.** `LinkRule` has one value, so the enum check refuses any other Step 1 link rule first. The rule stays in `_check_step_rules` for a later value, and a test calls it directly.
- **Order of a Step's checks.** Rule 7 checks `purpose`, each `allowed_content` item, `ask`, then each `guidance` item. The Sequence `name` is checked after its Steps.
- **A row set whose Steps aren't numbered 1 to n** (say, a hand-deleted middle row) raises `InvalidSequences` on `sequences[<key>].steps` when it is decoded.
- **Messages.** A missing field reads `missing`. Rule 1 reads "must be 0: Step 1 is sent when the Sequence starts", and rule 2 "must be more than Step n's delay_days (d)".

**Review patches (pass 1)**
- **P1.** `_check_allows_a_word` (`MIN_WORDS = 1`, `ALLOWS_NO_WORD_REASON`) refuses a limit whose `max_words` is below 1, so `1` with `under` fails. It runs right after a subject rule's fields, on `…steps[1].subject_rule.word_limit`. On a Step it runs after the fields and missing fields and before rules 1–7, on `…steps[n].word_limit`. The rule-5 test case and two new domain tests cover it.
- **P2, P3, P5** are tests only, in `tests/app/test_sequence_sync.py`:
  - a removed middle Step moves the later Steps up;
  - `seed sync --help` names `seed/sequences.yaml`;
  - `cli.main(["seed", "sync"])` stores `default` end to end;
  - an edited subject guidance item and an edited `allowed_content` item rewrite only their rows.

  The misnamed angle test is renamed `test_an_added_angle_changes_its_column`. The syntax-error test now computes its line number from the file.
- **P4.** The seed header now states decision 2's sources: each ask word for word, the purposes and allowed content from the Content cell, and Step 1's purpose from Playbook §3.

**Final verification (after the pass-1 patches)**
- The spec's Verification command: 1310 passed, 1 skipped.
- `NEXTRON_ENV=test uv run nextron seed sync --help` names the three files.
- The full suite through `suite_lock.py` (`track-C`): 5465 passed and 2 skipped, with no failure. `uv run lint-imports`: 29 kept, 0 broken.

**Tests changed beyond the new files** (each forced by the third file or the new revision)
- `test_seed_sync.py` and `test_market_sync.py`:
  - `seed_dir()` copies `sequences.yaml`;
  - the exact outputs gain the sequences block;
  - the not-in-file event count goes from +4 to +5;
  - the direct `sync_seed` calls pass the shipped Sequences;
  - the log-name list gains `sequences_synced`.
- The `HEAD` constants and chain probes in `test_persistence_contracts.py`, `test_migrations.py` and `test_serve_process.py`, plus the four new files in `test_source_tree` `FILES`.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers reviewed the diff since the baseline (`nextron-engine/` only):
- blind hunter (BH): 12 findings;
- edge-case hunter (EC): 5;
- verification gap (VG): 1.

That is 18 raw findings, which deduplicate into 14 groups: 5 patched (P1–P5), 0 deferred and 9 rejected (R1–R9).

| # | Finding(s) | Verdict | Evidence | Route |
|---|---|---|---|---|
| P1 | BH2, EC1: `word_limit: 1` with `under` (a Step or the Step 1 subject rule) allows 0 words, and validation accepts it | low | **Real:** `max_words(1, under)` is 0, and `_word_limit`/`_subject_word_limit` check only the raw 1-to-150 and 1-to-20 ranges. `test_rule_5_…` even lists `(1, "under", True)`. The AC refuses "a non-positive word limit", and an effective limit of 0 is one. The fix is one check. | patch: refuse a limit whose `max_words` is below 1 on the limit's own field; update the rule-5 case |
| P2 | BH7: no test removes or inserts a middle Step (rows are matched by Step number) | low | **Real gap:** `test_a_step_5_is_added_then_removed` only touches the last Step. The code looks right: an update never changes a row's `step`, so there is no unique clash. The fix is a test. | patch: a test that removes Step 2 and checks the shifted rows, `updated_at`, the row count and the decoded Sequence |
| P3 | BH8: nothing runs `nextron.__main__` for Sequences (help text and end-to-end sync) | low | **Real gap:** Markets have `test_cli_help_names_both_seed_files` and `test_cli_seed_sync_loads_the_markets_end_to_end`; Sequences have no equivalent, and the new `description=` is untested. | patch: a help test naming `seed/sequences.yaml` and an end-to-end `cli.main(["seed", "sync"])` that stores `default` |
| P4 | BH11: the seed header says the briefs are "taken word for word" from the FR-4 table | low | **Real:** only the asks are word for word. `purpose` and `allowed_content` follow decision 2's split, and Step 1's purpose comes from the Playbook. Cosmetic; the fix is a direct correction. | patch: reword the header comment |
| P5 | VG1: no sync test edits only `subject_rule` or only `allowed_content`, so a comparison that skipped a JSON column would go unnoticed | low (gap, pre-verified) | **Filed evidence:** with `_JSON_COLUMNS` cut to `segment_angles`, all 451 tests passed while the stored brief stayed stale. Also, `test_an_added_subject_rule_and_angle_change_their_columns` touches no subject rule. | patch: rename that test to what it does, and add a test editing a Step 1 guidance item and an `allowed_content` item (new canonical JSON, `updated_at` on those rows only, decoded definition, then "0 changed") |
| R1 | BH1, EC4: one stored Sequence that no longer decodes blocks every seed sync, the file can't repair it, and the message names `seed/sequences.yaml` | low | **Frozen spec behaviour:** "A row that doesn't decode raises `InvalidSequences`". 3.1 rejected the same point for Markets (R4). It needs a hand edit of the database or a rule tightened in code without a migration. It fails closed (exit 1, nothing written). | rejected: it would change the approved spec |
| R2 | BH3, EC2: the regulation detectors miss "Op. 512" without "ABA", "American Bar Association Opinion 512" and "S.R.A."; and any "formal opinion" counts as ABA 512 | low | **Spec definition:** the frozen Boundaries define `mentions_aba_512` and `mentions_sra_notice` exactly, and 2.1's `represents_market_fact` must keep its behaviour. Getting past them takes a deliberate Operator seed edit. A false "formal opinion" match fails closed. The real guards on Drafts are 4.3's `allowed_facts` and 4.13's grounding check. | rejected: it would change the approved spec |
| R3 | BH4: the `no_price` and `no_audit` flags are forced true, but brief texts aren't scanned for price or audit wording | low | The flags are the brief's instruction to drafting. Drafts are checked by 4.13's `price_or_audit_in_step`, which fails closed. A text scan is a new rule the spec doesn't define, and the README's "can't loosen" refers to the listed floor. | rejected: it would change the approved spec |
| R4 | BH5, EC3: rule 6 and the reference checks don't re-run on a stored Sequence left out of the file | low | It needs two unlikely edits: a Sequence removed from the file, then a Market code's country changed. A Segment removed from the file stays stored, so the reference stays real. The spec checks this sync's input and leaves left-out Sequences "as they are"; the fix adds new checks. | rejected |
| R5 | BH6: the reference check runs after the database check, so on a fresh machine "the database doesn't exist yet" comes before an angle typo | low | **Spec placement:** the frozen Boundaries put `check_sequence_references` in `_sequence_inputs`, and the module docstring lists reference errors under step 4. Both errors still surface, one after the other, and nothing is written. | rejected: it would change the approved spec |
| R6 | BH9: `allowed_content` holds prohibitions (`Never "just following up"`, "No guilt or urgency") | low | **Decision 2** maps the PRD's Content cell, prohibitions included, into `allowed_content`. A separate list would change the file shape. Note for 4.15 and 4.18: render the items as binding content rules. | rejected: it would change the approved spec |
| R7 | BH10: the database checks don't cover the whole floor (`delay_days <= 365`, the flags, the enums, a subject rule only on Step 1) | low | **The spec lists the check constraints exactly.** The repository is the only writer, and it writes validated Sequences. | rejected: it would change the approved spec |
| R8 | BH12: bare `assert isinstance` in `_brief_values`, `_segment_key` and `_Validator._key` | false | Each assert only narrows a type the line before already guarantees (`_is_key` true means a `str`; `sequence_form` of a validated `SequenceDef` returns lists). Removing them under `-O` changes no outcome. `markets.py:684` and `segments.py:511` use the same idiom. | rejected |
| R9 | EC5: the one-writer contract misses a writer reached through a parent alias (`from nextron.db.repos import setup as r; r.sequences.update(...)`) | low | **Real but unlikely:** 3.1's contract (d) has the same limit. Only `app.setup` may import the setup repositories (import-linter c7). The fix makes the AST walk more complex. | rejected |

## Design Notes

**The seed file** (`seed/sequences.yaml`, after the header comment; the reader parses this shape as given):

```yaml
sequences:
  - key: default
    name: Default four-Step Sequence
    steps:
      # Step 1, day 0
      - delay_days: 0
        purpose: Event-led cold email
        word_limit: 80
        word_comparison: under
        allowed_content:
          - One observed Signal
          - One sentence on the likely problem
          - One credibility line from Approved Company Facts
        ask: 'Relevance only, such as "Worth a look?" No meeting request or price'
        link_rule: footer_only
        no_price: true
        no_audit: true
        subject_rule:
          word_limit: 7
          word_comparison: under
          guidance:
            - About the prospect's world
            - Never misleading
      # Step 2, day 3
      - delay_days: 3
        purpose: New angle
        word_limit: 70
        word_comparison: at_most
        allowed_content:
          - One short Segment insight from Approved Company Facts
        ask: The relevance question, reworded
        link_rule: footer_only
        no_price: true
        no_audit: true
        segment_angles:
          - segment: law_private_ai
            market: us
            angle: What ABA Formal Opinion 512 means for their intake or document work
          - segment: law_private_ai
            market: uk
            angle: What the SRA notice means for their intake or document work
      # Step 3, day 7
      - delay_days: 7
        purpose: Proof
        word_limit: 70
        word_comparison: at_most
        allowed_content:
          - One relevant case study or regulatory note from Approved Company Facts
          - Never "just following up"
        ask: Offer to send more detail by reply (no link or attachment)
        link_rule: footer_only
        no_price: true
        no_audit: true
      # Step 4, day 14
      - delay_days: 14
        purpose: Respectful close
        word_limit: 50
        word_comparison: at_most
        allowed_content:
          - The timing may be wrong
          - The door stays open
          - No guilt or urgency
        ask: '"Should I close the loop?" or a check-back date of their choosing'
        link_rule: footer_only
        no_price: true
        no_audit: true
```

**The PRD FR-4 table** (`prd.md`, under `#### FR-4: Sequence configuration`; header `| Step | Day | Content | Ask | Words |`) is the source for decision 2. Rule 7 permits "regulatory note" in Step 3: it names no regulation.

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_sequences_domain.py tests/unit/test_sequence_seed.py tests/app/test_sequence_sync.py tests/app/test_seed_sync.py tests/app/test_market_sync.py tests/unit/test_segment_seed.py tests/contracts` -- expected: all pass.
- the full suite through `suite_lock.py` (`track-C`) -- expected: exit 0, all pass, every import contract kept.
- `NEXTRON_ENV=test uv run nextron seed sync --help` -- expected: usage naming the three files, exit 0.
