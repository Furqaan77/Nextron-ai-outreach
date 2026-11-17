"""``nextron.domain.sequences`` (FR-4, FR-71; Story 4.1): the Sequence types, ``max_words``, the
file validation with its exact field paths, rules 1 to 7 (the AC's and the compliance floor),
``check_sequence_references``, the file form round trip, and the regulation detectors
``mentions_aba_512`` and ``mentions_sra_notice`` in ``nextron.domain.segments``. Standard
library, pytest and ``nextron.domain`` only."""

import copy
import dataclasses
from typing import Any

import pytest

from nextron.domain import sequences as sequences_module
from nextron.domain.errors import DomainError
from nextron.domain.segments import (
    mentions_aba_512,
    mentions_sra_notice,
    represents_market_fact,
)
from nextron.domain.sequences import (
    REGULATION_REASON,
    InvalidSequences,
    LinkRule,
    SegmentAngle,
    SequenceDef,
    StepBrief,
    SubjectRule,
    WordComparison,
    check_sequence_references,
    max_words,
    sequence_form,
    sequence_from_form,
    validate_sequences,
)

LONG = "x" * 41  # a value a reason must never repeat
US_ANGLE = "What ABA Formal Opinion 512 means for their intake or document work"
UK_ANGLE = "What the SRA notice means for their intake or document work"
SEGMENT_KEYS = frozenset({"law_private_ai", "ops_automation"})
MARKET_COUNTRIES = {"us": "US", "uk": "GB"}
DEFAULT = "sequences[default]"


def step_1(**changes: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "delay_days": 0,
        "purpose": "Event-led cold email",
        "word_limit": 80,
        "word_comparison": "under",
        "allowed_content": ["One observed Signal", "One sentence on the likely problem"],
        "ask": 'Relevance only, such as "Worth a look?"',
        "link_rule": "footer_only",
        "no_price": True,
        "no_audit": True,
        "subject_rule": {
            "word_limit": 7,
            "word_comparison": "under",
            "guidance": ["About the prospect's world", "Never misleading"],
        },
    }
    form.update(changes)
    return form


def step_2(**changes: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "delay_days": 3,
        "purpose": "New angle",
        "word_limit": 70,
        "word_comparison": "at_most",
        "allowed_content": ["One short Segment insight from Approved Company Facts"],
        "ask": "The relevance question, reworded",
        "link_rule": "footer_only",
        "no_price": True,
        "no_audit": True,
        "segment_angles": [
            {"segment": "law_private_ai", "market": "us", "angle": US_ANGLE},
            {"segment": "law_private_ai", "market": "uk", "angle": UK_ANGLE},
        ],
    }
    form.update(changes)
    return form


def later_step(day: int, **changes: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "delay_days": day,
        "purpose": f"Step on day {day}",
        "word_limit": 50,
        "word_comparison": "at_most",
        "allowed_content": ["One relevant case study or regulatory note"],
        "ask": "Should I close the loop?",
        "link_rule": "footer_only",
        "no_price": True,
        "no_audit": True,
    }
    form.update(changes)
    return form


def sequence(key: str = "default", **changes: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "key": key,
        "name": "Default four-Step Sequence",
        "steps": [step_1(), step_2(), later_step(7), later_step(14)],
    }
    form.update(changes)
    return form


def file(*items: dict[str, Any]) -> dict[str, Any]:
    return {"sequences": list(items) if items else [sequence()]}


def without(form: dict[str, Any], name: str) -> dict[str, Any]:
    trimmed = dict(form)
    del trimmed[name]
    return trimmed


def with_step(number: int, step: dict[str, Any]) -> dict[str, Any]:
    """The default file with Step ``number`` (1-based) replaced by ``step``."""
    form = sequence()
    form["steps"][number - 1] = step
    return file(form)


def refused(data: object) -> InvalidSequences:
    with pytest.raises(InvalidSequences) as caught:
        validate_sequences(data)
    return caught.value


def problem(data: object) -> tuple[str, str]:
    error = refused(data)
    return error.field, error.reason


EXPECTED_STEP_1 = StepBrief(
    step=1,
    delay_days=0,
    purpose="Event-led cold email",
    word_limit=80,
    word_comparison=WordComparison.UNDER,
    allowed_content=("One observed Signal", "One sentence on the likely problem"),
    ask='Relevance only, such as "Worth a look?"',
    link_rule=LinkRule.FOOTER_ONLY,
    no_price=True,
    no_audit=True,
    subject_rule=SubjectRule(
        7, WordComparison.UNDER, ("About the prospect's world", "Never misleading")
    ),
    segment_angles=(),
)
EXPECTED_STEP_2 = StepBrief(
    step=2,
    delay_days=3,
    purpose="New angle",
    word_limit=70,
    word_comparison=WordComparison.AT_MOST,
    allowed_content=("One short Segment insight from Approved Company Facts",),
    ask="The relevance question, reworded",
    link_rule=LinkRule.FOOTER_ONLY,
    no_price=True,
    no_audit=True,
    subject_rule=None,
    segment_angles=(
        SegmentAngle("law_private_ai", "us", US_ANGLE),
        SegmentAngle("law_private_ai", "uk", UK_ANGLE),
    ),
)


def expected_later(step: int, delay_days: int) -> StepBrief:
    return StepBrief(
        step=step,
        delay_days=delay_days,
        purpose=f"Step on day {delay_days}",
        word_limit=50,
        word_comparison=WordComparison.AT_MOST,
        allowed_content=("One relevant case study or regulatory note",),
        ask="Should I close the loop?",
        link_rule=LinkRule.FOOTER_ONLY,
        no_price=True,
        no_audit=True,
        subject_rule=None,
        segment_angles=(),
    )


