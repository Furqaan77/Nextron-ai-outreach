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

Nothing here copies, links or reads the live database file's bytes: only the backup API reads
it (``tests/contracts/test_backup_contracts.py``).
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from nextron.db.engine import copy_database, open_copy
from nextron.db.migrations import stored_revision
from nextron.domain.backup import COUNTED_TABLES, BackupNotVerified

CHUNK_BYTES: Final = 1024 * 1024
INTEGRITY_ROWS: Final = 10
MAX_REASON_LENGTH: Final = 200
INTEGRITY_OK: Final = [("ok",)]

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
            present = {
                name
                for (name,) in copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            counts: dict[str, int | None] = {}
            for table in COUNTED_TABLES:
                if table in present:
                    [count] = copy.execute(f'SELECT count(*) FROM "{table}"').fetchone()
                    counts[table] = int(count)
                else:
                    counts[table] = None
        size = target.stat().st_size
        digest = _sha256(target)
    except BaseException:
        discard(target)
        raise
    suppression, provenance = (counts[table] for table in COUNTED_TABLES)
    return Snapshot(target, size, digest, revision, suppression, provenance)
