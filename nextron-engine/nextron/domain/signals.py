"""How a Signal ages (FR-11, AD-7; Story 2.9). Standard library and ``nextron.domain`` only.

A Signal is an Event (something that happened: a hire, a post, an expansion) or an Attribute (a
standing fact about the organization). Only this module computes a Signal's age and what it is
still worth; the scores (Story 2.15) and the Lead page read them here.

- ``age_days(event_date, first_seen_at, now)``: the whole UTC calendar days from the Signal's
  event date, or from the UTC date of ``first_seen_at`` when it has none, to ``now``'s UTC date.
  It is never negative: an event date after today, or a first-seen time after ``now``, is age 0.
- ``weight_factor(signal_class, tier, age)``: what share of its weight a Signal of that class and
  Tier still carries at ``age`` days:

  - **a Tier 1 Event**: 1.0 through day 30, then a linear fall to 0 at day 90, so
    ``(90 - age) / 60`` from day 31 to day 89, and 0 from day 90;
  - **a Tier 2 or Tier 3 Event**: 1.0 through day 90, then 0;
  - **an Attribute**: always 1.0 (Attributes don't decay).

- ``is_live(signal_class, tier, age)``: whether the Signal still counts. A Tier 1 Event is live
  while ``age < 90``; a Tier 2 or 3 Event through day 90 (``age <= 90``); an Attribute always.

Each raises ``TypeError`` unless ``signal_class`` is a ``SignalClass``, ``tier`` and ``age`` are
``int``s (not ``bool``s), the dates are a ``date`` (not a ``datetime``) or ``None`` and the times
aware ``datetime``s; and ``ValueError`` for a Tier outside 1 to 3 or a negative age.
"""

from datetime import UTC, date, datetime
from typing import Final

from nextron.domain.segments import TIERS, SignalClass

FULL_WEIGHT_DAYS: Final = 30  # a Tier 1 Event keeps its full weight through this day
EVENT_LIFE_DAYS: Final = 90  # a Tier 1 Event is worth 0 from this day; others expire after it
DECAY_DAYS: Final = EVENT_LIFE_DAYS - FULL_WEIGHT_DAYS  # 60: the Tier 1 linear fall


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError(f"{name} must be an aware datetime")
    return value


def _int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def _checked(signal_class: object, tier: object, age: object) -> tuple[SignalClass, int, int]:
    if not isinstance(signal_class, SignalClass):
        raise TypeError("signal_class must be a SignalClass")
    if _int("tier", tier) not in TIERS:
        raise ValueError("tier must be 1, 2 or 3")
    if _int("age", age) < 0:
        raise ValueError("age must not be negative")
    return signal_class, tier, age  # type: ignore[return-value]


def age_days(event_date: date | None, first_seen_at: datetime, now: datetime) -> int:
    """Whole UTC days from the event date, else from first seen, to ``now``; never negative."""
    if event_date is not None and (
        isinstance(event_date, datetime) or not isinstance(event_date, date)
    ):
        raise TypeError("event_date must be a date or None")
    seen = _aware("first_seen_at", first_seen_at)
    today = _aware("now", now).astimezone(UTC).date()
    start = event_date if event_date is not None else seen.astimezone(UTC).date()
    return max(0, (today - start).days)


def weight_factor(signal_class: SignalClass, tier: int, age: int) -> float:
    """The share of its weight a Signal still carries; see the module docstring."""
    signal_class, tier, age = _checked(signal_class, tier, age)
    if signal_class is SignalClass.ATTRIBUTE:
        return 1.0
    if tier == 1:
        if age <= FULL_WEIGHT_DAYS:
            return 1.0
        if age >= EVENT_LIFE_DAYS:
            return 0.0
        return (EVENT_LIFE_DAYS - age) / DECAY_DAYS
    return 1.0 if age <= EVENT_LIFE_DAYS else 0.0


def is_live(signal_class: SignalClass, tier: int, age: int) -> bool:
    """Whether the Signal still counts; see the module docstring."""
    signal_class, tier, age = _checked(signal_class, tier, age)
    if signal_class is SignalClass.ATTRIBUTE:
        return True
    if tier == 1:
        return age < EVENT_LIFE_DAYS
    return age <= EVENT_LIFE_DAYS
