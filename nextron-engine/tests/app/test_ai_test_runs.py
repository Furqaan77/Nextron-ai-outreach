"""Story 4.5's AI test gate on a temporary database at head (FR-73, FR-71, AD-8, AD-12, NFR-26):
``Playbook.sync_test_cases`` and ``activate``, ``AiTestRuns.queue_runs`` and ``run`` with
``ScriptedLlm``, ``FrozenClock``, an in-memory meter and the real job types; the
``run_ai_test_sets`` handler; and ``queries.playbook``'s runs and ``health_issues``.

Every row of the spec's matrix that the service owns is here: the first start, the restart, a
bootstrap tuple that passes and one that fails, the newest candidate that passes, a set too
small, the budget, the API down, no key, activating an untested tuple, a route without a
candidate and an edited fixture; plus decision 5 (an older tuple that passes is only a rollback
target), the unknown route, a Segment that isn't stored, a set without a scorer and the skips.
The view's rows (ungated marks, metrics, failing cases, rollback) are in
``tests/app/test_ai_content_page.py``; the startup's are in ``tests/unit/test_serve.py``.
"""

import dataclasses
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.adapters.ai import LLM_UNKNOWN_ROUTE, routes
from nextron.app.jobqueue.api import JobQueue
from nextron.app.playbook.api import (
    SCORERS,
    UNKNOWN_ROUTE_CODE,
    ActivationRefused,
    ActivationTrigger,
    AiTestRuns,
    Playbook,
    TestCaseSync,
)
from nextron.app.queries import playbook as playbook_queries
from nextron.app.setup.api import Setup
from nextron.db.repos.playbook import test_runs as runs_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.ai import Effort, RouteSpec, Urgency
from nextron.domain.ai_test_cases import InvalidTestCases, TestCase, case_set_sha256
from nextron.domain.ai_test_gate import ROUTE_SETS, RunError, RunOutcome, TestSet
from nextron.domain.budget import AiCall
from nextron.domain.job import DEFERRED, Job, JobStatus, Lane
from nextron.domain.playbook import PlaybookSnapshot
from nextron.domain.prompts import PromptFile
from nextron.domain.segments import validate_segments
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmInvalid, LlmOk, LlmResult, LlmUsage
from tests.fakes.clock import FrozenClock
from tests.fakes.llm import ScriptedLlm
from tests.fakes.usage_meter import InMemoryUsageMeter

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
STAMP = "2026-11-18T09:00:00.000000Z"
HAIKU = "claude-haiku-5-5"
SEGMENTS_FILE = main.SEED_DIR / main.SEGMENTS_FILE_NAME
PLAYBOOK = PlaybookSnapshot("1.0.0", "---\nversion: 1.0.0\n---\n\nA test Sales Playbook.\n")
SIGNALS, PEOPLE, INBOX = "extract_signals", "extract_people", "signal_inbox_parse"
ROUTES = (SIGNALS, PEOPLE, INBOX)
CASE_TEXT = "client confidentiality is not something we will gamble with"  # a fixture's quote
INVENTED = "We are opening three new offices next year."  # in no block


def ok(parsed: dict[str, Any], cost: int = 1_000) -> LlmOk:
    return LlmOk(parsed, model=HAIKU, usage=LlmUsage(requests=1, cost_micros=cost))


def labelled(case: TestCase) -> dict[str, Any]:
    """The answer that finds every label of ``case`` with its own quotes."""
    expected = case.expected
    if case.route == PEOPLE:
        return {"people": [dict(person) for person in expected["people"]]}
    if case.route == SIGNALS:
        return {"signals": [dict(signal) for signal in expected["signals"]]}
    [signal] = expected["signals"]
    return {
        "signal_type": signal["signal_type"],
        "segment": case.input["segment"],
        "quote": signal["quote"],
    }


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    meter: InMemoryUsageMeter
    llm: ScriptedLlm
    queue: JobQueue
    playbook: Playbook
    runs: AiTestRuns
    cases: tuple[TestCase, ...] = field(default=())

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def segments(self) -> None:
        segments = validate_segments(seed_yaml.parse(SEGMENTS_FILE.read_text(encoding="utf-8")))
        with self.units.open():
            Setup(self.clock).sync_segments(segments)

    def prompts(
        self,
        defaults: Sequence[RouteSpec] | None = None,
        extra: Sequence[PromptFile] = (),
    ) -> None:
        files, shipped = main.load_prompts()
        with self.units.open():
            self.playbook.sync_prompts(
                (*files, *extra), shipped if defaults is None else defaults, PLAYBOOK
            )

    def sync_cases(self, cases: Sequence[TestCase]) -> TestCaseSync:
        with self.units.open():
            return self.playbook.sync_test_cases(cases)

    def start(self) -> None:
        """What a first start leaves: Segments, the shipped prompts and the fixtures."""
        self.segments()
        self.prompts()
        self.cases = main.load_test_cases()
        self.sync_cases(self.cases)

    def of(self, route: str) -> list[TestCase]:
        return sorted(
            (case for case in self.cases if case.route == route), key=lambda c: c.case_key
        )

    def tuple_id(self, route: str, effort: str = "medium") -> str:
        [(found,)] = self.sql(
            "SELECT id FROM ai_route_tuples WHERE route = ? AND effort = ?", (route, effort)
        )
        return str(found)

    def answer(
        self, route: str, change: Callable[[TestCase, dict[str, Any]], Any] | None = None
    ) -> None:
        """Script one answer per live case of ``route``, in case order."""
        for case in self.of(route):
            parsed = labelled(case)
            self.llm.script.append(ok(parsed) if change is None else change(case, parsed))

    def run(self, tuple_id: str, test_set: str = "extraction", **kwargs: Any) -> bool:
        with self.units.open():
            return self.runs.run(tuple_id, test_set, **kwargs)

    def queue_runs(self) -> int:
        with self.units.open():
            return self.runs.queue_runs()

    def activate(self, tuple_id: str, trigger: ActivationTrigger) -> Any:
        with self.units.open():
            return self.playbook.activate(tuple_id, trigger=trigger)

    def stored_runs(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT tuple_id, test_set, outcome, error, case_count, passed_count, cost_micros, "
            "started_at, finished_at FROM ai_test_runs ORDER BY finished_at, id"
        )

    def activations(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT route, effort, actor, bootstrap, test_run_id, activated_at, deactivated_at "
            "FROM ai_route_activations ORDER BY route, activated_at, id"
        )

    def jobs(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT idempotency_key, lane, payload, status FROM jobs "
            "WHERE type = 'run_ai_test_sets' ORDER BY idempotency_key"
        )

    def route(self, route: str) -> playbook_queries.RouteTuples:
        with self.units.open(read_only=True):
            found = playbook_queries.route_tuples()
        [chosen] = [row for row in found if row.route == route]
        return chosen

    def issues(self) -> tuple[Any, ...]:
        with self.units.open(read_only=True):
            return playbook_queries.health_issues()

    def later(self, minutes: int = 1) -> None:
        self.clock.advance(timedelta(minutes=minutes))


