---
title: 'Story 5.6: Edit a Draft, then approve it once the recheck passes'
type: 'feature'
created: '2026-11-20'
status: 'in-review'
baseline_commit: '408a16771a991091c45f1bd315e960e2c033bf82'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-5-4-approve-a-draft-bound-to-the-exact-text-shown.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Mac can only approve a Draft as written. Fixing one wrong word means rejecting or regenerating it, and nothing lets him correct an already-approved, still-queued message.

**Approach:** Add an inline editor (`E`) on the queue card. `Ctrl+Enter` posts "edit then approve". `Approval.edit` checks the shown binding, un-approves an approved Draft first (cancelling its `queued` OM), stores the new revision through `drafting.revise` (recheck at `interactive` urgency) and records a pending approval on the Draft. When that revision's AI grounding result is stored, `drafting` enqueues `complete_pending_approval:<draft_id>:<rev>`. Its handler calls `Approval.complete_pending`, which runs the same `_check` path as `approve` and approves, does nothing (stale), or clears the pending approval (refused).

## Boundaries & Constraints

**Always:**
- **Migration** `0050_5_6_pending_approval` (down `0049_5_4_approvals`). It adds nullable `drafts.pending_approval_rev` (≥1), `pending_approval_hash` (64 lowercase hex) and `pending_review_ms` (≥0) to the `approval` status group. `owners.py` maps them to approval, and `pii_registry` classifies them `none`. Only `approval` writes them: `edit` sets them, and `approve`, `complete_pending` and cascades clear them.
- **`@use_case Approval.edit(draft_id, rev, shown_hash, subject, body, *, served_at=None, first_served_at=None) -> ApproveResult`.** It runs in one `BEGIN IMMEDIATE` transaction, and every refusal writes nothing. Checks, in order:
  1. unknown → `UnknownDraft` (404);
  2. status not `ready`/`approved` → `blocked` NOT_IN_QUEUE;
  3. `rev` ≠ current → `stale`;
  4. card has no `shown_hash` → `blocked` UNBINDABLE;
  5. `shown_hash` ≠ `card_of(draft).shown_hash` → `stale`;
  6. subject/body unchanged from the current revision → `blocked` "Nothing changed";
  7. the text fails drafting's rules (`ValueError` from `revise`) → `blocked` with that message, rolled back.

  For an `approved` Draft, the live OM is checked first:
  - OM `queued` → `sending_api.cancel_for_cascade`;
  - otherwise (or the cancel fails) → `blocked` "Already sending", rolled back;
  - then the Draft moves `approved → ready` (`RETURN_EDITED`, actor `operator`, an event).

  Then `drafting.revise` stores the new revision. The pending fields get the new rev, the new `card_of(...).shown_hash` and `review_ms`. The session counts the decision as in `approve`. The result is `pending`, a new `ApproveOutcome` member with the reason "Recheck pending".
- **`Drafting.revise`** keeps origin `operator_edit` and the `interactive` lane (it already does), and is never batched.
- **Drafting enqueue.** `GroundingChecks(clock, llm, queue=None)`. When `_record` stores a final result, pass or fail, for the revision equal to the Draft's `pending_approval_rev` (read via `queries.approval`), it enqueues `complete_pending_approval` in the same transaction:
  - scope `draft_id`, discriminator `rev`;
  - payload `draft_id` and `rev` only;
  - `interactive` lane.
- **`@use_case Approval.complete_pending(draft_id, rev) -> ApproveResult | None`.** It returns `None` and writes nothing if the Draft is unknown or `pending_approval_rev` ≠ `rev`. Otherwise it runs `_check(draft_id, rev, pending_approval_hash)`, the exact path `approve` uses:
  - `approved` → `_record`: `approvals` row with `approved_by` `operator`, `review_ms` = `pending_review_ms`, `edit_ratio` against revision 1, and an event with actor `operator`, `cause_ref` the approval; one OM; the pending fields cleared; no session change;
  - `stale` → nothing written;
  - `blocked` (grounding fail included) → the pending fields cleared, nothing else;
  - an enqueue failure rolls back and raises, so the job retries and then goes dead (health panel).
- **`approve` and the cascades** also clear the pending fields of the Draft they move.
- **Job type `complete_pending_approval`:** interactive lane only, priority 60, `max_runtime` 2 min, retry limit 3, group `other`. The handler is `handlers.complete_pending_approval(approval)`, and `build_job_types(..., approval=None)` raises if it is missing. Approval needs no budget gate.
- **Card states**, derived:
  - pending rev = current and grounding `pending` → badge and approve notice "Recheck pending" (approve disabled);
  - pending rev < current → badge `stale` and the notice "This Draft changed since you opened it", with the newest text;
  - `fail` → red flags, as in 5.3.
