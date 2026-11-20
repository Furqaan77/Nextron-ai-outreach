"""Drafting Step 1 with the ``draft_sequence`` route (FR-20, FR-21, AD-6, AD-7, AD-8, AD-16,
AD-17, AD-18; Story 4.15): the ``draft_step`` job's use case, owned by ``drafting``.

**``StepDrafts(clock, llm, queue).draft(enrollment_id, step, urgency) -> StepDraftOutcome``**
(``@use_case``) writes revision 1 of the Enrollment's Step 1 Draft, which ``Drafting.
request_steps`` enqueued with the slot. It never calls ``nextron.domain.budget.allow``:
``draft_sequence`` is ``essential``. Prepare transaction, then the call with no transaction
open, then the apply transaction (AD-2). ``enrollment_id`` must be a ``str`` and ``step`` an
``int`` (``TypeError``), ``urgency`` an ``Urgency``.

1. **Prepare.** A Step other than 1 (Steps 2 to 4 are Story 4.18's), an unknown Enrollment, or
   a Step 1 slot (kind ``step`` or ``recontact``) that is missing, not ``ready`` or already has
   a revision is a no-op (``skipped``; a re-run job calls nothing). A Contact that is missing or
   erased is ``skipped`` too, with no call.
2. **The gate** (AD-16): ``compliance_api.assert_contactable(ContactSubject(contact), "email",
   "cold")``. ``Blocked``: no call and no revision; one ``events`` row (entity ``drafts``, the
   slot, ``ready`` to ``drafting_blocked``, cause the first reason code, ``cause_ref`` the
   Enrollment, actor ``system:drafting``) and ``blocked`` (decision 6: no ``draft_failed_at``;
   the suppression path stops the Enrollment).
2a. **Generic copy** (Story 4.19, FR-22): when ``fallback.generic_copy(enrollment, now)`` (the
   Lead is Tier C at the clock's now), ``fallback.write_generic(draft, enrollment, contact, now,
   urgency, queue=queue)`` fills the Segment's fallback template and stores revision 1 (origin
   ``generated``, no Evidence or Signal, ``drafts.generic`` set) in this prepare transaction;
   its outcome (``drafted``, ``failed`` with ``not_composable``, or ``blocked``) is returned.
   No ``llm.run`` call is made and no active ``draft_sequence`` tuple is needed.
3. **The route.** No active ``draft_sequence`` tuple (``queries.playbook.active_tuple`` and
   ``active_tuple_id``): ``draft_failed`` with ``no_route`` and no call. A context that can't be
   read (the Enrollment's Campaign, Sequence, Segment or Market gone, its Sequence's stored
   version not the Enrollment's ``sequence_version``, or its Sequence without a Step 1 brief) is
   ``draft_failed`` with ``not_composable``, with no call.
4. **The call**, ``llm.run(tuple, blocks, urgency, framing=framing)``. The framing
   (``nextron.domain.step_drafting.framing``): the Step 1 brief of the Enrollment's Sequence
   with the Campaign's Segment × Market angle; the facts ``queries.playbook.allowed_facts``
   allows for the Campaign's Segment and Market (fact id, version, wording); the note: the
   Variant's Step 1 angle note (if any) and the Campaign Market's English variant. The blocks
   (``step_drafting.input_blocks``, subject ``lead:<lead id>``): the Lead's live Evidence (text
   as ``grounding._evidence_text``), its current Signals with their live Evidence ids, and the
   Contact's name and role. Every text is masked (``[email]``, ``[phone]``); no Contact email
   enters the prompt. An ``AdapterError`` propagates (the runner retries; nothing is stored,
   decision 3). ``LlmRefused``, ``LlmTruncated`` (after the gateway's one doubled retry),
   ``LlmInvalid`` and ``LlmFailed`` are ``draft_failed`` with ``refused``, ``truncated``,
   ``invalid`` and ``failed``.
5. **Validate** (``step_drafting.validate_proposal``, then ``api._check_text`` on the stripped
   subject and body): every claim cites exactly one input Evidence id or input ``(fact_id,
   version)``, and ``cited_signal_id`` is a live Event Signal of the Lead
   (``domain.signals.is_live`` at the clock's now, class ``event``); else the proposal is
   discarded and ``draft_failed`` with ``invalid_proposal``.
6. **Apply.** Re-read: a slot gone, not ``ready`` or with a revision is a no-op (``skipped``); a
   Contact erased, gone or no longer contactable stores nothing (``skipped``, logged).
   Otherwise ``Drafting._append(draft, subject, body, GENERATED, Traceability(the Evidence ids
   and ``(fact_id, version)`` pairs the claims cite, in first-appearance order; the cited
   Signal; the serving tuple id; its Playbook SHA-256 and version; its prompt version), now,
   claims=the claims, urgency=urgency, generic=False)``, which stores revision 1 with its grounding result and
   enqueues its ``check_grounding`` job in this unit. A ``ValueError``, ``LookupError`` or
   ``InvalidMarkets`` from it is ``draft_failed`` with ``not_composable``. Success clears
   ``draft_failed_at`` and ``draft_failed_reason`` (``drafted``).

``draft_failed`` writes ``drafts.draft_failed_at`` (now) and ``draft_failed_reason``; no
revision is stored. It logs ``step_drafted`` (``draft_id``, ``rev``, ``served_model``),
``step_draft_failed`` (``draft_id``, ``reason``), ``step_draft_blocked`` (``draft_id``) or
``step_draft_skipped`` (``enrollment_id``, ``reason``). No prompt or answer text is stored or
logged.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.drafting.api import ACTOR, ENTITY, Drafting, Traceability, _check_text
from nextron.app.drafting.fallback import generic_copy, write_generic
from nextron.app.drafting.grounding import _evidence_text
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.drafting import DraftView
from nextron.app.queries.sequencing import StoredEnrollment
from nextron.db import eventlog
from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.db.uow import current, use_case
from nextron.domain.ai import Framing, RouteTuple, UntrustedBlock, Urgency, check_urgency
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import STEP_KINDS, DraftStatus, RevisionOrigin
from nextron.domain.markets import InvalidMarkets
from nextron.domain.segments import SignalClass
from nextron.domain.signals import age_days, is_live
from nextron.domain.step_drafting import (
    DRAFT_SEQUENCE_ROUTE,
    DraftContact,
    DraftEvidence,
    DraftFact,
    DraftFailure,
    DraftSignal,
    citations_of,
    claims_of,
    framing,
    input_blocks,
    read_proposal,
    validate_proposal,
)
from nextron.ports.clock import Clock
from nextron.ports.llm import (
    LlmFailed,
    LlmInvalid,
    LlmOk,
    LlmPort,
    LlmRefused,
    LlmResult,
    LlmTruncated,
)

log = logging.getLogger(__name__)

STEP_DRAFTED: Final = "step_drafted"
STEP_DRAFT_FAILED: Final = "step_draft_failed"
STEP_DRAFT_BLOCKED: Final = "step_draft_blocked"
STEP_DRAFT_SKIPPED: Final = "step_draft_skipped"
DRAFTING_BLOCKED: Final = "drafting_blocked"
CHANNEL: Final = "email"
PURPOSE: Final = "cold"
FIRST_STEP: Final = 1

FAILURES: Final = {
    LlmRefused: DraftFailure.REFUSED,
    LlmTruncated: DraftFailure.TRUNCATED,
    LlmInvalid: DraftFailure.INVALID,
    LlmFailed: DraftFailure.FAILED,
}


class StepDraftOutcome(StrEnum):
    """What one ``draft`` did."""

    DRAFTED = "drafted"  # revision 1 was stored
    FAILED = "failed"  # draft_failed_at and its reason were recorded
    BLOCKED = "blocked"  # AD-16 blocked the subject: an events row, nothing else
    SKIPPED = "skipped"  # nothing to do


@dataclass(frozen=True, slots=True)
class _Call:
    """What the prepare transaction read for the call."""

    draft_id: str
    enrollment_id: str
    contact_id: str
    route_tuple: RouteTuple
    tuple_id: str
    blocks: list[UntrustedBlock]
    framing: Framing
    evidence_ids: frozenset[str]
    facts: frozenset[tuple[str, int]]
    live_events: frozenset[str]


def _slot(enrollment_id: str) -> DraftView | None:
    """The Enrollment's Step 1 slot, or ``None``."""
    return next(
        (
            draft
            for draft in drafting_queries.drafts_of(enrollment_id)
            if draft.kind in STEP_KINDS and draft.step_no == FIRST_STEP
        ),
        None,
    )


