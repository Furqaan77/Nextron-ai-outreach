"""Units of work, use cases, commands, notifications and read-only units of work against the
I/O matrices of Stories 1.3 and 1.5.

Every test runs on a temporary database at head (``db_engine``) with a ``scratch`` table made by
raw DDL, mapped by a throwaway ORM base that is not on ``nextron``'s metadata. ``committed()``
reads the table outside the engine, as another process would: WAL lets it read while a unit of
work holds the write lock, so it shows exactly what has been committed.
"""

import contextvars
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, object_session

from nextron.db import engine as engine_module
from nextron.db import eventlog
from nextron.db.engine import ReadOnlySqlError, TransactionSqlError, create_engine
from nextron.db.uow import (
    REFUSED_READ_ONLY_METHODS,
    REFUSED_SESSION_METHODS,
    Subscribers,
    UnitOfWork,
    UnitOfWorkError,
    UnitOfWorkFactory,
    command,
    current,
    is_command,
    is_use_case,
    use_case,
)
from nextron.domain.errors import DomainError
from nextron.domain.notifications import DraftRejected, FooterChanged, Notification

MARKET = "01JD3QZ8X5V9K2M7N4P6R8T0WY"


class ScratchBase(DeclarativeBase):
    """A mapping for the scratch table only."""


class ScratchRow(ScratchBase):
    __tablename__ = "scratch"

    id: Mapped[int] = mapped_column(primary_key=True)
    note: Mapped[str]


class InvariantBroken(DomainError):
    code = "test_uow_invariant_broken"


@dataclass(frozen=True, slots=True)
class Unlisted(Notification):
    market_id: str


@dataclass(frozen=True, slots=True)
class LateFooterChanged(FooterChanged):
    """A subclass of a listed type is not a listed type."""


@pytest.fixture
def scratch(db_engine: Engine) -> Engine:
    with db_engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE scratch (id INTEGER PRIMARY KEY, note TEXT NOT NULL)"
        )
        connection.commit()
    return db_engine


def committed(engine: Engine) -> list[str]:
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        return [note for (note,) in reader.execute("SELECT note FROM scratch ORDER BY id")]
    finally:
        reader.close()


def write(note: str) -> None:
    current().session.execute(text("INSERT INTO scratch (note) VALUES (:note)"), {"note": note})


def visible() -> list[str]:
    """The notes this transaction sees, its own uncommitted writes included."""
    return list(current().session.scalars(select(ScratchRow.note).order_by(ScratchRow.id)))


@command
def record(note: str) -> None:
    write(note)


@command
def break_invariant(note: str) -> None:
    write(note)
    raise InvariantBroken(f"invariant broken after {note}")


Registration = tuple[type[Notification], Callable[[Notification], object]]


def frozen(*registrations: Registration) -> Subscribers:
    subscribers = Subscribers()
    for notification_type, subscriber in registrations:
        subscribers.register_subscriber(notification_type, subscriber)
    subscribers.freeze()
    return subscribers


def run_in_thread(fn: Callable[[], object], *, copy_context: bool) -> BaseException | None:
    """Run ``fn`` in a new thread, in a copy of this context or an empty one."""
    context = contextvars.copy_context() if copy_context else contextvars.Context()
    errors: list[BaseException] = []

    def target() -> None:
        try:
            context.run(fn)
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=30)
    return errors[0] if errors else None


# Opening ------------------------------------------------------------------------------------------


def test_current_raises_outside_a_unit_of_work() -> None:
    with pytest.raises(UnitOfWorkError, match="no unit of work is open"):
        current()


def test_open_makes_the_unit_of_work_current_until_exit(units: UnitOfWorkFactory) -> None:
    with units.open() as uow:
        assert isinstance(uow, UnitOfWork)
        assert current() is uow

    with pytest.raises(UnitOfWorkError):
        current()
    with units.open() as again:
        assert current() is again is not uow


