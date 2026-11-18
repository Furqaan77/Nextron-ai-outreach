"""The ``backups`` table's repository (AD-3, AD-20, Story 1.16): the only SQL on it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores what the ``ops`` service gives it and checks nothing (the table's checks
do). Rows come back as ``BackupRow``, a frozen copy of every column.

- ``insert(...)``: a new row, written once; returns its new ULID;
- ``newest(*, kind=None, status=None)``: the newest row (``started_at``, then id, descending),
  of ``kind`` and ``status`` when given, or ``None``;
- ``failed_after(since)``: every ``failed`` row that started after ``since`` (every one when
  ``since`` is ``None``), oldest first;
- ``rotation_candidates(before, keep_id)``: every ``verified`` row whose local copy is still
  there (``local_deleted_at IS NULL``), that started before ``before`` and isn't ``keep_id``,
  oldest first;
- ``mark_deleted(ids, at)``: sets ``local_deleted_at`` on those rows while it is ``NULL``;
  returns the row count.

The off-PC outcome (Story 1.17):

- ``set_offpc(backup_id, *, status, name, ledger, at, error)``: records it on the row, only
  while ``offpc_status IS NULL`` (it is written once); returns the row count;
- ``newest_offpc(statuses)``: the newest row (``started_at``, then id, descending) whose
  ``offpc_status`` is one of ``statuses``, or ``None``;
- ``offpc_failed_after(since)``: every row whose ``offpc_status`` is ``failed`` that started
  after ``since`` (every one when ``since`` is ``None``), oldest first;
- ``mark_offpc_deleted(names, at)``: sets ``offpc_deleted_at`` on the rows whose
  ``offpc_name`` is one of ``names``, while it is ``NULL``; returns the row count.

The copy under test (Story 1.18):

- ``newest_offpc_copy()``: the newest row (``started_at``, then id, descending) whose off-PC
  copy is ``copied`` and not yet rotated out (``offpc_deleted_at IS NULL``), or ``None``.

The copies an erasure leaves (Story 3.14):

- ``copies_holding(since, until)``: every ``verified`` row that started from ``since`` to
  ``until`` (both included) whose local copy is still there (``local_deleted_at IS NULL``) or
  whose off-PC copy is ``copied`` and not yet rotated out, oldest first.
"""

from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import Executable, and_, or_, select, update
from sqlalchemy import insert as sql_insert

from nextron.db.models.base import new_id
from nextron.db.models.ops import BackupRecord
from nextron.db.uow import current

FAILED = "failed"  # nextron.domain.backup.BackupStatus.FAILED
VERIFIED = "verified"  # nextron.domain.backup.BackupStatus.VERIFIED
OFFPC_FAILED = "failed"  # nextron.domain.backup.OffpcStatus.FAILED
OFFPC_COPIED = "copied"  # nextron.domain.backup.OffpcStatus.COPIED
_NO_SYNC = {"synchronize_session": False}


@dataclass(frozen=True, slots=True)
class BackupRow:
    """Every column of one ``backups`` row."""

    id: str
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime
    path: str
    size_bytes: int | None
    sha256: str | None
    db_revision: str | None
    suppression_count: int | None
    provenance_count: int | None
    error: str | None
    local_deleted_at: datetime | None
    offpc_status: str | None
    offpc_name: str | None
    offpc_ledger: str | None
    offpc_at: datetime | None
    offpc_deleted_at: datetime | None
    offpc_error: str | None


_COLUMNS = tuple(getattr(BackupRecord, field.name) for field in fields(BackupRow))


def _rows(statement: Executable) -> list[BackupRow]:
    return [BackupRow(*row) for row in current().session.execute(statement)]


def insert(
    *,
    kind: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    path: str,
    size_bytes: int | None,
    sha256: str | None,
    db_revision: str | None,
    suppression_count: int | None,
    provenance_count: int | None,
    error: str | None,
) -> str:
    """Insert one backup row; return its new id."""
    backup_id = new_id()
    current().session.execute(
        sql_insert(BackupRecord).values(
            id=backup_id,
            kind=kind,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            path=path,
            size_bytes=size_bytes,
            sha256=sha256,
            db_revision=db_revision,
            suppression_count=suppression_count,
            provenance_count=provenance_count,
            error=error,
            local_deleted_at=None,
        )
    )
    return backup_id


def newest(*, kind: str | None = None, status: str | None = None) -> BackupRow | None:
    """The newest row, of ``kind`` and ``status`` when given; ``None`` when there is none."""
    statement = select(*_COLUMNS)
    if kind is not None:
        statement = statement.where(BackupRecord.kind == kind)
    if status is not None:
        statement = statement.where(BackupRecord.status == status)
    rows = _rows(
        statement.order_by(BackupRecord.started_at.desc(), BackupRecord.id.desc()).limit(1)
    )
    return rows[0] if rows else None


