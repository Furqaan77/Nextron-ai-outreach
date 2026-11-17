"""The job runner (AD-10): worker threads per lane and a keeper thread.

``nextron.main`` builds one ``Runner(units, queue, job_types, dead_jobs=...)`` and the FastAPI
lifespan starts and stops it (``nextron.main.engine_lifespan``), so handlers never run on the
event loop. It imports only ``nextron.app.jobqueue.api``, ``nextron.app.queries.setup``, the
domain and the standard library: it never reads the keyring or the network, and
``JobQueue.fail`` classifies errors.

**start()** reads ``bulk_threads`` once (``queries.setup.get(Key.BULK_THREADS)``) in a
read-only unit of work it closes before any thread starts; any error means 1 and logs
``bulk_threads_unreadable`` (WARNING) with the error's ``code``. It then starts daemon threads,
each in a fresh ``contextvars.Context``: ``nextron-job-realtime-1``,
``nextron-job-interactive-1``, ``nextron-job-bulk-1`` to ``nextron-job-bulk-<n>`` and
``nextron-job-keeper``, and logs ``job_runner_started`` with ``bulk_threads``. A second
``start()`` raises ``RuntimeError``.

**A lane thread** calls ``run_one(lane)`` until the runner stops, and waits ``poll_interval``
(1 s) on the stop event whenever nothing ran. An exception escaping ``run_one`` (a database
error, say) logs ``job_claim_failed`` with the traceback, waits ``poll_interval``, and the
thread goes on.

**run_one(lane)** (also the test hook) returns whether a job ran. It claims in its own unit of
work (``JobQueue.claim``), holds the claim with its monotonic start time, calls the handler in a
second unit of work (the handler calls one use case and returns ``None``, or the domain's
``DEFERRED`` when the budget refused its work; any other value, the plain string ``"deferred"``
included, counts as ``InvalidJob("the handler returned a value")``), and finishes in a third:
``complete`` for ``None``, ``defer`` for ``DEFERRED`` (back to ``queued`` an hour later with its
attempt given back, so a deferral never makes a job ``dead``), or ``fail`` with any
``Exception`` the handler raised. The claim is held only while the handler
runs: a ``finally`` drops it as soon as the handler returns, before finishing, so a keeper tick
after the finish commits never takes it for a lost claim (the lease, renewed every 30 s, still
has at least ``max_runtime`` + 30 s left). A failure to finish logs ``job_finish_failed`` and
leaves the lease to expire. When ``fail`` returns ``dead``, the dead-job sweep runs (below). A
``BaseException`` that isn't an ``Exception`` re-raises and ends the thread. Once the runner is
stopping, ``run_one`` claims nothing.

**The keeper** ticks at once and then every ``renew_interval`` (30 s). ``keep_once()`` (one
tick, also a test hook) calls ``JobQueue.keep`` with every held claim, which renews their
leases and reaps expired ones, and logs ``job_lease_lost`` (WARNING) once for each held claim it
could no longer renew. It logs ``job_overran`` (WARNING) once per claim whose run has passed
its ``max_runtime``: Python can't stop a thread, so the lease is still renewed. It runs the
purge (``maintain_once()``, also a test hook) on the first tick and then whenever
``maintenance_interval`` (24 h) has passed since the last purge that succeeded, so a failed
purge is tried again on the next tick. Any exception logs ``job_keeper_failed`` and the keeper
goes on. Each tick then runs the dead-job sweep.

**The dead-job sweep.** ``dead_jobs`` (``DeadJobs``: anything with ``alert_dead_jobs() -> int``,
the ``ops`` service's ``DeadJobAlerts`` in ``nextron.main``) raises the ``job_dead`` alert for
every dead job not yet alerted. The runner calls it in its own unit of work right after a run
whose ``fail`` returned ``dead``, and on every keeper tick, which catches the jobs that died
anywhere else (a reaped lease, an unrunnable claim, a release at shutdown) and any alert lost
to a crash. A sweep that raises logs ``dead_job_alerts_failed`` (ERROR, with the traceback) and
nothing stops. Without ``dead_jobs`` there is no sweep.

**stop()** sets the stop event (no thread claims again), joins the lane threads until
``shutdown_grace`` (15 s) has passed, one deadline for all, then the keeper; then it calls
``JobQueue.release`` with every claim still held in its own unit of work (an error logs
``job_finish_failed``) and logs ``job_runner_stopped`` with ``released``, the count. A handler
still running keeps its thread; when it ends, its finish finds the claim gone and logs
``job_claim_lost``. ``stop()`` does nothing before ``start()`` or a second time.

**alive()** is true while the runner has started, hasn't stopped, and every lane thread and the
keeper are alive. **overrunning()** returns the held claims whose run has passed their
``max_runtime`` on the runner's monotonic clock, the rule ``job_overran`` uses: a hung handler
keeps its thread alive, so the heartbeat writer (``nextron.jobs.heartbeat``) reads both and
withholds the beat while either says the runner is unwell.

Log records carry ``job_id``, ``job_type``, ``lane`` and ``attempts``, never a payload value.
"""

