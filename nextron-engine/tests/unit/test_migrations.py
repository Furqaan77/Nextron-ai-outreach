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

HEAD = "0037_3_11_unsub_requests"


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


# Story 4.4: 0026 rebuilds ai_route_activations on a 0025 database that already holds rows
ACTIVATION_COLUMNS = (
    "id, route, model, effort, prompt_version, prompt_sha256, schema_version, actor, bootstrap, "
    "test_run_id, activated_at, deactivated_at"
)
ACTIVE_ROW = "01JD3QZ8X5V9K2M7N4P6R8T0B1"
OLD_ROW = "01JD3QZ8X5V9K2M7N4P6R8T0B2"


def activation_rows(path: Path, columns: str) -> list[tuple[object, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(f"SELECT {columns} FROM ai_route_activations ORDER BY id").fetchall()
    finally:
        reader.close()


def test_0026_keeps_every_activation_and_its_partial_index(tmp_path: Path) -> None:
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0025_2_9_signals_evidence":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0025_2_9_signals_evidence",
        )
    digest, at = "a" * 64, "2026-11-17T09:00:00.000000Z"
    run_sql(
        path,
        "INSERT INTO prompt_snapshots (id, route, prompt_version, sha256, text, created_at) "
        f"VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0B0', 'extract_signals', 'v1', '{digest}', 'Text.', "
        f"'{at}')",
        f"INSERT INTO ai_route_activations ({ACTIVATION_COLUMNS}) VALUES ('{ACTIVE_ROW}', "
        f"'extract_signals', 'claude-haiku-5-5', 'medium', 'v1', '{digest}', 1, 'seed', 1, NULL, "
        f"'{at}', NULL)",
        f"INSERT INTO ai_route_activations ({ACTIVATION_COLUMNS}) VALUES ('{OLD_ROW}', "
        f"'extract_signals', 'claude-haiku-5-5', 'low', 'v1', '{digest}', 1, 'operator', 0, "
        f"'01JD3QZ8X5V9K2M7N4P6R8T0B3', '{at}', '{at}')",
    )
    before = activation_rows(path, ACTIVATION_COLUMNS)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        # only 0026 to 0029: 0030's foreign key would refuse OLD_ROW's invented test run
        if "0025_2_9_signals_evidence" < revision.stem <= "0029_2_9_tag_managers":
            shutil.copy2(revision, location / "versions" / revision.name)

    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            "0025_2_9_signals_evidence",
            "0029_2_9_tag_managers",
        )

    assert activation_rows(path, ACTIVATION_COLUMNS) == before  # every row unchanged
    assert activation_rows(path, "playbook_sha256") == [(None,), (None,)]
    reader = sqlite3.connect(path)
    try:
        [(index_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_ai_route_activations_active_route'"
        ).fetchall()
        columns = [row[1] for row in reader.execute("PRAGMA table_info(ai_route_activations)")]
        keys = reader.execute("PRAGMA foreign_key_list(ai_route_activations)").fetchall()
    finally:
        reader.close()
    assert "WHERE deactivated_at IS NULL" in index_sql
    assert columns.index("playbook_sha256") == columns.index("prompt_sha256") + 1
    assert {row[2] for row in keys} == {"prompt_snapshots", "playbook_snapshots"}
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):  # still one active per route
        run_sql(
            path,
            f"INSERT INTO ai_route_activations ({ACTIVATION_COLUMNS}) VALUES "
            f"('01JD3QZ8X5V9K2M7N4P6R8T0B4', 'extract_signals', 'claude-haiku-5-5', 'high', 'v1', "
            f"'{digest}', 1, 'seed', 1, NULL, '{at}', NULL)",
        )
    with pytest.raises(sqlite3.IntegrityError, match=r"ck_ai_route_activations_tested$"):
        run_sql(path, f"UPDATE ai_route_activations SET bootstrap = 0 WHERE id = '{ACTIVE_ROW}'")
    with pytest.raises(sqlite3.IntegrityError, match=r"ck_ai_route_activations_effort$"):
        run_sql(path, f"UPDATE ai_route_activations SET effort = 'huge' WHERE id = '{ACTIVE_ROW}'")
    with pytest.raises(sqlite3.IntegrityError, match=r"ck_ai_route_activations_schema_version$"):
        run_sql(
            path, f"UPDATE ai_route_activations SET schema_version = 0 WHERE id = '{ACTIVE_ROW}'"
        )
    bad_playbook = (
        f"UPDATE ai_route_activations SET playbook_sha256 = 'X' WHERE id = '{ACTIVE_ROW}'"
    )
    with pytest.raises(sqlite3.IntegrityError, match=r"ck_ai_route_activations_playbook_sha256$"):
        run_sql(path, bad_playbook)


