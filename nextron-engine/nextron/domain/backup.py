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

**Off-PC copies (Story 1.17; AD-9, AD-17, AD-20, OP-6, NFR-17).** After each verified daily or
manual backup, ``ops`` sends an encrypted copy (and the erasure ledger, when there is one) to the
``backup_offpc_dir`` folder; a ``pre_migrate`` copy stays local.

- ``ERASURE_LEDGER_FILE`` (``erasure-ledger.jsonl``) is the erasure ledger in the backup dir
  (Story 4.x writes it). ``OFFPC_SUFFIX`` is ``.enc``. ``OFFPC_ROTATION_DAYS`` (35, equal to
  ``MAX_ROTATION_DAYS``) is how long off-PC copies are kept: the disaster copy follows AD-17's
  window, not the local ``backup_rotation_days``.
- ``OffpcStatus`` is ``copied``, ``failed`` or ``not_configured``; ``.label`` is "copied off
  the PC", "off-PC copy failed" or "off-PC not configured". ``LedgerStatus`` is ``copied`` or
  ``absent`` (there was no ledger to copy).
- ``offpc_names(env, at)`` is ``(db_name, ledger_name)``: ``copy_name(env, at) + ".enc"`` and
  ``nextron-<env>-<stamp>.ledger.jsonl.enc``. ``offpc_stamp(env, name)`` is the UTC stamp of
  either shape, with or without a trailing ``.part`` (a copy cut off while it was written), or
  ``None`` for any other name (another environment's, or a file that isn't a backup).
- ``offpc_rotation(env, names, now, days)`` is the names to delete: those with a stamp older
  than ``now - days``, sorted. It never selects the newest complete ``.db.enc`` copy, nor the
  ledger with the same stamp, however old they are.
- ``offpc_problem(folder, data_dir, backup_dir)`` is why ``folder`` can't hold off-PC copies:
  ``folder_not_set`` (blank), ``folder_in_data_dir`` (the data dir or inside it) or
  ``folder_in_backup_dir`` (the backup dir or inside it), else ``None``. It compares
  casefolded ``PureWindowsPath`` parts and touches no file.
- ``same_volume(folder, data_dir)`` is whether both are on the same drive (a drive letter or a
  ``\\\\server\\share``), compared casefolded, with no I/O: a folder on a volume mounted under
  ``C:\\`` isn't detected.
- ``is_offpc_folder(text)`` is the ``backup_offpc_dir`` setting's rule: blank, or an absolute
  Windows path, which is a drive path (``E:\\Backups``) or a UNC share (``\\\\nas\\backups``),
  with no ``..`` part and not a ``\\\\?\\`` or ``\\\\.\\`` device path. No name after the drive
  (the UNC server and share included) holds any of ``<>"|?*`` or a ``:`` (an NTFS stream),
  is a reserved device name (``CON``, ``PRN``, ``AUX``, ``NUL``, ``COM1``-``COM9``,
  ``LPT1``-``LPT9``, any case, with or without an extension), or ends in ``.`` or a space
  (Windows drops those, so ``<data dir>.`` would be the data dir itself).
- ``offpc_failed_text(kind, backup_id)`` is exactly ``"Off-PC backup copy failed: <kind>
  <ULID>"``, the ``backup_failed`` alert of a failed off-PC copy.
- ``OffpcCopyMismatch`` (code ``offpc_copy_mismatch``): the plaintext SHA-256 computed while
  the copy was encrypted differs from the verified backup's.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import PurePath, PureWindowsPath
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

ERASURE_LEDGER_FILE: Final = "erasure-ledger.jsonl"  # in the backup dir; Story 4.x writes it
OFFPC_SUFFIX: Final = ".enc"
OFFPC_ROTATION_DAYS: Final = MAX_ROTATION_DAYS  # the off-PC copy follows AD-17's window
FOLDER_NOT_SET: Final = "folder_not_set"
FOLDER_IN_DATA_DIR: Final = "folder_in_data_dir"
FOLDER_IN_BACKUP_DIR: Final = "folder_in_backup_dir"

