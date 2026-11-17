"""The dev and test sink (AD-14, NFR-8, NFR-17): mail and alerts reach only an allowlist.

Import rule: ``nextron.ports``, ``nextron.domain`` and the standard library. The sink talks to
no external system: it writes local files only.

Outside ``prod``, ``nextron.main.build_role_adapter`` wraps every adapter registered for the
``mail`` role in ``MailSink`` and every one for the ``alert`` role in ``AlertSink``. Each takes
the live adapter (``wrapped``, exposed as ``.wrapped``), an ``allowlist`` provider (a callable
returning the ``sink_allowlist`` setting's entries) and a ``SinkRecorder``. On every send:

1. The sink calls ``allowlist()``, so a change on the Settings page applies to the next send.
   If it raises, or returns a bare ``str`` or ``bytes`` (which would split into characters),
   the allowlist counts as empty: the message is recorded, and the sink logs
   ``sink_allowlist_unreadable`` (``port``, ``error`` = the exception's or the value's type
   name) as a WARNING. A message is never delivered when the allowlist can't be read.
2. A recipient that ``nextron.domain.sink.allowlisted`` accepts is passed on: the sink logs
   ``sink_passed`` (``port``), calls ``wrapped.send(message)`` and returns its delivery
   unchanged, so the wrapped adapter's errors propagate.
3. Anyone else is recorded, never sent: the recorder appends one line to
   ``<folder>\\<UTC date>.jsonl`` and the sink returns a delivery with every id ``None`` and
   ``sink=True``, then logs ``sink_recorded`` (``port``). ``wrapped`` is never called.

The line is one ASCII JSON object (``ensure_ascii``, so a Unicode line separator in the text
can't split a record): ``{"time", "port", "recipient", "subject" (mail only), "text"}``, where
``time`` is UTC as ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` and ``port`` is ``mail`` or ``alert``. The
folder is created when it is missing. One module-level lock serialises the writes of every
recorder in the process. A write that fails raises ``AdapterError(False, "sink_write_failed",
...)``, and nothing is sent.

No log record carries a recipient, a subject or any text. The sink files do: they hold
recipient addresses and message text, under the environment's own data dir.
"""

import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC
from pathlib import Path
from typing import Final

from nextron.domain.sink import allowlisted
from nextron.ports.alert import AlertDelivery, AlertMessage, AlertPort
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.mail import MailDelivery, MailMessage, MailPort

ALERT_PORT: Final = "alert"
MAIL_PORT: Final = "mail"
SINK_WRITE_FAILED: Final = "sink_write_failed"
FILE_SUFFIX: Final = ".jsonl"

Allowlist = Callable[[], Iterable[str]]

# Serialises every sink file write in this process (see the module docstring).
_WRITE_LOCK: Final = threading.Lock()

log = logging.getLogger(__name__)


class SinkRecorder:
    """Appends recorded messages to ``<folder>\\<UTC date>.jsonl``, dated by ``clock``."""

    def __init__(self, folder: Path, clock: Clock) -> None:
        self._folder = Path(folder)
        self._clock = clock

    @property
    def folder(self) -> Path:
        return self._folder

    def record(self, port: str, fields: Mapping[str, object]) -> None:
        """Append ``{"time", "port", **fields}`` as one ASCII JSON line; raise ``AdapterError``
        ``sink_write_failed`` when it can't be written."""
        try:
            now = self._clock.now().astimezone(UTC)
            line = json.dumps(
                {"time": f"{now:%Y-%m-%dT%H:%M:%S.%fZ}", "port": port, **fields},
                ensure_ascii=True,
                separators=(",", ":"),
            )
            path = self._folder / f"{now:%Y-%m-%d}{FILE_SUFFIX}"
            with _WRITE_LOCK:
                self._folder.mkdir(parents=True, exist_ok=True)
                with path.open("ab") as file:
                    file.write(line.encode("ascii") + b"\n")
        except (OSError, TypeError, ValueError) as exc:
            raise AdapterError(
                False, SINK_WRITE_FAILED, f"the sink could not record a message in {self._folder}"
            ) from exc


def _entries(port: str, allowlist: Allowlist) -> tuple[object, ...]:
    """The allowlist's entries now; empty, with a WARNING, when they can't be read or the
    provider returns a bare ``str`` or ``bytes`` (which would split into characters)."""
    try:
        entries = allowlist()
        if isinstance(entries, (str, bytes)):
            error = type(entries).__name__
        else:
            return tuple(entries)
    except Exception as exc:  # noqa: BLE001 - any failure fails closed: nothing is delivered
        error = type(exc).__name__
    log.warning("sink_allowlist_unreadable", extra={"port": port, "error": error})
    return ()


class AlertSink:
    """``AlertPort`` outside ``prod``: delivers to allowlisted recipients, records the rest."""

    def __init__(self, wrapped: AlertPort, allowlist: Allowlist, recorder: SinkRecorder) -> None:
        self._wrapped = wrapped
        self._allowlist = allowlist
        self._recorder = recorder

    @property
    def wrapped(self) -> AlertPort:
        return self._wrapped

    def send(self, message: AlertMessage) -> AlertDelivery:
        if allowlisted(message.recipient, _entries(ALERT_PORT, self._allowlist)):
            log.info("sink_passed", extra={"port": ALERT_PORT})
            return self._wrapped.send(message)
        self._recorder.record(ALERT_PORT, {"recipient": message.recipient, "text": message.text})
        log.info("sink_recorded", extra={"port": ALERT_PORT})
        return AlertDelivery(None, sink=True)


class MailSink:
    """``MailPort`` outside ``prod``: delivers to allowlisted recipients, records the rest."""

    def __init__(self, wrapped: MailPort, allowlist: Allowlist, recorder: SinkRecorder) -> None:
        self._wrapped = wrapped
        self._allowlist = allowlist
        self._recorder = recorder

    @property
    def wrapped(self) -> MailPort:
        return self._wrapped

    def send(self, message: MailMessage) -> MailDelivery:
        if allowlisted(message.recipient, _entries(MAIL_PORT, self._allowlist)):
            log.info("sink_passed", extra={"port": MAIL_PORT})
            return self._wrapped.send(message)
        self._recorder.record(
            MAIL_PORT,
            {"recipient": message.recipient, "subject": message.subject, "text": message.text},
        )
        log.info("sink_recorded", extra={"port": MAIL_PORT})
        return MailDelivery(None, None, sink=True)
