"""Startup and readiness checks (AD-2, AD-14, AD-20, FR-56): pure functions over injected probes
and callables, the doctor check registry, ``run_checks`` and the two output formats."""

import ast
import json
import os
import socket
import sys
import time
import zoneinfo
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any

import pytest

from nextron import startup_checks as checks
from nextron.domain import credentials
from nextron.domain.health import HEARTBEAT_FRESH
from nextron.ports.errors import AdapterError
from nextron.startup_checks import CheckFailed, CheckResult, DoctorCheck, DoctorChecks, Status
from tests.fakes.probes import PortProbe, fake_probes

DATA_DIR = PureWindowsPath(r"C:\Users\mac\AppData\Local\Nextron\dev")


@pytest.mark.parametrize(
    ("version", "ok"),
    [("3.51.2", False), ("3.9.0", False), ("3.51.3", True), ("3.53.1", True), ("4.0", True)],
)
def test_sqlite_version(version: str, ok: bool) -> None:
    result = checks.check_sqlite_version(fake_probes(sqlite_version=version))

    assert result.id == "sqlite_version"
    assert result.status is (Status.OK if ok else Status.FAIL)
    assert version in result.message


def test_sqlite_version_probe_failure_fails_the_check_naming_only_the_type() -> None:
    def broken() -> str:
        raise RuntimeError("no sqlite")

    result = checks.check_sqlite_version(fake_probes(sqlite_version=broken))

    assert result.status is Status.FAIL
    assert result.message == "cannot read the SQLite version (RuntimeError)"


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ((3, 14, 7), False),
        ((3, 13, 12), False),
        ((3, 14, 8), True),
        ((3, 14, 10), True),
        ((3, 15, 0), True),
    ],
)
def test_python_version(version: tuple[int, ...], ok: bool) -> None:
    result = checks.check_python_version(fake_probes(python_version=version))

    assert result.id == "python_version"
    assert result.status is (Status.OK if ok else Status.FAIL)
    assert ".".join(map(str, version)) in result.message
    assert "3.14.8" in result.message


def test_python_version_probe_failure_fails_the_check_naming_only_the_type() -> None:
    def broken() -> tuple[int, ...]:
        raise OSError("secret detail")

    result = checks.check_python_version(fake_probes(python_version=broken))

    assert result == CheckResult(
        "python_version", Status.FAIL, "cannot read the Python version (OSError)"
    )


def test_tzdata_passes_when_europe_london_loads() -> None:
    loaded: list[str] = []

    def load(name: str) -> object:
        loaded.append(name)
        return object()

    assert checks.check_tzdata(fake_probes(load_zone=load)).status is Status.OK
    assert loaded == ["Europe/London"]


def test_tzdata_fails_when_zoneinfo_raises() -> None:
    def missing(name: str) -> object:
        raise zoneinfo.ZoneInfoNotFoundError(name)

    result = checks.check_tzdata(fake_probes(load_zone=missing))

    assert result.id == "tzdata"
    assert result.status is Status.FAIL
    assert "Europe/London" in result.message


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

    assert result.id == "data_dir_outside_onedrive"
    assert result.status is Status.FAIL


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

    assert result.status is Status.OK, result.message


def test_port_check_uses_the_probe() -> None:
    probe = PortProbe(free=False)

    result = checks.check_port_free("127.0.0.1", 8766, fake_probes(port=probe))

    assert result == CheckResult(
        "port_free", Status.FAIL, "127.0.0.1:8766 is already in use by another process"
    )
    assert probe.calls == [("127.0.0.1", 8766)]


def test_port_check_passes_when_free() -> None:
    assert checks.check_port_free("127.0.0.1", 8766, fake_probes()).status is Status.OK


def test_a_port_probe_failure_fails_naming_only_the_type() -> None:
    def broken(host: str, port: int) -> bool:
        raise PermissionError("secret detail")

    result = checks.check_port_free("127.0.0.1", 8766, fake_probes(port_is_free=broken))

    assert result == CheckResult(
        "port_free", Status.FAIL, "cannot probe 127.0.0.1:8766 (PermissionError)"
    )


HEARTBEAT = DATA_DIR / "heartbeat"


def _age(seconds: float | None) -> Callable[[PurePath], float | None]:
    def file_age(path: PurePath) -> float | None:
        assert path == HEARTBEAT
        return seconds

    return file_age


# Row: Port --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "status"),
    [
        (30.0, Status.OK),
        (0.0, Status.OK),
        (119.9, Status.OK),
        (120.0, Status.FAIL),
        (300.0, Status.FAIL),
        (None, Status.FAIL),
        (-5.0, Status.OK),  # a just-touched file whose mtime runs slightly ahead
        (-300.0, Status.FAIL),  # from the future: the clock was set back, so not fresh
    ],
    ids=["30s", "0s", "119.9s", "120s", "300s", "missing", "minus-5s", "minus-300s"],
)
def test_a_busy_port_is_ok_only_with_a_fresh_heartbeat(age: float | None, status: Status) -> None:
    probes = fake_probes(port=PortProbe(free=False), file_age=_age(age))

    result = checks.check_port_free("127.0.0.1", 8766, probes, heartbeat=HEARTBEAT)

    assert result.id == "port_free"
    assert result.status is status
    if status is Status.OK:
        assert "held by this environment's running engine" in result.message
    else:
        assert "already in use" in result.message
        assert f"{HEARTBEAT} is missing or not within the last 2 minutes" in result.message


