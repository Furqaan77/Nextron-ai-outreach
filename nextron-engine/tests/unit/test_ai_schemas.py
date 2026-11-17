"""The output schemas (``nextron.adapters.ai.schemas``, Story 2.7): the schema walk (no
reasoning-like property, a short ``explanation``, ``snake_case`` enums, a strict wire schema with
no unsupported constraint), case normalisation and validation. Probe schemas prove each check
catches its problem."""

from collections.abc import Iterator, Mapping
from datetime import date
from typing import Any, Final, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from nextron.adapters.ai import schemas
from nextron.adapters.ai.schemas import (
    EXTRACT_PEOPLE_V1,
    EXTRACT_SIGNALS_V1,
    SCHEMAS,
    SIGNAL_INBOX_PARSE_V1,
    OutputSchema,
    json_schema,
    normalise,
    validate,
    wire_schema,
)

FORBIDDEN_NAMES: Final = ("reasoning", "chain_of_thought", "thought")
MAX_EXPLANATION: Final = 200
UNSUPPORTED: Final = ("minLength", "maxLength", "minimum", "maximum")
ENUM_VALUE: Final = r"[a-z][a-z0-9_]*"
NESTED: Final = ("items", "additionalProperties", "not")
VARIANTS: Final = ("anyOf", "allOf", "oneOf", "prefixItems")


# The schema walk --------------------------------------------------------------------------------


def schema_nodes(node: object) -> Iterator[Mapping[str, Any]]:
    """Every schema node under ``node``: itself, its properties, items, variants and $defs."""
    if not isinstance(node, Mapping):
        return
    yield node
    for child in (node.get("properties") or {}).values():
        yield from schema_nodes(child)
    for child in (node.get("$defs") or {}).values():
        yield from schema_nodes(child)
    for key in NESTED:
        yield from schema_nodes(node.get(key))
    for key in VARIANTS:
        for child in node.get(key) or ():
            yield from schema_nodes(child)


def model_problems(tree: Mapping[str, Any]) -> list[str]:
    """What's wrong with a model's own JSON schema: a property named like reasoning, an
    ``explanation`` without ``maxLength`` of 200 or fewer, an enum value not ``snake_case``."""
    import re  # noqa: PLC0415

    problems = []
    for node in schema_nodes(tree):
        for name, child in (node.get("properties") or {}).items():
            lowered = name.lower()
            if any(word in lowered for word in FORBIDDEN_NAMES):
                problems.append(f"property {name} is named like reasoning")
            if "explanation" in lowered:
                limit = child.get("maxLength") if isinstance(child, Mapping) else None
                if not isinstance(limit, int) or limit > MAX_EXPLANATION:
                    problems.append(f"property {name} has no maxLength of 200 or fewer")
        values = [*(node.get("enum") or ()), *([node["const"]] if "const" in node else [])]
        for value in values:
            if not (isinstance(value, str) and re.fullmatch(ENUM_VALUE, value)):
                problems.append(f"enum value {value!r} is not snake_case")
    return problems


def wire_problems(tree: Mapping[str, Any]) -> list[str]:
    """What's wrong with a wire schema: an object without ``additionalProperties: false``, or a
    constraint the API doesn't support."""
    problems = []
    for node in schema_nodes(tree):
        if (node.get("type") == "object" or "properties" in node) and node.get(
            "additionalProperties"
        ) is not False:
            problems.append(f"object {node.get('title')} allows additional properties")
        problems += [f"{key} in {node.get('title')}" for key in UNSUPPORTED if key in node]
    return problems


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda schema: schema.model.__name__)
def test_every_route_schema_passes_the_walk(schema: OutputSchema) -> None:
    assert model_problems(json_schema(schema)) == []
    assert model_problems(wire_schema(schema)) == []
    assert wire_problems(wire_schema(schema)) == []


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda schema: schema.model.__name__)
def test_every_model_forbids_extra_keys_and_requires_every_field(schema: OutputSchema) -> None:
    for node in schema_nodes(json_schema(schema)):
        if "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


def test_the_walk_reaches_every_nested_model() -> None:
    titles = {node.get("title") for node in schema_nodes(wire_schema(EXTRACT_SIGNALS_V1))}

    assert {"ExtractSignalsV1", "Signal", "ProfileFact", "SizeClue"} <= titles


