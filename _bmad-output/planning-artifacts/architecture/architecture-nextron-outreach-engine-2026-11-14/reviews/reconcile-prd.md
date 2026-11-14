---
review_of: ARCHITECTURE-SPINE.md (draft 2026-11-14) and its .memlog.md
against:
  - _bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/prd.md (status final)
  - _bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/addendum.md
lens: PRD reconciliation (requirements the ADs, conventions, ownership map or capability map fail to carry or contradict)
reviewed: 2026-11-14
---

# Review: PRD reconciliation

## Verdict

**Not ready for Slice 1 as written.** Every FR appears in some capability-map row, but the spine contradicts the PRD in six places and leaves several Slice 1 needs without an owner:

- the send gate (AD-5) and the Reply Draft exemption (FR-34);
- Telegram alert content (AD-15 and FR-42);
- the AI budget scope (AD-8 and FR-57);
- per-route batching and FR-8's 15-minute Signal Inbox path;
- Places storage (AD-9 and FR-5);
- the AD-1 layer rule and the ownership map's own writers.

The quiet gaps cluster in three places:

- **§5.3 backups:** the monthly restore test has no owner, the backup mechanism is out-of-process, and a restore can resurrect opt-outs.
- **Analytics facts** that §6.3 says every slice must record.
- **Glossary entities** with no owner or no single owner.

**46 findings: 12 High, 23 Medium, 11 Low.** Overlaps with `review-verified.md` are cross-referenced, not re-counted (see the last section).

**Severity scale**

- **High:** the spine contradicts a PRD requirement, or omits something Slice 1 needs. A builder who follows the spine would ship non-compliant, duplicate-sending or data-losing behaviour.
- **Medium:** the spine doesn't carry the requirement, so builders would fill it in inconsistently or late.
- **Low:** clarification, naming, or a gap with a small blast radius.

PRD line numbers refer to `prd.md` as of 2026-11-14 (`L###`). Addendum references use `addendum.md` section and line.

## Summary

| ID | Sev | Gap | PRD | Fix target |
|---|---|---|---|---|
| H1 | High | AD-15 strips Telegram alert content that FR-42 and FR-48 require | FR-42, FR-48 | AD-15 |
| H2 | High | AD-5 gate applies Window, Cap, warm-up and Eligibility to Reply Drafts, and misses FR-specific send checks | FR-34, FR-23, FR-3, FR-31, FR-33, FR-38, FR-65 | AD-5 |
| H3 | High | Budget and quota gates block grounding, reply and polling paths that FR-57 protects | FR-57 | AD-8, AD-9 |
| H4 | High | Per-route batching breaks FR-8's 15-minute path and interactive regenerate and recheck | FR-8, FR-25, FR-21 | AD-8 |
| H5 | High | AD-9 Places rule removes the website URL that FR-5 records, so Places Leads can't be crawled or deduplicated | FR-5, FR-9, FR-10, OQ-3 | AD-9 |
| H6 | High | Monthly test restore has no job, owner, record or pass criteria | §5.3, FR-58 | new AD-16, map |
| H7 | High | `backup.ps1` copies a live WAL DB out of process and can't report to FR-58; "nightly" doesn't fit PC uptime | §5.3, FR-58 | new AD-16, AD-2 |
| H8 | High | Restoring a backup resurrects opt-outs and erasures, and can resend messages | §5.3, FR-35, FR-41, FR-50 | AD-15, AD-5, AD-16 |
| H9 | High | FR-49 (no LinkedIn, Maps or job-site fetching) has no crawler guard | FR-49, FR-8 | AD-9, capability map |
| H10 | High | Real Replies flow into version-controlled AI test fixtures, outside erasure and retention | FR-40, FR-52, FR-53, §5.4 | map, source tree |
| H11 | High | FR-73 gate: nothing writes `ai_test_runs`, and the mutable Playbook file defeats activation | FR-73, FR-71 | AD-8, AD-12 |
| H12 | High | Slice 1 doesn't record the facts FR-54 and FR-55 later report | §6.3, FR-54, FR-55 | AD-4, map |
| M1 | Med | Ownership map gives Lead, Draft, Enrollment and Contact several writers, against AD-3 | FR-28, FR-37, FR-45, FR-67 | AD-3, map |
| M2 | Med | AD-1 forbids the writers the map names for `jobs`, `api_usage` and `ai_usage` | FR-57, FR-58 | AD-1, map |
| M3 | Med | Off-PC backup key custody isn't specified | §5.3 | AD-15, AD-16 |
| M4 | Med | AD-5 reconciles `sending` rows only at startup, not after sleep or network loss | FR-35, UJ-5 | AD-5 |
| M5 | Med | The job runner has no priorities or lanes for time-bound work | FR-39, FR-42, FR-8, FR-34 | AD-10 |
| M6 | Med | Retention and erasure scope misses `events`, Evidence, bodies, backups and batches | FR-52, FR-53 | AD-4, compliance |
| M7 | Med | Unsubscribe tokens have no owner, dangle after purge, and the mailto path is unrouted | FR-70, FR-41 | AD-15, map |
| M8 | Med | Suppression is enforced only at send, not at discovery, drafting or channel Drafts | FR-50, FR-41 | AD-5 sibling rule |
| M9 | Med | FR-38's Postmaster input has no port; Sending Domain and warm-up record have no owner | FR-38, FR-32, FR-33, FR-64 | ports, map |
| M10 | Med | Outbound message assembly (plain text, footer, headers, DKIM, approval-hash scope) is unowned | §5.5, FR-23, FR-70 | AD-6, Conventions |
| M11 | Med | Temporary-bounce retry conflicts with exactly-once; Bounce has no owner | FR-44 | AD-4, AD-5, map |
| M12 | Med | Manual Reply detection can log the engine's own sends as Manual Replies | FR-65, FR-37 | AD-5 |
| M13 | Med | Variant assignment and the sticky Sending Inbox aren't on the Enrollment | FR-3, FR-34, FR-54, SM-10 | ER diagram, map |
| M14 | Med | Lead Tier: owner conflict, no time-based recompute, needed in Slice 1 | FR-62, FR-11, FR-26, §6.3 | map, AD-10 |
| M15 | Med | Address Class has no owner or state machine; per-domain pattern state has no entity | FR-15, FR-16, FR-14 | AD-4, map |
| M16 | Med | Approved Company Facts aren't versioned or scoped by Segment and Market; two seed sources | FR-72, FR-21, FR-26 | AD-7, AD-12 |
| M17 | Med | No headless browser in the stack for FR-10 (Slice 1) | FR-10 | Stack, ports |
| M18 | Med | Internal emails (alert, Call Prep, Task list) have no defined send path | FR-42, FR-68, FR-48 | AD-5, AD-14 |
| M19 | Med | PRD timing and quota numbers aren't bound anywhere | FR-39, FR-70, FR-57, FR-33, FR-48 | Conventions, AD-9 |
| M20 | Med | The AI cost estimate ($12–20) leaves no headroom under the $20 budget | FR-57, §5.2, §11 | AD-8 sizing |
| M21 | Med | Slices 1–2 have no way to change seeded config (including the UK LIA) | §6.3, FR-3 | AD-12 |
| M22 | Med | Channel Permission, touch log and LinkedIn account have no entity or owner | FR-24, FR-47, FR-48 | map |
| M23 | Med | "Researched overnight" doesn't fit the PC's uptime | UJ-1, §5.3 | AD-10 schedule |
| L1 | Low | Logon-only start versus "restart when the PC boots" | §5.3 | Stack note |
| L2 | Low | Local backups share a folder with the live DB | §5.3 | Conventions |
| L3 | Low | Reversing a rule-based opt-out isn't modelled | FR-40, FR-50 | AD-4 |
| L4 | Low | The OAuth callback conflicts with AD-13's no side-effecting GETs | FR-56 | AD-13 |
| L5 | Low | AD-11 has no Sender timezone and no defined day for caps | SM-8, FR-43, FR-33 | AD-11 |
| L6 | Low | Evidence type, source and "extracted" label are conflated | Glossary, FR-8, §5.6 | AD-7 |
| L7 | Low | The grounding check's rule-based half and Call Prep grounding have no home | FR-21, FR-68 | AD-7, domain |
| L8 | Low | The ports list doesn't match AD-9's one-port-per-system rule | FR-7, FR-15, FR-38 | source tree |
| L9 | Low | Telegram is send-only, but FR-48 has the owner mark Tasks from his phone | FR-48 | adapter, map |
| L10 | Low | Other PRD entities are missing from the ownership map | FR-4, FR-7, FR-22, FR-61, FR-66 | map |
| L11 | Low | The Worker's GET behaviour is undefined (link-scanner prefetch) | FR-70 | AD-15 |

