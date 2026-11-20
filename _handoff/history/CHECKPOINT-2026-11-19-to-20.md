# CHECKPOINT: build paused on 2026-11-19, RESUMED the same day

**Resumed 2026-11-19.** Mac said "read these and continue pls". Fresh agents were started:
- one merge agent, running section A ("Finish batch 4");
- four track orchestrators resuming 8.11 (A), 6.6 (B), 4.17 (D) and 5.2 (E), per section C.

The old scratchpad `suite_lock.py` now uses the same lock folder as `_handoff\suite_lock.py`.

## LATEST STATE (update this block first)
- **A move to a new PC is planned.** See `MOVE-TO-NEW-MACHINE.md`. When Mac says "prepare to move":
  - start no new stories;
  - let running stories and the merge finish;
  - update this block;
  - report "build idle, safe to pack".
- **Batch 5 is DONE and verified (2026-11-20).**
  - Merges: 8.10 `c8e99d8`, 8.7 `61af1bc`, 6.9 `17c4090`, 6.11 `36d2c37`, 4.15 `01bd4ab`, 5.1 `1aef1c3`.
  - Full suite: 20127 passed, 7 skipped, 29 contracts kept (log `scratch\main-merge\full-batch5.log`).
  - Docs commit: `e4feb56`.
  - Migration head: `0064_4_15_draft_failed`. The next migration is 0065.
- **Batch 6 merge is running** (agent `a2777d1fd245fadee`). Stories in merge order:
  - 4.17 `8318a90`
  - 4.18 `640bb27`
  - 5.2 `fb8d52b`
  - 5.3 `e183ef5`
  - 6.6 `59433fc`
- **Tracks running:**

  | Track | Story | Branch |
  |---|---|---|
  | A | 8.11, PAUSED for memory. Uncommitted work in e:\Nextron-epic1; resume later | track-a11 |
  | B | 4.19 Tier C fallback copy. Agent `a47136f0da134ddb0` | `track-b18`, on 4.16 `edd6b52` |
  | D | 4.20 Regenerate. Agent `aab27ab557dec2b13` | `track-d13`, on 4.18 `640bb27` |
  | E | 5.6 edit then approve. Agent `a93f959e41aea6555` | `track-e16`, on 5.4 `408a167` |

  Track C is idle.
- **Waiting for the next merge batch:**
  - 6.2 `98ac14e` (on 6.6). Migration `0048_6_2_inbox_connect`: `sending_inboxes.status_changed_at` NOT NULL (fixtures must set it) and `connected_at`. Notes:
    - setting `gmail_internal_app_confirmed`;
    - `Sending(clock, *, env, gap_source=None, domain_checks=None, notify=None)`;
    - `InboxConnections`;
    - doctor check `sending_inbox_grants` (last; 15 checks);
    - `build_app(gmail=)`;
    - `GmailPort` is in the approved extra ports.
  - 5.4 `408a167` (on 5.3). Migration `0049_5_4_approvals`. Notes:
    - `create_app(approval=)` is required;
    - setting `approval_session_idle`;
    - wire 6.9's `current_approval` stub to `queries.approval.approval_of`, mapping `text_hash` to `approved_hash`;
    - cascades go through `sending.api.cancel_for_cascade`;
    - `static/queue-keys.js`.
  - 4.16 `edd6b52` (on main `e4feb56`). Migration `0065_4_16_draft_when_ready`, which clashes with 4.17's 0065. Notes:
    - `LeadScores(..., selection=, discovery=)`;
    - `Selection.pick_one`;
    - the 4.18 v2 default must reach its speed test.
    - OPEN for Mac: a Lead created from a Signal Inbox post has no `country_code`, so it is never auto-drafted.
- **Load (2026-11-19 ~21:25):** RAM ran out and caused a 4th restart. Now running 3 tracks + the merge, with `suite_lock` at `-n 6`.
- **Load (~22:50):** available RAM fell to 62 MB (commit stayed at 81%), so `suite_lock` was lowered to `-n 4`. VS Code itself uses about 3.4 GB. A memory monitor alerts the main session.
- **5th restart (2026-11-20, after the rate limit):** the stale lock (dead track-B pid) was removed, and the merge and tracks B, D and E were resumed by SendMessage.
- **Lock fix (2026-11-19):** `suite_lock.py` treats a lock as stale only when the holder pid is dead, with a 4 h backstop. Before this, two suites ran at once and crashed Claude Code.
- **Disk:** C: had about 8 GB free. `suite_lock.py` (both copies) now passes `--basetemp` under `_handoff\scratch\tmp\<label>`. Track rules require `--basetemp` on E: for targeted runs.
- **After any Claude Code restart:**
  - kill orphaned Nextron pytest processes;
  - check for a stale `_handoff\pytest-suite.lock`;
  - resume agents with SendMessage to their ids if they still resolve, or start fresh agents using PROMPTS.md.

