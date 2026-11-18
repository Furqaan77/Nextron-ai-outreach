---
title: "Story 4.9: Create a Sequence's Draft slots, due times and lifecycle commands"
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '2c10b7ab0a2fb7612b137640373f349b05aa3671'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An Enrollment (4.8) has no Drafts and no due times, so nothing can be drafted (4.12+), approved (Epic 5) or sent (Epic 6); a stop leaves nothing to cancel, follow-ups are never spaced, and capacity can't reserve follow-ups (4.10).

**Approach:** Add `drafts` (content group owner `drafting`, status group owner `approval`) and the Draft machine; `drafting.request_steps` (an AD-16 caller) inserts one `ready` slot per Step from `enroll`, Step 1 due at once through the single `domain.sending_policy.step_due_at`; `sequencing.on_step_sent` (on `MessageSent`) sets the next Step's due through `drafting.set_due` and completes the Enrollment after the last; `pause`/`resume`/`delay_until`; every stop calls the new `approval.cancel_pending`.

## Boundaries & Constraints

**Always:**
- `step_due_at(prev_sent_at, delay_days, recipient_tz, not_before=None) -> datetime` (UTC): `prev_sent_at` aware; local wall time in `recipient_tz` (IANA, `zoneinfo`) kept, local date + `delay_days`; if `not_before` (a `date`) is later, that date at the same local time; then Saturday/Sunday → Monday, same local time; a nonexistent local time (spring-forward gap) resolves forward, an ambiguous one takes `fold=0`. Bad types raise `TypeError`, negative delay or unknown zone `ValueError`.
- A Step's delay is the gap between its Sequence day and the previous requested Step's day (default days 0, 3, 7, 14 → gaps 0, 3, 4, 7), so Steps keep the PRD's days when sends are on time. The day is snapshotted on the slot (`day_offset`) at request time: a later Sequence version never changes a running Enrollment (4.2 R9).
- `request_steps(enrollment_id, steps, urgency) -> tuple[str, ...] | Blocked`: validates (types, live Enrollment, Steps within its bound Sequence, `Urgency`) and raises before any write; then calls `compliance_api.assert_contactable(ContactSubject(contact), "email", "cold")` (global looked up at call time) before any write and returns `Blocked` writing nothing; else inserts, in **one** INSERT statement, a slot per Step not already present (kind `recontact` for origin `recontact`, else `step`), with one `events` row each (`None`→`ready`, cause `requested`, `system:drafting`). Only the Enrollment's first Step slot when it is Step 1 gets `due_at = step_due_at(now, 0, contact tz)`. Urgency is validated, not stored; no job (4.15/4.18). `enroll` calls it with `Urgency.SOON` after its writes and before `Enrolled`; a `Blocked` there raises `RuntimeError` (fail closed, whole enroll rolls back).
- `on_step_sent(n: MessageSent)` (registered for `MessageSent`) uses the Draft found by `n.draft_id` (kind `step`/`recontact`; anything else or unknown: logged no-op, never raises): send time = the clock's now; `last_step_sent_at` = max(existing, now) on every sent Step; the next requested Step gets `set_due(step_due_at(now, gap, contact tz))` only while its `due_at` is unset (idempotent); with no next Step a live Enrollment becomes `completed` (`ended_at` = now, one event). An ended Enrollment only records `last_step_sent_at` and raises `ended_at` to keep the CHECK. A Contact with no `tz` leaves the next Step without a due time (logged).
- `pause(enrollment_id, cause) -> bool` (`active` only; else no-op `False`); `resume(enrollment_id) -> bool` (`paused` → `active`, `pause_cause` cleared; else `False`, nothing changed); `delay_until(enrollment_id, date) -> datetime | None` (live only; the lowest-numbered `ready`/`approved` Step slot with a `due_at` is recomputed from the previous Step's send time — or its own creation time for the Enrollment's first Step — with `not_before` = the date). Each status change one `events` row; unknown Enrollment raises `LookupError`.
- `Sequencing.stop` (and the suppression sweep) calls `Approval(clock).cancel_pending(enrollment_ids)` for what it stopped: each `ready` Draft → `cancelled` with one `events` row (cause `sequence_stopped`, `cause_ref` the Enrollment). `approval.cancel_pending_suppressed(n: SubjectSuppressed)` is registered between `Sequencing.stop_suppressed` and `Pipeline.close_suppressed`, cancelling `ready` Drafts of ended Enrollments (idempotent backstop).
- `_follow_ups_due(local_date)`: per inbox, unsent (`ready`/`approved`) Step slots with `step_no > 1` of live Enrollments due before the end of that date in `sender_timezone` (overdue included), read through `queries.drafting`.

