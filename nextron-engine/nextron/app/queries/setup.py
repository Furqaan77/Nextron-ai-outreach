"""Read API of the ``setup`` service (AD-3, AD-12).

Import rule: ``nextron.domain`` and ``nextron.db.repos.setup`` only.

- ``get(key)``: the typed value of a declared setting, the only way consumers read one
  (``get(Key.SENDER_TIMEZONE)``). With a row it is the stored text decoded by ``parse`` (which
  raises ``InvalidSettingValue`` when the text no longer parses or passes the rules); without
  one it is the default, and a required setting nobody has set raises ``SettingNotSet``, so no
  consumer runs on a missing value by accident. An undeclared key raises ``UnknownSetting``.
- ``stored_texts()``: every stored ``{key: canonical text}`` (the Settings page shows them).
- ``required_unset()``: the keys of the required settings, in declaration order, that have no
  row or whose stored text no longer parses (``nextron doctor`` lists them).
- ``budget_limits()``: the ``nextron.domain.budget.Limits`` the seven Budget and limits
  settings make: each quota in ``QUOTAS`` (its setting × its ``scale``, so Hunter's credits
  are doubled like its stored units), ``ai_budget_monthly`` and ``ai_budget_alert_pct``. A
  stored value that no longer parses raises ``InvalidSettingValue``, like ``get``.
- ``segments()``: every stored Segment as a ``SeededSegment(id, key, version, definition)``,
  by key; ``segment(key)``: one, or ``None``. ``definition`` is the ``SegmentDef`` decoded by
  ``nextron.domain.segments.segment_from_text``, which raises ``InvalidSegments`` for a stored
  text that no longer validates. Research, scoring and drafting (Stories 2.2 onwards) read the
  Segments only through these two. ``segment_ids()``: every stored Segment's id, a
  ``frozenset`` (``Discovery.upsert_lead`` checks a candidate's Segment against it).
- ``markets()``: every stored Market as a ``SeededMarket(id, code, version, definition)``, by
  code; ``market(code)``: one, or ``None``. ``definition`` is the
  ``nextron.domain.markets.MarketDef`` the ``markets`` repository decodes through
  ``market_from_form``, which raises ``InvalidMarkets`` for a row that no longer validates. A
  Lead's Market is derived from these with ``nextron.domain.markets.market_for_country``, never
  stored on the Lead; Market resolution (3.2), Eligibility (3.6), the footer (4.11), drafting
  (4.15) and the send gate (6.9) read the Markets only through these two.
- ``sequences()``: every stored Sequence as a ``SeededSequence(id, key, version, definition)``,
  by key; ``sequence(key)``: one, or ``None``. ``definition`` is the
  ``nextron.domain.sequences.SequenceDef`` (with its Step briefs, in Step order) the
  ``sequences`` repository decodes through ``sequence_from_form``, which raises
  ``InvalidSequences`` for a stored Sequence that no longer validates. Campaigns (4.2), Draft
  slots and due times (4.9), the grounding rules (4.13) and drafting (4.15, 4.18) read the
  Sequences only through these two.

All read through the current unit of work, read-only ones included.
"""

from dataclasses import dataclass
from typing import Any

from nextron.db.repos.setup import markets as markets_repo
from nextron.db.repos.setup import segments as segments_repo
from nextron.db.repos.setup import sequences as sequences_repo
from nextron.db.repos.setup import settings as settings_repo
from nextron.db.repos.setup.markets import StoredMarket
from nextron.db.repos.setup.segments import StoredSegment
from nextron.db.repos.setup.sequences import StoredSequence
from nextron.domain.budget import QUOTAS, Limits
from nextron.domain.markets import MarketDef
from nextron.domain.segments import SegmentDef, segment_from_text
from nextron.domain.sequences import SequenceDef
from nextron.domain.settings_schema import (
    REQUIRED,
    InvalidSettingValue,
    Key,
    SettingNotSet,
    declared,
    parse,
    setting,
)


def get(key: str) -> Any:
    """The typed value of the declared setting ``key``; see the module docstring."""
    found = setting(key)
    text = settings_repo.stored_text(str(found.key))
    if text is None:
        if found.default is REQUIRED:
            raise SettingNotSet(found.key)
        return found.default
    return parse(found, text)


def stored_texts() -> dict[str, str]:
    """Every stored setting's canonical text, by key."""
    return settings_repo.stored_texts()


def required_unset() -> tuple[str, ...]:
    """The required settings' keys, in declaration order, that have no row or whose stored text
    fails ``parse``."""
    stored = settings_repo.stored_texts()
    unset = []
    for found in declared():
        if not found.required:
            continue
        key = str(found.key)
        text = stored.get(key)
        if text is None:
            unset.append(key)
            continue
        try:
            parse(found, text)
        except InvalidSettingValue:
            unset.append(key)
    return tuple(unset)


def budget_limits() -> Limits:
    """The quotas (in stored units) and the AI budget the settings hold now."""
    return Limits(
        quotas={spec.name: get(spec.setting) * spec.scale for spec in QUOTAS},
        ai_budget_micros=get(Key.AI_BUDGET_MONTHLY),
        ai_alert_pct=get(Key.AI_BUDGET_ALERT_PCT),
    )


@dataclass(frozen=True, slots=True)
class SeededSegment:
    """A stored Segment: its row id, key and version, and its decoded definition."""

    id: str
    key: str
    version: int
    definition: SegmentDef


def _seeded(stored: StoredSegment) -> SeededSegment:
    return SeededSegment(
        id=stored.id,
        key=stored.key,
        version=stored.version,
        definition=segment_from_text(stored.definition),
    )


def segments() -> tuple[SeededSegment, ...]:
    """Every stored Segment, by key."""
    return tuple(_seeded(stored) for stored in segments_repo.all_segments())


def segment_ids() -> frozenset[str]:
    """Every stored Segment's id."""
    return segments_repo.segment_ids()


def segment(key: str) -> SeededSegment | None:
    """The stored Segment ``key``, or ``None``."""
    stored = segments_repo.by_key(key)
    return None if stored is None else _seeded(stored)


@dataclass(frozen=True, slots=True)
class SeededMarket:
    """A stored Market: its row id, code and version, and its decoded definition."""

    id: str
    code: str
    version: int
    definition: MarketDef


def _seeded_market(stored: StoredMarket) -> SeededMarket:
    return SeededMarket(
        id=stored.id, code=stored.code, version=stored.version, definition=stored.market
    )


def markets() -> tuple[SeededMarket, ...]:
    """Every stored Market, by code."""
    return tuple(_seeded_market(stored) for stored in markets_repo.all_markets())


def market(code: str) -> SeededMarket | None:
    """The stored Market ``code``, or ``None``."""
    stored = markets_repo.by_code(code)
    return None if stored is None else _seeded_market(stored)


@dataclass(frozen=True, slots=True)
class SeededSequence:
    """A stored Sequence: its row id, key and version, and its decoded definition."""

    id: str
    key: str
    version: int
    definition: SequenceDef


def _seeded_sequence(stored: StoredSequence) -> SeededSequence:
    return SeededSequence(
        id=stored.id, key=stored.key, version=stored.version, definition=stored.sequence
    )


def sequences() -> tuple[SeededSequence, ...]:
    """Every stored Sequence, by key."""
    return tuple(_seeded_sequence(stored) for stored in sequences_repo.all_sequences())


def sequence(key: str) -> SeededSequence | None:
    """The stored Sequence ``key``, or ``None``."""
    stored = sequences_repo.by_key(key)
    return None if stored is None else _seeded_sequence(stored)
