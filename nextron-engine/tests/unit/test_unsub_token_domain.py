"""``nextron.domain.unsub_token`` (FR-70, AD-15, Story 3.8): the byte format, its checks, the
key text, the key life and the errors; the per-kid key family in ``nextron.domain.credentials``;
and ``nextron.ports.secrets.read_unsub_key``. Pure: a fixed ``now``, no keyring."""

import base64
import hashlib
import hmac
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from nextron.domain import credentials
from nextron.domain.contactability import key_hash
from nextron.domain.credentials import (
    META_PREFIX,
    UnknownCredential,
    UnsubKey,
    declared_unsub_keys,
    unsub_key,
)
from nextron.domain.errors import DomainError
from nextron.domain.unsub_token import (
    AUTHED_SIZE,
    CIPHERTEXT_SIZE,
    KEY_MIN_LIFE,
    PLAINTEXT_SIZE,
    RAW_SIZE,
    TOKEN_LENGTH,
    DecodedToken,
    RawToken,
    TokenAlreadyIssued,
    TokenInvalid,
    TokenInvalidReason,
    UnsubKeyInUse,
    addr_hash_of,
    assemble,
    check_addr_hash,
    check_issued_at,
    check_kid,
    check_nonce,
    edge_tag,
    encode_key,
    key_in_use,
    parse_key,
    parse_token,
    plaintext,
    split_plaintext,
    tag_matches,
    token_hash,
)
from nextron.domain.settings_schema import UNSUB_MAX_KID, UNSUB_MIN_KID
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED, read_unsub_key
from tests.fakes.secrets import InMemorySecrets

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
K_EDGE = hashlib.sha256(b"edge").digest()
NONCE = bytes(range(12))
CT = bytes(range(100, 156))  # 56 bytes
KEY = bytes(range(32))
KEY_TEXT = base64.urlsafe_b64encode(KEY).rstrip(b"=").decode()
SERVICE = "nextron-engine/test"


# The format -----------------------------------------------------------------------------------


def test_the_sizes_add_up() -> None:
    assert (PLAINTEXT_SIZE, CIPHERTEXT_SIZE, AUTHED_SIZE, RAW_SIZE, TOKEN_LENGTH) == (
        40,
        56,
        69,
        81,
        108,
    )
    assert (UNSUB_MIN_KID, UNSUB_MAX_KID) == (1, 255)  # the setting's bounds match the format


def test_plaintext_is_the_hash_then_eight_big_endian_bytes() -> None:
    addr_hash = bytes([9]) * 32

    plain = plaintext(addr_hash, 0x0102030405060708)

    assert plain == addr_hash + bytes([1, 2, 3, 4, 5, 6, 7, 8])
    assert split_plaintext(plain) == (addr_hash, 0x0102030405060708)
    with pytest.raises(ValueError, match="40 bytes"):
        split_plaintext(plain[:-1])


def test_assemble_tags_the_first_69_bytes_and_parse_token_splits_them() -> None:
    token = assemble(7, NONCE, CT, K_EDGE)
    raw = base64.urlsafe_b64decode(token + "===")

    assert len(token) == 108
    assert raw[:69] == bytes([7]) + NONCE + CT
    assert raw[69:] == hmac.new(K_EDGE, raw[:69], hashlib.sha256).digest()[:12]
    assert raw[69:] == edge_tag(K_EDGE, raw[:69])
    parsed = parse_token(token)
    assert parsed == RawToken(7, NONCE, CT, raw[69:])
    assert parsed is not None and parsed.authed == raw[:69]
    assert tag_matches(K_EDGE, parsed)
    assert not tag_matches(hashlib.sha256(b"other").digest(), parsed)


@pytest.mark.parametrize(
    "token",
    [None, "", "A" * 107, "A" * 109, "+" * 108, "A" * 107 + "=", "A" * 107 + "é", b"A" * 108, 1],
)
def test_parse_token_refuses_any_other_shape(token: object) -> None:
    assert parse_token(token) is None


