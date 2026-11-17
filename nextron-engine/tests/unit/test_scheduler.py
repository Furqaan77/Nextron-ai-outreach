"""The periodic triggers (``nextron/jobs/scheduler.py``, AD-10, NFR-14) on a real
``AsyncIOScheduler``.

APScheduler's clock is a ``FrozenClock``: its time sources (the ``datetime.now`` calls in
``schedulers/base.py``, ``executors/base.py`` and ``triggers/interval.py``, and the scheduler
module's own ``next_run_time`` for ``at_startup``) are patched with a ``datetime`` subclass that
reads it. A test simulates the PC sleeping by moving that clock forward and calling
``wakeup()``, as APScheduler's own timer does when the PC wakes. A ``DailyAt`` trigger (Story
1.16) is a ``CronTrigger``, which takes the time APScheduler passes it.

``fire`` runs on the loop's executor threads. A firing holds its ``max_instances=1`` slot until
its future's callback runs on the loop, which then dispatches ``EVENT_JOB_EXECUTED``; the tests
wait for that event (``Captured.finished``) before the next wakeup, so a firing is never
skipped because the previous one hadn't let go. Waits are slices of at most 0.05 s, for at most
2 s.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobEvent
from apscheduler.executors import base as aps_executors
from apscheduler.schedulers import base as aps_base
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers import interval as aps_interval
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine

from nextron.app.jobqueue.api import Enqueued, JobQueue
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.job import Job, JobType, JobTypes, Lane, UnknownJobType
from nextron.jobs import scheduler as scheduler_module
from nextron.jobs.scheduler import Scheduler
from nextron.jobs.triggers import DailyAt, DailyRun, Every, Trigger, Triggers
from tests.fakes.clock import FrozenClock
from tests.fakes.units import FakeUnits

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
SLICE = 0.05  # the longest single wait in this module
ATTEMPTS = 40  # slices before a condition counts as never reached (2 s)
LOGGER = "nextron.jobs.scheduler"
APS_MODULES = (aps_base, aps_executors, aps_interval, scheduler_module)


# APScheduler's clock and instances --------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FrozenClock:
    """The ``FrozenClock`` APScheduler (and the scheduler module) reads as ``datetime.now``."""
    frozen = FrozenClock(T0)

    class ClockDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
            moment = frozen.now()
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in APS_MODULES:
        monkeypatch.setattr(module, "datetime", ClockDatetime)
    return frozen


class Captured:
    """The ``AsyncIOScheduler`` the scheduler module created, and every finished run of one of
    its jobs (``EVENT_JOB_EXECUTED`` or ``EVENT_JOB_ERROR``, dispatched once the run's
    ``max_instances`` slot is free again)."""

    def __init__(self) -> None:
        self.schedulers: list[AsyncIOScheduler] = []
        self.finished: list[JobEvent] = []

    @property
    def only(self) -> AsyncIOScheduler:
        [scheduler] = self.schedulers
        return scheduler


@pytest.fixture
def aps(monkeypatch: pytest.MonkeyPatch) -> Captured:
    captured = Captured()

    class Capturing(AsyncIOScheduler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            captured.schedulers.append(self)
            self.add_listener(captured.finished.append, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", Capturing)
    return captured


@pytest.fixture(autouse=True)
def no_leftover_threads() -> Iterator[None]:
    """``asyncio.run`` waits for the loop's executor threads, so none outlives its test."""
    before = {thread.name for thread in threading.enumerate()}
    yield
    assert {thread.name for thread in threading.enumerate()} <= before


# Fakes and helpers ------------------------------------------------------------------------------


class RecordingQueue:
    """Stands in for ``JobQueue``: records each ``enqueue_tick`` with its thread, raises for the
    first ``failures`` calls, and hands the rest to ``inner`` when there is one."""

    def __init__(self, inner: JobQueue | None = None, *, failures: int = 0) -> None:
        self.inner = inner
        self.failures = failures
        self.calls: list[tuple[str, threading.Thread]] = []
        self.results: list[Enqueued] = []

    def enqueue_tick(self, job_type: str) -> Enqueued:
        self.calls.append((job_type, threading.current_thread()))
        if self.failures:
            self.failures -= 1
            raise UnknownJobType(job_type)
        result = (
            Enqueued(f"job-{len(self.calls)}", True)
            if self.inner is None
            else self.inner.enqueue_tick(job_type)
        )
        self.results.append(result)
        return result

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def noop(job: Job) -> None:
    del job


