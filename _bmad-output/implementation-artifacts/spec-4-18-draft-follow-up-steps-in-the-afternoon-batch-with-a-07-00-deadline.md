---
title: "Story 4.18: Draft follow-up Steps in the afternoon batch with a 07:00 deadline"
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '8318a90135adb77a9f16e6d6a1f7ffc777f9c3d9'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Steps 2–4 Draft slots (4.9) never get text: 4.15 drafts Step 1 only, and 4.17's batches carry research work only, so no follow-up exists to approve or send when it falls due.

**Approach:** `request_steps` queues one `pending` drafting batch item per Enrollment for Steps 2–4; the daily `submit_bulk_ai` sends it (built from Step 1's current revision, the Lead's Evidence, the allowed facts and the Step 2–4 briefs) on a new `draft_sequence` candidate that can write follow-ups; `poll_ai_batches` validates and applies results through `drafting`'s `apply_draft_sequence_result`, and re-runs any item not applied by `draft_deadline` (07:00) as a direct call.

## Boundaries & Constraints

**Always:**
- **New candidate** `draft_sequence` **v2**, first in `CANDIDATES` (v1 kept unchanged; a shipped candidate never changes): same model/effort/batchable/max_tokens/budget class/playbook/fallbacks as v1, prompt `prompts/draft_sequence/v2.md`, schema `DraftSequenceV2` (schema 2): `steps[]` of `{step, subject, body, claims[] (as v1), angle_fact_id|null, angle_fact_version|null}`, `cited_signal_id|null`, `explanation` (≤200). One prompt serves both modes; the framing brief names the Steps to write. Manifest line, schema lock, `SHIPPED_CANDIDATES`, docs.
- **Step 1 on v2:** `StepDrafts` and the drafting smoke scorer read the answer by the tuple's schema version: v1 as today; v2 needs exactly one `steps` entry with `step` 1, then the same checks as v1 (its angle fact is ignored).
- **Follow-up mode needs a schema-2 tuple.** No active `draft_sequence` tuple, or one on schema 1: Drafts 2–4 `draft_failed` (`no_route`), item `stale`, no call.
- **Queue:** `request_steps`, when it creates any of Steps 2–4 slots, queues in the same unit `ai_batches.queue_item(BatchOwner.DRAFTING, route=draft_sequence, target_id=<enrollment id>, rev=<next rev for that target>, context_id=None)`.
- **Input** (domain builder, all text masked): framing brief = the Step 2, 3 and 4 briefs of the Enrollment's bound Sequence version (with each Segment × Market angle, e.g. ABA 512 / SRA notice for Law-PrivateAI Step 2) plus "write Steps 2, 3, 4"; facts = `allowed_facts`; note = Variant angle notes for Steps 2–4 + English variant. Blocks: Step 1's current revision (subject and body) as `<untrusted source="earlier_step" id="step_1">`, then the Lead's live Evidence, Signals and Contact blocks exactly as Step 1.
- **Validate** (pure domain): exactly Steps 2, 3, 4 once each; every body non-empty and passes `_check_text` with Step 1's current subject; every claim cites one input Evidence id or input `(fact_id, version)`; every Step has an angle fact that is an input `(fact_id, version)`, differs from every fact Step 1's current revision cites and from the other follow-ups' angle facts; a Step whose brief has an angle for the Campaign's Segment × Market takes a `regulation` fact. Else discard: Drafts 2–4 `draft_failed` (`invalid_proposal`). Word limits and other rule breaks are left to `grounding_rules` (flagged, not discarded).
- **Fingerprint** (`ai_batch.Fingerprint`): the tuple key; `(step1 draft id, current_rev)`; each live Evidence id sent; each allowed `(fact_id, version)`.
- **`DraftingBatches(clock, llm, queue, follow_ups)`** (new `app/drafting/batches.py`, owner `DRAFTING` only), registered with `ResearchBatches` in `build_app`'s batch owners, same `submit_bulk()` / `poll()` / `schedule()` shape:
  - **submit:** per Enrollment only the newest pending item survives (older → `stale`). Enrollment ended → `stale`; paused, or Step 1 without a revision → stays `pending`; no follow-up tuple → as above. Else built now; one batch per tuple key; no budget check (`essential`).
  - **`apply_draft_sequence_result`** (in the item's transaction): Enrollment `active`, Contact passes `assert_contactable(ContactSubject, "email", "cold")`, fingerprint recomputed now equals the stored one, then validate; for each of Drafts 2–4 still `ready` without a revision, `Drafting._append(..., GENERATED, Traceability(cited Evidence/facts in first-appearance order, no cited Signal, tuple id, Playbook SHA/version, prompt version), claims=..., urgency=SOON)` with Step 1's current subject → `applied` (clears `draft_failed_*`). Fingerprint mismatch or Enrollment paused → `stale` and a new `pending` item (rev+1), or the direct job if the deadline has passed. Ended Enrollment, blocked/erased Contact, no open Draft → `stale`, nothing written.
  - **poll:** like `ResearchBatches` (`LlmInvalid` → `failed` + `draft_failed` `invalid`; refused/truncated/lost/missing → `rerun` + direct job), but a batch whose items are all terminal is still fetched until `ended` before `delete_batch`. Then the **deadline sweep:** every open drafting item whose deadline ≤ now, whose Enrollment is `active` and whose Step 1 has a revision → `stale` and the direct job (an ended Enrollment's item → `stale`; a paused one, or one with no Step 1 revision, is left alone).
  - **Deadline:** setting `draft_deadline` (TIME, default 07:00, `timezone_key` sender_timezone, appended last). An Enrollment's deadline is the first `draft_deadline` after the first `bulk_ai_submit_at` slot after its `created_at` (pure domain function).
- **Direct job:** `draft_step:<enrollment>:2` (payload `step` 2), interactive lane, `soon`; `StepDrafts.draft(enr, 2, urgency)` drafts Steps 2–4 in one direct call (with server fallbacks) using the same builder, validation and apply (no fingerprint); failures set `draft_failed_*` on Drafts 2–4 as Step 1 does; Steps 3 and 4 alone stay `skipped`.
- AD-16 caller `drafting.batch_apply` with a scenario; row-owner contract keeps `app/drafting/` on `DRAFTING`.

**Never:** sending email; storing prompt or answer text; a call that waits on a batch; fallbacks on a batch request; a migration; changing v1's prompt or schema; budget checks on drafting; Regenerate (4.20); stuck-batch health line or in-flight cost budgeting (deferred, 4.17); a test reaching Claude.

### Decisions made overnight (pending user review)
1. Follow-ups need a new `draft_sequence` v2 candidate: one route tuple can't hold two schemas, and a shipped candidate never changes. Until a v2 tuple passes the 4.5 gate, follow-ups fail `no_route` (as Step 1 does without a tuple).
2. "Operator time" is `sender_timezone`, as `bulk_ai_submit_at` and `backup_time` use.
3. The deadline is checked by the 15-minute `poll_ai_batches` (AD-10's trigger list is closed), so "at 07:00" means the first poll at or after 07:00.
4. A deadline re-run marks the item `stale` (so a later batch result writes nothing) and runs as a `draft_step` job at `soon`.
5. Step 1's "angle" is the set of facts its current revision cites; Step 4 must also carry an angle fact (the AC says each follow-up).
6. A Law Step 2 angle that isn't a `regulation` fact is discarded (fail closed).
7. Follow-up revisions store Step 1's current subject (follow-ups reply in Step 1's thread); they cite no Signal.
8. Step 1's text enters the prompt as an untrusted block: it is model output and may be Operator-edited.
9. A paused Enrollment keeps its item pending at submit and requeues it at apply; an ended one makes it `stale`.
10. Apply is per Draft: a Draft 2–4 that already has a revision or isn't `ready` is skipped; none open → `stale`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Enroll | `enroll` | Step 1 job + one pending drafting item | N/A |
| Submit | Step 1 has rev, v2 active | one batch, item `submitted`, request has no fallbacks | Step 1 no rev → stays pending |
| Applied | ended, valid answer | rev 1 on Drafts 2–4, `check_grounding` at soon each, `applied` | N/A |
| Repeated angle | Step 3 angle = Step 2's or Step 1's fact | discarded, `invalid_proposal`, `failed` | N/A |
| Law UK | law_private_ai × UK | framing names the SRA angle; non-regulation Step 2 angle discarded | N/A |
| Fingerprint | Step 1 edited after submit | `stale`, new pending item | deadline passed → direct job |
| Deadline | frozen clock 07:00, batch in progress | item `stale`, `draft_step:<enr>:2` enqueued; later result writes nothing | N/A |
| Stopped | Enrollment stopped before results | `stale`, no revision | N/A |
| Blocked | Contact suppressed | `stale`, no revision | N/A |
| v1 tuple | only v1 active | Drafts 2–4 `no_route`, no call | N/A |

</frozen-after-approval>

## Code Map

- `nextron/app/drafting/api.py` -- `request_steps` (:432; queue the item when a Step 2–4 slot is created), `_append` (:532), `Traceability`, `_check_text`.
- `nextron/app/drafting/generation.py` -- `StepDrafts`; `_prepare`/`_framing`/`_apply` to share with the follow-up builder; `step != FIRST_STEP` skip (:210) becomes the follow-up path for step 2.
- `nextron/domain/step_drafting.py` -- `framing`, `brief_section`, `input_blocks`, `read_proposal`, `validate_proposal`, `claims_of`, `citations_of`, `mask`; add the follow-up builder, v2 readers, follow-up validation, fingerprint inputs, deadline function (or in `domain/ai_batch.py`; `domain.backup.latest_slot` helps).
- `nextron/app/research/batches.py` -- the template for `DraftingBatches` (submit grouping, `_poll`, `_settle`, `schedule`); `nextron/db/ai_batches.py` (`queue_item`, `pending`, `settle`, `mark_submitted`, `items_of`, `open_batches`, `mark_ended`, `mark_deleted`); add an "open items of owner" read if needed (owner-scoped).
- `nextron/adapters/ai/routes.py` (:202), `schemas.py` (:182, `SCHEMAS`), `prompts/draft_sequence/v1.md` (base for v2), `manifest.lock`; `tests/contracts/test_prompt_manifest.py` (`SHIPPED_CANDIDATES`, schema lock, wording checks).
- `nextron/domain/drafting_smoke.py` (:213) and `nextron/app/playbook/api.py` (:1403) -- read the proposal by schema version.
- `nextron/domain/settings_schema.py` -- `draft_deadline` like `bulk_ai_submit_at`.
- `nextron/jobs/handlers.py` (`draft_step` :567 accepts step 2), `nextron/main.py` (:1286 batch owners, `read_bulk_ai_schedule` :2172, `draft_step` job type).
- `nextron/db/repos/drafting/drafts.py` (`mark_draft_failed`, `clear_draft_failed`); `nextron/domain/grounding_rules.py` (`repeated_angle` reads earlier Steps' current revisions; Steps applied in order in one unit).
- Tests: `tests/app/test_step_drafting.py`, `tests/app/test_ai_batches.py` (fixtures), `tests/unit/test_step_drafting.py`, `tests/contracts/test_ad16_callers.py`, `tests/contracts/test_ai_batch_owners.py`, settings key lists, full Settings form post (`tests/app/test_telegram_secrecy.py`), `tests/fakes/llm.py` (`ScriptedLlm.end_batch`).

## Tasks & Acceptance

**Execution:**
- [x] AI layer: v2 candidate, schema, prompt, manifest, locks; batch body test (no fallbacks) for a drafting item.
- [x] Domain: follow-up builder, v2 readers, validation, fingerprint inputs, deadline; unit tests.
- [x] `request_steps` queue; `StepDrafts` v2 Step 1 + direct follow-ups; smoke scorer by schema version.
- [x] `DraftingBatches` + `apply_draft_sequence_result` + deadline sweep; setting; handlers/main wiring.
- [x] `tests/app/test_follow_up_drafting.py` -- the I/O matrix with `ScriptedLlm` and a frozen clock; AD-16 scenario.
- [x] `nextron-engine/README.md` -- Story 4.18 section.

**Acceptance Criteria:**
- Given an ended batch with a valid follow-up answer, when `poll_ai_batches` runs twice, then Drafts 2–4 each have exactly one revision and one `check_grounding` job, and the batch is deleted.
- Given a direct follow-up call and any batch request, then only the direct request carries `fallbacks`.

## Implementation Notes

**What was built** (an interrupted first implementer's tree, reviewed against the spec, kept where right, then completed):

- **AI layer.** `draft_sequence` v2 first in `CANDIDATES` (same settings as v1), `DraftStep` / `DraftSequenceV2` (`OutputSchema(2, ...)`) in `schemas.py`, `prompts/draft_sequence/v2.md` (one prompt for Step 1 alone or Steps 2–4; the Step brief names the Steps), manifest line, schema lock, `SHIPPED_CANDIDATES`, the `test_ai_routes` table. v1's prompt and schema are unchanged. New batch body test: a framed drafting item (an `earlier_step` block, custom id `draft_sequence_<enrollment>_<rev>`) on v2 carries no `fallbacks` and no beta header, while the same call made directly carries both (`tests/unit/test_ai_batch_gateway.py`).
- **Domain** (`nextron/domain/step_drafting.py`): `read_step_1` (by schema version), `follow_up_framing`, `regulation_steps`, `follow_up_blocks`, `FollowUp`/`FollowUpProposal`/`read_follow_ups`, `validate_follow_ups`, `follow_up_citations`, `follow_up_claims`, `follow_up_fingerprint`, `next_slot`, `draft_deadline_of`. `BulkAiSchedule` moved to `domain/ai_batch.py` so both owners share it; `DEFAULT_DRAFT_DEADLINE` 07:00. `drafting_smoke.draft_flags(..., schema_version=1)` and `playbook/api.py` read the answer by the tuple's schema version.
- **App.** `Drafting.request_steps` queues the drafting item in the same unit (`ai_batches.next_rev`). `StepDrafts`: `follow_up_call` (read-only builder returning a `FollowUpCall` or a `FollowUpState`), `write_follow_ups`, `fail_follow_ups`, the direct path `draft(enr, 2, urgency)`, plus shared `requeue_follow_ups` / `enqueue_follow_up_job`. New `app/drafting/batches.py` `DraftingBatches(clock, llm, queue, follow_ups)` with `submit_bulk`/`poll`/`schedule`, `apply_draft_sequence_result`, the deadline sweep and `deadline_of`. `db/ai_batches.py` gains `next_rev` and `open_items` (owner-scoped).
- **Wiring.** Setting `draft_deadline` (TIME, 07:00, `timezone_key` sender_timezone, last). `main.build_app` registers both owners; the `submit_bulk_ai` / `poll_ai_batches` handlers get one `BatchJobs` whose `submit_bulk()` / `poll()` are each one `@use_case` calling every owner's `submit_queued()` / `poll_open()` (AD-3: a job calls exactly one use case). `read_bulk_ai_schedule` reads both owners' `schedule()`.
- **Tests.** `tests/app/test_follow_up_drafting.py` (29 tests: every I/O matrix row, both AC, Law-PrivateAI × UK through a real UK enrollment, the direct job), 32 new domain test cases in `tests/unit/test_step_drafting.py`, the AD-16 `drafting.batch_apply` scenario, owner contract, settings/prompt/serve pins.
- **Docs.** README "Drafting the follow-ups (Steps 2 to 4)"; module docstrings of `generation.py`, `batches.py`, `step_drafting.py`, `handlers.py`, `main.py`.

**Behaviour notes.**
- `follow_up_call` checks, in order: unknown/ended Enrollment, no open Draft 2–4, Contact gone/erased, the AD-16 gate, paused, no Step 1 revision, no schema-2 tuple, unreadable context. The gate comes **before** the paused check, so every write `apply_draft_sequence_result` makes (including the requeue for a paused Enrollment) follows exactly one gate call.
- Submit: a blocked or erased Contact makes the item `stale` (no call); `no_route` and `not_composable` record `draft_failed` on Drafts 2–4 and make it `stale`.
- Apply: a valid answer writes Drafts 2–4 in Step order in one unit, so Step 3's `repeated_angle` rule sees Step 2. A follow-up revision's `fact_versions` are its claims' facts, then its angle fact if no claim cites it, so later grounding sees the angle. Item `applied` when any revision was written, `failed` when the proposal was refused.
- The direct job for a paused Enrollment, or one whose Step 1 has no revision, queues a new `pending` item (unless one is already open) and calls nothing.
- Existing Step 1 tests (`tests/app/test_step_drafting.py`) pin `draft_sequence` to v1 through `activate(..., version="v1")`. The drafting smoke tests in `tests/app/test_ai_test_runs.py` now answer in v2 shape, because v2 is the default candidate.
- `BatchJobs` runs the owners in order (research, then drafting) in one use case: a retryable `AdapterError` from research's poll propagates and the job retries, so drafting's poll and deadline sweep wait for that retry.

**Resumption check (second implementer, after a restart).** The first implementer's uncommitted tree was re-read against the frozen block (`batches.py`, `generation.py` follow-up path, domain validation/fingerprint/deadline, `request_steps` queue, `ai_batches.next_rev`/`open_items`, setting, `main.py`/`handlers.py` wiring, tests) and found complete; the only change was a docstring line-wrap fix in `handlers.py`. Verified: 264 passed (follow-up, Step 1 app and unit, AI test runs, batch gateway, AI routes), 558 passed (settings page/schema, serve, Telegram secrecy, AI batches, serve process; `test_serve_process` timed out once under load at startup and passed 5/5 alone), `tests/contracts` 1956 passed / 7 skipped, `lint-imports` 29 kept / 0 broken.

**Review patches (pass 1)** (Review Triage Log rows 1–11):
1. `DraftingBatches.submit_queued`: a buildable item whose Enrollment's deadline (`deadline_of`) is at or before now becomes `stale` and the direct `draft_step:<enr>:2` job is enqueued; no batch request (`_past_deadline`). Test `test_a_submit_after_the_deadline_runs_the_direct_job_and_sends_no_batch`.
2. `StepDrafts._draft_follow_ups`: the apply transaction now rebuilds the call with `follow_up_call` (its one gate call) instead of separate Enrollment/Contact/gate checks; ended/unknown, nothing open, erased or blocked → skipped, nothing written; paused or a fingerprint unequal to the prepared one (Step 1 rev, tuple key, Evidence ids, fact versions) → nothing written, `requeue_follow_ups`, skipped (`inputs_changed`). Test `test_a_step_1_edited_during_the_direct_call_writes_nothing_and_requeues` (Step 1 revised from inside the fake's `run`).
3. `DraftingBatches._settle` for `BatchSucceeded(LlmInvalid)`: `follow_up_call` first; unknown/ended Enrollment, no open Draft, erased/missing Contact or AD-16 `Blocked` (`_GONE`, shared with `apply_draft_sequence_result`) → `stale`, nothing written. Test `test_an_invalid_answer_for_a_stopped_or_blocked_enrollment_writes_nothing` (stopped, blocked).
4. README: "an answer the code check rejects" (`invalid_proposal`) vs "a model refusal (`LlmRefused`)".
5. `batches.py` docstring and README: a batch whose route tuple no longer resolves is neither fetched nor deleted at the provider; its open items re-run directly and the row is marked `deleted` locally (the exception to fetched-until-ended). README also notes the submit's past-deadline rule and the direct path's re-check.
6. `test_build_app_wires_step_drafts` asserts `ai_batches()` yields one `BatchJobs` whose owners are `ResearchBatches` then `DraftingBatches` (on the same `StepDrafts` and LLM gateway); new `test_batch_jobs_runs_every_owner_once_in_order_and_sums` (two recording owners).
7. `test_the_schedule_reports_an_item_queued_before_the_latest_submit_slot` (`missed` False, then True after the 14:00 slot).
8. `test_a_batch_whose_tuple_no_longer_resolves_re_runs_directly_with_no_provider_call` (item `rerun`, direct job, no fetch/delete, row `deleted`).
9. `test_an_over_long_body_discards_the_whole_answer` (one body > `MAX_BODY`: no revision on Drafts 2–4, all `invalid_proposal`, item `failed`).
10. `test_a_direct_success_after_a_failure_clears_the_failure` (`draft_failed_at`/`_reason` cleared on Drafts 2–4).
11. `test_only_the_newest_pending_item_is_sent_the_older_goes_stale`; `test_a_paused_enrollment_at_apply_makes_the_result_stale_and_requeues` (before the deadline: new pending item; after: direct job); `test_step_1_on_an_active_v2_tuple_stores_revision_1`.

### Decisions made overnight (implementation time, pending user review)
1. **A batch whose route tuple no longer builds:** its open drafting items become `rerun`, and the direct `draft_step:<enr>:2` job runs. `ResearchBatches` marks them `failed` instead. Here a lost tuple shouldn't leave follow-ups undrafted, and the direct job re-checks the route (`no_route` if none).
2. **The submit and poll jobs share one use case.** They call both owners through `BatchJobs` in `main.py`, not one handler call per owner. `research` and `drafting` are sibling services in the app DAG, and AD-3 allows one use case per job entry point. Each owner keeps its own `@use_case submit_bulk()` / `poll()` for direct callers.
3. **Step 1's angle compares fact ids, not versions.** A follow-up angle on a newer version of a fact Step 1 cites still counts as a repeat (the conservative reading).
4. **A blocked Contact at submit gives `stale` and no events row.** The direct job writes the `drafting_blocked` events row per open Draft, as Step 1 does. The suppression path stops the Enrollment anyway.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 31 raw, 24 after dedup; 11 patched, 6 deferred, 7 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | EC5: a pending item that becomes sendable after its deadline (resumed Enrollment, late Step 1) is still sent in the batch, paid, then made stale by the sweep | low | `submit_queued` never checks `deadline_of`; the sweep runs every 15 min, so the window is the gap before a submit | patch: submit makes a past-deadline item `stale` and enqueues the direct job, no batch request |
| 2 | EC6: the direct follow-up path writes from the prepare-time Step 1 subject/text and tuple even if Step 1 was edited (or the tuple changed) during the call | medium | `_draft_follow_ups` re-checks Enrollment and gate but not Step 1's rev or the tuple key | patch: re-build the fingerprint after the call; a mismatch writes nothing and requeues a pending item (the sweep or next batch drafts it) |
| 3 | BH6+EC7: a batch `LlmInvalid` writes `draft_failed` with no Enrollment-status or AD-16 check | low | `_settle` calls `fail_follow_ups` directly; harmless to contact but breaks the "every write follows one gate" note | patch: ended Enrollment or blocked/erased Contact → `stale`, nothing written |
| 4 | BH10: README calls both a code-rejected proposal and a model refusal "refused" | low | direct wording correction | patch |
| 5 | EC9+BH8 (doc part): docs say a batch is fetched until ended before delete, but the tuple-missing branch marks it deleted locally | low | `_poll` tuple-missing branch | patch: state the exception in the docstring/README |
| 6 | VG1+BH13: `build_app` registering `DraftingBatches` and `BatchJobs` (both owners, in order, summed) untested | medium | dropping `DraftingBatches` from `batch_owners` fails no test | patch: wiring assertion + `BatchJobs` test |
| 7 | VG2+BH13: `DraftingBatches.schedule()` untested | medium | no test calls it | patch: test either side of the submit slot |
| 8 | VG3+BH13: implementation decision 1 (tuple missing → `rerun` + direct job, no fetch) untested | medium | copying research's `failed` fails no test | patch: test |
| 9 | VG4: whole-proposal discard on one over-long body untested | medium | deleting the `_check_text` pre-loop fails no test | patch: test |
| 10 | VG5: `clear_draft_failed` after a follow-up success unobserved | medium | deleting the clear fails no test | patch: fail then succeed test |
| 11 | VG6+BH13: newest-pending-only at submit, paused Enrollment at apply, and Step 1 through `StepDrafts` on a v2 tuple untested | medium | removable with no test failing | patch: tests |
| 12 | EC1+EC2: a Sequence with other than 4 Steps (1–6 allowed): follow-ups are fixed to Steps 2–4, so 2–3 Step Sequences fail `not_composable` and Steps 5–6 are never drafted | medium | the frozen block says "exactly Steps 2, 3, 4"; only the 4-Step default is seeded | defer (would change the approved spec) |
| 13 | EC3: `_append` failing for one Draft while the others succeed leaves the item `applied` with that Draft `not_composable` | low | needs a composition error after the pre-checks passed; rare | defer |
| 14 | EC4+BH8: the tuple-missing branch leaves the provider batch undeleted (results kept at the provider until expiry) | medium | same as 4.17's accepted #3; fetching needs the tuple | defer |
| 15 | BH1: `no_route` at submit (no schema-2 tuple, e.g. before the first v2 gate run) fails the follow-ups for good; nothing re-queues on activation | medium | spec-decided (decision 1); same gap as 4.15's deferred Step 1 recovery | defer |
| 16 | BH3: a batch `LlmInvalid` or `invalid_proposal` leaves the follow-ups empty with no retry or alert | medium | spec-decided `failed`; Regenerate is 4.20 | defer |
| 17 | BH4: the 4.5 smoke gate never exercises follow-up mode, so a v2 tuple can activate untested for Steps 2–4 | medium | `draft_flags` reads Step 1 only; rules + AI grounding + approval still guard each follow-up | defer |
| 18 | BH7+EC8: waiting pending items keep `schedule().missed` true, so every start runs a catch-up submit | low | the catch-up only sends queued work early; same as 4.17's rejected #12 | reject |
| 19 | BH2: Enrollments enrolled before 4.18 get no item | low | no real Enrollment has drafted text yet (no `draft_sequence` tuple is active anywhere) | reject |
| 20 | BH5: calls are made when the allowed facts can't give three distinct angles | low | the seed gives 5 non-offer facts per Segment × Market | reject |
| 21 | BH9: fingerprint omits Signals, Contact and Evidence text | low | the frozen fingerprint list is exactly what's coded | reject (would change the approved spec) |
| 22 | BH11: `cited_signal_id` and the model's subject aren't checked on follow-ups | low | both are ignored by design (decision 7) | reject |
| 23 | BH12: a Contact blocked during the direct call writes no `drafting_blocked` row | low | identical to Step 1's 4.15 behaviour | reject |
| 24 | BH14+BH15+BH16: deferred-work not updated, `last_updated` went backwards, review sections empty | low | deferred entries and triage are written in step 4; `last_updated` corrected | patch (orchestrator) |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q -n 2 --basetemp="e:/Nextron AI outreach/_handoff/scratch/track-D/pt" <touched test files>` -- all pass
- `uv run pytest -q -n 2 --basetemp=... tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept

**Final gate:** full suite through the lock on the final tree: 18100 passed, 10 skipped; lint-imports 29 kept, 0 broken.
