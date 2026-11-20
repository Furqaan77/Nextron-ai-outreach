"""Drafting a Step 1 email with the ``draft_sequence`` route (FR-20, FR-21, AD-7, AD-8, AD-17,
AD-18; Story 4.15): the job, its failure reasons, the prompt's trusted framing and untrusted
blocks, and the code checks of the model's proposal. Standard library only, plus
``nextron.domain``.

**Route and job.** ``DRAFT_SEQUENCE_ROUTE`` is ``draft_sequence``. ``DRAFT_STEP_JOB`` is
``draft_step``: scope the Enrollment id, discriminator the Step number, payload ``{"v": 1,
"enrollment_id": <ULID>, "step": 1}`` (``ENROLLMENT_ID_FIELD``, ``STEP_FIELD``).

**Failures.** ``DraftFailure`` is why a Step's drafting stored no revision: ``no_route`` (no
active ``draft_sequence`` tuple), ``refused``, ``truncated``, ``invalid`` (the answer didn't fit
the schema), ``failed`` (the request failed), ``invalid_proposal`` (the code checks below
refused it) and ``not_composable`` (the revision couldn't be composed). ``failure_text(reason)``
is the Lead page's sentence for it. Each code is at most ``MAX_REASON`` (32) characters.

**Masking.** Every text that enters the prompt, trusted or not, passes through
``nextron.domain.pseudonymise.mask_contact_details`` (``mask``): email addresses become
``[email]`` and phone numbers ``[phone]`` (AD-17: a prompt carries neither).

**The framing** (trusted; ``nextron.domain.ai.Framing``): ``framing(brief, segment, market,
facts, angle_note, english_variant)``:

- ``brief`` is ``brief_section``: a ``# Step brief`` heading, then the Step's number, purpose,
  word limit (``max_words``), subject rule and its guidance, allowed content, ask, link rule and
  the ``no_price`` and ``no_audit`` rules, then the brief's angle for ``segment`` in ``market``
  when it has one;
- ``facts`` is ``facts_section``: a ``# Approved facts`` heading, then one line per allowed fact
  (``DraftFact(fact_id, version, wording)``): ``- fact_id `<id>`, version <n>: <wording>``
  (``(none)`` when there is none);
- ``note`` is ``note_text``: the Variant's angle note for the Step when there is one
  (``Campaign angle for this Step: <note>``), then ``English: British English`` or ``American
  English`` from the Market's ``EnglishVariant``.

**The blocks** (untrusted; ``input_blocks(subject_key, evidence, signals, contact)``), all of
the one subject ``subject_key`` (``lead:<id>``):

- ``evidence`` / the Evidence id: ``kind: <kind>``, a blank line, then its text
  (``DraftEvidence(id, kind, text)``);
- ``signal`` / the Signal id: ``type``, ``class``, ``tier``, ``event date`` (``unknown`` when
  none) and ``evidence`` (its Evidence ids, comma-separated, ``none`` when none) lines
  (``DraftSignal``);
- ``contact`` / ``contact``: ``name`` and ``role`` lines (``unknown`` for a missing one).

No email address of the Contact ever enters a block: only its name and role do.

**The proposal.** ``read_proposal(parsed)`` turns a validated ``DraftSequenceV1`` answer into a
``Proposal(subject, body, claims, cited_signal_id, explanation)`` of ``ProposedClaim(span,
evidence_id, fact_id, fact_version)``. ``validate_proposal(proposal, *, evidence_ids, facts,
live_events) -> bool`` is the code check (AD-18): the subject and the body are non-empty after
strip; every claim cites exactly one of an Evidence id in ``evidence_ids`` or a ``(fact_id,
version)`` in ``facts`` (a fact id without its version, or the other way round, cites neither);
and ``cited_signal_id`` is in ``live_events`` (the Lead's live Event Signals). ``claims_of``
gives the claims as ``grounding_rules.Claim``\\ s, and ``citations_of`` the revision's
traceability: the Evidence ids and the ``(fact_id, version)`` pairs the claims cite, each once,
in first-appearance order.

**Story 4.18: schema 2 and the follow-ups.** ``FIRST_STEP`` is 1, ``FOLLOW_UP_STEPS`` (2, 3, 4)
and ``FOLLOW_UP_SCHEMA`` 2 (the ``draft_sequence`` schema that can write follow-ups).

- ``read_step_1(parsed, schema_version) -> Proposal | None``: schema 1 as ``read_proposal``;
  schema 2 needs exactly one ``steps`` entry with ``step`` 1 (its angle fact is ignored), read
  into a ``Proposal``; anything else is ``None`` (discarded).
- ``follow_up_framing(briefs, segment, market, facts, angle_notes, english_variant)``: the brief
  is a ``# Steps to write`` heading ("Write Steps 2, 3 and 4 ...") then ``brief_section`` of the
  Step 2, 3 and 4 briefs (``ValueError`` for any other briefs); the facts as ``framing``'s; the
  note: ``Campaign angle for Step <n>: <note>`` per Step 2 to 4 note, then the English line.
- ``regulation_steps(briefs, segment, market)``: the Steps whose brief has an angle for that
  Segment in that Market (each must take a ``regulation`` fact as its angle).
- ``follow_up_blocks(subject_key, step_1_subject, step_1_body, evidence, signals, contact)``:
  an ``earlier_step`` / ``step_1`` block (``subject: <subject>``, a blank line, the body), then
  ``input_blocks``.
- ``read_follow_ups(parsed) -> FollowUpProposal`` of ``FollowUp(step, subject, body, claims,
  angle_fact_id, angle_fact_version)``; ``validate_follow_ups(proposal, *, evidence_ids, facts,
  step_1_fact_ids, regulation_steps, regulation_facts) -> bool``: exactly Steps 2, 3 and 4 once
  each; each body non-empty; each claim cites one input Evidence id or input fact version; each
  Step's angle is an input fact version whose fact id Step 1's current revision doesn't cite
  and no other follow-up takes; a Step in ``regulation_steps`` takes a ``regulation_facts``
  version. ``follow_up_citations`` (the claims' citations, then the angle fact when no claim
  cites it) and ``follow_up_claims`` give a follow-up revision's traceability and claims.
- ``follow_up_fingerprint(route_tuple_key, step_1_draft_id, step_1_rev, evidence_ids, facts)``:
  an ``ai_batch.Fingerprint`` of the tuple key, ``step_1:<draft id>`` at its rev, each
  ``evidence:<id>`` and each ``fact:<id>`` at its version.
**Story 4.20: regenerating one Step.** ``REGENERATE_PROMPTS`` (``{"v3"}``) are the
``draft_sequence`` prompt versions that know the rewrite mode, the ``current_draft`` block and
the ``<operator_instruction>``; ``MAX_INSTRUCTION`` is 200.

- ``check_instruction(instruction) -> str | None``: ``None`` stays ``None``; else a ``str``
  (``TypeError``), stripped, blank is ``None``; longer than ``MAX_INSTRUCTION`` or holding any
  character of category ``Cc``, ``Cf``, ``Zl`` or ``Zp`` (line breaks, U+2028/U+2029, bidi
  and zero-width format characters) raises ``ValueError``.
- ``regenerate_framing(brief, segment, market, facts, angle_note, english_variant,
  instruction)``: ``framing``'s (the one Step's brief with its Segment x Market angle, the
  allowed facts, the Variant's note for that Step and the English line), with ``instruction``
  masked (``[email]``, ``[phone]``) as ``Framing.instruction`` (``""`` for ``None``).
- ``regenerate_blocks(subject_key, step, current_subject, current_body, other_steps, evidence,
  signals, contact)``: a ``current_draft`` block (id ``step_<n>``: ``subject: <subject>``, a
  blank line, the body); then, for a follow-up (``step`` 2 or more), one ``earlier_step`` block
  (id ``step_<n>``, the same layout) per ``RegenStep(step, subject, body)`` in
  ``other_steps`` (the other Steps' current revisions, by Step); then ``input_blocks``.
- ``read_follow_up(parsed, step) -> FollowUp | None``: exactly one ``steps`` entry, for
  ``step``; else ``None``. ``validate_follow_up(found, *, evidence_ids, facts, other_fact_ids,
  needs_regulation, regulation_facts) -> bool``: the body non-empty after strip, every claim
  cites one input Evidence id or input fact version, the angle is an input fact version whose
  fact id ``other_fact_ids`` (every fact id another Step's current revision cites) doesn't
  hold, and a regulation fact version when ``needs_regulation``.

- ``next_slot(after, at, timezone)``: the first UTC instant after ``after`` whose local time in
  ``timezone`` is ``at``; ``draft_deadline_of(created_at, submit_at, deadline, timezone)``: the
  first ``deadline`` after the first ``submit_at`` slot after ``created_at``.
"""

