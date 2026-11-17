"""The ``contacts`` table's repository (FR-14, FR-15, AD-3): the only code that writes it.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``), so writes run in the order they are called: reads work in a
read-only unit of work too, writes only inside the running use case's transaction. It checks
nothing; ``nextron.app.research`` does.

- ``StoredContact``: one row;
- ``get(contact_id)``: the row, or ``None``;
- ``for_lead(lead_id)``: the Lead's Contacts, by ``created_at``, then ``id``;
- ``insert(...) -> id``: a new row, created and updated at ``at``; ``is_primary`` is left to
  its server default (false) and ``tz`` and ``market_id`` are NULL;
- ``fill(contact_id, updated_at, **fields)``: sets the named columns and ``updated_at``;
- ``has_primary(lead_id)``: whether one of the Lead's Contacts is its Primary;
- ``move_to_lead(from_lead, to_lead, updated_at, clear_primary)``: gives every Contact of
  ``from_lead`` to ``to_lead`` (and clears their Primary flags when ``clear_primary``), in one
  statement.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.research import ContactRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredContact:
    """One ``contacts`` row."""

    id: str
    lead_id: str
    name: str | None
    role: str | None
    email: str | None
    address_class: str | None
    linkedin_url: str | None
    is_primary: bool
    tz: str | None
    market_id: str | None
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    ContactRecord.id,
    ContactRecord.lead_id,
    ContactRecord.name,
    ContactRecord.role,
    ContactRecord.email,
    ContactRecord.address_class,
    ContactRecord.linkedin_url,
    ContactRecord.is_primary,
    ContactRecord.tz,
    ContactRecord.market_id,
    ContactRecord.created_at,
    ContactRecord.updated_at,
)


def get(contact_id: str) -> StoredContact | None:
    """The Contact ``contact_id``, or ``None``."""
    statement = select(*_COLUMNS).where(ContactRecord.id == contact_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredContact(*row)


def for_lead(lead_id: str) -> tuple[StoredContact, ...]:
    """The Contacts of the Lead ``lead_id``, oldest first (by ``created_at``, then ``id``)."""
    statement = (
        select(*_COLUMNS)
        .where(ContactRecord.lead_id == lead_id)
        .order_by(ContactRecord.created_at, ContactRecord.id)
    )
    return tuple(StoredContact(*row) for row in current().session.execute(statement))


def insert(
    *,
    lead_id: str,
    name: str | None,
    role: str | None,
    email: str | None,
    address_class: str | None,
    linkedin_url: str | None,
    at: datetime,
) -> str:
    """Store a new Contact on the Lead ``lead_id``, created and updated at ``at``; return its
    new ULID."""
    contact_id = new_id()
    current().session.execute(
        insert_rows(ContactRecord).values(
            id=contact_id,
            lead_id=lead_id,
            name=name,
            role=role,
            email=email,
            address_class=address_class,
            linkedin_url=linkedin_url,
            created_at=at,
            updated_at=at,
        )
    )
    return contact_id


def fill(contact_id: str, updated_at: datetime, **fields: str | None) -> None:
    """Set the columns named in ``fields`` on the Contact ``contact_id``, and ``updated_at``;
    nothing when ``fields`` is empty."""
    if not fields:
        return
    current().session.execute(
        update_rows(ContactRecord)
        .where(ContactRecord.id == contact_id)
        .values(updated_at=updated_at, **fields)
    )


def has_primary(lead_id: str) -> bool:
    """Whether one of the Lead ``lead_id``'s Contacts is its Primary Contact."""
    statement = select(
        exists().where(ContactRecord.lead_id == lead_id, ContactRecord.is_primary.is_(True))
    )
    return bool(current().session.execute(statement).scalar_one())


def move_to_lead(
    from_lead: str, to_lead: str, updated_at: datetime, *, clear_primary: bool
) -> None:
    """Give every Contact of the Lead ``from_lead`` to ``to_lead``, updated at ``updated_at``;
    with ``clear_primary``, none of them stays a Primary."""
    values: dict[str, object] = {"lead_id": to_lead, "updated_at": updated_at}
    if clear_primary:
        values["is_primary"] = False
    current().session.execute(
        update_rows(ContactRecord).where(ContactRecord.lead_id == from_lead).values(**values)
    )
