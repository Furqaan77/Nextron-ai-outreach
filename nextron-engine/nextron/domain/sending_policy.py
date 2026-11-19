"""Time, caps and pacing, computed in one place (AD-11, AD-21, FR-19, FR-31, FR-33, FR-34,
NFR-22). Standard library only, no clock (callers pass the local date). Story 4.8 adds the two
rules Enrollment needs and Story 4.9 the Step due time; Story 6.4 the cold-send volume policy
(warm-up, the ramped Daily Cap and the total daily ceiling); later stories add the Send Window,
the gap draw and ``approval_expired`` here.

**Stories 4.8 and 4.9.**

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

**Story 4.10.** ``new_sequence_capacity(total_cold, follow_ups_due, steps) -> int``: the new
Sequences all inboxes can carry today: ``min(capacity_forecast(total_cold, follow_ups_due),
total_cold // steps)``, never negative. The first two arguments are checked as
``capacity_forecast``'s; ``steps`` (the largest stored Sequence's Step count) is an ``int`` of
at least 1 (``bool`` refused): another type raises ``TypeError``, a smaller one ``ValueError``.

**Story 6.4: the cold-send volume policy.**

**Ranges** (shared by the warm-up form, ``Sending.record_warmup`` and the ``sending_inboxes``
CHECKs): ``warm_level`` ``WARM_LEVEL_RANGE`` (0 to 100), ``warmup_tool_daily``
``TOOL_DAILY_RANGE`` (0 to 100), ``ramp_step`` ``RAMP_STEP_RANGE`` (1 to 10, default
``DEFAULT_RAMP_STEP`` 5), ``ramp_target`` ``RAMP_TARGET_RANGE`` (1 to 50, default
``DEFAULT_RAMP_TARGET`` 30); the ``total_daily_ceiling`` setting ``CEILING_RANGE`` (0 to 100,
default ``DEFAULT_CEILING`` 50). ``MIN_WARMUP_DAYS`` is 14 and ``RAMP_PERIOD_DAYS`` 7.

**Days** are calendar days in ``sender_timezone``: ``local_date(now, tz_name)`` is ``now`` (an
aware datetime) seen in that zone.

- ``WarmupPlan(warmup_started_on, warm_level, warmup_tool_daily, ramp_step, ramp_target)``: an
  inbox's stored warm-up record and ramp; ``warmup_started_on`` and ``warm_level`` are ``None``
  while warm-up is unrecorded.
- ``warmup_days(plan, local_date)``: ``(local_date - warmup_started_on).days``, or ``None`` when
  unrecorded.
- ``daily_cap(plan, local_date)``: 0 when unrecorded or under 14 days; otherwise
  ``min(target, min(warm_level, target) + step * ((days - 14) // 7))``: the cap starts at the
  warm level (or the target, if lower) and rises one step a week to the target.
- ``tool_send_figure(plan, other_sent=0)``: ``max(warmup_tool_daily, other_sent)``, the day's
  warm-up tool sends that count toward the ceiling (Story 6.8 passes the synced ``other_sent``
  count).
- ``cold_cap(plan, local_date, ceiling)``: ``max(0, min(daily_cap, ceiling - tool_send_figure))``;
  never negative.
- ``inbox_capacity_forecast(plan, local_date, *, ceiling, follow_ups_due=0)``:
  ``capacity_forecast(cold_cap(plan, local_date, ceiling), follow_ups_due)``, the new Step 1
  sends the inbox has room for (Story 4.8's ``capacity_forecast`` on the ramped cold cap).

The engine never runs warm-up exchanges: warm-up is done by hand or by an outside tool, and
this module only records and reads its facts.

**Story 5.11: away dates (FR-31).** The ``away_periods`` setting holds one period per line,
``YYYY-MM-DD to YYYY-MM-DD`` (start and end, both inclusive), read as calendar days in
``sender_timezone``. While the Operator is away, ``Sequencing.enroll`` refuses origins
``selection`` and ``restart`` (``operator_away``); nothing else pauses.

- ``AwayPeriod(start, end)``: one period of plain ``date`` values; ``end`` before ``start``
  raises ``ValueError``, another type ``TypeError``.
- ``away_period_dates(line) -> tuple[date, date] | None``: the two dates of a line shaped
  ``YYYY-MM-DD to YYYY-MM-DD`` (ASCII digits, real calendar dates, spaces or tabs around
  ``to``, the line stripped), in the order written; otherwise ``None`` (a single date
  included).
- ``parse_away_period(line) -> AwayPeriod | None``: that line as a period, or ``None`` when it
  isn't shaped so or its end is before its start.
- ``is_away(local_date, periods)``: the single test of an away day: ``local_date`` falls in
  some period, ends included.
- ``away_until(local_date, periods) -> date | None``: ``None`` when ``local_date`` isn't away;
  otherwise the last day of the contiguous away run holding it: overlapping periods and
  back-to-back ones (the next starts the day after one ends) merge.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SATURDAY: Final = 5  # date.weekday()

MIN_WARMUP_DAYS: Final = 14
RAMP_PERIOD_DAYS: Final = 7
WARM_LEVEL_RANGE: Final = (0, 100)
TOOL_DAILY_RANGE: Final = (0, 100)
RAMP_STEP_RANGE: Final = (1, 10)
RAMP_TARGET_RANGE: Final = (1, 50)
CEILING_RANGE: Final = (0, 100)
DEFAULT_RAMP_STEP: Final = 5
DEFAULT_RAMP_TARGET: Final = 30
DEFAULT_TOOL_DAILY: Final = 0
DEFAULT_CEILING: Final = 50


@dataclass(frozen=True, slots=True)
class WarmupPlan:
    """An inbox's warm-up record and ramp; unrecorded while ``warmup_started_on`` is ``None``."""

    warmup_started_on: date | None
    warm_level: int | None
    warmup_tool_daily: int = DEFAULT_TOOL_DAILY
    ramp_step: int = DEFAULT_RAMP_STEP
    ramp_target: int = DEFAULT_RAMP_TARGET

    @property
    def recorded(self) -> bool:
        return self.warmup_started_on is not None and self.warm_level is not None


