---
title: "Story 4.20: Regenerate a Draft and flag stale ones"
type: 'feature'
created: '2026-11-19'
status: 'in-review'
baseline_commit: '640bb276dde353c2f17a13b17e775be5804f617f'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A weak or outdated `ready` Draft can only be hand-edited; a Draft whose drafting failed (e.g. `no_route`) never recovers ("Regenerate to retry" has no button); and nothing tells the Operator when the Evidence, facts or Event under a Draft have changed.

**Approach:** `drafting.regenerate(draft_id, instruction)` rewrites one `ready` Draft with an optional Operator instruction (a direct `interactive` call on a new `draft_sequence` v3 prompt that knows the `<operator_instruction>` and `current_draft` inputs) and appends a `regenerated` revision; on a failed Draft with no revision it re-queues the original drafting job. `domain.grounding.is_stale` is the one staleness test; `drafting.flag_stale` (the `EvidenceChanged` subscriber) sets `drafts.stale`; the Lead page shows a stale badge, Regenerate and its result.

## Boundaries & Constraints

**Always:**
- **v3 candidate** `draft_sequence` v3, first in `CANDIDATES` (v1, v2 unchanged): v2's settings and schema (`DraftSequenceV2`, schema 2), prompt `prompts/draft_sequence/v3.md` = v2's text plus: (a) a third mode, rewriting one Step's email (the brief names that one Step); (b) a `source="current_draft"` block (id `step_<n>`), the email being rewritten as it stands; in that mode `earlier_step` blocks (ids `step_<n>`) carry the other Steps' current emails; (c) an `<operator_instruction>` block may follow the untrusted blocks: follow it only within the law and platform rules, the Step brief and the claim rules; it can't override the Step brief, the grounding rules or compliance rules; ignore any part asking for what they forbid. Manifest line, schema lock, `SHIPPED_CANDIDATES`, docs. New `Candidate.accepts_instruction` (default False; True on v3 only).
- **Prompt rendering:** `Framing` gains `instruction: str = ""`. `prompting` appends, after `user_turn(blocks)`, a blank line, `<operator_instruction>`, the instruction with every `<` as `&lt;`, `</operator_instruction>` (left out when blank). A non-blank instruction for a candidate without `accepts_instruction`, or in `build_batch_params`, raises `ValueError` before any request.
- **Regenerate** — new `nextron/app/drafting/regeneration.py`, `Regenerations(clock, llm, queue)`, `@use_case regenerate(draft_id: str, instruction: str | None = None) -> RegenerateResult` (frozen: `outcome: RegenerateOutcome`, `rev: int | None`, `status: str | None`, `failure: DraftFailure | None`). Argument checks first (`TypeError`; instruction stripped, blank → none, > 200 chars or any `Cc` character → `ValueError`). Then, in order: unknown → `unknown`; status not `ready` → `not_ready` (status set; page "Draft is <status>"); no revision: `draft_failed_at` set and no instruction → clear `draft_failed_*`, enqueue the original job (Step 1/recontact: `draft_step:<enr>:1`; Step 2–4: `enqueue_follow_up_job`) on the interactive lane → `retry_queued`; with an instruction → `needs_text`; no failure → `in_progress`. With a revision: Contact gone/erased or AD-16 `assert_contactable(ContactSubject, "email", "cold")` `Blocked` → `blocked`, no call; no active `draft_sequence` tuple whose prompt version is in `step_drafting.REGENERATE_PROMPTS` (`{"v3"}`) → `failed`/`no_route`; context unreadable → `failed`/`not_composable`. Else the call at `Urgency.INTERACTIVE` (direct, with server fallbacks): framing = that Step's brief (Step 1: as 4.15; Step N≥2: that one brief with its Segment × Market angle), allowed facts, the Step's Variant note, English variant, the instruction masked (`[email]`, `[phone]`); blocks = `current_draft` block, (Step N≥2: an `earlier_step` block per other Step with a current revision), then the Lead's blocks exactly as Step 1. `AdapterError` propagates.
- **Apply** (new transaction): Draft not `ready` or `current_rev` changed → `changed`, nothing written; Contact erased/blocked now → `blocked`, nothing written; non-`LlmOk` → `failed` with the 4.15 mapping. Validate: Step 1 as 4.15 (`read_step_1`, `validate_proposal`, `_check_text`); Step N≥2: exactly one `steps` entry for N, claims cite inputs, an input angle fact whose id no other Step's current revision cites, a `regulation` fact when its brief has an angle for the Campaign's Segment × Market, `_check_text` with Step 1's current subject; else `failed`/`invalid_proposal`. A failure never writes `draft_failed_*` and keeps the current revision. Success: `Drafting._append(..., REGENERATED, Traceability(... instruction=<stripped instruction or None>), claims, urgency=INTERACTIVE)` (Step N≥2 subject = Step 1's current subject; citations as 4.18) → `regenerated` with the new `rev`. Revision 1 is never touched.
- **`domain.grounding.is_stale(revision, now) -> StaleReason | None`** (new `nextron/domain/grounding.py`, pure; the input carries each cited Evidence's `superseded_by`/`retracted_at`, each cited `(fact_id, version)`'s current version and whether it is in `allowed_facts` for the Draft's Segment × Market, and the cited Signal's class/tier/event date/first seen/retracted). Reasons, first found in this order: `evidence_changed`, `fact_changed` (not current), `fact_not_allowed`, `event_not_live` (retracted, missing, or `is_live` false at `now`). The only staleness test (contract: no other module defines one).
- **`@command flag_stale(n: EvidenceChanged)`** on `Drafting`, registered in `build_subscribers`: every `ready` Draft whose current revision's `evidence_ids` holds `n.evidence_id` gets `stale = 1` in the same transaction. `_append` with origin `generated` or `regenerated` sets `stale = 0`.
- **Lead page:** each Draft shows a "Stale: <reason text>" badge when live `is_stale` of its current revision gives a reason (or "Stale: Evidence changed" when only `drafts.stale` is set); a Regenerate form (`method="post"`, `hx-post`, `{{ csrf_field() }}`, instruction `maxlength=200`, label "Retry drafting" with no instruction field when the Draft has no revision and a failure) outside the `hx-disable` container that holds AI text; `hx-disabled-elt` + an indicator "Regenerating…" as progress. `POST /leads/{lead_id}/drafts/{draft_id}/regenerate` (`@personal_data(True)`) returns the Draft's fragment with the outcome notice (typed failure text, "Draft is <status>", "Drafting again…"); `GET /leads/{lead_id}/drafts/{draft_id}` returns the fragment, which polls itself every 3 s while its grounding is `pending` or a retry is drafting. `AdapterError` → notice "Regenerate failed: the AI service is unavailable", nothing written.
- **SM-7:** `queries.drafting.regenerated_count(draft_id) -> int`.
- AD-16 caller scenario `drafting.regenerate`; instruction text never logged or put in a job payload.

