"""Story 4.20's pure regenerate helpers in ``nextron.domain.step_drafting``: the instruction's
checks, the framing with the masked instruction, the ``current_draft`` and ``earlier_step``
blocks, and the one-follow-up read and validation."""

from typing import Any, Final

import pytest

from nextron.domain import seed_yaml
from nextron.domain.ai import check_blocks
from nextron.domain.markets import EnglishVariant
from nextron.domain.sequences import validate_sequences
from nextron.domain.step_drafting import (
    MAX_INSTRUCTION,
    REGENERATE_PROMPTS,
    DraftContact,
    DraftEvidence,
    DraftFact,
    DraftSignal,
    RegenStep,
    check_instruction,
    framing,
    read_follow_up,
    regenerate_blocks,
    regenerate_framing,
    validate_follow_up,
)
from tests.contracts.support import ENGINE_ROOT

LEAD: Final = "lead:01JD3QZ8X5V9K2M7N4P6R8T0WY"
EVIDENCE: Final = "01JD3QZ8X5V9K2M7N4P6R8T0E1"
SIGNAL: Final = "01JD3QZ8X5V9K2M7N4P6R8T0S1"
[SEQUENCE] = validate_sequences(
    seed_yaml.parse((ENGINE_ROOT / "seed" / "sequences.yaml").read_text(encoding="utf-8"))
)
ABA: Final = ("aba_formal_opinion_512", 1)
WISH: Final = ("wish_wheels", 1)
FACTS: Final = frozenset({ABA, WISH, ("legal_ai", 1)})
REGULATION: Final = frozenset({ABA})


def test_only_v3_regenerates() -> None:
    assert frozenset({"v3"}) == REGENERATE_PROMPTS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("   ", None), ("  shorter  ", "shorter"), ("x" * 200, "x" * 200)],
)
def test_an_instruction_is_stripped_and_blank_is_none(
    raw: str | None, expected: str | None
) -> None:
    assert check_instruction(raw) == expected


@pytest.mark.parametrize("raw", ["x" * (MAX_INSTRUCTION + 1), "shorter\nplease", "tab\there"])
def test_a_long_or_multi_line_instruction_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="instruction"):
        check_instruction(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "shorter please",  # line separator (Zl)
        "shorter please",  # paragraph separator (Zp)
        "warmer ‮esrever",  # right-to-left override (Cf)
        "war​mer",  # zero-width space (Cf)
        "war‍mer",  # zero-width joiner (Cf)
        "﻿warmer",  # byte-order mark (Cf)
    ],
)
def test_a_separator_or_format_character_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="instruction"):
        check_instruction(raw)


def test_a_non_str_instruction_is_a_type_error() -> None:
    with pytest.raises(TypeError, match="instruction"):
        check_instruction(5)


def test_the_regenerate_framing_is_the_steps_framing_with_the_masked_instruction() -> None:
    facts = (DraftFact("legal_ai", 1, "We build legal AI."),)
    base = framing(SEQUENCE.steps[1], "law_private_ai", "uk", facts, "Lead", EnglishVariant.EN_GB)

    found = regenerate_framing(
        SEQUENCE.steps[1],
        "law_private_ai",
        "uk",
        facts,
        "Lead",
        EnglishVariant.EN_GB,
        "write to zelda@example.com or 020 7946 0958",
    )

    assert (found.brief, found.facts, found.note) == (base.brief, base.facts, base.note)
    assert found.brief.startswith("# Step brief\n\nThis email is Step 2 of the Sequence.")
    assert "Angle for this Segment and Market:" in found.brief
    assert found.instruction == "write to [email] or [phone]"
    none = regenerate_framing(
        SEQUENCE.steps[0], "law_private_ai", "uk", facts, None, EnglishVariant.EN_US, None
    )
    assert none.instruction == ""


