"""No test, engine module or script can reach Task Scheduler by accident (Story 1.21).

- Only three files may name a way to change Task Scheduler's state:
  ``tests/task_scheduler_guard.py`` (the shadows), ``scripts/install_logon_task.ps1`` (the real
  install) and
  ``scripts/spike/oq_a9.ps1`` (the spike). The scan covers ``tests/``, ``nextron/`` and
  ``scripts/`` (bytecode caches aside) and looks for:
  - the Register, Unregister, Start, Stop, Set, Enable and Disable task commands;
  - the PSScheduledJob module's Register, Unregister, Set, Enable and Disable job commands;
  - the scheduler's CIM class behind those commands (its methods change tasks too);
  - any module-qualified ScheduledTasks command (a qualified name skips the tests' shadows);
  - ``schtasks`` with a writing switch (create, delete, run, end or change);
  - the scheduler's COM object.

  A probe on planted files shows the scan finds each form and lets the read-only ones through.
- ``scripts/run.ps1`` names no ScheduledTasks or scheduled-job command, no ``schtasks`` and no
  COM object.
- ``scripts/run.ps1`` and ``scripts/install_logon_task.ps1`` are ASCII: Windows PowerShell 5.1
  reads a file without a BOM in the ANSI code page.
- The guard's shadows hold: after ``shadow_lines`` every stateful command is a function from no
  module, calling one trips the marker, and module autoloading is off.

The names are assembled from parts here, so this file never matches its own scan.
"""

import base64
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.contracts.support import ENGINE_ROOT
from tests.task_scheduler_guard import REGISTER, STATEFUL_TASK_COMMANDS, ps_array, shadow_lines

SCANNED = ("tests", "nextron", "scripts")
ALLOWED = [
    "scripts/install_logon_task.ps1",
    "scripts/spike/oq_a9.ps1",
    "tests/task_scheduler_guard.py",
]
SCRIPTS = (ENGINE_ROOT / "scripts" / "run.ps1", ENGINE_ROOT / "scripts" / "install_logon_task.ps1")
POWERSHELL = shutil.which("powershell.exe")

_TASK = "Scheduled" + "Task"
_JOB = "Scheduled" + "Job"
_MODULE = _TASK + "s"
_CIM = "PS_" + _TASK
_SCH = "sch" + "tasks"
_COM = "Schedule" + r"\." + "Service"
_VERBS = ("Register", "Unregister", "Start", "Stop", "Set", "Enable", "Disable")
_JOB_VERBS = ("Register", "Unregister", "Set", "Enable", "Disable")
STATE_CHANGING = re.compile(
    r"\b(?:" + "|".join(_VERBS) + r")-" + _TASK + r"\b"
    r"|\b(?:" + "|".join(_JOB_VERBS) + r")-" + _JOB + r"\b"
    r"|" + _CIM + r"\b"
    r"|\b" + _MODULE + r"\\[A-Za-z]+-[A-Za-z]"
    r"|\b" + _SCH + r"(?:\.exe)?\b[^\r\n]*?[/-](?:create|delete|run|end|change)\b"
    r"|\b" + _COM + r"\b",
    re.IGNORECASE,
)


