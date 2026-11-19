"""Time, caps and pacing, computed in one place (AD-11, AD-21, FR-19, FR-31, FR-33, FR-34,
NFR-22). Standard library only, no clock (callers pass the local
date or ``now``). Story 4.8 adds the two rules Enrollment needs and Story 4.9 the Step due
time; Story 6.4 the cold-send volume policy (warm-up, the ramped Daily Cap and the total daily
ceiling); Story 6.9 the Send Window and the approval lifetime; Story 6.11 the gap draw, the
restart rule and the per-tick dispatch pick.

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

**Story 6.9: the Send Window and the approval lifetime** (FR-19, FR-31, AD-11).

- ``SEND_WINDOW``: ``Window(08:00, 17:00)``, weekdays only, in the recipient's local time;
  ``start`` is inside the window and ``end`` outside it.
- ``in_send_window(contact_tz, market, now) -> bool``: whether ``now`` (an aware ``datetime``;
  a naive one, or another type, raises ``ValueError``) falls in the recipient's Send Window.
  ``market`` is a ``markets.MarketDef`` (read through its ``unknown_timezone`` and
  ``unknown_send_window``; this module can't import ``markets``, which imports the settings
  schema, which imports this module).
  ``contact_tz`` is the Contact's IANA zone, or ``None`` when it is unknown: then the zone is
  ``market.unknown_timezone`` and the window ``market.unknown_send_window`` (the US: 11:00 to
  17:00 ``America/New_York``), or ``SEND_WINDOW`` when the Market doesn't narrow it. A Saturday
  or Sunday in that zone is outside; a zone that ``zoneinfo`` doesn't know is outside too
  (fail closed).
- ``APPROVAL_TTL`` is 72 hours; ``approval_expired(approved_at, now)`` is true once ``now -
  approved_at`` reaches it (both aware; otherwise ``ValueError``).

**Story 6.11: cold-send pacing and the dispatch pick** (FR-31, AD-11, NFR-22).

- ``GAP_MIN`` and ``GAP_MAX`` are 4 and 12 minutes. ``GapSource`` is anything with
  ``uniform(a, b)`` (production passes ``random.SystemRandom()``; tests a seeded
  ``random.Random`` or a stub). ``draw_gap(source) -> timedelta`` asks it for
  ``uniform(4.0, 12.0)`` minutes, the only place a gap is drawn; an answer outside 4 to 12 (or
  not a finite number) raises ``ValueError``.
- ``next_send_after(sent_at, source)``: ``sent_at`` (aware) plus a fresh draw, the inbox's
  ``next_send_at`` after a cold send.
- ``repaced_next_send_at(last_sent_at, started_at, source)``: after a restart, the later of
  ``last_sent_at`` (``None`` when the inbox never sent) and ``started_at`` (process start), plus
  a fresh draw, so the first cold send after a wake is at least one gap after the last one and
  after the start.
- ``cold_paced(next_send_at, process_started_at, now) -> bool``: true only when
  ``next_send_at`` is set, later than ``process_started_at`` and not later than ``now``. A value
  left from before the restart (or ``None``) never releases a cold send, so a failed re-pace
  fails closed.
- ``POSTING_RECHECK_AFTER`` is 24 hours.
- ``COLD_KIND_NAMES`` (``step``, ``recontact``) and ``RESPONSIVE_KIND_NAMES`` (``reply``,
  ``rebook``) equal ``send_gate.COLD_KINDS`` and ``RESPONSIVE_KINDS`` (a unit test pins it);
  ``send_gate`` imports this module, so this module can't import it back.
- ``DispatchCandidate(om_id, kind, step, approved_at, due_at, contact_tz, market, signal_id,
  posting_checked_at)``: what the pick needs of one queued OM (Story 6.10 builds it). ``step``
  is the Step number of a ``step`` OM (``None`` otherwise); a follow-up is a ``step`` OM past
  Step 1. ``cites_posting`` is ``signal_id is not None``. A kind outside the four raises
  ``ValueError``, as does a ``step`` OM without a step of at least 1 or another kind with a
  step; a naive time ``ValueError``.
- ``InboxDispatchState(inbox_id, recovery_mode, process_started_at, last_sync_completed_at,
  next_send_at, cold_sent_today, cold_cap)``: one inbox's dispatch facts; ``None`` is unknown.
- ``pick_dispatch(state, candidates, now) -> DispatchPlan(om_id, sync_mailbox,
  recheck_signal_ids)``: at most one OM for the inbox this tick. Nothing at all while
  ``recovery_mode`` is on or until a sync of the inbox completed at or after process start.
  Then:

  1. responsive OMs, oldest ``approved_at`` first (then id): one goes only when a sync completed
     after its ``approved_at`` (otherwise it is skipped and ``sync_mailbox`` is asked) and, when
     it has a ``due_at``, that is not later than ``now``. They skip the gap, the Window and the
     Cap.
  2. cold OMs, only while ``cold_paced`` and ``cold_sent_today`` is known and under
     ``cold_cap``: follow-ups (highest Step, then oldest ``due_at``), then Step 1 and
     ``recontact`` (oldest ``due_at``), ties by id. One goes when its ``due_at`` is set and not
     later than ``now`` and ``now`` is inside its recipient's Send Window. A Step 1 or
     ``recontact`` citing a posting checked more than 24 hours before ``now``, never, or after
     ``now`` is skipped and its ``signal_id`` asked to be rechecked.

  A candidate that can't go is skipped, so it never blocks the others. The pick never replaces
  the send gate: Story 6.10 still runs ``assert_sendable``.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Protocol
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
APPROVAL_TTL: Final = timedelta(hours=72)


@dataclass(frozen=True, slots=True)
class Window:
    """Send hours in the recipient's local time: from ``start`` (inside) to ``end`` (outside)."""

    start: time
    end: time


