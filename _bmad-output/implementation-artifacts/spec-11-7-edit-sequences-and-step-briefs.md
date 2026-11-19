---
title: 'Story 11.7: Edit Sequences and Step briefs'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: 'ffc294a84c8c51211c48b73ab239f6bf3dd3833b'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-11-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-11-5-edit-segments-in-the-dashboard.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Sequences and Step briefs (FR-4) change only through `seed/sequences.yaml`, which overwrites `step_briefs` in place and keeps no history. After any version bump, Drafting's `request_steps` refuses a running Enrollment ("the Enrollment's Sequence version isn't stored").

**Approach:** Add a Sequences section to Settings (`/sequences`). It edits an existing Sequence's Step count, delays and briefs through the unchanged Story 4.1 validator, with the fixed FR-4 rules shown read-only. Every save appends an immutable `sequence_versions` row and an event. Each Enrollment binds the version row it started with (`enrollments.sequence_version_id`), and every later Step reads that version. Seed sync stops writing Sequences once the DB holds any. Copy Story 11.5's pattern throughout.

## Boundaries & Constraints

**Always:**
- `validate_sequences`/`sequence_from_form` (`domain/sequences.py`) validate every save. Their rules and messages stay unchanged. Refusals name the Step number and the form field.
- **Editable:** Step count (1–6; Step 1 can't be removed; rows are removed or appended, never reordered), each Step's `delay_days`, `purpose`, `word_limit` (1–150), `word_comparison`, `allowed_content` (textarea, one item per line) and `ask`, plus the Sequence `name`.
- **Fixed and shown read-only:** Step 1 delay 0, `link_rule` `footer_only`, Step 1's stored `subject_rule`, and `no_price`/`no_audit` true. The editor sets these itself and never reads them from the form. A kept Step keeps its stored `segment_angles`; an appended Step has none.
- Migration `0044_11_7_sequence_editing`:
  - It creates `sequence_versions` (owner `setup`, no PII): id, sequence_id FK (no cascade), version ≥ 1, definition (canonical `sequence_form` JSON), created_at, actor (`seed`|`operator`). The row is unique on (sequence_id, version) and immutable through no-update, no-delete and no-id-reuse triggers.
  - It backfills one row per Sequence at its current version (actor `seed`).
  - It adds the nullable `enrollments.sequence_version_id` (FK → `sequence_versions.id`, owner `sequencing`, no PII) and backfills it where (sequence_id, sequence_version) matches.
  - It recreates `trg_enrollments_bound` with that column added.
- `db/repos/setup/sequences.insert`/`update` write the version row themselves, with `actor`.
- `enroll` stores the current version row's id. `request_steps` reads the briefs of the Enrollment's bound version. When `sequence_version_id` is NULL, it falls back to today's check: current version equal, else LookupError. `day_offset` is still copied into each slot.
- A save carries `based_on`; a stale one is refused. An identical save writes nothing.
- A save that leaves fewer Steps than any Variant `step_angles` step of a Campaign using this Sequence is refused, naming the Campaign and Variant.
- Every route is `@personal_data(False)`. Forms use `method="post"`, `hx-post`, `{{ csrf_field() }}` and `hx-sync="this:drop"`. GETs are read-only units of work.

**Never:**
- Never rewrite or delete a `sequence_versions` row, or change a running Enrollment's bound version or slot `day_offset`.
- Never let seed sync update a stored Sequence.
- No Sequence create, delete or key change in this story. No change to the Playbook snapshot or the AI test gate: neither keys on Sequences.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Edit | valid change, based_on = v2 | `sequences.version` 3, step_briefs replaced, `sequence_versions` v3, event v2→v3 cause `operator_edit` actor `operator` | — |
| Bad delays | Step 3 delay ≤ Step 2's | refused on `steps[3].delay_days`, nothing written | — |
| Missing ask / bad word limit | blank ask on Step 2, word_limit 0 or 151 | refused on `steps[2].ask` / `steps[n].word_limit` | — |
| Stale form | based_on ≠ stored | "This Sequence changed since you opened it" | — |
| Running Enrollment | enrolled at v2, Sequence now v3, `request_steps` for Step 3 | slots use v2's briefs and delays | — |
| New Enrollment | after v3 | binds v3's row id | — |
| Form warning | 2 live Enrollments on v2, 1 on v3 | "2 active Enrollments keep version 2", "1 active Enrollment keeps version 3" | — |
| Capacity | active Campaigns on 4-Step and 3-Step current versions | `steps_per_sequence()` = 4 | no active Campaign → largest over all Sequences |
| Seed sync, DB has Sequences | any YAML | writes none; "Sequences are managed in the dashboard; seed/sequences.yaml ignored" plus per-key differences | — |
| Seed sync, DB empty | YAML | inserts v1 with version rows (actor `seed`) | — |

</frozen-after-approval>

## Code Map

- `nextron/domain/sequences.py` -- the validator, `sequence_form`, `sequence_from_form` and `_check_step_rules`, all reused unchanged. Add `sequence_form_diff(old, new)`, which gives the changed paths for the versions page, and `SequenceRefused(problems)`.
- `nextron/db/alembic/versions/0043_11_5_segment_editing.py` -- the pattern to copy: table, triggers and Python backfill. `0038_4_8_enrollments.py` has `BOUND` and `trg_enrollments_bound`. `0013_4_1_sequences.py` has the step_briefs columns.
- `nextron/db/repos/setup/sequences.py` -- `insert`, `update` (overwrites briefs in place; keep that), `StoredSequence` and `FIRST_VERSION`. Add `version_row`, `versions` and `current_version_id`. Add the model to `db/models/setup.py` and `sequence_version_id` to `db/models/sequencing.py`, and update `db/owners.py` and `db/pii_registry.py`.
- `nextron/db/repos/sequencing/enrollments.py:155` `insert(...)` -- add `sequence_version_id`. `nextron/app/sequencing/api.py:336-380` `enroll` sets it.
- `nextron/app/drafting/api.py:107-117` `request_steps` -- read the bound version.
- `nextron/app/queries/setup.py:273-305` -- `SeededSequence` and `sequence_by_id`. Add `sequence_version(version_id)`, `sequence_versions(sequence_id)` and `steps_per_sequence()`. `steps_per_sequence()` is the largest step count among `campaigns.status='active'` current versions, falling back to all Sequences, and 0 when there are none. Add `live_enrollments_by_version(sequence_id)` to `app/queries/sequencing.py`.
- `nextron/app/setup/api.py` -- `_sync_sequences` (~1146), `check_seed` (~693, which has `stored_segment_keys`), `check_campaign_references(..., sequence_steps)` (~1249), the Sequence key→id map for Campaigns (~1359), `SequenceSync`, `log_seed_sync`, and the module docstring. `nextron/app/ops/api.py` `Seeds.sync_seed`, the `nextron/main.py` seed CLI and `__main__.py` help: copy 11.5's managed mode with `stored_sequence_steps`.
- `nextron/app/setup/segment_editing.py`, `nextron/web/segments_page.py` and the `segment*.html` templates and routes in `web/app.py` -- the editor, page, refusal mapping (`form_problems`) and versions page to copy. Add the Settings link in `templates/settings.html`.

## Tasks & Acceptance

**Execution:**
- [x] Domain: add `sequence_form_diff` and `SequenceRefused`.
- [x] Migration 0044, models, owners/PII, repos:
  - bump the HEAD constants, chain probes and `test_source_tree` FILES;
  - add Enrollment insert binding.
- [x] `nextron/app/setup/sequence_editing.py` (new) -- `SequenceEditor(clock)` with `@use_case edit(sequence_id, based_on, fields)`:
  - check in this order: unknown id, stale, the Variant-step check, validation;
  - write the event, log `sequence_saved` and return `SequenceSaved`.
- [x] Seed managed mode and bootstrap: setup api, ops api, CLI and help.
- [x] Drafting `request_steps` binding, plus the queries: `sequence_version`, `steps_per_sequence` and the live-Enrollment counts.
- [x] Web, as `nextron/web/sequences_page.py` with templates:
  - `GET /sequences` lists key, name, version, steps and Campaign count;
  - `GET /sequences/{id}` shows the form and the "n active Enrollments keep version k" warning;
  - `GET /sequences/{id}/versions`;
  - `POST /sequences/{id}/edit`;
  - add the Settings link.
  - `create_app` takes `sequence_editor=`.
- [x] Tests:
  - every Matrix row;
  - migration, backfill and triggers, including the recreated enrollments bound trigger;
  - CSRF refusal;
  - the README section "Editing Sequences" in `nextron-engine/README.md`, with the Seeds section updated.

**Acceptance Criteria:**
- Given a forged or missing CSRF token on `/sequences/{id}/edit`, nothing is written.
- Given a save, `/sequences/{id}/versions` lists every stored version with its changed fields.
- Given the full suite, every contract and `lint-imports` pass.

## Implementation Notes

- **Migration** `0044_11_7_sequence_editing` (down `0043_11_5_segment_editing`):
  - It creates `sequence_versions` with checks on `version >= 1` and on `actor`, a unique (sequence_id, version), and no-update, no-delete and no-id-reuse triggers.
  - It backfills one row per Sequence (actor `seed`, `created_at` = the Sequence's `updated_at`). The definition is the canonical `sequence_form` JSON built from the `step_briefs` rows.
  - It adds `enrollments.sequence_version_id` in place with `ALTER TABLE ADD COLUMN` and a named FK, so the existing triggers and partial index stay. It backfills that column on (sequence_id, sequence_version) and recreates `trg_enrollments_bound` with the column added.
- **Domain** (`domain/sequences.py`) adds:
  - `definition_text`, the canonical JSON;
  - `SequenceRefused`;
  - `edited_field`, which turns a validator path into a form field;
  - `sequence_form_diff`, which matches Steps by number: `key` and `name` first, then each Step's fields, and a Step only one side has counts as a whole `steps[n]`.
- **Repos.** `sequences.insert(seq, at, actor)` and `update(..., actor)` write the version row. New functions: `version_row`, `versions` and `current_version_id`. `enrollments.insert(..., sequence_version_id=)` and `live_counts_by_version` are added.
- **Queries.**
  - `queries.setup` adds `sequence_version`, `sequence_versions`, `current_sequence_version_id`, `sequence_steps()` (managed-mode seed checks) and `steps_per_sequence()`.
  - `queries.sequencing` adds `live_enrollments_by_version`.
- **Editor** `app/setup/sequence_editing.py`:
  - Check order: unknown id, unreadable stored Sequence, stale, row shape (`_kept`: Step 1 kept, no reordering, appended rows last; each form row carries `stored_step`), the Variant step-angle check, then validation.
  - The fixed FR-4 fields are set by the editor. A kept Step keeps its stored `segment_angles`.
  - An identical save writes nothing.
- **Enroll** binds `current_sequence_version_id`. `Drafting.request_steps` reads the bound version; with a NULL binding it falls back to the old current-version check.
- **Seed sync.**
  - Sequences are inserted only into an empty DB (actor `seed`). Otherwise managed mode writes nothing and returns per-key differences.
  - The CLI prints the "managed in the dashboard" notice.
  - In managed mode `check_seed(..., stored_sequence_steps=)` checks Campaigns against the stored Step counts.
- **Web** (`web/sequences_page.py`, 5 templates, 4 routes, Settings link):
  - A stale refusal re-renders the current stored form with an alert.
  - An unreadable stored Sequence shows the unavailable notice.
  - `create_app(sequence_editor=)`.
- **Tests.**
  - Domain and app editor tests, page and route tests (CSRF, stale, versions, the live-Enrollment warning, unreadable).
  - Drafting bound-version tests (Step 3's brief and delay, a removed Step still served).
  - Migration 0044 test; the managed-mode stored-count Campaign check.
  - Contract pins bumped: persistence HEAD, source tree, web routes and templates, Google `PARAMETER_VALUES`, and the sequence writer contract (two writers).
- The Story 3.15 subject-access export now includes `enrollments.sequence_version_id` (it exports every column; `test_contact_export` pin bumped).
- **Extra overnight decisions:**
  - (8) The Variant-step refusal goes on the `steps` field.
  - (9) The form shows blank rows up to 6 Steps; blank appended rows are ignored.
  - (10) Managed mode still validates the seed file, as in 11.5.

## Spec Change Log

## Review Triage Log

Iteration 0. Layers: blind hunter (B, 12), edge-case hunter (E, 8), verification gap (V, 2 + 1 other). 23 raw, 20 after dedup.

| # | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | B1+E6: `steps_per_sequence()` counts current versions only; live Enrollments on an older, longer version are not counted | medium | defer | The spec defines it as "largest step count among active Campaigns' current versions" (matrix Capacity row). Its consumer, Story 4.10, should decide whether bound older versions count. The "never under-reserves" docstring wording gets narrowed (patch, row 1a). |
| 1a | Docstring/README say capacity is "never under-reserved" | low | patch | Narrow the claim to "with no active Campaign it falls back to every stored Sequence". |
| 2 | B2: `enroll` can bind NULL silently | false | reject | Every Sequence write goes through repo `insert`/`update`, which both write the version row, and 0044 backfills one row per Sequence at its current version, so the current version always has a row. |
| 3 | B3: README says a removed Step shows as a whole `steps[n]`, but removing a middle Step shows shifted field changes | low | patch | README wording; the diff matches Steps by number. Document that. |
| 4 | B4: README example order `steps[3].ask, name` vs code `name, steps[3].ask`; docstring omits `key` | low | patch | Direct text correction. |
| 5 | B5+V1: no test runs the managed-mode Campaign check against the stored Step counts | medium | patch | A grep of `stored_sequence_steps` in the tests finds no `check_seed`/`sync_seed` caller. Add a sync test that shortens stored `default` to 3, keeps the 4-Step file and a Variant `step: 4` note, and expects exit 1, the stored-count message and an unchanged dump. |
| 6 | B6+E5: a stale refusal re-renders with the submitted stale `based_on`, so every retry is refused again | medium | patch | Verified in `web/app.py` `sequence_edit`: the refused context reuses the posted rows and `based_on`. On a stale refusal, re-render the current stored form, with the stale alert telling the user the form now shows the current version. |
| 7 | B7: live-Enrollment counts group by the version number, not the bound id | low | reject | Only pre-11.7 Enrollments at a version never kept differ. Those already fail `request_steps`, and listing them needs a new branch. Rare in practice. |
| 8 | B8: `deferred-work.md` lacks Design Notes 4 and 6 follow-ups | low | patch | Append both entries. |
| 9 | B9: spec task ticks and Implementation Notes empty | low | patch | Orchestrator fills them at step 3/5. |
| 10 | B10: repo `version(sequence_id, n)` unused | low | patch | Grep finds no caller in `nextron/` or `tests/`. Delete it and its docstring line. |
| 11 | B11: `SequenceVersionRecord` reuses `SEGMENT_VERSION_ACTOR_CHECK`/`LENGTH` | low | patch | A change to the Segment actor set would silently change the Sequence ORM. Give the Sequence model its own constants with the same values. The `FORM_FIELD` import and the `SEGMENT_ONLY_*` difference texts are shared UI vocabulary, so they stay. |
| 12 | B12: the "Running Enrollment" matrix row is tested only for a Step 2 delay | medium | patch | Add a test: v1-bound Enrollment, Sequence edited to change Step 3's brief text and delay, `request_steps` for Step 3 uses v1's delay and brief. Also: after Steps are removed, a v1-bound Enrollment may still request a Step beyond the new count. |
| 13 | E1: GET `/sequences/{id}` 500s when the stored Sequence no longer validates | low | patch | Only the list page catches `STORED_ROW_REFUSED`. Catch it the same way and render the unavailable notice (11.5's pattern). |
| 14 | E2: the versions page 500s on an undecodable version row | low | patch | Same catch on `/sequences/{id}/versions`. |
| 15 | E3: the edit POST 500s when the stored Sequence no longer validates | low | patch | Catch `InvalidSequences` from `by_id` in the editor and raise `SequenceRefused({"form": ...})`. |
| 16 | E4: `request_steps` can raise `InvalidSequences` | false | reject | The pre-11.7 path (`sequence_by_id`) raised the same type for an undecodable Sequence. This is not new behaviour, and the rows are validated on write. |
| 17 | E7: a refused re-render with Remove ticked can show more than 6 rows | maybe-false | reject | Blank appended rows are dropped on submit, so a save is refused only when more than 6 rows are filled. If true it would be low. |
| 18 | E8: a forged form dropping Step 1 places the error beside the first kept row | low | reject | Only a forged form reaches it, and the UI has no Remove on Step 1. |
| 19 | V2: the "n active Enrollments keep version k" warning isn't checked through the route | medium | patch | Add a route test: enroll Contacts, edit, enroll again, GET `/sequences/{id}`, assert both lines. |
| 20 | V-other: wrong comment in `test_sync_seed_logs_the_sequences_counts` | low | patch | Fix the comment so it matches the difference the run produces. |

## Design Notes

### Decisions made overnight (pending user review)
1. **Seed bootstrap** works like 11.5: `nextron seed sync` inserts Sequences only into a DB that holds none.
2. **Edit only.** This story has no Sequence create, delete or key change. 11.8 Campaigns choose among the stored Sequences.
3. **Fields beyond the AC.** `word_comparison` and `name` are editable. `segment_angles` aren't: a kept Step keeps them, so the US/UK regulatory angles can't be lost or misplaced.
4. **Variant step angles.** A save may not drop below a step any Variant angle names. Removing a middle Step still shifts later Variant notes by one; 11.8's Variant editing is where to revisit that (deferred-work).
5. **Capacity.** `steps_per_sequence()` falls back to all stored Sequences when no Campaign is active. It does not count older versions that live Enrollments are bound to; Story 4.10 consumes it and decides (deferred-work).
6. **Grounding rules.** `domain.grounding_rules` doesn't exist yet; Story 4.13 builds it. Saved briefs always satisfy the fixed FR-4 rules. 4.13 must check against the Enrollment's bound version's word limit (deferred-work).
7. **Pre-11.7 Enrollments** bound to a version that was never kept have a NULL `sequence_version_id`. They keep the old behaviour: they work while the current version matches.

## Verification

**Commands:**
- `cd e:/Nextron-epic2/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- pass
- `uv run pytest -q tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
