"""``PlaywrightRenderer`` (``nextron.adapters.renderer``, Story 2.6) on the fake Playwright objects
of ``tests/fakes/browser.py`` and the real ``HttpCrawler`` on ``httpx2.MockTransport``, a fake
resolver and a fake monotonic clock: no browser is launched and nothing reaches the network.

It covers the context, the routes, the sockets and closing; the request handler's matrix (method,
type, frame, media type, private and denylisted hosts, redirects, off-site navigation, the caps
and the deadline); the main document's failures; Playwright's errors; pacing and metering; and
``default_browser``'s launch, with ``sync_playwright`` replaced after ``allow_live_constructor``.
"""

import inspect
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest
from playwright import sync_api
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import nextron.adapters.renderer as renderer_module
from nextron.adapters.crawler import HttpCrawler
from nextron.adapters.renderer import CHROMIUM_ARGS, DEAD_PROXY, PlaywrightRenderer
from nextron.domain.budget import ApiCall
from nextron.domain.crawl import USER_AGENT
from nextron.domain.fetch_policy import MAX_PAGE_BYTES
from nextron.ports.errors import AdapterError
from nextron.ports.renderer import RenderedPage, RendererPort
from tests.fakes.browser import (
    CHILD,
    RAISES,
    Aborted,
    FakeBrowser,
    FakeBrowsers,
    FakePlaywright,
    MAIN,
    FakeSite,
    Fulfilled,
    GotoCall,
    Sub,
)
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import allow_live_constructor

PUBLIC = "93.184.216.34"
SITE = "smith-law.co.uk"
HOME = f"https://{SITE}/"
CDN = "cdn.example.com"
LEAK = "Qz7Lm2Vx9Kp4Tw8Nr3Hs"  # text in a URL or a body that must never reach an error
SHELL = "<html><head><script src='https://cdn.example.com/app.js'></script></head><body></body>"
RENDERED = (
    "<html><body><h1>Smith Law LLP</h1><p>"
    + "We help families and businesses. " * 40
    + "</p><iframe src='https://calendly.com/smith-law/consult'></iframe>"
    + "<a href='/team'>Our team</a><a href='tel:+442079460958'>Call</a></body></html>"
)

type Answer = httpx2.Response | BaseException | Callable[[httpx2.Request], httpx2.Response]


@dataclass
class Web:
    """A fake web: answers by ``(Host header, path)``, else ``default``; records every
    request."""

    answers: dict[tuple[str, str], Answer] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)
    default: Answer | None = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = (request.headers["host"], request.url.raw_path.decode("ascii").split("?")[0])
        answer = self.answers.get(key, self.default)
        if answer is None:
            return httpx2.Response(404, headers={"content-type": "text/html"}, text="missing")
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(request)
        return answer

    def on(self, host: str, path: str, answer: Answer) -> None:
        self.answers[(host, path)] = answer

    def hosts(self) -> list[str]:
        return [request.headers["host"] for request in self.requests]


@dataclass
class Ticks:
    """A fake monotonic clock; ``sleep`` advances it and records each wait."""

    now: float = 1000.0
    slept: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 6))
        self.now += seconds


@dataclass
class Resolver:
    addresses: dict[str, Sequence[str]] = field(default_factory=dict)
    lookups: list[str] = field(default_factory=list)

    def __call__(self, host: str, port: int) -> Sequence[str]:
        self.lookups.append(host)
        return self.addresses.get(host, (PUBLIC,))


@dataclass
class World:
    web: Web
    resolver: Resolver
    ticks: Ticks
    meter: InMemoryUsageMeter
    crawler: HttpCrawler
    site: FakeSite
    browsers: FakeBrowsers
    renderer: PlaywrightRenderer

    def render(self, url: str = HOME, site: str | None = SITE) -> RenderedPage:
        return self.renderer.render(url, site=site, min_interval_s=1.0)

    def failure(self, url: str = HOME, site: str | None = SITE) -> AdapterError:
        with pytest.raises(AdapterError) as error:
            self.render(url, site)
        return error.value

    @property
    def routes(self) -> list[Any]:
        return self.browsers.context.handled

    def outcome(self, url: str) -> Fulfilled | Aborted | None:
        [route] = [route for route in self.routes if route.request.url == url]
        return route.outcome


def page(
    body: str, status: int = 200, ctype: str = "text/html; charset=utf-8", **headers: str
) -> Any:
    return httpx2.Response(status, headers={"content-type": ctype, **headers}, text=body)


def redirect(location: str) -> Any:
    return httpx2.Response(302, headers={"location": location})


def script(body: str = "console.log(1)", ctype: str = "application/javascript") -> Any:
    return httpx2.Response(200, headers={"content-type": ctype}, text=body)


