"""The page renderer (FR-10, AD-9, AD-18, NFR-34, Story 2.6): ``RendererPort`` over Playwright's
bundled Chromium, with every request the browser makes fetched by the crawler's egress-filtered
client and fulfilled from it.

Import rule: ``nextron.ports``, ``nextron.domain``, ``nextron.adapters.crawler``, ``playwright``
and the standard library. Nothing here logs.

**``PlaywrightRenderer(*, meter, fetcher=None, browser=None, monotonic=time.monotonic)``**
implements ``RendererPort``. ``fetcher`` (``fetch_resource``, as
``nextron.adapters.crawler.HttpCrawler`` has it) defaults to ``HttpCrawler(meter=meter)``, so
every request the browser makes is checked, resolved, pinned, redirected, capped and metered
there. ``browser`` is a no-argument callable returning a context manager that yields a
Playwright ``Browser``; without one, ``default_browser`` is looked up at each ``render`` (so a
test can refuse it).

**``default_browser()``**, the live constructor, starts ``sync_playwright()`` and launches
headless Chromium with ``CHROMIUM_ARGS`` (``--host-resolver-rules=MAP * ~NOTFOUND``: it can
resolve no name; ``--force-webrtc-ip-handling-policy=disable_non_proxied_udp``: no WebRTC UDP),
the launch ``proxy`` set to the dead loopback proxy ``DEAD_PROXY`` (``http://127.0.0.1:9``) and
``handle_sigint``, ``handle_sigterm`` and ``handle_sighup`` off; on exit it closes the browser,
then Playwright. The browser itself never reaches the network: every request is intercepted.

**``render(url, *, site, min_interval_s)``**, in order:

1. ``nextron.domain.crawl.request_interval(min_interval_s)``, then the first URL and the
   ``site`` pin (``nextron.adapters.crawler.check_target``): a refusal is ``fetch_refused``
   before anything is launched.
2. One browser per call, and one fresh context: ``accept_downloads=False``,
   ``service_workers="block"``, no storage state, ``user_agent=USER_AGENT``.
3. ``context.route("**/*")`` handles every request (below) and ``route_web_socket("**/*")``
   closes every socket.
4. The ``RENDER_TIMEOUT_S`` (20 s) deadline starts at navigation. ``goto(url,
   wait_until="domcontentloaded")`` gets the time left; then the ``load`` state, and
   ``networkidle`` capped at ``NETWORK_IDLE_S`` (5 s), are awaited within it, their timeouts
   ignored. A main document whose fetch failed is raised as its own ``AdapterError``; one that
   isn't 2xx is ``render_failed``. When ``goto`` fails with Playwright's ``Error`` (not a
   timeout) after a client-side redirect (more than one main-frame document fulfilled, the last
   one 2xx), the page is waited for and read as usual; any other ``Error`` is
   ``render_failed``.
5. A main frame left on Chromium's error page (``chrome-error:``) is ``render_failed``; so is
   a ``page.content()`` longer than ``MAX_PAGE_BYTES`` (5 MiB, AD-9) characters. Otherwise it is
   parsed by ``nextron.adapters.crawler.parsing.parse_html`` against the last main document's
   final URL (the crawler's stripping rules; computed CSS isn't read).

**The session limit.** Steps 2 to 5 run on a daemon worker thread, waited for at most
``SESSION_LIMIT_S`` (``RENDER_TIMEOUT_S`` + 40 s) of real time, since a page whose script
busy-loops can hold ``page.content()`` forever. Past it the render is the retryable
``render_timeout`` and the worker is abandoned: it keeps its browser until it ends or the
engine exits.

**The request handler** first aborts any request whose URL ``nextron.domain.fetch_policy``'s
``is_denied`` matches (FR-49, Story 10.1: a LinkedIn badge, an Instagram embed or anything else
on the denylist, of any type and method), before any counter is touched and with no lookup or
fetch; the crawler would refuse it too, but this refusal doesn't rest on it. Then it aborts
every request unless all of these hold: its method is GET; it comes before the deadline,
within ``MAX_REQUESTS`` (100) fetches, and before ``MAX_BYTES`` (20 MiB) have been read in
this render (fetching stops once they have; a body that takes the total past them is read but
never handed to the page); and its type is allowed (``ALLOWED``).
A refused navigation of the main frame after its first document was fulfilled is aborted with
``"aborted"`` (``ERR_ABORTED`` leaves the current document in place, where
``"blockedbyclient"`` would commit Chromium's error page); every other refusal with
``"blockedbyclient"``. The allowed types:

- ``document``: only a navigation of the page's own main frame (pinned to ``site``, at most
  ``MAX_NAVIGATIONS``, 6), taking ``text/html`` or ``application/xhtml+xml``, paced by
  ``min_interval_s``;
- ``script`` (JavaScript media types), ``stylesheet`` (``text/css``), and ``xhr`` and ``fetch``
  (JSON, any ``+json``, ``text/plain`` or HTML): unpinned and unpaced, as a visitor's browser
  loads them.

An allowed request is fetched with ``fetch_resource`` (operation ``render``, the render's
deadline, no header of the browser's forwarded) and fulfilled with the final status, the body, a
rebuilt ``content-type`` and the two CORS headers, and no ``set-cookie``. A fetch failure aborts
it; a fetch whose body takes the render past ``MAX_BYTES`` is aborted too. Images, fonts, media,
frames' documents, popups, beacons and anything else are aborted; the browser makes no request
of its own (no ``route.continue_()``, no ``route.fetch()``).

**Errors.** Only ``AdapterError`` leaves ``render``, with no URL, header or page text: the main
document's fetch failure as the crawler raised it, ``render_timeout`` (retryable) when the
navigation passes the deadline or the session its limit, ``render_unavailable`` when the
browser can't start, and
``render_failed`` for anything else (Playwright's ``Error`` and any other exception included),
each with this module's fixed message. Closing the context and the browser never fails a render.
"""