**Never:** sending email; a migration; changing v1/v2 prompts or schemas; batching a regenerate; budget checks (`essential`); auto-regenerating stale or `no_route` Drafts (`refresh_due_queue` is a later story); changing approved/sent Drafts; Tier C `generic` handling (4.19); a test reaching Claude.

### Decisions made overnight (pending user review)
1. Regenerate runs synchronously in the POST (direct `interactive` call), not as an interactive-lane job: job payloads can't hold the instruction text, and a pending-instruction table would need a migration. Story 5.5 and `refresh_due_queue` call the same use case.
2. Regenerate needs the new v3 prompt: v1/v2 never told the model what an `<operator_instruction>` or a `current_draft` is, and shipped prompts never change. Until a v3 tuple passes the 4.5 gate, regenerate fails `no_route` (shown on the page); "Retry drafting" works on any tuple.
3. The regenerate path is also the recovery for a failed Draft with no revision (4.15/4.18 `no_route` and the rest): it re-queues the original drafting job. Nothing re-queues them automatically when a tuple activates (deferred).
4. A regenerate failure isn't stored (the current revision stays); the page shows it in the response only.
5. A follow-up's new angle must avoid every fact id any other Step's current revision cites (conservative).
6. The current text is sent as an untrusted `current_draft` block (model output, maybe Operator-edited).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Step 1 regen | ready, rev 1, v3 active, "shorter" | rev 2 `regenerated` + instruction, rev 1 unchanged, `check_grounding` interactive | N/A |
| Follow-up regen | Step 3, rev 1 | rev 2; `earlier_step` blocks for 1, 2, 4 | repeated angle → `invalid_proposal` |
| Pricing | "add our pricing", model writes a price | rev stored, grounding `fail` with `price_or_audit_in_step` | N/A |
| Not ready | approved | `not_ready`, "Draft is approved" | nothing written |
| Model fails | refused / truncated / invalid | current rev stays, typed failure shown | N/A |
| Retry | no rev, `no_route` | failure cleared, `draft_step:<enr>:1` queued | instruction → `needs_text` |
| No v3 | only v2 active | `failed`/`no_route`, no call | N/A |
| Blocked | Contact suppressed | `blocked`, no call | N/A |
| Race | rev changed during call | `changed`, nothing written | N/A |
| Evidence superseded | `EvidenceChanged` for a cited id | `stale = 1` on ready Drafts citing it | others untouched |
| Fact edited | cited fact v1, now v2 | badge "Stale: …", regenerate clears it | N/A |