@pytest.fixture
def world() -> World:
    web, resolver, ticks, meter = Web(), Resolver(), Ticks(), InMemoryUsageMeter()
    crawler = HttpCrawler(
        meter=meter,
        http=httpx2.Client(transport=httpx2.MockTransport(web)),
        resolve=resolver,
        own_addresses=frozenset(),
        monotonic=ticks.monotonic,
        sleep=ticks.sleep,
    )
    site = FakeSite()
    browsers = FakeBrowsers(site)
    renderer = PlaywrightRenderer(
        meter=meter, fetcher=crawler, browser=browsers, monotonic=ticks.monotonic
    )
    web.on(SITE, "/", page(SHELL))
    web.on(CDN, "/app.js", script())
    return World(web, resolver, ticks, meter, crawler, site, browsers, renderer)


def shown(error: BaseException) -> str:
    return "\n".join([str(error), repr(error), *traceback.format_exception(error)])


def test_it_is_a_renderer_port(world: World) -> None:
    assert isinstance(world.renderer, RendererPort)


# A render ---------------------------------------------------------------------------------------


def test_a_render_returns_the_rendered_dom_parsed_against_the_final_url(world: World) -> None:
    world.web.on(SITE, "/", httpx2.Response(301, headers={"location": "/home"}))
    world.web.on(SITE, "/home", page(SHELL))
    world.site.dom = RENDERED

    rendered = world.render()

    assert (rendered.url, rendered.final_url) == (HOME, f"https://{SITE}/home")
    assert rendered.text.startswith("Smith Law LLP\nWe help families")
    assert len(rendered.text) > 500
    assert rendered.html == RENDERED
    # relative links resolve against the final URL; a tel: link isn't a web link
    assert [(link.tag, link.url) for link in rendered.links] == [
        ("a", f"https://{SITE}/team"),
        ("iframe", "https://calendly.com/smith-law/consult"),
    ]
    assert "We help" not in repr(rendered)


def test_the_context_routes_every_request_closes_every_socket_and_is_closed(world: World) -> None:
    world.site.sockets = ["wss://smith-law.co.uk/live", "wss://cdn.example.com/chat"]

    world.render()

    [browser] = world.browsers.browsers
    context = world.browsers.context
    assert context.options == {
        "accept_downloads": False,
        "service_workers": "block",
        "storage_state": None,
        "user_agent": USER_AGENT,
    }
    assert [pattern for pattern, _ in context.routes] == ["**/*"]
    assert [pattern for pattern, _ in context.socket_routes] == ["**/*"]
    assert [socket.closed for socket in context.sockets] == [True, True]
    assert (context.closed, browser.closed) == (True, True)
    [page_] = context.pages
    assert page_.gotos == [GotoCall(HOME, "domcontentloaded", 20_000.0)]
    assert page_.waits == [("load", 20_000.0), ("networkidle", 5_000.0)]


def test_one_browser_per_render(world: World) -> None:
    world.render()
    world.render()

    assert [browser.closed for browser in world.browsers.browsers] == [True, True]


def test_the_main_document_is_fulfilled_with_a_rebuilt_type_the_cors_headers_and_no_cookie(
    world: World,
) -> None:
    world.web.on(
        SITE,
        "/",
        page(
            SHELL,
            ctype="text/html; charset=ISO-8859-1; boundary=x",
            **{
                "set-cookie": "session=abc",
                "access-control-allow-origin": "*",
                "access-control-allow-credentials": "true",
                "x-frame-options": "DENY",
            },
        ),
    )

    world.render()

    outcome = world.outcome(HOME)
    assert isinstance(outcome, Fulfilled)
    assert outcome.status == 200
    assert outcome.headers == {
        "content-type": "text/html; charset=ISO-8859-1",
        "access-control-allow-origin": "*",
        "access-control-allow-credentials": "true",
    }
    assert outcome.body == SHELL.encode()


def test_no_header_of_the_browsers_is_forwarded_and_each_type_sends_its_accept(
    world: World,
) -> None:
    world.web.default = script()
    world.site.requests = [
        Sub(f"https://{CDN}/a.js", "script"),
        Sub(f"https://{CDN}/a.css", "stylesheet"),
        Sub(f"https://{CDN}/api", "xhr"),
    ]
    world.web.on(CDN, "/a.css", script(ctype="text/css"))
    world.web.on(CDN, "/api", script(ctype="application/json"))

    world.render()

    sent = [
        (request.headers["user-agent"], request.headers["accept"]) for request in world.web.requests
    ]
    assert sent == [
        (USER_AGENT, "text/html,application/xhtml+xml;q=0.9"),
        (USER_AGENT, "*/*"),
        (USER_AGENT, "text/css,*/*;q=0.1"),
        (USER_AGENT, "application/json,text/plain,*/*;q=0.1"),
    ]
    for request in world.web.requests:
        assert {"cookie", "referer", "origin"}.isdisjoint(request.headers)


