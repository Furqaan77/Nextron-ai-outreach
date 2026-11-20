"""Segment fallback copy (FR-22; Story 4.19): ``nextron.domain.fallback_copy``'s placeholders,
validation and fill, and the shipped ``seed/fallback_templates.yaml`` against the Step briefs."""

from typing import Any

import pytest

from nextron import main
from nextron.domain import seed_yaml
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.fallback_copy import (
    FACT_NOT_ALLOWED,
    MISSING_VALUE,
    FallbackFillFailed,
    FallbackTemplate,
    FillValues,
    InvalidFallbackTemplates,
    fact_ids,
    fill,
    first_name_of,
    placeholders,
    validate_fallback_templates,
)
from nextron.domain.grounding_rules import count_words
from nextron.domain.markets import EnglishVariant
from nextron.domain.sequences import max_words, validate_sequences

SHIPPED = main.SEED_DIR / main.FALLBACK_TEMPLATES_FILE_NAME


def shipped_data() -> Any:
    return seed_yaml.parse(SHIPPED.read_text(encoding="utf-8"))


def item(**changes: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "segment": "law_private_ai",
        "step": 1,
        "english_variant": "en_us",
        "subject": "Client data",
        "body": ["Hi {first_name},", "Worth a look at {company}?"],
    }
    found.update(changes)
    return {key: value for key, value in found.items() if value is not None}


def refused(*items: dict[str, Any]) -> InvalidFallbackTemplates:
    with pytest.raises(InvalidFallbackTemplates) as caught:
        validate_fallback_templates({"fallback_templates": list(items)})
    return caught.value


# Placeholders -------------------------------------------------------------------------------------


def test_the_four_placeholders_are_accepted_in_order() -> None:
    text = "{first_name} at {company} ({role}): {fact:legal_ai}"

    assert placeholders(text) == ("first_name", "company", "role", "fact:legal_ai")


@pytest.mark.parametrize(
    "text",
    ["{email}", "{First_name}", "{fact:}", "{fact:Bad}", "{ company }", "{}", "{fact:a b}"],
)
def test_any_other_placeholder_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="unknown placeholder"):
        placeholders(text)


@pytest.mark.parametrize("text", ["a { b", "a } b", "{first_name", "first_name}", "{{company}}"])
def test_a_stray_brace_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"unknown placeholder|\{ or \}"):
        placeholders(text)


def test_fact_ids_are_in_first_appearance_order_each_once() -> None:
    assert fact_ids("{fact:b}", None, "{fact:a} {fact:b} {company} {fact:c}") == ("b", "a", "c")


def test_fill_replaces_every_placeholder() -> None:
    values = FillValues("Zelda", "Firm 1", "Partner", {"legal_ai": "Built a thing."})

    assert fill("Hi {first_name} ({role}), {company}: {fact:legal_ai}", values) == (
        "Hi Zelda (Partner), Firm 1: Built a thing."
    )


@pytest.mark.parametrize(("value", "detail"), [(None, MISSING_VALUE), ("  ", MISSING_VALUE)])
def test_a_missing_value_fails_the_fill(value: str | None, detail: str) -> None:
    with pytest.raises(FallbackFillFailed) as caught:
        fill("Hi {role}", FillValues("Zelda", "Firm", value))
    assert caught.value.detail == detail


def test_a_fact_without_wording_fails_the_fill() -> None:
    with pytest.raises(FallbackFillFailed) as caught:
        fill("{fact:legal_ai}", FillValues("Zelda", "Firm", None))
    assert caught.value.detail == FACT_NOT_ALLOWED


def test_an_unused_missing_value_is_fine() -> None:
    assert fill("Hi {first_name}", FillValues("Zelda", None, None)) == "Hi Zelda"


@pytest.mark.parametrize(
    ("name", "first"), [("Zelda Quill", "Zelda"), ("  Ana  ", "Ana"), ("", None), (None, None)]
)
def test_first_name_is_the_first_token(name: str | None, first: str | None) -> None:
    assert first_name_of(name) == first


# Validation ---------------------------------------------------------------------------------------


