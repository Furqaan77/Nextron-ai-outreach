"""The ``sending_domains`` repository (FR-32, AD-3): the only code that writes Sending Domains.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.sending`` does.

- ``StoredDomain``: one row;
- ``by_domain(domain)``: the row holding that registrable domain, or ``None``;
- ``insert(domain, created_at) -> id``: a new row.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.sending import SendingDomainRecord
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


def insert(*, domain: str, created_at: datetime) -> str:
    """Store a new Sending Domain; return its new ULID."""
    domain_id = new_id()
    current().session.execute(
        insert_rows(SendingDomainRecord).values(id=domain_id, domain=domain, created_at=created_at)
    )
    return domain_id
