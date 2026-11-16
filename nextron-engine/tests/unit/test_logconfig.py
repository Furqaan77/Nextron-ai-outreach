"""``nextron.logconfig`` against the story's I/O matrix: JSON fields, redaction on every path,
SDK levels, rotation, and ``serve`` starting logging only once it holds the lock.

Secret-shaped values are assembled at run time; every value here is fake.
"""

import io
import json
import logging
import re
import subprocess
import sys
import threading
import uuid
import warnings
from datetime import UTC, date, datetime
from enum import Enum, IntEnum, StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from ulid import ULID as UlidValue

from nextron import __version__, main
from nextron.bootstrap import BootstrapConfig
from nextron.lock import InstanceLock, LockHeldError
from nextron.logconfig import (
    BACKUP_COUNT,
    LOG_FILE_NAME,
    MAX_BYTES,
    SDK_LOGGERS,
    JsonLineFormatter,
    RedactingFilter,
    configure_logging,
    utc_timestamp,
)
from tests.fakes.probes import PortProbe, fake_probes

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
EMAIL = "ann.lee@example.co.uk"
ANTHROPIC_KEY = "sk-ant-" + "api03-" + "FAKE0" * 8
FORTY_CHAR_KEY = "Fk" * 20
BOT_TOKEN = "123456789:" + "AA" + "Fake_Token-" * 3
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "logs"
    folder.mkdir()
    return folder


@pytest.fixture
def log_file(logs_dir: Path) -> Path:
    return configure_logging(logs_dir)


# Row: normal record
def test_a_record_is_one_json_line_with_its_ids(log_file: Path) -> None:
    logging.getLogger("nextron.x").info("lead_enriched", extra={"lead_id": ULID})

    [line] = read_lines(log_file)
    assert log_file == log_file.parent / LOG_FILE_NAME
    assert list(line) == ["ts", "level", "logger", "event", "lead_id"]
    assert TS.match(line["ts"])
    assert (line["level"], line["logger"], line["event"], line["lead_id"]) == (
        "INFO",
        "nextron.x",
        "lead_enriched",
        ULID,
    )


def test_ts_is_utc_iso_8601_with_milliseconds_and_z() -> None:
    created = datetime(2026, 11, 16, 9, 0, 0, 123_987, tzinfo=UTC).timestamp()
    record = logging.LogRecord("nextron.x", logging.INFO, __file__, 1, "tick", None, None)
    record.created = created

    assert utc_timestamp(created) == "2026-11-16T09:00:00.123Z"
    assert json.loads(JsonLineFormatter().format(record))["ts"] == "2026-11-16T09:00:00.123Z"


def test_scalar_extras_are_kept_and_the_rest_dropped(log_file: Path) -> None:
    extra = {
        "count": 3,
        "sent": True,
        "ratio": 0.5,
        "reply_id": None,
        "lead_ids": [ULID],
        "payload": {"lead_id": ULID},
        "blob": b"bytes",
        "score": float("nan"),
    }
    logging.getLogger("nextron.x").info("batch_done", extra=extra)

    [line] = read_lines(log_file)
    assert {k: line[k] for k in ("count", "sent", "ratio", "reply_id", "score")} == {
        "count": 3,
        "sent": True,
        "ratio": 0.5,
        "reply_id": None,
        "score": "nan",
    }
    assert not {"lead_ids", "payload", "blob"} & set(line)


def test_an_extra_named_like_a_field_does_not_replace_it(log_file: Path) -> None:
    logging.getLogger("nextron.x").info("real_event", extra={"event": "fake", "exc": "fake"})

    [line] = read_lines(log_file)
    assert line["event"] == "real_event"
    assert "exc" not in line


def test_uvicorns_colour_copy_of_its_message_is_dropped(log_file: Path) -> None:
    logging.getLogger("uvicorn.error").info(
        "Started server process [%d]", 42, extra={"color_message": "Started \x1b[36m%d\x1b[0m"}
    )

    [line] = read_lines(log_file)
    assert line["event"] == "Started server process [42]"
    assert "color_message" not in line


