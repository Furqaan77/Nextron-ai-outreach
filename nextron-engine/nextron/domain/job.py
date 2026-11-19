"""The Job machine and the rules of durable jobs (AD-4, AD-10, AD-17). Standard library only.

**Statuses, events and causes.** A job is ``queued``, ``running``, ``done`` or ``dead``;
``LIVE_STATUSES`` are ``queued`` and ``running``, the ones the live-key index covers.
``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``; a creation has no ``from`` status and cause ``enqueued`` (``CREATION``).

=========  ===================  =========  ===================
From       Event                To         Cause
=========  ===================  =========  ===================
queued     claim                running    claimed
running    succeed              done       succeeded
running    retry                queued     retry_scheduled
running    fail                 dead       failed
running    exhaust              dead       retries_exhausted
running    expire               queued     lease_expired
running    expire_exhausted     dead       lease_expired
running    release              queued     released
running    release_exhausted    dead       released
running    defer                queued     deferred
=========  ===================  =========  ===================

**Leases and retries.** ``attempts`` counts claims, except deferred ones (a deferral gives its
attempt back), so a run cut off by an expired lease or a shutdown counts too.

- ``lease_until(now, max_runtime)`` is ``now + max_runtime + LEASE_MARGIN`` (60 s); the keeper
  renews held leases every ``RENEW_INTERVAL`` (30 s).
- ``retry_delay(attempts)`` is ``min(30 s × 2^(attempts − 1), 30 min)``: 30, 60, 120 ... 1800 s.
- ``after_failure(attempts, retry_limit, *, retryable)``: not retryable is ``fail``; past the
  limit (``attempts > retry_limit``) is ``exhaust``; otherwise ``retry``.
- ``after_interruption(attempts, retry_limit, *, released)``: past the limit is
  ``release_exhausted`` (``released``) or ``expire_exhausted``; otherwise ``release`` or
  ``expire``. A type with retry limit 0 therefore never runs twice, except that it runs again
  after each ``DEFERRED``.

**Deferral.** A handler whose work the budget refuses returns ``DEFERRED``
(``HandlerResult.DEFERRED``). The runner then finishes the job with ``defer``: back to
``queued``, ``DEFER_DELAY`` (1 h) later, with its attempt given back, so a deferral never uses a
retry and never makes a job ``dead``. A deferred job runs again from the start, so a handler
returns ``DEFERRED`` only before its use case has done any work: the budget check comes first.

**Keys.** ``idempotency_key(job_type, scope, discriminator)`` is
``<type>:<scope>:<discriminator>``. The type is a ``verb_noun`` job-type name; the scope is a
ULID, ``inbox:<ULID>`` or ``global``; the discriminator is ``tick``, a real ``YYYY-MM-DD`` date,
a revision from ``0`` to ``999999999`` without leading zeros, or the value of an AI Test Set
(``nextron.domain.ai_test_gate.TestSet``: ``reply``, ``grounding``, ``drafting_smoke`` or
``extraction``; Story 4.5's ``run_ai_test_sets:<tuple id>:<set>``), all as text.
``is_concurrency_key(value)`` accepts ``inbox:<ULID>`` and ``domain:<name>``, where the name is
at most 253 characters of ``[a-z0-9-]`` labels joined by dots, with at least one dot, and the
global keys in ``GLOBAL_CONCURRENCY_KEYS`` (Story 4.17: ``ai_batches``, which a job type names
as its ``concurrency_key`` so its jobs never run at once).

**Payloads** hold IDs and scalars, never personal data, secrets or free text.
``encode_payload(payload)`` returns compact JSON with sorted keys; ``decode_payload(text)``
returns a read-only mapping. Both raise ``InvalidJob`` unless the payload is a flat JSON object
of at most 32 fields, at most 4,096 UTF-8 bytes once encoded, whose keys fullmatch
``[a-z][a-z0-9_]{0,31}``, with ``v`` an int from 1 to 999, and every value one of: a ULID, an
``int`` (not a ``bool``) within signed 64 bits, a ``bool``, a real ``YYYY-MM-DD`` date, a real
UTC time ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``, or a token fullmatching ``[a-z0-9_:.-]{1,64}``. No
``null``, float, list or object, and a string holding ``@`` or a space fits no rule. A
refusal's reason names the field, never its value.

**Job types.** ``JobType(name, default_lane, lanes, priority, max_runtime, retry_limit,
handler, group=JobGroup.OTHER, concurrency_key=None)`` is checked when it is built (``TypeError`` or
``ValueError``): ``name`` a ``verb_noun`` name of at most 48 characters; ``lanes`` a non-empty
``frozenset`` of ``Lane`` holding ``default_lane``; ``priority`` an int from 0 to 100 (higher
runs first); ``max_runtime`` a ``timedelta`` of whole seconds from 1 s to 24 h;
``retry_limit`` an int from 0 to 20; ``handler`` callable; ``group`` a ``JobGroup``, the
health panel's group for the type (``discovery`` "Discovery", ``enrichment`` "Enrichment",
``recheck`` "Re-check", ``sending`` "Sending", ``reply_check`` "Reply check",
``unsubscribe_poll`` "Unsubscribe Endpoint poll", ``backup`` "Backup" or ``other`` "Other";
``JobGroup.label`` is the heading); ``concurrency_key`` ``None`` or a concurrency key every job
of the type is enqueued with unless the caller names another (Story 4.17). Every registration
names its group
(``tests/contracts/test_health_contracts.py``). A ``bool`` is refused wherever an int is
expected. ``JobTypes``
is the registry: ``register_job_type`` until ``freeze()``, then ``get(name)`` (raising
``UnknownJobType``) and ``names()``. Only ``nextron.main.build_job_types`` builds one.

A handler receives a ``Job(id, type, lane, payload, attempts)``, calls one use case and returns
``None``, or ``DEFERRED`` when the use case was refused by the budget (``Handler``).
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.ai_test_gate import set_of
from nextron.domain.errors import DomainError
from nextron.domain.ids import is_ulid
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "job"

LEASE_MARGIN: Final = timedelta(seconds=60)
RENEW_INTERVAL: Final = timedelta(seconds=30)
FIRST_RETRY_DELAY: Final = timedelta(seconds=30)
MAX_RETRY_DELAY: Final = timedelta(minutes=30)
DONE_RETENTION: Final = timedelta(days=30)
DEFER_DELAY: Final = timedelta(hours=1)  # a job deferred by the budget runs again this much later
MAX_BULK_THREADS: Final = 2
MAX_ERROR_LENGTH: Final = 500

MAX_JOB_TYPE_NAME: Final = 48
MIN_PRIORITY: Final = 0
MAX_PRIORITY: Final = 100
MIN_MAX_RUNTIME: Final = timedelta(seconds=1)
MAX_MAX_RUNTIME: Final = timedelta(hours=24)
MAX_RETRY_LIMIT: Final = 20

VERSION_FIELD: Final = "v"
MIN_VERSION: Final = 1
MAX_VERSION: Final = 999
MAX_PAYLOAD_FIELDS: Final = 32
MAX_PAYLOAD_BYTES: Final = 4096
INT64_MIN: Final = -(2**63)
INT64_MAX: Final = 2**63 - 1

GLOBAL_SCOPE: Final = "global"
TICK: Final = "tick"
INBOX_PREFIX: Final = "inbox:"
DOMAIN_PREFIX: Final = "domain:"
GLOBAL_CONCURRENCY_KEYS: Final = frozenset({"ai_batches"})  # Story 4.17: a job type's own key
MAX_DOMAIN_NAME: Final = 253

# Each matched with fullmatch.
JOB_TYPE_NAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
REVISION_PATTERN: Final = re.compile(r"0|[1-9][0-9]{0,8}")
PAYLOAD_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
TOKEN_PATTERN: Final = re.compile(r"[a-z0-9_:.-]{1,64}")
DOMAIN_NAME_PATTERN: Final = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UTC_TIME_PATTERN: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?Z"
)
_MAX_DOUBLINGS: Final = 16  # far past the cap; keeps the arithmetic small


# The machine ----------------------------------------------------------------------------------


class Lane(StrEnum):
    """The runner's lanes; each has its own worker threads."""

    REALTIME = "realtime"
    INTERACTIVE = "interactive"
    BULK = "bulk"


