"""The ``signal_inbox_items`` repository (Story 2.16, AD-3): the only code that writes the table.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.discovery`` does.

- ``StoredItem``: one row;
- ``insert(url, text, author, company, status, submitted_at, draft_when_ready=False) -> id``: a
  new row;
- ``get(item_id)``: the row, or ``None``;
- ``items(limit)``: the newest ``limit`` rows (``submitted_at DESC, id DESC``);
- ``set_parse(item_id, parse)``: writes the accepted parse's columns and ``extracted_by``;
- ``set_status(item_id, status, *, flag_reason, lead_id, processed_at)``: the status and the
  columns the status sets;
- ``reparent(loser_id, survivor_id)``: moves the rows of a merged Lead to its survivor;
- ``of_leads(lead_ids)``: every column of each item of those Leads, as a mapping, by id (the
  subject access export, Story 3.15's rules);
- ``unlinked()``: every column of each item with no Lead (``pending`` or ``flagged``), as a
  mapping, by id;
- ``undecided_draft_items(lead_id)`` (Story 4.16): the ids of the Lead's ``processed`` items
  with ``draft_when_ready`` and no ``draft_decided_at``, by id;
- ``record_draft_outcome(item_ids, outcome, at)``: sets those items' ``draft_outcome`` and
  ``draft_decided_at``, only on the ones still undecided, ``processed`` and ``draft_when_ready``
  (an item is decided once);
- ``refusals(since)``: ``(lead_id, Lead name, outcome, decided_at)`` of each item whose
  ``draft_outcome`` is set and isn't ``enrolled``, decided at or after ``since``, whose Lead
  isn't merged, newest first (by ``draft_decided_at``, then the item id, descending);
- ``tombstone(item_ids) -> int`` (the erasure hook, Story 3.13's rules): each of those items
  gets ``nextron.db.erasure.tombstoned`` (its ``redact`` columns become ``[erased]``; a NULL
  stays NULL); how many items.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.erasure import tombstoned
from nextron.db.models.base import new_id
from nextron.db.models.leads import LeadRecord, SignalInboxItemRecord
from nextron.db.uow import current
from nextron.domain.signal_inbox_item import ItemStatus

_R = SignalInboxItemRecord
TABLE: Final = "signal_inbox_items"


@dataclass(frozen=True, slots=True)
class StoredItem:
    """One ``signal_inbox_items`` row."""

    id: str
    url: str
    text: str
    author: str | None
    company: str | None
    status: str
    flag_reason: str | None
    submitted_at: datetime
    processed_at: datetime | None
    lead_id: str | None
    parsed_segment: str | None
    parsed_signal_type: str | None
    parsed_website: str | None
    parsed_author_name: str | None
    parsed_author_role: str | None
    parsed_event_date: date | None
    parsed_quote: str | None
    extracted_by: str | None
    draft_when_ready: bool
    draft_outcome: str | None
    draft_decided_at: datetime | None


@dataclass(frozen=True, slots=True)
class Refusal:
    """One item decided without an Enrollment (Story 4.16): its Lead, the Lead's name, the
    outcome and when."""

    lead_id: str
    lead_name: str
    outcome: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ParseColumns:
    """The accepted parse as stored."""

    segment: str | None
    signal_type: str | None
    website: str | None
    author_name: str | None
    author_role: str | None
    event_date: date | None
    quote: str | None
    extracted_by: str | None


_COLUMNS = (
    _R.id,
    _R.url,
    _R.text,
    _R.author,
    _R.company,
    _R.status,
    _R.flag_reason,
    _R.submitted_at,
    _R.processed_at,
    _R.lead_id,
    _R.parsed_segment,
    _R.parsed_signal_type,
    _R.parsed_website,
    _R.parsed_author_name,
    _R.parsed_author_role,
    _R.parsed_event_date,
    _R.parsed_quote,
    _R.extracted_by,
    _R.draft_when_ready,
    _R.draft_outcome,
    _R.draft_decided_at,
)


def _stored(row: object) -> StoredItem:
    return StoredItem(*row)  # type: ignore[misc]  # the columns, in field order


def insert(
    *,
    url: str,
    text: str,
    author: str | None,
    company: str | None,
    status: str,
    submitted_at: datetime,
    draft_when_ready: bool = False,
) -> str:
    """Store a new item; its new ULID."""
    item_id = new_id()
    current().session.execute(
        insert_rows(_R).values(
            id=item_id,
            url=url,
            text=text,
            author=author,
            company=company,
            status=status,
            flag_reason=None,
            submitted_at=submitted_at,
            processed_at=None,
            lead_id=None,
            draft_when_ready=draft_when_ready,
        )
    )
    return item_id


def get(item_id: str) -> StoredItem | None:
    row = current().session.execute(select(*_COLUMNS).where(_R.id == item_id)).one_or_none()
    return None if row is None else _stored(row)


def items(limit: int) -> tuple[StoredItem, ...]:
    """The newest ``limit`` items, newest first."""
    rows = current().session.execute(
        select(*_COLUMNS).order_by(_R.submitted_at.desc(), _R.id.desc()).limit(limit)
    )
    return tuple(_stored(row) for row in rows)


def set_parse(item_id: str, parse: ParseColumns) -> None:
    current().session.execute(
        update_rows(_R)
        .where(_R.id == item_id)
        .values(
            parsed_segment=parse.segment,
            parsed_signal_type=parse.signal_type,
            parsed_website=parse.website,
            parsed_author_name=parse.author_name,
            parsed_author_role=parse.author_role,
            parsed_event_date=parse.event_date,
            parsed_quote=parse.quote,
            extracted_by=parse.extracted_by,
        )
    )


def set_status(
    item_id: str,
    status: str,
    *,
    flag_reason: str | None,
    lead_id: str | None,
    processed_at: datetime | None,
) -> None:
    current().session.execute(
        update_rows(_R)
        .where(_R.id == item_id)
        .values(
            status=status,
            flag_reason=flag_reason,
            lead_id=lead_id,
            processed_at=processed_at,
        )
    )


def reparent(loser_id: str, survivor_id: str) -> int:
    """Move the merged Lead's items to its survivor; how many moved."""
    result = current().session.execute(
        update_rows(_R).where(_R.lead_id == loser_id).values(lead_id=survivor_id)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def undecided_draft_items(lead_id: str) -> tuple[str, ...]:
    """The ids of the Lead's processed items waiting for a drafting decision, by id."""
    rows = current().session.execute(
        select(_R.id)
        .where(
            _R.lead_id == lead_id,
            _R.status == ItemStatus.PROCESSED,
            _R.draft_when_ready.is_(True),
            _R.draft_decided_at.is_(None),
        )
        .order_by(_R.id)
    )
    return tuple(str(row[0]) for row in rows)


def record_draft_outcome(item_ids: Collection[str], outcome: str, at: datetime) -> int:
    """Store the drafting decision on the undecided items among ``item_ids``; how many rows
    changed."""
    wanted = sorted(set(item_ids))
    if not wanted:
        return 0
    result = current().session.execute(
        update_rows(_R)
        .where(
            _R.id.in_(wanted),
            _R.draft_decided_at.is_(None),
            _R.draft_when_ready.is_(True),
            _R.status == ItemStatus.PROCESSED,
        )
        .values(draft_outcome=outcome, draft_decided_at=at)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def refusals(since: datetime, enrolled: str) -> tuple[Refusal, ...]:
    """Each item decided at or after ``since`` with an outcome other than ``enrolled``, whose
    Lead isn't merged, newest first."""
    rows = current().session.execute(
        select(_R.lead_id, LeadRecord.name, _R.draft_outcome, _R.draft_decided_at)
        .join(LeadRecord, LeadRecord.id == _R.lead_id)
        .where(
            _R.draft_outcome.is_not(None),
            _R.draft_outcome != enrolled,
            _R.draft_decided_at >= since,
            LeadRecord.merged_into.is_(None),
        )
        .order_by(_R.draft_decided_at.desc(), _R.id.desc())
    )
    return tuple(Refusal(str(row[0]), str(row[1]), str(row[2]), row[3]) for row in rows)


def of_leads(lead_ids: Collection[str]) -> tuple[dict[str, object], ...]:
    """Every column of each item of the Leads ``lead_ids``, by id."""
    wanted = sorted(set(lead_ids))
    if not wanted:
        return ()
    rows = current().session.execute(
        select(*_COLUMNS).where(_R.lead_id.in_(wanted)).order_by(_R.id)
    ).all()
    return tuple(dict(row._mapping) for row in rows)


def unlinked() -> tuple[dict[str, object], ...]:
    """Every column of each item with no Lead (``pending`` or ``flagged``), by id."""
    rows = current().session.execute(
        select(*_COLUMNS).where(_R.lead_id.is_(None)).order_by(_R.id)
    ).all()
    return tuple(dict(row._mapping) for row in rows)


def tombstone(item_ids: Collection[str]) -> int:
    """Tombstone the items ``item_ids``; how many items that was."""
    wanted = sorted(set(item_ids))
    if not wanted:
        return 0
    session = current().session
    rows = session.execute(select(*_COLUMNS).where(_R.id.in_(wanted)).order_by(_R.id)).all()
    for row in rows:
        values = tombstoned(TABLE, dict(row._mapping))
        session.execute(update_rows(_R).where(_R.id == row.id).values(**values))
    return len(rows)
