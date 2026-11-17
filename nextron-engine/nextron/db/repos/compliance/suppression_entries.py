"""The ``suppression_entries`` repository (FR-50, FR-63, AD-3, AD-16): the only code that writes
the Suppression List.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.compliance`` does.

- ``StoredSuppressionEntry``: one row;
- readers: ``get(entry_id)``, the row or ``None``; ``active_for(kind, value_hash)``, the
  ``active`` row of that key or ``None``;
- the one writer: ``insert(...) -> id``, a new ``active`` row.

Nothing here removes a row, voids one or ages one out: entries are permanent (AD-16), and the
table's triggers refuse it too. Story 3.5 adds the void.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.compliance import SuppressionEntryRecord
from nextron.db.uow import current
from nextron.domain.suppression_entry import EntryStatus

ACTIVE: Final = str(EntryStatus.ACTIVE)


@dataclass(frozen=True, slots=True)
class StoredSuppressionEntry:
    """One ``suppression_entries`` row."""

    id: str
    kind: str
    value_hash: str
    domain_plain: str | None
    status: str
    source: str
    cause_ref: str | None
    created_at: datetime
    voided_at: datetime | None
    void_reason: str | None


_COLUMNS = (
    SuppressionEntryRecord.id,
    SuppressionEntryRecord.kind,
    SuppressionEntryRecord.value_hash,
    SuppressionEntryRecord.domain_plain,
    SuppressionEntryRecord.status,
    SuppressionEntryRecord.source,
    SuppressionEntryRecord.cause_ref,
    SuppressionEntryRecord.created_at,
    SuppressionEntryRecord.voided_at,
    SuppressionEntryRecord.void_reason,
)


def get(entry_id: str) -> StoredSuppressionEntry | None:
    """The entry ``entry_id``, or ``None``."""
    statement = select(*_COLUMNS).where(SuppressionEntryRecord.id == entry_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredSuppressionEntry(*row)


def active_for(kind: str, value_hash: str) -> StoredSuppressionEntry | None:
    """The ``active`` entry of the key (``kind``, ``value_hash``), or ``None``."""
    statement = select(*_COLUMNS).where(
        SuppressionEntryRecord.kind == kind,
        SuppressionEntryRecord.value_hash == value_hash,
        SuppressionEntryRecord.status == ACTIVE,
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredSuppressionEntry(*row)


def insert(
    *,
    kind: str,
    value_hash: str,
    domain_plain: str | None,
    source: str,
    cause_ref: str | None,
    created_at: datetime,
) -> str:
    """Store a new ``active`` entry; return its new ULID."""
    entry_id = new_id()
    current().session.execute(
        insert_rows(SuppressionEntryRecord).values(
            id=entry_id,
            kind=kind,
            value_hash=value_hash,
            domain_plain=domain_plain,
            status=ACTIVE,
            source=source,
            cause_ref=cause_ref,
            created_at=created_at,
        )
    )
    return entry_id
