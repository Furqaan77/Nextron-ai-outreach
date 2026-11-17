"""Composition root (AD-1, AD-2, AD-3, AD-14): wiring, startup checks, the instance lock, the
database, the notification subscriber registry and Uvicorn.

``serve`` runs these steps in order and stops at the first failure. The supervisor
(``scripts/run.ps1``, Story 1.21) relies on the exit codes:

1. Bootstrap config, including ``NEXTRON_ENV``: exit 2, before any file or folder exists.
2. Environment checks (SQLite version, tzdata, OneDrive): exit 2, still before any folder exists.
3. Create the data dir and its ``logs`` folder, then take ``<data dir>\\nextron.lock``: exit 3
   when another instance of the same environment holds it, exit 2 when the folders or the lock
   file can't be created.
4. Logging (``nextron.logconfig``): redacted JSON lines in ``<data dir>\\logs\\nextron.log``.
   It starts only once the lock is held, so a second instance never opens the log file.
5. The port check: exit 2, logged as ``startup_check_failed``. It runs after the lock because a
   second instance of the same environment finds its port taken by the first; it must report
   exit 3, not a busy port.
6. The database, ``<data dir>\\nextron.db`` (``open_database``): created when missing and brought
   to the code's head revision by ``nextron.db.migrations.ensure_current``, which logs
   ``database_migrated`` (``from_revision``, ``to_revision``) when it upgrades. A database whose
   revision is ahead of the code, or a foreign or forked one, is refused untouched: exit 2,
   logged as ``startup_check_failed`` with ``check=database_revision``. A database that can't
   be opened, or a failed migration (rolled back), exits 2, logged as
   ``database_migration_failed`` with the traceback. A second instance or a busy port never
   reaches this step.
7. Uvicorn on 127.0.0.1 with one worker (``--reload`` only in ``dev``) until it stops: exit 0.
   The dashboard's GET handlers read through the unit-of-work factory step 6 returned.
   ``engine_started`` (with ``db_revision``) is logged before it and ``engine_stopped`` (with
   ``exit_code``) after. The database engine is disposed when ``serve`` returns. Uvicorn runs
   with ``log_config=None``, so its records go through the root handlers. Uvicorn exits with
   code 3 when it can't bind or start the app; ``serve`` logs ``server_start_failed`` and
   reports exit 2, so it never looks like a held lock.

Any other error is logged as ``engine_crashed`` and ends with exit 1.

``build_subscribers`` is the notification subscriber registry (AD-3): the only code that
constructs ``Subscribers`` or registers a subscriber. It ships empty and frozen; each story that
adds a subscriber registers its owner command there, in the order AD-3 lists.

With ``--reload`` (``dev`` only), Uvicorn serves from a worker process that does not write
``nextron.log``: only the reloader's own records reach the file. The worker's WARNING and above
go to stderr, redacted. The worker opens the database with ``connect_database``, which never
migrates: the reloader brought it to head before starting Uvicorn.

``doctor`` runs every check, prints PASS or FAIL for each, and exits 0 when none fail, 1 when
any fails, and 2 when ``NEXTRON_ENV`` or another bootstrap setting is invalid. It takes no lock,
creates nothing and starts no server.

``build_app`` wires the dashboard (AD-13): its ``DashboardEntry`` reads the one-time code
through ``KeyringSecrets(<env>)`` (Windows Credential Manager, service ``nextron-engine/<env>``),
its ``Setup`` stores the settings, both on one ``SystemClock``, and its handlers open their
units of work through ``units``; tests inject fakes.

``open_dashboard`` (``nextron open``) runs these steps and stops at the first failure:

1. Bootstrap config: exit 2.
2. Is the engine running? A TCP connect to ``127.0.0.1:<port>`` with a 1 s timeout
   (``startup_checks.engine_is_running``). No: ``engine not running`` on stderr and exit 1,
   before any secrets object is built, so no code is written.
3. ``DashboardEntry.issue_code()`` writes a fresh code to the keyring entry
   ``session/open_code``. A Credential Manager failure: exit 1.
4. The default browser opens ``http://127.0.0.1:<port>/enter?t=<code>``. When it can't, the
   code is withdrawn (best effort) and the command exits 1.
5. Exit 0. The code is never printed or logged; the link works once, within 5 minutes.
"""

import contextlib
import logging
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Final, TextIO

import uvicorn
from fastapi import FastAPI

from nextron import __version__
from nextron.adapters.clock import SystemClock
from nextron.adapters.secrets import KeyringSecrets
from nextron.app.ops.api import DashboardEntry
from nextron.app.setup.api import Setup
from nextron.bootstrap import BootstrapConfig, BootstrapError, load_bootstrap
from nextron.db.engine import ConnectionSettingError, create_engine
from nextron.db.migrations import DatabaseStateError, ensure_current
from nextron.db.uow import Subscribers, UnitOfWorkFactory
from nextron.lock import LOCK_FILE_NAME, InstanceLock, LockHeldError
from nextron.logconfig import CRASH_EVENT, LOG_FILE_NAME, configure_logging, mark_logged
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort
from nextron.startup_checks import (
    CheckResult,
    Probes,
    all_checks,
    check_port_free,
    engine_is_running,
    environment_checks,
)
from nextron.web.app import create_app
from nextron.web.routing import Units