</frozen-after-approval>

## Code Map

- `nextron/app/drafting/generation.py` -- `StepDrafts._lead`, `_framing`, `_follow_up_context`, `_apply`, `FAILURES`, `_slot`, `enqueue_follow_up_job`; reuse (lift shared helpers to functions if needed), don't change 4.15/4.18 behaviour.
- `nextron/app/drafting/api.py` -- `_append` (:553; stale reset), `Traceability.instruction`, `_check_text`, `request_steps` Step 1 job enqueue (reuse for retry); add `flag_stale`.
- `nextron/domain/step_drafting.py` -- `framing`, `brief_section`, `input_blocks`, `follow_up_blocks`, `read_step_1`, `validate_proposal`, `read_follow_ups`, `validate_follow_ups`, `follow_up_citations/claims`, `mask`, `failure_text`; add `REGENERATE_PROMPTS`, regenerate blocks/framing, single follow-up validation.
- `nextron/adapters/ai/routes.py` (:209), `schemas.py`, `prompting.py` (`_params` :129), `prompts/draft_sequence/v2.md` (base of v3), `manifest.lock`; `nextron/domain/ai.py` `Framing` (:322); `tests/contracts/test_prompt_manifest.py`.
- `nextron/app/queries/drafting.py` (`ready_with_text`, `current_revision`; add `regenerated_count`), `nextron/db/repos/drafting/drafts.py` (add `set_stale`), `queries/research.py` (`evidence_history`, add a by-id signal read incl. retracted), `queries/playbook.py` (`fact`, `allowed_facts`).
- `nextron/main.py` (`build_subscribers` :2397 `EvidenceChanged` → `flag_stale` in AD-3 order; `build_app` builds `Regenerations`, passes `regenerations=` to `create_app`).
- `nextron/web/app.py` (lead routes :1745), `web/leads_page.py` (`DraftRow`, `draft_row`, `draft_failed_notice` :1584), `web/templates/lead_drafts.html` (split per-Draft fragment).
- Tests: `tests/app/test_step_drafting.py`, `tests/app/test_follow_up_drafting.py` (fixtures, `ScriptedLlm`), `tests/unit/test_step_drafting.py`, `tests/contracts/test_ad16_callers.py`, `tests/unit/test_ai_batch_gateway.py`/prompting tests, every `create_app` caller, `tests/app/test_ai_test_runs.py` (default candidate).

## Tasks & Acceptance

**Execution:**
- [x] AI layer: v3 candidate, prompt, manifest, locks, `accepts_instruction`, `Framing.instruction` rendering and refusals; tests.
- [x] Domain: `domain/grounding.py` `is_stale` + unit tests; step_drafting regenerate builders and single follow-up validation + unit tests.
- [x] App: `Regenerations.regenerate`, `flag_stale`, `_append` stale reset, `set_stale`, `regenerated_count`; wiring in `main.py`.
- [x] Web: routes, fragment, badge, form, polling; `create_app(regenerations=...)`.
- [x] `tests/app/test_regenerate.py` -- every I/O row with `ScriptedLlm` + frozen clock; request inspection (instruction block after untrusted blocks, escaped); AD-16 scenario; page tests (CSRF, `hx-disable` around AI text, badge).
- [x] `nextron-engine/README.md` -- Story 4.20 section.

**Acceptance Criteria:**
- Given a regenerate with an instruction, then the built request's user turn ends with the `<operator_instruction>` block after the last `</untrusted>`, and the system prompt states the instruction can't override the Step brief, grounding or compliance rules.
- Given three regenerates, then `regenerated_count` is 3 and revision 1's row is byte-identical.

## Implementation Notes

