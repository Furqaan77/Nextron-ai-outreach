"""``domain.sending_policy`` (AD-11, AD-21; Story 4.8) at fixed dates: ``capacity_forecast``
reserves follow-ups first and never goes below 0, and ``pick_inbox`` orders by most capacity,
fewest live Enrollments, earliest registration, then id."""

from datetime import UTC, datetime, timedelta

import pytest

from nextron.domain.sending_policy import InboxLoad, capacity_forecast, pick_inbox

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
