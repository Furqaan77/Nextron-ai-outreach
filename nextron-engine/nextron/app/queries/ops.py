"""Read API of the ``ops`` service (AD-3, AD-20).

Import rule: ``nextron.domain`` and ``nextron.db.repos.ops`` only.

- ``heartbeat()``: the ``HeartbeatRow`` of the engine's heartbeat (its ``runner_heartbeat``
  row: ``pid``, ``started_at`` and ``beat_at``), or ``None`` before the first beat.
- ``last_backup()``: the newest ``BackupRow`` of any kind and status (by ``started_at``, then
  id), or ``None`` before the first backup. The health panel shows it.
- ``newest_backup(kind)``: the newest ``BackupRow`` of ``kind``, of either status, or ``None``.
  The daily schedule reads the newest ``daily`` one to find a missed daily time.
- ``health_issues()``: the backups' health issues (``nextron.main.build_health_issues``
  registers it as the ``backups`` source): each ``failed`` backup newer than the newest
  ``verified`` one (every failed one when none is verified), oldest first, as
  ``HealthIssue("backup_failed", <backup id>, backup_failed_text(kind, id), detail="Error: " +
  redact(error))``. The list clears when a later backup verifies; there is no Dismiss. The
  error was redacted when it was stored; it is redacted again here, because it is free text
  (``redact``-class in ``pii_registry``). After those come the failed off-PC copies (Story
  1.17): each row whose ``offpc_status`` is ``failed`` and that is newer than the newest
  ``copied`` or ``not_configured`` one (every such row when there is none), oldest first, as
  ``HealthIssue("backup_failed", <backup id>, offpc_failed_text(kind, id), detail="Error: " +
  redact(offpc_error))``. That list clears when a later backup is copied off the PC or the
  off-PC copy is no longer configured. Last come the failed restore tests (Story 1.18): each
  ``fail`` test newer than the newest ``pass`` one (every failed one when none passed), oldest
  first, as ``HealthIssue("restore_test_failed", <test id>, restore_test_failed_text(kind, id,
  reason), detail="Error: " + redact(detail or reason))``. That list clears when a later test
  passes; there is no Dismiss.

The restore test (Story 1.18):

- ``last_restore_test()``: the newest ``RestoreTestRow`` of either kind and result (by
  ``started_at``, then id), or ``None`` before the first test. The health panel shows it.
- ``newest_restore_test(kind)``: the newest ``RestoreTestRow`` of ``kind``, of either result, or
  ``None``. The monthly schedule reads the newest ``monthly`` one.
- ``newest_offpc_copy()``: the ``BackupRow`` whose off-PC copy a restore test fetches: the newest
  one ``copied`` off the PC and not yet rotated out, or ``None``.

The copies an erasure leaves (Story 3.14):

- ``copies_holding(since, until)``: the ``verified`` ``BackupRow`` rows started from ``since`` to
  ``until`` whose local copy is still there or whose off-PC copy is ``copied`` and not yet
  rotated out, oldest first: the copies that may hold a Contact erased at ``until``.

Restore and recovery mode (Story 1.19):

- ``recovery_mode() -> bool``: whether recovery mode is on (a ``recovery_state`` row is
  ``on``). This is the gate: every send path calls it in its own transaction and holds its
  sends while it is true, because a restored database has forgotten every opt-out, erasure and
  send made after its copy was taken. The notification area's banner reads it too.
- ``recovery_state()``: the ``on`` ``RecoveryRow`` when recovery mode is on, else the newest
  row of any mode (by ``restored_at``, then id), or ``None`` before the first restore. The
  health panel's Recovery section shows it.

It reads through the current unit of work, read-only ones included.
"""

from datetime import datetime
from typing import Final