EXPECTED = SequenceDef(
    key="default",
    name="Default four-Step Sequence",
    steps=(EXPECTED_STEP_1, EXPECTED_STEP_2, expected_later(3, 7), expected_later(4, 14)),
)


# The types ----------------------------------------------------------------------------------------


def test_a_valid_file_validates_into_sequence_defs_in_file_order() -> None:
    other = sequence("other", name="Other", steps=[step_1()])

    result = validate_sequences(file(sequence(), other))

    assert result == (
        EXPECTED,
        SequenceDef("other", "Other", (EXPECTED_STEP_1,)),
    )
    assert isinstance(result, tuple)
    assert all(isinstance(brief.allowed_content, tuple) for brief in result[0].steps)


def test_the_enums_hold_exactly_the_spec_values() -> None:
    assert [member.value for member in WordComparison] == ["under", "at_most"]
    assert [member.value for member in LinkRule] == ["footer_only"]


@pytest.mark.parametrize(
    "value",
    [
        EXPECTED,
        EXPECTED_STEP_1,
        EXPECTED_STEP_1.subject_rule,
        EXPECTED_STEP_2.segment_angles[0],
    ],
)
def test_the_types_are_frozen_and_slotted(value: object) -> None:
    assert not hasattr(value, "__dict__")
    field = dataclasses.fields(value)[0].name  # type: ignore[arg-type]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, field, None)


def test_invalid_sequences_is_a_domain_error_with_its_own_code() -> None:
    error = refused(with_step(2, without(step_2(), "ask")))

    assert isinstance(error, DomainError)
    assert error.code == "invalid_sequences"
    assert error.message == "sequences[default].steps[2].ask: missing"
    assert (error.field, error.reason) == ("sequences[default].steps[2].ask", "missing")


@pytest.mark.parametrize(
    ("limit", "comparison", "words"),
    [
        (80, WordComparison.UNDER, 79),
        (70, WordComparison.AT_MOST, 70),
        (7, WordComparison.UNDER, 6),
        (6, WordComparison.AT_MOST, 6),
        (1, WordComparison.AT_MOST, 1),
        (7, "under", 6),  # a StrEnum compares equal to its value
    ],
)
def test_max_words_counts_under_as_one_fewer(limit: int, comparison: Any, words: int) -> None:
    assert max_words(limit, comparison) == words


def test_max_words_refuses_an_unknown_comparison() -> None:
    with pytest.raises(ValueError, match="under or at_most"):
        max_words(7, "fewer")  # type: ignore[arg-type]


# The top level ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "field", "reason"),
    [
        ([], "(top level)", "must be a mapping with exactly one field, sequences"),
        ({}, "sequences", "missing"),
        ({"other": []}, "other", "unknown field"),
        ({"sequences": [sequence()], "other": 1}, "other", "unknown field"),
        ({"Other Thing": 1}, "(top level)", "has an unknown field"),
        ({"sequences": {"key": "default"}}, "sequences", "must be a list"),
        ({"sequences": []}, "sequences", "must hold at least 1 item"),
        ({"sequences": ["default"]}, "sequences[0]", "must be a mapping"),
    ],
)
def test_the_top_level_is_exactly_one_list_of_sequences(
    data: object, field: str, reason: str
) -> None:
    assert problem(data) == (field, reason)


def test_at_most_20_sequences() -> None:
    items = [sequence(f"s{index}") for index in range(21)]

    assert len(validate_sequences({"sequences": items[:20]})) == 20
    assert problem({"sequences": items}) == ("sequences", "must hold at most 20 items")


# A sequence's fields ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["key", "name", "steps"])
def test_each_sequence_field_is_required(name: str) -> None:
    form = without(sequence(), name)
    shown = "0" if name == "key" else "default"

    assert problem(file(form)) == (f"sequences[{shown}].{name}", "missing")


def test_an_unknown_sequence_field_is_refused_where_it_appears() -> None:
    form = {"key": "default", "number": 1, "name": "Default", "steps": "not checked yet"}

    assert problem(file(form)) == ("sequences[default].number", "unknown field")


def test_an_unknown_field_with_an_odd_name_is_not_repeated() -> None:
    form = {"key": "default", f"Odd {LONG}": 1}

    error = refused(file(form))

    assert (error.field, error.reason) == ("sequences[default]", "has an unknown field")
    assert LONG not in error.message


KEY_REASON = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 31 "
    "characters"
)


@pytest.mark.parametrize("key", ["Default", "1st", "_x", "a-b", "a" * 32, "", 7, None, True])
def test_a_bad_key_is_refused_and_shown_by_index(key: object) -> None:
    data = file(sequence(), sequence(key))  # type: ignore[arg-type]

    assert problem(data) == ("sequences[1].key", KEY_REASON)


def test_a_key_of_31_characters_is_accepted() -> None:
    assert validate_sequences(file(sequence("a" * 31)))[0].key == "a" * 31


def test_a_repeated_key_is_refused_and_shown_by_index() -> None:
    assert problem(file(sequence(), sequence())) == (
        "sequences[1].key",
        "repeats an earlier Sequence key (default)",
    )


def test_a_problem_before_an_invalid_key_shows_the_index() -> None:
    form = {"name": "", "key": "Bad Key"}

    assert problem(file(form)) == ("sequences[0].name", "must not be blank")


