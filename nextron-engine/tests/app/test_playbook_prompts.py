"""``Playbook.sync_prompts`` and ``queries.playbook.active_tuple`` (FR-71, AD-8, AD-12, AD-18,
Story 2.8) on real units of work in a temporary database: every sync and active-tuple row of
the spec's I/O matrix, the tables' checks, foreign key and partial unique index, and that no
prompt text reaches a log record, an event or an error."""

import dataclasses
import logging
import shutil
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.adapters.ai import routes
from nextron.app.playbook.api import Playbook, PromptSync
from nextron.app.queries import playbook as playbook_queries
from nextron.db.eventlog import ACTOR_PATTERN
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.ai import Effort, RouteSpec, RouteTuple
from nextron.domain.prompts import InvalidPrompts, PromptFile
from tests.fakes.clock import DEFAULT_NOW, FrozenClock

ROUTES = ("extract_signals", "extract_people", "signal_inbox_parse")
STAMP = "2026-11-16T09:00:00.000000Z"
MARKER = "Pr0mptTextMarker"  # appears in a test prompt's text, never in a log or an error


def query(engine: Engine, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
    with engine.connect() as connection:
        result = connection.exec_driver_sql(statement, params)
        rows = result.all() if result.returns_rows else []
        connection.commit()
    return [tuple(row) for row in rows]


def snapshots(engine: Engine) -> list[tuple[Any, ...]]:
    return query(
        engine,
        "SELECT route, prompt_version, sha256, text, created_at FROM prompt_snapshots "
        "ORDER BY route, prompt_version",
    )


def activations(engine: Engine) -> list[tuple[Any, ...]]:
    return query(
        engine,
        "SELECT route, model, effort, prompt_version, prompt_sha256, schema_version, actor, "
        "bootstrap, test_run_id, activated_at, deactivated_at FROM ai_route_activations "
        "ORDER BY route, activated_at",
    )


def events(engine: Engine) -> list[tuple[Any, ...]]:
    return query(
        engine,
        "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
        "FROM events WHERE entity = 'ai_route_activations' ORDER BY at, entity_id",
    )


def shipped() -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
    return main.load_prompts()


def sync(
    units: UnitOfWorkFactory,
    files: Sequence[PromptFile],
    defaults: Sequence[RouteSpec],
    clock: FrozenClock | None = None,
) -> PromptSync:
    with units.open():
        return Playbook(clock or FrozenClock()).sync_prompts(files, defaults)


def active(units: UnitOfWorkFactory, route: str) -> RouteTuple | None:
    with units.open(read_only=True):
        return playbook_queries.active_tuple(route)


def v2(route: str = "extract_signals", text: str = f"Version two. {MARKER}") -> PromptFile:
    return PromptFile(route, "v2", text)


def moved(spec: RouteSpec, version: str = "v2") -> RouteSpec:
    return dataclasses.replace(spec, prompt_version=version)


# Row: First start
def test_a_first_start_stores_each_prompt_and_bootstrap_activates_each_default(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    files, defaults = shipped()
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    result = sync(units, files, defaults)

    pairs = tuple((route, "v1") for route in ROUTES)
    assert result == PromptSync(stored=pairs, unchanged=(), activated=pairs, kept=())
    assert snapshots(db_engine) == sorted(
        (f.route, f.version, f.sha256, f.text, STAMP) for f in files
    )
    hashes = {f.route: f.sha256 for f in files}
    assert activations(db_engine) == sorted(
        (s.route, s.model, s.effort.value, "v1", hashes[s.route], 1, "seed", 1, None, STAMP, None)
        for s in defaults
    )
    rows = events(db_engine)
    ids = {row[0] for row in query(db_engine, "SELECT id FROM ai_route_activations")}
    assert len(rows) == 3
    assert {row[1] for row in rows} == ids
    assert {row[:1] + row[2:] for row in rows} == {
        ("ai_route_activations", None, "active", "bootstrap", None, "seed", STAMP)
    }
    [record] = [r for r in caplog.records if r.getMessage() == "prompts_synced"]
    counts = ("stored_count", "unchanged_count", "activated_count", "kept_count")
    assert record.levelname == "INFO"
    assert [getattr(record, name) for name in counts] == [3, 0, 3, 0]


def not_default(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "active_tuple_not_default"]


# Row: Restart
def test_a_restart_with_the_same_files_writes_nothing(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    files, defaults = shipped()
    sync(units, files, defaults)
    before = (snapshots(db_engine), activations(db_engine), events(db_engine))
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    result = sync(units, files, defaults, FrozenClock(DEFAULT_NOW + timedelta(days=1)))

    pairs = tuple((route, "v1") for route in ROUTES)
    assert result == PromptSync(stored=(), unchanged=pairs, activated=(), kept=pairs)
    assert (snapshots(db_engine), activations(db_engine), events(db_engine)) == before
    assert not_default(caplog) == []  # every kept activation is its route's default


# Row: New version
def test_a_new_version_is_stored_and_the_activation_stays_on_v1(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    files, defaults = shipped()
    sync(units, files, defaults)
    before = activations(db_engine)

    result = sync(units, (*files, v2()), defaults)

    assert result.stored == (("extract_signals", "v2"),)
    assert ("extract_signals", "v2") not in result.unchanged
    assert activations(db_engine) == before
    assert [row[:2] for row in snapshots(db_engine)].count(("extract_signals", "v2")) == 1
    found = active(units, "extract_signals")
    assert found is not None and found.prompt_version == "v1"


# Row: Default moved
def test_a_moved_default_never_replaces_an_active_activation(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    files, defaults = shipped()
    sync(units, files, defaults)
    before = activations(db_engine)
    defaults_v2 = tuple(moved(s) if s.route == "extract_signals" else s for s in defaults)
    caplog.set_level(logging.DEBUG)

    result = sync(units, (*files, v2()), defaults_v2)

    assert result.activated == ()
    assert result.kept == tuple((route, "v1") for route in ROUTES)  # kept on v1
    assert activations(db_engine) == before
    assert len(events(db_engine)) == 3
    [warning] = not_default(caplog)  # the drift is logged, once, for that route only
    assert (warning.levelname, warning.name) == ("WARNING", "nextron.app.playbook.api")
    fields = ("route", "active_version", "default_version")
    assert [getattr(warning, name) for name in fields] == ["extract_signals", "v1", "v2"]
    assert MARKER not in caplog.text


def test_an_active_activation_that_differs_from_the_default_is_kept_as_it_is(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    files, defaults = shipped()
    sync(units, files, defaults)
    before = activations(db_engine)
    changed = tuple(dataclasses.replace(s, effort=Effort.HIGH) for s in defaults)
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    sync(units, files, changed)

    assert activations(db_engine) == before
    fields = ("route", "active_version", "default_version")
    found = [tuple(getattr(w, name) for name in fields) for w in not_default(caplog)]
    assert found == [(route, "v1", "v1") for route in ROUTES]  # the effort drifted


def test_a_route_with_no_activation_gets_its_default_on_a_later_start(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    files, defaults = shipped()
    sync(units, files, [s for s in defaults if s.route != "signal_inbox_parse"])

    result = sync(units, files, defaults)

    assert result.activated == (("signal_inbox_parse", "v1"),)
    assert result.kept == (("extract_signals", "v1"), ("extract_people", "v1"))
    assert len(activations(db_engine)) == 3


# Row: Rewritten version
def test_a_stored_version_with_another_hash_is_refused_and_nothing_is_written(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    files, defaults = shipped()
    sync(units, files, defaults)
    before = (snapshots(db_engine), activations(db_engine), events(db_engine))
    rewritten = tuple(
        PromptFile(f.route, f.version, f"{f.text}\n{MARKER}") if f.route == "extract_people" else f
        for f in files
    )

    with pytest.raises(InvalidPrompts) as raised:
        sync(units, (*rewritten, v2()), defaults)

    message = raised.value.message
    assert message == (
        "prompt extract_people v1 is stored with another SHA-256; a used prompt never "
        "changes: add a new version"
    )
    assert MARKER not in message
    assert (snapshots(db_engine), activations(db_engine), events(db_engine)) == before


# Row: Missing prompt
def test_a_default_without_its_prompt_file_is_refused_before_anything_is_written(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    files, defaults = shipped()
    defaults_v3 = tuple(moved(s, "v3") if s.route == "extract_people" else s for s in defaults)

    with pytest.raises(InvalidPrompts, match="route extract_people names prompt v3"):
        sync(units, files, defaults_v3)

    assert (snapshots(db_engine), activations(db_engine), events(db_engine)) == ([], [], [])


@pytest.mark.parametrize(
    ("files", "defaults", "error", "match"),
    [
        (["extract_signals/v1.md"], [], TypeError, "takes PromptFiles"),
        ([], [("extract_signals", "v1")], TypeError, "takes RouteSpecs"),
        ("twice", [], ValueError, "prompt version more than once: extract_signals v1"),
        ([], "twice", ValueError, "default route more than once: extract_people"),
    ],
)
def test_bad_inputs_are_refused_before_any_write(
    files: object,
    defaults: object,
    error: type[Exception],
    match: str,
    units: UnitOfWorkFactory,
    db_engine: Engine,
) -> None:
    shipped_files, shipped_defaults = shipped()
    if files == "twice":
        files = [*shipped_files, shipped_files[0]]
    if defaults == "twice":
        files = shipped_files
        defaults = [*shipped_defaults, shipped_defaults[1]]

    with pytest.raises(error, match=match):
        sync(units, files, defaults)  # type: ignore[arg-type]

    assert (snapshots(db_engine), activations(db_engine)) == ([], [])


# Row: Active tuple
def test_the_active_tuple_carries_the_snapshot_text_even_after_the_file_changes(
    units: UnitOfWorkFactory, db_engine: Engine, tmp_path: Path
) -> None:
    folder = tmp_path / "prompts"
    shutil.copytree(routes.PROMPTS_DIR, folder)
    files, defaults = main.load_prompts(folder)
    sync(units, files, defaults)
    [signals_file] = [f for f in files if f.route == "extract_signals"]
    (folder / "extract_signals" / "v1.md").write_text("changed on disk", encoding="utf-8")

    found = active(units, "extract_signals")

    assert found == RouteTuple(
        "extract_signals", "claude-haiku-5-5", Effort.MEDIUM, "v1", 1, signals_file.text
    )
    assert found.prompt_sha256 == signals_file.sha256
    assert found.actor == "ai:extract_signals@v1"
    assert ACTOR_PATTERN.fullmatch(found.actor)
    assert routes.find(found) is routes.default_candidate("extract_signals")
    [(stored,)] = query(
        db_engine,
        "SELECT prompt_sha256 FROM ai_route_activations WHERE route = 'extract_signals'",
    )
    assert found.prompt_sha256 == stored


def test_every_shipped_route_has_an_active_tuple_after_a_sync(units: UnitOfWorkFactory) -> None:
    sync(units, *shipped())

    for route in ROUTES:
        found = active(units, route)
        assert found is not None
        assert routes.find(found) is routes.default_candidate(route)


# Row: No tuple
def test_an_unknown_route_or_one_with_no_activation_has_no_tuple(
    units: UnitOfWorkFactory,
) -> None:
    assert active(units, "extract_signals") is None  # nothing synced yet
    files, defaults = shipped()
    sync(units, files, [s for s in defaults if s.route != "extract_people"])

    assert active(units, "extract_people") is None
    assert active(units, "classify_reply") is None
    with pytest.raises(TypeError):
        active(units, None)  # type: ignore[arg-type]


def test_a_tampered_snapshot_is_refused(units: UnitOfWorkFactory, db_engine: Engine) -> None:
    sync(units, *shipped())
    query(
        db_engine,
        "UPDATE prompt_snapshots SET text = ? WHERE route = 'extract_people'",
        (f"tampered {MARKER}",),
    )

    with pytest.raises(ValueError, match="the stored prompt of extract_people v1") as raised:
        active(units, "extract_people")

    assert MARKER not in str(raised.value)


# Rows: Two active, Untested, and the other checks


def raw_activation(engine: Engine, **changes: object) -> str:
    [(sha,)] = query(
        engine,
        "SELECT sha256 FROM prompt_snapshots WHERE route = 'extract_signals' "
        "AND prompt_version = 'v1'",
    )
    values: dict[str, object] = {
        "id": new_id(),
        "route": "extract_signals",
        "model": "claude-haiku-5-5",
        "effort": "medium",
        "prompt_version": "v1",
        "prompt_sha256": sha,
        "schema_version": 1,
        "actor": "operator",
        "bootstrap": 0,
        "test_run_id": new_id(),
        "activated_at": STAMP,
        "deactivated_at": STAMP,
    }
    values.update(changes)
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    query(
        engine,
        f"INSERT INTO ai_route_activations ({columns}) VALUES ({marks})",  # noqa: S608
        tuple(values.values()),
    )
    return str(values["id"])


@pytest.fixture
def synced(units: UnitOfWorkFactory, db_engine: Engine) -> Engine:
    sync(units, *shipped())
    return db_engine


def test_a_deactivated_tested_row_is_accepted(synced: Engine) -> None:
    raw_activation(synced)

    assert len(activations(synced)) == 4


def test_a_second_active_row_for_a_route_is_refused(synced: Engine) -> None:  # Row: Two active
    with pytest.raises(IntegrityError):
        raw_activation(synced, deactivated_at=None)

    raw_activation(synced)  # an inactive one beside the active one is fine
    raw_activation(synced)
    assert len(activations(synced)) == 5


@pytest.mark.parametrize(
    "changes",
    [
        {"bootstrap": 0, "test_run_id": None},  # Row: Untested
        {"bootstrap": 1},  # a bootstrap activation with a test run
        {"bootstrap": 2, "test_run_id": None},
        {"effort": "extreme"},
        {"effort": "MEDIUM"},
        {"schema_version": 0},
        {"schema_version": 10_000},
        {"prompt_version": "v2"},  # no such snapshot: the foreign key
        {"prompt_sha256": "0" * 64},
        {"route": None},
        {"actor": None},
        {"activated_at": None},
    ],
)
def test_a_raw_activation_that_breaks_a_rule_is_refused(
    synced: Engine, changes: dict[str, object]
) -> None:
    before = activations(synced)

    with pytest.raises(IntegrityError):
        raw_activation(synced, **changes)

    assert activations(synced) == before


def raw_snapshot(engine: Engine, **changes: object) -> None:
    values: dict[str, object] = {
        "id": new_id(),
        "route": "extract_signals",
        "prompt_version": "v9",
        "sha256": "a" * 64,
        "text": "text",
        "created_at": STAMP,
    }
    values.update(changes)
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    query(
        engine,
        f"INSERT INTO prompt_snapshots ({columns}) VALUES ({marks})",  # noqa: S608
        tuple(values.values()),
    )


def test_a_valid_raw_snapshot_is_accepted(synced: Engine) -> None:
    raw_snapshot(synced, prompt_version="v9999")

    assert len(snapshots(synced)) == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"prompt_version": "v1"},  # (route, prompt_version) is unique
        {"sha256": "A" * 64},
        {"sha256": "a" * 63},
        {"sha256": "g" * 64},
        {"prompt_version": "v0"},
        {"prompt_version": "v01"},
        {"prompt_version": "v10000"},
        {"prompt_version": "1"},
        {"prompt_version": "vx"},
        {"text": None},
    ],
)
def test_a_raw_snapshot_that_breaks_a_rule_is_refused(
    synced: Engine, changes: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        raw_snapshot(synced, **changes)

    assert len(snapshots(synced)) == 3


# No prompt text anywhere


def test_no_prompt_text_reaches_a_log_record_or_an_event(
    units: UnitOfWorkFactory, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    files = (PromptFile("extract_signals", "v1", f"System text. {MARKER}"),)
    spec = next(s for s in routes.default_specs() if s.route == "extract_signals")

    sync(units, files, (spec,))
    with pytest.raises(InvalidPrompts) as raised:
        sync(units, (PromptFile("extract_signals", "v1", f"Rewritten. {MARKER}"),), (spec,))

    assert MARKER not in caplog.text
    assert all(MARKER not in str(record.__dict__) for record in caplog.records)
    assert MARKER not in raised.value.message
    assert all(MARKER not in str(row) for row in query(db_engine, "SELECT * FROM events"))
    assert MARKER not in repr(files[0])
