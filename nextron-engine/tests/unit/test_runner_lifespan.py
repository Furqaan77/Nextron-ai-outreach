"""The job runner in the app's lifespan (``nextron.main.build_app`` and ``runner_lifespan``,
AD-10): on a real database at head, driven through the ASGI lifespan by hand, startup starts the
named threads, shutdown stops them, and building the app starts none."""

import asyncio
import logging
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI

from nextron import main
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
EXPECTED_THREADS = [
    "nextron-job-bulk-1",
    "nextron-job-interactive-1",
    "nextron-job-keeper",
    "nextron-job-realtime-1",
]


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


def test_building_the_app_starts_no_thread(units: UnitOfWorkFactory) -> None:
    before = set(threading.enumerate())

    build(units)

    assert job_threads() == []
    assert set(threading.enumerate()) == before


def test_the_lifespan_starts_the_runner_and_shutdown_stops_it(
    units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    app = build(units)

    sent, during = drive_lifespan(app)

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert during == EXPECTED_THREADS
    assert job_threads() == []
    events = [r.getMessage() for r in caplog.records if r.name == "nextron.jobs.runner"]
    assert events == ["job_runner_started", "job_runner_stopped"]
    [started] = [r for r in caplog.records if r.getMessage() == "job_runner_started"]
    assert started.bulk_threads == 1  # type: ignore[attr-defined]


class RecordingRunner:
    """Stands in for ``Runner``: records the thread each call ran on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, threading.Thread]] = []

    def start(self) -> None:
        self.calls.append(("start", threading.current_thread()))

    def stop(self) -> None:
        self.calls.append(("stop", threading.current_thread()))


def test_runner_lifespan_starts_and_stops_off_the_event_loop() -> None:
    runner = RecordingRunner()
    lifespan = main.runner_lifespan(runner)  # type: ignore[arg-type]
    inside: list[list[str]] = []

    async def run() -> threading.Thread:
        async with lifespan(FastAPI()):
            inside.append([name for name, _ in runner.calls])
        return threading.current_thread()

    loop_thread = asyncio.run(run())

    assert inside == [["start"]]
    assert [name for name, _ in runner.calls] == ["start", "stop"]
    assert all(thread is not loop_thread for _, thread in runner.calls)


def test_runner_lifespan_stops_the_runner_when_serving_fails() -> None:
    runner = RecordingRunner()
    lifespan = main.runner_lifespan(runner)  # type: ignore[arg-type]

    async def run() -> None:
        async with lifespan(FastAPI()):
            raise RuntimeError("the server failed")

    with pytest.raises(RuntimeError):
        asyncio.run(run())

    assert [name for name, _ in runner.calls] == ["start", "stop"]
