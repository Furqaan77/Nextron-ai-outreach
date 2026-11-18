"""The ``scoring`` service's own table (FR-19 S1, FR-59, UX-DR20; Story 4.10, migration
``0052_4_10_daily_capacity``), written only by ``nextron.db.repos.scoring.capacity``.

``daily_capacity``: one row per calendar day in the ``sender_timezone``. A ULID ``id``;
``local_date`` (unique, ``uq_daily_capacity_local_date``); the live figures at the last
computation that day: ``total_cold_capacity`` (the sum of the inboxes' cold Daily Caps),
``follow_ups_reserved`` (the follow-ups due that day) and ``new_sequence_capacity``
(``nextron.domain.sending_policy.new_sequence_capacity``); ``picked`` (server default 0), the
Leads enrolled by the Operator's picks that day; ``signal_shortfall``, NULL until a later day's
computation finalises the row to ``max(0, new_sequence_capacity - picked)``; and
``computed_at``. ``ck_daily_capacity_counts``: no count is negative. Every column holds no
personal data: IDs, dates, counts and times.
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import CheckConstraint, Date, Integer, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

COUNTS_CHECK: Final = (
    "total_cold_capacity >= 0 AND follow_ups_reserved >= 0 AND new_sequence_capacity >= 0 "
    "AND picked >= 0 AND (signal_shortfall IS NULL OR signal_shortfall >= 0)"
)


class DailyCapacityRecord(Base):
    """One day's capacity figures and picks."""

    __tablename__ = "daily_capacity"
    __table_args__ = (
        UniqueConstraint("local_date"),
        CheckConstraint(COUNTS_CHECK, name="counts"),
    )

    id: Mapped[str] = ulid_pk()
    local_date: Mapped[date] = mapped_column(Date)
    total_cold_capacity: Mapped[int] = mapped_column(Integer)
    follow_ups_reserved: Mapped[int] = mapped_column(Integer)
    new_sequence_capacity: Mapped[int] = mapped_column(Integer)
    picked: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    signal_shortfall: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(UtcDateTime)