def test_assemble_refuses_wrong_parts() -> None:
    with pytest.raises(ValueError):
        assemble(1, NONCE, CT[:-1], K_EDGE)
    with pytest.raises(ValueError):
        assemble(0, NONCE, CT, K_EDGE)
    with pytest.raises(ValueError):
        assemble(1, NONCE[:-1], CT, K_EDGE)


@pytest.mark.parametrize(
    ("check", "value", "error"),
    [
        (check_kid, 0, ValueError),
        (check_kid, 256, ValueError),
        (check_kid, True, TypeError),
        (check_kid, 1.0, TypeError),
        (check_kid, "1", TypeError),
        (check_addr_hash, bytes(31), ValueError),
        (check_addr_hash, bytearray(32), TypeError),
        (check_addr_hash, "a" * 32, TypeError),
        (check_issued_at, -1, ValueError),
        (check_issued_at, 2**64, ValueError),
        (check_issued_at, True, TypeError),
        (check_issued_at, 1.5, TypeError),
        (check_nonce, bytes(11), ValueError),
        (check_nonce, "x" * 12, TypeError),
    ],
)
def test_each_check_raises_for_a_bad_value(check: object, value: object, error: type) -> None:
    with pytest.raises(error):
        check(value)  # type: ignore[operator]


def test_each_check_returns_a_good_value() -> None:
    assert check_kid(1) == 1 and check_kid(255) == 255
    assert check_addr_hash(bytes(32)) == bytes(32)
    assert check_issued_at(0) == 0 and check_issued_at(2**64 - 1) == 2**64 - 1
    assert check_nonce(NONCE) == NONCE


def test_addr_hash_is_the_suppression_address_key_as_bytes() -> None:
    address = "  Jane.Doe+News@Example.COM "

    assert addr_hash_of(address) == bytes.fromhex(key_hash("address", address))
    assert addr_hash_of(address) == hashlib.sha256(b"address:jane.doe+news@example.com").digest()
    assert addr_hash_of(address) != addr_hash_of("jane.doe@example.com")  # no plus stripping


def test_token_hash_is_the_hex_sha256_of_the_ascii_token() -> None:
    token = assemble(1, NONCE, CT, K_EDGE)

    assert token_hash(token) == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert token_hash(token) == token_hash(token).lower() and len(token_hash(token)) == 64
    with pytest.raises(TypeError):
        token_hash(token.encode())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        token_hash("é")


def test_the_results_are_frozen_and_the_reasons_fixed() -> None:
    assert [str(reason) for reason in TokenInvalidReason] == [
        "malformed",
        "unknown_kid",
        "bad_tag",
        "bad_ciphertext",
    ]
    decoded = DecodedToken(1, bytes(32), 5)
    invalid = TokenInvalid(TokenInvalidReason.BAD_TAG)
    with pytest.raises(FrozenInstanceError):
        decoded.kid = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        invalid.reason = TokenInvalidReason.MALFORMED  # type: ignore[misc]


# Keys -----------------------------------------------------------------------------------------


def test_a_canonical_key_parses_with_or_without_its_padding() -> None:
    assert len(KEY_TEXT) == 43
    assert parse_key(KEY_TEXT) == KEY
    assert parse_key(KEY_TEXT + "=") == KEY
    assert encode_key(KEY) == KEY_TEXT


@pytest.mark.parametrize(
    "text",
    [
        None,
        b"x" * 43,
        "",
        KEY_TEXT[:-1],  # 42 characters
        KEY_TEXT + "A",  # 44 characters, no padding
        KEY_TEXT + "==",
        base64.urlsafe_b64encode(bytes(31)).decode(),  # 31 bytes
        base64.urlsafe_b64encode(bytes(33)).rstrip(b"=").decode(),  # 33 bytes
        base64.b64encode(b"\xfb" * 32).decode(),  # standard base64: "+" and "/"
        KEY_TEXT[:20] + " " + KEY_TEXT[21:],  # a space inside
        " " + KEY_TEXT,  # a space before
        KEY_TEXT + " ",
        KEY_TEXT[:-1] + "B",  # the unused low bits set: not canonical
        "é" * 43,
    ],
    ids=range(14),
)
def test_anything_else_is_no_key(text: object) -> None:
    assert parse_key(text) is None


