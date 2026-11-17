"""The crawl tables' repository (FR-10, AD-3, AD-17): the only code that reads or writes
``crawl_runs``, ``crawled_pages`` and ``ats_links``.

Every function runs explicit Core statements through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It stores and returns what it is given and checks nothing;
``nextron.app.research`` does.

- ``StoredCrawlRun``, ``StoredPage`` (its ``text`` never shows in the ``repr``) and
  ``StoredAtsLink``: one row of each table.
- ``insert_run(...) -> id``; ``run(run_id)``; ``latest_run(lead_id)``: the Lead's run started
  last (then the greatest id), or ``None``.
- ``page(lead_id, url)``; ``pages(lead_id)``, by URL; ``insert_page(...) -> id``;
  ``update_page(page_id, ...)``: a new fetch of a stored page.
- ``ats_link(lead_id, url)``; ``ats_links(lead_id)``, by URL; ``insert_ats_link(...) -> id``;
  ``touch_ats_link(link_id, run_id, page_id, at)``: seen again.
- ``purge_text(now) -> int``: nulls ``text`` where ``text_purge_after <= now``, keeping the
  hash; returns how many rows it changed.
- ``reparent(loser_id, survivor_id)``: moves every run of the Lead ``loser_id``, and each page
  and ATS link whose ``url`` the survivor doesn't already hold, to ``survivor_id``; a moved link
  whose page stayed with the loser then points at the survivor's page with the same URL.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import ColumnElement, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows
from sqlalchemy.orm import aliased

from nextron.db.models.base import new_id
from nextron.db.models.crawl import AtsLinkRecord, CrawledPageRecord, CrawlRunRecord
from nextron.db.uow import current


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


@dataclass(frozen=True, slots=True)
class StoredPage:
    """One ``crawled_pages`` row; its ``text`` never shows in the ``repr``."""

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


# Pages ----------------------------------------------------------------------------------------


def page(lead_id: str, url: str) -> StoredPage | None:
    """The Lead's page at ``url``, or ``None``."""
    statement = select(*_PAGE_COLUMNS).where(
        CrawledPageRecord.lead_id == lead_id, CrawledPageRecord.url == url
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredPage(*row)


def pages(lead_id: str) -> tuple[StoredPage, ...]:
    """The Lead's pages, by URL."""
    statement = (
        select(*_PAGE_COLUMNS)
        .where(CrawledPageRecord.lead_id == lead_id)
        .order_by(CrawledPageRecord.url)
    )
    return tuple(StoredPage(*row) for row in current().session.execute(statement))


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
        )
    )


def purge_text(now: datetime) -> int:
    """Null every page text whose ``text_purge_after`` is ``now`` or earlier; the count."""
    result = current().session.execute(
        update_rows(CrawledPageRecord)
        .where(
            CrawledPageRecord.text.is_not(None),
            CrawledPageRecord.text_purge_after <= now,
        )
        .values(text=None)
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
