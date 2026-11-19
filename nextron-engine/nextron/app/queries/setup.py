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
- ``footer_settings()``: the six footer settings (Story 4.11) as a
  ``nextron.domain.render.FooterSettings``, each read through ``get``; one that raises
  ``SettingNotSet`` or ``InvalidSettingValue`` is ``""``, so a stored text that no longer parses
  counts as missing (fail closed: ``footer_complete`` then blocks sending).
- ``budget_limits()``: the ``nextron.domain.budget.Limits`` the seven Budget and limits
  settings make: each quota in ``QUOTAS`` (its setting × its ``scale``, so Hunter's credits
  are doubled like its stored units), ``ai_budget_monthly`` and ``ai_budget_alert_pct``. A
  stored value that no longer parses raises ``InvalidSettingValue``, like ``get``.
- ``segments()``: every stored Segment as a ``SeededSegment(id, key, version, definition)``,
  by key; ``segment(key)``: one, or ``None``. ``definition`` is the ``SegmentDef`` decoded by
  ``nextron.domain.segments.segment_from_text``, which raises ``InvalidSegments`` for a stored
  text that no longer validates. Research, scoring and drafting (Stories 2.2 onwards) read the
  Segments only through these two and ``segment_by_id(segment_id)`` (Story 2.9: the Segment
  with that row id, or ``None``, a text that isn't a ULID included; a Lead names its Segment by
  id). ``segment_ids()``: every stored Segment's id, a ``frozenset`` (``Discovery.upsert_lead``
  checks a candidate's Segment against it). ``segment_keys()`` (Story 11.5): every stored
  Segment's key, a ``frozenset``, without decoding (the seed sync's managed mode).
