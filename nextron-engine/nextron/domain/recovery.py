"""Restoring a backup and recovery mode (Story 1.19; AD-2, AD-5, AD-17, AD-20, OP-8): the names,
modes, steps, the step registry, the restore's file-name rules and the backup time. Standard
library only; a function that needs the time takes it.

``nextron restore <file>`` (``nextron.main.restore``) puts a backup copy back while the engine is
stopped and turns recovery mode on: one ``recovery_state`` row with four ``pending`` steps. A
restored database forgets every opt-out, erasure and send made after the copy was taken, so
until every step is ``done`` or ``not_applicable`` sending is held: every send path checks
``nextron.app.queries.ops.recovery_mode()`` in its own transaction.

**Names.** ``CHECK_RECOVERY_JOB`` (``check_recovery``) is the job type, and the AD-10 trigger of
the same name, that runs the recovery steps every ``CHECK_PERIOD`` (1 minute) while recovery mode
is on. ``PRE_RESTORE_DIR_NAME`` (``pre_restore``) is the folder under the backup dir that holds
the safety copy of the database a restore replaced. ``RESTORE_WORK_DIR_NAME`` (``restore``) is
the folder under the data dir where a restore decrypts, checks and upgrades the copy.
``RECOVERY_BANNER`` is the dashboard's banner while recovery mode is on.

**Modes, steps and statuses.**

- ``RecoveryMode``: ``on`` (sending is held), ``off`` (every step settled) or ``superseded`` (a
  later restore replaced it before it ended).
- ``RecoveryStep``, in the order they run: ``reapply_erasure_ledger``, ``mark_oms_unknown``,
  ``reingest_mailboxes`` and ``repoll_unsubscribes``; ``.label`` is "Reapply the erasure
  ledger", "Mark unfinished sends unknown", "Re-ingest the mailboxes" or "Re-poll the
  unsubscribe Worker".
- ``StepStatus``: ``pending``, ``done`` or ``not_applicable``; ``.label`` is "pending", "done"
  or "not applicable". ``settled(statuses)`` is whether every status is ``done`` or
  ``not_applicable``.

**Handlers and the registry.** ``RecoveryContext(recovery_id, backup_at, restored_at)`` (frozen;
both times aware, kept in UTC) is what a step's handler gets: the ``recovery_state`` row's ULID,
the time the restored copy was taken (re-ingest and re-poll from there) and the time of the
restore. ``RecoveryHandler(step, owner, run, startup)`` (frozen) is one owner's handler of one
step: ``run(context) -> bool`` returns ``True`` when the step is done; a ``startup`` handler runs
only at an engine start, before the job runner, and the others from every ``check_recovery``
until they are done, so they must be idempotent and quick (enqueue work, don't do it). Each
field is checked (``TypeError``, ``ValueError``). ``RecoverySteps`` is the registry:
``register_step(handler)`` (``RuntimeError`` once frozen, ``TypeError`` for anything but a
``RecoveryHandler``, ``ValueError`` for a step already registered), ``freeze()``, ``frozen``,
``get(step)`` (the handler or ``None``) and ``registered()`` (a ``frozenset``). Only
``nextron.main.build_recovery_steps`` constructs one or calls ``register_step``
(``tests/contracts/test_recovery_contracts.py``); it ships empty, and Stories 3.12, 3.16 and
6.12 register their handlers there. A step nobody registered becomes ``not_applicable`` at the
next pass. ``RecoveryPass(context, pending)`` (frozen) is one pass's work: the context and the
registered steps still ``pending``, in step order.

**The restore's file names.** A backup copy is named ``nextron-<env>-<YYYYMMDDTHHMMSS.mmm>Z.db``
(Story 1.16) or the same with ``.enc`` (Story 1.17, encrypted).

- ``copy_format(name)`` is ``"encrypted"`` for a name ending ``.db.enc``, ``"plain"`` for one
  ending ``.db`` and ``None`` for any other (``.part`` included), compared casefolded.
- ``copy_env(name)`` is the environment of a name of either shape (casefolded, so
  ``NEXTRON-PROD-...`` is ``prod``), and ``copy_stamp(name)`` its UTC stamp; both are ``None``
  for any other name, and ``copy_stamp`` also for a stamp that isn't a real time (month 13).
- ``backup_time(name, newest_event, restored_at)`` is when the restored copy was taken, at the
  latest: the earliest of ``copy_stamp(name)``, ``newest_event`` (the copy's newest ``events``
  time) and ``restored_at``, skipping a missing one. A renamed file, or a copy taken while the
  engine was idle, therefore re-ingests more, never less.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

CHECK_RECOVERY_JOB: Final = "check_recovery"
CHECK_PERIOD: Final = timedelta(minutes=1)
PRE_RESTORE_DIR_NAME: Final = "pre_restore"
RESTORE_WORK_DIR_NAME: Final = "restore"
RECOVERY_BANNER: Final = (
    "Recovery mode is on: sending is held until every recovery step is done."
)

PLAIN: Final = "plain"
ENCRYPTED: Final = "encrypted"
_PLAIN_SUFFIX: Final = ".db"
_ENCRYPTED_SUFFIX: Final = ".db.enc"
# nextron-<env>-<stamp>.db or .db.enc (nextron.domain.backup's ENV_PATTERN and stamp), any case
_COPY_NAME: Final = re.compile(
    r"nextron-(?P<env>[a-z][a-z0-9]{0,15})-(?P<stamp>\d{8}T\d{6}\.\d{3}Z)\.db(?:\.enc)?",
    re.IGNORECASE,
)
_STAMP_FORMAT: Final = "%Y%m%dT%H%M%S.%fZ"  # nextron.domain.backup's copy-name stamp


class RecoveryMode(StrEnum):
    """Whether a restore's recovery mode is on, ended, or replaced by a later restore."""

    ON = "on"
    OFF = "off"
    SUPERSEDED = "superseded"


