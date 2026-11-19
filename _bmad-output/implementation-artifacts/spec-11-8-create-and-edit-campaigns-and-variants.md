---
title: 'Story 11.8: Create and edit Campaigns and Variants'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '821c0d56688e110cc1c0a11989469c89c375447e'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-11-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-11-7-edit-sequences-and-step-briefs.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Campaigns, Variants and Offers (FR-3) come only from seed YAML. The dashboard can activate, pause, resume and record the LIA, but it can't create a Campaign, change its Offer or Sequence, or add, edit or retire a Variant. The 4.2 decoder also refuses a Campaign with no Variant or no active one, so a fresh dashboard Campaign can't even be stored. And 11.7 left a gap: removing a middle Step silently moves later Variant angle notes onto the wrong Step.

**Approach:** Add a Campaigns page (`/campaigns`, linked from Settings) to create a Campaign at `draft`, edit it and manage its Variants. It extends the 4.2 `Campaigns` use cases and repo, and keeps the activate, pause, resume and LIA controls on the Settings panel. Variants are retired and never rewritten once they have Enrollments. Seed sync stops writing Campaigns, Variants and Offers once the DB holds a Campaign. Copy 11.5/11.7's pattern.

## Boundaries & Constraints

**Always:**
- **Create** `create_campaign(key, name, segment_id, market_id, offer_id, sequence_id)`:
  - every field is required, the key matches `KEY_PATTERN` and is unique, and the name follows the 4.2 text rules;
  - the Campaign starts `draft` with no Variants, version 1;
  - the event is `None`→`v1`, cause `operator_create`;
  - a taken (Segment, Market) pair is refused with `PAIR_TAKEN` ("A Campaign already exists for this Segment and Market").
- **Edit** `edit_campaign(id, based_on, name, segment_id, market_id, offer_id, sequence_id)`:
  - the key is fixed; a stale `based_on` is refused; an identical save writes nothing;
  - otherwise it bumps the version, with the event `v<n>`→`v<n+1>`, cause `operator_edit`;
  - a changed Segment or Market is refused when the Campaign has any Enrollment (any status): "Segment and Market can't change once Contacts are enrolled". `PAIR_TAKEN` also applies.
  - A new Sequence must cover every active Variant's note Steps. A new Market re-runs the note floor (`note_floor_problem`) on active Variants. A non-draft Campaign moving to a GB Market without an LIA is refused with `LIA_FIRST`.
  - Offer and Sequence changes reach new Enrollments only, because Enrollments already bind `offer_id`, `sequence_id` and `sequence_version_id`.
- **Variants:**
  - At most 2 **active** Variants per Campaign: a third active one is refused with "A Campaign can have at most 2 Variants". Retired Variants don't count. Names are unique per Campaign, case-insensitively, retired ones included.
  - `add_variant` (4.2) keeps its rules.
  - `edit_variant(id, variant_id, based_on, name, step_angles)` follows the `variant_from_fields` rules. A Variant with any Enrollment is refused with "Retire this Variant and add a new one, so its results stay comparable". A retired Variant can't be edited.
  - `retire_variant(id, variant_id, based_on)` sets it `inactive`, which is final. Retiring the last active Variant of a non-`draft` Campaign is refused.
  - Every Variant write bumps the Campaign version, with its own event cause. Each write is refused by `_refuse_lia_gap`, as `add_variant` is.
- **Activation:** `activate` (and `resume`) with no active Variant is refused with "Add an active Variant first", checked before the LIA. The UK LIA rule is unchanged.
- **Stored decode:** a stored Campaign may hold any number of Variants, none active included, with at most 2 active. The seed **file** validator stays unchanged (1–2 Variants, at least 1 active). `next_variant` already skips inactive Variants.
- **Migration** `0045_11_8_campaign_editing` (triggers only, no columns):
  - it replaces `trg_variants_at_most_two` with an active-only insert cap;
  - `trg_variants_retired_final` refuses inactive→active;
  - `trg_variants_enrolled_frozen` refuses changing `name`/`step_angles` of a Variant that has Enrollments;
  - `trg_campaigns_pair_frozen` refuses changing `segment_id`/`market_id` of a Campaign that has Enrollments.