---

## High

### H1. AD-15 strips Telegram alert content that FR-42 and FR-48 require

- **PRD:**
  - §4.9 FR-42 (L416–419);
  - §4.11 FR-48 (L483);
  - addendum §2 Alerts row (L30): the same channels carry the LinkedIn Task list;
  - §5.4 (L588): data may go to the named external APIs, and Telegram is one.
- **Spine:** AD-15: "Telegram alerts include the Lead name, the Reply text and the Draft, nothing else."
- **Gap:**
  - FR-42 requires the Telegram message *and* the email each to carry:
    - the Lead and Contact;
    - the Reply text;
    - the Signal cited;
    - the Reply Draft;
    - the booking link;
    - two proposed slots;
    - the Sending Inbox to answer from.
  - AD-15 forbids the Contact, Signal, slots, booking link and Sending Inbox. Without the Sending Inbox, Furqaan can't tell which of three Gmail accounts to reply from (UJ-2, SM-8).
  - FR-48 Task messages (profile link, name, company, role, note, first message) are banned outright.
- **Fix:**
  - Rewrite AD-15's Telegram bullet as an allowlist per message type:
    - Hot Lead alert: exactly FR-42's field list;
    - "draft to follow" message: the Reply Draft plus a thread reference;
    - LinkedIn Task list: FR-48's fields.
  - Everything else stays banned (Evidence beyond the cited Signal, other Contacts, Provenance), which keeps the minimisation intent.
  - Apply the same allowlist to the alert email.

### H2. AD-5's single send gate contradicts FR-34 and misses send-time checks the PRD requires

- **PRD:**
  - FR-34 (L371): Reply Drafts are sent at once, outside Cap, gap and Window;
  - FR-33 (L363) and FR-64 (L359): block *cold* sends;
  - FR-23 (L301);
  - FR-3 (L137–138);
  - FR-38 (L392–395);
  - FR-31 (L342, L346);
  - FR-65 (L429);
  - FR-37 (L379–388);
  - FR-67 (L443).
- **Spine:** AD-5 step 1. `compliance.assert_sendable` re-checks the Suppression List, Eligibility, Send Window, Daily Cap, warm-up and domain authentication for every OutboundMessage.
- **Gap:**
  - **(a) Contradiction: the gate applies cold-email checks to replies.**
    - A hot reply approved at 2:31 PM could be held to the next Send Window or blocked by a full Daily Cap.
    - Warm-up and authentication block only cold sends in the PRD.
    - Eligibility (FR-18) is a cold-email test. A new person who replied (FR-37) or a referrer receiving a thank-you (FR-67) may have no Published or Verified address.
  - **(b) Checks missing from the gate:**
    - FR-23: legal name, postal address and UK privacy link are not blank;
    - FR-3: the Campaign is active, not paused, and has a UK LIA recorded;
    - FR-38: the inbox and domain are not paused;
    - FR-33: the total daily ceiling (default 50) isn't reached;
    - FR-37: the Enrollment is still active, not stopped or paused by an uncertain Reply;
    - FR-31: a Step 1 citing a job posting last checked over 24 hours ago is re-checked;
    - FR-65: the thread is re-checked for a Manual Reply before a Reply Draft is sent.
- **Fix:**
  - Amend AD-5: OutboundMessage gets a `kind` from {`step`, `reply_draft`, `referral_thanks`, `referral_step1`, `rebooking`}.
  - `domain.sending_policy` holds a check matrix per kind:
    - **Steps:** every cold check plus the missing ones above.
    - **Reply kinds:** suppression, Enrollment and thread state, footer, inbox not policy-paused, and the Manual Reply re-check. No Window, Cap, gap or warm-up.
  - Network re-checks (FR-31 posting, FR-65 thread) run before the transaction. Their results are passed in with a timestamp, and the gate rejects stale ones.

### H3. Budget and quota gates block paths FR-57 says must never be blocked

- **PRD:** FR-57 (L531–533); FR-21 (L293); FR-43; FR-26 (L317); FR-68; FR-67 (L444).
- **Spine:**
  - AD-8: "The FR-57 budget blocks only non-urgent routes; `classify_reply` and `draft_reply` always run."
  - AD-9: "each adapter records the call in `api_usage` and checks its quota."
- **Gap:**
  - FR-57 lets quotas or the AI budget stop only discovery, enrichment and re-checks. It says they never block sending, reply handling or unsubscribe polling. AD-8 blocks every other route:
    - `grounding_check`: an unchecked Reply Draft can't be approved (FR-21), so the hot path stalls;
    - `call_prep`: the FR-68 Hot Lead path;
    - `draft_sequence`: FR-26 regeneration of already-enrolled follow-ups, and the FR-67 referral Step 1;
    - `signal_inbox_parse`.
  - AD-9's "check quota before calling" applies equally to the Gmail, Telegram, Unsubscribe and DNS (FR-64) adapters.
  - The 80% alert isn't carried anywhere.
  - Against the memlog's own $12–20 estimate (M20), the stop will be hit in normal months.
- **Fix:**
  - **AD-8:**
    - Each call carries a `purpose`.
    - The budget blocks only `discovery`, `enrichment` and `recheck`.
    - Reply handling (`classify_reply`, `draft_reply`, grounding of Reply Drafts and of Operator edits, `call_prep`) and queue upkeep (FR-26 regeneration) always run.
    - The decision on new-Sequence drafting under budget is made explicitly.
    - Alert at 80%.
  - **AD-9:** tag ports as either:
    - essential (mail, alert, unsubscribe, DNS auth): meter, never block;
    - non-essential: meter and block.

### H4. Per-route batching breaks FR-8's 15-minute path and interactive regenerate and recheck

- **PRD:**
  - FR-8 (L168): a resulting Step 1 Draft reaches the queue within 15 minutes;
  - UJ-3 (L48);
  - SM-11 (L668);
  - FR-25 (L308–309);
  - FR-21 recheck after an edit (L293);
  - FR-26 (L317);
  - FR-67 (L444).
- **Spine:**
  - AD-8: "Batchable routes run through the Message Batches API." `batchable` is a route property.
  - Memlog: batches cover "nightly enrichment extraction and Step drafting"; only `signal_inbox_parse` is named as direct.
- **Gap:**
  - A Signal Inbox Lead needs `extract_signals`, `extract_people`, `draft_sequence` and `grounding_check` within 15 minutes. Batches carry no latency guarantee: up to 24 hours (review-verified M1).
  - Regenerate (FR-25) and recheck-after-edit (FR-21) happen while Mac sits at the queue (FR-29 targets under 300 ms per card).
- **Fix:** AD-8 chooses batching per call, not per route:
  - `LlmPort.run(route, inputs, priority)` with priority `interactive`, `soon` or `bulk`.
  - Only `bulk` work may batch: scheduled enrichment, and Sequence drafting for Leads selected automatically.
  - Signal Inbox, referral, regenerate, recheck and every Reply path run direct.
  - Add review-verified M1's per-route deadline.

### H5. AD-9's Places rule removes the website URL that FR-5 records

- **PRD:**
  - FR-5 (L157–160) and its ASSUMPTION: name and website are held until the Lead's own website replaces them;
  - FR-9 (L171–172);
  - FR-10 (L188);
  - OQ-3 (L686), owned by the architecture step;
  - addendum §2 Places row (L23): website URL is an Enterprise field.
- **Spine:** AD-9: "Places adapters return only `place_id`, plus coordinates kept for no more than 30 days. They never return or store Places name, address, phone or reviews."
- **Gap:**
  - With only `place_id` and coordinates, a Places candidate has:
    - no website to crawl (FR-10);
    - no domain to deduplicate on (FR-9);
    - no name for the Operator to see.
  - The memlog resolves OQ-3 but never says what happens to the website URL, so Slice 2 discovery can't be built as specified.