import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Final
from zoneinfo import ZoneInfo

from nextron.domain.ai import Framing, UntrustedBlock
from nextron.domain.ai_batch import Fingerprint
from nextron.domain.grounding_rules import Claim
from nextron.domain.markets import EnglishVariant
from nextron.domain.pseudonymise import mask_contact_details
from nextron.domain.sequences import StepBrief, WordComparison, max_words

DRAFT_SEQUENCE_ROUTE: Final = "draft_sequence"
FIRST_STEP: Final = 1
FOLLOW_UP_STEPS: Final = (2, 3, 4)  # Story 4.18: drafted together, in one call
FOLLOW_UP_SCHEMA: Final = 2  # the draft_sequence schema version that can write follow-ups
EARLIER_STEP_SOURCE: Final = "earlier_step"
STEP_1_BLOCK_ID: Final = "step_1"
DRAFT_STEP_JOB: Final = "draft_step"
ENROLLMENT_ID_FIELD: Final = "enrollment_id"
STEP_FIELD: Final = "step"
MAX_REASON: Final = 32
REGENERATE_PROMPTS: Final = frozenset({"v3"})  # Story 4.20: the prompts that know a rewrite
CURRENT_DRAFT_SOURCE: Final = "current_draft"
MAX_INSTRUCTION: Final = 200
# Unicode categories an instruction may not hold: control, format (bidi, zero-width) and the
# line and paragraph separators.
REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})

