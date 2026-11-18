"""The ``job_dead`` sweep (``DeadJobAlerts``, AD-15, AD-20) through a real ``Runner``
(``run_one`` and ``keep_once``) on a temporary database at head, with ``FrozenClock``, the
registered ``send_alert`` job type and the in-memory alert port: Story 1.12's Dead job and Sweep
rows. Nothing here starts a thread or waits."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.jobqueue.api import Claim, JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import DeadJobAlerts
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.job import Job, JobType, JobTypes, Lane
from nextron.domain.settings_schema import Key
from nextron.jobs.runner import Runner
from nextron.ports.alert import AlertMessage
from nextron.ports.errors import AdapterError
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
CHAT = "-1001234567890"
RUNNER_LOGGER = "nextron.jobs.runner"
OPS_LOGGER = "nextron.app.ops.api"


def no_backups() -> Any:
    """``build_job_types``'s ``backups`` where no ``backup_db`` job runs."""
    raise AssertionError("no backup_db job runs in these tests")


def no_restore_tests() -> Any:
    """``build_job_types``'s ``restore_tests`` where no ``restore_test`` job runs."""
    raise AssertionError("no restore_test job runs in these tests")


def no_recovery() -> Any:
    """``build_job_types``'s ``recovery`` where no ``check_recovery`` job runs."""
    raise AssertionError("no check_recovery job runs in these tests")


def no_contact_markets() -> Any:
    """``build_job_types``'s ``contact_markets`` where no ``resolve_contact_markets`` job runs."""
    raise AssertionError("no resolve_contact_markets job runs in these tests")


def no_crawls() -> Any:
    """``build_job_types``'s ``crawls``, ``discovery`` and ``research`` where no Story 2.5 job
    runs."""
    raise AssertionError("no enrich_lead, canonicalize_lead or purge_page_text job runs here")


def fail_with_value_error(job: Job) -> None:
    raise ValueError(f"enrichment failed for job {job.id}")


