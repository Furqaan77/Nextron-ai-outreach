"""Public command API of the ``jobqueue`` service (AD-3, AD-4, AD-10, AD-17).

Other services and the delivery layers change ``jobqueue`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``JobQueue(clock, job_types)`` owns the ``jobs`` table. Each call reads the clock once. Every
status change follows the Job machine (``nextron.domain.job.transition``) and appends an
``events`` row in the same transaction: entity ``jobs``, actor ``system:jobqueue``, no
``cause_ref``.

- ``@command enqueue(job_type, *, scope, discriminator, payload, run_after=None, lane=None,
  concurrency_key=None) -> Enqueued(job_id, created)`` runs in the caller's transaction. An
  unregistered type raises ``UnknownJobType``; a lane outside the type's ``lanes``, a bad key,
  payload or concurrency key, or a naive ``run_after`` raises ``InvalidJob``. Either dooms the
  caller's transaction. A live job with the same key gives ``Enqueued(<its id>, False)`` and
  writes nothing; otherwise the job is inserted ``queued`` with no attempts, the type's
  priority, the lane (the type's default unless given) and ``run_after`` (now unless given),
  with its creation event. Without a ``concurrency_key`` the job takes its type's
  ``JobType.concurrency_key`` (Story 4.17), if it has one.
- ``@use_case enqueue_tick(job_type) -> Enqueued``: a periodic trigger's firing
  (``nextron.jobs.scheduler``). In one transaction it calls ``enqueue(job_type,
  scope="global", discriminator="tick", payload={"v": 1})``, with the type's default lane and
  ``run_after`` now, so a live job with that key absorbs the firing (``created=False``).
- ``@use_case claim(lane) -> Claim | None`` takes the next candidate in one transaction: a
  ``queued`` job of the lane with ``run_after <= now`` whose ``concurrency_key`` is ``NULL`` or
  held by no ``running`` job, by ``priority DESC, run_after ASC, id ASC``. The conditional
  ``UPDATE`` must change exactly one row (anything else raises and rolls back); it counts the
  attempt and sets ``lease_until(now, max_runtime)``. A candidate whose type isn't registered,
  or whose payload no longer decodes, is claimed and moved straight to ``dead`` (``fail``),
  with ``last_error`` ``unknown_job_type: ...`` or ``invalid_job: ...``, and the next one is
  tried, up to ``MAX_CLAIM_CANDIDATES`` (10) per call. ``Claim(job, max_runtime)`` is what the
  runner holds.
- ``@use_case complete(claim) -> bool``: ``running`` to ``done``, lease cleared.
- ``@use_case defer(claim) -> bool``: the handler returned ``DEFERRED`` (the budget refused its
  work). ``running`` back to ``queued`` with ``run_after = now + DEFER_DELAY`` (1 h), the lease
  cleared and the attempt given back (``attempts - 1``), so a deferral never uses a retry and
  never makes a job ``dead``; the ``deferred`` event is appended. It logs ``job_deferred``
  (INFO).
- ``@use_case fail(claim, error) -> JobStatus | None``: the error is retryable when it is an
  ``AdapterError`` with ``retryable``; ``after_failure`` picks ``retry`` (``queued``, with
  ``run_after = now + retry_delay(attempts)``), ``fail`` or ``exhaust`` (``dead``). Either way
  the lease is cleared and ``last_error = error_text(error)``.
- ``@use_case keep(held) -> Kept(lost, requeued, dead)``, in one transaction: renews each held
  claim's lease to ``lease_until(now, max_runtime)`` (a miss is ``lost``), then reaps every
  other ``running`` job whose lease ended by now through ``after_interruption(released=False)``:
  back to ``queued`` with ``run_after = now``, or ``dead`` with ``LEASE_EXPIRED_ERROR``. A type
  that is no longer registered counts as retry limit 0.
- ``@use_case release(held) -> tuple[str, ...]``: each held claim follows
  ``after_interruption(released=True)``: ``queued`` (``run_after`` unchanged), or ``dead`` with
  ``RELEASED_ERROR``. It returns the ids it changed.
- ``@use_case purge_done() -> int``: deletes ``done`` rows last changed more than 30 days ago,
  except each type's newest ``done`` row; never a ``dead`` one. It returns the count.
- ``@use_case dismiss(job_id) -> bool``: the Operator dismisses a ``dead`` job on the health
  panel (AD-10: ``dead`` rows stay until then). In one transaction it reads the job and deletes
  its row only while it is ``dead``. It returns ``True`` only when a ``dead`` row was deleted;
  a missing job, or one that isn't ``dead``, is ``False`` and changes nothing. Like the purge,
  a dismissal is no Job-machine transition, so it appends no ``events`` row (the job's history
  stays there); it logs ``job_dismissed`` (INFO, the job's fields) after the commit. The ``ops``
  dead-job sweep reads only ``dead`` rows, so it never alerts a dismissed job again.

**The fence.** ``complete``, ``defer``, ``fail``, ``keep``'s renewal and ``release`` change a
job only while it is ``running`` with the claim's ``attempts``. A run whose claim was lost
(reaped or released meanwhile) changes nothing: ``complete`` and ``defer`` return ``False``,
``fail`` ``None``, and all three log ``job_claim_lost``.

**error_text(error)** is ``"<code>: <message>"`` for an ``AdapterError`` or ``DomainError`` and
``"<type qualname>: <str(error)>"`` for anything else, then ``redact``-ed and cut to 500
characters.

**Logs** carry ``job_id``, ``job_type``, ``lane``, ``attempts`` and ``code`` (the error's code,
or else its class name), never a payload value or ``last_error`` text, and are written after
the transaction commits: ``job_retry_scheduled`` (WARNING), ``job_dead`` (ERROR, every move to
``dead``), ``job_claim_lost``, ``job_lease_expired`` and ``job_released`` (WARNING),
``job_deferred`` (INFO), ``job_dismissed`` (INFO) and ``jobs_purged`` (INFO, with ``count``,
only when it is above 0).

**Erasure (Story 3.13).** ``ERASURE`` declares ``jobs`` ``not_held``: payloads and keys hold IDs
and scalars only (AD-10), and ``last_error`` is a redacted, truncated exception message that by
convention holds codes, not values (AD-17, Story 1.2). The module-level ``@command
erase_pii(n: ContactErased)`` is jobqueue's ``ContactErased`` subscriber
(``nextron.main.build_subscribers``); it writes nothing.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final

from nextron.db import eventlog
from nextron.db.repos.jobqueue import jobs as jobs_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.erasure import Coverage, TableErasure
from nextron.domain.errors import DomainError
from nextron.domain.job import (
    CREATION,
    DEFER_DELAY,
    DONE_RETENTION,
    GLOBAL_SCOPE,
    MAX_ERROR_LENGTH,
    TICK,
    InvalidJob,
    Job,
    JobCause,
    JobEvent,
    JobStatus,
    JobTypes,
    Lane,
    UnknownJobType,
    after_failure,
    after_interruption,
    decode_payload,
    encode_payload,
    idempotency_key,
    is_concurrency_key,
    lease_until,
    retry_delay,
    transition,
)
from nextron.domain.machines import Transition
from nextron.domain.notifications import ContactErased
from nextron.domain.redaction import redact
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError

ENTITY: Final = "jobs"
ACTOR: Final = "system:jobqueue"
MAX_CLAIM_CANDIDATES: Final = 10
LEASE_EXPIRED: Final = "lease_expired"
RELEASED: Final = "released"
LEASE_EXPIRED_ERROR: Final = f"{LEASE_EXPIRED}: the lease expired before the job finished"
RELEASED_ERROR: Final = f"{RELEASED}: the engine stopped before the job finished"
UNRUNNABLE_RUNTIME: Final = timedelta(0)  # an unrunnable job's lease lasts only its transaction
TICK_PAYLOAD: Final[Mapping[str, object]] = MappingProxyType({"v": 1})

JOB_RETRY_SCHEDULED: Final = "job_retry_scheduled"
JOB_DEAD: Final = "job_dead"
JOB_CLAIM_LOST: Final = "job_claim_lost"
JOB_LEASE_EXPIRED: Final = "job_lease_expired"
JOB_RELEASED: Final = "job_released"
JOB_DEFERRED: Final = "job_deferred"
JOBS_PURGED: Final = "jobs_purged"
JOB_DISMISSED: Final = "job_dismissed"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Enqueued:
    """The live job for the key, and whether this call created it."""

    job_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class Claim:
    """A claimed job and its type's ``max_runtime``, held by the runner while it runs."""

    job: Job
    max_runtime: timedelta


