"""``nextron.domain.ai_test_gate`` (FR-73, AD-12, Story 4.5): the sets, the routes' sets, the
minimums, every bar of every metric (a denominator of 0 included), the case results' cut texts,
and the stored JSON."""

import json

import pytest

from nextron.domain.ai_test_gate import (
    BOOTSTRAP_ROUTES,
    MAX_RESULT_TEXT,
    MIN_CASES,
    ROUTE_SETS,
    RUN_AI_TEST_SETS_JOB,
    CaseResult,
    DraftingOutcome,
    ExtractionOutcome,
    GroundingCase,
    GroundingOutcome,
    Metric,
    ReplyOutcome,
    RunError,
    RunOutcome,
    TestSet,
    count,
    cut,
    drafting_metrics,
    extraction_metrics,
    grounding_metrics,
    metrics_from_json,
    metrics_json,
    reply_metrics,
    results_from_json,
    results_json,
    run_passed,
    set_of,
    share,
)


def by_name(metrics: tuple[Metric, ...]) -> dict[str, tuple[int, int | None, int, bool]]:
    return {m.name: (m.numerator, m.denominator, m.bar, m.passed) for m in metrics}


# The sets ----------------------------------------------------------------------------------------


def test_the_sets_their_labels_and_the_job_name() -> None:
    assert [(s.value, s.label) for s in TestSet] == [
        ("reply", "Reply set"),
        ("grounding", "Grounding set"),
        ("drafting_smoke", "Drafting smoke set"),
        ("extraction", "Extraction fixtures"),
    ]
    assert RUN_AI_TEST_SETS_JOB == "run_ai_test_sets"
    assert [(e.value, e.text) for e in RunError] == [
        ("set_too_small", "set too small"),
        ("unknown_route", "the AI gateway knows no route candidate for this tuple"),
    ]
    assert [o.value for o in RunOutcome] == ["passed", "failed"]
    assert set_of("extraction") is TestSet.EXTRACTION
    assert set_of("Extraction") is None and set_of(None) is None


def test_each_route_has_its_set_and_the_bootstrap_routes_are_the_extraction_ones() -> None:
    assert dict(ROUTE_SETS) == {
        "classify_reply": TestSet.REPLY,
        "grounding_check": TestSet.GROUNDING,
        "grounding_check_fallback": TestSet.GROUNDING,  # Story 4.14
        "draft_sequence": TestSet.DRAFTING_SMOKE,
        "draft_reply": TestSet.DRAFTING_SMOKE,
        "draft_channel": TestSet.DRAFTING_SMOKE,
        "call_prep": TestSet.DRAFTING_SMOKE,
        "extract_signals": TestSet.EXTRACTION,
        "extract_people": TestSet.EXTRACTION,
        "signal_inbox_parse": TestSet.EXTRACTION,
    }
    assert BOOTSTRAP_ROUTES == {"extract_signals", "extract_people", "signal_inbox_parse"}
    assert dict(MIN_CASES) == {
        TestSet.REPLY: 50,
        TestSet.GROUNDING: 30,
        TestSet.DRAFTING_SMOKE: 10,
        TestSet.EXTRACTION: 1,
    }
    with pytest.raises(TypeError):
        ROUTE_SETS["x"] = TestSet.REPLY  # type: ignore[index]


# Shares and counts --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numerator", "denominator", "bar", "passed"),
    [
        (4, 5, 80, True),  # exactly at the bar
        (79, 100, 80, False),  # just under it
        (19, 20, 95, True),
        (18, 20, 95, False),
        (10, 10, 100, True),
        (9, 10, 100, False),
    ],
)
def test_a_share_passes_at_or_above_its_bar(
    numerator: int, denominator: int, bar: int, passed: bool
) -> None:
    metric = share("m", "Measure", numerator, denominator, bar, empty_passes=False)

    assert metric.passed is passed
    assert metric.is_share
    assert metric.percent == numerator * 100 // denominator


@pytest.mark.parametrize("empty_passes", [True, False])
def test_a_share_of_nothing_passes_only_when_said(empty_passes: bool) -> None:
    metric = share("m", "Measure", 0, 0, 100, empty_passes=empty_passes)

    assert (metric.passed, metric.percent) == (empty_passes, None)


