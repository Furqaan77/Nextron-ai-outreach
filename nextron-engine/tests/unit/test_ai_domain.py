"""The values of a Claude call (``nextron.domain.ai``, Story 2.7): every field check, the route
tuple's key and prompt hash, the shared checks, and that no ``repr`` shows a prompt or a text."""

import hashlib
from dataclasses import FrozenInstanceError, replace
from typing import Any, Final

import pytest

from nextron.domain.ai import (
    Effort,
    RouteTuple,
    UntrustedBlock,
    Urgency,
    check_blocks,
    check_token_limits,
    check_urgency,
)
from nextron.domain.budget import MODEL_PATTERN

PROMPT: Final = "Extract the Signals. Quote the page exactly."
LEAD: Final = "lead:01JD3QZ8X5V9K2M7N4P6R8T0WY"
TEXT: Final = "Smith & Jones LLP is hiring a Head of Marketing."


def route_tuple(**changes: Any) -> RouteTuple:
    fields: dict[str, Any] = {
        "route": "extract_signals",
        "model": "claude-haiku-5-5",
        "effort": Effort.MEDIUM,
        "prompt_version": "v1",
        "schema_version": 1,
        "system_prompt": PROMPT,
    }
    return RouteTuple(**(fields | changes))


def block(**changes: Any) -> UntrustedBlock:
    fields: dict[str, Any] = {"subject": LEAD, "source": "page", "block_id": "p1", "text": TEXT}
    return UntrustedBlock(**(fields | changes))


# Enums ------------------------------------------------------------------------------------------


def test_the_enums_hold_exactly_the_specified_values() -> None:
    assert [member.value for member in Urgency] == ["interactive", "soon", "bulk"]
    assert [member.value for member in Effort] == ["low", "medium", "high", "xhigh", "max"]


# RouteTuple -------------------------------------------------------------------------------------


def test_a_route_tuple_derives_its_hash_and_key_from_the_prompt_text() -> None:
    value = route_tuple()
    digest = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()

    assert value.prompt_sha256 == digest
    assert value.key == f"claude-haiku-5-5:medium:v1:s1:{digest[:12]}"
    assert MODEL_PATTERN.fullmatch(value.key)
    assert route_tuple(system_prompt=PROMPT + " ").key != value.key  # the text names the key


def test_the_longest_route_tuple_key_still_fits_the_usage_table() -> None:
    value = route_tuple(
        route="r" * 64, model="m" * 64, effort=Effort.MEDIUM, prompt_version="v9999",
        schema_version=9_999,
    )

    assert MODEL_PATTERN.fullmatch(value.key)
    assert value.key.startswith("m" * 64 + ":medium:v9999:s9999:")


def test_a_non_ascii_prompt_is_hashed_as_utf8() -> None:
    value = route_tuple(system_prompt="Café ＜ résumé")

    assert value.prompt_sha256 == hashlib.sha256("Café ＜ résumé".encode()).hexdigest()


@pytest.mark.parametrize(
    ("changes", "error", "field"),
    [
        ({"route": "Extract"}, ValueError, "route"),
        ({"route": ""}, ValueError, "route"),
        ({"route": "r" * 65}, ValueError, "route"),
        ({"route": 7}, TypeError, "route"),
        ({"model": "Claude-Haiku"}, ValueError, "model"),
        ({"model": "-haiku"}, ValueError, "model"),
        ({"model": "claude:haiku"}, ValueError, "model"),
        ({"model": "m" * 65}, ValueError, "model"),
        ({"model": None}, TypeError, "model"),
        ({"effort": "medium"}, TypeError, "effort"),
        ({"prompt_version": "1"}, ValueError, "prompt_version"),
        ({"prompt_version": "v0"}, ValueError, "prompt_version"),
        ({"prompt_version": "v01"}, ValueError, "prompt_version"),
        ({"prompt_version": "v10000"}, ValueError, "prompt_version"),
        ({"prompt_version": "V1"}, ValueError, "prompt_version"),
        ({"schema_version": 0}, ValueError, "schema_version"),
        ({"schema_version": 10_000}, ValueError, "schema_version"),
        ({"schema_version": True}, TypeError, "schema_version"),
        ({"schema_version": 1.0}, TypeError, "schema_version"),
        ({"schema_version": "1"}, TypeError, "schema_version"),
        ({"system_prompt": ""}, ValueError, "system_prompt"),
        ({"system_prompt": " \n\t "}, ValueError, "system_prompt"),
        ({"system_prompt": "　"}, ValueError, "system_prompt"),
        ({"system_prompt": None}, TypeError, "system_prompt"),
        ({"system_prompt": b"bytes"}, TypeError, "system_prompt"),
        ({"system_prompt": "bad \ud800 surrogate"}, ValueError, "system_prompt"),
    ],
)
def test_a_route_tuple_refuses_each_bad_field_by_name(
    changes: dict[str, Any], error: type[Exception], field: str
) -> None:
    with pytest.raises(error) as raised:
        route_tuple(**changes)

    message = str(raised.value)
    assert message.startswith(field)
    for value in changes.values():
        if isinstance(value, str) and len(value) > 4:
            assert value not in message  # the field, never its value


def test_the_route_tuple_bounds_are_accepted() -> None:
    assert route_tuple(schema_version=9_999).schema_version == 9_999
    assert route_tuple(prompt_version="v9999").prompt_version == "v9999"
    assert route_tuple(model="m" * 64).model == "m" * 64
    assert route_tuple(model="claude-opus-5.5").model == "claude-opus-5.5"