# Rows: query string, Telegram token, email, bearer and key
@pytest.mark.parametrize(
    ("message", "event", "absent"),
    [
        ("GET https://h/p?key=abc&e=a@b.co", "GET https://h/p", ["key=abc", "a@b.co", "?"]),
        (
            f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            "POST https://api.telegram.org/bot<redacted>/sendMessage",
            [BOT_TOKEN, "Fake_Token"],
        ),
        (f"reply from {EMAIL}", "reply from <email>", [EMAIL]),
        ("Authorization: Bearer xyz", "Authorization: Bearer <redacted>", ["xyz"]),
        (f"key {ANTHROPIC_KEY}", "key <key>", [ANTHROPIC_KEY, "FAKE0"]),
        (f"token {FORTY_CHAR_KEY}", "token <key>", [FORTY_CHAR_KEY]),
    ],
)
def test_secrets_in_the_message_are_masked(
    message: str, event: str, absent: list[str], log_file: Path
) -> None:
    logging.getLogger("nextron.x").warning(message)

    [line] = read_lines(log_file)
    assert line["event"] == event
    text = log_file.read_text(encoding="utf-8")
    for value in absent:
        assert value not in text


# Row: secret in args or traceback
def test_a_secret_passed_as_an_argument_is_masked(log_file: Path) -> None:
    logging.getLogger("nextron.x").error("failed %s for %s", FORTY_CHAR_KEY, EMAIL)

    [line] = read_lines(log_file)
    assert line["event"] == "failed <key> for <email>"


def test_a_secret_in_an_exception_is_masked_in_event_and_exc(log_file: Path) -> None:
    try:
        raise RuntimeError(f"GET https://h.io/x?api_key=abc as {EMAIL} with {FORTY_CHAR_KEY}")
    except RuntimeError as exc:
        logging.getLogger("nextron.x").exception("call_failed: %s", exc)

    [line] = read_lines(log_file)
    assert line["event"] == "call_failed: GET https://h.io/x as <email> with <key>"
    assert line["exc"].startswith("Traceback (most recent call last):")
    assert "RuntimeError: GET https://h.io/x as <email> with <key>" in line["exc"]
    text = log_file.read_text(encoding="utf-8")
    for value in ("api_key", EMAIL, FORTY_CHAR_KEY):
        assert value not in text


def test_a_secret_in_a_string_extra_is_masked(log_file: Path) -> None:
    logging.getLogger("nextron.x").info("sent", extra={"to": EMAIL, "lead_id": ULID})

    [line] = read_lines(log_file)
    assert (line["to"], line["lead_id"]) == ("<email>", ULID)


def test_a_secret_in_stack_info_is_masked() -> None:
    record = logging.LogRecord("nextron.x", logging.WARNING, __file__, 1, "here", None, None)
    record.stack_info = f"Stack (most recent call last):\n  token={FORTY_CHAR_KEY}"

    RedactingFilter().filter(record)

    assert record.stack_info == "Stack (most recent call last):\n  token=<redacted>"


# Row: child logger
def test_a_child_logger_is_masked_by_the_handler_filters(log_file: Path) -> None:
    root = logging.getLogger()
    for root_filter in root.filters[:]:
        root.removeFilter(root_filter)  # only the handler filters are left

    logging.getLogger("nextron.app.setup.api").warning("saved %s for %s", FORTY_CHAR_KEY, EMAIL)

    [line] = read_lines(log_file)
    assert line["logger"] == "nextron.app.setup.api"
    assert line["event"] == "saved <key> for <email>"


def test_the_root_logger_and_every_root_handler_carry_the_filter(logs_dir: Path) -> None:
    root = logging.getLogger()

    configure_logging(logs_dir)  # in the test body, after pytest attached its own handlers

    assert sum(isinstance(f, RedactingFilter) for f in root.filters) == 1
    assert len(root.handlers) == 2
    for handler in root.handlers:
        assert any(isinstance(f, RedactingFilter) for f in handler.filters)


def test_a_record_on_the_root_logger_is_masked(log_file: Path) -> None:
    logging.getLogger().warning("root saw %s", EMAIL)

    [line] = read_lines(log_file)
    assert line["event"] == "root saw <email>"


