"""``nextron.domain.ai_test_cases`` (FR-73, AD-12, AD-18, Story 4.5): the ``TestCase`` value,
reading Story 2.8's extraction fixtures (every shipped file, and each refusal naming the file
and the field, never the text), the call inputs the harness builds, and the scoring of every
fixture shape: signals, people and inbox items, each injection, and a result that isn't an
answer. The harness (``tests/ai_eval/extraction.py``) is the reference the port must match."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from nextron.domain.ai import Urgency
from nextron.domain.ai_test_cases import (
    InvalidTestCases,
    TestCase,
    check_segments,
    extraction_case,
    extraction_inputs,
    score_extraction,
    stored_case,
)
from nextron.domain.ai_test_gate import ExtractionOutcome, TestSet, extraction_metrics
from tests.ai_eval.extraction import CASES_DIR, case_blocks, load_case, shipped_segments

FILES = sorted(CASES_DIR.glob("*.json"))
INVENTED = "We are opening three new offices next year."


def raw(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CASES_DIR / name).read_text(encoding="utf-8"))
    return data


def case_of(name: str) -> TestCase:
    return extraction_case(name, raw(name))


def labelled(case: TestCase) -> dict[str, Any]:
    expected = case.expected
    if case.route == "extract_people":
        return {"people": copy.deepcopy(expected["people"])}
    if case.route == "extract_signals":
        return {"signals": copy.deepcopy(expected["signals"])}
    [signal] = expected["signals"]
    return {
        "signal_type": signal["signal_type"],
        "segment": case.input["segment"],
        "quote": signal["quote"],
    }


# The value ----------------------------------------------------------------------------------------


def reply(data: dict[str, Any], covers: tuple[str, ...]) -> TestCase:
    return TestCase("reply_01", TestSet.REPLY, "classify_reply", data, {"class": "other"}, covers)


def test_a_case_keeps_a_copy_of_its_json_and_hashes_every_field() -> None:
    data = {"text": "Reply."}
    case = reply(data, ("a",))
    data["text"] = "changed"

    assert case.input == {"text": "Reply."}
    again = reply({"text": "Reply."}, ("a",))
    assert case == again and case.sha256 == again.sha256
    assert len(case.sha256) == 64
    other = reply({"text": "Reply."}, ("b",))
    assert other.sha256 != case.sha256
    assert "Reply." not in repr(case)
    with pytest.raises(TypeError):
        hash(case)


@pytest.mark.parametrize(
    "fields",
    [
        ("Bad key", TestSet.REPLY, "classify_reply", {}, {}, ()),
        ("k", "reply", "classify_reply", {}, {}, ()),
        ("k", TestSet.REPLY, "extract_people", {}, {}, ()),  # not a route of the set
        ("k", TestSet.REPLY, "classify_reply", [], {}, ()),
        ("k", TestSet.REPLY, "classify_reply", {}, {"x": float("nan")}, ()),
        ("k", TestSet.REPLY, "classify_reply", {}, {}, ["a"]),
        ("k", TestSet.REPLY, "classify_reply", {}, {}, ("Not a token",)),
    ],
)
def test_a_case_that_breaks_a_rule_is_refused(fields: tuple[object, ...]) -> None:
    with pytest.raises((TypeError, ValueError)):
        TestCase(*fields)  # type: ignore[arg-type]


def test_a_stored_case_reads_back_and_a_broken_one_is_refused() -> None:
    case = case_of(FILES[0].name)
    stored = stored_case(
        case.case_key,
        "extraction",
        case.route,
        json.dumps(case.input),
        json.dumps(case.expected),
        json.dumps(list(case.covers)),
    )

    assert stored == case and stored.sha256 == case.sha256
    broken = f"stored test case {case.case_key}: doesn't read back"
    with pytest.raises(InvalidTestCases, match=broken):
        stored_case(case.case_key, "extraction", case.route, "{", "{}", "[]")
    with pytest.raises(InvalidTestCases):
        stored_case(case.case_key, "other", case.route, "{}", "{}", "[]")


# Reading the fixtures -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", FILES, ids=[path.stem for path in FILES])
def test_every_shipped_fixture_reads_as_the_harness_reads_it(path: Path) -> None:
    case = extraction_case(path.name, raw(path.name))
    harness = load_case(path)

    assert (case.case_key, case.test_set, case.route) == (
        harness.case,
        TestSet.EXTRACTION,
        harness.route,
    )
    assert case.input["segment"] == harness.segment
    assert [tuple(block.values()) for block in case.input["blocks"]] == [
        (block.id, block.source, block.text) for block in harness.blocks
    ]
    assert case.expected == raw(path.name)["expect"]
    assert case.covers == (harness.segment, harness.injection or "clean")


def test_the_fixtures_cover_each_route_and_injection() -> None:
    cases = [case_of(path.name) for path in FILES]

    routes = {case.route for case in cases}
    assert routes == {"extract_signals", "extract_people", "signal_inbox_parse"}
    covered = {token for case in cases for token in case.covers}
    assert {"hidden_instructions", "invented_quote", "dictated_tier", "clean"} <= covered


NAME = "law_signals_hiring_and_expansion.json"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda d: d.pop("segment"), "the case must be an object with exactly"),
        (lambda d: d.update(extra=1), "the case must be an object with exactly"),
        (lambda d: d["expect"].pop("people"), "expect must be an object with exactly"),
        (lambda d: d.update(case="other_name"), "case must be the file's stem"),
        (lambda d: d.update(route="classify_reply"), "route must be an extraction route"),
        (lambda d: d.update(route=""), "route must be a non-empty string"),
        (lambda d: d.update(segment="Law Private"), "segment must match"),
        (lambda d: d.update(injection="other"), "injection must be null or one of"),
        (lambda d: d.update(blocks="text"), "blocks must be a list"),
        (lambda d: d["blocks"][0].update(text=""), "blocks[0].text must be a non-empty string"),
        (lambda d: d["blocks"][1].pop("source"), "blocks[1] must be an object with exactly"),
        (lambda d: d["expect"]["signals"][0].update(quote=7), "expect.signals[0].quote must be"),
        (
            lambda d: d["expect"].update(forbidden_signals=[1]),
            "forbidden_signals must hold strings",
        ),
        (lambda d: d["expect"].update(people={}), "expect.people must be a list"),
    ],
)
def test_a_bad_fixture_names_the_file_and_the_field_never_the_text(
    change: Any, reason: str
) -> None:
    data = raw(NAME)
    texts = [block["text"] for block in data["blocks"]]
    change(data)

    with pytest.raises(InvalidTestCases) as raised:
        extraction_case(NAME, data)

    assert raised.value.code == "invalid_test_cases"
    assert raised.value.message.startswith(f"{NAME}: ")
    assert reason in raised.value.message
    assert not any(text in raised.value.message for text in texts)


def test_a_fixture_that_isnt_an_object_is_refused() -> None:
    with pytest.raises(InvalidTestCases, match="the case must be an object"):
        extraction_case(NAME, [])


# The inputs ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", FILES, ids=[path.stem for path in FILES])
def test_the_inputs_are_the_harness_inputs(path: Path) -> None:
    segments = shipped_segments()
    case = case_of(path.name)

    blocks, urgency = extraction_inputs(case, 3, segments)

    assert (blocks, urgency) == case_blocks(load_case(path), 3, segments)
    assert urgency is (Urgency.SOON if case.route == "signal_inbox_parse" else Urgency.BULK)


def test_a_case_whose_segment_isnt_stored_is_refused_before_any_call() -> None:
    cases = [case_of(path.name) for path in FILES]
    segments = shipped_segments()

    check_segments(cases, segments)  # every shipped Segment is there
    law_only = [segment for segment in segments if segment.key == "law_private_ai"]
    with pytest.raises(InvalidTestCases, match="Segment isn't stored; run nextron seed sync"):
        check_segments(cases, law_only)


# Scoring ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", FILES, ids=[path.stem for path in FILES])
def test_a_labelled_answer_passes_every_case(path: Path) -> None:
    case = case_of(path.name)

    found, result = score_extraction(case, labelled(case))

    assert result.passed, result
    assert found.answered and found.violations == 0
    assert found.quotes_passed == found.quotes_proposed > 0
    assert found.signals_found == found.signals_labelled
    assert found.people_found == found.people_labelled
    assert result.case_key == case.case_key


def test_an_invented_quote_fails_its_case() -> None:
    case = case_of(NAME)
    answer = labelled(case)
    answer["signals"][0]["quote"] = INVENTED

    found, result = score_extraction(case, answer)

    assert (found.quotes_proposed, found.quotes_passed) == (2, 1)
    assert not result.passed
    labels = "Signals: law_expansion, law_hiring_legal_ops"
    assert result.expected == f"{labels}; every quote in its block"
    assert result.got == f"{labels}; 1 of 2 quotes in their block"


def test_a_quote_citing_the_signal_types_block_or_an_unknown_block_fails() -> None:
    case = case_of(NAME)
    for block_id in ("signal_types", "nowhere"):
        answer = labelled(case)
        answer["signals"][0]["block_id"] = block_id

        found, result = score_extraction(case, answer)

        assert found.quotes_passed == 1 and not result.passed


def other_lists(size_quote: str = "14 lawyers in Leeds") -> dict[str, Any]:
    """An answer with no Signal and one quote in each other quoted list (the harness's
    ``test_every_quoted_list_counts``)."""
    quoted = {"block_id": "p1", "quote": "14 lawyers in Leeds"}
    return {
        "signals": [],
        "profile_facts": [{"kind": "location", "value": "Leeds", **quoted}],
        "size_clues": [
            {"kind": "lawyers_firm_wide", "number": 14, "block_id": "p1", "quote": size_quote}
        ],
        "job_postings": [
            {
                "title": "Legal Operations Manager",
                "url": None,
                "posted_date": None,
                "block_id": "p2",
                "quote": "Legal Operations Manager",
            }
        ],
    }


def test_every_quoted_list_counts() -> None:
    case = case_of(NAME)

    found, _ = score_extraction(case, other_lists())

    assert (found.quotes_proposed, found.quotes_passed) == (3, 3)
    assert found.signals_found == 0


def test_an_invented_size_clue_quote_fails_the_case_and_the_quote_bar() -> None:
    case = case_of(NAME)
    answer = labelled(case)
    answer.update({name: value for name, value in other_lists(INVENTED).items() if name != "signals"})

    found, result = score_extraction(case, answer)

    assert (found.quotes_proposed, found.quotes_passed) == (5, 4)
    assert not result.passed
    metrics = {m.name: m.passed for m in extraction_metrics(case.route, [found])}
    assert metrics == {"quotes": False, "recall": True, "injections": True, "answers": True}


def test_a_missed_label_fails_its_case() -> None:
    case = case_of(NAME)
    answer = labelled(case)
    answer["signals"] = answer["signals"][:1]

    found, result = score_extraction(case, answer)

    assert (found.signals_labelled, found.signals_found) == (2, 1)
    assert not result.passed


@pytest.mark.parametrize(
    ("name", "forbidden"),
    [
        ("law_signals_hidden_instructions.json", "law_expansion"),
        ("law_signals_invented_quote.json", "law_expansion"),
        ("law_inbox_dictated_tier.json", "law_hiring_legal_ops"),
    ],
)
def test_a_forbidden_type_fails_its_case(name: str, forbidden: str) -> None:
    case = case_of(name)
    answer = labelled(case)
    if case.route == "signal_inbox_parse":
        answer["signal_type"] = forbidden
        answer["segment"] = "ops_automation"  # a forbidden type counts whatever the Segment
    else:
        answer["signals"].append({"signal_type": forbidden, "block_id": "x", "quote": None})

    found, result = score_extraction(case, answer)

    assert found.violations == 1
    assert not result.passed


def test_an_inbox_type_under_another_segment_isnt_found() -> None:
    case = case_of("law_inbox_confidentiality_post.json")
    answer = labelled(case)
    answer["segment"] = "ops_automation"

    found, result = score_extraction(case, answer)

    assert (found.signals_labelled, found.signals_found) == (1, 0)
    assert not result.passed
    assert result.got.startswith(
        "Signals: law_ai_confidentiality_post under Segment ops_automation"
    )


def test_an_inbox_answer_without_a_quote_proposes_none() -> None:
    case = case_of("ops_inbox_job_ad.json")
    answer = labelled(case)
    answer["quote"] = None

    found, _ = score_extraction(case, answer)

    assert (found.quotes_proposed, found.quotes_passed) == (0, 0)


def test_people_are_found_by_name_normalised_and_case_folded() -> None:
    case = case_of("law_people_team_pages.json")
    answer = labelled(case)
    for person in answer["people"]:
        person["name"] = "  " + person["name"].upper() + " "

    found, result = score_extraction(case, answer)

    assert found.people_found == found.people_labelled == 4
    assert result.passed
    assert result.expected.startswith("People: Daniel Okafor, Margaret Ellison")


@pytest.mark.parametrize("result_type", ["LlmRefused", "LlmTruncated", "LlmInvalid", "LlmFailed"])
def test_a_result_that_isnt_an_answer_fails_with_its_type(result_type: str) -> None:
    case = case_of("ops_people_leadership_page.json")

    found, result = score_extraction(case, result_type)

    assert found == ExtractionOutcome(answered=False, people_labelled=3)
    assert (result.passed, result.got) == (False, result_type)


def test_scoring_refuses_a_case_of_another_set() -> None:
    case = TestCase("reply_01", TestSet.REPLY, "classify_reply", {}, {}, ())

    with pytest.raises(TypeError):
        score_extraction(case, {})
    with pytest.raises(TypeError):
        score_extraction(case_of(NAME), 7)  # type: ignore[arg-type]
