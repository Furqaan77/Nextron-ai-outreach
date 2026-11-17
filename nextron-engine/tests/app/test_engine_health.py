"""``EngineHealth`` (the ``ops`` service, Story 1.15) and its reads, on real units of work over a
temporary database at head, with ``FrozenClock``: the Running, Stale and Last success rows,
``last_done`` across types, the Dead job issue (redacted detail), the Source crash row,
``JobQueue.dismiss`` (the Dismiss and Dismiss no-op rows, and the sweep afterwards),
``current_revision_in_unit`` inside a read-only unit of work (and ``EngineDatabase`` failing a
foreign database with its reason), and ``report()`` running the
readiness checks before its first database read. Nothing here starts a thread or waits."""

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Budget, DeadJobAlerts, EngineHealth, Heartbeat, Worker
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.db.engine import create_engine
from nextron.db.migrations import (
    DatabaseStateError,
    current_revision_in_unit,
    head_revision,
    known_revisions,
)
from nextron.db.uow import Subscribers, UnitOfWorkFactory, current, in_transaction, use_case
from nextron.domain.alert import Channel
from nextron.domain.budget import AiCall
from nextron.domain.health import (
    GroupRuns,
    HealthIssue,
    HealthIssues,
    IssueSource,
    LastRun,
)
from nextron.domain.job import Job, JobGroup, JobType, JobTypes, Lane
from nextron.domain.readiness import CheckResult, Status
from nextron.ports.errors import AdapterError
from nextron.startup_checks import CheckFailed, check_database_revision
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.usage_meter import InMemoryUsageMeter

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
ALERTS = [f"01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}" for n in range(10, 30)]
UNKNOWN_JOB = "01JD3QZ8X5V9K2M7N4P6R8T0ZZ"
OPS_LOGGER = "nextron.app.ops.api"
JOBQUEUE_LOGGER = "nextron.app.jobqueue.api"
READINESS = (CheckResult("python_version", Status.OK, "fine"),)


def no_backups() -> Any:
    """``build_job_types``'s ``backups`` where no ``backup_db`` job runs."""
    raise AssertionError("no backup_db job runs in these tests")


def no_restore_tests() -> Any:
    """``build_job_types``'s ``restore_tests`` where no ``restore_test`` job runs."""
    raise AssertionError("no restore_test job runs in these tests")


def no_recovery() -> Any:
    """``build_job_types``'s ``recovery`` where no ``check_recovery`` job runs."""
    raise AssertionError("no check_recovery job runs in these tests")


def enrich(job: Job) -> None:
    return None


