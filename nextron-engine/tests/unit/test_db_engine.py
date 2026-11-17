"""``nextron.db.engine``: the six connection settings, the write lock, the transaction-SQL guard,
the foreign-keys-off connection, the read-only connection and the read-only engine."""

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from nextron.db import engine as engine_module
from nextron.db.engine import (
    BEGIN_SQL,
    ConnectionSettingError,
    ReadOnlySqlError,
    TransactionSqlError,
    _on_begin,
    _on_connect,
    _refuse_transaction_sql,
    create_engine,
    create_read_only_engine,
    fk_off_connection,
    leading_keyword,
    read_only_connection,
)


def db_path(engine: Engine) -> str:
    assert engine.url.database
    return engine.url.database


def raw(engine: Engine, **kwargs: object) -> sqlite3.Connection:
    """A connection outside the engine, as another process would open one."""
    return sqlite3.connect(db_path(engine), **kwargs)  # type: ignore[arg-type]


def pragma(connection: sqlite3.Connection, name: str) -> object:
    return connection.execute(f"PRAGMA {name}").fetchone()[0]


@pytest.fixture
def statements(db_engine: Engine) -> Iterator[list[str]]:
    """Every statement the engine sends from now on."""
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        seen.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    yield seen
    event.remove(db_engine, "before_cursor_execute", record)


@pytest.fixture
def scratch(db_engine: Engine) -> Engine:
    with db_engine.connect() as connection:
        connection.exec_driver_sql("CREATE TABLE scratch (id INTEGER PRIMARY KEY, note TEXT)")
        connection.commit()
    return db_engine


def notes(engine: Engine) -> list[str]:
    reader = raw(engine)
    try:
        return [note for (note,) in reader.execute("SELECT note FROM scratch ORDER BY id")]
    finally:
        reader.close()


# Row: connection settings -----------------------------------------------------------------------


def test_every_connection_gets_the_six_settings(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        dbapi = connection.connection.driver_connection
        assert isinstance(dbapi, sqlite3.Connection)

        assert pragma(dbapi, "journal_mode") == "wal"
        assert pragma(dbapi, "busy_timeout") == 5000
        assert pragma(dbapi, "synchronous") == 1  # NORMAL
        assert pragma(dbapi, "foreign_keys") == 1
        assert dbapi.isolation_level is None
        assert dbapi.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL


def test_begin_emits_begin_immediate_for_every_transaction_reads_included(
    db_engine: Engine, statements: list[str]
) -> None:
    with db_engine.connect() as connection:
        connection.begin()
        assert statements == [BEGIN_SQL]
        connection.rollback()
        connection.execute(text("SELECT 1"))  # autobegin

    assert statements == [BEGIN_SQL, BEGIN_SQL, "SELECT 1"]


def test_an_idle_begin_holds_the_write_lock(db_engine: Engine) -> None:
    other = raw(db_engine, timeout=0, isolation_level=None)
    try:
        with db_engine.connect() as connection:
            connection.begin()  # no statement yet

            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
            assert other.execute("SELECT count(*) FROM events").fetchone() == (0,)  # WAL read

        other.execute("BEGIN IMMEDIATE")  # free again once the transaction ends
        other.execute("ROLLBACK")
    finally:
        other.close()


class FakeCursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.row = row

    def fetchone(self) -> tuple[object, ...]:
        return self.row


class FakeConnection:
    """Answers the connect hook's PRAGMAs and records them with the isolation level."""

    def __init__(self, journal_mode: str, *, page_count: int = 0, switched: str = "wal") -> None:
        self.answers = {
            "PRAGMA journal_mode": journal_mode,
            "PRAGMA page_count": page_count,
            "PRAGMA journal_mode=WAL": switched,
        }
        self.isolation_level: str | None = ""
        self.log: list[str] = []

    def execute(self, sql: str) -> FakeCursor:
        self.log.append(f"{sql} (isolation_level={self.isolation_level!r})")
        return FakeCursor((self.answers.get(sql),))


SETTINGS = [
    "PRAGMA busy_timeout=5000 (isolation_level=None)",
    "PRAGMA synchronous=NORMAL (isolation_level=None)",
    "PRAGMA foreign_keys=ON (isolation_level=None)",
]
READ_MODE = "PRAGMA journal_mode (isolation_level=None)"
READ_PAGES = "PRAGMA page_count (isolation_level=None)"
SWITCH = "PRAGMA journal_mode=WAL (isolation_level=None)"


def test_the_hooks_are_registered_on_the_engine() -> None:
    engine = create_engine(Path("unused.db"))  # never connects

    assert event.contains(engine, "connect", _on_connect)
    assert event.contains(engine, "begin", _on_begin)
    assert event.contains(engine, "before_cursor_execute", _refuse_transaction_sql)


@pytest.mark.parametrize(
    ("connection", "log"),
    [
        (FakeConnection("wal"), [READ_MODE, *SETTINGS]),  # this engine's file
        (FakeConnection("delete", page_count=0), [READ_MODE, READ_PAGES, SWITCH, *SETTINGS]),
    ],
    ids=["wal", "empty file"],
)
def test_the_connect_hook_sets_the_settings_in_order(
    connection: FakeConnection, log: list[str]
) -> None:
    _on_connect(connection, None)  # type: ignore[arg-type]

    assert connection.isolation_level is None
    assert connection.log == log


def test_the_connect_hook_refuses_a_non_empty_file_in_another_journal_mode() -> None:
    connection = FakeConnection("delete", page_count=3)

    with pytest.raises(ConnectionSettingError, match="journal_mode delete, not wal"):
        _on_connect(connection, None)  # type: ignore[arg-type]

    assert connection.log == [READ_MODE, READ_PAGES]  # nothing switched


def test_the_connect_hook_fails_when_wal_does_not_take() -> None:
    connection = FakeConnection("delete", switched="delete")

    with pytest.raises(ConnectionSettingError, match="journal_mode is delete, not wal"):
        _on_connect(connection, None)  # type: ignore[arg-type]

    assert connection.log == [READ_MODE, READ_PAGES, SWITCH]


def test_a_real_rollback_journal_file_is_refused_untouched(tmp_path: Path) -> None:
    path = tmp_path / "foreign.db"
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY)")
    writer.commit()
    writer.close()
    before = path.read_bytes()
    engine = create_engine(path)
    try:
        with pytest.raises(ConnectionSettingError, match="not this engine's database"):
            engine.connect()
    finally:
        engine.dispose()

    assert path.read_bytes() == before


