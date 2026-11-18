"""Unsubscribe tokens (FR-70, AD-15, AD-17, NFR-17, NFR-35, Story 3.8): the byte format.
Standard library only, plus ``nextron.domain``.

Every Step's ``List-Unsubscribe`` link carries one opaque token. It identifies only a hashed
address, so a click opts the person out without the address ever leaving the PC. The engine
encodes and decodes tokens through ``nextron.ports.token_codec.TokenCodecPort`` (the
``cryptography`` adapter is ``nextron.adapters.crypto.unsub_token.TokenCodec``); the
unsubscribe Worker (Story 3.10) checks only the tag. ``tests/fixtures/unsub_token_vectors.json``
binds every implementation to this format.

**The format.** All integers are unsigned big-endian.

- ``raw`` = ``kid`` (1 byte) || ``nonce`` (12 bytes) || ``ct`` (56 bytes) || ``tag`` (12 bytes):
  ``RAW_SIZE``, 81 bytes.
- ``ct`` = AES-256-GCM-SIV under ``k_tok[kid]``, with that nonce and no associated data, of
  ``addr_hash`` (32 bytes) || ``issued_at`` (8 bytes, Unix seconds): 40 bytes of plaintext,
  56 of ciphertext (the 16-byte GCM-SIV tag included).
- ``tag`` = the first 12 bytes of HMAC-SHA256 under ``k_edge[kid]`` over ``raw[:69]`` (``kid``,
  ``nonce`` and ``ct``), so the tag also covers the kid and no associated data is needed.
- The token is base64url of ``raw`` without padding: exactly ``TOKEN_LENGTH`` (108) characters
  of ``[A-Za-z0-9_-]``.
- ``kid`` is 1 to 255 (``MIN_KID``..``MAX_KID``). 0 (``RESERVED_KID``) is never a known kid,
  so a zeroed buffer always reads as ``unknown_kid``.
- ``addr_hash_of(address)`` is ``bytes.fromhex(key_hash("address", address))``
  (``nextron.domain.contactability``): the Suppression List's address key, as bytes, the
  SHA-256 of ``address:`` plus ``identity.normalize_email(address)`` (stripped, NFC'd and
  lowercased, with its domain in IDNA ASCII). Only the engine computes it; the Worker checks
  only the tag.
- ``token_hash(token)`` is the lowercase hex SHA-256 of the token's ASCII bytes: the only form
  of a token the database ever holds (``unsub_tokens.token_hash``; Story 3.11 reuses it).

**Helpers.** ``check_kid``, ``check_addr_hash``, ``check_issued_at`` and ``check_nonce`` return
their argument or raise ``TypeError`` (wrong type; a ``bool`` is no ``int``) or ``ValueError``
(out of range or the wrong length). ``plaintext(addr_hash, issued_at)`` and
``split_plaintext(plain)`` build and read the 40 plaintext bytes. ``edge_tag(k_edge, authed)``
is the 12-byte tag; ``assemble(kid, nonce, ct, k_edge)`` the token text. ``parse_token(token)``
is the token's ``RawToken(kid, nonce, ct, tag)``, or ``None`` when it isn't a token's shape (not
a ``str``, another length, a character outside the alphabet, padding); ``tag_matches(k_edge,
raw)`` compares the tag with ``hmac.compare_digest``.

**Decoding results.** ``DecodedToken(kid, addr_hash, issued_at)`` and ``TokenInvalid(reason)``
are frozen. ``TokenInvalidReason`` is ``malformed`` (the shape), ``unknown_kid`` (kid 0, or a kid
without a valid key), ``bad_tag`` (the tag doesn't verify) or ``bad_ciphertext`` (the tag
verifies but the ciphertext doesn't decrypt).

**Keys.** Each kid has two 32-byte keys in Windows Credential Manager (``unsub/k_tok/<kid>`` and
``unsub/k_edge/<kid>``, ``nextron.domain.credentials``), stored as canonical base64url text.
``parse_key(text)`` is the 32 bytes of such a text (43 characters, or 44 with one trailing
``=``, and re-encoding gives the same text, so the unused low bits are zero), and ``None`` for
anything else; ``encode_key(raw)`` is the canonical unpadded text of 32 bytes.

**Key life.** A kid's keys must keep working for 2 years after its newest token
(``KEY_MIN_LIFE``, 731 days). ``key_in_use(newest_issued_at, now)`` is ``True`` while a kid's
newest token is younger than that (or in the future); ``None`` (no token) is never in use.
While it holds, the Credentials page refuses to replace or remove the kid's keys with
``UnsubKeyInUse`` (code ``unsub_key_in_use``, "Key <kid> still has tokens under 2 years old").
After a restore, while recovery mode is on (Story 3.12), replacing or removing a key is refused
with ``UnsubKeysHeldInRecovery`` (code ``unsub_keys_held_in_recovery``): the restored
``unsub_tokens`` can't be rebuilt, so the keys of the tokens already sent must stay as they are.
``TokenAlreadyIssued`` (code ``unsub_token_already_issued``) is ``Compliance.issue_unsub_token``
refusing a second token for one message. No message here ever holds a token, a key, an address
or an address hash.
"""

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from nextron.domain.contactability import KeyKind, key_hash
from nextron.domain.errors import DomainError

