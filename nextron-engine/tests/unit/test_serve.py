"""``serve`` against the story's I/O matrix, with fake probes and a fake server runner."""

import importlib
import io
import json
import zoneinfo
from pathlib import Path
from typing import Any

import pytest

from nextron import main
from nextron.bootstrap import ENV_ERROR, BootstrapConfig
from nextron.lock import InstanceLock, LockHeldError
from nextron.startup_checks import Probes
from tests.asgi import get
from tests.fakes.probes import PortProbe, fake_probes


class ServerSpy:
    """Stands in for Uvicorn and records what the engine looked like when it started."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, config: BootstrapConfig, reload: bool) -> None:
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
                "data_dir_exists": config.data_dir.is_dir(),
                "logs_dir_exists": (config.data_dir / "logs").is_dir(),
                "lock_held": lock_held,
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


def tree(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*"))


# Row: env unset or invalid
@pytest.mark.parametrize("value", [None, "staging"])
def test_env_unset_or_invalid_exits_2_and_creates_nothing(
    value: str | None, local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if value is not None:
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
    dev: Path, monkeypatch: pytest.MonkeyPatch
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


def test_reload_is_allowed_in_dev(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    status, _, body = get(factory(), "/health")  # NEXTRON_ENV=dev, as in the reload worker
    assert status == 200
    assert json.loads(body)["env"] == "dev"


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
    with InstanceLock(dev / "nextron.lock"):
        pass


def test_uvicorn_startup_failure_exits_2_not_3(dev: Path) -> None:
    def fail_to_start(config: BootstrapConfig, reload: bool) -> None:
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
    def crash(config: BootstrapConfig, reload: bool) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        main.serve(probes=fake_probes(), run_server=crash, err=io.StringIO())

    with InstanceLock(dev / "nextron.lock"):
        pass


def test_cli_dispatches_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron import __main__ as cli

    seen: list[bool] = []
    monkeypatch.setattr(cli.engine, "serve", lambda *, reload: seen.append(reload) or 0)

    assert cli.main(["serve", "--reload"]) == 0
    assert seen == [True]
