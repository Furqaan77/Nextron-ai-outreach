"""``nextron.domain.signals`` (FR-11, AD-7; Story 2.9): ``age_days`` from the event date or first
seen, and how each class and Tier decays, with the boundary days 29, 30, 31, 89, 90 and 91 on a
fixed ``now``. Standard library, pytest and ``nextron.domain`` only."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from nextron.domain.segments import SignalClass
from nextron.domain.signals import age_days, is_live, weight_factor

NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
EVENT = SignalClass.EVENT
ATTRIBUTE = SignalClass.ATTRIBUTE


# age_days ----------------------------------------------------------------------------------------


def test_the_age_counts_from_the_event_date_when_there_is_one() -> None:
    first_seen = datetime(2026, 11, 1, tzinfo=UTC)

    assert age_days(date(2026, 10, 18), first_seen, NOW) == 31
    assert age_days(None, first_seen, NOW) == 17


@pytest.mark.parametrize(
    ("first_seen", "age"),
    [
        (datetime(2026, 11, 17, 23, 59, tzinfo=UTC), 1),  # yesterday, minutes ago: a whole day
        (datetime(2026, 11, 18, 0, 0, tzinfo=UTC), 0),
        # 2026-11-18 01:00 in UTC+2 is 2026-11-17 23:00 UTC: counted in UTC
        (datetime(2026, 11, 18, 1, 0, tzinfo=timezone(timedelta(hours=2))), 1),
    ],
)
def test_the_age_counts_whole_utc_calendar_days(first_seen: datetime, age: int) -> None:
    assert age_days(None, first_seen, NOW) == age


def test_the_age_is_never_negative() -> None:
    assert age_days(date(2026, 12, 1), NOW, NOW) == 0
    assert age_days(None, NOW + timedelta(days=3), NOW) == 0


@pytest.mark.parametrize(
    ("event_date", "first_seen", "now"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), NOW, NOW),  # a datetime is not a date
        ("2026-01-01", NOW, NOW),
        (None, datetime(2026, 1, 1), NOW),  # naive
        (None, NOW, datetime(2026, 1, 1)),
        (None, "2026-01-01", NOW),
    ],
)
def test_age_days_refuses_the_wrong_types(
    event_date: object, first_seen: object, now: object
) -> None:
    with pytest.raises(TypeError):
        age_days(event_date, first_seen, now)  # type: ignore[arg-type]


# weight_factor and is_live -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "factor", "live"),
    [
        (0, 1.0, True),
        (29, 1.0, True),
        (30, 1.0, True),
        (31, 59 / 60, True),
        (60, 0.5, True),
        (89, 1 / 60, True),
        (90, 0.0, False),
        (91, 0.0, False),
        (400, 0.0, False),
    ],
)
def test_a_tier_1_event_falls_linearly_from_day_30_to_0_at_day_90(
    age: int, factor: float, live: bool
) -> None:
    assert weight_factor(EVENT, 1, age) == pytest.approx(factor)
    assert is_live(EVENT, 1, age) is live


@pytest.mark.parametrize("tier", [2, 3])
@pytest.mark.parametrize(
    ("age", "factor", "live"),
    [
        (0, 1.0, True),
        (29, 1.0, True),
        (30, 1.0, True),
        (31, 1.0, True),
        (89, 1.0, True),
        (90, 1.0, True),
        (91, 0.0, False),
        (365, 0.0, False),
    ],
)
def test_a_tier_2_or_3_event_keeps_its_weight_through_day_90_then_expires(
    tier: int, age: int, factor: float, live: bool
) -> None:
    assert weight_factor(EVENT, tier, age) == factor
    assert is_live(EVENT, tier, age) is live


@pytest.mark.parametrize("tier", [1, 2, 3])
@pytest.mark.parametrize("age", [0, 29, 30, 31, 89, 90, 91, 10_000])
def test_attributes_dont_decay(tier: int, age: int) -> None:
    assert weight_factor(ATTRIBUTE, tier, age) == 1.0
    assert is_live(ATTRIBUTE, tier, age) is True


@pytest.mark.parametrize(
    ("signal_class", "tier", "age", "error"),
    [
        ("event", 1, 0, TypeError),  # a str is not a SignalClass
        (EVENT, True, 0, TypeError),
        (EVENT, 1.0, 0, TypeError),
        (EVENT, 0, 0, ValueError),
        (EVENT, 4, 0, ValueError),
        (EVENT, 1, -1, ValueError),
        (EVENT, 1, 1.5, TypeError),
        (ATTRIBUTE, 1, False, TypeError),
    ],
)
def test_bad_arguments_are_refused(
    signal_class: object, tier: object, age: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        weight_factor(signal_class, tier, age)  # type: ignore[arg-type]
    with pytest.raises(error):
        is_live(signal_class, tier, age)  # type: ignore[arg-type]
