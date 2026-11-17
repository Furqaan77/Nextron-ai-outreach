"""The OQ-A9 spike (Story 1.20): ``scripts/spike/supervisor.ps1`` driven with the stdlib fake
``scripts/spike/fake_serve.py``, and ``scripts/spike/oq_a9.ps1``'s dry run and refusals.

One test per supervisor row of the spec's matrix. Nothing here calls a Task Scheduler cmdlet or
starts ``uv``: the serve command is the fake run by this test's Python, and ``oq_a9.ps1`` runs
with every state-changing ScheduledTasks command replaced by a tripwire. The assertions read the
JSON-line logs, not the clock. Each test kills, in a ``finally``, the PowerShell it started with
its whole tree and every Python pid the logs name.
"""

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.task_scheduler_guard import GET, shadow_lines

ENGINE_ROOT = Path(__file__).resolve().parents[2]
SPIKE = ENGINE_ROOT / "scripts" / "spike"
SUPERVISOR = SPIKE / "supervisor.ps1"
OQ_A9 = SPIKE / "oq_a9.ps1"
FAKE = SPIKE / "fake_serve.py"
POWERSHELL = shutil.which("powershell.exe")
RUN_TIMEOUT_S = 60
GONE_TIMEOUT_S = 10

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None, reason="needs Windows PowerShell 5.1"
)

# The oq_a9 tests shadow every ScheduledTasks command that changes or reads Task Scheduler's
# state through tests/task_scheduler_guard.py (Story 1.21 moved the list and the shadows there).


# --- Processes ------------------------------------------------------------------------------------

SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_TIMEOUT = 0x102


def _kernel32():  # type: ignore[no-untyped-def]
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    return kernel32


def gone(pid: int, timeout_s: float = GONE_TIMEOUT_S) -> bool:
    """Whether process ``pid`` has ended, waiting up to ``timeout_s`` for it."""
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout_s * 1000)) != WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def kill_python(pid: int) -> None:
    """Ends ``pid`` if it is still a running ``python.exe`` (a pid can be reused)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    access = PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return
    try:
        if kernel32.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT:
            return
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return
        if Path(buffer.value).name.lower() in {"python.exe", "pythonw.exe"}:
            kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def kill_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


# --- Running PowerShell ---------------------------------------------------------------------------


def ps_string(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ps_array(values: Sequence[object]) -> str:
    return "@(" + ", ".join(ps_string(v) for v in values) + ")"


def read_lines(path: Path) -> list[dict[str, object]]:
    """The complete JSON lines of a log; an unterminated last line is still being written."""
    if not path.is_file():
        return []
    complete = path.read_text(encoding="utf-8").split("\n")[:-1]
    return [json.loads(line) for line in complete if line]


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str
    events: list[dict[str, object]] = field(default_factory=list)
    fake_events: list[dict[str, object]] = field(default_factory=list)

    def names(self) -> list[object]:
        return [event["event"] for event in self.events]

    def of(self, name: str) -> list[dict[str, object]]:
        return [event for event in self.events if event["event"] == name]

    def tree_pids(self) -> list[int]:
        """Every pid of every child tree the logs name: the supervisor's child (the venv
        launcher), the fake's interpreter and its parent, and the grandchild's pids."""
        pids = [int(event["pid"]) for event in self.of("start")]  # type: ignore[call-overload]
        for event in self.fake_events:
            if event["event"] == "start":
                pids += [int(event["pid"]), int(event["ppid"])]  # type: ignore[call-overload]
                pids += [int(p) for p in event.get("grandchild_pids", [])]  # type: ignore[union-attr]
        return sorted(set(pids))


def run_powershell(script: str, tmp_path: Path, env: dict[str, str] | None = None) -> Run:
    """Runs ``script`` in a hidden console of its own; kills its tree when it is done."""
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
            cwd=tmp_path,  # no uv.exe here for CreateProcess's current-directory search
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
            startupinfo=startup,
        )
        try:
            code = process.wait(timeout=RUN_TIMEOUT_S)
        finally:
            if process.poll() is None:
                kill_tree(process.pid)
                process.wait(timeout=30)
    return Run(
        code=code,
        stdout=out.read_text(encoding="utf-8", errors="replace"),
        stderr=err.read_text(encoding="utf-8", errors="replace"),
    )


@dataclass
class Spike:
    tmp_path: Path
    data_dir: Path
    log: Path
    fake_log: Path
    runs: list[Run] = field(default_factory=list)

    def fake(self, *flags: str) -> list[str]:
        """The serve command: the fake on this test's Python."""
        return [
            sys.executable, str(FAKE),
            "--heartbeat", str(self.data_dir / "heartbeat"),
            "--log", str(self.fake_log),
            *flags,
        ]

    def supervise(
        self,
        command: Sequence[str] | None,
        env: dict[str, str] | None = None,
        **params: object,
    ) -> Run:
        """Runs supervisor.ps1 to its end with ``-ServeCommand command`` (none: its default)
        and ``params``, then reports this PowerShell's own NEXTRON_ENV, as the supervisor left
        it."""
        arguments = [
            f"-DataDir {ps_string(self.data_dir)}",
            f"-LogPath {ps_string(self.log)}",
            *([f"-ServeCommand {ps_array(command)}"] if command is not None else []),
            *(f"-{name} {ps_string(value)}" for name, value in params.items()),
        ]
        script = "\n".join(
            [
                "$ProgressPreference = 'SilentlyContinue'",
                f"& {ps_string(SUPERVISOR)} {' '.join(arguments)}",
                "$code = $LASTEXITCODE",
                "$own = [Environment]::GetEnvironmentVariable('NEXTRON_ENV')",
                "[Console]::Out.WriteLine('own NEXTRON_ENV=' + $own)",
                "exit $code",
            ]
        )
        run = run_powershell(script, self.tmp_path, env)
        run.events = read_lines(self.log)
        run.fake_events = read_lines(self.fake_log)
        self.runs.append(run)
        return run