- **Editor.** A native `<details class="queue-edit">` holds a POST form outside the `hx-disable` article (`hx-post /queue/edit`, `csrf_field()`, hidden `draft_id`/`rev`/`shown_hash`). It has an `<input name="subject">` and a `<textarea name="body">` with the current revision's own subject and body. The signature, footer and UK privacy line (the footer snapshot text) show read-only below.
- **Keys.** `queue-keys.js` `KEYMAP` gains `e` → open the editor and focus the subject. Inside the edit form, `Ctrl+Enter` submits and `Esc` resets the form, closes it and refocuses the card; other shortcuts stay off while typing. There is no inline script.
- **Approved, not yet sent.** A section lists `approved` Drafts whose live OM isn't `sent`, showing their OM status and linking to `?draft=<id>`. That card renders with approve disabled ("Already approved") and with the editor.
- **AD-16 scenario** `approval.complete_pending`.
- **`domain.render.MAJOR_EDIT_RATIO = 0.20`** and `is_major_edit(ratio)` (`> 0.20`).

**Never:**
- send or call `MailPort`;
- approve without the Operator's Ctrl+Enter, or a second copy of the checks;
- write approval columns from `drafting`;
- edit the footer or signature;
- reject or `R` (5.5);
- a new AD-10 trigger or a new dependency;
- a write from a GET.

### Decisions made overnight (pending user review)
1. One job serves both results. A `fail` (or any refusal) clears the pending approval through `complete_pending`, because `drafting` may not write approval columns (AD-3: drafting sits below approval).
2. The shown binding is fixed at edit time: the composed hash of the new revision. A changed address or a newer revision before completion is `stale`, and nothing is approved.
3. An edit that changes nothing is refused ("Nothing changed"), so a no-op makes no revision. Press `A` instead.
4. The edit POST answers the same card showing "Recheck pending". The session decision is counted at the edit, and the `approvals` row gets the review time measured then.
5. An enqueue failure at completion retries the job and leaves the pending approval, so the Operator can still press `A` once the result is `pass`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Edit | ready rev 1, changed body | rev 2 `operator_edit`, pending=2, `check_grounding` interactive, "Recheck pending" | N/A |
| Pass | pending rev 2 result `pass` | job → approvals row, Draft `approved`, one `queued` OM, pending cleared | N/A |
| Fail | AD-18 stray URL / AI fail | job → pending cleared, card red with flags | N/A |
| Newer rev | footer recompose → rev 3 | complete_pending writes nothing; card `stale`, newest text | `stale` |
| Address changed | before completion | nothing approved | `stale` |
| Old binding | posted rev/hash outdated | nothing written | `stale` |
| Approved + queued | E from the approved list | OM `cancelled`, Draft `ready` → edit proceeds | N/A |
| In flight | OM `sending`/`unknown` | "Already sending", nothing changes | `blocked` |
| Suppressed | Contact suppressed before completion | pending cleared, no approval | `blocked` |
| Bad text | empty subject, body > 10,000 | message shown, nothing written | `blocked` |

</frozen-after-approval>

## Code Map

All paths are under `nextron-engine/`.
- `nextron/app/approval/api.py`:
  - `_check` (:301) is reused as is: no copy;
  - `_record` (:347) takes `review_ms` and clears the pending fields;
  - `_count_decision`, `_RolledBack` pattern (:253-257);
  - `_cancel_approved` (:461) un-approve logic: reuse it for edit's OM cancel;
  - `Approval(clock, *, sending=None, drafting=None)`: `edit` needs `drafting` (`TypeError` without it).
- `nextron/app/drafting/api.py`:
  - `revise` (:578) needs `ready` (approval moves the Draft back first);
  - `MAX_SUBJECT`/`MAX_BODY` (:177);
  - `_append` enqueues `check_grounding` on the interactive lane.
- `nextron/app/drafting/grounding.py`: `GroundingChecks.__init__` (:243), `_record` (:217), called from `_fail_now` (:269) and `_apply` (:348). Add the conditional enqueue after a recorded result. Imports: drafting may use any `app.queries.*`, never `app.approval`.
- `nextron/app/queries/approval.py`:
  - `QueueCard` (:140); `_card` (:352): expose the revision's own subject and body and the footer text for the editor, plus the pending state;
  - `card_of` (:536), `approval_of` (:577), `due_queue` (:633);
  - add `pending_approval(draft_id)` and `approved_unsent()`.
