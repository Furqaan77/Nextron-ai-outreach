"""When a completed Sequence may restart, and on which Event (FR-63, AD-7, AD-16; Story 8.11).
Standard library and ``nextron.domain`` only.

A Sequence that ended without a Reply may restart once at least ``RECONTACT_WAIT`` (90 days,
``nextron.domain.eligibility``) after its last Step, on a live Tier 1 or 2 Event first seen after
it ended, and the new Step 1 cites that Event.

- ``EventFacts(signal_id, signal_class, tier, score_only, event_date, first_seen_at, retracted,
  evidence_ids)``: one of the Lead's Signals, with the ids of its live Evidence.
- ``qualifying_event(event, now) -> bool``: the Signal is an Event (``signal_class`` ``event``)
  of Tier 1 or 2 that isn't ``score_only`` or retracted, was first seen at or before ``now``,
  has at least one live Evidence id and is live (``signals.is_live`` at ``signals.age_days``);
  a stored class or Tier the domain refuses never qualifies.
- ``events_first_seen(events, now) -> tuple[datetime, ...]``: the ``first_seen_at`` of each
  qualifying Event, sorted: what ``compliance.evaluate_eligibility`` gives
  ``EligibilityLead.events_first_seen`` for FR-63's ``recontact_not_allowed``.
- ``LatestEnrollment(enrollment_id, contact_id, campaign_id, origin, status, stop_cause,
  last_step_sent_at, ended_at)``: the Lead's latest Enrollment (by ``created_at``, then id).
- ``RestartFacts(latest, stage, live_enrollment, events, restarted=False)``: the Lead's
  latest Enrollment (``None`` without one), its Pipeline Stage (``None`` when unknown), whether
  it has a live Enrollment, its Signals and whether any of its Enrollments (not only the
  latest) has origin ``restart``.
- ``restart_event(facts, now) -> str | None``: the restart Event's id, or ``None`` when the Lead
  may not restart. It may exactly when the Lead never restarted (``restarted`` false and the
  latest Enrollment's origin isn't ``restart``: a Lead restarts once, even when another of its
  Contacts was enrolled after the restart), the latest Enrollment is ``completed`` with no
  ``stop_cause`` (a Reply stops a Sequence, so it ended without one) and sent a Step, ``now -
  last_step_sent_at >= RECONTACT_WAIT``, the stage is in ``ENROLLABLE_STAGES`` (``new``,
  ``queued`` or ``contacted``: so never ``not_now``, ``lost``, ``disqualified`` or ``replied``
  onwards), there is no live Enrollment, and some qualifying Event was first seen after
  ``ended_at``. The restart Event is the one of those first seen last (a tie: the smallest id).
- ``restart_event_cited(cited_evidence_ids, restart_evidence_ids) -> bool``: whether a Draft's
  claims cite at least one live Evidence of the restart Event (Story 4.13's
  ``restart_event_not_cited`` rule wraps it). Each argument must be a non-``str`` iterable of
  ids (``TypeError`` otherwise: a bare ``str`` would be read character by character).
- ``RECONTACT_REASON`` and ``without_restart(decision, history) -> Decision`` (AD-21): only a
  restart re-contacts a Contact whose Sequence ended without a Reply. When ``history`` (the
  Contact's ``EndedSequence``s) holds one that wasn't replied, the Decision comes back not
  eligible, with no basis rule, and with ``recontact_not_allowed`` added once among its reasons
  in ``ReasonKind`` order (kept as it is when already there); otherwise ``decision`` unchanged.
  ``compliance.evaluate_eligibility(contact_id, restart=False)`` applies it.

Each raises ``TypeError`` for a ``now`` that isn't an aware ``datetime``, or a ``facts``,
``event`` or ``decision`` of the wrong type.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from nextron.domain.eligibility import (
    RECONTACT_TEXT,
    RECONTACT_WAIT,
    Decision,
    EndedSequence,
    Reason,
    ReasonKind,
)
from nextron.domain.enrollment import EnrollmentOrigin, EnrollmentStatus
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import SignalClass
from nextron.domain.signals import age_days, is_live

RESTART_TIERS: Final = frozenset({1, 2})
ENROLLABLE_STAGES: Final = frozenset(
    {PipelineStage.NEW, PipelineStage.QUEUED, PipelineStage.CONTACTED}
)
RECONTACT_REASON: Final = Reason(ReasonKind.RECONTACT_NOT_ALLOWED, RECONTACT_TEXT)
_KIND_ORDER: Final = {kind: index for index, kind in enumerate(ReasonKind)}


@dataclass(frozen=True, slots=True)
class EventFacts:
    """One of the Lead's Signals, with the ids of its live Evidence."""

    signal_id: str
    signal_class: str
    tier: int
    score_only: bool
    event_date: date | None
    first_seen_at: datetime
    retracted: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LatestEnrollment:
    """The Lead's latest Enrollment, by ``created_at``, then id."""

    enrollment_id: str
    contact_id: str
    campaign_id: str
    origin: str
    status: str
    stop_cause: str | None
    last_step_sent_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class RestartFacts:
    """What ``restart_event`` reads of one Lead."""

    latest: LatestEnrollment | None
    stage: str | None
    live_enrollment: bool
    events: tuple[EventFacts, ...]
    restarted: bool = False


