"""The ``ops`` service's tables (AD-2, AD-20).

``runner_heartbeat`` holds exactly one row, the engine's heartbeat, written only by
``nextron.db.repos.ops.heartbeat`` (migration ``0004_1_11_heartbeat``):

- ``id``: always ``HEARTBEAT_ID``, the all-zero ULID; ``ck_runner_heartbeat_singleton`` refuses
  any other, so the table can never hold a second row;
- ``pid``: the engine process that wrote the last beat (``ck_runner_heartbeat_pid``: above 0);
- ``started_at``: when that process first beat, which is the engine's start within a second;
- ``beat_at``: the last beat.

The supervisor reads the heartbeat file, not this row; the health panel (Story 1.15) reads the
row. It holds no personal data.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

HEARTBEAT_ID: Final = "00000000000000000000000000"  # the one row's fixed id


class HeartbeatRecord(Base):
    """The engine's one heartbeat row."""

    __tablename__ = "runner_heartbeat"
    __table_args__ = (
        CheckConstraint(f"id = '{HEARTBEAT_ID}'", name="singleton"),
        CheckConstraint("pid > 0", name="pid"),
    )

    id: Mapped[str] = ulid_pk()
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    beat_at: Mapped[datetime] = mapped_column(UtcDateTime)
