"""The ``jobqueue`` service (``JobQueue``, AD-3, AD-4, AD-10) on a temporary database at head,
with ``FrozenClock``: Story 1.10's matrix rows from Enqueue to Purge, the ``events`` row of each
transition, the partial unique index, the checks and the fences."""

import logging
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import Engine, event

from nextron.app.jobqueue.api import (
    LEASE_EXPIRED_ERROR,
    RELEASED_ERROR,
    Claim,
    Enqueued,
    JobQueue,
    Kept,
    error_text,
)
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.db.models.jobqueue import LIVE_WHERE
from nextron.db.repos.jobqueue import jobs as jobs_repo
from nextron.db.repos.jobqueue.jobs import JobRow
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current, use_case
from nextron.domain.job import (
    LIVE_STATUSES,
    InvalidJob,
    Job,
    JobStatus,
    JobType,
    JobTypes,
    Lane,
    UnknownJobType,
)
from nextron.ports.errors import AdapterError
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
INBOX_A = "inbox:01JD3QZ8X5V9K2M7N4P6R8T0W1"
INBOX_B = "inbox:01JD3QZ8X5V9K2M7N4P6R8T0W2"
PLANTED = "mac@example.com"
MICRO = timedelta(microseconds=1)
LOGGER = "nextron.app.jobqueue.api"


def noop(job: Job) -> None:
    del job


ENRICH = JobType(
    "enrich_lead",
    Lane.BULK,
    frozenset({Lane.BULK, Lane.INTERACTIVE}),
    50,
    timedelta(minutes=5),
    3,
    noop,
)
SCORE = JobType("score_lead", Lane.BULK, frozenset({Lane.BULK}), 10, timedelta(minutes=1), 0, noop)
CRAWL = JobType("crawl_site", Lane.BULK, frozenset({Lane.BULK}), 90, timedelta(minutes=10), 2, noop)
SYNC = JobType(
    "sync_mailbox", Lane.REALTIME, frozenset({Lane.REALTIME}), 90, timedelta(minutes=2), 3, noop
)
RETIRED = JobType(
    "retire_lead", Lane.BULK, frozenset({Lane.BULK}), 95, timedelta(minutes=1), 3, noop
)


def registry(*job_types: JobType) -> JobTypes:
    found = JobTypes()
    for job_type in job_types or (ENRICH, SCORE, CRAWL, SYNC):
        found.register_job_type(job_type)
    found.freeze()
    return found


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def queue(clock: FrozenClock) -> JobQueue:
    return JobQueue(clock, registry())


# Helpers ----------------------------------------------------------------------------------------


def transact[R](units: UnitOfWorkFactory, fn: Callable[[], R]) -> R:
    """Run ``fn`` inside a caller's use case and transaction, as a service would."""

    @use_case
    def caller() -> R:
        with current().transaction():
            return fn()

    with units.open():
        return caller()


def enqueue(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    job_type: str = "enrich_lead",
    *,
    scope: str = LEAD,
    discriminator: str = "1",
    payload: dict[str, object] | None = None,
    **options: Any,
) -> Enqueued:
    body = {"v": 1, "lead_id": LEAD} if payload is None else payload
    return transact(
        units,
        lambda: queue.enqueue(
            job_type, scope=scope, discriminator=discriminator, payload=body, **options
        ),
    )


def call(units: UnitOfWorkFactory, method: Callable[..., Any], *args: object) -> Any:
    """One use case in its own unit of work, as an entry point calls it."""
    with units.open():
        return method(*args)


def claim(units: UnitOfWorkFactory, queue: JobQueue, lane: Lane = Lane.BULK) -> Claim | None:
    return call(units, queue.claim, lane)


def claimed(units: UnitOfWorkFactory, queue: JobQueue, lane: Lane = Lane.BULK) -> Claim:
    found = claim(units, queue, lane)
    assert found is not None
    return found


def row(units: UnitOfWorkFactory, job_id: str) -> JobRow:
    with units.open(read_only=True):
        found = jobqueue_queries.job(job_id)
    assert found is not None
    return found


def query(engine: Engine, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
    assert engine.url.database
    connection = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        return connection.execute(statement, params).fetchall()
    finally:
        connection.close()


def events(engine: Engine, job_id: str) -> list[tuple[str | None, str, str]]:
    return query(
        engine,
        "SELECT from_status, to_status, cause FROM events WHERE entity_id = ? ORDER BY rowid",
        (job_id,),
    )


def causes(engine: Engine, job_id: str) -> list[str]:
    return [cause for _, _, cause in events(engine, job_id)]


def job_ids(engine: Engine) -> set[str]:
    return {job_id for (job_id,) in query(engine, "SELECT id FROM jobs")}


def records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == event]


def extras(record: logging.LogRecord, *names: str) -> tuple[object, ...]:
    """The ``extra`` fields ``names`` of ``record``."""
    return tuple(getattr(record, name) for name in names)