def test_a_second_open_in_the_same_context_raises(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    other_factory = UnitOfWorkFactory(db_engine, frozen())
    with units.open() as uow:
        with pytest.raises(UnitOfWorkError, match="already open"):
            with units.open():
                pass
        with pytest.raises(UnitOfWorkError, match="already open"):
            with other_factory.open():
                pass
        assert current() is uow


def test_current_raises_from_another_thread(units: UnitOfWorkFactory) -> None:
    with units.open():
        copied = run_in_thread(current, copy_context=True)
        fresh = run_in_thread(current, copy_context=False)
        nested = run_in_thread(lambda: units.open().__enter__(), copy_context=True)

    assert isinstance(copied, UnitOfWorkError) and "another thread" in str(copied)
    assert isinstance(fresh, UnitOfWorkError) and "no unit of work" in str(fresh)
    assert isinstance(nested, UnitOfWorkError) and "already open" in str(nested)


# Row: two threads
def test_two_threads_open_independent_units_of_work_and_both_commit(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    both_open = threading.Barrier(2, timeout=30)
    seen: dict[str, UnitOfWork] = {}

    @use_case
    def save(note: str) -> None:
        uow = current()
        seen[note] = uow
        both_open.wait()
        with uow.transaction():
            record(note)

    errors: list[BaseException] = []

    def entry(note: str) -> None:  # each thread starts with an empty context
        try:
            with units.open():
                save(note)
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            errors.append(exc)

    threads = [
        threading.Thread(target=entry, args=(note,), context=contextvars.Context())
        for note in ("a", "b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert seen["a"] is not seen["b"]
    assert sorted(committed(scratch)) == ["a", "b"]


# Use cases --------------------------------------------------------------------------------------


@use_case
def save_note(note: str) -> None:
    with current().transaction():
        write(note)


def test_a_use_case_runs_inside_a_unit_of_work(units: UnitOfWorkFactory, scratch: Engine) -> None:
    with units.open():
        save_note("saved")

    assert committed(scratch) == ["saved"]
    assert is_use_case(save_note) and not is_command(save_note)
    assert is_command(record) and not is_use_case(record)


def test_a_use_case_outside_a_unit_of_work_raises(scratch: Engine) -> None:
    with pytest.raises(UnitOfWorkError, match="no unit of work"):
        save_note("x")


def test_a_second_use_case_call_raises(units: UnitOfWorkFactory, scratch: Engine) -> None:
    with units.open():
        save_note("first")
        with pytest.raises(UnitOfWorkError, match="second use case"):
            save_note("second")

    assert committed(scratch) == ["first"]


def test_a_use_case_inside_a_use_case_raises(units: UnitOfWorkFactory, scratch: Engine) -> None:
    @use_case
    def outer() -> None:
        save_note("inner")

    with units.open(), pytest.raises(UnitOfWorkError, match="inside use case"):
        outer()

    assert committed(scratch) == []


def test_a_use_case_inside_a_command_raises_and_dooms(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @command
    def calls_a_use_case() -> None:
        try:
            save_note("inner")
        except UnitOfWorkError:
            pass  # swallowed, but the transaction is doomed

    @use_case
    def outer() -> None:
        with current().transaction():
            write("outer")
            calls_a_use_case()

    with units.open(), pytest.raises(UnitOfWorkError, match="rolled back: use case"):
        outer()

    assert committed(scratch) == []


def test_the_markers_refuse_async_functions() -> None:
    async def handler() -> None:
        pass

    async def stream() -> object:
        yield None

    def rows() -> object:  # its body would run after the wrapper had returned
        yield None

    for fn in (handler, stream, rows):
        with pytest.raises(TypeError, match="plain sync function"):
            use_case(fn)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="plain sync function"):
            command(fn)  # type: ignore[arg-type]


# Transactions -----------------------------------------------------------------------------------


def test_a_transaction_commits_on_a_clean_exit(units: UnitOfWorkFactory, scratch: Engine) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            write("one")
            write("two")
            assert committed(scratch) == []

    with units.open():
        save()

    assert committed(scratch) == ["one", "two"]


def test_a_transaction_rolls_back_on_any_exception(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            write("lost")
            raise KeyError("boom")

    with units.open(), pytest.raises(KeyError):
        save()

    assert committed(scratch) == []


def test_a_transaction_begins_at_once_and_holds_the_write_lock(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    assert scratch.url.database
    other = sqlite3.connect(scratch.url.database, timeout=0, isolation_level=None)

    @use_case
    def idle() -> None:
        with current().transaction():
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")

    try:
        with units.open():
            idle()
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
    finally:
        other.close()


def test_only_the_running_use_case_opens_a_transaction(units: UnitOfWorkFactory) -> None:
    with units.open() as uow:
        with pytest.raises(UnitOfWorkError, match="only the running use case"):
            with uow.transaction():
                pass


def test_transactions_do_not_nest(units: UnitOfWorkFactory, scratch: Engine) -> None:
    @use_case
    def nested() -> None:
        uow = current()
        with uow.transaction():
            write("outer")
            with uow.transaction():
                write("inner")

    with units.open(), pytest.raises(UnitOfWorkError, match="do not nest"):
        nested()

    assert committed(scratch) == []


# Row: prepare, call, apply
def test_two_sequential_transactions_each_commit(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def prepare_call_apply() -> None:
        uow = current()
        with uow.transaction():
            write("prepared")
        assert committed(scratch) == ["prepared"]
        with pytest.raises(UnitOfWorkError, match="only inside uow.transaction"):
            uow.session  # noqa: B018 - between the blocks, where the port call goes
        with uow.transaction():
            write("applied")

    with units.open():
        prepare_call_apply()

    assert committed(scratch) == ["prepared", "applied"]


def test_pending_orm_objects_are_flushed_and_committed_at_exit(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            current().session.add(ScratchRow(note="added, never flushed"))

    with units.open():
        save()

    assert committed(scratch) == ["added, never flushed"]


def test_a_begin_that_times_out_returns_the_connection(
    scratch: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert scratch.url.database
    monkeypatch.setattr(engine_module, "BUSY_TIMEOUT_MS", 100)  # read by each new connection
    engine = create_engine(Path(scratch.url.database))
    blocker = sqlite3.connect(scratch.url.database, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # another process holds the write lock
    try:
        with UnitOfWorkFactory(engine, frozen()).open():
            with pytest.raises(OperationalError, match="locked"):
                save_note("blocked")
        assert engine.pool.checkedout() == 0
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        engine.dispose()

    assert committed(scratch) == []


def test_a_commit_that_fails_returns_the_connection(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            write("written")
            current().session.add(ScratchRow(note=None))  # NOT NULL fails at the exit flush

    with units.open(), pytest.raises(IntegrityError, match="NOT NULL"):
        save()

    assert scratch.pool.checkedout() == 0
    assert committed(scratch) == []


# Commands ---------------------------------------------------------------------------------------


def test_a_command_joins_the_open_transaction(units: UnitOfWorkFactory, scratch: Engine) -> None:
    seen: list[list[str]] = []

    @command
    def look() -> None:
        seen.append(visible())

    @use_case
    def save() -> None:
        with current().transaction():
            write("by the use case")
            record("by a command")
            look()

    with units.open():
        save()

    assert seen == [["by the use case", "by a command"]]
    assert committed(scratch) == ["by the use case", "by a command"]


# Row: command outside a transaction
def test_a_command_outside_a_transaction_raises(units: UnitOfWorkFactory, scratch: Engine) -> None:
    @use_case
    def too_early() -> None:
        record("early")

    with pytest.raises(UnitOfWorkError, match="no unit of work"):
        record("outside")
    with units.open(), pytest.raises(UnitOfWorkError, match="only inside an open transaction"):
        too_early()

    assert committed(scratch) == []


def _misuse(kind: str) -> None:
    uow = current()
    if kind == "transaction":
        with uow.transaction():
            pass
    else:
        getattr(uow.session, kind)()


MISUSES = ["transaction", "commit", "rollback", "begin"]


# Row: command misuse
@pytest.mark.parametrize("kind", MISUSES)
def test_a_command_that_tries_to_control_the_transaction_raises(
    kind: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @command
    def misbehave() -> None:
        _misuse(kind)

    @use_case
    def save() -> None:
        with current().transaction():
            write("before")
            misbehave()

    with units.open(), pytest.raises(UnitOfWorkError, match="not allowed|cannot open"):
        save()

    assert committed(scratch) == []


@pytest.mark.parametrize("kind", MISUSES)
def test_a_refused_transaction_control_dooms_even_when_caught(
    kind: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @command
    def misbehave_quietly() -> None:
        try:
            _misuse(kind)
        except UnitOfWorkError:
            pass

    @use_case
    def save() -> None:
        with current().transaction():
            write("before")
            misbehave_quietly()
            write("after")

    with units.open(), pytest.raises(UnitOfWorkError, match="rolled back"):
        save()

    assert committed(scratch) == []


# Row: invariant violation
def test_an_invariant_violation_rolls_back_the_whole_transaction(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            write("by the use case")
            record("by a command")
            break_invariant("by the failing command")

    with units.open(), pytest.raises(InvariantBroken):
        save()

    assert committed(scratch) == []


# Row: swallowed failure
def test_a_swallowed_command_failure_still_rolls_back(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            write("by the use case")
            try:
                break_invariant("by the failing command")
            except InvariantBroken:
                pass
            write("after the failure")

    with units.open(), pytest.raises(UnitOfWorkError, match="rolled back") as raised:
        save()

    assert isinstance(raised.value.__cause__, InvariantBroken)
    assert "break_invariant raised InvariantBroken" in str(raised.value)
    assert committed(scratch) == []


# The session ------------------------------------------------------------------------------------


def test_the_session_exists_only_inside_a_transaction(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    kept = []

    @use_case
    def save() -> None:
        uow = current()
        with pytest.raises(UnitOfWorkError):
            uow.session  # noqa: B018
        with uow.transaction():
            kept.append(uow.session)
            write("x")
        with pytest.raises(UnitOfWorkError, match="has ended"):
            kept[0].execute(text("SELECT 1"))

    with units.open():
        save()

    assert committed(scratch) == ["x"]


@pytest.mark.parametrize("method", sorted(REFUSED_SESSION_METHODS))
def test_the_session_refuses_transaction_control_and_dooms(
    method: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            write("x")
            with pytest.raises(UnitOfWorkError, match=f"uow.session.{method} is not allowed"):
                getattr(current().session, method)  # reading the name is enough

    with units.open(), pytest.raises(UnitOfWorkError, match="rolled back"):
        save()

    assert committed(scratch) == []


@pytest.mark.parametrize("ending", ["commit", "rollback", "close", "connection_commit"])
def test_ending_the_transaction_through_object_session_dooms_it(
    ending: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @command
    def sneak() -> None:
        row = ScratchRow(note="added")
        current().session.add(row)
        current().session.flush()
        session = object_session(row)
        assert session is not None
        try:
            if ending == "connection_commit":
                session.connection().commit()
            else:
                getattr(session, ending)()
        except UnitOfWorkError:
            pass  # a commit is refused; caught here so only the doom is left

    @use_case
    def save() -> None:
        with current().transaction():
            write("before")
            sneak()
            assert committed(scratch) == []
            if ending != "connection_commit":  # SQLAlchemy then refuses all use until rollback
                write("after")

    with units.open(), pytest.raises(UnitOfWorkError, match="outside the unit of work"):
        save()

    assert committed(scratch) == []


# Row: raw transaction SQL
@pytest.mark.parametrize("sql", ["COMMIT", "/* x */ rollback", "SAVEPOINT a", "; COMMIT"])
def test_raw_transaction_sql_raises_and_commits_or_rolls_back_nothing(
    sql: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            write("pending")
            with pytest.raises(TransactionSqlError):
                current().session.execute(text(sql))
            assert committed(scratch) == []
            assert visible() == ["pending"]

    with units.open():
        save()

    assert committed(scratch) == ["pending"]


# Notifications ----------------------------------------------------------------------------------


@command
def first_subscriber(notification: FooterChanged) -> None:
    write(f"first sees {len(visible())}")


@command
def second_subscriber(notification: FooterChanged) -> None:
    write(f"second sees {len(visible())}")


@command
def failing_subscriber(notification: FooterChanged) -> None:
    write("failing")
    raise InvariantBroken("the subscriber's invariant broke")


@command
def other_type_subscriber(notification: DraftRejected) -> None:
    write("wrong type")


def publish_footer(units: UnitOfWorkFactory, notification: Notification | None = None) -> None:
    @use_case
    def change_footer() -> None:
        uow = current()
        with uow.transaction():
            write("publisher")
            uow.publish(notification or FooterChanged(market_id=MARKET))

    with units.open():
        change_footer()


# Row: publish
def test_publish_runs_the_subscribers_in_order_in_the_same_transaction(
    db_engine: Engine, scratch: Engine
) -> None:
    units = UnitOfWorkFactory(
        db_engine,
        frozen(
            (FooterChanged, first_subscriber),
            (DraftRejected, other_type_subscriber),
            (FooterChanged, second_subscriber),
        ),
    )

    publish_footer(units)

    assert committed(scratch) == ["publisher", "first sees 1", "second sees 2"]


def test_publish_with_no_subscribers_does_nothing(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    publish_footer(units)

    assert committed(scratch) == ["publisher"]


def test_if_the_second_subscriber_fails_nothing_commits(
    db_engine: Engine, scratch: Engine
) -> None:
    units = UnitOfWorkFactory(
        db_engine, frozen((FooterChanged, first_subscriber), (FooterChanged, failing_subscriber))
    )

    with pytest.raises(InvariantBroken):
        publish_footer(units)

    assert committed(scratch) == []


# Row: bad publish
@pytest.mark.parametrize(
    "notification", [Unlisted(market_id=MARKET), LateFooterChanged(market_id=MARKET)]
)
def test_publishing_a_type_outside_the_list_raises(
    notification: Notification, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    with pytest.raises(UnitOfWorkError, match="not in the closed list"):
        publish_footer(units, notification)

    assert committed(scratch) == []


def test_publishing_without_an_open_transaction_raises(units: UnitOfWorkFactory) -> None:
    @use_case
    def too_early() -> None:
        current().publish(FooterChanged(market_id=MARKET))

    with units.open(), pytest.raises(UnitOfWorkError, match="needs an open transaction"):
        too_early()


# Row: bad registration
@pytest.mark.parametrize(
    "notification_type", [Unlisted, LateFooterChanged, Notification, int, "FooterChanged"]
)
def test_registering_a_type_outside_the_list_raises(notification_type: object) -> None:
    with pytest.raises(TypeError, match="closed list"):
        Subscribers().register_subscriber(
            notification_type,  # type: ignore[arg-type]
            first_subscriber,
        )


def test_registering_a_callable_that_is_not_a_command_raises() -> None:
    def plain(notification: FooterChanged) -> None:
        pass

    with pytest.raises(TypeError, match="not an @command"):
        Subscribers().register_subscriber(FooterChanged, plain)
    with pytest.raises(TypeError, match="not an @command"):
        Subscribers().register_subscriber(FooterChanged, save_note)


def test_registering_after_freeze_raises() -> None:
    subscribers = frozen((FooterChanged, first_subscriber))

    with pytest.raises(UnitOfWorkError, match="frozen"):
        subscribers.register_subscriber(FooterChanged, second_subscriber)
    assert subscribers.subscribers_of(FooterChanged) == (first_subscriber,)


def test_the_factory_takes_only_a_frozen_registry(db_engine: Engine) -> None:
    with pytest.raises(UnitOfWorkError, match="freeze"):
        UnitOfWorkFactory(db_engine, Subscribers())
    with pytest.raises(TypeError):
        UnitOfWorkFactory(db_engine, object())  # type: ignore[arg-type]


def test_the_subscriber_count_is_visible_only_inside_the_transaction(
    db_engine: Engine, scratch: Engine
) -> None:
    """A subscriber's reads see the publisher's uncommitted row: one transaction, one session."""
    seen: list[int] = []

    @command
    def count(notification: FooterChanged) -> None:
        seen.append(current().session.scalar(select(func.count()).select_from(ScratchRow)) or 0)

    publish_footer(UnitOfWorkFactory(db_engine, frozen((FooterChanged, count))))

    assert seen == [1]


# Read-only units of work (Story 1.5) ------------------------------------------------------------

AT = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def events_count(engine: Engine) -> int:
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        [(count,)] = reader.execute("SELECT count(*) FROM events").fetchall()
        return int(count)
    finally:
        reader.close()


def append_event() -> str:
    return eventlog.append(
        entity="events",
        entity_id=MARKET,
        from_status=None,
        to_status="created",
        cause="test",
        cause_ref=None,
        actor="operator",
        at=AT,
    )


@pytest.fixture
def checkouts(db_engine: Engine) -> list[sqlite3.Connection]:
    """The driver connection of every pool checkout from now on."""
    seen: list[sqlite3.Connection] = []

    def record(dbapi_connection: sqlite3.Connection, *args: object) -> None:
        seen.append(dbapi_connection)

    event.listen(db_engine.pool, "checkout", record)
    return seen


def assert_writes_normally(units: UnitOfWorkFactory, scratch: Engine) -> None:
    """A write unit of work afterwards commits, on a connection with query_only off."""
    with units.open():
        save_note("written after")
    assert committed(scratch)[-1] == "written after"
    with scratch.connect() as connection:
        dbapi = connection.connection.driver_connection
        assert isinstance(dbapi, sqlite3.Connection)
        assert dbapi.execute("PRAGMA query_only").fetchone() == (0,)


def test_open_takes_read_only_as_a_bool(units: UnitOfWorkFactory) -> None:
    with units.open() as write:
        assert write.read_only is False
    with units.open(read_only=True) as read:
        assert read.read_only is True
    for value in (1, "yes", None):
        with pytest.raises(TypeError, match="read_only takes a bool"):
            with units.open(read_only=value):  # type: ignore[arg-type]
                pass


def test_a_read_only_unit_that_never_reads_never_touches_the_database(
    units: UnitOfWorkFactory, checkouts: list[sqlite3.Connection]
) -> None:
    with units.open(read_only=True) as uow:
        assert current() is uow

    assert checkouts == []


def test_the_session_reads_anywhere_in_one_read_transaction(
    units: UnitOfWorkFactory, scratch: Engine, checkouts: list[sqlite3.Connection]
) -> None:
    with units.open():
        save_note("committed")
    assert scratch.url.database
    other = sqlite3.connect(scratch.url.database, timeout=0, isolation_level=None)
    before = len(checkouts)
    try:
        with units.open(read_only=True) as uow:
            assert visible() == ["committed"]
            assert uow.session.scalar(select(func.count()).select_from(ScratchRow)) == 1
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")  # BEGIN IMMEDIATE held since the first read
            dbapi = checkouts[-1]
            assert dbapi.execute("PRAGMA query_only").fetchone() == (1,)
        assert len(checkouts) == before + 1  # one connection, one read transaction
        other.execute("BEGIN IMMEDIATE")  # released when the unit of work closed
        other.execute("ROLLBACK")
    finally:
        other.close()


@use_case
def read_notes() -> None:
    visible()


@command
def read_in_a_command() -> None:
    visible()


def _use_case(uow: UnitOfWork) -> None:
    read_notes()


def _transaction(uow: UnitOfWork) -> None:
    with uow.transaction():
        pass


def _command(uow: UnitOfWork) -> None:
    read_in_a_command()


def _publish(uow: UnitOfWork) -> None:
    uow.publish(FooterChanged(market_id=MARKET))


READ_ONLY_REFUSALS = {
    "use_case": _use_case,
    "transaction": _transaction,
    "command": _command,
    "publish": _publish,
}


@pytest.mark.parametrize("case", sorted(READ_ONLY_REFUSALS))
@pytest.mark.parametrize("after_a_read", [False, True])
def test_a_read_only_unit_refuses_use_cases_transactions_commands_and_publish(
    case: str, after_a_read: bool, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    with units.open(read_only=True) as uow:
        if after_a_read:
            visible()
        with pytest.raises(UnitOfWorkError, match="not allowed in a read-only unit of work"):
            READ_ONLY_REFUSALS[case](uow)

    assert committed(scratch) == []
    assert events_count(scratch) == 0
    assert_writes_normally(units, scratch)


def _event(uow: UnitOfWork) -> None:
    append_event()


def _insert(uow: UnitOfWork) -> None:
    uow.session.execute(text("INSERT INTO scratch (note) VALUES ('x')"))


def _query_only_off(uow: UnitOfWork) -> None:
    uow.session.execute(text("PRAGMA query_only=OFF"))


def _raw_event_insert(uow: UnitOfWork) -> None:
    uow.session.execute(
        text(
            "INSERT INTO events (id, entity, entity_id, to_status, cause, actor, at) "
            "VALUES (:id, 'events', :id, 'x', 'x', 'operator', '2026-11-17T09:00:00.000000Z')"
        ),
        {"id": MARKET},
    )


SQL_REFUSALS = {
    "eventlog_append": _event,
    "insert": _insert,
    "query_only_off": _query_only_off,
    "raw_event_insert": _raw_event_insert,
}


@pytest.mark.parametrize("case", sorted(SQL_REFUSALS))
def test_a_write_statement_in_a_read_only_unit_raises_and_writes_nothing(
    case: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    with pytest.raises(ReadOnlySqlError):
        with units.open(read_only=True) as uow:
            SQL_REFUSALS[case](uow)

    assert committed(scratch) == []
    assert events_count(scratch) == 0
    assert_writes_normally(units, scratch)


def test_a_caught_write_statement_still_writes_nothing(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    with units.open(read_only=True) as uow:
        with pytest.raises(ReadOnlySqlError):
            _insert(uow)
        assert visible() == []  # the read transaction carries on

    assert committed(scratch) == []
    assert_writes_normally(units, scratch)


@pytest.mark.parametrize("method", sorted(REFUSED_READ_ONLY_METHODS | REFUSED_SESSION_METHODS))
def test_the_read_only_session_refuses_every_write_method(
    method: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    with units.open(read_only=True) as uow:
        with pytest.raises(UnitOfWorkError, match=f"uow.session.{method} is not allowed"):
            getattr(uow.session, method)  # reading the name is enough

    assert committed(scratch) == []
    assert_writes_normally(units, scratch)


def test_the_write_methods_stay_available_in_a_write_transaction(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    @use_case
    def save() -> None:
        with current().transaction():
            current().session.add(ScratchRow(note="added"))
            current().session.flush()

    with units.open():
        save()

    assert committed(scratch) == ["added"]


def _seed(units: UnitOfWorkFactory) -> None:
    with units.open():
        save_note("original")


def test_a_dirty_object_raises_at_a_clean_exit_and_changes_nothing(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    _seed(units)

    with pytest.raises(UnitOfWorkError, match="unsaved changes"):
        with units.open(read_only=True) as uow:
            row = uow.session.scalars(select(ScratchRow)).one()
            row.note = "changed"

    assert committed(scratch) == ["original"]
    assert_writes_normally(units, scratch)


def test_a_dirty_object_is_refused_at_the_next_autoflush(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    _seed(units)

    with pytest.raises(UnitOfWorkError, match="flush was attempted in a read-only"):
        with units.open(read_only=True) as uow:
            row = uow.session.scalars(select(ScratchRow)).one()
            row.note = "changed"
            visible()  # autoflush

    assert committed(scratch) == ["original"]
    assert_writes_normally(units, scratch)


@pytest.mark.parametrize("operation", ["add", "delete", "flush", "commit"])
def test_the_raw_session_through_object_session_cannot_write_either(
    operation: str, units: UnitOfWorkFactory, scratch: Engine
) -> None:
    _seed(units)

    with pytest.raises(UnitOfWorkError):
        with units.open(read_only=True) as uow:
            row = uow.session.scalars(select(ScratchRow)).one()
            session = object_session(row)
            assert session is not None
            if operation == "add":
                session.add(ScratchRow(note="sneaked in"))
            elif operation == "delete":
                session.delete(row)
            elif operation == "flush":
                row.note = "changed"
                session.flush()
            else:
                session.add(ScratchRow(note="sneaked in"))
                session.commit()

    assert committed(scratch) == ["original"]
    assert_writes_normally(units, scratch)


def test_an_exception_in_a_read_only_unit_is_raised_as_it_is(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    _seed(units)

    with pytest.raises(KeyError, match="boom"):
        with units.open(read_only=True) as uow:
            row = uow.session.scalars(select(ScratchRow)).one()
            row.note = "changed"  # unsaved, but the KeyError is what the caller sees
            raise KeyError("boom")

    assert committed(scratch) == ["original"]
    assert_writes_normally(units, scratch)


def test_the_read_only_session_belongs_to_its_thread_and_ends_with_the_unit(
    units: UnitOfWorkFactory, scratch: Engine
) -> None:
    with units.open(read_only=True) as uow:
        session = uow.session
        visible()
        error = run_in_thread(lambda: session.execute(text("SELECT 1")), copy_context=True)
        assert isinstance(error, UnitOfWorkError) and "another thread" in str(error)

    with pytest.raises(UnitOfWorkError, match="closed"):
        session.execute(text("SELECT 1"))


def test_a_read_only_unit_and_a_write_unit_do_not_nest(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        with pytest.raises(UnitOfWorkError, match="already open"):
            with units.open():
                pass
