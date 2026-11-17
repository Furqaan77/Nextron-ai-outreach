"""Backups of the database (AD-2, AD-17, AD-20, FR-58, OP-5, OP-6, NFR-16): the names, kinds,
statuses, copy names, the daily schedule and the ops alert text. Standard library only; a
function that needs the time takes ``now``.

**Names.** ``BACKUP_DB_JOB`` (``backup_db``) is the job type that takes a daily or manual
backup, and the AD-10 trigger of the same name. ``BACKUP_FAILED`` (``backup_failed``) is both
the ops alert kind of a failed backup and its health-issue kind. ``KIND_FIELD`` (``kind``) is
the ``backup_db`` payload field naming the kind (``daily`` when it is absent: a trigger's tick
payload is ``{"v": 1}``). ``LOCAL_DIR_NAME`` (``local``) is the folder under the backup dir
that holds the local copies. ``COUNTED_TABLES`` are the tables a verified copy counts rows of:
the Suppression List and the Provenance Records (``None`` while a table doesn't exist yet).

**Kinds and statuses.** ``BackupKind`` is ``daily`` (the calendar trigger), ``manual`` (the
health panel's "Back up now") or ``pre_migrate`` (``serve``, before an upgrade); ``.label`` is
"daily", "manual" or "before a migration". ``JOB_KINDS`` are the kinds the ``backup_db`` job
takes. ``BackupStatus`` is ``verified`` or ``failed``: a backup's row is written once, after
the copy, with no state machine and no ``events`` row (backups aren't one of AD-4's machines).

**Copy names.** ``copy_name(env, at)`` is ``nextron-<env>-<YYYYMMDDTHHMMSS.mmm>Z.db`` from the
UTC time of ``at`` (aware; ``ValueError`` for a naive one), with milliseconds so a
``pre_migrate`` copy and the catch-up daily copy never share a name; Windows forbids ``:`` in a
file name. ``env`` fullmatches ``ENV_PATTERN``. ``is_copy_name(env, name)`` is whether ``name``
fullmatches that shape for ``env``: rotation deletes only such files.

**The schedule.** ``DEFAULT_BACKUP_TIME`` is 10:00. ``latest_slot(now, at, timezone)`` is the
latest UTC instant at or before ``now`` whose local time in ``timezone`` (an IANA name) is
``at``: today's ``at`` in that zone, or yesterday's while today's is still to come.
``missed_daily(newest_daily_started_at, now, at, timezone)`` is whether the newest daily backup
started before that slot: the engine was off (shut down, rebooted) at the daily time, so it
makes the backup up once at its next start. ``None`` (no daily backup yet) is never missed, so a
new install waits for its first daily time. ``BackupSchedule(at, timezone, missed)`` is what
``ops`` reads for the trigger.

**Rotation.** Local copies older than the ``backup_rotation_days`` setting are deleted after
each successful backup, keeping the newest verified one. ``MIN_ROTATION_DAYS`` (7) keeps a
week of history; ``MAX_ROTATION_DAYS`` (35) is the most, because AD-17 says erased data ages
out of backups within the 35-day rotation. ``DEFAULT_ROTATION_DAYS`` is 35.

**Errors and text.** ``BackupNotVerified`` (code ``backup_not_verified``) is a copy that failed
its verification; its message is the reason. ``backup_failed_text(kind, backup_id)`` is exactly
``"Backup failed: <kind> <ULID>"``: ops alert text carries IDs and codes only.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from nextron.domain.errors import DomainError
from nextron.domain.ids import is_ulid

BACKUP_DB_JOB: Final = "backup_db"
BACKUP_FAILED: Final = "backup_failed"  # the alert kind and the health-issue kind
KIND_FIELD: Final = "kind"
LOCAL_DIR_NAME: Final = "local"
COUNTED_TABLES: Final = ("suppression_entries", "provenance_records")
DEFAULT_BACKUP_TIME: Final = time(10, 0)
MIN_ROTATION_DAYS: Final = 7
MAX_ROTATION_DAYS: Final = 35  # AD-17: erased data ages out of backups within 35 days
DEFAULT_ROTATION_DAYS: Final = 35

ENV_PATTERN: Final = re.compile(r"[a-z][a-z0-9]{0,15}")  # matched with fullmatch
_STAMP: Final = r"\d{8}T\d{6}\.\d{3}Z"


class BackupKind(StrEnum):
    """What started a backup; ``label`` is how the health panel names it."""

    DAILY = "daily"
    MANUAL = "manual"
    PRE_MIGRATE = "pre_migrate"

    @property
    def label(self) -> str:
        return _KIND_LABELS[self]


_KIND_LABELS: Final = MappingProxyType(
    {
        BackupKind.DAILY: "daily",
        BackupKind.MANUAL: "manual",
        BackupKind.PRE_MIGRATE: "before a migration",
    }
)

JOB_KINDS: Final = frozenset({BackupKind.DAILY, BackupKind.MANUAL})


class BackupStatus(StrEnum):
    """A backup's outcome, written once."""

    VERIFIED = "verified"
    FAILED = "failed"


