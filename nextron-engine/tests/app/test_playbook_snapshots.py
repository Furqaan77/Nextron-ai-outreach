"""The Sales Playbook snapshots, the candidate route tuples and the Playbook checks (FR-71,
AD-8, NFR-25, Story 4.4) on real units of work in a temporary database: every sync and query
row of the spec's I/O matrix, the pinned Playbook surviving an edit of the live file,
``health_issues``, the tables' checks and indexes, and that no Playbook text reaches a log
record, an event or an error. The Playbook is a temporary copy; the real file is only read."""

import dataclasses
import json
import logging
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.adapters.ai.prompting import build_request
from nextron.adapters.ai.routes import CANDIDATES
from nextron.app.playbook.api import Playbook, PlaybookRead, PromptSync
from nextron.app.queries import playbook as playbook_queries
from nextron.bootstrap import load_bootstrap
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.ai import Effort, RouteSpec, UntrustedBlock
from nextron.domain.health import HealthIssue
from nextron.domain.playbook import (
    AUTHORITY_ORDER,
    PlaybookProblem,
    PlaybookSnapshot,
    SyncTrigger,
    playbook_section,
    problem_text,
)
from nextron.domain.prompts import InvalidPrompts, PromptFile
from tests.fakes.clock import DEFAULT_NOW, FrozenClock

ROUTES: Final = ("extract_people", "extract_signals", "signal_inbox_parse")  # by name
STAMP: Final = "2026-11-16T09:00:00.000000Z"
MARKER: Final = "PlaybookTextMarker"  # in every test Playbook's text; never logged or raised
PROBE: Final = "draft_probe"  # a Playbook route that only these tests define
PROBE_FILE: Final = PromptFile(PROBE, "v1", "Draft one email for the Lead.")
PROBE_SPEC: Final = RouteSpec(PROBE, "claude-opus-5-5", Effort.HIGH, "v1", 1, uses_playbook=True)
PROBE_CANDIDATE: Final = dataclasses.replace(
    CANDIDATES["extract_signals"][0],
    name=PROBE,
    model="claude-opus-5-5",
    effort=Effort.HIGH,
    batchable=False,
    uses_playbook=True,
)


def playbook_text(version: str = "0.2.0", body: str = "Cite one Signal.") -> str:
    return f"---\ntitle: Test Playbook\nversion: {version}\n---\n\n{MARKER}: {body}\n"


def snapshot(version: str = "0.2.0", body: str = "Cite one Signal.") -> PlaybookSnapshot:
    return PlaybookSnapshot(version, playbook_text(version, body))


