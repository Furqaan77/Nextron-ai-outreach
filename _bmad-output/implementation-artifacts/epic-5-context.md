# Epic 5 Context: Fast, keyboard-first Approval Queue

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give Mac (the Operator) a localhost Approval Queue where he can clear about 90 due items a day in 30 minutes or less, deciding each with one key. Every outbound email needs explicit approval, follow-ups included. So each card must show the exact rendered message plus the Evidence, Approved Company Facts and grounding result behind it. Each approval is bound to the exact text shown and creates exactly one `queued` OutboundMessage (OM) holding its own copy of that text. Edits, rejections, expiry and cascades cancel that OM. Approved messages wait safely in the send queue until delivery (Epic 6) picks them up. This epic never sends anything.

## Stories

- Story 5.1: Show due Drafts in queue order, one focused card at a time
- Story 5.2: Hold each approved Draft as exactly one queued OutboundMessage
- Story 5.3: Show the Evidence, facts and grounding result behind every card
- Story 5.4: Approve a Draft bound to the exact text shown
- Story 5.5: Reject with a reason, skip, or regenerate a card
- Story 5.6: Edit a Draft, then approve it once the recheck passes
- Story 5.7: Decide every card from the keyboard with the next card in under 300 ms
- Story 5.8: Batch-approve grounding-passed follow-ups from a compact list
- Story 5.9: Expire stale approvals and pull back Step 1s whose Event closed
- Story 5.10: Regenerate stale Drafts before they are due and flag stalled follow-ups
- Story 5.11: Set away dates that pause new Sequence selection

## Requirements & Constraints

- **Queue contents.** The queue shows only `ready` Drafts that are responsive (reply or rebook), or cold with `due_at <= now + 24 h` and an `active` Enrollment. Groups come in this order:
  1. Reply Drafts, Hot first;
  2. Tier A Step 1s, newest Event first, ties broken by Lead Score;
  3. other Step 1s and recontacts, by Lead Score;
  4. follow-ups (Steps 2–4), by Lead Score.

  A follow-up never appears after the Contact replied, unsubscribed or bounced. Empty state: "Queue clear" plus the next due time.
- **Live eligibility.** Eligibility and contactability are evaluated live for every card. A Draft that fails goes under "Held back" with its reasons. Scoring's stored copy is for display only.
- **Card contents.** Each card shows:
  - the Lead name, Segment, Market, Tier and Score;
  - the Contact's name, role and Address Class;
  - the exact composed message (subject, body, signature, footer and the UK privacy line);
  - the Playbook version;
  - for follow-ups, the earlier Steps sent;
  - each Evidence item (Quote, Absence or Count, its source link, retrieval time and an "AI-extracted" label);
  - each fact version relied on;
  - the grounding result per claim.
- **Decisions.** The Operator can approve, edit then approve, regenerate (with an optional instruction of up to 200 characters), reject with a reason or skip. The reasons are `wrong_person`, `bad_fit`, `wrong_fact`, `not_eligible` and `other`, and no free text is stored.
  - Rejecting a Draft for any Step stops the Contact's Sequence.
  - Skipping doesn't stop it; the card returns in the next session with a "Skipped earlier" badge.
  - "Bad fit" can also disqualify the Lead.
- **Grounding.** A Draft whose grounding is `fail` or `pending` can never be approved. Operator edits are rechecked at `interactive` urgency and never batched. An edit that adds a URL, email address or phone number not equal to a Settings value fails the check. The footer and signature can't be edited.
- **Speed.** Every decision has a single key. The next card renders under 300 ms (p95, Playwright, keys only). Server time per decision is under 150 ms at p95 with 90 due Drafts.
- **Freshness.** An approval expires at exactly `approved_at + 72 h`. "Stalled" means a cold Draft that is unapproved with `due_at` more than 7 days past. It is derived at read time and never stored. Stale Drafts are regenerated before they are due.
- **Metrics.** Record these analytics facts:
  - `approvals.review_ms` (server time from serving the card to the decision; for a batch, split evenly);
  - `approvals.edit_ratio` against revision 1, where a value above 0.20 counts as a major edit;
  - `approval_sessions`, which close at "Queue clear" or after `approval_session_idle` (15 min) with no decision.

  Revision 1 stays unchanged.
- **Not autonomous.** No OM may exist without an `approvals` row. No module in this epic calls `MailPort.send`.
- **Away dates.** During an away period, enrolls with origin `selection` or `restart` are refused with reason `operator_away`. Origins `operator_pick`, `referral` and `recontact` still enroll.

