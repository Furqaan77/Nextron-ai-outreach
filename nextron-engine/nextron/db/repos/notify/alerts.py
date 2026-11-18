"""The ``alerts`` table's repository (AD-3, AD-15): the only SQL on it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores what the ``notify`` service gives it and checks nothing; the service
applies the Alert machine. Rows come back as ``AlertRow``, a frozen copy of every column.

- ``get(alert_id)``: the alert, or ``None``;
- ``find(kind, subject, channel)``: the alert with that natural key, or ``None``;
- ``insert_alert(...)``: a new row with no message id, error or resend; returns its id;
- ``update_seen(alert_id, seen_status, *, at, **changes)``: sets ``changes`` and ``updated_at``
  only while the alert still has the status the caller saw (the fence); returns the row count;
- ``subjects(kind)``: the subjects of every alert of ``kind``;
- ``about(subject)`` (Story 3.15): every column of each alert about ``subject``, of any
  kind, as a ``dict`` (a generic ``select`` of the table), by ``created_at``, then ``id``.
"""

from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import Executable, insert, select, update

from nextron.db.models.base import new_id
from nextron.db.models.notify import AlertRecord
from nextron.db.uow import current

_NO_SYNC = {"synchronize_session": False}


@dataclass(frozen=True, slots=True)
class AlertRow:
    """Every column of one ``alerts`` row."""

    id: str
    kind: str
    subject: str
    channel: str
    text: str
    status: str
    provider_message_id: str | None
    error_code: str | None
    resent_at: datetime | None
    created_at: datetime
    updated_at: datetime


_COLUMNS = tuple(getattr(AlertRecord, field.name) for field in fields(AlertRow))


def _one(statement: Executable) -> AlertRow | None:
    rows = [AlertRow(*row) for row in current().session.execute(statement)]
    return rows[0] if rows else None


def get(alert_id: str) -> AlertRow | None:
    """The alert ``alert_id``, or ``None``."""
    return _one(select(*_COLUMNS).where(AlertRecord.id == alert_id))


def find(kind: str, subject: str, channel: str) -> AlertRow | None:
    """The alert of ``kind`` about ``subject`` on ``channel``, or ``None``."""
    return _one(
        select(*_COLUMNS).where(
            AlertRecord.kind == kind,
            AlertRecord.subject == subject,
            AlertRecord.channel == channel,
        )
    )


def insert_alert(
    *, kind: str, subject: str, channel: str, text: str, status: str, at: datetime
) -> str:
    """Insert an alert with no message id, error code or resend, created and updated at
    ``at``; return its new id."""
    alert_id = new_id()
    current().session.execute(
        insert(AlertRecord).values(
            id=alert_id,
            kind=kind,
            subject=subject,
            channel=channel,
            text=text,
            status=status,
            provider_message_id=None,
            error_code=None,
            resent_at=None,
            created_at=at,
            updated_at=at,
        )
    )
    return alert_id


def update_seen(alert_id: str, seen_status: str, *, at: datetime, **changes: object) -> int:
    """Set ``changes`` and ``updated_at`` on ``alert_id`` only while its status is still
    ``seen_status``; the row count (0 when it changed meanwhile)."""
    statement = (
        update(AlertRecord)
        .where(AlertRecord.id == alert_id, AlertRecord.status == seen_status)
        .values(**changes, updated_at=at)
        .execution_options(**_NO_SYNC)
    )
    result = current().session.execute(statement)
    return int(result.rowcount)  # type: ignore[attr-defined]


def subjects(kind: str) -> set[str]:
    """The subjects of every alert of ``kind``."""
    statement = select(AlertRecord.subject).where(AlertRecord.kind == kind).distinct()
    return set(current().session.execute(statement).scalars())


def about(subject: str) -> tuple[dict[str, object], ...]:
    """Every column of each alert about ``subject``, by ``created_at``, then ``id``."""
    table = AlertRecord.__table__
    statement = (
        select(table)
        .where(table.c.subject == subject)
        .order_by(table.c.created_at, table.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
