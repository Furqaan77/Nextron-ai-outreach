# Epic 6 Context: Safe sending from warmed inboxes

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Approved OutboundMessages (OMs), queued by Epic 5, go out only from Furqaan's authenticated, warmed Sending Inboxes on lookalike Sending Domains, never from `nextronai.com`. Every send passes one closed gate just before it leaves. Cold sends respect the recipient's Send Window, the ramped Daily Cap and the total ceiling, with human-like gaps. Each OM goes out exactly once, even across crashes, sleep, network drops or a restore. Follow-ups stay in one plain-text Thread, and one mailbox sync per inbox confirms what really went out. Deliverability guardrails pause an inbox before it burns its domain. This is Slice 1's path to the first real send.

## Stories

- Story 6.1: Spike: Gmail send path, header reconciliation and Internal OAuth in the real Workspace
- Story 6.2: Connect a Sending Inbox through Gmail OAuth with an identity check
- Story 6.3: Pause, resume and remove Sending Inboxes
- Story 6.4: Record warm-up and ramp each inbox's Daily Cap under a total ceiling
- Story 6.5: Check Sending Domain authentication on connect and daily
- Story 6.6: Pause inboxes on blocklistings and Postmaster spam rates
- Story 6.7: Assemble plain-text messages in one Thread per Contact
- Story 6.8: Sync each Sending Inbox every 2 minutes and confirm engine sends
- Story 6.9: Evaluate the closed send gate, including stop rules, before every send
- Story 6.10: Send each approved message exactly once
- Story 6.11: Pace cold sends inside Send Windows and catch up without bursts
- Story 6.12: Reconcile uncertain sends and let the Operator resolve `unknown` messages
- Story 6.13: Pause inboxes on high bounce rates and policy Bounces

## Requirements & Constraints

- **Inboxes:**
  - The address is a personal one in the Sender's name, and the From name is the Sender's name setting.
  - Refuse an address on `nextronai.com` or a subdomain, a role address (`team`, `info`, `sales`), or a profile that doesn't match the requested address. Outside `prod`, also refuse any address not in `sink_allowlist`.
  - More than 3 inboxes on one domain shows a warning but doesn't block.
- **Domain authentication** (on connect and daily):
  - SPF includes `_spf.google.com`.
  - DKIM: `google._domainkey` has a non-empty `p=`.
  - DMARC is `v=DMARC1`.
  - SPF or DKIM aligns under relaxed alignment.

  A failure, or no pass within 26 h, blocks cold sends. A check that couldn't run never counts as a pass.
- **Volume:**
  - No cold send before 14 days of recorded warm-up.
  - The cap starts at the warm level (or the target, if lower) and rises 5 a week to 30.
  - The ceiling (default 50) includes warm-up tool sends: the larger of `warmup_tool_daily` and the day's `other_sent` count.
  - Days are calendar days in `sender_timezone`.
  - The engine never runs warm-up exchanges.
- **Send Window and gaps:** weekdays 08:00–17:00 in the recipient's local time. A US recipient with an unknown timezone gets 11:00–17:00 `America/New_York` only. Cold gaps are drawn uniformly from 4–12 minutes per inbox.
- **Responsive kinds** (`reply`, `rebook`) skip the cold predicates, the gap, the Window and the Cap. They still wait for a mailbox sync that completed after `approved_at`.
- **Exactly once:**
  - `send` is never retried.
  - An uncertain outcome is reconciled against SENT, then becomes `unknown`.
  - Only a later sync or the Operator resolves an `unknown` OM; nothing re-queues automatically.
  - After downtime there's no burst, and missed sends roll to the next valid window.
