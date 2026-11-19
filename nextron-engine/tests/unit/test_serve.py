"""``serve`` against the story's I/O matrix, with fake probes and a fake server runner; since
Story 1.16 also the ``pre_migrate`` backup rows (Pre-migrate, Pre-migrate fails, Migration
fails, No pre-migrate, and a record that fails while startup continues), through the
``tests/migration_chains.py`` helpers."""

import importlib
import io
import json
import re
import shutil
import signal
import sqlite3
import zoneinfo
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.domain.ai_test_gate import BOOTSTRAP_ROUTES
from nextron.app.approval.api import Approval
from nextron.app.compliance.erasure import Erasures
from nextron.app.drafting import api as drafting_api
from nextron.app.drafting.api import Drafting
from nextron.app.jobqueue import api as jobqueue_api
from nextron.app.notify import api as notify_api
from nextron.app.ops import api as ops_api
from nextron.app.pipeline.api import Pipeline
from nextron.app.playbook.api import Playbook
from nextron.app.research.api import Research
from nextron.app.sequencing.api import Sequencing
from nextron.app.setup import api as setup_api
from nextron.bootstrap import ENV_ERROR, BootstrapConfig, load_bootstrap
from nextron.db import migrations
from nextron.db.migrations import ensure_current, head_revision
from nextron.db.repos.ops import backups as backups_repo
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current, is_command
from nextron.domain.backup import BackupKind, BackupNotVerified
from nextron.domain.ai import RouteSpec
from nextron.domain.ai_test_cases import InvalidTestCases, TestCase
from nextron.domain.ai_test_gate import TestSet
from nextron.domain.notifications import (
    NOTIFICATIONS,
    ContactErased,
    Enrolled,
    FooterChanged,
    LeadsMerged,
    MessageSent,
    SubjectSuppressed,
)
from nextron.domain.playbook import (
    MAX_PLAYBOOK_BYTES,
    PlaybookProblem,
    PlaybookSnapshot,
    problem_text,
)
from nextron.domain.prompts import PromptFile
from nextron.lock import InstanceLock, LockHeldError
from nextron.startup_checks import Probes
from tests.asgi import get
from tests.fakes.clock import FrozenClock
from tests.fakes.probes import PortProbe, fake_probes
from tests.fakes.secrets import InMemorySecrets
from tests.migration_chains import (
    chain,
    create_table,
    engine_on,
    extend,
    run_sql,
    sha256,
    stored_revisions,
)

HEAD = head_revision()


def db_revision(db_file: Path) -> str | None:
    """The revision stored in ``db_file``, read outside the engine; ``None`` without one."""
    if not db_file.exists():
        return None
    try:
        [revision] = stored_revisions(db_file)
    except sqlite3.Error:
        return None
    return revision


class ServerSpy:
    """Stands in for Uvicorn and records what the engine looked like when it started."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
        probe = InstanceLock(config.data_dir / "nextron.lock")
        try:
            probe.acquire()
        except LockHeldError:
            lock_held = True
        else:
            probe.release()
            lock_held = False
        self.calls.append(
            {
                "config": config,
                "reload": reload,
                "units": units,
                "data_dir_exists": config.data_dir.is_dir(),
                "logs_dir_exists": (config.data_dir / "logs").is_dir(),
                "lock_held": lock_held,
                "db_revision": db_revision(config.data_dir / "nextron.db"),
            }
        )


def run_serve(
    *,
    probes: Probes | None = None,
    reload: bool = False,
    load_prompts: main.PromptLoader = main.load_prompts,
    read_playbook: main.PlaybookReader = main.read_playbook_file,
    load_test_cases: main.TestCaseLoader = main.load_test_cases,
) -> tuple[int, str, ServerSpy]:
    spy = ServerSpy()
    err = io.StringIO()
    code = main.serve(
        reload=reload,
        probes=probes if probes is not None else fake_probes(),
        run_server=spy,
        err=err,
        load_prompts=load_prompts,
        read_playbook=read_playbook,
        load_test_cases=load_test_cases,
    )
    return code, err.getvalue(), spy


def no_prompts() -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
    """A loader with nothing to sync, for a toy migration chain that has no playbook tables:
    the sync then reads and writes no table."""
    return (), ()


def no_cases() -> tuple[TestCase, ...]:
    """A case loader with nothing to sync (Story 4.5), for the same toy chains."""
    return ()


@pytest.fixture
def dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``NEXTRON_ENV=dev`` with a temporary data dir and a copy of the Sales Playbook
    (``NEXTRON_PLAYBOOK_PATH``, so no test touches the real one); returns the data dir."""
    data_dir = tmp_path / "data" / "dev"
    playbook = tmp_path / "playbook" / "sales-playbook.md"
    playbook.parent.mkdir()
    shutil.copyfile(load_bootstrap("dev").playbook_path, playbook)
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXTRON_PLAYBOOK_PATH", str(playbook))
    return data_dir


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> list[InMemorySecrets]:
    """``main.KeyringSecrets`` builds in-memory secrets (the live keyring is refused in tests);
    returns every one built."""
    built: list[InMemorySecrets] = []

    def keyring_secrets(env: str) -> InMemorySecrets:
        built.append(InMemorySecrets(env))
        return built[-1]

    monkeypatch.setattr(main, "KeyringSecrets", keyring_secrets)
    return built


