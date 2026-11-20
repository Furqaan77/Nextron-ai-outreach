"""The Enrollment machine and its rules (``nextron.domain.enrollment``, AD-4, AD-21; Story 4.8):
the closed lists, every (status, event) pair is its table row or raises, ``CREATION``, the
refusal and result types, and ``next_variant`` (round-robin in name order, skipping inactive
Variants, 100 picks split 50/50)."""

import itertools
from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType

import pytest

from nextron.domain.campaign import VariantStatus
from nextron.domain.contactability import BlockReason
from nextron.domain.eligibility import Reason, ReasonKind
from nextron.domain.enrollment import (
    CREATION,
    ENDED_STATUSES,
    LIVE_STATUSES,
    MACHINE,
    PAUSE_EVENTS,
    REPLY_CAUSES,
    STOP_EVENTS,
    TRANSITIONS,
    EnrollmentCause,
    EnrollmentEvent,
    EnrollmentOrigin,
    EnrollmentStatus,
    EnrollRefusal,
    NewEnrollment,
    PauseCause,
    RefusalCode,
    StopCause,
    next_variant,
    transition,
)
from nextron.domain.machines import IllegalTransition, Transition

PAUSES = ["inbound_pending", "uncertain_class", "operator"]
STOPS = [
    "reply",
    "manual_reply",
    "hard_bounce",
    "suppressed",
    "operator_stop",
    "operator_rejected",
    "lead_closed",
    "inbox_removed",
    "contact_erased",
]
# The Design Notes table, written out by hand: (from, event) -> (to, cause).
EXPECTED = {
    **{("active", f"pause_{cause}"): ("paused", cause) for cause in PAUSES},
    ("paused", "resume"): ("active", "resumed"),
    **{
        (source, f"stop_{cause}"): ("stopped", cause)
        for cause in STOPS
        for source in ("active", "paused")
    },
    ("active", "complete"): ("completed", "completed"),
    ("paused", "complete"): ("completed", "completed"),
}


def test_the_closed_lists() -> None:
    assert [str(s) for s in EnrollmentStatus] == ["active", "paused", "stopped", "completed"]
    assert [str(o) for o in EnrollmentOrigin] == [
        "selection",
        "operator_pick",
        "referral",
        "recontact",
        "restart",
    ]
    assert [str(c) for c in PauseCause] == PAUSES
    assert [str(c) for c in StopCause] == STOPS
    assert [str(e) for e in EnrollmentEvent] == [
        *(f"pause_{cause}" for cause in PAUSES),
        "resume",
        *(f"stop_{cause}" for cause in STOPS),
        "complete",
    ]
    assert {str(c) for c in EnrollmentCause} == {
        "enrolled",
        *PAUSES,
        "resumed",
        *STOPS,
        "completed",
    }
    assert LIVE_STATUSES == (EnrollmentStatus.ACTIVE, EnrollmentStatus.PAUSED)
    assert ENDED_STATUSES == (EnrollmentStatus.STOPPED, EnrollmentStatus.COMPLETED)
    assert REPLY_CAUSES == {StopCause.REPLY, StopCause.MANUAL_REPLY}
    assert CREATION == Transition(EnrollmentStatus.ACTIVE, EnrollmentCause.ENROLLED)
    assert {str(k): str(v) for k, v in PAUSE_EVENTS.items()} == {
        cause: f"pause_{cause}" for cause in PAUSES
    }
    assert {str(k): str(v) for k, v in STOP_EVENTS.items()} == {
        cause: f"stop_{cause}" for cause in STOPS
    }


def test_the_table_is_exactly_the_design_notes_table() -> None:
    assert isinstance(TRANSITIONS, MappingProxyType)
    assert {
        (str(status), str(event)): (str(row.to), str(row.cause))
        for (status, event), row in TRANSITIONS.items()
    } == EXPECTED


@pytest.mark.parametrize(
    ("status", "event"), list(itertools.product(EnrollmentStatus, EnrollmentEvent))
)
def test_every_status_and_event_pair_is_its_row_or_raises(
    status: EnrollmentStatus, event: EnrollmentEvent
) -> None:
    expected = EXPECTED.get((str(status), str(event)))
    if expected is None:
        with pytest.raises(IllegalTransition) as caught:
            transition(status, event)
        assert caught.value.machine == MACHINE
        assert str(caught.value) == f"enrollment: {event} is not allowed from {status}"
    else:
        found = transition(status, event)
        assert (str(found.to), str(found.cause)) == expected


