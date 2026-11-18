"""Storing a Lead's job postings (FR-7, FR-11, AD-3, AD-7; Story 2.11): part of the ``research``
service, the sole writer of ``job_postings``, Evidence and Signals. Every path that finds or
re-checks postings (the careers step of ``extract_lead``, ``check_job_boards``,
``recheck_event`` and a merge) stores them here, in its caller's transaction. The rules
(boards, excerpts, what a careers page really lists, which hiring type a posting matches) are
pure and live in ``nextron.domain.postings`` and ``nextron.domain.signals``. Nothing here logs,
and no ``events`` row, error or notification holds a title, a description, a URL or a quote.

**A check lists postings** (``Listed(url, title, posted_date, text)``: the title normalised,
1 to 300 characters; ``text`` the full description an ATS board gave, or the careers posting's
validated quote). ``store(lead_id, segment, source, board_token, listed, checked_at, now,
tally)`` stores each (the first of a repeated ``(url, title)`` only):

- **a new posting** is inserted ``live`` (``first_seen_at`` and ``last_checked_at`` the check
  time) with its ``posted_date`` and its description (below);
- **a listed live posting** has ``last_checked_at`` updated and its live Evidence confirmed
  (``last_confirmed_at`` the check time, never earlier than it was); it isn't matched again
  (Decision 7);
- **a listed closed posting** reopens: ``live``, ``closed_at`` NULL, with the description and
  ``posted_date`` it lists now, and is matched again, so it gets a new Event (the old one stays
  retracted).

A new or reopened posting with a ``match_hiring(title, text, segment)`` match becomes one Event:
a Quote Evidence row (``quote`` the title when the term was in the title, else the stored
description: ``excerpt(text, term)``, the window holding the term; ``source`` ``ats``, or
``website`` for a careers posting; ``source_url`` and ``pages_checked`` ``[url]``;
``retrieved_at`` and ``last_confirmed_at`` the check time; ``extracted_by`` ``rule``), then a
Signal of the matched type (its class, Tier and ``score_only``; ``event_date`` the
``posted_date`` for an Event; ``first_seen_at`` the posting's) linked to it; the posting's
``signal_id`` and ``evidence_id`` point to them. The stored description is ``excerpt(text,
term)`` when the term was only in the description, else ``excerpt(text)``.

**Closing** (``close(lead_id, rows, now, tally)``): each live posting of ``rows`` becomes
``closed`` (``closed_at`` now) and its live Evidence is retracted, publishing
``EvidenceChanged(lead_id, evidence_id)``; ``lead_id`` is the Lead the Evidence is on. The
caller then retracts the Signals left without live Evidence
(``signals_repo.retract_unsupported``) and calls ``publish_closed(lead_id, tally)``, which
publishes ``EventClosed(signal_id, lead_id)`` for each closed posting that had an Event.
``close_and_publish`` does all three.

**The rules a check applies:**

- ``apply_page(lead_id, segment, url, text, kept, checked_at, now, tally)``: a careers page
  that was checked (sent in an extraction, or fetched by a recheck) at ``checked_at``. The
  ``kept`` postings (``validate_postings``'s, on that page) are stored (source
  ``careers_page``); each other live careers posting of that page stays live (touched and
  confirmed, and counted as listed) while its title still passes ``quote_in`` against ``text``,
  and is closed otherwise.
- ``apply_board(lead_id, segment, board, listing, checked_at, now, tally)``: a board's answer.
  Its postings with a title (``posting_title``) are stored (source and token the board's; a
  ``posted_date`` after the check's UTC date is dropped); each live posting of that board the
  answer doesn't list (by URL and title) is closed.
- ``close_board(lead_id, board, now, tally)``: the board is gone (``board_not_found``): every
  live posting of it is closed.

``Tally`` counts what a check did: ``listed`` and ``events`` (created) per source, ``closed``,
``reopened`` and the Signals of the postings closed.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from nextron.db.repos.research import postings as postings_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.postings import StoredPosting
from nextron.db.uow import current
from nextron.domain.extraction import EXTRACTED_BY_RULE, EvidenceSource
from nextron.domain.notifications import EventClosed, EvidenceChanged
from nextron.domain.postings import (
    Board,
    CareersPosting,
    PostingSource,
    excerpt,
    posting_title,
)
from nextron.domain.quotes import quote_in
from nextron.domain.segments import EvidenceType, SegmentDef, SignalClass
from nextron.domain.signals import HiringMatch, match_hiring
from nextron.ports.job_board import BoardListing


@dataclass(frozen=True, slots=True)
class Listed:
    """One posting a check lists; its title and text never show in a ``repr``."""

    url: str
    title: str = field(repr=False)
    posted_date: date | None
    text: str | None = field(repr=False)


@dataclass(slots=True)
class Tally:
    """What one check did, per source and in all."""

    listed: dict[PostingSource, int] = field(default_factory=dict)
    events: dict[PostingSource, int] = field(default_factory=dict)
    closed: int = 0
    reopened: int = 0
    closed_signals: list[str] = field(default_factory=list)

    def count(self, source: PostingSource, *, listed: int = 0, events: int = 0) -> None:
        self.listed[source] = self.listed.get(source, 0) + listed
        self.events[source] = self.events.get(source, 0) + events


def _evidence_source(source: PostingSource) -> EvidenceSource:
    return EvidenceSource.WEBSITE if source is PostingSource.CAREERS_PAGE else EvidenceSource.ATS


def _live_evidence(lead_id: str) -> dict[str, datetime]:
    """Each live Evidence row of the Lead, with when it was last confirmed."""
    return {
        row.id: row.last_confirmed_at
        for row in signals_repo.evidence(lead_id, live_only=True)
    }


def _described(listed: Listed, segment: SegmentDef) -> tuple[HiringMatch | None, str | None]:
    """The posting's hiring match and its stored description; see the module docstring."""
    match = match_hiring(listed.title, listed.text, segment)
    term = match.term if match is not None and not match.in_title else None
    return match, excerpt(listed.text, term)