@pytest.mark.parametrize("path", [Path(":memory:"), Path("")])
def test_create_engine_opens_database_files_only(path: Path) -> None:
    with pytest.raises(ValueError, match="database file"):
        create_engine(path)


def test_create_engine_takes_a_path() -> None:
    with pytest.raises(TypeError):
        create_engine("nextron.db")  # type: ignore[arg-type]


def test_bound_values_stay_out_of_error_messages(scratch: Engine) -> None:
    secret = "ann@example.com"
    with scratch.connect() as connection:
        connection.exec_driver_sql("INSERT INTO scratch (id, note) VALUES (1, 'x')")
        with pytest.raises(IntegrityError) as raised:
            connection.execute(
                text("INSERT INTO scratch (id, note) VALUES (1, :note)"), {"note": secret}
            )

    assert "UNIQUE" in str(raised.value)
    assert secret not in str(raised.value)


# Row: raw transaction SQL ---------------------------------------------------------------------

REFUSED = [
    "COMMIT",
    "commit",
    "  COMMIT;",
    "/* x */ rollback",
    "-- a comment\nROLLBACK",
    "/* a */ -- b\n /* c */ END TRANSACTION",
    "END",
    "BEGIN",
    "begin deferred",
    "BEGIN IMMEDIATE",
    "SAVEPOINT a",
    "RELEASE a",
    "release savepoint a",
    "\n\tRollBack TO a",
    "; COMMIT",
    "/* x */ ; ROLLBACK",
]


@pytest.mark.parametrize("sql", REFUSED)
def test_transaction_sql_is_refused_and_changes_nothing(sql: str, scratch: Engine) -> None:
    with scratch.connect() as connection:
        connection.begin()
        connection.exec_driver_sql("INSERT INTO scratch (note) VALUES ('pending')")

        with pytest.raises(TransactionSqlError, match="refused"):
            connection.exec_driver_sql(sql)

        assert connection.in_transaction()
        assert connection.connection.driver_connection.in_transaction  # type: ignore[union-attr]
        assert notes(scratch) == []  # nothing committed by it
        connection.commit()  # and nothing rolled back by it

    assert notes(scratch) == ["pending"]