- `nextron/db/repos/approval/drafts.py`: setters and clearers for the pending fields. Model `nextron/db/models/drafting.py` `DraftRecord` status group (:197). Migration pattern in `versions/0049_5_4_approvals.py`. `db/owners.py` :417, `db/pii_registry.py` :414.
- `nextron/domain/draft.py`: `(APPROVED, RETURN_EDITED)` exists (:137). `nextron/domain/render.py`: `edit_ratio`.
- `nextron/jobs/handlers.py`: the `check_grounding` pattern (:525-543). `nextron/main.py`:
  - `build_job_types` (:1478), the `CHECK_GROUNDING_JOB` registration (:1812), `enqueue_only_job_types` (:1832);
  - runner wiring: `grounding_checks` (:1259) gets the queue; the runner gets an `Approval(clock, sending=codec-bearing Sending, drafting=...)` provider;
  - `build_app` (:1370-1392) passes `drafting=Drafting(clock, queue)`.
- Web:
  - `nextron/web/app.py`: `queue_answer` (:697), `queue_approve` (:737). Add `POST /queue/edit` (`queue_edit`, `@personal_data(True)`). Refusals and pending answer 200 focusing `?draft=`; unknown → 404.
  - `nextron/web/queue_page.py`: `card_row` (:396), `_approval_block` (:381), `queue_context` (:485), `submitted_approval` (:449), the `MAX_FIELD_LENGTH` 128 for binding fields (subject ≤ 200 and body ≤ 10,000 need their own limits).
  - `templates/queue.html` (approve form at the end of the card), `static/queue-keys.js` (`KEYMAP`, `typing`).
- Tests:
  - helpers in `tests/app/test_approve.py`, `test_due_queue.py`, `test_card_details.py`, `test_ai_grounding.py`, `test_outbound_messages.py`;
  - `tests/contracts/test_ad16_callers.py` (the approve scenario);
  - job contract pins (grep `CHECK_GROUNDING_JOB` in tests);
  - HEAD ×3 (`test_persistence_contracts`, `test_migrations`, `test_serve_process`), chain probes, `test_source_tree` FILES;
  - the `create_app` kwargs in `tests/dashboard.py`, `tests/unit/test_serve.py`, `tests/unit/test_runner_lifespan.py`.

## Tasks & Acceptance

**Execution:**
- [x] Migration, model, owners, PII, repos.
- [x] `domain/render.py` `MAJOR_EDIT_RATIO` and `is_major_edit`.
- [x] `app/approval/api.py`: `edit`, `complete_pending`, the `pending` outcome, clearing in `approve` and the cascades.
- [x] `app/drafting/grounding.py`: the queue and the conditional enqueue.
- [x] `queries/approval.py`: card fields and pending state, `pending_approval`, `approved_unsent`.
- [x] `jobs/handlers.py` and `main.py`: the job type and wiring.
- [x] Web: the route, `queue_page.py`, `queue.html`, `queue-keys.js`.
- [x] Tests: new `tests/app/test_edit_approve.py` covering the I/O matrix on a migrated temp DB with a frozen clock and fake LLM. No-write is proven by row counts. The flow runs end to end: edit POST → grounding job → `complete_pending` job.
- [x] AD-16 scenario, pins, and a `README.md` "Story 5.6" section.

**Acceptance Criteria:**
- Given any test DB after these tests, every OM has an `approvals` row, and every edit-approval's revision has origin `operator_edit` with an `approvals.rev` equal to it.
- Given the queue page, the editor form has no inline script, and `queue-keys.js` maps `e`, Ctrl+Enter and Esc from the one `KEYMAP` table.
- Given `edit_ratio` 0.21, `is_major_edit` is true; given 0.20, it is false.

## Implementation Notes

**Merge touchpoints (not in this tree):**
- 4.17's batches must never batch an `operator_edit` check.
- 4.20's `drafting.regenerate` makes a newer revision, so the pending approval goes `stale`.
- 8.1: `complete_pending_approval` needs budget class `essential` and an `ESSENTIAL_SAMPLES` entry.
- 6.9 `current_approval` → `approval_of`.
- 5.5's reject from the approved list must reuse edit's un-approve step.
- Renumber migration 0050 after main's head.

