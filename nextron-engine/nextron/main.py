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

**Jobs (AD-10).** ``build_job_types`` is the job type registry: the only code that constructs
``JobTypes`` or calls ``register_job_type``. It ships empty and frozen; each story that adds a
job type registers its ``JobType`` (lanes, priority, ``max_runtime``, retry limit and a thin
handler) there. ``build_app`` builds one ``JobQueue(clock, job_types)`` and one
``Runner(units, queue, job_types)`` (``nextron.jobs.runner``).

**Triggers (AD-10).** ``build_triggers(job_types)`` is the trigger registry: the only code that
constructs ``Triggers`` or calls ``register_trigger``. It ships empty and frozen, so the
scheduler runs with no triggers until a story adds one: register each AD-10 ``Trigger`` with an
``Every`` schedule there, after its job type (a firing enqueues the job type with the trigger's
own name). ``build_app`` builds one ``Scheduler(units, queue, triggers)``
(``nextron.jobs.scheduler``), an ``AsyncIOScheduler(timezone=UTC)`` whose firings only enqueue.

**The heartbeat (AD-20, NFR-14).** ``build_app`` builds one ``Heartbeat(clock, heartbeat
file)`` (``nextron.app.ops.api``), whose file is ``<data dir>\\heartbeat``
(``HEARTBEAT_FILE_NAME``), and one ``HeartbeatWriter(units, heartbeat, runner, scheduler)``
(``nextron.jobs.heartbeat``). Its ``nextron-job-heartbeat`` thread beats at once and every
30 s, writing the ``runner_heartbeat`` row and then touching the file, but only while every lane
thread and the keeper are alive, no running job has passed its ``max_runtime`` and the
scheduler's event loop answers its probe. The supervisor (``scripts/run.ps1``, Story 1.21)
restarts the engine when the file is more than 5 minutes old.

**The lifespan.** ``build_app`` hands ``engine_lifespan(runner, scheduler, writer)`` to
``create_app``, so all three start when the server starts and stop when it stops, in whichever
process serves the app (the reload worker included). Building the app starts nothing. Startup
runs ``runner.start`` through ``asyncio.to_thread``, then ``scheduler.start()`` on the event
loop (APScheduler needs the running loop), then ``writer.start`` through ``asyncio.to_thread``.
Shutdown runs in reverse: ``writer.stop`` (to a thread), ``await scheduler.stop()``, then
``runner.stop`` (to a thread), which waits up to 15 s for running handlers and then releases
their jobs back to the queue. Each part that started is stopped even when a later start, or the
server, failed.

With ``--reload`` (``dev`` only), Uvicorn serves from a worker process that does not write
``nextron.log``: only the reloader's own records reach the file. The worker's WARNING and above
go to stderr, redacted. The worker opens the database with ``connect_database``, which never
migrates: the reloader brought it to head before starting Uvicorn.

``doctor`` runs every check, prints PASS or FAIL for each, and exits 0 when none fail, 1 when
any fails, and 2 when ``NEXTRON_ENV`` or another bootstrap setting is invalid. It takes no lock,
creates nothing and starts no server.

``build_app`` wires the dashboard (AD-13): one ``KeyringSecrets(<env>)`` (Windows Credential
Manager, namespace ``nextron-engine/<env>``) serves its ``DashboardEntry``, which reads the
one-time code, its ``Credentials``, which store the external credentials, and its
``GoogleAccess``, which holds the Google client and grants; its ``Setup`` stores the settings;
all of them share one ``SystemClock``; and its handlers open their units of work through
``units``. Tests inject the secrets and the clock.

