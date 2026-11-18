---
title: 'Story 11.5: Edit Segments in the dashboard'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '43f9c1b2afe7291f31cc49648341ba4ccf0259a3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-11-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Segments (FR-1) can only change through `seed/segments.yaml` and `nextron seed sync`, which overwrites the row in place, keeps no history, and makes every later score use the new definition, so existing Leads are silently re-scored.

**Approach:** Add a Segments section to Settings (`/segments`) that creates and edits Segments through the unchanged Epic 2 validator. Every save appends an immutable `segment_versions` row (version n+1) and an `events` row (actor `operator`). `score_lead` keeps scoring an existing Lead with its stored version; only "Re-score with the current definition" (per Lead or per Segment) moves Leads forward. Seed sync stops writing Segments once the DB holds any.

## Boundaries & Constraints

**Always:**
- The Story 2.1 validator (`validate_segments`) validates every save, run over all stored Segments with the edited one substituted or appended, so cross-Segment key uniqueness still holds. Its rules and messages stay unchanged. The one extra rule is "A Segment needs a decision-maker role and at least one Tier 1 or 2 Event".
- Migration `0043_11_5_segment_editing` creates `segment_versions` (owner `setup`, no PII). Each row holds (segment_id, version, definition, created_at, actor), is unique on (segment_id, version), and is immutable through triggers that refuse UPDATE and DELETE. The migration backfills one row per existing Segment at its current version.
- `db/repos/setup/segments.insert`/`update` write the matching `segment_versions` row themselves, so no Segment write can skip a version.
- Every route is `@personal_data(False)`. Every form uses `method="post"`, `hx-post` and `{{ csrf_field() }}`. GETs are read-only units of work.
- A save carries `based_on` (the version the form showed). A stale save is refused with nothing written.
- A save identical to the stored definition writes nothing.

**Never:**
- Never rewrite or delete a `segment_versions` row, or re-score a Lead implicitly: no save and no seed sync enqueues `score_lead`.
- Never change a Segment's `key` after creation.
- Never let `nextron seed sync` update a stored Segment.
- No Segment delete or archive in this story.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Edit | valid change, based_on = stored v3 | `segments.version` 4; `segment_versions` v4 inserted; event `v3`→`v4` cause `operator_edit`, actor `operator` | N/A |
| Create | new key, valid | version 1 + row + event `None`→`v1` cause `operator_create`; inert (no Campaign) | duplicate key → refused on `key` |
| Validator refusal | Tier 2 weight ≥ a Tier 1 weight / Event with non-quote evidence / ABA 512 type / repeated key | 200, form re-rendered naming the failing field; nothing written | — |
| Missing role / Event | no decision-maker role, or no Tier 1–2 Event | refused: "A Segment needs a decision-maker role and at least one Tier 1 or 2 Event" | — |
| Stale form | based_on ≠ stored version | refused: "This Segment changed since you opened it"; nothing written | — |
| Score existing Lead | Lead scored at v3, Segment now v4, plain `score_lead` | scored with the v3 definition from `segment_versions`; version stays 3 | v3 row missing → skipped `segment_version_missing` |
| Score new Lead | never scored | scored with the current version | — |
| Move to current | "Re-score with the current definition" (Lead or Segment) | enqueues `score_lead` with use-current for each open, unmerged Lead; its score stores v4 | merged or closed Lead → refused or left out |
| Seed sync, DB has Segments | any YAML | writes no Segment; reports "Segments are managed in the dashboard; seed/segments.yaml ignored" plus per-key differences | — |
| Seed sync, DB empty | YAML | bootstrap inserts each at v1 with version rows (actor `seed`) | — |

</frozen-after-approval>

## Code Map

