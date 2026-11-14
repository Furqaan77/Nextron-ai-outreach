---
title: 'Spine v2: where each critical and high review finding landed'
target: ../ARCHITECTURE-SPINE.md (status draft, updated 2026-11-14)
inputs: [review-rubric.md, review-verified.md, review-adversary.md, reconcile-prd.md, ../.memlog.md D1–D15]
date: 2026-11-14
---

# Spine v2 change map

## Summary

| Review | Critical | High | Resolved | Deferred | Resolved, with a residual open question |
|---|---|---|---|---|---|
| review-rubric.md | 2 | 14 | 16 | 0 | 4 (H-1, H-2, H-12, H-13) |
| review-verified.md | 0 | 2 | 2 | 0 | 1 (H2) |
| review-adversary.md | 9 | 24 | 33 | 0 | 1 (H-26) |
| reconcile-prd.md | 0 | 12 | 12 | 0 | 1 (H1) |
| **Total** | **11** | **52** | **63** | **0** | **7** |

New ADs: AD-16 contactability (D7), AD-17 PII registry and erasure (D8), AD-18 prompt-injection containment (D9), AD-19 inbound ingest and stop rules (D4, D5, D15), AD-20 operational envelope (D14), AD-21 sequencing and due times (D15), AD-22 threads and outbound assembly (D15). Evidence immutability (D15) went into AD-7 and batch handling (D12) into AD-8 rather than new ADs. New sections: *Open Questions*, *Structural Seed → Operations*, owner map rewrite, two ER diagrams, app-DAG diagram, send-path sequence diagram, settings defaults table.

## Where a D-decision overrode a reviewer's suggested fix

| Finding | Reviewer proposed | Spine follows |
|---|---|---|
| rubric H-2, adversary H-13 | return to `queued` after two absence checks | D6: not found → `unknown`; only the Operator re-queues (AD-5) |
| rubric H-1, adversary H-31, reconcile H8 | `POST /ack` marking entries acknowledged | D10: no acks, no deletes; 30-day TTL; commit before processed; local dedup (AD-15) |
| rubric H-4 | the UoW passed as a parameter to owner commands | D1: ambient, context-local UoW per entry point (AD-3) |
| rubric M-4, adversary H-06 | drafting blockable at 100 % plus reserve | D11: only discovery, enrichment and re-check work is blockable (AD-8) |
| adversary H-18 | `enrollments.next_step_due_at`, Drafts carry no due date | D15: `drafts.due_at` is the only due time (AD-21) |
| adversary H-26, reconcile M11 | a retry OM per temporary Bounce | D2: one live OM per Draft → Gmail's own retry; third temporary = hard; OQ-A5 |
| rubric H-12, adversary H-39, reconcile H1 | allowlists incl. Contact role and LinkedIn Task fields | D13: Contact name only, never email addresses, Task content stays local (AD-15); OQ-A6 |
| rubric H-14 | Python `nextron.launcher` supervisor | D14: `scripts/run.ps1` restart loop plus lock (AD-20) |
| adversary H-08 | `cited_event_fresh` as a gate predicate | D3 gate list kept exact; the FR-31 posting recheck and FR-65 thread recheck run in AD-5's prepare step |
| reconcile H8 | off-PC suppression journal written at once | D8/D14: erasure ledger (extended to every suppression key) copied off-PC with each backup; Gmail re-ingest and Worker re-poll cover reply and one-click opt-outs |

## review-rubric.md

| ID | Sev | Resolved in | Note |
|---|---|---|---|
| C-1 | Critical | AD-16; AD-5 `contactable`; AD-3 `SubjectSuppressed` | Gate on every outreach path; four hashed key kinds |
| C-2 | Critical | AD-17; AD-4 (events hold no PII); AD-20 (ledger on restore) | Registry, tombstones, external-store table, receipts |
| H-1 | High | AD-15 | Token, Worker routes, bearer polling, host, mailto. Open question OQ-A7 (Cloudflare DNS for `u.<domain>`); dead-man switch → Deferred |
| H-2 | High | AD-5, AD-4 (OM machine), AD-20 (restore) | Window listing plus metadata header read; `unknown` per D6. Open question OQ-A1 (Gmail spike) |
| H-3 | High | AD-18, AD-19 | Rule-first opt-out final; code validates proposals |
| H-4 | High | AD-3, AD-1, owner map, app DAG | Column-group ownership, `sequencing` and `jobqueue` owners, notifications |
| H-5 | High | AD-4 | All six machines plus seven supporting ones |
| H-6 | High | AD-6 | Rendered-message hash incl. footer, recipient and inbox; shown hash on approve; gate re-checks |
| H-7 | High | AD-19 | One history-based sync per inbox; `mail_messages` |
| H-8 | High | AD-10, AD-2, Conventions (Concurrency) | Three lanes, concurrency keys, leases, sync `def` |
| H-9 | High | AD-8 | Urgency per call; no `fallbacks` in batches; typed Haiku failures |
| H-10 | High | AD-13 | Escaping, link schemes, CSP, frame-ancestors, HTMX config |
| H-11 | High | AD-9 | Egress filter with pinned IPs and redirect re-validation |
| H-12 | High | AD-15 | D13 content. Open question OQ-A4 (Telegram 48 h delete limit) |
| H-13 | High | AD-20, AD-17 | In-process backup API, encryption decided, escrow, restore test, recovery mode. Open question OQ-A3 (destination) |
| H-14 | High | AD-20, *Operations*, AD-2 | doctor, logon task recipe, run.ps1, lock |