class JobGroup(StrEnum):
    """The health panel's groups of job types (FR-58), in the order it shows them; ``label`` is
    the group's heading."""

    DISCOVERY = "discovery"
    ENRICHMENT = "enrichment"
    RECHECK = "recheck"
    SENDING = "sending"
    REPLY_CHECK = "reply_check"
    UNSUBSCRIBE_POLL = "unsubscribe_poll"
    BACKUP = "backup"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _GROUP_LABELS[self]


_GROUP_LABELS: Final[Mapping[JobGroup, str]] = MappingProxyType(
    {
        JobGroup.DISCOVERY: "Discovery",
        JobGroup.ENRICHMENT: "Enrichment",
        JobGroup.RECHECK: "Re-check",
        JobGroup.SENDING: "Sending",
        JobGroup.REPLY_CHECK: "Reply check",
        JobGroup.UNSUBSCRIBE_POLL: "Unsubscribe Endpoint poll",
        JobGroup.BACKUP: "Backup",
        JobGroup.OTHER: "Other",
    }
)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    DEAD = "dead"


LIVE_STATUSES: Final = (JobStatus.QUEUED, JobStatus.RUNNING)


class JobEvent(StrEnum):
    CLAIM = "claim"
    SUCCEED = "succeed"
    RETRY = "retry"
    FAIL = "fail"
    EXHAUST = "exhaust"
    EXPIRE = "expire"
    EXPIRE_EXHAUSTED = "expire_exhausted"
    RELEASE = "release"
    RELEASE_EXHAUSTED = "release_exhausted"
    DEFER = "defer"


