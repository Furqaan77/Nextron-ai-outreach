"""Fit Score, Signal Score, Lead Score and Lead Tier (FR-17, FR-62, FR-1, AD-7; Story 2.15).
Standard library and ``nextron.domain`` only: every function takes ``now`` and does no I/O.

Only this module computes a Lead's Tier (``tier``); an Event's age comes only from
``nextron.domain.signals.age_days`` and its decay only from ``weight_factor``. The ``scoring``
service (``nextron.app.scoring``) reads the Lead's rows, calls ``score`` or ``retier`` and stores
what they return.

**Inputs.**

- ``ScoringSignal(signal_id, signal_type, signal_class, tier, event_date, first_seen_at,
  evidence_ids, open=True)``: a current (never a retracted) Signal of the Lead as stored, with
  the ids of its live Evidence (sorted). ``open`` is ``False`` for a job-posting Event whose
  posting is closed.
- ``SizeEvidence(evidence_id, clue, number, retrieved_at, counted)``: a live Evidence row that
  reads a size: a size-fact row (``fact`` the clue, ``number`` set; ``counted`` ``False``) or a
  ``count`` row of ``lawyers listed`` (``counted`` ``True``, its clue ``lawyers_firm_wide``).

**A Signal that counts** (Decisions 3 and 4). A Signal counts for nothing when its type isn't
one of the Segment's, when its stored class isn't a ``SignalClass`` or isn't its type's class,
or when its stored Tier isn't 1 to 3. A counted Signal is scored with its type's current
``weight`` and ``tier`` from the Segment. An Event is **live** at ``now`` when
``signals.is_live`` holds at its age, its ``weight_factor`` is above 0 and it is ``open``.

- **Each Signal type counts once.** For an Event type, its live Event with the highest decay
  factor (a tie goes to the younger, then the earlier ``first_seen_at``, then the smaller id);
  for an Attribute type, its earliest current Signal (by ``first_seen_at``, then id).

**Size** (Decision 5). ``size_readings(evidence)`` gives one reading per clue: the size-fact row
with the latest ``retrieved_at``, then the largest id; for ``lawyers_firm_wide`` only, when it
has no fact row, the ``count`` row chosen the same way. The size fit reads the clue of the
Segment's ``size_range.unit``: ``lawyers`` reads ``lawyers_firm_wide``, ``staff`` reads
``staff``, and ``usd_revenue`` is always unknown (no revenue source exists). Inside the range
(``minimum <= n <= maximum``) is ``SIZE_INSIDE_POINTS`` (50), unknown ``SIZE_UNKNOWN_POINTS``
(25), outside ``0``.

**Disqualifiers** (Decision 6). ``disqualifier(segment, readings)`` tries the Segment's
disqualifiers in order; the first match wins: ``fewer_than`` is ``n < threshold``,
``more_than`` ``n > threshold``, on the reading of its ``size_clue``. A clue with no reading
never matches, so an unknown size never disqualifies. A match is ``Disqualified(rule_key,
evidence_ids)``: the rule's key and the reading's Evidence id.

**The scores** (Decisions 1 and 2). Each factor keeps its exact points; ``Factor.points`` is
that value rounded to 2 decimals (what is stored and shown).

- ``fit_score(segment, readings, signals)``: the size factor, then each counted Attribute type
  (``weight`` points), in the Segment's order; the score is ``min(100, floor(sum + 0.5))``.
- ``signal_score(segment, signals, now)``: each counted Event type (``weight ×
  weight_factor`` points), in the Segment's order; the same rounding and cap.
- ``lead_score(fit, signal, fit_weight, signal_weight)``: the weights are whole percents
  summing to 100 (``weights_sum_ok``; otherwise ``ValueError``), and the score is
  ``(fit_weight × fit + signal_weight × signal + 50) // 100``, rounding half up.
- ``tier(events, now) -> Tier``: ``A`` when any live Event has Tier 1, ``B`` when one has
  Tier 2 and none Tier 1, else ``C``. It looks at every live Event given, and uses each one's
  ``tier`` as given (``score`` passes the counted Events with their type's current Tier).
- ``score(segment, signals, size_evidence, fit_weight, signal_weight, now)``: the disqualifier
  first (a ``Disqualified``, with no score), else ``Score(fit, signal, lead, tier, fit_factors,
  signal_factors)``.
- ``retier(fit, fit_factors, signal_factors, signals, fit_weight, signal_weight, now) ->
  Retiered`` (Decision 8): re-evaluates each stored Event factor at ``now`` with its stored
  ``weight`` and ``tier`` against the Signal's current row (``signals``, by id). A factor whose
  Signal is missing (retracted included), isn't a valid Event, is closed or isn't live is
  dropped. The Signal Score, the Lead Score and the Tier are recomputed; the Fit Score and its
  factors stay as given. ``Retiered(score, dropped)``: ``dropped`` counts the factors dropped.

**Stored form.** ``factors_text(fit_factors, signal_factors)`` is canonical JSON (``sort_keys``,
no spaces, ``ensure_ascii=False``): ``{"fit": [...], "signal": [...]}``, each factor
``{"evidence_ids", "key", "kind", "points", "signal_id", "tier", "weight"}``.
``factors_from_text(text)`` is its inverse and raises ``ValueError`` for a text it can't read.
``evidence_text(ids)`` and ``evidence_from_text(text)`` store a Disqualified rule's Evidence ids
the same way (a JSON array).

**"Ready to draft"** (Story 4.10). ``ReadyCandidate(lead_id, tier, lead_score,
newest_event_age)``: ``newest_event_age`` is the smallest ``signals.age_days`` over the Lead's
stored Event factors' Signals that aren't retracted (``None`` without one).
``ready_order(candidates)``: Tier A by newest live Event first (a tie, then a Tier A Lead with
no such Event, after: by the higher Lead Score), then Tier B by Lead Score, then Tier C by Lead
Score; the final tie-break is the Lead id. ``below_minimum(score, minimum)``: ``score <
minimum`` (the ``min_lead_score`` badge; such a Lead can still be picked).

Every function raises ``TypeError`` for an argument of the wrong type.
"""

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from fractions import Fraction
from typing import Any, Final