EVIDENCE_SOURCE: Final = "evidence"
SIGNAL_SOURCE: Final = "signal"
CONTACT_SOURCE: Final = "contact"
CONTACT_ID: Final = "contact"
UNKNOWN: Final = "unknown"


class DraftFailure(StrEnum):
    """Why a Step's drafting stored no revision; see the module docstring."""

    NO_ROUTE = "no_route"
    REFUSED = "refused"
    TRUNCATED = "truncated"
    INVALID = "invalid"
    FAILED = "failed"
    INVALID_PROPOSAL = "invalid_proposal"
    NOT_COMPOSABLE = "not_composable"


FAILURE_TEXTS: Final[Mapping[DraftFailure, str]] = {
    DraftFailure.NO_ROUTE: "no tested drafting model is active",
    DraftFailure.REFUSED: "the model declined to write it",
    DraftFailure.TRUNCATED: "the model's answer was cut off",
    DraftFailure.INVALID: "the model's answer wasn't in the expected form",
    DraftFailure.FAILED: "the drafting request failed",
    DraftFailure.INVALID_PROPOSAL: "the draft cited something it may not, so it was discarded",
    DraftFailure.NOT_COMPOSABLE: "the draft couldn't be composed into an email",
}


def failure_text(reason: str) -> str:
    """The Lead page's text for a stored failure reason (the code itself when unknown)."""
    try:
        return FAILURE_TEXTS[DraftFailure(reason)]
    except ValueError:
        return reason


def mask(text: str) -> str:
    """``text`` with its email addresses and phone numbers masked (AD-17)."""
    return mask_contact_details(text)


# The framing --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DraftFact:
    """One allowed Approved Company Fact version."""

    fact_id: str
    version: int
    wording: str


def _limit(word_limit: int, comparison: WordComparison) -> str:
    return f"at most {max_words(word_limit, comparison)} words"


def brief_section(brief: StepBrief, segment: str, market: str) -> str:
    """The Step brief section of the system prompt; see the module docstring."""
    if not isinstance(brief, StepBrief):
        raise TypeError("brief_section takes a StepBrief")
    lines = [
        "# Step brief",
        "",
        f"This email is Step {brief.step} of the Sequence.",
        f"Purpose: {brief.purpose}",
        f"Body length: {_limit(brief.word_limit, brief.word_comparison)}.",
    ]
    if brief.subject_rule is not None:
        rule = brief.subject_rule
        lines.append(f"Subject: {_limit(rule.word_limit, rule.word_comparison)}.")
        lines += [f"- {item}" for item in rule.guidance]
    lines.append("Allowed content:")
    lines += [f"- {item}" for item in brief.allowed_content]
    lines.append(f"Ask: {brief.ask}")
    lines.append("Links: none in the body; the signature and footer hold the only links.")
    if brief.no_price:
        lines.append("Never name a price.")
    if brief.no_audit:
        lines.append("Never offer or mention an audit.")
    angle = next(
        (
            found.angle
            for found in brief.segment_angles
            if found.segment == segment and found.market == market
        ),
        None,
    )
    if angle is not None:
        lines.append(f"Angle for this Segment and Market: {angle}")
    return mask("\n".join(lines))


