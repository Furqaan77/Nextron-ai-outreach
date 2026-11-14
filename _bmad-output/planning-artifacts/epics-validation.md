---
document: epics-validation
target: _bmad-output/planning-artifacts/epics.md
workflow: bmad-create-epics-and-stories, Step 4 (final validation)
date: 2026-11-15
result: pass
---

# Epics and Stories: Final Validation

**Target:** `_bmad-output/planning-artifacts/epics.md`
**Result:** All 8 checks pass. Some passed after fixes made in place. `epics.md` is set to `status: final`, `stepsCompleted: [1, 2, 3, 4]` and `updated: 2026-11-15`.

## Totals

There are 11 epics and 146 stories.

| Epic | Title | Slice | Stories |
|---|---|---|---|
| 1 | Safe, self-running engine on the PC | 1 | 21 |
| 2 | Researched Leads with evidence and a reachable decision-maker | 1 | 16 |
| 3 | Legal to contact: suppression, eligibility and opt-outs everywhere | 1 | 16 |
| 4 | Grounded AI drafts in Furqaan's voice | 1 (4.21 in 2) | 21 |
| 5 | Fast, keyboard-first Approval Queue | 1 (5.11 in 2) | 11 |
| 6 | Safe sending from warmed inboxes | 1 | 13 |
| 7 | Replies, opt-outs and the hot-lead handoff to Furqaan's phone | 1 | 11 |
| 8 | Automated Lead supply | 2 | 12 |
| 9 | Discovery calls and pipeline | 2 (Pipeline view in 3) | 8 |
| 10 | LinkedIn assist and warm channels | 3 | 9 |
| 11 | Funnel insight and self-service settings | 3 | 8 |

Each story has between 2 and 11 Given/When/Then blocks, with an average of 6.3.

## Edits made during Steps 3 and 4

**Merge**
- The four story parts replaced the template placeholder block in order: Epics 1–2, 3–4, 5–7, then 8–11.
- No `{{...}}` placeholders remain.

**Requested fixes (B1–B6)**

| # | Fix | Where |
|---|---|---|
| B1 | Retention moved from Story 3.16 to the end of Epic 8 as **Story 8.12**. Its content is unchanged. Its `Slice: 2` tag was dropped because it now matches the epic's slice. The Epic List, the FR Coverage Map (FR-53 → Epic 8) and both epic goal statements were updated. | Epic List, FR Coverage Map, Epic 3 and Epic 8 intros, Story 8.12 |
| B2 | `step1_rejected` and the follow-up warning card are gone. Rejecting a Draft for any Step now runs `sequencing.stop(operator_rejected)` in the same unit of work. A new AC covers rejecting a Step 3 Draft: the Enrollment is `stopped`, the Step 4 Draft is `cancelled`, and if the stop fails the whole transaction rolls back. A second new AC confirms that skipping doesn't stop the Enrollment. The FR-28 inventory line now notes the 2026-11-15 architecture amendment. | Story 5.5, FR-28 inventory line |
| B3 | New AC: the story registers the AD-17 Sending Inbox mailbox erasure handler. It uses `MailboxPort.trash_thread` (Gmail `threads.trash` under the `gmail.modify` grant) to move the Contact's Threads to Trash, which satisfies Epic 3's erasure contract test. | Story 6.7 |
| B4 | New AC: Warm-up-Ramp caps replace Epic 4's placeholder `queries.sending.cold_daily_caps`. Each inbox's cap is min(`daily_cap`, ceiling − tool sends), keeping the same signature. | Story 6.4 |
| B5 | Added `**Slice:** 2`. New AC: the spike runs only after 2 weeks of `ai_usage` data, or at the first 80% budget alert. | Story 4.21 |
| B6 | Checked against the PRD values: the connection note is at most 200 characters, the first message at most 300, there are 10 Tasks per weekday, and at most 100 invitations per rolling week. The one mismatch was `daily_invite_cap`, which defaulted to 20. It now defaults to 10, matching the PRD risk table's "10 a day". The existing test cases still hold. | Story 10.2 |

**Validation fixes (Step 4)**
- **Forward references (3, reworded in place):**
  - Story 3.8 named Story 3.10. It now describes the vector file as a shared fixture.
  - Story 3.9 named Story 3.10. It now says "how the Worker is deployed".
  - Story 4.13 depended on Story 4.14 for claims on Operator edits. `check` now takes the claims as an input. Story 4.14 gained an AC that passes the AI-extracted claims back into `grounding_rules.check`, which is a backward dependency.
- **Oversized story:** Story 3.11 had 13 AC blocks. Its 4 monitoring and recovery ACs moved to a new **Story 3.12**, "Monitor unsubscribe polling and re-poll the Worker after a restore". Stories 3.12–3.15 were renumbered 3.13–3.16. No text referred to their old numbers.
- **Role lines:** these were normalized to the template's "As a/an …" form:
  - 21 "As Operator (Mac)" became "As an Operator (Mac)".
  - 3 "As prospect" became "As a prospect".
  - 2 "As Sender (Furqaan)" became "As a Sender (Furqaan)".