def test_a_problem_in_a_repeated_key_s_sequence_shows_the_index() -> None:
    repeat = {"name": " x", "key": "default", "steps": []}

    assert problem(file(sequence(), repeat)) == (
        "sequences[1].name",
        "must not start or end with whitespace",
    )


# Texts --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (5, "must be text"),
        (["x"], "must be text"),
        ("", "must not be blank"),
        ("   ", "must not be blank"),
        (" Default", "must not start or end with whitespace"),
        ("Default\n", "must not start or end with whitespace"),
        ("Def\x00ault", "must not hold control, format or line-separator characters"),
        ("Def​ault", "must not hold control, format or line-separator characters"),
        ("Def ault", "must not hold control, format or line-separator characters"),
        ("Def ault", "must not hold control, format or line-separator characters"),
        ("x" * 101, "must be at most 100 characters"),
    ],
)
def test_the_name_follows_the_text_rules(value: object, reason: str) -> None:
    error = refused(file(sequence(name=value)))

    assert (error.field, error.reason) == ("sequences[default].name", reason)
    assert LONG not in error.message


def test_a_name_of_100_characters_is_accepted() -> None:
    assert validate_sequences(file(sequence(name="x" * 100)))[0].name == "x" * 100


@pytest.mark.parametrize("name", ["purpose", "ask"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "must be text"),
        ("", "must not be blank"),
        ("x ", "must not start or end with whitespace"),
        ("a\tb", "must not hold control, format or line-separator characters"),
        ("x" * 201, "must be at most 200 characters"),
    ],
)
def test_purpose_and_ask_follow_the_text_rules(name: str, value: object, reason: str) -> None:
    assert problem(with_step(3, later_step(7, **{name: value}))) == (
        f"sequences[default].steps[3].{name}",
        reason,
    )


def test_texts_of_200_characters_are_accepted() -> None:
    step = later_step(7, purpose="p" * 200, ask="a" * 200, allowed_content=["c" * 200])

    brief = validate_sequences(with_step(3, step))[0].steps[2]

    assert (len(brief.purpose), len(brief.ask), len(brief.allowed_content[0])) == (200, 200, 200)


# Lists --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "field", "reason"),
    [
        ("One observed Signal", "allowed_content", "must be a list"),
        ([], "allowed_content", "must hold at least 1 item"),
        ([f"item {index}" for index in range(11)], "allowed_content", "must hold at most 10 items"),
        (["One", "Two", "one"], "allowed_content[2]", "repeats an earlier item"),
        (["One", 2], "allowed_content[1]", "must be text"),
        (["One", "x" * 201], "allowed_content[1]", "must be at most 200 characters"),
    ],
)
def test_allowed_content_holds_1_to_10_distinct_texts(
    value: object, field: str, reason: str
) -> None:
    assert problem(with_step(4, later_step(14, allowed_content=value))) == (
        f"sequences[default].steps[4].{field}",
        reason,
    )


def test_allowed_content_of_10_items_is_accepted() -> None:
    items = [f"item {index}" for index in range(10)]

    brief = validate_sequences(with_step(4, later_step(14, allowed_content=items)))[0].steps[3]

    assert brief.allowed_content == tuple(items)


# Integers, enums and flags ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "reason"),
    [
        ("delay_days", "7", "must be a whole number"),
        ("delay_days", True, "must be a whole number"),
        ("delay_days", 7.0, "must be a whole number"),
        ("delay_days", -1, "must be from 0 to 365"),
        ("delay_days", 366, "must be from 0 to 365"),
        ("word_limit", 0, "must be from 1 to 150"),
        ("word_limit", -5, "must be from 1 to 150"),
        ("word_limit", 151, "must be from 1 to 150"),
        ("word_limit", False, "must be a whole number"),
        ("word_limit", None, "must be a whole number"),
        ("word_comparison", "fewer", "must be one of under, at_most"),
        ("word_comparison", "UNDER", "must be one of under, at_most"),
        ("word_comparison", 1, "must be one of under, at_most"),
        ("link_rule", "anywhere", "must be one of footer_only"),
        ("link_rule", None, "must be one of footer_only"),
        ("no_price", "true", "must be true or false"),
        ("no_price", 1, "must be true or false"),
        ("no_audit", None, "must be true or false"),
    ],
)
def test_a_step_s_values_are_typed(name: str, value: object, reason: str) -> None:
    assert problem(with_step(3, later_step(7, **{name: value}))) == (
        f"sequences[default].steps[3].{name}",
        reason,
    )


@pytest.mark.parametrize(("delay", "limit"), [(365, 150), (8, 1)])
def test_the_integer_bounds_are_inclusive(delay: int, limit: int) -> None:
    brief = validate_sequences(with_step(4, later_step(delay, word_limit=limit)))[0].steps[3]

    assert (brief.delay_days, brief.word_limit) == (delay, limit)


# A Step's fields ----------------------------------------------------------------------------------

STEP_REQUIRED = (
    "delay_days",
    "purpose",
    "word_limit",
    "word_comparison",
    "allowed_content",
    "ask",
    "link_rule",
    "no_price",
    "no_audit",
)


@pytest.mark.parametrize("name", STEP_REQUIRED)
def test_each_required_step_field_is_required(name: str) -> None:
    assert problem(with_step(2, without(step_2(), name))) == (
        f"sequences[default].steps[2].{name}",
        "missing",
    )


