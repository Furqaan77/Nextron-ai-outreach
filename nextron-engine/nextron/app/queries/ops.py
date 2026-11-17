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
  off-PC copy is no longer configured.

It reads through the current unit of work, read-only ones included.
"""

from typing import Final

from nextron.db.repos.ops import backups as backups_repo
from nextron.db.repos.ops import heartbeat as heartbeat_repo
from nextron.db.repos.ops.backups import BackupRow
from nextron.db.repos.ops.heartbeat import HeartbeatRow
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


def health_issues() -> tuple[HealthIssue, ...]:
    """One ``backup_failed`` issue per failed backup newer than the newest verified one, then
    one per failed off-PC copy newer than the newest copied or not configured one, each
    oldest first."""
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
    return local + offpc
