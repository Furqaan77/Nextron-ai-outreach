"""Public command API of the ``scoring`` service (FR-17, FR-62, FR-1, AD-3, AD-4, AD-7; Story
2.15).

Other services and the delivery layers change ``scoring`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back. The formulas are
pure and live in ``nextron.domain.scoring``; this module reads the Lead's rows through
``nextron.app.queries.*``, writes the score group of ``leads`` through
``nextron.db.repos.scoring.scores`` and calls ``pipeline`` and ``research`` through their
``api``. ``nextron.app.scoring.lead_scores`` runs the use cases.

**``Scoring(clock, pipeline, research)``.**

``@command rescore(lead_id) -> RescoreOutcome`` applies the Lead's current Segment:

- **Skips** (``RescoreOutcome(SKIPPED, reason)``, nothing written): ``unknown_lead`` (a text
  that isn't a ULID included), ``merged_lead``, ``lead_closed`` (``lost`` or ``disqualified``),
  ``not_enriched`` (no ``ok`` signal extraction: ``queries.research.last_ok_extraction``) and
  ``unknown_segment`` (the Lead's Segment has no row).
- **The inputs.** Every current Signal (``queries.research.signals``) as a ``ScoringSignal``
  with its live Evidence ids (``signal_evidence_ids``); a Signal that is the Event of one or
  more job postings (``queries.research.job_postings``) is ``open`` only while one of them is
  live. Every live Evidence row with a ``number`` may read a size: a ``count`` row of ``lawyers
  listed`` (``nextron.domain.people.LAWYERS_LISTED``) reads ``lawyers_firm_wide`` (counted), a
  row whose ``fact`` is a size clue (``nextron.domain.extraction.SIZE_FACTS``) reads that clue.
  The weights are the ``lead_score_fit_weight`` and ``lead_score_signal_weight`` settings.
- **Disqualified** (``domain.scoring.score`` gives a ``Disqualified``):
  ``pipeline.close(lead, disqualified, <the reading's Evidence id>,
  cause=segment_disqualifier, actor="system:scoring")``, then the score group stores the rule's
  key and its Evidence ids, NULL scores, Tier and factors, the Segment's version and
  ``tier_computed_at`` now: ``RescoreOutcome(DISQUALIFIED, evidence_id=...)``.
- **Scored**: the four scores, the Tier, ``tier_computed_at`` now, the Segment's version and
  the factors (``factors_text``), with the disqualifier columns NULL: ``RescoreOutcome(SCORED,
  tier=..., lead_score=..., factors=<how many>)``.

``@command retier(lead_id) -> bool`` re-tiers a Lead (Decision 8) and says whether it did. It
does nothing and gives ``False`` for an unknown or merged Lead, a ``lost`` or ``disqualified``
one, one with no stored ``tier``, Fit Score or factors, and one whose stored factors the domain
can't read. Otherwise it calls ``domain.scoring.retier`` on the stored Fit Score and factors and
the Lead's current Signals (built as for ``rescore``), at now with the current weights, and
stores the Signal Score, the Lead Score, the Tier, ``tier_computed_at`` and the factors, keeping
the Fit Score and the Segment version (the Segment isn't read). When it dropped an Event factor,
it also calls ``research.select_primary(lead_id)`` in the same transaction, so the Primary
Contact's choice follows the Events still live.

**Tier changes (Story 8.7).** When ``rescore`` (scored) or ``retier`` stores a Tier other than the
one the Lead held (none included), it appends one ``events`` row: entity ``leads``, the Lead's
id, ``tier_<x>`` (the old Tier, lowercase) or ``None`` to ``tier_<y>``, cause ``lead_rescored``
or ``lead_retiered``, no ``cause_ref``, actor ``scoring.ACTOR``, at now. An unchanged Tier, and
a disqualified rescore (the Pipeline's close records its own event), append none.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.db import eventlog
from nextron.db.repos.scoring import scores as scores_repo
from nextron.db.uow import command
from nextron.domain import scoring
from nextron.domain.extraction import SIZE_FACTS
from nextron.domain.people import LAWYERS_LISTED
from nextron.domain.pipeline_stage import CLOSED_STAGES, PipelineStage, StageCause
from nextron.domain.scoring import Disqualified, ScoringSignal, SizeEvidence, Tier
from nextron.domain.segments import EvidenceType, SizeClue
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock

UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
LEAD_CLOSED: Final = "lead_closed"
NOT_ENRICHED: Final = "not_enriched"
UNKNOWN_SEGMENT: Final = "unknown_segment"
ENTITY: Final = "leads"
LEAD_RESCORED: Final = "lead_rescored"  # Story 8.7: the causes of a Tier change's event
LEAD_RETIERED: Final = "lead_retiered"


class RescoreResult(StrEnum):
    SCORED = "scored"
    DISQUALIFIED = "disqualified"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class RescoreOutcome:
    """What ``rescore`` did: scored (with the Tier, the Lead Score and the number of factors),
    disqualified (with the reading's Evidence id) or skipped (with the reason)."""

    result: RescoreResult
    reason: str | None = None
    tier: Tier | None = None
    lead_score: int | None = None
    factors: int = 0
    evidence_id: str | None = None


def scoring_signals(lead_id: str) -> tuple[ScoringSignal, ...]:
    """The Lead's current Signals as the domain scores them; see the module docstring."""
    evidence_ids = research_queries.signal_evidence_ids(lead_id)
    open_events: dict[str, bool] = {}
    for posting in research_queries.job_postings(lead_id):
        if posting.signal_id is not None:
            held = open_events.get(posting.signal_id, False)
            open_events[posting.signal_id] = held or posting.live
    return tuple(
        ScoringSignal(
            signal_id=signal.id,
            signal_type=signal.signal_type,
            signal_class=signal.signal_class,
            tier=signal.tier,
            event_date=signal.event_date,
            first_seen_at=signal.first_seen_at,
            evidence_ids=evidence_ids.get(signal.id, ()),
            open=open_events.get(signal.id, True),
        )
        for signal in research_queries.signals(lead_id)
    )


def size_evidence(lead_id: str) -> tuple[SizeEvidence, ...]:
    """The Lead's live Evidence rows that read a size; see the module docstring."""
    found: list[SizeEvidence] = []
    for row in research_queries.evidence(lead_id):
        if row.number is None:
            continue
        if row.type == EvidenceType.COUNT and row.sought == LAWYERS_LISTED:
            clue = str(SizeClue.LAWYERS_FIRM_WIDE)
            found.append(SizeEvidence(row.id, clue, row.number, row.retrieved_at, True))
        elif row.fact is not None and row.fact in SIZE_FACTS:
            found.append(SizeEvidence(row.id, row.fact, row.number, row.retrieved_at, False))
    return tuple(found)


def _weights() -> tuple[int, int]:
    return (
        setup_queries.get(Key.LEAD_SCORE_FIT_WEIGHT),
        setup_queries.get(Key.LEAD_SCORE_SIGNAL_WEIGHT),
    )


def _closed(lead_id: str) -> bool:
    return pipeline_queries.stage(lead_id) in CLOSED_STAGES


def _tier_status(tier: str | None) -> str | None:
    return None if tier is None else f"tier_{tier.lower()}"


def _record_tier_change(
    lead_id: str, old: str | None, new: str, cause: str, now: datetime
) -> None:
    """Story 8.7: one ``events`` row when the stored Tier changes; see the module docstring."""
    if old == new:
        return
    eventlog.append(
        entity=ENTITY,
        entity_id=lead_id,
        from_status=_tier_status(old),
        to_status=f"tier_{new.lower()}",
        cause=cause,
        cause_ref=None,
        actor=scoring.ACTOR,
        at=now,
    )


class Scoring:
    """The score group of ``leads``; see the module docstring."""

    def __init__(self, clock: Clock, pipeline: Pipeline, research: Research) -> None:
        self._clock = clock
        self._pipeline = pipeline
        self._research = research

    @staticmethod
    def _skip_reason(lead_id: str) -> str | None:
        lead = discovery_queries.lead(lead_id)
        if lead is None:
            return UNKNOWN_LEAD
        if lead.merged_into is not None:
            return MERGED_LEAD
        if _closed(lead.id):
            return LEAD_CLOSED
        if research_queries.last_ok_extraction(lead.id) is None:
            return NOT_ENRICHED
        if setup_queries.segment_by_id(lead.segment_id) is None:
            return UNKNOWN_SEGMENT
        return None

    @command
    def rescore(self, lead_id: str) -> RescoreOutcome:
        """Score the Lead against its current Segment; see the module docstring."""
        skipped = self._skip_reason(lead_id)
        lead = discovery_queries.lead(lead_id)
        seeded = None if lead is None else setup_queries.segment_by_id(lead.segment_id)
        if skipped is not None or lead is None or seeded is None:
            return RescoreOutcome(RescoreResult.SKIPPED, reason=skipped)
        now = self._clock.now()
        fit_weight, signal_weight = _weights()
        found = scoring.score(
            seeded.definition,
            scoring_signals(lead.id),
            size_evidence(lead.id),
            fit_weight,
            signal_weight,
            now,
        )
        if isinstance(found, Disqualified):
            [evidence_id] = found.evidence_ids
            self._pipeline.close(
                lead.id,
                PipelineStage.DISQUALIFIED,
                evidence_id,
                cause=StageCause.SEGMENT_DISQUALIFIER,
                actor=scoring.ACTOR,
            )
            scores_repo.write_disqualified(
                lead.id,
                rule=found.rule_key,
                evidence=scoring.evidence_text(found.evidence_ids),
                computed_at=now,
                segment_version=seeded.version,
            )
            return RescoreOutcome(RescoreResult.DISQUALIFIED, evidence_id=evidence_id)
        stored = scores_repo.stored_row(lead.id)
        scores_repo.write_score(
            lead.id,
            fit=found.fit,
            signal=found.signal,
            lead=found.lead,
            tier=str(found.tier),
            computed_at=now,
            segment_version=seeded.version,
            factors=scoring.factors_text(found.fit_factors, found.signal_factors),
        )
        old_tier = None if stored is None else stored.tier
        _record_tier_change(lead.id, old_tier, str(found.tier), LEAD_RESCORED, now)
        return RescoreOutcome(
            RescoreResult.SCORED,
            tier=found.tier,
            lead_score=found.lead,
            factors=len(found.fit_factors) + len(found.signal_factors),
        )

    @command
    def retier(self, lead_id: str) -> bool:
        """Re-tier the Lead at now with its stored factors; see the module docstring."""
        lead = discovery_queries.lead(lead_id)
        if lead is None or lead.merged_into is not None or _closed(lead.id):
            return False
        row = scores_repo.stored_row(lead.id)
        if row is None or row.tier is None or row.fit_score is None or row.score_factors is None:
            return False
        try:
            fit_factors, signal_factors = scoring.factors_from_text(row.score_factors)
        except ValueError:  # a stored text the domain no longer reads: Re-score applies anew
            return False
        fit_weight, signal_weight = _weights()
        now = self._clock.now()
        result = scoring.retier(
            row.fit_score,
            fit_factors,
            signal_factors,
            scoring_signals(lead.id),
            fit_weight,
            signal_weight,
            now,
        )
        scores_repo.write_tier(
            lead.id,
            signal=result.score.signal,
            lead=result.score.lead,
            tier=str(result.score.tier),
            computed_at=now,
            factors=scoring.factors_text(result.score.fit_factors, result.score.signal_factors),
        )
        _record_tier_change(lead.id, row.tier, str(result.score.tier), LEAD_RETIERED, now)
        if result.dropped:
            self._research.select_primary(lead.id)
        return True
