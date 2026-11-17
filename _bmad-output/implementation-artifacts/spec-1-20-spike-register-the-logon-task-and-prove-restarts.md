---
title: 'Story 1.20: Spike: register the logon task and prove crash and stale-heartbeat restarts (OQ-A9)'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '339434eebc4feb67c44f6d5a5be9fbc5018c00f1'
review_loop_iteration: 0
story_key: '1-20-spike-register-the-logon-task-and-prove-crash-and-stale-hear'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-20's supervision has never been tried on this PC (OQ-A9). It assumes four things: a non-elevated logon task registers with the AD-20 parameters, a supervisor loop restarts `nextron serve` with backoff, it kills the engine on a stale heartbeat, and it stops on exit code 3. Story 1.21 would otherwise be built on those assumptions, including that killing `uv` stops the engine and that a PC wake or a slow start doesn't look like a hang.

Requirements: AD-20, OP-3, OP-4 · NFR-14, NFR-42.

**Approach:** A throwaway spike under `nextron-engine/scripts/spike/` with three parts:
- a fake `serve` (stdlib Python) that touches a heartbeat file and can be told to stall or exit;
- a throwaway supervisor loop, `supervisor.ps1`;
- one control script, `oq_a9.ps1`, which prints (dry run), registers, drives, inspects and unregisters the spike task.

Pytest drives the supervisor with the fake. A contained live proof registers a throwaway on-demand task, runs the cases through Task Scheduler and always unregisters it. The steps that need Mac at the PC are scripted for him. `docs/spikes/oq-a9-logon-task.md` records the evidence and a go or no-go.

The acceptance criteria are Story 1.20 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Non-elevated only.** `oq_a9.ps1` refuses to run elevated (exit 2), since an elevated run proves nothing for OQ-A9.
- **The production task is never touched.** No spike path registers, starts, stops or removes a task named `Nextron Engine (prod)`, and `-TaskName` refuses that name in any case (exit 2).
- **Tests stay away from the machine.** The automated tests never call a Task Scheduler cmdlet and never start `uv`. They run the fake with the test's Python. Each test:
  - kills every process it started (the whole tree) in a `finally`;
  - uses `tmp_path` only and binds no port;
  - finishes in seconds.
- **The spike is self-contained.** The fake and the supervisor import nothing from `nextron` and never open a Nextron data dir.
  - Mac's run uses `%LOCALAPPDATA%\Nextron\spike`, and the contained proof uses a fresh temp folder.
  - The child gets `NEXTRON_ENV=test` unless `-Env` says otherwise.
- **Windows PowerShell 5.1 syntax** (`powershell.exe`, on every Windows 11 PC).

**Never:**
- No change under `nextron/`, and no migration, setting, credential or dependency.
- No `scripts/run.ps1` and no `scripts/install_logon_task.ps1`; those are Story 1.21.
- No system-wide setting changes. The execution policy is given per process with `-ExecutionPolicy Bypass`.
- No logon trigger is registered by the build. Only Mac's `-Action Register` makes one.

### Decisions made overnight (pending user review)

1. **What the build proves, and what Mac does.** The build doesn't register the `-AtLogOn` task, sign out and in, or sleep the PC. Those are Mac's steps, with exact commands in the README and the decision record. The record's verdict is "conditional go" until Mac fills in those rows. Everything else is proven by tests and by a contained on-demand task.
2. **Stale means no heartbeat change, counted in the supervisor's own polls, not the file's age.**
   - Each poll that sees the same mtime adds one poll interval. A change resets the count. The child is killed at `StaleAfterSeconds` (300).
   - A poll's wait doesn't count time asleep, so a wake or a file left stale by the previous child can't trigger a kill.
   - Until the new child's first beat, the allowance is `StartGraceSeconds` (spike default 900), so a slow `pre_migrate` backup, migration or recovery startup pass isn't killed in a loop. Story 1.21 sets the final values.
3. **Exit codes.**
   - A stale kill and any exit other than 3 both restart with the backoff.
   - Exit 3 ends the loop only when the child exited with 3 by itself; a kill never counts. The supervisor then exits 3, so Task Scheduler shows it as the last result.
4. **The supervisor kills the whole process tree** (`taskkill /T /F`). The tree is `uv run` → venv `python.exe` → base `python.exe`, and killing `uv.exe` leaves both Pythons running (seen in a probe while planning).
5. **The fake takes one-shot commands from `<data dir>\spike-control`** (`stall`, `exit <n>`). The child that reads a command deletes the file. Persistent behaviours come only from CLI flags, which the tests use.
6. **The task action** is `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "<...>\supervisor.ps1" …`, with the working directory set to `nextron-engine`. This PC's execution policy is Undefined (Restricted) at every scope except Process.
7. **Timings.** Mac's spike run uses the AD-20 values:
   - backoff from 5 s doubling to 300 s, reset after 600 s up;
   - stale at 300 s, with a 15 s poll and a 900 s start grace.

   The contained proof and the tests use timings of a few seconds.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Crash | the child exits with any code but 3, or is killed | logs `exit` (code, uptime), waits 5, 10, 20 … s (capped at 300), then restarts | N/A |