from nextron.domain.segments import (
    TIERS,
    DisqualifierKind,
    SegmentDef,
    SignalClass,
    SignalType,
    SizeClue,
    SizeUnit,
)
from nextron.domain.signals import age_days, is_live, weight_factor

SCORE_LEAD_JOB: Final = "score_lead"
USE_CURRENT_FIELD: Final = "use_current"  # its payload field: apply the current Segment (11.5)
RECOMPUTE_TIERS_JOB: Final = "recompute_tiers"  # nextron.jobs.triggers.Trigger.RECOMPUTE_TIERS
ACTOR: Final = "system:scoring"
MAX_SCORE: Final = 100
SIZE_INSIDE_POINTS: Final = 50
SIZE_UNKNOWN_POINTS: Final = 25
SIZE_OUTSIDE_POINTS: Final = 0
WEIGHT_TOTAL: Final = 100  # the two Lead Score weights are whole percents summing to this
DEFAULT_WEIGHT: Final = 50
DEFAULT_MIN_LEAD_SCORE: Final = 50  # the min_lead_score setting's default (Story 4.10)
WEIGHTS_SUM_RULE: Final = "weights_sum"
WEIGHTS_SUM_REASON: Final = "the fit and signal weights must add up to 100"
POINTS_DECIMALS: Final = 2
# a decay factor is (90 - age) / 60: limit_denominator recovers it exactly from the float
_FACTOR_DENOMINATOR: Final = 1000
FIT: Final = "fit"
SIGNAL: Final = "signal"

# the clue each size unit reads; None: no source (R-18), always unknown
SIZE_CLUES: Final[Mapping[SizeUnit, SizeClue | None]] = {
    SizeUnit.LAWYERS: SizeClue.LAWYERS_FIRM_WIDE,
    SizeUnit.STAFF: SizeClue.STAFF,
    SizeUnit.USD_REVENUE: None,
}