KID_SIZE: Final = 1
NONCE_SIZE: Final = 12
ADDR_HASH_SIZE: Final = 32
ISSUED_AT_SIZE: Final = 8
PLAINTEXT_SIZE: Final = ADDR_HASH_SIZE + ISSUED_AT_SIZE  # 40
SIV_TAG_SIZE: Final = 16  # AES-GCM-SIV's own tag, part of ct
CIPHERTEXT_SIZE: Final = PLAINTEXT_SIZE + SIV_TAG_SIZE  # 56
AUTHED_SIZE: Final = KID_SIZE + NONCE_SIZE + CIPHERTEXT_SIZE  # 69: what the tag covers
TAG_SIZE: Final = 12
RAW_SIZE: Final = AUTHED_SIZE + TAG_SIZE  # 81
TOKEN_LENGTH: Final = 108  # base64url of 81 bytes, unpadded
KEY_SIZE: Final = 32
KEY_TEXT_LENGTH: Final = 43  # base64url of 32 bytes, unpadded
RESERVED_KID: Final = 0
MIN_KID: Final = 1
MAX_KID: Final = 255
MAX_ISSUED_AT: Final = 2**64 - 1
KEY_MIN_LIFE: Final = timedelta(days=731)  # "2 years", leap day included

TOKEN_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{108}", re.ASCII)  # matched with fullmatch
KEY_TEXT_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{43}=?", re.ASCII)  # matched with fullmatch


class TokenInvalidReason(StrEnum):
    """Why a token doesn't decode, in the order decoding checks."""

    MALFORMED = "malformed"
    UNKNOWN_KID = "unknown_kid"
    BAD_TAG = "bad_tag"
    BAD_CIPHERTEXT = "bad_ciphertext"


@dataclass(frozen=True, slots=True)
class TokenInvalid:
    """A token that doesn't decode, and why."""

    reason: TokenInvalidReason


@dataclass(frozen=True, slots=True)
class DecodedToken:
    """What a valid token holds: its kid, the address hash (32 bytes) and its Unix second."""

    kid: int
    addr_hash: bytes
    issued_at: int


@dataclass(frozen=True, slots=True)
class RawToken:
    """A token's parts, split but not yet verified."""

    kid: int
    nonce: bytes
    ciphertext: bytes
    tag: bytes

    @property
    def authed(self) -> bytes:
        """The 69 bytes the tag covers: ``kid || nonce || ct``."""
        return bytes([self.kid]) + self.nonce + self.ciphertext


# Errors ---------------------------------------------------------------------------------------


class UnsubKeyInUse(DomainError):
    """A kid's key can't be replaced or removed: it has a token under 2 years old."""

    code = "unsub_key_in_use"

    def __init__(self, kid: int) -> None:
        super().__init__(f"Key {kid} still has tokens under 2 years old")
        self.kid = kid


class UnsubKeysHeldInRecovery(DomainError):
    """A key can't be replaced or removed while recovery mode is on (Story 3.12)."""

    code = "unsub_keys_held_in_recovery"

    def __init__(self, kid: int) -> None:
        super().__init__(
            f"Key {kid} can't be replaced or removed while recovery mode is on after a restore"
        )
        self.kid = kid


class TokenAlreadyIssued(DomainError):
    """A message already has its unsubscribe token; a second one is never issued."""

    code = "unsub_token_already_issued"

    def __init__(self) -> None:
        super().__init__("token already issued for this message")


# Checks ---------------------------------------------------------------------------------------


def check_kid(kid: object) -> int:
    """``kid`` itself when it is an ``int`` from 1 to 255; ``TypeError`` or ``ValueError``
    otherwise."""
    if not isinstance(kid, int) or isinstance(kid, bool):
        raise TypeError(f"a kid is an int, not {type(kid).__name__}")
    if not MIN_KID <= kid <= MAX_KID:
        raise ValueError(f"a kid is {MIN_KID} to {MAX_KID}")
    return kid


def check_addr_hash(addr_hash: object) -> bytes:
    """``addr_hash`` as ``bytes`` when it is 32 bytes; ``TypeError`` or ``ValueError``
    otherwise."""
    if not isinstance(addr_hash, bytes):
        raise TypeError(f"an addr_hash is bytes, not {type(addr_hash).__name__}")
    if len(addr_hash) != ADDR_HASH_SIZE:
        raise ValueError(f"an addr_hash is {ADDR_HASH_SIZE} bytes")
    return addr_hash


def check_issued_at(issued_at: object) -> int:
    """``issued_at`` itself when it is an ``int`` from 0 to 2**64 - 1 (Unix seconds);
    ``TypeError`` or ``ValueError`` otherwise."""
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        raise TypeError(f"issued_at is an int of Unix seconds, not {type(issued_at).__name__}")
    if not 0 <= issued_at <= MAX_ISSUED_AT:
        raise ValueError("issued_at is 0 to 2**64 - 1 Unix seconds")
    return issued_at


