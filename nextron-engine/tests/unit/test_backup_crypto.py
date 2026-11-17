"""``nextron.adapters.crypto`` (Story 1.17): backup format v1. Round trips of 0, 1, 65,536,
65,537 bytes and three chunks; the header's fields; a fresh salt per file; every decrypt error
of the story's matrix (a changed header, tag or body byte; a cut at a chunk boundary and inside
a chunk; an extra chunk; swapped chunks; parameters out of range; another magic or version; a
wrong passphrase); NFC equivalence; and the default parameters deriving a key. Every test uses
``ScryptParams(10, 8, 1)`` except the one about the default."""

import hashlib
import hmac
import io
import os
import traceback

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from nextron.adapters import crypto
from nextron.adapters.crypto import (
    CHUNK_SIZE,
    DEFAULT_SCRYPT,
    HEADER_SIZE,
    MAGIC,
    TAG_SIZE,
    Opened,
    ScryptParams,
    Sealed,
    seal,
    unseal,
)
from nextron.ports.errors import AdapterError

LOW = ScryptParams(10, 8, 1)
PASSPHRASE = "correct horse battery staple QZXPLANTEDPASSPHRASE"
FULL = CHUNK_SIZE + TAG_SIZE  # one full chunk as stored


def sealed(plain: bytes, passphrase: str = PASSPHRASE, params: ScryptParams = LOW) -> bytes:
    sink = io.BytesIO()
    seal(io.BytesIO(plain), sink, passphrase, params=params)
    return sink.getvalue()


def opened(blob: bytes, passphrase: str = PASSPHRASE) -> tuple[Opened, bytes]:
    sink = io.BytesIO()
    result = unseal(io.BytesIO(blob), sink, passphrase)
    return result, sink.getvalue()


def refused(blob: bytes, passphrase: str = PASSPHRASE) -> AdapterError:
    with pytest.raises(AdapterError) as raised:
        unseal(io.BytesIO(blob), io.BytesIO(), passphrase)
    assert raised.value.retryable is False
    return raised.value


def flipped(blob: bytes, at: int) -> bytes:
    return blob[:at] + bytes([blob[at] ^ 0x01]) + blob[at + 1 :]


def chunks(count: int) -> int:
    """The size of a plaintext of ``count`` chunks, the last one 5 bytes long."""
    return (count - 1) * CHUNK_SIZE + 5


# Round trips ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "count"), [(0, 1), (1, 1), (CHUNK_SIZE, 1), (CHUNK_SIZE + 1, 2), (chunks(3), 3)]
)
def test_a_round_trip_gives_the_plaintexts_size_and_sha256(size: int, count: int) -> None:
    plain = os.urandom(size)
    sink = io.BytesIO()

    made = seal(io.BytesIO(plain), sink, PASSPHRASE, params=LOW)
    result, back = opened(sink.getvalue())

    digest = hashlib.sha256(plain).hexdigest()
    assert made == Sealed(size, digest, HEADER_SIZE + count * TAG_SIZE + size)
    assert len(sink.getvalue()) == made.sealed_size
    assert result == Opened(size, digest)
    assert back == plain


def test_every_chunk_but_the_last_holds_64_kib_and_only_the_last_is_final() -> None:
    plain = os.urandom(chunks(3))
    blob = sealed(plain)
    header = blob[:HEADER_SIZE]
    salt = header[8:24]
    derived = Scrypt(salt=salt, length=64, n=2**10, r=8, p=1).derive(PASSPHRASE.encode())
    aead = AESGCM(derived[:32])
    body = blob[HEADER_SIZE:]
    stored = [body[:FULL], body[FULL : 2 * FULL], body[2 * FULL :]]

    plains = [
        aead.decrypt(
            index.to_bytes(12, "big"),
            chunk,
            header + index.to_bytes(8, "big") + bytes([1 if index == 2 else 0]),
        )
        for index, chunk in enumerate(stored)
    ]

    assert [len(part) for part in plains] == [CHUNK_SIZE, CHUNK_SIZE, 5]
    assert b"".join(plains) == plain