| Long run | the child was up `ResetAfterSeconds` or more | the next wait is `BackoffStartSeconds` again | N/A |
| Exit 3 | the child exits with 3 by itself | logs `stop`; the supervisor exits 3; no restart | N/A |
| Stale | after its first beat, the heartbeat doesn't change for `StaleAfterSeconds` of polls | logs `stale`; no process of the tree is left; restarts with the backoff | `taskkill` fails: logged, then `Process.Kill` |
| Slow start | the heartbeat is an hour old or missing, and the first beat comes before `StartGraceSeconds` | not killed | N/A |
| Hung start | no beat within `StartGraceSeconds` | `stale` with `before_first_beat`; killed and restarted | N/A |
| Env | `-Env dev` | the child sees `NEXTRON_ENV=dev`; the supervisor's own environment is unchanged | N/A |
| Cannot start | the command's executable is missing | logs `start_failed`, waits the backoff, retries | N/A |
| Max restarts | `-MaxRestarts N` reached (tests only) | logs `max_restarts`; exits 0 | N/A |
| Refusals | `oq_a9.ps1` elevated, or `-TaskName "Nextron Engine (prod)"` | one line on stderr; exit 2; nothing registered | N/A |
| Dry run | `oq_a9.ps1` with no action | prints the trigger, principal, settings and action; registers nothing; exit 0 | N/A |
| Contained proof | `oq_a9.ps1 -Action Prove` | registers `NextronSpikeTest-<hex>` with no trigger, runs the cases and writes a results file; unregisters in `finally`; no `NextronSpikeTest*` task or spike process is left | a denied cmdlet is recorded as a result, not worked around |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/main.py:507-521, 1115-1214` -- `serve()` exit codes:
  - 0 is a clean stop;
  - 1 is a crash;
  - 2 is a failed startup check, a Uvicorn bind failure (`server_start_failed`) or a recovery bookkeeping failure;
  - 3 is lock held, raised before any work.

  Read-only for this story.
- `nextron-engine/nextron/lock.py` -- `msvcrt.locking` on `<data dir>\nextron.lock`. The OS releases it when the process dies, so a hard kill frees it. `nextron restore` holds it for its whole run.
- `nextron-engine/nextron/jobs/heartbeat.py`, `main.py:499,635` -- `HEARTBEAT_FILE_NAME = "heartbeat"`. The writer touches `<data dir>\heartbeat` every 30 s, after the row commit. It starts after the runner and the scheduler, so after the recovery startup pass and migrations.
- `nextron-engine/.venv/Lib/site-packages/uvicorn/server.py:45-50` -- Uvicorn 0.54.0 handles SIGINT and SIGBREAK on Windows. A CTRL_BREAK sent to the child's process group is therefore a graceful-stop route for Story 1.21 (record it; don't build it).
- `nextron-engine/tests/app/test_serve_process.py` -- the pattern for tests that start subprocesses (a fixture, and a kill in `finally`).
- `nextron-engine/tests/conftest.py` -- the autouse fixtures give each test a temporary `LOCALAPPDATA` and `NEXTRON_ENV=test`.
- `nextron-engine/tests/contracts/test_source_tree.py` `FILES` -- add the three spike files.
- `nextron-engine/README.md` -- add a section after "Triggers and the heartbeat".
- `docs/` (repo root) -- holds `playbook/`. Add `spikes/`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/scripts/spike/fake_serve.py` -- the stdlib fake:
  - args `--heartbeat`, `--interval`, `--control`, `--log`, `--beat-delay`, `--stall`, `--exit-after`/`--exit-code` and `--grandchild` (starts a sleeping child Python and logs its pid);
  - JSON-line log events `start` (pid, `NEXTRON_ENV`), `command` and `exit`.
- [x] `nextron-engine/scripts/spike/supervisor.ps1` -- the loop of the matrix:
  - params `-Env`, `-DataDir`, `-ServeCommand` (`string[]`; by default `uv run --frozen python <fake>` on `<DataDir>`), `-LogPath` (default `<DataDir>\logs\supervisor.log`), the timing params and `-MaxRestarts`;
  - starts the child with `ProcessStartInfo` (the environment set for the child only);
  - writes JSON-line events `supervisor_start`, `start`, `start_failed`, `exit`, `stale`, `kill`, `backoff`, `stop` and `max_restarts` (UTF-8, no BOM).
