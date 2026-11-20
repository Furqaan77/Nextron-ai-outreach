# Moving the Nextron build to a new PC

This moves **everything**: the repo, every track worktree with its uncommitted work, this `_handoff` folder, and Claude's memory notes. On the new PC, a fresh Claude reads `START-HERE.md` and `CHECKPOINT.md` and carries on exactly where the old PC stopped.

**Short version:**
1. Tell Claude on the old PC: **"prepare to move"**.
2. Run `pack_for_move.ps1` onto a USB drive.
3. Copy the folders to `E:\` on the new PC.
4. Run `setup_new_machine.ps1`.
5. Open VS Code there and tell Claude to continue.

---

## 0. What the new PC needs

| Need | Why |
|---|---|
| **Windows 10 or 11** | The architecture (AD-1..AD-22) is built for a local Windows PC: Windows keyring, `%LOCALAPPDATA%\Nextron`, Task Scheduler for the real install, and Windows process checks in tests. macOS or Linux would be a planning change. Ask Claude to review the architecture first; don't just copy. |
| **An `E:` drive** (best) | Every path in `_handoff` and the agent prompts says `E:\...`. With an `E:` drive, nothing needs changing. Any other place works too: `setup_new_machine.ps1 -Parent "D:\Projects"` rewrites the paths. |
| **About 15 GB free** | The folders, plus a rebuilt `.venv` per worktree. Test temp files go under `_handoff\scratch`. |
| Git for Windows | https://git-scm.com/download/win |
| uv | https://docs.astral.sh/uv/getting-started/installation/. The script installs Python 3.14 itself. |
| VS Code + Claude Code extension | Sign in with the Claude account you want to use. |

**Nothing secret moves, because nothing secret exists yet.**
- No real accounts are connected. The spikes 3.9, 6.1 and 4.21 still wait for Mac.
- Mac's real `%LOCALAPPDATA%\Nextron` was never created.
- Tests use a throwaway test keyring.
- When the engine is later set up for real, that setup happens on whichever PC runs it.

---

## 1. On the OLD PC: stop the build cleanly

**Done on 2026-11-20 ~12:40 IST.** Mac said "pause it": all agents were stopped and test processes killed, `main` is clean, and `CHECKPOINT.md` records everything. Go straight to step 2.

Tell Claude one of these:

- **"prepare to move"** (recommended). Claude:
  - starts no new stories;
  - lets each running story finish and commit;
  - lets the current merge into `main` finish;
  - updates `CHECKPOINT.md` and the memory notes;
  - tells you when the build is idle.

  This can take a few hours, because a story runs for 1–2 hours.
- **"pause now for the move"**. Claude:
  - stops every agent at once and kills leftover test processes;
  - records each half-built story in `CHECKPOINT.md`.

  The uncommitted work moves with the folders. On the new PC it is resumed with the "Resume a stopped story" prompt in `PROMPTS.md`. If `main` is mid-merge, the merge is either finished or aborted first, so `main` moves in a clean state.

Either way, Claude must confirm **"build idle, safe to pack"** before step 2.

## 2-ALT. Easier: pull everything from GitHub (no USB needed)

Everything is pushed to https://github.com/Furqaan77/Nextron-ai-outreach:

| Branch | What it holds |
|---|---|
| `main` | The integration branch |
| `track-*` | Each track's current branch |
| `wip/track-*` | The half-built stories, stored as snapshots |
| `handoff` | This `_handoff` folder |

On the new PC:
1. Install Git for Windows and uv (section 0).
2. Download `restore_from_github.ps1` from the `handoff` branch:
   https://github.com/Furqaan77/Nextron-ai-outreach/raw/handoff/_handoff/restore_from_github.ps1
3. Run it:
   ```powershell
   powershell -ExecutionPolicy Bypass -File restore_from_github.ps1
   ```
   Pass `-Parent "D:\Projects"` if there is no E: drive.

The script:
- clones `main` to `E:\Nextron AI outreach`;
- unpacks `_handoff`;
- recreates the five track worktrees and puts the half-built work back as uncommitted changes;
- then runs `setup_new_machine.ps1` (step 3 below).

After that, skip to step 4.

## 2. On the OLD PC: pack (USB route)

Plug in a USB drive or external disk (say it's `F:`), then run in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "E:\Nextron AI outreach\_handoff\pack_for_move.ps1" -Dest "F:\NextronMove"
```

