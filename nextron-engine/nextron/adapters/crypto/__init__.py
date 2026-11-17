"""Backup encryption (AD-9, AD-17, NFR-17, Story 1.17): format v1, a passphrase-derived
AES-256-GCM stream in 64 KiB chunks, over ``cryptography``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``cryptography`` and the standard library.
It is pure computation over the binary streams it is given: it opens no file, reaches nothing
outside this process and logs nothing. The backup format has no port of its own: the folder
adapter (``nextron.adapters.backup_target``) is its only user, and its tests call it directly.

The package also holds the unsubscribe token codec (Story 3.8),
``nextron.adapters.crypto.unsub_token.TokenCodec``: the ``TokenCodecPort`` (AES-256-GCM-SIV and
a truncated HMAC-SHA256 tag), which reads its per-kid keys from Windows Credential Manager
through the ``SecretsPort`` it is given and is otherwise pure computation too.

**Format v1.** A 40-byte header, then the chunks:

- the header is ``MAGIC`` (``NXBK``, 4 bytes) | ``FORMAT_VERSION`` (1) | ``log2_n`` | ``r`` |
  ``p`` (1 byte each) | a salt (16 bytes, ``os.urandom``, fresh for every file) | a key check
  (16 bytes);
- ``scrypt(NFC(passphrase).encode("utf-8"), salt, n=2**log2_n, r, p, length=64)`` gives the
  encryption key (its first 32 bytes) and the check key (its last 32). The key check is
  ``HMAC-SHA256(check key, header[:24])[:16]``, so a wrong passphrase (or a changed parameter or
  salt) is told apart from a changed chunk without the encryption key ever being exposed;
- chunk ``i`` (from 0) is ``AESGCM(encryption key).encrypt(nonce=i.to_bytes(12, "big"),
  plaintext, aad=header || i.to_bytes(8, "big") || bytes([final]))``. Every chunk but the last
  holds exactly 65,536 plaintext bytes; the last has ``final = 1`` and may be shorter (empty
  only for an empty plaintext). Each chunk is its plaintext plus a 16-byte tag. The chunk index
  is the nonce, which is safe because every file has its own salt and so its own key; the index
  and the final flag are also in the chunk's associated data, so a reordered, removed, added or
  cut chunk fails.

``ScryptParams(log2_n, r, p)`` is frozen. ``DEFAULT_SCRYPT`` is ``ScryptParams(17, 8, 1)``
(128 MiB, about 0.3 s). The parameters are stored in each file, so they can rise later; a reader
accepts ``10 <= log2_n <= 18``, ``1 <= r <= 8`` and ``1 <= p <= 4`` only, and so does ``seal``.

- ``seal(source, sink, passphrase, *, params=DEFAULT_SCRYPT) -> Sealed(plain_size,
  plain_sha256, sealed_size)`` reads ``source`` and writes the header and the chunks to
  ``sink``, streaming with a one-chunk read-ahead (it holds at most two chunks).
- ``unseal(source, sink, passphrase) -> Opened(plain_size, plain_sha256)`` writes each chunk's
  plaintext to ``sink`` only after it authenticated. Each failure is a non-retryable
  ``AdapterError`` (codes from ``nextron.ports.backup_target``):

  - ``backup_bad_format``: shorter than the header, another magic or version, or parameters out
    of range;
  - ``backup_wrong_passphrase``: the key check differs (a wrong passphrase, or a changed header
    byte);
  - ``backup_tampered``: a chunk fails authentication (a changed byte, a reordered, extra or
    removed chunk, or a cut inside a chunk);
  - ``backup_truncated``: every chunk authenticates but the last one read isn't marked final (a
    cut at a chunk boundary, or no chunk at all).

  The caller deletes whatever ``sink`` holds after a failure.

A passphrase that isn't valid text (a lone surrogate) is ``backup_wrong_passphrase`` in both.
What ``source`` and ``sink`` raise propagates unchanged: the folder adapter wraps its files so
they raise ``AdapterError``. No message, ``repr`` or traceback holds the passphrase or a key:
every error is raised ``from None`` with this module's own text.
"""