def test_the_header_holds_the_magic_version_parameters_salt_and_key_check() -> None:
    blob = sealed(b"x", params=ScryptParams(11, 4, 2))
    header = blob[:HEADER_SIZE]

    assert header[:4] == MAGIC == b"NXBK"
    assert list(header[4:8]) == [1, 11, 4, 2]
    salt = header[8:24]
    derived = Scrypt(salt=salt, length=64, n=2**11, r=4, p=2).derive(PASSPHRASE.encode())
    check = hmac.new(derived[32:], header[:24], hashlib.sha256).digest()[:16]
    assert header[24:40] == check
    assert derived[:32] not in blob and derived[32:] not in blob


def test_each_file_has_a_fresh_salt_so_the_same_plaintext_seals_differently() -> None:
    first, second = sealed(b"same bytes"), sealed(b"same bytes")

    assert first[8:24] != second[8:24]
    assert first[HEADER_SIZE:] != second[HEADER_SIZE:]
    assert opened(first)[1] == opened(second)[1] == b"same bytes"


def test_the_passphrase_is_nfc_normalised() -> None:
    composed, decomposed = "café pass", "café pass"
    assert composed != decomposed

    assert opened(sealed(b"data", composed), decomposed)[1] == b"data"
    assert opened(sealed(b"data", decomposed), composed)[1] == b"data"


def test_the_default_parameters_derive_a_key() -> None:
    assert DEFAULT_SCRYPT == ScryptParams(17, 8, 1)

    blob = sealed(b"default", params=DEFAULT_SCRYPT)

    assert list(blob[4:8]) == [1, 17, 8, 1]
    assert opened(blob)[1] == b"default"


# Decrypt errors --------------------------------------------------------------------------------

THREE = sealed(os.urandom(chunks(3)))


@pytest.mark.parametrize(
    ("blob", "code"),
    [
        (flipped(THREE, 9), "backup_wrong_passphrase"),  # a salt byte
        (flipped(THREE, 30), "backup_wrong_passphrase"),  # a key-check byte
        (flipped(THREE, 4), "backup_bad_format"),  # the version
        (flipped(THREE, len(THREE) - 1), "backup_tampered"),  # the last chunk's tag
        (flipped(THREE, HEADER_SIZE + FULL - 3), "backup_tampered"),  # the first chunk's tag
        (flipped(THREE, HEADER_SIZE + 100), "backup_tampered"),  # a body byte
        (THREE[: HEADER_SIZE + 2 * FULL], "backup_truncated"),  # cut at a chunk boundary
        (THREE[: HEADER_SIZE + FULL], "backup_truncated"),  # cut after the first chunk
        (THREE[:HEADER_SIZE], "backup_truncated"),  # no chunk at all
        (THREE[: HEADER_SIZE + FULL + 100], "backup_tampered"),  # cut inside a chunk
        (THREE[: HEADER_SIZE + 2 * FULL + 3], "backup_tampered"),  # cut inside the last tag
        (THREE + THREE[HEADER_SIZE : HEADER_SIZE + FULL], "backup_tampered"),  # an extra chunk
        (THREE + b"\x00", "backup_tampered"),  # a byte after the final chunk
        (
            THREE[:HEADER_SIZE]
            + THREE[HEADER_SIZE + FULL : HEADER_SIZE + 2 * FULL]
            + THREE[HEADER_SIZE : HEADER_SIZE + FULL]
            + THREE[HEADER_SIZE + 2 * FULL :],
            "backup_tampered",
        ),  # swapped chunks
        (
            THREE[:HEADER_SIZE] + THREE[HEADER_SIZE + FULL :],
            "backup_tampered",
        ),  # a removed middle chunk
        (b"XXXX" + THREE[4:], "backup_bad_format"),
        (THREE[: HEADER_SIZE - 1], "backup_bad_format"),
        (b"", "backup_bad_format"),
    ],
    ids=[
        "salt byte",
        "key-check byte",
        "version",
        "last tag byte",
        "first tag byte",
        "body byte",
        "cut at a chunk boundary",
        "cut after the first chunk",
        "no chunk",
        "cut inside a chunk",
        "cut inside the last tag",
        "extra chunk",
        "trailing byte",
        "swapped chunks",
        "removed chunk",
        "bad magic",
        "short header",
        "empty",
    ],
)
def test_each_decrypt_error_has_its_code(blob: bytes, code: str) -> None:
    assert refused(blob).code == code


