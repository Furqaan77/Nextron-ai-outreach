"""The heartbeat writer (``nextron/jobs/heartbeat.py``, AD-20, NFR-14).

With fakes: each reason to withhold the beat, in its order; the logs, written only when the
outcome changes; failures, which never raise; and the thread, which ticks at once, then every
``interval``, until ``stop``. With a real ``Runner`` on a temporary database: a dead lane thread
and a hung handler stop the beat. Waits are slices of at most 0.05 s, for at most 2 s.
"""

import contextvars
import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nextron.app.jobqueue.api import JobQueue
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.job import Job, JobType, JobTypes, Lane
from nextron.jobs import heartbeat as heartbeat_module
from nextron.jobs.heartbeat import HeartbeatWriter
from nextron.jobs.runner import Runner
from tests.fakes.clock import FrozenClock
from tests.fakes.units import FakeUnits

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
SLICE = 0.05
ATTEMPTS = 40  # 2 s
LOGGER = "nextron.jobs.heartbeat"
MARK: contextvars.ContextVar[str] = contextvars.ContextVar("test_heartbeat_mark", default="fresh")


def wait_until(condition: Callable[[], bool]) -> bool:
    pause = threading.Event()
    for _ in range(ATTEMPTS):
        if condition():
            return True
        pause.wait(SLICE)
    return condition()


class FakeRunner:
    def __init__(self) -> None:
        self.up = True
        self.overrun: tuple[object, ...] = ()
        self.calls: list[str] = []

    def alive(self) -> bool:
        self.calls.append("runner.alive")
        return self.up

    def overrunning(self) -> tuple[object, ...]:
        self.calls.append("runner.overrunning")
        return self.overrun


class FakeScheduler:
    def __init__(self, calls: list[str]) -> None:
        self.up = True
        self.calls = calls
        self.error: Exception | None = None

    def alive(self) -> bool:
        self.calls.append("scheduler.alive")
        if self.error is not None:
            raise self.error
        return self.up


class FakeHeartbeat:
    """Records each beat with its thread and the units open around it; raises ``error`` while
    it is set."""

    def __init__(self, units: FakeUnits) -> None:
        self.units = units
        self.error: Exception | None = None
        self.beats: list[tuple[str, int, str]] = []

    def beat(self) -> None:
        if self.error is not None:
            raise self.error
        thread = threading.current_thread()
        assert [unit.thread for unit in self.units.open_now] == [thread.ident]
        self.beats.append((thread.name, len(self.units.open_now), MARK.get()))


class Rig:
    """A writer on fakes, with the writer's own default interval unless one is given."""

    def __init__(self, interval: float | None = None) -> None:
        self.units = FakeUnits()
        self.runner = FakeRunner()
        self.scheduler = FakeScheduler(self.runner.calls)
        self.heartbeat = FakeHeartbeat(self.units)
        options = {} if interval is None else {"interval": interval}
        self.writer = HeartbeatWriter(
            self.units,
            self.heartbeat,  # type: ignore[arg-type]
            self.runner,
            self.scheduler,
            **options,
        )


@pytest.fixture(autouse=True)
def info_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Capture the writer's INFO records (``heartbeat_resumed``) too."""
    caplog.set_level(logging.INFO, logger=LOGGER)


@pytest.fixture
def rig() -> Iterator[Rig]:
    built = Rig()
    yield built
    built.writer.stop()


def logged(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, object]]:
    """The writer's records as ``(event, level, reason or code)``."""
    return [
        (r.getMessage(), r.levelno, getattr(r, "reason", getattr(r, "code", None)))
        for r in caplog.records
        if r.name == LOGGER
    ]


WITHHELD = logging.WARNING
FAILED = logging.ERROR
RESUMED = logging.INFO


# Withholding ------------------------------------------------------------------------------------


