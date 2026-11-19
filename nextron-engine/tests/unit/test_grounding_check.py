"""The AI grounding check's pure core (``nextron.domain.grounding_check``, Story 4.14): the input
blocks, how an answer becomes claims and flags, the fail-closed combine, and the Grounding set's
scorer over the shipped cases."""

import json
from typing import Any, Final

import pytest

from nextron.adapters.ai.schemas import GROUNDING_CHECK_V1, validate
from nextron.domain.ai import UntrustedBlock, Urgency, check_blocks
from nextron.domain.ai_test_cases import TestCase
from nextron.domain.ai_test_gate import GroundingCase, TestSet, grounding_metrics
from nextron.domain.ai_test_sets import GroundingPattern, set_case
from nextron.domain.grounding_check import (
    AI_CODES,
    CHECKER_UNAVAILABLE,
    PATTERN_CODES,
    UNAVAILABLE_NO_TUPLE,
    UNCITED_CLAIM,
    UNSUPPORTED_CLAIM,
    CheckedFlag,
    FlagSource,
    GroundingEarlierStep,
    GroundingEvidence,
    GroundingFact,
    GroundingInput,
    case_input,
    combine,
    grounding_inputs,
    grounding_set_metrics,
    input_blocks,
    read_answer,
    rules_flags,
    score_grounding,
    unavailable,
)
from nextron.domain.grounding_rules import Claim, Flag, GroundingStatus
from tests.contracts.support import ENGINE_ROOT

LEAD: Final = "lead:01JD3QZ8X5V9K2M7N4P6R8T0WY"
SUBJECT: Final = "your intake role"
BODY: Final = "Hi Zelda,\n\nSaw the firm is hiring an intake coordinator.\n\nWorth a look?"
HIRING: Final = "the firm is hiring an intake coordinator"
CASES: Final = sorted((ENGINE_ROOT / "tests" / "ai_sets" / "grounding").glob("*.json"))


def given(**changes: Any) -> GroundingInput:
    fields: dict[str, Any] = {
        "step": 2,
        "market": "us",
        "subject": SUBJECT,
        "body": BODY,
        "evidence": (GroundingEvidence("e1", "quote", "We are hiring an intake coordinator."),),
        "facts": (GroundingFact("legal_ai", 1, "Built an AI legal assistant platform."),),
        "earlier_steps": (GroundingEarlierStep(1, "first", "Step 1 body"),),
    }
    return GroundingInput(**(fields | changes))


def claim(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "span": HIRING,
        "evidence_id": "e1",
        "fact_id": None,
        "fact_version": None,
        "supported": True,
        "explanation": "The careers page says so.",
    }
    return row | changes


def answer(claims: list[dict[str, Any]], flags: list[dict[str, Any]] | None = None) -> Any:
    validated = validate(GROUNDING_CHECK_V1, {"claims": claims, "flags": flags or []})
    assert validated is not None
    return validated


# The input --------------------------------------------------------------------------------------


def test_the_blocks_hold_the_context_the_draft_and_each_cited_item() -> None:
    blocks = input_blocks(LEAD, given())

    assert [(b.source, b.block_id) for b in blocks] == [
        ("context", "context"),
        ("draft", "subject"),
        ("draft", "body"),
        ("evidence", "e1"),
        ("fact", "legal_ai.v1"),
        ("earlier_step", "step1"),
    ]
    assert check_blocks(blocks) == LEAD  # one Lead only
    texts = {b.block_id: b.text for b in blocks}
    assert texts["context"] == "step: 2\nmarket: us"
    assert (texts["subject"], texts["body"]) == (SUBJECT, BODY)
    assert texts["e1"] == "kind: quote\n\nWe are hiring an intake coordinator."
    assert texts["legal_ai.v1"] == "Built an AI legal assistant platform."
    assert texts["step1"] == "Step 1\nSubject: first\n\nStep 1 body"


def test_an_evidence_id_that_repeats_another_block_is_refused() -> None:
    blocks = input_blocks(LEAD, given(evidence=(GroundingEvidence("body", "quote", "x"),)))

    with pytest.raises(ValueError, match="repeat"):
        check_blocks(blocks)


