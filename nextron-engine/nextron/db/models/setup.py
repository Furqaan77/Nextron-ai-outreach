"""The ``setup`` service's tables (AD-3, AD-12).

``settings`` holds one row per stored operator setting: its ``key`` (a key declared in
``nextron.domain.settings_schema``), its ``value`` as the canonical text ``encode`` returns, and
when it last changed. A declared key without a row has its default, or is unset when it is
required. Written only by ``nextron.db.repos.setup.settings`` (migration ``0002_1_6_settings``).
Later keys hold Nextron staff addresses and the postal address, so ``value`` is ``redact`` in
the PII registry; a secret never goes here (AD-12).

``segments`` holds one row per Segment (FR-1, Story 2.1), written only by
``nextron.db.repos.setup.segments`` (migration ``0008_2_1_segments``): its ``key`` (unique,
``uq_segments_key``), its ``definition`` as the canonical JSON
``nextron.domain.segments.definition_text`` returns, its ``version`` (1 when created, one more
at each change; ``ck_segments_version``: at least 1), ``created_at`` and ``updated_at``. The
definition holds role titles, keywords and rules, never a person, so no column is personal
data.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

KEY_LENGTH = 64  # settings_schema.KEY_PATTERN allows 31 characters; the column has room
SEGMENT_KEY_LENGTH: Final = 32  # nextron.domain.segments.KEY_PATTERN allows 31 characters


class SettingRecord(Base):
    """One stored setting."""

    __tablename__ = "settings"

    id: Mapped[str] = ulid_pk()
    key: Mapped[str] = mapped_column(String(KEY_LENGTH), unique=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class SegmentRecord(Base):
    """One stored Segment definition."""

    __tablename__ = "segments"
    __table_args__ = (CheckConstraint("version >= 1", name="version"),)

    id: Mapped[str] = ulid_pk()
    key: Mapped[str] = mapped_column(String(SEGMENT_KEY_LENGTH), unique=True)
    definition: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
