"""The ``prompt_snapshots`` table's repository (FR-71, AD-3, AD-8): the only code that reads or
writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; a row is never updated
or deleted.

- ``get(route, prompt_version)``: the snapshot of that version as a ``StoredSnapshot``, or
  ``None``;
- ``insert(route, prompt_version, sha256, text, at) -> id``: a new snapshot created at ``at``.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.playbook import PromptSnapshotRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredSnapshot:
    """One ``prompt_snapshots`` row; its ``text`` never shows in the ``repr``."""

    id: str
    route: str
    prompt_version: str
    sha256: str
    text: str = field(repr=False)
    created_at: datetime


_COLUMNS = (
    PromptSnapshotRecord.id,
    PromptSnapshotRecord.route,
    PromptSnapshotRecord.prompt_version,
    PromptSnapshotRecord.sha256,
    PromptSnapshotRecord.text,
    PromptSnapshotRecord.created_at,
)


def get(route: str, prompt_version: str) -> StoredSnapshot | None:
    """The snapshot of ``route``'s ``prompt_version``, or ``None`` when it has no row."""
    statement = select(*_COLUMNS).where(
        PromptSnapshotRecord.route == str(route),
        PromptSnapshotRecord.prompt_version == str(prompt_version),
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredSnapshot(*row)


def insert(route: str, prompt_version: str, sha256: str, text: str, at: datetime) -> str:
    """Store ``text`` as ``route``'s ``prompt_version``, created at ``at``; return its ULID."""
    snapshot_id = new_id()
    current().session.execute(
        insert_rows(PromptSnapshotRecord).values(
            id=snapshot_id,
            route=route,
            prompt_version=prompt_version,
            sha256=sha256,
            text=text,
            created_at=at,
        )
    )
    return snapshot_id
