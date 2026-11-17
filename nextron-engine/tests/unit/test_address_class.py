"""``nextron.domain.address_class`` (AD-4, FR-15): every (class, event) pair against the table,
``assign`` for every Provenance source, the terminal class and the closed causes."""

import itertools

import pytest

from nextron.domain.address_class import (
    ASSIGNMENT,
    MACHINE,
    TERMINAL,
    TRANSITIONS,
    AddressCause,
    AddressClass,
    AddressEvent,
    assign,
    transition,
)
from nextron.domain.contacts import ProvenanceSource
from nextron.domain.machines import IllegalTransition, Transition

C = AddressClass
E = AddressEvent

EXPECTED = {
    (C.GUESSED, E.VERIFY): Transition(C.VERIFIED, AddressCause.VERIFICATION_VALID),
    (C.GUESSED, E.FIND_ON_SITE): Transition(C.PUBLISHED, AddressCause.FOUND_ON_SITE),
    (C.PUBLISHED, E.FAIL_CHECK): Transition(C.INVALID, AddressCause.CHECK_FAILED),
    (C.GUESSED, E.FAIL_CHECK): Transition(C.INVALID, AddressCause.CHECK_FAILED),
    (C.VERIFIED, E.FAIL_CHECK): Transition(C.INVALID, AddressCause.CHECK_FAILED),
    (C.PUBLISHED, E.HARD_BOUNCE): Transition(C.INVALID, AddressCause.HARD_BOUNCE),
    (C.GUESSED, E.HARD_BOUNCE): Transition(C.INVALID, AddressCause.HARD_BOUNCE),
    (C.VERIFIED, E.HARD_BOUNCE): Transition(C.INVALID, AddressCause.HARD_BOUNCE),
}


def test_the_classes_events_and_causes() -> None:
    assert MACHINE == "address_class"
    assert [str(c) for c in AddressClass] == ["published", "guessed", "verified", "invalid"]
    assert TERMINAL == (C.INVALID,)
    assert [str(e) for e in AddressEvent] == ["verify", "find_on_site", "fail_check", "hard_bounce"]
    assert {str(cause) for cause in AddressCause} == {
        "verification_valid",
        "found_on_site",
        "check_failed",
        "hard_bounce",
        "assigned_from_csv",
        "assigned_from_pattern",
        "assigned_from_finder",
        "assigned_from_website",
    }


@pytest.mark.parametrize(("current", "event"), list(itertools.product(AddressClass, AddressEvent)))
def test_every_pair_follows_the_table_or_is_illegal(
    current: AddressClass, event: AddressEvent
) -> None:
    expected = EXPECTED.get((current, event))
    if expected is None:
        with pytest.raises(IllegalTransition) as caught:
            transition(current, event)
        assert caught.value.message == f"address_class: {event} is not allowed from {current}"
    else:
        assert transition(current, event) == expected


def test_the_table_is_exactly_the_expected_rows_and_read_only() -> None:
    assert dict(TRANSITIONS) == EXPECTED
    with pytest.raises(TypeError):
        TRANSITIONS[(C.INVALID, E.VERIFY)] = EXPECTED[(C.GUESSED, E.VERIFY)]  # type: ignore[index]


def test_invalid_is_terminal_and_nothing_moves_to_guessed() -> None:
    assert not any(current in TERMINAL for current, _ in TRANSITIONS)
    assert all(change.to is not C.GUESSED for change in TRANSITIONS.values())


@pytest.mark.parametrize("source", list(ProvenanceSource))
def test_assign_for_every_source(source: ProvenanceSource) -> None:
    expected = {
        ProvenanceSource.CSV: Transition(C.GUESSED, AddressCause.ASSIGNED_FROM_CSV),
        ProvenanceSource.PATTERN: Transition(C.GUESSED, AddressCause.ASSIGNED_FROM_PATTERN),
        ProvenanceSource.FINDER: Transition(C.GUESSED, AddressCause.ASSIGNED_FROM_FINDER),
        ProvenanceSource.WEBSITE: Transition(C.PUBLISHED, AddressCause.ASSIGNED_FROM_WEBSITE),
    }.get(source)
    if expected is None:
        with pytest.raises(IllegalTransition) as caught:
            assign(source)
        assert caught.value.message == f"address_class: assign is not allowed from {source}"
    else:
        assert assign(source) == expected
        assert ASSIGNMENT[source] == expected


@pytest.mark.parametrize("value", [None, ["csv"], "nope", 3])
def test_anything_else_is_illegal(value: object) -> None:
    with pytest.raises(IllegalTransition):
        assign(value)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        transition(value, E.VERIFY)  # type: ignore[arg-type]
