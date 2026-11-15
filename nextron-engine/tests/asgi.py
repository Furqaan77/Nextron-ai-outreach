"""Drive an ASGI app in-process (Starlette's TestClient needs httpx, which is not in the Stack)."""

import asyncio
from collections.abc import Callable
from typing import Any

Asgi = Callable[..., Any]


def get(app: Asgi, path: str) -> tuple[int, dict[bytes, bytes], bytes]:
    """Send one GET through ``app`` and return status, headers and body."""
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8766")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8766),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body
