"""The ``notify`` service's tables (AD-4, AD-15, UX-DR5).

``alerts`` holds one row per Telegram alert, written only by
``nextron.db.repos.notify.alerts`` (migration ``0005_1_12_notify``):

- ``kind`` and ``subject``: what the alert is about (``job_dead`` and the job's ULID, say), and
  ``channel``: where it goes (``telegram``). ``uq_alerts_kind_subject_channel`` makes the three
  unique, so raising an alert again is a no-op. The channel has no check: a new delivery
  channel needs no migration;
- ``text``: the plain text sent. Ops alerts carry IDs and counts only, but later kinds may name
  a person, so it is ``redact`` in the PII registry;
- ``status``: its status in the Alert machine (``ck_alerts_status``);
- ``provider_message_id``: Telegram's ``message_id`` once sent (``NULL`` for a sink delivery);
- ``error_code``: the last failure's or unknown outcome's code;
- ``resent_at``: when the one resend of an ``unknown`` alert ran;
- ``created_at`` and ``updated_at``.

``notifications`` holds the dashboard's notices, written only by
``nextron.db.repos.notify.notifications``: ``kind``, ``subject`` (unique together,
``uq_notifications_kind_subject``), ``text`` (``redact``), ``created_at`` and ``dismissed_at``
(``NULL`` until Mac dismisses it). ``ix_notifications_open`` serves the notification area's
read of the undismissed ones, newest first.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

KIND_LENGTH: Final = 32  # nextron.domain.alert.KIND_PATTERN
SUBJECT_LENGTH: Final = 64  # a ULID or a token of at most 64 characters
CHANNEL_LENGTH: Final = 16
STATUS_LENGTH: Final = 16
MESSAGE_ID_LENGTH: Final = 128
ERROR_CODE_LENGTH: Final = 64


class AlertRecord(Base):
    """One alert to one delivery channel."""

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("kind", "subject", "channel", name="uq_alerts_kind_subject_channel"),
        CheckConstraint("status IN ('pending','sent','unknown','failed')", name="status"),
    )

    id: Mapped[str] = ulid_pk()
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    subject: Mapped[str] = mapped_column(String(SUBJECT_LENGTH))
    channel: Mapped[str] = mapped_column(String(CHANNEL_LENGTH))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    provider_message_id: Mapped[str | None] = mapped_column(String(MESSAGE_ID_LENGTH))
    error_code: Mapped[str | None] = mapped_column(String(ERROR_CODE_LENGTH))
    resent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class NotificationRecord(Base):
    """One notice in the dashboard's notification area."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("kind", "subject", name="uq_notifications_kind_subject"),
        Index("ix_notifications_open", "dismissed_at", "created_at"),
    )

    id: Mapped[str] = ulid_pk()
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    subject: Mapped[str] = mapped_column(String(SUBJECT_LENGTH))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    dismissed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
