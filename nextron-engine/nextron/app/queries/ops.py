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
  (``redact``-class in ``pii_registry``).

It reads through the current unit of work, read-only ones included.
"""

from typing import Final

from nextron.db.repos.ops import backups as backups_repo
from nextron.db.repos.ops import heartbeat as heartbeat_repo
from nextron.db.repos.ops.backups import BackupRow
from nextron.db.repos.ops.heartbeat import HeartbeatRow
from nextron.domain.backup import BACKUP_FAILED, BackupKind, BackupStatus, backup_failed_text
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
    """One ``backup_failed`` issue per failed backup newer than the newest verified one, oldest
    first."""
    verified = backups_repo.newest(status=str(BackupStatus.VERIFIED))
    since = None if verified is None else verified.started_at
    return tuple(
        HealthIssue(
            BACKUP_FAILED,
            row.id,
            backup_failed_text(row.kind, row.id),
            detail=ERROR_LEAD + redact(row.error or ""),
        )
        for row in backups_repo.failed_after(since)
    )