# Row: ULID kept
def test_a_ulid_survives_in_extra_and_in_the_message(log_file: Path) -> None:
    logging.getLogger("nextron.x").info(f"lead {ULID} scored", extra={"lead_id": ULID})

    [line] = read_lines(log_file)
    assert line["event"] == f"lead {ULID} scored"
    assert line["lead_id"] == ULID


# Row: SDK noise
def test_sdk_loggers_stay_at_warning_when_the_root_is_at_debug(logs_dir: Path) -> None:
    log_file = configure_logging(logs_dir, logging.DEBUG)

    assert set(SDK_LOGGERS) == {
        "googleapiclient",
        "google_auth_oauthlib",
        "google.auth",
        "google_auth_httplib2",
        "httplib2",
        "httpx2",
        "httpcore2",
        "urllib3",
        "requests_oauthlib",
        "oauthlib",
        "anthropic",
    }
    for name in SDK_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING
        sdk = logging.getLogger(name)
        sdk.debug("sdk debug")
        sdk.info("sdk info")
        logging.getLogger(f"{name}.child").info("sdk child info")
        sdk.warning("sdk warning")
    logging.getLogger("nextron.x").debug("engine debug")

    events = [(line["logger"], line["event"]) for line in read_lines(log_file)]
    assert events == [(name, "sdk warning") for name in SDK_LOGGERS] + [
        ("nextron.x", "engine debug")
    ]


def test_the_default_level_is_info(log_file: Path) -> None:
    logging.getLogger("nextron.x").debug("hidden")
    logging.getLogger("nextron.x").info("shown")

    assert [line["event"] for line in read_lines(log_file)] == ["shown"]
    assert logging.getLogger().level == logging.INFO


# Row: rotation
def test_the_file_rotates_and_keeps_at_most_ten_files(logs_dir: Path) -> None:
    log_file = configure_logging(logs_dir, max_bytes=1024)
    log = logging.getLogger("nextron.x")

    for number in range(600):
        log.info("tick", extra={"number": number, "lead_id": ULID})

    files = sorted(path.name for path in logs_dir.iterdir())
    assert f"{LOG_FILE_NAME}.1" in files
    assert files == sorted([LOG_FILE_NAME] + [f"{LOG_FILE_NAME}.{n}" for n in range(1, 10)])
    for path in logs_dir.iterdir():
        assert path.stat().st_size <= 1024
        assert all(line["event"] == "tick" for line in read_lines(path))
    assert read_lines(log_file)[-1]["number"] == 599


def test_rotation_defaults_are_10_mib_and_10_files(log_file: Path) -> None:
    [handler] = [h for h in logging.getLogger().handlers if hasattr(h, "maxBytes")]

    assert MAX_BYTES == handler.maxBytes == 10 * 1024 * 1024  # type: ignore[attr-defined]
    assert BACKUP_COUNT == handler.backupCount == 9  # type: ignore[attr-defined]


def test_the_file_is_created_on_the_first_record(logs_dir: Path) -> None:
    log_file = configure_logging(logs_dir)

    assert not log_file.exists()
    logging.getLogger("nextron.x").info("first")
    assert log_file.exists()


def test_lines_are_utf8(log_file: Path) -> None:
    logging.getLogger("nextron.x").info("café ✓ Zürich")

    assert "café ✓ Zürich" in log_file.read_bytes().decode("utf-8")


