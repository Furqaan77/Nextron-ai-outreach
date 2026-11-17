"""The ``jobs`` table's repository (AD-3, AD-10): the only SQL on it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores what the ``jobqueue`` service gives it and checks nothing; the service
applies the Job machine. Rows come back as ``JobRow``, a frozen copy of every column.

- ``get(job_id)``: the job, or ``None``;
- ``live_job(key)``: the ``queued`` or ``running`` job with that idempotency key, or ``None``;
- ``insert(...)``: a new row; returns its id;
- ``next_candidate(lane, now)``: the next ``queued`` job of the lane with ``run_after <= now``
  whose ``concurrency_key`` is ``NULL`` or held by no ``running`` job, by ``priority DESC,
  run_after ASC, id ASC``;
- ``claim(job_id, ...)``: ``queued`` to ``running``, ``attempts + 1``; ``update_running(job_id,
  attempts, ...)``: changes a job only while it is ``running`` with exactly those attempts (the
  fence). Both return their row count;
- ``defer_running(job_id, attempts, *, run_after, at)``: behind the same fence, back to
  ``queued`` with the attempt given back (``attempts - 1``), no lease and ``run_after``; returns
  its row count;
- ``expired(now, exclude)``: the ``running`` jobs whose lease ended by ``now``;
- ``dead()``: every ``dead`` job, oldest change first;
- ``purge_done(cutoff)``: deletes ``done`` rows last changed before ``cutoff``, except each
  type's newest ``done`` row; returns how many it deleted.
"""

from collections.abc import Collection
from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import Executable, and_, delete, exists, insert, or_, select, text, update
from sqlalchemy.orm import aliased

from nextron.db.models.base import new_id
from nextron.db.models.jobqueue import LIVE_WHERE, JobRecord
from nextron.db.uow import current
from nextron.domain.job import JobStatus

_NO_SYNC = {"synchronize_session": False}


@dataclass(frozen=True, slots=True)
class JobRow:
    """Every column of one ``jobs`` row."""

    id: str
    type: str
    lane: str
    status: str
    priority: int
    payload: str
    idempotency_key: str
    concurrency_key: str | None
    run_after: datetime
    attempts: int
    lease_until: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


_COLUMNS = tuple(getattr(JobRecord, field.name) for field in fields(JobRow))


def _rows(statement: Executable) -> list[JobRow]:
    return [JobRow(*row) for row in current().session.execute(statement)]


def _count(statement: Executable) -> int:
    """Run an ``UPDATE`` or ``DELETE``; how many rows it changed."""
    result = current().session.execute(statement)
    return int(result.rowcount)  # type: ignore[attr-defined]


def _one(statement: Executable) -> JobRow | None:
    rows = _rows(statement)
    return rows[0] if rows else None


def get(job_id: str) -> JobRow | None:
    """The job ``job_id``, or ``None``."""
    return _one(select(*_COLUMNS).where(JobRecord.id == job_id))


def live_job(key: str) -> JobRow | None:
    """The live (``queued`` or ``running``) job with idempotency key ``key``, or ``None``. The
    filter is the partial index's own literal ``WHERE`` (``LIVE_WHERE``): with bound parameters
    instead, SQLite couldn't prove the index applies and wouldn't use ``uq_jobs_live_key``."""
    statement = select(*_COLUMNS).where(JobRecord.idempotency_key == key, text(LIVE_WHERE))
    return _one(statement)


def insert_job(
    *,
    job_type: str,
    lane: str,
    status: str,
    priority: int,
    payload: str,
    idempotency_key: str,
    concurrency_key: str | None,
    run_after: datetime,
    at: datetime,
) -> str:
    """Insert a job with no attempts, lease or error, created and updated at ``at``; return its
    new id."""
    job_id = new_id()
    current().session.execute(
        insert(JobRecord).values(
            id=job_id,
            type=job_type,
            lane=lane,
            status=status,
            priority=priority,
            payload=payload,
            idempotency_key=idempotency_key,
            concurrency_key=concurrency_key,
            run_after=run_after,
            attempts=0,
            lease_until=None,
            last_error=None,
            created_at=at,
            updated_at=at,
        )
    )
    return job_id