from nextron.db.repos.ops import backups as backups_repo
from nextron.db.repos.ops import heartbeat as heartbeat_repo
from nextron.db.repos.ops import recovery as recovery_repo
from nextron.db.repos.ops import restore_tests as restore_tests_repo
from nextron.db.repos.ops.backups import BackupRow
from nextron.db.repos.ops.heartbeat import HeartbeatRow
from nextron.db.repos.ops.recovery import RecoveryRow
from nextron.db.repos.ops.restore_tests import RestoreTestRow
from nextron.domain.backup import (
    BACKUP_FAILED,
    BackupKind,
    BackupStatus,
    OffpcStatus,
    backup_failed_text,
    offpc_failed_text,
)
from nextron.domain.health import HealthIssue
from nextron.domain.redaction import redact
from nextron.domain.restore_test import (
    RESTORE_TEST_FAILED,
    RestoreResult,
    RestoreTestKind,
    restore_test_failed_text,
)

ERROR_LEAD: Final = "Error: "


def heartbeat() -> HeartbeatRow | None:
    """The engine's heartbeat row, or ``None``."""
    return heartbeat_repo.get()


def last_backup() -> BackupRow | None:
    """The newest backup of any kind and status, or ``None``."""
    return backups_repo.newest()


def newest_backup(kind: BackupKind) -> BackupRow | None:
    """The newest backup of ``kind``, of either status, or ``None``."""
    return backups_repo.newest(kind=str(BackupKind(kind)))


def last_restore_test() -> RestoreTestRow | None:
    """The newest restore test of either kind and result, or ``None``."""
    return restore_tests_repo.newest()


def newest_restore_test(kind: RestoreTestKind) -> RestoreTestRow | None:
    """The newest restore test of ``kind``, of either result, or ``None``."""
    return restore_tests_repo.newest(kind=str(RestoreTestKind(kind)))


def newest_offpc_copy() -> BackupRow | None:
    """The newest backup copied off the PC whose copy is not rotated out, or ``None``."""
    return backups_repo.newest_offpc_copy()


def copies_holding(since: datetime, until: datetime) -> tuple[BackupRow, ...]:
    """The verified backups started from ``since`` to ``until`` that still have a copy."""
    return backups_repo.copies_holding(since, until)


def recovery_mode() -> bool:
    """Whether recovery mode is on: the gate every send path checks."""
    return recovery_repo.active() is not None


def recovery_state() -> RecoveryRow | None:
    """The ``on`` recovery row, else the newest one of any mode, or ``None``."""
    return recovery_repo.newest()


def _restore_issues() -> tuple[HealthIssue, ...]:
    passed = restore_tests_repo.newest(result=str(RestoreResult.PASS))
    since = None if passed is None else passed.started_at
    return tuple(
        HealthIssue(
            RESTORE_TEST_FAILED,
            row.id,
            restore_test_failed_text(row.kind, row.id, row.reason or ""),
            detail=ERROR_LEAD + redact(row.detail or row.reason or ""),
        )
        for row in restore_tests_repo.failed_after(since)
    )


def health_issues() -> tuple[HealthIssue, ...]:
    """One ``backup_failed`` issue per failed backup newer than the newest verified one, then
    one per failed off-PC copy newer than the newest copied or not configured one, then one
    ``restore_test_failed`` issue per failed restore test newer than the newest passed one,
    each oldest first."""
    verified = backups_repo.newest(status=str(BackupStatus.VERIFIED))
    since = None if verified is None else verified.started_at
    local = tuple(
        HealthIssue(
            BACKUP_FAILED,
            row.id,
            backup_failed_text(row.kind, row.id),
            detail=ERROR_LEAD + redact(row.error or ""),
        )
        for row in backups_repo.failed_after(since)
    )
    settled = backups_repo.newest_offpc(
        (str(OffpcStatus.COPIED), str(OffpcStatus.NOT_CONFIGURED))
    )
    offpc_since = None if settled is None else settled.started_at
    offpc = tuple(
        HealthIssue(
            BACKUP_FAILED,
            row.id,
            offpc_failed_text(row.kind, row.id),
            detail=ERROR_LEAD + redact(row.offpc_error or ""),
        )
        for row in backups_repo.offpc_failed_after(offpc_since)
    )
    return local + offpc + _restore_issues()