def test_a_valid_file_gives_templates_with_joined_paragraphs() -> None:
    [found] = validate_fallback_templates({"fallback_templates": [item()]})

    assert found == FallbackTemplate(
        "law_private_ai",
        1,
        EnglishVariant.EN_US,
        "Client data",
        "Hi {first_name},\n\nWorth a look at {company}?",
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"body": ["Hi {email},"]},
            "fallback_templates[law_private_ai/1/en_us].body[0]: holds an unknown placeholder",
        ),
        (
            {"subject": "Hi {name"},
            "fallback_templates[law_private_ai/1/en_us].subject: holds a { or }",
        ),
        ({"subject": None}, "fallback_templates[law_private_ai/1/en_us].subject: missing"),
        ({"step": 0}, "fallback_templates[0].step: must be a whole number from 1 to 6"),
        ({"step": True}, "fallback_templates[0].step: must be a whole number"),
        ({"english_variant": "en_au"}, "fallback_templates[0].english_variant: must be one of"),
        ({"segment": "Law"}, "fallback_templates[0].segment: must be a Segment key"),
        ({"body": []}, "fallback_templates[law_private_ai/1/en_us].body: must hold 1 to 10"),
        ({"body": [" padded"]}, "body[0]: must not start or end with whitespace"),
        ({"tone": "warm"}, "fallback_templates[0].tone: unknown field"),
    ],
)
def test_a_bad_template_is_refused_naming_its_key(
    changes: dict[str, Any], message: str
) -> None:
    assert message in refused(item(**changes)).message


def test_a_follow_up_has_no_subject() -> None:
    error = refused(item(), item(step=2))

    assert error.field == "fallback_templates[law_private_ai/2/en_us].subject"
    assert "only Step 1 has a subject" in error.reason


def test_a_follow_up_needs_its_step_1_template() -> None:
    error = refused(item(step=2, subject=None))

    assert error.field == "fallback_templates[law_private_ai/2/en_us].step"


def test_a_repeated_key_is_refused() -> None:
    error = refused(item(), item())

    assert error.message.startswith("fallback_templates[law_private_ai/1/en_us].step: repeats")


@pytest.mark.parametrize(
    "data", [[], {"fallback_templates": []}, {"fallback_templates": "x"}, {"other": []}]
)
def test_a_bad_top_level_is_refused(data: Any) -> None:
    with pytest.raises(InvalidFallbackTemplates):
        validate_fallback_templates(data)


# The shipped file ---------------------------------------------------------------------------------


def test_the_shipped_file_covers_both_segments_four_steps_and_both_variants() -> None:
    found = validate_fallback_templates(shipped_data())

    assert sorted(template.natural_key for template in found) == sorted(
        (segment, step, variant)
        for segment in ("law_private_ai", "ops_automation")
        for step in (1, 2, 3, 4)
        for variant in ("en_us", "en_gb")
    )


def test_the_shipped_templates_obey_the_step_briefs_and_never_repeat_a_fact() -> None:
    """Filled with the facts' wordings and three-word values, each body keeps its Step's word
    limit, Step 1's subject stays under 7 words with one question, no Step names the audit, a
    link or a price, and no fact repeats across one Segment and variant's Steps."""
    templates = validate_fallback_templates(shipped_data())
    [sequence] = validate_sequences(
        seed_yaml.parse((main.SEED_DIR / main.SEQUENCES_FILE_NAME).read_text(encoding="utf-8"))
    )
    facts = {
        fact.fact_id: fact.content.wording
        for fact in validate_company_facts(
            seed_yaml.parse(
                (main.SEED_DIR / main.COMPANY_FACTS_FILE_NAME).read_text(encoding="utf-8")
            )
        )
    }
    values = FillValues("Zelda", "Smith Jones Partners", "Managing Partner", facts)
    used: dict[tuple[str, str], list[str]] = {}
    for template in templates:
        brief = sequence.steps[template.step - 1]
        body = fill(template.body, values)
        assert count_words(body) <= max_words(brief.word_limit, brief.word_comparison), template
        assert "audit" not in body.casefold() and "http" not in body and "$" not in body
        if template.step == 1:
            assert template.subject is not None and brief.subject_rule is not None
            subject = fill(template.subject, values)
            limit = max_words(brief.subject_rule.word_limit, brief.subject_rule.word_comparison)
            assert count_words(subject) <= limit
            assert body.count("?") == 1
        key = (template.segment, str(template.english_variant))
        found = fact_ids(template.subject, template.body)
        assert not set(found) & set(used.get(key, [])), template
        used.setdefault(key, []).extend(found)
