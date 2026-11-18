"""The erasure ledger (FR-50, FR-52, AD-16, AD-17, AD-20, NFR-16, NFR-36, Story 3.16), part of
the ``compliance`` service.

A restored backup has forgotten every opt-out, void and erasure made after the copy was taken.
The ledger, ``<backup dir>\\erasure-ledger.jsonl`` (``nextron.domain.backup.ERASURE_LEDGER_FILE``),
mirrors each of them as one line (``nextron.domain.erasure_ledger``: hashes and ids only), is
copied off the PC with every backup (``nextron.app.ops.api.OffpcBackups``) and is reapplied
after a restore, before the job runner starts. File I/O never runs inside a transaction.

- ``LedgerFile(path)``: the file. ``exists()``; ``read() -> ParsedLedger | None`` (``None`` when
  it is missing; read with ``open(path, "rb")``); ``ensure()`` creates an empty file only when
  none is there (``open(path, "ab")``, which never truncates), and is the only code that
  creates the file; ``append(lines)``: under one process-wide ``threading.Lock``, opens the
  existing file for appending without creating it (``os.open`` with ``O_WRONLY | O_APPEND``, no
  ``O_CREAT``), so a missing ledger (a restore waiting for its copy) raises
  ``FileNotFoundError``, which takes the failed-append path below, and never gets a one-line
  stand-in that would let a reapply end recovery mode; when the file isn't empty and its last
  byte isn't ``\\n`` a ``\\n`` goes first (a torn tail becomes a malformed line of its own);
  then every encoded line in one ``write``, ``flush`` and ``os.fsync``. Nothing anywhere opens
  the ledger with another mode, truncates it, renames over it or removes it
  (``tests/contracts/test_erasure_ledger_contracts.py``).
- ``LedgerAppender(file, alerts)``: ``Compliance``'s ``LedgerRecorder``. ``record(lines)`` is
  called by a command inside a transaction and registers ``current().after_commit(...)``, so
  the lines are appended right after the commit and never for a rolled-back change. A failed
  append logs ``erasure_ledger_append_failed`` (ERROR: ``count``, ``code``) and, in a new
  transaction of the still-running use case, raises the ops alert ``erasure_ledger_failed``
  (``alerts.alert``; subject the first line's entry or Contact id; text "Erasure ledger append
  failed: <n> line(s) not written (<ErrorType>); the next engine start re-appends them"; every
  channel). Nothing is rolled back; a failure raising the alert logs
  ``erasure_ledger_alert_failed`` (ERROR, ``code``) and is swallowed.
- ``ErasureLedger(clock, file, compliance, erasures)``:

  - ``@use_case reapply(context=None) -> bool``, the ``reapply_erasure_ledger`` recovery step's
    handler (``context``, a ``RecoveryContext``, is not needed). A missing file logs
    ``erasure_ledger_missing`` (WARNING) and returns ``False``: the step stays ``pending`` and
    recovery mode holds sending. Otherwise the file is parsed outside any transaction (logging
    ``erasure_ledger_damaged_lines``, WARNING: ``malformed``, ``torn``, when either is set) and
    one transaction applies ``plan_reapply``: every void (``compliance.void_reapplied``), then
    every insert (``compliance.reinstate``), then every erasure. An erased line whose Contact is
    unknown or already tombstoned is skipped; a ``dsr`` line runs ``erase_contact(contact,
    REAPPLIED, "dsr")``, falling back to ``retention`` only when ``check_request_ref`` refuses
    ``REAPPLIED`` for that Contact's name (logged ``erasure_ledger_dsr_reapplied_as_retention``,
    WARNING, ``contact_id``); a ``retention`` line runs ``erase_contact(contact,
    None, "retention")``. Any exception rolls everything back and propagates (the step stays
    ``pending``). After the commit it logs ``erasure_ledger_reapplied`` (INFO: ``voided``,
    ``reinstated``, ``erased``) and returns ``True``.
  - ``@use_case sync() -> int``, the engine's first startup step: one transaction reads every
    entry, every voided entry and every erased Contact with its cause (``dsr`` when it has a
    completed ``erase`` request, else ``retention``; no behaviour reads ``events``) and whether
    a restore's reapply is pending. After it, a missing file is created only when no reapply is
    pending (else nothing is written and it returns ``0``, so a restored database can't make a
    missing ledger look present). Then every line whose ``identity`` the file lacks is appended
    in one ``append`` (entries by ``created_at``, voids by ``voided_at``, erasures by
    ``erased_at``), and it logs ``erasure_ledger_synced`` (INFO: ``appended``) and returns the
    count. This covers a crash between a commit and its append, a failed append, and the rows
    from before this story.

Nothing logged holds a value: counts, codes and ids only.
"""

