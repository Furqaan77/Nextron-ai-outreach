"""The AI grounding check (FR-21, AD-7, AD-8, AD-18; Story 4.14): the input the
``grounding_check`` route reads, how its answer becomes flags, and how those combine with the
deterministic rules (``nextron.domain.grounding_rules``). Standard library only, plus
``nextron.domain``.

**Routes and job.** ``GROUNDING_CHECK_ROUTE`` is ``grounding_check`` (Haiku);
``GROUNDING_FALLBACK_ROUTE`` is ``grounding_check_fallback``, the tested Opus fallback a refused,
invalid or truncated Haiku answer re-runs on once. ``CHECK_GROUNDING_JOB`` is
``check_grounding``: scope the Draft id, discriminator the revision number, payload ``{"v": 1,
"draft_id": <ULID>, "rev": <int>}`` (``DRAFT_ID_FIELD``, ``REV_FIELD``).

**The input.** ``GroundingInput(step, market, subject, body, evidence, facts, earlier_steps)``
is one revision to check: the Step number, the Market code, the subject and the body (without
signature or footer), the cited Evidence (``GroundingEvidence(id, kind, text)``), the cited
facts (``GroundingFact(fact_id, version, wording)``) and the earlier Steps' current text
(``GroundingEarlierStep(step, subject, body)``). ``input_blocks(subject_key, given)`` turns it
into the call's ``UntrustedBlock``\\ s, all of the one subject ``subject_key`` (``lead:<id>``):

- ``context`` / ``context``: ``step: <n>`` and ``market: <code>`` lines;
- ``draft`` / ``subject`` and ``draft`` / ``body``;
- ``evidence`` / the Evidence id: ``kind: <kind>``, a blank line, then the text;
- ``fact`` / ``<fact_id>.v<version>``: the wording;
- ``earlier_step`` / ``step<n>``: ``Step <n>``, ``Subject: <subject>``, a blank line, the body.

Nothing else goes in: no Settings value, email address or phone number beyond the Draft's own
text. An id that repeats (an Evidence id equal to another block's) raises ``ValueError``
(``nextron.domain.ai.check_blocks``), so one Lead's call never mixes blocks up.

**The answer.** ``read_answer(parsed, subject, body) -> AiAnswer(claims, flags)`` reads a
``GroundingCheckV1`` answer (already schema-validated): each claim becomes a
``grounding_rules.Claim(span, evidence_id, (fact_id, fact_version))`` (the fact only when both
are given) for the rules to re-check, and flags, in answer order:

- a claim that cites no Evidence and no fact: ``uncited_claim``;
- else a claim the answer marks ``supported`` false: ``unsupported_claim``;
- each pattern flag: its ``code`` (``PATTERN_CODES``, the six ``GroundingPattern`` values the
  schema allows).

Each flag's ``span`` is the answer's span when it is a substring of the subject or the body,
else ``""``; its explanation is the answer's (stripped, cut to ``MAX_EXPLANATION``), or a fixed
text when that is blank. Every AI flag has ``source`` ``ai``.

**Fail closed.** ``unavailable(reason)`` is the one ``checker_unavailable`` flag (span ``""``):
no active tuple, a failed or unreachable call, a refused, invalid or truncated answer with no
usable fallback, or a subject no longer contactable. ``UNAVAILABLE_*`` are its explanations.

**Combining.** ``combine(rules_flags, ai_flags, *, checked)`` is the stored result: every rules
flag (``source`` ``rules``) then every AI flag; ``pass`` only when ``checked`` (a tuple served a
usable answer) and there is no flag, else ``fail`` (``pending`` never comes out of it).
``CheckedFlag(code, span, explanation, source)`` is one stored flag; ``code`` is a
``grounding_rules.RULE_IDS`` id for ``rules`` and one of ``AI_CODES`` for ``ai``.

**The Grounding set's scorer** (AD-12; ``nextron.app.playbook.api.SCORERS``):
``case_input(case)`` is a Grounding case's revision (all its Evidence, facts and earlier Steps), so a run reads
exactly what the job reads, through the same ``input_blocks``; ``grounding_inputs(case, number,
segments)`` its blocks for ``lead:case<number>`` at ``bulk`` urgency; ``score_grounding(case,
result)`` its ``GroundingOutcome`` (flagged when the pinned ``expected.rules`` is non-empty, the
answer has any flag, or there is no ``LlmOk`` answer: fail closed) and ``CaseResult`` (passes
when only a clean control is unflagged); ``grounding_set_metrics(route, outcomes)`` is
``grounding_metrics``.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from nextron.domain.ai import UntrustedBlock, Urgency
from nextron.domain.ai_test_cases import TestCase
from nextron.domain.ai_test_gate import (
    CaseResult,
    GroundingCase,
    GroundingOutcome,
    Metric,
    TestSet,
    grounding_metrics,
)
from nextron.domain.ai_test_sets import GroundingPattern
from nextron.domain.grounding_rules import MAX_EXPLANATION, RULE_IDS, Claim, GroundingStatus

GROUNDING_CHECK_ROUTE: Final = "grounding_check"
GROUNDING_FALLBACK_ROUTE: Final = "grounding_check_fallback"
CHECK_GROUNDING_JOB: Final = "check_grounding"
DRAFT_ID_FIELD: Final = "draft_id"
REV_FIELD: Final = "rev"

CHECKER_UNAVAILABLE: Final = "checker_unavailable"
UNCITED_CLAIM: Final = "uncited_claim"
UNSUPPORTED_CLAIM: Final = "unsupported_claim"
PATTERN_CODES: Final = (
    GroundingPattern.FAKE_FAMILIARITY.value,
    GroundingPattern.FALSE_URGENCY.value,
    GroundingPattern.COMPETITOR_CLAIM.value,
    GroundingPattern.TWO_CALLS_TO_ACTION.value,
    GroundingPattern.DECEPTIVE_SUBJECT.value,
    GroundingPattern.REPEATS_EARLIER_STEP.value,
)
AI_CODES: Final = (CHECKER_UNAVAILABLE, UNCITED_CLAIM, UNSUPPORTED_CLAIM, *PATTERN_CODES)

UNAVAILABLE_NO_TUPLE: Final = "No tested grounding check is active, so the Draft can't pass."
UNAVAILABLE_FAILED: Final = "The grounding check didn't answer, so the Draft can't pass."
UNAVAILABLE_NO_ANSWER: Final = (
    "The grounding check refused or gave no usable answer, so the Draft can't pass."
)
UNAVAILABLE_NOT_CONTACTABLE: Final = (
    "The Contact is no longer contactable, so the Draft wasn't checked."
)
UNCITED_TEXT: Final = "The claim cites no Evidence and no approved fact."
UNSUPPORTED_TEXT: Final = "The cited Evidence or fact doesn't support the claim."
PATTERN_TEXT: Final = "The Draft uses a banned pattern."

CONTEXT_SOURCE: Final = "context"
DRAFT_SOURCE: Final = "draft"
EVIDENCE_SOURCE: Final = "evidence"
FACT_SOURCE: Final = "fact"
EARLIER_STEP_SOURCE: Final = "earlier_step"
SUBJECT_ID: Final = "subject"
BODY_ID: Final = "body"


class FlagSource(StrEnum):
    """Where a stored flag came from."""

    RULES = "rules"
    AI = "ai"


@dataclass(frozen=True, slots=True)
class GroundingEvidence:
    """One cited Evidence row as the check reads it."""

    id: str
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class GroundingFact:
    """One cited fact version as the check reads it."""

    fact_id: str
    version: int
    wording: str


@dataclass(frozen=True, slots=True)
class GroundingEarlierStep:
    """An earlier Step's current subject and body."""

    step: int
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class GroundingInput:
    """One revision to check; see the module docstring."""

    step: int
    market: str
    subject: str
    body: str
    evidence: tuple[GroundingEvidence, ...]
    facts: tuple[GroundingFact, ...]
    earlier_steps: tuple[GroundingEarlierStep, ...]


