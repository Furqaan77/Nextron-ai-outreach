"""``nextron.db.migrations``: the startup migration against the story's I/O matrix.

The real chain has one revision, so "behind" and "a migration fails" use temporary chains
(``tests/migration_chains.py``). Refusals are checked byte for byte: every engine is disposed
(which checkpoints the WAL into the file) before a SHA-256 is taken.
"""

import shutil
import sqlite3
import warnings
from pathlib import Path

import pytest
from alembic import command

from nextron.db.engine import ConnectionSettingError
from nextron.db.migrations import (
    SCRIPT_LOCATION,
    DatabaseAheadError,
    DatabaseStateError,
    ForeignKeyCheckError,
    alembic_config,
    current_revision,
    ensure_current,
    head_revision,
    known_revisions,
)
from tests.migration_chains import (
    chain,
    create_table,
    engine_on,
    extend,
    run_sql,
    sha256,
    stored_revisions,
    tables,
)

HEAD = "0024_3_8_unsub_tokens"


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    """A database file at head, with every connection to it closed."""
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        ensure_current(engine)
    return path


# Row: fresh data dir
def test_a_new_file_is_created_at_head(tmp_path: Path) -> None:
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert current_revision(engine) is None

        assert ensure_current(engine) == (None, HEAD)

        assert current_revision(engine) == HEAD
    assert {"alembic_version", "events", "settings"} <= tables(path)


def test_a_database_at_head_is_left_alone(db_file: Path) -> None:
    before = sha256(db_file)

    with engine_on(db_file) as engine:
        assert ensure_current(engine) == (HEAD, HEAD)

    assert sha256(db_file) == before


def test_head_revision_is_this_storys_migration() -> None:
    assert head_revision() == HEAD


def test_known_revisions_are_every_revision_in_the_chain() -> None:
    files = {path.stem for path in (SCRIPT_LOCATION / "versions").glob("*.py")}

    assert known_revisions() == files
    assert HEAD in known_revisions()


def test_known_revisions_of_a_temporary_chain(tmp_path: Path) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")), ("0002_b", create_table("b")))

    assert known_revisions(location) == frozenset({"0001_a", "0002_b"})
    assert head_revision(location) == "0002_b"


def test_the_startup_migration_raises_no_alembic_warnings(tmp_path: Path) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with engine_on(tmp_path / "nextron.db") as engine:
            ensure_current(engine)

    assert [str(warning.message) for warning in caught] == []


# Row: DB behind
def test_a_database_behind_is_upgraded_in_one_go(tmp_path: Path) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (None, "0001_a")
    extend(location, ("0002_b", create_table("b")), ("0003_c", create_table("c")))

    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == ("0001_a", "0003_c")
        assert current_revision(engine) == "0003_c"

    assert {"a", "b", "c"} <= tables(path)
    assert stored_revisions(path) == ["0003_c"]


# Story 1.17: 0009 on a 0008 database that already holds backups
BACKUP_COLUMNS = (
    "id, kind, status, started_at, finished_at, path, size_bytes, sha256, db_revision, "
    "suppression_count, provenance_count, error, local_deleted_at"
)
OFFPC_COLUMNS = (
    "offpc_status, offpc_name, offpc_ledger, offpc_at, offpc_deleted_at, offpc_error"
)
VERIFIED_DAILY = "01JD3QZ8X5V9K2M7N4P6R8T0A1"
FAILED_MANUAL = "01JD3QZ8X5V9K2M7N4P6R8T0A2"
PRE_MIGRATE = "01JD3QZ8X5V9K2M7N4P6R8T0A3"


