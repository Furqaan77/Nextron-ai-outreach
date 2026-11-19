"""``domain.sending_policy`` (AD-11, AD-21; Stories 4.8, 4.9 and 6.4) at fixed dates.

Story 4.8: ``capacity_forecast`` reserves follow-ups first and never goes below 0, and
``pick_inbox`` orders by most capacity, fewest live Enrollments, earliest registration, then id.

Story 4.9: ``step_due_at`` keeps the local wall time across the weekend shift, US and UK DST
changes, an Out-of-Office date, the spring-forward gap (forward) and the autumn fold (first
occurrence).

Story 6.4 (FR-33, FR-34, AD-11): the ramp row of the I/O matrix day by day, a warm level above
the target, an unrecorded inbox, the ceiling and the tool send figure (never negative), the
capacity forecast, the local-date day boundary, and the 6.4 acceptance examples (13 days 0, day
14 the warm level, 30/25/50 gives 25).

Story 6.9 (FR-19, FR-31): the Send Window on weekdays 08:00 to 17:00 in the recipient's zone
(UK and US hours, weekends, the end outside), the US unknown-location window 11:00 to 17:00
Eastern, the 2027 DST gap between 14 and 28 March (London 08:00 is New York 04:00), an unknown
zone and a naive ``now``; ``approval_expired`` at exactly 72 hours."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from nextron import main
from nextron.domain import seed_yaml
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.sending_policy import (
    DEFAULT_CEILING,
    DEFAULT_RAMP_STEP,
    APPROVAL_TTL,
    DEFAULT_RAMP_TARGET,
    SEND_WINDOW,
    InboxLoad,
    WarmupPlan,
    approval_expired,
    capacity_forecast,
    cold_cap,
    daily_cap,
    in_send_window,
    inbox_capacity_forecast,
    local_date,
    pick_inbox,
    step_due_at,
    tool_send_figure,
    warmup_days,
)

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


# Story 6.4 ---------------------------------------------------------------------------------

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
    assert inbox_capacity_forecast(WARM, day, ceiling=50) == 30
    assert inbox_capacity_forecast(WARM, day, ceiling=50, follow_ups_due=12) == 18
    assert inbox_capacity_forecast(WARM, day, ceiling=50, follow_ups_due=35) == 0


def test_the_local_date_is_the_calendar_date_in_the_sender_timezone() -> None:
    now = datetime(2026, 11, 14, 23, 30, tzinfo=UTC)
    assert local_date(now, "Asia/Kolkata") == date(2026, 11, 15)
    assert local_date(now, "UTC") == date(2026, 11, 14)
    assert local_date(now, "America/New_York") == date(2026, 11, 14)
    assert local_date(now + timedelta(hours=5), "America/New_York") == date(2026, 11, 14)


def test_the_local_date_needs_an_aware_now() -> None:
    with pytest.raises(ValueError, match="aware"):
        local_date(datetime(2026, 11, 14, 23, 30), "UTC")  # noqa: DTZ001 - the naive probe


# The Send Window and the approval lifetime (Story 6.9) ------------------------------------------

LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")


def shipped_market(code: str) -> MarketDef:
    text = (main.SEED_DIR / main.MARKETS_FILE_NAME).read_text(encoding="utf-8")
    [found] = [market for market in validate_markets(seed_yaml.parse(text)) if market.code == code]
    return found


UK, US = shipped_market("uk"), shipped_market("us")


def at(zone: ZoneInfo, *parts: int) -> datetime:
    return datetime(*parts, tzinfo=zone)  # type: ignore[misc]


def test_the_send_window_is_8_to_17_on_weekdays() -> None:
    assert SEND_WINDOW.start == time(8, 0) and SEND_WINDOW.end == time(17, 0)


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (at(LONDON, 2026, 11, 16, 7, 59), False),  # Monday 07:59
        (at(LONDON, 2026, 11, 16, 8, 0), True),
        (at(LONDON, 2026, 11, 16, 16, 59), True),
        (at(LONDON, 2026, 11, 16, 17, 0), False),  # the end is outside
        (at(LONDON, 2026, 11, 20, 12, 0), True),  # Friday
        (at(LONDON, 2026, 11, 21, 12, 0), False),  # Saturday
        (at(LONDON, 2026, 11, 22, 12, 0), False),  # Sunday
    ],
)
def test_uk_hours_in_london(moment: datetime, inside: bool) -> None:
    assert in_send_window("Europe/London", UK, moment) is inside
    assert in_send_window("Europe/London", UK, moment.astimezone(UTC)) is inside


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (at(ZoneInfo("America/Chicago"), 2026, 11, 18, 8, 0), True),
        (at(ZoneInfo("America/Chicago"), 2026, 11, 18, 7, 59), False),
        (at(ZoneInfo("America/Chicago"), 2026, 11, 18, 17, 0), False),
        (at(ZoneInfo("America/Chicago"), 2026, 11, 21, 10, 0), False),  # Saturday
    ],
)
def test_us_hours_in_the_contacts_own_zone(moment: datetime, inside: bool) -> None:
    assert in_send_window("America/Chicago", US, moment) is inside


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (at(NEW_YORK, 2026, 11, 18, 10, 59), False),
        (at(NEW_YORK, 2026, 11, 18, 11, 0), True),
        (at(NEW_YORK, 2026, 11, 18, 16, 59), True),
        (at(NEW_YORK, 2026, 11, 18, 17, 0), False),
        (at(NEW_YORK, 2026, 11, 21, 12, 0), False),  # Saturday
    ],
)
def test_a_us_contact_with_an_unknown_zone_gets_11_to_17_eastern(
    moment: datetime, inside: bool
) -> None:
    assert US.unknown_send_window is not None
    assert in_send_window(None, US, moment) is inside


def test_a_uk_contact_with_an_unknown_zone_gets_the_default_window_in_london() -> None:
    assert UK.unknown_send_window is None
    assert in_send_window(None, UK, at(LONDON, 2026, 11, 16, 8, 0)) is True
    assert in_send_window(None, UK, at(LONDON, 2026, 11, 16, 7, 59)) is False


def test_the_2027_dst_gap_between_the_us_and_uk_changes() -> None:
    # the US moves on 14 March 2027, the UK on 28 March: in between London 08:00 is New York 04:00
    london_8 = at(LONDON, 2027, 3, 17, 8, 0)  # a Wednesday in the gap
    assert london_8.astimezone(NEW_YORK).hour == 4
    assert in_send_window("Europe/London", UK, london_8) is True
    assert in_send_window("America/New_York", US, london_8) is False
    assert in_send_window(None, US, london_8) is False
    # New York 11:00 is London 15:00 in the gap (16:00 outside it)
    ny_11 = at(NEW_YORK, 2027, 3, 17, 11, 0)
    assert ny_11.astimezone(LONDON).hour == 15
    assert in_send_window(None, US, ny_11) is True
    assert at(NEW_YORK, 2027, 3, 10, 11, 0).astimezone(LONDON).hour == 16  # before the gap


def test_an_unknown_zone_is_outside_every_window() -> None:
    assert in_send_window("Mars/Olympus", US, at(NEW_YORK, 2026, 11, 18, 12, 0)) is False


def test_a_naive_now_raises() -> None:
    with pytest.raises(ValueError):
        in_send_window("Europe/London", UK, datetime(2026, 11, 16, 9, 0))
    with pytest.raises(ValueError):
        approval_expired(T0, datetime(2026, 11, 16, 9, 0))
    with pytest.raises(ValueError):
        approval_expired(datetime(2026, 11, 16, 9, 0), T0)


def test_an_approval_expires_at_72_hours() -> None:
    assert APPROVAL_TTL == timedelta(hours=72)
    assert approval_expired(T0, T0) is False
    assert approval_expired(T0, T0 + timedelta(hours=71, minutes=59, seconds=59)) is False
    assert approval_expired(T0, T0 + timedelta(hours=72)) is True
    assert approval_expired(T0, T0 + timedelta(days=4)) is True
