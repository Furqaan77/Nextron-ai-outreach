"""The pure parts of an erasure (``nextron.domain.erasure`` and
``nextron.domain.data_subject_request``, FR-52, AD-4, AD-17, Story 3.13): the tombstone of each
registry class, the tombstone hash (the suppression key's own hash, a fallback that never
raises on a ``str``), ``TableErasure``'s checks, the DataSubjectRequest machine and the request
reference check, whose messages never repeat a value."""

import hashlib

import pytest

from nextron.db.pii_registry import PiiClass
from nextron.domain.contactability import key_hash
from nextron.domain.data_subject_request import (
    CREATION,
    MAX_REF_LENGTH,
    TERMINAL_STATUSES,
    TRANSITIONS,
    RequestCause,
    RequestEvent,
    RequestKind,
    RequestStatus,
    check_request_ref,
    transition,
)
from nextron.domain.erasure import (
    PII_CLASSES,
    REDACTED,
    Coverage,
    ErasureCause,
    TableErasure,
    tombstone,
    tombstone_hash,
)
from nextron.domain.machines import IllegalTransition, Transition

EMAIL = "Jane.Doe@Firm.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
HASH = "a" * 64


# The closed enums -------------------------------------------------------------------------------


def test_the_enums_are_closed() -> None:
    assert REDACTED == "[erased]"
    assert [str(cause) for cause in ErasureCause] == ["dsr", "retention"]
    assert [str(coverage) for coverage in Coverage] == ["tombstoned", "kept", "not_held"]
    assert [str(kind) for kind in RequestKind] == ["erase", "export"]
    assert [str(status) for status in RequestStatus] == ["open", "completed"]
    assert TERMINAL_STATUSES == (RequestStatus.COMPLETED,)


def test_the_class_codes_are_the_registrys() -> None:
    assert PII_CLASSES == tuple(str(pii_class) for pii_class in PiiClass)


# TableErasure -----------------------------------------------------------------------------------


def test_a_table_erasure_holds_its_coverage_and_reason() -> None:
    found = TableErasure(Coverage.KEPT, "the proof of erasure")

    assert (found.coverage, found.reason) == (Coverage.KEPT, "the proof of erasure")


@pytest.mark.parametrize("coverage", ["kept", None, 1])
def test_a_table_erasure_takes_a_coverage_member(coverage: object) -> None:
    with pytest.raises(TypeError, match="TableErasure.coverage must be a Coverage"):
        TableErasure(coverage, "why")  # type: ignore[arg-type]


@pytest.mark.parametrize("reason", ["", " why", "why ", "x" * 301, None, 3])
def test_a_table_erasure_takes_a_stripped_reason(reason: object) -> None:
    with pytest.raises(ValueError, match="TableErasure.reason"):
        TableErasure(Coverage.NOT_HELD, reason)  # type: ignore[arg-type]


# The tombstone hash -----------------------------------------------------------------------------


def test_the_tombstone_hash_is_the_suppression_keys_hash() -> None:
    assert tombstone_hash("address", EMAIL) == key_hash("address", EMAIL)
    assert tombstone_hash("profile", LINKEDIN) == key_hash("profile", LINKEDIN)
    assert tombstone_hash("domain", "www.firm.co.uk") == key_hash("domain", "firm.co.uk")


@pytest.mark.parametrize(
    "value",
    ["", "no-at-sign", "two@@signs.com", "x" * 5000, "a b@c.com", "\ud800@x.com", "@", "é"],
)
def test_a_value_the_normaliser_refuses_still_hashes(value: str) -> None:
    found = tombstone_hash("address", value)

    raw = f"address:{value}".encode("utf-8", "surrogatepass")
    assert found == hashlib.sha256(raw).hexdigest()
    assert len(found) == 64 and found == found.lower()


@pytest.mark.parametrize("value", [None, 1, b"jane@firm.co.uk", ("a", "b")])
def test_the_tombstone_hash_takes_a_str(value: object) -> None:
    with pytest.raises(TypeError, match="a tombstone hash takes a str"):
        tombstone_hash("address", value)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["person", "Address", "phone", "", None])
def test_the_tombstone_hash_takes_a_single_value_kind(kind: object) -> None:
    with pytest.raises(ValueError, match="address, domain or profile"):
        tombstone_hash(kind, EMAIL)  # type: ignore[arg-type]