@dataclass(frozen=True, slots=True)
class CheckedFlag:
    """One stored flag: its code, span, explanation and source."""

    code: str
    span: str
    explanation: str
    source: FlagSource

    def __post_init__(self) -> None:
        if not isinstance(self.source, FlagSource):
            raise TypeError("a flag's source must be a FlagSource")
        allowed = RULE_IDS if self.source is FlagSource.RULES else AI_CODES
        if self.code not in allowed:
            raise ValueError("a flag's code must be one of its source's codes")
        if not 0 < len(self.explanation) <= MAX_EXPLANATION:
            raise ValueError(f"a flag's explanation must be 1 to {MAX_EXPLANATION} characters")


@dataclass(frozen=True, slots=True)
class AiAnswer:
    """What an answer gives: the claims for the rules and the AI's flags."""

    claims: tuple[Claim, ...]
    flags: tuple[CheckedFlag, ...]


def fact_block_id(fact_id: str, version: int) -> str:
    """A fact block's id: ``<fact_id>.v<version>``."""
    return f"{fact_id}.v{version}"


def input_blocks(subject_key: str, given: GroundingInput) -> list[UntrustedBlock]:
    """The call's blocks for ``given``; see the module docstring."""
    if not isinstance(given, GroundingInput):
        raise TypeError("input_blocks takes a GroundingInput")
    blocks = [
        UntrustedBlock(
            subject_key,
            CONTEXT_SOURCE,
            CONTEXT_SOURCE,
            f"step: {given.step}\nmarket: {given.market}",
        ),
        UntrustedBlock(subject_key, DRAFT_SOURCE, SUBJECT_ID, given.subject),
        UntrustedBlock(subject_key, DRAFT_SOURCE, BODY_ID, given.body),
    ]
    blocks.extend(
        UntrustedBlock(subject_key, EVIDENCE_SOURCE, row.id, f"kind: {row.kind}\n\n{row.text}")
        for row in given.evidence
    )
    blocks.extend(
        UntrustedBlock(
            subject_key, FACT_SOURCE, fact_block_id(row.fact_id, row.version), row.wording
        )
        for row in given.facts
    )
    blocks.extend(
        UntrustedBlock(
            subject_key,
            EARLIER_STEP_SOURCE,
            f"step{row.step}",
            f"Step {row.step}\nSubject: {row.subject}\n\n{row.body}",
        )
        for row in given.earlier_steps
    )
    return blocks


