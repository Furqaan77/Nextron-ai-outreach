"""``ScriptedCrawler``: the scripted fake of ``nextron.ports.crawler.CrawlerPort``.

``ScriptedCrawler(robots=..., pages=...)`` answers each fetch by its exact URL: ``robots`` maps a
robots.txt URL, and ``pages`` a page URL, to a ``Robots`` or ``FetchedPage`` (returned), an
``AdapterError`` (raised) or a no-argument callable returning or raising one of those (so a test
can move its clock during a fetch). The script is kept, so a URL answers the same every time;
a URL with no answer raises ``AssertionError``, so a test can't make a call it didn't plan.

Before it reads the script, a fetch makes the real adapter's checks on the first URL, in its
order, so a service test can't pass with a fetch the adapter would refuse:

1. ``nextron.domain.crawl.request_interval(min_interval_s)`` (``TypeError``);
2. ``nextron.domain.fetch_policy.url_refusal(url)``: ``AdapterError(False, "fetch_refused")``;
3. for ``fetch_page`` with a ``site``, the URL's registrable domain must be ``site``, else the
   same refusal (``off_site``).

A refused fetch isn't recorded. ``calls`` records every fetch that passed, as
``CrawlCall(kind, url, site, min_interval_s)`` (``kind`` ``robots`` or ``page``). Unlike the real
adapter it resolves, paces and meters nothing, and it doesn't check a scripted final URL.

``robots_txt(url, text, *, status=200)`` and ``html_page(url, html, *, status=200,
final_url=None, media_type="text/html")`` build answers the way the adapter does: protego's rules
for a 2xx robots.txt, and ``nextron.adapters.crawler.parsing.parse_html`` for a 2xx page.
"""

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from protego import Protego

from nextron.adapters.crawler.parsing import parse_html
from nextron.domain.crawl import ROBOTS_TOKEN, request_interval
from nextron.domain.fetch_policy import OFF_SITE, check_url, url_refusal
from nextron.domain.identity import IdentityError, registrable_domain
from nextron.ports.crawler import FETCH_REFUSED, FetchedPage, Robots
from nextron.ports.errors import AdapterError

type RobotsAnswer = Robots | AdapterError | Callable[[], Robots]
type PageAnswer = FetchedPage | AdapterError | Callable[[], FetchedPage]


@dataclass(frozen=True, slots=True)
class CrawlCall:
    """One fetch that passed the checks."""

    kind: str
    url: str
    site: str | None
    min_interval_s: float


def _can_fetch(parser: Protego, url: str) -> bool:
    return bool(parser.can_fetch(url, ROBOTS_TOKEN))


def robots_txt(url: str, text: str, *, status: int = 200) -> Robots:
    """A robots.txt answer: ``text``'s rules and crawl delay for a 2xx, nothing otherwise."""
    if not 200 <= status < 300:
        return Robots(final_url=url, status=status)
    parser = Protego.parse(text)
    delay = parser.crawl_delay(ROBOTS_TOKEN)
    return Robots(
        final_url=url,
        status=status,
        crawl_delay_s=None if delay is None else float(delay),
        allows=functools.partial(_can_fetch, parser),
    )


def html_page(
    url: str,
    html: str = "",
    *,
    status: int = 200,
    final_url: str | None = None,
    media_type: str = "text/html",
) -> FetchedPage:
    """A page answer: ``html`` parsed as the adapter parses it for a 2xx, nothing otherwise."""
    landed = url if final_url is None else final_url
    if not 200 <= status < 300:
        return FetchedPage(url=url, final_url=landed, status=status, media_type="")
    parsed = parse_html(html, landed)
    return FetchedPage(
        url=url,
        final_url=landed,
        status=status,
        media_type=media_type,
        text=parsed.text,
        links=parsed.links,
        html=html,
    )


def first_url_refusal(url: str, site: str | None) -> str | None:
    """Why the real adapter would refuse ``url`` as a first URL pinned to ``site``, or
    ``None``."""
    reason = url_refusal(url)
    if reason is not None or site is None:
        return reason
    try:
        found = registrable_domain(check_url(url).host)
    except IdentityError:
        return OFF_SITE
    return None if found == site else OFF_SITE


class ScriptedCrawler:
    def __init__(
        self,
        *,
        robots: Mapping[str, RobotsAnswer] | None = None,
        pages: Mapping[str, PageAnswer] | None = None,
    ) -> None:
        self.robots: dict[str, RobotsAnswer] = dict(robots or {})
        self.pages: dict[str, PageAnswer] = dict(pages or {})
        self.calls: list[CrawlCall] = []

    def fetch_robots(self, url: str, *, min_interval_s: float) -> Robots:
        answer = self._answer("robots", self.robots, url, None, min_interval_s)
        if not isinstance(answer, Robots):
            raise AssertionError("ScriptedCrawler: a robots.txt answer must be a Robots")
        return answer

    def fetch_page(self, url: str, *, site: str | None, min_interval_s: float) -> FetchedPage:
        answer = self._answer("page", self.pages, url, site, min_interval_s)
        if not isinstance(answer, FetchedPage):
            raise AssertionError("ScriptedCrawler: a page answer must be a FetchedPage")
        return answer

    def _answer(
        self,
        kind: str,
        script: Mapping[str, object],
        url: str,
        site: str | None,
        min_interval_s: float,
    ) -> object:
        request_interval(min_interval_s)
        reason = first_url_refusal(url, site)
        if reason is not None:
            raise AdapterError(False, FETCH_REFUSED, f"the fetch was refused ({reason})")
        self.calls.append(CrawlCall(kind, url, site, min_interval_s))
        if url not in script:
            raise AssertionError(f"ScriptedCrawler: no {kind} answer is scripted for {url}")
        answer = script[url]
        if callable(answer):
            answer = answer()
        if isinstance(answer, AdapterError):
            raise answer
        return answer

    def urls(self, kind: str = "page") -> list[str]:
        """The URLs fetched, in order, of one kind."""
        return [call.url for call in self.calls if call.kind == kind]