ENV_PATTERN: Final = re.compile(r"[a-z][a-z0-9]{0,15}")  # matched with fullmatch
_STAMP: Final = r"\d{8}T\d{6}\.\d{3}Z"
_STAMP_FORMAT: Final = "%Y%m%dT%H%M%S.%fZ"
_DB_SHAPE: Final = ".db"
_LEDGER_SHAPE: Final = ".ledger.jsonl"
_PART_SUFFIX: Final = ".part"
_DRIVE_LETTER: Final = re.compile(r"[A-Za-z]:")  # matched with fullmatch
_UNC_SHARE: Final = re.compile(r"\\\\([^\\]+)\\([^\\]+)")  # matched with fullmatch
_DEVICE_SERVERS: Final = frozenset({"?", "."})  # \\?\ and \\.\ are device paths
_REFUSED_NAME_CHARACTERS: Final = frozenset('<>"|?*:')  # ':' after the drive is an NTFS stream
_RESERVED_NAMES: Final = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)


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


class OffpcStatus(StrEnum):
    """What happened to a verified backup's off-PC copy, written once; ``label`` is how the
    health panel names it."""

    COPIED = "copied"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"

    @property
    def label(self) -> str:
        return _OFFPC_LABELS[self]


_OFFPC_LABELS: Final = MappingProxyType(
    {
        OffpcStatus.COPIED: "copied off the PC",
        OffpcStatus.FAILED: "off-PC copy failed",
        OffpcStatus.NOT_CONFIGURED: "off-PC not configured",
    }
)


class LedgerStatus(StrEnum):
    """Whether the erasure ledger went off the PC with a copied backup."""

    COPIED = "copied"
    ABSENT = "absent"


class BackupNotVerified(DomainError):
    """The copy failed its verification; the message is the reason."""

    code = "backup_not_verified"


class OffpcCopyMismatch(DomainError):
    """The off-PC copy's plaintext SHA-256 differs from the verified backup's."""

    code = "offpc_copy_mismatch"


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


def _stamp_text(at: datetime) -> str:
    moment = _check_aware("at", at).astimezone(UTC)
    return f"{moment:%Y%m%dT%H%M%S}.{moment.microsecond // 1000:03d}Z"


def copy_name(env: str, at: datetime) -> str:
    """``nextron-<env>-<YYYYMMDDTHHMMSS.mmm>Z.db`` for the UTC time of ``at``."""
    _check_env(env)
    return f"nextron-{env}-{_stamp_text(at)}{_DB_SHAPE}"


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


# Off-PC copies ----------------------------------------------------------------------------------


def offpc_names(env: str, at: datetime) -> tuple[str, str]:
    """The off-PC names of the backup that started at ``at``: the encrypted copy
    (``nextron-<env>-<stamp>.db.enc``) and the erasure ledger
    (``nextron-<env>-<stamp>.ledger.jsonl.enc``)."""
    _check_env(env)
    stem = f"nextron-{env}-{_stamp_text(at)}"
    return f"{stem}{_DB_SHAPE}{OFFPC_SUFFIX}", f"{stem}{_LEDGER_SHAPE}{OFFPC_SUFFIX}"


def _offpc_match(env: str, name: object) -> re.Match[str] | None:
    if not (is_env_name(env) and isinstance(name, str)):
        return None
    shapes = f"(?P<shape>{re.escape(_DB_SHAPE)}|{re.escape(_LEDGER_SHAPE)})"
    return re.fullmatch(
        rf"nextron-{re.escape(env)}-(?P<stamp>{_STAMP}){shapes}{re.escape(OFFPC_SUFFIX)}"
        rf"(?P<part>{re.escape(_PART_SUFFIX)})?",
        name,
    )