class JobCause(StrEnum):
    ENQUEUED = "enqueued"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    RETRIES_EXHAUSTED = "retries_exhausted"
    LEASE_EXPIRED = "lease_expired"
    RELEASED = "released"
    DEFERRED = "deferred"


CREATION: Final[Transition[JobStatus, JobCause]] = Transition(JobStatus.QUEUED, JobCause.ENQUEUED)

TRANSITIONS: Final[Mapping[tuple[JobStatus, JobEvent], Transition[JobStatus, JobCause]]] = (
    MappingProxyType(
        {
            (JobStatus.QUEUED, JobEvent.CLAIM): Transition(JobStatus.RUNNING, JobCause.CLAIMED),
            (JobStatus.RUNNING, JobEvent.SUCCEED): Transition(
                JobStatus.DONE, JobCause.SUCCEEDED
            ),
            (JobStatus.RUNNING, JobEvent.RETRY): Transition(
                JobStatus.QUEUED, JobCause.RETRY_SCHEDULED
            ),
            (JobStatus.RUNNING, JobEvent.FAIL): Transition(JobStatus.DEAD, JobCause.FAILED),
            (JobStatus.RUNNING, JobEvent.EXHAUST): Transition(
                JobStatus.DEAD, JobCause.RETRIES_EXHAUSTED
            ),
            (JobStatus.RUNNING, JobEvent.EXPIRE): Transition(
                JobStatus.QUEUED, JobCause.LEASE_EXPIRED
            ),
            (JobStatus.RUNNING, JobEvent.EXPIRE_EXHAUSTED): Transition(
                JobStatus.DEAD, JobCause.LEASE_EXPIRED
            ),
            (JobStatus.RUNNING, JobEvent.RELEASE): Transition(JobStatus.QUEUED, JobCause.RELEASED),
            (JobStatus.RUNNING, JobEvent.RELEASE_EXHAUSTED): Transition(
                JobStatus.DEAD, JobCause.RELEASED
            ),
            (JobStatus.RUNNING, JobEvent.DEFER): Transition(JobStatus.QUEUED, JobCause.DEFERRED),
        }
    )
)