- ``segment_version(segment_id, version)`` (Story 11.5): that stored version of the Segment as
  a ``SegmentVersion(segment_id, version, definition, created_at, actor)``, or ``None`` (a
  Segment id that isn't a ULID, or a version not stored, included); ``segment_versions
  (segment_id)``: every stored version, oldest first. Each ``definition`` is decoded with
  ``segment_from_text`` (``InvalidSegments`` for a text that no longer validates). Scoring
  re-scores an existing Lead with its stored version through ``segment_version``.
- ``markets()``: every stored Market as a ``SeededMarket(id, code, version, definition)``, by
  code; ``market(code)``: one, or ``None``; ``market_by_id(market_id)`` (Story 4.12): one by
  its row id, or ``None`` (a text that isn't a ULID included). ``definition`` is the
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
  Sequences only through these two and ``sequence_by_id(sequence_id)`` (Story 4.8: the Sequence
  with that row id, or ``None``, a text that isn't a ULID included; a Campaign names its
  Sequence by id, and Enrollment binds its version).
- ``offers()``: every stored Offer as a ``SeededOffer(id, key, version, definition)``, by key;
  ``offer(key)``: one, or ``None``. ``definition`` is the ``nextron.domain.offers.OfferDef`` the
  ``offers`` repository decodes through ``offer_from_form``, which raises ``InvalidOffers`` for a
  row that no longer validates. An Offer is commercial configuration, never Draft wording.
- ``campaigns()``: every stored Campaign as a ``SeededCampaign(id, key, name, version,
  segment_id, market_id, offer_id, sequence_id, status, lia, variants)``, by key;
  ``campaign(campaign_id)``: one, or ``None``. ``status`` is a ``CampaignStatus``, ``lia`` the
  recorded ``Lia`` or ``None``, and ``variants`` a tuple of ``SeededVariant(id, name, status,
  step_angles)`` ordered by name. A row set that no longer validates raises
  ``InvalidCampaigns``. Enrollment (4.8) reads the LIA it records as the eligibility basis here.
- ``campaign_active(campaign_id) -> bool``: the single answer to "may this Campaign run?"
  (AD-5's ``campaign_active``): ``nextron.domain.campaign.may_run`` on the stored status, the
  stored LIA and the Market's **current** ``country_code``, read live with the Campaign. An
  unknown id is ``False``.
- ``away_periods() -> tuple[AwayPeriod, ...]`` (Story 5.11, FR-31): the stored
  ``away_periods`` lines, each read with ``nextron.domain.sending_policy.parse_away_period``;
  a stored line that no longer parses is ignored, and without a row there are none.
  ``sender_local_date(now) -> date``: ``now`` (aware) as a calendar date in
  ``sender_timezone``, or in UTC while that is unset or unreadable. ``away_until(now) -> date |
  None``: ``sending_policy.away_until`` of that date over those periods: the last day of the
  away run holding today, or ``None`` when today isn't away (the Dashboard's banner).

All read through the current unit of work, read-only ones included.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nextron.db.repos.setup import campaigns as campaigns_repo
from nextron.db.repos.setup import markets as markets_repo
from nextron.db.repos.setup import offers as offers_repo
from nextron.db.repos.setup import segments as segments_repo
from nextron.db.repos.setup import sequences as sequences_repo
from nextron.db.repos.setup import settings as settings_repo
from nextron.db.repos.setup.campaigns import StoredCampaign
from nextron.db.repos.setup.markets import StoredMarket
from nextron.db.repos.setup.offers import StoredOffer
from nextron.db.repos.setup.segments import StoredSegment, StoredSegmentVersion
from nextron.db.repos.setup.sequences import StoredSequence
from nextron.domain.budget import QUOTAS, Limits
from nextron.domain.campaign import CampaignStatus, Lia, StepAngleNote, VariantStatus, may_run
from nextron.domain.ids import is_ulid
from nextron.domain.markets import MarketDef
from nextron.domain.offers import OfferDef
from nextron.domain.render import FOOTER_SETTING_KEYS, FooterSettings
from nextron.domain.segments import SegmentDef, segment_from_text
from nextron.domain.sending_policy import AwayPeriod, parse_away_period
from nextron.domain.sending_policy import away_until as _away_until
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


def footer_settings() -> FooterSettings:
    """The footer settings now, ``""`` for one that is unset or no longer parses."""
    values: dict[str, str] = {}
    for key in FOOTER_SETTING_KEYS:
        try:
            value = get(key)
        except (SettingNotSet, InvalidSettingValue):
            value = ""
        values[key] = value if isinstance(value, str) else ""
    return FooterSettings(**values)


def budget_limits() -> Limits:
    """The quotas (in stored units) and the AI budget the settings hold now."""
    return Limits(
        quotas={spec.name: get(spec.setting) * spec.scale for spec in QUOTAS},
        ai_budget_micros=get(Key.AI_BUDGET_MONTHLY),
        ai_alert_pct=get(Key.AI_BUDGET_ALERT_PCT),
    )


def away_periods() -> tuple[AwayPeriod, ...]:
    """The stored away periods, in stored order; a line that no longer parses is ignored."""
    text = settings_repo.stored_text(str(Key.AWAY_PERIODS))
    if text is None:
        return ()
    found = (parse_away_period(line) for line in text.splitlines())
    return tuple(period for period in found if period is not None)


def _sender_zone() -> tzinfo:
    try:
        return ZoneInfo(str(get(Key.SENDER_TIMEZONE)))
    except (SettingNotSet, InvalidSettingValue, ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def sender_local_date(now: datetime) -> date:
    """``now`` as a date in ``sender_timezone``, or in UTC while it is unset or unreadable."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise TypeError("now must be an aware datetime")
    return now.astimezone(_sender_zone()).date()


def away_until(now: datetime) -> date | None:
    """The last day of the away run holding ``now``'s date, or ``None`` when not away; with
    no period stored it is ``None`` without reading ``sender_timezone``."""
    periods = away_periods()
    if not periods:
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise TypeError("now must be an aware datetime")
        return None
    return _away_until(sender_local_date(now), periods)


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


def segment_keys() -> frozenset[str]:
    """Every stored Segment's key, without decoding a definition (Story 11.5)."""
    return frozenset(stored.key for stored in segments_repo.all_segments())


def segment(key: str) -> SeededSegment | None:
    """The stored Segment ``key``, or ``None``."""
    stored = segments_repo.by_key(key)
    return None if stored is None else _seeded(stored)


def segment_by_id(segment_id: str) -> SeededSegment | None:
    """The stored Segment with the row id ``segment_id``, or ``None``."""
    if not is_ulid(segment_id):
        return None
    stored = segments_repo.get(segment_id)
    return None if stored is None else _seeded(stored)


@dataclass(frozen=True, slots=True)
class SegmentVersion:
    """One stored version of a Segment: its Segment's id, the version, its decoded definition,
    when it was stored and by whom (``seed`` or ``operator``)."""

    segment_id: str
    version: int
    definition: SegmentDef
    created_at: datetime
    actor: str


def _version(stored: StoredSegmentVersion) -> SegmentVersion:
    return SegmentVersion(
        segment_id=stored.segment_id,
        version=stored.version,
        definition=segment_from_text(stored.definition),
        created_at=stored.created_at,
        actor=stored.actor,
    )


def segment_version(segment_id: str, version: int) -> SegmentVersion | None:
    """Version ``version`` of the Segment ``segment_id``, or ``None``."""
    if not is_ulid(segment_id) or isinstance(version, bool) or not isinstance(version, int):
        return None
    stored = segments_repo.version(segment_id, version)
    return None if stored is None else _version(stored)


def segment_versions(segment_id: str) -> tuple[SegmentVersion, ...]:
    """Every stored version of the Segment ``segment_id``, oldest first."""
    if not is_ulid(segment_id):
        return ()
    return tuple(_version(stored) for stored in segments_repo.versions(segment_id))


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


def market_by_id(market_id: str) -> SeededMarket | None:
    """The stored Market whose row id is ``market_id``, or ``None``."""
    if not is_ulid(market_id):
        return None
    stored = markets_repo.by_id(market_id)
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


def sequence_by_id(sequence_id: str) -> SeededSequence | None:
    """The stored Sequence with the row id ``sequence_id``, or ``None``."""
    if not is_ulid(sequence_id):
        return None
    stored = sequences_repo.by_id(sequence_id)
    return None if stored is None else _seeded_sequence(stored)


@dataclass(frozen=True, slots=True)
class SeededOffer:
    """A stored Offer: its row id, key and version, and its decoded definition."""

    id: str
    key: str
    version: int
    definition: OfferDef


def _seeded_offer(stored: StoredOffer) -> SeededOffer:
    return SeededOffer(
        id=stored.id, key=stored.key, version=stored.version, definition=stored.offer
    )


def offers() -> tuple[SeededOffer, ...]:
    """Every stored Offer, by key."""
    return tuple(_seeded_offer(stored) for stored in offers_repo.all_offers())


def offer(key: str) -> SeededOffer | None:
    """The stored Offer ``key``, or ``None``."""
    stored = offers_repo.by_key(key)
    return None if stored is None else _seeded_offer(stored)


@dataclass(frozen=True, slots=True)
class SeededVariant:
    """A stored Variant of a Campaign."""

    id: str
    name: str
    status: VariantStatus
    step_angles: tuple[StepAngleNote, ...]


@dataclass(frozen=True, slots=True)
class SeededCampaign:
    """A stored Campaign: its row id, key, name and version, the ids it uses, its status, its
    recorded LIA (or ``None``) and its Variants by name."""

    id: str
    key: str
    name: str
    version: int
    segment_id: str
    market_id: str
    offer_id: str
    sequence_id: str
    status: CampaignStatus
    lia: Lia | None
    variants: tuple[SeededVariant, ...]


def _seeded_campaign(stored: StoredCampaign) -> SeededCampaign:
    return SeededCampaign(
        id=stored.id,
        key=stored.key,
        name=stored.name,
        version=stored.version,
        segment_id=stored.segment_id,
        market_id=stored.market_id,
        offer_id=stored.offer_id,
        sequence_id=stored.sequence_id,
        status=stored.status,
        lia=stored.lia,
        variants=tuple(
            SeededVariant(
                id=variant.id,
                name=variant.name,
                status=variant.status,
                step_angles=variant.step_angles,
            )
            for variant in stored.variants
        ),
    )


def campaigns() -> tuple[SeededCampaign, ...]:
    """Every stored Campaign, by key."""
    return tuple(_seeded_campaign(stored) for stored in campaigns_repo.all_campaigns())


def campaign(campaign_id: str) -> SeededCampaign | None:
    """The stored Campaign ``campaign_id``, or ``None``."""
    if not isinstance(campaign_id, str):
        return None
    stored = campaigns_repo.by_id(campaign_id)
    return None if stored is None else _seeded_campaign(stored)


def campaign_active(campaign_id: str) -> bool:
    """Whether the Campaign ``campaign_id`` may run now; see the module docstring."""
    if not isinstance(campaign_id, str):
        return False
    stored = campaigns_repo.by_id(campaign_id)
    if stored is None:
        return False
    return may_run(stored.status, stored.country_code, stored.lia)
