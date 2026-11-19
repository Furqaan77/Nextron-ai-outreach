"""The ``outbound_messages`` repository (FR-26, AD-3, AD-4, AD-6, AD-17; Story 5.2): the only code
that writes OutboundMessages.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.sending`` does. The copy
and the snapshot never change: the table's triggers refuse a ``DELETE`` and any ``UPDATE`` of
them but the erasure tombstone (``tombstone_contact``).

- ``StoredOutboundMessage``: one row;
- ``new_om_id() -> str``: a new ULID for an OM (its unsubscribe token is issued for that id
  before the row is inserted);
- ``NewOutboundMessage(...)`` and ``insert(om_id, message, *, status, created_at) -> id``: one
  new row;
- ``get(om_id)``: the row, or ``None``;
- ``live_of_draft(draft_id)``: the Draft's OM whose status isn't ``cancelled``, or ``None``;
- ``sent_of_enrollment(enrollment_id)``: the Enrollment's OMs at status ``sent``, by
  ``step_no`` (then ``sent_at`` and id; Story 5.3);
- ``set_status(om_id, from_status, to_status, at) -> bool``: a conditional status update (only
  while the row is at ``from_status``); a move to ``cancelled`` sets ``cancelled_at`` and one to
  ``sent`` sets ``sent_at`` to ``at``. Whether a row changed;
- ``move_lead(from_lead_id, to_lead_id) -> int``: every OM of the merged Lead names the survivor
  (a Lead merge, AD-3); the rows moved;
- ``tombstone_contact(contact_id) -> int``: every OM of the Contact gets ``to_address``,
  ``subject``, ``body`` and ``unsub_token`` ``[erased]`` (ids, statuses, times, hash and
  snapshot kept); the rows matched. Running it again changes nothing;
- ``for_export(contact_id) -> tuple[dict, ...]``: every column of each OM of the Contact but
  ``unsub_token`` (withheld from the export), oldest first.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select, update
from sqlalchemy import insert as insert_rows

from nextron.db.models.base import new_id
from nextron.db.models.sending import OutboundMessageRecord
from nextron.db.uow import current
from nextron.domain.erasure import REDACTED
from nextron.domain.outbound_message import OmStatus

WITHHELD: Final = frozenset({"unsub_token"})
CANCELLED: Final = OmStatus.CANCELLED
SENT: Final = OmStatus.SENT


@dataclass(frozen=True, slots=True)
class NewOutboundMessage:
    """The copy and the snapshot of a new OM."""

    draft_id: str
    approval_id: str
    kind: str
    to_address: str
    inbox_id: str
    contact_id: str
    lead_id: str
    enrollment_id: str | None
    subject: str
    body: str
    approved_hash: str
    unsub_token: str
    tier: str | None
    lead_score: int | None
    variant_id: str | None
    segment_id: str
    market_id: str | None
    step_no: int | None


@dataclass(frozen=True, slots=True)
class StoredOutboundMessage:
    """One ``outbound_messages`` row."""

    id: str
    draft_id: str
    approval_id: str
    kind: str
    status: str
    to_address: str
    inbox_id: str
    contact_id: str
    lead_id: str
    enrollment_id: str | None
    subject: str
    body: str
    approved_hash: str
    unsub_token: str
    tier: str | None
    lead_score: int | None
    variant_id: str | None
    segment_id: str
    market_id: str | None
    step_no: int | None
    created_at: datetime
    cancelled_at: datetime | None
    sent_at: datetime | None


_COLUMNS: Final = tuple(
    getattr(OutboundMessageRecord, name) for name in StoredOutboundMessage.__slots__
)


def new_om_id() -> str:
    """A new ULID for an OutboundMessage."""
    return new_id()


def insert(
    om_id: str, message: NewOutboundMessage, *, status: str, created_at: datetime
) -> str:
    """Store a new OM with id ``om_id`` at ``status``; return its id."""
    values = {name: getattr(message, name) for name in NewOutboundMessage.__slots__}
    current().session.execute(
        insert_rows(OutboundMessageRecord).values(
            id=om_id, status=status, created_at=created_at, **values
        )
    )
    return om_id


def get(om_id: str) -> StoredOutboundMessage | None:
    """The OM ``om_id``, or ``None``."""
    row = current().session.execute(
        select(*_COLUMNS).where(OutboundMessageRecord.id == om_id)
    ).first()
    return None if row is None else StoredOutboundMessage(*row)


def live_of_draft(draft_id: str) -> StoredOutboundMessage | None:
    """The Draft's OM that isn't ``cancelled``, or ``None``."""
    row = current().session.execute(
        select(*_COLUMNS).where(
            OutboundMessageRecord.draft_id == draft_id,
            OutboundMessageRecord.status != CANCELLED,
        )
    ).first()
    return None if row is None else StoredOutboundMessage(*row)


def sent_of_enrollment(enrollment_id: str) -> tuple[StoredOutboundMessage, ...]:
    """The Enrollment's OMs at status ``sent``, by ``step_no``, then ``sent_at`` and id."""
    rows = current().session.execute(
        select(*_COLUMNS)
        .where(
            OutboundMessageRecord.enrollment_id == enrollment_id,
            OutboundMessageRecord.status == SENT,
        )
        .order_by(
            OutboundMessageRecord.step_no,
            OutboundMessageRecord.sent_at,
            OutboundMessageRecord.id,
        )
    )
    return tuple(StoredOutboundMessage(*row) for row in rows)


def set_status(om_id: str, from_status: str, to_status: str, at: datetime) -> bool:
    """Move the OM from ``from_status`` to ``to_status`` (only while it is at ``from_status``);
    whether a row changed."""
    values: dict[str, object] = {"status": to_status}
    if to_status == CANCELLED:
        values["cancelled_at"] = at
    if to_status == SENT:
        values["sent_at"] = at
    result = current().session.execute(
        update(OutboundMessageRecord)
        .where(OutboundMessageRecord.id == om_id, OutboundMessageRecord.status == from_status)
        .values(**values)
    )
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


def move_lead(from_lead_id: str, to_lead_id: str) -> int:
    """Move every OM of ``from_lead_id`` to ``to_lead_id`` (a Lead merge); the rows moved."""
    result = current().session.execute(
        update(OutboundMessageRecord)
        .where(OutboundMessageRecord.lead_id == from_lead_id)
        .values(lead_id=to_lead_id)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def tombstone_contact(contact_id: str) -> int:
    """Redact the copy of every OM of the Contact (the erasure); the rows matched."""
    result = current().session.execute(
        update(OutboundMessageRecord)
        .where(OutboundMessageRecord.contact_id == contact_id)
        .values(to_address=REDACTED, subject=REDACTED, body=REDACTED, unsub_token=REDACTED)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def for_export(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column but ``unsub_token`` of each OM of the Contact, oldest first."""
    table = OutboundMessageRecord.__table__
    shown = [column for column in table.c if column.name not in WITHHELD]
    statement = (
        select(*shown)
        .where(table.c.contact_id == contact_id)
        .order_by(table.c.created_at, table.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
