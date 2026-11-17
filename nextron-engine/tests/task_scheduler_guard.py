"""Keeps every test away from Task Scheduler (Stories 1.20 and 1.21).

This is the only file under ``tests/`` that may name a command that changes Task Scheduler's
state (``tests/contracts/test_task_scheduler_contracts.py`` checks it). A test that runs a script
calling ScheduledTasks commands puts ``shadow_lines(marker, stand_ins)`` at the top of its
PowerShell script:

1. ``Import-Module`` loads PowerShell's own modules and ``ScheduledTasks`` first: importing
   ScheduledTasks after the shadows would replace them, which is how a scratch harness once
   registered a real task.
2. Each command in ``STATEFUL_TASK_COMMANDS`` becomes a global function: a tripwire that records
   its name in ``marker`` and throws, or a test's stand-in body.
3. Each is checked to be a function from no module (else the script throws before anything runs).
4. Module autoloading is turned off, so nothing can load another copy of these commands.

The ``New-ScheduledTask*`` builders stay real: they make objects in memory and change nothing.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

REGISTER: Final = "Register-ScheduledTask"
UNREGISTER: Final = "Unregister-ScheduledTask"
START: Final = "Start-ScheduledTask"
STOP: Final = "Stop-ScheduledTask"
SET: Final = "Set-ScheduledTask"
ENABLE: Final = "Enable-ScheduledTask"
DISABLE: Final = "Disable-ScheduledTask"
GET: Final = "Get-ScheduledTask"
GET_INFO: Final = "Get-ScheduledTaskInfo"
EXPORT: Final = "Export-ScheduledTask"

# The ScheduledTasks commands that change Task Scheduler's state, or read it.
STATEFUL_TASK_COMMANDS: Final = (
    REGISTER,
    UNREGISTER,
    START,
    STOP,
    SET,
    ENABLE,
    DISABLE,
    GET,
    GET_INFO,
    EXPORT,
)

# PowerShell's own modules the scripts use, loaded before autoloading is turned off.
PRELOADED_MODULES: Final = (
    "Microsoft.PowerShell.Management",
    "Microsoft.PowerShell.Utility",
    "CimCmdlets",
    "ScheduledTasks",
)


def ps_string(value: object) -> str:
    """A PowerShell single-quoted string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def ps_array(values: Sequence[object]) -> str:
    """A PowerShell array of single-quoted string literals."""
    return "@(" + ", ".join(ps_string(v) for v in values) + ")"


def tripwire(name: str, marker: Path) -> str:
    """A body that records ``name`` in ``marker`` and throws."""
    return (
        f"Add-Content -LiteralPath {ps_string(marker)} -Value {ps_string(name)}; "
        f"throw {ps_string('tripwire: ' + name)}"
    )


def shadow_lines(marker: Path, stand_ins: Mapping[str, str] | None = None) -> list[str]:
    """PowerShell lines that shadow every ``STATEFUL_TASK_COMMANDS`` entry, as the module
    docstring says. ``stand_ins`` gives some of them another body (a ``param`` block included)
    instead of the tripwire; a name that isn't in the list is refused."""
    stand_ins = dict(stand_ins or {})
    unknown = sorted(set(stand_ins) - set(STATEFUL_TASK_COMMANDS))
    if unknown:
        raise ValueError(f"not a stateful ScheduledTasks command: {unknown}")
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        f"Import-Module {', '.join(PRELOADED_MODULES)}",
    ]
    for name in STATEFUL_TASK_COMMANDS:
        body = stand_ins.get(name, tripwire(name, marker))
        lines.append(f"function global:{name} {{ {body} }}")
    lines += [
        f"foreach ($n in {ps_array(STATEFUL_TASK_COMMANDS)}) {{",
        "  $c = @(Get-Command -Name $n -ErrorAction Stop)",
        "  if ($c.Count -ne 1 -or $c[0].Module -or \"$($c[0].CommandType)\" -ne 'Function') {",
        '    throw "$n is not shadowed"',
        "  }",
        "}",
        "$PSModuleAutoloadingPreference = 'None'",
    ]
    return lines
