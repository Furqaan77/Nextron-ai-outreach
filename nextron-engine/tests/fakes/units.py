"""``FakeUnits``: an in-memory stand-in for ``nextron.db.uow.UnitOfWorkFactory`` as the
dashboard sees it (``nextron.web.routing.Units``).

It touches no database. ``opened`` records every unit of work opened, as ``(read_only, thread
id)``; ``open_now`` holds the ones not yet closed, so a test handler can see that it runs inside
one. Like ``UnitOfWorkFactory.open``, ``open`` takes ``read_only`` only as a bool and refuses a
second unit of work while one is open on the same thread.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True, eq=False)
class FakeUnit:
    read_only: bool
    thread: int


class FakeUnits:
    def __init__(self) -> None:
        self.opened: list[tuple[bool, int]] = []
        self.open_now: list[FakeUnit] = []

    @contextmanager
    def open(self, *, read_only: bool = False) -> Iterator[FakeUnit]:
        if not isinstance(read_only, bool):
            raise TypeError(f"read_only takes a bool, not {type(read_only).__name__}")
        thread = threading.get_ident()
        if any(unit.thread == thread for unit in self.open_now):
            raise RuntimeError("a unit of work is already open on this thread")
        unit = FakeUnit(read_only, thread)
        self.opened.append((unit.read_only, unit.thread))
        self.open_now.append(unit)
        try:
            yield unit
        finally:
            self.open_now.remove(unit)
