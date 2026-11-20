"""The drafting smoke set's scorer (FR-73, AD-12, AD-18; Story 4.15): how a ``draft_sequence``
tuple's test run drafts each fixture Lead's Step 1 and counts the flags on the output. Standard
library only, plus ``nextron.domain``. ``nextron.app.playbook.api`` runs it.

A drafting smoke case (``nextron.domain.ai_test_sets``) is one Lead's Step 1: its Segment and
Market, the Lead name, the Contact's name and role, an optional Campaign angle note, its Signals,
Evidence and facts, and ``expected.cited_signal_id`` (the Event the draft must open with).

**The draft call**, exactly as the ``draft_step`` job builds it (``nextron.domain.
step_drafting``): ``case_blocks(case, number, segments)`` are ``input_blocks`` for the subject
``lead:case<number>``: each Evidence item (``kind``, then its text), each Signal (its type, its
class from the case's Segment, its tier, its ``observed_on`` as the event date and its one
Evidence id) and the Contact; at ``bulk`` urgency. ``case_framing(case, brief, english_variant)``
is ``framing`` with ``brief`` (the Step 1 brief of the stored ``default`` Sequence, with its
angle for the case's Segment and Market), the case's facts, its angle note and the Market's
English variant.

**Scoring.** ``SmokeContext(brief, english_variants, grounding)`` is what the run read in its
first transaction: the brief (``None`` when no ``default`` Sequence is stored), each stored
Market's English variant by code, and the active ``grounding_check`` tuple (``None`` without
one). A case whose brief or Market variant is missing is flagged ``no_brief`` with no call.
Per case:

1. ``draft_flags(case, result, brief, segments, schema_version=1) -> DraftScore``: a result
   that isn't ``LlmOk`` (its type name) is one flag and no proposal. Otherwise the proposal is
   read by the tuple's schema version (``read_step_1``, Story 4.18: schema 2 needs exactly one
   ``steps`` entry, Step 1; else one flag and no proposal) and validated
   (``validate_proposal``, against the case's Evidence ids,
   its facts and its Events: a Signal of the Segment's ``event`` class with Tier 1 or 2 and an
   ``observed_on``); an invalid proposal is one flag. Then ``grounding_rules.check`` runs over
   it with the Step 1 brief and a context built from the case: no contact details; each
   Evidence id's kind; the Signals with their class and score-only flag from the Segment; the
   strongest Event ``expected.cited_signal_id``; the case's facts as the allowed facts; no
   earlier Step. Each rules flag counts.
2. ``grounding_input_of(case, proposal)``: the active ``grounding_check`` tuple then reads the
   output through the shared builder (``grounding_check.input_blocks`` for ``lead:case<n>``):
   Step 1, the Market, the subject and body, the Evidence and facts the claims cite.
3. ``score_drafting(case, draft, grounding)`` is the case's ``DraftingOutcome`` (flags = rules
   flags + AI flags, +1 for an invalid proposal, a non-``LlmOk`` answer or no active
   ``grounding_check`` tuple: fail closed) and ``CaseResult`` (passes with zero flags).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from nextron.domain.ai import Framing, RouteTuple, UntrustedBlock, Urgency
from nextron.domain.ai_test_cases import TestCase
from nextron.domain.ai_test_gate import CaseResult, DraftingOutcome, TestSet
from nextron.domain.ai_test_sets import is_event
from nextron.domain.grounding_check import (
    GroundingEvidence,
    GroundingFact,
    GroundingInput,
    read_answer,
)
from nextron.domain.grounding_rules import LeadSignal, RevisionInput, RulesContext, check
from nextron.domain.markets import EnglishVariant
from nextron.domain.segments import SegmentDef, SignalClass
from nextron.domain.sequences import StepBrief
from nextron.domain.step_drafting import (
    DraftContact,
    DraftEvidence,
    DraftFact,
    DraftSignal,
    Proposal,
    citations_of,
    claims_of,
    framing,
    input_blocks,
    read_step_1,
    validate_proposal,
)

FIRST_STEP: Final = 1
NO_ANSWER: Final = "no_answer"
INVALID_PROPOSAL: Final = "invalid_proposal"
NO_BRIEF: Final = "no_brief"
NO_GROUNDING: Final = "no_grounding_tuple"


@dataclass(frozen=True, slots=True)
class SmokeContext:
    """What a drafting smoke run read in its first transaction; see the module docstring."""

    brief: StepBrief | None
    english_variants: Mapping[str, EnglishVariant]
    grounding: RouteTuple | None


@dataclass(frozen=True, slots=True)
class DraftScore:
    """The draft call's part of a case's score: the codes it flagged, and the proposal when
    one was valid enough to check further."""

    codes: tuple[str, ...]
    proposal: Proposal | None


def _types(case: TestCase, segments: Sequence[SegmentDef]) -> Mapping[str, Any]:
    segment = next((found for found in segments if found.key == case.input["segment"]), None)
    return {} if segment is None else {kind.key: kind for kind in segment.signal_types}


def _class(case: TestCase, segments: Sequence[SegmentDef], signal_type: str) -> SignalClass:
    found = _types(case, segments).get(signal_type)
    return SignalClass.ATTRIBUTE if found is None else found.signal_class


def _events(case: TestCase, segments: Sequence[SegmentDef]) -> frozenset[str]:
    return frozenset(
        row["id"]
        for row in case.input["signals"]
        if is_event(row) and _class(case, segments, row["signal_type"]) is SignalClass.EVENT
    )


def _facts(case: TestCase) -> tuple[DraftFact, ...]:
    return tuple(
        DraftFact(str(row["fact_id"]), int(row["version"]), str(row["wording"]))
        for row in case.input["facts"]
    )


def case_blocks(
    case: TestCase, number: int, segments: Sequence[SegmentDef]
) -> tuple[list[UntrustedBlock], Urgency]:
    """The draft call's blocks and urgency for case ``number`` (1-based)."""
    if not isinstance(case, TestCase) or case.test_set is not TestSet.DRAFTING_SMOKE:
        raise TypeError("case_blocks takes a drafting smoke TestCase")
    given = case.input
    contact = given["contact"]
    blocks = input_blocks(
        f"lead:case{number}",
        (
            DraftEvidence(str(row["id"]), str(row["kind"]), str(row["text"]))
            for row in given["evidence"]
        ),
        (
            DraftSignal(
                str(row["id"]),
                str(row["signal_type"]),
                _class(case, segments, row["signal_type"]).value,
                int(row["tier"]),
                None if row["observed_on"] is None else date.fromisoformat(row["observed_on"]),
                (str(row["evidence_id"]),),
            )
            for row in given["signals"]
        ),
        DraftContact(str(contact["name"]), str(contact["role"])),
    )
    return blocks, Urgency.BULK