import contextlib
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Final, Protocol

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from nextron.adapters.crawler import HttpCrawler, Resource, check_target
from nextron.adapters.crawler.parsing import parse_html
from nextron.domain.crawl import (
    HTML_MEDIA_TYPES,
    RENDER_TIMEOUT_S,
    USER_AGENT,
    request_interval,
)
from nextron.domain.fetch_policy import MAX_PAGE_BYTES, is_denied
from nextron.ports.errors import AdapterError
from nextron.ports.renderer import (
    RENDER_FAILED,
    RENDER_TIMEOUT,
    RENDER_UNAVAILABLE,
    RenderedPage,
)
from nextron.ports.usage_meter import UsageMeterPort

CHROMIUM_ARGS: Final = (
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)
DEAD_PROXY: Final = "http://127.0.0.1:9"  # nothing listens there: a request that escapes fails
ROUTE_ALL: Final = "**/*"
OPERATION: Final = "render"  # the usage meter's operation for every fetch of a render
NETWORK_IDLE_S: Final = 5.0
MAX_REQUESTS: Final = 100
MAX_BYTES: Final = 20 * 1024 * 1024
MAX_NAVIGATIONS: Final = 6
MIN_TIMEOUT_MS: Final = 1.0  # Playwright reads a timeout of 0 as "none"
SESSION_LIMIT_S: Final = RENDER_TIMEOUT_S + 40.0  # real time a render's browser session may take
ABORT_CODE: Final = "blockedbyclient"
# A later main-frame navigation refused: ERR_ABORTED keeps the current document
NAVIGATION_ABORT_CODE: Final = "aborted"
ERROR_PAGE_PREFIX: Final = "chrome-error:"
GET: Final = "GET"
DOCUMENT: Final = "document"
CONTENT_TYPE: Final = "content-type"
CHARSET_TEXT: Final = re.compile(r"[A-Za-z0-9._:-]{1,40}")  # matched with fullmatch
SCRIPT_MEDIA_TYPES: Final = frozenset(
    {
        "application/javascript",
        "text/javascript",
        "application/x-javascript",
        "application/ecmascript",
        "text/ecmascript",
    }
)
STYLE_MEDIA_TYPES: Final = frozenset({"text/css"})
DATA_MEDIA_TYPES: Final = frozenset({"application/json", "text/plain", *HTML_MEDIA_TYPES})
JSON_SUFFIX: Final = "+json"
DOCUMENT_ACCEPT: Final = "text/html,application/xhtml+xml;q=0.9"
SCRIPT_ACCEPT: Final = "*/*"
STYLE_ACCEPT: Final = "text/css,*/*;q=0.1"
DATA_ACCEPT: Final = "application/json,text/plain,*/*;q=0.1"


