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

``markets`` holds one row per Market (FR-2, Story 3.1), written only by
``nextron.db.repos.setup.markets`` (migration ``0010_3_1_markets``): the
``nextron.domain.markets.MarketDef`` fields as typed columns, keyed by ``code`` (unique,
``uq_markets_code``) with a unique ``country_code`` (``uq_markets_country_code``). ``cctlds``,
``footer_template`` and ``eligible_legal_forms`` hold canonical JSON lists;
``eligible_legal_forms`` is NULL when legal forms are not checked, and ``unknown_send_window``
(``HH:MM-HH:MM``) is NULL when it isn't narrowed. ``version`` is 1 when created and one more at
each change (``ck_markets_version``: at least 1). Every other column is NOT NULL. A footer
template holds placeholders and rules, never a person, so no column is personal data. Other
tables reference a Market by its ULID ``id`` (``market_id``).

``sequences`` holds one row per Sequence (FR-4, Story 4.1), written only by
``nextron.db.repos.setup.sequences`` (migration ``0013_4_1_sequences``): its ``key`` (unique,
``uq_sequences_key``), its ``name``, its ``version`` (1 when created, one more at each change;
``ck_sequences_version``: at least 1), ``created_at`` and ``updated_at``. A Sequence's version
and its ``events`` row cover its Step briefs too.

``step_briefs`` holds one row per Step of a Sequence, written by the same repository: the
``sequence_id`` of its Sequence (``fk_step_briefs_sequence_id_sequences``, a foreign key with
no cascade), the 1-based ``step`` (``ck_step_briefs_step``: 1 to 6; unique per Sequence,
``uq_step_briefs_sequence_id_step``) and the ``nextron.domain.sequences.StepBrief`` fields as
typed columns: ``delay_days`` (``ck_step_briefs_delay_days``: at least 0), ``purpose``,
``word_limit`` (``ck_step_briefs_word_limit``: 1 to 150), ``word_comparison``,
``allowed_content`` (a canonical JSON list), ``ask``, ``link_rule``, ``no_price``, ``no_audit``,
``subject_rule`` (a canonical JSON object, NULL when the Step has none) and ``segment_angles``
(a canonical JSON list, ``[]`` for none), with ``created_at`` and ``updated_at``. Every other
column is NOT NULL. Briefs hold rules and angles, never a person, so no column of either table
is personal data.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

KEY_LENGTH = 64  # settings_schema.KEY_PATTERN allows 31 characters; the column has room
SEGMENT_KEY_LENGTH: Final = 32  # nextron.domain.segments.KEY_PATTERN allows 31 characters
MARKET_CODE_LENGTH: Final = 16  # nextron.domain.markets.CODE_PATTERN
MARKET_NAME_LENGTH: Final = 100  # nextron.domain.markets.MAX_NAME
COUNTRY_CODE_LENGTH: Final = 2
TIMEZONE_LENGTH: Final = 64  # IANA names are far shorter
SEND_WINDOW_LENGTH: Final = 11  # HH:MM-HH:MM
ENGLISH_VARIANT_LENGTH: Final = 5  # en_us, en_gb
SEQUENCE_KEY_LENGTH: Final = 32  # nextron.domain.sequences.KEY_PATTERN allows 31 characters
SEQUENCE_NAME_LENGTH: Final = 100  # nextron.domain.sequences.MAX_NAME
BRIEF_TEXT_LENGTH: Final = 200  # nextron.domain.sequences.MAX_TEXT: purpose, ask
WORD_COMPARISON_LENGTH: Final = 8  # under, at_most
LINK_RULE_LENGTH: Final = 16  # footer_only


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


class MarketRecord(Base):
    """One stored Market."""

    __tablename__ = "markets"
    __table_args__ = (CheckConstraint("version >= 1", name="version"),)

    id: Mapped[str] = ulid_pk()
    code: Mapped[str] = mapped_column(String(MARKET_CODE_LENGTH), unique=True)
    name: Mapped[str] = mapped_column(String(MARKET_NAME_LENGTH))
    enabled: Mapped[bool] = mapped_column(Boolean)
    country_code: Mapped[str] = mapped_column(String(COUNTRY_CODE_LENGTH), unique=True)
    cctlds: Mapped[str] = mapped_column(Text)
    cold_email_allowed: Mapped[bool] = mapped_column(Boolean)
    eligible_legal_forms: Mapped[str | None] = mapped_column(Text)
    free_webmail_blocked: Mapped[bool] = mapped_column(Boolean)
    default_timezone: Mapped[str] = mapped_column(String(TIMEZONE_LENGTH))
    unknown_timezone: Mapped[str] = mapped_column(String(TIMEZONE_LENGTH))
    unknown_send_window: Mapped[str | None] = mapped_column(String(SEND_WINDOW_LENGTH))
    english_variant: Mapped[str] = mapped_column(String(ENGLISH_VARIANT_LENGTH))
    footer_template: Mapped[str] = mapped_column(Text)
    privacy_line_required: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class SequenceRecord(Base):
    """One stored Sequence; its Steps are ``StepBriefRecord`` rows."""

    __tablename__ = "sequences"
    __table_args__ = (CheckConstraint("version >= 1", name="version"),)

    id: Mapped[str] = ulid_pk()
    key: Mapped[str] = mapped_column(String(SEQUENCE_KEY_LENGTH), unique=True)
    name: Mapped[str] = mapped_column(String(SEQUENCE_NAME_LENGTH))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class StepBriefRecord(Base):
    """One Step's brief in a stored Sequence."""

    __tablename__ = "step_briefs"
    __table_args__ = (
        UniqueConstraint("sequence_id", "step"),
        CheckConstraint("step BETWEEN 1 AND 6", name="step"),
        CheckConstraint("delay_days >= 0", name="delay_days"),
        CheckConstraint("word_limit BETWEEN 1 AND 150", name="word_limit"),
    )

    id: Mapped[str] = ulid_pk()
    sequence_id: Mapped[str] = mapped_column(Ulid, ForeignKey("sequences.id"))
    step: Mapped[int] = mapped_column(Integer)
    delay_days: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(BRIEF_TEXT_LENGTH))
    word_limit: Mapped[int] = mapped_column(Integer)
    word_comparison: Mapped[str] = mapped_column(String(WORD_COMPARISON_LENGTH))
    allowed_content: Mapped[str] = mapped_column(Text)
    ask: Mapped[str] = mapped_column(String(BRIEF_TEXT_LENGTH))
    link_rule: Mapped[str] = mapped_column(String(LINK_RULE_LENGTH))
    no_price: Mapped[bool] = mapped_column(Boolean)
    no_audit: Mapped[bool] = mapped_column(Boolean)
    subject_rule: Mapped[str | None] = mapped_column(Text)
    segment_angles: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