JOB_COLUMNS = (
    "id, type, lane, status, priority, payload, idempotency_key, concurrency_key, run_after, "
    "attempts, lease_until, last_error, created_at, updated_at"
)


def insert_raw(engine: Engine, job_id: str, key: str, status: str, lease: str | None) -> None:
    query(
        engine,
        f"INSERT INTO jobs ({JOB_COLUMNS}) VALUES (?, 'enrich_lead', 'bulk', ?, 50, '{{\"v\":1}}', "
        "?, NULL, '2026-11-17T09:00:00.000000Z', 0, ?, NULL, '2026-11-17T09:00:00.000000Z', "
        "'2026-11-17T09:00:00.000000Z')",
        (job_id, status, key, lease),
    )


# Row: Enqueue -----------------------------------------------------------------------------------


def test_enqueue_inserts_a_queued_job_and_its_creation_event(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    result = enqueue(units, queue, discriminator="3")

    assert result.created is True
    job = row(units, result.job_id)
    assert (job.type, job.lane, job.status, job.priority, job.attempts) == (
        "enrich_lead",
        "bulk",
        "queued",
        50,
        0,
    )
    assert job.idempotency_key == f"enrich_lead:{LEAD}:3"
    assert job.payload == f'{{"lead_id":"{LEAD}","v":1}}'
    assert (job.run_after, job.created_at, job.updated_at) == (T0, T0, T0)
    assert (job.concurrency_key, job.lease_until, job.last_error) == (None, None, None)
    assert query(db_engine, "SELECT entity, entity_id, from_status, to_status, cause, "
                 "cause_ref, actor, at FROM events") == [
        (
            "jobs",
            result.job_id,
            None,
            "queued",
            "enqueued",
            None,
            "system:jobqueue",
            "2026-11-17T09:00:00.000000Z",
        )
    ]


def test_an_enqueue_is_undone_when_the_caller_rolls_back(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    @use_case
    def caller() -> None:
        with current().transaction():
            queue.enqueue("enrich_lead", scope=LEAD, discriminator="3", payload={"v": 1})
            raise RuntimeError("the caller's own step failed")

    with pytest.raises(RuntimeError), units.open():
        caller()

    assert query(db_engine, "SELECT count(*) FROM jobs") == [(0,)]
    assert query(db_engine, "SELECT count(*) FROM events") == [(0,)]


def test_enqueue_runs_only_inside_a_transaction(queue: JobQueue, units: UnitOfWorkFactory) -> None:
    with pytest.raises(UnitOfWorkError), units.open():
        queue.enqueue("enrich_lead", scope=LEAD, discriminator="3", payload={"v": 1})


def test_enqueue_takes_a_lane_a_run_after_and_a_concurrency_key(
    units: UnitOfWorkFactory, queue: JobQueue
) -> None:
    later = datetime(2026, 11, 17, 4, 30, tzinfo=timezone(timedelta(hours=-5)))  # 09:30 UTC

    result = enqueue(
        units,
        queue,
        scope=f"inbox:{LEAD}",
        discriminator="tick",
        lane=Lane.INTERACTIVE,
        run_after=later,
        concurrency_key="domain:example.com",
    )

    job = row(units, result.job_id)
    assert job.lane == "interactive"
    assert job.run_after == T0 + timedelta(minutes=30) and job.run_after.tzinfo is UTC
    assert job.concurrency_key == "domain:example.com"
    assert job.idempotency_key == f"enrich_lead:inbox:{LEAD}:tick"
    assert job.created_at == T0


def test_a_lane_may_be_given_as_its_text(units: UnitOfWorkFactory, queue: JobQueue) -> None:
    result = enqueue(units, queue, lane="interactive")

    assert row(units, result.job_id).lane == "interactive"


# Row: Duplicate ---------------------------------------------------------------------------------


def test_a_duplicate_while_queued_or_running_returns_the_live_job(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    first = enqueue(units, queue)

    assert enqueue(units, queue, payload={"v": 2}) == Enqueued(first.job_id, False)
    claimed(units, queue)
    assert enqueue(units, queue) == Enqueued(first.job_id, False)
    assert job_ids(db_engine) == {first.job_id}
    assert causes(db_engine, first.job_id) == ["enqueued", "claimed"]
    assert row(units, first.job_id).payload == f'{{"lead_id":"{LEAD}","v":1}}'


@pytest.mark.parametrize("finish", ["done", "dead"])
def test_after_done_or_dead_the_same_key_makes_a_new_job(
    finish: str, units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    first = enqueue(units, queue)
    held = claimed(units, queue)
    if finish == "done":
        assert call(units, queue.complete, held) is True
    else:
        assert call(units, queue.fail, held, ValueError("broken")) is JobStatus.DEAD

    second = enqueue(units, queue)

    assert second.created is True and second.job_id != first.job_id
    assert row(units, second.job_id).status == "queued"
    assert job_ids(db_engine) == {first.job_id, second.job_id}


# Row: Bad enqueue -------------------------------------------------------------------------------

BAD_ENQUEUES: list[tuple[dict[str, Any], type[Exception]]] = [
    ({"job_type": "send_message"}, UnknownJobType),
    ({"job_type": PLANTED}, UnknownJobType),
    ({"lane": Lane.REALTIME}, InvalidJob),
    ({"lane": "fast"}, InvalidJob),
    ({"lane": ["bulk"]}, InvalidJob),
    ({"payload": {"v": 1, "who": "a@b.co"}}, InvalidJob),
    ({"payload": {"v": 1, "note": "a b"}}, InvalidJob),
    ({"payload": {"v": 1, "ratio": 1.5}}, InvalidJob),
    ({"payload": {"v": 1, "missing": None}}, InvalidJob),
    ({"payload": {"v": 1, "items": [1]}}, InvalidJob),
    ({"payload": {"lead_id": LEAD}}, InvalidJob),
    ({"scope": "x"}, InvalidJob),
    ({"scope": PLANTED}, InvalidJob),
    ({"discriminator": "v2"}, InvalidJob),
    ({"concurrency_key": f"lead:{LEAD}"}, InvalidJob),
    ({"concurrency_key": "domain:Example.com"}, InvalidJob),
    ({"run_after": datetime(2026, 11, 17, 9, 0)}, InvalidJob),
    ({"run_after": "2026-11-17T09:00:00Z"}, InvalidJob),
]


@pytest.mark.parametrize(("overrides", "error"), BAD_ENQUEUES)
def test_a_bad_enqueue_writes_nothing_and_dooms_the_transaction(
    overrides: dict[str, Any],
    error: type[Exception],
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
) -> None:
    arguments: dict[str, Any] = {
        "job_type": "enrich_lead",
        "scope": LEAD,
        "discriminator": "3",
        "payload": {"v": 1, "lead_id": LEAD},
        **overrides,
    }
    caught: list[Exception] = []

    @use_case
    def caller() -> None:
        with current().transaction():
            try:
                queue.enqueue(arguments.pop("job_type"), **arguments)
            except (UnknownJobType, InvalidJob) as refused:
                caught.append(refused)  # caught, but the transaction is doomed anyway

    with pytest.raises(UnitOfWorkError, match="rolled back"), units.open():
        caller()

    [refused] = caught
    assert type(refused) is error
    for planted in (PLANTED, "a@b.co"):
        assert planted not in str(refused)
    assert query(db_engine, "SELECT count(*) FROM jobs") == [(0,)]
    assert query(db_engine, "SELECT count(*) FROM events") == [(0,)]


def test_a_queue_needs_a_job_type_registry(clock: FrozenClock) -> None:
    with pytest.raises(TypeError):
        JobQueue(clock, {})  # type: ignore[arg-type]


# Row: Claim order -------------------------------------------------------------------------------


def test_claims_take_the_highest_priority_due_job_first(
    units: UnitOfWorkFactory, queue: JobQueue, clock: FrozenClock
) -> None:
    low = enqueue(units, queue, "score_lead")  # priority 10
    middle = enqueue(units, queue, "enrich_lead")  # priority 50
    high = enqueue(units, queue, "crawl_site", run_after=T0 + timedelta(minutes=1))  # 90, later

    assert claimed(units, queue).job.id == middle.job_id
    assert claimed(units, queue).job.id == low.job_id
    assert claim(units, queue) is None
    clock.advance(timedelta(minutes=1) - MICRO)
    assert claim(units, queue) is None
    clock.advance(MICRO)
    assert claimed(units, queue).job.id == high.job_id


def test_equal_priorities_go_by_run_after_then_by_id(
    units: UnitOfWorkFactory, queue: JobQueue
) -> None:
    later = enqueue(units, queue, discriminator="1", run_after=T0 - timedelta(seconds=1))
    earliest = enqueue(units, queue, discriminator="2", run_after=T0 - timedelta(seconds=2))
    same = [enqueue(units, queue, discriminator=str(n)).job_id for n in (3, 4, 5)]

    order = [claimed(units, queue).job.id for _ in range(5)]

    assert order == [earliest.job_id, later.job_id, *sorted(same)]


def test_a_lane_claims_only_its_own_jobs(units: UnitOfWorkFactory, queue: JobQueue) -> None:
    bulk = enqueue(units, queue, "enrich_lead")
    realtime = enqueue(units, queue, "sync_mailbox", scope=f"inbox:{LEAD}", discriminator="tick")

    assert claim(units, queue, Lane.INTERACTIVE) is None
    assert claimed(units, queue, Lane.REALTIME).job.id == realtime.job_id
    assert claim(units, queue, Lane.REALTIME) is None
    assert claimed(units, queue, Lane.BULK).job.id == bulk.job_id


def test_a_claim_with_nothing_to_do_writes_nothing(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    assert claim(units, queue) is None
    assert query(db_engine, "SELECT count(*) FROM events") == [(0,)]


def test_a_claim_that_changes_other_than_one_row_raises_and_rolls_back(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = enqueue(units, queue)
    monkeypatch.setattr(jobs_repo, "claim", lambda job_id, **_: 0)

    with pytest.raises(RuntimeError, match="changed 0 rows; expected 1"):
        claim(units, queue)

    assert row(units, created.job_id).status == "queued"
    assert causes(db_engine, created.job_id) == ["enqueued"]


# Row: Concurrency -------------------------------------------------------------------------------


def test_a_running_concurrency_key_holds_back_its_other_jobs(
    units: UnitOfWorkFactory, queue: JobQueue
) -> None:
    first = enqueue(units, queue, discriminator="1", lane=Lane.BULK, concurrency_key=INBOX_A)
    second = enqueue(
        units, queue, discriminator="2", lane=Lane.INTERACTIVE, concurrency_key=INBOX_A
    )
    held = claimed(units, queue, Lane.BULK)
    assert held.job.id == first.job_id

    assert claim(units, queue, Lane.INTERACTIVE) is None
    other = enqueue(
        units, queue, discriminator="3", lane=Lane.INTERACTIVE, concurrency_key=INBOX_B
    )
    assert claimed(units, queue, Lane.INTERACTIVE).job.id == other.job_id
    assert claim(units, queue, Lane.INTERACTIVE) is None
    assert call(units, queue.complete, held) is True
    assert claimed(units, queue, Lane.INTERACTIVE).job.id == second.job_id


# Row: Success -----------------------------------------------------------------------------------


def test_a_successful_run_is_done_with_no_lease(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine, clock: FrozenClock
) -> None:
    created = enqueue(units, queue)

    held = claimed(units, queue)

    assert held.job == Job(created.job_id, "enrich_lead", Lane.BULK, {"v": 1, "lead_id": LEAD}, 1)
    assert held.max_runtime == timedelta(minutes=5)
    running = row(units, created.job_id)
    assert (running.status, running.attempts) == ("running", 1)
    assert running.lease_until == T0 + timedelta(minutes=6)
    assert running.updated_at == T0

    clock.advance(timedelta(seconds=10))
    assert call(units, queue.complete, held) is True

    done = row(units, created.job_id)
    assert (done.status, done.lease_until, done.updated_at) == ("done", None, clock.now())
    assert events(db_engine, created.job_id) == [
        (None, "queued", "enqueued"),
        ("queued", "running", "claimed"),
        ("running", "done", "succeeded"),
    ]


def test_every_transition_event_is_a_system_jobqueue_row_with_no_cause_ref(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine, clock: FrozenClock
) -> None:
    created = enqueue(units, queue)
    clock.advance(timedelta(seconds=1))
    held = claimed(units, queue)
    clock.advance(timedelta(seconds=1))
    call(units, queue.fail, held, AdapterError(True, "rate_limited", "slow down"))

    rows = query(
        db_engine, "SELECT entity, entity_id, cause_ref, actor, at FROM events ORDER BY rowid"
    )

    assert rows == [
        ("jobs", created.job_id, None, "system:jobqueue", f"2026-11-17T09:00:0{n}.000000Z")
        for n in range(3)
    ]


# Row: Retryable ---------------------------------------------------------------------------------


def test_a_retryable_error_backs_off_30_60_and_120_seconds_then_the_job_is_dead(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    created = enqueue(units, queue)  # retry limit 3
    error = AdapterError(True, "rate_limited", f"Hunter asked {PLANTED} to slow down")

    for attempt, delay in enumerate((30, 60, 120), start=1):
        held = claimed(units, queue)
        assert held.job.attempts == attempt
        assert call(units, queue.fail, held, error) is JobStatus.QUEUED
        job = row(units, created.job_id)
        assert (job.status, job.lease_until) == ("queued", None)
        assert job.run_after == clock.now() + timedelta(seconds=delay)
        assert job.last_error == "rate_limited: Hunter asked <email> to slow down"
        clock.advance(timedelta(seconds=delay) - MICRO)
        assert claim(units, queue) is None
        clock.advance(MICRO)

    held = claimed(units, queue)
    assert held.job.attempts == 4
    assert call(units, queue.fail, held, error) is JobStatus.DEAD

    dead = row(units, created.job_id)
    assert (dead.status, dead.attempts, dead.lease_until) == ("dead", 4, None)
    assert causes(db_engine, created.job_id) == [
        "enqueued",
        *["claimed", "retry_scheduled"] * 3,
        "claimed",
        "retries_exhausted",
    ]
    assert events(db_engine, created.job_id)[-1] == ("running", "dead", "retries_exhausted")
    assert len(records(caplog, "job_retry_scheduled")) == 3
    [dead_record] = records(caplog, "job_dead")
    assert dead_record.levelno == logging.ERROR
    assert PLANTED not in caplog.text and "slow down" not in caplog.text


# Row: Fatal -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "text", "code"),
    [
        (ValueError("x@y.com"), "ValueError: <email>", "ValueError"),
        (AdapterError(False, "bad_request", "Hunter refused it"), "bad_request: Hunter refused it",
         "bad_request"),
        (UnknownJobType("score_lead"), "unknown_job_type: job type score_lead is not registered",
         "unknown_job_type"),
    ],
)
def test_any_other_error_makes_the_job_dead_at_once(
    error: Exception,
    text: str,
    code: str,
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = enqueue(units, queue)  # retry limit 3
    held = claimed(units, queue)

    assert call(units, queue.fail, held, error) is JobStatus.DEAD

    dead = row(units, created.job_id)
    assert (dead.status, dead.attempts, dead.last_error) == ("dead", 1, text)
    assert dead.lease_until is None
    assert events(db_engine, created.job_id)[-1] == ("running", "dead", "failed")
    [record] = records(caplog, "job_dead")
    assert extras(record, "job_id", "job_type", "lane", "attempts", "code") == (
        created.job_id,
        "enrich_lead",
        Lane.BULK,
        1,
        code,
    )


# Row: Limit 0 -----------------------------------------------------------------------------------


def test_a_type_with_retry_limit_0_is_dead_after_its_first_run(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    created = enqueue(units, queue, "score_lead")
    held = claimed(units, queue)

    status = call(units, queue.fail, held, AdapterError(True, "timeout", "timed out"))

    assert status is JobStatus.DEAD
    assert row(units, created.job_id).last_error == "timeout: timed out"
    assert causes(db_engine, created.job_id) == ["enqueued", "claimed", "retries_exhausted"]


# Row: Lease expiry ------------------------------------------------------------------------------


def test_an_expired_lease_is_requeued_with_its_attempt_counted(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = enqueue(units, queue)
    claimed(units, queue)
    lease = row(units, created.job_id).lease_until
    assert lease == T0 + timedelta(minutes=6)

    clock.set(lease - MICRO)
    assert call(units, queue.keep, ()) == Kept((), (), ())
    assert row(units, created.job_id).status == "running"

    clock.set(lease)
    assert call(units, queue.keep, ()) == Kept((), (created.job_id,), ())

    job = row(units, created.job_id)
    assert (job.status, job.attempts, job.run_after, job.lease_until, job.last_error) == (
        "queued",
        1,
        lease,
        None,
        None,
    )
    assert events(db_engine, created.job_id)[-1] == ("running", "queued", "lease_expired")
    [record] = records(caplog, "job_lease_expired")
    assert (record.levelno, *extras(record, "job_id", "attempts", "code")) == (
        logging.WARNING,
        created.job_id,
        1,
        "lease_expired",
    )
    again = claimed(units, queue)
    assert (again.job.id, again.job.attempts) == (created.job_id, 2)


def test_an_expired_lease_at_the_last_attempt_is_dead(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = enqueue(units, queue, "score_lead")  # retry limit 0
    claimed(units, queue)
    clock.advance(timedelta(hours=1))

    assert call(units, queue.keep, ()) == Kept((), (), (created.job_id,))

    dead = row(units, created.job_id)
    assert (dead.status, dead.lease_until, dead.last_error) == ("dead", None, LEASE_EXPIRED_ERROR)
    assert LEASE_EXPIRED_ERROR == "lease_expired: the lease expired before the job finished"
    assert events(db_engine, created.job_id)[-1] == ("running", "dead", "lease_expired")
    [record] = records(caplog, "job_dead")
    assert record.code == "lease_expired"  # type: ignore[attr-defined]
    assert claim(units, queue) is None


def test_a_reaped_job_whose_type_is_gone_counts_as_retry_limit_0(
    units: UnitOfWorkFactory, queue: JobQueue, clock: FrozenClock
) -> None:
    created = enqueue(units, queue)  # retry limit 3 while it is registered
    claimed(units, queue)
    clock.advance(timedelta(hours=1))
    upgraded = JobQueue(clock, registry(SCORE))

    assert call(units, upgraded.keep, ()) == Kept((), (), (created.job_id,))
    assert row(units, created.job_id).status == "dead"


# Row: Renewal -----------------------------------------------------------------------------------


def test_a_held_claim_is_renewed_and_never_reaped(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine, clock: FrozenClock
) -> None:
    created = enqueue(units, queue)
    held = claimed(units, queue)

    clock.advance(timedelta(seconds=30))
    assert call(units, queue.keep, (held,)) == Kept((), (), ())
    assert row(units, created.job_id).lease_until == clock.now() + timedelta(minutes=6)

    clock.advance(timedelta(hours=2))  # the PC slept: the old lease ended long ago
    assert call(units, queue.keep, (held,)) == Kept((), (), ())
    job = row(units, created.job_id)
    assert (job.status, job.lease_until) == ("running", clock.now() + timedelta(minutes=6))
    assert call(units, queue.complete, held) is True
    assert causes(db_engine, created.job_id) == ["enqueued", "claimed", "succeeded"]


def test_a_held_claim_whose_job_was_reaped_is_lost(
    units: UnitOfWorkFactory, queue: JobQueue, clock: FrozenClock
) -> None:
    created = enqueue(units, queue)
    held = claimed(units, queue)
    clock.advance(timedelta(hours=1))
    assert call(units, queue.keep, ()) == Kept((), (created.job_id,), ())

    assert call(units, queue.keep, (held,)) == Kept((held,), (), ())
    assert row(units, created.job_id).status == "queued"


def test_renewal_and_reaping_happen_in_one_keep(
    units: UnitOfWorkFactory, queue: JobQueue, clock: FrozenClock
) -> None:
    orphan = enqueue(units, queue, discriminator="1")
    claimed(units, queue)  # a crashed run: nobody holds it
    clock.advance(timedelta(minutes=1))
    mine = enqueue(units, queue, discriminator="2")
    held = claimed(units, queue)
    clock.advance(timedelta(minutes=10))

    assert call(units, queue.keep, (held,)) == Kept((), (orphan.job_id,), ())
    assert row(units, mine.job_id).status == "running"


# Row: Lost claim --------------------------------------------------------------------------------


@pytest.mark.parametrize("finish", ["complete", "fail"])
def test_a_run_whose_claim_was_lost_changes_nothing(
    finish: str,
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = enqueue(units, queue)
    stale = claimed(units, queue)
    clock.advance(timedelta(hours=1))
    call(units, queue.keep, ())  # reaped: back in the queue
    fresh = claimed(units, queue)  # and claimed again
    before = row(units, created.job_id)
    history = events(db_engine, created.job_id)

    if finish == "complete":
        assert call(units, queue.complete, stale) is False
    else:
        assert call(units, queue.fail, stale, ValueError("late")) is None

    assert row(units, created.job_id) == before
    assert events(db_engine, created.job_id) == history
    [record] = records(caplog, "job_claim_lost")
    assert (record.levelno, record.job_id, record.attempts) == (  # type: ignore[attr-defined]
        logging.WARNING,
        created.job_id,
        1,
    )
    assert call(units, queue.complete, fresh) is True


# Row: Shutdown ----------------------------------------------------------------------------------


def test_release_requeues_held_claims_and_kills_a_last_attempt(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    db_engine: Engine,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    kept = enqueue(units, queue, "enrich_lead")  # retry limit 3
    last = enqueue(units, queue, "score_lead")  # retry limit 0
    first_held = claimed(units, queue)
    second_held = claimed(units, queue)
    clock.advance(timedelta(seconds=5))

    released = call(units, queue.release, (first_held, second_held))

    assert released == (kept.job_id, last.job_id)
    requeued = row(units, kept.job_id)
    assert (requeued.status, requeued.attempts, requeued.lease_until) == ("queued", 1, None)
    assert requeued.run_after == T0  # unchanged: it keeps its place in the queue
    assert events(db_engine, kept.job_id)[-1] == ("running", "queued", "released")
    dead = row(units, last.job_id)
    assert (dead.status, dead.last_error) == ("dead", RELEASED_ERROR)
    assert RELEASED_ERROR == "released: the engine stopped before the job finished"
    assert events(db_engine, last.job_id)[-1] == ("running", "dead", "released")
    assert len(records(caplog, "job_released")) == 2
    assert len(records(caplog, "job_dead")) == 1
    assert call(units, queue.release, (first_held, second_held)) == ()


# Row: Unrunnable --------------------------------------------------------------------------------


def test_a_job_of_an_unregistered_type_is_made_dead_by_the_claim(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = JobQueue(clock, registry(ENRICH, RETIRED))
    gone = enqueue(units, before, "retire_lead")  # priority 95: the first candidate
    good = enqueue(units, before, "enrich_lead")
    after = JobQueue(clock, registry(ENRICH))

    held = claimed(units, after)

    assert held.job.id == good.job_id
    dead = row(units, gone.job_id)
    assert (dead.status, dead.attempts, dead.lease_until) == ("dead", 1, None)
    assert dead.last_error == "unknown_job_type: job type retire_lead is not registered"
    assert events(db_engine, gone.job_id) == [
        (None, "queued", "enqueued"),
        ("queued", "running", "claimed"),
        ("running", "dead", "failed"),
    ]
    [record] = records(caplog, "job_dead")
    assert (record.levelno, record.job_id, record.code) == (  # type: ignore[attr-defined]
        logging.ERROR,
        gone.job_id,
        "unknown_job_type",
    )


def test_a_job_whose_payload_no_longer_decodes_is_made_dead_by_the_claim(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    garbled = enqueue(units, queue, discriminator="1", run_after=T0 - timedelta(seconds=1))
    good = enqueue(units, queue, discriminator="2")
    query(
        db_engine,
        "UPDATE jobs SET payload = ? WHERE id = ?",
        ('{"v":1,"who":"a@b.co"}', garbled.job_id),
    )

    held = claimed(units, queue)

    assert held.job.id == good.job_id
    dead = row(units, garbled.job_id)
    assert dead.status == "dead"
    assert dead.last_error is not None
    assert dead.last_error.startswith("invalid_job: payload field who")
    assert "a@b.co" not in dead.last_error


def test_a_claim_makes_at_most_ten_unrunnable_jobs_dead(
    units: UnitOfWorkFactory, clock: FrozenClock, db_engine: Engine
) -> None:
    before = JobQueue(clock, registry(ENRICH, RETIRED))
    gone = [enqueue(units, before, "retire_lead", discriminator=str(n)).job_id for n in range(11)]
    good = enqueue(units, before, "enrich_lead")
    after = JobQueue(clock, registry(ENRICH))

    assert claim(units, after) is None
    assert sum(row(units, job_id).status == "dead" for job_id in gone) == 10
    assert claimed(units, after).job.id == good.job_id
    assert all(row(units, job_id).status == "dead" for job_id in gone)


# Row: Purge -------------------------------------------------------------------------------------


def finished(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    clock: FrozenClock,
    job_type: str,
    discriminator: str,
    at: datetime,
    *,
    dead: bool = False,
) -> str:
    clock.set(at)
    created = enqueue(units, queue, job_type, discriminator=discriminator)
    held = claimed(units, queue)
    assert held.job.id == created.job_id
    if dead:
        call(units, queue.fail, held, ValueError("broken"))
    else:
        call(units, queue.complete, held)
    return created.job_id


def test_purge_keeps_recent_rows_each_types_newest_and_every_dead_row(
    units: UnitOfWorkFactory,
    queue: JobQueue,
    clock: FrozenClock,
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    now = T0 + timedelta(days=100)
    a_old = finished(units, queue, clock, "enrich_lead", "1", now - timedelta(days=31))
    a_new = finished(units, queue, clock, "enrich_lead", "2", now - timedelta(days=29))
    b_only = finished(units, queue, clock, "crawl_site", "1", now - timedelta(days=40))
    dead = finished(units, queue, clock, "score_lead", "1", now - timedelta(days=90), dead=True)
    clock.set(now)

    assert call(units, queue.purge_done) == 1

    assert job_ids(db_engine) == {a_new, b_only, dead}
    assert a_old not in job_ids(db_engine)
    [record] = records(caplog, "jobs_purged")
    assert (record.levelno, record.count) == (logging.INFO, 1)  # type: ignore[attr-defined]
    assert call(units, queue.purge_done) == 0
    assert len(records(caplog, "jobs_purged")) == 1  # nothing purged, nothing logged


def test_purge_keeps_the_newest_done_row_even_when_every_one_is_old(
    units: UnitOfWorkFactory, queue: JobQueue, clock: FrozenClock, db_engine: Engine
) -> None:
    at = T0 - timedelta(days=60)
    first = finished(units, queue, clock, "enrich_lead", "1", at)
    second = finished(units, queue, clock, "enrich_lead", "2", at)  # the same updated_at
    clock.set(T0)

    assert call(units, queue.purge_done) == 1
    assert job_ids(db_engine) == {max(first, second)}


def test_purge_keeps_a_row_exactly_30_days_old(
    units: UnitOfWorkFactory, queue: JobQueue, clock: FrozenClock, db_engine: Engine
) -> None:
    edge = finished(units, queue, clock, "enrich_lead", "1", T0 - timedelta(days=30))
    finished(units, queue, clock, "enrich_lead", "2", T0)
    clock.set(T0)

    assert call(units, queue.purge_done) == 0
    assert edge in job_ids(db_engine)


# The index, the checks and the fences -----------------------------------------------------------


def test_the_live_key_index_refuses_a_second_live_row_but_not_one_beside_a_done_row(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    created = enqueue(units, queue)
    key = row(units, created.job_id).idempotency_key
    held = claimed(units, queue)

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert_raw(db_engine, "01JD3QZ8X5V9K2M7N4P6R8T0X1", key, "queued", None)
    call(units, queue.complete, held)
    insert_raw(db_engine, "01JD3QZ8X5V9K2M7N4P6R8T0X1", key, "queued", None)
    insert_raw(db_engine, "01JD3QZ8X5V9K2M7N4P6R8T0X2", key, "dead", None)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert_raw(
            db_engine, "01JD3QZ8X5V9K2M7N4P6R8T0X3", key, "running", "2026-11-17T09:06:00.000000Z"
        )

    assert len(job_ids(db_engine)) == 3


def test_the_live_key_lookup_uses_the_partial_unique_index(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    sent: list[tuple[str, Any]] = []

    def capture(conn: Any, cursor: Any, statement: str, parameters: Any, *rest: Any) -> None:
        if "idempotency_key =" in statement:
            sent.append((statement, parameters))

    event.listen(db_engine, "before_cursor_execute", capture)
    try:
        with units.open(read_only=True):
            assert jobs_repo.live_job(f"enrich_lead:{LEAD}:1") is None
    finally:
        event.remove(db_engine, "before_cursor_execute", capture)

    [(statement, parameters)] = sent
    plan = query(db_engine, f"EXPLAIN QUERY PLAN {statement}", tuple(parameters))
    assert any("uq_jobs_live_key" in str(detail) for *_, detail in plan), plan
    assert LIVE_WHERE == "status IN ({})".format(
        ",".join(f"'{status}'" for status in LIVE_STATUSES)
    )


@pytest.mark.parametrize(
    "change",
    [
        "status = 'paused'",
        "lane = 'fast'",
        "attempts = -1",
        "priority = 101",
        "priority = -1",
        "status = 'running'",  # running without a lease
        "lease_until = '2026-11-17T09:06:00.000000Z'",  # queued with a lease
    ],
)
def test_the_checks_refuse_an_impossible_row(
    change: str, units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    created = enqueue(units, queue)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        query(db_engine, f"UPDATE jobs SET {change} WHERE id = ?", (created.job_id,))


def test_the_fence_needs_the_claims_attempts(
    units: UnitOfWorkFactory, queue: JobQueue, db_engine: Engine
) -> None:
    created = enqueue(units, queue)
    held = claimed(units, queue)
    job = held.job
    wrong = Claim(Job(job.id, job.type, job.lane, job.payload, 2), held.max_runtime)

    assert call(units, queue.complete, wrong) is False
    assert call(units, queue.fail, wrong, ValueError("x")) is None
    assert call(units, queue.keep, (wrong,)) == Kept((wrong,), (), ())
    assert call(units, queue.release, (wrong,)) == ()
    assert row(units, created.job_id).status == "running"
    assert call(units, queue.complete, held) is True
    assert call(units, queue.complete, held) is False  # done now: a second finish is lost


# error_text and the clock -----------------------------------------------------------------------


class _Unprintable(Exception):
    def __str__(self) -> str:
        raise RuntimeError("no")


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (AdapterError(True, "rate_limited", "slow down"), "rate_limited: slow down"),
        (InvalidJob("the payload has no version field v"),
         "invalid_job: the payload has no version field v"),
        (ValueError(f"no {PLANTED} here"), "ValueError: no <email> here"),
        (KeyError("k"), "KeyError: 'k'"),
        (_Unprintable(), "_Unprintable: (the error could not be shown)"),
        (ValueError("token=abc123"), "ValueError: token=<redacted>"),
    ],
)
def test_error_text_is_code_and_message_redacted(error: Exception, text: str) -> None:
    assert error_text(error) == text


def nested() -> type[Exception]:
    class Nested(Exception):
        pass

    return Nested


def test_error_text_uses_the_qualified_name_and_stops_at_500_characters() -> None:
    assert error_text(nested()("x")) == "nested.<locals>.Nested: x"
    assert len(error_text(ValueError("word " * 400))) == 500


class CountingClock(FrozenClock):
    def __init__(self) -> None:
        super().__init__(T0)
        self.reads = 0

    def now(self) -> datetime:
        self.reads += 1
        return super().now()


@pytest.fixture
def counting() -> Iterator[CountingClock]:
    yield CountingClock()


def test_each_call_reads_the_clock_once(units: UnitOfWorkFactory, counting: CountingClock) -> None:
    queue = JobQueue(counting, registry())
    reads: list[int] = []

    def measure(action: Callable[[], object]) -> None:
        start = counting.reads
        action()
        reads.append(counting.reads - start)

    measure(lambda: enqueue(units, queue, discriminator="1"))
    measure(lambda: enqueue(units, queue, discriminator="2"))
    held: list[Claim] = []
    measure(lambda: held.append(claimed(units, queue)))
    measure(lambda: call(units, queue.keep, tuple(held)))
    measure(lambda: call(units, queue.fail, held[0], ValueError("x")))
    second = claimed(units, queue)
    measure(lambda: call(units, queue.complete, second))
    measure(lambda: call(units, queue.release, ()))
    measure(lambda: call(units, queue.purge_done))

    assert reads == [1] * 8


def test_the_query_returns_none_for_an_unknown_job(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        assert jobqueue_queries.job(LEAD) is None
