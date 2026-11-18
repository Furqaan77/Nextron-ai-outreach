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

**A deletion** (``AlertSink.delete(recipient, message_id)``, Story 3.14) is passed to
``wrapped.delete`` unchanged, with no allowlist check: it sends nothing, and the sink recorded
nothing it could delete (a recorded alert has no message id).

**An erasure** (Story 3.14): ``SinkRecorder`` implements ``nextron.ports.sink.SinkFilesPort``.
``erase_recipient(address_hash)`` runs under the write lock. It first deletes any leftover
``*.jsonl.erasing`` file in the folder (from an erasure cut off before its replace). Then, in
each ``*.jsonl`` file, every ``mail`` line whose ``recipient``'s
``nextron.domain.erasure.tombstone_hash("address", recipient)`` equals ``address_hash`` gets
``recipient``, ``subject`` and ``text`` set to ``[erased]``; ``time`` and ``port`` are kept, and
every other line (an alert, another address, a line that isn't a JSON object) is kept byte for
byte. A file that changed is written to ``<name>.erasing``, fsynced, then put in place with
``os.replace``, so a crash leaves the old file or the new one, never half of one. It returns
``SinkErasure(files, lines)``: how many files and lines changed. A missing folder changes
nothing. An ``OSError`` raises the retryable ``AdapterError("sink_erase_failed")``.
"""

import json
import logging
import os
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC
from pathlib import Path
from typing import Final

from nextron.domain.erasure import REDACTED, tombstone_hash
from nextron.domain.sink import allowlisted
from nextron.ports.alert import AlertDelivery, AlertMessage, AlertPort
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.mail import MailDelivery, MailMessage, MailPort
from nextron.ports.sink import SINK_ERASE_FAILED, SinkErasure

ALERT_PORT: Final = "alert"
MAIL_PORT: Final = "mail"
SINK_WRITE_FAILED: Final = "sink_write_failed"
FILE_SUFFIX: Final = ".jsonl"
ERASING_SUFFIX: Final = ".erasing"
ERASED_FIELDS: Final = ("recipient", "subject", "text")

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

    def erase_recipient(self, address_hash: str) -> SinkErasure:
        """Tombstone every recorded mail to the address whose hash is ``address_hash``; see the
        module docstring."""
        try:
            with _WRITE_LOCK:
                if not self._folder.is_dir():
                    return SinkErasure(0, 0)
                for leftover in sorted(self._folder.glob(f"*{FILE_SUFFIX}{ERASING_SUFFIX}")):
                    leftover.unlink(missing_ok=True)
                files = lines = 0
                for path in sorted(self._folder.glob(f"*{FILE_SUFFIX}")):
                    changed = _erase_lines(path.read_bytes(), address_hash)
                    if changed is None:
                        continue
                    content, count = changed
                    _replace(path, content)
                    files += 1
                    lines += count
                return SinkErasure(files, lines)
        except OSError:
            raise AdapterError(
                True, SINK_ERASE_FAILED, "the sink could not erase a recipient's messages"
            ) from None


def _erased_line(line: bytes, address_hash: str) -> bytes | None:
    """``line`` with its mail's personal fields tombstoned, or ``None`` when it isn't a mail
    to the address ``address_hash``."""
    try:
        record = json.loads(line)
    except ValueError:  # not JSON (or not UTF-8): kept as it is
        return None
    if not isinstance(record, dict) or record.get("port") != MAIL_PORT:
        return None
    recipient = record.get("recipient")
    if not isinstance(recipient, str) or tombstone_hash("address", recipient) != address_hash:
        return None
    for name in ERASED_FIELDS:
        if name in record:
            record[name] = REDACTED
    return json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _erase_lines(content: bytes, address_hash: str) -> tuple[bytes, int] | None:
    """The file ``content`` with the address's mails tombstoned and how many, or ``None`` when
    none is."""
    out: list[bytes] = []
    count = 0
    for line in content.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        erased = _erased_line(body, address_hash) if body else None
        if erased is None:
            out.append(line)
        else:
            out.append(erased + line[len(body) :])
            count += 1
    return (b"".join(out), count) if count else None


def _replace(path: Path, content: bytes) -> None:
    """Write ``content`` to ``<path>.erasing``, fsync it, then replace ``path`` with it."""
    staging = path.with_name(path.name + ERASING_SUFFIX)
    with staging.open("wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(staging, path)


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

    def delete(self, recipient: str, message_id: str) -> bool:
        """Pass the deletion to ``wrapped``: it sends nothing (see the module docstring)."""
        return self._wrapped.delete(recipient, message_id)


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