def _event(
    lead_id: str,
    posting_id: str,
    first_seen_at: datetime,
    source: PostingSource,
    listed: Listed,
    match: HiringMatch,
    description: str | None,
    checked_at: datetime,
    now: datetime,
) -> bool:
    """Store a new or reopened posting's Event; whether one was created."""
    quote = listed.title if match.in_title else description
    if quote is None:  # a term found in an empty description can't be: nothing to quote
        return False
    evidence_id = signals_repo.insert_evidence(
        lead_id=lead_id,
        evidence_type=str(EvidenceType.QUOTE),
        quote=quote,
        sought=None,
        number=None,
        fact=None,
        value=None,
        pages_checked=(listed.url,),
        source=str(_evidence_source(source)),
        source_url=listed.url,
        retrieved_at=checked_at,
        extracted_by=EXTRACTED_BY_RULE,
        person_contact_id=None,
        at=now,
    )
    signal_type = match.signal_type
    is_event = signal_type.signal_class is SignalClass.EVENT
    signal_id = signals_repo.insert_signal(
        lead_id=lead_id,
        signal_type=signal_type.key,
        signal_class=str(signal_type.signal_class),
        tier=signal_type.tier,
        score_only=signal_type.score_only,
        event_date=listed.posted_date if is_event else None,
        first_seen_at=first_seen_at,
        evidence_ids=[evidence_id],
    )
    postings_repo.set_event(posting_id, signal_id, evidence_id)
    return True


def store(
    lead_id: str,
    segment: SegmentDef,
    source: PostingSource,
    board_token: str | None,
    listed: Iterable[Listed],
    checked_at: datetime,
    now: datetime,
    tally: Tally,
) -> set[str]:
    """Store the postings a check listed; the ids of the postings listed. See the module
    docstring."""
    ids: set[str] = set()
    seen: set[tuple[str, str]] = set()
    live = _live_evidence(lead_id)
    created = 0
    for each in listed:
        if (each.url, each.title) in seen:
            continue
        seen.add((each.url, each.title))
        found = postings_repo.posting(lead_id, each.url, each.title)
        if found is not None and found.live:
            postings_repo.touch(found.id, max(found.last_checked_at, checked_at))
            if found.evidence_id is not None and found.evidence_id in live:
                at = max(live[found.evidence_id], checked_at)
                signals_repo.confirm_evidence(found.evidence_id, at)
            ids.add(found.id)
            continue
        match, description = _described(each, segment)
        if found is None:
            posting_id = postings_repo.insert_posting(
                lead_id=lead_id,
                source=str(source),
                board_token=board_token,
                url=each.url,
                title=each.title,
                description=description,
                posted_date=each.posted_date,
                checked_at=checked_at,
            )
            first_seen_at = checked_at
        else:  # a closed posting listed again
            posting_id, first_seen_at = found.id, found.first_seen_at
            postings_repo.reopen(
                posting_id,
                description=description,
                posted_date=each.posted_date,
                checked_at=max(found.last_checked_at, checked_at),
            )
            tally.reopened += 1
        if match is not None and _event(
            lead_id,
            posting_id,
            first_seen_at,
            source,
            each,
            match,
            description,
            checked_at,
            now,
        ):
            created += 1
        ids.add(posting_id)
    tally.count(source, listed=len(ids), events=created)
    return ids


