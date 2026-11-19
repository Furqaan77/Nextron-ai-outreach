"""The rules of the weekday discovery top-up (Story 8.10; FR-5, FR-6, FR-9, AD-9, AD-10).
Standard library and ``nextron.domain`` only.

**Days.** A local date is a weekday (``is_weekday``) from Monday to Friday.
``weekdays_left(local_date)`` counts the weekdays from ``local_date`` to the last day of its
calendar month, ``local_date`` included (0 when ``local_date`` is the month's last day and a
weekend day). ``local_date_of(now, timezone)`` is the date at ``now`` in the IANA zone
``timezone`` and ``day_start_utc(local_date, timezone)`` the UTC instant that local date starts
(the Operator's ``sender_timezone``).

**Places allowance.** ``places_day_allowance(quota, used_before_today, weekdays_left)`` is
``max(0, quota - used_before_today) // weekdays_left`` (0 when ``weekdays_left`` is 0): the
Places requests a day of scheduled discovery may use, so the free monthly quota lasts the
month. Every argument is an ``int`` of at least 0 (a ``bool`` isn't one), else ``ValueError``.

**Pool.** ``pool_sufficient(ready, pool_days, share)``: ``ready >= pool_days * share``; a
Campaign whose ready pool already covers ``pool_days`` days of its capacity share gets no
discovery that day.

**Rotation.** ``next_pair(pairs, last_runs, now, requery_days)`` picks the pair a scheduled run
searches: ``pairs`` are keys in their own order (seed × setting), ``last_runs`` maps a key to
its last finished run's start. A pair whose last run is less than ``requery_days`` × 24 h before
``now`` is skipped. Of the rest, the first pair never run, else the one whose last run is oldest
(the first in pair order on a tie); ``None`` when no pair is due.

**The scheduled payload.** A scheduled ``discover_places`` or ``discover_registry`` job carries
``{v: 1, campaign_id, scheduled_for: "<YYYY-MM-DD>"}`` (``SCHEDULED_FOR_FIELD``), keyed
``<type>:<campaign ULID>:<YYYY-MM-DD>``.

**Selection statuses.** ``TOP_UP_STATUSES`` are the ``daily_selection`` outcomes after which the
top-up runs: ``ran`` and ``away`` (Story 8.9's ``weekend`` and ``no_capacity`` never top up).
"""

import calendar
from collections.abc import Hashable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

SCHEDULED_FOR_FIELD: Final = "scheduled_for"
SELECTION_RAN: Final = "ran"
SELECTION_AWAY: Final = "away"
TOP_UP_STATUSES: Final = frozenset({SELECTION_RAN, SELECTION_AWAY})
FRIDAY: Final = 4  # date.weekday()
HOURS_PER_DAY: Final = 24

# The settings' bounds and defaults (Budget and limits)
MIN_POOL_DAYS: Final = 1
MAX_POOL_DAYS: Final = 60
DEFAULT_POOL_DAYS: Final = 5
MIN_REQUERY_DAYS: Final = 1
MAX_REQUERY_DAYS: Final = 365
DEFAULT_REQUERY_DAYS: Final = 30
MAX_DAILY_LEAD_TARGET: Final = 1000
DEFAULT_DAILY_LEAD_TARGET: Final = 60


def _count(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an int of at least 0")
    return value


def _date(value: object) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("a local date must be a datetime.date")
    return value


def is_weekday(local_date: date) -> bool:
    """Whether ``local_date`` is Monday to Friday."""
    return _date(local_date).weekday() <= FRIDAY


def weekdays_left(local_date: date) -> int:
    """The weekdays from ``local_date`` to the end of its month, ``local_date`` included."""
    day = _date(local_date)
    last = calendar.monthrange(day.year, day.month)[1]
    return sum(
        1
        for number in range(day.day, last + 1)
        if date(day.year, day.month, number).weekday() <= FRIDAY
    )


def local_date_of(now: datetime, timezone: str) -> date:
    """The date at ``now`` (aware) in ``timezone``."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(ZoneInfo(timezone)).date()


def day_start_utc(local_date: date, timezone: str) -> datetime:
    """The UTC instant ``local_date`` starts in ``timezone``."""
    start = datetime.combine(_date(local_date), time(0), tzinfo=ZoneInfo(timezone))
    return start.astimezone(UTC)


def places_day_allowance(quota: int, used_before_today: int, weekdays_left: int) -> int:
    """Today's share of what is left of the month's Places quota; see the module docstring."""
    quota = _count("quota", quota)
    used = _count("used_before_today", used_before_today)
    days = _count("weekdays_left", weekdays_left)
    if days == 0:
        return 0
    return max(0, quota - used) // days


def pool_sufficient(ready: int, pool_days: int, share: int) -> bool:
    """Whether ``ready`` Leads cover ``pool_days`` days of a ``share`` a day."""
    return _count("ready", ready) >= _count("pool_days", pool_days) * _count("share", share)


def next_pair[K: Hashable](
    pairs: Sequence[K],
    last_runs: Mapping[K, datetime],
    now: datetime,
    requery_days: int,
) -> K | None:
    """The pair the next scheduled run searches, or ``None``; see the module docstring."""
    window = timedelta(hours=HOURS_PER_DAY * _count("requery_days", requery_days))
    oldest: tuple[datetime, K] | None = None
    for pair in pairs:
        last = last_runs.get(pair)
        if last is None:
            return pair
        if now - last < window:
            continue
        if oldest is None or last < oldest[0]:
            oldest = (last, pair)
    return None if oldest is None else oldest[1]