def test_an_unknown_step_field_is_refused() -> None:
    assert problem(with_step(3, later_step(7, step=3))) == (
        "sequences[default].steps[3].step",
        "unknown field",
    )


def test_a_step_that_is_not_a_mapping_is_refused_by_its_number() -> None:
    assert problem(with_step(4, ["delay_days", 14])) == (  # type: ignore[arg-type]
        "sequences[default].steps[4]",
        "must be a mapping",
    )


@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        ("step one", "must be a list"),
        ([], "must hold at least 1 item"),
        ([step_1(), *[later_step(day) for day in range(1, 7)]], "must hold at most 6 items"),
    ],
)
def test_steps_hold_1_to_6_items(steps: object, reason: str) -> None:
    assert problem(file(sequence(steps=steps))) == ("sequences[default].steps", reason)


def test_six_steps_are_accepted_and_numbered_by_position() -> None:
    steps = [step_1(), *[later_step(day) for day in (2, 4, 8, 16, 32)]]

    result = validate_sequences(file(sequence(steps=steps)))[0]

    assert [brief.step for brief in result.steps] == [1, 2, 3, 4, 5, 6]
    assert [brief.delay_days for brief in result.steps] == [0, 2, 4, 8, 16, 32]


def test_one_step_is_accepted() -> None:
    assert validate_sequences(file(sequence(steps=[step_1()])))[0].steps == (EXPECTED_STEP_1,)


# The subject rule ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject_rule", "field", "reason"),
    [
        ("Under 7 words", "subject_rule", "must be a mapping"),
        (
            {"word_limit": 7, "word_comparison": "under"},
            "subject_rule.guidance",
            "missing",
        ),
        (
            {"word_limit": 7, "word_comparison": "under", "guidance": ["x"], "tone": "warm"},
            "subject_rule.tone",
            "unknown field",
        ),
        (
            {"word_limit": 0, "word_comparison": "under", "guidance": ["x"]},
            "subject_rule.word_limit",
            "must be from 1 to 20",
        ),
        (
            {"word_limit": 21, "word_comparison": "under", "guidance": ["x"]},
            "subject_rule.word_limit",
            "must be from 1 to 20",
        ),
        (
            {"word_limit": "7", "word_comparison": "under", "guidance": ["x"]},
            "subject_rule.word_limit",
            "must be a whole number",
        ),
        (
            {"word_limit": 7, "word_comparison": "below", "guidance": ["x"]},
            "subject_rule.word_comparison",
            "must be one of under, at_most",
        ),
        (
            {"word_limit": 7, "word_comparison": "under", "guidance": []},
            "subject_rule.guidance",
            "must hold at least 1 item",
        ),
        (
            {"word_limit": 7, "word_comparison": "under", "guidance": ["Short", "SHORT"]},
            "subject_rule.guidance[1]",
            "repeats an earlier item",
        ),
        (
            {"word_limit": 7, "word_comparison": "under", "guidance": ["g"] * 11},
            "subject_rule.guidance",
            "must hold at most 10 items",
        ),
    ],
)
def test_the_subject_rule_has_exactly_its_three_fields(
    subject_rule: object, field: str, reason: str
) -> None:
    assert problem(with_step(1, step_1(subject_rule=subject_rule))) == (
        f"sequences[default].steps[1].{field}",
        reason,
    )


# Segment angles -----------------------------------------------------------------------------------

MARKET_REASON = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 16 "
    "characters"
)


def angle(
    segment: object = "law_private_ai", market: object = "us", text: object = "An angle"
) -> dict[str, Any]:
    return {"segment": segment, "market": market, "angle": text}


@pytest.mark.parametrize(
    ("angles", "field", "reason"),
    [
        ("law_private_ai", "segment_angles", "must be a list"),
        (
            [angle(market=f"m{index}") for index in range(21)],
            "segment_angles",
            "must hold at most 20 items",
        ),
        (["law_private_ai"], "segment_angles[0]", "must be a mapping"),
        ([without(angle(), "angle")], "segment_angles[0].angle", "missing"),
        ([without(angle(), "segment")], "segment_angles[0].segment", "missing"),
        ([{**angle(), "tier": 1}], "segment_angles[0].tier", "unknown field"),
        ([angle(), angle(segment="Law")], "segment_angles[1].segment", KEY_REASON),
        ([angle(segment="a" * 32)], "segment_angles[0].segment", KEY_REASON),
        ([angle(market="UK")], "segment_angles[0].market", MARKET_REASON),
        ([angle(market="u" * 17)], "segment_angles[0].market", MARKET_REASON),
        ([angle(market=None)], "segment_angles[0].market", MARKET_REASON),
        ([angle(text="")], "segment_angles[0].angle", "must not be blank"),
        ([angle(text="x" * 201)], "segment_angles[0].angle", "must be at most 200 characters"),
        (
            [angle(), angle(market="uk"), angle(text="Another")],
            "segment_angles[2]",
            "repeats the segment and market of an earlier angle (law_private_ai, us)",
        ),
    ],
)
def test_segment_angles_hold_up_to_20_distinct_segment_market_pairs(
    angles: object, field: str, reason: str
) -> None:
    error = refused(with_step(2, step_2(segment_angles=angles)))

    assert (error.field, error.reason) == (f"sequences[default].steps[2].{field}", reason)


