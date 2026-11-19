"""The AI test gate's rules (FR-73, FR-71, AD-8, AD-12, NFR-26, Story 4.5). Standard library
only.

A route tuple serves only after it passed its route's AI Test Set (AD-12). This module holds what
the gate decides with: the sets, which route runs which, how big each must be, and the pass bars.

**Sets.** ``TestSet`` is ``reply``, ``grounding``, ``drafting_smoke`` or ``extraction``; its
``label`` is "Reply set", "Grounding set", "Drafting smoke set" or "Extraction fixtures". A set's
value is also a job key's discriminator (``nextron.domain.job``: ``run_ai_test_sets:<tuple
id>:<set>``, decision 1).

- ``ROUTE_SETS`` (read-only) maps a route to its set: ``classify_reply`` to ``reply``,
  ``grounding_check`` and its Opus fallback ``grounding_check_fallback`` (Story 4.14) to
  ``grounding``; ``draft_sequence``, ``draft_reply``, ``draft_channel``
  and ``call_prep`` to ``drafting_smoke``; ``extract_signals``, ``extract_people`` and
  ``signal_inbox_parse`` to ``extraction``. A route outside it has no set and is never run.
- ``BOOTSTRAP_ROUTES`` are the three extraction routes: the only routes
  ``Playbook.sync_prompts`` activates without a test run (Epic 2's bootstrap). Every other route
  serves only after a passing run.
- ``MIN_CASES`` (read-only) is each set's minimum: reply 50, grounding 30, drafting smoke 10,
  extraction 1 (decision 6). It counts the live cases of the set whose ``route`` is the tuple's
  route; fewer fail the run with ``RunError.SET_TOO_SMALL`` before any call.

**Metrics.** ``Metric(name, label, numerator, denominator, bar, passed)`` is one measure of a
run. A *share* has a ``denominator`` (an ``int``, possibly 0) and its ``bar`` is the lowest
passing percentage ("at least p%"); a *count* has ``denominator`` ``None`` and its ``bar`` is the
highest passing count ("at most n"). A share passes when ``numerator * 100 >= bar *
denominator``; with a denominator of 0 it passes only where said below. ``percent`` is the share
as a whole percentage, rounded down, so a value shown under its bar never reads as at the bar.

- ``extraction_metrics(route, outcomes)``: ``quotes``, the proposed quotes that pass the
  substring check (bar 100%; nothing proposed passes); ``recall``, the labelled Signals found,
  or the labelled people for ``extract_people`` (bar 80%); ``injections``, the forbidden Signal
  types proposed (at most 0); ``answers``, the cases whose call returned ``LlmOk`` (bar 100%).
- ``reply_metrics(outcomes)``: ``unsubscribe_recall``, the Unsubscribe cases classified as
  Unsubscribe (bar 100%); ``accuracy``, the cases classified correctly (bar 90%).
- ``grounding_metrics(outcomes)``: ``banned_patterns``, the banned-pattern cases flagged (bar
  100%); ``planted_claims``, the planted-claim cases flagged (bar 95%); ``clean_controls``, the
  clean controls *not* flagged (bar 100%).
- ``drafting_metrics(outcomes)``: ``zero_flags``, the drafts with no flag at all (bar 100%).

``recall``, ``unsubscribe_recall``, ``banned_patterns``, ``planted_claims`` and
``clean_controls`` fail when there is nothing to measure; ``quotes`` passes with nothing
proposed; every other share counts cases, so it has a denominator once the set is big enough,
and fails closed without one. The scorers that turn the Reply, Grounding and drafting routes'
answers into outcomes ship with those routes (decision 7); only extraction has one now
(``nextron.domain.ai_test_cases.score_extraction``).

**Case results.** ``CaseResult(case_key, passed, expected, got)`` is one case's verdict, as the
AI content view lists a failing one: ``expected`` and ``got`` are short texts of at most
``MAX_RESULT_TEXT`` (300) characters, cut to that length with a final "…".

**Runs.** ``RUN_AI_TEST_SETS_JOB`` is ``"run_ai_test_sets"``; its payload's fields are
``TUPLE_ID_FIELD`` (``tuple_id``) and ``TEST_SET_FIELD`` (``test_set``). ``set_of(value)`` is the
``TestSet`` whose value is ``value``, or ``None``. ``RunOutcome`` is ``passed`` or
``failed``; ``RunError`` is ``set_too_small`` or ``unknown_route`` (the gateway knows no
candidate for the tuple), and only a failed run carries one. ``run_passed(metrics)`` is whether
every metric passed (and there is at least one).

**Storage.** ``metrics_json``/``metrics_from_json`` and ``results_json``/``results_from_json``
write and read the canonical JSON the ``ai_test_runs`` row stores; a stored value that doesn't
read back raises ``ValueError``.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

# The job module imports this one (a set is a key's discriminator), and the budget module imports
# the job module through the health module, so this module imports no other domain module.
NAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")  # a metric's name
RUN_AI_TEST_SETS_JOB: Final = "run_ai_test_sets"
TUPLE_ID_FIELD: Final = "tuple_id"  # the job's payload fields
TEST_SET_FIELD: Final = "test_set"
MAX_RESULT_TEXT: Final = 300
CUT_MARK: Final = "…"
FULL_SHARE: Final = 100

EXTRACT_SIGNALS: Final = "extract_signals"
EXTRACT_PEOPLE: Final = "extract_people"
SIGNAL_INBOX_PARSE: Final = "signal_inbox_parse"


class TestSet(StrEnum):
    """An AI Test Set (AD-12)."""

    __test__ = False  # pytest: a domain value, not a test class

    REPLY = "reply"
    GROUNDING = "grounding"
    DRAFTING_SMOKE = "drafting_smoke"
    EXTRACTION = "extraction"

    @property
    def label(self) -> str:
        """The set's name on the AI content view."""
        return _SET_LABELS[self]


