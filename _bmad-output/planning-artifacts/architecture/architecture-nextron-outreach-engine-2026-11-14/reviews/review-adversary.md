---
title: 'Adversarial review: Architecture Spine, Nextron Outreach Engine'
target: ../ARCHITECTURE-SPINE.md (status draft, 2026-11-14)
context: ../.memlog.md; prds/prd-nextron-outreach-engine-2026-11-14/prd.md §3, §4
lens: adversary (pairs of units that obey every AD to the letter and still build incompatibly)
date: 2026-11-14
---

# Adversarial Review: Architecture Spine

## Verdict

**Not build-ready for parallel, independent AI builds (FAIL as a build substrate).**

The ADs pin down the *inside* of each unit well: layers, one send path, approval bound to text, evidence-first, one AI gateway, durable jobs. They leave the *seams between units* open:

- The spine contradicts itself in ways that make "obey every AD" impossible. AD-1 forbids the app-to-app calls that AD-3 requires. Adapters "own" tables they can't import. The job runner owns a table it can't touch. Lead Tier has two owners.
- The AD-5 send gate is a closed list of six checks. It omits most of the PRD's stop conditions: Enrollment stopped, reply on the thread, Manual Reply, paused inbox, expired approval, blank footer.
- Several things every unit depends on have no contract: Threads, "due" times, Enrollment and Draft status vocabularies, Bounce, batch write-back, Evidence mutation, and the key used to tell the engine's own Sent-folder messages from Manual Replies.

This review found **46 holes: 9 Critical, 24 High, 11 Medium, 2 Low.** Closing them needs 9 new ADs (AD-16 to AD-24), tightened text in 13 existing ADs, and an executable owner map. Five of the Critical holes can send email that must not go out: a duplicate, a message after an opt-out, unapproved text, or a message from a paused inbox.

## Method

- **Unit pairs.** For every entity with more than one writer and every flow that crosses owners (approve → send → thread → reply → stop → stage), I took two units one level down (an epic or story), each built by a different agent.
- **Literal builds.** I wrote down what each agent would build if it followed the AD text literally and filled the gaps with the most natural reading.
- **Filter.** A hole counts only if both builds are defensible under the ADs and the combination fails.
- **Facts to verify.** Facts about external APIs (Gmail, Anthropic Batches) are marked **[verify]** where a fix depends on them.

**Severity:**

| Level | Meaning |
|---|---|
| **Critical** | Can send email that must not go out (duplicate, after opt-out, unapproved text, from a paused inbox), breach a legal duty, or stall the whole pipeline |
| **High** | Breaks a core FR or UJ, or corrupts state or metrics, in normal operation |
| **Medium** | Inconsistent behavior or a dead feature on an edge path, recoverable by hand |
| **Low** | Friction or rework between units; caught at integration |

## Spine-internal contradictions (no unit can obey every AD)

| # | Contradiction | Hole |
|---|---|---|
| C1 | AD-1 table: `app` may import only `domain, ports, db`. AD-3: non-owners "call the owner's methods" | H-01 |
| C2 | AD-1: adapters may not import `db`. The owner map: "adapters (meters) → api_usage, ai_usage". AD-9: each adapter "records the call in api_usage" | H-06 |
| C3 | AD-1: `jobs` never imports `db`. The owner map: "`nextron/jobs` (runner) → jobs" | H-02 |
| C4 | AD-1: domain imports stdlib only. AD-11: `domain.sending_policy` "takes a `Clock` port" | H-46 |
| C5 | AD-3: "each entity has exactly one owning application service". The map splits Lead four ways, Draft two ways and Enrollment two ways | H-05, H-17 |
| C6 | The owner map gives Lead Tier to `research`. The capability map puts FR-62 in `app.scoring` | H-05 |
| C7 | AD-7 Evidence types `quote, absence, count, manual, registry`. PRD §3 types are Quote, Absence and Count; FR-8 uses `manual` as a *source* | H-35 |
| C8 | AD-15: Telegram carries "Lead name, Reply text and the Draft, nothing else". FR-42 also requires Contact, Signal, booking link, slots and Sending Inbox | H-39 |
| C9 | AD-9: Places adapters "return only `place_id`" plus coordinates. FR-5 needs the website URL to crawl | H-40 |
| C10 | AD-5's gate applies Send Window, Daily Cap and Eligibility to every send. FR-34 exempts Reply Drafts | H-12 |
| C11 | AD-8: "Batchable routes run through the Message Batches API". FR-8 requires a Step 1 Draft within 15 minutes | H-37 |
| C12 | The ER has `DRAFT ||--o| OUTBOUND_MESSAGE` (at most one). FR-31 re-approval after expiry needs a second | H-07 |
| C13 | AD-8: activation is recorded in `ai_test_runs` (DB). The gateway is an adapter and may not read the DB | H-38 |
| C14 | AD-5 (only `app.sending` sends), AD-6 (every send needs an approved Draft) and AD-9 (one adapter per system) together leave no legal path for FR-42 alert emails or FR-68 | H-39 |

## Summary of holes

| ID | Hole | Units in conflict | Severity | Closed by |
|---|---|---|---|---|
| H-01 | Cross-owner calls are banned by AD-1 and required by AD-3; nested transactions self-deadlock | replies × pipeline/compliance (all owners) | Critical | AD-16 new; AD-1 |
| H-02 | Nobody may legally write `jobs`; no rule says who enqueues the next stage | research × scoring; runner | Critical | AD-10 |
| H-03 | Idempotency-key scope and payload shape | triggers × app enqueuers | High | AD-10 |
| H-04 | Runner concurrency, event loop, per-inbox serialization, leases | runner × web × sending | High | AD-10; AD-2 |
| H-05 | Ownership per entity versus per column; Lead Tier has two owners | research × scoring | High | AD-3 |
| H-06 | Metering location; which calls a quota or budget may block | adapters × db; budget × replies/approval | High | AD-8; AD-9 |
| H-07 | Who creates the OutboundMessage, and how many per Draft | approval × sending | Critical | AD-6 |
| H-08 | The send gate is a closed list that omits the stop conditions | replies × sending | Critical | AD-5 |
| H-09 | Hash canonicalization, footer injection, OM text copy | approval × sending | Critical | AD-6 |
| H-10 | Grounding not bound to the text revision; owner of edits | drafting × approval | High | AD-6 |
| H-11 | Approval expiry versus a queued OM; what "3 days" means | approval × sending | High | AD-6; AD-11 |
| H-12 | No message kinds: Reply Drafts are gated like cold Steps | replies × sending | High | AD-19 new |
| H-13 | `sending` rows are reconciled only at startup | runner retry × sending | Critical | AD-5 |
| H-14 | Dispatch pacing and priority; burst after downtime | sending × scoring | High | AD-11 |
| H-15 | Sending Inbox bound at enrollment versus at send | scoring × sending | Medium | AD-17 new |
| H-16 | Job-posting recheck before Step 1 has no home | sending × research | Medium | AD-5 |
| H-17 | Enrollment lifecycle has no single owner | scoring × replies (+approval, pipeline, compliance, sending) | High | AD-17 new |
| H-18 | Three definitions of "due" | drafting × approval × sending | High | AD-17 new; AD-11 |
| H-19 | Draft status versus flags; who moves which status | drafting × approval × replies | High | AD-4 |
| H-20 | Who creates non-Step Drafts and grounded artifacts | replies/pipeline × drafting | Medium | AD-19 new |
| H-21 | Where the Not Now date lives; what a re-contact is | replies × research × scoring | Medium | AD-17 new |
| H-22 | No Thread entity; Reply matching keys undefined | sending × replies | High | AD-18 new |
| H-23 | Engine's own in-flight sends are logged as Manual Replies | replies × sending | Critical | AD-18 new |
| H-24 | Stops wait for AI classification; opt-outs don't halt the next Step | replies × sending/sequencing | Critical | AD-18 new |
| H-25 | Manual Reply versus stop rules, Pipeline Stage and startup order | replies × pipeline × runner | High | AD-18 new |
| H-26 | Bounce has no owner; temporary-bounce retry versus exactly-once | replies × sending × research | High | AD-18 new |
| H-27 | Strict Pipeline Stage machine versus automatic moves | pipeline × sending/replies/approval | High | AD-4 |
| H-28 | Three definitions of Hot Lead | replies × approval × pipeline | Medium | AD-18 new |
| H-29 | Cross-channel contactability; nobody owns the opt-out mark | compliance/replies × pipeline/drafting | Critical | AD-20 new |
| H-30 | Email and domain normalization for suppression and dedupe | compliance × discovery × replies | High | AD-20 new; Conventions |
| H-31 | Unsubscribe token owner; ack before commit | unsubscribe adapter × compliance × sending | High | AD-15 |
| H-32 | Erasure and retention versus other owners' rows and cascades | compliance × all owners | High | AD-23 new |
| H-33 | Stored Eligibility snapshot versus live Eligibility | scoring × approval × gate × research | Medium | AD-20 new |
| H-34 | Re-enrichment mutates Evidence that Drafts cite | research/playbook × drafting/approval | High | AD-21 new |
| H-35 | Evidence type versus source; discovery writes Evidence | discovery × research × drafting | High | AD-7 |
| H-36 | Batch write-back and the synchronous `LlmPort` contract | AI gateway × drafting/research | High | AD-22 new |
| H-37 | `batchable` is set per route, not per call | discovery/drafting × routes | High | AD-22 new; AD-8 |
| H-38 | Two sources of truth for the active route and Playbook version | AI gateway × playbook | High | AD-22 new |
| H-39 | No legal path for internal emails; alert content conflict | replies/pipeline × sending/telegram | High | AD-24 new; AD-15 |
| H-40 | Places adapter returns nothing discovery can use | places adapter × discovery | Medium | AD-9 |
| H-41 | Lead merge must re-parent rows that discovery doesn't own | discovery × research/sequencing | Medium | AD-3 |
| H-42 | Null Contact timezone or Market | research × sending_policy | Medium | AD-11 |
| H-43 | Parallel Alembic heads; schema concepts duplicated | any two schema stories | Medium | AD-2; AD-3 |
| H-44 | No settings key registry | setup × consumers | Low | AD-12 |
| H-45 | `events` vocabulary too loose for analytics | all owners × analytics | Medium | AD-4 |
| H-46 | Domain signatures: `Clock` port versus `now` | domain × app | Low | AD-1; AD-11 |

---

## A. Substrate: cross-service mechanics

### H-01: AD-1 forbids the cross-owner calls that AD-3 requires · Critical

- **Units:** *Reply handling* (`app.replies`), which must stop the Enrollment, suppress the address and move the Pipeline Stage, × *Pipeline* (`app.pipeline`) and *Compliance* (`app.compliance`). The same conflict applies to every pair where one owner changes another owner's entity.
- **How they diverge:**
  - **Agent A (replies)** reads AD-3, "call the owner's methods", and imports `nextron.app.pipeline`. It argues that the AD-1 table doesn't forbid imports within the same layer.
  - **Agent B (compliance)** reads the AD-1 table literally (`app` imports `domain, ports, db` only). To stop a Sequence it writes a `jobs` row, or writes an `events` row and expects the owner to react.
  - **Transactions.** Agent A's `pipeline.move()` opens its own unit of work with `BEGIN IMMEDIATE` while replies' transaction is open. On the same connection, sqlite3 raises "cannot start a transaction within a transaction". On a second connection, it waits on the RESERVED lock its own caller holds until `busy_timeout` (5 s) runs out, then fails with "database is locked".
- **Result:** Either every cross-entity rule (stop rules, suppression, stage moves) self-deadlocks, or it becomes eventual and non-atomic. In the eventual case, the Reply commits, the stop lands minutes later, and a Step can go out in between (see H-08 and H-24).
- **Fix: add AD-16 (new) and tighten AD-1.**

> **AD-16: Cross-owner effects share the caller's transaction**
> - **Binds:** every `nextron/app` service.
> - **Rule:**
>   - An app service may import another service's `nextron/app/<owner>/commands.py` (writes) and `queries.py` (reads), and nothing else of it.
>   - Command edges follow this acyclic graph, which the import-linter enforces:
>     - `approval → {sending}`
>     - `sequencing → {approval, sending, research}`
>     - `pipeline → {sequencing}`
>     - `compliance → {sequencing, pipeline}`
>     - `scoring → {sequencing, pipeline}`
>     - `discovery → {research}`
>     - `replies → {compliance, sequencing, pipeline, approval, drafting, research, sending}`
>     - `sending`, `research`, `drafting`, `playbook` and `setup` call no other service.
>   - Owner commands take the caller's open `uow` as their first argument. They never begin, commit or roll back. Only the outermost entry point (a job handler's or web route's single service call) owns the transaction.
>   - **Notifications for reverse effects.** Effects that would point backwards in the graph are *notifications*. The owner calls `uow.notify(<Event>)`. Handlers are registered only in `main.py`, run before commit in the same transaction, and are owner commands themselves. Registered notifications:
>     - `MessageSent` → `approval.on_sent`, `sequencing.on_step_sent`, `pipeline.advance_to_at_least(Contacted)`
>     - `Step1Rejected` → `sequencing.stop`
>     - `BadFitRejected` → `pipeline.close(Disqualified)`
>     - `Enrolled` → `pipeline.advance_to_at_least(Queued)`
>     - `EventChanged` → `scoring.rescore`
>     - `ApprovalLapsedAtGate` → `approval.expire`
>   - A command that finds nothing to do returns a no-op result. It raises only on a real invariant violation, which rolls back the whole unit.
>   - The `events` table (AD-4) is an audit log only. No code reads it to trigger behavior.
>
> **AD-1 (tighten), Application row:** "May import: domain, ports, db repositories of its own column groups (AD-3), and other app services' `commands`/`queries` modules along the AD-16 graph."