def test_segment_angles_may_be_empty_or_absent_and_bound_at_20() -> None:
    twenty = [angle(market=f"m{index}") for index in range(20)]

    empty = validate_sequences(with_step(2, step_2(segment_angles=[])))[0].steps[1]
    absent = validate_sequences(with_step(2, without(step_2(), "segment_angles")))[0].steps[1]
    full = validate_sequences(with_step(2, step_2(segment_angles=twenty)))[0].steps[1]

    assert empty.segment_angles == absent.segment_angles == ()
    assert len(full.segment_angles) == 20
    assert full.segment_angles[19] == SegmentAngle("law_private_ai", "m19", "An angle")


def test_the_same_segment_in_two_markets_and_two_segments_in_one_market_are_distinct() -> None:
    angles = [angle(), angle(market="uk"), angle(segment="ops_automation")]

    brief = validate_sequences(with_step(2, step_2(segment_angles=angles)))[0].steps[1]

    assert [(a.segment, a.market) for a in brief.segment_angles] == [
        ("law_private_ai", "us"),
        ("law_private_ai", "uk"),
        ("ops_automation", "us"),
    ]


# Rules 1 to 7 -------------------------------------------------------------------------------------


def test_rule_1_step_1_is_on_day_0() -> None:
    assert problem(with_step(1, step_1(delay_days=3))) == (
        "sequences[default].steps[1].delay_days",
        "must be 0: Step 1 is sent when the Sequence starts",
    )


@pytest.mark.parametrize(
    ("delays", "number", "previous"),
    [((0, 7, 7, 14), 3, (2, 7)), ((0, 3, 2, 14), 3, (2, 3)), ((0, 3, 7, 7), 4, (3, 7))],
)
def test_rule_2_each_later_step_comes_after_the_one_before(
    delays: tuple[int, ...], number: int, previous: tuple[int, int]
) -> None:
    steps = [step_1(), step_2(delay_days=delays[1])]
    steps += [later_step(delay) for delay in delays[2:]]

    assert problem(file(sequence(steps=steps))) == (
        f"sequences[default].steps[{number}].delay_days",
        f"must be more than Step {previous[0]}'s delay_days ({previous[1]})",
    )


def test_rule_2_a_later_step_on_day_0_is_refused() -> None:
    assert problem(file(sequence(steps=[step_1(), step_2(delay_days=0)]))) == (
        "sequences[default].steps[2].delay_days",
        "must be more than Step 1's delay_days (0)",
    )


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("no_price", "must be true: no Step names a price (FR-4)"),
        ("no_audit", "must be true: no Step proposes the audit (FR-4)"),
    ],
)
@pytest.mark.parametrize("number", [1, 2, 4])
def test_rule_3_no_price_and_no_audit_are_true_on_every_step(
    name: str, reason: str, number: int
) -> None:
    steps = [step_1(), step_2(), later_step(7), later_step(14)]
    steps[number - 1][name] = False

    assert problem(file(sequence(steps=steps))) == (
        f"sequences[default].steps[{number}].{name}",
        reason,
    )


def test_rule_4_step_1_s_link_rule_is_refused_unless_footer_only() -> None:
    assert problem(with_step(1, step_1(link_rule="body"))) == (
        "sequences[default].steps[1].link_rule",
        "must be one of footer_only",
    )


def test_rule_4_guards_step_1_against_a_future_link_rule() -> None:
    """``LinkRule`` has one member today, so the enum refuses anything else first; the rule
    itself still names the field for a Step 1 whose rule isn't ``footer_only``."""
    brief = dataclasses.replace(EXPECTED_STEP_1, link_rule="links")  # type: ignore[arg-type]

    with pytest.raises(InvalidSequences) as caught:
        sequences_module._check_step_rules(brief, "sequences[default].steps[1]", None)

    assert caught.value.field == "sequences[default].steps[1].link_rule"
    assert "(FR-4)" in caught.value.reason
    later = dataclasses.replace(expected_later(2, 3), link_rule="links")  # type: ignore[arg-type]
    sequences_module._check_step_rules(later, "p", EXPECTED_STEP_1)  # only Step 1's is fixed


def test_rule_5_step_1_needs_a_subject_rule() -> None:
    assert problem(with_step(1, without(step_1(), "subject_rule"))) == (
        "sequences[default].steps[1].subject_rule",
        "missing: Step 1 needs a subject rule (FR-4: fewer than 7 words)",
    )


FEWER_THAN_7 = (
    "must allow fewer than 7 words: at most 7 with under, or at most 6 with at_most (FR-4)"
)
ALLOWS_NO_WORD = "must allow at least 1 word: use 2 or more with under"


@pytest.mark.parametrize(
    ("limit", "comparison", "reason"),
    [
        (7, "under", None),
        (6, "at_most", None),
        (2, "under", None),
        (1, "at_most", None),
        (1, "under", ALLOWS_NO_WORD),  # 0 words
        (8, "under", FEWER_THAN_7),
        (7, "at_most", FEWER_THAN_7),
        (20, "at_most", FEWER_THAN_7),
    ],
)
def test_rule_5_step_1_s_subject_has_fewer_than_7_words(
    limit: int, comparison: str, reason: str | None
) -> None:
    rule = {"word_limit": limit, "word_comparison": comparison, "guidance": ["Short"]}
    data = with_step(1, step_1(subject_rule=rule))

    if reason is None:
        assert validate_sequences(data)[0].steps[0].subject_rule == SubjectRule(
            limit, WordComparison(comparison), ("Short",)
        )
    else:
        assert problem(data) == ("sequences[default].steps[1].subject_rule.word_limit", reason)