## Technical Decisions

- **Text and hash binding.** Draft text lives in immutable `draft_revisions`, and only `drafting` writes it. Operator edits reach it through `approval` → `drafting.revise` with origin `operator_edit`.
  - `domain.render.compose` defines the message shown and sent.
  - `domain.render.text_hash` is the single hash. It covers the address, inbox, subject and body after canonicalisation.
  - Every card carries hidden `rev` and `shown_hash` fields.
- **Approve.** `approval.approve(draft_id, rev, shown_hash)` checks that:
  - the Draft is `ready`;
  - `rev` is current;
  - grounding for that revision is `pass`;
  - the hash matches;
  - `assert_contactable` allows the Contact.

  If any check fails, the result is `stale` or `blocked` and nothing is written. On success, one unit of work inserts the `approvals` row, moves the Draft `ready → approved` and calls `sending.enqueue`. Batch approval runs this per item in one unit of work, and any invariant violation rolls back the whole batch.
- **AD-16 purposes.** Pass `cold` for Step 1, `follow_up` for Steps 2–4, and `recontact`, `reply` or `rebook` for those kinds.
- **OutboundMessage.** `outbound_messages` is owned by `sending`, with a ULID id. It holds its own copy of the recipient, the Enrollment's bound inbox, subject, body, `approved_hash`, `unsub_token` and the AD-4 snapshot (`tier`, `lead_score`, `variant_id`, `segment_id`, `market_id`, `step_no`, `kind`).
  - A partial unique index allows one non-cancelled OM per `draft_id`.
  - `enqueue` raises if the copy's hash differs from the approval's hash. It raises `UnsupportedKind` when the Draft has no Enrollment, because Reply inboxes come from Threads.
  - It issues the unsubscribe token through `compliance.issue_unsub_token`.
  - Sending reads only the OM copy, never the Draft.
- **Un-approve.** Edit, reject, expiry, Event-expired and cascades on an `approved` Draft first call `sending.cancel(om)`.
  - A `queued` OM moves to `cancelled`.
  - An OM in `sending` or `unknown` raises `AlreadySending`, and the action fails with "Already sending".
  - In cascades (`approval.cancel_pending`), the Draft stays `approved` and the OM id is logged. The cascade never raises.
- **State machines.**
  - Draft (owner `approval`; `drafting` inserts rows at `ready`): `ready → approved`; `approved → ready` (edit, expiry, Event expired); `ready` or `approved` → `rejected` or `cancelled`.
  - OM (owner `sending`): `queued`, `sending`, `sent`, `unknown`, `cancelled`, with the transition table in `domain/outbound_message.py`.
  - Every transition appends an `events` row with an actor of `operator`, `system:approval` or `system:sending`.
- **Draft flags are columns, not statuses.** They are `grounding` per revision, `generic`, `stale`, `event_expired`, `reapproval_required` and `skipped_at`, plus `reject_reason` and `pending_approval_rev`.
  - All of them are in the `drafts` approval status group and owned by `approval`, except `generic`, `stale` and `due_at`, which `drafting` owns.
  - The queue shows every due `ready` Draft whatever its flags. Flags render as badges, and `fail` blocks approval.
- **Notifications** run in the same transaction:
  - `DraftRejected` → `sequencing.stop(operator_rejected)`, which cancels the Contact's other pending Step Drafts, plus `pipeline.close(disqualified)` when "also disqualify Lead" is ticked;
  - `EventClosed` → `approval.return_event_expired`;
  - `SubjectSuppressed` → `approval.cancel_pending`;
  - `ContactErased` → `sending.erase_pii`, which tombstones `to_address`, `subject` and `body` and keeps ids, statuses and hashes.

  Subscribers are registered only in `main.py`.
- **Edit then approve.** The edit sets `pending_approval_rev`. When the recheck passes, `drafting` enqueues the job `complete_pending_approval:<draft_id>:<rev>`, and its handler calls `approval.complete_pending`, which reruns every check. If a newer revision has appeared, it does nothing and the card shows `stale`.
- **Expiry and policy.** `domain.sending_policy.approval_expired` and `is_away` are the only definitions of approval expiry and away dates. The `expire_approvals` and `refresh_due_queue` triggers each run every 15 minutes and belong to the closed AD-10 trigger list.
  - `domain.grounding.is_stale` drives `drafting.flag_stale`.
  - Regeneration is requested with idempotency key `regenerate_draft:<draft_id>:<rev>`, at urgency `soon` when due within 24 h and `bulk` otherwise.
