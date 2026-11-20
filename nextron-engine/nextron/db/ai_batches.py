"""The Message Batch store (AD-3, AD-8, INT-7; Story 4.17): the only writer of ``ai_batches`` and
``ai_batch_items``, and the only SQL on them. A shared writer (``nextron.db.owners.
SHARED_WRITERS``): the tables are shared by row owner, so every function takes a
``nextron.domain.ai_batch.BatchOwner`` (anything else is a ``TypeError``) and reads or writes
only that owner's rows. ``tests/contracts/test_ai_batch_owners.py`` holds each service under
``nextron/app/<owner>/`` to its own ``BatchOwner``.

Every function runs explicit Core statements through the current unit of work's session: reads
work in a read-only unit of work too, writes only inside the running use case's transaction.
Nothing here holds text: ids, codes, hashes and times only.

- ``StoredBatch`` and ``StoredItem``: one row of each table.
- ``queue_item(owner, *, route, target_id, rev, context_id, at) -> str | None``: a new
  ``pending`` item (its ``custom_id`` built by ``domain.ai_batch.custom_id``), or ``None`` when
  the owner already holds an item of that ``(route, target_id, rev)``.
- ``pending(owner, route)``: the route's ``pending`` items, oldest first (``created_at``, then
  ``id``); ``oldest_pending_at(owner)``: the oldest ``pending`` item's ``created_at``, or
  ``None``.
- ``insert_batch(owner, *, provider_batch_id, route, route_tuple_key, at) -> str``: a
  ``submitted`` batch row.
- ``mark_submitted(owner, item_id, *, batch_row_id, fingerprint, at)``: a ``pending`` item sent
  in that batch with that fingerprint (``Fingerprint.encode()``).
- ``settle(owner, item_id, status, at) -> bool``: a ``pending`` or ``submitted`` item moved to
  a terminal ``ItemStatus``; ``False`` (nothing written) when it was terminal already, so a
  re-poll never settles an item twice.
- ``next_rev(owner, route, target_id) -> int``: one more than the highest ``rev`` the owner
  holds for that route and target (any status), or 1 (Story 4.18).
- ``open_items(owner, route)``: the route's ``pending`` and ``submitted`` items, oldest first
  (Story 4.18).
- ``open_batches(owner)``: the batches not yet deleted, oldest first; ``item(owner,
  item_id)``: one item, or ``None``; ``items_of(owner, batch_row_id)``: a batch's items, by
  ``id``.
- ``mark_ended(owner, batch_row_id, at)``: a ``submitted`` batch becomes ``ended``;
  ``mark_deleted(owner, batch_row_id, at)``: a batch becomes ``deleted`` (``ended_at`` set too
  when it had none).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import func, insert, select, update

from nextron.db.models.ai_batches import AiBatchItemRecord, AiBatchRecord
from nextron.db.models.base import new_id, parse_utc_text
from nextron.db.uow import current
from nextron.domain.ai_batch import TERMINAL, BatchOwner, BatchStatus, ItemStatus, custom_id

OPEN_ITEM: Final = (str(ItemStatus.PENDING), str(ItemStatus.SUBMITTED))


@dataclass(frozen=True, slots=True)
class StoredBatch:
    """One ``ai_batches`` row."""

    id: str
    owner: str
    provider_batch_id: str
    route: str
    route_tuple_key: str
    status: str
    submitted_at: datetime
    ended_at: datetime | None
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class StoredItem:
    """One ``ai_batch_items`` row."""

    id: str
    owner: str
    batch_row_id: str | None
    route: str
    custom_id: str
    target_id: str
    rev: int
    context_id: str | None
    fingerprint: str | None
    status: str
    created_at: datetime
    updated_at: datetime


_BATCH_COLUMNS = (
    AiBatchRecord.id,
    AiBatchRecord.owner,
    AiBatchRecord.provider_batch_id,
    AiBatchRecord.route,
    AiBatchRecord.route_tuple_key,
    AiBatchRecord.status,
    AiBatchRecord.submitted_at,
    AiBatchRecord.ended_at,
    AiBatchRecord.deleted_at,
)
_ITEM_COLUMNS = (
    AiBatchItemRecord.id,
    AiBatchItemRecord.owner,
    AiBatchItemRecord.batch_row_id,
    AiBatchItemRecord.route,
    AiBatchItemRecord.custom_id,
    AiBatchItemRecord.target_id,
    AiBatchItemRecord.rev,
    AiBatchItemRecord.context_id,
    AiBatchItemRecord.fingerprint,
    AiBatchItemRecord.status,
    AiBatchItemRecord.created_at,
    AiBatchItemRecord.updated_at,
)


def _owner(owner: object) -> str:
    if not isinstance(owner, BatchOwner):
        raise TypeError("owner must be a BatchOwner")
    return str(owner)


# Items ------------------------------------------------------------------------------------------


def queue_item(
    owner: BatchOwner,
    *,
    route: str,
    target_id: str,
    rev: int,
    context_id: str | None,
    at: datetime,
) -> str | None:
    """A new ``pending`` item, or ``None`` when the owner holds one of that key already."""
    name = _owner(owner)
    made = custom_id(route, target_id, rev)  # ValueError for a bad route, target or rev
    session = current().session
    held = session.execute(
        select(AiBatchItemRecord.id).where(
            AiBatchItemRecord.owner == name,
            AiBatchItemRecord.route == route,
            AiBatchItemRecord.target_id == target_id,
            AiBatchItemRecord.rev == rev,
        )
    ).first()
    if held is not None:
        return None
    item_id = new_id()
    session.execute(
        insert(AiBatchItemRecord).values(
            id=item_id,
            owner=name,
            batch_row_id=None,
            route=route,
            custom_id=made,
            target_id=target_id,
            rev=rev,
            context_id=context_id,
            fingerprint=None,
            status=str(ItemStatus.PENDING),
            created_at=at,
            updated_at=at,
        )
    )
    return item_id


def pending(owner: BatchOwner, route: str) -> tuple[StoredItem, ...]:
    """The route's ``pending`` items, oldest first."""
    statement = (
        select(*_ITEM_COLUMNS)
        .where(
            AiBatchItemRecord.owner == _owner(owner),
            AiBatchItemRecord.route == route,
            AiBatchItemRecord.status == str(ItemStatus.PENDING),
        )
        .order_by(AiBatchItemRecord.created_at, AiBatchItemRecord.id)
    )
    return tuple(StoredItem(*row) for row in current().session.execute(statement))


