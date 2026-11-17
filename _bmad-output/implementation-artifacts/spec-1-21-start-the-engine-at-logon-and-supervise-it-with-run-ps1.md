---
title: 'Story 1.21: Start the engine at logon and supervise it with run.ps1'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '2cd6479e0fd1b15496963300753b0385cb6ad932'
review_loop_iteration: 0
story_key: '1-21-start-the-engine-at-logon-and-supervise-it-with-run-ps1'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/docs/spikes/oq-a9-logon-task.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing starts the engine at logon or restarts it after a crash or hang (AD-20, OP-3, OP-4, OP-5, NFR-14, NFR-42). The OQ-A9 spike (Story 1.20) proved the loop with a fake and found that `Stop-ScheduledTask` leaves the engine running, that `uv` sits two layers above the real Python, and that a crash loop of exit 2 can fill the disk with `pre_migrate` copies.

**Approach:** `scripts/run.ps1` is the production supervisor, built from the spike's `supervisor.ps1` with the record's parameter changes. It runs each child in its own kill-on-close job object and process group, can stop the child gracefully, and stops on repeated exit 2. `scripts/install_logon_task.ps1` prints the AD-20 task by default and registers it only with `-Register`. `nextron serve` exits 0 on a CTRL_BREAK stop. The build never changes Task Scheduler state: the live checks go to Mac's checklist.