import contextvars
import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final, Protocol

from nextron.app.jobqueue.api import Claim, JobQueue
from nextron.app.queries import setup as setup_queries
from nextron.domain.errors import DomainError
from nextron.domain.job import (
    DEFERRED,
    MAX_BULK_THREADS,
    RENEW_INTERVAL,
    HandlerResult,
    InvalidJob,
    JobStatus,
    JobTypes,
    Lane,
)
from nextron.domain.settings_schema import Key

THREAD_PREFIX: Final = "nextron-job"
KEEPER_THREAD: Final = f"{THREAD_PREFIX}-keeper"
DEFAULT_BULK_THREADS: Final = 1
KEEPER_JOIN_TIMEOUT: Final = 10.0  # the keeper waits on the stop event, so it ends at once
HANDLER_RETURNED_A_VALUE: Final = "the handler returned a value"

log = logging.getLogger(__name__)


class Units(Protocol):
    """What the runner needs of ``nextron.db.uow.UnitOfWorkFactory``."""

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]: ...


class DeadJobs(Protocol):
    """The dead-job sweep: raise the ``job_dead`` alert of every dead job not yet alerted, and
    return how many it alerted (``nextron.app.ops.api.DeadJobAlerts``)."""

    def alert_dead_jobs(self) -> int: ...


@dataclass(slots=True)
class _Held:
    """A claim this process is running: when its run started, and what was logged about it."""

    claim: Claim
    started: float
    overran: bool = False
    lost: bool = False


def _key(claim: Claim) -> tuple[str, int]:
    return claim.job.id, claim.job.attempts


def _fields(claim: Claim) -> dict[str, object]:
    job = claim.job
    return {"job_id": job.id, "job_type": job.type, "lane": job.lane, "attempts": job.attempts}


def _overran(entry: _Held, now: float) -> bool:
    """Whether the run of ``entry`` has passed its ``max_runtime`` at monotonic ``now``."""
    return now - entry.started > entry.claim.max_runtime.total_seconds()


def _code(error: BaseException) -> str:
    return error.code if isinstance(error, DomainError) else type(error).__name__