- `nextron/domain/segments.py` -- `SegmentDef`, `_Validator`, `validate_segments`, `definition_text`, `segment_from_text`, `_file_form`. Extend `DiscoveryQueries` with optional `registry_keywords`, `registry_sic_codes` (each a 4–5 digit text) and `job_titles`. In the file form they appear as `discovery_queries.registry: {keywords, sic_codes}` and `discovery_queries.job_titles`. All are optional and may be empty, and are emitted **only when non-empty**, so existing canonical texts stay byte-identical (AI test-set fingerprints hash them).
- `nextron/db/alembic/versions/0008_2_1_segments.py`, `0017_4_3_company_facts.py` -- the table style and the immutability-trigger pattern to copy.
- `nextron/db/repos/setup/segments.py`, `nextron/db/models/setup.py`, `nextron/db/owners.py`, `nextron/db/pii_registry.py` -- the Segment repository, model and registries.
- `nextron/app/setup/api.py` -- `sync_segments`/`_sync_segments` (around line 840/912), `SegmentSync`, `check_seed`, `CheckedSeed`, `seed_sync`, `log_seed_sync`. Its docstring holds the rules; update it.
- `nextron/app/ops/api.py:638` `Seeds.sync_seed` -- runs `check_seed` and the facts check. In managed mode, run the reference checks (Sequences, Campaigns, facts) against the **stored** Segments.
- `nextron/main.py:~2897` `seed_sync` (CLI output), `nextron/__main__.py` (help text).
- `nextron/app/queries/setup.py` -- `SeededSegment`, `segments()`, `segment_by_id`. Add `segment_version(segment_id, version)` and `segment_versions(segment_id)`.
- `nextron/app/scoring/api.py` `Scoring.rescore`, `nextron/app/scoring/lead_scores.py` `score_lead`/`request_rescore`/`enqueue_score_lead`, `nextron/domain/scoring.py` `SCORE_LEAD_JOB`, and the `score_lead` job handler in `nextron/main.py` `build_job_types`.
- `nextron/app/queries/scoring.py` (exposes `segment_version`), `nextron/web/templates/lead_score.html`, the `POST /leads/{lead_id}/rescore` route in `nextron/web/app.py:1706`, `nextron/web/leads_page.py`.
- `nextron/web/facts_page.py`, `templates/facts.html`, `facts_panel.html`, the `/facts` routes in `app.py:~800` -- the page/panel/refusal/`based_on` pattern to copy. `templates/settings.html` links its sub-pages.
- Tests that call `sync_segments` or `seed sync` to *change* a Segment (`tests/app/test_segment_sync.py` and others) must move to the new edit command.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/segments.py` -- add the optional discovery fields. Add `segment_editing_rule(segment)`, which gives the extra refusal; `segment_form_diff(old, new)`, which lists the changed field paths with their old and new values for the history view; and `file_form(segment)` as a public helper. Keep every existing rule and message unchanged.
- [x] `nextron/db/alembic/versions/0043_11_5_segment_editing.py` -- create the table, add the triggers and run the backfill. Then bump the HEAD constants, chain probes and `test_source_tree` FILES, and update `owners.py` and `pii_registry.py`.
- [x] `nextron/db/models/setup.py`, `nextron/db/repos/setup/segments.py` -- add the model. `insert`/`update` also write the version row. Add `version(segment_id, n)` and `versions(segment_id)`.
- [x] `nextron/app/setup/segment_editing.py` (new; `setup` service) -- `SegmentEditor(clock)` with `@use_case create(fields)` and `@use_case edit(segment_id, based_on, fields)`. It runs the validator as described in Boundaries, writes the event and logs `segment_saved` (key, version). Every refusal is `SegmentRefused(problems: {form field: text})` and writes nothing.
- [x] `nextron/app/setup/api.py`, `nextron/app/ops/api.py`, `nextron/main.py`, `nextron/__main__.py` -- seed coexistence as in the Matrix. `SegmentSync` gains `managed: bool` and `differences` (key plus "only in the file", "only in the dashboard" or the changed paths). The CLI prints the notice and the differences.
- [x] `nextron/app/scoring/api.py`, `lead_scores.py`, the job handler -- `rescore(lead_id, *, use_current=False)`. A stored version is read from `segment_versions`; with none stored, or with `use_current`, the current version applies. Add the `segment_version_missing` skip. `enqueue_score_lead(..., use_current=False)`: use-current jobs use their own discriminator `current` and the payload field `use_current: true`, so a pending stored-version job can't absorb them. Add `request_rescore_current(lead_id)` and `request_segment_rescore(segment_id) -> int`; the second covers every open, unmerged Lead of the Segment on the bulk lane. Plain "Re-score" keeps the stored version.
- [x] `nextron/web/segments_page.py` + templates + routes -- `GET /segments` (list: key, name, version, Campaign count, "inert" when unused, create form), `GET /segments/{id}` (edit form), `GET /segments/{id}/versions` (each version with the diff from the previous), `POST /segments/create`, `POST /segments/{id}/edit`, `POST /segments/{id}/rescore-current`. List fields are textareas, one item per line; ranks are one rank per line, with equal titles separated by ` | `. Signal types and disqualifiers are indexed fieldsets plus 2 blank rows, with a remove checkbox. Disqualifier kind and size clue, and Signal class, evidence, sources and unit, are selects or checkboxes built from the code enums. Add the Settings link.
- [x] `nextron/web/templates/lead_score.html`, `app.py`, `leads_page.py` -- show "Segment version n (current m)". Add `POST /leads/{lead_id}/rescore-current` ("Re-score with the current definition"); it is registered before any catch-all.
- [x] tests -- domain (the new fields, round-trip and byte-stability of existing texts, the extra rule, diff), migration and backfill, trigger immutability, the editor's use cases (every Matrix row), the seed sync modes, scoring with the stored, missing and current version, the web routes (CSRF refusal, refusal re-render, stale form), and the README section in `nextron-engine/README.md`.

**Acceptance Criteria:**
- Given a forged or missing CSRF token on any `/segments` POST, then nothing is written.
- Given a saved v4, when `/segments/{id}/versions` is opened, then v1..v4 are listed, each with its changed fields.
- Given this story, when the full suite runs, then every contract and `lint-imports` pass.

## Implementation Notes

**Files.** New: `nextron/app/setup/segment_editing.py` (`SegmentEditor`, `SegmentSaved`),
`nextron/db/alembic/versions/0043_11_5_segment_editing.py` (revision `0043_11_5_segment_editing`,
down `0042_4_9_drafts`), `nextron/web/segments_page.py`, templates `segments.html`,
`segments_panel.html`, `segment.html`, `segment_panel.html`, `segment_form.html` (field macros
only, no form) and `segment_versions.html`; tests `tests/unit/test_segment_editing_domain.py`,
`tests/app/test_segment_editing.py`, `tests/app/test_segments_page.py`. Changed:
`domain/segments.py`, `domain/job.py`, `db/models/setup.py` (+`__init__`), `db/owners.py`,
`db/pii_registry.py`, `db/repos/setup/segments.py`, `db/repos/scoring/scores.py`,
`app/queries/setup.py`, `app/queries/scoring.py`, `app/setup/api.py`, `app/ops/api.py`,
`app/scoring/api.py`, `app/scoring/lead_scores.py`, `jobs/handlers.py`, `main.py`,
`__main__.py`, `web/app.py`, `web/leads_page.py`, `templates/lead_score.html`,
`templates/settings.html`, `README.md` (new "Editing Segments" section; Seeds section updated),
`tests/dashboard.py` and the pinned tests (HEAD constants, chain probes, `FILES`, template and
route lists, `PARAMETER_VALUES` in the Google GET contract).

**Decisions.**
- *Domain.* `DiscoveryQueries` gains `registry_keywords`, `registry_sic_codes` (fullmatch
  `[0-9]{4,5}`), `job_titles`, all default `()`. File form: `discovery_queries.registry.{keywords,
  sic_codes}` and `discovery_queries.job_titles`, emitted only when non-empty (each registry list
  separately), so shipped texts are byte-identical (tested against a hand-built pre-11.5 form).
  `file_form` is public. Added `segment_editing_rule` (returns an `InvalidSegments` on
  `.decision_maker_roles` or `.signal_types` with `EDITING_RULE`), `segment_form_diff` ->
  `FieldChange(path, old, new)` (compact JSON values; Signal types and disqualifiers matched by
  key; an added/removed item is one whole-item change), `SegmentRefused(problems)` and
  `edited_field(path, form)` (validator path -> form field: keyed items become their 0-based
  position, cut at any other list index). Validator rules and messages are unchanged.
- *Migration.* `segment_versions` (id, segment_id FK no cascade, version >= 1, definition,
  created_at, actor IN ('seed','operator')), unique (segment_id, version); triggers
  `trg_segment_versions_no_delete`, `_no_id_reuse` (also blocks INSERT OR REPLACE) and
  `_no_update` (every UPDATE). Backfill: one row per Segment at its current version,
  `created_at` = `segments.updated_at`, actor `seed`, ULIDs from python-ulid via `op.get_bind()`.
- *Repo.* `insert(key, definition, at, actor)` / `update(..., actor)` always write the version
  row; added `version(segment_id, n)` and `versions(segment_id)`.
- *Editor.* Validates the other stored Segments alone first (a broken stored row refuses on
  `form`: "A stored Segment no longer validates (...)"), then them plus the proposed one placed
  **last**, so a repeated key is always named on the edited Segment's own field. Order of edit
  refusals: unknown id, stale `based_on`, key change (`"A Segment's key can't change"`), then
  validation. A form without `key` keeps the stored key (the web edit form never sends one).
- *Seed coexistence.* `_sync_segments` decides inside the transaction: no stored Segment ->
  bootstrap insert (actor `seed`); otherwise managed: writes nothing and returns
  `SegmentSync(..., managed=True, differences=(SegmentDifference(key, only_in, paths), ...))`.
  The old shared-key refusal (`_refuse_shared_keys`) became unreachable and was removed, with
  its tests. CLI prints `segments: Segments are managed in the dashboard; seed/segments.yaml
  ignored` then `  <key>: only in the file` / `differs in <paths>` / `only in the dashboard`.
  `Seeds.sync_seed` now reads `queries.setup.segment_keys()` (new) and runs `check_seed(...,
  stored_segment_keys=...)` and `check_seed_facts` **inside** its one transaction, so in managed
  mode Sequence angles, Campaigns and facts are checked against the stored keys. A refused check
  still writes nothing (the transaction rolls back), but it is no longer "before any
  transaction": the sync_seed tests that patched `uow.transaction` were renamed
  `..._before_any_write` and drop that probe (they keep the "nothing written" asserts).
- *Scoring.* `rescore(lead_id, *, use_current=False)`: stored version used when set and not
  `use_current` and different from current; missing row -> skipped `segment_version_missing`.
  `enqueue_score_lead(..., use_current=True)` uses discriminator `current` and payload
  `use_current: true`; `domain.job` now accepts `current` as a discriminator (the spine lists
  revision/date/tick; this is the extension the spec asks for). The handler passes
  `use_current=True` only when the payload says so (a non-bool raises `InvalidJob`).
  `request_rescore_current(lead_id)` (interactive) and `request_segment_rescore(segment_id) ->
  int` (bulk, `queries.scoring.segment_leads`, logs `segment_rescore_requested`) added.
- *Web.* Form field names are the editor's field paths (`signal_types[2].weight`, etc.); the
  web keeps the form row index of each kept row and maps the editor's positions back
  (`form_problems`). Removed/blank rows are dropped. Numbers that aren't whole numbers stay text
  so the validator names the field. Preferred roles: one per line `term | term => role | role`.
  Unknown Segment -> 404 "That Segment doesn't exist." (the rescore POST covers no Lead, then
  404s). The Lead Score panel always shows `v<n> (current v<m>)` when the current version is
  known, and gains a second form `POST /leads/{lead_id}/rescore-current`.
  `create_app` takes a new required `segment_editor=`.

**Verification run.** Targeted (`-n 2`): the three new test files, `test_seed_sync`,
`test_lead_scoring`, the market/sequence/fact/campaign sync tests, `test_leads_page`,
`test_lead_provenance_page`, erase/export/import page tests, `test_migrations`,
`test_job_domain`, campaigns/facts/enroll/eligibility-basis/ai-test-runs/contact-markets app
tests, serve/lifespan `create_app` tests: all pass. `tests/contracts` ran in full once (only the
route-list and Google GET-parameter pins failed; fixed and those files re-run green).
`lint-imports`: 29 kept, 0 broken. The full suite was not run (track rule).

**Review patches (triage 1-12).** The Lead page and both Re-score answers label the Score panel
from the version the score used (`web.app.scored_segment`: `queries.setup.segment_version` when
that version differs from current and its row exists, else the current definition;
`lead_context(..., score_segment=)`). `segment_editing._others` turns a non-JSON stored
definition into the `form` refusal "A stored Segment no longer validates (its stored definition
is not JSON)". `segment_editing_rule` also refuses a decision-maker title, preferred role or
when-term containing `|` or `=>` (`FORM_SEPARATOR` "can't contain | or =>", on
`decision_maker_roles` or `signal_types[i].preferred_roles`); the Story 2.1 validator is
unchanged. Create/edit forms carry `hx-sync="this:drop"`; `ROW_FIELD` takes `(0|[1-9][0-9]?)`,
so a leading-zero row is malformed. `segments_synced` logs `managed` and `differences_count`.
`USE_CURRENT_FIELD` lives once in `domain.scoring`. The versions page notes "Versions before
v<n> were not kept." when the earliest stored version is above 1. Tests added for each.

**Surprises / risks.** `Seeds.sync_seed`'s checks moved inside the transaction (see above). A
stored Segment that no longer decodes makes `/segments/{id}` raise like other pages that decode
Segments (the list page shows an "unavailable" alert instead).

**Final gate.** Full suite through `suite_lock.py` on the final tree: 17003 passed, 10 skipped; `lint-imports` 29 kept, 0 broken.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 15 findings, edge-case hunter 9, verification gap 3 gaps + 2 other = 29 raw; 22 after dedup).

| # | Source | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|---|
| 1 | blind, VG-other | Score panel labels factors/rule from the current definition for a Lead pinned to an older version | medium | patch | `leads_page.lead_context`/`app.rescore_answer` pass `seeded.definition`; should use `queries.setup.segment_version(segment_id, stored.segment_version)` with fallback |
| 2 | blind, edge | `_others` json.loads on a non-JSON stored row escapes as 500 | low | patch | `segment_editing._others` calls `json.loads` unguarded; direct fix: catch ValueError -> STORED_INVALID refusal |
| 3 | edge | A title/role/term containing `|` or `=>` is split on the next edit (round-trip corruption) | medium | patch | `segments_page` joins ranks with " | " and splits on "|"/"=>"; validator allows both characters. Fix: the editing rule refuses `|` and `=>` in titles, roles and when_terms, naming the field (validator unchanged; the seed has none) |
| 4 | VG gap 1 | Disqualified branch with an older stored version untested | medium | patch | only disqualify test runs at v1==current |
| 5 | VG gap 2 | Segment-level rescore-current route only tested with zero Leads | medium | patch | `test_the_segment_rescore_answers_with_how_many_leads_it_queued` uses a Lead-less fixture |
| 6 | VG gap 3 | Managed-mode facts check against stored keys untested | medium | patch | managed test stops at the Sequences check |
| 7 | blind | Create/edit forms lack `hx-sync="this:drop"`; double click gives a stale refusal or duplicate-key refusal | low | patch | `segments_panel.html`/`segment_panel.html` edit/create forms; direct attribute addition |
| 8 | edge | `ROW_FIELD` accepts `[01]` and `[1]` as distinct names that overwrite each other | low | patch | regex `[0-9]{1,2}`; direct fix `(0|[1-9][0-9]?)` |
| 9 | blind | `segments_synced` log can't tell managed mode from no-op | low | patch | `log_seed_sync` logs four zero counts; add `managed` and `differences_count` |
| 10 | blind | `use_current` payload field name defined twice (handlers, lead_scores) | low | patch | move to `domain.scoring` next to `SCORE_LEAD_JOB` |
| 11 | blind | Missing tests: score panel "v<n> (current v<m>)" label; rescore-current for unknown/non-ULID Segment | low | patch | no assertion found in test_leads_page / test_segments_page |
| 12 | blind, edge-claim | Versions page shows a backfilled v3 as "first stored version" with no hint earlier versions weren't kept | low | patch | template note when the first stored version > 1 |
| 13 | blind, edge | An old `segment_versions` row or current row that no longer decodes makes the versions page / segment page 500 and `score_lead` raise | maybe-false (medium if true) | defer | Only reachable if a later story tightens the validator; consistent with every existing Segment reader (precedent: Story 2.1 queries raise `InvalidSegments`) |
| 14 | blind | Managed-mode differences raise on an undecodable stored row, blaming seed/segments.yaml and blocking the other entities | low | reject | Same root as 13; a stored row can only become undecodable through a validator change; low and the fix adds branches |
| 15 | blind, edge | Managed mode still validates the ignored segments.yaml, so a broken file blocks the whole sync | low | reject | The CLI `_load_seed` validates every seed file before the use case (pre-existing behaviour); the file is still the bootstrap source and the differences need a valid file |
| 16 | blind | `current` discriminator is now valid for every job type | low | reject | Harmless widening of the key grammar; recorded as overnight decision 7 |
| 17 | blind | Pinned scoring ignores Signal types added in later versions (decision 6 consequence) | low | reject | This is the AC's required behaviour (existing Leads keep their version until the Operator moves them); recorded in decision 6 |
| 18 | blind | Segment-wide notice overstates (not-enriched Leads skip); unknown Segment logs before 404 | low | reject | Skips are logged per job; notice says "queued"; fix adds branches |
| 19 | edge | Plain Re-score of a `segment_version_missing` Lead says "queued" then skips silently | low | reject | Approved spec decision 3; the Lead page offers "Re-score with the current definition" (rejected only because it would change the approved spec) |
| 20 | edge | Managed-mode Sequence/Campaign refusal wording says "this sync" | low | reject | Message still names the field and key; rewording needs parameterising three shared checks |
| 21 | edge-claim | AC "v1..v4 listed" vs backfill keeping only the current version | false | reject | The AC's scenario is versions saved through this story; pre-11.5 versions were never stored (decision 3); note added via #12 |
| 22 | edge-claim, VG-other | Label wording "v<n> (current v<m>)" vs spec "Segment version n (current m)"; leftover test helper names | low | reject | Paraphrase of the spec; dead helper names have no effect |
| 23 | blind | Spec bookkeeping (tasks unticked, logs empty) | low | reject | Orchestrator fills these in step 3/5 bookkeeping, not code |

## Design Notes

### Decisions made overnight (pending user review)
1. **Seed bootstrap.** `nextron seed sync` still inserts Segments into a DB that holds **none**, so a fresh install ships Law-PrivateAI and Ops-Automation (FR-1). Once any Segment exists it writes none (AD-12: "the DB alone owns them") and reports the differences.
2. **The plain "Re-score" button (2.15)** now re-scores with the Lead's stored version. Moving a Lead to the current version needs "Re-score with the current definition". This settles the 2.15/11.5 tension without any silent re-scoring.
3. **Missing old version.** A Lead scored at a version older than the backfill (that history was never kept) is skipped as `segment_version_missing`. The Lead page offers the current-definition button.
4. **New discovery query fields.** Companies House keywords and SIC codes and aggregator job titles are added as optional, empty-allowed fields. Places queries stay required as before. Stories 8.4 and 8.6 consume them. Seed-file additions for them now reach only a fresh DB, so 8.4 must edit them through the dashboard or a migration.
5. **Fixed Segment key.** A Segment's key is fixed after creation, and there is no delete.
6. **Other readers stay on the current definition.** Discovery and research (job boards, legal forms, people, Places) and the Playbook test sets keep reading the current definition. Only scoring pins a version. Consequence: a Lead pinned to an older version is scored only on that version's Signal types, so Signals of types added later count only after "Re-score with the current definition".
7. **`current` job discriminator.** The job-key grammar (`domain/job.py`) now accepts `current` beside `tick` for any job type; only `score_lead` uses it.

## Verification

**Commands:**
- `cd e:/Nextron-epic2/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- pass
- `uv run pytest -q tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
