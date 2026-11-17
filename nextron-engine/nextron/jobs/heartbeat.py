"""The heartbeat writer (AD-2, AD-20, NFR-14): proof that the engine is alive.

It imports only ``nextron.app.ops.api`` and the standard library; the runner and the scheduler
are reached through local Protocols.

The supervisor (``scripts/run.ps1``, Story 1.21) restarts the engine when ``<data dir>\\heartbeat``
is more than 5 minutes old. ``nextron.main`` builds one ``HeartbeatWriter(units, heartbeat,
runner, scheduler)`` and the FastAPI lifespan starts it after the scheduler and stops it first.

**start()** runs one tick at once, then starts the daemon thread ``nextron-job-heartbeat`` in a
fresh ``contextvars.Context()``, which ticks every ``interval`` (30 s) until ``stop()``. A
second ``start()`` raises ``RuntimeError``.

**beat_once()** is one tick (and the test hook). It withholds the beat for the first of these
reasons that holds:

1. ``runner_down``: ``runner.alive()`` is false (a lane thread or the keeper ended, or the
   runner isn't running);
2. ``job_overran``: ``runner.overrunning()`` holds a claim past its ``max_runtime``: a hung
   handler keeps its thread alive, so only this catches it;
3. ``scheduler_down``: ``scheduler.alive()`` is false (APScheduler stopped, or the event loop
   didn't answer its probe in time).

Otherwise it calls ``heartbeat.beat()`` (``nextron.app.ops.api.Heartbeat``: the
``runner_heartbeat`` row, then the file) in its own unit of work. It never raises.

**Logs**, written only when the outcome changes: ``heartbeat_withheld`` (WARNING, ``reason``)
when the beat is first withheld, or withheld for another reason; ``heartbeat_failed`` (ERROR,
``code``, the error's class name, and the traceback) once per streak of failures (a check or
the beat raised); ``heartbeat_resumed`` (INFO) on the first beat after either. A beat logs
nothing otherwise.

**stop()** sets the stop event and joins the thread (at most 20 s). It does nothing before
``start()`` or a second time.
"""

import contextvars
import logging
import threading
from contextlib import AbstractContextManager
from enum import StrEnum
from typing import Final, Protocol

from nextron.app.ops.api import Heartbeat

THREAD_NAME: Final = "nextron-job-heartbeat"
DEFAULT_INTERVAL: Final = 30.0
JOIN_TIMEOUT: Final = 20.0

log = logging.getLogger(__name__)


class Units(Protocol):
    """What the writer needs of ``nextron.db.uow.UnitOfWorkFactory``."""

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]: ...


class RunnerHealth(Protocol):
    """What the writer reads of ``nextron.jobs.runner.Runner``."""

    def alive(self) -> bool: ...

    def overrunning(self) -> tuple[object, ...]: ...


class SchedulerHealth(Protocol):
    """What the writer reads of ``nextron.jobs.scheduler.Scheduler``."""

    def alive(self) -> bool: ...


class Withheld(StrEnum):
    """Why a tick withheld the beat, in the order the checks run."""

    RUNNER_DOWN = "runner_down"
    JOB_OVERRAN = "job_overran"
    SCHEDULER_DOWN = "scheduler_down"


_BEAT: Final = "beat"
_FAILED: Final = "failed"


class HeartbeatWriter:
    """Beats every ``interval`` while the runner, its jobs and the scheduler are healthy."""

    def __init__(
        self,
        units: Units,
        heartbeat: Heartbeat,
        runner: RunnerHealth,
        scheduler: SchedulerHealth,
        *,
        interval: float = DEFAULT_INTERVAL,
    ) -> None:
        self._units = units
        self._heartbeat = heartbeat
        self._runner = runner
        self._scheduler = scheduler
        self._interval = interval
        self._lock = threading.Lock()  # guards the started/stopped flags and the last outcome
        self._stopping = threading.Event()
        self._started = False
        self._stopped = False
        self._thread: threading.Thread | None = None
        self._last: str | None = None  # the last tick's outcome: beat, failed or a reason

    def start(self) -> None:
        """Tick once now, then start the heartbeat thread."""
        with self._lock:
            if self._started:
                raise RuntimeError("the heartbeat writer has already been started")
            self._started = True
        self.beat_once()
        thread = threading.Thread(
            target=self._loop, name=THREAD_NAME, daemon=True, context=contextvars.Context()
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop ticking and wait up to ``JOIN_TIMEOUT`` for the thread to end."""
        with self._lock:
            if not self._started or self._stopped:
                return
            self._stopped = True
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(JOIN_TIMEOUT)

    def _loop(self) -> None:
        while not self._stopping.wait(self._interval):
            self.beat_once()

    def _withheld(self) -> Withheld | None:
        if not self._runner.alive():
            return Withheld.RUNNER_DOWN
        if self._runner.overrunning():
            return Withheld.JOB_OVERRAN
        if not self._scheduler.alive():
            return Withheld.SCHEDULER_DOWN
        return None

    def beat_once(self) -> None:
        """One tick: withhold the beat, or beat; log a change of outcome. Never raises."""
        error: Exception | None = None
        reason: Withheld | None = None
        try:
            reason = self._withheld()
            if reason is None:
                with self._units.open():
                    self._heartbeat.beat()
        except Exception as exc:  # noqa: BLE001 - a tick never raises; the outcome is logged
            error = exc
        outcome = _FAILED if error is not None else _BEAT if reason is None else str(reason)
        with self._lock:
            previous, self._last = self._last, outcome
        if outcome == previous:
            return
        if error is not None:
            log.error("heartbeat_failed", exc_info=error, extra={"code": type(error).__name__})
        elif reason is not None:
            log.warning("heartbeat_withheld", extra={"reason": reason})
        elif previous is not None:
            log.info("heartbeat_resumed")
