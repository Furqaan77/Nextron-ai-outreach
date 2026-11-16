"""Build the dashboard with fakes and open a session on it, as ``nextron open`` would."""

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import FastAPI

from nextron.app.ops.api import DashboardEntry
from nextron.web.app import create_app
from tests.asgi import DEFAULT_HOST, Reply, request
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

PORT = 8766  # matches tests.asgi.DEFAULT_HOST


@dataclass
class Dashboard:
    """A dashboard app on ``127.0.0.1:8766`` with in-memory secrets and a frozen clock."""

    secrets: InMemorySecrets = field(default_factory=InMemorySecrets)
    clock: FrozenClock = field(default_factory=FrozenClock)
    env: str = "dev"
    version: str = "1.2.3"
    monotonic: Callable[[], float] | None = None
    app: FastAPI = field(init=False)
    entry: DashboardEntry = field(init=False)

    def __post_init__(self) -> None:
        self.entry = DashboardEntry(self.secrets, self.clock)
        extra = {"monotonic": self.monotonic} if self.monotonic is not None else {}
        self.app = create_app(
            env=self.env, version=self.version, port=PORT, entry=self.entry, **extra
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
