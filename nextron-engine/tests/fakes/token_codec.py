"""``FakeTokenCodec``: the in-memory fake of ``nextron.ports.token_codec.TokenCodecPort``."""

import base64
import hashlib
import itertools
from collections.abc import Iterable

from nextron.domain.unsub_token import (
    RAW_SIZE,
    RESERVED_KID,
    DecodedToken,
    TokenInvalid,
    TokenInvalidReason,
    parse_token,
)
from nextron.ports.errors import AdapterError
from nextron.ports.token_codec import UNSUB_KEY_MISSING, checked_encode

_COUNTER = itertools.count()  # process-wide, so no two fakes ever give the same token


class FakeTokenCodec:
    """Encodes under the kids in ``kids`` (``{1}`` by default) and decodes only what it encoded.

    Each token has the real shape (108 base64url characters, the kid in its first byte); the
    rest is a process-wide counter's SHA-256, so no two calls, on any fake, give the same
    token. ``encode`` checks its
    arguments as the port says, and raises ``AdapterError`` ``unsub_key_missing`` for a kid not
    in ``kids``. ``decode`` gives ``malformed`` for any other shape, ``unknown_kid`` for kid 0
    or a kid not in ``kids``, ``bad_tag`` for a well-shaped token it didn't encode, and the
    recorded ``DecodedToken`` otherwise. Set ``fail`` to an ``AdapterError`` to make either
    method raise it (a Windows Credential Manager failure). ``encoded`` records every
    ``(kid, addr_hash, issued_at)`` it encoded, ``decoded`` every token it was asked about.
    """

    def __init__(self, kids: Iterable[int] = (1,)) -> None:
        self.kids = set(kids)
        self.fail: AdapterError | None = None
        self.encoded: list[tuple[int, bytes, int]] = []
        self.decoded: list[object] = []
        self._tokens: dict[str, DecodedToken] = {}

    def encode(self, kid: int, addr_hash: bytes, issued_at: int) -> str:
        kid, addr_hash, issued_at = checked_encode(kid, addr_hash, issued_at)
        if self.fail is not None:
            raise self.fail
        if kid not in self.kids:
            raise AdapterError(
                False, UNSUB_KEY_MISSING, f"unsubscribe token key unsub/k_tok/{kid} is not set"
            )
        counter = next(_COUNTER).to_bytes(8, "big")
        body = hashlib.sha256(b"fake-token" + counter).digest()
        body += hashlib.sha256(body).digest()
        raw = bytes([kid]) + body + hashlib.sha256(body).digest()
        token = base64.urlsafe_b64encode(raw[:RAW_SIZE]).rstrip(b"=").decode("ascii")
        self._tokens[token] = DecodedToken(kid, addr_hash, issued_at)
        self.encoded.append((kid, addr_hash, issued_at))
        return token

    def decode(self, token: str) -> DecodedToken | TokenInvalid:
        self.decoded.append(token)
        raw = parse_token(token)
        if raw is None:
            return TokenInvalid(TokenInvalidReason.MALFORMED)
        if self.fail is not None:
            raise self.fail
        if raw.kid == RESERVED_KID or raw.kid not in self.kids:
            return TokenInvalid(TokenInvalidReason.UNKNOWN_KID)
        found = self._tokens.get(token)
        return found if found is not None else TokenInvalid(TokenInvalidReason.BAD_TAG)
