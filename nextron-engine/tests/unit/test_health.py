"""``GET /health`` (FR-58 seed), driven through the ASGI interface without a network."""

import json
from pathlib import Path

import pytest

from nextron import __version__, main
from nextron.bootstrap import load_bootstrap
from nextron.web.app import create_app
from tests.asgi import get


class Ticks:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0)


def test_health_reports_env_version_and_uptime_only() -> None:
    app = create_app(env="dev", version="1.2.3", monotonic=Ticks(100.0, 142.9))

    status, headers, body = get(app, "/health")

    assert status == 200
    assert headers[b"content-type"] == b"application/json"
    assert json.loads(body) == {"env": "dev", "version": "1.2.3", "uptime_s": 42}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_served(path: str) -> None:
    status, _, _ = get(create_app(env="dev", version="1.2.3"), path)

    assert status == 404


def test_composition_root_serves_the_package_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))

    status, _, body = get(main.build_app(load_bootstrap()), "/health")

    assert status == 200
    payload = json.loads(body)
    assert payload["env"] == "test"
    assert payload["version"] == __version__ != "0+unknown"
    assert isinstance(payload["uptime_s"], int)
