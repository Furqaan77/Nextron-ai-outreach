"""The crawl tables' repository (FR-10, AD-3, AD-17): the only code that reads or writes
``crawl_runs``, ``crawled_pages``, ``ats_links`` and ``page_contact_methods``.

Every function runs explicit Core statements through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It stores and returns what it is given and checks nothing;
``nextron.app.research`` does.

- ``StoredCrawlRun``, ``StoredPage`` (its ``text`` and ``mailto`` never show in the ``repr``)
  and ``StoredAtsLink``: one row of each table.
- ``insert_run(...) -> id``; ``run(run_id)``; ``latest_run(lead_id)``: the Lead's run started
  last (then the greatest id), or ``None``; ``run_count(lead_id)``: how many runs the Lead
  holds (Story 2.9: the revision of its ``extract_lead`` job); ``run_ordinal(lead_id, run_id)``:
  that run's place among the Lead's runs (1 for its first; by ``started_at``, then id), or
  ``None`` when the Lead holds no such run (Story 4.17: the revision of a batch item).
- ``page(lead_id, url)``; ``pages(lead_id)``, by URL; ``insert_page(...) -> id``;
  ``update_page(page_id, ...)``: a new fetch of a stored page.
- ``ats_link(lead_id, url)``; ``ats_links(lead_id)``, by URL; ``insert_ats_link(...) -> id``;
  ``touch_ats_link(link_id, run_id, page_id, at)``: seen again.
- ``set_posting_counts(run_id, counts)`` (Story 2.11): writes the run's job posting counts, each
  named in ``POSTING_COUNTS`` (``careers_postings``, ``careers_events``, ``<source>_postings``
  and ``<source>_events`` for ``greenhouse``, ``lever`` and ``ashby``, and ``board_errors``);
  ``StoredCrawlRun`` carries them (``None`` while unwritten).
- ``purge_text(now) -> int``: nulls ``text`` and ``mailto`` where ``text_purge_after <= now``,
  keeping the hash; returns how many rows it changed.
- ``reparent(loser_id, survivor_id)``: moves every run of the Lead ``loser_id``, and each page
  and ATS link whose ``url`` the survivor doesn't already hold, to ``survivor_id``; a moved link
  whose page stayed with the loser then points at the survivor's page with the same URL. A
  page's ``page_contact_methods`` rows have no ``lead_id``: they follow their page.

Story 2.6:

- ``run_pages(run_id)``: the pages the run last fetched, by URL; ``set_page_text(page_id,
  text, content_hash, changed_at, mailto)``: a rendered page's text (and its ``mailto``,
  Story 2.13); ``add_render_counts(run_id, renders, errors)``: adds to the run's two counts.
- ``StoredContactMethod`` (its ``matched`` never shows in the ``repr``): one
  ``page_contact_methods`` row. ``replace_contact_methods(page_id, detections, at)`` deletes the
  page's rows and inserts one per ``nextron.domain.widgets.Detection``;
  ``add_contact_methods(page_id, detections, at) -> int`` inserts those whose ``(method,
  signature, source)`` the page doesn't hold yet and returns how many;
  ``contact_methods_of(lead_id)`` lists the rows of every page of the Lead, by the page's URL,
  then method, signature and source.

Story 2.9 (``crawled_pages.tag_managers``, stored as a JSON array of keys, sorted and without
repeats, no spaces; NULL for a page never checked):

- ``set_tag_managers(page_id, keys)``: the page's value is now ``keys``;
- ``add_tag_managers(page_id, keys)``: the page's value is now its own keys (none when NULL)
  with ``keys`` added;
- ``tag_managers_of(page_ids) -> dict[page_id, tuple[str, ...] | None]``: the value of each of
  those pages that exists (``None`` for NULL), its keys sorted.

Story 3.13: ``tombstone_pages(lead_ids) -> int`` replaces the personal-data columns of every page
of those Leads (``text`` and, from Story 2.13, ``mailto``, class ``null``: NULL;
``content_hash`` kept) and of every
``page_contact_methods`` row of those pages (``matched``, class ``redact``: ``[erased]``) with
their tombstones (``nextron.db.erasure.tombstoned``), one statement per row that changes;
returns how many pages it held.

Story 2.13 (``crawled_pages.mailto``, the addresses of the page's visible ``mailto:`` links,
stored as a JSON list in the order given; NULL when there are none):

- ``insert_page``, ``update_page`` and ``set_page_text`` take ``mailto`` (a sequence of
  addresses) and store it, replacing what the page held;
- ``StoredPage.mailto`` is the stored list as a tuple (``()`` for NULL), never in the
  ``repr``;
- ``purge_text`` nulls ``mailto`` together with ``text``.
"""

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy import delete as delete_rows
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows
from sqlalchemy.orm import aliased