def test_a_route_tuple_is_frozen_and_its_repr_hides_the_prompt() -> None:
    value = route_tuple()

    with pytest.raises(FrozenInstanceError):
        value.model = "claude-opus-5-5"  # type: ignore[misc]
    assert PROMPT not in repr(value)
    assert "system_prompt" not in repr(value)
    assert "extract_signals" in repr(value)
    assert replace(value, effort=Effort.HIGH).effort is Effort.HIGH


# UntrustedBlock ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "error", "field"),
    [
        ({"subject": "lead"}, ValueError, "subject"),
        ({"subject": "Lead:01ABC"}, ValueError, "subject"),
        ({"subject": "lead:"}, ValueError, "subject"),
        ({"subject": "lead:01-ABC"}, ValueError, "subject"),
        ({"subject": "lead:" + "a" * 65}, ValueError, "subject"),
        ({"subject": "l" * 33 + ":1"}, ValueError, "subject"),
        ({"subject": None}, TypeError, "subject"),
        ({"source": "Page"}, ValueError, "source"),
        ({"source": "page-1"}, ValueError, "source"),
        ({"source": "s" * 33}, ValueError, "source"),
        ({"source": 1}, TypeError, "source"),
        ({"block_id": ""}, ValueError, "block_id"),
        ({"block_id": "-p1"}, ValueError, "block_id"),
        ({"block_id": 'p1" x="y'}, ValueError, "block_id"),
        ({"block_id": "p" * 65}, ValueError, "block_id"),
        ({"block_id": 1}, TypeError, "block_id"),
        ({"text": None}, TypeError, "text"),
        ({"text": b"page"}, TypeError, "text"),
        ({"text": "lone \udc80 surrogate"}, ValueError, "text"),
    ],
)
def test_a_block_refuses_each_bad_field_by_name(
    changes: dict[str, Any], error: type[Exception], field: str
) -> None:
    with pytest.raises(error) as raised:
        block(**changes)

    assert str(raised.value).startswith(field)


def test_a_block_accepts_its_bounds_and_hides_its_text() -> None:
    accepted = [
        block(subject="signal_item:01JD3QZ8X5V9K2M7N4P6R8T0WY"),
        block(subject="l" * 32 + ":" + "A" * 64),
        block(source="s" * 32, block_id="P" + "_.-9" * 15 + "abc"),
        block(text=""),
    ]
    value = block()

    assert [each.subject for each in accepted][:2] == [
        "signal_item:01JD3QZ8X5V9K2M7N4P6R8T0WY",
        "l" * 32 + ":" + "A" * 64,
    ]
    assert TEXT not in repr(value)
    assert "text=" not in repr(value)
    with pytest.raises(FrozenInstanceError):
        value.text = "other"  # type: ignore[misc]


# The shared checks ------------------------------------------------------------------------------


def test_check_blocks_returns_the_one_subject() -> None:
    blocks = [block(block_id="p1"), block(block_id="p2", source="careers")]

    assert check_blocks(blocks) == LEAD
    assert check_blocks(tuple(blocks)) == LEAD


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([], "blocks must hold at least one block"),
        (
            [block(), block(subject="lead:01JD3QZ8X5V9K2M7N4P6R8T0WZ", block_id="p2")],
            "blocks must all belong to one subject",
        ),
        (
            [block(), block(subject="signal_item:01JD3QZ8X5V9K2M7N4P6R8T0WY", block_id="p2")],
            "blocks must all belong to one subject",
        ),
        ([block(), block(source="careers")], "blocks must not repeat a block_id"),
    ],
)
def test_check_blocks_refuses_bad_inputs(blocks: list[UntrustedBlock], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        check_blocks(blocks)


@pytest.mark.parametrize("blocks", ["lead:1", b"x", None, {block()}, [block(), "text"], 3])
def test_check_blocks_refuses_anything_but_a_sequence_of_blocks(blocks: object) -> None:
    with pytest.raises(TypeError, match="blocks must be a sequence of UntrustedBlock"):
        check_blocks(blocks)  # type: ignore[arg-type]


def test_check_urgency_accepts_only_an_urgency() -> None:
    assert check_urgency(Urgency.SOON) is Urgency.SOON
    for value in ("soon", None, 1):
        with pytest.raises(TypeError, match="urgency must be an Urgency"):
            check_urgency(value)


@pytest.mark.parametrize(
    ("max_tokens", "cap", "error", "message"),
    [
        (0, 100, ValueError, "max_tokens must be above 0"),
        (-1, 100, ValueError, "max_tokens must be above 0"),
        (101, 100, ValueError, "max_tokens must be at most max_tokens_cap"),
        (100, 16_001, ValueError, "max_tokens_cap must be at most 16,000"),
        (True, 100, TypeError, "max_tokens must be an int"),
        (100, 1.5e4, TypeError, "max_tokens_cap must be an int"),
    ],
)
def test_check_token_limits_refuses_each_broken_rule(
    max_tokens: object, cap: object, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        check_token_limits(max_tokens, cap, 16_000)


def test_check_token_limits_accepts_the_edges() -> None:
    check_token_limits(1, 1, 16_000)
    check_token_limits(16_000, 16_000, 16_000)