def failed_after(since: datetime | None) -> tuple[BackupRow, ...]:
    """Every ``failed`` row that started after ``since`` (all when ``None``), oldest first."""
    statement = select(*_COLUMNS).where(BackupRecord.status == FAILED)
    if since is not None:
        statement = statement.where(BackupRecord.started_at > since)
    return tuple(_rows(statement.order_by(BackupRecord.started_at, BackupRecord.id)))


def rotation_candidates(before: datetime, keep_id: str | None) -> tuple[BackupRow, ...]:
    """Every ``verified`` row with its local copy still there that started before ``before``,
    except ``keep_id``, oldest first."""
    statement = select(*_COLUMNS).where(
        BackupRecord.status == VERIFIED,
        BackupRecord.local_deleted_at.is_(None),
        BackupRecord.started_at < before,
    )
    if keep_id is not None:
        statement = statement.where(BackupRecord.id != keep_id)
    return tuple(_rows(statement.order_by(BackupRecord.started_at, BackupRecord.id)))


def mark_deleted(ids: Iterable[str], at: datetime) -> int:
    """Set ``local_deleted_at`` to ``at`` on the rows ``ids`` while it is ``NULL``; the row
    count."""
    chosen = list(ids)
    if not chosen:
        return 0
    result = current().session.execute(
        update(BackupRecord)
        .where(BackupRecord.id.in_(chosen), BackupRecord.local_deleted_at.is_(None))
        .values(local_deleted_at=at)
        .execution_options(**_NO_SYNC)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


# The off-PC outcome (Story 1.17) ----------------------------------------------------------------


def set_offpc(
    backup_id: str,
    *,
    status: str,
    name: str | None,
    ledger: str | None,
    at: datetime,
    error: str | None,
) -> int:
    """Record the off-PC outcome on ``backup_id`` while none is recorded; the row count."""
    result = current().session.execute(
        update(BackupRecord)
        .where(BackupRecord.id == backup_id, BackupRecord.offpc_status.is_(None))
        .values(
            offpc_status=status,
            offpc_name=name,
            offpc_ledger=ledger,
            offpc_at=at,
            offpc_error=error,
        )
        .execution_options(**_NO_SYNC)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def newest_offpc(statuses: Iterable[str]) -> BackupRow | None:
    """The newest row whose off-PC status is one of ``statuses``, or ``None``."""
    chosen = list(statuses)
    if not chosen:
        return None
    rows = _rows(
        select(*_COLUMNS)
        .where(BackupRecord.offpc_status.in_(chosen))
        .order_by(BackupRecord.started_at.desc(), BackupRecord.id.desc())
        .limit(1)
    )
    return rows[0] if rows else None


def offpc_failed_after(since: datetime | None) -> tuple[BackupRow, ...]:
    """Every row whose off-PC copy failed that started after ``since`` (all when ``None``),
    oldest first."""
    statement = select(*_COLUMNS).where(BackupRecord.offpc_status == OFFPC_FAILED)
    if since is not None:
        statement = statement.where(BackupRecord.started_at > since)
    return tuple(_rows(statement.order_by(BackupRecord.started_at, BackupRecord.id)))


def mark_offpc_deleted(names: Iterable[str], at: datetime) -> int:
    """Set ``offpc_deleted_at`` to ``at`` on the rows whose ``offpc_name`` is one of ``names``,
    while it is ``NULL``; the row count."""
    chosen = list(names)
    if not chosen:
        return 0
    result = current().session.execute(
        update(BackupRecord)
        .where(BackupRecord.offpc_name.in_(chosen), BackupRecord.offpc_deleted_at.is_(None))
        .values(offpc_deleted_at=at)
        .execution_options(**_NO_SYNC)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


# The copy under test (Story 1.18) ---------------------------------------------------------------


def newest_offpc_copy() -> BackupRow | None:
    """The newest row whose off-PC copy is ``copied`` and not rotated out, or ``None``."""
    rows = _rows(
        select(*_COLUMNS)
        .where(
            BackupRecord.offpc_status == OFFPC_COPIED,
            BackupRecord.offpc_deleted_at.is_(None),
        )
        .order_by(BackupRecord.started_at.desc(), BackupRecord.id.desc())
        .limit(1)
    )
    return rows[0] if rows else None


# The copies an erasure leaves (Story 3.14) -------------------------------------------------------


def copies_holding(since: datetime, until: datetime) -> tuple[BackupRow, ...]:
    """Every ``verified`` row started from ``since`` to ``until`` with a local copy still there
    or an off-PC copy not yet rotated out, oldest first."""
    statement = select(*_COLUMNS).where(
        BackupRecord.status == VERIFIED,
        BackupRecord.started_at >= since,
        BackupRecord.started_at <= until,
        or_(
            BackupRecord.local_deleted_at.is_(None),
            and_(
                BackupRecord.offpc_status == OFFPC_COPIED,
                BackupRecord.offpc_deleted_at.is_(None),
            ),
        ),
    )
    return tuple(_rows(statement.order_by(BackupRecord.started_at, BackupRecord.id)))
