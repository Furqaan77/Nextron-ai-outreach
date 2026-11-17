"""``serve`` against the story's I/O matrix, with fake probes and a fake server runner."""

import importlib
import io
import json
import sqlite3
import zoneinfo
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.bootstrap import ENV_ERROR, BootstrapConfig, load_bootstrap
from nextron.db import migrations
from nextron.db.migrations import ensure_current, head_revision
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.notifications import NOTIFICATIONS
from nextron.lock import InstanceLock, LockHeldError
from nextron.startup_checks import Probes
from tests.asgi import get
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
    *, probes: Probes | None = None, reload: bool = False
) -> tuple[int, str, ServerSpy]:
    spy = ServerSpy()
    err = io.StringIO()
    code = main.serve(
        reload=reload,
        probes=probes if probes is not None else fake_probes(),
        run_server=spy,
        err=err,
    )
    return code, err.getvalue(), spy


@pytest.fixture
def dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``NEXTRON_ENV=dev`` with a temporary data dir; returns the data dir."""
    data_dir = tmp_path / "data" / "dev"
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(data_dir))
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
        assert fake_keyring[0].calls == []
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


# The database step --------------------------------------------------------------------------


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[UnitOfWorkFactory]:
    """Every unit-of-work factory ``serve`` opens."""
    factories: list[UnitOfWorkFactory] = []
    real = main.open_database

    def spy(config: BootstrapConfig) -> tuple[UnitOfWorkFactory, str]:
        units, revision = real(config)
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

    code, err, spy = run_serve()

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


def test_build_subscribers_ships_an_empty_frozen_registry() -> None:
    subscribers = main.build_subscribers()

    assert subscribers.frozen
    assert all(subscribers.subscribers_of(kind) == () for kind in NOTIFICATIONS)


def test_cli_dispatches_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron import __main__ as cli

    seen: list[bool] = []
    monkeypatch.setattr(cli.engine, "serve", lambda *, reload: seen.append(reload) or 0)

    assert cli.main(["serve", "--reload"]) == 0
    assert seen == [True]
