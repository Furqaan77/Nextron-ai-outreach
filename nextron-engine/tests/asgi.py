"""Drive an ASGI app in-process (Starlette's TestClient needs httpx, which is not in the Stack).

``request`` sends one HTTP request with any method, query string and header list and returns
a ``Reply``; ``get`` keeps the old shape (status, headers as a dict, body). The default header
list is just ``Host: 127.0.0.1:8766``, which is what a dashboard on the ``dev`` port expects.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

Asgi = Callable[..., Any]
Headers = Sequence[tuple[bytes, bytes]]

DEFAULT_HOST = b"127.0.0.1:8766"


@dataclass
class Reply:
    """What the app sent: status, the header list in order, the body, and any exception the
    app raised (only when ``request`` was told to catch it)."""

    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    error: BaseException | None = field(default=None)

    def all(self, name: bytes) -> list[bytes]:
        """Every value of header ``name`` (lowercase), in order."""
        return [value for key, value in self.headers if key.lower() == name]

    def header(self, name: bytes) -> bytes | None:
        """The only value of header ``name``; ``None`` when absent, an error when repeated."""
        values = self.all(name)
        assert len(values) <= 1, f"{name!r} appears {len(values)} times"
        return values[0] if values else None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


def request(
    app: Asgi,
    path: str,
    *,
    method: str = "GET",
    query: bytes = b"",
    headers: Headers | None = None,
    catch: bool = False,
) -> Reply:
    """Send one request through ``app``. ``headers`` replaces the default ``Host`` header list.
    With ``catch``, an exception the app raises is returned in ``Reply.error`` instead."""
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": list(headers) if headers is not None else [(b"host", DEFAULT_HOST)],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8766),
    }
    error: BaseException | None = None
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as exc:
        if not catch:
            raise
        error = exc
    [start] = [m for m in messages if m["type"] == "http.response.start"]  # exactly one response
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return Reply(start["status"], list(start["headers"]), body, error)


def get(app: Asgi, path: str) -> tuple[int, dict[bytes, bytes], bytes]:
    """Send one GET through ``app`` and return status, headers and body."""
    reply = request(app, path)
    return reply.status, dict(reply.headers), reply.body