def test_input_blocks_takes_only_a_grounding_input() -> None:
    with pytest.raises(TypeError):
        input_blocks(LEAD, {"step": 1})  # type: ignore[arg-type]


# The answer -------------------------------------------------------------------------------------


def test_supported_cited_claims_give_no_flag_and_become_rules_claims() -> None:
    read = read_answer(
        answer(
            [
                claim(),
                claim(span="AI legal", evidence_id=None, fact_id="legal_ai", fact_version=1),
            ]
        ),
        SUBJECT,
        BODY,
    )

    assert read.flags == ()
    assert read.claims == (Claim(HIRING, "e1", None), Claim("AI legal", None, ("legal_ai", 1)))


def test_an_uncited_claim_flags_uncited_and_a_false_one_unsupported() -> None:
    read = read_answer(
        answer(
            [
                claim(evidence_id=None, supported=False, explanation="  "),
                claim(supported=False, explanation="x" * 150),
                claim(evidence_id=None, fact_id="legal_ai", fact_version=None),  # half a fact
            ]
        ),
        SUBJECT,
        BODY,
    )

    assert [(f.code, f.span, f.source) for f in read.flags] == [
        (UNCITED_CLAIM, HIRING, FlagSource.AI),
        (UNSUPPORTED_CLAIM, HIRING, FlagSource.AI),
        (UNCITED_CLAIM, HIRING, FlagSource.AI),
    ]
    assert read.flags[0].explanation == "The claim cites no Evidence and no approved fact."
    assert read.flags[1].explanation == "x" * 150
    assert read.claims[2] == Claim(HIRING, None, None)


@pytest.mark.parametrize("code", PATTERN_CODES)
def test_each_pattern_flag_keeps_its_code(code: str) -> None:
    read = read_answer(
        answer([], [{"code": code.upper(), "span": "Worth a look?", "explanation": "e"}]),
        SUBJECT,
        BODY,
    )

    assert [(f.code, f.span, f.explanation) for f in read.flags] == [(code, "Worth a look?", "e")]


def test_the_pattern_codes_are_grounding_patterns() -> None:
    assert set(PATTERN_CODES) <= {pattern.value for pattern in GroundingPattern}
    assert len(PATTERN_CODES) == 6


def test_a_span_not_in_the_subject_or_body_is_stored_empty() -> None:
    read = read_answer(
        answer(
            [claim(span="words the Draft never says", supported=False)],
            [{"code": "false_urgency", "span": "Act now", "explanation": "e"}],
        ),
        SUBJECT,
        BODY,
    )

    assert [f.span for f in read.flags] == ["", ""]
    assert read.claims[0].text == "words the Draft never says"  # the rules find it nowhere


def test_the_schema_refuses_an_unknown_code_and_a_long_explanation() -> None:
    unknown = {"code": "pressure_tactic", "span": "", "explanation": "e"}
    assert validate(GROUNDING_CHECK_V1, {"claims": [], "flags": [unknown]}) is None
    long = claim(explanation="x" * 201)
    assert validate(GROUNDING_CHECK_V1, {"claims": [long], "flags": []}) is None


# Fail closed and the combine --------------------------------------------------------------------


def test_combine_passes_only_a_checked_revision_with_no_flag() -> None:
    rules = rules_flags((Flag("banned_phrase", "Circling back", "Banned."),))
    ai = (CheckedFlag(UNSUPPORTED_CLAIM, HIRING, "No.", FlagSource.AI),)

    assert combine((), (), checked=True) == (GroundingStatus.PASS, ())
    status, flags = combine(rules, ai, checked=True)
    assert status is GroundingStatus.FAIL
    assert [(f.code, f.source) for f in flags] == [
        ("banned_phrase", FlagSource.RULES),
        (UNSUPPORTED_CLAIM, FlagSource.AI),
    ]
    status, flags = combine((), (), checked=False)  # never a pass without a check
    assert (status, [f.code for f in flags]) == (GroundingStatus.FAIL, [CHECKER_UNAVAILABLE])