@pytest.mark.parametrize(
    ("number", "comparison", "reason"),
    [
        (1, "under", ALLOWS_NO_WORD),
        (3, "under", ALLOWS_NO_WORD),
        (3, "at_most", None),
    ],
)
def test_a_step_s_word_limit_allows_at_least_1_word(
    number: int, comparison: str, reason: str | None
) -> None:
    steps = [step_1(), step_2(), later_step(7), later_step(14)]
    steps[number - 1].update(word_limit=1, word_comparison=comparison)
    data = file(sequence(steps=steps))

    if reason is None:
        brief = validate_sequences(data)[0].steps[number - 1]
        assert max_words(brief.word_limit, brief.word_comparison) == 1
    else:
        assert problem(data) == (f"sequences[default].steps[{number}].word_limit", reason)


def test_a_limit_that_allows_no_word_is_checked_after_the_fields_before_the_rules() -> None:
    later_field = {**later_step(7, word_limit=1, word_comparison="under"), "no_audit": "x"}
    assert problem(with_step(3, later_field)) == (
        "sequences[default].steps[3].no_audit",
        "must be true or false",
    )
    missing = without(later_step(7, word_limit=1, word_comparison="under"), "ask")
    assert problem(with_step(3, missing)) == ("sequences[default].steps[3].ask", "missing")
    before_rules = later_step(3, word_limit=1, word_comparison="under", no_price=False)
    assert problem(with_step(3, before_rules)) == (
        "sequences[default].steps[3].word_limit",
        ALLOWS_NO_WORD,
    )


@pytest.mark.parametrize("number", [2, 3])
def test_rule_5_a_later_step_has_no_subject_rule(number: int) -> None:
    rule = {"word_limit": 5, "word_comparison": "under", "guidance": ["Re: none"]}
    steps = [step_1(), step_2(), later_step(7), later_step(14)]
    steps[number - 1]["subject_rule"] = rule

    assert problem(file(sequence(steps=steps))) == (
        f"sequences[default].steps[{number}].subject_rule",
        "only Step 1 has a subject rule: follow-ups reply in Step 1's thread (AD-22)",
    )


@pytest.mark.parametrize(
    ("number", "changes", "field"),
    [
        (2, {"purpose": "SRA angle"}, "purpose"),
        (3, {"ask": "Ask about ABA Formal Opinion 512"}, "ask"),
        (3, {"ask": "Ask about formal opinions"}, None),  # "formal opinions" isn't the phrase
        (3, {"ask": "What the formal opinion says"}, "ask"),
        (
            3,
            {"allowed_content": ["One case study", "The Solicitors Regulation Authority view"]},
            "allowed_content[1]",
        ),
        (4, {"allowed_content": ["ABA Op. 512 in brief"]}, "allowed_content[0]"),
        (4, {"purpose": "The SRA's notice"}, "purpose"),
    ],
)
def test_rule_7_no_shared_brief_text_names_a_regulation(
    number: int, changes: dict[str, Any], field: str | None
) -> None:
    steps = [step_1(), step_2(), later_step(7), later_step(14)]
    steps[number - 1].update(changes)
    data = file(sequence(steps=steps))

    if field is None:
        validate_sequences(data)
    else:
        assert problem(data) == (f"sequences[default].steps[{number}].{field}", REGULATION_REASON)


def test_rule_7_covers_the_subject_guidance() -> None:
    rule = {"word_limit": 7, "word_comparison": "under", "guidance": ["Short", "Mention the SRA"]}

    assert problem(with_step(1, step_1(subject_rule=rule))) == (
        "sequences[default].steps[1].subject_rule.guidance[1]",
        REGULATION_REASON,
    )


def test_rule_7_covers_the_sequence_name_after_its_steps() -> None:
    named = sequence(name="ABA 512 Sequence")
    assert problem(file(named)) == ("sequences[default].name", REGULATION_REASON)

    named["steps"][1] = without(step_2(), "ask")
    assert problem(file(named)) == ("sequences[default].steps[2].ask", "missing")


def test_rule_7_allows_a_regulation_only_in_an_angle() -> None:
    assert validate_sequences(file())[0].steps[1].segment_angles[0].angle == US_ANGLE


def test_regulatory_note_names_no_regulation() -> None:
    step = later_step(7, allowed_content=["One relevant case study or regulatory note"])

    assert validate_sequences(with_step(3, step))[0].steps[2].allowed_content == (
        "One relevant case study or regulatory note",
    )


# The order of problems ----------------------------------------------------------------------------


def test_fields_come_in_file_order_then_missing_fields_then_rules() -> None:
    fields_first = {
        **without(later_step(7, no_price=False), "ask"),
        "word_limit": 0,
    }
    assert problem(with_step(3, fields_first)) == (
        "sequences[default].steps[3].word_limit",
        "must be from 1 to 150",
    )
    missing_before_rules = without(later_step(7, no_price=False), "ask")
    assert problem(with_step(3, missing_before_rules)) == (
        "sequences[default].steps[3].ask",
        "missing",
    )


def test_the_file_s_own_field_order_decides() -> None:
    reordered = {"no_audit": "x", **without(later_step(7, word_limit=0), "no_audit")}

    assert problem(with_step(3, reordered)) == (
        "sequences[default].steps[3].no_audit",
        "must be true or false",
    )


def test_an_earlier_step_s_problem_comes_first() -> None:
    steps = [step_1(), without(step_2(), "ask"), later_step(7, word_limit=0), later_step(14)]

    assert problem(file(sequence(steps=steps))) == ("sequences[default].steps[2].ask", "missing")