@dataclass(frozen=True, slots=True)
class Kept:
    """What a keeper tick did: the held claims it couldn't renew, and the ids of the expired
    jobs it put back in the queue or made ``dead``."""

    lost: tuple[Claim, ...]
    requeued: tuple[str, ...]
    dead: tuple[str, ...]


def error_code(error: BaseException) -> str:
    """The code a log record carries for ``error``: its own code, or else its class name."""
    if isinstance(error, AdapterError | DomainError):
        return error.code
    return type(error).__name__


def _described(error: BaseException) -> str:
    try:
        return str(error)
    except Exception:  # noqa: BLE001 - a broken __str__ must not stop the job being recorded
        return "(the error could not be shown)"


def error_text(error: BaseException) -> str:
    """``last_error`` for ``error``; see the module docstring."""
    if isinstance(error, AdapterError | DomainError):
        text = f"{error.code}: {error.message}"
    else:
        text = f"{type(error).__qualname__}: {_described(error)}"
    return redact(text)[:MAX_ERROR_LENGTH]


# A log record to write once the transaction has committed: (level, event, extra).
type _Record = tuple[int, str, dict[str, object]]


def _fields(
    job_id: str, job_type: str, lane: str, attempts: int, code: str | None = None
) -> dict[str, object]:
    extra: dict[str, object] = {
        "job_id": job_id,
        "job_type": job_type,
        "lane": lane,
        "attempts": attempts,
    }
    if code is not None:
        extra["code"] = code
    return extra


