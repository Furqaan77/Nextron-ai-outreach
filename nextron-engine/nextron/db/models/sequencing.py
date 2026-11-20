"""The ``enrollments`` and ``variant_counters`` tables (FR-3, FR-63, AD-3, AD-4, AD-16, AD-21;
migration ``0038_4_8_enrollments``), owned by ``sequencing`` (``nextron.db.repos.sequencing``).

- ``enrollments``: one row per Enrollment of a Contact in a Campaign: ``id``; the ``lead_id``
  (moved to the survivor when its Lead is merged), ``contact_id``, ``campaign_id``,
  ``variant_id``, ``sending_inbox_id``, ``sequence_id`` and ``offer_id`` (foreign keys, no
  cascade); the ``sequence_version`` it was enrolled at (``ck_enrollments_sequence_version``:
  at least 1); the ``origin`` and ``status`` (``nextron.domain.enrollment``'s values:
  ``ck_enrollments_origin``, ``ck_enrollments_status``); the ``pause_cause``
  (``ck_enrollments_pause_cause``; ``ck_enrollments_paused``: set exactly while ``paused``) and
  ``stop_cause`` (``ck_enrollments_stop_cause``; ``ck_enrollments_stopped``: set exactly when
  ``stopped``); ``created_at`` and ``updated_at``; ``last_step_sent_at``; and ``ended_at``
  (``ck_enrollments_ended``: set exactly when ``stopped`` or ``completed``;
  ``ck_enrollments_ended_after_last_step``: never before ``last_step_sent_at``); and
  ``restart_event_id`` (Story 8.11, migration ``0055_8_11_sequence_restart``; a foreign key to
  ``signals.id``): the new Event a ``restart`` Enrollment was restarted on
  (``ck_enrollments_restart_event``: set exactly when ``origin`` is ``restart``), bound for good.
  ``uq_enrollments_one_live_per_lead`` (partial, ``status IN ('active','paused')``): at most one
  live Enrollment per Lead. Indexes on ``contact_id`` and ``sending_inbox_id``. Two triggers:
  ``trg_enrollments_bound`` refuses an update of ``id`` or any bound column (``BOUND_COLUMNS``),
  and ``trg_enrollments_erased_insert`` refuses an insert for an erased Contact.
- ``variant_counters``: one row per Campaign that has enrolled anyone: ``id``, the
  ``campaign_id`` (unique: ``uq_variant_counters_campaign_id``), the ``last_variant_id``
  assigned (round-robin, AD-21) and ``updated_at``.

Every column is PII class ``none``: IDs, codes and times, never a person's data.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.enrollment import (
    EnrollmentOrigin,
    EnrollmentStatus,
    PauseCause,
    StopCause,
)

ORIGIN_LENGTH: Final = 16
STATUS_LENGTH: Final = 16
CAUSE_LENGTH: Final = 24
BOUND_COLUMNS: Final = (
    "contact_id",
    "campaign_id",
    "variant_id",
    "sending_inbox_id",
    "sequence_id",
    "sequence_version",
    "offer_id",
    "origin",
    "created_at",
    "restart_event_id",  # Story 8.11
)
LIVE_WHERE: Final = "status IN ('active','paused')"


def _one_of(column: str, values: Iterable[str]) -> str:
    return "{} IN ({})".format(column, ",".join(f"'{value}'" for value in values))


ORIGIN_CHECK: Final = _one_of("origin", EnrollmentOrigin)
STATUS_CHECK: Final = _one_of("status", EnrollmentStatus)
PAUSE_CAUSE_CHECK: Final = "pause_cause IS NULL OR " + _one_of("pause_cause", PauseCause)
STOP_CAUSE_CHECK: Final = "stop_cause IS NULL OR " + _one_of("stop_cause", StopCause)
PAUSED_CHECK: Final = "(status = 'paused') = (pause_cause IS NOT NULL)"
STOPPED_CHECK: Final = "(status = 'stopped') = (stop_cause IS NOT NULL)"
ENDED_CHECK: Final = "(status IN ('stopped','completed')) = (ended_at IS NOT NULL)"
ENDED_AFTER_LAST_STEP_CHECK: Final = (
    "ended_at IS NULL OR last_step_sent_at IS NULL OR ended_at >= last_step_sent_at"
)
SEQUENCE_VERSION_CHECK: Final = "sequence_version >= 1"
# Story 8.11: a restart names its new Event, and only a restart does (FR-63)
RESTART_EVENT_CHECK: Final = "(origin = 'restart') = (restart_event_id IS NOT NULL)"


class EnrollmentRecord(Base):
    """One Enrollment: a Contact in a Campaign, bound to a Variant, inbox, Sequence and Offer."""

    __tablename__ = "enrollments"
    __table_args__ = (
        Index(
            "uq_enrollments_one_live_per_lead",
            "lead_id",
            unique=True,
            sqlite_where=text(LIVE_WHERE),
        ),
        Index("ix_enrollments_contact_id", "contact_id"),
        Index("ix_enrollments_sending_inbox_id", "sending_inbox_id"),
        CheckConstraint(ORIGIN_CHECK, name="origin"),
        CheckConstraint(STATUS_CHECK, name="status"),
        CheckConstraint(PAUSE_CAUSE_CHECK, name="pause_cause"),
        CheckConstraint(STOP_CAUSE_CHECK, name="stop_cause"),
        CheckConstraint(PAUSED_CHECK, name="paused"),
        CheckConstraint(STOPPED_CHECK, name="stopped"),
        CheckConstraint(ENDED_CHECK, name="ended"),
        CheckConstraint(ENDED_AFTER_LAST_STEP_CHECK, name="ended_after_last_step"),
        CheckConstraint(SEQUENCE_VERSION_CHECK, name="sequence_version"),
        CheckConstraint(RESTART_EVENT_CHECK, name="restart_event"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    contact_id: Mapped[str] = mapped_column(Ulid, ForeignKey("contacts.id"))
    campaign_id: Mapped[str] = mapped_column(Ulid, ForeignKey("campaigns.id"))
    variant_id: Mapped[str] = mapped_column(Ulid, ForeignKey("variants.id"))
    sending_inbox_id: Mapped[str] = mapped_column(Ulid, ForeignKey("sending_inboxes.id"))
    sequence_id: Mapped[str] = mapped_column(Ulid, ForeignKey("sequences.id"))
    sequence_version: Mapped[int] = mapped_column(Integer)
    offer_id: Mapped[str] = mapped_column(Ulid, ForeignKey("offers.id"))
    origin: Mapped[str] = mapped_column(String(ORIGIN_LENGTH))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    pause_cause: Mapped[str | None] = mapped_column(String(CAUSE_LENGTH))
    stop_cause: Mapped[str | None] = mapped_column(String(CAUSE_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_step_sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    restart_event_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("signals.id"))


class VariantCounterRecord(Base):
    """A Campaign's round-robin counter: the last Variant it assigned."""

    __tablename__ = "variant_counters"
    __table_args__ = (Index("uq_variant_counters_campaign_id", "campaign_id", unique=True),)

    id: Mapped[str] = ulid_pk()
    campaign_id: Mapped[str] = mapped_column(Ulid, ForeignKey("campaigns.id"))
    last_variant_id: Mapped[str] = mapped_column(Ulid, ForeignKey("variants.id"))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
