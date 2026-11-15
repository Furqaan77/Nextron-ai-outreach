"""Startup checks (AD-2): pure functions over injected probes."""

import socket
import zoneinfo
from pathlib import Path, PureWindowsPath

import pytest

from nextron import startup_checks as checks
from tests.fakes.probes import PortProbe, fake_probes

DATA_DIR = PureWindowsPath(r"C:\Users\mac\AppData\Local\Nextron\dev")


@pytest.mark.parametrize(
    ("version", "ok"),
    [("3.51.2", False), ("3.9.0", False), ("3.51.3", True), ("3.53.1", True), ("4.0", True)],
)
def test_sqlite_version(version: str, ok: bool) -> None:
    result = checks.check_sqlite_version(fake_probes(sqlite_version=version))

    assert result.name == "sqlite_version"
    assert result.ok is ok
    assert version in result.detail


def test_sqlite_version_probe_failure_fails_the_check() -> None:
    def broken() -> str:
        raise RuntimeError("no sqlite")

    assert not checks.check_sqlite_version(fake_probes(sqlite_version=broken)).ok


def test_tzdata_passes_when_europe_london_loads() -> None:
    loaded: list[str] = []

    def load(name: str) -> object:
        loaded.append(name)
        return object()

    assert checks.check_tzdata(fake_probes(load_zone=load)).ok
    assert loaded == ["Europe/London"]


def test_tzdata_fails_when_zoneinfo_raises() -> None:
    def missing(name: str) -> object:
        raise zoneinfo.ZoneInfoNotFoundError(name)

    result = checks.check_tzdata(fake_probes(load_zone=missing))

    assert result.name == "tzdata"
    assert not result.ok
    assert "Europe/London" in result.detail


@pytest.mark.parametrize(
    ("data_dir", "environ"),
    [
        (r"C:\Users\mac\OneDrive\Nextron\dev", {"OneDrive": r"C:\Users\mac\OneDrive"}),
        (r"D:\Work\Data\Nextron", {"OneDriveCommercial": r"D:\Work"}),
        (r"E:\Sync\Nextron", {"OneDriveConsumer": "E:\\Sync\\"}),
        (r"c:\users\MAC\onedrive\nextron", {"OneDrive": r"C:\Users\mac\OneDrive"}),
        (r"D:\OneDrive - Nextron AI\engine\dev", {}),
        (r"D:\files\onedrive\dev", {}),
    ],
)
def test_data_dir_inside_onedrive_fails(data_dir: str, environ: dict[str, str]) -> None:
    result = checks.check_data_dir_outside_onedrive(
        PureWindowsPath(data_dir), fake_probes(environ=environ)
    )

    assert result.name == "data_dir_outside_onedrive"
    assert not result.ok


@pytest.mark.parametrize(
    "data_dir",
    [
        r"C:\Users\mac\AppData\Local\Nextron\dev",
        r"C:\Users\mac\Documents\Nextron",
        r"D:\Data\MyOneDrive\dev",  # contains "OneDrive" but no segment starts with it
    ],
)
def test_data_dir_outside_onedrive_passes(data_dir: str) -> None:
    environ = {"OneDrive": r"C:\Users\mac\OneDrive", "OneDriveConsumer": r"C:\Users\mac\OneDrive"}

    result = checks.check_data_dir_outside_onedrive(
        PureWindowsPath(data_dir), fake_probes(environ=environ)
    )

    assert result.ok, result.detail


def test_port_check_uses_the_probe() -> None:
    probe = PortProbe(free=False)

    result = checks.check_port_free("127.0.0.1", 8766, fake_probes(port=probe))

    assert result.name == "port_free"
    assert not result.ok
    assert "127.0.0.1:8766" in result.detail
    assert probe.calls == [("127.0.0.1", 8766)]


def test_port_check_passes_when_free() -> None:
    assert checks.check_port_free("127.0.0.1", 8766, fake_probes()).ok


def test_all_checks_run_in_order() -> None:
    names = [r.name for r in checks.all_checks(DATA_DIR, "127.0.0.1", 8766, fake_probes())]

    assert names == ["sqlite_version", "tzdata", "data_dir_outside_onedrive", "port_free"]


def test_environment_checks_leave_out_the_port() -> None:
    probe = PortProbe()

    results = checks.environment_checks(DATA_DIR, fake_probes(port=probe))

    assert [r.name for r in results] == ["sqlite_version", "tzdata", "data_dir_outside_onedrive"]
    assert probe.calls == []


@pytest.mark.parametrize("reuse_address", [False, True])
def test_socket_probe_sees_a_bound_port(reuse_address: bool) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        if reuse_address:  # how Uvicorn binds; on Windows it would let a second server share
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]

        assert checks.socket_port_is_free("127.0.0.1", port) is False

    assert checks.socket_port_is_free("127.0.0.1", port) is True


def test_system_probes_pass_on_this_machine() -> None:
    probes = checks.Probes.system()

    assert checks.check_sqlite_version(probes).ok
    assert checks.check_tzdata(probes).ok


def test_system_probes_read_the_live_onedrive_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OneDrive", str(tmp_path))

    result = checks.check_data_dir_outside_onedrive(
        tmp_path / "Nextron" / "dev", checks.Probes.system()
    )

    assert not result.ok
    assert "%OneDrive%" in result.detail


def test_system_probes_see_a_listening_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]

        result = checks.check_port_free("127.0.0.1", port, checks.Probes.system())

    assert not result.ok
