"""The erasure ledger's lines (Story 3.16): validation, encoding in the acceptance criteria's key
order, parsing with torn and malformed lines, identities and the reapply plan."""

import codecs
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nextron.domain.contactability import KeyKind
from nextron.domain.erasure import ErasureCause
from nextron.domain.erasure_ledger import (
    LEDGER_VERSION,
    REAPPLIED,
    Erased,
    ParsedLedger,
    ReapplyPlan,
    Suppressed,
    Voided,
    encode,
    identity,
    parse,
    plan_reapply,
)
from nextron.domain.suppression_entry import SuppressionSource

A = "01JD3QZ8X5V9K2M7N4P6R8T0WA"
B = "01JD3QZ8X5V9K2M7N4P6R8T0WB"
C = "01JD3QZ8X5V9K2M7N4P6R8T0WC"
HASH = "a" * 64
OTHER_HASH = "b" * 64
AT = datetime(2026, 11, 18, 9, 30, 15, 123456, tzinfo=UTC)
STAMP = "2026-11-18T09:30:15.123456Z"


def raw_line(**fields: object) -> bytes:
    return json.dumps(fields, separators=(",", ":")).encode()


def suppressed(entry_id: str = A, value_hash: str = HASH) -> Suppressed:
    return Suppressed(entry_id, KeyKind.ADDRESS, value_hash, SuppressionSource.ONE_CLICK, AT)


def test_the_constants() -> None:
    assert LEDGER_VERSION == 1
    assert REAPPLIED == "reapplied after restore"


def test_encode_writes_the_acceptance_criteria_keys_in_order() -> None:
    line = encode(suppressed())
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert line == (
        '{"v":1,"type":"suppressed","entry_id":"01JD3QZ8X5V9K2M7N4P6R8T0WA","kind":"address",'
        f'"value_hash":"{HASH}","source":"one_click","at":"2026-11-18T09:30:15.123456Z"}}\n'
    )
    assert list(json.loads(encode(Voided(B, AT)))) == ["v", "type", "entry_id", "at"]
    assert list(json.loads(encode(Erased(C, ErasureCause.DSR, AT)))) == [
        "v",
        "type",
        "contact_id",
        "cause",
        "at",
    ]


def test_a_domain_line_carries_its_domain_last() -> None:
    line = Suppressed(A, KeyKind.DOMAIN, HASH, SuppressionSource.OPERATOR, AT, "example.co.uk")
    found = json.loads(encode(line))
    assert list(found)[-1] == "domain"
    assert found["domain"] == "example.co.uk"
    assert "domain" not in json.loads(encode(suppressed()))