## review-verified.md

| ID | Sev | Resolved in | Note |
|---|---|---|---|
| H1 | High | AD-8 | `fallbacks` only on direct calls; batch refusals re-run directly; test asserts batch bodies |
| H2 | High | AD-5 | No header search; list `SENT` in the window and read `X-Nextron-Message-Id` via metadata; ambiguous → `unknown`. Open question OQ-A1 |

## review-adversary.md

| ID | Sev | Resolved in | Note |
|---|---|---|---|
| H-01 | Critical | AD-1, AD-3 | D1; app DAG plus closed notification list |
| H-02 | Critical | AD-10 | `app.jobqueue` owns `jobs`; transactional enqueue; chained stages; closed trigger list |
| H-07 | Critical | AD-6 | D2; partial unique index on `draft_id` |
| H-08 | Critical | AD-5 | D3 predicate table |
| H-09 | Critical | AD-6, AD-22 | Canonical hash; OM holds its own text copy; headers outside the hash |
| H-13 | Critical | AD-5 | D6; reconcile every 2 min; never retry `send` |
| H-23 | Critical | AD-19 | D5; header-bearing SENT is never a Manual Reply |
| H-24 | Critical | AD-19 | D4; suppress or pause in the ingest transaction |
| H-29 | Critical | AD-16 | D7 |
| H-03 | High | AD-10 | Key format; partial unique index; ID-only payloads |
| H-04 | High | AD-10, AD-2 | Thread lanes; `inbox:` concurrency key; lease heartbeat; lock file |
| H-05 | High | AD-3, owner map, AD-7 | Column groups; Tier only in `scoring` |
| H-06 | High | AD-9, AD-8 | `UsageMeterPort` in `db.metering`; D11 budget classes |
| H-10 | High | AD-6 | Revisions; grounding per revision; edit-then-approve |
| H-11 | High | AD-6, AD-11 | One `approval_expired`; expiry cancels the OM first |
| H-12 | High | AD-5 | Cold vs responsive kinds |
| H-14 | High | AD-11 | Dispatch-time pacing; follow-ups first; no burst after downtime |
| H-17 | High | AD-21 | `app.sequencing` sole owner and creator |
| H-18 | High | AD-21, AD-11 | D15 `drafts.due_at` |
| H-19 | High | AD-4 | Status vs flags; writers per transition |
| H-22 | High | AD-22, AD-19 | Thread entity; matching by `threadId` then `In-Reply-To` |
| H-25 | High | AD-19, AD-11, AD-4 | Sync before dispatch at startup; Manual Reply effects; `advance_to_at_least` |
| H-26 | High | AD-19 | Bounce owned by replies (D15). Open question OQ-A5 (temporary retry) |
| H-27 | High | AD-4 | Ranked stages; `advance_to_at_least` no-op; `close` |
| H-30 | High | Conventions (Identity), AD-16 | `domain.identity` normalisers; PSL; `mail_domains` |
| H-31 | High | AD-15 | D10; token record owned by compliance |
| H-32 | High | AD-17 | Tombstones; no `ON DELETE CASCADE` |
| H-34 | High | AD-7 | D15 immutability; single staleness test |
| H-35 | High | AD-7 | `type` vs `source`; only research writes Evidence |
| H-36 | High | AD-8 | Async port calls; owner `apply_<route>_result` with fingerprint re-check |
| H-37 | High | AD-8 | Urgency per call |
| H-38 | High | AD-12, AD-8 | Activation rows; snapshots by hash; gateway reads the snapshot |
| H-39 | High | AD-15, AD-5 | `AlertPort` `internal_email` adapter; never `MailPort` |

