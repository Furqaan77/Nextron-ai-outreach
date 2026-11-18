"""The job postings' repository (FR-7, FR-11, AD-3, AD-7; Story 2.11): the only code that reads or
writes ``job_postings``.

Every function runs explicit Core statements through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It stores and returns what it is given and checks nothing;
``nextron.app.research`` does.

- ``StoredPosting``: one row (its title and description never show in the ``repr``).
- ``insert_posting(*, lead_id, source, board_token, url, title, description, posted_date,
  checked_at) -> id``: a new ``live`` posting, first seen and last checked at ``checked_at``.
- ``posting(lead_id, url, title)``: the Lead's posting with that URL and title, or ``None``;
  ``by_signal(signal_id)``: the posting whose Event is that Signal, or ``None``;
  ``for_lead(lead_id)``: the Lead's postings, by ``first_seen_at``, then ``id``.
- ``touch(posting_id, checked_at)``: seen again (``last_checked_at``); ``reopen(posting_id, *,
  description, posted_date, checked_at)``: a closed posting listed again (``live``,
  ``closed_at`` NULL, its Event cleared); ``set_event(posting_id, signal_id, evidence_id)``: its
  Event and Quote Evidence; ``close(posting_id, at)``: ``closed`` at ``at``.
- ``reparent(loser_id, survivor_id) -> ids``: moves each posting of the Lead ``loser_id`` whose
  ``(url, title)`` the survivor doesn't hold to ``survivor_id``; returns the ids of the
  postings that stayed (they collide).
- ``tombstone_for_leads(lead_ids) -> int`` (Story 3.13's erasure): every posting of those
  Leads gets ``title`` ``[erased] <its id>`` (unique per row, so
  ``uq_job_postings_lead_id_url_title`` holds for postings that share a URL) and a
  non-NULL ``description`` ``[erased]``; returns how many postings it tombstoned.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows
from sqlalchemy.orm import aliased

from nextron.db.models.base import new_id
from nextron.db.models.crawl import JobPostingRecord
from nextron.db.uow import current
from nextron.domain.erasure import REDACTED

LIVE = "live"  # nextron.domain.postings.PostingStatus
CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class StoredPosting:
    """One ``job_postings`` row; its title and description never show in the ``repr``."""

    id: str
    lead_id: str
    source: str
    board_token: str | None
    url: str
    title: str = field(repr=False)
    description: str | None = field(repr=False)
    posted_date: date | None
    first_seen_at: datetime
    last_checked_at: datetime
    status: str
    closed_at: datetime | None
    signal_id: str | None
    evidence_id: str | None

    @property
    def live(self) -> bool:
        return self.status == LIVE


_COLUMNS = (
    JobPostingRecord.id,
    JobPostingRecord.lead_id,
    JobPostingRecord.source,
    JobPostingRecord.board_token,
    JobPostingRecord.url,
    JobPostingRecord.title,
    JobPostingRecord.description,
    JobPostingRecord.posted_date,
    JobPostingRecord.first_seen_at,
    JobPostingRecord.last_checked_at,
    JobPostingRecord.status,
    JobPostingRecord.closed_at,
    JobPostingRecord.signal_id,
    JobPostingRecord.evidence_id,
)


def insert_posting(
    *,
    lead_id: str,
    source: str,
    board_token: str | None,
    url: str,
    title: str,
    description: str | None,
    posted_date: date | None,
    checked_at: datetime,
) -> str:
    """Store a new live posting first seen at ``checked_at``; return its new ULID."""
    posting_id = new_id()
    current().session.execute(
        insert_rows(JobPostingRecord).values(
            id=posting_id,
            lead_id=lead_id,
            source=source,
            board_token=board_token,
            url=url,
            title=title,
            description=description,
            posted_date=posted_date,
            first_seen_at=checked_at,
            last_checked_at=checked_at,
            status=LIVE,
            closed_at=None,
            signal_id=None,
            evidence_id=None,
        )
    )
    return posting_id


def _one(condition: ColumnElement[bool]) -> StoredPosting | None:
    statement = select(*_COLUMNS).where(condition).order_by(JobPostingRecord.id).limit(1)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredPosting(*row)


def posting(lead_id: str, url: str, title: str) -> StoredPosting | None:
    """The Lead's posting with that URL and title, or ``None``."""
    return _one(
        (JobPostingRecord.lead_id == lead_id)
        & (JobPostingRecord.url == url)
        & (JobPostingRecord.title == title)
    )