def test_no_event_leaves_an_ended_status() -> None:
    assert not [key for key in TRANSITIONS if key[0] in ENDED_STATUSES]


@pytest.mark.parametrize("bad", ["nope", None, 3, [], {}])
def test_transition_refuses_what_is_no_status_or_event(bad: object) -> None:
    with pytest.raises(IllegalTransition):
        transition(bad, EnrollmentEvent.RESUME)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        transition(EnrollmentStatus.ACTIVE, bad)  # type: ignore[arg-type]


def test_refusal_codes_are_in_enroll_order() -> None:
    assert [str(code) for code in RefusalCode] == [
        "blocked",
        "restart_not_allowed",
        "campaign_not_active",
        "missing_tz_or_market",
        "not_eligible",
        "campaign_mismatch",
        "lead_already_replied",
        "lead_already_enrolled",
        "no_active_variant",
        "no_sending_inbox",
    ]


def test_a_refusal_holds_reasons_only_for_its_code() -> None:
    reason = Reason(ReasonKind.LEAD_CLOSED, "The Lead is closed")
    assert EnrollRefusal(RefusalCode.NOT_ELIGIBLE, reasons=(reason,)).reasons == (reason,)
    blocked = EnrollRefusal(RefusalCode.BLOCKED, blocks=(BlockReason.CONTACT_ERASED,))
    assert blocked.blocks == (BlockReason.CONTACT_ERASED,)
    assert EnrollRefusal(RefusalCode.NO_SENDING_INBOX) == EnrollRefusal(
        RefusalCode.NO_SENDING_INBOX, (), ()
    )
    with pytest.raises(ValueError, match="not_eligible"):
        EnrollRefusal(RefusalCode.BLOCKED, reasons=(reason,))
    with pytest.raises(ValueError, match="blocked"):
        EnrollRefusal(RefusalCode.NOT_ELIGIBLE, blocks=(BlockReason.CONTACT_ERASED,))
    with pytest.raises(TypeError):
        EnrollRefusal("blocked")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        EnrollRefusal(RefusalCode.NOT_ELIGIBLE, reasons=["x"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        EnrollRefusal(RefusalCode.BLOCKED, blocks=("x",))  # type: ignore[arg-type]


def test_new_enrollment_holds_what_was_bound() -> None:
    found = NewEnrollment("e", "l", "c", "camp", "v", "i")
    assert (found.variant_id, found.sending_inbox_id) == ("v", "i")


# next_variant ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class V:
    id: str
    status: VariantStatus = VariantStatus.ACTIVE


A, B = V("a"), V("b")
B_OFF = V("b", VariantStatus.INACTIVE)


def test_next_variant_starts_at_the_first_active_one() -> None:
    assert next_variant([A, B], None) == "a"
    assert next_variant([V("a", VariantStatus.INACTIVE), B], None) == "b"
    assert next_variant([A, B], "gone") == "a"  # a counter naming no current Variant


def test_next_variant_alternates_and_wraps() -> None:
    assert next_variant([A, B], "a") == "b"
    assert next_variant([A, B], "b") == "a"
    assert next_variant([A], "a") == "a"


def test_an_inactive_variant_is_skipped() -> None:
    assert next_variant([A, B_OFF], None) == "a"
    assert next_variant([A, B_OFF], "a") == "a"
    assert next_variant([A, B_OFF], "b") == "a"  # the counter named it before it was retired


def test_no_active_variant_is_none() -> None:
    assert next_variant([], None) is None
    assert next_variant([V("a", VariantStatus.INACTIVE), B_OFF], "a") is None


def test_one_hundred_picks_split_fifty_fifty() -> None:
    last = None
    picks = []
    for _ in range(100):
        last = next_variant([A, B], last)
        picks.append(last)
    assert Counter(picks) == {"a": 50, "b": 50}
    assert picks[:4] == ["a", "b", "a", "b"]