def next_rev(owner: BatchOwner, route: str, target_id: str) -> int:
    """One more than the highest ``rev`` the owner holds for ``(route, target_id)``, or 1."""
    statement = select(func.max(AiBatchItemRecord.rev)).where(
        AiBatchItemRecord.owner == _owner(owner),
        AiBatchItemRecord.route == route,
        AiBatchItemRecord.target_id == target_id,
    )
    found = current().session.execute(statement).scalar_one()
    return 1 if found is None else int(found) + 1


def open_items(owner: BatchOwner, route: str) -> tuple[StoredItem, ...]:
    """The route's ``pending`` and ``submitted`` items, oldest first."""
    statement = (
        select(*_ITEM_COLUMNS)
        .where(
            AiBatchItemRecord.owner == _owner(owner),
            AiBatchItemRecord.route == route,
            AiBatchItemRecord.status.in_(OPEN_ITEM),
        )
        .order_by(AiBatchItemRecord.created_at, AiBatchItemRecord.id)
    )
    return tuple(StoredItem(*row) for row in current().session.execute(statement))


def oldest_pending_at(owner: BatchOwner) -> datetime | None:
    """The oldest ``pending`` item's ``created_at``, or ``None``."""
    statement = select(func.min(AiBatchItemRecord.created_at)).where(
        AiBatchItemRecord.owner == _owner(owner),
        AiBatchItemRecord.status == str(ItemStatus.PENDING),
    )
    found = current().session.execute(statement).scalar_one()
    if found is None:
        return None
    return found if isinstance(found, datetime) else parse_utc_text(str(found))


def mark_submitted(
    owner: BatchOwner, item_id: str, *, batch_row_id: str, fingerprint: str, at: datetime
) -> None:
    """A ``pending`` item sent in ``batch_row_id`` with ``fingerprint``."""
    current().session.execute(
        update(AiBatchItemRecord)
        .where(
            AiBatchItemRecord.owner == _owner(owner),
            AiBatchItemRecord.id == item_id,
            AiBatchItemRecord.status == str(ItemStatus.PENDING),
        )
        .values(
            status=str(ItemStatus.SUBMITTED),
            batch_row_id=batch_row_id,
            fingerprint=fingerprint,
            updated_at=at,
        )
    )


