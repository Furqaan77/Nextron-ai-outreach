---
title: 'Story 6.11: Pace cold sends inside Send Windows and catch up without bursts'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '28be53cd5c9977b0d40d67d3a824db630a776442'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing decides which approved OutboundMessage (OM) an inbox sends next or when, so once Story 6.10 sends, cold mail could go out back to back, outside business hours, or in a burst after the PC was off.

**Approach:** Put pacing and the dispatch choice in `domain.sending_policy` as pure functions (the gap draw with an injectable random source, the restart rule, and the per-tick candidate pick). Store `next_send_at` and `last_sent_at` on `sending_inboxes`, and give 6.10 the seams it will call: record a cold send, re-pace at process start, and read an inbox's dispatch state. OMs (5.2) and sending (6.10) aren't built, so a domain-level day simulation with the `MailPort` fake proves the no-burst behaviour.

## Boundaries & Constraints

**Always:**
- The gap is `sent_at` plus a uniform draw from 4–12 minutes. The draw is made only in `domain.sending_policy`, through an injectable source (anything with `uniform(a, b)`; production uses `random.SystemRandom()`). A draw outside 4–12 minutes raises `ValueError`.
- After a restart, `next_send_at` = the later of `last_sent_at` and process start, plus a fresh draw. Dispatch also refuses any `next_send_at` that is `None` or not later than process start, so a failed re-pace fails closed.
- An inbox sends nothing while recovery mode is on, or until a mailbox sync of that inbox has completed since process start.
- Responsive OMs (`reply`, `rebook`) go first, oldest `approved_at` first. They ignore the gap, the Send Window and the Daily Cap. A responsive OM goes only if a sync completed after its `approved_at`; otherwise the plan asks for `sync_mailbox` and skips it.
- A cold OM is picked only when `now ≥ next_send_at`, `due_at ≤ now`, it is inside its recipient's Send Window (`in_send_window`), and the inbox's known cold count today is under its cold cap. An unknown count means no cold send. Order: follow-ups (highest Step, then oldest `due_at`), then Step 1 and `recontact` (oldest `due_at`); ties break by OM id.
- A Step 1 citing a job posting whose last check is more than 24 h old, or unknown, is skipped, and the plan asks for `recheck_event(signal_id)`.
- At most one OM is picked per inbox per tick. The pick never replaces the send gate: 6.10 still runs `assert_sendable`.

**Never:**
- Send email, call `MailPort` from `nextron/`, or build OM tables, `send_due_messages` or `sync_mailbox` (5.2, 6.8, 6.10).
- Store a planned send slot (AD-11). Only `next_send_at` and `last_sent_at` are stored.
- Add a setting that changes the gap, the window or the caps.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| recovery | `recovery_mode` true | nothing picked, no actions |
| no sync since start | last sync `None` or before process start | nothing picked |
| responsive first | a `reply` and a due cold follow-up, both pickable | the `reply`, even when `now < next_send_at` |
| responsive needs fresh sync | sync completed ≤ `approved_at` | skipped; plan asks `sync_mailbox`; a cold OM may still be picked |
| gap | `now < next_send_at` | no cold OM |
| stale pace | `next_send_at` ≤ process start, or `None` | no cold OM |
| order | Step 3 due 10:00, Step 2 due 09:00, Step 1 due 08:00 | Step 3, then Step 2, then Step 1 on later ticks |
| not due | `due_at > now` | skipped |
| window | a US recipient at 10:59 New York | skipped; a later in-window candidate is picked |
| cap | cold today ≥ cap, or unknown | no cold OM; responsive still goes |
| stale posting | Step 1, posting checked 24 h 1 min ago or never | skipped; plan asks `recheck_event` for that Signal |
| draw | source returns 4.0 or 12.0 minutes | gap 4 or 12 minutes; 3.99 or 12.01 raises `ValueError` |
| restart | `last_sent_at` 12:58, start 13:00 | `next_send_at` = 13:00 + draw |

</frozen-after-approval>

## Code Map

- `nextron/domain/sending_policy.py` (stdlib only; extend the module docstring "Story 6.11"):
  - `GAP_MIN`/`GAP_MAX` (4 and 12 minutes), a `GapSource` Protocol, `draw_gap(source) -> timedelta`, `next_send_after(sent_at, source)`, `repaced_next_send_at(last_sent_at|None, started_at, source)` and `cold_paced(next_send_at, process_started_at, now) -> bool`.
  - `POSTING_RECHECK_AFTER = 24h`.
  - `DispatchCandidate(om_id, kind, step|None, approved_at, due_at|None, contact_tz|None, market: WindowMarket, signal_id|None, posting_checked_at|None)`. `cites_posting` is `signal_id is not None`. The kinds come from `domain.send_gate` (`COLD_KINDS`, `RESPONSIVE_KINDS`); check that this import doesn't cycle.
  - `InboxDispatchState(inbox_id, recovery_mode, process_started_at, last_sync_completed_at|None, next_send_at|None, cold_sent_today|None, cold_cap)`.
  - `pick_dispatch(state, candidates, now) -> DispatchPlan(om_id|None, sync_mailbox: bool, recheck_signal_ids: tuple[str, ...])`. Reuse `in_send_window` and `_aware`.
