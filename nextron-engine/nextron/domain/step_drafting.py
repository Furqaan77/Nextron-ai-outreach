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
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

from nextron.domain.ai import Framing, UntrustedBlock
from nextron.domain.grounding_rules import Claim
from nextron.domain.markets import EnglishVariant
from nextron.domain.pseudonymise import mask_contact_details
from nextron.domain.sequences import StepBrief, WordComparison, max_words

DRAFT_SEQUENCE_ROUTE: Final = "draft_sequence"
DRAFT_STEP_JOB: Final = "draft_step"
ENROLLMENT_ID_FIELD: Final = "enrollment_id"
STEP_FIELD: Final = "step"
MAX_REASON: Final = 32

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