class Runner:
    """Lane worker threads and the keeper; see the module docstring."""

    def __init__(
        self,
        units: Units,
        queue: JobQueue,
        job_types: JobTypes,
        *,
        poll_interval: float = 1.0,
        renew_interval: float = RENEW_INTERVAL.total_seconds(),
        maintenance_interval: float = 86_400.0,
        shutdown_grace: float = 15.0,
        monotonic: Callable[[], float] = time.monotonic,
        dead_jobs: DeadJobs | None = None,
    ) -> None:
        self._units = units
        self._queue = queue
        self._job_types = job_types
        self._dead_jobs = dead_jobs
        self._poll_interval = poll_interval
        self._renew_interval = renew_interval
        self._maintenance_interval = maintenance_interval
        self._shutdown_grace = shutdown_grace
        self._monotonic = monotonic
        self._lock = threading.Lock()  # guards the held claims and the started/stopped flags
        self._held: dict[tuple[str, int], _Held] = {}
        self._stopping = threading.Event()
        self._started = False
        self._stopped = False
        self._lanes: tuple[threading.Thread, ...] = ()
        self._keeper: threading.Thread | None = None
        self._last_maintenance: float | None = None

    # Starting and stopping -------------------------------------------------------------------

    def _read_bulk_threads(self) -> int:
        try:
            with self._units.open(read_only=True):
                value = setup_queries.get(Key.BULK_THREADS)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not DEFAULT_BULK_THREADS <= value <= MAX_BULK_THREADS
            ):
                raise ValueError("bulk_threads is not 1 or 2")
        except Exception as exc:  # noqa: BLE001 - any failure falls back to one bulk thread
            log.warning("bulk_threads_unreadable", extra={"code": _code(exc)})
            return DEFAULT_BULK_THREADS
        return value

    @staticmethod
    def _thread(name: str, target: Callable[..., None], *args: object) -> threading.Thread:
        return threading.Thread(
            target=target, args=args, name=name, daemon=True, context=contextvars.Context()
        )

    def start(self) -> None:
        """Read ``bulk_threads``, then start the lane threads and the keeper."""
        with self._lock:
            if self._started:
                raise RuntimeError("the job runner has already been started")
            self._started = True
        bulk_threads = self._read_bulk_threads()
        counts = ((Lane.REALTIME, 1), (Lane.INTERACTIVE, 1), (Lane.BULK, bulk_threads))
        lanes = tuple(
            self._thread(f"{THREAD_PREFIX}-{lane}-{number}", self._lane_loop, lane)
            for lane, count in counts
            for number in range(1, count + 1)
        )
        keeper = self._thread(KEEPER_THREAD, self._keeper_loop)
        self._lanes, self._keeper = lanes, keeper
        for thread in (*lanes, keeper):
            thread.start()
        log.info("job_runner_started", extra={"bulk_threads": bulk_threads})

    def stop(self) -> None:
        """Stop claiming, wait up to ``shutdown_grace`` for running handlers, then release the
        claims still held."""
        with self._lock:
            if not self._started or self._stopped:
                return
            self._stopped = True
        self._stopping.set()
        deadline = time.monotonic() + self._shutdown_grace  # joins wait in real time
        for thread in self._lanes:
            thread.join(max(0.0, deadline - time.monotonic()))
        if self._keeper is not None:
            self._keeper.join(KEEPER_JOIN_TIMEOUT)
        held = [entry.claim for entry in self._snapshot()]
        released: tuple[str, ...] = ()
        if held:
            try:
                with self._units.open():
                    released = self._queue.release(held)
            except Exception:
                log.exception("job_finish_failed")
        log.info("job_runner_stopped", extra={"released": len(released)})

    def alive(self) -> bool:
        """Whether the runner is running with every lane thread and the keeper alive."""
        with self._lock:
            if not self._started or self._stopped:
                return False
        threads = (*self._lanes, self._keeper)
        return bool(self._lanes) and all(
            thread is not None and thread.is_alive() for thread in threads
        )

    def overrunning(self) -> tuple[Claim, ...]:
        """The held claims whose run has passed their ``max_runtime`` (the ``job_overran``
        rule), oldest run first."""
        now = self._monotonic()
        with self._lock:
            entries = sorted(
                (entry for entry in self._held.values() if _overran(entry, now)),
                key=lambda entry: entry.started,
            )
        return tuple(entry.claim for entry in entries)

    # The held claims -------------------------------------------------------------------------

    def _hold(self, claim: Claim) -> None:
        with self._lock:
            self._held[_key(claim)] = _Held(claim, self._monotonic())

    def _drop(self, claim: Claim) -> None:
        with self._lock:
            self._held.pop(_key(claim), None)

    def _snapshot(self) -> list[_Held]:
        """The held claims whose leases are still this process's."""
        with self._lock:
            return [entry for entry in self._held.values() if not entry.lost]

    # Lanes -----------------------------------------------------------------------------------

    def _lane_loop(self, lane: Lane) -> None:
        while not self._stopping.is_set():
            try:
                ran = self.run_one(lane)
            except Exception:
                log.exception("job_claim_failed", extra={"lane": lane})
                ran = False
            if not ran:
                self._stopping.wait(self._poll_interval)

    def run_one(self, lane: Lane) -> bool:
        """Claim, run and finish one job of ``lane``; whether one ran."""
        if self._stopping.is_set():
            return False
        with self._units.open():
            claim = self._queue.claim(lane)
        if claim is None:
            return False
        self._hold(claim)
        try:
            outcome = self._run_handler(claim)
        finally:
            # Dropped before finishing, so a keeper tick after the finish commits can't take
            # the claim for a lost one. The lease still has at least max_runtime + 30 s left.
            self._drop(claim)
        self._finish(claim, outcome)
        return True

    def _run_handler(self, claim: Claim) -> Exception | HandlerResult | None:
        """Run the job's handler in its own unit of work; its outcome: ``None`` (done),
        ``DEFERRED``, or the exception it raised (a value it returned is ``InvalidJob``)."""
        try:
            handler = self._job_types.get(claim.job.type).handler
            with self._units.open():
                result: object = handler(claim.job)
        except Exception as exc:  # noqa: BLE001 - JobQueue.fail decides what to retry
            return exc
        if result is None:
            return None
        if result is DEFERRED:  # the domain's member itself, never an equal string
            return DEFERRED
        return InvalidJob(HANDLER_RETURNED_A_VALUE)

    def _finish(self, claim: Claim, outcome: Exception | HandlerResult | None) -> None:
        status: JobStatus | None = None
        try:
            with self._units.open():
                if outcome is None:
                    self._queue.complete(claim)
                elif isinstance(outcome, Exception):
                    status = self._queue.fail(claim, outcome)
                else:  # DEFERRED: back to queued, the attempt given back; never dead
                    self._queue.defer(claim)
        except Exception:
            log.exception("job_finish_failed", extra=_fields(claim))
            return
        if status is JobStatus.DEAD:
            self.sweep_dead_jobs()

    def sweep_dead_jobs(self) -> None:
        """Run the dead-job sweep in its own unit of work; a failure is logged, never
        raised. Does nothing without ``dead_jobs``."""
        if self._dead_jobs is None:
            return
        try:
            with self._units.open():
                self._dead_jobs.alert_dead_jobs()
        except Exception:
            log.exception("dead_job_alerts_failed")

    # The keeper ------------------------------------------------------------------------------

    def _keeper_loop(self) -> None:
        while True:
            self.keep_once()
            if self._stopping.wait(self._renew_interval):
                return

    def keep_once(self) -> None:
        """One keeper tick: renew and reap, log overruns, and purge when it is due."""
        try:
            self._renew()
        except Exception:
            log.exception("job_keeper_failed")
        try:
            self._log_overruns()
        except Exception:
            log.exception("job_keeper_failed")
        if self._maintenance_due():
            try:
                self.maintain_once()
            except Exception:
                log.exception("job_keeper_failed")
        self.sweep_dead_jobs()

    def _renew(self) -> None:
        held = self._snapshot()
        with self._units.open():
            kept = self._queue.keep([entry.claim for entry in held])
        lost: list[Claim] = []
        with self._lock:
            for claim in kept.lost:
                entry = self._held.get(_key(claim))
                if entry is not None and not entry.lost:  # dropped meanwhile: it finished
                    entry.lost = True
                    lost.append(claim)
        for claim in lost:
            log.warning("job_lease_lost", extra=_fields(claim))

    def _log_overruns(self) -> None:
        now = self._monotonic()
        overran: list[Claim] = []
        with self._lock:
            for entry in self._held.values():
                if not entry.overran and _overran(entry, now):
                    entry.overran = True
                    overran.append(entry.claim)
        for claim in overran:
            log.warning("job_overran", extra=_fields(claim))

    def _maintenance_due(self) -> bool:
        last = self._last_maintenance
        return last is None or self._monotonic() - last >= self._maintenance_interval

    def maintain_once(self) -> int:
        """Purge old ``done`` jobs now; how many were deleted. Only a purge that returns
        restarts the ``maintenance_interval``, so a failed one is tried again on the next
        tick."""
        with self._units.open():
            count = self._queue.purge_done()
        self._last_maintenance = self._monotonic()
        return count
