"""Startup and readiness checks (AD-2, AD-14, AD-20, FR-56). Composition-root helper: only
``nextron.main`` uses it. It may import ``nextron.domain``, ``nextron.ports`` and the standard
library, never ``nextron.db`` or ``nextron.app``: the checks that read the database or Google
get callables that ``main`` wires.

Each check is a pure function over a ``Probes`` value or over injected callables, so tests
inject fakes and never touch the real SQLite library, timezone data, environment, Credential
Manager, database or network. ``Probes.system()`` builds the real probes. The port probe binds a
local socket and closes it.

Each check returns a ``CheckResult(id, status, message)``: ``status`` is a ``Status`` (``ok``,
``warn`` or ``fail``) and ``message`` one line, safe to print. Both types live in
``nextron.domain.readiness`` (the health panel shows the same results) and are imported here,
so ``from nextron.startup_checks import CheckResult, Status`` still works. No message holds a
secret value, an account address or an exception's text: only the exception's type name, or
the message of a ``CheckFailed``, which a probe raises with text that is safe to print.

``engine_is_running`` is the probe of ``nextron open``: it connects to the engine's port instead
of binding it, because a bind probe could take the port from an engine that is starting.

``serve`` runs ``environment_checks`` (SQLite, tzdata, OneDrive) before the instance lock and
``check_port_free`` after it, and stops on the first result whose status is ``fail``.

``prod_env_not_persistent`` (AD-14) runs only in ``doctor``, not before ``serve``: the logon
task's own ``prod`` start inherits any persistent value, so refusing one at ``serve`` would also
stop the real engine, not just the stray processes it endangers. It fails when the persistent
user or machine environment (``HKCU\\Environment`` or
``HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment``) sets
``NEXTRON_ENV`` to ``prod`` (any case, spaces ignored), because then every process started on
the PC, a test run included, would be ``prod``. It also fails when the registry can't be read.

**Doctor.** ``nextron doctor`` runs a registry of checks:

- ``DoctorCheck[C](id, run)`` is one check: ``id`` matches ``[a-z][a-z0-9_]{0,63}`` and
  ``run(context)`` returns its ``CheckResult``; anything else raises ``TypeError``.
- ``DoctorChecks[C]`` is the registry, generic over the context ``C`` its checks read:
  ``register_check(check)`` (a repeated id raises ``ValueError``; registering after ``freeze()``
  raises ``RuntimeError``), ``freeze()``, ``frozen`` and ``checks`` (a tuple in registration
  order). ``nextron.main.build_doctor_checks`` is the only code that constructs one or calls
  ``register_check`` (``tests/contracts/test_doctor_contracts.py``); later stories add their
  checks there.
- ``run_checks(checks, context)`` needs a frozen registry (``RuntimeError`` otherwise) and runs
  every check in order. A check that raises gives ``fail`` "the check crashed
  (<ExceptionType>)"; a result that isn't a ``CheckResult`` with the registered id (and a
  ``Status`` and a ``str`` message) gives ``fail`` "the check returned no result". The other
  checks still run either way. Line breaks in a message become spaces.
- ``format_table(results)`` is a header (``status  check  message``, ``check`` padded to the
  longest id), one row per result and the line ``<n> ok, <n> warn, <n> fail``;
  ``format_json(results)`` is ``[{"id", "status", "message"}, ...]`` as indented, ASCII-only
  JSON.

The doctor checks here, in the order ``doctor`` runs them:

- ``python_version``: Python 3.14.8 or later.
- ``sqlite_version``: SQLite 3.51.3 or later.
- ``tzdata``: ``ZoneInfo("Europe/London")`` loads.
- ``prod_env_not_persistent``: see above; the message names the environment and where it came
  from (``--env`` or ``NEXTRON_ENV``).
- ``data_dir_outside_onedrive``: the data dir is outside OneDrive.
- ``backup_dir_separate``: the backup dir is not the data dir, neither contains the other
  (case-insensitive path parts), and it is outside OneDrive (the data dir's rules).
- ``port_free``: the port is free, or it is busy and ``<data dir>\\heartbeat`` is under 2 minutes
  old (``HEARTBEAT_FRESH_S``, which is ``nextron.domain.health.HEARTBEAT_FRESH`` in seconds), so
  this environment's running engine holds it. A ``file_age`` error counts as no heartbeat. A
  hung engine therefore shows as "in use". In the engine (``in_engine``, the health panel) it
  probes nothing and is ``ok``: the engine serving the page holds the port.
- ``required_credentials``: every required credential (``nextron.domain.credentials``) is set,
  with at most ``MAX_SECRET_LENGTH`` UTF-16 units; each value is read and dropped at once. It
  fails listing the labels that are not set, too long or unreadable.
- ``internal_notify_grant``: ``access_token("internal_notify")`` returns. ``warn`` when Google
  can't be reached (a retryable ``AdapterError``); ``fail`` when the client is not imported, the
  grant is missing or needs re-consent, Credential Manager fails, or any other error (by code).
  In the engine (``refresh=False``, the health panel) the callable is ``check_stored_grant``,
  which reads only what is stored and calls no one, so a GET never refreshes the grant: its
  ``ok`` says the panel doesn't refresh it (``nextron doctor`` does).
- ``offpc_backup`` (Story 1.17, OQ-A3): the encrypted off-PC backup is configured. It reads the
  Backup passphrase's state first (``backup/passphrase``, through Credential Manager; the value
  is dropped at once), then the ``backup_offpc_dir`` setting (``offpc_dir()``, the database),
  and touches no file or folder. It warns ``off-PC backup not configured (OQ-A3): backups are
  local only; <reasons>``, giving every reason with its fix: the folder is blank, the data dir
  or the backup dir (or inside either), or can't be read (by type name), or the passphrase is
  not set, too long or unreadable. It warns when the folder is on the data dir's drive
  (``nextron.domain.backup.same_volume``: a lost disk would take both); otherwise it is
  ``ok``.
- ``erasure_ledger`` (Story 3.16): ``<backup dir>\\erasure-ledger.jsonl``, the erasure ledger,
  exists and is writable (``os.access(W_OK)``): ``ok``. It fails when the file is missing while
  the database exists (start the engine to create it; after a restore, put the ledger's copy
  there, since recovery mode holds sending until it is reapplied) or when it isn't writable;
  it warns when it is missing and there is no database yet ("created at the first engine
  start"). It never opens the file.
- ``required_settings``: every required setting is set and parses; it fails listing
  ``<label> (<key>)`` for each one that isn't, or naming the error type when the settings can't
  be read.
- ``database_revision``: the database is at the Alembic head. ``warn`` "will migrate on next
  start" with no database file, an empty one or a known revision behind head; ``fail`` for a
  revision this code doesn't know ("newer than this code"), a ``CheckFailed`` (its message) or
  any other error (its type name).
- ``unsub_token_keys`` (Story 3.8, AD-15): the active kid's unsubscribe token keys work. It
  reads the ``unsub_active_kid`` setting first (``active_kid()``, the database: the schema
  default with no database file), then ``unsub/k_tok/<kid>`` and ``unsub/k_edge/<kid>`` through
  Credential Manager (each value is dropped at once). It fails when the setting can't be read
  (by type name) or isn't 1 to 255, or when either key is missing, isn't a 32-byte base64url
  key (``nextron.domain.unsub_token.parse_key``) or can't be read, naming each such keyring
  entry, or when the two are the same key (Cloudflare would then hold the decryption key);
  otherwise it is ``ok``.
- ``unsub_worker`` (Story 3.12, FR-70, AD-15): the unsubscribe Worker answers. It reads the
  ``unsub_worker_host`` setting first (``host()``): blank is ``ok``, "polling is off"; a read
  error fails by type name. Then the bearer's keyring entry (``unsub/bearer``, the value
  dropped at once): missing or blank fails, "polling on but bearer not set"; unreadable fails.
  With ``in_engine`` it is then ``ok``, "not probed in the engine", with no network call.
  Otherwise ``probe(host)`` (``UnsubscribePort.probe`` with ``DOCTOR_DEADLINE_S``) returning is
  ``ok``, "reachable"; a retryable ``AdapterError`` or ``unsub_unreachable`` (an answer slower
  than the deadline) is ``warn``; any other ``AdapterError`` fails. Messages carry codes only,
  never the host, the bearer or a body.
"""