def tree(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*"))


def main_log(data_dir: Path) -> list[dict[str, Any]]:
    """The ``nextron.main`` lines of the log (Alembic logs a migration's steps too)."""
    text = (data_dir / "logs" / "nextron.log").read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    return [line for line in lines if line["logger"] == "nextron.main"]


def events(data_dir: Path) -> list[str]:
    return [line["event"] for line in main_log(data_dir)]


# Row: env unset or invalid
@pytest.mark.parametrize("value", [None, "staging"])
def test_env_unset_or_invalid_exits_2_and_creates_nothing(
    value: str | None, local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if value is None:
        monkeypatch.delenv("NEXTRON_ENV")
    else:
        monkeypatch.setenv("NEXTRON_ENV", value)

    code, err, spy = run_serve()

    assert code == 2
    assert err.strip() == ENV_ERROR
    assert spy.calls == []
    assert tree(local_app_data) == []


# Row: happy dev start
def test_dev_start_prepares_folders_takes_the_lock_and_serves(
    local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")  # default data dir under %LOCALAPPDATA%

    code, err, spy = run_serve()

    assert (code, err) == (0, "")
    [call] = spy.calls
    data_dir = local_app_data / "Nextron" / "dev"
    assert call["config"].data_dir == data_dir
    assert call["config"].port == 8766
    assert call["reload"] is False
    assert call["data_dir_exists"] and call["logs_dir_exists"]
    assert call["lock_held"]
    with InstanceLock(data_dir / "nextron.lock"):  # released once the server stops
        pass


def test_uvicorn_binds_127_0_0_1_with_one_worker(
    dev: Path, fake_keyring: list[InMemorySecrets], monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[tuple[object, dict[str, Any]]] = []
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: runs.append((app, kwargs)))

    assert main.serve(probes=fake_probes(), err=io.StringIO()) == 0

    [(app, kwargs)] = runs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8766
    assert kwargs["workers"] == 1
    assert kwargs.get("reload", False) is False
    assert kwargs["log_config"] is None  # Uvicorn's records go through the root handlers
    assert callable(app)
    assert [secrets.service for secrets in fake_keyring] == ["nextron-engine/dev"]


def test_reload_is_allowed_in_dev(
    dev: Path, fake_keyring: list[InMemorySecrets], monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[tuple[object, dict[str, Any]]] = []
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: runs.append((app, kwargs)))

    assert main.serve(reload=True, probes=fake_probes(), err=io.StringIO()) == 0

    [(app, kwargs)] = runs
    assert kwargs["factory"] is True
    assert kwargs["reload"] is True
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8766
    assert kwargs["workers"] == 1
    assert kwargs["log_config"] is None
    module_name, _, attribute = str(app).partition(":")
    factory = getattr(importlib.import_module(module_name), attribute)
    connected: list[UnitOfWorkFactory] = []
    real_connect = main.connect_database

    def connect(config: BootstrapConfig) -> UnitOfWorkFactory:
        connected.append(real_connect(config))
        return connected[-1]

    def never_migrate(*args: object, **kwargs: object) -> None:
        raise AssertionError("the reload worker must not migrate")

    monkeypatch.setattr(main, "connect_database", connect)
    monkeypatch.setattr(main, "ensure_current", never_migrate)
    # NEXTRON_ENV=dev, as in the reload worker: the dev port's Host passes the guard, and with
    # no session (and no keyring call) /health answers 401. The worker builds its own keyring
    # adapter for dev (in-memory here: tests never build the live one).
    try:
        status, headers, _ = get(factory(), "/health")
        assert [secrets.service for secrets in fake_keyring] == ["nextron-engine/dev"]
        # only Story 8.2's Places key presence check (main.build_places; value discarded)
        assert fake_keyring[0].calls == [("read", "google_places/api_key")]
        [units] = connected  # the worker reads through its own factory on the data dir's file
        assert units.engine.url.database == str(dev / "nextron.db")
    finally:
        for units in connected:
            units.engine.dispose()
    assert status == 401
    assert headers[b"x-frame-options"] == b"DENY"


def test_connect_database_connects_lazily_and_never_migrates(
    dev: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never_migrate(*args: object, **kwargs: object) -> None:
        raise AssertionError("connect_database must not migrate")

    monkeypatch.setattr(main, "ensure_current", never_migrate)
    dev.mkdir(parents=True)

    units = main.connect_database(load_bootstrap())
    try:
        assert units.engine.url.database == str(dev / "nextron.db")
        assert not (dev / "nextron.db").exists()  # nothing connected yet
        with units.open(read_only=True) as uow:  # a GET that never reads
            assert uow.read_only
        assert not (dev / "nextron.db").exists()
    finally:
        units.engine.dispose()


@pytest.mark.parametrize("env", ["test", "prod"])
def test_reload_is_refused_outside_dev(
    env: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", env)
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))

    code, err, spy = run_serve(reload=True)

    assert code == 2
    assert "--reload" in err
    assert spy.calls == []
    assert not (tmp_path / "data").exists()


# Row: second instance
def test_second_instance_exits_3_before_binding_a_port(dev: Path) -> None:
    dev.mkdir(parents=True)
    port = PortProbe(free=False)  # the first instance holds the port too

    with InstanceLock(dev / "nextron.lock"):
        code, err, spy = run_serve(probes=fake_probes(port=port))

    assert code == 3
    assert err.strip() == "another Nextron instance is running for dev"
    assert port.calls == []
    assert spy.calls == []
    assert not (dev / "nextron.db").exists()  # the database is never opened


# Rows: old SQLite, no tz data, OneDrive path
def _missing_zone(name: str) -> object:
    raise zoneinfo.ZoneInfoNotFoundError(name)


@pytest.mark.parametrize(
    ("probes", "check"),
    [
        (fake_probes(sqlite_version="3.51.2"), "sqlite_version"),
        (fake_probes(load_zone=_missing_zone), "tzdata"),
    ],
)
def test_failed_environment_check_exits_2_naming_the_check(
    probes: Probes, check: str, dev: Path
) -> None:
    code, err, spy = run_serve(probes=probes)

    assert code == 2
    assert f"startup check failed: {check}:" in err
    assert spy.calls == []
    assert not dev.exists()


@pytest.mark.parametrize("variable", ["OneDrive", "OneDriveCommercial", "OneDriveConsumer"])
def test_data_dir_under_onedrive_variable_exits_2(
    variable: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    onedrive = tmp_path / "Sync Root"
    data_dir = onedrive / "Nextron" / "dev"
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(data_dir))

    code, err, spy = run_serve(probes=fake_probes(environ={variable: str(onedrive)}))

    assert code == 2
    assert "startup check failed: data_dir_outside_onedrive:" in err
    assert spy.calls == []
    assert not data_dir.exists()


def test_data_dir_with_a_onedrive_segment_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "OneDrive - Nextron AI" / "dev"
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(data_dir))

    code, err, spy = run_serve()

    assert code == 2
    assert "data_dir_outside_onedrive" in err
    assert not data_dir.exists()


# Row: port busy
def test_busy_port_exits_2_and_releases_the_lock(dev: Path) -> None:
    port = PortProbe(free=False)

    code, err, spy = run_serve(probes=fake_probes(port=port))

    assert code == 2
    assert "startup check failed: port_free: 127.0.0.1:8766" in err
    assert port.calls == [("127.0.0.1", 8766)]
    assert spy.calls == []
    assert not (dev / "nextron.db").exists()  # the database is never opened
    with InstanceLock(dev / "nextron.lock"):
        pass


def test_uvicorn_startup_failure_exits_2_not_3(dev: Path) -> None:
    def fail_to_start(config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
        raise SystemExit(3)  # Uvicorn's STARTUP_FAILURE, e.g. the bind failed

    err = io.StringIO()
    code = main.serve(probes=fake_probes(), run_server=fail_to_start, err=err)

    assert code == 2
    assert "server failed to start" in err.getvalue()
    with InstanceLock(dev / "nextron.lock"):
        pass


def test_data_dir_that_is_a_file_exits_2_naming_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_folder = tmp_path / "data-file"
    not_a_folder.write_text("")
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(not_a_folder))

    code, err, spy = run_serve()

    assert code == 2
    assert str(not_a_folder) in err
    assert len(err.strip().splitlines()) == 1
    assert spy.calls == []


def test_lock_is_released_when_the_server_crashes(dev: Path) -> None:
    def crash(config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        main.serve(probes=fake_probes(), run_server=crash, err=io.StringIO())

    with InstanceLock(dev / "nextron.lock"):
        pass


# Row: Stop (Story 1.21): run.ps1 stops the engine with CTRL_BREAK
@pytest.mark.skipif(not hasattr(signal, "SIGBREAK"), reason="CTRL_BREAK is Windows only")
@pytest.mark.parametrize("how", ["called", "raised"])
def test_a_ctrl_break_while_the_server_runs_stops_it_cleanly_and_serve_exits_0(
    dev: Path, how: str
) -> None:
    sigbreak = signal.SIGBREAK  # type: ignore[attr-defined]
    before = signal.getsignal(sigbreak)
    seen: list[object] = []

    def server(config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
        handler = signal.getsignal(sigbreak)
        seen.append(handler)
        # Uvicorn saves this handler and, after its graceful shutdown, raises the signal again to
        # it; a CTRL_BREAK before Uvicorn takes the signal over reaches it directly. It must be a
        # Python handler before anything raises the signal, or the raise would end this process.
        if not callable(handler) or handler is before:
            pytest.fail(f"no Python SIGBREAK handler installed: {handler!r}")
        if how == "called":
            with pytest.raises(SystemExit) as stopped:
                handler(sigbreak, None)
            assert stopped.value.code == 0
            raise stopped.value
        signal.raise_signal(sigbreak)  # the handler raises SystemExit(0) here
        pytest.fail("the SIGBREAK handler returned")

    err = io.StringIO()
    code = main.serve(probes=fake_probes(), run_server=server, err=err)

    assert (code, err.getvalue()) == (0, "")
    assert len(seen) == 1
    assert signal.getsignal(sigbreak) is before  # the previous handler is back
    [stopped] = [line for line in main_log(dev) if line["event"] == "engine_stopped"]
    assert stopped["exit_code"] == 0


def test_the_ctrl_break_handler_is_back_after_a_server_crash(dev: Path) -> None:
    sigbreak = getattr(signal, "SIGBREAK", signal.SIGINT)
    before = signal.getsignal(sigbreak)

    def crash(config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        main.serve(probes=fake_probes(), run_server=crash, err=io.StringIO())

    assert signal.getsignal(sigbreak) is before


# The database step --------------------------------------------------------------------------


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[UnitOfWorkFactory]:
    """Every unit-of-work factory ``serve`` opens."""
    factories: list[UnitOfWorkFactory] = []
    real = main.open_database

    def spy(config: BootstrapConfig, **kwargs: Any) -> tuple[UnitOfWorkFactory, str]:
        units, revision = real(config, **kwargs)
        factories.append(units)
        return units, revision

    monkeypatch.setattr(main, "open_database", spy)
    return factories


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> list[Engine]:
    """Every database engine ``serve`` creates, whether or not the database step succeeds."""
    created: list[Engine] = []
    real = main.create_engine

    def spy(db_path: Path) -> Engine:
        created.append(real(db_path))
        return created[-1]

    monkeypatch.setattr(main, "create_engine", spy)
    return created


def assert_disposed(engine: Engine, db_file: Path) -> None:
    assert engine.pool.checkedin() == 0  # disposed: a fresh, empty pool
    db_file.unlink()  # nothing holds the file open


def db_at_head(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True)
    db_file = data_dir / "nextron.db"
    with engine_on(db_file) as engine:
        ensure_current(engine)
    return db_file


# Row: fresh data dir
def test_a_fresh_data_dir_gets_a_database_at_head_before_the_server_starts(dev: Path) -> None:
    code, err, spy = run_serve()

    assert (code, err) == (0, "")
    [call] = spy.calls
    assert call["db_revision"] == HEAD
    lines = main_log(dev)
    assert [line["event"] for line in lines] == [
        "database_migrated",
        "engine_started",
        "engine_stopped",
    ]
    assert (lines[0]["from_revision"], lines[0]["to_revision"]) == (None, HEAD)
    assert lines[1]["db_revision"] == HEAD


def test_a_database_at_head_is_not_migrated_again(dev: Path) -> None:
    assert run_serve()[0] == 0
    assert run_serve()[0] == 0

    assert events(dev).count("database_migrated") == 1
    assert events(dev).count("engine_started") == 2


# Row: DB behind
def test_a_database_behind_is_upgraded_before_the_server_starts(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))
    dev.mkdir(parents=True)
    with engine_on(dev / "nextron.db") as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_b", create_table("b")))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)

    code, err, spy = run_serve(load_prompts=no_prompts, load_test_cases=no_cases)

    assert (code, err) == (0, "")
    assert spy.calls[0]["db_revision"] == "0002_b"
    [migrated] = [line for line in main_log(dev) if line["event"] == "database_migrated"]
    assert (migrated["from_revision"], migrated["to_revision"]) == ("0001_a", "0002_b")
    [started] = [line for line in main_log(dev) if line["event"] == "engine_started"]
    assert started["db_revision"] == "0002_b"


# Rows: DB ahead, foreign DB
@pytest.mark.parametrize(
    ("statements", "message"),
    [
        (
            ["UPDATE alembic_version SET version_num = '0099_future'"],
            "database revision 0099_future is newer than this code",
        ),
        (
            ["INSERT INTO alembic_version (version_num) VALUES ('0099_fork')"],
            f"the database has 2 Alembic revisions ({HEAD}, 0099_fork); expected one",
        ),
        (
            ["DROP TABLE alembic_version"],
            "the database has tables but no Alembic revision; it was not made by this engine",
        ),
    ],
)
def test_a_refused_database_exits_2_and_changes_nothing(
    statements: list[str], message: str, dev: Path
) -> None:
    db_file = db_at_head(dev)
    run_sql(db_file, *statements)
    before = sha256(db_file)

    code, err, spy = run_serve()

    assert code == 2
    assert err.strip() == f"startup check failed: database_revision: {message}"
    assert spy.calls == []
    assert sha256(db_file) == before
    [failed] = main_log(dev)
    assert (failed["event"], failed["level"], failed["check"]) == (
        "startup_check_failed",
        "ERROR",
        "database_revision",
    )
    with InstanceLock(dev / "nextron.lock"):
        pass


def test_a_foreign_rollback_journal_database_exits_2_and_changes_nothing(dev: Path) -> None:
    dev.mkdir(parents=True)
    db_file = dev / "nextron.db"
    run_sql(db_file, "CREATE TABLE contacts (id INTEGER PRIMARY KEY, email TEXT)")
    before = sha256(db_file)

    code, err, spy = run_serve()

    assert code == 2
    assert err.strip() == (
        "startup check failed: database_revision: the database file uses journal_mode delete, "
        "not wal; it is not this engine's database"
    )
    assert spy.calls == []
    assert sha256(db_file) == before
    [failed] = main_log(dev)
    assert (failed["event"], failed["check"]) == ("startup_check_failed", "database_revision")


def test_a_file_that_is_not_sqlite_exits_2(dev: Path) -> None:
    dev.mkdir(parents=True)
    db_file = dev / "nextron.db"
    db_file.write_bytes(b"not a database, just some text that is long enough " * 4)
    before = sha256(db_file)

    code, err, spy = run_serve()

    assert code == 2
    assert err.startswith(
        "database startup failed: it could not be opened, or a migration failed and was "
        "rolled back; the traceback is in "
    )
    assert spy.calls == []
    assert sha256(db_file) == before
    [failed] = main_log(dev)
    assert (failed["event"], failed["level"]) == ("database_migration_failed", "ERROR")
    assert "file is not a database" in failed["exc"]


# Row: migration fails (a migration that raises, or one that leaves foreign key violations)
PARENT_AND_CHILD = """
op.create_table('parent', sa.Column('id', sa.Integer, primary_key=True))
op.create_table(
    'child',
    sa.Column('id', sa.Integer, primary_key=True),
    sa.Column('parent_id', sa.Integer, sa.ForeignKey('parent.id'), nullable=False),
)
"""


@pytest.mark.parametrize(
    ("body", "cause"),
    [
        ("raise RuntimeError('the migration broke')", "RuntimeError: the migration broke"),
        (
            "op.execute('INSERT INTO child (id, parent_id) VALUES (1, 42)')",
            "ForeignKeyCheckError: the upgrade left 1 foreign key violations in child",
        ),
    ],
)
def test_a_failed_migration_exits_2_and_logs_the_traceback(
    body: str, cause: str, dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    location = chain(tmp_path, ("0001_a", PARENT_AND_CHILD))
    dev.mkdir(parents=True)
    with engine_on(dev / "nextron.db") as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_fails", body))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)

    code, err, spy = run_serve()

    assert code == 2
    assert "database startup failed: it could not be opened, or a migration failed" in err
    assert spy.calls == []
    assert stored_revisions(dev / "nextron.db") == ["0001_a"]
    [failed] = main_log(dev)
    assert (failed["event"], failed["level"]) == ("database_migration_failed", "ERROR")
    assert cause in failed["exc"]
    with InstanceLock(dev / "nextron.lock"):
        pass


@pytest.mark.parametrize("outcome", ["stops", "crashes"])
def test_the_database_engine_is_disposed_when_serve_returns(
    outcome: str, dev: Path, opened: list[UnitOfWorkFactory]
) -> None:
    def server(config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
        assert units is opened[0]  # the server reads through the factory serve opened
        assert opened[0].engine.pool.checkedin() == 1  # the pool holds a connection
        if outcome == "crashes":
            raise RuntimeError("boom")

    if outcome == "crashes":
        with pytest.raises(RuntimeError):
            main.serve(probes=fake_probes(), run_server=server, err=io.StringIO())
    else:
        assert main.serve(probes=fake_probes(), run_server=server, err=io.StringIO()) == 0

    [units] = opened
    assert_disposed(units.engine, dev / "nextron.db")


def test_the_served_app_reads_through_the_factory_serve_opened(
    dev: Path,
    opened: list[UnitOfWorkFactory],
    fake_keyring: list[InMemorySecrets],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[object] = []
    real_build_app = main.build_app

    def spy(config: BootstrapConfig, **kwargs: Any) -> object:
        built.append(kwargs["units"])
        return real_build_app(config, **kwargs)

    monkeypatch.setattr(main, "build_app", spy)
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: None)

    assert main.serve(probes=fake_probes(), err=io.StringIO()) == 0

    [units] = opened
    assert built == [units]


def test_the_database_engine_is_disposed_when_the_database_is_refused(
    dev: Path, engines: list[Engine]
) -> None:
    run_sql(db_at_head(dev), "UPDATE alembic_version SET version_num = '0099_future'")

    assert run_serve()[0] == 2

    [engine] = engines
    assert_disposed(engine, dev / "nextron.db")


def test_the_database_engine_is_disposed_when_a_migration_fails(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engines: list[Engine]
) -> None:
    location = chain(tmp_path, ("0001_fails", "raise RuntimeError('the migration broke')"))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)

    assert run_serve()[0] == 2

    [engine] = engines
    assert_disposed(engine, dev / "nextron.db")


def test_build_subscribers_registers_reparent_lead_close_suppressed_and_erase_pii_only() -> None:
    subscribers = main.build_subscribers()

    assert subscribers.frozen
    found = subscribers.subscribers_of(LeadsMerged)
    assert all(is_command(subscriber) for subscriber in found)
    assert [subscriber.__name__ for subscriber in found] == ["reparent_lead"] * 3
    owners = [type(getattr(subscriber, "__self__", None)) for subscriber in found]
    assert owners == [Pipeline, Research, Sequencing]  # pipeline, research, then sequencing
    suppressed = subscribers.subscribers_of(SubjectSuppressed)  # Stories 4.8, 4.9 and 3.3
    assert all(is_command(subscriber) for subscriber in suppressed)
    assert [subscriber.__name__ for subscriber in suppressed] == [
        "stop_suppressed",
        "cancel_pending_suppressed",
        "close_suppressed",
    ]
    owners = [type(getattr(subscriber, "__self__", None)) for subscriber in suppressed]
    assert owners == [Sequencing, Approval, Pipeline]
    erased = subscribers.subscribers_of(ContactErased)  # Story 3.13
    assert all(is_command(subscriber) for subscriber in erased)
    assert {subscriber.__name__ for subscriber in erased} == {"erase_pii"}
    compliance, research, playbook, sequencing, *rest = erased
    assert type(getattr(compliance, "__self__", None)) is Erasures  # compliance first
    assert type(getattr(research, "__self__", None)) is Research  # then research
    assert type(getattr(playbook, "__self__", None)) is Playbook  # Story 4.6's override cases
    assert type(getattr(sequencing, "__self__", None)) is Sequencing  # Story 4.8, then no-ops
    assert rest == [
        setup_api.erase_pii,
        drafting_api.erase_pii,
        jobqueue_api.erase_pii,
        ops_api.erase_pii,
        notify_api.erase_pii,
    ]
    [enrolled] = subscribers.subscribers_of(Enrolled)  # Story 4.8
    assert is_command(enrolled)
    assert enrolled.__name__ == "on_enrolled"
    assert type(getattr(enrolled, "__self__", None)) is Pipeline
    [sent] = subscribers.subscribers_of(MessageSent)  # Story 4.9
    assert is_command(sent)
    assert sent.__name__ == "on_step_sent"
    assert type(getattr(sent, "__self__", None)) is Sequencing
    [footer] = subscribers.subscribers_of(FooterChanged)  # Story 4.12
    assert is_command(footer)
    assert footer.__name__ == "recompose_ready"
    assert type(getattr(footer, "__self__", None)) is Drafting
    registered = (
        LeadsMerged,
        SubjectSuppressed,
        ContactErased,
        Enrolled,
        MessageSent,
        FooterChanged,
    )
    others = [kind for kind in NOTIFICATIONS if kind not in registered]
    assert all(subscribers.subscribers_of(kind) == () for kind in others)


def test_build_subscribers_gives_its_services_the_clock() -> None:
    clock = FrozenClock()

    subscribers = main.build_subscribers(clock=clock)
    found = [
        *subscribers.subscribers_of(LeadsMerged),
        *subscribers.subscribers_of(SubjectSuppressed),
        *subscribers.subscribers_of(ContactErased)[:4],  # the rest are module functions
        *subscribers.subscribers_of(Enrolled),
        *subscribers.subscribers_of(MessageSent),
        *subscribers.subscribers_of(FooterChanged),
    ]
    clocks = [getattr(subscriber, "__self__")._clock for subscriber in found]

    assert clocks == [clock] * 13
    erasures = getattr(subscribers.subscribers_of(ContactErased)[0], "__self__")
    assert erasures._compliance._clock is clock


def test_cli_dispatches_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron import __main__ as cli

    seen: list[bool] = []
    monkeypatch.setattr(cli.engine, "serve", lambda *, reload: seen.append(reload) or 0)

    assert cli.main(["serve", "--reload"]) == 0
    assert seen == [True]


# The pre_migrate backup (Story 1.16) ------------------------------------------------------------


def local_copies(local_app_data: Path) -> list[Path]:
    """The copies in ``dev``'s default ``<backup dir>\\local``."""
    folder = local_app_data / "Nextron" / "backups" / "dev" / "local"
    return sorted(folder.iterdir()) if folder.exists() else []


def copy_revision(copy: Path) -> str:
    """The Alembic revision of a copy, read without changing it."""
    reader = sqlite3.connect(f"{copy.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        [(revision,)] = reader.execute("SELECT version_num FROM alembic_version").fetchall()
        return str(revision)
    finally:
        reader.close()


def backup_rows(db_file: Path) -> list[tuple[str, str, str, str | None, str | None]]:
    reader = sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)
    try:
        return reader.execute(
            "SELECT kind, status, path, db_revision, sha256 FROM backups"
        ).fetchall()
    finally:
        reader.close()


def real_chain_plus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dev: Path) -> Path:
    """The real chain, a database at its head, then one more revision: the database is
    behind the code by one revision that the real ``backups`` table predates."""
    location = chain(tmp_path)
    for revision in (migrations.SCRIPT_LOCATION / "versions").glob("*.py"):
        shutil.copy2(revision, location / "versions" / revision.name)
    dev.mkdir(parents=True)
    with engine_on(dev / "nextron.db") as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0046_more", create_table("more")))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)
    return dev / "nextron.db"


# Row: Pre-migrate
def test_a_database_behind_is_copied_before_the_upgrade_and_the_copy_recorded(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_app_data: Path
) -> None:
    db_file = real_chain_plus(tmp_path, monkeypatch, dev)

    code, err, spy = run_serve()

    assert (code, err) == (0, "")
    assert spy.calls[0]["db_revision"] == "0046_more"
    [copy] = local_copies(local_app_data)
    assert re.fullmatch(r"nextron-dev-\d{8}T\d{6}\.\d{3}Z\.db", copy.name)
    assert copy_revision(copy) == HEAD  # taken before the upgrade
    assert backup_rows(db_file) == [("pre_migrate", "verified", str(copy), HEAD, sha256(copy))]
    assert events(dev) == ["database_migrated", "engine_started", "engine_stopped"]
    assert not [line for line in main_log(dev) if "backup" in line["event"]]



def test_a_failed_rotation_after_the_pre_migrate_copy_is_no_record_failure(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_app_data: Path
) -> None:
    db_file = real_chain_plus(tmp_path, monkeypatch, dev)

    def locked(*args: object, **kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backups_repo, "rotation_candidates", locked)

    code, err, spy = run_serve()

    assert (code, err) == (0, "")
    [copy] = local_copies(local_app_data)
    assert backup_rows(db_file) == [("pre_migrate", "verified", str(copy), HEAD, sha256(copy))]
    assert events(dev) == ["database_migrated", "engine_started", "engine_stopped"]
    text = (dev / "logs" / "nextron.log").read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    [failed] = [line for line in lines if line["event"] == "backup_rotation_failed"]
    assert (failed["logger"], failed["level"], failed["code"]) == (
        "nextron.app.ops.api",
        "ERROR",
        "OperationalError",
    )


# Row: Pre-migrate fails
def test_a_failed_pre_migrate_backup_exits_2_and_migrates_nothing(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_app_data: Path
) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))
    dev.mkdir(parents=True)
    with engine_on(dev / "nextron.db") as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_b", create_table("b")))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)
    before = sha256(dev / "nextron.db")

    def broken_snapshot(db_path: Path, target: Path) -> object:
        raise BackupNotVerified("integrity_check: database disk image is malformed")

    monkeypatch.setattr(main, "snapshot", broken_snapshot)

    code, err, spy = run_serve()

    assert code == 2
    assert err.strip() == (
        "startup check failed: pre_migrate_backup: backup_not_verified: integrity_check: "
        "database disk image is malformed; the database was not migrated"
    )
    assert spy.calls == []
    assert stored_revisions(dev / "nextron.db") == ["0001_a"]
    assert sha256(dev / "nextron.db") == before
    assert local_copies(local_app_data) == []
    [failed] = main_log(dev)
    assert (failed["event"], failed["level"], failed["code"]) == (
        "pre_migrate_backup_failed",
        "ERROR",
        "backup_not_verified",
    )
    with InstanceLock(dev / "nextron.lock"):
        pass


def test_a_pre_migrate_copy_that_cannot_be_made_exits_2(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_app_data: Path
) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))
    dev.mkdir(parents=True)
    with engine_on(dev / "nextron.db") as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_b", create_table("b")))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)
    backups = local_app_data / "Nextron" / "backups"
    backups.mkdir(parents=True)
    (backups / "dev").write_bytes(b"a file where the backup dir should be")

    code, err, spy = run_serve()

    assert code == 2
    assert err.startswith("startup check failed: pre_migrate_backup: ")
    assert err.strip().endswith("; the database was not migrated")
    assert spy.calls == []
    assert stored_revisions(dev / "nextron.db") == ["0001_a"]
    [failed] = main_log(dev)
    assert failed["event"] == "pre_migrate_backup_failed"


