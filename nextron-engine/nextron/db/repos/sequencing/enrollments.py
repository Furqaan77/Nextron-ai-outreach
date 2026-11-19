"""The ``enrollments`` repository (FR-3, AD-3, AD-4, AD-21): the only code that writes
Enrollments.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.sequencing`` applies the
Enrollment machine.

There is no setter for a bound column (``contact_id``, ``campaign_id``, ``variant_id``,
``sending_inbox_id``, ``sequence_id``, ``sequence_version``, ``offer_id``, ``origin``,
``created_at``, and since Story 11.7 ``sequence_version_id``): ``set_status`` writes only the
status group, and ``move_lead`` only ``lead_id``. The ``trg_enrollments_bound`` trigger refuses any other update.

- ``StoredEnrollment``: one row;
- ``get(enrollment_id)``: the row, or ``None``;
- ``live_for_lead(lead_id)``: the Lead's ``active`` or ``paused`` row, or ``None``;
- ``live_for_contact(contact_id)``: the Contact's live rows, oldest first;
- ``for_lead(lead_id)`` and ``for_contact(contact_id)`` (Story 4.12): every row of the Lead or
  the Contact, whatever its status, oldest first;
- ``live()``: every live row, oldest first;
- ``ended_with_step(contact_id)``: the Contact's ``stopped`` or ``completed`` rows that sent a
  Step, by ``ended_at``, then ``id``;
- ``live_counts_by_inbox()``: ``{sending_inbox_id: number of live rows}``;
- ``live_counts_by_version(sequence_id)`` (Story 11.7): ``{sequence_version: number of live
  rows}`` of the Sequence's live Enrollments;
- ``insert(...) -> id``: a new row;
- ``set_status(enrollment_id, status, pause_cause, stop_cause, ended_at, at)``: the status
  group and ``updated_at``;
- ``record_step_sent(enrollment_id, sent_at, ended_at, at)`` (Story 4.9): ``last_step_sent_at``,
  ``ended_at`` and ``updated_at``;
- ``move_lead(from_lead_id, to_lead_id, at) -> int``: every row of one Lead moved to another (a
  merge); the number moved;
- ``for_export(contact_id) -> tuple[dict, ...]`` (Story 3.15's subject access export, added at
  the merge of Story 4.8): every column of each of the Contact's rows (a generic ``select`` of
  the table, so a new column is exported without code), oldest first.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Row, func, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.sequencing import EnrollmentRecord
from nextron.db.uow import current

LIVE: Final = ("active", "paused")
ENDED: Final = ("stopped", "completed")


@dataclass(frozen=True, slots=True)
class StoredEnrollment:
    """One ``enrollments`` row."""

    id: str
    lead_id: str
    contact_id: str
    campaign_id: str
    variant_id: str
    sending_inbox_id: str
    sequence_id: str
    sequence_version: int
    offer_id: str
    origin: str
    status: str
    pause_cause: str | None
    stop_cause: str | None
    created_at: datetime
    updated_at: datetime
    last_step_sent_at: datetime | None
    ended_at: datetime | None
    sequence_version_id: str | None = None  # Story 11.7: the bound sequence_versions row


_COLUMNS: Final = (
    EnrollmentRecord.id,
    EnrollmentRecord.lead_id,
    EnrollmentRecord.contact_id,
    EnrollmentRecord.campaign_id,
    EnrollmentRecord.variant_id,
    EnrollmentRecord.sending_inbox_id,
    EnrollmentRecord.sequence_id,
    EnrollmentRecord.sequence_version,
    EnrollmentRecord.offer_id,
    EnrollmentRecord.origin,
    EnrollmentRecord.status,
    EnrollmentRecord.pause_cause,
    EnrollmentRecord.stop_cause,
    EnrollmentRecord.created_at,
    EnrollmentRecord.updated_at,
    EnrollmentRecord.last_step_sent_at,
    EnrollmentRecord.ended_at,
    EnrollmentRecord.sequence_version_id,
)
_OLDEST_FIRST: Final = (EnrollmentRecord.created_at, EnrollmentRecord.id)


def _stored(row: Row[Any]) -> StoredEnrollment:
    return StoredEnrollment(*row)


def get(enrollment_id: str) -> StoredEnrollment | None:
    """The Enrollment ``enrollment_id``, or ``None``."""
    statement = select(*_COLUMNS).where(EnrollmentRecord.id == enrollment_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def live_for_lead(lead_id: str) -> StoredEnrollment | None:
    """The Lead's ``active`` or ``paused`` Enrollment, or ``None``."""
    statement = select(*_COLUMNS).where(
        EnrollmentRecord.lead_id == lead_id, EnrollmentRecord.status.in_(LIVE)
    )
    row = current().session.execute(statement).first()
    return None if row is None else _stored(row)


def live_for_contact(contact_id: str) -> list[StoredEnrollment]:
    """The Contact's live Enrollments, oldest first."""
    statement = (
        select(*_COLUMNS)
        .where(EnrollmentRecord.contact_id == contact_id, EnrollmentRecord.status.in_(LIVE))
        .order_by(*_OLDEST_FIRST)
    )
    return [_stored(row) for row in current().session.execute(statement)]


