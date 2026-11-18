"""Today's new-Sequence capacity and the Operator's picks (FR-19 S1, FR-59, UX-DR20, AD-11,
AD-16, AD-21; Story 4.10): part of the ``scoring`` service, the sole writer of
``daily_capacity`` (``nextron.db.repos.scoring.capacity``). No log line holds a name, an
address or a URL: counts only.

**``Selection(clock, sequencing, compliance)``.**

**Today's capacity** (``today() -> TodayCapacity``, a read that works in a read-only unit of
work too), computed live from the clock's now:

- the date is ``now`` in the ``sender_timezone`` setting, or in UTC while that is unset or
  unreadable;
- ``total``: the sum of ``queries.sending.cold_daily_caps(date)`` (Story 6.4's ramped cold
  caps; 0 for an inbox that isn't ``active`` or warmed up 14 days);
- ``reserved``: the follow-ups due that date over every inbox
  (``queries.sequencing.follow_ups_due``);
- ``steps``: the largest Step count among the stored Sequences (0 with none, or when they no
  longer decode);
- ``capacity``: ``domain.sending_policy.new_sequence_capacity(total, reserved, steps)``, or 0
  while ``steps`` is 0;
- ``picked``: today's ``daily_capacity`` row's ``picked`` (0 without one); ``remaining``:
  ``max(0, capacity - picked)``.

**``@command record_capacity() -> TodayCapacity``** joins the caller's transaction: it
finalises every earlier ``daily_capacity`` row whose ``signal_shortfall`` is NULL (to ``max(0,
new_sequence_capacity - picked)``), then upserts today's row with the live figures (``picked``
kept). It runs at each pick and once a day (``LeadScores.recompute_tiers``). Nothing ever fills
unused capacity automatically.

**``ready_list() -> tuple[ReadyRow, ...]``** (a read): the Leads with a stored Tier and Lead
Score that aren't merged, whose stage is ``new``, ``queued`` or ``contacted``, that have a
Primary Contact who isn't erased and that have no live Enrollment, in
``domain.scoring.ready_order``: a Tier A Lead's newest live Event is the smallest
``domain.signals.age_days`` at now over its stored Event factors' Signals that aren't
retracted. Each ``ReadyRow`` carries the Lead's name, its Segment's name, the Contact's Market's
name (``None`` without one), the Tier, the Lead Score, the Contact's role, the live Eligibility
(``Compliance.evaluate_eligibility``; never stored), ``generic_copy`` (Tier C) and
``below_minimum`` (the Lead Score below the ``min_lead_score`` setting; its default while
unreadable). Both badges leave the Lead pickable.

**``@use_case pick(lead_ids) -> tuple[PickOutcome, ...]``** runs in one transaction, in the
given order, one ``PickOutcome(lead_id, enrollment_id, reason)`` per Lead. ``lead_ids`` is a
``tuple`` or ``list`` of ``str`` (``TypeError`` otherwise). The reasons, checked in order:
``unknown_lead`` (unknown or merged), ``no_primary_contact`` (no Primary Contact that isn't
erased), ``no_campaign`` (no Campaign for the Lead's Segment and the Contact's Market),
``over_capacity`` (nothing remains today); otherwise ``Sequencing.enroll(contact, campaign,
operator_pick, every Step of the Campaign's Sequence)``, whose refusal gives its
``RefusalCode`` value. The Campaign is the first by key that ``queries.setup.campaign_active``
allows, else the first by key (so ``enroll`` answers ``campaign_not_active``). Each enrolled
Lead takes one from what remains. After the loop it records the capacity (as
``record_capacity``) and adds the enrolled count to today's ``picked``; nothing is written
before the first ``enroll`` (AD-16: its gate call comes first). No job, AI call or email:
``enroll``'s Draft slots only. After the commit it logs ``leads_picked`` (``leads``,
``enrolled``, ``refused``).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nextron.app.compliance.api import Compliance
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import scoring as scoring_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.sequencing.api import Sequencing
from nextron.db.repos.scoring import capacity as capacity_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.campaign import InvalidCampaigns
from nextron.domain.eligibility import Decision
from nextron.domain.enrollment import EnrollmentOrigin, NewEnrollment
from nextron.domain.markets import InvalidMarkets
from nextron.domain.scoring import (
    DEFAULT_MIN_LEAD_SCORE,
    FactorKind,
    ReadyCandidate,
    Tier,
    below_minimum,
    ready_order,
)
from nextron.domain.segments import InvalidSegments
from nextron.domain.sending_policy import new_sequence_capacity
from nextron.domain.sequences import InvalidSequences
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.domain.signals import age_days
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

LEADS_PICKED: Final = "leads_picked"


class PickReason(StrEnum):
    """Why ``pick`` didn't enroll a Lead before calling ``enroll``."""

    UNKNOWN_LEAD = "unknown_lead"
    NO_PRIMARY_CONTACT = "no_primary_contact"
    NO_CAMPAIGN = "no_campaign"
    OVER_CAPACITY = "over_capacity"