@pytest.fixture
def spike(tmp_path: Path) -> Iterator[Spike]:
    data_dir = tmp_path / "spike data"  # a space, so every path must be quoted
    spike = Spike(tmp_path, data_dir, data_dir / "logs" / "supervisor.log", data_dir / "fake.log")
    try:
        yield spike
    finally:
        pids = {pid for run in spike.runs for pid in run.tree_pids()}
        for pid in pids | set(read_lines_pids(spike.fake_log)):
            kill_python(pid)


def read_lines_pids(path: Path) -> list[int]:
    """Every pid the fake's log names, read again (the log may have grown since the run)."""
    pids: list[int] = []
    for event in read_lines(path):
        if event["event"] == "start":
            pids += [int(event["pid"]), int(event["ppid"])]  # type: ignore[call-overload]
            pids += [int(p) for p in event.get("grandchild_pids", [])]  # type: ignore[union-attr]
    return pids


FAST = {"BackoffStartSeconds": 0.1, "BackoffMaxSeconds": 0.4, "PollSeconds": 0.1}


# --- The supervisor: one test per matrix row ------------------------------------------------------


def test_a_crash_restarts_after_a_doubling_backoff_capped_at_the_maximum(spike: Spike) -> None:
    run = spike.supervise(spike.fake("--exit-after", "0", "--exit-code", "1"), MaxRestarts=4, **FAST)

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        *["start", "exit", "backoff"] * 4,
        "start", "exit", "max_restarts",
    ]
    assert [e["wait_s"] for e in run.of("backoff")] == pytest.approx([0.1, 0.2, 0.4, 0.4])
    assert {(e["code"], e["killed"]) for e in run.of("exit")} == {(1, False)}
    assert len(run.fake_events) == 10  # five starts, five exits, all logged by the fake


