"""The ``call_outcomes`` repository (Stories 9.1 and 9.4, AD-3): the only code that writes the
table.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.pipeline`` does.

A booking is **pending** while it has neither ``superseded_at`` nor ``recorded_at`` (Story 9.4:
a recorded outcome ends it, so a later rebooking never supersedes a recorded call).

- ``insert(*, lead_id, contact_id, scheduled_at, prospect_tz, booked_at, booked_by)``: a new
  pending booking; returns its new id;
- ``get(booking_id)``: the ``StoredBooking``, or ``None``;
- ``pending(lead_id)``: the Lead's pending bookings, oldest first (by ``booked_at``, then
  ``id``);
- ``supersede(booking_id, at)``: sets its ``superseded_at``;
- ``record(booking_id, outcome, at, by)`` (Story 9.4): stores the ``Outcome``'s columns and
  ``recorded_at``/``recorded_by``; ``update_outcome(booking_id, outcome)`` stores the columns
  only (a correction);
- ``oldest_open_due(lead_id, now)``: the Lead's oldest pending booking (by ``scheduled_at``,
  then ``id``) whose ``scheduled_at`` is at or before ``now``, or ``None``;
- ``latest_recorded(lead_id)``: the Lead's newest recorded booking that isn't superseded (by
  ``scheduled_at``, then ``id``), or ``None``;
- ``due(cutoff)``: every pending booking with ``scheduled_at`` at or before ``cutoff`` whose
  Lead (``leads``, read-only) is at ``call_booked`` and not merged, oldest first, each a
  ``DueBooking`` (the booking's and the Lead's ids and the Lead's name);
- ``redact_text(lead_ids, tombstone)``: sets ``reason`` and ``next_step`` to ``tombstone``
  wherever they hold any text on the bookings of ``lead_ids``, returning how many values
  changed;
- ``reparent(survivor_lead_id, merged_lead_id)``: moves every booking of the merged Lead to the
  survivor, returning how many;
- ``about(contact_id)``: every booking of the Contact as a row ``dict`` of all its columns
  (oldest first), for the subject access export.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import CursorResult, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.leads import LeadRecord
from nextron.db.models.pipeline import CallOutcomeRecord
from nextron.db.uow import current
from nextron.domain.call_outcomes import Outcome
from nextron.domain.pipeline_stage import PipelineStage

_COLUMNS = (
    CallOutcomeRecord.id,
    CallOutcomeRecord.lead_id,
    CallOutcomeRecord.contact_id,
    CallOutcomeRecord.scheduled_at,
    CallOutcomeRecord.prospect_tz,
    CallOutcomeRecord.booked_at,
    CallOutcomeRecord.booked_by,
    CallOutcomeRecord.superseded_at,
    CallOutcomeRecord.outcome,
    CallOutcomeRecord.qualified,
    CallOutcomeRecord.reason,
    CallOutcomeRecord.next_step,
    CallOutcomeRecord.next_step_on,
    CallOutcomeRecord.audit_proposed,
    CallOutcomeRecord.audit_price,
    CallOutcomeRecord.objections,
    CallOutcomeRecord.price_reaction,
    CallOutcomeRecord.recorded_at,
    CallOutcomeRecord.recorded_by,
)


@dataclass(frozen=True, slots=True)
class StoredBooking:
    """One ``call_outcomes`` row."""

    id: str
    lead_id: str
    contact_id: str
    scheduled_at: datetime
    prospect_tz: str
    booked_at: datetime
    booked_by: str
    superseded_at: datetime | None
    outcome: str | None = None
    qualified: bool | None = None
    reason: str | None = None
    next_step: str | None = None
    next_step_on: date | None = None
    audit_proposed: bool | None = None
    audit_price: int | None = None
    objections: str | None = None
    price_reaction: str | None = None
    recorded_at: datetime | None = None
    recorded_by: str | None = None

    @property
    def result(self) -> Outcome | None:
        """The recorded ``Outcome``, or ``None`` while the booking has none."""
        if self.recorded_at is None or self.outcome is None:
            return None
        return Outcome.from_columns(
            {
                "outcome": self.outcome,
                "qualified": self.qualified,
                "reason": self.reason,
                "audit_proposed": self.audit_proposed,
                "audit_price": self.audit_price,
                "price_reaction": self.price_reaction,
                "objections": self.objections,
                "next_step": self.next_step,
                "next_step_on": self.next_step_on,
            }
        )


@dataclass(frozen=True, slots=True)
class DueBooking:
    """A booking whose Call Outcome is due: its id, its Lead's id and the Lead's name."""

    booking_id: str
    lead_id: str
    lead_name: str


def _pending() -> tuple[object, ...]:
    return (CallOutcomeRecord.superseded_at.is_(None), CallOutcomeRecord.recorded_at.is_(None))


def insert(
    *,
    lead_id: str,
    contact_id: str,
    scheduled_at: datetime,
    prospect_tz: str,
    booked_at: datetime,
    booked_by: str,
) -> str:
    """Insert a pending booking; return its id."""
    booking_id = new_id()
    current().session.execute(
        insert_rows(CallOutcomeRecord).values(
            id=booking_id,
            lead_id=lead_id,
            contact_id=contact_id,
            scheduled_at=scheduled_at,
            prospect_tz=prospect_tz,
            booked_at=booked_at,
            booked_by=booked_by,
            superseded_at=None,
        )
    )
    return booking_id


def get(booking_id: str) -> StoredBooking | None:
    """The booking ``booking_id``, or ``None``."""
    statement = select(*_COLUMNS).where(CallOutcomeRecord.id == booking_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredBooking(*row)


def pending(lead_id: str) -> tuple[StoredBooking, ...]:
    """The Lead's pending bookings, oldest first."""
    statement = (
        select(*_COLUMNS)
        .where(CallOutcomeRecord.lead_id == lead_id, *_pending())
        .order_by(CallOutcomeRecord.booked_at, CallOutcomeRecord.id)
    )
    return tuple(StoredBooking(*row) for row in current().session.execute(statement))


