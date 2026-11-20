"""Regenerating one ``ready`` Draft, and the live staleness of a Draft's revision (FR-21, FR-22,
AD-6, AD-7, AD-8, AD-16, AD-17, AD-18; Story 4.20), owned by ``drafting``.

**``Regenerations(clock, llm, queue).regenerate(draft_id, instruction=None) -> RegenerateResult``**
(``@use_case``) rewrites the Draft's current text at the Operator's request, directly (an
``interactive`` call with the server fallbacks; never batched, never budget-checked:
``draft_sequence`` is ``essential``), and appends a ``regenerated`` revision. Prepare
transaction, then the call with no transaction open, then the apply transaction (AD-2).

1. **Arguments**, before anything else: ``draft_id`` a ``str`` (``TypeError``); ``instruction``
   ``None`` or a ``str`` (``TypeError``), stripped, blank as none, more than 200 characters or
   any ``Cc``, ``Cf``, ``Zl`` or ``Zp`` character ``ValueError``
   (``step_drafting.check_instruction``).
2. **Prepare**, in order: an unknown Draft is ``unknown``; one not ``ready`` is ``not_ready``
   (its ``status``). A Draft with no revision: ``draft_failed_at`` set and no instruction clears
   ``draft_failed_at`` and ``draft_failed_reason`` (for Steps 2 to 4, on every ``ready`` Step
   2-4 Draft of the Enrollment with no revision: one job drafts them together) and enqueues the original drafting job on the
   interactive lane (Step 1 or a recontact: ``draft_step:<enrollment>:1``,
   ``api.enqueue_step_1_job``; Steps 2 to 4: ``generation.enqueue_follow_up_job``) and is
   ``retry_queued``, once the Contact passes the same checks as below (gone, erased or AD-16
   ``Blocked``: ``blocked``, nothing written; overnight decision 7); with an instruction it is
   ``needs_text``; with no failure ``in_progress``
   (its drafting is still running). A Draft that isn't a Step Draft of an Enrollment is
   ``failed`` / ``not_composable``. With a revision: the Contact gone or erased, or the AD-16
   gate (``compliance_api.assert_contactable(ContactSubject, "email", "cold")``) ``Blocked``, is
   ``blocked`` with no call; no active ``draft_sequence`` tuple whose prompt version is in
   ``step_drafting.REGENERATE_PROMPTS`` (``v3``) is ``failed`` / ``no_route`` with no call; a
   context that can't be read (the Enrollment, its Campaign, Sequence version, the Step's brief,
   Segment or Market) is ``failed`` / ``not_composable``.
3. **The call**, ``llm.run(tuple, blocks, Urgency.INTERACTIVE, framing=framing)``: the framing
   (``step_drafting.regenerate_framing``) is the Step's brief with its Segment x Market angle,
   the allowed facts, the Variant's note for that Step, the English variant and the masked
   instruction; the blocks (``step_drafting.regenerate_blocks``) the ``current_draft`` block,
   for a follow-up one ``earlier_step`` block per other Step Draft of the Enrollment with a
   current revision, then the Lead's blocks exactly as Step 1's (``generation.lead_inputs``).
   An ``AdapterError`` propagates; nothing is written.
4. **Apply**: the Contact gone, erased or blocked now is ``blocked`` (first: a suppression
   also stops the Draft); the Draft no longer ``ready`` or its ``current_rev`` moved is
   ``changed``; a result that isn't ``LlmOk`` is
   ``failed`` with ``generation.FAILURES``' reason. Step 1 is validated as Story 4.15
   (``read_step_1``, ``validate_proposal``, ``api._check_text``); a follow-up needs exactly one
   ``steps`` entry for its Step (``read_follow_up``), claims citing the inputs and an input angle
   fact whose id no other Step's current revision cites, a ``regulation`` one when its brief has
   an angle for the Campaign's Segment x Market (``validate_follow_up``), and
   ``api._check_text`` with Step 1's current subject; else ``failed`` / ``invalid_proposal``. A
   failure keeps the current revision and never writes ``draft_failed_*``. Success:
   ``Drafting._append(origin regenerated, Traceability(the claims' citations, the cited Signal
   (Step 1), the serving tuple, its Playbook and prompt version, the stripped instruction),
   claims, urgency interactive)`` (a follow-up's subject is Step 1's current subject, its
   citations as Story 4.18's) is ``regenerated`` with the new ``rev``. Revision 1 is never
   touched. A ``ValueError``, ``LookupError`` or ``InvalidMarkets`` from the append is
   ``failed`` / ``not_composable``.

It logs ``draft_regenerated`` (``draft_id``, ``rev``, ``served_model``),
``draft_regenerate_failed`` (``draft_id``, ``reason``), ``draft_regenerate_blocked``
(``draft_id``) and ``draft_retry_queued`` (``draft_id``); never the instruction, the prompt or
the answer, and no job payload holds the instruction.

**``stale_reason(draft, now) -> StaleReason | None``** (``Regenerations.stale(draft)`` at the
clock's now) reads what the Draft's current revision
cites as it stands now (each cited Evidence row, each cited fact's current version and whether
the cited version is allowed for the Campaign's Segment x Market, the cited Signal retracted or
not) and asks ``nextron.domain.grounding.is_stale``; ``None`` without a revision or Enrollment.
When the allowed set can't be read (the Campaign is gone) every cited fact counts as not allowed
(fail closed).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from nextron.app.compliance import api as compliance_api
from nextron.app.drafting.api import Drafting, Traceability, _check_text, enqueue_step_1_job
from nextron.app.drafting.generation import (
    CHANNEL,
    FAILURES,
    PURPOSE,
    allowed_versions,
    campaign_of,
    enqueue_follow_up_job,
    lead_inputs,
)
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.drafting import DraftView, RevisionView
from nextron.app.queries.sequencing import StoredEnrollment
from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.db.uow import current, use_case
from nextron.domain.ai import Framing, RouteTuple, UntrustedBlock, Urgency
from nextron.domain.company_facts import FactType
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import STEP_KINDS, DraftStatus, RevisionOrigin
from nextron.domain.grounding import (
    CitedEvent,
    CitedEvidence,
    CitedFact,
    StaleInput,
    StaleReason,
    is_stale,
)
from nextron.domain.grounding_rules import Claim
from nextron.domain.job import Lane
from nextron.domain.markets import InvalidMarkets
from nextron.domain.step_drafting import (
    DRAFT_SEQUENCE_ROUTE,
    FIRST_STEP,
    REGENERATE_PROMPTS,
    DraftFact,
    DraftFailure,
    RegenStep,
    check_instruction,
    citations_of,
    claims_of,
    follow_up_citations,
    follow_up_claims,
    read_follow_up,
    read_step_1,
    regenerate_blocks,
    regenerate_framing,
    regulation_steps,
    validate_follow_up,
    validate_proposal,
)
from nextron.ports.clock import Clock
from nextron.ports.llm import LlmOk, LlmPort, LlmResult

log = logging.getLogger(__name__)

DRAFT_REGENERATED: Final = "draft_regenerated"
DRAFT_REGENERATE_FAILED: Final = "draft_regenerate_failed"
DRAFT_REGENERATE_BLOCKED: Final = "draft_regenerate_blocked"
DRAFT_RETRY_QUEUED: Final = "draft_retry_queued"


class RegenerateOutcome(StrEnum):
    """What one ``regenerate`` did; see the module docstring."""

    REGENERATED = "regenerated"  # a regenerated revision was appended
    RETRY_QUEUED = "retry_queued"  # a failed Draft's original drafting job was queued again
    UNKNOWN = "unknown"  # no such Draft
    NOT_READY = "not_ready"  # the Draft isn't ready
    NEEDS_TEXT = "needs_text"  # an instruction for a Draft with no text yet
    IN_PROGRESS = "in_progress"  # no text yet and no failure: its drafting is still running
    BLOCKED = "blocked"  # AD-16 blocked the Contact, or it is gone or erased
    FAILED = "failed"  # nothing written; see ``failure``
    CHANGED = "changed"  # the Draft changed during the call: nothing written


@dataclass(frozen=True, slots=True)
class RegenerateResult:
    """What ``regenerate`` answers."""

    outcome: RegenerateOutcome
    rev: int | None = None
    status: str | None = None
    failure: DraftFailure | None = None


@dataclass(frozen=True, slots=True)
class _Call:
    """What the prepare transaction read for the call."""

    draft_id: str
    rev: int
    step: int
    contact_id: str
    route_tuple: RouteTuple
    tuple_id: str
    blocks: tuple[UntrustedBlock, ...]
    framing: Framing
    instruction: str | None
    evidence_ids: frozenset[str]
    facts: frozenset[tuple[str, int]]
    live_events: frozenset[str]
    step_1_subject: str
    other_fact_ids: frozenset[str]
    needs_regulation: bool
    regulation_facts: frozenset[tuple[str, int]]


def _failed(draft_id: str, reason: DraftFailure) -> RegenerateResult:
    log.info(DRAFT_REGENERATE_FAILED, extra={"draft_id": draft_id, "reason": reason.value})
    return RegenerateResult(RegenerateOutcome.FAILED, failure=reason)


def _blocked(draft_id: str) -> RegenerateResult:
    log.info(DRAFT_REGENERATE_BLOCKED, extra={"draft_id": draft_id})
    return RegenerateResult(RegenerateOutcome.BLOCKED)


def _contactable(contact_id: str) -> bool:
    """Whether the Contact exists, isn't erased and AD-16 allows it (email, cold)."""
    contact = research_queries.contact(contact_id)
    if contact is None or contact.erased_at is not None:
        return False
    answer = compliance_api.assert_contactable(ContactSubject(contact.id), CHANNEL, PURPOSE)
    return not isinstance(answer, Blocked)


