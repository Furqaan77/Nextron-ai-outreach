"""The Alert machine and the alert rules (``nextron/domain/alert.py``, AD-4, AD-15)."""

from collections.abc import Iterator
from datetime import timedelta
from types import MappingProxyType
from typing import Any

import pytest

from nextron.domain.alert import (
    CREATION,
    DELIVERY_CHANNELS,
    JOB_DEAD,
    MAX_TEXT_UNITS,
    NOT_DELIVERED,
    RESEND_DELAY,
    RESENDS_UNKNOWN,
    SEND_ALERT_JOB,
    TRANSITIONS,
    AlertCause,
    AlertEvent,
    AlertStatus,
    Channel,
    InvalidAlert,
    check_alert,
    is_telegram_chat_id,
    job_dead_text,
    not_delivered_text,
    transition,
)
from nextron.domain.errors import DomainError
from nextron.domain.machines import IllegalTransition, Transition

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
BOTH = {Channel.TELEGRAM, Channel.DASHBOARD}
PLANTED = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"

# The machine ------------------------------------------------------------------------------------

EXPECTED_TABLE = {
    ("pending", "deliver"): ("sent", "delivered"),
    ("pending", "lose"): ("unknown", "outcome_unknown"),
    ("pending", "fail"): ("failed", "failed"),
    ("unknown", "deliver"): ("sent", "delivered"),
    ("unknown", "fail"): ("failed", "failed"),
}


def test_the_statuses_events_and_causes_are_closed() -> None:
    assert [status.value for status in AlertStatus] == ["pending", "sent", "unknown", "failed"]
    assert [event.value for event in AlertEvent] == ["deliver", "lose", "fail"]
    assert [cause.value for cause in AlertCause] == [
        "requested",
        "delivered",
        "outcome_unknown",
        "failed",
    ]


def test_the_transition_table_is_exactly_the_specified_one() -> None:
    assert isinstance(TRANSITIONS, MappingProxyType)
    assert {
        (str(status), str(event)): (str(change.to), str(change.cause))
        for (status, event), change in TRANSITIONS.items()
    } == EXPECTED_TABLE
    for (status, event), change in TRANSITIONS.items():
        assert transition(status, event) is change
        assert isinstance(change, Transition)


def test_a_creation_is_pending_because_it_was_requested() -> None:
    assert (CREATION.to, CREATION.cause) == (AlertStatus.PENDING, AlertCause.REQUESTED)


def every_missing_row() -> Iterator[tuple[AlertStatus, AlertEvent]]:
    for status in AlertStatus:
        for event in AlertEvent:
            if (status, event) not in TRANSITIONS:
                yield status, event


@pytest.mark.parametrize(("status", "event"), list(every_missing_row()))
def test_every_other_move_is_illegal(status: AlertStatus, event: AlertEvent) -> None:
    with pytest.raises(IllegalTransition) as raised:
        transition(status, event)

    assert raised.value.code == "illegal_transition"
    assert str(raised.value) == f"alert: {event} is not allowed from {status}"


def test_sent_and_failed_are_final_and_unknown_is_never_lost_twice() -> None:
    for status in (AlertStatus.SENT, AlertStatus.FAILED):
        assert not [event for event in AlertEvent if (status, event) in TRANSITIONS]
    assert (AlertStatus.UNKNOWN, AlertEvent.LOSE) not in TRANSITIONS


@pytest.mark.parametrize("bad", [[], "queued", None])
def test_an_unhashable_or_foreign_argument_is_illegal_too(bad: Any) -> None:
    with pytest.raises(IllegalTransition):
        transition(bad, AlertEvent.DELIVER)


# Channels, names and timing -------------------------------------------------------------------


def test_the_channels_and_their_sets() -> None:
    assert [channel.value for channel in Channel] == ["telegram", "dashboard"]
    assert DELIVERY_CHANNELS == frozenset({Channel.TELEGRAM})
    assert RESENDS_UNKNOWN == frozenset({Channel.TELEGRAM})


def test_the_names_the_timing_and_the_text_limit() -> None:
    assert (SEND_ALERT_JOB, JOB_DEAD, NOT_DELIVERED) == (
        "send_alert",
        "job_dead",
        "alert_not_delivered",
    )
    assert RESEND_DELAY == timedelta(seconds=60)
    assert MAX_TEXT_UNITS == 4096


# check_alert -----------------------------------------------------------------------------------