def test_a_long_run_resets_the_backoff_to_its_start(spike: Spike) -> None:
    run = spike.supervise(
        spike.fake("--exit-after", "0.5", "--exit-code", "0"),
        MaxRestarts=3,
        ResetAfterSeconds=0.3,
        **FAST,
    )

    assert run.code == 0, run.stderr
    assert all(float(e["uptime_s"]) >= 0.3 for e in run.of("exit"))  # type: ignore[arg-type]
    assert [e["wait_s"] for e in run.of("backoff")] == pytest.approx([0.1, 0.1, 0.1])
    assert {e["code"] for e in run.of("exit")} == {0}  # a clean stop restarts too


def test_the_childs_own_exit_3_ends_the_loop_and_the_supervisor_exits_3(spike: Spike) -> None:
    run = spike.supervise(spike.fake("--exit-after", "0", "--exit-code", "3"), **FAST)

    assert run.code == 3, run.stderr
    assert run.names() == ["supervisor_start", "start", "exit", "stop"]
    [exit_event] = run.of("exit")
    assert (exit_event["code"], exit_event["killed"]) == (3, False)


def test_a_stale_heartbeat_kills_the_whole_tree_and_restarts_it(spike: Spike) -> None:
    run = spike.supervise(
        spike.fake("--interval", "0.05", "--stall", "--grandchild"),
        MaxRestarts=1,
        StaleAfterSeconds=0.5,
        StartGraceSeconds=30,
        **FAST,
    )

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        "start", "stale", "kill", "exit", "backoff",
        "start", "stale", "kill", "exit", "max_restarts",
    ]
    for stale in run.of("stale"):
        assert stale["before_first_beat"] is False
        assert float(stale["quiet_s"]) >= 0.5  # type: ignore[arg-type]
    for kill in run.of("kill"):
        assert (kill["method"], kill["ok"], kill["reason"]) == ("taskkill", True, "stale")
    assert {e["killed"] for e in run.of("exit")} == {True}
    starts = [e for e in run.fake_events if e["event"] == "start"]
    assert len(starts) == 2
    assert all(e.get("grandchild_pids") for e in starts)
    pids = run.tree_pids()
    assert len(pids) >= 4  # per child at least: the fake and its grandchild
    assert [pid for pid in pids if not gone(pid)] == []


# Stands in for uv.exe: a plain interpreter (no venv launcher, so no job object) that runs the
# rest of the command as its child and exits with its code.
PASS_THROUGH = "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))"


def test_when_taskkill_fails_the_supervisor_kills_every_descendant_then_the_child(
    spike: Spike,
) -> None:
    base = getattr(sys, "_base_executable", sys.executable)
    fake = spike.fake("--interval", "0.05", "--stall", "--grandchild")
    fake[0] = base
    run = spike.supervise(
        [base, "-c", PASS_THROUGH, *fake],
        MaxRestarts=0,
        StaleAfterSeconds=0.5,
        TaskkillPath=spike.tmp_path / "missing" / "taskkill.exe",
        **FAST,
    )

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start", "start", "stale", "kill", "kill", "exit", "max_restarts",
    ]
    failed, fallback = run.of("kill")
    assert (failed["method"], failed["ok"]) == ("taskkill", False)
    assert failed["error"]
    assert (fallback["method"], fallback["ok"]) == ("process_kill", True)
    [start] = run.of("start")
    [fake_start] = [e for e in run.fake_events if e["event"] == "start"]
    tree = {
        int(start["pid"]),  # type: ignore[call-overload]
        int(fake_start["pid"]),  # type: ignore[call-overload]
        int(fake_start["ppid"]),  # type: ignore[call-overload]
        *(int(p) for p in fake_start["grandchild_pids"]),  # type: ignore[attr-defined]
    }
    assert len(tree) == 3  # pass-through, fake, grandchild
    assert tree <= set(fallback["pids"])  # type: ignore[arg-type]
    assert [pid for pid in tree if not gone(pid)] == []