class Tier(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class FactorKind(StrEnum):
    SIZE_IN_RANGE = "size_in_range"
    SIZE_UNKNOWN = "size_unknown"
    SIZE_OUTSIDE = "size_outside"
    ATTRIBUTE = "attribute"
    EVENT = "event"


SIZE_KINDS: Final = frozenset(
    {FactorKind.SIZE_IN_RANGE, FactorKind.SIZE_UNKNOWN, FactorKind.SIZE_OUTSIDE}
)


@dataclass(frozen=True, slots=True)
class ScoringSignal:
    """A current Signal of the Lead, as stored, with its live Evidence ids."""

    signal_id: str
    signal_type: str
    signal_class: str
    tier: int
    event_date: date | None
    first_seen_at: datetime
    evidence_ids: tuple[str, ...]
    open: bool = True


@dataclass(frozen=True, slots=True)
class SizeEvidence:
    """A live Evidence row reading a size clue."""

    evidence_id: str
    clue: str
    number: int
    retrieved_at: datetime
    counted: bool


@dataclass(frozen=True, slots=True)
class Factor:
    """One contribution to a score: its kind, its key (a Signal type, or the size clue or
    unit), its points (2 decimals), the weight and Tier it was scored with (``None`` for the
    size), its Signal and its Evidence."""

    kind: FactorKind
    key: str
    points: float
    weight: int | None
    tier: int | None
    signal_id: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Disqualified:
    """The Segment disqualifier that matched, and the Evidence of its reading."""

    rule_key: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Score:
    """A Lead's scores, its Tier and the factors behind them."""

    fit: int
    signal: int
    lead: int
    tier: Tier
    fit_factors: tuple[Factor, ...]
    signal_factors: tuple[Factor, ...]


@dataclass(frozen=True, slots=True)
class Retiered:
    """A re-tiered score and how many Event factors were dropped."""

    score: Score
    dropped: int


# Checks ---------------------------------------------------------------------------------------


def _int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError(f"{name} must be an aware datetime")
    return value


def _segment(segment: object) -> SegmentDef:
    if not isinstance(segment, SegmentDef):
        raise TypeError("segment must be a SegmentDef")
    return segment


def _signals(signals: Iterable[ScoringSignal]) -> tuple[ScoringSignal, ...]:
    if isinstance(signals, (str, bytes)):
        raise TypeError("signals must be an iterable of ScoringSignals")
    found = tuple(signals)
    if not all(isinstance(signal, ScoringSignal) for signal in found):
        raise TypeError("signals must be ScoringSignals")
    return found


def _class(signal: ScoringSignal) -> SignalClass | None:
    """The stored class, or ``None`` when the domain refuses it or its Tier."""
    try:
        signal_class = SignalClass(signal.signal_class)
    except (TypeError, ValueError):
        return None
    if isinstance(signal.tier, bool) or signal.tier not in TIERS:
        return None
    return signal_class


def _rounded(points: Fraction) -> float:
    return float(round(points, POINTS_DECIMALS))


def _capped(total: Fraction) -> int:
    return min(MAX_SCORE, math.floor(total + Fraction(1, 2)))


def weights_sum_ok(fit_weight: int, signal_weight: int) -> bool:
    """Whether the two Lead Score weights are whole percents from 0 to 100 summing to 100."""
    fit_weight = _int("fit_weight", fit_weight)
    signal_weight = _int("signal_weight", signal_weight)
    return (
        0 <= fit_weight <= WEIGHT_TOTAL
        and 0 <= signal_weight <= WEIGHT_TOTAL
        and fit_weight + signal_weight == WEIGHT_TOTAL
    )


# Live Events ----------------------------------------------------------------------------------


def _event_factor(signal: ScoringSignal, tier: int, now: datetime) -> Fraction | None:
    """The Event's exact decay factor at ``now`` when it is live (``tier`` its Tier), else
    ``None``."""
    if not signal.open:
        return None
    age = age_days(signal.event_date, signal.first_seen_at, now)
    if not is_live(SignalClass.EVENT, tier, age):
        return None
    factor = Fraction(weight_factor(SignalClass.EVENT, tier, age)).limit_denominator(
        _FACTOR_DENOMINATOR
    )
    return factor if factor > 0 else None


def _counted(
    segment: SegmentDef, signals: Iterable[ScoringSignal], signal_class: SignalClass
) -> list[tuple[ScoringSignal, SignalType]]:
    """The Signals of ``signal_class`` that count, each with its Segment type."""
    types = {each.key: each for each in segment.signal_types}
    found: list[tuple[ScoringSignal, SignalType]] = []
    for signal in signals:
        signal_type = types.get(signal.signal_type)
        stored_class = _class(signal)
        if signal_type is None or stored_class is None:
            continue
        if stored_class is not signal_class or signal_type.signal_class is not signal_class:
            continue
        found.append((signal, signal_type))
    return found


def tier(events: Iterable[ScoringSignal], now: datetime) -> Tier:
    """``A`` for a live Tier 1 Event, ``B`` for a live Tier 2 Event and no Tier 1, else ``C``;
    see the module docstring."""
    now = _aware("now", now)
    best: int | None = None
    for event in _signals(events):
        if _class(event) is not SignalClass.EVENT:
            continue
        if _event_factor(event, event.tier, now) is None:
            continue
        best = event.tier if best is None else min(best, event.tier)
    if best == 1:
        return Tier.A
    if best == 2:  # noqa: PLR2004 - Tier 2
        return Tier.B
    return Tier.C


# Size -----------------------------------------------------------------------------------------


def _reading_key(row: SizeEvidence) -> tuple[datetime, str]:
    return (row.retrieved_at, row.evidence_id)


def size_readings(evidence: Iterable[SizeEvidence]) -> dict[SizeClue, SizeEvidence]:
    """One reading per size clue; see the module docstring."""
    facts: dict[SizeClue, SizeEvidence] = {}
    counts: list[SizeEvidence] = []
    for row in evidence:
        if not isinstance(row, SizeEvidence):
            raise TypeError("size_readings takes SizeEvidence rows")
        _int("number", row.number)
        _aware("retrieved_at", row.retrieved_at)
        try:
            clue = SizeClue(row.clue)
        except ValueError:
            continue
        if row.counted:
            if clue is SizeClue.LAWYERS_FIRM_WIDE:
                counts.append(row)
            continue
        held = facts.get(clue)
        if held is None or _reading_key(row) > _reading_key(held):
            facts[clue] = row
    if SizeClue.LAWYERS_FIRM_WIDE not in facts and counts:
        facts[SizeClue.LAWYERS_FIRM_WIDE] = max(counts, key=_reading_key)
    return facts


def disqualifier(
    segment: SegmentDef, readings: Mapping[SizeClue, SizeEvidence]
) -> Disqualified | None:
    """The first of the Segment's disqualifiers its reading matches, or ``None``."""
    for rule in _segment(segment).disqualifiers:
        reading = readings.get(rule.size_clue)
        if reading is None:
            continue
        if rule.kind is DisqualifierKind.FEWER_THAN:
            matched = reading.number < rule.threshold
        else:
            matched = reading.number > rule.threshold
        if matched:
            return Disqualified(rule.key, (reading.evidence_id,))
    return None


def _size_factor(segment: SegmentDef, readings: Mapping[SizeClue, SizeEvidence]) -> Factor:
    size = segment.size_range
    clue = SIZE_CLUES.get(size.unit)
    reading = None if clue is None else readings.get(clue)
    if clue is None or reading is None:
        points = float(SIZE_UNKNOWN_POINTS)
        return Factor(FactorKind.SIZE_UNKNOWN, str(size.unit), points, None, None, None, ())
    inside = size.minimum <= reading.number <= size.maximum
    return Factor(
        FactorKind.SIZE_IN_RANGE if inside else FactorKind.SIZE_OUTSIDE,
        str(clue),
        float(SIZE_INSIDE_POINTS if inside else SIZE_OUTSIDE_POINTS),
        None,
        None,
        None,
        (reading.evidence_id,),
    )


# Scores ---------------------------------------------------------------------------------------


def _order(segment: SegmentDef) -> dict[str, int]:
    return {each.key: index for index, each in enumerate(segment.signal_types)}


def fit_score(
    segment: SegmentDef,
    readings: Mapping[SizeClue, SizeEvidence],
    signals: Iterable[ScoringSignal],
) -> tuple[int, tuple[Factor, ...]]:
    """The Fit Score and its factors: the size, then each counted Attribute type."""
    segment = _segment(segment)
    size = _size_factor(segment, readings)
    earliest: dict[str, tuple[ScoringSignal, SignalType]] = {}
    for signal, signal_type in _counted(segment, _signals(signals), SignalClass.ATTRIBUTE):
        held = earliest.get(signal_type.key)
        if held is None or (signal.first_seen_at, signal.signal_id) < (
            held[0].first_seen_at,
            held[0].signal_id,
        ):
            earliest[signal_type.key] = (signal, signal_type)
    order = _order(segment)
    factors = [size]
    total = Fraction(size.points)
    for key in sorted(earliest, key=order.__getitem__):
        signal, signal_type = earliest[key]
        total += signal_type.weight
        factors.append(
            Factor(
                FactorKind.ATTRIBUTE,
                key,
                float(signal_type.weight),
                signal_type.weight,
                signal_type.tier,
                signal.signal_id,
                signal.evidence_ids,
            )
        )
    return _capped(total), tuple(factors)


def _live_events(
    segment: SegmentDef, signals: Iterable[ScoringSignal], now: datetime
) -> list[tuple[ScoringSignal, SignalType, Fraction]]:
    """The counted Events that are live, each with its type and exact decay factor."""
    found = []
    for signal, signal_type in _counted(segment, signals, SignalClass.EVENT):
        factor = _event_factor(signal, signal_type.tier, now)
        if factor is not None:
            found.append((signal, signal_type, factor))
    return found


def signal_score(
    segment: SegmentDef, signals: Iterable[ScoringSignal], now: datetime
) -> tuple[int, tuple[Factor, ...]]:
    """The Signal Score and its factors: each counted Event type's strongest live Event."""
    segment = _segment(segment)
    now = _aware("now", now)
    best: dict[str, tuple[ScoringSignal, SignalType, Fraction]] = {}

    def rank(entry: tuple[ScoringSignal, SignalType, Fraction]) -> tuple[Any, ...]:
        signal, _, factor = entry
        age = age_days(signal.event_date, signal.first_seen_at, now)
        return (-factor, age, signal.first_seen_at, signal.signal_id)

    for entry in _live_events(segment, _signals(signals), now):
        key = entry[1].key
        if key not in best or rank(entry) < rank(best[key]):
            best[key] = entry
    order = _order(segment)
    factors = []
    total = Fraction(0)
    for key in sorted(best, key=order.__getitem__):
        signal, signal_type, factor = best[key]
        points = signal_type.weight * factor
        total += points
        factors.append(
            Factor(
                FactorKind.EVENT,
                key,
                _rounded(points),
                signal_type.weight,
                signal_type.tier,
                signal.signal_id,
                signal.evidence_ids,
            )
        )
    return _capped(total), tuple(factors)


def lead_score(fit: int, signal: int, fit_weight: int, signal_weight: int) -> int:
    """``(fit_weight × fit + signal_weight × signal + 50) // 100``; ``ValueError`` unless the
    weights sum to 100."""
    fit = _int("fit", fit)
    signal = _int("signal", signal)
    if not weights_sum_ok(fit_weight, signal_weight):
        raise ValueError(WEIGHTS_SUM_REASON)
    return (fit_weight * fit + signal_weight * signal + WEIGHT_TOTAL // 2) // WEIGHT_TOTAL


def score(
    segment: SegmentDef,
    signals: Iterable[ScoringSignal],
    size_evidence: Iterable[SizeEvidence],
    fit_weight: int,
    signal_weight: int,
    now: datetime,
) -> Score | Disqualified:
    """The Lead's disqualifier, or its scores and Tier; see the module docstring."""
    segment = _segment(segment)
    now = _aware("now", now)
    found = _signals(signals)
    readings = size_readings(size_evidence)
    matched = disqualifier(segment, readings)
    if matched is not None:
        return matched
    fit, fit_factors = fit_score(segment, readings, found)
    signal, signal_factors = signal_score(segment, found, now)
    events = [
        replace(each, tier=signal_type.tier)
        for each, signal_type in _counted(segment, found, SignalClass.EVENT)
    ]
    return Score(
        fit=fit,
        signal=signal,
        lead=lead_score(fit, signal, fit_weight, signal_weight),
        tier=tier(events, now),
        fit_factors=fit_factors,
        signal_factors=signal_factors,
    )


def retier(
    fit: int,
    fit_factors: Sequence[Factor],
    signal_factors: Sequence[Factor],
    signals: Iterable[ScoringSignal],
    fit_weight: int,
    signal_weight: int,
    now: datetime,
) -> Retiered:
    """The stored score re-tiered at ``now``; see the module docstring."""
    fit = _int("fit", fit)
    now = _aware("now", now)
    current = {signal.signal_id: signal for signal in _signals(signals)}
    kept: list[Factor] = []
    events: list[ScoringSignal] = []
    total = Fraction(0)
    dropped = 0
    for factor in signal_factors:
        if not isinstance(factor, Factor):
            raise TypeError("signal_factors must be Factors")
        signal = None if factor.signal_id is None else current.get(factor.signal_id)
        weight, factor_tier = factor.weight, factor.tier
        valid = (
            factor.kind is FactorKind.EVENT
            and signal is not None
            and _class(signal) is SignalClass.EVENT
            and isinstance(weight, int)
            and not isinstance(weight, bool)
            and isinstance(factor_tier, int)
            and not isinstance(factor_tier, bool)
            and factor_tier in TIERS
        )
        decay = None
        if valid and signal is not None and factor_tier is not None:
            decay = _event_factor(signal, factor_tier, now)
        if decay is None or signal is None or weight is None or factor_tier is None:
            dropped += 1
            continue
        points = weight * decay
        total += points
        kept.append(replace(factor, points=_rounded(points)))
        events.append(replace(signal, tier=factor_tier))
    signal_value = _capped(total)
    return Retiered(
        Score(
            fit=fit,
            signal=signal_value,
            lead=lead_score(fit, signal_value, fit_weight, signal_weight),
            tier=tier(events, now),
            fit_factors=tuple(fit_factors),
            signal_factors=tuple(kept),
        ),
        dropped,
    )


# Stored form ----------------------------------------------------------------------------------


def _factor_form(factor: Factor) -> dict[str, object]:
    if not isinstance(factor, Factor):
        raise TypeError("factors must be Factors")
    return {
        "evidence_ids": list(factor.evidence_ids),
        "key": factor.key,
        "kind": str(factor.kind),
        "points": factor.points,
        "signal_id": factor.signal_id,
        "tier": factor.tier,
        "weight": factor.weight,
    }


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def factors_text(fit_factors: Iterable[Factor], signal_factors: Iterable[Factor]) -> str:
    """The canonical JSON of both factor lists."""
    return _dumps(
        {
            FIT: [_factor_form(each) for each in fit_factors],
            SIGNAL: [_factor_form(each) for each in signal_factors],
        }
    )


_FACTOR_FIELDS: Final = frozenset(
    {"evidence_ids", "key", "kind", "points", "signal_id", "tier", "weight"}
)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("a factor's weight and tier are integers or null")
    return value


def _ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(each, str) for each in value):
        raise ValueError("Evidence ids are a list of strings")
    return tuple(value)


def _factor(value: object) -> Factor:
    if not isinstance(value, dict) or set(value) != _FACTOR_FIELDS:
        raise ValueError("a factor has exactly its fields")
    points = value["points"]
    if isinstance(points, bool) or not isinstance(points, (int, float)):
        raise ValueError("a factor's points are a number")
    signal_id = value["signal_id"]
    if signal_id is not None and not isinstance(signal_id, str):
        raise ValueError("a factor's Signal id is a string or null")
    key = value["key"]
    if not isinstance(key, str):
        raise ValueError("a factor's key is a string")
    return Factor(
        kind=FactorKind(value["kind"]),
        key=key,
        points=float(points),
        weight=_optional_int(value["weight"]),
        tier=_optional_int(value["tier"]),
        signal_id=signal_id,
        evidence_ids=_ids(value["evidence_ids"]),
    )


def factors_from_text(text: str) -> tuple[tuple[Factor, ...], tuple[Factor, ...]]:
    """The fit and signal factors of a ``factors_text``; ``ValueError`` for anything else."""
    if not isinstance(text, str):
        raise TypeError("factors_from_text takes a str")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("the factors aren't JSON") from error
    if not isinstance(data, dict) or set(data) != {FIT, SIGNAL}:
        raise ValueError("the factors are exactly fit and signal")
    lists = (data[FIT], data[SIGNAL])
    if not all(isinstance(each, list) for each in lists):
        raise ValueError("each factor list is a list")
    fit = tuple(_factor(each) for each in data[FIT])
    return fit, tuple(_factor(each) for each in data[SIGNAL])


def evidence_text(evidence_ids: Iterable[str]) -> str:
    """The JSON array of a Disqualified rule's Evidence ids."""
    ids = list(evidence_ids)
    if not all(isinstance(each, str) for each in ids):
        raise TypeError("Evidence ids are strs")
    return _dumps(ids)


def evidence_from_text(text: str) -> tuple[str, ...]:
    """The Evidence ids of an ``evidence_text``; ``ValueError`` for anything else."""
    if not isinstance(text, str):
        raise TypeError("evidence_from_text takes a str")
    try:
        return _ids(json.loads(text))
    except json.JSONDecodeError as error:
        raise ValueError("the Evidence ids aren't JSON") from error


# "Ready to draft" (Story 4.10) ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadyCandidate:
    """A Lead the "Ready to draft" list may show: its Tier, its Lead Score and the age in days
    of its newest live Event (``None`` without one)."""

    lead_id: str
    tier: Tier
    lead_score: int
    newest_event_age: int | None


_TIER_RANK: Final[Mapping[Tier, int]] = {Tier.A: 0, Tier.B: 1, Tier.C: 2}


def _ready_key(candidate: ReadyCandidate) -> tuple[Any, ...]:
    if not isinstance(candidate, ReadyCandidate):
        raise TypeError("ready_order takes ReadyCandidate values")
    if not isinstance(candidate.lead_id, str):
        raise TypeError("ReadyCandidate.lead_id must be a str")
    tier_value = Tier(candidate.tier)
    score_value = _int("lead_score", candidate.lead_score)
    age = candidate.newest_event_age
    if tier_value is Tier.A:
        if age is not None:
            _int("newest_event_age", age)
        no_event = age is None
        return (_TIER_RANK[tier_value], no_event, age or 0, -score_value, candidate.lead_id)
    return (_TIER_RANK[tier_value], False, 0, -score_value, candidate.lead_id)


def ready_order(candidates: Iterable[ReadyCandidate]) -> tuple[ReadyCandidate, ...]:
    """The "Ready to draft" order: Tier A by newest live Event first (smallest age; a Lead with
    none after those with one; a tie to the higher Lead Score), then Tier B and then Tier C by
    Lead Score (highest first); the final tie-break is the Lead id."""
    return tuple(sorted(candidates, key=_ready_key))


def below_minimum(score_value: int, minimum: int) -> bool:
    """Whether a Lead Score is below the ``min_lead_score`` setting."""
    return _int("score", score_value) < _int("minimum", minimum)