def case_framing(case: TestCase, brief: StepBrief, english_variant: EnglishVariant) -> Framing:
    """The draft call's framing for ``case``; see the module docstring."""
    given = case.input
    note = given["angle_note"]
    return framing(
        brief,
        str(given["segment"]),
        str(given["market"]),
        _facts(case),
        None if note is None else str(note),
        english_variant,
    )


def _context(
    case: TestCase, segments: Sequence[SegmentDef], proposal: Proposal
) -> tuple[RevisionInput, RulesContext]:
    given = case.input
    types = _types(case, segments)
    evidence_ids, fact_versions = citations_of(proposal)
    revision = RevisionInput(
        subject=proposal.subject.strip(),
        body=proposal.body.strip(),
        claims=claims_of(proposal),
        cited_signal_id=proposal.cited_signal_id,
        evidence_ids=evidence_ids,
        fact_versions=fact_versions,
    )
    context = RulesContext(
        step=FIRST_STEP,
        contact_details=(),
        evidence_kinds={str(row["id"]): str(row["kind"]) for row in given["evidence"]},
        signals=tuple(
            LeadSignal(
                id=str(row["id"]),
                signal_type=str(row["signal_type"]),
                signal_class=_class(case, segments, row["signal_type"]),
                tier=int(row["tier"]),
                score_only=bool(getattr(types.get(row["signal_type"]), "score_only", False)),
                evidence_ids=(str(row["evidence_id"]),),
            )
            for row in given["signals"]
        ),
        strongest_event_id=str(case.expected["cited_signal_id"]),
        allowed_facts=frozenset((fact.fact_id, fact.version) for fact in _facts(case)),
        earlier_steps=(),
    )
    return revision, context