- **Seed sync managed mode:** when the DB holds any Campaign, sync writes no Offers, Campaigns or Variants. It returns per-key differences, and the CLI prints "Campaigns are managed in the dashboard; seed/campaigns.yaml and seed/offers.yaml ignored". In that mode `check_seed` validates the campaign and offer files' shape only, with no reference checks. With no Campaign stored, the 4.2 behaviour is unchanged.
- **11.7 deferred fix** (`sequence_editing._check_variants`):
  - it considers only **active** Variants;
  - it also refuses removing any stored Step `k` when an active Variant has a note on Step ≥ `k`: "Campaign <key>'s Variant <name> has angle notes on Steps after a removed Step; retire it and add a new one before removing Step <k>".
- Every route is `@personal_data(False)`. Forms use `method="post"`, `hx-post`, `{{ csrf_field() }}` and `hx-sync="this:drop"`. GETs are read-only units of work. Events have entity `campaigns`, actor `operator`, and no values.

**Never:**
- Never change a running Enrollment's bound Variant, Offer, Sequence version or slots. Never rewrite or reactivate a Variant that has Enrollments.
- No Campaign delete, no key change, and no Offer editor. No change to the Playbook snapshot, the AI test gate (it keys on route tuples) or the footer (it keys on Market).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Create | valid fields | `draft`, v1, 0 Variants, event `operator_create` |
| Pair taken | existing (Segment, Market) | refused with `PAIR_TAKEN`, nothing written |
| Activate, no Variant | draft, 0 active | refused with "Add an active Variant first" |
| Third active Variant | 2 active (plus any retired) | refused with "A Campaign can have at most 2 Variants" |
| Retire then add | A enrolled, B active; retire A, add C | A `inactive`, C `active`; round-robin is B and C only; A's Enrollments keep A |
| Edit enrolled Variant | Variant has Enrollments | refused with "Retire this Variant…" |
| Retire last active | active Campaign, 1 active Variant | refused |
| Segment change, enrolled | any Enrollment | refused with "Segment and Market can't change once Contacts are enrolled" |
| Offer/Sequence change, enrolled | — | saved; existing Enrollment rows unchanged; the next enroll binds the new ids |
| Stale | based_on ≠ version | "This Campaign changed since you opened it" |
| Middle Step removal | active Variant has a note on Step 3; remove Step 2 | refused (11.7 fix) |
| Seed sync, Campaign stored | any YAML | writes nothing to offers/campaigns/variants; managed notice and differences |

</frozen-after-approval>

## Code Map

- `nextron/domain/campaign.py` -- `campaign_from_form`, `_variants`, `variant_from_fields`, `note_floor_problem`, `CampaignRefused` and the message constants:
  - add a stored-decode mode (any number of Variants, ≤2 active);
  - add `campaign_fields_problems`, the create/edit field validation;
  - add the new messages and causes.
- `nextron/db/repos/setup/campaigns.py` -- `_stored` decodes through the stored mode:
  - add `update_definition` (name, segment, market, offer, sequence, version) and `set_variant_status`;
  - `insert` accepts zero Variants.
- `nextron/db/alembic/versions/0021_4_2_campaigns.py` -- the existing triggers. Copy `0044_11_7_sequence_editing.py` for the header/test pattern. Bump the HEAD constants, chain probes and `test_source_tree` FILES.
- `nextron/app/setup/api.py`:
  - the `Campaigns` class (~1571) and `add_variant` (~1663) to extend;
  - `_sync_offers`/`_sync_campaigns` (~1254/1403) get managed mode; the stored-Campaign update branch (`_variant_writes`, `_refuse_held_pairs`) becomes dead, so remove it;
  - extend `check_seed` (~720) and `log_seed_sync`;
  - `SequenceSync`/`SequenceDifference` are the shapes to copy.
- `nextron/app/ops/api.py` `Seeds.sync_seed`, `nextron/main.py` (~2844 managed notices) and `nextron/__main__.py` help -- the managed mode, copied from 11.7.
- `nextron/app/queries/sequencing.py` -- add `enrollment_counts(campaign_id) -> {variant_id: n}` (all statuses). Setup reads Enrollments only through it (AD-3; check `lint-imports`).
- `nextron/app/setup/sequence_editing.py` `_check_variants` -- the 11.7 deferred fix.
- `nextron/web/sequences_page.py` and `segments_page.py`, the `sequence*.html` templates, and the `web/app.py` routes (~955–1110) -- the page pattern. `web/campaigns_page.py` and `campaigns_panel.html` -- the 4.2 Settings panel; change its empty text to point at `/campaigns`. `templates/settings.html` -- add the link.
- `tests/app/test_campaign_sync.py` -- 4.2 tests; rewrite those for the removed update path, and update the 11.7 managed stored-count test.

## Tasks & Acceptance

