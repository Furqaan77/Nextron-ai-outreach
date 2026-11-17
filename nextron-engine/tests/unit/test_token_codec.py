"""The unsubscribe token codec (FR-70, AD-15, NFR-17, Story 3.8): ``TokenCodec`` against the
committed vectors, tampering, garbage, the tag-before-decrypt order, Credential Manager failures
and missing keys; and ``FakeTokenCodec``, the port's fake.

``build_vectors()`` is the vector file's recipe, written independently of
``nextron.domain.unsub_token`` and the adapter: raw ``AESGCMSIV``, the standard library's
``hmac``, ``hashlib`` and ``base64``. ``tests/fixtures/unsub_token_vectors.json`` must be exactly
what it builds, so the file, this recipe and the engine's codec all agree; the Worker (Story
3.10) is bound by the same file. Every key in it is a test key, never deployed.
"""

import base64
import hashlib
import hmac
import json
import string
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV

from nextron.adapters.crypto.unsub_token import TokenCodec
from nextron.domain.contactability import key_hash
from nextron.domain.unsub_token import (
    DecodedToken,
    TokenInvalid,
    TokenInvalidReason,
    token_hash,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED
from nextron.ports.token_codec import UNSUB_KEY_MISSING, TokenCodecPort
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.token_codec import FakeTokenCodec

VECTORS = Path(__file__).parents[1] / "fixtures" / "unsub_token_vectors.json"
FORMAT = "nextron-unsub-token/1"
KIDS = (1, 2)
SERVICE = "nextron-engine/test"
VALID = [  # (name, kid, address, issued_at)
    ("kid1_plain", 1, "jane.doe@example.com", 1_790_000_000),
    ("kid1_normalised", 1, "  J.Smith+News@Example.CO.UK ", 1_795_123_456),
    ("kid2_plain", 2, "ops@example.org", 1_800_000_000),
    ("kid2_far_future", 2, "zed@example.net", 4_102_444_800),
]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _raw(token: str) -> bytes:
    return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))


def _test_key(kind: str, kid: int) -> bytes:
    return hashlib.sha256(f"nextron unsub test vector {kind} {kid}".encode()).digest()


def _nonce(name: str) -> bytes:
    return hashlib.sha256(f"nextron unsub test nonce {name}".encode()).digest()[:12]


def _tag(k_edge: bytes, authed: bytes) -> bytes:
    return hmac.new(k_edge, authed, hashlib.sha256).digest()[:12]


def _token(kid: int, nonce: bytes, ct: bytes, k_edge: bytes) -> str:
    authed = bytes([kid]) + nonce + ct
    return _b64url(authed + _tag(k_edge, authed))


def _flip(token: str, index: int, mask: int) -> str:
    raw = bytearray(_raw(token))
    raw[index] ^= mask
    return _b64url(bytes(raw))


def _with_kid(token: str, kid: int) -> str:
    raw = bytearray(_raw(token))
    raw[0] = kid
    return _b64url(bytes(raw))


