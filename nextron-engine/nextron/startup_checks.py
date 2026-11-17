"""Startup checks (AD-2, AD-20). Composition-root helper: only ``nextron.main`` uses it.

Each check is a pure function over a ``Probes`` value, so tests inject fakes and never touch the
real SQLite library, timezone data, environment or network. ``Probes.system()`` builds the real
probes. No check makes a network call: the port probe binds a local socket and closes it.

``engine_is_running`` is the probe of ``nextron open``: it connects to the engine's port instead
of binding it, because a bind probe could take the port from an engine that is starting.

``prod_env_not_persistent`` (AD-14) runs only in ``doctor`` (``all_checks``), not before
``serve`` (``environment_checks``): the logon task's own ``prod`` start inherits any persistent
value, so refusing one at ``serve`` would also stop the real engine, not just the stray
processes it endangers. It fails when the persistent
user or machine environment (``HKCU\\Environment`` or
``HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment``) sets
``NEXTRON_ENV`` to ``prod`` (any case, spaces ignored), because then every process started on
the PC, a test run included, would be ``prod``. It also fails when the registry can't be read.
"""

import os
import socket
import sqlite3
import zoneinfo
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Final

from nextron.domain.sink import PROD

MIN_SQLITE_VERSION: Final = (3, 51, 3)
REQUIRED_ZONE: Final = "Europe/London"
ONEDRIVE_VARIABLES: Final = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")

SQLITE_VERSION: Final = "sqlite_version"
TZDATA: Final = "tzdata"
DATA_DIR_OUTSIDE_ONEDRIVE: Final = "data_dir_outside_onedrive"
PORT_FREE: Final = "port_free"
PROD_ENV_NOT_PERSISTENT: Final = "prod_env_not_persistent"
ENGINE_PROBE_TIMEOUT_S: Final = 1.0

