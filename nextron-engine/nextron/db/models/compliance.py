"""The ``compliance`` service's tables (FR-50, FR-52, FR-63, FR-70, AD-3, AD-4, AD-15, AD-16,
AD-17; migrations ``0022_3_3_suppression_entries``, ``0024_3_8_unsub_tokens``,
``0028_3_13_erase_contacts`` and ``0029_3_14_erasure_receipts``), owned by ``compliance``
(``nextron.db.repos.compliance``).

**``suppression_entries``**: the Suppression List, one row per suppressed key.

- ``id``; ``kind`` (``ck_suppression_entries_kind``: one of
  ``nextron.domain.contactability.KeyKind``); ``value_hash``, the key's
  ``nextron.domain.contactability.key_hash`` (``ck_suppression_entries_value_hash``: 64
  lowercase hex characters); ``domain_plain``, the normalised domain, set exactly on the domain
  kind (``ck_suppression_entries_domain_plain``);
- ``status`` (``ck_suppression_entries_status``: one of
  ``nextron.domain.suppression_entry.EntryStatus``) and ``source``
  (``ck_suppression_entries_source``: one of ``SuppressionSource``);
- ``cause_ref`` (nullable): the ULID of what caused the entry (a reply, a token, a DSR), with no
  foreign key; ``created_at``;
- ``voided_at`` and ``void_reason``, both NULL until an entry is voided and both set when it is
  (``ck_suppression_entries_voided``).

The partial unique index ``uq_suppression_entries_active_key`` allows one ``active`` row per
(``kind``, ``value_hash``).

Four triggers (migration ``0022_3_3_suppression_entries``, the ``events`` precedent) make the
list permanent: ``trg_suppression_entries_no_delete`` aborts any ``DELETE``;
``trg_suppression_entries_no_id_reuse`` aborts an ``INSERT`` whose ``id`` exists, or an
``active`` one whose ``active`` key exists (so ``INSERT OR REPLACE`` can't replace an entry);
``trg_suppression_entries_insert_active`` aborts an ``INSERT`` that isn't ``active``; and
``trg_suppression_entries_immutable`` aborts an ``UPDATE`` that changes ``id``, ``kind``,
``value_hash``, ``domain_plain``, ``source``, ``cause_ref`` or ``created_at``, leaves
``voided``, voids an entry whose ``source`` isn't ``reply_rule`` (AD-4: ``VOIDABLE_SOURCES``),
or changes a set ``voided_at``. ``void_reason`` may change while the entry stays ``voided``
(Story 3.13 redacts it). A later migration that rebuilds the table in batch mode
must create them again.

PII classes (``nextron.db.pii_registry``): ``value_hash`` is ``keep_hash`` (it is the hash,
kept after an erasure so the person stays suppressed), ``void_reason`` is ``redact`` (the
Operator's free text) and every other column is ``none``: no column holds a plaintext address,
name or profile URL.

**``unsub_tokens``** (Story 3.8): one row per issued unsubscribe token, so 3.11 can tell a
token it issued and the Credentials page can tell how recently each kid was used. It never
holds the token, the address or the address hash.

- ``id``; ``token_hash``, ``nextron.domain.unsub_token.token_hash`` of the token
  (``ck_unsub_tokens_token_hash``: 64 lowercase hex characters; unique,
  ``uq_unsub_tokens_token_hash``); ``om_id``, the ULID of the OutboundMessage it was issued for,
  with no foreign key (unique, ``uq_unsub_tokens_om_id``: one token per message); ``kid``, the
  key id it was encoded under (``ck_unsub_tokens_kid``: 1 to 255); ``issued_at``, in whole
  seconds (``ck_unsub_tokens_issued_at``). Every column is NOT NULL.
- ``ix_unsub_tokens_kid_issued_at`` serves "the newest token of each kid".

Three triggers (migration ``0024_3_8_unsub_tokens``) make the records permanent, so the 2-year
key check can't be defeated by deleting rows: ``trg_unsub_tokens_no_delete`` aborts any
``DELETE``, ``trg_unsub_tokens_immutable`` any ``UPDATE``, and ``trg_unsub_tokens_no_reuse`` an
``INSERT`` whose ``id``, ``token_hash`` or ``om_id`` exists (so ``INSERT OR REPLACE`` can't
replace one), each with "unsubscribe token records are permanent". Every column is PII class
``none``.

**``data_subject_requests``** (Story 3.13): one row per data subject request, the proof that
an erasure (or, from Story 3.15, an export) ran.

- ``id``; ``contact_id`` (``fk_data_subject_requests_contact_id_contacts``, no cascade,
  ``ix_data_subject_requests_contact_id``); ``kind`` (``ck_data_subject_requests_kind``: one of
  ``nextron.domain.data_subject_request.RequestKind``); ``status``
  (``ck_data_subject_requests_status``: one of ``RequestStatus``); ``request_ref``, the
  Operator's ticket or letter reference (``ck_data_subject_requests_request_ref``: 1 to 200
  characters); ``opened_at``; ``completed_at``, set exactly on a ``completed`` request
  (``ck_data_subject_requests_completed``); ``receipt_id`` (a ULID, nullable; a trigger
  rather than a foreign key checks it, Story 3.14).
- Three triggers (migration ``0028_3_13_erase_contacts``): no ``DELETE``; an ``INSERT`` is
  ``open`` with a new ``id``; an ``UPDATE`` changes only ``status`` (``open`` to
  ``completed``) with ``completed_at``, and sets ``receipt_id`` at most once. A fourth
  (``0029_3_14_erasure_receipts``) refuses a ``receipt_id`` that names no receipt of the same
  Contact.

PII classes: ``request_ref`` is ``redact`` (free text, though ``check_request_ref`` refuses an
address or the person's name); every other column is ``none``.

**``erasure_receipts``** (Story 3.14): one row per erasure, or per repeat erase request on a
Contact erased before: the receipt that tells the requester what was erased and what remains.

- ``id``; ``contact_id`` (``fk_erasure_receipts_contact_id_contacts``, no cascade,
  ``ix_erasure_receipts_contact_id``); ``request_ref`` (nullable, 1 to 200 characters), set
  exactly on a ``dsr`` receipt (``ck_erasure_receipts_dsr_ref``); ``cause`` (one of
  ``nextron.domain.erasure.ErasureCause``); ``outcome`` (one of
  ``nextron.domain.erasure_receipt.ReceiptOutcome``); ``created_at``.
- Two triggers: no ``DELETE``, no ``UPDATE``.

**``erasure_receipt_stores``** (Story 3.14): one row per store of a receipt.

- ``id``; ``receipt_id`` (``fk_erasure_receipt_stores_receipt_id_erasure_receipts``, no
  cascade, ``ix_erasure_receipt_stores_receipt_id``); ``store`` (one of
  ``nextron.domain.erasure_receipt.ErasureStore``); ``action`` (its fixed text); ``status`` (one
  of ``StoreStatus``); ``detail`` (1 to 1000 characters, no ``@``); ``at``. One row per store of
  a receipt (``uq_erasure_receipt_stores_receipt_id_store``).
- Three triggers: no ``DELETE``; an ``INSERT`` with a new ``id`` and a new (``receipt_id``,
  ``store``); an ``UPDATE`` only from ``pending`` to another status, with ``id``,
  ``receipt_id``, ``store`` and ``action`` unchanged (each store settles once).

PII classes: ``erasure_receipts.request_ref`` is ``redact``; every other column of both tables
is ``none`` (each detail is built by ``nextron.domain.erasure_receipt`` from counts, dates, ids
and codes, and holds no ``@``).
"""