def _span(value: object, subject: str, body: str) -> str:
    """``value`` when it is a non-empty substring of the subject or body, else ``""``."""
    if isinstance(value, str) and value and (value in subject or value in body):
        return value
    return ""


def _explanation(value: object, fallback: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text[:MAX_EXPLANATION] if text else fallback


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def read_answer(parsed: Mapping[str, Any], subject: str, body: str) -> AiAnswer:
    """The claims and AI flags of a validated ``GroundingCheckV1`` answer; see the module
    docstring."""
    if not isinstance(parsed, Mapping):
        raise TypeError("read_answer takes the parsed answer")
    claims: list[Claim] = []
    flags: list[CheckedFlag] = []
    for row in parsed.get("claims") or ():
        span_text = row.get("span") if isinstance(row.get("span"), str) else ""
        evidence_id = _text(row.get("evidence_id"))
        fact_id = _text(row.get("fact_id"))
        version = row.get("fact_version")
        fact = (
            (fact_id, version)
            if fact_id is not None and isinstance(version, int) and not isinstance(version, bool)
            else None
        )
        claims.append(Claim(span_text, evidence_id, fact))
        span = _span(span_text, subject, body)
        if evidence_id is None and fact is None:
            flags.append(
                CheckedFlag(
                    UNCITED_CLAIM,
                    span,
                    _explanation(row.get("explanation"), UNCITED_TEXT),
                    FlagSource.AI,
                )
            )
        elif row.get("supported") is not True:
            flags.append(
                CheckedFlag(
                    UNSUPPORTED_CLAIM,
                    span,
                    _explanation(row.get("explanation"), UNSUPPORTED_TEXT),
                    FlagSource.AI,
                )
            )
    for row in parsed.get("flags") or ():
        code = row.get("code")
        if code not in PATTERN_CODES:  # pragma: no cover - the schema allows only these
            continue
        flags.append(
            CheckedFlag(
                code,
                _span(row.get("span"), subject, body),
                _explanation(row.get("explanation"), PATTERN_TEXT),
                FlagSource.AI,
            )
        )
    return AiAnswer(tuple(claims), tuple(flags))


def unavailable(reason: str) -> CheckedFlag:
    """The fail-closed ``checker_unavailable`` flag, explained by ``reason``."""
    return CheckedFlag(CHECKER_UNAVAILABLE, "", reason, FlagSource.AI)


def rules_flags(flags: Iterable[Any]) -> tuple[CheckedFlag, ...]:
    """``grounding_rules.Flag``\\ s as stored ``rules`` flags."""
    return tuple(
        CheckedFlag(flag.rule, flag.span, flag.explanation, FlagSource.RULES) for flag in flags
    )


def combine(
    rules: Sequence[CheckedFlag], ai: Sequence[CheckedFlag], *, checked: bool
) -> tuple[GroundingStatus, tuple[CheckedFlag, ...]]:
    """The stored status and flags: rules flags, then AI flags; ``pass`` only when ``checked``
    and nothing flagged."""
    flags = (*rules, *ai)
    if checked and not flags:
        return GroundingStatus.PASS, ()
    if not flags:  # fail closed: an unchecked revision never passes
        flags = (unavailable(UNAVAILABLE_FAILED),)
    return GroundingStatus.FAIL, flags


# The Grounding set's scorer (Story 4.14) ----------------------------------------------------------


def case_input(case: TestCase) -> GroundingInput:
    """A Grounding case's revision as the check reads it: every Evidence item, fact and earlier
    Step of the case (the case's citation set, as Story 4.13's fixture test builds it)."""
    given = case.input
    return GroundingInput(
        step=int(given["step"]),
        market=str(given["market"]),
        subject=str(given["draft"]["subject"]),
        body=str(given["draft"]["body"]),
        evidence=tuple(
            GroundingEvidence(str(row["id"]), str(row["kind"]), str(row["text"]))
            for row in given["evidence"]
        ),
        facts=tuple(
            GroundingFact(str(row["fact_id"]), int(row["version"]), str(row["wording"]))
            for row in given["facts"]
        ),
        earlier_steps=tuple(
            GroundingEarlierStep(int(row["step"]), str(row["subject"]), str(row["body"]))
            for row in sorted(given["earlier_steps"], key=lambda row: int(row["step"]))
        ),
    )


def grounding_inputs(
    case: TestCase, number: int, segments: Sequence[object]
) -> tuple[list[UntrustedBlock], Urgency]:
    """The blocks and urgency of Grounding case ``number`` (1-based): ``input_blocks`` of
    ``case_input(case)`` for the subject ``lead:case<number>``, at ``bulk`` urgency."""
    return input_blocks(f"lead:case{number}", case_input(case)), Urgency.BULK


def score_grounding(
    case: TestCase, result: Mapping[str, Any] | str
) -> tuple[GroundingOutcome, CaseResult]:
    """Score one Grounding case's answer. The case counts as flagged when its pinned
    ``expected.rules`` is non-empty (Story 4.13's fixture test proves it equals
    ``grounding_rules.check``), or the answer has any flag (``read_answer``: an uncited or
    unsupported claim, or a pattern flag); an answer that isn't ``LlmOk`` (its result's type
    name) counts as flagged (fail closed). It passes when a clean control isn't flagged and any
    other case is."""
    if not isinstance(case, TestCase) or case.test_set is not TestSet.GROUNDING:
        raise TypeError("score_grounding takes a Grounding TestCase")
    kind = GroundingCase(case.expected["kind"])
    rules = tuple(str(rule) for rule in case.expected["rules"])
    want = kind is not GroundingCase.CLEAN_CONTROL
    expected = "flagged" if want else "not flagged"
    if not isinstance(result, Mapping):
        if not isinstance(result, str):
            raise TypeError("result must be the parsed answer or the result's type name")
        return GroundingOutcome(kind, True), CaseResult(
            case.case_key, want, expected, f"flagged: no answer ({result})"
        )
    given = case_input(case)
    answer = read_answer(result, given.subject, given.body)
    codes = (*rules, *(flag.code for flag in answer.flags))
    flagged = bool(codes)
    got = "flagged: " + ", ".join(dict.fromkeys(codes)) if flagged else "not flagged"
    return GroundingOutcome(kind, flagged), CaseResult(
        case.case_key, flagged == want, expected, got
    )


def grounding_set_metrics(
    route: str, outcomes: Sequence[GroundingOutcome]
) -> tuple[Metric, ...]:
    """The Grounding set's metrics (``grounding_metrics``), for either grounding route."""
    return grounding_metrics(outcomes)