HOST: Final = "127.0.0.1"
WORKERS: Final = 1
LOGS_DIR_NAME: Final = "logs"
DB_FILE_NAME: Final = "nextron.db"
DATABASE_REVISION_CHECK: Final = "database_revision"
PACKAGE_DIR: Final = Path(__file__).resolve().parent

EXIT_OK: Final = 0
EXIT_CRASHED: Final = 1  # serve: an unexpected error (a Python traceback)
EXIT_CHECKS_FAILED: Final = 1  # doctor: at least one check failed
EXIT_STARTUP_FAILED: Final = 2  # invalid config or a failed startup check
EXIT_LOCK_HELD: Final = 3  # another instance runs for this environment
EXIT_OPEN_FAILED: Final = 1  # open: the engine is down, or the code or the browser failed

ENGINE_NOT_RUNNING: Final = "engine not running"
CODE_NOT_STORED: Final = "could not store the one-time code in Windows Credential Manager"
BROWSER_NOT_OPENED: Final = "could not open the default browser"
DASHBOARD_OPENED: Final = (
    "opened the dashboard in your browser; the link works once, within 5 minutes"
)

ServerRunner = Callable[[BootstrapConfig, bool, UnitOfWorkFactory], None]
EngineProbe = Callable[[str, int], bool]
Browser = Callable[[str], bool]

log = logging.getLogger(__name__)


def build_entry(
    config: BootstrapConfig, *, secrets: SecretsPort | None = None, clock: Clock | None = None
) -> DashboardEntry:
    """The dashboard's one-time code service: keyring and the system clock unless injected."""
    return DashboardEntry(
        secrets if secrets is not None else KeyringSecrets(config.env),
        clock if clock is not None else SystemClock(),
    )