# Row: Migration fails
def test_a_failed_migration_after_a_copy_names_the_copy_and_keeps_it(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_app_data: Path
) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))
    dev.mkdir(parents=True)
    with engine_on(dev / "nextron.db") as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_fails", "raise RuntimeError('the migration broke')"))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)

    code, err, spy = run_serve()

    assert code == 2
    assert spy.calls == []
    assert stored_revisions(dev / "nextron.db") == ["0001_a"]
    [copy] = local_copies(local_app_data)
    assert copy_revision(copy) == "0001_a"
    assert "database startup failed: it could not be opened, or a migration failed" in err
    assert f"The database was copied before the migration to {copy}" in err
    assert f'nextron restore "{copy}"' in err
    [failed] = main_log(dev)
    assert (failed["event"], failed["level"], failed["file"]) == (
        "database_migration_failed",
        "ERROR",
        copy.name,
    )
    assert "RuntimeError: the migration broke" in failed["exc"]


# Row: No pre-migrate
def test_a_new_database_or_one_at_head_takes_no_copy(dev: Path, local_app_data: Path) -> None:
    assert run_serve()[0] == 0  # a new file
    assert run_serve()[0] == 0  # at head

    assert local_copies(local_app_data) == []
    assert not (local_app_data / "Nextron" / "backups").exists()
    assert backup_rows(dev / "nextron.db") == []