import hashlib
import hmac
import os
import unicodedata
from dataclasses import dataclass
from typing import Final, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from nextron.ports.backup_target import (
    BACKUP_BAD_FORMAT,
    BACKUP_TAMPERED,
    BACKUP_TRUNCATED,
    BACKUP_WRONG_PASSPHRASE,
)
from nextron.ports.errors import AdapterError

MAGIC: Final = b"NXBK"
FORMAT_VERSION: Final = 1
CHUNK_SIZE: Final = 64 * 1024  # plaintext bytes in every chunk but the last
TAG_SIZE: Final = 16
SALT_SIZE: Final = 16
CHECK_SIZE: Final = 16
PREFIX_SIZE: Final = len(MAGIC) + 4 + SALT_SIZE  # what the key check covers: 24 bytes
HEADER_SIZE: Final = PREFIX_SIZE + CHECK_SIZE  # 40 bytes
KEY_SIZE: Final = 32
NONCE_SIZE: Final = 12
INDEX_SIZE: Final = 8
MIN_LOG2_N: Final = 10
MAX_LOG2_N: Final = 18
MAX_R: Final = 8
MAX_P: Final = 4


@dataclass(frozen=True, slots=True)
class ScryptParams:
    """scrypt's cost: ``n = 2**log2_n``, the block size ``r`` and the parallelism ``p``."""

    log2_n: int
    r: int
    p: int

    @property
    def in_range(self) -> bool:
        """Whether a reader accepts these parameters."""
        return all(
            isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
            for value, low, high in (
                (self.log2_n, MIN_LOG2_N, MAX_LOG2_N),
                (self.r, 1, MAX_R),
                (self.p, 1, MAX_P),
            )
        )


DEFAULT_SCRYPT: Final = ScryptParams(17, 8, 1)  # 128 MiB, about 0.3 s


@dataclass(frozen=True, slots=True)
class Sealed:
    """What ``seal`` wrote: the plaintext's size and SHA-256, and the sealed size."""

    plain_size: int
    plain_sha256: str
    sealed_size: int


@dataclass(frozen=True, slots=True)
class Opened:
    """What ``unseal`` wrote: the plaintext's size and SHA-256."""

    plain_size: int
    plain_sha256: str


class Source(Protocol):
    """A binary stream to read from."""

    def read(self, size: int, /) -> bytes: ...


class Sink(Protocol):
    """A binary stream to write to."""

    def write(self, data: bytes, /) -> object: ...


def _read(source: Source, size: int) -> bytes:
    """Up to ``size`` bytes: fewer only at the end of ``source``."""
    parts: list[bytes] = []
    wanted = size
    while wanted > 0:
        part = source.read(wanted)
        if not part:
            break
        parts.append(part)
        wanted -= len(part)
    return b"".join(parts)


def _keys(passphrase: str, salt: bytes, params: ScryptParams) -> tuple[bytes, bytes]:
    """The encryption key and the check key for ``passphrase``, ``salt`` and ``params``."""
    try:
        secret = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    except (TypeError, UnicodeError):
        raise AdapterError(
            False, BACKUP_WRONG_PASSPHRASE, "the backup passphrase is not valid text"
        ) from None
    derived = Scrypt(
        salt=salt, length=2 * KEY_SIZE, n=2**params.log2_n, r=params.r, p=params.p
    ).derive(secret)
    return derived[:KEY_SIZE], derived[KEY_SIZE:]


def _key_check(check_key: bytes, prefix: bytes) -> bytes:
    return hmac.new(check_key, prefix, hashlib.sha256).digest()[:CHECK_SIZE]


def _nonce(index: int) -> bytes:
    return index.to_bytes(NONCE_SIZE, "big")


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + index.to_bytes(INDEX_SIZE, "big") + bytes([1 if final else 0])