def no_jobs() -> Any:
    raise AssertionError("no other job runs in these tests")


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    meter = InMemoryUsageMeter(clock)
    llm = ScriptedLlm()
    found: dict[str, AiTestRuns] = {}
    job_types = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        test_runs=lambda: found["runs"],
        extractions=no_jobs,
        primary=no_jobs,
        job_boards=no_jobs,
        legal_forms=no_jobs,
    )
    queue = JobQueue(clock, job_types)
    runs = AiTestRuns(clock, meter, llm, queue)
    found["runs"] = runs
    return World(units, db_engine, clock, meter, llm, queue, Playbook(clock), runs)


@pytest.fixture
def started(world: World) -> World:
    world.start()
    return world


def more_candidates(monkeypatch: pytest.MonkeyPatch, route: str, *efforts: Effort) -> None:
    """Shipped-like candidates of ``route`` at other efforts, so ``ScriptedLlm`` (like the
    gateway) finds a tuple that differs from the default only by its effort."""
    [default] = routes.CANDIDATES[route]
    extra = tuple(dataclasses.replace(default, effort=effort) for effort in efforts)
    changed = {**routes.CANDIDATES, route: (default, *extra)}
    monkeypatch.setattr(routes, "CANDIDATES", MappingProxyType(changed))


def candidate(world: World, route: str, effort: Effort) -> str:
    """A newer candidate tuple of ``route`` (its default at another effort, recorded by a sync);
    its id."""
    world.later()
    _, defaults = main.load_prompts()
    world.prompts(
        tuple(
            dataclasses.replace(spec, effort=effort) if spec.route == route else spec
            for spec in defaults
        )
    )
    return world.tuple_id(route, effort.value)


# The sets and the scorers ------------------------------------------------------------------------


def test_the_unknown_route_code_is_the_gateways() -> None:
    assert UNKNOWN_ROUTE_CODE == LLM_UNKNOWN_ROUTE


def test_every_shipped_route_has_a_set_and_its_set_a_scorer() -> None:
    for spec in routes.default_specs():
        assert spec.route in ROUTE_SETS, spec.route
        assert ROUTE_SETS[spec.route] in SCORERS, spec.route
    assert set(SCORERS) == {TestSet.EXTRACTION}  # decision 7: the others ship with their routes


# Row: First start --------------------------------------------------------------------------------