class _Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbeReasoning(_Probe):
    Reasoning: str
    chain_OF_thought: str
    thoughts: list[str]


class ProbeExplanation(_Probe):
    explanation: str
    short_explanation: str = Field(max_length=200)
    long_explanation: str = Field(max_length=201)


class ProbeEnum(_Probe):
    kind: Literal["Staff", "group staff", "ok_1"]


class ProbeNested(_Probe):
    items: list[ProbeEnum]


class ProbeConst(_Probe):
    kind: Literal["Only"]


def test_the_walk_catches_a_property_named_like_reasoning() -> None:
    problems = model_problems(ProbeReasoning.model_json_schema())

    assert problems == [
        "property Reasoning is named like reasoning",
        "property chain_OF_thought is named like reasoning",
        "property thoughts is named like reasoning",
    ]


def test_the_walk_catches_an_explanation_without_a_short_limit() -> None:
    problems = model_problems(ProbeExplanation.model_json_schema())

    assert problems == [
        "property explanation has no maxLength of 200 or fewer",
        "property long_explanation has no maxLength of 200 or fewer",
    ]


def test_the_walk_catches_an_enum_value_that_isnt_snake_case_even_nested() -> None:
    expected = [
        "enum value 'Staff' is not snake_case",
        "enum value 'group staff' is not snake_case",
    ]

    assert model_problems(ProbeEnum.model_json_schema()) == expected
    assert model_problems(ProbeNested.model_json_schema()) == expected
    assert model_problems(ProbeConst.model_json_schema()) == [
        "enum value 'Only' is not snake_case"  # a one-value Literal is a const
    ]


def test_the_wire_check_catches_an_open_object_and_unsupported_constraints() -> None:
    tree = {
        "type": "object",
        "title": "Open",
        "properties": {
            "name": {"type": "string", "title": "Name", "minLength": 1, "maxLength": 9},
            "count": {"type": "integer", "title": "Count", "minimum": 0, "maximum": 5},
            "inner": {"type": "object", "title": "Inner", "properties": {}},
        },
        "additionalProperties": False,
    }

    assert wire_problems(tree) == [
        "minLength in Name",
        "maxLength in Name",
        "minimum in Count",
        "maximum in Count",
        "object Inner allows additional properties",
    ]


def test_the_wire_schema_moves_constraints_into_descriptions() -> None:
    class Limited(_Probe):
        explanation: str = Field(max_length=200)
        count: int = Field(ge=0, le=5)

    wire = wire_schema(OutputSchema(1, Limited))

    assert wire_problems(wire) == []
    assert "maxLength: 200" in wire["properties"]["explanation"]["description"]
    assert model_problems(Limited.model_json_schema()) == []


def test_the_wire_schema_is_computed_once_and_handed_out_as_a_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[type[BaseModel]] = []
    original = schemas.anthropic.transform_schema

    def counting(model: type[BaseModel]) -> dict[str, Any]:
        calls.append(model)
        return original(model)

    class Once(_Probe):
        name: str

    monkeypatch.setattr(schemas.anthropic, "transform_schema", counting)
    schema = OutputSchema(1, Once)
    first, second = wire_schema(schema), wire_schema(schema)
    first["properties"].clear()

    assert calls == [Once]
    assert second == wire_schema(schema)
    assert second["properties"] == {"name": {"type": "string", "title": "Name"}}


def test_the_v1_schemas_have_the_specified_shapes() -> None:
    signals = json_schema(EXTRACT_SIGNALS_V1)
    definitions = signals["$defs"]

    assert list(signals["properties"]) == ["signals", "profile_facts", "size_clues"]
    assert list(definitions["Signal"]["properties"]) == [
        "signal_type",
        "block_id",
        "quote",
        "event_date",
    ]
    assert definitions["ProfileFact"]["properties"]["kind"]["enum"] == [
        "what_we_do",
        "location",
        "technology",
    ]
    assert definitions["SizeClue"]["properties"]["kind"]["enum"] == [
        "lawyers_at_office",
        "lawyers_firm_wide",
        "staff",
        "group_staff",
        "locations",
    ]
    assert definitions["SizeClue"]["properties"]["number"]["type"] == "integer"
    assert list(json_schema(EXTRACT_PEOPLE_V1)["$defs"]["Person"]["properties"]) == [
        "name",
        "role",
        "block_id",
        "quote",
    ]
    inbox = json_schema(SIGNAL_INBOX_PARSE_V1)["properties"]
    assert list(inbox) == [
        "segment",
        "signal_type",
        "website",
        "author_name",
        "author_role",
        "event_date",
        "quote",
    ]
    assert all({"type": "null"} in field["anyOf"] for field in inbox.values())


