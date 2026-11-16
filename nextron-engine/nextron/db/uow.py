"""Units of work, use cases, owner commands and notifications (AD-2, AD-3).

Each entry point (web handler, job handler, CLI command) opens exactly one unit of work and calls
exactly one use case::

    with units.open():                       # the entry point; main.py wires ``units``
        approval_api.approve(draft_id, rev, shown_hash)

    @use_case                                # nextron/app/approval/api.py
    def approve(draft_id: str, rev: int, shown_hash: str) -> None:
        uow = current()
        with uow.transaction():              # BEGIN IMMEDIATE ... COMMIT
            ...                              # own repositories use uow.session
            sending_api.enqueue(draft_id)    # an @command: joins this transaction
            uow.publish(MessageSent(...))    # subscribers run here, in order

A web entry point opens its unit of work in the sync handler body, not in a ``Depends`` or a
middleware: FastAPI may run a dependency's setup and teardown on other threadpool threads than
the endpoint, and a unit of work is bound to the thread that opened it.

The rules, each enforced here (a broken rule raises ``UnitOfWorkError``):

- ``UnitOfWorkFactory(engine, subscribers).open()`` binds the unit of work to a ``ContextVar``
  and to the opening thread. A second ``open()`` in the same context raises; ``current()``
  raises outside a unit of work and from any other thread.
- ``@use_case`` marks the one use-case method an entry point calls. A second use-case call in the
  same unit of work raises, and so does a use-case call inside a use case or a command.
- ``uow.transaction()`` is opened only by the running use case, never from inside a command, and
  never nested. It begins at once (``BEGIN IMMEDIATE``), commits on a clean exit and rolls back
  on any exception. A use case may open several in turn: no transaction spans a network call,
  so network work is prepare transaction, then the port call, then apply transaction.
- ``@command`` marks an owner command; every notification subscriber is one. A command runs only
  inside an open transaction, which it joins; it never begins, commits or rolls back. Any
  exception leaving a command dooms the transaction: the block rolls back at exit even when the
  use case caught the exception, and then raises ``UnitOfWorkError``.
- ``uow.session`` exists only inside a transaction. It is the transaction's ORM ``Session``
  without ``begin``, ``begin_nested``, ``commit``, ``rollback``, ``close``, ``connection``,
  ``get_bind``, ``bind``, ``invalidate`` and ``reset``: merely reading one of these names raises.
  Events also refuse a commit the unit of work didn't start, and doom the transaction when it
  ends outside the unit of work (for example through ``object_session(obj)``). Refusing any of
  these dooms the transaction too.
- ``uow.publish(notification)`` needs an open transaction and a type exactly in
  ``NOTIFICATIONS`` (not a subclass). It runs that type's subscribers synchronously, in
  registration order, in the same transaction.
- ``Subscribers.register_subscriber(type, command)`` accepts only listed types and ``@command``
  callables, until ``freeze()``. ``UnitOfWorkFactory`` takes only a frozen registry. Only
  ``nextron.main.build_subscribers()`` constructs ``Subscribers`` or registers subscribers
  (``tests/contracts/test_persistence_contracts.py`` checks the source).

**Read-only units of work.** ``units.open(read_only=True)`` is what every dashboard GET handler
runs in (``nextron.web.routing``); ``uow.read_only`` says which kind is open. It only reads:

- ``@use_case``, ``uow.transaction()``, ``@command`` and ``uow.publish`` raise.
- ``uow.session`` works anywhere in it. Its first use begins one read transaction through
  ``nextron.db.engine.read_only_connection`` (``BEGIN IMMEDIATE``, then ``query_only`` on, and
  only ``SELECT`` or ``WITH`` statements), kept until the unit of work closes and then rolled
  back. A unit of work that never touches the session never touches the database.
- The session also refuses ``add``, ``add_all``, ``delete``, ``merge``, ``flush``,
  ``bulk_save_objects``, ``bulk_insert_mappings`` and ``bulk_update_mappings``, and a
  ``before_flush`` event refuses any flush, autoflush included. A unit of work that exits
  cleanly with pending new, changed or deleted ORM objects raises after the rollback.
"""

