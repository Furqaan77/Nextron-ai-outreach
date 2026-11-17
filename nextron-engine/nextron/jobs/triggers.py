"""The closed list of periodic triggers and the trigger registry (AD-10).

It imports only the domain and the standard library.

**Trigger** is AD-10's closed list, in the order of its table. A trigger only enqueues: when it
fires, ``nextron.jobs.scheduler`` enqueues one global ``tick`` job of the job type with the
trigger's own name (``JobQueue.enqueue_tick``), so a trigger can be registered only after that
job type. ``tests/unit/test_triggers.py`` checks the enum against the spine's AD-10 table.

While that job is ``queued`` or ``running``, a firing adds nothing. A periodic job waiting out a
retry backoff is still ``queued``, so the firings meanwhile are absorbed and it retries on its
backoff, not every period: the stories that add periodic job types choose their retry limits
with that in mind.

**Every(period, *, at_startup=False)** is an interval schedule. ``period`` is a ``timedelta``
of whole seconds from 1 minute to 31 days (``TypeError`` or ``ValueError`` otherwise);
``at_startup`` (a ``bool``) fires once right after the scheduler starts too. Its misfire
``grace`` equals the period, so the one coalesced firing that catches up after the PC slept
normally runs; when the wake lands just before a slot, that slot fires instead (see
``nextron.jobs.scheduler``).

Schedules live in memory, so an ``Every`` restarts from zero at each engine start. A period
longer than the engine usually runs between restarts (a day, say, on a PC that is shut down
every night) therefore never fires unless ``at_startup=True``. Daily work wants ``at_startup``
with a handler that skips what is already done, or the calendar schedule (daily at a time,
monthly, Operator time) its story adds; calendar schedules arrive with their first trigger.

**TriggerSpec(trigger, schedule)** is one registration; its ``job_type`` is ``trigger.value``.

**Triggers(job_types)** is the registry. It takes only a frozen ``JobTypes`` (``TypeError``,
``RuntimeError``). ``register_trigger(trigger, schedule)`` raises ``RuntimeError`` after
``freeze()``, ``TypeError`` unless ``trigger`` is a ``Trigger`` member (a plain ``str`` is
refused) and ``schedule`` an ``Every``, ``UnknownJobType`` unless a job type named
``trigger.value`` is registered, and ``ValueError`` for a trigger registered already.
``specs()`` returns the registrations in order. Only ``nextron.main.build_triggers`` builds
one (``tests/contracts/test_job_contracts.py`` checks it).
"""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Final

from nextron.domain.job import JobTypes

MIN_PERIOD: Final = timedelta(minutes=1)
MAX_PERIOD: Final = timedelta(days=31)


class Trigger(StrEnum):
    """AD-10's periodic triggers, in the order of its table."""

    SYNC_MAILBOX = "sync_mailbox"
    SEND_DUE_MESSAGES = "send_due_messages"
    RECONCILE_SENDS = "reconcile_sends"
    POLL_UNSUBSCRIBES = "poll_unsubscribes"
    POLL_AI_BATCHES = "poll_ai_batches"
    EXPIRE_APPROVALS = "expire_approvals"
    REFRESH_DUE_QUEUE = "refresh_due_queue"
    CHECK_DOMAINS = "check_domains"
    RECOMPUTE_TIERS = "recompute_tiers"
    RECHECK_LEADS = "recheck_leads"
    RECONTACT_DUE = "recontact_due"
    APPLY_RETENTION = "apply_retention"
    PURGE_PLACE_COORDS = "purge_place_coords"
    PURGE_PAGE_TEXT = "purge_page_text"
    SUBMIT_BULK_AI = "submit_bulk_ai"
    DAILY_SELECTION = "daily_selection"
    BACKUP_DB = "backup_db"
    RESTORE_TEST = "restore_test"
    CHECK_RECOVERY = "check_recovery"


@dataclass(frozen=True, slots=True)
class Every:
    """Fire every ``period``; with ``at_startup``, also right after the scheduler starts."""

    period: timedelta
    at_startup: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.period, timedelta):
            raise TypeError("a schedule's period must be a timedelta")
        if self.period.microseconds or not MIN_PERIOD <= self.period <= MAX_PERIOD:
            raise ValueError("a schedule's period must be whole seconds from 1 minute to 31 days")
        if not isinstance(self.at_startup, bool):
            raise TypeError("a schedule's at_startup must be a bool")

    @property
    def grace(self) -> timedelta:
        """How late a firing may still run: the period itself."""
        return self.period


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    """One registered trigger and its schedule."""

    trigger: Trigger
    schedule: Every

    def __post_init__(self) -> None:
        if not isinstance(self.trigger, Trigger):
            raise TypeError("a trigger must be a Trigger member")
        if not isinstance(self.schedule, Every):
            raise TypeError(f"trigger {self.trigger}: the schedule must be an Every")

    @property
    def job_type(self) -> str:
        """The job type a firing enqueues: the trigger's own name."""
        return self.trigger.value


class Triggers:
    """The trigger registry (AD-10). Only ``nextron.main.build_triggers`` builds one."""

    def __init__(self, job_types: JobTypes) -> None:
        if not isinstance(job_types, JobTypes):
            raise TypeError("job_types must be a JobTypes registry")
        if not job_types.frozen:
            raise RuntimeError("freeze the job type registry before building the triggers")
        self._job_types = job_types
        self._specs: dict[Trigger, TriggerSpec] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register_trigger(self, trigger: Trigger, schedule: Every) -> None:
        """Schedule ``trigger``; see the module docstring for what it refuses."""
        if self._frozen:
            raise RuntimeError("the trigger registry is frozen")
        if not isinstance(trigger, Trigger):
            raise TypeError(f"{trigger!r} is not a Trigger member")
        if not isinstance(schedule, Every):
            raise TypeError(f"trigger {trigger}: the schedule must be an Every")
        self._job_types.get(trigger.value)  # raises UnknownJobType
        if trigger in self._specs:
            raise ValueError(f"trigger {trigger} is already registered")
        self._specs[trigger] = TriggerSpec(trigger, schedule)

    def freeze(self) -> None:
        """End registration."""
        self._frozen = True

    def specs(self) -> tuple[TriggerSpec, ...]:
        """Every registered trigger, in registration order."""
        return tuple(self._specs.values())