def test_a_heartbeat_read_error_counts_as_no_heartbeat() -> None:
    def broken(path: PurePath) -> float | None:
        raise PermissionError("secret detail")

    probes = fake_probes(port=PortProbe(free=False), file_age=broken)

    result = checks.check_port_free("127.0.0.1", 8766, probes, heartbeat=HEARTBEAT)

    assert result.status is Status.FAIL
    assert "secret detail" not in result.message


def test_a_free_port_never_reads_the_heartbeat() -> None:
    def never(path: PurePath) -> float | None:
        raise AssertionError("a free port needs no heartbeat")

    result = checks.check_port_free(
        "127.0.0.1", 8766, fake_probes(file_age=never), heartbeat=HEARTBEAT
    )

    assert result == CheckResult("port_free", Status.OK, "127.0.0.1:8766 is free")


def test_in_the_engine_the_port_check_probes_nothing_and_passes() -> None:
    def never_probe(host: str, port: int) -> bool:
        raise AssertionError("the engine serving the page holds the port")

    def never_read(path: PurePath) -> float | None:
        raise AssertionError("no heartbeat is read in the engine")

    probes = fake_probes(port_is_free=never_probe, file_age=never_read)

    result = checks.check_port_free(
        "127.0.0.1", 8766, probes, heartbeat=HEARTBEAT, in_engine=True
    )

    assert result == CheckResult(
        "port_free", Status.OK, "127.0.0.1:8766 is held by this engine, which serves this page"
    )


def test_the_heartbeat_threshold_is_the_health_panels() -> None:
    assert HEARTBEAT_FRESH.total_seconds() == checks.HEARTBEAT_FRESH_S == 120.0


# Row: Backup dir --------------------------------------------------------------------------------

BACKUP_DIR = PureWindowsPath(r"C:\Users\mac\AppData\Local\Nextron\backups\dev")


@pytest.mark.parametrize(
    ("backup_dir", "environ", "reason"),
    [
        (r"c:\users\MAC\appdata\local\nextron\DEV", {}, "is the data dir"),
        (r"C:\Users\mac\AppData\Local\Nextron\dev\backups", {}, "is inside the data dir"),
        (r"C:\Users\mac\AppData\Local\Nextron", {}, "contains the data dir"),
        (
            r"C:\Users\mac\OneDrive\Backups",
            {"OneDrive": r"C:\Users\mac\OneDrive"},
            "is inside %OneDrive%",
        ),
        (
            r"D:\OneDrive - Nextron AI\backups",
            {},
            'has the path segment "OneDrive - Nextron AI"',
        ),
    ],
    ids=[
        "equal-other-case",
        "inside-the-data-dir",
        "containing-it",
        "onedrive-variable",
        "onedrive-segment",
    ],
)
def test_a_backup_dir_that_is_not_separate_fails(
    backup_dir: str, environ: dict[str, str], reason: str
) -> None:
    result = checks.check_backup_dir_separate(
        DATA_DIR, PureWindowsPath(backup_dir), fake_probes(environ=environ)
    )

    assert result.id == "backup_dir_separate"
    assert result.status is Status.FAIL
    assert reason in result.message
    assert "NEXTRON_BACKUP_DIR" in result.message


@pytest.mark.parametrize(
    "backup_dir",
    [
        str(BACKUP_DIR),  # the default
        r"C:\Users\mac\AppData\Local\Nextron\dev-backups",  # a sibling sharing a prefix
        r"E:\Backups\Nextron",
    ],
)
def test_a_separate_backup_dir_passes(backup_dir: str) -> None:
    environ = {"OneDrive": r"C:\Users\mac\OneDrive"}

    result = checks.check_backup_dir_separate(
        DATA_DIR, PureWindowsPath(backup_dir), fake_probes(environ=environ)
    )

    assert result.status is Status.OK, result.message


def test_environment_checks_leave_out_the_port_and_the_persistent_env() -> None:
    probe = PortProbe()

    def persistent(name: str) -> dict[str, str]:
        raise AssertionError("serve's checks must not read the persistent environment")

    results = checks.environment_checks(
        DATA_DIR, fake_probes(port=probe, persistent_variable=persistent)
    )

    assert [r.id for r in results] == ["sqlite_version", "tzdata", "data_dir_outside_onedrive"]
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

    assert result.id == "prod_env_not_persistent"
    assert result.status is Status.FAIL
    assert result.message.startswith("NEXTRON_ENV=prod must only be set by the logon task")
    assert f"set persistently in the {scopes};" in result.message


@pytest.mark.parametrize(
    "values", [{}, {"user": "dev"}, {"machine": "test"}, {"user": "production"}, {"user": ""}]
)
def test_no_persistent_prod_passes(values: dict[str, str]) -> None:
    asked: list[str] = []

    def persistent(name: str) -> dict[str, str]:
        asked.append(name)
        return values

    result = checks.check_prod_env_not_persistent(fake_probes(persistent_variable=persistent))

    assert result.status is Status.OK, result.message
    assert asked == ["NEXTRON_ENV"]


@pytest.mark.parametrize("source", ["--env", "NEXTRON_ENV"])
@pytest.mark.parametrize("values", [{}, {"user": "prod"}], ids=["none", "user-prod"])
def test_the_persistent_env_message_names_the_env_and_its_source(
    source: str, values: dict[str, str]
) -> None:
    result = checks.check_prod_env_not_persistent(
        fake_probes(persistent_variable=lambda name: values), "dev", source
    )

    assert result.message.endswith(f"; this run is dev, from {source}")