- **Traceability:** FR-27 was added to Story 5.1's Requirements, because that story's ACs render the card fields FR-27 lists.

## Check results

### 1. FR coverage: pass

A script parsed every story's **Requirements** line, excluding `NFR-` matches. All of FR-1 to FR-73 appear on at least one story. The story-level map agrees with the FR Coverage Map:

- **Split FRs** land in the epics the map names:
  - FR-1 to FR-4: setup in Epics 2 and 4, editing screens in Epic 11.
  - FR-19: capacity and Operator pick in Story 4.10, automatic selection in Story 8.9.
  - FR-45: automatic stages in Story 7.6, manual stages in Stories 9.1 and 9.4.
  - FR-57: meters in Epic 1, budget classes in Epics 4 and 8.
- **FR-53** is now covered only by Story 8.12.

**Spot check of ACs against each FR's PRD consequences.** A script checked one to four key consequences per FR (thresholds, numbers, states) against the text of the stories that cite it. All FRs matched except six, which were reviewed by hand:

| FR | What was checked | Finding |
|---|---|---|
| FR-7 | ATS hosts | `ats_hosts` defaults to Greenhouse, Lever and Ashby (Story 2.5). Story 2.11 uses them as the sources `greenhouse`, `lever` and `ashby`. |
| FR-15 | accept-all handling | `accept_all` and `unknown` leave the address `guessed` (Story 2.14). |
| FR-38 | 3% bounce pause | Story 6.13 tests 100 sends with 4 hard Bounces and the inbox pauses. 3 Bounces in 100 sends only alerts. |
| FR-65 | Manual Reply to a suppressed address | Story 7.3 alerts the Operator and keeps the suppression. |
| FR-67 | Referral Drafts at the top of the queue | Story 7.11 puts the thank-you in the Reply Draft group and the referral Step 1 right after it, above every other Step 1. This fits FR-26's group order. |
| FR-28 | Rejection | Follows the 2026-11-15 spine amendment: rejecting any Step stops the Enrollment. This is broader than the PRD's "Step 1" and is noted on the inventory line. |

Key consequences confirmed in the ACs include:
- Step delays of 0/3/7/14 days with the weekend shift
- word limits per Step
- 15-minute Signal Inbox drafting
- robots.txt with a 1-second crawl delay
- Tier 1 and Tier 2 Event decay at 30/90 days
- the 14-day warm-up gate, the 5-a-week ramp and the 50 ceiling
- 4–12 minute gaps between sends
- the 3-day approval expiry and 7-day stall flag
- the 300 ms next-card time
- bounce rates of 2% (alert) and 3% (pause) on at least 50 sends
- spam rates of 0.1% (alert) and 0.3% (pause), with "no data" kept as no data
- the 5-minute hot-Lead alert and "draft to follow"
- 90-word Reply Drafts with two 20-minute slots inside 3 business days
- 5-minute unsubscribe polling and the 24-hour polling-failure alert
- the 150-contacted guard on Variant comparisons
- the 20% major-edit threshold
- AI Test Set bars: at least 30 and 50 cases, 100% and 95%, 100% and 90%
- 90-day restarts, and Not Now re-contact on the chosen date
- retention after 6 and 12 months

### 2. UX-DR coverage: pass

All 32 UX-DRs in the inventory (UX-DR1 to UX-DR32) appear on at least one story's Requirements line. The most widely cited are:
- UX-DR3 (CSRF POST actions): 21 stories
- UX-DR17 (Lead page): 15 stories
- UX-DR29 (health panel): 16 stories

### 3. Starter: pass

There is no third-party starter. Story 1.1, "Scaffold the engine project with layer contracts and a localhost health page", is the scaffold. It covers:
- the uv project with a committed `uv.lock` and the SC-1 pins
- the package layout from the spine's source tree
- the import-linter contracts
- `NEXTRON_ENV` and a data dir per environment
- the `nextron doctor` stub with startup checks
- a health page served on 127.0.0.1

### 4. Tables created only when needed: pass

**Epic 1 creates no domain tables.** It creates only infrastructure tables, each in the story that first uses it:

| Table | Story |
|---|---|
| `events` | 1.3 |
| `settings` | 1.6 |
| `jobs` | 1.10 |
| `runner_heartbeat` | 1.11 |
| `alerts`, `notifications` | 1.12 |
| `api_usage`, `ai_usage` | 1.13 |
| `backups` | 1.16 |
| `restore_tests` | 1.18 |

**Domain tables** are created by the story that first writes them. Examples:
- `leads` (2.2), `contacts` and `provenance_records` (2.3)
- `suppression_entries` (3.3)
- `enrollments` (4.8), `drafts` (4.9)
- `outbound_messages` (5.2)
- `threads` (6.7)
- `replies` (7.1)
- `discovery_runs` (8.3)
- `call_outcomes` (9.1)
- `linkedin_tasks` (10.4)
- `segment_versions` (11.5)

Later stories add columns through their own migrations, for example `drafts` in 5.5 and 10.3, and `sending_inboxes` in 6.4 and 6.13.

