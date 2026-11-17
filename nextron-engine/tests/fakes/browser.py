"""Fake Playwright objects for ``nextron.adapters.renderer``: no browser is launched and nothing
reaches the network.

``FakeBrowsers(site)`` is a ``browser=`` factory for ``PlaywrightRenderer``: each call returns a
context manager that yields a new ``FakeBrowser`` (recorded in ``browsers``, ``closed`` once the
manager exits); ``start_error`` makes entering it raise, as a browser that can't start does.

A ``FakeSite`` scripts what one render's page does:

- ``goto`` records a ``GotoCall``, runs ``on_goto`` (a test may move its clock there), then sends
  the main document request (a main-frame ``document`` navigation) through the context's route
  handler. An aborted main document raises Playwright's ``Error``, as Chromium's
  ``net::ERR_BLOCKED_BY_CLIENT`` does. Once it is fulfilled, each of ``requests`` (a ``Sub``:
  its URL, resource type, method, frame (``main``, ``child`` or ``raises``) and whether it is a
  navigation) goes through the handler in order, then each of ``sockets`` through the
  WebSocket handler; then ``goto_error`` is raised when set, else the main response returns.
- ``wait_for_load_state`` records ``(state, timeout)`` and raises ``load_errors[state]`` when
  set.
- ``content()`` is ``dom`` (a ``str``, or a callable taking the main document's body), else the
  main document's body as UTF-8.
- The main frame's ``url`` is ``about:blank`` until a main-frame document is fulfilled, then
  that document's URL; ``main_frame_url``, when set, replaces it once ``goto`` has sent its
  requests (as Chromium's ``chrome-error://chromewebdata/`` would).

``FakeBrowsers(..., exit_error=...)`` makes leaving the browser's context manager raise, as a
browser whose close fails does.

Every request records its ``FakeRoute``: ``outcome`` is ``Fulfilled(status, headers, body)`` or
``Aborted(code)``. A handler that does neither, a second outcome, ``continue_()``, ``fetch()`` or
``fallback()``, a request with no route registered, and a socket with no WebSocket route raise
``AssertionError``: the renderer must never let the browser make a request of its own.

``FakePlaywright`` stands in for ``sync_playwright()`` (a context manager yielding an object
whose ``chromium.launch(**options)`` records its options and returns a ``FakeBrowser``), so a test
can check ``default_browser``'s launch and its closing order (``events``).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Error as PlaywrightError

MAIN = "main"
CHILD = "child"
RAISES = "raises"


@dataclass(frozen=True, slots=True)
class Sub:
    """A request the page makes once its main document is fulfilled."""

    url: str
    resource_type: str = "script"
    method: str = "GET"
    frame: str = MAIN
    navigation: bool = False


@dataclass
class FakeSite:
    """What one render's page does; see the module docstring."""

    requests: list[Sub] = field(default_factory=list)
    sockets: list[str] = field(default_factory=list)
    dom: str | Callable[[bytes], str] | None = None
    goto_error: BaseException | None = None
    load_errors: dict[str, BaseException] = field(default_factory=dict)
    on_goto: Callable[[], None] | None = None
    main_frame_url: str | None = None


@dataclass(frozen=True, slots=True)
class Fulfilled:
    status: int
    headers: dict[str, str]
    body: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class Aborted:
    code: str | None


@dataclass(frozen=True, slots=True)
class GotoCall:
    url: str
    wait_until: str | None
    timeout: float | None


class FakeFrame:
    def __init__(self, name: str) -> None:
        self.name = name
        self.url = "about:blank"


class FakeRequest:
    """A Playwright ``Request``: the attributes the renderer reads."""

    def __init__(
        self,
        url: str,
        *,
        method: str = "GET",
        resource_type: str = "document",
        navigation: bool = False,
        frame: FakeFrame | None = None,
    ) -> None:
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self._navigation = navigation
        self._frame = frame

    @property
    def frame(self) -> FakeFrame:
        if self._frame is None:  # Playwright raises for a popup's or a worker's request
            raise PlaywrightError("the request has no frame")
        return self._frame

    def is_navigation_request(self) -> bool:
        return self._navigation


class FakeRoute:
    """A Playwright ``Route``: records the one outcome the handler gave."""

    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self.outcome: Fulfilled | Aborted | None = None

    def _settle(self, outcome: Fulfilled | Aborted) -> None:
        if self.outcome is not None:
            raise AssertionError("a route was handled twice")
        self.outcome = outcome

    def fulfill(
        self,
        *,
        status: int | None = None,
        headers: dict[str, str] | None = None,
        body: str | bytes | None = None,
        **other: Any,
    ) -> None:
        if other:
            raise AssertionError(f"the renderer fulfils with status, headers and body only: {other}")
        data = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        self._settle(Fulfilled(200 if status is None else status, dict(headers or {}), data))

    def abort(self, error_code: str | None = None) -> None:
        self._settle(Aborted(error_code))

    def continue_(self, **options: Any) -> None:
        raise AssertionError("the renderer never lets the browser make a request itself")

    def fetch(self, **options: Any) -> None:
        raise AssertionError("the renderer never lets the browser make a request itself")

    def fallback(self, **options: Any) -> None:
        raise AssertionError("the renderer never falls back to the browser's own request")


class FakeWebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    def close(self, *, code: int | None = None, reason: str | None = None) -> None:
        self.closed = True

    def connect_to_server(self) -> None:
        raise AssertionError("the renderer never connects a socket")


@dataclass(frozen=True, slots=True)
class FakeResponse:
    status: int


