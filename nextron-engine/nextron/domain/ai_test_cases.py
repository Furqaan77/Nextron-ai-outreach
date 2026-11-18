"""The AI Test Set cases and the extraction scorer (FR-73, AD-12, AD-18, Story 4.5). Standard
library and ``json`` only.

**``TestCase(case_key, test_set, route, input, expected, covers)``** is one synthetic case of an
AI Test Set, frozen. Construction raises ``TypeError`` or ``ValueError`` naming the field, never
its value:

- ``case_key`` fullmatches ``CASE_KEY_PATTERN`` (``[a-z0-9][a-z0-9_.-]{0,63}``);
- ``test_set`` is a ``nextron.domain.ai_test_gate.TestSet`` and ``route`` a route of that set
  (``ROUTE_SETS``);
- ``input`` and ``expected`` are JSON objects (``dict``): each is kept as its canonical JSON
  read back, so a later change to the caller's object changes nothing, and neither shows in the
  ``repr``;
- ``covers`` is a tuple of tokens fullmatching ``[a-z0-9_:.-]{1,64}``: what the case covers
  (Story 4.6's coverage tests read them).

``input`` and ``expected`` must also encode as UTF-8 (a lone surrogate is a ``ValueError``,
Story 4.6), so every case can be hashed and stored.

``sha256`` is the hex SHA-256 of the case's canonical JSON (every field, sorted keys, compact,
UTF-8): a fixture file edited in any way gives a new hash, so ``Playbook.sync_test_cases``
retires the old row. ``stored_case(case_key, test_set, route, input_json, expected_json,
covers_json)`` builds the case back from a stored row's JSON text (``InvalidTestCases`` naming
the case key when it doesn't read back).

**The case-set fingerprint** (Story 4.6). ``case_set_sha256(shas)`` is the hex SHA-256 of the
canonical JSON of the sorted case SHA-256s (a list of strings): the identity of a set of cases,
whatever their order. A test run stores the fingerprint of the cases it measured, so a run is
bound to them (``Playbook.activate`` accepts only a pass on the live set). ``CaseOrigin`` is
where a stored case came from: ``fixture`` (a file under ``tests/ai_sets``) or ``override`` (an
Operator's Reply class override, pseudonymised; ``Playbook.add_override_case``).
``OVERRIDE_PREFIX`` (``override-``) starts every override case's key and no fixture's.

**``InvalidTestCases(where, reason)``** (code ``invalid_test_cases``, a ``DomainError``) is a
case file, or a stored case, that can't be used. Its message is ``<where>: <reason>``: the file
(or case key) and the field, never a case's text.

**Extraction cases.** ``extraction_case(name, data)`` reads Story 2.8's case format (the rules
of ``tests/ai_eval/extraction.py``'s ``load_case``) from the JSON ``data`` of the file ``name``:
an object with exactly ``case``, ``route``, ``segment``, ``injection``, ``blocks`` and
``expect``; ``expect`` with exactly ``signals``, ``people`` and ``forbidden_signals``; ``case``
(the file's stem), ``route`` (an extraction route) and ``segment`` non-empty strings;
``injection`` ``null`` or one of ``INJECTIONS``; ``blocks`` a list of ``{id, source, text}``,
``signals`` of ``{signal_type, block_id, quote}`` and ``people`` of ``{name, role, block_id,
quote}``, each field a non-empty string; ``forbidden_signals`` a list of strings. Story 4.6 also
checks each block as ``nextron.domain.ai.UntrustedBlock`` would at call time
(``check_block_fields``: ``source`` fullmatches ``SOURCE_PATTERN``, ``id`` ``BLOCK_ID_PATTERN``,
and ``text`` encodes as UTF-8), the block ids unique and never ``signal_types`` (the catalogue
block's id), and each label's ``block_id`` one of the case's blocks, so a bad fixture stops
``serve`` instead of failing a paid run; ``case`` never starts with ``override-`` (the
Operator's override cases own those keys). ``nextron.main`` passes ``name`` as
``extraction/<file>``, so a message names the set's folder too. The case's
``case_key`` is ``case``, its ``input`` ``{segment, blocks}``, its ``expected`` the ``expect``
object, and its ``covers`` ``[segment, injection]``, or ``[segment, "clean"]``.

``extraction_inputs(case, number, segments)`` gives the call's ``(blocks, urgency)`` for case
``number`` (1-based), as the harness's ``case_blocks`` does: one subject (``lead:case<n>``, or
``signal_item:case<n>`` for ``signal_inbox_parse``), a ``signal_types`` block
(``nextron.domain.segments.signal_catalogue``) of the case's Segment for ``extract_signals`` and
of every Segment for ``signal_inbox_parse`` (none for ``extract_people``), then the case's
blocks; urgency ``bulk``, or ``soon`` for an inbox item. ``check_segments(cases, segments)``
raises ``InvalidTestCases`` for the first case whose Segment isn't among ``segments`` (the
stored Segments), before any call.

``score_extraction(case, result)`` ports the harness's ``evaluate`` to one case and gives
``(ExtractionOutcome, CaseResult)``. ``result`` is the call's parsed answer (an ``LlmOk``'s
``parsed``: a ``Mapping``) or, for any other result, its type name (a ``str``), because the
domain never imports the port. It counts the quotes proposed (every ``quote`` of every list, and
an inbox answer's ``quote`` when it isn't ``null``, which cites the item's block) and those that
pass (the cited block is one of the case's blocks and ``quote_in`` holds); the labelled Signal
types found (an inbox answer's type counts only under the case's Segment); the labelled people
found (by name, ``normalise``'d and case-folded); and the forbidden types proposed, whatever the
Segment. The case fails on any failing quote, any missed label (Signal or person), any forbidden
type, or a result that isn't an answer, whose ``got`` is then the result's type name.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath
from typing import Any, Final

from nextron.domain.ai import BLOCK_ID_PATTERN, SOURCE_PATTERN, UntrustedBlock, Urgency
from nextron.domain.ai_test_gate import (
    EXTRACT_PEOPLE,
    EXTRACT_SIGNALS,
    ROUTE_SETS,
    SIGNAL_INBOX_PARSE,
    CaseResult,
    ExtractionOutcome,
    TestSet,
    set_of,
)
from nextron.domain.budget import CODE_PATTERN
from nextron.domain.errors import DomainError
from nextron.domain.quotes import normalise, quote_in
from nextron.domain.segments import SegmentDef, signal_catalogue

CASE_KEY_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
COVER_PATTERN: Final = re.compile(r"[a-z0-9_:.-]{1,64}")
INJECTIONS: Final = ("hidden_instructions", "invented_quote", "dictated_tier")
CLEAN: Final = "clean"
SIGNAL_TYPES: Final = "signal_types"  # the catalogue block's source and block_id
CASE_FIELDS: Final = frozenset({"case", "route", "segment", "injection", "blocks", "expect"})
EXPECT_FIELDS: Final = frozenset({"signals", "people", "forbidden_signals"})
BLOCK_FIELDS: Final = ("id", "source", "text")
SIGNAL_FIELDS: Final = ("signal_type", "block_id", "quote")
PERSON_FIELDS: Final = ("name", "role", "block_id", "quote")
QUOTED_LISTS: Final = ("signals", "profile_facts", "size_clues", "job_postings", "people")
NONE_TEXT: Final = "none"
OVERRIDE_PREFIX: Final = "override-"


class CaseOrigin(StrEnum):
    """Where a stored test case came from (Story 4.6)."""

    FIXTURE = "fixture"
    OVERRIDE = "override"


class InvalidTestCases(DomainError):
    """A test case file, or a stored case, that can't be used; the message names the file (or
    case key) and the field, never a case's text."""

    code = "invalid_test_cases"

    def __init__(self, where: str, reason: str) -> None:
        super().__init__(f"{where}: {reason}")
        self.where = where
        self.reason = reason