def test_an_earlier_sequence_s_problem_comes_first() -> None:
    bad = sequence("bad", name="")
    later = sequence("later", steps="none")

    assert problem(file(sequence(), bad, later)) == ("sequences[bad].name", "must not be blank")


def test_the_rules_run_in_order_within_a_step() -> None:
    step = without(step_1(delay_days=2, no_price=False), "subject_rule")

    assert problem(with_step(1, step))[0] == "sequences[default].steps[1].delay_days"
    assert problem(with_step(1, {**step, "delay_days": 0}))[0] == (
        "sequences[default].steps[1].no_price"
    )


# Reasons never repeat a long value ----------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        file(sequence(LONG)),
        file(sequence(), sequence(LONG)),
        file(sequence(name=LONG * 3)),
        {LONG: []},
        with_step(3, later_step(7, word_comparison=LONG)),
        with_step(3, later_step(7, link_rule=LONG)),
        with_step(3, later_step(7, **{LONG: 1})),
        with_step(3, later_step(7, allowed_content=[LONG, LONG])),
        with_step(3, later_step(7, purpose=LONG * 5)),
        with_step(2, step_2(segment_angles=[angle(segment=LONG)])),
        with_step(2, step_2(segment_angles=[angle(market=LONG)])),
        with_step(2, step_2(segment_angles=[angle(text=LONG * 5)])),
    ],
)
def test_a_reason_never_repeats_a_long_value(data: object) -> None:
    assert LONG not in refused(data).message


# References and rule 6 ----------------------------------------------------------------------------


def checked(angles: list[dict[str, Any]], **references: Any) -> None:
    sequences = validate_sequences(with_step(2, step_2(segment_angles=angles)))
    check_sequence_references(
        sequences,
        references.get("segment_keys", SEGMENT_KEYS),
        references.get("market_countries", MARKET_COUNTRIES),
    )


def reference_problem(angles: list[dict[str, Any]], **references: Any) -> tuple[str, str]:
    with pytest.raises(InvalidSequences) as caught:
        checked(angles, **references)
    return caught.value.field, caught.value.reason


ANGLES = "sequences[default].steps[2].segment_angles"


def test_the_references_pass_when_every_angle_names_a_segment_and_market() -> None:
    checked([angle(text=US_ANGLE), angle(market="uk", text=UK_ANGLE)])
    checked([angle(segment="ops_automation", market="uk", text="Their month-end close")])
    checked([])


def test_an_unknown_segment_is_refused() -> None:
    assert reference_problem([angle(segment="law_privateai")]) == (
        f"{ANGLES}[0].segment",
        "isn't the key of a Segment in this seed sync",
    )


def test_an_unknown_market_is_refused() -> None:
    assert reference_problem([angle(), angle(market="ca")]) == (
        f"{ANGLES}[1].market",
        "isn't the code of a Market in this seed sync",
    )


def test_the_segment_is_checked_before_the_market() -> None:
    assert reference_problem([angle(segment="nope", market="ca")])[0] == f"{ANGLES}[0].segment"


def test_the_references_are_the_given_ones() -> None:
    angles = [angle(text=US_ANGLE), angle(market="uk", text=UK_ANGLE)]

    assert reference_problem(angles, segment_keys=["ops_automation"])[0] == (
        f"{ANGLES}[0].segment"
    )
    assert reference_problem(angles, market_countries={"us": "US"})[0] == f"{ANGLES}[1].market"


ABA_ONLY_US = "names ABA Formal Opinion 512, which only a US Market's angle may (market {})"
SRA_ONLY_GB = "names the SRA notice, which only a GB Market's angle may (market {})"


@pytest.mark.parametrize(
    ("market", "text", "reason"),
    [
        ("uk", US_ANGLE, ABA_ONLY_US.format("uk is GB")),
        ("uk", "ABA Op. 512 for the firm", ABA_ONLY_US.format("uk is GB")),
        ("us", UK_ANGLE, SRA_ONLY_GB.format("us is US")),
        ("ie", UK_ANGLE, SRA_ONLY_GB.format("ie is IE")),
        ("ie", US_ANGLE, ABA_ONLY_US.format("ie is IE")),
        ("us", "ABA Formal Opinion 512 and the SRA notice", SRA_ONLY_GB.format("us is US")),
    ],
)
def test_rule_6_a_regulation_is_cited_only_in_its_country_s_market(
    market: str, text: str, reason: str
) -> None:
    countries = {**MARKET_COUNTRIES, "ie": "IE"}

    assert reference_problem([angle(market=market, text=text)], market_countries=countries) == (
        f"{ANGLES}[0].angle",
        reason,
    )


def test_the_first_bad_angle_in_file_order_is_named() -> None:
    first_angles = [angle(), angle(market="ca")]
    first = sequence("first", steps=[step_1(), step_2(segment_angles=first_angles)])
    second = sequence("second", steps=[step_1(), step_2(segment_angles=[angle(segment="nope")])])
    sequences = validate_sequences(file(first, second))

    with pytest.raises(InvalidSequences) as caught:
        check_sequence_references(sequences, SEGMENT_KEYS, MARKET_COUNTRIES)

    assert caught.value.field == "sequences[first].steps[2].segment_angles[1].market"


# The file form ------------------------------------------------------------------------------------


