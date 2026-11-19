# Epic 5 Context: Fast, keyboard-first Approval Queue

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the Operator (Mac) a localhost Approval Queue where he clears about 90 daily items (about 24 Step 1s plus 61–68 follow-ups) in 30 minutes or less. One focused card at a time shows the exact message the prospect will receive and the Evidence and Approved Company Facts behind every claim. Every decision has a single key. Each approval is bound to the exact rendered text and creates exactly one queued OutboundMessage (OM) that holds its own copy of that text. Edits, rejections, expiry and cascades cancel that OM, so an approved message waits safely in the send queue until delivery (Epic 6) picks it up. This epic is the human-approval guarantee: nothing reaches a prospect unless the Operator approved that exact message.

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

- **Queue contents:** `ready` Drafts that are responsive, or cold with `due_at ≤ now + 24 h` and an `active` Enrollment. Group order: Reply Drafts; Tier A Step 1s (newest live Event first, ties by Lead Score); other Step 1s and `recontact`; follow-ups (Steps 2–4). Every group except Tier A sorts by Lead Score descending. A follow-up never appears after the Contact replied, unsubscribed or bounced.
- **Live Eligibility:** Eligibility and contactability are evaluated live for every card. The scoring display copy is never used. Contacts that fail are listed under "Held back" with reasons.
- **Card contents:** Lead name, Segment, Market, Tier and Score. Contact name, role and Address Class. The exact composed message (subject, body, signature, footer, UK privacy line) and the Playbook version. Earlier sent Steps for follow-ups. Each Evidence row and each Approved Company Fact relied on, with source links and an "AI-extracted" label. The per-claim grounding result.
- **Decisions:** approve, edit then approve, regenerate (optional instruction of up to 200 chars), reject with a reason (`wrong_person`, `bad_fit`, `wrong_fact`, `not_eligible`, `other`; no free text stored), skip. Rejecting a Draft for any Step stops the Enrollment (`operator_rejected`). "Bad fit" can also disqualify the Lead. Skip changes nothing: the Draft returns next session with a "Skipped earlier" badge.
- **Grounding:** a `fail` blocks approval (red card, failing claims highlighted). A `pending` result also blocks it. Operator edits are rechecked at `interactive` urgency and never batched. A typed URL, email or phone that doesn't match a Settings value fails. The signature and footer can't be edited or removed.
- **Performance:** the next card renders in under 300 ms after a decision (p95, measured from keypress to paint). Server time is under 150 ms at p95 with 90 due Drafts.
- **Expiry and stalls:** an approval expires after 72 h unsent and returns as "Re-approval required". A Step 1 citing a closed Event returns as "Event expired". A cold Draft unapproved more than 7 days past `due_at` shows "Stalled", derived at read time and never stored. Away dates stop `selection` and `restart` enrollments only.
- **SM-7 instrumentation:** keep revision 1 unchanged. Record `review_ms` (server time from serving the card to the decision) and `edit_ratio` against revision 1 (above 0.20 is a major edit). Record `approval_sessions` (idle close after `approval_session_idle`, default 15 min).
- **Never:** call `MailPort.send` in this epic (OMs stay `queued`); create an OM without an `approvals` row; send any message without human approval.

## Technical Decisions