**Never:** draft text, revisions, AI calls, jobs, approval or send (4.12+, Epic 5/6); `approved → cancelled` (needs `sending.cancel`, Epic 5); a `DraftRejected`/`InboxRemoved` subscriber; a stored "stalled"; a Draft written by anyone but `drafting` (content) or `approval` (status); another def name containing "enroll" in `app/sequencing/` or "request_" in `app/drafting/`; an `ERASURE` entry for the all-`none` `drafts`; a new dependency.

### Decisions made overnight (pending user review)
1. **Delay = gap between Sequence days**, snapshotted per slot (`day_offset`), as above.
2. **Send time** is the clock's now in `on_step_sent` (`MessageSent` carries no time and only codes/ints/ids; Epic 6 publishes it in the transaction that records the send, or adds an int field).
3. **Weekend shift applies to Step 1 too** (a Saturday enroll is due Monday, same local time).
4. **Follow-ups due** counts overdue unsent follow-ups too (reserves more capacity).
5. **Extra columns:** `day_offset`, `created_at`, `updated_at` (drafting) and `status_at` (approval).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Enroll | US Contact, steps (1,2,3,4) | 4 `ready` slots, no rev; Step 1 due now (weekday), 2–4 no due; 4 Draft events | N/A |
| Fri send | NY, Fri 18:30 local + 3 | Mon 18:30 NY | N/A |
| Weekend | due lands Sat/Sun local | Monday same local time | N/A |
| DST | London/NY across a change | same local wall time | N/A |
| OOO | `not_before` after computed date | that date (weekday-shifted) | N/A |
| Step sent | `MessageSent` Step 1 at T | Step 2 due = step_due_at(T, 3, tz); `last_step_sent_at` = T; repeat no-op | unknown Draft: no-op |
| Last sent | Step 4 sent | Enrollment `completed`, `ended_at` = T | N/A |
| Pause/resume | active / paused / ended | paused / active / `False` no change | unknown: `LookupError` |
| Stop | any cause, live with ready Drafts | `stopped`; ready → `cancelled` + events, same transaction | repeat: no-op |
| Suppression | Contact suppressed | Enrollment `stopped`/`suppressed`, ready Drafts `cancelled` | N/A |
| Gate blocks | `request_steps`, `Blocked` | `Blocked`, nothing written | in `enroll`: `RuntimeError` |

</frozen-after-approval>

## Code Map