class BackupNotVerified(DomainError):
    """The copy failed its verification; the message is the reason."""

    code = "backup_not_verified"


# Copy names -------------------------------------------------------------------------------------


def _check_env(env: object) -> str:
    if not isinstance(env, str):
        raise TypeError("env must be a str")
    if ENV_PATTERN.fullmatch(env) is None:
        raise ValueError(f"env must match {ENV_PATTERN.pattern}")
    return env


def is_env_name(env: object) -> bool:
    """Whether ``env`` can name a copy (``ENV_PATTERN``)."""
    return isinstance(env, str) and ENV_PATTERN.fullmatch(env) is not None


def _check_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value


def copy_name(env: str, at: datetime) -> str:
    """``nextron-<env>-<YYYYMMDDTHHMMSS.mmm>Z.db`` for the UTC time of ``at``."""
    _check_env(env)
    moment = _check_aware("at", at).astimezone(UTC)
    return f"nextron-{env}-{moment:%Y%m%dT%H%M%S}.{moment.microsecond // 1000:03d}Z.db"


def is_copy_name(env: str, name: str) -> bool:
    """Whether ``name`` is a copy name of ``env``'s (``copy_name``'s shape)."""
    if not (is_env_name(env) and isinstance(name, str)):
        return False
    return re.fullmatch(rf"nextron-{re.escape(env)}-{_STAMP}\.db", name) is not None


# The schedule -----------------------------------------------------------------------------------


def latest_slot(now: datetime, at: time, timezone: str) -> datetime:
    """The latest UTC instant at or before ``now`` whose local time in ``timezone`` is ``at``."""
    _check_aware("now", now)
    if not isinstance(at, time):
        raise TypeError("at must be a datetime.time")
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    slot = datetime.combine(today, at, tzinfo=zone).astimezone(UTC)
    if slot > now:
        slot = datetime.combine(today - timedelta(days=1), at, tzinfo=zone).astimezone(UTC)
    return slot


def missed_daily(
    newest_daily_started_at: datetime | None, now: datetime, at: time, timezone: str
) -> bool:
    """Whether the newest daily backup started before the latest daily slot; ``None`` (no
    daily backup yet) is never missed."""
    if newest_daily_started_at is None:
        return False
    return newest_daily_started_at < latest_slot(now, at, timezone)


@dataclass(frozen=True, slots=True)
class BackupSchedule:
    """When the daily backup runs (``at`` in ``timezone``), and whether the last one was
    missed, so the engine makes it up once now."""

    at: time
    timezone: str
    missed: bool


# Text -------------------------------------------------------------------------------------------


def backup_failed_text(kind: str, backup_id: str) -> str:
    """The ops alert text of a failed backup: its kind and its ULID, nothing else."""
    if not is_ulid(backup_id):
        raise ValueError("backup_id must be a ULID")
    return f"Backup failed: {BackupKind(kind).value} {backup_id}"
