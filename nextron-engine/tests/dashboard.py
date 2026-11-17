"""Build the dashboard with fakes and open a session on it, as ``nextron open`` would.

``Dashboard`` reads through ``FakeUnits`` unless a test passes real units of work (the
``units`` fixture); its ``Setup`` uses the dashboard's frozen clock. ``post`` sends a POST from
the dashboard's own origin by default; ``csrf_token(cookie)`` reads the session's CSRF token
from ``GET /``'s ``<body hx-headers>``, as HTMX would; ``form(**fields)`` builds an urlencoded
body.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlencode

from fastapi import FastAPI

from nextron.app.ops.api import DashboardEntry
from nextron.app.setup.api import Setup
from nextron.web.app import create_app
from nextron.web.routing import Units
from tests.asgi import DEFAULT_HOST, Headers, Reply, request
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

PORT = 8766  # matches tests.asgi.DEFAULT_HOST
ORIGIN = b"http://" + DEFAULT_HOST
FORM = b"application/x-www-form-urlencoded"


def form(**fields: str) -> bytes:
    """An ``application/x-www-form-urlencoded`` body of ``fields``, in order, as a browser
    encodes it."""
    return urlencode(fields).encode("ascii")


class _BodyHeaders(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.values.append(dict(attrs).get("hx-headers"))


def body_hx_headers(html: str) -> dict[str, object]:
    """The JSON object in the page's one ``<body hx-headers>``."""
    parser = _BodyHeaders()
    parser.feed(html)
    parser.close()
    [value] = parser.values
    assert value is not None, "<body> has no hx-headers"
    headers = json.loads(value)
    assert isinstance(headers, dict)
    return headers


@dataclass
class Dashboard:
    """A dashboard app on ``127.0.0.1:8766`` with in-memory secrets, a frozen clock and, by
    default, fake units of work."""

    secrets: InMemorySecrets = field(default_factory=InMemorySecrets)
    clock: FrozenClock = field(default_factory=FrozenClock)
    env: str = "dev"
    version: str = "1.2.3"
    monotonic: Callable[[], float] | None = None
    units: Units = field(default_factory=FakeUnits)
    app: FastAPI = field(init=False)
    entry: DashboardEntry = field(init=False)
    setup: Setup = field(init=False)

    def __post_init__(self) -> None:
        self.entry = DashboardEntry(self.secrets, self.clock)
        self.setup = Setup(self.clock)
        extra = {"monotonic": self.monotonic} if self.monotonic is not None else {}
        self.app = create_app(
            env=self.env,
            version=self.version,
            port=PORT,
            entry=self.entry,
            setup=self.setup,
            units=self.units,
            **extra,
        )

    def enter(self, code: str) -> Reply:
        return request(self.app, "/enter", query=b"t=" + code.encode())

    def open_session(self) -> bytes:
        """Issue a code, enter with it and return the ``Cookie`` header value to send."""
        reply = self.enter(self.entry.issue_code())
        assert reply.status == 303, reply
        cookie = reply.header(b"set-cookie")
        assert cookie is not None
        return cookie.split(b";", 1)[0]

    def get(self, path: str, *, cookie: bytes | None = None, query: bytes = b"") -> Reply:
        headers = [(b"host", DEFAULT_HOST)]
        if cookie is not None:
            headers.append((b"cookie", cookie))
        return request(self.app, path, query=query, headers=headers)

    def csrf_token(self, cookie: bytes) -> str:
        """The session's CSRF token, read from ``GET /``'s ``<body hx-headers>``."""
        reply = self.get("/", cookie=cookie)
        assert reply.status == 200, reply
        token = body_hx_headers(reply.text)["X-CSRF-Token"]
        assert isinstance(token, str) and token
        return token

    def post(
        self,
        path: str,
        *,
        cookie: bytes | None = None,
        token: str | None = None,
        origin: bytes | None = ORIGIN,
        headers: Headers = (),
        body: bytes = b"",
        query: bytes = b"",
        catch: bool = False,
    ) -> Reply:
        """POST from the dashboard's origin. ``token`` goes in an ``X-CSRF-Token`` header;
        ``origin=None`` sends no ``Origin``; ``headers`` are added after these."""
        sent = [(b"host", DEFAULT_HOST)]
        if cookie is not None:
            sent.append((b"cookie", cookie))
        if origin is not None:
            sent.append((b"origin", origin))
        if token is not None:
            sent.append((b"x-csrf-token", token.encode()))
        sent.extend(headers)
        return request(
            self.app, path, method="POST", query=query, headers=sent, body=body, catch=catch
        )
