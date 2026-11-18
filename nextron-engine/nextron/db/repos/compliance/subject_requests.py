"""The ``data_subject_requests`` repository (FR-52, AD-3, AD-4, AD-17, Story 3.15): the only code
that writes the data subject requests.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.compliance`` does.

- ``StoredRequest``: one row (its ``request_ref`` left out of the ``repr``);
- ``insert(*, contact_id, kind, request_ref, opened_at) -> id``: a new ``open`` request with no
  ``completed_at`` or ``receipt_id``;
- ``get(request_id)``: the row, or ``None``;
- ``complete(request_id, completed_at) -> bool``: sets one ``open`` request ``completed`` at
  ``completed_at`` (``UPDATE ... WHERE id = ? AND status = 'open'``) and says whether it did;
- ``for_contact(contact_id)``: every column of each of the Contact's requests, as a ``dict`` (a
  generic ``select`` of the table, so a new column is exported without code), by ``opened_at``,
  then ``id``.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.compliance import DataSubjectRequestRecord
from nextron.db.uow import current
from nextron.domain.subject_request import RequestStatus

OPEN: Final = str(RequestStatus.OPEN)
COMPLETED: Final = str(RequestStatus.COMPLETED)
_TABLE: Final = DataSubjectRequestRecord.__table__


@dataclass(frozen=True, slots=True)
class StoredRequest:
    """One ``data_subject_requests`` row."""

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


def insert(*, contact_id: str, kind: str, request_ref: str, opened_at: datetime) -> str:
    """Store a new ``open`` request for the Contact ``contact_id``; return its new ULID."""
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


def get(request_id: str) -> StoredRequest | None:
    """The request ``request_id``, or ``None``."""
    statement = select(*_COLUMNS).where(DataSubjectRequestRecord.id == request_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredRequest(*row)


def complete(request_id: str, completed_at: datetime) -> bool:
    """Set the ``open`` request ``request_id`` ``completed`` at ``completed_at``; whether a row
    changed."""
    result = current().session.execute(
        update_rows(DataSubjectRequestRecord)
        .where(DataSubjectRequestRecord.id == request_id, DataSubjectRequestRecord.status == OPEN)
        .values(status=COMPLETED, completed_at=completed_at)
    )
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


def for_contact(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of the Contact's requests, by ``opened_at``, then ``id``."""
    statement = (
        select(_TABLE)
        .where(_TABLE.c.contact_id == contact_id)
        .order_by(_TABLE.c.opened_at, _TABLE.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