def test_a_count_passes_at_or_below_its_bar() -> None:
    assert count("c", "Count", 0, 0).passed
    assert not count("c", "Count", 1, 0).passed
    assert count("c", "Count", 2, 2).passed
    assert (count("c", "Count", 1, 0).is_share, count("c", "Count", 1, 0).percent) == (False, None)


def test_a_rounded_down_percent_never_reads_as_at_the_bar() -> None:
    assert share("m", "M", 799, 1000, 80, empty_passes=False).percent == 79


@pytest.mark.parametrize(
    "fields",
    [
        ("Bad", "L", 1, 1, 100, True),
        ("m", " ", 1, 1, 100, True),
        ("m", "L", -1, 1, 100, True),
        ("m", "L", 2, 1, 100, True),
        ("m", "L", 1, 1, 101, True),
        ("m", "L", True, 1, 100, True),
        ("m", "L", 1, 1, 100, "yes"),
    ],
)
def test_a_metric_that_breaks_a_rule_is_refused(fields: tuple[object, ...]) -> None:
    with pytest.raises((TypeError, ValueError)):
        Metric(*fields)  # type: ignore[arg-type]


# The extraction bars ------------------------------------------------------------------------------


def outcome(**fields: object) -> ExtractionOutcome:
    values: dict[str, object] = {
        "answered": True,
        "quotes_proposed": 2,
        "quotes_passed": 2,
        "signals_labelled": 1,
        "signals_found": 1,
    }
    values.update(fields)
    return ExtractionOutcome(**values)  # type: ignore[arg-type]


def test_the_extraction_bars_pass_on_labelled_answers() -> None:
    metrics = extraction_metrics("extract_signals", [outcome(), outcome()])

    assert [m.name for m in metrics] == ["quotes", "recall", "injections", "answers"]
    assert by_name(metrics) == {
        "quotes": (4, 4, 100, True),
        "recall": (2, 2, 80, True),
        "injections": (0, None, 0, True),
        "answers": (2, 2, 100, True),
    }
    assert run_passed(metrics)
    assert [m.label for m in metrics] == [
        "Quotes that pass the substring check",
        "Labelled Signals found",
        "Forbidden Signal types proposed",
        "Cases answered",
    ]


@pytest.mark.parametrize(
    ("outcomes", "failed"),
    [
        ([outcome(quotes_passed=1)], "quotes"),
        ([outcome(signals_labelled=5, signals_found=3)], "recall"),
        ([outcome(signals_labelled=0, signals_found=0)], "recall"),  # nothing labelled fails
        ([outcome(violations=1)], "injections"),
        ([outcome(), outcome(answered=False, quotes_proposed=0, quotes_passed=0)], "answers"),
        ([], "recall"),
    ],
)
def test_each_extraction_bar_fails_closed(outcomes: list[ExtractionOutcome], failed: str) -> None:
    metrics = by_name(extraction_metrics("extract_signals", outcomes))

    assert metrics[failed][3] is False
    assert not run_passed(extraction_metrics("extract_signals", outcomes))


def test_nothing_proposed_passes_the_quote_bar() -> None:
    nothing = outcome(quotes_proposed=0, quotes_passed=0)

    metrics = by_name(extraction_metrics("extract_signals", [nothing]))

    assert metrics["quotes"] == (0, 0, 100, True)


def test_extract_people_recall_is_over_labelled_people() -> None:
    found = outcome(signals_labelled=0, signals_found=0, people_labelled=4, people_found=4)

    metrics = extraction_metrics("extract_people", [found])

    assert by_name(metrics)["recall"] == (4, 4, 80, True)
    assert metrics[1].label == "Labelled people found"
    assert not by_name(extraction_metrics("extract_signals", [found]))["recall"][3]


# The other sets' bars -----------------------------------------------------------------------------