# A failed record continues
def test_a_copy_whose_row_cannot_be_recorded_is_kept_and_startup_continues(
    dev: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_app_data: Path
) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))  # no backups table at all
    dev.mkdir(parents=True)
    with engine_on(dev / "nextron.db") as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_b", create_table("b")))
    monkeypatch.setattr(migrations, "SCRIPT_LOCATION", location)

    code, err, spy = run_serve(load_prompts=no_prompts, load_test_cases=no_cases)

    assert (code, err) == (0, "")
    assert spy.calls[0]["db_revision"] == "0002_b"
    [copy] = local_copies(local_app_data)
    assert copy_revision(copy) == "0001_a"
    assert events(dev) == [
        "database_migrated",
        "backup_record_failed",
        "engine_started",
        "engine_stopped",
    ]
    failed = main_log(dev)[1]
    assert (failed["level"], failed["code"], failed["file"]) == (
        "ERROR",
        "OperationalError",
        copy.name,
    )
    assert str(local_app_data) not in json.dumps(failed)  # the file name, never its path


def test_the_hook_takes_one_copy_and_raises_when_it_failed(tmp_path: Path) -> None:
    taken: list[str] = []

    class Local:
        def take(self, kind: BackupKind) -> object:
            taken.append(str(kind))
            return failed

    failed = SimpleNamespace(error="OSError: disk full", code="OSError")
    hook = main.PreMigrateBackup(Local())  # type: ignore[arg-type]

    with pytest.raises(main.PreMigrateBackupFailed, match="OSError: disk full"):
        hook("0001_a")

    assert taken == ["pre_migrate"]
    assert hook.taken is failed


