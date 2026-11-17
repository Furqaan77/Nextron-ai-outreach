"""The output schemas of the AI routes (AD-8): pydantic models of the JSON each route returns.

Import rule: ``nextron.domain``, ``pydantic``, ``anthropic`` and the standard library.

**``OutputSchema(version, model)``** pairs a schema version (what a route tuple's
``schema_version`` names) with a pydantic model. Every model is ``extra="forbid"``, every
field is required (a missing value is ``null``), and every enum value is lowercase
``snake_case``. A schema checks shape only: the business rules (quotes of 300 characters or
fewer that are exact substrings of the cited page, Signal types of the Lead's Segment, Segment
keys) belong to the services that read the answer (Stories 2.9, 2.10 and 2.16). No property is
named like ``reasoning``, ``chain_of_thought`` or ``thought``, and an ``explanation`` property
would need a ``max_length`` of 200 or fewer (``tests/unit/test_ai_schemas.py`` checks both).

- ``EXTRACT_SIGNALS_V1`` (``ExtractSignalsV1``): ``signals`` (``signal_type``, ``block_id``,
  ``quote``, ``event_date``: a date or ``null``), ``profile_facts`` (``kind``: ``what_we_do``,
  ``location`` or ``technology``; ``value``; ``block_id``; ``quote``) and ``size_clues``
  (``kind``: ``lawyers_at_office``, ``lawyers_firm_wide``, ``staff``, ``group_staff`` or
  ``locations``; ``number``, an integer; ``block_id``; ``quote``) and, since Story 2.8,
  ``job_postings`` (``title``; ``url`` and ``posted_date``, a date, each ``null`` when the page
  doesn't give it; ``block_id``; ``quote``), which Story 2.11 reads.
- ``EXTRACT_PEOPLE_V1`` (``ExtractPeopleV1``): ``people`` (``name``, ``role``, ``block_id``,
  ``quote``).
- ``SIGNAL_INBOX_PARSE_V1`` (``SignalInboxParseV1``): ``segment``, ``signal_type``,
  ``website``, ``author_name``, ``author_role``, ``event_date`` (a date) and ``quote``, each
  of them ``null`` when the item doesn't say.

**``wire_schema(schema)``** is ``anthropic.transform_schema(model)``, computed once per schema
(each call returns a fresh copy): the JSON schema sent in ``output_config.format``. Every
object in it has ``additionalProperties: false``; a constraint the API doesn't support moves
into a description, and pydantic still checks it locally. ``json_schema(schema)`` is the
model's own ``model_json_schema()``, computed once too: what case normalisation walks.

**``validate(schema, document)``** case-normalises ``document`` (a parsed JSON object) and
validates it, returning the validated object as JSON-ready data, or ``None`` when it doesn't
fit. Normalisation walks the document alongside ``json_schema(schema)``, resolving ``$ref``,
``anyOf``, ``items`` and ``properties``, and turns every string whose schema has an ``enum``
(or a string ``const``, pydantic's one-value enum) into ``strip().lower()``; nothing else
changes. Validation is pydantic's strict JSON mode, so
``"5"`` is not an integer and an unknown key is refused.
"""

import copy
import functools
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

import anthropic
import pydantic
from pydantic import BaseModel, ConfigDict

MAX_DEPTH: Final = 64  # how deep normalisation walks; a document is never this deep
DEFS: Final = "#/$defs/"  # how pydantic's JSON schema refers to a nested model


class _Strict(BaseModel):
    """A route output: extra keys are refused."""

    model_config = ConfigDict(extra="forbid")


# extract_signals --------------------------------------------------------------------------------


class Signal(_Strict):
    signal_type: str
    block_id: str
    quote: str
    event_date: date | None


class ProfileFact(_Strict):
    kind: Literal["what_we_do", "location", "technology"]
    value: str
    block_id: str
    quote: str


class SizeClue(_Strict):
    kind: Literal["lawyers_at_office", "lawyers_firm_wide", "staff", "group_staff", "locations"]
    number: int
    block_id: str
    quote: str


class JobPosting(_Strict):
    title: str
    url: str | None
    posted_date: date | None
    block_id: str
    quote: str


