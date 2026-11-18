"""The ``pipeline`` service's own tables (AD-3; migration ``0037_9_1_manual_stages``, Story 9.1).

``call_outcomes``: one row per discovery call booked for a Lead. ``id`` (ULID), ``lead_id``
(``fk_call_outcomes_lead_id_leads``, indexed: ``ix_call_outcomes_lead_id``), ``contact_id``
(``fk_call_outcomes_contact_id_contacts``), ``scheduled_at`` (UTC), ``prospect_tz`` (the IANA
zone the time was given in), ``booked_at``, ``booked_by`` (the actor, ``operator``) and
``superseded_at`` (nullable: set when a rebooking replaces the row). A row with no
``superseded_at`` is the Lead's pending booking. Story 9.4 adds the outcome's columns. Every
column is an ID, a code or a time: PII class ``none``. No foreign key cascades.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

TZ_LENGTH: Final = 64  # nextron.domain.manual_stages.MAX_TIMEZONE_LENGTH
ACTOR_LENGTH: Final = 64


class CallOutcomeRecord(Base):
    """One booked discovery call of a Lead (its outcome arrives in Story 9.4)."""

    __tablename__ = "call_outcomes"
    __table_args__ = (Index("ix_call_outcomes_lead_id", "lead_id"),)

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    contact_id: Mapped[str] = mapped_column(Ulid, ForeignKey("contacts.id"))
    scheduled_at: Mapped[datetime] = mapped_column(UtcDateTime)
    prospect_tz: Mapped[str] = mapped_column(String(TZ_LENGTH))
    booked_at: Mapped[datetime] = mapped_column(UtcDateTime)
    booked_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    superseded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
