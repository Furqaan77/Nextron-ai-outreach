---
name: build-checkpoint
description: "Exact paused state of the parallel build (2026-11-19) — main mid-merge, per-track uncommitted work, and how to resume"
metadata:
  node_type: memory
  type: project
  originSessionId: 85872522-d5e0-4288-b624-7096a7dc9d2a
  modified: 2026-11-19T18:20:22.463Z
---

**Paused by Mac on 2026-11-19.** Mac said "make a check point and pause". All agents were stopped and the leftover track-D pytest processes were killed. The suite lock is free. **Do not resume until Mac says so.**

## Main (`e:\Nextron AI outreach`): mid-merge and unverified
- Last docs commit: `e530b38` (18613 tests, verified, head `0054_2_16_signal_inbox`).
- The batch-4 merge commits after it are **not verified by a full suite yet**:
  - `1874b10` (flake fix), `5eaacc6` (5.11), `917902e` (11.5), `b6d0b48` (11.7), `1af4568` (11.8), `3cd459b` (9.6), `52f3c3f` (9.8), `f8276b0` (4.13), `f8beb44` (4.14).
- In progress: the merge of `a44bcf2` (8.4). MERGE_HEAD is set and 66 files are staged; 8.4's migration is renumbered to `0060_8_4_registry_discovery`.
- Still to merge in this batch: `8819f87` (8.1).
- **To resume:** SendMessage the merge agent `a19bb00dce94e9039`. It has the full instructions: finish the 8.4 merge, merge 8.1, then run one full suite and fix anything that fails. Then update HANDOFF and README (docs_mergeN.py pattern in scratchpad).

## Committed on tracks, waiting to merge (next batch)
| Story | Commit | Built on |
|---|---|---|
| 8.10 | `946d18a` | 8.1 |
| 8.7 | `784622a` | main e530b38 |
| 6.9 | `28be53c` | 954f10f |
| 6.11 | `c1a45a2` | 6.9 |
| 4.15 | `564a027` | 4.14 |
| 5.1 | `e9ab781` | 4.14 |

## Stopped mid-story (uncommitted work kept in the worktree)
| Track | Worktree | Branch | Story | Dirty files | Resume by messaging |
|---|---|---|---|---|---|
| A | `e:\Nextron-epic1` | `track-a11` (base e530b38) | 8.11 | 28 | `a8ab34c218d5f86c1` |
| B | `e:\Nextron-epic2` | `track-b15` (base c1a45a2) | 6.6 | 43 | `a20486a8ffa4bd705` |
| D | `e:\Nextron-track4` | `track-d11` (base 564a027) | 4.17 | 49 | `a89492acb868c6b27` |
| E | `e:\Nextron-track5` | `track-e13` (base e9ab781) | 5.2 | 28 | `a96384ef6c957ff4b` |

- Track D had finished review and was about to dispatch its patcher.
- Track C (`e:\Nextron-track3`) is idle and clean. It is paused because of machine load.
- If an agent id no longer resolves, start a new orchestrator. Its prompt should tell it to inspect the dirty worktree and the spec status, then continue from there.

## Follow-ups noted
- Mac should delete the empty stray file `e:\_tmp_ignore`; the safety check blocked deleting it.
- Next stories once the merges land: 8.9 (needs 8.10 + main), 11.6, 4.16/4.18–4.20, 5.3+, 6.7/6.10, 9.2/9.3/9.5.
- The spikes needing Mac's accounts are still skipped: 3.9, 6.1, 4.21.

See [[autonomous-build-mode]] and [[agent-model-effort]].