class RecoveryStep(StrEnum):
    """What a restored database must redo before sending, in the order the steps run."""

    REAPPLY_ERASURE_LEDGER = "reapply_erasure_ledger"
    MARK_OMS_UNKNOWN = "mark_oms_unknown"
    REINGEST_MAILBOXES = "reingest_mailboxes"
    REPOLL_UNSUBSCRIBES = "repoll_unsubscribes"

    @property
    def label(self) -> str:
        return _STEP_LABELS[self]


_STEP_LABELS: Final = MappingProxyType(
    {
        RecoveryStep.REAPPLY_ERASURE_LEDGER: "Reapply the erasure ledger",
        RecoveryStep.MARK_OMS_UNKNOWN: "Mark unfinished sends unknown",
        RecoveryStep.REINGEST_MAILBOXES: "Re-ingest the mailboxes",
        RecoveryStep.REPOLL_UNSUBSCRIBES: "Re-poll the unsubscribe Worker",
    }
)


class StepStatus(StrEnum):
    """Where one recovery step is."""

    PENDING = "pending"
    DONE = "done"
    NOT_APPLICABLE = "not_applicable"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS: Final = MappingProxyType(
    {
        StepStatus.PENDING: "pending",
        StepStatus.DONE: "done",
        StepStatus.NOT_APPLICABLE: "not applicable",
    }
)
SETTLED: Final = frozenset({StepStatus.DONE, StepStatus.NOT_APPLICABLE})


def settled(statuses: Iterable[StepStatus | str]) -> bool:
    """Whether every status is ``done`` or ``not_applicable``."""
    return all(StepStatus(status) in SETTLED for status in statuses)


def _utc(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    """What a recovery step's handler gets; see the module docstring."""

    recovery_id: str
    backup_at: datetime
    restored_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_id, str):
            raise TypeError("recovery_id must be a str")
        if not self.recovery_id:
            raise ValueError("recovery_id must not be empty")
        object.__setattr__(self, "backup_at", _utc("backup_at", self.backup_at))
        object.__setattr__(self, "restored_at", _utc("restored_at", self.restored_at))


