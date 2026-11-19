"""Drafting Step 1 with the ``draft_sequence`` route (FR-20, FR-21, AD-6, AD-7, AD-8, AD-16,
AD-17, AD-18; Story 4.15): the ``draft_step`` job's use case, owned by ``drafting``.

**``StepDrafts(clock, llm, queue).draft(enrollment_id, step, urgency) -> StepDraftOutcome``**
(``@use_case``) writes revision 1 of the Enrollment's Step 1 Draft, which ``Drafting.
request_steps`` enqueued with the slot. It never calls ``nextron.domain.budget.allow``:
``draft_sequence`` is ``essential``. Prepare transaction, then the call with no transaction
open, then the apply transaction (AD-2). ``enrollment_id`` must be a ``str`` and ``step`` an
``int`` (``TypeError``), ``urgency`` an ``Urgency``.

1. **Prepare.** Step 2 is the follow-up call below; Step 3 or 4 alone, an unknown Enrollment, or
   a Step 1 slot (kind ``step`` or ``recontact``) that is missing, not ``ready`` or already has
   a revision is a no-op (``skipped``; a re-run job calls nothing). A Contact that is missing or
   erased is ``skipped`` too, with no call.
2. **The gate** (AD-16): ``compliance_api.assert_contactable(ContactSubject(contact), "email",
   "cold")``. ``Blocked``: no call and no revision; one ``events`` row (entity ``drafts``, the
   slot, ``ready`` to ``drafting_blocked``, cause the first reason code, ``cause_ref`` the
   Enrollment, actor ``system:drafting``) and ``blocked`` (decision 6: no ``draft_failed_at``;
   the suppression path stops the Enrollment).
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
   claims=the claims, urgency=urgency)``, which stores revision 1 with its grounding result and
   enqueues its ``check_grounding`` job in this unit. A ``ValueError``, ``LookupError`` or
   ``InvalidMarkets`` from it is ``draft_failed`` with ``not_composable``. Success clears
   ``draft_failed_at`` and ``draft_failed_reason`` (``drafted``).

``draft_failed`` writes ``drafts.draft_failed_at`` (now) and ``draft_failed_reason``; no
revision is stored. It logs ``step_drafted`` (``draft_id``, ``rev``, ``served_model``),
``step_draft_failed`` (``draft_id``, ``reason``), ``step_draft_blocked`` (``draft_id``) or
``step_draft_skipped`` (``enrollment_id``, ``reason``). No prompt or answer text is stored or
logged.

**The follow-ups** (Story 4.18). Step 1 on a ``draft_sequence`` tuple of schema 2 reads its
answer with ``step_drafting.read_step_1`` (exactly one ``steps`` entry, Step 1; else
``invalid_proposal``). Steps 2 to 4 are drafted together, in one call, on a schema-2 tuple only:

- ``follow_up_call(enrollment_id) -> FollowUpCall | FollowUpState`` reads, in the caller's
  transaction and with no write, what the call is built from, or why there is none, in this
  order: an unknown (``unknown``) or ended (``ended``) Enrollment; no Draft 2 to 4 still
  ``ready`` without a revision (``nothing_open``); a Contact gone or erased
  (``not_contactable``); the AD-16 gate ``Blocked`` (``blocked``); a paused Enrollment
  (``paused``); Step 1 with no revision (``no_step_1``); no active schema-2 ``draft_sequence``
  tuple (``no_route``); a context that can't be read (``not_composable``). Otherwise the blocks
  (``step_drafting.follow_up_blocks``: Step 1's current revision as an ``earlier_step`` block,
  then the Lead's blocks as Step 1's), the framing (``follow_up_framing``: the Step 2 to 4
  briefs of the bound Sequence version with their Segment x Market angles, the allowed facts,
  the Variant's Step 2 to 4 angle notes and the English variant), the fact sets the answer is
  checked against (allowed, and the ``regulation`` ones for a Step whose brief has an angle for
  the Campaign's Segment x Market) and the fingerprint (``follow_up_fingerprint``).
- ``write_follow_ups(call, parsed, urgency)`` validates (``validate_follow_ups``, then
  ``api._check_text`` of each body with Step 1's current subject) and, when accepted, appends
  revision 1 of each Draft 2 to 4 still open, in Step order, with Step 1's current subject, no
  cited Signal and the claims' citations (then the angle fact); refused: Drafts 2 to 4
  ``draft_failed`` (``invalid_proposal``). ``fail_follow_ups(enrollment_id, reason)`` records a
  failure on each open Draft 2 to 4.
- ``draft(enrollment_id, 2, urgency)`` (the direct job ``draft_step:<enrollment>:2``) builds the
  call, makes it directly (with the server fallbacks), then in the apply transaction rebuilds
  the call (the Enrollment, the gate, Step 1, the tuple, the Evidence and the facts): ended,
  nothing open, erased or blocked: nothing written; paused, or the fingerprint now unequal to
  the prepared one: nothing written and a new ``pending`` item (the next batch or the deadline
  sweep drafts it); else writes as above. Paused or no Step 1 revision: a new ``pending`` item
  instead (``requeue_follow_ups``); ``blocked``: one ``drafting_blocked`` events row per open
  Draft; ``no_route`` / ``not_composable``: ``draft_failed``; a failed call: ``draft_failed``
  with its reason, as Step 1.

``requeue_follow_ups`` and ``enqueue_follow_up_job`` are shared with
``nextron.app.drafting.batches.DraftingBatches``, which batches the same call.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from nextron.app.compliance import api as compliance_api
from nextron.app.drafting.api import (
    ACTOR,
    ENTITY,
    PAYLOAD_VERSION,
    Drafting,
    Traceability,
    _check_text,
)
from nextron.app.drafting.grounding import _evidence_text
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.drafting import DraftView
from nextron.app.queries.sequencing import StoredEnrollment
from nextron.db import ai_batches, eventlog
from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.db.uow import current, use_case
from nextron.domain.ai import Framing, RouteTuple, UntrustedBlock, Urgency, check_urgency
from nextron.domain.ai_batch import BatchOwner, Fingerprint
from nextron.domain.company_facts import FactType
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import STEP_KINDS, DraftStatus, RevisionOrigin
from nextron.domain.enrollment import LIVE_STATUSES, EnrollmentStatus
from nextron.domain.job import VERSION_FIELD, Lane
from nextron.domain.markets import InvalidMarkets
from nextron.domain.segments import SignalClass
from nextron.domain.signals import age_days, is_live
from nextron.domain.step_drafting import (
    DRAFT_SEQUENCE_ROUTE,
    DRAFT_STEP_JOB,
    ENROLLMENT_ID_FIELD,
    FOLLOW_UP_SCHEMA,
    FOLLOW_UP_STEPS,
    STEP_FIELD,
    DraftContact,
    DraftEvidence,
    DraftFact,
    DraftFailure,
    DraftSignal,
    citations_of,
    claims_of,
    follow_up_blocks,
    follow_up_citations,
    follow_up_claims,
    follow_up_fingerprint,
    follow_up_framing,
    framing,
    input_blocks,
    read_follow_ups,
    read_step_1,
    regulation_steps,
    validate_follow_ups,
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


@dataclass(frozen=True, slots=True)
class _LeadInputs:
    """The Lead's untrusted inputs, as Step 1 and the follow-ups send them."""

    subject_key: str
    evidence: tuple[DraftEvidence, ...]
    signals: tuple[DraftSignal, ...]
    contact: DraftContact
    evidence_ids: frozenset[str]
    live_events: frozenset[str]