**Execution:**
- [x] Domain: the stored-decode mode, field validation, messages and causes, with unit tests.
- [x] Migration 0045, its tests, the contract pins and the repo functions.
- [x] `Campaigns` use cases: `create_campaign`, `edit_campaign`, `edit_variant`, `retire_variant`, plus the activate/resume no-Variant check. Add the `enrollment_counts` query.
- [x] Seed managed mode for Offers/Campaigns: setup api, ops, CLI and help.
- [x] The 11.7 `_check_variants` fix, with tests.
- [x] Web (`nextron/web/campaign_editor_page.py` plus templates):
  - `GET /campaigns` shows the list and the create form;
  - `GET /campaigns/{id}` shows the edit form, and the Variants with their Enrollment counts, edit/retire forms and the add form;
  - the POSTs are `/campaigns/create`, `/campaigns/{id}/edit`, `/campaigns/{id}/variants/add`, `/campaigns/{id}/variants/{vid}/edit` and `/campaigns/{id}/variants/{vid}/retire`;
  - when enrolled, Segment and Market show read-only with the reason;
  - add the Settings link; `create_app` keeps taking `campaigns=`.
- [x] Tests: every Matrix row, CSRF refusal, the routes, and the README section "Editing Campaigns" (update the Seeds section).

**Acceptance Criteria:**
- Given a forged or missing CSRF token on any new POST, nothing is written.
- Given the full suite, every contract and `lint-imports` pass.

## Design Notes

### Decisions made overnight (pending user review)
1. **"At most 2 Variants" counts active Variants.** Retired Variants stay as history. Otherwise "Retire this Variant and add a new one" would be impossible in a 2-Variant Campaign. Retirement is final.
2. **A new Campaign has no Variants.** Variants are added on its page. Activation needs an active Variant. A running (non-draft) Campaign can't retire its last active Variant.
3. **Variant edits** (name or notes) are allowed only before the Variant's first Enrollment. After that, the only change is retire. DB triggers back this up.
4. **Offers** have no editor (no story asks for one). They are frozen together with Campaigns once the DB holds any Campaign.
5. **No Campaign version table.** Enrollments already bind the Variant, Offer and Sequence version, and the `events` rows record each version bump.
6. **11.7 deferred fix:** a middle Step removal that would shift an active Variant's notes is refused, rather than remapping the notes. Remapping would rewrite enrolled Variants. Retired Variants no longer block Sequence edits: they get no new Enrollments, and running ones read their bound version.
7. **Key** is typed at create and fixed afterwards.

### Merge touchpoints
- 4.12 Drafts must read the Enrollment's bound `variant_id`/`offer_id`/`sequence_version_id`, never the Campaign's current values.
- 4.10 capacity: `steps_per_sequence()` follows a Campaign's current Sequence.
- 11.2 Variant comparison: a Campaign can now have more than 2 Variant rows (retired ones included).

## Implementation Notes

