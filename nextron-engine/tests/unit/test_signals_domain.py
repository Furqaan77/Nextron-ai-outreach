"""``nextron.domain.signals`` (FR-11, AD-7; Story 2.9): ``age_days`` from the event date or first
seen, and how each class and Tier decays, with the boundary days 29, 30, 31, 89, 90 and 91 on a
fixed ``now``. Story 2.10: ``strongest_event`` (live Events only, weight times factor, the tie
order) and ``preferred_roles`` (a ``when_terms`` phrase in a quote). Standard library, pytest
and ``nextron.domain`` only."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from nextron.domain.segments import (
    EvidenceType,
    PreferredRoles,
    SignalClass,
    SignalSource,
    SignalType,
)
from nextron.domain.signals import (
    EventSignal,
    age_days,
    is_live,
    preferred_roles,
    strongest_event,
    weight_factor,
)

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


# strongest_event (Story 2.10) --------------------------------------------------------------------


def event(
    signal_id: str,
    *,
    tier: int = 1,
    weight: int = 60,
    age: int = 0,
    dated: bool = True,
    seen_minutes: int = 0,
) -> EventSignal:
    when = NOW - timedelta(days=age)
    seen = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=seen_minutes)
    return EventSignal(
        signal_id,
        "law_hiring_legal_ops",
        tier,
        weight,
        when.date() if dated else None,
        seen if dated else when,
    )


def test_the_strongest_event_has_the_highest_weight_times_its_factor() -> None:
    fresh_tier_2 = event("A", tier=2, weight=40, age=10)
    aging_tier_1 = event("B", tier=1, weight=60, age=60)  # 60 * 0.5 = 30
    fresh_tier_1 = event("C", tier=1, weight=60, age=10)

    assert strongest_event([fresh_tier_2, aging_tier_1], NOW) == fresh_tier_2
    assert strongest_event([fresh_tier_2, aging_tier_1, fresh_tier_1], NOW) == fresh_tier_1


def test_only_live_events_count() -> None:
    expired = event("A", tier=1, weight=60, age=90)
    expired_tier_3 = event("B", tier=3, weight=10, age=91)
    live_tier_3 = event("C", tier=3, weight=10, age=90)

    assert strongest_event([expired, expired_tier_3], NOW) is None
    assert strongest_event([expired, expired_tier_3, live_tier_3], NOW) == live_tier_3
    assert strongest_event([], NOW) is None


def test_a_tie_goes_to_the_lower_tier_then_the_younger_then_first_seen_then_the_id() -> None:
    tier_1 = event("Z", tier=1, weight=40, age=50)  # 40 * (40 / 60) = 26.67
    tier_2 = event("A", tier=2, weight=40, age=1)
    assert strongest_event([tier_2, event("Y", tier=1, weight=40, age=1)], NOW).signal_id == "Y"
    assert strongest_event([tier_1, tier_2], NOW) == tier_2  # a higher score wins first

    older = event("A", weight=60, age=5)
    younger = event("B", weight=60, age=3)
    assert strongest_event([older, younger], NOW) == younger
    seen_later = event("A", weight=60, age=3, seen_minutes=5)
    seen_first = event("B", weight=60, age=3, seen_minutes=1)
    assert strongest_event([seen_later, seen_first], NOW) == seen_first
    same = [event("B", weight=60, age=3), event("A", weight=60, age=3)]
    assert strongest_event(same, NOW).signal_id == "A"  # type: ignore[union-attr]


def test_an_undated_event_ages_from_first_seen() -> None:
    undated_old = event("A", weight=60, age=95, dated=False)
    assert strongest_event([undated_old], NOW) is None


def test_strongest_event_refuses_what_isnt_an_event_signal() -> None:
    with pytest.raises(TypeError):
        strongest_event(["A"], NOW)  # type: ignore[list-item]


# preferred_roles (Story 2.10) --------------------------------------------------------------------

HIRING = SignalType(
    key="ops_hiring_manual_entry",
    name="Hiring data-entry, order-entry, AP/AR, or dispatch staff",
    signal_class=SignalClass.EVENT,
    tier=1,
    evidence=(EvidenceType.QUOTE,),
    sources=(SignalSource.CAREERS_PAGE,),
    score_only=False,
    weight=60,
    match_terms=("data entry", "accounts payable", "accounts receivable", "AP/AR", "dispatch"),
    preferred_roles=(
        PreferredRoles(("accounts payable", "accounts receivable", "AP/AR"), ("Controller", "CFO")),
        PreferredRoles(("dispatch",), ("COO", "controller", "Dispatch Manager")),
    ),
)


@pytest.mark.parametrize(
    ("quotes", "roles"),
    [
        (["We are hiring an Accounts Payable Clerk."], ("Controller", "CFO")),
        (["Join us as an AP/AR specialist"], ("Controller", "CFO")),
        (["ACCOUNTS   RECEIVABLE lead"], ("Controller", "CFO")),
        (["A dispatch coordinator"], ("COO", "controller", "Dispatch Manager")),
        (
            ["Dispatch and accounts payable roles"],
            ("Controller", "CFO", "COO", "Dispatch Manager"),  # each role once
        ),
        (["Data entry clerk"], ()),
        (["Our dispatcher team"], ()),  # not a whole phrase
        (["We use SAP/ARIBA"], ()),
        (["xAP/AR"], ()),
        ([], ()),
    ],
)
def test_preferred_roles_need_a_when_term_as_a_whole_phrase(
    quotes: list[str], roles: tuple[str, ...]
) -> None:
    assert preferred_roles(HIRING, quotes) == roles


def test_preferred_roles_refuses_a_bare_string() -> None:
    with pytest.raises(TypeError):
        preferred_roles(HIRING, "Accounts Payable Clerk")
    with pytest.raises(TypeError):
        preferred_roles("ops_hiring_manual_entry", [])  # type: ignore[arg-type]