def test_a_probe_failure_fails_the_persistent_env_check() -> None:
    def broken(name: str) -> dict[str, str]:
        raise PermissionError("access denied")

    result = checks.check_prod_env_not_persistent(fake_probes(persistent_variable=broken))

    assert result.status is Status.FAIL
    assert "PermissionError" in result.message
    assert "access denied" not in result.message


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

    assert checks.check_sqlite_version(probes).status is Status.OK
    assert checks.check_tzdata(probes).status is Status.OK
    assert checks.check_python_version(probes).status is Status.OK
    assert probes.python_version() == tuple(sys.version_info[:3])
    assert probes.file_age is checks.file_age_seconds


def test_file_age_seconds(tmp_path: Path) -> None:
    beat = tmp_path / "heartbeat"

    assert checks.file_age_seconds(beat) is None
    assert not beat.exists()
    beat.touch()
    age = checks.file_age_seconds(beat)
    assert age is not None and -5 < age < 60
    old = time.time() - 300
    os.utime(beat, (old, old))
    age = checks.file_age_seconds(beat)
    assert age is not None and 295 < age < 360


def test_system_probes_read_the_live_onedrive_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OneDrive", str(tmp_path))

    result = checks.check_data_dir_outside_onedrive(
        tmp_path / "Nextron" / "dev", checks.Probes.system()
    )

    assert result.status is Status.FAIL
    assert "%OneDrive%" in result.message


def test_system_probes_see_a_listening_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]

        result = checks.check_port_free("127.0.0.1", port, checks.Probes.system())

    assert result.status is Status.FAIL


# Row: Credential --------------------------------------------------------------------------------

REQUIRED = [found for found in credentials.declared() if found.required]
PLANTED = "QZXPLANTEDSECRET"


class Reader:
    """A ``SecretsPort.read`` stand-in: ``values`` by secret name; ``failing`` names raise
    ``keyring_failed``."""

    def __init__(self, values: dict[str, str], failing: frozenset[str] = frozenset()) -> None:
        self.values = values
        self.failing = failing
        self.asked: list[str] = []

    def __call__(self, name: str) -> str | None:
        self.asked.append(name)
        if name in self.failing:
            raise AdapterError(False, "keyring_failed", f"read of secret {name} failed")
        return self.values.get(name)


def all_set() -> dict[str, str]:
    return {found.secret_name: f"{PLANTED}-{found.key}" for found in REQUIRED}


def test_the_required_credentials_are_the_five_declared_ones() -> None:
    assert [str(found.key) for found in REQUIRED] == [
        "claude",
        "hunter",
        "companies_house",
        "telegram_bot",
        "unsubscribe_bearer",
    ]


def test_every_required_credential_set_passes_and_reads_only_the_required_ones() -> None:
    read = Reader(all_set() | {"prospeo/api_key": "x" * 5000})  # optional: not read

    result = checks.check_required_credentials(read)

    assert result.id == "required_credentials"
    assert result.status is Status.OK
    assert read.asked == [found.secret_name for found in REQUIRED]
    assert PLANTED not in result.message


def test_a_missing_credential_fails_naming_its_label() -> None:
    values = all_set()
    del values["hunter/api_key"]

    result = checks.check_required_credentials(Reader(values))

    assert result.status is Status.FAIL
    assert "not set: Hunter API key;" in result.message
    assert PLANTED not in result.message


@pytest.mark.parametrize(
    ("value", "status"),
    [
        ("x" * 1280, Status.OK),
        ("x" * 1281, Status.FAIL),
        ("x" * 1279 + "\N{GRINNING FACE}", Status.FAIL),  # 1,280 characters, 1,281 units
        ("x" * 1278 + "\N{GRINNING FACE}", Status.OK),  # 1,280 units
    ],
    ids=["1280", "1281", "1281-units", "1280-units"],
)
def test_a_credential_over_1280_utf16_units_fails(value: str, status: Status) -> None:
    result = checks.check_required_credentials(Reader(all_set() | {"telegram/bot_token": value}))

    assert result.status is status
    if status is Status.FAIL:
        assert "over 1,280 characters: Telegram bot token;" in result.message
        assert "xxxx" not in result.message


def test_an_unreadable_credential_fails_naming_its_label() -> None:
    read = Reader(all_set(), failing=frozenset({"unsub/bearer"}))

    result = checks.check_required_credentials(read)

    assert result.status is Status.FAIL
    assert "unreadable (Windows Credential Manager failed): Unsubscribe Endpoint bearer secret" in (
        result.message
    )
    assert "keyring_failed" not in result.message


def test_every_problem_is_listed_at_once() -> None:
    read = Reader(
        {"anthropic/api_key": "x" * 1281, "telegram/bot_token": "ok"},
        failing=frozenset({"unsub/bearer"}),
    )

    result = checks.check_required_credentials(read)

    assert result.message == (
        "required credentials not set: Hunter API key, Companies House API key; "
        "over 1,280 characters: Claude API key; "
        "unreadable (Windows Credential Manager failed): Unsubscribe Endpoint bearer secret; "
        "enter them on the Credentials page"
    )


# Row: Grant -------------------------------------------------------------------------------------

TOKEN = "ya29.QZXPLANTEDTOKEN"


def grant(outcome: AdapterError | None) -> tuple[Callable[[str], str], list[str]]:
    asked: list[str] = []

    def access_token(name: str) -> str:
        asked.append(name)
        if outcome is not None:
            raise outcome
        return TOKEN

    return access_token, asked


def test_a_refreshable_grant_passes_without_showing_the_token() -> None:
    access_token, asked = grant(None)

    result = checks.check_internal_notify_grant(access_token)

    assert result.id == "internal_notify_grant"
    assert result.status is Status.OK
    assert asked == ["internal_notify"]
    assert TOKEN not in result.message


