"""Logging setup (the *Logging* convention; NFR-17, NFR-18). Composition-root helper: only
``nextron.main`` uses it.

``configure_logging`` points the root logger at two handlers:

- ``<data dir>\\logs\\nextron.log``: one JSON object per line, UTF-8, rotating at 10 MiB and
  keeping 10 files in all (``nextron.log`` and ``nextron.log.1`` to ``.9``). The file is opened
  on the first record, not before.
- stderr: records at WARNING and above, as ``LEVEL logger: message`` text.

It also sends uncaught exceptions (``engine_crashed``), exceptions that end a thread
(``thread_crashed``) and Python warnings (logger ``py.warnings``) through those handlers, so
none of them reaches stderr unredacted.

Each JSON line holds ``ts`` (UTC, ISO 8601 with milliseconds and ``Z``), ``level``, ``logger``,
``event`` (the message), every scalar ``extra`` field (the IDs a record carries) and, when there
is one, ``exc`` (the traceback) and ``stack`` (from ``stack_info=True``). ULIDs, UUIDs, enums,
paths, dates and datetimes are written as text; other non-scalar extras are dropped. U+2028,
U+2029 and U+0085 are escaped, so every reader sees one record per line. Logs hold IDs only:
pass IDs as ``extra`` fields and never log personal data on purpose.

Redaction is the safety net. A ``RedactingFilter`` sits on the root logger and on each of its
handlers, because Python applies a logger's filters only to records logged on that logger, not
to records that propagate up from child loggers such as ``nextron.app.setup.api``. The filter
runs ``nextron.domain.redaction.redact`` over the message after %-formatting, the traceback, the
stack and every string ``extra`` value; the JSON formatter redacts again, so it is safe on its
own. Importing this module also replaces ``logging.lastResort`` (what Python uses when no
handler is configured: ``doctor``, records before ``configure_logging``, and the worker process
of ``serve --reload``) with a redacting stderr handler at WARNING.

The loggers of the SDKs in ``SDK_LOGGERS`` stay at WARNING whatever the root level, because at
DEBUG they log request details (and APScheduler logs two INFO lines per firing). Uvicorn runs
with ``log_config=None``, so its records, the access log included, reach these handlers through
the root logger.
"""

import json
import logging
import math
import sys
import threading
from datetime import UTC, date, datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePath
from types import TracebackType
from typing import Final, TextIO
from uuid import UUID

from ulid import ULID

from nextron.domain.redaction import redact

LOG_FILE_NAME: Final = "nextron.log"
MAX_BYTES: Final = 10 * 1024 * 1024
BACKUP_COUNT: Final = 9  # with nextron.log itself: 10 files
DEFAULT_LEVEL: Final = logging.INFO
STDERR_LEVEL: Final = logging.WARNING
STDERR_FORMAT: Final = "%(levelname)s %(name)s: %(message)s"
SDK_LOGGERS: Final = (
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
    "apscheduler",  # its INFO lines say "Running job" and "executed successfully" per firing
)
SDK_LEVEL: Final = logging.WARNING
CRASH_EVENT: Final = "engine_crashed"
THREAD_CRASH_EVENT: Final = "thread_crashed"

# The attributes every LogRecord has; anything else on a record came from ``extra``.
_RECORD_ATTRIBUTES: Final = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"message", "asctime"}
_JSON_FIELDS: Final = frozenset({"ts", "level", "logger", "event", "exc", "stack"})
# Uvicorn adds an ANSI-coloured copy of its message template; it carries nothing new.
_IGNORED_EXTRAS: Final = frozenset({"color_message"})
_REDACTED_MARK: Final = "_nextron_redacted"
_OWNED_MARK: Final = "_nextron_handler"
_LOGGED_MARK: Final = "_nextron_logged"
_TRACEBACKS: Final = logging.Formatter()
_DROP: Final = object()
# Characters that str.splitlines() and many viewers treat as line breaks but JSON leaves raw.
_LINE_BREAKS: Final = str.maketrans(
    {" ": "\\u2028", " ": "\\u2029", "\x85": "\\u0085"}
)


def _message(record: logging.LogRecord) -> str:
    """The record's message after %-formatting; never raises, like logging itself."""
    try:
        return record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format or argument must not reach the caller
        try:
            template = str(record.msg)
        except Exception:  # noqa: BLE001
            template = "<unprintable log message>"
        return f"{template} (log arguments could not be formatted)"


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in vars(record).items()
        if key not in _RECORD_ATTRIBUTES and not key.startswith("_")
    }