def settle(owner: BatchOwner, item_id: str, status: ItemStatus, at: datetime) -> bool:
    """A ``pending`` or ``submitted`` item moved to the terminal ``status``; ``False`` when it
    was terminal already."""
    if status not in TERMINAL:
        raise ValueError("an item settles only to a terminal status")
    result = current().session.execute(
        update(AiBatchItemRecord)
        .where(
            AiBatchItemRecord.owner == _owner(owner),
            AiBatchItemRecord.id == item_id,
            AiBatchItemRecord.status.in_(OPEN_ITEM),
        )
        .values(status=str(status), updated_at=at)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def item(owner: BatchOwner, item_id: str) -> StoredItem | None:
    """The owner's item ``item_id``, or ``None``."""
    statement = select(*_ITEM_COLUMNS).where(
        AiBatchItemRecord.owner == _owner(owner), AiBatchItemRecord.id == item_id
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredItem(*row)


def items_of(owner: BatchOwner, batch_row_id: str) -> tuple[StoredItem, ...]:
    """The batch's items, by ``id``."""
    statement = (
        select(*_ITEM_COLUMNS)
        .where(
            AiBatchItemRecord.owner == _owner(owner),
            AiBatchItemRecord.batch_row_id == batch_row_id,
        )
        .order_by(AiBatchItemRecord.id)
    )
    return tuple(StoredItem(*row) for row in current().session.execute(statement))


# Batches ----------------------------------------------------------------------------------------


def insert_batch(
    owner: BatchOwner,
    *,
    provider_batch_id: str,
    route: str,
    route_tuple_key: str,
    at: datetime,
) -> str:
    """A ``submitted`` batch row; its id."""
    batch_row_id = new_id()
    current().session.execute(
        insert(AiBatchRecord).values(
            id=batch_row_id,
            owner=_owner(owner),
            provider_batch_id=provider_batch_id,
            route=route,
            route_tuple_key=route_tuple_key,
            status=str(BatchStatus.SUBMITTED),
            submitted_at=at,
            ended_at=None,
            deleted_at=None,
        )
    )
    return batch_row_id


def open_batches(owner: BatchOwner) -> tuple[StoredBatch, ...]:
    """The owner's batches not yet deleted, oldest first."""
    statement = (
        select(*_BATCH_COLUMNS)
        .where(
            AiBatchRecord.owner == _owner(owner),
            AiBatchRecord.status != str(BatchStatus.DELETED),
        )
        .order_by(AiBatchRecord.submitted_at, AiBatchRecord.id)
    )
    return tuple(StoredBatch(*row) for row in current().session.execute(statement))


def mark_ended(owner: BatchOwner, batch_row_id: str, at: datetime) -> None:
    """A ``submitted`` batch becomes ``ended`` at ``at``."""
    current().session.execute(
        update(AiBatchRecord)
        .where(
            AiBatchRecord.owner == _owner(owner),
            AiBatchRecord.id == batch_row_id,
            AiBatchRecord.status == str(BatchStatus.SUBMITTED),
        )
        .values(status=str(BatchStatus.ENDED), ended_at=at)
    )


def mark_deleted(owner: BatchOwner, batch_row_id: str, at: datetime) -> None:
    """A batch becomes ``deleted`` at ``at`` (``ended_at`` too when it had none)."""
    name = _owner(owner)
    session = current().session
    session.execute(
        update(AiBatchRecord)
        .where(
            AiBatchRecord.owner == name,
            AiBatchRecord.id == batch_row_id,
            AiBatchRecord.ended_at.is_(None),
        )
        .values(ended_at=at)
    )
    session.execute(
        update(AiBatchRecord)
        .where(
            AiBatchRecord.owner == name,
            AiBatchRecord.id == batch_row_id,
            AiBatchRecord.status != str(BatchStatus.DELETED),
        )
        .values(status=str(BatchStatus.DELETED), deleted_at=at)
    )