**No story creates a table it doesn't use.** Two cases were checked by hand:
- Story 4.5 creates `ai_test_cases` and `ai_test_runs` because the activation gate reads both. Story 4.6 fills the cases.
- Story 5.4 creates `approval_sessions` and opens and closes sessions in its own ACs.

**Mentions of a table before its creating story are guards, not dependencies:**
- Stories 1.16 and 1.18 record or skip `suppression_entries` and `provenance_records` as "absent".
- Story 3.4's AST scan lists insert paths for tables later epics add.
- Story 3.14's contract test fires only "if a `threads` table exists".

### 5. Story quality: pass (after fixes)

- **Template:** every one of the 146 stories has:
  - an As a / I want / So that statement
  - exactly one **Requirements** line
  - an **Acceptance Criteria** header
  - matched Given/When/Then blocks, with equal counts in every story
- **Size:** after the Story 3.11 split, the largest stories have 11 AC blocks (4.9, 6.2 and 6.10). Each covers one component:
  - 4.9: the Draft slots and Enrollment lifecycle
  - 6.2: inbox OAuth connection
  - 6.10: the exactly-once send path
- **Length:** the longest stories by word count are 1.1 (scaffold), 2.9 (Signal detection) and 3.4 (contactability gate). Each is cohesive. None spans unrelated components.

### 6. Forward dependencies: pass (after fixes)

- **References to story numbers:** the remaining ones all point backward within the same epic: 3.10→3.9, 4.21→4.5, 8.x→8.1, 8.11→8.9, 9.3→9.2 and 11.2→11.1. The three forward ones (3.8→3.10, 3.9→3.10, 4.13→4.14) were reworded, as listed above. No story cites a story number in another epic.
- **References to epics:** no story or epic intro names its own or a later epic ("Epic K" with K ≥ current). Cross-epic hand-offs go backward. For example:
  - 6.4 replaces Epic 4's cap placeholder.
  - 6.7 satisfies Epic 3's erasure contract.
  - 8.12 reads sending and Reply history from Epics 6 and 7.
- **Extension points:** wording such as "later stories add their checks to the same registry" (doctor, health-issue and recovery-step registries; `reparent_lead`; `assert_contactable` callers) describes open registries that later work plugs into. It isn't a dependency.
- No open items remain.

### 7. Epic structure and file churn: pass

**Structure**
- Each epic delivers an outcome the Operator, the Sender or compliance can observe, and builds only on earlier epics.
- Epic 1 is the platform epic. It is framed around Operator value: one-command install, `nextron doctor`, safe dashboard entry, credentials, quotas, health, backups and recovery.
- Slice order holds:
  - Epics 1–7 (Slice 1) reach the first approved, compliant send.
  - Epics 8–9 are Slice 2.
  - Epics 10–11 are Slice 3.
  - Stories tagged with a different slice: 4.21 (Slice 2), 5.11 (Slice 2), and 9.7 and 9.8 (Slice 3).
- Spikes come before the stories that depend on them: 1.20 (OQ-A9), 3.9 (OQ-A7), 6.1 (Gmail send path) and 8.5 (OQ-7). Story 4.21 (OQ-A8) is deliberately last, because it needs real usage data.

**Justified overlaps (shared surfaces touched by several epics)**

| Surface | Stories | Epics | Why |
|---|---|---|---|
| Lead page templates | 35 | 2, 3, 4, 6, 7, 8, 9 | Each epic adds its own section (Provenance, Drafts, Threads, Replies, re-check, pipeline history). |
| Health panel and health-issue registry | 25 | 1, 3–10 | Each owner registers its own `health_issues()` query in `main.py`. |
| Settings panel and `settings_schema` | 17 | 1, 3–6, 8, 10, 11 | Settings are declared once per story, through one schema. |
| `domain.grounding_rules` | 15 | 4, 5, 7–11 | Rules are added per Draft kind (Reply, restart, channel, Call Prep). |
| `pii_registry.py` and `owners.py` | 14 and 11 | — | Every new table or column must be classified and owned, which AD-3 and AD-17 enforce by test. |
| import-linter contract list | 19 | — | Each new adapter joins its contract group. |
| `domain.sending_policy` | 9 | 4, 5, 6, 11 | AD-11 makes it the single home for time, caps and pacing. |

**Altered shared tables**
- `drafts`: 4.9, 5.5, 10.3
- `sending_inboxes`: 4.7, 6.4, 6.13
- `enrollments`: 4.8, 7.6, 8.11, 11.7
- `contacts`: 2.3, 3.2

Each alteration comes with its own migration and is owned by one column group.

**No unjustified churn:** no story rewrites another epic's behavior. Replacements are explicit and backward-facing:
- 6.4 replaces the 4.10 placeholder.
- 11.5–11.8 move seed-managed configuration into the dashboard.

### 8. Numbering: pass

- Epics are numbered 1–11 with no gaps.
- Stories run continuously from 1 within each epic, with no gaps.
- Every story ID's prefix matches its epic.
- The Story 3.16 → 8.12 move and the 3.11/3.12 split were renumbered and re-verified by script.

## Open checks

None.