def build_app(
    config: BootstrapConfig,
    *,
    units: Units,
    secrets: SecretsPort | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """The dashboard app; its handlers open their units of work through ``units``. One clock,
    the system clock unless injected, serves the entry code and the settings."""
    clock = clock if clock is not None else SystemClock()
    return create_app(
        env=config.env,
        version=__version__,
        port=config.port,
        entry=build_entry(config, secrets=secrets, clock=clock),
        setup=Setup(clock),
        units=units,
    )


def build_subscribers() -> Subscribers:
    """The notification subscriber registry (AD-3), frozen. Empty until a story adds one:
    register each subscriber with ``register_subscriber(<Notification>, <owner command>)``,
    in the order AD-3 lists, before ``freeze()``."""
    subscribers = Subscribers()
    subscribers.freeze()
    return subscribers


def connect_database(config: BootstrapConfig) -> UnitOfWorkFactory:
    """The entry points' unit-of-work factory on ``<data dir>\\nextron.db``, with the frozen
    subscriber registry. It connects lazily and never migrates."""
    subscribers = build_subscribers()
    engine = create_engine(config.data_dir / DB_FILE_NAME)
    try:
        return UnitOfWorkFactory(engine, subscribers)
    except BaseException:
        engine.dispose()
        raise


def open_database(config: BootstrapConfig) -> tuple[UnitOfWorkFactory, str]:
    """Open ``<data dir>\\nextron.db`` and bring it to head; return the entry points' factory and
    the database's revision.

    Raises ``DatabaseStateError`` (``DatabaseAheadError`` included) or ``ConnectionSettingError``
    when the database is refused, and whatever made opening or a migration fail; either way the
    engine is disposed first.
    """
    units = connect_database(config)
    try:
        before, after = ensure_current(units.engine)
    except BaseException:
        units.engine.dispose()
        raise
    if before != after:
        log.info("database_migrated", extra={"from_revision": before, "to_revision": after})
    return units, after


def create_app_from_environment() -> FastAPI:
    """App factory for Uvicorn's reload worker, which starts in a fresh process. The reloader
    has already brought the database to head, so the worker only connects."""
    config = load_bootstrap()
    return build_app(config, units=connect_database(config))


def run_uvicorn(config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
    """Serve on 127.0.0.1 only, with one worker, until Uvicorn stops. Without reload the app
    reads through ``units``; the reload worker builds its own (``create_app_from_environment``)."""
    if reload:
        uvicorn.run(
            "nextron.main:create_app_from_environment",
            factory=True,
            reload=True,
            reload_dirs=[str(PACKAGE_DIR)],
            host=HOST,
            port=config.port,
            workers=WORKERS,
            proxy_headers=False,
            log_config=None,
        )
    else:
        uvicorn.run(
            build_app(config, units=units),
            host=HOST,
            port=config.port,
            workers=WORKERS,
            proxy_headers=False,
            log_config=None,
        )


def _report_failures(results: list[CheckResult], err: TextIO) -> bool:
    failures = [result for result in results if not result.ok]
    for result in failures:
        print(f"startup check failed: {result.name}: {result.detail}", file=err)
    return bool(failures)


def serve(
    *,
    reload: bool = False,
    probes: Probes | None = None,
    run_server: ServerRunner = run_uvicorn,
    err: TextIO | None = None,
) -> int:
    """Start the engine for ``NEXTRON_ENV``; return the process exit code."""
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    if reload and config.env != "dev":
        print("--reload is allowed only when NEXTRON_ENV=dev", file=err)
        return EXIT_STARTUP_FAILED

    probes = probes if probes is not None else Probes.system()
    if _report_failures(environment_checks(config.data_dir, probes), err):
        return EXIT_STARTUP_FAILED

    lock = InstanceLock(config.data_dir / LOCK_FILE_NAME)
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / LOGS_DIR_NAME).mkdir(exist_ok=True)
        lock.acquire()
    except LockHeldError:
        print(f"another Nextron instance is running for {config.env}", file=err)
        return EXIT_LOCK_HELD
    except OSError as exc:
        print(f"cannot prepare {exc.filename or config.data_dir}: {exc.strerror or exc}", file=err)
        return EXIT_STARTUP_FAILED
    try:
        configure_logging(config.data_dir / LOGS_DIR_NAME)
        port_check = check_port_free(HOST, config.port, probes)
        if _report_failures([port_check], err):
            log.error("startup_check_failed", extra={"check": port_check.name})
            return EXIT_STARTUP_FAILED
        try:
            units, db_revision = open_database(config)
        except (DatabaseStateError, ConnectionSettingError) as exc:
            print(f"startup check failed: {DATABASE_REVISION_CHECK}: {exc}", file=err)
            log.error("startup_check_failed", extra={"check": DATABASE_REVISION_CHECK})
            return EXIT_STARTUP_FAILED
        except Exception:
            print(
                "database startup failed: it could not be opened, or a migration failed and "
                f"was rolled back; the traceback is in "
                f"{config.data_dir / LOGS_DIR_NAME / LOG_FILE_NAME}",
                file=err,
            )
            log.exception("database_migration_failed")
            return EXIT_STARTUP_FAILED
        try:
            identity = {"env": config.env, "version": __version__}
            log.info("engine_started", extra={**identity, "db_revision": db_revision})
            exit_code = EXIT_CRASHED
            try:
                run_server(config, reload, units)
                exit_code = EXIT_OK
            except SystemExit as exc:
                if exc.code in (None, 0):
                    exit_code = EXIT_OK
                else:
                    print(f"server failed to start (Uvicorn exit code {exc.code})", file=err)
                    log.error("server_start_failed", extra={"exit_code": exc.code})
                    exit_code = EXIT_STARTUP_FAILED
            except Exception as exc:
                log.exception(CRASH_EVENT)
                mark_logged(exc)  # the uncaught-exception hook would log it a second time
                raise
            finally:
                log.info("engine_stopped", extra={**identity, "exit_code": exit_code})
            return exit_code
        finally:
            units.engine.dispose()
    finally:
        lock.release()


def doctor(
    *,
    probes: Probes | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Print every startup check as PASS or FAIL; return 0 when none failed, else 1."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    probes = probes if probes is not None else Probes.system()
    results = all_checks(config.data_dir, HOST, config.port, probes)
    width = max(len(result.name) for result in results)
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status}  {result.name:<{width}}  {result.detail}", file=out)
    return EXIT_OK if all(result.ok for result in results) else EXIT_CHECKS_FAILED


def open_dashboard(
    *,
    secrets: SecretsPort | None = None,
    clock: Clock | None = None,
    engine_running: EngineProbe = engine_is_running,
    browser: Browser = webbrowser.open,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Open the dashboard in the default browser through a one-time code; return the exit
    code. Never prints or logs the code."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    if not engine_running(HOST, config.port):
        print(ENGINE_NOT_RUNNING, file=err)
        return EXIT_OPEN_FAILED
    entry = build_entry(config, secrets=secrets, clock=clock)
    try:
        code = entry.issue_code()
    except AdapterError:
        print(CODE_NOT_STORED, file=err)
        return EXIT_OPEN_FAILED
    try:
        opened = bool(browser(f"http://{HOST}:{config.port}/enter?t={code}"))
    except Exception:  # noqa: BLE001 - any browser failure withdraws the code
        opened = False
    if not opened:
        with contextlib.suppress(AdapterError):
            entry.withdraw_code()
        print(BROWSER_NOT_OPENED, file=err)
        return EXIT_OPEN_FAILED
    print(DASHBOARD_OPENED, file=out)
    return EXIT_OK