class RedactingFilter(logging.Filter):
    """Redact a record in place; it never drops one.

    The message is formatted with its arguments first, so a secret passed as an argument is
    redacted too, then ``args`` is cleared. The traceback is rendered to ``exc_text`` and
    redacted, and ``exc_info`` is cleared, so no handler can render the raw exception again. A
    record is redacted once and marked; every later filter it meets returns at once.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, _REDACTED_MARK, False):
            return True
        record.msg = redact(_message(record))
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = _TRACEBACKS.formatException(record.exc_info)
        record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        for key, value in _extras(record).items():
            if isinstance(value, str):
                setattr(record, key, redact(value))
        setattr(record, _REDACTED_MARK, True)
        return True


class _StderrHandler(logging.StreamHandler[TextIO]):
    """Writes to whatever ``sys.stderr`` is when a record arrives (as ``logging.lastResort``)."""

    def __init__(self, level: int) -> None:
        logging.Handler.__init__(self, level)  # no stream of its own

    @property
    def stream(self) -> TextIO:  # type: ignore[override]
        return sys.stderr


def _stderr_handler(level: int) -> logging.Handler:
    handler = _StderrHandler(level)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter(STDERR_FORMAT))
    return handler


# Before configure_logging runs, and in processes that never run it, Python sends WARNING and
# above to logging.lastResort; this one redacts.
logging.lastResort = _stderr_handler(logging.WARNING)


def utc_timestamp(created: float) -> str:
    """``created`` (seconds since the epoch) as UTC ISO 8601 with milliseconds and ``Z``."""
    moment = datetime.fromtimestamp(created, tz=UTC)
    return moment.isoformat(timespec="milliseconds").removesuffix("+00:00") + "Z"


def _scalar(value: object) -> object:
    """A JSON-safe scalar for an ``extra`` value, or ``_DROP`` for anything else."""
    if isinstance(value, Enum):  # before str and int: StrEnum and IntEnum are both
        inner = value.value
        if inner is None or isinstance(inner, str | int | float):
            return _scalar(inner)
        return redact(str(value))
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, int):  # bool is an int
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, date):  # datetime is a date
        return redact(value.isoformat())
    if isinstance(value, ULID | UUID | PurePath):
        return redact(str(value))
    return _DROP


class JsonLineFormatter(logging.Formatter):
    """Format a record as one JSON object on one line; see the module docstring for the fields."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": utc_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(_message(record)),
        }
        for key, value in _extras(record).items():
            if key in _JSON_FIELDS or key in _IGNORED_EXTRAS:
                continue
            scalar = _scalar(value)
            if scalar is not _DROP:
                line[key] = scalar
        exc = record.exc_text
        if not exc and record.exc_info:
            exc = self.formatException(record.exc_info)
        if exc:
            line["exc"] = redact(exc)
        if record.stack_info:
            line["stack"] = redact(record.stack_info)
        return json.dumps(line, ensure_ascii=False).translate(_LINE_BREAKS)


def mark_logged(exc: BaseException) -> None:
    """Note that ``exc`` was logged already, so the uncaught-exception hook skips it."""
    try:
        setattr(exc, _LOGGED_MARK, True)
    except Exception:  # noqa: BLE001 - a few built-in exceptions take no attributes
        pass


def _log_uncaught(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    if getattr(exc, _LOGGED_MARK, False):
        return
    logging.getLogger().critical(CRASH_EVENT, exc_info=(exc_type, exc, tb))


def _log_thread_crash(args: threading.ExceptHookArgs) -> None:
    if issubclass(args.exc_type, SystemExit):  # threading's own hook ignores it too
        return
    exc_info = (args.exc_type, args.exc_value, args.exc_traceback)
    logging.getLogger().error(THREAD_CRASH_EVENT, exc_info=exc_info)  # type: ignore[arg-type]


def configure_logging(
    logs_dir: Path, level: int = DEFAULT_LEVEL, *, max_bytes: int = MAX_BYTES
) -> Path:
    """Send every log record to ``logs_dir / "nextron.log"`` and stderr, redacted.

    Replaces every handler on the root logger, closing the ones an earlier call created, so a
    repeat call leaves exactly one file handler, one stderr handler and one root filter. A
    handler this function didn't create is removed too: its formatter could write what the
    filter can't see. Also installs ``sys.excepthook`` and ``threading.excepthook`` hooks that
    log, and captures warnings. ``logs_dir`` must exist; the file is created on the first
    record. ``max_bytes`` exists for tests. Returns the log file's path.
    """
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        if getattr(handler, _OWNED_MARK, False):
            handler.close()
    for old in [f for f in root.filters if isinstance(f, RedactingFilter)]:
        root.removeFilter(old)

    log_file = logs_dir / LOG_FILE_NAME
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
        errors="backslashreplace",
        delay=True,
    )
    file_handler.setFormatter(JsonLineFormatter())
    stderr_handler = _StderrHandler(STDERR_LEVEL)
    stderr_handler.setFormatter(logging.Formatter(STDERR_FORMAT))

    redacting = RedactingFilter()
    for handler in (file_handler, stderr_handler):
        handler.addFilter(redacting)
        setattr(handler, _OWNED_MARK, True)
        root.addHandler(handler)
    root.addFilter(redacting)
    root.setLevel(level)
    for name in SDK_LOGGERS:
        logging.getLogger(name).setLevel(SDK_LEVEL)
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_thread_crash
    logging.captureWarnings(True)
    return log_file