def test_unavailable_is_the_one_checker_unavailable_flag() -> None:
    assert unavailable(UNAVAILABLE_NO_TUPLE) == CheckedFlag(
        CHECKER_UNAVAILABLE, "", UNAVAILABLE_NO_TUPLE, FlagSource.AI
    )


@pytest.mark.parametrize(
    ("code", "source"),
    [("banned_phrase", FlagSource.AI), (UNCITED_CLAIM, FlagSource.RULES), ("other", FlagSource.AI)],
)
def test_a_flag_with_a_code_outside_its_source_is_refused(code: str, source: FlagSource) -> None:
    with pytest.raises(ValueError):
        CheckedFlag(code, "", "e", source)


def test_a_flag_needs_a_short_explanation_and_a_source() -> None:
    with pytest.raises(ValueError):
        CheckedFlag(UNCITED_CLAIM, "", "", FlagSource.AI)
    with pytest.raises(ValueError):
        CheckedFlag(UNCITED_CLAIM, "", "x" * 201, FlagSource.AI)
    with pytest.raises(TypeError):
        CheckedFlag(UNCITED_CLAIM, "", "e", "ai")  # type: ignore[arg-type]
    assert set(AI_CODES) == {CHECKER_UNAVAILABLE, UNCITED_CLAIM, UNSUPPORTED_CLAIM, *PATTERN_CODES}


# The Grounding set's scorer ---------------------------------------------------------------------


def load(path: Any) -> TestCase:
    return set_case(TestSet.GROUNDING, path.name, json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def cases() -> list[TestCase]:
    return [load(path) for path in CASES]


def test_the_inputs_are_one_case_s_blocks_at_bulk(cases: list[TestCase]) -> None:
    for number, case in enumerate(cases, start=1):
        blocks, urgency = grounding_inputs(case, number, ())
        assert urgency is Urgency.BULK
        assert check_blocks(blocks) == f"lead:case{number}"
        assert all(isinstance(block, UntrustedBlock) for block in blocks)
        found = case_input(case)
        assert len(blocks) == 3 + len(found.evidence) + len(found.facts) + len(
            found.earlier_steps
        )


def test_a_clean_answer_flags_only_the_cases_with_pinned_rules(cases: list[TestCase]) -> None:
    clean = {"claims": [], "flags": []}
    outcomes = []
    for case in cases:
        outcome, result = score_grounding(case, clean)
        outcomes.append(outcome)
        assert outcome.flagged == bool(case.expected["rules"])
        if case.expected["kind"] == GroundingCase.CLEAN_CONTROL.value:
            assert result.passed and result.got == "not flagged"
    assert grounding_set_metrics("grounding_check_fallback", outcomes) == grounding_metrics(
        outcomes
    )


def test_an_answer_flagging_every_planted_case_passes_the_gate(cases: list[TestCase]) -> None:
    outcomes = []
    for case in cases:
        planted = case.expected["kind"] != GroundingCase.CLEAN_CONTROL.value
        found = (
            {"claims": [claim(span="x", evidence_id=None, supported=False)], "flags": []}
            if planted
            else {"claims": [], "flags": []}
        )
        outcome, result = score_grounding(case, found)
        outcomes.append(outcome)
        assert result.passed
    assert all(metric.passed for metric in grounding_metrics(outcomes))


def test_no_answer_counts_as_flagged(cases: list[TestCase]) -> None:
    clean = next(c for c in cases if c.expected["kind"] == GroundingCase.CLEAN_CONTROL.value)
    banned = next(c for c in cases if c.expected["kind"] == GroundingCase.BANNED_PATTERN.value)

    outcome, result = score_grounding(clean, "LlmRefused")
    assert outcome.flagged and not result.passed
    assert result.got == "flagged: no answer (LlmRefused)"
    outcome, result = score_grounding(banned, "LlmInvalid")
    assert outcome.flagged and result.passed


def test_score_grounding_takes_a_grounding_case_and_an_answer(cases: list[TestCase]) -> None:
    with pytest.raises(TypeError):
        score_grounding("case", {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        score_grounding(cases[0], 3)  # type: ignore[arg-type]