ENRICH = JobType(
    "enrich_lead",
    Lane.BULK,
    frozenset({Lane.BULK}),
    50,
    timedelta(minutes=5),
    0,
    enrich,
    group=JobGroup.ENRICHMENT,
)


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    tmp_path: Path
    job_types: JobTypes
    queue: JobQueue = field(init=False)
    notify: Notify = field(init=False)
    budget: Budget = field(init=False)
    meter: InMemoryUsageMeter = field(init=False)
    readiness: Callable[[], Sequence[CheckResult]] = lambda: READINESS
    used: int = 0

    def __post_init__(self) -> None:
        self.queue = JobQueue(self.clock, self.job_types)
        self.notify = Notify(
            self.clock, self.queue, alert_ports={Channel.TELEGRAM: lambda: InMemoryAlert()}
        )
        self.meter = InMemoryUsageMeter(self.clock)
        self.budget = Budget(self.clock, self.meter, self.notify)

    def health(self, issues: HealthIssues | None = None) -> EngineHealth:
        return EngineHealth(
            self.clock,
            self.job_types,
            issues=issues if issues is not None else main.build_health_issues(budget=self.budget),
            readiness=self.readiness,
        )

    def query(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        assert self.engine.url.database
        connection = sqlite3.connect(self.engine.url.database, isolation_level=None)
        try:
            return connection.execute(statement, params).fetchall()
        finally:
            connection.close()

    def enqueue(self, job_type: str = "send_alert") -> str:
        subject = ALERTS[self.used]
        self.used += 1
        key = "alert_id" if job_type == "send_alert" else "lead_id"

        @use_case
        def caller() -> str:
            with current().transaction():
                return self.queue.enqueue(
                    job_type, scope=subject, discriminator="0", payload={"v": 1, key: subject}
                ).job_id

        with self.units.open():
            return caller()

    def run(self, job_type: str = "send_alert", *, succeed: bool = True) -> str:
        """Enqueue a job, claim it and finish it: ``done``, or ``dead`` at once."""
        job_id = self.enqueue(job_type)
        lane = self.job_types.get(job_type).default_lane
        with self.units.open():
            claim = self.queue.claim(lane)
        assert claim is not None and claim.job.id == job_id
        with self.units.open():
            if succeed:
                assert self.queue.complete(claim)
            else:
                self.queue.fail(claim, ValueError("broken"))
        return job_id

    def exhaust(self) -> str:
        """A ``send_alert`` job that dies after its 6 runs (retry limit 5)."""
        job_id = self.enqueue()
        for _ in range(6):
            with self.units.open():
                claim = self.queue.claim(Lane.REALTIME)
            assert claim is not None and claim.job.id == job_id
            with self.units.open():
                self.queue.fail(claim, AdapterError(True, "telegram_unavailable", "HTTP 429"))
            self.clock.advance(timedelta(minutes=31))
        assert self.status(job_id) == ("dead", 6)
        return job_id

    def status(self, job_id: str) -> tuple[str, int]:
        [(status, attempts)] = self.query(
            "SELECT status, attempts FROM jobs WHERE id = ?", (job_id,)
        )
        return status, attempts

    def beat(self) -> None:
        with self.units.open():
            Heartbeat(self.clock, self.tmp_path / "heartbeat").beat()

    def read(self, call: Callable[[], Any]) -> Any:
        with self.units.open(read_only=True):
            return call()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


def registered_job_types(world_notify: Callable[[], Notify]) -> JobTypes:
    """The real registered ``send_alert``, plus ``enrich_lead`` (Enrichment)."""
    registered = main.build_job_types(
        alerts=world_notify,
        backups=no_backups,
        restore_tests=no_restore_tests,
        recovery=no_recovery,
    )
    job_types = JobTypes()
    job_types.register_job_type(registered.get("send_alert"))
    job_types.register_job_type(ENRICH)
    job_types.freeze()
    return job_types


@pytest.fixture
def world(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock, tmp_path: Path
) -> World:
    built: dict[str, Notify] = {}
    job_types = registered_job_types(lambda: built["notify"])
    found = World(units, db_engine, clock, tmp_path, job_types)
    built["notify"] = found.notify
    return found


# Rows: Running and Stale ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("after", "running"),
    [
        (timedelta(seconds=119), True),
        (timedelta(seconds=-5), True),
        (timedelta(seconds=120), False),
        (timedelta(seconds=-300), False),
    ],
    ids=["119s-ago", "5s-ahead", "120s-ago", "300s-ahead"],
)
def test_the_worker_runs_only_while_its_beat_is_under_2_minutes_away(
    world: World, clock: FrozenClock, after: timedelta, running: bool
) -> None:
    world.beat()
    clock.advance(after)

    report = world.read(world.health().report)

    assert report.worker == Worker(running, T0)
    assert report.checked_at == T0 + after


def test_before_the_first_beat_the_worker_is_not_running(world: World) -> None:
    assert world.read(world.health().report).worker == Worker(False, None)


# Row: Last success --------------------------------------------------------------------------------


def test_each_type_shows_its_latest_done_run_and_never_without_one(
    world: World, clock: FrozenClock
) -> None:
    world.run()  # done at T1
    clock.advance(timedelta(minutes=10))
    t2 = clock.now()
    world.run()  # done at T2
    clock.advance(timedelta(minutes=10))
    world.run(succeed=False)  # dead: not a success
    world.enqueue()  # queued: not a success

    report = world.read(world.health().report)

    assert report.groups == (
        GroupRuns(JobGroup.ENRICHMENT, (LastRun("enrich_lead", None),)),
        GroupRuns(JobGroup.OTHER, (LastRun("send_alert", t2),)),
    )


def test_last_done_is_each_types_latest_done_change(world: World, clock: FrozenClock) -> None:
    assert world.read(jobqueue_queries.last_successes) == {}

    world.run()
    t1 = clock.now()
    clock.advance(timedelta(minutes=1))
    world.run("enrich_lead")
    t2 = clock.now()
    clock.advance(timedelta(minutes=1))
    world.run("enrich_lead", succeed=False)  # dead: ignored

    found = world.read(jobqueue_queries.last_successes)

    assert dict(found) == {"send_alert": t1, "enrich_lead": t2}
    assert all(moment.tzinfo is not None for moment in found.values())
    with pytest.raises(TypeError):
        found["send_alert"] = t2  # type: ignore[index]


def test_the_real_registry_groups_the_backup_jobs_as_backup_and_send_alert_as_other(
    world: World,
) -> None:
    health = EngineHealth(
        world.clock,
        main.build_job_types(
            alerts=lambda: world.notify,
            backups=no_backups,
            restore_tests=no_restore_tests,
            recovery=no_recovery,
        ),
        issues=main.build_health_issues(budget=world.budget),
        readiness=world.readiness,
    )

    report = world.read(health.report)

    assert report.groups == (
        GroupRuns(
            JobGroup.BACKUP,
            (
                LastRun("backup_db", None),
                LastRun("restore_test", None),
                LastRun("check_recovery", None),
            ),
        ),
        GroupRuns(JobGroup.OTHER, (LastRun("send_alert", None),)),
    )
    assert report.readiness == READINESS
    assert report.issues == ()