def naming_files(root: Path, folders: tuple[str, ...] = SCANNED) -> list[str]:
    """Every file under ``root``'s ``folders`` (bytecode caches aside) that names a way to change
    Task Scheduler's state, as a sorted path relative to ``root``."""
    found = []
    for folder in folders:
        for path in sorted((root / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if STATE_CHANGING.search(path.read_bytes().decode("utf-8", errors="ignore")):
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


def test_only_the_guard_the_install_script_and_the_spike_name_a_state_changing_command() -> None:
    assert naming_files(ENGINE_ROOT) == ALLOWED


PLANTED = [
    *(f"{verb}-{_TASK} -TaskName 'x'" for verb in _VERBS),
    f"{_VERBS[1].lower()}-{_TASK.lower()} x",  # any letter case
    *(f"{verb}-{_JOB} -Name x" for verb in _JOB_VERBS),
    f"Invoke-CimMethod -ClassName {_CIM} -MethodName RegisterByPrincipal",
    f"& {_MODULE}\\Get-{_TASK} -TaskName x",  # read-only, but past the shadows
    f"{_MODULE.lower()}\\new-{_TASK.lower()}trigger -AtLogOn",
    f"{_SCH} /Create /TN x /TR y",
    f"{_SCH.upper()}.EXE /delete /tn x",
    f'subprocess.run(["{_SCH}", "/Run", "/TN", "x"])',
    f"{_SCH} /End /TN x",
    f"{_SCH} -change -tn x",
    "New-Object -ComObject " + "Schedule" + ".Service",
]
READ_ONLY = [
    f"Get-{_TASK} -TaskName x",
    f"Get-{_TASK}Info -TaskName x",
    f"Export-{_TASK} -TaskName x",
    f"New-{_TASK}Trigger -AtLogOn",
    f"Get-{_JOB} -Name x",
    f"Import-Module {_MODULE}",
    f"$task.CimClass.CimClassName -eq 'MSFT_{_TASK}'",
    f"{_SCH} /Query /TN x",
    "the task commands that change state",
]


def test_the_scan_finds_each_planted_form_and_lets_reads_through(tmp_path: Path) -> None:
    folder = tmp_path / "tests"
    folder.mkdir()
    for number, text in enumerate(PLANTED):
        (folder / f"planted_{number:02}.{'ps1' if number % 2 else 'py'}").write_text(
            f"# line one\n    {text}\n", encoding="utf-8"
        )
    for number, text in enumerate(READ_ONLY):
        (folder / f"allowed_{number:02}.py").write_text(f"{text}\n", encoding="utf-8")
    cache = folder / "__pycache__"
    cache.mkdir()
    (cache / "skipped.pyc").write_text(PLANTED[0], encoding="utf-8")

    assert naming_files(tmp_path, ("tests",)) == sorted(
        f"tests/planted_{number:02}.{'ps1' if number % 2 else 'py'}"
        for number in range(len(PLANTED))
    )


def test_run_ps1_names_no_task_scheduler_command() -> None:
    text = SCRIPTS[0].read_text(encoding="ascii").lower()
    for name in (_TASK.lower(), _JOB.lower(), _SCH, "schedule" + ".service"):
        assert name not in text


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_scripts_are_ascii(script: Path) -> None:
    script.read_bytes().decode("ascii")  # raises on any other byte, a BOM included


def test_the_guard_refuses_a_stand_in_for_another_command(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a stateful ScheduledTasks command"):
        shadow_lines(tmp_path / "marker", {"Get-Process": ""})


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="needs PowerShell 5.1")
def test_the_guards_shadows_hold(tmp_path: Path) -> None:
    marker = tmp_path / "task-scheduler-called.txt"
    lines = shadow_lines(marker)
    lines += [
        f"foreach ($n in {ps_array(STATEFUL_TASK_COMMANDS)}) {{",
        "  $c = Get-Command -Name $n",
        "  [Console]::Out.WriteLine(\"$n|$($c.CommandType)|$($c.ModuleName)\")",
        "}",
        "[Console]::Out.WriteLine('autoload=' + $PSModuleAutoloadingPreference)",
        f"try {{ {REGISTER} -TaskName 'x' }} catch {{ "
        "[Console]::Out.WriteLine('tripped: ' + $_.Exception.Message) }",
    ]
    encoded = base64.b64encode("\n".join(lines).encode("utf-16-le")).decode("ascii")
    assert POWERSHELL is not None
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ),
        cwd=tmp_path,
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        *(f"{name}|Function|" for name in STATEFUL_TASK_COMMANDS),
        "autoload=None",
        f"tripped: tripwire: {REGISTER}",
    ]
    assert marker.read_text(encoding="utf-8").split() == [REGISTER]