# The tombstones ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pii_class", "value", "hash_kind", "expected"),
    [
        ("redact", "Jane Doe", None, REDACTED),
        ("redact", None, None, None),
        ("redact", "", None, REDACTED),
        ("redact", REDACTED, None, REDACTED),
        ("null", LINKEDIN, None, None),
        ("null", None, None, None),
        ("keep_hash", EMAIL, "address", key_hash("address", EMAIL)),
        ("keep_hash", None, "address", None),
        ("keep_hash", HASH, None, HASH),  # already a hash
        ("none", "01JD3QZ8X5V9K2M7N4P6R8T0AB", None, "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
        ("none", None, None, None),
        (PiiClass.REDACT, "x", None, REDACTED),
        (PiiClass.KEEP_HASH, EMAIL, "address", key_hash("address", EMAIL)),
    ],
)
def test_each_class_has_its_tombstone(
    pii_class: str, value: object, hash_kind: str | None, expected: object
) -> None:
    assert tombstone(pii_class, value, hash_kind=hash_kind) == expected


def test_an_unknown_class_is_refused_without_the_value() -> None:
    with pytest.raises(ValueError, match="pii_class must be one of") as raised:
        tombstone("hash", "Jane Doe", hash_kind=None)
    assert "Jane" not in str(raised.value)
    with pytest.raises(TypeError, match="pii_class must be a str"):
        tombstone(None, "Jane Doe", hash_kind=None)  # type: ignore[arg-type]


def test_a_keep_hash_value_must_be_text() -> None:
    with pytest.raises(TypeError, match="a keep_hash column takes a str"):
        tombstone("keep_hash", 42, hash_kind="address")


# The DataSubjectRequest machine -----------------------------------------------------------------


def test_a_request_starts_open() -> None:
    assert CREATION == Transition(RequestStatus.OPEN, RequestCause.OPENED)


def test_an_open_request_completes_once() -> None:
    assert transition(RequestStatus.OPEN, RequestEvent.COMPLETE) == Transition(
        RequestStatus.COMPLETED, RequestCause.COMPLETED
    )
    assert dict(TRANSITIONS) == {
        (RequestStatus.OPEN, RequestEvent.COMPLETE): Transition(
            RequestStatus.COMPLETED, RequestCause.COMPLETED
        )
    }
    with pytest.raises(TypeError):
        TRANSITIONS[("x", "y")] = None  # type: ignore[index]


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (RequestStatus.COMPLETED, RequestEvent.COMPLETE),
        ("open", "reopen"),
        ([], RequestEvent.COMPLETE),
    ],
)
def test_anything_else_is_an_illegal_transition(status: object, event: object) -> None:
    with pytest.raises(IllegalTransition, match="data_subject_request"):
        transition(status, event)  # type: ignore[arg-type]


# The request reference --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "name"),
    [
        ("DSR-2026-0042", "Jane Doe"),
        ("Letter of 18 Nov 2026", "Jane Doe"),
        ("x", None),
        ("r" * MAX_REF_LENGTH, None),
        ("Ticket 17 for Doe", "Jane Doe"),  # one of her words alone is no name
        ("JD-1", "Jane Doe"),
        ("Ticket 9", "!!!"),  # a name with no word checks nothing
        ("Janet Doering 7", "Jane Doe"),  # whole words only
    ],
)
def test_a_reference_that_is_not_the_person_is_accepted(ref: str, name: str | None) -> None:
    check_request_ref(ref, name=name)


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("", "1 to 200 characters"),
        ("r" * (MAX_REF_LENGTH + 1), "1 to 200 characters"),
        (" DSR-1", "1 to 200 characters"),
        ("DSR-1\n", "1 to 200 characters"),
        ("DSR\t1", "1 to 200 characters"),
        ("DSR\x001", "1 to 200 characters"),
        ("jane@firm.co.uk", "no @"),
        ("DSR from x@y", "no @"),
        (None, "1 to 200 characters"),
        (42, "1 to 200 characters"),
    ],
)
def test_a_malformed_reference_is_refused(ref: object, message: str) -> None:
    with pytest.raises(ValueError, match=message) as raised:
        check_request_ref(ref, name=None)
    assert "firm" not in str(raised.value) and "DSR" not in str(raised.value)


@pytest.mark.parametrize(
    "ref",
    [
        "Jane Doe",
        "DSR for jane doe",
        "Doe, Jane - letter",
        "JANE-DOE-1",
        "request jane doe 2026",
    ],
)
def test_a_reference_holding_the_persons_name_is_refused(ref: str) -> None:
    with pytest.raises(ValueError, match="must not hold the person's name") as raised:
        check_request_ref(ref, name="Jane Doe")
    assert "Jane" not in str(raised.value) and "Doe" not in str(raised.value)


def test_the_name_must_be_text_or_none() -> None:
    with pytest.raises(TypeError, match="name must be a str or None"):
        check_request_ref("DSR-1", name=42)  # type: ignore[arg-type]