def test_a_valid_alert_returns_its_channels() -> None:
    assert check_alert("job_dead", ULID, "Job dead: x", BOTH) == frozenset(BOTH)
    assert check_alert("k", "a:b.c-d_9", "line one\nline two", [Channel.DASHBOARD]) == {
        Channel.DASHBOARD
    }
    assert check_alert("k" * 32, "x" * 64, "t", (c for c in [Channel.TELEGRAM])) == {
        Channel.TELEGRAM
    }
    assert check_alert("k", "s", "x" * MAX_TEXT_UNITS, BOTH) == BOTH
    assert check_alert("k", "s", "\U0001f600" * (MAX_TEXT_UNITS // 2), BOTH) == BOTH


REFUSALS = [
    ({"kind": "Job-Dead"}, "the kind must match"),
    ({"kind": ""}, "the kind must match"),
    ({"kind": "9job"}, "the kind must match"),
    ({"kind": "k" * 33}, "the kind must match"),
    ({"kind": None}, "the kind must match"),
    ({"subject": "a b"}, "the subject must be"),
    ({"subject": ""}, "the subject must be"),
    ({"subject": "x" * 65}, "the subject must be"),
    ({"subject": "UPPER"}, "the subject must be"),
    ({"subject": ULID.lower() + "!"}, "the subject must be"),
    ({"subject": 42}, "the subject must be"),
    ({"text": None}, "the text must be a str"),
    ({"text": b"bytes"}, "the text must be a str"),
    ({"text": ""}, "the text must not be blank"),
    ({"text": " \n\t "}, "the text must not be blank"),
    ({"text": "x" * (MAX_TEXT_UNITS + 1)}, "exceeds 4,096 UTF-16 code units"),
    ({"text": "x" + "\U0001f600" * (MAX_TEXT_UNITS // 2)}, "exceeds 4,096 UTF-16 code units"),
    ({"text": "tab\there"}, "no control"),
    ({"text": "cr\rhere"}, "no control"),
    ({"text": "bell\x07"}, "no control"),
    ({"text": "rtl‮override"}, "no control"),
    ({"text": "zero​width"}, "no control"),
    ({"text": "line sep"}, "no control"),
    ({"text": "para sep"}, "no control"),
    ({"text": "lone\ud800"}, "no control"),
    ({"channels": []}, "at least one channel"),
    ({"channels": "telegram"}, "not a str"),
    ({"channels": Channel.TELEGRAM}, "not a str"),
    ({"channels": b"telegram"}, "not a str"),
    ({"channels": None}, "not a str"),
    ({"channels": ["telegram"]}, "every channel must be a Channel member"),
    ({"channels": [Channel.TELEGRAM, "dashboard"]}, "every channel must be a Channel member"),
]


@pytest.mark.parametrize(("change", "reason"), REFUSALS)
def test_check_alert_refuses(change: dict[str, Any], reason: str) -> None:
    fields: dict[str, Any] = {"kind": "job_dead", "subject": ULID, "text": "t", "channels": BOTH}
    fields.update(change)
    if "text" not in change and "kind" in change:
        fields["text"] = PLANTED  # a refusal never repeats a value

    with pytest.raises(InvalidAlert) as raised:
        check_alert(**fields)

    assert reason in raised.value.reason
    assert raised.value.code == "invalid_alert"
    assert isinstance(raised.value, DomainError)
    shown = str(raised.value)
    for value in change.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in shown
    assert PLANTED not in shown


# Chat ids and texts -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["1", "-1001234567890", "123456789", "-1", "9" + "0" * 19, "-" + "1" * 20]
)
def test_a_numeric_chat_id_is_accepted(value: str) -> None:
    assert is_telegram_chat_id(value)


@pytest.mark.parametrize(
    "value",
    ["", "0", "-0", "01", "@channel", "1 ", " 1", "1" * 21, "+1", "1.0", "١٢٣", 123, None],
)
def test_anything_else_is_not_a_chat_id(value: object) -> None:
    assert not is_telegram_chat_id(value)


def test_the_job_dead_text_is_exact() -> None:
    assert job_dead_text("send_due_messages", ULID, 3) == (
        f"Job dead: send_due_messages {ULID}, attempts: 3"
    )


def test_the_not_delivered_texts_are_exact() -> None:
    assert not_delivered_text("job_dead", ULID, "telegram_refused") == (
        f"Telegram alert not delivered: job_dead {ULID}, telegram_refused"
    )
    assert not_delivered_text("job_dead", ULID, "alert_delivery_unknown", maybe=True) == (
        f"Telegram alert may not have been delivered: job_dead {ULID}, alert_delivery_unknown"
    )
