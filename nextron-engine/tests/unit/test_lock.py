"""The per-environment instance lock (AD-2)."""

import errno
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nextron import lock as lock_module
from nextron.lock import InstanceLock, LockHeldError

HOLD_LOCK = """
import sys
from pathlib import Path
from nextron.lock import InstanceLock
lock = InstanceLock(Path(sys.argv[1]))
lock.acquire()
print("locked", flush=True)
sys.stdin.readline()
lock.release()
"""


def test_acquire_creates_the_file_and_excludes_a_second_holder(tmp_path: Path) -> None:
    path = tmp_path / "nextron.lock"
    first = InstanceLock(path)

    first.acquire()
    try:
        assert path.exists()
        assert first.held
        with pytest.raises(LockHeldError):
            InstanceLock(path).acquire()
    finally:
        first.release()

    assert not first.held
    with InstanceLock(path) as second:
        assert second.held


def test_release_keeps_the_lock_file(tmp_path: Path) -> None:
    path = tmp_path / "nextron.lock"
    with InstanceLock(path):
        pass

    assert path.exists()


def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = InstanceLock(tmp_path / "nextron.lock")
    lock.acquire()
    lock.release()
    lock.release()


def test_other_lock_errors_propagate_and_close_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_locks(fd: int) -> None:
        raise OSError(errno.ENOLCK, "no locks available")

    closed: list[int] = []
    real_close = lock_module.os.close

    def recording_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(lock_module, "_try_lock", no_locks)
    monkeypatch.setattr(lock_module.os, "close", recording_close)
    lock = InstanceLock(tmp_path / "nextron.lock")

    with pytest.raises(OSError) as raised:
        lock.acquire()

    assert not isinstance(raised.value, LockHeldError)
    assert raised.value.errno == errno.ENOLCK
    assert len(closed) == 1
    assert not lock.held


def _start_holder(path: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "locked"
    return holder


def test_the_os_drops_the_lock_when_the_holder_is_killed(tmp_path: Path) -> None:
    path = tmp_path / "nextron.lock"
    holder = _start_holder(path)
    try:
        with pytest.raises(LockHeldError):
            InstanceLock(path).acquire()
    finally:
        holder.kill()  # a crash: no release, no clean exit
        holder.communicate(timeout=30)

    # Windows unlocks a dead process's regions "depending upon available system resources",
    # so allow a short delay.
    deadline = time.monotonic() + 10
    while True:
        try:
            with InstanceLock(path):
                break
        except LockHeldError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def test_a_lock_held_by_another_process_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "nextron.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdin is not None
        assert holder.stdout.readline().strip() == "locked"

        with pytest.raises(LockHeldError):
            InstanceLock(path).acquire()

        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.wait(timeout=30) == 0
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()

    with InstanceLock(path):
        pass
