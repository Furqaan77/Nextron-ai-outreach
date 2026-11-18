"""Time, caps and pacing, computed in one place (AD-11, AD-21, FR-19, FR-31). Standard library
only. Story 4.8 adds the two rules Enrollment needs and Story 4.9 the Step due time; later
stories add the Send Window, the Daily Cap ramp, the total ceiling, the gap draw and
``approval_expired`` here.

- ``capacity_forecast(cold_daily_cap, follow_ups_due) -> int``: how many new Step 1s an inbox
  can take on a day: its cold Daily Cap for that day less the follow-ups already due then
  (follow-ups are reserved first), never below 0. Both arguments are whole numbers of at least
  0 (``bool`` refused): another type raises ``TypeError``, a negative number ``ValueError``.
- ``InboxLoad(inbox_id, capacity, live, registered_at)``: one bindable inbox as ``pick_inbox``
  weighs it: its forecast ``capacity``, its number of ``live`` (``active`` or ``paused``)
  Enrollments and when it was registered (an aware ``datetime``).
- ``pick_inbox(loads) -> str | None``: the inbox a new Enrollment binds to: the most
  ``capacity``, then the fewest ``live``, then the earliest ``registered_at``, then the
  smallest id; ``None`` with no load. Capacity never refuses an Enrollment here: Story 4.10's
  selection limits how many it picks.
- ``step_due_at(prev_sent_at, delay_days, recipient_tz, not_before=None) -> datetime`` (Story
  4.9, AD-21): the single definition of when a Step is due, as an aware UTC ``datetime``. It
  keeps the local wall time of ``prev_sent_at`` (aware) in ``recipient_tz`` (an IANA zone name,
  read with ``zoneinfo``) and moves the local date on by ``delay_days`` (a whole number of at
  least 0); when ``not_before`` (a ``date``, such as an Out-of-Office return date) is later,
  that date at the same local time; then a Saturday or Sunday becomes the Monday after, same
  local time. A local time that doesn't exist that day (the spring-forward gap) resolves
  forward, by the gap's length; an ambiguous one (the autumn fold) takes its first occurrence
  (``fold=0``). A bad type (a naive or non-``datetime`` ``prev_sent_at``, a non-``int`` or
  ``bool`` delay, a non-``str`` zone, a ``not_before`` that isn't a plain ``date``) raises
  ``TypeError``; a negative delay or an unknown zone ``ValueError``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SATURDAY: Final = 5  # date.weekday()


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be at least 0")
    return value


def capacity_forecast(cold_daily_cap: int, follow_ups_due: int) -> int:
    """New Step 1s an inbox can take on a day: the cap less the follow-ups due, at least 0."""
    cap = _count(cold_daily_cap, "cold_daily_cap")
    due = _count(follow_ups_due, "follow_ups_due")
    return max(0, cap - due)


@dataclass(frozen=True, slots=True)
class InboxLoad:
    """One bindable inbox: its forecast capacity, live Enrollments and registration time."""

    inbox_id: str
    capacity: int
    live: int
    registered_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.inbox_id, str):
            raise TypeError("InboxLoad.inbox_id must be a str")
        _count(self.capacity, "InboxLoad.capacity")
        _count(self.live, "InboxLoad.live")
        if not isinstance(self.registered_at, datetime) or self.registered_at.utcoffset() is None:
            raise TypeError("InboxLoad.registered_at must be an aware datetime")


def pick_inbox(loads: Iterable[InboxLoad]) -> str | None:
    """The inbox a new Enrollment binds to, or ``None``; see the module docstring."""
    found = list(loads)
    if not all(isinstance(load, InboxLoad) for load in found):
        raise TypeError("pick_inbox takes InboxLoad values")
    if not found:
        return None
    best = min(
        found, key=lambda load: (-load.capacity, load.live, load.registered_at, load.inbox_id)
    )
    return best.inbox_id


def _zone(recipient_tz: object) -> ZoneInfo:
    if not isinstance(recipient_tz, str):
        raise TypeError(f"recipient_tz must be a str, not {type(recipient_tz).__name__}")
    try:
        return ZoneInfo(recipient_tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("recipient_tz is not a known IANA time zone") from None


def step_due_at(
    prev_sent_at: datetime,
    delay_days: int,
    recipient_tz: str,
    not_before: date | None = None,
) -> datetime:
    """When a Step is due, in UTC; see the module docstring."""
    if not isinstance(prev_sent_at, datetime) or prev_sent_at.utcoffset() is None:
        raise TypeError("prev_sent_at must be an aware datetime")
    delay = _count(delay_days, "delay_days")
    zone = _zone(recipient_tz)
    if not_before is not None and (
        isinstance(not_before, datetime) or not isinstance(not_before, date)
    ):
        raise TypeError("not_before must be a date or None")
    local = prev_sent_at.astimezone(zone)
    day = local.date() + timedelta(days=delay)
    if not_before is not None and not_before > day:
        day = not_before
    if day.weekday() >= SATURDAY:
        day += timedelta(days=7 - day.weekday())
    wall = datetime.combine(day, local.time().replace(tzinfo=None, fold=0))
    # fold=0: an ambiguous time is its first occurrence; a time in the spring-forward gap
    # takes the offset before the change, so it lands that many minutes later (forward)
    return wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