def _others(draft: DraftView, enrollment_id: str) -> tuple[tuple[int, RevisionView], ...]:
    """The Enrollment's other Step Drafts' Step numbers and current revisions, by Step."""
    found = []
    for other in drafting_queries.drafts_of(enrollment_id):
        if other.id == draft.id or other.kind not in STEP_KINDS or other.step_no is None:
            continue
        revision = drafting_queries.current_revision(other)
        if revision is not None:
            found.append((other.step_no, revision))
    return tuple(sorted(found, key=lambda pair: pair[0]))


class Regenerations:
    """The Operator's regenerate; see the module docstring."""

    def __init__(self, clock: Clock, llm: LlmPort, queue: JobQueue) -> None:
        self._clock = clock
        self._llm = llm
        self._queue = queue

    @use_case
    def regenerate(self, draft_id: str, instruction: str | None = None) -> RegenerateResult:
        """Regenerate the Draft's text, or retry its failed drafting; see the module
        docstring."""
        if not isinstance(draft_id, str):
            raise TypeError("regenerate takes the Draft id as str")
        chosen = check_instruction(instruction)
        uow = current()
        with uow.transaction():
            prepared = self._prepare(draft_id, chosen)
        if not isinstance(prepared, _Call):
            return prepared
        result = self._llm.run(
            prepared.route_tuple,
            prepared.blocks,
            Urgency.INTERACTIVE,
            framing=prepared.framing,
        )  # AdapterError propagates: nothing is stored
        with uow.transaction():
            return self._apply(prepared, result)

    def stale(self, draft: DraftView) -> StaleReason | None:
        """``stale_reason(draft, now)`` at the clock's now, in the caller's unit of work (the
        Lead page's live badge)."""
        return stale_reason(draft, self._clock.now())

    # Prepare ------------------------------------------------------------------------------------

    def _prepare(self, draft_id: str, instruction: str | None) -> _Call | RegenerateResult:
        draft = drafting_queries.draft(draft_id)
        if draft is None:
            return RegenerateResult(RegenerateOutcome.UNKNOWN)
        if draft.status is not DraftStatus.READY:
            return RegenerateResult(RegenerateOutcome.NOT_READY, status=str(draft.status))
        if draft.kind not in STEP_KINDS or draft.step_no is None or draft.enrollment_id is None:
            return _failed(draft.id, DraftFailure.NOT_COMPOSABLE)
        current_text = drafting_queries.current_revision(draft)
        if current_text is None:
            return self._retry(draft, draft.enrollment_id, draft.step_no, instruction)
        enrollment = sequencing_queries.enrollment(draft.enrollment_id)
        if enrollment is None:
            return _failed(draft.id, DraftFailure.NOT_COMPOSABLE)
        if not _contactable(enrollment.contact_id):  # AD-16: no AI call
            return _blocked(draft.id)
        route_tuple = playbook_queries.active_tuple(DRAFT_SEQUENCE_ROUTE)
        tuple_id = playbook_queries.active_tuple_id(DRAFT_SEQUENCE_ROUTE)
        if (
            route_tuple is None
            or tuple_id is None
            or route_tuple.prompt_version not in REGENERATE_PROMPTS
        ):
            return _failed(draft.id, DraftFailure.NO_ROUTE)
        try:
            return self._call(
                draft, current_text, enrollment, route_tuple, tuple_id, instruction
            )
        except LookupError:
            return _failed(draft.id, DraftFailure.NOT_COMPOSABLE)

    def _retry(
        self, draft: DraftView, enrollment_id: str, step: int, instruction: str | None
    ) -> RegenerateResult:
        """A Draft with no revision: re-queue a failed drafting, else say why not."""
        if draft.draft_failed_at is None:
            return RegenerateResult(RegenerateOutcome.IN_PROGRESS)
        if instruction is not None:
            return RegenerateResult(RegenerateOutcome.NEEDS_TEXT)
        enrollment = sequencing_queries.enrollment(enrollment_id)
        if enrollment is None:
            return _failed(draft.id, DraftFailure.NOT_COMPOSABLE)
        if not _contactable(enrollment.contact_id):  # AD-16: no drafting job for a blocked one
            return _blocked(draft.id)
        now = self._clock.now()
        if step == FIRST_STEP:
            drafts_repo.clear_draft_failed(draft.id, now)
        else:  # one job drafts Steps 2-4 together: clear every open follow-up's failure
            for other in drafting_queries.drafts_of(enrollment_id):
                if (
                    other.kind in STEP_KINDS
                    and other.step_no is not None
                    and other.step_no > FIRST_STEP
                    and other.status is DraftStatus.READY
                    and drafting_queries.current_revision(other) is None
                ):
                    drafts_repo.clear_draft_failed(other.id, now)
        if step == FIRST_STEP:
            enqueue_step_1_job(self._queue, enrollment_id, Lane.INTERACTIVE)
        else:
            enqueue_follow_up_job(self._queue, enrollment_id)
        log.info(DRAFT_RETRY_QUEUED, extra={"draft_id": draft.id})
        return RegenerateResult(RegenerateOutcome.RETRY_QUEUED)

    def _call(
        self,
        draft: DraftView,
        current_text: RevisionView,
        enrollment: StoredEnrollment,
        route_tuple: RouteTuple,
        tuple_id: str,
        instruction: str | None,
    ) -> _Call:
        """The call's inputs; ``LookupError`` when its context is gone."""
        step = draft.step_no or FIRST_STEP
        campaign = campaign_of(enrollment)
        sequence = setup_queries.sequence_by_id(enrollment.sequence_id)
        if sequence is None or sequence.version != enrollment.sequence_version:
            raise LookupError("the Enrollment's Sequence version isn't stored")
        steps = sequence.definition.steps
        if not FIRST_STEP <= step <= len(steps):
            raise LookupError("the Draft's Step has no brief")
        brief = steps[step - 1]
        segment = setup_queries.segment_by_id(campaign.segment_id)
        market = setup_queries.market_by_id(campaign.market_id)
        if segment is None or market is None:
            raise LookupError("the Campaign's Segment or Market is unknown")
        contact = research_queries.contact(enrollment.contact_id)
        if contact is None:
            raise LookupError("the Enrollment's Contact is unknown")
        variant = next((v for v in campaign.variants if v.id == enrollment.variant_id), None)
        note = (
            None
            if variant is None
            else next((angle.note for angle in variant.step_angles if angle.step == step), None)
        )
        allowed = playbook_queries.allowed_facts(campaign.segment_id, campaign.market_id)
        others = _others(draft, enrollment.id)
        lead = lead_inputs(enrollment.lead_id, contact, self._clock.now())
        first = next(
            (
                other
                for other in drafting_queries.drafts_of(enrollment.id)
                if other.kind in STEP_KINDS and other.step_no == FIRST_STEP
            ),
            None,
        )
        first_text = None if first is None else drafting_queries.current_revision(first)
        return _Call(
            draft_id=draft.id,
            rev=current_text.rev,
            step=step,
            contact_id=contact.id,
            route_tuple=route_tuple,
            tuple_id=tuple_id,
            blocks=tuple(
                regenerate_blocks(
                    lead.subject_key,
                    step,
                    current_text.subject,
                    current_text.body,
                    tuple(
                        RegenStep(number, other.subject, other.body) for number, other in others
                    ),
                    lead.evidence,
                    lead.signals,
                    lead.contact,
                )
            ),
            framing=regenerate_framing(
                brief,
                segment.key,
                market.code,
                tuple(
                    DraftFact(found.fact_id, found.version, found.content.wording)
                    for found in allowed
                ),
                note,
                market.definition.english_variant,
                instruction,
            ),
            instruction=instruction,
            evidence_ids=lead.evidence_ids,
            facts=frozenset((found.fact_id, found.version) for found in allowed),
            live_events=lead.live_events,
            step_1_subject=current_text.subject if first_text is None else first_text.subject,
            other_fact_ids=frozenset(
                fact_id for _, other in others for fact_id, _ in other.fact_versions
            ),
            needs_regulation=bool(regulation_steps((brief,), segment.key, market.code)),
            regulation_facts=frozenset(
                (found.fact_id, found.version)
                for found in allowed
                if found.content.type == FactType.REGULATION
            ),
        )

    # Apply --------------------------------------------------------------------------------------

    def _apply(self, call: _Call, result: LlmResult) -> RegenerateResult:
        if not _contactable(call.contact_id):  # suppressed or erased during the call
            return _blocked(call.draft_id)
        draft = drafting_queries.draft(call.draft_id)
        if draft is None or draft.status is not DraftStatus.READY or draft.current_rev != call.rev:
            return RegenerateResult(RegenerateOutcome.CHANGED)
        if not isinstance(result, LlmOk):
            return _failed(draft.id, FAILURES.get(type(result), DraftFailure.FAILED))
        written = (
            self._step_1(draft, call, result.parsed)
            if call.step == FIRST_STEP
            else self._follow_up(draft, call, result.parsed)
        )
        if isinstance(written, RegenerateResult):
            return written
        log.info(
            DRAFT_REGENERATED,
            extra={"draft_id": draft.id, "rev": written, "served_model": result.model},
        )
        return RegenerateResult(RegenerateOutcome.REGENERATED, rev=written)

    def _traceability(
        self,
        call: _Call,
        evidence_ids: tuple[str, ...],
        fact_versions: tuple[tuple[str, int], ...],
        cited_signal_id: str | None,
    ) -> Traceability:
        route_tuple = call.route_tuple
        return Traceability(
            evidence_ids=evidence_ids,
            fact_versions=fact_versions,
            cited_signal_id=cited_signal_id,
            route_tuple_id=call.tuple_id,
            playbook_sha256=route_tuple.playbook_sha256,
            playbook_version=None if route_tuple.playbook is None else route_tuple.playbook.version,
            prompt_version=route_tuple.prompt_version,
            instruction=call.instruction,
        )

    def _write(
        self,
        draft: DraftView,
        subject: str,
        body: str,
        traceability: Traceability,
        claims: tuple[Claim, ...],
    ) -> int | RegenerateResult:
        try:
            return Drafting(self._clock, self._queue)._append(
                draft,
                subject,
                body,
                RevisionOrigin.REGENERATED,
                traceability,
                self._clock.now(),
                claims=claims,
                urgency=Urgency.INTERACTIVE,
            )
        except (ValueError, LookupError, InvalidMarkets):  # raised before any write
            return _failed(draft.id, DraftFailure.NOT_COMPOSABLE)

    def _step_1(
        self, draft: DraftView, call: _Call, parsed: Mapping[str, Any]
    ) -> int | RegenerateResult:
        proposal = read_step_1(parsed, call.route_tuple.schema_version)
        if proposal is None:
            return _failed(draft.id, DraftFailure.INVALID_PROPOSAL)
        subject, body = proposal.subject.strip(), proposal.body.strip()
        valid = validate_proposal(
            proposal,
            evidence_ids=call.evidence_ids,
            facts=call.facts,
            live_events=call.live_events,
        )
        if valid:
            try:
                _check_text(subject, body)
            except ValueError:
                valid = False
        if not valid:
            return _failed(draft.id, DraftFailure.INVALID_PROPOSAL)
        evidence_ids, fact_versions = citations_of(proposal)
        return self._write(
            draft,
            subject,
            body,
            self._traceability(call, evidence_ids, fact_versions, proposal.cited_signal_id),
            claims_of(proposal),
        )

    def _follow_up(
        self, draft: DraftView, call: _Call, parsed: Mapping[str, Any]
    ) -> int | RegenerateResult:
        found = read_follow_up(parsed, call.step)
        if found is None:
            return _failed(draft.id, DraftFailure.INVALID_PROPOSAL)
        body = found.body.strip()
        valid = validate_follow_up(
            found,
            evidence_ids=call.evidence_ids,
            facts=call.facts,
            other_fact_ids=call.other_fact_ids,
            needs_regulation=call.needs_regulation,
            regulation_facts=call.regulation_facts,
        )
        if valid:
            try:
                _check_text(call.step_1_subject, body)
            except ValueError:
                valid = False
        if not valid:
            return _failed(draft.id, DraftFailure.INVALID_PROPOSAL)
        evidence_ids, fact_versions = follow_up_citations(found)
        return self._write(
            draft,
            call.step_1_subject,  # a follow-up replies in Step 1's thread
            body,
            self._traceability(call, evidence_ids, fact_versions, None),
            follow_up_claims(found),
        )


