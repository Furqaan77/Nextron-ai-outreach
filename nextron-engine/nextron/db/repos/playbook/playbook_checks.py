"""The ``playbook_checks`` table's repository (FR-71, AD-3, Story 4.4): the only code that reads
or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; the table's checks and
foreign key refuse a bad row (``IntegrityError``). A row is never updated or deleted.

- ``newest()``: the newest check (``checked_at``, then ``id``) as a ``StoredCheck``, or
  ``None``;
- ``newest_read()``: the newest check that read a snapshot (``playbook_sha256`` set), or
  ``None``;
- ``insert(checked_at, trigger, playbook_sha256, problem) -> id``: a new check.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select
from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.playbook import PlaybookCheckRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredCheck:
    """One ``playbook_checks`` row."""

    id: str
    checked_at: datetime
    trigger: str
    playbook_sha256: str | None
    problem: str | None


_COLUMNS = (
    PlaybookCheckRecord.id,
    PlaybookCheckRecord.checked_at,
    PlaybookCheckRecord.trigger,
    PlaybookCheckRecord.playbook_sha256,
    PlaybookCheckRecord.problem,
)


def _newest(statement: Select[tuple[object, ...]]) -> StoredCheck | None:
    ordered = statement.order_by(
        PlaybookCheckRecord.checked_at.desc(), PlaybookCheckRecord.id.desc()
    ).limit(1)
    row = current().session.execute(ordered).one_or_none()
    return None if row is None else StoredCheck(*row)


def newest() -> StoredCheck | None:
    """The newest check, or ``None`` when there is none."""
    return _newest(select(*_COLUMNS))


def newest_read() -> StoredCheck | None:
    """The newest check that read a snapshot, or ``None`` when there is none."""
    return _newest(select(*_COLUMNS).where(PlaybookCheckRecord.playbook_sha256.is_not(None)))


def insert(
    checked_at: datetime, trigger: str, playbook_sha256: str | None, problem: str | None
) -> str:
    """Store a new check; return its ULID."""
    check_id = new_id()
    current().session.execute(
        insert_rows(PlaybookCheckRecord).values(
            id=check_id,
            checked_at=checked_at,
            trigger=trigger,
            playbook_sha256=playbook_sha256,
            problem=problem,
        )
    )
    return check_id