@pytest.mark.parametrize(
    "params", [(9, 8, 1), (19, 8, 1), (10, 0, 1), (10, 9, 1), (10, 8, 0), (10, 8, 5)]
)
def test_parameters_out_of_range_are_a_bad_format(params: tuple[int, int, int]) -> None:
    blob = THREE[:5] + bytes(params) + THREE[8:]

    error = refused(blob)

    assert (error.code, error.message) == (
        "backup_bad_format",
        "the backup's scrypt parameters are out of range",
    )


@pytest.mark.parametrize(
    "params", [ScryptParams(9, 8, 1), ScryptParams(19, 8, 1), ScryptParams(10, 9, 1)]
)
def test_seal_refuses_parameters_a_reader_would_refuse(params: ScryptParams) -> None:
    sink = io.BytesIO()

    with pytest.raises(AdapterError) as raised:
        seal(io.BytesIO(b"x"), sink, PASSPHRASE, params=params)

    assert raised.value.code == "backup_bad_format"
    assert sink.getvalue() == b""


def test_the_widest_accepted_parameters_open() -> None:
    assert ScryptParams(10, 1, 1).in_range and ScryptParams(18, 8, 4).in_range
    assert not ScryptParams(True, 8, 1).in_range  # type: ignore[arg-type]
    assert opened(sealed(b"edge", params=ScryptParams(10, 1, 4)))[1] == b"edge"


def test_a_wrong_passphrase_is_its_own_error_and_writes_nothing() -> None:
    sink = io.BytesIO()

    with pytest.raises(AdapterError) as raised:
        unseal(io.BytesIO(THREE), sink, "not the passphrase")

    assert (raised.value.code, raised.value.message) == (
        "backup_wrong_passphrase",
        "the passphrase does not open this backup",
    )
    assert sink.getvalue() == b""


def test_a_tampered_chunk_writes_only_the_chunks_before_it() -> None:
    plain = os.urandom(chunks(3))
    blob = sealed(plain)
    sink = io.BytesIO()

    with pytest.raises(AdapterError):
        unseal(io.BytesIO(flipped(blob, HEADER_SIZE + FULL + 10)), sink, PASSPHRASE)

    assert sink.getvalue() == plain[:CHUNK_SIZE]  # never an unauthenticated byte


def test_a_passphrase_that_is_not_text_is_refused_without_quoting_it() -> None:
    bad = "QZX\ud800PLANTED"

    with pytest.raises(AdapterError) as sealing:
        seal(io.BytesIO(b"x"), io.BytesIO(), bad, params=LOW)
    error = refused(THREE, bad)

    for raised in (sealing.value, error):
        assert raised.code == "backup_wrong_passphrase"
        assert "QZX" not in str(raised) and "PLANTED" not in repr(raised)
        assert raised.__cause__ is None and raised.__suppress_context__


def test_no_error_holds_the_passphrase_or_a_key() -> None:
    header = THREE[:HEADER_SIZE]
    derived = Scrypt(salt=header[8:24], length=64, n=2**10, r=8, p=1).derive(
        PASSPHRASE.encode()
    )
    errors = [
        refused(flipped(THREE, HEADER_SIZE + 100)),
        refused(THREE[: HEADER_SIZE + 2 * FULL]),
        refused(THREE, PASSPHRASE + "x"),
        refused(b"XXXX" + THREE[4:]),
    ]

    for error in errors:
        text = "".join(traceback.format_exception(error)) + str(error) + repr(error)
        assert "QZXPLANTEDPASSPHRASE" not in text
        assert derived[:32].hex() not in text and derived[32:].hex() not in text
        assert error.__cause__ is None


def test_the_module_reads_and_writes_only_the_streams_it_is_given() -> None:
    source = open(__file__, "rb")  # noqa: SIM115 - a real file stream, closed below
    try:
        sink = io.BytesIO()
        made = seal(source, sink, PASSPHRASE, params=LOW)
    finally:
        source.close()

    with open(__file__, "rb") as again:
        assert made.plain_sha256 == hashlib.sha256(again.read()).hexdigest()
    assert not hasattr(crypto, "open") and not hasattr(crypto, "Path")