- **Messages** are plain text only, with no tracking and no setting that enables it. Every message carries RFC 8058 one-click unsubscribe.
- **Guardrails** (alerts deduplicated through `notify`; an inbox resumed while its condition persists is re-paused by the next check):

  | Condition | Action |
  |---|---|
  | 7-day hard-bounce rate above 2% (50+ sends) | alert |
  | 7-day hard-bounce rate above 3% (50+ sends) | pause, cause `bounce_rate` |
  | policy Bounce | pause, cause `policy_bounce`, alert at once |
  | temporary Bounce | no counter change |
  | DNSBL listing code | pause the domain's inboxes, cause `blocklist` |
  | DNSBL answer in `127.255.255.0/24` | `query_refused`, warning only |
  | Postmaster spam rate ≥ 0.1% | alert |
  | Postmaster spam rate ≥ 0.3% | pause the domain's inboxes, cause `spam_rate` |
  | no Postmaster data or grant | `no_data`, never 0% |

- No setting or control disables a gate predicate.

## Technical Decisions

- **Ownership:**
  - `sending` owns `outbound_messages`, `threads`, `sending_inboxes`, `sending_domains` and `domain_checks`. Its commands are `enqueue`, `cancel`, `confirm_sent`, `record_bounce`, `resolve_unknown` and `mark_nonterminal_unknown`.
  - `replies` owns `mail_messages` and `inbox_sync_state`, with `resync_since`.
- **SendingInbox machine:**

  | | |
  |---|---|
  | Statuses | `connecting`, `active`, `paused`, `auth_failed`, `removed` (terminal) |
  | Pause causes | `operator`, `bounce_rate`, `policy_bounce`, `blocklist`, `spam_rate` |

  - `invalid_grant` moves an inbox to `auth_failed`, and reconnecting moves it back.
  - Remove publishes `InboxRemoved`, which runs `sequencing.stop(inbox_removed)`. Removal is refused while an OM is `sending` or `unknown`.
  - Authentication and warm-up are gate predicates, not statuses.
- **OM machine:**
  - `queued → sending → sent`.
  - `sending → unknown` on a reconciliation miss or a restore.
  - `unknown → sent` through sync or the Operator.
  - `unknown → queued` or `cancelled` by the Operator only.
- **Gate:** `compliance.assert_sendable(om, now)` evaluates `domain.send_gate.PREDICATES`.
  - All kinds: `contactable`, `approval_live` (72 h), `hash_matches`, `inbox_ready`, `footer_complete`, `market_eligible` (live Eligibility).
  - Cold only: `enrollment_active`, `thread_quiet`, `campaign_active`, `in_send_window`, `under_daily_cap`, `warmup_ok`, `under_total_ceiling`.
  - It reads only through `app.queries` and returns every failure.
  - A failure leaves the OM `queued` with `blocked_by`. The gate never cancels an OM or writes another owner's rows. A `hash_matches` failure also raises an ops alert.
  - It calls `assert_contactable` (AD-16 contract test).
- **Send flow:** per inbox, on the `realtime` lane with key `inbox:<id>`, one OM at a time.
  1. Prepare outside any transaction: pick the OM, refresh the token, build the MIME.
  2. Claim: the gate, then `UPDATE … SET status='sending' … WHERE id=? AND status='queued'` must change exactly one row.
  3. `MailPort.send` outside any transaction.
  4. Apply: `sent` with the Gmail ids, read back `Message-ID` with a metadata `messages.get`, publish `MessageSent`.

  Only `app.sending` calls `MailPort.send`.
- **Reconciliation** handles OMs `sending` for over 2 minutes. It lists SENT to the recipient after `send_started_at − 120 s` and matches `X-Nextron-Message-Id` exactly. A listing error keeps the OM `sending`. Search operators are used only if 6.1 adopts them.
- **`domain.sending_policy`** is the single home of the window, `daily_cap`, the ceiling, the gap draw (injectable random source), `capacity_forecast` and `pick_inbox`.
- **Dispatch:**
  - No inbox sends in recovery mode or before its first sync since start.
  - Responsive OMs go first.
  - A cold OM goes only when `now ≥ next_send_at` and `due_at ≤ now`, follow-ups first.
  - After a restart, the gap counts from the later of `last_sent_at` and process start.
