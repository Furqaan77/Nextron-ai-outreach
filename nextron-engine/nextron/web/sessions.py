"""In-memory dashboard sessions (AD-13).

``GET /enter`` creates a session when the one-time code is accepted and sends its token in the
``nextron_session`` cookie (``HttpOnly; Path=/; SameSite=Strict``). Each session also gets its
own synchronizer CSRF token (``token_urlsafe(32)``), kept for the session's life: pages carry it
in ``<body hx-headers>`` and in each form's hidden ``csrf_token`` field, and every POST must
present it (``nextron.web.csrf``). The store keeps only the SHA-256 digest of each session
token, mapped to its CSRF token; it lives inside the app, and so is dropped by every restart.
There is no idle expiry: a session ends when the engine restarts or the browser drops its
cookie.

Story 5.1: the store is built with the app's clock (``now``) and records each session's start,
the clock's time at ``create``; ``started_at(session_token)`` returns it (``None`` for no live
session, or a store built without a clock). The Approval Queue shows the time since.
"""

import hashlib
import secrets
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Final

SESSION_COOKIE: Final = "nextron_session"


def session_cookie_values(header: str) -> Iterator[str]:
    """Each value of the ``nextron_session`` cookie in the ``Cookie`` header ``header``, in
    order, stripped."""
    for pair in header.split(";"):
        name, separator, value = pair.partition("=")
        if separator and name.strip() == SESSION_COOKIE:
            yield value.strip()


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", errors="replace")).digest()


class SessionStore:
    """Live sessions, each a SHA-256 digest of its token mapped to its CSRF token; safe to use
    from several threads."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._csrf_tokens: dict[bytes, str] = {}
        self._started: dict[bytes, datetime] = {}
        self._now = now
        self._lock = threading.Lock()

    def create(self) -> str:
        """Start a session with its own CSRF token and return the session token (256 bits, 43
        base64url characters)."""
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        started = None if self._now is None else self._now()
        with self._lock:
            self._csrf_tokens[_digest(token)] = csrf_token
            if started is not None:
                self._started[_digest(token)] = started
        return token

    def csrf_token(self, session_token: str) -> str | None:
        """The CSRF token of the session ``session_token`` names; ``None`` when there is none."""
        if not session_token:
            return None
        digest = _digest(session_token)
        with self._lock:
            return self._csrf_tokens.get(digest)

    def started_at(self, session_token: str) -> datetime | None:
        """When the session ``session_token`` names started; ``None`` when there is none or the
        store has no clock."""
        if not session_token:
            return None
        digest = _digest(session_token)
        with self._lock:
            return self._started.get(digest)
