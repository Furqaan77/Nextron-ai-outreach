---
title: "OQ-A9: the logon task and supervision (Story 1.20 spike)"
updated: 2026-11-18
status: conditional go, pending Mac's rows
---

# OQ-A9: the logon task and supervision

**Verdict: conditional go.** A current-user task with the AD-20 trigger, principal and settings
can be registered and run without elevation on this PC. The supervision parameters change as
listed under [Parameter changes](#parameter-changes-for-story-121).

The verdict becomes a full go when Mac's rows 1, 2, 5 and 7 pass (see [Mac's rows](#macs-rows)):
the real registration, a start at logon, the supervisor across a sleep, and a clean removal. A
failure means:

- **Row 1** (registration refused without elevation): see
  [Fallbacks](#fallbacks-if-non-elevated-registration-fails).
- **Row 2** (nothing starts at logon): the AtLogOn trigger or the Interactive principal doesn't
  work here. The fallbacks C to E start the engine at logon without Task Scheduler.
- **Row 5** (the fake is killed or restarted across a sleep): the poll-count stale rule (change 1
  below) doesn't hold across a real sleep. Story 1.21 then needs a wake allowance, for example
  from the power-resume event.

Story 1.21 can be built and tested with fakes now. It is installed for `prod` only after a full go.

## The question

The spine's open question OQ-A9 asks: does non-elevated task registration with the AD-20
parameters work on this PC, and does a crash restart as expected? Story 1.21 also relies on four
assumptions that had never been tried here:

1. A non-elevated logon task registers with `-AtLogOn`, `Interactive`/`Limited`,
   `-ExecutionTimeLimit` PT0S, `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
   -StartWhenAvailable -MultipleInstances IgnoreNew`.
2. A supervisor loop restarts `nextron serve` with backoff (5 s doubling to 5 min, reset after
   10 min up).
3. It kills the engine when the heartbeat goes stale, and killing `uv` stops the engine.
4. It stops looping on exit code 3, and a PC wake or a slow start doesn't look like a hang.

## Method

The spike is throwaway code in `nextron-engine/scripts/spike/`. Story 1.21 replaces it.

- `fake_serve.py`: a stdlib fake of `nextron serve`. It touches a heartbeat file, and can be told
  to stall or exit by command-line flags (the tests) or by one-shot commands in
  `<data dir>\spike-control` (Mac's run and the contained proof).
- `supervisor.ps1`: a throwaway supervisor loop with the AD-20 timings as its defaults.
- `oq_a9.ps1`: one control script with `-Action DryRun|Register|Status|Kill|Stall|Exit3|Unregister|Prove`.
  It refuses to run elevated, and refuses the production task's name.

There are three layers of evidence:

1. **Automated tests** prove the loop's behaviour without Task Scheduler.
2. **The contained proof** (`oq_a9.ps1 -Action Prove`) registers an on-demand task with no
   trigger, runs the cases through Task Scheduler and always unregisters it.
3. **Mac's rows** cover what only Mac can do at the PC: the logon trigger, signing out and in, and
   sleeping the PC.

## Evidence

### Probes (2026-11-17 and 18, on this PC)

- Windows 11 Pro 10.0.26300; Windows PowerShell 5.1.26100.9818. The build session was not
  elevated (`IsInRole(Administrator)` was False).
- **Execution policy** is Undefined at every scope (MachinePolicy, UserPolicy, CurrentUser,
  LocalMachine), so the effective policy is Restricted. The task action therefore passes
  `-ExecutionPolicy Bypass` for its own process; no system-wide setting changes.
- **uv** is `C:\Users\<user>\AppData\Local\Programs\Python\Python312\Scripts\uv.exe`, on the
  user PATH.
- **The process tree** of `uv run --frozen python <script>` is `uv.exe` → the venv's
  `python.exe` (a launcher) → the base `python.exe`. Killing `uv.exe` alone leaves both Pythons
  running (seen in a probe while planning). In the proof's logs, where the supervisor's child is
  `uv`, the fake's parent pid is the venv launcher, not `uv`, which confirms the extra layer. `taskkill /PID <child> /T /F`
  takes the whole tree (tests, and both proof runs).
- **Task priority.** `New-ScheduledTaskSettingsSet` gives the task priority 7 (below normal).
  AD-20 doesn't set a priority; see [Story 1.21](#what-story-121-must-do).
- **Arrays can't pass through `-File`.** `powershell -File script.ps1 -Arr a,b` binds one string,
  `"a,b"`. The supervisor therefore builds its own default serve command, and the task action
  passes only plain values.
- The **Task Scheduler operational log** is disabled on this PC, so its history isn't available
  as evidence.

### Automated tests

`nextron-engine/tests/app/test_spike_supervisor.py` has 41 tests. All pass, in about 15 s with
`-n 6`. They never start `uv` and never call a Task Scheduler command: the serve command is the
fake on the test's Python. Each test reads the JSON-line logs, and kills its PowerShell's tree
and every logged Python pid in a `finally`.

| Matrix row | Test | Shown |
|---|---|---|
| Crash | `test_a_crash_restarts_after_a_doubling_backoff_capped_at_the_maximum` | backoffs 0.1, 0.2, 0.4, 0.4 (capped) |
| Long run | `test_a_long_run_resets_the_backoff_to_its_start` | each child up ≥ the reset: every wait is the start value; exit 0 restarts too |
| Exit 3 | `test_the_childs_own_exit_3_ends_the_loop_and_the_supervisor_exits_3` | `stop`, no restart, supervisor exit code 3 |
| Stale | `test_a_stale_heartbeat_kills_the_whole_tree_and_restarts_it` | `stale` after the first beat, `taskkill` ok, every pid of both trees gone (launcher, fake, grandchild) |
| Stale, taskkill fails | `test_when_taskkill_fails_the_supervisor_kills_every_descendant_then_the_child` | with a pass-through interpreter standing in for `uv` (no job object): `kill` (taskkill, not ok, error), then `kill` (process_kill, ok) naming every pid; pass-through, fake and grandchild all gone |
| Beat slower than the poll | `test_a_beat_slower_than_the_poll_never_counts_as_stale` | a beat every 0.2 s, polls every 0.1 s, limit 1 s: never stale (each beat resets the count) |
| Slow start | `test_a_slow_start_is_not_killed_before_its_first_beat[missing]`, `[an hour old]` | first beat 2 s in, stale limit 1 s: not killed |
| Restart after a beating child | `test_a_child_restarted_after_one_that_beat_gets_the_start_grace_again` | the second child's first beat 1.5 s in, past the 1 s limit: not killed |
| Hung start | `test_a_start_with_no_beat_within_the_start_grace_is_killed_and_restarted` | `stale` with `before_first_beat` at exactly the grace, whole tree gone |
| Env | `test_the_child_alone_gets_nextron_env[default]`, `[dev]` | the child sees `test` (default) or `dev`; the supervisor's own `NEXTRON_ENV` is unchanged afterwards |
| Cannot start | `test_a_missing_executable_logs_start_failed_and_retries_after_the_backoff` | `start_failed`, backoff 0.1 then 0.2 |
| Max restarts | `test_max_restarts_ends_the_loop_with_exit_0` | `max_restarts`, exit 0 |
| Default command | `test_the_default_serve_command_runs_the_fake_through_uv_on_the_data_dir` | with no uv on PATH: `start_failed`; the logged command is `uv.exe run --frozen python <fake>`, and the fake's own parser accepts the rest, with the heartbeat, control and log paths on the data dir |
| Log format | `test_every_line_is_utf8_json_without_a_bom` | UTF-8, no BOM, `…Z` times |
| Control file | `test_the_fake_obeys_each_control_command_once` | unknown text logged and ignored; `stall`, `exit 4` each obeyed once; the file is consumed |
| Dry run | `test_the_dry_run_prints_the_ad20_task_and_registers_nothing` | the AD-20 trigger, principal, settings and action; no ScheduledTasks state command called |
| Refusals | `test_the_production_task_name_is_refused_before_anything_runs` (18 cases) | the prod name in any case or padded, an empty name, or one holding `*`, `\`, `/`, `?` or `[`: exit 2, one stderr line, nothing called |
| Kill, Stall, Exit3 | `test_kill_ends_the_running_fakes_python`, `test_stall_reaches_the_running_fake`, `test_exit3_makes_the_running_fake_exit_3` | on a fake with the supervisor's default paths: its Python is gone; `stall` is accepted; it exits 3 |
| Unregister | `test_unregister_refuses_a_task_this_spike_did_not_register`, `test_unregister_deletes_a_leftover_command` | a task with another description: exit 2, nothing stopped or removed; with no task, a leftover `spike-control` is deleted |

The tests run `oq_a9.ps1` with every state-changing or state-reading ScheduledTasks command
shadowed by a tripwire that fails the test if it is called. The module is imported first, since
importing it later would replace the shadows. The two Unregister tests give `Get-ScheduledTask`
a stand-in body, so the real one is never called either.

### The contained proof

#### An unplanned early run (2026-11-17, 18:41 to 18:45 UTC)

While building, the implementer ran the proof's logic in a scratch harness meant to replace every
Task Scheduler command with a stub. The ScheduledTasks commands are CDXML functions, and the
module was imported after the stubs were defined, so it replaced them. The proof therefore ran
for real: it registered a real on-demand task, `NextronSpikeTest-30575c6c`, without elevation,
drove it and unregistered it. Its cleanup worked: a separate read-only check afterwards found no
`NextronSpikeTest*` task and no spike process.

A bug in the proof's log reading made most of its checks time out. In PowerShell 5.1 a single
`PSCustomObject` has no `.Count`, so "wait for the first event" never matched. The bug is fixed.
The supervisor's and the fake's own logs from that run are still real Task Scheduler evidence:

- **Registration without elevation succeeded** with the AD-20 principal and settings. The
  exported XML shows `LogonType` InteractiveToken, no `RunLevel` element (that is,
  LeastPrivilege), `DisallowStartIfOnBatteries` false, `StopIfGoingOnBatteries` false,
  `ExecutionTimeLimit` PT0S, `MultipleInstancesPolicy` IgnoreNew and `StartWhenAvailable` true.
- **Start.** `Start-ScheduledTask` ran the hidden supervisor, which started
  `uv run --frozen python fake_serve.py`; the fake saw `NEXTRON_ENV=test`. The first start took
  about 42 s from `Start-ScheduledTask` to the supervisor's first log line (worked out from the
  case timeouts; the operational log is off). A later start took about 1 s. The cause is unknown.
- **IgnoreNew.** A second `Start-ScheduledTask` while the task ran was accepted, but only one
  supervisor process ran.
- **Stale kill.** The fake took `stall` at 18:42:22.505. The supervisor logged `stale` at
  18:42:28.645 (6 polls of 1 s), `kill` by taskkill (ok), and `exit` (killed). It restarted after
  its 2 s backoff, at 18:42:30.955.
- **Exit 3.** The fake exited with 3 at 18:43:22.591. The supervisor logged `stop` at
  18:43:22.615 and didn't restart. `LastTaskResult` wasn't captured, because of the bug.
- **`Stop-ScheduledTask` ends only the supervisor.** It ended `powershell.exe`, but `uv.exe`, the
  venv launcher and the fake's Python kept running; the proof then stopped them. Story 1.21 must
  handle this (see below).

The same proof logic then ran in a corrected harness, with the module imported first, the stubs
checked and module autoloading off. There the stub "task" started the action's command line
directly. Every case passed: registration, start, ignore_new, crash (backoffs 2 then 4), stall,
exit3 and cleanup, with stop_task observed. That run checks the proof's own logic, not Task
Scheduler's behaviour.

#### The orchestrator's run

On 2026-11-17, from 19:10:08 to 19:11:03 UTC, the build ran
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\spike\oq_a9.ps1 -Action Prove` once,
non-elevated, from `nextron-engine`. Task `NextronSpikeTest-53674b07` had no trigger, worked on the
temp folder `nextron-oq-a9-53674b07`, and used the proof's timings: backoff 2 s doubling to 8 s,
stale at 6 s, poll 1 s, start grace 120 s, fake beat 1 s. Every case passed, and `stop_task` came
back "observed", as expected.

| Case | Result | Details from `results.json` |
|---|---|---|
| registration | pass | Registered without elevation. `Get-ScheduledTask`: UserId `<user>`, LogonType Interactive, RunLevel Limited, ExecutionTimeLimit PT0S, DisallowStartIfOnBatteries False, StopIfGoingOnBatteries False, StartWhenAvailable True, MultipleInstances IgnoreNew, Priority 7. The exported XML has `InteractiveToken`, `PT0S`, `IgnoreNew` and `<Triggers />`. `Get-ScheduledTaskInfo` before the first run: LastTaskResult 267011 (0x41303, "has not yet run"). |
| start | pass | 1.4 s from `Start-ScheduledTask` to the supervisor's first log line, and 1.45 s to the first beat. The tree is the supervisor, `uv`, the venv launcher and the fake. The fake saw `NEXTRON_ENV=test`. State Running. |
| ignore_new | pass | A second `Start-ScheduledTask` was accepted, but there was still one `supervisor_start` and one supervisor process. |
| crash | pass | The fake's Python was killed twice. Its exit code, -1, came back up through the launcher and `uv`. The restarts waited 2.01 s, then 4.02 s, matching the backoffs of 2 then 4. |
| stall | pass | `stale` after 6 quiet polls (`before_first_beat` false). `taskkill` ok; the exit was logged as killed. All three pids of the tree (`uv`, launcher, fake) were gone. The restart came 8.3 s after `stale` (the backoff had reached its 8 s cap). |
| exit3 | pass | The supervisor logged `stop` and ended. The task left Running, and `LastTaskResult` was **3**. No spike process was left. |
| stop_task | observed | `Stop-ScheduledTask` ended only the supervisor's `powershell.exe`. The child `uv.exe`, the venv launcher and the fake's Python **kept running**, and the task showed Ready. The proof then stopped them. |
| cleanup | pass | Task unregistered. No `NextronSpikeTest*` task and no spike process remained. |

A separate check after the run found the same: `Get-ScheduledTask -TaskName 'NextronSpikeTest*'`
returned nothing, and no process command line named `fake_serve` or `supervisor.ps1`.

**A re-run on the final scripts**, after the review patches. It ran on 2026-11-17 from 20:00:01
to 20:01:10 UTC, on task `NextronSpikeTest-a91986f2`, and exited 0.
- Every case passed again, and `stop_task` came back "observed" again.
- The supervisor's first log line came 5.1 s after the start.
- The crash restarts came 2.10 s and 4.02 s after each exit.
- The stale kill came after 6 polls; taskkill was ok and no pid of the tree was left.
- `LastTaskResult` was 3 after the exit-3 case.
- `Stop-ScheduledTask` again left `uv`, the launcher and the fake running.
- The cleanup reported no stop errors, and left no task and no process.

## Mac's rows

Time box: one day. Run each command from `nextron-engine` in a **normal** (not "Run as
administrator") PowerShell.

- **Evidence file.** Register, Status, Kill, Stall, Exit3 and Unregister append what they print
  to `%LOCALAPPDATA%\Nextron\spike\oq-a9-evidence.txt`. Paste the relevant part into the Result
  column.
  - A refusal, or an error that stops an action (such as "already exists" or "no fake has
    started"), prints to the screen only. A failed registration is also recorded.
- **Times.** The supervisor's and the fake's logs, and the evidence file's headers, are in UTC
  (`…Z`). Task Scheduler's own times (`LastRunTime`) and the process start times are local.
- **Redact before pasting.** The output names this PC and your Windows user (the task's
  `UserId`, paths under `C:\Users\…`). Replace them with `<PC>` and `<user>`.

Each `-Action …` below is short for
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\spike\oq_a9.ps1 -Action …`, for
example:

```powershell
cd "<your checkout>\nextron-engine"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\spike\oq_a9.ps1 -Action Register
```

| # | Step | Command | Expect | Result |
|---|---|---|---|---|
| 1 | Register without elevation | `-Action Register` | "Registered without elevation", with the `Get-ScheduledTask` and `Get-ScheduledTaskInfo` output | |
| 2 | Sign out, sign in | sign out and in, wait 1 minute, then `-Action Status` | State Running; `supervisor_start` and `start` just after the logon; heartbeat under 45 s old; no console window appears (watch the screen after signing in) | |
| 3 | Crash | `-Action Kill`, then `-Action Status` after 1 minute; repeat once within 10 minutes | `exit`, `backoff` 5 then `start`; the second time `backoff` 10; no console window appears (watch the screen after the restart) | |
| 4 | Stale heartbeat | `-Action Stall`, then `-Action Status` after 6 minutes | `stale` (`quiet_s` 300, `before_first_beat` false), `kill` taskkill ok, `exit` killed, `backoff`, `start`; the old pids are gone | |
| 5 | Sleep and wake | `-Action Status`, sleep the PC for 10 minutes or more, wake it, wait 1 minute, `-Action Status` | no new `start`, `exit` or `stale` event after the wake; heartbeat under 45 s old | |
| 6 | Exit 3 | `-Action Exit3`, then `-Action Status` after 1 minute | `stop`; LastTaskResult 3; State Ready; no spike process | |
| 7 | Remove it | `-Action Unregister` | "Checked: no task named 'Nextron Spike OQ-A9' remains" | |

## Parameter changes for Story 1.21

AD-20's trigger, principal and settings stay as written, pending row 1. These change:

1. **Stale is counted in the supervisor's own polls, not from the file's age.** Every poll (a
   relative 15 s wait) that sees the same modification time adds 15 s, and any change resets
   the count.
   - After the child's first beat the limit is 300 s.
   - Before it, the limit is a **start grace** of 900 s, so a slow `pre_migrate` backup,
     migration or recovery startup pass isn't killed in a loop.
   - A poll's wait doesn't count time asleep, and a file left stale by the previous child counts
     for nothing. So the supervisor's own counting never turns a wake or a restart into a kill.
   - That covers the supervisor only. The real engine can withhold its beat after a wake: a job
     that ran across the sleep can be past its `max_runtime` (the runner's clock counts sleep),
     and the beat stays withheld until that job returns. Story 1.21 needs an engine-side answer,
     such as a `max_runtime` clock that excludes sleep, or a wake allowance.

   AD-20's wording, "if `<data dir>\heartbeat` is older than 5 min", would kill the engine right
   after every long sleep.
2. **Kill the whole tree**: `taskkill /PID <child> /T /F` first. If taskkill fails, the
   fallback lists every descendant of the child itself (by parent pid, created no earlier than
   the child, so a reused pid is never matched), kills each, deepest first, then kills the
   child. Never kill only `uv.exe`.
   - **Judge success by the tree, not by taskkill's exit code.** Under test load, `taskkill /T`
     sometimes exits 255 even though nothing is left: a process of the tree ends by itself while
     taskkill runs (the venv launcher's job ends its Python). The spike calls a kill ok when the
     child has exited and no descendant remains, and uses the fallback only otherwise.
3. **Exit 3 ends the loop only when the child exited with 3 by itself.** A kill never counts,
   even when its exit code happens to be 3. The supervisor then exits 3, so Task Scheduler shows
   3 as the last result.
4. **The task action** is
   `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "<checkout>\nextron-engine\scripts\run.ps1" -Env prod`,
   with the working directory `nextron-engine`.
   - Quote every path: Mac's checkout path has spaces.
   - Never end a quoted path with a backslash.
5. **The child is started with `ProcessStartInfo`**, and `NEXTRON_ENV` is set in its
   environment only, never in the supervisor's. `UseShellExecute` is false. The child shares the
   supervisor's hidden console (no `CreateNoWindow`), so uv's Python doesn't open a console
   window of its own.

## Fallbacks if non-elevated registration fails

For Mac to decide, only if row 1 fails:

| Option | How | For | Against |
|---|---|---|---|
| A. Register once elevated | run the install script once as administrator; the task itself still runs `Interactive`/`Limited` | keeps every AD-20 setting | needs admin at install, and again whenever the task changes |
| B. `schtasks.exe` | `schtasks /Create /SC ONLOGON /RL LIMITED /TN … /TR …`, or `/XML` with the exported definition | same Task Scheduler, different API | may hit the same refusal |
| C. Startup folder shortcut | a hidden-window shortcut to `run.ps1` in `shell:startup` | no Task Scheduler, no admin | no IgnoreNew (the engine's lock and exit 3 cover a second copy); no "start when available"; runs only at logon |
| D. `HKCU\…\Run` value | the same command as a per-user Run entry | no admin | as C |
| E. Start by hand | a desktop shortcut Mac clicks | simplest | the engine doesn't start by itself at logon (NFR-42) |

## What Story 1.21 must do

- **Build `scripts/run.ps1` from `supervisor.ps1`** with the changes above, without the fake and
  the test-only parameters.
  - It loops `uv run --frozen python -m nextron serve`.
  - It logs to `<data dir>\logs\supervisor.log`, with the data dir resolved as the engine does:
    `NEXTRON_DATA_DIR`, else `%LOCALAPPDATA%\Nextron\<env>`.
  - It rotates or caps that log, which the spike lets grow.
- **Build `scripts/install_logon_task.ps1` from `oq_a9.ps1`'s parts builder** (trigger,
  principal, settings and action built once).
  - It refuses to run elevated.
  - It replaces an existing task of that name, and `-Uninstall` removes it.
- **Handle `Stop-ScheduledTask`, or "End" in the Task Scheduler window, leaving the engine
  running.**
  - At the next start the new supervisor's engine exits 3 (the lock is held), the loop ends, and
    the old engine runs with no supervisor.
  - The likely fix is a Windows job object with kill-on-close (P/Invoke through `Add-Type`), so
    the engine ends with its supervisor. It must be race-free:
    - The supervisor puts **itself** into the job at startup, so every child it starts inherits
      it. Alternatively, it starts the child suspended through `CreateProcess` and assigns it
      before resuming it.
    - Assigning the child after `Process.Start` races `uv`, which may already have started its
      Python.
  - The same job object covers `uv` dying while its Pythons live on. That otherwise leaves an
    unsupervised engine, whose lock makes the next start exit 3.
  - **A graceful stop:** Uvicorn 0.54 handles CTRL_BREAK, so the child would need its own
    process group and a CTRL_BREAK before a tree kill.
    - `ProcessStartInfo` can't set `CREATE_NEW_PROCESS_GROUP`, so this needs `CreateProcess`.
    - Whether CTRL_BREAK passes through `uv.exe` and the venv launcher to Python is untested.
- **Upgrades: restart only the child.** After `git pull`, stop only the child tree, so `run.ps1`
  restarts it. Running `uv sync --frozen` while the engine holds its `.pyd` files open can fail
  on Windows, and `uv run --frozen` syncs at each start anyway.
- **Stop a loop of exit 2.** Exit 2 restarts forever. A migration that keeps failing takes a new
  `pre_migrate` copy at every start, and those copies are never rotated. At the 5-minute backoff
  cap that is about 288 copies a day, enough to fill the disk. Story 1.21 should cap or stop on
  repeated exit 2, and make it visible.
- **Nothing alerts on the supervisor.** The task has no restart-on-failure setting. A dead
  supervisor, a crash loop or a stop after exit 3 raises no alert, because alerts come from the
  running engine. Mac decides any setting beyond AD-20.
- **Measure the start grace, and fix the reset.** The 900 s grace hasn't been measured against a
  real startup (the `pre_migrate` copy, a migration, the recovery pass). Also, since 900 s is
  more than the 600 s reset, a hung start resets the backoff every time. Basing the reset on the
  time since the child's first beat avoids that.
- **Check the window really stays hidden.** On Windows 11 with Windows Terminal as the default
  terminal, `-WindowStyle Hidden` may leave a visible window, and closing it kills the
  supervisor and the engine. A possible remedy is `conhost.exe --headless` (unverified). Mac's
  rows 2 and 3 watch for a window.
- **Decide the task priority.** The default 7 runs the engine below normal priority;
  `New-ScheduledTaskSettingsSet -Priority` can set it.
- **Expect a slow first start now and then.** In the early run, about 42 s passed between
  `Start-ScheduledTask` and the supervisor's first log line; in the second run it was 1.4 s. The
  delay comes before the supervisor runs, so the start grace plays no part. It only delays the
  engine's start at logon.
- **Write down the stop procedure for a restore.** `nextron restore` holds `nextron.lock` for its
  whole run. A `serve` started meanwhile exits 3, and exit 3 ends the loop.
  1. Stop the task and its whole tree (with the job object above, `Stop-ScheduledTask` is
     enough; without it, also end the engine's `uv` and Python processes).
  2. Run `nextron restore`.
  3. Run `Start-ScheduledTask` again.

  The deferred "clean stop on Windows" item (Story 1.1) belongs here too.
- **Observe the trigger catch-up after a real sleep.** This spike checks only the supervisor
  across a sleep. The trigger catch-up in the engine README ("After the PC sleeps") is still
  unobserved on a real sleep.
