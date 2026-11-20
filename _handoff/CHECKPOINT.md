# CHECKPOINT: build PAUSED on 2026-11-20 ~12:40 IST to move to a new PC

Mac said: "pause it, we will do it on other machine". **Every agent was stopped** and every leftover test process killed. The suite lock was removed. Nothing is running.

**On the new PC, do this first:**
1. Follow `MOVE-TO-NEW-MACHINE.md` (pack, copy, `setup_new_machine.ps1`).
2. Read `START-HERE.md`.
3. Come back here.

**Old agent ids do not exist on the new PC.** Start every agent fresh from the templates in `PROMPTS.md`. The previous checkpoint, with the full history of 2026-11-19/20, is in `history/CHECKPOINT-2026-11-19-to-20.md`.

## Progress
| | Stories |
|---|---|
| Built and committed (on main or on a track branch) | **98 of 146** |
| On `main`, last fully verified | 90, at `e4feb56` (20127 tests) |
| On `main`, merged but NOT yet suite-verified | 5: 4.17, 4.18, 5.2, 5.3, 6.6 (main = `5f2fa61`) |
| Committed on tracks, waiting to merge | 3: 6.2, 5.4, 4.16 |
| Stopped mid-story (uncommitted work kept in the worktree) | 4: 8.11, 4.19, 4.20, 5.6 |
| Not started | 44 |

By release:
- **Release 1** (Epics 1–7, first real sends): 28 left, of which 3 are mid-build (4.19, 4.20, 5.6).
- **Release 2:** 9 left, 8.11 mid-build.
- **Release 3:** 11 left.

---

## A. `main` (`E:\Nextron AI outreach`): clean, needs ONE full suite

- **HEAD:** `5f2fa61`. `git status` is clean, with no merge in progress.
- **Batch 6 is merged** by commit hash, in this order:

  | Merge | Story |
  |---|---|
  | `cd88b82` | 4.17 `8318a90`, Message Batches |
  | `182f3c0` | 4.18 `640bb27`, follow-up drafts |
  | `67ae37b` | 5.2 `fb8d52b`, one queued OutboundMessage per approved Draft |
  | `eb49cdb` | 5.3 `e183ef5`, Evidence and grounding on cards |
  | `5f2fa61` | 6.6 `59433fc`, blocklist and spam-rate pauses |

- **Migration head:** `0067_6_6_reputation`. The renumbering was 4.17 → `0065_4_17_ai_batches`, 5.2 → `0066_5_2_outbound_messages`, 6.6 → `0067_6_6_reputation`. **The next migration is 0068.**
- **Each merge passed its targeted checks** (logs `scratch\main-merge\t7..t10`).
- **The final full suite NEVER RAN.** The merge agent was waiting for the lock when the build was paused. The merge agent's final report was lost, so the new merge agent must check the merges itself.
- **Spot checks done at pause:**
  - 4.18's follow-ups read the bound Sequence version through `sequence_version(enrollment.sequence_version_id)` (`app/drafting/generation.py` ~line 307). The 11.7 fix is in.
  - `queries.approval.current_approval` is still the `None` stub. Story 5.4 wires it in batch 7.

### Resume steps for main
1. Start a merge agent (PROMPTS.md §4 style). It should:
   - read the five batch-6 merge commits (`git show --stat`, plus the spec "merge notes" in the history file);
   - check the pins: chain probes for head 0067, `ESSENTIAL_SAMPLES` for `submit_bulk_ai` / `poll_ai_batches` / `draft_step`, the settings lists with `bulk_ai_submit_at`, `draft_deadline` and `dnsbl_zones`, and PORT_FAKES with `FakePostmaster`;
   - then run **one full suite** through `suite_lock.py` (label `main-merge`), fix any failure with a `fix(merge): …` commit, and re-run.
2. When it's green, the main session writes the docs commit for these 5 stories: HANDOFF.md and README.md, copying `scratch\main-merge\docs_merge18.py` as `docs_merge19.py`.
   - The HANDOFF anchors are "**Story 5.1** (merge `1aef1c3`)", "Main now: **20127 tests**", "of which 74 are done" and "- **Story 3.12:**".
   - 4.17, 4.18, 5.2, 5.3 and 6.6 are all Release 1 stories, so the count goes from 74 to 79.