def canonical_json(value: object) -> str:
    """``value`` as canonical JSON: sorted keys, compact, non-ASCII kept; ``ValueError`` for a
    value JSON can't hold (``NaN`` included)."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError, RecursionError):
        raise ValueError("the value is not plain JSON") from None


def _json_object(name: str, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a dict")
    try:
        text = canonical_json(value)
    except ValueError:
        raise ValueError(f"{name} must be plain JSON") from None
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must encode as UTF-8") from None
    copied = json.loads(text)
    if not isinstance(copied, dict):  # pragma: no cover - a dict always reads back as one
        raise TypeError(f"{name} must be a dict")
    return copied


@dataclass(frozen=True, slots=True)
class TestCase:
    """One case of an AI Test Set; see the module docstring."""

    __test__ = False  # pytest: a domain value, not a test class

    case_key: str
    test_set: TestSet
    route: str
    input: dict[str, Any] = field(repr=False)
    expected: dict[str, Any] = field(repr=False)
    covers: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.case_key, str):
            raise TypeError("case_key must be a str")
        if CASE_KEY_PATTERN.fullmatch(self.case_key) is None:
            raise ValueError(f"case_key must match {CASE_KEY_PATTERN.pattern}")
        if not isinstance(self.test_set, TestSet):
            raise TypeError("test_set must be a TestSet")
        if not isinstance(self.route, str) or CODE_PATTERN.fullmatch(self.route) is None:
            raise ValueError(f"route must match {CODE_PATTERN.pattern}")
        if ROUTE_SETS.get(self.route) is not self.test_set:
            raise ValueError("route must be a route of the test set")
        object.__setattr__(self, "input", _json_object("input", self.input))
        object.__setattr__(self, "expected", _json_object("expected", self.expected))
        if not isinstance(self.covers, tuple) or not all(
            isinstance(token, str) and COVER_PATTERN.fullmatch(token) for token in self.covers
        ):
            raise ValueError(f"covers must be a tuple of tokens matching {COVER_PATTERN.pattern}")

    __hash__ = None  # type: ignore[assignment]  # input and expected are dicts

    def as_json(self) -> dict[str, Any]:
        """The case as one JSON object."""
        return {
            "case_key": self.case_key,
            "test_set": self.test_set.value,
            "route": self.route,
            "input": self.input,
            "expected": self.expected,
            "covers": list(self.covers),
        }

    @property
    def sha256(self) -> str:
        """The hex SHA-256 of the case's canonical JSON."""
        return hashlib.sha256(canonical_json(self.as_json()).encode("utf-8")).hexdigest()