from nextron.db.erasure import tombstoned
from nextron.db.models.base import new_id
from nextron.db.models.crawl import (
    AtsLinkRecord,
    CrawledPageRecord,
    CrawlRunRecord,
    PageContactMethodRecord,
)
from nextron.db.uow import current
from nextron.domain.widgets import Detection

PAGES_TABLE = "crawled_pages"
METHODS_TABLE = "page_contact_methods"


@dataclass(frozen=True, slots=True)
class StoredCrawlRun:
    """One ``crawl_runs`` row."""

    id: str
    lead_id: str
    site_domain: str
    final_domain: str | None
    started_at: datetime
    ended_at: datetime
    outcome: str
    error_code: str | None
    pages_fetched: int
    pages_skipped_robots: int
    renders: int
    errors: int
    # Story 2.11: postings and hiring Events per source, NULL while the run hasn't checked it
    careers_postings: int | None = None
    careers_events: int | None = None
    greenhouse_postings: int | None = None
    greenhouse_events: int | None = None
    lever_postings: int | None = None
    lever_events: int | None = None
    ashby_postings: int | None = None
    ashby_events: int | None = None
    board_errors: int | None = None


@dataclass(frozen=True, slots=True)
class StoredPage:
    """One ``crawled_pages`` row; its ``text`` and ``mailto`` never show in the ``repr``."""

    id: str
    lead_id: str
    run_id: str
    url: str
    final_url: str
    kind: str
    fetched_at: datetime
    http_status: int
    content_hash: str
    text: str | None = field(repr=False)
    text_purge_after: datetime
    changed_at: datetime
    mailto: tuple[str, ...] = field(repr=False)  # Story 2.13: () for NULL


@dataclass(frozen=True, slots=True)
class StoredAtsLink:
    """One ``ats_links`` row."""

    id: str
    lead_id: str
    run_id: str
    page_id: str
    url: str
    host: str
    tag: str
    first_seen_at: datetime
    last_seen_at: datetime


_RUN_COLUMNS = (
    CrawlRunRecord.id,
    CrawlRunRecord.lead_id,
    CrawlRunRecord.site_domain,
    CrawlRunRecord.final_domain,
    CrawlRunRecord.started_at,
    CrawlRunRecord.ended_at,
    CrawlRunRecord.outcome,
    CrawlRunRecord.error_code,
    CrawlRunRecord.pages_fetched,
    CrawlRunRecord.pages_skipped_robots,
    CrawlRunRecord.renders,
    CrawlRunRecord.errors,
    CrawlRunRecord.careers_postings,
    CrawlRunRecord.careers_events,
    CrawlRunRecord.greenhouse_postings,
    CrawlRunRecord.greenhouse_events,
    CrawlRunRecord.lever_postings,
    CrawlRunRecord.lever_events,
    CrawlRunRecord.ashby_postings,
    CrawlRunRecord.ashby_events,
    CrawlRunRecord.board_errors,
)
# The crawl_runs counts set_posting_counts may write (Story 2.11)
POSTING_COUNTS = frozenset(
    {
        "careers_postings",
        "careers_events",
        "greenhouse_postings",
        "greenhouse_events",
        "lever_postings",
        "lever_events",
        "ashby_postings",
        "ashby_events",
        "board_errors",
    }
)
_PAGE_COLUMNS = (
    CrawledPageRecord.id,
    CrawledPageRecord.lead_id,
    CrawledPageRecord.run_id,
    CrawledPageRecord.url,
    CrawledPageRecord.final_url,
    CrawledPageRecord.kind,
    CrawledPageRecord.fetched_at,
    CrawledPageRecord.http_status,
    CrawledPageRecord.content_hash,
    CrawledPageRecord.text,
    CrawledPageRecord.text_purge_after,
    CrawledPageRecord.changed_at,
    CrawledPageRecord.mailto,  # decoded by _page
)


