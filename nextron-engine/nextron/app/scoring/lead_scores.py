"""The scoring use cases (FR-17, FR-62, AD-7, AD-10; Story 2.15): part of the ``scoring``
service, the sole writer of the score group of ``leads``. ``nextron.app.scoring.api``'s
docstring holds the rules. No log line or job payload holds a name, a quote or a URL: IDs, codes
and counts only.

**``LeadScores(clock, scoring, queue)``.**

- ``@use_case score_lead(lead_id) -> RescoreOutcome`` (the ``score_lead`` job, the end of the
  research chain): ``scoring.rescore(lead_id)`` in one transaction. After the commit it logs
  ``lead_scored`` (``lead_id``, ``lead_score``, ``factors``), ``lead_disqualified``
  (``lead_id``, ``evidence_id``) or ``score_skipped`` (``lead_id``, ``reason``).
- ``@use_case recompute_tiers() -> int`` (the daily ``recompute_tiers`` trigger's job): reads
  ``queries.scoring.retier_candidates()`` in one transaction, then calls
  ``scoring.retier(lead_id)`` for each in a transaction of its own, so one Lead's work never
  waits on another's. It logs ``tiers_recomputed`` (``leads``: the candidates, ``retiered``)
  and returns how many were re-tiered.
- ``@use_case request_rescore(lead_id) -> RescoreRequest`` (the Lead page's "Re-score"): in
  one transaction, enqueues ``score_lead`` on the interactive lane (``enqueue_score_lead``) and
  returns ``QUEUED``. It enqueues nothing and returns ``UNKNOWN`` for an unknown Lead (a text
  that isn't a ULID included), ``MERGED`` for a merged one and ``CLOSED`` for a ``lost`` or
  ``disqualified`` one, which the job would only skip.

``enqueue_score_lead(queue, lead_id, lane=None)`` is this service's enqueue: scope the Lead,
discriminator ``tick`` (a live job absorbs a repeat), payload ``{v, lead_id}``. The research
service (``people.py``, ``job_boards.py``) enqueues the same job with the same key and payload
inline (``nextron.domain.scoring.SCORE_LEAD_JOB``), at the end of the chain and after a
re-check closes or reopens a posting, because it can't import this module.
"""

import logging
from enum import StrEnum
from typing import Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import scoring as scoring_queries
from nextron.app.scoring.api import RescoreOutcome, RescoreResult, Scoring
from nextron.db.uow import current, use_case
from nextron.domain.crawl import LEAD_ID_FIELD, PAYLOAD_VERSION
from nextron.domain.job import TICK, VERSION_FIELD, Lane
from nextron.domain.pipeline_stage import CLOSED_STAGES
from nextron.domain.scoring import SCORE_LEAD_JOB
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

LEAD_SCORED: Final = "lead_scored"
LEAD_DISQUALIFIED: Final = "lead_disqualified"
SCORE_SKIPPED: Final = "score_skipped"
TIERS_RECOMPUTED: Final = "tiers_recomputed"


class RescoreRequest(StrEnum):
    """What ``request_rescore`` did."""

    QUEUED = "queued"
    UNKNOWN = "unknown"
    MERGED = "merged"
    CLOSED = "closed"


def enqueue_score_lead(queue: JobQueue, lead_id: str, lane: Lane | None = None) -> None:
    """Enqueue the Lead's ``score_lead`` job in the caller's transaction."""
    queue.enqueue(
        SCORE_LEAD_JOB,
        scope=lead_id,
        discriminator=TICK,
        payload={VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: lead_id},
        lane=lane,
    )


class LeadScores:
    """The scoring jobs and the Lead page's "Re-score"; see the module docstring."""

    def __init__(self, clock: Clock, scoring: Scoring, queue: JobQueue) -> None:
        self._clock = clock
        self._scoring = scoring
        self._queue = queue

    @use_case
    def score_lead(self, lead_id: str) -> RescoreOutcome:
        """Score the Lead; see the module docstring."""
        with current().transaction():
            outcome = self._scoring.rescore(lead_id)
        if outcome.result is RescoreResult.SCORED:
            log.info(
                LEAD_SCORED,
                extra={
                    "lead_id": lead_id,
                    "lead_score": outcome.lead_score,
                    "factors": outcome.factors,
                },
            )
        elif outcome.result is RescoreResult.DISQUALIFIED:
            log.info(
                LEAD_DISQUALIFIED,
                extra={"lead_id": lead_id, "evidence_id": outcome.evidence_id},
            )
        else:
            log.info(SCORE_SKIPPED, extra={"lead_id": lead_id, "reason": outcome.reason})
        return outcome

    @use_case
    def recompute_tiers(self) -> int:
        """Re-tier every candidate Lead, one transaction each; see the module docstring."""
        uow = current()
        with uow.transaction():
            candidates = scoring_queries.retier_candidates()
        retiered = 0
        for lead_id in candidates:
            with uow.transaction():
                if self._scoring.retier(lead_id):
                    retiered += 1
        log.info(TIERS_RECOMPUTED, extra={"leads": len(candidates), "retiered": retiered})
        return retiered

    @use_case
    def request_rescore(self, lead_id: str) -> RescoreRequest:
        """Queue the Lead's ``score_lead`` job on the interactive lane, unless the Lead is
        unknown, merged or closed; see the module docstring."""
        with current().transaction():
            lead = discovery_queries.lead(lead_id)
            if lead is None:
                return RescoreRequest.UNKNOWN
            if lead.merged_into is not None:
                return RescoreRequest.MERGED
            if pipeline_queries.stage(lead.id) in CLOSED_STAGES:
                return RescoreRequest.CLOSED
            enqueue_score_lead(self._queue, lead.id, Lane.INTERACTIVE)
        return RescoreRequest.QUEUED