import functools
import inspect
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from typing import Any, Final, NoReturn, cast

from sqlalchemy import Connection, Engine, event
from sqlalchemy.orm import Session, SessionTransaction

from nextron.db.engine import read_only_connection
from nextron.domain.notifications import NOTIFICATIONS, Notification

REFUSED_SESSION_METHODS: Final = frozenset(
    {
        "begin",
        "begin_nested",
        "commit",
        "rollback",
        "close",
        "connection",
        "get_bind",
        "bind",
        "invalidate",
        "reset",
    }
)
# Refused as well in a read-only unit of work: every way the session would write.
REFUSED_READ_ONLY_METHODS: Final = frozenset(
    {
        "add",
        "add_all",
        "delete",
        "merge",
        "flush",
        "bulk_save_objects",
        "bulk_insert_mappings",
        "bulk_update_mappings",
    }
)
_COMMAND_MARK: Final = "__nextron_command__"
_USE_CASE_MARK: Final = "__nextron_use_case__"
_MARKER: Final = object()

Subscriber = Callable[[Any], object]


class UnitOfWorkError(RuntimeError):
    """A unit-of-work rule was broken, or a doomed transaction was rolled back."""


_CURRENT: ContextVar[UnitOfWork | None] = ContextVar("nextron_unit_of_work", default=None)


def is_command(fn: object) -> bool:
    """Whether ``fn`` was made by ``@command``."""
    return getattr(fn, _COMMAND_MARK, None) is _MARKER


def is_use_case(fn: object) -> bool:
    """Whether ``fn`` was made by ``@use_case``."""
    return getattr(fn, _USE_CASE_MARK, None) is _MARKER


class Subscribers:
    """The notification subscriber registry (AD-3). Only ``nextron.main`` builds one."""

    def __init__(self) -> None:
        self._by_type: dict[type[Notification], list[Subscriber]] = {
            kind: [] for kind in NOTIFICATIONS
        }
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register_subscriber(
        self, notification_type: type[Notification], command: Subscriber
    ) -> None:
        """Run ``command(notification)`` for every published ``notification_type``, in order."""
        if self._frozen:
            raise UnitOfWorkError("the subscriber registry is frozen")
        if not isinstance(notification_type, type) or notification_type not in NOTIFICATIONS:
            raise TypeError(f"{notification_type!r} is not in the closed list of notifications")
        if not is_command(command):
            raise TypeError(f"{command!r} is not an @command; every subscriber is an owner command")
        self._by_type[notification_type].append(command)

    def freeze(self) -> None:
        """End registration."""
        self._frozen = True

    def subscribers_of(self, notification_type: type[Notification]) -> tuple[Subscriber, ...]:
        return tuple(self._by_type[notification_type])


