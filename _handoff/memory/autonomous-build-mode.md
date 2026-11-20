---
name: autonomous-build-mode
description: "The user asked for continuous, unattended story-by-story builds; treat BMAD checkpoints as pre-approved until they say stop. Since 2026-11-17 the build runs as two parallel tracks."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 85872522-d5e0-4288-b624-7096a7dc9d2a
  modified: 2026-11-18T05:17:17.124Z
---

On 2026-11-17 the user said: "continue the development... I don't want you to stop... stop only when I say or you have reached the limit."

**Why:** planning is complete and the user is away. They want progress through the remaining stories without waiting for approvals.

**How to apply:**
- Run /bmad-build in sprint order.
- Treat spec approval and the token-size gates as "approve and continue / keep as one".
- If a story has a real intent gap, pick the conservative, compliance-safe option and record it under "Decisions made overnight" in the spec, for review.
- Keep full verification and three-layer review.
- Commit locally per story. Never push to GitHub unless asked.
- Keep HANDOFF.md and sprint-status.yaml current. See [[outreach-engine-constraints]].

## Speed mode: two parallel tracks

On 2026-11-17 the user asked "can we increase the speed" and chose "Two at once". This overrides CLAUDE.md's "one story at a time".

**Tracks:**
- **Track A** is Epic 1's tail (1.16–1.21) in `e:\Nextron AI outreach` on branch `main`.
- **Track B** is Epic 2 onward in the git worktree `e:\Nextron-epic2`, on branch `epic-2`.
- Each track has one orchestrator agent per story.

**Suite lock:** every full suite runs through the scratchpad `suite_lock.py`, because both tracks share the fixed test port 8767.

**Merging:**
- Merge `epic-2` into `main` after a track-A story commits and before the next one starts.
- Fast-forward or merge `main` into `epic-2` when track B is between stories.
- Migration ids collide: both tracks take the next number. At merge time, renumber and re-chain the later one.
- Root HANDOFF.md and README.md are edited only on `main`.

**Lean steps:**
- Orchestrators read fewer specs.
- No duplicate full-suite run by the main session; check git state and the agent's report instead.

## Even faster (2026-11-17)

The user asked for "even faster" and chose a third track plus faster tests. The pytest-xdist chore runs in worktree `e:\Nextron-chore`, branch `chore-xdist`.

**Integration design:**
- **`main` becomes integration-only.** `e:\Nextron AI outreach` (branch `main`) is used only for merging once Story 1.16 commits there.
- **Track A** moves to worktree `e:\Nextron-epic1` (branch `epic-1`).
- **Track C** is worktree `e:\Nextron-track3` (branch `track-3`), created from main after Story 2.1 is merged.
- **Each story:**
  1. merge the track's branch into main;
  2. renumber and re-chain its new migrations after main's head, keeping file names `NNNN_<story>` matched to revision ids (`test_persistence_contracts`);
  3. run the full suite under the lock, and commit;
  4. fast-forward the idle track branch to main before launching its next story.

**Track C queue** (from the dependency analysis):
1. 3.1, 4.1, 4.3, then 4.11. Defer 4.11's Lead-page "Sending blocked" clause to 4.12.
2. 4.2 and 4.7, after 2.1 and 2.2 are merged.

