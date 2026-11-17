"""``scripts/run.ps1``, the engine's supervisor (Story 1.21): one test per row of the spec's matrix.

The serve command is the stdlib fake ``tests/fake_serve.py`` on this test's Python, except in the
real-engine test, which runs ``python -m nextron serve``; nothing here starts ``uv`` or touches
Task Scheduler. Every run uses a temporary data dir (with a space in its path), a temporary
``%LOCALAPPDATA%`` and its own hidden console. The assertions read the JSON-line logs and check
that pids are gone, not the clock. Each test kills, in a ``finally``, every PowerShell it started
with its whole tree and every Python pid the logs name.
"""

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.task_scheduler_guard import ps_array, ps_string

ENGINE_ROOT = Path(__file__).resolve().parents[2]
RUN_PS1 = ENGINE_ROOT / "scripts" / "run.ps1"
FAKE = ENGINE_ROOT / "tests" / "fake_serve.py"
POWERSHELL = shutil.which("powershell.exe")
RUN_TIMEOUT_S = 90
WAIT_TIMEOUT_S = 60
GONE_TIMEOUT_S = 15

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None, reason="needs Windows PowerShell 5.1"
)

FAST = {"BackoffStartSeconds": 0.1, "BackoffMaxSeconds": 0.4, "PollSeconds": 0.1}


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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until(
    condition: Callable[[], object], what: str, timeout_s: float = WAIT_TIMEOUT_S
) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


# --- Logs -----------------------------------------------------------------------------------------


def read_lines(path: Path) -> list[dict[str, object]]:
    """The complete JSON lines of a log; an unterminated last line is still being written."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [json.loads(line) for line in text.split("\n")[:-1] if line]


def pids_of(fake_events: list[dict[str, object]]) -> list[int]:
    """Every pid a fake's log names: its interpreter, the venv launcher above it and its
    grandchild's pids."""
    pids: list[int] = []
    for event in fake_events:
        if event["event"] in ("start", "start_v2"):
            pids += [int(event["pid"]), int(event["ppid"])]  # type: ignore[call-overload]
            pids += [int(p) for p in event.get("grandchild_pids", [])]  # type: ignore[union-attr]
    return pids


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
        """Every pid of every child tree the logs name."""
        pids = [int(event["pid"]) for event in self.of("start")]  # type: ignore[call-overload]
        return sorted(set(pids + pids_of(self.fake_events)))


# --- The harness ----------------------------------------------------------------------------------


@dataclass
class Supervision:
    """One test's supervisor: its data dir, its environment and every PowerShell it started."""

    tmp_path: Path
    data_dir: Path
    env: dict[str, str]
    processes: list[tuple[subprocess.Popen[bytes], Path, Path]] = field(default_factory=list)

    @property
    def log(self) -> Path:
        return self.data_dir / "logs" / "supervisor.log"

    @property
    def fake_log(self) -> Path:
        return self.data_dir / "fake.log"

    def events(self) -> list[dict[str, object]]:
        return read_lines(self.log)

    def fake_events(self) -> list[dict[str, object]]:
        return read_lines(self.fake_log)

    def count(self, name: str) -> int:
        return sum(1 for event in self.events() if event["event"] == name)

    def fake(self, *flags: str, script: Path = FAKE) -> list[str]:
        """The serve command: the fake on this test's Python."""
        return [
            sys.executable, str(script),
            "--heartbeat", str(self.data_dir / "heartbeat"),
            "--log", str(self.fake_log),
            *flags,
        ]

    def start(
        self, command: Sequence[str] | None, *switches: str, env: str = "test", **params: object
    ) -> subprocess.Popen[bytes]:
        """Starts run.ps1 in a hidden console of its own; it then reports its own NEXTRON_ENV and
        NEXTRON_DATA_DIR, as run.ps1 left them."""
        arguments = [
            f"-Env {env}",
            *switches,
            *([f"-ServeCommand {ps_array(command)}"] if command is not None else []),
            *(f"-{name} {ps_string(value)}" for name, value in params.items()),
        ]
        script = "\n".join(
            [
                "$ProgressPreference = 'SilentlyContinue'",
                f"& {ps_string(RUN_PS1)} {' '.join(arguments)}",
                "$code = $LASTEXITCODE",
                "$own = [Environment]::GetEnvironmentVariable('NEXTRON_ENV')",
                "[Console]::Out.WriteLine('own NEXTRON_ENV=' + $own)",
                "$own = [Environment]::GetEnvironmentVariable('NEXTRON_DATA_DIR')",
                "[Console]::Out.WriteLine('own NEXTRON_DATA_DIR=' + $own)",
                "exit $code",
            ]
        )
        assert POWERSHELL is not None
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        number = len(self.processes)
        out = self.tmp_path / f"powershell-{number}.out"
        err = self.tmp_path / f"powershell-{number}.err"
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
                env=self.env,
                cwd=self.tmp_path,  # no uv.exe here for CreateProcess's current-directory search
                creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
                startupinfo=startup,
            )
        self.processes.append((process, out, err))
        return process

    def finish(self, process: subprocess.Popen[bytes], timeout_s: float = RUN_TIMEOUT_S) -> Run:
        """Waits for a PowerShell started by ``start`` (killing its tree if it overruns)."""
        try:
            code = process.wait(timeout=timeout_s)
        finally:
            if process.poll() is None:
                kill_tree(process.pid)
                process.wait(timeout=30)
        [(_, out, err)] = [entry for entry in self.processes if entry[0] is process]
        return Run(
            code=code,
            stdout=out.read_text(encoding="utf-8", errors="replace"),
            stderr=err.read_text(encoding="utf-8", errors="replace"),
            events=self.events(),
            fake_events=self.fake_events(),
        )

    def run(self, command: Sequence[str] | None, *switches: str, **params: object) -> Run:
        """Runs run.ps1 to its end."""
        return self.finish(self.start(command, *switches, **params))

    def request(self, switch: str, **params: object) -> Run:
        """``run.ps1 -Stop``, ``-Restart`` or ``-Status``, from a second PowerShell."""
        return self.run(None, switch, **params)

    def wait_for(self, name: str, count: int = 1, *, fake: bool = False) -> dict[str, object]:
        """The ``count``-th event called ``name`` in the supervisor's (or the fake's) log."""
        found: list[dict[str, object]] = []

        def seen() -> bool:
            nonlocal found
            events = self.fake_events() if fake else self.events()
            found = [event for event in events if event["event"] == name]
            return len(found) >= count

        wait_until(seen, f"{name} number {count}")
        return found[count - 1]

    def cleanup(self) -> None:
        for process, _, _ in self.processes:
            if process.poll() is None:
                kill_tree(process.pid)
                process.wait(timeout=30)
        starts = [e for e in self.events() if e["event"] == "start"]
        pids = {int(e["pid"]) for e in starts}  # type: ignore[call-overload]
        for pid in pids | set(pids_of(self.fake_events())):
            kill_python(pid)