def offpc_stamp(env: str, name: str) -> datetime | None:
    """The UTC stamp of an off-PC copy or ledger name of ``env``'s (a trailing ``.part``
    allowed), or ``None`` for any other name."""
    match = _offpc_match(env, name)
    if match is None:
        return None
    try:
        return datetime.strptime(match["stamp"], _STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:  # the shape of a stamp, but no real time (month 13, say)
        return None


def offpc_rotation(env: str, names: Iterable[str], now: datetime, days: int) -> tuple[str, ...]:
    """The off-PC names to delete, sorted: ``env``'s copies, ledgers and stale ``.part`` files
    whose stamp is older than ``now - days``, never the newest complete copy nor its ledger."""
    _check_aware("now", now)
    if isinstance(days, bool) or not isinstance(days, int):
        raise TypeError("days must be an int")
    stamped: list[tuple[str, datetime, re.Match[str]]] = []
    for name in names:
        match = _offpc_match(env, name)
        stamp = offpc_stamp(env, name)
        if match is not None and stamp is not None:
            stamped.append((name, stamp, match))
    complete = [
        (stamp, name)
        for name, stamp, match in stamped
        if match["shape"] == _DB_SHAPE and match["part"] is None
    ]
    kept: set[str] = set()
    if complete:
        newest_stamp, newest_name = max(complete)
        kept = {newest_name, offpc_names(env, newest_stamp)[1]}
    cutoff = now - timedelta(days=days)
    return tuple(
        sorted(name for name, stamp, _ in stamped if stamp < cutoff and name not in kept)
    )


def _parts(path: str | PurePath) -> list[str]:
    return [part.casefold().rstrip("\\/") for part in PureWindowsPath(str(path)).parts]


def _is_within(path: str | PurePath, folder: str | PurePath) -> bool:
    """Whether ``path`` is ``folder`` or inside it, comparing casefolded path parts."""
    inner, outer = _parts(path), _parts(folder)
    return len(outer) > 0 and inner[: len(outer)] == outer


def offpc_problem(
    folder: str, data_dir: str | PurePath, backup_dir: str | PurePath
) -> str | None:
    """Why ``folder`` can't hold the off-PC copies (``folder_not_set``, ``folder_in_data_dir``
    or ``folder_in_backup_dir``), or ``None`` when it can. No I/O."""
    if not isinstance(folder, str) or not folder.strip():
        return FOLDER_NOT_SET
    if _is_within(folder, data_dir):
        return FOLDER_IN_DATA_DIR
    if _is_within(folder, backup_dir):
        return FOLDER_IN_BACKUP_DIR
    return None


def drive_of(path: str | PurePath) -> str:
    """The drive of ``path``: ``C:`` or ``\\\\server\\share`` (empty for none), as written."""
    return PureWindowsPath(str(path)).drive


def same_volume(folder: str | PurePath, data_dir: str | PurePath) -> bool:
    """Whether ``folder`` and ``data_dir`` are on the same drive (a drive letter or a
    ``\\\\server\\share``), casefolded. No I/O."""
    left = drive_of(folder).casefold()
    right = drive_of(data_dir).casefold()
    return bool(left) and left == right


def is_offpc_folder(text: object) -> bool:
    """Blank, or an absolute Windows folder path: a drive path or a UNC share, with no ``..``
    part, and not a ``\\\\?\\`` or ``\\\\.\\`` device path."""
    if not isinstance(text, str):
        return False
    if not text.strip():
        return True
    path = PureWindowsPath(text)
    if path.root != "\\" or ".." in path.parts:
        return False
    names = list(path.parts[1:])
    if not _DRIVE_LETTER.fullmatch(path.drive):
        share = _UNC_SHARE.fullmatch(path.drive)
        if share is None or share[1] in _DEVICE_SERVERS:
            return False
        names += [share[1], share[2]]
    return all(_is_plain_name(name) for name in names)


def _is_plain_name(name: str) -> bool:
    """Whether Windows reads the path component ``name`` as written: no refused character, not a
    reserved device name, and no trailing dot or space (Windows drops them)."""
    if any(char in _REFUSED_NAME_CHARACTERS for char in name):
        return False
    if name.endswith((".", " ")):
        return False
    return name.split(".", 1)[0].rstrip(" ").casefold() not in _RESERVED_NAMES


# Text -------------------------------------------------------------------------------------------


def backup_failed_text(kind: str, backup_id: str) -> str:
    """The ops alert text of a failed backup: its kind and its ULID, nothing else."""
    if not is_ulid(backup_id):
        raise ValueError("backup_id must be a ULID")
    return f"Backup failed: {BackupKind(kind).value} {backup_id}"


def offpc_failed_text(kind: str, backup_id: str) -> str:
    """The ops alert text of a failed off-PC copy: its kind and its ULID, nothing else."""
    if not is_ulid(backup_id):
        raise ValueError("backup_id must be a ULID")
    return f"Off-PC backup copy failed: {BackupKind(kind).value} {backup_id}"
