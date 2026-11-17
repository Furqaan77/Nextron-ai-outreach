"""``ScriptedRenderer``: the scripted fake of ``nextron.ports.renderer.RendererPort``.

``ScriptedRenderer(pages=...)`` answers each render by its exact URL: ``pages`` maps a URL to a
``RenderedPage`` (returned), an ``AdapterError`` (raised) or a no-argument callable returning or
raising one of those (so a test can move its clock during a render). The script is kept, so a
URL answers the same every time; a URL with no answer raises ``AssertionError``, so a test can't
make a render it didn't plan.

Before it reads the script, a render makes the real adapter's checks on the first URL, in its
order: ``nextron.domain.crawl.request_interval(min_interval_s)`` (``TypeError``), then the egress
rules and the ``site`` pin as ``tests.fakes.crawler.ScriptedCrawler`` makes them
(``AdapterError(False, "fetch_refused")``). A refused render isn't recorded. ``calls`` records
every render that passed, as ``RenderCall(url, site, min_interval_s)``. It launches, fetches and
meters nothing.

``rendered_page(url, html, *, final_url=None)`` builds an answer the way the adapter does: the
HTML parsed by ``nextron.adapters.crawler.parsing.parse_html`` against the final URL.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from nextron.adapters.crawler.parsing import parse_html
from nextron.domain.crawl import request_interval
from nextron.ports.crawler import FETCH_REFUSED
from nextron.ports.errors import AdapterError
from nextron.ports.renderer import RenderedPage
from tests.fakes.crawler import first_url_refusal

type RenderAnswer = RenderedPage | AdapterError | Callable[[], RenderedPage]


@dataclass(frozen=True, slots=True)
class RenderCall:
    """One render that passed the checks."""

    url: str
    site: str | None
    min_interval_s: float


def rendered_page(url: str, html: str = "", *, final_url: str | None = None) -> RenderedPage:
    """A render's answer: ``html`` parsed as the adapter parses the rendered DOM."""
    landed = url if final_url is None else final_url
    parsed = parse_html(html, landed)
    return RenderedPage(url=url, final_url=landed, text=parsed.text, links=parsed.links, html=html)


class ScriptedRenderer:
    def __init__(self, *, pages: Mapping[str, RenderAnswer] | None = None) -> None:
        self.pages: dict[str, RenderAnswer] = dict(pages or {})
        self.calls: list[RenderCall] = []

    def render(self, url: str, *, site: str | None, min_interval_s: float) -> RenderedPage:
        request_interval(min_interval_s)
        reason = first_url_refusal(url, site)
        if reason is not None:
            raise AdapterError(False, FETCH_REFUSED, f"the fetch was refused ({reason})")
        self.calls.append(RenderCall(url, site, min_interval_s))
        if url not in self.pages:
            raise AssertionError(f"ScriptedRenderer: no render is scripted for {url}")
        answer = self.pages[url]
        if callable(answer):
            answer = answer()
        if isinstance(answer, AdapterError):
            raise answer
        if not isinstance(answer, RenderedPage):
            raise AssertionError("ScriptedRenderer: an answer must be a RenderedPage")
        return answer

    def urls(self) -> list[str]:
        """The URLs rendered, in order."""
        return [call.url for call in self.calls]