class ResourceFetcher(Protocol):
    """What the renderer needs of the crawler: one filtered fetch of any resource."""

    def fetch_resource(
        self,
        url: str,
        *,
        site: str | None,
        accepts: Callable[[str], bool],
        accept_header: str,
        operation: str,
        deadline: float | None,
        min_interval_s: float | None,
    ) -> Resource: ...


type BrowserFactory = Callable[[], AbstractContextManager[Any]]


@contextlib.contextmanager
def default_browser() -> Iterator[Any]:
    """The live browser: headless Chromium that can reach nothing itself; see the module
    docstring."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=list(CHROMIUM_ARGS),
            proxy={"server": DEAD_PROXY},
            handle_sigint=False,
            handle_sigterm=False,
            handle_sighup=False,
        )
        try:
            yield browser
        finally:
            browser.close()


def _is_document(media_type: str) -> bool:
    return media_type in HTML_MEDIA_TYPES


def _is_script(media_type: str) -> bool:
    return media_type in SCRIPT_MEDIA_TYPES


def _is_style(media_type: str) -> bool:
    return media_type in STYLE_MEDIA_TYPES


def _is_data(media_type: str) -> bool:
    return media_type in DATA_MEDIA_TYPES or (
        "/" in media_type and media_type.endswith(JSON_SUFFIX)
    )


@dataclass(frozen=True, slots=True)
class _Allowed:
    """What one allowed request type takes."""

    accepts: Callable[[str], bool]
    accept_header: str


DOCUMENT_TYPE: Final = _Allowed(_is_document, DOCUMENT_ACCEPT)
# The sub-resource types a render loads; everything else (images, fonts, media, ...) is aborted
ALLOWED: Final = {
    "script": _Allowed(_is_script, SCRIPT_ACCEPT),
    "stylesheet": _Allowed(_is_style, STYLE_ACCEPT),
    "xhr": _Allowed(_is_data, DATA_ACCEPT),
    "fetch": _Allowed(_is_data, DATA_ACCEPT),
}


@dataclass(frozen=True, slots=True)
class _Failure:
    retryable: bool
    code: str
    message: str


FAILED: Final = _Failure(False, RENDER_FAILED, "the page could not be rendered")
TIMED_OUT: Final = _Failure(True, RENDER_TIMEOUT, "the page did not render in time")
UNAVAILABLE: Final = _Failure(False, RENDER_UNAVAILABLE, "the browser could not start")


def _milliseconds(seconds: float) -> float:
    return max(seconds * 1000.0, MIN_TIMEOUT_MS)


def _quietly(action: Callable[[], object]) -> None:
    """Run ``action``; whatever it raises is ignored (closing, aborting)."""
    try:
        action()
    except Exception:  # noqa: BLE001 - a close or an abort never fails a render
        pass


def _close_socket(socket_route: Any) -> None:
    _quietly(socket_route.close)


def _headers(resource: Resource) -> dict[str, str]:
    """What a fulfilled request carries: a rebuilt ``content-type`` and the CORS headers."""
    headers: dict[str, str] = {}
    if resource.media_type:
        charset = resource.charset
        suffix = (
            f"; charset={charset}"
            if charset is not None and CHARSET_TEXT.fullmatch(charset)
            else ""
        )
        headers[CONTENT_TYPE] = resource.media_type + suffix
    headers.update(resource.headers)
    return headers


class _Render:
    """One render's state and request handler."""

    def __init__(
        self,
        fetcher: ResourceFetcher,
        *,
        site: str | None,
        interval: float,
        monotonic: Callable[[], float],
    ) -> None:
        self.fetcher = fetcher
        self.site = site
        self.interval = interval
        self.monotonic = monotonic
        self.deadline = monotonic() + RENDER_TIMEOUT_S  # reset when navigation starts
        self.page: Any = None
        self.lock = threading.Lock()
        self.fetches = 0
        self.bytes_read = 0
        self.navigations = 0
        self.main_failure: _Failure | None = None  # the first document's fetch failure
        self.main_status: int | None = None  # the last main document fulfilled
        self.final_url: str | None = None
        self.main_fulfilled = 0  # main-frame documents fulfilled

    def left(self) -> float:
        return self.deadline - self.monotonic()

    def redirected(self) -> bool:
        """Whether the page went on to another main-frame document, the last one 2xx (a
        client-side redirect)."""
        status = self.main_status
        return self.main_fulfilled > 1 and status is not None and 200 <= status < 300

    def handle(self, route: Any, request: Any) -> None:
        """The route handler: fulfil an allowed request from the filtered fetch, else abort."""
        # once a document stands, a refused navigation must leave it in place
        later = self.main_status is not None and self._is_main_navigation(request)
        code = NAVIGATION_ABORT_CODE if later else ABORT_CODE
        try:
            fulfilled = self._serve(route, request)
        except Exception:  # noqa: BLE001 - anything unexpected aborts the request
            fulfilled = False
        if not fulfilled:
            _quietly(lambda: route.abort(code))

    def _is_main_navigation(self, request: Any) -> bool:
        """Whether ``request`` navigates the page's own main frame (``False`` when Playwright
        can't say, as for a popup's request)."""
        try:
            return bool(
                request.resource_type == DOCUMENT
                and self.page is not None
                and request.is_navigation_request()
                and request.frame is self.page.main_frame
            )
        except Exception:  # noqa: BLE001 - a request without a frame is no main navigation
            return False

    def _allowed(self, request: Any) -> tuple[_Allowed, bool] | None:
        """What ``request`` may take, and whether it is a main-frame document; ``None`` when its
        type isn't allowed."""
        kind = request.resource_type
        if kind != DOCUMENT:
            found = ALLOWED.get(kind)
            return None if found is None else (found, False)
        if not self._is_main_navigation(request):  # a frame's or a popup's document
            return None
        return DOCUMENT_TYPE, True

    def _serve(self, route: Any, request: Any) -> bool:
        if is_denied(request.url):  # FR-49 (Story 10.1): never fetched, whatever its type
            return False
        if request.method != GET or self.monotonic() >= self.deadline:
            return False
        allowed = self._allowed(request)
        if allowed is None:
            return False
        rule, main = allowed
        with self.lock:
            if self.fetches >= MAX_REQUESTS or self.bytes_read >= MAX_BYTES:
                return False
            if main and self.navigations >= MAX_NAVIGATIONS:
                return False
            self.fetches += 1
            if main:
                self.navigations += 1
            first = main and self.navigations == 1
        try:
            resource = self.fetcher.fetch_resource(
                request.url,
                site=self.site if main else None,
                accepts=rule.accepts,
                accept_header=rule.accept_header,
                operation=OPERATION,
                deadline=self.deadline,
                min_interval_s=self.interval if main else None,
            )
        except AdapterError as error:
            if first:
                self.main_failure = _Failure(error.retryable, error.code, error.message)
            return False
        with self.lock:
            self.bytes_read += len(resource.body)
            if self.bytes_read > MAX_BYTES:
                return False
        route.fulfill(status=resource.status, headers=_headers(resource), body=resource.body)
        if main:
            self.main_status = resource.status
            self.final_url = resource.final_url
            self.main_fulfilled += 1
        return True


