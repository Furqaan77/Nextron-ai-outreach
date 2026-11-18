"""The ``erasure_receipts`` and ``erasure_receipt_stores`` repository (FR-52, AD-3, AD-17, Story
3.14): the only code that writes the erasure receipts.

Every function runs explicit Core statements through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.compliance`` does (and the
tables' checks and triggers).

Writers:

- ``insert_receipt(*, contact_id, request_ref, cause, outcome, created_at, stores) -> id``: a
  new receipt and one ``erasure_receipt_stores`` row per ``NewStore(store, action, status,
  detail)`` in ``stores``, each at ``created_at``;
- ``settle(contact_id, store, status, detail, at) -> int``: sets ``status``, ``detail`` and
  ``at`` on the ``pending`` rows of ``store`` on that Contact's receipts, and only those; the
  row count.

Readers:

- ``pending_count(contact_id, store) -> int`` and ``pending_rows(contact_id, store)``: the
  ``pending`` rows of ``store`` on the Contact's receipts (rows by receipt, oldest first);
- ``receipts_for(contact_ids) -> {contact_id: (StoredReceipt, ...)}``: each Contact's receipts,
  oldest first (``created_at``, then ``id``), each with its rows in ``ErasureStore`` order;
  a Contact without a receipt is left out;
- ``failed_rows()``: every ``failed`` row, as a ``FailedStore`` with its receipt's Contact,
  oldest first (``at``, then ``id``);
- ``for_export(contact_id) -> tuple[dict, ...]`` (Story 3.15's subject access export, added at
  the merge of Story 3.14): every column of each of the Contact's receipts (a generic
  ``select`` of the table), oldest first (``created_at``, then ``id``), each with ``stores``:
  every column of its store rows, in ``ErasureStore`` order.

Nothing here removes a receipt or a row: they are the proof of erasure, and the tables'
triggers refuse a ``DELETE`` too.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from sqlalchemy import Select, func, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.compliance import ErasureReceiptRecord, ErasureReceiptStoreRecord
from nextron.db.uow import current
from nextron.domain.erasure_receipt import ErasureStore, StoreStatus

PENDING: Final = str(StoreStatus.PENDING)
FAILED: Final = str(StoreStatus.FAILED)
_ORDER: Final = {str(store): index for index, store in enumerate(ErasureStore)}
_NO_SYNC = {"synchronize_session": False}


@dataclass(frozen=True, slots=True)
class NewStore:
    """One store row to insert with a receipt."""

    store: str
    action: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class StoredReceiptStore:
    """One ``erasure_receipt_stores`` row."""

    id: str
    receipt_id: str
    store: str
    action: str
    status: str
    detail: str
    at: datetime


@dataclass(frozen=True, slots=True)
class StoredReceipt:
    """One ``erasure_receipts`` row with its store rows; its ``request_ref`` never shows in the
    ``repr``."""

    id: str
    contact_id: str
    request_ref: str | None = field(repr=False)
    cause: str
    outcome: str
    created_at: datetime
    stores: tuple[StoredReceiptStore, ...] = ()


@dataclass(frozen=True, slots=True)
class FailedStore:
    """One ``failed`` store row, with its receipt's Contact."""

    id: str
    receipt_id: str
    contact_id: str
    store: str
    detail: str
    at: datetime


_RECEIPT_COLUMNS = (
    ErasureReceiptRecord.id,
    ErasureReceiptRecord.contact_id,
    ErasureReceiptRecord.request_ref,
    ErasureReceiptRecord.cause,
    ErasureReceiptRecord.outcome,
    ErasureReceiptRecord.created_at,
)
_STORE_COLUMNS = (
    ErasureReceiptStoreRecord.id,
    ErasureReceiptStoreRecord.receipt_id,
    ErasureReceiptStoreRecord.store,
    ErasureReceiptStoreRecord.action,
    ErasureReceiptStoreRecord.status,
    ErasureReceiptStoreRecord.detail,
    ErasureReceiptStoreRecord.at,
)


def insert_receipt(
    *,
    contact_id: str,
    request_ref: str | None,
    cause: str,
    outcome: str,
    created_at: datetime,
    stores: Sequence[NewStore],
) -> str:
    """Store a new receipt and its store rows; return the receipt's new ULID."""
    receipt_id = new_id()
    session = current().session
    session.execute(
        insert_rows(ErasureReceiptRecord).values(
            id=receipt_id,
            contact_id=contact_id,
            request_ref=request_ref,
            cause=cause,
            outcome=outcome,
            created_at=created_at,
        )
    )
    for row in stores:
        session.execute(
            insert_rows(ErasureReceiptStoreRecord).values(
                id=new_id(),
                receipt_id=receipt_id,
                store=row.store,
                action=row.action,
                status=row.status,
                detail=row.detail,
                at=created_at,
            )
        )
    return receipt_id


def _receipts_of(contact_id: str) -> Select[tuple[str]]:
    return select(ErasureReceiptRecord.id).where(ErasureReceiptRecord.contact_id == contact_id)