**Google OAuth (AD-12, AD-14).** ``GoogleAccess`` gets ``GoogleOAuthClient()``, which builds its
HTTP client only on its first call to Google; ``LoopbackListener``, the class, which builds one
listener per consent flow; ``LocalClientFiles()``; the environment; ``browser``, which defaults
to ``open_in_browser`` (``webbrowser.open``, looked up when it is called); and
``functools.partial(read_sink_allowlist, units)``, read only outside ``prod``. Building the app
therefore constructs no HTTP client, no listener and no browser. Tests inject ``google_oauth``
and ``browser`` (or build ``GoogleAccess`` themselves with fakes).

**Role adapters and the sink (AD-14).** Every adapter with the ``mail`` or ``alert`` role
(``SinkRole``) is registered once in ``ROLE_ADAPTERS`` as a ``RoleAdapter(name, role,
adapter_type, build)``, and built only by ``build_role_adapter(name, context, units=units)``,
the one function that reads ``ROLE_ADAPTERS``. It looks the name up (``LookupError`` unless it
is registered exactly once), calls ``build(AdapterContext(config, secrets, clock))``, and checks
that the result is an ``adapter_type`` that implements its role's port (``TypeError``
otherwise). In ``prod`` it returns that adapter. Anywhere else
(``nextron.domain.sink.sink_applies``) it returns the adapter wrapped in its role's sink
(``nextron.adapters.sink``: ``MailSink`` or ``AlertSink``), which records to
``<data dir>\\sink`` and reads the allowlist through ``read_sink_allowlist`` on every send. A
live ``mail`` or ``alert`` adapter could be obtained outside ``prod`` only by constructing it
directly, which ``tests/contracts/test_sink_contracts.py`` refuses anywhere in this module but
the ``ROLE_ADAPTERS`` assignment. The registry ships empty: Stories 1.12 (Telegram), 6.x (Gmail)
and 7.9 add the entries.

``read_sink_allowlist(units)`` returns ``queries.setup.get(Key.SINK_ALLOWLIST)``, read in its
own read-only unit of work inside a fresh ``contextvars.Context()``: a port call happens inside
its caller's unit of work, between that unit's transactions, and a second ``units.open()`` in
the same context would raise. The read-only unit takes ``BEGIN IMMEDIATE`` on its first read,
so it must never run while the caller holds a transaction open.

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

import asyncio
import contextlib
import contextvars
import functools
import logging
import sys
import webbrowser
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, TextIO

import uvicorn
from fastapi import FastAPI

from nextron import __version__
from nextron.adapters.clock import SystemClock
from nextron.adapters.google_oauth import GoogleOAuthClient, LocalClientFiles, LoopbackListener
from nextron.adapters.secrets import KeyringSecrets
from nextron.adapters.sink import AlertSink, MailSink, SinkRecorder
from nextron.app.jobqueue.api import JobQueue
from nextron.app.ops.api import DashboardEntry, Heartbeat
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Credentials, GoogleAccess, Setup
from nextron.bootstrap import BootstrapConfig, BootstrapError, load_bootstrap
from nextron.db.engine import ConnectionSettingError, create_engine
from nextron.db.migrations import DatabaseStateError, ensure_current
from nextron.db.uow import Subscribers, UnitOfWorkFactory
from nextron.domain.job import JobTypes
from nextron.domain.settings_schema import Key
from nextron.domain.sink import sink_applies
from nextron.jobs.heartbeat import HeartbeatWriter
from nextron.jobs.runner import Runner
from nextron.jobs.scheduler import Scheduler
from nextron.jobs.triggers import Triggers
from nextron.lock import LOCK_FILE_NAME, InstanceLock, LockHeldError
from nextron.logconfig import CRASH_EVENT, LOG_FILE_NAME, configure_logging, mark_logged
from nextron.ports.alert import AlertPort
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import GoogleOAuthPort
from nextron.ports.mail import MailPort
from nextron.ports.secrets import SecretsPort
from nextron.startup_checks import (
    CheckResult,
    Probes,
    all_checks,
    check_port_free,
    engine_is_running,
    environment_checks,
)
from nextron.web.app import Lifespan, create_app
from nextron.web.routing import Units

