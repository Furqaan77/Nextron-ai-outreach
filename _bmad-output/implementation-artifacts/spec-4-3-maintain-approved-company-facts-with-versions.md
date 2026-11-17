---
title: 'Story 4.3: Maintain Approved Company Facts with versions'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: 'd404c9f72282947b19e768b93a6e2c8fa5c84e36'
review_loop_iteration: 0
story_key: '4-3-maintain-approved-company-facts-with-versions'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Drafts may claim only Approved Company Facts (FR-72, NFR-24, AD-7), and the engine has none. Grounding (4.13, 4.14) and drafting (4.15) need versioned facts, each allowed only in its Segments and Markets, so ABA Formal Opinion 512 never reaches a UK Draft and the SRA notice never reaches a US Draft. AD-12 seeds them from Playbook §6 through `nextron seed sync`, and the Operator maintains them in a dashboard panel.

Requirements: FR-72, AD-3, AD-7, AD-12, AD-13, NFR-24, UX-DR3, UX-DR4, UX-DR24.

**Approach:**
- A stdlib-only `nextron/domain/company_facts.py` validates facts.
- A new `company_facts` table (owner `playbook`, migration `0011_4_3_company_facts`) holds them as append-only versions keyed (`fact_id`, `version`). Database triggers forbid deleting a row or changing anything but a NULL `retracted_at`.
- `seed/company_facts.yaml` holds the Playbook §6 facts.
- **One transaction for the whole seed sync.** `nextron seed sync` loads it as a fourth file. Setup and playbook sit in the same app-DAG layer, so neither may call the other. So the seed sync use case moves to `ops`, which runs setup's `seed_sync` command and playbook's `seed_facts` command in one transaction (decision 1).
- An Approved Company Facts page (`/facts`, linked from Settings) lists each fact's current version, with its earlier versions expandable. It adds, edits (as a new version) and retracts facts by CSRF-checked POSTs.
- `queries.playbook.allowed_facts(segment_id, market_id)` gives drafting the facts allowed for a Segment and Market.

The acceptance criteria are Story 4.3 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **`nextron/domain/company_facts.py`** (stdlib and `nextron.domain` only; frozen, slotted dataclasses; every list a tuple).
  - **Types:**
    - `FactType` (`StrEnum`): `company`, `case_study`, `offer`, `regulation`, `statistic`.
    - `FactCreator` (`StrEnum`): `seed`, `operator`.
    - `FactContent(type, wording, source_url, segments, markets, metric, metric_confirmed_at, scope_note, last_checked_on)`:
      - `segments` and `markets` are sorted tuples of unique references: keys and codes in the file, ULIDs in the database;
      - `metric`, `metric_confirmed_at` (a `date`) and `scope_note` may be `None`;
      - `last_checked_on` is a `date`.
    - `SeedFact(fact_id, content)` is one file item.
  - **The file shape.** The top level is exactly `company_facts: [<fact>, ...]`, with 1 to 200 items.
    - Required keys: `fact_id`, `type`, `wording`, `source_url`, `segments`, `markets` and `last_checked_on`.
    - Optional keys: `metric`, `metric_confirmed_at` and `scope_note`.
    - Unknown and missing keys are refused. Dates are quoted `'YYYY-MM-DD'` strings (the reader refuses plain dates).
  - **`validate_company_facts(data) -> tuple[SeedFact, ...]`**, in file order. The first problem raises `InvalidCompanyFacts(field, reason)`:
    - a `DomainError` with code `invalid_company_facts` and the message `"<field>: <reason>"`;
    - `field` is `company_facts[<fact_id, or the 0-based index when invalid or repeated>].<key>[<i>]`;
    - the reason never repeats a value longer than 40 characters.
  - **Value rules** (shared by the file, the Operator form and decoding a stored row):
    - `fact_id` fullmatches `[a-z][a-z0-9_]{0,30}` and is unique in the file.
    - `wording` is a text of at most 500 characters, under 2.1's text rules: a non-blank `str`, no leading or trailing whitespace, and no `Cc`, `Cf`, `Zl` or `Zp` character.
    - `scope_note` (optional) is a text of at most 500 characters.
    - `metric` (optional) is a text of at most 200 characters.
    - `type` is a `FactType`.
    - `source_url` is at most 500 characters, with no whitespace or `Cc`/`Cf`/`Zs`/`Zl`/`Zp` character. `urlsplit` gives an `http` or `https` scheme, a hostname, a port that parses (if any), and no user name or password, so `nextron.web.templating.safe_link` always renders it as a link. The reason is "Use an http or https link".
    - `segments` and `markets` each hold 1 to 50 items, with no duplicates. In the file they are a Segment `key` and a Market `code` (`[a-z][a-z0-9_]{0,30}` and `[a-z][a-z0-9_]{0,15}`).
    - Dates are a real calendar date written `YYYY-MM-DD`.
  - **Rules** (each fails naming its field):
    1. A `regulation` fact has exactly one Market: "A regulatory fact applies to one Market" (on `markets`).
    2. A `metric` without `metric_confirmed_at`: "Add a metric only once the client confirms it" (on `metric`). A `metric_confirmed_at` without a metric: "Add the confirmation date only with the client's metric" (on `metric_confirmed_at`).
    3. **Compliance floor (decision 6).**
       - If the wording or the scope note satisfies `segments.mentions_aba_512`, every allowed Market's country is `US`: "ABA Formal Opinion 512 applies only to a US Market".
       - If either satisfies `segments.mentions_sra_notice`, every allowed Market's country is `GB`: "The SRA notice applies only to a UK Market".
       - Both are checked on `markets`, against a given `{reference: country_code}` map, in `check_fact_references`.
    4. Every reference is known: a Segment reference in the given set ("names no stored Segment" / "isn't a Segment of this sync"), and a Market reference in the given country map.
    5. `metric_confirmed_at` and `last_checked_on` are no later than a given `latest` date: "can't be in the future". Callers pass the UTC date of `now` plus one day, so any timezone's today passes.
  - **Functions:**
    - `content_problems(content) -> list[tuple[str, str]]`: the value rules and rules 1–2, as (field, reason) in field order.
    - `check_fact_references(content, segment_refs, market_countries) -> list[tuple[str, str]]`: rules 3–4.
    - `date_problems(content, latest)`: rule 5.
    - `fact_form(fact_id, content) -> dict` gives the file form back (optional keys only when set). `fact_from_form(form) -> SeedFact` validates one item, so `fact_from_form(fact_form(...))` round-trips.
    - `check_seed_facts(facts, segment_keys, market_countries, latest) -> None` raises `InvalidCompanyFacts` for the first broken rule 3–5, in file order.
  - **The Operator path.**
    - `FactFields` holds the form's raw strings, with `segments` and `markets` as tuples of strings.
    - `fact_from_fields(fields, *, segment_refs, market_countries, latest) -> FactContent` collects every problem and raises `InvalidFact(problems: Mapping[str, str])` (code `invalid_fact`; one reason per field, the first found).
    - Before the rules it strips each field, collapses each run of whitespace in `wording`, `scope_note` and `metric` to one space, and reads a blank `metric`, `metric_confirmed_at` or `scope_note` as `None`.
    - A blank required field reads "Enter a value", and a blank `type` reads "Choose a type".