def test_google_unreachable_is_a_warning() -> None:
    access_token, _ = grant(AdapterError(True, "google_unavailable", f"down {TOKEN}"))

    result = checks.check_internal_notify_grant(access_token)

    assert result.status is Status.WARN
    assert "google_unavailable" in result.message
    assert TOKEN not in result.message


@pytest.mark.parametrize(
    ("code", "says"),
    [
        ("google_client_not_imported", "the Google Desktop client is not imported"),
        ("google_not_granted", "internal_notify is not granted"),
        ("google_needs_reconsent", "internal_notify needs re-consent"),
        ("google_invalid_grant", "internal_notify needs re-consent"),
        ("keyring_failed", "Windows Credential Manager could not be read (keyring_failed)"),
        ("google_rejected", "internal_notify could not be refreshed (google_rejected)"),
        ("google_bad_response", "internal_notify could not be refreshed (google_bad_response)"),
    ],
)
def test_every_other_grant_error_fails_by_code(code: str, says: str) -> None:
    access_token, _ = grant(AdapterError(False, code, f"detail {TOKEN} mac@nextronai.com"))

    result = checks.check_internal_notify_grant(access_token)

    assert result.status is Status.FAIL
    assert says in result.message
    assert TOKEN not in result.message and "mac@" not in result.message


def test_without_refresh_a_stored_grant_passes_saying_the_panel_does_not_refresh_it() -> None:
    def check_stored_grant(name: str) -> None:
        asked.append(name)

    asked: list[str] = []

    result = checks.check_internal_notify_grant(check_stored_grant, refresh=False)

    assert result == CheckResult(
        "internal_notify_grant",
        Status.OK,
        "internal_notify is granted; the health panel doesn't refresh it (nextron doctor does)",
    )
    assert asked == ["internal_notify"]


@pytest.mark.parametrize(
    ("code", "says"),
    [
        ("google_client_not_imported", "the Google Desktop client is not imported"),
        ("google_not_granted", "internal_notify is not granted"),
        ("google_needs_reconsent", "internal_notify needs re-consent"),
        ("keyring_failed", "Windows Credential Manager could not be read (keyring_failed)"),
    ],
)
def test_without_refresh_each_stored_grant_problem_fails_by_code(code: str, says: str) -> None:
    check_stored_grant, _ = grant(AdapterError(False, code, f"detail {TOKEN} mac@nextronai.com"))

    result = checks.check_internal_notify_grant(check_stored_grant, refresh=False)

    assert result.status is Status.FAIL
    assert says in result.message
    assert TOKEN not in result.message and "mac@" not in result.message


def test_a_grant_check_that_raises_something_else_propagates() -> None:
    def broken(name: str) -> str:
        raise RuntimeError("not an adapter error")

    with pytest.raises(RuntimeError):
        checks.check_internal_notify_grant(broken)


# Row: Off-PC backup (Story 1.17) ------------------------------------------------------------------

OFFPC_BACKUPS_DIR = PureWindowsPath(r"C:\Users\mac\AppData\Local\Nextron\backups\dev")
PASSPHRASE_NAME = "backup/passphrase"
NOT_CONFIGURED = "off-PC backup not configured (OQ-A3): backups are local only; "
FOLDER_FIX = (
    "choose a folder on another drive or a network share in Off-PC backup folder "
    "(backup_offpc_dir) on the Settings page"
)


class Calls:
    """A ``SecretsPort.read`` and an ``offpc_dir`` that record the order they are called in."""

    def __init__(
        self, passphrase: str | None | Exception = "QZXPLANTEDPASS", folder: str | Exception = ""
    ) -> None:
        self.order: list[str] = []
        self._passphrase = passphrase
        self._folder = folder

    def read(self, name: str) -> str | None:
        self.order.append(f"read {name}")
        if isinstance(self._passphrase, Exception):
            raise self._passphrase
        return self._passphrase

    def offpc_dir(self) -> str:
        self.order.append("offpc_dir")
        if isinstance(self._folder, Exception):
            raise self._folder
        return self._folder


def offpc_check(calls: Calls) -> CheckResult:
    return checks.check_offpc_backup(calls.read, calls.offpc_dir, DATA_DIR, OFFPC_BACKUPS_DIR)


def test_an_off_pc_folder_on_another_drive_with_a_passphrase_is_ok() -> None:
    calls = Calls(folder=r"E:\NextronBackups")

    result = offpc_check(calls)

    assert result == CheckResult(
        "offpc_backup",
        Status.OK,
        r"off-PC backup folder E:\NextronBackups is set and the Backup passphrase is set; each "
        "daily and manual backup is encrypted and copied there",
    )
    assert calls.order == [f"read {PASSPHRASE_NAME}", "offpc_dir"]  # the passphrase first


def test_a_network_share_with_a_passphrase_is_ok() -> None:
    assert offpc_check(Calls(folder=r"\\nas\backups\nextron")).status is Status.OK


@pytest.mark.parametrize(
    ("folder", "reason"),
    [
        (
            "",
            "no off-PC backup folder is set; set Off-PC backup folder (backup_offpc_dir) on the "
            "Settings page",
        ),
        (
            str(OFFPC_BACKUPS_DIR),
            f"the off-PC backup folder {OFFPC_BACKUPS_DIR} is in the backup dir "
            f"{OFFPC_BACKUPS_DIR}; {FOLDER_FIX}",
        ),
        (
            rf"{str(DATA_DIR).upper()}\offpc",
            rf"the off-PC backup folder {str(DATA_DIR).upper()}\offpc is in the data dir "
            f"{DATA_DIR}; {FOLDER_FIX}",
        ),
    ],
    ids=["unset", "the backup dir", "inside the data dir"],
)
def test_a_folder_that_is_not_set_or_not_separate_warns(folder: str, reason: str) -> None:
    result = offpc_check(Calls(folder=folder))

    assert result == CheckResult("offpc_backup", Status.WARN, NOT_CONFIGURED + reason)


@pytest.mark.parametrize(
    ("passphrase", "reason"),
    [
        (None, "the Backup passphrase is not set; enter it on the Credentials page"),
        ("", "the Backup passphrase is not set; enter it on the Credentials page"),
        (
            "x" * 1281,
            "the Backup passphrase is over 1,280 characters; enter it on the Credentials page",
        ),
        (
            AdapterError(False, "keyring_failed", "QZXPLANTEDKEYRINGTEXT"),
            "the Backup passphrase could not be read (Windows Credential Manager failed); run "
            "doctor again, or enter it again on the Credentials page",
        ),
    ],
    ids=["not set", "blank", "too long", "unreadable"],
)
def test_a_passphrase_that_is_not_usable_warns(
    passphrase: str | None | Exception, reason: str
) -> None:
    result = offpc_check(Calls(passphrase=passphrase, folder=r"E:\NextronBackups"))

    assert result == CheckResult("offpc_backup", Status.WARN, NOT_CONFIGURED + reason)
    assert "QZXPLANTED" not in result.message


def test_every_reason_is_listed_with_its_fix() -> None:
    result = offpc_check(Calls(passphrase=None, folder=""))

    assert result.status is Status.WARN
    assert result.message == (
        NOT_CONFIGURED
        + "no off-PC backup folder is set; set Off-PC backup folder (backup_offpc_dir) on the "
        "Settings page; the Backup passphrase is not set; enter it on the Credentials page"
    )


def test_a_folder_setting_that_cannot_be_read_warns_naming_only_its_type() -> None:
    calls = Calls(folder=RuntimeError("QZXPLANTEDDATABASETEXT"))

    result = offpc_check(calls)

    assert result == CheckResult(
        "offpc_backup",
        Status.WARN,
        NOT_CONFIGURED + "the off-PC backup folder setting could not be read (RuntimeError); "
        "set Off-PC backup folder (backup_offpc_dir) on the Settings page",
    )
    assert calls.order == [f"read {PASSPHRASE_NAME}", "offpc_dir"]


@pytest.mark.parametrize(
    ("folder", "data_dir", "drive"),
    [
        (r"c:\NextronBackups", DATA_DIR, "C:"),
        (r"\\NAS\Backups\offpc", PureWindowsPath(r"\\nas\backups\nextron\dev"), r"\\nas\backups"),
    ],
)
def test_a_folder_on_the_data_dirs_drive_warns(
    folder: str, data_dir: PureWindowsPath, drive: str
) -> None:
    calls = Calls(folder=folder)

    result = checks.check_offpc_backup(calls.read, calls.offpc_dir, data_dir, OFFPC_BACKUPS_DIR)

    assert result == CheckResult(
        "offpc_backup",
        Status.WARN,
        f"off-PC backup folder {folder} is on the same drive as the data dir ({drive}); a "
        f"failed or lost disk would take both, so {FOLDER_FIX}",
    )


def test_the_off_pc_check_touches_no_folder(tmp_path: Path) -> None:
    missing = tmp_path / "unplugged" / "backups"

    result = offpc_check(Calls(folder=str(missing)))

    assert result.status in (Status.OK, Status.WARN)  # the same drive as tmp, or not
    assert not (tmp_path / "unplugged").exists()


# Row: Settings ----------------------------------------------------------------------------------


def test_no_unset_required_setting_passes() -> None:
    result = checks.check_required_settings(lambda: ())

    assert result == CheckResult(
        "required_settings", Status.OK, "every required setting is set and valid"
    )


def test_unset_required_settings_fail_naming_label_and_key() -> None:
    result = checks.check_required_settings(lambda: ("sender_timezone", "alert_chat_id"))

    assert result.status is Status.FAIL
    assert result.message == (
        "required settings not set or no longer valid: Sender's timezone (sender_timezone), "
        "Telegram alert chat id (alert_chat_id); set them on the Settings page"
    )


def test_unreadable_settings_fail_naming_only_the_error_type() -> None:
    def broken() -> tuple[str, ...]:
        raise CheckFailed("planted detail")

    result = checks.check_required_settings(broken)

    assert result == CheckResult(
        "required_settings", Status.FAIL, "the settings could not be read (CheckFailed)"
    )


# Row: Behind, Ahead / foreign -------------------------------------------------------------------

HEAD_REVISION = "0006_1_13_metering"
KNOWN = frozenset({"0005_1_12_notify", HEAD_REVISION})


def revision_check(exists: bool, revision: Callable[[], str | None]) -> CheckResult:
    return checks.check_database_revision(exists, revision, head=HEAD_REVISION, known=KNOWN)


def test_no_database_file_warns_and_reads_nothing() -> None:
    def never() -> str | None:
        raise AssertionError("no file: nothing to read")

    result = revision_check(False, never)

    assert result == CheckResult(
        "database_revision",
        Status.WARN,
        "no database file yet; it will be created and migrated on next start",
    )


@pytest.mark.parametrize(
    ("revision", "status", "message"),
    [
        (HEAD_REVISION, Status.OK, f"the database is at head ({HEAD_REVISION})"),
        (
            "0005_1_12_notify",
            Status.WARN,
            f"the database is at 0005_1_12_notify, behind {HEAD_REVISION}; "
            "it will migrate on next start",
        ),
        (None, Status.WARN, "the database is empty; it will migrate on next start"),
        (
            "0099_future",
            Status.FAIL,
            "database revision 0099_future is newer than this code; run the newer code or "
            "restore a backup",
        ),
    ],
    ids=["head", "behind", "empty", "ahead"],
)
def test_the_database_revision(revision: str | None, status: Status, message: str) -> None:
    assert revision_check(True, lambda: revision) == CheckResult(
        "database_revision", status, message
    )


def test_a_check_failed_shows_its_message() -> None:
    def foreign() -> str | None:
        raise CheckFailed("the database has tables but no Alembic revision")

    result = revision_check(True, foreign)

    assert result == CheckResult(
        "database_revision", Status.FAIL, "the database has tables but no Alembic revision"
    )


def test_any_other_read_error_names_only_its_type() -> None:
    def broken() -> str | None:
        raise PermissionError("planted detail")

    result = revision_check(True, broken)

    assert result == CheckResult(
        "database_revision", Status.FAIL, "the database could not be read (PermissionError)"
    )


# The registry -----------------------------------------------------------------------------------


def ok(check_id: str, message: str = "fine") -> DoctorCheck[object]:
    return DoctorCheck(check_id, lambda context: CheckResult(check_id, Status.OK, message))


@pytest.mark.parametrize(
    "check_id",
    ["", "Python", "1st", "a-b", "a b", "a\n", "x" * 65, "é", 7, None],
    ids=["empty", "upper", "digit-first", "dash", "space", "newline", "65", "accent", "int",
         "none"],
)
def test_a_bad_check_id_is_refused(check_id: object) -> None:
    with pytest.raises(TypeError):
        DoctorCheck(check_id, lambda context: None)  # type: ignore[arg-type]


def test_the_longest_id_is_accepted() -> None:
    longest = "a" * 64

    assert ok(longest).id == longest


def test_run_must_be_callable() -> None:
    with pytest.raises(TypeError):
        DoctorCheck("a", "not callable")  # type: ignore[arg-type]


def test_the_registry_keeps_registration_order_and_refuses_a_repeated_id() -> None:
    registry: DoctorChecks[object] = DoctorChecks()
    first, second = ok("zeta"), ok("alpha")
    registry.register_check(first)
    registry.register_check(second)

    with pytest.raises(ValueError, match="zeta"):
        registry.register_check(ok("zeta"))
    with pytest.raises(TypeError):
        registry.register_check("zeta")  # type: ignore[arg-type]

    assert registry.checks == (first, second)
    assert not registry.frozen


def test_the_registry_refuses_registration_after_freeze() -> None:
    registry: DoctorChecks[object] = DoctorChecks()
    registry.register_check(ok("a"))
    registry.freeze()

    with pytest.raises(RuntimeError):
        registry.register_check(ok("b"))

    assert registry.frozen
    assert [check.id for check in registry.checks] == ["a"]


def test_run_checks_needs_a_frozen_registry() -> None:
    registry: DoctorChecks[object] = DoctorChecks()
    registry.register_check(ok("a"))

    with pytest.raises(RuntimeError):
        checks.run_checks(registry, object())


def frozen(*items: DoctorCheck[Any]) -> DoctorChecks[Any]:
    registry: DoctorChecks[Any] = DoctorChecks()
    for item in items:
        registry.register_check(item)
    registry.freeze()
    return registry


# Row: Crash -------------------------------------------------------------------------------------


def test_a_crashed_check_fails_naming_its_type_and_the_rest_still_run() -> None:
    contexts: list[object] = []

    def boom(context: object) -> CheckResult:
        contexts.append(context)
        raise RuntimeError("planted detail")

    def last(context: object) -> CheckResult:
        contexts.append(context)
        return CheckResult("last", Status.WARN, "still ran")

    context = object()
    results = checks.run_checks(
        frozen(ok("first"), DoctorCheck("boom", boom), DoctorCheck("last", last)), context
    )

    assert results == [
        CheckResult("first", Status.OK, "fine"),
        CheckResult("boom", Status.FAIL, "the check crashed (RuntimeError)"),
        CheckResult("last", Status.WARN, "still ran"),
    ]
    assert contexts == [context, context]


@pytest.mark.parametrize(
    "returned",
    [
        None,
        "ok",
        CheckResult("other", Status.OK, "the wrong id"),
        CheckResult("mine", "ok", "a plain string status"),  # type: ignore[arg-type]
        CheckResult("mine", Status.OK, None),  # type: ignore[arg-type]
    ],
    ids=["none", "str", "wrong-id", "str-status", "no-message"],
)
def test_a_check_that_returns_no_result_fails(returned: object) -> None:
    def mine_run(context: object) -> CheckResult:
        return returned  # type: ignore[return-value]

    mine: DoctorCheck[Any] = DoctorCheck("mine", mine_run)

    results = checks.run_checks(frozen(mine, ok("next")), None)

    assert results == [
        CheckResult("mine", Status.FAIL, "the check returned no result"),
        CheckResult("next", Status.OK, "fine"),
    ]


def test_line_breaks_in_a_message_become_spaces() -> None:
    results = checks.run_checks(frozen(ok("a", "one\ntwo\r\nthree\rfour\u2028five")), None)

    assert results[0].message == "one two three four five"


def test_a_base_exception_is_not_swallowed() -> None:
    def interrupted(context: object) -> CheckResult:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        checks.run_checks(frozen(DoctorCheck("a", interrupted)), None)


# The formats ------------------------------------------------------------------------------------

RESULTS = [
    CheckResult("python_version", Status.OK, "Python 3.14.8 (needs 3.14.8 or later)"),
    CheckResult("database_revision", Status.WARN, "the database is empty; it will migrate"),
    CheckResult("tzdata", Status.FAIL, 'ZoneInfo("Europe/London") failed'),
]


def test_format_table() -> None:
    assert checks.format_table(RESULTS) == (
        "status  check              message\n"
        "ok      python_version     Python 3.14.8 (needs 3.14.8 or later)\n"
        "warn    database_revision  the database is empty; it will migrate\n"
        'fail    tzdata             ZoneInfo("Europe/London") failed\n'
        "1 ok, 1 warn, 1 fail"
    )


def test_format_table_pads_to_the_header_when_every_id_is_short() -> None:
    assert checks.format_table([CheckResult("a", Status.OK, "")]) == (
        "status  check  message\nok      a\n1 ok, 0 warn, 0 fail"
    )


def test_format_json() -> None:
    text = checks.format_json(RESULTS)

    assert text == (
        "[\n"
        "  {\n"
        '    "id": "python_version",\n'
        '    "status": "ok",\n'
        '    "message": "Python 3.14.8 (needs 3.14.8 or later)"\n'
        "  },\n"
        "  {\n"
        '    "id": "database_revision",\n'
        '    "status": "warn",\n'
        '    "message": "the database is empty; it will migrate"\n'
        "  },\n"
        "  {\n"
        '    "id": "tzdata",\n'
        '    "status": "fail",\n'
        '    "message": "ZoneInfo(\\"Europe/London\\") failed"\n'
        "  }\n"
        "]"
    )
    assert json.loads(text) == [
        {"id": r.id, "status": str(r.status), "message": r.message} for r in RESULTS
    ]


def test_format_json_is_ascii_only() -> None:
    text = checks.format_json([CheckResult("a", Status.OK, r"data dir D:\Données")])

    assert text.isascii()
    assert json.loads(text)[0]["message"] == r"data dir D:\Données"


# Row: unsub_token_keys (Story 3.8) ----------------------------------------------------------------

K_TOK_TEXT = "QZXPLANTEDKTOK" + "A" * 28 + "Q"  # a 32-byte base64url key
K_EDGE_TEXT = "QZXPLANTEDKEDGE" + "A" * 27 + "Q"


class UnsubKeys:
    """A ``SecretsPort.read`` and an ``active_kid`` that record the order they are called in."""

    def __init__(self, kid: object = 1, **entries: object) -> None:
        self.order: list[str] = []
        self._kid = kid
        self._entries = {"unsub/k_tok/1": K_TOK_TEXT, "unsub/k_edge/1": K_EDGE_TEXT, **entries}

    def read(self, name: str) -> str | None:
        self.order.append(f"read {name}")
        found = self._entries.get(name)
        if isinstance(found, Exception):
            raise found
        return found  # type: ignore[return-value]

    def active_kid(self) -> object:
        self.order.append("active_kid")
        if isinstance(self._kid, Exception):
            raise self._kid
        return self._kid

    def check(self) -> CheckResult:
        return checks.check_unsub_token_keys(self.read, self.active_kid)


def test_the_active_kids_two_distinct_keys_are_ok_read_after_the_setting() -> None:
    keys = UnsubKeys()

    result = keys.check()

    assert result == CheckResult(
        "unsub_token_keys",
        Status.OK,
        "the unsubscribe token keys of key id 1 (unsub/k_tok/1 and unsub/k_edge/1) are set "
        "and differ",
    )
    assert keys.order == ["active_kid", "read unsub/k_tok/1", "read unsub/k_edge/1"]


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ({"unsub/k_tok/1": None}, "unsub/k_tok/1 is not set"),
        ({"unsub/k_edge/1": None}, "unsub/k_edge/1 is not set"),
        ({"unsub/k_tok/1": ""}, "unsub/k_tok/1 is not set"),
        ({"unsub/k_tok/1": K_TOK_TEXT[:-1]}, "unsub/k_tok/1 is not a 32-byte base64url key"),
        ({"unsub/k_edge/1": "+" * 43}, "unsub/k_edge/1 is not a 32-byte base64url key"),
        (
            {"unsub/k_tok/1": AdapterError(False, "keyring_failed", "read failed")},
            "unsub/k_tok/1 could not be read (Windows Credential Manager failed)",
        ),
        (
            {"unsub/k_edge/1": K_TOK_TEXT + "="},  # the same key, padded
            "unsub/k_tok/1 and unsub/k_edge/1 are the same key; make a new k_edge",
        ),
        (
            {"unsub/k_tok/1": None, "unsub/k_edge/1": None},
            "unsub/k_tok/1 is not set; unsub/k_edge/1 is not set",
        ),
    ],
    ids=[
        "k_tok_missing",
        "k_edge_missing",
        "k_tok_empty",
        "k_tok_short",
        "k_edge_standard_base64",
        "unreadable",
        "equal",
        "both_missing",
    ],
)
def test_a_missing_malformed_unreadable_or_equal_key_fails_naming_its_entry(
    entries: dict[str, object], problem: str
) -> None:
    result = UnsubKeys(**entries).check()

    assert result == CheckResult(
        "unsub_token_keys",
        Status.FAIL,
        f"unsubscribe token keys of key id 1: {problem}; enter them in Unsubscribe token keys "
        "on the Credentials page",
    )
    assert K_TOK_TEXT not in result.message and K_EDGE_TEXT not in result.message


def test_the_active_kid_picks_the_entries() -> None:
    keys = UnsubKeys(kid=7, **{"unsub/k_tok/7": K_EDGE_TEXT, "unsub/k_edge/7": K_TOK_TEXT})

    assert keys.check().status is Status.OK
    assert keys.order == ["active_kid", "read unsub/k_tok/7", "read unsub/k_edge/7"]
    assert "unsub/k_tok/2 is not set" in UnsubKeys(kid=2).check().message


@pytest.mark.parametrize(
    ("kid", "type_name"),
    [(RuntimeError("no settings table"), "RuntimeError"), (0, "ValueError"), ("1", "TypeError")],
)
def test_a_setting_that_cant_be_read_fails_with_no_keyring_read(
    kid: object, type_name: str
) -> None:
    keys = UnsubKeys(kid=kid)

    result = keys.check()

    assert result == CheckResult(
        "unsub_token_keys",
        Status.FAIL,
        f"the Unsubscribe token key id setting (unsub_active_kid) could not be read "
        f"({type_name}); set it on the Settings page",
    )
    assert keys.order == ["active_kid"]


# unsub_worker (Story 3.12) --------------------------------------------------------------------

WORKER_HOST = "u.example-sender.com"
WORKER_BEARER = "Ub4Xq8Lm2Vz6Np1Rt5Hk9Jc3Wd7Gs0Fy4Tb8Mn2Qe6Ah"


class Worker:
    """The ``unsub_worker`` check's inputs: the setting, the keyring and the probe, recording
    the order they are used in."""

    def __init__(
        self,
        host: object = WORKER_HOST,
        bearer: str | None | AdapterError = WORKER_BEARER,
        answer: AdapterError | None = None,
    ) -> None:
        self.host, self.bearer, self.answer = host, bearer, answer
        self.order: list[str] = []
        self.probed: list[str] = []

    def read_host(self) -> object:
        self.order.append("host")
        if isinstance(self.host, BaseException):
            raise self.host
        return self.host

    def read(self, name: str) -> str | None:
        self.order.append(name)
        if isinstance(self.bearer, AdapterError):
            raise self.bearer
        return self.bearer

    def probe(self, host: str) -> None:
        self.order.append("probe")
        self.probed.append(host)
        if self.answer is not None:
            raise self.answer

    def check(self, *, in_engine: bool = False) -> CheckResult:
        return checks.check_unsub_worker(
            self.read, self.read_host, self.probe, in_engine=in_engine, deadline_s=10.0
        )


def test_unsub_worker_is_ok_and_off_with_no_host_reading_nothing_else() -> None:
    worker = Worker(host="")

    assert worker.check() == CheckResult(
        "unsub_worker", Status.OK, "unsubscribe polling is off (no Unsub Worker host set)"
    )
    assert worker.order == ["host"]


@pytest.mark.parametrize("bearer", [None, ""])
def test_unsub_worker_fails_when_polling_is_on_and_the_bearer_is_not_set(
    bearer: str | None,
) -> None:
    worker = Worker(bearer=bearer)

    assert worker.check() == CheckResult(
        "unsub_worker",
        Status.FAIL,
        "unsubscribe polling on but bearer not set; enter the Unsubscribe Endpoint bearer "
        "secret on the Credentials page",
    )
    assert worker.order == ["host", "unsub/bearer"]


def test_unsub_worker_fails_when_the_bearer_cannot_be_read() -> None:
    worker = Worker(bearer=AdapterError(True, "keyring_failed", "keyring"))

    result = worker.check()

    assert (result.status, worker.probed) == (Status.FAIL, [])
    assert "could not be read" in result.message


def test_unsub_worker_in_the_engine_never_probes() -> None:
    worker = Worker()

    assert worker.check(in_engine=True) == CheckResult(
        "unsub_worker",
        Status.OK,
        "unsubscribe polling on; the Worker is not probed in the engine (nextron doctor "
        "probes it)",
    )
    assert worker.probed == []


def test_unsub_worker_is_ok_when_the_probe_returns() -> None:
    worker = Worker()

    assert worker.check() == CheckResult(
        "unsub_worker", Status.OK, "the unsubscribe Worker is reachable"
    )
    assert worker.order == ["host", "unsub/bearer", "probe"]
    assert worker.probed == [WORKER_HOST]


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (AdapterError(True, "unsub_unreachable", "slow"), Status.WARN),
        (AdapterError(True, "unsub_unavailable", "down"), Status.WARN),
        (AdapterError(False, "unsub_unreachable", "slow"), Status.WARN),
        (AdapterError(False, "unsub_unauthorized", "refused"), Status.FAIL),
        (AdapterError(False, "unsub_bearer_invalid", "short"), Status.FAIL),
        (AdapterError(False, "unsub_bad_response", "garbage"), Status.FAIL),
    ],
)
def test_unsub_worker_warns_on_a_slow_or_down_worker_and_fails_on_a_refusal(
    answer: AdapterError, status: Status
) -> None:
    result = Worker(answer=answer).check()

    assert result.status is status
    assert answer.code in result.message
    if answer.code == "unsub_unreachable":
        assert "slower than 10 s" in result.message
    elif status is Status.WARN:
        assert "down, unreachable or rate-limited" in result.message
    assert WORKER_HOST not in result.message and WORKER_BEARER not in result.message


def test_unsub_worker_fails_when_the_host_cannot_be_read() -> None:
    worker = Worker(host=RuntimeError("no database"))

    assert worker.check() == CheckResult(
        "unsub_worker",
        Status.FAIL,
        "the Unsub Worker host setting (unsub_worker_host) could not be read (RuntimeError); "
        "set it on the Settings page",
    )
    assert worker.order == ["host"]