- [x] `nextron-engine/scripts/spike/oq_a9.ps1` -- `-Action DryRun|Register|Status|Kill|Stall|Exit3|Unregister|Prove`, with `-TaskName` (default `Nextron Spike OQ-A9`):
  - the AD-20 trigger, principal and settings, built once and shared by every action;
  - the refusals;
  - `Prove` runs these cases on the on-demand task:
    - registration (`Get-ScheduledTask`, `Get-ScheduledTaskInfo` and `Export-ScheduledTask` XML);
    - crash (kill the fake's Python);
    - a second `Start-ScheduledTask` while running (IgnoreNew);
    - stall, then the stale kill;
    - `exit 3`, then the loop ends with result 3;
    - `Stop-ScheduledTask`: are the supervisor and its children gone?

    Results go to `<proof dir>\results.json` and stdout. `finally` stops and unregisters the task, kills leftovers and checks that no `NextronSpikeTest*` remains.
- [x] `nextron-engine/tests/app/test_spike_supervisor.py` -- one test per supervisor row of the matrix (skipped without `powershell.exe`), plus `oq_a9.ps1`'s dry run and the prod-name refusal. Assert on log events, not on timing. Check that every pid of a killed tree is gone.
- [x] `nextron-engine/tests/contracts/test_source_tree.py` -- add the three spike files to `FILES`.
- [x] `docs/spikes/oq-a9-logon-task.md` -- the decision record:
  - the question;
  - the method;
  - the evidence (probes, tests, the contained proof's output);
  - the rows for Mac (logon, sign-out/in, sleep/wake, the real registration's `Get-ScheduledTask`/`Get-ScheduledTaskInfo`);
  - the go or no-go;
  - the parameter changes;
  - the fallbacks if non-elevated registration fails;
  - what Story 1.21 must do.
- [x] `nextron-engine/README.md` -- a "Logon task spike (OQ-A9)" section: what the spike is and Mac's exact steps.
- [x] Orchestrator: run `oq_a9.ps1 -Action Prove` once, check that nothing is left, and copy the results into the record.

**Acceptance Criteria:**
- Given the spike scripts, when the tests run, then the backoff sequence, the reset, the stop on exit 3, the stale kill of the whole tree, the start grace and the per-child environment are proven without Task Scheduler.
- Given `oq_a9.ps1 -Action Prove` non-elevated, when it ends (pass, fail or denied), then no `NextronSpikeTest*` task and no spike process remains, and the record holds its output.
- Given the record, when Mac reads it, then it states the verdict, the changed parameters, the fallbacks and his remaining steps.

## Implementation Notes

**Files.** `nextron-engine/scripts/spike/{fake_serve.py,supervisor.ps1,oq_a9.ps1}`,
`nextron-engine/tests/app/test_spike_supervisor.py` (41 tests), `FILES` in
`tests/contracts/test_source_tree.py`, `docs/spikes/oq-a9-logon-task.md` and a README section
"Logon task spike (OQ-A9)". Nothing changed under `nextron/`; no migration, setting or dependency.
The PowerShell files are ASCII only, because PowerShell 5.1 reads a `.ps1` without a BOM as ANSI.

**`fake_serve.py`.**
- Flags: `--heartbeat`, `--interval` (default 30), `--control`, `--log`, `--beat-delay`,
  `--stall`, `--exit-after`, `--exit-code` (default 0) and `--grandchild`.
  - `--stall` means beat once, then never again.
  - `--grandchild` starts a sleeping `sys.executable -c …` that prints its own pid. The `start`
    event logs `grandchild_pids`: the Popen pid and, behind a venv launcher, the interpreter's
    own pid. The sleeper ends by itself after 600 s if leaked. On a normal exit the fake
    taskkills it.
- Events: `start` (`pid`, `ppid`, `nextron_env`, `grandchild_pids`), `command` (`text`,
  `accepted`) and `exit` (`code`).
- A command is claimed by renaming the control file to `<name>.<pid>.claimed`, then read and
  deleted, so exactly one child obeys it. Only `stall` and `exit <0..255>` (ASCII digits only)
  are accepted. A file that isn't UTF-8 is read as an empty command and ignored.

**`supervisor.ps1`.**
- Parameters: `-Env` (dev/test/prod, default test), `-DataDir` (default
  `%LOCALAPPDATA%\Nextron\spike`), `-ServeCommand`, `-LogPath`, and the timings with the AD-20
  defaults: `-BackoffStartSeconds` 5, `-BackoffMaxSeconds` 300, `-ResetAfterSeconds` 600,
  `-StaleAfterSeconds` 300, `-PollSeconds` 15, `-StartGraceSeconds` 900. Also:
  - `-MaxRestarts` (-1 means forever; tests only);
  - `-FakeBeatSeconds` (the default command's `--interval`);
  - `-TaskkillPath` (tests only: a missing path forces the descendant-kill fallback).
- The default command is `uv.exe run --frozen python <fake> --heartbeat … --control … --log
  <DataDir>\logs\fake_serve.log --interval …`, with `uv.exe` resolved once through
  `Get-Command`. A `string[]` can't pass through `-File`, so the task never passes one.
- The child starts with `ProcessStartInfo` (`UseShellExecute` false, working directory
  `nextron-engine`, `NEXTRON_ENV` in `EnvironmentVariables` only). It shares the supervisor's
  console, so uv's Python opens no window under the hidden task.
- Arguments are quoted for `CommandLineToArgvW`.
- The heartbeat's baseline modification time is read before each start.
- On a stale heartbeat: `taskkill /PID /T /F` first.
  - If that fails, or the child is still running 10 s later, the fallback lists the child's
    descendants through `Win32_Process` `ParentProcessId`, recursively. It keeps only processes
    created no earlier than the child's `StartTime`, truncated to whole microseconds, so a
    reused pid is never matched.
  - It stops each one with `Stop-Process -Force`, deepest first, then calls `Process.Kill` on
    the child.
  - Each attempt logs `kill` with `method` and `ok`: `taskkill` with `code` or `error`, and
    `process_kill` with `pids` (the descendants, then the child) and an `error` for any process
    still running.
- `max_restarts` is checked before the backoff. The backoff starts at
  `min(-BackoffStartSeconds, -BackoffMaxSeconds)`, and returns there when the child was up at
  least `-ResetAfterSeconds`. A start failure counts as uptime 0.
- An unexpected error logs `supervisor_error` and exits 1. A `finally` kills a still-running
  child tree, with reason `supervisor_exit`.
- Every event carries `ts` (UTC `…Z`) and `supervisor_pid`. Lines are appended as UTF-8 without
  a BOM, with up to 5 tries.

**`oq_a9.ps1`.**
- Refusals come first, before any action (one stderr line, exit 2): an elevated session; a
  `-TaskName` equal to `Nextron Engine (prod)` after trimming, in any letter case; an empty name;
  or one holding `* ? [ ] \ /`.
- `New-SpikeTaskParts` builds the trigger (AtLogOn for the current user; none with `-OnDemand`),
  the principal (`Interactive`/`Limited`), the settings and the action, and every action uses it.
  - The action runs `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` with
    `-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File
    "<spike>\supervisor.ps1" -Env <env> -DataDir "<dir>"`, plus the proof's timings.
  - The working directory is `nextron-engine`.
- Actions:
  - `Register` refuses an existing name.
  - `Status` is read-only.
  - `Kill` stops the last logged fake pid, only while it is a listed spike process.
  - `Stall` and `Exit3` write the control file through a temporary file and a rename.
  - `Unregister` refuses (one stderr line, exit 2, nothing changed) when the task's
    `Description` isn't this spike's; several tasks sharing the name are refused the same way.
    Otherwise it stops the task, unregisters it, deletes a leftover `spike-control` and stops
    leftover spike processes.
    - The stop and the unregister each have their own `try`, so a failing stop never skips the
      rest. A failed unregister sets exit 1.
  - `Register` and `Unregister` both delete a leftover `<DataDir>\spike-control` and say so in
    their evidence, so the next fake can't obey a stale `Stall` or `Exit3`.
  - Register, Status, Kill, Stall, Exit3 and Unregister append their output to
    `<DataDir>\oq-a9-evidence.txt`.
- Spike processes are matched by image (`powershell.exe`, `uv.exe`, `python.exe`,
  `pythonw.exe`) and by a command line that names both the data dir and `supervisor.ps1` or
  `fake_serve.py`. The current process is never matched.
- `Prove` uses timings of 2/8/600/6/1/120 s and a fake beat of 1 s, on
  `%TEMP%\nextron-oq-a9-<hex>`. It runs these cases: registration (the settings checked through
  `Get-ScheduledTask`, plus the evidence text and the XML export), start, ignore_new, crash (two
  rounds, backoffs 2 then 4), stall, exit3 (`LastTaskResult` 3), and stop_task (`observed` when
  `Stop-ScheduledTask` leaves processes, which it then stops).
  - Its `finally` unregisters its own task and any `NextronSpikeTest-<8 hex>` leftover, stops
    the spike processes on the proof folder, and records `tasks_left` and `processes_left`. It
    then writes `results.json` and prints it.
  - Each `Stop-ScheduledTask` there has its own `try`, and its error is recorded in
    `stop_errors`, so the unregister always runs. The spike processes are stopped outside that
    `try`.
  - It exits 1 if any case is `fail`, `denied` or `skipped`.

**Tests.**
- PowerShell runs hidden in its own console through `-EncodedCommand`, which lets an array
  reach `-ServeCommand`. Its stdout and stderr go to files.
- The supervisor tests use a wrapper that prints the PowerShell's own `NEXTRON_ENV` after the
  supervisor returns.
- The `oq_a9` tests import `ScheduledTasks` first, then shadow every stateful ScheduledTasks
  command with a tripwire. Importing the module afterwards would replace the shadows, because
  those commands are CDXML functions.
- Refusal tests use only `-Action DryRun` and `Status`, over the prod name in any case or padded,
  an empty name, and names holding `*`, `\`, `/`, `?` or `[`.
- Kill, Stall and Exit3 run on a fake that the test starts with the supervisor's default paths.
- The two Unregister tests replace `Get-ScheduledTask` with a stand-in body (another task's
  `Description`, or none), so the real command is never called. Stop- and
  Unregister-ScheduledTask stay tripwires.
- The default-command test runs the supervisor with `PATH` limited to System32, the Windows
  folder and WindowsPowerShell, so there is no uv, and with PowerShell's working folder set to
  the test's temporary folder. It skips if `uv.exe` is reachable anyway.
- The taskkill-fails test puts a plain base interpreter in front of the fake as a pass-through,
  standing in for `uv` (no venv launcher, so no job object), and asserts every pid of the tree
  is gone.
- `read_lines` ignores an unterminated last line.
- Liveness checks use `OpenProcess` and `WaitForSingleObject` through ctypes. The cleanup ends
  only pids whose image is `python.exe`.

**Verification.**
- The targeted tests pass with `-n 6` (41 tests, about 15 s).
- Three mutations of `supervisor.ps1` were each caught by the new tests: no reset of the quiet
  count on a beat, `firstBeat` not reset per child, and a fallback that skips the descendants.
- `tests/contracts` passes, and lint-imports keeps all 29 contracts.
- The dry run prints the AD-20 task.
- A stubbed harness, outside the repo, ran Mac's actions and the proof's logic: every case
  passed, and stop_task was observed.
- An unplanned real proof run happened during the build. It registered and removed
  `NextronSpikeTest-30575c6c`, and nothing was left. The decision record has its evidence.

**Final-gate fixes** (orchestrator, after the review patches):
- **Run 1.** The first full suite failed only on the known job-runner flake.
- **Run 2.** The re-run failed on `test_a_stale_heartbeat_kills_the_whole_tree_and_restarts_it`.
  - Its log shows `taskkill /T /F` exiting 255 on the second child, though the tree was gone: the fallback then found no descendant. One member of the tree had ended by itself while taskkill ran (the venv launcher's job ends its Python).
  - So the extra `kill` event broke the expected sequence.
- **The fix in `Stop-ChildTree`.** The `taskkill` event's `ok` now means the tree is gone: the child exited within 10 s and `Get-Descendants` finds nothing. It logs `code` (or `error` when taskkill can't run), and the fallback runs only when the tree isn't gone.
- **A bug found on the way.** `Get-Descendants` returns one `int[]` through a unary comma, so `@(Get-Descendants …)` always has `Count` 1. The check assigns it without `@()`.
- **Result.** `tests/app/test_spike_supervisor.py` then passed three times in a row with `-n 6`: 41 passed each time.
- The record's parameter change 2 now says to judge success by the tree, not by taskkill's exit code.

**The orchestrator's contained proof** (2026-11-17, 19:10:08 to 19:11:03 UTC; task `NextronSpikeTest-53674b07`, no trigger, non-elevated). Results:
- **Cases.** registration, start, ignore_new, crash, stall and exit3 passed, and so did cleanup. stop_task came back `observed`.
- **Registration.** It needed no elevation and kept every AD-20 setting (`InteractiveToken`, `PT0S`, `IgnoreNew`, no battery stops, `StartWhenAvailable`), at priority 7.
- **Timings.** The supervisor's first log line came 1.4 s after `Start-ScheduledTask`. The crash restarts waited 2.01 s, then 4.02 s.
- **Stale kill.** It came after 6 polls, and no pid of the tree was left.
- **Exit 3.** `LastTaskResult` was 3.
- **`Stop-ScheduledTask`.** The child `uv.exe`, the venv launcher and the fake's Python were left running.
- **Afterwards.** Neither a `NextronSpikeTest*` task nor a spike process remained. The decision record has the full table.

**Final verification** (orchestrator, on the final tree, through the shared suite lock):
- `uv run pytest -q -n 6 --dist loadgroup`: 4920 passed, 2 skipped (`ai_eval` and the symlink test).
- `uv run lint-imports`: 29 kept, 0 broken.
- **Earlier gate runs:** the first failed only on the known job-runner flake (`test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper[None-1]`), and its re-run hit the taskkill-255 case fixed above.
- **The contained proof, re-run on the final scripts:** task `NextronSpikeTest-a91986f2` (20:00:01 to 20:01:10 UTC). Every case passed, `stop_task` came back observed, and nothing was left.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-18).**
- **Sources:**
  - BH = blind hunter: 16 findings, with BH13 making two claims;
  - EC = edge-case hunter: 19;
  - VG = verification gap: 5 gaps and 1 other finding.
- **Counts:** 41 raw findings make 42 claims, which give 35 entries after deduplication.
- **Outcomes:**
  - 27 entries patched (the rows routed `patch`);
  - 1 deferred (AI);
  - 7 rejected.
- **No loopback.** No finding is an intent gap or a bad spec.
  - The fallback fix (A) meets the matrix's own "Stale" row, which says "no process of the tree is left".
  - The Unregister refusal (Y) follows the boundary "the production task is never touched", extended to every task that isn't the spike's.
- **Rejected only because the fix would change the approved spec:** none.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, VG6, EC11 | When taskkill fails, `Stop-ChildTree` falls back to `Process.Kill()` on the direct child. Under the task that child is `uv.exe`, so the venv launcher and Python survive and keep `nextron.lock`. The record also recommends this fallback right beside "Never kill only `uv.exe`". | medium | Verified: `supervisor.ps1:180-182` kills only `$Process`. The planning probe showed that killing `uv.exe` leaves both Pythons running. The matrix's Stale row expects no process of the tree left, so the code misses the spec on this path. taskkill rarely fails, which makes this medium. Fix: the fallback `Process.Kill`s every descendant (found by `ParentProcessId`, created no earlier than the child), then the child. The record's parameter change 2 is reworded to match. | patch |
| B | BH2, VG5, EC18 | The fallback test runs without `--grandchild` and without an intermediate layer, and checks only `gone(start pid)`. The fixture's cleanup hides any survivor. | medium | Pre-verified by VG (`test_spike_supervisor.py:340-360`). The venv launcher's own job object makes a partial kill invisible. Fix: put a non-job intermediate layer in front (the base interpreter running `subprocess.call`, standing in for `uv`), add `--grandchild`, and assert that every tree pid is gone. | patch |
| C | VG1 | No test has a fake that beats slower than the supervisor polls, so deleting the reset on a beat (`$quietPolls = 0`) fails no test. With the AD-20 defaults (15 s poll, 30 s beat) that regression would kill a healthy engine every ~10 minutes. | medium | Pre-verified by VG. Fix: add a test where the beat is 0.2 s, the poll 0.1 s and the stale limit 1.0 s, running 3 s; assert no `stale`. | patch |
| D | VG2 | No test gives a child the start grace after a restart that followed a child that beat. The hung-start test doesn't check `quiet_s`. | medium | Pre-verified by VG. Fix: add a restart test where the first beat comes 1.5 s in, against a stale limit of 1.0 s and a grace of 30 s, twice; assert no `stale`. In the hung-start test, assert `quiet_s` ≈ `limit_s`. | patch |
| E | VG3, BH13 (actions) | `oq_a9.ps1`'s `Kill`, `Stall` and `Exit3` never run in a test, though they need no Task Scheduler. A typo in the command text or the control path would break Mac's rows 3, 4 and 6 unnoticed. | medium | Pre-verified by VG (the Python `send()` reimplements the write). Fix: a test that starts the fake on `<tmp>` with the default paths, runs `-Action Stall`, `Exit3` and `Kill` with `-DataDir <tmp>` under the tripwires, and asserts the fake obeys, the pid is gone and no ScheduledTasks command was called. | patch |
| F | BH13 (refusals) | The refusal tests cover only the prod name and `*`. An empty name, `\`, `/`, `?` and `[` are untested. | low | Verified: the parametrize list has four names. The fix is a direct test addition. | patch |
| G | VG4 | The default serve command, the only one the task runs, is never executed or inspected by a test. A misspelt `--control` would crash-loop every fake under Task Scheduler. | medium | Pre-verified by VG. Fix: run `supervisor.ps1` without `-ServeCommand`, with a `PATH` holding no uv and `-MaxRestarts 0`. Assert `start_failed`, then the logged command: `run --frozen python`, flags that `fake_serve.parse_args` accepts, and the heartbeat, control and log paths under `-DataDir`. | patch |
| H | BH3 | The record and the README say a wake "can't trigger a kill". But the real engine withholds its beat after a wake while a job that ran across the sleep is past `max_runtime` (the runner's clock counts sleep), and the supervisor then kills it about 300 s later. | low | Verified against the README's heartbeat paragraph (Story 1.11). The claim is true only of the supervisor's own counting; Mac's row 5 uses the fake, which never withholds. Fix: narrow the claim, and add the engine-side point (a `max_runtime` clock that excludes sleep, or a wake allowance) to the Story 1.21 list. | patch |
| I | BH4 | The job-object advice clashes with parameter change 5 (`ProcessStartInfo` can't set `CREATE_NEW_PROCESS_GROUP` or `CREATE_SUSPENDED`). Assigning the child to a job after `Process.Start` races uv's spawn of the launcher. CTRL_BREAK through `uv` and the launcher is untested. | low | Verified: .NET Framework's `ProcessStartInfo` has no creation-flags field. Fix (record): name a race-free route, the supervisor placing itself in a kill-on-close job at startup so every child inherits it (or `CreateProcess` started suspended), and mark CTRL_BREAK through uv as untested. | patch |
| J | EC13 | If `uv.exe` itself exits while its Pythons keep running, the next start exits 3 and supervision ends, leaving an unsupervised engine. | low | True in principle: the probe showed that uv's death doesn't end its Pythons. It needs uv to die alone, which nothing in the spike does (Kill targets the fake's Python). Fix (record only): note that the same job object covers it. | patch |
| K | BH5 | The record has no upgrade stop path (`git pull`, `uv sync --frozen`, then stop only the child). `uv sync` can fail on Windows while the engine holds `.pyd` files open, and `uv run --frozen` syncs on every start anyway. | low | Verified: "What Story 1.21 must do" covers only restore. Fix (record): add the upgrade procedure and these two points. | patch |
| L | BH6 | Exit 2 restarts forever. A migration that keeps failing takes a new `pre_migrate` copy at every start, and copies of failed migrations are never deleted, so a crash loop can fill the disk (about 288 copies a day at the 5-minute cap). | medium | Verified against the README's `pre_migrate` text (Story 1.16: a copy whose migration failed has no row and is never rotated). The supervisor follows AD-20 as written. The remedy is Story 1.21's design. Fix (record): add it to the Story 1.21 list. | patch |
| M | BH7 | The task has no restart-on-failure setting, so if the supervisor itself dies, supervision is gone until the next logon. A crash loop, an exit-3 stop and a dead supervisor raise no alert, because alerts come from the running engine. | low | Verified: no `-RestartCount` in the settings, and `supervisor_error` exits 1. AD-20 doesn't cover it. Fix (record): list it for Story 1.21 (Mac to decide on any setting beyond AD-20). | patch |
| N | BH8 | `StartGraceSeconds` (900) is more than `ResetAfterSeconds` (600), so a hung start resets the backoff every time. The 900 s grace was never measured against a real startup. | low | Verified at `supervisor.ps1:279`: uptime includes hung time. The restart cadence is still about 15 minutes, so this is no hot loop. Fix (record): suggest basing the reset on time since the first beat, and say the grace is unmeasured. | patch |
| O | BH9 | The verdict has no path for a failure of row 2 or row 5, doesn't say whether Story 1.21 may start before Mac's rows, and leaves out row 7 (the AC's unregister). | low | Verified (record lines 9-13). Fix (record): full go needs rows 1, 2, 5 and 7. Say what a failure of row 2 or row 5 means, and that Story 1.21 can be built and tested with fakes but installed for `prod` only after a full go. | patch |
| P | BH10 | Nothing checks whether a console window appears. With Windows Terminal as the default terminal, `-WindowStyle Hidden` may leave a visible window, and closing it kills the supervisor and the engine. | maybe-false | This PC has Windows Terminal 1.24 and no `DelegationConsole` override ("let Windows decide"). The build couldn't see the screen during the proof. Settle it by watching the screen at row 2. Fix (record): a "no window" check in rows 2 and 3, and a Story 1.21 note (`conhost.exe --headless` as the remedy). | patch |
| Q | BH11, EC8 | `Prove`'s leftover sweep unregisters any `NextronSpikeTest-<hex>` (a concurrent proof's too) but doesn't kill those tasks' processes. | low | True, but it needs two proofs at once, or an earlier proof killed mid-run (its own `finally` normally cleans up). The fix needs each leftover task's data dir parsed out of its action. | reject |
| R | BH12, EC9 | The record and the README say every action appends to `oq-a9-evidence.txt`, but `DryRun`, `Prove` and the failure paths of `Register` and `Kill` don't. Times are UTC, which neither says. | low | Verified (`oq_a9.ps1:292-358`). The direct fix narrows the docs to the actions that append, and says that failures print only, and that log times are UTC. | patch |
| S | BH14 | Nothing in `oq_a9.ps1` itself (no confirmation switch, no refusal under `NEXTRON_ENV=test`) stops an accidental real run like the build's unplanned one. | low | That run happened because a harness's stubs were replaced, not because of the script. The tests import the module first and check every shadow. A confirmation switch would be new surface the spec doesn't define. | reject |
| T | BH15 | Row 2's "heartbeat under 30 s" can read 30.x s with the 30 s fake beat. Row 5's "note the fake's pid" is ambiguous, since `Status` shows uv's pid in events and two unlabelled `python.exe` rows. | low | Verified. Direct doc fix: row 2 says "under 45 s"; row 5 expects no new `start`, `exit` or `stale` event after the wake. | patch |
| U | BH16 | The record masks the user in the uv path but shows `UserId mac` in the proof table, and Mac's pasted evidence will hold the machine and user names. | low | Verified. Direct doc fix: mask as `<user>` throughout, and ask Mac to redact machine and user names before pasting. | patch |
| V | EC1 | A control file that isn't UTF-8 (for example `echo stall > spike-control` in PowerShell 5.1, which writes UTF-16) raises `UnicodeDecodeError`, which crashes the fake. | low | Verified: `read_command` catches only `OSError`. Direct fix: also catch `UnicodeDecodeError`. | patch |
| W | EC2 | `exit ²` passes `isdigit()` but `int()` raises, which crashes the fake. | low | Verified. Direct fix: `isascii() and isdigit()`. | patch |
| X | EC3 | `Stall` or `Exit3` sent while no fake runs stays in `spike-control`, and the next fake (after a later logon or a Register) obeys it at once. | low | Verified: nothing clears the file. Direct fix: `Register` and `Unregister` delete a leftover `spike-control`. | patch |
| Y | EC4 | `-Action Unregister -TaskName <another task>` stops and permanently removes any task the user owns with that name. | medium | Verified (`oq_a9.ps1:366-372`): only the prod name is refused. Direct fix: `Unregister` refuses (exit 2) a task whose description isn't the spike's. | patch |
| Z | EC5 | `-DataDir C:\` trims to the drive-relative `C:`. | low | Nobody points the spike's data dir at a drive root, and process matching still needs a spike script's name. | reject |
| AA | EC6 | Process matching by `IndexOf($Dir)` also matches a data dir sharing the prefix (`spike2`). | low | No second spike data dir exists, and matching still needs a spike script's name. | reject |
| AB | EC7 | If `Stop-ScheduledTask` throws during the cleanup or `Unregister`, `Unregister-ScheduledTask` is skipped and the task stays. | low | Verified: both calls share one `try`. The direct fix wraps each stop in its own `try`, so the unregister always runs, as the safety rule requires. | patch |
| AC | EC10 | A deleted heartbeat, or a read error, counts as a beat when the readings change back. | low | `GetLastWriteTimeUtc` doesn't throw for a missing file, and nothing deletes the heartbeat while the engine runs. At worst one poll is masked once. The fix adds sentinel branches. | reject |
| AD | EC12 | If neither taskkill nor `Process.Kill` ends the child, `WaitForExit()` blocks forever. | low | `TerminateProcess` on a process the user owns doesn't fail in practice, and the fix needs a new timeout branch and a policy for the stuck child. | reject |
| AE | EC14 | A log append that fails five times drops the event silently. | low | Under the hidden task there is no other channel to report it on. Rare (the log dir is the supervisor's own). | reject |
| AF | EC15 | `-BackoffStartSeconds` greater than `-BackoffMaxSeconds` makes the first wait exceed the cap. | low | Verified. Direct fix: `Min(start, max)` at the start and on each reset. | patch |
| AG | EC16 | The stale test's `len(pids) >= 6` assumes the venv launcher's extra layer. | low | Verified: a plain interpreter gives fewer pids. Direct fix: assert that every fake `start` names grandchild pids, with a looser count. | patch |
| AH | EC17 | `read_lines` can parse a half-written last line while polling the fake's log. | low | Possible while the fake appends. Direct fix: ignore an unterminated last line. | patch |
| AI | EC19 | The README no longer says Story 1.20 checks the trigger catch-up after a real sleep, but the spec-1-11 entry in `deferred-work.md` still gives that check to Story 1.20. | low | Verified. Existing entries can't be edited, so append a new entry that hands the check to Story 1.21 (or Mac's first real sleep with the engine). | defer |

## Design Notes

```text
loop: start child (env for child only) -> poll every PollSeconds (WaitForExit(ms): relative wait, sleep not counted)
  mtime changed -> quiet = 0, firstBeat = true ; else quiet += PollSeconds
  limit = firstBeat ? StaleAfterSeconds : StartGraceSeconds ; quiet >= limit -> taskkill /PID <child> /T /F
exit: own exit 3 -> stop (exit 3) ; else uptime >= ResetAfter -> backoff = start ; wait backoff ; backoff = min(2x, max)
```

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q tests/app/test_spike_supervisor.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: 29 contracts KEPT.
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/spike/oq_a9.ps1` -- expected: the dry run prints and registers nothing.
- `Get-ScheduledTask -TaskName 'NextronSpikeTest*'` after the proof -- expected: nothing.
