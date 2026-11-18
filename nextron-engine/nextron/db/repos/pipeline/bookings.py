"""The ``call_outcomes`` repository (Story 9.1, AD-3): the only code that writes the table.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.pipeline`` does.

- ``insert(*, lead_id, contact_id, scheduled_at, prospect_tz, booked_at, booked_by)``: a new
  pending booking (no ``superseded_at``); returns its new id;
- ``pending(lead_id)``: the Lead's pending bookings, oldest first (by ``booked_at``, then
  ``id``), each a ``StoredBooking``;
- ``supersede(booking_id, at)``: sets its ``superseded_at``;
- ``reparent(survivor_lead_id, merged_lead_id)``: moves every booking of the merged Lead to the
  survivor, returning how many;
- ``about(contact_id)``: every booking of the Contact as a row ``dict`` of all its columns
  (oldest first), for the subject access export.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import CursorResult, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.pipeline import CallOutcomeRecord
from nextron.db.uow import current

_COLUMNS = (
    CallOutcomeRecord.id,
    CallOutcomeRecord.lead_id,
    CallOutcomeRecord.contact_id,
    CallOutcomeRecord.scheduled_at,
    CallOutcomeRecord.prospect_tz,
    CallOutcomeRecord.booked_at,
    CallOutcomeRecord.booked_by,
    CallOutcomeRecord.superseded_at,
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


def pending(lead_id: str) -> tuple[StoredBooking, ...]:
    """The Lead's bookings with no ``superseded_at``, oldest first."""
    statement = (
        select(*_COLUMNS)
        .where(CallOutcomeRecord.lead_id == lead_id, CallOutcomeRecord.superseded_at.is_(None))
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