**Full handoff package:** `e:\Nextron AI outreach\_handoff\` holds START-HERE.md, CHECKPOINT.md, PROMPTS.md, the rules, suite_lock.py and copies of the memory files. Git ignores it via `.git/info/exclude`. From now on the rule files there are the canonical copies.

**RESUMED 2026-11-19.** Mac said "read these and continue pls". Fresh agents were started, all opus/medium:
- the batch-4 merge (8.4 staged, then 8.1, then one full suite): `a98972fb7893496c6`;
- A 8.11: `a4b71a10b06119e35`;
- B 6.6: `aa2b5738acfca6ce7`;
- D 4.17: `a79335288a28f2e0f`;
- E 5.2: `ab796c16c5a149aba`.

The old scratchpad suite_lock.py now shares the `_handoff` lock folder. The old scratchpad rule files are now pointers to the `_handoff` copies.
- **Later on 2026-11-19:** Claude Code restarted again. All 5 agents were resumed by SendMessage, same ids, and the transcripts were kept. Track A's orphaned pytest run was killed first. Main was still mid-merge of 8.4, with an extra unstaged edit to `segments.py` that the merge agent will review.
- **After any restart:** check for orphaned Nextron pytest processes and a stale `_handoff\pytest-suite.lock` before resuming.
- **Batch 4 done:** `510f672` (8.4), `f597413` (8.1). Suite 19526 passed. Docs commit `219f1f4`. Head is 0060.
- **Batch 5 merge running** (agent `aa17211149a392ad7`): 8.10, 8.7, 6.9, 6.11, 4.15, 5.1.
- **C: nearly full (8 GB free).** suite_lock adds `--basetemp` on E:, and targeted runs must use `--basetemp` on E:. The `_handoff\CHECKPOINT.md` "LATEST STATE" block is kept current.
- **Third restart, 2026-11-19 ~20:00. Root cause found:** suite_lock treated any hold over 30 min as stale. Full suites now take about 30 min, so two waiters both took the lock: tracks B and E each ran `-n 8` at once, memory peaked (swap 9.8 GB on D:), and Claude Code died.
  - **Fix, in both script copies:** a lock is stale only when its holder pid is dead (ctypes check), with a 4 h backstop. The owner is re-checked before clearing, and the wait limit is 6 h.
  - 29 orphaned test processes were killed and the stale lock removed.
  - Old `pytest-of-mac` temp folders on C: are being deleted (C: was down to 3.9 GB free).
  - All 5 agents were resumed by SendMessage: merge `aa17211149a392ad7` (mid-merge of 8.10), A 8.11, B 6.6, D 4.18 `aec6e78f3de8b3a09`, E 5.2.
- **Fourth restart, 2026-11-19 ~21:25.** RAM had dropped to 0.5 GB free. Recovery:
  - Killed the orphaned Nextron test runs; RAM back to 6.1 GB.
  - **Concurrency reduced:** 3 tracks + the merge. Track A (8.11, Release 2, agent `a4b71a10b06119e35`) is PAUSED with uncommitted work in `e:\Nextron-epic1` (track-a11).
  - Full suites now use `-n 6`.
  - C: has 59 GB free after deleting the old pytest-of-mac folders.
  - Main: 8.10 merged (`c8e99d8`), mid-merge of 8.7.
  - Agents resumed: merge `aa17211149a392ad7`, B 6.6 `aa2b5738acfca6ce7`, D 4.18 `aec6e78f3de8b3a09`, E 5.3 `aff4ef6dd6af9bc33`.
- **Later on 2026-11-19:** suite_lock was lowered to `-n 4` (RAM). New track stories:
  - B 6.2 `a3b4e177598c36707`;
  - D 4.20 `aab27ab557dec2b13`;
  - E 5.4 `a74e58e9add3d1f6e`.

  Waiting to merge: 6.6 `59433fc`, 4.18 `640bb27`, 5.3 `e183ef5`.
- **Mac plans to move the build to a stronger PC.**
  - Files: `_handoff/MOVE-TO-NEW-MACHINE.md`, `pack_for_move.ps1` and `setup_new_machine.ps1`.
  - On "prepare to move": start no new stories, let the running ones and the merge finish, then report "build idle, safe to pack".
- **2026-11-20:** at about 01:15 the API 5-hour limit stopped every agent (it resets at 02:30 IST), and Claude Code restarted. At 09:45 the stale lock was cleared and all agents were resumed by SendMessage.
  - **Batch 5 is verified:** 20127 tests. Docs commit `e4feb56`. Head `0064`.
  - **Batch 6 merge** (`a2777d1fd245fadee`): 4.17, 4.18, 5.2, 5.3, 6.6.
- **PAUSED on 2026-11-20 ~12:40 IST for the move to a new PC** (Mac's request). Every agent was stopped, test processes killed and the lock removed.
  - **Main:** `5f2fa61`, clean. Batch 6 (4.17, 4.18, 5.2, 5.3, 6.6) is merged but has NO full suite yet. Head 0067.
  - **Waiting to merge:** 6.2 `98ac14e`, 5.4 `408a167`, 4.16 `edd6b52`.
  - **Mid-story:** A 8.11, B 4.19, D 4.20, E 5.6.
  - **Canonical state:** `_handoff/CHECKPOINT.md` (rewritten). The guide is `_handoff/MOVE-TO-NEW-MACHINE.md`.