def _job_fields(job: Job, code: str | None = None) -> dict[str, object]:
    return _fields(job.id, job.type, job.lane, job.attempts, code)


def _write(records: Iterable[_Record]) -> None:
    for level, event, extra in records:
        log.log(level, event, extra=extra)


def _record(
    job_id: str,
    from_status: JobStatus | None,
    change: Transition[JobStatus, JobCause],
    at: datetime,
) -> None:
    eventlog.append(
        entity=ENTITY,
        entity_id=job_id,
        from_status=None if from_status is None else str(from_status),
        to_status=str(change.to),
        cause=str(change.cause),
        cause_ref=None,
        actor=ACTOR,
        at=at,
    )


def _move_running(
    job_id: str, attempts: int, event: JobEvent, at: datetime, **changes: object
) -> Transition[JobStatus, JobCause] | None:
    """Apply ``event`` to the ``running`` job ``job_id`` claimed with ``attempts`` (the fence),
    with ``changes``, and record it; ``None`` when the fence missed."""
    change = transition(JobStatus.RUNNING, event)
    if change.to is not JobStatus.RUNNING:
        changes.setdefault("lease_until", None)
    count = jobs_repo.update_running(
        job_id, attempts, at=at, status=str(change.to), **changes
    )
    if count != 1:
        return None
    _record(job_id, JobStatus.RUNNING, change, at)
    return change


