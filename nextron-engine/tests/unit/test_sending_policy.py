"""``nextron.domain.sending_policy`` (Story 6.4, FR-33, FR-34, AD-11): the ramp row of the I/O
matrix day by day, a warm level above the target, an unrecorded inbox, the ceiling and the tool
send figure (never negative), the capacity forecast, the local-date day boundary, and the 6.4
acceptance examples (13 days 0, day 14 the warm level, 30/25/50 gives 25)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from nextron.domain.sending_policy import (
    DEFAULT_CEILING,
    DEFAULT_RAMP_STEP,
    DEFAULT_RAMP_TARGET,
    WarmupPlan,
    capacity_forecast,
    cold_cap,
    daily_cap,
    local_date,
    tool_send_figure,
    warmup_days,
)

START = date(2026, 11, 1)
RAMP = WarmupPlan(START, 12, 0, 5, 30)
WARM = WarmupPlan(START, 30, 0, 5, 30)  # fully warmed from day 14
UNRECORDED = WarmupPlan(None, None)


@pytest.mark.parametrize(
    ("day", "cap"),
    [
        (date(2026, 11, 1), 0),
        (date(2026, 11, 14), 0),  # 13 days
        (date(2026, 11, 15), 12),  # day 14: the warm level
        (date(2026, 11, 21), 12),
        (date(2026, 11, 22), 17),
        (date(2026, 11, 29), 22),
        (date(2026, 12, 6), 27),
        (date(2026, 12, 13), 30),
        (date(2026, 12, 20), 30),
        (date(2027, 6, 1), 30),
    ],
)
def test_the_cap_ramps_from_the_warm_level_by_the_step_a_week_to_the_target(
    day: date, cap: int
) -> None:
    assert daily_cap(RAMP, day) == cap


def test_warm_days_count_calendar_days_and_unrecorded_is_none() -> None:
    assert warmup_days(RAMP, date(2026, 11, 14)) == 13
    assert warmup_days(RAMP, date(2026, 11, 15)) == 14
    assert warmup_days(UNRECORDED, date(2026, 11, 15)) is None
    assert warmup_days(RAMP, START) == 0


def test_a_warm_level_above_the_target_starts_at_the_target() -> None:
    assert daily_cap(WarmupPlan(START, 40, 0, 5, 30), date(2026, 11, 15)) == 30


def test_an_unrecorded_inbox_has_no_cap() -> None:
    assert daily_cap(UNRECORDED, date(2030, 1, 1)) == 0
    assert cold_cap(UNRECORDED, date(2030, 1, 1), 50) == 0


def test_a_warm_level_of_zero_ramps_from_zero() -> None:
    plan = WarmupPlan(START, 0, 0, 5, 30)
    assert daily_cap(plan, date(2026, 11, 15)) == 0
    assert daily_cap(plan, date(2026, 11, 22)) == 5


def test_the_defaults_are_the_planned_ramp_and_ceiling() -> None:
    assert (DEFAULT_RAMP_STEP, DEFAULT_RAMP_TARGET, DEFAULT_CEILING) == (5, 30, 50)
    assert WarmupPlan(START, 12).ramp_step == 5 and WarmupPlan(START, 12).ramp_target == 30


def test_the_ceiling_leaves_the_cold_cap_what_the_tool_sends_do_not_use() -> None:
    tool = WarmupPlan(START, 30, 25, 5, 30)
    day = date(2026, 12, 20)
    assert daily_cap(tool, day) == 30
    assert cold_cap(tool, day, 50) == 25  # 30 / 25 / 50 -> 25
    assert cold_cap(WarmupPlan(START, 30, 10, 5, 30), day, 50) == 30


@pytest.mark.parametrize(("tool", "ceiling"), [(60, 50), (25, 0), (50, 50)])
def test_a_ceiling_at_or_below_the_tool_sends_gives_zero_never_negative(
    tool: int, ceiling: int
) -> None:
    assert cold_cap(WarmupPlan(START, 30, tool, 5, 30), date(2026, 12, 20), ceiling) == 0


def test_the_tool_send_figure_is_the_larger_of_the_tool_daily_and_other_sent() -> None:
    plan = WarmupPlan(START, 30, 20, 5, 30)
    assert tool_send_figure(plan) == 20
    assert tool_send_figure(plan, 35) == 35
    assert tool_send_figure(plan, 5) == 20
    assert cold_cap(plan, date(2026, 12, 20), 50, other_sent=35) == 15


def test_the_forecast_subtracts_the_follow_ups_due_and_is_never_negative() -> None:
    day = date(2026, 12, 20)
    assert capacity_forecast(WARM, day, ceiling=50) == 30
    assert capacity_forecast(WARM, day, ceiling=50, follow_ups_due=12) == 18
    assert capacity_forecast(WARM, day, ceiling=50, follow_ups_due=35) == 0


def test_the_local_date_is_the_calendar_date_in_the_sender_timezone() -> None:
    now = datetime(2026, 11, 14, 23, 30, tzinfo=UTC)
    assert local_date(now, "Asia/Kolkata") == date(2026, 11, 15)
    assert local_date(now, "UTC") == date(2026, 11, 14)
    assert local_date(now, "America/New_York") == date(2026, 11, 14)
    assert local_date(now + timedelta(hours=5), "America/New_York") == date(2026, 11, 14)


def test_the_local_date_needs_an_aware_now() -> None:
    with pytest.raises(ValueError, match="aware"):
        local_date(datetime(2026, 11, 14, 23, 30), "UTC")  # noqa: DTZ001 - the naive probe
