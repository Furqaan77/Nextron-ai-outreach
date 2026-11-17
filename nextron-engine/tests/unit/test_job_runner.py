"""The job runner (``nextron/jobs/runner.py``, AD-10) on a temporary database at head.

Behaviour goes through ``run_one`` and ``keep_once`` with ``FrozenClock``; the threaded tests
start real lane and keeper threads with small intervals and wait on events and conditions in
slices of at most 0.05 s, never with a sleep.
"""

import asyncio
import contextvars
import inspect
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.jobqueue.api import Claim, Enqueued, JobQueue
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.db.repos.jobqueue.jobs import JobRow
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.job import RENEW_INTERVAL, Job, JobType, JobTypes, Lane
from nextron.domain.settings_schema import Key
from nextron.jobs.runner import Runner
from nextron.ports.errors import AdapterError
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
SLICE = 0.05  # the longest single wait in this module
ATTEMPTS = 400  # slices before a condition counts as never reached (20 s)
LOGGER = "nextron.jobs.runner"
QUEUE_LOGGER = "nextron.app.jobqueue.api"
FAST = {"poll_interval": 0.01, "renew_interval": 0.02, "shutdown_grace": SLICE}


def wait_until(condition: Callable[[], bool]) -> bool:
    """Whether ``condition`` becomes true, checked every 0.05 s at most."""
    pause = threading.Event()
    for _ in range(ATTEMPTS):
        if condition():
            return True
        pause.wait(SLICE)
    return condition()


def wait_for(event: threading.Event) -> bool:
    return wait_until(event.is_set)


class Gate:
    """A handler that records each job and its thread, and blocks until ``open()``."""

    def __init__(self, *, blocking: bool = True) -> None:
        self.entered = threading.Event()
        self.proceed = threading.Event()
        self.left = threading.Event()
        if not blocking:
            self.proceed.set()
        self.jobs: list[Job] = []
        self.threads: list[threading.Thread] = []
        self.loops: list[bool] = []

    def __call__(self, job: Job) -> None:
        self.jobs.append(job)
        self.threads.append(threading.current_thread())
        try:
            asyncio.get_running_loop()
            self.loops.append(True)
        except RuntimeError:
            self.loops.append(False)
        self.entered.set()
        try:
            assert wait_for(self.proceed)
        finally:
            self.left.set()

    def open(self) -> None:
        self.proceed.set()


def job_type(
    handler: Callable[[Job], object],
    *,
    name: str = "enrich_lead",
    lane: Lane = Lane.BULK,
    retry_limit: int = 3,
    max_runtime: timedelta = timedelta(minutes=5),
) -> JobType:
    return JobType(
        name, lane, frozenset({lane}), 50, max_runtime, retry_limit, handler
    )


def registry(*job_types: JobType) -> JobTypes:
    found = JobTypes()
    for declared in job_types:
        found.register_job_type(declared)
    found.freeze()
    return found


class Monotonic:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RecordingUnits:
    """``units`` that records each unit of work opened: ``(read_only, thread name)``, and
    ``closed`` as each one ends."""

    def __init__(self, inner: UnitOfWorkFactory) -> None:
        self.inner = inner
        self.log: list[tuple[str, bool, str]] = []

    @contextmanager
    def open(self, *, read_only: bool = False) -> Iterator[object]:
        name = threading.current_thread().name
        self.log.append(("open", read_only, name))
        try:
            with self.inner.open(read_only=read_only) as uow:
                yield uow
        finally:
            self.log.append(("close", read_only, name))


class FailingUnits:
    """``units`` whose ``open`` raises for the first ``failures`` write units on lane threads,
    or for every unit when ``failures`` is ``None``."""

    def __init__(self, inner: UnitOfWorkFactory, failures: int | None) -> None:
        self.inner = inner
        self.failures = failures
        self.lock = threading.Lock()

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]:
        if self.failures is None:
            raise RuntimeError("database is locked")
        on_lane = threading.current_thread().name.startswith("nextron-job-bulk")
        with self.lock:
            if on_lane and not read_only and self.failures > 0:
                self.failures -= 1
                raise RuntimeError("database is locked")
        return self.inner.open(read_only=read_only)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def monotonic() -> Monotonic:
    return Monotonic()


@pytest.fixture
def runners() -> Iterator[list[Runner]]:
    """Every runner a test builds; each is stopped at teardown."""
    built: list[Runner] = []
    yield built
    for runner in built:
        runner.stop()


