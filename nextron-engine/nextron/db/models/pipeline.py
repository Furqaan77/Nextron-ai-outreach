"""The ``pipeline`` service's own tables (AD-3, AD-7).

``linkedin_accounts`` (FR-49, PRD OQ-5, Story 10.2; migration ``0037_10_2_assist_accounts``),
written only by ``nextron.db.repos.pipeline.assist_accounts``: one row per account a person sends
LinkedIn Tasks from by hand. A ULID ``id``; the Operator's ``label`` (unique:
``uq_linkedin_accounts_label``); the owner's ``owner_name`` and ``owner_role`` (the voice its Drafts
use); ``status`` (``ck_linkedin_accounts_status``: ``active`` or ``inactive``); the caps
``tasks_per_weekday``, ``daily_invite_cap`` and ``weekly_invite_cap`` (server defaults 10, 10 and
100; ``ck_linkedin_accounts_caps``: each 0 or more; ``ck_linkedin_accounts_weekly_cap``: weekly at
most 100; ``ck_linkedin_accounts_daily_cap``: daily at most weekly); ``created_at`` and
``updated_at``. The partial unique index ``uq_linkedin_accounts_active`` lets at most one row be
``active``. Every column is NOT NULL; ``nextron.domain.linkedin_caps`` holds the same rules.

``label`` and ``owner_name`` may name a Nextron staff member, so both are classed ``redact``;
every other column holds no personal data. No Contact's data is ever stored here.

``channel_permissions`` (FR-24, FR-47, Story 10.7; migration ``0038_10_7_channel_permissions``),
written only by ``nextron.db.repos.pipeline.channel_permissions``: one row per opening a
prospect gave on WhatsApp or Instagram, recorded by the Operator. A ULID ``id``; ``contact_id``
(FK ``contacts.id``, indexed); ``channel`` and ``prospect_action``
(``nextron.domain.channel_permission``: ``ck_channel_permissions_channel`` and
``ck_channel_permissions_action``, an action valid for the channel); ``occurred_at``;
``evidence_id`` (FK ``evidence.id``, the operator Quote Evidence); ``whatsapp_number`` and
``instagram_handle`` (``ck_channel_permissions_identifier``: only the channel's own, required
while ``active``); ``last_inbound_at`` (``ck_channel_permissions_last_inbound``: exactly for
Instagram, never before ``occurred_at``); ``status`` (``active`` or ``revoked``) with
``revoked_at`` (``ck_channel_permissions_revoked_at``: set exactly when ``revoked``);
``recorded_by`` (``operator``); ``created_at`` and ``updated_at``. The partial unique index
``uq_channel_permissions_active`` allows one ``active`` permission per Contact and channel.
``whatsapp_number`` and ``instagram_handle`` are the Contact's personal data, classed ``null``
(erasure NULLs them); every other column is ``none``.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.channel_permission import (
    MAX_HANDLE_LENGTH,
    MAX_NUMBER_LENGTH,
    RECORDED_BY,
    VALID_ACTIONS,
    PermissionChannel,
    PermissionStatus,
)
from nextron.domain.linkedin_caps import (
    DEFAULT_DAILY_INVITES,
    DEFAULT_TASKS_PER_WEEKDAY,
    DEFAULT_WEEKLY_INVITES,
    MAX_TEXT_LENGTH,
    MAX_WEEKLY_INVITES,
    AccountStatus,
)

ACCOUNT_STATUS_LENGTH: Final = 8
ACCOUNT_STATUS_CHECK: Final = "status IN ({})".format(
    ",".join(f"'{status}'" for status in AccountStatus)
)
CAPS_CHECK: Final = "tasks_per_weekday >= 0 AND daily_invite_cap >= 0 AND weekly_invite_cap >= 0"
WEEKLY_CAP_CHECK: Final = f"weekly_invite_cap <= {MAX_WEEKLY_INVITES}"
DAILY_CAP_CHECK: Final = "daily_invite_cap <= weekly_invite_cap"
ONE_ACTIVE_WHERE: Final = f"status = '{AccountStatus.ACTIVE}'"


class AssistAccountRecord(Base):
    """One account a person sends LinkedIn Tasks from by hand; see the module docstring."""

    __tablename__ = "linkedin_accounts"
    __table_args__ = (
        Index("uq_linkedin_accounts_label", "label", unique=True),
        Index(
            "uq_linkedin_accounts_active",
            "status",
            unique=True,
            sqlite_where=text(ONE_ACTIVE_WHERE),
        ),
        CheckConstraint(ACCOUNT_STATUS_CHECK, name="status"),
        CheckConstraint(CAPS_CHECK, name="caps"),
        CheckConstraint(WEEKLY_CAP_CHECK, name="weekly_cap"),
        CheckConstraint(DAILY_CAP_CHECK, name="daily_cap"),
    )

    id: Mapped[str] = ulid_pk()
    label: Mapped[str] = mapped_column(String(MAX_TEXT_LENGTH))
    owner_name: Mapped[str] = mapped_column(String(MAX_TEXT_LENGTH))
    owner_role: Mapped[str] = mapped_column(String(MAX_TEXT_LENGTH))
    status: Mapped[str] = mapped_column(String(ACCOUNT_STATUS_LENGTH))
    tasks_per_weekday: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_TASKS_PER_WEEKDAY))
    )
    daily_invite_cap: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_DAILY_INVITES))
    )
    weekly_invite_cap: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_WEEKLY_INVITES))
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


PERMISSION_CHANNEL_LENGTH: Final = 16
PROSPECT_ACTION_LENGTH: Final = 32
PERMISSION_STATUS_LENGTH: Final = 8
RECORDED_BY_LENGTH: Final = 16
_W: Final = PermissionChannel.WHATSAPP
_I: Final = PermissionChannel.INSTAGRAM


def _quoted(values: Iterable[str]) -> str:
    return ",".join(f"'{value}'" for value in values)


PERMISSION_CHANNEL_CHECK: Final = f"channel IN ({_quoted(PermissionChannel)})"
PERMISSION_ACTION_CHECK: Final = (
    f"(channel = '{_W}' AND prospect_action IN ({_quoted(VALID_ACTIONS[_W])}))"
    f" OR (channel = '{_I}' AND prospect_action IN ({_quoted(VALID_ACTIONS[_I])}))"
)
PERMISSION_IDENTIFIER_CHECK: Final = (
    f"(channel = '{_W}' AND instagram_handle IS NULL"
    f" AND (status = 'revoked' OR whatsapp_number IS NOT NULL))"
    f" OR (channel = '{_I}' AND whatsapp_number IS NULL"
    f" AND (status = 'revoked' OR instagram_handle IS NOT NULL))"
)
PERMISSION_INBOUND_CHECK: Final = (
    f"(channel = '{_I}') = (last_inbound_at IS NOT NULL)"
    " AND (last_inbound_at IS NULL OR last_inbound_at >= occurred_at)"
)
PERMISSION_STATUS_CHECK: Final = f"status IN ({_quoted(PermissionStatus)})"
PERMISSION_REVOKED_CHECK: Final = "(status = 'revoked') = (revoked_at IS NOT NULL)"
PERMISSION_RECORDED_BY_CHECK: Final = f"recorded_by = '{RECORDED_BY}'"
ONE_ACTIVE_PERMISSION_WHERE: Final = f"status = '{PermissionStatus.ACTIVE}'"


class ChannelPermissionRecord(Base):
    """One opening a prospect gave on WhatsApp or Instagram; see the module docstring."""

    __tablename__ = "channel_permissions"
    __table_args__ = (
        Index("ix_channel_permissions_contact_id", "contact_id"),
        Index(
            "uq_channel_permissions_active",
            "contact_id",
            "channel",
            unique=True,
            sqlite_where=text(ONE_ACTIVE_PERMISSION_WHERE),
        ),
        CheckConstraint(PERMISSION_CHANNEL_CHECK, name="channel"),
        CheckConstraint(PERMISSION_ACTION_CHECK, name="action"),
        CheckConstraint(PERMISSION_IDENTIFIER_CHECK, name="identifier"),
        CheckConstraint(PERMISSION_INBOUND_CHECK, name="last_inbound"),
        CheckConstraint(PERMISSION_STATUS_CHECK, name="status"),
        CheckConstraint(PERMISSION_REVOKED_CHECK, name="revoked_at"),
        CheckConstraint(PERMISSION_RECORDED_BY_CHECK, name="recorded_by"),
    )

    id: Mapped[str] = ulid_pk()
    contact_id: Mapped[str] = mapped_column(Ulid, ForeignKey("contacts.id"))
    channel: Mapped[str] = mapped_column(String(PERMISSION_CHANNEL_LENGTH))
    prospect_action: Mapped[str] = mapped_column(String(PROSPECT_ACTION_LENGTH))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)
    evidence_id: Mapped[str] = mapped_column(Ulid, ForeignKey("evidence.id"))
    whatsapp_number: Mapped[str | None] = mapped_column(String(MAX_NUMBER_LENGTH))
    instagram_handle: Mapped[str | None] = mapped_column(String(MAX_HANDLE_LENGTH))
    last_inbound_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(PERMISSION_STATUS_LENGTH))
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    recorded_by: Mapped[str] = mapped_column(String(RECORDED_BY_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