import hmac
import json
import os
import re
import socket
import sqlite3
import sys
import time
import zoneinfo
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePath
from typing import Any, Final

from nextron.domain import credentials
from nextron.domain.backup import (
    FOLDER_IN_BACKUP_DIR,
    FOLDER_IN_DATA_DIR,
    FOLDER_NOT_SET,
    drive_of,
    offpc_problem,
    same_volume,
)
from nextron.domain.credentials import CredentialKey, UnsubKey, declared_unsub_keys, unsub_key
from nextron.domain.google_oauth import GoogleGrant
from nextron.domain.health import HEARTBEAT_FRESH
from nextron.domain.readiness import CheckResult, Status, tally
from nextron.domain.settings_schema import setting
from nextron.domain.sink import PROD
from nextron.domain.unsub_token import check_kid, parse_key
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import (
    GOOGLE_CLIENT_NOT_IMPORTED,
    GOOGLE_INVALID_GRANT,
    GOOGLE_NEEDS_RECONSENT,
    GOOGLE_NOT_GRANTED,
)
from nextron.ports.secrets import KEYRING_FAILED, MAX_SECRET_LENGTH, secret_length

MIN_PYTHON_VERSION: Final = (3, 14, 8)
MIN_SQLITE_VERSION: Final = (3, 51, 3)
REQUIRED_ZONE: Final = "Europe/London"
ONEDRIVE_VARIABLES: Final = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
HEARTBEAT_FRESH_S: Final = HEARTBEAT_FRESH.total_seconds()  # the panel's "running" rule

PYTHON_VERSION: Final = "python_version"
SQLITE_VERSION: Final = "sqlite_version"
TZDATA: Final = "tzdata"
PROD_ENV_NOT_PERSISTENT: Final = "prod_env_not_persistent"
DATA_DIR_OUTSIDE_ONEDRIVE: Final = "data_dir_outside_onedrive"
BACKUP_DIR_SEPARATE: Final = "backup_dir_separate"
PORT_FREE: Final = "port_free"
REQUIRED_CREDENTIALS: Final = "required_credentials"
INTERNAL_NOTIFY_GRANT: Final = "internal_notify_grant"
OFFPC_BACKUP: Final = "offpc_backup"
ERASURE_LEDGER: Final = "erasure_ledger"
REQUIRED_SETTINGS: Final = "required_settings"
DATABASE_REVISION: Final = "database_revision"
UNSUB_TOKEN_KEYS: Final = "unsub_token_keys"
UNSUB_WORKER: Final = "unsub_worker"
ENGINE_PROBE_TIMEOUT_S: Final = 1.0

