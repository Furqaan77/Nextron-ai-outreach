"""``nextron.domain.open_code``: the stored form of the one-time code and what a presented code
is worth (AD-13)."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nextron.domain import open_code
from nextron.domain.open_code import OPEN_CODE_TTL, CodeCheck, check, encode

CODE = "A" * 21 + "b-_9" + "z" * 18  # 43 base64url characters
ISSUED = datetime(2026, 11, 17, 9, 0, 0, 123456, tzinfo=UTC)
STORED = encode(CODE, ISSUED)


def test_ttl_is_five_minutes() -> None:
    assert OPEN_CODE_TTL == timedelta(minutes=5)


def test_code_check_values() -> None:
    assert [member.value for member in CodeCheck] == [
        "accepted",
        "absent",
        "malformed",
        "expired",
        "mismatch",
        "unavailable",
    ]


def test_encode_writes_compact_json_with_a_utc_microsecond_time() -> None:
    assert STORED == f'{{"code":"{CODE}","issued_at":"2026-11-17T09:00:00.123456Z"}}'


def test_encode_converts_to_utc_and_always_writes_microseconds() -> None:
    local = datetime(2026, 11, 17, 10, 30, tzinfo=timezone(timedelta(hours=1)))

    assert json.loads(encode(CODE, local))["issued_at"] == "2026-11-17T09:30:00.000000Z"


@pytest.mark.parametrize("code", ["", "A" * 42, "A" * 44, "A" * 42 + "=", "A" * 42 + "é", None])
def test_encode_refuses_a_code_that_is_not_43_base64url_characters(code: object) -> None:
    with pytest.raises(ValueError):
        encode(code, ISSUED)  # type: ignore[arg-type]


def test_encode_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError):
        encode(CODE, datetime(2026, 11, 17, 9, 0))


def test_check_refuses_a_naive_now() -> None:
    with pytest.raises(ValueError):
        check(STORED, CODE, datetime(2026, 11, 17, 9, 1))
    with pytest.raises(ValueError):
        check(None, CODE, datetime(2026, 11, 17, 9, 1))


# Row: enter (fresh code, age up to 5:00)
@pytest.mark.parametrize("age", [timedelta(0), timedelta(minutes=2), OPEN_CODE_TTL])
def test_a_fresh_code_is_accepted_up_to_exactly_five_minutes(age: timedelta) -> None:
    assert check(STORED, CODE, ISSUED + age) is CodeCheck.ACCEPTED


def test_now_in_another_timezone_is_compared_as_an_instant() -> None:
    now = (ISSUED + timedelta(minutes=1)).astimezone(timezone(timedelta(hours=-8)))

    assert check(STORED, CODE, now) is CodeCheck.ACCEPTED


# Row: enter, refused
def test_no_stored_entry_is_absent() -> None:
    assert check(None, CODE, ISSUED) is CodeCheck.ABSENT


def test_one_microsecond_past_five_minutes_is_expired() -> None:
    now = ISSUED + OPEN_CODE_TTL + timedelta(microseconds=1)

    assert check(STORED, CODE, now) is CodeCheck.EXPIRED


def test_a_future_dated_code_is_expired() -> None:
    assert check(STORED, CODE, ISSUED - timedelta(microseconds=1)) is CodeCheck.EXPIRED


def test_expiry_is_checked_before_the_code() -> None:
    later = ISSUED + timedelta(minutes=6)

    assert check(STORED, "wrong", later) is CodeCheck.EXPIRED


@pytest.mark.parametrize(
    "presented",
    ["", CODE[:-1], CODE + "A", CODE.lower(), "B" + CODE[1:], "é" * 43, "\ud800" * 43, " " + CODE],
    ids=["empty", "short", "long", "case", "one-off", "non-ascii", "lone-surrogate", "padded"],
)
def test_any_other_code_is_a_mismatch(presented: str) -> None:
    assert check(STORED, presented, ISSUED) is CodeCheck.MISMATCH


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not json",
        "null",
        "[]",
        '"text"',
        json.dumps({"code": CODE}),
        json.dumps({"issued_at": "2026-11-17T09:00:00.123456Z"}),
        json.dumps({"code": CODE, "issued_at": "2026-11-17T09:00:00.123456Z", "x": 1}),
        json.dumps({"code": 7, "issued_at": "2026-11-17T09:00:00.123456Z"}),
        json.dumps({"code": CODE, "issued_at": 1763370000}),
        json.dumps({"code": CODE[:-1], "issued_at": "2026-11-17T09:00:00.123456Z"}),
        json.dumps({"code": CODE[:-1] + "=", "issued_at": "2026-11-17T09:00:00.123456Z"}),
        json.dumps({"code": CODE, "issued_at": "2026-11-17T09:00:00Z"}),
        json.dumps({"code": CODE, "issued_at": "2026-11-17T09:00:00.123456"}),
        json.dumps({"code": CODE, "issued_at": "2026-11-17T09:00:00.123456+00:00"}),
        json.dumps({"code": CODE, "issued_at": "2026-11-17 09:00:00.123456Z"}),
        json.dumps({"code": CODE, "issued_at": "2026-13-17T09:00:00.123456Z"}),
        json.dumps({"code": CODE, "issued_at": "2026-11-17T09:00:00.12345٣Z"}),
        "[" * 100_000,
    ],
)
def test_a_stored_entry_of_another_shape_is_malformed(stored: str) -> None:
    assert check(stored, CODE, ISSUED) is CodeCheck.MALFORMED


def test_the_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    compared: list[tuple[object, object]] = []
    real = open_code.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(open_code.hmac, "compare_digest", spy)

    assert check(STORED, CODE, ISSUED) is CodeCheck.ACCEPTED
    assert check(STORED, "é", ISSUED) is CodeCheck.MISMATCH
    assert compared == [(CODE.encode(), CODE.encode()), (CODE.encode(), "é".encode())]