import logging
import os
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, cast

from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import research as research_queries
from nextron.db.repos.compliance import data_subject_requests as requests_repo
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import UnitOfWork, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.contactability import KeyKind
from nextron.domain.data_subject_request import check_request_ref
from nextron.domain.erasure import ErasureCause
from nextron.domain.erasure_ledger import (
    REAPPLIED,
    Erased,
    LedgerLine,
    ParsedLedger,
    Suppressed,
    Voided,
    encode,
    identity,
    parse,
    plan_reapply,
)
from nextron.domain.recovery import RecoveryContext
from nextron.domain.suppression_entry import SuppressionSource
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

ERASURE_LEDGER_FAILED: Final = "erasure_ledger_failed"  # the ops alert's kind
APPEND_FAILED: Final = "erasure_ledger_append_failed"
ALERT_FAILED: Final = "erasure_ledger_alert_failed"
LEDGER_MISSING: Final = "erasure_ledger_missing"
DAMAGED_LINES: Final = "erasure_ledger_damaged_lines"
LEDGER_REAPPLIED: Final = "erasure_ledger_reapplied"
LEDGER_SYNCED: Final = "erasure_ledger_synced"
DSR_AS_RETENTION: Final = "erasure_ledger_dsr_reapplied_as_retention"
ALERT_CHANNELS: Final = (Channel.TELEGRAM, Channel.DASHBOARD)  # every channel, as ops alerts
_APPEND: Final = "ab"
# append to the existing file only: no O_CREAT, so a missing ledger is never created here
_APPEND_FLAGS: Final = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
_READ: Final = "rb"
_NEWLINE: Final = b"\n"

_LOCK: Final = threading.Lock()  # one append at a time in this process


def failed_text(count: int, error_type: str) -> str:
    """The ``erasure_ledger_failed`` alert's text."""
    return (
        f"Erasure ledger append failed: {count} line(s) not written ({error_type}); "
        "the next engine start re-appends them"
    )


def _subject(line: LedgerLine) -> str:
    """The line's entry or Contact id: the alert's subject."""
    return identity(line)[1]


class LedgerFile:
    """The erasure ledger file; see the module docstring."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def read(self) -> ParsedLedger | None:
        """The parsed file, or ``None`` when it is missing."""
        try:
            with open(self._path, _READ) as handle:
                data = handle.read()
        except FileNotFoundError:
            return None
        return parse(data)

    def ensure(self) -> None:
        """Create an empty file when none is there; never truncates."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(self._path, _APPEND):
            pass

    def append(self, lines: Iterable[LedgerLine]) -> None:
        """Append ``lines`` in one write, flushed and fsynced; see the module docstring."""
        data = "".join(encode(line) for line in lines).encode("utf-8")
        if not data:
            return
        with _LOCK:
            fd = os.open(self._path, _APPEND_FLAGS)  # FileNotFoundError when it is missing
            with os.fdopen(fd, _APPEND) as handle:
                if os.fstat(fd).st_size > 0 and self._last_byte() != _NEWLINE:
                    data = _NEWLINE + data  # a torn tail becomes its own (malformed) line
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())

    def _last_byte(self) -> bytes:
        with open(self._path, _READ) as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1)


class Alerts(Protocol):
    """What the appender needs of the ``notify`` service."""

    def alert(self, kind: str, subject: str, text: str, channels: Iterable[Channel]) -> object: ...