def test_a_beat_slower_than_the_poll_never_counts_as_stale(spike: Spike) -> None:
    # Every other poll sees no change; only the reset on each beat keeps it under the limit.
    run = spike.supervise(
        spike.fake("--interval", "0.2", "--exit-after", "3", "--exit-code", "3"),
        StaleAfterSeconds=1.0,
        StartGraceSeconds=30,
        **FAST,
    )

    assert run.code == 3, run.stderr
    assert run.names() == ["supervisor_start", "start", "exit", "stop"]


def test_a_child_restarted_after_one_that_beat_gets_the_start_grace_again(spike: Spike) -> None:
    run = spike.supervise(
        spike.fake("--beat-delay", "1.5", "--interval", "0.05", "--exit-after", "2.0",
                   "--exit-code", "1"),
        MaxRestarts=1,
        StaleAfterSeconds=1.0,
        StartGraceSeconds=30,
        **FAST,
    )

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start", "start", "exit", "backoff", "start", "exit", "max_restarts",
    ]


@pytest.mark.parametrize("heartbeat", ["missing", "an hour old"])
def test_a_slow_start_is_not_killed_before_its_first_beat(spike: Spike, heartbeat: str) -> None:
    if heartbeat == "an hour old":
        spike.data_dir.mkdir()
        beat = spike.data_dir / "heartbeat"
        beat.touch()
        old = beat.stat().st_mtime - 3600
        os.utime(beat, (old, old))
    run = spike.supervise(
        # The first beat comes 2 s in, past the 1 s stale limit: only the start grace saves it.
        spike.fake("--beat-delay", "2.0", "--interval", "0.05", "--exit-after", "2.8",
                   "--exit-code", "3"),
        StaleAfterSeconds=1.0,
        StartGraceSeconds=30,
        **FAST,
    )

    assert run.code == 3, run.stderr
    assert run.names() == ["supervisor_start", "start", "exit", "stop"]


def test_a_start_with_no_beat_within_the_start_grace_is_killed_and_restarted(
    spike: Spike,
) -> None:
    run = spike.supervise(
        spike.fake("--beat-delay", "3600", "--grandchild"),
        MaxRestarts=1,
        StartGraceSeconds=0.8,
        StaleAfterSeconds=30,
        **FAST,
    )

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        "start", "stale", "kill", "exit", "backoff",
        "start", "stale", "kill", "exit", "max_restarts",
    ]
    for stale in run.of("stale"):
        assert stale["before_first_beat"] is True
        assert stale["limit_s"] == pytest.approx(0.8)
        assert stale["quiet_s"] == pytest.approx(stale["limit_s"])
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []


@pytest.mark.parametrize(
    ("own", "param", "child"),
    [(None, None, "test"), ("test", "dev", "dev")],
    ids=["default", "dev"],
)
def test_the_child_alone_gets_nextron_env(
    spike: Spike, own: str | None, param: str | None, child: str
) -> None:
    env = {k: v for k, v in os.environ.items() if k.upper() != "NEXTRON_ENV"}
    if own is not None:
        env["NEXTRON_ENV"] = own
    params: dict[str, object] = {"Env": param} if param is not None else {}
    run = spike.supervise(
        spike.fake("--exit-after", "0", "--exit-code", "3"), env=env, **params, **FAST
    )

    assert run.code == 3, run.stderr
    [start] = [e for e in run.fake_events if e["event"] == "start"]
    assert start["nextron_env"] == child
    assert run.of("supervisor_start")[0]["env"] == child
    assert f"own NEXTRON_ENV={own or ''}" in run.stdout.splitlines()


