"""The AI test gate against the real Claude API (FR-73, AD-12, AD-14, Story 4.5). Opt-in:
``uv run pytest -m ai_eval`` with ``ANTHROPIC_API_KEY`` set in the shell (never in a file). It
costs a few cents of Haiku.

It runs ``AiTestRuns.run(tuple_id, "extraction", activate=False)``, the same runner the
``run_ai_test_sets`` job calls, for each of the three extraction tuples, through
``ClaudeGateway`` on the real HTTP client (``allow_live_constructor`` restores
``default_http_client``; the network guard lets an ``ai_eval`` item reach ``api.anthropic.com``
only), with an ``InMemorySecrets`` holding the key and an in-memory meter, on the temporary test
database (``db_engine``) holding the shipped Segments, prompts and extraction fixtures. It
prints each run's metrics and failing cases, and asserts that every run was stored and that
``ai_route_activations`` is unchanged: the eval never activates a tuple. Whether the runs pass
is printed, not asserted (``tests/ai_eval/test_extraction_eval.py`` holds the bars).
"""

import os
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.playbook.api import AiTestRuns, Playbook
from nextron.app.queries import playbook as playbook_queries
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.credentials import CredentialKey, credential
from nextron.domain.playbook import PlaybookSnapshot
from nextron.domain.segments import validate_segments
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import allow_live_constructor

KEY_VARIABLE = "ANTHROPIC_API_KEY"
ROUTES = ("extract_signals", "extract_people", "signal_inbox_parse")
PLAYBOOK = PlaybookSnapshot("1.0.0", "---\nversion: 1.0.0\n---\n\nA test Sales Playbook.\n")


def no_jobs() -> Any:
    raise AssertionError("no job runs in the eval: it calls the runner itself")


def activation_rows(engine: Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT * FROM ai_route_activations ORDER BY id"
            ).all()
        ]


@pytest.mark.ai_eval
def test_the_gate_runs_the_extraction_tuples_and_activates_nothing(
    monkeypatch: pytest.MonkeyPatch, db_engine: Engine
) -> None:
    key = os.environ.get(KEY_VARIABLE, "").strip()
    if not key:
        pytest.skip(f"set {KEY_VARIABLE} in this shell to run the gate eval")
    from nextron.adapters.ai import ClaudeGateway  # noqa: PLC0415 - only this test loads it

    allow_live_constructor(monkeypatch, "nextron.adapters.ai", "default_http_client")
    secrets = InMemorySecrets()
    secrets.write(credential(CredentialKey.CLAUDE).secret_name, key)
    clock = FrozenClock()
    meter = InMemoryUsageMeter(clock)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments_file = main.SEED_DIR / main.SEGMENTS_FILE_NAME
    segments = validate_segments(seed_yaml.parse(segments_file.read_text(encoding="utf-8")))
    files, defaults = main.load_prompts()
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Playbook(clock).sync_prompts(files, defaults, PLAYBOOK)
    with units.open():
        Playbook(clock).sync_test_cases(main.load_test_cases())
    job_types = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        test_runs=no_jobs,
        extractions=no_jobs,
        primary=no_jobs,
        job_boards=no_jobs,
        legal_forms=no_jobs,
    )
    runs = AiTestRuns(clock, meter, ClaudeGateway(secrets, meter=meter), JobQueue(clock, job_types))
    before = activation_rows(db_engine)

    for route in ROUTES:
        with units.open(read_only=True):
            [found] = [row for row in playbook_queries.route_tuples() if row.route == route]
        assert found.active is not None and found.active.tuple_id is not None
        with units.open():
            assert runs.run(found.active.tuple_id, "extraction", activate=False) is True

    with units.open(read_only=True):
        tuples = playbook_queries.route_tuples()
    for found in tuples:
        assert found.active is not None and found.active.run is not None
        run = found.active.run
        lines = [f"{found.route}: {run.outcome.value}, {run.passed_count} of {run.case_count}"]
        lines += [
            f"  {m.label}: {m.numerator} of {m.denominator}, passed {m.passed}"
            for m in run.metrics
        ]
        lines += [
            f"  failing {c.case_key}: expected {c.expected}; got {c.got}" for c in run.failing
        ]
        print("\n".join(lines))  # noqa: T201 - the report is the point of the run
    print(f"metered requests: {len(meter.calls)}")  # noqa: T201
    assert activation_rows(db_engine) == before  # the eval never writes ai_route_activations