from datetime import datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.contactability import KeyKind
from nextron.domain.data_subject_request import RequestKind, RequestStatus
from nextron.domain.erasure import ErasureCause
from nextron.domain.erasure_receipt import ErasureStore, ReceiptOutcome, StoreStatus
from nextron.domain.suppression_entry import EntryStatus, SuppressionSource

KIND_LENGTH: Final = 16
VALUE_HASH_LENGTH: Final = 64  # nextron.domain.contactability.HASH_LENGTH
DOMAIN_LENGTH: Final = 253  # nextron.domain.identity.MAX_DOMAIN_LENGTH
STATUS_LENGTH: Final = 16
SOURCE_LENGTH: Final = 16


def _one_of(
    column: str,
    values: type[
        KeyKind
        | EntryStatus
        | SuppressionSource
        | RequestKind
        | RequestStatus
        | ErasureCause
        | ReceiptOutcome
        | ErasureStore
        | StoreStatus
    ],
) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


KIND_CHECK: Final = _one_of("kind", KeyKind)
STATUS_CHECK: Final = _one_of("status", EntryStatus)
SOURCE_CHECK: Final = _one_of("source", SuppressionSource)
VALUE_HASH_CHECK: Final = "length(value_hash) = 64 AND value_hash NOT GLOB '*[^0-9a-f]*'"
DOMAIN_PLAIN_CHECK: Final = "(kind = 'domain') = (domain_plain IS NOT NULL)"
VOIDED_CHECK: Final = (
    "(status = 'voided') = (voided_at IS NOT NULL) "
    "AND (status = 'voided') = (void_reason IS NOT NULL)"
)
ACTIVE_WHERE: Final = "status = 'active'"
TOKEN_HASH_LENGTH: Final = 64  # nextron.domain.unsub_token.token_hash
TOKEN_HASH_CHECK: Final = "length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'"
KID_CHECK: Final = "kid BETWEEN 1 AND 255"  # nextron.domain.unsub_token.MIN_KID, MAX_KID
WHOLE_SECONDS_CHECK: Final = "issued_at LIKE '%.000000Z'"
REQUEST_KIND_LENGTH: Final = 8
REQUEST_STATUS_LENGTH: Final = 16
REQUEST_REF_LENGTH: Final = 200  # nextron.domain.data_subject_request.MAX_REF_LENGTH
REQUEST_KIND_CHECK: Final = _one_of("kind", RequestKind)
REQUEST_STATUS_CHECK: Final = _one_of("status", RequestStatus)
REQUEST_REF_CHECK: Final = "length(request_ref) BETWEEN 1 AND 200"
REQUEST_COMPLETED_CHECK: Final = "(status = 'completed') = (completed_at IS NOT NULL)"
RECEIPT_CAUSE_LENGTH: Final = 16
RECEIPT_OUTCOME_LENGTH: Final = 16
RECEIPT_REF_CHECK: Final = "length(request_ref) BETWEEN 1 AND 200"
RECEIPT_DSR_REF_CHECK: Final = "(cause = 'dsr') = (request_ref IS NOT NULL)"
RECEIPT_CAUSE_CHECK: Final = _one_of("cause", ErasureCause)
RECEIPT_OUTCOME_CHECK: Final = _one_of("outcome", ReceiptOutcome)
STORE_LENGTH: Final = 16
ACTION_LENGTH: Final = 300  # nextron.domain.erasure_receipt.MAX_ACTION_LENGTH
STORE_STATUS_LENGTH: Final = 16
STORE_CHECK: Final = _one_of("store", ErasureStore)
STORE_STATUS_CHECK: Final = _one_of("status", StoreStatus)
DETAIL_CHECK: Final = "length(detail) BETWEEN 1 AND 1000"  # MAX_DETAIL_LENGTH
DETAIL_ADDRESS_CHECK: Final = "instr(detail, '@') = 0"


