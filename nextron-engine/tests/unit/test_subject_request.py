"""The data subject request machine (``nextron.domain.subject_request``, AD-4, AD-17, Stories 3.13
and 3.15): the exact kinds, statuses, events and causes; ``CREATION``; the one-row transition
table and ``IllegalTransition`` for every other pair; and the request reference rules (blank,
too long, control and format characters, the stripped value), whose messages never repeat the
text."""

import itertools
from types import MappingProxyType

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.subject_request import (
    CREATION,
    MACHINE,
    MAX_REQUEST_REF_LENGTH,
    REF_INVALID,
    REF_REQUIRED,
    TERMINAL_STATUSES,
    TRANSITIONS,
    RequestCause,
    RequestEvent,
    RequestKind,
    RequestRefused,
    RequestStatus,
    checked_request_ref,
    transition,
)


def test_the_kinds_statuses_events_and_causes_are_closed() -> None:
    assert MACHINE == "subject_request"
    assert [str(kind) for kind in RequestKind] == ["erase", "export"]
    assert [str(status) for status in RequestStatus] == ["open", "completed"]
    assert [str(event) for event in RequestEvent] == ["complete"]
    assert [str(cause) for cause in RequestCause] == ["opened", "completed"]
    assert TERMINAL_STATUSES == (RequestStatus.COMPLETED,)


def test_every_request_is_created_open() -> None:
    assert CREATION == Transition(RequestStatus.OPEN, RequestCause.OPENED)


def test_an_open_request_completes_and_the_table_is_read_only() -> None:
    assert transition(RequestStatus.OPEN, RequestEvent.COMPLETE) == Transition(
        RequestStatus.COMPLETED, RequestCause.COMPLETED
    )
    assert isinstance(TRANSITIONS, MappingProxyType)
    assert len(TRANSITIONS) == 1
    with pytest.raises(TypeError):
        key = (RequestStatus.COMPLETED, RequestEvent.COMPLETE)
        TRANSITIONS[key] = CREATION  # type: ignore[index]


@pytest.mark.parametrize(
    ("status", "event"),
    [
        pair
        for pair in itertools.product(RequestStatus, RequestEvent)
        if pair != (RequestStatus.OPEN, RequestEvent.COMPLETE)
    ],
)
def test_every_other_pair_is_illegal(status: RequestStatus, event: RequestEvent) -> None:
    with pytest.raises(IllegalTransition) as caught:
        transition(status, event)

    assert caught.value.message == f"subject_request: {event} is not allowed from {status}"


@pytest.mark.parametrize(
    ("status", "event"), [("opened", "complete"), ([], "complete"), (None, None)]
)
def test_values_that_are_no_status_or_event_are_illegal(status: object, event: object) -> None:
    with pytest.raises(IllegalTransition):
        transition(status, event)  # type: ignore[arg-type]


# The request reference --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Email of 18 Nov 2026", "Email of 18 Nov 2026"),
        ("  ticket 42 \n", "ticket 42"),
        ("x", "x"),
        ("é" * MAX_REQUEST_REF_LENGTH, "é" * MAX_REQUEST_REF_LENGTH),
        (" " + "a" * MAX_REQUEST_REF_LENGTH + " ", "a" * MAX_REQUEST_REF_LENGTH),
    ],
)
def test_a_reference_is_stripped_and_kept(text: str, expected: str) -> None:
    assert checked_request_ref(text) == expected


@pytest.mark.parametrize("text", ["", " ", "\t\n ", "　"])
def test_a_blank_reference_is_refused(text: str) -> None:
    with pytest.raises(RequestRefused) as caught:
        checked_request_ref(text)

    assert caught.value.message == REF_REQUIRED == "Enter the request reference"


@pytest.mark.parametrize(
    "text",
    [
        "a" * (MAX_REQUEST_REF_LENGTH + 1),
        "ticket\n42",
        "ticket\x0042",
        "ticket\x1b[31m42",
        "ticket\u200b42",  # a zero-width space (Cf)
        "ticket\u202e42",  # a right-to-left override (Cf)
        "ticket\u202842",  # a line separator (Zl)
        "ticket\u202942",  # a paragraph separator (Zp)
        "\x7fticket",
    ],
)
def test_a_long_or_control_reference_is_refused_without_repeating_it(text: str) -> None:
    with pytest.raises(RequestRefused) as caught:
        checked_request_ref(text)

    assert caught.value.message == REF_INVALID
    assert REF_INVALID == (
        "A request reference is at most 200 characters, with no control characters"
    )
    assert "ticket" not in str(caught.value)


@pytest.mark.parametrize("text", [None, 42, b"ticket", ["ticket"]])
def test_a_reference_that_isnt_a_str_is_a_type_error(text: object) -> None:
    with pytest.raises(TypeError):
        checked_request_ref(text)  # type: ignore[arg-type]


def test_the_refusal_is_a_domain_error_with_its_own_code() -> None:
    assert issubclass(RequestRefused, DomainError)
    assert RequestRefused.code == "request_refused"
