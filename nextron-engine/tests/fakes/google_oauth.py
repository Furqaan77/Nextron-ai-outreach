"""Fakes of the Google OAuth ports (``nextron.ports.google_oauth``) and an unsigned ID token
builder.

- ``FakeGoogleOAuth`` (``GoogleOAuthPort``) answers ``exchange`` and ``refresh`` with the
  ``TokenSet`` (or raises the ``AdapterError``) a test queued in ``exchanges`` and
  ``refreshes``, else with a default set; ``revoke`` raises ``revoke_error`` when it is set.
  ``calls`` records ``(operation, argument)`` for every call (``exchange``: the code;
  ``refresh`` and ``revoke``: the token), and ``verifiers`` and ``redirect_uris`` what each
  exchange was given. It never reaches Google.
- ``FakeConsentListener`` (``ConsentListener``): ``answer`` is what ``wait`` returns (``None``,
  the default, is a timeout); ``waited`` records each timeout asked for and ``closes`` counts
  ``close`` calls. ``redirect_uri()`` is ``http://127.0.0.1:<port>/`` on a fixed fake port.
- ``FakeClientFiles`` (``ClientFilePort``): ``files`` maps a path to its bytes; ``read_error`` and
  ``delete_error`` name an error code to raise; ``reads`` and ``deleted`` record the paths.
- ``id_token(email, ...)`` builds an unsigned JWT whose payload holds the claims Google sends;
  each keyword overrides one claim (``None`` drops it).
"""

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from nextron.domain.google_oauth import ClientConfig
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import CLIENT_FILE_MISSING, Query, TokenSet

CLIENT_ID = "123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com"
FAKE_PORT = 53682
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
_DROP = object()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def id_token(
    email: str | None = "mac@nextronai.com",
    *,
    aud: object = CLIENT_ID,
    iss: object = "https://accounts.google.com",
    exp: object = None,
    email_verified: object = True,
    **extra: object,
) -> str:
    """An unsigned ID token (``header.payload.signature``) with these claims; ``exp`` defaults
    to the year 2100. A claim given as ``None`` is left out."""
    claims: dict[str, object] = {
        "iss": iss,
        "aud": aud,
        "sub": "1234567890",
        "email": email,
        "email_verified": email_verified,
        "exp": int(FAR_FUTURE.timestamp()) if exp is None else exp,
        "iat": int((FAR_FUTURE - timedelta(hours=1)).timestamp()),
        **extra,
    }
    payload = {name: value for name, value in claims.items() if value is not None}
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    return f"{header}.{_b64(json.dumps(payload).encode())}.{_b64(b'not-a-signature')}"


def token_set(
    access_token: str = "ya29.fake-access",
    *,
    scope: str = "https://www.googleapis.com/auth/gmail.send openid email",
    refresh_token: str | None = "1//fake-refresh",
    email: str | None = "mac@nextronai.com",
    expires_in: int = 3599,
    id_token_text: str | None = None,
) -> TokenSet:
    """A ``TokenSet`` as Google answers it; ``id_token_text`` overrides the ID token built for
    ``email``."""
    return TokenSet(
        access_token=access_token,
        expires_in=expires_in,
        scope=scope,
        refresh_token=refresh_token,
        id_token=id_token_text if id_token_text is not None else id_token(email),
    )


class FakeGoogleOAuth:
    def __init__(self) -> None:
        self.exchanges: list[TokenSet | AdapterError] = []
        self.refreshes: list[TokenSet | AdapterError] = []
        self.revoke_error: AdapterError | None = None
        self.calls: list[tuple[str, str]] = []
        self.verifiers: list[str] = []
        self.redirect_uris: list[str] = []
        self.clients: list[ClientConfig] = []

    @staticmethod
    def _answer(queue: list[TokenSet | AdapterError]) -> TokenSet:
        answer = queue.pop(0) if queue else token_set()
        if isinstance(answer, AdapterError):
            raise answer
        return answer

    def exchange(
        self, client: ClientConfig, code: str, verifier: str, redirect_uri: str
    ) -> TokenSet:
        self.calls.append(("exchange", code))
        self.clients.append(client)
        self.verifiers.append(verifier)
        self.redirect_uris.append(redirect_uri)
        return self._answer(self.exchanges)

    def refresh(self, client: ClientConfig, refresh_token: str) -> TokenSet:
        self.calls.append(("refresh", refresh_token))
        self.clients.append(client)
        return self._answer(self.refreshes)

    def revoke(self, token: str) -> None:
        self.calls.append(("revoke", token))
        if self.revoke_error is not None:
            raise self.revoke_error

    @property
    def revoked(self) -> list[str]:
        return [token for operation, token in self.calls if operation == "revoke"]


class FakeConsentListener:
    def __init__(self, answer: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self.answer: Query | None = answer
        self.waited: list[float] = []
        self.closes = 0

    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{FAKE_PORT}/"

    def wait(self, timeout: float) -> Query | None:
        self.waited.append(timeout)
        return self.answer

    def close(self) -> None:
        self.closes += 1

    @property
    def closed(self) -> bool:
        return self.closes > 0


class FakeClientFiles:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.read_error: str | None = None
        self.delete_error: str | None = None
        self.reads: list[tuple[str, int]] = []
        self.deleted: list[str] = []

    def read(self, path: str, limit: int) -> bytes:
        self.reads.append((path, limit))
        if self.read_error is not None:
            raise AdapterError(False, self.read_error, "the client file could not be read")
        if path not in self.files:
            raise AdapterError(False, CLIENT_FILE_MISSING, "there is no client file")
        return self.files[path]

    def delete(self, path: str) -> None:
        if self.delete_error is not None:
            raise AdapterError(False, self.delete_error, "the client file could not be deleted")
        self.deleted.append(path)
        self.files.pop(path, None)