# Row: Dead job ----------------------------------------------------------------------------------


def test_a_dead_job_is_an_issue_with_its_redacted_last_error(world: World) -> None:
    job_id = world.exhaust()
    stored = "telegram_unavailable: refused for mac@example.com <script>alert(1)</script>"
    world.query("UPDATE jobs SET last_error = ? WHERE id = ?", (stored, job_id))

    issues = world.read(world.health().issues)

    assert issues == (
        HealthIssue(
            "job_dead",
            job_id,
            f"Job dead: send_alert {job_id}, attempts: 6",
            detail="Last error: telegram_unavailable: refused for <email> "
            "<script>alert(1)</script>",
        ),
    )
    assert world.read(world.health().issue_count) == 1


def test_dead_jobs_come_oldest_first_and_without_an_error_have_no_detail(
    world: World, clock: FrozenClock
) -> None:
    first = world.run(succeed=False)
    clock.advance(timedelta(minutes=1))
    second = world.run("enrich_lead", succeed=False)
    world.query("UPDATE jobs SET last_error = NULL WHERE id = ?", (second,))

    issues = world.read(world.health().issues)

    assert [(issue.subject, issue.detail) for issue in issues] == [
        (first, "Last error: ValueError: broken"),
        (second, None),
    ]


def test_budget_issues_follow_the_jobqueue_ones(world: World) -> None:
    job_id = world.run(succeed=False)
    spend = AiCall(
        "draft_email", "claude-x", "claude-x", input_tokens=1, output_tokens=1,
        cost_micros=16_000_000,
    )
    world.meter.record(spend)  # $16 of the default $20: the 80% warning

    issues = world.read(world.health().issues)

    assert [(issue.kind, issue.subject) for issue in issues] == [
        ("job_dead", job_id),
        ("ai_budget_warning", "ai_budget:2026-11"),
    ]


# Row: Source crash ------------------------------------------------------------------------------


def registry(*sources: IssueSource) -> HealthIssues:
    issues = HealthIssues()
    for source in sources:
        issues.register_source(source)
    issues.freeze()
    return issues