def _mailto_text(addresses: Sequence[str]) -> str | None:
    """``addresses`` as the stored JSON list, in order; ``None`` (NULL) when there are none."""
    return json.dumps(list(addresses), separators=(",", ":")) if addresses else None


def _page(row: Sequence[object]) -> StoredPage:
    """A ``_PAGE_COLUMNS`` row as a ``StoredPage``, its ``mailto`` decoded."""
    *columns, mailto = row
    decoded = () if mailto is None else tuple(json.loads(str(mailto)))
    return StoredPage(*columns, mailto=decoded)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class StoredContactMethod:
    """One ``page_contact_methods`` row; its ``matched`` never shows in the ``repr``."""

    id: str
    page_id: str
    method: str
    signature: str
    source: str
    matched: str = field(repr=False)
    detected_at: datetime


_METHOD_COLUMNS = (
    PageContactMethodRecord.id,
    PageContactMethodRecord.page_id,
    PageContactMethodRecord.method,
    PageContactMethodRecord.signature,
    PageContactMethodRecord.source,
    PageContactMethodRecord.matched,
    PageContactMethodRecord.detected_at,
)
_LINK_COLUMNS = (
    AtsLinkRecord.id,
    AtsLinkRecord.lead_id,
    AtsLinkRecord.run_id,
    AtsLinkRecord.page_id,
    AtsLinkRecord.url,
    AtsLinkRecord.host,
    AtsLinkRecord.tag,
    AtsLinkRecord.first_seen_at,
    AtsLinkRecord.last_seen_at,
)


# Runs -----------------------------------------------------------------------------------------


def insert_run(
    *,
    lead_id: str,
    site_domain: str,
    final_domain: str | None,
    started_at: datetime,
    ended_at: datetime,
    outcome: str,
    error_code: str | None,
    pages_fetched: int,
    pages_skipped_robots: int,
    renders: int,
    errors: int,
) -> str:
    """Store a crawl run; return its new ULID."""
    run_id = new_id()
    current().session.execute(
        insert_rows(CrawlRunRecord).values(
            id=run_id,
            lead_id=lead_id,
            site_domain=site_domain,
            final_domain=final_domain,
            started_at=started_at,
            ended_at=ended_at,
            outcome=outcome,
            error_code=error_code,
            pages_fetched=pages_fetched,
            pages_skipped_robots=pages_skipped_robots,
            renders=renders,
            errors=errors,
        )
    )
    return run_id