### H-02: Nobody may legally write `jobs`, and nothing says who enqueues the next stage · Critical

- **Units:** *Enrich Lead* (`app.research` with handler `enrich_lead`) × *Score Lead* (`app.scoring` with handler `score_lead`), plus the runner (`nextron/jobs`).
- **How they diverge:**
  - The paradigm says each stage "is a durable job that reads state … and writes state back", and AD-10 says handlers call one app service. Neither says who enqueues stage N+1.
  - **Agent A (research)** finishes enrichment and returns. It expects a periodic trigger to scan for Leads that are enriched but not scored.
  - **Agent B (scoring)** registers no trigger. It expects `enrich_lead` to enqueue `score_lead`.
  - **The table itself.** The owner map gives `jobs` to the runner, but AD-1 says `jobs` never imports `db`, and `app` can't import `jobs` (a delivery layer). Each agent breaks a different rule to write the table.
- **Result:** Leads stall between stages with no error, or get processed twice (once by a chain, once by a scan). FR-8's 15-minute chain (intake → enrich → score → enroll → draft) has no defined driver. UJ-3 fails.
- **Fix: tighten AD-10.**

> - The `jobs` table is owned by `nextron/app/jobqueue` (application layer). `jobqueue.enqueue(uow, type, payload, key, run_after)` is the only way to create a job. It is called inside the transaction whose state change makes the work necessary (transactional outbox). The runner in `nextron/jobs` claims, renews, completes and fails jobs only through `jobqueue` commands.
> - **Chained stages.** The service that finishes stage N for an entity enqueues stage N+1 for that entity in the same transaction:
>   - `ingest_signal_item → enrich_lead → score_lead → (sequencing.enroll) → draft_enrollment`
>   - `poll_inbox → classify_reply → draft_reply`
>   - `approve → (OM queued) → send_due_messages`
> - **Periodic triggers** exist only for time-driven work. They are listed here and nowhere else:
>
>   | Trigger | Interval |
>   |---|---|
>   | `send_due_messages` | 1 min |
>   | `poll_inbox` (per inbox) | 2 min |
>   | `reconcile_sending` | 2 min |
>   | `poll_unsubscribes` | 5 min |
>   | `collect_ai_batches` | 10 min |
>   | `expire_approvals` | 15 min |
>   | `refresh_due_queue` | 15 min |
>   | `sweep_stuck` | 15 min |
>   | `daily_selection` | 05:00, Operator's timezone |
>   | `recheck_leads` | daily |
>   | `recontact_due` | daily |
>   | `expire_events` | daily |
>   | `check_domain_auth` | daily |
>   | `retention_sweep` | daily |
>   | `backup` | nightly |
>
>   `sweep_stuck` re-enqueues any entity that has sat in an intermediate state for more than 30 minutes with no pending job.

### H-03: Idempotency-key scope and payload shape · High

- **Units:** scheduler triggers (`nextron/jobs/triggers`) × any app service that enqueues jobs.
- **How they diverge:**
  - AD-10 says only "`idempotency_key (unique)`".
  - **Agent A** enqueues `send_due_messages` with the constant key `send_due_messages` and uses `INSERT OR IGNORE`. Once the first run is `done`, every later enqueue is silently dropped, so sending runs once in the life of the database.
  - **Agent B** treats keys as unique among live jobs only, using a partial index.
  - **Payloads.** Agent A puts entity snapshots in `payload` (address, body), so personal data sits in `jobs` where FR-52 erasure never looks. Agent B puts only ids.
- **Result:** Periodic work stops after one run, or gets duplicated. Personal data outlives erasure.
- **Fix: tighten AD-10.**

> - `idempotency_key` = `<type>:<scope>:<discriminator>`:
>   - scope: the target ULID, `inbox:<ulid>`, or `global`;
>   - discriminator: the content revision (for example `rev3`) for entity work, or `tick` for periodic work.
> - Uniqueness is a partial unique index over `status IN ('queued','running')`. Rows that are `done` or `dead` never block a new enqueue. Enqueueing a duplicate is a no-op that returns the existing job.
> - `payload` is JSON `{"v": <int>, …}` holding ULIDs and scalars only. It never holds addresses, names, bodies or other personal data.
> - `done` rows are purged after 30 days. `dead` rows stay until the Operator dismisses them on the health panel.

### H-04: Runner concurrency, the event loop, per-inbox serialization and leases · High

- **Units:** runner (`nextron/jobs`) × dashboard (`nextron/web`; FR-29 requires the next card in under 300 ms) × `app.sending` (4–12 minute gaps per inbox).
- **How they diverge:**
  - AD-2 fixes one process but says nothing about threads.
  - **Agent A** runs the runner as an `asyncio` task in the FastAPI lifespan and calls the synchronous handlers inline. A 15-page crawl (at least 1 s per page) or a Gmail call blocks the event loop, so card renders take seconds.
  - **Agent B** uses 4 worker threads. Two concurrent send jobs read the same inbox's `last_sent_at` and both send. AD-5's conditional `queued → sending` update stops the same message going twice, but not two messages from one inbox seconds apart.
  - **Leases.** A crawl with headless rendering outlives a short lease. Another worker re-claims the job, so the crawl and the AI extraction run twice.
- **Result:** The FR-29 and SM-6 latency targets are missed, FR-34's gaps are broken, and FR-10 politeness and AI cost both double.
- **Fix: tighten AD-10 and AD-2.**

> - The runner is a fixed pool of worker threads (default 4), started and stopped by the FastAPI lifespan. Handlers never run on the event loop. Web routes that call app services are plain `def` endpoints, not `async def`.
> - Each job type declares `concurrency_key(payload)`. The claim query skips any job whose key a running job holds. Keys:
>   - sending: `inbox:<id>`
>   - inbox polling: `poll:<inbox id>`
>   - crawling: `domain:<registrable domain>`
> - Handlers renew `lease_until` every 30 s. A job can be re-claimed only after its lease has gone unrenewed for 2 minutes.
> - **AD-2 addition:** at startup the process takes an exclusive OS file lock on `<data dir>\nextron.lock`. A second process (for example from `--reload` or a duplicate logon task) exits.

### H-05: AD-3 assigns owners per entity, but the map splits entities; Lead Tier has two owners · High

- **Units:** `app.research` (the owner map gives it "Lead attributes and Tier") × `app.scoring` (the capability map puts FR-62 there).
- **How they diverge:**
  - AD-3 says each entity has exactly one owner, yet the map splits Lead across `discovery`, `research`, `scoring` and `pipeline`, and Draft across `drafting` and `approval`.
  - **Agent research** recomputes Tier whenever it stores an Event, and ages Events from `event_date`.
  - **Agent scoring** recomputes Tier in its rescoring and on expiry, and ages Events from `first_seen`. FR-11 allows either.
  - **Persistence.** A persistence agent reading AD-3 per entity gives the Lead repository a single writer.
- **Result:** Tier flips depending on which service wrote last. FR-19 selection, FR-26 queue order and the review card disagree about the same Lead.
- **Fix: tighten AD-3.**

> - Ownership is per **column group**. For each table that more than one service writes, the map names the column groups:
>   - **Lead.** `discovery` owns identity: `name, web_domain, mail_domains, ch_number, place_id, segment_id, market_id, merged_into`. `research` owns the profile: size clues, locations, contact methods, legal form. `scoring` owns `fit_score, signal_score, lead_score, tier, tier_computed_at`. `pipeline` owns `pipeline_stage, recontact_on, deal_value, notes`.
>   - **Draft.** `drafting` owns row creation, revisions and grounding. `approval` owns `status` and the approval columns.
>   - **SendingInbox.** `sending` owns everything, including the warm-up record and auth results. `replies` owns the separate `inbox_sync_state` table (history cursor).
> - Lead Tier is computed only by `domain.scoring.tier(events, now)` and written only by `app.scoring`. Remove "Tier" from research's row. Event age comes from one function, `domain.signals.age_days`, which counts from `event_date` when known, otherwise from `first_seen_at`.
> - The map is executable. `nextron/db/owners.py` maps `(table, column_group) → owner`. Write repositories live in `nextron/db/repos/<owner>/`, and the import-linter lets only that owner import them. A test fails if any model column is missing from the map.

### H-06: Where metering lives, and which calls a quota or budget may block · High

- **Units:** adapters (the map gives them `api_usage` and `ai_usage`; AD-9 says they record usage and check quota) × `db`. Separately, the budget policy (`app.playbook`, AD-8) × `app.replies` and `app.approval`.
- **How they diverge:**
  - **Writing usage.** AD-1 bars adapters from importing `db`. Agent gmail adds a `db` import anyway, so the import-linter fails or gets loosened. Agent hunter returns usage to the app service, which then writes `api_usage`, giving the table two owners.
  - **Gmail quota.** Under AD-9, every adapter checks its quota before calling out. The Gmail adapter therefore blocks sends when it hits its own limit, which FR-57 forbids ("never blocks sending, reply handling, or unsubscribe polling").
  - **AI budget.** AD-8 lets the budget block every route except `classify_reply` and `draft_reply`. At month end, `grounding_check` is blocked, so a Hot Lead's Reply Draft never gets a grounding pass and can't be approved (FR-21, FR-43). `signal_inbox_parse` is blocked too, which stops UJ-3.
- **Result:** The layer rule breaks, and essential paths stall when budget or quota runs out.
- **Fix: tighten AD-9 and AD-8.**

> - **Metering.** It goes through a `UsageMeterPort` in `ports`, implemented in `db.metering` and injected into adapters by `main.py`. Adapters never import `db`. `db.metering` is the only writer of `api_usage` and `ai_usage`.
> - **Call classes.** Every outbound call carries a `call_class` chosen by the calling app service:
>   - `essential`: all Gmail calls, unsubscribe polling, Telegram and other alert calls, `classify_reply`, `draft_reply`, and `grounding_check` for reply-kind Drafts and for Operator edits. Always metered, never blocked by a quota or the budget.
>   - `operator`: work the Operator triggers (regenerate, the Signal Inbox chain, call prep). Blocked only at 100% of quota or budget.
>   - `bulk`: discovery, enrichment, re-checks, nightly drafting. Warned at 80%, blocked at 100%.
> - **Deciding.** The app service calls `domain.budget.allow(call_class, usage_snapshot)` before calling the port. Adapters enforce only hard provider limits (HTTP 429) and report them as `AdapterError(retryable=True)`.

---

## B. Send path and approval binding

### H-07: Who creates the OutboundMessage, and how many per Draft · Critical

- **Units:** *Approve / batch approve* (`app.approval`) × *Send due messages* (`app.sending`).
- **How they diverge:**
  - AD-6 says an OM "can be created only from a Draft in status `approved`". It doesn't say when, or by whom.
  - **Agent A** has `approval.approve()` call `sending.enqueue(draft)`, so the OM is `queued` at approval.
  - **Agent B** has `send_due_messages` select approved Drafts that have no sent OM and create the OM just in time, since it checks the Send Window then anyway.
  - **Both run.** One Draft gets two OMs, with two ULIDs and two `X-Nextron-Message-Id` values. AD-5's exactly-once guarantee is per OM row, so both go out.
  - **Re-approval.** The ER allows at most one OM per Draft, but FR-31 re-approval needs a second. Agent A revives the expired OM; Agent B creates another.
- **Result:** A prospect gets the same cold email twice (breaks FR-35 and UJ-5).
- **Fix: tighten AD-6.**

> - **One creation path.** Only `approval.approve(uow, draft_id, rev)` creates an OutboundMessage. In one transaction it inserts an `approvals` row (`approval_id, draft_id, rev, text_hash, approved_by, approved_at, review_ms`) and calls `sending.commands.enqueue(uow, approval_id)`. That call inserts exactly one OM in `queued`, carrying a copy of the approved text (H-09). Sending never reads Drafts to find work; it reads only OMs.
> - **Constraints.** `outbound_messages.approval_id` is UNIQUE. A partial UNIQUE index on `draft_id` covers `status IN ('queued','sending','sent')`. Over time a Draft has 0..n OMs, and at most one is live.
> - **Cancelling.** Anything that un-approves a Draft (edit, expiry, supersede, stop, Event expired) calls `sending.commands.cancel(uow, approval_id, cause)` in the same transaction. From `queued`, cancel succeeds. On a canceled OM it is a no-op. On `sending` or `sent` it raises, so the un-approve fails and the card says "already sending".
> - **ER update:** `DRAFT ||--o{ OUTBOUND_MESSAGE`, `APPROVAL ||--|| OUTBOUND_MESSAGE`.