HOST: Final = "127.0.0.1"
WORKERS: Final = 1
LOGS_DIR_NAME: Final = "logs"
DB_FILE_NAME: Final = "nextron.db"
SINK_DIR_NAME: Final = "sink"
HEARTBEAT_FILE_NAME: Final = "heartbeat"
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


def open_in_browser(url: str) -> bool:
    """Open ``url`` in the default browser; whether it opened. ``webbrowser.open`` is looked up
    now, not when this module was imported."""
    return bool(webbrowser.open(url))


def build_app(
    config: BootstrapConfig,
    *,
    units: Units,
    secrets: SecretsPort | None = None,
    clock: Clock | None = None,
    google_oauth: GoogleOAuthPort | None = None,
    browser: Browser | None = None,
) -> FastAPI:
    """The dashboard app; its handlers open their units of work through ``units``. One secrets
    object, keyring unless injected, serves the entry code, the credentials and the Google
    grants; one clock, the system clock unless injected, serves them and the settings. Google
    is reached through ``google_oauth`` (``GoogleOAuthClient()`` unless injected) and the
    consent page opens with ``browser`` (``open_in_browser`` unless injected). The job runner,
    the scheduler and the heartbeat writer are built here and started by the app's lifespan
    (``engine_lifespan``), never by building."""
    clock = clock if clock is not None else SystemClock()
    secrets = secrets if secrets is not None else KeyringSecrets(config.env)
    google = GoogleAccess(
        secrets,
        clock,
        oauth=google_oauth if google_oauth is not None else GoogleOAuthClient(),
        listen=LoopbackListener,
        files=LocalClientFiles(),
        browser=browser if browser is not None else open_in_browser,
        env=config.env,
        allowlist=functools.partial(read_sink_allowlist, units),
    )
    job_types = build_job_types()
    queue = JobQueue(clock, job_types)
    runner = Runner(units, queue, job_types)
    scheduler = Scheduler(units, queue, build_triggers(job_types))
    heartbeat = Heartbeat(clock, config.data_dir / HEARTBEAT_FILE_NAME)
    writer = HeartbeatWriter(units, heartbeat, runner, scheduler)
    return create_app(
        env=config.env,
        version=__version__,
        port=config.port,
        entry=build_entry(config, secrets=secrets, clock=clock),
        setup=Setup(clock),
        credentials=Credentials(secrets, clock),
        google=google,
        units=units,
        lifespan=engine_lifespan(runner, scheduler, writer),
    )


def build_job_types() -> JobTypes:
    """The job type registry (AD-10), frozen. Empty until a story adds a job type: register each
    with ``register_job_type(JobType(...))`` before ``freeze()``."""
    job_types = JobTypes()
    job_types.freeze()
    return job_types


def build_triggers(job_types: JobTypes) -> Triggers:
    """The trigger registry (AD-10), frozen. Empty until a story adds a trigger: register each
    with ``register_trigger(Trigger.X, Every(...))``, after its job type, before ``freeze()``."""
    triggers = Triggers(job_types)
    triggers.freeze()
    return triggers