3. Then merge **batch 7** (below).

---

## B. Committed on tracks, waiting to merge (batch 7)

Merge by hash, in this order:

| Story | Commit | Built on | Notes for the merge |
|---|---|---|---|
| 6.2 Gmail connect | `98ac14e` (`E:\Nextron-epic2`) | 6.6 `59433fc` | Migration `0048_6_2_inbox_connect` → renumber to 0068. Adds `sending_inboxes.status_changed_at`, NOT NULL with no default, so every raw INSERT fixture on main must set it. Also adds `connected_at`. See notes below the table. |
| 5.4 approve | `408a167` (`E:\Nextron-track5`) | 5.3 `e183ef5` | Migration `0049_5_4_approvals` → 0069. See notes below the table. |
| 4.16 Signal Inbox to draft | `edd6b52` (`E:\Nextron-epic2`) | main `e4feb56` | Migration `0065_4_16_draft_when_ready` → 0070. See notes below the table. |

**6.2 merge notes:**
- New setting `gmail_internal_app_confirmed` (default false; Connect refuses until Spike 6.1).
- `Sending(clock, *, env, gap_source=None, domain_checks=None, notify=None)`.
- New `InboxConnections(...)`.
- `build_app(gmail=)`.
- Doctor check `sending_inbox_grants`, registered last.
- `GmailPort` is in `APPROVED_EXTRA_PORTS`.
- The domain check is queued on connect.

**5.4 merge notes:**
- `create_app(approval=)` is required. `build_app` passes `Approval(clock, sending=sending)`.
- Setting `approval_session_idle` must be in every all-settings test post.
- **Wire 6.9's stub:** `queries.approval.current_approval(draft_id)` must return `approval_of(draft_id)` mapped to `ApprovalView`, with `text_hash` → `approved_hash`.
- Cascades cancel queued OMs through `sending.api.cancel_for_cascade`.
- Adds `static/queue-keys.js` and the `{% block scripts %}` in `base.html`.

**4.16 merge notes:**
- `LeadScores(..., selection=, discovery=)` and `Selection.pick_one`.
- Its 15-minute speed test must use 4.18's `draft_sequence` v2 default.
- `draft_deadline` and the 4.17 settings join its settings lists.

Each story's spec, in that worktree's `_bmad-output/implementation-artifacts/`, has its full report.

---

## C. Stopped mid-story: uncommitted work kept in the worktree

Resume each with the **"Resume a stopped story"** prompt in `PROMPTS.md`. It inspects `git status`, the spec status and the triage log, then continues from the right bmad-build step.

| Track | Worktree | Branch (base commit) | Story | Spec status | Where it stopped |
|---|---|---|---|---|---|
| A | `E:\Nextron-epic1` | `track-a11` (`e530b38`, old main) | 8.11 Restart a completed Sequence only on a new Event ≥90 days later | in-review | review/triage stage; 31 dirty files. Paused since 2026-11-19 for RAM. |
| B | `E:\Nextron-epic2` | `track-b18` (`edd6b52` = 4.16) | 4.19 Segment fallback copy for hand-picked Tier C Leads | in-progress | implementation underway (34 dirty files) |
| D | `E:\Nextron-track4` | `track-d13` (`640bb27` = 4.18) | 4.20 Regenerate a Draft and flag stale ones | in-review | implementation done, review underway (40 dirty files) |
| E | `E:\Nextron-track5` | `track-e16` (`408a167` = 5.4) | 5.6 Edit a Draft, then approve it once the recheck passes | spec written | implementer had just been dispatched (34 dirty files) |

**Track C** (`E:\Nextron-track3`, branch `track-c4`, clean) is idle.

**Story-specific context for the resume prompts** (also in each spec):
- **4.19:**
  - must match 5.3's `generic` meaning;
  - waive the "exactly one Signal" grounding rule for generic Drafts;
  - follow-ups (4.18) and Regenerate (4.20) are merge touchpoints.
- **4.20:** `drafting.regenerate(draft_id, instruction)` must stay clean for 5.5. It should recover Drafts stuck on `no_route`.
- **5.6:** `approval.complete_pending` must reuse 5.4's AD-6 check path, and an approval must trace back to Mac's Ctrl+Enter. `complete_pending_approval` is an essential job (8.1).

