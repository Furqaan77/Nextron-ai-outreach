"""The dashboard guard (AD-13): one pure ASGI middleware in front of every route.

For an ``http`` request, in this order:

1. ``Host``: exactly one header, equal byte for byte to ``127.0.0.1:<port>`` or
   ``localhost:<port>``. Anything else (another name, a missing or extra port, a trailing dot,
   IPv6, no header, two headers) gets 400 ``Bad request: unexpected Host header`` before
   routing, which stops DNS rebinding.
2. Session: any path other than exactly ``/enter`` needs a ``nextron_session`` cookie that
   names a live session; otherwise 401 with the locked page, GET included. An HTMX request
   (exactly one ``HX-Request: true`` header) gets the 401 as plain text instead,
   ``LOCKED_TEXT``, which HTMX puts in the page's error notice. The CSRF token of the first
   live ``nextron_session`` value goes into ``scope["nextron.csrf_token"]``, where
   ``nextron.web.csrf.require_csrf`` and the page renderer read it.
3. For any method other than ``GET`` and ``HEAD``, before routing (so unknown paths and wrong
   methods are covered too):

   - ``Origin``: exactly one header, equal byte for byte to ``http://`` plus the request's
     ``Host`` (a page served from ``localhost:<port>`` posts with that origin, one from
     ``127.0.0.1:<port>`` with that one);
   - ``Sec-Fetch-Site``: no header, or exactly one equal to ``same-origin``.

   A failure gets 403 ``Request refused: security check failed`` and is logged as
   ``request_refused`` (WARNING) with ``check`` set to ``origin`` or ``fetch_site``, the
   ``method`` and the ``path`` (never the query string or a header value). A page on another
   port of ``127.0.0.1`` is same-site, so the browser sends it the ``SameSite=Strict`` cookie;
   these checks, and the CSRF token, are what stop it posting to the dashboard. Under the
   ``Referrer-Policy: no-referrer`` header, a browser's native form submission sends
   ``Origin: null``, so every dashboard form posts through HTMX (``hx-post``), whose request
   keeps the real ``Origin``; a native fallback (HTMX not loaded) is refused here.
4. The app runs.

Every response that passes through, the guard's own included, carries exactly the
``SECURITY_HEADERS`` (replacing any the handler set) and no ``access-control-*`` header. It
also carries ``Cache-Control: no-store`` (replacing the handler's) unless the matched endpoint
declared ``personal_data(False)``; the router puts the endpoint in ``scope["endpoint"]``, which
the guard reads when the response starts. So undeclared, unmatched, 400, 401, 403 and 500
responses are never cached.

An exception raised before the response starts produces a 500 ``Internal error`` with the same
headers, and is then raised again so Uvicorn logs it. ``websocket`` connections are closed
(the dashboard has none) and ``lifespan`` passes through.
"""

import logging
from collections.abc import Iterable
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nextron.web.csrf import CSRF_SCOPE_KEY, SECURITY_CHECK_FAILED
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
SECURITY_CHECK_FAILED_BODY: Final = SECURITY_CHECK_FAILED.encode("ascii")
INTERNAL_ERROR_BODY: Final = b"Internal error"
LOCKED_TEXT: Final = (
    "The dashboard is locked: run nextron open in a terminal to open it again."
)
LOCKED_TEXT_BODY: Final = LOCKED_TEXT.encode("ascii")
HX_REQUEST: Final = b"hx-request"
TEXT_PLAIN: Final = b"text/plain; charset=utf-8"
TEXT_HTML: Final = b"text/html; charset=utf-8"
POLICY_VIOLATION: Final = 1008  # WebSocket close code
SAFE_METHODS: Final = frozenset({"GET", "HEAD"})
SAME_ORIGIN: Final = b"same-origin"
ORIGIN_CHECK: Final = "origin"
FETCH_SITE_CHECK: Final = "fetch_site"

_REPLACED: Final = frozenset(name for name, _ in SECURITY_HEADERS)

log = logging.getLogger(__name__)


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


def _failed_check(scope: Scope) -> str | None:
    """Which header check an unsafe-method request fails, or ``None``. The ``Host`` header has
    already been checked: there is exactly one."""
    [host] = _header_values(scope, b"host")
    origins = _header_values(scope, b"origin")
    if origins != [b"http://" + host]:
        return ORIGIN_CHECK
    fetch_sites = _header_values(scope, b"sec-fetch-site")
    if fetch_sites and fetch_sites != [SAME_ORIGIN]:
        return FETCH_SITE_CHECK
    return None


class Guard:
    """Host, session and unsafe-method checks and security headers for the dashboard (AD-13)."""

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

    def _session_csrf_token(self, scope: Scope) -> str | None:
        """The CSRF token of the first ``nextron_session`` cookie value that names a live
        session; ``None`` when none does."""
        for header in _header_values(scope, b"cookie"):
            for pair in header.decode("latin-1").split(";"):
                name, separator, value = pair.partition("=")
                if separator and name.strip() == SESSION_COOKIE:
                    csrf_token = self.sessions.csrf_token(value.strip())
                    if csrf_token is not None:
                        return csrf_token
        return None

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._host_allowed(scope):
            await _respond(send, 400, TEXT_PLAIN, BAD_HOST_BODY)
            return
        csrf_token = self._session_csrf_token(scope)
        if csrf_token is not None:
            scope[CSRF_SCOPE_KEY] = csrf_token
        elif scope.get("path") != ENTER_PATH:
            if _header_values(scope, HX_REQUEST) == [b"true"]:
                await _respond(send, 401, TEXT_PLAIN, LOCKED_TEXT_BODY)
            else:
                await _respond(send, 401, TEXT_HTML, self.locked_page)
            return
        if scope.get("method") not in SAFE_METHODS:
            failed = _failed_check(scope)
            if failed is not None:
                log.warning(
                    "request_refused",
                    extra={
                        "check": failed,
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                    },
                )
                await _respond(send, 403, TEXT_PLAIN, SECURITY_CHECK_FAILED_BODY)
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