SEND_WINDOW: Final = Window(time(8, 0), time(17, 0))


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


# The Send Window and the approval lifetime (Story 6.9) ------------------------------------------


def _aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value


class _Hours(Protocol):
    @property
    def start(self) -> time: ...

    @property
    def end(self) -> time: ...


class WindowMarket(Protocol):
    """What ``in_send_window`` reads of a ``markets.MarketDef``."""

    @property
    def unknown_timezone(self) -> str: ...

    @property
    def unknown_send_window(self) -> _Hours | None: ...


def in_send_window(contact_tz: str | None, market: WindowMarket, now: datetime) -> bool:
    """Whether ``now`` is inside the recipient's Send Window; see the module docstring."""
    moment = _aware(now, "now")
    window: _Hours
    if contact_tz is None:
        zone_name = market.unknown_timezone
        narrowed = market.unknown_send_window
        window = SEND_WINDOW if narrowed is None else narrowed
    else:
        zone_name = contact_tz
        window = SEND_WINDOW
    try:
        zone = _zone(zone_name)
    except (TypeError, ValueError):
        return False  # an unreadable zone is outside every window: fail closed
    local = moment.astimezone(zone)
    if local.weekday() >= SATURDAY:
        return False
    return window.start <= local.time().replace(tzinfo=None) < window.end


def approval_expired(approved_at: datetime, now: datetime) -> bool:
    """Whether an approval given at ``approved_at`` has lapsed at ``now`` (72 hours on)."""
    return _aware(now, "now") - _aware(approved_at, "approved_at") >= APPROVAL_TTL


# Cold-send pacing and the dispatch pick (Story 6.11) --------------------------------------------

GAP_MIN: Final = timedelta(minutes=4)
GAP_MAX: Final = timedelta(minutes=12)
POSTING_RECHECK_AFTER: Final = timedelta(hours=24)
_GAP_MIN_MINUTES: Final = 4.0
_GAP_MAX_MINUTES: Final = 12.0

COLD_KIND_NAMES: Final = frozenset({"step", "recontact"})
RESPONSIVE_KIND_NAMES: Final = frozenset({"reply", "rebook"})
STEP_KIND: Final = "step"
FIRST_STEP: Final = 1


class GapSource(Protocol):
    """A random source: ``random.SystemRandom()`` in production, a seeded one in tests."""

    def uniform(self, a: float, b: float) -> float: ...


def draw_gap(source: GapSource) -> timedelta:
    """One cold-send gap, drawn uniformly from 4 to 12 minutes through ``source``."""
    minutes = source.uniform(_GAP_MIN_MINUTES, _GAP_MAX_MINUTES)
    if isinstance(minutes, bool) or not isinstance(minutes, int | float):
        raise ValueError("the gap source must answer a number of minutes")
    if not math.isfinite(minutes) or not _GAP_MIN_MINUTES <= minutes <= _GAP_MAX_MINUTES:
        raise ValueError("a cold-send gap must be 4 to 12 minutes")
    return timedelta(minutes=minutes)


def next_send_after(sent_at: datetime, source: GapSource) -> datetime:
    """The inbox's ``next_send_at`` after a cold send at ``sent_at``: one fresh gap later."""
    return _aware(sent_at, "sent_at") + draw_gap(source)


def repaced_next_send_at(
    last_sent_at: datetime | None, started_at: datetime, source: GapSource
) -> datetime:
    """The inbox's ``next_send_at`` after a restart: the later of ``last_sent_at`` and
    ``started_at``, plus a fresh gap."""
    start = _aware(started_at, "started_at")
    if last_sent_at is None:
        return start + draw_gap(source)
    return max(_aware(last_sent_at, "last_sent_at"), start) + draw_gap(source)


def cold_paced(next_send_at: datetime | None, process_started_at: datetime, now: datetime) -> bool:
    """Whether the gap lets a cold send go at ``now``: ``next_send_at`` is set, later than
    process start and not later than ``now``."""
    moment = _aware(now, "now")
    started = _aware(process_started_at, "process_started_at")
    if next_send_at is None:
        return False
    return started < _aware(next_send_at, "next_send_at") <= moment


