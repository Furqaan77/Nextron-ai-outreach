"""The restore's file work (``nextron.db.repos.ops.restore``, Story 1.19) on temporary folders:
``copy_candidate`` and ``save_current`` leave no target when the copy fails; ``swap_in`` onto a
missing database discards the empty file it created when the copy fails, and onto an existing
one leaves its bytes as they were; the good paths copy with the backup API and leave nothing
beside the source."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from nextron.db.engine import create_engine
from nextron.db.migrations import ensure_current
from nextron.db.repos.ops.restore import copy_candidate, save_current, swap_in

NOT_A_DATABASE = b"this is not an SQLite database at all\n" * 200


def database(path: Path, marker: str) -> Path:
    """A database file at head holding one marker row; closed cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(path)
    try:
        ensure_current(engine)
    finally:
        engine.dispose()
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
    return path


def marker(path: Path) -> str:
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        [(value,)] = connection.execute("SELECT value FROM marker").fetchall()
    return str(value)


def not_a_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(NOT_A_DATABASE)
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def test_copy_candidate_copies_and_leaves_nothing_beside_the_source(tmp_path: Path) -> None:
    source = database(tmp_path / "copies" / "nextron-test-x.db", "copy")
    before, beside = digest(source), names(source.parent)
    target = tmp_path / "work" / "candidate.db"
    target.parent.mkdir()

    copy_candidate(source, target)

    assert marker(target) == "copy"
    assert (digest(source), names(source.parent)) == (before, beside)


def test_copy_candidate_of_a_non_database_leaves_no_target(tmp_path: Path) -> None:
    source = not_a_database(tmp_path / "copies" / "nextron-test-x.db")
    target = tmp_path / "work" / "candidate.db"
    target.parent.mkdir()

    with pytest.raises(sqlite3.DatabaseError):
        copy_candidate(source, target)

    assert names(target.parent) == []
    assert source.read_bytes() == NOT_A_DATABASE


def test_copy_candidate_never_overwrites_a_target(tmp_path: Path) -> None:
    source = database(tmp_path / "copies" / "nextron-test-x.db", "copy")
    target = tmp_path / "candidate.db"
    target.write_bytes(b"already here")

    with pytest.raises(FileExistsError):
        copy_candidate(source, target)

    assert target.read_bytes() == b"already here"


def test_save_current_makes_its_folder_and_copies(tmp_path: Path) -> None:
    live = database(tmp_path / "data" / "nextron.db", "live")
    target = tmp_path / "backups" / "pre_restore" / "nextron-test-now.db"

    save_current(live, target)

    assert marker(target) == "live"


def test_save_current_of_a_non_database_leaves_no_target(tmp_path: Path) -> None:
    live = not_a_database(tmp_path / "data" / "nextron.db")
    target = tmp_path / "backups" / "pre_restore" / "nextron-test-now.db"

    with pytest.raises(sqlite3.DatabaseError):
        save_current(live, target)

    assert names(target.parent) == []
    assert live.read_bytes() == NOT_A_DATABASE


def test_swap_in_creates_a_missing_database(tmp_path: Path) -> None:
    candidate = database(tmp_path / "work" / "candidate.db", "copy")
    db_path = tmp_path / "data" / "nextron.db"
    db_path.parent.mkdir()

    swap_in(candidate, db_path)

    assert marker(db_path) == "copy"


def test_a_failed_swap_onto_a_missing_database_leaves_no_database(tmp_path: Path) -> None:
    candidate = not_a_database(tmp_path / "work" / "candidate.db")
    db_path = tmp_path / "data" / "nextron.db"
    db_path.parent.mkdir()

    with pytest.raises(sqlite3.DatabaseError):
        swap_in(candidate, db_path)

    assert not db_path.exists()  # the empty file swap_in made is gone again


def test_a_failed_swap_onto_a_live_database_leaves_its_bytes(tmp_path: Path) -> None:
    candidate = not_a_database(tmp_path / "work" / "candidate.db")
    db_path = database(tmp_path / "data" / "nextron.db", "live")
    before = db_path.read_bytes()

    with pytest.raises(sqlite3.DatabaseError):
        swap_in(candidate, db_path)

    assert db_path.read_bytes() == before
    assert marker(db_path) == "live"