EV: Final = (DraftEvidence(EVIDENCE, "quote", "We are hiring."),)
SIG: Final = (DraftSignal(SIGNAL, "hiring", "event", 1, None, (EVIDENCE,)),)
WHO: Final = DraftContact("Zelda", "Partner")
OTHERS: Final = (
    RegenStep(4, "Subject", "Step 4 body"),
    RegenStep(1, "First subject", "Step 1 body, mail me at a@b.co"),
    RegenStep(3, "Subject", "Step 3 body"),
)


def test_a_step_1_regenerate_sends_the_current_draft_then_the_leads_blocks() -> None:
    blocks = regenerate_blocks(LEAD, 1, "Old subject", "Old body", OTHERS, EV, SIG, WHO)

    assert [(b.source, b.block_id) for b in blocks] == [
        ("current_draft", "step_1"),
        ("evidence", EVIDENCE),
        ("signal", SIGNAL),
        ("contact", "contact"),
    ]
    assert blocks[0].text == "subject: Old subject\n\nOld body"
    assert check_blocks(blocks) == LEAD


def test_a_follow_up_regenerate_adds_every_other_step_by_step_masked() -> None:
    blocks = regenerate_blocks(LEAD, 3, "Re", "Step 3 now", (*OTHERS,), EV, SIG, WHO)

    assert [(b.source, b.block_id) for b in blocks][:4] == [
        ("current_draft", "step_3"),
        ("earlier_step", "step_1"),
        ("earlier_step", "step_4"),
        ("evidence", EVIDENCE),
    ]
    assert blocks[1].text == "subject: First subject\n\nStep 1 body, mail me at [email]"
    assert check_blocks(blocks) == LEAD


def answer(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"steps": list(rows), "cited_signal_id": None, "explanation": "x"}


def row(step: int, angle: tuple[str, int] | None = WISH, **changes: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "step": step,
        "subject": "ignored",
        "body": "Hi Zelda,\n\nA case study.\n\nUseful?",
        "claims": []
        if angle is None
        else [
            {"span": "A case study", "evidence_id": None, "fact_id": angle[0], "fact_version": 1}
        ],
        "angle_fact_id": None if angle is None else angle[0],
        "angle_fact_version": None if angle is None else angle[1],
    }
    return found | changes


def test_read_follow_up_takes_exactly_one_entry_for_its_step() -> None:
    found = read_follow_up(answer(row(3)), 3)

    assert found is not None and found.step == 3 and found.angle == WISH
    assert read_follow_up(answer(row(2)), 3) is None
    assert read_follow_up(answer(row(3), row(4)), 3) is None
    assert read_follow_up(answer(), 3) is None
    assert read_follow_up({"steps": [row(3), "junk"]}, 3) is None


def check(found: Any, *, others: frozenset[str] = frozenset(), regulation: bool = False) -> bool:
    return validate_follow_up(
        found,
        evidence_ids=frozenset({EVIDENCE}),
        facts=FACTS,
        other_fact_ids=others,
        needs_regulation=regulation,
        regulation_facts=REGULATION,
    )


def test_a_clean_follow_up_passes() -> None:
    assert check(read_follow_up(answer(row(3)), 3))
    assert check(read_follow_up(answer(row(2, ABA)), 2), regulation=True)


@pytest.mark.parametrize(
    ("found", "others", "regulation"),
    [
        (row(3, None), frozenset(), False),  # no angle
        (row(3, ("unknown", 1)), frozenset(), False),  # not an input fact
        (row(3), frozenset({"wish_wheels"}), False),  # another Step cites it
        (row(2), frozenset(), True),  # its brief needs a regulation angle
        (row(3, body="   "), frozenset(), False),  # empty body
        (
            row(3, claims=[{"span": "x", "evidence_id": "nope", "fact_id": None}]),
            frozenset(),
            False,
        ),  # a claim citing nothing given
    ],
)
def test_each_bad_follow_up_is_refused(
    found: dict[str, Any], others: frozenset[str], regulation: bool
) -> None:
    read = read_follow_up(answer(found), found["step"])
    assert read is not None
    assert not check(read, others=others, regulation=regulation)
