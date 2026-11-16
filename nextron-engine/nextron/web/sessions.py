"""In-memory dashboard sessions (AD-13).

``GET /enter`` creates a session when the one-time code is accepted and sends its token in the
``nextron_session`` cookie (``HttpOnly; Path=/; SameSite=Strict``). The store keeps only the
SHA-256 digest of each token, lives inside the app, and so is dropped by every restart. There is
no idle expiry: a session ends when the engine restarts or the browser drops its cookie.
"""

import hashlib
import secrets
import threading
from typing import Final

SESSION_COOKIE: Final = "nextron_session"


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", errors="replace")).digest()


class SessionStore:
    """Valid session tokens, stored as SHA-256 digests; safe to use from several threads."""

    def __init__(self) -> None:
        self._digests: set[bytes] = set()
        self._lock = threading.Lock()

    def create(self) -> str:
        """Start a session and return its token (256 bits, 43 base64url characters)."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._digests.add(_digest(token))
        return token

    def is_valid(self, token: str) -> bool:
        """Whether ``token`` belongs to a session of this store."""
        if not token:
            return False
        digest = _digest(token)
        with self._lock:
            return digest in self._digests