def draft_flags(
    case: TestCase,
    result: Mapping[str, Any] | str,
    brief: StepBrief,
    segments: Sequence[SegmentDef],
    schema_version: int = 1,
) -> DraftScore:
    """The draft call's flags and checked proposal; see the module docstring."""
    if not isinstance(result, Mapping):
        return DraftScore((NO_ANSWER,), None)
    proposal = read_step_1(result, schema_version)
    if proposal is None:  # a schema-2 answer that isn't exactly Step 1 (Story 4.18)
        return DraftScore((INVALID_PROPOSAL,), None)
    valid = validate_proposal(
        proposal,
        evidence_ids=frozenset(str(row["id"]) for row in case.input["evidence"]),
        facts=frozenset((fact.fact_id, fact.version) for fact in _facts(case)),
        live_events=_events(case, segments),
    )
    codes: list[str] = [] if valid else [INVALID_PROPOSAL]
    try:
        revision, context = _context(case, segments, proposal)
        codes += [flag.rule for flag in check(revision, brief, context).flags]
    except (TypeError, ValueError):  # a proposal the rules can't read: fail closed
        codes.append(INVALID_PROPOSAL)
    return DraftScore(tuple(codes), proposal if valid else None)


def grounding_input_of(case: TestCase, proposal: Proposal) -> GroundingInput:
    """The grounding check's input for the case's checked output; see the module docstring."""
    given = case.input
    evidence_ids, fact_versions = citations_of(proposal)
    rows = {str(row["id"]): row for row in given["evidence"]}
    wording = {(fact.fact_id, fact.version): fact.wording for fact in _facts(case)}
    return GroundingInput(
        step=FIRST_STEP,
        market=str(given["market"]),
        subject=proposal.subject.strip(),
        body=proposal.body.strip(),
        evidence=tuple(
            GroundingEvidence(found, str(rows[found]["kind"]), str(rows[found]["text"]))
            for found in evidence_ids
            if found in rows
        ),
        facts=tuple(
            GroundingFact(fact_id, version, wording[(fact_id, version)])
            for fact_id, version in fact_versions
            if (fact_id, version) in wording
        ),
        earlier_steps=(),
    )


def grounding_codes(
    proposal: Proposal, result: Mapping[str, Any] | str | None
) -> tuple[str, ...]:
    """The grounding check's flag codes on the output: ``no_grounding_tuple`` when there was no
    call (``None``), ``no_answer`` for an answer that isn't ``LlmOk`` (its type name), else
    ``read_answer``'s flags."""
    if result is None:
        return (NO_GROUNDING,)
    if not isinstance(result, Mapping):
        return (NO_ANSWER,)
    answer = read_answer(result, proposal.subject.strip(), proposal.body.strip())
    return tuple(flag.code for flag in answer.flags)


def score_drafting(
    case: TestCase, codes: Iterable[str]
) -> tuple[DraftingOutcome, CaseResult]:
    """A case's outcome and result from every code flagged on it (zero passes)."""
    if not isinstance(case, TestCase) or case.test_set is not TestSet.DRAFTING_SMOKE:
        raise TypeError("score_drafting takes a drafting smoke TestCase")
    found = tuple(codes)
    got = "flagged: " + ", ".join(dict.fromkeys(found)) if found else "not flagged"
    return DraftingOutcome(len(found)), CaseResult(case.case_key, not found, "not flagged", got)


def no_brief_codes() -> tuple[str, ...]:
    """The codes of a case run with no stored ``default`` Sequence: fail closed."""
    return (NO_BRIEF,)