def test_stderr_gets_warnings_and_above_redacted(
    logs_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(logs_dir)  # after capsys swapped sys.stderr, so the handler writes to it
    log = logging.getLogger("nextron.x")

    log.info("quiet")
    log.warning("loud for %s", EMAIL)

    assert capsys.readouterr().err == "WARNING nextron.x: loud for <email>\n"


def test_repeat_calls_replace_the_handlers(logs_dir: Path) -> None:
    root = logging.getLogger()
    foreign = logging.StreamHandler(io.StringIO())
    root.addHandler(foreign)

    configure_logging(logs_dir)
    first = root.handlers[:]
    log_file = configure_logging(logs_dir)

    assert foreign not in root.handlers
    assert len(root.handlers) == 2
    assert not set(first) & set(root.handlers)
    assert sum(isinstance(f, RedactingFilter) for f in root.filters) == 1
    logging.getLogger("nextron.x").info("once")
    assert [line["event"] for line in read_lines(log_file)] == ["once"]


def test_unformattable_arguments_never_reach_the_caller(log_file: Path) -> None:
    logging.getLogger("nextron.x").info("count %d of %s", "not a number")

    [line] = read_lines(log_file)
    assert line["event"] == "count %d of %s (log arguments could not be formatted)"


def test_the_filter_redacts_once_and_drops_the_raw_exception() -> None:
    try:
        raise ValueError(f"bad {EMAIL}")
    except ValueError:
        record = logging.LogRecord(
            "nextron.x", logging.ERROR, __file__, 1, "failed %s", (EMAIL,), sys.exc_info()
        )
    record.to = EMAIL
    redacting = RedactingFilter()

    assert redacting.filter(record) is True
    assert (record.msg, record.args, record.exc_info, record.to) == (
        "failed <email>",
        None,
        None,
        "<email>",
    )
    assert record.exc_text is not None and "ValueError: bad <email>" in record.exc_text
    assert EMAIL not in record.exc_text
    record.msg = "changed"
    assert redacting.filter(record) is True
    assert record.msg == "changed"  # already redacted: the second filter leaves it alone


# Serve: logging starts only once the lock is held
class LoggingSpy:
    """Stands in for ``configure_logging`` and records whether the lock was held."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, bool]] = []

    def __call__(self, logs_dir: Path, level: int = logging.INFO) -> Path:
        probe = InstanceLock(logs_dir.parent / "nextron.lock")
        try:
            probe.acquire()
        except LockHeldError:
            held = True
        else:
            probe.release()
            held = False
        self.calls.append((logs_dir, held))
        return configure_logging(logs_dir, level)


@pytest.fixture
def dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``NEXTRON_ENV=dev`` with a temporary data dir; returns the data dir."""
    data_dir = tmp_path / "data" / "dev"
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(data_dir))
    return data_dir