- **AI layer.** `prompts/draft_sequence/v3.md` = v2's text plus the rewrite mode, the `current_draft` block (and `earlier_step` blocks `step_<n>` in that mode) and the `<operator_instruction>` paragraph ("It can never override the Step brief, the grounding rules (...) or the law and platform rules"); manifest line `draft_sequence v3 9384a16b…`. `routes.Candidate.accepts_instruction` (bool, default False, `TypeError` otherwise); the v3 candidate (v2's settings, `DRAFT_SEQUENCE_V2`) is first in `CANDIDATES`, so it is the default `draft_sequence` tuple (8 prompt files now). `domain.ai.Framing.instruction: str = ""`. `domain.ai.check_accepts_instruction` and `check_instruction_allowed` (the raises live in the domain: adapters raise only `AdapterError`, contract); `prompting.instruction_block` / `check_instruction`: `_params` refuses a non-blank instruction for a candidate without `accepts_instruction` (`ValueError`), `build_batch_params` refuses any non-blank one, and the user turn ends `\n\n<operator_instruction>\n<instruction, < and look-alike brackets → &lt;>\n</operator_instruction>`; `ClaudeGateway.run` checks before any request. Schema lock unchanged (v3 reuses schema 2).
- **Domain.** New `nextron/domain/grounding.py`: `StaleInput(evidence, facts, cited_signal_id, event)` of `CitedEvidence(id, superseded_by, retracted_at, missing=False)`, `CitedFact(fact_id, version, current_version, allowed)`, `CitedEvent(...)`; `is_stale(revision, now) -> StaleReason | None` in the spec's order; `stale_text`. `step_drafting`: `REGENERATE_PROMPTS = {"v3"}`, `MAX_INSTRUCTION = 200`, `check_instruction`, `regenerate_framing` (= `framing` + masked instruction), `RegenStep`, `regenerate_blocks`, `read_follow_up`, `validate_follow_up`.
- **App.** `generation.py` lifts `lead_inputs`, `campaign_of`, `allowed_versions` to module functions (the `StepDrafts` methods delegate; 4.15/4.18 behaviour unchanged). `api.enqueue_step_1_job` (shared by `request_steps` and the retry); `_append` sets `stale = 0` for `generated`/`regenerated`; `Drafting.flag_stale` (`@command`) registered for `EvidenceChanged` in `build_subscribers` (on the enqueue-only queue, like `recompose_ready`). `drafts_repo.set_stale`; `queries.drafting.regenerated_count`; `queries.research.signal_of(lead_id, signal_id)` (retracted included). New `nextron/app/drafting/regeneration.py`: `Regenerations(clock, llm, queue).regenerate` (`RegenerateOutcome`, `RegenerateResult`), `Regenerations.stale(draft)` and `stale_reason(draft, now)` (builds the `StaleInput` from `evidence_history`, `playbook_queries.fact`, `allowed_versions`, `signal_of`). `main.build_app` builds `Regenerations(clock, llm, queue)` and passes `regenerations=` to `create_app` (required kwarg; `tests/dashboard.py` too).
- **Web.** `lead_drafts.html` no longer carries `hx-disable`; each Draft renders from the new `lead_draft.html` (`<article id="draft-<id>">`), its AI text in `<div class="draft-content" hx-disable>`, then the notice and the Regenerate form (`method="post"`, `hx-post`, `csrf_field()`, `hx-disabled-elt="find button"`, `hx-sync`, instruction `maxlength=200`, a `.htmx-indicator` "Regenerating…", CSS added to `app.css` since `includeIndicatorStyles` is off). "Retry drafting" (no instruction field) for a `ready` Draft with no revision and a failure; no form for a Draft still being drafted. Routes `GET /leads/{lead_id}/drafts/{draft_id}` and `POST /leads/{lead_id}/drafts/{draft_id}/regenerate` (`@personal_data(True)`; 404 text for a Draft not on the Lead's survivor; the instruction checked in the web layer first, a refusal is a notice; `AdapterError` → "Regenerate failed: the AI service is unavailable"). `leads_page` reads only queries: `regenerate_notice(outcome, rev, status, failure)` takes the result's fields as strings.
- **Tests.** New `tests/app/test_regenerate.py`, `tests/unit/test_grounding_stale.py`, `tests/unit/test_regenerate_domain.py`; new contract `tests/contracts/test_staleness_contracts.py` (no module but `domain/grounding.py` defines `is_stale` or `StaleReason`, with a probe); additions to `tests/unit/test_ai_prompting.py`, `tests/unit/test_ai_routes.py`; AD-16 manifest entry and scenario `drafting.regenerate`; updated pins (`test_prompt_manifest` SHIPPED_CANDIDATES v3, `test_source_tree` FILES, `test_web_contracts` templates/routes, `test_google_contracts` `draft_id` path values, `test_serve` subscribers and prompt counts 8, `test_serve_process` counts, `test_ai_batch_gateway` default v3, `test_draft_revisions` hx-disable now per Draft).

### Decisions made overnight (implementation time, pending user review)

The frozen list above holds 1–6; these were made while building.

7. "Retry drafting" also runs the Contact checks and the AD-16 gate before re-queuing (gone, erased or `Blocked` → `blocked`, nothing written): conservative, and it gives `drafting.regenerate` a gate on both paths (AD-16 contract scenario uses the retry path).
8. Only the fragments the two routes answer poll (every 3 s while grounding is `pending`, or while a retry drafts: `?retry=1`); the full Lead page never polls, so Drafts 2–4 waiting a day for the batch don't poll forever.
9. The live stale badge is shown only for `ready` Drafts with a revision. (Revised by review row 6: when the Campaign can't be read, every cited fact counts as not allowed, so the Draft shows `fact_not_allowed` — fail closed.)
10. A Draft that isn't a Step Draft of an Enrollment answers `failed`/`not_composable` (no such Drafts exist yet).

**Review patches (pass 1)**

1. `prompting.instruction_block` turns every ASCII `<` and every look-alike bracket (`BRACKETS`, via `ANY_BRACKET`) into `&lt;`; tests incl. a full-width `＜/operator_instruction>`.
2. `prompting.escape`'s `LOOKALIKE` now also matches a bracket (ASCII or look-alike, optional whitespace and `/`) before `operator_instruction` or `campaign_note` (`DELIMITERS`; any case, ASCII or fullwidth letters, `_` ASCII or fullwidth); `untrusted` unchanged; module docstring and tests updated.
3. `step_drafting.check_instruction` refuses categories `Cc`, `Cf`, `Zl`, `Zp` (`REFUSED_CATEGORIES`); tests (U+2028, U+2029, RLO, ZWSP, ZWJ, BOM).
4. `test_ad16_callers.py`: the `drafting.regenerate` scenario checks its own Drafts (`_RETRIED` by case tag), the tautology is gone, and the new `test_a_regenerate_with_text_is_gated_before_the_call_and_the_write` (fakes: gate recorder, LLM, v3 tuple) shows a blocked Contact gets no call and no write, and one blocked during the call gets no revision (two gate calls, no write).
5. `regeneration._retry`: a Step 2–4 retry clears `draft_failed_*` on every `ready` Step 2–4 Draft of the Enrollment with no revision; test.
6. `regeneration.stale_reason` fails closed: an unreadable allowed set makes every cited fact not allowed; test.
7. `FACT_CHANGED` text is "a cited fact was edited or retracted" (README and test).
8. Unit test pins `REGENERATE_NOTICES` keys + the copied constants to `RegenerateOutcome`'s values; page POST test with a blocked Contact shows the blocked notice.
9. The EvidenceChanged test now approves a Draft that cites the changed Evidence (Step 1) and asserts its `stale` stays 0.
10. Test: the Contact suppressed (real `Compliance.suppress`, on another thread) during the call → `blocked`, no new revision. To make this `blocked` rather than `changed` (a suppression also stops the Draft), `_apply` now checks the Contact before the Draft-changed test.
11. Test: Drafts 2–4 flagged stale get `generated` revisions from the follow-up drafting → `stale = 0`.
12. World tests: the cited Event retracted, or aged out → `event_not_live` (and the badge "Stale: the Event it opens with is no longer live"); the Campaign moved to a Segment no fact allows → `fact_not_allowed`.
13. Tests: the GET fragment has no `hx-get`/`hx-trigger` once grounding is `pass`, or once a retry failed again (`?retry=1`). Hygiene: `DraftStatus` (and the other 4.20 domain imports in `leads_page.py`) sorted into place; new test lines over 100 chars wrapped; README says v2 was `draft_sequence`'s default until Story 4.20 made v3 the default (plus the follow-up retry and crawled-tag escaping).

**Full-suite fix.** The first locked run failed `test_notifications_area::test_empty_notices_take_no_space_and_are_never_hidden` (no `display: none` in `app.css`): the Regenerate indicator now hides with `opacity: 0` and shows with `opacity: 1` while the request runs.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 37 raw, 24 after dedup; 13 patched, 2 deferred, 9 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1+EC8: `instruction_block` escapes only ASCII `<`; a look-alike bracket (`＜/operator_instruction>`) survives | medium | `str.replace("<", ...)`, not the look-alike set `escape()` uses | patch |
| 2 | EC9: crawled text in an untrusted block can carry a literal `<operator_instruction>` tag imitating the block v3 obeys | medium | `escape()` only neutralised brackets before `untrusted` | patch: also before `operator_instruction` and `campaign_note` |
| 3 | BH2+EC7: `check_instruction` lets U+2028/U+2029 and `Cf` (bidi, zero-width) through the one-line rule | low | refused only `Cc`; direct correction | patch |
| 4 | BH4+BH5+VG-other: AD-16 scenario counted every Draft (vacuous), ended in a tautology; regenerate-with-text had no scenario | medium | `cleared >= 1` matched never-failed slots | patch |
| 5 | BH7a: a follow-up "Retry drafting" cleared only the clicked Draft's failure; the job drafts Steps 2–4 together | medium | `clear_draft_failed(draft.id)` only | patch |
| 6 | BH9: `stale_reason` treated cited facts as allowed when the allowed set couldn't be read | low | `allowed is None or ...` | patch: fail closed |
| 7 | BH11: a retracted fact showed "a cited fact was edited" | low | `FACT_CHANGED` covers `current_version is None` | patch: text |
| 8 | BH12+VG6: notice map and copied outcome constants not pinned to `RegenerateOutcome`; blocked notice never rendered | medium | no test named them | patch: tests |
| 9 | BH13+VG3: `flag_stale`'s ready-only filter unobserved | medium | the approved Draft in the test didn't cite the Evidence | patch: test |
| 10 | VG1: apply-time contactability re-check untested | medium | deleting it failed no test | patch: test (and Contact checked before the changed test) |
| 11 | VG2: a `generated` revision clearing `stale` untested | medium | only regenerate observed | patch: test |
| 12 | VG4: `event_not_live` / `fact_not_allowed` app wiring untested | medium | only unit tests of `is_stale` | patch: world tests |
| 13 | VG5+BH16: poll stop untested; import order, long lines, README v3 sentence | medium | no test asserted no `hx-get` after `pass` | patch |
| 14 | BH6+EC2+EC3: a Step 1 regenerate can change the subject the follow-ups carry, and can state a fact a follow-up uses as its angle | medium | frozen block: Step 1 validated "as 4.15"; follow-ups keep Step 1's subject at their time (4.18 decision 7) | reject (would change the approved spec); logged in deferred-work |
| 15 | BH10: staleness not shown on the approval queue, approve path doesn't check it | medium | approval cards (5.1–5.3) aren't in this tree | defer (merge touchpoint) |
| 16 | VG7: the badge's flag-only fallback never rendered alone | low | the live reason gives the same text | defer |
| 17 | BH8+EC13: the "Stale: Evidence changed" flag fallback is a second staleness source | low | frozen block prescribes it; `EvidenceChanged` fires only for superseded/retracted rows, which `is_stale` also reports | reject (spec-prescribed) |
| 18 | EC10: approved Drafts citing changed Evidence aren't flagged | low | AC: only `ready` Drafts | reject (would change the approved spec) |
| 19 | BH3+BH14: `flag_stale` and the page's live test read every ready Draft / the Lead's Evidence per Draft | low | a few hundred ready Drafts at most | reject |
| 20 | BH15+EC6: retry/grounding polling has no cap when the runner is down | low | a 3 s GET of one fragment while the page is open | reject |
| 21 | BH7b: a follow-up retry with no Step 1 text "fails again at once" | false | `NO_STEP_1` requeues a pending item and waits | reject |
| 22 | EC1: Step 1's subject or other Steps' facts can change during a follow-up regenerate's call | low | needs concurrent regenerates by the one Operator | reject |
| 23 | EC4+EC5: `Invalid*` decode errors from setup reads give a 500 | low | seeds validated at sync; same shape as 4.15's prepare | reject |
| 24 | EC11+EC12: Step 1 lookup may pick a recontact Draft; follow-up regen with no Step 1 revision uses its own subject | false | a recontact Enrollment's slots are all `recontact`; a follow-up revision exists only after Step 1 had one | reject |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q -n 2 --basetemp="e:/Nextron AI outreach/_handoff/scratch/track-D/pt" <touched test files>` -- all pass
- `uv run pytest -q -n 2 --basetemp=... tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept
