"""The ``pipeline`` service's own tables (AD-3; migration ``0037_9_1_manual_stages``, Story 9.1,
extended by ``0038_9_4_call_outcomes``, Story 9.4).

``call_outcomes``: one row per discovery call booked for a Lead. ``id`` (ULID), ``lead_id``
(``fk_call_outcomes_lead_id_leads``, indexed: ``ix_call_outcomes_lead_id``), ``contact_id``
(``fk_call_outcomes_contact_id_contacts``), ``scheduled_at`` (UTC), ``prospect_tz`` (the IANA
zone the time was given in), ``booked_at``, ``booked_by`` (the actor, ``operator``) and
``superseded_at`` (nullable: set when a rebooking replaces the row). A row with neither
``superseded_at`` nor ``recorded_at`` is a pending booking.

The outcome (Story 9.4, all nullable, no CHECK: ``nextron.domain.call_outcomes`` keeps the
rules): ``outcome`` (``held``/``no_show``), ``qualified``, ``reason``, ``next_step``,
``next_step_on`` (a date), ``audit_proposed``, ``audit_price`` (micro-dollars), ``objections``
(comma-joined sorted codes, ``""`` for none), ``price_reaction``, ``recorded_at`` and
``recorded_by``. ``reason`` and ``next_step`` are free text that may name a person (PII class
``redact``); every other column is an ID, a code, an amount or a time (``none``). No foreign
key cascades.
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk

TZ_LENGTH: Final = 64  # nextron.domain.manual_stages.MAX_TIMEZONE_LENGTH
ACTOR_LENGTH: Final = 64
CODE_LENGTH: Final = 16
OBJECTIONS_LENGTH: Final = 200


class CallOutcomeRecord(Base):
    """One booked discovery call of a Lead and, once recorded, its outcome."""

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
    # Story 9.4: the outcome
    outcome: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    qualified: Mapped[bool | None] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    next_step: Mapped[str | None] = mapped_column(Text)
    next_step_on: Mapped[date | None] = mapped_column(Date)
    audit_proposed: Mapped[bool | None] = mapped_column(Boolean)
    audit_price: Mapped[int | None] = mapped_column(BigInteger)
    objections: Mapped[str | None] = mapped_column(String(OBJECTIONS_LENGTH))
    price_reaction: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    recorded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    recorded_by: Mapped[str | None] = mapped_column(String(ACTOR_LENGTH))