class ExtractSignalsV1(_Strict):
    signals: list[Signal]
    profile_facts: list[ProfileFact]
    size_clues: list[SizeClue]
    job_postings: list[JobPosting]


# extract_people ---------------------------------------------------------------------------------


class Person(_Strict):
    name: str
    role: str
    block_id: str
    quote: str


class ExtractPeopleV1(_Strict):
    people: list[Person]


# signal_inbox_parse -----------------------------------------------------------------------------


class SignalInboxParseV1(_Strict):
    segment: str | None
    signal_type: str | None
    website: str | None
    author_name: str | None
    author_role: str | None
    event_date: date | None
    quote: str | None


# Schemas ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutputSchema:
    """A schema version and the pydantic model it names."""

    version: int
    model: type[BaseModel]


EXTRACT_SIGNALS_V1: Final = OutputSchema(1, ExtractSignalsV1)
EXTRACT_PEOPLE_V1: Final = OutputSchema(1, ExtractPeopleV1)
SIGNAL_INBOX_PARSE_V1: Final = OutputSchema(1, SignalInboxParseV1)
SCHEMAS: Final = (EXTRACT_SIGNALS_V1, EXTRACT_PEOPLE_V1, SIGNAL_INBOX_PARSE_V1)


@functools.cache
def _wire(schema: OutputSchema) -> dict[str, Any]:
    return anthropic.transform_schema(schema.model)


@functools.cache
def _json(schema: OutputSchema) -> dict[str, Any]:
    return schema.model.model_json_schema()


def wire_schema(schema: OutputSchema) -> dict[str, Any]:
    """The JSON schema for ``output_config.format``; a fresh copy each call."""
    return copy.deepcopy(_wire(schema))


def json_schema(schema: OutputSchema) -> dict[str, Any]:
    """The model's own JSON schema; a fresh copy each call."""
    return copy.deepcopy(_json(schema))


# Case normalisation and validation --------------------------------------------------------------


def _variants(
    node: object, definitions: Mapping[str, Any], depth: int = 0
) -> Iterator[Mapping[str, Any]]:
    """``node`` and every schema it reaches through ``$ref`` and ``anyOf``."""
    if depth > MAX_DEPTH or not isinstance(node, Mapping):
        return
    reference = node.get("$ref")
    if isinstance(reference, str) and reference.startswith(DEFS):
        target = definitions.get(reference.removeprefix(DEFS))
        yield from _variants(target, definitions, depth + 1)
    yield node
    variants = node.get("anyOf")
    for variant in variants if isinstance(variants, list) else ():
        yield from _variants(variant, definitions, depth + 1)


def _normalise(value: object, node: object, definitions: Mapping[str, Any], depth: int) -> object:
    if depth > MAX_DEPTH:
        return value
    variants = list(_variants(node, definitions))
    if isinstance(value, str):
        if any("enum" in variant or isinstance(variant.get("const"), str) for variant in variants):
            return value.strip().lower()
        return value
    if isinstance(value, list):
        items = [variant["items"] for variant in variants if "items" in variant]
        return [_normalise(item, {"anyOf": items}, definitions, depth + 1) for item in value]
    if isinstance(value, dict):
        normalised = {}
        for key, item in value.items():
            properties = [
                variant["properties"][key]
                for variant in variants
                if isinstance(variant.get("properties"), Mapping) and key in variant["properties"]
            ]
            normalised[key] = _normalise(item, {"anyOf": properties}, definitions, depth + 1)
        return normalised
    return value


def normalise(schema: OutputSchema, document: object) -> object:
    """``document`` with every enum-typed string stripped and lowercased."""
    tree = _json(schema)
    definitions = tree.get("$defs", {})
    return _normalise(document, tree, definitions if isinstance(definitions, Mapping) else {}, 0)


def validate(schema: OutputSchema, document: object) -> dict[str, Any] | None:
    """The normalised, validated ``document`` as JSON-ready data, or ``None`` when it doesn't
    fit the schema (or isn't a JSON object)."""
    if not isinstance(document, dict):
        return None
    try:
        text = json.dumps(normalise(schema, document))
        validated = schema.model.model_validate_json(text, strict=True)
    except (pydantic.ValidationError, ValueError, TypeError, RecursionError):
        return None
    return validated.model_dump(mode="json")