def transition(current: JobStatus, event: JobEvent) -> Transition[JobStatus, JobCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


# Errors ---------------------------------------------------------------------------------------


class UnknownJobType(DomainError):
    """A job type the registry doesn't hold."""

    code = "unknown_job_type"

    def __init__(self, job_type: object) -> None:
        named = is_job_type_name(job_type)
        shown = job_type if named else "(not a job type name)"
        super().__init__(f"job type {shown} is not registered")
        self.job_type = str(job_type) if named else None


class InvalidJob(DomainError):
    """A job the queue refuses; ``reason`` names what is wrong, never a value."""

    code = "invalid_job"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Leases and retries ---------------------------------------------------------------------------


def lease_until(now: datetime, max_runtime: timedelta) -> datetime:
    """When a lease taken (or renewed) at ``now`` for a job of ``max_runtime`` expires."""
    return now + max_runtime + LEASE_MARGIN


def _check_attempts(attempts: object) -> int:
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise TypeError("attempts must be an int")
    if attempts < 1:
        raise ValueError("attempts counts claims, so it is at least 1")
    return attempts


def retry_delay(attempts: int) -> timedelta:
    """How long a job waits before its next try after its ``attempts``-th run failed."""
    doublings = min(_check_attempts(attempts) - 1, _MAX_DOUBLINGS)
    return min(FIRST_RETRY_DELAY * 2**doublings, MAX_RETRY_DELAY)


def after_failure(attempts: int, retry_limit: int, *, retryable: bool) -> JobEvent:
    """The event for a run that raised: ``fail``, ``exhaust`` or ``retry``."""
    if not retryable:
        return JobEvent.FAIL
    if _check_attempts(attempts) > retry_limit:
        return JobEvent.EXHAUST
    return JobEvent.RETRY


def after_interruption(attempts: int, retry_limit: int, *, released: bool) -> JobEvent:
    """The event for a run cut off by a shutdown (``released``) or an expired lease."""
    if _check_attempts(attempts) > retry_limit:
        return JobEvent.RELEASE_EXHAUSTED if released else JobEvent.EXPIRE_EXHAUSTED
    return JobEvent.RELEASE if released else JobEvent.EXPIRE


# Keys -----------------------------------------------------------------------------------------


def is_job_type_name(value: object) -> bool:
    """Whether ``value`` is a ``verb_noun`` job-type name of at most 48 characters."""
    return (
        isinstance(value, str)
        and len(value) <= MAX_JOB_TYPE_NAME
        and JOB_TYPE_NAME_PATTERN.fullmatch(value) is not None
    )


def is_iso_date(value: object) -> bool:
    """Whether ``value`` is a real calendar date as ``YYYY-MM-DD`` text."""
    if not (isinstance(value, str) and _DATE_PATTERN.fullmatch(value)):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_utc_time(value: object) -> bool:
    """Whether ``value`` is a real UTC time as ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z`` text."""
    if not (isinstance(value, str) and _UTC_TIME_PATTERN.fullmatch(value)):
        return False
    try:
        datetime.fromisoformat(value[:-1])
    except ValueError:
        return False
    return True


def _is_scope(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if value == GLOBAL_SCOPE or is_ulid(value):
        return True
    return value.startswith(INBOX_PREFIX) and is_ulid(value.removeprefix(INBOX_PREFIX))


def _is_discriminator(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return (
        value == TICK
        or is_iso_date(value)
        or REVISION_PATTERN.fullmatch(value) is not None
        or set_of(value) is not None
    )


def idempotency_key(job_type: str, scope: str, discriminator: str) -> str:
    """``<type>:<scope>:<discriminator>``; raises ``InvalidJob`` for a part of the wrong shape."""
    if not is_job_type_name(job_type):
        raise InvalidJob("the job type must be a verb_noun name of at most 48 characters")
    if not _is_scope(scope):
        raise InvalidJob("the key's scope must be a ULID, inbox:<ULID> or global")
    if not _is_discriminator(discriminator):
        raise InvalidJob(
            "the key's discriminator must be tick, a YYYY-MM-DD date, a revision from 0 to "
            "999999999 or an AI Test Set"
        )
    return f"{job_type}:{scope}:{discriminator}"


def is_concurrency_key(value: object) -> bool:
    """Whether ``value`` is ``inbox:<ULID>`` or ``domain:<name>``."""
    if not isinstance(value, str):
        return False
    if value in GLOBAL_CONCURRENCY_KEYS:
        return True
    if value.startswith(INBOX_PREFIX):
        return is_ulid(value.removeprefix(INBOX_PREFIX))
    if value.startswith(DOMAIN_PREFIX):
        name = value.removeprefix(DOMAIN_PREFIX)
        return len(name) <= MAX_DOMAIN_NAME and DOMAIN_NAME_PATTERN.fullmatch(name) is not None
    return False


# Payloads -------------------------------------------------------------------------------------


def _is_scalar_text(value: str) -> bool:
    return (
        is_ulid(value)
        or is_iso_date(value)
        or is_utc_time(value)
        or TOKEN_PATTERN.fullmatch(value) is not None
    )


def _value_problem(key: str, value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if INT64_MIN <= value <= INT64_MAX:
            return None
        return f"payload field {key} must be an integer within signed 64 bits"
    if isinstance(value, str):
        if _is_scalar_text(value):
            return None
        return (
            f"payload field {key} must be a ULID, a YYYY-MM-DD date, a UTC time or a token of "
            "[a-z0-9_:.-]"
        )
    return (
        f"payload field {key} must be a ULID, an integer, a boolean, a date, a UTC time or a "
        "token"
    )


def _check_payload(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise InvalidJob("the payload must be a JSON object")
    if len(payload) > MAX_PAYLOAD_FIELDS:
        raise InvalidJob(f"the payload has more than {MAX_PAYLOAD_FIELDS} fields")
    for key, value in payload.items():
        if not (isinstance(key, str) and PAYLOAD_KEY_PATTERN.fullmatch(key)):
            raise InvalidJob("a payload field name must match [a-z][a-z0-9_]{0,31}")
        problem = _value_problem(key, value)
        if problem is not None:
            raise InvalidJob(problem)
    if VERSION_FIELD not in payload:
        raise InvalidJob("the payload has no version field v")
    version = payload[VERSION_FIELD]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not MIN_VERSION <= version <= MAX_VERSION
    ):
        raise InvalidJob(f"payload field v must be an integer from {MIN_VERSION} to {MAX_VERSION}")
    return payload


def _check_size(text: str) -> None:
    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise InvalidJob(f"the payload exceeds {MAX_PAYLOAD_BYTES:,} bytes")


def encode_payload(payload: Mapping[str, object]) -> str:
    """The payload as compact JSON with sorted keys; raises ``InvalidJob`` (module docstring)."""
    checked = _check_payload(payload)
    text = json.dumps(dict(checked), separators=(",", ":"), sort_keys=True, allow_nan=False)
    _check_size(text)
    return text


class _RepeatedField(ValueError):
    pass


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in pairs:
        if key in fields:
            raise _RepeatedField
        fields[key] = value
    return fields


def decode_payload(text: str) -> Mapping[str, object]:
    """The read-only payload stored as ``text``; raises ``InvalidJob`` (module docstring)."""
    if not isinstance(text, str):
        raise InvalidJob("the payload must be JSON text")
    try:
        _check_size(text)
        value = json.loads(text, object_pairs_hook=_unique_fields)
    except _RepeatedField:
        raise InvalidJob("the payload repeats a field") from None
    except (ValueError, RecursionError):  # JSONDecodeError and a lone surrogate included
        raise InvalidJob("the payload is not valid JSON") from None
    return MappingProxyType(dict(_check_payload(value)))


# Job types ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Job:
    """What a handler receives: the claimed job, its decoded payload and its claim count."""

    id: str
    type: str
    lane: Lane
    payload: Mapping[str, object]
    attempts: int


class HandlerResult(StrEnum):
    """What a handler may return besides ``None`` (done)."""

    DEFERRED = "deferred"  # the budget refused the work: run it again DEFER_DELAY later


DEFERRED: Final = HandlerResult.DEFERRED

type Handler = Callable[[Job], HandlerResult | None]


def _check_int(name: str, value: object, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"a job type's {name} must be an int")
    if not low <= value <= high:
        raise ValueError(f"a job type's {name} must be from {low} to {high}")


@dataclass(frozen=True, slots=True)
class JobType:
    """One registered kind of job; see the module docstring for the rules."""

    name: str
    default_lane: Lane
    lanes: frozenset[Lane]
    priority: int
    max_runtime: timedelta
    retry_limit: int
    handler: Handler
    group: JobGroup = JobGroup.OTHER
    concurrency_key: str | None = None  # Story 4.17: every job of the type holds this key

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("a job type's name must be a str")
        if not is_job_type_name(self.name):
            raise ValueError(
                "a job type's name must be verb_noun ([a-z][a-z0-9]*(_[a-z0-9]+)+), at most "
                f"{MAX_JOB_TYPE_NAME} characters"
            )
        if not isinstance(self.default_lane, Lane):
            raise TypeError(f"job type {self.name}: default_lane must be a Lane")
        if not isinstance(self.lanes, frozenset):
            raise TypeError(f"job type {self.name}: lanes must be a frozenset of Lane")
        if not all(isinstance(lane, Lane) for lane in self.lanes):
            raise TypeError(f"job type {self.name}: lanes must be a frozenset of Lane")
        if not self.lanes:
            raise ValueError(f"job type {self.name}: lanes must not be empty")
        if self.default_lane not in self.lanes:
            raise ValueError(f"job type {self.name}: lanes must hold default_lane")
        _check_int("priority", self.priority, MIN_PRIORITY, MAX_PRIORITY)
        if not isinstance(self.max_runtime, timedelta):
            raise TypeError(f"job type {self.name}: max_runtime must be a timedelta")
        if self.max_runtime.microseconds or not (
            MIN_MAX_RUNTIME <= self.max_runtime <= MAX_MAX_RUNTIME
        ):
            raise ValueError(
                f"job type {self.name}: max_runtime must be whole seconds from 1 s to 24 h"
            )
        _check_int("retry_limit", self.retry_limit, 0, MAX_RETRY_LIMIT)
        if not callable(self.handler):
            raise TypeError(f"job type {self.name}: handler must be callable")
        if not isinstance(self.group, JobGroup):
            raise TypeError(f"job type {self.name}: group must be a JobGroup")
        if self.concurrency_key is not None and not is_concurrency_key(self.concurrency_key):
            raise ValueError(f"job type {self.name}: concurrency_key must be a concurrency key")


class JobTypes:
    """The job type registry (AD-10). Only ``nextron.main.build_job_types`` builds one."""

    def __init__(self) -> None:
        self._types: dict[str, JobType] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register_job_type(self, job_type: JobType) -> None:
        """Add ``job_type``; raises after ``freeze()``, for a non-``JobType`` and for a name
        that is already registered."""
        if self._frozen:
            raise RuntimeError("the job type registry is frozen")
        if not isinstance(job_type, JobType):
            raise TypeError(f"{job_type!r} is not a JobType")
        if job_type.name in self._types:
            raise ValueError(f"job type {job_type.name} is already registered")
        self._types[job_type.name] = job_type

    def freeze(self) -> None:
        """End registration."""
        self._frozen = True

    def get(self, name: str) -> JobType:
        """The job type registered as ``name``; raises ``UnknownJobType``."""
        found = self._types.get(name) if isinstance(name, str) else None
        if found is None:
            raise UnknownJobType(name)
        return found

    def names(self) -> tuple[str, ...]:
        """Every registered name, in registration order."""
        return tuple(self._types)
