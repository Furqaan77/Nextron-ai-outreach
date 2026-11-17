"""Checking and ending the processes a test's logs name (Stories 1.20 and 1.21).

Windows hands a freed pid to a new process soon, and other processes start all the time (the
suite's other workers, other checkouts' runs). So by the time a test looks at a pid a log named,
an unrelated process may hold it. ``gone`` and ``kill_python`` take ``named_at``, the stamp of
the log line that named the pid: a running process created more than ``PID_REUSE_SLACK`` after
it is a newer process on the reused pid, so ours has ended, and cleanup never kills it. A real
leak is still caught: an original process is always created before the line that names it.

The Windows APIs load on first use, so importing this module works on any platform.
"""

import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_TIMEOUT = 0x102
GONE_TIMEOUT_S = 15
# The slack covers the logs' millisecond stamps and the coarser clocks behind them.
PID_REUSE_SLACK = timedelta(seconds=1)


def load_kernel32():  # type: ignore[no-untyped-def]
    """kernel32, with the signatures these helpers use."""
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
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    filetime = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, filetime, filetime, filetime, filetime)
    return kernel32


def _reused(  # type: ignore[no-untyped-def]
    kernel32, handle: int, named_at: datetime | None
) -> bool:
    """Whether the process behind ``handle`` was created more than ``PID_REUSE_SLACK`` after
    ``named_at``, so it isn't the one a log named: Windows reused the pid. False when unknown."""
    import ctypes
    from ctypes import wintypes

    if named_at is None:
        return False
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return False
    ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime  # 100 ns since 1601, UTC
    created = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
    return created > named_at + PID_REUSE_SLACK


def gone(
    pid: int, timeout_s: float = GONE_TIMEOUT_S, *, named_at: datetime | None = None
) -> bool:
    """Whether process ``pid`` has ended, waiting up to ``timeout_s`` for it. With ``named_at``
    (when a log named ``pid`` as ours), a process on a reused pid counts as ours having ended."""
    kernel32 = load_kernel32()
    handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True
    try:
        if _reused(kernel32, handle, named_at):
            return True
        return kernel32.WaitForSingleObject(handle, int(timeout_s * 1000)) != WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def still_running(named: Mapping[int, datetime]) -> list[int]:
    """The pids of ``named`` (each with when a log last named it) whose process hasn't ended (a
    reused pid isn't ours)."""
    return sorted(pid for pid, at in named.items() if not gone(pid, named_at=at))


def kill_python(pid: int, *, named_at: datetime | None = None) -> None:
    """Ends ``pid`` if it is still a running ``python.exe`` (a pid can be reused), and, with
    ``named_at``, only if it isn't a newer process on a reused pid."""
    import ctypes
    from ctypes import wintypes

    kernel32 = load_kernel32()
    access = PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return
    try:
        if kernel32.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT:
            return
        if _reused(kernel32, handle, named_at):
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
    """Ends ``pid`` and its descendants with ``taskkill /T /F``."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