def test_a_source_that_raises_gives_one_failure_issue_and_the_others_still_run(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    job_id = world.run(succeed=False)

    def crash() -> tuple[HealthIssue, ...]:
        raise RuntimeError("the source broke on mac@example.com")

    def wrong() -> list[object]:
        return [HealthIssue("other", "x", "fine"), "not an issue"]

    health = world.health(
        registry(
            IssueSource("crashing", crash),
            IssueSource("jobqueue", jobqueue_queries.health_issues),
            IssueSource("wrong", wrong),
        )
    )

    issues = world.read(health.issues)

    assert [(issue.kind, issue.subject, issue.text) for issue in issues] == [
        (
            "health_check_failed",
            "crashing",
            "The crashing health check could not be read (RuntimeError)",
        ),
        ("job_dead", job_id, f"Job dead: send_alert {job_id}, attempts: 1"),
        ("health_check_failed", "wrong", "The wrong health check could not be read (TypeError)"),
    ]
    failed = [r for r in caplog.records if r.getMessage() == "health_check_failed"]
    assert [(r.name, r.levelno, getattr(r, "source"), getattr(r, "code")) for r in failed] == [
        (OPS_LOGGER, logging.ERROR, "crashing", "RuntimeError"),
        (OPS_LOGGER, logging.ERROR, "wrong", "TypeError"),
    ]
    assert all(r.exc_info is not None for r in failed)
    assert all("mac@example.com" not in issue.text for issue in issues)


def test_engine_health_needs_a_frozen_registry(world: World) -> None:
    with pytest.raises(RuntimeError):
        EngineHealth(world.clock, world.job_types, issues=HealthIssues(), readiness=tuple)
    with pytest.raises(TypeError):
        EngineHealth(
            world.clock,
            "job types",  # type: ignore[arg-type]
            issues=registry(),
            readiness=tuple,
        )


# Rows: Dismiss and Dismiss no-op ----------------------------------------------------------------


def dismiss(world: World, job_id: str) -> bool:
    with world.units.open():
        return world.queue.dismiss(job_id)


def test_dismissing_a_dead_job_deletes_its_row_and_logs_it(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    job_id = world.exhaust()
    events = world.query("SELECT count(*) FROM events WHERE entity_id = ?", (job_id,))

    assert dismiss(world, job_id) is True

    assert world.query("SELECT id FROM jobs WHERE id = ?", (job_id,)) == []
    # no events row: a dismissal is no Job-machine transition; the history stays
    assert world.query("SELECT count(*) FROM events WHERE entity_id = ?", (job_id,)) == events
    [record] = [r for r in caplog.records if r.getMessage() == "job_dismissed"]
    assert (record.name, record.levelno) == (JOBQUEUE_LOGGER, logging.INFO)
    assert (
        getattr(record, "job_id"),
        getattr(record, "job_type"),
        getattr(record, "lane"),
        getattr(record, "attempts"),
    ) == (job_id, "send_alert", "realtime", 6)
    assert world.read(world.health().issues) == ()


def test_the_sweep_never_alerts_a_dismissed_job(world: World) -> None:
    job_id = world.run(succeed=False)
    assert dismiss(world, job_id) is True

    with world.units.open():
        alerted = DeadJobAlerts(world.notify).alert_dead_jobs()

    assert alerted == 0
    assert world.query("SELECT subject FROM notifications") == []


def snapshot(world: World) -> tuple[list[Any], list[Any]]:
    return (
        world.query("SELECT id, status, attempts, updated_at FROM jobs ORDER BY id"),
        world.query("SELECT id FROM events ORDER BY id"),
    )


def test_dismissing_anything_but_a_dead_job_changes_nothing(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    done = world.run()
    dead = world.run(succeed=False)
    queued = world.enqueue()
    before = snapshot(world)

    for job_id in (done, queued, UNKNOWN_JOB):
        assert dismiss(world, job_id) is False

    assert snapshot(world) == before
    assert dismiss(world, dead) is True
    assert dismiss(world, dead) is False  # twice: already gone
    assert [r.getMessage() for r in caplog.records].count("job_dismissed") == 1


def test_a_running_job_is_not_dismissed(world: World) -> None:
    job_id = world.enqueue()
    with world.units.open():
        claim = world.queue.claim(Lane.REALTIME)
    assert claim is not None

    assert dismiss(world, job_id) is False
    assert world.status(job_id) == ("running", 1)


# current_revision_in_unit -----------------------------------------------------------------------


def factory(engine: Engine) -> UnitOfWorkFactory:
    subscribers = Subscribers()
    subscribers.freeze()
    return UnitOfWorkFactory(engine, subscribers)


def test_the_revision_reads_at_head_inside_a_read_only_unit(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        assert not in_transaction()
        assert current_revision_in_unit() == head_revision()
        assert in_transaction()  # it read through the unit's own read transaction


def test_the_revision_of_an_empty_database_is_none(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "empty.db")
    try:
        with factory(engine).open(read_only=True):
            assert current_revision_in_unit() is None
    finally:
        engine.dispose()


def foreign_database(tmp_path: Path) -> Path:
    """A WAL database file with a table but no Alembic revision: not this engine's."""
    path = tmp_path / "foreign.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE other (id INTEGER)")
    connection.close()
    return path


def test_a_database_with_tables_but_no_revision_is_refused(tmp_path: Path) -> None:
    engine = create_engine(foreign_database(tmp_path))
    try:
        with factory(engine).open(read_only=True), pytest.raises(DatabaseStateError):
            current_revision_in_unit()
    finally:
        engine.dispose()


def test_the_engine_database_fails_a_foreign_database_with_its_reason(tmp_path: Path) -> None:
    path = foreign_database(tmp_path)
    engine = create_engine(path)
    database = main.EngineDatabase(path)
    try:
        with factory(engine).open(read_only=True), pytest.raises(DatabaseStateError) as state:
            current_revision_in_unit()
        with factory(engine).open(read_only=True), pytest.raises(CheckFailed) as caught:
            database.revision()
        with factory(engine).open(read_only=True):
            result = check_database_revision(
                database.exists(), database.revision, head=head_revision(), known=known_revisions()
            )
    finally:
        engine.dispose()

    reason = str(state.value)
    assert reason == (
        "the database has tables but no Alembic revision; it was not made by this engine"
    )
    assert str(caught.value) == reason
    assert result == CheckResult("database_revision", Status.FAIL, reason)


# report() reads readiness first ----------------------------------------------------------------


def test_the_report_runs_readiness_before_its_first_database_read(world: World) -> None:
    seen: list[bool] = []

    def readiness() -> Sequence[CheckResult]:
        seen.append(in_transaction())
        return READINESS

    world.readiness = readiness

    with world.units.open(read_only=True):
        report = world.health().report()
        held = in_transaction()

    assert seen == [False]  # no read transaction (and no lock) while the checks ran
    assert held  # the report's reads began it afterwards
    assert report.readiness == READINESS
