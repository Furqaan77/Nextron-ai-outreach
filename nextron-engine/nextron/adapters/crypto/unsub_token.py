"""The unsubscribe token codec (AD-9, AD-15, FR-70, NFR-17, Story 3.8): ``TokenCodec``, the
``TokenCodecPort`` over ``cryptography``'s AES-256-GCM-SIV and the standard library's HMAC.

Import rule: ``nextron.ports``, ``nextron.domain``, ``cryptography`` and the standard library.
The byte format is ``nextron.domain.unsub_token``'s; this module adds only the cipher. It touches
no database, opens no file and logs nothing.

``TokenCodec(secrets, *, nonce=os.urandom)`` reads each key at call time through
``nextron.ports.secrets.read_unsub_key`` and never caches one. ``nonce(12)`` gives each token's
nonce (tests inject a fixed one to reproduce the vectors); AES-GCM-SIV stays safe even if a
nonce ever repeated, apart from showing that two tokens hold the same plaintext.

- ``encode(kid, addr_hash, issued_at)``: ``checked_encode`` first (``TypeError`` or
  ``ValueError``), then ``k_tok`` and ``k_edge`` of ``kid``; either missing or malformed raises
  ``AdapterError(False, "unsub_key_missing", "unsubscribe token key unsub/<key>/<kid> is not set
  or not a 32-byte base64url key")``, and so do two equal keys (compared in constant time),
  naming both entries: Cloudflare holds ``k_edge``, so it must never be the cipher key. Then
  ``ct = AESGCMSIV(k_tok).encrypt(nonce, addr_hash || issued_at, None)`` and the token is
  ``assemble(kid, nonce, ct, k_edge)``.
- ``decode(token)`` checks, in this order, and returns the first ``TokenInvalid`` it finds:

  1. the shape (``parse_token``), otherwise ``malformed``;
  2. the kid: 0, or no valid ``k_edge``, is ``unknown_kid``;
  3. the tag, compared with ``hmac.compare_digest`` before any decryption: ``bad_tag``;
  4. ``k_tok``: missing or malformed is ``unknown_kid``;
  5. the decryption: ``InvalidTag`` is ``bad_ciphertext``.

  Otherwise it returns ``DecodedToken(kid, addr_hash, issued_at)``. It never raises for any
  token value; only a Windows Credential Manager failure (``AdapterError``) propagates.

No error, ``repr`` or log holds a key, a token or an address hash.
"""

import hmac
import os
from collections.abc import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV

from nextron.domain.credentials import UnsubKey, unsub_key
from nextron.domain.unsub_token import (
    NONCE_SIZE,
    RESERVED_KID,
    DecodedToken,
    TokenInvalid,
    TokenInvalidReason,
    assemble,
    check_nonce,
    parse_token,
    plaintext,
    split_plaintext,
    tag_matches,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort, read_unsub_key
from nextron.ports.token_codec import UNSUB_KEY_MISSING, checked_encode

Nonce = Callable[[int], bytes]


class TokenCodec:
    """Unsubscribe tokens under the per-kid keys in Windows Credential Manager."""

    def __init__(self, secrets: SecretsPort, *, nonce: Nonce = os.urandom) -> None:
        self._secrets = secrets
        self._nonce = nonce

    def __repr__(self) -> str:
        return "TokenCodec()"

    def _required_key(self, key: UnsubKey, kid: int) -> bytes:
        found = read_unsub_key(self._secrets, key, kid)
        if found is None:
            raise AdapterError(
                False,
                UNSUB_KEY_MISSING,
                f"unsubscribe token key {unsub_key(key).secret_name(kid)} is not set or not a "
                "32-byte base64url key",
            )
        return found

    def encode(self, kid: int, addr_hash: bytes, issued_at: int) -> str:
        """A new token; see the module docstring."""
        kid, addr_hash, issued_at = checked_encode(kid, addr_hash, issued_at)
        k_tok = self._required_key(UnsubKey.K_TOK, kid)
        k_edge = self._required_key(UnsubKey.K_EDGE, kid)
        if hmac.compare_digest(k_tok, k_edge):  # Cloudflare holds k_edge: never the cipher key
            raise AdapterError(
                False,
                UNSUB_KEY_MISSING,
                f"unsubscribe token keys {unsub_key(UnsubKey.K_TOK).secret_name(kid)} and "
                f"{unsub_key(UnsubKey.K_EDGE).secret_name(kid)} are the same key",
            )
        nonce = check_nonce(self._nonce(NONCE_SIZE))
        ciphertext = AESGCMSIV(k_tok).encrypt(nonce, plaintext(addr_hash, issued_at), None)
        return assemble(kid, nonce, ciphertext, k_edge)

    def decode(self, token: str) -> DecodedToken | TokenInvalid:
        """What ``token`` holds, or why it doesn't decode; see the module docstring."""
        raw = parse_token(token)
        if raw is None:
            return TokenInvalid(TokenInvalidReason.MALFORMED)
        if raw.kid == RESERVED_KID:
            return TokenInvalid(TokenInvalidReason.UNKNOWN_KID)
        k_edge = read_unsub_key(self._secrets, UnsubKey.K_EDGE, raw.kid)
        if k_edge is None:
            return TokenInvalid(TokenInvalidReason.UNKNOWN_KID)
        if not tag_matches(k_edge, raw):
            return TokenInvalid(TokenInvalidReason.BAD_TAG)
        k_tok = read_unsub_key(self._secrets, UnsubKey.K_TOK, raw.kid)
        if k_tok is None:
            return TokenInvalid(TokenInvalidReason.UNKNOWN_KID)
        try:
            plain = AESGCMSIV(k_tok).decrypt(raw.nonce, raw.ciphertext, None)
        except InvalidTag:
            return TokenInvalid(TokenInvalidReason.BAD_CIPHERTEXT)
        addr_hash, issued_at = split_plaintext(plain)
        return DecodedToken(raw.kid, addr_hash, issued_at)
