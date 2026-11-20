"""The ``sending_domains`` and ``sending_inboxes`` tables (FR-32, AD-3, AD-4, AD-21; migration
``0018_4_7_sending_inboxes``) and the ``domain_checks`` table (FR-64, Story 6.5; migration
``0033_6_5_domain_checks``), owned by ``sending`` (``nextron.db.repos.sending``).

- ``sending_domains``: one row per Sending Domain: ``id``, the registrable ``domain`` (unique:
  ``uq_sending_domains_domain``; ``ck_sending_domains_not_brand``: never ``nextronai.com``) and
  ``created_at``.
- ``sending_inboxes``: one row per Sending Inbox: ``id``, the normalised ``address`` (unique:
  ``uq_sending_inboxes_address``), the Operator's ``label``, its ``sending_domain_id``
  (``fk_sending_inboxes_sending_domain_id_sending_domains``, no cascade), the ``from_name``
  copied from the Sender's name setting, the ``status`` (server default ``connecting``; one of
  ``nextron.domain.sending_inbox.InboxStatus``: ``ck_sending_inboxes_status``) and
  ``created_at``. ``ck_sending_inboxes_not_brand``: the address is on neither nextronai.com nor
  a subdomain of it.

  Story 6.4 (migration ``0039_6_4_inbox_warmup``) adds the warm-up record and the ramp
  (``nextron.domain.sending_policy``): ``warmup_started_on`` (a date), ``warmup_method``
  (``manual`` or a warm-up tool's name, 1 to 40 characters) and ``warm_level`` (0 to 100), all
  NULL until warm-up is recorded (``ck_sending_inboxes_warmup_record``: all three NULL or all
  three set); ``warmup_tool_daily`` (0 to 100, server default 0), ``ramp_step`` (1 to 10, server
  default 5) and ``ramp_target`` (1 to 50, server default 30), each NOT NULL.
  ``ck_sending_inboxes_warmup_ranges`` holds every range.

  Story 6.11 (migration ``0063_6_11_inbox_pacing``) adds the cold-send pacing
  (``nextron.domain.sending_policy``): ``next_send_at`` (the earliest time the inbox's next
  cold send may go) and ``last_sent_at`` (its last cold send), both nullable UTC times, NULL
  until set. No planned send slot is stored (AD-11).
- ``domain_checks``: one row per authentication check that ran (append-only: the repository
  only inserts and reads): ``id``, its ``sending_domain_id``
  (``fk_domain_checks_sending_domain_id_sending_domains``, no cascade), ``checked_at``, the
  items' outcomes ``spf_ok``, ``dkim_ok``, ``dmarc_ok`` and ``aligned_ok``, ``passed``
  (``ck_domain_checks_passed``: ``passed = (spf_ok AND dkim_ok AND dmarc_ok AND aligned_ok)``)
  and ``details`` (``nextron.domain.domain_auth.encode_details``: each item's reason). Index
  ``ix_domain_checks_sending_domain_id_checked_at`` finds a domain's newest check.

The nextronai.com checks repeat ``nextron.domain.sending_inbox.check_address`` as defence in
depth (FR-32). Every column but the three warm-up record columns and the two pacing columns is NOT
NULL; every column is PII class ``none``: these are Nextron's own sender
identity, never a data subject's. Story 6.4 added the warm-up columns (migration
``0039_6_4_inbox_warmup``), Story 6.5 the ``domain_checks`` table, and Story 6.2 will add the
Gmail connection. (Those three tables only: ``outbound_messages`` below holds personal data.)

- ``outbound_messages`` (Story 5.2, migration ``0066_5_2_outbound_messages``): one row per
  approved message (``nextron.domain.outbound_message``): its own copy of the recipient
  (``to_address``), the Enrollment's bound inbox (``inbox_id``), ``subject``, ``body``,
  ``approved_hash`` and ``unsub_token``; the ``draft_id``, ``approval_id`` (no foreign key until
  Story 5.4), ``contact_id``, ``lead_id`` and nullable ``enrollment_id``; the AD-4 snapshot
  (``kind``, ``tier``, ``lead_score``, ``variant_id``, ``segment_id``, ``market_id``,
  ``step_no``; no foreign keys on the snapshot ids); ``status``, ``created_at``,
  ``cancelled_at`` (``ck_outbound_messages_cancelled``: set exactly when ``cancelled``) and
  ``sent_at``. The partial unique index ``uq_outbound_messages_live_draft`` allows one
  non-``cancelled`` OM per Draft. Triggers (in the migration) refuse a ``DELETE``, a duplicate
  live insert and any ``UPDATE`` of the copy or snapshot but the erasure tombstone
  (``[erased]``) of ``to_address``, ``subject``, ``body`` and ``unsub_token``, the four
  ``redact`` columns; every other column is PII class ``none``.
"""

