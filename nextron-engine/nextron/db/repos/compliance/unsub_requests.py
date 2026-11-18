"""The ``unsub_requests`` repository (FR-70, FR-41, AD-3, AD-15, Story 3.11): the record of every
applied unsubscribe request.

Every function but ``new_request_id`` runs one explicit Core statement through the current unit
of work's session (``nextron.db.uow.current()``): the read works in a read-only unit of work too,
the write only inside the running transaction. It checks nothing; ``nextron.app.compliance`` does.

- ``StoredUnsubRequest``: one row;
- ``new_request_id()``: a new ULID for a request, made before its row is written, so the
  suppression entries it causes can name it as their ``cause_ref``;
- the reader ``for_token_hash(token_hash)``: the row with that hash, or ``None``;
- the one writer: ``insert(...)``, a new row with the given ``id``.

The table holds only the token's hash. Nothing here changes a row or takes one away: the
records are permanent, and the table's triggers refuse it too.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.compliance import UnsubRequestRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredUnsubRequest:
    """One ``unsub_requests`` row."""

    id: str
    token_hash: str
    received_at: datetime
    processed_at: datetime


_COLUMNS = (
    UnsubRequestRecord.id,
    UnsubRequestRecord.token_hash,
    UnsubRequestRecord.received_at,
    UnsubRequestRecord.processed_at,
)


def new_request_id() -> str:
    """A new ULID for an unsubscribe request."""
    return new_id()


def for_token_hash(token_hash: str) -> StoredUnsubRequest | None:
    """The request whose token hashes to ``token_hash``, or ``None``."""
    statement = select(*_COLUMNS).where(UnsubRequestRecord.token_hash == token_hash)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredUnsubRequest(*row)


def insert(
    *, request_id: str, token_hash: str, received_at: datetime, processed_at: datetime
) -> None:
    """Store a new request record under ``request_id``."""
    current().session.execute(
        insert_rows(UnsubRequestRecord).values(
            id=request_id,
            token_hash=token_hash,
            received_at=received_at,
            processed_at=processed_at,
        )
    )
