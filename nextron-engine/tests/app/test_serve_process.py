"""``python -m nextron`` as a real process: env refusal, health page, the database at head, the
log file and the second instance."""

import json
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from nextron import __version__
from nextron.db.migrations import head_revision
from tests.migration_chains import stored_revisions

HEAD = head_revision()
STARTUP_TIMEOUT_S = 60
PLANTED_CODE = "Fk" * 20  # a fake 40-character secret, assembled at run time


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def nextron(*args: str, env: dict[str, str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "nextron", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture
def engine_env(tmp_path: Path, local_app_data: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NEXTRON_")}
    env.update(
        NEXTRON_ENV="test",
        NEXTRON_DATA_DIR=str(tmp_path / "data"),
        NEXTRON_BACKUP_DIR=str(tmp_path / "backups"),
        NEXTRON_PORT=str(free_port()),
        LOCALAPPDATA=str(local_app_data),
        PYTHONIOENCODING="utf-8",
    )
    return env


@pytest.fixture
def running_engine(engine_env: dict[str, str], tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    log = (tmp_path / "serve.log").open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "nextron", "serve"],
        env=engine_env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{engine_env['NEXTRON_PORT']}/health"
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while True:
            if process.poll() is not None:
                pytest.fail(f"serve exited with {process.returncode}: {_read(tmp_path)}")
            try:
                with urllib.request.urlopen(url, timeout=2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail(f"serve did not answer within {STARTUP_TIMEOUT_S}s")
                time.sleep(0.2)
        yield process
    finally:
        process.terminate()
        process.wait(timeout=30)
        log.close()


def _read(tmp_path: Path) -> str:
    return (tmp_path / "serve.log").read_text(encoding="utf-8", errors="replace")


def log_text(data_dir: Path) -> str:
    return (data_dir / "logs" / "nextron.log").read_text(encoding="utf-8")


def log_lines(data_dir: Path) -> list[dict[str, object]]:
    """The complete lines of the log; a last line the engine is still writing is left out."""
    text = log_text(data_dir)
    complete = text if text.endswith("\n") else text.rpartition("\n")[0]
    return [json.loads(line) for line in complete.splitlines()]


def wait_for_log_line(data_dir: Path, logger: str, needle: str) -> list[dict[str, object]]:
    """Return the log's lines once one from ``logger`` has ``needle`` in its event."""
    deadline = time.monotonic() + 10
    lines: list[dict[str, object]] = []
    while True:
        try:
            lines = log_lines(data_dir)
        except (OSError, json.JSONDecodeError):  # not created yet, or caught mid-write
            pass
        else:
            if any(line["logger"] == logger and needle in str(line["event"]) for line in lines):
                return lines
        if time.monotonic() > deadline:
            pytest.fail(f"no {logger} line with {needle!r} in {lines}")
        time.sleep(0.1)


@pytest.mark.parametrize("value", [None, "staging"])
def test_serve_without_a_valid_env_exits_2(
    value: str | None, engine_env: dict[str, str], local_app_data: Path, tmp_path: Path
) -> None:
    del engine_env["NEXTRON_DATA_DIR"]  # fall back to %LOCALAPPDATA%, which must stay empty
    if value is None:
        del engine_env["NEXTRON_ENV"]
    else:
        engine_env["NEXTRON_ENV"] = value

    result = nextron("serve", env=engine_env)

    assert result.returncode == 2
    assert result.stderr.strip() == "NEXTRON_ENV must be set to dev, test or prod"
    assert list(local_app_data.iterdir()) == []
    assert not (tmp_path / "backups").exists()


def test_health_page_and_second_instance(
    running_engine: subprocess.Popen[bytes], engine_env: dict[str, str], tmp_path: Path
) -> None:
    port = engine_env["NEXTRON_PORT"]
    url = f"http://127.0.0.1:{port}/health?code={PLANTED_CODE}"
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.status == 200
        payload = json.loads(response.read())

    assert set(payload) == {"env", "version", "uptime_s"}
    assert payload["env"] == "test"
    assert payload["version"] == __version__
    assert isinstance(payload["uptime_s"], int) and payload["uptime_s"] >= 0

    data_dir = tmp_path / "data"
    assert (data_dir / "logs").is_dir()
    assert (data_dir / "nextron.lock").is_file()
    assert stored_revisions(data_dir / "nextron.db") == [HEAD]

    lines = wait_for_log_line(data_dir, "uvicorn.access", '"GET /health HTTP/1.1" 200')
    [migrated] = [line for line in lines if line["event"] == "database_migrated"]
    assert (migrated["from_revision"], migrated["to_revision"]) == (None, HEAD)
    [started] = [line for line in lines if line["event"] == "engine_started"]
    assert (started["logger"], started["env"], started["version"], started["db_revision"]) == (
        "nextron.main",
        "test",
        __version__,
        HEAD,
    )
    assert any(line["logger"] == "uvicorn.error" for line in lines)
    for line in lines:
        assert {"ts", "level", "logger", "event"} <= set(line)
    assert PLANTED_CODE not in log_text(data_dir)
    assert "code=" not in log_text(data_dir)

    second = nextron("serve", env=engine_env)

    assert second.returncode == 3
    assert second.stderr.strip() == "another Nextron instance is running for test"
    assert running_engine.poll() is None
    after = log_lines(data_dir)
    assert [line["event"] for line in after].count("engine_started") == 1


def test_nextron_console_script_runs_doctor(engine_env: dict[str, str], tmp_path: Path) -> None:
    script = shutil.which("nextron", path=sysconfig.get_path("scripts"))
    assert script, "the nextron console script is not installed; run uv sync --frozen"

    result = subprocess.run(
        [script, "doctor"], env=engine_env, capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert [line.split()[0] for line in lines] == ["PASS"] * 4
    assert not (tmp_path / "data").exists()