def supersede(booking_id: str, at: datetime) -> None:
    """Mark the booking ``booking_id`` superseded at ``at``."""
    current().session.execute(
        update_rows(CallOutcomeRecord)
        .where(CallOutcomeRecord.id == booking_id)
        .values(superseded_at=at)
    )


def record(booking_id: str, outcome: Outcome, at: datetime, by: str) -> None:
    """Store the booking's outcome, recorded at ``at`` by ``by``."""
    current().session.execute(
        update_rows(CallOutcomeRecord)
        .where(CallOutcomeRecord.id == booking_id)
        .values(**outcome.columns(), recorded_at=at, recorded_by=by)
    )


def update_outcome(booking_id: str, outcome: Outcome) -> None:
    """Replace the booking's stored outcome (a correction)."""
    current().session.execute(
        update_rows(CallOutcomeRecord)
        .where(CallOutcomeRecord.id == booking_id)
        .values(**outcome.columns())
    )


def oldest_open_due(lead_id: str, now: datetime) -> StoredBooking | None:
    """The Lead's oldest pending booking scheduled at or before ``now``, or ``None``."""
    statement = (
        select(*_COLUMNS)
        .where(
            CallOutcomeRecord.lead_id == lead_id,
            *_pending(),
            CallOutcomeRecord.scheduled_at <= now,
        )
        .order_by(CallOutcomeRecord.scheduled_at, CallOutcomeRecord.id)
        .limit(1)
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredBooking(*row)


def latest_recorded(lead_id: str) -> StoredBooking | None:
    """The Lead's newest recorded booking that isn't superseded, or ``None``."""
    statement = (
        select(*_COLUMNS)
        .where(
            CallOutcomeRecord.lead_id == lead_id,
            CallOutcomeRecord.superseded_at.is_(None),
            CallOutcomeRecord.recorded_at.is_not(None),
        )
        .order_by(CallOutcomeRecord.scheduled_at.desc(), CallOutcomeRecord.id.desc())
        .limit(1)
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredBooking(*row)


def due(cutoff: datetime) -> tuple[DueBooking, ...]:
    """Every pending booking at ``call_booked`` Leads scheduled at or before ``cutoff``."""
    statement = (
        select(CallOutcomeRecord.id, LeadRecord.id, LeadRecord.name)
        .join(LeadRecord, LeadRecord.id == CallOutcomeRecord.lead_id)
        .where(
            *_pending(),
            CallOutcomeRecord.scheduled_at <= cutoff,
            LeadRecord.pipeline_stage == str(PipelineStage.CALL_BOOKED),
            LeadRecord.merged_into.is_(None),
        )
        .order_by(CallOutcomeRecord.scheduled_at, CallOutcomeRecord.id)
    )
    return tuple(DueBooking(*row) for row in current().session.execute(statement))


def redact_text(lead_ids: Sequence[str], tombstone: str) -> int:
    """Replace ``reason`` and ``next_step`` with ``tombstone`` on the Leads' bookings."""
    changed = 0
    for column in (CallOutcomeRecord.reason, CallOutcomeRecord.next_step):
        result = current().session.execute(
            update_rows(CallOutcomeRecord)
            .where(CallOutcomeRecord.lead_id.in_(list(lead_ids)), column.is_not(None))
            .values({column.key: tombstone})
        )
        changed += result.rowcount if isinstance(result, CursorResult) else 0
    return changed


def reparent(survivor_lead_id: str, merged_lead_id: str) -> int:
    """Move the merged Lead's bookings to the survivor; return how many moved."""
    result = current().session.execute(
        update_rows(CallOutcomeRecord)
        .where(CallOutcomeRecord.lead_id == merged_lead_id)
        .values(lead_id=survivor_lead_id)
    )
    return result.rowcount if isinstance(result, CursorResult) else 0


def about(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every booking of the Contact ``contact_id``, as row dicts, oldest first."""
    statement = (
        select(*_COLUMNS)
        .where(CallOutcomeRecord.contact_id == contact_id)
        .order_by(CallOutcomeRecord.booked_at, CallOutcomeRecord.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
