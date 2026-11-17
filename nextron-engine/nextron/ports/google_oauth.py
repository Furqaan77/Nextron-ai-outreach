"""The Google OAuth ports (AD-9, AD-12, INT-2, INT-3): Google's token endpoints, the one-shot
consent listener and the downloaded client file.

``nextron.adapters.google_oauth`` holds the live adapters; ``tests/fakes/google_oauth.py`` holds
the fakes. ``nextron.app.setup.api.GoogleAccess`` drives them.

**``GoogleOAuthPort``** calls Google's token and revoke endpoints
(``nextron.domain.google_oauth``):

- ``exchange(client, code, verifier, redirect_uri)`` trades an authorization code (with its
  PKCE verifier) for a ``TokenSet``;
- ``refresh(client, refresh_token)`` gets a fresh access token;
- ``revoke(token)`` revokes a refresh or access token. Google's answer 200, and a 400 whose
  ``error`` is ``invalid_token`` (the token is already gone), both count as done.

Its errors are ``AdapterError`` with one of these codes:

- ``google_invalid_grant`` (not retryable): Google refused the code or the refresh token;
- ``google_rejected`` (not retryable): any other 4xx;
- ``google_unavailable`` (retryable): a network failure, a timeout, a 429 or a 5xx;
- ``google_bad_response`` (not retryable): a 2xx that isn't a JSON object with a non-empty
  ``access_token`` and an integer ``expires_in`` from 1 to 86,400 (a day), or any other
  status.

A ``TokenSet`` leaves every token out of its ``repr``.

**``ConsentListener``** is one flow's listener on ``127.0.0.1``: ``redirect_uri()`` is where
Google sends the browser back; ``wait(timeout)`` returns the query of the first request to
``/`` (each field with all its values, blank ones kept), or ``None`` on timeout or once it is
closed; ``close()`` stops it and may be called any number of times. Building one that can't
bind raises ``AdapterError`` ``listener_failed``.

**``ClientFilePort``** reads and deletes the downloaded ``client_secret.json``:
``read(path, limit)`` returns the bytes of a regular file of at most ``limit`` bytes;
``delete(path)`` removes it, and does nothing when it is already gone. The codes are
``client_file_missing``, ``client_file_not_regular`` (a folder, a link or a reparse point),
``client_file_too_large``, ``client_file_unreadable`` and ``client_file_not_deleted``, all not
retryable.

No error message, log record or ``repr`` holds a token, a code, a verifier, a URL with a query,
the client secret or a path.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from nextron.domain.google_oauth import ClientConfig

GOOGLE_INVALID_GRANT: Final = "google_invalid_grant"
GOOGLE_REJECTED: Final = "google_rejected"
GOOGLE_UNAVAILABLE: Final = "google_unavailable"
GOOGLE_BAD_RESPONSE: Final = "google_bad_response"
LISTENER_FAILED: Final = "listener_failed"

CLIENT_FILE_MISSING: Final = "client_file_missing"
CLIENT_FILE_NOT_REGULAR: Final = "client_file_not_regular"
CLIENT_FILE_TOO_LARGE: Final = "client_file_too_large"
CLIENT_FILE_UNREADABLE: Final = "client_file_unreadable"
CLIENT_FILE_NOT_DELETED: Final = "client_file_not_deleted"

# Raised by GoogleAccess.access_token, never retryable.
GOOGLE_CLIENT_NOT_IMPORTED: Final = "google_client_not_imported"
GOOGLE_NOT_GRANTED: Final = "google_not_granted"
GOOGLE_NEEDS_RECONSENT: Final = "google_needs_reconsent"

Query = Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class TokenSet:
    """What Google's token endpoint answered; every token is left out of ``repr``."""

    access_token: str = field(repr=False)
    expires_in: int
    scope: str
    refresh_token: str | None = field(default=None, repr=False)
    id_token: str | None = field(default=None, repr=False)


@runtime_checkable
class GoogleOAuthPort(Protocol):
    """Google's token and revoke endpoints."""

    def exchange(
        self, client: ClientConfig, code: str, verifier: str, redirect_uri: str
    ) -> TokenSet:
        """Trade an authorization code and its PKCE verifier for tokens."""
        ...

    def refresh(self, client: ClientConfig, refresh_token: str) -> TokenSet:
        """A fresh access token for ``refresh_token``."""
        ...

    def revoke(self, token: str) -> None:
        """Revoke ``token`` at Google; an already-revoked token counts as done."""
        ...


@runtime_checkable
class ConsentListener(Protocol):
    """One consent flow's one-shot listener on ``127.0.0.1``."""

    def redirect_uri(self) -> str:
        """Where Google sends the browser back: ``http://127.0.0.1:<port>/``."""
        ...

    def wait(self, timeout: float) -> Query | None:
        """The query of the first request to ``/``, or ``None`` on timeout or close."""
        ...

    def close(self) -> None:
        """Stop listening; calling it again does nothing."""
        ...


@runtime_checkable
class ClientFilePort(Protocol):
    """The downloaded ``client_secret.json`` on this PC."""

    def read(self, path: str, limit: int) -> bytes:
        """The bytes of the regular file at ``path``, at most ``limit`` of them."""
        ...

    def delete(self, path: str) -> None:
        """Delete the file at ``path``; do nothing when it is already gone."""
        ...
