"""The ``suppression_entries`` repository (FR-50, FR-63, AD-3, AD-16): the only code that writes
the Suppression List.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.compliance`` does.

- ``StoredSuppressionEntry``: one row; ``ListedEntry``: one row as the Suppression List panel
  lists it (Story 3.5), without its ``cause_ref``, ``voided_at`` or ``void_reason``;
- readers: ``get(entry_id)``, the row or ``None``; ``active_for(kind, value_hash)``, the
  ``active`` row of that key or ``None``; ``listed(kind=, source=, status=, limit=, offset=)``,
  at most ``limit`` rows matching the filters (each ``None`` for any), newest first (by
  ``created_at``, then ``id``, descending), after skipping ``offset``; ``count_listed(kind=,
  source=, status=)``, how many match; ``by_ids(ids)``, the rows of those ids, in no set order;
  ``active_ids_for_cause(source=, cause_ref=)``, the ids of the ``active`` rows of that source
  with that ``cause_ref``, by ``created_at``, then ``id``; ``voided_for(kind, value_hash)``
  (Story 3.13), the ``voided`` rows of that key, by ``id``; ``all_for_ledger()`` (Story 3.16),
  every row, by ``created_at``, then ``id``; ``voided_for_ledger()``, every ``voided`` row, by
  ``voided_at``, then ``id``;
- the writers: ``insert(...) -> id``, a new ``active`` row; ``void(entry_id, voided_at,
  void_reason) -> bool``, which sets one ``active`` row ``voided`` with its time and reason
  (``UPDATE ... WHERE id = ? AND status = 'active'``) and says whether it did;
  ``redact_void_reason(entry_id)`` (Story 3.13), which replaces a ``voided`` entry's
  ``void_reason`` (the Operator's free text) with its tombstone
  (``nextron.db.erasure.tombstoned``: ``[erased]``), keeping its ``value_hash``; nothing for
  an entry that isn't ``voided``.

Nothing here removes a row or ages one out: entries are permanent (AD-16), and the table's
triggers refuse it too (and refuse a void of any source but ``reply_rule``).
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement, func, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.erasure import tombstoned
from nextron.db.models.base import new_id
from nextron.db.models.compliance import SuppressionEntryRecord
from nextron.db.uow import current
from nextron.domain.suppression_entry import EntryStatus

ACTIVE: Final = str(EntryStatus.ACTIVE)
VOIDED: Final = str(EntryStatus.VOIDED)
TABLE: Final = "suppression_entries"


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


@dataclass(frozen=True, slots=True)
class ListedEntry:
    """One ``suppression_entries`` row as the Suppression List panel lists it."""

    id: str
    kind: str
    value_hash: str
    domain_plain: str | None
    status: str
    source: str
    created_at: datetime


_LISTED_COLUMNS = (
    SuppressionEntryRecord.id,
    SuppressionEntryRecord.kind,
    SuppressionEntryRecord.value_hash,
    SuppressionEntryRecord.domain_plain,
    SuppressionEntryRecord.status,
    SuppressionEntryRecord.source,
    SuppressionEntryRecord.created_at,
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


def _listed_where(
    *, kind: str | None, source: str | None, status: str | None
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if kind is not None:
        conditions.append(SuppressionEntryRecord.kind == kind)
    if source is not None:
        conditions.append(SuppressionEntryRecord.source == source)
    if status is not None:
        conditions.append(SuppressionEntryRecord.status == status)
    return conditions


def listed(
    *, kind: str | None, source: str | None, status: str | None, limit: int, offset: int
) -> tuple[ListedEntry, ...]:
    """At most ``limit`` entries matching the filters, newest first, after skipping
    ``offset``."""
    statement = (
        select(*_LISTED_COLUMNS)
        .where(*_listed_where(kind=kind, source=source, status=status))
        .order_by(SuppressionEntryRecord.created_at.desc(), SuppressionEntryRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return tuple(ListedEntry(*row) for row in current().session.execute(statement))


def count_listed(*, kind: str | None, source: str | None, status: str | None) -> int:
    """How many entries match the filters."""
    statement = (
        select(func.count())
        .select_from(SuppressionEntryRecord)
        .where(*_listed_where(kind=kind, source=source, status=status))
    )
    return int(current().session.execute(statement).scalar_one())


def by_ids(ids: Collection[str]) -> tuple[ListedEntry, ...]:
    """The entries whose id is in ``ids``, in no set order."""
    if not ids:
        return ()
    statement = select(*_LISTED_COLUMNS).where(SuppressionEntryRecord.id.in_(list(ids)))
    return tuple(ListedEntry(*row) for row in current().session.execute(statement))


def active_ids_for_cause(*, source: str, cause_ref: str) -> tuple[str, ...]:
    """The ids of the ``active`` entries of ``source`` with the ``cause_ref``, oldest first (by
    ``created_at``, then ``id``)."""
    statement = (
        select(SuppressionEntryRecord.id)
        .where(
            SuppressionEntryRecord.source == source,
            SuppressionEntryRecord.cause_ref == cause_ref,
            SuppressionEntryRecord.status == ACTIVE,
        )
        .order_by(SuppressionEntryRecord.created_at, SuppressionEntryRecord.id)
    )
    return tuple(current().session.execute(statement).scalars())


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


def void(entry_id: str, voided_at: datetime, void_reason: str) -> bool:
    """Set the ``active`` entry ``entry_id`` ``voided`` at ``voided_at`` with ``void_reason``;
    whether a row changed."""
    result = current().session.execute(
        update_rows(SuppressionEntryRecord)
        .where(SuppressionEntryRecord.id == entry_id, SuppressionEntryRecord.status == ACTIVE)
        .values(status=VOIDED, voided_at=voided_at, void_reason=void_reason)
    )
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


def voided_for(kind: str, value_hash: str) -> tuple[StoredSuppressionEntry, ...]:
    """The ``voided`` entries of the key (``kind``, ``value_hash``), by ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(
            SuppressionEntryRecord.kind == kind,
            SuppressionEntryRecord.value_hash == value_hash,
            SuppressionEntryRecord.status == VOIDED,
        )
        .order_by(SuppressionEntryRecord.id)
    )
    return tuple(StoredSuppressionEntry(*row) for row in current().session.execute(statement))


def all_for_ledger() -> tuple[StoredSuppressionEntry, ...]:
    """Every entry, oldest first (by ``created_at``, then ``id``)."""
    statement = select(*_COLUMNS).order_by(
        SuppressionEntryRecord.created_at, SuppressionEntryRecord.id
    )
    return tuple(StoredSuppressionEntry(*row) for row in current().session.execute(statement))


def voided_for_ledger() -> tuple[StoredSuppressionEntry, ...]:
    """Every ``voided`` entry, by ``voided_at``, then ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(SuppressionEntryRecord.status == VOIDED)
        .order_by(SuppressionEntryRecord.voided_at, SuppressionEntryRecord.id)
    )
    return tuple(StoredSuppressionEntry(*row) for row in current().session.execute(statement))


def redact_void_reason(entry_id: str) -> None:
    """Replace the ``voided`` entry ``entry_id``'s ``void_reason`` with its tombstone."""
    session = current().session
    row = session.execute(
        select(SuppressionEntryRecord.__table__).where(
            SuppressionEntryRecord.id == entry_id, SuppressionEntryRecord.status == VOIDED
        )
    ).mappings().one_or_none()
    if row is None:
        return
    session.execute(
        update_rows(SuppressionEntryRecord)
        .where(SuppressionEntryRecord.id == entry_id)
        .values(**tombstoned(TABLE, row))
    )