def test_encode_key_refuses_anything_but_32_bytes() -> None:
    with pytest.raises(ValueError):
        encode_key(bytes(31))
    with pytest.raises(TypeError):
        encode_key(KEY_TEXT)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("newest", "in_use"),
    [
        (None, False),
        (NOW - timedelta(days=365), True),  # a year
        (NOW - timedelta(days=731) + timedelta(seconds=1), True),
        (NOW - timedelta(days=731), False),  # exactly 2 years: no longer in use
        (NOW - timedelta(days=732), False),
        (NOW + timedelta(days=1), True),  # a token from the future still counts
    ],
)
def test_a_key_is_in_use_while_its_newest_token_is_under_731_days_old(
    newest: datetime | None, in_use: bool
) -> None:
    assert KEY_MIN_LIFE == timedelta(days=731)
    assert key_in_use(newest, NOW) is in_use


def test_the_errors_have_their_codes_and_messages() -> None:
    in_use = UnsubKeyInUse(3)
    issued = TokenAlreadyIssued()

    assert isinstance(in_use, DomainError) and isinstance(issued, DomainError)
    assert (in_use.code, str(in_use), in_use.kid) == (
        "unsub_key_in_use",
        "Key 3 still has tokens under 2 years old",
        3,
    )
    assert (issued.code, str(issued)) == (
        "unsub_token_already_issued",
        "token already issued for this message",
    )


# The key family (nextron.domain.credentials) ----------------------------------------------------


def test_the_family_names_each_kids_keyring_entries() -> None:
    k_tok, k_edge = declared_unsub_keys()

    assert (k_tok.key, k_edge.key) == (UnsubKey.K_TOK, UnsubKey.K_EDGE)
    assert unsub_key("k_tok") is k_tok and unsub_key("k_edge") is k_edge
    assert (k_tok.secret_name(1), k_edge.secret_name(255)) == ("unsub/k_tok/1", "unsub/k_edge/255")
    assert k_tok.meta_name(7) == META_PREFIX + "unsub/k_tok/7"
    assert k_tok.required and k_edge.required
    assert k_tok.label != k_edge.label


def test_the_family_refuses_unknown_keys_and_bad_kids() -> None:
    with pytest.raises(UnknownCredential):
        unsub_key("k_other")
    with pytest.raises(UnknownCredential):
        unsub_key(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        unsub_key("k_tok").secret_name(0)
    with pytest.raises(TypeError):
        unsub_key("k_tok").secret_name("1")  # type: ignore[arg-type]


def test_the_registry_is_unchanged_by_the_family() -> None:
    assert len(credentials.declared()) == 9
    assert not any(found.secret_name.startswith("unsub/k_") for found in credentials.declared())


# read_unsub_key (nextron.ports.secrets) -----------------------------------------------------------


def test_read_unsub_key_gives_the_bytes_or_none() -> None:
    secrets = InMemorySecrets()

    assert read_unsub_key(secrets, "k_tok", 1) is None  # missing
    secrets.store[(SERVICE, "unsub/k_tok/1")] = KEY_TEXT
    assert read_unsub_key(secrets, "k_tok", 1) == KEY
    secrets.store[(SERVICE, "unsub/k_tok/1")] = "not a key"
    assert read_unsub_key(secrets, "k_tok", 1) is None  # malformed
    assert secrets.calls == [("read", "unsub/k_tok/1")] * 3


def test_read_unsub_key_lets_a_keyring_failure_out_and_checks_its_arguments() -> None:
    secrets = InMemorySecrets()
    secrets.failing.add("read")

    with pytest.raises(AdapterError) as failed:
        read_unsub_key(secrets, "k_edge", 2)
    assert failed.value.code == KEYRING_FAILED
    with pytest.raises(UnknownCredential):
        read_unsub_key(secrets, "bearer", 1)
    with pytest.raises(ValueError):
        read_unsub_key(secrets, "k_edge", 256)