---

## D. What to build next

**Load:** on a stronger PC, raise the limits first (`MOVE-TO-NEW-MACHINE.md` §5).

**After batch 7 lands,** the critical path to the first real send is Epic 6:

| Story | What it needs |
|---|---|
| 6.10 send exactly once | 5.2, 5.4, 6.9, 6.11 (all on main after batch 7) |
| 6.3 pause/resume/remove inboxes | 6.2 + 5.2 |
| 6.7 plain-text messages, one Thread per Contact | 5.2 |
| 6.8 mailbox sync every 2 minutes | 6.2 |
| 6.12 reconcile uncertain sends | 6.10 |
| 6.13 bounce pauses | 6.8 |

The rest:
- **Epic 5:** 5.5 reject/skip/regenerate (needs 4.20), 5.7 keyboard, 5.8 batch-approve follow-ups, 5.9 expire stale approvals, 5.10 regenerate stale Drafts (needs 4.20).
- **Epic 7, replies (7.1–7.11):** most need 6.8 and 6.10.
- **Epic 8:**
  - 8.9 daily selection (its deps 8.10, 4.10 and 5.11 are on main; it calls `discovery_topup.chain` and implements `ReadyPool`);
  - 8.6, 8.8, 8.12 retention;
  - 8.5 spike (mostly from public docs).
- **Epic 9:** 9.2, 9.3, 9.5.
- **Epic 10:** 10.3–10.6, 10.8, 10.9.
- **Epic 11:** 11.1–11.4 (funnel metrics, need sends and replies), 11.6 edit Markets (copy the 11.5/11.7/11.8 pattern; must publish 4.12's `FooterChanged`).
- **Spikes that need Mac's accounts:** 3.9 (Cloudflare), 6.1 (Google Workspace), 4.21 (AI cost).

Pick stories whose prerequisites are on `main` or on the same track's branch. Read `deferred-work.md` for follow-ups.

---

## E. Things waiting for Mac
- **Story 4.16:** a Lead created only from a pasted Signal Inbox post has no `country_code`, so it gets no Market, is refused (`missing_tz_or_market`) and is never auto-drafted. Decide how such a Lead gets its country; this needs a small follow-up story.
- **Story 6.2:**
  - Connect stays off until `gmail_internal_app_confirmed` is set after Spike 6.1. The spec lists what 6.1 must confirm.
  - Should Sending Domains on free-mail providers (gmail.com etc.) be refused? Undecided.
- **Story 5.4:** reply and rebook Drafts can't be approved until Epic 7. `edit_ratio` is measured against revision 1.
- **Story 6.6:** the default blocklist zone (Spamhaus DBL) may refuse queries from public DNS or commercial use, which would give a permanent "query refused" warning.
- **Story 4.18:** follow-ups fail `no_route` until a `draft_sequence` v2 route passes the 4.5 gate.
- **Earlier items:**
  - 11.8: no Offer editor.
  - 11.5: seed sync stops writing Segments.
  - 5.1: "next due" can show a past time.
  - 2.16: author name and role are accepted separately.
  - 8.4: the website lookup trusts the first Places match.
  - 4.14: grounding fails `checker_unavailable` until a route passes the gate.
  - 3.12: a backup older than 30 days loses opt-outs.
- **On the old PC:** delete the stray empty file `e:\_tmp_ignore`.
- **Each spec** has "Decisions made overnight (pending user review)".

## F. Lessons from the old PC (16 GB RAM)
- **Memory:** it crashed four times from memory exhaustion. It ended at 3 tracks + 1 merge, with full suites at `-n 4` and targeted runs at `-n 2`. VS Code itself used about 3.4 GB.
- **Usage limit:** the Claude 5-hour limit stopped every agent once (2026-11-20 ~01:15). After a limit or a restart:
  1. kill orphaned Nextron python/pytest/uv processes (match "Nextron" in the command line);
  2. remove `_handoff\pytest-suite.lock` if its pid is dead;
  3. resume agents with SendMessage, or start fresh agents from PROMPTS.md.
- **Full suite timing:** about 20,000 tests, taking 45–50 min at `-n 4–6` under load.
