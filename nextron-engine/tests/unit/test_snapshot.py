"""``inspect_copy`` (``nextron.db.repos.ops.snapshot``, Story 1.18): what it reads from a restored
copy (a good copy, counted tables present or absent, a file that isn't a database, a copy at an
older or unknown revision, several revisions, a corrupted index), that it reads only through
``open_copy`` and leaves the file's bytes (its SHA-256) unchanged, with no ``-wal`` or ``-shm``
beside it."""

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import Engine

from nextron.db.migrations import head_revision, known_revisions
from nextron.db.repos.ops import snapshot as snapshot_module
from nextron.db.repos.ops.snapshot import CopyInspection, inspect_copy, snapshot

HEAD = head_revision()
OLD_REVISION = max(known_revisions() - {HEAD})  # the revision before head (ids zero-padded)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_file(engine: Engine) -> Path:
    assert engine.url.database
    return Path(engine.url.database)


def copy_of(engine: Engine, tmp_path: Path, *statements: str) -> Path:
    """A real copy of the test database at head, changed with ``statements`` afterwards."""
    target = tmp_path / "restored" / "copy.db"
    snapshot(live_file(engine), target)
    if statements:
        connection = sqlite3.connect(target, isolation_level=None)
        try:
            for statement in statements:
                connection.execute(statement)
        finally:
            connection.close()
    return target


def suppression_row(n: int) -> str:
    """An INSERT of the ``n``-th valid ``active`` Suppression List entry (Story 3.3's table)."""
    return (
        "INSERT INTO suppression_entries (id, kind, value_hash, status, source, created_at) "
        f"VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}', 'address', '{n:064x}', 'active', "
        "'operator', '2026-11-18T09:00:00.000000Z')"
    )


@contextmanager
def unchanged(path: Path) -> Iterator[None]:
    """The file's bytes are the same afterwards, and nothing was created beside it."""
    before = digest(path)
    beside = sorted(found.name for found in path.parent.iterdir())
    yield
    assert digest(path) == before
    assert sorted(found.name for found in path.parent.iterdir()) == beside


def test_a_good_copy_at_head_counts_the_counted_tables_it_has(
    db_engine: Engine, tmp_path: Path
) -> None:
    copy = copy_of(db_engine, tmp_path)

    with unchanged(copy):
        found = inspect_copy(copy)

    # both counted tables exist at head (Stories 2.3 and 3.3), empty here
    assert found == CopyInspection("ok", HEAD, HEAD, True, 0, 0)


def test_a_good_copy_counts_the_suppression_and_provenance_rows(
    db_engine: Engine, tmp_path: Path
) -> None:
    copy = copy_of(
        db_engine,
        tmp_path,
        suppression_row(1),
        suppression_row(2),
        suppression_row(3),
        # both counted tables exist at head (Stories 2.3 and 3.3); provenance_records is empty
    )

    with unchanged(copy):
        found = inspect_copy(copy)

    assert found == CopyInspection("ok", HEAD, HEAD, True, 3, 0)


@pytest.mark.parametrize(
    ("statements", "counts"),
    [
        ((suppression_row(1), "DROP TABLE provenance_records"), (1, None)),
        (("DROP TABLE suppression_entries",), (None, 0)),
    ],
    ids=["provenance_records", "suppression_entries"],
)
def test_a_copy_with_one_counted_table_absent(
    db_engine: Engine, tmp_path: Path, statements: tuple[str, ...], counts: tuple[int | None, ...]
) -> None:
    # both tables exist at head (Stories 2.3 and 3.3): the copy lost one
    copy = copy_of(db_engine, tmp_path, *statements)

    with unchanged(copy):
        found = inspect_copy(copy)

    assert (found.suppression_count, found.provenance_count) == counts


def test_a_file_that_is_not_a_database_is_an_integrity_error(tmp_path: Path) -> None:
    copy = tmp_path / "copy.db"
    copy.write_bytes(b"this is not a database at all\n" * 200)

    with unchanged(copy):
        found = inspect_copy(copy)

    assert found == CopyInspection("error: DatabaseError", None, HEAD, False, None, None)


def test_a_missing_file_is_an_integrity_error(tmp_path: Path) -> None:
    found = inspect_copy(tmp_path / "gone.db")

    assert found.integrity == "error: OperationalError"
    assert (found.revision, found.revision_known) == (None, False)
    assert not (tmp_path / "gone.db").exists()  # read-only: never created


@pytest.mark.parametrize(
    ("revision", "known"),
    [(OLD_REVISION, True), ("0099_from_newer_code", False)],
    ids=["older", "unknown"],
)
def test_a_copy_at_another_revision_reports_it_and_whether_it_is_known(
    db_engine: Engine, tmp_path: Path, revision: str, known: bool
) -> None:
    copy = copy_of(db_engine, tmp_path, f"UPDATE alembic_version SET version_num = '{revision}'")

    with unchanged(copy):
        found = inspect_copy(copy)

    # both counted tables exist at head (Stories 2.3 and 3.3), empty here
    assert found == CopyInspection("ok", revision, HEAD, known, 0, 0)


@pytest.mark.parametrize(
    "statement",
    ["DELETE FROM alembic_version", f"INSERT INTO alembic_version VALUES ('{OLD_REVISION}')"],
    ids=["no revision", "two revisions"],
)
def test_a_copy_whose_revision_cannot_be_read_has_none(
    db_engine: Engine, tmp_path: Path, statement: str
) -> None:
    copy = copy_of(db_engine, tmp_path, statement)

    with unchanged(copy):
        found = inspect_copy(copy)

    # both counted tables exist at head (Stories 2.3 and 3.3), empty here
    assert found == CopyInspection("ok", None, HEAD, False, 0, 0)


def test_a_corrupted_copy_reports_the_first_integrity_line_and_reads_nothing_else(
    db_engine: Engine, tmp_path: Path
) -> None:
    copy = copy_of(
        db_engine,
        tmp_path,
        "CREATE TABLE probe_rows (value TEXT)",
        "CREATE INDEX ix_probe_value ON probe_rows (value)",
        "INSERT INTO probe_rows VALUES ('QZXMARKERVALUE')",
    )
    data = copy.read_bytes()
    at = data.rindex(b"QZXMARKERVALUE")
    copy.write_bytes(data[:at] + b"QZXMARKERVALUF" + data[at + 14 :])

    with unchanged(copy):
        found = inspect_copy(copy)

    assert found.integrity in (
        "row 1 missing from index ix_probe_value",
        "wrong # of entries in index ix_probe_value",
    )
    assert found == CopyInspection(found.integrity, None, HEAD, False, None, None)


def test_inspect_copy_reads_only_through_open_copy(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = copy_of(db_engine, tmp_path)
    opened: list[Path] = []
    real_open_copy = snapshot_module.open_copy

    @contextmanager
    def spy(path: Path) -> Iterator[sqlite3.Connection]:
        opened.append(path)
        with real_open_copy(path) as connection:
            yield connection

    monkeypatch.setattr(snapshot_module, "open_copy", spy)

    assert inspect_copy(copy).integrity == "ok"
    assert opened == [copy]


def test_inspect_copy_never_touches_the_live_database(db_engine: Engine, tmp_path: Path) -> None:
    live = live_file(db_engine)
    copy = copy_of(db_engine, tmp_path)
    before = digest(live)

    inspect_copy(copy)

    assert digest(live) == before