def for_lead(lead_id: str) -> list[StoredEnrollment]:
    """Every Enrollment of the Lead, oldest first."""
    statement = (
        select(*_COLUMNS).where(EnrollmentRecord.lead_id == lead_id).order_by(*_OLDEST_FIRST)
    )
    return [_stored(row) for row in current().session.execute(statement)]


def for_contact(contact_id: str) -> list[StoredEnrollment]:
    """Every Enrollment of the Contact, oldest first."""
    statement = (
        select(*_COLUMNS)
        .where(EnrollmentRecord.contact_id == contact_id)
        .order_by(*_OLDEST_FIRST)
    )
    return [_stored(row) for row in current().session.execute(statement)]


def live() -> list[StoredEnrollment]:
    """Every live Enrollment, oldest first."""
    statement = select(*_COLUMNS).where(EnrollmentRecord.status.in_(LIVE)).order_by(*_OLDEST_FIRST)
    return [_stored(row) for row in current().session.execute(statement)]


def ended_with_step(contact_id: str) -> list[StoredEnrollment]:
    """The Contact's ended Enrollments that sent a Step, by ``ended_at``, then ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(
            EnrollmentRecord.contact_id == contact_id,
            EnrollmentRecord.status.in_(ENDED),
            EnrollmentRecord.last_step_sent_at.is_not(None),
        )
        .order_by(EnrollmentRecord.ended_at, EnrollmentRecord.id)
    )
    return [_stored(row) for row in current().session.execute(statement)]


def live_counts_by_inbox() -> dict[str, int]:
    """The number of live Enrollments bound to each Sending Inbox that has any."""
    statement = (
        select(EnrollmentRecord.sending_inbox_id, func.count())
        .where(EnrollmentRecord.status.in_(LIVE))
        .group_by(EnrollmentRecord.sending_inbox_id)
    )
    return {str(inbox): int(count) for inbox, count in current().session.execute(statement)}


def live_counts_by_version(sequence_id: str) -> dict[int, int]:
    """The number of live Enrollments of the Sequence ``sequence_id`` at each version."""
    statement = (
        select(EnrollmentRecord.sequence_version, func.count())
        .where(
            EnrollmentRecord.sequence_id == sequence_id, EnrollmentRecord.status.in_(LIVE)
        )
        .group_by(EnrollmentRecord.sequence_version)
    )
    return {
        int(version): int(count) for version, count in current().session.execute(statement)
    }


def insert(
    *,
    lead_id: str,
    contact_id: str,
    campaign_id: str,
    variant_id: str,
    sending_inbox_id: str,
    sequence_id: str,
    sequence_version: int,
    offer_id: str,
    origin: str,
    status: str,
    at: datetime,
    sequence_version_id: str | None = None,
) -> str:
    """Store a new Enrollment created at ``at``, bound to the ``sequence_versions`` row
    ``sequence_version_id`` (Story 11.7); return its new ULID."""
    enrollment_id = new_id()
    current().session.execute(
        insert_rows(EnrollmentRecord).values(
            id=enrollment_id,
            lead_id=lead_id,
            contact_id=contact_id,
            campaign_id=campaign_id,
            variant_id=variant_id,
            sending_inbox_id=sending_inbox_id,
            sequence_id=sequence_id,
            sequence_version=sequence_version,
            offer_id=offer_id,
            origin=origin,
            status=status,
            pause_cause=None,
            stop_cause=None,
            created_at=at,
            updated_at=at,
            last_step_sent_at=None,
            ended_at=None,
            sequence_version_id=sequence_version_id,
        )
    )
    return enrollment_id


def set_status(
    enrollment_id: str,
    *,
    status: str,
    pause_cause: str | None,
    stop_cause: str | None,
    ended_at: datetime | None,
    at: datetime,
) -> None:
    """Set the Enrollment's status group and ``updated_at``."""
    current().session.execute(
        update_rows(EnrollmentRecord)
        .where(EnrollmentRecord.id == enrollment_id)
        .values(
            status=status,
            pause_cause=pause_cause,
            stop_cause=stop_cause,
            ended_at=ended_at,
            updated_at=at,
        )
    )


def record_step_sent(
    enrollment_id: str, *, sent_at: datetime, ended_at: datetime | None, at: datetime
) -> None:
    """Set the Enrollment's ``last_step_sent_at``, ``ended_at`` and ``updated_at``."""
    current().session.execute(
        update_rows(EnrollmentRecord)
        .where(EnrollmentRecord.id == enrollment_id)
        .values(last_step_sent_at=sent_at, ended_at=ended_at, updated_at=at)
    )


def move_lead(from_lead_id: str, to_lead_id: str, at: datetime) -> int:
    """Move every Enrollment of the Lead ``from_lead_id`` to ``to_lead_id``; the number moved."""
    result = current().session.execute(
        update_rows(EnrollmentRecord)
        .where(EnrollmentRecord.lead_id == from_lead_id)
        .values(lead_id=to_lead_id, updated_at=at)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def for_export(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of each of the Contact's Enrollments, oldest first."""
    table = EnrollmentRecord.__table__
    statement = (
        select(table)
        .where(table.c.contact_id == contact_id)
        .order_by(table.c.created_at, table.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
