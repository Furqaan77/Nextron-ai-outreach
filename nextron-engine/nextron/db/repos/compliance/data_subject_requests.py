"""The ``data_subject_requests`` repository (FR-52, AD-3, AD-4, AD-17, Story 3.13): the only code
that writes the data subject requests.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.compliance`` does.

- ``StoredRequest``: one row (its ``request_ref`` never shows in the ``repr``);
- ``insert_open(*, contact_id, kind, request_ref, opened_at) -> id``: a new ``open`` request;
- ``complete(request_id, completed_at)``: sets ``status`` to ``completed`` and
  ``completed_at``;
- ``open_for(contact_id, kind, request_ref)``: the ``open`` request of that Contact, kind and
  reference (the oldest, by ``opened_at`` then ``id``), or ``None``;
- ``get(request_id)``: the row, or ``None``;
- ``completed_erasures(contact_ids)``: for each of those Contacts with a ``completed`` ``erase``
  request, its first one (by ``completed_at``, then ``id``: the one that erased it, or, after a
  retention erasure, the first request completed afterwards), as ``{contact_id:
  StoredRequest}``;
- ``ids_for_contacts(contact_ids)`` (Story 9.8): the ids of every request of those Contacts, by
  ``opened_at``, then ``id``;
- ``for_contact(contact_id)`` (Story 3.15): every column of each of the Contact's requests, as a
  ``dict`` (a generic ``select`` of the table, so a new column is exported without code), by
  ``opened_at``, then ``id``.

Nothing here removes a request: they are the proof of erasure, and the table's triggers refuse a
``DELETE`` too.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.compliance import DataSubjectRequestRecord
from nextron.db.uow import current
from nextron.domain.data_subject_request import RequestKind, RequestStatus

OPEN: Final = str(RequestStatus.OPEN)
COMPLETED: Final = str(RequestStatus.COMPLETED)
ERASE: Final = str(RequestKind.ERASE)
_TABLE: Final = DataSubjectRequestRecord.__table__


@dataclass(frozen=True, slots=True)
class StoredRequest:
    """One ``data_subject_requests`` row; its ``request_ref`` never shows in the ``repr``."""

    id: str
    contact_id: str
    kind: str
    status: str
    request_ref: str = field(repr=False)
    opened_at: datetime
    completed_at: datetime | None
    receipt_id: str | None


_COLUMNS = (
    DataSubjectRequestRecord.id,
    DataSubjectRequestRecord.contact_id,
    DataSubjectRequestRecord.kind,
    DataSubjectRequestRecord.status,
    DataSubjectRequestRecord.request_ref,
    DataSubjectRequestRecord.opened_at,
    DataSubjectRequestRecord.completed_at,
    DataSubjectRequestRecord.receipt_id,
)


def insert_open(*, contact_id: str, kind: str, request_ref: str, opened_at: datetime) -> str:
    """Store a new ``open`` request; return its new ULID."""
    request_id = new_id()
    current().session.execute(
        insert_rows(DataSubjectRequestRecord).values(
            id=request_id,
            contact_id=contact_id,
            kind=kind,
            status=OPEN,
            request_ref=request_ref,
            opened_at=opened_at,
        )
    )
    return request_id


def complete(request_id: str, completed_at: datetime) -> None:
    """Mark the request ``request_id`` ``completed`` at ``completed_at``."""
    current().session.execute(
        update_rows(DataSubjectRequestRecord)
        .where(DataSubjectRequestRecord.id == request_id)
        .values(status=COMPLETED, completed_at=completed_at)
    )


def open_for(contact_id: str, kind: str, request_ref: str) -> StoredRequest | None:
    """The oldest ``open`` request of the Contact ``contact_id`` with ``kind`` and
    ``request_ref``, or ``None``."""
    statement = (
        select(*_COLUMNS)
        .where(
            DataSubjectRequestRecord.contact_id == contact_id,
            DataSubjectRequestRecord.kind == kind,
            DataSubjectRequestRecord.request_ref == request_ref,
            DataSubjectRequestRecord.status == OPEN,
        )
        .order_by(DataSubjectRequestRecord.opened_at, DataSubjectRequestRecord.id)
        .limit(1)
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredRequest(*row)


def get(request_id: str) -> StoredRequest | None:
    """The request ``request_id``, or ``None``."""
    statement = select(*_COLUMNS).where(DataSubjectRequestRecord.id == request_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredRequest(*row)


def completed_erasures(contact_ids: Iterable[str]) -> dict[str, StoredRequest]:
    """The first ``completed`` ``erase`` request of each of ``contact_ids`` that has one."""
    wanted = sorted(set(contact_ids))
    if not wanted:
        return {}
    statement = (
        select(*_COLUMNS)
        .where(
            DataSubjectRequestRecord.contact_id.in_(wanted),
            DataSubjectRequestRecord.kind == ERASE,
            DataSubjectRequestRecord.status == COMPLETED,
        )
        .order_by(DataSubjectRequestRecord.completed_at, DataSubjectRequestRecord.id)
    )
    found: dict[str, StoredRequest] = {}
    for row in current().session.execute(statement):
        request = StoredRequest(*row)
        found.setdefault(request.contact_id, request)  # the first one completed
    return found


def ids_for_contacts(contact_ids: Iterable[str]) -> tuple[str, ...]:
    """The ids of every request, of any kind or status, of one of ``contact_ids``."""
    wanted = sorted(set(contact_ids))
    if not wanted:
        return ()
    statement = (
        select(DataSubjectRequestRecord.id)
        .where(DataSubjectRequestRecord.contact_id.in_(wanted))
        .order_by(DataSubjectRequestRecord.opened_at, DataSubjectRequestRecord.id)
    )
    return tuple(current().session.execute(statement).scalars())


def for_contact(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of the Contact's requests, by ``opened_at``, then ``id``."""
    statement = (
        select(_TABLE)
        .where(_TABLE.c.contact_id == contact_id)
        .order_by(_TABLE.c.opened_at, _TABLE.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
