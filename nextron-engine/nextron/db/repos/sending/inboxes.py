"""The ``sending_inboxes`` repository (FR-32, AD-3, AD-21): the only code that writes Sending
Inboxes.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.sending`` does.

- ``StoredInbox``: one row with its Sending Domain's ``domain``;
- ``id_by_address(address)``: the id of the inbox holding that address, at any status, or
  ``None``;
- ``insert(...) -> id``: a new row at the given status;
- ``listed(statuses)``: the inboxes at those statuses, by ``created_at``, then ``id``;
- ``addresses_of(sending_domain_id, statuses)``: the addresses of that Sending Domain's inboxes
  at those statuses, sorted (Story 6.5);
- ``status_of(inbox_id)``: that inbox's status, or ``None`` when no inbox has that id (Story
  6.4);
- ``update_warmup(inbox_id, *, warmup_started_on, warmup_method, warm_level, warmup_tool_daily,
  ramp_step, ramp_target) -> bool``: set the six warm-up columns; whether a row changed (Story
  6.4);
- ``update_pacing(inbox_id, *, next_send_at, last_sent_at=UNCHANGED) -> bool``: set the inbox's
  ``next_send_at`` and, unless left ``UNCHANGED``, its ``last_sent_at``; whether a row changed
  (Story 6.11);
- ``active_ids_of(sending_domain_id)``: the ids of that Sending Domain's ``active`` inboxes,
  sorted (Story 6.6);
- ``set_status(inbox_id, *, from_status, to_status, at) -> bool``: move the inbox to
  ``to_status`` and set ``status_changed_at`` to ``at``, fenced on ``status = from_status``;
  whether a row changed (Story 6.6; ``at`` since Story 6.2);
- ``by_id(inbox_id)``: that inbox as a ``StoredInbox``, or ``None`` (Story 6.2);
- ``mark_connected(inbox_id, *, from_status, to_status, at, from_name) -> bool``: the Gmail
  connect's write (Story 6.2): ``status``, ``status_changed_at`` and ``connected_at`` (both
  ``at``) and ``from_name``, fenced on ``status = from_status``; whether a row changed;
- ``pause_causes(inbox_ids)``: each of those inboxes that is ``paused`` to the cause of its
  newest ``events`` row moving it to ``paused`` (``to_status='paused'``, newest by ``at``, then
  ``id``), read-only (Story 6.2; Story 6.3 adds a ``pause_cause`` column);
- ``newest_event_into(inbox_id, to_status)``: the ``(from_status, cause)`` of the inbox's newest
  ``events`` row into ``to_status`` (newest by ``at``, then ``id``), or ``None`` (Story 6.2).

``insert`` sets ``status_changed_at`` to ``created_at``. ``StoredInbox`` carries the six
warm-up columns (Story 6.4), the two pacing columns, ``next_send_at`` and ``last_sent_at``
(Story 6.11), and ``status_changed_at`` and ``connected_at`` (Story 6.2), too.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Final, Literal

from sqlalchemy import insert as insert_rows
from sqlalchemy import select, update

from nextron.db.models.base import new_id
from nextron.db.models.events import EventRecord
from nextron.db.models.sending import SendingDomainRecord, SendingInboxRecord
from nextron.db.uow import current

ACTIVE: Final = "active"  # nextron.domain.sending_inbox.InboxStatus.ACTIVE
PAUSED: Final = "paused"  # nextron.domain.sending_inbox.InboxStatus.PAUSED
ENTITY: Final = "sending_inboxes"


@dataclass(frozen=True, slots=True)
class StoredInbox:
    """One ``sending_inboxes`` row and its Sending Domain's ``domain``."""

    id: str
    address: str
    label: str
    sending_domain_id: str
    domain: str
    from_name: str
    status: str
    created_at: datetime
    warmup_started_on: date | None
    warmup_method: str | None
    warm_level: int | None
    warmup_tool_daily: int
    ramp_step: int
    ramp_target: int
    next_send_at: datetime | None
    last_sent_at: datetime | None
    status_changed_at: datetime
    connected_at: datetime | None