It refuses to run while Nextron tests are still running. Into `F:\NextronMove` it writes:
- `Nextron AI outreach\`: the main repo, including all git history and this `_handoff` folder;
- `Nextron-epic1`, `Nextron-epic2`, `Nextron-track3`, `Nextron-track4` and `Nextron-track5`: the track worktrees, with their uncommitted work;
- `nextron-all-branches.bundle`: a safety copy of every commit on every branch;
- `MANIFEST.txt`: each worktree's branch, last commit and uncommitted files, to compare on the new PC.

It leaves out the `.venv` folders, caches and test temp files; the new PC rebuilds them. It also refreshes `_handoff\memory\` from Claude's live memory notes.

## 3. On the NEW PC: copy and set up

1. Install Git, uv, VS Code and Claude Code (section 0).
2. Copy the five `Nextron-*` folders and `Nextron AI outreach` from `F:\NextronMove\` to the **root of `E:\`**. The folder names must stay exactly the same, all next to each other.
3. Run in PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File "E:\Nextron AI outreach\_handoff\setup_new_machine.ps1"
   ```
   If you used another drive, run instead:
   ```powershell
   powershell -ExecutionPolicy Bypass -File "D:\Projects\Nextron AI outreach\_handoff\setup_new_machine.ps1" -Parent "D:\Projects"
   ```

   It:
   - checks the tools and installs Python 3.14;
   - re-links the git worktrees;
   - rewrites the `_handoff` paths if the drive changed;
   - rebuilds each `.venv`;
   - restores Claude's memory notes;
   - runs the import-contract check;
   - lists the uncommitted work it found.

   It ends with **READY** or a list of problems. It is safe to run again.
4. Compare its last section with `F:\NextronMove\MANIFEST.txt`. The branches and dirty-file counts should match.

## 4. On the NEW PC: continue the build

Open VS Code at `E:\Nextron AI outreach`, open Claude Code, and say:

> We moved to a new PC. Read `_handoff/MOVE-TO-NEW-MACHINE.md` and `_handoff/START-HERE.md`, then continue the build.

**What the new Claude must do first:**
1. Read `START-HERE.md`, then `CHECKPOINT.md`, its LATEST STATE block especially.
2. Old agent ids don't exist on the new PC. Start fresh agents from the templates in `PROMPTS.md`:
   - "Build a new story" for idle tracks;
   - "Resume a stopped story" for tracks with uncommitted work.
3. Set the load for the new PC (section 5) before launching anything.
4. Run one full suite on `main` through `suite_lock.py` (label `main-check`) to prove the new PC is green before merging anything.

**Don't push.** `origin` is still at `67b8431`, and pushing needs Mac's explicit ask. Using GitHub to move the code would also leave behind the uncommitted work and the git-ignored `_handoff` folder. That's why this guide copies folders instead.

## 5. Use the stronger PC: raise the load limits

The old PC (16 GB RAM) crashed four times from memory exhaustion. It ran at most 3 tracks plus a merge, with full suites at `-n 4`. On a stronger PC, raise the limits step by step.

| New PC RAM | Tracks at once | Full-suite workers (`suite_lock.py`) | Targeted runs |
|---|---|---|---|
| 16 GB | 3 + merge | `-n 4` | `-n 2` |
| 32 GB | 4 + merge | `-n 8` | `-n 4` |
| 64 GB or more | 5 + merge | `-n 12` | `-n 4` |

Where to change it:
- **Full-suite workers:** `_handoff\suite_lock.py`, the line `extra = ["-n", "4", "--dist", "loadgroup", *extra]`.
- **Targeted-run limit and track count:** the "Machine load" sections of `_handoff\track-rules.md` and `_handoff\merge-rules.md`, and section 4 of `START-HERE.md`.

Only **one** full suite runs at a time, whatever the PC. The tests share a fixed port, and `suite_lock.py` enforces this. More CPU cores make each full suite faster; more RAM allows more tracks.

**Track C** (`E:\Nextron-track3`, idle) and **Track A** (8.11, paused with uncommitted work) can be restarted once the load allows.
