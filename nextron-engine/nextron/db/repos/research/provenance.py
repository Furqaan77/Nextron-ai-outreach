"""The ``provenance_records`` table's repository (FR-51, AD-3, AD-7): the only code that writes
it; ``research`` is the sole writer of Provenance.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.research`` does.

- ``StoredProvenance``: one row;
- ``insert(...) -> id``: a new record; ``notes`` and the eligibility-basis fields are NULL;
- ``for_contact(contact_id)``: the Contact's records, by ``retrieved_at``, then ``id``;
- ``for_lead(lead_id)`` (Story 2.4): the records of every Contact of the Lead, by
  ``retrieved_at``, then ``id``, in one statement joining ``contacts``.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.research import ContactRecord, ProvenanceRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredProvenance:
    """One ``provenance_records`` row."""

    id: str
    contact_id: str
    subject: str
    source_kind: str
    source_detail: str
    address: str | None
    retrieved_at: datetime
    address_class: str | None
    notes: str | None
    basis_rule: str | None
    basis_campaign_id: str | None
    lia_ref: str | None
    lia_date: date | None
    basis_recorded_at: datetime | None


_COLUMNS = (
    ProvenanceRecord.id,
    ProvenanceRecord.contact_id,
    ProvenanceRecord.subject,
    ProvenanceRecord.source_kind,
    ProvenanceRecord.source_detail,
    ProvenanceRecord.address,
    ProvenanceRecord.retrieved_at,
    ProvenanceRecord.address_class,
    ProvenanceRecord.notes,
    ProvenanceRecord.basis_rule,
    ProvenanceRecord.basis_campaign_id,
    ProvenanceRecord.lia_ref,
    ProvenanceRecord.lia_date,
    ProvenanceRecord.basis_recorded_at,
)


def insert(
    *,
    contact_id: str,
    subject: str,
    source_kind: str,
    source_detail: str,
    address: str | None,
    retrieved_at: datetime,
    address_class: str | None,
) -> str:
    """Store a new Provenance Record for the Contact ``contact_id``; return its new ULID."""
    record_id = new_id()
    current().session.execute(
        insert_rows(ProvenanceRecord).values(
            id=record_id,
            contact_id=contact_id,
            subject=subject,
            source_kind=source_kind,
            source_detail=source_detail,
            address=address,
            retrieved_at=retrieved_at,
            address_class=address_class,
        )
    )
    return record_id


def for_contact(contact_id: str) -> tuple[StoredProvenance, ...]:
    """The Provenance Records of the Contact ``contact_id``, by ``retrieved_at``, then ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(ProvenanceRecord.contact_id == contact_id)
        .order_by(ProvenanceRecord.retrieved_at, ProvenanceRecord.id)
    )
    return tuple(StoredProvenance(*row) for row in current().session.execute(statement))


def for_lead(lead_id: str) -> tuple[StoredProvenance, ...]:
    """The Provenance Records of every Contact of the Lead ``lead_id``, by ``retrieved_at``,
    then ``id``."""
    statement = (
        select(*_COLUMNS)
        .join(ContactRecord, ContactRecord.id == ProvenanceRecord.contact_id)
        .where(ContactRecord.lead_id == lead_id)
        .order_by(ProvenanceRecord.retrieved_at, ProvenanceRecord.id)
    )
    return tuple(StoredProvenance(*row) for row in current().session.execute(statement))
