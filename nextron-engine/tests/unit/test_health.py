"""``GET /health`` (FR-58 seed), driven through the ASGI interface without a network. Since
Story 1.4 it needs a dashboard session like every other route."""

import json
from pathlib import Path

import pytest

from nextron import __version__, main
from nextron.app.ops.api import DashboardEntry
from nextron.bootstrap import load_bootstrap
from tests.asgi import request
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets


class Ticks:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0)


def test_health_reports_env_version_and_uptime_only() -> None:
    dashboard = Dashboard(env="dev", version="1.2.3", monotonic=Ticks(100.0, 142.9))
    cookie = dashboard.open_session()

    reply = dashboard.get("/health", cookie=cookie)

    assert reply.status == 200
    assert reply.header(b"content-type") == b"application/json"
    assert json.loads(reply.body) == {"env": "dev", "version": "1.2.3", "uptime_s": 42}


def test_health_needs_a_session() -> None:
    reply = Dashboard().get("/health")

    assert reply.status == 401
    assert b"uptime_s" not in reply.body


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_served(path: str) -> None:
    dashboard = Dashboard()
    cookie = dashboard.open_session()

    assert dashboard.get(path, cookie=cookie).status == 404


def test_composition_root_serves_the_package_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    secrets, clock = InMemorySecrets("test"), FrozenClock()
    app = main.build_app(load_bootstrap(), secrets=secrets, clock=clock)
    code = DashboardEntry(secrets, clock).issue_code()  # as nextron open would
    host = [(b"host", b"127.0.0.1:8767")]  # the test port

    entered = request(app, "/enter", query=b"t=" + code.encode(), headers=host)
    cookie = entered.header(b"set-cookie").split(b";", 1)[0]  # type: ignore[union-attr]
    reply = request(app, "/health", headers=[*host, (b"cookie", cookie)])

    assert entered.status == 303
    assert reply.status == 200
    payload = json.loads(reply.body)
    assert payload["env"] == "test"
    assert payload["version"] == __version__ != "0+unknown"
    assert isinstance(payload["uptime_s"], int)


def test_composition_root_wires_keyring_and_the_system_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[str] = []
    secrets, clock = InMemorySecrets("test"), FrozenClock()

    def keyring_secrets(env: str) -> InMemorySecrets:
        built.append(f"KeyringSecrets({env})")
        return secrets

    def system_clock() -> FrozenClock:
        built.append("SystemClock()")
        return clock

    monkeypatch.setattr(main, "KeyringSecrets", keyring_secrets)
    monkeypatch.setattr(main, "SystemClock", system_clock)

    entry = main.build_entry(load_bootstrap())

    assert built == ["KeyringSecrets(test)", "SystemClock()"]
    code = entry.issue_code()  # the entry uses what was built
    [stored] = secrets.store.values()
    assert json.loads(stored) == {"code": code, "issued_at": "2026-11-16T09:00:00.000000Z"}