# The request handler ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sub", "ctype", "fulfilled"),
    [
        (Sub(f"https://{CDN}/x.js", "script"), "application/javascript", True),
        (Sub(f"https://{CDN}/x.js", "script"), "text/javascript", True),
        (Sub(f"https://{CDN}/x.css", "stylesheet"), "text/css", True),
        (Sub(f"https://{CDN}/x", "xhr"), "application/json", True),
        (Sub(f"https://{CDN}/x", "fetch"), "application/ld+json", True),
        (Sub(f"https://{CDN}/x", "fetch"), "text/plain", True),
        (Sub(f"https://{CDN}/x", "xhr"), "text/html", True),
        (Sub(f"https://{CDN}/x.js", "script"), "text/plain", False),  # not JavaScript
        (Sub(f"https://{CDN}/x.css", "stylesheet"), "text/html", False),
        (Sub(f"https://{CDN}/x", "xhr"), "image/png", False),
        (Sub(f"https://{CDN}/x", "fetch"), "application/octet-stream", False),
    ],
)
def test_a_sub_resource_is_fulfilled_only_with_its_types_media(
    world: World, sub: Sub, ctype: str, fulfilled: bool
) -> None:
    world.web.default = script(ctype=ctype)
    world.site.requests = [sub]

    world.render()

    outcome = world.outcome(sub.url)
    assert isinstance(outcome, Fulfilled) is fulfilled
    assert len(world.web.requests) == 2  # the main document and this one, either way


@pytest.mark.parametrize(
    "sub",
    [
        Sub(f"https://{CDN}/x.js", "script", method="POST"),
        Sub(f"https://{CDN}/x.js", "script", method="HEAD"),
        Sub(f"https://{CDN}/x.png", "image"),
        Sub(f"https://{CDN}/x.woff2", "font"),
        Sub(f"https://{CDN}/x.mp4", "media"),
        Sub(f"https://{CDN}/beacon", "ping"),
        Sub(f"https://{CDN}/events", "eventsource"),
        Sub(f"https://{CDN}/site.webmanifest", "manifest"),
        Sub(f"https://{CDN}/x", "other"),
        Sub(f"https://{CDN}/x", "texttrack"),
        Sub(f"https://{CDN}/frame", "document", frame=CHILD, navigation=True),  # an iframe
        Sub(f"https://{SITE}/popup", "document", frame=RAISES, navigation=True),  # a popup
        Sub(f"https://{SITE}/x", "document", navigation=False),
    ],
)
def test_disallowed_traffic_is_aborted_with_no_request(world: World, sub: Sub) -> None:
    world.web.default = script()
    world.site.requests = [sub]

    world.render()

    assert world.outcome(sub.url) == Aborted("blockedbyclient")
    assert len(world.web.requests) == 1  # the main document only
    assert world.resolver.lookups == [SITE]


@pytest.mark.parametrize(
    ("url", "lookups"),
    [
        ("https://intranet.example.com/x.js", ["intranet.example.com"]),  # resolves to 10.0.0.5
        ("http://127.0.0.1/x.js", []),
        ("https://10.0.0.5/x.js", []),
        ("https://cdn.example.com:8080/x.js", []),
        ("https://www.linkedin.com/x.js", []),
        ("ftp://cdn.example.com/x.js", []),
    ],
)
def test_a_private_or_denylisted_sub_resource_is_aborted_with_no_request_to_it(
    world: World, url: str, lookups: list[str]
) -> None:
    world.resolver.addresses["intranet.example.com"] = ("10.0.0.5",)
    world.site.requests = [Sub(url, "script")]

    world.render()

    assert world.outcome(url) == Aborted("blockedbyclient")
    assert world.web.hosts() == [SITE]
    assert world.resolver.lookups == [SITE, *lookups]


@pytest.mark.parametrize(
    "location",
    ["https://maps.google.com/?q=smith", "https://intranet.example.com/x.js", "http://127.0.0.1/"],
)
def test_a_redirect_to_a_denylisted_or_private_host_is_aborted_after_one_request(
    world: World, location: str
) -> None:
    world.resolver.addresses["intranet.example.com"] = ("10.0.0.5",)
    world.web.on(CDN, "/x.js", httpx2.Response(302, headers={"location": location}))
    world.site.requests = [Sub(f"https://{CDN}/x.js", "script")]

    world.render()

    assert world.outcome(f"https://{CDN}/x.js") == Aborted("blockedbyclient")
    assert world.web.hosts() == [SITE, CDN]


