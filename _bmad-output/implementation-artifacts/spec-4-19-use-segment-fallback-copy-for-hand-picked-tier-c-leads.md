---
title: 'Story 4.19: Use Segment fallback copy for hand-picked Tier C Leads'
type: 'feature'
created: '2026-11-20'
status: 'in-progress'
baseline_commit: 'edd6b523f0eaf3799c9109c18e61975dce6cac57'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A hand-picked Tier C Lead (no live Tier 1–2 Event) today goes to Opus, which must cite a live Event Signal, so the proposal is always discarded (`invalid_proposal`, deferred from 4.15 BH3) — or worse, tempts invented specifics. FR-22 wants the Segment's fallback template instead, flagged `generic` for closer review.

**Approach:** A new `seed/fallback_templates.yaml` loads through `nextron seed sync` into a new `fallback_templates` table (owner `setup`). At drafting time, when `domain.scoring.tier` of the Lead is `C`, code fills the template (no AI call), stores revision 1 (origin `generated`) with its fact versions and no Evidence or Signal, and sets the existing `drafts.generic`. The grounding rules and the AI check still run, with `signal_count` ("exactly one Signal") waived and the waiver stored on the grounding result. The Lead page's Drafts section shows a "generic" badge.

## Boundaries & Constraints

**Always:**
- Placeholders are exactly `{first_name}`, `{company}`, `{role}`, `{fact:<fact_id>}`; any other `{...}` (or a stray `{`/`}`) fails seed sync naming file and key; Step 1 needs a subject; Steps 2+ store none (NULL), and their filled revision takes the filled subject of the Step 1 template of the same Segment and variant (the thread's subject). A `{fact:id}` must be a stored current fact allowed (`queries.playbook.allowed_facts`) for the template's Segment and **every** Market whose `english_variant` equals the template's (a variant no Market uses fails sync).
- The per-Step decision lives in ONE function, `nextron.app.drafting.fallback.generic_copy(enrollment, now) -> bool` (Tier C per `domain.scoring.tier` over the Lead's current Signals at `now`), and the template write in ONE function `fallback.write_generic(draft, enrollment, contact, now, urgency) -> StepDraftOutcome-like result`, usable for any Step. Step 1 (`StepDrafts._prepare`) calls them after the AD-16 gate and before the route lookup; a generic Step 1 makes no `llm.run` call and needs no active `draft_sequence` tuple.
- Filled revision: `Traceability(evidence_ids=(), fact_versions=<first-appearance (fact_id, version)>, cited_signal_id=None, route_tuple_id=None, playbook_sha256=None, playbook_version=None, prompt_version=None, instruction=None)`; claims = one `Claim(wording, None, (fact_id, version))` per distinct fact placeholder, so citation rules run.
- `_append` gains `generic: bool | None = None`: `True` (template) / `False` (route-made) write `drafts.generic` when it differs; `None` keeps the Draft's flag, except an origin `regenerated` with a `route_tuple_id` clears it (so Story 4.20's route-made regenerate clears `generic` without code of its own). The rules' waiver comes from the revision's generic-ness (`generic` resolved as above).
- `generic` and its waiver: `RulesContext.waived_rules: frozenset[str]` (default empty); `RulesResult.waived: tuple[str, ...]` lists only rules that applied and were waived (`signal_count` on Step 1). `grounding_results.waived_rules` (JSON array text, default `'[]'`) stores it; the AI-check rerun in `grounding.py` re-uses the stored row's `waived_rules`. `RULES_VERSION` → 2.
- Contact gate (AD-16) before any template write, exactly as Step 1 does; failures use existing `DraftFailure.NOT_COMPOSABLE` (no template for Segment/Step/variant, a missing `first_name`/`company`/`role` value a template uses, a fact no longer allowed) with a log `generic_draft_failed` carrying a `detail` code — no change to the `drafts` CHECK.
- No new job type (runs inside `draft_step`); no new dependency; migration `0066_4_19_fallback_templates` (creates the table; adds `grounding_results.waived_rules`).

**Never:** call the LLM for a Tier C Draft; invent or paraphrase specifics; touch `queue.html` / `QueueCard` (Story 5.3 owns the queue card's `generic` badge, `badge-generic`); add a column other than reusing `drafts.generic`; send email; reach a real service.

### Decisions made overnight (pending user review)
1. Seed sync always syncs `fallback_templates` from the file (no dashboard editor exists; 11.5's managed mode is for Segments only). The file is the full set: rows absent from the file are deleted, changed rows updated, by natural key (Segment key, Step, English variant). Templates are checked against the **stored** Segment keys in managed mode (`checked.segment_keys`). Missing file → seed sync fails like the other six.
2. "Markets the template serves" = every Market in this sync whose `english_variant` equals the template's.
3. The template's English variant at drafting time is the Campaign Market's (as 4.15's framing); `{company}` is the Lead's name, `{first_name}` the first whitespace token of the Contact's name, `{role}` the Contact's role.
4. Template body in YAML is a list of paragraphs joined with a blank line (the seed YAML subset refuses multi-line text).
5. Steps 2–4 get a filled subject from the Step 1 template of the same Segment/variant (follow-ups share the thread subject).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Tier C Step 1 | Lead with no live Tier 1–2 Event, template exists | rev 1 `generated`, facts only, no Evidence/Signal, `drafts.generic=1`, grounding job enqueued, no LLM call | N/A |
| Tier A/B Step 1 | live Tier 1 Event | 4.15 Opus path unchanged; `generic` stays 0 | N/A |
| No template | Tier C, no row for Segment/Step/variant | `draft_failed` `not_composable`, log detail `no_template` | no call |
| Missing value | template uses `{role}`, Contact role NULL | `draft_failed` `not_composable` (`missing_value`) | no call |
| Blocked subject | suppressed Contact | `blocked` events row, nothing written | as 4.15 |
| Generic grounding | generic Step 1 rev | rules skip `signal_count`, `waived_rules=["signal_count"]`; AI check still runs | fail closed as 4.14 |
| Bad seed | `{email}` placeholder or disallowed fact | seed sync exits 1 naming `seed/fallback_templates.yaml` and key; nothing loaded | rollback |
| Edit of generic | Operator `revise` | keeps `generic=1` and the waiver | N/A |

</frozen-after-approval>

## Code Map

- `nextron/app/drafting/generation.py` -- `StepDrafts._prepare`: insert generic branch after the AD-16 gate, before `active_tuple`. `_apply` passes `generic=False`.
- `nextron/app/drafting/api.py` -- `_append`/`_prepare`/`_grounding`: add `generic` resolution, `waived_rules` into `RulesContext`, store `waived` on the `grounding_results` insert; `revise`/`recompose_ready` keep the flag (None).
- `nextron/app/drafting/grounding.py` -- AI-check rerun calls `_grounding`; pass the stored row's `waived_rules`.
- `nextron/domain/grounding_rules.py` -- `RulesContext.waived_rules`, `RulesResult.waived`, skip `signal_count` when waived; bump `RULES_VERSION`; docstring.
- `nextron/domain/scoring.py` -- `tier(events, now)` (reuse; never duplicate). `nextron/app/scoring/api.py:scoring_signals` builds `ScoringSignal`s from `queries.research`; drafting may not import `app.scoring` (c6 DAG) — move the builder to `nextron/app/queries/research.py` as `scoring_signals(lead_id)` and make `app.scoring.api.scoring_signals` delegate (one definition).
- New `nextron/domain/fallback_copy.py` -- placeholder parsing/validation, YAML entry validation (`validate_fallback_templates`, `InvalidFallbackTemplates`), `fill(...)`; stdlib + `nextron.domain` only.
- New `nextron/app/drafting/fallback.py` -- `generic_copy`, `write_generic` (the merge points for 4.18 / 4.20).
- `nextron/db/models/setup.py` (or where `segments` model lives), new `nextron/db/repos/setup/fallback_templates.py`, `nextron/app/queries/setup.py` (`fallback_template(segment_id, step, english_variant)`), `nextron/app/setup/api.py` (check + sync), `nextron/app/ops/api.py` `Seeds.sync_seed` (7th argument; fact check after `seed_facts` in the same transaction via `queries.playbook.allowed_facts`), `nextron/main.py` seed CLI (7th file, `format_seed_sync`).
- `nextron/db/models/drafting.py`, `nextron/db/repos/drafting/grounding_results.py` -- `waived_rules`.
- Migration `nextron/db/alembic/versions/0066_4_19_fallback_templates.py` (down `0065_4_16_draft_when_ready`); `grounding_results` rebuild with `copy_from` (follow 0059's pattern) or plain `add_column` with `server_default '[]'` + `json_valid` check. Bump HEAD in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`, chain probes, `test_source_tree` `FILES`; `owners.py` (`fallback_templates` → setup; `waived_rules` → drafting), `pii_registry.py` (all NONE; template text is Nextron copy).
- `seed/fallback_templates.yaml` -- 2 Segments × 4 Steps × {en_us, en_gb}; obey Step briefs (Step 1 <80 words, subject <7 words, one CTA "Worth a look?"-style relevance question, no link/price/audit; Steps 2–3 ≤70, Step 4 ≤50); facts only where allowed for every Market of the variant.
- `nextron/web/leads_page.py`, `nextron/web/templates/lead_drafts.html` -- `generic` badge (`<span class="badge-generic">generic</span>`).
- `tests/contracts/test_ad16_callers.py` -- scenario: generic path gated.
- `nextron-engine/README.md` -- story notes.

## Tasks & Acceptance

**Execution:**
- [ ] `nextron/domain/fallback_copy.py` + unit tests -- placeholders, validation, fill.
- [ ] migration, model, repo, owners, pii, HEAD bumps -- schema.
- [ ] setup check/sync + ops `Seeds` + CLI + seed file + tests (`tests/app/test_seed_sync.py`, new `tests/app/test_fallback_template_sync.py`).
- [ ] `queries.research.scoring_signals` move; `drafting/fallback.py`; `generation.py` branch; `api.py` generic + waiver; `grounding.py` waiver reuse; `grounding_rules.py` -- tests in `tests/app/test_step_drafting.py`-style new `tests/app/test_generic_drafting.py` (Tier C Step 1 with a fake LLM asserting zero calls; Steps 2–4 through `write_generic` directly; Tier A unchanged; failure rows; edit keeps flag; a `regenerated` route-made `_append` clears it) and `tests/unit/test_grounding_rules.py` (waiver).
- [ ] Lead page badge + test; AD-16 scenario; README.

**Acceptance Criteria:**
- Given `seed/fallback_templates.yaml`, when seed sync runs, then `fallback_templates` holds per Segment, Step and English variant a subject (Step 1) and a body, and an unknown placeholder or a fact not allowed for the Segment and every served Market fails the sync with nothing loaded.
- Given an Enrollment whose Lead is Tier C at drafting time, when Step 1 drafting runs, then no AI call is made and revision 1 (`generated`) holds the filled text, its fact versions, no Evidence or cited Signal, and `drafts.generic = 1`.
- Given a generic Draft, when it's checked, then all other rules and the AI check run and `grounding_results.waived_rules` records `signal_count`.
- Given the Lead page, when it renders a generic Draft, then it shows a "generic" badge.

## Implementation Notes

**Merge touchpoints (record for the main session):**
- Story 4.18 (`640bb27`): before adding a follow-up Step to the afternoon batch (`StepDrafts._follow_up_context` / `DraftingBatches`), call `fallback.generic_copy(enrollment, now)`; when true, call `fallback.write_generic(...)` instead of batching. `draft_sequence` v2 is irrelevant to the template path.
- Story 4.20 (track D): `regenerate` must call `fallback.generic_copy` first (a still-Tier-C Lead regenerates from the template, `generic` stays); a route-made `regenerated` revision clears `generic` automatically via `_append`.
- Story 5.3 (track E): its `QueueCard.generic` reads this same `drafts.generic` column (exists since 4.9); no new column here.
- Migration 0066 renumbered at merge.

## Spec Change Log

## Review Triage Log

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q -n 2 --basetemp="e:\Nextron AI outreach\_handoff\scratch\track-B\pt" <touched test files>` -- pass
- `uv run pytest -q -n 2 --basetemp=... tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
