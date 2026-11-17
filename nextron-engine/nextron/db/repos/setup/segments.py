"""The ``segments`` table's repository (FR-1, AD-3, AD-12): the only code that reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A definition is the canonical text ``nextron.domain.segments.definition_text``
returned; this module stores and returns it as it is and checks nothing.

- ``all_segments()``: every row as a ``StoredSegment``, by key;
- ``by_key(key)``: the row of ``key``, or ``None``;
- ``insert(key, definition, at) -> id``: a new row at version 1, created and updated at ``at``;
- ``update(segment_id, definition, version, at)``: sets the row's definition, version and
  ``updated_at``.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.setup import SegmentRecord
from nextron.db.uow import current

FIRST_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredSegment:
    """One ``segments`` row."""

    id: str
    key: str
    definition: str
    version: int
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    SegmentRecord.id,
    SegmentRecord.key,
    SegmentRecord.definition,
    SegmentRecord.version,
    SegmentRecord.created_at,
    SegmentRecord.updated_at,
)


def all_segments() -> tuple[StoredSegment, ...]:
    """Every stored Segment, ordered by key."""
    statement = select(*_COLUMNS).order_by(SegmentRecord.key)
    return tuple(StoredSegment(*row) for row in current().session.execute(statement))


def by_key(key: str) -> StoredSegment | None:
    """The stored Segment ``key``, or ``None`` when it has no row."""
    statement = select(*_COLUMNS).where(SegmentRecord.key == str(key))
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredSegment(*row)


def insert(key: str, definition: str, at: datetime) -> str:
    """Store a new Segment at version 1, created at ``at``; return its new ULID."""
    segment_id = new_id()
    current().session.execute(
        insert_rows(SegmentRecord).values(
            id=segment_id,
            key=key,
            definition=definition,
            version=FIRST_VERSION,
            created_at=at,
            updated_at=at,
        )
    )
    return segment_id


def update(segment_id: str, definition: str, version: int, at: datetime) -> None:
    """Set the definition and version of the Segment ``segment_id``, changed at ``at``."""
    current().session.execute(
        update_rows(SegmentRecord)
        .where(SegmentRecord.id == segment_id)
        .values(definition=definition, version=version, updated_at=at)
    )
