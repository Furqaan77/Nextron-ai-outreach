"""The ``segments`` table's repository (FR-1, AD-3, AD-12): the only code that reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A definition is the canonical text ``nextron.domain.segments.definition_text``
returned; this module stores and returns it as it is and checks nothing.

- ``all_segments()``: every row as a ``StoredSegment``, by key;
- ``by_key(key)``: the row of ``key``, or ``None``;
- ``get(segment_id)`` (Story 2.9): the row with that id, or ``None``;
- ``segment_ids()``: every row's id;
- ``insert(key, definition, at, actor) -> id``: a new row at version 1, created and updated at
  ``at``, with its ``segment_versions`` row (version 1, ``actor``);
- ``update(segment_id, definition, version, at, actor)``: sets the row's definition, version and
  ``updated_at``, and inserts that version's ``segment_versions`` row (Story 11.5), so no
  Segment write can skip a version;
- ``version(segment_id, n)`` (Story 11.5): that version's ``StoredSegmentVersion``, or ``None``;
- ``versions(segment_id)``: every stored version of the Segment, oldest first.

``segment_versions`` rows are never updated or deleted (its triggers refuse it).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.setup import SegmentRecord, SegmentVersionRecord
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


@dataclass(frozen=True, slots=True)
class StoredSegmentVersion:
    """One ``segment_versions`` row."""

    id: str
    segment_id: str
    version: int
    definition: str
    created_at: datetime
    actor: str


_VERSION_COLUMNS = (
    SegmentVersionRecord.id,
    SegmentVersionRecord.segment_id,
    SegmentVersionRecord.version,
    SegmentVersionRecord.definition,
    SegmentVersionRecord.created_at,
    SegmentVersionRecord.actor,
)

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


def get(segment_id: str) -> StoredSegment | None:
    """The stored Segment with the row id ``segment_id``, or ``None``."""
    statement = select(*_COLUMNS).where(SegmentRecord.id == segment_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredSegment(*row)


def segment_ids() -> frozenset[str]:
    """Every stored Segment's id."""
    statement = select(SegmentRecord.id)
    return frozenset(current().session.execute(statement).scalars())


def _insert_version(
    segment_id: str, version: int, definition: str, at: datetime, actor: str
) -> None:
    current().session.execute(
        insert_rows(SegmentVersionRecord).values(
            id=new_id(),
            segment_id=segment_id,
            version=version,
            definition=definition,
            created_at=at,
            actor=actor,
        )
    )


def insert(key: str, definition: str, at: datetime, actor: str) -> str:
    """Store a new Segment at version 1, created at ``at``, with its version row; return its
    new ULID."""
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
    _insert_version(segment_id, FIRST_VERSION, definition, at, actor)
    return segment_id


def update(segment_id: str, definition: str, version: int, at: datetime, actor: str) -> None:
    """Set the definition and version of the Segment ``segment_id``, changed at ``at``, and
    store that version's row."""
    current().session.execute(
        update_rows(SegmentRecord)
        .where(SegmentRecord.id == segment_id)
        .values(definition=definition, version=version, updated_at=at)
    )
    _insert_version(segment_id, version, definition, at, actor)


def version(segment_id: str, number: int) -> StoredSegmentVersion | None:
    """Version ``number`` of the Segment ``segment_id``, or ``None``."""
    statement = select(*_VERSION_COLUMNS).where(
        SegmentVersionRecord.segment_id == segment_id,
        SegmentVersionRecord.version == number,
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredSegmentVersion(*row)


def versions(segment_id: str) -> tuple[StoredSegmentVersion, ...]:
    """Every stored version of the Segment ``segment_id``, oldest first."""
    statement = (
        select(*_VERSION_COLUMNS)
        .where(SegmentVersionRecord.segment_id == segment_id)
        .order_by(SegmentVersionRecord.version)
    )
    return tuple(StoredSegmentVersion(*row) for row in current().session.execute(statement))