class PlaywrightRenderer:
    """``RendererPort`` over Playwright, every request through the egress filter; see the
    module docstring."""

    def __init__(
        self,
        *,
        meter: UsageMeterPort,
        fetcher: ResourceFetcher | None = None,
        browser: BrowserFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher: ResourceFetcher = (
            fetcher if fetcher is not None else HttpCrawler(meter=meter)
        )
        self._browser = browser
        self._monotonic = monotonic

    def render(self, url: str, *, site: str | None, min_interval_s: float) -> RenderedPage:
        interval = request_interval(min_interval_s)
        check_target(url, site)  # a refusal is fetch_refused, before anything is launched
        outcome = self._bounded(url, site, interval)
        if isinstance(outcome, _Failure):
            raise AdapterError(outcome.retryable, outcome.code, outcome.message) from None
        return outcome

    def _bounded(self, url: str, site: str | None, interval: float) -> RenderedPage | _Failure:
        """The session on a daemon worker thread, waited for at most ``SESSION_LIMIT_S`` of real
        time; past it the worker is abandoned and the render timed out."""
        outcomes: list[RenderedPage | _Failure] = []

        def run() -> None:
            outcomes.append(self._session(url, site, interval))

        worker = threading.Thread(target=run, name="nextron-render", daemon=True)
        worker.start()
        worker.join(SESSION_LIMIT_S)
        if worker.is_alive():
            return TIMED_OUT
        return outcomes[0] if outcomes else FAILED

    def _session(self, url: str, site: str | None, interval: float) -> RenderedPage | _Failure:
        """One browser for one render; only a ``RenderedPage`` or a ``_Failure`` comes out."""
        factory = self._browser if self._browser is not None else default_browser
        try:
            manager = factory()  # default_browser is looked up now, so tests can refuse it
            browser = manager.__enter__()
        except Exception:  # noqa: BLE001 - Playwright's or the driver's errors alike
            return UNAVAILABLE
        try:
            return self._in_browser(browser, url, site, interval)
        except Exception:  # noqa: BLE001 - Playwright's errors and any other become render_failed
            return FAILED
        finally:
            _quietly(lambda: manager.__exit__(None, None, None))

    def _in_browser(
        self, browser: Any, url: str, site: str | None, interval: float
    ) -> RenderedPage | _Failure:
        context = browser.new_context(
            accept_downloads=False,
            service_workers="block",
            storage_state=None,
            user_agent=USER_AGENT,
        )
        try:
            render = _Render(self._fetcher, site=site, interval=interval, monotonic=self._monotonic)
            context.route(ROUTE_ALL, render.handle)
            context.route_web_socket(ROUTE_ALL, _close_socket)
            render.page = context.new_page()
            return self._navigate(render, url)
        finally:
            _quietly(context.close)

    def _navigate(self, render: _Render, url: str) -> RenderedPage | _Failure:
        page = render.page
        render.deadline = self._monotonic() + RENDER_TIMEOUT_S  # from navigation
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_milliseconds(render.left()))
        except PlaywrightTimeoutError:
            return render.main_failure if render.main_failure is not None else TIMED_OUT
        except PlaywrightError:
            if render.main_failure is not None:
                return render.main_failure
            if not render.redirected():  # only a client-side redirect carries on
                return FAILED
        if render.main_failure is not None:
            return render.main_failure
        status = render.main_status
        if status is None or not 200 <= status < 300 or render.final_url is None:
            return FAILED
        for state, cap in (("load", None), ("networkidle", NETWORK_IDLE_S)):
            left = render.left()
            if left <= 0:
                break
            try:
                page.wait_for_load_state(
                    state, timeout=_milliseconds(left if cap is None else min(left, cap))
                )
            except PlaywrightTimeoutError:
                continue  # a page still loading is read as it stands
        if str(page.main_frame.url).startswith(ERROR_PAGE_PREFIX):  # Chromium's error page
            return FAILED
        html = page.content()
        if not isinstance(html, str) or len(html) > MAX_PAGE_BYTES:
            return FAILED
        parsed = parse_html(html, render.final_url)
        return RenderedPage(
            url=url,
            final_url=render.final_url,
            text=parsed.text,
            links=parsed.links,
            html=html,
            mailto=parsed.mailto,
        )