- **Approval binding:** `approval.approve(draft_id, rev, shown_hash)` succeeds only if the Draft is `ready`, `rev` is current, grounding for that rev is `pass`, `shown_hash` equals `domain.render.text_hash` of the current composed message, and `assert_contactable` allows. Otherwise the result is `stale` or `blocked`, nothing is written, and the card re-renders with the reason. On success, one unit of work inserts the `approvals` row, moves the Draft `ready → approved` with an event, and calls `sending.enqueue`.
- **`text_hash`** (defined only in `domain.render`) is SHA-256 over the recipient, the inbox and the canonicalised subject and body (NFC, LF line endings, trailing whitespace and trailing blank lines stripped). Only `domain.render.compose` renders the message.
- **OM:** `outbound_messages` is owned by `sending`. It holds its own copy of recipient, inbox, subject and body, plus `approved_hash`, `unsub_token` (via `compliance.issue_unsub_token`) and an AD-4 snapshot (`tier`, `lead_score`, `variant_id`, `segment_id`, `market_id`, `step_no`, `kind`). A partial unique index allows one non-cancelled OM per `draft_id`. Statuses `queued`/`sending`/`sent`/`unknown`/`cancelled` follow a closed transition table in `domain/outbound_message.py`. `enqueue` raises `UnsupportedKind` for Drafts without an Enrollment.
- **Un-approve:** edit, reject, expiry, Event expired and cascades call `sending.cancel(om)` first. From `queued` it cancels. On `sending` or `unknown` it raises `AlreadySending`, and the UI shows "Already sending". The `cancel_pending` cascade never raises: it leaves such Drafts `approved` and logs the OM id.
- **Ownership:** `approval` owns the `drafts` status group (`status`, `reapproval_required`, `event_expired`, `skipped_at`, `reject_reason`, `pending_approval_rev`), `approvals` and `approval_sessions`. `drafting` owns content, revisions, `due_at`, `generic`, `stale` and grounding. Edits go through `drafting.revise`, and regeneration through `drafting.regenerate`. Each migration maps every column in `owners.py` and classifies it in `pii_registry.py`.
- **Notifications (same transaction):** `DraftRejected` → `sequencing.stop(operator_rejected)`, plus `pipeline.close(disqualified)` when ticked. `EventClosed` → `approval.return_event_expired`. `SubjectSuppressed` → `approval.cancel_pending`. `ContactErased` → `sending.erase_pii` tombstones OM text. Subscribers are registered only in `main.py`. A failing subscriber rolls back the whole transaction.
- **Edit then approve:** this sets `pending_approval_rev`. When that revision's grounding passes, drafting enqueues `complete_pending_approval:<draft_id>:<rev>` in the same transaction, and the handler reruns every approval check. A newer revision makes it a no-op and the card shows `stale`.
- **Triggers:** `expire_approvals` and `refresh_due_queue` run every 15 min (closed AD-10 list). Regeneration idempotency key: `regenerate_draft:<draft_id>:<rev>`, urgency `soon` within 24 h, otherwise `bulk`. Expiry is defined only by `domain.sending_policy.approval_expired` (`now ≥ approved_at + 72 h`). Away dates use `sending_policy.is_away`, stored under a `settings_schema` key in the Operator's timezone.
- **Contactability purposes:** `cold` (Step 1), `follow_up` (Steps 2–4), `recontact`, `reply`, `rebook`. Cover each in the AD-16 contract test.
- **Batch:** applies `approve` per item in one unit of work. Stale or blocked items stay `ready`. An invariant violation rolls back the whole batch ("Nothing approved: <error id>"). Each row gets a shared `batch_id`, and `review_ms` is split evenly across the batch.

## UX & Interaction Patterns

- Server-rendered Jinja2 with HTMX partials and sync `def` handlers. No inline scripts or styles (CSP `'self'`). The keymap lives in the self-hosted `nextron/web/static/queue-keys.js`, built from one table. HTMX config stays at `allowEval=false`, `allowScriptTags=false`, `selfRequestsOnly=true`, `historyCacheSize=0`, and the CSRF token goes in `hx-headers`.
- **Keys:** `A` approve, `E` edit, `R` reject (`1`–`5` pick the reason, `D` toggles disqualify for bad fit, `Enter` confirms), `G` regenerate (`Ctrl+Enter` submits), `S` skip, `B` batch list (`X`/`Space` select), `J`/`K` next and previous, `V` Evidence panel, `?` overlay, `Esc` cancel. Shortcuts are off inside inputs. Focus moves to the new card after each swap.
- A decision's POST returns the next card plus out-of-band updates for the group counts and "n of N", with no second request.
- **Layout:** group headers with counts, "n of N" and session elapsed time. Badges: grounding `fail`, `generic`, `stale` (also "Regenerating" or "Regenerate failed"), "Event expired", "Re-approval required", "Stalled", "Skipped earlier". The empty state reads "Queue clear" with the next due time, or "Nothing scheduled". An away banner reads "Away until <date>: no new Sequences are selected". The notification area shows the stalled count, linked to a filtered queue.
- **Security:** all state changes are POSTs with CSRF and Origin checks, and a GET never changes state. Untrusted text is autoescaped inside `hx-disable` containers, with no `|safe` or `Markup`. Links render only for http and https, with `rel="noopener noreferrer"`. Queue pages send `Cache-Control: no-store`.

## Cross-Story Dependencies

- **Builds on Epic 4:** drafts, revisions, `domain.render.compose` and `text_hash`, grounding results, `due_at`, `flag_stale`, `revise`, `regenerate`, `recompose_ready`. Also on Epic 3 (`assert_contactable`, `issue_unsub_token`, the suppression cascade), Epic 2 (Evidence, Events, `EventClosed`) and Story 4.10 (scoring and selection, which 5.11 gates).
- **Order within the epic:** 5.2 (OM) is a prerequisite of 5.4. 5.1 and 5.3 form the card that 5.4–5.10 extend. 5.4 is the core that 5.5, 5.6, 5.8 and 5.9 reuse. 5.7 consolidates the keymap, and 5.8 adds `B`.
- **Feeds Epic 6:** sending picks up `queued` OMs, re-checks `approval_live` and `hash_matches` at the gate, and publishes `MessageSent` (`approval.on_sent`).