def build_vectors() -> dict[str, Any]:
    """The vector file's content; see the module docstring."""
    k_tok = {kid: _test_key("k_tok", kid) for kid in KIDS}
    k_edge = {kid: _test_key("k_edge", kid) for kid in KIDS}
    valid = []
    for name, kid, address, issued_at in VALID:
        nonce = _nonce(name)
        # normalize_email strips, NFCs and lowercases, with an IDNA domain; for these ASCII
        # addresses that is just strip().lower(), and a test checks it against key_hash.
        addr_hash = hashlib.sha256(f"address:{address.strip().lower()}".encode()).digest()
        plain = addr_hash + issued_at.to_bytes(8, "big")
        token = _token(kid, nonce, AESGCMSIV(k_tok[kid]).encrypt(nonce, plain, None), k_edge[kid])
        valid.append(
            {
                "name": name,
                "kid": kid,
                "nonce": nonce.hex(),
                "address": address,
                "addr_hash": addr_hash.hex(),
                "issued_at": issued_at,
                "token": token,
                "token_hash": hashlib.sha256(token.encode("ascii")).hexdigest(),
            }
        )
    first = valid[0]["token"]
    garbage_nonce = _nonce("garbage")
    wrong_nonce = _nonce("wrong_k_tok")
    wrong_plain = bytes.fromhex(valid[0]["addr_hash"]) + (1_790_000_000).to_bytes(8, "big")
    wrong_ct = AESGCMSIV(k_tok[2]).encrypt(wrong_nonce, wrong_plain, None)
    tampered = [
        ("tag_flipped", _flip(first, 80, 0x01), "bad_tag"),
        ("ciphertext_flipped", _flip(first, 20, 0x01), "bad_tag"),
        ("nonce_flipped", _flip(first, 5, 0x80), "bad_tag"),
        ("kid_swapped_to_2", _with_kid(first, 2), "bad_tag"),
        ("kid_zero", _with_kid(first, 0), "unknown_kid"),
        ("kid_unknown_3", _with_kid(first, 3), "unknown_kid"),
        ("kid_unknown_255", _with_kid(first, 255), "unknown_kid"),
        (
            "retagged_garbage_ciphertext",
            _token(1, garbage_nonce, b"\xab" * 56, k_edge[1]),
            "bad_ciphertext",
        ),
        (
            "encrypted_under_kid2_k_tok",
            _token(1, wrong_nonce, wrong_ct, k_edge[1]),
            "bad_ciphertext",
        ),
        ("truncated_107", first[:-1], "malformed"),
        ("extended_109", first + "A", "malformed"),
        ("padded", first + "=", "malformed"),
        ("standard_base64_plus", "+" + first[1:], "malformed"),
        ("standard_base64_slash", first[:-1] + "/", "malformed"),
        ("space_inside", first[:50] + " " + first[51:], "malformed"),
        ("empty", "", "malformed"),
    ]
    return {
        "format": FORMAT,
        "description": (
            "Story 3.8 unsubscribe token vectors. token = base64url without padding of "
            "kid (1 byte) || nonce (12) || AES-256-GCM-SIV(k_tok[kid], nonce, no AAD, "
            "addr_hash (32) || issued_at (8, unsigned big-endian Unix seconds)) (56) || "
            "HMAC-SHA256(k_edge[kid], the 69 bytes before it)[:12]: 81 bytes, 108 characters. "
            "addr_hash = SHA-256('address:' + normalize_email(address)), the engine's "
            "key_hash('address', address): the address stripped, NFC'd and lowercased, with "
            "its domain in IDNA ASCII (for these ASCII vectors, just trimmed and lowercased). "
            "Only the engine computes addr_hash; the Worker checks only the tag. Keys are "
            "base64url without padding. 'edge' is what the Worker, which holds only k_edge, "
            "decides: accept exactly when the tag verifies. These keys are test keys only and "
            "must never be deployed."
        ),
        "keys": {
            str(kid): {"k_tok": _b64url(k_tok[kid]), "k_edge": _b64url(k_edge[kid])}
            for kid in KIDS
        },
        "valid": valid,
        "tampered": [
            {
                "name": name,
                "token": token,
                "reason": reason,
                "edge": "accept" if reason == "bad_ciphertext" else "reject",
            }
            for name, token, reason in tampered
        ],
    }


@pytest.fixture(scope="module")
def vectors() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return loaded


def keyed_secrets(vectors: dict[str, Any], kids: tuple[int, ...] = KIDS) -> InMemorySecrets:
    secrets = InMemorySecrets()
    for kid in kids:
        for key, text in vectors["keys"][str(kid)].items():
            secrets.store[(SERVICE, f"unsub/{key}/{kid}")] = text
    return secrets


# The vector file --------------------------------------------------------------------------------


def test_the_vector_file_is_exactly_what_the_recipe_builds(vectors: dict[str, Any]) -> None:
    assert vectors == build_vectors()
    assert vectors["format"] == FORMAT
    assert {case["reason"] for case in vectors["tampered"]} == {str(r) for r in TokenInvalidReason}


def test_every_valid_vector_has_the_format(vectors: dict[str, Any]) -> None:
    for case in vectors["valid"]:
        token = case["token"]
        assert len(token) == 108
        assert set(token) <= set(string.ascii_letters + string.digits + "-_")
        raw = _raw(token)
        assert len(raw) == 81
        assert raw[0] == case["kid"]
        assert raw[1:13] == bytes.fromhex(case["nonce"])
        assert case["addr_hash"] == key_hash("address", case["address"])
        assert case["token_hash"] == token_hash(token)