- **Fix:**
  - AD-9: the adapter returns a transient `PlaceCandidate {place_id, website_uri, display_name}`. It is used once to start the crawl, and is either never persisted or kept at most 30 days in a TTL column owned by `discovery`.
  - The durable Lead name and domain come from the Lead's own website, with Evidence (AD-7).
  - Deduplication uses the crawled domain plus `place_id`.
  - Record the OQ-3 answer, including whether `websiteUri` may be cached.

### H6. The monthly test restore has no job, owner, record or pass criteria

- **PRD:**
  - §5.3 (L585): "A monthly test restore reports to the health panel";
  - FR-58 (L540): "the last monthly restore test result";
  - §6.3 (L622): FR-58 ships in Slice 1.
- **Spine:**
  - Capability map row: "Reliability and backups (§5.3) | `scripts/`, `main.py` startup | AD-2, AD-10".
  - Deferred: "Off-PC backup encryption tool and destination".
- **Gap:**
  - No AD mentions restore testing. There is:
    - no job type;
    - no table;
    - no owner in the ownership map;
    - no definition of a pass.
  - The restore is the only proof that the mandatory off-PC copy of the Suppression List and Provenance Records is usable.
- **Fix:** add **AD-16 Backup and restore**.
  - **Jobs (AD-10):** `backup_db` daily and `test_restore` monthly.
  - **Owner:** an `ops` service owns:
    - `backup_runs`;
    - `restore_tests` {started_at, backup_id, location, decrypted_ok, integrity_ok, alembic_head_match, suppression_count, provenance_count, result}.
  - **A pass means:**
    1. decrypt the latest *off-PC* copy with the escrowed key (M3);
    2. open it in a temporary directory;
    3. `PRAGMA integrity_check` returns ok;
    4. the Alembic revision is at head;
    5. suppression and provenance counts are at least the live counts at backup time.
  - **On failure:** raise a health-panel error and send an Operator alert.
  - Add the rows to the ownership map.

### H7. The backup mechanism conflicts with AD-2 and with PC uptime

- **PRD:**
  - §5.3 (L583–585);
  - FR-58 (L538): "each job's last successful run (… backup)";
  - addendum §1 (L12).
- **Spine:**
  - Source tree: `scripts/backup.ps1`.
  - Deployment diagram: "encrypted nightly backup".
  - AD-2: one process, WAL.
- **Gap:**
  - A PowerShell copy of the DB file while the engine writes can tear and miss the `-wal` file.
  - An out-of-process script can't record its "last successful run" for FR-58 without becoming a second DB writer, which AD-2 forbids.
  - "Nightly" assumes the PC is on at night. The PRD's assumption runs from the UK morning to 17:00 PT, and Mac confirmed mornings only, so a fixed night trigger may never fire.
- **Fix:** in AD-16:
  - Backups run in-process as a job, using `sqlite3.Connection.backup()` or `VACUUM INTO` to a temp file, then encrypt and copy off-PC.
  - Trigger during PC-on hours, with `misfire_grace_time` long enough to run at the next start.
  - Record the result in `backup_runs`.
  - Remove `backup.ps1`, or reduce it to a manual-restore helper.

### H8. Restoring a backup can resurrect opt-outs and re-send messages

- **PRD:**
  - §5.3 (L585): the off-PC copy is mandatory for the Suppression List;
  - FR-41 (L414);
  - FR-50 (L494): entries never expire;
  - FR-52 (L500);
  - FR-35 (L374);
  - FR-70 (L507–508);
  - §5.1 (L563): CAN-SPAM opt-outs are honored.
- **Spine:**
  - AD-15: the engine polls `GET /pending` "and acknowledges", which deletes from KV (review-verified M3).
  - AD-5: startup reconciles only `sending` rows.
- **Gap:** after restoring a DB that is N hours old:
  1. Opt-outs pulled from the Worker since the backup were acknowledged and deleted. They now exist in neither copy.
  2. Erasures since the backup are undone.
  3. Messages sent since the backup are back in `queued` or approved state. AD-5 reconciles only `sending` rows, so they send twice.

  Each case either re-contacts someone who opted out or breaks exactly-once.
- **Fix:** in AD-16 and AD-15:
  1. Append every suppression insert and erasure, as hash plus time, to an encrypted off-PC **suppression journal** at once, not with the daily backup.
  2. The Worker marks tokens as acknowledged and keeps them N days (e.g., 30) instead of deleting them.
  3. After a restore, the engine starts in a `recovery` mode that blocks sending until it has:
     - replayed the journal;
     - re-polled the Worker from the backup time;
     - reconciled every OutboundMessage created before the backup against each inbox's Sent folder since the backup time.

### H9. FR-49 has no structural guard against fetching LinkedIn, Maps or job sites

- **PRD:**
  - FR-49 (L485–486);
  - FR-8 (L167): Signal Inbox items are typically LinkedIn post URLs;
  - §5.1 platform terms (L567);
  - §7 (L648).
- **Spine:**
  - AD-9 crawler rule: robots.txt, fixed User-Agent, 1 second per domain.
  - Capability map maps FR-49 to `app.pipeline` and `web` ("no LinkedIn adapter exists").
- **Gap:**
  - FR-49 binds every outbound fetch, not just the LinkedIn assist screen.
  - The crawler has no host denylist, and the Signal Inbox stores a URL beside the pasted text, so "fetch the source page for context" is the natural implementation, and it breaches FR-49.
  - robots.txt doesn't express the policy for Google Maps or job sites.
- **Fix:**
  - AD-9: `CrawlerPort` and every HTTP adapter enforce a hard-coded, non-configurable denylist, with a unit test:
    - `linkedin.com` and its subdomains, and `lnkd.in`;
    - `instagram.com`;
    - Google Maps hosts and paths;
    - `indeed.*`;
    - LinkedIn Jobs.
  - Signal Inbox URLs are stored as Evidence source only and never fetched when denylisted.
  - Add FR-49 to the 4.3 row (`adapters.crawler`).

### H10. Real prospect Replies would enter version-controlled test fixtures, outside erasure and retention

- **PRD:**
  - FR-40 (L408): "Overrides join the Reply set";
  - addendum §3 (L38): fixtures are files under version control, plus Operator overrides "as real Replies arrive";
  - §5.4 (L588);
  - FR-52 (L500);
  - FR-53 (L503).
- **Spine:**
  - Source tree: `tests/ai_sets/` (FR-73 golden sets).
  - Ownership map: `playbook` owns only `ai_test_runs`.
- **Gap:**
  - The running engine has no owner or write path for adding an override to the Reply set. The only home is repository files.
  - Real Reply text (names, signatures, phone numbers) in Git escapes FR-52 erasure and FR-53 retention, and leaves the PC if the repository has a remote (§5.4).
- **Fix:**
  - `playbook` owns `ai_test_cases` {set, input, expected, source = synthetic | override, contact_id nullable} in the DB.
  - Overrides are stored **redacted**: names, emails, firm and phone replaced with placeholders. The `contact_id` link lets purges cascade.
  - Only synthetic or redacted cases may be exported to `tests/ai_sets/`.
  - Add the table to the map and to M6's PII registry.

### H11. The FR-73 activation gate has no legitimate writer, and the mutable Playbook file defeats it

- **PRD:**
  - FR-73 (L556): "The engine … runs them whenever a prompt, model setting, or Sales Playbook version changes. A failing version can't go active";
  - FR-71 (L547);
  - §5.6 (L598).
- **Spine:**
  - AD-8 last bullet: a route version activates only on an `ai_test_runs` pass for the exact tuple.
  - AD-12: the Playbook is one file, and its frontmatter `version` is the Playbook version.
  - AD-14 and Conventions: `ai_eval` is an opt-in pytest suite, and each environment has its own DB.
- **Gap:**
  1. The sets run under pytest, against a test DB (AD-14) and outside the engine process (AD-2). Nothing writes `ai_test_runs` into the prod DB that `playbook` owns.
  2. Editing `sales-playbook.md` and bumping its version leaves every route without a passing tuple, so drafting and `draft_reply` go inactive. Editing it *without* bumping the version bypasses the gate silently.
  3. The last passing Playbook text is overwritten, so "a failing version can't go active" leaves nothing active to fall back to.
  4. The tuple's fields aren't defined.