def build(
    runners: list[Runner],
    units: Any,
    clock: FrozenClock,
    *job_types: JobType,
    **options: Any,
) -> tuple[Runner, JobQueue]:
    types = registry(*job_types)
    queue = JobQueue(clock, types)
    runner = Runner(units, queue, types, **options)
    runners.append(runner)
    return runner, queue


def enqueue(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    job_type_name: str = "enrich_lead",
    *,
    discriminator: str = "1",
    payload: dict[str, object] | None = None,
    **options: Any,
) -> Enqueued:
    @use_case
    def caller() -> Enqueued:
        with current().transaction():
            return queue.enqueue(
                job_type_name,
                scope=LEAD,
                discriminator=discriminator,
                payload={"v": 1, "lead_id": LEAD} if payload is None else payload,
                **options,
            )

    with units.open():
        return caller()


def row(units: UnitOfWorkFactory, job_id: str) -> JobRow:
    with units.open(read_only=True):
        found = jobqueue_queries.job(job_id)
    assert found is not None
    return found


def claim_directly(units: UnitOfWorkFactory, queue: JobQueue) -> Claim:
    """A claim no runner holds, as a crashed process would leave it."""
    with units.open():
        found = queue.claim(Lane.BULK)
    assert found is not None
    return found


def records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == event]


def job_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("nextron-job-"))


def store_setting(engine: Engine, key: str, text: str) -> None:
    """A stored text written behind ``Setup``'s back, as an older version might have."""
    assert engine.url.database
    writer = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        writer.execute(
            "INSERT INTO settings (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            ("01JD3QZ8X5V9K2M7N4P6R8T0WY", key, text, "2026-11-01T00:00:00.000000Z"),
        )
    finally:
        writer.close()


# run_one ----------------------------------------------------------------------------------------


