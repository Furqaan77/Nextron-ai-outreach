"""Environment isolation (AD-14, Story 1.8 AC 4): each environment has its own data dir,
database, keyring service (``nextron-engine/<env>``) and port, and neither touches the other's.

- As real processes: ``dev`` and ``test`` engines start side by side on one temporary
  ``%LOCALAPPDATA%``, each on a free port, and both answer ``/health``. Each data dir then holds
  only its own database, lock and log, each log carries only its own environment, and nothing
  else exists under ``Nextron\\``.
- In-process: two dashboards built by ``main.build_app`` share one recording keyring backend
  through the real ``KeyringSecrets``. A Hunter key saved in ``dev`` leaves ``test`` "Not set",
  and every backend call stays in the app's own ``nextron-engine/<env>`` namespace: the
  secret's own target ``nextron-engine/<env>/<name>``, keyring's copy of it
  (``<name>@nextron-engine/<env>/<name>``), or a legacy entry under ``nextron-engine/<env>``.

Nothing here touches Windows Credential Manager or the network beyond 127.0.0.1.
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from keyring.backend import KeyringBackend
from keyring.compat import properties
from keyring.errors import PasswordDeleteError

import nextron.adapters.secrets
from nextron import main
from nextron.adapters.secrets import KeyringSecrets, service_name
from nextron.app.ops.api import DashboardEntry
from nextron.bootstrap import BootstrapConfig, load_bootstrap
from nextron.db.migrations import head_revision
from tests.app.test_serve_process import fetch
from tests.asgi import Reply, request
from tests.dashboard import FORM, body_hx_headers, form
from tests.live_guard import child_env
from tests.fakes.clock import FrozenClock
from tests.fakes.units import FakeUnits
from tests.migration_chains import stored_revisions
from tests.unit.test_credentials_page import Page

STARTUP_TIMEOUT_S = 60
T = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
PLANTED = "Hk" * 16  # a fake Hunter key, assembled at run time
ENVIRONMENTS = ("dev", "test")


# Two engines side by side ----------------------------------------------------------------------


@dataclass
class Engine:
    env: str
    port: int
    process: subprocess.Popen[bytes]
    output: Path

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/health"


def free_ports(count: int) -> list[int]:
    """``count`` distinct free ports: every probe socket stays bound until all are read, so
    no two can be the same."""
    with ExitStack() as stack:
        probes = [
            stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            for _ in range(count)
        ]
        for probe in probes:
            probe.bind(("127.0.0.1", 0))
        return [probe.getsockname()[1] for probe in probes]


def start_engine(env: str, port: int, local_app_data: Path, tmp_path: Path) -> Engine:
    """``python -m nextron serve`` for ``env`` on ``port``: the default data dir under
    ``local_app_data``."""
    # Story 4.5: every engine a test starts gets the network guard (tests/child_guard)
    variables = child_env(
        {k: v for k, v in os.environ.items() if not k.upper().startswith("NEXTRON_")}
    )
    variables.update(
        NEXTRON_ENV=env,
        NEXTRON_PORT=str(port),
        LOCALAPPDATA=str(local_app_data),
        PYTHONIOENCODING="utf-8",
    )
    output = tmp_path / f"serve-{env}.out"
    with output.open("wb") as sink:
        process = subprocess.Popen(
            [sys.executable, "-m", "nextron", "serve"],
            env=variables,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    return Engine(env, port, process, output)


def wait_until_answering(engine: Engine) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while True:
        if engine.process.poll() is not None:
            text = engine.output.read_text(encoding="utf-8", errors="replace")
            pytest.fail(f"{engine.env} serve exited with {engine.process.returncode}: {text}")
        try:
            with urllib.request.urlopen(engine.url, timeout=2):
                return
        except urllib.error.HTTPError:  # any HTTP answer, the 401 included, means it is up
            return
        except OSError:
            if time.monotonic() > deadline:
                pytest.fail(f"{engine.env} serve did not answer within {STARTUP_TIMEOUT_S}s")
            time.sleep(0.2)


def log_records(data_dir: Path) -> list[dict[str, Any]]:
    text = (data_dir / "logs" / "nextron.log").read_text(encoding="utf-8")
    complete = text if text.endswith("\n") else text.rpartition("\n")[0]
    return [json.loads(line) for line in complete.splitlines()]


def wait_for_access_line(data_dir: Path) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 10
    while True:
        try:
            records = log_records(data_dir)
        except (OSError, json.JSONDecodeError):
            records = []
        if any(record["logger"] == "uvicorn.access" for record in records):
            return records
        if time.monotonic() > deadline:
            pytest.fail(f"no access log line in {data_dir}: {records}")
        time.sleep(0.1)


@pytest.fixture
def engines(local_app_data: Path, tmp_path: Path) -> Iterator[dict[str, Engine]]:
    ports = free_ports(len(ENVIRONMENTS))
    started: dict[str, Engine] = {}
    try:
        for env, port in zip(ENVIRONMENTS, ports, strict=True):
            started[env] = start_engine(env, port, local_app_data, tmp_path)
        for engine in started.values():
            wait_until_answering(engine)
        yield started
    finally:
        for engine in started.values():
            engine.process.terminate()
        for engine in started.values():
            engine.process.wait(timeout=30)


REQUIRED_FILES = {"nextron.db", "nextron.lock", "heartbeat", "logs", "logs/nextron.log"}
SQLITE_SIDE_FILES = {"nextron.db-wal", "nextron.db-shm"}  # WAL mode keeps these while open


def revisions_after_exit(path: Path) -> list[str]:
    """``stored_revisions`` of a database a terminated engine had open. The job runner's
    threads hold pooled connections, and Windows releases a killed process's file locks a
    moment after it exits, so a read straight away can fail with "disk I/O error"."""
    deadline = time.monotonic() + 10
    while True:
        try:
            return stored_revisions(path)
        except sqlite3.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def test_dev_and_test_run_side_by_side_without_touching_each_other(
    engines: dict[str, Engine], local_app_data: Path
) -> None:
    nextron_dir = local_app_data / "Nextron"
    records: dict[str, list[dict[str, Any]]] = {}
    for env, engine in engines.items():
        other = engines["test" if env == "dev" else "dev"]
        status, _, body = fetch(engine.url)
        assert status == 401 and "nextron open" in body  # each answers on its own port
        status, _, _ = fetch(engine.url, headers={"Host": f"127.0.0.1:{other.port}"})
        assert status == 400  # and only for its own port
        records[env] = wait_for_access_line(nextron_dir / env)
    for engine in engines.values():
        engine.process.terminate()
        engine.process.wait(timeout=30)

    assert sorted(path.name for path in nextron_dir.iterdir()) == ["dev", "test"]
    assert sorted(path.name for path in local_app_data.iterdir()) == ["Nextron"]
    for env in ENVIRONMENTS:
        data_dir = nextron_dir / env
        other_dir = nextron_dir / ("test" if env == "dev" else "dev")
        files = {path.relative_to(data_dir).as_posix() for path in data_dir.rglob("*")}
        assert REQUIRED_FILES <= files <= REQUIRED_FILES | SQLITE_SIDE_FILES, (env, files)
        assert revisions_after_exit(data_dir / "nextron.db") == [head_revision()]
        [started] = [record for record in records[env] if record["event"] == "engine_started"]
        assert started["env"] == env
        for record in log_records(data_dir):
            assert record.get("env", env) == env, record
            for value in record.values():
                assert str(other_dir) not in str(value), record


# Two dashboards on one keyring -------------------------------------------------------------------


class RecordingKeyring(KeyringBackend):
    """A keyring backend in a dict, shared like Credential Manager, that records every call as
    ``(operation, service, name)``. It is never chosen by ``keyring.get_keyring()``."""

    @properties.classproperty
    def priority(cls) -> float:
        raise RuntimeError("test backend only")

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append(("set", service, username))
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        if (service, username) not in self.store:
            raise PasswordDeleteError(service)
        del self.store[(service, username)]


@dataclass
class Dashboard:
    """One environment's dashboard, as ``main.build_app`` builds it, with a session open."""

    config: BootstrapConfig
    app: FastAPI
    cookie: bytes = b""
    token: str = ""

    @property
    def host(self) -> bytes:
        return f"127.0.0.1:{self.config.port}".encode()

    def get(self, path: str) -> Reply:
        return request(self.app, path, headers=[(b"host", self.host), (b"cookie", self.cookie)])

    def post(self, path: str, body: bytes) -> Reply:
        return request(
            self.app,
            path,
            method="POST",
            headers=[
                (b"host", self.host),
                (b"cookie", self.cookie),
                (b"origin", b"http://" + self.host),
                (b"x-csrf-token", self.token.encode()),
                (b"content-type", FORM),
            ],
            body=body,
        )

    def hunter_state(self) -> str:
        reply = self.get("/credentials")
        assert reply.status == 200, reply
        return Page(reply.text).state("hunter")