- **Fix:** in AD-8 and AD-12:
  - The engine detects changes by **content hash**: prompt file, Playbook, model, effort and schema.
  - On a change, it enqueues `run_ai_test_sets`, and `playbook` writes `ai_test_runs`.
  - Route resolution uses the newest passing snapshot. Playbook and prompt content are stored per hash in the DB, so the old version stays active until a new one passes.
  - pytest `ai_eval` stays for developer runs only.

### H12. Slice 1 doesn't record the facts FR-54 and FR-55 later report

- **PRD:**
  - §6.3 (L618): "Every slice records the events FR-54 and FR-55 later report";
  - FR-54 (L516–519);
  - FR-55 (L522);
  - SM-3 (L660), SM-6, SM-7 (L664), SM-8 (L665), SM-11 (L668).
- **Spine:**
  - Capability map: "4.13 Analytics | `app.pipeline` read models over `events` | AD-4".
  - AD-4 records only state-machine transitions.
  - AD-6: an edit returns the Draft to `ready`.
- **Gap:** these metrics can't be rebuilt from status transitions:
  - the share of characters changed by editing, which needs the AI's original text (SM-7);
  - daily approval time (SM-6);
  - daily capacity and signal shortfall, which need a daily snapshot;
  - Lead Tier and Lead Score *at contact time*, since Tier changes (SM-3 by Tier);
  - Variant, Segment, Market and Sending Inbox per contacted Contact;
  - hiring Events per 100 Leads crawled by source;
  - the generic-flag share (FR-22).

  If Slice 1 overwrites Draft bodies on edit, SM-7's history is lost for good.
- **Fix:** add an "analytics facts" rule to AD-4 and ship it in Slice 1's first migrations, with owners named:
  - Draft keeps an immutable `ai_subject` and `ai_body` beside the approved text.
  - `approval_sessions` {start, end, items}.
  - `daily_capacity` snapshot, written by the selection job.
  - OutboundMessage snapshots {tier, lead_score, variant_id, segment, market, inbox_id, step}.
  - `crawl_runs` {lead, source, pages, events_found}.

---

## Medium

### M1. The ownership map breaks AD-3's single-owner rule

- **PRD:**
  - FR-1 disqualifiers (L110);
  - FR-28 (L330–331);
  - FR-31 (L343);
  - FR-37 (L379–388);
  - FR-45 (L456);
  - FR-65 (L429);
  - FR-67 (L442);
  - FR-16 and FR-44;
  - FR-52 and FR-53.
- **Spine:** AD-3 ("exactly one owning application service"); ownership map.
- **Gap:**
  - **Lead** is written by `discovery`, `research` (attributes, Tier), `scoring` (scores) and `pipeline` (stage). FR-1 and FR-28 also disqualify it from `research` and `approval`.
  - **Draft** is written by `drafting` and `approval`, and FR-65 supersedes it from `replies`.
  - **Enrollment** is written by `scoring` (create) and `replies` (stop). FR-28, FR-37 and FR-31 also put writes in `approval`, `pipeline`, `compliance` and `sending`, and nobody owns "advance to the next Step" or the Out of Office delay.
  - **Contact** is written by `research`. `replies` creates repliers (FR-37) and referrals (FR-67), bounces set it Invalid, and `compliance` erases it.
- **Fix:** choose one of:
  - split each entity into per-owner tables (`lead_scores`, `lead_stage`, `enrollment_schedule`);
  - name one owner per entity and list the commands others call. For example, a `sequencing` service owns Enrollment: `create`, `advance`, `pause`, `delay_until`, `stop(cause)`.

  Either way, every map row becomes exactly one table.

### M2. AD-1 forbids the writers the ownership map names for `jobs`, `api_usage` and `ai_usage`

- **PRD:** FR-57 (L531); FR-58 (L537–538).
- **Spine:**
  - AD-1 table: adapters may import only ports, domain types and SDKs; `web` and `jobs` never import `db`.
  - Ownership map: "`nextron/jobs` (runner) | jobs" and "adapters (meters) | api_usage, ai_usage".
  - AD-9: each adapter records its own calls.
- **Gap:** neither the runner nor the adapters may touch the DB, so FR-57 metering and the job table have no legal writer.
- **Fix:**
  - Add an `app.ops` service that owns `jobs`, `api_usage` and `ai_usage`.
  - Adapters get a `UsageMeter` port, which the app implements.
  - The runner calls `ops.claim`, `ops.complete` and `ops.fail`.
  - Update AD-1 and the map.

### M3. Off-PC backup key custody is unspecified

- **PRD:** §5.3 (L585); §5.4 (L588).
- **Spine:**
  - AD-15: "Off-PC backups are encrypted before they leave the PC."
  - AD-12: secrets live only in Credential Manager.
  - Deferred: the encryption tool.
- **Gap:** if the key lives only in this PC's Credential Manager, losing the PC makes the mandatory off-PC copy undecryptable.
- **Fix:**
  - AD-16: the key is derived from a passphrase that Mac and Furqaan hold off the PC.
  - The restore test (H6) proves decryption without relying on the local keyring copy.

### M4. AD-5 reconciles in-flight sends only at startup

- **PRD:**
  - FR-35 (L374): "crashes, the PC sleeps, or the network drops mid-send";
  - UJ-5 (L50);
  - §5.3 (L583).
- **Spine:** AD-5 last bullet.
- **Gap:**
  - Sleep and network loss don't restart the process, so a row stuck in `sending` stays there until the next restart.
  - Meanwhile it blocks the Sequence and can pass its 3-day approval expiry.
- **Fix:**
  - A `reconcile_sends` job runs:
    - at startup;
    - on resume, detected as a jump in wall-clock time against the monotonic clock;
    - every N minutes, for `sending` rows older than a timeout.
  - Ambiguous rows go to review (review-verified H2).

### M5. The job runner has no priorities, so time-bound work can wait behind crawls

- **PRD:**
  - FR-39 (L402): every 2 minutes;
  - FR-42 (L417, L419): within 5 minutes, or "draft to follow" after 2 minutes;
  - FR-8 (L168): 15 minutes;
  - FR-34 (L368, L371): send gaps, and Reply Drafts "at once";
  - FR-41 (L414);
  - FR-70 (L508).
- **Spine:** AD-10 has one jobs table and a leased runner, with no priority, lanes or concurrency.
- **Gap:** a 15-page crawl at one request per second, headless rendering (M17) or batch ingestion can hold the runner while a hot Reply waits. That misses the 5-minute alert and SM-8.
- **Fix:**
  - AD-10: add `priority` and `lane` columns, with separate workers per lane:
    - `realtime`: reply poll, classify, alert, reply send, unsubscribe poll;
    - `send`: scheduled Steps;
    - `bulk`: crawl, enrichment, batches, backups.
  - Each lane has a maximum runtime, and bulk handlers yield between pages.

### M6. Retention and erasure scope stops short of where personal data lives

- **PRD:** FR-52 (L500); FR-53 (L503); §5.4 (L588); addendum §3 FR-53 (L44).
- **Spine:**
  - AD-4: `events` are never edited "apart from FR-52 erasure, which blanks PII fields".
  - Ownership map: `compliance` owns erasure, export and retention.
- **Gap:**
  - AD-4 allows blanking `events` only for FR-52, so FR-53's automatic deletion would leave PII there.
  - No rule covers:
    - Evidence quoting a named person's post, and SignalInboxItems;
    - Reply, ManualReply and OutboundMessage bodies, and Drafts;
    - CallPrep and LinkedInTasks;
    - AI test cases (H10);
    - Anthropic batch inputs, kept 29 days (review-verified L6);
    - local and off-PC backups, which keep erased data indefinitely.
- **Fix:**
  - Add a **PII registry** (a list of table.column pairs) with a test that new columns are classified.
  - One `purge_contact(contact_id, reason)` path blanks every registry column, including `events`.
  - Backups rotate (e.g., 35 days), and a restore re-applies the purge log (H8).
  - Each batch is deleted after ingest.

