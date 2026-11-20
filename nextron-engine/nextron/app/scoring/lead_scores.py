"""The scoring use cases (FR-17, FR-62, AD-7, AD-10; Story 2.15): part of the ``scoring``
service, the sole writer of the score group of ``leads``. ``nextron.app.scoring.api``'s
docstring holds the rules. No log line or job payload holds a name, a quote or a URL: IDs, codes
and counts only.

**``LeadScores(clock, scoring, queue, *, selection=None, discovery=None)``.**

- ``@use_case score_lead(lead_id, *, use_current=False) -> RescoreOutcome`` (the ``score_lead``
  job, the end of the research chain): ``scoring.rescore(lead_id, use_current=...)`` in one
  transaction (a job whose payload says ``use_current: true`` applies the current Segment
  definition; any other keeps the Lead's stored version). After the commit it logs
  ``lead_scored`` (``lead_id``, ``lead_score``, ``factors``), ``lead_disqualified``
  (``lead_id``, ``evidence_id``) or ``score_skipped`` (``lead_id``, ``reason``).

  **Draft when ready (Story 4.16).** Built with both a ``selection`` and a ``discovery`` (any
  object with ``record_draft_outcome(lead_id, outcome)``, ``DraftOutcomes``: scoring can't
  import its sibling ``discovery``, so ``main.py`` hands it in), and only then, the same
  transaction goes on when the Lead has ``processed`` Signal Inbox items with
  ``draft_when_ready`` and no decision yet (``queries.discovery.undecided_draft_items``):
  ``SCORED`` calls ``selection.pick_one(lead_id)`` and records ``enrolled`` or the pick's reason
  on those items; ``DISQUALIFIED`` records ``disqualified`` with no pick; ``SKIPPED`` does
  nothing (the items stay undecided until the Lead is next scored; items a merge moves to the
  survivor likewise stay undecided until the survivor is next scored, since nothing re-scores it
  on merge). An item is decided once, so a later rescore never picks again. The Enrollment, its
  ``draft_step`` job and the items' outcome commit with the score, or not at all. After the
  commit, and only when a pick happened, it logs ``signal_inbox_pick`` (``lead_id``,
  ``enrolled``, ``reason``).
- ``@use_case recompute_tiers() -> int`` (the daily ``recompute_tiers`` trigger's job): reads
  ``queries.scoring.retier_candidates()`` in one transaction, then calls
  ``scoring.retier(lead_id)`` for each in a transaction of its own, so one Lead's work never
  waits on another's. It logs ``tiers_recomputed`` (``leads``: the candidates, ``retiered``)
  and returns how many were re-tiered. Then, with a ``selection`` (Story 4.10), it calls
  ``selection.record_capacity()`` in a transaction of its own, so a day with no picks still
  gets its ``daily_capacity`` row and earlier days are finalised.
- ``@use_case request_rescore(lead_id) -> RescoreRequest`` (the Lead page's "Re-score"): in
  one transaction, enqueues ``score_lead`` on the interactive lane (``enqueue_score_lead``) and
  returns ``QUEUED``. It enqueues nothing and returns ``UNKNOWN`` for an unknown Lead (a text
  that isn't a ULID included), ``MERGED`` for a merged one and ``CLOSED`` for a ``lost`` or
  ``disqualified`` one, which the job would only skip. It keeps the Lead's stored Segment
  version (Story 11.5).
- ``@use_case request_rescore_current(lead_id) -> RescoreRequest`` (Story 11.5, the Lead page's
  "Re-score with the current definition"): the same, but the job applies the Segment's current
  definition (``use_current``).
- ``@use_case request_segment_rescore(segment_id) -> int`` (Story 11.5, the Segment page's
  "Re-score with the current definition"): in one transaction, enqueues a ``use_current``
  ``score_lead`` on the bulk lane for each open, unmerged Lead of the Segment
  (``queries.scoring.segment_leads``) and returns how many Leads it covered (an unknown
  Segment, or one with no such Lead, covers none). It logs ``segment_rescore_requested``
  (``segment_id``, ``leads``).

``enqueue_score_lead(queue, lead_id, lane=None, use_current=False)`` is this service's enqueue:
scope the Lead, discriminator ``tick`` (a live job absorbs a repeat), payload ``{v, lead_id}``;
with ``use_current`` the discriminator is ``current`` and the payload adds ``use_current:
true``, so a pending stored-version job never absorbs a move to the current version (nor the
other way round). The research
service (``people.py``, ``job_boards.py``) enqueues the same job with the same key and payload
inline (``nextron.domain.scoring.SCORE_LEAD_JOB``), at the end of the chain and after a
re-check closes or reopens a posting, because it can't import this module.
"""

import logging
from enum import StrEnum
from typing import Final, Protocol

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import scoring as scoring_queries
from nextron.app.scoring.api import RescoreOutcome, RescoreResult, Scoring
from nextron.app.scoring.selection import PickOutcome, Selection
from nextron.db.uow import current, use_case
from nextron.domain.crawl import LEAD_ID_FIELD, PAYLOAD_VERSION
from nextron.domain.ids import is_ulid
from nextron.domain.job import CURRENT, TICK, VERSION_FIELD, Lane
from nextron.domain.pipeline_stage import CLOSED_STAGES
from nextron.domain.scoring import SCORE_LEAD_JOB, USE_CURRENT_FIELD
from nextron.domain.signal_inbox_item import DRAFT_DISQUALIFIED, DRAFT_ENROLLED
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

