"""In-memory dashboard sessions (AD-13).

``GET /enter`` creates a session when the one-time code is accepted and sends its token in the
``nextron_session`` cookie (``HttpOnly; Path=/; SameSite=Strict``). Each session also gets its
own synchronizer CSRF token (``token_urlsafe(32)``), kept for the session's life: pages carry it
in ``<body hx-headers>`` and in each form's hidden ``csrf_token`` field, and every POST must
present it (``nextron.web.csrf``). The store keeps only the SHA-256 digest of each session
token, mapped to its CSRF token; it lives inside the app, and so is dropped by every restart.
There is no idle expiry: a session ends when the engine restarts or the browser drops its
cookie.
"""

import hashlib
import secrets
import threading
from typing import Final

SESSION_COOKIE: Final = "nextron_session"


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", errors="replace")).digest()


class SessionStore:
    """Live sessions, each a SHA-256 digest of its token mapped to its CSRF token; safe to use
    from several threads."""

    def __init__(self) -> None:
        self._csrf_tokens: dict[bytes, str] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        """Start a session with its own CSRF token and return the session token (256 bits, 43
        base64url characters)."""
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self._lock:
            self._csrf_tokens[_digest(token)] = csrf_token
        return token

    def csrf_token(self, session_token: str) -> str | None:
        """The CSRF token of the session ``session_token`` names; ``None`` when there is none."""
        if not session_token:
            return None
        digest = _digest(session_token)
        with self._lock:
            return self._csrf_tokens.get(digest)