def test_serve_starts_logging_after_the_lock_and_logs_start_and_stop(
    dev: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = LoggingSpy()
    monkeypatch.setattr(main, "configure_logging", spy)
    seen: list[list[str]] = []

    def server(config: BootstrapConfig, reload: bool) -> None:
        seen.append([line["event"] for line in read_lines(dev / "logs" / LOG_FILE_NAME)])

    assert main.serve(probes=fake_probes(), run_server=server, err=io.StringIO()) == 0

    assert spy.calls == [(dev / "logs", True)]
    assert seen == [["engine_started"]]
    lines = read_lines(dev / "logs" / LOG_FILE_NAME)
    assert [line["event"] for line in lines] == ["engine_started", "engine_stopped"]
    for line in lines:
        assert (line["logger"], line["env"], line["version"]) == (
            "nextron.main",
            "dev",
            __version__,
        )
    assert lines[1]["exit_code"] == 0


@pytest.mark.parametrize(
    ("failure", "exit_code", "cause"),
    [(SystemExit(3), 2, "server_start_failed"), (RuntimeError("boom"), 1, "engine_crashed")],
)
def test_serve_logs_the_cause_and_engine_stopped_when_the_server_fails(
    failure: BaseException, exit_code: int, cause: str, dev: Path
) -> None:
    def fail(config: BootstrapConfig, reload: bool) -> None:
        raise failure

    if isinstance(failure, SystemExit):
        assert main.serve(probes=fake_probes(), run_server=fail, err=io.StringIO()) == exit_code
    else:
        with pytest.raises(RuntimeError):
            main.serve(probes=fake_probes(), run_server=fail, err=io.StringIO())

    lines = read_lines(dev / "logs" / LOG_FILE_NAME)
    assert [line["event"] for line in lines] == ["engine_started", cause, "engine_stopped"]
    assert lines[2]["exit_code"] == exit_code
    if isinstance(failure, SystemExit):
        assert (lines[1]["level"], lines[1]["exit_code"]) == ("ERROR", 3)
    else:
        assert lines[1]["level"] == "ERROR"
        assert "RuntimeError: boom" in lines[1]["exc"]


def test_a_crash_logged_by_serve_is_not_logged_again_by_the_hook(dev: Path) -> None:
    def crash(config: BootstrapConfig, reload: bool) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as raised:
        main.serve(probes=fake_probes(), run_server=crash, err=io.StringIO())
    sys.excepthook(RuntimeError, raised.value, raised.value.__traceback__)

    events = [line["event"] for line in read_lines(dev / "logs" / LOG_FILE_NAME)]
    assert events.count("engine_crashed") == 1


def test_a_busy_port_is_logged_as_a_failed_startup_check(dev: Path) -> None:
    port = PortProbe(free=False)

    code = main.serve(
        probes=fake_probes(port=port), run_server=ServerNeverRuns(), err=io.StringIO()
    )

    assert code == 2
    [line] = read_lines(dev / "logs" / LOG_FILE_NAME)
    assert (line["event"], line["level"], line["check"]) == (
        "startup_check_failed",
        "ERROR",
        "port_free",
    )


class ServerNeverRuns:
    def __call__(self, config: BootstrapConfig, reload: bool) -> None:
        raise AssertionError("the server must not start")


# Row: lock held
def test_a_second_instance_never_opens_the_log_file(
    dev: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = LoggingSpy()
    monkeypatch.setattr(main, "configure_logging", spy)
    handlers = logging.getLogger().handlers[:]
    dev.mkdir(parents=True)

    with InstanceLock(dev / "nextron.lock"):  # a fake first instance holds the lock
        code = main.serve(probes=fake_probes(), err=io.StringIO())

    assert code == 3
    assert spy.calls == []
    assert logging.getLogger().handlers == handlers
    assert not (dev / "logs" / LOG_FILE_NAME).exists()


def test_doctor_never_opens_the_log_file(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = LoggingSpy()
    monkeypatch.setattr(main, "configure_logging", spy)

    main.doctor(probes=fake_probes(), out=io.StringIO(), err=io.StringIO())

    assert spy.calls == []
    assert not dev.exists()


def test_uvicorn_records_reach_the_file_with_the_query_removed(log_file: Path) -> None:
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "GET", "/enter?code=abc123", "1.1", 200
    )

    [line] = read_lines(log_file)
    assert line["logger"] == "uvicorn.access"
    assert line["event"] == '127.0.0.1:50000 - "GET /enter HTTP/1.1" 200'


# Extras that are not plain scalars
class Lane(StrEnum):
    BULK = "bulk"


class Priority(IntEnum):
    HIGH = 1


class Shape(Enum):
    PAIR = (1, 2)


def test_ids_enums_paths_and_dates_in_extra_are_written_as_text(log_file: Path) -> None:
    lead_id = UlidValue()
    logging.getLogger("nextron.x").info(
        "typed",
        extra={
            "lead_id": lead_id,
            "request_id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "lane": Lane.BULK,
            "priority": Priority.HIGH,
            "shape": Shape.PAIR,
            "folder": PurePosixPath("/data/dev"),
            "export": PurePosixPath(f"/exports/{EMAIL}.csv"),
            "at": datetime(2026, 11, 16, 9, 0, 0, 123000, tzinfo=UTC),
            "day": date(2026, 11, 16),
        },
    )

    [line] = read_lines(log_file)
    assert line["lead_id"] == str(lead_id)
    assert len(line["lead_id"]) == 26
    assert line["request_id"] == "<key>"  # 36 characters of hex and "-": the 32+ rule
    assert (line["lane"], line["priority"], line["shape"]) == ("bulk", 1, "Shape.PAIR")
    assert (line["folder"], line["export"]) == ("/data/dev", "/exports/<email>")
    assert (line["at"], line["day"]) == ("2026-11-16T09:00:00.123000+00:00", "2026-11-16")


def test_stack_info_is_written_as_stack(log_file: Path) -> None:
    logging.getLogger("nextron.x").warning("here", stack_info=True)

    [line] = read_lines(log_file)
    assert line["stack"].startswith("Stack (most recent call last):")


def test_the_formatter_redacts_the_stack() -> None:
    record = logging.LogRecord("nextron.x", logging.WARNING, __file__, 1, "here", None, None)
    record.stack_info = f"Stack (most recent call last):\n  lead {EMAIL}"

    line = json.loads(JsonLineFormatter().format(record))

    assert line["stack"] == "Stack (most recent call last):\n  lead <email>"


def test_unicode_line_separators_are_escaped(log_file: Path) -> None:
    logging.getLogger("nextron.x").info("a b c\x85d")
    logging.getLogger("nextron.x").info("next")

    text = log_file.read_text(encoding="utf-8")
    assert not {" ", " ", "\x85"} & set(text)
    assert r"a\u2028b\u2029c\u0085d" in text  # the escapes, written out
    assert [line["event"] for line in read_lines(log_file)] == ["a b c\x85d", "next"]


def test_a_lone_surrogate_is_escaped_not_dropped(log_file: Path) -> None:
    log = logging.getLogger("nextron.x")

    log.info("odd", extra={"label": "bad\udcffname"})
    log.info("next")

    lines = read_lines(log_file)
    assert [line["event"] for line in lines] == ["odd", "next"]
    assert lines[0]["label"] == "bad\udcffname"


# Crashes, thread crashes, warnings and the last-resort handler
def test_an_uncaught_exception_is_logged_redacted(logs_dir: Path) -> None:
    log_file = configure_logging(logs_dir)

    try:
        raise RuntimeError(f"crashed for {EMAIL}")
    except RuntimeError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)

    [line] = read_lines(log_file)
    assert (line["event"], line["level"], line["logger"]) == ("engine_crashed", "CRITICAL", "root")
    assert "RuntimeError: crashed for <email>" in line["exc"]
    assert EMAIL not in log_file.read_text(encoding="utf-8")


def test_keyboard_interrupt_goes_to_the_default_hook(
    logs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = configure_logging(logs_dir)
    seen: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "__excepthook__", lambda kind, exc, tb: seen.append(kind))

    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)

    assert seen == [KeyboardInterrupt]
    assert not log_file.exists()


