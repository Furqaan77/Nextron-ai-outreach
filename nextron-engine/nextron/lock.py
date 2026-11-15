"""The per-environment instance lock (AD-2). Composition-root helper: only ``nextron.main`` uses it.

``serve`` holds an exclusive OS lock on ``<data dir>\\nextron.lock`` for its whole life, so one
environment never runs two engines (two runners, two schedulers, two SQLite writers). The OS
drops the lock when the process exits, even after a crash, so a stale lock file is harmless and
is never deleted. Windows uses ``msvcrt.locking``; other systems use ``fcntl.flock``.
"""

import errno
import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Final, Self

LOCK_FILE_NAME: Final = "nextron.lock"

# The errors that mean "someone else holds it": msvcrt.locking with LK_NBLCK raises EACCES or
# EDEADLK; flock with LOCK_NB raises EAGAIN (EWOULDBLOCK). Any other OSError is a real failure.
_HELD_ERRNOS: Final = frozenset({errno.EACCES, errno.EDEADLK, errno.EAGAIN, errno.EWOULDBLOCK})


class LockHeldError(Exception):
    """Another process (or another handle in this process) holds the lock."""


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # raises OSError when held

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises OSError when held

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class InstanceLock:
    """An exclusive, non-blocking lock on one file. Use ``acquire``/``release`` or ``with``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        """Take the lock or raise ``LockHeldError`` at once; other ``OSError``s propagate.

        The parent folder must exist.
        """
        if self._fd is not None:
            raise RuntimeError(f"{self.path} is already locked by this handle")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _try_lock(fd)
        except OSError as exc:
            os.close(fd)
            if exc.errno in _HELD_ERRNOS:
                raise LockHeldError(str(self.path)) from exc
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