def test_the_reply_bars() -> None:
    unsubscribe = ReplyOutcome(expected_unsubscribe=True, got_unsubscribe=True, correct=True)
    other = ReplyOutcome(expected_unsubscribe=False, got_unsubscribe=False, correct=True)
    wrong = ReplyOutcome(expected_unsubscribe=False, got_unsubscribe=False, correct=False)
    missed = ReplyOutcome(expected_unsubscribe=True, got_unsubscribe=False, correct=False)

    assert by_name(reply_metrics([unsubscribe] + [other] * 8 + [wrong])) == {
        "unsubscribe_recall": (1, 1, 100, True),
        "accuracy": (9, 10, 90, True),
    }
    assert not by_name(reply_metrics([unsubscribe] * 9 + [missed]))["unsubscribe_recall"][3]
    assert not by_name(reply_metrics([unsubscribe] * 8 + [wrong] * 2))["accuracy"][3]
    assert not by_name(reply_metrics([other] * 10))["unsubscribe_recall"][3]  # nothing to measure
    assert not run_passed(reply_metrics([]))


def test_the_grounding_bars() -> None:
    banned = GroundingOutcome(GroundingCase.BANNED_PATTERN, flagged=True)
    planted = GroundingOutcome(GroundingCase.PLANTED_CLAIM, flagged=True)
    unflagged = GroundingOutcome(GroundingCase.PLANTED_CLAIM, flagged=False)
    clean = GroundingOutcome(GroundingCase.CLEAN_CONTROL, flagged=False)
    flagged_clean = GroundingOutcome(GroundingCase.CLEAN_CONTROL, flagged=True)

    passing = [banned] * 5 + [planted] * 19 + [unflagged] + [clean] * 5
    assert by_name(grounding_metrics(passing)) == {
        "banned_patterns": (5, 5, 100, True),
        "planted_claims": (19, 20, 95, True),
        "clean_controls": (5, 5, 100, True),
    }
    missed = [GroundingOutcome(GroundingCase.BANNED_PATTERN, flagged=False), *passing]
    assert not by_name(grounding_metrics(missed))["banned_patterns"][3]
    assert not by_name(grounding_metrics([*passing, unflagged]))["planted_claims"][3]
    assert not by_name(grounding_metrics([*passing, flagged_clean]))["clean_controls"][3]
    for kind in GroundingCase:  # each kind with nothing to measure fails
        without = [found for found in passing if found.kind is not kind]
        assert not run_passed(grounding_metrics(without))


def test_the_drafting_smoke_bar() -> None:
    assert by_name(drafting_metrics([DraftingOutcome(0)] * 10)) == {
        "zero_flags": (10, 10, 100, True)
    }
    assert not run_passed(drafting_metrics([DraftingOutcome(0)] * 9 + [DraftingOutcome(1)]))
    assert not run_passed(drafting_metrics([]))


def test_a_run_with_no_metric_never_passes() -> None:
    assert not run_passed(())


# Case results -------------------------------------------------------------------------------------


def test_expected_and_got_are_cut_to_300_characters() -> None:
    result = CaseResult("case_one", False, "e" * 400, "g" * 300)

    assert len(result.expected) == MAX_RESULT_TEXT == 300
    assert result.expected == "e" * 299 + "…"
    assert result.got == "g" * 300  # at the limit: kept whole
    assert cut("short") == "short"
    with pytest.raises(TypeError):
        CaseResult("case_one", "no", "e", "g")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CaseResult("", True, "e", "g")


# Stored JSON --------------------------------------------------------------------------------------


def test_metrics_and_results_read_back_from_their_json() -> None:
    metrics = extraction_metrics("extract_people", [outcome(people_labelled=2, people_found=1)])
    results = (CaseResult("a", True, "x", "y"), CaseResult("b", False, "é", "z" * 400))

    assert metrics_from_json(metrics_json(metrics)) == metrics
    assert results_from_json(results_json(results)) == results
    assert json.loads(metrics_json(metrics))[0]["name"] == "quotes"
    assert metrics_json(()) == results_json(()) == "[]"


@pytest.mark.parametrize("text", ["not json", "{}", '[{"name": "x"}]', "[1]"])
def test_a_stored_value_that_doesnt_read_back_is_refused(text: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        metrics_from_json(text)
    with pytest.raises((TypeError, ValueError)):
        results_from_json(text)