### M7. Unsubscribe tokens have no owner and can outlive their Contact

- **PRD:**
  - FR-70 (L506–508);
  - addendum §3 (L43): a token per Step, mapped locally;
  - FR-41 (L411);
  - FR-39 (L402): non-Replies are ignored;
  - FR-52 and FR-53.
- **Spine:** AD-15 first bullet; the ownership map has no token row.
- **Gap:**
  - Nobody owns creating tokens or resolving them.
  - After erasure or a retention purge, old emails stay clickable but the Contact row is gone. The opt-out is lost, and a rediscovered person can be emailed again.
  - The mailto unsubscribe (`subject=unsubscribe-TOKEN`) arrives off-thread, where FR-39's "non-Replies are ignored" drops it.
- **Fix:**
  - `compliance` owns `unsubscribe_tokens` {token, outbound_message_id, address_hash, domain, created_at}.
  - `sending` requests a token when it builds the message.
  - A purge keeps the token → `address_hash` mapping.
  - The inbound mail router hands `unsubscribe-<token>` messages to `compliance` before Reply matching.

### M8. Suppression is enforced only at send time

- **PRD:**
  - FR-50 (L492): checked "at discovery, Eligibility, and drafting, before any channel Draft, and immediately before each send";
  - FR-41 (L411): "blocks all future channel Drafts";
  - FR-63 (L277).
- **Spine:** AD-5 (the send gate); `domain.eligibility`.
- **Gap:** nothing requires a check at discovery, drafting or channel Drafts. LinkedIn, WhatsApp and Instagram Drafts never pass AD-5. Suppressed people can be re-imported, drafted, queued and given LinkedIn Tasks.
- **Fix:**
  - Add a rule beside AD-5: `compliance.is_suppressed(address | domain | contact)` is called by:
    - `discovery`, before creating a Contact;
    - `scoring`, in Eligibility;
    - `drafting`, for every Draft kind;
    - `pipeline`, for LinkedIn Tasks and FR-47 drafts.
  - A test asserts that every Draft-creating path calls it.

### M9. FR-38's inputs and the Sending Domain have no port, adapter or owner

- **PRD:**
  - FR-38 (L390–396);
  - FR-64 (L357–359);
  - FR-32 (L351–355);
  - FR-33 (L362–365);
  - SM-5 (L662);
  - §6.3: FR-38 is Slice 1.
- **Spine:**
  - The adapter list has no Postmaster adapter.
  - The ownership map gives `sending` only "OutboundMessage, SendingInbox status and caps".
- **Gap:**
  - The Postmaster Tools spam rate (alert at 0.1%, pause at 0.3%, "no data" recorded as no data) needs its own API, scope and credential. None is in the spine or in the addendum's credential list.
  - Sending Domain (authentication results, blocklist status, domain-level pause) has no owner.
  - The warm-up record and inbox connect and remove are owned only as "status and caps".
  - Spam-accusation Replies, which count as complaints for SM-5, need a flag in the `classify_reply` output, because spam accusation isn't a Reply Class.
- **Fix:**
  - Add a `PostmasterPort` and adapter (read-only scope, credential in FR-56), with null meaning no data.
  - `sending` owns SendingDomain, the whole SendingInbox row, WarmupRecord and DomainCheck {kind: auth | dnsbl | postmaster, result, at}.
  - Add `spam_accusation: bool` to the `classify_reply` schema.

### M10. Outbound message assembly isn't specified

- **PRD:**
  - §5.5 (L594): plain text only; no open or link tracking, and neither can be turned on;
  - FR-23 (L299–301);
  - FR-70 (L506);
  - addendum §3 (L43): the DKIM signature must cover both List-Unsubscribe headers;
  - FR-34 (L370);
  - FR-67 (L443);
  - FR-4 (L150).
- **Spine:** AD-5 (`X-Nextron-Message-Id`); AD-6 (SHA-256 of the approved subject and body).
- **Gap:**
  - No module owns building the MIME message:
    - `text/plain` only, with no HTML part, pixel or link rewriting;
    - the footer, which edits can't remove, and the UK privacy line;
    - the List-Unsubscribe headers;
    - the threading headers.
  - AD-6 doesn't say whether the footer is inside the approval hash. If it's appended at send time, a postal-address change alters text after it was approved.
  - Google's DKIM coverage of the List-Unsubscribe headers is unverified.
- **Fix:**
  - Add a pure `domain.message_builder` that renders the exact sendable text from the Draft, the Market footer, the token and the thread references.
  - AD-6 hashes the rendered body, footer included.
  - Conventions: "outbound MIME is `text/plain; charset=UTF-8`; no tracking setting exists."
  - Add a Slice 1 spike to check that the DKIM `h=` list includes both List-Unsubscribe headers.

### M11. Temporary-bounce retries conflict with exactly-once sending, and Bounce has no owner

- **PRD:** FR-44 (L446–450); glossary Bounce (L86); FR-16 (L243); FR-38 (L392–393).
- **Spine:** AD-4 OutboundMessage machine; AD-5; the ownership map has no Bounce row.
- **Gap:**
  - "Temporary: retried inside the Send Window; treated as hard after 3 failures" means a second send of an approved message.
  - `queued → sending → sent` and "at most once" don't model that.
  - Bounce attribution, the write that marks an address Invalid, and the bounce-rate feed have no owner.
- **Fix:** choose one of:
  - `replies` owns Bounce {outbound_message_id, kind, code, at}. OutboundMessage gains a `deferred` state and an `attempt` counter (same ULID lineage, at most 3), and each retry is re-gated by AD-5 and approval expiry.
  - Decide that Gmail's own 4xx retries are enough, and the engine only records them.

### M12. Manual Reply detection can mistake the engine's own sends for Manual Replies

- **PRD:**
  - FR-65 (L428): "any message … it didn't send (per its own send records)";
  - FR-65 (L430): the Lead moves to *Interested*;
  - FR-37 (L382).
- **Spine:** AD-5: `sent` is recorded after the call, and reconciliation happens only at startup.
- **Gap:**
  - The Sent-folder poll can see a message the engine sent before the `sent` commit, or after a crash left the row in `sending`.
  - Logged as a Manual Reply, it stops the Sequence, moves the Lead to *Interested*, and corrupts SM-8.
- **Fix:**
  - AD-5: match `X-Nextron-Message-Id` against OutboundMessages in **any** status. A match in `sending` triggers reconciliation, never a Manual Reply.
  - Reply polling for an inbox waits until that inbox's startup reconciliation is done.

### M13. Variant assignment and the sticky Sending Inbox aren't on the Enrollment

- **PRD:**
  - FR-3 (L136): new Contacts split evenly between Variants;
  - FR-34 (L370): one inbox, one thread;
  - FR-67 (L443);
  - FR-54 (L516, L519): by Variant, with 150 contacted Contacts per Variant;
  - SM-10 (L667);
  - §8 checkpoints (L678).
- **Spine:**
  - ER diagram: Variant links only to Campaign; Enrollment links to Contact and Campaign.
  - Ownership map: `setup` owns Variant.
- **Gap:**
  - Nothing records which Variant a Contact got, or who assigns it, so the by-Variant metrics, the opt-out comparison and the replace-the-Variant checkpoint can't be computed.
  - Nothing pins an Enrollment's inbox, so Steps could go out from different inboxes.
- **Fix:**
  - Enrollment carries `variant_id` and `sending_inbox_id`, set once by its owner at creation. The balanced assignment is persisted.
  - Drafts and OutboundMessages inherit both.
  - Add the edges to the ER diagram.

### M14. Lead Tier: owner conflict, no time-based recompute, and Slice 1 needs it

- **PRD:**
  - FR-62 (L252–254): recomputed "whenever an Event is added or expires";
  - FR-11 (L197): decay and expiry;
  - FR-26 (L315), FR-27 (L321) and FR-22 (L296) are Slice 1;
  - FR-17 and FR-62 are Slice 2 (§6.3, L622–623);
  - UJ-3 (L48).
- **Spine:**
  - Ownership map: `research` writes "Lead attributes and Tier".
  - Capability map: FR-62 lives in `app.scoring`.