- `nextron/app/sequencing/api.py` -- `enroll` (call `request_steps` after `record_eligibility_basis`, before publish), `stop`/`_stop_row` (add cancel), `stop_suppressed`, `_follow_ups_due` (fill), `_local_date` (reuse for the day bounds). Add `pause`, `resume`, `delay_until`, `on_step_sent`. Update module docstring.
- `nextron/domain/enrollment.py` -- `PAUSE_EVENTS`, `RESUME`, `COMPLETE`, `transition`; `LIVE_STATUSES`.
- `nextron/domain/sending_policy.py` -- add `step_due_at`; docstring.
- `nextron/domain/draft.py` (new) -- copy `enrollment.py`'s machine shape: `DraftStatus`, `DraftKind`, `DraftEvent` (`approve`, `return_edited`, `return_expired`, `return_event_expired`, `reject`, `send`, `supersede`, `cancel`, `log_touch`), `DraftCause`, `CREATION` (`ready`, `requested`), `TRANSITIONS` per AD-4 (`log_touch` `ready→sent` only for kind `channel`: `transition(current, event, *, kind=None)` raises `IllegalTransition` otherwise), `TERMINAL`, `PENDING` (`ready`, `approved`).
- `nextron/db/repos/sequencing/enrollments.py` -- `set_status` exists; add `record_step_sent(id, sent_at, ended_at, at)`.
- `nextron/db/repos/discovery/leads.py:29,174` -- precedent: a repo reading another group's column read-only; `insert` leaves it to its server default.
- `nextron/app/drafting/api.py` -- add `Drafting(clock)` with `@command request_steps`, `@command set_due(draft_id, due_at)` (slot must be `ready`/`approved`, else no-op `False`). Keep `ERASURE`/`erase_pii`.
- `nextron/app/approval/api.py` -- `Approval(clock)`: `@command cancel_pending(enrollment_ids) -> tuple[str, ...]`, `@command cancel_pending_suppressed(n)`. No "approve" in any def name (AD-16 manifest).
- `nextron/db/repos/drafting/drafts.py`, `nextron/db/repos/approval/drafts.py` (new) -- drafting: insert (one statement), set_due, reads (status read-only); approval: cancel ready by Enrollment, ready Drafts of ended Enrollments.
- `nextron/app/queries/drafting.py` -- `DraftView` (with `reviewable` = `current_rev is not None`), `draft(id)`, `drafts_of(enrollment_id)` (by `step_no`), `follow_ups_due_before(end) -> Mapping[enrollment_id, int]`.
- `nextron/db/alembic/versions/0034_4_9_drafts.py` -- table template `0033_4_8_enrollments.py`. `drafts`: FK `enrollment_id`→enrollments (nullable; CHECK kind in (step, recontact) ⇒ enrollment_id, step_no, day_offset NOT NULL); CHECKs on kind, status, `step_no >= 1`, `day_offset >= 0`, `current_rev >= 1`; booleans server default 0; `status` server default `ready`; partial unique (enrollment_id, step_no) WHERE kind IN ('step','recontact'); index on `due_at`. `downgrade()` raises.
- `nextron/db/models/drafting.py` (new), `models/__init__.py`, `db/owners.py`, `db/pii_registry.py` (all `none`).
- `nextron/main.py:1613` -- `build_subscribers`: `Approval.cancel_pending_suppressed` after `Sequencing.stop_suppressed`; `MessageSent` → `Sequencing.on_step_sent`. Pinned in `tests/unit/test_serve.py` (~774, 805).
- `nextron/domain/ai.py:87` -- `Urgency`, `check_urgency`.
- Tests to update: `tests/contracts/test_ad16_callers.py` (enroll scenario now 3 gate calls with `gates_first=False`, add `drafting.request_*` scenario: prepare enrolls with `(1,)`, action requests `(2,)`; built-callers set; insert-scan models), `test_persistence_contracts.py` (HEAD, chain probes), `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`, `tests/contracts/test_source_tree.py` `FILES`. Fixtures: `tests/app/test_enroll.py`, `tests/app/test_enrollment_stops.py`.

## Tasks & Acceptance

**Execution:**
- [x] migration + model + owners + PII -- as above.
- [x] `domain/draft.py`, `domain/sending_policy.step_due_at` -- machine and due rule.
- [x] repos + `queries.drafting` + `app/drafting` + `app/approval` -- slots, due, cancel.
- [x] `app/sequencing/api.py` + `main.py` -- wiring, lifecycle commands.
- [x] Tests -- `tests/unit/test_draft.py` (all pairs), `tests/unit/test_sending_policy.py` (`step_due_at` matrix incl. Fri 18:30 NY, UK and US DST weekends, `not_before`), `tests/app/test_draft_slots.py` (enroll slots, `on_step_sent`, completion, pause/resume/delay_until, cancel on stop/suppression/close/erasure, `follow_ups_due`, gate-blocked `request_steps`, no "stalled" column), AD-16 and HEAD/contract updates.
- [x] `nextron-engine/README.md` -- Story 4.9 section.

**Acceptance Criteria:**
- Given the migration at head, then `drafts` exists with every listed column mapped (content `drafting`, status `approval`) and classified.
- Given the AD-16 contract, then `drafting.request_*` has a scenario: `Allowed` gates before its first write; `Blocked` adds no row.
- Given any stop, then the stopped Enrollments' `ready` Drafts are `cancelled` in the same transaction with one event each.