### H-08: The final gate is a closed list that leaves out the stop conditions · Critical

- **Units:** *Replies and stop rules* (`app.replies`) × *Sending* (`app.sending`, calling `compliance.assert_sendable`).
- **How they diverge:**
  - AD-5 lists what the gate checks: Suppression List, Eligibility, Send Window, Daily Cap, warm-up and domain authentication. A literal sending agent checks exactly those six. The list leaves out:
    - the Enrollment is still active (FR-37 stop rules);
    - no newer inbound Reply on the thread;
    - no Manual Reply on the thread (FR-43 requires a re-check before sending an approved Reply Draft);
    - the Draft is still approved and its approval hasn't expired (FR-31);
    - the inbox isn't paused: FR-32 manual pause, or FR-38 auto-pause on bounce rate, policy Bounce, blocklisting or spam rate;
    - the footer fields are filled in (FR-23: no email while the legal name, postal address or UK privacy link is blank);
    - the Campaign isn't paused;
    - the cited job posting is fresh (FR-31);
    - the approved text hash still matches (AD-6).
  - The replies agent, for its part, relies on "compliance re-checks right before each send" and implements a stop as a status change on the Enrollment only.
- **Result:** Steps go out after a reply, from a paused inbox, after Furqaan has already answered, or with a blank postal address. That's a CAN-SPAM and PECR exposure as well as a deliverability one.
- **Fix: tighten AD-5 by replacing flow step 1.**

> 1. In one transaction, `compliance.assert_sendable(om, now)` evaluates every predicate in `domain.send_gate.PREDICATES`. The status moves `queued → sending` only if all of them pass. The list is closed here and nowhere else: adding a stop condition anywhere in the PRD means adding a predicate here, with a test. "Kind" refers to the message kinds in AD-19.
>
>    | Predicate | Applies to | Checks | On failure |
>    |---|---|---|---|
>    | `not_suppressed` | all | address and domain not on the Suppression List | terminal |
>    | `contactable` | all | AD-20 contactability | terminal |
>    | `cold_eligible` | step, recontact | live Eligibility (FR-18) | terminal |
>    | `responsive_eligible` | reply, rebook | responsive Eligibility (AD-19) | terminal |
>    | `enrollment_active` | step, recontact | Enrollment status is `active` | terminal |
>    | `thread_quiet` | all | no Reply or Manual Reply on the Thread newer than the Draft revision; for reply kind, no Manual Reply newer than the Reply being answered | terminal |
>    | `approval_live` | all | Draft still `approved`, this OM's `approval_id` is current, and `sending_policy.approval_expired` is false | terminal |
>    | `text_hash_matches` | all | stored hash equals the hash of the OM's text | terminal |
>    | `footer_complete` | all | FR-23 fields not blank | wait |
>    | `inbox_active` | all | Sending Inbox status is `active` | wait |
>    | `domain_auth_ok` | all | FR-64 checks pass | wait |
>    | `warmup_ok` | step, recontact | FR-33 warm-up met | wait |
>    | `in_send_window` | step, recontact | inside the recipient's Send Window | wait |
>    | `under_daily_cap` | step, recontact | below the inbox's Daily Cap | wait |
>    | `gap_elapsed` | step, recontact | per-inbox gap has passed | wait |
>    | `campaign_active` | step | Campaign not paused | wait |
>    | `cited_event_fresh` | Step 1 citing a job posting | posting checked within 24 h (H-16) | wait |
>
>    "Wait" leaves the OM `queued` with `blocked_by=<predicate>` for the next tick. "Terminal" moves it to `canceled` and calls the owning service to put the Draft back in the queue or cancel it (AD-16 notification).
>
> *[Decision for Mac: `campaign_active` holds already-approved Steps of a paused Campaign. The PRD only says pausing stops new Steps entering the queue.]*

### H-09: Approved-text hash, footer injection and the OM's own copy of the text · Critical

- **Units:** *Approve* (`app.approval`, which computes the SHA-256) × *Compose and send MIME* (`app.sending` and the Gmail adapter).
- **How they diverge:**
  - AD-6 hashes "the approved subject and body", without defining either.
  - **Agent approval** stores the body without the footer (FR-23's "editing can't remove the footer" reads naturally as "append it outside the editable area"). It hashes `subject + body` with no separator.
  - **Agent sending** renders the signature, footer and UK privacy line at send time, since it has the settings, then:
    - if it verifies `sha256(rendered)`, every send fails;
    - if it doesn't verify (AD-6 doesn't require it), it reads `draft.body` by foreign key at send time. If the Operator edits after approval, AD-6 returns the Draft to `ready`, but the queued OM still points at the Draft and sends the edited, unapproved text.
  - **Whitespace.** HTMX textareas submit CRLF line endings, and NFC and trailing whitespace vary, so identical text can produce different hashes.
- **Result:** Either nothing sends, or text nobody approved goes out, which defeats AD-6.
- **Fix: tighten AD-6.**

> - **What is approved.** The approved artifact is the complete message the prospect will see: subject and plain-text body, *including* signature, footer and the UK privacy line. `domain.render.compose(draft_rev, footer_settings)` produces it, and the card shows it exactly as it will be sent. Footer settings are snapshotted into the revision when it is composed.
> - **The hash.** `text_hash = sha256(utf8(canon(subject) + "\n\n" + canon(body)))`, defined once in `domain.render.text_hash`. `canon` means:
>   - Unicode NFC;
>   - `\r\n` and `\r` become `\n`;
>   - trailing whitespace is stripped from each line;
>   - trailing blank lines are stripped.
> - **The OM's copy.** The OM stores its own `subject`, `body` and `text_hash`, copied at enqueue. Sending sends only that copy and re-computes the hash in the gate (`text_hash_matches`).
> - **What sending may add.** Only headers outside the hash: `Message-ID`, `X-Nextron-Message-Id`, `In-Reply-To`, `References`, `List-Unsubscribe`, `List-Unsubscribe-Post`, `Date`, `From` and `To`.

### H-10: Grounding isn't bound to the text revision, and Operator edits have no owner · High

- **Units:** `app.drafting` (owns generation and grounding results; grounding is a Haiku call) × `app.approval` (edit then approve; FR-28 and FR-29 make it one keystroke with a 300 ms next card).
- **How they diverge:**
  - `drafting` owns "Draft (generate/regenerate)" and `approval` owns "Draft status". An Operator edit fits neither.
  - **Agent approval** writes `draft.body` itself on edit-then-approve. It then checks "grounding result for `draft_id` is pass", but that pass is for the pre-edit text, so the edited text is approved without a check.
  - **Agent drafting** re-runs grounding on every edit, asynchronously, which takes seconds.
  - FR-21 says a failed Draft can't be approved until it is edited *and passes a recheck*.
- **Result:** Unchecked text is approved and sent, or the approve keystroke blocks for seconds (SM-6).
- **Fix: tighten AD-6 (with a matching line in AD-7).**

> - **Revisions.** Draft text lives in immutable `draft_revisions(draft_id, rev, subject, body, text_hash, origin ∈ {generated, regenerated, operator_edit}, created_at)`. Only `app.drafting` writes them; Operator edits go through `drafting.commands.revise`.
> - **Grounding per revision.** Grounding results are keyed by `(draft_id, rev, text_hash)`. `approval.approve(draft_id, rev)` succeeds only if a `pass` exists for exactly that revision. Otherwise it returns:
>   - `GroundingPending`: the card shows "checking…" and approves automatically when the pass arrives, if the Operator chose edit-then-approve; or
>   - `GroundingFailed`.
> - An Operator-edit grounding check is `call_class=essential` (H-06) and is never batched.

### H-11: Approval expiry versus a queued OM, and what "3 days" means · High

- **Units:** *Approval expiry and stalls* (`app.approval`, FR-31) × *Send due* (`app.sending`).
- **How they diverge:**
  - AD-6 says "an approval expires after 3 days", and AD-11 puts expiry in `domain.sending_policy`.
  - **Agent approval** runs `expire_approvals`. It moves Drafts `approved → ready` after 3 calendar days in the Operator's timezone and leaves OMs alone, since they belong to sending.
  - **Agent sending** follows AD-11: if `approval_expired(approved_at, now)` (72 hours wall clock) is true, it marks the OM `expired`. It doesn't touch the Draft, which belongs to approval.
- **Result:** Two failures:
  - Approval sees an OM as expired while sending sees it as live (calendar days versus 72 hours). It goes out while the same Draft sits in the queue; Mac re-approves, and H-07 creates a second OM.
  - An OM that sending expired never returns its Draft to the queue, so the follow-up quietly disappears.
- **Fix: tighten AD-6 and AD-11.**

> - `domain.sending_policy.approval_expired(approved_at, now) := now >= approved_at + 72h` (UTC wall clock). There is no other definition.
> - `approval.commands.expire(uow, approval_id)` is the only expiry writer:
>   - it moves the Draft `approved → ready` with `reapproval_required = true`;
>   - it calls `sending.commands.cancel(uow, approval_id, 'approval_expired')`;
>   - if the OM is `sending`, it skips the expiry and retries on the next tick.
> - The `expire_approvals` job drives expiry. The gate predicate `approval_live` evaluates the same function, so nothing slips through between ticks. When `approval_live` fails at the gate, sending emits `ApprovalLapsedAtGate` (AD-16) instead of writing the Draft.

### H-12: No message kinds, so Reply Drafts are gated like cold Steps · High

- **Units:** `app.replies` and `app.drafting` (FR-43 Reply Drafts are "sent at once", outside cap, gap and window) × `app.sending` (AD-5 applies window, cap and Eligibility to every send).
- **How they diverge:**
  - AD-5's gate has no notion of message kind. A literal sending agent:
    - holds a Hot Lead Reply Draft approved at 17:30 until 08:00 the next day;
    - counts it against the Daily Cap;
    - refuses it outright when the prospect wrote from personal webmail (FR-2: UK free-webmail is never Eligible);
    - refuses it when a colleague replied (FR-37 adds them as a Contact, but their address is neither Published nor Verified).
  - The replies agent assumes immediate dispatch.
  - Some kinds have no defined policy at all: the no-show rebooking email (FR-69), the referrer thank-you (FR-67), the Not Now re-contact (FR-63), and objection and data-question replies (FR-66).
- **Result:** UJ-2 and SM-8 break. Interested prospects can't be answered from the engine.
- **Fix: add AD-19 (new).**

> **AD-19: Message kinds and their send policy**
> - **Binds:** Draft, OutboundMessage, the AD-5 gate; FR-34, FR-43, FR-63, FR-66, FR-67, FR-69.
> - **Rule:**
>   - Every Draft and OM carries `kind ∈ {step, recontact, reply, rebook}`. Channel Drafts (LinkedIn, WhatsApp, Instagram) are `kind=channel` and never become OMs.
>   - The gate reads this matrix. Nothing else may branch on kind.
>
>   | kind | Thread | Send Window | Daily Cap and gap | Eligibility test | Enrollment must be active | Counts as a send in FR-54 |
>   |---|---|---|---|---|---|---|
>   | step (Steps 1–4, including a referral Step 1) | new thread for Step 1, same thread after | yes | yes | cold (FR-18) | yes | yes |
>   | recontact (FR-63) | new thread | yes | yes | cold (FR-18) | yes (one-Step Enrollment) | yes |
>   | reply (FR-43, FR-66, FR-67 thank-you) | the Reply's thread | no | no | responsive | no | no |
>   | rebook (FR-69) | the Contact's latest thread | no | gap only | responsive | no | no |
>
>   - **Responsive Eligibility:** the recipient is the From address of the message being answered, or a Contact already on the Thread who has written to us; the address isn't suppressed; and the Contact passes AD-20 contactability. It needs no Address Class, because the prospect proved the address by writing to us.
>   - Reply-kind OMs are sent ahead of all other kinds and never count toward the Daily Cap.

### H-13: `sending` rows are reconciled only at startup · Critical

- **Units:** runner retry policy (AD-10) × `app.sending` (AD-5 reconciles on startup only).
- **How they diverge:**
  - A `MailPort.send` call times out mid-upload while the engine keeps running. FR-35 names network drops explicitly. The handler raises `AdapterError(retryable=True)`, and the runner retries (AD-10).
  - **Agent A**, reasoning "handlers are idempotent, and `sending` means my last attempt was interrupted", calls `MailPort.send` again. The message may go out twice.
  - **Agent B** skips `sending` rows. The OM stays stuck until the next reboot, which may be days away. The Enrollment stalls, and expiry can't cancel an OM in `sending`.
