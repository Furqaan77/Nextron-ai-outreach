"""The ``research`` service's tables (FR-2, FR-13, FR-14, FR-15, FR-51, AD-3, AD-7, AD-11, AD-17;
migrations ``0015_2_3_contacts_provenance``, ``0019_3_2_contact_markets``,
``0028_2_10_primary_contact``, ``0031_3_7_basis_rule_width`` and ``0032_3_13_erase_contacts``).
Owner ``research`` writes every column (``nextron.db.repos.research``). No foreign key
cascades.

**``contacts``**: one person on a Lead.

- ``id``; ``lead_id`` (``fk_contacts_lead_id_leads``, index ``ix_contacts_lead_id``);
- ``name`` and ``role`` (each nullable, at most 200 characters);
- ``email`` (nullable, at most 254 characters, normalised) and ``address_class`` (nullable,
  one of ``nextron.domain.address_class.AddressClass``: ``ck_contacts_address_class``); an
  address has a class and a class has an address (``ck_contacts_email_class``);
- ``linkedin_url`` (nullable, normalised, never fetched);
- ``is_primary`` (server default false) and ``primary_set_by`` (Story 2.10, migration
  ``0028_2_10_primary_contact``; nullable, ``system`` or ``operator``: who chose the Primary
  Contact, set exactly when ``is_primary`` is, which the code keeps, since the column was added
  in place with no CHECK); at most one Contact per Lead is Primary (the partial unique index
  ``uq_contacts_one_primary`` on ``lead_id`` where ``is_primary = 1``); ``tz`` and ``market_id``
  (``fk_contacts_market_id_markets``), both nullable: the Contact's IANA timezone and Market,
  set together or not at all (``ck_contacts_market_tz``);
- ``market_basis`` and ``tz_basis`` (Story 3.2, each nullable, one of
  ``nextron.domain.contact_market.MarketBasis`` and ``TzBasis``: ``ck_contacts_market_basis``,
  ``ck_contacts_tz_basis``), why the Market and timezone apply; both are set once the Contact is
  resolved, or neither (``ck_contacts_resolved``);
- ``office_country`` and ``office_region`` (Story 3.2, each nullable, two capitals): the place
  the Contact's office is in; a region needs a country (``ck_contacts_office_country``,
  ``ck_contacts_office_region``);
- ``created_at`` and ``updated_at``;
- ``erased_at`` (Story 3.13, nullable): when the Contact was erased. Once it is set the row is a
  tombstone: the trigger ``trg_contacts_tombstone`` (which no model declares) refuses setting it
  on a row that still holds a value, and any later change but a merge's (``lead_id``,
  ``is_primary``, ``updated_at``): ``primary_set_by`` stays NULL, so a tombstone is never made
  Primary again.

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
- the eligibility-basis fields, all nullable and set only on an ``eligibility_basis`` record
  (``Research.record_eligibility_basis``, Story 3.7): ``basis_rule`` (at most 128 characters,
  ``BASIS_RULE_LENGTH``, widened from 64 by ``0031_3_7_basis_rule_width`` so a UK basis fits),
  ``basis_campaign_id`` (a ULID; no foreign key), ``lia_ref``, ``lia_date`` (a calendar date)
  and ``basis_recorded_at``.

Two triggers (Story 3.13) refuse an ``UPDATE`` or ``INSERT`` of a record whose Contact is
erased.

PII classes (``nextron.db.pii_registry``): a Contact's ``name`` and ``role`` and a record's
``source_detail`` and ``notes`` are ``redact``; ``email`` and a record's ``address`` are
``keep_hash``, so suppression still matches after an erasure; ``linkedin_url``,
``office_country`` and ``office_region`` are ``null``; the bases, ``primary_set_by`` and
``erased_at`` are ``none``.
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
from nextron.domain.contact_market import MarketBasis, TzBasis
from nextron.domain.contacts import ProvenanceSource, ProvenanceSubject
from nextron.domain.eligibility import MAX_BASIS_RULE_LENGTH
from nextron.domain.email_finding import Pattern, PatternSource

TEXT_LENGTH: Final = 200  # nextron.domain.contacts.MAX_TEXT_LENGTH
EMAIL_LENGTH: Final = 254  # nextron.domain.identity.MAX_EMAIL_LENGTH
URL_LENGTH: Final = 2048  # nextron.domain.identity.MAX_INPUT_LENGTH
CLASS_LENGTH: Final = 16
TZ_LENGTH: Final = 64
SUBJECT_LENGTH: Final = 16
SOURCE_KIND_LENGTH: Final = 32
DETAIL_LENGTH: Final = 2048
BASIS_RULE_LENGTH: Final = MAX_BASIS_RULE_LENGTH  # 128: a Decision refuses a longer rule
LIA_REF_LENGTH: Final = 200
BASIS_LENGTH: Final = 32
COUNTRY_LENGTH: Final = 2
SET_BY_LENGTH: Final = 8  # nextron.domain.contacts.PrimarySetBy
ONE_PRIMARY_WHERE: Final = "is_primary = 1"
TRIED_LENGTH: Final = 32  # Story 2.13: "hunter,prospeo"
OUTCOME_LENGTH: Final = 16  # nextron.domain.email_finding.EmailSearchOutcome
CHECK_CODE_LENGTH: Final = 32  # Story 2.14: nextron.domain.email_checks codes
DOMAIN_LENGTH: Final = 253  # nextron.domain.identity.MAX_DOMAIN_LENGTH
PATTERN_LENGTH: Final = 16  # nextron.domain.email_finding.Pattern
PATTERN_SOURCE_LENGTH: Final = 32  # nextron.domain.email_finding.PatternSource

type _Values = type[
    AddressClass
    | ProvenanceSource
    | ProvenanceSubject
    | MarketBasis
    | TzBasis
    | Pattern
    | PatternSource
]


def _one_of(column: str, values: _Values) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


CONTACT_CLASS_CHECK: Final = "address_class IS NULL OR " + _one_of("address_class", AddressClass)
CONTACT_EMAIL_CLASS_CHECK: Final = "(email IS NULL) = (address_class IS NULL)"
CONTACT_IDENTITY_CHECK: Final = "name IS NOT NULL OR email IS NOT NULL"
CONTACT_MARKET_BASIS_CHECK: Final = "market_basis IS NULL OR " + _one_of(
    "market_basis", MarketBasis
)
CONTACT_TZ_BASIS_CHECK: Final = "tz_basis IS NULL OR " + _one_of("tz_basis", TzBasis)
CONTACT_RESOLVED_CHECK: Final = "(market_basis IS NULL) = (tz_basis IS NULL)"
CONTACT_MARKET_TZ_CHECK: Final = "(market_id IS NULL) = (tz IS NULL)"
CONTACT_OFFICE_COUNTRY_CHECK: Final = "office_country IS NULL OR office_country GLOB '[A-Z][A-Z]'"
CONTACT_OFFICE_REGION_CHECK: Final = (
    "office_region IS NULL OR (office_country IS NOT NULL AND office_region GLOB '[A-Z][A-Z]')"
)
SUBJECT_CHECK: Final = _one_of("subject", ProvenanceSubject)
SOURCE_KIND_CHECK: Final = _one_of("source_kind", ProvenanceSource)
RECORD_ADDRESS_CHECK: Final = (
    f"(subject = '{ProvenanceSubject.ADDRESS}') = (address IS NOT NULL)"
)
RECORD_CLASS_CHECK: Final = "address_class IS NULL OR " + _one_of("address_class", AddressClass)
EMAIL_DOMAIN_PATTERN_CHECK: Final = "pattern IS NULL OR " + _one_of("pattern", Pattern)
EMAIL_DOMAIN_SOURCE_CHECK: Final = "pattern_source IS NULL OR " + _one_of(
    "pattern_source", PatternSource
)
EMAIL_DOMAIN_LEARNED_CHECK: Final = (
    "(pattern IS NULL) = (pattern_source IS NULL) "
    "AND (pattern IS NULL) = (pattern_learned_at IS NULL)"
)
EMAIL_DOMAIN_EVIDENCE_CHECK: Final = (
    f"(COALESCE(pattern_source, '') = '{PatternSource.PUBLISHED}') "
    "= (pattern_evidence_ids IS NOT NULL)"
)


class ContactRecord(Base):
    """One Contact: a person on a Lead, with at most one email address and its class."""

    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_lead_id", "lead_id"),
        Index(
            "uq_contacts_one_primary",
            "lead_id",
            unique=True,
            sqlite_where=text(ONE_PRIMARY_WHERE),
        ),
        CheckConstraint(CONTACT_CLASS_CHECK, name="address_class"),
        CheckConstraint(CONTACT_EMAIL_CLASS_CHECK, name="email_class"),
        CheckConstraint(CONTACT_IDENTITY_CHECK, name="identity"),
        CheckConstraint(CONTACT_MARKET_BASIS_CHECK, name="market_basis"),
        CheckConstraint(CONTACT_TZ_BASIS_CHECK, name="tz_basis"),
        CheckConstraint(CONTACT_RESOLVED_CHECK, name="resolved"),
        CheckConstraint(CONTACT_MARKET_TZ_CHECK, name="market_tz"),
        CheckConstraint(CONTACT_OFFICE_COUNTRY_CHECK, name="office_country"),
        CheckConstraint(CONTACT_OFFICE_REGION_CHECK, name="office_region"),
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
    market_basis: Mapped[str | None] = mapped_column(String(BASIS_LENGTH))
    tz_basis: Mapped[str | None] = mapped_column(String(BASIS_LENGTH))
    office_country: Mapped[str | None] = mapped_column(String(COUNTRY_LENGTH))
    office_region: Mapped[str | None] = mapped_column(String(COUNTRY_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    primary_set_by: Mapped[str | None] = mapped_column(String(SET_BY_LENGTH))
    erased_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    email_search_tried: Mapped[str | None] = mapped_column(String(TRIED_LENGTH))  # Story 2.13
    email_search_outcome: Mapped[str | None] = mapped_column(String(OUTCOME_LENGTH))
    email_search_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    email_check_result: Mapped[str | None] = mapped_column(String(CHECK_CODE_LENGTH))  # 2.14
    email_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    email_invalid_reason: Mapped[str | None] = mapped_column(String(CHECK_CODE_LENGTH))
    email_verifier_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


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


class EmailDomainRecord(Base):
    """What the engine learned about one registrable mail domain (Story 2.13): its address
    pattern and whether Hunter's Domain Search already ran for it."""

    __tablename__ = "email_domains"
    __table_args__ = (
        CheckConstraint(EMAIL_DOMAIN_PATTERN_CHECK, name="pattern"),
        CheckConstraint(EMAIL_DOMAIN_SOURCE_CHECK, name="pattern_source"),
        CheckConstraint(EMAIL_DOMAIN_LEARNED_CHECK, name="pattern_learned"),
        CheckConstraint(EMAIL_DOMAIN_EVIDENCE_CHECK, name="pattern_evidence"),
    )

    id: Mapped[str] = ulid_pk()
    domain: Mapped[str] = mapped_column(String(DOMAIN_LENGTH), unique=True)
    pattern: Mapped[str | None] = mapped_column(String(PATTERN_LENGTH))
    pattern_source: Mapped[str | None] = mapped_column(String(PATTERN_SOURCE_LENGTH))
    pattern_evidence_ids: Mapped[str | None] = mapped_column(Text)
    pattern_learned_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    domain_search_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