- **Gap:**
  - The two maps disagree on the owner.
  - Tier changes with time alone: Tier 1 Events reach zero weight at 90 days, Tier 2 Events expire, and postings close. No recompute job exists.
  - Slice 1's queue order, review card and fallback copy all use Tier and Score, which ship in Slice 2.
- **Fix:**
  - Tier belongs to `scoring`.
  - The pure `domain.scoring` functions ship in Slice 1; only automatic selection waits for Slice 2.
  - Add a daily `recompute_tiers` job, plus a recompute when an Event is added or a posting closes.

### M15. Address Class has no owner or state machine

- **PRD:**
  - glossary (L68);
  - FR-15 (L233–240);
  - FR-16 (L243);
  - FR-44 (L448);
  - FR-59 (L177);
  - FR-14 (L224–229).
- **Spine:** AD-4's list has no Address Class; the map lists only "Contact (create/attributes)" under `research`.
- **Gap:**
  - Address Class gates Eligibility, yet these transitions have no rule and two writers (`research` and the bounce path):
    - Guessed → Verified;
    - any class → Invalid, on a failed check or a hard bounce;
    - accept-all and unknown stay Guessed;
    - CSV addresses stay Guessed unless they're also on the Lead's website.
  - Per-domain state has no entity: FR-16's stop on pattern inference after two hard bounces, and FR-14's Domain Search once per domain.
- **Fix:**
  - Add Address Class to AD-4's machines. `research` owns it, and bounce handling calls `mark_invalid(cause)`.
  - Add `email_domains` {domain, pattern, pattern_source, domain_search_at, pattern_hard_bounces, inference_disabled}, owned by `research`.

### M16. Approved Company Facts aren't versioned or scoped

- **PRD:** FR-72 (L551–553); FR-21 (L290); FR-26 (L317); FR-20 (L283).
- **Spine:**
  - AD-7: Drafts store `company_fact_ids`, and claims come only from ApprovedCompanyFact.
  - AD-12: seed facts come from `seed/company_facts.yaml`.
- **Gap:**
  - FR-26 regenerates follow-ups whose facts changed since drafting. With facts edited in place, the stored ids can't reveal a change.
  - FR-21 and FR-72 require facts filtered to the Draft's Segment and Market (ABA Opinion 512 for the US only; the SRA notice for the UK only). AD-7 has no such filter.
  - FR-72 seeds from Sales Playbook §6, while AD-12 seeds from YAML: two sources that will drift.
- **Fix:**
  - Fact rows are immutable versions: an edit creates a new version and retires the old one. Drafts store version ids.
  - AD-7 adds: "facts are filtered by the Draft's Segment and Market before prompting, and re-checked by the grounding check."
  - Keep one seed source.

### M17. No headless browser for FR-10

- **PRD:** FR-10 (L191); addendum §3 (L36); §6.3: FR-10 is Slice 1.
- **Spine:** the Stack has only selectolax, trafilatura and protego; the ports list has only Crawler.
- **Gap:**
  - FR-10 renders near-empty pages and detects chat and booking widgets from scripts. Nothing in the stack executes JavaScript.
  - Chromium is a significant install and memory choice inside the single process (AD-2).
- **Fix:** choose one of:
  - add a `RendererPort` and adapter (for example Playwright for Python, pinned with its Chromium build), invoked only below a text threshold, in the `bulk` lane, with a timeout;
  - record a deferral and change the PRD.

### M18. Internal emails have no defined send path

- **PRD:**
  - FR-42 (L417): email to Furqaan's main nextronai.com inbox;
  - FR-68 (L467): the Call Prep Sheet is emailed;
  - FR-48 (L483): Tasks go by Telegram or email;
  - addendum §2 credentials (L33): the only Gmail grants are per Sending Inbox.
- **Spine:**
  - AD-5: "Only `app.sending` calls `MailPort.send`. Every send is an OutboundMessage."
  - AD-14: AlertPort sink.
- **Gap:**
  - Under AD-5, these internal emails would be OutboundMessages subject to suppression, Eligibility, Window and Cap.
  - The sending account is unstated. With only Sending Inbox grants, internal mail would come from a cold domain and count toward that inbox's daily ceiling (FR-33).
- **Fix:**
  - Route internal mail through `AlertPort` (Telegram and email), sent from a designated inbox set in settings.
  - It sits outside AD-5 and the Daily Cap, but counts toward FR-33's ceiling.
  - Content follows H1's allowlists.

### M19. Timing and quota numbers from the PRD aren't bound anywhere

- **PRD:**
  - FR-39 (L402): reply and Sent-folder check every 2 minutes;
  - FR-70 (L508, L510): Unsubscribe poll every 5 minutes, alert after 24 hours of failures;
  - FR-42 (L417, L419): alert within 5 minutes, "draft to follow" after 2;
  - FR-8 (L168): 15 minutes;
  - FR-57 (L531–532): $20 budget, 80% alert, Places Pro and Enterprise metered separately, paid Places never automatic;
  - addendum §2 (L26): Hunter Domain Search costs 1 credit per domain;
  - FR-33 (L365): total daily ceiling of 50;
  - FR-48 (L479, L482): 10 Tasks a weekday, at most 100 invitations per rolling week.
- **Spine:**
  - AD-9 lists Hunter at 1 credit per find and 0.5 per verification.
  - The memlog says to poll KV "at most every 5 min".
- **Gap:**
  - None of these numbers is contradicted, but none sits in an AD or in the Conventions, so builders will pick their own.
  - AD-9's credit list omits Domain Search, which the memlog itself flagged for sizing.
  - Dev and prod polling share the KV quota (review-verified M3).
- **Fix:**
  - Add a "Timing and quota defaults" table to Conventions: setting keys with these defaults, and a dev poll rate well below prod.
  - Add Domain Search and the Places SKU tiers to AD-9.

### M20. The AI cost estimate leaves no headroom under the $20 budget

- **PRD:**
  - FR-57 (L531–532);
  - §5.2 (L576);
  - §11 (L726): "the architecture step resizes prompts and models";
  - addendum §4 (L54).
- **Spine:** AD-8; memlog: "Est. $12–20/mo, inside the $20 AI budget".
- **Gap:**
  - The estimate is 60–100% of the default budget. The 80% alert would fire most months, and the stop rule would halt enrichment late in each month.
  - There's no per-route sizing.
  - Opus 5.5's always-on thinking, billed as output, isn't visibly counted.
  - Caching inside batches is best effort (review-verified L7).
- **Fix:**
  - Add a sizing table per route: calls per month (from addendum §4) × input, output and thinking tokens × price, with batch and cache factors. Target at most 70% of the budget.
  - Levers: effort per route, prompt size, and cache-prefix discipline.

### M21. Slices 1–2 have no way to change seeded configuration

- **PRD:**
  - §6.3 (L622): FR-1 to FR-4 ship "as shipped configuration, without editing screens" until Slice 3;
  - FR-3 (L138): a UK Campaign needs an LIA reference and date before it can be activated;
  - §6.4 (L639).
- **Spine:** AD-12: seed data loads "once from `seed/*.yaml` into the DB, which then owns them."
- **Gap:**
  - For months there is no editing screen, and the YAML is read only once.
  - Step briefs, discovery queries, Signal weights and an LIA reference recorded later then have no way in, and builders will hand-edit the DB.
- **Fix:**
  - AD-12: an idempotent `seed sync` (upsert by natural key, writing an event with actor = seed) is allowed until the Slice 3 screens ship.
  - After that, the DB alone owns the configuration.

### M22. Channel Permission, touch log and LinkedIn account have no entity or owner

- **PRD:**
  - glossary (L97);
  - FR-24 (L305–306);
  - FR-47 (L462);
  - FR-48 (L480–483);
  - §5.1 (L567);
  - OQ-5.
- **Spine:** the ownership map gives `pipeline` only LinkedInTask; capability map rows 4.6 and 4.11.
- **Gap:**
  - Channel Permission gates WhatsApp and Instagram Drafts: what the prospect did, when, the Evidence, and the Instagram 24-hour window. It has no entity or owner.
  - Neither do FR-47's logged touches or the LinkedIn account (its owner, its tier, and the 100-invitations-per-week cap).