def test_0029_leaves_every_existing_page_unchecked(tmp_path: Path) -> None:
    """A page stored before 0029 was never checked for tag managers: its value is NULL."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0028_2_10_primary_contact":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0028_2_10_primary_contact",
        )
    at = "2026-11-17T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0C0", "01JD3QZ8X5V9K2M7N4P6R8T0C1"
    run, page = "01JD3QZ8X5V9K2M7N4P6R8T0C2", "01JD3QZ8X5V9K2M7N4P6R8T0C3"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'law_private_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, created_at) "
        f"VALUES ('{lead}', 'Smith Law LLP', 'smith-law.com', '{segment}', '{at}')",
        "INSERT INTO crawl_runs (id, lead_id, site_domain, started_at, ended_at, outcome, "
        "pages_fetched, pages_skipped_robots, renders, errors) "
        f"VALUES ('{run}', '{lead}', 'smith-law.com', '{at}', '{at}', 'ok', 1, 0, 0, 0)",
        "INSERT INTO crawled_pages (id, lead_id, run_id, url, final_url, kind, fetched_at, "
        "http_status, content_hash, text, text_purge_after, changed_at) "
        f"VALUES ('{page}', '{lead}', '{run}', 'https://smith-law.com/', "
        f"'https://smith-law.com/', 'home', '{at}', 200, '{'a' * 64}', 'Smith Law LLP', '{at}', "
        f"'{at}')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0028_2_10_primary_contact", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute("SELECT id, text, tag_managers FROM crawled_pages").fetchall()
        columns = [row[1] for row in reader.execute("PRAGMA table_info(crawled_pages)")]
    finally:
        reader.close()
    assert rows == [(page, "Smith Law LLP", None)]  # kept, and never checked
    assert columns[-1] == "tag_managers"


# Story 4.5: 0030 adds the test cases and runs, and rebuilds ai_route_activations again with the
# foreign key from test_run_id to ai_test_runs.id
TESTED_ROW = "01JD3QZ8X5V9K2M7N4P6R8T0C1"
TUPLE_ROW = "01JD3QZ8X5V9K2M7N4P6R8T0C2"
RUN_ROW = "01JD3QZ8X5V9K2M7N4P6R8T0C3"
UNKNOWN_RUN = "01JD3QZ8X5V9K2M7N4P6R8T0C9"


def test_0030_keeps_every_activation_and_adds_the_test_run_key(tmp_path: Path) -> None:
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0029_2_9_tag_managers":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0029_2_9_tag_managers",
        )
    digest, at = "a" * 64, "2026-11-18T09:00:00.000000Z"
    columns = f"{ACTIVATION_COLUMNS}, playbook_sha256"
    run_sql(
        path,
        "INSERT INTO prompt_snapshots (id, route, prompt_version, sha256, text, created_at) "
        f"VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0B0', 'extract_signals', 'v1', '{digest}', 'Text.', "
        f"'{at}')",
        f"INSERT INTO ai_route_activations ({columns}) VALUES ('{ACTIVE_ROW}', "
        f"'extract_signals', 'claude-haiku-5-5', 'medium', 'v1', '{digest}', 1, 'seed', 1, NULL, "
        f"'{at}', NULL, NULL)",
    )
    before = activation_rows(path, columns)

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0029_2_9_tag_managers", HEAD)

    assert activation_rows(path, columns) == before  # the bootstrap activation survives
    reader = sqlite3.connect(path)
    try:
        [(index_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_ai_route_activations_active_route'"
        ).fetchall()
        [(cases_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_ai_test_cases_live_key'"
        ).fetchall()
        keys = reader.execute("PRAGMA foreign_key_list(ai_route_activations)").fetchall()
        run_keys = reader.execute("PRAGMA foreign_key_list(ai_test_runs)").fetchall()
        run_indexes = [row[1] for row in reader.execute("PRAGMA index_list(ai_test_runs)")]
    finally:
        reader.close()
    assert "WHERE deactivated_at IS NULL" in index_sql
    assert "WHERE retired_at IS NULL" in cases_sql
    assert {(row[2], row[3], row[4]) for row in keys} == {
        ("prompt_snapshots", "route", "route"),
        ("prompt_snapshots", "prompt_version", "prompt_version"),
        ("prompt_snapshots", "prompt_sha256", "sha256"),
        ("playbook_snapshots", "playbook_sha256", "sha256"),
        ("ai_test_runs", "test_run_id", "id"),
    }
    assert [(row[2], row[3], row[4]) for row in run_keys] == [("ai_route_tuples", "tuple_id", "id")]
    assert "ix_ai_test_runs_tuple_id_test_set" in run_indexes
    for check, statement in (
        ("tested", f"UPDATE ai_route_activations SET bootstrap = 0 WHERE id = '{ACTIVE_ROW}'"),
        ("effort", f"UPDATE ai_route_activations SET effort = 'huge' WHERE id = '{ACTIVE_ROW}'"),
        (
            "schema_version",
            f"UPDATE ai_route_activations SET schema_version = 0 WHERE id = '{ACTIVE_ROW}'",
        ),
        (
            "playbook_sha256",
            f"UPDATE ai_route_activations SET playbook_sha256 = 'X' WHERE id = '{ACTIVE_ROW}'",
        ),
    ):
        with pytest.raises(sqlite3.IntegrityError, match=rf"ck_ai_route_activations_{check}$"):
            run_sql(path, statement)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):  # still one active per route
        run_sql(
            path,
            f"INSERT INTO ai_route_activations ({columns}) VALUES "
            f"('01JD3QZ8X5V9K2M7N4P6R8T0B4', 'extract_signals', 'claude-haiku-5-5', 'high', 'v1', "
            f"'{digest}', 1, 'seed', 1, NULL, '{at}', NULL, NULL)",
        )
    run_sql(  # a tuple and a passing run of it: a tested activation names the run
        path,
        "INSERT INTO ai_route_tuples (id, route, model, effort, prompt_version, prompt_sha256, "
        f"playbook_sha256, schema_version, created_at) VALUES ('{TUPLE_ROW}', 'extract_signals', "
        f"'claude-haiku-5-5', 'medium', 'v1', '{digest}', NULL, 1, '{at}')",
        "INSERT INTO ai_test_runs (id, tuple_id, test_set, outcome, error, case_count, "
        "passed_count, metrics, results, cost_micros, started_at, finished_at) VALUES "
        f"('{RUN_ROW}', '{TUPLE_ROW}', 'extraction', 'passed', NULL, 1, 1, '[]', '[]', 0, '{at}', "
        f"'{at}')",
        f"UPDATE ai_route_activations SET deactivated_at = '{at}' WHERE id = '{ACTIVE_ROW}'",
        f"INSERT INTO ai_route_activations ({columns}) VALUES ('{TESTED_ROW}', "
        f"'extract_signals', 'claude-haiku-5-5', 'medium', 'v1', '{digest}', 1, "
        f"'system:playbook', 0, '{RUN_ROW}', '{at}', NULL, NULL)",
    )
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            writer.execute(
                f"UPDATE ai_route_activations SET test_run_id = '{UNKNOWN_RUN}' "
                f"WHERE id = '{TESTED_ROW}'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            writer.execute(f"UPDATE ai_test_runs SET tuple_id = '{UNKNOWN_RUN}'")
    finally:
        writer.close()
    for check, statement in (
        ("outcome", f"UPDATE ai_test_runs SET outcome = 'maybe' WHERE id = '{RUN_ROW}'"),
        (
            "error_when_failed",
            f"UPDATE ai_test_runs SET error = 'set_too_small' WHERE id = '{RUN_ROW}'",
        ),
        (
            "error",
            f"UPDATE ai_test_runs SET outcome = 'failed', error = 'x' WHERE id = '{RUN_ROW}'",
        ),
        ("counts", f"UPDATE ai_test_runs SET passed_count = 2 WHERE id = '{RUN_ROW}'"),
        ("test_set", f"UPDATE ai_test_runs SET test_set = 'other' WHERE id = '{RUN_ROW}'"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match=rf"ck_ai_test_runs_{check}$"):
            run_sql(path, statement)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):  # one live case per key
        run_sql(
            path,
            *(
                "INSERT INTO ai_test_cases (id, case_key, test_set, route, input, expected, "
                f"covers, sha256, loaded_at, retired_at) VALUES ('{case_id}', 'one', "
                f"'extraction', 'extract_signals', '{{}}', '{{}}', '[]', '{digest}', '{at}', NULL)"
                for case_id in ("01JD3QZ8X5V9K2M7N4P6R8T0D1", "01JD3QZ8X5V9K2M7N4P6R8T0D2")
            ),
        )


# Story 4.6: 0034 rebuilds ai_test_cases (origin, contact_id) and ai_test_runs (cases_sha256)
CASE_COLUMNS = (
    "id, case_key, test_set, route, input, expected, covers, sha256, loaded_at, retired_at"
)
RUN_COLUMNS = (
    "id, tuple_id, test_set, outcome, error, case_count, passed_count, metrics, results, "
    "cost_micros, started_at, finished_at"
)
LIVE_CASE = "01JD3QZ8X5V9K2M7N4P6R8T0E1"
RETIRED_CASE = "01JD3QZ8X5V9K2M7N4P6R8T0E2"
CONTACT = "01JD3QZ8X5V9K2M7N4P6R8T0E3"


def rows_of(path: Path, table: str, columns: str) -> list[tuple[object, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(f"SELECT {columns} FROM {table} ORDER BY id").fetchall()
    finally:
        reader.close()


def test_0034_keeps_every_case_and_run_and_adds_the_origin_and_the_fingerprint(
    tmp_path: Path,
) -> None:
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0033_6_5_domain_checks":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (None, "0033_6_5_domain_checks")
    digest, at = "a" * 64, "2026-11-18T09:00:00.000000Z"
    activations = f"{ACTIVATION_COLUMNS}, playbook_sha256"
    run_sql(
        path,
        "INSERT INTO prompt_snapshots (id, route, prompt_version, sha256, text, created_at) "
        f"VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0B0', 'extract_signals', 'v1', '{digest}', 'Text.', "
        f"'{at}')",
        "INSERT INTO ai_route_tuples (id, route, model, effort, prompt_version, prompt_sha256, "
        f"playbook_sha256, schema_version, created_at) VALUES ('{TUPLE_ROW}', 'extract_signals', "
        f"'claude-haiku-5-5', 'medium', 'v1', '{digest}', NULL, 1, '{at}')",
        f"INSERT INTO ai_test_runs ({RUN_COLUMNS}) VALUES ('{RUN_ROW}', '{TUPLE_ROW}', "
        f"'extraction', 'passed', NULL, 1, 1, '[]', '[]', 0, '{at}', '{at}')",
        f"INSERT INTO ai_route_activations ({activations}) VALUES ('{TESTED_ROW}', "
        f"'extract_signals', 'claude-haiku-5-5', 'medium', 'v1', '{digest}', 1, "
        f"'system:playbook', 0, '{RUN_ROW}', '{at}', NULL, NULL)",
        f"INSERT INTO ai_test_cases ({CASE_COLUMNS}) VALUES ('{LIVE_CASE}', 'one', 'extraction', "
        f"'extract_signals', '{{}}', '{{}}', '[]', '{digest}', '{at}', NULL)",
        f"INSERT INTO ai_test_cases ({CASE_COLUMNS}) VALUES ('{RETIRED_CASE}', 'one', "
        f"'extraction', 'extract_signals', '{{}}', '{{}}', '[]', '{'b' * 64}', '{at}', '{at}')",
    )
    cases_before = rows_of(path, "ai_test_cases", CASE_COLUMNS)
    runs_before = rows_of(path, "ai_test_runs", RUN_COLUMNS)
    activations_before = rows_of(path, "ai_route_activations", activations)

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0033_6_5_domain_checks", HEAD)

    assert rows_of(path, "ai_test_cases", CASE_COLUMNS) == cases_before
    assert rows_of(path, "ai_test_cases", "origin, contact_id") == [("fixture", None)] * 2
    assert rows_of(path, "ai_test_runs", RUN_COLUMNS) == runs_before
    assert rows_of(path, "ai_test_runs", "cases_sha256") == [(None,)]
    assert rows_of(path, "ai_route_activations", activations) == activations_before
    reader = sqlite3.connect(path)
    try:
        [(cases_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_ai_test_cases_live_key'"
        ).fetchall()
        run_keys = reader.execute("PRAGMA foreign_key_list(ai_test_runs)").fetchall()
        activation_keys = reader.execute("PRAGMA foreign_key_list(ai_route_activations)").fetchall()
        run_indexes = [row[1] for row in reader.execute("PRAGMA index_list(ai_test_runs)")]
        broken = reader.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        reader.close()
    assert "WHERE retired_at IS NULL" in cases_sql
    assert [(row[2], row[3], row[4]) for row in run_keys] == [("ai_route_tuples", "tuple_id", "id")]
    assert ("ai_test_runs", "test_run_id", "id") in {
        (row[2], row[3], row[4]) for row in activation_keys
    }
    assert "ix_ai_test_runs_tuple_id_test_set" in run_indexes
    assert broken == []
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            writer.execute(
                f"UPDATE ai_route_activations SET test_run_id = '{UNKNOWN_RUN}' "
                f"WHERE id = '{TESTED_ROW}'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            writer.execute(f"UPDATE ai_test_runs SET tuple_id = '{UNKNOWN_RUN}'")
    finally:
        writer.close()
    for check, statement in (
        ("ai_test_cases_origin_contact", f"UPDATE ai_test_cases SET origin = 'override' WHERE id = '{LIVE_CASE}'"),
        ("ai_test_cases_origin_contact", f"UPDATE ai_test_cases SET contact_id = '{CONTACT}' WHERE id = '{LIVE_CASE}'"),
        ("ai_test_cases_origin_contact", f"UPDATE ai_test_cases SET origin = 'other' WHERE id = '{LIVE_CASE}'"),
        ("ai_test_cases_test_set", f"UPDATE ai_test_cases SET test_set = 'other' WHERE id = '{LIVE_CASE}'"),
        ("ai_test_cases_sha256", f"UPDATE ai_test_cases SET sha256 = 'X' WHERE id = '{LIVE_CASE}'"),
        ("ai_test_runs_cases_sha256", f"UPDATE ai_test_runs SET cases_sha256 = 'X' WHERE id = '{RUN_ROW}'"),
        ("ai_test_runs_cases_sha256", f"UPDATE ai_test_runs SET cases_sha256 = '{'A' * 64}' WHERE id = '{RUN_ROW}'"),
        ("ai_test_runs_outcome", f"UPDATE ai_test_runs SET outcome = 'maybe' WHERE id = '{RUN_ROW}'"),
        ("ai_test_runs_counts", f"UPDATE ai_test_runs SET passed_count = 2 WHERE id = '{RUN_ROW}'"),
        (
            "ai_test_runs_error_when_failed",
            f"UPDATE ai_test_runs SET error = 'set_too_small' WHERE id = '{RUN_ROW}'",
        ),
    ):  # fmt: skip
        with pytest.raises(sqlite3.IntegrityError, match=rf"ck_{check}$"):
            run_sql(path, statement)
    run_sql(  # an override row needs its Contact; a fingerprint is 64 lowercase hex digits
        path,
        f"UPDATE ai_test_cases SET origin = 'override', contact_id = '{CONTACT}' "
        f"WHERE id = '{RETIRED_CASE}'",
        f"UPDATE ai_test_runs SET cases_sha256 = '{'c' * 64}' WHERE id = '{RUN_ROW}'",
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):  # still one live case per key
        run_sql(path, f"UPDATE ai_test_cases SET retired_at = NULL WHERE id = '{RETIRED_CASE}'")


def test_0035_keeps_every_crawl_run_and_checks_each_job_posting(tmp_path: Path) -> None:
    """A crawl run stored before 0035 (Story 2.11) keeps its row and reads NULL for every posting count
    (never checked); ``job_postings`` refuses a careers posting with a board token, an ATS one
    without, a closed one with no ``closed_at``, an unknown source and a repeated
    ``(lead, url, title)``."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0034_4_6_ai_test_sets":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (None, "0034_4_6_ai_test_sets")
    at = "2026-11-17T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0D0", "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    run = "01JD3QZ8X5V9K2M7N4P6R8T0D2"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'law_private_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, created_at) "
        f"VALUES ('{lead}', 'Smith Law LLP', 'smith-law.com', '{segment}', '{at}')",
        "INSERT INTO crawl_runs (id, lead_id, site_domain, started_at, ended_at, outcome, "
        "pages_fetched, pages_skipped_robots, renders, errors) "
        f"VALUES ('{run}', '{lead}', 'smith-law.com', '{at}', '{at}', 'ok', 1, 0, 0, 0)",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0034_4_6_ai_test_sets", HEAD)

    counts = (
        "careers_postings, careers_events, greenhouse_postings, greenhouse_events, "
        "lever_postings, lever_events, ashby_postings, ashby_events, board_errors"
    )
    reader = sqlite3.connect(path)
    try:
        rows = reader.execute(f"SELECT id, pages_fetched, {counts} FROM crawl_runs").fetchall()
        columns = [row[1] for row in reader.execute("PRAGMA table_info(crawl_runs)")]
    finally:
        reader.close()
    assert rows == [(run, 1, *([None] * 9))]  # kept, and never checked
    assert columns[-9:] == counts.split(", ")
    insert = (
        "INSERT INTO job_postings (id, lead_id, source, board_token, url, title, description, "
        "posted_date, first_seen_at, last_checked_at, status, closed_at, signal_id, evidence_id) "
        "VALUES ('{id}', '" + lead + "', '{source}', {token}, 'https://smith-law.com/careers', "
        "'Legal Operations Manager', NULL, NULL, '" + at + "', '" + at + "', '{status}', "
        "{closed}, NULL, NULL)"
    )

    def posting(
        id_: str, source: str, token: str, status: str = "live", closed: str = "NULL"
    ) -> str:
        return insert.format(id=id_, source=source, token=token, status=status, closed=closed)

    run_sql(path, posting("01JD3QZ8X5V9K2M7N4P6R8T0D3", "careers_page", "NULL"))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        run_sql(path, posting("01JD3QZ8X5V9K2M7N4P6R8T0D4", "careers_page", "NULL"))
    for id_, source, token, status, closed, check in (
        ("01JD3QZ8X5V9K2M7N4P6R8T0D5", "careers_page", "'acme'", "live", "NULL", "board_token"),
        ("01JD3QZ8X5V9K2M7N4P6R8T0D6", "greenhouse", "NULL", "live", "NULL", "board_token"),
        ("01JD3QZ8X5V9K2M7N4P6R8T0D7", "indeed", "'acme'", "live", "NULL", "source"),
        ("01JD3QZ8X5V9K2M7N4P6R8T0D8", "lever", "'acme'", "closed", "NULL", "closed_at"),
        ("01JD3QZ8X5V9K2M7N4P6R8T0D9", "ashby", "'acme'", "live", f"'{at}'", "closed_at"),
        ("01JD3QZ8X5V9K2M7N4P6R8T0DA", "ashby", "'acme'", "gone", "NULL", "status"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match=rf"ck_job_postings_{check}$"):
            run_sql(path, posting(id_, source, token, status, closed).replace("careers'", "x'"))


LEGAL_FORM_COLUMNS = (
    "legal_form",
    "company_status",
    "legal_form_status",
    "legal_form_site_evidence_id",
    "legal_form_registry_evidence_id",
)


def test_0036_adds_the_legal_form_columns_in_place_and_leaves_every_lead_unchecked(
    tmp_path: Path,
) -> None:
    """A Lead stored before 0036 (Story 2.12) keeps its row and was never checked: every legal-form column is
    NULL, and the five columns are nullable, with no CHECK, at the end of the table."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0035_2_11_job_postings":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (None, "0035_2_11_job_postings")
    at = "2026-11-18T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0D0", "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, ch_number, segment_id, country_code, "
        f"created_at) VALUES ('{lead}', 'Acme Accounting', 'acme.co.uk', '01234567', "
        f"'{segment}', 'GB', '{at}')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0035_2_11_job_postings", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute(
            f"SELECT id, name, ch_number, {', '.join(LEGAL_FORM_COLUMNS)} FROM leads"
        ).fetchall()
        info = {row[1]: row for row in reader.execute("PRAGMA table_info(leads)")}
        columns = [row[1] for row in reader.execute("PRAGMA table_info(leads)")]
        [(table_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'leads'"
        ).fetchall()
    finally:
        reader.close()
    assert rows == [(lead, "Acme Accounting", "01234567", None, None, None, None, None)]
    assert tuple(columns[-5:]) == LEGAL_FORM_COLUMNS
    assert {name: (info[name][2], info[name][3]) for name in LEGAL_FORM_COLUMNS} == {
        "legal_form": ("VARCHAR(64)", 0),
        "company_status": ("VARCHAR(32)", 0),
        "legal_form_status": ("VARCHAR(16)", 0),
        "legal_form_site_evidence_id": ("VARCHAR(26)", 0),
        "legal_form_registry_evidence_id": ("VARCHAR(26)", 0),
    }
    assert "legal_form_status IN" not in table_sql  # no CHECK: added in place
    run_sql(  # any text fits: the code, not a CHECK, keeps the values
        path,
        "UPDATE leads SET legal_form = 'ltd', legal_form_status = 'confirmed' "
        f"WHERE id = '{lead}'",
    )