@pytest.mark.parametrize("sql", ["COMMIT", "/* x */ rollback", "SAVEPOINT a"])
def test_transaction_sql_through_a_session_is_refused(sql: str, scratch: Engine) -> None:
    with scratch.connect() as connection, Session(bind=connection) as session:
        with pytest.raises(TransactionSqlError):
            session.execute(text(sql))


def test_a_savepoint_is_refused(scratch: Engine) -> None:
    with scratch.connect() as connection:
        connection.begin()
        with pytest.raises(TransactionSqlError, match="SAVEPOINT"):
            connection.begin_nested()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 -- COMMIT",
        "/* BEGIN */ SELECT 2",
        "SELECT 'COMMIT'",
        "CREATE TRIGGER t AFTER INSERT ON scratch BEGIN SELECT 1; END",
        "WITH x AS (SELECT 1) SELECT * FROM x",
    ],
)
def test_other_sql_runs(sql: str, scratch: Engine) -> None:
    with scratch.connect() as connection:
        connection.exec_driver_sql(sql)
        connection.rollback()


@pytest.mark.parametrize(
    ("sql", "keyword"),
    [
        ("", ""),
        ("   ", ""),
        ("/* unterminated COMMIT", ""),
        ("-- only a comment", ""),
        ("--\nselect 1", "SELECT"),
        ("/**/commit", "COMMIT"),
        ("RELEASE_x", "RELEASE_X"),
        ("savepoint\ta", "SAVEPOINT"),
        (";;  commit", "COMMIT"),
        ("; /* x */ ;\n-- y\n; end", "END"),
        (";", ""),
    ],
)
def test_leading_keyword(sql: str, keyword: str) -> None:
    assert leading_keyword(sql) == keyword


def test_the_keyword_scan_is_linear_on_long_whitespace() -> None:
    assert leading_keyword(" " * 1_000_000 + "x") == "X"


# The foreign-keys-off connection --------------------------------------------------------------


def test_fk_off_connection_turns_foreign_keys_back_on_for_the_pool(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "fk.db")
    try:
        with fk_off_connection(engine) as connection:
            dbapi = connection.connection.driver_connection
            assert isinstance(dbapi, sqlite3.Connection)
            assert pragma(dbapi, "foreign_keys") == 0
            connection.begin()
            connection.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            # left open: the context manager rolls it back

        with engine.connect() as again:
            assert again.connection.driver_connection is dbapi  # the same pooled connection
            assert pragma(dbapi, "foreign_keys") == 1
            assert again.exec_driver_sql("SELECT count(*) FROM sqlite_master").scalar() == 0
    finally:
        engine.dispose()


def test_fk_off_connection_invalidates_a_connection_it_cannot_restore(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = create_engine(tmp_path / "fk.db")
    try:
        with fk_off_connection(engine) as connection:
            dbapi = connection.connection.driver_connection
            assert isinstance(dbapi, sqlite3.Connection)
            dbapi.execute("BEGIN")  # a transaction SQLAlchemy can't see: PRAGMAs are no-ops now

        assert "foreign_keys_restore_failed" in caplog.messages
        with pytest.raises(sqlite3.ProgrammingError):  # closed, never back in the pool
            dbapi.execute("SELECT 1")
        with engine.connect() as again:
            fresh = again.connection.driver_connection
            assert fresh is not dbapi
            assert pragma(fresh, "foreign_keys") == 1  # type: ignore[arg-type]
    finally:
        engine.dispose()


def test_fk_off_connection_restores_after_an_exception(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "fk.db")
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with fk_off_connection(engine) as connection:
                dbapi = connection.connection.driver_connection
                connection.begin()
                raise RuntimeError("boom")

        with engine.connect() as again:
            assert again.connection.driver_connection is dbapi
            assert pragma(dbapi, "foreign_keys") == 1  # type: ignore[arg-type]
    finally:
        engine.dispose()


def test_no_warning_is_logged_when_restore_works(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = create_engine(tmp_path / "fk.db")
    caplog.set_level(logging.WARNING)
    try:
        with fk_off_connection(engine):
            pass
    finally:
        engine.dispose()

    assert caplog.messages == []


# The read-only connection -----------------------------------------------------------------------


def test_read_only_connection_begins_immediate_then_turns_query_only_on(
    scratch: Engine, statements: list[str]
) -> None:
    with read_only_connection(scratch) as connection:
        dbapi = connection.connection.driver_connection
        assert isinstance(dbapi, sqlite3.Connection)

        assert statements == [BEGIN_SQL]  # the PRAGMAs go straight to the driver
        assert connection.in_transaction() and dbapi.in_transaction
        assert pragma(dbapi, "query_only") == 1


def test_read_only_connection_holds_the_write_lock_until_exit(scratch: Engine) -> None:
    other = raw(scratch, timeout=0, isolation_level=None)
    try:
        with read_only_connection(scratch):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")

        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
    finally:
        other.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT note FROM scratch",
        "  select 1",
        "/* x */ SELECT 1",
        "WITH x AS (SELECT 1) SELECT * FROM x",
    ],
)
def test_read_only_connection_runs_reads(sql: str, scratch: Engine) -> None:
    with read_only_connection(scratch) as connection:
        connection.exec_driver_sql(sql).fetchall()