def base_env(**extra: str) -> dict[str, str]:
    """This process's environment (``NEXTRON_ENV=test``, the temporary ``%LOCALAPPDATA%``) without
    any other ``NEXTRON_*`` variable, plus ``extra``."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NEXTRON_")}
    env["NEXTRON_ENV"] = "test"
    env.update(extra)
    return env


@pytest.fixture
def sup(tmp_path: Path) -> Iterator[Supervision]:
    data_dir = tmp_path / "run data"  # a space, so every path must be quoted
    supervision = Supervision(tmp_path, data_dir, base_env(NEXTRON_DATA_DIR=str(data_dir)))
    try:
        yield supervision
    finally:
        supervision.cleanup()


# --- Settings and the loop ------------------------------------------------------------------------


def test_the_timing_defaults_are_ad20s(sup: Supervision) -> None:
    run = sup.run(sup.fake("--exit-after", "0", "--exit-code", "3"))

    assert run.code == 3, run.stderr
    [start] = run.of("supervisor_start")
    assert {key: start[key] for key in (
        "backoff_start_s", "backoff_max_s", "reset_after_s", "stale_after_s", "start_grace_s",
        "poll_s", "stop_grace_s", "request_timeout_s", "max_restarts",
    )} == {
        "backoff_start_s": 5, "backoff_max_s": 300, "reset_after_s": 600, "stale_after_s": 300,
        "start_grace_s": 900, "poll_s": 5, "stop_grace_s": 60, "request_timeout_s": 120,
        "max_restarts": -1,
    }
    assert (start["env"], start["data_dir"], start["working_dir"]) == (
        "test", str(sup.data_dir), str(ENGINE_ROOT)
    )


def test_a_crash_restarts_after_a_doubling_backoff_capped_at_the_maximum(sup: Supervision) -> None:
    run = sup.run(sup.fake("--exit-after", "0", "--exit-code", "1"), MaxRestarts=4, **FAST)

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        *["start", "exit", "backoff"] * 4,
        "start", "exit", "max_restarts",
    ]
    assert [e["wait_s"] for e in run.of("backoff")] == pytest.approx([0.1, 0.2, 0.4, 0.4])
    assert {(e["code"], e["killed"], e["requested"]) for e in run.of("exit")} == {(1, False, False)}
    assert len(run.fake_events) == 10  # five starts, five exits, all logged by the fake


def test_a_child_up_the_reset_time_since_its_first_beat_resets_the_backoff(
    sup: Supervision,
) -> None:
    run = sup.run(
        sup.fake("--interval", "0.05", "--exit-after", "1.5", "--exit-code", "0"),
        MaxRestarts=3,
        ResetAfterSeconds=0.5,
        **FAST,
    )

    assert run.code == 0, run.stderr
    assert all(float(e["up_s"]) >= 0.5 for e in run.of("exit"))  # type: ignore[arg-type]
    assert [e["wait_s"] for e in run.of("backoff")] == pytest.approx([0.1, 0.1, 0.1])
    assert {e["code"] for e in run.of("exit")} == {0}  # a clean exit restarts too


def test_a_child_that_never_beat_never_resets_the_backoff(sup: Supervision) -> None:
    # Up longer than the reset time, but never beat: a hung start never resets the backoff.
    run = sup.run(
        sup.fake("--beat-delay", "3600", "--exit-after", "0.8", "--exit-code", "1"),
        MaxRestarts=3,
        ResetAfterSeconds=0.3,
        BackoffStartSeconds=0.1,
        BackoffMaxSeconds=1,
        PollSeconds=0.1,
    )

    assert run.code == 0, run.stderr
    assert all(float(e["uptime_s"]) >= 0.3 for e in run.of("exit"))  # type: ignore[arg-type]
    assert {e["up_s"] for e in run.of("exit")} == {None}
    assert [e["wait_s"] for e in run.of("backoff")] == pytest.approx([0.1, 0.2, 0.4])


def test_the_childs_own_exit_3_ends_the_loop_and_the_supervisor_exits_3(sup: Supervision) -> None:
    run = sup.run(sup.fake("--exit-after", "0", "--exit-code", "3"), **FAST)

    assert run.code == 3, run.stderr
    assert run.names() == ["supervisor_start", "start", "exit", "stop"]
    [exit_event] = run.of("exit")
    assert (exit_event["code"], exit_event["killed"]) == (3, False)
    [stop] = run.of("stop")
    assert (stop["code"], stop["reason"]) == (3, "lock_held")


def test_five_exit_2s_in_a_row_stop_the_loop_and_the_supervisor_exits_2(sup: Supervision) -> None:
    run = sup.run(sup.fake("--exit-after", "0", "--exit-code", "2"), **FAST)

    assert run.code == 2, run.stderr
    assert run.names() == [
        "supervisor_start", *["start", "exit", "backoff"] * 4, "start", "exit", "stop",
    ]
    [stop] = run.of("stop")
    assert (stop["code"], stop["reason"], stop["count"]) == (2, "repeated_exit_2", 5)


# Exits with the code its run number picks from argv[2] (the last one repeats); argv[1] counts.
SEQUENCE = (
    "import pathlib, sys; p = pathlib.Path(sys.argv[1]); "
    "n = int(p.read_text()) if p.exists() else 0; p.write_text(str(n + 1)); "
    "codes = [int(c) for c in sys.argv[2].split(',')]; sys.exit(codes[min(n, len(codes) - 1)])"
)


def test_another_outcome_resets_the_exit_2_count(sup: Supervision) -> None:
    counter = sup.tmp_path / "runs.txt"
    run = sup.run(
        [sys.executable, "-c", SEQUENCE, str(counter), "2,2,2,2,1,2,2,2,2,2"], **FAST
    )

    assert run.code == 2, run.stderr
    assert [e["code"] for e in run.of("exit")] == [2, 2, 2, 2, 1, 2, 2, 2, 2, 2]
    assert run.names()[-1] == "stop"
    assert run.of("stop")[0]["reason"] == "repeated_exit_2"


def test_a_missing_executable_logs_start_failed_and_retries_after_the_backoff(
    sup: Supervision,
) -> None:
    run = sup.run([str(sup.tmp_path / "missing" / "serve.exe")], MaxRestarts=2, **FAST)

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        "start_failed", "backoff", "start_failed", "backoff", "start_failed",
        "max_restarts",
    ]
    assert [e["wait_s"] for e in run.of("backoff")] == pytest.approx([0.1, 0.2])
    assert all(e["error"] for e in run.of("start_failed"))


def test_the_default_command_is_uv_run_frozen_python_m_nextron_serve(sup: Supervision) -> None:
    windows = Path(os.environ["SYSTEMROOT"])
    folders = [windows / "System32", windows, windows / "System32/WindowsPowerShell/v1.0"]
    path = os.pathsep.join(str(folder) for folder in folders)
    if shutil.which("uv.exe", path=path) or shutil.which("uv.exe", path=str(sup.tmp_path)):
        pytest.skip("uv.exe is reachable without PATH; this test must never start uv")
    sup.env["PATH"] = path
    run = sup.run(None, MaxRestarts=0, **FAST)

    assert run.code == 0, run.stderr
    assert run.names() == ["supervisor_start", "start_failed", "max_restarts"]  # no uv: no start
    command = run.of("supervisor_start")[0]["command"]
    assert command == ["uv.exe", "run", "--frozen", "python", "-m", "nextron", "serve"]


# --- The heartbeat --------------------------------------------------------------------------------


def test_a_stale_heartbeat_kills_the_whole_job_and_restarts_it(sup: Supervision) -> None:
    run = sup.run(
        sup.fake("--interval", "0.05", "--stall", "--grandchild"),
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
    starts = [e for e in run.fake_events if e["event"] == "start"]
    assert len(starts) == 2
    for kill, start, fake_start in zip(run.of("kill"), run.of("start"), starts, strict=True):
        assert (kill["reason"], kill["method"], kill["ok"]) == ("stale", "job", True)
        tree = {int(start["pid"]), *pids_of([fake_start])}  # type: ignore[call-overload]
        assert len(tree) >= 4  # the launcher, the fake, and the grandchild's pids
        assert tree <= set(kill["pids"])  # type: ignore[arg-type]
    assert {(e["killed"], e["requested"]) for e in run.of("exit")} == {(True, False)}
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []


def test_when_the_job_kill_fails_each_process_is_stopped_and_the_child_restarts(
    sup: Supervision,
) -> None:
    run = sup.run(
        sup.fake("--interval", "0.05", "--stall", "--grandchild"),
        "-FailJobKillOnce",
        MaxRestarts=1,
        StaleAfterSeconds=0.5,
        StartGraceSeconds=30,
        **FAST,
    )

    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        "start", "stale", "kill", "kill", "exit", "backoff",
        "start", "stale", "kill", "exit", "max_restarts",
    ]
    failed, fallback, second = run.of("kill")
    assert (failed["reason"], failed["method"], failed["ok"]) == ("stale", "job", False)
    assert "-FailJobKillOnce" in str(failed["error"])
    assert (fallback["reason"], fallback["method"]) == ("stale", "process_kill")
    assert fallback["ok"] is True
    assert (second["method"], second["ok"]) == ("job", True)  # only the first kill fails
    [first_start] = run.of("start")[:1]
    first_fake = [e for e in run.fake_events if e["event"] == "start"][0]
    tree = {int(first_start["pid"]), *pids_of([first_fake])}  # type: ignore[call-overload]
    assert len(tree) >= 4  # the launcher, the fake, and the grandchild's pids
    assert tree <= set(fallback["pids"])  # type: ignore[arg-type]
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []


def test_a_beat_slower_than_the_poll_never_counts_as_stale(sup: Supervision) -> None:
    run = sup.run(
        sup.fake("--interval", "0.2", "--exit-after", "3", "--exit-code", "3"),
        StaleAfterSeconds=1.0,
        StartGraceSeconds=30,
        **FAST,
    )

    assert run.code == 3, run.stderr
    assert run.names() == ["supervisor_start", "start", "exit", "stop"]


@pytest.mark.parametrize("heartbeat", ["missing", "an hour old"])
def test_a_slow_start_is_not_killed_before_its_first_beat(sup: Supervision, heartbeat: str) -> None:
    if heartbeat == "an hour old":
        sup.data_dir.mkdir()
        beat = sup.data_dir / "heartbeat"
        beat.touch()
        old = beat.stat().st_mtime - 3600
        os.utime(beat, (old, old))
    run = sup.run(
        # The first beat comes 2 s in, past the 1 s stale limit: only the start grace saves it.
        sup.fake("--beat-delay", "2.0", "--interval", "0.05", "--exit-after", "2.8",
                 "--exit-code", "3"),
        StaleAfterSeconds=1.0,
        StartGraceSeconds=30,
        **FAST,
    )

    assert run.code == 3, run.stderr
    assert run.names() == ["supervisor_start", "start", "exit", "stop"]


def test_a_start_with_no_beat_within_the_start_grace_is_killed_and_restarted(
    sup: Supervision,
) -> None:
    run = sup.run(
        sup.fake("--beat-delay", "3600", "--grandchild"),
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
    assert all(kill["ok"] is True for kill in run.of("kill"))
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []


# --- The job object -------------------------------------------------------------------------------


def test_orphans_left_in_the_job_are_killed_before_a_normal_restart(sup: Supervision) -> None:
    process = sup.start(sup.fake("--interval", "0.05", "--grandchild"), MaxRestarts=1, **FAST)
    first = sup.wait_for("start", fake=True)
    kill_python(int(first["pid"]))  # type: ignore[call-overload]  # the fake, not its grandchild
    sup.wait_for("start", 2, fake=True)
    stop = sup.request("-Stop")
    run = sup.finish(process)

    assert stop.code == 0, stop.stderr
    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start",
        "start", "kill", "exit", "backoff",
        "start", "request", "exit", "stop",
    ]
    [kill] = run.of("kill")
    assert (kill["reason"], kill["method"], kill["ok"]) == ("orphans", "job", True)
    grandchild = {int(p) for p in first["grandchild_pids"]}  # type: ignore[attr-defined]
    assert grandchild <= set(kill["pids"])  # type: ignore[arg-type]
    exit_event = run.of("exit")[0]
    assert (exit_event["code"], exit_event["killed"], exit_event["requested"]) == (1, False, False)
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []


def test_killing_the_supervisor_ends_the_childs_whole_tree(sup: Supervision) -> None:
    process = sup.start(sup.fake("--interval", "0.05", "--grandchild"), **FAST)
    start = sup.wait_for("start")
    fake_start = sup.wait_for("start", fake=True)
    tree = {int(start["pid"]), *pids_of([fake_start])}  # type: ignore[call-overload]
    assert len(tree) >= 4

    process.kill()  # TerminateProcess on the supervisor's PowerShell, as ending its task does
    process.wait(timeout=30)

    assert [pid for pid in tree if not gone(pid)] == []
    assert sup.count("exit") == 0  # nothing ran in the supervisor after it was killed
    assert sup.request("-Status").stdout.startswith("Supervisor : not running for test")


# --- Requests -------------------------------------------------------------------------------------


def test_stop_ends_the_child_gracefully_and_the_supervisor_with_exit_0(sup: Supervision) -> None:
    process = sup.start(sup.fake("--interval", "0.05", "--grandchild", "--break-delay", "0.3"),
                        **FAST)
    sup.wait_for("start", fake=True)
    stop = sup.request("-Stop")
    run = sup.finish(process)

    assert (stop.code, stop.stderr) == (0, "")
    assert stop.stdout.startswith("stopped: ")
    assert run.code == 0, run.stderr
    assert run.names() == ["supervisor_start", "start", "request", "exit", "stop"]
    [request] = run.of("request")
    assert (request["text"], request["accepted"]) == ("stop", True)
    [exit_event] = run.of("exit")
    assert (exit_event["killed"], exit_event["requested"]) == (False, True)
    [stop_event] = run.of("stop")
    assert (stop_event["code"], stop_event["reason"]) == (0, "requested")
    fake_names = [e["event"] for e in run.fake_events]
    assert fake_names == ["start", "break", "exit"]  # the fake stopped by itself, gracefully
    assert run.fake_events[-1]["code"] == 0
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []
    assert not (sup.data_dir / "supervisor-request").exists()


def test_a_child_that_ignores_ctrl_break_is_killed_after_the_stop_grace(sup: Supervision) -> None:
    process = sup.start(sup.fake("--interval", "0.05", "--on-break", "ignore", "--grandchild"),
                        StopGraceSeconds=0.5, **FAST)
    sup.wait_for("start", fake=True)
    stop = sup.request("-Stop")
    run = sup.finish(process)

    assert (stop.code, run.code) == (0, 0), (stop.stderr, run.stderr)
    assert run.names() == ["supervisor_start", "start", "request", "kill", "exit", "stop"]
    [kill] = run.of("kill")
    assert (kill["reason"], kill["method"], kill["ok"]) == ("stop_timeout", "job", True)
    [exit_event] = run.of("exit")
    assert (exit_event["killed"], exit_event["requested"]) == (True, True)
    assert "break" in [e["event"] for e in run.fake_events]  # it got the CTRL_BREAK
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []


def test_stop_during_a_backoff_ends_the_loop_at_once(sup: Supervision) -> None:
    process = sup.start(sup.fake("--exit-after", "0", "--exit-code", "1"),
                        BackoffStartSeconds=60, BackoffMaxSeconds=60, PollSeconds=0.1)
    sup.wait_for("backoff")
    stop = sup.request("-Stop")
    run = sup.finish(process)

    assert (stop.code, run.code) == (0, 0), (stop.stderr, run.stderr)
    assert run.names() == ["supervisor_start", "start", "exit", "backoff", "request", "stop"]


def test_restart_during_a_backoff_starts_the_child_at_once_and_resets_the_backoff(
    sup: Supervision,
) -> None:
    process = sup.start(sup.fake("--exit-after", "0", "--exit-code", "1"),
                        BackoffStartSeconds=30, BackoffMaxSeconds=120, PollSeconds=0.1)
    sup.wait_for("backoff")
    restart = sup.request("-Restart")
    sup.wait_for("backoff", 2)
    stop = sup.request("-Stop")
    run = sup.finish(process)

    assert (restart.code, restart.stderr) == (0, ""), restart.stdout
    assert (stop.code, run.code) == (0, 0), (stop.stderr, run.stderr)
    assert run.names() == [
        "supervisor_start",
        "start", "exit", "backoff",
        "request", "start", "exit", "backoff",  # no backoff between the request and the start
        "request", "stop",
    ]
    assert [e["text"] for e in run.of("request")] == ["restart", "stop"]
    assert [e["wait_s"] for e in run.of("backoff")] == [30, 30]  # reset, not doubled to 60


def hold_unshared(path: Path) -> int:
    """Opens ``path`` with no sharing, as a running supervisor holds its lock; returns the
    handle for ``CloseHandle``."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.DWORD, wintypes.HANDLE,
    )
    generic_read_write, open_always, normal = 0xC0000000, 4, 0x80
    handle = kernel32.CreateFileW(str(path), generic_read_write, 0, None, open_always, normal, None)
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    return int(handle)