def case_set_sha256(shas: Iterable[str]) -> str:
    """The hex SHA-256 of the canonical JSON of the sorted case SHA-256s ``shas``; see the
    module docstring."""
    chosen = list(shas)
    if not all(isinstance(sha, str) for sha in chosen):
        raise TypeError("case_set_sha256 takes str SHA-256s")
    return hashlib.sha256(canonical_json(sorted(chosen)).encode("utf-8")).hexdigest()


def stored_case(
    case_key: str,
    test_set: str,
    route: str,
    input_json: str,
    expected_json: str,
    covers_json: str,
) -> TestCase:
    """The ``TestCase`` a stored row holds (its JSON text read back); ``InvalidTestCases``
    naming the case key when it no longer makes one."""
    try:
        found = set_of(test_set)
        covers = json.loads(covers_json)
        if found is None or not isinstance(covers, list):
            raise ValueError("not a test case")
        return TestCase(
            case_key,
            found,
            route,
            json.loads(input_json),
            json.loads(expected_json),
            tuple(covers),
        )
    except (TypeError, ValueError, RecursionError):
        raise InvalidTestCases(f"stored test case {case_key}", "doesn't read back") from None


# Extraction cases --------------------------------------------------------------------------------


def _fields(value: object, names: frozenset[str], where: str, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != names:
        raise InvalidTestCases(where, f"{path} must be an object with exactly {sorted(names)}")
    return value


def _text(value: object, where: str, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidTestCases(where, f"{path} must be a non-empty string")
    return value


def _list(value: object, where: str, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidTestCases(where, f"{path} must be a list")
    return value


def _items(
    value: object, names: tuple[str, ...], where: str, path: str
) -> list[dict[str, str]]:
    found = []
    for index, item in enumerate(_list(value, where, path)):
        at = f"{path}[{index}]"
        chosen = _fields(item, frozenset(names), where, at)
        found.append({name: _text(chosen[name], where, f"{at}.{name}") for name in names})
    return found


def check_block_fields(source: str, block_id: str, text: str, where: str, path: str) -> None:
    """Raise ``InvalidTestCases`` unless a block's fields fit ``UntrustedBlock``'s rules: the
    ``source`` and ``block_id`` patterns, and a ``text`` that encodes as UTF-8."""
    if SOURCE_PATTERN.fullmatch(source) is None:
        raise InvalidTestCases(where, f"{path}.source must match {SOURCE_PATTERN.pattern}")
    if BLOCK_ID_PATTERN.fullmatch(block_id) is None:
        raise InvalidTestCases(where, f"{path}.id must match {BLOCK_ID_PATTERN.pattern}")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidTestCases(where, f"{path}.text must encode as UTF-8") from None


def _check_blocks(blocks: Sequence[Mapping[str, str]], name: str) -> None:
    seen: set[str] = set()
    for index, block in enumerate(blocks):
        at = f"blocks[{index}]"
        check_block_fields(block["source"], block["id"], block["text"], name, at)
        if block["id"] == SIGNAL_TYPES or block["id"] in seen:
            raise InvalidTestCases(name, f"{at}.id must be unique and never {SIGNAL_TYPES}")
        seen.add(block["id"])


def extraction_case(name: str, data: object) -> TestCase:
    """The extraction case in the JSON ``data`` of the file ``name``; ``InvalidTestCases`` when
    it isn't shaped as the module docstring says."""
    case = _fields(data, CASE_FIELDS, name, "the case")
    expect = _fields(case["expect"], EXPECT_FIELDS, name, "expect")
    key = _text(case["case"], name, "case")
    if key != PurePath(name).stem or CASE_KEY_PATTERN.fullmatch(key) is None:
        raise InvalidTestCases(
            name, f"case must be the file's stem and match {CASE_KEY_PATTERN.pattern}"
        )
    if key.startswith(OVERRIDE_PREFIX):
        raise InvalidTestCases(name, f"case must not start with {OVERRIDE_PREFIX}")
    route = _text(case["route"], name, "route")
    if ROUTE_SETS.get(route) is not TestSet.EXTRACTION:
        raise InvalidTestCases(name, "route must be an extraction route")
    segment = _text(case["segment"], name, "segment")
    if COVER_PATTERN.fullmatch(segment) is None:
        raise InvalidTestCases(name, f"segment must match {COVER_PATTERN.pattern}")
    injection = case["injection"]
    if injection is not None and injection not in INJECTIONS:
        raise InvalidTestCases(name, f"injection must be null or one of {list(INJECTIONS)}")
    blocks = _items(case["blocks"], BLOCK_FIELDS, name, "blocks")
    _check_blocks(blocks, name)
    signals = _items(expect["signals"], SIGNAL_FIELDS, name, "expect.signals")
    people = _items(expect["people"], PERSON_FIELDS, name, "expect.people")
    ids = {block["id"] for block in blocks}
    for path, labels in (("expect.signals", signals), ("expect.people", people)):
        for index, label in enumerate(labels):
            if label["block_id"] not in ids:
                raise InvalidTestCases(
                    name, f"{path}[{index}].block_id must name one of the case's blocks"
                )
    forbidden = _list(expect["forbidden_signals"], name, "expect.forbidden_signals")
    if not all(isinstance(key_, str) for key_ in forbidden):
        raise InvalidTestCases(name, "expect.forbidden_signals must hold strings")
    return TestCase(
        case_key=key,
        test_set=TestSet.EXTRACTION,
        route=route,
        input={"segment": segment, "blocks": blocks},
        expected={"signals": signals, "people": people, "forbidden_signals": list(forbidden)},
        covers=(segment, injection if injection is not None else CLEAN),
    )


def _segment(case: TestCase) -> str:
    return str(case.input["segment"])


def _blocks(case: TestCase) -> list[Mapping[str, str]]:
    return list(case.input["blocks"])


def check_segments(cases: Iterable[TestCase], segments: Sequence[SegmentDef]) -> None:
    """Raise ``InvalidTestCases`` for the first case whose Segment isn't among ``segments``."""
    keys = {segment.key for segment in segments}
    for case in cases:
        if _segment(case) not in keys:
            raise InvalidTestCases(
                f"test case {case.case_key}",
                "its Segment isn't stored; run nextron seed sync, then restart the engine",
            )


def extraction_inputs(
    case: TestCase, number: int, segments: Sequence[SegmentDef]
) -> tuple[list[UntrustedBlock], Urgency]:
    """The blocks and urgency of case ``number`` (1-based); see the module docstring."""
    inbox = case.route == SIGNAL_INBOX_PARSE
    subject = f"signal_item:case{number}" if inbox else f"lead:case{number}"
    blocks: list[UntrustedBlock] = []
    if case.route == EXTRACT_SIGNALS:
        listed = [segment for segment in segments if segment.key == _segment(case)]
        catalogue = signal_catalogue(listed)
        blocks.append(UntrustedBlock(subject, SIGNAL_TYPES, SIGNAL_TYPES, catalogue))
    elif inbox:
        catalogue = signal_catalogue(segments)
        blocks.append(UntrustedBlock(subject, SIGNAL_TYPES, SIGNAL_TYPES, catalogue))
    blocks += [
        UntrustedBlock(subject, block["source"], block["id"], block["text"])
        for block in _blocks(case)
    ]
    return blocks, Urgency.SOON if inbox else Urgency.BULK


def _block(case: TestCase, block_id: object) -> Mapping[str, str] | None:
    """The case's block ``block_id``, or ``None``."""
    return next((block for block in _blocks(case) if block["id"] == block_id), None)


def _proposed_quotes(case: TestCase, parsed: Mapping[str, Any]) -> list[tuple[object, object]]:
    """``(block_id, quote)`` of every quote the answer proposed."""
    if case.route == SIGNAL_INBOX_PARSE:
        quote = parsed.get("quote")
        blocks = _blocks(case)
        item = blocks[0]["id"] if blocks else None
        return [] if quote is None else [(item, quote)]
    found = []
    for name in QUOTED_LISTS:
        items = parsed.get(name)
        for item in items if isinstance(items, list) else ():
            if isinstance(item, Mapping):
                found.append((item.get("block_id"), item.get("quote")))
    return found


def _proposed_types(case: TestCase, parsed: Mapping[str, Any]) -> set[str]:
    if case.route == SIGNAL_INBOX_PARSE:
        signal_type = parsed.get("signal_type")
        return {signal_type} if isinstance(signal_type, str) else set()
    signals = parsed.get("signals")
    return {
        item["signal_type"]
        for item in (signals if isinstance(signals, list) else ())
        if isinstance(item, Mapping) and isinstance(item.get("signal_type"), str)
    }


def _found_types(case: TestCase, parsed: Mapping[str, Any], types: set[str]) -> set[str]:
    """The proposed types that count for recall: an inbox answer's only under the case's
    Segment."""
    if case.route == SIGNAL_INBOX_PARSE and parsed.get("segment") != _segment(case):
        return set()
    return types


def _proposed_names(parsed: Mapping[str, Any]) -> set[str]:
    people = parsed.get("people")
    return {
        normalise(item["name"]).casefold()
        for item in (people if isinstance(people, list) else ())
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }


def _passes(case: TestCase, block_id: object, quote: object) -> bool:
    block = _block(case, block_id)
    return (
        block is not None
        and block["source"] != SIGNAL_TYPES
        and isinstance(quote, str)
        and quote_in(quote, block["text"])
    )


def _listed(values: Iterable[str]) -> str:
    chosen = sorted(values)
    return ", ".join(chosen) if chosen else NONE_TEXT


def _expected_text(case: TestCase) -> str:
    expected = case.expected
    if case.route == EXTRACT_PEOPLE:
        text = "People: " + _listed(person["name"] for person in expected["people"])
    else:
        text = "Signals: " + _listed({label["signal_type"] for label in expected["signals"]})
        forbidden = expected["forbidden_signals"]
        if forbidden:
            text += "; never: " + _listed(set(forbidden))
    return text + "; every quote in its block"


def _got_text(
    case: TestCase, parsed: Mapping[str, Any], types: set[str], passed: int, proposed: int
) -> str:
    if case.route == EXTRACT_PEOPLE:
        people = parsed.get("people")
        names = {
            normalise(item["name"])
            for item in (people if isinstance(people, list) else ())
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }
        text = "People: " + _listed(names)
    else:
        text = "Signals: " + _listed(types)
        segment = parsed.get("segment")
        if case.route == SIGNAL_INBOX_PARSE and types:
            text += f" under Segment {segment if isinstance(segment, str) else NONE_TEXT}"
    return text + f"; {passed} of {proposed} quotes in their block"


def score_extraction(
    case: TestCase, result: Mapping[str, Any] | str
) -> tuple[ExtractionOutcome, CaseResult]:
    """Score one extraction case's answer; see the module docstring."""
    if not isinstance(case, TestCase) or case.test_set is not TestSet.EXTRACTION:
        raise TypeError("score_extraction takes an extraction TestCase")
    expected = case.expected
    labels = {label["signal_type"] for label in expected["signals"]}
    names = {normalise(person["name"]).casefold() for person in expected["people"]}
    if not isinstance(result, Mapping):
        if not isinstance(result, str):
            raise TypeError("result must be the parsed answer or the result's type name")
        outcome = ExtractionOutcome(
            answered=False, signals_labelled=len(labels), people_labelled=len(names)
        )
        return outcome, CaseResult(case.case_key, False, _expected_text(case), result)
    quotes = _proposed_quotes(case, result)
    passed = sum(1 for block_id, quote in quotes if _passes(case, block_id, quote))
    types = _proposed_types(case, result)
    found = len(labels & _found_types(case, result, types))
    people_found = len(names & _proposed_names(result))
    violations = sum(1 for key in set(expected["forbidden_signals"]) if key in types)
    outcome = ExtractionOutcome(
        answered=True,
        quotes_proposed=len(quotes),
        quotes_passed=passed,
        signals_labelled=len(labels),
        signals_found=found,
        people_labelled=len(names),
        people_found=people_found,
        violations=violations,
    )
    ok = (
        passed == len(quotes)
        and found == len(labels)
        and people_found == len(names)
        and violations == 0
    )
    got = _got_text(case, result, types, passed, len(quotes))
    return outcome, CaseResult(case.case_key, ok, _expected_text(case), got)