READ_ONLY_REFUSED = [
    "INSERT INTO scratch (note) VALUES ('x')",
    "insert into scratch (note) values ('x')",
    "/* x */ REPLACE INTO scratch (note) VALUES ('x')",
    "UPDATE scratch SET note = 'x'",
    "DELETE FROM scratch",
    "PRAGMA query_only=OFF",
    "pragma query_only = 0",
    "PRAGMA query_only",
    "CREATE TABLE other (id INTEGER)",
    "DROP TABLE scratch",
    "ATTACH DATABASE ':memory:' AS other",
    "VACUUM",
    "ANALYZE",
    "REINDEX",
    "EXPLAIN SELECT 1",
    "VALUES (1)",
    "",
]


@pytest.mark.parametrize("sql", READ_ONLY_REFUSED)
def test_read_only_connection_refuses_every_other_statement(sql: str, scratch: Engine) -> None:
    with read_only_connection(scratch) as connection:
        dbapi = connection.connection.driver_connection
        assert isinstance(dbapi, sqlite3.Connection)

        with pytest.raises(ReadOnlySqlError, match="read-only"):
            connection.exec_driver_sql(sql)

        assert pragma(dbapi, "query_only") == 1  # PRAGMA query_only=OFF never ran
        assert connection.in_transaction()

    assert notes(scratch) == []


def test_read_only_connection_refuses_writes_through_a_session(scratch: Engine) -> None:
    with read_only_connection(scratch) as connection, Session(bind=connection) as session:
        with pytest.raises(ReadOnlySqlError):
            session.execute(text("INSERT INTO scratch (note) VALUES ('x')"))
        with pytest.raises(TransactionSqlError):  # transaction SQL is still its own error
            session.execute(text("COMMIT"))

    assert notes(scratch) == []


def test_sqlite_itself_refuses_a_write_the_keyword_check_lets_through(scratch: Engine) -> None:
    sql = "WITH x AS (SELECT 1) INSERT INTO scratch (note) SELECT 'x' FROM x"
    with read_only_connection(scratch) as connection:
        with pytest.raises(OperationalError, match="readonly database"):
            connection.exec_driver_sql(sql)

    assert notes(scratch) == []


@pytest.mark.parametrize("raises", [False, True])
def test_read_only_connection_returns_a_writable_connection_to_the_pool(
    raises: bool, scratch: Engine
) -> None:
    lent: list[sqlite3.Connection] = []

    def use() -> None:
        with read_only_connection(scratch) as connection:
            dbapi = connection.connection.driver_connection
            assert isinstance(dbapi, sqlite3.Connection)
            lent.append(dbapi)
            with pytest.raises(ReadOnlySqlError):
                connection.exec_driver_sql("INSERT INTO scratch (note) VALUES ('refused')")
            if raises:
                raise RuntimeError("boom")

    if raises:
        with pytest.raises(RuntimeError, match="boom"):
            use()
    else:
        use()

    [dbapi] = lent
    with scratch.connect() as again:
        assert again.connection.driver_connection is dbapi  # the same pooled connection
        assert pragma(dbapi, "query_only") == 0
        assert not dbapi.in_transaction
        again.exec_driver_sql("INSERT INTO scratch (note) VALUES ('written')")
        again.commit()

    assert notes(scratch) == ["written"]


