"""The ``recovery_state`` table's repository (AD-3, AD-20, Story 1.19): the only SQL on it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores what the ``ops`` service gives it and checks nothing (the table's checks
and its one-``on`` index do). Rows come back as ``RecoveryRow``, a frozen copy of every column;
``steps`` is each recovery step and its status, in step order.

- ``insert(...)``: a new row; returns its new ULID;
- ``active()``: the ``on`` row, or ``None``;
- ``newest()``: the ``on`` row when there is one, else the newest row of any mode
  (``restored_at``, then id, descending), or ``None``. The ``on`` row comes first even when the
  clock was set back so that a superseded row's ``restored_at`` is later;
- ``supersede_active(at)``: turns the ``on`` row, if any, into ``superseded`` with ``ended_at``
  = ``at``; returns that row as it now is, or ``None``;
- ``set_step(recovery_id, step, status) -> int``: sets one step's status, only from
  ``pending`` and only on the ``on`` row; returns how many rows changed (0 or 1);
- ``end(recovery_id, at) -> int``: turns the ``on`` row ``off`` with ``ended_at`` = ``at`` (the
  table refuses it while a step is ``pending``); returns how many rows changed.
"""

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Final

from sqlalchemy import Executable, case, select, update
from sqlalchemy import insert as sql_insert

from nextron.db.models.base import new_id
from nextron.db.models.ops import RecoveryStateRecord
from nextron.db.uow import current
from nextron.domain.recovery import RecoveryMode, RecoveryStep, StepStatus

ON: Final = str(RecoveryMode.ON)


@dataclass(frozen=True, slots=True)
class RecoveryRow:
    """Every column of one ``recovery_state`` row."""

    id: str
    mode: str
    restored_at: datetime
    backup_at: datetime
    source_name: str
    pre_restore_name: str | None
    ended_at: datetime | None
    reapply_erasure_ledger: str
    mark_oms_unknown: str
    reingest_mailboxes: str
    repoll_unsubscribes: str

    @property
    def steps(self) -> tuple[tuple[RecoveryStep, StepStatus], ...]:
        """Each recovery step and its status, in step order."""
        return tuple((step, StepStatus(getattr(self, step.value))) for step in RecoveryStep)


_COLUMNS = tuple(getattr(RecoveryStateRecord, field.name) for field in fields(RecoveryRow))


def _rows(statement: Executable) -> list[RecoveryRow]:
    return [RecoveryRow(*row) for row in current().session.execute(statement)]


def _one(statement: Executable) -> RecoveryRow | None:
    rows = _rows(statement)
    return rows[0] if rows else None


def insert(
    *,
    mode: str,
    restored_at: datetime,
    backup_at: datetime,
    source_name: str,
    pre_restore_name: str | None,
    ended_at: datetime | None,
    steps: dict[RecoveryStep, str],
) -> str:
    """Insert one row, with ``steps`` giving every step's status; return its new id."""
    recovery_id = new_id()
    current().session.execute(
        sql_insert(RecoveryStateRecord).values(
            id=recovery_id,
            mode=mode,
            restored_at=restored_at,
            backup_at=backup_at,
            source_name=source_name,
            pre_restore_name=pre_restore_name,
            ended_at=ended_at,
            **{step.value: steps[step] for step in RecoveryStep},
        )
    )
    return recovery_id


def active() -> RecoveryRow | None:
    """The ``on`` row, or ``None``."""
    return _one(select(*_COLUMNS).where(RecoveryStateRecord.mode == ON))


def newest() -> RecoveryRow | None:
    """The ``on`` row, else the newest row of any mode, or ``None``."""
    on_first = case((RecoveryStateRecord.mode == ON, 1), else_=0)
    return _one(
        select(*_COLUMNS)
        .order_by(
            on_first.desc(),
            RecoveryStateRecord.restored_at.desc(),
            RecoveryStateRecord.id.desc(),
        )
        .limit(1)
    )


def supersede_active(at: datetime) -> RecoveryRow | None:
    """Turn the ``on`` row ``superseded`` at ``at``; that row as it now is, or ``None``."""
    found = active()
    if found is None:
        return None
    current().session.execute(
        update(RecoveryStateRecord)
        .where(RecoveryStateRecord.id == found.id, RecoveryStateRecord.mode == ON)
        .values(mode=str(RecoveryMode.SUPERSEDED), ended_at=at)
    )
    return _one(select(*_COLUMNS).where(RecoveryStateRecord.id == found.id))


def set_step(recovery_id: str, step: RecoveryStep, status: str) -> int:
    """Set ``step`` to ``status`` on the ``on`` row ``recovery_id`` while it is ``pending``;
    how many rows changed."""
    column = getattr(RecoveryStateRecord, RecoveryStep(step).value)
    result = current().session.execute(
        update(RecoveryStateRecord)
        .where(
            RecoveryStateRecord.id == recovery_id,
            RecoveryStateRecord.mode == ON,
            column == str(StepStatus.PENDING),
        )
        .values({column: status})
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def end(recovery_id: str, at: datetime) -> int:
    """Turn the ``on`` row ``recovery_id`` ``off`` at ``at``; how many rows changed."""
    result = current().session.execute(
        update(RecoveryStateRecord)
        .where(RecoveryStateRecord.id == recovery_id, RecoveryStateRecord.mode == ON)
        .values(mode=str(RecoveryMode.OFF), ended_at=at)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
