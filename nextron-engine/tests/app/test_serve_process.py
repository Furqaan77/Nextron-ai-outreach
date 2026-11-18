"""``python -m nextron`` as a real process: env refusal, the guarded health page, the database
at head, the log file, the prompts pinned at start (Story 2.8), the runner, scheduler and
heartbeat started by the lifespan, the page-text purge enqueued at scheduler start (Story 2.5),
the AI test gate's start (Story 4.5: the extraction fixtures stored, one ``run_ai_test_sets``
job per extraction tuple, no tested activation, and the child's network guarded), the second
instance, and ``nextron open`` with no engine running.

Every engine here starts with ``tests.live_guard.child_env``, so it can look up and connect to
loopback only (``tests/child_guard``).

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
from nextron.bootstrap import DEFAULT_PLAYBOOK_PATH, ENGINE_ROOT
from nextron.db.migrations import head_revision
from nextron.domain.backup import ERASURE_LEDGER_FILE
from tests.live_guard import child_env
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
    # Story 4.5: every engine a test starts gets the network guard (tests/child_guard)
    env = child_env({k: v for k, v in os.environ.items() if not k.upper().startswith("NEXTRON_")})
    playbook = tmp_path / "playbook" / "sales-playbook.md"  # Story 4.4: a copy, never the real one
    playbook.parent.mkdir()
    shutil.copyfile(ENGINE_ROOT / DEFAULT_PLAYBOOK_PATH, playbook)
    env.update(
        NEXTRON_ENV="test",
        NEXTRON_DATA_DIR=str(tmp_path / "data"),
        NEXTRON_BACKUP_DIR=str(tmp_path / "backups"),
        NEXTRON_PLAYBOOK_PATH=str(playbook),
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


def purge_jobs(path: Path) -> list[tuple[str]]:
    """The idempotency keys of the running engine's ``purge_page_text`` jobs."""
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return reader.execute(
            "SELECT idempotency_key FROM jobs WHERE type = 'purge_page_text'"
        ).fetchall()
    finally:
        reader.close()


def playbook_counts(path: Path) -> tuple[int, int, int, int, int]:
    """How many Playbook snapshots, candidate tuples, tuples with a Playbook, startup checks
    that read a snapshot, and activations that are not bootstrap or name a Playbook the running
    engine's database holds."""
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        counts = [
            reader.execute(statement).fetchone()[0]
            for statement in (
                "SELECT COUNT(*) FROM playbook_snapshots",
                "SELECT COUNT(*) FROM ai_route_tuples",
                "SELECT COUNT(*) FROM ai_route_tuples WHERE playbook_sha256 IS NOT NULL",
                "SELECT COUNT(*) FROM playbook_checks WHERE \"trigger\" = 'startup' "
                "AND playbook_sha256 IS NOT NULL AND problem IS NULL",
                "SELECT COUNT(*) FROM ai_route_activations "
                "WHERE bootstrap = 0 OR playbook_sha256 IS NOT NULL",
            )
        ]
        return tuple(int(count) for count in counts)  # type: ignore[return-value]
    finally:
        reader.close()


def read_rows(path: Path, statement: str) -> list[tuple[object, ...]]:
    """The rows ``statement`` reads from the running engine's database, read outside it."""
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return reader.execute(statement).fetchall()
    finally:
        reader.close()


def wait_for_dead_test_runs(path: Path, count: int) -> list[tuple[object, ...]]:
    """The ``run_ai_test_sets`` jobs once ``count`` of them are ``dead``: with no Segment stored
    (no seed sync ran), each run refuses its cases before any call."""
    deadline = time.monotonic() + 20
    statement = (
        "SELECT idempotency_key, status, last_error FROM jobs "
        "WHERE type = 'run_ai_test_sets' ORDER BY idempotency_key"
    )
    while True:
        try:
            rows = read_rows(path, statement)
        except sqlite3.OperationalError:  # the engine holds a write lock for a moment
            rows = []
        if len([row for row in rows if row[1] == "dead"]) == count:
            return rows
        if time.monotonic() > deadline:
            pytest.fail(f"the test runs did not end: {rows}")
        time.sleep(0.1)