def facts_section(facts: Iterable[DraftFact]) -> str:
    """The allowed facts section of the system prompt; see the module docstring."""
    chosen = tuple(facts)
    if not all(isinstance(fact, DraftFact) for fact in chosen):
        raise TypeError("facts_section takes DraftFacts")
    lines = [
        "# Approved facts",
        "",
        "These are the only facts about Nextron AI you may state. Cite each by its fact id and "
        "version.",
        "",
    ]
    lines += [
        f"- fact_id `{fact.fact_id}`, version {fact.version}: {fact.wording}" for fact in chosen
    ] or ["(none)"]
    return mask("\n".join(lines))


ENGLISH: Final[Mapping[EnglishVariant, str]] = {
    EnglishVariant.EN_GB: "British English",
    EnglishVariant.EN_US: "American English",
}


def note_text(angle_note: str | None, english_variant: EnglishVariant) -> str:
    """The Campaign note that opens the user turn; see the module docstring."""
    lines = []
    if angle_note is not None and angle_note.strip():
        lines.append(f"Campaign angle for this Step: {angle_note.strip()}")
    lines.append(f"English: {ENGLISH[EnglishVariant(english_variant)]}")
    return mask("\n".join(lines))


def framing(
    brief: StepBrief,
    segment: str,
    market: str,
    facts: Iterable[DraftFact],
    angle_note: str | None,
    english_variant: EnglishVariant,
) -> Framing:
    """The call's trusted framing; see the module docstring."""
    return Framing(
        brief=brief_section(brief, segment, market),
        facts=facts_section(facts),
        note=note_text(angle_note, english_variant),
    )


# The blocks ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DraftEvidence:
    """One live Evidence item as the prompt shows it."""

    id: str
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class DraftSignal:
    """One current Signal as the prompt shows it."""

    id: str
    signal_type: str
    signal_class: str
    tier: int
    event_date: date | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DraftContact:
    """The Contact as the prompt shows it: name and role only, never an address."""

    name: str | None
    role: str | None


def _known(value: str | None) -> str:
    return value if value is not None and value.strip() else UNKNOWN


def input_blocks(
    subject_key: str,
    evidence: Iterable[DraftEvidence],
    signals: Iterable[DraftSignal],
    contact: DraftContact,
) -> list[UntrustedBlock]:
    """The call's untrusted blocks; see the module docstring."""
    blocks = [
        UntrustedBlock(
            subject_key, EVIDENCE_SOURCE, row.id, mask(f"kind: {row.kind}\n\n{row.text}")
        )
        for row in evidence
    ]
    for signal in signals:
        event_date = UNKNOWN if signal.event_date is None else signal.event_date.isoformat()
        lines = (
            f"type: {signal.signal_type}",
            f"class: {signal.signal_class}",
            f"tier: {signal.tier}",
            f"event date: {event_date}",
            f"evidence: {', '.join(signal.evidence_ids) or 'none'}",
        )
        blocks.append(
            UntrustedBlock(subject_key, SIGNAL_SOURCE, signal.id, mask("\n".join(lines)))
        )
    text = f"name: {_known(contact.name)}\nrole: {_known(contact.role)}"
    blocks.append(UntrustedBlock(subject_key, CONTACT_SOURCE, CONTACT_ID, mask(text)))
    return blocks


# The proposal -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProposedClaim:
    """One claim of the proposal, as the model gave it."""

    span: str
    evidence_id: str | None
    fact_id: str | None
    fact_version: int | None


