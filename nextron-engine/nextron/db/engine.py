"""The one SQLite connection configurator (AD-2).

No other module under ``nextron/`` creates an SQLAlchemy engine or calls ``sqlite3.connect``;
``tests/contracts/test_persistence_contracts.py`` checks the source.

``create_engine(db_path)`` opens a file database with pysqlite and passes
``autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL`` explicitly, so a future Python default can't
change how transactions start. Three events then take over transaction control:

- ``connect`` (each new pooled connection): ``isolation_level = None`` so sqlite3 never emits
  BEGIN itself, then WAL: a file already in WAL mode stays so, an empty file is switched to
  ``journal_mode=WAL`` (anything but ``wal`` fails the connection), and a non-empty file in
  another journal mode fails the connection untouched (``ConnectionSettingError``: it is not
  this engine's database). Then ``busy_timeout=5000``, ``synchronous=NORMAL`` and
  ``foreign_keys=ON``.
- ``begin``: emits ``BEGIN IMMEDIATE`` for every transaction, reads included, so a transaction
  holds the write lock from its start and never fails later on lock upgrade. Keep transactions
  short; sqlite3 still issues COMMIT and ROLLBACK.
- ``before_cursor_execute``: any other transaction-control statement sent through SQLAlchemy
  (``BEGIN``, ``COMMIT``, ``END``, ``ROLLBACK``, ``SAVEPOINT`` or ``RELEASE`` after any leading
  whitespace, comments and semicolons) raises ``TransactionSqlError`` before it runs.
  Transactions belong to the unit of work (``nextron.db.uow``); this also refuses
  ``Session.begin_nested()``.

``create_read_only_engine(db_path)`` is ``nextron doctor``'s engine: it never creates, migrates or
writes the file. Each connection is ``sqlite3.connect(<file URI>?mode=ro, uri=True,
autocommit=LEGACY_TRANSACTION_CONTROL)`` (SQLite refuses every write on it), with ``NullPool``
and ``hide_parameters=True``. Its ``connect`` event sets ``isolation_level = None``, requires
``journal_mode`` ``wal`` or an empty file (``page_count`` 0; it never switches the mode), else
``ConnectionSettingError``, and sets ``busy_timeout``. The ``begin`` and
``before_cursor_execute`` events above apply too: on a read-only file ``BEGIN IMMEDIATE`` opens
a read transaction, so a read doesn't wait for another connection's writes. A missing file
raises on connect and is never created. Reading a WAL file may leave empty ``-wal`` and ``-shm``
files next to it; the engine reuses them, and SQLite deletes them when the engine's last
connection closes at a clean stop.

``fk_off_connection(engine)`` is the one place that turns ``foreign_keys`` off: the startup
migration (``nextron.db.migrations``) runs on it, following Alembic's SQLite batch guidance.

``read_only_connection(engine)`` is the one place that sets ``query_only``: the read-only unit of
work (``nextron.db.uow``, every dashboard GET) reads through it. It begins the transaction first
(``BEGIN IMMEDIATE`` fails under ``query_only``), then sets ``PRAGMA query_only=ON`` and reads it
back. While the connection is lent, ``before_cursor_execute`` also refuses, with
``ReadOnlySqlError``, any statement whose leading keyword is not ``SELECT`` or ``WITH``, which
closes ``PRAGMA query_only=OFF``. On exit it rolls back and turns ``query_only`` off again, so
the pooled connection writes normally for the next user; if that fails, the connection is
invalidated.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import sqlalchemy
from sqlalchemy import URL, Connection, Engine, event
from sqlalchemy.pool import NullPool

BUSY_TIMEOUT_MS: Final = 5000
BEGIN_SQL: Final = "BEGIN IMMEDIATE"
TRANSACTION_KEYWORDS: Final = frozenset(
    {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
)
READ_KEYWORDS: Final = frozenset({"SELECT", "WITH"})  # all a read-only connection may send
_BEGINNING: Final = "nextron_beginning"  # Connection.info flag set while the begin event runs
_READ_ONLY: Final = "nextron_read_only"  # Connection.info flag set while query_only is on

log = logging.getLogger(__name__)


class TransactionSqlError(RuntimeError):
    """Transaction-control SQL was sent through SQLAlchemy; the unit of work owns transactions."""


class ConnectionSettingError(RuntimeError):
    """A connection setting did not take effect."""


class ReadOnlySqlError(RuntimeError):
    """A statement other than ``SELECT`` or ``WITH`` was sent on a read-only connection."""


def leading_keyword(sql: str) -> str:
    """The first word of ``sql`` in uppercase, after any whitespace, SQL comments and ``;``."""
    i, n = 0, len(sql)
    while i < n:
        if sql[i].isspace() or sql[i] == ";":  # SQLite skips empty statements
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            break
    start = i
    while i < n and (sql[i].isalpha() or sql[i] == "_"):
        i += 1
    return sql[start:i].upper()


def _on_connect(dbapi_connection: sqlite3.Connection, connection_record: Any) -> None:
    dbapi_connection.isolation_level = None
    [mode] = dbapi_connection.execute("PRAGMA journal_mode").fetchone()
    if str(mode).lower() != "wal":
        [pages] = dbapi_connection.execute("PRAGMA page_count").fetchone()
        if pages != 0:  # switching would rewrite the header of a file this engine didn't make
            raise ConnectionSettingError(
                f"the database file uses journal_mode {mode}, not wal; "
                "it is not this engine's database"
            )
        [mode] = dbapi_connection.execute("PRAGMA journal_mode=WAL").fetchone()
        if str(mode).lower() != "wal":
            raise ConnectionSettingError(f"journal_mode is {mode}, not wal")
    dbapi_connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def _on_begin(connection: Connection) -> None:
    connection.info[_BEGINNING] = True
    try:
        connection.exec_driver_sql(BEGIN_SQL)
    finally:
        connection.info.pop(_BEGINNING, None)


def _refuse_transaction_sql(
    connection: Connection,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    if connection.info.get(_BEGINNING) and statement == BEGIN_SQL:
        return
    keyword = leading_keyword(statement)
    if keyword in TRANSACTION_KEYWORDS:
        raise TransactionSqlError(
            f"{keyword} refused: transactions are opened and ended only by the unit of work"
        )
    if connection.info.get(_READ_ONLY) and keyword not in READ_KEYWORDS:
        raise ReadOnlySqlError(
            f"{keyword or 'the statement'} refused: this connection is read-only "
            "(a dashboard GET reads; a POST writes)"
        )


def create_engine(db_path: Path) -> Engine:
    """The engine for the SQLite file ``db_path``; file databases only."""
    if not isinstance(db_path, Path):
        raise TypeError("db_path must be a pathlib.Path to a database file")
    if db_path.name in ("", ".", "..", ":memory:"):
        raise ValueError("create_engine opens a database file, not an in-memory database")
    engine = sqlalchemy.create_engine(
        URL.create("sqlite+pysqlite", database=str(db_path)),
        connect_args={"autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL},
        hide_parameters=True,  # bound values can hold personal data; errors and logs show none
    )
    event.listen(engine, "connect", _on_connect)
    event.listen(engine, "begin", _on_begin)
    event.listen(engine, "before_cursor_execute", _refuse_transaction_sql)
    return engine


def _on_read_only_connect(dbapi_connection: sqlite3.Connection, connection_record: Any) -> None:
    dbapi_connection.isolation_level = None
    [mode] = dbapi_connection.execute("PRAGMA journal_mode").fetchone()
    if str(mode).lower() != "wal":
        [pages] = dbapi_connection.execute("PRAGMA page_count").fetchone()
        if pages != 0:
            raise ConnectionSettingError(
                f"the database file uses journal_mode {mode}, not wal; "
                "it is not this engine's database"
            )
    dbapi_connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")


def create_read_only_engine(db_path: Path) -> Engine:
    """A read-only engine on the SQLite file ``db_path``, which must exist; see the module
    docstring. Nothing it does creates or changes the file."""
    if not isinstance(db_path, Path):
        raise TypeError("db_path must be a pathlib.Path to a database file")
    if db_path.name in ("", ".", "..", ":memory:"):
        raise ValueError("create_read_only_engine opens a database file, not an in-memory one")
    uri = db_path.absolute().as_uri() + "?mode=ro"

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True, autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL)

    engine = sqlalchemy.create_engine(
        URL.create("sqlite+pysqlite", database=str(db_path)),
        creator=connect,
        poolclass=NullPool,
        hide_parameters=True,  # bound values can hold personal data; errors and logs show none
    )
    event.listen(engine, "connect", _on_read_only_connect)
    event.listen(engine, "begin", _on_begin)
    event.listen(engine, "before_cursor_execute", _refuse_transaction_sql)
    return engine


def _set_foreign_keys(dbapi_connection: sqlite3.Connection, on: bool) -> None:
    """Set ``foreign_keys`` and read it back; the PRAGMA is a no-op inside a transaction."""
    dbapi_connection.execute(f"PRAGMA foreign_keys={'ON' if on else 'OFF'}")
    [value] = dbapi_connection.execute("PRAGMA foreign_keys").fetchone()
    if value != int(on):
        raise ConnectionSettingError(f"foreign_keys stayed {value}")


@contextmanager
def fk_off_connection(engine: Engine) -> Iterator[Connection]:
    """A connection with ``foreign_keys`` off, for the startup migration only.

    The caller begins and ends its own transaction on it. On exit any open transaction is
    rolled back and ``foreign_keys`` is turned back on before the connection returns to the
    pool; if that fails, the connection is invalidated, so the pool never hands it out again.
    """
    with engine.connect() as connection:
        dbapi_connection = connection.connection.driver_connection
        assert isinstance(dbapi_connection, sqlite3.Connection)
        _set_foreign_keys(dbapi_connection, False)
        try:
            yield connection
        finally:
            restored = False
            try:
                if connection.in_transaction():
                    connection.rollback()
                _set_foreign_keys(dbapi_connection, True)
                restored = True
            except Exception:  # noqa: BLE001 - the connection is discarded below instead
                log.warning("foreign_keys_restore_failed", exc_info=True)
            if not restored:
                connection.invalidate()


def _set_query_only(dbapi_connection: sqlite3.Connection, on: bool) -> None:
    """Set ``query_only`` and read it back."""
    dbapi_connection.execute(f"PRAGMA query_only={'ON' if on else 'OFF'}")
    [value] = dbapi_connection.execute("PRAGMA query_only").fetchone()
    if value != int(on):
        raise ConnectionSettingError(f"query_only stayed {value}")


@contextmanager
def read_only_connection(engine: Engine) -> Iterator[Connection]:
    """A connection in one read transaction with ``query_only`` on, for the read-only unit of
    work only.

    It begins at once (``BEGIN IMMEDIATE``, AD-2), and only then turns ``query_only`` on, because
    ``BEGIN IMMEDIATE`` fails under it. While it is lent, any statement but ``SELECT`` or
    ``WITH`` raises ``ReadOnlySqlError``. On exit the transaction is rolled back and
    ``query_only`` is turned back off before the connection returns to the pool; if that fails,
    the connection is invalidated, so the pool never hands it out again.
    """
    with engine.connect() as connection:
        connection.begin()  # the begin event emits BEGIN IMMEDIATE now
        dbapi_connection = connection.connection.driver_connection
        assert isinstance(dbapi_connection, sqlite3.Connection)
        try:
            _set_query_only(dbapi_connection, True)
        except BaseException:
            connection.invalidate()  # query_only may be on; never hand it out again
            raise
        connection.info[_READ_ONLY] = True
        try:
            yield connection
        finally:
            connection.info.pop(_READ_ONLY, None)
            restored = False
            try:
                if connection.in_transaction():
                    connection.rollback()
                _set_query_only(dbapi_connection, False)
                restored = True
            except Exception:  # noqa: BLE001 - the connection is discarded below instead
                log.warning("query_only_restore_failed", exc_info=True)
            finally:
                if not restored:  # also after a BaseException such as KeyboardInterrupt
                    connection.invalidate()