class SuppressionEntryRecord(Base):
    """One Suppression List entry: a key's hash, its status and why it exists."""

    __tablename__ = "suppression_entries"
    __table_args__ = (
        Index(
            "uq_suppression_entries_active_key",
            "kind",
            "value_hash",
            unique=True,
            sqlite_where=text(ACTIVE_WHERE),
        ),
        CheckConstraint(KIND_CHECK, name="kind"),
        CheckConstraint(VALUE_HASH_CHECK, name="value_hash"),
        CheckConstraint(DOMAIN_PLAIN_CHECK, name="domain_plain"),
        CheckConstraint(STATUS_CHECK, name="status"),
        CheckConstraint(SOURCE_CHECK, name="source"),
        CheckConstraint(VOIDED_CHECK, name="voided"),
    )

    id: Mapped[str] = ulid_pk()
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    value_hash: Mapped[str] = mapped_column(String(VALUE_HASH_LENGTH))
    domain_plain: Mapped[str | None] = mapped_column(String(DOMAIN_LENGTH))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    source: Mapped[str] = mapped_column(String(SOURCE_LENGTH))
    cause_ref: Mapped[str | None] = mapped_column(Ulid)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    voided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    void_reason: Mapped[str | None] = mapped_column(Text)


class UnsubTokenRecord(Base):
    """One issued unsubscribe token: its hash, its message, its kid and when it was issued."""

    __tablename__ = "unsub_tokens"
    __table_args__ = (
        Index("uq_unsub_tokens_token_hash", "token_hash", unique=True),
        Index("uq_unsub_tokens_om_id", "om_id", unique=True),
        Index("ix_unsub_tokens_kid_issued_at", "kid", "issued_at"),
        CheckConstraint(TOKEN_HASH_CHECK, name="token_hash"),
        CheckConstraint(KID_CHECK, name="kid"),
        CheckConstraint(WHOLE_SECONDS_CHECK, name="issued_at"),
    )

    id: Mapped[str] = ulid_pk()
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    om_id: Mapped[str] = mapped_column(Ulid)
    kid: Mapped[int] = mapped_column(Integer)
    issued_at: Mapped[datetime] = mapped_column(UtcDateTime)