def query(engine: Engine, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
    with engine.connect() as connection:
        result = connection.exec_driver_sql(statement, params)
        rows = result.all() if result.returns_rows else []
        connection.commit()
    return [tuple(row) for row in rows]


def playbook_rows(engine: Engine) -> list[tuple[Any, ...]]:
    return query(engine, "SELECT version, sha256, captured_at FROM playbook_snapshots ORDER BY id")


def tuple_rows(engine: Engine) -> list[tuple[Any, ...]]:
    return query(
        engine,
        "SELECT route, model, effort, prompt_version, prompt_sha256, playbook_sha256, "
        "schema_version, created_at FROM ai_route_tuples ORDER BY route, id",
    )


def check_rows(engine: Engine) -> list[tuple[Any, ...]]:
    return query(
        engine,
        'SELECT checked_at, "trigger", playbook_sha256, problem FROM playbook_checks ORDER BY id',
    )


def activation_rows(engine: Engine) -> list[tuple[Any, ...]]:
    return query(
        engine,
        "SELECT route, model, effort, prompt_version, playbook_sha256, bootstrap, actor, "
        "deactivated_at FROM ai_route_activations ORDER BY route, id",
    )


def tuple_events(engine: Engine) -> list[tuple[Any, ...]]:
    return query(
        engine,
        "SELECT entity_id, from_status, to_status, cause, cause_ref, actor, at FROM events "
        "WHERE entity = 'ai_route_tuples' ORDER BY id",
    )


def shipped() -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
    return main.load_prompts()


def at(days: int) -> FrozenClock:
    return FrozenClock(DEFAULT_NOW + timedelta(days=days))


def sync(
    units: UnitOfWorkFactory,
    playbook: PlaybookSnapshot | PlaybookProblem,
    *,
    files: tuple[PromptFile, ...] | None = None,
    defaults: tuple[RouteSpec, ...] | None = None,
    clock: FrozenClock | None = None,
    trigger: SyncTrigger = SyncTrigger.STARTUP,
) -> PromptSync:
    shipped_files, shipped_defaults = shipped()
    with units.open():
        return Playbook(clock or FrozenClock()).sync_prompts(
            shipped_files if files is None else files,
            shipped_defaults if defaults is None else defaults,
            playbook,
            trigger=trigger,
        )


def with_probe() -> dict[str, Any]:
    files, defaults = shipped()
    return {"files": (*files, PROBE_FILE), "defaults": (*defaults, PROBE_SPEC)}


def health(units: UnitOfWorkFactory) -> tuple[HealthIssue, ...]:
    with units.open(read_only=True):
        return playbook_queries.health_issues()


def route_tuples(units: UnitOfWorkFactory) -> tuple[playbook_queries.RouteTuples, ...]:
    with units.open(read_only=True):
        return playbook_queries.route_tuples()


def status(units: UnitOfWorkFactory) -> playbook_queries.PlaybookStatus:
    with units.open(read_only=True):
        return playbook_queries.playbook_status()


@pytest.fixture
def playbook_copy(tmp_path: Path) -> Path:
    """A copy of the shipped Sales Playbook that a test may edit."""
    path = tmp_path / "playbook" / "sales-playbook.md"
    path.parent.mkdir()
    shutil.copyfile(load_bootstrap("test").playbook_path, path)
    return path


# Row: First start
def test_a_first_start_stores_the_playbook_and_records_each_tuple_and_the_check(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    playbook_copy: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    read = main.read_playbook_file(playbook_copy)
    assert isinstance(read, PlaybookSnapshot)
    assert read.version == "0.2.0"
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    result = sync(units, read)

    assert len(query(db_engine, "SELECT id FROM prompt_snapshots")) == 3
    assert playbook_rows(db_engine) == [("0.2.0", read.sha256, STAMP)]
    files = {f.route: f.sha256 for f in shipped()[0]}
    assert tuple_rows(db_engine) == [
        (route, "claude-haiku-5-5", "medium", "v1", files[route], None, 1, STAMP)
        for route in ROUTES
    ]
    ids = {row[0] for row in query(db_engine, "SELECT id FROM ai_route_tuples")}
    events = tuple_events(db_engine)
    assert {row[0] for row in events} == ids
    assert {row[1:] for row in events} == {
        (None, "candidate", "startup_sync", None, "system:playbook", STAMP)
    }
    assert [row[4:7] for row in activation_rows(db_engine)] == [(None, 1, "seed")] * 3
    assert check_rows(db_engine) == [(STAMP, "startup", read.sha256, None)]
    assert result.playbook == PlaybookRead("0.2.0", read.sha256, stored=True)
    assert sorted(route for route, _ in result.candidates) == list(ROUTES)
    assert {tuple_id for _, tuple_id in result.candidates} == ids
    [record] = [r for r in caplog.records if r.getMessage() == "prompts_synced"]
    assert record.candidate_count == 3  # type: ignore[attr-defined]
    assert record.trigger == "startup"  # type: ignore[attr-defined]
    assert health(units) == ()


# Row: Restart
def test_a_restart_with_nothing_changed_only_records_a_check(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    first = snapshot()
    sync(units, first)
    before = (playbook_rows(db_engine), tuple_rows(db_engine), tuple_events(db_engine))

    result = sync(units, first, clock=at(1))

    assert (playbook_rows(db_engine), tuple_rows(db_engine), tuple_events(db_engine)) == before
    assert result.candidates == ()
    assert result.playbook == PlaybookRead("0.2.0", first.sha256, stored=False)
    assert [row[1:] for row in check_rows(db_engine)] == [("startup", first.sha256, None)] * 2


# Row: Playbook edited
def test_an_edited_playbook_is_stored_and_gives_its_route_one_candidate(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    first, edited = snapshot(), snapshot("0.3.0", "Cite one Signal and one fact.")
    created = sync(units, first, **with_probe())
    [(probe_route, first_tuple)] = [pair for pair in created.candidates if pair[0] == PROBE]
    activations = activation_rows(db_engine)

    result = sync(units, edited, clock=at(1), **with_probe())

    assert [row[:2] for row in playbook_rows(db_engine)] == [
        ("0.2.0", first.sha256),
        ("0.3.0", edited.sha256),
    ]
    [(route, new_tuple)] = result.candidates
    assert route == PROBE
    [stored] = query(
        db_engine, "SELECT playbook_sha256 FROM ai_route_tuples WHERE id = ?", (new_tuple,)
    )
    assert stored == (edited.sha256,)
    assert activation_rows(db_engine) == activations  # nothing activated, nothing changed
    assert PROBE not in {row[0] for row in activations}  # a Playbook route: never bootstrapped
    [probe] = [found for found in route_tuples(units) if found.route == PROBE]
    assert probe.active is None
    assert [c.id for c in probe.candidates] == [new_tuple, first_tuple]  # newest first
    assert [c.playbook for c in probe.candidates] == [
        playbook_queries.PlaybookRef("0.3.0", edited.sha256),
        playbook_queries.PlaybookRef("0.2.0", first.sha256),
    ]


def test_a_new_hash_is_stored_even_when_its_version_is_already_used(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:  # decision 7
    first, same_version = snapshot(), snapshot(body="Reworded, but still 0.2.0.")

    sync(units, first)
    result = sync(units, same_version, clock=at(1))

    assert [row[:2] for row in playbook_rows(db_engine)] == [
        ("0.2.0", first.sha256),
        ("0.2.0", same_version.sha256),
    ]
    assert result.playbook == PlaybookRead("0.2.0", same_version.sha256, stored=True)


# Row: Playbook reverted
def test_a_reverted_playbook_stores_nothing_and_clears_the_health_issue(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    first, edited = snapshot(), snapshot("0.3.0")
    sync(units, first, **with_probe())
    sync(units, edited, clock=at(1), **with_probe())
    sync(units, PlaybookProblem.NO_VERSION, clock=at(2), **with_probe())
    assert [issue.kind for issue in health(units)] == ["playbook_invalid"]
    snapshots, tuples = playbook_rows(db_engine), tuple_rows(db_engine)

    result = sync(units, first, clock=at(3), **with_probe())

    assert (playbook_rows(db_engine), tuple_rows(db_engine)) == (snapshots, tuples)
    assert result.candidates == ()
    assert result.playbook == PlaybookRead("0.2.0", first.sha256, stored=False)
    assert check_rows(db_engine)[-1][1:] == ("startup", first.sha256, None)  # it names it
    assert health(units) == ()
    current = status(units).current
    assert current is not None and (current.version, current.sha256) == ("0.2.0", first.sha256)


# Rows: No version, Other problems
@pytest.mark.parametrize("problem", list(PlaybookProblem))
def test_a_problem_read_stores_no_snapshot_and_gives_the_playbook_route_no_tuple(
    problem: PlaybookProblem,
    units: UnitOfWorkFactory,
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    result = sync(units, problem, **with_probe())

    assert playbook_rows(db_engine) == []
    assert result.playbook is problem
    assert sorted(route for route, _ in result.candidates) == list(ROUTES)  # no probe tuple
    assert len(activation_rows(db_engine)) == 3  # extraction is unaffected
    assert check_rows(db_engine) == [(STAMP, "startup", None, problem.value)]
    assert health(units) == (
        HealthIssue(
            "playbook_invalid",
            "playbook",
            "Sales Playbook: " + problem_text(problem),
            detail="Fix the file, then use Check for changes on the AI content page (/ai) or "
            "restart the engine.",
        ),
    )
    [warning] = [r for r in caplog.records if r.getMessage() == "playbook_invalid"]
    assert warning.levelname == "WARNING"
    assert warning.problem == problem.value  # type: ignore[attr-defined]
    assert warning.trigger == "startup"  # type: ignore[attr-defined]
    last = status(units).last_check
    assert last == playbook_queries.LastCheck(DEFAULT_NOW, SyncTrigger.STARTUP, problem)
    assert status(units).current is None


def test_a_problem_after_a_good_read_keeps_the_stored_snapshot_current(
    units: UnitOfWorkFactory,
) -> None:
    first = snapshot()
    sync(units, first)
    sync(units, PlaybookProblem.TOO_LARGE, clock=at(1))

    found = status(units)

    assert found.current == playbook_queries.PlaybookVersion("0.2.0", first.sha256, DEFAULT_NOW)
    assert found.last_check == playbook_queries.LastCheck(
        DEFAULT_NOW + timedelta(days=1), SyncTrigger.STARTUP, PlaybookProblem.TOO_LARGE
    )


# Row: BOM and CRLF
def test_a_bom_and_crlf_copy_of_the_playbook_is_the_same_snapshot(
    units: UnitOfWorkFactory, db_engine: Engine, playbook_copy: Path
) -> None:
    text = playbook_copy.read_bytes().decode("utf-8").replace("\r\n", "\n")
    playbook_copy.write_bytes(text.encode("utf-8"))
    sync(units, main.read_playbook_file(playbook_copy))
    playbook_copy.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))

    result = sync(units, main.read_playbook_file(playbook_copy), clock=at(1))

    assert isinstance(result.playbook, PlaybookRead)
    assert result.playbook.stored is False
    assert len(playbook_rows(db_engine)) == 1


# Row: New default
def test_a_changed_default_gives_one_candidate_and_keeps_the_active_tuple(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    sync(units, snapshot())
    activations = activation_rows(db_engine)
    files, defaults = shipped()
    changed = tuple(
        dataclasses.replace(s, effort=Effort.HIGH) if s.route == "extract_people" else s
        for s in defaults
    )
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    result = sync(units, snapshot(), defaults=changed, clock=at(1))

    [(route, tuple_id)] = result.candidates
    assert route == "extract_people"
    assert activation_rows(db_engine) == activations
    warnings = [r for r in caplog.records if r.getMessage() == "active_tuple_not_default"]
    assert [w.route for w in warnings] == ["extract_people"]  # type: ignore[attr-defined]
    [people] = [found for found in route_tuples(units) if found.route == "extract_people"]
    assert people.active is not None and people.active.effort is Effort.MEDIUM
    assert [(c.id, c.effort, c.playbook) for c in people.candidates] == [
        (tuple_id, Effort.HIGH, None)
    ]


def test_a_default_that_starts_using_the_playbook_is_drift_from_the_active_tuple(
    units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    sync(units, snapshot())
    files, defaults = shipped()
    changed = tuple(
        dataclasses.replace(s, uses_playbook=True) if s.route == "extract_people" else s
        for s in defaults
    )
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    sync(units, snapshot(), defaults=changed, clock=at(1))

    warnings = [r for r in caplog.records if r.getMessage() == "active_tuple_not_default"]
    fields = ("route", "active_version", "default_version")
    assert [tuple(getattr(w, name) for name in fields) for w in warnings] == [
        ("extract_people", "v1", "v1")
    ]  # only the use of the Playbook drifted


def test_an_active_activation_without_a_tuple_row_gets_one(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    """A database from before Story 4.4 holds activations but no tuple rows."""
    sync(units, snapshot())
    query(db_engine, "DELETE FROM ai_route_tuples")
    files, defaults = shipped()
    changed = tuple(dataclasses.replace(s, effort=Effort.LOW) for s in defaults)

    result = sync(units, snapshot(), defaults=changed, clock=at(1))

    assert len(result.candidates) == 6  # each route's new default, then its active tuple
    assert sorted((row[0], row[2]) for row in tuple_rows(db_engine)) == sorted(
        [(route, "low") for route in ROUTES] + [(route, "medium") for route in ROUTES]
    )
    for found in route_tuples(units):
        assert [c.effort for c in found.candidates] == [Effort.LOW]  # the active one is hidden


def test_no_input_touches_no_table(units: UnitOfWorkFactory, db_engine: Engine) -> None:
    result = sync(units, snapshot(), files=(), defaults=())

    assert result == PromptSync((), (), (), (), (), None)
    assert (playbook_rows(db_engine), tuple_rows(db_engine), check_rows(db_engine)) == (
        [],
        [],
        [],
    )


def test_an_operator_check_records_its_own_actor_and_cause(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    result = sync(units, snapshot(), trigger=SyncTrigger.OPERATOR_CHECK)
    sync(units, PlaybookProblem.UNREADABLE, clock=at(1), trigger=SyncTrigger.OPERATOR_CHECK)

    assert len(result.candidates) == 3
    assert {row[3:6] for row in tuple_events(db_engine)} == {
        ("operator_check", None, "operator")
    }
    assert [row[1] for row in check_rows(db_engine)] == ["operator_check"] * 2
    found = status(units).last_check
    assert found is not None and found.trigger is SyncTrigger.OPERATOR_CHECK
    logged = [
        (r.getMessage(), r.trigger)  # type: ignore[attr-defined]
        for r in caplog.records
        if r.getMessage() in ("prompts_synced", "playbook_invalid")
    ]
    assert logged == [
        ("prompts_synced", "operator_check"),
        ("prompts_synced", "operator_check"),
        ("playbook_invalid", "operator_check"),
    ]


@pytest.mark.parametrize(
    ("playbook", "trigger", "message"),
    [
        (playbook_text(), SyncTrigger.STARTUP, "takes a PlaybookSnapshot or a PlaybookProblem"),
        ("no_version", SyncTrigger.STARTUP, "takes a PlaybookSnapshot or a PlaybookProblem"),
        (None, SyncTrigger.STARTUP, "takes a PlaybookSnapshot or a PlaybookProblem"),
        (snapshot(), "startup", "takes a SyncTrigger"),
    ],
)
def test_bad_playbook_inputs_are_refused_before_any_write(
    playbook: object, trigger: object, message: str, units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    with pytest.raises(TypeError, match=message) as raised:
        sync(units, playbook, trigger=trigger)  # type: ignore[arg-type]

    assert MARKER not in str(raised.value)
    assert query(db_engine, "SELECT COUNT(*) FROM prompt_snapshots") == [(0,)]
    assert check_rows(db_engine) == []


def test_refused_prompts_store_no_playbook_and_no_check(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    files, defaults = shipped()
    sync(units, snapshot())
    rewritten = tuple(
        PromptFile(f.route, f.version, f.text + "\nchanged") if f.route == "extract_people" else f
        for f in files
    )

    with pytest.raises(InvalidPrompts):
        sync(units, snapshot("0.3.0"), files=rewritten, clock=at(1))

    assert len(playbook_rows(db_engine)) == 1
    assert len(check_rows(db_engine)) == 1


# Row: Pinned Playbook


def raw_run(engine: Engine, route: str, playbook_sha256: str | None) -> str:
    """A passing test run of the stored tuple of ``route`` with that Playbook (Story 4.5: a
    tested activation names a stored run), inserting the tuple row when there is none."""
    found = query(
        engine,
        "SELECT id FROM ai_route_tuples WHERE route = ? AND playbook_sha256 IS ?",
        (route, playbook_sha256),
    )
    if found:
        [(tuple_id,)] = found
    else:
        [(sha,)] = query(
            engine, "SELECT sha256 FROM prompt_snapshots WHERE route = ? LIMIT 1", (route,)
        )
        tuple_id = new_id()
        query(
            engine,
            "INSERT INTO ai_route_tuples (id, route, model, effort, prompt_version, prompt_sha256, "
            "playbook_sha256, schema_version, created_at) VALUES (?, ?, 'claude-opus-5-5', "
            "'high', 'v1', ?, ?, 1, ?)",
            (tuple_id, route, sha, playbook_sha256, STAMP),
        )
    run_id = new_id()
    query(
        engine,
        "INSERT INTO ai_test_runs (id, tuple_id, test_set, outcome, error, case_count, "
        "passed_count, metrics, results, cost_micros, started_at, finished_at) VALUES "
        "(?, ?, 'drafting_smoke', 'passed', NULL, 10, 10, '[]', '[]', 0, ?, ?)",
        (run_id, tuple_id, STAMP, STAMP),
    )
    return run_id


def pin(engine: Engine, pinned: PlaybookSnapshot) -> str:
    """A tested activation of the probe route that names ``pinned``: what Story 4.5 writes."""
    activation_id = new_id()
    run_id = raw_run(engine, PROBE, pinned.sha256)
    query(
        engine,
        "INSERT INTO ai_route_activations (id, route, model, effort, prompt_version, "
        "prompt_sha256, playbook_sha256, schema_version, actor, bootstrap, test_run_id, "
        "activated_at, deactivated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'operator', 0, ?, ?, NULL)",
        (
            activation_id,
            PROBE,
            "claude-opus-5-5",
            "high",
            "v1",
            PROBE_FILE.sha256,
            pinned.sha256,
            run_id,
            STAMP,
        ),
    )
    return activation_id


def probe_request(units: UnitOfWorkFactory) -> dict[str, Any]:
    with units.open(read_only=True):
        found = playbook_queries.active_tuple(PROBE)
    assert found is not None
    block = UntrustedBlock("lead:01JD3QZ8X5V9K2M7N4P6R8T0WY", "page", "p1", "We are hiring.")
    return build_request(found, PROBE_CANDIDATE, [block], 4_000)


def test_the_pinned_playbook_is_sent_framed_and_survives_an_edit_of_the_live_file(
    units: UnitOfWorkFactory, db_engine: Engine, playbook_copy: Path
) -> None:
    playbook_copy.write_text(playbook_text(), encoding="utf-8")
    pinned = main.read_playbook_file(playbook_copy)
    assert isinstance(pinned, PlaybookSnapshot)
    sync(units, pinned, **with_probe())
    pin(db_engine, pinned)

    with units.open(read_only=True):
        found = playbook_queries.active_tuple(PROBE)
    request = probe_request(units)

    assert found is not None and found.playbook == pinned
    assert found.key.endswith(f":p{pinned.sha256[:12]}")
    assert request["system"] == [
        {"type": "text", "text": PROBE_FILE.text},
        {
            "type": "text",
            "text": playbook_section(pinned),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    framed = request["system"][1]["text"]
    assert framed.index(AUTHORITY_ORDER) < framed.index(MARKER)
    playbook_copy.write_text(playbook_text("0.9.0", "Edited live."), encoding="utf-8")
    result = sync(units, main.read_playbook_file(playbook_copy), clock=at(1), **with_probe())
    assert isinstance(result.playbook, PlaybookRead) and result.playbook.stored
    assert json.dumps(probe_request(units)) == json.dumps(request)  # byte-identical
    [probe] = [found for found in route_tuples(units) if found.route == PROBE]
    assert probe.active is not None
    assert probe.active.playbook == playbook_queries.PlaybookRef("0.2.0", pinned.sha256)
    assert (probe.active.bootstrap, probe.active.actor) == (False, "operator")
    assert [c.playbook.version for c in probe.candidates if c.playbook] == ["0.9.0"]


# Row: Tampered
def test_a_tampered_playbook_snapshot_is_refused(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    pinned = snapshot()
    sync(units, pinned, **with_probe())
    pin(db_engine, pinned)
    query(
        db_engine,
        "UPDATE playbook_snapshots SET content = ? WHERE sha256 = ?",
        (playbook_text(body="Tampered."), pinned.sha256),
    )

    with pytest.raises(ValueError, match=f"the stored Playbook of {PROBE}") as raised:
        with units.open(read_only=True):
            playbook_queries.active_tuple(PROBE)

    assert MARKER not in str(raised.value)
    assert "0.2.0" not in str(raised.value)


def test_a_missing_playbook_snapshot_is_refused(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    pinned = snapshot()
    sync(units, pinned, **with_probe())
    activation_id = pin(db_engine, pinned)
    other = snapshot("0.5.0")
    edit = sqlite3.connect(str(db_engine.url.database))  # foreign keys off: a hand edit
    try:
        edit.execute(
            "UPDATE ai_route_activations SET playbook_sha256 = ? WHERE id = ?",
            (other.sha256, activation_id),
        )
        edit.commit()
    finally:
        edit.close()

    with pytest.raises(ValueError, match=f"the active tuple of {PROBE} names a Playbook"):
        with units.open(read_only=True):
            playbook_queries.active_tuple(PROBE)


def test_the_extraction_tuples_carry_no_playbook(units: UnitOfWorkFactory) -> None:
    sync(units, snapshot())

    block = UntrustedBlock("lead:01JD3QZ8X5V9K2M7N4P6R8T0WY", "page", "p1", "Hiring.")
    for route in ROUTES:
        with units.open(read_only=True):
            found = playbook_queries.active_tuple(route)
        assert found is not None
        assert (found.playbook, found.playbook_sha256) == (None, None)
        assert ":p" not in found.key.removeprefix(found.model)
        request = build_request(found, CANDIDATES[route][0], [block], 1_000)
        assert request["system"] == [
            {"type": "text", "text": found.system_prompt, "cache_control": {"type": "ephemeral"}}
        ]


# The queries' empty states


def test_the_queries_on_an_empty_database(units: UnitOfWorkFactory) -> None:
    assert route_tuples(units) == ()
    assert status(units) == playbook_queries.PlaybookStatus(None, None)
    assert health(units) == ()


def only_tuple_id(units: UnitOfWorkFactory, route: str) -> str:
    """The id of the one tuple row of ``route``."""
    [(tuple_id,)] = query(units.engine, "SELECT id FROM ai_route_tuples WHERE route = ?", (route,))
    return str(tuple_id)


def test_route_tuples_shows_each_route_by_name_with_its_active_tuple(
    units: UnitOfWorkFactory,
) -> None:
    sync(units, snapshot(), **with_probe())

    found = route_tuples(units)

    assert [r.route for r in found] == [PROBE, *ROUTES]
    people = found[1]
    assert people.active == playbook_queries.ActiveTuple(
        model="claude-haiku-5-5",
        effort=Effort.MEDIUM,
        prompt_version="v1",
        schema_version=1,
        playbook=None,
        activated_at=DEFAULT_NOW,
        bootstrap=True,
        actor="seed",
        tuple_id=only_tuple_id(units, "extract_people"),
        ungated=True,  # Story 4.5: a bootstrap activation serves without a test run
        run=None,
    )
    assert people.candidates == ()  # its only tuple is the active one
    assert found[0].active is None and len(found[0].candidates) == 1


# The tables' rules


def raw_tuple(engine: Engine, **changes: object) -> None:
    [(sha,)] = query(
        engine,
        "SELECT sha256 FROM prompt_snapshots WHERE route = 'extract_people'",
    )
    values: dict[str, object] = {
        "id": new_id(),
        "route": "extract_people",
        "model": "claude-haiku-5-5",
        "effort": "high",
        "prompt_version": "v1",
        "prompt_sha256": sha,
        "playbook_sha256": None,
        "schema_version": 1,
        "created_at": STAMP,
    }
    values.update(changes)
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    query(
        engine,
        f"INSERT INTO ai_route_tuples ({columns}) VALUES ({marks})",  # noqa: S608
        tuple(values.values()),
    )


@pytest.fixture
def synced(units: UnitOfWorkFactory, db_engine: Engine) -> PlaybookSnapshot:
    stored = snapshot()
    sync(units, stored)
    return stored


def test_a_valid_raw_tuple_is_accepted_and_one_with_each_playbook_too(
    synced: PlaybookSnapshot, db_engine: Engine
) -> None:
    raw_tuple(db_engine)
    raw_tuple(db_engine, playbook_sha256=synced.sha256)  # the same fields with a Playbook

    assert len(tuple_rows(db_engine)) == 5


@pytest.mark.parametrize(
    "changes",
    [
        {"effort": "medium"},  # the shipped tuple again: uq_ai_route_tuples_no_playbook
        {"effort": "extreme"},
        {"schema_version": 0},
        {"prompt_version": "v2"},  # no such prompt snapshot
        {"playbook_sha256": "a" * 64},  # no such Playbook snapshot
        {"playbook_sha256": "A" * 64},
        {"playbook_sha256": "a" * 63},
        {"route": None},
    ],
)
def test_a_raw_tuple_that_breaks_a_rule_is_refused(
    synced: PlaybookSnapshot, db_engine: Engine, changes: dict[str, object]
) -> None:
    before = tuple_rows(db_engine)

    with pytest.raises(IntegrityError):
        raw_tuple(db_engine, **changes)

    assert tuple_rows(db_engine) == before


def test_a_repeated_playbook_tuple_is_refused(
    synced: PlaybookSnapshot, db_engine: Engine
) -> None:
    raw_tuple(db_engine, playbook_sha256=synced.sha256)

    with pytest.raises(IntegrityError):
        raw_tuple(db_engine, playbook_sha256=synced.sha256)  # uq_ai_route_tuples_playbook


@pytest.mark.parametrize(
    ("trigger", "playbook_sha256", "problem"),
    [
        ("startup", None, None),  # neither
        ("startup", "synced", "no_version"),  # both
        ("boot", "synced", None),
        ("startup", None, "missing"),
        ("startup", "b" * 64, None),  # no such snapshot
    ],
)
def test_a_raw_check_that_breaks_a_rule_is_refused(
    synced: PlaybookSnapshot,
    db_engine: Engine,
    trigger: str,
    playbook_sha256: str | None,
    problem: str | None,
) -> None:
    sha = synced.sha256 if playbook_sha256 == "synced" else playbook_sha256

    with pytest.raises(IntegrityError):
        query(
            db_engine,
            'INSERT INTO playbook_checks (id, checked_at, "trigger", playbook_sha256, problem) '
            "VALUES (?, ?, ?, ?, ?)",
            (new_id(), STAMP, trigger, sha, problem),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"sha256": "c" * 63},
        {"sha256": "C" * 64},
        {"version": ".1"},
        {"version": "a" * 33},
        {"version": ""},
        {"content": None},
        {"sha256": "synced"},  # unique
    ],
)
def test_a_raw_playbook_snapshot_that_breaks_a_rule_is_refused(
    synced: PlaybookSnapshot, db_engine: Engine, changes: dict[str, object]
) -> None:
    values: dict[str, object] = {
        "id": new_id(),
        "sha256": "c" * 64,
        "version": "1.0.0",
        "content": "text",
        "captured_at": STAMP,
    }
    values.update(changes)
    if values["sha256"] == "synced":
        values["sha256"] = synced.sha256

    with pytest.raises(IntegrityError):
        query(
            db_engine,
            "INSERT INTO playbook_snapshots (id, sha256, version, content, captured_at) "
            "VALUES (?, ?, ?, ?, ?)",
            tuple(values.values()),
        )


def test_a_raw_activation_with_a_bad_playbook_sha256_is_refused(
    synced: PlaybookSnapshot, db_engine: Engine
) -> None:
    for bad in ("a" * 64, "A" * 64, "x"):
        with pytest.raises(IntegrityError):
            query(
                db_engine,
                "UPDATE ai_route_activations SET playbook_sha256 = ? "
                "WHERE route = 'extract_people'",
                (bad,),
            )


# No Playbook text anywhere


def test_no_playbook_text_reaches_a_log_record_an_event_or_an_error(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    sync(units, snapshot(), **with_probe())
    sync(units, snapshot("0.3.0"), clock=at(1), **with_probe())
    sync(units, PlaybookProblem.BAD_VERSION, clock=at(2), **with_probe())

    assert MARKER not in caplog.text
    assert all(MARKER not in str(record.__dict__) for record in caplog.records)
    assert all(MARKER not in str(row) for row in query(db_engine, "SELECT * FROM events"))
    assert all(MARKER not in str(row) for row in query(db_engine, "SELECT * FROM playbook_checks"))
    assert all(MARKER not in str(row) for row in query(db_engine, "SELECT * FROM ai_route_tuples"))
    assert MARKER not in repr(route_tuples(units)) + repr(status(units)) + repr(health(units))
    assert MARKER not in repr(snapshot())


def test_the_check_time_is_the_sync_clock(units: UnitOfWorkFactory, db_engine: Engine) -> None:
    sync(units, snapshot(), clock=at(4))

    [(checked_at, *_)] = check_rows(db_engine)
    assert datetime.fromisoformat(checked_at.replace("Z", "+00:00")) == DEFAULT_NOW + timedelta(
        days=4
    )