class _Transaction:
    """One ``uow.transaction()`` block, or a read-only unit's read transaction: its connection,
    session and fate. ``release`` gives the connection back when the transaction ends."""

    def __init__(
        self, connection: Connection, session: Session, release: Callable[[], object]
    ) -> None:
        self.connection = connection
        self.session = session
        self.release = release
        self.doom: str | None = None  # why it must roll back; the first reason wins
        self.cause: BaseException | None = None
        self.ending = False  # set while the unit of work commits or rolls back

    def doom_with(self, reason: str, cause: BaseException | None = None) -> None:
        if self.doom is None:
            self.doom, self.cause = reason, cause

    # Session and connection events: transactions end only through the unit of work.

    def refuse_session_commit(self, session: Session) -> None:
        reason = "a session commit was attempted outside the unit of work"
        self.doom_with(reason)
        raise UnitOfWorkError(f"{reason}; only the use case's transaction block commits")

    def on_session_end(self, session: Session, transaction: SessionTransaction) -> None:
        if transaction.parent is None and not self.ending:
            self.doom_with("the session's transaction ended outside the unit of work")

    def refuse_connection_commit(self, connection: Connection) -> None:
        if not self.ending:
            reason = "a connection commit was attempted outside the unit of work"
            self.doom_with(reason)
            raise UnitOfWorkError(f"{reason}; only the use case's transaction block commits")

    def on_connection_rollback(self, connection: Connection) -> None:
        if not self.ending:
            self.doom_with("the transaction was rolled back outside the unit of work")

    def refuse_flush(self, session: Session, flush_context: Any, instances: Any) -> None:
        reason = "a flush was attempted in a read-only unit of work"
        self.doom_with(reason)
        raise UnitOfWorkError(f"{reason}; a dashboard GET only reads, a POST writes")

    def listen(self) -> None:
        """Hook the events that keep the transaction's end in the unit of work's hands."""
        event.listen(self.session, "before_commit", self.refuse_session_commit)
        event.listen(self.session, "after_transaction_end", self.on_session_end)
        event.listen(self.connection, "commit", self.refuse_connection_commit)
        event.listen(self.connection, "rollback", self.on_connection_rollback)


class GuardedSession:
    """``uow.session``: the transaction's ORM ``Session`` without its transaction controls, and
    in a read-only unit of work without its writes either."""

    __slots__ = ("_transaction", "_uow")

    def __init__(self, uow: UnitOfWork, transaction: _Transaction) -> None:
        self._uow = uow
        self._transaction = transaction

    def __getattr__(self, name: str) -> Any:
        uow = self._uow
        uow._check_open()
        if uow._tx is not self._transaction:
            raise UnitOfWorkError("this session's transaction has ended; use it only inside it")
        if name in REFUSED_SESSION_METHODS:
            uow._refuse(
                f"uow.session.{name} is not allowed; the unit of work begins, commits and rolls "
                "back the transaction and owns its connection"
            )
        if uow._read_only and name in REFUSED_READ_ONLY_METHODS:
            uow._refuse(
                f"uow.session.{name} is not allowed in a read-only unit of work; a dashboard GET "
                "only reads, a POST writes"
            )
        return getattr(self._transaction.session, name)

    def __repr__(self) -> str:
        return f"<GuardedSession of {self._transaction.session!r}>"