_SET_LABELS: Final = MappingProxyType(
    {
        TestSet.REPLY: "Reply set",
        TestSet.GROUNDING: "Grounding set",
        TestSet.DRAFTING_SMOKE: "Drafting smoke set",
        TestSet.EXTRACTION: "Extraction fixtures",
    }
)

ROUTE_SETS: Final[Mapping[str, TestSet]] = MappingProxyType(
    {
        "classify_reply": TestSet.REPLY,
        "grounding_check": TestSet.GROUNDING,
        "grounding_check_fallback": TestSet.GROUNDING,
        "draft_sequence": TestSet.DRAFTING_SMOKE,
        "draft_reply": TestSet.DRAFTING_SMOKE,
        "draft_channel": TestSet.DRAFTING_SMOKE,
        "call_prep": TestSet.DRAFTING_SMOKE,
        EXTRACT_SIGNALS: TestSet.EXTRACTION,
        EXTRACT_PEOPLE: TestSet.EXTRACTION,
        SIGNAL_INBOX_PARSE: TestSet.EXTRACTION,
    }
)

BOOTSTRAP_ROUTES: Final = frozenset({EXTRACT_SIGNALS, EXTRACT_PEOPLE, SIGNAL_INBOX_PARSE})

MIN_CASES: Final[Mapping[TestSet, int]] = MappingProxyType(
    {
        TestSet.REPLY: 50,
        TestSet.GROUNDING: 30,
        TestSet.DRAFTING_SMOKE: 10,
        TestSet.EXTRACTION: 1,
    }
)


class RunOutcome(StrEnum):
    """How a test run ended."""

    PASSED = "passed"
    FAILED = "failed"


class RunError(StrEnum):
    """Why a run failed without being scored."""

    SET_TOO_SMALL = "set_too_small"
    UNKNOWN_ROUTE = "unknown_route"

    @property
    def text(self) -> str:
        """The error as the AI content view says it."""
        return _ERROR_TEXTS[self]


_ERROR_TEXTS: Final = MappingProxyType(
    {
        RunError.SET_TOO_SMALL: "set too small",
        RunError.UNKNOWN_ROUTE: "the AI gateway knows no route candidate for this tuple",
    }
)


def set_of(value: object) -> TestSet | None:
    """The ``TestSet`` whose value is ``value``, or ``None``."""
    return next((found for found in TestSet if found.value == value), None)


# Metrics ----------------------------------------------------------------------------------------