@pytest.mark.parametrize("switch", ["-Stop", "-Restart"])
def test_an_unanswered_request_times_out_with_exit_1_and_is_withdrawn(
    sup: Supervision, switch: str
) -> None:
    sup.data_dir.mkdir()
    handle = hold_unshared(sup.data_dir / "supervisor.lock")  # a supervisor that never answers
    try:
        run = sup.request(switch, RequestTimeoutSeconds=0.5)
    finally:
        _kernel32().CloseHandle(handle)

    assert run.code == 1
    [line] = run.stderr.splitlines()
    assert line.startswith("run.ps1: no answer from the supervisor for test within 0.5 s")
    assert sorted(p.name for p in sup.data_dir.iterdir()) == ["supervisor.lock"]


def test_restart_stops_the_child_gracefully_and_starts_the_code_on_disk_at_once(
    sup: Supervision,
) -> None:
    script = sup.tmp_path / "serve.py"
    shutil.copy2(FAKE, script)
    process = sup.start(sup.fake("--interval", "0.05", script=script), **FAST)
    sup.wait_for("start", fake=True)
    # "git pull": the code on disk changes while the child runs.
    source = script.read_text(encoding="utf-8")
    old = 'log(args.log, "start", **fields)'
    assert source.count(old) == 1
    script.write_text(source.replace(old, 'log(args.log, "start_v2", **fields)'), encoding="utf-8")

    restart = sup.request("-Restart")
    sup.wait_for("start_v2", fake=True)
    stop = sup.request("-Stop")
    run = sup.finish(process)

    assert (restart.code, restart.stderr) == (0, ""), restart.stdout
    assert restart.stdout.startswith("restarted: ")
    assert (stop.code, run.code) == (0, 0), (stop.stderr, run.stderr)
    assert f"new engine process (pid {run.of('start')[1]['pid']})" in restart.stdout
    assert run.names() == [
        "supervisor_start",
        "start", "request", "exit",  # no backoff: the new child starts at once
        "start", "request", "exit", "stop",
    ]
    assert [e["text"] for e in run.of("request")] == ["restart", "stop"]
    assert {(e["killed"], e["requested"], e["code"]) for e in run.of("exit")} == {(False, True, 0)}
    assert [e["event"] for e in run.fake_events] == ["start", "break", "exit", "start_v2", "break",
                                                     "exit"]


