"""Time, caps and pacing, computed in one place (AD-11, AD-21, FR-19, FR-31). Standard library
only. Story 4.8 adds the two rules Enrollment needs; later stories add the Send Window, the
Daily Cap ramp, the total ceiling, the gap draw, ``approval_expired`` and ``step_due_at`` here.

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
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


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
