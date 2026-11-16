"""The dashboard guard (AD-13): one pure ASGI middleware in front of every route.

For an ``http`` request, in this order:

1. ``Host``: exactly one header, equal byte for byte to ``127.0.0.1:<port>`` or
   ``localhost:<port>``. Anything else (another name, a missing or extra port, a trailing dot,
   IPv6, no header, two headers) gets 400 ``Bad request: unexpected Host header`` before
   routing, which stops DNS rebinding.
2. Session: any path other than exactly ``/enter`` needs a ``nextron_session`` cookie that
   names a live session; otherwise 401 with the locked page, GET included.
3. The app runs.

Every response that passes through, the guard's own included, carries exactly the
``SECURITY_HEADERS`` (replacing any the handler set) and no ``access-control-*`` header. It
also carries ``Cache-Control: no-store`` (replacing the handler's) unless the matched endpoint
declared ``personal_data(False)``; the router puts the endpoint in ``scope["endpoint"]``, which
the guard reads when the response starts. So undeclared, unmatched, 400, 401 and 500 responses
are never cached.

An exception raised before the response starts produces a 500 ``Internal error`` with the same
headers, and is then raised again so Uvicorn logs it. ``websocket`` connections are closed
(the dashboard has none) and ``lifespan`` passes through.
"""

from collections.abc import Iterable
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nextron.web.routing import declared_personal_data
from nextron.web.sessions import SESSION_COOKIE, SessionStore

ENTER_PATH: Final = "/enter"
CONTENT_SECURITY_POLICY: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
)
SECURITY_HEADERS: Final[tuple[tuple[bytes, bytes], ...]] = (
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode("ascii")),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
)
NO_STORE: Final = (b"cache-control", b"no-store")
BAD_HOST_BODY: Final = b"Bad request: unexpected Host header"
INTERNAL_ERROR_BODY: Final = b"Internal error"
TEXT_PLAIN: Final = b"text/plain; charset=utf-8"
TEXT_HTML: Final = b"text/html; charset=utf-8"
POLICY_VIOLATION: Final = 1008  # WebSocket close code

_REPLACED: Final = frozenset(name for name, _ in SECURITY_HEADERS)


def _secured(
    headers: Iterable[tuple[bytes, bytes]], *, no_store: bool
) -> list[tuple[bytes, bytes]]:
    """``headers`` without CORS headers or the guard's own, followed by the guard's."""
    kept = []
    for name, value in headers:
        lowered = name.lower()
        if lowered in _REPLACED or lowered.startswith(b"access-control-"):
            continue
        if no_store and lowered == NO_STORE[0]:
            continue
        kept.append((name, value))
    kept.extend(SECURITY_HEADERS)
    if no_store:
        kept.append(NO_STORE)
    return kept


def _header_values(scope: Scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers", []) if key.lower() == name]


class Guard:
    """Host check, session check and security headers for the dashboard (AD-13)."""

    def __init__(
        self, app: ASGIApp, *, port: int, sessions: SessionStore, locked_page: bytes
    ) -> None:
        self.app = app
        self.allowed_hosts = frozenset(
            {f"127.0.0.1:{port}".encode("ascii"), f"localhost:{port}".encode("ascii")}
        )
        self.sessions = sessions
        self.locked_page = locked_page

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": POLICY_VIOLATION, "reason": ""})
        else:
            await self.app(scope, receive, send)

    def _host_allowed(self, scope: Scope) -> bool:
        hosts = _header_values(scope, b"host")
        return len(hosts) == 1 and hosts[0] in self.allowed_hosts

    def _has_session(self, scope: Scope) -> bool:
        for header in _header_values(scope, b"cookie"):
            for pair in header.decode("latin-1").split(";"):
                name, separator, value = pair.partition("=")
                if (
                    separator
                    and name.strip() == SESSION_COOKIE
                    and self.sessions.is_valid(value.strip())
                ):
                    return True
        return False

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._host_allowed(scope):
            await _respond(send, 400, TEXT_PLAIN, BAD_HOST_BODY)
            return
        if scope.get("path") != ENTER_PATH and not self._has_session(scope):
            await _respond(send, 401, TEXT_HTML, self.locked_page)
            return

        started = False

        async def send_secured(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                no_store = declared_personal_data(scope.get("endpoint")) is not False
                headers = _secured(message.get("headers", []), no_store=no_store)
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_secured)
        except Exception:
            if not started:
                await _respond(send, 500, TEXT_PLAIN, INTERNAL_ERROR_BODY)
            raise


async def _respond(send: Send, status: int, content_type: bytes, body: bytes) -> None:
    """Send a complete response from the guard itself, with every guard header."""
    headers = [
        (b"content-type", content_type),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": _secured(headers, no_store=True),
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