def test_a_healthy_tick_beats_in_its_own_unit_of_work_and_logs_nothing(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.writer.beat_once()

    assert rig.heartbeat.beats == [(threading.current_thread().name, 1, "fresh")]
    assert rig.units.opened == [(False, threading.get_ident())]
    assert rig.units.open_now == []
    assert rig.runner.calls == ["runner.alive", "runner.overrunning", "scheduler.alive"]
    assert logged(caplog) == []


@pytest.mark.parametrize(
    ("runner_up", "overrun", "scheduler_up", "reason", "checked"),
    [
        (False, ("claim",), False, "runner_down", ["runner.alive"]),
        (True, ("claim",), False, "job_overran", ["runner.alive", "runner.overrunning"]),
        (
            True,
            (),
            False,
            "scheduler_down",
            ["runner.alive", "runner.overrunning", "scheduler.alive"],
        ),
    ],
)
def test_the_first_reason_that_holds_withholds_the_beat(
    runner_up: bool,
    overrun: tuple[object, ...],
    scheduler_up: bool,
    reason: str,
    checked: list[str],
    rig: Rig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig.runner.up, rig.runner.overrun, rig.scheduler.up = runner_up, overrun, scheduler_up

    rig.writer.beat_once()

    assert rig.heartbeat.beats == []
    assert rig.units.opened == []
    assert rig.runner.calls == checked
    assert logged(caplog) == [("heartbeat_withheld", WITHHELD, reason)]


def test_withholding_logs_once_per_reason_and_resuming_logs_once(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.runner.up = False
    rig.writer.beat_once()
    rig.writer.beat_once()
    rig.runner.up, rig.runner.overrun = True, ("claim",)
    rig.writer.beat_once()
    rig.writer.beat_once()
    rig.runner.overrun = ()
    rig.writer.beat_once()
    rig.writer.beat_once()

    assert logged(caplog) == [
        ("heartbeat_withheld", WITHHELD, "runner_down"),
        ("heartbeat_withheld", WITHHELD, "job_overran"),
        ("heartbeat_resumed", RESUMED, None),
    ]
    assert len(rig.heartbeat.beats) == 2


# Failures ---------------------------------------------------------------------------------------


def test_a_failed_beat_is_logged_once_per_streak_and_never_raises(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.writer.beat_once()
    rig.heartbeat.error = OSError("database is locked")
    rig.writer.beat_once()
    rig.heartbeat.error = PermissionError("the file is held")
    rig.writer.beat_once()
    rig.heartbeat.error = None
    rig.writer.beat_once()
    rig.heartbeat.error = OSError("again")
    rig.writer.beat_once()

    assert logged(caplog) == [
        ("heartbeat_failed", FAILED, "OSError"),
        ("heartbeat_resumed", RESUMED, None),
        ("heartbeat_failed", FAILED, "OSError"),
    ]
    failed = [r for r in caplog.records if r.getMessage() == "heartbeat_failed"]
    assert all(r.exc_info is not None for r in failed)
    assert len(rig.heartbeat.beats) == 2
    assert rig.units.open_now == []


def test_a_check_that_raises_is_a_failure_too(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    rig.scheduler.error = RuntimeError("alive() would block the event loop")

    rig.writer.beat_once()

    assert logged(caplog) == [("heartbeat_failed", FAILED, "RuntimeError")]
    assert rig.heartbeat.beats == []


def test_a_failure_then_a_withheld_beat_logs_both_and_resumes_once(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.heartbeat.error = OSError("locked")
    rig.writer.beat_once()
    rig.heartbeat.error = None
    rig.scheduler.up = False
    rig.writer.beat_once()
    rig.scheduler.up = True
    rig.writer.beat_once()

    assert [event for event, _, _ in logged(caplog)] == [
        "heartbeat_failed",
        "heartbeat_withheld",
        "heartbeat_resumed",
    ]


# The thread -------------------------------------------------------------------------------------


def heartbeat_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "nextron-job-heartbeat"]


def test_start_ticks_at_once_then_the_thread_ticks_every_interval_until_stop() -> None:
    rig = Rig(interval=0.01)
    token = MARK.set("caller")
    try:
        rig.writer.start()

        assert rig.heartbeat.beats[0] == (threading.current_thread().name, 1, "caller")
        [thread] = heartbeat_threads()
        assert thread.daemon
        assert wait_until(lambda: len(rig.heartbeat.beats) >= 4)
        assert {name for name, _, _ in rig.heartbeat.beats[1:]} == {"nextron-job-heartbeat"}
        assert {mark for _, _, mark in rig.heartbeat.beats[1:]} == {"fresh"}  # a fresh context
    finally:
        rig.writer.stop()
        MARK.reset(token)

    assert heartbeat_threads() == []
    count = len(rig.heartbeat.beats)
    threading.Event().wait(SLICE)
    assert len(rig.heartbeat.beats) == count


def test_the_thread_waits_its_interval_between_ticks(rig: Rig) -> None:
    rig.writer.start()

    threading.Event().wait(SLICE)

    assert len(rig.heartbeat.beats) == 1  # the 30 s interval hasn't passed
    assert len(heartbeat_threads()) == 1


def test_by_default_the_thread_ticks_every_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main.build_app`` passes no interval; the supervisor restarts at 300 s, so the default
    must stay well under it."""
    timeouts: list[float | None] = []

    class RecordingEvent(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            timeouts.append(timeout)
            return super().wait(timeout)

    monkeypatch.setattr(
        heartbeat_module,
        "threading",
        SimpleNamespace(Event=RecordingEvent, Lock=threading.Lock, Thread=threading.Thread),
    )
    rig = Rig()  # no interval: the writer's default
    try:
        rig.writer.start()
        assert wait_until(lambda: len(timeouts) == 1)  # the thread waits out its interval
    finally:
        rig.writer.stop()

    assert timeouts == [30.0]
    assert len(rig.heartbeat.beats) == 1


def test_a_second_start_raises_and_stop_is_idempotent(rig: Rig) -> None:
    rig.writer.stop()  # before start: nothing
    rig.writer.start()

    with pytest.raises(RuntimeError, match="already been started"):
        rig.writer.start()

    rig.writer.stop()
    rig.writer.stop()
    assert heartbeat_threads() == []
    assert len(rig.heartbeat.beats) == 1


def test_start_never_raises_when_its_first_tick_fails(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.heartbeat.error = OSError("locked")

    rig.writer.start()

    assert logged(caplog) == [("heartbeat_failed", FAILED, "OSError")]
    assert len(heartbeat_threads()) == 1


# With a real runner -----------------------------------------------------------------------------


class Halt(BaseException):
    """Ends the lane thread that raises it."""


class Gate:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.proceed = threading.Event()

    def __call__(self, job: Job) -> None:
        self.entered.set()
        for _ in range(400):  # at most 20 s, in slices
            if self.proceed.wait(SLICE):
                return


class Monotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


FAST = {"poll_interval": 0.01, "renew_interval": 0.02, "shutdown_grace": SLICE}


@pytest.fixture
def runners() -> Iterator[list[Runner]]:
    built: list[Runner] = []
    yield built
    for runner in built:
        runner.stop()


def real_runner(
    runners: list[Runner],
    units: UnitOfWorkFactory,
    handler: Callable[[Job], None],
    **options: object,
) -> tuple[Runner, JobQueue]:
    types = JobTypes()
    minute = timedelta(seconds=60)
    types.register_job_type(
        JobType("enrich_lead", Lane.BULK, frozenset({Lane.BULK}), 50, minute, 0, handler)
    )
    types.freeze()
    queue = JobQueue(FrozenClock(T0), types)
    runner = Runner(units, queue, types, **FAST, **options)  # type: ignore[arg-type]
    runners.append(runner)
    return runner, queue


def enqueue(units: UnitOfWorkFactory, queue: JobQueue) -> None:
    @use_case
    def caller() -> None:
        with current().transaction():
            queue.enqueue(
                "enrich_lead", scope=LEAD, discriminator="1", payload={"v": 1, "lead_id": LEAD}
            )

    with units.open():
        caller()


def writer_for(runner: Runner) -> tuple[HeartbeatWriter, FakeHeartbeat]:
    fake_units = FakeUnits()
    heartbeat = FakeHeartbeat(fake_units)
    writer = HeartbeatWriter(
        fake_units,
        heartbeat,  # type: ignore[arg-type]
        runner,
        FakeScheduler([]),
    )
    return writer, heartbeat


def test_a_dead_lane_thread_stops_the_beat(
    units: UnitOfWorkFactory,
    runners: list[Runner],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threading, "excepthook", lambda args: None)

    def handler(job: Job) -> None:
        raise Halt

    runner, queue = real_runner(runners, units, handler)
    runner.start()
    writer, heartbeat = writer_for(runner)
    writer.beat_once()
    assert len(heartbeat.beats) == 1

    enqueue(units, queue)
    assert wait_until(lambda: not runner.alive())
    writer.beat_once()
    writer.beat_once()

    assert len(heartbeat.beats) == 1
    assert logged(caplog) == [("heartbeat_withheld", WITHHELD, "runner_down")]


def test_a_hung_handler_stops_the_beat_until_it_returns(
    units: UnitOfWorkFactory, runners: list[Runner], caplog: pytest.LogCaptureFixture
) -> None:
    gate = Gate()
    monotonic = Monotonic()
    runner, queue = real_runner(runners, units, gate, monotonic=monotonic)
    runner.start()
    writer, heartbeat = writer_for(runner)
    enqueue(units, queue)
    try:
        assert gate.entered.wait(2)
        writer.beat_once()  # running, within its max_runtime
        monotonic.now += 61  # past its 60 s max_runtime
        writer.beat_once()
        writer.beat_once()
        assert len(heartbeat.beats) == 1
        assert runner.alive()
    finally:
        gate.proceed.set()

    assert wait_until(lambda: runner.overrunning() == ())
    writer.beat_once()

    assert len(heartbeat.beats) == 2
    assert logged(caplog) == [
        ("heartbeat_withheld", WITHHELD, "job_overran"),
        ("heartbeat_resumed", RESUMED, None),
    ]