ENV_VARIABLE: Final = "NEXTRON_ENV"
PERSISTENT_PROD: Final = "NEXTRON_ENV=prod must only be set by the logon task"
USER_SCOPE: Final = "user"
MACHINE_SCOPE: Final = "machine"
USER_ENVIRONMENT_KEY: Final = "Environment"  # under HKEY_CURRENT_USER
MACHINE_ENVIRONMENT_KEY: Final = (  # under HKEY_LOCAL_MACHINE
    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one check; ``detail`` explains it in one line."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Probes:
    """Everything the checks read from the machine, injectable for tests."""

    sqlite_version: Callable[[], str]
    load_zone: Callable[[str], object]
    environ: Mapping[str, str]
    port_is_free: Callable[[str, int], bool]
    # name -> its persistent value by scope ("user", "machine"); an absent scope is unset
    persistent_variable: Callable[[str], Mapping[str, str]]

    @classmethod
    def system(cls) -> Probes:
        return cls(
            sqlite_version=lambda: sqlite3.sqlite_version,
            load_zone=zoneinfo.ZoneInfo,
            environ=os.environ,
            port_is_free=socket_port_is_free,
            persistent_variable=read_persistent_variable,
        )


def socket_port_is_free(host: str, port: int) -> bool:
    """Return whether nothing else holds ``host:port``.

    On Windows the probe socket asks for exclusive use, so the bind fails even when the other
    holder set ``SO_REUSEADDR`` (which Uvicorn does, and which would otherwise let two servers
    share the port).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def engine_is_running(host: str, port: int) -> bool:
    """Whether something accepts TCP connections on ``host:port`` within 1 second."""
    try:
        with socket.create_connection((host, port), timeout=ENGINE_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def read_persistent_variable(name: str, *, registry: Any = None) -> dict[str, str]:
    """The persistent values of the environment variable ``name``, by scope: ``user``
    (``HKCU\\Environment``) and ``machine`` (``HKLM\\...\\Session Manager\\Environment``).

    ``registry`` is ``winreg`` unless a test injects a stand-in; ``winreg`` is imported only
    here, when the probe runs. A missing key or value counts as absent, and so does a value that
    isn't a string (Windows builds the environment from string values only). Any other registry
    error propagates.
    """
    if registry is None:
        import winreg as registry  # noqa: PLC0415 - Windows-only, imported when probed
    scopes = (
        (USER_SCOPE, registry.HKEY_CURRENT_USER, USER_ENVIRONMENT_KEY),
        (MACHINE_SCOPE, registry.HKEY_LOCAL_MACHINE, MACHINE_ENVIRONMENT_KEY),
    )
    values: dict[str, str] = {}
    for scope, hive, path in scopes:
        try:
            with registry.OpenKey(hive, path) as key:
                value, _kind = registry.QueryValueEx(key, name)
        except FileNotFoundError:
            continue
        if isinstance(value, str):
            values[scope] = value
    return values


def _parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().split("."))


def check_sqlite_version(probes: Probes) -> CheckResult:
    required = ".".join(map(str, MIN_SQLITE_VERSION))
    try:
        actual = probes.sqlite_version()
        too_old = _parse_version(actual) < MIN_SQLITE_VERSION
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(SQLITE_VERSION, False, f"cannot read the SQLite version: {exc!r}")
    if too_old:
        return CheckResult(
            SQLITE_VERSION, False, f"SQLite {actual} is older than the required {required}"
        )
    return CheckResult(SQLITE_VERSION, True, f"SQLite {actual} (needs {required} or later)")


def check_tzdata(probes: Probes) -> CheckResult:
    try:
        probes.load_zone(REQUIRED_ZONE)
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(
            TZDATA,
            False,
            f'ZoneInfo("{REQUIRED_ZONE}") failed ({type(exc).__name__}); '
            "install the tzdata package with uv sync --frozen",
        )
    return CheckResult(TZDATA, True, f'ZoneInfo("{REQUIRED_ZONE}") loads')


def _parts(path: PurePath) -> list[str]:
    return [part.casefold().rstrip("\\/") for part in path.parts]


def _is_within(path: PurePath, folder: PurePath) -> bool:
    inner, outer = _parts(path), _parts(folder)
    return len(outer) > 0 and inner[: len(outer)] == outer


def check_data_dir_outside_onedrive(data_dir: PurePath, probes: Probes) -> CheckResult:
    """Fail when ``data_dir`` is inside a OneDrive folder (SQLite and sync clients don't mix).

    A folder counts as OneDrive when it is under ``%OneDrive%``, ``%OneDriveCommercial%`` or
    ``%OneDriveConsumer%``, or when any of its path segments starts with "OneDrive".
    """
    for variable in ONEDRIVE_VARIABLES:
        root = probes.environ.get(variable)
        if root and _is_within(data_dir, data_dir.with_segments(root)):
            return CheckResult(
                DATA_DIR_OUTSIDE_ONEDRIVE,
                False,
                f"data dir {data_dir} is inside %{variable}% ({root}); "
                "set NEXTRON_DATA_DIR to a folder outside OneDrive",
            )
    anchor = data_dir.anchor
    for segment in data_dir.parts:
        if segment != anchor and segment.casefold().startswith("onedrive"):
            return CheckResult(
                DATA_DIR_OUTSIDE_ONEDRIVE,
                False,
                f'data dir {data_dir} has the path segment "{segment}"; '
                "set NEXTRON_DATA_DIR to a folder outside OneDrive",
            )
    return CheckResult(DATA_DIR_OUTSIDE_ONEDRIVE, True, f"data dir {data_dir} is outside OneDrive")


def check_port_free(host: str, port: int, probes: Probes) -> CheckResult:
    try:
        free = probes.port_is_free(host, port)
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(PORT_FREE, False, f"cannot probe {host}:{port}: {exc!r}")
    if not free:
        return CheckResult(PORT_FREE, False, f"{host}:{port} is already in use by another process")
    return CheckResult(PORT_FREE, True, f"{host}:{port} is free")


def check_prod_env_not_persistent(probes: Probes) -> CheckResult:
    """Fail when ``NEXTRON_ENV`` is persistently ``prod`` in the user or machine environment,
    or when the registry can't be read; see the module docstring."""
    try:
        values = probes.persistent_variable(ENV_VARIABLE)
        scopes = [
            scope for scope, value in values.items() if value.strip().casefold() == PROD
        ]
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(
            PROD_ENV_NOT_PERSISTENT,
            False,
            f"cannot read the persistent {ENV_VARIABLE} from the registry "
            f"({type(exc).__name__}); check it in System Properties > Environment Variables",
        )
    if scopes:
        where = f"{' and '.join(scopes)} environment{'s' if len(scopes) > 1 else ''}"
        return CheckResult(
            PROD_ENV_NOT_PERSISTENT,
            False,
            f"{PERSISTENT_PROD}, but it is set persistently in the {where}; delete it in "
            "System Properties > Environment Variables",
        )
    return CheckResult(
        PROD_ENV_NOT_PERSISTENT,
        True,
        f"no persistent user or machine {ENV_VARIABLE}=prod (only the logon task sets prod)",
    )


def environment_checks(data_dir: PurePath, probes: Probes) -> list[CheckResult]:
    """The checks that run before the instance lock: SQLite, tzdata and OneDrive."""
    return [
        check_sqlite_version(probes),
        check_tzdata(probes),
        check_data_dir_outside_onedrive(data_dir, probes),
    ]


def all_checks(data_dir: PurePath, host: str, port: int, probes: Probes) -> list[CheckResult]:
    """Every startup check, in the order ``doctor`` prints them. ``serve`` runs the same
    checks except ``prod_env_not_persistent`` (see the module docstring)."""
    return [
        *environment_checks(data_dir, probes),
        check_prod_env_not_persistent(probes),
        check_port_free(host, port, probes),
    ]