def test_the_edge_verdict_is_whether_the_tag_verifies(vectors: dict[str, Any]) -> None:
    edges = {
        int(kid): base64.urlsafe_b64decode(keys["k_edge"] + "=")
        for kid, keys in vectors["keys"].items()
    }

    def verifies(token: str) -> bool:
        if len(token) != 108 or not set(token) <= set(string.ascii_letters + string.digits + "-_"):
            return False
        raw = _raw(token)
        k_edge = edges.get(raw[0])
        return k_edge is not None and hmac.compare_digest(_tag(k_edge, raw[:69]), raw[69:])

    assert all(verifies(case["token"]) for case in vectors["valid"])
    for case in vectors["tampered"]:
        assert verifies(case["token"]) is (case["edge"] == "accept"), case["name"]


# TokenCodec against the vectors -----------------------------------------------------------------


def test_the_codec_decodes_every_valid_vector(vectors: dict[str, Any]) -> None:
    codec = TokenCodec(keyed_secrets(vectors))

    for case in vectors["valid"]:
        assert codec.decode(case["token"]) == DecodedToken(
            case["kid"], bytes.fromhex(case["addr_hash"]), case["issued_at"]
        )


def test_the_codec_encodes_every_valid_vector_byte_for_byte(vectors: dict[str, Any]) -> None:
    for case in vectors["valid"]:
        nonce = bytes.fromhex(case["nonce"])
        asked: list[int] = []

        def given(size: int, nonce: bytes = nonce) -> bytes:
            asked.append(size)
            return nonce

        codec = TokenCodec(keyed_secrets(vectors), nonce=given)

        token = codec.encode(case["kid"], bytes.fromhex(case["addr_hash"]), case["issued_at"])

        assert token == case["token"]
        assert asked == [12]


def test_every_tampered_vector_gives_its_reason(vectors: dict[str, Any]) -> None:
    codec = TokenCodec(keyed_secrets(vectors))

    for case in vectors["tampered"]:
        assert codec.decode(case["token"]) == TokenInvalid(
            TokenInvalidReason(case["reason"])
        ), case["name"]


def test_a_fresh_token_round_trips_with_a_random_nonce(vectors: dict[str, Any]) -> None:
    codec = TokenCodec(keyed_secrets(vectors))
    addr_hash = bytes(range(32))

    first = codec.encode(1, addr_hash, 1_800_000_000)
    second = codec.encode(1, addr_hash, 1_800_000_000)

    assert first != second  # a fresh nonce each time
    assert codec.decode(first) == codec.decode(second) == DecodedToken(1, addr_hash, 1_800_000_000)
    assert isinstance(codec, TokenCodecPort)


# Garbage never raises ---------------------------------------------------------------------------

GARBAGE: list[object] = [
    None,
    "",
    "A" * 107,
    "A" * 109,
    "A" * 500,
    "+" * 108,
    "/" * 108,
    "=" * 108,
    "A" * 106 + "==",
    "é" * 108,
    "A" * 107 + "é",
    "A" * 107 + "\n",
    "\x00" * 108,
    b"A" * 108,
    108,
    1.5,
    ["A" * 108],
    object(),
]


@pytest.mark.parametrize("token", GARBAGE, ids=range(len(GARBAGE)))
def test_garbage_is_malformed_and_never_raises(vectors: dict[str, Any], token: object) -> None:
    secrets = keyed_secrets(vectors)
    codec = TokenCodec(secrets)

    found = codec.decode(token)  # type: ignore[arg-type]

    assert found == TokenInvalid(TokenInvalidReason.MALFORMED)
    assert secrets.calls == []  # no keyring read for a malformed token


def test_random_well_shaped_tokens_never_raise(vectors: dict[str, Any]) -> None:
    codec = TokenCodec(keyed_secrets(vectors))
    alphabet = string.ascii_letters + string.digits + "-_"

    for seed in range(300):
        digest = hashlib.sha256(seed.to_bytes(4, "big")).digest() * 4
        token = "".join(alphabet[byte % 64] for byte in digest[:108])
        found = codec.decode(token)
        assert isinstance(found, TokenInvalid)
        assert found.reason in (TokenInvalidReason.UNKNOWN_KID, TokenInvalidReason.BAD_TAG)


def test_a_zeroed_buffer_is_an_unknown_kid(vectors: dict[str, Any]) -> None:
    secrets = keyed_secrets(vectors)

    assert TokenCodec(secrets).decode(_b64url(bytes(81))) == TokenInvalid(
        TokenInvalidReason.UNKNOWN_KID
    )
    assert secrets.calls == []  # kid 0 is never looked up


# The order: kid, then tag, then k_tok ----------------------------------------------------------