- **Result:** A double send, or a stuck Sequence that needs a reboot to recover (§5.3 requires recovery without manual steps).
- **Fix: tighten AD-5.**

> - **When reconciliation runs.** At startup, *and* every 2 minutes (`reconcile_sending`) for each OM in `sending` whose `send_started_at` is more than 2 minutes old. It is the only code that moves an OM out of `sending` without a provider response:
>   - found in Sent by header → `sent`, with the provider ids recorded;
>   - not found after a fresh listing of that inbox's Sent messages → `queued`, to be gated again as usual.
> - **Claiming a send.** A handler calls `MailPort.send` only after it wins the conditional update `UPDATE … SET status='sending' WHERE id=? AND status='queued'` (rowcount 1).
> - **Errors.** After any error that might follow the request reaching Gmail (timeout, connection reset, 5xx after upload), the handler leaves the OM in `sending` and completes the job without retrying. Only errors proven to happen before sending (auth failure, local validation, a 4xx before upload) may move `sending → queued` directly.
> - **Matching.** Gmail search can't query arbitrary `X-` headers; only `rfc822msgid:` searches `Message-ID` **[verify]**. Reconciliation therefore lists the inbox's Sent messages since `send_started_at − 5 min` with `format=metadata` and reads `X-Nextron-Message-Id`.

### H-14: Dispatch pacing and priority; a burst after downtime · High

- **Units:** `app.sending` scheduler × `app.scoring` (FR-19 capacity forecast).
- **How they diverge:**
  - AD-11 centralizes the window, cap and ramp *values*, but not the dispatch algorithm.
  - **Agent A** gives each OM a `scheduled_at` at enqueue, spread with 4–12 minute gaps across the recipient's window. After the PC sleeps through the window, every overdue `scheduled_at` is due at wake, so they all go at once (UJ-5 forbids a burst).
  - **Agent A again**, sending in `scheduled_at` order, lets Step 1s approved early take the cap. FR-19 had reserved that capacity for follow-ups, so they slip a day.
  - **Agent B** computes the next send at dispatch time.
- **Result:** A burst after wake, which risks reputation, and follow-ups squeezed out by Step 1s (FR-19, FR-31).
- **Fix: tighten AD-11.**

> - Sending never stores a planned send slot. On each `send_due_messages` tick, for each inbox:
>   - If `now < sending_policy.next_send_at(inbox)`, do nothing. `next_send_at` = `last_sent_at` plus a gap drawn uniformly from 4–12 minutes and stored on the inbox at each send. After downtime it is measured from `max(last_sent_at, process_start)`.
>   - Otherwise send one OM that passes the gate, picked in this order:
>     1. reply kind, oldest first;
>     2. follow-ups, highest Step number first, then oldest due;
>     3. Step 1 and recontact, in Approval Queue order (FR-26).
>   - Reply-kind OMs skip the gap (AD-19).
> - `sending_policy.capacity_forecast(inbox, local_date)` is the only capacity function. FR-19 selection uses it with the same rule: follow-ups due that date are reserved first.

### H-15: Sending Inbox bound at enrollment versus at send · Medium

- **Units:** `app.scoring` (creates the Enrollment and needs per-inbox capacity for FR-19) × `app.sending`.
- **How they diverge:**
  - FR-34 requires all of a Contact's Steps to come from the same inbox.
  - **Agent scoring** binds an inbox at enrollment.
  - **Agent sending** picks the least-loaded inbox at Step 1 dispatch and records it on the OM and thread. Follow-ups then find inbox X on the Enrollment and inbox Y on the thread.
  - **Paused inbox.** One agent reassigns the Enrollment, which breaks threading. The other waits forever.
  - **Removed inbox.** Its Enrollments are orphaned.
- **Result:** Broken threading, a wrong capacity forecast and stranded Sequences.
- **Fix: in AD-17 (new; full text under H-17).**

> - `sequencing.enroll` binds `enrollment.sending_inbox_id` using `sending_policy.pick_inbox(capacity_forecast)`. Sending never chooses or changes it. A referral Enrollment inherits the referrer's inbox (FR-67).
> - If the bound inbox is paused, its OMs wait (`blocked_by=inbox_active`). If it is removed, its active Enrollments stop with reason `inbox_removed` and show on the health panel. There is no reassignment in v1.

### H-16: The pre-send job-posting recheck has no home · Medium