@pytest.mark.parametrize("switch", ["-Stop", "-Restart"])
def test_a_request_with_no_supervisor_exits_1_and_writes_nothing(
    sup: Supervision, switch: str
) -> None:
    run = sup.request(switch)

    assert run.code == 1
    assert run.stdout.splitlines()[0] == "own NEXTRON_ENV=test"
    [line] = run.stderr.splitlines()
    assert line.startswith("run.ps1: no supervisor is running for test")
    assert not sup.data_dir.exists()


def test_a_leftover_request_is_discarded_at_start_and_an_unknown_one_is_ignored(
    sup: Supervision,
) -> None:
    sup.data_dir.mkdir()
    (sup.data_dir / "supervisor-request").write_text("stop", encoding="utf-8")
    process = sup.start(sup.fake("--interval", "0.05", "--exit-after", "3", "--exit-code", "3"),
                        **FAST)
    sup.wait_for("start", fake=True)
    request = sup.data_dir / "supervisor-request"
    request.with_name("request.tmp").write_text("dance", encoding="utf-8")
    os.replace(request.with_name("request.tmp"), request)
    run = sup.finish(process)

    assert run.code == 3, run.stderr  # neither request stopped it: its own exit 3 did
    assert run.names() == [
        "supervisor_start", "request_discarded", "start", "request", "exit", "stop",
    ]
    assert run.of("request_discarded")[0]["text"] == "stop"
    [unknown] = run.of("request")
    assert (unknown["text"], unknown["accepted"]) == ("dance", False)
    assert not request.exists()