from collections.abc import Iterable
from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.outbound_message import OmKind, OmStatus
from nextron.domain.scoring import Tier
from nextron.domain.sending_inbox import (
    BRAND_DOMAIN,
    MAX_LABEL_LENGTH,
    MAX_METHOD_LENGTH,
    InboxStatus,
)
from nextron.domain.sending_policy import (
    DEFAULT_RAMP_STEP,
    DEFAULT_RAMP_TARGET,
    DEFAULT_TOOL_DAILY,
    RAMP_STEP_RANGE,
    RAMP_TARGET_RANGE,
    TOOL_DAILY_RANGE,
    WARM_LEVEL_RANGE,
)

DOMAIN_LENGTH: Final = 253  # nextron.domain.identity.MAX_DOMAIN_LENGTH
ADDRESS_LENGTH: Final = 254  # nextron.domain.identity.MAX_EMAIL_LENGTH
FROM_NAME_LENGTH: Final = 100  # the sender_name setting's max_length
STATUS_LENGTH: Final = 16

NOT_BRAND_DOMAIN_CHECK: Final = f"domain <> '{BRAND_DOMAIN}'"
NOT_BRAND_ADDRESS_CHECK: Final = (
    f"address NOT LIKE '%@{BRAND_DOMAIN}' AND address NOT LIKE '%.{BRAND_DOMAIN}'"
)
STATUS_CHECK: Final = "status IN ({})".format(",".join(f"'{status}'" for status in InboxStatus))
DETAILS_LENGTH: Final = 256
WARMUP_RECORD_CHECK: Final = (
    "(warmup_started_on IS NULL AND warmup_method IS NULL AND warm_level IS NULL) OR "
    "(warmup_started_on IS NOT NULL AND warmup_method IS NOT NULL AND warm_level IS NOT NULL)"
)


def _between(column: str, bounds: tuple[int, int]) -> str:
    return f"{column} BETWEEN {bounds[0]} AND {bounds[1]}"


WARMUP_RANGES_CHECK: Final = " AND ".join(
    (
        f"(warmup_method IS NULL OR length(warmup_method) BETWEEN 1 AND {MAX_METHOD_LENGTH})",
        f"(warm_level IS NULL OR {_between('warm_level', WARM_LEVEL_RANGE)})",
        _between("warmup_tool_daily", TOOL_DAILY_RANGE),
        _between("ramp_step", RAMP_STEP_RANGE),
        _between("ramp_target", RAMP_TARGET_RANGE),
    )
)
PASSED_CHECK: Final = "passed = (spf_ok AND dkim_ok AND dmarc_ok AND aligned_ok)"


class SendingDomainRecord(Base):
    """One Sending Domain: a registrable domain that Sending Inboxes send from."""

    __tablename__ = "sending_domains"
    __table_args__ = (
        Index("uq_sending_domains_domain", "domain", unique=True),
        CheckConstraint(NOT_BRAND_DOMAIN_CHECK, name="not_brand"),
    )

    id: Mapped[str] = ulid_pk()
    domain: Mapped[str] = mapped_column(String(DOMAIN_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class SendingInboxRecord(Base):
    """One Sending Inbox: its address, label, Sending Domain, From name and status."""

    __tablename__ = "sending_inboxes"
    __table_args__ = (
        Index("uq_sending_inboxes_address", "address", unique=True),
        CheckConstraint(STATUS_CHECK, name="status"),
        CheckConstraint(NOT_BRAND_ADDRESS_CHECK, name="not_brand"),
        CheckConstraint(WARMUP_RECORD_CHECK, name="warmup_record"),
        CheckConstraint(WARMUP_RANGES_CHECK, name="warmup_ranges"),
    )

    id: Mapped[str] = ulid_pk()
    address: Mapped[str] = mapped_column(String(ADDRESS_LENGTH))
    label: Mapped[str] = mapped_column(String(MAX_LABEL_LENGTH))
    sending_domain_id: Mapped[str] = mapped_column(Ulid, ForeignKey("sending_domains.id"))
    from_name: Mapped[str] = mapped_column(String(FROM_NAME_LENGTH))
    status: Mapped[str] = mapped_column(
        String(STATUS_LENGTH), server_default=text(f"'{InboxStatus.CONNECTING}'")
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    warmup_started_on: Mapped[date | None] = mapped_column(Date)
    warmup_method: Mapped[str | None] = mapped_column(String(MAX_METHOD_LENGTH))
    warm_level: Mapped[int | None] = mapped_column(Integer)
    warmup_tool_daily: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_TOOL_DAILY))
    )
    ramp_step: Mapped[int] = mapped_column(Integer, server_default=text(str(DEFAULT_RAMP_STEP)))
    ramp_target: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_RAMP_TARGET))
    )
    next_send_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class DomainCheckRecord(Base):
    """One authentication check of a Sending Domain that ran: each item's outcome, whether it
    passed, and each item's reason."""

    __tablename__ = "domain_checks"
    __table_args__ = (
        CheckConstraint(PASSED_CHECK, name="passed"),
        Index("ix_domain_checks_sending_domain_id_checked_at", "sending_domain_id", "checked_at"),
    )

    id: Mapped[str] = ulid_pk()
    sending_domain_id: Mapped[str] = mapped_column(Ulid, ForeignKey("sending_domains.id"))
    checked_at: Mapped[datetime] = mapped_column(UtcDateTime)
    spf_ok: Mapped[bool] = mapped_column(Boolean)
    dkim_ok: Mapped[bool] = mapped_column(Boolean)
    dmarc_ok: Mapped[bool] = mapped_column(Boolean)
    aligned_ok: Mapped[bool] = mapped_column(Boolean)
    passed: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[str] = mapped_column(String(DETAILS_LENGTH))


