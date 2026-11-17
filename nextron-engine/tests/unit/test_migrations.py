"""``nextron.db.migrations``: the startup migration against the story's I/O matrix.

The real chain has one revision, so "behind" and "a migration fails" use temporary chains
(``tests/migration_chains.py``). Refusals are checked byte for byte: every engine is disposed
(which checkpoints the WAL into the file) before a SHA-256 is taken.
"""

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

HEAD = "0004_1_11_heartbeat"


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
