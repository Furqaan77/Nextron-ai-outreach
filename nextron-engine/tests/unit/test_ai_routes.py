"""The AI routes as data (``nextron.adapters.ai.routes``, Story 2.7): the exact candidate table,
the lookups, the token-limit rules refused at construction, a price for every model, and
server-side ``fallbacks`` only on the Opus ``grounding_check_fallback`` (Story 4.14)."""

import dataclasses
from dataclasses import replace
from typing import Any, Final

import pytest

from nextron.adapters.ai import pricing, routes
from nextron.adapters.ai.routes import CANDIDATES, Candidate, candidates, default_candidate, find
from nextron.adapters.ai.schemas import (
    EXTRACT_PEOPLE_V1,
    EXTRACT_SIGNALS_V1,
    GROUNDING_CHECK_V1,
    SIGNAL_INBOX_PARSE_V1,
    ExtractPeopleV1,
    ExtractSignalsV1,
    GroundingCheckV1,
    OutputSchema,
    SignalInboxParseV1,
)
from nextron.domain.ai import Effort, RouteSpec, RouteTuple
from nextron.domain.budget import BudgetClass
from nextron.domain.playbook import PlaybookSnapshot

PROMPT: Final = "Extract."
PLAYBOOK: Final = PlaybookSnapshot("0.2.0", "---\nversion: 0.2.0\n---\nRules.\n")

# name, model, effort, prompt, schema model, schema version, batchable, max_tokens, cap, class,
# uses_playbook (Story 4.4), server_fallbacks (Story 4.14: only the Opus grounding fallback)
TABLE: Final = [
    (
        "extract_signals",
        "claude-haiku-5-5",
        Effort.MEDIUM,
        "v1",
        ExtractSignalsV1,
        1,
        True,
        8_000,
        16_000,
        BudgetClass.BLOCKABLE,
        False,
        False,
    ),
    (
        "extract_people",
        "claude-haiku-5-5",
        Effort.MEDIUM,
        "v1",
        ExtractPeopleV1,
        1,
        True,
        4_000,
        16_000,
        BudgetClass.BLOCKABLE,
        False,
        False,
    ),
    (
        "signal_inbox_parse",
        "claude-haiku-5-5",
        Effort.MEDIUM,
        "v1",
        SignalInboxParseV1,
        1,
        False,
        2_000,
        8_000,
        BudgetClass.BLOCKABLE,
        False,
        False,
    ),
    (
        "grounding_check",
        "claude-haiku-5-5",
        Effort.MEDIUM,
        "v1",
        GroundingCheckV1,
        1,
        False,
        4_000,
        8_000,
        BudgetClass.ESSENTIAL,
        True,
        False,
    ),
    (
        "grounding_check_fallback",
        "claude-opus-5-5",
        Effort.MEDIUM,
        "v1",
        GroundingCheckV1,
        1,
        False,
        4_000,
        8_000,
        BudgetClass.ESSENTIAL,
        True,
        True,
    ),
]


def _row(candidate: Candidate) -> tuple[Any, ...]:
    return (
        candidate.name,
        candidate.model,
        candidate.effort,
        candidate.prompt_version,
        candidate.output_schema.model,
        candidate.output_schema.version,
        candidate.batchable,
        candidate.max_tokens,
        candidate.max_tokens_cap,
        candidate.budget_class,
        candidate.uses_playbook,
        candidate.server_fallbacks,
    )


def tuple_of(candidate: Candidate, **changes: Any) -> RouteTuple:
    fields: dict[str, Any] = {
        "route": candidate.name,
        "model": candidate.model,
        "effort": candidate.effort,
        "prompt_version": candidate.prompt_version,
        "schema_version": candidate.output_schema.version,
        "system_prompt": PROMPT,
    }
    return RouteTuple(**(fields | changes))


def test_the_candidates_are_exactly_the_specified_table() -> None:
    assert list(CANDIDATES) == [
        "extract_signals",
        "extract_people",
        "signal_inbox_parse",
        "grounding_check",
        "grounding_check_fallback",
    ]
    assert [_row(found) for route in CANDIDATES for found in CANDIDATES[route]] == TABLE
    assert [schema.version for schema in (EXTRACT_SIGNALS_V1, EXTRACT_PEOPLE_V1)] == [1, 1]
    assert SIGNAL_INBOX_PARSE_V1 == OutputSchema(1, SignalInboxParseV1)
    assert GROUNDING_CHECK_V1 == OutputSchema(1, GroundingCheckV1)


def test_each_candidate_is_filed_under_its_own_route() -> None:
    for route, found in CANDIDATES.items():
        assert found
        assert {candidate.name for candidate in found} == {route}


def test_the_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        CANDIDATES["extract_people"] = ()  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        CANDIDATES["extract_people"][0].max_tokens = 1  # type: ignore[misc]


