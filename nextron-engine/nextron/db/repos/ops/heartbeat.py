"""The ``runner_heartbeat`` table's repository (AD-3, AD-20): the only SQL on it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
``get`` works in a read-only unit of work too, ``save`` only inside the running use case's
transaction. The table holds one row, whose id is always ``HEARTBEAT_ID``; this module stores
what the ``ops`` service gives it and checks nothing. The row comes back as ``HeartbeatRow``, a
frozen copy of every column.

- ``get()``: the heartbeat row, or ``None`` before the first beat;
- ``save(*, pid, started_at, beat_at)``: one ``INSERT ... ON CONFLICT(id) DO UPDATE``, which
  updates the row, or inserts it when it is missing.
"""

from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from nextron.db.models.ops import HEARTBEAT_ID, HeartbeatRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class HeartbeatRow:
    """Every column of the ``runner_heartbeat`` row."""

    id: str
    pid: int
    started_at: datetime
    beat_at: datetime


_COLUMNS = tuple(getattr(HeartbeatRecord, field.name) for field in fields(HeartbeatRow))


def get() -> HeartbeatRow | None:
    """The heartbeat row, or ``None`` when nothing has beaten yet."""
    statement = select(*_COLUMNS).where(HeartbeatRecord.id == HEARTBEAT_ID)
    rows = [HeartbeatRow(*row) for row in current().session.execute(statement)]
    return rows[0] if rows else None


def save(*, pid: int, started_at: datetime, beat_at: datetime) -> None:
    """Store the heartbeat: the row updated, or inserted when it is missing."""
    statement = insert(HeartbeatRecord).values(
        id=HEARTBEAT_ID, pid=pid, started_at=started_at, beat_at=beat_at
    )
    current().session.execute(
        statement.on_conflict_do_update(
            index_elements=[HeartbeatRecord.id],
            set_={
                "pid": statement.excluded.pid,
                "started_at": statement.excluded.started_at,
                "beat_at": statement.excluded.beat_at,
            },
        )
    )