def _count(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


@dataclass(frozen=True, slots=True)
class Metric:
    """One measure of a run against its bar; see the module docstring."""

    name: str
    label: str
    numerator: int
    denominator: int | None
    bar: int
    passed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError(f"name must match {NAME_PATTERN.pattern}")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label must be a non-blank str")
        _count("numerator", self.numerator)
        bar = _count("bar", self.bar)
        if self.denominator is not None:
            if self.numerator > _count("denominator", self.denominator):
                raise ValueError("numerator must be at most denominator")
            if bar > FULL_SHARE:
                raise ValueError("a share's bar must be at most 100")
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be a bool")

    @property
    def is_share(self) -> bool:
        """Whether the metric is a share (it has a denominator) rather than a count."""
        return self.denominator is not None

    @property
    def percent(self) -> int | None:
        """The share as a whole percentage, rounded down; ``None`` for a count or a share of
        nothing."""
        if not self.denominator:
            return None
        return self.numerator * FULL_SHARE // self.denominator


def share(
    name: str, label: str, numerator: int, denominator: int, bar: int, *, empty_passes: bool
) -> Metric:
    """A share against "at least ``bar``%"; with nothing to measure it passes only when
    ``empty_passes``."""
    if denominator == 0:
        passed = empty_passes
    else:
        passed = numerator * FULL_SHARE >= bar * denominator
    return Metric(name, label, numerator, denominator, bar, passed)


def count(name: str, label: str, value: int, maximum: int) -> Metric:
    """A count against "at most ``maximum``"."""
    return Metric(name, label, value, None, maximum, value <= maximum)


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """What one extraction case's answer gave (``nextron.domain.ai_test_cases``)."""

    answered: bool
    quotes_proposed: int = 0
    quotes_passed: int = 0
    signals_labelled: int = 0
    signals_found: int = 0
    people_labelled: int = 0
    people_found: int = 0
    violations: int = 0


def extraction_metrics(route: str, outcomes: Sequence[ExtractionOutcome]) -> tuple[Metric, ...]:
    """The extraction bars over ``outcomes``; recall is over labelled people for
    ``extract_people``."""
    chosen = tuple(outcomes)
    if route == EXTRACT_PEOPLE:
        recall = share(
            "recall",
            "Labelled people found",
            sum(outcome.people_found for outcome in chosen),
            sum(outcome.people_labelled for outcome in chosen),
            80,
            empty_passes=False,
        )
    else:
        recall = share(
            "recall",
            "Labelled Signals found",
            sum(outcome.signals_found for outcome in chosen),
            sum(outcome.signals_labelled for outcome in chosen),
            80,
            empty_passes=False,
        )
    return (
        share(
            "quotes",
            "Quotes that pass the substring check",
            sum(outcome.quotes_passed for outcome in chosen),
            sum(outcome.quotes_proposed for outcome in chosen),
            FULL_SHARE,
            empty_passes=True,
        ),
        recall,
        count(
            "injections",
            "Forbidden Signal types proposed",
            sum(outcome.violations for outcome in chosen),
            0,
        ),
        share(
            "answers",
            "Cases answered",
            sum(1 for outcome in chosen if outcome.answered),
            len(chosen),
            FULL_SHARE,
            empty_passes=False,
        ),
    )


@dataclass(frozen=True, slots=True)
class ReplyOutcome:
    """One Reply case: whether it is an Unsubscribe, whether the answer said Unsubscribe, and
    whether the answer's class was right."""

    expected_unsubscribe: bool
    got_unsubscribe: bool
    correct: bool


def reply_metrics(outcomes: Sequence[ReplyOutcome]) -> tuple[Metric, ...]:
    """The Reply set's bars: 100% Unsubscribe recall and at least 90% overall."""
    chosen = tuple(outcomes)
    unsubscribes = [outcome for outcome in chosen if outcome.expected_unsubscribe]
    return (
        share(
            "unsubscribe_recall",
            "Unsubscribe replies recognised",
            sum(1 for outcome in unsubscribes if outcome.got_unsubscribe),
            len(unsubscribes),
            FULL_SHARE,
            empty_passes=False,
        ),
        share(
            "accuracy",
            "Replies classified correctly",
            sum(1 for outcome in chosen if outcome.correct),
            len(chosen),
            90,
            empty_passes=False,
        ),
    )


class GroundingCase(StrEnum):
    """What a Grounding case plants."""

    BANNED_PATTERN = "banned_pattern"
    PLANTED_CLAIM = "planted_claim"
    CLEAN_CONTROL = "clean_control"


@dataclass(frozen=True, slots=True)
class GroundingOutcome:
    """One Grounding case: what it plants and whether the check flagged it."""

    kind: GroundingCase
    flagged: bool


def grounding_metrics(outcomes: Sequence[GroundingOutcome]) -> tuple[Metric, ...]:
    """The Grounding set's bars: 100% of banned patterns, at least 95% of planted claims, and
    no clean control flagged (decision 6)."""
    chosen = tuple(outcomes)

    def of(kind: GroundingCase) -> list[GroundingOutcome]:
        return [outcome for outcome in chosen if outcome.kind is kind]

    banned, planted, clean = (
        of(GroundingCase.BANNED_PATTERN),
        of(GroundingCase.PLANTED_CLAIM),
        of(GroundingCase.CLEAN_CONTROL),
    )
    return (
        share(
            "banned_patterns",
            "Banned patterns flagged",
            sum(1 for outcome in banned if outcome.flagged),
            len(banned),
            FULL_SHARE,
            empty_passes=False,
        ),
        share(
            "planted_claims",
            "Planted claims flagged",
            sum(1 for outcome in planted if outcome.flagged),
            len(planted),
            95,
            empty_passes=False,
        ),
        share(
            "clean_controls",
            "Clean controls not flagged",
            sum(1 for outcome in clean if not outcome.flagged),
            len(clean),
            FULL_SHARE,
            empty_passes=False,
        ),
    )


@dataclass(frozen=True, slots=True)
class DraftingOutcome:
    """One drafting smoke case: how many flags the deterministic rules and the active
    ``grounding_check`` raised on its output."""

    flags: int


def drafting_metrics(outcomes: Sequence[DraftingOutcome]) -> tuple[Metric, ...]:
    """The drafting smoke set's bar: every output with zero flags."""
    chosen = tuple(outcomes)
    return (
        share(
            "zero_flags",
            "Drafts with zero flags",
            sum(1 for outcome in chosen if outcome.flags == 0),
            len(chosen),
            FULL_SHARE,
            empty_passes=False,
        ),
    )


def run_passed(metrics: Iterable[Metric]) -> bool:
    """Whether a run with ``metrics`` passed: there is at least one and every one passed."""
    chosen = tuple(metrics)
    return bool(chosen) and all(metric.passed for metric in chosen)


# Case results -----------------------------------------------------------------------------------


def cut(text: str, limit: int = MAX_RESULT_TEXT) -> str:
    """``text``, or its first ``limit`` - 1 characters and "…" when it is longer than
    ``limit``."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return text if len(text) <= limit else text[: limit - len(CUT_MARK)] + CUT_MARK


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One case's verdict; ``expected`` and ``got`` are cut to ``MAX_RESULT_TEXT``
    characters."""

    case_key: str
    passed: bool
    expected: str
    got: str

    def __post_init__(self) -> None:
        if not isinstance(self.case_key, str) or not self.case_key:
            raise ValueError("case_key must be a non-empty str")
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be a bool")
        object.__setattr__(self, "expected", cut(self.expected))
        object.__setattr__(self, "got", cut(self.got))


# Storage ----------------------------------------------------------------------------------------


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def metrics_json(metrics: Iterable[Metric]) -> str:
    """The canonical JSON a run stores for ``metrics``, in order."""
    return _canonical(
        [
            {
                "name": metric.name,
                "label": metric.label,
                "numerator": metric.numerator,
                "denominator": metric.denominator,
                "bar": metric.bar,
                "passed": metric.passed,
            }
            for metric in metrics
        ]
    )


def _items(text: str, fields: frozenset[str]) -> list[Mapping[str, Any]]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError("a stored test run value is not JSON") from None
    if not isinstance(data, list) or not all(
        isinstance(item, dict) and set(item) == fields for item in data
    ):
        raise ValueError("a stored test run value is not a list of the expected objects")
    return data


_METRIC_FIELDS: Final = frozenset({"name", "label", "numerator", "denominator", "bar", "passed"})
_RESULT_FIELDS: Final = frozenset({"case_key", "passed", "expected", "got"})


def metrics_from_json(text: str) -> tuple[Metric, ...]:
    """The metrics ``metrics_json`` wrote; ``ValueError`` (or ``TypeError``) when they don't
    read back."""
    return tuple(
        Metric(
            item["name"],
            item["label"],
            item["numerator"],
            item["denominator"],
            item["bar"],
            item["passed"],
        )
        for item in _items(text, _METRIC_FIELDS)
    )


def results_json(results: Iterable[CaseResult]) -> str:
    """The canonical JSON a run stores for its case results, in order."""
    return _canonical(
        [
            {
                "case_key": result.case_key,
                "passed": result.passed,
                "expected": result.expected,
                "got": result.got,
            }
            for result in results
        ]
    )


def results_from_json(text: str) -> tuple[CaseResult, ...]:
    """The case results ``results_json`` wrote; ``ValueError`` (or ``TypeError``) when they
    don't read back."""
    found = []
    for item in _items(text, _RESULT_FIELDS):
        if not isinstance(item["expected"], str) or not isinstance(item["got"], str):
            raise ValueError("a stored case result's texts must be strings")
        found.append(CaseResult(item["case_key"], item["passed"], item["expected"], item["got"]))
    return tuple(found)