@dataclass(frozen=True, slots=True)
class _FollowUpContext:
    """The follow-up call's framing and the fact sets its answer is checked against."""

    framing: Framing
    facts: frozenset[tuple[str, int]]
    regulation_steps: frozenset[int]
    regulation_facts: frozenset[tuple[str, int]]


class FollowUpState(StrEnum):
    """Why ``StepDrafts.follow_up_call`` built no follow-up call (Story 4.18)."""

    UNKNOWN = "unknown"  # no such Enrollment
    ENDED = "ended"  # the Enrollment stopped or completed
    NOTHING_OPEN = "nothing_open"  # no Draft 2 to 4 is ready without a revision
    PAUSED = "paused"  # the Enrollment is paused: wait
    NOT_CONTACTABLE = "not_contactable"  # the Contact is gone or erased
    BLOCKED = "blocked"  # AD-16 blocked the Contact
    NO_STEP_1 = "no_step_1"  # Step 1 has no revision yet: wait
    NO_ROUTE = "no_route"  # no active schema-2 draft_sequence tuple
    NOT_COMPOSABLE = "not_composable"  # the Sequence, Campaign, Segment or Market is gone


@dataclass(frozen=True, slots=True)
class FollowUpCall:
    """What a follow-up call is built from (Story 4.18): the call's tuple, blocks and framing,
    and what its answer is checked against."""

    enrollment_id: str
    contact_id: str
    open_draft_ids: tuple[str, ...]
    step_1_subject: str
    step_1_fact_ids: frozenset[str]
    route_tuple: RouteTuple
    tuple_id: str
    blocks: tuple[UntrustedBlock, ...]
    framing: Framing
    evidence_ids: frozenset[str]
    facts: frozenset[tuple[str, int]]
    regulation_steps: frozenset[int]
    regulation_facts: frozenset[tuple[str, int]]
    fingerprint: Fingerprint