@dataclass(frozen=True, slots=True)
class DispatchCandidate:
    """One queued OM as the dispatch pick weighs it; see the module docstring."""

    om_id: str
    kind: str
    step: int | None
    approved_at: datetime
    due_at: datetime | None
    contact_tz: str | None
    market: WindowMarket
    signal_id: str | None = None
    posting_checked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.om_id, str):
            raise TypeError("DispatchCandidate.om_id must be a str")
        if self.kind not in COLD_KIND_NAMES | RESPONSIVE_KIND_NAMES:
            raise ValueError("DispatchCandidate.kind must be step, recontact, reply or rebook")
        if self.kind == STEP_KIND:
            if self.step is None or _count(self.step, "DispatchCandidate.step") < FIRST_STEP:
                raise ValueError("a step DispatchCandidate needs a step of at least 1")
        elif self.step is not None:
            raise ValueError(f"a {self.kind} DispatchCandidate has no step")
        _aware(self.approved_at, "approved_at")
        if self.due_at is not None:
            _aware(self.due_at, "due_at")
        if self.posting_checked_at is not None:
            _aware(self.posting_checked_at, "posting_checked_at")

    @property
    def cites_posting(self) -> bool:
        return self.signal_id is not None

    @property
    def responsive(self) -> bool:
        return self.kind in RESPONSIVE_KIND_NAMES

    @property
    def follow_up(self) -> bool:
        """A ``step`` OM past Step 1."""
        return self.kind == STEP_KIND and self.step is not None and self.step > FIRST_STEP

    @property
    def first_touch(self) -> bool:
        """A cold OM that isn't a follow-up: a Step 1 or a ``recontact``."""
        return self.kind in COLD_KIND_NAMES and not self.follow_up


@dataclass(frozen=True, slots=True)
class InboxDispatchState:
    """One inbox's dispatch facts; ``None`` is unknown and fails closed."""

    inbox_id: str
    recovery_mode: bool
    process_started_at: datetime
    last_sync_completed_at: datetime | None
    next_send_at: datetime | None
    cold_sent_today: int | None
    cold_cap: int

    def __post_init__(self) -> None:
        _aware(self.process_started_at, "process_started_at")
        if self.last_sync_completed_at is not None:
            _aware(self.last_sync_completed_at, "last_sync_completed_at")
        if self.next_send_at is not None:
            _aware(self.next_send_at, "next_send_at")
        if self.cold_sent_today is not None:
            _count(self.cold_sent_today, "cold_sent_today")
        _count(self.cold_cap, "cold_cap")


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    """One inbox's pick this tick: the OM to send (or ``None``), whether a mailbox sync is
    needed first, and the Signals whose posting needs a recheck."""

    om_id: str | None
    sync_mailbox: bool = False
    recheck_signal_ids: tuple[str, ...] = ()


NOTHING: Final = DispatchPlan(None)


def _posting_stale(candidate: DispatchCandidate, now: datetime) -> bool:
    checked = candidate.posting_checked_at
    return checked is None or checked > now or now - checked > POSTING_RECHECK_AFTER


def _cold_order(candidate: DispatchCandidate) -> tuple[int, int, datetime, str]:
    due = candidate.due_at
    assert due is not None  # only due candidates are sorted
    if candidate.follow_up:
        return (0, -(candidate.step or 0), due, candidate.om_id)
    return (1, 0, due, candidate.om_id)


def _cold_open(state: InboxDispatchState, now: datetime) -> bool:
    if not cold_paced(state.next_send_at, state.process_started_at, now):
        return False
    return state.cold_sent_today is not None and state.cold_sent_today < state.cold_cap


def pick_dispatch(
    state: InboxDispatchState, candidates: Iterable[DispatchCandidate], now: datetime
) -> DispatchPlan:
    """At most one OM for the inbox this tick; see the module docstring."""
    moment = _aware(now, "now")
    if not isinstance(state, InboxDispatchState):
        raise TypeError("pick_dispatch takes an InboxDispatchState")
    found = list(candidates)
    if not all(isinstance(candidate, DispatchCandidate) for candidate in found):
        raise TypeError("pick_dispatch takes DispatchCandidate values")
    synced = state.last_sync_completed_at
    if state.recovery_mode or synced is None or synced < state.process_started_at:
        return NOTHING
    sync_needed = False
    responsive = sorted(
        (candidate for candidate in found if candidate.responsive),
        key=lambda candidate: (candidate.approved_at, candidate.om_id),
    )
    for candidate in responsive:
        if candidate.due_at is not None and candidate.due_at > moment:
            continue
        if synced <= candidate.approved_at:
            sync_needed = True
            continue
        return DispatchPlan(candidate.om_id, sync_needed)
    rechecks: list[str] = []
    if _cold_open(state, moment):
        due = (
            candidate
            for candidate in found
            if not candidate.responsive
            and candidate.due_at is not None
            and candidate.due_at <= moment
        )
        for candidate in sorted(due, key=_cold_order):
            if not in_send_window(candidate.contact_tz, candidate.market, moment):
                continue
            if (
                candidate.first_touch
                and candidate.signal_id is not None
                and _posting_stale(candidate, moment)
            ):
                if candidate.signal_id not in rechecks:
                    rechecks.append(candidate.signal_id)
                continue
            return DispatchPlan(candidate.om_id, sync_needed, tuple(rechecks))
    return DispatchPlan(None, sync_needed, tuple(rechecks))
