"""Read API of the ``research`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.research`` only. All read through the
current unit of work, read-only ones included. A text that isn't a ULID finds nothing.

- ``crawl_run(run_id)``: the crawl run as a ``StoredCrawlRun``, or ``None``.
- ``latest_crawl_run(lead_id)``: the Lead's run started last, or ``None``. The Lead page
  (Story 2.4) shows its outcome with ``nextron.domain.crawl.OUTCOME_LABELS`` ("website
  unreachable").
- ``crawled_pages(lead_id)``: the Lead's stored pages, by URL, each a ``StoredPage`` (whose
  ``text`` is ``None`` once purged).
- ``ats_links(lead_id)``: the ATS board links found on the Lead's website, by URL.
"""

from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research.crawl import StoredAtsLink, StoredCrawlRun, StoredPage
from nextron.domain.ids import is_ulid

__all__ = [
    "StoredAtsLink",
    "StoredCrawlRun",
    "StoredPage",
    "ats_links",
    "crawl_run",
    "crawled_pages",
    "latest_crawl_run",
]


def crawl_run(run_id: str) -> StoredCrawlRun | None:
    """The crawl run ``run_id``, or ``None``."""
    return crawl_repo.run(run_id) if is_ulid(run_id) else None


def latest_crawl_run(lead_id: str) -> StoredCrawlRun | None:
    """The Lead's crawl run started last, or ``None``."""
    return crawl_repo.latest_run(lead_id) if is_ulid(lead_id) else None


def crawled_pages(lead_id: str) -> tuple[StoredPage, ...]:
    """The Lead's stored pages, by URL."""
    return crawl_repo.pages(lead_id) if is_ulid(lead_id) else ()


def ats_links(lead_id: str) -> tuple[StoredAtsLink, ...]:
    """The Lead's ATS board links, by URL."""
    return crawl_repo.ats_links(lead_id) if is_ulid(lead_id) else ()