- Migration `nextron/db/alembic/versions/0046_6_11_inbox_pacing.py`: two nullable `op.add_column` calls, no rebuild. Update `models/sending.py` (`UtcDateTime`, PII none), `owners.py`, `pii_registry.py`, and the HEAD constants, chain probes and `test_source_tree` FILES (track rules §3).
- `nextron/db/repos/sending/inboxes.py`: add the two fields to `StoredInbox` and `update_pacing(inbox_id, next_send_at, last_sent_at=<unchanged>)`.
- `nextron/app/queries/sending.py`: add `next_send_at` and `last_sent_at` to `SendingInboxView`.
- `nextron/app/queries/replies.py`: add the seam `last_sync_completed(inbox_id) -> datetime | None`. It returns `None` until 6.8, so nothing dispatches (fail closed).
- `nextron/app/sending/api.py`, where `Sending(clock, *, env, gap_source=None)` records `process_started_at = clock.now()` at construction:
  - `@command record_cold_send(inbox_id, sent_at)`: sets `last_sent_at` and `next_send_at`. 6.10 calls it in its apply unit.
  - `@use_case repace_after_start()`: re-paces every listed inbox from `process_started_at`.
  - `dispatch_state(inbox_id, now) -> InboxDispatchState`: reads `queries.ops.recovery_mode`, the inbox view, `last_sync_completed`, `cold_sent_on` and `cold_daily_caps` for the `sender_timezone` local date. A missing inbox or an unset timezone gives a state that picks nothing.