def test_run_one_runs_a_job_in_three_units_of_work_and_completes_it(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    gate = Gate(blocking=False)
    recording = RecordingUnits(units)
    runner, queue = build(runners, recording, clock, job_type(gate))
    created = enqueue(units, queue)

    assert runner.run_one(Lane.BULK) is True

    [job] = gate.jobs
    assert (job.id, job.type, job.lane, dict(job.payload), job.attempts) == (
        created.job_id,
        "enrich_lead",
        Lane.BULK,
        {"v": 1, "lead_id": LEAD},
        1,
    )
    done = row(units, created.job_id)
    assert (done.status, done.lease_until) == ("done", None)
    opens = [entry for entry in recording.log if entry[0] == "open"]
    assert [read_only for _, read_only, _ in opens] == [False, False, False]  # claim, run, finish
    assert recording.log[1::2] == [("close", False, threading.current_thread().name)] * 3


def test_run_one_with_nothing_to_claim_returns_false(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    gate = Gate(blocking=False)
    recording = RecordingUnits(units)
    runner, _ = build(runners, recording, clock, job_type(gate))

    assert runner.run_one(Lane.BULK) is False
    assert runner.run_one(Lane.REALTIME) is False
    assert gate.jobs == []
    assert [entry[0] for entry in recording.log] == ["open", "close"] * 2


def test_the_handler_runs_inside_its_own_unit_of_work_and_calls_one_use_case(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    setup = Setup(clock)
    seen: list[bool] = []

    def handler(job: Job) -> None:
        seen.append(current().read_only)
        setup.set(Key.BULK_THREADS, 2)

    runner, queue = build(runners, units, clock, job_type(handler))
    created = enqueue(units, queue)

    assert runner.run_one(Lane.BULK) is True

    assert seen == [False]
    with units.open(read_only=True):
        assert setup_queries.get(Key.BULK_THREADS) == 2
    assert row(units, created.job_id).status == "done"


@pytest.mark.parametrize(
    ("error", "status", "last_error"),
    [
        (AdapterError(True, "rate_limited", "slow down"), "queued", "rate_limited: slow down"),
        (AdapterError(False, "bad_request", "refused"), "dead", "bad_request: refused"),
        (ValueError("x@y.com"), "dead", "ValueError: <email>"),
    ],
)
def test_a_raising_handler_fails_its_job(
    error: Exception,
    status: str,
    last_error: str,
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
) -> None:
    def handler(job: Job) -> None:
        raise error

    runner, queue = build(runners, units, clock, job_type(handler))
    created = enqueue(units, queue)

    assert runner.run_one(Lane.BULK) is True

    failed = row(units, created.job_id)
    assert (failed.status, failed.last_error, failed.lease_until) == (status, last_error, None)


def test_a_handler_that_returns_a_value_fails_its_job(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    runner, queue = build(runners, units, clock, job_type(lambda job: "done"))
    created = enqueue(units, queue)

    assert runner.run_one(Lane.BULK) is True

    failed = row(units, created.job_id)
    assert (failed.status, failed.last_error) == (
        "dead",
        "invalid_job: the handler returned a value",
    )


class Halt(BaseException):
    """Not an ``Exception``: it ends the lane thread."""


def test_a_base_exception_re_raises_and_drops_the_claim(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    def handler(job: Job) -> None:
        raise Halt

    runner, queue = build(runners, units, clock, job_type(handler))
    created = enqueue(units, queue)

    with pytest.raises(Halt):
        runner.run_one(Lane.BULK)

    assert row(units, created.job_id).status == "running"  # left for its lease to expire
    clock.advance(timedelta(hours=1))
    runner.keep_once()  # not held any more, so it is reaped
    assert row(units, created.job_id).status == "queued"


def test_a_failure_to_finish_is_logged_and_leaves_the_lease_to_expire(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, queue = build(runners, units, clock, job_type(Gate(blocking=False)))
    created = enqueue(units, queue)

    def locked(claim: Claim) -> bool:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(queue, "complete", locked)

    assert runner.run_one(Lane.BULK) is True

    [record] = records(caplog, "job_finish_failed")
    assert record.levelno == logging.ERROR and record.exc_info is not None
    assert (record.job_id, record.attempts) == (created.job_id, 1)  # type: ignore[attr-defined]
    assert row(units, created.job_id).status == "running"


def test_run_one_claims_nothing_once_the_runner_stops(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    gate = Gate(blocking=False)
    runner, queue = build(runners, units, clock, job_type(gate), **FAST)
    runner.start()
    runner.stop()
    created = enqueue(units, queue)

    assert runner.run_one(Lane.BULK) is False
    assert row(units, created.job_id).status == "queued"


# Starting and stopping --------------------------------------------------------------------------


@pytest.mark.parametrize(("stored", "bulk"), [(None, 1), ("2", 2), ("1", 1), ("7", 1)])
def test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper(
    stored: str | None,
    bulk: int,
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    if stored is not None:
        store_setting(db_engine, "bulk_threads", stored)  # "7" no longer passes the rule
    runner, _ = build(runners, units, clock, **FAST)

    runner.start()

    expected = sorted(
        [
            "nextron-job-realtime-1",
            "nextron-job-interactive-1",
            *[f"nextron-job-bulk-{n}" for n in range(1, bulk + 1)],
            "nextron-job-keeper",
        ]
    )
    assert job_threads() == expected
    assert all(t.daemon for t in threading.enumerate() if t.name.startswith("nextron-job-"))
    assert runner.alive()
    [started] = records(caplog, "job_runner_started")
    assert started.bulk_threads == bulk  # type: ignore[attr-defined]
    unreadable = records(caplog, "bulk_threads_unreadable")
    if stored == "7":
        [warning] = unreadable
        assert (warning.levelno, getattr(warning, "code")) == (logging.WARNING, "invalid_setting")
    else:
        assert unreadable == []

    runner.stop()

    assert job_threads() == []
    assert not runner.alive()
    [stopped] = records(caplog, "job_runner_stopped")
    assert stopped.released == 0  # type: ignore[attr-defined]


def test_an_unreadable_database_at_start_means_one_bulk_thread(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    runner, _ = build(runners, FailingUnits(units, None), clock, **FAST)

    runner.start()

    assert "nextron-job-bulk-1" in job_threads() and "nextron-job-bulk-2" not in job_threads()
    [warning] = records(caplog, "bulk_threads_unreadable")
    assert warning.code == "RuntimeError"  # type: ignore[attr-defined]
    runner.stop()


def test_start_reads_bulk_threads_in_a_read_only_unit_it_closes_before_any_thread_starts(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock, runners: list[Runner]
) -> None:
    store_setting(db_engine, "bulk_threads", "2")
    recording = RecordingUnits(units)
    runner, _ = build(runners, recording, clock, **FAST)
    me = threading.current_thread().name

    runner.start()
    runner.stop()

    assert recording.log[:2] == [("open", True, me), ("close", True, me)]
    assert all(name != me for _, _, name in recording.log[2:])


def test_a_second_start_raises_and_stop_is_idempotent(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    runner, _ = build(runners, units, clock, **FAST)
    runner.stop()  # before start: nothing
    assert not runner.alive()

    runner.start()
    with pytest.raises(RuntimeError, match="already been started"):
        runner.start()
    runner.stop()
    runner.stop()
    with pytest.raises(RuntimeError):
        runner.start()

    assert job_threads() == []


def test_lane_threads_run_jobs_off_the_event_loop_and_the_starting_thread(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    gate = Gate(blocking=False)
    runner, queue = build(runners, units, clock, job_type(gate), **FAST)
    starter: list[threading.Thread] = []

    def start() -> None:
        starter.append(threading.current_thread())
        runner.start()

    async def serve() -> threading.Thread:
        await asyncio.to_thread(start)
        loop_thread = threading.current_thread()
        await asyncio.to_thread(enqueue, units, queue)
        assert await asyncio.to_thread(wait_for, gate.left)
        await asyncio.to_thread(runner.stop)
        return loop_thread

    loop_thread = asyncio.run(serve())

    [handler_thread] = gate.threads
    assert handler_thread.name == "nextron-job-bulk-1"
    assert handler_thread not in (loop_thread, starter[0])
    assert gate.loops == [False]


def test_each_lane_runs_only_its_own_jobs(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    gates = {lane: Gate(blocking=False) for lane in Lane}
    types = [
        job_type(gates[lane], name=f"run_{lane}", lane=lane) for lane in Lane
    ]
    runner, queue = build(runners, units, clock, *types, **FAST)
    runner.start()
    for lane in Lane:
        enqueue(units, queue, f"run_{lane}")

    assert all(wait_for(gate.left) for gate in gates.values())
    runner.stop()

    for lane, gate in gates.items():
        assert [thread.name for thread in gate.threads] == [f"nextron-job-{lane}-1"]


def test_stop_releases_a_blocked_handlers_job_and_its_late_finish_is_a_lost_claim(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    gate = Gate()
    runner, queue = build(runners, units, clock, job_type(gate), **FAST)
    created = enqueue(units, queue)
    runner.start()
    try:
        assert wait_for(gate.entered)
        [lane_thread] = gate.threads

        runner.stop()  # the grace (0.05 s) ends while the handler still waits

        released = row(units, created.job_id)
        assert (released.status, released.attempts, released.lease_until) == ("queued", 1, None)
        [stopped] = records(caplog, "job_runner_stopped")
        assert stopped.released == 1  # type: ignore[attr-defined]
        assert len(records(caplog, "job_released")) == 1
        assert lane_thread.is_alive()
    finally:
        gate.open()
    assert wait_until(lambda: not lane_thread.is_alive())

    [lost] = records(caplog, "job_claim_lost")
    assert (lost.name, lost.job_id) == (QUEUE_LOGGER, created.job_id)  # type: ignore[attr-defined]
    assert row(units, created.job_id).status == "queued"  # the late finish changed nothing
    assert job_threads() == []


def test_stop_waits_for_a_handler_that_finishes_within_the_grace(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    gate = Gate()
    runner, queue = build(runners, units, clock, job_type(gate), **{**FAST, "shutdown_grace": 20})
    created = enqueue(units, queue)
    runner.start()
    assert wait_for(gate.entered)

    stopping = threading.Thread(target=runner.stop)
    stopping.start()
    gate.open()
    assert wait_until(lambda: not stopping.is_alive())

    assert row(units, created.job_id).status == "done"  # finished, not released


def test_alive_is_false_once_a_lane_thread_ends(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ended: list[str] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: ended.append(args.thread.name))

    def handler(job: Job) -> None:
        raise Halt

    runner, queue = build(runners, units, clock, job_type(handler), **FAST)
    runner.start()
    assert runner.alive()

    enqueue(units, queue)

    assert wait_until(lambda: not runner.alive())
    assert ended == ["nextron-job-bulk-1"]
    assert "nextron-job-realtime-1" in job_threads()
    runner.stop()
    assert not runner.alive()


def test_a_claim_error_is_logged_and_the_lane_goes_on(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = Gate(blocking=False)
    runner, queue = build(runners, FailingUnits(units, 2), clock, job_type(gate), **FAST)
    runner.start()

    assert wait_until(lambda: len(records(caplog, "job_claim_failed")) == 2)
    created = enqueue(units, queue)
    assert wait_for(gate.left)

    assert runner.alive()
    failed = records(caplog, "job_claim_failed")
    assert all(r.exc_info is not None and getattr(r, "lane") == Lane.BULK for r in failed)
    assert wait_until(lambda: row(units, created.job_id).status == "done")
    runner.stop()


# The keeper -------------------------------------------------------------------------------------


def run_in_background(runner: Runner) -> threading.Thread:
    """``run_one`` on its own thread, as a lane thread would."""
    thread = threading.Thread(
        target=runner.run_one, args=(Lane.BULK,), context=contextvars.Context()
    )
    thread.start()
    return thread


def test_the_keeper_renews_a_held_claim(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    gate = Gate()
    runner, queue = build(runners, units, clock, job_type(gate))
    created = enqueue(units, queue)
    worker = run_in_background(runner)
    try:
        assert wait_for(gate.entered)
        clock.advance(timedelta(hours=2))  # far past the first lease: still never reaped

        runner.keep_once()

        renewed = row(units, created.job_id)
        assert (renewed.status, renewed.lease_until) == (
            "running",
            clock.now() + timedelta(minutes=6),
        )
    finally:
        gate.open()
        worker.join(20)
    assert row(units, created.job_id).status == "done"


def test_the_keeper_reaps_a_lease_no_one_holds(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    runner, queue = build(runners, units, clock, job_type(Gate(blocking=False)))
    created = enqueue(units, queue)
    claim_directly(units, queue)
    clock.advance(timedelta(minutes=6))

    runner.keep_once()

    reaped = row(units, created.job_id)
    assert (reaped.status, reaped.attempts) == ("queued", 1)


def test_the_keeper_logs_a_lost_lease_once(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = Gate()
    runner, queue = build(runners, units, clock, job_type(gate))
    created = enqueue(units, queue)
    worker = run_in_background(runner)
    try:
        assert wait_for(gate.entered)
        [job] = gate.jobs
        with units.open():  # someone else took the job back
            queue.release([Claim(job, timedelta(minutes=5))])

        runner.keep_once()
        runner.keep_once()

        [lost] = records(caplog, "job_lease_lost")
        assert (lost.levelno, lost.job_id, lost.attempts) == (  # type: ignore[attr-defined]
            logging.WARNING,
            created.job_id,
            1,
        )
    finally:
        gate.open()
        worker.join(20)
    assert records(caplog, "job_claim_lost")


def test_the_keeper_logs_an_overrun_once_and_keeps_renewing(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    monotonic: Monotonic,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = Gate()
    runner, queue = build(
        runners,
        units,
        clock,
        job_type(gate, max_runtime=timedelta(seconds=60)),
        monotonic=monotonic,
    )
    created = enqueue(units, queue)
    worker = run_in_background(runner)
    try:
        assert wait_for(gate.entered)
        monotonic.now += 60
        runner.keep_once()
        assert records(caplog, "job_overran") == []

        monotonic.now += 1
        clock.advance(timedelta(seconds=61))
        runner.keep_once()
        runner.keep_once()

        [overran] = records(caplog, "job_overran")
        assert (overran.levelno, getattr(overran, "job_id")) == (logging.WARNING, created.job_id)
        assert row(units, created.job_id).lease_until == clock.now() + timedelta(seconds=120)
    finally:
        gate.open()
        worker.join(20)


def finish_old_done_jobs(
    units: UnitOfWorkFactory, queue: JobQueue, clock: FrozenClock, ages: tuple[int, int] = (40, 35)
) -> tuple[str, str]:
    """Two ``done`` jobs of one type, ``ages`` days old, the second the newer."""
    ids = []
    for discriminator, age in zip(("1", "2"), ages, strict=True):
        clock.set(T0 - timedelta(days=age))
        ids.append(enqueue(units, queue, discriminator=discriminator).job_id)
        with units.open():
            held = queue.claim(Lane.BULK)
        assert held is not None
        with units.open():
            queue.complete(held)
    clock.set(T0)
    return ids[0], ids[1]


def test_the_keeper_purges_on_its_first_tick_and_then_each_maintenance_interval(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    monotonic: Monotonic,
    runners: list[Runner],
    db_engine: Engine,
) -> None:
    runner, queue = build(
        runners,
        units,
        clock,
        job_type(Gate(blocking=False)),
        monotonic=monotonic,
        maintenance_interval=100.0,
    )
    oldest, newest = finish_old_done_jobs(units, queue, clock)

    runner.keep_once()

    with units.open(read_only=True):
        assert jobqueue_queries.job(oldest) is None
        assert jobqueue_queries.job(newest) is not None
    newer = finish_old_done_jobs(units, queue, clock, (45, 33))
    runner.keep_once()
    monotonic.now += 99
    runner.keep_once()
    with units.open(read_only=True):
        assert jobqueue_queries.job(newer[0]) is not None  # not purged: not due yet
    monotonic.now += 1
    runner.keep_once()
    with units.open(read_only=True):
        assert jobqueue_queries.job(newer[0]) is None
        assert jobqueue_queries.job(newest) is None
        assert jobqueue_queries.job(newer[1]) is not None


def test_maintain_once_purges_now(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    runner, queue = build(runners, units, clock, job_type(Gate(blocking=False)))
    oldest, _ = finish_old_done_jobs(units, queue, clock)

    assert runner.maintain_once() == 1
    assert runner.maintain_once() == 0
    with units.open(read_only=True):
        assert jobqueue_queries.job(oldest) is None


def test_a_keeper_failure_is_logged_and_never_raises(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner, _ = build(runners, FailingUnits(units, None), clock)

    runner.keep_once()

    failures = records(caplog, "job_keeper_failed")
    assert len(failures) == 2  # the renewal and the purge
    assert all(r.levelno == logging.ERROR and r.exc_info is not None for r in failures)


def test_the_keeper_thread_ticks_at_once(
    units: UnitOfWorkFactory, clock: FrozenClock, runners: list[Runner]
) -> None:
    runner, queue = build(runners, units, clock, job_type(Gate(blocking=False)), **FAST)
    created = enqueue(units, queue)
    claim_directly(units, queue)
    clock.advance(timedelta(minutes=6))

    runner.start()

    assert wait_until(lambda: row(units, created.job_id).attempts == 2)  # reaped, then run again
    assert wait_until(lambda: row(units, created.job_id).status == "done")
    runner.stop()


# Defaults, the finish, failures at stop and concurrency -----------------------------------------


def test_the_runner_timing_defaults() -> None:
    parameters = inspect.signature(Runner).parameters
    names = ("poll_interval", "renew_interval", "maintenance_interval", "shutdown_grace")

    assert {name: parameters[name].default for name in names} == {
        "poll_interval": 1.0,
        "renew_interval": RENEW_INTERVAL.total_seconds(),
        "maintenance_interval": 86_400.0,
        "shutdown_grace": 15.0,
    }
    assert RENEW_INTERVAL.total_seconds() == 30.0


def test_a_keeper_tick_right_after_the_finish_commits_logs_no_lost_lease(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, queue = build(runners, units, clock, job_type(Gate(blocking=False)))
    created = enqueue(units, queue)
    complete = queue.complete
    finished: list[bool] = []

    def complete_then_tick(claim: Claim) -> bool:
        done = complete(claim)  # committed: the job is done
        ticker = threading.Thread(target=runner.keep_once, context=contextvars.Context())
        ticker.start()
        assert wait_until(lambda: not ticker.is_alive())
        finished.append(done)
        return done

    monkeypatch.setattr(queue, "complete", complete_then_tick)

    assert runner.run_one(Lane.BULK) is True

    assert finished == [True]
    assert records(caplog, "job_lease_lost") == []
    assert row(units, created.job_id).status == "done"


def test_a_failed_purge_is_tried_again_on_the_next_tick(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    monotonic: Monotonic,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, queue = build(
        runners, units, clock, job_type(Gate(blocking=False)), monotonic=monotonic
    )
    oldest, _ = finish_old_done_jobs(units, queue, clock)
    purge = queue.purge_done
    calls: list[int] = []

    def locked_once() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return purge()

    monkeypatch.setattr(queue, "purge_done", locked_once)

    runner.keep_once()
    assert len(records(caplog, "job_keeper_failed")) == 1
    assert row(units, oldest).status == "done"

    runner.keep_once()  # the monotonic clock hasn't moved: tried again at once
    assert len(calls) == 2
    with units.open(read_only=True):
        assert jobqueue_queries.job(oldest) is None

    runner.keep_once()  # that purge succeeded, so the next one waits for the interval
    assert len(calls) == 2


def test_stop_logs_a_failed_release_and_still_returns(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO)
    gate = Gate()
    runner, queue = build(runners, units, clock, job_type(gate), **FAST)
    created = enqueue(units, queue)
    runner.start()
    try:
        assert wait_for(gate.entered)
        [lane_thread] = gate.threads

        def locked(held: object) -> tuple[str, ...]:
            raise RuntimeError("database is locked")

        monkeypatch.setattr(queue, "release", locked)

        runner.stop()

        [failed] = records(caplog, "job_finish_failed")
        assert failed.levelno == logging.ERROR and failed.exc_info is not None
        [stopped] = records(caplog, "job_runner_stopped")
        assert getattr(stopped, "released") == 0
        assert row(units, created.job_id).status == "running"
    finally:
        gate.open()
    assert wait_until(lambda: not lane_thread.is_alive())
    assert row(units, created.job_id).status == "done"  # still its own claim, so it finished


def test_the_shutdown_grace_is_one_deadline_for_every_lane(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    grace = 0.5
    gates = {Lane.INTERACTIVE: Gate(), Lane.BULK: Gate()}
    types = [job_type(gate, name=f"run_{lane}", lane=lane) for lane, gate in gates.items()]
    runner, queue = build(runners, units, clock, *types, **{**FAST, "shutdown_grace": grace})
    ids = [enqueue(units, queue, f"run_{lane}").job_id for lane in gates]
    runner.start()
    try:
        assert all(wait_for(gate.entered) for gate in gates.values())
        began = time.monotonic()
        runner.stop()
        elapsed = time.monotonic() - began
    finally:
        for gate in gates.values():
            gate.open()

    assert 0.9 * grace <= elapsed < 1.75 * grace  # a deadline per lane would take 2 x grace
    [stopped] = records(caplog, "job_runner_stopped")
    assert getattr(stopped, "released") == 2
    assert [row(units, job_id).status for job_id in ids] == ["queued", "queued"]
    for gate in gates.values():
        [thread] = gate.threads
        assert wait_until(lambda: not thread.is_alive())


def done_count(engine: Engine) -> int:
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        [(count,)] = reader.execute("SELECT count(*) FROM jobs WHERE status = 'done'").fetchall()
        return int(count)
    finally:
        reader.close()


def test_concurrent_lanes_never_run_a_job_or_a_concurrency_key_twice_at_once(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock, runners: list[Runner]
) -> None:
    store_setting(db_engine, "bulk_threads", "2")
    lock = threading.Lock()
    held_jobs: set[str] = set()
    held_keys: set[str] = set()
    clashes: list[str] = []
    ran: list[str] = []
    pause = threading.Event()  # never set: each run lasts 0.01 s, so the claims overlap

    def handler(job: Job) -> None:
        key = f"inbox:{job.payload['inbox']}"
        with lock:
            if job.id in held_jobs or key in held_keys:
                clashes.append(job.id)
            held_jobs.add(job.id)
            held_keys.add(key)
            ran.append(job.id)
        pause.wait(0.01)
        with lock:
            held_jobs.discard(job.id)
            held_keys.discard(key)

    shared = JobType(
        "sync_inbox",
        Lane.BULK,
        frozenset({Lane.BULK, Lane.INTERACTIVE}),
        50,
        timedelta(minutes=5),
        3,
        handler,
    )
    runner, queue = build(runners, units, clock, shared, **FAST)
    inboxes = ("01JD3QZ8X5V9K2M7N4P6R8T0W1", "01JD3QZ8X5V9K2M7N4P6R8T0W2")
    ids = [
        enqueue(
            units,
            queue,
            "sync_inbox",
            discriminator=str(n),
            payload={"v": 1, "inbox": inboxes[n % 2]},
            lane=Lane.INTERACTIVE if n % 3 == 0 else Lane.BULK,
            concurrency_key=f"inbox:{inboxes[n % 2]}",
        ).job_id
        for n in range(12)
    ]

    runner.start()
    assert "nextron-job-bulk-2" in job_threads()
    assert wait_until(lambda: done_count(db_engine) == len(ids))
    runner.stop()

    assert clashes == []
    assert sorted(ran) == sorted(ids)  # each job ran exactly once