def test_only_one_of_stop_restart_and_status(sup: Supervision) -> None:
    run = sup.run(None, "-Stop", "-Status")

    assert run.code == 2
    [line] = run.stderr.splitlines()
    assert line.startswith("run.ps1: use at most one of")
    assert not sup.data_dir.exists()


def test_a_second_supervisor_exits_3_and_starts_nothing(sup: Supervision) -> None:
    first = sup.start(sup.fake("--interval", "0.05"), **FAST)
    sup.wait_for("start", fake=True)
    second = sup.run(sup.fake("--interval", "0.05"), **FAST)
    stop = sup.request("-Stop")
    run = sup.finish(first)

    assert second.code == 3
    [line] = second.stderr.splitlines()
    assert line.startswith("run.ps1: a supervisor is already running for test")
    [refused] = run.of("supervisor_refused")
    assert refused["reason"] == "lock_held"
    owner = run.of("supervisor_start")[0]["supervisor_pid"]
    assert refused["supervisor_pid"] != owner
    assert {e["supervisor_pid"] for e in run.of("start")} == {owner}
    assert len([e for e in run.fake_events if e["event"] == "start"]) == 1
    assert (stop.code, run.code) == (0, 0)


# --- Environment and data dir ---------------------------------------------------------------------


def test_the_child_alone_gets_nextron_env_and_the_data_dir(
    sup: Supervision, local_app_data: Path
) -> None:
    sup.env.pop("NEXTRON_DATA_DIR")
    sup.data_dir = local_app_data / "Nextron" / "prod"  # the default for -Env prod
    run = sup.run(sup.fake("--exit-after", "0", "--exit-code", "3"), env="prod", **FAST)

    assert run.code == 3, run.stderr
    [start] = [e for e in run.fake_events if e["event"] == "start"]
    assert (start["nextron_env"], start["nextron_data_dir"]) == ("prod", str(sup.data_dir))
    assert run.of("supervisor_start")[0]["env"] == "prod"
    # This PowerShell's own environment is unchanged.
    assert run.stdout.splitlines()[-2:] == ["own NEXTRON_ENV=test", "own NEXTRON_DATA_DIR="]


