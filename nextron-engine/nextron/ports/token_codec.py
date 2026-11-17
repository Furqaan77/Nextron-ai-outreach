"""The ``TokenCodecPort`` (AD-9, AD-15, FR-70, Story 3.8): unsubscribe tokens, encoded and
decoded.

The format is ``nextron.domain.unsub_token``'s. ``nextron.adapters.crypto.unsub_token.TokenCodec``
is the adapter (AES-256-GCM-SIV and HMAC-SHA256 over ``cryptography`` and the standard library);
tests use ``tests.fakes.token_codec.FakeTokenCodec``. Neither touches a database.

- ``encode(kid, addr_hash, issued_at) -> str`` returns a new token. Every implementation first
  passes its arguments through ``checked_encode``, which raises ``TypeError`` or ``ValueError``
  for a bad argument (a programming error, not an adapter failure, so it is no
  ``AdapterError``). A key of ``kid`` that is missing or isn't a 32-byte base64url key raises
  ``AdapterError(False, UNSUB_KEY_MISSING, ...)``, whose message names the keyring entry
  (``unsub/k_tok/<kid>``), never a value.
- ``decode(token) -> DecodedToken | TokenInvalid`` never raises for any token value, a
  non-``str`` included (``malformed``).
- Either method lets a Windows Credential Manager failure (``AdapterError``, from
  ``SecretsPort.read``) propagate: a decode that couldn't read a key is never called an invalid
  token, so the caller can retry.

Keys are read at call time through ``nextron.ports.secrets.read_unsub_key`` and never kept. No
token, key or address hash is ever logged.
"""

from typing import Final, Protocol, runtime_checkable

from nextron.domain.unsub_token import (
    DecodedToken,
    TokenInvalid,
    check_addr_hash,
    check_issued_at,
    check_kid,
)

UNSUB_KEY_MISSING: Final = "unsub_key_missing"


@runtime_checkable
class TokenCodecPort(Protocol):
    """Encodes and decodes unsubscribe tokens."""

    def encode(self, kid: int, addr_hash: bytes, issued_at: int) -> str:
        """A new token for ``addr_hash`` issued at ``issued_at`` (Unix seconds) under ``kid``."""
        ...

    def decode(self, token: str) -> DecodedToken | TokenInvalid:
        """What ``token`` holds, or why it doesn't decode; never raises for any token."""
        ...


def checked_encode(kid: object, addr_hash: object, issued_at: object) -> tuple[int, bytes, int]:
    """``(kid, addr_hash, issued_at)`` when ``kid`` is 1 to 255, ``addr_hash`` 32 bytes and
    ``issued_at`` an ``int`` from 0 to 2**64 - 1; ``TypeError`` or ``ValueError`` otherwise."""
    return check_kid(kid), check_addr_hash(addr_hash), check_issued_at(issued_at)