class LedgerAppender:
    """Appends recorded lines after their transaction commits; see the module docstring."""

    def __init__(self, file: LedgerFile, alerts: Alerts) -> None:
        self._file = file
        self._alerts = alerts

    @property
    def file(self) -> LedgerFile:
        return self._file

    def record(self, lines: Sequence[LedgerLine]) -> None:
        """Append ``lines`` once the open transaction commits."""
        listed = tuple(lines)
        if not listed:
            return
        uow = current()
        uow.after_commit(lambda: self._append(uow, listed))

    def _append(self, uow: UnitOfWork, lines: tuple[LedgerLine, ...]) -> None:
        try:
            self._file.append(lines)
        except Exception as error:  # noqa: BLE001 - the change is committed; sync re-appends it
            code = type(error).__name__
            log.error(APPEND_FAILED, extra={"count": len(lines), "code": code})
            try:
                with uow.transaction():
                    self._alerts.alert(
                        ERASURE_LEDGER_FAILED,
                        _subject(lines[0]),
                        failed_text(len(lines), code),
                        ALERT_CHANNELS,
                    )
            except Exception as alert_error:  # noqa: BLE001 - the log line still says it
                log.error(ALERT_FAILED, extra={"code": type(alert_error).__name__})


class ErasureLedger:
    """Reapplies the ledger after a restore, and syncs it from the database at startup; see
    the module docstring."""

    def __init__(
        self, clock: Clock, file: LedgerFile, compliance: Compliance, erasures: Erasures
    ) -> None:
        self._clock = clock
        self._file = file
        self._compliance = compliance
        self._erasures = erasures

    @use_case
    def reapply(self, context: RecoveryContext | None = None) -> bool:
        """Reapply every ledger line to a restored database; see the module docstring."""
        parsed = self._file.read()
        if parsed is None:
            log.warning(LEDGER_MISSING)
            return False
        if parsed.malformed or parsed.torn:
            log.warning(DAMAGED_LINES, extra={"malformed": parsed.malformed, "torn": parsed.torn})
        plan = plan_reapply(parsed.lines)
        voided = reinstated = erased = 0
        with current().transaction():
            for entry_id in plan.voids:
                voided += self._compliance.void_reapplied(entry_id)
            for line in plan.inserts:
                reinstated += self._compliance.reinstate(line) is not None
            for line in plan.erasures:
                erased += self._erase(line)
        log.info(
            LEDGER_REAPPLIED,
            extra={"voided": voided, "reinstated": reinstated, "erased": erased},
        )
        return True

    def _erase(self, line: Erased) -> bool:
        """Re-erase the line's Contact unless it is unknown or tombstoned already."""
        contact = research_queries.contact(line.contact_id)
        if contact is None or contact.erased_at is not None:
            return False
        if line.cause is ErasureCause.DSR:
            try:
                check_request_ref(REAPPLIED, name=contact.name)
            except ValueError:  # the name is the reference's words: erase it without one
                log.warning(DSR_AS_RETENTION, extra={"contact_id": contact.id})
                self._erasures.erase_contact(contact.id, None, ErasureCause.RETENTION)
            else:
                self._erasures.erase_contact(contact.id, REAPPLIED, ErasureCause.DSR)
        else:
            self._erasures.erase_contact(contact.id, None, ErasureCause.RETENTION)
        return True

    @use_case
    def sync(self) -> int:
        """Append every entry, void and erasure the ledger lacks; see the module docstring."""
        with current().transaction():
            lines = self._database_lines()
            pending = compliance_queries.pending_reapply()
        if not self._file.exists():
            if pending is not None:  # a restore waits for its ledger: never fake one
                return 0
            self._file.ensure()
        parsed = self._file.read()
        known = set() if parsed is None else {identity(line) for line in parsed.lines}
        lacking = [line for line in lines if identity(line) not in known]
        self._file.append(lacking)
        log.info(LEDGER_SYNCED, extra={"appended": len(lacking)})
        return len(lacking)

    @staticmethod
    def _database_lines() -> list[LedgerLine]:
        """Every entry, void and erasure the database holds, as ledger lines."""
        lines: list[LedgerLine] = [
            Suppressed(
                entry.id,
                KeyKind(entry.kind),
                entry.value_hash,
                SuppressionSource(entry.source),
                entry.created_at,
                entry.domain_plain,
            )
            for entry in entries_repo.all_for_ledger()
        ]
        lines += [  # a voided entry always has its voided time (the table checks it)
            Voided(entry.id, cast(datetime, entry.voided_at))
            for entry in entries_repo.voided_for_ledger()
        ]
        by_request = requests_repo.completed_erase_contact_ids()
        lines += [
            Erased(
                contact_id,
                ErasureCause.DSR if contact_id in by_request else ErasureCause.RETENTION,
                erased_at,
            )
            for contact_id, erased_at in research_queries.erased_contacts()
        ]
        return lines