def test_a_first_start_stores_the_fixtures_and_queues_one_run_per_extraction_tuple(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")
    world.segments()
    world.prompts()
    cases = main.load_test_cases()

    synced = world.sync_cases(cases)
    created = world.queue_runs()

    assert len(synced.added) == len(cases)
    assert synced.changed == synced.retired == synced.unchanged == ()
    assert world.sql(  # Story 4.6: the four sets, every row a fixture
        "SELECT test_set, origin, contact_id, COUNT(*) FROM ai_test_cases "
        "WHERE retired_at IS NULL GROUP BY test_set, origin, contact_id ORDER BY test_set"
    ) == [
        (found.value, "fixture", None, sum(1 for case in cases if case.test_set is found))
        for found in sorted(TestSet, key=lambda found: found.value)
    ]
    assert sum(1 for case in cases if case.test_set is TestSet.EXTRACTION) == 11
    assert created == 3  # only the extraction routes have tuples
    expected = sorted(
        (
            f"run_ai_test_sets:{world.tuple_id(route)}:extraction",
            "bulk",
            json.dumps(
                {"test_set": "extraction", "tuple_id": world.tuple_id(route), "v": 1},
                separators=(",", ":"),
            ),
            "queued",
        )
        for route in ROUTES
    )
    assert world.jobs() == expected
    for route in ROUTES:
        active = world.route(route).active
        assert active is not None
        assert (active.tuple_id, active.ungated, active.run) == (world.tuple_id(route), True, None)
    assert world.issues() == ()
    [queued] = [r for r in caplog.records if r.getMessage() == "ai_test_runs_queued"]
    assert queued.count == 3  # type: ignore[attr-defined]
    [record] = [r for r in caplog.records if r.getMessage() == "test_cases_synced"]
    assert (record.added_count, record.retired_count) == (len(cases), 0)  # type: ignore[attr-defined]


# Row: Restart ------------------------------------------------------------------------------------


def test_a_restart_queues_no_job_for_a_live_job_or_a_stored_run(started: World) -> None:
    assert started.queue_runs() == 3

    assert started.queue_runs() == 0  # each live job absorbs its repeat
    started.answer(PEOPLE)
    started.run(started.tuple_id(PEOPLE))
    started.sql("DELETE FROM jobs")
    assert started.queue_runs() == 2  # the people tuple has its run; the other two don't
    assert len(started.jobs()) == 2


def test_a_restart_with_the_same_fixtures_changes_no_case(started: World) -> None:
    before = started.sql("SELECT id, sha256, loaded_at, retired_at FROM ai_test_cases")

    started.later()
    again = started.sync_cases(main.load_test_cases())

    assert (again.added, again.changed, again.retired) == ((), (), ())
    assert len(again.unchanged) == len(started.cases)
    assert started.sql("SELECT id, sha256, loaded_at, retired_at FROM ai_test_cases") == before


# Row: Pass (bootstrap) ---------------------------------------------------------------------------


def test_a_bootstrap_tuple_that_passes_gets_a_tested_activation_of_the_same_tuple(
    started: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")
    tuple_id = started.tuple_id(PEOPLE)
    started.answer(PEOPLE)
    started.later()

    assert started.run(tuple_id) is True

    [(run_id,)] = started.sql("SELECT id FROM ai_test_runs")
    at = "2026-11-18T09:01:00.000000Z"
    assert started.stored_runs() == [(tuple_id, "extraction", "passed", None, 2, 2, 2_000, at, at)]
    [old, new] = [row for row in started.activations() if row[0] == PEOPLE]
    assert old == (PEOPLE, "medium", "seed", 1, None, STAMP, at)
    assert new == (PEOPLE, "medium", "system:playbook", 0, run_id, at, None)
    events = started.sql(
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'ai_route_activations' AND at = ? ORDER BY to_status DESC",
        (at,),
    )
    assert events == [
        ("active", "inactive", "test_passed", run_id, "system:playbook"),
        (None, "active", "test_passed", run_id, "system:playbook"),
    ]
    active = started.route(PEOPLE).active
    assert active is not None and active.run is not None
    assert (active.tuple_id, active.ungated, active.bootstrap) == (tuple_id, False, False)
    assert (active.run.outcome, active.run.failing) == (RunOutcome.PASSED, ())
    assert [(m.name, m.numerator, m.denominator, m.passed) for m in active.run.metrics] == [
        ("quotes", 7, 7, True),  # 4 and 3 labelled people, each with its quote
        ("recall", 7, 7, True),
        ("injections", 0, None, True),
        ("answers", 2, 2, True),
    ]
    assert started.issues() == ()
    with started.units.open(read_only=True):
        served = playbook_queries.active_tuple(PEOPLE)
    assert served is not None and served.effort is Effort.MEDIUM
    calls = started.llm.calls
    assert [urgency for _, _, urgency in calls] == [Urgency.BULK, Urgency.BULK]
    assert [blocks[0].subject for _, blocks, _ in calls] == ["lead:case1", "lead:case2"]
    assert all(found.route == PEOPLE for found, _, _ in calls)
    [finished] = [r for r in caplog.records if r.getMessage() == "ai_test_run_finished"]
    assert finished.__dict__["outcome"] == "passed"
    assert (finished.__dict__["activated"], finished.__dict__["passed_count"]) == (True, 2)
    [activated] = [r for r in caplog.records if r.getMessage() == "route_activated"]
    trigger, changed = activated.trigger, activated.changed  # type: ignore[attr-defined]
    assert (trigger, changed) == ("test_passed", True)
    assert CASE_TEXT not in caplog.text


def test_the_extraction_inputs_follow_the_harness(started: World) -> None:
    started.answer(SIGNALS)
    started.answer(INBOX)

    started.run(started.tuple_id(SIGNALS))
    started.run(started.tuple_id(INBOX))

    signals = [call for call in started.llm.calls if call[0].route == SIGNALS]
    inbox = [call for call in started.llm.calls if call[0].route == INBOX]
    for _, blocks, urgency in signals:
        assert urgency is Urgency.BULK
        assert blocks[0].block_id == blocks[0].source == "signal_types"
        assert blocks[0].text.count("segment ") == 1  # the case's Segment only
    for number, (_, blocks, urgency) in enumerate(inbox, start=1):
        assert urgency is Urgency.SOON
        assert blocks[0].subject == f"signal_item:case{number}"
        assert blocks[0].text.count("segment ") == 2  # every stored Segment
        assert [block.source for block in blocks[1:]] == ["signal_inbox"]


# Row: Fail (bootstrap) ---------------------------------------------------------------------------


def invent_a_quote(case: TestCase, parsed: dict[str, Any]) -> LlmResult:
    if case.case_key == "law_signals_hiring_and_expansion":
        parsed["signals"][0]["quote"] = INVENTED
    return ok(parsed)


def test_a_bootstrap_tuple_that_fails_keeps_serving_and_is_a_health_issue(
    started: World,
) -> None:
    tuple_id = started.tuple_id(SIGNALS)
    started.answer(SIGNALS, invent_a_quote)

    assert started.run(tuple_id) is True

    [row] = started.stored_runs()
    assert row[:6] == (tuple_id, "extraction", "failed", None, 5, 4)
    assert [row for row in started.activations() if row[0] == SIGNALS] == [
        (SIGNALS, "medium", "seed", 1, None, STAMP, None)  # the bootstrap keeps serving
    ]
    active = started.route(SIGNALS).active
    assert active is not None and active.run is not None
    assert (active.ungated, active.run.outcome) == (True, RunOutcome.FAILED)
    assert {m.name: m.passed for m in active.run.metrics} == {
        "quotes": False,
        "recall": True,
        "injections": True,
        "answers": True,
    }
    [failing] = active.run.failing
    assert failing.case_key == "law_signals_hiring_and_expansion"
    assert failing.expected == (
        "Signals: law_expansion, law_hiring_legal_ops; every quote in its block"
    )
    assert failing.got == (
        "Signals: law_expansion, law_hiring_legal_ops; 1 of 2 quotes in their block"
    )
    [issue] = started.issues()
    assert (issue.kind, issue.subject, issue.text) == (
        "ai_tuple_failed",
        SIGNALS,
        "extract_signals: active tuple failed its test set",
    )
    assert issue.detail == (
        "It keeps serving until a tuple passes its test set and is activated. The AI content "
        "page (/ai) shows the run's metrics, its failing cases or why it failed."
    )

    started.later()  # the next run of the same tuple passes: it re-qualifies and clears
    started.answer(SIGNALS)
    started.run(tuple_id)

    assert started.issues() == ()
    active = started.route(SIGNALS).active
    assert active is not None and active.run is not None
    assert (active.ungated, active.run.outcome) == (False, RunOutcome.PASSED)


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        (lambda case, parsed: LlmInvalid(model=HAIKU, usage=LlmUsage()), "answers"),
        (
            lambda case, parsed: ok(
                {**parsed, "signals": [*parsed["signals"], {"signal_type": "law_expansion"}]}
                if case.case_key == "law_signals_hidden_instructions"
                else parsed
            ),
            "injections",
        ),
        (lambda case, parsed: ok({"signals": []}), "recall"),
    ],
)
def test_each_extraction_bar_fails_the_run(
    started: World, change: Callable[[TestCase, dict[str, Any]], LlmResult], failed: str
) -> None:
    started.answer(SIGNALS, change)

    started.run(started.tuple_id(SIGNALS))

    active = started.route(SIGNALS).active
    assert active is not None and active.run is not None
    assert active.run.outcome is RunOutcome.FAILED
    assert not {m.name: m.passed for m in active.run.metrics}[failed]
    assert active.run.failing  # every failing case is listed


