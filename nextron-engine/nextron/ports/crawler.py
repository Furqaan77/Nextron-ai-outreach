"""The ``CrawlerPort`` (FR-10, FR-49, FR-57, AD-9, AD-18, NFR-6): polite, filtered web fetches.

``nextron.adapters.crawler.HttpCrawler`` implements it; tests use
``tests.fakes.crawler.ScriptedCrawler``. Every hop of every fetch passes the egress rules of
``nextron.domain.fetch_policy`` (the URL, the FR-49 denylist and each resolved address), is paced
per registrable domain and is metered.

**``fetch_robots(url, *, min_interval_s) -> Robots(final_url, status, crawl_delay_s,
allows)``** fetches one robots.txt, following redirects. ``allows`` is set only for a 2xx
answer: ``allows(url)`` says whether ``nextron.domain.crawl.ROBOTS_TOKEN`` may fetch ``url``,
and ``crawl_delay_s`` is the file's crawl delay for it (``None`` when it gives none). For any
other status both are ``None``.

**``fetch_page(url, *, site, min_interval_s) -> FetchedPage(url, final_url, status, media_type,
text, links, html)``** fetches one page, following redirects. ``site`` is ``None`` or a
registrable domain every hop must stay on. A 2xx answer carries its ``media_type`` and its
parsed content: ``text`` (the visible text, ``nextron.domain.crawl.visible_text``), ``links``
(a tuple of ``PageLink(url, text, tag)``: each visible ``a[href]`` and every ``iframe[src]`` and
``script[src]``, absolute, without a fragment, ``tag`` one of ``a``, ``iframe`` or ``script``)
and ``html`` (the decoded document). Any other status has ``text=""``, ``html=""`` and no links.

``min_interval_s`` is the least time between two requests to the same registrable domain; an
adapter never goes under ``nextron.domain.crawl.MIN_INTERVAL_S`` (1 s), whatever it is given.

**Failures.** Every failure raises ``AdapterError`` with one of ``FETCH_CODES``:

- ``fetch_refused``: a hop the egress rules refuse (its reason from
  ``nextron.domain.fetch_policy``), or ``off_site`` (a hop left ``site``);
- ``fetch_dns_failed``, ``fetch_connect_failed``: the name didn't resolve, or nothing answered;
- ``fetch_timeout``: the request took too long; the only retryable one;
- ``fetch_too_many_redirects``, ``fetch_too_large``, ``fetch_unsupported_type``: more than
  ``MAX_REDIRECTS`` redirects, a body over its limit, or a 2xx media type the fetch doesn't
  take;
- ``fetch_failed``: anything else.

No value here shows page text, HTML or links in its ``repr``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from nextron.domain.crawl import LINK_TAGS

FETCH_REFUSED: Final = "fetch_refused"
FETCH_DNS_FAILED: Final = "fetch_dns_failed"
FETCH_CONNECT_FAILED: Final = "fetch_connect_failed"
FETCH_TIMEOUT: Final = "fetch_timeout"
FETCH_TOO_MANY_REDIRECTS: Final = "fetch_too_many_redirects"
FETCH_TOO_LARGE: Final = "fetch_too_large"
FETCH_UNSUPPORTED_TYPE: Final = "fetch_unsupported_type"
FETCH_FAILED: Final = "fetch_failed"
FETCH_CODES: Final = (
    FETCH_REFUSED,
    FETCH_DNS_FAILED,
    FETCH_CONNECT_FAILED,
    FETCH_TIMEOUT,
    FETCH_TOO_MANY_REDIRECTS,
    FETCH_TOO_LARGE,
    FETCH_UNSUPPORTED_TYPE,
    FETCH_FAILED,
)
MIN_STATUS: Final = 100
MAX_STATUS: Final = 599


def _status(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("status must be an int")
    if not MIN_STATUS <= value <= MAX_STATUS:
        raise ValueError("status must be an HTTP status from 100 to 599")


def _is_2xx(status: int) -> bool:
    return 200 <= status < 300


@dataclass(frozen=True, slots=True)
class PageLink:
    """One link on a page: its absolute URL (no fragment), its text and the tag it came from."""

    url: str
    text: str
    tag: str

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not isinstance(self.text, str):
            raise TypeError("a link's url and text must be str")
        if self.tag not in LINK_TAGS:
            raise ValueError("a link's tag must be a, iframe or script")


@dataclass(frozen=True, slots=True)
class Robots:
    """One robots.txt answer; ``allows`` and ``crawl_delay_s`` are set only for a 2xx."""

    final_url: str
    status: int
    crawl_delay_s: float | None = None
    allows: Callable[[str], bool] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.final_url, str):
            raise TypeError("final_url must be a str")
        _status(self.status)
        if not _is_2xx(self.status) and (self.allows is not None or self.crawl_delay_s is not None):
            raise ValueError("only a 2xx robots.txt has rules or a crawl delay")
        if self.allows is not None and not callable(self.allows):
            raise TypeError("allows must be callable or None")


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """One fetched page; see the module docstring. Its text, links and HTML never show in the
    ``repr``."""

    url: str
    final_url: str
    status: int
    media_type: str
    text: str = field(default="", repr=False)
    links: tuple[PageLink, ...] = field(default=(), repr=False)
    html: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in (self.url, self.final_url)):
            raise TypeError("url and final_url must be str")
        _status(self.status)
        if not all(isinstance(value, str) for value in (self.media_type, self.text, self.html)):
            raise TypeError("media_type, text and html must be str")
        if not isinstance(self.links, tuple) or not all(
            isinstance(link, PageLink) for link in self.links
        ):
            raise TypeError("links must be a tuple of PageLink")
        if not _is_2xx(self.status) and (self.text or self.html or self.links):
            raise ValueError("a page that isn't 2xx has no text, HTML or links")

    @property
    def ok(self) -> bool:
        """Whether the page answered 2xx."""
        return _is_2xx(self.status)


@runtime_checkable
class CrawlerPort(Protocol):
    """Fetches robots.txt files and pages inside the egress filter."""

    def fetch_robots(self, url: str, *, min_interval_s: float) -> Robots:
        """Fetch one robots.txt; see the module docstring."""
        ...

    def fetch_page(self, url: str, *, site: str | None, min_interval_s: float) -> FetchedPage:
        """Fetch one page; see the module docstring."""
        ...