def test_sequence_form_is_the_file_mapping() -> None:
    form = sequence_form(EXPECTED)

    assert form == sequence()
    assert "subject_rule" not in form["steps"][1]  # type: ignore[index]
    assert "segment_angles" not in form["steps"][0]  # type: ignore[index]
    assert type(form["steps"][0]["word_comparison"]) is str  # type: ignore[index]
    assert type(form["steps"][0]["subject_rule"]["word_comparison"]) is str  # type: ignore[index]
    assert type(form["steps"][0]["link_rule"]) is str  # type: ignore[index]


def test_the_round_trip_gives_the_sequence_back() -> None:
    for value in validate_sequences(file(sequence(), sequence("one", steps=[step_1()]))):
        assert sequence_from_form(sequence_form(value)) == value
    assert sequence_from_form(sequence_form(EXPECTED)) == EXPECTED


def test_the_form_holds_no_step_number_so_validation_renumbers() -> None:
    misnumbered = dataclasses.replace(
        EXPECTED, steps=tuple(dataclasses.replace(b, step=9) for b in EXPECTED.steps)
    )

    assert sequence_from_form(sequence_form(misnumbered)) == EXPECTED


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"allowed_content": "One"}, "allowed_content", "must be a list"),
        ({"segment_angles": "none"}, "segment_angles", "must be a list"),
        ({"segment_angles": ("x",)}, "segment_angles[0]", "must be a mapping"),
        ({"subject_rule": "short"}, "subject_rule", "must be a mapping"),
        ({"word_comparison": 7}, "word_comparison", "must be one of under, at_most"),
        ({"no_audit": False}, "no_audit", "must be true: no Step proposes the audit (FR-4)"),
    ],
)
def test_a_hand_built_value_of_the_wrong_type_is_left_for_validation_to_refuse(
    changes: dict[str, Any], field: str, reason: str
) -> None:
    steps = list(EXPECTED.steps)
    steps[2] = dataclasses.replace(steps[2], **changes)
    hand_built = dataclasses.replace(EXPECTED, steps=tuple(steps))

    with pytest.raises(InvalidSequences) as caught:
        sequence_from_form(sequence_form(hand_built))

    assert (caught.value.field, caught.value.reason) == (f"{DEFAULT}.steps[3].{field}", reason)


def test_hand_built_steps_that_are_not_a_list_are_refused() -> None:
    hand_built = dataclasses.replace(EXPECTED, steps="none")  # type: ignore[arg-type]

    with pytest.raises(InvalidSequences) as caught:
        sequence_from_form(sequence_form(hand_built))

    assert caught.value.field == f"{DEFAULT}.steps"


def test_hand_built_plain_strings_for_the_enums_are_accepted() -> None:
    plain = dataclasses.replace(
        EXPECTED_STEP_1, word_comparison="under", link_rule="footer_only"  # type: ignore[arg-type]
    )
    hand_built = dataclasses.replace(EXPECTED, steps=(plain, *EXPECTED.steps[1:]))

    assert sequence_from_form(sequence_form(hand_built)) == EXPECTED


def test_sequence_from_form_names_its_key_or_index() -> None:
    with pytest.raises(InvalidSequences) as by_key:
        sequence_from_form(sequence(name=""))
    with pytest.raises(InvalidSequences) as by_index:
        sequence_from_form(sequence("Bad", steps=[]))
    with pytest.raises(InvalidSequences) as not_a_mapping:
        sequence_from_form(["default"])

    assert by_key.value.field == "sequences[default].name"
    assert by_index.value.field == "sequences[0].key"
    assert not_a_mapping.value.field == "sequences[0]"


def test_validation_leaves_its_input_alone() -> None:
    data = file()
    before = copy.deepcopy(data)

    validate_sequences(data)

    assert data == before


# The regulation detectors -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ABA Formal Opinion 512",
        "aba formal opinion 512",
        "What ABA Op. 512 means",
        "ABA-512",
        "the formal opinion",
        "Formal  Opinion on AI",
        "512 (ABA)",
    ],
)
def test_mentions_aba_512_finds_the_opinion(text: str) -> None:
    assert mentions_aba_512(text)
    assert represents_market_fact(text)


@pytest.mark.parametrize(
    "text",
    [
        "ABA",
        "512 lawyers",
        "ABA512",
        "the American Bar Association",
        "formal opinions",
        "SRA notice",
        "",
    ],
)
def test_mentions_aba_512_ignores_other_text(text: str) -> None:
    assert not mentions_aba_512(text)


@pytest.mark.parametrize(
    "text",
    [
        "the SRA notice",
        "sra",
        "SRA-regulated firms",
        "the Solicitors Regulation Authority",
        "solicitors   regulation-authority guidance",
    ],
)
def test_mentions_sra_notice_finds_the_notice(text: str) -> None:
    assert mentions_sra_notice(text)
    assert represents_market_fact(text)


@pytest.mark.parametrize(
    "text",
    ["extra", "S.R.A.", "srael", "Solicitors Regulation", "ABA Formal Opinion 512", ""],
)
def test_mentions_sra_notice_ignores_other_text(text: str) -> None:
    assert not mentions_sra_notice(text)


@pytest.mark.parametrize(
    "text",
    [
        "ABA Formal Opinion 512",
        "SRA",
        "formal opinion",
        "solicitors regulation authority",
        "ABA alone",
        "512 alone",
        "Ops automation",
        "",
    ],
)
def test_represents_market_fact_is_either_detector(text: str) -> None:
    assert represents_market_fact(text) == (mentions_aba_512(text) or mentions_sra_notice(text))
