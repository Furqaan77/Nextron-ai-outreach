"""Story 8.10's pure rules (``nextron.domain.discovery_topup``): weekdays, the days left in the
month, the local day's start, the Places day allowance (the spec's 45 and 0), the pool check,
the rotation (the spec's never-run, oldest and recent pairs) and the three settings."""

from datetime import UTC, date, datetime, timedelta

import pytest

from nextron.domain.discovery_topup import (
    day_start_utc,
    is_weekday,
    local_date_of,
    next_pair,
    places_day_allowance,
    pool_sufficient,
    weekdays_left,
)
from nextron.domain.place_discovery import RunStatus
from nextron.domain.settings_schema import InvalidSettingValue, Key, parse, setting

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "weekday"),
    [
        (date(2026, 11, 16), True),  # Monday
        (date(2026, 11, 20), True),  # Friday
        (date(2026, 11, 21), False),  # Saturday
        (date(2026, 11, 22), False),  # Sunday
    ],
)
def test_weekdays_are_monday_to_friday(day: date, weekday: bool) -> None:
    assert is_weekday(day) is weekday


@pytest.mark.parametrize(
    ("day", "left"),
    [
        (date(2026, 11, 2), 21),  # Monday 2 November: every weekday of the month but the 1st
        (date(2026, 11, 3), 20),
        (date(2026, 11, 19), 8),  # Thursday: 19, 20, 23-27, 30
        (date(2026, 11, 30), 1),  # the month's last day, a Monday, counts itself
        (date(2026, 10, 31), 0),  # a Saturday on the last day
        (date(2026, 12, 31), 1),  # December's last day, a Thursday
        (date(2028, 2, 29), 1),  # a leap day, a Tuesday
    ],
)
def test_weekdays_left_count_today_to_the_end_of_the_month(day: date, left: int) -> None:
    assert weekdays_left(day) == left


def test_the_local_day_starts_in_the_operator_s_zone() -> None:
    assert day_start_utc(date(2026, 11, 19), "UTC") == datetime(2026, 11, 19, tzinfo=UTC)
    assert day_start_utc(date(2026, 11, 19), "America/New_York") == datetime(
        2026, 11, 19, 5, 0, tzinfo=UTC
    )
    assert day_start_utc(date(2026, 7, 1), "Europe/London") == datetime(
        2026, 6, 30, 23, 0, tzinfo=UTC
    )
    late = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
    assert local_date_of(late, "America/New_York") == date(2026, 11, 19)
    assert local_date_of(late, "UTC") == date(2026, 11, 20)


def test_a_naive_now_or_a_datetime_for_a_date_is_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        local_date_of(datetime(2026, 11, 19), "UTC")  # noqa: DTZ001
    with pytest.raises(TypeError):
        weekdays_left(NOW)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("quota", "used", "days", "allowance"),
    [
        (1000, 0, 22, 45),  # the spec's example
        (1000, 990, 20, 0),  # 10 left over 20 weekdays
        (1000, 1200, 5, 0),  # over the quota: nothing
        (1000, 0, 0, 0),  # no weekday left
        (30, 0, 9, 3),
        (0, 0, 5, 0),  # a quota of 0 stays a hard stop
    ],
)
def test_the_places_day_allowance(quota: int, used: int, days: int, allowance: int) -> None:
    assert places_day_allowance(quota, used, days) == allowance


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3"])
def test_the_allowance_takes_counts_only(bad: object) -> None:
    with pytest.raises(ValueError, match="int"):
        places_day_allowance(bad, 0, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ready", "days", "share", "sufficient"),
    [
        (30, 5, 6, True),  # the spec's example: 30 >= 5 x 6
        (29, 5, 6, False),
        (0, 5, 0, True),  # no capacity share: nothing to top up
    ],
)
def test_the_pool_is_sufficient_when_it_covers_the_days(
    ready: int, days: int, share: int, sufficient: bool
) -> None:
    assert pool_sufficient(ready, days, share) is sufficient


def test_rotation_takes_a_never_run_pair_then_the_oldest_and_skips_recent_ones() -> None:
    pairs = ("A", "B", "C")
    last = {"A": NOW - timedelta(days=40), "B": NOW - timedelta(days=10)}

    assert next_pair(pairs, last, NOW, 30) == "C"
    assert next_pair(pairs, {**last, "C": NOW}, NOW, 30) == "A"  # B (10 days) is skipped
    assert next_pair(pairs, {"A": NOW, "B": NOW, "C": NOW}, NOW, 30) is None
    assert next_pair((), {}, NOW, 30) is None


def test_rotation_picks_the_oldest_and_the_first_on_a_tie_and_counts_whole_hours() -> None:
    old = NOW - timedelta(days=50)
    assert next_pair(("A", "B", "C"), {"A": NOW, "B": old, "C": old}, NOW, 30) == "B"
    exactly = NOW - timedelta(hours=30 * 24)
    assert next_pair(("A",), {"A": exactly}, NOW, 30) == "A"  # due at exactly 30 x 24 h
    assert next_pair(("A",), {"A": exactly + timedelta(seconds=1)}, NOW, 30) is None


def test_the_new_statuses() -> None:
    assert {"target_reached", "paced", "pool_sufficient"} <= {str(s) for s in RunStatus}


@pytest.mark.parametrize(
    ("key", "default", "low", "high"),
    [
        (Key.DISCOVERY_POOL_DAYS, 5, 1, 60),
        (Key.DISCOVERY_REQUERY_DAYS, 30, 1, 365),
        (Key.DISCOVERY_DAILY_LEAD_TARGET, 60, 0, 1000),
    ],
)
def test_the_three_settings(key: str, default: int, low: int, high: int) -> None:
    found = setting(key)
    assert found.default == default
    assert found.section == "Budget and limits"
    assert parse(found, str(low)) == low
    assert parse(found, str(high)) == high
    for bad in (str(low - 1), str(high + 1)):
        with pytest.raises(InvalidSettingValue):
            parse(found, bad)