The acceptance criteria are Story 1.21 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Task Scheduler safety (Mac's real PC).** No test, script run, probe or subagent calls a state-changing Task Scheduler command (`Register-`, `Unregister-`, `Start-`, `Stop-`, `Set-`, `Enable-`, `Disable-ScheduledTask`, `schtasks /create|/delete|/run|/end|/change`, `Schedule.Service` writes). Read-only `Get-ScheduledTask` is allowed by hand; tests shadow it too. Tests run `install_logon_task.ps1` only with every ScheduledTasks command shadowed through `tests/task_scheduler_guard.py`, the one file under `tests/` that may name a state-changing command. A contract enforces this.
- **Processes.** Every test kills what it started (the PowerShell's tree and every pid its logs name) in a `finally`. Tests use `tmp_path`, `NEXTRON_ENV=test`, a temporary `%LOCALAPPDATA%` and a free port, and never touch the real `%LOCALAPPDATA%\Nextron`.
- **Windows PowerShell 5.1**, ASCII-only `.ps1` files. `run.ps1` never names a ScheduledTasks command.
- `NEXTRON_ENV` and `NEXTRON_DATA_DIR` are set for the child only; the supervisor's own environment never changes.

**Never:**
- No migration, setting, credential, dependency or new route. The only engine change is the CTRL_BREAK handler in `serve`.
- Don't delete or change `scripts/spike/` (Mac's OQ-A9 rows still use it), except moving its tests onto the shared guard.
- No alert, Task Scheduler restart-on-failure setting or priority change beyond AD-20.

### Decisions made overnight (pending user review)

1. **Dry run by default.** `install_logon_task.ps1` with no switch prints the task and registers nothing. `-Register` registers or replaces it (`Register-ScheduledTask -Force`), and `-Uninstall` stops and removes it. Every action refuses to run elevated (exit 2). Under `NEXTRON_ENV=test` it refuses (exit 2) to call any ScheduledTasks command that comes from a module. Mac runs `-Register` himself.
2. **Stale counting.** Stale is counted in the supervisor's own polls, not from the file's age, per the record's change 1. Each quiet poll adds `PollSeconds`, and a change resets the count. The limit is 300 s after the child's first beat, and a 900 s start grace before it. AD-20's literal wording would kill the engine after every wake.
3. **"Up 10 minutes" counts from the child's first beat,** so a hung start never resets the backoff.
4. **Repeated exit 2 stops the loop.** After 5 consecutive exits with code 2 (not killed, not requested), the supervisor logs `stop` (`reason` `repeated_exit_2`) and exits 2, so Task Scheduler shows 2. Any other outcome resets the count. This caps a failing migration at 5 `pre_migrate` copies per task start.
5. **A job object per child.** Each child starts suspended, in a new process group, and joins a kill-on-close job before it resumes. A kill is `TerminateJobObject`, judged by the job's active-process count. When the supervisor dies (`Stop-ScheduledTask`, "End"), the whole tree dies with it.
   - Probed on this PC with test processes: `uv run --frozen` runs inside the job; the venv launcher's nested job works; `TerminateJobObject` ends all three processes; and `TerminateProcess` on the supervisor's PowerShell ends the tree.
6. **Graceful stop.**
   - `run.ps1 -Env <env> -Stop` or `-Restart` writes a one-shot request, `<data dir>\supervisor-request`. The supervisor reads it at each poll and sends CTRL_BREAK to the child's group. It waits up to `StopGraceSeconds` (60) for the job to empty, then kills it. `stop` ends the loop (exit 0); `restart` starts the child again at once, with the backoff and the exit-2 count reset.
   - Probe result: Python gets the CTRL_BREAK; `uv.exe` dies at once (0xC000013A) while the launcher waits for Python. So the supervisor always waits for the job to empty, never for `uv` alone.
   - `serve` installs a SIGBREAK handler. The CTRL_BREAK that Uvicorn re-raises after its graceful shutdown then no longer kills the process, so `serve` exits 0 and logs `engine_stopped`. This closes the Story 1.1 deferred clean-stop item.
7. **Upgrade.** The README gives two routes:
   - `-Stop`, `git pull`, `uv sync --frozen`, then `Start-ScheduledTask`. This is AD-20's order, and it also picks up changes to `run.ps1`.
   - The quick route: `git pull`, then `-Restart`. `uv run --frozen` syncs while the engine is stopped.

   The AC's "child stopped → run.ps1 starts the new code" is tested through `-Restart` and through a killed child.
8. **One data dir.** `run.ps1` resolves the data dir as the engine does: `NEXTRON_DATA_DIR`, with `%VAR%` and a leading `~` expanded and relative paths taken against `nextron-engine`; otherwise `%LOCALAPPDATA%\Nextron\<env>`. It passes the absolute result to the child as `NEXTRON_DATA_DIR`.
9. **Test switches.** `-ServeCommand`, `-MaxRestarts` and the timing parameters are for tests only; a test pins the timings' AD-20 defaults. The poll is 5 s (the spike used 15), so requests are answered quickly.
10. **One supervisor per environment.** `<data dir>\supervisor.lock` is held open (no sharing) for the supervisor's whole run. A second supervisor prints one line and exits 3. A leftover request is discarded at start.
11. `supervisor.log` rotates at 1 MiB, keeping 3 old files.
12. **Not built; Mac decides or it is deferred:**
    - the task priority stays at Task Scheduler's default 7 (below normal);
    - nothing restarts a dead supervisor or alerts on one, on a crash loop or on a stop;
    - the window stays `-WindowStyle Hidden`, and Mac checks that none appears;
    - there is no engine-side wake allowance for a job that ran across a sleep (new `deferred-work.md` entry);
    - the data dir isn't yet bound to its environment (the Story 1.8 entry, still due before the first `prod` install).
13. **Live checks go to Mac.** These checks go into a new "Story 1.21 rows" table in the OQ-A9 record and into the README, because the build may not run them:
    - the real registration;
    - the start at logon;
    - `Stop-ScheduledTask` ending the whole tree;
    - sleep and wake;
    - no visible window;
    - `-Stop` and `-Restart` under the task;
    - `-Uninstall`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Crash | the child exits with any code but 3 (or 2 five times running) | `exit`, then `backoff` 5, 10, 20 … 300 s, then `start` | N/A |
| Long run | the child beat, then ran `ResetAfterSeconds` past its first beat | the next wait is the start value again | a child that never beat doesn't reset it |
| Exit 3 | the child exits with 3 by itself | `stop` (`lock_held`), the supervisor exits 3 | N/A |
| Repeated exit 2 | 5 consecutive exits with code 2 | `stop` (`repeated_exit_2`), the supervisor exits 2 | another outcome resets the count |
| Stale | after a first beat, no change for `StaleAfterSeconds` of polls | `stale`, `kill` (`job`, `ok` true), no pid of the tree left, then a restart | job not empty after 10 s: `Stop-Process` on each job pid, then a second `kill` (`process_kill`) |
| Start grace | no beat yet; a missing or hour-old file | not killed before `StartGraceSeconds`; killed at it (`before_first_beat`) | N/A |
| Orphans | the child exits but its job still holds processes | `kill` (`orphans`), the tree is gone, a normal restart | N/A |
| Supervisor killed | `TerminateProcess` on the supervisor's PowerShell (as `Stop-ScheduledTask`) | every process of the child's tree ends | N/A |
| Stop | `run.ps1 -Stop` while supervising | `request`, CTRL_BREAK, the child exits gracefully, then `stop` (`requested`), exit 0; `-Stop` prints "stopped" and exits 0 | not out in `StopGraceSeconds`: `kill` (`stop_timeout`); no supervisor: exit 1, no request written |
| Restart | `run.ps1 -Restart` | graceful stop, then `start` at once (no `backoff`), running the code on disk now | as Stop |
| Env | `-Env prod`, `NEXTRON_DATA_DIR` unset | the child sees `NEXTRON_ENV=prod` and `NEXTRON_DATA_DIR=<LOCALAPPDATA>\Nextron\prod`; the supervisor's own env is unchanged | N/A |
| Second supervisor | the lock is held | one stderr line, exit 3, no child started | N/A |
| Cannot start | the executable is missing | `start_failed`, backoff, retry | N/A |
| Real engine | `-ServeCommand` = this Python `-m nextron serve`, on a database one revision behind | a `pre_migrate` copy, the DB at head before the first beat; `-Restart` and then `-Stop` log `engine_stopped` with exit 0 twice | N/A |
| Install dry run | no switch | the AD-20 task "Nextron Engine (prod)" with action `run.ps1 -Env prod` printed; nothing called | N/A |
| Install refusals | elevated, `-Register -Uninstall` together, or `NEXTRON_ENV=test` with module commands | one stderr line, exit 2, nothing called | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/scripts/spike/supervisor.ps1` -- the base for `run.ps1`. Reuse `Write-SupervisorEvent` (JSON lines, UTF-8 without BOM, 5 tries), `Format-Argument`/`Join-Arguments`, `Get-HeartbeatTicks`, the poll loop and the backoff arithmetic. Drop the fake default, `-TaskkillPath`, `Invoke-Taskkill` and `Get-Descendants`: the job replaces them.
- `nextron-engine/scripts/spike/oq_a9.ps1:66-170` -- `Test-Elevated`, `New-SpikeTaskParts` (trigger, principal, settings and action built once), `Format-TaskParts` and `Format-TaskEvidence`, the base for `install_logon_task.ps1`. The action quoting is at lines 88-100.
- **Probe code** -- C# for `Add-Type` covering `CreateJobObjectW`, `SetInformationJobObject` (class 9, flag 0x2000), `QueryInformationJobObject` (class 1 for `ActiveProcesses`, class 3 for the pid list), `CreateProcessW` (`CREATE_SUSPENDED|CREATE_NEW_PROCESS_GROUP|CREATE_UNICODE_ENVIRONMENT`, inherit handles true, no `STARTF_USESTDHANDLES`), `AssignProcessToJobObject`, `ResumeThread`, `GenerateConsoleCtrlEvent(1, pid)`, `TerminateJobObject`, `WaitForSingleObject` and `GetExitCodeProcess`. A working version is in the orchestrator's scratchpad, `probe121/probe.ps1`.
  - The environment block is the current environment plus the overrides, sorted case-insensitively, NUL-separated and ending in a double NUL, passed through `Marshal.StringToHGlobalUni`.
  - Keep each handle; close them all at child end. Waits are relative, so time asleep doesn't count.
- `nextron-engine/nextron/main.py:1115-1214` -- `serve()`. Install `signal.signal(signal.SIGBREAK, <no-op>)` (on Windows, when `SIGBREAK` exists) just before `run_server`, and restore the previous handler in the `finally`. Uvicorn 0.54's `capture_signals` (`.venv/Lib/site-packages/uvicorn/server.py`) saves that handler and re-raises the signal to it after shutdown, so `run_server` returns and the exit is 0. Add a "Stopping" paragraph to the module docstring at lines 1-60.
- `nextron-engine/nextron/bootstrap.py` -- the engine's data-dir rule, which `run.ps1` mirrors.
- `nextron-engine/tests/app/test_spike_supervisor.py` -- the harness to copy: `run_powershell` (`-EncodedCommand`, `CREATE_NEW_CONSOLE`, hidden), `gone`, `kill_python`, `read_lines`, `Run`, and `run_oq_a9`'s shadowing at lines 603-631. Move `STATEFUL_TASK_COMMANDS` and the shadow builder into `tests/task_scheduler_guard.py`, and import them here.
- `nextron-engine/scripts/spike/fake_serve.py` -- the base for the new test fake.
- `nextron-engine/tests/unit/test_serve.py:~694-770` and `tests/unit/test_migrations.py` -- how tests build a database behind head (Alembic `command.upgrade` to the previous revision), for the real-engine test.
- `nextron-engine/tests/app/test_serve_process.py:44-75` -- `free_port`, and the `engine_env` pattern for a real engine child.
- `nextron-engine/tests/contracts/test_source_tree.py` `FILES` -- add `scripts/run.ps1` and `scripts/install_logon_task.ps1`.
- `nextron-engine/README.md` -- "Run the engine" (lines 133-164: change "a later story" and add exit-code notes) and "Restore and recovery mode" (line ~1131: the stop step). Add a new section, "Start at logon and supervision", after "Logon task spike (OQ-A9)".
- `docs/spikes/oq-a9-logon-task.md` -- add "Story 1.21 rows" for Mac after "Mac's rows", and mark the "What Story 1.21 must do" items as done or deferred.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- append the engine-side wake entry.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/scripts/run.ps1` -- the production supervisor:
  - **Parameters.** `-Env` (mandatory: dev, test or prod). Exactly one of `-Stop`, `-Restart` or `-Status`, or none. The test switches `-ServeCommand` (`string[]`) and `-MaxRestarts` (-1 means forever). The timings, with these defaults: `-BackoffStartSeconds` 5, `-BackoffMaxSeconds` 300, `-ResetAfterSeconds` 600, `-StaleAfterSeconds` 300, `-StartGraceSeconds` 900, `-PollSeconds` 5 and `-StopGraceSeconds` 60. Also `-RequestTimeoutSeconds` 120.
  - **The default command** is `uv.exe run --frozen python -m nextron serve`, with `uv.exe` resolved through `Get-Command`. The working dir is `nextron-engine`.
  - **Events:** `supervisor_start` (with every setting), `request_discarded`, `start`, `start_failed`, `exit` (`code`, `uptime_s`, `up_s` since the first beat or null, `killed`, `requested`), `stale`, `kill` (`reason`, `method`, `ok`, `pids`), `request` (`text`, `accepted`), `backoff`, `stop` (`code`, `reason`), `max_restarts`, `supervisor_refused` and `supervisor_error`.
  - **`finally`:** a gracefully stopped or killed child, then the job handles closed.
  - **`-Stop` and `-Restart`:**
    - with no supervisor running (the lock is free), print one line and exit 1, writing nothing;
    - otherwise write the request through a temporary file and a rename;
    - `-Stop` waits for the lock to be released, and `-Restart` for a `start` logged after its `request`;
    - print the outcome; exit 0, or 1 on timeout.
  - **`-Status`** prints whether a supervisor runs, the heartbeat's age and the last 15 log lines. It never names Task Scheduler.
- [x] `nextron-engine/scripts/install_logon_task.ps1` -- the task "Nextron Engine (prod)", built once:
  - the trigger `-AtLogOn -User <me>`;
  - the principal `Interactive`/`Limited`;
  - the settings `PT0S`, `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew`;
  - the action `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "<engine>\scripts\run.ps1" -Env prod`, with the working dir `<engine>`;
  - a fixed description.

  The modes are the dry run, `-Register` (then it prints `Get-ScheduledTask`/`Get-ScheduledTaskInfo`) and `-Uninstall` (stop, then unregister; "nothing to remove" when there is no task). The refusals come first.
- [x] `nextron-engine/nextron/main.py` -- the SIGBREAK handler and the docstring.
- [x] `nextron-engine/tests/fake_serve.py` -- the stdlib test fake, from the spike's fake without the control file:
  - `--heartbeat`, `--interval`, `--log`, `--beat-delay`, `--stall`, `--exit-after`/`--exit-code` and `--grandchild`;
  - `--on-break graceful|ignore` (`graceful` logs `break`, waits `--break-delay`, then exits 0);
  - `start` logs `NEXTRON_ENV` and `NEXTRON_DATA_DIR`.
- [x] `nextron-engine/tests/task_scheduler_guard.py` -- `STATEFUL_TASK_COMMANDS` and `shadow_lines(marker, stand_ins)`:
  - `Import-Module ScheduledTasks`, then a global tripwire or stand-in function for each command;
  - each one verified to have no `Module`;
  - `$PSModuleAutoloadingPreference = 'None'`.
- [x] `nextron-engine/tests/app/test_run_supervisor.py` -- a test per matrix row of `run.ps1`:
  - the timing defaults pinned through `supervisor_start`;
  - the log's format and rotation;
  - the data-dir rule (unset, relative, `%VAR%`);
  - a `-Status` test.

  Assert on log events and on the pids being gone, not on timing.
- [x] `nextron-engine/tests/app/test_install_logon_task.py` -- the install rows. `-Register` and `-Uninstall` run with recording stand-ins. The `NEXTRON_ENV=test` refusal runs with stand-ins inside a dynamic module named `ScheduledTasks`, so a broken guard calls only fakes.
- [x] `nextron-engine/tests/contracts/test_task_scheduler_contracts.py`:
  - no file under `tests/` but the guard names a state-changing command, with a probe on planted files;
  - `run.ps1` names no `ScheduledTask` and no `schtasks`;
  - both scripts are ASCII;
  - the guard's shadows hold.
- [x] `nextron-engine/tests/unit/test_serve.py` -- the fake `run_server` finds a Python SIGBREAK handler installed, calls it, and returns, so `serve` gives 0. Afterwards the old handler is back.
- [x] `tests/app/test_spike_supervisor.py`, `tests/contracts/test_source_tree.py`, `README.md`, `docs/spikes/oq-a9-logon-task.md` and `deferred-work.md` -- as the Code Map says.

**Acceptance Criteria:**
- Given the tests, when they run, then they cover the backoff, the reset, exit 3, repeated exit 2, the stale kill of the whole tree, the start grace, orphans, the supervisor's death, stop, restart, the per-child environment and the install script, and no state-changing Task Scheduler command runs.
- Given the real engine under `run.ps1`, when it is restarted and stopped by request, then it migrates with a `pre_migrate` copy before its first beat and each stop logs `engine_stopped` with exit 0.
- Given the README and the record, when Mac reads them, then he has the exact install, check, stop, restore, upgrade and uninstall steps, and the live rows to run.

## Implementation Notes

**Files.** New: `nextron-engine/scripts/run.ps1`, `scripts/install_logon_task.ps1`, `tests/fake_serve.py`, `tests/task_scheduler_guard.py`, `tests/app/test_run_supervisor.py` (37 tests), `tests/app/test_install_logon_task.py` (16), `tests/contracts/test_task_scheduler_contracts.py` (7). Changed: `nextron/main.py`, `tests/unit/test_serve.py` (+3), `tests/app/test_spike_supervisor.py`, `tests/contracts/test_source_tree.py`, `README.md`, `docs/spikes/oq-a9-logon-task.md`, `deferred-work.md`. No migration, setting, credential, dependency or route.

**`run.ps1`.**
- ASCII, LF, Windows PowerShell 5.1, `Set-StrictMode -Version 2.0`. The C# (`NextronRun.Native`) is loaded by `Add-Type` only in supervising mode, so `-Stop`, `-Restart` and `-Status` start fast. It wraps:
  - `CreateJobObjectW` and `SetInformationJobObject` (class 9, `KILL_ON_JOB_CLOSE`);
  - `QueryInformationJobObject` (class 1 for `ActiveProcesses`, class 3 for the pid list, up to 1,024);
  - `CreateProcessW` (`lpApplicationName` null, inherit handles true, `CREATE_SUSPENDED|CREATE_NEW_PROCESS_GROUP|CREATE_UNICODE_ENVIRONMENT`, no `STARTF_USESTDHANDLES`);
  - `AssignProcessToJobObject`, then `ResumeThread` (a failure of either terminates the suspended child and closes its handles);
  - `GenerateConsoleCtrlEvent(1, pid)`, `TerminateJobObject`, `WaitForSingleObject` and `GetExitCodeProcess` (logged as a signed int, as .NET's `ExitCode` does).
- The environment block is this process's environment (entries starting `=` skipped) plus `NEXTRON_ENV` and `NEXTRON_DATA_DIR`, sorted with `OrdinalIgnoreCase`.
- Each child gets a new job. `Close-Child` closes the thread, process and job handles when the child ends. Every wait is a relative slice (the poll's `WaitForSingleObject`, 50 ms slices for "job empty", poll-sized `Start-Sleep` slices for the backoff), so time asleep never counts.
- The poll loop runs these checks in order: has the child ended? is there a request? has the heartbeat changed (`quiet` resets, the first beat is noted)? Otherwise `quiet += PollSeconds`, and at `quiet >= limit` (1e-9 tolerance) it logs `stale`, then `kill` (`stale`).
- After a child ends by itself, the job gets 2 s to empty before `kill` (`orphans`), so a launcher and its Python that end a moment after `uv` aren't killed. An orphans kill doesn't set `killed`: the exit code is still the child's own, so an exit 3 or 2 still counts.
- `Invoke-Kill` takes the pid list for the log (best effort, in its own `try`), calls `TerminateJobObject(job, 1)` in its own `try` (so a failed listing never skips it), then waits up to 10 s for the job to empty (`ok`); errors go to the event's `error`. Otherwise it runs `Stop-Process -Force` on each pid still in the job and logs a second `kill` (`process_kill`). The tests-only switch `-FailJobKillOnce` makes the first kill skip `TerminateJobObject` (`ok` is then read at once, with no 10 s wait), so the fallback is tested; `supervisor_start` logs it as `fail_job_kill_once`. The child handle is then waited on without a bound, as the spike did, so no second engine is ever started beside one that survived.
- Graceful stop: CTRL_BREAK to the child's group, then up to `StopGraceSeconds` for the job to empty, else `kill` (`stop_timeout`). If `GenerateConsoleCtrlEvent` itself fails, it kills at once with reason `ctrl_break_failed`, an extra reason not in the spec's list.
- **Requests.**
  - The client writes `<verb> <32-hex id>` to `supervisor-request.<pid>.tmp`, deletes any older request and renames its file into place.
  - The supervisor claims a request by renaming it to `.<pid>.claimed`, reads it and deletes it. It logs `request` with `text` (the verb), `accepted` and `id` (an extra field).
  - Unknown text is logged (first 64 characters) with `accepted` false, and ignored.
  - Requests are read at every poll, and at every poll-sized slice of a backoff. There, a `stop` ends the loop (exit 0), and a `restart` starts the child at once with the backoff and the exit-2 count reset.
  - A restart requested while the child runs doesn't count toward `-MaxRestarts`.
- **`-Restart` matches its request by the id.** It finds the `request` line holding `"id":"<id>"` in `supervisor.log.1` and `supervisor.log`, then waits for a later event:
  - a `start` gives exit 0;
  - a `start_failed` or `stop` gives exit 1 ("did not start again");
  - a supervisor that ends first also gives exit 1.

  On a client timeout, a request nobody claimed that is still its own is deleted. Failures go to stderr, outcomes to stdout. A request that can't be renamed into place deletes its temporary file and exits 1.
- The restart outcome names what is known: `restarted: the supervisor for <env> started a new engine process (pid <pid from the start event>); run.ps1 -Env <env> -Status shows its heartbeat`. The `start` event only means the process was created.
- **A leftover request.** The supervisor notes the time it took the lock. At start it discards (`request_discarded`) only a request whose last-write time is older than that; one written since (a client saw this supervisor running) is answered at the first poll. The delete is best effort: a failure is logged in the event's `error` and the supervisor goes on.
- **The lock.** The supervisor opens `supervisor.lock` with `OpenOrCreate`, `ReadWrite` and `FileShare.None`, trying 10 times 100 ms apart (a `-Status` probe may hold the file for a moment).
  - Clients probe it with `FileMode.Open`. A missing file or folder means no supervisor runs, and nothing is created. A sharing or lock violation (HRESULT low word 32 or 33) means one does.
  - A refused second supervisor logs `supervisor_refused` (`reason` `lock_held`, `lock`) without rotating the log, prints one stderr line and exits 3.
- **The data dir.**
  - `NEXTRON_DATA_DIR` is used when set; an empty value counts as unset.
  - It goes through `ExpandEnvironmentVariables`. A leading `~\` or `~/`, or a bare `~`, becomes `USERPROFILE`.
  - A rooted path with no drive goes on the engine's drive, and a relative one is taken against `nextron-engine`. `GetFullPath` follows, without a trailing backslash.
  - Unset, it is `%LOCALAPPDATA%\Nextron\<env>`, with `<profile>\AppData\Local` when `%LOCALAPPDATA%` is unset.
  - `$VAR` is not expanded: the decision lists only `%VAR%` and `~`. The child receives the absolute result either way.
- **The log.** `Step-Log` rotates before a write once `supervisor.log` reaches 1 MiB: `.3` is deleted, `.2` becomes `.3`, `.1` becomes `.2` and the log becomes `.1`. Only the lock holder rotates, and a failed rotation is retried at the next write.
- `-Status` prints the `Supervisor`, `Data dir`, `Heartbeat` and `Log` lines, then the last 15 complete lines of `.1` and the current log.
- More than one of `-Stop`, `-Restart` and `-Status` prints one stderr line and exits 2, writing nothing.

**`install_logon_task.ps1`.**
- The modes set `$script:ExitCode` instead of returning it, so no output is captured.
- `Get-Refusal -Elevated -Register -Uninstall -NextronEnv` returns the first refusal: elevated, then both switches, then, under `NEXTRON_ENV=test` (trimmed), any of the five commands it can call that `Get-Command` resolves to a module's command. The five are `Get-ScheduledTask`, `Get-ScheduledTaskInfo`, `Register-ScheduledTask`, `Stop-ScheduledTask` and `Unregister-ScheduledTask`.
- The refusal check runs in every mode, the dry run included. The `New-ScheduledTask*` builders aren't checked: they make objects in memory, and they run for real in the dry-run test, as in Story 1.20.
- The action's `Execute` is the expanded `%SystemRoot%` path, as the spike proved.
- Every Task Scheduler call names the root folder (`$TaskPath = '\'`, passed as `-TaskPath` to each `Get-ScheduledTask`, `Get-ScheduledTaskInfo`, `Register-`, `Stop-` and `Unregister-ScheduledTask`), so a task of the same name in another folder is never read or changed. The dry run's `Task name` line shows the folder.
- The dry run and `-Register` print `uv.exe      : <path>` for the `uv.exe` that `Get-Command -CommandType Application` resolves, or `uv.exe      : not found on PATH; run.ps1 cannot start the engine` (a warning, not a refusal). The label is padded like the others.
- `-Register` builds the task once with `New-ScheduledTask` (with the fixed description) and calls `Register-ScheduledTask -Force`. A failure prints one stderr line and exits 1. A failed read-back afterwards (`Get-ScheduledTask` or `Get-ScheduledTaskInfo`) prints one stderr line, `install_logon_task: registered; reading it back failed: ...`, and the exit code stays 0.
- `-Uninstall`:
  1. `Get-ScheduledTask`; with no task, it prints "Nothing to remove" and exits 0.
  2. If the task is `Running`, `Stop-ScheduledTask`. A failed stop prints one stderr line ("... failed, so nothing was removed"), exits 1 and doesn't unregister, so `run.ps1` is never left running without its task.
  3. `Unregister-ScheduledTask -Confirm:$false`.
  4. A second `Get-ScheduledTask` checks that the task is gone.

  It doesn't run `run.ps1 -Stop` first. The README says to, for a graceful stop.

**`serve`.**
- `_stop_on_ctrl_break()` is a context manager around `run_server` only. When `SIGBREAK` exists and this is the main thread, it installs `_exit_on_ctrl_break`, which raises `SystemExit(0)`, then restores the previous handler (or `SIG_DFL` when `signal.signal` returned `None`). `serve`'s existing `except SystemExit` turns it into exit 0 and `engine_stopped`. It covers both the CTRL_BREAK Uvicorn raises again after its graceful shutdown and one that lands before Uvicorn's `capture_signals` takes the signal over (app build, Config and Server set-up, the event loop's start: nothing has started yet), which a no-op handler used to swallow, leaving the supervisor to wait 60 s and kill. A CTRL_BREAK before `run_server` (while the database opens or migrates) still ends the process at once.
- The module docstring has a "Stopping" paragraph, and its heartbeat paragraph now describes the poll-count rule.
- Probed through `uv` on this PC, with a temporary data dir, `-Env test` and a free port: `-Restart` and `-Stop` each logged `engine_stopped` with `exit_code` 0, and `uv`'s own exit code was -1073741510.

**Tests.**
- `tests/task_scheduler_guard.py` exports:
  - the command names as constants (`REGISTER` to `EXPORT`) and `STATEFUL_TASK_COMMANDS`;
  - `PRELOADED_MODULES`: `Microsoft.PowerShell.Management`, `Microsoft.PowerShell.Utility`, `CimCmdlets` and `ScheduledTasks`. With autoloading off, every module a script uses must be loaded first, and `oq_a9.ps1`'s Unregister needs `Get-CimInstance`;
  - `ps_string`, `ps_array`, `tripwire` and `shadow_lines`, which refuses a stand-in for any other name.

  The check after the shadows requires exactly one `Function` with no module for each name.
- The contract scans every file under `tests/`, `nextron/` and `scripts/` except `__pycache__`. Only `tests/task_scheduler_guard.py`, `scripts/install_logon_task.ps1` and `scripts/spike/oq_a9.ps1` may match its case-insensitive pattern:
  - the seven verbs followed by `-ScheduledTask`;
  - the PSScheduledJob verbs (Register, Unregister, Set, Enable, Disable) followed by `-ScheduledJob`;
  - `PS_ScheduledTask` (the CIM class whose methods those commands call);
  - a module-qualified `ScheduledTasks\<verb>-<noun>` (a qualified name skips the shadows, reads included);
  - `schtasks` followed on its line by `/` or `-` and create, delete, run, end or change;
  - the COM object's name.

  The pattern is assembled from parts, so the contract never matches itself; the planted-file probe has a case for each form and lets the read-only ones (`Get-`, `Export-`, `New-...Trigger`, `Get-ScheduledJob`, `Import-Module ScheduledTasks`, `MSFT_ScheduledTask`, `schtasks /Query`) through. `run.ps1` must not contain `scheduledtask`, `scheduledjob`, `schtasks` or the COM object's name, in any case.
- `test_spike_supervisor.py` now uses `shadow_lines` and the guard's `GET`, and its comment no longer names the commands that stop and remove a task.
- **`test_run_supervisor.py`** uses a `Supervision` harness.
  - `start` starts each PowerShell hidden, in a new console, with the temporary folder as `cwd` and its own `.out` and `.err` files. `finish` waits for it.
  - `cleanup` kills every tree and every Python pid the logs name.
  - The real-engine test copies the real chain without its head into a temporary chain and brings a database to that chain's head with `ensure_current`. It sets `NEXTRON_BACKUP_DIR`, a free `NEXTRON_PORT` and `PYTHONIOENCODING`.
  - It then waits for the heartbeat file before `-Restart`, and for the file's next change before `-Stop`, so the second engine is serving when CTRL_BREAK arrives.
  - `-FailJobKillOnce` drives the `process_kill` fallback: a stale kill logs `kill` (`job`, `ok` false), then `kill` (`process_kill`, `ok` true) naming the whole tree, every pid is gone, and the child restarts (the second kill is a normal job kill).
  - A `-Restart` sent during a 30 s backoff starts the child at once (no second `backoff` between the request and the `start`), and the next `backoff` is 30 s again, not 60.
  - The `-Stop` and `-Restart` timeout runs against a `supervisor.lock` held unshared through `CreateFileW` (share mode 0) with `-RequestTimeoutSeconds 0.5`: exit 1, one stderr line ("no answer ..."), and no `supervisor-request` left. The handle is closed in a `finally`.
- The elevated refusal is tested by taking `Get-Refusal` out of the script with the PowerShell parser and calling it with `-Elevated $true`, because the test's PowerShell can't be elevated.
- `test_install_logon_task.py`: the stand-ins accept and record `-TaskPath`, and each call's is `\`; the dry-run test checks the `uv.exe` line against `shutil.which`, and a second dry run with `uv.exe` off PATH shows the "not found" line and exits 0. New tests: a failed read-back after a registration (exit 0, one stderr line) and `-Uninstall`'s failures, parametrized: Unregister throws, Unregister leaves the task registered, and Stop throws on a running task (no Unregister call). Each exits 1 with one stderr line and no "Removed".
- `test_serve.py`: the CTRL_BREAK test is parametrized: the installed handler is called directly, or `signal.raise_signal` runs it (only after checking it is a Python callable other than the old handler). Either way it raises `SystemExit(0)` and `serve` returns 0 with `engine_stopped` `exit_code` 0, and the old handler is back.
- No test covers:
  - `ctrl_break_failed`;
  - the `finally` stopping a live child after a `supervisor_error` or a Ctrl+C.

**Verification so far.**
- After the review patches (pass 1), one targeted run with `-n 6` of `tests/app/test_run_supervisor.py` (37), `tests/app/test_install_logon_task.py` (16), `tests/unit/test_serve.py` (47) and `tests/contracts/test_task_scheduler_contracts.py` (7): 107 passed. `lint-imports`: 29 kept. Afterwards no Nextron task existed, no process this track started was left, and both scripts were ASCII.
- Before the patches:
- The spec's set (`test_run_supervisor.py` 33, `test_install_logon_task.py` 11, `test_spike_supervisor.py` 41, `tests/unit/test_serve.py` and `tests/contracts`), run with `-n 6`: 988 passed, 1 skipped (ai_eval).
- After the last `run.ps1` change (a request that can't be renamed into place deletes its temporary file and exits 1), `test_run_supervisor.py`, `test_task_scheduler_contracts.py` and `test_source_tree.py` ran again: 207 passed. `lint-imports`: 29 kept.
- No test process, `run.ps1`, fake or engine child was left afterwards, and nothing was created under the real `%LOCALAPPDATA%\Nextron`.
- The dry run printed the task. `Get-ScheduledTask | ? TaskName -like 'Nextron*'` found nothing before or after.

**Final verification** (orchestrator, on the final tree, through the shared suite lock):
- `uv run pytest -q -n 6 --dist loadgroup`: 4987 passed, 2 skipped (`ai_eval` and the symlink test). `uv run lint-imports`: 29 kept, 0 broken.
- **Earlier gate runs on the same tree** each failed only on the known job-runner flake, a different test each time: first `test_a_second_start_raises_and_stop_is_idempotent`, then `test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper[None-1]`. This tree predates the flake fix on `main` (`496e2ba`). `tests/unit/test_job_runner.py` alone passed (41). The third run passed.
- The install script's dry run printed the task and the resolved `uv.exe`, and registered nothing.
- `Get-ScheduledTask | ? TaskName -like 'Nextron*'` found nothing before the build, after each test run, or at the end. No process naming `Nextron-epic1` was left running.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-18).**
- **Sources:**
  - BH = blind hunter: 14 findings;
  - EC = edge-case hunter: 17;
  - VG = verification gap: 5 gaps and 1 other finding.
- **Counts:** 37 raw findings give 27 entries after deduplication.
- **Outcomes:**
  - 19 entries patched;
  - 1 deferred (T);
  - 7 rejected.
- **No loopback.** No finding is an intent gap, and none needs a spec change to fix.
  - Entry A's fix (the SIGBREAK handler raises `SystemExit(0)` instead of doing nothing) departs from the Code Map's word "no-op", but meets decision 6's intent: a CTRL_BREAK stop exits 0.
  - Entry N's test-only switch follows decision 9's pattern of test switches.
- **Rejected only because the fix would change the approved spec:** the behaviour half of H. Capping consecutive pre-first-beat kills would widen decision 4 beyond exit 2. Only the README's overclaim is corrected.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH7, EC7, EC17, VG (other) | The no-op SIGBREAK handler is installed before `run_server`. Uvicorn installs its own only in `Server.serve` → `capture_signals`. A CTRL_BREAK in between (`build_app`, `Config`/`Server` set-up, `asyncio_run` start) is swallowed: the engine runs on until the 60 s `stop_timeout` kill, contradicting the README's "ends it at once". | low | Verified in `main.py:1232` → `run_uvicorn:1112` and `uvicorn/server.py` (`run` → `serve` → `capture_signals`). The window is short, and only a request right after a start hits it. The kill comes during start-up, before the runner, so no data is harmed. The direct fix: the handler raises `SystemExit(0)`. `serve`'s existing `except SystemExit` then gives exit 0 both in the window and when Uvicorn raises the signal again after its graceful shutdown. Update the docstring, README and unit test. | patch |
| B | BH4, EC3 | `Invoke-Kill` lists the job's pids before `TerminateJobObject`, in the same `try`. If the listing throws, nothing is terminated, the fallback gets an empty list, and the main loop's unbounded `Wait(child, -1)` hangs the supervisor. | low | Verified (`run.ps1:595-597`). The listing fails only on a query error or past 1,024 pids (the tree has 3), so this is unlikely. The fix is a direct reordering: terminate first in its own `try`, then list the pids best-effort. | patch |
| C | EC1 | After a failed kill and a failed fallback, `Wait($script:Child.Process, -1)` blocks forever. | low | True only if both `TerminateJobObject` and `Stop-Process` fail on the user's own process, which doesn't happen in practice. The fix adds a timeout branch and a policy for a child that survives. | reject |
| D | EC2 | After a failed kill, a surviving Python holds `nextron.lock`, so the next child exits 3 and the supervisor stops, taking it for another engine. | low | It needs the same double kill failure as C. The fix adds state that lasts across restarts. | reject |
| E | EC4 | When its second listing fails, the fallback reuses pids listed 10 s earlier, so `Stop-Process` could hit a reused pid. | low | It needs a failed query and a pid reused within 10 s. After B, the fallback runs only when `TerminateJobObject` fails. | reject |
| F | BH1, EC8 | `Resolve-DataDir` expands `%VAR%` and `~`, but `bootstrap.py`'s `os.path.expandvars` also expands `$VAR` and `${VAR}`. A `NEXTRON_DATA_DIR` written as `$LOCALAPPDATA\...` makes the supervisor and the engine disagree. | low | Verified. Decision 8 lists only `%VAR%` and `~`, and `$VAR` syntax in a Windows user variable is unusual. Mirroring `ntpath.expandvars` adds new parsing for an unlikely input. The direct fix is the README: say the task's `NEXTRON_DATA_DIR` must use `%VAR%`, not `$VAR`. | patch |
| G | BH2 | `uv`'s own failures exit 2 too (for example, an offline `--frozen` sync after the quick `git pull` route). Five in a row stop supervision until the next sign-in, but the README describes exit 2 only as a failing startup check or migration. | low | uv exits 2 on its own errors. The stop on repeated exit 2 is decision 4. The direct fix is the README and the record: name uv's own failures, and recommend AD-20's route (`uv sync --frozen` before the start) when dependencies change. | patch |
| H | BH3, EC6 | A migration or `pre_migrate` copy that runs past the 900 s start grace is killed as `stale`, which resets the exit-2 count. Each retry takes a new copy, without bound, and the README's "caps them at five per task start" overclaims. | low | Verified (`run.ps1:789-798`: a kill resets the count). A start longer than 15 minutes is unlikely (the probe's first beat came in 2.6 s). Capping pre-first-beat kills would change decision 4, so the behaviour is rejected as spec-changing. The README claim is corrected to "a migration that exits 2". | patch (README only) |
| I | BH5, EC13 | `-Uninstall` (and `-Register`'s read-back) look up "Nextron Engine (prod)" in every folder. Several matches make `State` an array string, which skips the stop, and `Unregister` could remove another folder's task. The spike's ownership check was dropped. | low | Verified: there is no `-TaskPath`. A same-named task in another folder is unlikely, but the direct fix is cheap: pass `-TaskPath '\'` to every Get-, Register-, Stop- and Unregister- call, and let the stand-ins accept it. The ownership check is rejected: the name is this story's own. | patch |
| J | BH6, EC12 | When `Stop-ScheduledTask` fails, `-Uninstall` prints to stdout, still unregisters, and exits 0 with "Removed … Checked", while `run.ps1` and the engine keep running with no task. | low | Verified (`install_logon_task.ps1:177-183`). Direct fix: on a failed stop, print one stderr line, exit 1 and keep the task, so Mac can retry. Having `-Uninstall` run `run.ps1 -Stop` first is rejected; the README says to do it. | patch |
| K | BH8 | `-Restart` prints "restarted … started the engine again" at the `start` event, which only means the process was created. A failing migration on the quick upgrade route still reports success. | low | Verified. Waiting for `start` is the spec's own task wording, so the wait stays. The direct fix is the wording: "started a new engine process (pid N); `-Status` shows its heartbeat", with the README to match. | patch |
| L | BH9, VG2 | No test covers the `-Stop`/`-Restart` timeout or the withdrawal of an unanswered request. | medium | Pre-verified by VG: deleting `Remove-OwnRequest` fails no test. The fix is a direct test: hold `supervisor.lock` unshared through ctypes `CreateFileW`, run `-Stop -RequestTimeoutSeconds 0.5`, and assert exit 1, one stderr line and no `supervisor-request` left. | patch |
| M | VG1 | No test sends a `-Restart` during a backoff, and none sees a restart reset the backoff. | medium | Pre-verified by VG: removing the restart branch in `Wait-Backoff` fails no test. Direct test: a fake that exits 1 at once, with backoffs of 30 s and 120 s. Wait for `backoff`, send `-Restart`, then assert exit 0, `start` right after `request`, and that the next `backoff` is 30 again. Then `-Stop`. | patch |
| N | VG3 | The job-kill fallback (`process_kill`), which the matrix's Stale row promises, is never exercised. | medium | Pre-verified by VG. Fix: a tests-only switch, `-FailJobKillOnce`, that skips `TerminateJobObject` on the first kill, following decision 9's test switches and the spike's `-TaskkillPath`. Then test a stale kill: `kill` (`job`, `ok` false), then `kill` (`process_kill`, `ok` true), with every pid of the tree gone. | patch |
| O | VG4 | `-Uninstall`'s failure paths (the unregister throws; the task remains) have no test. | medium | Pre-verified by VG. Direct test, parametrized over stand-ins for the unregister that throw or don't remove the task, plus J's failed stop: each exits 1 with one stderr line and no "Removed". | patch |
| P | VG5 | No test can run `install_logon_task.ps1` elevated, so `Test-Elevated` and the main path's refusal are proven in one direction only. | low | Pre-verified by VG; the build can't elevate. Direct fix: add a row to Mac's "Story 1.21 rows". From a "Run as administrator" PowerShell, `install -Register` must refuse with exit 2 and register nothing. | patch |
| Q | BH10, EC16 | The safety contract scans only `tests/`. It misses `Register-ScheduledJob` and the other PSScheduledJob verbs, CIM calls on `PS_ScheduledTask`, and module-qualified `ScheduledTasks\…` calls, which bypass the global-function shadows. | low | Verified: no violation exists today, but the rule is meant to prevent one. Direct fix: widen the pattern (the ScheduledJob verbs, `PS_ScheduledTask`, `ScheduledTasks\`). Scan `tests/`, `nextron/` and `scripts/`, allowing only the guard, `install_logon_task.ps1` and `spike/oq_a9.ps1`, with probe cases for each new form. | patch |
| R | BH11 | If `uv.exe` isn't on the task's `PATH`, `run.ps1` retries a bare `uv.exe` forever, with no sign but `start_failed` lines. | low | Verified. AD-20 restarts forever. Direct fix for visibility: the install script's dry run and `-Register` print the `uv.exe` they resolve, or "uv.exe: not found on PATH". | patch |
| S | BH12 | The README installs the `prod` task from "<your checkout>". Every automatic restart runs whatever code is on disk, so a crash restart mid-merge or on an unmerged branch could migrate the `prod` database irreversibly. | medium | Verified: this repository merges tracks in its checkout, and `downgrade()` raises. Direct fix: the README and Mac's rows require a dedicated clean checkout for `prod`, updated only through the upgrade steps. | patch |
| T | BH13 | At sign-out or shutdown Windows ends the task's console processes. `run.ps1` can't send CTRL_BREAK, and the engine handles no close, logoff or shutdown event, so it stops without `engine_stopped`. | low | True: it ends the same way the engine did before this story. A graceful stop at sign-out needs an engine console-control handler, which is new work. A `deferred-work.md` entry is added. | defer |
| U | BH14 | Two README lines run past the file's width. | low | Verified. Direct fix: wrap them. | patch |
| V | EC5 | The leftover-request check at start can discard a request sent just after the lock was taken. A `Delete` that throws there ends the supervisor with exit 1. | low | Verified (`run.ps1:694-700`). The window is milliseconds, but a throw costs supervision until the next sign-in. Direct fix: discard only a request older than the lock, and make the delete best-effort. | patch |
| W | EC9 | A data dir that can't be resolved or created at logon (a drive not ready) makes `run.ps1` exit 1 with no log and no retry. | low | The default data dir is under `%LOCALAPPDATA%` on `C:`. A retry loop is new behaviour for an unlikely set-up, and Task Scheduler shows the result 1. | reject |
| X | EC10 | Another process holding `supervisor.log` without delete sharing blocks rotation, so the log grows while it is held. | low | Rotation resumes once the file is released. The fix adds a copy-and-truncate path for an unlikely case. | reject |
| Y | EC11 | A second `-Stop`/`-Restart` replaces an earlier unclaimed request. | low | The last request wins, and each client reports truthfully (stopped, or stopped instead of restarting). No harm. | reject |
| Z | EC14 | If reading the task back fails after a successful `Register-ScheduledTask`, the script exits 1 with a raw error. | low | Verified (`install_logon_task.ps1:167`). Direct fix: a `try` around the read-back that prints "registered; reading it back failed: …" to stderr. | patch |
| AA | EC15 | With UAC off, every token is elevated, so the install is always refused. | low | UAC is on for this PC (the build sessions ran non-elevated). A different refusal for UAC-off accounts is new behaviour. | reject |

## Design Notes

```text
start: job = new kill-on-close job; CreateProcess(SUSPENDED|NEW_PROCESS_GROUP, env+overrides); assign; resume
poll (WaitForSingleObject(child, PollSeconds)): request? -> CTRL_BREAK(group), wait job empty <= StopGrace, else TerminateJobObject
  beat changed -> quiet=0, firstBeat=now ; else quiet += Poll ; quiet >= (firstBeat ? Stale : Grace) -> stale, TerminateJobObject
child exited: job not empty -> kill(orphans); own 3 -> stop(3); own 2 x5 -> stop(2); up since first beat >= Reset -> backoff=start
```

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q -n 6 tests/app/test_run_supervisor.py tests/app/test_install_logon_task.py tests/app/test_spike_supervisor.py tests/unit/test_serve.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: 29 kept.
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_logon_task.ps1` -- expected: the dry run prints and registers nothing.
- `Get-ScheduledTask | ? TaskName -like 'Nextron*'` -- expected: nothing, before and after.