_COLUMNS: Final = (
    SendingInboxRecord.id,
    SendingInboxRecord.address,
    SendingInboxRecord.label,
    SendingInboxRecord.sending_domain_id,
    SendingDomainRecord.domain,
    SendingInboxRecord.from_name,
    SendingInboxRecord.status,
    SendingInboxRecord.created_at,
    SendingInboxRecord.warmup_started_on,
    SendingInboxRecord.warmup_method,
    SendingInboxRecord.warm_level,
    SendingInboxRecord.warmup_tool_daily,
    SendingInboxRecord.ramp_step,
    SendingInboxRecord.ramp_target,
    SendingInboxRecord.next_send_at,
    SendingInboxRecord.last_sent_at,
    SendingInboxRecord.status_changed_at,
    SendingInboxRecord.connected_at,
)


class _Unchanged(Enum):
    UNCHANGED = "unchanged"


UNCHANGED: Final = _Unchanged.UNCHANGED


def id_by_address(address: str) -> str | None:
    """The id of the inbox ``address``, at any status, or ``None``."""
    found = current().session.execute(
        select(SendingInboxRecord.id).where(SendingInboxRecord.address == address)
    )
    return found.scalar_one_or_none()


def insert(
    *,
    address: str,
    label: str,
    sending_domain_id: str,
    from_name: str,
    status: str,
    created_at: datetime,
) -> str:
    """Store a new Sending Inbox; return its new ULID."""
    inbox_id = new_id()
    current().session.execute(
        insert_rows(SendingInboxRecord).values(
            id=inbox_id,
            address=address,
            label=label,
            sending_domain_id=sending_domain_id,
            from_name=from_name,
            status=status,
            created_at=created_at,
            status_changed_at=created_at,
        )
    )
    return inbox_id


def listed(statuses: Iterable[str]) -> list[StoredInbox]:
    """The inboxes at ``statuses``, oldest first (by ``created_at``, then ``id``)."""
    wanted = [str(status) for status in statuses]
    rows = current().session.execute(
        select(*_COLUMNS)
        .join(SendingDomainRecord, SendingDomainRecord.id == SendingInboxRecord.sending_domain_id)
        .where(SendingInboxRecord.status.in_(wanted))
        .order_by(SendingInboxRecord.created_at, SendingInboxRecord.id)
    )
    return [StoredInbox(*row) for row in rows]


def by_id(inbox_id: str) -> StoredInbox | None:
    """The inbox ``inbox_id``, at any status, or ``None``."""
    row = (
        current()
        .session.execute(
            select(*_COLUMNS)
            .join(
                SendingDomainRecord,
                SendingDomainRecord.id == SendingInboxRecord.sending_domain_id,
            )
            .where(SendingInboxRecord.id == inbox_id)
        )
        .one_or_none()
    )
    return None if row is None else StoredInbox(*row)


def addresses_of(sending_domain_id: str, statuses: Iterable[str]) -> list[str]:
    """The addresses of ``sending_domain_id``'s inboxes at ``statuses``, sorted."""
    wanted = [str(status) for status in statuses]
    rows = current().session.execute(
        select(SendingInboxRecord.address)
        .where(SendingInboxRecord.sending_domain_id == sending_domain_id)
        .where(SendingInboxRecord.status.in_(wanted))
        .order_by(SendingInboxRecord.address)
    )
    return list(rows.scalars())


def status_of(inbox_id: str) -> str | None:
    """The status of the inbox ``inbox_id``, or ``None`` when there is none."""
    found = current().session.execute(
        select(SendingInboxRecord.status).where(SendingInboxRecord.id == inbox_id)
    )
    return found.scalar_one_or_none()