def job_types(*names: str, handler: Callable[[Job], None] = noop) -> JobTypes:
    registry = JobTypes()
    for name in names:
        registry.register_job_type(
            JobType(name, Lane.REALTIME, frozenset({Lane.REALTIME}), 50, MINUTE, 0, handler)
        )
    registry.freeze()
    return registry


def triggers(*registered: tuple[Trigger, Every]) -> Triggers:
    registry = Triggers(job_types(*(trigger.value for trigger, _ in registered)))
    for trigger, schedule in registered:
        registry.register_trigger(trigger, schedule)
    registry.freeze()
    return registry


def build(
    queue: Any, *registered: tuple[Trigger, Every], units: Any = None, **options: Any
) -> Scheduler:
    return Scheduler(
        FakeUnits() if units is None else units, queue, triggers(*registered), **options
    )


async def until(condition: Callable[[], bool]) -> bool:
    """Whether ``condition`` becomes true, checked every 0.05 s for at most 2 s, with the loop
    free meanwhile."""
    for _ in range(ATTEMPTS):
        if condition():
            return True
        await asyncio.sleep(SLICE)
    return condition()


async def settle() -> None:
    """Give a firing that should not happen time to show up."""
    for _ in range(2):
        await asyncio.sleep(SLICE)


async def fired(aps: Captured, count: int) -> bool:
    """Whether ``count`` runs have finished and freed their slots."""
    return await until(lambda: len(aps.finished) == count)


def run[R](scenario: Callable[[], Coroutine[Any, Any, R]]) -> R:
    return asyncio.run(scenario())


def records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == event]


SEND = (Trigger.SEND_DUE_MESSAGES, Every(MINUTE))
RECONCILE_AT_STARTUP = (Trigger.RECONCILE_SENDS, Every(2 * MINUTE, at_startup=True))


# Construction -----------------------------------------------------------------------------------


@pytest.mark.parametrize("registry", [None, object(), job_types("send_due_messages")])
def test_the_scheduler_needs_a_trigger_registry(registry: object) -> None:
    with pytest.raises(TypeError, match="Triggers"):
        Scheduler(FakeUnits(), RecordingQueue(), registry)  # type: ignore[arg-type]


def test_the_scheduler_needs_a_frozen_trigger_registry() -> None:
    with pytest.raises(RuntimeError, match="freeze the trigger registry"):
        Scheduler(FakeUnits(), RecordingQueue(), Triggers(job_types()))  # type: ignore[arg-type]


def test_start_runs_only_on_the_loop_and_only_once(clock: FrozenClock) -> None:
    scheduler = build(RecordingQueue(), SEND)
    with pytest.raises(RuntimeError):
        scheduler.start()  # no running loop

    async def scenario() -> None:
        scheduler.start()
        try:
            with pytest.raises(RuntimeError, match="already been started"):
                scheduler.start()
        finally:
            await scheduler.stop()

    run(scenario)


# Start ------------------------------------------------------------------------------------------


