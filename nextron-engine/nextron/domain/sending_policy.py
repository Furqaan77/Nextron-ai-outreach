"""The cold-send volume policy (FR-33, FR-34, FR-19, NFR-22, AD-11, Story 6.4): warm-up, the
ramped Daily Cap and the total daily ceiling. Pure: standard library and ``nextron.domain``
only, no clock (callers pass the local date).

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
- ``capacity_forecast(plan, local_date, *, ceiling, follow_ups_due=0)``: ``max(0, cold_cap -
  follow_ups_due)``, the new Step 1 sends the inbox has room for (Story 4.8).

The engine never runs warm-up exchanges: warm-up is done by hand or by an outside tool, and
this module only records and reads its facts.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final
from zoneinfo import ZoneInfo

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


def capacity_forecast(
    plan: WarmupPlan, local_date: date, *, ceiling: int, follow_ups_due: int = 0
) -> int:
    """The cold cap left after the follow-ups due on ``local_date``; never negative."""
    return max(0, cold_cap(plan, local_date, ceiling) - follow_ups_due)
