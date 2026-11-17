"""The engine's lifespan (``nextron.main.build_app`` and ``engine_lifespan``, AD-2, AD-10,
AD-20).

With recording fakes: startup runs the runner (off the loop), the scheduler (on the loop's
thread) and the heartbeat writer (off the loop) in that order, shutdown stops them in reverse,
and each part that started is stopped after a later start, a stop or the server failed. On a
real database at head, driven through the ASGI lifespan by hand: startup starts the named
threads, beats once (the ``runner_heartbeat`` row and ``<data dir>\\heartbeat``), shutdown
stops them, and building the app starts none.
"""

import asyncio
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI

from nextron import main
from nextron.app.queries import ops as ops_queries
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
EXPECTED_THREADS = [
    "nextron-job-bulk-1",
    "nextron-job-heartbeat",
    "nextron-job-interactive-1",
    "nextron-job-keeper",
    "nextron-job-realtime-1",
]
LIFESPAN_LOGGERS = ("nextron.jobs.runner", "nextron.jobs.scheduler", "nextron.jobs.heartbeat")


def job_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("nextron-job-"))


def build(units: UnitOfWorkFactory) -> FastAPI:
    return main.build_app(
        load_bootstrap(), units=units, secrets=InMemorySecrets("test"), clock=FrozenClock(T)
    )


def drive_lifespan(app: FastAPI) -> tuple[list[str], list[str]]:
    """Run the app's lifespan startup and shutdown; return the messages it sent and the job
    threads alive once startup completed."""
    sent: list[str] = []
    during: list[str] = []

    async def run() -> None:
        incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await incoming.put({"type": "lifespan.startup"})

        async def receive() -> dict[str, Any]:
            return await incoming.get()

        async def send(message: dict[str, Any]) -> None:
            sent.append(message["type"])
            if message["type"] == "lifespan.startup.complete":
                during.extend(job_threads())
                await incoming.put({"type": "lifespan.shutdown"})

        await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)

    asyncio.run(run())
    return sent, during


# The real app ------------------------------------------------------------------------------------


def test_building_the_app_starts_no_thread(units: UnitOfWorkFactory) -> None:
    before = set(threading.enumerate())

    build(units)

    assert job_threads() == []
    assert set(threading.enumerate()) == before
    assert not (load_bootstrap().data_dir / main.HEARTBEAT_FILE_NAME).exists()


def test_the_lifespan_starts_the_runner_scheduler_and_heartbeat_and_shutdown_stops_them(
    units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    data_dir = load_bootstrap().data_dir
    data_dir.mkdir(parents=True)
    app = build(units)

    sent, during = drive_lifespan(app)

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert during == EXPECTED_THREADS
    assert job_threads() == []
    events = [r.getMessage() for r in caplog.records if r.name in LIFESPAN_LOGGERS]
    assert events == [
        "job_runner_started",
        "scheduler_started",
        "scheduler_stopped",
        "job_runner_stopped",
    ]
    [started] = [r for r in caplog.records if r.getMessage() == "job_runner_started"]
    assert started.bulk_threads == 1  # type: ignore[attr-defined]
    [scheduled] = [r for r in caplog.records if r.getMessage() == "scheduler_started"]
    assert scheduled.triggers == 0  # type: ignore[attr-defined]
    # the writer beat once at startup: the row, then the file
    with units.open(read_only=True):
        row = ops_queries.heartbeat()
    assert row is not None
    assert (row.pid, row.started_at, row.beat_at) == (os.getpid(), T, T)
    assert (data_dir / main.HEARTBEAT_FILE_NAME).is_file()


# engine_lifespan with recording fakes -----------------------------------------------------------


class Failed(Exception):
    """A start, a stop or the server failed."""


class Parts:
    """A recording runner, scheduler and heartbeat writer sharing one call log of
    ``(call, thread)``; any call named in ``failing`` raises ``Failed`` after recording."""

    def __init__(self, *failing: str) -> None:
        self.failing = set(failing)
        self.calls: list[tuple[str, threading.Thread]] = []
        self.runner = _Part(self, "runner")
        self.scheduler = _SchedulerPart(self)
        self.writer = _Part(self, "writer")

    def record(self, call: str) -> None:
        self.calls.append((call, threading.current_thread()))
        if call in self.failing:
            raise Failed(call)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def lifespan(self) -> main.Lifespan:
        return main.engine_lifespan(
            self.runner,  # type: ignore[arg-type]
            self.scheduler,  # type: ignore[arg-type]
            self.writer,  # type: ignore[arg-type]
        )


class _Part:
    def __init__(self, parts: Parts, name: str) -> None:
        self.parts, self.name = parts, name

    def start(self) -> None:
        self.parts.record(f"{self.name}.start")

    def stop(self) -> None:
        self.parts.record(f"{self.name}.stop")


class _SchedulerPart:
    def __init__(self, parts: Parts) -> None:
        self.parts = parts

    def start(self) -> None:
        asyncio.get_running_loop()  # like AsyncIOScheduler.start: only on the loop
        self.parts.record("scheduler.start")

    async def stop(self) -> None:
        self.parts.record("scheduler.stop")


def serve(parts: Parts, *, server_fails: bool = False) -> threading.Thread:
    """Run the lifespan around a server that returns (or raises ``Failed``); the loop's
    thread."""

    async def run() -> threading.Thread:
        async with parts.lifespan()(FastAPI()):
            parts.calls.append(("serving", threading.current_thread()))
            if server_fails:
                raise Failed("the server failed")
        return threading.current_thread()

    return asyncio.run(run())


def test_startup_runs_runner_scheduler_writer_and_shutdown_reverses_it() -> None:
    parts = Parts()

    loop_thread = serve(parts)

    assert parts.names() == [
        "runner.start",
        "scheduler.start",
        "writer.start",
        "serving",
        "writer.stop",
        "scheduler.stop",
        "runner.stop",
    ]
    on_loop = {name for name, thread in parts.calls if thread is loop_thread}
    assert on_loop == {"scheduler.start", "serving", "scheduler.stop"}


def test_every_part_stops_when_serving_fails() -> None:
    parts = Parts()

    with pytest.raises(Failed):
        serve(parts, server_fails=True)

    assert parts.names()[-3:] == ["writer.stop", "scheduler.stop", "runner.stop"]


@pytest.mark.parametrize(
    ("failing", "calls"),
    [
        ("runner.start", ["runner.start"]),
        ("scheduler.start", ["runner.start", "scheduler.start", "runner.stop"]),
        (
            "writer.start",
            ["runner.start", "scheduler.start", "writer.start", "scheduler.stop", "runner.stop"],
        ),
    ],
)
def test_a_failed_start_stops_the_parts_that_started_in_reverse(
    failing: str, calls: list[str]
) -> None:
    parts = Parts(failing)

    with pytest.raises(Failed):
        serve(parts)

    assert parts.names() == calls


def test_a_failed_stop_still_stops_the_other_parts() -> None:
    parts = Parts("writer.stop")

    with pytest.raises(Failed):
        serve(parts)

    assert parts.names()[-3:] == ["writer.stop", "scheduler.stop", "runner.stop"]