# The prompts (Story 2.8) ------------------------------------------------------------------------


def prompt_rows(db_file: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """The ``prompt_snapshots`` and ``ai_route_activations`` rows, read outside the engine."""
    reader = sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)
    try:
        snapshots = reader.execute(
            "SELECT route, prompt_version, sha256 FROM prompt_snapshots ORDER BY route"
        ).fetchall()
        activations = reader.execute(
            "SELECT route, prompt_version, prompt_sha256, actor, bootstrap, test_run_id, "
            "deactivated_at FROM ai_route_activations ORDER BY route"
        ).fetchall()
        return snapshots, activations
    finally:
        reader.close()


def loader_of(folder: Path) -> main.PromptLoader:
    """``load_prompts`` on a copy of the prompts folder."""
    return lambda: main.load_prompts(folder)


def log_lines(data_dir: Path) -> list[dict[str, Any]]:
    text = (data_dir / "logs" / "nextron.log").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


@pytest.fixture
def prompts(tmp_path: Path) -> Path:
    """A copy of the shipped prompts folder a test may change."""
    from nextron.adapters.ai import routes  # noqa: PLC0415 - as serve loads it

    folder = tmp_path / "prompts"
    shutil.copytree(routes.PROMPTS_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    return folder


# Row: First start
def test_a_first_start_stores_the_prompts_and_activates_each_route(dev: Path) -> None:
    files, defaults = main.load_prompts()

    code, err, spy = run_serve()

    assert (code, err) == (0, "")
    snapshots, activations = prompt_rows(dev / "nextron.db")
    assert snapshots == sorted((f.route, f.version, f.sha256) for f in files)
    bootstrapped = [f for f in files if f.route in BOOTSTRAP_ROUTES]
    assert activations == sorted(
        (f.route, f.version, f.sha256, "seed", 1, None, None) for f in bootstrapped
    )
    # Story 4.14: the grounding routes serve only after their gate
    assert {s.route for s in defaults if s.route in BOOTSTRAP_ROUTES} == {
        row[0] for row in activations
    }
    lines = log_lines(dev)
    logged = [line["event"] for line in lines]
    assert logged.index("prompts_synced") < logged.index("engine_started")
    [synced] = [line for line in lines if line["event"] == "prompts_synced"]
    assert (synced["logger"], synced["stored_count"], synced["activated_count"]) == (
        "nextron.app.playbook.api",
        7,  # Story 4.15: draft_sequence's prompt too; Story 4.18: and its v2
        3,
    )


# Row: Restart
def test_a_restart_changes_no_prompt_row(dev: Path) -> None:
    assert run_serve()[0] == 0
    before = prompt_rows(dev / "nextron.db")

    assert run_serve()[0] == 0

    assert prompt_rows(dev / "nextron.db") == before
    synced = [line for line in log_lines(dev) if line["event"] == "prompts_synced"]
    assert [(line["unchanged_count"], line["kept_count"]) for line in synced] == [(0, 0), (7, 3)]


def test_the_loader_runs_outside_any_unit_of_work(dev: Path) -> None:
    seen: list[bool] = []

    def loader() -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
        try:
            current()
        except UnitOfWorkError:
            seen.append(True)
        return main.load_prompts()

    assert run_serve(load_prompts=loader)[0] == 0
    assert seen == [True]


# Row: Edited file
@pytest.mark.parametrize(
    ("file_name", "message"),
    [
        ("v1.md", "startup check failed: prompts: manifest.lock line "),
        (
            "v2.md",
            "startup check failed: prompts: extract_people/v2.md: the file isn't listed in "
            "manifest.lock",
        ),
    ],
)
def test_a_file_that_disagrees_with_the_lock_exits_2_and_writes_nothing(
    file_name: str, message: str, dev: Path, prompts: Path
) -> None:
    text = "An edit nobody locked."
    (prompts / "extract_people" / file_name).write_text(text, encoding="utf-8")

    code, err, spy = run_serve(load_prompts=loader_of(prompts))

    assert code == 2
    assert err.startswith(message)
    assert text not in err
    assert spy.calls == []
    assert prompt_rows(dev / "nextron.db") == ([], [])  # migrated, but nothing stored
    [failed] = [line for line in main_log(dev) if line["event"] == "startup_check_failed"]
    assert (failed["level"], failed["check"]) == ("ERROR", "prompts")
    assert "engine_started" not in events(dev)
    assert text not in (dev / "logs" / "nextron.log").read_text(encoding="utf-8")
    with InstanceLock(dev / "nextron.lock"):
        pass


# Row: Rewritten version
def test_a_rewritten_version_exits_2_and_writes_nothing(dev: Path, prompts: Path) -> None:
    assert run_serve()[0] == 0
    before = prompt_rows(dev / "nextron.db")
    path = prompts / "signal_inbox_parse" / "v1.md"
    path.write_text("A rewritten v1 with its lock updated.\n", encoding="utf-8")
    lock = prompts / "manifest.lock"
    text = lock.read_text(encoding="utf-8")
    [old] = [line for line in text.splitlines() if line.startswith("signal_inbox_parse ")]
    rewritten = PromptFile("signal_inbox_parse", "v1", path.read_text(encoding="utf-8"))
    extra = PromptFile("extract_people", "v2", "Also new.\n")
    (prompts / "extract_people" / "v2.md").write_text(extra.text, encoding="utf-8")
    text = text.replace(old, f"signal_inbox_parse v1 {rewritten.sha256}")
    lock.write_text(text + f"extract_people v2 {extra.sha256}\n", encoding="utf-8")

    code, err, spy = run_serve(load_prompts=loader_of(prompts))

    assert code == 2
    assert err.strip() == (
        "startup check failed: prompts: prompt signal_inbox_parse v1 is stored with another "
        "SHA-256; a used prompt never changes: add a new version"
    )
    assert spy.calls == []
    assert prompt_rows(dev / "nextron.db") == before  # extract_people v2 wasn't stored either


# Row: Missing prompt
def test_a_default_without_its_file_exits_2(dev: Path) -> None:
    def loader() -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
        files, defaults = main.load_prompts()
        return tuple(f for f in files if f.route != "extract_people"), defaults

    code, err, spy = run_serve(load_prompts=loader)

    assert code == 2
    assert err.strip() == (
        "startup check failed: prompts: the default of route extract_people names prompt v1, "
        "which has no file"
    )
    assert spy.calls == []
    assert prompt_rows(dev / "nextron.db") == ([], [])


def test_any_other_prompt_failure_exits_2_and_logs_the_traceback(dev: Path) -> None:
    def broken() -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
        raise OSError("the prompts folder vanished")

    code, err, spy = run_serve(load_prompts=broken)

    assert code == 2
    log_file = dev / "logs" / "nextron.log"
    assert err.strip() == f"prompt sync failed (OSError); the traceback is in {log_file}"
    assert spy.calls == []
    [failed] = [line for line in main_log(dev) if line["event"] == "prompt_sync_failed"]
    assert failed["level"] == "ERROR"
    assert "OSError: the prompts folder vanished" in failed["exc"]
    assert "engine_started" not in events(dev)
    with InstanceLock(dev / "nextron.lock"):
        pass


def test_the_database_engine_is_disposed_when_the_prompts_are_refused(
    dev: Path, engines: list[Engine], prompts: Path
) -> None:
    (prompts / "extract_people" / "v1.md").write_text("edited", encoding="utf-8")

    assert run_serve(load_prompts=loader_of(prompts))[0] == 2

    [engine] = engines
    assert_disposed(engine, dev / "nextron.db")


# Story 4.4: the Sales Playbook read at serve start


def playbook_rows(
    db_file: Path,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """The ``playbook_snapshots``, ``ai_route_tuples`` and ``playbook_checks`` rows."""
    reader = sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)
    try:
        snapshots = reader.execute("SELECT version, sha256 FROM playbook_snapshots").fetchall()
        tuples = reader.execute(
            "SELECT route, prompt_version, playbook_sha256 FROM ai_route_tuples ORDER BY route"
        ).fetchall()
        checks = reader.execute(
            'SELECT "trigger", playbook_sha256, problem FROM playbook_checks ORDER BY checked_at'
        ).fetchall()
        return snapshots, tuples, checks
    finally:
        reader.close()


def test_a_first_start_stores_the_playbook_and_records_the_tuples_and_the_check(
    dev: Path,
) -> None:
    config = load_bootstrap()
    read = main.read_playbook_file(config.playbook_path)
    assert isinstance(read, PlaybookSnapshot)

    code, err, spy = run_serve()

    assert (code, err) == (0, "")
    snapshots, tuples, checks = playbook_rows(dev / "nextron.db")
    assert snapshots == [(read.version, read.sha256)]
    assert tuples == [
        ("draft_sequence", "v2", read.sha256),  # Story 4.15, v2 since 4.18: pinned to it
        ("extract_people", "v1", None),
        ("extract_signals", "v1", None),
        ("grounding_check", "v1", read.sha256),  # Story 4.14: pinned to the Playbook read
        ("grounding_check_fallback", "v1", read.sha256),
        ("signal_inbox_parse", "v1", None),
    ]
    assert checks == [("startup", read.sha256, None)]
    [synced] = [line for line in log_lines(dev) if line["event"] == "prompts_synced"]
    assert synced["candidate_count"] == 6


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"# A Playbook without front matter\n", PlaybookProblem.NO_VERSION),
        (b"---\nversion: 1\nversion: 2\n---\nText.\n", PlaybookProblem.BAD_VERSION),
        (b"---\nversion: 1.0\n---\n\xff\n", PlaybookProblem.NOT_UTF8),
        (None, PlaybookProblem.UNREADABLE),  # the file is missing
    ],
)
def test_a_playbook_problem_never_stops_serve(
    content: bytes | None, problem: PlaybookProblem, dev: Path
) -> None:
    path = load_bootstrap().playbook_path
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)

    code, err, spy = run_serve()

    assert code == 0
    assert err == f"warning: playbook: {problem_text(problem)}\n"
    assert len(spy.calls) == 1  # the server started
    snapshots, tuples, checks = playbook_rows(dev / "nextron.db")
    assert snapshots == []
    assert len(tuples) == 3  # extraction is unaffected
    assert checks == [("startup", None, problem.value)]
    _, activations = prompt_rows(dev / "nextron.db")
    assert len(activations) == 3
    [warned] = [line for line in log_lines(dev) if line["event"] == "playbook_invalid"]
    assert (warned["level"], warned["problem"]) == ("WARNING", problem.value)
    assert "engine_started" in events(dev)