def local_date(now: datetime, tz_name: str) -> date:
    """``now`` (aware) as a calendar date in the zone ``tz_name``."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(ZoneInfo(tz_name)).date()


def warmup_days(plan: WarmupPlan, local_date: date) -> int | None:
    """Days of recorded warm-up on ``local_date``, or ``None`` when unrecorded."""
    if plan.warmup_started_on is None:
        return None
    return (local_date - plan.warmup_started_on).days


def daily_cap(plan: WarmupPlan, local_date: date) -> int:
    """The ramped cold Daily Cap on ``local_date``; 0 when unrecorded or under 14 days."""
    days = warmup_days(plan, local_date)
    if days is None or plan.warm_level is None or days < MIN_WARMUP_DAYS:
        return 0
    target = plan.ramp_target
    weeks = (days - MIN_WARMUP_DAYS) // RAMP_PERIOD_DAYS
    return max(0, min(target, min(plan.warm_level, target) + plan.ramp_step * weeks))


def tool_send_figure(plan: WarmupPlan, other_sent: int = 0) -> int:
    """The day's warm-up tool sends counted toward the ceiling."""
    return max(plan.warmup_tool_daily, other_sent)


def cold_cap(plan: WarmupPlan, local_date: date, ceiling: int, other_sent: int = 0) -> int:
    """The cold sends allowed on ``local_date``: the Daily Cap, limited by what the ceiling
    leaves after the warm-up tool sends; never negative."""
    room = ceiling - tool_send_figure(plan, other_sent)
    return max(0, min(daily_cap(plan, local_date), room))


def inbox_capacity_forecast(
    plan: WarmupPlan, local_date: date, *, ceiling: int, follow_ups_due: int = 0
) -> int:
    """The cold cap left after the follow-ups due on ``local_date``; never negative."""
    return capacity_forecast(cold_cap(plan, local_date, ceiling), follow_ups_due)



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


def new_sequence_capacity(total_cold: int, follow_ups_due: int, steps: int) -> int:
    """New Sequences the inboxes can carry today (Story 4.10, FR-19 S1): the cold capacity left
    after the follow-ups due, limited so each new Sequence's Steps fit a day's total; never
    negative."""
    total = _count(total_cold, "total_cold")
    due = _count(follow_ups_due, "follow_ups_due")
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise TypeError(f"steps must be an int, not {type(steps).__name__}")
    if steps < 1:
        raise ValueError("steps must be at least 1")
    return max(0, min(capacity_forecast(total, due), total // steps))


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


# Away dates (Story 5.11) ----------------------------------------------------------------------

_ISO_DAY: Final = r"([0-9]{4}-[0-9]{2}-[0-9]{2})"
_AWAY_LINE: Final = re.compile(_ISO_DAY + r"[ \t]+to[ \t]+" + _ISO_DAY)


def _plain_date(value: object, name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{name} must be a date")
    return value


@dataclass(frozen=True, slots=True)
class AwayPeriod:
    """Away from ``start`` to ``end``, both inclusive, calendar days in ``sender_timezone``."""

    start: date
    end: date

    def __post_init__(self) -> None:
        _plain_date(self.start, "AwayPeriod.start")
        _plain_date(self.end, "AwayPeriod.end")
        if self.end < self.start:
            raise ValueError("AwayPeriod.end must be on or after its start")


def away_period_dates(line: str) -> tuple[date, date] | None:
    """The start and end dates written on ``line``, or ``None``; see the module docstring."""
    if not isinstance(line, str):
        raise TypeError("away_period_dates takes a str")
    found = _AWAY_LINE.fullmatch(line.strip())
    if found is None:
        return None
    try:
        return date.fromisoformat(found[1]), date.fromisoformat(found[2])
    except ValueError:  # not a real calendar date, such as 2026-02-30
        return None


def parse_away_period(line: str) -> AwayPeriod | None:
    """``line`` as an ``AwayPeriod``, or ``None`` when it is malformed or reversed."""
    dates = away_period_dates(line)
    if dates is None or dates[1] < dates[0]:
        return None
    return AwayPeriod(*dates)


def _periods(periods: Iterable[AwayPeriod]) -> list[AwayPeriod]:
    found = list(periods)
    if not all(isinstance(period, AwayPeriod) for period in found):
        raise TypeError("away periods must be AwayPeriod values")
    return found


def is_away(local_date: date, periods: Iterable[AwayPeriod]) -> bool:
    """Whether ``local_date`` falls in one of ``periods``, ends included."""
    day = _plain_date(local_date, "local_date")
    return any(period.start <= day <= period.end for period in _periods(periods))


def away_until(local_date: date, periods: Iterable[AwayPeriod]) -> date | None:
    """The last day of the contiguous away run holding ``local_date``, or ``None``."""
    day = _plain_date(local_date, "local_date")
    found = sorted(_periods(periods), key=lambda period: period.start)
    if not is_away(day, found):
        return None
    end = day
    for period in found:  # by start, so each period that reaches the run extends it
        if end == date.max:  # nothing can extend it, and end + 1 day would overflow
            break
        if period.start <= end + timedelta(days=1):
            end = max(end, period.end)
    return end