def test_the_tag_is_checked_before_k_tok_is_read(vectors: dict[str, Any]) -> None:
    secrets = keyed_secrets(vectors)
    codec = TokenCodec(secrets)
    tampered = {case["name"]: case["token"] for case in vectors["tampered"]}

    found = codec.decode(tampered["ciphertext_flipped"])
    assert found == TokenInvalid(TokenInvalidReason.BAD_TAG)
    assert secrets.calls == [("read", "unsub/k_edge/1")]

    secrets.calls.clear()
    codec.decode(vectors["valid"][0]["token"])
    assert secrets.calls == [("read", "unsub/k_edge/1"), ("read", "unsub/k_tok/1")]


def test_the_tag_is_compared_in_constant_time(
    vectors: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    compared: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr("nextron.domain.unsub_token.hmac.compare_digest", spy)
    TokenCodec(keyed_secrets(vectors)).decode(vectors["valid"][0]["token"])

    assert len(compared) == 1
    assert all(len(side) == 12 for side in compared[0])


def test_a_kid_without_a_valid_k_edge_is_unknown(vectors: dict[str, Any]) -> None:
    token = vectors["valid"][0]["token"]
    missing = keyed_secrets(vectors)
    del missing.store[(SERVICE, "unsub/k_edge/1")]
    malformed = keyed_secrets(vectors)
    malformed.store[(SERVICE, "unsub/k_edge/1")] = "not a key"

    for secrets in (missing, malformed):
        assert TokenCodec(secrets).decode(token) == TokenInvalid(TokenInvalidReason.UNKNOWN_KID)
        assert ("read", "unsub/k_tok/1") not in secrets.calls


def test_a_good_tag_without_a_valid_k_tok_is_an_unknown_kid(vectors: dict[str, Any]) -> None:
    token = vectors["valid"][0]["token"]
    secrets = keyed_secrets(vectors)
    del secrets.store[(SERVICE, "unsub/k_tok/1")]

    assert TokenCodec(secrets).decode(token) == TokenInvalid(TokenInvalidReason.UNKNOWN_KID)

    secrets.store[(SERVICE, "unsub/k_tok/1")] = vectors["keys"]["1"]["k_tok"][:-1]  # 42 chars
    assert TokenCodec(secrets).decode(token) == TokenInvalid(TokenInvalidReason.UNKNOWN_KID)


def test_keys_are_read_at_each_call_and_never_cached(vectors: dict[str, Any]) -> None:
    secrets = keyed_secrets(vectors)
    codec = TokenCodec(secrets)
    token = vectors["valid"][0]["token"]
    assert isinstance(codec.decode(token), DecodedToken)

    secrets.store[(SERVICE, "unsub/k_edge/1")] = vectors["keys"]["2"]["k_edge"]

    assert codec.decode(token) == TokenInvalid(TokenInvalidReason.BAD_TAG)
    assert "k_" not in repr(codec)
    assert all(vectors["keys"]["1"][key] not in repr(vars(codec)) for key in ("k_tok", "k_edge"))


# Credential Manager failures and missing keys ----------------------------------------------------


def test_a_keyring_failure_propagates_from_decode_and_encode(vectors: dict[str, Any]) -> None:
    secrets = keyed_secrets(vectors)
    secrets.failing.add("read")
    codec = TokenCodec(secrets)

    with pytest.raises(AdapterError) as decoding:
        codec.decode(vectors["valid"][0]["token"])
    with pytest.raises(AdapterError) as encoding:
        codec.encode(1, bytes(32), 0)

    assert decoding.value.code == encoding.value.code == KEYRING_FAILED


@pytest.mark.parametrize(
    ("dropped", "stored", "entry"),
    [
        ("unsub/k_tok/1", None, "unsub/k_tok/1"),
        ("unsub/k_edge/1", None, "unsub/k_edge/1"),
        ("unsub/k_tok/1", "QUJD", "unsub/k_tok/1"),  # 3 bytes
        ("unsub/k_edge/1", _b64url(bytes(31)), "unsub/k_edge/1"),  # 31 bytes
        ("unsub/k_edge/1", base64.b64encode(b"\xfb" * 32).decode(), "unsub/k_edge/1"),  # std
    ],
)
def test_a_missing_or_malformed_key_refuses_to_encode(
    vectors: dict[str, Any], dropped: str, stored: str | None, entry: str
) -> None:
    secrets = keyed_secrets(vectors)
    if stored is None:
        del secrets.store[(SERVICE, dropped)]
    else:
        secrets.store[(SERVICE, dropped)] = stored

    with pytest.raises(AdapterError) as refused:
        TokenCodec(secrets).encode(1, bytes(32), 0)

    assert (refused.value.retryable, refused.value.code) == (False, UNSUB_KEY_MISSING)
    assert entry in refused.value.message
    assert all(text not in refused.value.message for text in vectors["keys"]["1"].values())
    assert stored is None or stored not in refused.value.message


@pytest.mark.parametrize(
    ("kid", "addr_hash", "issued_at", "error"),
    [
        (0, bytes(32), 0, ValueError),
        (256, bytes(32), 0, ValueError),
        (-1, bytes(32), 0, ValueError),
        (True, bytes(32), 0, TypeError),
        ("1", bytes(32), 0, TypeError),
        (1, bytes(31), 0, ValueError),
        (1, bytes(33), 0, ValueError),
        (1, "00" * 32, 0, TypeError),
        (1, bytearray(32), 0, TypeError),
        (1, bytes(32), -1, ValueError),
        (1, bytes(32), 2**64, ValueError),
        (1, bytes(32), 1.0, TypeError),
        (1, bytes(32), False, TypeError),
    ],
)
def test_a_bad_argument_raises_before_any_keyring_read(
    vectors: dict[str, Any], kid: object, addr_hash: object, issued_at: object, error: type
) -> None:
    secrets = keyed_secrets(vectors)

    with pytest.raises(error):
        TokenCodec(secrets).encode(kid, addr_hash, issued_at)  # type: ignore[arg-type]
    with pytest.raises(error):
        FakeTokenCodec().encode(kid, addr_hash, issued_at)  # type: ignore[arg-type]

    assert secrets.calls == []


def test_the_extreme_issued_at_values_round_trip(vectors: dict[str, Any]) -> None:
    codec = TokenCodec(keyed_secrets(vectors))

    for issued_at in (0, 2**64 - 1):
        token = codec.encode(2, bytes([7]) * 32, issued_at)
        assert codec.decode(token) == DecodedToken(2, bytes([7]) * 32, issued_at)


# The fake ---------------------------------------------------------------------------------------


def test_the_fake_round_trips_what_it_encoded_and_refuses_the_rest() -> None:
    fake = FakeTokenCodec()
    addr_hash = bytes(range(32))

    token = fake.encode(1, addr_hash, 1_800_000_000)
    other = fake.encode(1, addr_hash, 1_800_000_000)

    assert isinstance(fake, TokenCodecPort)
    assert len(token) == 108 and token != other
    assert fake.decode(token) == DecodedToken(1, addr_hash, 1_800_000_000)
    assert fake.decode(_flip(token, 80, 1)) == TokenInvalid(TokenInvalidReason.BAD_TAG)
    assert fake.decode(_with_kid(token, 2)) == TokenInvalid(TokenInvalidReason.UNKNOWN_KID)
    assert fake.decode(None) == TokenInvalid(TokenInvalidReason.MALFORMED)  # type: ignore[arg-type]
    with pytest.raises(AdapterError) as missing:
        fake.encode(2, addr_hash, 0)
    assert missing.value.code == UNSUB_KEY_MISSING
    fake.fail = AdapterError(False, KEYRING_FAILED, "read failed")
    with pytest.raises(AdapterError):
        fake.decode(token)
    assert fake.encoded == [(1, addr_hash, 1_800_000_000)] * 2


def test_two_fakes_never_give_the_same_token() -> None:
    first, second = FakeTokenCodec(), FakeTokenCodec()

    tokens = [fake.encode(1, bytes(32), 0) for fake in (first, second, first, second)]

    assert len(set(tokens)) == 4
    assert second.decode(tokens[1]) == DecodedToken(1, bytes(32), 0)
    assert second.decode(tokens[0]) == TokenInvalid(TokenInvalidReason.BAD_TAG)  # not its own


def test_equal_keys_refuse_to_encode(vectors: dict[str, Any]) -> None:
    secrets = keyed_secrets(vectors)
    secrets.store[(SERVICE, "unsub/k_edge/1")] = vectors["keys"]["1"]["k_tok"] + "="

    with pytest.raises(AdapterError) as refused:
        TokenCodec(secrets).encode(1, bytes(32), 0)

    assert (refused.value.retryable, refused.value.code) == (False, UNSUB_KEY_MISSING)
    assert "unsub/k_tok/1" in refused.value.message
    assert "unsub/k_edge/1" in refused.value.message
    assert vectors["keys"]["1"]["k_tok"] not in refused.value.message