def test_a_data_dir_with_a_variable_is_expanded_for_the_child(sup: Supervision) -> None:
    sup.env["NEXTRON_TEST_ROOT"] = str(sup.tmp_path)
    sup.env["NEXTRON_DATA_DIR"] = r"%NEXTRON_TEST_ROOT%\var data"
    sup.data_dir = sup.tmp_path / "var data"
    run = sup.run(sup.fake("--exit-after", "0", "--exit-code", "3"), **FAST)

    assert run.code == 3, run.stderr
    [start] = [e for e in run.fake_events if e["event"] == "start"]
    assert start["nextron_data_dir"] == str(sup.data_dir)
    assert run.of("supervisor_start")[0]["data_dir"] == str(sup.data_dir)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("relative\\run data", lambda home, lad: ENGINE_ROOT / "relative" / "run data"),
        ("~\\run data", lambda home, lad: home / "run data"),
        ("", lambda home, lad: lad / "Nextron" / "test"),
    ],
    ids=["relative", "tilde", "empty"],
)
def test_the_data_dir_follows_the_engines_rule(
    sup: Supervision,
    local_app_data: Path,
    value: str,
    expected: Callable[[Path, Path], Path],
) -> None:
    home = sup.tmp_path / "home"
    sup.env["USERPROFILE"] = str(home)
    sup.env["NEXTRON_DATA_DIR"] = value
    run = sup.request("-Status")  # -Status reads only: nothing is created

    assert run.code == 0, run.stderr
    data_dir = expected(home, local_app_data)
    assert f"Data dir   : {data_dir}" in run.stdout.splitlines()
    assert not data_dir.exists()