## Implementation Notes

- **Migration** `0034_4_9_drafts.py` (revision `0034_4_9_drafts`, down `0033_4_8_enrollments`): `drafts` with content group (`id`, `kind`, `enrollment_id`, `step_no`, `day_offset`, `current_rev`, `due_at`, `generic`, `stale`, `created_at`, `updated_at`) and status group (`status` default `ready`, `reapproval_required`, `event_expired`, `skipped_at`, `status_at`). CHECKs `ck_drafts_kind`/`status`/`step_slot`/`step_no`/`day_offset`/`current_rev`; partial unique `uq_drafts_enrollment_step`; `ix_drafts_due_at`. `parent` (AD-3's owner map) is not added: no reply/rebook/channel kind exists yet; their stories add it.
- **Domain.** `nextron/domain/draft.py` (`DraftKind` = step, recontact, reply, rebook, channel; `STEP_KINDS`; `cancel` cause `sequence_stopped`; `log_touch` only with `kind=channel`). `sending_policy.step_due_at` uses `fold=0` on the target wall time, which zoneinfo resolves forward in a gap and to the first occurrence in a fold.
- **Repos.** `db/repos/drafting/drafts.py` (`insert_slots` generates the ULIDs and inserts in one statement, never writes the status group; `set_due` is a conditional update on `ready`/`approved`; `follow_ups_due_before` returns counts per Enrollment). `db/repos/approval/drafts.py` (`ready_of`, `ready_of_ended_enrollments` reads `enrollments.status` read-only, `set_status` conditional). `enrollments.record_step_sent` added.
- **`request_steps`** raises `LookupError` when the Enrollment's bound `sequence_version` is no longer the stored one (only the current version is stored; fail closed rather than snapshot another version's days). A Step 1 slot for a Contact with no `tz` is left without a due time (logged), same as `on_step_sent`.
- **Sequencing.** `_follow_ups_due` maps the per-Enrollment counts onto the live Enrollments' inboxes (so the drafting repo needn't filter on live status). `resume`'s actor is `operator` when the pause cause was `operator`, else `system:sequencing` (the signature carries no actor). `delay_until` uses `last_step_sent_at` as the previous Step's send time (falls back to the slot's `created_at`); no event (no status change). `on_step_sent` ignores `n.kind`/`n.step_no` and trusts the stored Draft; completion's `cause_ref` is the sent Draft.
- **Wiring.** `main.build_subscribers`: `SubjectSuppressed` → `Sequencing.stop_suppressed`, `Approval.cancel_pending_suppressed`, `Pipeline.close_suppressed`; `MessageSent` → `Sequencing.on_step_sent`. Every stop path cancels through `Sequencing.stop`/`stop_suppressed`.
- **Tests.** New `tests/unit/test_draft.py`, `tests/app/test_draft_slots.py`; `step_due_at` matrix in `tests/unit/test_sending_policy.py` (incl. Israel's weekday spring-forward gap and Cairo's weekday fold); AD-16 `drafting.request_*` scenario and enroll's third gate call (`gates_first=False`); HEAD and chain probes bumped (0035/0036); `FILES`; `test_serve` subscriber pins; AD-16 insert-scan pins (`insert_slots` → `drafts`, `DraftRecord` guarded); `test_enroll` happy-path counts (+1 draft, +1 event) and the enrollments-repo function list (`record_step_sent`).
- Review patches (pass 1): `on_step_sent` writes nothing on a repeat (the next pending slot already has a `due_at`, or the Enrollment is `completed`; a `stopped` one still records the send) and only `ready`/`approved` slots count as following Steps (a rejected/superseded/cancelled one is skipped); plain index `ix_drafts_enrollment_id` (migration and model); `cancel_pending`'s `TypeError` says "a tuple or list"; the AD-16 enroll scenario checks `gates_before_first_write=2` (`leading_gates_problems`, with a probe); the follow-ups test keeps a stopped Enrollment's Step 2 `approved` so the live-Enrollment filter is exercised.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 25 raw, 19 after dedup; 6 patched, 1 deferred, 12 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1: a repeated `MessageSent` moves `last_step_sent_at` (and a completed Enrollment's `ended_at`) forward | medium | `on_step_sent` records `max(last, now)` before checking anything; the test even pins the drift; FR-63's 90 days count from it | patch: a repeat (next slot already due, or Enrollment `completed`) writes nothing |
| 2 | BH3, EC1, VG-other: a terminal next slot stalls the Enrollment and blocks completion | low | unreachable today (reject, supersede and cancel all stop the Enrollment) but Epic 5's skip may not; direct fix | patch: only `PENDING` slots count as following Steps |
| 3 | VG1: `gates_first=False` on the enroll scenario drops 4.8's "both gate calls before any write" | medium | `order_problems` checks only the first gate call; moving a write between the two would pass | patch: assert the first two gate calls precede the first write |
| 4 | VG2: `_follow_ups_due`'s live filter is untested | medium | the stopped Enrollment's Step 2 is cancelled by the stop, so the status filter alone passes the test | patch: an `approved` follow-up of a stopped Enrollment is not counted |
| 5 | BH8: no index usable for `drafts.enrollment_id` without a kind predicate | low | the only index is partial (`kind IN step, recontact`); `ready_of`/`of_enrollment` scan; one line in an unmerged migration | patch: `ix_drafts_enrollment_id` |
| 6 | BH12a: `cancel_pending`'s TypeError says "tuple" but takes a list | low | message only; direct correction | patch |
| 7 | BH5: `approved` Drafts of a stopped Enrollment stay `approved` | medium | spec Never excludes `approved → cancelled` (needs `sending.cancel`); nothing can be approved before Epic 5 | defer to Epic 5 |
| 8 | BH2: send time is the subscriber's clock | low | overnight decision 2 | reject (spec decision) |
| 9 | BH4: a Step 1 slot for a Contact with no `tz` is never due | false | `enroll` refuses `missing_tz_or_market` before `request_steps` in the same transaction | reject |
| 10 | BH6, EC5: cancelling has no kind filter | false | no reply/rebook/channel Draft carries an Enrollment yet; when they do, cancelling every pending Draft of a stopped (e.g. suppressed) Enrollment is the compliance-safe default; Epic 7 decides for its kinds | reject |
| 11 | BH7, EC7: pause leaves due times and still reserves capacity | low | reserving capacity for a paused Enrollment is the conservative choice; dispatch needs an `active` Enrollment; recompute on resume is Epic 7's `delay_until` | reject |
| 12 | BH9: `set_due` accepts any aware datetime | low | every caller passes `step_due_at`; a provenance check adds machinery | reject |
| 13 | BH10: `skipped_at` and the flags have no behaviour | false | the AC requires the columns; Epic 5 and 4.20 own them | reject |
| 14 | BH11: untested paths (delay_until without tz or paused, non-step kind) | low | small branches; adding tests is not a defect fix | reject |
| 15 | BH12b: `urgency` validated, not stored; c: third gate call | false | spec Always says so; the `RuntimeError` branch is the fail-closed backstop the spec asks for | reject |
| 16 | EC2: steps leaving out Step 1 never become due | low | every caller (4.10 selection, recontact `[1]`) starts at Step 1; a guard adds a branch | reject |
| 17 | EC3: an unreadable stored tz raises in the `MessageSent` subscriber | low | `tz` values come from 3.2's validated resolution and the vendored table; guard for an undemonstrated state | reject |
| 18 | EC4: operator pause over a system pause is dropped | false | spec: `pause` on any status but `active` is a no-op (AD-4 has one pause cause) | reject |
| 19 | EC6: a lower Step requested after a higher one is scheduled | low | no caller requests Steps out of order after sends; contrived | reject |

## Verification

**Commands:**
- `cd e:/Nextron-track3/nextron-engine && uv run pytest -q tests/unit/test_draft.py tests/unit/test_sending_policy.py tests/app/test_draft_slots.py tests/app/test_enroll.py tests/app/test_enrollment_stops.py tests/unit/test_serve.py tests/unit/test_migrations.py` -- all pass
- `uv run pytest -q tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept
