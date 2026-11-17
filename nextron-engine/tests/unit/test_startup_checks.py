"""Startup checks (AD-2, AD-14): pure functions over injected probes."""

import ast
import socket
import zoneinfo
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any

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

    assert names == [
        "sqlite_version",
        "tzdata",
        "data_dir_outside_onedrive",
        "prod_env_not_persistent",
        "port_free",
    ]


def test_environment_checks_leave_out_the_port_and_the_persistent_env() -> None:
    probe = PortProbe()

    def persistent(name: str) -> dict[str, str]:
        raise AssertionError("serve's checks must not read the persistent environment")

    results = checks.environment_checks(
        DATA_DIR, fake_probes(port=probe, persistent_variable=persistent)
    )

    assert [r.name for r in results] == ["sqlite_version", "tzdata", "data_dir_outside_onedrive"]
    assert probe.calls == []


# Row: Doctor (a persistent NEXTRON_ENV=prod) ----------------------------------------------------


@pytest.mark.parametrize(
    ("values", "scopes"),
    [
        ({"user": "prod"}, "user environment"),
        ({"machine": "PROD "}, "machine environment"),
        ({"user": " Prod", "machine": "prod\t"}, "user and machine environments"),
        ({"user": "dev", "machine": "prod"}, "machine environment"),
    ],
)
def test_a_persistent_prod_fails_naming_the_scopes(values: dict[str, str], scopes: str) -> None:
    result = checks.check_prod_env_not_persistent(
        fake_probes(persistent_variable=lambda name: values)
    )

    assert result.name == "prod_env_not_persistent"
    assert not result.ok
    assert result.detail.startswith("NEXTRON_ENV=prod must only be set by the logon task")
    assert f"set persistently in the {scopes};" in result.detail


@pytest.mark.parametrize(
    "values", [{}, {"user": "dev"}, {"machine": "test"}, {"user": "production"}, {"user": ""}]
)
def test_no_persistent_prod_passes(values: dict[str, str]) -> None:
    asked: list[str] = []

    def persistent(name: str) -> dict[str, str]:
        asked.append(name)
        return values

    result = checks.check_prod_env_not_persistent(fake_probes(persistent_variable=persistent))

    assert result.ok, result.detail
    assert asked == ["NEXTRON_ENV"]


def test_a_probe_failure_fails_the_persistent_env_check() -> None:
    def broken(name: str) -> dict[str, str]:
        raise PermissionError("access denied")

    result = checks.check_prod_env_not_persistent(fake_probes(persistent_variable=broken))

    assert not result.ok
    assert "PermissionError" in result.detail


class FakeKey:
    def __init__(self, values: dict[str, tuple[object, int]]) -> None:
        self.values = values


class FakeRegistry:
    """A ``winreg`` stand-in: ``keys`` maps ``(hive, path)`` to the key's values, each
    ``(value, type)``; ``error`` makes every ``OpenKey`` raise it."""

    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(
        self,
        keys: dict[tuple[str, str], dict[str, tuple[object, int]]],
        error: OSError | None = None,
    ) -> None:
        self.keys = keys
        self.error = error
        self.opened: list[tuple[str, str]] = []
        self.closed = 0

    @contextmanager
    def OpenKey(self, hive: str, path: str) -> Iterator[FakeKey]:  # noqa: N802 - winreg's name
        self.opened.append((hive, path))
        if self.error is not None:
            raise self.error
        if (hive, path) not in self.keys:
            raise FileNotFoundError(path)
        try:
            yield FakeKey(self.keys[(hive, path)])
        finally:
            self.closed += 1

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[object, int]:  # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]


USER_KEY = ("HKCU", "Environment")
MACHINE_KEY = ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
REG_SZ, REG_EXPAND_SZ, REG_DWORD = 1, 2, 4


def test_read_persistent_variable_reads_the_user_and_machine_environment() -> None:
    registry = FakeRegistry(
        {
            USER_KEY: {"NEXTRON_ENV": ("prod", REG_SZ), "Other": ("x", REG_SZ)},
            MACHINE_KEY: {"NEXTRON_ENV": ("PROD ", REG_EXPAND_SZ)},
        }
    )

    values = checks.read_persistent_variable("NEXTRON_ENV", registry=registry)

    assert values == {"user": "prod", "machine": "PROD "}
    assert registry.opened == [USER_KEY, MACHINE_KEY]
    assert registry.closed == 2


@pytest.mark.parametrize(
    "keys",
    [
        {},  # neither key exists
        {USER_KEY: {}, MACHINE_KEY: {}},  # neither holds the value
        {USER_KEY: {"NEXTRON_ENV": (1, REG_DWORD)}},  # not a string: not in the environment
    ],
    ids=["no-keys", "no-values", "not-a-string"],
)
def test_a_missing_key_or_value_counts_as_absent(keys: Any) -> None:
    assert checks.read_persistent_variable("NEXTRON_ENV", registry=FakeRegistry(keys)) == {}


def test_a_registry_error_propagates() -> None:
    registry = FakeRegistry({}, error=PermissionError("access denied"))

    with pytest.raises(PermissionError):
        checks.read_persistent_variable("NEXTRON_ENV", registry=registry)


def test_winreg_is_imported_only_when_the_probe_runs() -> None:
    tree = ast.parse(Path(checks.__file__).read_text(encoding="utf-8"))
    top_level = [
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    ] + [node.module for node in tree.body if isinstance(node, ast.ImportFrom)]

    assert "winreg" not in top_level


def test_the_real_registry_is_read_for_a_variable_every_pc_has() -> None:
    """The live probe on this machine: the machine environment always sets ``Path``."""
    assert "machine" in checks.read_persistent_variable("Path")
    assert checks.read_persistent_variable("NEXTRON_PROBE_NEVER_SET") == {}
    assert checks.Probes.system().persistent_variable is checks.read_persistent_variable


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
