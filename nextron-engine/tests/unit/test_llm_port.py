"""The ``LlmPort`` values (``nextron.ports.llm``, Story 2.7): every construction check of
``LlmUsage`` and the five results, refused by name; valid values build; and ``LlmOk``'s
``repr`` hides ``parsed``."""

from dataclasses import FrozenInstanceError
from typing import Any, Final

import pytest

from nextron.ports.llm import (
    LlmFailed,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmResult,
    LlmTruncated,
    LlmUsage,
)

HAIKU: Final = "claude-haiku-5-5"
USAGE: Final = LlmUsage(
    input_tokens=1_000,
    output_tokens=200,
    cache_read_tokens=30,
    cache_write_tokens=40,
    iterations=1,
    requests=1,
    cost_micros=200,
)
COUNTS: Final = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "iterations",
    "requests",
    "cost_micros",
)


def served(**changes: Any) -> dict[str, Any]:
    return {"model": HAIKU, "usage": USAGE} | changes


# LlmUsage ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", COUNTS)
@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        (-1, ValueError, "must not be negative"),
        (True, TypeError, "must be an int"),
        (1.0, TypeError, "must be an int"),
        ("1", TypeError, "must be an int"),
        (None, TypeError, "must be an int"),
    ],
)
def test_usage_refuses_each_count_that_isnt_a_non_negative_int(
    name: str, value: object, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=f"^{name} {message}$"):
        LlmUsage(**{name: value})  # type: ignore[arg-type]


def test_usage_defaults_to_zero_and_is_frozen() -> None:
    assert LlmUsage() == LlmUsage(0, 0, 0, 0, 0, 0, 0)
    assert USAGE.cost_micros == 200
    with pytest.raises(FrozenInstanceError):
        USAGE.requests = 2  # type: ignore[misc]


# The results ------------------------------------------------------------------------------------


def _ok(**changes: Any) -> LlmResult:
    return LlmOk({"people": []}, **served(**changes))


def _refused(**changes: Any) -> LlmResult:
    return LlmRefused("cyber", **served(**changes))


def _truncated(**changes: Any) -> LlmResult:
    return LlmTruncated(**served(**changes))


def _invalid(**changes: Any) -> LlmResult:
    return LlmInvalid(**served(**changes))


def _failed(**changes: Any) -> LlmResult:
    return LlmFailed(False, "llm_rejected", 400, **served(**changes))


BUILDERS: Final = [_ok, _refused, _truncated, _invalid, _failed]


@pytest.mark.parametrize("build", BUILDERS, ids=lambda build: build.__name__.strip("_"))
@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"model": "Bad Model"}, ValueError, "model must match"),
        ({"model": ""}, ValueError, "model must match"),
        ({"model": "-haiku"}, ValueError, "model must match"),
        ({"model": None}, TypeError, "model must be a str"),
        ({"usage": None}, TypeError, "usage must be an LlmUsage"),
        ({"usage": {"requests": 1}}, TypeError, "usage must be an LlmUsage"),
    ],
)
def test_every_result_refuses_a_bad_model_or_usage(
    build: Any, changes: dict[str, Any], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        build(**changes)


@pytest.mark.parametrize("parsed", [[], None, "{}", ("people",)])
def test_ok_refuses_parsed_that_isnt_a_dict(parsed: object) -> None:
    with pytest.raises(TypeError, match="parsed must be a dict"):
        LlmOk(parsed, **served())  # type: ignore[arg-type]


@pytest.mark.parametrize("category", ["Cyber Harm", "cyber-harm", "", "1cyber", 7, b"cyber"])
def test_refused_refuses_a_category_that_isnt_a_code(category: object) -> None:
    with pytest.raises(ValueError, match="category must be None or match"):
        LlmRefused(category, **served())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ((1, "llm_rejected", 400), TypeError, "retryable must be a bool"),
        ((None, "llm_rejected", 400), TypeError, "retryable must be a bool"),
        ((False, "Bad Code", None), ValueError, "code must match"),
        ((False, "", None), ValueError, "code must match"),
        ((False, None, None), ValueError, "code must match"),
        ((False, "llm_rejected", 99), ValueError, "status must be from 100 to 599"),
        ((False, "llm_rejected", 600), ValueError, "status must be from 100 to 599"),
        ((False, "llm_rejected", True), TypeError, "status must be None or an int"),
        ((False, "llm_rejected", "400"), TypeError, "status must be None or an int"),
        ((False, "llm_rejected", 400.0), TypeError, "status must be None or an int"),
    ],
)
def test_failed_refuses_each_bad_field(
    arguments: tuple[object, object, object], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        LlmFailed(*arguments, **served())  # type: ignore[arg-type]


def test_valid_results_build_with_their_fields() -> None:
    ok = LlmOk({"people": [{"name": "Ann Smith"}]}, model="claude-opus-5-5", usage=USAGE)
    failed_bounds = [
        LlmFailed(False, "llm_rejected", status, **served()) for status in (100, 599)
    ]

    assert ok.parsed == {"people": [{"name": "Ann Smith"}]}
    assert ok.model == "claude-opus-5-5"
    assert LlmRefused(None, **served()).category is None
    assert LlmRefused("general_harms", **served()).category == "general_harms"
    assert LlmTruncated(**served()).usage is USAGE
    assert LlmInvalid(**served()).model == HAIKU
    assert LlmFailed(True, "llm_unexpected", None, **served()).retryable is True
    assert [failed.status for failed in failed_bounds] == [100, 599]
    assert all(isinstance(build(), LlmResult) for build in BUILDERS)  # type: ignore[arg-type]


def test_model_and_usage_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        LlmTruncated(HAIKU, USAGE)  # type: ignore[misc]
    with pytest.raises(TypeError):
        LlmFailed(False, "llm_rejected", 400, HAIKU, USAGE)  # type: ignore[misc]


def test_oks_repr_hides_parsed() -> None:
    ok = LlmOk({"people": [{"name": "Ann Smith", "quote": "Managing Partner"}]}, **served())

    assert "Ann Smith" not in repr(ok)
    assert "Managing Partner" not in repr(ok)
    assert "parsed" not in repr(ok)
    assert HAIKU in repr(ok)
    with pytest.raises(FrozenInstanceError):
        ok.model = "claude-opus-5-5"  # type: ignore[misc]