class FakePage:
    def __init__(self, context: "FakeContext") -> None:
        self.context = context
        self.main_frame = FakeFrame(MAIN)
        self.child_frame = FakeFrame(CHILD)
        self.gotos: list[GotoCall] = []
        self.waits: list[tuple[str | None, float | None]] = []
        self.body: bytes | None = None

    def _frame(self, name: str) -> FakeFrame | None:
        return {MAIN: self.main_frame, CHILD: self.child_frame}.get(name)

    def goto(
        self,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,
        referer: str | None = None,
    ) -> FakeResponse:
        site = self.context.browser.site
        self.gotos.append(GotoCall(url, wait_until, timeout))
        if site.on_goto is not None:
            site.on_goto()
        main = self.context.dispatch(
            FakeRequest(url, resource_type="document", navigation=True, frame=self.main_frame)
        )
        if not isinstance(main.outcome, Fulfilled):
            raise PlaywrightError("net::ERR_BLOCKED_BY_CLIENT")
        self.body = main.outcome.body
        self.main_frame.url = url
        for sub in site.requests:
            frame = self._frame(sub.frame)
            route = self.context.dispatch(
                FakeRequest(
                    sub.url,
                    method=sub.method,
                    resource_type=sub.resource_type,
                    navigation=sub.navigation,
                    frame=frame,
                )
            )
            fulfilled = isinstance(route.outcome, Fulfilled)
            if frame is self.main_frame and sub.navigation and fulfilled:
                self.main_frame.url = sub.url
        for socket_url in site.sockets:
            self.context.dispatch_socket(socket_url)
        if site.main_frame_url is not None:
            self.main_frame.url = site.main_frame_url
        if site.goto_error is not None:
            raise site.goto_error
        return FakeResponse(main.outcome.status)

    def wait_for_load_state(self, state: str | None = None, *, timeout: float | None = None) -> None:
        self.waits.append((state, timeout))
        error = self.context.browser.site.load_errors.get(state or "load")
        if error is not None:
            raise error

    def content(self) -> str:
        dom = self.context.browser.site.dom
        if isinstance(dom, str):
            return dom
        body = self.body if self.body is not None else b""
        if dom is not None:
            return dom(body)
        return body.decode("utf-8", errors="replace")


class FakeContext:
    def __init__(self, browser: "FakeBrowser", options: dict[str, Any]) -> None:
        self.browser = browser
        self.options = options
        self.routes: list[tuple[object, Callable[..., None]]] = []
        self.socket_routes: list[tuple[object, Callable[..., None]]] = []
        self.pages: list[FakePage] = []
        self.handled: list[FakeRoute] = []
        self.sockets: list[FakeWebSocketRoute] = []
        self.closed = False

    def route(self, url: object, handler: Callable[..., None], *, times: int | None = None) -> None:
        self.routes.append((url, handler))

    def route_web_socket(self, url: object, handler: Callable[..., None]) -> None:
        self.socket_routes.append((url, handler))

    def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True

    def dispatch(self, request: FakeRequest) -> FakeRoute:
        if not self.routes:
            raise AssertionError("a request went out with no route registered")
        route = FakeRoute(request)
        self.handled.append(route)
        _, handler = self.routes[-1]
        handler(route, request)
        if route.outcome is None:
            raise AssertionError("the route handler neither fulfilled nor aborted a request")
        return route

    def dispatch_socket(self, url: str) -> FakeWebSocketRoute:
        if not self.socket_routes:
            raise AssertionError("a socket opened with no WebSocket route registered")
        socket_route = FakeWebSocketRoute(url)
        self.sockets.append(socket_route)
        for _, handler in self.socket_routes:
            handler(socket_route)
        return socket_route


class FakeBrowser:
    def __init__(self, site: FakeSite | None = None) -> None:
        self.site = site if site is not None else FakeSite()
        self.contexts: list[FakeContext] = []
        self.closed = False

    def new_context(self, **options: Any) -> FakeContext:
        context = FakeContext(self, options)
        self.contexts.append(context)
        return context

    def close(self) -> None:
        self.closed = True


class FakeBrowsers:
    """A ``browser=`` factory; see the module docstring."""

    def __init__(
        self,
        site: FakeSite | None = None,
        *,
        start_error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.site = site if site is not None else FakeSite()
        self.start_error = start_error
        self.exit_error = exit_error
        self.browsers: list[FakeBrowser] = []

    @contextmanager
    def _open(self) -> Iterator[FakeBrowser]:
        if self.start_error is not None:
            raise self.start_error
        browser = FakeBrowser(self.site)
        self.browsers.append(browser)
        try:
            yield browser
        finally:
            browser.closed = True
            if self.exit_error is not None:
                raise self.exit_error

    def __call__(self) -> Any:
        return self._open()

    @property
    def context(self) -> FakeContext:
        """The one context of the one browser opened."""
        [browser] = self.browsers
        [context] = browser.contexts
        return context


class FakePlaywright:
    """``sync_playwright`` for ``default_browser``; see the module docstring."""

    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.browser: FakeBrowser | None = None
        self.chromium = self

    def __call__(self) -> Any:
        return self._started()

    @contextmanager
    def _started(self) -> Iterator["FakePlaywright"]:
        self.events.append("start")
        try:
            yield self
        finally:
            self.events.append("stop")

    def launch(self, **options: Any) -> FakeBrowser:
        self.launches.append(options)
        browser = _RecordingBrowser(self)
        self.browser = browser
        return browser


class _RecordingBrowser(FakeBrowser):
    def __init__(self, playwright: FakePlaywright) -> None:
        super().__init__()
        self._playwright = playwright

    def close(self) -> None:
        super().close()
        self._playwright.events.append("browser closed")