def test_at_is_written_in_utc_with_z() -> None:
    local = AT.astimezone(timezone(timedelta(hours=5)))
    assert json.loads(encode(Voided(A, local)))["at"] == "2026-11-18T09:30:15.123456Z"


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: Suppressed("nope", KeyKind.ADDRESS, HASH, SuppressionSource.DSR, AT), ValueError),
        (lambda: Suppressed(A, "address", HASH, SuppressionSource.DSR, AT), TypeError),
        (lambda: Suppressed(A, KeyKind.ADDRESS, "A" * 64, SuppressionSource.DSR, AT), ValueError),
        (lambda: Suppressed(A, KeyKind.ADDRESS, HASH, "dsr", AT), TypeError),
        (
            lambda: Suppressed(A, KeyKind.ADDRESS, HASH, SuppressionSource.DSR, AT, "x.com"),
            ValueError,
        ),
        (lambda: Suppressed(A, KeyKind.DOMAIN, HASH, SuppressionSource.DSR, AT), ValueError),
        (lambda: Suppressed(A, KeyKind.DOMAIN, HASH, SuppressionSource.DSR, AT, ""), ValueError),
        (lambda: Voided(A, datetime(2026, 11, 18)), ValueError),  # naive
        (lambda: Voided(A, "2026-11-18"), TypeError),
        (lambda: Voided(123, AT), TypeError),
        (lambda: Erased(A, "dsr", AT), TypeError),
        (lambda: Erased("nope", ErasureCause.DSR, AT), ValueError),
    ],
)
def test_each_line_is_checked_when_built(build: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        build()  # type: ignore[operator]


def test_parse_round_trips_every_line_type() -> None:
    lines = (
        suppressed(),
        Suppressed(B, KeyKind.DOMAIN, OTHER_HASH, SuppressionSource.IMPORT, AT, "example.com"),
        Voided(A, AT),
        Erased(C, ErasureCause.RETENTION, AT),
    )
    data = "".join(encode(line) for line in lines).encode()
    assert parse(data) == ParsedLedger(lines, malformed=0, torn=False)
    assert parse(b"") == ParsedLedger((), 0, False)


def test_a_torn_tail_is_ignored_and_reported() -> None:
    whole = encode(suppressed()).encode()
    torn = encode(Voided(A, AT)).encode()[:-10]
    assert parse(whole + torn) == ParsedLedger((suppressed(),), malformed=0, torn=True)


@pytest.mark.parametrize(
    "raw",
    [
        b"",  # a blank line
        b"not json",
        bytes([0xFF, 0xFE]),  # not UTF-8
        b"[1, 2]",
        raw_line(v=2, type="voided", entry_id=A, at=STAMP),
        raw_line(v=True, type="voided", entry_id=A, at=STAMP),
        raw_line(v=1, type="gone", entry_id=A, at=STAMP),
        raw_line(v=1, type="voided", entry_id=A),
        raw_line(v=1, type="voided", entry_id=A, at=STAMP, x=1),
        raw_line(v=1, type="voided", entry_id="bad", at=STAMP),
        raw_line(v=1, type="voided", entry_id=A, at="yesterday"),
        raw_line(v=1, type="erased", contact_id=A, cause="whim", at=STAMP),
        raw_line(
            v=1,
            type="suppressed",
            entry_id=A,
            kind="domain",
            value_hash=HASH,
            source="dsr",
            at=STAMP,
        ),
        raw_line(
            v=1,
            type="suppressed",
            entry_id=A,
            kind="address",
            value_hash=HASH,
            source="dsr",
            at=STAMP,
            domain="x.com",
        ),
    ],
)
def test_a_malformed_complete_line_is_counted_and_skipped(raw: bytes) -> None:
    good = encode(Voided(B, AT)).encode()
    found = parse(good + raw + b"\n" + good)
    assert found == ParsedLedger((Voided(B, AT), Voided(B, AT)), malformed=1, torn=False)


def test_parse_takes_bytes() -> None:
    with pytest.raises(TypeError):
        parse("text")  # type: ignore[arg-type]


def test_identity() -> None:
    assert identity(suppressed()) == ("suppressed", A)
    assert identity(Voided(A, AT)) == ("voided", A)
    assert identity(Erased(C, ErasureCause.DSR, AT)) == ("erased", C)
    with pytest.raises(TypeError):
        identity("line")  # type: ignore[arg-type]


def test_the_plan_voids_first_skips_voided_inserts_and_keeps_first_lines() -> None:
    later = AT + timedelta(hours=1)
    lines = (
        suppressed(A),
        Erased(C, ErasureCause.DSR, AT),
        suppressed(B, OTHER_HASH),
        Voided(A, AT),  # A was suppressed then voided: not re-inserted
        suppressed(B, OTHER_HASH),  # a duplicate keeps the first
        Voided(A, later),
        Erased(C, ErasureCause.RETENTION, later),
        Suppressed(C, KeyKind.ADDRESS, HASH, SuppressionSource.REPLY_RULE, later),  # re-suppressed
    )
    assert plan_reapply(lines) == ReapplyPlan(
        voids=(A,),
        inserts=(
            suppressed(B, OTHER_HASH),
            Suppressed(C, KeyKind.ADDRESS, HASH, SuppressionSource.REPLY_RULE, later),
        ),
        erasures=(Erased(C, ErasureCause.DSR, AT),),
    )
    assert plan_reapply(()) == ReapplyPlan((), (), ())
    with pytest.raises(TypeError):
        plan_reapply(["line"])  # type: ignore[list-item]


def test_a_leading_byte_order_mark_is_dropped() -> None:
    data = codecs.BOM_UTF8 + encode(Voided(A, AT)).encode() + encode(Voided(B, AT)).encode()
    assert parse(data) == ParsedLedger((Voided(A, AT), Voided(B, AT)), malformed=0, torn=False)


def test_a_line_nested_too_deep_to_decode_is_malformed() -> None:
    deep = b"[" * 100_000 + b"]" * 100_000
    good = encode(Voided(B, AT)).encode()
    assert parse(deep + b"\n" + good) == ParsedLedger((Voided(B, AT),), malformed=1, torn=False)