- **Persistence.** **Migration `0011_4_3_company_facts`** has down revision `0010_4_1_sequences`, and `downgrade()` raises like 0010. It creates `company_facts`:

  | Column | Type | Notes |
  |---|---|---|
  | `id` | ULID | `pk_company_facts` |
  | `fact_id` | `String(31)` | `uq_company_facts_fact_id_version` on (`fact_id`, `version`) |
  | `version` | `Integer` | `ck_company_facts_version`: `version >= 1` |
  | `type` | `String(16)` | `ck_company_facts_type`: one of the five |
  | `wording`, `source_url` | `Text` | |
  | `allowed_segment_ids`, `allowed_market_ids` | `Text` | canonical JSON sorted lists of ULIDs (4.1's canonical JSON) |
  | `metric` | `String(200)` | **nullable** |
  | `metric_confirmed_at` | `Date` | **nullable**; the client's confirmation date (decision 12) |
  | `scope_note` | `Text` | **nullable** |
  | `last_checked_on` | `Date` | |
  | `created_by` | `String(8)` | `ck_company_facts_created_by`: `seed` or `operator` |
  | `created_at` | `UtcDateTime` | |
  | `retracted_at` | `UtcDateTime` | **nullable** |

  Every other column is NOT NULL.
  - **Triggers** (the `events` precedent, migration 0001):
    - `trg_company_facts_no_delete` aborts any `DELETE`.
    - `trg_company_facts_no_id_reuse` aborts an `INSERT` whose `id`, or whose (`fact_id`, `version`), already exists. This stops `INSERT OR REPLACE`.
    - `trg_company_facts_immutable` aborts an `UPDATE` that changes any column but `retracted_at`, or changes `retracted_at` once it isn't NULL.
  - **Model and registries.** `CompanyFactRecord` lives in a new `db/models/playbook.py`, imported in `db/models/__init__.py`, and matches the migration. `owners.py` maps every column to `playbook`. `pii_registry.py` classifies every column `none`.
  - **The repository** (`db/repos/playbook/company_facts.py`) is the only code that touches the table. It works like `markets.py`:
    - `all_versions()` returns every row, by `fact_id` then `version`; `versions_of(fact_id)` returns one fact's rows by `version`.
    - `insert(fact_id, version, content, created_by, at) -> id`.
    - `retract(fact_id, at) -> tuple[str, ...]` sets `retracted_at` on every row of the fact where it is NULL, and returns their ids.
    - There is no other write. Rows decode to `StoredFact(id, fact_id, version, content, created_by, created_at, retracted_at)` through the value rules and rules 1–2. A row that doesn't decode raises `InvalidCompanyFacts`.
  - **Migration test updates.** Bump the `HEAD` constants and the chain probes (`0011_*` → `0012_*`, "revision 11" → "revision 12"). Add the new files to `test_source_tree` `FILES`. Add a trigger test like `test_the_events_triggers_exist_at_head`.
- **Playbook** (`app/playbook/api.py`, class `CompanyFacts(clock)`).
  - **Terms.** A fact's *latest* row is its highest version. Its *current* version is the highest unretracted row (the AC), or none. **Retracting a fact retracts every version** (decision 4).
  - **`check_seed_facts(facts, segment_keys, market_countries, latest) -> CheckedFacts`** (module function) runs before any transaction:
    - `TypeError` for an item that isn't a `SeedFact`;
    - `ValueError` for a repeated `fact_id`;
    - for a non-empty input, it re-validates the file form, then runs the domain `check_seed_facts` against this sync's Segments and Markets.
  - **`@command seed_facts(checked, now) -> CompanyFactSync(created, changed, unchanged, conflicts, not_in_file)`** joins the caller's transaction. It maps keys and codes to ids through `queries.setup.segments()` and `markets()`, which see this transaction's rows. Then, per file fact in file order (decision 5):
    - **No row:** insert version 1 (`seed`). Created `(fact_id, 1)`.
    - **The latest row's content equals the file's:** unchanged, even when that row is retracted.
    - **The latest row is `seed` and unretracted:** insert version n+1 (`seed`). Changed `(fact_id, n, n+1)`.
    - **Otherwise** (the latest row is the Operator's, or it is retracted): unchanged when the file equals the highest `seed` row's content. Else it's a conflict `(fact_id, n)`, and nothing is written.
    - **`not_in_file`** is `(fact_id, latest version)` for every stored fact the file leaves out, by `fact_id`.
    - **Events.** Each insert writes one `events` row: entity `company_facts`, `entity_id` the new row's id, `None` → `v1` or `v<n>` → `v<n+1>`, cause `seed_sync`, actor `seed`.
  - **`@use_case add(fact_id, fields) -> FactSaved`:**
    - It reads the clock once.
    - In one transaction it validates through `fact_from_fields`, against `queries.setup` Segment ids and Market countries, with `latest` the UTC date of now plus one day.
    - `fact_id` must fit the pattern ("Use lowercase letters, digits and underscores, starting with a letter") and have no row ("This fact id is already used"). Both problems go in the same `InvalidFact`.
    - It inserts version 1 (`operator`) with an event `None` → `v1`, cause `operator_add`, actor `operator`.
  - **`@use_case edit(fact_id, based_on, fields) -> FactSaved`:**
    - It validates like `add`.
    - An unknown `fact_id` gives `InvalidFact({"form": "No fact has this id"})`. A latest version other than `based_on` gives `InvalidFact({"form": "This fact changed since the page loaded; reload the page and edit again"})`.
    - **A no-op:** when the latest row is unretracted and its content equals the new content, it writes nothing and returns `FactSaved(..., changed=False)`.
    - **Otherwise** it inserts version n+1 (`operator`), even for a retracted fact, which restores it, with an event `v<n>` → `v<n+1>`, cause `operator_edit`, actor `operator`.
  - **`@use_case retract(fact_id) -> FactRetracted`:**
    - An unknown fact gives `InvalidFact({"form": ...})`.
    - It sets `retracted_at` on every unretracted version, with one event on the current row: `v<n>` → `retracted`, cause `operator_retract`, actor `operator`.
    - A fact with no current version writes nothing (`retracted=False`).
  - **Logs after the commit:** `company_facts_synced` (from `log_fact_sync(result)`, with the four `*_count` fields plus `conflict_count`), `company_fact_saved` (`fact_id`, `version`, `changed`) and `company_fact_retracted` (`fact_id`).
- **Setup** (`app/setup/api.py`).
  - `Setup.sync_seed` (the use case) is replaced by:
    - `check_seed(segments, markets, sequences) -> CheckedSeed`: the existing three `_*_inputs`, unchanged. It exposes the Segment keys and `{code: country_code}` of this sync.
    - `@command Setup.seed_sync(checked, now) -> SetupSeedSync(segments, markets, sequences)`: the existing three in-transaction syncs, in the same order, unchanged.
    - `log_seed_sync(result)`: the three existing log records.
  - `sync_segments` and `sync_markets` stay as they are.
- **Ops** (`app/ops/api.py`).
  - **`SeedSync(segments, markets, sequences, company_facts)`** now lives here.
  - **`Seeds(clock)`** with **`@use_case sync_seed(segments, markets, sequences, company_facts) -> SeedSync`**:
    1. It reads the clock once.
    2. It runs `check_seed`, then `check_seed_facts` (with `latest` the UTC date of now plus one day).
    3. In **one** transaction it runs `Setup(clock).seed_sync(...)`, then `CompanyFacts(clock).seed_facts(...)`.
    4. After the commit it runs `log_seed_sync`, then `log_fact_sync`, and returns `SeedSync`.
- **Queries** (`app/queries/playbook.py`; imports only `nextron.domain` and its repositories).
  - `company_facts() -> tuple[FactHistory, ...]`, by `fact_id`. `FactHistory(fact_id, current: FactVersion | None, versions: tuple[FactVersion, ...])` lists the versions newest first.
  - `fact(fact_id) -> FactHistory | None`.
  - `allowed_facts(segment_id, market_id) -> tuple[FactVersion, ...]`: every fact's current version whose allowed lists hold both ids, by `fact_id`.
  - `FactVersion(id, fact_id, version, content, created_by, created_at, retracted_at)` is frozen.
- **The command** (`main.py`).
  - `COMPANY_FACTS_FILE_NAME = "company_facts.yaml"` and `COMPANY_FACTS_FILE_LABEL = "seed/company_facts.yaml"`.
  - `seed_sync` loads the fourth file through `_load_seed`, which also catches `InvalidCompanyFacts`. All four files are required.
  - It runs `Seeds(clock).sync_seed(...)`. Its `InvalidCompanyFacts` prints under the facts label (exit 1).
  - `format_seed_sync` appends the facts block:
    - `company_facts: <n> changed, <m> unchanged`, plus `, <c> conflict(s)` when c > 0;
    - then, per fact: created and changed lines, as for the other blocks;
    - `  <fact_id>: conflict, left as the Operator set it (version <n>); seed/company_facts.yaml differs`;
    - not-in-file lines.
  - `build_app` passes `facts=CompanyFacts(clock)` to `create_app`. Update the docstrings and help text of `main.py`, `__main__.py`, `app/setup/api.py`, `app/ops/api.py` and both queries modules.
- **The page** (`web/facts_page.py`, `templates/facts.html` and `templates/facts_panel.html`). Every route is `@personal_data(False)`: facts are claims about Nextron, and the PII class is `none`.
  - **`GET /facts`** (`company_facts_page`) renders the panel in a read-only unit. Per fact, by `fact_id`:
    - the current version's wording (`|plain`), type, source as `safe_link`, Segment names, Market names, metric with its confirmation date (or "none"), last-checked date, version, and who made it and when;
    - an Edit form inside `<details>`, prefilled, with a hidden `fact_id` and `based_on`;
    - a Retract form;
    - `<details>` "Earlier versions (k)", each with the same fields, plus "retracted <date>" where set.

    A fact with no current version shows "Retracted" with its latest version, and keeps its Edit form. An "Add a fact" form sits above the list, with `last_checked_on` defaulting to today's UTC date. Segments and Markets are checkboxes valued with their ids.
  - **The POSTs:** `POST /facts/add` (`company_fact_add`), `POST /facts/edit` (`company_fact_edit`) and `POST /facts/retract` (`company_fact_retract`).
    - Each takes its fields through `form_fields`. A field outside its form's set, a repeated single field, a non-ULID `segment` or `market` value, or a `based_on` that isn't a positive int raises `MalformedForm` (400).
    - Each runs its use case in `units.open()`, then renders the panel partial in `units.open(read_only=True)`. The forms use `hx-post`, `hx-target="#facts-panel"` and `hx-swap="outerHTML"`.
    - **On success** the panel shows a `role="status"` notice: "Saved <fact_id> as version <n>.", "Nothing changed, so no new version was saved.", "Retracted <fact_id>; Drafts can no longer cite it." or "<fact_id> was already retracted.".
    - **On `InvalidFact`** it answers 200: that form re-rendered open with the submitted values, each problem beside its field (`aria-invalid="true"`), and a `role="alert"` notice that nothing was saved. Form-level problems show in the notice.
  - The Settings page gains a link: "Approved Company Facts are on the <a href="/facts">Approved Company Facts page</a>." The navigation is unchanged (UX-DR1).
- **The earlier seed tests** (`tests/app/test_seed_sync.py`, `test_market_sync.py`, `test_sequence_sync.py`). Change only what the fourth file and the moved use case force:
  - `seed_dir()` copies the shipped `company_facts.yaml`;
  - the exact outputs gain the facts block, and the event counts gain its events;
  - the direct `Setup(...).sync_seed(a, b, c)` calls become `Seeds(clock).sync_seed(a, b, c, shipped_facts)`, with `SeedSync` imported from `nextron.app.ops.api`;
  - the log-name lists gain `company_facts_synced`.

  The 3.1 market contract's `FORBIDDEN_NAMES` gains `seed_sync`.
- **Tests run as `test`** with temporary data dirs. They read no keyring and make no network call. The domain tests import only the stdlib, pytest and `nextron.domain`.

**Never:**
- No YAML library, no second loader or command, and no reader change.
- No new dependency, adapter, port, setting, job type, trigger or credential, and no navigation section.
- No write to a `playbook` table from `setup`, and no import between `setup` and `playbook` (c6).
- No Draft, grounding rule, staleness check or prompt (4.12–4.15 consume `allowed_facts`). No `playbook_snapshots` (4.4).
- No editing outside `nextron-engine/`, except this spec, `epic-4-context.md`, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **One seed-sync transaction through `ops`.**
   - Under AD-3 and c6, `setup` and `playbook` share the foundation layer, so neither may call the other. Passing one service into the other would only hide that call.
   - AD-3's pattern is one use case in a higher box that calls owner commands in one transaction. The owner map already lists `seed_sync` as a setup command others call, and `ops` as entry points only. So `ops.Seeds.sync_seed` is the use case, and `setup.seed_sync` and `playbook.seed_facts` are commands.
   - The playbook's command list gains `seed_facts`: an owner-map addition for Mac to note.
   - "Nothing was changed" on any failure still holds.
2. **`fact_id` is a short key.** The seed file gives it, and the Add form asks for it. The row's ULID `id` is the primary key, and (`fact_id`, `version`) is unique (the spine's ID convention). Revisions will cite readable (`fact_id`, `version`) pairs.
3. **Allowed Segments and Markets are explicit lists** of `segments.id` and `markets.id` ULIDs, with at least one of each. There is no "all": a new Segment or Market gets no facts until they are ticked or seeded (fail closed).
4. **A retraction retracts the whole fact,** every unretracted version, so an older wording never silently becomes current. Editing a retracted fact saves a new current version, which restores it. The seed never restores one.
5. **Seed versus Operator.** The seed writes a new version only while the latest version is its own and unretracted. Once the Operator has edited or retracted a fact, a seed entry that changed since its last seed version is reported as a conflict and not written. An unchanged one stays quiet.
6. **A compliance floor in code,** like 3.1 and 4.1. On every write, by any path:
   - a fact whose wording or scope note names ABA 512 is allowed only in US-country Markets, and one naming the SRA notice only in GB-country Markets;
   - a regulatory fact has exactly one Market;
   - a metric needs its confirmation date;
   - dates can't be in the future.

   The database adds immutability triggers (AD-7).
7. **The seed content** (Design Notes):
   - **Sources.** The nextronai.com facts and the Offer cite `https://nextronai.com/`. ABA 512 cites the ABA's PDF of the opinion. The SRA notice cites `https://www.sra.org.uk/solicitors/guidance/misuse-ai/`, and its wording and scope come from `docs/research/icp-segments-2026.md`.
   - **Mac should check before the first send:** both regulation entries against their sources, and the Offer's source URL.
   - **The Offer names no price.** Prices are still being validated (FR-69), and no Step may name one.
   - **Allowed lists.** Every non-regulation fact is allowed for both Segments and both Markets. ABA 512 is allowed only for `law_private_ai` and `us`, and the SRA notice only for `law_private_ai` and `uk`.
   - `last_checked_on` is 2026-11-14, the date of Playbook v0.2.0 and the research.
8. **The Legal AI and Wish Wheels guardrails** are in each fact's scope note: "The client was a legal-tech venture, not a law firm." and "An automotive retail and services business, not a distributor; don't present it as distribution experience." Neither wording says "for a law firm" or "distribut".
9. **Edits.** An edit that changes nothing saves nothing. An edit made against a stale page (a different latest version) is refused.
10. **The panel.** It is at `/facts`, linked from Settings, because UX-DR1 fixes the navigation. It is `@personal_data(False)`, and its forms need no JavaScript (`<details>`).
11. **No deferral.** `allowed_facts` is built and tested directly. Its caller, drafting, comes in 4.15.
12. **`metric_confirmed_at` is a date,** the client's confirmation date, under the AC's column name.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First sync | DB at head; the four shipped files | The other blocks as in 4.1. `company_facts` gets 8 rows at version 1 (`seed`) and 8 `events` rows (`company_facts`, `None` → `v1`, `seed_sync`, `seed`). The output ends "company_facts: 8 changed, 0 unchanged" with 8 created lines. Exit 0. | N/A |
| Second sync | the same files | "company_facts: 0 changed, 8 unchanged". Nothing written. | N/A |
| Seed edit | `legal_ai` wording edited in the file | version 2 (`seed`), event `v1` → `v2`; version 1 kept | N/A |
| Conflict | Operator edited `legal_ai` (v2), then its seed wording changes | "…, 1 conflict" and the conflict line; no row or event. Unchanged seed: no conflict. | N/A |
| Retracted | Operator retracted `wish_wheels`; seed unchanged, then changed | unchanged, then a conflict; never restored | N/A |
| Not in file | an Operator-added `stat_x` | "  stat_x: not in seed/company_facts.yaml, left as it is (version 1)" | N/A |
| Invalid file | regulation with 2 markets; metric without date; `ftp://` URL; ABA wording on `uk`; SRA scope note on `us`; unknown segment or market; repeated `fact_id`; missing `wording`; future date | Exit 1, `seed/company_facts.yaml: <field>: <reason>`; the DB file's SHA-256 unchanged | nothing written |
| File | `company_facts.yaml` missing, larger than 1 MiB, or not UTF-8 | exit 1, naming `seed/company_facts.yaml` | nothing written |
| Atomic | a failure while recording a fact, after the other three syncs ran | "seed sync failed (…); nothing was changed"; no rows in any seeded table, and no events | rollback |
| Panel | after the first sync | 8 facts with type, link, Segment and Market names, "none" metric and 2026-11-14 | N/A |
| Add | valid form | version 1 (`operator`), "Saved <id> as version 1." | N/A |
| Edit | changed wording, `based_on` = latest | version n+1; earlier versions listed under it | N/A |
| Edit refused | regulation with 2 Markets; metric without date; `javascript:` URL; ABA on UK; stale `based_on`; taken `fact_id` on add | 200, the form re-rendered with each reason (the AC's exact texts); nothing saved | `InvalidFact` |
| Retract | a current fact | every version `retracted_at`; listed "Retracted"; `allowed_facts` no longer returns it | N/A |
| Allowed | `allowed_facts(law, uk)` / `(law, us)` / `(ops, us)` | uk: SRA, not ABA; us: ABA, not SRA; ops: neither regulation | N/A |
| Immutable | a raw `UPDATE … SET wording`, a `DELETE`, a second `retracted_at` change | aborted by the triggers | `IntegrityError` |
| Bad POST | missing CSRF; an extra field; a non-ULID `segment` | 403 / 400 / 400; nothing written | N/A |

</frozen-after-approval>

## Code Map

- **The CLI** (`nextron-engine/nextron/main.py`):
  - the "Seed sync" docstring paragraph (around 225-262);
  - the constants (432-437);
  - `_sync_block`, `format_seed_sync`, `_load_seed` and `seed_sync` (1334-1430). Reuse them.
  - `build_app`'s `create_app(...)` call (around 541).

  `nextron/__main__.py` needs its docstring and the `seed sync` help and description.
- **Setup** (`nextron/app/setup/api.py`):
  - the docstring (around 77-103);
  - `SequenceSync` and `SeedSync` (366-383);
  - `Setup.sync_seed` (477-501, to become `seed_sync` plus `check_seed`) and `_log_sync`;
  - `_segment_inputs`, `_market_inputs` and `_sequence_inputs`, and `_sync_*` and `_record_*`. Keep them.

  A unit of work allows **one** `@use_case`, and a `@command` only joins an open transaction (`db/uow.py`).
- **Ops** (`nextron/app/ops/api.py`): its docstring and imports (158-211). `DeadJobAlerts(notify)` shows the pattern of an ops class calling another service.
- **Playbook:** `nextron/app/playbook/api.py` (empty stub), `nextron/app/queries/playbook.py` (empty stub) and `nextron/db/repos/playbook/__init__.py`.
  - Patterns to follow: `app/queries/setup.py` (`SeededMarket`, `markets()`), `app/setup/api.py` `_sync_markets` and `_record_market` (622-698), and `db/eventlog.py` `append`.
  - Notify's `@command alert` is a command defined as a class method.
- **Persistence:**
  - `db/alembic/versions/0010_4_1_sequences.py` (the pattern) and `0001_1_3_persistence_foundation.py` (the trigger precedent);
  - `db/models/base.py` and `db/models/setup.py`; `db/models/__init__.py` (import the new module);
  - `db/owners.py` and `db/pii_registry.py`;
  - `db/repos/setup/markets.py` and `sequences.py` (canonical JSON, decoding).
- **Domain:**
  - `nextron/domain/markets.py`: `_Validator`, the field paths and text rules. Mirror the private helpers; don't import them.
  - `nextron/domain/segments.py:228-245`: `mentions_aba_512` and `mentions_sra_notice`. Reuse these.
  - `nextron/domain/seed_yaml.py`: dates must be quoted.
- **Web:**
  - `nextron/web/app.py`: the docstring route list, `create_app`'s parameters, `settings_page` and `settings_save`, and the `health_job_dismiss` write-then-read pattern;
  - `web/settings_page.py` and `templates/settings_form.html` (the refused-form pattern);
  - `web/forms.py` (`form_fields`, `MalformedForm`, `WRONG_FIELDS`, `REPEATED_FIELD`);
  - `web/templating.py` (`plain`, `safe_link`, `csrf_field`);
  - `templates/settings.html` (add the link);
  - `nextron/domain/ids.py` `is_ulid`.
- **Tests to update:**
  - `tests/dashboard.py` (`create_app` call, about 223; build `CompanyFacts(self.clock)`);
  - `tests/contracts/test_web_contracts.py` (the route lists near 150-165, 270-290 and 450-460);
  - `tests/contracts/test_market_contracts.py:36`;
  - `tests/contracts/test_persistence_contracts.py:72`, its probes (668-690) and the trigger tests (760-790);
  - `tests/unit/test_migrations.py:38`;
  - `tests/app/test_serve_process.py:263`;
  - `tests/contracts/test_source_tree.py` `FILES` (around 61, 88, 93 and 146);
  - the seed tests: `seed_dir` in `test_seed_sync.py:73`, `test_market_sync.py:119` and `test_sequence_sync.py:133`; the summary constants (`test_seed_sync.py:136-152`); the direct `sync_seed` calls (`test_market_sync.py:666, 872`; `test_sequence_sync.py:775-965`).
- **Contracts that apply:**
  - `test_domain_stdlib_only.py`;
  - `test_error_contracts.py` (unique codes `invalid_company_facts` and `invalid_fact`);
  - `test_log_event_names.py` (names under 32 characters);
  - import-linter c6 (the app DAG) and c7;
  - the web form, autoescape and `|safe` allowlist checks.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/company_facts.py` -- the types, the value rules, rules 1–5, the file validation, the Operator path, the errors and the form round trip -- one Approved Company Fact module.
- [x] `nextron-engine/seed/company_facts.yaml` -- the Design Notes content, with a header comment: what it is, that `nextron seed sync` loads it, the YAML subset, and that the Operator's panel edits win over seed changes.
- [x] `nextron-engine/nextron/db/models/playbook.py`, `db/models/__init__.py`, `db/alembic/versions/0011_4_3_company_facts.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/playbook/company_facts.py`, `db/repos/playbook/__init__.py` -- the table, its triggers, its mapping and its repository.
- [x] `nextron-engine/nextron/app/playbook/api.py`, `app/queries/playbook.py` -- `CompanyFacts` (`seed_facts`, `add`, `edit`, `retract`), `check_seed_facts`, `log_fact_sync`, the result types and the queries.
- [x] `nextron-engine/nextron/app/setup/api.py`, `app/ops/api.py` -- `check_seed`, `CheckedSeed`, the `seed_sync` command, `SetupSeedSync`, `log_seed_sync`; `Seeds` and `SeedSync` in ops.
- [x] `nextron-engine/nextron/main.py`, `nextron/__main__.py` -- the fourth file, `Seeds`, the facts block, the docstrings and help, and `create_app(..., facts=...)`.
- [x] `nextron-engine/nextron/web/app.py`, `web/facts_page.py`, `web/templates/facts.html`, `web/templates/facts_panel.html`, `web/templates/settings.html` -- the page, its three POSTs and the Settings link.
- [x] `nextron-engine/tests/unit/test_company_facts_domain.py` -- stdlib-only:
  - each value rule and rules 1–5 with exact field paths and the AC's reasons;
  - unknown and missing keys;
  - the round trip;
  - the Operator path's normalising and its collected problems.
- [x] `nextron-engine/tests/unit/test_company_fact_seed.py` -- the shipped file validates into exactly the Design Notes values. Also:
  - the Legal AI wording never contains "for a law firm" (case-insensitive);
  - the Wish Wheels wording contains no "distribut";
  - every fact has a source URL and no metric;
  - ABA 512 is `us`-only and the SRA notice `uk`-only;
  - the references pass against the shipped Segments and Markets.
- [x] `nextron-engine/tests/app/test_company_fact_sync.py` -- every seed row of the I/O Matrix through `main.seed_sync(env="test", seed_dir=...)`. Also:
  - the `Seeds` transaction (one clock read, one transaction, nothing before it on bad input);
  - `queries.playbook` (`company_facts`, `fact`, `allowed_facts`);
  - the use cases directly (`add`, `edit`, `retract`, their events and logs);
  - the triggers and check constraints;
  - a stored row that no longer decodes;
  - the CLI help naming `seed/company_facts.yaml`.
- [x] `nextron-engine/tests/app/test_facts_page.py` -- the panel and every POST row of the I/O Matrix through `tests/dashboard.py` with real units: notices, a refused form keeping its values, the earlier versions, `safe_link` output, `no-store`, CSRF and malformed refusals.
- [x] `nextron-engine/tests/contracts/test_company_fact_contracts.py` -- the repository's `insert` and `retract` are used only in `app/playbook/api.py`, and no web module names the repository or `seed_facts`. Probes prove each check catches a violation.
- [x] The earlier seed tests, the migration and tree tests, `tests/dashboard.py` and `test_web_contracts.py` -- only the forced changes from the Code Map.
- [x] `nextron-engine/README.md` -- "Seeds" now covers four files and four blocks, and the use case moves to `ops`. Add an "Approved Company Facts" subsection (the fields, the floor, versions, retraction, the seed conflict, the panel and `allowed_facts`) and a migrations-table row.

**Acceptance Criteria:**
- Given the full suite, when it runs through the suite lock and `uv run lint-imports` runs, then everything passes and every contract is kept.
- Given every model column, when the persistence contracts run, then `company_facts` is fully mapped in `owners.py` (owner `playbook`) and `pii_registry.py` (`none`), and the migration matches the model.
- Given the 2.1, 3.1 and 4.1 behaviour, when their tests run with only the forced changes, then they pass.

## Implementation Notes

- **Domain** (`nextron/domain/company_facts.py`). One table of per-field checkers is shared by the file (`validate_company_facts`, `fact_from_form`; first problem, file order, then missing keys, then rules 1–2), `content_problems` (every problem, field order) and `fact_from_fields` (every field's first problem). A reference passes the value rules when it fits the key/code pattern **or** is a ULID, so the same rules decode a stored row; rule 4 then names it: a ULID fails "names no stored Segment" (or Market), a key or code fails "names <key>, which isn't a Segment of this sync" (or Market). Segments and markets are sorted after validation, so an index in a rule 3–4 path counts the sorted list. Rule 3 looks only at Markets in the country map (an unknown Market is rule 4's). `content_problems` refuses a date given as text or as a `datetime`. `is_link` mirrors `safe_link` (backslash and `Zs` included). The Operator's empty checkbox groups read "Choose at least one Segment" / "Choose at least one Market" (the spec names only "Enter a value" and "Choose a type").
- **Persistence.** `CompanyFactRecord` (`db/models/playbook.py`) and migration `0011_4_3_company_facts` (down `0010_4_1_sequences`; `downgrade()` raises). The repository reads both `Date` columns as their stored text (`type_coerce(..., String)`) so a malformed date raises `InvalidCompanyFacts`, not a driver error; a list column that isn't JSON fails on `segments`/`markets` ("allowed_segment_ids is not valid JSON"). `retract` sets `retracted_at` only where it is NULL (the trigger refuses a second change).
- **Playbook** (`app/playbook/api.py`). `CompanyFacts.seed_facts` is a `@command`; `add`, `edit` and `retract` are `@use_case`s with one clock read and one transaction each. `edit` merges a form-level problem (unknown fact, stale `based_on`) with the field problems in one `InvalidFact`. Extra public helpers: `latest_allowed(now)` (UTC date + 1 day) and `CompanyFacts.today()` (the Add form's default date). `FactRetracted(fact_id, retracted)`.
- **Setup / ops.** `check_seed` and `log_seed_sync` are module functions in `setup.api`; `Setup.seed_sync` is the `@command`; `CheckedSeed` exposes `segment_keys` and `market_countries`. The `_*_inputs` messages are unchanged (they still say `sync_seed`). `ops.Seeds.sync_seed` reads the clock first (for `latest`), then runs both checks, then one transaction.
- **Web.** `web/facts_page.py` builds the panel context and parses the three forms; `web/app.py` adds the four routes (registered right after `/settings`). Element ids: `fact-new-*` for the Add form, `fact-edit-<fact_id>-*` for each Edit form. Segment and Market names show in alphabetical order. A small block of `.fact*` rules was added to `app.css`. The Retract button asks no confirmation (a retraction is undone by an edit).
- **Forced test changes.** The three earlier seed tests copy the fourth file, gain the facts block in their exact outputs, the 8 events in their counts, `Seeds(clock).sync_seed(a, b, c, shipped_facts())` for the direct calls (with `SeedSync` from `nextron.app.ops.api`), and `company_facts_synced` in the log-name lists (their `caplog` now also captures `nextron.app.playbook.api`). The market contract's `FORBIDDEN_NAMES` gains `seed_sync` (with one probe). HEAD constants and chain probes bumped; `test_source_tree` `FILES`, the web-contract route and template lists, and `tests/dashboard.py` updated.
- **Review patches (pass 1).** All done:
  - P1: rule 3 (the compliance floor) now also scans `metric` on every path: the file through `check_fact_references`, and `fact_from_fields`. The module docstring, the seed header and the README now say "wording, scope note or metric". Tests: a domain test per regulation on all three paths, and an app test where `add` refuses a metric naming ABA 512 on a UK Market.
  - P2: the unused `FactForm.action`, `ADD_ACTION` and `EDIT_ACTION` are removed from `web/facts_page.py`.
  - P3: the trigger-existence check moved to `tests/contracts/test_persistence_contracts.py` (`test_the_company_facts_triggers_exist_at_head`) and was deleted from the app tests.
  - P4: two app tests accept a date of exactly UTC today plus one day (2026-11-17 under the 2026-11-16 clock): one through seed sync, one through `add` with `metric_confirmed_at` and `last_checked_on` both set to it.
  - P5: a test pins the comparison with the *highest* seed version. The sequence is seed v1, seed v2, then the Operator's v3. The v2 file is then unchanged, and the shipped (v1) file is a conflict at version 3. No row or event is written.
  - P6: the immutability probe now covers every column `PRAGMA table_info(company_facts)` lists except `retracted_at` (14 columns). Each is set to a different value of its type, and each must abort with "append-only" and leave the table unchanged. It is one test looping over the PRAGMA list, because pytest collects parameters before any database exists. The DELETE and INSERT probes and the retracted_at-once checks stay.

**Final verification (after the pass-1 patches)**
- **The spec's targeted set,** plus `test_migrations.py`, `test_settings_page.py` and `tests/contracts`: 1368 passed, 1 skipped.
- **`uv run lint-imports`:** 29 kept, 0 broken.
- **`NEXTRON_ENV=test uv run nextron seed sync --help`** names the four files.
- **The full suite through `suite_lock.py` (`track-C`):** 5728 passed and 2 skipped, with no failure (the job-runner flake didn't appear). `lint-imports` exited 0.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers reviewed the diff since the baseline (`nextron-engine/` only):
- blind hunter (BH): 13 findings;
- edge-case hunter (EC): 8;
- verification gap (VG): 3.

That is 24 raw findings, which deduplicate into 20 groups: 6 patched (P1–P6), 0 deferred and 14 rejected (R1–R14). Each raw finding appears once:
- BH: P1, P2, P3, R1, R4, R5, R6, R7, R8, R9, R10, R11, R12 (13);
- EC: P1, R1, R2, R3, R6, R9, R13, R14 (8);
- VG: P4, P5, P6 (3).

| # | Finding(s) | Verdict | Evidence | Route |
|---|---|---|---|---|
| P1 | BH4, EC4: the compliance floor scans only `wording` and `scope_note`, so a `metric` naming ABA 512 or the SRA notice passes on a wrong-country Market | low | **Real:** `_reference_problems` (`domain/company_facts.py:468`) builds `texts` from wording and scope note only. A metric is quoted text a Draft may cite, so the AC's "ABA 512 never offered for a UK Draft" can fail through it. The fix is a direct correction. | patch: scan `metric` too, on every path; add domain and app tests |
| P2 | BH8: `FactForm.action`, `ADD_ACTION` and `EDIT_ACTION` are computed but never rendered; the template hardcodes the paths | low | **Real:** `facts_panel.html` hardcodes `hx-post="/facts/add"` and `"/facts/edit"` and never reads `form.action`, so it is dead code and a second source of the URLs. The fix is a deletion. | patch: remove the unused field and the two constants |
| P3 | BH9: the trigger-existence test is in `tests/app`, not the persistence contracts the Code Map pointed at | low | **Real:** `test_the_triggers_exist_at_head` lives in `test_company_fact_sync.py`. Patchers run only `tests/contracts` plus the files they touch (§4a), so a later batch rebuild of `company_facts` that drops the triggers would pass that gate. The fix is moving one test. | patch: move the trigger-existence test into `tests/contracts/test_persistence_contracts.py`, next to the `events` one |
| P4 | VG1: no app-level test accepts a date of exactly UTC today plus one day through `Seeds.sync_seed` or `add`/`edit` | low (gap, pre-verified) | **Filed evidence:** with `latest_allowed(now)` replaced by `now.date()` at both call sites, all 237 tests passed. | patch: a seed sync with `last_checked_on: '2026-11-17'` (DEFAULT_NOW 2026-11-16) exits 0 and stores it; `add` with `last_checked_on` and a metric's `metric_confirmed_at` of `2026-11-17` saves version 1 |
| P5 | VG2: no test pins "unchanged against the *highest* seed version" when the latest row is the Operator's | low (gap, pre-verified) | **Filed evidence:** `seeded[-1]` changed to `seeded[0]` and every test passed; each test of that branch stores at most one seed row. | patch: v1 seed (shipped), v2 seed (new wording), v3 Operator edit; a sync of the new wording is unchanged, and a sync of the shipped wording is a `legal_ai` conflict at version 3 |
| P6 | VG3: the immutability trigger is probed for only 5 of its 14 protected columns | low (gap, pre-verified) | **Filed evidence:** with `source_url` and `allowed_segment_ids` dropped from `FIXED_COLUMNS`, every trigger test passed. | patch: parametrize the refusal test over every column but `retracted_at`, read from `PRAGMA table_info(company_facts)`, each set to another value of its type; assert the abort and an unchanged dump |
| R1 | BH1, EC1: an Edit form drawn before a retraction (a second tab) passes the `based_on` check and saves version n+1, restoring the fact | medium | **Real:** `retract` adds no version, so `based_on` still matches. But the fix needs a new staleness token (a hidden field or a use-case parameter), and the frozen spec fixes the edit form to `fact_id` and `based_on` and its check to "a latest version other than `based_on`". It needs two open tabs, and the restoring save is still an explicit Operator action, recorded as an `operator_edit` event. | rejected: it would change the approved spec (for Mac) |
| R2 | EC2: a Retract posted from a stale page retracts a version the Operator never saw | low | Retraction is fact-level (decision 4) and fails safe: it removes claims. The fix adds a form field to a form the spec fixes. | rejected: it would change the approved spec |
| R3 | EC3: a later seed change of a stored Market's country under the same code isn't re-checked against stored facts | low | It needs a multi-sync dance: country codes are unique in the file and the table, and the Market floor allows cold email only for US and GB. File facts are re-checked each sync. Fixing it means re-checking every stored fact, which adds new checks. | rejected |
| R4 | BH5: ABA 512 and SRA facts are tied to Markets but not to the law Segment | low | No compliance harm: the Market floor holds. The domain has no notion of a "law" Segment, so the fix would be a new rule the spec doesn't define. | rejected: it would change the approved spec |
| R5 | BH6: the "for a law firm", "distribut" and Offer-price guardrails are tested on the seed file only, not enforced on Operator saves | low | The AC asks for a test on the seed wording. Enforcing it on every save is a new rule the spec doesn't define, and Drafts are checked by 4.13's grounding rules. | rejected: it would change the approved spec |
| R6 | BH2, EC6: a stored row that no longer decodes breaks `/facts`, `allowed_facts` and every seed sync, and the triggers leave a migration as the only repair | low | **Frozen spec behaviour:** "A row that doesn't decode raises `InvalidCompanyFacts`" (3.1 R4 and 4.1 R1 rejected the same). It needs a hand edit of the database or a rule tightened later. It fails closed. | rejected: it would change the approved spec (for Mac: a rule tightened later needs a data migration) |
| R7 | BH3: `allowed_facts` decodes every version on each call | low | The table holds tens of rows, so the cost is negligible. The fix is a new repository query. | rejected |
| R8 | BH7: the seed summary's conflict line doesn't say the fact is retracted | low | The frozen spec fixes the line ("conflict, left as the Operator set it (version <n>)") for both an edit and a retraction. | rejected: it would change the approved spec |
| R9 | BH10, EC7: an Edit for an unknown `fact_id` with field problems says "correct the fields marked below", yet no form renders | low | Facts are never deleted, so an unknown `fact_id` comes only from a crafted form. The fix adds a branch. | rejected |
| R10 | BH11: after a stale-page refusal the refused form keeps the old `based_on`, so the typed edit can't be resubmitted | low | It needs two tabs. The spec's message tells the Operator to reload. Re-rendering with the new version would bypass the very check the refusal made. | rejected: it would change the approved spec |
| R11 | BH12: the web tests check rows, not `events`, for refused and successful POSTs | low | Events are written in the same use-case transaction as the rows, and the use-case tests assert them. A refused POST raises before any write. No bad outcome is shown. | rejected |
| R12 | BH13: dates use UTC, not `sender_timezone` | low | **Spec rule 5 and the Add form default are UTC by design** (decision 6), with one day's tolerance. | rejected: it would change the approved spec |
| R13 | EC5: a seeded text with a double space or NBSP, saved unchanged by the Operator, becomes a new version, because the form collapses whitespace | low | The shipped seed has no such text. The no-op save is still an explicit Operator action. The fix is a new rule or a different comparison. | rejected |
| R14 | EC8: the keyword floor misses "Opinion 512 of the American Bar Association" | low | **Spec definition:** the frozen spec reuses 4.1's `mentions_aba_512`, and 4.1 R2 rejected the same point. Getting past it takes an Operator's deliberate wording. 4.13's grounding check is the guard on Drafts. | rejected: it would change the approved spec |

## Design Notes

**The seed file** (`seed/company_facts.yaml`, after the header comment; the reader parses this shape):

```yaml
company_facts:
  - fact_id: nextron_team
    type: company
    wording: A 17+ person team of engineers, AI specialists and analysts, based in San Jose.
    source_url: https://nextronai.com/
    segments: [law_private_ai, ops_automation]
    markets: [us, uk]
    last_checked_on: '2026-11-14'
  - fact_id: legal_ai
    type: case_study
    wording: Built an AI legal assistant platform for research and document help for a legal-tech venture (Legal AI).
    source_url: https://nextronai.com/
    segments: [law_private_ai, ops_automation]
    markets: [us, uk]
    scope_note: The client was a legal-tech venture, not a law firm.
    last_checked_on: '2026-11-14'
  - fact_id: wish_wheels
    type: case_study
    wording: Built a single operations platform covering CRM, orders, inventory, vendors and analytics for an automotive retail and services business (Wish Wheels).
    source_url: https://nextronai.com/
    segments: [law_private_ai, ops_automation]
    markets: [us, uk]
    scope_note: An automotive retail and services business, not a distributor; don't present it as distribution experience.
    last_checked_on: '2026-11-14'
  - fact_id: mp_online_digital
    type: case_study
    wording: Built a citizen-services digital transformation platform with workflow automation and dashboards (MP Online Digital).
    # same source_url, segments, markets and last_checked_on as above
  - fact_id: property_3d_sites
    type: case_study
    wording: Built immersive 3D property sites with CRM-connected lead capture (Sapphire Farms, Woodland 157).
    # same source_url, segments, markets and last_checked_on as above
  - fact_id: audit_offer
    type: offer
    wording: 'A fixed-fee Private AI & Workflow Audit, credited in full toward a build.'
    scope_note: Name the audit only in Reply Drafts, never in a Step; no price until the Offer is validated.
    # same source_url, segments, markets and last_checked_on as above
  - fact_id: aba_formal_opinion_512
    type: regulation
    wording: ABA Formal Opinion 512 says a lawyer needs the client's informed consent before putting client information into a self-learning generative AI tool.
    source_url: https://www.americanbar.org/content/dam/aba/administrative/professional_responsibility/ethics-opinions/aba-formal-opinion-512.pdf
    segments: [law_private_ai]
    markets: [us]
    scope_note: Its informed-consent point concerns self-learning tools only; it is ethics guidance for US lawyers.
    last_checked_on: '2026-11-14'
  - fact_id: sra_ai_notice
    type: regulation
    wording: The Solicitors Regulation Authority (SRA) warning notice of 17 August 2026 allows client data into AI tools only with contractual, technical and organisational safeguards.
    source_url: https://www.sra.org.uk/solicitors/guidance/misuse-ai/
    segments: [law_private_ai]
    markets: [uk]
    scope_note: It also acknowledges that firms are building their own AI systems; cite it only for what it says about client data.
    last_checked_on: '2026-11-14'
```

The "same as above" comments stand for the full keys, which the shipped file writes out. A wording with `: ` or a leading special character must be quoted, and `'` doubles inside single quotes. The ABA wording names "ABA" and "512", and the SRA wording names "SRA", so the floor (rule 3) binds them to their Markets.

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_company_facts_domain.py tests/unit/test_company_fact_seed.py tests/app/test_company_fact_sync.py tests/app/test_facts_page.py tests/app/test_seed_sync.py tests/app/test_market_sync.py tests/app/test_sequence_sync.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
- the full suite through `suite_lock.py` (`track-C`) -- expected: exit 0.
- `NEXTRON_ENV=test uv run nextron seed sync --help` -- expected: names the four files, exit 0.