# FR-49 (Story 10.1): a LinkedIn badge or an Instagram embed on a firm's page is refused by the
# handler itself, before any counter, lookup, fetch or meter call.
PLATFORM_URLS = (
    "https://platform.linkedin.com/badges/js/profile.js",
    "https://static.licdn.com/sc/h/badge.png",
    "https://www.instagram.com/embed.js",
    "https://scontent.cdninstagram.com/v/t51/photo.jpg",
)
PLATFORM_TYPES = (
    ("image", MAIN, False, "GET"),
    ("script", MAIN, False, "GET"),
    ("stylesheet", MAIN, False, "GET"),
    ("font", MAIN, False, "GET"),
    ("media", MAIN, False, "GET"),
    ("ping", MAIN, False, "POST"),
    ("document", CHILD, True, "GET"),  # an iframe
    ("xhr", MAIN, False, "GET"),
    ("xhr", MAIN, False, "POST"),
    ("fetch", MAIN, False, "GET"),
)


@pytest.mark.parametrize("url", PLATFORM_URLS)
@pytest.mark.parametrize(("kind", "frame", "navigation", "method"), PLATFORM_TYPES)
def test_a_platform_sub_resource_is_aborted_with_no_lookup_fetch_or_metering(
    world: World, url: str, kind: str, frame: str, navigation: bool, method: str
) -> None:
    world.site.requests = [Sub(url, kind, method=method, frame=frame, navigation=navigation)]

    world.render()

    assert world.outcome(url) == Aborted("blockedbyclient")
    assert world.web.hosts() == [SITE]
    assert world.resolver.lookups == [SITE]
    assert [call.operation for call in world.meter.calls] == ["render"]  # the main document


@pytest.mark.parametrize("url", PLATFORM_URLS)
def test_a_later_main_frame_navigation_to_a_platform_is_aborted_in_place(
    world: World, url: str
) -> None:
    world.site.requests = [Sub(url, "document", navigation=True)]

    rendered = world.render()

    # ERR_ABORTED leaves the firm's page in place, as for any refused later navigation
    assert world.outcome(url) == Aborted("aborted")
    assert rendered.final_url == HOME
    assert world.web.hosts() == [SITE]
    assert world.resolver.lookups == [SITE]
    assert [call.operation for call in world.meter.calls] == ["render"]


def test_platform_requests_take_nothing_from_the_request_cap(world: World) -> None:
    """100 denied requests, then 99 allowed scripts: all 99 are fulfilled, so the denied ones
    took nothing from ``MAX_REQUESTS`` (100, the main document included)."""
    world.web.default = script()
    denied = [Sub(PLATFORM_URLS[index % 4] + f"?n={index}", "script") for index in range(100)]
    allowed = [Sub(f"https://{CDN}/s{index}.js", "script") for index in range(99)]
    world.site.requests = [*denied, *allowed]

    world.render()

    assert all(world.outcome(sub.url) == Aborted("blockedbyclient") for sub in denied)
    assert all(isinstance(world.outcome(sub.url), Fulfilled) for sub in allowed)
    assert len(world.web.requests) == 100