def test_the_playbook_is_read_outside_any_unit_of_work(dev: Path) -> None:
    seen: list[bool] = []

    def reader(path: Path) -> PlaybookSnapshot | PlaybookProblem:
        try:
            current()
        except UnitOfWorkError:
            seen.append(True)
        return main.read_playbook_file(path)

    assert run_serve(read_playbook=reader)[0] == 0
    assert seen == [True]


def test_read_playbook_file_gives_the_snapshot_or_the_problem(tmp_path: Path) -> None:
    path = tmp_path / "sales-playbook.md"
    text = "---\nversion: 0.9.0\n---\n\nRules.\n"
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    assert main.read_playbook_file(path) == PlaybookSnapshot("0.9.0", text)
    assert main.read_playbook_file(tmp_path / "missing.md") is PlaybookProblem.UNREADABLE
    assert main.read_playbook_file(tmp_path) is PlaybookProblem.UNREADABLE  # a folder
    path.write_bytes(b"---\nversion: 1\n---\n" + b"x" * MAX_PLAYBOOK_BYTES)
    assert main.read_playbook_file(path) is PlaybookProblem.TOO_LARGE


def test_load_ai_content_reads_the_prompts_and_the_playbook(tmp_path: Path) -> None:
    path = tmp_path / "sales-playbook.md"
    path.write_text("---\nversion: 4.4.0\n---\n\nRules.\n", encoding="utf-8")

    loaded = main.load_ai_content(path)

    assert loaded == (*main.load_prompts(), main.read_playbook_file(path))
    assert isinstance(loaded[2], PlaybookSnapshot) and loaded[2].version == "4.4.0"
    missing = main.load_ai_content(tmp_path / "missing.md")
    assert missing[2] is PlaybookProblem.UNREADABLE