- **Threads and MIME:**
  - Step 1, recontact and referral Step 1 each start a Thread. Follow-ups reuse it.
  - `compose` renders `Re: <subject>`, so the approval hash covers the sent subject.
  - `domain.message_builder` adds the transport headers (outside the hash): `Message-ID` (proposed; only the read-back value is trusted), `X-Nextron-Message-Id`, `In-Reply-To`, `References`, and both List-Unsubscribe headers on `u.<Sending Domain>`.
- **Sync:** `sync_mailbox` runs every 2 minutes through `history.list`. A 404 or a new inbox triggers a 30-day resync. It classifies SENT mail as `engine_send` or `other_sent`, and everything else as `unrouted`. Only reconciliation and erasure call Gmail directly.
- **OAuth:**
  - Reuse Epic 1's loopback flow, started only by a POST.
  - The scope is `gmail.modify` only.
  - The refresh token goes in keyring as `gmail/<address>`; access tokens stay in memory.
  - Revoke the grant on any identity failure.
  - Postmaster uses its own grant.
- **Ports:** `MailPort`, `MailboxPort`, `DnsPort` and `PostmasterPort`, each with a fake.
  - Google libraries are imported only in adapters.
  - A DNS timeout is `AdapterError(retryable=True)`.
  - Outside `prod`, sends go to the sink adapter.
- **Triggers:**
  - `sync_mailbox`: every 2 minutes
  - `send_due_messages`: every minute
  - `reconcile_sends`: at startup, then every 2 minutes
  - `check_domains`: daily, and on connect
- **Restore:** the recovery handlers are `mark_oms_unknown` (a startup step) and `reingest_mailboxes`, which runs `resync_since(backup_at − 1 day)`.
- **Erasure:**
  - Tombstone `threads.subject` and `mail_messages.body_text`.
  - Move the Contact's Threads to Gmail Trash.
- **Health:** `queries.sending.health_issues()` reports paused and `auth_failed` inboxes, failing domains and `unknown` OMs. Ops alerts carry IDs only.

## UX & Interaction Patterns

- **Sending Inboxes panel:**
  - Connect inbox
  - status and pause cause
  - the warm-up form and the ramp
  - today's cap and the cap used
  - authentication, DNSBL and Postmaster results ("No data")
  - "Cold sends blocked"
  - Recheck, Pause, Resume and Remove (with confirmation)
- **Health panel:** inbox status and last sync. Resolve `unknown` OMs with Mark sent, Re-queue or Cancel; Cancel returns the Draft to `ready` and sets `reapproval_required`.
- **"Approved, not yet sent" list:** shows the predicates each OM currently fails.
- **Lead page:** Stop Sequence.
- Every control is a POST with CSRF.

## Cross-Story Dependencies

- **Upstream:**
  - Epic 1: OAuth, keyring, triggers, the registries, `notify`.
  - Epic 3: `assert_contactable`, unsubscribe tokens, the erasure contract.
  - Epic 4: the minimal inbox tables, the placeholder `cold_daily_caps` (6.4 replaces it, keeping its signature), `capacity_forecast`, `pick_inbox`, `step_due_at`, `text_hash`.
  - Epic 5: `sending.enqueue` and the "Approved, not yet sent" list.
- **Within the epic:**
  - 6.1 must settle header survival and Internal OAuth before any real send or inbox connection.
  - 6.3's `pause_cause` serves 6.6 and 6.13.
  - 6.4 and 6.5 feed the gate.
  - 6.8 feeds `thread_quiet`, `other_sent` and `confirm_sent`.
  - The send chain runs 6.9 → 6.10 → 6.11 and 6.12.
  - 6.10 registers the `MessageSent` subscribers.
- **Downstream:** Epic 7 builds on 6.8's sync, calls `record_bounce` (6.13) and sends Reply Drafts as responsive OMs.