def _one_run(condition: ColumnElement[bool]) -> StoredCrawlRun | None:
    statement = (
        select(*_RUN_COLUMNS)
        .where(condition)
        .order_by(CrawlRunRecord.started_at.desc(), CrawlRunRecord.id.desc())
        .limit(1)
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredCrawlRun(*row)


def run(run_id: str) -> StoredCrawlRun | None:
    """The run ``run_id``, or ``None``."""
    return _one_run(CrawlRunRecord.id == run_id)


def latest_run(lead_id: str) -> StoredCrawlRun | None:
    """The Lead's run started last, or ``None``."""
    return _one_run(CrawlRunRecord.lead_id == lead_id)


def set_posting_counts(run_id: str, counts: Mapping[str, int]) -> None:
    """Write the job posting counts ``counts`` (each a name of ``POSTING_COUNTS``) on the run
    ``run_id``; ``ValueError`` for any other name, before any write."""
    unknown = set(counts) - POSTING_COUNTS
    if unknown:
        raise ValueError(f"not a posting count: {sorted(unknown)}")
    if counts:
        current().session.execute(
            update_rows(CrawlRunRecord).where(CrawlRunRecord.id == run_id).values(**counts)
        )


def run_count(lead_id: str) -> int:
    """How many crawl runs the Lead holds."""
    statement = (
        select(func.count()).select_from(CrawlRunRecord).where(CrawlRunRecord.lead_id == lead_id)
    )
    return int(current().session.execute(statement).scalar_one())


def run_ordinal(lead_id: str, run_id: str) -> int | None:
    """The run's place among the Lead's crawl runs (1 for the first; by ``started_at``, then
    ``id``), or ``None`` when the Lead holds no run ``run_id``."""
    session = current().session
    started_at = session.execute(
        select(CrawlRunRecord.started_at).where(
            CrawlRunRecord.id == run_id, CrawlRunRecord.lead_id == lead_id
        )
    ).scalar_one_or_none()
    if started_at is None:
        return None
    statement = (
        select(func.count())
        .select_from(CrawlRunRecord)
        .where(
            CrawlRunRecord.lead_id == lead_id,
            or_(
                CrawlRunRecord.started_at < started_at,
                (CrawlRunRecord.started_at == started_at) & (CrawlRunRecord.id <= run_id),
            ),
        )
    )
    return int(session.execute(statement).scalar_one())


# Pages ----------------------------------------------------------------------------------------


def page(lead_id: str, url: str) -> StoredPage | None:
    """The Lead's page at ``url``, or ``None``."""
    statement = select(*_PAGE_COLUMNS).where(
        CrawledPageRecord.lead_id == lead_id, CrawledPageRecord.url == url
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _page(row)


def pages(lead_id: str) -> tuple[StoredPage, ...]:
    """The Lead's pages, by URL."""
    statement = (
        select(*_PAGE_COLUMNS)
        .where(CrawledPageRecord.lead_id == lead_id)
        .order_by(CrawledPageRecord.url)
    )
    return tuple(_page(row) for row in current().session.execute(statement))


def insert_page(
    *,
    lead_id: str,
    run_id: str,
    url: str,
    final_url: str,
    kind: str,
    fetched_at: datetime,
    http_status: int,
    content_hash: str,
    text: str,
    text_purge_after: datetime,
    changed_at: datetime,
    mailto: Sequence[str],
) -> str:
    """Store a new page; return its new ULID."""
    page_id = new_id()
    current().session.execute(
        insert_rows(CrawledPageRecord).values(
            id=page_id,
            lead_id=lead_id,
            run_id=run_id,
            url=url,
            final_url=final_url,
            kind=kind,
            fetched_at=fetched_at,
            http_status=http_status,
            content_hash=content_hash,
            text=text,
            text_purge_after=text_purge_after,
            changed_at=changed_at,
            mailto=_mailto_text(mailto),
        )
    )
    return page_id


def update_page(
    page_id: str,
    *,
    run_id: str,
    final_url: str,
    kind: str,
    fetched_at: datetime,
    http_status: int,
    content_hash: str,
    text: str,
    text_purge_after: datetime,
    changed_at: datetime,
    mailto: Sequence[str],
) -> None:
    """Record a new fetch of the stored page ``page_id``."""
    current().session.execute(
        update_rows(CrawledPageRecord)
        .where(CrawledPageRecord.id == page_id)
        .values(
            run_id=run_id,
            final_url=final_url,
            kind=kind,
            fetched_at=fetched_at,
            http_status=http_status,
            content_hash=content_hash,
            text=text,
            text_purge_after=text_purge_after,
            changed_at=changed_at,
            mailto=_mailto_text(mailto),
        )
    )


def run_pages(run_id: str) -> tuple[StoredPage, ...]:
    """The pages the run ``run_id`` last fetched, by URL."""
    statement = (
        select(*_PAGE_COLUMNS)
        .where(CrawledPageRecord.run_id == run_id)
        .order_by(CrawledPageRecord.url)
    )
    return tuple(_page(row) for row in current().session.execute(statement))


def set_page_text(
    page_id: str,
    *,
    text: str,
    content_hash: str,
    changed_at: datetime,
    mailto: Sequence[str],
) -> None:
    """Replace the page ``page_id``'s text (a render's), its hash and its ``mailto``."""
    current().session.execute(
        update_rows(CrawledPageRecord)
        .where(CrawledPageRecord.id == page_id)
        .values(
            text=text,
            content_hash=content_hash,
            changed_at=changed_at,
            mailto=_mailto_text(mailto),
        )
    )


def add_render_counts(run_id: str, *, renders: int, errors: int) -> None:
    """Add ``renders`` and ``errors`` to the run ``run_id``'s counts."""
    current().session.execute(
        update_rows(CrawlRunRecord)
        .where(CrawlRunRecord.id == run_id)
        .values(
            renders=CrawlRunRecord.renders + renders,
            errors=CrawlRunRecord.errors + errors,
        )
    )


def purge_text(now: datetime) -> int:
    """Null every page text and ``mailto`` list whose ``text_purge_after`` is ``now`` or
    earlier; the count."""
    result = current().session.execute(
        update_rows(CrawledPageRecord)
        .where(
            or_(CrawledPageRecord.text.is_not(None), CrawledPageRecord.mailto.is_not(None)),
            CrawledPageRecord.text_purge_after <= now,
        )
        .values(text=None, mailto=None)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


# ATS links ------------------------------------------------------------------------------------


def ats_link(lead_id: str, url: str) -> StoredAtsLink | None:
    """The Lead's ATS link at ``url``, or ``None``."""
    statement = select(*_LINK_COLUMNS).where(
        AtsLinkRecord.lead_id == lead_id, AtsLinkRecord.url == url
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredAtsLink(*row)


def ats_links(lead_id: str) -> tuple[StoredAtsLink, ...]:
    """The Lead's ATS links, by URL."""
    statement = (
        select(*_LINK_COLUMNS).where(AtsLinkRecord.lead_id == lead_id).order_by(AtsLinkRecord.url)
    )
    return tuple(StoredAtsLink(*row) for row in current().session.execute(statement))


def insert_ats_link(
    *,
    lead_id: str,
    run_id: str,
    page_id: str,
    url: str,
    host: str,
    tag: str,
    at: datetime,
) -> str:
    """Store a new ATS link first and last seen ``at``; return its new ULID."""
    link_id = new_id()
    current().session.execute(
        insert_rows(AtsLinkRecord).values(
            id=link_id,
            lead_id=lead_id,
            run_id=run_id,
            page_id=page_id,
            url=url,
            host=host,
            tag=tag,
            first_seen_at=at,
            last_seen_at=at,
        )
    )
    return link_id


def touch_ats_link(link_id: str, *, run_id: str, page_id: str, at: datetime) -> None:
    """Record that the run ``run_id`` saw the ATS link ``link_id`` again, on ``page_id``."""
    current().session.execute(
        update_rows(AtsLinkRecord)
        .where(AtsLinkRecord.id == link_id)
        .values(run_id=run_id, page_id=page_id, last_seen_at=at)
    )


# Contact methods ------------------------------------------------------------------------------


def _insert_contact_methods(
    page_id: str, detections: Iterable[Detection], at: datetime
) -> int:
    rows = [
        {
            "id": new_id(),
            "page_id": page_id,
            "method": str(detection.method),
            "signature": detection.signature,
            "source": str(detection.source),
            "matched": detection.matched,
            "detected_at": at,
        }
        for detection in detections
    ]
    if rows:
        current().session.execute(insert_rows(PageContactMethodRecord), rows)
    return len(rows)


def replace_contact_methods(page_id: str, detections: Iterable[Detection], at: datetime) -> None:
    """The page ``page_id``'s contact methods are now ``detections``, detected ``at``."""
    current().session.execute(
        delete_rows(PageContactMethodRecord).where(PageContactMethodRecord.page_id == page_id)
    )
    _insert_contact_methods(page_id, detections, at)


def add_contact_methods(page_id: str, detections: Iterable[Detection], at: datetime) -> int:
    """Add each of ``detections`` the page ``page_id`` doesn't hold yet; how many were added."""
    statement = select(
        PageContactMethodRecord.method,
        PageContactMethodRecord.signature,
        PageContactMethodRecord.source,
    ).where(PageContactMethodRecord.page_id == page_id)
    held = {tuple(row) for row in current().session.execute(statement)}
    new: list[Detection] = []
    for detection in detections:
        key = (str(detection.method), detection.signature, str(detection.source))
        if key not in held:
            held.add(key)
            new.append(detection)
    return _insert_contact_methods(page_id, new, at)


def contact_methods_of(lead_id: str) -> tuple[StoredContactMethod, ...]:
    """The contact methods of every page of the Lead ``lead_id``, by page URL, then method,
    signature and source."""
    statement = (
        select(*_METHOD_COLUMNS)
        .join(CrawledPageRecord, CrawledPageRecord.id == PageContactMethodRecord.page_id)
        .where(CrawledPageRecord.lead_id == lead_id)
        .order_by(
            CrawledPageRecord.url,
            PageContactMethodRecord.method,
            PageContactMethodRecord.signature,
            PageContactMethodRecord.source,
        )
    )
    return tuple(StoredContactMethod(*row) for row in current().session.execute(statement))


# Tag managers (Story 2.9) ---------------------------------------------------------------------


def _tag_manager_text(keys: Iterable[str]) -> str:
    return json.dumps(sorted(set(keys)), separators=(",", ":"))


def set_tag_managers(page_id: str, keys: Iterable[str]) -> None:
    """The page ``page_id`` loads the tag managers ``keys``, and only those."""
    current().session.execute(
        update_rows(CrawledPageRecord)
        .where(CrawledPageRecord.id == page_id)
        .values(tag_managers=_tag_manager_text(keys))
    )


def add_tag_managers(page_id: str, keys: Iterable[str]) -> None:
    """The page ``page_id`` also loads the tag managers ``keys``."""
    held = tag_managers_of((page_id,)).get(page_id)
    set_tag_managers(page_id, (*(held or ()), *keys))


def tag_managers_of(page_ids: Collection[str]) -> dict[str, tuple[str, ...] | None]:
    """The tag managers of each page of ``page_ids`` that exists; ``None`` for a page never
    checked."""
    if not page_ids:
        return {}
    statement = select(CrawledPageRecord.id, CrawledPageRecord.tag_managers).where(
        CrawledPageRecord.id.in_(list(page_ids))
    )
    return {
        page_id: None if text is None else tuple(sorted(json.loads(text)))
        for page_id, text in current().session.execute(statement)
    }


# Merges ---------------------------------------------------------------------------------------


def reparent(loser_id: str, survivor_id: str) -> None:
    """Move the Lead ``loser_id``'s runs, and its pages and ATS links whose URL the survivor
    lacks, to ``survivor_id``."""
    session = current().session
    session.execute(
        update_rows(CrawlRunRecord)
        .where(CrawlRunRecord.lead_id == loser_id)
        .values(lead_id=survivor_id)
    )
    held_pages = select(CrawledPageRecord.url).where(CrawledPageRecord.lead_id == survivor_id)
    session.execute(
        update_rows(CrawledPageRecord)
        .where(CrawledPageRecord.lead_id == loser_id, CrawledPageRecord.url.not_in(held_pages))
        .values(lead_id=survivor_id)
    )
    held_links = select(AtsLinkRecord.url).where(AtsLinkRecord.lead_id == survivor_id)
    session.execute(
        update_rows(AtsLinkRecord)
        .where(AtsLinkRecord.lead_id == loser_id, AtsLinkRecord.url.not_in(held_links))
        .values(lead_id=survivor_id)
    )
    # A moved link whose page stayed on the merged Lead (the survivor holds that page's URL)
    # points at the survivor's page with that URL instead.
    stayed = aliased(CrawledPageRecord)
    held = aliased(CrawledPageRecord)
    stayed_url = (
        select(stayed.url)
        .where(stayed.id == AtsLinkRecord.page_id, stayed.lead_id == loser_id)
        .scalar_subquery()
    )
    survivors_page = (
        select(held.id).where(held.lead_id == survivor_id, held.url == stayed_url).scalar_subquery()
    )
    loser_pages = select(CrawledPageRecord.id).where(CrawledPageRecord.lead_id == loser_id)
    session.execute(
        update_rows(AtsLinkRecord)
        .where(AtsLinkRecord.lead_id == survivor_id, AtsLinkRecord.page_id.in_(loser_pages))
        .values(page_id=survivors_page)
    )


# Erasure (Story 3.13) -------------------------------------------------------------------------


def _tombstone_rows(table: str, record: type, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write the tombstone of each of ``rows`` of ``table`` whose values change."""
    session = current().session
    for row in rows:
        values = tombstoned(table, row)
        if all(row[column] == value for column, value in values.items()):
            continue
        session.execute(
            update_rows(record).where(getattr(record, "id") == row["id"]).values(**values)
        )


def tombstone_pages(lead_ids: Iterable[str]) -> int:
    """Tombstone the page text and the matched contact methods of every page of the Leads
    ``lead_ids``; the number of pages they hold."""
    session = current().session
    wanted = tuple(lead_ids)
    if not wanted:
        return 0
    pages = session.execute(
        select(CrawledPageRecord.__table__)
        .where(CrawledPageRecord.lead_id.in_(wanted))
        .order_by(CrawledPageRecord.id)
    ).mappings().all()
    _tombstone_rows(PAGES_TABLE, CrawledPageRecord, pages)
    methods = session.execute(
        select(PageContactMethodRecord.__table__)
        .join(CrawledPageRecord, CrawledPageRecord.id == PageContactMethodRecord.page_id)
        .where(CrawledPageRecord.lead_id.in_(wanted))
        .order_by(PageContactMethodRecord.id)
    ).mappings().all()
    _tombstone_rows(METHODS_TABLE, PageContactMethodRecord, methods)
    return len(pages)