def _open(draft: DraftView | None) -> bool:
    """Whether ``draft`` still waits for its first revision."""
    return draft is not None and draft.status is DraftStatus.READY and draft.current_rev is None


class StepDrafts:
    """The ``draft_step`` jobs; see the module docstring."""

    def __init__(self, clock: Clock, llm: LlmPort, queue: JobQueue) -> None:
        self._clock = clock
        self._llm = llm
        self._queue = queue

    @use_case
    def draft(self, enrollment_id: str, step: int, urgency: Urgency) -> StepDraftOutcome:
        """Draft the Enrollment's Step ``step`` and store revision 1; see the module
        docstring."""
        if not isinstance(enrollment_id, str):
            raise TypeError("draft takes the Enrollment id as str")
        if not isinstance(step, int) or isinstance(step, bool):
            raise TypeError("draft takes the Step number as int")
        check_urgency(urgency)
        uow = current()
        with uow.transaction():
            prepared = self._prepare(enrollment_id, step, urgency)
        if not isinstance(prepared, _Call):
            return prepared
        result = self._llm.run(
            prepared.route_tuple, prepared.blocks, urgency, framing=prepared.framing
        )  # AdapterError propagates: the runner retries, nothing is stored
        with uow.transaction():
            return self._apply(prepared, result, urgency)

    # Prepare ------------------------------------------------------------------------------------

    def _skip(self, enrollment_id: str, reason: str) -> StepDraftOutcome:
        log.info(STEP_DRAFT_SKIPPED, extra={"enrollment_id": enrollment_id, "reason": reason})
        return StepDraftOutcome.SKIPPED

    def _fail(self, draft_id: str, reason: DraftFailure) -> StepDraftOutcome:
        drafts_repo.mark_draft_failed(draft_id, reason.value, self._clock.now())
        log.info(STEP_DRAFT_FAILED, extra={"draft_id": draft_id, "reason": reason.value})
        return StepDraftOutcome.FAILED

    def _prepare(
        self, enrollment_id: str, step: int, urgency: Urgency
    ) -> _Call | StepDraftOutcome:
        if step != FIRST_STEP:
            return self._skip(enrollment_id, "unsupported_step")
        enrollment = sequencing_queries.enrollment(enrollment_id)
        if enrollment is None:
            return self._skip(enrollment_id, "unknown_enrollment")
        draft = _slot(enrollment.id)
        if draft is None or not _open(draft):
            return self._skip(enrollment_id, "nothing_to_draft")
        contact = research_queries.contact(enrollment.contact_id)
        if contact is None or contact.erased_at is not None:
            return self._skip(enrollment_id, "not_contactable")
        answer = compliance_api.assert_contactable(
            ContactSubject(contact.id), CHANNEL, PURPOSE
        )  # AD-16: no AI call for a subject that may not be reached
        if isinstance(answer, Blocked):
            eventlog.append(
                entity=ENTITY,
                entity_id=draft.id,
                from_status=str(DraftStatus.READY),
                to_status=DRAFTING_BLOCKED,
                cause=str(answer.reasons[0]),
                cause_ref=enrollment.id,
                actor=ACTOR,
                at=self._clock.now(),
            )
            log.info(STEP_DRAFT_BLOCKED, extra={"draft_id": draft.id})
            return StepDraftOutcome.BLOCKED
        now = self._clock.now()
        if generic_copy(enrollment, now):  # Story 4.19: Tier C, no Signal to cite, no AI call
            outcome = write_generic(draft, enrollment, contact, now, urgency, queue=self._queue)
            return StepDraftOutcome(outcome.value)
        route_tuple = playbook_queries.active_tuple(DRAFT_SEQUENCE_ROUTE)
        tuple_id = playbook_queries.active_tuple_id(DRAFT_SEQUENCE_ROUTE)
        if route_tuple is None or tuple_id is None:
            return self._fail(draft.id, DraftFailure.NO_ROUTE)
        try:
            call_framing = self._framing(enrollment)
        except LookupError:
            return self._fail(draft.id, DraftFailure.NOT_COMPOSABLE)
        lead_id = enrollment.lead_id
        evidence = research_queries.evidence(lead_id)
        linked = research_queries.signal_evidence_ids(lead_id)
        signals = research_queries.signals(lead_id)
        live_events = frozenset(
            signal.id
            for signal in signals
            if signal.signal_class == SignalClass.EVENT
            and is_live(
                SignalClass.EVENT,
                signal.tier,
                age_days(signal.event_date, signal.first_seen_at, now),
            )
        )
        blocks = input_blocks(
            f"lead:{lead_id}",
            (DraftEvidence(row.id, row.type, _evidence_text(row)) for row in evidence),
            (
                DraftSignal(
                    signal.id,
                    signal.signal_type,
                    signal.signal_class,
                    signal.tier,
                    signal.event_date,
                    linked.get(signal.id, ()),
                )
                for signal in signals
            ),
            DraftContact(contact.name, contact.role),
        )
        return _Call(
            draft_id=draft.id,
            enrollment_id=enrollment.id,
            contact_id=contact.id,
            route_tuple=route_tuple,
            tuple_id=tuple_id,
            blocks=blocks,
            framing=call_framing,
            evidence_ids=frozenset(row.id for row in evidence),
            facts=self._allowed(enrollment),
            live_events=live_events,
        )

    @staticmethod
    def _campaign(enrollment: StoredEnrollment) -> setup_queries.SeededCampaign:
        campaign = setup_queries.campaign(enrollment.campaign_id)
        if campaign is None:
            raise LookupError("the Enrollment's Campaign is unknown")
        return campaign

    def _allowed(self, enrollment: StoredEnrollment) -> frozenset[tuple[str, int]]:
        campaign = self._campaign(enrollment)
        return frozenset(
            (found.fact_id, found.version)
            for found in playbook_queries.allowed_facts(campaign.segment_id, campaign.market_id)
        )

    def _framing(self, enrollment: StoredEnrollment) -> Framing:
        """The call's framing; ``LookupError`` when its context is gone."""
        campaign = self._campaign(enrollment)
        sequence = setup_queries.sequence_by_id(enrollment.sequence_id)
        if sequence is None or sequence.version != enrollment.sequence_version:
            raise LookupError("the Enrollment's Sequence version isn't stored")
        if not sequence.definition.steps:
            raise LookupError("the Enrollment's Sequence has no Step 1 brief")
        segment = setup_queries.segment_by_id(campaign.segment_id)
        market = setup_queries.market_by_id(campaign.market_id)
        if segment is None or market is None:
            raise LookupError("the Campaign's Segment or Market is unknown")
        variant = next((v for v in campaign.variants if v.id == enrollment.variant_id), None)
        note = (
            None
            if variant is None
            else next(
                (angle.note for angle in variant.step_angles if angle.step == FIRST_STEP), None
            )
        )
        facts = tuple(
            DraftFact(found.fact_id, found.version, found.content.wording)
            for found in playbook_queries.allowed_facts(campaign.segment_id, campaign.market_id)
        )
        return framing(
            sequence.definition.steps[0],
            segment.key,
            market.code,
            facts,
            note,
            market.definition.english_variant,
        )

    # Apply --------------------------------------------------------------------------------------

    def _apply(self, call: _Call, result: LlmResult, urgency: Urgency) -> StepDraftOutcome:
        draft = drafting_queries.draft(call.draft_id)
        if draft is None or not _open(draft):  # drafted or moved meanwhile
            return self._skip(call.enrollment_id, "nothing_to_draft")
        contact = research_queries.contact(call.contact_id)
        if contact is None or contact.erased_at is not None:
            return self._skip(call.enrollment_id, "not_contactable")
        answer = compliance_api.assert_contactable(ContactSubject(contact.id), CHANNEL, PURPOSE)
        if isinstance(answer, Blocked):  # suppressed during the call: store nothing
            return self._skip(call.enrollment_id, "not_contactable")
        if not isinstance(result, LlmOk):
            return self._fail(draft.id, FAILURES.get(type(result), DraftFailure.FAILED))
        proposal = read_proposal(result.parsed)
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
            return self._fail(draft.id, DraftFailure.INVALID_PROPOSAL)
        evidence_ids, fact_versions = citations_of(proposal)
        route_tuple = call.route_tuple
        now = self._clock.now()
        try:
            rev = Drafting(self._clock, self._queue)._append(
                draft,
                subject,
                body,
                RevisionOrigin.GENERATED,
                Traceability(
                    evidence_ids=evidence_ids,
                    fact_versions=fact_versions,
                    cited_signal_id=proposal.cited_signal_id,
                    route_tuple_id=call.tuple_id,
                    playbook_sha256=route_tuple.playbook_sha256,
                    playbook_version=(
                        None if route_tuple.playbook is None else route_tuple.playbook.version
                    ),
                    prompt_version=route_tuple.prompt_version,
                    instruction=None,
                ),
                now,
                claims=claims_of(proposal),
                urgency=urgency,
                generic=False,
            )
        except (ValueError, LookupError, InvalidMarkets):  # raised before any write
            return self._fail(draft.id, DraftFailure.NOT_COMPOSABLE)
        drafts_repo.clear_draft_failed(draft.id, now)
        log.info(
            STEP_DRAFTED, extra={"draft_id": draft.id, "rev": rev, "served_model": result.model}
        )
        return StepDraftOutcome.DRAFTED
