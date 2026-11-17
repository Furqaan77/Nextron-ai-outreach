"""The ``sending_inboxes`` repository (FR-32, AD-3, AD-21): the only code that writes Sending
Inboxes.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.sending`` does.

- ``StoredInbox``: one row with its Sending Domain's ``domain``;
- ``id_by_address(address)``: the id of the inbox holding that address, at any status, or
  ``None``;
- ``insert(...) -> id``: a new row at the given status;
- ``listed(statuses)``: the inboxes at those statuses, by ``created_at``, then ``id``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.sending import SendingDomainRecord, SendingInboxRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredInbox:
    """One ``sending_inboxes`` row and its Sending Domain's ``domain``."""

    id: str
    address: str
    label: str
    sending_domain_id: str
    domain: str
    from_name: str
    status: str
    created_at: datetime


def id_by_address(address: str) -> str | None:
    """The id of the inbox ``address``, at any status, or ``None``."""
    found = current().session.execute(
        select(SendingInboxRecord.id).where(SendingInboxRecord.address == address)
    )
    return found.scalar_one_or_none()


def insert(
    *,
    address: str,
    label: str,
    sending_domain_id: str,
    from_name: str,
    status: str,
    created_at: datetime,
) -> str:
    """Store a new Sending Inbox; return its new ULID."""
    inbox_id = new_id()
    current().session.execute(
        insert_rows(SendingInboxRecord).values(
            id=inbox_id,
            address=address,
            label=label,
            sending_domain_id=sending_domain_id,
            from_name=from_name,
            status=status,
            created_at=created_at,
        )
    )
    return inbox_id


def listed(statuses: Iterable[str]) -> list[StoredInbox]:
    """The inboxes at ``statuses``, oldest first (by ``created_at``, then ``id``)."""
    wanted = [str(status) for status in statuses]
    rows = current().session.execute(
        select(
            SendingInboxRecord.id,
            SendingInboxRecord.address,
            SendingInboxRecord.label,
            SendingInboxRecord.sending_domain_id,
            SendingDomainRecord.domain,
            SendingInboxRecord.from_name,
            SendingInboxRecord.status,
            SendingInboxRecord.created_at,
        )
        .join(SendingDomainRecord, SendingDomainRecord.id == SendingInboxRecord.sending_domain_id)
        .where(SendingInboxRecord.status.in_(wanted))
        .order_by(SendingInboxRecord.created_at, SendingInboxRecord.id)
    )
    return [StoredInbox(*row) for row in rows]
