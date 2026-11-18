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
- ``domain_checks``: one row per authentication check that ran (append-only: the repository
  only inserts and reads): ``id``, its ``sending_domain_id``
  (``fk_domain_checks_sending_domain_id_sending_domains``, no cascade), ``checked_at``, the
  items' outcomes ``spf_ok``, ``dkim_ok``, ``dmarc_ok`` and ``aligned_ok``, ``passed``
  (``ck_domain_checks_passed``: ``passed = (spf_ok AND dkim_ok AND dmarc_ok AND aligned_ok)``)
  and ``details`` (``nextron.domain.domain_auth.encode_details``: each item's reason). Index
  ``ix_domain_checks_sending_domain_id_checked_at`` finds a domain's newest check.

The nextronai.com checks repeat ``nextron.domain.sending_inbox.check_address`` as defence in
depth (FR-32). Every column is NOT NULL and PII class ``none``: these are Nextron's own sender
identity, never a data subject's. Story 6.2 alters these tables for the Gmail connection,
warm-up and authentication checks.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.sending_inbox import BRAND_DOMAIN, MAX_LABEL_LENGTH, InboxStatus

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