class UnitOfWork:
    """One entry point's unit of work. Only ``UnitOfWorkFactory.open()`` makes one."""

    def __init__(self, engine: Engine, subscribers: Subscribers, *, read_only: bool) -> None:
        self._engine = engine
        self._subscribers = subscribers
        self._read_only = read_only
        self._thread = threading.get_ident()
        self._open = True
        self._use_case: str | None = None  # the use case running now
        self._use_case_called = False
        self._commands = 0  # how deep in commands the call stack is
        # the open transaction: a use case's transaction() block, or a read-only unit's read
        self._tx: _Transaction | None = None

    @property
    def read_only(self) -> bool:
        """Whether this unit of work only reads (a dashboard GET's)."""
        return self._read_only

    # Guards ------------------------------------------------------------------------------------

    def _check_open(self) -> None:
        if not self._open:
            raise UnitOfWorkError("this unit of work is closed")
        if threading.get_ident() != self._thread:
            raise UnitOfWorkError("this unit of work belongs to another thread")

    def _refuse(self, message: str) -> NoReturn:
        """Raise ``UnitOfWorkError``, dooming the open transaction if there is one."""
        if self._tx is not None:
            self._tx.doom_with(message)
        raise UnitOfWorkError(message)

    def _refuse_in_read_only(self, what: str) -> None:
        if self._read_only:
            self._refuse(
                f"{what} is not allowed in a read-only unit of work; a dashboard GET only reads, "
                "a POST writes"
            )

    # Use cases and commands --------------------------------------------------------------------

    def _start_use_case(self, name: str) -> None:
        self._refuse_in_read_only(f"use case {name}")
        if self._commands:
            self._refuse(f"use case {name} was called inside a command")
        if self._use_case is not None:
            self._refuse(f"use case {name} was called inside use case {self._use_case}")
        if self._use_case_called:
            self._refuse(f"use case {name} is a second use case; an entry point calls only one")
        self._use_case = name
        self._use_case_called = True

    def _end_use_case(self) -> None:
        self._use_case = None

    def _start_command(self, name: str) -> None:
        self._refuse_in_read_only(f"command {name}")
        if self._tx is None:
            raise UnitOfWorkError(f"command {name} runs only inside an open transaction")
        self._commands += 1

    def _end_command(self, name: str, error: BaseException | None) -> None:
        self._commands -= 1
        if error is not None and self._tx is not None:
            self._tx.doom_with(f"command {name} raised {type(error).__name__}", error)

    # Transactions ------------------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Open a transaction for the running use case; see the module docstring."""
        self._check_open()
        self._refuse_in_read_only("uow.transaction()")
        if self._commands:
            self._refuse("a command cannot open a transaction; it joins the open one")
        if self._tx is not None:
            self._refuse("transactions do not nest; one is already open")
        if self._use_case is None:
            self._refuse("only the running use case opens uow.transaction()")
        tx = self._begin()
        self._tx = tx
        try:
            yield
        except BaseException:
            self._tx = None
            self._end(tx, commit=False)
            raise
        self._tx = None
        self._end(tx, commit=True)

    def _begin(self) -> _Transaction:
        connection = self._engine.connect()
        try:
            connection.begin()  # the begin event emits BEGIN IMMEDIATE now
            session = Session(bind=connection, join_transaction_mode="rollback_only")
            tx = _Transaction(connection, session, connection.close)
            tx.listen()
        except BaseException:
            connection.close()
            raise
        return tx

    def _begin_read(self) -> _Transaction:
        """The read-only unit's read transaction, on a ``query_only`` connection."""
        stack = ExitStack()
        try:
            connection = stack.enter_context(read_only_connection(self._engine))
            session = Session(bind=connection, join_transaction_mode="rollback_only")
            tx = _Transaction(connection, session, stack.close)
            tx.listen()
            event.listen(session, "before_flush", tx.refuse_flush)
        except BaseException:
            stack.close()
            raise
        return tx

    def _end(self, tx: _Transaction, *, commit: bool) -> None:
        """Commit ``tx`` when asked and not doomed, else roll it back; always close it."""
        tx.ending = True
        committed = False
        try:
            if commit and tx.doom is None:
                tx.session.flush()
                tx.connection.commit()
                committed = True
        finally:
            try:
                tx.session.close()
            finally:
                tx.release()  # rolls back whatever is still open and closes the connection
        if commit and not committed:
            raise UnitOfWorkError(f"the transaction was rolled back: {tx.doom}") from tx.cause

    @property
    def session(self) -> Session:
        """The open transaction's guarded ORM session; raises outside a transaction. In a
        read-only unit of work it exists throughout: the first use begins the read."""
        self._check_open()
        if self._tx is None and self._read_only:
            self._tx = self._begin_read()
        if self._tx is None:
            raise UnitOfWorkError("uow.session exists only inside uow.transaction()")
        return cast(Session, GuardedSession(self, self._tx))

    # Notifications -----------------------------------------------------------------------------

    def publish(self, notification: Notification) -> None:
        """Run the subscribers of ``type(notification)`` in this transaction, in order."""
        self._check_open()
        self._refuse_in_read_only("publish")
        if self._tx is None:
            raise UnitOfWorkError("publish needs an open transaction")
        kind = type(notification)
        if kind not in NOTIFICATIONS:
            self._refuse(f"{kind.__name__} is not in the closed list of notifications")
        for subscriber in self._subscribers.subscribers_of(kind):
            subscriber(notification)

    def _close(self, *, clean: bool) -> None:
        """Close the unit of work, rolling back anything still open. ``clean`` is whether the
        entry point's block exited without an exception."""
        tx, self._tx = self._tx, None
        self._open = False
        if tx is None:
            return
        session = tx.session
        unsaved = False
        try:
            unsaved = self._read_only and bool(session.new or session.dirty or session.deleted)
        finally:
            self._end(tx, commit=False)
        if clean and unsaved:
            raise UnitOfWorkError(
                "a read-only unit of work ended with unsaved changes to ORM objects; they were "
                "rolled back (a dashboard GET only reads, a POST writes)"
            )