## reconcile-prd.md

| ID | Sev | Resolved in | Note |
|---|---|---|---|
| H1 | High | AD-15 | FR-42 content per D13. FR-48 Task delivery → open question OQ-A6 (D13 keeps Task content local) |
| H2 | High | AD-5 | D3 per-kind gate; FR-31 and FR-65 rechecks in the prepare step |
| H3 | High | AD-8, AD-9 | D11 |
| H4 | High | AD-8 | Urgency per call |
| H5 | High | AD-9 | Transient `PlaceCandidate`; only `place_id` kept |
| H6 | High | AD-20 | Monthly restore test, `restore_tests`, pass criteria |
| H7 | High | AD-20, AD-10 | In-process backup API; PC-on scheduling; `backup.ps1` removed |
| H8 | High | AD-20, AD-5, AD-15, AD-17 | Recovery mode, OMs to `unknown`, ledger reapplied, full re-poll |
| H9 | High | AD-9 | Hard-coded FR-49 denylist on every fetch |
| H10 | High | AD-17, AD-12 | `ai_test_cases` in the DB; pseudonymised only; nothing real in Git |
| H11 | High | AD-12, AD-8 | Engine-run sets; activation by exact tuple and hash; snapshots |
| H12 | High | AD-4 | Analytics fact columns from Slice 1 |

## Medium and low findings applied

- **rubric:** M-1 (AD-1, AD-9, AD-10, AD-11), M-2 (AD-12), M-3 (AD-12), M-4 (AD-8; sizing → OQ-A8), M-5 (AD-5), M-6 (AD-9), M-7 (AD-9, Playwright 1.65.2), M-8 (Stack, Conventions, AD-11), M-9 (AD-15), M-10 (AD-9), M-11 (Conventions), M-12 (AD-13), M-13 (AD-2, AD-20), M-14 (AD-20), M-15 (AD-10, AD-15); L-2 (AD-14), L-3 (AD-12; DKIM in OQ-A1), L-4 (AD-2), L-5 (Conventions), L-6 (AD-8), L-7 (AD-15, Conventions), L-8 (Open Questions). Not applied: L-1 (D5 names `X-Nextron-Message-Id`).
- **verified:** M1 (AD-8, AD-10), M2 (AD-8), M3 (AD-15), M4 (AD-20; OQ-A9), M5 (Stack, AD-2), M6 (AD-1); L1–L3 (Stack), L4 (AD-10), L5 (AD-2, Conventions), L6 (AD-8, AD-17), L7 (AD-8), L8 (Stack), L9 (AD-12, Stack), L10 (AD-9).
- **adversary:** H-15, H-21 (AD-21), H-16 (AD-5 prepare step), H-20 (owner map: `drafting` creates every Draft kind and Call Prep Sheet), H-28 (AD-19), H-33 (AD-16), H-40 (AD-9), H-41 (AD-3 `LeadsMerged`), H-42 (AD-11), H-43 (AD-2), H-45 (AD-4), H-44 (AD-12), H-46 (AD-11, AD-4).
- **reconcile:** M1 (AD-3), M2 (AD-1, AD-9, AD-10), M3 (AD-20), M4 (AD-5), M5 (AD-10), M6 (AD-17), M7 (AD-15), M8 (AD-16), M9 (AD-9, owner map, AD-19 `spam_accusation`), M10 (AD-6, AD-22), M11 (AD-19; OQ-A5), M12 (AD-19), M13 (AD-21), M14 (AD-7, owner map, capability map), M15 (AD-4, owner map), M16 (AD-7), M17 (AD-9), M18 (AD-15), M19 (Conventions defaults), M20 (OQ-A8), M21 (AD-12), M22 (owner map), M23 (AD-10); L1 (OQ-A11), L2 (Conventions), L3 (AD-4, AD-16), L4 (AD-12), L5 (AD-11), L6 (AD-7), L7 (AD-7), L8 (AD-9), L9 (Deferred), L10 (owner map), L11 (AD-15).

## Verification

- `lint_spine.py`: ok, 0 findings.
- All 6 mermaid blocks parse with mermaid 11.4.1 (3 flowchart, 2 ER, 1 sequence).
- New Stack pins checked on PyPI today: cryptography 50.0.2 (abi3 win_amd64 wheel, released 2026-09-30), pytest 9.2.0. Others come from review-verified.md and review-rubric.md.