def _aware(now: object) -> datetime:
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise TypeError("now must be an aware datetime")
    return now


def _live(event: EventFacts, now: datetime) -> bool:
    try:
        signal_class = SignalClass(event.signal_class)
        age = age_days(event.event_date, event.first_seen_at, now)
        return signal_class is SignalClass.EVENT and is_live(signal_class, event.tier, age)
    except (TypeError, ValueError):  # a stored class or Tier the domain no longer knows
        return False


def qualifying_event(event: EventFacts, now: datetime) -> bool:
    """Whether ``event`` is a live Tier 1 or 2 Event with Evidence; see the module docstring."""
    if not isinstance(event, EventFacts):
        raise TypeError("qualifying_event takes an EventFacts")
    _aware(now)
    if isinstance(event.tier, bool) or event.tier not in RESTART_TIERS:
        return False
    if event.score_only or event.retracted or not event.evidence_ids:
        return False
    if event.first_seen_at > now:
        return False
    return _live(event, now)


def events_first_seen(events: Iterable[EventFacts], now: datetime) -> tuple[datetime, ...]:
    """The ``first_seen_at`` of each qualifying Event, sorted."""
    _aware(now)
    return tuple(sorted(event.first_seen_at for event in events if qualifying_event(event, now)))


def restart_event(facts: RestartFacts, now: datetime) -> str | None:
    """The id of the Event the Lead's completed Sequence may restart on, or ``None``; see the
    module docstring."""
    if not isinstance(facts, RestartFacts):
        raise TypeError("restart_event takes a RestartFacts")
    _aware(now)
    latest = facts.latest
    if latest is None or latest.status != EnrollmentStatus.COMPLETED:
        return None
    if latest.stop_cause is not None:
        return None
    if facts.restarted or latest.origin == EnrollmentOrigin.RESTART:  # a Lead restarts once
        return None
    if latest.last_step_sent_at is None or latest.ended_at is None:
        return None
    if now - latest.last_step_sent_at < RECONTACT_WAIT:
        return None
    if facts.stage is None or facts.stage not in ENROLLABLE_STAGES:
        return None
    if facts.live_enrollment:
        return None
    ended_at = latest.ended_at
    found = [
        event
        for event in facts.events
        if qualifying_event(event, now) and event.first_seen_at > ended_at
    ]
    if not found:
        return None
    newest = max(event.first_seen_at for event in found)
    return min(event.signal_id for event in found if event.first_seen_at == newest)


def restart_event_cited(
    cited_evidence_ids: Iterable[str], restart_evidence_ids: Iterable[str]
) -> bool:
    """Whether the claims cite at least one live Evidence of the restart Event."""
    return not _ids(cited_evidence_ids, "cited_evidence_ids").isdisjoint(
        _ids(restart_evidence_ids, "restart_evidence_ids")
    )


def _ids(value: object, name: str) -> frozenset[str]:
    if isinstance(value, str | bytes) or not isinstance(value, Iterable):
        raise TypeError(f"restart_event_cited takes {name} as an iterable of ids, not a str")
    return frozenset(value)


def without_restart(decision: Decision, history: Iterable[EndedSequence]) -> Decision:
    """``decision`` with ``recontact_not_allowed`` added while ``history`` holds a Sequence that
    ended without a Reply (AD-21); see the module docstring."""
    if not isinstance(decision, Decision):
        raise TypeError("without_restart takes a Decision")
    if not any(not sequence.replied for sequence in history):
        return decision
    if ReasonKind.RECONTACT_NOT_ALLOWED in decision.kinds:
        return decision
    reasons = sorted((*decision.reasons, RECONTACT_REASON), key=lambda r: _KIND_ORDER[r.kind])
    return Decision(False, None, tuple(reasons))
