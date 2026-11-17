"""Shared pytest setup.

- The ``ai_eval`` marker is opt-in: its tests are skipped unless ``-m`` selects ``ai_eval``.
- Every test runs as ``NEXTRON_ENV=test``, with no other ``NEXTRON_*`` variable and an empty
  temporary ``%LOCALAPPDATA%``, so the data dir defaults to
  ``<temp LOCALAPPDATA>\\Nextron\\test`` and nothing reads the real environment or writes under
  the real ``%LOCALAPPDATA%\\Nextron``. A test of the unset case calls
  ``monkeypatch.delenv("NEXTRON_ENV")``.
- No test reaches a real external system (AD-14, ``tests/live_guard.py``). These raise
  ``LiveAdapterInTests``: ``nextron.adapters.secrets.default_backend``,
  ``nextron.adapters.google_oauth.default_http_client``, the ``build`` of each
  ``nextron.main.ROLE_ADAPTERS`` entry, ``WinVaultKeyring``'s ``get_password``,
  ``set_password``, ``delete_password`` and ``get_credential``, and ``webbrowser.open``,
  ``open_new`` and ``open_new_tab`` (no test opens a browser). These raise ``NetworkInTests``
  for any host but a loopback address or ``localhost``: ``socket.getaddrinfo``,
  ``gethostbyname``, ``gethostbyname_ex``, ``gethostbyaddr`` and ``getnameinfo``;
  ``socket.socket.connect``, ``connect_ex``, ``sendto`` and (where it exists) ``sendmsg``; and
  on Windows asyncio's ``IocpProactor.connect`` and ``sendto``. Ports resolve to their fakes
  (``tests.fakes.PORT_FAKES``).
- Every test leaves logging as it found it: ``configure_logging`` (called directly or by
  ``serve``) replaces the root handlers, so the fixture puts them back and closes the log files.
- ``db_engine`` is a temporary database file at the head revision, disposed after the test;
  ``units`` opens units of work on it with an empty, frozen subscriber registry.
"""

import logging
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from nextron.db.engine import create_engine
from nextron.db.migrations import ensure_current
from nextron.db.uow import Subscribers, UnitOfWorkFactory
from nextron.logconfig import SDK_LOGGERS
from tests.live_guard import guard_live_constructors, guard_network

AI_EVAL = "ai_eval"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if AI_EVAL in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="ai_eval is opt-in: run pytest -m ai_eval")
    for item in items:
        if AI_EVAL in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def local_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty ``%LOCALAPPDATA%``, ``NEXTRON_ENV=test`` and no other ``NEXTRON_*`` variable;
    returns the folder."""
    for name in list(os.environ):
        if name.upper().startswith("NEXTRON_"):
            monkeypatch.delenv(name)
    folder = tmp_path / "LocalAppData"
    folder.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(folder))
    monkeypatch.setenv("NEXTRON_ENV", "test")
    return folder


@pytest.fixture(autouse=True)
def live_adapters_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every live adapter constructor raises ``LiveAdapterInTests`` (``tests/live_guard.py``)."""
    guard_live_constructors(monkeypatch)


@pytest.fixture(autouse=True)
def network_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lookups of, and connections to, anything but loopback raise ``NetworkInTests``."""
    guard_network(monkeypatch)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Restore what ``configure_logging`` changes: the root logger's handlers, filters and level,
    the SDK logger levels, ``logging.lastResort``, the exception hooks and warning capture."""
    root = logging.getLogger()
    handlers, filters, level = root.handlers[:], root.filters[:], root.level
    sdk_levels = {name: logging.getLogger(name).level for name in SDK_LOGGERS}
    hooks = (sys.excepthook, threading.excepthook, logging.lastResort)
    yield
    for handler in root.handlers[:]:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.filters[:] = filters
    root.setLevel(level)
    for name, sdk_level in sdk_levels.items():
        logging.getLogger(name).setLevel(sdk_level)
    sys.excepthook, threading.excepthook, logging.lastResort = hooks
    logging.captureWarnings(False)


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """A temporary database file migrated to head; disposed after the test."""
    engine = create_engine(tmp_path / "db" / "nextron.db")
    (tmp_path / "db").mkdir()
    ensure_current(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def units(db_engine: Engine) -> UnitOfWorkFactory:
    """Units of work on ``db_engine`` with an empty, frozen subscriber registry."""
    subscribers = Subscribers()
    subscribers.freeze()
    return UnitOfWorkFactory(db_engine, subscribers)
