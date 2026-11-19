"""The OutboundMessage machine (AD-4, Story 5.2): the transition table and every illegal pair."""

import itertools

import pytest

from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.outbound_message import (
    CREATION,
    LIVE,
    MACHINE,
    TERMINAL,
    TRANSITIONS,
    OmCause,
    OmEvent,
    OmKind,
    OmStatus,
    transition,
)

S = OmStatus
E = OmEvent
C = OmCause

TABLE = {  # the spec's machine, row by row
    (S.QUEUED, E.CLAIM): Transition(S.SENDING, C.GATE_PASSED),
    (S.SENDING, E.CONFIRM_SENT): Transition(S.SENT, C.SENT),
    (S.UNKNOWN, E.CONFIRM_SENT): Transition(S.SENT, C.SENT),
    (S.SENDING, E.MARK_UNKNOWN): Transition(S.UNKNOWN, C.NOT_FOUND),
    (S.QUEUED, E.RESTORE): Transition(S.UNKNOWN, C.RESTORED),
    (S.SENDING, E.RESTORE): Transition(S.UNKNOWN, C.RESTORED),
    (S.UNKNOWN, E.REQUEUE): Transition(S.QUEUED, C.OPERATOR_REQUEUED),
    (S.UNKNOWN, E.OPERATOR_CANCEL): Transition(S.CANCELLED, C.OPERATOR_CANCELLED),
    (S.QUEUED, E.CANCEL): Transition(S.CANCELLED, C.UNAPPROVED),
}


def test_the_enums_are_closed() -> None:
    assert [str(s) for s in OmStatus] == ["queued", "sending", "sent", "unknown", "cancelled"]
    assert [str(k) for k in OmKind] == ["step", "recontact", "reply", "rebook"]
    assert [str(e) for e in OmEvent] == [
        "claim",
        "confirm_sent",
        "mark_unknown",
        "restore",
        "requeue",
        "operator_cancel",
        "cancel",
    ]
    assert CREATION == Transition(S.QUEUED, C.ENQUEUED)
    assert TERMINAL == (S.SENT, S.CANCELLED)
    assert set(LIVE) == set(OmStatus) - {S.CANCELLED}


def test_the_table_is_the_spec() -> None:
    assert dict(TRANSITIONS) == TABLE
    with pytest.raises(TypeError):
        TRANSITIONS[(S.SENT, E.CLAIM)] = TABLE[(S.QUEUED, E.CLAIM)]  # type: ignore[index]


@pytest.mark.parametrize(("pair", "expected"), list(TABLE.items()))
def test_each_legal_transition(
    pair: tuple[OmStatus, OmEvent], expected: Transition[OmStatus, OmCause]
) -> None:
    assert transition(*pair) == expected


ILLEGAL = [pair for pair in itertools.product(OmStatus, OmEvent) if pair not in TABLE]


@pytest.mark.parametrize("pair", ILLEGAL)
def test_every_other_pair_is_illegal(pair: tuple[OmStatus, OmEvent]) -> None:
    status, event = pair
    with pytest.raises(IllegalTransition) as raised:
        transition(status, event)

    assert raised.value.machine == MACHINE
    assert str(raised.value) == f"{MACHINE}: {event} is not allowed from {status}"


def test_the_terminal_statuses_have_no_way_out() -> None:
    assert not [pair for pair in TABLE if pair[0] in TERMINAL]


def test_an_unhashable_argument_is_illegal() -> None:
    with pytest.raises(IllegalTransition):
        transition([], E.CLAIM)  # type: ignore[arg-type]