def prompt_counts(path: Path) -> tuple[int, int, int]:
    """How many prompt snapshots, active bootstrap activations by ``seed`` with no test run,
    and activation ``events`` rows the running engine's database holds."""
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        [(snapshots,)] = reader.execute("SELECT COUNT(*) FROM prompt_snapshots").fetchall()
        [(bootstrapped,)] = reader.execute(
            "SELECT COUNT(*) FROM ai_route_activations WHERE bootstrap = 1 AND actor = 'seed' "
            "AND test_run_id IS NULL AND deactivated_at IS NULL"
        ).fetchall()
        [(events,)] = reader.execute(
            "SELECT COUNT(*) FROM events WHERE entity = 'ai_route_activations' "
            "AND to_status = 'active' AND cause = 'bootstrap' AND actor = 'seed'"
        ).fetchall()
        return int(snapshots), int(bootstrapped), int(events)
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
    # then the scheduler, with backup_db's, restore_test's and check_domains's daily times (no
    # catch-up on a new database), and the heartbeat writer's first beat
    dailies = {
        line["trigger"]: line for line in lines if line["event"] == "daily_trigger_scheduled"
    }
    assert sorted(dailies) == ["backup_db", "check_domains", "restore_test"]
    daily = dailies["backup_db"]
    assert (daily["at"], daily["timezone"], daily["catch_up"]) == ("10:00", "UTC", False)
    restore = dailies["restore_test"]
    assert (restore["at"], restore["timezone"], restore["catch_up"]) == ("11:00", "UTC", False)
    domains = dailies["check_domains"]  # Story 6.5: no Sending Domain yet, so no catch-up
    assert (domains["at"], domains["timezone"], domains["catch_up"]) == ("07:00", "UTC", False)
    [scheduler_started] = [line for line in lines if line["event"] == "scheduler_started"]
    assert (scheduler_started["logger"], scheduler_started["triggers"]) == (
        "nextron.jobs.scheduler",
        5,  # backup_db, restore_test, check_recovery, purge_page_text and check_domains
    )
    assert lines.index(runner_started) < lines.index(daily) < lines.index(scheduler_started)
    assert lines.index(daily) < lines.index(restore) < lines.index(scheduler_started)
    assert lines.index(restore) < lines.index(domains) < lines.index(scheduler_started)
    # a new database takes no pre_migrate copy and no catch-up backup: the backup dir holds
    # only the empty erasure ledger the startup sync creates (Story 3.16)
    backups = tmp_path / "backups"
    assert sorted(path.name for path in backups.rglob("*")) == [ERASURE_LEDGER_FILE]
    assert (backups / ERASURE_LEDGER_FILE).read_bytes() == b""
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
    assert HEAD == "0038_4_8_enrollments"
    # purge_page_text fires at scheduler start (Story 2.5): its tick was enqueued and ran
    purged = wait_for_log_line(data_dir, "nextron.app.research.api", "page_text_purged")
    [purge] = [line for line in purged if line["event"] == "page_text_purged"]
    assert purge["count"] == 0
    assert purge_jobs(data_dir / "nextron.db") == [("purge_page_text:global:tick",)]
    # no adapter or HTTP client was built, so no external call was made or recorded
    assert usage_counts(data_dir / "nextron.db") == (0, 0)
    # the prompts were pinned before the engine started (Story 2.8): one snapshot and one
    # bootstrap activation, by seed and with no test run, per route
    assert prompt_counts(data_dir / "nextron.db") == (3, 3, 3)
    # Story 4.4: one Sales Playbook snapshot, one candidate tuple per route (none with a
    # Playbook) and one startup check; no route is activated with a Playbook, and every
    # activation is a bootstrap one
    assert playbook_counts(data_dir / "nextron.db") == (1, 3, 0, 1, 0)
    [synced] = [line for line in lines if line["event"] == "prompts_synced"]
    assert (synced["stored_count"], synced["activated_count"]) == (3, 3)
    assert lines.index(synced) < lines.index(started)
    # Story 4.5: the 11 extraction fixtures were stored before the engine started, and the
    # lifespan queued one run_ai_test_sets job per extraction tuple, keyed by tuple and set.
    # Story 4.6: the Reply, Grounding and drafting-smoke sets were stored too, every row a
    # fixture, and still only the three extraction jobs were queued (no other route has a tuple)
    db = data_dir / "nextron.db"
    stored = dict(
        read_rows(
            db,
            "SELECT test_set, COUNT(*) FROM ai_test_cases "
            "WHERE retired_at IS NULL AND origin = 'fixture' AND contact_id IS NULL "
            "GROUP BY test_set",
        )
    )
    assert stored["extraction"] == 11
    assert stored["reply"] >= 50 and stored["grounding"] >= 30 and stored["drafting_smoke"] >= 10
    assert read_rows(db, "SELECT COUNT(*) FROM ai_test_cases") == [(sum(stored.values()),)]
    [cases_synced] = [line for line in lines if line["event"] == "test_cases_synced"]
    assert (cases_synced["added_count"], cases_synced["retired_count"]) == (
        sum(stored.values()),
        0,
    )
    assert lines.index(cases_synced) < lines.index(started)
    tuple_ids = sorted(str(row[0]) for row in read_rows(db, "SELECT id FROM ai_route_tuples"))
    runs = wait_for_dead_test_runs(db, 3)
    assert [row[0] for row in runs] == [f"run_ai_test_sets:{t}:extraction" for t in tuple_ids]
    # no Segment is stored (seed sync never ran), so each run refused its cases before any
    # call: nothing was stored, no Claude call was made and no activation is tested
    assert all("invalid_test_cases" in str(row[2]) for row in runs), runs
    assert read_rows(db, "SELECT COUNT(*) FROM ai_test_runs") == [(0,)]
    assert playbook_counts(db)[4] == 0  # every activation is still a bootstrap one
    lines = log_lines(data_dir)
    [queued] = [line for line in lines if line["event"] == "ai_test_runs_queued"]
    assert queued["count"] == 3
    # each dead run raised a job_dead alert, which failed for want of a Telegram chat id: no
    # adapter was built and nothing reached the sink
    alerting = [line for line in lines if str(line["event"]).startswith(("alert_", "sink_"))]
    assert all(
        (line["event"], line["kind"], line["code"])
        == ("alert_failed", "job_dead", "setting_not_set")
        for line in alerting
    ), alerting
    assert not (data_dir / "sink").exists()
    assert usage_counts(db) == (0, 0)
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