def test_the_served_app_loads_the_ai_content_from_the_configured_playbook(
    dev: Path,
    opened: list[UnitOfWorkFactory],
    fake_keyring: list[InMemorySecrets],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_bootstrap().playbook_path.write_text(
        "---\nversion: 7.7.7-wiring\n---\n\nRules.\n", encoding="utf-8"
    )
    created: list[dict[str, Any]] = []
    real_create_app = main.create_app

    def spy(**kwargs: Any) -> object:
        created.append(kwargs)
        return real_create_app(**kwargs)

    monkeypatch.setattr(main, "create_app", spy)
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: None)

    assert main.serve(probes=fake_probes(), err=io.StringIO()) == 0

    [kwargs] = created
    assert isinstance(kwargs["playbook"], main.Playbook)
    files, defaults, read = kwargs["ai_content"]()  # the loader runs only when called
    assert (files, defaults) == main.load_prompts()
    assert isinstance(read, PlaybookSnapshot) and read.version == "7.7.7-wiring"


# Story 4.5: the AI Test Set cases loaded at serve start


def case_rows(db_file: Path) -> list[tuple[Any, ...]]:
    """The ``ai_test_cases`` rows, read outside the engine."""
    reader = sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)
    try:
        return reader.execute(
            "SELECT case_key, test_set, route, sha256, retired_at FROM ai_test_cases "
            "ORDER BY case_key, loaded_at"
        ).fetchall()
    finally:
        reader.close()


