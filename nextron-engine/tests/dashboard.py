"""Build the dashboard with fakes and open a session on it, as ``nextron open`` would.

``Dashboard`` reads through ``FakeUnits`` unless a test passes real units of work (the
``units`` fixture); its ``Setup`` uses the dashboard's frozen clock, and its ``Credentials`` the
dashboard's in-memory secrets and that clock. ``post`` sends a POST from
the dashboard's own origin by default; ``csrf_token(cookie)`` reads the session's CSRF token
from ``GET /``'s ``<body hx-headers>``, as HTMX would; ``form(**fields)`` builds an urlencoded
body.

Its ``GoogleAccess`` (``google``) uses the same secrets and clock with fakes:
``FakeGoogleOAuth`` (``oauth``), ``FakeClientFiles`` (``files``), a ``RecordingBrowser``
(``browser``, which records each URL and answers ``opens``), the ``allowlist`` field (a list,
read on every check; ``allowlist_error`` makes the provider raise), and ``listen``, which builds
a ``FakeConsentListener`` per flow and keeps it in ``listeners`` (set ``next_answer`` before the
Grant click, or the listener's ``answer`` before running the job). A flow's background job is
never started on its own: ``start`` keeps it in ``jobs``, and ``run_jobs()`` runs them, so a
test sees the waiting row before Google answers.

Its ``Notify`` (``notify``) uses the same clock, a ``JobQueue`` (``queue``) on the real job types
(``job_types``, from ``nextron.main.build_job_types``) and, for Telegram, the ``alert`` port
(``InMemoryAlert`` unless a test passes another ``AlertPort``); the notification area and its
Dismiss go through it, on the dashboard's units of work.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlencode

from fastapi import FastAPI

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import DashboardEntry
from nextron.app.setup.api import Credentials, GoogleAccess, Setup
from nextron.domain.alert import Channel
from nextron.domain.job import JobTypes
from nextron.ports.alert import AlertPort
from nextron.ports.google_oauth import Query
from nextron.web.app import create_app
from nextron.web.routing import Units
from tests.asgi import DEFAULT_HOST, Headers, Reply, request
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.google_oauth import FakeClientFiles, FakeConsentListener, FakeGoogleOAuth
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


class RecordingBrowser:
    """A browser that records each URL it is asked to open and answers ``opens``; with
    ``error`` set it raises that instead."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.opens = True
        self.error: Exception | None = None

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.opens


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
    oauth: FakeGoogleOAuth = field(default_factory=FakeGoogleOAuth)
    files: FakeClientFiles = field(default_factory=FakeClientFiles)
    browser: RecordingBrowser = field(default_factory=RecordingBrowser)
    allowlist: list[str] = field(default_factory=list)
    allowlist_error: Exception | None = None
    next_answer: Query | None = None  # what the next listener's wait returns
    listen_error: Exception | None = None
    listeners: list[FakeConsentListener] = field(default_factory=list)
    jobs: list[Callable[[], None]] = field(default_factory=list)
    alert: AlertPort = field(default_factory=InMemoryAlert)
    app: FastAPI = field(init=False)
    entry: DashboardEntry = field(init=False)
    setup: Setup = field(init=False)
    credentials: Credentials = field(init=False)
    google: GoogleAccess = field(init=False)
    job_types: JobTypes = field(init=False)
    queue: JobQueue = field(init=False)
    notify: Notify = field(init=False)

    def __post_init__(self) -> None:
        self.entry = DashboardEntry(self.secrets, self.clock)
        self.setup = Setup(self.clock)
        self.credentials = Credentials(self.secrets, self.clock)
        self.google = GoogleAccess(
            self.secrets,
            self.clock,
            oauth=self.oauth,
            listen=self.listen,
            files=self.files,
            browser=self.browser,
            env=self.env,
            allowlist=self.read_allowlist,
            start=self.jobs.append,
        )
        self.job_types = main.build_job_types(alerts=lambda: self.notify)
        self.queue = JobQueue(self.clock, self.job_types)
        self.notify = Notify(
            self.clock, self.queue, alert_ports={Channel.TELEGRAM: lambda: self.alert}
        )
        extra = {"monotonic": self.monotonic} if self.monotonic is not None else {}
        self.app = create_app(
            env=self.env,
            version=self.version,
            port=PORT,
            entry=self.entry,
            setup=self.setup,
            credentials=self.credentials,
            google=self.google,
            notify=self.notify,
            units=self.units,
            **extra,
        )

    def listen(self) -> FakeConsentListener:
        if self.listen_error is not None:
            raise self.listen_error
        self.listeners.append(FakeConsentListener(self.next_answer))
        return self.listeners[-1]

    def read_allowlist(self) -> list[str]:
        if self.allowlist_error is not None:
            raise self.allowlist_error
        return list(self.allowlist)

    def run_jobs(self) -> int:
        """Run every queued flow job, in order; return how many ran."""
        jobs, self.jobs[:] = list(self.jobs), []
        for job in jobs:
            job()
        return len(jobs)

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
