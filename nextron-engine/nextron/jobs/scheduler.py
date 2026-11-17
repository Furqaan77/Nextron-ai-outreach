"""Periodic triggers on APScheduler (AD-2, AD-10, NFR-14).

It imports ``apscheduler``, ``nextron.app.jobqueue.api``, ``nextron.jobs.triggers``, the domain
and the standard library; nothing else in ``nextron`` imports ``apscheduler``.

``nextron.main`` builds one ``Scheduler(units, queue, triggers)`` and the FastAPI lifespan
starts it right after the job runner and stops it right before (``nextron.main.engine_lifespan``).
It takes only a frozen ``Triggers`` (``TypeError``, ``RuntimeError``).

**start()** runs on the event loop, once (a second call raises ``RuntimeError``). It creates an
``AsyncIOScheduler(timezone=UTC)`` and, for each registered trigger, adds ``fire`` as an
interval job: ``IntervalTrigger(seconds=<period>, timezone=UTC)``, ``args=(trigger,)``,
``id=<trigger name>``, ``coalesce=True``, ``misfire_grace_time=<period in seconds>`` and
``max_instances=1``; an ``at_startup`` trigger also gets ``next_run_time=<now>``. It then starts
APScheduler and logs ``scheduler_started`` with ``triggers``, the count.

**Sleep.** On Windows the event loop's clock (QueryPerformanceCounter) counts the time the PC
slept, and Uvicorn's server loop wakes the event loop every 0.1 s, so APScheduler's timer runs
within a moment of the wake and finds the firings missed during the sleep. With ``coalesce`` it
submits only the latest missed slot, once. The next firing keeps the original schedule: it is
due at that slot plus one period, the first slot after the wake, 0 to one period later. The
grace equals the period, so the catch-up normally runs. When the wake lands just before a slot,
though, the catch-up can be past its grace by the time it runs: APScheduler logs it as missed
(WARNING) and the slot that has just come due fires at once instead. Either way a sleep gives
one firing, not one per missed slot. This has not yet been observed on a real PC sleep; Story
1.20's spike on the PC is where it gets checked.

**Restarts.** The schedules live in memory, so each interval starts from zero at every engine
start (see ``nextron.jobs.triggers.Every``).

**fire(trigger)** is the job APScheduler runs, as a sync job on the loop's default executor
thread, never on the loop. It opens one unit of work and calls
``queue.enqueue_tick(trigger.value)``, which a live job with the same key absorbs. It runs no
handler: the runner's lanes do. Any exception logs ``trigger_failed`` (ERROR, with ``trigger``,
the error's ``code`` and the traceback) and is not re-raised, so later firings still run. A
firing that succeeds logs nothing: the job's creation event records it.

**stop()** (async, on the loop) shuts APScheduler down without waiting, yields to the loop once
so its deferred shutdown runs, and logs ``scheduler_stopped``. It does nothing before
``start()`` or a second time. A ``fire`` already running finishes on its executor thread.

**alive()** is called off the loop's thread (on it, it raises ``RuntimeError``). It is true
only while the scheduler has started and hasn't stopped, APScheduler is running, and the loop
runs a ``call_soon_threadsafe`` probe within ``probe_timeout`` (10 s): a stuck event loop
leaves triggers and the dashboard dead, so the heartbeat writer withholds the beat.

**wakeup()** (async, on the loop; a test hook) processes the due triggers now, as APScheduler's
timer does when the PC wakes.
"""

import asyncio
import logging
import threading
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Final, Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from nextron.app.jobqueue.api import JobQueue, error_code
from nextron.jobs.triggers import Trigger, Triggers

DEFAULT_PROBE_TIMEOUT: Final = 10.0

log = logging.getLogger(__name__)


class Units(Protocol):
    """What the scheduler needs of ``nextron.db.uow.UnitOfWorkFactory``."""

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]: ...


class Scheduler:
    """The periodic triggers on an ``AsyncIOScheduler``; see the module docstring."""

    def __init__(
        self,
        units: Units,
        queue: JobQueue,
        triggers: Triggers,
        *,
        probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    ) -> None:
        if not isinstance(triggers, Triggers):
            raise TypeError("triggers must be a Triggers registry")
        if not triggers.frozen:
            raise RuntimeError("freeze the trigger registry before building the scheduler")
        self._units = units
        self._queue = queue
        self._triggers = triggers
        self._probe_timeout = probe_timeout
        self._lock = threading.Lock()  # guards the started/stopped flags and the loop
        self._started = False
        self._stopped = False
        self._scheduler: AsyncIOScheduler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def start(self) -> None:
        """Schedule every registered trigger and start APScheduler on the running loop."""
        loop = asyncio.get_running_loop()  # raises RuntimeError off the loop
        with self._lock:
            if self._started:
                raise RuntimeError("the scheduler has already been started")
            self._started = True
            self._loop, self._loop_thread = loop, threading.get_ident()
        scheduler = AsyncIOScheduler(timezone=UTC)
        specs = self._triggers.specs()
        for spec in specs:
            seconds = int(spec.schedule.period.total_seconds())
            grace = int(spec.schedule.grace.total_seconds())
            first: dict[str, datetime] = (
                {"next_run_time": datetime.now(UTC)} if spec.schedule.at_startup else {}
            )
            scheduler.add_job(
                self.fire,
                IntervalTrigger(seconds=seconds, timezone=UTC),
                args=(spec.trigger,),
                id=spec.trigger.value,
                coalesce=True,
                misfire_grace_time=grace,
                max_instances=1,
                **first,
            )
        self._scheduler = scheduler  # before starting it, so stop() can always shut it down
        scheduler.start()
        log.info("scheduler_started", extra={"triggers": len(specs)})

    def fire(self, trigger: Trigger) -> None:
        """Enqueue ``trigger``'s tick job in its own unit of work; never raises."""
        try:
            with self._units.open():
                self._queue.enqueue_tick(trigger.value)
        except Exception as exc:  # noqa: BLE001 - a failed firing must not stop later ones
            log.exception("trigger_failed", extra={"trigger": trigger, "code": error_code(exc)})

    async def stop(self) -> None:
        """Shut APScheduler down; see the module docstring."""
        with self._lock:
            if not self._started or self._stopped:
                return
            self._stopped = True
        scheduler = self._scheduler
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
            await asyncio.sleep(0)  # APScheduler defers its shutdown to the loop's next turn
        log.info("scheduler_stopped")

    def alive(self) -> bool:
        """Whether the scheduler runs and its loop answers a probe within ``probe_timeout``."""
        with self._lock:
            if threading.get_ident() == self._loop_thread:
                raise RuntimeError("alive() would block the event loop; call it off the loop")
            if not self._started or self._stopped:
                return False
            loop = self._loop
        scheduler = self._scheduler
        if loop is None or scheduler is None or not scheduler.running:
            return False
        answered = threading.Event()
        try:
            loop.call_soon_threadsafe(answered.set)
        except RuntimeError:  # the loop is closed
            return False
        return answered.wait(self._probe_timeout)

    async def wakeup(self) -> None:
        """Process the due triggers now, as APScheduler's timer does when the PC wakes."""
        scheduler = self._scheduler
        if self._stopped or scheduler is None or not scheduler.running:
            raise RuntimeError("the scheduler is not running")
        scheduler.wakeup()
        await asyncio.sleep(0)  # APScheduler defers the wakeup to the loop's next turn