@dataclass(frozen=True, slots=True)
class Proposal:
    """The model's Step 1 proposal; see the module docstring."""

    subject: str
    body: str
    claims: tuple[ProposedClaim, ...]
    cited_signal_id: str | None
    explanation: str


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def read_proposal(parsed: Mapping[str, Any]) -> Proposal:
    """The proposal of a validated ``DraftSequenceV1`` answer."""
    if not isinstance(parsed, Mapping):
        raise TypeError("read_proposal takes the parsed answer")
    claims = []
    for row in parsed.get("claims") or ():
        version = row.get("fact_version")
        claims.append(
            ProposedClaim(
                span=row.get("span") if isinstance(row.get("span"), str) else "",
                evidence_id=_text(row.get("evidence_id")),
                fact_id=_text(row.get("fact_id")),
                fact_version=(
                    version
                    if isinstance(version, int) and not isinstance(version, bool)
                    else None
                ),
            )
        )
    subject = parsed.get("subject")
    body = parsed.get("body")
    explanation = parsed.get("explanation")
    return Proposal(
        subject=subject if isinstance(subject, str) else "",
        body=body if isinstance(body, str) else "",
        claims=tuple(claims),
        cited_signal_id=_text(parsed.get("cited_signal_id")),
        explanation=explanation if isinstance(explanation, str) else "",
    )


def _cites(
    claim: ProposedClaim, evidence_ids: Collection[str], facts: Collection[tuple[str, int]]
) -> bool:
    """Whether ``claim`` cites exactly one input Evidence id or input fact version."""
    has_fact = claim.fact_id is not None or claim.fact_version is not None
    if claim.evidence_id is not None:
        return not has_fact and claim.evidence_id in evidence_ids
    if claim.fact_id is None or claim.fact_version is None:
        return False
    return (claim.fact_id, claim.fact_version) in facts


def validate_proposal(
    proposal: Proposal,
    *,
    evidence_ids: Collection[str],
    facts: Collection[tuple[str, int]],
    live_events: Collection[str],
) -> bool:
    """Whether code accepts ``proposal``; see the module docstring."""
    if not isinstance(proposal, Proposal):
        raise TypeError("validate_proposal takes a Proposal")
    if not proposal.subject.strip() or not proposal.body.strip():
        return False
    if not all(_cites(claim, evidence_ids, facts) for claim in proposal.claims):
        return False
    return proposal.cited_signal_id is not None and proposal.cited_signal_id in live_events


def claims_of(proposal: Proposal) -> tuple[Claim, ...]:
    """The proposal's claims as ``grounding_rules.Claim``\\ s."""
    return tuple(
        Claim(
            claim.span,
            claim.evidence_id,
            None
            if claim.fact_id is None or claim.fact_version is None
            else (claim.fact_id, claim.fact_version),
        )
        for claim in proposal.claims
    )


def citations_of(
    proposal: Proposal,
) -> tuple[tuple[str, ...], tuple[tuple[str, int], ...]]:
    """The Evidence ids and fact versions the claims cite, each once, in first-appearance
    order."""
    evidence: dict[str, None] = {}
    facts: dict[tuple[str, int], None] = {}
    for claim in proposal.claims:
        if claim.evidence_id is not None:
            evidence.setdefault(claim.evidence_id)
        if claim.fact_id is not None and claim.fact_version is not None:
            facts.setdefault((claim.fact_id, claim.fact_version))
    return tuple(evidence), tuple(facts)


# Step 1 by schema version (Story 4.18) ------------------------------------------------------


def _claims(rows: object) -> tuple[ProposedClaim, ...]:
    claims = []
    for row in rows if isinstance(rows, list | tuple) else ():
        if not isinstance(row, Mapping):
            continue
        claims.append(
            ProposedClaim(
                span=row.get("span") if isinstance(row.get("span"), str) else "",
                evidence_id=_text(row.get("evidence_id")),
                fact_id=_text(row.get("fact_id")),
                fact_version=_int(row.get("fact_version")),
            )
        )
    return tuple(claims)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def read_step_1(parsed: Mapping[str, Any], schema_version: int) -> Proposal | None:
    """The Step 1 proposal of a validated ``draft_sequence`` answer, read by the tuple's schema
    version: schema 1 as ``read_proposal``; schema 2 needs exactly one ``steps`` entry, with
    ``step`` 1 (its angle fact is ignored); anything else is ``None`` (discarded)."""
    if not isinstance(parsed, Mapping):
        raise TypeError("read_step_1 takes the parsed answer")
    if schema_version == 1:
        return read_proposal(parsed)
    if schema_version != FOLLOW_UP_SCHEMA:
        return None
    steps = parsed.get("steps")
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], Mapping):
        return None
    only = steps[0]
    if _int(only.get("step")) != FIRST_STEP:
        return None
    subject, body, explanation = only.get("subject"), only.get("body"), parsed.get("explanation")
    return Proposal(
        subject=subject if isinstance(subject, str) else "",
        body=body if isinstance(body, str) else "",
        claims=_claims(only.get("claims")),
        cited_signal_id=_text(parsed.get("cited_signal_id")),
        explanation=explanation if isinstance(explanation, str) else "",
    )


