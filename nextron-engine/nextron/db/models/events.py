"""The ``events`` table (AD-4): the append-only, personal-data-free audit log of transitions.

Written only by ``nextron.db.eventlog.append``. SQLite triggers (migration
``0001_1_3_persistence_foundation``) abort any ``UPDATE`` or ``DELETE`` and any ``INSERT`` that
reuses an existing ``id``, which stops ``INSERT OR REPLACE``. No code reads it to trigger
behaviour. The class is ``EventRecord`` because "Event" is a PRD Glossary noun for a Signal.
"""

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

CODE_LENGTH = 64  # table names, statuses and causes: lowercase snake_case, at most 64
ACTOR_LENGTH = 100  # the longest actor: "ai:" + a 64-character route + "@" + a 32-character version


class EventRecord(Base):
    """One state transition of one row of ``entity``."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_entity_entity_id", "entity", "entity_id"),)

    id: Mapped[str] = ulid_pk()
    entity: Mapped[str] = mapped_column(String(CODE_LENGTH))
    entity_id: Mapped[str] = mapped_column(Ulid)
    from_status: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    to_status: Mapped[str] = mapped_column(String(CODE_LENGTH))
    cause: Mapped[str] = mapped_column(String(CODE_LENGTH))
    cause_ref: Mapped[str | None] = mapped_column(Ulid)
    actor: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
