"""The ``RendererPort`` (FR-10, AD-9, AD-18, Story 2.6): a page rendered in a browser, every
request it makes fetched inside the egress filter.

``nextron.adapters.renderer.PlaywrightRenderer`` implements it; tests use
``tests.fakes.renderer.ScriptedRenderer``.

**``render(url, *, site, min_interval_s) -> RenderedPage(url, final_url, text, links,
html, mailto)``** loads ``url`` in a fresh browser, lets its scripts run, and returns what the
page then shows: always a 2xx HTML page. ``site`` is ``None`` or the registrable domain every
main-frame navigation must stay on, as in ``CrawlerPort.fetch_page``; ``min_interval_s`` paces
the page's own document as the crawler paces a page. ``final_url`` is where the document was
fetched from after redirects; ``text`` is the rendered DOM's visible text, ``links`` its links
(a tuple of ``nextron.ports.crawler.PageLink``) and ``mailto`` (Story 2.13) the addresses of
its visible ``mailto:`` links (a tuple of ``str``), all read as the crawler reads a page;
``html`` is the rendered DOM. Text, links, HTML and ``mailto`` never show in the ``repr``.

**Failures.** Every failure raises ``AdapterError`` with:

- a ``FETCH_*`` code of ``nextron.ports.crawler``: the main document's own fetch failed (a
  refused first URL included, before anything is launched);
- ``render_failed``: the main document isn't a 2xx HTML page, or anything else failed;
- ``render_timeout`` (retryable): the navigation passed the time limit;
- ``render_unavailable``: the browser couldn't start.
"""

from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from nextron.ports.crawler import PageLink

RENDER_FAILED: Final = "render_failed"
RENDER_TIMEOUT: Final = "render_timeout"
RENDER_UNAVAILABLE: Final = "render_unavailable"
RENDER_CODES: Final = (RENDER_FAILED, RENDER_TIMEOUT, RENDER_UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One rendered page; see the module docstring. Its text, links, HTML and ``mailto``
    addresses never show in the ``repr``."""

    url: str
    final_url: str
    text: str = field(default="", repr=False)
    links: tuple[PageLink, ...] = field(default=(), repr=False)
    html: str = field(default="", repr=False)
    mailto: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) for value in (self.url, self.final_url, self.text, self.html)
        ):
            raise TypeError("url, final_url, text and html must be str")
        if not isinstance(self.links, tuple) or not all(
            isinstance(link, PageLink) for link in self.links
        ):
            raise TypeError("links must be a tuple of PageLink")
        if not isinstance(self.mailto, tuple) or not all(
            isinstance(address, str) for address in self.mailto
        ):
            raise TypeError("mailto must be a tuple of str")


@runtime_checkable
class RendererPort(Protocol):
    """Renders a page in a browser whose every request passes the egress filter."""

    def render(self, url: str, *, site: str | None, min_interval_s: float) -> RenderedPage:
        """Render one page; see the module docstring."""
        ...