The text below records the state as it was at the pause. Sections A and C are partly outdated now (see LATEST STATE).

Original pause note: Mac said "make a check point and pause". Every agent was stopped and every leftover test process killed. The suite lock was free.

Old agent IDs from the previous Claude session won't exist in a new session or account. Start fresh agents with the templates in `PROMPTS.md`.

## Progress
| | Stories |
|---|---|
| Built in total | **90 of 146** |
| On `main`, last fully verified | 74, at `e530b38` |
| On `main`, merged but not yet suite-verified | 8, see below |
| Mid-merge / still to merge in this batch | 8.4, 8.1 |
| Committed on tracks, waiting to merge | 6 |
| Stopped mid-story (uncommitted) | 4 |
| Not started | 52 |

By release:
- **Release 1:** 36 left: 33 not started + 3 mid-build.
- **Release 2:** 9 left.
- **Release 3:** 11 left.

Estimate for the rest: about 35–40 hours of building with 4 tracks. Release 1 alone is about 25 hours.

---

## A. `main` (`e:\Nextron AI outreach`) is MID-MERGE: finish this first

- **Last docs commit, fully verified:** `e530b38`. That is 18613 tests passing and migration head `0054_2_16_signal_inbox`.
- **Merge commits since then. Committed, but NO full suite has run on them yet:**

  | Commit | What it merges |
  |---|---|
  | `1874b10` | `e71c66e`, a test-only flake fix |
  | `5eaacc6` | Story 5.11, away dates (no migration) |
  | `917902e` | Story 11.5, edit Segments: `0055_11_5_segment_editing` |
  | `b6d0b48` | Story 11.7, edit Sequences: `0056_11_7_sequence_editing` |
  | `1af4568` | Story 11.8, edit Campaigns: `0057_11_8_campaign_editing` |
  | `3cd459b` | Story 9.6, the calls report |
  | `52f3c3f` | Story 9.8, Lead history |
  | `f8276b0` | Story 4.13, deterministic grounding rules: `0058_4_13_grounding_results` |
  | `f8beb44` | Story 4.14, AI grounding check: `0059_4_14_ai_grounding` |

- **In progress, not committed:** `git merge --no-ff a44bcf2` (Story 8.4, Companies House discovery).
  - `MERGE_HEAD` is set and 66 files are staged. **0 files are unmerged**, so every conflict was resolved.
  - Its migration is already renumbered to `0060_8_4_registry_discovery.py`.
  - The merge agent was stopped before it finished checking.
- **Still to merge in this batch:** `8819f87` (Story 8.1, budget classes).

### Resume steps for main
1. Start a merge agent with the **"Finish batch 4" prompt** in `PROMPTS.md`. It will:
   - Review the staged 8.4 merge. Confirm:
     - the renumbering and the chain pins;
     - that `provenance_records` is rebuilt from main's real shape, not 8.4's hard-coded 0031 snapshot;
     - that 8.4's discovery fields agree with 11.5's Segment discovery fields.
   - Run targeted tests, then commit the merge.
   - Merge `8819f87` (8.1):
     - mark main-only types `process_signal_item`, `find_contact_email` and `verify_contact_email` as `BudgetClass.BLOCKABLE`;
     - add an `ESSENTIAL_SAMPLES` entry for every essential type on main;
     - give fake budgets a `status()`;
     - add the `discovery` health source after `sending`.
   - Run ONE full suite through the lock and fix any failures (never weaken a test). Then commit.
2. When main is green, update `HANDOFF.md` and `README.md` for these 10 stories in one `docs:` commit.

---

## B. Committed on track branches, waiting to merge (next batch)

Merge by hash, in this order. Their parents may already be on main, which is fine.