@pytest.fixture
def ai_sets(tmp_path: Path) -> Path:
    """A copy of the shipped ``tests/ai_sets`` folder a test may change."""
    folder = tmp_path / "ai_sets"
    shutil.copytree(main.AI_SETS_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    return folder


def cases_of(folder: Path) -> main.TestCaseLoader:
    return lambda: main.load_test_cases(folder)


def test_a_first_start_stores_the_four_sets_as_fixtures(dev: Path) -> None:
    cases = main.load_test_cases()

    code, err, spy = run_serve()

    assert (code, err) == (0, "")
    assert case_rows(dev / "nextron.db") == sorted(
        (case.case_key, case.test_set.value, case.route, case.sha256, None) for case in cases
    )
    counts = Counter(case.test_set for case in cases)
    assert counts[TestSet.EXTRACTION] == 11
    assert counts[TestSet.REPLY] >= 50 and counts[TestSet.GROUNDING] >= 30
    assert counts[TestSet.DRAFTING_SMOKE] >= 10
    reader = sqlite3.connect(f"{(dev / 'nextron.db').as_uri()}?mode=ro", uri=True)
    try:
        origins = reader.execute(
            "SELECT DISTINCT origin, contact_id FROM ai_test_cases"
        ).fetchall()
    finally:
        reader.close()
    assert origins == [("fixture", None)]
    lines = log_lines(dev)
    logged = [line["event"] for line in lines]
    assert logged.index("prompts_synced") < logged.index("test_cases_synced")
    assert logged.index("test_cases_synced") < logged.index("engine_started")
    [synced] = [line for line in lines if line["event"] == "test_cases_synced"]
    assert (synced["added_count"], synced["unchanged_count"]) == (len(cases), 0)


def test_an_edited_fixture_retires_its_old_row_at_the_next_start(
    dev: Path, ai_sets: Path
) -> None:
    assert run_serve(load_test_cases=cases_of(ai_sets))[0] == 0
    path = ai_sets / "extraction" / "ops_inbox_job_ad.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["expect"]["forbidden_signals"] = ["ops_expansion"]
    path.write_text(json.dumps(data), encoding="utf-8")

    assert run_serve(load_test_cases=cases_of(ai_sets))[0] == 0

    rows = [row for row in case_rows(dev / "nextron.db") if row[0] == "ops_inbox_job_ad"]
    assert len(rows) == 2
    assert sorted(row[4] is None for row in rows) == [False, True]  # one retired, one live
    assert len({row[3] for row in rows}) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda folder: (folder / "extraction" / "ops_inbox_job_ad.json").write_text(
                "{not json", encoding="utf-8"
            ),
            "extraction/ops_inbox_job_ad.json: the file isn't UTF-8 JSON",
        ),
        (
            lambda folder: (folder / "extraction" / "ops_inbox_job_ad.json").write_bytes(
                b'{"case": "\xff"}'
            ),
            "extraction/ops_inbox_job_ad.json: the file isn't UTF-8 JSON",
        ),
        (
            lambda folder: (folder / "extraction" / "ops_inbox_job_ad.json").write_text(
                (folder / "extraction" / "ops_inbox_job_ad.json")
                .read_text(encoding="utf-8")
                .replace('"injection": null', '"injection": "other"'),
                encoding="utf-8",
            ),
            "extraction/ops_inbox_job_ad.json: injection must be null or one of",
        ),
        (
            lambda folder: shutil.rmtree(folder / "extraction"),
            "extraction: the folder doesn't exist",
        ),
        (  # Story 4.6: every set's folder is required, and each file is checked at load
            lambda folder: shutil.rmtree(folder / "grounding"),
            "grounding: the folder doesn't exist",
        ),
        (
            lambda folder: (folder / "reply" / "reply_optout_take_me_off.json").write_text(
                "{not json", encoding="utf-8"
            ),
            "reply/reply_optout_take_me_off.json: the file isn't UTF-8 JSON",
        ),
        (
            lambda folder: (folder / "reply" / "reply_optout_take_me_off.json").write_text(
                (folder / "reply" / "reply_optout_take_me_off.json")
                .read_text(encoding="utf-8")
                .replace('"id": "r1"', '"id": "bad id!"'),
                encoding="utf-8",
            ),
            "reply/reply_optout_take_me_off.json: input.blocks[0].id must match",
        ),
    ],
)
def test_a_malformed_fixture_exits_2_and_stores_nothing(
    dev: Path, ai_sets: Path, change: Any, message: str
) -> None:
    change(ai_sets)

    code, err, spy = run_serve(load_test_cases=cases_of(ai_sets))

    assert code == 2
    assert err.startswith(f"startup check failed: ai_sets: {message}")
    assert spy.calls == []
    assert case_rows(dev / "nextron.db") == []
    assert prompt_rows(dev / "nextron.db") == ([], [])  # the files are read before any write
    [failed] = [line for line in main_log(dev) if line["event"] == "startup_check_failed"]
    assert (failed["level"], failed["check"]) == ("ERROR", "ai_sets")
    assert "engine_started" not in events(dev)


def test_the_case_loader_runs_outside_any_unit_of_work(dev: Path) -> None:
    seen: list[bool] = []

    def loader() -> tuple[TestCase, ...]:
        try:
            current()
        except UnitOfWorkError:
            seen.append(True)
        return main.load_test_cases()

    assert run_serve(load_test_cases=loader)[0] == 0
    assert seen == [True]


def test_load_test_cases_names_the_file_and_the_field_never_the_text(tmp_path: Path) -> None:
    folder = tmp_path / "ai_sets"
    shutil.copytree(main.AI_SETS_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    path = folder / "extraction" / "law_inbox_confidentiality_post.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    secret = data["blocks"][0]["text"]
    data["blocks"][0]["source"] = ""
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(InvalidTestCases) as raised:
        main.load_test_cases(folder)

    assert raised.value.message == (
        "extraction/law_inbox_confidentiality_post.json: blocks[0].source must be a non-empty "
        "string"
    )
    assert secret not in str(raised.value)
    path.write_bytes(b" " * (main.MAX_CASE_FILE_BYTES + 1))
    with pytest.raises(InvalidTestCases, match="larger than 1 MiB"):
        main.load_test_cases(folder)


def test_load_test_cases_reads_every_file_of_each_set_by_name() -> None:
    cases = main.load_test_cases()

    files = [
        (test_set, path.stem)
        for test_set in main.CASE_FOLDERS
        for path in sorted((main.AI_SETS_DIR / test_set.value).glob("*.json"))
    ]
    assert [(case.test_set, case.case_key) for case in cases] == files
    assert main.CASE_FOLDERS == (
        TestSet.EXTRACTION,
        TestSet.REPLY,
        TestSet.GROUNDING,
        TestSet.DRAFTING_SMOKE,
    )
    assert {case.route for case in cases} == {
        "extract_signals",
        "extract_people",
        "signal_inbox_parse",
        "classify_reply",
        "grounding_check",
        "draft_sequence",
    }


def test_load_test_cases_names_a_new_sets_file_and_path_never_the_text(tmp_path: Path) -> None:
    folder = tmp_path / "ai_sets"
    shutil.copytree(main.AI_SETS_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    path = folder / "reply" / "reply_optout_take_me_off.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    secret = data["input"]["blocks"][0]["text"]
    data["expected"]["reply_class"] = "maybe"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(InvalidTestCases) as raised:
        main.load_test_cases(folder)

    assert raised.value.message.startswith(
        "reply/reply_optout_take_me_off.json: expected.reply_class must be one of"
    )
    assert secret not in str(raised.value)