def test_a_thread_crash_is_logged_redacted(logs_dir: Path) -> None:
    log_file = configure_logging(logs_dir)

    def fail() -> None:
        raise RuntimeError(f"thread failed for {EMAIL}")

    thread = threading.Thread(target=fail, name="worker-1")
    thread.start()
    thread.join()

    [line] = read_lines(log_file)
    assert (line["event"], line["level"]) == ("thread_crashed", "ERROR")
    assert "RuntimeError: thread failed for <email>" in line["exc"]


def test_warnings_are_logged_redacted(logs_dir: Path) -> None:
    log_file = configure_logging(logs_dir)

    warnings.warn(f"deprecated for {EMAIL}", UserWarning, stacklevel=1)

    [line] = read_lines(log_file)
    assert (line["logger"], line["level"]) == ("py.warnings", "WARNING")
    assert "UserWarning: deprecated for <email>" in line["event"]


def test_the_last_resort_handler_redacts(capsys: pytest.CaptureFixture[str]) -> None:
    logging.getLogger().handlers.clear()  # no handler at all, as before configure_logging

    logging.getLogger("nextron.x").warning("early for %s", EMAIL)

    assert capsys.readouterr().err == "WARNING nextron.x: early for <email>\n"
    last_resort = logging.lastResort
    assert last_resort is not None
    assert any(isinstance(f, RedactingFilter) for f in last_resort.filters)


def test_a_process_that_never_configures_logging_still_redacts() -> None:
    """The ``serve --reload`` worker imports ``nextron.main`` and never configures logging."""
    code = (
        "import logging, nextron.main; "
        "logging.getLogger('uvicorn.error').warning('worker saw %s', 'ann@x.co')"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == "WARNING uvicorn.error: worker saw <email>\n"