ENV_VARIABLE: Final = "NEXTRON_ENV"
PERSISTENT_PROD: Final = "NEXTRON_ENV=prod must only be set by the logon task"
USER_SCOPE: Final = "user"
MACHINE_SCOPE: Final = "machine"
USER_ENVIRONMENT_KEY: Final = "Environment"  # under HKEY_CURRENT_USER
MACHINE_ENVIRONMENT_KEY: Final = (  # under HKEY_LOCAL_MACHINE
    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
)

CHECK_ID_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")  # matched with fullmatch
CHECK_CRASHED: Final = "the check crashed ({})"
NO_RESULT: Final = "the check returned no result"
WILL_MIGRATE: Final = "will migrate on next start"
NEWER_THAN_CODE: Final = "is newer than this code"


class CheckFailed(Exception):
    """A probe failed; the message is safe to print, and a check shows it as it is."""


@dataclass(frozen=True, slots=True)
class Probes:
    """Everything the checks read from the machine, injectable for tests."""

    sqlite_version: Callable[[], str]
    load_zone: Callable[[str], object]
    environ: Mapping[str, str]
    port_is_free: Callable[[str, int], bool]
    # name -> its persistent value by scope ("user", "machine"); an absent scope is unset
    persistent_variable: Callable[[str], Mapping[str, str]]
    python_version: Callable[[], tuple[int, ...]]
    # seconds since the file's last change, or None when the file is missing
    file_age: Callable[[PurePath], float | None]

    @classmethod
    def system(cls) -> Probes:
        return cls(
            sqlite_version=lambda: sqlite3.sqlite_version,
            load_zone=zoneinfo.ZoneInfo,
            environ=os.environ,
            port_is_free=socket_port_is_free,
            persistent_variable=read_persistent_variable,
            python_version=lambda: tuple(sys.version_info[:3]),
            file_age=file_age_seconds,
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


def file_age_seconds(path: PurePath) -> float | None:
    """Seconds since ``path`` last changed, or ``None`` when it is missing; any other
    ``OSError`` propagates."""
    try:
        changed = os.stat(path).st_mtime
    except FileNotFoundError:
        return None
    return time.time() - changed


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


def _shown(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


# The checks -----------------------------------------------------------------------------------


def check_python_version(probes: Probes) -> CheckResult:
    required = _shown(MIN_PYTHON_VERSION)
    try:
        actual = tuple(int(part) for part in probes.python_version())
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(
            PYTHON_VERSION,
            Status.FAIL,
            f"cannot read the Python version ({type(exc).__name__})",
        )
    if actual < MIN_PYTHON_VERSION:
        return CheckResult(
            PYTHON_VERSION,
            Status.FAIL,
            f"Python {_shown(actual)} is older than the required {required}; "
            "run uv sync --frozen",
        )
    return CheckResult(
        PYTHON_VERSION, Status.OK, f"Python {_shown(actual)} (needs {required} or later)"
    )


def check_sqlite_version(probes: Probes) -> CheckResult:
    required = _shown(MIN_SQLITE_VERSION)
    try:
        actual = probes.sqlite_version()
        too_old = _parse_version(actual) < MIN_SQLITE_VERSION
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(
            SQLITE_VERSION, Status.FAIL, f"cannot read the SQLite version ({type(exc).__name__})"
        )
    if too_old:
        return CheckResult(
            SQLITE_VERSION, Status.FAIL, f"SQLite {actual} is older than the required {required}"
        )
    return CheckResult(
        SQLITE_VERSION, Status.OK, f"SQLite {actual} (needs {required} or later)"
    )


def check_tzdata(probes: Probes) -> CheckResult:
    try:
        probes.load_zone(REQUIRED_ZONE)
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(
            TZDATA,
            Status.FAIL,
            f'ZoneInfo("{REQUIRED_ZONE}") failed ({type(exc).__name__}); '
            "install the tzdata package with uv sync --frozen",
        )
    return CheckResult(TZDATA, Status.OK, f'ZoneInfo("{REQUIRED_ZONE}") loads')


def _parts(path: PurePath) -> list[str]:
    return [part.casefold().rstrip("\\/") for part in path.parts]


def _is_within(path: PurePath, folder: PurePath) -> bool:
    """Whether ``path`` is ``folder`` or inside it, comparing case-insensitive path parts."""
    inner, outer = _parts(path), _parts(folder)
    return len(outer) > 0 and inner[: len(outer)] == outer


def _onedrive_reason(path: PurePath, probes: Probes) -> str | None:
    """Why ``path`` counts as inside OneDrive, or ``None`` when it doesn't.

    A folder counts as OneDrive when it is under ``%OneDrive%``, ``%OneDriveCommercial%`` or
    ``%OneDriveConsumer%``, or when any of its path segments starts with "OneDrive".
    """
    for variable in ONEDRIVE_VARIABLES:
        root = probes.environ.get(variable)
        if root and _is_within(path, path.with_segments(root)):
            return f"is inside %{variable}% ({root})"
    anchor = path.anchor
    for segment in path.parts:
        if segment != anchor and segment.casefold().startswith("onedrive"):
            return f'has the path segment "{segment}"'
    return None


def check_data_dir_outside_onedrive(data_dir: PurePath, probes: Probes) -> CheckResult:
    """Fail when ``data_dir`` is inside a OneDrive folder (SQLite and sync clients don't mix)."""
    reason = _onedrive_reason(data_dir, probes)
    if reason is not None:
        return CheckResult(
            DATA_DIR_OUTSIDE_ONEDRIVE,
            Status.FAIL,
            f"data dir {data_dir} {reason}; set NEXTRON_DATA_DIR to a folder outside OneDrive",
        )
    return CheckResult(
        DATA_DIR_OUTSIDE_ONEDRIVE, Status.OK, f"data dir {data_dir} is outside OneDrive"
    )


def check_backup_dir_separate(
    data_dir: PurePath, backup_dir: PurePath, probes: Probes
) -> CheckResult:
    """Fail when the backup dir is the data dir, either contains the other, or it is inside
    OneDrive: a backup must survive losing the data dir, and a plaintext copy synced by
    OneDrive would leave the PC unencrypted."""
    fix = "set NEXTRON_BACKUP_DIR to a separate folder outside OneDrive"
    inside_data = _is_within(backup_dir, data_dir)
    holds_data = _is_within(data_dir, backup_dir)
    if inside_data and holds_data:
        problem = f"backup dir {backup_dir} is the data dir"
    elif inside_data:
        problem = f"backup dir {backup_dir} is inside the data dir {data_dir}"
    elif holds_data:
        problem = f"backup dir {backup_dir} contains the data dir {data_dir}"
    elif (reason := _onedrive_reason(backup_dir, probes)) is not None:
        problem = f"backup dir {backup_dir} {reason}"
    else:
        return CheckResult(
            BACKUP_DIR_SEPARATE,
            Status.OK,
            f"backup dir {backup_dir} is separate from the data dir and outside OneDrive",
        )
    return CheckResult(BACKUP_DIR_SEPARATE, Status.FAIL, f"{problem}; {fix}")


def check_port_free(
    host: str,
    port: int,
    probes: Probes,
    *,
    heartbeat: PurePath | None = None,
    in_engine: bool = False,
) -> CheckResult:
    """``ok`` when ``host:port`` is free. ``serve`` passes no ``heartbeat``, so a busy port
    fails. ``doctor`` passes ``<data dir>\\heartbeat``: a busy port is then ``ok`` when that file
    is under ``HEARTBEAT_FRESH_S`` seconds old (this environment's running engine holds it), and
    a ``file_age`` error counts as no heartbeat. The health panel passes ``in_engine``: the
    engine serving the page holds the port, so nothing is probed and the result is ``ok``."""
    if in_engine:
        return CheckResult(
            PORT_FREE, Status.OK, f"{host}:{port} is held by this engine, which serves this page"
        )
    try:
        free = probes.port_is_free(host, port)
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(
            PORT_FREE, Status.FAIL, f"cannot probe {host}:{port} ({type(exc).__name__})"
        )
    if free:
        return CheckResult(PORT_FREE, Status.OK, f"{host}:{port} is free")
    if heartbeat is None:
        return CheckResult(
            PORT_FREE, Status.FAIL, f"{host}:{port} is already in use by another process"
        )
    try:
        age = probes.file_age(heartbeat)
        # abs: a heartbeat from the future (the clock was set back) is not fresh either
        fresh = age is not None and abs(age) < HEARTBEAT_FRESH_S
    except Exception:  # noqa: BLE001 - an unreadable heartbeat counts as none
        age, fresh = None, False
    if fresh:
        return CheckResult(
            PORT_FREE,
            Status.OK,
            f"{host}:{port} is held by this environment's running engine "
            f"(its heartbeat is {max(age or 0.0, 0.0):.0f} s old)",
        )
    minutes = f"{HEARTBEAT_FRESH_S / 60:g} minutes"
    return CheckResult(
        PORT_FREE,
        Status.FAIL,
        f"{host}:{port} is already in use, and not by this environment's running engine: "
        f"{heartbeat} is missing or not within the last {minutes} (a hung engine shows the "
        "same)",
    )


def check_prod_env_not_persistent(
    probes: Probes, env: str | None = None, env_source: str | None = None
) -> CheckResult:
    """Fail when ``NEXTRON_ENV`` is persistently ``prod`` in the user or machine environment,
    or when the registry can't be read; see the module docstring. With ``env`` and
    ``env_source`` (``--env`` or ``NEXTRON_ENV``), the message also names this run's
    environment and where it came from."""
    run = f"; this run is {env}, from {env_source}" if env is not None else ""
    try:
        values = probes.persistent_variable(ENV_VARIABLE)
        scopes = [
            scope for scope, value in values.items() if value.strip().casefold() == PROD
        ]
    except Exception as exc:  # noqa: BLE001 - any probe failure fails the check
        return CheckResult(
            PROD_ENV_NOT_PERSISTENT,
            Status.FAIL,
            f"cannot read the persistent {ENV_VARIABLE} from the registry "
            f"({type(exc).__name__}); check it in System Properties > Environment Variables"
            f"{run}",
        )
    if scopes:
        where = f"{' and '.join(scopes)} environment{'s' if len(scopes) > 1 else ''}"
        return CheckResult(
            PROD_ENV_NOT_PERSISTENT,
            Status.FAIL,
            f"{PERSISTENT_PROD}, but it is set persistently in the {where}; delete it in "
            f"System Properties > Environment Variables{run}",
        )
    return CheckResult(
        PROD_ENV_NOT_PERSISTENT,
        Status.OK,
        f"no persistent user or machine {ENV_VARIABLE}=prod (only the logon task sets prod)"
        f"{run}",
    )


def _credential_state(read: Callable[[str], str | None], name: str) -> str:
    """``set``, ``not set``, ``too long`` or ``unreadable``; the value is dropped on return."""
    try:
        value = read(name)
    except AdapterError:
        return "unreadable"
    if not value:
        return "not set"
    return "too long" if secret_length(value) > MAX_SECRET_LENGTH else "set"


def check_required_credentials(read: Callable[[str], str | None]) -> CheckResult:
    """``ok`` when every required credential is set with at most ``MAX_SECRET_LENGTH`` UTF-16
    units; ``read`` is ``SecretsPort.read``. Fails listing the labels that are not."""
    required = [found for found in credentials.declared() if found.required]
    labels: dict[str, list[str]] = {"not set": [], "too long": [], "unreadable": []}
    for found in required:
        state = _credential_state(read, found.secret_name)
        if state != "set":
            labels[state].append(found.label)
    problems = [
        f"{heading}: {', '.join(names)}"
        for heading, names in (
            ("not set", labels["not set"]),
            (f"over {MAX_SECRET_LENGTH:,} characters", labels["too long"]),
            ("unreadable (Windows Credential Manager failed)", labels["unreadable"]),
        )
        if names
    ]
    if problems:
        return CheckResult(
            REQUIRED_CREDENTIALS,
            Status.FAIL,
            f"required credentials {'; '.join(problems)}; enter them on the Credentials page",
        )
    return CheckResult(
        REQUIRED_CREDENTIALS,
        Status.OK,
        f"all {len(required)} required credentials are set, each {MAX_SECRET_LENGTH:,} "
        "characters or fewer",
    )


def check_internal_notify_grant(
    access: Callable[[str], object], *, refresh: bool = True
) -> CheckResult:
    """``ok`` when ``access("internal_notify")`` returns (whatever it returns is dropped at
    once); ``warn`` when Google can't be reached; ``fail`` otherwise, by error code.

    ``doctor`` passes ``GoogleAccess.access_token``, which refreshes the grant at Google. The
    health panel passes ``refresh=False`` with ``GoogleAccess.check_stored_grant``, which only
    reads what is stored and calls no one, and its ``ok`` says so."""
    grant = str(GoogleGrant.INTERNAL_NOTIFY)
    on_page = "in the Google section of the Credentials page"
    try:
        access(grant)
    except AdapterError as failed:
        code = failed.code
        if failed.retryable:
            return CheckResult(
                INTERNAL_NOTIFY_GRANT,
                Status.WARN,
                f"Google could not be reached to refresh {grant} ({code}); run doctor again "
                "later",
            )
        if code == GOOGLE_CLIENT_NOT_IMPORTED:
            message = f"the Google Desktop client is not imported; import it {on_page}"
        elif code == GOOGLE_NOT_GRANTED:
            message = f"{grant} is not granted; grant it {on_page}"
        elif code in (GOOGLE_NEEDS_RECONSENT, GOOGLE_INVALID_GRANT):
            message = (
                f"{grant} needs re-consent: Google refused the stored grant; grant it again "
                f"{on_page}"
            )
        elif code == KEYRING_FAILED:
            message = f"Windows Credential Manager could not be read ({code})"
        else:
            message = f"{grant} could not be refreshed ({code})"
        return CheckResult(INTERNAL_NOTIFY_GRANT, Status.FAIL, message)
    if not refresh:
        return CheckResult(
            INTERNAL_NOTIFY_GRANT,
            Status.OK,
            f"{grant} is granted; the health panel doesn't refresh it (nextron doctor does)",
        )
    return CheckResult(
        INTERNAL_NOTIFY_GRANT,
        Status.OK,
        f"{grant} is granted and Google refreshed its access token",
    )


UNSUB_KEYS_FIX: Final = "enter them in Unsubscribe token keys on the Credentials page"


def check_unsub_token_keys(
    read: Callable[[str], str | None], active_kid: Callable[[], object]
) -> CheckResult:
    """``ok`` when the active kid's ``k_tok`` and ``k_edge`` are set, are 32-byte base64url
    keys and differ; ``fail`` otherwise, naming each keyring entry at fault. ``active_kid``
    reads the ``unsub_active_kid`` setting (called first); ``read`` is ``SecretsPort.read``."""
    try:
        kid = check_kid(active_kid())
    except Exception as exc:  # noqa: BLE001 - any read failure fails the check, not a crash
        return CheckResult(
            UNSUB_TOKEN_KEYS,
            Status.FAIL,
            f"the Unsubscribe token key id setting (unsub_active_kid) could not be read "
            f"({type(exc).__name__}); set it on the Settings page",
        )
    problems: list[str] = []
    keys: dict[str, bytes] = {}
    for found in declared_unsub_keys():
        name = found.secret_name(kid)
        try:
            text = read(name)
        except AdapterError:
            problems.append(f"{name} could not be read (Windows Credential Manager failed)")
            continue
        raw = parse_key(text)
        if not text:
            problems.append(f"{name} is not set")
        elif raw is None:
            problems.append(f"{name} is not a 32-byte base64url key")
        else:
            keys[found.key] = raw
        del text  # the value isn't kept
    k_tok, k_edge = keys.get(UnsubKey.K_TOK), keys.get(UnsubKey.K_EDGE)
    if k_tok is not None and k_edge is not None and hmac.compare_digest(k_tok, k_edge):
        problems.append(
            f"{unsub_key(UnsubKey.K_TOK).secret_name(kid)} and "
            f"{unsub_key(UnsubKey.K_EDGE).secret_name(kid)} are the same key; make a new k_edge"
        )
    if problems:
        return CheckResult(
            UNSUB_TOKEN_KEYS,
            Status.FAIL,
            f"unsubscribe token keys of key id {kid}: {'; '.join(problems)}; {UNSUB_KEYS_FIX}",
        )
    names = " and ".join(found.secret_name(kid) for found in declared_unsub_keys())
    return CheckResult(
        UNSUB_TOKEN_KEYS,
        Status.OK,
        f"the unsubscribe token keys of key id {kid} ({names}) are set and differ",
    )


UNSUB_UNREACHABLE: Final = "unsub_unreachable"  # nextron.ports.unsubscribe.UNSUB_UNREACHABLE
UNSUB_BEARER_FIX: Final = "enter the Unsubscribe Endpoint bearer secret on the Credentials page"


def check_unsub_worker(
    read: Callable[[str], str | None],
    host: Callable[[], object],
    probe: Callable[[str], None],
    *,
    in_engine: bool,
    deadline_s: float,
) -> CheckResult:
    """Whether the unsubscribe Worker answers; see the module docstring. ``host`` reads the
    ``unsub_worker_host`` setting (called first), ``read`` is ``SecretsPort.read`` and ``probe``
    calls ``UnsubscribePort.probe`` with the deadline."""
    try:
        found = str(host())
    except Exception as exc:  # noqa: BLE001 - any read failure fails the check, not a crash
        return CheckResult(
            UNSUB_WORKER,
            Status.FAIL,
            f"the Unsub Worker host setting (unsub_worker_host) could not be read "
            f"({type(exc).__name__}); set it on the Settings page",
        )
    if not found:
        return CheckResult(
            UNSUB_WORKER, Status.OK, "unsubscribe polling is off (no Unsub Worker host set)"
        )
    state = _credential_state(
        read, credentials.credential(CredentialKey.UNSUBSCRIBE_BEARER).secret_name
    )
    if state == "unreadable":
        return CheckResult(
            UNSUB_WORKER,
            Status.FAIL,
            "unsubscribe polling on but the bearer could not be read (Windows Credential "
            "Manager failed); run doctor again",
        )
    if state == "not set":
        return CheckResult(
            UNSUB_WORKER,
            Status.FAIL,
            f"unsubscribe polling on but bearer not set; {UNSUB_BEARER_FIX}",
        )
    if in_engine:
        return CheckResult(
            UNSUB_WORKER,
            Status.OK,
            "unsubscribe polling on; the Worker is not probed in the engine (nextron doctor "
            "probes it)",
        )
    try:
        probe(found)
    except AdapterError as exc:
        if exc.code == UNSUB_UNREACHABLE:
            return CheckResult(
                UNSUB_WORKER,
                Status.WARN,
                f"the unsubscribe Worker was slower than {deadline_s:g} s ({exc.code}); "
                "polling retries every period",
            )
        if exc.retryable:
            return CheckResult(
                UNSUB_WORKER,
                Status.WARN,
                f"the unsubscribe Worker is down, unreachable or rate-limited ({exc.code}); "
                "polling retries every period",
            )
        return CheckResult(
            UNSUB_WORKER,
            Status.FAIL,
            f"the unsubscribe Worker refused the check ({exc.code}); check the bearer and the "
            "Unsub Worker host",
        )
    return CheckResult(UNSUB_WORKER, Status.OK, "the unsubscribe Worker is reachable")


OFFPC_NOT_CONFIGURED: Final = "off-PC backup not configured (OQ-A3): backups are local only"
_SETTINGS_FIX: Final = "set Off-PC backup folder (backup_offpc_dir) on the Settings page"
_ANOTHER_FOLDER: Final = (
    "choose a folder on another drive or a network share in Off-PC backup folder "
    "(backup_offpc_dir) on the Settings page"
)


def _offpc_folder_reason(folder: str, data_dir: PurePath, backup_dir: PurePath) -> str | None:
    problem = offpc_problem(folder, data_dir, backup_dir)
    if problem == FOLDER_NOT_SET:
        return f"no off-PC backup folder is set; {_SETTINGS_FIX}"
    if problem == FOLDER_IN_DATA_DIR:
        return f"the off-PC backup folder {folder} is in the data dir {data_dir}; {_ANOTHER_FOLDER}"
    if problem == FOLDER_IN_BACKUP_DIR:
        return (
            f"the off-PC backup folder {folder} is in the backup dir {backup_dir}; "
            f"{_ANOTHER_FOLDER}"
        )
    return None


def _passphrase_reason(state: str) -> str | None:
    on_page = "enter it on the Credentials page"
    if state == "not set":
        return f"the Backup passphrase is not set; {on_page}"
    if state == "too long":
        return f"the Backup passphrase is over {MAX_SECRET_LENGTH:,} characters; {on_page}"
    if state == "unreadable":
        return (
            "the Backup passphrase could not be read (Windows Credential Manager failed); "
            "run doctor again, or enter it again on the Credentials page"
        )
    return None


def check_offpc_backup(
    read: Callable[[str], str | None],
    offpc_dir: Callable[[], str],
    data_dir: PurePath,
    backup_dir: PurePath,
) -> CheckResult:
    """``ok`` when the off-PC folder is set (not in the data or backup dir, and on another
    drive than the data dir) and the Backup passphrase is set; ``warn`` otherwise, with every
    reason and its fix. ``read`` is ``SecretsPort.read`` (called first) and ``offpc_dir`` reads
    the ``backup_offpc_dir`` setting. No I/O on the folder."""
    passphrase = _credential_state(
        read, credentials.credential(CredentialKey.BACKUP_PASSPHRASE).secret_name
    )
    reasons: list[str] = []
    folder: str | None = None
    try:
        folder = str(offpc_dir())
    except Exception as exc:  # noqa: BLE001 - any read failure is a reason, not a crash
        reasons.append(
            f"the off-PC backup folder setting could not be read ({type(exc).__name__}); "
            f"{_SETTINGS_FIX}"
        )
    if folder is not None and (found := _offpc_folder_reason(folder, data_dir, backup_dir)):
        reasons.append(found)
    if (found := _passphrase_reason(passphrase)) is not None:
        reasons.append(found)
    if reasons or folder is None:  # an unread folder is always one of the reasons
        return CheckResult(
            OFFPC_BACKUP, Status.WARN, f"{OFFPC_NOT_CONFIGURED}; {'; '.join(reasons)}"
        )
    if same_volume(folder, data_dir):
        return CheckResult(
            OFFPC_BACKUP,
            Status.WARN,
            f"off-PC backup folder {folder} is on the same drive as the data dir "
            f"({drive_of(data_dir)}); a failed or lost disk would take both, so "
            f"{_ANOTHER_FOLDER}",
        )
    return CheckResult(
        OFFPC_BACKUP,
        Status.OK,
        f"off-PC backup folder {folder} is set and the Backup passphrase is set; each daily "
        "and manual backup is encrypted and copied there",
    )


def check_erasure_ledger(ledger: Path, database_exists: Callable[[], bool]) -> CheckResult:
    """``ok`` when the erasure ledger ``ledger`` exists and is writable; see the module
    docstring. It never opens the file."""
    if ledger.is_file():
        if os.access(ledger, os.W_OK):
            return CheckResult(
                ERASURE_LEDGER, Status.OK, f"erasure ledger {ledger} exists and is writable"
            )
        return CheckResult(
            ERASURE_LEDGER,
            Status.FAIL,
            f"erasure ledger {ledger} is not writable; make it writable for this user, or "
            "opt-outs and erasures are not mirrored",
        )
    try:
        has_database = database_exists()
    except Exception as exc:  # noqa: BLE001 - an unreadable answer is a failure, not a crash
        return CheckResult(
            ERASURE_LEDGER,
            Status.FAIL,
            f"erasure ledger {ledger} not found, and the database could not be checked "
            f"({type(exc).__name__})",
        )
    if not has_database:
        return CheckResult(
            ERASURE_LEDGER,
            Status.WARN,
            f"erasure ledger {ledger} not found yet; it is created at the first engine start",
        )
    return CheckResult(
        ERASURE_LEDGER,
        Status.FAIL,
        f"erasure ledger {ledger} not found; start the engine to create it, or after a "
        "restore put the ledger's copy there (recovery mode holds sending until it is "
        "reapplied)",
    )


def check_required_settings(required_unset: Callable[[], Collection[str]]) -> CheckResult:
    """``ok`` when ``required_unset()`` names no setting; fails listing ``<label> (<key>)``
    for each it names, or naming the error's type when the settings can't be read."""
    try:
        unset = list(required_unset())
    except Exception as exc:  # noqa: BLE001 - any read failure fails the check
        return CheckResult(
            REQUIRED_SETTINGS,
            Status.FAIL,
            f"the settings could not be read ({type(exc).__name__})",
        )
    if unset:
        names = ", ".join(f"{setting(key).label} ({key})" for key in unset)
        return CheckResult(
            REQUIRED_SETTINGS,
            Status.FAIL,
            f"required settings not set or no longer valid: {names}; set them on the "
            "Settings page",
        )
    return CheckResult(REQUIRED_SETTINGS, Status.OK, "every required setting is set and valid")


def check_database_revision(
    exists: bool,
    revision: Callable[[], str | None],
    *,
    head: str,
    known: Collection[str],
) -> CheckResult:
    """``ok`` at ``head``; ``warn`` with no file (``exists`` false), an empty database or a
    ``known`` revision behind head; ``fail`` for an unknown revision, a ``CheckFailed`` (its
    message) or any other error (its type name)."""
    if not exists:
        return CheckResult(
            DATABASE_REVISION,
            Status.WARN,
            "no database file yet; it will be created and migrated on next start",
        )
    try:
        current = revision()
    except CheckFailed as failed:
        return CheckResult(DATABASE_REVISION, Status.FAIL, str(failed))
    except Exception as exc:  # noqa: BLE001 - any read failure fails the check
        return CheckResult(
            DATABASE_REVISION,
            Status.FAIL,
            f"the database could not be read ({type(exc).__name__})",
        )
    if current is None:
        return CheckResult(
            DATABASE_REVISION, Status.WARN, f"the database is empty; it {WILL_MIGRATE}"
        )
    if current == head:
        return CheckResult(DATABASE_REVISION, Status.OK, f"the database is at head ({head})")
    if current in known:
        return CheckResult(
            DATABASE_REVISION,
            Status.WARN,
            f"the database is at {current}, behind {head}; it {WILL_MIGRATE}",
        )
    return CheckResult(
        DATABASE_REVISION,
        Status.FAIL,
        f"database revision {current} {NEWER_THAN_CODE}; run the newer code or restore a "
        "backup",
    )


def environment_checks(data_dir: PurePath, probes: Probes) -> list[CheckResult]:
    """The checks ``serve`` runs before the instance lock: SQLite, tzdata and OneDrive."""
    return [
        check_sqlite_version(probes),
        check_tzdata(probes),
        check_data_dir_outside_onedrive(data_dir, probes),
    ]


# The doctor check registry --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DoctorCheck[C]:
    """One doctor check: ``run(context)`` returns its ``CheckResult``, with this ``id``."""

    id: str
    run: Callable[[C], CheckResult]

    def __post_init__(self) -> None:
        if not (isinstance(self.id, str) and CHECK_ID_PATTERN.fullmatch(self.id)):
            raise TypeError("a doctor check's id must match [a-z][a-z0-9_]{0,63}")
        if not callable(self.run):
            raise TypeError(f"doctor check {self.id}: run must be callable")


class DoctorChecks[C]:
    """The doctor check registry; see the module docstring."""

    def __init__(self) -> None:
        self._checks: list[DoctorCheck[C]] = []
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def checks(self) -> tuple[DoctorCheck[C], ...]:
        """Every registered check, in registration order."""
        return tuple(self._checks)

    def register_check(self, check: DoctorCheck[C]) -> None:
        """Add ``check``; raises ``RuntimeError`` once frozen, ``TypeError`` for anything but
        a ``DoctorCheck`` and ``ValueError`` for an id already registered."""
        if self._frozen:
            raise RuntimeError("the doctor check registry is frozen")
        if not isinstance(check, DoctorCheck):
            raise TypeError("register a DoctorCheck")
        if any(existing.id == check.id for existing in self._checks):
            raise ValueError(f"doctor check {check.id} is already registered")
        self._checks.append(check)

    def freeze(self) -> None:
        """Refuse any further registration."""
        self._frozen = True


def _one_line(message: str) -> str:
    return " ".join(message.splitlines())


def _valid(result: object, check_id: str) -> bool:
    return (
        isinstance(result, CheckResult)
        and result.id == check_id
        and isinstance(result.status, Status)
        and isinstance(result.message, str)
    )


def run_checks[C](checks: DoctorChecks[C], context: C) -> list[CheckResult]:
    """Run every check of the frozen registry ``checks`` on ``context``, in order; see the
    module docstring."""
    if not checks.frozen:
        raise RuntimeError("freeze the doctor check registry before running it")
    results = []
    for check in checks.checks:
        try:
            result = check.run(context)
        except Exception as exc:  # noqa: BLE001 - one crashed check never stops the others
            result = CheckResult(check.id, Status.FAIL, CHECK_CRASHED.format(type(exc).__name__))
        if not _valid(result, check.id):
            result = CheckResult(check.id, Status.FAIL, NO_RESULT)
        results.append(replace(result, message=_one_line(result.message)))
    return results


def format_table(results: Collection[CheckResult]) -> str:
    """The results as a table: a header, one row each and the counts; see the module
    docstring."""
    width = max((len(result.id) for result in results), default=0)
    width = max(width, len("check"))
    status_width = len("status")
    lines = [f"{'status':<{status_width}}  {'check':<{width}}  message"]
    lines += [
        f"{result.status.value:<{status_width}}  {result.id:<{width}}  {result.message}".rstrip()
        for result in results
    ]
    ok, warn, fail = tally(results)
    lines.append(f"{ok} ok, {warn} warn, {fail} fail")
    return "\n".join(lines)


def format_json(results: Collection[CheckResult]) -> str:
    """The results as indented, ASCII-only JSON: ``[{"id", "status", "message"}, ...]``."""
    return json.dumps(
        [
            {"id": result.id, "status": result.status.value, "message": result.message}
            for result in results
        ],
        indent=2,
        ensure_ascii=True,
    )
