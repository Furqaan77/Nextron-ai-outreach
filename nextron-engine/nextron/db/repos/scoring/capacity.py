"""The ``daily_capacity`` table's repository (FR-19 S1, FR-59, AD-3; Story 4.10): the only code
that reads or writes it.

Every function runs explicit Core statements through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It stores what it is given and checks nothing;
``nextron.app.scoring.selection`` does.

- ``StoredCapacity``: one row, as stored.
- ``by_date(local_date)``: the row of that day, or ``None``.
- ``upsert(local_date, *, total, reserved, capacity, computed_at)``: the day's live figures
  (``total_cold_capacity``, ``follow_ups_reserved``, ``new_sequence_capacity``,
  ``computed_at``), inserted with ``picked`` 0 when the day has no row, else updated in place
  (``picked`` and ``signal_shortfall`` kept).
- ``add_picked(local_date, count)``: adds ``count`` to the day's ``picked``.
- ``finalise_before(local_date) -> int``: every row of an earlier day whose
  ``signal_shortfall`` is NULL gets ``max(0, new_sequence_capacity - picked)``; the number of
  rows finalised.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.scoring import DailyCapacityRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredCapacity:
    """One ``daily_capacity`` row."""

    id: str
    local_date: date
    total_cold_capacity: int
    follow_ups_reserved: int
    new_sequence_capacity: int
    picked: int
    signal_shortfall: int | None
    computed_at: datetime


_COLUMNS = (
    DailyCapacityRecord.id,
    DailyCapacityRecord.local_date,
    DailyCapacityRecord.total_cold_capacity,
    DailyCapacityRecord.follow_ups_reserved,
    DailyCapacityRecord.new_sequence_capacity,
    DailyCapacityRecord.picked,
    DailyCapacityRecord.signal_shortfall,
    DailyCapacityRecord.computed_at,
)


def by_date(local_date: date) -> StoredCapacity | None:
    """The row of ``local_date``, or ``None``."""
    row = (
        current()
        .session.execute(select(*_COLUMNS).where(DailyCapacityRecord.local_date == local_date))
        .one_or_none()
    )
    return None if row is None else StoredCapacity(*row)


def upsert(
    local_date: date, *, total: int, reserved: int, capacity: int, computed_at: datetime
) -> None:
    """Store the day's live figures; ``picked`` and ``signal_shortfall`` are kept."""
    session = current().session
    figures = {
        "total_cold_capacity": total,
        "follow_ups_reserved": reserved,
        "new_sequence_capacity": capacity,
        "computed_at": computed_at,
    }
    found = session.execute(
        select(DailyCapacityRecord.id).where(DailyCapacityRecord.local_date == local_date)
    ).scalar_one_or_none()
    if found is None:
        session.execute(
            insert_rows(DailyCapacityRecord).values(id=new_id(), local_date=local_date, **figures)
        )
        return
    session.execute(
        update_rows(DailyCapacityRecord)
        .where(DailyCapacityRecord.id == found)
        .values(**figures)
    )


def add_picked(local_date: date, count: int) -> None:
    """Add ``count`` to the day's ``picked``."""
    current().session.execute(
        update_rows(DailyCapacityRecord)
        .where(DailyCapacityRecord.local_date == local_date)
        .values(picked=DailyCapacityRecord.picked + count)
    )


def finalise_before(local_date: date) -> int:
    """Finalise every earlier day's open row; how many rows were finalised."""
    shortfall = func.max(0, DailyCapacityRecord.new_sequence_capacity - DailyCapacityRecord.picked)
    result = current().session.execute(
        update_rows(DailyCapacityRecord)
        .where(
            DailyCapacityRecord.local_date < local_date,
            DailyCapacityRecord.signal_shortfall.is_(None),
        )
        .values(signal_shortfall=shortfall)
    )
    return int(getattr(result, "rowcount", 0) or 0)