class JobQueue:
    """Durable jobs: enqueue, claim, finish, keep leases, release, purge and dismiss."""

    def __init__(self, clock: Clock, job_types: JobTypes) -> None:
        if not isinstance(job_types, JobTypes):
            raise TypeError("job_types must be a JobTypes registry")
        self._clock = clock
        self._job_types = job_types

    def _retry_limit(self, job_type: str) -> int:
        try:
            return self._job_types.get(job_type).retry_limit
        except UnknownJobType:
            return 0  # a type no longer registered never runs again

    # Enqueue ---------------------------------------------------------------------------------

    @command
    def enqueue(
        self,
        job_type: str,
        *,
        scope: str,
        discriminator: str,
        payload: Mapping[str, object],
        run_after: datetime | None = None,
        lane: Lane | None = None,
        concurrency_key: str | None = None,
    ) -> Enqueued:
        """Enqueue a job in the caller's transaction, or find the live one with its key."""
        now = self._clock.now()
        found = self._job_types.get(job_type)
        chosen = found.default_lane if lane is None else lane
        if not isinstance(chosen, str) or chosen not in found.lanes:
            raise InvalidJob(f"job type {found.name} does not run on that lane")
        key = idempotency_key(found.name, scope, discriminator)
        text = encode_payload(payload)
        if concurrency_key is None:
            concurrency_key = found.concurrency_key  # Story 4.17: the type's own key, if any
        elif not is_concurrency_key(concurrency_key):
            raise InvalidJob("the concurrency key must be inbox:<ULID> or domain:<name>")
        if run_after is not None and not (
            isinstance(run_after, datetime) and run_after.utcoffset() is not None
        ):
            raise InvalidJob("run_after must be an aware datetime")
        live = jobs_repo.live_job(key)
        if live is not None:
            return Enqueued(live.id, False)
        job_id = jobs_repo.insert_job(
            job_type=found.name,
            lane=str(Lane(chosen)),
            status=str(CREATION.to),
            priority=found.priority,
            payload=text,
            idempotency_key=key,
            concurrency_key=concurrency_key,
            run_after=now if run_after is None else run_after,
            at=now,
        )
        _record(job_id, None, CREATION, now)
        return Enqueued(job_id, True)

    @use_case
    def enqueue_tick(self, job_type: str) -> Enqueued:
        """Enqueue a periodic trigger's global ``tick`` job of ``job_type``, or find the live
        one."""
        with current().transaction():
            return self.enqueue(
                job_type, scope=GLOBAL_SCOPE, discriminator=TICK, payload=TICK_PAYLOAD
            )

    # Claim -----------------------------------------------------------------------------------

    @staticmethod
    def _claim_row(row: jobs_repo.JobRow, now: datetime, max_runtime: timedelta) -> None:
        change = transition(JobStatus.QUEUED, JobEvent.CLAIM)
        count = jobs_repo.claim(row.id, lease_until=lease_until(now, max_runtime), at=now)
        if count != 1:
            raise RuntimeError(f"claiming job {row.id} changed {count} rows; expected 1")
        _record(row.id, JobStatus.QUEUED, change, now)

    @use_case
    def claim(self, lane: Lane) -> Claim | None:
        """Claim the next runnable job of ``lane``, or ``None`` when there is none."""
        now = self._clock.now()
        lane = Lane(lane)
        records: list[_Record] = []
        claimed: Claim | None = None
        with current().transaction():
            for _ in range(MAX_CLAIM_CANDIDATES):
                row = jobs_repo.next_candidate(str(lane), now)
                if row is None:
                    break
                attempts = row.attempts + 1
                try:
                    found = self._job_types.get(row.type)
                    payload = decode_payload(row.payload)
                except (UnknownJobType, InvalidJob) as unrunnable:
                    self._claim_row(row, now, UNRUNNABLE_RUNTIME)
                    if _move_running(
                        row.id,
                        attempts,
                        JobEvent.FAIL,
                        now,
                        last_error=error_text(unrunnable),
                    ) is None:
                        raise RuntimeError(f"job {row.id} changed while it was claimed") from None
                    fields = _fields(row.id, row.type, lane, attempts, unrunnable.code)
                    records.append((logging.ERROR, JOB_DEAD, fields))
                    continue
                self._claim_row(row, now, found.max_runtime)
                claimed = Claim(Job(row.id, row.type, lane, payload, attempts), found.max_runtime)
                break
        _write(records)
        return claimed

    # Finish ----------------------------------------------------------------------------------

    @use_case
    def complete(self, claim: Claim) -> bool:
        """Mark the claimed job ``done``; ``False`` when the claim was lost."""
        now = self._clock.now()
        job = claim.job
        with current().transaction():
            change = _move_running(job.id, job.attempts, JobEvent.SUCCEED, now)
        if change is None:
            log.warning(JOB_CLAIM_LOST, extra=_job_fields(job))
            return False
        return True

    @use_case
    def defer(self, claim: Claim) -> bool:
        """Put the claimed job back in the queue ``DEFER_DELAY`` from now, with its attempt
        given back; ``False`` when the claim was lost."""
        now = self._clock.now()
        job = claim.job
        change = transition(JobStatus.RUNNING, JobEvent.DEFER)
        with current().transaction():
            count = jobs_repo.defer_running(
                job.id, job.attempts, run_after=now + DEFER_DELAY, at=now
            )
            if count == 1:
                _record(job.id, JobStatus.RUNNING, change, now)
        if count != 1:
            log.warning(JOB_CLAIM_LOST, extra=_job_fields(job))
            return False
        log.info(JOB_DEFERRED, extra=_job_fields(job))
        return True

    @use_case
    def fail(self, claim: Claim, error: BaseException) -> JobStatus | None:
        """Record that the claimed job's run raised ``error``: back to ``queued`` for a retry,
        or ``dead``. ``None`` when the claim was lost."""
        now = self._clock.now()
        job = claim.job
        retryable = isinstance(error, AdapterError) and error.retryable
        event = after_failure(job.attempts, self._retry_limit(job.type), retryable=retryable)
        changes: dict[str, object] = {"last_error": error_text(error)}
        if event is JobEvent.RETRY:
            changes["run_after"] = now + retry_delay(job.attempts)
        with current().transaction():
            change = _move_running(job.id, job.attempts, event, now, **changes)
        if change is None:
            log.warning(JOB_CLAIM_LOST, extra=_job_fields(job))
            return None
        fields = _job_fields(job, error_code(error))
        if change.to is JobStatus.QUEUED:
            log.warning(JOB_RETRY_SCHEDULED, extra=fields)
        else:
            log.error(JOB_DEAD, extra=fields)
        return change.to

    # Leases ----------------------------------------------------------------------------------

    @use_case
    def keep(self, held: Iterable[Claim]) -> Kept:
        """Renew the held claims' leases, then reap every other expired lease."""
        now = self._clock.now()
        lost: list[Claim] = []
        renewed: set[str] = set()
        requeued: list[str] = []
        dead: list[str] = []
        records: list[_Record] = []
        with current().transaction():
            for claim in held:
                job = claim.job
                count = jobs_repo.update_running(
                    job.id, job.attempts, at=now, lease_until=lease_until(now, claim.max_runtime)
                )
                if count == 1:
                    renewed.add(job.id)
                else:
                    lost.append(claim)
            for row in jobs_repo.expired(now, exclude=renewed):
                event = after_interruption(
                    row.attempts, self._retry_limit(row.type), released=False
                )
                changes: dict[str, object] = (
                    {"run_after": now}
                    if event is JobEvent.EXPIRE
                    else {"last_error": LEASE_EXPIRED_ERROR}
                )
                change = _move_running(row.id, row.attempts, event, now, **changes)
                if change is None:
                    continue
                fields = _fields(row.id, row.type, row.lane, row.attempts, LEASE_EXPIRED)
                records.append((logging.WARNING, JOB_LEASE_EXPIRED, fields))
                if change.to is JobStatus.DEAD:
                    dead.append(row.id)
                    records.append((logging.ERROR, JOB_DEAD, fields))
                else:
                    requeued.append(row.id)
        _write(records)
        return Kept(tuple(lost), tuple(requeued), tuple(dead))

    @use_case
    def release(self, held: Iterable[Claim]) -> tuple[str, ...]:
        """Hand the held claims back when the engine stops: ``queued``, or ``dead`` at the last
        attempt. Returns the ids it changed."""
        now = self._clock.now()
        changed: list[str] = []
        records: list[_Record] = []
        with current().transaction():
            for claim in held:
                job = claim.job
                event = after_interruption(
                    job.attempts, self._retry_limit(job.type), released=True
                )
                changes: dict[str, object] = (
                    {} if event is JobEvent.RELEASE else {"last_error": RELEASED_ERROR}
                )
                change = _move_running(job.id, job.attempts, event, now, **changes)
                if change is None:
                    continue
                changed.append(job.id)
                fields = _job_fields(job, RELEASED)
                records.append((logging.WARNING, JOB_RELEASED, fields))
                if change.to is JobStatus.DEAD:
                    records.append((logging.ERROR, JOB_DEAD, fields))
        _write(records)
        return tuple(changed)

    # Maintenance -----------------------------------------------------------------------------

    @use_case
    def purge_done(self) -> int:
        """Delete ``done`` jobs older than 30 days, keeping each type's newest; the count."""
        now = self._clock.now()
        with current().transaction():
            count = jobs_repo.purge_done(now - DONE_RETENTION)
        if count > 0:
            log.info(JOBS_PURGED, extra={"count": count})
        return count

    @use_case
    def dismiss(self, job_id: str) -> bool:
        """Delete the ``dead`` job ``job_id``; ``False`` (and nothing changed) when it is missing
        or not ``dead``."""
        with current().transaction():
            row = jobs_repo.get(job_id)
            if row is None or row.status != str(JobStatus.DEAD):
                return False
            if jobs_repo.delete_dead(job_id) != 1:
                return False
        log.info(JOB_DISMISSED, extra=_fields(row.id, row.type, row.lane, row.attempts))
        return True


# Erasure (Story 3.13) -------------------------------------------------------------------------

# What jobqueue's erase_pii does to each of its tables with a personal-data column: none of them
# ever holds a Contact's data.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "jobs": TableErasure(
            Coverage.NOT_HELD,
            "payloads and keys hold IDs and scalars only (AD-10); last_error is a redacted, "
            "truncated exception message that by convention holds codes, not values (AD-17, "
            "Story 1.2)",
        ),
    }
)


@command
def erase_pii(n: ContactErased) -> None:
    """``jobqueue``'s ``ContactErased`` subscriber (Story 3.13, AD-17): it writes nothing,
    because no ``jobqueue`` table holds a Contact's data (``ERASURE``)."""
