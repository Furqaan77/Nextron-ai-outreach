"""The AI grounding check of a Draft revision (FR-21, AD-6, AD-7, AD-8, AD-16, AD-18; Story
4.14): the ``check_grounding`` job's use case, owned by ``drafting``.

**``GroundingChecks(clock, llm).check(draft_id, rev, urgency) -> GroundingCheckOutcome``**
(``@use_case``) checks the revision ``rev`` of the Draft ``draft_id``, which ``Drafting._append``
enqueued with it. It never calls ``nextron.domain.budget.allow``: both grounding routes are
``essential``. Prepare transaction, then the call(s) with no transaction open, then the apply
transaction (AD-2):

1. **Prepare.** An unknown Draft or revision, or a revision whose ``grounding_results`` row
   already has ``ai_checked_at``, is a no-op (``skipped``; a re-run job calls nothing). The
   Draft's Enrollment and Contact must exist; then ``compliance_api.assert_contactable(
   ContactSubject(contact), "email", "cold")`` (AD-16): ``Blocked``, or an erased Contact, records
   ``fail`` with ``checker_unavailable`` (``UNAVAILABLE_NOT_CONTACTABLE``) at once, with no
   call. So does no active ``grounding_check`` tuple (``queries.playbook.active_tuple`` and
   ``active_tuple_id``; ``UNAVAILABLE_NO_TUPLE``). Otherwise it reads the input
   (``domain.grounding_check.GroundingInput``): the Draft's Step number; its Contact's Market
   code; the revision's subject and body; its cited Evidence, in its ``evidence_ids`` order,
   each ``{id, kind, text}`` (a quote's text; ``Looked for and not found: <sought>`` for
   Absence; ``Counted <sought>: <number>`` for a count; ``<fact>: <value>`` appended when set);
   its cited fact versions with their wording (``queries.playbook.fact``); and the current
   subject and body of the Enrollment's earlier Step Drafts. A cited id that isn't found is
   left out (the rules flag it). It also reads the active ``grounding_check_fallback`` tuple,
   if any.
2. **The calls.** ``llm.run(tuple, input_blocks("lead:<lead id>", input), urgency)``. A refused,
   invalid or truncated answer re-runs once on the fallback tuple when one is active (AD-8).
   ``AdapterError`` (timeout, 5xx, connection) or ``LlmFailed`` → ``checker_unavailable``
   (``UNAVAILABLE_FAILED``), never retried (decision 2); a refused, invalid or truncated answer
   with no fallback, or a fallback answer that isn't ``LlmOk`` → ``checker_unavailable``
   (``UNAVAILABLE_NO_ANSWER``).
3. **Apply.** A Contact erased (or gone) since the prepare records ``fail`` with the stored,
   already tombstoned, rules flags and ``checker_unavailable`` (``UNAVAILABLE_NOT_CONTACTABLE``),
   and no AI flag. An ``LlmOk`` answer is read by ``grounding_check.read_answer``; its claims
   re-run ``domain.grounding_rules.check`` with the same context ``Drafting`` builds
   (``api._grounding``, at the clock's now), so text rules and citation rules both apply. The
   rules flags are that run's, then any flag stored by ``_append`` whose (code, span) it doesn't
   repeat (so a generated revision's own claim flags are never lost), then the AI flags
   (``grounding_check.combine``): ``pass`` only with no flag at all. A context that can no
   longer be built (``LookupError``) keeps the stored rules flags and adds
   ``checker_unavailable``. On every failure path the stored rules flags stay, followed by
   ``checker_unavailable``. ``grounding_results.record_ai_result`` writes status, flags, the
   tuple row that served (``None`` when there was none), the served model (``LlmResult.model``)
   and ``ai_checked_at`` = now, only while ``ai_checked_at`` is NULL. A result for a revision that
   is no longer current is stored on that revision's row only.

It logs ``grounding_checked`` (``draft_id``, ``rev``, ``status``, ``flag_count``,
``served_model``, ``fallback``) or ``grounding_check_skipped`` (``draft_id``, ``rev``,
``reason``). No prompt or answer text is stored or logged.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.drafting.api import _grounding
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.drafting import DraftView, RevisionView
from nextron.db.repos.drafting import grounding_results as grounding_results_repo
from nextron.db.repos.drafting.grounding_results import StoredFlag, StoredGroundingResult
from nextron.db.uow import current, use_case
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency, check_urgency
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import STEP_KINDS
from nextron.domain.grounding_check import (
    GROUNDING_CHECK_ROUTE,
    GROUNDING_FALLBACK_ROUTE,
    UNAVAILABLE_FAILED,
    UNAVAILABLE_NO_ANSWER,
    UNAVAILABLE_NO_TUPLE,
    UNAVAILABLE_NOT_CONTACTABLE,
    CheckedFlag,
    FlagSource,
    GroundingEarlierStep,
    GroundingEvidence,
    GroundingFact,
    GroundingInput,
    combine,
    input_blocks,
    read_answer,
    rules_flags,
    unavailable,
)
from nextron.domain.grounding_rules import Claim, GroundingStatus, RevisionInput
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmFailed, LlmOk, LlmPort, LlmResult

log = logging.getLogger(__name__)

GROUNDING_CHECKED: Final = "grounding_checked"
GROUNDING_CHECK_SKIPPED: Final = "grounding_check_skipped"
CHANNEL: Final = "email"
PURPOSE: Final = "cold"
QUOTE: Final = "quote"
ABSENCE: Final = "absence"
COUNT: Final = "count"


class GroundingCheckOutcome(StrEnum):
    """What one ``check`` did."""

    RECORDED = "recorded"  # a result was recorded
    SKIPPED = "skipped"  # nothing to do: unknown, or already checked


@dataclass(frozen=True, slots=True)
class _Tuple:
    route_tuple: RouteTuple
    tuple_id: str


@dataclass(frozen=True, slots=True)
class _Call:
    """What the prepare transaction read for the call."""

    blocks: list[UntrustedBlock]
    contact_id: str
    primary: _Tuple
    fallback: _Tuple | None
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class _Answer:
    """What the calls gave: the usable result, or why there is none."""

    result: LlmOk | None
    tuple_id: str | None
    served_model: str | None
    reason: str | None  # set when result is None
    fallback: bool


def _active(route: str) -> _Tuple | None:
    route_tuple = playbook_queries.active_tuple(route)
    tuple_id = playbook_queries.active_tuple_id(route)
    if route_tuple is None or tuple_id is None:
        return None
    return _Tuple(route_tuple, tuple_id)


def _evidence_text(row: research_queries.StoredEvidence) -> str:
    if row.type == ABSENCE:
        text = f"Looked for and not found: {row.sought or ''}".rstrip()
    elif row.type == COUNT:
        number = "" if row.number is None else str(row.number)
        text = f"Counted {row.sought or ''}: {number}".rstrip()
    else:
        text = row.quote or ""
    if row.fact is not None and row.value is not None:
        text = f"{text}\n{row.fact}: {row.value}".strip()
    return text


def _input(
    draft: DraftView, revision: RevisionView, lead_id: str, enrollment_id: str, market: str
) -> GroundingInput:
    """The check's input for ``revision``; see the module docstring."""
    by_id = {row.id: row for row in research_queries.evidence_history(lead_id)}
    evidence = tuple(
        GroundingEvidence(found.id, found.type, _evidence_text(found))
        for found in (
            by_id.get(evidence_id) for evidence_id in dict.fromkeys(revision.evidence_ids)
        )
        if found is not None
    )
    facts: list[GroundingFact] = []
    for fact_id, version in dict.fromkeys(revision.fact_versions):
        history = playbook_queries.fact(fact_id)
        chosen = (
            None
            if history is None
            else next((v for v in history.versions if v.version == version), None)
        )
        if chosen is not None:
            facts.append(GroundingFact(fact_id, version, chosen.content.wording))
    earlier: list[GroundingEarlierStep] = []
    for other in drafting_queries.drafts_of(enrollment_id):
        if (
            other.kind not in STEP_KINDS
            or other.step_no is None
            or draft.step_no is None
            or other.step_no >= draft.step_no
        ):
            continue
        found = drafting_queries.current_revision(other)
        if found is not None:
            earlier.append(GroundingEarlierStep(other.step_no, found.subject, found.body))
    return GroundingInput(
        step=draft.step_no or 0,
        market=market,
        subject=revision.subject,
        body=revision.body,
        evidence=evidence,
        facts=tuple(facts),
        earlier_steps=tuple(sorted(earlier, key=lambda row: row.step)),
    )