- **Units:** `app.sending` × `app.research` (FR-31's last bullet).
- **How they diverge:**
  - **Agent sending** either:
    - re-checks the posting synchronously inside the send handler. That is a network call between claim and gate; the handler holds the inbox's concurrency key through robots.txt delays, so throughput stalls; or
    - treats the check as research's job and skips it.
  - **Agent research** checks careers pages only on its weekly (FR-7) and daily schedules.
- **Result:** A Step 1 citing a closed job posting goes out, which is a false claim, or sending throughput stalls.
- **Fix: tighten AD-5 (gate predicate `cited_event_fresh`).**

> - For a Step 1 whose cited Event is a job posting, `cited_event_fresh` requires `event.last_checked_at ≥ now − 24h`.
> - On failure, the OM stays `queued` (`blocked_by`) and sending enqueues `research.recheck_event(event_id)` (`call_class=essential`, key `recheck_event:<event>:<date>`). Research then either:
>   - refreshes `last_checked_at`, and the OM proceeds on the next tick; or
>   - marks the Event closed, which emits a notification that runs `approval.return_to_queue(draft, flag='event_expired')` and cancels the OM (H-07).
> - When such an OM is enqueued, sending also schedules the recheck early, at `max(now, window_open − 30 min)`.

---

## C. Sequencing, due times and Draft lifecycle

### H-17: The Enrollment lifecycle has no single owner · High

- **Units:** `app.scoring` ("Enrollment (create)") × `app.replies` ("Enrollment stop"). Other writers also touch it:
  - `approval`: rejecting a Step 1 stops the Sequence (FR-28);
  - `pipeline`: Disqualified, Lost and manual stop;
  - `compliance`: one-click unsubscribe;
  - `sending`: completion after the last Step;
  - `replies`: pause on an uncertain class, and the out-of-office delay.
- **How they diverge:**
  - **Agent replies** owns "stop", so it also writes `paused` and the out-of-office `resume_at`.
  - **Agent sending** writes `completed` after Step 4, because it is the one that knows.
  - **Creation.** FR-67's referral Step 1 needs an Enrollment, and Agent replies creates it while handling the Referral. For FR-59 hand-picks, the web calls drafting, which creates the Enrollment as a side effect. Only scoring's path enforces FR-19 (one active Contact per Lead) and the even Variant split (FR-3).
- **Result:**
  - Two live Enrollments on one Lead.
  - Unbalanced Variants, which make the FR-54 comparison invalid.
  - A stop overwritten by a later `resume` (out-of-office resume after compliance stopped the Enrollment).
- **Fix: add AD-17 (new).**

> **AD-17: Sequencing owns the Enrollment and Step timing**
> - **Binds:** Enrollment; FR-3, FR-4, FR-19, FR-28, FR-31, FR-34, FR-37, FR-59, FR-63, FR-67.
> - **Rule:**
>   - A new owner service, `app.sequencing`, owns Enrollment completely: creation, status, step pointer, due time, Variant and inbox binding. Remove "Enrollment (create)" from scoring and "Enrollment stop" from replies.
>   - **Commands:**
>     - `enroll(uow, contact_id, campaign_id, origin ∈ {selection, operator_pick, referral, recontact, restart}, steps = all | [1])`
>     - `stop(uow, enrollment_id | lead_id, reason)`
>     - `pause(uow, id, reason)` and `resume(uow, id)`
>     - `delay_until(uow, id, ts)`
>     - `on_step_sent` (a notification handler)
>   - **Status** ∈ {active, paused, stopped, completed}. `stopped` and `completed` are terminal. `resume` works only from `paused`, and only if no stop reason has been recorded since the pause.
>   - **Stop reasons** ∈ {reply, manual_reply, hard_bounce, unsubscribe, not_interested, operator_stop, step1_rejected, lead_closed, inbox_removed, contact_erased}.
>   - **`enroll` is the only creator.** In one transaction it:
>     - checks live Eligibility (FR-18, H-33) and contactability (AD-20);
>     - enforces one active-or-paused Enrollment per Lead, with a partial UNIQUE index on `lead_id` where `status IN ('active','paused')`;
>     - applies FR-63 and away dates (FR-31);
>     - assigns the Variant round-robin per Campaign, using a counter row;
>     - binds the inbox (H-15);
>     - emits `Enrolled` and enqueues `draft_enrollment`.
>   - **`stop` cascades in the same transaction.** It calls `approval.cancel_pending(enrollment)` for every Step Draft not yet `sent`, which cancels their OMs (H-07).

### H-18: Three definitions of "due" · High

- **Units:** `app.drafting` (FR-20 drafts every Step up front) × `app.approval` (FR-26 queue shows Drafts due within 24 hours) × `app.sending` (FR-31: each delay counts from the previous Step's actual send).
- **How they diverge:**
  - **Agent drafting** stores `draft.due_at = enrolled_at + {0,3,7,14} days`, moved off weekends in UTC.
  - **Agent approval** shows Drafts with `due_at ≤ now + 24h`.
  - **Agent sending** holds a follow-up until the previous send plus the delay, counted in the recipient's local days.
  - **Example.** Step 1 is approved Tuesday and sent Wednesday. Approval shows Step 2 on Friday, but sending won't send it until Monday. Meanwhile the 72-hour expiry fires on Monday morning, so Mac re-approves.
  - **Out of office.** Replies writes the return date (FR-37) on the Enrollment; neither approval nor sending reads it.
  - **Unsent predecessor.** A Step whose previous Step hasn't been sent still has a valid `due_at`, so Steps 1 and 2 appear together, which is the bunching FR-31 forbids.
- **Result:** The queue and sending disagree, approvals churn, and follow-ups bunch.
- **Fix: in AD-17 (new), and tighten AD-11.**

> - Step timing is stored only on the Enrollment: `next_step_no` and `next_step_due_at` (UTC). Drafts carry no due date.
> - `domain.sending_policy.step_due_at(prev_sent_at, delay_days, contact_tz, not_before)` is the only due-time function:
>   - due = the first Send Window opening on the recipient's local date `local_date(prev_sent_at) + delay_days`;
>   - moved past weekends;
>   - never before `not_before` (the out-of-office return date).
>   - Step 1 is due at the first window opening at or after enrollment.
> - `sequencing.on_step_sent` runs as a notification in the same transaction as `sending → sent`. It advances `next_step_no` and sets `next_step_due_at`. `delay_until` (out of office) recomputes it. Step N+1 has no due time until Step N is `sent`.
> - **The Approval Queue's "due within 24 h"** := `draft.kind = reply`, OR all of:
>   - `draft.step_no = enrollment.next_step_no`;
>   - `enrollment.status = 'active'`;
>   - `enrollment.next_step_due_at ≤ now + 24h`.
> - **FR-31 "stalled"** := that due time plus 7 days is past and the Draft isn't approved.
> - Drafting may generate Steps 2–4 early. They are regenerated if stale (AD-21) before they become due.

### H-19: Draft status versus flags, and who moves which status · High

- **Units:** `app.drafting` × `app.approval` × `app.replies` (supersede) × `app.sequencing` (cancel).
- **How they diverge:**
  - AD-4 binds "Draft status" without listing values, and AD-6 implies `ready` and `approved`. The PRD adds more states: red or failed (FR-21), generic (FR-22), skipped (FR-28), stalled (FR-31), Event expired (FR-31), superseded (FR-65), rejected and expired.
  - **Agent drafting** makes `grounding_failed` and `generic` statuses.
  - **Agent approval's** queue query is `status = 'ready'`. Red Drafts never appear, so nobody edits them, and those Contacts stall quietly. FR-21 requires them to show, flagged red.
  - **Agent replies** writes `superseded` directly, although it doesn't own Draft status.
- **Result:** Drafts vanish from the queue, statuses get written by non-owners, and the dashboard counts disagree.
- **Fix: tighten AD-4.**

> - **Draft status** ∈ {generating, ready, approved, sent, rejected, superseded, canceled}. Everything else is a column, not a status:
>   - `grounding ∈ {pending, pass, fail}`, per revision;
>   - `generic`, `event_expired`, `reapproval_required`, `skipped_until`;
>   - `stalled`, which is derived and never stored.
> - **Writers:**
>   - drafting: `∅ → generating → ready`;
>   - approval: every other transition:
>     - `ready → approved | rejected`;
>     - `approved → ready` (edit, expiry, Event expired);
>     - `approved → sent` (the `MessageSent` handler);
>     - `ready | approved → superseded` (a command that replies calls);
>     - `ready | approved → canceled` (a command that sequencing calls).
> - The queue shows every `ready` Draft that is due (H-18), whatever its flags. The card's badges come from the flags.
> - **Every machine** (Draft, Enrollment, OutboundMessage, Pipeline Stage, Reply, SendingInbox) declares its status enum, transition table and cause enum in `nextron/domain/<machine>.py`. No other module defines status strings. OutboundMessage status ∈ {queued, sending, sent, canceled, bounced}.

### H-20: Who creates non-Step Drafts and grounded artifacts · Medium

- **Units:** `app.replies` (Reply Drafts for FR-43, FR-66 and FR-67, and the referral Step 1) and `app.pipeline` (FR-69 rebooking, FR-47 and FR-48 channel drafts, the FR-68 Call Prep Sheet, which must pass grounding) × `app.drafting` (owns Draft creation and grounding results).
- **How they diverge:**
  - The ER lets a Draft belong to an Enrollment or a Reply. Rebooking and channel Drafts belong to neither.
  - **Agent replies** calls `LlmPort.run('draft_reply')` and inserts the Draft itself. That makes two creators, and the Draft skips grounding.
  - **Agent pipeline** runs its own `grounding_check` and stores the result on CallPrep, creating a second grounding implementation with different flags.
- **Result:** Grounding is inconsistent, Drafts are orphaned, and FR-21 is applied unevenly.
- **Fix: in AD-19 (new).**

> - Only `app.drafting` commands generate Drafts of any kind, and grounded artifacts:
>   - `draft_steps(enrollment)`
>   - `draft_reply(reply_id)`
>   - `draft_rebook(call_outcome_id)`
>   - `draft_channel(linkedin_task_id | channel_permission_id)`
>   - `draft_call_prep(lead_id)`
> - Each Draft has exactly one parent foreign key among {`enrollment_id`, `reply_id`, `call_outcome_id`, `linkedin_task_id`}.
> - Grounding runs only in drafting and is stored per revision. Pipeline stores the Call Prep Sheet that drafting returns.

### H-21: Where the Not Now date lives, and what a re-contact is · Medium

- **Units:** `app.replies` (FR-66 Not Now date) × `app.research` (FR-61 re-enriches on that date) × scoring or sequencing (FR-63 "one re-contact Draft").
- **How they diverge:**
  - **The date.** Replies stores it on the Reply. Pipeline stores it on the Lead (FR-45: "Not Now (with a re-contact date)"). Research's FR-61 scheduler reads the Lead, while scoring reads the Reply.
  - **The re-contact.** One agent builds a full 4-Step Sequence; another builds a single Draft with no Enrollment, so the send gate's `enrollment_active` check fails.
- **Result:** Re-contacts fire on the wrong date or never, and don't pass the gate.
- **Fix: in AD-17 (new).**

> - `Lead.recontact_on` (a date in the Contact's timezone) is written only by `pipeline.park_not_now(uow, lead_id, date, cause_ref=reply_id)`, which replies calls on a Not Now class or override.
> - A daily `recontact_due` job, for each Lead whose `recontact_on ≤ today`:
>   1. chains `research.refresh(lead)`;
>   2. then calls `sequencing.enroll(contact, campaign, origin='recontact', steps=[1])`. That is a one-Step Enrollment, `kind=recontact`, which re-runs Eligibility and the suppression check and moves the Lead from Not Now to Queued.

---

## D. Replies, threads, Manual Replies and bounces

### H-22: There is no Thread entity, and no rule for matching Replies · High

- **Units:** `app.sending` (AD-5 records only "the provider id") × `app.replies` (FR-39 links each message to Contact, Lead and thread).
- **How they diverge:**
  - Several FRs depend on a thread: FR-34 (follow-ups in the same thread), FR-43 and FR-65 ("Campaign thread") and FR-67 (new thread from the same inbox). The ER has no Thread entity.
  - Gmail threading needs `threadId`, `In-Reply-To` and `References`, and a matching Subject.
  - **Agent sending** stores only the Gmail message `id`, as AD-5 says.
  - **Agent replies** matches inbound mail by `In-Reply-To` against RFC Message-IDs that nobody stored. It falls back to the From address, so when a Contact has two threads, a Reply attaches to the wrong Enrollment or to none. Two threads happen when there's a referral thank-you thread beside the Step thread, or a restart after 90 days. A colleague replying in a new thread has the same problem.
  - FR-39 says messages that aren't Replies are ignored. So a real reply can be ignored, and its stop rule never fires.
- **Result:** Stop rules miss, Replies are misattributed, and follow-ups lose their threading.
- **Fix: add AD-18 (new); this hole covers part 1.**

> **AD-18: Threads, inbound matching, Manual Replies and Bounces**
> - **Binds:** `app.sending`, `app.replies`; FR-34, FR-37, FR-39–44, FR-65, FR-67.
> - **Rule (threads):**
>   - **Thread entity** (owner `app.sending`): `{id, sending_inbox_id, gmail_thread_id, root_rfc_message_id, contact_id, lead_id, enrollment_id?, opened_by_om_id}`. It is created when the thread's first OM is enqueued; `gmail_thread_id` is filled in at `sent`.
>   - **Every OM stores** `rfc_message_id`, `gmail_message_id` and `thread_id`. The engine sets `Message-ID: <{om_ulid}@{sending_domain}>`. If Gmail replaces it, sending reads the real value back after the send and stores that **[verify]**.
>   - **Follow-ups** are sent with:
>     - the Thread's `gmail_thread_id`;
>     - `In-Reply-To` = the previous OM's `rfc_message_id`;
>     - `References` = the whole chain;
>     - the subject `Re: <Step 1 subject>`.
>   - **Matching.** `replies` matches each inbound message by trying these in order:
>     1. the receiving inbox's `gmail_thread_id` → Thread;
>     2. `In-Reply-To` or `References` contains a stored `rfc_message_id` → Thread;
>     3. the From address is a Contact with a Thread in this inbox → that Contact's latest Thread;
>     4. the From address's registrable domain is in the Lead's `mail_domains` (AD-20) → the Lead's latest Thread in this inbox, flagged `domain_match`. The new person becomes a Contact through `research.add_contact_from_reply` (FR-37).
>
>     Anything else isn't a Reply. Bounces (delivery-failure notices) are detected before matching (H-26).
>   - **Polling** uses Gmail `history.list` from a per-inbox `history_id` cursor in `inbox_sync_state`, owned by `replies`.

### H-23: The engine's own in-flight sends are logged as Manual Replies · Critical

- **Units:** `app.replies` (FR-65: a Sent-folder message in a Campaign thread that the engine "didn't send itself (matched against its own send records)") × `app.sending` (AD-5 records the provider id only at `sending → sent`).
- **How they diverge:**
  - **Agent replies** matches Sent-folder messages to OMs by Gmail message id, the obvious "send record".
  - Sometimes the engine's own Step is in the Sent folder before any provider id is recorded:
    - between `MailPort.send` returning and the `sending → sent` commit; or
    - when an OM is left in `sending` after a crash or timeout (H-13).
  - Replies' 2-minute poll (FR-39) then sees an unmatched message in a Campaign thread and logs a Manual Reply. That stops the Sequence, supersedes the Reply Drafts, moves the Lead to *Interested* and stops the SM-8 clock.
  - AD-5 reconciliation later finds the header and marks the OM `sent`, so the same message is both a Step and a Manual Reply.
- **Result:** Each crash or timeout turns an outbound Step into a fake *Interested* Lead and kills its Sequence. SM-2, SM-3, SM-8 and FR-54 are all corrupted.
- **Fix: AD-18 (new), part 2.**

> - A Sent-folder message is the engine's own if and only if its `X-Nextron-Message-Id` matches an OM in `sending` or `sent`, including unconfirmed `sending`. Gmail ids are never the match key.
> - The Gmail adapter's list calls always return the headers needed for this (`format=metadata`, `metadataHeaders=[X-Nextron-Message-Id, Message-ID, In-Reply-To, References]`).
> - A Sent-folder message on a Thread with no such header is a Manual Reply. A message whose header holds a ULID not in this database raises an integrity alert and is never a Manual Reply (this happens, for example, when a dev database shares a live inbox).
> - Sent-folder processing for a Thread is deferred while any OM on that Thread is in `sending`. Reconciliation (AD-5) runs first.

### H-24: Stops wait for AI classification, so an opt-out reply doesn't halt the next Step · Critical

- **Units:** `app.replies` (ingestion and classification; AD-8 sends `classify_reply` to Opus; AD-2 bans network calls inside write transactions) × `app.sending` (gate) and sequencing.
- **How they diverge:**
  - Because the AI call can't sit inside a write transaction, ingestion and classification run as separate transactions and jobs.
  - FR-37's stop rules depend on the class (out-of-office doesn't stop), so **Agent replies** stops the Enrollment only once the class is known.
  - FR-40's rule-based opt-out check "runs alongside the classifier", so Agent replies puts it in the classify job, where it waits too.
  - Meanwhile the Reply sits unclassified, during a Claude outage, a refusal, a budget block or just queue lag. The next Step on the thread passes AD-5's six checks and goes out to someone whose reply says "remove me".
- **Result:** Email is sent after an opt-out reply. That's a PECR and CAN-SPAM exposure, and it fails UJ-4.
- **Fix: AD-18 (new), part 3.**

> - In the same transaction that inserts a Reply, `replies`:
>   - runs `domain.optout.rule_match(text)`, a stdlib regex list versioned with the Reply set. On a match it calls `compliance.suppress(address | domain, scope=all_channels)` and `sequencing.stop(reason='unsubscribe')`, and sets the class to `Unsubscribe (rule)`;
>   - otherwise, calls `sequencing.pause(reason='awaiting_classification')` and enqueues `classify_reply` (`call_class=essential`).
> - **Classification resolves the pause:**
>   - out of office → `delay_until(return_date)`, then `resume`;
>   - any other class → `stop` with the matching reason;
>   - uncertain → stays paused and is flagged for the Operator (FR-40).
> - A Reply still unclassified 15 minutes after it arrived alerts the Operator. The pause holds until a class is set, by the model or by the Operator.
> - The gate's `thread_quiet` predicate (H-08) separately blocks any Step on a Thread with an inbound message newer than the Draft, so the pause is never the only guard.

### H-25: Manual Replies versus stop rules, Pipeline Stage and startup order · High

- **Units:** `app.replies` (FR-65 effects) × `app.pipeline` and `app.sequencing` × runner startup, which also starts sending's catch-up.
- **How they diverge:**
  - **Startup order.** The PC was off and Furqaan answered from his phone (§5.3). On wake, the runner claims the overdue `send_due_messages` before the first `poll_inbox`, so Step 3 goes into a thread where Furqaan has already replied. AD-5 reconciles only `sending` rows at startup, not inbound or Sent state.
  - **Lost to Interested.** FR-65 moves the Lead to *Interested* "if it was at an earlier stage". Suppose Furqaan replies "Done, you're removed" to an Unsubscribe. Agent replies asks for Interested. Agent pipeline either treats Lost as terminal and raises, rolling back the Manual Reply insert (the job retries until it is dead), or ranks Lost as earlier and revives a suppressed Lead. AD-4 itself gives "reviving a suppressed Contact" as the example of an illegal transition.
  - **No inbound reply.** Furqaan bumps a thread himself. Replies stops the Sequence and marks the Lead Interested, though the prospect did nothing.
- **Result:** Steps are sent after Furqaan has replied, opted-out Leads are revived, or Manual Replies are lost.
- **Fix: AD-18 (new), part 4.**

> - **Startup.** `send_due_messages` skips an inbox until that inbox's `poll_inbox` (inbound and Sent, from its history cursor) has completed since process start and within the last 10 minutes.
> - **Manual Reply effects**, in one transaction:
>   - always `sequencing.stop(reason='manual_reply')`, and `approval.supersede` for every pending Reply Draft on the Thread;
>   - `pipeline.advance_to_at_least(Interested)` only if the Thread's latest Reply is hot (H-28), the Lead isn't Lost or Disqualified, and the Contact passes contactability;
>   - a Manual Reply to a suppressed address only alerts the Operator (FR-65).
> - The SM-8 clock stops only for a Thread whose latest Reply is hot.

### H-26: Bounce has no owner, and a temporary-bounce retry conflicts with exactly-once · High

- **Units:** `app.replies` (finds delivery-failure notices while polling) × `app.sending` (owns OM status, SendingInbox status and the FR-38 pause) × `app.research` (owns Address Class and FR-16 pattern learning).
- **How they diverge:**
  - Bounce appears in neither the owner map nor the ER.
  - **Agent replies** parses delivery-failure notices and writes `contacts.address_class = 'Invalid'` and `sending_inboxes.status = 'paused'` directly. That breaches AD-3, but no other service is named as the owner of bounce effects.
  - **Agent sending** computes the FR-38 7-day bounce rate from an OM status `bounced` that nobody ever writes.
  - **Temporary bounces.** FR-44 says retry inside the Send Window. Agent sending re-sends the same OM, which AD-5 forbids (same ULID). Agent replies creates a new Draft that needs re-approval.
- **Result:** FR-38's auto-pause never fires (deliverability risk), Contact has two owners, and the retry either breaks exactly-once or waits on Mac.
- **Fix: AD-18 (new), part 5.**

> - **Bounce** is an entity owned by `replies`. It holds detection, parsing and a class `hard | temporary | policy`, and links to the OM through the original `Message-ID` or `X-Nextron-Message-Id` in the notice.
> - **Applying a Bounce**, in one transaction:
>   - `sending.record_bounce(om_id, class)`: the OM moves `sent → bounced`, with its class; the inbox's 7-day counters update; FR-38 pauses go through the SendingInbox machine;
>   - for a hard bounce, also `research.mark_address_invalid(contact_id, cause_ref)` (the FR-16 counters live in research) and `sequencing.stop(reason='hard_bounce')`.
> - **Temporary-bounce retry.** Sending creates a retry OM with `retry_of=<om>`, the same `approval_id` and text, a new ULID and the same kind, gated as usual. There are at most 3 retries per approval, and H-07's live-OM index doesn't count `bounced` rows. The third temporary bounce is recorded as hard. No new approval is needed, because the text is the same.

### H-27: A strict Pipeline Stage machine versus automatic moves · High

- **Units:** `app.pipeline` (the owner; AD-4 strict transitions) × the services that move stages automatically:
  - `app.sending`: Contacted;
  - `app.replies`: Replied, Interested, Not Now, Lost;
  - `app.approval`: "bad fit" → Disqualified;
  - `scoring` and `research`: FR-1 disqualifiers;
  - `compliance`: unsubscribe → Lost.
- **How they diverge:**
  - AD-4: `transition(current, event)` rejects disallowed transitions.
  - **Agent pipeline** raises `DomainError` on `Call Booked → Replied`.
  - **Agent replies** requests `Replied` for every Reply (FR-45: "moves Leads through … Replied automatically"), inside the ingestion transaction. A second reply from a Call Booked Lead therefore rolls back the Reply insert, the job retries until it is dead, and the stop or pause never commits.
  - **Agent sending** requests `Contacted` on every Step, which hits the same wall once a Lead is Interested.
  - "Disqualified" is decided in three services, each with its own rules.
- **Result:** Reply ingestion fails for engaged Leads, which are the most valuable ones.
- **Fix: tighten AD-4.**

> - **Order.** Pipeline Stages are ranked: New < Queued < Contacted < Replied < Interested < Call Booked < Call Held < Audit Proposed < Audit Won < Build Proposed < Won. Not Now is a parking stage. Lost and Disqualified are terminal.
> - **Automatic moves** use only `pipeline.advance_to_at_least(uow, lead_id, stage, cause, cause_ref)`. It is a no-op (no event row, no error) when the Lead is already at or past `stage`, parked or terminal. It never raises over ordering.
> - **Backward moves.** Only Operator actions, plus the `recontact_due` job for Not Now → Queued, move a Lead backward or out of Not Now, Lost or Disqualified.
> - **Terminal stages.** `pipeline.close(uow, lead_id, Lost | Disqualified, reason, cause_ref)` is the only way in. It calls `sequencing.stop(lead, 'lead_closed')`.
> - **Disqualifiers** live only in `domain.eligibility.disqualifiers(segment, lead_profile)`, run by scoring. "Bad fit" reaches `pipeline.close` through the `BadFitRejected` notification.

### H-28: Three definitions of Hot Lead · Medium

- **Units:** `app.replies` (FR-42 alerts) × `app.approval` (FR-26 puts Hot Leads first) × `app.pipeline` (the Interested stage "means the Lead is a Hot Lead").
- **How they diverge:**
  - **Agent approval** sorts by `pipeline_stage = Interested`, so a Call Booked Lead who sends a *Question* isn't prioritized.
  - **Agent replies** uses the latest Reply Class.
  - An uncertain Reply that is probably hot (FR-40) triggers an alert but isn't treated as Hot in the queue.
- **Result:** The alert and the queue order disagree, and SM-8 is measured on a different set of Leads.
- **Fix: AD-18 (new), part 6.**

> - `domain.hot.is_hot(reply_class, objection_engaged, uncertain_candidates)` is the only definition. `replies` stores `Reply.hot` at classification or override. A Lead is Hot if and only if its latest Reply has `hot = true`.
> - Queue order, alerts, FR-54 and SM-8 read `Reply.hot`. Pipeline Stage is a consequence of being hot, never the test for it.

---

## E. Compliance and identity

### H-29: Cross-channel contactability, and nobody owns the opt-out mark · Critical

- **Units:** `app.replies` and `app.compliance` (FR-41 Unsubscribe and Not Interested handling) × `app.pipeline` (FR-48 LinkedIn Tasks) and `app.drafting` (FR-24 and FR-47 channel Drafts).
- **How they diverge:**
  - FR-41 does two things: it adds the address to the Suppression List, *and* it "marks the Contact so that no channel Drafts are ever created for them again". FR-63 says the Contact is never contacted again on any channel.
  - The map gives SuppressionEntry to compliance and Contact attributes to research. The Contact mark has no owner.
  - **Agent compliance** writes only the SuppressionEntry (an address hash).
  - **Agent pipeline** builds LinkedIn Tasks after checking the Suppression List by the Contact's email hash, so it still builds Tasks for:
    - Contacts with no email (an FR-12 UK Lead that is barred from email but allowed on LinkedIn);
    - the same person re-created from a Signal Inbox post after erasure;
    - Contacts whose *domain* was suppressed ("remove us"), since the lookup is by address.
  - **Agent replies**, handling "remove me", writes the SuppressionEntry itself, giving it two owners.
- **Result:** LinkedIn, WhatsApp or Instagram outreach to people who opted out, which breaches PECR and UK GDPR Art. 21 (FR-41, FR-63, §5.1).
- **Fix: add AD-20 (new); this hole covers part 1.**

> **AD-20: Contactability and Eligibility are single predicates with single writers**
> - **Binds:** FR-2, FR-9, FR-18, FR-24, FR-37, FR-41, FR-48, FR-50, FR-51, FR-63, FR-70.
> - **Rule (contactability):**
>   - `app.compliance` is the only writer of SuppressionEntry and of `contact_optouts(contact_id, scope ∈ {email, all_channels}, cause, cause_ref, at)`.
>   - Replies, unsubscribe polling, Operator actions and erasure call `compliance.suppress(uow, target, scope, cause)`. Unsubscribe and Not Interested always use `all_channels`. "Remove us" also suppresses the domain (FR-41).
>   - Every channel decision calls one predicate, `domain.contactability.check(contact, lead, suppression_view, channel)`. That covers enrollment, generating a Draft of any kind, creating a LinkedIn Task, and the send gate. It fails if:
>     - the address hash is suppressed;
>     - any of the Lead's `web_domain` or `mail_domains` is suppressed;
>     - a matching `contact_optouts` row exists;
>     - the Lead is Lost or Disqualified;
>     - the person matches a suppressed `person` key.
>   - Opt-out and erasure also record a `person` suppression key: `sha256(normalize_name(full_name) + "|" + registrable(web_domain))`. Every Contact creation checks it, from any source. *[Legal check: this is a pseudonymised key kept for suppression only.]*

### H-30: Normalizing emails and domains for suppression and dedupe · High

- **Units:** `app.compliance` (the suppression hash is the "SHA-256 of the normalized address") × `app.discovery` (FR-9 dedupe by website domain; FR-50 check at discovery) × `app.replies` (FR-37 "any address at the Lead's domain"; "remove us" suppresses a domain).
- **How they diverge:**
  - The Conventions define how to normalize an email, but not a domain, and not what a domain entry's hash covers.
  - **Agent compliance** hashes domain entries as `sha256("example.co.uk")`.
  - **Agent discovery** normalizes `https://www.Example.co.uk/` to `www.example.co.uk`, or to the final redirect host. The check misses, so a firm that said "remove us" is rediscovered as a new Lead. FR-9's dedupe misses too, because research stored `example.co.uk`.
  - **Mail domain versus web domain.** A firm may use `examplelaw.com` for mail and `example-law.com` for its website, so FR-37 domain matching and domain suppression both miss.
  - IDN domains are another source of mismatches.
- **Result:** Suppressed firms are re-contacted, and duplicate Leads appear (FR-9, FR-41, FR-50).
- **Fix: AD-20 (new) part 2, plus the Conventions row for Emails.**

> - `domain.identity` holds the only normalizers:
>   - `normalize_email`: trim, NFC, lowercase, IDNA-encode the domain; no dot or plus stripping;
>   - `registrable_domain(host)`: lowercase, IDNA, strip `www.`, then eTLD+1 using a vendored Public Suffix List snapshot;
>   - `normalize_name`.
> - **SuppressionEntry** = `{kind ∈ {address, domain, person}, value_hash = sha256(kind + ":" + normalized value), value_plain (nullable; blanked on erasure), source, cause_ref, created_at}`.
> - A Lead stores `web_domain` and `mail_domains[]`: the registrable domains of every Contact address and of every inbound sender matched to the Lead. FR-9 dedupe, domain suppression, the discovery-time suppression check and FR-37 domain matching all use these fields.

### H-31: Who owns unsubscribe tokens, and acknowledging before the commit · High

- **Units:** `adapters.unsubscribe` (UnsubscribePort) × `app.compliance` (processes requests) × `app.sending` (puts the token in `List-Unsubscribe`).
- **How they diverge:**
  - AD-15 says the engine "polls and acknowledges", but not in which order.
  - **Agent adapter** writes `fetch_pending()` to GET and acknowledge in one call, the natural port shape. A crash or "database is locked" error before compliance commits loses the opt-out for good, because the Worker has already deleted it.
  - **Token ownership.** Agent sending creates a token per OM and stores it on the OM. Agent compliance expects to own an `unsub_tokens` table, keyed per Contact.
  - **Deleted Contacts.** Erasure or retention (FR-52, FR-53) deletes the Contact. A late one-click, which mail clients may send days later, then can't be resolved and is dropped.
- **Result:** Lost opt-outs (CAN-SPAM, PECR, FR-70).
- **Fix: tighten AD-15.**

> - **Tokens** belong to compliance: `unsub_tokens(token_hash PK, address_hash, contact_id NULL, om_id, created_at)`. `sending.enqueue` gets one per OM from `compliance.issue_unsub_token(uow, contact_id, om_id)`. Erasure and retention set `contact_id` to null but keep `address_hash`, so a late request still suppresses the address.
> - **The port** is `list_pending() -> [{token, received_at}]` plus `ack(tokens)`.
> - **Processing.** `compliance.process_unsubscribes` first commits `suppress(…, scope=all_channels)` and `sequencing.stop`, and calls `ack` only after the commit. Processing is idempotent by token. An unknown token is logged and acknowledged.
> - **Mailto** unsubscribes arrive in the Sending Inbox and go through Reply ingestion (H-24).

### H-32: Erasure and retention versus other owners' rows and cascades · High

- **Units:** `app.compliance` (FR-52 erasure, FR-53 retention) × every owner that holds personal data:
  - research: Contact, Evidence quotes that name the person, Provenance;
  - drafting: Draft revisions;
  - sending: OM copies and Threads;
  - replies: Reply, Manual Reply and Bounce text;
  - pipeline: Call Prep, LinkedIn Tasks, notes;
  - any stored AI request or response.
- **How they diverge:**
  - AD-3 bars compliance from writing those tables, and AD-4 allows erasure to blank only `events`.
  - **Agent compliance** deletes the Contact row, and the persistence agent declared `ON DELETE CASCADE`. OMs, Threads and Enrollments vanish with it. Reconciliation can then no longer recognize a past send by its header: a later Sent scan finds an unknown ULID (H-23), and FR-54's counts drop.
  - **The alternative** blanks only the Contact's columns. The name and address survive in Draft bodies, Reply text and Evidence quotes, so the erasure is incomplete.
- **Result:** Either a GDPR erasure failure, or the send ledger and analytics are corrupted.
- **Fix: add AD-23 (new).**

> **AD-23: Erasure fans out to owners and leaves tombstones**
> - **Binds:** FR-52, FR-53, every table with a personal-data column.
> - **Rule:**
>   - `compliance.erase(uow, contact_id, cause ∈ {dsr, retention})` first writes the suppression entries (address and person key). Then, in the same transaction, it calls every registered `erase_pii(uow, contact_id)` hook. Each owner implements its hook in its `commands.py`, registered in `main.py`. A test asserts that every table with a personal-data column has a hook.
>   - **Owners tombstone rows that other rows reference; they never delete them.**
>     - Free text (subject, body, quotes, names, addresses, notes, delivery-failure text) becomes `"[erased]"`.
>     - Ids, statuses, kinds, timestamps, hashes, `X-Nextron-Message-Id` and `rfc_message_id` stay.
>   - No foreign key uses `ON DELETE CASCADE`.
>   - Evidence rows that name a person carry `person_contact_id`, so the hook can find them.
>   - AI request and response bodies are never stored; only `ai_usage` metadata is kept.

### H-33: A stored Eligibility snapshot versus live Eligibility · Medium

- **Units:** `app.scoring` (owns the "Eligibility snapshot") × `app.approval` (FR-18: ineligible Contacts never enter the queue) × `compliance.assert_sendable` (re-checks "Eligibility") × `app.research` (FR-51 Provenance records "the Market rule that made it Eligible").
- **How they diverge:**
  - **Agent sending** re-checks Eligibility by reading the snapshot, since that's the stored Eligibility. **Agent scoring** refreshes the snapshot nightly. A suppression, hard bounce, move to Lost or Address Class downgrade during the day leaves the snapshot unchanged, so the gate passes.
  - **Agent approval** also filters the queue by the snapshot.
  - **Agent research** writes the Eligibility basis into the Provenance Record from its own evaluation, which can disagree with scoring's.
- **Result:** Ineligible sends get through, and the provenance basis is inconsistent.
- **Fix: AD-20 (new), part 3.**

> - Eligibility is always evaluated live by `domain.eligibility.evaluate(contact, lead, market_rules, suppression_view, now) -> Decision{eligible, basis_rule, reasons[]}`.
> - The snapshot is a display and ranking cache that scoring writes. The queue, `enroll` and the gate never read it.
> - The Provenance "rule that made it Eligible" is the Decision returned by `sequencing.enroll`, recorded through `research.record_eligibility_basis(uow, contact_id, decision)`.

---

## F. Evidence and AI

### H-34: Re-enrichment changes Evidence that existing Drafts cite · High

- **Units:** `app.research` and `app.playbook` × `app.drafting` and `app.approval`.
  - Research changes Evidence through FR-61 re-crawls, FR-7 weekly careers and ATS checks, FR-11 Event expiry and the FR-31 posting recheck. Playbook changes facts through FR-72 edits.
  - Drafts store `evidence_ids` and `company_fact_ids` (AD-7). FR-26 says a follow-up whose Evidence or facts changed after drafting is regenerated before it enters the queue.
- **How they diverge:**
  - AD-7 says rows *reference* Evidence. It doesn't say Evidence can't change.
  - **Agent research** upserts Evidence on each re-crawl (new quote text, new `retrieved_at`) and deletes Evidence for pages that have gone.
  - **Agent drafting** detects "changed" by comparing `retrieved_at`. Every re-crawl, even with identical content, regenerates every pending follow-up, adding cost, churn and re-review for Mac. (Or it detects nothing at all.)
  - **What breaks:**
    - An approved Step whose cited quote was rewritten in place now shows Mac different Evidence from what he approved.
    - A deleted Evidence row breaks the card and its foreign key.
    - Editing an Approved Company Fact in place silently changes approved Drafts.
  - FR-61 skips Leads in an active Sequence, but the FR-7 weekly checks and Signal Inbox updates don't.
- **Result:** Approved claims drift from the Evidence behind them, follow-ups churn, and cards break.
- **Fix: add AD-21 (new).**

> **AD-21: Evidence and facts are immutable; staleness is computed**
> - **Binds:** Evidence, Signal, ApprovedCompanyFact, Draft; FR-7, FR-11, FR-26, FR-31, FR-61, FR-72.
> - **Rule:**
>   - Evidence and ApprovedCompanyFact rows are never updated or deleted, except for erasure tombstones.
>     - A changed observation inserts a new Evidence row and sets `superseded_by` on the old one.
>     - A source that has gone sets `retracted_at`.
>     - An identical re-observation only updates `last_confirmed_at`, which change detection ignores.
>     - Editing a fact inserts a new `(fact_id, version)`. Drafts cite `(fact_id, version)`.
>   - `domain.grounding.is_stale(draft_rev, evidence_view, fact_view, now)` is the only staleness test. A Draft is stale if any of these hold:
>     - a cited Evidence row is superseded or retracted;
>     - a cited fact version is no longer current, or no longer allowed for the Segment and Market;
>     - the cited Event isn't live (`domain.signals.is_live`).
>   - **Ready (unapproved) stale Drafts** are regenerated by drafting before they become due (the `refresh_due_queue` job).
>   - **Approved stale Drafts** go back to the queue only when the cited Event isn't live or a cited fact version was withdrawn. Approval sets the flag `event_expired` or `facts_changed` and cancels the OM. A superseded Evidence text alone doesn't revoke an approval during its 72-hour life.

### H-35: Evidence type versus source, and discovery writing Evidence · High

- **Units:** `app.discovery` (FR-8 attaches content as Evidence with source "manual"; the map gives discovery the SignalInboxItem) × `app.research` (owns Evidence and Signal; FR-11 requires Evidence "of the matching type") × `app.drafting` (FR-11: state an Absence only in hedged wording).
- **How they diverge:**
  - AD-7's list of Evidence types (`quote, absence, count, manual, registry`) mixes type and source.
  - **Agent discovery** stores a LinkedIn post as Evidence of type `manual`, as AD-7 literally allows.
  - **Agent research's** Signal validator requires `quote` for Events (the FR-1 table gives Quote as their Evidence). The Signal Inbox Event is never stored, so there's no Tier A and no Step 1 within 15 minutes. UJ-3 fails.
  - **Agent drafting** treats every non-`quote` type as needing hedged wording, and writes "I couldn't find…" about a post the prospect wrote.
  - Discovery writing Evidence at all breaches research's ownership.
- **Result:** UJ-3 and FR-8 are dead, and Drafts misstate their own Evidence.
- **Fix: tighten AD-7.**

> - Evidence has two independent fields:
>   - `type ∈ {quote, absence, count}`: what was observed (PRD §3);
>   - `source ∈ {website, ats, registry, signal_inbox, csv, referral, operator}`: where it came from.
>
>   `manual` and `registry` are sources, not types.
> - Only `app.research` writes Evidence and Signals. Signal Inbox intake (`app.discovery`) creates the SignalInboxItem and the Lead, then chains `research.ingest_signal_item(item_id)`, which writes Quote Evidence with `source=signal_inbox`.

### H-36: Batch write-back and the synchronous `LlmPort` contract · High

- **Units:** AI gateway (`adapters.ai`; AD-8 defines `LlmPort.run(route, inputs)` and says batchable routes use the Message Batches API) × `app.drafting` and `app.research` (call the port and write results).
- **How they diverge:**
  - AD-8 gives the port one synchronous signature.
  - **Agent gateway** implements `run()` for batchable routes as submit, then poll until done, which can take up to 24 hours. The calling job keeps running long past its lease, the runner re-claims it, and a second batch is submitted. Two result sets come back, and drafting writes two Drafts per Step: duplicates in the queue at double the cost.
  - **Agent drafting** assumes `run()` returns a result straight away.
  - Nothing says where batch state lives, and adapters can't write the database (AD-1).
  - Results that arrive after the world has changed are written anyway: a stopped Enrollment, a suppressed Contact, superseded Evidence, a deactivated route version.
- **Result:** Duplicate Drafts, double cost, and Drafts for Contacts who have since been stopped or suppressed.
- **Fix: add AD-22 (new); this hole covers part 1.**

> **AD-22: Asynchronous AI: batch contract, urgency and activation**
> - **Binds:** `LlmPort`, `adapters.ai`, every caller; FR-8, FR-20–22, FR-25, FR-57, FR-71, FR-73.
> - **Rule (batches):**
>   - `LlmPort` has three calls, and no adapter call waits on a batch:
>     - `run(route_tuple, inputs, call_class) -> Result` (Messages API);
>     - `submit_batch(route_tuple, items[{custom_id, inputs}]) -> provider_batch_id`;
>     - `fetch_batch(provider_batch_id) -> {status, items[{custom_id, Result | Error}]}`.
>   - **Batch state** belongs to the calling service: `ai_batches` and `ai_batch_items(custom_id, target_table, target_id, target_rev, input_fingerprint, status ∈ {submitted, applied, stale, failed})`. `custom_id = <route>_<target ULID>_<rev>`; the Batches API limits it to 64 characters of `[A-Za-z0-9_-]` **[verify]**.
>   - **Submission and collection are separate jobs:** `submit_<route>_batch`, and `collect_ai_batches` every 10 minutes.
>   - **Applying a result** goes through the owner's `apply_<route>_result(uow, item)`, which re-checks that:
>     - the target still exists and still needs this result;
>     - `input_fingerprint` (a hash of the cited Evidence ids and versions, the fact versions and the route tuple) still matches;
>     - the Contact is still contactable.
>
>     There is at most one result per `(target, rev)`, enforced by a UNIQUE constraint. Anything else is marked `stale`, and requested again if it is still needed.

### H-37: `batchable` is set per route, not per call · High

- **Units:** `app.discovery` (the Signal Inbox chain; FR-8 needs a Step 1 Draft in the queue within 15 minutes), `app.drafting` (Operator regenerate, FR-25; referral Step 1, FR-67) × `adapters/ai/routes.py` (sets `batchable` per route; the memlog batches nightly enrichment extraction and Step drafting).
- **How they diverge:**
  - AD-8: "Batchable routes run through the Message Batches API." `extract_signals` and `draft_sequence` are batchable.
  - **Agent research** sends extraction for a Signal Inbox Lead through the Batches API, because the route says so.
  - **Agent drafting** does the same for that Lead's Step 1 Draft, and for an Operator's regenerate.
- **Result:** Minutes-to-hours latency. FR-8's 15 minutes and SM-11 fail, and regenerate becomes unusable (FR-25, FR-29).
- **Fix: tighten AD-8, as AD-22 part 2.**

> - `batchable` means the route *may* use the Batches API. Each call site passes `urgency ∈ {interactive, near_real_time, bulk}`, and only `bulk` calls are batched:
>   - `bulk`: nightly enrichment, and nightly Step drafting for Enrollments due 24 hours or more ahead;
>   - `interactive`: Operator regenerate, edit recheck;
>   - `near_real_time`: the Signal Inbox chain, referrals, the Reply-ingestion chain, and regenerating Drafts due within 24 hours.
>
>   `interactive` and `near_real_time` calls use the Messages API.

### H-38: Two sources of truth for the active route and Playbook version · High

- **Units:** AI gateway (routes are data in `routes.py`, prompts at `prompts/<route>/<version>.md`, and the Playbook version is the file's frontmatter, per AD-12) × `app.playbook` (owns "AI route activation, ai_test_runs").
- **How they diverge:**
  - AD-1 keeps the gateway out of the database, so it can't read activation records or `ai_test_runs`.
  - **Agent gateway** uses the `prompt_version` in `routes.py` and the Playbook file as it is on disk at call time. Editing `sales-playbook.md` therefore changes the live `playbook_version` at once, without testing, which breaches FR-73.
  - **Agent playbook** writes an `ai_route_activations` table that nothing reads.
  - **If the gateway refuses untested tuples instead,** one Playbook edit stops every AI call, including `classify_reply`.
  - **Rollback** to the last passing version is impossible, because the Playbook is one mutable file and its old text isn't stored.
- **Result:** Untested prompts or Playbooks go live, or a single file edit halts all AI work.
- **Fix: AD-22 (new), part 3.**

> - `routes.py` declares candidate versions. The active tuple for each route is a row in `ai_route_activations(route, model, effort, prompt_version, prompt_sha256, playbook_version, playbook_sha256, schema_version, test_run_id, activated_at)`. Only `app.playbook` writes it, and only after an `ai_test_runs` pass on exactly that tuple.
> - App services read the active tuple and pass it to the port. The gateway loads that prompt file, checks `prompt_sha256`, and reads the Playbook text from `playbook_snapshots(version, sha256, text)`, never from the live file.
> - Editing the Playbook or a prompt creates only a candidate. The previous active tuple keeps serving until a new one passes. A test asserts that prompt files for activated versions are unchanged, by hash.

### H-39: No legal path for internal emails, and conflicting alert content · High

- **Units:** `app.replies` (FR-42: an email to Furqaan's main inbox) and `app.pipeline` (FR-68: the Call Prep Sheet by email; FR-48: LinkedIn Tasks by email) × `app.sending` and the Telegram adapter.
  - AD-5: only `app.sending` calls `MailPort.send`.
  - AD-6: every OM comes from an approved Draft.
  - AD-9: one port and one adapter per external system.
  - AD-15: limits what alerts may contain.
- **How they diverge:**
  - A literal replies agent has no way to send the alert email:
    - not through MailPort, because of AD-5;
    - not through a second Gmail adapter, because of AD-9;
    - not through sending, because that needs an approved Draft, fails the Eligibility check, and would send from a cold Sending Inbox to `nextronai.com`.
  - **Agent A** routes alerts through `AlertPort` with a Gmail-backed adapter, breaching AD-9.
  - **Agent B** makes alert emails into OMs with an "auto-approved" Draft, breaching AD-6, and they count against the Daily Cap.
  - **Content.** AD-15 limits Telegram to the Lead name, the Reply text and the Draft. FR-42 also requires the Contact, the cited Signal, the booking link, two slots and the Sending Inbox. The Telegram adapter agent strips the extra fields, and the replies agent's FR-42 test fails.
- **Result:** FR-42's email channel and FR-68 delivery can't be built within the ADs.
- **Fix: add AD-24 (new) and tighten AD-15.**

> **AD-24: Internal notifications**
> - **Binds:** FR-42, FR-48, FR-58, FR-68, FR-70 health alerts.
> - **Rule:**
>   - `AlertPort` is the only path for messages to Nextron people: Telegram, email to the addresses in the `internal_recipients` setting, and dashboard notifications.
>   - Its email adapter calls the Gmail API under a separate credential (an `internal_notify` account) and refuses any recipient outside `internal_recipients`. It never creates OMs and never uses a Sending Inbox.
>   - AD-5's "only `app.sending` calls `MailPort.send`" covers prospect-facing mail. AD-9's "one adapter" rule is per port, not per vendor.
>
> **AD-15 (tighten), alert content:**
> - FR-42 alerts may contain: the Lead name, the Contact's name and role, the Reply text, a summary of the cited Signal, the Reply Draft, the booking link, the proposed slots and the Sending Inbox address.
> - The FR-68 Call Prep Sheet goes to the Sender, and the day's LinkedIn Tasks (FR-48) go to the account owner.
> - Nothing else: no Evidence dumps and no other Contacts.

---

## G. Edges and integration

### H-40: The Places adapter returns nothing discovery can use · Medium

- **Units:** `adapters.places` (AD-9: returns only `place_id` and coordinates) × `app.discovery` (FR-5 records the place ID, name and website URL; Leads are crawled from their website).
- **How they diverge:**
  - **Agent adapter** returns `{place_id, lat, lng}`. Discovery then has no website to crawl and no domain to dedupe on (FR-9).
  - **Agent discovery** works around it by calling Place Details from the service (breaching AD-1 and AD-9), or by finding the website some other way.
- **Result:** FR-5 (Slice 2) produces no usable Leads.
- **Fix: tighten AD-9.**

> - The Places adapter returns `{place_id, website_uri, lat, lng}`.
> - `website_uri` is transient. Discovery may use it only to derive `web_domain` and enqueue the crawl. The Lead becomes durable with `web_domain` only after the crawler has fetched the site and research has stored Quote Evidence from it (the FR-5 assumption).
> - The adapter never returns Places names, addresses, phone numbers or reviews. Latitude and longitude are purged after 30 days. **[Verify against the Maps Platform terms, OQ-3.]**

### H-41: Merging Leads must re-parent rows that discovery doesn't own · Medium

- **Units:** `app.discovery` (creates and merges Leads) × the owners of Contact, Signal and Evidence (research), Enrollment (sequencing), Draft (drafting) and Pipeline Stage (pipeline).
- **How they diverge:**
  - **Agent discovery** either updates `contacts.lead_id` and similar columns directly (breaching AD-3), or merges only the Lead row and leaves the children on the absorbed Lead.
  - A merge where both Leads have an active Enrollment leaves two active Contacts on one Lead (FR-19).
- **Result:** Orphaned Evidence and Contacts, a broken FR-19, or an ownership breach.
- **Fix: tighten AD-3.**

> - `discovery.merge(uow, survivor_id, absorbed_id)` calls each owner's `reparent_lead(uow, from_id, to_id)` in one transaction. The absorbed Lead stays as a row with `merged_into` set.
> - A merge where both Leads have an active or paused Enrollment is refused and shown to the Operator.
> - The survivor keeps the more advanced Pipeline Stage.

### H-42: Contact timezone or Market can be null · Medium

- **Units:** `app.research` (writes Contact attributes; AD-11 takes the timezone from the office location, otherwise from the Lead) × `domain.sending_policy` and `app.sending`, plus the queue's due computation.
- **How they diverge:**
  - Many US Leads have no single location, and Places addresses can't be stored (AD-9).
  - **Agent research** leaves `tz` null.
  - **Agent sending** treats null as UTC (US recipients then get mail from 03:00 ET) or raises, which leaves the OM `queued` forever.
- **Result:** Sends outside the Send Window (§5.3, FR-34) or Sequences stuck.
- **Fix: tighten AD-11.**

> - `Contact.tz` and `Contact.market` must be set before enrollment; `sequencing.enroll` refuses a Contact without them.
> - Research resolves `tz` from the Contact's office location, then the Lead's headquarters from website Evidence, then a Market default:
>   - UK: `Europe/London`;
>   - US, when unknown: `America/New_York`, with the Send Window narrowed to the overlap of all contiguous US zones (11:00–17:00 ET) **[ASSUMPTION]**.
> - The fallback used is recorded in the Provenance Record.

### H-43: Parallel Alembic heads and duplicated schema concepts · Medium

- **Units:** any two stories that add schema in parallel, for example *Replies* (adds `replies`) and *Compliance* (adds `contact_optouts`).
- **How they diverge:**
  - AD-2 allows schema changes only through Alembic.
  - Each agent autogenerates a revision whose `down_revision` is the head it saw. That makes two heads, so `alembic upgrade head` fails at integration.
  - Separately, both stories add the same concept under different names, such as `contacts.do_not_contact` versus `contact_optouts`.
- **Result:** Integration breaks, and duplicate sources of truth appear.
- **Fix: tighten AD-2 and AD-3.**

> - Migrations form one linear chain, with revision ids `<NNNN>_<story-key>`.
> - A CI test asserts exactly one head, and that every model table and column group appears in `nextron/db/owners.py` (the executable AD-3 map).
> - A story that needs a column in another owner's table asks for it through that owner's story or epic. It doesn't add the column itself.

### H-44: No registry of settings keys · Low

- **Units:** `app.setup` (Settings) × the services that read settings:
  - `sending_policy`: windows and caps;
  - replies: SM-8 business hours;
  - drafting: footer fields, and Reply Draft slots from the Sender's availability.
- **How they diverge:**
  - Each unit invents its own keys and shapes. For example, `send_window_start="08:00"` versus `sending.window.start_minute=480`, or availability as a cron expression versus a list of ranges.
  - The Slice 3 editing screens write keys that nothing reads.
- **Result:** Rework at integration.
- **Fix: tighten AD-12.**

> - Every setting is declared once in `nextron/domain/settings_schema.py` with `key, type, default, unit, validation`.
> - The `settings` table rejects unknown keys. Consumers read typed values only through `setup.queries.get(Key.X)`.
> - Formats:
>   - times are `HH:MM`, local to a named timezone key;
>   - durations are ISO 8601;
>   - weekly availability is a list of `{weekday, start, end}` in the Sender's timezone.

### H-45: The `events` vocabulary is too loose for analytics · Medium

- **Units:** every owner that writes `events` (AD-4) × the analytics read models in `app.pipeline` (FR-54, FR-55, SM-8, SM-11).
- **How they diverge:**
  - Owners write `events` inconsistently:
    - `entity`: one owner writes the class name, another the table name;
    - `cause`: free text ("reply", "Reply received", "classified:Interested");
    - `actor`: "operator", "mac" or "web".
  - FR-54's "the Step that drew the first Reply" needs a Reply → OM → Step link that nobody records.
  - FR-55's approval time and edit share aren't status transitions, so no unit records them.
- **Result:** FR-54, FR-55, SM-6, SM-7 and SM-8 can't be computed, or are computed wrongly.
- **Fix: tighten AD-4.**

> - `events` columns:
>   - `entity` (the table name) and `entity_id`;
>   - `from_status` and `to_status`;
>   - `cause`: a member of the machine's closed cause enum in `domain/<machine>.py`;
>   - `cause_ref`: the ULID of the triggering row (reply, OM, approval, job, call outcome);
>   - `actor ∈ {operator, sender, system:<service>}`;
>   - `at`.
> - Facts analytics need that aren't transitions live as columns on their owner's rows: `approvals.review_ms`, `approvals.edit_ratio`, `replies.om_id`, `replies.step_no`, and the `ai_usage` rows. Analytics read `events` plus these columns and nothing else.

### H-46: Domain signatures: a `Clock` port versus `now` · Low

- **Units:** `domain.sending_policy` (AD-1: stdlib only) × `app.sending` (AD-11: "takes a `Clock` port").
- **How they diverge:**
  - **Agent domain** can't import `nextron.ports.Clock`, so its functions take `now: datetime`.
  - **Agent app** passes a `Clock` object, as AD-11 says.
  - The signatures differ at about 10 call sites. `transition(current, event)` has the same problem: events arrive as strings, enums or dataclasses.
- **Result:** Rework at integration.
- **Fix: tighten AD-11 and AD-1.**

> - Domain functions take `now: datetime` (UTC, timezone-aware) explicitly. Only app services hold a `Clock`, and they call `clock.now()` once per use case.
> - State machines take `(current: Status, event: <Machine>Event enum)` and return `Transition(to, cause)` or raise `IllegalTransition`.

---

## Patch plan (where each fix lands)

| AD | Change | Holes |
|---|---|---|
| AD-1 | Allow `app → app.commands/queries` along the AD-16 graph; domain takes `now` | H-01, H-46 |
| AD-2 | Single-process file lock; linear migrations | H-04, H-43 |
| AD-3 | Ownership per column group; executable `owners.py`; Tier → scoring; merge via `reparent_lead` | H-05, H-41, H-43 |
| AD-4 | Closed status, cause and actor enums per machine; Draft statuses versus flags; `advance_to_at_least` and `close` | H-19, H-27, H-45 |
| AD-5 | Gate as a closed predicate table with terminal or wait outcomes; continuous reconciliation; header-only matching; `cited_event_fresh` | H-08, H-13, H-16 |
| AD-6 | Single OM creation path and constraints; canonical rendered-text hash and OM copy; revisions and grounding by hash; 72-hour expiry and cancel | H-07, H-09, H-10, H-11 |
| AD-7 | Evidence `type` versus `source`; only research writes Evidence | H-35 |
| AD-8 | Call classes; urgency instead of route-level batching | H-06, H-37 |
| AD-9 | `UsageMeterPort`; Places returns a transient `website_uri` | H-06, H-40 |
| AD-10 | `app.jobqueue` owns `jobs`; outbox enqueue; chained stages plus a closed list of periodic triggers; key scope; payload rule; thread pool, concurrency keys, lease renewal | H-02, H-03, H-04 |
| AD-11 | Dispatch-time pacing and priority; one `step_due_at` and `approval_expired`; timezone not null | H-11, H-14, H-18, H-42, H-46 |
| AD-12 | Settings schema registry | H-44 |
| AD-15 | Token ownership and ack after commit; alert content aligned with FR-42 | H-31, H-39 |
| **AD-16** new | Cross-owner commands share the caller's transaction; notifications; acyclic graph | H-01 |
| **AD-17** new | `app.sequencing` owns Enrollment, Step timing, inbox binding and re-contact | H-15, H-17, H-18, H-21 |
| **AD-18** new | Threads, inbound matching, own-send detection, pause on arrival, Manual Reply effects, Bounce, Hot | H-22–H-26, H-28 |
| **AD-19** new | Message kinds and per-kind send policy; drafting is the sole Draft creator | H-12, H-20 |
| **AD-20** new | Contactability, identity normalization and live Eligibility | H-29, H-30, H-33 |
| **AD-21** new | Immutable Evidence and facts; computed staleness | H-34 |
| **AD-22** new | Async `LlmPort`, batch write-back, urgency, DB-held activation and Playbook snapshots | H-36, H-37, H-38 |
| **AD-23** new | Erasure fan-out with tombstones; no cascades | H-32 |
| **AD-24** new | Internal notifications through `AlertPort` | H-39 |

**Owner-map rows to add or change:**

| Owner | Writes |
|---|---|
| `sequencing` (new) | Enrollment |
| `jobqueue` (new) | jobs |
| `sending` | adds Thread |
| `replies` | adds Bounce and `inbox_sync_state`; loses "Enrollment stop" |
| `compliance` | adds `contact_optouts` and `unsub_tokens` |
| `drafting` | adds `draft_revisions` and its own `ai_batch_items` |
| `approval` | adds `approvals` |
| `playbook` | adds `ai_route_activations` and `playbook_snapshots` |
| `db.metering` | `api_usage` and `ai_usage` (replaces "adapters (meters)") |
| `scoring` | loses "Enrollment (create)"; gains Tier |
| `research` | loses Tier |

**ER changes:** add THREAD and BOUNCE; change to `DRAFT ||--o{ OUTBOUND_MESSAGE`; let a Draft's parent also be a CallOutcome or a LinkedInTask.

## Gaps noticed in passing (not unit-pair holes; for the architect)

- **No port or adapter is named for:**
  - Google Postmaster Tools (FR-38 spam rate);
  - the headless browser that FR-10 requires (Playwright isn't in the Stack);
  - DNS blocklist checks (FR-38), unless `adapters/dns` is meant to cover them; say so.
- **The Stack has no import-linter pin,** though AD-1 depends on it.
- **The FR-36 catch-up and the 72-hour approval expiry interact over long weekends.** Expect re-approval of every Step approved on Friday that couldn't be sent by Monday. That is the intended FR-31 behavior, but the health panel should explain it.