ENRICH = JobType(
    "enrich_lead",
    Lane.BULK,
    frozenset({Lane.BULK}),
    50,
    timedelta(minutes=5),
    0,
    fail_with_value_error,
)


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    port: InMemoryAlert
    queue: JobQueue
    notify: Notify
    runner: Runner

    def query(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        assert self.engine.url.database
        connection = sqlite3.connect(self.engine.url.database, isolation_level=None)
        try:
            return connection.execute(statement, params).fetchall()
        finally:
            connection.close()

    def enqueue(self, job_type: str = "enrich_lead", discriminator: str = "1") -> str:
        @use_case
        def caller() -> str:
            with current().transaction():
                return self.queue.enqueue(
                    job_type,
                    scope=LEAD,
                    discriminator=discriminator,
                    payload={"v": 1, "lead_id": LEAD},
                ).job_id

        with self.units.open():
            return caller()

    def claim(self, lane: Lane = Lane.BULK) -> Claim:
        with self.units.open():
            found = self.queue.claim(lane)
        assert found is not None
        return found

    def job_status(self, job_id: str) -> tuple[str, int]:
        [(status, attempts)] = self.query(
            "SELECT status, attempts FROM jobs WHERE id = ?", (job_id,)
        )
        return status, attempts

    def alerts(self) -> list[tuple[str, str, str, str]]:
        return self.query("SELECT kind, subject, text, status FROM alerts ORDER BY rowid")

    def notifications(self) -> list[tuple[str, str, str]]:
        return self.query("SELECT kind, subject, text FROM notifications ORDER BY rowid")

    def send_alert_jobs(self) -> list[tuple[str, str]]:
        return self.query(
            "SELECT id, status FROM jobs WHERE type = 'send_alert' ORDER BY rowid"
        )


def build_world(
    units: UnitOfWorkFactory,
    engine: Engine,
    clock: FrozenClock,
    *,
    dead_jobs: Callable[[Notify], Any] = DeadJobAlerts,
    extra: tuple[JobType, ...] = (ENRICH,),
) -> World:
    port = InMemoryAlert()
    built: dict[str, Notify] = {}
    registered = main.build_job_types(
        alerts=lambda: built["notify"],
        backups=no_backups,
        restore_tests=no_restore_tests,
        recovery=no_recovery,
        contact_markets=no_contact_markets,
        crawls=no_crawls,
        discovery=no_crawls,
        research=no_crawls,
        extractions=no_crawls,
        primary=no_crawls,
        legal_forms=no_crawls,
    )
    job_types = JobTypes()
    job_types.register_job_type(registered.get("send_alert"))  # the real, registered one
    for job_type in extra:
        job_types.register_job_type(job_type)
    job_types.freeze()
    queue = JobQueue(clock, job_types)
    notify = Notify(clock, queue, alert_ports={Channel.TELEGRAM: lambda: port})
    built["notify"] = notify
    runner = Runner(units, queue, job_types, dead_jobs=dead_jobs(notify))
    with units.open():
        Setup(clock).set(Key.ALERT_CHAT_ID, CHAT)
    return World(units, engine, clock, port, queue, notify, runner)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def world(units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock) -> World:
    return build_world(units, db_engine, clock)


def dead_text(job_type: str, job_id: str, attempts: int) -> str:
    return f"Job dead: {job_type} {job_id}, attempts: {attempts}"


# Row: Dead job ----------------------------------------------------------------------------------


def test_a_job_whose_handler_raises_is_alerted_on_telegram_and_the_dashboard(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    job_id = world.enqueue()

    assert world.runner.run_one(Lane.BULK) is True

    assert world.job_status(job_id) == ("dead", 1)
    text = dead_text("enrich_lead", job_id, 1)
    assert world.alerts() == [("job_dead", job_id, text, "pending")]
    assert world.notifications() == [("job_dead", job_id, text)]
    [(send_job, status)] = world.send_alert_jobs()
    assert status == "queued"
    [alerted] = [r for r in caplog.records if r.getMessage() == "dead_jobs_alerted"]
    assert (alerted.name, getattr(alerted, "count")) == (OPS_LOGGER, 1)

    assert world.runner.run_one(Lane.REALTIME) is True  # the registered send_alert runs

    assert world.port.sent == [AlertMessage(CHAT, text)]
    assert world.alerts() == [("job_dead", job_id, text, "sent")]
    assert world.job_status(send_job) == ("done", 1)
    assert "ValueError" not in text and LEAD not in text


def test_a_dead_send_alert_job_alerts_only_the_dashboard(world: World) -> None:
    job_id = world.enqueue()
    world.runner.run_one(Lane.BULK)
    world.port.outcomes.append(RuntimeError("a bug in the adapter"))
    [(send_job, _)] = world.send_alert_jobs()

    assert world.runner.run_one(Lane.REALTIME) is True

    assert world.job_status(send_job) == ("dead", 1)
    first = dead_text("enrich_lead", job_id, 1)
    assert world.alerts() == [("job_dead", job_id, first, "pending")]  # no Telegram for it
    assert world.notifications() == [
        ("job_dead", job_id, first),
        ("job_dead", send_job, dead_text("send_alert", send_job, 1)),
    ]
    assert len(world.send_alert_jobs()) == 1  # a broken Telegram can't feed itself


def test_a_rate_limited_alert_stays_pending_and_its_job_dies_after_six_runs(
    world: World, clock: FrozenClock
) -> None:
    job_id = world.enqueue()
    world.runner.run_one(Lane.BULK)
    world.port.failure = AdapterError(True, "telegram_unavailable", "HTTP 429")
    [(send_job, _)] = world.send_alert_jobs()

    world.runner.run_one(Lane.REALTIME)

    assert world.job_status(send_job) == ("queued", 1)
    [(run_after,)] = world.query("SELECT run_after FROM jobs WHERE id = ?", (send_job,))
    assert run_after == "2026-11-17T09:00:30.000000Z"
    assert world.runner.run_one(Lane.REALTIME) is False  # not due yet

    for _ in range(5):
        clock.advance(timedelta(minutes=30))
        assert world.runner.run_one(Lane.REALTIME) is True

    assert world.job_status(send_job) == ("dead", 6)
    assert len(world.port.attempts) == 6
    assert world.alerts() == [("job_dead", job_id, dead_text("enrich_lead", job_id, 1), "pending")]
    assert world.notifications()[-1] == (
        "job_dead",
        send_job,
        dead_text("send_alert", send_job, 6),
    )
    assert len(world.send_alert_jobs()) == 1


# Row: Sweep -------------------------------------------------------------------------------------


def test_a_job_reaped_at_its_last_attempt_is_alerted_on_the_next_keeper_tick(
    world: World, clock: FrozenClock
) -> None:
    job_id = world.enqueue()
    world.claim()  # a process that then crashed: the lease is never renewed
    clock.advance(timedelta(minutes=7))

    world.runner.keep_once()

    assert world.job_status(job_id) == ("dead", 1)
    assert world.notifications() == [("job_dead", job_id, dead_text("enrich_lead", job_id, 1))]
    assert len(world.alerts()) == 1


def test_an_unrunnable_job_is_alerted_on_the_next_keeper_tick(world: World) -> None:
    world.query(
        "INSERT INTO jobs (id, type, lane, status, priority, payload, idempotency_key, "
        "concurrency_key, run_after, attempts, lease_until, last_error, created_at, "
        "updated_at) VALUES (?, 'retire_lead', 'bulk', 'queued', 50, '{\"v\":1}', "
        "'retire_lead:global:tick', NULL, '2026-11-17T09:00:00.000000Z', 0, NULL, NULL, "
        "'2026-11-17T09:00:00.000000Z', '2026-11-17T09:00:00.000000Z')",
        (LEAD,),
    )

    assert world.runner.run_one(Lane.BULK) is False  # claimed straight to dead; nothing ran
    assert world.job_status(LEAD) == ("dead", 1)
    assert world.notifications() == []

    world.runner.keep_once()

    assert world.notifications() == [("job_dead", LEAD, dead_text("retire_lead", LEAD, 1))]


def test_a_job_released_at_its_last_attempt_is_alerted_on_the_next_keeper_tick(
    world: World,
) -> None:
    job_id = world.enqueue()
    claim = world.claim()
    with world.units.open():
        world.queue.release([claim])  # the engine stopped while it ran

    world.runner.keep_once()

    assert world.job_status(job_id) == ("dead", 1)
    assert [subject for _, subject, _ in world.notifications()] == [job_id]


def test_an_alert_lost_to_a_crash_is_raised_once_on_the_next_tick(world: World) -> None:
    job_id = world.enqueue()
    claim = world.claim()
    with world.units.open():  # the job died, then the process crashed before the sweep
        world.queue.fail(claim, ValueError("boom"))

    world.runner.keep_once()
    world.runner.keep_once()
    world.runner.keep_once()

    assert [subject for _, subject, _ in world.notifications()] == [job_id]
    assert [subject for _, subject, _, _ in world.alerts()] == [job_id]
    assert len(world.send_alert_jobs()) == 1


def test_each_dead_job_is_alerted_once_whatever_runs_the_sweep(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    first = world.enqueue(discriminator="1")
    second = world.enqueue(discriminator="2")
    world.runner.run_one(Lane.BULK)
    world.runner.run_one(Lane.BULK)

    world.runner.keep_once()
    with world.units.open():
        assert DeadJobAlerts(world.notify).alert_dead_jobs() == 0

    assert [subject for _, subject, _ in world.notifications()] == [first, second]
    counts = [getattr(r, "count") for r in caplog.records if r.getMessage() == "dead_jobs_alerted"]
    assert counts == [1, 1]


class BrokenSweep:
    def __init__(self, notify: Notify) -> None:
        del notify
        self.calls = 0

    def alert_dead_jobs(self) -> int:
        self.calls += 1
        raise RuntimeError("database is locked")


def test_a_sweep_error_is_logged_and_nothing_stops(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = build_world(units, db_engine, clock, dead_jobs=BrokenSweep)
    job_id = world.enqueue()

    assert world.runner.run_one(Lane.BULK) is True  # the run's own sweep fails
    world.runner.keep_once()  # and so does the keeper's
    other = world.enqueue(discriminator="2")
    world.claim()
    clock.advance(timedelta(minutes=7))
    world.runner.keep_once()  # the keeper still reaps

    assert world.job_status(job_id) == ("dead", 1)
    assert world.job_status(other) == ("dead", 1)
    failures = [
        r for r in caplog.records
        if r.name == RUNNER_LOGGER and r.getMessage() == "dead_job_alerts_failed"
    ]
    assert len(failures) == 3
    assert all(r.levelno == logging.ERROR and r.exc_info for r in failures)
    assert world.notifications() == []


def test_without_a_sweep_the_runner_alerts_nothing(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock
) -> None:
    world = build_world(units, db_engine, clock, dead_jobs=lambda notify: None)
    job_id = world.enqueue()

    world.runner.run_one(Lane.BULK)
    world.runner.keep_once()

    assert world.job_status(job_id) == ("dead", 1)
    assert world.notifications() == []