- **Fix:**
  - `pipeline` owns:
    - ChannelPermission {contact, channel, act, at, evidence_id, window_closes_at};
    - Touch {lead, contact, channel, at, by};
    - LinkedInAccount {owner, tier, weekly_invite_cap}.
  - `drafting` refuses WhatsApp or Instagram Drafts without a live permission.

### M23. "Researched overnight" doesn't fit the PC's uptime

- **PRD:**
  - UJ-1 (L46): "researched overnight";
  - §5.3 (L584): the PC is on from the UK morning to 17:00 PT, and Mac confirmed mornings only;
  - addendum §1 (L12).
- **Spine:**
  - Memlog: batches are "nightly enrichment extraction and Step drafting".
  - Deployment diagram: "nightly backup".
- **Gap:**
  - Crawling needs the PC on, and batches can take up to 24 hours.
  - Night-scheduled work runs only at 08:00, when the PC starts, so the morning queue misses the first UK Send Window.
- **Fix:** AD-10 defines the daily schedule relative to PC-on hours:
  - crawl, extract and submit drafting batches in the afternoon and evening session;
  - ingest results at startup (results are kept 29 days);
  - keep the morning for the queue and sending.

---

## Low

### L1. Logon-only start versus "restart when the PC boots"

- **PRD:** §5.3 (L583); addendum §1 (L16): "at logon or boot".
- **Spine:** Stack row (Task Scheduler logon task); memlog ("run only when logged on").
- **Gap:** after an unattended reboot, such as a Windows Update, nothing runs until someone logs in. No alert can fire either, because the engine is the alerter.
- **Fix:**
  - State the constraint in the spine, and recommend auto-logon or a check after updates.
  - Optionally, have the Worker raise a dead-man alert when `/pending` goes unpolled during weekday PC-on hours (see review-verified M4).

### L2. Local backups share a folder with the live DB

- **PRD:** §5.3 (L585): "a second location on the PC".
- **Spine:** Conventions, Data dir: it "holds the DB, logs and local backups".
- **Gap:** the same folder isn't a second location. Deleting or corrupting the data directory loses both.
- **Fix:**
  - The local backup directory is a setting that must differ from the data directory, preferably on another volume.
  - Startup warns if it doesn't.

### L3. Reversing a rule-based opt-out isn't modelled

- **PRD:**
  - FR-40 (L406): the Operator can reverse it, with a logged reason;
  - FR-50 (L494): entries never expire.
