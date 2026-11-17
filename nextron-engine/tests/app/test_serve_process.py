"""``python -m nextron`` as a real process: env refusal, the guarded health page, the database
at head, the log file, the runner, scheduler and heartbeat started by the lifespan, the second
instance, and ``nextron open`` with no engine running.

Nothing here runs ``nextron open`` against a live engine or touches Credential Manager: entering
the dashboard is covered in-process with fakes (``tests/unit/test_dashboard.py``)."""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path

import pytest

from nextron import __version__
from nextron.db.migrations import head_revision
from tests.migration_chains import stored_revisions

HEAD = head_revision()
STARTUP_TIMEOUT_S = 60
PLANTED_CODE = "Fk" * 20  # a fake 40-character secret, assembled at run time
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    ),
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
}


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
            except urllib.error.HTTPError:  # any HTTP answer, the 401 included, means it is up
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


def heartbeat_rows(path: Path) -> list[tuple[str, int, str, str]]:
    """The ``runner_heartbeat`` rows of the running engine's database, read outside it."""
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return reader.execute(
            "SELECT id, pid, started_at, beat_at FROM runner_heartbeat"
        ).fetchall()
    finally:
        reader.close()


def usage_counts(path: Path) -> tuple[int, int]:
    """How many ``api_usage`` and ``ai_usage`` rows the running engine's database holds."""
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        [(api,)] = reader.execute("SELECT COUNT(*) FROM api_usage").fetchall()
        [(ai,)] = reader.execute("SELECT COUNT(*) FROM ai_usage").fetchall()
        return int(api), int(ai)
    finally:
        reader.close()


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


def fetch(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    """GET ``url``; return status, headers (lowercase names, each of which must appear once)
    and body, whatever the status."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, _single(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        with error:
            return error.code, _single(error.headers), error.read().decode("utf-8")


def _single(message: Message) -> dict[str, str]:
    names = [name.lower() for name in message.keys()]
    assert len(names) == len(set(names)), names
    return {name.lower(): value for name, value in message.items()}


def test_guarded_health_page_and_second_instance(
    running_engine: subprocess.Popen[bytes], engine_env: dict[str, str], tmp_path: Path
) -> None:
    port = engine_env["NEXTRON_PORT"]
    status, headers, body = fetch(f"http://127.0.0.1:{port}/health?code={PLANTED_CODE}")

    assert status == 401  # no session
    assert "nextron open" in body
    assert "uptime_s" not in body and __version__ not in body
    for name, value in SECURITY_HEADERS.items():
        assert headers[name] == value
    assert headers["cache-control"] == "no-store"
    assert not [name for name in headers if name.startswith("access-control-")]

    status, headers, body = fetch(
        f"http://127.0.0.1:{port}/health", headers={"Host": "evil.example"}
    )
    assert (status, body) == (400, "Bad request: unexpected Host header")
    assert headers["x-frame-options"] == "DENY"

    data_dir = tmp_path / "data"
    assert (data_dir / "logs").is_dir()
    assert (data_dir / "nextron.lock").is_file()
    assert stored_revisions(data_dir / "nextron.db") == [HEAD]

    lines = wait_for_log_line(data_dir, "uvicorn.access", '"GET /health HTTP/1.1" 401')
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
    # the lifespan started the job runner before the server took requests
    [runner_started] = [line for line in lines if line["event"] == "job_runner_started"]
    assert (runner_started["logger"], runner_started["bulk_threads"]) == ("nextron.jobs.runner", 1)
    assert lines.index(started) < lines.index(runner_started)
    # then the scheduler, with backup_db's and restore_test's daily times (no catch-up on a new
    # database), and the heartbeat writer's first beat
    dailies = {
        line["trigger"]: line for line in lines if line["event"] == "daily_trigger_scheduled"
    }
    assert sorted(dailies) == ["backup_db", "restore_test"]
    daily = dailies["backup_db"]
    assert (daily["at"], daily["timezone"], daily["catch_up"]) == ("10:00", "UTC", False)
    restore = dailies["restore_test"]
    assert (restore["at"], restore["timezone"], restore["catch_up"]) == ("11:00", "UTC", False)
    [scheduler_started] = [line for line in lines if line["event"] == "scheduler_started"]
    assert (scheduler_started["logger"], scheduler_started["triggers"]) == (
        "nextron.jobs.scheduler",
        2,
    )
    assert lines.index(runner_started) < lines.index(daily) < lines.index(scheduler_started)
    assert lines.index(daily) < lines.index(restore) < lines.index(scheduler_started)
    # a new database takes no pre_migrate copy and no catch-up backup: the backup dir is empty
    assert not (tmp_path / "backups").exists()
    assert not [line for line in lines if str(line["event"]).startswith("backup")]
    [(row_id, pid, started_at, beat_at)] = heartbeat_rows(data_dir / "nextron.db")
    assert (row_id, pid > 0) == ("00000000000000000000000000", True)
    first_beat = datetime.fromisoformat(started_at)
    assert timedelta(0) <= datetime.fromisoformat(beat_at) - first_beat
    runner_ts = datetime.fromisoformat(str(runner_started["ts"]))
    assert abs(first_beat - runner_ts) < timedelta(seconds=1)  # fresh at startup
    heartbeat = data_dir / "heartbeat"
    assert heartbeat.is_file()
    assert heartbeat.stat().st_mtime >= first_beat.timestamp() - 1
    assert HEAD == "0009_1_18_restore_tests"
    # no adapter or HTTP client was built, so no external call was made or recorded
    assert usage_counts(data_dir / "nextron.db") == (0, 0)
    # no alert was raised or sent: nothing built the Telegram adapter or reached the sink
    assert not [line for line in lines if str(line["event"]).startswith(("alert_", "sink_"))]
    assert not (data_dir / "sink").exists()
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


def test_nextron_console_script_runs_doctor_help(
    engine_env: dict[str, str], tmp_path: Path
) -> None:
    """``--help`` only: a real ``nextron doctor`` would read this PC's Credential Manager (decision
    7). ``main.doctor`` itself is covered end to end with fakes in ``tests/unit/test_doctor.py``."""
    script = shutil.which("nextron", path=sysconfig.get_path("scripts"))
    assert script, "the nextron console script is not installed; run uv sync --frozen"

    result = subprocess.run(
        [script, "doctor", "--help"], env=engine_env, capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--json" in result.stdout
    assert "--env" in result.stdout
    assert not (tmp_path / "data").exists()


def test_open_with_no_engine_running_exits_1(engine_env: dict[str, str], tmp_path: Path) -> None:
    # Hold the port, bound but not listening, for the whole call: nothing else can start
    # listening on it, so nextron open can't reach Credential Manager or a browser.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            holder.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        holder.bind(("127.0.0.1", 0))
        engine_env["NEXTRON_PORT"] = str(holder.getsockname()[1])

        result = nextron("open", env=engine_env)

    assert result.returncode == 1
    assert result.stderr.strip() == "engine not running"
    assert result.stdout == ""
    assert not (tmp_path / "data").exists()