def test_the_lookups_find_each_route_and_its_default() -> None:
    for route, found in CANDIDATES.items():
        assert candidates(route) == found
        assert default_candidate(route) is found[0]
        playbook = PLAYBOOK if found[0].uses_playbook else None
        assert find(tuple_of(found[0], playbook=playbook)) is found[0]
    assert candidates("draft_email") == ()
    assert default_candidate("draft_email") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"route": "draft_email"},
        {"model": "claude-opus-5-5"},
        {"effort": Effort.HIGH},
        {"prompt_version": "v2"},
        {"schema_version": 2},
    ],
)
def test_find_needs_every_field_of_the_tuple_to_match(changes: dict[str, Any]) -> None:
    candidate = CANDIDATES["extract_signals"][0]

    assert find(tuple_of(candidate, **changes)) is None


def test_find_needs_the_playbook_to_match_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # Row: Mismatch
    candidate = CANDIDATES["extract_signals"][0]
    playbook_candidate = replace(candidate, uses_playbook=True)

    assert find(tuple_of(candidate, playbook=PLAYBOOK)) is None  # a Playbook tuple
    assert find(tuple_of(candidate)) is candidate
    monkeypatch.setattr(
        routes, "CANDIDATES", {**CANDIDATES, "extract_signals": (playbook_candidate,)}
    )
    assert find(tuple_of(playbook_candidate, playbook=PLAYBOOK)) is playbook_candidate
    assert find(tuple_of(playbook_candidate)) is None  # a tuple without its Playbook


def test_the_default_specs_carry_uses_playbook() -> None:
    specs = routes.default_specs()

    assert [spec.route for spec in specs] == list(CANDIDATES)
    assert [spec.uses_playbook for spec in specs] == [False, False, False, True, True]
    assert specs[0] == RouteSpec(
        "extract_signals", "claude-haiku-5-5", Effort.MEDIUM, "v1", 1, uses_playbook=False
    )


def test_find_returns_none_for_anything_but_a_route_tuple() -> None:
    assert find("extract_signals") is None  # type: ignore[arg-type]
    assert find(None) is None  # type: ignore[arg-type]


def test_the_system_prompt_doesnt_choose_the_candidate() -> None:
    candidate = CANDIDATES["extract_people"][0]

    assert find(tuple_of(candidate, system_prompt="Another prompt.")) is candidate


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"max_tokens": 0}, "max_tokens must be above 0"),
        ({"max_tokens": 16_001, "max_tokens_cap": 16_000}, "at most max_tokens_cap"),
        ({"max_tokens": 9_000, "max_tokens_cap": 8_000}, "at most max_tokens_cap"),
        ({"max_tokens_cap": 16_001}, "max_tokens_cap must be at most 16,000"),
        ({"max_tokens_cap": 32_000}, "max_tokens_cap must be at most 16,000"),
    ],
)
def test_a_candidate_outside_the_token_limits_is_refused_at_construction(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(CANDIDATES["extract_people"][0], **changes)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"max_tokens": True}, TypeError),
        ({"max_tokens_cap": 16_000.0}, TypeError),
        ({"name": "Extract People"}, ValueError),
        ({"model": "Claude"}, ValueError),
        ({"effort": "medium"}, TypeError),
        ({"prompt_version": "1"}, ValueError),
        ({"output_schema": OutputSchema(0, ExtractPeopleV1)}, ValueError),
        ({"output_schema": ExtractPeopleV1}, TypeError),
        ({"uses_playbook": 1}, TypeError),
        ({"uses_playbook": None}, TypeError),
        ({"server_fallbacks": 1}, TypeError),
        ({"server_fallbacks": True}, ValueError),  # a Haiku candidate: no server fallback
    ],
)
def test_a_candidate_with_a_bad_route_field_is_refused(
    changes: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        replace(CANDIDATES["extract_people"][0], **changes)


def test_a_candidate_at_the_ceiling_is_accepted() -> None:
    probe = replace(CANDIDATES["extract_people"][0], max_tokens=16_000, max_tokens_cap=16_000)

    assert (probe.max_tokens, probe.max_tokens_cap) == (16_000, routes.MAX_TOKENS_CEILING)


def test_every_candidate_model_has_a_price() -> None:
    models = {found.model for route in CANDIDATES.values() for found in route}

    assert models <= set(pricing.PRICES)


def test_only_an_opus_candidate_that_isnt_batchable_carries_server_fallbacks() -> None:
    opus = CANDIDATES["grounding_check_fallback"][0]

    assert [c.name for r in CANDIDATES.values() for c in r if c.server_fallbacks] == [
        "grounding_check_fallback"
    ]
    with pytest.raises(ValueError, match="batchable"):
        replace(opus, batchable=True)
    with pytest.raises(ValueError, match="Opus"):
        replace(opus, model="claude-haiku-5-5")


def test_the_candidate_fields_are_exactly_these() -> None:
    names = [field.name.lower() for field in dataclasses.fields(Candidate)]

    assert not [name for name in names if "beta" in name]
    assert names == [
        "name",
        "model",
        "effort",
        "prompt_version",
        "output_schema",
        "batchable",
        "max_tokens",
        "max_tokens_cap",
        "budget_class",
        "uses_playbook",
        "server_fallbacks",
    ]