def _open_follow_ups(enrollment_id: str) -> tuple[DraftView, ...]:
    """The Enrollment's Drafts 2 to 4 still ``ready`` without a revision, by Step."""
    return tuple(
        draft
        for draft in drafting_queries.drafts_of(enrollment_id)
        if draft.kind in STEP_KINDS and draft.step_no in FOLLOW_UP_STEPS and _open(draft)
    )


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
        if step == FOLLOW_UP_STEPS[0]:  # Story 4.18: Steps 2 to 4 in one direct call
            return self._draft_follow_ups(enrollment_id, urgency)
        uow = current()
        with uow.transaction():
            prepared = self._prepare(enrollment_id, step)
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

    def _prepare(self, enrollment_id: str, step: int) -> _Call | StepDraftOutcome:
        if step != FIRST_STEP:  # Steps 3 and 4 alone: drafted with Step 2 (Story 4.18)
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
        route_tuple = playbook_queries.active_tuple(DRAFT_SEQUENCE_ROUTE)
        tuple_id = playbook_queries.active_tuple_id(DRAFT_SEQUENCE_ROUTE)
        if route_tuple is None or tuple_id is None:
            return self._fail(draft.id, DraftFailure.NO_ROUTE)
        try:
            call_framing = self._framing(enrollment)
        except LookupError:
            return self._fail(draft.id, DraftFailure.NOT_COMPOSABLE)
        lead = self._lead(enrollment.lead_id, contact)
        return _Call(
            draft_id=draft.id,
            enrollment_id=enrollment.id,
            contact_id=contact.id,
            route_tuple=route_tuple,
            tuple_id=tuple_id,
            blocks=input_blocks(lead.subject_key, lead.evidence, lead.signals, lead.contact),
            framing=call_framing,
            evidence_ids=lead.evidence_ids,
            facts=self._allowed(enrollment),
            live_events=lead.live_events,
        )

    def _lead(self, lead_id: str, contact: research_queries.StoredContact) -> _LeadInputs:
        """The Lead's live Evidence, current Signals and the Contact as the prompt shows them;
        shared by Step 1 and the follow-ups."""
        evidence = research_queries.evidence(lead_id)
        linked = research_queries.signal_evidence_ids(lead_id)
        signals = research_queries.signals(lead_id)
        now = self._clock.now()
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
        return _LeadInputs(
            subject_key=f"lead:{lead_id}",
            evidence=tuple(
                DraftEvidence(row.id, row.type, _evidence_text(row)) for row in evidence
            ),
            signals=tuple(
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
            contact=DraftContact(contact.name, contact.role),
            evidence_ids=frozenset(row.id for row in evidence),
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
        proposal = read_step_1(result.parsed, call.route_tuple.schema_version)
        if proposal is None:  # a schema-2 answer that isn't exactly Step 1 (Story 4.18)
            return self._fail(draft.id, DraftFailure.INVALID_PROPOSAL)
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
            )
        except (ValueError, LookupError, InvalidMarkets):  # raised before any write
            return self._fail(draft.id, DraftFailure.NOT_COMPOSABLE)
        drafts_repo.clear_draft_failed(draft.id, now)
        log.info(
            STEP_DRAFTED, extra={"draft_id": draft.id, "rev": rev, "served_model": result.model}
        )
        return StepDraftOutcome.DRAFTED

    # The follow-ups (Story 4.18) ----------------------------------------------------------------

    def follow_up_call(self, enrollment_id: str) -> FollowUpCall | FollowUpState:
        """What a follow-up call (Steps 2 to 4) needs, read in the caller's transaction with no
        write, or why there is none; see the module docstring."""
        enrollment = sequencing_queries.enrollment(enrollment_id)
        if enrollment is None:
            return FollowUpState.UNKNOWN
        status = EnrollmentStatus(enrollment.status)
        if status not in LIVE_STATUSES:
            return FollowUpState.ENDED
        open_drafts = _open_follow_ups(enrollment.id)
        if not open_drafts:
            return FollowUpState.NOTHING_OPEN
        contact = research_queries.contact(enrollment.contact_id)
        if contact is None or contact.erased_at is not None:
            return FollowUpState.NOT_CONTACTABLE
        # AD-16: the gate comes before the paused check, so a caller's requeue of a paused
        # Enrollment's item (its only write) always follows one gate call
        answer = compliance_api.assert_contactable(ContactSubject(contact.id), CHANNEL, PURPOSE)
        if isinstance(answer, Blocked):  # no AI call for a subject that may not be reached
            return FollowUpState.BLOCKED
        if status is EnrollmentStatus.PAUSED:
            return FollowUpState.PAUSED
        first = _slot(enrollment.id)
        step_1 = None if first is None else drafting_queries.current_revision(first)
        if first is None or step_1 is None:
            return FollowUpState.NO_STEP_1
        route_tuple = playbook_queries.active_tuple(DRAFT_SEQUENCE_ROUTE)
        tuple_id = playbook_queries.active_tuple_id(DRAFT_SEQUENCE_ROUTE)
        if (
            route_tuple is None
            or tuple_id is None
            or route_tuple.schema_version != FOLLOW_UP_SCHEMA
        ):
            return FollowUpState.NO_ROUTE
        try:
            context = self._follow_up_context(enrollment)
        except LookupError:
            return FollowUpState.NOT_COMPOSABLE
        lead = self._lead(enrollment.lead_id, contact)
        return FollowUpCall(
            enrollment_id=enrollment.id,
            contact_id=contact.id,
            open_draft_ids=tuple(draft.id for draft in open_drafts),
            step_1_subject=step_1.subject,
            step_1_fact_ids=frozenset(fact_id for fact_id, _ in step_1.fact_versions),
            route_tuple=route_tuple,
            tuple_id=tuple_id,
            blocks=tuple(
                follow_up_blocks(
                    lead.subject_key,
                    step_1.subject,
                    step_1.body,
                    lead.evidence,
                    lead.signals,
                    lead.contact,
                )
            ),
            framing=context.framing,
            evidence_ids=lead.evidence_ids,
            facts=context.facts,
            regulation_steps=context.regulation_steps,
            regulation_facts=context.regulation_facts,
            fingerprint=follow_up_fingerprint(
                route_tuple.key, first.id, step_1.rev, lead.evidence_ids, context.facts
            ),
        )

    def _follow_up_context(self, enrollment: StoredEnrollment) -> _FollowUpContext:
        """The follow-up call's framing and fact sets; ``LookupError`` when its context is
        gone (the Sequence version, a Step 2 to 4 brief, the Campaign, Segment or Market)."""
        campaign = self._campaign(enrollment)
        sequence = setup_queries.sequence_by_id(enrollment.sequence_id)
        if sequence is None or sequence.version != enrollment.sequence_version:
            raise LookupError("the Enrollment's Sequence version isn't stored")
        steps = sequence.definition.steps
        if len(steps) < FOLLOW_UP_STEPS[-1]:
            raise LookupError("the Enrollment's Sequence has no Step 2 to 4 briefs")
        briefs = tuple(steps[step - 1] for step in FOLLOW_UP_STEPS)
        segment = setup_queries.segment_by_id(campaign.segment_id)
        market = setup_queries.market_by_id(campaign.market_id)
        if segment is None or market is None:
            raise LookupError("the Campaign's Segment or Market is unknown")
        variant = next((v for v in campaign.variants if v.id == enrollment.variant_id), None)
        notes = (
            {}
            if variant is None
            else {
                angle.step: angle.note
                for angle in variant.step_angles
                if angle.step in FOLLOW_UP_STEPS
            }
        )
        allowed = playbook_queries.allowed_facts(campaign.segment_id, campaign.market_id)
        return _FollowUpContext(
            framing=follow_up_framing(
                briefs,
                segment.key,
                market.code,
                tuple(
                    DraftFact(found.fact_id, found.version, found.content.wording)
                    for found in allowed
                ),
                notes,
                market.definition.english_variant,
            ),
            facts=frozenset((found.fact_id, found.version) for found in allowed),
            regulation_steps=regulation_steps(briefs, segment.key, market.code),
            regulation_facts=frozenset(
                (found.fact_id, found.version)
                for found in allowed
                if found.content.type == FactType.REGULATION
            ),
        )

    def write_follow_ups(
        self, call: FollowUpCall, parsed: Mapping[str, Any], urgency: Urgency
    ) -> StepDraftOutcome:
        """Validate the follow-up answer and, when code accepts it, append revision 1 of each of
        the Enrollment's Drafts 2 to 4 still ``ready`` without a revision, in Step order, in the
        caller's transaction; see the module docstring."""
        open_drafts = _open_follow_ups(call.enrollment_id)
        if not open_drafts:
            return self._skip(call.enrollment_id, "nothing_to_draft")
        proposal = read_follow_ups(parsed)
        valid = validate_follow_ups(
            proposal,
            evidence_ids=call.evidence_ids,
            facts=call.facts,
            step_1_fact_ids=call.step_1_fact_ids,
            regulation_steps=call.regulation_steps,
            regulation_facts=call.regulation_facts,
        )
        if valid:
            try:
                for step in proposal.steps:
                    _check_text(call.step_1_subject, step.body.strip())
            except ValueError:
                valid = False
        if not valid:
            return self._fail_all(open_drafts, DraftFailure.INVALID_PROPOSAL)
        by_step = {step.step: step for step in proposal.steps}
        route_tuple = call.route_tuple
        drafting = Drafting(self._clock, self._queue)
        now = self._clock.now()
        drafted = 0
        for draft in open_drafts:  # in Step order: each Step's rules see the earlier ones
            step = by_step[draft.step_no or 0]
            evidence_ids, fact_versions = follow_up_citations(step)
            try:
                rev = drafting._append(
                    draft,
                    call.step_1_subject,  # a follow-up replies in Step 1's thread
                    step.body.strip(),
                    RevisionOrigin.GENERATED,
                    Traceability(
                        evidence_ids=evidence_ids,
                        fact_versions=fact_versions,
                        cited_signal_id=None,
                        route_tuple_id=call.tuple_id,
                        playbook_sha256=route_tuple.playbook_sha256,
                        playbook_version=(
                            None if route_tuple.playbook is None else route_tuple.playbook.version
                        ),
                        prompt_version=route_tuple.prompt_version,
                        instruction=None,
                    ),
                    now,
                    claims=follow_up_claims(step),
                    urgency=urgency,
                )
            except (ValueError, LookupError, InvalidMarkets):  # raised before any write
                self._fail(draft.id, DraftFailure.NOT_COMPOSABLE)
                continue
            drafts_repo.clear_draft_failed(draft.id, now)
            log.info(STEP_DRAFTED, extra={"draft_id": draft.id, "rev": rev})
            drafted += 1
        return StepDraftOutcome.DRAFTED if drafted else StepDraftOutcome.FAILED

    def fail_follow_ups(self, enrollment_id: str, reason: DraftFailure) -> StepDraftOutcome:
        """Record ``reason`` on each of the Enrollment's Drafts 2 to 4 still ``ready`` without a
        revision (no revision stored), in the caller's transaction."""
        return self._fail_all(_open_follow_ups(enrollment_id), reason)

    def _fail_all(self, drafts: Sequence[DraftView], reason: DraftFailure) -> StepDraftOutcome:
        for draft in drafts:
            self._fail(draft.id, reason)
        return StepDraftOutcome.FAILED

    def _draft_follow_ups(self, enrollment_id: str, urgency: Urgency) -> StepDraftOutcome:
        """The direct follow-up call: Steps 2 to 4 in one call (with the server fallbacks), the
        same builder, validation and apply as the batch; inputs that changed during the call
        write nothing and queue a new ``pending`` item."""
        uow = current()
        with uow.transaction():
            prepared = self.follow_up_call(enrollment_id)
            if not isinstance(prepared, FollowUpCall):
                return self._not_called(enrollment_id, prepared)
        result = self._llm.run(
            prepared.route_tuple, prepared.blocks, urgency, framing=prepared.framing
        )  # AdapterError propagates: the runner retries, nothing is stored
        with uow.transaction():
            # rebuilt now (its one gate call before any write): what changed during the call?
            rebuilt = self.follow_up_call(enrollment_id)
            if rebuilt in (FollowUpState.UNKNOWN, FollowUpState.ENDED):
                return self._skip(enrollment_id, "enrollment_ended")
            if rebuilt is FollowUpState.NOTHING_OPEN:
                return self._skip(enrollment_id, "nothing_to_draft")
            if rebuilt in (FollowUpState.NOT_CONTACTABLE, FollowUpState.BLOCKED):
                return self._skip(enrollment_id, "not_contactable")  # store nothing
            if rebuilt is FollowUpState.PAUSED:
                requeue_follow_ups(enrollment_id, self._clock.now())
                return self._skip(enrollment_id, "enrollment_paused")
            if (
                not isinstance(rebuilt, FollowUpCall)
                or rebuilt.fingerprint.encode() != prepared.fingerprint.encode()
            ):
                # Step 1 edited, the tuple, the Evidence or the facts changed: drafted later
                requeue_follow_ups(enrollment_id, self._clock.now())
                return self._skip(enrollment_id, "inputs_changed")
            if not isinstance(result, LlmOk):
                return self.fail_follow_ups(
                    enrollment_id, FAILURES.get(type(result), DraftFailure.FAILED)
                )
            return self.write_follow_ups(prepared, result.parsed, urgency)

    def _not_called(self, enrollment_id: str, state: FollowUpState) -> StepDraftOutcome:
        """The direct follow-up job's answer when there is no call to make."""
        if state in (FollowUpState.PAUSED, FollowUpState.NO_STEP_1):
            # wait: the afternoon batch (or the deadline sweep) drafts them later
            requeue_follow_ups(enrollment_id, self._clock.now())
            return self._skip(enrollment_id, str(state))
        if state is FollowUpState.BLOCKED:
            return self._blocked_follow_ups(enrollment_id)
        if state is FollowUpState.NO_ROUTE:
            return self.fail_follow_ups(enrollment_id, DraftFailure.NO_ROUTE)
        if state is FollowUpState.NOT_COMPOSABLE:
            return self.fail_follow_ups(enrollment_id, DraftFailure.NOT_COMPOSABLE)
        return self._skip(enrollment_id, str(state))

    def _blocked_follow_ups(self, enrollment_id: str) -> StepDraftOutcome:
        """As Step 1: one ``drafting_blocked`` events row per open Draft 2 to 4, nothing else."""
        enrollment = sequencing_queries.enrollment(enrollment_id)
        if enrollment is None:
            return self._skip(enrollment_id, "unknown_enrollment")
        answer = compliance_api.assert_contactable(
            ContactSubject(enrollment.contact_id), CHANNEL, PURPOSE
        )
        if not isinstance(answer, Blocked):  # cleared meanwhile: the next run drafts them
            return self._skip(enrollment_id, "not_blocked")
        now = self._clock.now()
        for draft in _open_follow_ups(enrollment_id):
            eventlog.append(
                entity=ENTITY,
                entity_id=draft.id,
                from_status=str(DraftStatus.READY),
                to_status=DRAFTING_BLOCKED,
                cause=str(answer.reasons[0]),
                cause_ref=enrollment_id,
                actor=ACTOR,
                at=now,
            )
            log.info(STEP_DRAFT_BLOCKED, extra={"draft_id": draft.id})
        return StepDraftOutcome.BLOCKED


# Shared with the batches (Story 4.18) -----------------------------------------------------------


def requeue_follow_ups(
    enrollment_id: str, at: datetime, *, besides: str | None = None
) -> str | None:
    """A new ``pending`` drafting item for the Enrollment's follow-ups (the next ``rev``), in the
    caller's transaction, unless an open one other than ``besides`` (the item being settled)
    is already held; its id, or ``None``."""
    held = any(
        item.target_id == enrollment_id and item.id != besides
        for item in ai_batches.open_items(BatchOwner.DRAFTING, DRAFT_SEQUENCE_ROUTE)
    )
    if held:
        return None
    return ai_batches.queue_item(
        BatchOwner.DRAFTING,
        route=DRAFT_SEQUENCE_ROUTE,
        target_id=enrollment_id,
        rev=ai_batches.next_rev(BatchOwner.DRAFTING, DRAFT_SEQUENCE_ROUTE, enrollment_id),
        context_id=None,
        at=at,
    )


def enqueue_follow_up_job(queue: JobQueue, enrollment_id: str) -> None:
    """The direct follow-up job, in the caller's transaction: ``draft_step:<enrollment>:2``
    (payload ``step`` 2) on the interactive lane, so it drafts at ``soon``."""
    queue.enqueue(
        DRAFT_STEP_JOB,
        scope=enrollment_id,
        discriminator=str(FOLLOW_UP_STEPS[0]),
        payload={
            VERSION_FIELD: PAYLOAD_VERSION,
            ENROLLMENT_ID_FIELD: enrollment_id,
            STEP_FIELD: FOLLOW_UP_STEPS[0],
        },
        lane=Lane.INTERACTIVE,
    )
