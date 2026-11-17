"""The ``playbook_snapshots`` table's repository (FR-71, AD-3, AD-8, Story 4.4): the only code
that reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; the table's checks and
unique ``sha256`` refuse a bad row (``IntegrityError``). A row is never updated or deleted.

- ``get(sha256)``: the snapshot with that SHA-256 as a ``StoredPlaybook``, or ``None``;
- ``insert(sha256, version, content, at) -> id``: a new snapshot captured at ``at``.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.playbook import PlaybookSnapshotRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredPlaybook:
    """One ``playbook_snapshots`` row; its ``content`` never shows in the ``repr``."""

    id: str
    sha256: str
    version: str
    content: str = field(repr=False)
    captured_at: datetime


_COLUMNS = (
    PlaybookSnapshotRecord.id,
    PlaybookSnapshotRecord.sha256,
    PlaybookSnapshotRecord.version,
    PlaybookSnapshotRecord.content,
    PlaybookSnapshotRecord.captured_at,
)


def get(sha256: str) -> StoredPlaybook | None:
    """The snapshot whose SHA-256 is ``sha256``, or ``None`` when it has no row."""
    statement = select(*_COLUMNS).where(PlaybookSnapshotRecord.sha256 == str(sha256))
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredPlaybook(*row)


def insert(sha256: str, version: str, content: str, at: datetime) -> str:
    """Store ``content`` as the snapshot ``sha256`` of ``version``, captured at ``at``; return
    its ULID."""
    snapshot_id = new_id()
    current().session.execute(
        insert_rows(PlaybookSnapshotRecord).values(
            id=snapshot_id, sha256=sha256, version=version, content=content, captured_at=at
        )
    )
    return snapshot_id