def test_a_result_that_isnt_an_answer_is_listed_by_its_type(started: World) -> None:
    started.answer(PEOPLE, lambda case, parsed: LlmInvalid(model=HAIKU, usage=LlmUsage()))

    started.run(started.tuple_id(PEOPLE))

    active = started.route(PEOPLE).active
    assert active is not None and active.run is not None
    assert [(f.case_key, f.got) for f in active.run.failing] == [
        ("law_people_team_pages", "LlmInvalid"),
        ("ops_people_leadership_page", "LlmInvalid"),
    ]


# Row: Pass (newest candidate) and decision 5 ------------------------------------------------------


def test_the_newest_candidate_that_passes_replaces_the_active_tuple(
    started: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    more_candidates(monkeypatch, PEOPLE, Effort.HIGH)
    old = started.tuple_id(PEOPLE)
    new = candidate(started, PEOPLE, Effort.HIGH)
    started.answer(PEOPLE)

    started.run(new)

    rows = [row for row in started.activations() if row[0] == PEOPLE]
    assert [(row[1], row[3], row[6] is None) for row in rows] == [
        ("medium", 1, False),
        ("high", 0, True),
    ]
    found = started.route(PEOPLE)
    assert found.active is not None and found.active.tuple_id == new
    assert [(c.id, c.passing, c.run) for c in found.candidates] == [(old, False, None)]


@pytest.mark.parametrize("passes", [True, False])
def test_the_active_tuple_is_run_when_a_newer_candidate_exists(
    started: World, monkeypatch: pytest.MonkeyPatch, passes: bool
) -> None:
    more_candidates(monkeypatch, PEOPLE, Effort.HIGH)
    old = started.tuple_id(PEOPLE)
    newer = candidate(started, PEOPLE, Effort.HIGH)  # the route's newest tuple row
    if passes:
        started.answer(PEOPLE)
    else:
        started.answer(PEOPLE, lambda case, parsed: LlmInvalid(model=HAIKU, usage=LlmUsage()))
    started.later()

    started.run(old)

    found = started.route(PEOPLE)
    assert found.active is not None and found.active.run is not None
    assert found.active.tuple_id == old  # the active row, though not the newest
    assert [(c.id, c.passing) for c in found.candidates] == [(newer, False)]
    rows = [row for row in started.activations() if row[0] == PEOPLE]
    if passes:  # it re-qualifies: a tested activation of the same tuple
        assert [(row[1], row[2], row[3], row[6] is None) for row in rows] == [
            ("medium", "seed", 1, False),
            ("medium", "system:playbook", 0, True),
        ]
        assert (found.active.ungated, found.active.run.outcome) == (False, RunOutcome.PASSED)
        assert started.issues() == ()
    else:  # it keeps serving, ungated, and is a health issue
        assert [(row[1], row[2], row[3], row[6] is None) for row in rows] == [
            ("medium", "seed", 1, True)
        ]
        assert (found.active.ungated, found.active.run.outcome) == (True, RunOutcome.FAILED)
        [issue] = started.issues()
        assert (issue.kind, issue.subject) == ("ai_tuple_failed", PEOPLE)


def test_an_older_tuple_that_passes_is_a_rollback_target_only(
    started: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    more_candidates(monkeypatch, PEOPLE, Effort.HIGH, Effort.LOW)
    older = candidate(started, PEOPLE, Effort.HIGH)
    newest = candidate(started, PEOPLE, Effort.LOW)
    started.answer(PEOPLE)

    started.run(older)

    assert [row[3] for row in started.activations() if row[0] == PEOPLE] == [1]  # bootstrap
    found = started.route(PEOPLE)
    passing = {c.id: c.passing for c in found.candidates}
    assert passing == {older: True, newest: False}

    started.later()
    rolled = started.activate(older, ActivationTrigger.ROLLBACK)  # the Operator's rollback

    assert (rolled.route, rolled.tuple_id, rolled.changed) == (PEOPLE, older, True)
    rows = [row for row in started.activations() if row[0] == PEOPLE]
    assert [(row[1], row[2], row[6] is None) for row in rows] == [
        ("medium", "seed", False),
        ("high", "operator", True),
    ]
    [cause] = started.sql(
        "SELECT DISTINCT cause FROM events WHERE entity = 'ai_route_activations' "
        "AND actor = 'operator'"
    )
    assert cause == ("rollback",)
    again = started.activate(older, ActivationTrigger.ROLLBACK)  # already serving: no change
    assert again.changed is False
    assert len(started.activations()) == len(rows) + 2  # the two other routes' bootstraps


def test_the_first_activation_of_a_route_deactivates_nothing(world: World) -> None:
    world.segments()
    tuple_id = reply_tuple(world)  # classify_reply: a tuple, no activation

    @use_case
    def store_passing_run() -> str:
        with current().transaction():
            return runs_repo.insert(
                tuple_id=tuple_id,
                test_set="reply",
                outcome="passed",
                error=None,
                case_count=50,
                passed_count=50,
                metrics="[]",
                results="[]",
                cost_micros=0,
                started_at=T0,
                finished_at=T0,
                cases_sha256=playbook_queries.case_set_fingerprint(TestSet.REPLY, "classify_reply"),
            )

    with world.units.open():
        run_id = store_passing_run()
    with world.units.open(read_only=True):
        assert playbook_queries.active_tuple("classify_reply") is None
    world.later()

    activated = world.activate(tuple_id, ActivationTrigger.TEST_PASSED)

    assert (activated.route, activated.tuple_id, activated.run_id) == (
        "classify_reply",
        tuple_id,
        run_id,
    )
    assert activated.changed is True
    events = world.sql(
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'ai_route_activations' AND at = ?",
        ("2026-11-18T09:01:00.000000Z",),
    )
    assert events == [(None, "active", "test_passed", run_id, "system:playbook")]
    with world.units.open(read_only=True):
        served = playbook_queries.active_tuple("classify_reply")
    assert served is not None
    assert (served.route, served.model, served.effort) == ("classify_reply", HAIKU, Effort.MEDIUM)
    assert [row[:5] for row in world.activations() if row[0] == "classify_reply"] == [
        ("classify_reply", "medium", "system:playbook", 0, run_id)
    ]


def test_a_passing_run_with_activate_false_changes_no_activation(started: World) -> None:
    before = started.activations()
    started.answer(PEOPLE)

    started.run(started.tuple_id(PEOPLE), activate=False)

    assert [row[2] for row in started.stored_runs()] == ["passed"]
    assert started.activations() == before


# Row: Too small ----------------------------------------------------------------------------------


def test_a_route_with_too_few_cases_fails_with_set_too_small_and_no_call(started: World) -> None:
    started.sync_cases([case for case in started.cases if case.route != PEOPLE])
    tuple_id = started.tuple_id(PEOPLE)

    started.run(tuple_id)

    assert started.llm.calls == []
    assert started.stored_runs() == [
        (tuple_id, "extraction", "failed", "set_too_small", 0, 0, 0, STAMP, STAMP)
    ]
    [(metrics, results)] = started.sql("SELECT metrics, results FROM ai_test_runs")
    assert (metrics, results) == ("[]", "[]")
    active = started.route(PEOPLE).active
    assert active is not None and active.run is not None
    assert (active.run.error, active.ungated) == (RunError.SET_TOO_SMALL, True)


def reply_case(number: int) -> TestCase:
    return TestCase(
        f"reply_{number:02}",
        TestSet.REPLY,
        "classify_reply",
        {"text": f"Reply {number}."},
        {"class": "unsubscribe"},
        ("unsubscribe",),
    )


def reply_tuple(world: World) -> str:
    _, defaults = main.load_prompts()
    spec = RouteSpec("classify_reply", HAIKU, Effort.MEDIUM, "v1", 1)
    world.prompts((*defaults, spec), (PromptFile("classify_reply", "v1", "Classify it."),))
    return world.tuple_id("classify_reply")


def test_a_reply_tuple_before_story_4_6_fails_too_small(world: World) -> None:
    world.segments()
    tuple_id = reply_tuple(world)
    world.sync_cases([reply_case(n) for n in range(49)])

    world.run(tuple_id, "reply")

    assert [row[2:5] for row in world.stored_runs()] == [("failed", "set_too_small", 49)]
    assert world.llm.calls == []


def test_a_set_big_enough_without_a_scorer_raises_before_any_call(world: World) -> None:
    world.segments()
    tuple_id = reply_tuple(world)
    world.sync_cases([reply_case(n) for n in range(50)])

    with pytest.raises(LookupError, match="reply set has no scorer"):
        world.run(tuple_id, "reply")

    assert (world.stored_runs(), world.llm.calls) == ([], [])


# Row: Over budget, API down, No key --------------------------------------------------------------


def spend_the_budget(world: World) -> None:
    world.meter.record(
        AiCall(
            SIGNALS,
            "claude-haiku-5-5:medium:v1:s1:aaaaaaaaaaaa",
            HAIKU,
            input_tokens=1,
            output_tokens=1,
            cost_micros=20_000_000,  # the default $20 budget
        )
    )


def test_an_over_budget_run_makes_no_call_stores_nothing_and_the_job_is_deferred(
    started: World,
) -> None:
    spend_the_budget(started)
    started.queue_runs()

    assert started.run(started.tuple_id(PEOPLE)) is False

    with started.units.open():
        claim = started.queue.claim(Lane.BULK)
    assert claim is not None
    handler = started.queue._job_types.get("run_ai_test_sets").handler  # noqa: SLF001
    with started.units.open():
        assert handler(claim.job) is DEFERRED
    assert (started.llm.calls, started.stored_runs()) == ([], [])


def run_the_job(world: World, error: AdapterError) -> JobStatus | None:
    """Claim the first queued run, let it fail on ``error``, and record the failure as the
    runner does."""
    world.queue_runs()
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None
    world.llm.script.append(error)
    handler = world.queue._job_types.get("run_ai_test_sets").handler  # noqa: SLF001
    with pytest.raises(AdapterError) as raised:
        with world.units.open():
            handler(claim.job)
    assert raised.value is error
    with world.units.open():
        return world.queue.fail(claim, raised.value)


def test_the_api_down_stores_nothing_and_the_job_is_retried(started: World) -> None:
    status = run_the_job(started, AdapterError(True, "llm_unavailable", "the API is down"))

    assert status is JobStatus.QUEUED
    assert started.stored_runs() == []


def test_no_key_stores_nothing_kills_the_job_and_the_next_start_queues_it_again(
    started: World,
) -> None:
    status = run_the_job(started, AdapterError(False, "credential_not_set", "no Claude key"))

    assert status is JobStatus.DEAD
    assert started.stored_runs() == []
    assert started.queue_runs() == 1  # the dead job's key is free again


def test_an_unknown_route_ends_the_run_failed(started: World) -> None:
    tuple_id = candidate(started, PEOPLE, Effort.HIGH)  # no shipped candidate at effort high

    started.run(tuple_id)

    assert started.llm.calls == []  # the gateway refused the tuple before any request
    [row] = started.stored_runs()
    assert row[:6] == (tuple_id, "extraction", "failed", "unknown_route", 2, 0)


def test_a_case_naming_an_unstored_segment_raises_before_any_call(world: World) -> None:
    world.prompts()  # no Segment stored: seed sync never ran
    world.cases = main.load_test_cases()
    world.sync_cases(world.cases)

    with pytest.raises(InvalidTestCases, match="Segment isn't stored") as raised:
        world.run(world.tuple_id(PEOPLE))

    assert (world.stored_runs(), world.llm.calls) == ([], [])
    assert CASE_TEXT not in str(raised.value)


# Rows: Activate untested, the refusals ------------------------------------------------------------


def test_activating_a_tuple_without_a_passing_run_is_refused_and_writes_nothing(
    started: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    more_candidates(monkeypatch, PEOPLE, Effort.HIGH)
    untested = candidate(started, PEOPLE, Effort.HIGH)
    failed = started.tuple_id(SIGNALS)
    started.answer(SIGNALS, invent_a_quote)
    started.run(failed)
    before = (
        started.activations(),
        started.sql("SELECT COUNT(*) FROM events"),
    )

    for tuple_id in (untested, failed):
        for trigger in ActivationTrigger:
            with pytest.raises(ActivationRefused, match="^No passing test run for this tuple$"):
                started.activate(tuple_id, trigger)
    for tuple_id in ("01JD0000000000000000000099", "not-a-ulid"):
        with pytest.raises(ActivationRefused, match="^No such tuple$") as refused:
            started.activate(tuple_id, ActivationTrigger.ROLLBACK)
        assert refused.value.code == "activation_refused"

    assert (started.activations(), started.sql("SELECT COUNT(*) FROM events")) == before
    with started.units.open(read_only=True):
        served = playbook_queries.active_tuple(PEOPLE)
    assert served is not None and served.effort is Effort.MEDIUM  # the previous tuple serves
    with pytest.raises(TypeError):
        started.activate(untested, "rollback")  # type: ignore[arg-type]


# Row: No candidate --------------------------------------------------------------------------------


def test_a_route_without_a_tuple_is_not_run_and_a_route_without_a_set_gets_no_job(
    started: World,
) -> None:
    _, defaults = main.load_prompts()
    probe = RouteSpec("draft_probe", "claude-opus-5-5", Effort.HIGH, "v1", 1)
    started.prompts((*defaults, probe), (PromptFile("draft_probe", "v1", "Draft."),))

    assert started.queue_runs() == 3  # the three extraction tuples only

    assert not any("classify_reply" in key or ":draft" in key for key, *_ in started.jobs())


# The skips ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tuple_id", "test_set", "reason"),
    [
        ("01JD0000000000000000000099", "extraction", "unknown_tuple"),
        ("not-a-ulid", "extraction", "unknown_tuple"),
        (None, "reply", "not_the_routes_set"),
        (None, "nonsense", "not_the_routes_set"),
    ],
)
def test_an_unknown_tuple_or_another_routes_set_is_skipped(
    started: World,
    caplog: pytest.LogCaptureFixture,
    tuple_id: str | None,
    test_set: str,
    reason: str,
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")
    chosen = tuple_id if tuple_id is not None else started.tuple_id(PEOPLE)

    assert started.run(chosen, test_set) is True

    assert (started.stored_runs(), started.llm.calls) == ([], [])
    [skipped] = [r for r in caplog.records if r.getMessage() == "ai_test_run_skipped"]
    assert skipped.reason == reason  # type: ignore[attr-defined]


# Row: Fixture edited, and the sync's input checks -------------------------------------------------


def test_an_edited_fixture_retires_its_old_row_and_a_removed_one_is_retired(
    started: World,
) -> None:
    [first, second, *rest] = started.cases
    edited = dataclasses.replace(first, covers=(*first.covers, "edited"))
    started.later()

    result = started.sync_cases([edited, *rest])

    assert result.changed == (("extraction", first.case_key),)
    assert result.retired == (("extraction", second.case_key),)
    assert len(result.unchanged) == len(started.cases) - 2 and result.added == ()
    rows = started.sql(
        "SELECT case_key, sha256, retired_at FROM ai_test_cases WHERE case_key IN (?, ?) "
        "ORDER BY case_key, loaded_at",
        (first.case_key, second.case_key),
    )
    at = "2026-11-18T09:01:00.000000Z"
    assert rows == sorted(
        [
            (first.case_key, first.sha256, at),
            (first.case_key, edited.sha256, None),
            (second.case_key, second.sha256, at),
        ]
    )


def test_the_sync_refuses_a_non_case_or_a_repeated_key_before_any_write(world: World) -> None:
    cases = main.load_test_cases()

    with pytest.raises(TypeError):
        world.sync_cases([*cases, "case"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="more than once"):
        world.sync_cases([*cases, cases[0]])
    override = dataclasses.replace(cases[0], case_key="override-01jd3qz8x5v9k2m7n4p6r8t0b1")
    with pytest.raises(ValueError, match="never starts with override-"):  # Story 4.6
        world.sync_cases([*cases, override])

    assert world.sql("SELECT COUNT(*) FROM ai_test_cases") == [(0,)]


def test_an_empty_input_touches_no_table(started: World) -> None:
    assert started.sync_cases(()) == TestCaseSync((), (), (), ())
    assert started.sql(
        "SELECT COUNT(*) FROM ai_test_cases WHERE retired_at IS NULL"
    ) == [(len(started.cases),)]


# Story 4.6: a run is bound to the case set it measured ------------------------------------------


def people_edited(world: World) -> TestCase:
    """Edit one extract_people fixture (a new extraction set for that route); the edited case."""
    [first] = [case for case in world.cases if case.case_key == "law_people_team_pages"]
    edited = dataclasses.replace(first, covers=(*first.covers, "edited"))
    world.cases = tuple(edited if case is first else case for case in world.cases)
    world.later()
    world.sync_cases(world.cases)
    return edited


def fingerprint(world: World, test_set: TestSet, route: str) -> str:
    with world.units.open(read_only=True):
        return playbook_queries.case_set_fingerprint(test_set, route)


def test_a_run_stores_the_fingerprint_of_the_cases_it_measured(started: World) -> None:
    started.answer(PEOPLE)

    started.run(started.tuple_id(PEOPLE))

    shas = sorted(case.sha256 for case in started.of(PEOPLE))
    [(stored,)] = started.sql("SELECT cases_sha256 FROM ai_test_runs")
    assert stored == case_set_sha256(shas) == fingerprint(started, TestSet.EXTRACTION, PEOPLE)
    assert stored != fingerprint(started, TestSet.EXTRACTION, SIGNALS)  # per route
    active = started.route(PEOPLE).active
    assert active is not None and active.run is not None and active.run.current is True


def test_a_changed_case_set_queues_the_tuple_again_and_a_run_on_it_does_not(
    started: World,
) -> None:
    started.answer(PEOPLE)
    started.run(started.tuple_id(PEOPLE))
    started.sql("DELETE FROM jobs")
    assert started.queue_runs() == 2  # the people tuple measured the live set

    started.sql("DELETE FROM jobs")
    people_edited(started)

    assert started.queue_runs() == 3  # the people set changed: its tuple runs again
    assert f"run_ai_test_sets:{started.tuple_id(PEOPLE)}:extraction" in {
        row[0] for row in started.jobs()
    }
    active = started.route(PEOPLE).active
    assert active is not None and active.run is not None and active.run.current is False
    started.sql("DELETE FROM jobs")
    started.answer(PEOPLE)
    started.later()

    started.run(started.tuple_id(PEOPLE))  # the newest run measured the live set

    assert started.queue_runs() == 2  # so the people tuple is left out, though an older run
    assert f"run_ai_test_sets:{started.tuple_id(PEOPLE)}:extraction" not in {  # didn't
        row[0] for row in started.jobs()
    }
    active = started.route(PEOPLE).active
    assert active is not None and active.run is not None and active.run.current is True


def test_a_run_stored_before_story_4_6_is_queued_again_and_qualifies_nothing(
    started: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    more_candidates(monkeypatch, PEOPLE, Effort.HIGH)
    old = started.tuple_id(PEOPLE)
    started.answer(PEOPLE)
    started.run(old)
    new = candidate(started, PEOPLE, Effort.HIGH)
    started.answer(PEOPLE)
    started.run(new)  # both passed; the newer serves
    started.sql("DELETE FROM jobs")
    started.sql("UPDATE ai_test_runs SET cases_sha256 = NULL")  # 4.5 runs: no fingerprint

    assert started.queue_runs() == 4  # both people tuples, and the two other routes
    route = started.route(PEOPLE)
    assert route.active is not None and route.active.run is not None
    assert route.active.run.current is False
    [older] = route.candidates
    assert older.id == old and older.run is not None
    assert (older.run.outcome, older.run.current, older.passing) == (
        RunOutcome.PASSED,
        False,
        False,
    )
    with pytest.raises(ActivationRefused, match="^No passing test run for this tuple$"):
        started.activate(old, ActivationTrigger.ROLLBACK)


def test_a_failed_run_on_the_live_set_is_not_queued_again(started: World) -> None:
    started.answer(SIGNALS, invent_a_quote)
    started.run(started.tuple_id(SIGNALS))
    [(outcome,)] = started.sql("SELECT outcome FROM ai_test_runs")
    assert outcome == "failed"
    started.sql("DELETE FROM jobs")

    assert started.queue_runs() == 2  # people and inbox; the failed signals tuple waits
    assert f"run_ai_test_sets:{started.tuple_id(SIGNALS)}:extraction" not in {
        row[0] for row in started.jobs()
    }


def test_a_too_small_set_runs_again_once_it_grows(world: World) -> None:
    world.segments()
    tuple_id = reply_tuple(world)
    world.sync_cases([reply_case(n) for n in range(49)])
    world.run(tuple_id, "reply")
    world.sql("DELETE FROM jobs")
    key = f"run_ai_test_sets:{tuple_id}:reply"
    world.queue_runs()
    assert key not in {row[0] for row in world.jobs()}  # its failed run measured the live set
    world.sql("DELETE FROM jobs")

    world.sync_cases([reply_case(n) for n in range(50)])

    world.queue_runs()
    assert key in {row[0] for row in world.jobs()}


def test_a_pass_on_an_earlier_set_qualifies_nothing(started: World) -> None:
    tuple_id = started.tuple_id(PEOPLE)
    started.answer(PEOPLE)
    started.run(tuple_id)  # passes and activates: a tested activation
    before = started.activations()
    people_edited(started)

    with pytest.raises(ActivationRefused, match="^No passing test run for this tuple$"):
        started.activate(tuple_id, ActivationTrigger.ROLLBACK)

    assert started.activations() == before  # the tested activation keeps serving


def test_a_stale_pass_is_no_rollback_target(
    started: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    more_candidates(monkeypatch, PEOPLE, Effort.HIGH)
    old = started.tuple_id(PEOPLE)
    started.answer(PEOPLE)
    started.run(old)
    new = candidate(started, PEOPLE, Effort.HIGH)
    started.answer(PEOPLE)
    started.run(new)  # the newest candidate passes and serves
    [older] = started.route(PEOPLE).candidates
    assert (older.id, older.passing) == (old, True)

    people_edited(started)

    [older] = started.route(PEOPLE).candidates
    assert older.passing is False
    assert older.run is not None and older.run.current is False
    with pytest.raises(ActivationRefused, match="^No passing test run for this tuple$"):
        started.activate(old, ActivationTrigger.ROLLBACK)


def test_activation_takes_the_newest_pass_on_the_live_set(started: World) -> None:
    tuple_id = started.tuple_id(PEOPLE)
    started.answer(PEOPLE)
    started.later()
    started.run(tuple_id)  # a pass on set A
    [(first_run,)] = started.sql("SELECT id FROM ai_test_runs")
    shipped = started.cases
    people_edited(started)
    started.answer(PEOPLE)
    started.later()
    started.run(tuple_id)  # a pass on set B
    started.later()
    started.sync_cases(shipped)  # back to set A
    started.cases = shipped

    activated = started.activate(tuple_id, ActivationTrigger.ROLLBACK)

    assert activated.run_id == first_run  # set A's pass, not the newer pass on set B


class ChangesCasesMidRun:
    """An LLM that retires one live case of the route at its first call, as an edit landing
    while a run's calls are made."""

    def __init__(self, world: World, case_key: str) -> None:
        self.world = world
        self.case_key = case_key
        self.changed = False

    def run(self, route_tuple: Any, inputs: Any, urgency: Any) -> LlmResult:
        if not self.changed:
            self.changed = True
            self.world.sql(
                "UPDATE ai_test_cases SET retired_at = ? WHERE case_key = ? "
                "AND retired_at IS NULL",
                ("2026-11-18T09:00:30.000000Z", self.case_key),
            )
        return self.world.llm.run(route_tuple, inputs, urgency)


def test_cases_changed_during_a_passing_run_leave_it_stored_and_activate_nothing(
    started: World,
) -> None:
    tuple_id = started.tuple_id(PEOPLE)
    started.answer(PEOPLE)
    before = started.activations()
    measured = fingerprint(started, TestSet.EXTRACTION, PEOPLE)
    runs = AiTestRuns(
        started.clock,
        started.meter,
        ChangesCasesMidRun(started, "ops_people_leadership_page"),  # type: ignore[arg-type]
        started.queue,
    )

    with started.units.open():
        assert runs.run(tuple_id, "extraction") is True

    [(outcome, stored)] = started.sql("SELECT outcome, cases_sha256 FROM ai_test_runs")
    assert (outcome, stored) == ("passed", measured)
    assert started.activations() == before  # still the bootstrap: nothing activated
    assert fingerprint(started, TestSet.EXTRACTION, PEOPLE) != measured
    started.sql("DELETE FROM jobs")
    assert started.queue_runs() == 3  # the people tuple runs again, on the live set