@dataclass(frozen=True, slots=True)
class RecoveryHandler:
    """One owner's handler of one recovery step; see the module docstring."""

    step: RecoveryStep
    owner: str
    run: Callable[[RecoveryContext], bool]
    startup: bool

    def __post_init__(self) -> None:
        if not isinstance(self.step, RecoveryStep):
            raise TypeError("step must be a RecoveryStep")
        if not isinstance(self.owner, str):
            raise TypeError("owner must be a str")
        if not self.owner.strip():
            raise ValueError("owner must not be blank")
        if not callable(self.run):
            raise TypeError("run must be callable")
        if not isinstance(self.startup, bool):
            raise TypeError("startup must be a bool")


@dataclass(frozen=True, slots=True)
class RecoveryPass:
    """One pass over the recovery steps: the context and the registered steps still pending,
    in step order."""

    context: RecoveryContext
    pending: tuple[RecoveryStep, ...]


class RecoverySteps:
    """The recovery step registry; see the module docstring."""

    def __init__(self) -> None:
        self._handlers: dict[RecoveryStep, RecoveryHandler] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register_step(self, handler: RecoveryHandler) -> None:
        """Add ``handler``; raises ``RuntimeError`` once frozen, ``TypeError`` for anything but
        a ``RecoveryHandler`` and ``ValueError`` for a step already registered."""
        if self._frozen:
            raise RuntimeError("the recovery step registry is frozen")
        if not isinstance(handler, RecoveryHandler):
            raise TypeError("register a RecoveryHandler")
        if handler.step in self._handlers:
            raise ValueError(f"recovery step {handler.step} is already registered")
        self._handlers[handler.step] = handler

    def freeze(self) -> None:
        """Refuse any further registration."""
        self._frozen = True

    def get(self, step: RecoveryStep) -> RecoveryHandler | None:
        """The handler of ``step``, or ``None`` when no owner registered one."""
        return self._handlers.get(RecoveryStep(step))

    def registered(self) -> frozenset[RecoveryStep]:
        """Every step that has a handler."""
        return frozenset(self._handlers)


# The restore's file names -----------------------------------------------------------------------


def copy_format(name: object) -> Literal["plain", "encrypted"] | None:
    """``"encrypted"`` for a name ending ``.db.enc``, ``"plain"`` for one ending ``.db``, else
    ``None``; casefolded."""
    if not isinstance(name, str):
        return None
    folded = name.casefold()
    if folded.endswith(_ENCRYPTED_SUFFIX):
        return "encrypted"
    if folded.endswith(_PLAIN_SUFFIX):
        return "plain"
    return None


def _copy_match(name: object) -> re.Match[str] | None:
    if not isinstance(name, str):
        return None
    return _COPY_NAME.fullmatch(name)


def copy_env(name: object) -> str | None:
    """The environment of a backup copy name (``nextron-<env>-<stamp>.db`` or ``.db.enc``,
    any case), casefolded; ``None`` for any other name."""
    match = _copy_match(name)
    return None if match is None else match["env"].casefold()


def copy_stamp(name: object) -> datetime | None:
    """The UTC stamp of a backup copy name, or ``None`` for any other name or a stamp that
    isn't a real time."""
    match = _copy_match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match["stamp"].upper(), _STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:  # the shape of a stamp, but no real time (month 13, say)
        return None


def backup_time(
    name: object, newest_event: datetime | None, restored_at: datetime
) -> datetime:
    """When the restored copy was taken, at the latest: the earliest of the name's stamp, the
    copy's newest event and ``restored_at``, skipping a missing one."""
    candidates = [_utc("restored_at", restored_at)]
    if newest_event is not None:
        candidates.append(_utc("newest_event", newest_event))
    stamp = copy_stamp(name)
    if stamp is not None:
        candidates.append(stamp)
    return min(candidates)