# The follow-ups (Story 4.18) ----------------------------------------------------------------


def follow_up_framing(
    briefs: Sequence[StepBrief],
    segment: str,
    market: str,
    facts: Iterable[DraftFact],
    angle_notes: Mapping[int, str],
    english_variant: EnglishVariant,
) -> Framing:
    """The follow-up call's trusted framing: a heading naming the Steps to write, then the Step
    2, 3 and 4 briefs (``brief_section``, each with its Segment x Market angle); the allowed
    facts; the note: each Variant angle note for Steps 2 to 4, then the English variant."""
    chosen = tuple(briefs)
    if [brief.step for brief in chosen] != list(FOLLOW_UP_STEPS):
        raise ValueError("follow_up_framing takes the Step 2, 3 and 4 briefs, in order")
    heading = (
        "# Steps to write\n\nWrite Steps 2, 3 and 4 of the Sequence: one email each, in one "
        "answer, in Step order. Each Step brief below governs its own email."
    )
    sections = [heading, *(brief_section(brief, segment, market) for brief in chosen)]
    lines = []
    for step in FOLLOW_UP_STEPS:
        note = angle_notes.get(step)
        if note is not None and note.strip():
            lines.append(f"Campaign angle for Step {step}: {note.strip()}")
    lines.append(f"English: {ENGLISH[EnglishVariant(english_variant)]}")
    return Framing(
        brief=mask("\n\n".join(sections)), facts=facts_section(facts), note=mask("\n".join(lines))
    )


def regulation_steps(briefs: Iterable[StepBrief], segment: str, market: str) -> frozenset[int]:
    """The Steps whose brief has an angle for ``segment`` in ``market``: each must take a
    ``regulation`` fact as its angle."""
    return frozenset(
        brief.step
        for brief in briefs
        if any(
            found.segment == segment and found.market == market for found in brief.segment_angles
        )
    )


def follow_up_blocks(
    subject_key: str,
    step_1_subject: str,
    step_1_body: str,
    evidence: Iterable[DraftEvidence],
    signals: Iterable[DraftSignal],
    contact: DraftContact,
) -> list[UntrustedBlock]:
    """The follow-up call's untrusted blocks: Step 1's current revision (``earlier_step`` /
    ``step_1``: a ``subject`` line, a blank line, then the body; it is model output and may be
    Operator-edited), then ``input_blocks`` exactly as Step 1's."""
    first = UntrustedBlock(
        subject_key,
        EARLIER_STEP_SOURCE,
        STEP_1_BLOCK_ID,
        mask(f"subject: {step_1_subject}\n\n{step_1_body}"),
    )
    return [first, *input_blocks(subject_key, evidence, signals, contact)]


@dataclass(frozen=True, slots=True)
class FollowUp:
    """One follow-up of the model's proposal."""

    step: int
    subject: str
    body: str
    claims: tuple[ProposedClaim, ...]
    angle_fact_id: str | None
    angle_fact_version: int | None

    @property
    def angle(self) -> tuple[str, int] | None:
        """``(angle_fact_id, angle_fact_version)``, or ``None`` when either is missing."""
        if self.angle_fact_id is None or self.angle_fact_version is None:
            return None
        return self.angle_fact_id, self.angle_fact_version


@dataclass(frozen=True, slots=True)
class FollowUpProposal:
    """The model's follow-up proposal (schema 2)."""

    steps: tuple[FollowUp, ...]
    cited_signal_id: str | None
    explanation: str


def read_follow_ups(parsed: Mapping[str, Any]) -> FollowUpProposal:
    """The follow-up proposal of a validated ``DraftSequenceV2`` answer."""
    if not isinstance(parsed, Mapping):
        raise TypeError("read_follow_ups takes the parsed answer")
    steps = []
    rows = parsed.get("steps")
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, Mapping):
            continue
        step = _int(row.get("step"))
        subject, body = row.get("subject"), row.get("body")
        steps.append(
            FollowUp(
                step=-1 if step is None else step,
                subject=subject if isinstance(subject, str) else "",
                body=body if isinstance(body, str) else "",
                claims=_claims(row.get("claims")),
                angle_fact_id=_text(row.get("angle_fact_id")),
                angle_fact_version=_int(row.get("angle_fact_version")),
            )
        )
    explanation = parsed.get("explanation")
    return FollowUpProposal(
        steps=tuple(steps),
        cited_signal_id=_text(parsed.get("cited_signal_id")),
        explanation=explanation if isinstance(explanation, str) else "",
    )