def backup_rows(path: Path, columns: str) -> list[tuple[object, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(f"SELECT {columns} FROM backups ORDER BY id").fetchall()
    finally:
        reader.close()


def test_0009_keeps_every_0008_backup_row_with_no_off_pc_outcome(tmp_path: Path) -> None:
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0008_2_1_segments":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (None, "0008_2_1_segments")
    digest, at = "a" * 64, "2026-11-17T09:00:00.000000Z"
    run_sql(
        path,
        f"INSERT INTO backups ({BACKUP_COLUMNS}) VALUES ('{VERIFIED_DAILY}', 'daily', "
        f"'verified', '{at}', '{at}', 'C:/b/local/d.db', 4096, '{digest}', '0007_1_16_backups', "
        f"3, 5, NULL, '{at}')",
        f"INSERT INTO backups ({BACKUP_COLUMNS}) VALUES ('{FAILED_MANUAL}', 'manual', 'failed', "
        f"'{at}', '{at}', 'C:/b/local/m.db', NULL, NULL, NULL, NULL, NULL, "
        "'OSError: disk full', NULL)",
        f"INSERT INTO backups ({BACKUP_COLUMNS}) VALUES ('{PRE_MIGRATE}', 'pre_migrate', "
        f"'verified', '{at}', '{at}', 'C:/b/local/p.db', 8192, '{digest}', '0006_1_13_metering', "
        "NULL, NULL, NULL, NULL)",
    )
    before = backup_rows(path, BACKUP_COLUMNS)
    assert len(before) == 3

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0008_2_1_segments", HEAD)

    assert backup_rows(path, BACKUP_COLUMNS) == before  # every row unchanged
    assert backup_rows(path, OFFPC_COLUMNS) == [(None,) * 6] * 3
    copied = (
        "UPDATE backups SET offpc_status = 'copied', offpc_name = 'x.db.enc', "
        f"offpc_ledger = 'absent', offpc_at = '{at}' WHERE id = '{{}}'"
    )
    run_sql(path, copied.format(VERIFIED_DAILY))  # a valid outcome on a verified row
    with pytest.raises(sqlite3.IntegrityError, match=r"ck_backups_offpc$"):
        run_sql(path, copied.format(FAILED_MANUAL))  # never on a failed backup
    assert [row[0] for row in backup_rows(path, OFFPC_COLUMNS)] == ["copied", None, None]


# Row: DB ahead
def test_a_database_ahead_is_refused_untouched(db_file: Path) -> None:
    run_sql(db_file, "UPDATE alembic_version SET version_num = '0099_future'")
    before = sha256(db_file)

    with engine_on(db_file) as engine:
        with pytest.raises(DatabaseAheadError) as raised:
            ensure_current(engine)

    assert str(raised.value) == "database revision 0099_future is newer than this code"
    assert isinstance(raised.value, DatabaseStateError)
    assert sha256(db_file) == before


# Row: foreign DB
FOREIGN_TABLES = [
    ["CREATE TABLE contacts (id INTEGER PRIMARY KEY, email TEXT)"],
    ["CREATE VIEW v AS SELECT 1"],
    [
        "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)",
        "CREATE TABLE contacts (id INTEGER PRIMARY KEY)",
    ],
]


@pytest.mark.parametrize("statements", FOREIGN_TABLES)
def test_a_wal_database_with_tables_but_no_revision_is_refused_untouched(
    statements: list[str], tmp_path: Path
) -> None:
    path = tmp_path / "nextron.db"
    run_sql(path, "PRAGMA journal_mode=WAL", *statements)
    before = sha256(path)

    with engine_on(path) as engine:
        with pytest.raises(DatabaseStateError, match="tables but no Alembic revision") as raised:
            ensure_current(engine)

    assert not isinstance(raised.value, DatabaseAheadError)
    assert sha256(path) == before


@pytest.mark.parametrize("statements", [*FOREIGN_TABLES, ["PRAGMA user_version = 7"]])
def test_a_rollback_journal_database_is_refused_untouched(
    statements: list[str], tmp_path: Path
) -> None:
    path = tmp_path / "nextron.db"
    run_sql(path, *statements)  # SQLite's default rollback journal, as another tool leaves it
    before = sha256(path)

    with engine_on(path) as engine:
        with pytest.raises(ConnectionSettingError, match="not this engine's database"):
            ensure_current(engine)

    assert sha256(path) == before


def test_a_database_with_several_revisions_is_refused_untouched(db_file: Path) -> None:
    run_sql(db_file, "INSERT INTO alembic_version (version_num) VALUES ('0099_fork')")
    before = sha256(db_file)

    with engine_on(db_file) as engine:
        with pytest.raises(DatabaseStateError, match=rf"2 Alembic revisions \({HEAD}, 0099_fork\)"):
            ensure_current(engine)
        with pytest.raises(DatabaseStateError):
            current_revision(engine)

    assert sha256(db_file) == before


def test_an_empty_version_table_with_no_other_table_counts_as_new(tmp_path: Path) -> None:
    path = tmp_path / "nextron.db"
    run_sql(
        path,
        "PRAGMA journal_mode=WAL",
        "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == (None, HEAD)


def test_an_empty_file_is_switched_to_wal_and_migrated(tmp_path: Path) -> None:
    path = tmp_path / "nextron.db"
    path.touch()

    with engine_on(path) as engine:
        assert ensure_current(engine) == (None, HEAD)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


# Row: migration fails
@pytest.fixture
def at_0001(tmp_path: Path) -> tuple[Path, Path]:
    """A temporary chain at ``0001_parent`` (tables ``parent`` and ``child``) and a DB at it."""
    location = chain(
        tmp_path,
        (
            "0001_parent",
            """
            op.create_table('parent', sa.Column('id', sa.Integer, primary_key=True))
            op.create_table(
                'child',
                sa.Column('id', sa.Integer, primary_key=True),
                sa.Column('parent_id', sa.Integer, sa.ForeignKey('parent.id'), nullable=False),
            )
            """,
        ),
    )
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        ensure_current(engine, script_location=location)
    return location, path


def test_a_failing_migration_rolls_the_whole_upgrade_back(at_0001: tuple[Path, Path]) -> None:
    location, path = at_0001
    extend(
        location,
        ("0002_ok", create_table("kept_if_committed")),
        ("0003_fails", create_table("half_done") + "\nraise RuntimeError('the migration broke')"),
    )

    with engine_on(path) as engine:
        with pytest.raises(RuntimeError, match="the migration broke"):
            ensure_current(engine, script_location=location)
        assert current_revision(engine) == "0001_parent"

    assert {"kept_if_committed", "half_done"} & tables(path) == set()


def test_a_foreign_key_violation_rolls_the_upgrade_back(at_0001: tuple[Path, Path]) -> None:
    location, path = at_0001
    orphan = "op.execute('INSERT INTO child (id, parent_id) VALUES (1, 42)')"
    extend(location, ("0002_orphan", orphan))

    with engine_on(path) as engine:
        with pytest.raises(ForeignKeyCheckError, match="1 foreign key violations in child"):
            ensure_current(engine, script_location=location)
        assert current_revision(engine) == "0001_parent"
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM child").scalar() == 0


def test_the_upgrade_runs_with_foreign_keys_off_then_turns_them_back_on(
    at_0001: tuple[Path, Path],
) -> None:
    location, path = at_0001
    extend(
        location,
        (
            "0002_record_fk",
            """
            op.create_table('fk_state', sa.Column('value', sa.Integer))
            op.execute('INSERT INTO fk_state SELECT foreign_keys FROM pragma_foreign_keys')
            """,
        ),
    )

    with engine_on(path) as engine:
        ensure_current(engine, script_location=location)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT value FROM fk_state").scalar() == 0
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


# env.py -----------------------------------------------------------------------------------------


def test_env_py_refuses_to_run_without_the_engines_connection() -> None:
    with pytest.raises(RuntimeError, match="only through nextron.db.migrations"):
        command.upgrade(alembic_config(), "head")


def test_env_py_refuses_offline_mode() -> None:
    with pytest.raises(RuntimeError, match="only through nextron.db.migrations"):
        command.upgrade(alembic_config(), "head", sql=True)


def test_the_config_is_built_in_code() -> None:
    config = alembic_config()

    assert config.config_file_name is None
    assert Path(config.get_main_option("script_location") or "") == SCRIPT_LOCATION
    assert config.get_main_option("path_separator") == "os"


# The before_upgrade hook (Story 1.16) -----------------------------------------------------------


def test_the_hook_runs_before_the_upgrade_of_a_database_behind(tmp_path: Path) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_b", create_table("b")))
    seen: list[tuple[str, set[str]]] = []

    def hook(before: str) -> None:
        # another connection can't write: the migration's write transaction is already held
        writer = sqlite3.connect(path, isolation_level=None, timeout=0.05)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("BEGIN IMMEDIATE")
        finally:
            writer.close()
        seen.append((before, tables(path)))

    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location, before_upgrade=hook) == (
            "0001_a",
            "0002_b",
        )

    [(before, found)] = seen
    assert before == "0001_a"
    assert "b" not in found  # called before the upgrade
    assert "b" in tables(path)


def test_a_hook_that_raises_migrates_nothing(tmp_path: Path) -> None:
    location = chain(tmp_path, ("0001_a", create_table("a")))
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        ensure_current(engine, script_location=location)
    extend(location, ("0002_b", create_table("b")))

    def hook(before: str) -> None:
        raise RuntimeError("the backup failed")

    with engine_on(path) as engine:
        with pytest.raises(RuntimeError, match="the backup failed"):
            ensure_current(engine, script_location=location, before_upgrade=hook)
        assert current_revision(engine) == "0001_a"

    assert "b" not in tables(path)


def test_the_hook_never_runs_for_a_new_file_or_a_database_at_head(tmp_path: Path) -> None:
    calls: list[str] = []
    path = tmp_path / "nextron.db"

    with engine_on(path) as engine:
        assert ensure_current(engine, before_upgrade=calls.append) == (None, HEAD)
        assert ensure_current(engine, before_upgrade=calls.append) == (HEAD, HEAD)

    assert calls == []


def test_the_hook_never_runs_for_a_refused_database(db_file: Path) -> None:
    run_sql(db_file, "UPDATE alembic_version SET version_num = '0099_future'")
    calls: list[str] = []

    with engine_on(db_file) as engine:
        with pytest.raises(DatabaseAheadError):
            ensure_current(engine, before_upgrade=calls.append)

    assert calls == []