- **Spine:** AD-4 (SuppressionEntry isn't in any state machine); ownership map (`compliance`).
- **Gap:** a reversal needs a sanctioned way to void one entry, while "never expire" stays true for everything else.
- **Fix:**
  - SuppressionEntry is `active` or `voided`.
  - Voiding is reachable only through an FR-40 reversal, with a reason, an actor and an event row.
  - Hash-only entries left by erasure can't be voided.

### L4. The OAuth callback conflicts with AD-13's no side-effecting GETs

- **PRD:** FR-56 (L528); FR-32 (L352).
- **Spine:** AD-13; memlog (OAuth Desktop client).
- **Gap:** an OAuth redirect is a GET that stores tokens. A builder who adds `/oauth/callback` to the dashboard breaks AD-13.
- **Fix:** AD-13 names either:
  - the installed-app loopback flow, on an ephemeral port separate from the dashboard, with `state` checked;
  - a single exempt callback route.

### L5. AD-11 has no Sender timezone and no defined day for caps

- **PRD:**
  - SM-8 (L665): the Sender's working hours, in his timezone;
  - FR-43 (L423): the Sender's weekly availability;
  - FR-33 (L365): total daily ceiling;
  - FR-34: Daily Cap.
- **Spine:** AD-11 defines only recipient timezones and UTC storage.
- **Gap:**
  - One inbox sends from the UK morning to the US Pacific afternoon, so "per day" for the Daily Cap and the ceiling is undefined.
  - SM-8 and FR-43 need the Sender's timezone.
- **Fix:**
  - Add a `sender_timezone` setting, and count cap and ceiling days as calendar days in that timezone.
  - The ceiling counts every SENT-labelled message, including warm-up-tool sends, read through the Gmail adapter.

### L6. Evidence type, source and "extracted" label are conflated

- **PRD:**
  - glossary Evidence (L64): Quote up to 300 characters, Absence with the pages checked, Count;
  - FR-8 (L167): source = "manual";
  - FR-1 table, Evidence column;
  - §5.6 (L597): AI-extracted facts are labeled as extracted.
- **Spine:** AD-7 types are `quote`, `absence`, `count`, `manual` and `registry`.
- **Gap:**
  - `manual` and `registry` are sources, not observation types. A Signal Inbox quote typed `manual` won't satisfy FR-1's "Quote".
  - Nothing marks a fact as AI-extracted, rule-derived or human-entered.
- **Fix:**
  - `type` is one of {quote, absence, count}.
  - `source` is one of {crawl, ats, registry, signal_inbox, csv, referral}.
  - `extracted_by` records the route and version, `rule`, or `operator`.
  - Enforce the 300-character quote limit.

### L7. The grounding check's rule-based half and Call Prep grounding have no home

- **PRD:**
  - FR-21 (L288–293);
  - addendum §3 (L37): rule-based checks;
  - FR-68 (L466): the Call Prep Sheet passes the grounding check;
  - FR-73 (L557): 100% of banned patterns caught.
- **Spine:** AD-7 binds Drafts only; AD-8 has the `grounding_check` route.
- **Gap:**
  - Word limits, link and CTA counts, subject prefixes, banned phrases and hedged Absence wording are deterministic. The 100% bar is realistic only if they're code.
  - A Call Prep Sheet isn't a Draft, so AD-7 doesn't bind it.
- **Fix:**
  - Add a pure `domain.grounding_rules` module, parameterised by the Playbook, that runs before the AI pass.
  - Extend AD-7 to CallPrep.

### L8. The ports list doesn't match AD-9's one-port-per-system rule

- **PRD:**
  - FR-7 (ATS);
  - FR-15 (verification);
  - FR-38 (Postmaster);
  - FR-10 (renderer);
  - §5.3 (off-PC destination);
  - FR-60 (aggregator).
- **Spine:** the source-tree ports list; `adapters/ats/` has no port.
- **Fix:**
  - Add JobBoard, EmailVerifier (or state that EmailFinder covers verification), Postmaster, Renderer and BackupTarget ports.
  - List Aggregator as deferred.

### L9. Telegram is send-only, but FR-48 has the owner mark Tasks from his phone

- **PRD:** FR-48 (L483): the owner or Operator marks each Task and can log a found URL; §5.4 (L590).
- **Spine:** memlog: Telegram is a plain POST `sendMessage`.
- **Gap:** Furqaan has no dashboard away from the PC, so he can't mark Tasks.
- **Fix:** in Slice 3, either:
  - poll `getUpdates` for inline-button callbacks, which is outbound-only with no listening port, owned by `pipeline`;
  - state that only the Operator marks Tasks.

### L10. Other PRD entities missing from the ownership map

- **PRD:**
  - FR-4: Sequence and Step briefs (L141);
  - Offer (L98, FR-3);
  - FR-22: Segment-level fallback template (L296);
  - FR-7: job postings with live and closed history (L211);
  - FR-61: changed-page detection (L217);
  - FR-42: dashboard notification (L417);
  - FR-66: data subject request flag (L437);
  - FR-31: away dates;
  - FR-43: Sender availability.
- **Spine:** ownership map.
- **Gap:** each needs a table and an owner. Job postings and page hashes drive FR-11 liveness, FR-31's pre-send check and FR-61's AI-cost saving.
- **Fix:**
  - `setup`: Sequence, StepBrief, Offer, FallbackTemplate.
  - `research`: JobPosting {url, title, posted_at, first_seen, status history} and CrawledPage {url, content_hash, fetched_at}.
  - `replies`: Notification.
  - `compliance`: DataSubjectRequest.
  - `settings`: away dates and Sender availability.

### L11. The Worker's GET behaviour is undefined

- **PRD:**
  - FR-70 (L507): "returns success with no further step";
  - addendum §2 (L27): "plus a GET for clients that open the URL".
- **Spine:** AD-15; memlog ("returning 200 (no redirect)").
- **Gap:**
  - If a GET records an opt-out, corporate link scanners that prefetch URLs will unsubscribe people who never clicked.
  - If a GET does nothing, a person who opens the link isn't unsubscribed.
- **Fix:**
  - AD-15: the RFC 8058 POST records the opt-out at once.
  - A GET returns a one-button page that POSTs.
  - Both store only the token and the time.

---

## Coverage checks

### FR → module

All 73 FRs appear in some capability-map row, so no FR is unmapped. These are mapped to a module that can't deliver them alone:

| FR | Mapped to | Problem | Finding |
|---|---|---|---|
| FR-49 | 4.11 `app.pipeline`, `web` | It binds the crawler and every HTTP adapter | H9 |
| FR-38 | 4.8 `app.sending`, `adapters.gmail` | No Postmaster adapter; DNSBL is in `adapters.dns`, mapped under 4.4 | M9 |
| FR-10 | 4.3 `adapters.crawler` | No renderer | M17 |
| FR-42, FR-68 (email) | 4.9, 4.10 | No internal mail path | M18 |
| FR-54, FR-55 | 4.13, read models over `events` | Needed facts aren't in `events` | H12 |
| FR-62 | 4.5 `app.scoring` | The ownership map says `research` | M14 |
| FR-73 | 4.15 `tests/ai_sets` | The engine must run it; no prod writer | H11 |
| FR-57 | 4.14 `app.setup`, `jobs` | Meters live in adapters, which AD-1 bars from the DB | M2, H3 |
| FR-24, FR-47 | 4.6, 4.10 | No Channel Permission owner | M22 |
| FR-44 | 4.9 `app.replies` | Retry conflicts with AD-5; no Bounce owner | M11 |
| §5.3 | `scripts/`, `main.py` | Out-of-process; no restore test | H6, H7 |

### Glossary entity → owner

| Glossary entity | Owner in map | Status |
|---|---|---|
| Segment, Market, Campaign | `setup` | Owned; seed-once problem (M21) |
| Variant / Variant assignment | `setup` owns Variant; assignment unowned and absent from the ER diagram | M13 |
| Sequence, Step briefs, Offer | none | L10 |
| Lead | `discovery`, `research`, `scoring`, `pipeline` | Multi-writer (M1) |
| Contact | `research`, plus repliers and referrals created in `replies` | Multi-writer (M1) |
| Signal, Evidence, Provenance Record | `research` | Owned; type and source mixed (L6) |
| Lead Tier | `research` in the ownership map, `scoring` in the capability map | M14 |
| Fit, Signal and Lead Score | `scoring` | Owned; Slice 1 needs them (M14) |
| Address Class | none | M15 |
| Eligibility | `scoring` snapshot plus `domain.eligibility` | Owned |
| Draft, Reply Draft | `drafting` and `approval` | Multi-writer (M1) |
| Sales Playbook | file (AD-12) | No snapshots (H11) |
| Approved Company Facts | `playbook` | Owned; not versioned or scoped (M16) |
| AI Test Sets | only `ai_test_runs` | Sets and override intake unowned (H10, H11) |
| Sending Domain | none | M9 |
| Sending Inbox (and warm-up record) | `sending`: status and caps only | M9 |
| Daily Cap, Warm-up Ramp, Send Window | `domain.sending_policy` | Owned; no day boundary (L5) |
| Reply, Reply Class, Manual Reply | `replies` | Owned; race with own sends (M12) |
| Bounce | none | M11 |
| Hot Lead alerts and notifications | none | M18, L10 |
| Pipeline Stage, Call Outcome, Call Prep Sheet | `pipeline` | Owned; Call Prep email (M18), grounding (L7) |
| Suppression List | `compliance` | Owned; check points (M8), reversal (L3), restore (H8) |
| Unsubscribe Endpoint tokens | none | M7 |
| Signal Inbox items | `discovery` | Owned; 15-minute path (H4) |
| LinkedIn Task | `pipeline` | Owned; account and invite cap (M22), phone marking (L9) |
| Channel Permission | none | M22 |
| Data subject request | `compliance` handles erasure and export; the flag is unowned | L10 |

### Numbers

| PRD number | PRD location | Spine | Status |
|---|---|---|---|
| Reply and Sent-folder poll at least every 2 minutes | FR-39 L402 | absent | Not bound (M19) |
| Unsubscribe poll every 5 minutes; alert after 24 hours of failures | FR-70 L508, L510 | memlog only | Not bound; KV quota (review-verified M3) |
| Hot alert within 5 minutes; "draft to follow" at 2 minutes | FR-42 L417, L419 | absent; no job priorities | At risk (M5) |
| Signal Inbox Step 1 in the queue within 15 minutes | FR-8 L168 | AD-8 batches the needed routes | Contradicted (H4) |
| Reply Drafts exempt from Cap, gap and Window | FR-34 L371 | AD-5 applies Window and Cap to every send | Contradicted (H2) |
| Places: name and website held until the site replaces them | FR-5 L159 | AD-9: `place_id` and coordinates only | Contradicted (H5) |
| AI budget $20 default, alert at 80% | FR-57 L531 | AD-8 rule without numbers; memlog estimate $12–20 | No headroom (M20) |
| Budget stops only discovery, enrichment and re-checks | FR-57 L532–533 | AD-8 blocks every route except two | Contradicted (H3) |
| Hunter 1 credit per find, 0.5 per verification | FR-57 L531 | AD-9 | Consistent |
| Hunter Domain Search 1 credit per domain | addendum §2 L26 | absent | Gap (M19) |
| Places Pro and Enterprise metered separately | FR-57 L531 | absent | Gap (M19) |
| Approval expiry 3 days | FR-31 L342 | AD-6 | Consistent |
| 14-day warm-up gate; cap starts at warm level, +5 a week to 30 | FR-33 L363–364 | AD-11 names the concepts | Consistent (values belong in settings) |
| Total daily ceiling 50, including warm-up-tool sends | FR-33 L365 | absent | Gap (L5, M19) |
| Bounce thresholds 2% / 3% with at least 50 sends; spam 0.1% / 0.3% | FR-38 L392–395 | absent; no Postmaster adapter | Gap (M9) |
| Retention 6 months (never emailed) and 12 months (no engagement); suppression kept | FR-53 L503 | no numbers; AD-4 exempts only FR-52 | Gap (M6) |
| Daily backup, a second location, encrypted off-PC copy, monthly test restore | §5.3 L585 | "nightly", backups in the data dir, no restore test | Contradicted or missing (H6, H7, L2) |
| Crawl up to 15 pages; at least 1 second per domain | FR-10 L189–190 | AD-9 (1 second) | Consistent |
| Event decay: Tier 1 at 30 / 90 days, Tier 2 at 90 days | FR-11 L197 | no recompute job | Gap (M14) |
| 10 Tasks a weekday; at most 100 invitations per rolling week | FR-48 L479, L482 | absent | Gap (M22, M19) |
| Variant comparisons need 150 contacted Contacts each | FR-54 L519 | no `variant_id` on Enrollment | Gap (M13) |
| Quote Evidence up to 300 characters | glossary L64 | absent | Gap (L6) |
| Test-set bars: 100% / 95% grounding; 100% unsubscribe recall and 90% overall | FR-73 L557–558 | AD-8 references the sets | Consistent; no writer (H11) |

---

## Cross-references to `review-verified.md` (not counted here)

| review-verified item | Relation to this review |
|---|---|
| H1 (`fallbacks` rejected in batches) | Separate. It reinforces H4's case for direct calls on latency-bound paths. |
| H2 (Gmail search by header) | Separate. M4 and M12 assume its fix: Sent-folder listing plus a review state. |
| M1 (no batch latency or expiry handling) | Underlies H4 and M23. |
| M3 (KV list and delete quotas) | Underlies M19 and the Worker change in H8. |
| M4 (Task Scheduler recipe) | Underlies L1. |
| L6 (batch data kept 29 days) | Folded into M6's retention scope. |
| L7 (best-effort batch caching) | Folded into M20. |
| L9 (keyring limit in characters) | Spine-internal: AD-12 says "under 2,560 bytes" while the memlog says "1,280 chars". Not a PRD conflict, but AD-12 should be corrected. |