def close(lead_id: str, rows: Iterable[StoredPosting], now: datetime, tally: Tally) -> None:
    """Close each live posting of ``rows`` and retract its live Evidence; see the module
    docstring."""
    live = _live_evidence(lead_id)
    for row in rows:
        if not row.live:
            continue
        postings_repo.close(row.id, now)
        tally.closed += 1
        if row.evidence_id is not None and row.evidence_id in live:
            signals_repo.retract_evidence(row.evidence_id, now)
            del live[row.evidence_id]
            current().publish(EvidenceChanged(lead_id=lead_id, evidence_id=row.evidence_id))
        if row.signal_id is not None:
            tally.closed_signals.append(row.signal_id)


def publish_closed(lead_id: str, tally: Tally) -> None:
    """Publish ``EventClosed`` for each closed posting's Event, once its Signal is retracted."""
    for signal_id in tally.closed_signals:
        current().publish(EventClosed(signal_id=signal_id, lead_id=lead_id))
    tally.closed_signals.clear()


def close_and_publish(
    lead_id: str, rows: Iterable[StoredPosting], now: datetime, tally: Tally
) -> None:
    """Close the postings, retract the Signals left without live Evidence and publish."""
    close(lead_id, rows, now, tally)
    signals_repo.retract_unsupported(lead_id, now)
    publish_closed(lead_id, tally)


def apply_page(
    lead_id: str,
    segment: SegmentDef,
    url: str,
    text: str,
    kept: Sequence[CareersPosting],
    checked_at: datetime,
    now: datetime,
    tally: Tally,
) -> None:
    """The page rule for one careers page checked; see the module docstring."""
    source = PostingSource.CAREERS_PAGE
    listed = [
        Listed(url, posting.title, posting.posted_date, posting.description) for posting in kept
    ]
    ids = store(lead_id, segment, source, None, listed, checked_at, now, tally)
    staying: list[StoredPosting] = []
    leaving: list[StoredPosting] = []
    for row in postings_repo.for_lead(lead_id):
        if row.source != source or row.url != url or not row.live or row.id in ids:
            continue
        (staying if quote_in(row.title, text) else leaving).append(row)
    live = _live_evidence(lead_id)
    for row in staying:
        postings_repo.touch(row.id, max(row.last_checked_at, checked_at))
        if row.evidence_id is not None and row.evidence_id in live:
            at = max(live[row.evidence_id], checked_at)
            signals_repo.confirm_evidence(row.evidence_id, at)
    tally.count(source, listed=len(staying))
    close(lead_id, leaving, now, tally)


def listed_from_board(listing: BoardListing, checked_at: datetime) -> list[Listed]:
    """The postings of a board's answer that can be stored, a future date dropped."""
    today = checked_at.astimezone(UTC).date()
    found: list[Listed] = []
    for posting in listing.postings:
        title = posting_title(posting.title)
        if title is None:
            continue
        posted = posting.posted_date
        found.append(
            Listed(
                posting.url,
                title,
                None if posted is not None and posted > today else posted,
                posting.description,
            )
        )
    return found


def _board_rows(lead_id: str, board: Board) -> list[StoredPosting]:
    return [
        row
        for row in postings_repo.for_lead(lead_id)
        if row.live and row.source == board.source and row.board_token == board.token
    ]


def apply_board(
    lead_id: str,
    segment: SegmentDef,
    board: Board,
    listing: BoardListing,
    checked_at: datetime,
    now: datetime,
    tally: Tally,
) -> None:
    """Store a board's answer and close what it no longer lists; see the module docstring."""
    listed = listed_from_board(listing, checked_at)
    store(lead_id, segment, board.source, board.token, listed, checked_at, now, tally)
    keys = {(each.url, each.title) for each in listed}
    gone = [row for row in _board_rows(lead_id, board) if (row.url, row.title) not in keys]
    close(lead_id, gone, now, tally)


def close_board(lead_id: str, board: Board, now: datetime, tally: Tally) -> None:
    """The board no longer exists: close every live posting of it."""
    tally.count(board.source)
    close(lead_id, _board_rows(lead_id, board), now, tally)


def tally_counts(tally: Tally, sources: Iterable[PostingSource]) -> Mapping[str, int]:
    """The ``crawl_runs`` counts of ``sources`` (0 for a source the check didn't list)."""
    counts: dict[str, int] = {}
    for source in sources:
        prefix = "careers" if source is PostingSource.CAREERS_PAGE else str(source)
        counts[f"{prefix}_postings"] = tally.listed.get(source, 0)
        counts[f"{prefix}_events"] = tally.events.get(source, 0)
    return counts