# Story 5.2 ----------------------------------------------------------------------------------------

SHA256_LENGTH: Final = 64
TIER_LENGTH: Final = 1


def _one_of(column: str, values: Iterable[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


OM_KIND_CHECK: Final = _one_of("kind", OmKind)
OM_STATUS_CHECK: Final = _one_of("status", OmStatus)
OM_CANCELLED_CHECK: Final = "(cancelled_at IS NOT NULL) = (status = 'cancelled')"
OM_APPROVED_HASH_CHECK: Final = (
    "length(approved_hash) = 64 AND approved_hash NOT GLOB '*[^0-9a-f]*'"
)
OM_TIER_CHECK: Final = f"tier IS NULL OR {_one_of('tier', Tier)}"
OM_STEP_NO_CHECK: Final = "step_no IS NULL OR step_no >= 1"
OM_STEP_SLOT_CHECK: Final = (
    "kind NOT IN ('step','recontact') OR (enrollment_id IS NOT NULL AND step_no IS NOT NULL)"
)
OM_LIVE_WHERE: Final = f"status <> '{OmStatus.CANCELLED}'"


class OutboundMessageRecord(Base):
    """One approved message: its own copy of the approved text and the AD-4 snapshot."""

    __tablename__ = "outbound_messages"
    __table_args__ = (
        Index(
            "uq_outbound_messages_live_draft",
            "draft_id",
            unique=True,
            sqlite_where=text(OM_LIVE_WHERE),
        ),
        Index("ix_outbound_messages_contact_id", "contact_id"),
        CheckConstraint(OM_KIND_CHECK, name="kind"),
        CheckConstraint(OM_STATUS_CHECK, name="status"),
        CheckConstraint(OM_CANCELLED_CHECK, name="cancelled"),
        CheckConstraint(OM_APPROVED_HASH_CHECK, name="approved_hash"),
        CheckConstraint(OM_TIER_CHECK, name="tier"),
        CheckConstraint(OM_STEP_NO_CHECK, name="step_no"),
        CheckConstraint(OM_STEP_SLOT_CHECK, name="step_slot"),
    )

    id: Mapped[str] = ulid_pk()
    draft_id: Mapped[str] = mapped_column(Ulid, ForeignKey("drafts.id"))
    approval_id: Mapped[str] = mapped_column(Ulid)
    kind: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    to_address: Mapped[str] = mapped_column(String(ADDRESS_LENGTH))
    inbox_id: Mapped[str] = mapped_column(Ulid, ForeignKey("sending_inboxes.id"))
    contact_id: Mapped[str] = mapped_column(Ulid, ForeignKey("contacts.id"))
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    enrollment_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("enrollments.id"))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    approved_hash: Mapped[str] = mapped_column(String(SHA256_LENGTH))
    unsub_token: Mapped[str] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(String(TIER_LENGTH))
    lead_score: Mapped[int | None] = mapped_column(Integer)
    variant_id: Mapped[str | None] = mapped_column(Ulid)
    segment_id: Mapped[str] = mapped_column(Ulid)
    market_id: Mapped[str | None] = mapped_column(Ulid)
    step_no: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