**Track D** (added on the user's "much faster without compromising quality" request): worktree `e:\Nextron-track4`, branch `track-4`. It builds Epic 2's AI chain, 2.7 then 2.8, then possibly 2.5 and 2.6 (the crawler) once 2.2 is merged.

**Tracks don't idle for merges.** A track starts its next story right away when it doesn't need the content being merged. Shared rules for every track are in the scratchpad `track-rules.md`, which each prompt references.

**Track E** is in worktree `e:\Nextron-track5`, branch `track-5`. It was added on 2026-11-17 after the user asked "faster without losing quality", and started with Story 4.7.

**Full-suite policy (`track-rules.md` §4a).**
- Implementers and patchers run only targeted tests, contracts and lint-imports.
- The orchestrator runs the full suite once, on the final tree.
- Each merge into main runs the full suite again.
- `suite_lock` uses `-n 8`.

**Branches can change.** When a track's next story doesn't depend on its own unmerged work, its worktree switches to a fresh branch off `main`. For example:
- `e:\Nextron-epic2` is now on `track-b5` (Story 3.10, Worker code only, no deploy). `track-b4` holds 3.14 (`95e0118`), which needs its own merge after the current 5-merge batch;
- `e:\Nextron-track5` is now on `track-e5` (Story 3.16). 2.12 (`cd48494`, `track-e4`) and 2.11 (`35e55f1`, `track-a3`) are queued to merge. Earlier this checkout was on `track-e4` (Story 2.12); `track-5b` is merged. `track-e3` holds 3.15 (`fd10bea`), which awaits merge after 3.13. The unify steps are in the scratchpad parent folder, at `...\aa4d8b4ed58855c9\scratchpad\merge-3-15-unify.md`;
- **Main status, 2026-11-18:** `505c05b` has 47 stories. Merging now: 6.5 (`06bd536`) and 3.15 (`fd10bea`, unified with 3.13).
- `e:\Nextron-track4` is now on `track-d3` (Story 2.13). `track-d2` (6.5, `06bd536`) is awaiting merge, and `track-4` is merged;
- `e:\Nextron-epic1` is now on `track-a3` (Story 2.11); `track-a2` is merged or merging;
- `e:\Nextron-track3` is now on `track-c3` (Story 4.8). `track-c2` holds 4.6 (`7b4b0b0`), which is queued to merge after 6.5 and 3.15.

**Epic 1 is fully built** (1.1–1.21) as of 2026-11-18.

**Safety lessons.**
- During the Story 1.20 spike, a subagent accidentally registered a real scheduled task. It was removed and verified gone.
- Since then, `track-rules.md` §5a forbids system-state changes on the PC.
- Since 1.21, a contract enforces this for Task Scheduler.

**Merging a branch another agent will extend.** Always merge it by commit hash, not branch name.

**Reusable prompt rules.** The scratchpad holds `track-rules.md` and `merge-rules.md`.

**Merging a branch another agent will extend.** Merge it by commit hash, not branch name.

**Track C exclusions and notes:**
- Skip 3.8: it needs a planning change (`key_hash`).
- Skip 6.5: `DnsPort` is built in 2.14.
- Declare `us_solicitation_label` once, in 3.1.
- Tell Track B's 2.3 that `contacts.market` may link to 3.1's `markets` table.

## Status 2026-11-18 (later)
- Main `1d1735d`: 3.14 + 4.8 merged (head `0038_4_8_enrollments`, 15567 tests). HANDOFF/README docs for 3.14, 4.8 and the batch below are still due.
- Batch merge in progress, by hash and in this order: 3.10 `fa464e6`, 3.16 `4d076b9`, 6.4 `f84570f`, 2.13 `e13e7e4`, 2.15 `d128e7c`, 4.9 `923ee90`. Each merge runs targeted tests; one full suite runs at the end.
- Tracks:
  - A: `e:\Nextron-epic1` `track-a4`, Story 8.2.
  - B: `e:\Nextron-epic2` `track-b7`, Story 10.1.
  - C: `e:\Nextron-track3` `track-c4`, Story 4.12 (built on 4.9).
  - D: `e:\Nextron-track4` `track-d4`, Story 3.11.
  - E: `e:\Nextron-track5` `track-e6`, Story 9.7.
- Since the user asked on 2026-11-18, every agent runs with model opus and effort max (see [[agent-model-effort]]).
- Spikes 3.9, 6.1 and 4.21 need Mac's real accounts and are skipped.
- Next after the merges: 2.14 (needs 2.13), 2.16 (needs 2.15), 3.12 (needs 3.11), 4.10, the 9.7 campaign filter and lead_score sort.
- **2026-11-18 overload:** Claude Code crashed (0xC0000409) and Windows ran out of resources (WinError 1450); the full suite took about 28 min. Track C is paused, so 4 tracks are running, and targeted runs are capped at `-n 2` (track-rules §4a). Track C's next story is 4.13, on top of 4.12 `01e0ac5`.

**PAUSED 2026-11-19 at Mac's request ("make a check point and pause").** Don't resume the unattended build until Mac says so. The exact state is in [[build-checkpoint]].

**RESUMED 2026-11-19.** After switching Claude accounts, Mac said "read these and continue pls". The unattended build is running again, with 4 tracks and 1 merge agent. The canonical rules, prompts and lock now live in `e:\Nextron AI outreach\_handoff\`.

**PAUSED on 2026-11-20 ~12:40 IST for the move to a new PC.** Mac said: "pause it, we will do it on other machine".
- Don't resume on the old PC.
- On the new PC, read `_handoff/START-HERE.md` and `_handoff/CHECKPOINT.md`, then continue unattended. The standing instruction stays: build until Mac says stop.