def _stored_rules(row: StoredGroundingResult) -> tuple[CheckedFlag, ...]:
    """The stored row's rules flags."""
    return tuple(
        CheckedFlag(flag.rule, flag.span, flag.explanation, FlagSource.RULES)
        for flag in row.flags
        if flag.source == FlagSource.RULES.value
    )


def _record(
    draft_id: str,
    rev: int,
    status: GroundingStatus,
    flags: tuple[CheckedFlag, ...],
    tuple_id: str | None,
    served_model: str | None,
    at: datetime,
) -> bool:
    return grounding_results_repo.record_ai_result(
        draft_id,
        rev,
        status=str(status),
        flags=tuple(
            StoredFlag(flag.code, flag.span, flag.explanation, flag.source.value)
            for flag in flags
        ),
        route_tuple_id=tuple_id,
        served_model=served_model,
        at=at,
    )


class GroundingChecks:
    """The ``check_grounding`` jobs; see the module docstring."""

    def __init__(self, clock: Clock, llm: LlmPort) -> None:
        self._clock = clock
        self._llm = llm

    @use_case
    def check(self, draft_id: str, rev: int, urgency: Urgency) -> GroundingCheckOutcome:
        """Run the AI grounding check of the revision and record its result; see the module
        docstring."""
        check_urgency(urgency)
        uow = current()
        with uow.transaction():
            prepared = self._prepare(draft_id, rev)
        if not isinstance(prepared, _Call):
            return prepared
        answer = self._ask(prepared, urgency)
        with uow.transaction():
            return self._apply(draft_id, rev, prepared, answer)

    # Step 1 ------------------------------------------------------------------------------------

    def _skip(self, draft_id: str, rev: int, reason: str) -> GroundingCheckOutcome:
        log.info(
            GROUNDING_CHECK_SKIPPED, extra={"draft_id": draft_id, "rev": rev, "reason": reason}
        )
        return GroundingCheckOutcome.SKIPPED

    def _fail_now(
        self, row: StoredGroundingResult, reason: str, tuple_id: str | None
    ) -> GroundingCheckOutcome:
        """Record a fail-closed result with no call."""
        status, flags = combine(_stored_rules(row), (unavailable(reason),), checked=False)
        recorded = _record(row.draft_id, row.rev, status, flags, tuple_id, None, self._clock.now())
        self._log(row.draft_id, row.rev, status, flags, None, fallback=False)
        return GroundingCheckOutcome.RECORDED if recorded else GroundingCheckOutcome.SKIPPED

    def _prepare(self, draft_id: str, rev: int) -> _Call | GroundingCheckOutcome:
        draft = drafting_queries.draft(draft_id)
        revision = None if draft is None else drafting_queries.revision(draft_id, rev)
        row = None if revision is None else grounding_results_repo.of_revision(draft_id, rev)
        if draft is None or revision is None or row is None:
            return self._skip(draft_id, rev, "unknown_revision")
        if row.ai_checked_at is not None:
            return self._skip(draft_id, rev, "already_checked")
        enrollment = (
            None
            if draft.enrollment_id is None
            else sequencing_queries.enrollment(draft.enrollment_id)
        )
        contact = None if enrollment is None else research_queries.contact(enrollment.contact_id)
        if enrollment is None or contact is None:
            return self._fail_now(row, UNAVAILABLE_NOT_CONTACTABLE, None)
        answer = compliance_api.assert_contactable(
            ContactSubject(contact.id), CHANNEL, PURPOSE
        )  # AD-16: no AI call for a subject that may not be reached
        if isinstance(answer, Blocked) or contact.erased_at is not None:
            return self._fail_now(row, UNAVAILABLE_NOT_CONTACTABLE, None)
        primary = _active(GROUNDING_CHECK_ROUTE)
        if primary is None:
            return self._fail_now(row, UNAVAILABLE_NO_TUPLE, None)
        market = (
            None if contact.market_id is None else setup_queries.market_by_id(contact.market_id)
        )
        given = _input(
            draft,
            revision,
            enrollment.lead_id,
            enrollment.id,
            "" if market is None else market.definition.code,
        )
        return _Call(
            blocks=input_blocks(f"lead:{enrollment.lead_id}", given),
            contact_id=contact.id,
            primary=primary,
            fallback=_active(GROUNDING_FALLBACK_ROUTE),
            subject=revision.subject,
            body=revision.body,
        )

    # Step 2 ------------------------------------------------------------------------------------

    def _run(self, chosen: _Tuple, call: _Call, urgency: Urgency) -> LlmResult | None:
        """One call; ``None`` when the gateway raised (timeout, 5xx, connection)."""
        try:
            return self._llm.run(chosen.route_tuple, call.blocks, urgency)
        except AdapterError:
            return None

    def _ask(self, call: _Call, urgency: Urgency) -> _Answer:
        first = self._run(call.primary, call, urgency)
        if first is None or isinstance(first, LlmFailed):
            model = None if first is None else first.model
            return _Answer(None, call.primary.tuple_id, model, UNAVAILABLE_FAILED, False)
        if isinstance(first, LlmOk):
            return _Answer(first, call.primary.tuple_id, first.model, None, False)
        if call.fallback is None:  # refused, invalid or truncated, and no tested fallback
            return _Answer(None, call.primary.tuple_id, first.model, UNAVAILABLE_NO_ANSWER, False)
        second = self._run(call.fallback, call, urgency)
        if isinstance(second, LlmOk):
            return _Answer(second, call.fallback.tuple_id, second.model, None, True)
        model = None if second is None else second.model
        reason = UNAVAILABLE_FAILED if second is None else UNAVAILABLE_NO_ANSWER
        return _Answer(None, call.fallback.tuple_id, model, reason, True)

    # Step 3 ------------------------------------------------------------------------------------

    def _apply(
        self, draft_id: str, rev: int, call: _Call, answer: _Answer
    ) -> GroundingCheckOutcome:
        row = grounding_results_repo.of_revision(draft_id, rev)
        if row is None or row.ai_checked_at is not None:  # checked meanwhile
            return self._skip(draft_id, rev, "already_checked")
        stored = _stored_rules(row)
        now = self._clock.now()
        contact = research_queries.contact(call.contact_id)
        if contact is None or contact.erased_at is not None:  # erased during the call
            status, flags = combine(
                stored, (unavailable(UNAVAILABLE_NOT_CONTACTABLE),), checked=False
            )
        elif answer.result is None:
            reason = answer.reason or UNAVAILABLE_FAILED
            status, flags = combine(stored, (unavailable(reason),), checked=False)
        else:
            read = read_answer(answer.result.parsed, call.subject, call.body)
            rules = self._rules(draft_id, rev, read.claims, now)
            if rules is None:
                status, flags = combine(
                    stored, (*read.flags, unavailable(UNAVAILABLE_FAILED)), checked=False
                )
            else:
                rerun = {(flag.code, flag.span) for flag in rules}
                kept = (*rules, *(f for f in stored if (f.code, f.span) not in rerun))
                status, flags = combine(kept, read.flags, checked=True)
        recorded = _record(draft_id, rev, status, flags, answer.tuple_id, answer.served_model, now)
        self._log(draft_id, rev, status, flags, answer.served_model, fallback=answer.fallback)
        return GroundingCheckOutcome.RECORDED if recorded else GroundingCheckOutcome.SKIPPED

    @staticmethod
    def _rules(
        draft_id: str, rev: int, claims: tuple[Claim, ...], now: datetime
    ) -> tuple[CheckedFlag, ...] | None:
        """The rules re-run with the AI's claims, or ``None`` when the context is gone."""
        draft = drafting_queries.draft(draft_id)
        revision = None if draft is None else drafting_queries.revision(draft_id, rev)
        enrollment = (
            None
            if draft is None or draft.enrollment_id is None
            else sequencing_queries.enrollment(draft.enrollment_id)
        )
        if draft is None or revision is None or enrollment is None:
            return None
        try:
            result = _grounding(
                draft,
                enrollment,
                RevisionInput(
                    subject=revision.subject,
                    body=revision.body,
                    claims=claims,
                    cited_signal_id=revision.cited_signal_id,
                    evidence_ids=revision.evidence_ids,
                    fact_versions=revision.fact_versions,
                ),
                now,
            )
        except LookupError:
            return None
        return rules_flags(result.flags)

    @staticmethod
    def _log(
        draft_id: str,
        rev: int,
        status: GroundingStatus,
        flags: tuple[CheckedFlag, ...],
        served_model: str | None,
        *,
        fallback: bool,
    ) -> None:
        log.info(
            GROUNDING_CHECKED,
            extra={
                "draft_id": draft_id,
                "rev": rev,
                "status": str(status),
                "flag_count": len(flags),
                "served_model": served_model,
                "fallback": fallback,
            },
        )
