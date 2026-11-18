"""The ``sending_domains`` repository (FR-32, AD-3): the only code that writes Sending Domains.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.sending`` does.

- ``StoredDomain``: one row;
- ``by_domain(domain)``: the row holding that registrable domain, or ``None``;
- ``by_id(domain_id)``: the row with that id, or ``None`` (Story 6.5);
- ``with_inboxes(statuses)``: the Sending Domains with at least one inbox at ``statuses``, by
  ``domain`` (Story 6.5);
- ``insert(domain, created_at) -> id``: a new row.
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
class StoredDomain:
    """One ``sending_domains`` row."""

    id: str
    domain: str
    created_at: datetime


def by_domain(domain: str) -> StoredDomain | None:
    """The Sending Domain ``domain``, or ``None``."""
    row = (
        current()
        .session.execute(
            select(
                SendingDomainRecord.id, SendingDomainRecord.domain, SendingDomainRecord.created_at
            ).where(SendingDomainRecord.domain == domain)
        )
        .one_or_none()
    )
    return None if row is None else StoredDomain(*row)


def by_id(domain_id: str) -> StoredDomain | None:
    """The Sending Domain with id ``domain_id``, or ``None``."""
    row = (
        current()
        .session.execute(
            select(
                SendingDomainRecord.id, SendingDomainRecord.domain, SendingDomainRecord.created_at
            ).where(SendingDomainRecord.id == domain_id)
        )
        .one_or_none()
    )
    return None if row is None else StoredDomain(*row)


def with_inboxes(statuses: Iterable[str]) -> list[StoredDomain]:
    """The Sending Domains with at least one inbox at ``statuses``, by ``domain``."""
    wanted = [str(status) for status in statuses]
    listed = (
        select(SendingInboxRecord.id)
        .where(SendingInboxRecord.sending_domain_id == SendingDomainRecord.id)
        .where(SendingInboxRecord.status.in_(wanted))
    )
    rows = current().session.execute(
        select(SendingDomainRecord.id, SendingDomainRecord.domain, SendingDomainRecord.created_at)
        .where(listed.exists())
        .order_by(SendingDomainRecord.domain)
    )
    return [StoredDomain(*row) for row in rows]


def insert(*, domain: str, created_at: datetime) -> str:
    """Store a new Sending Domain; return its new ULID."""
    domain_id = new_id()
    current().session.execute(
        insert_rows(SendingDomainRecord).values(id=domain_id, domain=domain, created_at=created_at)
    )
    return domain_id