def test_start_adds_one_interval_job_per_trigger_with_the_specified_settings(
    clock: FrozenClock, aps: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    scheduler = build(RecordingQueue(), SEND, RECONCILE_AT_STARTUP)

    async def scenario() -> None:
        scheduler.start()
        try:
            created = aps.only
            assert created.running
            assert created.timezone.utcoffset(None) == timedelta(0)
            jobs = {job.id: job for job in created.get_jobs()}
            assert set(jobs) == {"send_due_messages", "reconcile_sends"}
            for trigger, schedule in (SEND, RECONCILE_AT_STARTUP):
                job = jobs[trigger.value]
                assert job.func == scheduler.fire
                assert job.args == (trigger,)
                assert isinstance(job.trigger, IntervalTrigger)
                assert job.trigger.interval == schedule.period
                assert job.trigger.timezone.utcoffset(None) == timedelta(0)
                assert job.coalesce is True
                assert job.misfire_grace_time == schedule.period.total_seconds()
                assert job.max_instances == 1
            # read before the loop turns: only the at_startup trigger is due now
            assert jobs["send_due_messages"].next_run_time == T0 + MINUTE
            assert jobs["reconcile_sends"].next_run_time == T0
        finally:
            await scheduler.stop()

    run(scenario)

    [started] = records(caplog, "scheduler_started")
    assert (started.levelno, getattr(started, "triggers")) == (logging.INFO, 2)


def test_a_scheduler_with_no_triggers_starts_and_stops(
    clock: FrozenClock, aps: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    scheduler = Scheduler(FakeUnits(), RecordingQueue(), triggers())  # type: ignore[arg-type]

    async def scenario() -> None:
        scheduler.start()
        assert aps.only.get_jobs() == []
        await scheduler.stop()

    run(scenario)

    [started] = records(caplog, "scheduler_started")
    assert getattr(started, "triggers") == 0
    assert len(records(caplog, "scheduler_stopped")) == 1


# Firing -----------------------------------------------------------------------------------------


def test_after_a_sleep_a_trigger_fires_once_and_next_one_period_later(
    clock: FrozenClock, aps: Captured
) -> None:
    queue = RecordingQueue()
    scheduler = build(queue, SEND)

    async def scenario() -> None:
        scheduler.start()
        try:
            await settle()
            assert queue.calls == []  # not an at_startup trigger

            clock.advance(timedelta(hours=5))  # the PC slept through 300 firings
            await scheduler.wakeup()
            assert await fired(aps, 1)
            await settle()
            assert queue.names() == ["send_due_messages"]

            clock.advance(timedelta(seconds=59))
            await scheduler.wakeup()
            await settle()
            assert len(queue.calls) == 1

            clock.advance(timedelta(seconds=1))  # one period after the catch-up
            await scheduler.wakeup()
            assert await fired(aps, 2)
            assert len(queue.calls) == 2
        finally:
            await scheduler.stop()

    run(scenario)


def test_without_coalescing_the_same_sleep_would_fire_once_per_missed_period(
    clock: FrozenClock, aps: Captured
) -> None:
    queue = RecordingQueue()
    scheduler = build(queue, SEND)

    async def scenario() -> None:
        scheduler.start()
        try:
            aps.only.modify_job("send_due_messages", coalesce=False, misfire_grace_time=None)
            clock.advance(timedelta(hours=5))
            await scheduler.wakeup()
            assert await fired(aps, 300)
        finally:
            await scheduler.stop()

    run(scenario)

    assert len(queue.calls) == 300


def test_an_at_startup_trigger_fires_right_after_start_then_every_period(
    clock: FrozenClock, aps: Captured
) -> None:
    queue = RecordingQueue()
    scheduler = build(queue, RECONCILE_AT_STARTUP)

    async def scenario() -> None:
        scheduler.start()
        try:
            assert await fired(aps, 1)
            clock.advance(MINUTE)
            await scheduler.wakeup()
            await settle()
            assert len(queue.calls) == 1

            clock.advance(MINUTE)
            await scheduler.wakeup()
            assert await fired(aps, 2)

            clock.advance(2 * MINUTE)
            await scheduler.wakeup()
            assert await fired(aps, 3)
        finally:
            await scheduler.stop()

    run(scenario)

    assert queue.names() == ["reconcile_sends"] * 3


def test_fire_runs_off_the_loops_thread_in_its_own_unit_of_work(
    clock: FrozenClock, aps: Captured
) -> None:
    queue = RecordingQueue()
    units = FakeUnits()
    scheduler = build(queue, RECONCILE_AT_STARTUP, units=units)

    async def scenario() -> threading.Thread:
        scheduler.start()
        try:
            assert await fired(aps, 1)
        finally:
            await scheduler.stop()
        return threading.current_thread()

    loop_thread = run(scenario)

    [(_, thread)] = queue.calls
    assert thread is not loop_thread
    assert units.opened == [(False, thread.ident)]
    assert units.open_now == []


def test_a_failed_firing_is_logged_and_later_firings_still_run(
    clock: FrozenClock, aps: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    queue = RecordingQueue(failures=1)
    scheduler = build(queue, SEND)

    async def scenario() -> None:
        scheduler.start()
        try:
            clock.advance(MINUTE)
            await scheduler.wakeup()
            assert await fired(aps, 1)
            clock.advance(MINUTE)
            await scheduler.wakeup()
            assert await fired(aps, 2)
        finally:
            await scheduler.stop()

    run(scenario)

    [failed] = records(caplog, "trigger_failed")
    assert failed.levelno == logging.ERROR
    assert getattr(failed, "trigger") == Trigger.SEND_DUE_MESSAGES
    assert getattr(failed, "code") == "unknown_job_type"
    assert failed.exc_info is not None
    assert queue.names() == ["send_due_messages", "send_due_messages"]
    assert len(queue.results) == 1
    assert {event.code for event in aps.finished} == {EVENT_JOB_EXECUTED}  # never re-raised


def test_fire_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    queue = RecordingQueue(failures=1)
    scheduler = build(queue, SEND)

    scheduler.fire(Trigger.SEND_DUE_MESSAGES)

    assert queue.names() == ["send_due_messages"]
    assert len(records(caplog, "trigger_failed")) == 1


# Firing with the real queue ---------------------------------------------------------------------


def job_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("SELECT count(*) FROM jobs").scalar_one())


def test_a_firing_enqueues_one_global_tick_job_and_a_live_job_absorbs_the_next(
    clock: FrozenClock, aps: Captured, units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    ran: list[Job] = []
    types = job_types("send_due_messages", handler=ran.append)
    queue = RecordingQueue(JobQueue(clock, types))
    registry = Triggers(types)
    registry.register_trigger(*SEND)
    registry.freeze()
    scheduler = Scheduler(units, queue, registry)  # type: ignore[arg-type]

    async def scenario() -> None:
        scheduler.start()
        try:
            clock.advance(MINUTE)
            await scheduler.wakeup()
            assert await fired(aps, 1)
            clock.advance(MINUTE)
            await scheduler.wakeup()
            assert await fired(aps, 2)
        finally:
            await scheduler.stop()

    run(scenario)

    first, second = queue.results
    assert first.created is True
    assert second == Enqueued(first.job_id, False)
    assert job_count(db_engine) == 1
    with units.open(read_only=True):
        row = jobqueue_queries.job(first.job_id)
    assert row is not None
    assert (row.type, row.idempotency_key, row.payload, row.status, row.lane) == (
        "send_due_messages",
        "send_due_messages:global:tick",
        '{"v":1}',
        "queued",
        "realtime",
    )
    assert (row.run_after, row.attempts) == (T0 + MINUTE, 0)
    assert ran == []  # a firing only enqueues


# alive ------------------------------------------------------------------------------------------


def test_alive_before_start_is_false() -> None:
    assert build(RecordingQueue(), SEND).alive() is False


def test_alive_while_running_and_not_after_stop(clock: FrozenClock) -> None:
    scheduler = build(RecordingQueue(), SEND, probe_timeout=2.0)

    async def scenario() -> tuple[bool, bool]:
        scheduler.start()
        try:
            running = await asyncio.to_thread(scheduler.alive)
        finally:
            await scheduler.stop()
        stopped = await asyncio.to_thread(scheduler.alive)
        return running, stopped

    assert run(scenario) == (True, False)


def test_alive_on_the_loops_thread_raises(clock: FrozenClock) -> None:
    scheduler = build(RecordingQueue(), SEND)

    async def scenario() -> None:
        scheduler.start()
        try:
            with pytest.raises(RuntimeError, match="off the loop"):
                scheduler.alive()
        finally:
            await scheduler.stop()

    run(scenario)


def test_alive_is_false_while_the_loop_is_blocked(clock: FrozenClock) -> None:
    scheduler = build(RecordingQueue(), SEND, probe_timeout=SLICE)
    answers: list[bool] = []
    answered = threading.Event()

    def probe() -> None:
        answers.append(scheduler.alive())
        answered.set()

    async def scenario() -> None:
        scheduler.start()
        try:
            prober = threading.Thread(target=probe)
            prober.start()
            for _ in range(ATTEMPTS):  # block the loop's thread until the probe gives up
                if answered.wait(SLICE):
                    break
            prober.join(2)
        finally:
            await scheduler.stop()

    run(scenario)

    assert answers == [False]


def test_alive_is_false_once_apscheduler_stops(clock: FrozenClock, aps: Captured) -> None:
    scheduler = build(RecordingQueue(), SEND, probe_timeout=2.0)

    async def scenario() -> bool:
        scheduler.start()
        try:
            aps.only.shutdown(wait=False)
            await asyncio.sleep(0)
            return await asyncio.to_thread(scheduler.alive)
        finally:
            await scheduler.stop()

    assert run(scenario) is False


# stop and wakeup --------------------------------------------------------------------------------


def test_stop_is_idempotent_and_does_nothing_before_start(
    clock: FrozenClock, aps: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    scheduler = build(RecordingQueue(), SEND)

    async def scenario() -> None:
        await scheduler.stop()  # before start: nothing
        scheduler.start()
        await scheduler.stop()
        assert aps.only.running is False
        await scheduler.stop()

    run(scenario)

    assert len(records(caplog, "scheduler_stopped")) == 1


def test_after_stop_nothing_fires(clock: FrozenClock) -> None:
    queue = RecordingQueue()
    scheduler = build(queue, SEND)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="not running"):
            await scheduler.wakeup()  # before start
        scheduler.start()
        await scheduler.stop()
        clock.advance(timedelta(hours=1))
        with pytest.raises(RuntimeError, match="not running"):
            await scheduler.wakeup()
        await settle()

    run(scenario)

    assert queue.calls == []


# Daily schedules (Story 1.16) -------------------------------------------------------------------


def daily(
    when: Callable[[], object], *, fallback: DailyRun | None = None
) -> tuple[Trigger, DailyAt]:
    chosen = fallback if fallback is not None else DailyRun(time(10, 0), "UTC")
    return (Trigger.BACKUP_DB, DailyAt(when, fallback=chosen))  # type: ignore[arg-type]


def test_a_daily_trigger_is_a_cron_job_at_its_time_in_its_timezone(
    clock: FrozenClock, aps: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    calls: list[str] = []

    def when() -> DailyRun:
        calls.append("read")
        return DailyRun(time(2, 30), "America/Los_Angeles")

    scheduler = build(RecordingQueue(), daily(when), SEND)
    assert calls == []  # building reads nothing

    async def scenario() -> None:
        scheduler.start()
        try:
            job = aps.only.get_job("backup_db")
            assert job is not None
            assert job.func == scheduler.fire
            assert job.args == (Trigger.BACKUP_DB,)
            assert isinstance(job.trigger, CronTrigger)
            fields = {field.name: str(field) for field in job.trigger.fields}
            assert (fields["hour"], fields["minute"], fields["second"]) == ("2", "30", "0")
            assert job.trigger.timezone == ZoneInfo("America/Los_Angeles")
            assert job.coalesce is True
            assert job.misfire_grace_time == 86400
            assert job.max_instances == 1
            # T0 is 01:00 PST: the next run is 02:30 PST today, 10:30 UTC
            assert job.next_run_time == datetime(2026, 11, 17, 10, 30, tzinfo=UTC)
        finally:
            await scheduler.stop()

    run(scenario)

    assert calls == ["read"]  # once, when the scheduler started
    [scheduled] = records(caplog, "daily_trigger_scheduled")
    assert scheduled.levelno == logging.INFO
    assert (
        getattr(scheduled, "trigger"),
        getattr(scheduled, "at"),
        getattr(scheduled, "timezone"),
        getattr(scheduled, "catch_up"),
    ) == (Trigger.BACKUP_DB, "02:30", "America/Los_Angeles", False)
    [started] = records(caplog, "scheduler_started")
    assert getattr(started, "triggers") == 2
    assert records(caplog, "trigger_schedule_failed") == []


def test_a_daily_trigger_fires_at_its_time_and_once_after_a_sleep(
    clock: FrozenClock, aps: Captured
) -> None:
    queue = RecordingQueue()
    scheduler = build(queue, daily(lambda: DailyRun(time(10, 0), "UTC")))

    async def scenario() -> None:
        scheduler.start()
        try:
            await settle()
            assert queue.calls == []  # no catch-up: 10:00 is still to come

            clock.advance(timedelta(hours=1))  # 10:00 UTC
            await scheduler.wakeup()
            assert await fired(aps, 1)

            clock.advance(timedelta(days=2, hours=3))  # slept through two daily times
            await scheduler.wakeup()
            assert await fired(aps, 2)
            await settle()
            assert len(queue.calls) == 2  # the missed times coalesce into one firing
            job = aps.only.get_job("backup_db")
            assert job is not None
            assert job.next_run_time == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
        finally:
            await scheduler.stop()

    run(scenario)

    assert queue.names() == ["backup_db", "backup_db"]


def test_a_daily_trigger_with_catch_up_fires_right_after_start(
    clock: FrozenClock, aps: Captured, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    queue = RecordingQueue()
    scheduler = build(queue, daily(lambda: DailyRun(time(10, 0), "UTC", catch_up=True)))

    async def scenario() -> None:
        scheduler.start()
        try:
            job = aps.only.get_job("backup_db")
            assert job is not None
            assert job.next_run_time == T0  # now, read before the loop turns
            assert await fired(aps, 1)
            assert queue.names() == ["backup_db"]
            next_job = aps.only.get_job("backup_db")
            assert next_job is not None
            assert next_job.next_run_time == datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
        finally:
            await scheduler.stop()

    run(scenario)

    [scheduled] = records(caplog, "daily_trigger_scheduled")
    assert getattr(scheduled, "catch_up") is True


def _raises() -> DailyRun:
    raise RuntimeError("the database is locked")


@pytest.mark.parametrize(
    ("when", "code"),
    [(_raises, "RuntimeError"), (lambda: "10:00", "TypeError"), (lambda: None, "TypeError")],
)
def test_an_unreadable_daily_time_falls_back_and_is_logged(
    when: Callable[[], object],
    code: str,
    clock: FrozenClock,
    aps: Captured,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    scheduler = build(RecordingQueue(), daily(when))

    async def scenario() -> None:
        scheduler.start()
        try:
            job = aps.only.get_job("backup_db")
            assert job is not None
            fields = {field.name: str(field) for field in job.trigger.fields}
            assert (fields["hour"], fields["minute"]) == ("10", "0")
            assert job.trigger.timezone == ZoneInfo("UTC")
            assert job.misfire_grace_time == 86400
            assert job.next_run_time == datetime(2026, 11, 17, 10, 0, tzinfo=UTC)  # no catch-up
        finally:
            await scheduler.stop()

    run(scenario)

    [failed] = records(caplog, "trigger_schedule_failed")
    assert failed.levelno == logging.WARNING
    assert (getattr(failed, "trigger"), getattr(failed, "code")) == (Trigger.BACKUP_DB, code)
    [scheduled] = records(caplog, "daily_trigger_scheduled")
    assert (getattr(scheduled, "at"), getattr(scheduled, "timezone")) == ("10:00", "UTC")


def test_the_fallback_is_whatever_the_registration_names(
    clock: FrozenClock, aps: Captured
) -> None:
    def broken() -> DailyRun:
        raise ValueError("no schedule")

    fallback = DailyRun(time(23, 45), "Europe/London")
    scheduler = build(RecordingQueue(), daily(broken, fallback=fallback))

    async def scenario() -> None:
        scheduler.start()
        try:
            job = aps.only.get_job("backup_db")
            assert job is not None
            assert job.trigger.timezone == ZoneInfo("Europe/London")
            assert job.next_run_time == datetime(2026, 11, 17, 23, 45, tzinfo=UTC)  # GMT
        finally:
            await scheduler.stop()

    run(scenario)
