"""The ``setup`` service's tables (AD-3, AD-12).

``settings`` holds one row per stored operator setting: its ``key`` (a key declared in
``nextron.domain.settings_schema``), its ``value`` as the canonical text ``encode`` returns, and
when it last changed. A declared key without a row has its default, or is unset when it is
required. Written only by ``nextron.db.repos.setup.settings`` (migration ``0002_1_6_settings``).
Later keys hold Nextron staff addresses and the postal address, so ``value`` is ``redact`` in
the PII registry; a secret never goes here (AD-12).
"""

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

KEY_LENGTH = 64  # settings_schema.KEY_PATTERN allows 31 characters; the column has room


class SettingRecord(Base):
    """One stored setting."""

    __tablename__ = "settings"

    id: Mapped[str] = ulid_pk()
    key: Mapped[str] = mapped_column(String(KEY_LENGTH), unique=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