def engine_lifespan(
    runner: Runner, scheduler: Scheduler, writer: HeartbeatWriter
) -> Lifespan:
    """The app's lifespan: start the runner (on a worker thread), the scheduler (on the event
    loop) and the heartbeat writer (on a worker thread) when the server starts, and stop them
    in reverse when it stops. Each part that started is stopped even when a later start, or the
    server, failed."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as started:
            await asyncio.to_thread(runner.start)
            started.push_async_callback(asyncio.to_thread, runner.stop)
            scheduler.start()
            started.push_async_callback(scheduler.stop)
            await asyncio.to_thread(writer.start)
            started.push_async_callback(asyncio.to_thread, writer.stop)
            yield

    return lifespan


class SinkRole(StrEnum):
    """The roles whose adapters reach people, so are wrapped in a sink outside ``prod``."""

    MAIL = "mail"
    ALERT = "alert"


@dataclass(frozen=True, slots=True)
class AdapterContext:
    """What a role adapter's ``build`` may use: the bootstrap config, the environment's secrets
    and the clock."""

    config: BootstrapConfig
    secrets: SecretsPort
    clock: Clock


@dataclass(frozen=True, slots=True)
class RoleAdapter:
    """One registered ``mail`` or ``alert`` adapter: ``build(context)`` returns an
    ``adapter_type``."""

    name: str
    role: SinkRole
    adapter_type: type
    build: Callable[[AdapterContext], object]

    def __post_init__(self) -> None:
        if not (isinstance(self.name, str) and self.name):
            raise TypeError("a role adapter's name must be a non-empty str")
        if not isinstance(self.role, SinkRole):
            raise TypeError(f"role adapter {self.name}: the role must be a SinkRole")
        if not isinstance(self.adapter_type, type):
            raise TypeError(f"role adapter {self.name}: adapter_type must be a class")
        if not callable(self.build):
            raise TypeError(f"role adapter {self.name}: build must be callable")


# The role adapter registry (AD-14): the only place a mail or alert adapter is constructed.
# Stories 1.12, 6.x and 7.9 add the entries. Only build_role_adapter reads it.
ROLE_ADAPTERS: tuple[RoleAdapter, ...] = ()

ROLE_PORTS: Final[Mapping[SinkRole, type]] = MappingProxyType(
    {SinkRole.MAIL: MailPort, SinkRole.ALERT: AlertPort}
)
ROLE_SINKS: Final[Mapping[SinkRole, type[MailSink] | type[AlertSink]]] = MappingProxyType(
    {SinkRole.MAIL: MailSink, SinkRole.ALERT: AlertSink}
)


def read_sink_allowlist(units: Units) -> tuple[str, ...]:
    """The ``sink_allowlist`` setting, read in its own read-only unit of work inside a fresh
    ``contextvars.Context()``, so it works while the caller's unit of work is open (between its
    transactions)."""

    def read() -> tuple[str, ...]:
        with units.open(read_only=True):
            return tuple(setup_queries.get(Key.SINK_ALLOWLIST))

    return contextvars.Context().run(read)


def build_role_adapter(
    name: str, context: AdapterContext, *, units: Units
) -> AlertPort | MailPort:
    """Build the role adapter registered as ``name``; outside ``prod``, wrapped in its role's
    sink. Raises ``LookupError`` unless ``name`` is registered exactly once, and ``TypeError``
    when ``build`` returns something other than an ``adapter_type`` that implements the role's
    port."""
    matches = [entry for entry in ROLE_ADAPTERS if entry.name == name]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} adapters are registered as {name!r}; expected one")
    [entry] = matches
    adapter = entry.build(context)
    if not isinstance(adapter, entry.adapter_type):
        raise TypeError(
            f"role adapter {entry.name} built a {type(adapter).__name__}, "
            f"not a {entry.adapter_type.__name__}"
        )
    port = ROLE_PORTS[entry.role]
    if not isinstance(adapter, port):
        raise TypeError(f"role adapter {entry.name} does not implement {port.__name__}")
    if not sink_applies(context.config.env):
        return adapter  # type: ignore[return-value]  # an AlertPort or MailPort, checked above
    recorder = SinkRecorder(context.config.data_dir / SINK_DIR_NAME, context.clock)
    allowlist = functools.partial(read_sink_allowlist, units)
    return ROLE_SINKS[entry.role](adapter, allowlist, recorder)  # type: ignore[arg-type]


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
    browser: Browser | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Open the dashboard in the default browser (``browser``, ``open_in_browser`` unless
    injected) through a one-time code; return the exit code. Never prints or logs the code."""
    browser = browser if browser is not None else open_in_browser
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
