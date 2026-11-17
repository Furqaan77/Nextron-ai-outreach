"""The ``research`` service's tables (FR-14, FR-15, FR-51, AD-3, AD-7, AD-17; migration
``0015_2_3_contacts_provenance``). Owner ``research`` writes every column
(``nextron.db.repos.research``). No foreign key cascades.

**``contacts``**: one person on a Lead.

- ``id``; ``lead_id`` (``fk_contacts_lead_id_leads``, index ``ix_contacts_lead_id``);
- ``name`` and ``role`` (each nullable, at most 200 characters);
- ``email`` (nullable, at most 254 characters, normalised) and ``address_class`` (nullable,
  one of ``nextron.domain.address_class.AddressClass``: ``ck_contacts_address_class``); an
  address has a class and a class has an address (``ck_contacts_email_class``);
- ``linkedin_url`` (nullable, normalised, never fetched);
- ``is_primary`` (server default false); ``tz`` and ``market_id``
  (``fk_contacts_market_id_markets``), both nullable and NULL until Epic 3 sets them;
- ``created_at`` and ``updated_at``.

``ck_contacts_identity``: a Contact has a name or an email.

**``provenance_records``**: where a Contact (subject ``contact``) or its address (subject
``address``) came from (FR-51).

- ``id``; ``contact_id`` (``fk_provenance_records_contact_id_contacts``, index
  ``ix_provenance_records_contact_id``);
- ``subject`` (``ck_provenance_records_subject``); ``source_kind``, one of
  ``nextron.domain.contacts.ProvenanceSource`` (``ck_provenance_records_source_kind``);
  ``source_detail`` (the CSV's origin, a URL, ...; at most 2,048 characters);
- ``address`` (nullable): the address an ``address`` record is about, kept so a later change
  of the Contact's address can't mislabel it; set exactly on ``address`` records
  (``ck_provenance_records_address``);
- ``retrieved_at``; ``address_class`` (nullable; the class at the time, on ``address`` records
  only: ``ck_provenance_records_address_class``); ``notes`` (nullable);
- the eligibility-basis fields, all nullable and NULL until Story 3.x sets them:
  ``basis_rule``, ``basis_campaign_id`` (a ULID; no foreign key until ``campaigns`` exists),
  ``lia_ref``, ``lia_date`` (a calendar date) and ``basis_recorded_at``.

PII classes (``nextron.db.pii_registry``): a Contact's ``name`` and ``role`` and a record's
``source_detail`` and ``notes`` are ``redact``; ``email`` and a record's ``address`` are
``keep_hash``, so suppression still matches after an erasure; ``linkedin_url`` is ``null``.
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.address_class import AddressClass
from nextron.domain.contacts import ProvenanceSource, ProvenanceSubject

TEXT_LENGTH: Final = 200  # nextron.domain.contacts.MAX_TEXT_LENGTH
EMAIL_LENGTH: Final = 254  # nextron.domain.identity.MAX_EMAIL_LENGTH
URL_LENGTH: Final = 2048  # nextron.domain.identity.MAX_INPUT_LENGTH
CLASS_LENGTH: Final = 16
TZ_LENGTH: Final = 64
SUBJECT_LENGTH: Final = 16
SOURCE_KIND_LENGTH: Final = 32
DETAIL_LENGTH: Final = 2048
BASIS_RULE_LENGTH: Final = 64
LIA_REF_LENGTH: Final = 200


def _one_of(column: str, values: type[AddressClass | ProvenanceSource | ProvenanceSubject]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


CONTACT_CLASS_CHECK: Final = "address_class IS NULL OR " + _one_of("address_class", AddressClass)
CONTACT_EMAIL_CLASS_CHECK: Final = "(email IS NULL) = (address_class IS NULL)"
CONTACT_IDENTITY_CHECK: Final = "name IS NOT NULL OR email IS NOT NULL"
SUBJECT_CHECK: Final = _one_of("subject", ProvenanceSubject)
SOURCE_KIND_CHECK: Final = _one_of("source_kind", ProvenanceSource)
RECORD_ADDRESS_CHECK: Final = (
    f"(subject = '{ProvenanceSubject.ADDRESS}') = (address IS NOT NULL)"
)
RECORD_CLASS_CHECK: Final = "address_class IS NULL OR " + _one_of("address_class", AddressClass)


class ContactRecord(Base):
    """One Contact: a person on a Lead, with at most one email address and its class."""

    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_lead_id", "lead_id"),
        CheckConstraint(CONTACT_CLASS_CHECK, name="address_class"),
        CheckConstraint(CONTACT_EMAIL_CLASS_CHECK, name="email_class"),
        CheckConstraint(CONTACT_IDENTITY_CHECK, name="identity"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    name: Mapped[str | None] = mapped_column(String(TEXT_LENGTH))
    role: Mapped[str | None] = mapped_column(String(TEXT_LENGTH))
    email: Mapped[str | None] = mapped_column(String(EMAIL_LENGTH))
    address_class: Mapped[str | None] = mapped_column(String(CLASS_LENGTH))
    linkedin_url: Mapped[str | None] = mapped_column(String(URL_LENGTH))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    tz: Mapped[str | None] = mapped_column(String(TZ_LENGTH))
    market_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("markets.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ProvenanceRecord(Base):
    """One Provenance Record: where a Contact or its address came from."""

    __tablename__ = "provenance_records"
    __table_args__ = (
        Index("ix_provenance_records_contact_id", "contact_id"),
        CheckConstraint(SUBJECT_CHECK, name="subject"),
        CheckConstraint(SOURCE_KIND_CHECK, name="source_kind"),
        CheckConstraint(RECORD_ADDRESS_CHECK, name="address"),
        CheckConstraint(RECORD_CLASS_CHECK, name="address_class"),
    )

    id: Mapped[str] = ulid_pk()
    contact_id: Mapped[str] = mapped_column(Ulid, ForeignKey("contacts.id"))
    subject: Mapped[str] = mapped_column(String(SUBJECT_LENGTH))
    source_kind: Mapped[str] = mapped_column(String(SOURCE_KIND_LENGTH))
    source_detail: Mapped[str] = mapped_column(String(DETAIL_LENGTH))
    address: Mapped[str | None] = mapped_column(String(EMAIL_LENGTH))
    retrieved_at: Mapped[datetime] = mapped_column(UtcDateTime)
    address_class: Mapped[str | None] = mapped_column(String(CLASS_LENGTH))
    notes: Mapped[str | None] = mapped_column(Text)
    basis_rule: Mapped[str | None] = mapped_column(String(BASIS_RULE_LENGTH))
    basis_campaign_id: Mapped[str | None] = mapped_column(Ulid)
    lia_ref: Mapped[str | None] = mapped_column(String(LIA_REF_LENGTH))
    lia_date: Mapped[date | None] = mapped_column(Date)
    basis_recorded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