def validate_follow_ups(
    proposal: FollowUpProposal,
    *,
    evidence_ids: Collection[str],
    facts: Collection[tuple[str, int]],
    step_1_fact_ids: Collection[str],
    regulation_steps: Collection[int],
    regulation_facts: Collection[tuple[str, int]],
) -> bool:
    """Whether code accepts the follow-ups (AD-18): exactly Steps 2, 3 and 4, each once; every
    body non-empty after strip; every claim cites exactly one input Evidence id or input fact
    version; every Step has an angle fact that is an input fact version, whose fact id is none
    Step 1's current revision cites and none another follow-up takes; and a Step in
    ``regulation_steps`` takes a fact version in ``regulation_facts``. The caller also checks
    each body with Step 1's subject (``api._check_text``). Word limits and the other rules are
    ``grounding_rules``' (flagged, not discarded)."""
    if not isinstance(proposal, FollowUpProposal):
        raise TypeError("validate_follow_ups takes a FollowUpProposal")
    if sorted(step.step for step in proposal.steps) != list(FOLLOW_UP_STEPS):
        return False
    taken: set[str] = set(step_1_fact_ids)
    for step in sorted(proposal.steps, key=lambda found: found.step):
        if not step.body.strip():
            return False
        if not all(_cites(claim, evidence_ids, facts) for claim in step.claims):
            return False
        angle = step.angle
        if angle is None or angle not in facts or angle[0] in taken:
            return False
        if step.step in regulation_steps and angle not in regulation_facts:
            return False
        taken.add(angle[0])
    return True


def _as_proposal(step: FollowUp) -> Proposal:
    return Proposal(step.subject, step.body, step.claims, None, "")


def follow_up_citations(
    step: FollowUp,
) -> tuple[tuple[str, ...], tuple[tuple[str, int], ...]]:
    """A follow-up revision's traceability: the Evidence ids and fact versions its claims cite,
    each once, in first-appearance order, then its angle fact when no claim cites it (so a later
    Step's ``repeated_angle`` rule sees it)."""
    evidence, facts = citations_of(_as_proposal(step))
    angle = step.angle
    if angle is not None and angle not in facts:
        facts = (*facts, angle)
    return evidence, facts


def follow_up_claims(step: FollowUp) -> tuple[Claim, ...]:
    """A follow-up's claims as ``grounding_rules.Claim``\\ s."""
    return claims_of(_as_proposal(step))


def follow_up_fingerprint(
    route_tuple_key: str,
    step_1_draft_id: str,
    step_1_rev: int,
    evidence_ids: Iterable[str],
    facts: Iterable[tuple[str, int]],
) -> Fingerprint:
    """What a follow-up request is built from (``ai_batch.Fingerprint``): the tuple key; Step
    1's ``(draft id, current_rev)``; each live Evidence id sent; each allowed ``(fact_id,
    version)``. Each input id is prefixed by its kind, so no two kinds collide."""
    inputs: list[tuple[str, str | int]] = [(f"step_1:{step_1_draft_id}", step_1_rev)]
    inputs += [(f"evidence:{found}", 0) for found in evidence_ids]
    inputs += [(f"fact:{fact_id}", version) for fact_id, version in facts]
    return Fingerprint(route_tuple_key, tuple(inputs))


# Regenerating one Step (Story 4.20) ---------------------------------------------------------


def check_instruction(instruction: object) -> str | None:
    """The Operator's regenerate instruction, stripped, or ``None`` when absent or blank; see
    the module docstring."""
    if instruction is None:
        return None
    if not isinstance(instruction, str):
        raise TypeError("the instruction must be a str or None")
    chosen = instruction.strip()
    if not chosen:
        return None
    if len(chosen) > MAX_INSTRUCTION:
        raise ValueError(f"the instruction must be at most {MAX_INSTRUCTION} characters")
    if any(unicodedata.category(character) in REFUSED_CATEGORIES for character in chosen):
        raise ValueError("the instruction must be one line without control characters")
    return chosen