- **Queries.** The queue is read through `app.queries.approval.due_queue(now)`. GETs are read-only.
- **Dashboard hardening.**
  - Every state change is a POST with a CSRF token. A GET to an approval route changes nothing.
  - Untrusted and AI text is autoescaped inside `hx-disable` containers, with no `|safe` or `Markup`.
  - Links render only for `http` and `https`, with `rel="noopener noreferrer"`.
  - Responses carry `Cache-Control: no-store`, the strict CSP (`script-src 'self'`, no inline script) and the security headers.
  - HTMX keeps `allowEval=false`, `allowScriptTags=false`, `selfRequestsOnly=true` and `historyCacheSize=0`. The CSRF token travels in `hx-headers`.

## UX & Interaction Patterns

- **Layout.** One focused card at a time, group headers with counts, "n of N" and the session's elapsed time. The badges are grounding `fail` (red), `generic`, `stale`, "Event expired", "Re-approval required", "Stalled" and "Skipped earlier".
- **Keymap.** All keys come from one table in self-hosted `nextron/web/static/queue-keys.js`:
  - `A` approve, `E` edit, `R` reject, `G` regenerate, `S` skip;
  - `J` and `K` for next and previous;
  - `V` toggles the Evidence and facts panel;
  - `?` opens the shortcut overlay, which lists every binding;
  - `Esc` cancels;
  - `B` opens the batch list (added in 5.8).
- **Dialogs.**
  - Reject: `1`–`5` pick the reason, `D` toggles "also disqualify Lead" (bad fit only), `Enter` confirms and `Esc` cancels. The chooser states "Rejecting stops this Sequence. To keep it going, regenerate (`G`) or skip (`S`) instead".
  - Regenerate and edit submit with `Ctrl+Enter`.
  - Shortcuts are off while focus is in an input, a textarea or a contenteditable element. `Esc` returns focus to the card.
- **Responses.** Each decision's POST returns the next card plus out-of-band updates to the group counts and "n of N" in one response, and focus moves to the new card.
- **Messages.** Conflicts re-render with an explanation and nothing is dropped silently. The messages are:
  - "This Draft changed since you opened it";
  - "Already approved" and "Contact is suppressed";
  - "Already sending";
  - "Recheck pending", "Grounding check pending" and "Regenerating…";
  - "Regenerate failed, try again".
- **Edit mode.** The inline editor covers the subject and body only. The signature, footer and UK privacy line show read-only.
- **Batch list.** It shows only due, `ready`, grounding-`pass` follow-ups with an `active` Enrollment. Each row shows the full body, untruncated. Select with `X` or `Space`, or "Select all shown", then press `A` and confirm the count with `Enter`. The list then reports each item as approved, `stale` or `blocked`.
- **Notifications and banners.**
  - The global notification area shows the count of stalled follow-ups, linked to a filtered queue.
  - During an away period, the queue shows a banner: "Away until <end date>: no new Sequences are selected".
  - The health panel shows the count of `queued` OMs and the age of the oldest.

## Cross-Story Dependencies

- **Builds on earlier epics.**
  - Epic 4 provides `draft_revisions`, grounding results, `domain.render`, `drafting.regenerate`, `drafting.revise`, `drafting.flag_stale` and `due_at`.
  - Epic 3 provides `assert_contactable`, unsubscribe tokens, suppression cascades and erasure.
  - Epic 2 provides Evidence, Events and `EventClosed`.
  - Sequencing provides Enrollments, `stop` and `enroll`.
- **Within this epic.**
  - 5.1 (queue) and 5.2 (OM and `sending.enqueue` and `cancel`) underpin everything.
  - 5.3 adds `shown_hash` to cards, which 5.4 (approve) requires.
  - 5.5, 5.6, 5.8 and 5.9 reuse approve and un-approve from 5.4.
  - 5.7 consolidates the keymap from 5.4–5.6.
  - 5.8 adds `B` to that keymap.
  - 5.10 builds on 5.9's badges and reapproval flow.
  - 5.11 (Slice 2) touches Settings and `sequencing.enroll`.
- **Each migration** adds its own `drafts` approval-group columns or tables.
- **Epic 6** (sending) will consume `queued` OMs through the AD-5 gate and send flow. This epic leaves OMs `queued`.