def next_candidate(lane: str, now: datetime) -> JobRow | None:
    """The ``queued`` job of ``lane`` to claim next at ``now``, or ``None``."""
    holder = aliased(JobRecord)
    held = exists().where(
        holder.status == str(JobStatus.RUNNING),
        holder.concurrency_key == JobRecord.concurrency_key,
    )
    statement = (
        select(*_COLUMNS)
        .where(
            JobRecord.status == str(JobStatus.QUEUED),
            JobRecord.lane == lane,
            JobRecord.run_after <= now,
            or_(JobRecord.concurrency_key.is_(None), ~held),
        )
        .order_by(JobRecord.priority.desc(), JobRecord.run_after.asc(), JobRecord.id.asc())
        .limit(1)
    )
    return _one(statement)


def claim(job_id: str, *, lease_until: datetime, at: datetime) -> int:
    """Make the ``queued`` job ``job_id`` ``running``, counting the attempt; the row count."""
    statement = (
        update(JobRecord)
        .where(JobRecord.id == job_id, JobRecord.status == str(JobStatus.QUEUED))
        .values(
            status=str(JobStatus.RUNNING),
            attempts=JobRecord.attempts + 1,
            lease_until=lease_until,
            updated_at=at,
        )
        .execution_options(**_NO_SYNC)
    )
    return _count(statement)


def update_running(job_id: str, attempts: int, *, at: datetime, **changes: object) -> int:
    """Set ``changes`` and ``updated_at`` on ``job_id`` only while it is ``running`` with
    exactly ``attempts``; the row count (0 when the claim was lost)."""
    statement = (
        update(JobRecord)
        .where(
            JobRecord.id == job_id,
            JobRecord.status == str(JobStatus.RUNNING),
            JobRecord.attempts == attempts,
        )
        .values(**changes, updated_at=at)
        .execution_options(**_NO_SYNC)
    )
    return _count(statement)


def defer_running(job_id: str, attempts: int, *, run_after: datetime, at: datetime) -> int:
    """Put ``job_id`` back in the queue with its attempt given back, only while it is
    ``running`` with exactly ``attempts``; the row count (0 when the claim was lost)."""
    statement = (
        update(JobRecord)
        .where(
            JobRecord.id == job_id,
            JobRecord.status == str(JobStatus.RUNNING),
            JobRecord.attempts == attempts,
        )
        .values(
            status=str(JobStatus.QUEUED),
            attempts=JobRecord.attempts - 1,
            lease_until=None,
            run_after=run_after,
            updated_at=at,
        )
        .execution_options(**_NO_SYNC)
    )
    return _count(statement)


def expired(now: datetime, exclude: Collection[str] = ()) -> list[JobRow]:
    """The ``running`` jobs, other than ``exclude``, whose lease ended at or before ``now``, by
    ``lease_until`` then id."""
    statement = (
        select(*_COLUMNS)
        .where(JobRecord.status == str(JobStatus.RUNNING), JobRecord.lease_until <= now)
        .order_by(JobRecord.lease_until.asc(), JobRecord.id.asc())
    )
    if exclude:
        statement = statement.where(JobRecord.id.not_in(list(exclude)))
    return _rows(statement)


def dead() -> list[JobRow]:
    """Every ``dead`` job, by ``updated_at`` then id."""
    statement = (
        select(*_COLUMNS)
        .where(JobRecord.status == str(JobStatus.DEAD))
        .order_by(JobRecord.updated_at.asc(), JobRecord.id.asc())
    )
    return _rows(statement)


def purge_done(cutoff: datetime) -> int:
    """Delete the ``done`` jobs last changed before ``cutoff``, keeping each type's newest
    ``done`` row (latest ``updated_at``, then highest id); how many were deleted."""
    newer = aliased(JobRecord)
    has_newer = exists().where(
        newer.type == JobRecord.type,
        newer.status == str(JobStatus.DONE),
        or_(
            newer.updated_at > JobRecord.updated_at,
            and_(newer.updated_at == JobRecord.updated_at, newer.id > JobRecord.id),
        ),
    )
    statement = (
        delete(JobRecord)
        .where(
            JobRecord.status == str(JobStatus.DONE),
            JobRecord.updated_at < cutoff,
            has_newer,
        )
        .execution_options(**_NO_SYNC)
    )
    return _count(statement)
