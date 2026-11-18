"""``domain.sending_policy`` (AD-11, AD-21; Stories 4.8 and 4.9) at fixed dates:
``capacity_forecast`` reserves follow-ups first and never goes below 0, ``pick_inbox`` orders by
most capacity, fewest live Enrollments, earliest registration, then id, and ``step_due_at``
keeps the local wall time across the weekend shift, US and UK DST changes, an Out-of-Office
date, the spring-forward gap (forward) and the autumn fold (first occurrence)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from nextron.domain.sending_policy import InboxLoad, capacity_forecast, pick_inbox, step_due_at

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=1)


def test_capacity_reserves_follow_ups_first() -> None:
    assert capacity_forecast(20, 0) == 20
    assert capacity_forecast(20, 5) == 15
    assert capacity_forecast(20, 20) == 0
    assert capacity_forecast(5, 9) == 0  # never below 0
    assert capacity_forecast(0, 0) == 0  # Story 4.8's placeholder caps


@pytest.mark.parametrize("bad", [True, 1.0, "3", None])
def test_capacity_refuses_what_is_no_count(bad: object) -> None:
    with pytest.raises(TypeError):
        capacity_forecast(bad, 0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        capacity_forecast(0, bad)  # type: ignore[arg-type]


def test_capacity_refuses_a_negative_count() -> None:
    with pytest.raises(ValueError):
        capacity_forecast(-1, 0)
    with pytest.raises(ValueError):
        capacity_forecast(0, -1)


def load(inbox_id: str, capacity: int = 0, live: int = 0, at: datetime = T0) -> InboxLoad:
    return InboxLoad(inbox_id, capacity, live, at)


def test_pick_inbox_prefers_capacity_then_fewest_live_then_earliest_then_id() -> None:
    assert pick_inbox([load("a", 1, live=9), load("b", 5, live=9)]) == "b"
    assert pick_inbox([load("a", live=2), load("b", live=1)]) == "b"
    assert pick_inbox([load("a", at=LATER), load("b", at=T0)]) == "b"
    assert pick_inbox([load("b"), load("a")]) == "a"
    assert pick_inbox([load("only")]) == "only"


def test_pick_inbox_with_no_load_is_none() -> None:
    assert pick_inbox([]) is None


def test_inbox_load_and_pick_inbox_refuse_bad_values() -> None:
    with pytest.raises(TypeError):
        InboxLoad("a", 0, 0, datetime(2026, 11, 18))  # naive
    with pytest.raises(TypeError):
        InboxLoad(1, 0, 0, T0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        InboxLoad("a", 0, -1, T0)
    with pytest.raises(TypeError):
        pick_inbox(["a"])  # type: ignore[list-item]


# step_due_at (Story 4.9) -------------------------------------------------------------------------

NY, LONDON = "America/New_York", "Europe/London"


@pytest.mark.parametrize(
    ("sent", "delay", "zone", "due"),
    [
        # Friday 18:30 in New York (EST) + 3 days: Monday 18:30
        (datetime(2026, 11, 20, 23, 30, tzinfo=UTC), 3, NY, datetime(2026, 11, 23, 23, 30)),
        # Wednesday + 3 lands on Saturday, Thursday + 3 on Sunday: both Monday, same time
        (datetime(2026, 11, 18, 14, 0, tzinfo=UTC), 3, NY, datetime(2026, 11, 23, 14, 0)),
        (datetime(2026, 11, 19, 14, 0, tzinfo=UTC), 3, NY, datetime(2026, 11, 23, 14, 0)),
        # a weekday + 0 is due at once; a Saturday + 0 (Step 1) is Monday, same local time
        (datetime(2026, 11, 18, 14, 0, tzinfo=UTC), 0, NY, datetime(2026, 11, 18, 14, 0)),
        (datetime(2026, 11, 21, 14, 0, tzinfo=UTC), 0, NY, datetime(2026, 11, 23, 14, 0)),
        # US spring forward (14 March 2027): Friday 09:00 EST + 3 = Monday 09:00 EDT
        (datetime(2027, 3, 12, 14, 0, tzinfo=UTC), 3, NY, datetime(2027, 3, 15, 13, 0)),
        # US fall back (7 November 2027): Friday 09:00 EDT + 3 = Monday 09:00 EST
        (datetime(2027, 11, 5, 13, 0, tzinfo=UTC), 3, NY, datetime(2027, 11, 8, 14, 0)),
        # UK spring forward (28 March 2027): Thursday 10:00 GMT + 4 = Monday 10:00 BST
        (datetime(2027, 3, 25, 10, 0, tzinfo=UTC), 4, LONDON, datetime(2027, 3, 29, 9, 0)),
        # UK fall back (25 October 2026): Friday 10:00 BST + 3 = Monday 10:00 GMT
        (datetime(2026, 10, 23, 9, 0, tzinfo=UTC), 3, LONDON, datetime(2026, 10, 26, 10, 0)),
        # the local date counts, not the UTC one: Friday 22:00 in New York is Saturday in UTC
        (datetime(2026, 11, 21, 3, 0, tzinfo=UTC), 3, NY, datetime(2026, 11, 24, 3, 0)),
    ],
)
def test_step_due_at_keeps_the_local_time_and_skips_the_weekend(
    sent: datetime, delay: int, zone: str, due: datetime
) -> None:
    found = step_due_at(sent, delay, zone)
    assert found == due.replace(tzinfo=UTC)
    assert found.tzinfo is UTC


def test_step_due_at_takes_an_out_of_office_date_when_it_is_later() -> None:
    wednesday = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)  # 09:00 in New York

    later = step_due_at(wednesday, 3, NY, not_before=date(2026, 11, 25))
    weekend = step_due_at(wednesday, 3, NY, not_before=date(2026, 11, 28))  # a Saturday
    earlier = step_due_at(wednesday, 3, NY, not_before=date(2026, 11, 19))

    assert later == datetime(2026, 11, 25, 14, 0, tzinfo=UTC)
    assert weekend == datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
    assert earlier == datetime(2026, 11, 23, 14, 0, tzinfo=UTC)  # the plain rule wins


def test_a_time_in_the_spring_forward_gap_resolves_forward() -> None:
    # Israel moves its clocks on Friday 26 March 2027 at 02:00 (a weekday): 02:30 doesn't exist
    thursday = datetime(2027, 3, 25, 0, 30, tzinfo=UTC)  # 02:30 IST

    found = step_due_at(thursday, 1, "Asia/Jerusalem")

    assert found == datetime(2027, 3, 26, 0, 30, tzinfo=UTC)  # 03:30 IDT, an hour on


def test_an_ambiguous_time_takes_its_first_occurrence() -> None:
    # Egypt's clocks go back at the end of Thursday 28 October 2027: 23:30 happens twice
    wednesday = datetime(2027, 10, 27, 20, 30, tzinfo=UTC)  # 23:30 EEST

    found = step_due_at(wednesday, 1, "Africa/Cairo")

    assert found == datetime(2027, 10, 28, 20, 30, tzinfo=UTC)  # the first 23:30 (UTC+3)


def test_step_due_at_refuses_bad_arguments() -> None:
    with pytest.raises(TypeError):
        step_due_at(datetime(2026, 11, 18, 9, 0), 3, NY)  # naive
    with pytest.raises(TypeError):
        step_due_at("2026-11-18", 3, NY)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        step_due_at(T0, True, NY)
    with pytest.raises(TypeError):
        step_due_at(T0, 3.0, NY)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        step_due_at(T0, -1, NY)
    with pytest.raises(TypeError):
        step_due_at(T0, 3, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        step_due_at(T0, 3, "Mars/Olympus")
    with pytest.raises(ValueError):
        step_due_at(T0, 3, "")
    with pytest.raises(TypeError):
        step_due_at(T0, 3, NY, not_before=T0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        step_due_at(T0, 3, NY, not_before="2026-12-01")  # type: ignore[arg-type]
