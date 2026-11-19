---
title: "Story 4.14: Run the AI grounding check and fail closed"
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '651a29d5c99461eb3a1f06c505c31a9f08335a05'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since 4.13 every Draft revision gets a deterministic-rules result that is only ever `fail` or `pending`; nothing checks claims against Evidence and facts, so no Draft can ever be `pass`, and nothing shows the result on the Lead page.

**Approach:** Every stored revision enqueues `check_grounding:<draft_id>:<rev>` in its own transaction. The job runs the `grounding_check` route (Haiku, `essential`, Playbook-pinned) over the revision and its cited Evidence and facts, re-runs once on a tested Opus fallback tuple on a refusal, invalid or truncated answer, feeds the AI's claims back into `domain.grounding_rules.check`, and updates the revision's `grounding_results` row: `pass` only when the AI check succeeded and nothing flagged, else `fail` with every flag. The Lead page shows the badge and flags.

## Boundaries & Constraints

**Always:**
- **Route `grounding_check`:** candidate `claude-haiku-5-5`, effort `medium`, prompt `v1`, schema `GroundingCheckV1` (schema 1), not batchable, `budget_class=ESSENTIAL`, `uses_playbook=True` (cached prefix = instructions + the tuple's Playbook snapshot). **Route `grounding_check_fallback`:** its only candidate is `claude-opus-5-5`, same schema and an identical-text prompt file of its own, `ESSENTIAL`, `uses_playbook=True`, carrying server-side refusal fallbacks (AD-8: request header `anthropic-beta: server-side-fallback-2026-07-01`, body `fallbacks: "default"`, sent via `extra_headers`/`extra_body`; only a `Candidate` with `server_fallbacks=True` gets them; no batch path exists yet). Both are in `ROUTE_SETS` → `TestSet.GROUNDING`; case lookups for a route use the set's case route (`grounding_check`), so the fallback is gated on the same 49 cases. Neither is bootstrap-activated: each goes live only through the 4.5 gate.
- **`GroundingCheckV1`:** `claims[]` of `{span, evidence_id|null, fact_id|null, fact_version|null, supported: bool, explanation ≤200}` and `flags[]` of `{code, span, explanation ≤200}`, `code` ∈ {`fake_familiarity`, `false_urgency`, `competitor_claim`, `two_calls_to_action`, `deceptive_subject`, `repeats_earlier_step`} (the `GroundingPattern` values).
- **Input (one Lead only):** one pure domain builder, used by the job and the test-gate scorer, turns a grounding input (Step number, Market, subject, body without footer, cited Evidence `{id, kind, text}`, cited facts `{fact_id, version, wording}`, earlier Steps' current subject/body) into `UntrustedBlock`s (`source` e.g. `draft`, `evidence`, `fact`, `earlier_step`). No email address or phone number from Settings goes into the prompt beyond the Draft text itself.
- **Fail closed — result rules:** no active `grounding_check` tuple, a `LlmFailed`, an `AdapterError` (timeout, 5xx, connection), or a refused/invalid/truncated answer with no active fallback tuple or a non-`LlmOk` fallback answer → `fail` with flag `checker_unavailable`. A claim with neither `evidence_id` nor fact → `uncited_claim`; `supported=false` → `unsupported_claim`; each pattern flag → a flag with its code. The AI's claims become `Claim(span, evidence_id, (fact_id, fact_version))` and `grounding_rules.check` re-runs with them (context built as 4.13's `_grounding`), so text rules and citation rules both apply. Final flags = rules flags then AI flags; `pass` iff no flags and a tuple served the answer. A span not found in subject/body is stored as `""`.
- **`grounding_results` (new migration):** add `ai_checked_at` (nullable); each stored flag gains `source` ∈ {`rules`, `ai`}; keep `ck_grounding_results_fail`; add a CHECK that `pass` requires non-null `route_tuple_id`, `served_model` and `ai_checked_at`. The update path is a repo `record_ai_result` that sets status, flags, tuple, served model (`LlmResult.model`) and `ai_checked_at` only when `ai_checked_at` is NULL (idempotent; a re-run job is a no-op). Erasure tombstone also replaces AI flags' explanations with `[erased]`.
- **Job:** type `check_grounding`, scope `draft_id`, discriminator `rev`, payload ULIDs/scalars; lanes interactive (default; `interactive` and `soon` urgency) and bulk. `_append` takes an `urgency`: `revise` → `interactive`; `recompose_ready` (footer change) → bulk lane; generated → the caller's (Step 1 `soon`). `Drafting(clock, queue=None)`; `_append` refuses before any write without a queue.
- **No AI call for an uncontactable subject:** the job calls `compliance_api.assert_contactable(contact, "email", "cold")` first; if it refuses, no call, result `fail` with `checker_unavailable` (explanation says the Contact is no longer contactable). Add an AD-16 scenario.
- A result for a non-current revision is stored on that revision's row only; the Lead page shows the current revision's result.
- **Test gate:** a `SCORERS[TestSet.GROUNDING]` scorer: inputs via the shared builder; a case counts as flagged when its pinned `expected.rules` is non-empty (proved equal to `check` by 4.13's fixture test) or the AI answer has any uncited/unsupported claim or flag; a non-`LlmOk` answer counts as flagged (fail closed); metrics `grounding_metrics`.
- **Lead page:** in `lead_drafts.html`, per Draft, a `pass` / `fail` / `pending` badge for the current revision (none → `pending`); for `fail`, each flag's code, its span in `<mark>` (autoescaped) and explanation.

**Never:** a call to `domain.budget.allow` for these routes, a new table, storing prompt or response text, claims storage on revisions (4.15), batching (4.17), a recheck button, approval gating (Epic 5), a test reaching Claude, a new dependency.

### Decisions made overnight (pending user review)
1. The Opus fallback is its own route `grounding_check_fallback` (deferred 4.5 item): one route has one active tuple, so a second candidate could never be tested or active.
2. A timeout or transport error is a final `fail` (`checker_unavailable`), not retried: the AC lists it as fail closed. A recheck path is later work.
3. Footer-change revisions are AI-checked too, on the bulk lane, so every revision can reach `pass` and claim flags can't be lost by a footer change (closes the 4.13 BH1 deferral for this story's part).
4. An uncontactable subject gets no AI call and a `checker_unavailable` fail.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Clean | rules clean, Haiku ok, all claims supported+cited | `pass`, tuple + `claude-haiku-5-5` stored | N/A |
| Rules + AI flags | banned phrase, unsupported claim | `fail`, both flags, rules first | N/A |
| Haiku refusal | fallback active, Opus ok | result from Opus; `served_model` Opus | N/A |
| Final refusal | Opus refuses too | `fail` `checker_unavailable` | fail closed |
| No tuple | no active `grounding_check` | `fail` `checker_unavailable`, no call | fail closed |
| Timeout | gateway raises `AdapterError` | `fail` `checker_unavailable` | job succeeds |
| Uncited | claim without citation | `fail` `uncited_claim` | N/A |
| Citation outside set | AI claim cites foreign Evidence | `fail` `citation_outside_set` | N/A |
| Old revision | job for rev 1 after rev 2 | rev 1 row updated; page shows rev 2 | N/A |
| Re-run | job runs twice | second is a no-op, one call | N/A |
| DB guard | `pass` with NULL tuple | IntegrityError | N/A |

</frozen-after-approval>

## Code Map

- `nextron/adapters/ai/routes.py` -- add both `Candidate`s; `Candidate.server_fallbacks: bool=False` (refuse it on a non-Opus model); docstring route list.
- `nextron/adapters/ai/prompting.py:99` `build_request` -- add `extra_headers`/`extra_body` when `candidate.server_fallbacks`.
- `nextron/adapters/ai/schemas.py` -- `GroundingCheckV1` (`_Strict`, `OutputSchema` constant like `SIGNAL_INBOX_PARSE_V1`).
- `nextron/adapters/ai/prompts/grounding_check/v1.md`, `grounding_check_fallback/v1.md`, `manifest.lock` (hand-added SHA-256 lines per `nextron/domain/prompts.py`); prompts must satisfy `tests/contracts/test_prompt_manifest.py` (:174 untrusted-data and quote wording, :187 field names, :260 `SHIPPED_CANDIDATES`, :333 scorer requirement).
- `nextron/domain/ai_test_gate.py:112` `ROUTE_SETS`; `nextron/domain/ai_test_sets.py:153` `CASE_ROUTES` -- add a `case_route(route)` helper; use it in `nextron/app/playbook/api.py` (`queue_runs` :1330, `activate`, `_prepare` ~:1480 `cases_repo.live`) and `nextron/app/queries/playbook.py` (`case_set_fingerprint` callers :386/:396/:519).
- `nextron/app/playbook/api.py:1281-1300` `Scorer`/`SCORERS` -- grounding scorer; `GroundingOutcome`, `grounding_metrics` (`ai_test_gate.py:335-390`).
- New pure `nextron/domain/grounding_check.py` -- input builder (blocks), answer → `(claims, ai_flags)` with the uncited/unsupported/pattern mapping, flag codes, final combine. Reuses `grounding_rules.Claim`/`check`/`RULE_IDS`, `domain.ai.UntrustedBlock`.
- Gateway pattern to copy: `nextron/app/research/extraction.py:255` (`active_tuple`, call outside transactions, `outcome_of`). Gateway: `nextron/adapters/ai/__init__.py:336` `run`; results `nextron/ports/llm.py:80-167`; `AdapterError(True,"llm_unavailable")` is raised, not returned. Fake: `tests/fakes/llm.py` `ScriptedLlm`.
- New `nextron/app/drafting/grounding.py` (or in `api.py`) -- `GroundingChecks(clock, llm).check(draft_id, rev)`: read unit → contactable/tuple → call(s) → apply unit. Reuse `_grounding` from `app/drafting/api.py:261`.
- `nextron/app/drafting/api.py` -- `Drafting(clock, queue=None)`; `_append` (:487) gains `urgency` and enqueues; `revise`, `recompose_ready`; `ERASURE`/tombstone. Callers: `app/sequencing/api.py:404,622,674`, `main.py:2188`, web revise route.
- `nextron/db/alembic/versions/0047_4_14_ai_grounding.py` (down `0046_4_13_grounding_results`), `db/models/drafting.py`, `owners.py`, `pii_registry.py` (`ai_checked_at` none), `db/repos/drafting/grounding_results.py` (`StoredFlag.source`, `record_ai_result`, tombstone).
- Job: constant in domain; handler + Protocol in `nextron/jobs/handlers.py` (pattern :362); `build_job_types` in `nextron/main.py:~1634`; pins in `tests/contracts/test_job_contracts.py` (docstring :5-17, `names()` :593, shapes :709-760, handler contract ~:948).
- `nextron/web/leads_page.py:1517` `DraftSource`, `nextron/web/app.py:1580` `lead_drafts()`, `nextron/web/templates/lead_drafts.html` -- read `queries.drafting.grounding_result` (`app/queries/drafting.py:233`).
- Pins: HEAD ×3, chain probes, `test_source_tree` `FILES`, `tests/contracts/test_ad16_callers.py`, erasure coverage.

## Tasks & Acceptance

**Execution:**
- [x] routes, prompting, schemas, prompts, manifest + `tests/unit` for build_request fallbacks (Opus only) and the schema.
- [x] `domain/grounding_check.py` + unit tests -- builder, mapping, combine, every matrix flag case.
- [x] migration, model, repo, PII/owners -- `ai_checked_at`, `source`, pass guard, `record_ai_result`, tombstone.
- [x] drafting: queue + enqueue in `_append`; `GroundingChecks`; job type + handler; main wiring.
- [x] case-route helper + grounding scorer + test-gate tests (fallback tuple runs on the 49 cases).
- [x] Lead page badge and flags + web test (autoescaped span).
- [x] `tests/app/test_ai_grounding.py` -- the I/O matrix through the job with `ScriptedLlm`; AD-16 scenario.
- [x] `nextron-engine/README.md` -- Story 4.14 section.

**Acceptance Criteria:**
- Given any revision stored, then a `check_grounding:<draft_id>:<rev>` job exists, enqueued in the same unit of work.
- Given any AI error path, then the row is never `pass`.

## Implementation Notes

- **Routes** (`adapters/ai/routes.py`): `grounding_check` (Haiku, medium, v1, `GroundingCheckV1` s1, not batchable, 4,000/8,000 tokens, `essential`, `uses_playbook`) and `grounding_check_fallback` (Opus, same, `server_fallbacks=True`). `Candidate.server_fallbacks` is checked by `domain.ai.check_server_fallbacks` (a `bool`; `True` only on `claude-opus-5-5` and never on a batchable candidate), because adapters may raise only `AdapterError` (error contract). `prompting.build_request` adds `extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}` and `extra_body={"fallbacks": "default"}` only for such a candidate (fresh copies per request). Prompts `grounding_check/v1.md` and `grounding_check_fallback/v1.md` are byte-identical; both hashes are in `manifest.lock`; both are in `SHIPPED_CANDIDATES` (the shipped-candidate test pins a Playbook snapshot for the two grounding routes, `PLAYBOOK_ROUTES`).
- **Test gate.** `ROUTE_SETS["grounding_check_fallback"] = GROUNDING`; `domain.ai_test_sets.case_route(route)` maps a route to its set's case route (extraction routes stay their own). `queries.playbook.case_set_fingerprint` applies it internally (so every caller, `queue_runs`, `activate`, the AI content view and health, uses it), and `AiTestRuns._prepare` reads `cases_repo.live(set, case_route(route))`. `SCORERS[GROUNDING]` uses `domain.grounding_check.grounding_inputs` / `score_grounding` / `grounding_set_metrics` (flagged = pinned `expected.rules` non-empty, or any AI flag, or no `LlmOk`).
- **Domain** `nextron/domain/grounding_check.py`: constants (routes, `CHECK_GROUNDING_JOB`, payload fields, AI codes), `GroundingInput` and `input_blocks` (sources `context`, `draft` (`subject`/`body`), `evidence` (id = Evidence id), `fact` (`<fact_id>.v<version>`), `earlier_step` (`step<n>`)), `read_answer` (uncited takes precedence over unsupported for one claim; a fact needs both id and version), `unavailable`, `combine` (never `pass` unless checked and flag-free), `CheckedFlag` with `FlagSource`.
- **Persistence**: migration `0047_4_14_ai_grounding` (down `0046_4_13_grounding_results`) rebuilds `grounding_results` from 0046's exact table (`copy_from`), adds `ai_checked_at` and `ck_grounding_results_pass`, and backfills `"source": "rules"` into existing flags. Repo: `StoredFlag.source` (default `rules`), `record_ai_result` (conditional on `ai_checked_at IS NULL`), tombstone also erases AI flags' explanations. Owners/PII: `ai_checked_at` drafting / `none`.
- **Drafting**: `Drafting(clock, queue=None)`; `_append(..., *, urgency)` raises `RuntimeError` before any write without a queue and enqueues `check_grounding` (bulk lane for `BULK`, else interactive). `revise` → interactive, `recompose_ready` → bulk. `GroundingChecks(clock, llm).check(draft_id, rev, urgency)` in `app/drafting/grounding.py` (prepare → calls → apply). On success, final rules flags = the rules re-run with the AI's claims, then any stored rules flag the re-run doesn't repeat (so a generated revision's own claim flags are never lost), then AI flags. Every failure path keeps the stored rules flags and adds `checker_unavailable`; the attempted tuple id and served model (when a result came back) are stored on a fail too.
- **Wiring**: job type `check_grounding` (interactive default, lanes interactive+bulk, priority 50, `max_runtime` 25 min, retry limit 3, group `other`), handler `handlers.check_grounding` (urgency from the lane), `build_job_types(..., grounding=None)` (optional, raising default like `unsubscribes`). The `FooterChanged` subscriber in `build_subscribers` needs a queue: it gets `JobQueue(clock, main.enqueue_only_job_types())`, i.e. `build_job_types` with every service unwired (the job-registry contract allows only `build_job_types` to build a registry); the runner's own registry runs the job.
- **Web**: `DraftSource.grounding` (the current revision's `GroundingResultView`), `DraftRow.grounding` / `grounding_flags`, badge + `<ul class="grounding-flags">` with `<mark>` spans in `lead_drafts.html` (autoescaped `|plain`). No CSS added.
- **Existing tests adjusted**: the Story 2.8/4.4/4.5 sync tests (`test_playbook_prompts`, `test_playbook_snapshots`, `test_ai_content_page`, `test_ai_test_runs`) now sync only the extraction routes through their helpers, with new tests for the grounding routes (candidate tuples, never bootstrap-activated; both tuples run the 49 Grounding cases and activate); counts updated where the real startup/check runs (`test_serve`, `test_serve_process`, `test_runner_lifespan`, AI content "Check for changes"); job-type lists in health tests; `test_ai_schemas` accepts the wire schema's `maxLength` moved into the description; `tests/ai_eval/extraction.default_tuples` skips Playbook routes; schema lock pins `GroundingCheckV1`; persistence chain probes bumped to 0048/0049.
- **Tests**: `tests/unit/test_grounding_check.py`, `tests/app/test_ai_grounding.py` (the I/O matrix through `GroundingChecks`), AD-16 scenario `drafting.grounding_check`, job-contract pins, route/prompting tests, HEAD ×3, `test_source_tree` FILES.
- **Review patches (pass 1)**:
  - `test_prompt_manifest.test_a_shipped_candidate_is_still_found` builds the Playbook-pinned tuple only for `PLAYBOOK_ROUTES` (`grounding_check`, `grounding_check_fallback`) and the plain tuple for the rest, so a candidate can't silently flip `uses_playbook`.
  - `GroundingChecks._apply`: a stored rules flag is dropped when the re-run has a flag with the same (code, span), so a changed explanation (e.g. `signal_count`) is never kept twice.
  - `_input` iterates `dict.fromkeys(...)` over `evidence_ids` and `fact_versions`, so a repeated id gives one block instead of a `check_blocks` `ValueError`.
  - `_Call` carries `contact_id`; `_apply` re-reads the Contact and, if it was erased (or is gone) since the prepare, records `fail` with the stored (already tombstoned) rules flags plus `checker_unavailable` (`UNAVAILABLE_NOT_CONTACTABLE`) and no AI flag.
  - New tests in `tests/app/test_ai_grounding.py`: a stored `citation_outside_set` claim flag kept once after a clean (or same-citing) answer; a Step 2 check with two Step 1 revisions (one `step1` block with the current text, none for Step 2; count and fact/value Evidence text; a repeated Evidence id read once); the Opus fallback raising `AdapterError` (fallback tuple id); `_grounding` raising `LookupError` at apply (AI flags + `checker_unavailable`); a Contact erased during the call; `test_build_app_wires_grounding_checks` (the registry's `grounding()` is the built `GroundingChecks`, on the same LLM gateway as `PeopleExtractions`).

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 26 raw, 22 after dedup; 8 patched, 2 deferred, 12 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1: the gate scorer counts a case flagged when its pinned `expected.rules` is non-empty, whatever the model says | low | the frozen spec defines the scorer so; production applies rules + AI together, so the gate measures the check as it runs | reject (would change the approved spec) |
| 2 | BH2: the gate accepts any flag, not the planted code/span | low | `GroundingOutcome(kind, flagged)` is 4.5's design; the 100% clean-control bar fails a tuple that over-flags | reject |
| 3 | BH3: a non-answer counts as flagged in the gate | low | frozen spec rule; a tuple refusing everything fails the clean-control bar | reject (would change the approved spec) |
| 4 | BH4: no recheck/backfill: revisions checked before activation, or after a timeout, stay `fail` | medium | true; spec Never lists a recheck button and overnight decision 2 | defer |
| 5 | BH5: superseded revisions still get paid calls | low | AC requires a result for a non-current revision to be stored | reject |
| 6 | BH6: badge shows `fail` before the AI check runs | false | a rules flag can't be cleared by the AI check; `fail` is already final | reject |
| 7 | BH7: `Drafting(clock, queue=None)` only fails at run time | low | refused loudly before any write; the frozen spec chose the optional queue | reject; noted for 4.15 |
| 8 | BH8: `max_runtime` comment says four requests | false | each route call may retry once on truncation (gateway), so 2 routes × 2 requests | reject |
| 9 | BH9: the shipped-candidate contract accepts either tuple form | low | `routes.find(t) or routes.find(pinned)` lets a candidate flip `uses_playbook` silently | patch: pick the form per route |
| 10 | BH10+VG1: no test that a stored rules flag the re-run doesn't repeat survives | medium | `kept = rules` would pass every test | patch: test |
| 11 | BH11+EC6: stored/re-run merge compares whole flags, keeping two `signal_count` flags with different counts | low | `flag not in rules` compares explanations | patch: compare on (code, span) |
| 12 | BH12: hard-coded `cold` purpose and `step 0` for non-Step Drafts | low | only Step Drafts get revisions today | reject |
| 13 | BH13: span length not in schema; identical prompts unenforced; no `ai_checked_at`/`pending` guard; `source` not validated on decode | low | spans not in the text are stored `""`; only the repo writes `source` from the enum; `record_ai_result` writes only pass/fail | reject |
| 14 | EC1: `InvalidMarkets` in prepare leaves the row `pending` | low | needs a corrupt Market row; the row is never `pass` | reject |
| 15 | EC2+EC3: duplicate Evidence/fact ids give duplicate block ids → `ValueError` from `check_blocks`, no result | low | `_input` doesn't dedupe; a one-line `dict.fromkeys` fixes it | patch |
| 16 | EC4: `_grounding` raising a `DomainError` after a paid call retries the job and repeats calls | low | needs undecodable seeds | reject |
| 17 | EC5: a Contact erased between prepare and apply gets pre-erasure spans and model explanations written after the tombstone | medium | `_apply` doesn't re-check erasure | patch: re-check in apply; erased → `fail` with stored flags + `checker_unavailable`, no AI flags |
| 18 | EC7: summary of EC1/EC2/EC4 | low | covered by rows 14–16 | reject (duplicate) |
| 19 | VG2: `build_app` wiring of `GroundingChecks` untested | medium | siblings `test_domain_checks`/`test_unsub_poll` have it | patch: test |
| 20 | VG3: `_input`'s earlier-Step gathering untested; VG5 count/fact Evidence text untested | medium | no assertion on `earlier_step` blocks | patch: test (incl. Evidence text forms) |
| 21 | VG4+BH10: fallback `AdapterError` and `_rules` → `None` paths untested | low | both fail closed | patch: tests (cheap) |
| 22 | VG6: migration 0047's data step untested | low | `_decoded` treats missing `source` as `rules` | defer |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- all pass
- `uv run pytest -q -n 2 tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept

**Final gate:** full suite through the lock on the final tree: 17772 passed, 10 skipped; lint-imports 29 kept, 0 broken.