LEAD_SCORED: Final = "lead_scored"
LEAD_DISQUALIFIED: Final = "lead_disqualified"
SCORE_SKIPPED: Final = "score_skipped"
TIERS_RECOMPUTED: Final = "tiers_recomputed"
SEGMENT_RESCORE_REQUESTED: Final = "segment_rescore_requested"
SIGNAL_INBOX_PICK: Final = "signal_inbox_pick"


class RescoreRequest(StrEnum):
    """What ``request_rescore`` did."""

    QUEUED = "queued"
    UNKNOWN = "unknown"
    MERGED = "merged"
    CLOSED = "closed"


def enqueue_score_lead(
    queue: JobQueue, lead_id: str, lane: Lane | None = None, *, use_current: bool = False
) -> None:
    """Enqueue the Lead's ``score_lead`` job in the caller's transaction; ``use_current`` makes
    it apply the Segment's current definition, under its own key."""
    payload: dict[str, object] = {VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: lead_id}
    if use_current:
        payload[USE_CURRENT_FIELD] = True
    queue.enqueue(
        SCORE_LEAD_JOB,
        scope=lead_id,
        discriminator=CURRENT if use_current else TICK,
        payload=payload,
        lane=lane,
    )


class DraftOutcomes(Protocol):
    """Where a Signal Inbox Lead's drafting decision is recorded (``Discovery``, Story 4.16)."""

    def record_draft_outcome(self, lead_id: str, outcome: str) -> int: ...


class LeadScores:
    """The scoring jobs and the Lead page's "Re-score"; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        scoring: Scoring,
        queue: JobQueue,
        *,
        selection: Selection | None = None,
        discovery: DraftOutcomes | None = None,
    ) -> None:
        self._clock = clock
        self._scoring = scoring
        self._queue = queue
        self._selection = selection
        self._discovery = discovery

    @use_case
    def score_lead(self, lead_id: str, *, use_current: bool = False) -> RescoreOutcome:
        """Score the Lead, then draft a waiting Signal Inbox Lead; see the module
        docstring."""
        with current().transaction():
            outcome = self._scoring.rescore(lead_id, use_current=use_current)
            picked = self._draft_when_ready(lead_id, outcome.result)
        if picked is not None:
            log.info(
                SIGNAL_INBOX_PICK,
                extra={
                    "lead_id": lead_id,
                    "enrolled": picked.enrollment_id is not None,
                    "reason": None if picked.reason is None else str(picked.reason),
                },
            )
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

    def _draft_when_ready(self, lead_id: str, result: RescoreResult) -> PickOutcome | None:
        """Story 4.16's hook, inside ``score_lead``'s transaction; the pick, if one ran."""
        if self._selection is None or self._discovery is None:
            return None
        if result is RescoreResult.SKIPPED:
            return None
        if not discovery_queries.undecided_draft_items(lead_id):
            return None
        if result is RescoreResult.DISQUALIFIED:
            self._discovery.record_draft_outcome(lead_id, DRAFT_DISQUALIFIED)
            return None
        picked = self._selection.pick_one(lead_id)
        decided = DRAFT_ENROLLED if picked.enrollment_id is not None else str(picked.reason)
        self._discovery.record_draft_outcome(lead_id, decided)
        return picked

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
        if self._selection is not None:
            with uow.transaction():
                self._selection.record_capacity()
        log.info(TIERS_RECOMPUTED, extra={"leads": len(candidates), "retiered": retiered})
        return retiered

    @use_case
    def request_rescore(self, lead_id: str) -> RescoreRequest:
        """Queue the Lead's ``score_lead`` job on the interactive lane, keeping its stored
        Segment version, unless the Lead is unknown, merged or closed; see the module
        docstring."""
        return self._request(lead_id, use_current=False)

    @use_case
    def request_rescore_current(self, lead_id: str) -> RescoreRequest:
        """Queue the Lead's ``score_lead`` job on the interactive lane with the Segment's
        current definition, unless the Lead is unknown, merged or closed."""
        return self._request(lead_id, use_current=True)

    def _request(self, lead_id: str, *, use_current: bool) -> RescoreRequest:
        with current().transaction():
            lead = discovery_queries.lead(lead_id)
            if lead is None:
                return RescoreRequest.UNKNOWN
            if lead.merged_into is not None:
                return RescoreRequest.MERGED
            if pipeline_queries.stage(lead.id) in CLOSED_STAGES:
                return RescoreRequest.CLOSED
            enqueue_score_lead(self._queue, lead.id, Lane.INTERACTIVE, use_current=use_current)
        return RescoreRequest.QUEUED

    @use_case
    def request_segment_rescore(self, segment_id: str) -> int:
        """Queue a current-definition ``score_lead`` on the bulk lane for each open, unmerged
        Lead of the Segment; how many Leads it covered."""
        with current().transaction():
            leads = scoring_queries.segment_leads(segment_id)
            for lead_id in leads:
                enqueue_score_lead(self._queue, lead_id, Lane.BULK, use_current=True)
        shown = segment_id if is_ulid(segment_id) else None  # IDs only, never other text
        log.info(SEGMENT_RESCORE_REQUESTED, extra={"segment_id": shown, "leads": len(leads)})
        return len(leads)
