"""The Draft machine (``nextron.domain.draft``, AD-4, AD-21; Story 4.9): the closed lists, every
(status, event, kind) triple is its table row or raises (``log_touch`` only for a ``channel``
Draft), ``CREATION``, ``PENDING`` and ``TERMINAL``."""

import itertools
from types import MappingProxyType

import pytest

from nextron.domain.draft import (
    CREATION,
    MACHINE,
    PENDING,
    STEP_KINDS,
    TERMINAL,
    TRANSITIONS,
    DraftCause,
    DraftEvent,
    DraftKind,
    DraftStatus,
    transition,
)
from nextron.domain.machines import IllegalTransition, Transition

# AD-4's Draft row, written out by hand: (from, event) -> (to, cause).
EXPECTED = {
    ("ready", "approve"): ("approved", "approved"),
    ("approved", "return_edited"): ("ready", "edited"),
    ("approved", "return_expired"): ("ready", "expired"),
    ("approved", "return_event_expired"): ("ready", "event_expired"),
    ("ready", "reject"): ("rejected", "rejected"),
    ("approved", "reject"): ("rejected", "rejected"),
    ("approved", "send"): ("sent", "sent"),
    ("ready", "supersede"): ("superseded", "superseded"),
    ("approved", "supersede"): ("superseded", "superseded"),
    ("ready", "cancel"): ("cancelled", "sequence_stopped"),
    ("approved", "cancel"): ("cancelled", "sequence_stopped"),
    ("ready", "log_touch"): ("sent", "touch_logged"),
}


def test_the_closed_lists() -> None:
    assert MACHINE == "draft"
    assert [str(s) for s in DraftStatus] == [
        "ready",
        "approved",
        "sent",
        "rejected",
        "superseded",
        "cancelled",
    ]
    assert [str(k) for k in DraftKind] == ["step", "recontact", "reply", "rebook", "channel"]
    assert [str(e) for e in DraftEvent] == [
        "approve",
        "return_edited",
        "return_expired",
        "return_event_expired",
        "reject",
        "send",
        "supersede",
        "cancel",
        "log_touch",
    ]
    assert {str(c) for c in DraftCause} == {to_cause[1] for to_cause in EXPECTED.values()} | {
        "requested"
    }
    assert CREATION == Transition(DraftStatus.READY, DraftCause.REQUESTED)
    assert PENDING == (DraftStatus.READY, DraftStatus.APPROVED)
    assert set(TERMINAL) == set(DraftStatus) - set(PENDING)
    assert STEP_KINDS == (DraftKind.STEP, DraftKind.RECONTACT)
    assert isinstance(TRANSITIONS, MappingProxyType)


def test_the_table_is_exactly_ad4s_row() -> None:
    found = {
        (str(status), str(event)): (str(row.to), str(row.cause))
        for (status, event), row in TRANSITIONS.items()
    }
    assert found == EXPECTED


@pytest.mark.parametrize(
    ("status", "event", "kind"),
    list(itertools.product(DraftStatus, DraftEvent, [None, *DraftKind])),
)
def test_every_status_event_and_kind_is_its_row_or_raises(
    status: DraftStatus, event: DraftEvent, kind: DraftKind | None
) -> None:
    expected = EXPECTED.get((str(status), str(event)))
    if event is DraftEvent.LOG_TOUCH and kind is not DraftKind.CHANNEL:
        expected = None  # a touch is logged only on a channel Draft
    if expected is None:
        with pytest.raises(IllegalTransition) as raised:
            transition(status, event, kind=kind)
        assert raised.value.code == "illegal_transition"
        assert str(raised.value) == f"draft: {event} is not allowed from {status}"
        return
    found = transition(status, event, kind=kind)
    assert (str(found.to), str(found.cause)) == expected


def test_no_event_leaves_a_terminal_status() -> None:
    assert all(status not in TERMINAL for status, _ in TRANSITIONS)


def test_approved_never_goes_straight_to_sent_by_a_touch() -> None:
    with pytest.raises(IllegalTransition):
        transition(DraftStatus.APPROVED, DraftEvent.LOG_TOUCH, kind=DraftKind.CHANNEL)


@pytest.mark.parametrize("bad", [None, "readyish", ["ready"], 3])
def test_transition_refuses_what_is_no_status_or_event(bad: object) -> None:
    with pytest.raises(IllegalTransition):
        transition(bad, DraftEvent.APPROVE)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        transition(DraftStatus.READY, bad)  # type: ignore[arg-type]