def update_warmup(
    inbox_id: str,
    *,
    warmup_started_on: date | None,
    warmup_method: str | None,
    warm_level: int | None,
    warmup_tool_daily: int,
    ramp_step: int,
    ramp_target: int,
) -> bool:
    """Set the inbox's six warm-up columns; whether a row changed."""
    result = current().session.execute(
        update(SendingInboxRecord)
        .where(SendingInboxRecord.id == inbox_id)
        .values(
            warmup_started_on=warmup_started_on,
            warmup_method=warmup_method,
            warm_level=warm_level,
            warmup_tool_daily=warmup_tool_daily,
            ramp_step=ramp_step,
            ramp_target=ramp_target,
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def update_pacing(
    inbox_id: str,
    *,
    next_send_at: datetime,
    last_sent_at: datetime | Literal[_Unchanged.UNCHANGED] = UNCHANGED,
) -> bool:
    """Set the inbox's ``next_send_at`` and, unless ``UNCHANGED``, its ``last_sent_at``;
    whether a row changed."""
    values: dict[str, datetime] = {"next_send_at": next_send_at}
    if last_sent_at is not UNCHANGED:
        values["last_sent_at"] = last_sent_at
    result = current().session.execute(
        update(SendingInboxRecord).where(SendingInboxRecord.id == inbox_id).values(**values)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def active_ids_of(sending_domain_id: str) -> list[str]:
    """The ids of ``sending_domain_id``'s ``active`` inboxes, sorted."""
    rows = current().session.execute(
        select(SendingInboxRecord.id)
        .where(SendingInboxRecord.sending_domain_id == sending_domain_id)
        .where(SendingInboxRecord.status == ACTIVE)
        .order_by(SendingInboxRecord.id)
    )
    return list(rows.scalars())


def set_status(inbox_id: str, *, from_status: str, to_status: str, at: datetime) -> bool:
    """Move the inbox to ``to_status`` (``status_changed_at`` = ``at``) only while it is at
    ``from_status``; whether a row changed."""
    result = current().session.execute(
        update(SendingInboxRecord)
        .where(SendingInboxRecord.id == inbox_id)
        .where(SendingInboxRecord.status == str(from_status))
        .values(status=str(to_status), status_changed_at=at)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def mark_connected(
    inbox_id: str, *, from_status: str, to_status: str, at: datetime, from_name: str
) -> bool:
    """Move the inbox to ``to_status`` as connected at ``at`` (``status_changed_at`` and
    ``connected_at``), with ``from_name``, only while it is at ``from_status``; whether a row
    changed."""
    result = current().session.execute(
        update(SendingInboxRecord)
        .where(SendingInboxRecord.id == inbox_id)
        .where(SendingInboxRecord.status == str(from_status))
        .values(
            status=str(to_status), status_changed_at=at, connected_at=at, from_name=from_name
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def newest_event_into(inbox_id: str, to_status: str) -> tuple[str | None, str] | None:
    """The ``from_status`` and ``cause`` of the inbox's newest ``events`` row into
    ``to_status``, or ``None`` when it has none."""
    row = (
        current()
        .session.execute(
            select(EventRecord.from_status, EventRecord.cause)
            .where(EventRecord.entity == ENTITY)
            .where(EventRecord.entity_id == inbox_id)
            .where(EventRecord.to_status == str(to_status))
            .order_by(EventRecord.at.desc(), EventRecord.id.desc())
            .limit(1)
        )
        .one_or_none()
    )
    return None if row is None else (row[0], row[1])


def pause_causes(inbox_ids: Iterable[str]) -> dict[str, str]:
    """Each ``paused`` inbox of ``inbox_ids`` to the cause of its newest event into
    ``paused``; an inbox without one is left out."""
    wanted = [str(inbox_id) for inbox_id in inbox_ids]
    if not wanted:
        return {}
    rows = current().session.execute(
        select(EventRecord.entity_id, EventRecord.cause)
        .join(SendingInboxRecord, SendingInboxRecord.id == EventRecord.entity_id)
        .where(EventRecord.entity == ENTITY)
        .where(EventRecord.to_status == PAUSED)
        .where(EventRecord.entity_id.in_(wanted))
        .where(SendingInboxRecord.status == PAUSED)
        .order_by(EventRecord.at, EventRecord.id)
    )
    return {entity_id: cause for entity_id, cause in rows}  # the newest wins
