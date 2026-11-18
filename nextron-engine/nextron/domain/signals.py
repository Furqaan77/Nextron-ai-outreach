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

**The strongest Event (Story 2.10)**, which steers the Primary Contact's choice:

- ``EventSignal(signal_id, signal_type, tier, weight, event_date, first_seen_at)``: a current
  Event of the Lead, with its type's ``weight`` from the Segment.
- ``strongest_event(events, now) -> EventSignal | None``: among the live Events
  (``is_live``), the one with the highest ``weight * weight_factor``. A tie goes to the lower
  Tier, then the younger (the smaller ``age_days``), then the earlier ``first_seen_at``, then
  the smaller ``signal_id``. No live Event gives ``None``.
- ``preferred_roles(signal_type, quotes) -> tuple[str, ...]``: the roles of each of the type's
  ``preferred_roles`` entries one of whose ``when_terms`` appears in one of ``quotes`` (the
  quotes of the Event's live Evidence) as a whole phrase: both normalised
  (``quotes.normalise``) and casefolded, with no letter or digit just before or after it. The
  roles keep the entries' order, each once (case-insensitive); no entry gives ``()``.

``has_phrase(text, phrase)`` is that whole-phrase test: ``phrase`` is in ``text`` with no letter
or digit just before or after it (an empty phrase is never in a text).

**Hiring matches (Story 2.11)**, which turn a job posting into an Event:

- ``HiringMatch(signal_type, in_title, term)``: the hiring Signal type a posting matched, whether
  the term was found in its title, and the term (as the Segment writes it).
- ``match_hiring(title, description, segment) -> HiringMatch | None``: the candidates are the
  Segment's Signal types whose ``sources`` include ``careers_page`` or ``ats_board``
  (``segments.HIRING_SOURCES``), in the Segment's order. Each type's ``match_terms`` are tried in
  order; a term matches as a whole phrase (``has_phrase``), case-insensitively, on the
  ``quotes.normalise``d and casefolded text. The title is searched for every type first, then
  the description (``None`` or empty: not searched). No match gives ``None``. ``TypeError``
  unless the title is a ``str``, the description a ``str`` or ``None`` and the Segment a
  ``SegmentDef``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final

from nextron.domain.quotes import normalise
from nextron.domain.segments import (
    HIRING_SOURCES,
    TIERS,
    SegmentDef,
    SignalClass,
    SignalType,
)

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


# The strongest Event (Story 2.10) --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventSignal:
    """A current Event of a Lead, with its type's weight."""

    signal_id: str
    signal_type: str
    tier: int
    weight: int
    event_date: date | None
    first_seen_at: datetime


def strongest_event(events: Iterable[EventSignal], now: datetime) -> EventSignal | None:
    """The live Event with the highest weight times its factor, or ``None``; see the module
    docstring."""
    best: EventSignal | None = None
    best_key: tuple[float, int, int, datetime, str] | None = None
    for event in events:
        if not isinstance(event, EventSignal):
            raise TypeError("strongest_event takes EventSignals")
        age = age_days(event.event_date, event.first_seen_at, now)
        if not is_live(SignalClass.EVENT, event.tier, age):
            continue
        score = event.weight * weight_factor(SignalClass.EVENT, event.tier, age)
        key = (-score, event.tier, age, event.first_seen_at, event.signal_id)
        if best_key is None or key < best_key:
            best, best_key = event, key
    return best


def has_phrase(text: str, phrase: str) -> bool:
    """Whether ``phrase`` is in ``text`` with no letter or digit just before or after it."""
    if not phrase:
        return False
    start = text.find(phrase)
    while start != -1:
        end = start + len(phrase)
        before = text[start - 1] if start > 0 else " "
        after = text[end] if end < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = text.find(phrase, start + 1)
    return False


def preferred_roles(signal_type: SignalType, quotes: Iterable[str]) -> tuple[str, ...]:
    """The roles the type prefers given its Event's quotes; see the module docstring."""
    if not isinstance(signal_type, SignalType):
        raise TypeError("preferred_roles takes a SignalType")
    if isinstance(quotes, str):
        raise TypeError("quotes must be an iterable of str, not a str")
    texts = [normalise(quote).casefold() for quote in quotes]
    roles: list[str] = []
    seen: set[str] = set()
    for preferred in signal_type.preferred_roles:
        terms = [normalise(term).casefold() for term in preferred.when_terms]
        if not any(has_phrase(text, term) for term in terms for text in texts):
            continue
        for role in preferred.roles:
            if role.casefold() not in seen:
                seen.add(role.casefold())
                roles.append(role)
    return tuple(roles)


# Hiring matches (Story 2.11) -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HiringMatch:
    """The hiring Signal type a posting matched, where, and the term matched."""

    signal_type: SignalType
    in_title: bool
    term: str


def _first_term(text: str, candidates: Iterable[SignalType]) -> tuple[SignalType, str] | None:
    folded = normalise(text).casefold()
    for signal_type in candidates:
        for term in signal_type.match_terms:
            if has_phrase(folded, normalise(term).casefold()):
                return signal_type, term
    return None


def match_hiring(
    title: str, description: str | None, segment: SegmentDef
) -> HiringMatch | None:
    """The hiring Signal type a posting matches, or ``None``; see the module docstring."""
    if not isinstance(title, str):
        raise TypeError("title must be a str")
    if description is not None and not isinstance(description, str):
        raise TypeError("description must be a str or None")
    if not isinstance(segment, SegmentDef):
        raise TypeError("segment must be a SegmentDef")
    candidates = [
        signal_type
        for signal_type in segment.signal_types
        if HIRING_SOURCES & set(signal_type.sources)
    ]
    found = _first_term(title, candidates)
    if found is not None:
        return HiringMatch(found[0], True, found[1])
    if description:
        found = _first_term(description, candidates)
        if found is not None:
            return HiringMatch(found[0], False, found[1])
    return None