def open_dashboard(
    env: str, backend: RecordingKeyring, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Dashboard:
    monkeypatch.setenv("NEXTRON_ENV", env)
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / env))
    config = load_bootstrap()
    dashboard = Dashboard(config, main.build_app(config, units=FakeUnits(), clock=FrozenClock(T)))
    # nextron open, in its own process, writes the code through its own KeyringSecrets(env)
    code = DashboardEntry(KeyringSecrets(env, backend=backend), FrozenClock(T)).issue_code()
    entered = request(
        dashboard.app, "/enter", query=b"t=" + code.encode(), headers=[(b"host", dashboard.host)]
    )
    assert entered.status == 303, entered
    cookie = entered.header(b"set-cookie")
    assert cookie is not None
    dashboard.cookie = cookie.split(b";", 1)[0]
    home = dashboard.get("/")
    assert home.status == 200, home
    token = body_hx_headers(home.text)["X-CSRF-Token"]
    assert isinstance(token, str)
    dashboard.token = token
    return dashboard


def test_a_credential_saved_in_dev_is_not_set_in_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = RecordingKeyring()
    # build_app's own KeyringSecrets(<env>) runs for real; only its backend is the recorder
    monkeypatch.setattr(nextron.adapters.secrets, "default_backend", lambda: backend)
    calls_by_env: dict[str, list[tuple[str, str, str]]] = {}

    start = len(backend.calls)
    dev = open_dashboard("dev", backend, tmp_path, monkeypatch)
    body = form(csrf_token=dev.token, credential="hunter", value=PLANTED)
    saved = dev.post("/credentials/save", body)
    assert saved.status == 200, saved
    assert dev.hunter_state() == "Set, last updated 2026-11-17 09:30 UTC"
    calls_by_env["dev"] = backend.calls[start:]

    start = len(backend.calls)
    test = open_dashboard("test", backend, tmp_path, monkeypatch)
    assert test.hunter_state() == "Not set"
    calls_by_env["test"] = backend.calls[start:]

    for env, calls in calls_by_env.items():
        assert calls, env
        namespace = f"nextron-engine/{env}"
        for _, service, name in calls:
            assert service in {
                f"{namespace}/{name}",  # the secret's own target
                f"{name}@{namespace}/{name}",  # keyring's copy of its previous value
                namespace,  # a legacy entry
            }, (env, service, name)
    assert ("set", "nextron-engine/dev/hunter/api_key", "hunter/api_key") in calls_by_env["dev"]
    assert ("get", "nextron-engine/test/hunter/api_key", "hunter/api_key") in calls_by_env["test"]
    assert [key for key in backend.store if "nextron-engine/test" in key[0]] == []
    assert backend.store[("nextron-engine/dev/hunter/api_key", "hunter/api_key")] == PLANTED


def test_each_environment_has_its_own_data_dir_database_keyring_service_and_port(
    local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, tuple[Path, Path, str, int]] = {}
    for env in ("dev", "test", "prod"):
        monkeypatch.setenv("NEXTRON_ENV", env)
        config = load_bootstrap()
        seen[env] = (
            config.data_dir,
            config.data_dir / main.DB_FILE_NAME,
            service_name(env),
            config.port,
        )

    for position in range(4):
        assert len({values[position] for values in seen.values()}) == 3
    assert seen["dev"][0] == local_app_data / "Nextron" / "dev"
    assert seen["dev"][2] == "nextron-engine/dev"