def by_signal(signal_id: str) -> StoredPosting | None:
    """The posting whose Event is the Signal ``signal_id``, or ``None``."""
    return _one(JobPostingRecord.signal_id == signal_id)


def for_lead(lead_id: str) -> tuple[StoredPosting, ...]:
    """The Lead's postings, by ``first_seen_at``, then ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(JobPostingRecord.lead_id == lead_id)
        .order_by(JobPostingRecord.first_seen_at, JobPostingRecord.id)
    )
    return tuple(StoredPosting(*row) for row in current().session.execute(statement))


def _update(posting_id: str, **values: object) -> None:
    current().session.execute(
        update_rows(JobPostingRecord).where(JobPostingRecord.id == posting_id).values(**values)
    )


def touch(posting_id: str, checked_at: datetime) -> None:
    """Record that the posting was listed again at ``checked_at``."""
    _update(posting_id, last_checked_at=checked_at)


def reopen(
    posting_id: str, *, description: str | None, posted_date: date | None, checked_at: datetime
) -> None:
    """A closed posting is listed again: live, with what it lists now and no Event yet."""
    _update(
        posting_id,
        status=LIVE,
        closed_at=None,
        description=description,
        posted_date=posted_date,
        last_checked_at=checked_at,
        signal_id=None,
        evidence_id=None,
    )


def set_event(posting_id: str, signal_id: str, evidence_id: str) -> None:
    """The posting's Event and its Quote Evidence."""
    _update(posting_id, signal_id=signal_id, evidence_id=evidence_id)


def close(posting_id: str, at: datetime) -> None:
    """The posting is no longer listed, since ``at``."""
    _update(posting_id, status=CLOSED, closed_at=at)


def reparent(loser_id: str, survivor_id: str) -> tuple[str, ...]:
    """Move the Lead ``loser_id``'s postings whose URL and title the survivor lacks to
    ``survivor_id``; the ids of those that stayed."""
    session = current().session
    held = aliased(JobPostingRecord)
    survivor_holds = exists(
        select(held.id).where(
            held.lead_id == survivor_id,
            held.url == JobPostingRecord.url,
            held.title == JobPostingRecord.title,
        )
    )
    session.execute(
        update_rows(JobPostingRecord)
        .where(JobPostingRecord.lead_id == loser_id, ~survivor_holds)
        .values(lead_id=survivor_id)
    )
    stayed = select(JobPostingRecord.id).where(JobPostingRecord.lead_id == loser_id)
    return tuple(session.execute(stayed.order_by(JobPostingRecord.id)).scalars())


def tombstone_for_leads(lead_ids: Iterable[str]) -> int:
    """Tombstone the title and description of every posting of the Leads ``lead_ids``; how
    many postings that was."""
    wanted = sorted(set(lead_ids))
    if not wanted:
        return 0
    session = current().session
    rows = session.execute(
        select(JobPostingRecord.id, JobPostingRecord.description)
        .where(JobPostingRecord.lead_id.in_(wanted))
        .order_by(JobPostingRecord.id)
    ).all()
    for posting_id, description in rows:
        session.execute(
            update_rows(JobPostingRecord)
            .where(JobPostingRecord.id == posting_id)
            .values(
                title=f"{REDACTED} {posting_id}",
                description=None if description is None else REDACTED,
            )
        )
    return len(rows)