- `nextron/main.py`: add a startup step `repace_cold_sends` after `queue_test_runs`. Use the `sync_erasure_ledger` pattern and update the startup docstring.
- `tests/fakes/mail.py` `InMemoryMail`: the simulation's "sender".

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/sending_policy.py`: add the gap, the restart rule and `pick_dispatch`.
- [x] Migration, model, owners, PII registry and the test constants.
- [x] Repo, queries (`sending`, `replies` seam) and the `Sending` methods; wire the startup step in `main.py`.
- [x] `tests/unit/test_sending_policy.py`: every matrix row, with a stub source.
- [x] `tests/app/test_inbox_pacing.py`: on a migrated temp DB with a `FrozenClock`, cover `record_cold_send`, `repace_after_start` (with and without `last_sent_at`), and `dispatch_state` failing closed on the seams' real `None`s.
- [x] `tests/unit/test_dispatch_simulation.py`: the UJ-5 day. Use a frozen clock ticking each minute, `random.Random(seed)`, `InMemoryMail`, 3 inboxes with cap 30, UK and US recipients, and the PC off 09:00–13:00 London (a restart at 13:00 that re-paces and needs a fresh sync). A test-local stand-in for 6.10 sends each picked OM through the fake, removes it, and applies `next_send_after`.
- [x] `nextron-engine/README.md`: add a "Cold-send pacing (Story 6.11)" section.

**Acceptance Criteria:**
- Given the database at the previous head, when `0046_6_11_inbox_pacing` runs, then `sending_inboxes` gains nullable `next_send_at` and `last_sent_at`, and every row is kept.
- Given the simulated day, then every cold send per inbox is at least 4 minutes after the one before (including the first after wake), no inbox exceeds 30, nothing goes outside a window or while the PC is off, no OM is sent twice, and the sends missed in the morning go out after 13:00 or on the next weekday's window.
- Given the full suite through the suite lock and `lint-imports`, then everything passes.

## Design Notes

`cold_paced` requires `next_send_at > process_started_at`. A stale value from before the restart (`last_sent_at` + gap, now in the past) can then never release a cold send at once, even if the startup re-pace failed.

Picking skips any candidate that can't go now (out of window, not due, stale posting), so one blocked OM never holds up the others.

## Verification

**Commands** (in `nextron-engine/`):
- `uv run pytest -q -n 2 tests/unit/test_sending_policy.py tests/unit/test_dispatch_simulation.py tests/app/test_inbox_pacing.py tests/unit/test_migrations.py tests/contracts`: expect them to pass.
- `uv run lint-imports`: expect every contract kept.

## Implementation Notes

### Decisions made overnight (pending user review)
1. **The boundary with 5.2 and 6.10.** No OM rows or send job exist yet. 6.11 ships pure pacing, the pick, two stored columns and the seams (`record_cold_send`, `repace_after_start`, `dispatch_state`, `last_sync_completed`). 6.10 builds `DispatchCandidate`s from OM rows, enqueues `sync_mailbox`/`recheck_event` as the plan asks, and calls `record_cold_send` in its apply unit. The end-to-end AC runs as a domain simulation with the `MailPort` fake.
2. **The pick checks the window and the cap too.** The gate also checks them, but checking them in the pick avoids head-of-line blocking.
3. **An unknown posting check counts as stale,** which fails closed by forcing a recheck.
4. **Process start** is `Sending` construction time. The startup re-pace runs later, so its values are always later than it.
5. **The kinds can't be imported from `domain.send_gate`.** `send_gate` imports `sending_policy`, so importing back would cycle. `sending_policy` defines `COLD_KIND_NAMES` and `RESPONSIVE_KIND_NAMES` as plain strings, and a unit test pins them equal to `send_gate.COLD_KINDS` and `RESPONSIVE_KINDS`.
6. **The stale-posting rule covers `recontact` too.** A recontact is a first touch, like a Step 1, and the spec orders the two together. Follow-ups that cite a posting are not held. A posting check dated after `now` counts as stale (fail closed).
7. **A responsive OM with a `due_at` later than `now` waits.** The spec says responsive OMs skip the gap, the Window and the Cap. It says nothing about `due_at`, so the conservative reading is that a set `due_at` is still respected.
8. **`dispatch_state`'s fail-closed state** (a missing inbox, one that isn't `active` (`connecting`, `paused`, `auth_failed` or `removed`), or an unset or unreadable `sender_timezone`) also sets `recovery_mode=True`, as a belt-and-braces measure. `record_cold_send` for an unknown inbox writes nothing and returns `None` rather than raising, so 6.10's apply unit is never doomed after a real send.

### What was built
- `nextron/domain/sending_policy.py`: `GAP_MIN`/`GAP_MAX`, `GapSource`, `draw_gap` (asks `uniform(4.0, 12.0)` minutes; refuses non-finite values, non-numbers and anything outside 4–12), `next_send_after`, `repaced_next_send_at`, `cold_paced`, `POSTING_RECHECK_AFTER`, `DispatchCandidate`, `InboxDispatchState`, `DispatchPlan` and `pick_dispatch`.
- Migration `0046_6_11_inbox_pacing` (down revision `0045_4_12_draft_revisions`): two nullable `VARCHAR(27)` columns added in place. The model, `owners.py`, `pii_registry.py`, the HEAD constants (persistence contracts, migrations, serve process), the chain-check probes in `test_persistence_contracts.py` (moved to 0047/0048) and the `test_source_tree` FILES are updated, and there is a new probe, `test_0046_adds_the_nullable_pacing_columns_and_keeps_every_inbox`.
- `repos.sending.inboxes`: `StoredInbox` gains `next_send_at` and `last_sent_at`. `update_pacing(inbox_id, *, next_send_at, last_sent_at=UNCHANGED)`.
- `queries.sending.SendingInboxView` gains `next_send_at` and `last_sent_at` (both default `None`). `queries.replies.last_sync_completed` is a seam that always returns `None` until 6.8.
- `Sending(clock, *, env, gap_source=None)`: `process_started_at`, `@command record_cold_send`, `@use_case repace_after_start` (logs `cold_sends_repaced` with `count`) and `dispatch_state`.
- `main.py`: one `sending` instance is built before the startup steps and passed to `create_app`. The new startup step is `repace_cold_sends` (after `queue_test_runs`), and the `test_runner_lifespan` step-name assertion is updated.
- Tests: the 6.11 section of `tests/unit/test_sending_policy.py`, `tests/app/test_inbox_pacing.py` and `tests/unit/test_dispatch_simulation.py`.
- README: a "Cold-send pacing (Story 6.11)" section, before "Enrollments".
- Review patches (T1, T3, T5, T8, T9, T11, T14, T15, T17): `register_inbox` now paces a new inbox in its own transaction (`next_send_at = repaced_next_send_at(None, now, gap_source)`), so an inbox registered after start isn't refused by `cold_paced` until the next restart (T1). `DispatchCandidate` raises `ValueError` unless `step` is set (at least 1) exactly when `kind == "step"` (T3). `dispatch_state` returns the closed state for any inbox that isn't `active`, so a paused, connecting or `auth_failed` inbox gets no reply picked (T5). The rest are test and tidy-ups: the simulation runs in a module-scoped fixture, not at import (T8); the removed-inbox closed state, the migration probe's read-back and a New York date and inbox-id case are asserted (T9, T11, T17); the unreachable `datetime.min` fallback in `_cold_order` is gone (T14); two long docstring lines are wrapped (T15).

## Spec Change Log

## Review Triage Log

### Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 26 raw, 18 after dedup; 9 patch, 2 defer, 7 reject

| # | Source | Finding | Verdict | Route |
|---|---|---|---|---|
| T1 | BH1, EC1 | An inbox registered after process start keeps `next_send_at` NULL, so `cold_paced` refuses it until the next restart. | medium: `register_inbox` never paces, and only the startup step writes `next_send_at`. | patch: pace a new inbox at registration (`repaced_next_send_at(None, clock.now(), gaps)`). |
| T2 | BH2, VG3 | A failed startup re-pace stops cold sending, and the only sign is a `startup_step_failed` log line; no built-startup test proves the step writes. | medium (fails closed: no burst, but an unnoticed outage). | defer to 6.10, the first story where it can matter: health issue or alert, plus a built-startup assertion. |
| T3 | BH3, EC7 | `DispatchCandidate` accepts `kind="step"` with `step=None` (treated as Step 1), and a step number on other kinds. | low: a 6.10 mapping bug would misorder the OM silently. | patch: require `step` exactly when `kind == "step"`. |
| T4 | BH4, EC8 | Rechecks are requested only when the cold path is open, in window and before the pick. | low: the AC says "when dispatch reaches it". A recheck just before sending is the fresher one, and the delay is one tick. | reject. |
| T5 | BH5, EC6 | `dispatch_state` ignores inbox status: a paused, connecting or `auth_failed` inbox still gets replies picked, so 6.10 would refresh its token and build MIME every tick until the gate blocks the send. | medium. | patch: a non-`active` inbox returns the closed state. |
| T6 | BH6 | The closed state reports `recovery_mode=True`, the plan gives no reason, and an unset timezone also blocks replies. | low: `sender_timezone` is required; a reason field adds surface. | reject. |
| T7 | BH7 | The day simulation re-implements the 6.10 stand-in and doesn't run through `Sending`; it has no responsive OMs. | low: the approved spec chose a domain simulation with a test-local stand-in. `test_inbox_pacing.py` covers the `Sending` methods, and the unit matrix covers responsive OMs. | reject (would change the approved spec's approach). |
| T8 | BH8 | The simulation runs at module import, during collection. | low: a bug there would surface as a collection error. | patch: module-scoped fixture. |
| T9 | BH9, VG2 | A removed inbox's closed `dispatch_state` is never asserted. | medium (verification gap). | patch: add the assertion. |
| T10 | BH9b, EC2, EC3, EC4 | `record_cold_send` with an old `sent_at` moves pacing backwards, or leaves `next_send_at` ≤ process start; it also writes for a removed inbox. | maybe-false: only 6.10's live send calls it today, with `sent_at` = now. It matters if 6.12 reconciliation records a send from before a restart. | defer to 6.12 (medium, unverified): reconciliation must not lower pacing. |
| T11 | BH10 | The 0046 migration test's last UPDATE is never read back. | low: the step checks nothing. | patch: read the values back. |
| T12 | BH11 | Spec and sprint statuses disagree, and the Verification command omits two changed test files. | false: `in-review` with sprint `in-progress` is the workflow's state until step 5. The rest is a spec edit. | reject. |
| T13 | BH12 | `dispatch_state` checks for a naive `now` by discarding a `local_date` call. | low (cosmetic; it works and has a comment). | reject. |
| T14 | BH13 | `_cold_order` has an unreachable `datetime.min` fallback. | low; the fix is a deletion. | patch. |
| T15 | BH14 | Two docstring lines in `app/sending/api.py` are longer than 100 characters. | low; the fix is direct. | patch: wrap them. |
| T16 | BH15 | The new imports in `test_sending_policy.py` are out of order. | low (cosmetic; ruff passes). | reject. |
| T17 | VG1 | The `dispatch_state` test can't tell the `sender_timezone` date from the UTC date, and never checks the inbox id passed to the seams. | medium (verification gap). | patch: a New York case at 03:00 UTC that asserts `(inbox, 18 Nov)`. |
| T18 | EC5 | With no sync since start, the plan doesn't ask for `sync_mailbox`. | low: the matrix says nothing is picked, and 6.8's 2-minute trigger syncs anyway. | reject. |
