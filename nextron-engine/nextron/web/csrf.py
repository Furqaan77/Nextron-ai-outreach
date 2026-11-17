"""The synchronizer CSRF token check on every dashboard POST (AD-13).

Every session has one CSRF token (``nextron.web.sessions``). The guard puts the token of the
request's session into ``scope["nextron.csrf_token"]``; ``require_csrf``, which the dashboard
route class puts first in every POST route's dependencies (``nextron.web.routing``), checks
what the request presents against it:

- **Presented:** every ``X-CSRF-Token`` header (HTMX sends the one ``<body hx-headers>``
  carries), and, when the ``Content-Type`` media type is ``application/x-www-form-urlencoded``,
  every ``csrf_token`` field of the body (a form's hidden ``csrf_field()``). Every form posts
  through HTMX (``hx-post``): a native form submission sends ``Origin: null`` under the
  dashboard's ``Referrer-Policy: no-referrer``, and the guard refuses it. The body is parsed
  with ``urllib.parse.parse_qs``: ASCII only, blank values kept, at most 1,000 fields. Any
  other body (multipart or JSON, say) presents nothing.
- **Pass:** at least one token is presented, and every presented token equals the session's,
  each compared in constant time (``hmac.compare_digest`` on UTF-8 bytes).
- **Fail:** anything else, a malformed body included, raises ``SecurityCheckFailed`` before the
  handler runs, so nothing changes. Its exception handler answers 403
  ``Request refused: security check failed`` with ``Cache-Control: no-store`` and logs
  ``request_refused`` with ``check=csrf_token``, the ``method`` and the ``path`` (never the
  query string or a token).

The ``Origin`` and ``Sec-Fetch-Site`` checks run earlier, in the guard, on every method other
than GET and HEAD.
"""

import hmac
import logging
from typing import Final
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import PlainTextResponse, Response

CSRF_SCOPE_KEY: Final = "nextron.csrf_token"
CSRF_HEADER: Final = "x-csrf-token"
CSRF_FIELD: Final = "csrf_token"
FORM_MEDIA_TYPE: Final = "application/x-www-form-urlencoded"
MAX_FORM_FIELDS: Final = 1000
SECURITY_CHECK_FAILED: Final = "Request refused: security check failed"
CSRF_CHECK: Final = "csrf_token"

log = logging.getLogger(__name__)


class SecurityCheckFailed(Exception):
    """A POST did not present its session's CSRF token; answered with 403."""

    def __init__(self) -> None:
        super().__init__("the CSRF token check failed")
        self.check = CSRF_CHECK


def _bytes(token: str) -> bytes:
    return token.encode("utf-8", errors="replace")


def media_type(content_type: str) -> str:
    """The lowercased media type of a ``Content-Type`` value, without its parameters."""
    return content_type.split(";", 1)[0].strip().lower()


async def _presented_tokens(request: Request) -> list[str]:
    """Every token the request presents; raises ``SecurityCheckFailed`` on a malformed body."""
    tokens = request.headers.getlist(CSRF_HEADER)
    content_types = request.headers.getlist("content-type")
    if len(content_types) > 1:
        raise SecurityCheckFailed()
    if content_types and media_type(content_types[0]) == FORM_MEDIA_TYPE:
        body = await request.body()
        try:
            fields = parse_qs(
                body.decode("ascii"), keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS
            )
        except ValueError as exc:  # non-ASCII (UnicodeDecodeError) or too many fields
            raise SecurityCheckFailed() from exc
        tokens += fields.get(CSRF_FIELD, [])
    return tokens


async def require_csrf(request: Request) -> None:
    """Raise ``SecurityCheckFailed`` unless the request presents its session's CSRF token, and
    nothing else, in every place it presents one."""
    expected = request.scope.get(CSRF_SCOPE_KEY)
    presented = await _presented_tokens(request)
    if not isinstance(expected, str) or not expected or not presented:
        raise SecurityCheckFailed()
    matches = [hmac.compare_digest(_bytes(token), _bytes(expected)) for token in presented]
    if not all(matches):
        raise SecurityCheckFailed()


def security_check_failed(request: Request, exc: Exception) -> Response:
    """The exception handler for ``SecurityCheckFailed``: 403 with the plain refusal."""
    log.warning(
        "request_refused",
        extra={
            "check": getattr(exc, "check", CSRF_CHECK),
            "method": request.method,
            "path": request.scope.get("path"),
        },
    )
    return PlainTextResponse(
        SECURITY_CHECK_FAILED, status_code=403, headers={"Cache-Control": "no-store"}
    )