def test_read_only_connection_invalidates_a_connection_it_cannot_restore(
    scratch: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    with read_only_connection(scratch) as connection:
        dbapi = connection.connection.driver_connection
        assert isinstance(dbapi, sqlite3.Connection)
        dbapi.close()  # the connection broke: neither the rollback nor the PRAGMA can run

    assert "query_only_restore_failed" in caplog.messages
    with scratch.connect() as again:
        fresh = again.connection.driver_connection
        assert fresh is not dbapi
        assert pragma(fresh, "query_only") == 0  # type: ignore[arg-type]
        again.exec_driver_sql("INSERT INTO scratch (note) VALUES ('written')")
        again.commit()

    assert notes(scratch) == ["written"]


def test_a_base_exception_during_the_restore_still_invalidates_the_connection(
    scratch: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = engine_module._set_query_only

    def interrupted(dbapi_connection: sqlite3.Connection, on: bool) -> None:
        if not on:
            raise KeyboardInterrupt
        real(dbapi_connection, on)

    monkeypatch.setattr(engine_module, "_set_query_only", interrupted)
    with pytest.raises(KeyboardInterrupt):
        with read_only_connection(scratch) as connection:
            dbapi = connection.connection.driver_connection
            assert isinstance(dbapi, sqlite3.Connection)

    with pytest.raises(sqlite3.ProgrammingError):  # closed, never back in the pool
        dbapi.execute("SELECT 1")
    with scratch.connect() as again:
        fresh = again.connection.driver_connection
        assert fresh is not dbapi
        assert pragma(fresh, "query_only") == 0  # type: ignore[arg-type]


def test_a_begin_that_times_out_leaves_query_only_off(
    scratch: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_module, "BUSY_TIMEOUT_MS", 50)  # read by each new connection
    engine = create_engine(Path(db_path(scratch)))
    blocker = raw(scratch, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # another process holds the write lock
    try:
        with pytest.raises(OperationalError, match="locked"):
            with read_only_connection(engine):
                pass
        with engine.connect() as again:
            dbapi = again.connection.driver_connection
            assert pragma(dbapi, "query_only") == 0  # type: ignore[arg-type]
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        engine.dispose()


# The read-only engine (nextron doctor) ----------------------------------------------------------


@pytest.fixture
def at_rest(scratch: Engine) -> Path:
    """The ``scratch`` database file with one note, every engine connection closed (so its WAL
    is checkpointed into the file); returns its path."""
    with scratch.connect() as connection:
        connection.exec_driver_sql("INSERT INTO scratch (note) VALUES ('kept')")
        connection.commit()
    scratch.dispose()
    return Path(db_path(scratch))


def file_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_the_read_only_engine_reads_with_its_settings(at_rest: Path) -> None:
    engine = create_read_only_engine(at_rest)
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with engine.connect() as connection:
            dbapi = connection.connection.driver_connection
            assert isinstance(dbapi, sqlite3.Connection)
            assert connection.exec_driver_sql("SELECT note FROM scratch").scalars().all() == [
                "kept"
            ]
            assert pragma(dbapi, "journal_mode") == "wal"
            assert pragma(dbapi, "busy_timeout") == 5000
            assert dbapi.isolation_level is None
            assert dbapi.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
    finally:
        engine.dispose()

    assert seen[:2] == [BEGIN_SQL, "SELECT note FROM scratch"]  # AD-2's begin, reads included
    assert isinstance(engine.pool, NullPool)
    assert engine.hide_parameters
    assert engine.url.database == str(at_rest)
    assert event.contains(engine, "begin", _on_begin)
    assert event.contains(engine, "before_cursor_execute", _refuse_transaction_sql)


def test_a_missing_file_is_never_created(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    engine = create_read_only_engine(folder / "nextron.db")
    try:
        with pytest.raises(OperationalError, match="unable to open database file"):
            engine.connect()
    finally:
        engine.dispose()

    assert list(folder.iterdir()) == []


def test_reads_work_while_another_connection_holds_an_open_read(at_rest: Path) -> None:
    other = sqlite3.connect(at_rest, isolation_level=None)
    engine = create_read_only_engine(at_rest)
    try:
        other.execute("BEGIN")
        assert other.execute("SELECT count(*) FROM scratch").fetchone() == (1,)  # holds a read
        with engine.connect() as connection:
            notes_read = connection.exec_driver_sql("SELECT note FROM scratch").scalars().all()
        other.execute("ROLLBACK")
    finally:
        engine.dispose()
        other.close()

    assert notes_read == ["kept"]


def test_reads_work_while_the_engine_holds_the_write_lock(at_rest: Path) -> None:
    writer = sqlite3.connect(at_rest, isolation_level=None)
    engine = create_read_only_engine(at_rest)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO scratch (note) VALUES ('uncommitted')")
        with engine.connect() as connection:
            notes_read = connection.exec_driver_sql("SELECT note FROM scratch").scalars().all()
        writer.execute("ROLLBACK")
    finally:
        engine.dispose()
        writer.close()

    assert notes_read == ["kept"]  # a read transaction: no wait, and no uncommitted row


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO scratch (note) VALUES ('written')",
        "UPDATE scratch SET note = 'changed'",
        "DELETE FROM scratch",
        "CREATE TABLE more (id INTEGER)",
        "PRAGMA user_version = 7",
    ],
    ids=["insert", "update", "delete", "create", "pragma"],
)
def test_a_write_is_refused_and_the_file_is_unchanged(at_rest: Path, sql: str) -> None:
    before = file_bytes(at_rest)
    engine = create_read_only_engine(at_rest)
    try:
        with engine.connect() as connection:
            with pytest.raises(OperationalError, match="readonly database"):
                connection.exec_driver_sql(sql)
                connection.commit()
    finally:
        engine.dispose()

    assert file_bytes(at_rest) == before
    reader = sqlite3.connect(at_rest)
    try:
        assert reader.execute("SELECT note FROM scratch").fetchall() == [("kept",)]
        assert reader.execute("PRAGMA user_version").fetchone() == (0,)
    finally:
        reader.close()


def test_the_transaction_guard_applies(at_rest: Path) -> None:
    engine = create_read_only_engine(at_rest)
    try:
        with engine.connect() as connection, pytest.raises(TransactionSqlError):
            connection.exec_driver_sql("COMMIT")
    finally:
        engine.dispose()


def test_a_read_only_unit_of_work_reads_through_it(at_rest: Path) -> None:
    engine = create_read_only_engine(at_rest)
    try:
        with read_only_connection(engine) as connection:
            assert connection.exec_driver_sql("SELECT note FROM scratch").scalars().all() == [
                "kept"
            ]
            with pytest.raises(ReadOnlySqlError):
                connection.exec_driver_sql("DELETE FROM scratch")
    finally:
        engine.dispose()


def test_a_non_wal_file_is_refused_untouched_by_the_read_only_engine(tmp_path: Path) -> None:
    path = tmp_path / "foreign.db"
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY)")
    writer.commit()
    writer.close()
    before = path.read_bytes()
    engine = create_read_only_engine(path)
    try:
        with pytest.raises(ConnectionSettingError, match="journal_mode delete, not wal"):
            engine.connect()
    finally:
        engine.dispose()

    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir() if item.name.startswith("foreign")) == [
        "foreign.db"
    ]


def test_an_empty_file_is_read_and_never_switched_to_wal(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    path.write_bytes(b"")
    engine = create_read_only_engine(path)
    try:
        with engine.connect() as connection:
            tables = connection.exec_driver_sql("SELECT name FROM sqlite_master").all()
            dbapi = connection.connection.driver_connection
            assert isinstance(dbapi, sqlite3.Connection)
            assert pragma(dbapi, "journal_mode") == "delete"
    finally:
        engine.dispose()

    assert tables == []
    assert path.read_bytes() == b""


def test_a_path_with_spaces_and_a_hash_opens(tmp_path: Path) -> None:
    folder = tmp_path / "Nextron AI #1 (dev)"
    folder.mkdir()
    path = folder / "nextron.db"
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    writer.close()
    engine = create_read_only_engine(path)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM t").scalar() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("path", [Path(":memory:"), Path("")])
def test_create_read_only_engine_opens_database_files_only(path: Path) -> None:
    with pytest.raises(ValueError, match="database file"):
        create_read_only_engine(path)


def test_create_read_only_engine_takes_a_path() -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        create_read_only_engine("nextron.db")  # type: ignore[arg-type]