@dataclass(frozen=True, slots=True)
class TodayCapacity:
    """Today's live capacity figures and picks."""

    local_date: date
    total: int
    reserved: int
    steps: int
    capacity: int
    picked: int

    @property
    def remaining(self) -> int:
        """What can still be picked today."""
        return max(0, self.capacity - self.picked)


@dataclass(frozen=True, slots=True)
class ReadyRow:
    """One Lead of the "Ready to draft" list."""

    lead_id: str
    name: str
    segment: str
    market: str | None
    tier: Tier
    lead_score: int
    role: str | None
    eligibility: Decision
    generic_copy: bool
    below_minimum: bool


@dataclass(frozen=True, slots=True)
class PickOutcome:
    """What ``pick`` did with one Lead: its new Enrollment, or why not."""

    lead_id: str
    enrollment_id: str | None
    reason: str | None


def _sender_zone() -> tzinfo:
    """The ``sender_timezone``, or UTC while it is unset or unreadable."""
    try:
        return ZoneInfo(str(setup_queries.get(Key.SENDER_TIMEZONE)))
    except (SettingNotSet, InvalidSettingValue, ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def _largest_sequence() -> int:
    """The largest Step count among the stored Sequences; 0 with none or when unreadable."""
    try:
        found = setup_queries.sequences()
    except InvalidSequences:
        return 0
    return max((len(sequence.definition.steps) for sequence in found), default=0)


def _min_lead_score() -> int:
    """The ``min_lead_score`` setting; its default while unreadable."""
    try:
        value = setup_queries.get(Key.MIN_LEAD_SCORE)
    except (SettingNotSet, InvalidSettingValue):
        return DEFAULT_MIN_LEAD_SCORE
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_MIN_LEAD_SCORE
    return value


def _primary(lead_id: str) -> research_queries.StoredContact | None:
    """The Lead's Primary Contact who isn't erased, or ``None``."""
    return next(
        (
            contact
            for contact in research_queries.contacts(lead_id)
            if contact.is_primary and contact.erased_at is None
        ),
        None,
    )


def _newest_event_age(lead_id: str, now: datetime) -> int | None:
    """The age in days of the Lead's newest live Event among its stored Event factors."""
    stored = scoring_queries.stored(lead_id)
    if stored is None:
        return None
    signals = {signal.id: signal for signal in research_queries.signals(lead_id)}
    events = [
        signals[factor.signal_id]
        for factor in stored.signal_factors
        if factor.kind is FactorKind.EVENT and factor.signal_id in signals
    ]
    ages = [age_days(signal.event_date, signal.first_seen_at, now) for signal in events]
    return min(ages, default=None)


def _segment_names() -> dict[str, str]:
    """Each stored Segment's name by id; ``{}`` when the rows no longer decode."""
    try:
        return {row.id: row.definition.name for row in setup_queries.segments()}
    except InvalidSegments:
        return {}


def _market_names() -> dict[str, str]:
    """Each stored Market's name by id; ``{}`` when the rows no longer decode."""
    try:
        return {row.id: row.definition.name for row in setup_queries.markets()}
    except InvalidMarkets:
        return {}


def _campaign(segment_id: str, market_id: str | None) -> setup_queries.SeededCampaign | None:
    """The Campaign a pick enrolls in; see the module docstring."""
    if market_id is None:
        return None
    try:
        found = [
            campaign
            for campaign in setup_queries.campaigns()
            if campaign.segment_id == segment_id and campaign.market_id == market_id
        ]
    except InvalidCampaigns:
        return None
    return next(
        (campaign for campaign in found if setup_queries.campaign_active(campaign.id)),
        found[0] if found else None,
    )


def _steps_of(campaign: setup_queries.SeededCampaign) -> tuple[int, ...]:
    """Every Step number of the Campaign's Sequence; ``()`` when it is missing or no longer
    decodes (the pick is then refused ``no_campaign``)."""
    try:
        sequence = setup_queries.sequence_by_id(campaign.sequence_id)
    except InvalidSequences:
        return ()
    if sequence is None:
        return ()
    return tuple(range(1, len(sequence.definition.steps) + 1))


def _checked_ids(lead_ids: object) -> tuple[str, ...]:
    if not isinstance(lead_ids, tuple | list):
        raise TypeError(f"pick takes the Lead ids as a tuple, not {type(lead_ids).__name__}")
    if not all(isinstance(lead_id, str) for lead_id in lead_ids):
        raise TypeError("pick takes the Lead ids as str")
    return tuple(lead_ids)


class Selection:
    """Today's capacity, the "Ready to draft" list and the Operator's picks; see the module
    docstring."""

    def __init__(self, clock: Clock, sequencing: Sequencing, compliance: Compliance) -> None:
        self._clock = clock
        self._sequencing = sequencing
        self._compliance = compliance

    def _figures(self, now: datetime) -> TodayCapacity:
        zone = _sender_zone()
        local_date = now.astimezone(zone).date()
        total = sum(sending_queries.cold_daily_caps(local_date).values())
        reserved = sum(sequencing_queries.follow_ups_due(local_date, zone).values())
        steps = _largest_sequence()
        capacity = new_sequence_capacity(total, reserved, steps) if steps >= 1 else 0
        row = capacity_repo.by_date(local_date)
        return TodayCapacity(
            local_date=local_date,
            total=total,
            reserved=reserved,
            steps=steps,
            capacity=capacity,
            picked=0 if row is None else row.picked,
        )

    def _record(self, now: datetime) -> TodayCapacity:
        figures = self._figures(now)
        capacity_repo.finalise_before(figures.local_date)
        capacity_repo.upsert(
            figures.local_date,
            total=figures.total,
            reserved=figures.reserved,
            capacity=figures.capacity,
            computed_at=now,
        )
        return figures

    def today(self) -> TodayCapacity:
        """Today's live capacity; see the module docstring."""
        return self._figures(self._clock.now())

    @command
    def record_capacity(self) -> TodayCapacity:
        """Finalise the earlier days and store today's live figures; see the module
        docstring."""
        return self._record(self._clock.now())

    def ready_list(self) -> tuple[ReadyRow, ...]:
        """The "Ready to draft" list, in its order; see the module docstring."""
        now = self._clock.now()
        minimum = _min_lead_score()
        segments = _segment_names()
        markets = _market_names()
        found: dict[
            str, tuple[ReadyCandidate, discovery_queries.StoredLead, research_queries.StoredContact]
        ] = {}
        for lead_id, tier_text, score in scoring_queries.ready_candidates():
            try:
                tier = Tier(tier_text)
            except ValueError:
                continue
            lead = discovery_queries.lead(lead_id)
            contact = _primary(lead_id)
            if lead is None or contact is None:
                continue
            if sequencing_queries.live_enrollment(lead_id) is not None:
                continue
            candidate = ReadyCandidate(lead_id, tier, score, _newest_event_age(lead_id, now))
            found[lead_id] = (candidate, lead, contact)
        rows = []
        for candidate in ready_order(entry[0] for entry in found.values()):
            _, lead, contact = found[candidate.lead_id]
            rows.append(
                ReadyRow(
                    lead_id=lead.id,
                    name=lead.name,
                    segment=segments.get(lead.segment_id, lead.segment_id),
                    market=None if contact.market_id is None else markets.get(contact.market_id),
                    tier=candidate.tier,
                    lead_score=candidate.lead_score,
                    role=contact.role,
                    eligibility=self._compliance.evaluate_eligibility(contact.id),
                    generic_copy=candidate.tier is Tier.C,
                    below_minimum=below_minimum(candidate.lead_score, minimum),
                )
            )
        return tuple(rows)

    def _pick_one(self, lead_id: str, remaining: int) -> PickOutcome:
        lead = discovery_queries.lead(lead_id)
        if lead is None or lead.merged_into is not None:
            return PickOutcome(lead_id, None, PickReason.UNKNOWN_LEAD)
        contact = _primary(lead.id)
        if contact is None:
            return PickOutcome(lead_id, None, PickReason.NO_PRIMARY_CONTACT)
        campaign = _campaign(lead.segment_id, contact.market_id)
        steps = () if campaign is None else _steps_of(campaign)
        if campaign is None or not steps:
            return PickOutcome(lead_id, None, PickReason.NO_CAMPAIGN)
        if remaining <= 0:
            return PickOutcome(lead_id, None, PickReason.OVER_CAPACITY)
        result = self._sequencing.enroll(
            contact.id, campaign.id, EnrollmentOrigin.OPERATOR_PICK, steps
        )
        if isinstance(result, NewEnrollment):
            return PickOutcome(lead_id, result.enrollment_id, None)
        return PickOutcome(lead_id, None, str(result.code))

    @use_case
    def pick(self, lead_ids: Sequence[str]) -> tuple[PickOutcome, ...]:
        """Enroll the picked Leads up to today's remaining capacity; see the module
        docstring."""
        chosen = _checked_ids(lead_ids)
        now = self._clock.now()
        outcomes: list[PickOutcome] = []
        with current().transaction():
            remaining = self._figures(now).remaining
            for lead_id in chosen:
                outcome = self._pick_one(lead_id, remaining)
                if outcome.enrollment_id is not None:
                    remaining -= 1
                outcomes.append(outcome)
            enrolled = sum(1 for outcome in outcomes if outcome.enrollment_id is not None)
            figures = self._record(now)
            if enrolled:
                capacity_repo.add_picked(figures.local_date, enrolled)
        log.info(
            LEADS_PICKED,
            extra={
                "leads": len(outcomes),
                "enrolled": enrolled,
                "refused": len(outcomes) - enrolled,
            },
        )
        return tuple(outcomes)