class DataSubjectRequestRecord(Base):
    """One data subject request: its Contact, kind, status and reference."""

    __tablename__ = "data_subject_requests"
    __table_args__ = (
        Index("ix_data_subject_requests_contact_id", "contact_id"),
        CheckConstraint(REQUEST_KIND_CHECK, name="kind"),
        CheckConstraint(REQUEST_STATUS_CHECK, name="status"),
        CheckConstraint(REQUEST_REF_CHECK, name="request_ref"),
        CheckConstraint(REQUEST_COMPLETED_CHECK, name="completed"),
    )

    id: Mapped[str] = ulid_pk()
    contact_id: Mapped[str] = mapped_column(Ulid, ForeignKey("contacts.id"))
    kind: Mapped[str] = mapped_column(String(REQUEST_KIND_LENGTH))
    status: Mapped[str] = mapped_column(String(REQUEST_STATUS_LENGTH))
    request_ref: Mapped[str] = mapped_column(String(REQUEST_REF_LENGTH))
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    receipt_id: Mapped[str | None] = mapped_column(Ulid)


class ErasureReceiptRecord(Base):
    """One erasure receipt: its Contact, its request reference, its cause and its outcome."""

    __tablename__ = "erasure_receipts"
    __table_args__ = (
        Index("ix_erasure_receipts_contact_id", "contact_id"),
        CheckConstraint(RECEIPT_REF_CHECK, name="request_ref"),
        CheckConstraint(RECEIPT_DSR_REF_CHECK, name="dsr_ref"),
        CheckConstraint(RECEIPT_CAUSE_CHECK, name="cause"),
        CheckConstraint(RECEIPT_OUTCOME_CHECK, name="outcome"),
    )

    id: Mapped[str] = ulid_pk()
    contact_id: Mapped[str] = mapped_column(Ulid, ForeignKey("contacts.id"))
    request_ref: Mapped[str | None] = mapped_column(String(REQUEST_REF_LENGTH))
    cause: Mapped[str] = mapped_column(String(RECEIPT_CAUSE_LENGTH))
    outcome: Mapped[str] = mapped_column(String(RECEIPT_OUTCOME_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ErasureReceiptStoreRecord(Base):
    """One store of an erasure receipt: its action, status, detail and time."""

    __tablename__ = "erasure_receipt_stores"
    __table_args__ = (
        Index("ix_erasure_receipt_stores_receipt_id", "receipt_id"),
        UniqueConstraint("receipt_id", "store"),
        CheckConstraint(STORE_CHECK, name="store"),
        CheckConstraint(STORE_STATUS_CHECK, name="status"),
        CheckConstraint(DETAIL_CHECK, name="detail"),
        CheckConstraint(DETAIL_ADDRESS_CHECK, name="detail_address"),
    )

    id: Mapped[str] = ulid_pk()
    receipt_id: Mapped[str] = mapped_column(Ulid, ForeignKey("erasure_receipts.id"))
    store: Mapped[str] = mapped_column(String(STORE_LENGTH))
    action: Mapped[str] = mapped_column(String(ACTION_LENGTH))
    status: Mapped[str] = mapped_column(String(STORE_STATUS_LENGTH))
    detail: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UtcDateTime)