- **Domain** (`domain/campaign.py`): `stored_campaign_from_form` (any number of Variants, at most 2 active, names unique), `campaign_form_diff` (managed-mode paths), `campaign_fields_problems(name, key=None)`, `variant_from_fields(..., active_count=None)` (names checked against every Variant, retired included; the cap against the active count), the causes `operator_create`, `operator_edit`, `operator_edit_variant`, `operator_retire_variant` and the messages (`NEEDS_VARIANT_FIRST`, `PAIR_FROZEN`, `VARIANT_ENROLLED`, `VARIANT_RETIRED`, `LAST_ACTIVE_VARIANT`, `STALE_CAMPAIGN`, `KEY_TAKEN`, `CHOOSE_ONE`, `NO_SUCH_VARIANT`). The seed file validator is unchanged.
- **Migration** `0045_11_8_campaign_editing` (down `0044_11_7_sequence_editing`), triggers only: `trg_variants_at_most_two` recreated as an active-only insert cap; `trg_variants_retired_final`; `trg_variants_enrolled_frozen` (name/step_angles of a Variant any Enrollment names); `trg_campaigns_pair_frozen` (segment_id/market_id of a Campaign any Enrollment names). HEAD pins, chain probes, trigger sets and `test_source_tree` FILES bumped.
- **Repos**: `campaigns._stored` decodes through the stored form; new `keys()`, `ids_for_pair()`, `update_definition(..., clear_lia=False)` (with `clear_lia` it also sets `lia_ref`/`lia_date` NULL), `set_variant_status()`. `enrollments.counts_by_variant(campaign_id)` (every status), read by setup only through `queries.sequencing.enrollment_counts`. `queries.setup.campaign_keys()`.
- **Use cases** (`app/setup/api.py` `Campaigns`): `create_campaign`, `edit_campaign`, `edit_variant`, `retire_variant`; `add_variant` gains an optional `based_on` and caps active Variants only; `activate`/`resume` refuse "Add an active Variant first" before the LIA. Check order of `edit_campaign`: unknown id, stale, LIA gap, field problems (collected), identical (no write), pair frozen, pair taken, a non-draft Campaign's pair change ending in a GB Market (`{form: LIA_FIRST, market: UK_PAIR_DRAFT_ONLY}`, even when only the Segment changed), Sequence covers active notes, Market floor. A Segment or Market change clears the stored LIA in the same `update_definition` call, so a draft UK Campaign asks for a new one at activation. References are row ids; blank is "Choose one", unknown "No stored <kind> has this id".
- **Seed managed mode**: `Setup.seed_sync` decides managed once (`checked.campaigns_managed or bool(campaigns_repo.keys())`, so Campaigns whose references weren't checked are never inserted); `_sync_offers`/`_sync_campaigns` then write nothing and return `OfferDifference`/`CampaignDifference` lists (`OfferSync`/`CampaignSync` gain `managed`, `differences`). `check_seed(..., campaigns_managed=)` skips the Campaign reference checks (shape only). The dead update path (`_variant_writes`, `_refuse_held_pairs`, `KEEPS_SEGMENT_AND_MARKET`) is removed. `log_seed_sync` adds `managed`/`differences_count` to `offers_synced`/`campaigns_synced`. `Seeds.sync_seed` passes `campaigns_managed`; the CLI prints both blocks as "<offers|campaigns>: Campaigns are managed in the dashboard; seed/campaigns.yaml and seed/offers.yaml ignored" plus a line per difference; help updated.
- **11.7 fix** (`sequence_editing._check_variants(sequence_id, kept, stored_steps)`): only active Variants; after the existing Step-count check, refuses removing stored Step k when an active Variant has a note on a Step >= k (smallest k named).
- **Web** (`web/campaign_editor_page.py`, templates `campaign_list.html`, `campaign_list_panel.html`, `campaign.html`, `campaign_edit_panel.html`; 7 routes in `web/app.py`; Settings link; the Settings panel's empty text points at `/campaigns`). Stale refusals re-render the stored Campaign with an alert. Enrolled: Segment/Market read-only, sent as hidden fields. A stored row that no longer decodes shows the unavailable notice: on the full list page (`campaign_list.html`, base layout) for the full-page GETs, else the list panel; `campaign_editor_run` and `campaign_create` catch `STORED_ROW_REFUSED` from the use case and render that panel in a read-only unit of work (no 500). Retire carries `hx-confirm` ("Retire Variant <name>? Retiring is final."; no web contract forbids it) and is hidden on a non-draft Campaign's only active Variant, which shows the `LAST_ACTIVE_VARIANT` reason instead; with 2 active Variants the add form is replaced by "A Campaign can have at most 2 Variants". `_angle_rows` pads with the unused indices of 1..`MAX_STEP_ANGLES`, ascending. `create_app` keeps taking `campaigns=`.
- **Tests**: `tests/app/test_campaign_editing.py` (matrix rows, triggers, 11.7 fix including a note on exactly the removed Step, enrollment counts, the LIA cleared by a draft UK pair change, a running UK Segment change and a running US→GB move refused), `tests/app/test_campaign_editor_page.py` (routes, CSRF on all 5 POSTs, malformed edit and Variant forms, stale, enrolled view, a refused Variant edit inside its own form, the hidden Retire and add controls, a direct retire of the last active Variant refused, sparse note rows re-rendered as 1..6, undecodable rows on GETs and POSTs), `tests/app/test_campaign_sync.py` rewritten for managed mode (the update-path tests now assert differences and no write; a new and a renamed Offer are listed), contract pins.
- **Extra overnight decisions:**
  - (8) Offers sync managed mode is decided by the same "any Campaign stored" test as Campaigns, and the CLI prints the one shared notice on both blocks.
  - (9) `edit_campaign` and the Variant commands also run the 4.2 LIA-gap refusal first (the DB triggers would refuse any write to such a row anyway).
  - (10) `add_variant` takes an optional `based_on` so the page's add form is stale-checked; without it the 4.2 behaviour is unchanged.
  - (11) A Segment or Market change clears the stored LIA, and a running Campaign can't move into a UK pair (review row 1).

## Spec Change Log

## Review Triage Log

Iteration 0. Layers: blind hunter (B, 14), edge-case hunter (E, 7), verification gap (V, 3 + 0 other). 24 raw, 20 after dedup.

| # | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | B1+E1: a Segment/Market change keeps the stored LIA, which was made for the old pair; a running US→GB move is refused with `LIA_FIRST`, but `record_lia` refuses a non-GB Campaign, so the operator has no way forward | medium | patch | Verified in `_edit_problems`: `update_definition` never touches `lia_*`. Fix: a Segment or Market change clears the stored LIA. A non-draft Campaign whose edit changes the pair and ends in a GB Market is refused with `{form: LIA_FIRST, market: "Only a draft Campaign can move to a new UK Segment or Market: its assessment must be made for the new pair"}`. That is still the frozen `LIA_FIRST` refusal, with the reason named. Overnight decision 11. |
| 2 | B2+E5: `create_campaign` has no 50-Campaign cap | low | reject | Stored decode doesn't depend on the count. Reaching 50 hand-made Campaigns is unlikely, and the fix adds a guard. |
| 3 | B3+E6: `seed_sync` recomputes `managed` and ignores `checked.campaigns_managed` | low | patch | The bad case needs a stored Campaign to disappear between check and sync, and there is no delete. Direct fix: `managed = checked.campaigns_managed or bool(campaigns_repo.keys())`, so references unchecked at check time are never inserted. |
| 4 | B4: once managed, no new Offer can reach the DB | low | reject | This is the recorded overnight decision 4, pending Mac's review. Changing it means changing the spec. |
| 5 | B5+E3: GET `/campaigns/{id}` with an undecodable row renders the bare list panel | medium | patch | Verified in `campaign_editor_answer`: the except branch always uses `LIST_PANEL_TEMPLATE`. A full-page GET must render the full list page with the unavailable notice. Add a test. |
| 6 | E2: a POST whose use case hits an undecodable stored row raises a 500 | medium | patch | `campaign_editor_run` and `campaign_create` catch only `CampaignRefused`. Catch `STORED_ROW_REFUSED` and render the unavailable panel, with a test. |
| 7 | B6: Retire is a one-click, irreversible POST | low | patch | Add `hx-confirm` (a direct attribute). If a web contract forbids it, drop it and log that. |
| 8 | B7: Retire shows on the last active Variant of a running Campaign, and Add shows with 2 active Variants, though both can only be refused | low | patch | Template conditions. Show the reason in place of the control, as `frozen_reason` does. |
| 9 | B8: `create_campaign` passes a `CampaignDef` with blank references to `insert` | low | reject | `insert` reads only key, name and variants. No caller is harmed today. |
| 10 | B9: `add_variant` looks up the Sequence by key, `edit_variant` by id; both fall back to 0 Steps | false | reject | The `fk_campaigns_sequence_id_sequences` FK guarantees the Sequence exists, and the key and id name the same row. |
| 11 | B10: `object`-typed callbacks force `type: ignore` | low | reject | Typing only. No runtime harm, and no type checker runs here. |
| 12 | B11: `_references` reads whole Market/Offer tables | low | reject | Tiny tables; performance only. |
| 13 | B12: `key` is a dead path in `campaign_form_diff` | false | reject | The function is general: it compares any two `CampaignDef`s, and `key` can differ for any caller. |
| 14 | B13: a trigger race (Enrollment inserted between the count check and the update) becomes a 500 | false | reject | Use cases run under `BEGIN IMMEDIATE`, so no other writer can insert an Enrollment inside the transaction. The decode-failure test gap is covered by rows 5–6. |
| 15 | B14: README line far past the wrap width | low | patch | Re-wrap. |
| 16 | E4: `_angle_rows` pads from the last submitted index, so sparse rows give indices > 6 and the resubmitted form is a 400 | low | patch | Verified in `_angle_rows` (`last + n`). Pad with the free indices in 1..6. |
| 17 | E7: `based_on` 2.0 passes the stale check | low | reject | The web layer always parses an int; only a direct caller passing a float could hit it. |
| 18 | V1: no test puts a note exactly on the removed Step | medium | patch | Add the case (note on Step 2, remove Step 2, 4 stored) expecting the refusal. |
| 19 | V2: no web test of a refused Variant edit, a refused retire, or a malformed Variant form | medium | patch | Add the three tests. |
| 20 | V3: Offer "only in the file" and "differs in name" difference lines are untested | low | patch | Add a managed-sync test case. |

## Verification

**Commands:**
- `cd e:/Nextron-epic2/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- pass
- `uv run pytest -q tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
