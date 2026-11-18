"""The ``notifications`` table's repository (AD-3, UX-DR5): the only SQL on it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores what the ``notify`` service gives it and checks nothing. Rows come back
as ``NotificationRow``, a frozen copy of every column.

- ``find(kind, subject)``: the notification with that natural key, or ``None``;
- ``insert_notification(...)``: a new, undismissed row; returns its id;
- ``subjects(kind)``: the subjects of every notification of ``kind``;
- ``open_count()``: how many are undismissed; ``newest_open(limit)``: the newest ``limit``
  undismissed ones, newest first (``created_at`` then id, descending);
- ``dismiss(notification_id, at)``: sets ``dismissed_at`` only while it is ``NULL`` (the
  fence); returns the row count;
- ``about(subject)`` (Story 3.15): every column of each notification about ``subject``, of
  any kind, as a ``dict`` (a generic ``select`` of the table), by ``created_at``, then
  ``id``.
"""

from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import Executable, func, insert, select, update

from nextron.db.models.base import new_id
from nextron.db.models.notify import NotificationRecord
from nextron.db.uow import current

_NO_SYNC = {"synchronize_session": False}


@dataclass(frozen=True, slots=True)
class NotificationRow:
    """Every column of one ``notifications`` row."""

    id: str
    kind: str
    subject: str
    text: str
    created_at: datetime
    dismissed_at: datetime | None


_COLUMNS = tuple(getattr(NotificationRecord, field.name) for field in fields(NotificationRow))


def _rows(statement: Executable) -> list[NotificationRow]:
    return [NotificationRow(*row) for row in current().session.execute(statement)]


def find(kind: str, subject: str) -> NotificationRow | None:
    """The notification of ``kind`` about ``subject``, or ``None``."""
    rows = _rows(
        select(*_COLUMNS).where(
            NotificationRecord.kind == kind, NotificationRecord.subject == subject
        )
    )
    return rows[0] if rows else None


def insert_notification(*, kind: str, subject: str, text: str, at: datetime) -> str:
    """Insert an undismissed notification created at ``at``; return its new id."""
    notification_id = new_id()
    current().session.execute(
        insert(NotificationRecord).values(
            id=notification_id,
            kind=kind,
            subject=subject,
            text=text,
            created_at=at,
            dismissed_at=None,
        )
    )
    return notification_id


def subjects(kind: str) -> set[str]:
    """The subjects of every notification of ``kind``."""
    statement = (
        select(NotificationRecord.subject).where(NotificationRecord.kind == kind).distinct()
    )
    return set(current().session.execute(statement).scalars())


def open_count() -> int:
    """How many notifications are undismissed."""
    statement = (
        select(func.count())
        .select_from(NotificationRecord)
        .where(NotificationRecord.dismissed_at.is_(None))
    )
    return int(current().session.execute(statement).scalar_one())


def newest_open(limit: int) -> list[NotificationRow]:
    """The newest ``limit`` undismissed notifications, newest first."""
    statement = (
        select(*_COLUMNS)
        .where(NotificationRecord.dismissed_at.is_(None))
        .order_by(NotificationRecord.created_at.desc(), NotificationRecord.id.desc())
        .limit(limit)
    )
    return _rows(statement)


def dismiss(notification_id: str, at: datetime) -> int:
    """Set ``dismissed_at`` on ``notification_id`` only while it is undismissed; the row
    count."""
    statement = (
        update(NotificationRecord)
        .where(
            NotificationRecord.id == notification_id,
            NotificationRecord.dismissed_at.is_(None),
        )
        .values(dismissed_at=at)
        .execution_options(**_NO_SYNC)
    )
    result = current().session.execute(statement)
    return int(result.rowcount)  # type: ignore[attr-defined]


def about(subject: str) -> tuple[dict[str, object], ...]:
    """Every column of each notification about ``subject``, by ``created_at``, then ``id``."""
    table = NotificationRecord.__table__
    statement = (
        select(table)
        .where(table.c.subject == subject)
        .order_by(table.c.created_at, table.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