def seal(
    source: Source, sink: Sink, passphrase: str, *, params: ScryptParams = DEFAULT_SCRYPT
) -> Sealed:
    """Encrypt all of ``source`` into ``sink`` in format v1; see the module docstring."""
    if not isinstance(params, ScryptParams) or not params.in_range:
        raise AdapterError(False, BACKUP_BAD_FORMAT, "the scrypt parameters are out of range")
    salt = os.urandom(SALT_SIZE)
    prefix = MAGIC + bytes([FORMAT_VERSION, params.log2_n, params.r, params.p]) + salt
    enc_key, check_key = _keys(passphrase, salt, params)
    header = prefix + _key_check(check_key, prefix)
    sink.write(header)
    aead = AESGCM(enc_key)
    digest = hashlib.sha256()
    plain_size, sealed_size, index = 0, HEADER_SIZE, 0
    chunk = _read(source, CHUNK_SIZE)
    while True:
        following = _read(source, CHUNK_SIZE) if len(chunk) == CHUNK_SIZE else b""
        final = not following
        digest.update(chunk)
        plain_size += len(chunk)
        sealed = aead.encrypt(_nonce(index), chunk, _aad(header, index, final))
        sink.write(sealed)
        sealed_size += len(sealed)
        if final:
            return Sealed(plain_size, digest.hexdigest(), sealed_size)
        chunk, index = following, index + 1


def _header_params(header: bytes) -> ScryptParams:
    if len(header) < HEADER_SIZE:
        raise AdapterError(False, BACKUP_BAD_FORMAT, "the file is shorter than a backup header")
    if header[: len(MAGIC)] != MAGIC:
        raise AdapterError(False, BACKUP_BAD_FORMAT, "the file is not a Nextron backup")
    if header[len(MAGIC)] != FORMAT_VERSION:
        raise AdapterError(
            False, BACKUP_BAD_FORMAT, "the backup's format version is not one this code reads"
        )
    start = len(MAGIC) + 1
    params = ScryptParams(header[start], header[start + 1], header[start + 2])
    if not params.in_range:
        raise AdapterError(
            False, BACKUP_BAD_FORMAT, "the backup's scrypt parameters are out of range"
        )
    return params


def _open(aead: AESGCM, block: bytes, index: int, header: bytes, final: bool) -> bytes | None:
    """``block``'s plaintext when it authenticates as chunk ``index`` with ``final``, else
    ``None``."""
    try:
        return aead.decrypt(_nonce(index), block, _aad(header, index, final))
    except InvalidTag:
        return None


def unseal(source: Source, sink: Sink, passphrase: str) -> Opened:
    """Decrypt a format v1 stream from ``source`` into ``sink``; see the module docstring."""
    header = _read(source, HEADER_SIZE)
    params = _header_params(header)
    enc_key, check_key = _keys(passphrase, header[len(MAGIC) + 4 : PREFIX_SIZE], params)
    if not hmac.compare_digest(_key_check(check_key, header[:PREFIX_SIZE]), header[PREFIX_SIZE:]):
        raise AdapterError(
            False, BACKUP_WRONG_PASSPHRASE, "the passphrase does not open this backup"
        ) from None
    aead = AESGCM(enc_key)
    digest = hashlib.sha256()
    plain_size, index, saw_final = 0, 0, False
    block = _read(source, CHUNK_SIZE + TAG_SIZE)
    while block:
        following = _read(source, CHUNK_SIZE + TAG_SIZE)
        if following:  # a later chunk follows, so this one is never the final one
            plain = _open(aead, block, index, header, final=False)
        else:
            plain = _open(aead, block, index, header, final=True)
            saw_final = plain is not None
            if plain is None:
                plain = _open(aead, block, index, header, final=False)
        if plain is None:
            raise AdapterError(
                False, BACKUP_TAMPERED, f"chunk {index} of the backup failed authentication"
            ) from None
        sink.write(plain)
        digest.update(plain)
        plain_size += len(plain)
        block, index = following, index + 1
    if not saw_final:
        raise AdapterError(
            False, BACKUP_TRUNCATED, "the backup ends before its final chunk"
        ) from None
    return Opened(plain_size, digest.hexdigest())