def test_the_handler_refuses_a_platform_url_even_if_the_fetcher_would_not(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is the handler's own: a platform request is never handed to the fetcher at
    all, so it doesn't rest on the crawler's denylist (which the real ``fetch_resource`` still
    applies behind this recorder)."""
    asked: list[str] = []
    fetch = world.crawler.fetch_resource

    def recording(url: str, **kwargs: Any) -> Any:
        asked.append(url)
        return fetch(url, **kwargs)

    monkeypatch.setattr(world.crawler, "fetch_resource", recording)
    world.site.requests = [Sub(url, "script") for url in PLATFORM_URLS]

    world.render()

    assert asked == [HOME]


def test_an_off_site_navigation_is_aborted_and_a_same_site_one_is_paced(world: World) -> None:
    world.web.on(SITE, "/next", page("<p>next</p>"))
    world.site.requests = [
        Sub("https://other-firm.com/", "document", navigation=True),
        Sub(f"https://www.{SITE}/next", "document", navigation=True),
    ]
    world.web.on(f"www.{SITE}", "/next", page("<p>next</p>"))

    world.render()

    # a refused later navigation is ERR_ABORTED, which leaves the current document in place
    assert world.outcome("https://other-firm.com/") == Aborted("aborted")
    assert isinstance(world.outcome(f"https://www.{SITE}/next"), Fulfilled)
    assert world.web.hosts() == [SITE, f"www.{SITE}"]
    assert world.ticks.slept == [1.0]  # the second document waited for the site's slot


def test_at_most_six_main_frame_navigations(world: World) -> None:
    world.web.default = page("<p>again</p>")
    world.site.requests = [
        Sub(f"https://{SITE}/p{index}", "document", navigation=True) for index in range(6)
    ]

    world.render()

    outcomes = [world.outcome(f"https://{SITE}/p{index}") for index in range(6)]
    assert [isinstance(outcome, Fulfilled) for outcome in outcomes] == [True] * 5 + [False]
    assert outcomes[-1] == Aborted("aborted")
    assert len(world.web.requests) == 6


def test_a_later_navigation_whose_fetch_fails_is_aborted_and_the_page_is_kept(
    world: World,
) -> None:
    world.web.on(SITE, "/gone", httpx2.ConnectError("refused"))
    world.site.requests = [
        Sub(f"https://{SITE}/gone", "document", navigation=True),
        Sub(f"https://{SITE}/frame", "document", frame=CHILD, navigation=True),
    ]
    world.site.dom = RENDERED

    rendered = world.render()

    assert world.outcome(f"https://{SITE}/gone") == Aborted("aborted")
    assert world.outcome(f"https://{SITE}/frame") == Aborted("blockedbyclient")  # not main
    assert rendered.final_url == HOME


def test_a_main_frame_left_on_chromes_error_page_is_render_failed(world: World) -> None:
    world.site.main_frame_url = "chrome-error://chromewebdata/"
    world.site.dom = RENDERED

    error = world.failure()

    assert (error.code, error.retryable) == ("render_failed", False)


def test_a_client_side_redirect_that_interrupts_goto_is_read_as_usual(world: World) -> None:
    world.web.on(SITE, "/en/", page("<p>en</p>"))
    world.site.requests = [Sub(f"https://{SITE}/en/", "document", navigation=True)]
    world.site.goto_error = PlaywrightError("Navigation interrupted by another navigation")
    world.site.dom = RENDERED

    rendered = world.render()

    assert rendered.final_url == f"https://{SITE}/en/"
    assert len(rendered.text) > 500
    [page_] = world.browsers.context.pages
    assert [state for state, _ in page_.waits] == ["load", "networkidle"]


@pytest.mark.parametrize(
    "requests",
    [
        [],  # no second document
        [Sub("https://other-firm.com/", "document", navigation=True)],  # refused
    ],
)
def test_goto_failing_without_a_fulfilled_redirect_is_render_failed(
    world: World, requests: list[Sub]
) -> None:
    world.site.requests = requests
    world.site.goto_error = PlaywrightError("Navigation interrupted by another navigation")

    assert world.failure().code == "render_failed"


def test_a_redirect_to_a_page_that_isnt_2xx_is_render_failed(world: World) -> None:
    world.web.on(SITE, "/en/", page("gone", status=404))
    world.site.requests = [Sub(f"https://{SITE}/en/", "document", navigation=True)]
    world.site.goto_error = PlaywrightError("Navigation interrupted by another navigation")

    assert world.failure().code == "render_failed"


def test_a_dom_over_the_page_cap_is_render_failed(world: World) -> None:
    world.site.dom = "<p>" + "x" * MAX_PAGE_BYTES + "</p>"

    error = world.failure()

    assert (error.code, error.retryable) == ("render_failed", False)


def test_a_session_past_its_limit_is_the_retryable_render_timeout(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer_module, "SESSION_LIMIT_S", 0.2)
    release = threading.Event()

    def busy_page(body: bytes) -> str:  # a script that never yields: content() never returns
        release.wait(10)
        return RENDERED

    world.site.dom = busy_page

    error = world.failure()

    assert (error.code, error.retryable) == ("render_timeout", True)
    release.set()  # the abandoned worker ends and closes its browser
    for _ in range(200):
        if world.browsers.browsers[0].closed:
            break
        time.sleep(0.01)
    assert world.browsers.browsers[0].closed


def test_the_101st_request_is_aborted(world: World) -> None:
    world.web.default = script()
    world.site.requests = [Sub(f"https://{CDN}/s{index}.js", "script") for index in range(100)]

    world.render()

    fulfilled = [route for route in world.routes if isinstance(route.outcome, Fulfilled)]
    assert len(fulfilled) == 100  # the main document and 99 scripts
    assert world.outcome(f"https://{CDN}/s99.js") == Aborted("blockedbyclient")
    assert len(world.web.requests) == 100


def test_a_render_reads_at_most_its_byte_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer_module, "MAX_BYTES", len(SHELL) + 25)
    world.web.default = script("x" * 20)
    world.site.requests = [Sub(f"https://{CDN}/s{index}.js", "script") for index in range(3)]

    world.render()

    assert [
        isinstance(world.outcome(f"https://{CDN}/s{index}.js"), Fulfilled) for index in range(3)
    ] == [True, False, False]
    assert len(world.web.requests) == 3  # the third is refused before it is fetched


def test_nothing_is_fetched_after_the_deadline(world: World) -> None:
    def slow_script(request: httpx2.Request) -> httpx2.Response:
        world.ticks.now += 21.0
        return script()

    world.web.on(CDN, "/slow.js", slow_script)
    world.site.requests = [Sub(f"https://{CDN}/slow.js"), Sub(f"https://{CDN}/late.js")]

    world.render()

    assert world.outcome(f"https://{CDN}/slow.js") == Aborted("blockedbyclient")  # timed out
    assert world.outcome(f"https://{CDN}/late.js") == Aborted("blockedbyclient")
    assert world.web.hosts() == [SITE, CDN]
    assert [page_.waits for page_ in world.browsers.context.pages] == [[]]  # no time left


def test_the_waits_get_the_time_left_and_their_timeouts_are_ignored(world: World) -> None:
    world.site.on_goto = lambda: setattr(world.ticks, "now", world.ticks.now + 16.0)
    world.site.load_errors = {
        "load": PlaywrightTimeoutError("load"),
        "networkidle": PlaywrightTimeoutError("idle"),
    }
    world.site.dom = RENDERED

    rendered = world.render()

    [page_] = world.browsers.context.pages
    assert page_.waits == [("load", 4_000.0), ("networkidle", 4_000.0)]
    assert len(rendered.text) > 500


# The main document ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "site", "reason"),
    [
        ("https://www.linkedin.com/company/smith-law", None, "denylisted"),
        ("https://maps.google.com/?q=smith", SITE, "denylisted"),
        ("https://other-firm.com/", SITE, "off_site"),
        ("https://smith-law.co.uk:8080/", SITE, "port"),
        ("https://10.0.0.5/", None, "ip_host"),
    ],
)
def test_a_refused_first_url_launches_nothing(
    world: World, url: str, site: str | None, reason: str
) -> None:
    error = world.failure(url, site)

    assert (error.code, error.retryable) == ("fetch_refused", False)
    assert f"({reason})" in error.message
    assert (world.browsers.browsers, world.web.requests, world.resolver.lookups) == ([], [], [])


@pytest.mark.parametrize(
    ("answer", "addresses", "code", "requests"),
    [
        (page(SHELL), ("10.0.0.5",), "fetch_refused", 0),
        (page("{}", ctype="application/json"), (PUBLIC,), "fetch_unsupported_type", 1),
        (redirect("https://other-firm.com/"), (PUBLIC,), "fetch_refused", 1),
        (redirect("https://maps.google.com/"), (PUBLIC,), "fetch_refused", 1),
        (httpx2.ConnectError("refused"), (PUBLIC,), "fetch_connect_failed", 1),
    ],
)
def test_a_main_document_whose_fetch_failed_is_raised_as_its_own_error(
    world: World, answer: Answer, addresses: tuple[str, ...], code: str, requests: int
) -> None:
    world.resolver.addresses[SITE] = addresses
    world.web.on(SITE, "/", answer)

    error = world.failure()

    assert error.code == code
    assert len(world.web.requests) == requests
    assert world.browsers.browsers[0].closed


def test_a_main_document_timeout_is_the_retryable_fetch_timeout(world: World) -> None:
    world.web.on(SITE, "/", httpx2.ReadTimeout("slow"))

    error = world.failure()

    assert (error.code, error.retryable) == ("fetch_timeout", True)


@pytest.mark.parametrize("status", [404, 410, 500, 503])
def test_a_main_document_that_isnt_2xx_is_render_failed(world: World, status: int) -> None:
    world.web.on(SITE, "/", page("gone", status=status))

    error = world.failure()

    assert (error.code, error.retryable) == ("render_failed", False)
    assert world.outcome(HOME) == Fulfilled(status, {}, b"")


# Playwright's errors ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "code", "retryable"),
    [
        ({"goto_error": PlaywrightTimeoutError(f"Timeout {LEAK}")}, "render_timeout", True),
        ({"goto_error": PlaywrightError(f"net::ERR {LEAK}")}, "render_failed", False),
        ({"load_errors": {"load": PlaywrightError(f"closed {LEAK}")}}, "render_failed", False),
        ({"load_errors": {"networkidle": RuntimeError(LEAK)}}, "render_failed", False),
    ],
)
def test_playwrights_errors_become_render_codes_with_fixed_messages(
    world: World, change: dict[str, Any], code: str, retryable: bool
) -> None:
    for name, value in change.items():
        setattr(world.site, name, value)

    error = world.failure(f"https://{SITE}/{LEAK}")

    assert (error.code, error.retryable) == (code, retryable)
    assert LEAK not in shown(error)
    assert error.__cause__ is None
    assert world.browsers.browsers[0].closed


def test_a_dom_that_cant_be_read_is_render_failed(world: World) -> None:
    def broken(body: bytes) -> str:
        raise PlaywrightError(f"navigating {LEAK}")

    world.site.dom = broken

    error = world.failure()

    assert error.code == "render_failed"
    assert LEAK not in shown(error)


def test_a_context_that_cant_be_made_is_render_failed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: FakeBrowser, **options: Any) -> Any:
        raise PlaywrightError(f"Browser closed {LEAK}")

    monkeypatch.setattr(FakeBrowser, "new_context", refuse)

    error = world.failure()

    assert error.code == "render_failed"
    assert world.browsers.browsers[0].closed


@pytest.mark.parametrize(
    "start_error",
    [PlaywrightError(f"Executable doesn't exist at {LEAK}"), OSError(LEAK), RuntimeError(LEAK)],
)
def test_a_browser_that_cant_start_is_render_unavailable(
    world: World, start_error: BaseException
) -> None:
    world.renderer = PlaywrightRenderer(
        meter=world.meter,
        fetcher=world.crawler,
        browser=FakeBrowsers(start_error=start_error),
        monotonic=world.ticks.monotonic,
    )

    error = world.failure()

    assert (error.code, error.retryable) == ("render_unavailable", False)
    assert LEAK not in shown(error)
    assert world.web.requests == []


def test_closing_errors_never_fail_a_render(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fakes import browser as fake_browser  # noqa: PLC0415

    def broken_close(self: Any) -> None:
        raise PlaywrightError("Target closed")

    monkeypatch.setattr(fake_browser.FakeContext, "close", broken_close)

    assert world.render().final_url == HOME


@pytest.mark.parametrize("goto_error", [None, PlaywrightError(f"net::ERR {LEAK}")])
def test_a_browser_whose_close_fails_never_changes_the_outcome(
    world: World, goto_error: BaseException | None
) -> None:
    world.site.goto_error = goto_error
    world.renderer = PlaywrightRenderer(
        meter=world.meter,
        fetcher=world.crawler,
        browser=FakeBrowsers(world.site, exit_error=PlaywrightError("Target closed")),
        monotonic=world.ticks.monotonic,
    )

    if goto_error is None:
        assert world.render().final_url == HOME
    else:
        error = world.failure()
        assert (error.code, error.retryable) == ("render_failed", False)
        assert LEAK not in shown(error)


def test_every_playwright_call_the_renderer_makes_exists_on_the_real_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline conformance: the fakes accept any keyword, so the real ``playwright.sync_api``
    classes are checked to have every method, property and keyword the renderer uses (and the
    fakes recorded). Nothing is launched."""
    allow_live_constructor(monkeypatch, "nextron.adapters.renderer", "default_browser")
    playwright = FakePlaywright()
    monkeypatch.setattr(renderer_module, "sync_playwright", playwright)
    with renderer_module.default_browser() as browser:
        pass
    browsers = FakeBrowsers()
    renderer = PlaywrightRenderer(
        meter=InMemoryUsageMeter(), fetcher=_NoFetch(), browser=browsers
    )
    with pytest.raises(AdapterError):
        renderer.render(HOME, site=SITE, min_interval_s=1.0)  # the context options are recorded
    assert browser is playwright.browser

    def keywords(cls: type, method: str) -> set[str]:
        return set(inspect.signature(getattr(cls, method)).parameters) - {"self"}

    def properties(cls: type) -> set[str]:
        return {name for name, value in vars(cls).items() if isinstance(value, property)}

    uses: dict[tuple[type, str], set[str]] = {
        (sync_api.BrowserType, "launch"): set(playwright.launches[0]),
        (sync_api.Browser, "new_context"): set(browsers.context.options),
        (sync_api.Browser, "close"): set(),
        (sync_api.BrowserContext, "route"): {"url", "handler"},
        (sync_api.BrowserContext, "route_web_socket"): {"url", "handler"},
        (sync_api.BrowserContext, "new_page"): set(),
        (sync_api.BrowserContext, "close"): set(),
        (sync_api.Route, "fulfill"): {"status", "headers", "body"},
        (sync_api.Route, "abort"): {"error_code"},
        (sync_api.Page, "goto"): {"url", "wait_until", "timeout"},
        (sync_api.Page, "wait_for_load_state"): {"state", "timeout"},
        (sync_api.Page, "content"): set(),
        (sync_api.Request, "is_navigation_request"): set(),
        (sync_api.WebSocketRoute, "close"): set(),
    }
    for (cls, method), used in uses.items():
        assert callable(getattr(cls, method)), (cls.__name__, method)
        assert used <= keywords(cls, method), (cls.__name__, method, used)
    assert {"chromium"} <= properties(sync_api.Playwright)
    assert {"main_frame"} <= properties(sync_api.Page)
    assert {"url"} <= properties(sync_api.Frame)
    assert {"method", "url", "resource_type", "frame"} <= properties(sync_api.Request)
    assert issubclass(sync_api.TimeoutError, sync_api.Error)


class _NoFetch:
    """A fetcher that refuses everything (the conformance test needs only the context)."""

    def fetch_resource(self, url: str, **options: Any) -> Any:
        raise AdapterError(False, "fetch_refused", "the fetch was refused (probe)")


# Pacing and metering ----------------------------------------------------------------------------


def test_the_main_document_is_paced_and_sub_resources_are_not(world: World) -> None:
    world.web.on(SITE, "/app.js", script())
    world.site.requests = [Sub(f"https://{SITE}/app.js"), Sub(f"https://{CDN}/app.js")]

    world.render()
    world.render()

    # the second render's document waited a second for the site; no script ever waited
    assert world.ticks.slept == [1.0]
    assert world.web.hosts() == [SITE, SITE, CDN, SITE, SITE, CDN]


def test_every_request_fetched_is_metered_once_as_a_render(world: World) -> None:
    world.resolver.addresses["intranet.example.com"] = ("10.0.0.5",)
    world.site.requests = [
        Sub(f"https://{CDN}/app.js"),
        Sub("https://intranet.example.com/x.js"),  # refused before any request
        Sub(f"https://{CDN}/x.png", "image"),  # aborted, never fetched
    ]

    world.render()

    calls = world.meter.calls
    assert all(isinstance(call, ApiCall) for call in calls)
    assert [(call.provider, call.operation, call.ok) for call in calls] == [
        ("web", "render", True),
        ("web", "render", True),
    ]


# The live constructor ---------------------------------------------------------------------------


def test_default_browser_launches_headless_chromium_that_can_reach_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.renderer", "default_browser")
    playwright = FakePlaywright()
    monkeypatch.setattr(renderer_module, "sync_playwright", playwright)

    with renderer_module.default_browser() as browser:
        assert browser is playwright.browser
        assert playwright.events == ["start"]

    assert playwright.launches == [
        {
            "headless": True,
            "args": [
                "--host-resolver-rules=MAP * ~NOTFOUND",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
            "proxy": {"server": "http://127.0.0.1:9"},
            "handle_sigint": False,
            "handle_sigterm": False,
            "handle_sighup": False,
        }
    ]
    assert CHROMIUM_ARGS == tuple(playwright.launches[0]["args"])
    assert DEAD_PROXY == "http://127.0.0.1:9"
    assert playwright.events == ["start", "browser closed", "stop"]


def test_a_renderer_without_a_browser_looks_up_default_browser_at_each_render(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.renderer", "default_browser")
    playwright = FakePlaywright()
    monkeypatch.setattr(renderer_module, "sync_playwright", playwright)
    renderer = PlaywrightRenderer(
        meter=world.meter, fetcher=world.crawler, monotonic=world.ticks.monotonic
    )

    rendered = renderer.render(HOME, site=SITE, min_interval_s=1.0)

    assert rendered.final_url == HOME
    assert playwright.events == ["start", "browser closed", "stop"]
    assert world.web.hosts() == [SITE]  # the main document, through the filtered fetch


def test_the_live_guard_refuses_the_browser_so_a_render_is_unavailable(world: World) -> None:
    renderer = PlaywrightRenderer(meter=world.meter, fetcher=world.crawler)

    with pytest.raises(AdapterError) as raised:
        renderer.render(HOME, site=SITE, min_interval_s=1.0)  # default_browser is refused

    assert raised.value.code == "render_unavailable"
    assert world.web.requests == []


def test_building_a_renderer_launches_and_builds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[object] = []
    monkeypatch.setattr(renderer_module, "default_browser", lambda: built.append("browser"))

    renderer = PlaywrightRenderer(meter=InMemoryUsageMeter())

    assert isinstance(renderer._fetcher, HttpCrawler)  # noqa: SLF001 - the default fetcher
    assert built == []


@pytest.mark.parametrize("interval", ["1", None, True])
def test_a_bad_interval_is_a_type_error_before_anything(world: World, interval: object) -> None:
    with pytest.raises(TypeError):
        world.renderer.render(HOME, site=SITE, min_interval_s=interval)  # type: ignore[arg-type]

    assert world.browsers.browsers == []