def regenerate_framing(
    brief: StepBrief,
    segment: str,
    market: str,
    facts: Iterable[DraftFact],
    angle_note: str | None,
    english_variant: EnglishVariant,
    instruction: str | None,
) -> Framing:
    """A regenerate's trusted framing: ``framing``'s, with the masked instruction."""
    base = framing(brief, segment, market, facts, angle_note, english_variant)
    return replace(base, instruction="" if instruction is None else mask(instruction))


def step_block_id(step: int) -> str:
    """``step_<n>``: the block id of Step ``step``'s email."""
    return f"step_{step}"


@dataclass(frozen=True, slots=True)
class RegenStep:
    """Another Step's current email, as a regenerate shows it."""

    step: int
    subject: str
    body: str


def regenerate_blocks(
    subject_key: str,
    step: int,
    current_subject: str,
    current_body: str,
    other_steps: Iterable[RegenStep],
    evidence: Iterable[DraftEvidence],
    signals: Iterable[DraftSignal],
    contact: DraftContact,
) -> list[UntrustedBlock]:
    """A regenerate's untrusted blocks; see the module docstring."""
    blocks = [
        UntrustedBlock(
            subject_key,
            CURRENT_DRAFT_SOURCE,
            step_block_id(step),
            mask(f"subject: {current_subject}\n\n{current_body}"),
        )
    ]
    if step != FIRST_STEP:
        blocks += [
            UntrustedBlock(
                subject_key,
                EARLIER_STEP_SOURCE,
                step_block_id(other.step),
                mask(f"subject: {other.subject}\n\n{other.body}"),
            )
            for other in sorted(other_steps, key=lambda found: found.step)
            if other.step != step
        ]
    return [*blocks, *input_blocks(subject_key, evidence, signals, contact)]


def read_follow_up(parsed: Mapping[str, Any], step: int) -> FollowUp | None:
    """The one follow-up of a regenerate's validated ``DraftSequenceV2`` answer: exactly one
    ``steps`` entry, for ``step``; else ``None``."""
    proposal = read_follow_ups(parsed)
    if len(proposal.steps) != 1 or proposal.steps[0].step != step:
        return None
    rows = parsed.get("steps")
    if not isinstance(rows, list) or len(rows) != 1:
        return None
    return proposal.steps[0]


def validate_follow_up(
    found: FollowUp,
    *,
    evidence_ids: Collection[str],
    facts: Collection[tuple[str, int]],
    other_fact_ids: Collection[str],
    needs_regulation: bool,
    regulation_facts: Collection[tuple[str, int]],
) -> bool:
    """Whether code accepts one regenerated follow-up; see the module docstring."""
    if not isinstance(found, FollowUp):
        raise TypeError("validate_follow_up takes a FollowUp")
    if not found.body.strip():
        return False
    if not all(_cites(claim, evidence_ids, facts) for claim in found.claims):
        return False
    angle = found.angle
    if angle is None or angle not in facts or angle[0] in other_fact_ids:
        return False
    return not needs_regulation or angle in regulation_facts


# The deadline (Story 4.18) ------------------------------------------------------------------


def next_slot(after: datetime, at: time, timezone: str) -> datetime:
    """The earliest UTC instant after ``after`` whose local time in ``timezone`` is ``at``."""
    if not isinstance(after, datetime) or after.utcoffset() is None:
        raise TypeError("after must be an aware datetime")
    if not isinstance(at, time):
        raise TypeError("at must be a datetime.time")
    zone = ZoneInfo(timezone)
    day = after.astimezone(zone).date()
    slot = datetime.combine(day, at, tzinfo=zone).astimezone(UTC)
    while slot <= after:
        day += timedelta(days=1)
        slot = datetime.combine(day, at, tzinfo=zone).astimezone(UTC)
    return slot


def draft_deadline_of(
    created_at: datetime, submit_at: time, deadline: time, timezone: str
) -> datetime:
    """An Enrollment's follow-up drafting deadline: the first ``deadline`` (``draft_deadline``)
    after the first ``submit_at`` (``bulk_ai_submit_at``) slot after its ``created_at``, both
    local times in ``timezone``."""
    return next_slot(next_slot(created_at, submit_at, timezone), deadline, timezone)