| Story | Commit | Built on | Notes for the merge |
|---|---|---|---|
| 8.10 discovery top-up | `946d18a` | 8.1 `8819f87` | Migration `0040_8_10_discovery_topup` (rebuilds `discovery_runs`). `create_app(discovery_topup=)`. `UsageMeterPort.units_since`. Nothing triggers it until 8.9 is built. |
| 8.7 scheduled re-checks | `784622a` | main `e530b38` | Migration `0055_8_7_crawl_run_mode`. `recheck_lead` must get `budget_class=BLOCKABLE` (8.1). 9 triggers. May conflict with 11.5 in `app/scoring/api.py`. |
| 6.9 send gate | `28be53c` | `954f10f` | No migration. `create_app(sequencing=)`. `assert_sendable` gets an AD-16 scenario. Its fail-closed seams are filled by 5.4, 6.8 and 6.10. |
| 6.11 send pacing | `c1a45a2` | 6.9 | Migration `0046_6_11_inbox_pacing`. One shared `Sending` is built in `main.py`. New startup step `repace_cold_sends`. |
| 4.15 Step 1 drafts (Opus) | `564a027` | 4.14 `31596db` | Migration `0048_4_15_draft_failed`. `LlmPort.run(framing=)`. `Sequencing(clock, queue=)` is now required for enroll, so 4.10's `pick` must pass a queue. New route `draft_sequence` and job `draft_step`. |
| 5.1 approval queue | `e9ab781` | 4.14 `31596db` | No migration. `GET /queue`. `create_app(now=)`. `queue.html` should include 5.11's `away_banner.html`. |

Each story's spec, in the track worktree's `_bmad-output/implementation-artifacts/`, has its full report, decisions and "what the next story must know".

---

## C. Stopped mid-story: uncommitted work kept in the worktree

| Track | Worktree | Branch (base) | Story | Spec status | Where it stopped |
|---|---|---|---|---|---|
| A | `e:\Nextron-epic1` | `track-a11` (`e530b38`) | 8.11 Restart a completed Sequence only on a new Event ≥90 days later | in-progress | implementation underway |
| B | `e:\Nextron-epic2` | `track-b15` (`c1a45a2`) | 6.6 Pause inboxes on blocklistings and Postmaster spam rates | in-progress | targeted tests were running |
| D | `e:\Nextron-track4` | `track-d11` (`564a027`) | 4.17 Send bulk AI work through Message Batches | in-review | review done, triage log written, patcher not yet dispatched |
| E | `e:\Nextron-track5` | `track-e13` (`e9ab781`) | 5.2 Hold each approved Draft as exactly one queued OutboundMessage | in-review | review underway or done |

To resume one, start a fresh orchestrator with the **"Resume a stopped story" prompt** in `PROMPTS.md`. It inspects the dirty tree, the spec status and the triage log, then continues from the right workflow step.

**Track C** (`e:\Nextron-track3`, branch `track-c4`, clean) is idle. It was paused for machine load; keep at most 4 tracks.

---

## D. What to build next, once the merges land

The stories not yet built:

| Epic | Not started |
|---|---|
| 3 | 3.9 (spike, needs Mac) |
| 4 | 4.16 (needs Signal Inbox + 4.15), 4.18 (needs 4.17), 4.19, 4.20 (Regenerate; also fixes Step 1 drafts that fail with `no_route` and never recover), 4.21 (spike, needs Mac) |
| 5 | 5.3–5.10 (after 5.2) |
| 6 | 6.1 (spike, needs Mac), 6.2 Gmail connect, 6.3, 6.7 (needs 5.2), 6.8 mailbox sync, 6.10 send exactly once (needs 5.2, 6.9, 6.11), 6.12, 6.13 |
| 7 | 7.1–7.11, replies (most need 6.8 and 6.10) |
| 8 | 8.5 (spike), 8.6, 8.8, 8.9 daily selection (needs 8.10 + 4.10 + 5.11 on main; calls `discovery_topup.chain` and implements `ReadyPool`), 8.12 retention |
| 9 | 9.2, 9.3, 9.5 (need drafting) |
| 10 | 10.3–10.6, 10.8, 10.9 |
| 11 | 11.1–11.4 (funnel metrics, need sends and replies), 11.6 edit Markets (copy the 11.5/11.7/11.8 pattern; must publish 4.12's `FooterChanged`) |

Pick stories whose prerequisites are on `main` or on the same track's branch. Read `deferred-work.md` for the follow-ups each story leaves.

## E. Things waiting for Mac
- Delete the empty stray file `e:\_tmp_ignore`. The safety check blocked Claude from deleting it.
- Review the "Decisions made overnight (pending user review)" in each spec. `HANDOFF.md` lists the main ones.
- The spikes 3.9, 6.1 and 4.21 need Mac's Cloudflare, Google Workspace and Claude accounts.
- Open questions raised in the latest reports:
  - Story 11.8: no Offer editor once Campaigns are managed.
  - Story 5.1: "next due" can show a time already past.
  - Story 2.16: an author's name and role are accepted separately.
  - Story 8.4: the website lookup trusts the first Places match, and runs always restart at page 0.