# Live staleness (AD-7) --------------------------------------------------------------------------


def stale_reason(draft: DraftView, now: datetime) -> StaleReason | None:
    """Whether the Draft's current revision is stale at ``now``, by
    ``nextron.domain.grounding.is_stale``; see the module docstring."""
    revision = drafting_queries.current_revision(draft)
    if revision is None or draft.enrollment_id is None:
        return None
    enrollment = sequencing_queries.enrollment(draft.enrollment_id)
    if enrollment is None:
        return None
    lead_id = enrollment.lead_id
    rows = {row.id: row for row in research_queries.evidence_history(lead_id)}
    evidence = tuple(
        CitedEvidence(evidence_id, None, None, missing=True)
        if (row := rows.get(evidence_id)) is None
        else CitedEvidence(evidence_id, row.superseded_by, row.retracted_at)
        for evidence_id in revision.evidence_ids
    )
    try:
        allowed: frozenset[tuple[str, int]] | None = allowed_versions(enrollment)
    except LookupError:  # the Campaign is gone: fail closed, no cited fact counts as allowed
        allowed = None
    facts = []
    for fact_id, version in revision.fact_versions:
        history = playbook_queries.fact(fact_id)
        current_version = (
            None if history is None or history.current is None else history.current.version
        )
        facts.append(
            CitedFact(
                fact_id,
                version,
                current_version,
                allowed is not None and (fact_id, version) in allowed,
            )
        )
    event = None
    if revision.cited_signal_id is not None:
        signal = research_queries.signal_of(lead_id, revision.cited_signal_id)
        if signal is not None:
            event = CitedEvent(
                signal.id,
                signal.signal_class,
                signal.tier,
                signal.event_date,
                signal.first_seen_at,
                signal.retracted_at,
            )
    return is_stale(
        StaleInput(tuple(evidence), tuple(facts), revision.cited_signal_id, event), now
    )