def check_nonce(nonce: object) -> bytes:
    """``nonce`` when it is 12 bytes; ``TypeError`` or ``ValueError`` otherwise."""
    if not isinstance(nonce, bytes):
        raise TypeError(f"a nonce is bytes, not {type(nonce).__name__}")
    if len(nonce) != NONCE_SIZE:
        raise ValueError(f"a nonce is {NONCE_SIZE} bytes")
    return nonce


# The format -----------------------------------------------------------------------------------


def addr_hash_of(address: str) -> bytes:
    """The 32-byte address hash of ``address``: the Suppression List's address key, as bytes.
    Raises what ``key_hash`` raises (``TypeError``, or an ``IdentityError`` for an address
    ``normalize_email`` refuses)."""
    return bytes.fromhex(key_hash(KeyKind.ADDRESS, address))


def plaintext(addr_hash: bytes, issued_at: int) -> bytes:
    """The 40 plaintext bytes: ``addr_hash || issued_at`` (8 bytes, big-endian)."""
    return check_addr_hash(addr_hash) + check_issued_at(issued_at).to_bytes(ISSUED_AT_SIZE, "big")


def split_plaintext(plain: bytes) -> tuple[bytes, int]:
    """The ``(addr_hash, issued_at)`` of 40 plaintext bytes; ``ValueError`` for another
    length."""
    if len(plain) != PLAINTEXT_SIZE:
        raise ValueError(f"a token's plaintext is {PLAINTEXT_SIZE} bytes")
    return plain[:ADDR_HASH_SIZE], int.from_bytes(plain[ADDR_HASH_SIZE:], "big")


def edge_tag(k_edge: bytes, authed: bytes) -> bytes:
    """The first 12 bytes of HMAC-SHA256 under ``k_edge`` over ``authed``."""
    return hmac.new(k_edge, authed, hashlib.sha256).digest()[:TAG_SIZE]


def assemble(kid: int, nonce: bytes, ciphertext: bytes, k_edge: bytes) -> str:
    """The token text of ``kid``, ``nonce`` and ``ciphertext`` (56 bytes), tagged under
    ``k_edge``."""
    if len(ciphertext) != CIPHERTEXT_SIZE:
        raise ValueError(f"a token's ciphertext is {CIPHERTEXT_SIZE} bytes")
    authed = bytes([check_kid(kid)]) + check_nonce(nonce) + ciphertext
    raw = authed + edge_tag(k_edge, authed)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def parse_token(token: object) -> RawToken | None:
    """The parts of ``token``, or ``None`` when it isn't a token's shape: not a ``str``, not
    exactly 108 characters of ``[A-Za-z0-9_-]``. Nothing is verified here."""
    if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
        return None
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    # 108 characters always carry exactly 81 bytes (108 * 6 = 648 = 81 * 8): no stray bits.
    return RawToken(
        kid=raw[0],
        nonce=raw[KID_SIZE : KID_SIZE + NONCE_SIZE],
        ciphertext=raw[KID_SIZE + NONCE_SIZE : AUTHED_SIZE],
        tag=raw[AUTHED_SIZE:],
    )


def tag_matches(k_edge: bytes, raw: RawToken) -> bool:
    """Whether ``raw``'s tag is the tag of its first 69 bytes under ``k_edge`` (constant
    time)."""
    return hmac.compare_digest(edge_tag(k_edge, raw.authed), raw.tag)


def token_hash(token: str) -> str:
    """The lowercase hex SHA-256 of ``token``'s ASCII bytes. Raises ``TypeError`` for a
    non-``str`` and ``ValueError`` (``UnicodeEncodeError``) for a non-ASCII one."""
    if not isinstance(token, str):
        raise TypeError(f"a token is a str, not {type(token).__name__}")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


# Keys -----------------------------------------------------------------------------------------


def encode_key(raw: bytes) -> str:
    """The canonical text of a 32-byte key: base64url without padding, 43 characters."""
    if not isinstance(raw, bytes):
        raise TypeError(f"a key is bytes, not {type(raw).__name__}")
    if len(raw) != KEY_SIZE:
        raise ValueError(f"a key is {KEY_SIZE} bytes")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def parse_key(text: object) -> bytes | None:
    """The 32 bytes of a canonical base64url key text (43 characters, or 44 with one trailing
    ``=``); ``None`` for anything else (another type, length or alphabet, standard base64,
    spaces, or non-zero unused bits)."""
    if not isinstance(text, str) or KEY_TEXT_PATTERN.fullmatch(text) is None:
        return None
    body = text.removesuffix("=")
    raw = base64.urlsafe_b64decode(body + "=")  # the pattern admits only the alphabet
    if len(raw) != KEY_SIZE or encode_key(raw) != body:
        return None
    return raw


def key_in_use(newest_issued_at: datetime | None, now: datetime) -> bool:
    """Whether a kid whose newest token was issued at ``newest_issued_at`` still has a token
    under 2 years (``KEY_MIN_LIFE``) old at ``now``; ``None`` (no token) is never in use."""
    if newest_issued_at is None:
        return False
    return now - newest_issued_at < KEY_MIN_LIFE
