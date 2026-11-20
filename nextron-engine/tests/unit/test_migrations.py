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

HEAD = "0066_4_19_fallback_templates"


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
    assert columns[-2:] == ["tag_managers", "mailto"]  # 0030 adds mailto after it


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


DRAFT_COLUMNS = (
    "id, kind, enrollment_id, step_no, day_offset, current_rev, due_at, generic, stale, "
    "created_at, updated_at, status, reapproval_required, event_expired, skipped_at, status_at"
)
CHANNEL_DRAFT = "01JD3QZ8X5V9K2M7N4P6R8T0D1"
REPLY_DRAFT = "01JD3QZ8X5V9K2M7N4P6R8T0D2"
DRAFT_ENROLLMENT = "01JD3QZ8X5V9K2M7N4P6R8T0D3"


def draft_rows(path: Path) -> list[tuple[object, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(f"SELECT {DRAFT_COLUMNS} FROM drafts ORDER BY id").fetchall()
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
    # Story 8.7's 0062 adds mode after them
    assert [name for name in columns if name != "mode"][-9:] == counts.split(", ")
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
    start = columns.index(LEGAL_FORM_COLUMNS[0])  # 0041 (Story 2.15) adds columns after them
    assert tuple(columns[start : start + 5]) == LEGAL_FORM_COLUMNS
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


WARMUP_COLUMNS = (
    "warmup_started_on",
    "warmup_method",
    "warm_level",
    "warmup_tool_daily",
    "ramp_step",
    "ramp_target",
)


def test_0039_keeps_every_inbox_unrecorded_with_the_default_ramp_and_checks_the_warmup(
    tmp_path: Path,
) -> None:
    """An inbox stored before 0039 (Story 6.4) keeps its row, reads as warm-up unrecorded with
    the default ramp, and keeps its foreign key and unique address; the new checks refuse a
    partial record and an out-of-range value."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0038_4_8_enrollments":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (None, "0038_4_8_enrollments")
    at = "2026-11-18T09:00:00.000000Z"
    domain, inbox = "01JD3QZ8X5V9K2M7N4P6R8T0E0", "01JD3QZ8X5V9K2M7N4P6R8T0E1"
    run_sql(
        path,
        f"INSERT INTO sending_domains (id, domain, created_at) VALUES ('{domain}', "
        f"'nextron-ai.com', '{at}')",
        "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, status, "
        f"created_at) VALUES ('{inbox}', 'furqaan@nextron-ai.com', 'Main', '{domain}', "
        f"'Furqaan Ahmed', 'active', '{at}')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0038_4_8_enrollments", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute(
            f"SELECT id, address, label, status, {', '.join(WARMUP_COLUMNS)} FROM sending_inboxes"
        ).fetchall()
        keys = reader.execute("PRAGMA foreign_key_list(sending_inboxes)").fetchall()
        indexes = [row[1] for row in reader.execute("PRAGMA index_list(sending_inboxes)")]
    finally:
        reader.close()
    assert rows == [
        (inbox, "furqaan@nextron-ai.com", "Main", "active", None, None, None, 0, 5, 30)
    ]
    assert [(row[2], row[3], row[4]) for row in keys] == [
        ("sending_domains", "sending_domain_id", "id")
    ]
    assert "uq_sending_inboxes_address" in indexes
    run_sql(
        path,
        "UPDATE sending_inboxes SET warmup_started_on = '2026-11-01', warmup_method = 'manual', "
        f"warm_level = 12, warmup_tool_daily = 100, ramp_step = 10, ramp_target = 50 "
        f"WHERE id = '{inbox}'",
    )
    for assignment, check in (
        ("warmup_method = NULL", "warmup_record"),
        ("warm_level = NULL", "warmup_record"),
        ("warmup_started_on = NULL", "warmup_record"),
        ("warm_level = 101", "warmup_ranges"),
        ("warm_level = -1", "warmup_ranges"),
        ("warmup_tool_daily = 101", "warmup_ranges"),
        ("ramp_step = 0", "warmup_ranges"),
        ("ramp_step = 11", "warmup_ranges"),
        ("ramp_target = 0", "warmup_ranges"),
        ("ramp_target = 51", "warmup_ranges"),
        ("warmup_method = ''", "warmup_ranges"),
        (f"warmup_method = '{'x' * 41}'", "warmup_ranges"),
        ("status = 'warming'", "status"),
        ("address = 'x@nextronai.com'", "not_brand"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match=rf"ck_sending_inboxes_{check}$"):
            run_sql(path, f"UPDATE sending_inboxes SET {assignment} WHERE id = '{inbox}'")



PLACE_COORD_COLUMNS = ("place_lat", "place_lng", "place_coords_at")


def test_0044_adds_the_place_coordinates_in_place_and_leaves_every_lead_without_them(
    tmp_path: Path,
) -> None:
    """A Lead stored before 0044 (Story 8.2) keeps its row, its ``place_id`` included, and holds
    no coordinates: the three columns are nullable, with no CHECK, at the end of the table."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0043_3_11_unsub_requests":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0043_3_11_unsub_requests",
        )
    at = "2026-11-18T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0D0", "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, place_id, segment_id, country_code, created_at) "
        f"VALUES ('{lead}', 'Smith CPA', 'ChIJ-first', '{segment}', 'US', '{at}')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0043_3_11_unsub_requests", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute(
            f"SELECT id, name, place_id, {', '.join(PLACE_COORD_COLUMNS)} FROM leads"
        ).fetchall()
        info = {row[1]: row for row in reader.execute("PRAGMA table_info(leads)")}
        columns = [row[1] for row in reader.execute("PRAGMA table_info(leads)")]
        [(table_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'leads'"
        ).fetchall()
    finally:
        reader.close()
    assert rows == [(lead, "Smith CPA", "ChIJ-first", None, None, None)]
    start = columns.index("place_lat")  # added at the end; later revisions add after them
    assert tuple(columns[start : start + 3]) == PLACE_COORD_COLUMNS
    assert columns[start - 1] == "disqualified_evidence"  # 0041 (Story 2.15) comes first
    assert {name: (info[name][2], info[name][3]) for name in PLACE_COORD_COLUMNS} == {
        "place_lat": ("FLOAT", 0),
        "place_lng": ("FLOAT", 0),
        "place_coords_at": ("VARCHAR(27)", 0),
    }
    checks = " ".join(line for line in table_sql.splitlines() if "CHECK" in line)
    assert not any(name in checks for name in PLACE_COORD_COLUMNS)  # no CHECK: added in place
    run_sql(
        path,
        "UPDATE leads SET place_lat = 39.96, place_lng = -82.99, "
        f"place_coords_at = '{at}' WHERE id = '{lead}'",
    )


def test_0045_keeps_every_draft_and_its_partial_index(tmp_path: Path) -> None:
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0044_8_2_place_coords":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (None, "0044_8_2_place_coords")
    at, due = "2026-11-18T09:00:00.000000Z", "2026-11-19T14:00:00.000000Z"
    # Non-step kinds only: a step slot needs a real Enrollment, whose own chain (Lead, Contact,
    # Campaign, Variant, inbox, Sequence, Offer) the post-upgrade foreign-key check would demand
    run_sql(
        path,
        f"INSERT INTO drafts ({DRAFT_COLUMNS}) VALUES ('{CHANNEL_DRAFT}', 'channel', NULL, NULL, "
        f"NULL, NULL, '{due}', 1, 0, '{at}', '{at}', 'approved', 1, 0, NULL, '{at}')",
        f"INSERT INTO drafts ({DRAFT_COLUMNS}) VALUES ('{REPLY_DRAFT}', 'reply', NULL, NULL, "
        f"NULL, NULL, NULL, 0, 1, '{at}', '{at}', 'ready', 0, 1, '{at}', NULL)",
    )
    before = draft_rows(path)
    shutil.copy2(
        SCRIPT_LOCATION / "versions" / "0045_4_12_draft_revisions.py",
        location / "versions" / "0045_4_12_draft_revisions.py",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            "0044_8_2_place_coords",
            "0045_4_12_draft_revisions",
        )

    assert draft_rows(path) == before  # every row and column unchanged, current_rev NULL
    assert {row[5] for row in before} == {None}
    reader = sqlite3.connect(path)
    try:
        [(index_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_drafts_enrollment_step'"
        ).fetchall()
        keys = reader.execute("PRAGMA foreign_key_list(drafts)").fetchall()
    finally:
        reader.close()
    assert "WHERE kind IN ('step','recontact')" in index_sql
    assert {row[2] for row in keys} == {"enrollments", "draft_revisions"}
    slot = (
        f"INSERT INTO drafts ({DRAFT_COLUMNS}) VALUES ('{{id}}', 'step', '{DRAFT_ENROLLMENT}', 1, "
        f"0, NULL, NULL, 0, 0, '{at}', '{at}', 'ready', 0, 0, NULL, NULL)"
    )
    run_sql(path, slot.format(id="01JD3QZ8X5V9K2M7N4P6R8T0D6"))  # foreign keys off
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):  # still one slot per Step
        run_sql(path, slot.format(id="01JD3QZ8X5V9K2M7N4P6R8T0D4"))
    run_sql(  # the index stays partial: a second reply without a Step is fine
        path,
        f"INSERT INTO drafts ({DRAFT_COLUMNS}) VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0D5', 'reply', "
        f"NULL, NULL, NULL, NULL, NULL, 0, 0, '{at}', '{at}', 'ready', 0, 0, NULL, NULL)",
    )
    with pytest.raises(sqlite3.IntegrityError, match=r"ck_drafts_step_slot$"):
        run_sql(path, "UPDATE drafts SET step_no = NULL WHERE id = '01JD3QZ8X5V9K2M7N4P6R8T0D6'")
def test_0047_creates_an_empty_discovery_runs_table_with_its_checks(tmp_path: Path) -> None:
    """0047 (Story 8.3) adds ``discovery_runs`` to a database at 0046 and touches no Lead; its
    CHECKs refuse an unknown source or status, a running row with a finish time, and a
    negative count."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0046_3_12_unsub_poll_state":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0046_3_12_unsub_poll_state",
        )
    at = "2026-11-18T09:00:00.000000Z"
    segment = "01JD3QZ8X5V9K2M7N4P6R8T0D0"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_ai', '{{}}', 1, '{at}', '{at}')",
    )

    shutil.copy2(  # 0047 alone: 0060 (Story 8.4) widens these checks
        SCRIPT_LOCATION / "versions" / "0047_8_3_discovery_runs.py",
        location / "versions" / "0047_8_3_discovery_runs.py",
    )
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            "0046_3_12_unsub_poll_state",
            "0047_8_3_discovery_runs",
        )

    reader = sqlite3.connect(path)
    try:
        columns = [row[1] for row in reader.execute("PRAGMA table_info(discovery_runs)")]
        count = reader.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0]
        indexes = [row[1] for row in reader.execute("PRAGMA index_list(discovery_runs)")]
    finally:
        reader.close()
    assert columns == [
        "id",
        "source",
        "campaign_id",
        "segment_id",
        "country_code",
        "location",
        "query_hash",
        "status",
        "started_at",
        "finished_at",
        "seen",
        "created",
        "merged",
        "suppressed",
        "no_website",
        "market_blocked",
    ]
    assert count == 0
    assert "ix_discovery_runs_started_at" in indexes
    campaign = "01JD3QZ8X5V9K2M7N4P6R8T0D9"
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA foreign_keys = OFF")  # only the CHECKs are under test here
        good = {
            "id": "01JD3QZ8X5V9K2M7N4P6R8T0E0",
            "source": "places",
            "campaign_id": campaign,
            "segment_id": segment,
            "country_code": "US",
            "location": "Columbus, OH",
            "query_hash": "0" * 16,
            "status": "finished",
            "started_at": at,
            "finished_at": at,
            "seen": 0,
            "created": 0,
            "merged": 0,
            "suppressed": 0,
            "no_website": 0,
            "market_blocked": 0,
        }

        def insert(**changes: object) -> None:
            row = {**good, **changes}
            names = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            writer.execute(
                f"INSERT INTO discovery_runs ({names}) VALUES ({marks})",  # noqa: S608
                tuple(row.values()),
            )

        bad = [
            {"source": "companies_house"},
            {"status": "done"},
            {"status": "running"},  # running with a finish time
            {"finished_at": None},  # finished without one
            {"country_code": "us"},
            {"seen": -1},
            {"market_blocked": -1},
        ]
        for number, changes in enumerate(bad):
            with pytest.raises(sqlite3.IntegrityError):
                insert(id=f"01JD3QZ8X5V9K2M7N4P6R8T1{number:02d}", **changes)
        insert()  # the good row is accepted
        insert(id="01JD3QZ8X5V9K2M7N4P6R8T0E1", status="running", finished_at=None)
        writer.commit()
    finally:
        writer.close()


def test_0049_adds_the_deal_columns_in_place_and_the_call_outcomes_table(
    tmp_path: Path,
) -> None:
    """A Lead stored before 0049 (Story 9.1) keeps its row with no deal value or notes; the two
    columns are nullable, with no CHECK, at the end of the table; ``call_outcomes`` is created
    with its foreign keys and its ``lead_id`` index."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0048_10_2_assist_accounts":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0048_10_2_assist_accounts",
        )
    at = "2026-11-18T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0D0", "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, created_at, pipeline_stage) "
        f"VALUES ('{lead}', 'Acme Accounting', 'acme.co.uk', '{segment}', '{at}', 'replied')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0048_10_2_assist_accounts", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute("SELECT id, pipeline_stage, deal_value, notes FROM leads").fetchall()
        columns = [(row[1], row[2], row[3]) for row in reader.execute("PRAGMA table_info(leads)")]
        outcomes = [
            (row[1], row[2], row[3]) for row in reader.execute("PRAGMA table_info(call_outcomes)")
        ]
        keys = sorted(
            (row[3], row[2], row[4])
            for row in reader.execute("PRAGMA foreign_key_list(call_outcomes)")
        )
        indexes = [row[1] for row in reader.execute("PRAGMA index_list(call_outcomes)")]
    finally:
        reader.close()
    assert rows == [(lead, "replied", None, None)]
    # 0060 (Story 8.4) adds website_lookup_at after them
    assert columns[-3:] == [
        ("deal_value", "BIGINT", 0),
        ("notes", "TEXT", 0),
        ("website_lookup_at", "VARCHAR(27)", 0),
    ]
    assert outcomes[:8] == [  # 0053 (Story 9.4) adds the outcome's columns after
        ("id", "VARCHAR(26)", 1),
        ("lead_id", "VARCHAR(26)", 1),
        ("contact_id", "VARCHAR(26)", 1),
        ("scheduled_at", "VARCHAR(27)", 1),
        ("prospect_tz", "VARCHAR(64)", 1),
        ("booked_at", "VARCHAR(27)", 1),
        ("booked_by", "VARCHAR(64)", 1),
        ("superseded_at", "VARCHAR(27)", 0),
    ]
    assert keys == [("contact_id", "contacts", "id"), ("lead_id", "leads", "id")]
    assert "ix_call_outcomes_lead_id" in indexes
    run_sql(  # any value fits: the code, not a CHECK, keeps the limits
        path, f"UPDATE leads SET deal_value = -1, notes = 'x' WHERE id = '{lead}'"
    )


def test_0053_adds_the_outcome_columns_in_place_and_keeps_every_booking_pending(
    tmp_path: Path,
) -> None:
    """A booking stored before 0053 (Story 9.4) keeps its row with no outcome; the eleven
    columns are nullable, with no CHECK, at the end of ``call_outcomes``."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0052_4_10_daily_capacity":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0052_4_10_daily_capacity",
        )
    at = "2026-11-18T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0D0", "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    contact, booking = "01JD3QZ8X5V9K2M7N4P6R8T0D2", "01JD3QZ8X5V9K2M7N4P6R8T0D3"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, created_at, pipeline_stage) "
        f"VALUES ('{lead}', 'Acme Accounting', 'acme.co.uk', '{segment}', '{at}', 'call_booked')",
        "INSERT INTO contacts (id, lead_id, name, created_at, updated_at) "
        f"VALUES ('{contact}', '{lead}', 'Jane Doe', '{at}', '{at}')",
        "INSERT INTO call_outcomes (id, lead_id, contact_id, scheduled_at, prospect_tz, "
        "booked_at, booked_by) "
        f"VALUES ('{booking}', '{lead}', '{contact}', '{at}', 'Europe/London', '{at}', 'operator')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0052_4_10_daily_capacity", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute(
            "SELECT id, outcome, recorded_at, reason, next_step FROM call_outcomes"
        ).fetchall()
        columns = [
            (row[1], row[2], row[3]) for row in reader.execute("PRAGMA table_info(call_outcomes)")
        ]
    finally:
        reader.close()
    assert rows == [(booking, None, None, None, None)]
    assert columns[8:] == [
        ("outcome", "VARCHAR(16)", 0),
        ("qualified", "BOOLEAN", 0),
        ("reason", "TEXT", 0),
        ("next_step", "TEXT", 0),
        ("next_step_on", "DATE", 0),
        ("audit_proposed", "BOOLEAN", 0),
        ("audit_price", "BIGINT", 0),
        ("objections", "VARCHAR(200)", 0),
        ("price_reaction", "VARCHAR(16)", 0),
        ("recorded_at", "VARCHAR(27)", 0),
        ("recorded_by", "VARCHAR(64)", 0),
    ]
    run_sql(  # any value fits: the code, not a CHECK, keeps the rules
        path, f"UPDATE call_outcomes SET outcome = 'maybe', audit_price = -1 WHERE id = '{booking}'"
    )


def test_0055_stores_each_existing_segments_current_version_and_keeps_it_immutable(
    tmp_path: Path,
) -> None:
    """Every Segment stored before 0055 (Story 11.5) gets one segment_versions row at its
    current version, with its definition, its updated_at and actor seed; the rows can't be
    updated, deleted or replaced afterwards."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0054_2_16_signal_inbox":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0054_2_16_signal_inbox",
        )
    created, updated = "2026-11-14T09:00:00.000000Z", "2026-11-18T09:00:00.000000Z"
    law, ops = "01JD3QZ8X5V9K2M7N4P6R8T0D0", "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{law}', 'law_ai', '{{\"v\":3}}', 3, '{created}', '{updated}')",
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{ops}', 'ops_ai', '{{\"v\":1}}', 1, '{created}', '{created}')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0054_2_16_signal_inbox", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute(
            "SELECT segment_id, version, definition, created_at, actor, length(id) "
            "FROM segment_versions ORDER BY segment_id"
        ).fetchall()
    finally:
        reader.close()
    assert rows == [
        (law, 3, '{"v":3}', updated, "seed", 26),
        (ops, 1, '{"v":1}', created, "seed", 26),
    ]
    for statement in (
        "UPDATE segment_versions SET definition = '{}'",
        "DELETE FROM segment_versions",
        "INSERT OR REPLACE INTO segment_versions (id, segment_id, version, definition, "
        f"created_at, actor) VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0D9', '{law}', 3, '{{}}', "
        f"'{updated}', 'operator')",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="segment_versions rows are immutable"):
            run_sql(path, statement)
    with pytest.raises(sqlite3.IntegrityError, match="ck_segment_versions_actor"):
        run_sql(
            path,
            "INSERT INTO segment_versions (id, segment_id, version, definition, created_at, "
            f"actor) VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0D8', '{law}', 4, '{{}}', '{updated}', "
            "'system')",
        )


def test_0056_stores_each_sequence_s_current_version_and_binds_matching_enrollments(
    tmp_path: Path,
) -> None:
    """Story 11.7: every Sequence stored before 0056 gets one immutable sequence_versions row at
    its current version, its definition the canonical sequence_form JSON of its brief rows; each
    Enrollment at that version is bound to it, one at a version never kept stays NULL; the
    recreated trg_enrollments_bound covers the new column."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0055_11_5_segment_editing":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0055_11_5_segment_editing",
        )
    at, later = "2026-11-14T09:00:00.000000Z", "2026-11-18T09:00:00.000000Z"
    ids = {name: f"01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}" for n, name in enumerate(
        ("seq", "seg", "mkt", "off", "cmp", "var", "dom", "box", "lead", "con", "e1", "e2", "b1",
         "b2", "lead2", "con2")
    )}
    brief = (
        "INSERT INTO step_briefs (id, sequence_id, step, delay_days, purpose, word_limit, "
        "word_comparison, allowed_content, ask, link_rule, no_price, no_audit, subject_rule, "
        "segment_angles, created_at, updated_at) VALUES "
    )
    enrollment = (
        "INSERT INTO enrollments (id, lead_id, contact_id, campaign_id, variant_id, "
        "sending_inbox_id, sequence_id, sequence_version, offer_id, origin, status, created_at, "
        "updated_at) VALUES ('{id}', '{lead}', '{con}', '{cmp}', '{var}', '{box}', '{seq}', "
        "{version}, '{off}', 'selection', '{status}', '{at}', '{at}')"
    )
    run_sql(
        path,
        "INSERT INTO sequences (id, key, name, version, created_at, updated_at) "
        f"VALUES ('{ids['seq']}', 'default', 'Default', 2, '{at}', '{later}')",
        brief + f"('{ids['b1']}', '{ids['seq']}', 1, 0, 'Open', 80, 'under', "
        "'[\"One observed Signal\"]', 'Worth a look?', 'footer_only', 1, 1, "
        "'{\"guidance\":[\"Short\"],\"word_comparison\":\"under\",\"word_limit\":7}', '[]', "
        f"'{at}', '{at}')",
        brief + f"('{ids['b2']}', '{ids['seq']}', 2, 3, 'New angle', 70, 'at_most', "
        "'[\"A new fact\"]', 'Still relevant?', 'footer_only', 1, 1, NULL, "
        "'[{\"angle\":\"Their intake\",\"market\":\"us\",\"segment\":\"law\"}]', "
        f"'{at}', '{at}')",
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{ids['seg']}', 'law', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO markets (id, code, name, enabled, country_code, cctlds, cold_email_allowed, "
        "free_webmail_blocked, default_timezone, unknown_timezone, english_variant, "
        "footer_template, privacy_line_required, version, created_at, updated_at) VALUES "
        f"('{ids['mkt']}', 'us', 'US', 1, 'US', '[]', 1, 1, 'America/New_York', "
        f"'America/New_York', 'en-US', '[]', 0, 1, '{at}', '{at}')",
        "INSERT INTO offers (id, key, name, ladder, version, created_at, updated_at) "
        f"VALUES ('{ids['off']}', 'default', 'Default', '[]', 1, '{at}', '{at}')",
        "INSERT INTO campaigns (id, key, name, segment_id, market_id, offer_id, sequence_id, "
        f"status, version, created_at, updated_at) VALUES ('{ids['cmp']}', 'c', 'C', "
        f"'{ids['seg']}', '{ids['mkt']}', '{ids['off']}', '{ids['seq']}', 'active', 1, "
        f"'{at}', '{at}')",
        "INSERT INTO variants (id, campaign_id, name, status, step_angles, created_at, "
        f"updated_at) VALUES ('{ids['var']}', '{ids['cmp']}', 'A', 'active', '[]', '{at}', "
        f"'{at}')",
        f"INSERT INTO sending_domains (id, domain, created_at) VALUES ('{ids['dom']}', "
        f"'nextron-mail.com', '{at}')",
        "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, "
        f"created_at) VALUES ('{ids['box']}', 'mac@nextron-mail.com', 'Main', '{ids['dom']}', "
        f"'Mac', '{at}')",
        "INSERT INTO leads (id, name, segment_id, web_domain, created_at) VALUES "
        f"('{ids['lead']}', 'Firm', '{ids['seg']}', 'firm.com', '{at}')",
        "INSERT INTO contacts (id, lead_id, name, created_at, updated_at) VALUES "
        f"('{ids['con']}', '{ids['lead']}', 'Zelda', '{at}', '{at}')",
        enrollment.format(**ids, id=ids["e1"], version=2, status="active", at=at),
        "INSERT INTO leads (id, name, segment_id, web_domain, created_at) VALUES "
        f"('{ids['lead2']}', 'Other', '{ids['seg']}', 'other.com', '{at}')",
        "INSERT INTO contacts (id, lead_id, name, created_at, updated_at) VALUES "
        f"('{ids['con2']}', '{ids['lead2']}', 'Ada', '{at}', '{at}')",
        enrollment.format(
            **{**ids, "lead": ids["lead2"], "con": ids["con2"]},
            id=ids["e2"],
            version=1,
            status="active",
            at=at,
        ),
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0055_11_5_segment_editing", HEAD)

    reader = sqlite3.connect(path)
    try:
        [(version_id, sequence_id, version, definition, created, actor)] = reader.execute(
            "SELECT id, sequence_id, version, definition, created_at, actor "
            "FROM sequence_versions"
        ).fetchall()
        bound = reader.execute(
            "SELECT id, sequence_version_id FROM enrollments ORDER BY id"
        ).fetchall()
    finally:
        reader.close()
    assert (sequence_id, version, created, actor, len(version_id)) == (
        ids["seq"], 2, later, "seed", 26
    )
    assert definition == (
        '{"key":"default","name":"Default","steps":[{"allowed_content":["One observed Signal"],'
        '"ask":"Worth a look?","delay_days":0,"link_rule":"footer_only","no_audit":true,'
        '"no_price":true,"purpose":"Open","subject_rule":{"guidance":["Short"],'
        '"word_comparison":"under","word_limit":7},"word_comparison":"under","word_limit":80},'
        '{"allowed_content":["A new fact"],"ask":"Still relevant?","delay_days":3,'
        '"link_rule":"footer_only","no_audit":true,"no_price":true,"purpose":"New angle",'
        '"segment_angles":[{"angle":"Their intake","market":"us","segment":"law"}],'
        '"word_comparison":"at_most","word_limit":70}]}'
    )
    assert bound == [(ids["e1"], version_id), (ids["e2"], None)]
    for statement in (
        "UPDATE sequence_versions SET definition = '{}'",
        "DELETE FROM sequence_versions",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="sequence_versions rows are immutable"):
            run_sql(path, statement)
    with pytest.raises(sqlite3.IntegrityError, match="bound columns"):
        run_sql(path, f"UPDATE enrollments SET sequence_version_id = NULL WHERE id = '{ids['e1']}'")
    run_sql(path, f"UPDATE enrollments SET status = 'paused', pause_cause = 'operator' "
            f"WHERE id = '{ids['e1']}'")  # an unbound column still changes


def _chain_to(tmp_path: Path, last: str) -> Path:
    """A copy of the real chain up to and including ``last``."""
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= last:
            shutil.copy2(revision, location / "versions" / revision.name)
    return location


def test_0060_rebuilds_discovery_runs_and_provenance_and_adds_website_lookup_at(
    tmp_path: Path,
) -> None:
    """0060 (Story 8.4) keeps a Lead and a Provenance Record stored at 0059; then
    ``discovery_runs`` allows a Companies House run without a location and counts ``inactive``,
    ``leads.website_lookup_at`` is added in place, and ``provenance_records`` allows ``registry``
    and keeps its erasure triggers."""
    location = _chain_to(tmp_path, "0059_4_14_ai_grounding")
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0059_4_14_ai_grounding",
        )
    at = "2026-11-19T09:00:00.000000Z"
    segment = "01JD3QZ8X5V9K2M7N4P6R8T0D0"
    lead = "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    contact = "01JD3QZ8X5V9K2M7N4P6R8T0D2"
    campaign = "01JD3QZ8X5V9K2M7N4P6R8T0D9"
    record = "01JD3QZ8X5V9K2M7N4P6R8T0E1"
    places_run = "01JD3QZ8X5V9K2M7N4P6R8T0E2"
    market = "01JD3QZ8X5V9K2M7N4P6R8T0E3"
    offer = "01JD3QZ8X5V9K2M7N4P6R8T0E4"
    sequence = "01JD3QZ8X5V9K2M7N4P6R8T0E5"
    zeros = "0" * 16
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, country_code, created_at) "
        f"VALUES ('{lead}', 'Acme Law', 'acme-law.com', '{segment}', 'US', '{at}')",
        "INSERT INTO contacts (id, lead_id, name, is_primary, created_at, updated_at) "
        f"VALUES ('{contact}', '{lead}', 'Ann Lee', 0, '{at}', '{at}')",
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        f"retrieved_at) VALUES ('{record}', '{contact}', 'contact', 'website', 'x', '{at}')",
        # a 0047 Places run of a real Campaign (the upgrade checks the foreign keys)
        "INSERT INTO markets (id, code, name, enabled, country_code, cctlds, cold_email_allowed, "
        "free_webmail_blocked, default_timezone, unknown_timezone, english_variant, "
        "footer_template, privacy_line_required, version, created_at, updated_at) VALUES "
        f"('{market}', 'uk', 'UK', 1, 'GB', '[]', 1, 1, 'Europe/London', "
        f"'Europe/London', 'en-GB', 'x', 1, 1, '{at}', '{at}')",
        "INSERT INTO offers (id, key, name, ladder, version, created_at, updated_at) VALUES "
        f"('{offer}', 'audit', 'Audit', '[]', 1, '{at}', '{at}')",
        "INSERT INTO sequences (id, key, name, version, created_at, updated_at) VALUES "
        f"('{sequence}', 'default', 'Default', 1, '{at}', '{at}')",
        "INSERT INTO campaigns (id, key, name, segment_id, market_id, offer_id, sequence_id, "
        "status, version, created_at, updated_at) VALUES "
        f"('{campaign}', 'ops_uk', 'Ops UK', '{segment}', '{market}', '{offer}', "
        f"'{sequence}', 'draft', 1, '{at}', '{at}')",
        "INSERT INTO discovery_runs (id, source, campaign_id, segment_id, country_code, location, "
        "query_hash, status, started_at, finished_at, seen, created, merged, suppressed, "
        f"no_website, market_blocked) VALUES ('{places_run}', 'places', '{campaign}', "
        f"'{segment}', 'GB', 'Leeds', '{zeros}', 'finished', '{at}', '{at}', 9, 4, 2, 1, 1, 1)",
    )
    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0059_4_14_ai_grounding", HEAD)

    reader = sqlite3.connect(path)
    try:
        runs = reader.execute(
            "SELECT id, source, location, status, seen, created, merged, suppressed, no_website, "
            "market_blocked, inactive FROM discovery_runs"
        ).fetchall()
        integrity = reader.execute("PRAGMA integrity_check").fetchall()
        run_columns = [row[1] for row in reader.execute("PRAGMA table_info(discovery_runs)")]
        run_indexes = [row[1] for row in reader.execute("PRAGMA index_list(discovery_runs)")]
        lead_columns = [row[1] for row in reader.execute("PRAGMA table_info(leads)")]
        leads = reader.execute("SELECT id, name, website_lookup_at FROM leads").fetchall()
        records = reader.execute("SELECT id, source_kind FROM provenance_records").fetchall()
        provenance_indexes = [
            row[1] for row in reader.execute("PRAGMA index_list(provenance_records)")
        ]
        triggers = {
            name
            for (name,) in reader.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'provenance_records'"
            )
        }
    finally:
        reader.close()
    assert runs == [(places_run, "places", "Leeds", "finished", 9, 4, 2, 1, 1, 1, 0)]
    assert integrity == [("ok",)]  # the copied row passes the new CHECKs
    assert "inactive" in run_columns  # 0040 (Story 8.10) adds two more after it
    assert "ix_discovery_runs_started_at" in run_indexes
    assert lead_columns[-1] == "website_lookup_at"
    assert leads == [(lead, "Acme Law", None)]
    assert records == [(record, "website")]
    assert "ix_provenance_records_contact_id" in provenance_indexes
    assert triggers == {
        "trg_provenance_records_erased_update",
        "trg_provenance_records_erased_insert",
    }

    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA foreign_keys = OFF")  # only the CHECKs and triggers are tested
        good = {
            "id": "01JD3QZ8X5V9K2M7N4P6R8T0F0",
            "source": "companies_house",
            "campaign_id": campaign,
            "segment_id": segment,
            "country_code": "GB",
            "location": None,
            "query_hash": zeros,
            "status": "finished",
            "started_at": at,
            "finished_at": at,
            "seen": 0,
            "created": 0,
            "merged": 0,
            "suppressed": 0,
            "no_website": 0,
            "market_blocked": 0,
            "inactive": 0,
        }

        def insert(**changes: object) -> None:
            row = {**good, **changes}
            names = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            writer.execute(
                f"INSERT INTO discovery_runs ({names}) VALUES ({marks})",  # noqa: S608
                tuple(row.values()),
            )

        bad = [
            {"source": "aggregator"},
            {"location": "Leeds"},  # a Companies House run has no location
            {"source": "places"},  # a Places run has one
            {"inactive": -1},
        ]
        for number, changes in enumerate(bad):
            with pytest.raises(sqlite3.IntegrityError):
                insert(id=f"01JD3QZ8X5V9K2M7N4P6R8T1{number:02d}", **changes)
        insert()  # a Companies House run without a location is accepted
        # rewriting the copied Places run re-checks it against the new CHECKs
        writer.execute("UPDATE discovery_runs SET seen = seen WHERE id = ?", (places_run,))

        def provenance(record_id: str, kind: str) -> None:
            writer.execute(
                "INSERT INTO provenance_records (id, contact_id, subject, source_kind, "
                "source_detail, retrieved_at) VALUES (?, ?, 'contact', ?, 'x', ?)",
                (record_id, contact, kind, at),
            )

        provenance("01JD3QZ8X5V9K2M7N4P6R8T0F1", "registry")
        with pytest.raises(sqlite3.IntegrityError):
            provenance("01JD3QZ8X5V9K2M7N4P6R8T0F2", "maps")
        writer.execute(
            "UPDATE contacts SET erased_at = ?, name = '[erased]' WHERE id = ?", (at, contact)
        )
        with pytest.raises(sqlite3.IntegrityError, match="tombstone"):
            provenance("01JD3QZ8X5V9K2M7N4P6R8T0F3", "registry")
        with pytest.raises(sqlite3.IntegrityError, match="tombstone"):
            writer.execute("UPDATE provenance_records SET notes = 'n' WHERE id = ?", (record,))
        writer.execute("UPDATE leads SET website_lookup_at = ? WHERE id = ?", (at, lead))
        writer.commit()
    finally:
        writer.close()


def test_0061_rebuilds_discovery_runs_for_the_weekday_top_up(tmp_path: Path) -> None:
    """0061 (Story 8.10) keeps a Places and a Companies House run stored at 0060 (both new
    columns NULL), allows the three new statuses, and checks ``scheduled_for``, ``ready_pool``
    and the location of a ``pool_sufficient`` row."""
    location = _chain_to(tmp_path, "0060_8_4_registry_discovery")
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0060_8_4_registry_discovery",
        )
    at = "2026-11-19T09:00:00.000000Z"
    segment = "01JD3QZ8X5V9K2M7N4P6R8T0D0"
    campaign = "01JD3QZ8X5V9K2M7N4P6R8T0D9"
    market = "01JD3QZ8X5V9K2M7N4P6R8T0E3"
    offer = "01JD3QZ8X5V9K2M7N4P6R8T0E4"
    sequence = "01JD3QZ8X5V9K2M7N4P6R8T0E5"
    places_run = "01JD3QZ8X5V9K2M7N4P6R8T0E2"
    registry_run = "01JD3QZ8X5V9K2M7N4P6R8T0E6"
    zeros = "0" * 16
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO markets (id, code, name, enabled, country_code, cctlds, cold_email_allowed, "
        "free_webmail_blocked, default_timezone, unknown_timezone, english_variant, "
        "footer_template, privacy_line_required, version, created_at, updated_at) VALUES "
        f"('{market}', 'uk', 'UK', 1, 'GB', '[]', 1, 1, 'Europe/London', "
        f"'Europe/London', 'en-GB', 'x', 1, 1, '{at}', '{at}')",
        "INSERT INTO offers (id, key, name, ladder, version, created_at, updated_at) VALUES "
        f"('{offer}', 'audit', 'Audit', '[]', 1, '{at}', '{at}')",
        "INSERT INTO sequences (id, key, name, version, created_at, updated_at) VALUES "
        f"('{sequence}', 'default', 'Default', 1, '{at}', '{at}')",
        "INSERT INTO campaigns (id, key, name, segment_id, market_id, offer_id, sequence_id, "
        "status, version, created_at, updated_at) VALUES "
        f"('{campaign}', 'ops_uk', 'Ops UK', '{segment}', '{market}', '{offer}', "
        f"'{sequence}', 'draft', 1, '{at}', '{at}')",
        "INSERT INTO discovery_runs (id, source, campaign_id, segment_id, country_code, location, "
        "query_hash, status, started_at, finished_at, seen, created, merged, suppressed, "
        f"no_website, market_blocked, inactive) VALUES ('{places_run}', 'places', '{campaign}', "
        f"'{segment}', 'GB', 'Leeds', '{zeros}', 'finished', '{at}', '{at}', 9, 4, 2, 1, 1, 1, 0)",
        "INSERT INTO discovery_runs (id, source, campaign_id, segment_id, country_code, location, "
        "query_hash, status, started_at, finished_at, seen, created, merged, suppressed, "
        f"no_website, market_blocked, inactive) VALUES ('{registry_run}', 'companies_house', "
        f"'{campaign}', '{segment}', 'GB', NULL, '{zeros}', 'deferred', '{at}', '{at}', "
        "3, 1, 0, 0, 1, 0, 2)",
    )
    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0060_8_4_registry_discovery", HEAD)

    reader = sqlite3.connect(path)
    try:
        runs = reader.execute(
            "SELECT id, source, location, status, seen, created, inactive, scheduled_for, "
            "ready_pool FROM discovery_runs ORDER BY id"
        ).fetchall()
        integrity = reader.execute("PRAGMA integrity_check").fetchall()
        columns = [row[1] for row in reader.execute("PRAGMA table_info(discovery_runs)")]
        indexes = [row[1] for row in reader.execute("PRAGMA index_list(discovery_runs)")]
    finally:
        reader.close()
    assert runs == [
        (places_run, "places", "Leeds", "finished", 9, 4, 0, None, None),
        (registry_run, "companies_house", None, "deferred", 3, 1, 2, None, None),
    ]
    assert integrity == [("ok",)]
    assert columns[-2:] == ["scheduled_for", "ready_pool"]
    assert "ix_discovery_runs_started_at" in indexes

    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA foreign_keys = OFF")  # only the CHECKs are tested
        good = {
            "id": "01JD3QZ8X5V9K2M7N4P6R8T0F0",
            "source": "places",
            "campaign_id": campaign,
            "segment_id": segment,
            "country_code": "GB",
            "location": None,
            "query_hash": zeros,
            "status": "pool_sufficient",
            "started_at": at,
            "finished_at": at,
            "seen": 0,
            "created": 0,
            "merged": 0,
            "suppressed": 0,
            "no_website": 0,
            "market_blocked": 0,
            "inactive": 0,
            "scheduled_for": "2026-11-19",
            "ready_pool": 30,
        }

        def insert(**changes: object) -> None:
            row = {**good, **changes}
            names = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            writer.execute(
                f"INSERT INTO discovery_runs ({names}) VALUES ({marks})",  # noqa: S608
                tuple(row.values()),
            )

        bad: list[tuple[str, dict[str, object]]] = [
            ("status", {"status": "skipped", "location": "Leeds", "ready_pool": None}),
            ("location", {"location": "Leeds"}),  # a pool_sufficient row records no location
            ("scheduled_for", {"scheduled_for": None}),  # a pool_sufficient row is for a date
            ("ready_pool", {"ready_pool": None}),  # and records the pool
            ("ready_pool", {"ready_pool": -1}),
            # ready_pool only on pool_sufficient
            ("ready_pool", {"status": "finished", "location": "Leeds"}),
            ("ready_pool", {"status": "paced", "location": "Leeds", "scheduled_for": None}),
            ("location", {"status": "paced", "ready_pool": None}),  # a Places run has a location
            # a pool_sufficient Companies House row records no location either
            ("location", {"source": "companies_house", "location": "Leeds"}),
        ]
        for number, (check, changes) in enumerate(bad):
            with pytest.raises(sqlite3.IntegrityError, match=rf"ck_discovery_runs_{check}$"):
                insert(id=f"01JD3QZ8X5V9K2M7N4P6R8T1{number:02d}", **changes)
        insert()  # the pool_sufficient row is accepted
        for number, status in enumerate(("target_reached", "paced", "finished")):
            insert(
                id=f"01JD3QZ8X5V9K2M7N4P6R8T2{number:02d}",
                status=status,
                location="Leeds",
                ready_pool=None,
            )
        insert(
            id="01JD3QZ8X5V9K2M7N4P6R8T300",
            source="companies_house",
            status="target_reached",
            ready_pool=None,
            scheduled_for=None,
        )
        writer.commit()
    finally:
        writer.close()


def test_0062_keeps_every_crawl_run_as_initial_and_checks_the_mode(tmp_path: Path) -> None:
    """Story 8.7: a crawl run stored before 0062 keeps every value and reads ``initial``; the
    rebuild keeps the foreign keys to ``crawl_runs`` intact and refuses an unknown mode."""
    location = _chain_to(tmp_path, "0061_8_10_discovery_topup")
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0061_8_10_discovery_topup",
        )
    at = "2026-11-17T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0D0", "01JD3QZ8X5V9K2M7N4P6R8T0D1"
    run, page = "01JD3QZ8X5V9K2M7N4P6R8T0D2", "01JD3QZ8X5V9K2M7N4P6R8T0D3"
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'law_private_ai', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, created_at) "
        f"VALUES ('{lead}', 'Smith Law LLP', 'smith-law.com', '{segment}', '{at}')",
        "INSERT INTO crawl_runs (id, lead_id, site_domain, started_at, ended_at, outcome, "
        "pages_fetched, pages_skipped_robots, renders, errors, greenhouse_postings, board_errors) "
        f"VALUES ('{run}', '{lead}', 'smith-law.com', '{at}', '{at}', 'ok', 3, 1, 0, 0, 2, 0)",
        "INSERT INTO crawled_pages (id, lead_id, run_id, url, final_url, kind, fetched_at, "
        "http_status, content_hash, text, text_purge_after, changed_at) "
        f"VALUES ('{page}', '{lead}', '{run}', 'https://smith-law.com/', "
        f"'https://smith-law.com/', 'home', '{at}', 200, '{'0' * 64}', 'Home', '{at}', '{at}')",
    )

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0061_8_10_discovery_topup", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute(
            "SELECT id, pages_fetched, pages_skipped_robots, greenhouse_postings, board_errors, "
            "careers_postings, mode FROM crawl_runs"
        ).fetchall()
        broken = reader.execute("PRAGMA foreign_key_check").fetchall()
        indexes = [row[1] for row in reader.execute("PRAGMA index_list(crawl_runs)")]
    finally:
        reader.close()
    assert rows == [(run, 3, 1, 2, 0, None, "initial")]
    assert broken == []
    assert "ix_crawl_runs_lead_id" in indexes
    with pytest.raises(sqlite3.IntegrityError, match="ck_crawl_runs_mode"):
        run_sql(path, f"UPDATE crawl_runs SET mode = 'weekly' WHERE id = '{run}'")
    run_sql(path, f"UPDATE crawl_runs SET mode = 'recheck_jobs' WHERE id = '{run}'")


def test_0063_adds_the_nullable_pacing_columns_and_keeps_every_inbox(tmp_path: Path) -> None:
    """0063 (Story 6.11) adds the nullable pacing columns in place and keeps every inbox."""
    location = _chain_to(tmp_path, "0062_8_7_crawl_run_mode")
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0062_8_7_crawl_run_mode",
        )
    at = "2026-11-18T09:00:00.000000Z"
    domain, inbox = "01JD3QZ8X5V9K2M7N4P6R8T0E0", "01JD3QZ8X5V9K2M7N4P6R8T0E1"
    run_sql(
        path,
        f"INSERT INTO sending_domains (id, domain, created_at) VALUES ('{domain}', "
        f"'nextron-ai.com', '{at}')",
        "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, status, "
        f"created_at) VALUES ('{inbox}', 'furqaan@nextron-ai.com', 'Main', '{domain}', "
        f"'Furqaan Ahmed', 'active', '{at}')",
    )
    reader = sqlite3.connect(path)
    try:
        before = reader.execute("SELECT * FROM sending_inboxes").fetchall()
    finally:
        reader.close()

    with engine_on(path) as engine:
        assert ensure_current(engine) == ("0062_8_7_crawl_run_mode", HEAD)

    reader = sqlite3.connect(path)
    try:
        rows = reader.execute("SELECT * FROM sending_inboxes").fetchall()
        info = {row[1]: row for row in reader.execute("PRAGMA table_info(sending_inboxes)")}
        indexes = {row[1] for row in reader.execute("PRAGMA index_list(sending_inboxes)")}
    finally:
        reader.close()
    assert [row[: len(before[0])] for row in rows] == before  # every row and column kept
    assert {name: (info[name][2], info[name][3]) for name in ("next_send_at", "last_sent_at")} == {
        "next_send_at": ("VARCHAR(27)", 0),
        "last_sent_at": ("VARCHAR(27)", 0),
    }
    assert "uq_sending_inboxes_address" in indexes
    assert rows[0][len(before[0]) :][:2] == (None, None)
    run_sql(
        path,
        f"UPDATE sending_inboxes SET next_send_at = '{at}', last_sent_at = '{at}' "
        f"WHERE id = '{inbox}'",
    )
    reader = sqlite3.connect(path)
    try:
        stored = reader.execute(
            "SELECT next_send_at, last_sent_at FROM sending_inboxes WHERE id = ?", (inbox,)
        ).fetchone()
    finally:
        reader.close()
    assert stored == (at, at)


def test_0064_keeps_every_draft_and_adds_the_failure_columns(tmp_path: Path) -> None:
    """0064 (Story 4.15) rebuilds ``drafts`` with the failure columns, every row kept."""
    location = _chain_to(tmp_path, "0063_6_11_inbox_pacing")
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0063_6_11_inbox_pacing",
        )
    at, due = "2026-11-18T09:00:00.000000Z", "2026-11-19T14:00:00.000000Z"
    market, footer, revision_id = (
        "01JD3QZ8X5V9K2M7N4P6R8T0E1",
        "01JD3QZ8X5V9K2M7N4P6R8T0E2",
        "01JD3QZ8X5V9K2M7N4P6R8T0E3",
    )
    hex64 = "a" * 64
    # Non-step kinds only (as for 0045); the channel Draft has a revision, so its current_rev
    # (and that revision's footer snapshot and Market) must survive the post-upgrade key check
    run_sql(
        path,
        "INSERT INTO markets (id, code, name, enabled, country_code, cctlds, "
        "cold_email_allowed, free_webmail_blocked, default_timezone, unknown_timezone, "
        "english_variant, footer_template, privacy_line_required, version, created_at, "
        f"updated_at) VALUES ('{market}', 'GB', 'UK', 1, 'GB', '[\".uk\"]', 1, 1, "
        f"'Europe/London', 'Europe/London', 'en-GB', 'Footer', 1, 1, '{at}', '{at}')",
        "INSERT INTO footer_snapshots (id, market_id, sha256, text, created_at) VALUES "
        f"('{footer}', '{market}', '{hex64}', 'Footer', '{at}')",
        f"INSERT INTO drafts ({DRAFT_COLUMNS}) VALUES ('{CHANNEL_DRAFT}', 'channel', NULL, NULL, "
        f"NULL, NULL, '{due}', 1, 0, '{at}', '{at}', 'approved', 1, 0, NULL, '{at}')",
        f"INSERT INTO drafts ({DRAFT_COLUMNS}) VALUES ('{REPLY_DRAFT}', 'reply', NULL, NULL, "
        f"NULL, NULL, NULL, 0, 1, '{at}', '{at}', 'ready', 0, 1, '{at}', NULL)",
        "INSERT INTO draft_revisions (id, draft_id, rev, subject, body, footer_snapshot_id, "
        "evidence_ids, fact_versions, origin, text_hash, created_at) VALUES "
        f"('{revision_id}', '{CHANNEL_DRAFT}', 1, 'Hello', 'Body', '{footer}', '[]', '[]', "
        f"'generated', '{hex64}', '{at}')",
        f"UPDATE drafts SET current_rev = 1 WHERE id = '{CHANNEL_DRAFT}'",
    )
    before = draft_rows(path)
    assert {row[5] for row in before} == {1, None}
    shutil.copy2(
        SCRIPT_LOCATION / "versions" / "0064_4_15_draft_failed.py",
        location / "versions" / "0064_4_15_draft_failed.py",
    )

    with engine_on(path) as engine:  # the post-upgrade foreign key check passes
        assert ensure_current(engine, script_location=location) == (
            "0063_6_11_inbox_pacing",
            "0064_4_15_draft_failed",
        )

    assert draft_rows(path) == before  # every row and column unchanged, current_rev kept
    reader = sqlite3.connect(path)
    try:
        failures = reader.execute(
            "SELECT draft_failed_at, draft_failed_reason FROM drafts ORDER BY id"
        ).fetchall()
        [(index_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_drafts_enrollment_step'"
        ).fetchall()
        keys = reader.execute("PRAGMA foreign_key_list(drafts)").fetchall()
    finally:
        reader.close()
    assert failures == [(None, None), (None, None)]
    assert "WHERE kind IN ('step','recontact')" in index_sql
    assert {row[2] for row in keys} == {"enrollments", "draft_revisions"}
    for assignments in (
        f"draft_failed_at = '{at}'",
        "draft_failed_reason = 'refused'",
        f"draft_failed_at = '{at}', draft_failed_reason = 'other'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match=r"ck_drafts_draft_failed$"):
            run_sql(path, f"UPDATE drafts SET {assignments} WHERE id = '{REPLY_DRAFT}'")
    run_sql(
        path,
        f"UPDATE drafts SET draft_failed_at = '{at}', draft_failed_reason = 'refused' "
        f"WHERE id = '{REPLY_DRAFT}'",
    )


def test_0065_keeps_every_signal_inbox_item_and_adds_the_draft_columns(tmp_path: Path) -> None:
    """0065 (Story 4.16) rebuilds ``signal_inbox_items`` with ``draft_when_ready`` (every
    existing item ``false``), ``draft_outcome`` and ``draft_decided_at`` (both NULL or both
    set), every row, check, index and foreign key kept."""
    location = _chain_to(tmp_path, "0064_4_15_draft_failed")
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0064_4_15_draft_failed",
        )
    at = "2026-11-18T09:00:00.000000Z"
    segment, lead = "01JD3QZ8X5V9K2M7N4P6R8T0F0", "01JD3QZ8X5V9K2M7N4P6R8T0F1"
    processed, flagged = "01JD3QZ8X5V9K2M7N4P6R8T0F2", "01JD3QZ8X5V9K2M7N4P6R8T0F3"
    columns = (
        "id, url, text, author, company, status, flag_reason, submitted_at, processed_at, "
        "lead_id, parsed_segment, parsed_signal_type, parsed_website, parsed_author_name, "
        "parsed_author_role, parsed_event_date, parsed_quote, extracted_by"
    )
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{segment}', 'ops_automation', '{{}}', 1, '{at}', '{at}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, created_at) "
        f"VALUES ('{lead}', 'Northgate', 'northgate.com', '{segment}', '{at}')",
        f"INSERT INTO signal_inbox_items ({columns}) VALUES ('{processed}', "
        "'https://post.example/1', 'We rekey orders.', 'Dana', 'Northgate', 'processed', NULL, "
        f"'{at}', '{at}', '{lead}', 'ops_automation', 'ops_rekeying_post', 'northgate.com', "
        "'Dana Cole', 'COO', '2026-11-17', 'We rekey orders.', 'ai:signal_inbox_parse@v1')",
        f"INSERT INTO signal_inbox_items ({columns}) VALUES ('{flagged}', "
        "'https://post.example/2', 'Text', NULL, NULL, 'flagged', 'segment_unknown', "
        f"'{at}', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
    )
    reader = sqlite3.connect(path)
    try:
        before = reader.execute(f"SELECT {columns} FROM signal_inbox_items ORDER BY id").fetchall()
    finally:
        reader.close()

    with engine_on(path) as engine:  # the post-upgrade foreign key check passes
        assert ensure_current(engine) == ("0064_4_15_draft_failed", HEAD)

    reader = sqlite3.connect(path)
    try:
        after = reader.execute(f"SELECT {columns} FROM signal_inbox_items ORDER BY id").fetchall()
        drafts = reader.execute(
            "SELECT draft_when_ready, draft_outcome, draft_decided_at FROM signal_inbox_items "
            "ORDER BY id"
        ).fetchall()
        [(table_sql,)] = reader.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'signal_inbox_items'"
        ).fetchall()
        indexes = {
            row[1] for row in reader.execute("PRAGMA index_list(signal_inbox_items)").fetchall()
        }
        keys = reader.execute("PRAGMA foreign_key_list(signal_inbox_items)").fetchall()
    finally:
        reader.close()
    assert after == before  # every row and column unchanged
    assert drafts == [(0, None, None), (0, None, None)]
    for check in ("status", "flag_reason", "processed", "draft_decided"):
        assert f"ck_signal_inbox_items_{check}" in table_sql
    assert {
        "ix_signal_inbox_items_status_submitted_at",
        "ix_signal_inbox_items_lead_id",
    } <= indexes
    assert [(row[2], row[3], row[4]) for row in keys] == [("leads", "lead_id", "id")]
    for assignments in (
        "draft_outcome = 'enrolled'",
        f"draft_decided_at = '{at}'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match=r"ck_signal_inbox_items_draft_decided$"):
            run_sql(path, f"UPDATE signal_inbox_items SET {assignments} WHERE id = '{processed}'")
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        run_sql(
            path,
            f"UPDATE signal_inbox_items SET draft_when_ready = NULL WHERE id = '{processed}'",
        )
    run_sql(
        path,
        "UPDATE signal_inbox_items SET draft_when_ready = 1, draft_outcome = 'over_capacity', "
        f"draft_decided_at = '{at}' WHERE id = '{processed}'",
    )
