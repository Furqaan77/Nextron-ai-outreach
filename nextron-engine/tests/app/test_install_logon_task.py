"""``scripts/install_logon_task.ps1`` (Story 1.21): the dry run, -Register, -Uninstall and the
refusals.

Nothing here reaches Task Scheduler. Every run shadows each command that reads or changes its
state through ``tests/task_scheduler_guard.py``: a tripwire that records its name and throws, or
a recording stand-in a test gives. The ``NEXTRON_ENV=test`` refusal runs with stand-ins inside a
dynamic module named ``ScheduledTasks`` instead, so even a broken guard would call only fakes.
These tests run as ``NEXTRON_ENV=test`` (``tests/conftest.py``), so every run also exercises the
script's own check that no real module command is in reach.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.task_scheduler_guard import (
    GET,
    GET_INFO,
    PRELOADED_MODULES,
    REGISTER,
    STATEFUL_TASK_COMMANDS,
    STOP,
    UNREGISTER,
    ps_string,
    shadow_lines,
)

ENGINE_ROOT = Path(__file__).resolve().parents[2]
INSTALL = ENGINE_ROOT / "scripts" / "install_logon_task.ps1"
RUN_PS1 = ENGINE_ROOT / "scripts" / "run.ps1"
POWERSHELL = shutil.which("powershell.exe")
TASK = "Nextron Engine (prod)"
ROOT = "\\"  # the root task folder, the only one the script touches
RUN_TIMEOUT_S = 90

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None, reason="needs Windows PowerShell 5.1"
)


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str


def run_powershell(script: str, tmp_path: Path, env: dict[str, str] | None = None) -> Run:
    """Runs ``script`` in a hidden console of its own; kills its tree if it overruns."""
    assert POWERSHELL is not None
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    out, err = tmp_path / "powershell.out", tmp_path / "powershell.err"
    startup = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    startup.wShowWindow = 0  # SW_HIDE
    with out.open("wb") as stdout, err.open("wb") as stderr:
        process = subprocess.Popen(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=env if env is not None else dict(os.environ),
            cwd=tmp_path,
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
            startupinfo=startup,
        )
        try:
            code = process.wait(timeout=RUN_TIMEOUT_S)
        finally:
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
                process.wait(timeout=30)
    return Run(
        code=code,
        stdout=out.read_text(encoding="utf-8", errors="replace"),
        stderr=err.read_text(encoding="utf-8", errors="replace"),
    )


def install(
    tmp_path: Path,
    *arguments: str,
    stand_ins: dict[str, str] | None = None,
    state: str = "Ready",
    env: dict[str, str] | None = None,
) -> tuple[Run, Path]:
    """Runs the install script under the guard's shadows, with ``$global:State`` (the stand-in
    task's state) set; returns the run and the tripwire marker file (which must not exist
    afterwards)."""
    marker = tmp_path / "task-scheduler-called.txt"
    lines = shadow_lines(marker, stand_ins)
    lines += [
        f"$global:State = {ps_string(state)}",
        "$global:Removed = $false",
        f"& {ps_string(INSTALL)} {' '.join(arguments)}",
        "exit $LASTEXITCODE",
    ]
    return run_powershell("\n".join(lines), tmp_path, env), marker


def recorder(recording: Path, body: str = "") -> str:
    """A stand-in that records its own name, ``TaskName`` and ``TaskPath`` as a JSON line, then
    runs ``body``."""
    return (
        "[CmdletBinding(SupportsShouldProcess = $true)] "
        "param([string]$TaskName, [string]$TaskPath, $InputObject, [switch]$Force) "
        "$r = [ordered]@{ command = $MyInvocation.MyCommand.Name; task = $TaskName; "
        "task_path = $TaskPath }; "
        f"Add-Content -LiteralPath {ps_string(recording)} -Value (ConvertTo-Json $r -Compress); "
        f"{body}"
    )


def recorded(recording: Path) -> list[dict[str, object]]:
    if not recording.exists():
        return []
    return [json.loads(line) for line in recording.read_text(encoding="utf-8").splitlines()]


# A registered task as Get-ScheduledTask and Get-ScheduledTaskInfo describe it.
TASK_OBJECT = (
    "[pscustomobject]@{ TaskName = 'Nextron Engine (prod)'; TaskPath = '\\'; "
    "State = $global:State; Description = 'stand-in'; "
    "Principal = [pscustomobject]@{ UserId = 'me'; LogonType = 'Interactive'; "
    "RunLevel = 'Limited' }; "
    "Settings = [pscustomobject]@{ ExecutionTimeLimit = 'PT0S'; MultipleInstances = 'IgnoreNew'; "
    "Priority = 7 }; Actions = @([pscustomobject]@{ Execute = 'powershell.exe'; Arguments = 'x'; "
    "WorkingDirectory = 'y' }) }"
)
INFO_OBJECT = (
    "[pscustomobject]@{ LastRunTime = $null; LastTaskResult = 267011; NextRunTime = $null }"
)


# Row: Install dry run
def test_the_dry_run_prints_the_ad20_task_and_calls_nothing(tmp_path: Path) -> None:
    run, marker = install(tmp_path)

    assert run.code == 0, run.stderr
    assert run.stderr == ""
    assert not marker.exists()
    expected = [
        "Nextron logon task: dry run. Nothing is registered; -Register registers it.",
        f"Task name   : {TASK}",
        "Trigger     : AtLogOn (MSFT_TaskLogonTrigger), user ",
        "Principal   : UserId ",
        "LogonType Interactive, RunLevel Limited",
        "ExecutionTimeLimit PT0S, DisallowStartIfOnBatteries False, StopIfGoingOnBatteries "
        "False, StartWhenAvailable True, MultipleInstances IgnoreNew",
        "WindowsPowerShell\\v1.0\\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy "
        f'Bypass -WindowStyle Hidden -File "{RUN_PS1}" -Env prod',
        f"Working dir : {ENGINE_ROOT}",
    ]
    for text in expected:
        assert text in run.stdout
    found = shutil.which("uv.exe")
    [uv_line] = [line for line in run.stdout.splitlines() if line.startswith("uv.exe ")]
    if found is None:
        assert uv_line == "uv.exe      : not found on PATH; run.ps1 cannot start the engine"
    else:
        assert Path(uv_line.removeprefix("uv.exe      : ")).samefile(found)


def test_the_dry_run_says_when_uv_is_not_on_path(tmp_path: Path) -> None:
    windows = Path(os.environ["SYSTEMROOT"])
    folders = [windows / "System32", windows, windows / "System32/WindowsPowerShell/v1.0"]
    path = os.pathsep.join(str(folder) for folder in folders)
    if shutil.which("uv.exe", path=path):
        pytest.skip("uv.exe is reachable without the user's PATH")
    run, marker = install(tmp_path, env={**os.environ, "PATH": path})

    assert run.code == 0, run.stderr  # a warning, not a refusal
    assert not marker.exists()
    expected = "uv.exe      : not found on PATH; run.ps1 cannot start the engine"
    assert expected in run.stdout.splitlines()


def test_register_registers_the_ad20_task_replacing_one_of_that_name(tmp_path: Path) -> None:
    recording = tmp_path / "recording.jsonl"
    details = tmp_path / "task.json"
    capture = (
        "$t = $InputObject; $a = @($t.Actions)[0]; $g = @($t.Triggers)[0]; "
        "$d = [ordered]@{ force = $Force.IsPresent; execute = $a.Execute; "
        "arguments = $a.Arguments; working_dir = $a.WorkingDirectory; "
        "trigger = $g.CimClass.CimClassName; "
        "trigger_user = $g.UserId; user = $t.Principal.UserId; "
        "logon_type = \"$($t.Principal.LogonType)\"; run_level = \"$($t.Principal.RunLevel)\"; "
        "limit = $t.Settings.ExecutionTimeLimit; "
        "disallow_on_batteries = $t.Settings.DisallowStartIfOnBatteries; "
        "stop_on_batteries = $t.Settings.StopIfGoingOnBatteries; "
        "when_available = $t.Settings.StartWhenAvailable; "
        "instances = \"$($t.Settings.MultipleInstances)\"; description = $t.Description }; "
        f"Set-Content -LiteralPath {ps_string(details)} -Value (ConvertTo-Json $d -Compress)"
    )
    run, marker = install(
        tmp_path,
        "-Register",
        stand_ins={
            REGISTER: recorder(recording, capture),
            GET: recorder(recording, TASK_OBJECT),
            GET_INFO: recorder(recording, INFO_OBJECT),
        },
    )

    assert run.code == 0, run.stderr
    assert not marker.exists()  # no other Task Scheduler command was called
    assert [(r["command"], r["task"], r["task_path"]) for r in recorded(recording)] == [
        (REGISTER, TASK, ROOT), (GET, TASK, ROOT), (GET_INFO, TASK, ROOT),
    ]
    assert "uv.exe      : " in run.stdout
    task = json.loads(details.read_text(encoding="utf-8-sig"))
    assert task["force"] is True  # replaces a task of that name
    assert task["execute"].lower().endswith("\\system32\\windowspowershell\\v1.0\\powershell.exe")
    assert task["arguments"] == (
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden "
        f'-File "{RUN_PS1}" -Env prod'
    )
    assert task["working_dir"] == str(ENGINE_ROOT)
    assert task["trigger"] == "MSFT_TaskLogonTrigger"
    assert task["trigger_user"] == task["user"]
    assert (task["logon_type"], task["run_level"]) == ("Interactive", "Limited")
    assert (task["limit"], task["instances"]) == ("PT0S", "IgnoreNew")
    assert (task["disallow_on_batteries"], task["stop_on_batteries"]) == (False, False)
    assert task["when_available"] is True
    assert "install_logon_task.ps1 -Uninstall" in task["description"]
    assert f"Registered '{TASK}' without elevation" in run.stdout
    assert "--- Get-ScheduledTaskInfo" in run.stdout
    assert "267011" in run.stdout


def test_a_failed_read_back_after_registering_keeps_exit_0(tmp_path: Path) -> None:
    recording = tmp_path / "recording.jsonl"
    run, marker = install(
        tmp_path,
        "-Register",
        stand_ins={REGISTER: recorder(recording), GET: recorder(recording, "throw 'RPC failed.'")},
    )

    assert run.code == 0, run.stderr
    assert not marker.exists()
    assert [r["command"] for r in recorded(recording)] == [REGISTER, GET]
    [line] = run.stderr.splitlines()
    assert line.startswith("install_logon_task: registered; reading it back failed: ")
    assert f"Registered '{TASK}' without elevation" in run.stdout


def test_a_failed_registration_exits_1_with_one_line(tmp_path: Path) -> None:
    recording = tmp_path / "recording.jsonl"
    run, marker = install(
        tmp_path,
        "-Register",
        stand_ins={REGISTER: recorder(recording, "throw 'Access is denied.'")},
    )

    assert run.code == 1
    assert not marker.exists()
    [line] = run.stderr.splitlines()
    assert line.startswith("install_logon_task: registration failed: ")
    assert "Access is denied." in line


@pytest.mark.parametrize("state", ["Running", "Ready"])
def test_uninstall_stops_a_running_task_then_removes_it(tmp_path: Path, state: str) -> None:
    recording = tmp_path / "recording.jsonl"
    run, marker = install(
        tmp_path,
        "-Uninstall",
        stand_ins={
            GET: recorder(recording, "if (-not $global:Removed) { " + TASK_OBJECT + " }"),
            STOP: recorder(recording),
            UNREGISTER: recorder(recording, "$global:Removed = $true"),
        },
        state=state,
    )

    assert run.code == 0, run.stderr
    assert not marker.exists()
    assert {r["task_path"] for r in recorded(recording)} == {ROOT}
    commands = [r["command"] for r in recorded(recording)]
    if state == "Running":
        assert commands == [GET, STOP, UNREGISTER, GET]
        assert f"Stopped '{TASK}'" in run.stdout
    else:
        assert commands == [GET, UNREGISTER, GET]  # nothing to stop
    assert f"Removed '{TASK}'. Checked: no task of that name remains." in run.stdout


@pytest.mark.parametrize(
    ("state", "stop", "unregister", "commands", "message"),
    [
        ("Ready", "", "throw 'Access is denied.'", [GET, UNREGISTER],
         f"install_logon_task: removing '{TASK}' failed: Access is denied."),
        ("Ready", "", "", [GET, UNREGISTER, GET],
         f"install_logon_task: '{TASK}' is still registered"),
        ("Running", "throw 'The task is busy.'", "$global:Removed = $true", [GET, STOP],
         f"install_logon_task: stopping '{TASK}' failed, so nothing was removed: "
         "The task is busy."),
    ],
    ids=["unregister-throws", "unregister-leaves-it", "stop-throws"],
)
def test_a_failed_uninstall_exits_1_with_one_line_and_removes_nothing_more(
    tmp_path: Path, state: str, stop: str, unregister: str, commands: list[str], message: str
) -> None:
    recording = tmp_path / "recording.jsonl"
    run, marker = install(
        tmp_path,
        "-Uninstall",
        stand_ins={
            GET: recorder(recording, "if (-not $global:Removed) { " + TASK_OBJECT + " }"),
            STOP: recorder(recording, stop),
            UNREGISTER: recorder(recording, unregister),
        },
        state=state,
    )

    assert run.code == 1
    assert not marker.exists()
    assert [r["command"] for r in recorded(recording)] == commands  # stop fails: no unregister
    assert run.stderr.splitlines() == [message]
    assert "Removed" not in run.stdout


def test_uninstall_with_no_task_has_nothing_to_remove(tmp_path: Path) -> None:
    recording = tmp_path / "recording.jsonl"
    run, marker = install(tmp_path, "-Uninstall", stand_ins={GET: recorder(recording)})

    assert run.code == 0, run.stderr
    assert not marker.exists()  # neither stopped nor unregistered
    assert [r["command"] for r in recorded(recording)] == [GET]
    assert run.stdout.strip() == f"Nothing to remove: no task named '{TASK}' is registered."


# Row: Install refusals
def test_register_with_uninstall_is_refused_before_anything_runs(tmp_path: Path) -> None:
    run, marker = install(tmp_path, "-Register", "-Uninstall")

    assert run.code == 2
    assert not marker.exists()
    assert run.stdout == ""
    [line] = run.stderr.splitlines()
    assert line == "install_logon_task: refused: use -Register or -Uninstall, not both"


@pytest.mark.parametrize("mode", ["", "-Register", "-Uninstall"], ids=["dry-run", "register",
                                                                      "uninstall"])
def test_under_nextron_env_test_the_module_commands_are_refused(tmp_path: Path, mode: str) -> None:
    # Fakes inside a module named ScheduledTasks: the script sees module commands and refuses,
    # and even if it didn't, it could reach only these fakes.
    assert os.environ["NEXTRON_ENV"] == "test"
    recording = tmp_path / "recording.jsonl"
    fakes = " ".join(
        f"function {name} {{ Add-Content -LiteralPath {ps_string(recording)} "
        f"-Value {ps_string(name)} }};"
        for name in STATEFUL_TASK_COMMANDS
    )
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            f"Import-Module {', '.join(m for m in PRELOADED_MODULES if m != 'ScheduledTasks')}",
            "$PSModuleAutoloadingPreference = 'None'",
            f"New-Module -Name ScheduledTasks -ScriptBlock {{ {fakes} Export-ModuleMember "
            "-Function * } | Import-Module",
            f"if ((Get-Command {GET}).ModuleName -ne 'ScheduledTasks') {{",
            "  throw 'no fake module'",
            "}",
            f"& {ps_string(INSTALL)} {mode}",
            "exit $LASTEXITCODE",
        ]
    )
    run = run_powershell(script, tmp_path)

    assert run.code == 2
    assert not recording.exists()
    assert run.stdout == ""
    [line] = run.stderr.splitlines()
    assert line.startswith("install_logon_task: refused: NEXTRON_ENV=test, so the real ")


def test_an_elevated_powershell_is_refused_first(tmp_path: Path) -> None:
    # This PowerShell can't be elevated, so Get-Refusal runs on its own, taken from the script.
    marker = tmp_path / "task-scheduler-called.txt"
    lines = shadow_lines(marker)
    lines += [
        f"$path = {ps_string(INSTALL)}",
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, "
        "[ref]$null)",
        "$found = $ast.FindAll({ param($n) $n -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$n.Name -eq 'Get-Refusal' }, $true)",
        "Invoke-Expression @($found)[0].Extent.Text",
        "[Console]::Out.WriteLine((Get-Refusal -Elevated $true -Register $true -Uninstall $true "
        "-NextronEnv 'test'))",
        "[Console]::Out.WriteLine((Get-Refusal -Elevated $true -Register $false -Uninstall $false "
        "-NextronEnv 'prod'))",
        "[Console]::Out.WriteLine('none=' + ($null -eq (Get-Refusal -Elevated $false "
        "-Register $true -Uninstall $false -NextronEnv 'prod')))",
    ]
    run = run_powershell("\n".join(lines), tmp_path)

    assert run.code == 0, run.stderr
    assert not marker.exists()
    elevated = (
        "this PowerShell is elevated; run it from a normal one, so the task runs as you, not as "
        "an administrator"
    )
    assert run.stdout.splitlines() == [elevated, elevated, "none=True"]