def test_a_missing_executable_logs_start_failed_and_retries_after_the_backoff(
    spike: Spike,
) -> None:
    run = spike.supervise([str(spike.tmp_path / "missing" / "serve.exe")], MaxRestarts=2, **FAST)

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        "start_failed", "backoff", "start_failed", "backoff", "start_failed",
        "max_restarts",
    ]
    assert [e["wait_s"] for e in run.of("backoff")] == pytest.approx([0.1, 0.2])
    assert all(e["error"] for e in run.of("start_failed"))


def test_max_restarts_ends_the_loop_with_exit_0(spike: Spike) -> None:
    run = spike.supervise(spike.fake("--exit-after", "0", "--exit-code", "1"), MaxRestarts=0, **FAST)

    assert run.code == 0, run.stderr
    assert run.names() == ["supervisor_start", "start", "exit", "max_restarts"]
    assert run.of("max_restarts")[0]["restarts"] == 0


def load_fake_serve():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("spike_fake_serve", FAKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_default_serve_command_runs_the_fake_through_uv_on_the_data_dir(spike: Spike) -> None:
    windows = Path(os.environ["SYSTEMROOT"])
    path = os.pathsep.join(
        str(folder)
        for folder in (windows / "System32", windows, windows / "System32/WindowsPowerShell/v1.0")
    )
    if shutil.which("uv.exe", path=path) or shutil.which("uv.exe", path=str(spike.tmp_path)):
        pytest.skip("uv.exe is reachable without PATH; this test must never start uv")
    run = spike.supervise(None, env={**os.environ, "PATH": path}, MaxRestarts=0, **FAST)

    assert run.code == 0, run.stderr
    assert run.names() == ["supervisor_start", "start_failed", "max_restarts"]  # no uv: no start
    command = run.of("supervisor_start")[0]["command"]
    assert isinstance(command, list)
    assert command[0] == "uv.exe"
    assert command[1:4] == ["run", "--frozen", "python"]
    assert Path(command[4]) == FAKE
    args = load_fake_serve().parse_args(command[5:])
    assert args.heartbeat == spike.data_dir / "heartbeat"
    assert args.control == spike.data_dir / "spike-control"
    assert args.log == spike.data_dir / "logs" / "fake_serve.log"
    assert args.interval == 30


def test_every_line_is_utf8_json_without_a_bom(spike: Spike) -> None:
    spike.supervise(spike.fake("--exit-after", "0", "--exit-code", "3"), **FAST)

    raw = spike.log.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == [
        "supervisor_start", "start", "exit", "stop",
    ]
    assert all(json.loads(line)["ts"].endswith("Z") for line in lines)


# --- The fake's one-shot commands -----------------------------------------------------------------


def send(control: Path, text: str) -> None:
    """Writes a command the way ``oq_a9.ps1`` does: a temporary file renamed into place."""
    temporary = control.with_name(control.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, control)


def wait_for_event(path: Path, name: str, count: int, process: subprocess.Popen[bytes]) -> None:
    """Blocks until the fake's log holds ``count`` events called ``name``."""
    import time

    deadline = time.monotonic() + RUN_TIMEOUT_S
    while sum(1 for e in read_lines(path) if e["event"] == name) < count:
        assert process.poll() is None or name == "exit", "the fake ended early"
        assert time.monotonic() < deadline, f"no {name} event"
        time.sleep(0.02)


def test_the_fake_obeys_each_control_command_once(tmp_path: Path) -> None:
    folder = tmp_path / "fake"
    control, log = folder / "spike-control", folder / "fake.log"
    process = subprocess.Popen(
        [sys.executable, str(FAKE), "--heartbeat", str(folder / "heartbeat"),
         "--interval", "0.05", "--control", str(control), "--log", str(log)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_event(log, "start", 1, process)
        send(control, "dance")
        wait_for_event(log, "command", 1, process)
        send(control, "stall")
        wait_for_event(log, "command", 2, process)
        send(control, "exit 4")
        assert process.wait(timeout=RUN_TIMEOUT_S) == 4
    finally:
        if process.poll() is None:
            kill_tree(process.pid)
            process.wait(timeout=30)
    events = read_lines(log)
    commands = [(e["text"], e["accepted"]) for e in events if e["event"] == "command"]
    assert commands == [("dance", False), ("stall", True), ("exit 4", True)]
    assert (events[-1]["event"], events[-1]["code"]) == ("exit", 4)
    assert sorted(p.name for p in folder.iterdir()) == ["fake.log", "heartbeat"]  # all consumed


# --- oq_a9.ps1 ------------------------------------------------------------------------------------


def run_oq_a9(
    tmp_path: Path, *arguments: str, overrides: dict[str, str] | None = None
) -> tuple[Run, Path]:
    """Runs oq_a9.ps1 under ``tests/task_scheduler_guard.py``'s shadows: every stateful
    ScheduledTasks command is a tripwire that records its name in a marker file and throws.
    ``overrides`` gives some of them another body instead (still a function, never the
    module's command)."""
    marker = tmp_path / "task-scheduler-called.txt"
    lines = shadow_lines(marker, overrides)
    lines += [
        f"& {ps_string(OQ_A9)} {' '.join(arguments)}",
        "exit $LASTEXITCODE",
    ]
    return run_powershell("\n".join(lines), tmp_path), marker


def test_the_dry_run_prints_the_ad20_task_and_registers_nothing(
    tmp_path: Path, local_app_data: Path
) -> None:
    run, marker = run_oq_a9(tmp_path)

    assert run.code == 0, run.stderr
    assert not marker.exists()
    assert run.stderr == ""
    data_dir = local_app_data / "Nextron" / "spike"
    expected = [
        "OQ-A9 spike: dry run. Nothing is registered.",
        "Task name   : Nextron Spike OQ-A9",
        "Principal   : UserId ",
        "LogonType Interactive, RunLevel Limited",
        "ExecutionTimeLimit PT0S, DisallowStartIfOnBatteries False, StopIfGoingOnBatteries "
        "False, StartWhenAvailable True, MultipleInstances IgnoreNew",
        "Trigger     : AtLogOn (MSFT_TaskLogonTrigger), user ",
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden "
        f'-File "{SUPERVISOR}" -Env test -DataDir "{data_dir}"',
        f"Working dir : {ENGINE_ROOT}",
    ]
    for text in expected:
        assert text in run.stdout
    assert not data_dir.exists()  # a dry run writes nothing


@pytest.mark.parametrize("action", ["DryRun", "Status"])
@pytest.mark.parametrize(
    "name",
    [
        "Nextron Engine (prod)",
        "NEXTRON ENGINE (PROD)",
        " nextron engine (prod) ",
        "Nextron*",
        "",
        "Nextron\\Spike",
        "Nextron/Spike",
        "Nextron?",
        "Nextron[1]",
    ],
    ids=["prod", "PROD", "padded", "star", "empty", "backslash", "slash", "question", "bracket"],
)
def test_the_production_task_name_is_refused_before_anything_runs(
    tmp_path: Path, action: str, name: str
) -> None:
    run, marker = run_oq_a9(tmp_path, f"-Action {action}", f"-TaskName {ps_string(name)}")

    assert run.code == 2
    assert not marker.exists()
    assert run.stdout == ""
    [line] = run.stderr.splitlines()
    assert line.startswith("oq_a9: refused: ")


# Kill, Stall and Exit3 act only on the data dir: they run here on a fake started by the test,
# with the paths the supervisor's default command gives it.


@pytest.fixture
def running_fake(tmp_path: Path) -> Iterator[tuple[Path, subprocess.Popen[bytes]]]:
    data_dir = tmp_path / "spike data"
    log = data_dir / "logs" / "fake_serve.log"
    process = subprocess.Popen(
        [sys.executable, str(FAKE), "--heartbeat", str(data_dir / "heartbeat"),
         "--control", str(data_dir / "spike-control"), "--log", str(log), "--interval", "0.05"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_event(log, "start", 1, process)
        yield data_dir, process
    finally:
        if process.poll() is None:
            kill_tree(process.pid)
            process.wait(timeout=30)
        for pid in read_lines_pids(log):
            kill_python(pid)


def test_stall_reaches_the_running_fake(
    tmp_path: Path, running_fake: tuple[Path, subprocess.Popen[bytes]]
) -> None:
    data_dir, process = running_fake
    run, marker = run_oq_a9(tmp_path, "-Action Stall", f"-DataDir {ps_string(data_dir)}")

    assert run.code == 0, run.stderr
    assert not marker.exists()
    log = data_dir / "logs" / "fake_serve.log"
    wait_for_event(log, "command", 1, process)
    [command] = [e for e in read_lines(log) if e["event"] == "command"]
    assert (command["text"], command["accepted"]) == ("stall", True)
    assert process.poll() is None  # stalled, not stopped


def test_exit3_makes_the_running_fake_exit_3(
    tmp_path: Path, running_fake: tuple[Path, subprocess.Popen[bytes]]
) -> None:
    data_dir, process = running_fake
    run, marker = run_oq_a9(tmp_path, "-Action Exit3", f"-DataDir {ps_string(data_dir)}")

    assert run.code == 0, run.stderr
    assert not marker.exists()
    assert process.wait(timeout=RUN_TIMEOUT_S) == 3
    assert not (data_dir / "spike-control").exists()


def test_kill_ends_the_running_fakes_python(
    tmp_path: Path, running_fake: tuple[Path, subprocess.Popen[bytes]]
) -> None:
    data_dir, process = running_fake
    [start] = [e for e in read_lines(data_dir / "logs" / "fake_serve.log") if e["event"] == "start"]
    run, marker = run_oq_a9(tmp_path, "-Action Kill", f"-DataDir {ps_string(data_dir)}")

    assert run.code == 0, run.stderr
    assert not marker.exists()
    assert f"Killed the fake's Python, pid {start['pid']}." in run.stdout
    assert gone(int(start["pid"]))  # type: ignore[call-overload]
    process.wait(timeout=RUN_TIMEOUT_S)


# Unregister under the tripwires: the task lookup answers from a stand-in function, so the real
# one is never called, and the commands that stop and remove a task stay tripwires.


def test_unregister_refuses_a_task_this_spike_did_not_register(tmp_path: Path) -> None:
    data_dir = tmp_path / "spike data"
    someone_elses = (
        "[pscustomobject]@{ TaskName = 'Nextron Spike OQ-A9'; TaskPath = '\\'; "
        "State = 'Running'; Description = 'Another task' }"
    )
    run, marker = run_oq_a9(
        tmp_path,
        "-Action Unregister",
        f"-DataDir {ps_string(data_dir)}",
        overrides={GET: someone_elses},
    )

    assert run.code == 2
    assert not marker.exists()  # neither stopped nor unregistered
    assert run.stdout == ""
    [line] = run.stderr.splitlines()
    assert line.startswith("oq_a9: refused: ")
    assert not data_dir.exists()  # nothing written either


def test_unregister_deletes_a_leftover_command(tmp_path: Path) -> None:
    data_dir = tmp_path / "spike data"
    data_dir.mkdir()
    (data_dir / "spike-control").write_text("exit 3", encoding="utf-8")
    run, marker = run_oq_a9(
        tmp_path,
        "-Action Unregister",
        f"-DataDir {ps_string(data_dir)}",
        overrides={GET: ""},  # no task registered
    )

    assert run.code == 0, run.stderr
    assert not marker.exists()
    assert not (data_dir / "spike-control").exists()
    assert "No task named 'Nextron Spike OQ-A9' is registered." in run.stdout
    assert "Deleted a leftover command file" in run.stdout
    assert "Checked: no task named 'Nextron Spike OQ-A9' remains." in run.stdout
