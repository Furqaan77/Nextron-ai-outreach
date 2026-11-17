"""The usage tables (AD-8, AD-9, FR-57), written only by ``nextron.db.metering`` (migration
``0006_1_13_metering``). Neither holds personal data: every column is a code, a count, a flag or
a time.

``api_usage`` holds one row per HTTP request an adapter attempted:

- ``provider``, ``operation`` and ``sku`` (``NULL`` when the provider has none): what was
  called, such as ``hunter``/``find`` or ``places``/``text_search``/``enterprise``;
- ``units``: the call's units, Hunter's credits doubled (``ck_api_usage_units``: 0 or more);
- ``ok`` and ``error_code``: the outcome; ``ck_api_usage_outcome`` keeps the code set exactly
  when the call failed;
- ``at``: when it was recorded; ``ix_api_usage_at`` serves the month's read.

``ai_usage`` holds one row per AI call: ``route``, ``route_tuple``, ``served_model``, the four
token counts, ``iterations``, ``batch``, ``cost_micros`` (micro-dollars), ``refused``,
``error_code`` and ``at`` (``ix_ai_usage_at``). Every count is 0 or more and each flag 0 or 1.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

NAME_LENGTH: Final = 32  # nextron.domain.budget.NAME_PATTERN
CODE_LENGTH: Final = 64  # nextron.domain.budget.CODE_PATTERN
MODEL_LENGTH: Final = 128  # nextron.domain.budget.MODEL_PATTERN


class ApiUsageRecord(Base):
    """One attempted HTTP request to an external provider."""

    __tablename__ = "api_usage"
    __table_args__ = (
        CheckConstraint("units >= 0", name="units"),
        CheckConstraint("ok IN (0, 1)", name="ok"),
        CheckConstraint("(ok = 1) = (error_code IS NULL)", name="outcome"),
        Index("ix_api_usage_at", "at"),
    )

    id: Mapped[str] = ulid_pk()
    provider: Mapped[str] = mapped_column(String(NAME_LENGTH))
    operation: Mapped[str] = mapped_column(String(NAME_LENGTH))
    sku: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    units: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    at: Mapped[datetime] = mapped_column(UtcDateTime)


class AiUsageRecord(Base):
    """One AI call."""

    __tablename__ = "ai_usage"
    __table_args__ = (
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 "
            "AND cache_write_tokens >= 0 AND iterations >= 0 AND cost_micros >= 0",
            name="counts",
        ),
        CheckConstraint("batch IN (0, 1) AND refused IN (0, 1)", name="flags"),
        Index("ix_ai_usage_at", "at"),
    )

    id: Mapped[str] = ulid_pk()
    route: Mapped[str] = mapped_column(String(CODE_LENGTH))
    route_tuple: Mapped[str] = mapped_column(String(MODEL_LENGTH))
    served_model: Mapped[str] = mapped_column(String(MODEL_LENGTH))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    iterations: Mapped[int] = mapped_column(Integer)
    batch: Mapped[bool] = mapped_column(Boolean)
    cost_micros: Mapped[int] = mapped_column(Integer)
    refused: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    at: Mapped[datetime] = mapped_column(UtcDateTime)