**Implementation (track-E, 2026-11-20).** All under `nextron-engine/`.

- **Schema.** `db/alembic/versions/0050_5_6_pending_approval.py` (revision `0050_5_6_pending_approval`, down `0049_5_4_approvals`): rebuilds `drafts` (`batch_alter_table(recreate="always")` with `copy_from` = the table as 0045 left it, no reflection) adding nullable `pending_approval_rev` (`ck_drafts_pending_approval_rev`, ≥ 1), `pending_approval_hash` (`ck_drafts_pending_approval_hash`, 64 lowercase hex) and `pending_review_ms` (`ck_drafts_pending_review_ms`, ≥ 0). Model `DraftRecord` (status group), `owners.py` → `approval`, `pii_registry.py` → `none`. Repo `db/repos/approval/drafts.py`: `PendingApproval`, `pending_of`, `set_pending`, `clear_pending`, `approved_ids`.
- **Domain.** `render.MAJOR_EDIT_RATIO = 0.20`, `render.is_major_edit(ratio)` (`> 0.20`; `TypeError` for a non-number/bool). `domain.draft.COMPLETE_PENDING_APPROVAL_JOB = "complete_pending_approval"` (drafting can't import approval, so the job name lives in the domain).
- **Approval** (`app/approval/api.py`). `Approval(clock, *, sending=None, drafting=None)`. `@use_case edit(...)` runs the spec's check order in one transaction (refusals return before any write); an `approved` Draft's live OM must be `queued` and is cancelled through `sending.api.cancel_for_cascade` (else `blocked` "Already sending"), then `approved → ready` (`RETURN_EDITED`, cause `edited`, actor `operator`, `cause_ref` the current approval id) in `_return_for_edit`; then `drafting.revise`; a `ValueError` becomes `_RolledBack` with the message (first letter upper-cased), a `LookupError` "This Draft can't be approved yet"; pending fields = new rev, new `card_of(...).shown_hash`, `review_ms`; the session counts the decision via the shared `_count_decision`; result `ApproveOutcome.PENDING` "Recheck pending". `@use_case complete_pending(draft_id, rev)`: `None` with no pending approval for `rev`; else the unchanged `_check(draft_id, rev, pending_approval_hash)`; pass → `_record(checked, rev, now, pending_review_ms)` (no session change); `stale` → nothing written; `blocked` → `clear_pending` only, except a grounding result still `pending`, which writes nothing; `_RolledBack` (enqueue failure) → raises `PendingApprovalFailed(RuntimeError)` so the job retries. `_record` now takes `review_ms` and clears the pending fields; `_move_cancelled` (both cascades) clears them too.
- **Drafting** (`app/drafting/grounding.py`). `GroundingChecks(clock, llm, queue=None)`. `_record` takes the queue; after `record_ai_result` returns true (every final result: pass, fail, fail-closed) it reads `queries.approval.pending_approval(draft_id)` and, when its `rev` is the recorded one, enqueues `complete_pending_approval` (scope draft id, discriminator rev, payload `{v, draft_id, rev}`, interactive lane) in the same transaction; with no queue it raises `RuntimeError` (the result rolls back). `revise` is unchanged (origin `operator_edit`, interactive lane).
- **Queries** (`app/queries/approval.py`). `QueueCard` gains `status`, `edit_subject`, `edit_body`, `footer_text` (the footer snapshot text) and `pending_rev`; `pending_approval(draft_id)`; `approved_unsent() -> tuple[ApprovedUnsent(draft_id, name, step_no, om_status)]` (approved Drafts whose live OM isn't `sent`, by `status_at`).
- **Jobs.** `handlers.complete_pending_approval(approval)` (+ `CompletingPendingApproval` Protocol): ULID `draft_id`, positive int `rev` else `InvalidJob`. `main.build_job_types(..., approval=None)` registers it (interactive only, priority 60, 2 min, retry 3, `JobGroup.OTHER`); `None` → `no_approval`, which raises `RuntimeError` when a job runs (same pattern as `grounding`); `enqueue_only_job_types` passes `_unwired`. `build_app`: `GroundingChecks(clock, llm, queue)`; one `Approval(clock, sending=sending, drafting=Drafting(clock, queue))` serves both the dashboard and the runner's job.
- **Web.** `POST /queue/edit` (`queue_edit`, `@personal_data(True)`, form via `queue_page.submitted_edit`: exactly the five fields; subject ≤ 1,000 and body ≤ 40,000 at the form layer so drafting's own 200/10,000 limits answer with a message; body `\r\n`/`\r` → `\n`). Answers 200 with the page focused on that Draft (`show_draft`: shown even outside the due queue while `ready`/`approved`, position "Outside the due queue"); unknown → 404. `GET /queue` lists "Approved, not yet sent" (`approved_unsent`, links `?draft=<id>`), and `?draft=` naming one shows its card (approve disabled "Already approved", editor present). `queue_page.card_row`: `editable`, the editor text, "Recheck pending" badge/approve notice (pending rev = rev and grounding pending), `stale` badge + `pending_notice` "This Draft changed since you opened it" (pending rev < rev). `queue.html`: `<details class="queue-edit">` after the Approve form holding the edit form (`hx-post /queue/edit?card=k`, `csrf_field()`, hidden binding) with the subject input, body textarea and read-only footer `<pre>` inside an `hx-disable` div; card `article` gets `tabindex="-1"`. `queue-keys.js`: `KEYMAP` = `a` approve, `e` edit, `ctrl+enter` submitEdit, `escape` cancelEdit (the last two only inside the edit form).
- **Tests.** New `tests/app/test_edit_approve.py` (46 tests): the I/O matrix with row counts (approvals, sessions, events, OMs, tokens, revisions, jobs), the end-to-end flow (edit POST → `check_grounding` handler with `ScriptedLlm` → `complete_pending_approval` handler), stray URL and AI fail, newer revision (stale; no job for the newer rev), changed address, suppressed, in-flight OM (`sending`/`unknown`/`sent`), approved+queued, bad text, nothing changed, failed enqueue (raises, rolls back), approve/cascade clear the pending fields, no-queue refusal, the job type/handler/payload, page and keymap checks, `is_major_edit`, and the acceptance checks. AD-16 scenario `approval.complete_pending` (+ manifest `Caller`). Updated pins: HEAD ×3, chain probes (0051/0052), `test_source_tree` FILES, job-type lists (`test_job_contracts`, `test_runner_lifespan`, `test_health_page` ×2, `test_engine_health`), `test_places_storage_terms` `ALLOWED_COLUMNS` (`drafts.pending_review_ms`), `tests/dashboard.py` (`Approval(..., drafting=...)`), `test_card_details.hidden`/form lookups and `test_due_queue` (two forms now). `README.md` "Edit a card, then approve it once the recheck passes (Story 5.6)".

**Decisions made in implementation (pending review):**
1. `complete_pending` treats a `blocked` "Grounding check pending" as not final and writes nothing (the job only runs after a final result, so this is defensive).
2. Edit of an `approved` Draft whose live OM is `sent` is refused "Already sending" (nothing to pull back).
3. "Nothing changed" compares the stripped subject and the (line-break-normalised) body exactly with the current revision.
4. The edit POST's refusal for a Draft that isn't `ready`/`approved` uses the prefix "The Draft you edited: "; an approve refusal for an approved-list Draft keeps 5.4's prefixed notice (`show_listed=False`) instead of focusing its card.
5. The approved-list card is reached only via `?draft=` (no new route).

**Verification run (implementer, before review):** `tests/contracts` 1924 passed / 6 skipped, with one failure (`test_web_contracts` route list lacked `/queue/edit`), fixed and re-run green with `test_ad16_callers`, `test_job_contracts`, `test_approve`, `test_due_queue`, `test_card_details` and `test_edit_approve` (578 passed, 5 skipped); `test_migrations`, `test_runner_lifespan`, `test_health_page`, `test_engine_health`, `test_dashboard`, `test_ai_grounding`, `test_outbound_messages` and `test_draft_slots` (377 passed); `lint-imports` 29 kept, 0 broken. `tests/app/test_serve_process.py` (HEAD bump only) was not run; the full suite is still to run through `suite_lock.py`.

## Spec Change Log

## Review Triage Log

## Verification

**Commands:** run them from `nextron-engine/`, with `-n 2` at most, one pytest run at a time, and `--basetemp="e:\Nextron AI outreach\_handoff\scratch\track-E\pt"`:
- `uv run pytest -q tests/app/test_edit_approve.py tests/app/test_approve.py tests/app/test_ai_grounding.py tests/app/test_due_queue.py tests/app/test_card_details.py`: all pass.
- `uv run pytest -q tests/contracts`: all pass.
- `uv run lint-imports`: all contracts kept.
