"""The backup copy (AD-2, AD-20, OP-5, OP-6, Story 1.16): copy the live database with SQLite's
backup API, verify the copy and describe it. It writes no table; the ``ops`` service records
the result in ``backups`` (``nextron.db.repos.ops.backups``).

``snapshot(db_path, target) -> Snapshot`` runs, in order:

1. ``mkdir`` of ``target``'s parent (and its parents);
2. creates ``target`` exclusively (``"xb"``), so an existing file is never overwritten: that
   backup fails instead (``FileExistsError``) and the existing file is left alone;
3. ``nextron.db.engine.copy_database(db_path, target)``: SQLite's backup API, from a read-only
   connection to the live file, so the copy is one file whose header keeps WAL mode;
4. in ``nextron.db.engine.open_copy(target)`` (read-only, ``immutable``):

   - ``PRAGMA integrity_check(10)``; any result but exactly ``[("ok",)]`` raises
     ``BackupNotVerified("integrity_check: <first line, at most 200 characters>")``;
   - the copy's Alembic revision (``nextron.db.migrations.stored_revision``); ``None`` (an empty
     copy) raises ``BackupNotVerified("the copy has no Alembic revision")``, and a copy that
     isn't this engine's raises ``DatabaseStateError``;
   - the row count of each ``COUNTED_TABLES`` table that ``sqlite_master`` lists, ``None`` for
     one that doesn't exist yet;

5. once the copy is closed, its size and its SHA-256, streamed in 1 MiB chunks.

Any failure discards ``target`` and then re-raises, so ``snapshot`` only ever returns a verified
copy and a failed one leaves no file (unless the delete fails too; see ``discard``).

``Snapshot(path, size_bytes, sha256, db_revision, suppression_count, provenance_count)`` is
frozen.

``discard(path) -> bool`` deletes the file (``missing_ok``) and returns whether it is gone. A
delete that fails (a locked file, say) logs ``backup_copy_not_deleted`` (WARNING, ``code``: the
error's class name, ``file``: the file name, never the full path) and returns ``False``.

``inspect_copy(path) -> CopyInspection`` (Story 1.18) describes a copy the restore test fetched
and decrypted, reading it only through ``open_copy`` (read-only, ``immutable``): it never
writes, migrates or attaches to it, and opens no engine on it.

- ``integrity`` is ``"ok"``, the first line of ``PRAGMA integrity_check(10)`` (at most 200
  characters), or ``error: <class name>`` when opening or reading the copy raises (a file that
  isn't a database, say). When it isn't ``"ok"``, nothing else is read: the revision and the
  counts are ``None``.
- ``revision`` is ``stored_revision(...)`` of the copy, or ``None`` when that raises
  ``DatabaseStateError`` (several revisions, or tables but none);
- ``head`` is ``head_revision()`` and ``revision_known`` is ``revision in known_revisions()``;
- ``suppression_count`` and ``provenance_count`` are counted as ``snapshot`` counts them
  (``None`` for a table the copy doesn't have).

``CopyInspection(integrity, revision, head, revision_known, suppression_count,
provenance_count)`` is frozen; ``nextron.domain.restore_test.judge`` decides what it means.

Nothing here copies, links or reads the live database file's bytes: only the backup API reads
it (``tests/contracts/test_backup_contracts.py``).
"""

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from nextron.db.engine import copy_database, open_copy
from nextron.db.migrations import (
    DatabaseStateError,
    head_revision,
    known_revisions,
    stored_revision,
)
from nextron.domain.backup import COUNTED_TABLES, BackupNotVerified

CHUNK_BYTES: Final = 1024 * 1024
INTEGRITY_ROWS: Final = 10
MAX_REASON_LENGTH: Final = 200
INTEGRITY_OK: Final = [("ok",)]
INTEGRITY_PASSED: Final = "ok"  # CopyInspection.integrity of a copy whose check answered ok

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A verified copy: where it is, its size, its SHA-256, its Alembic revision and the row
    counts of the Suppression List and the Provenance Records (``None`` when absent)."""

    path: Path
    size_bytes: int
    sha256: str
    db_revision: str
    suppression_count: int | None
    provenance_count: int | None


@dataclass(frozen=True, slots=True)
class CopyInspection:
    """What ``inspect_copy`` read from a restored copy: its integrity answer, its Alembic
    revision (``None`` when it can't be read), the code's head, whether the copy's revision is
    one this code knows, and the row counts of the Suppression List and the Provenance Records
    (``None`` when absent or not read)."""

    integrity: str
    revision: str | None
    head: str
    revision_known: bool
    suppression_count: int | None
    provenance_count: int | None


def discard(path: Path) -> bool:
    """Delete ``path`` if it is there; whether it is gone."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning(
            "backup_copy_not_deleted", extra={"code": type(exc).__name__, "file": path.name}
        )
        return False
    return True


def _first_line(rows: list[tuple[object, ...]]) -> str:
    if not rows or not rows[0]:
        return "no result"
    text = str(rows[0][0])
    lines = text.splitlines()
    return (lines[0] if lines else text)[:MAX_REASON_LENGTH]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as copy:
        while chunk := copy.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _counts(copy: sqlite3.Connection) -> dict[str, int | None]:
    """The row count of each ``COUNTED_TABLES`` table the copy has, ``None`` for one it
    doesn't."""
    present = {
        name for (name,) in copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    counts: dict[str, int | None] = {}
    for table in COUNTED_TABLES:
        if table in present:
            [count] = copy.execute(f'SELECT count(*) FROM "{table}"').fetchone()
            counts[table] = int(count)
        else:
            counts[table] = None
    return counts


def snapshot(db_path: Path, target: Path) -> Snapshot:
    """Copy the database ``db_path`` to the new file ``target``, verify it and describe it;
    see the module docstring."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb"):  # never overwrite: an existing name fails this backup
        pass
    try:
        copy_database(db_path, target)
        with open_copy(target) as copy:
            rows = copy.execute(f"PRAGMA integrity_check({INTEGRITY_ROWS})").fetchall()
            if rows != INTEGRITY_OK:
                raise BackupNotVerified(f"integrity_check: {_first_line(rows)}")
            revision = stored_revision(lambda sql: copy.execute(sql).fetchall())
            if revision is None:
                raise BackupNotVerified("the copy has no Alembic revision")
            counts = _counts(copy)
        size = target.stat().st_size
        digest = _sha256(target)
    except BaseException:
        discard(target)
        raise
    suppression, provenance = (counts[table] for table in COUNTED_TABLES)
    return Snapshot(target, size, digest, revision, suppression, provenance)


def _revision(copy: sqlite3.Connection) -> str | None:
    try:
        return stored_revision(lambda sql: copy.execute(sql).fetchall())
    except DatabaseStateError:
        return None


def inspect_copy(path: Path) -> CopyInspection:
    """Describe the restored copy at ``path``, read-only; see the module docstring."""
    head = head_revision()
    try:
        with open_copy(path) as copy:
            rows = copy.execute(f"PRAGMA integrity_check({INTEGRITY_ROWS})").fetchall()
            if rows != INTEGRITY_OK:
                return CopyInspection(_first_line(rows), None, head, False, None, None)
            revision = _revision(copy)
            counts = _counts(copy)
    except Exception as exc:  # noqa: BLE001 - a copy that can't be read fails its integrity
        return CopyInspection(f"error: {type(exc).__name__}", None, head, False, None, None)
    known = revision is not None and revision in known_revisions()
    suppression, provenance = (counts[table] for table in COUNTED_TABLES)
    return CopyInspection(INTEGRITY_PASSED, revision, head, known, suppression, provenance)