# Case normalisation and validation -------------------------------------------------------------

SIGNALS: Final = {
    "signals": [
        {
            "signal_type": " Hiring ",
            "block_id": "p1",
            "quote": " We are hiring ",
            "event_date": None,
        }
    ],
    "profile_facts": [
        {"kind": " What_We_Do\n", "value": "Law", "block_id": "p1", "quote": "We do law"}
    ],
    "size_clues": [{"kind": " Staff", "number": 40, "block_id": "p2", "quote": "40 staff"}],
}


def test_case_normalisation_lowers_only_enum_strings() -> None:
    normalised = normalise(EXTRACT_SIGNALS_V1, SIGNALS)

    assert normalised["size_clues"][0]["kind"] == "staff"  # type: ignore[index]
    assert normalised["profile_facts"][0]["kind"] == "what_we_do"  # type: ignore[index]
    assert normalised["signals"][0] == SIGNALS["signals"][0]  # type: ignore[index]
    assert SIGNALS["size_clues"][0]["kind"] == " Staff"  # the input is untouched


def test_validate_returns_the_normalised_object_as_json_data() -> None:
    document = {**SIGNALS, "signals": [{**SIGNALS["signals"][0], "event_date": "2026-11-03"}]}

    parsed = validate(EXTRACT_SIGNALS_V1, document)

    assert parsed is not None
    assert parsed["size_clues"][0]["kind"] == "staff"
    assert parsed["signals"][0]["event_date"] == "2026-11-03"
    assert parsed["signals"][0]["quote"] == " We are hiring "


def test_case_normalisation_follows_any_of_and_nested_lists() -> None:
    class Choice(_Probe):
        kind: Literal["first", "second_one"] | None
        tags: list[list[Literal["x"]]]

    class Outer(_Probe):
        choices: list[Choice]
        when: date | None

    schema = OutputSchema(1, Outer)
    document = {"choices": [{"kind": " SECOND_ONE ", "tags": [[" X"]]}], "when": None}

    assert validate(schema, document) == {
        "choices": [{"kind": "second_one", "tags": [["x"]]}],
        "when": None,
    }


@pytest.mark.parametrize(
    "document",
    [
        {"people": [{"name": "Ann", "role": "Partner", "block_id": "p1"}]},  # a missing key
        {"people": [{"name": "Ann", "role": "Partner", "block_id": "p1", "quote": "q", "x": 1}]},
        {"people": [], "extra": True},
        {"people": [{"name": 7, "role": "Partner", "block_id": "p1", "quote": "q"}]},
        {"people": {"name": "Ann"}},
        {},
        ["people"],
        "people",
        None,
    ],
)
def test_validate_refuses_what_doesnt_fit(document: object) -> None:
    assert validate(EXTRACT_PEOPLE_V1, document) is None


@pytest.mark.parametrize(
    "number",
    ["40", 40.0, True, None],
)
def test_validate_is_strict_about_types(number: object) -> None:
    document = {**SIGNALS, "size_clues": [{**SIGNALS["size_clues"][0], "number": number}]}

    assert validate(EXTRACT_SIGNALS_V1, document) is None


def test_validate_refuses_a_bad_date_and_an_unknown_enum_value() -> None:
    bad_date = {**SIGNALS, "signals": [{**SIGNALS["signals"][0], "event_date": "next week"}]}
    bad_kind = {**SIGNALS, "size_clues": [{**SIGNALS["size_clues"][0], "kind": "partners"}]}

    assert validate(EXTRACT_SIGNALS_V1, bad_date) is None
    assert validate(EXTRACT_SIGNALS_V1, bad_kind) is None


def test_validate_survives_a_very_deep_document() -> None:
    deep: object = "x"
    for _ in range(5_000):
        deep = [deep]

    assert validate(EXTRACT_PEOPLE_V1, {"people": deep}) is None