# --- The log and -Status --------------------------------------------------------------------------


def test_every_line_is_utf8_json_and_the_log_rotates_at_1_mib(sup: Supervision) -> None:
    logs = sup.data_dir / "logs"
    logs.mkdir(parents=True)
    filler = b'{"event":"filler"}\n' * (1024 * 1024 // 19 + 1)
    assert len(filler) >= 1024 * 1024
    (logs / "supervisor.log").write_bytes(filler)
    for number, text in ((1, b"one\n"), (2, b"two\n"), (3, b"three\n")):
        (logs / f"supervisor.log.{number}").write_bytes(text)

    run = sup.run(sup.fake("--exit-after", "0", "--exit-code", "3"), **FAST)

    assert run.code == 3, run.stderr
    raw = sup.log.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == [
        "supervisor_start", "start", "exit", "stop",
    ]
    assert all(json.loads(line)["ts"].endswith("Z") for line in lines)
    assert (logs / "supervisor.log.1").read_bytes() == filler
    assert (logs / "supervisor.log.2").read_bytes() == b"one\n"
    assert (logs / "supervisor.log.3").read_bytes() == b"two\n"
    assert sorted(p.name for p in logs.iterdir()) == [
        "supervisor.log", "supervisor.log.1", "supervisor.log.2", "supervisor.log.3",
    ]


def test_status_reports_the_supervisor_the_heartbeat_and_the_last_15_lines(
    sup: Supervision,
) -> None:
    logs = sup.data_dir / "logs"
    logs.mkdir(parents=True)
    old = [json.dumps({"event": "old", "n": n}) for n in range(20)]
    (logs / "supervisor.log").write_text("\n".join(old) + "\n", encoding="utf-8")

    idle = sup.request("-Status")
    assert idle.code == 0, idle.stderr
    assert idle.stdout.splitlines()[:6] == [
        "Supervisor : not running for test",
        f"Data dir   : {sup.data_dir}",
        "Heartbeat  : no file yet",
        f"Log        : {sup.log}",
        "--- the last 15 lines of the supervisor log",
        old[5],
    ]
    assert idle.stdout.splitlines()[5:20] == old[5:]
    assert sorted(p.name for p in sup.data_dir.rglob("*")) == ["logs", "supervisor.log"]

    process = sup.start(sup.fake("--interval", "0.05"), **FAST)
    sup.wait_for("start", fake=True)
    wait_until((sup.data_dir / "heartbeat").exists, "the first beat")
    busy = sup.request("-Status")
    stop = sup.request("-Stop")
    sup.finish(process)

    assert busy.code == 0, busy.stderr
    lines = busy.stdout.splitlines()
    assert lines[0] == "Supervisor : running for test"
    assert lines[2].startswith("Heartbeat  : last beat ")
    assert '"event":"start"' in busy.stdout
    assert stop.code == 0


# --- The real engine ------------------------------------------------------------------------------


def nextron_lines(data_dir: Path) -> list[dict[str, object]]:
    """The complete ``nextron.main`` lines of the engine's log."""
    return [
        line for line in read_lines(data_dir / "logs" / "nextron.log")
        if line.get("logger") == "nextron.main"
    ]


def test_the_real_engine_migrates_before_its_first_beat_and_stops_cleanly_on_request(
    sup: Supervision, tmp_path: Path
) -> None:
    from nextron.db.migrations import SCRIPT_LOCATION, ensure_current, head_revision
    from tests.migration_chains import chain, engine_on, stored_revisions

    head = head_revision()
    versions = sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py"))
    assert versions[-1].stem == head
    behind = versions[-2].stem
    location = chain(tmp_path / "behind")
    for revision in versions[:-1]:
        shutil.copy2(revision, location / "versions" / revision.name)
    sup.data_dir.mkdir()
    db_file = sup.data_dir / "nextron.db"
    with engine_on(db_file) as engine:
        assert ensure_current(engine, script_location=location) == (None, behind)
    backups = tmp_path / "backups"
    sup.env.update(
        NEXTRON_BACKUP_DIR=str(backups), NEXTRON_PORT=str(free_port()), PYTHONIOENCODING="utf-8"
    )
    heartbeat = sup.data_dir / "heartbeat"

    process = sup.start([sys.executable, "-m", "nextron", "serve"], PollSeconds=0.2)
    wait_until(heartbeat.exists, "the engine's first beat", 120)
    assert stored_revisions(db_file) == [head]  # migrated before the first beat
    [copy] = sorted((backups / "local").glob("*.db"))
    first_beat = heartbeat.stat().st_mtime_ns

    restart = sup.request("-Restart")
    wait_until(lambda: heartbeat.stat().st_mtime_ns != first_beat, "the new engine's beat", 120)
    stop = sup.request("-Stop")
    run = sup.finish(process)

    assert (restart.code, restart.stderr) == (0, ""), restart.stdout
    assert (stop.code, stop.stderr) == (0, ""), stop.stdout
    assert run.code == 0, run.stderr
    assert run.names() == [
        "supervisor_start", "start", "request", "exit", "start", "request", "exit", "stop",
    ]
    assert {(e["code"], e["killed"], e["requested"]) for e in run.of("exit")} == {(0, False, True)}
    events = [line["event"] for line in nextron_lines(sup.data_dir)]
    assert events.count("database_migrated") == 1  # the second start found it at head
    assert events.index("database_migrated") < events.index("engine_started")
    stopped = [line for line in nextron_lines(sup.data_dir) if line["event"] == "engine_stopped"]
    assert [line["exit_code"] for line in stopped] == [0, 0]
    assert sorted((backups / "local").glob("*.db")) == [copy]  # one pre_migrate copy only
    assert [pid for pid in run.tree_pids() if not gone(pid)] == []
