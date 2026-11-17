"""The restore's file work (Story 1.19; AD-2, AD-20, OP-8): copy a backup file into a private
candidate, read the candidate's newest event, save the current database, swap the candidate in
and check the result. It writes no table: ``nextron.main.restore`` runs it, with the engine
stopped and its lock held, and the ``ops`` service's ``Recovery`` writes ``recovery_state``.

Every function reads or writes a database only through ``nextron.db.engine``: SQLite's backup
API (``copy_database``), ``open_copy`` and ``create_read_only_engine``. Nothing copies, renames
or links a database file (``tests/contracts/test_backup_contracts.py``).

- ``copy_candidate(source, target)``: creates ``target`` exclusively (``"xb"``), then
  ``copy_database(source, target, immutable_source=True)``, so nothing is created or changed
  beside ``source``. Any failure discards ``target`` and re-raises.
- ``newest_event_at(path) -> datetime | None``: ``max(events.at)`` of the copy at ``path``,
  read through ``open_copy``; ``None`` when it has no ``events`` table or no event.
- ``save_current(db_path, target)``: ``mkdir`` of ``target``'s parent (and its parents),
  creates ``target`` exclusively, then ``copy_database(db_path, target)``: the safety copy of
  the database a restore replaces. Any failure discards ``target`` and re-raises.
- ``swap_in(candidate, db_path)``: creates an empty ``db_path`` when it is missing, then
  ``copy_database(candidate, db_path)``. The destination's write is one transaction, so a
  failure leaves the live database as it was; a ``-wal`` a crash left beside it is read first
  and checkpointed away when the copy's connection closes. When ``db_path`` was missing and the
  copy fails, the empty file it created is discarded (best effort) before the error re-raises,
  so a new PC is left with no database rather than an empty one.
- ``restored_check(db_path) -> RestoredCheck(integrity, revision, active_ids)``, read through
  ``create_read_only_engine`` (which reads the WAL, and may leave empty ``-wal`` and ``-shm``
  files): ``integrity`` is the first line of ``PRAGMA integrity_check(10)``, ``revision`` is
  ``stored_revision(...)`` and ``active_ids`` are the ids of the ``on`` rows in
  ``recovery_state`` (empty when the table is missing).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from sqlalchemy import text

from nextron.db.engine import copy_database, create_read_only_engine, open_copy
from nextron.db.migrations import stored_revision
from nextron.db.models.base import parse_utc_text
from nextron.db.repos.ops.snapshot import INTEGRITY_ROWS, discard
from nextron.domain.recovery import RecoveryMode

EVENTS_TABLE: Final = "events"
RECOVERY_TABLE: Final = "recovery_state"
MAX_INTEGRITY_LENGTH: Final = 200


@dataclass(frozen=True, slots=True)
class RestoredCheck:
    """What ``restored_check`` read from the live database after the swap: the first line of
    its integrity check, its Alembic revision and the ids of its ``on`` recovery rows."""

    integrity: str
    revision: str | None
    active_ids: tuple[str, ...]


def copy_candidate(source: Path, target: Path) -> None:
    """Copy the backup file ``source`` into the new file ``target`` with SQLite's backup API,
    opening ``source`` read-only and ``immutable``; a failure leaves no ``target``."""
    with target.open("xb"):  # never overwrite
        pass
    try:
        copy_database(source, target, immutable_source=True)
    except BaseException:
        discard(target)
        raise


def _tables(rows: list[tuple[object, ...]]) -> set[str]:
    return {str(row[0]) for row in rows}


def newest_event_at(path: Path) -> datetime | None:
    """The newest ``events.at`` of the copy at ``path``, read only through ``open_copy``;
    ``None`` with no ``events`` table or no event."""
    with open_copy(path) as copy:
        tables = _tables(
            copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        )
        if EVENTS_TABLE not in tables:
            return None
        [newest] = copy.execute(f'SELECT max("at") FROM "{EVENTS_TABLE}"').fetchone()
    return None if newest is None else parse_utc_text(str(newest))


def save_current(db_path: Path, target: Path) -> None:
    """Copy the live database ``db_path`` into the new file ``target`` (its folder made when
    missing) with SQLite's backup API; a failure leaves no ``target``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb"):  # never overwrite
        pass
    try:
        copy_database(db_path, target)
    except BaseException:
        discard(target)
        raise


def swap_in(candidate: Path, db_path: Path) -> None:
    """Copy the checked ``candidate`` into the live database ``db_path`` (created empty when
    missing, and discarded again when the copy fails) with SQLite's backup API, in one write
    transaction."""
    created = False
    if not db_path.exists():
        with db_path.open("xb"):
            created = True
    try:
        copy_database(candidate, db_path)
    except BaseException:
        if created:
            discard(db_path)
        raise


def _first_line(rows: list[tuple[object, ...]]) -> str:
    if not rows or not rows[0]:
        return "no result"
    lines = str(rows[0][0]).splitlines()
    return (lines[0] if lines else "")[:MAX_INTEGRITY_LENGTH]


def restored_check(db_path: Path) -> RestoredCheck:
    """Read the live database after the swap, read-only: its integrity, its revision and its
    ``on`` recovery rows."""
    engine = create_read_only_engine(db_path)
    try:
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(f"PRAGMA integrity_check({INTEGRITY_ROWS})")
            integrity = _first_line([tuple(row) for row in rows])
            revision = stored_revision(
                lambda sql: [tuple(row) for row in connection.exec_driver_sql(sql)]
            )
            tables = _tables(
                [
                    tuple(row)
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                ]
            )
            active: tuple[str, ...] = ()
            if RECOVERY_TABLE in tables:
                active = tuple(
                    str(row[0])
                    for row in connection.execute(
                        text(f'SELECT id FROM "{RECOVERY_TABLE}" WHERE mode = :mode'),
                        {"mode": str(RecoveryMode.ON)},
                    )
                )
    finally:
        engine.dispose()
    return RestoredCheck(integrity, revision, active)