class UnitOfWorkFactory:
    """Opens units of work on ``engine``; ``nextron.main`` builds the one the entry points use."""

    def __init__(self, engine: Engine, subscribers: Subscribers) -> None:
        if not isinstance(subscribers, Subscribers):
            raise TypeError("subscribers must be a Subscribers registry")
        if not subscribers.frozen:
            raise UnitOfWorkError("freeze the subscriber registry before opening units of work")
        self._engine = engine
        self._subscribers = subscribers

    @property
    def engine(self) -> Engine:
        return self._engine

    @contextmanager
    def open(self, *, read_only: bool = False) -> Iterator[UnitOfWork]:
        """The entry point's unit of work, current in this context and thread until exit.
        ``read_only=True`` opens the read-only kind every dashboard GET runs in."""
        if not isinstance(read_only, bool):
            raise TypeError(f"read_only takes a bool, not {type(read_only).__name__}")
        existing = _CURRENT.get()
        if existing is not None and existing._open:
            raise UnitOfWorkError("a unit of work is already open in this context")
        uow = UnitOfWork(self._engine, self._subscribers, read_only=read_only)
        token = _CURRENT.set(uow)
        try:
            try:
                yield uow
            except BaseException:
                uow._close(clean=False)
                raise
            uow._close(clean=True)
        finally:
            _CURRENT.reset(token)


def current() -> UnitOfWork:
    """The unit of work open in this context; raises outside one and from another thread."""
    uow = _CURRENT.get()
    if uow is None or not uow._open:
        raise UnitOfWorkError("no unit of work is open; an entry point opens one")
    uow._check_open()
    return uow


def _refuse_coroutine(fn: Callable[..., object], marker: str) -> None:
    """Refuse async and generator functions: their bodies run after the wrapper has returned."""
    if (
        inspect.iscoroutinefunction(fn)
        or inspect.isasyncgenfunction(fn)
        or inspect.isgeneratorfunction(fn)
    ):
        raise TypeError(
            f"@{marker} takes a plain sync function, not an async or generator function"
        )


def use_case[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Mark the one use-case method an entry point calls inside its unit of work."""
    _refuse_coroutine(fn, "use_case")
    name = fn.__qualname__

    @functools.wraps(fn)
    def run_use_case(*args: P.args, **kwargs: P.kwargs) -> R:
        uow = current()
        uow._start_use_case(name)
        try:
            return fn(*args, **kwargs)
        finally:
            uow._end_use_case()

    setattr(run_use_case, _USE_CASE_MARK, _MARKER)
    return run_use_case


def command[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Mark an owner command: it joins the open transaction and dooms it if it raises."""
    _refuse_coroutine(fn, "command")
    name = fn.__qualname__

    @functools.wraps(fn)
    def run_command(*args: P.args, **kwargs: P.kwargs) -> R:
        uow = current()
        uow._start_command(name)
        error: BaseException | None = None
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:
            error = exc
            raise
        finally:
            uow._end_command(name, error)

    setattr(run_command, _COMMAND_MARK, _MARKER)
    return run_command
