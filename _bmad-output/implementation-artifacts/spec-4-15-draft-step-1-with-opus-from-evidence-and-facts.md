---
title: "Story 4.15: Draft Step 1 with Opus from Evidence and facts"
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '31596dbf529771dfcf33823399bee7d70224e529'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Step 1 Draft slots (4.9) never get text: nothing calls Claude to write them, so no revision 1 exists to ground (4.13/4.14), approve (Epic 5) or send.

**Approach:** `request_steps` enqueues `draft_step:<enrollment_id>:1` at `soon` in its own transaction. The job's use case gates on AD-16, reads the active `draft_sequence` tuple (Opus, `essential`, Playbook-pinned, 4.5-gated), builds a cached prefix (instructions, Step brief, Playbook, allowed facts) and a one-Lead untrusted user turn, validates the proposal in code, and stores revision 1 (`generated`) through `Drafting._append` with its claims, traceability and the caller's urgency (which enqueues `check_grounding`). Every failure stores no revision and sets `draft_failed_at` + reason, shown on the Lead page.

## Boundaries & Constraints

**Always:**
- **Enqueue:** `Drafting.request_steps` enqueues job `draft_step` (scope Enrollment id, discriminator Step `1`, payload `{"v": 1, "enrollment_id", "step": 1}`) when it creates a Step 1 slot (kind `step` or `recontact`), in the same unit, at the caller's urgency (`enroll` passes `soon`); with no queue it refuses (`RuntimeError`) before any write. `Sequencing(clock, queue=None)` passes its queue to `Drafting`.
- **Route `draft_sequence`:** one candidate `claude-opus-5-5`, effort `medium` (explicit), prompt `v1`, schema `DraftSequenceV1` (schema 1), `batchable=True`, `max_tokens` 8,000 capped at 16,000, `budget_class=ESSENTIAL`, `uses_playbook=True`, `server_fallbacks=True`. `domain.ai.check_server_fallbacks` now allows fallbacks on a batchable Opus candidate: only the direct `build_request` adds them; a batch request (4.17) never does. Not bootstrap-activated: live only through the 4.5 gate. Add to `SHIPPED_CANDIDATES`, `manifest.lock`, `PLAYBOOK_ROUTES`.
- **`DraftSequenceV1`:** `subject`, `body`, `claims[]` of `{span, evidence_id|null, fact_id|null, fact_version|null}`, `cited_signal_id`, `explanation` (≤200). No property named like `reasoning`. Schema in `output_config.format`.
- **Prompt (AD-18):** the gateway's `run` (port, `ClaudeGateway`, `ScriptedLlm`) and `build_request` gain an optional keyword `framing` (a frozen `domain.ai.Framing`: trusted `brief`, `facts` and `note` texts). `system` = the route's prompt (law and platform rules, then the precedence: Step brief, then Playbook), then the brief section (Step 1 brief incl. the Campaign's Segment × Market angle), then `playbook_section`, then the allowed-facts section (`queries.playbook.allowed_facts(segment, market)`, each `fact_id`, `version`, wording); `cache_control` only on the last. User turn = a `<campaign_note>` with the Variant's Step 1 angle note (if any) and the Market's English variant, then `<untrusted>` blocks: the Lead's live Evidence (id = Evidence id, text as 4.14's `_evidence_text`), its current Signals (id = Signal id: type, class, tier, event date, linked Evidence ids), and the Contact's name and role. Every prompt text passes through one domain masker that replaces email addresses and phone numbers (`[email]`, `[phone]`); no Contact email ever enters a block. No `framing` → requests unchanged byte-for-byte.
- **Job use case** `StepDrafts(clock, llm, queue).draft(enrollment_id, step, urgency)` (new `app/drafting/generation.py`; prepare unit → call with no transaction → apply unit; never calls `domain.budget.allow`):
  1. No-op when the Step 1 slot is missing, not `ready`, or already has a revision (idempotent).
  2. `compliance_api.assert_contactable(contact, "email", "cold")` first: `Blocked` → no call, no revision, one `events` row (entity `drafts`, `ready`→`drafting_blocked`, cause the first reason code, `cause_ref` Enrollment, actor `system:drafting`).
  3. No active `draft_sequence` tuple → `draft_failed` (`no_route`), no call.
  4. Call. `AdapterError` propagates (the runner retries; nothing stored). `LlmRefused` / `LlmTruncated` (after the gateway's one doubled retry) / `LlmInvalid` / `LlmFailed` → `draft_failed` (`refused`, `truncated`, `invalid`, `failed`).
  5. **Validate** (pure domain): subject and body non-empty after strip and pass `_check_text`; every claim cites exactly one of an input Evidence id or an input `(fact_id, version)`; `cited_signal_id` is a live Event Signal of the Lead (`domain.signals.is_live`, class `event`) → else discard, `draft_failed` (`invalid_proposal`).
  6. **Apply:** re-read; slot gone/not `ready`/has a revision → no-op; Contact erased or not contactable → no revision (logged). `_append(draft, subject, body, GENERATED, Traceability(evidence ids and `(fact_id, version)` pairs from the claims in first-appearance order, the cited Signal, the serving tuple id, Playbook SHA and version, prompt version), now, claims=…, urgency=…)`; `ValueError`/`LookupError` from it → `draft_failed` (`not_composable`). Success clears `draft_failed_at`/reason.
- **`drafts`** (new migration, owner `drafting` content group, PII `none`): `draft_failed_at` (nullable), `draft_failed_reason` (nullable code ≤32; CHECK both NULL or both set; values the reasons above).
- **Job type** `draft_step`: default lane interactive, lanes {interactive}, priority 40, `max_runtime` 15 min, retry 3, group `other`; handler in `jobs/handlers.py`; `build_job_types(..., step_drafts=None)`.
- **Test gate:** `SCORERS[DRAFTING_SMOKE]`: per case, the draft call with the case's framing (Step 1 brief of the stored `default` Sequence for the case's Segment and Market; the case's facts; its angle note; the Market's English variant) → validate → `grounding_rules.check` (strongest = `expected.cited_signal_id`) → the active `grounding_check` tuple over the output (4.14's shared builder and `read_answer`). `DraftingOutcome.flags` = rules flags + AI flags, +1 for an invalid proposal, a non-`LlmOk` answer or no active grounding tuple (fail closed).
- **Lead page** (`lead_drafts.html`): a Draft with `draft_failed_at` and no revision shows "Drafting failed: <reason text>. Regenerate to retry." (autoescaped).
- AD-16 scenario `drafting.draft_step`; a test that with AI spend at 100% `draft_sequence` and `grounding_check` still run while `domain.budget.allow` refuses `extract_signals`.

**Never:** sending email, a Regenerate control (4.20), batching (4.17), Steps 2–4 drafting (4.18), Signal Inbox triggers (4.16), fallback copy (4.19), storing prompt or response text, a new table, a claims column on revisions, a test reaching Claude, a new dependency.

### Decisions made overnight (pending user review)
1. Effort `medium` for `draft_sequence` (spine review's cost sizing; 4.21 re-sizes).
2. `draft_sequence` is batchable (spine AD-8); the 4.14 "no fallbacks on a batchable candidate" check is relaxed to "only the direct request carries them".
3. A retryable gateway error is retried by the job runner (no revision exists yet), unlike 4.14's final fail.
4. Claims aren't stored on revisions: the AC lists only Evidence ids, fact versions and the cited Signal; the 4.14 AI check re-derives claims for every revision.
5. The smoke-gate scorer runs the active `grounding_check` per spine AD-12; without one, every case counts as flagged.
6. A `Blocked` subject gets an `events` row but no `draft_failed_at` (the suppression path stops the Enrollment).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Happy | tuple active, valid proposal | rev 1 `generated`, traceability, `check_grounding` enqueued at the urgency | N/A |
| Enroll | `request_steps([1..4], soon)` | one `draft_step:<enr>:1` job | no queue → RuntimeError, nothing written |
| Blocked | suppressed Contact | no call, no rev, events row | N/A |
| No tuple | none active | `draft_failed` `no_route`, no call | fail closed |
| Refused / truncated / invalid | gateway result | no rev, `draft_failed` with reason | N/A |
| Bad citation | claim cites unknown Evidence | discarded, `invalid_proposal` | N/A |
| Dead Signal | `cited_signal_id` not a live Event | discarded | N/A |
| Re-run | job twice | second no-op, one call | N/A |
| Contact detail | Evidence text holds email/phone | prompt shows `[email]`/`[phone]` | N/A |
| Budget 100% | spend at cap | drafting + grounding run; `extract_signals` refused | N/A |

</frozen-after-approval>

## Code Map

- `nextron/app/drafting/api.py` -- `request_steps` (:423; add enqueue after slot insert, `queue` check before writes); `_append` (:508, `claims=`, `urgency=`), `Traceability` (:185), `_grounding` (:281), `_check_text` (:233). Docstring.
- `nextron/app/drafting/grounding.py` -- the 4.14 template to copy: prepare/run/apply, `_active` (:142), `_evidence_text` (:150), erasure re-check in apply, logging without text.
- `nextron/app/sequencing/api.py:317,404` -- `Sequencing(clock, queue=None)`; `Drafting(self._clock, self._queue)`. Callers in tests: `tests/app/test_enroll.py`, `test_draft_slots.py`, `tests/contracts/test_ad16_callers.py:1213,1230,1251` (use the `queue_on(clock)` helper pattern from `tests/app/test_leads.py`).
- `nextron/adapters/ai/routes.py`, `schemas.py` (`_Strict`, `OutputSchema` like `GROUNDING_CHECK_V1`), `prompting.py:99` `build_request` (add `framing`), `__init__.py:336` `run`; `nextron/ports/llm.py` `LlmPort.run`; `tests/fakes/llm.py` `ScriptedLlm` (record the framing for assertions); `nextron/domain/ai.py:152` `check_server_fallbacks`, add `Framing`.
- `nextron/adapters/ai/prompts/draft_sequence/v1.md` + `manifest.lock` line; must satisfy `tests/contracts/test_prompt_manifest.py` (untrusted-data/quote wording ~:174, field names ~:187, `SHIPPED_CANDIDATES` ~:260, `PLAYBOOK_ROUTES`, scorer requirement :340).
- Masker: `nextron/domain/pseudonymise.py` has `_EMAIL_PATTERN` (:98) and `_phones` (:274) -- add a public `mask_contact_details(text)` there (email + phone only) and reuse it.
- New pure `nextron/domain/drafting.py` (or `step_drafting.py`) -- job constant `DRAFT_STEP_JOB`, payload fields, `DraftFailure` reasons + texts, input→blocks builder, framing builder, `validate_proposal`. Reuse `grounding_rules.Claim`, `domain.signals.is_live/age_days`, `domain.ai.UntrustedBlock`.
- `nextron/app/queries/*`: `research_queries.evidence/signals/signal_evidence_ids/contact`, `setup_queries.campaign/sequence_by_id/segment_by_id/market_by_id` (+ variant lookup for `step_angles`; add a query if missing), `playbook_queries.active_tuple/active_tuple_id/allowed_facts`.
- Migration `nextron/db/alembic/versions/0048_4_15_draft_failed.py` (down `0047_4_14_ai_grounding`); `db/models/drafting.py` `DraftRecord`; `db/repos/drafting/drafts.py` (`mark_draft_failed`, `clear_draft_failed`); `owners.py:389` drafting group; `pii_registry.py`; `queries/drafting.py` `DraftView` fields.
- `nextron/domain/ai_test_gate.py:395` `DraftingOutcome`; `nextron/app/playbook/api.py:1287` `Scorer`/`SCORERS`, the run loop (:1464 `scorer.inputs` → `llm.run`; extend so a scorer can supply framing and a second, grounding call); `nextron/domain/grounding_check.py` builder/`read_answer`; cases `tests/ai_sets/drafting_smoke/*.json` (14).
- Job wiring: `nextron/jobs/handlers.py` (pattern of `check_grounding`), `nextron/main.py:~1800` `build_job_types`, `:1252` build the service, `enqueue_only_job_types`; pins `tests/contracts/test_job_contracts.py`.
- Web: `nextron/web/leads_page.py` `DraftSource`/`DraftRow`, `nextron/web/templates/lead_drafts.html`.
- Pins: HEAD ×3, chain probes, `test_source_tree` `FILES`, `test_ad16_callers.py` (`Caller("drafting.draft_step", ("app/drafting/",), r"StepDrafts\.draft")` + scenario), schema lock for `DraftSequenceV1`, job-type lists in health tests.

## Tasks & Acceptance

**Execution:**
- [x] AI layer: `Framing`, port/gateway/fake/`build_request` framing, fallbacks check relaxed, route + schema + prompt + manifest; unit tests (framing order and cache_control, no framing unchanged, fallbacks on direct Opus only).
- [x] Domain drafting module + masker + unit tests (validation matrix, masking, blocks, framing text).
- [x] Migration, model, repo, owners/PII, queries.
- [x] `request_steps` enqueue; `Sequencing` queue; `StepDrafts` use case; job type + handler + main wiring.
- [x] Drafting smoke scorer + test-gate tests (passing and failing runs, no grounding tuple).
- [x] Lead page failure text + web test.
- [x] `tests/app/test_step_drafting.py` -- the I/O matrix through the job with `ScriptedLlm`; AD-16 scenario; budget test.
- [x] `nextron-engine/README.md` -- Story 4.15 section.

**Acceptance Criteria:**
- Given a valid proposal, then revision 1 and its `check_grounding` job are written in one unit of work, and the claims reached `grounding_rules.check`.
- Given any failure path, then no revision exists and the prompt sent (if any) held no email address or phone number.

## Implementation Notes

- **AI layer.** `domain.ai.Framing(brief, facts, note)` (texts hidden from `repr`) and `check_framing`; `LlmPort.run`, `ClaudeGateway.run`, `ScriptedLlm.run` (records `framings`) and `prompting.build_request` take keyword-only `framing=None`. System blocks: route prompt, `framing.brief`, Playbook section, `framing.facts` (blank parts left out), `cache_control` on the last only; the user turn opens with `<campaign_note>`, the escaped note, `</campaign_note>` and a blank line (left out for a blank note). No framing: byte-for-byte unchanged (unit-tested). `check_server_fallbacks` now only requires a `bool` batchable and Opus for fallbacks. Adapters raise only `AdapterError` (contract), so the framing type check lives in the domain.
- **Route.** `draft_sequence` candidate exactly as specified; `DraftSequenceV1`/`DRAFT_SEQUENCE_V1`; prompt `prompts/draft_sequence/v1.md` + manifest line; schema lock, `SHIPPED_CANDIDATES`, `PLAYBOOK_ROUTES` and source-tree pins updated.
- **Masker.** `domain.pseudonymise.mask_contact_details` (email then phone, same patterns as `pseudonymise`, idempotent); unlike `pseudonymise`, a phone run there must hold at most 15 digits and must not be one of `grounding_rules._NOT_PHONE`'s shapes (dates, year ranges, numbers grouped in thousands; a copy kept equal by a unit test, so `pseudonymise` stays standard-library-only).
- **Domain.** `nextron/domain/step_drafting.py`: job/payload constants, `DraftFailure` + `failure_text`, `brief_section`/`facts_section`/`note_text`/`framing`, `input_blocks` (sources `evidence`, `signal`, `contact`; block ids are Evidence/Signal ids and `contact`), `read_proposal`, `validate_proposal`, `claims_of`, `citations_of`. Every prompt text is masked. `nextron/domain/drafting_smoke.py` holds the smoke scorer's pure parts.
- **Migration** `0048_4_15_draft_failed.py` (revision `0048_4_15_draft_failed`, down `0047_4_14_ai_grounding`): rebuilds `drafts` from 0045's exact table (`copy_from`), adds `draft_failed_at`, `draft_failed_reason` (String 32) and `ck_drafts_draft_failed`. Owners: `drafting` content group; PII `none`. Repo `mark_draft_failed` / `clear_draft_failed`; `DraftView`/`StoredDraft` gain both fields (defaulted).
- **Enqueue.** `Drafting.request_steps` refuses with `RuntimeError` right after its argument checks when built without a queue, and enqueues `draft_step:<enr>:1` on the urgency's lane (bulk for `bulk`, else interactive) when it inserts a Step 1 slot. `Sequencing(clock, queue=None)`; `enroll` refuses without a queue right after its argument checks (before any read or write).
- **Use case.** `app/drafting/generation.py` `StepDrafts(clock, llm, queue).draft` returns `StepDraftOutcome` (`drafted`, `failed`, `blocked`, `skipped`). A Step other than 1, an unknown Enrollment, a missing/non-ready/already-texted slot, or a missing/erased Contact is `skipped`. A missing Campaign/Sequence/Segment/Market, or a stored Sequence version other than the Enrollment's `sequence_version`, is `not_composable` with no call. Validation is the domain check plus `api._check_text` on the stripped subject/body (both are stored stripped). At apply, a Contact blocked/erased during the call is `skipped` (logged, no failure recorded). Live Events use `is_live(EVENT, tier, age_days(...))` at the clock's now.
- **Job.** `draft_step`: interactive by default, or bulk, priority 40, 15 min, retry 3, `other`; `handlers.draft_step` calls `draft(enrollment_id, step, urgency)` with `Urgency.BULK` on the bulk lane and `Urgency.SOON` on the interactive lane; `build_job_types(..., step_drafts=None)` (default provider raises); `build_app` builds `StepDrafts(clock, llm, queue)`; enqueue-only registry passes `_unwired`.
- **Smoke gate.** `Scorer` gained optional `context` (read in the run's first transaction) and `run_case`; `inputs`/`score` are now optional (the existing sets still pass them). `SCORERS[DRAFTING_SMOKE]` reads the `default` Sequence's Step 1 brief, each Market's English variant and the active `grounding_check` tuple; per case: draft call (with framing, `bulk`), `draft_flags` (non-`LlmOk` = 1 flag; invalid proposal = 1 flag plus the rules' flags; no grounding call), then for a valid proposal the grounding call over `grounding_check.input_blocks` with the claimed Evidence/facts (no tuple = 1 flag, non-`LlmOk` = 1 flag, else `read_answer` flags). Both calls' cost summed.
- **Lead page.** `DraftRow.draft_failed`; `lead_drafts.html` shows `<p class="draft-failed" role="alert">` autoescaped.
- **AD-16.** `Caller("drafting.draft_step", ("app/drafting/",), r"StepDrafts\.draft")` with a scenario (no tuple: gate before the `UPDATE drafts`; blocked: only the `drafting_blocked` event).

### Decisions made overnight (implementation, pending user review)
7. The `draft_step` payload carries no urgency (as specified): `request_steps` enqueues it on the caller's urgency's lane (`bulk` on the bulk lane, else interactive), and the handler derives the urgency from the job's lane as `check_grounding` does (bulk lane `bulk`, interactive lane `soon`), so revision 1's `check_grounding` follows the same lane. `enroll` passes `soon`.
8. Smoke cases are fixed fixtures, so a case's Events are judged by the case (`is_event`: Tier 1–2 with `observed_on`, class `event` from the Segment) rather than by age at run time; the rules' context uses no contact details (fail closed), and a missing `default` Sequence or Market variant flags the case (`no_brief`) with no call.
9. `enroll` without a queue refuses before any read or write (rather than after inserting the Enrollment and rolling back).

- **Verified (implementer, targeted only):** `tests/contracts` (1928 passed, 7 skipped; one schema-lock probe that used `draft_sequence` as its "unpinned" route moved to `draft_reply`, then passed), `lint-imports` 29 kept; new `tests/unit/test_step_drafting.py`, `tests/app/test_step_drafting.py`, smoke-run tests in `tests/app/test_ai_test_runs.py`, and every touched suite (enroll, draft slots/revisions/grounding, AI grounding, AI content page, serve, serve process, runner lifespan, health) pass. Count and list pins updated for the sixth route and the new job type.
- **Review patches (pass 1).**
  - #5: `request_steps` enqueues `draft_step` on the bulk lane for `bulk` (else interactive); the job type's lanes are {interactive, bulk}; `handlers.draft_step` maps the lane to the urgency (bulk lane `bulk`, interactive `soon`); job-contract pins updated, plus a bulk-lane handler test and a `request_steps(..., BULK)` lane test.
  - #9: the garbled comment in `tests/app/test_draft_slots.py` rewritten; over-long docstring lines in `nextron/main.py` and `tests/fakes/llm.py` wrapped.
  - #10: tests for `_prepare`'s `not_composable` (Sequence version moved on; Segment gone) with no call, the erased-Contact skip (no call, no revision), and that re-requesting Step 1 enqueues no second `draft_step`.
  - #14: `StepDrafts._framing` raises `LookupError` (`not_composable`, no call) when the stored Sequence's version isn't the Enrollment's `sequence_version`; tested.
  - #17: `mask_contact_details` keeps dates, year ranges (`2019-2024`, `2019 - 2024`), grouped numbers (`10 000 000`, `1.250.000`) and runs over 15 digits; `pseudonymise()` unchanged; unit tests (incl. `020 7946 0318`, `+1 (614) 555-0142` and an email still masked).
  - #20: `tests/unit/test_ai_gateway.py` sends a `Framing` through `ClaudeGateway.run`: both the first request and the truncation retry have `system` = [prompt, brief, Playbook, facts] with `cache_control` on the last only and a user turn opening with `<campaign_note>`; a non-`Framing` raises `TypeError` before any request.
  - #21: a `StepDrafts` test with a Variant Step 1 angle note and a Segment × Market Step 1 angle (both reach `llm.framings[0]`); the clean smoke run asserts every non-null case `angle_note` is in its draft call's framing note.
  - #22: smoke-run `cost_micros` asserted (draft + grounding per case; draft only without a grounding tuple); `test_0048_keeps_every_draft_and_adds_the_failure_columns`; a smoke run with no stored `default` Sequence fails with every case `no_brief` and no call.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 29 raw, 22 after dedup; 11 patched, 4 deferred, 7 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1+EC1+EC8: a `no_route`/`refused`/`truncated` failure, or a job dead after AdapterError retries, never recovers; dead job leaves no `draft_failed_at` | medium | true; nothing re-enqueues `draft_step` on tuple activation; Regenerate is 4.20 (spec Never); a dead job raises the `job_dead` alert | defer |
| 2 | BH2: a retryable `LlmFailed` is recorded as permanent | false | the gateway returns `LlmFailed` only with `retryable=False` (port docstring); retryable failures raise `AdapterError`, which propagates | reject |
| 3 | BH3: Opus is called when the Lead has no live Event, so the proposal is certain to be discarded | medium | true cost waste; a Lead with no live Event is 4.19's fallback-copy case; a new failure reason changes the frozen reason list | defer |
| 4 | BH4: the prompt has no Lead/organisation name | low | the AC lists Evidence, Signals and the Contact's name and role only; the grounding check catches an invented name | reject (would change the approved spec) |
| 5 | BH5+EC10: `request_steps` drops the caller's urgency; `draft_step` always runs on the interactive lane at `soon` | low | frozen spec says "at the caller's urgency"; `check_grounding` already maps lane → urgency | patch: enqueue on the urgency's lane (bulk → bulk lane), add the bulk lane, handler takes urgency from the lane |
| 6 | BH6: "Regenerate to retry" names a control that doesn't exist yet; a Blocked slot shows only "No text yet" | low | the text is the AC's; decision 6 | reject |
| 7 | BH7+EC7: a re-run on a still-blocked subject writes another `drafting_blocked` event | low | jobs retry only on an exception; the job key is unique per Enrollment/Step | reject |
| 8 | BH8: double `allowed_facts`/`campaign` reads, private cross-module imports, repeated `FIRST_STEP` | low | same transaction, no harm named | reject |
| 9 | BH9: import order, >100-char lines, a garbled double comment in `test_draft_slots.py:144` | low | the repo has no ruff config; the garbled comment is a direct correction | patch: fix the comment and the over-long lines |
| 10 | BH10+VG3: no test of `_prepare`'s `not_composable` (setup row gone / no steps), the erased-Contact skip, or that re-requesting Step 1 enqueues no second job | medium | removing the `try/except LookupError` fails no test | patch: tests |
| 11 | BH11: the code check accepts zero claims and doesn't tie claims to the cited Signal's Evidence | low | the frozen validation list is exactly what's coded; rules + AI check judge grounding | reject (would change the approved spec) |
| 12 | BH12: the smoke gate calls at `bulk`, production at `soon` | low | urgency changes no request field; fallbacks are per candidate | reject |
| 13 | BH13: spec status/tasks not updated | false | updated by the orchestrator in step 3 | reject |
| 14 | EC2+VG-other: `_framing` reads the Sequence without checking `enrollment.sequence_version`, so a newer brief may be used | medium | `request_steps` refuses that mismatch | patch: version mismatch → `LookupError` → `not_composable`, no call; test |
| 15 | EC3: a Contact without email / Market fails only after the paid call | low | enroll's Eligibility needs an email; rare | reject |
| 16 | EC4: a Contact erased/blocked during the call is skipped with no visible reason | low | erased: nothing to show; blocked: suppression stops the Enrollment | reject |
| 17 | EC5: the masker turns year ranges, spaced thousands and dotted numbers in Evidence/fact wording into `[phone]` | medium | `pseudonymise._PHONE_PATTERN` has no date-range/thousands exclusion (unlike `grounding_rules._NOT_PHONE`); a fact's number would reach the model altered | patch: the masker skips the `_NOT_PHONE` shapes; unit tests |
| 18 | EC6: a grounding-tuple `AdapterError(llm_unknown_route)` in a smoke run fails the drafting run as `unknown_route` | low | an active tuple always has a candidate (gate) | reject |
| 19 | EC9: route prompt and Playbook text aren't masked | low | today's Playbook holds no email or phone; a future Playbook edit could add one | defer |
| 20 | VG1: no gateway test sends a `Framing` (first request and the truncation retry) | medium | dropping `framing=` in `ClaudeGateway.run` fails no test | patch: tests in `test_ai_gateway.py` |
| 21 | VG2: Variant Step 1 angle note and Segment × Market Step 1 angle never tested through `StepDrafts`/the smoke path | medium | the seeds have no such values | patch: tests |
| 22 | VG4+VG5+VG6: smoke run `cost_micros` unasserted; no 0048 row-preservation test; the smoke no-brief path never run | medium | each removable with no test failing | patch: tests |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- all pass
- `uv run pytest -q -n 2 tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept

**Final gate:** full suite through the lock on the final tree: 17892 passed, 10 skipped; lint-imports 29 kept, 0 broken. A first run had one failure, `tests/app/test_run_supervisor.py::test_a_beat_slower_than_the_poll_never_counts_as_stale` (a 1 s stale-threshold timing test, untouched by this story; it passed alone, 37/37), and the re-run passed.