def settle(contact_id: str, store: str, status: str, detail: str, at: datetime) -> int:
    """Settle the ``pending`` rows of ``store`` on the Contact's receipts; the row count."""
    result = current().session.execute(
        update_rows(ErasureReceiptStoreRecord)
        .where(
            ErasureReceiptStoreRecord.receipt_id.in_(_receipts_of(contact_id)),
            ErasureReceiptStoreRecord.store == store,
            ErasureReceiptStoreRecord.status == PENDING,
        )
        .values(status=status, detail=detail, at=at)
        .execution_options(**_NO_SYNC)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def pending_rows(contact_id: str, store: str) -> tuple[StoredReceiptStore, ...]:
    """The ``pending`` rows of ``store`` on the Contact's receipts, by receipt."""
    statement = (
        select(*_STORE_COLUMNS)
        .where(
            ErasureReceiptStoreRecord.receipt_id.in_(_receipts_of(contact_id)),
            ErasureReceiptStoreRecord.store == store,
            ErasureReceiptStoreRecord.status == PENDING,
        )
        .order_by(ErasureReceiptStoreRecord.receipt_id, ErasureReceiptStoreRecord.id)
    )
    return tuple(StoredReceiptStore(*row) for row in current().session.execute(statement))


def pending_count(contact_id: str, store: str) -> int:
    """How many ``pending`` rows of ``store`` the Contact's receipts hold."""
    statement = select(func.count()).where(
        ErasureReceiptStoreRecord.receipt_id.in_(_receipts_of(contact_id)),
        ErasureReceiptStoreRecord.store == store,
        ErasureReceiptStoreRecord.status == PENDING,
    )
    return int(current().session.execute(statement).scalar_one())


def receipts_for(contact_ids: Iterable[str]) -> dict[str, tuple[StoredReceipt, ...]]:
    """Each of ``contact_ids``' receipts, oldest first, each with its rows in store order."""
    wanted = sorted(set(contact_ids))
    if not wanted:
        return {}
    session = current().session
    receipts = [
        StoredReceipt(*row)
        for row in session.execute(
            select(*_RECEIPT_COLUMNS)
            .where(ErasureReceiptRecord.contact_id.in_(wanted))
            .order_by(ErasureReceiptRecord.created_at, ErasureReceiptRecord.id)
        )
    ]
    if not receipts:
        return {}
    rows: dict[str, list[StoredReceiptStore]] = {}
    for row in session.execute(
        select(*_STORE_COLUMNS).where(
            ErasureReceiptStoreRecord.receipt_id.in_([receipt.id for receipt in receipts])
        )
    ):
        stored = StoredReceiptStore(*row)
        rows.setdefault(stored.receipt_id, []).append(stored)
    found: dict[str, list[StoredReceipt]] = {}
    for receipt in receipts:
        ordered = sorted(
            rows.get(receipt.id, ()), key=lambda r: (_ORDER.get(r.store, len(_ORDER)), r.id)
        )
        found.setdefault(receipt.contact_id, []).append(
            StoredReceipt(
                receipt.id,
                receipt.contact_id,
                receipt.request_ref,
                receipt.cause,
                receipt.outcome,
                receipt.created_at,
                tuple(ordered),
            )
        )
    return {contact_id: tuple(items) for contact_id, items in found.items()}


def failed_rows() -> tuple[FailedStore, ...]:
    """Every ``failed`` store row with its receipt's Contact, oldest first."""
    statement = (
        select(
            ErasureReceiptStoreRecord.id,
            ErasureReceiptStoreRecord.receipt_id,
            ErasureReceiptRecord.contact_id,
            ErasureReceiptStoreRecord.store,
            ErasureReceiptStoreRecord.detail,
            ErasureReceiptStoreRecord.at,
        )
        .join(
            ErasureReceiptRecord,
            ErasureReceiptRecord.id == ErasureReceiptStoreRecord.receipt_id,
        )
        .where(ErasureReceiptStoreRecord.status == FAILED)
        .order_by(ErasureReceiptStoreRecord.at, ErasureReceiptStoreRecord.id)
    )
    return tuple(FailedStore(*row) for row in current().session.execute(statement))


def for_export(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of each of the Contact's receipts, oldest first, each with ``stores``."""
    receipts_table = ErasureReceiptRecord.__table__
    stores_table = ErasureReceiptStoreRecord.__table__
    session = current().session
    receipts = [
        dict(row._mapping)
        for row in session.execute(
            select(receipts_table)
            .where(receipts_table.c.contact_id == contact_id)
            .order_by(receipts_table.c.created_at, receipts_table.c.id)
        )
    ]
    if not receipts:
        return ()
    rows: dict[object, list[dict[str, object]]] = {}
    for row in session.execute(
        select(stores_table).where(
            stores_table.c.receipt_id.in_([receipt["id"] for receipt in receipts])
        )
    ):
        stored = dict(row._mapping)
        rows.setdefault(stored["receipt_id"], []).append(stored)
    return tuple(
        {
            **receipt,
            "stores": sorted(
                rows.get(receipt["id"], ()),
                key=lambda r: (_ORDER.get(str(r["store"]), len(_ORDER)), str(r["id"])),
            ),
        }
        for receipt in receipts
    )
