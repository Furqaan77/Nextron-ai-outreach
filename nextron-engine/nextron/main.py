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

**Jobs (AD-10).** ``build_job_types(*, alerts)`` is the job type registry: the only code that
constructs ``JobTypes`` or calls ``register_job_type``. It registers each story's ``JobType``
(lanes, priority, ``max_runtime``, retry limit, a thin handler from ``nextron.jobs.handlers``
and the health panel's ``JobGroup``, always named), then freezes it. So far: ``send_alert``
(Story 1.12: the realtime lane only, priority 90, ``max_runtime`` 3 minutes, retry limit 5,
``handlers.send_alert(alerts)``, group ``other``: the panel's groups have none for alerts).
``build_app`` builds one ``JobQueue(clock, job_types)`` and one ``Runner(units, queue,
job_types, dead_jobs=DeadJobAlerts(notify))`` (``nextron.jobs.runner``).

**Notify and alerts (AD-15, INT-8).** ``build_app`` builds one ``Notify(clock, queue,
alert_ports=...)`` (``nextron.app.notify.api``), which owns the ``alerts`` and
``notifications`` tables and sends each Telegram alert as a ``send_alert`` job. Its Telegram
port is ``functools.partial(build_role_adapter, "telegram", AdapterContext(config, secrets,
clock, meter), units=units)``, built on the first send, never when the app is built. The registry
needs the handler, the handler needs ``Notify``, ``Notify`` needs the queue and the queue needs
the registry: ``build_job_types(alerts=lambda: notify)`` breaks the cycle, with ``notify`` bound
just below it. The runner's dead-job sweep (``DeadJobAlerts(notify)``, ``nextron.app.ops.api``)
raises the ``job_dead`` alert for every dead job, and the dashboard (``create_app(...,
notify=notify)``) shows the notifications and dismisses them.

**Metering and the budget (AD-8, AD-9, FR-57).** ``build_app`` builds one ``UsageMeter(units,
clock, after_record=lambda: check_usage(units, budget))`` (``nextron.db.metering``, the
``UsageMeterPort``), the only writer of ``api_usage`` and ``ai_usage``, and hands it to every
HTTP adapter: ``GoogleOAuthClient(meter=meter)`` and, through ``AdapterContext.meter``,
``TelegramAlert(c.secrets, meter=c.meter)``. Each adapter records every request it attempts;
each record commits in its own unit of work. One ``Budget(clock, meter, notify)``
(``nextron.app.ops.api``) reads the month's usage against the quotas and the AI budget: the
Quotas and cost page shows ``budget.status()`` (``create_app(..., budget=budget)``), and after
each record the meter calls ``check_usage(units, budget)``, which runs ``budget.check()`` in a
unit of work of its own inside a fresh ``contextvars.Context()`` and raises each threshold's
dashboard notice once a month. The meter needs the hook, the hook needs ``budget`` and
``budget`` needs the meter: the lambda breaks the cycle, with ``budget`` bound just below it.
Building the app builds no adapter or HTTP client and records nothing.

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

**Doctor (FR-56).** ``build_doctor_checks()`` is the doctor check registry: the only code that
constructs ``DoctorChecks`` or calls ``register_check`` (``nextron.startup_checks``). It
registers eleven ``DoctorCheck``s, each binding a pure check function to a ``DoctorContext``
(``config``, ``env_source``, ``probes``, ``secrets``, ``google``, ``database``), then freezes
it; later stories register their checks there. ``doctor(env=..., as_json=...)`` loads the
bootstrap config (``--env`` overrides ``NEXTRON_ENV`` for the run), runs the registry
(``run_checks``) and prints the results as a table, or as JSON, and nothing else. It exits 0
when nothing is ``fail`` (warnings allowed), 1 otherwise, and 2 when the bootstrap config is
invalid. It opens the database read-only (``DoctorDatabase``, on
``nextron.db.engine.create_read_only_engine``): it never creates, migrates or writes
``nextron.db``. It takes no lock, configures no logging and builds no app, runner, scheduler or
heartbeat writer. Its ``GoogleAccess`` refreshes the ``internal_notify`` grant once through
``GoogleOAuthClient(meter=DoctorMeter())``: the meter keeps that call in memory only, so doctor
writes no usage row; with no client or grant stored, or a grant already marked for re-consent,
nothing is sent to Google. Its browser always refuses and its allowlist is empty, because
doctor never starts a consent flow.

``DoctorContext.database`` is a ``ReadinessDatabase`` (``exists``, ``revision`` and
``required_unset``): ``DoctorDatabase`` for ``doctor``, ``EngineDatabase`` in the engine.

**Health (AD-10, AD-20, FR-58, OP-9).** ``build_health_issues(*, budget)`` is the health-issue
registry: the only code that constructs ``HealthIssues`` or calls ``register_source``
(``nextron.domain.health``; ``tests/contracts/test_health_contracts.py``). It registers
``IssueSource("jobqueue", queries.jobqueue.health_issues)`` (each dead job), then
``IssueSource("budget", budget.health_issues)`` (AI spend and quotas), then freezes it; each
later owner registers its ``queries.<owner>.health_issues`` there. ``build_app`` builds one
``EngineHealth(clock, job_types, issues=build_health_issues(budget=budget),
readiness=functools.partial(run_checks, build_doctor_checks(), context))``
(``nextron.app.ops.api``) and hands it, with the ``JobQueue`` (which dismisses a dead job), to
``create_app(..., health=health, queue=queue)``: the health panel (``GET /health/panel``)
shows its report and the notification area its error count.

**The doctor in the engine.** The panel's readiness list is the doctor registry itself, run on
``DoctorContext(config, ENV_SOURCE_VARIABLE, probes, secrets, google,
EngineDatabase(<data dir>\\nextron.db), in_engine=True)``, where ``probes`` is
``build_app``'s ``probes`` (``Probes.system()`` unless injected). The ids and their order are
doctor's; with ``in_engine`` only two checks change form: ``port_free`` probes nothing and is
``ok`` (the engine serving the page holds the port), and ``internal_notify_grant`` calls
``GoogleAccess.check_stored_grant`` with ``refresh=False``, which reads Credential Manager and
never calls Google, so a GET makes no refresh, no ``api_usage`` row and no re-consent mark
(``nextron doctor`` tests the refresh). ``EngineDatabase`` reads the revision
(``nextron.db.migrations.current_revision_in_unit``) and the required settings
(``queries.setup.required_unset``) in the caller's unit of work: the GET's read-only one; those
two checks come last, so the Credential Manager and registry reads before them never hold the
read transaction (``build_doctor_checks`` states the order rule). Building the app runs no
check.

``build_app`` wires the dashboard (AD-13): one ``KeyringSecrets(<env>)`` (Windows Credential
Manager, namespace ``nextron-engine/<env>``) serves its ``DashboardEntry``, which reads the
one-time code, its ``Credentials``, which store the external credentials, and its
``GoogleAccess``, which holds the Google client and grants; its ``Setup`` stores the settings;
all of them share one ``SystemClock``; and its handlers open their units of work through
``units``. Tests inject the secrets and the clock.

**Google OAuth (AD-12, AD-14).** ``GoogleAccess`` gets ``GoogleOAuthClient(meter=meter)``, which
builds its HTTP client only on its first call to Google; ``LoopbackListener``, the class, which
builds one listener per consent flow; ``LocalClientFiles()``; the environment; ``browser``,
which defaults to ``open_in_browser`` (``webbrowser.open``, looked up when it is called); and
``functools.partial(read_sink_allowlist, units)``, read only outside ``prod``. Building the app
therefore constructs no HTTP client, no listener and no browser. Tests inject ``google_oauth``
and ``browser`` (or build ``GoogleAccess`` themselves with fakes).

**Role adapters and the sink (AD-14).** Every adapter with the ``mail`` or ``alert`` role
(``SinkRole``) is registered once in ``ROLE_ADAPTERS`` as a ``RoleAdapter(name, role,
adapter_type, build)``, and built only by ``build_role_adapter(name, context, units=units)``,
the one function that reads ``ROLE_ADAPTERS``. It looks the name up (``LookupError`` unless it
is registered exactly once), calls ``build(AdapterContext(config, secrets, clock, meter))``,
and checks that the result is an ``adapter_type`` that implements its role's port
(``TypeError`` otherwise). In ``prod`` it returns that adapter. Anywhere else
(``nextron.domain.sink.sink_applies``) it returns the adapter wrapped in its role's sink
(``nextron.adapters.sink``: ``MailSink`` or ``AlertSink``), which records to
``<data dir>\\sink`` and reads the allowlist through ``read_sink_allowlist`` on every send. A
live ``mail`` or ``alert`` adapter could be obtained outside ``prod`` only by constructing it
directly, which ``tests/contracts/test_sink_contracts.py`` refuses anywhere in this module but
the ``ROLE_ADAPTERS`` assignment. The entries so far: ``telegram`` (``TelegramAlert``, the
``alert`` role, Story 1.12); Stories 6.x (Gmail) and 7.9 add the others.

``read_sink_allowlist(units)`` returns ``queries.setup.get(Key.SINK_ALLOWLIST)``, read in its
own read-only unit of work inside a fresh ``contextvars.Context()``: a port call happens inside
its caller's unit of work, between that unit's transactions, and a second ``units.open()`` in
the same context would raise. The read-only unit takes ``BEGIN IMMEDIATE`` on its first read,
so it must never run while the caller holds a transaction open.

**Seed sync (FR-1, AD-12, Story 2.1).** ``seed_sync(env=..., seed_dir=...)`` (``nextron seed
sync``) loads ``<seed_dir>\\segments.yaml`` (``SEED_DIR``, the engine's ``seed`` folder, unless
given) into the ``segments`` table, stopping at the first failure:

1. Bootstrap config (``--env`` overrides ``NEXTRON_ENV``): exit 2.
2. The file, read as UTF-8, at most 1 MiB: missing, unreadable, larger or not UTF-8 prints
   ``seed/segments.yaml: <reason>`` and exits 1.
3. ``nextron.domain.seed_yaml.parse``, then ``nextron.domain.segments.validate_segments``: a
   ``SeedSyntaxError`` or ``InvalidSegments`` prints ``seed/segments.yaml: <message>`` and exits
   1, before any database access.
4. ``<data dir>\\nextron.db`` must exist and be at ``head_revision()``, read with
   ``current_revision`` on ``create_read_only_engine`` (disposed at once), as doctor does. A
   missing or empty database, one behind or ahead of head, or one that isn't this engine's
   prints why and exits 1, creating and changing nothing: a seed sync never creates or
   migrates the database (only ``serve`` does, after its backup).
5. ``connect_database`` (never ``open_database``) and one unit of work running
   ``Setup(clock).sync_segments``. Its ``InvalidSegments`` (a signal-type or disqualifier key of
   the file that a stored Segment no longer in the file still uses) prints
   ``seed/segments.yaml: <message>`` and exits 1. Any other exception prints ``seed sync failed
   (<type>); nothing was changed`` and exits 1. Either way the transaction rolled back.
6. The summary on ``out`` (``format_seed_sync``) and exit 0: ``segments: <n> changed, <m>
   unchanged`` (n counts created plus changed), then a line per created, changed and
   not-in-file Segment.

It takes no lock, configures no logging, reads no Credential Manager entry and builds no app,
runner or scheduler; the engine is disposed in a ``finally``. A sync never re-scores a Lead.

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol, TextIO

import uvicorn
from fastapi import FastAPI

from nextron import __version__
from nextron.adapters.clock import SystemClock
from nextron.adapters.google_oauth import GoogleOAuthClient, LocalClientFiles, LoopbackListener
from nextron.adapters.secrets import KeyringSecrets
from nextron.adapters.sink import AlertSink, MailSink, SinkRecorder
from nextron.adapters.telegram import TelegramAlert
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Budget, DashboardEntry, DeadJobAlerts, EngineHealth, Heartbeat
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Credentials, GoogleAccess, SegmentSync, Setup
from nextron.bootstrap import ENGINE_ROOT, BootstrapConfig, BootstrapError, load_bootstrap
from nextron.db.engine import ConnectionSettingError, create_engine, create_read_only_engine
from nextron.db.metering import UsageMeter
from nextron.db.migrations import (
    DatabaseStateError,
    current_revision,
    current_revision_in_unit,
    ensure_current,
    head_revision,
    known_revisions,
)
from nextron.db.uow import Subscribers, UnitOfWorkFactory
from nextron.domain import seed_yaml, settings_schema
from nextron.domain.alert import SEND_ALERT_JOB, Channel
from nextron.domain.budget import AiCall, ApiCall, Usage, month_of
from nextron.domain.health import HealthIssues, IssueSource
from nextron.domain.job import JobGroup, JobType, JobTypes, Lane
from nextron.domain.seed_yaml import SeedSyntaxError
from nextron.domain.segments import InvalidSegments, SegmentDef, validate_segments
from nextron.domain.settings_schema import Key
from nextron.domain.sink import sink_applies
from nextron.jobs import handlers
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
from nextron.ports.usage_meter import UsageMeterPort
from nextron.startup_checks import (
    BACKUP_DIR_SEPARATE,
    DATA_DIR_OUTSIDE_ONEDRIVE,
    DATABASE_REVISION,
    INTERNAL_NOTIFY_GRANT,
    PORT_FREE,
    PROD_ENV_NOT_PERSISTENT,
    PYTHON_VERSION,
    REQUIRED_CREDENTIALS,
    REQUIRED_SETTINGS,
    SQLITE_VERSION,
    TZDATA,
    CheckFailed,
    CheckResult,
    DoctorCheck,
    DoctorChecks,
    Probes,
    Status,
    check_backup_dir_separate,
    check_data_dir_outside_onedrive,
    check_database_revision,
    check_internal_notify_grant,
    check_port_free,
    check_prod_env_not_persistent,
    check_python_version,
    check_required_credentials,
    check_required_settings,
    check_sqlite_version,
    check_tzdata,
    engine_is_running,
    environment_checks,
    format_json,
    format_table,
    run_checks,
)
from nextron.web.app import Lifespan, create_app
from nextron.web.routing import Units

HOST: Final = "127.0.0.1"
WORKERS: Final = 1
LOGS_DIR_NAME: Final = "logs"
DB_FILE_NAME: Final = "nextron.db"
SINK_DIR_NAME: Final = "sink"
HEARTBEAT_FILE_NAME: Final = "heartbeat"
DATABASE_REVISION_CHECK: Final = DATABASE_REVISION
ENV_SOURCE_FLAG: Final = "--env"
ENV_SOURCE_VARIABLE: Final = "NEXTRON_ENV"
PACKAGE_DIR: Final = Path(__file__).resolve().parent

EXIT_OK: Final = 0
EXIT_CRASHED: Final = 1  # serve: an unexpected error (a Python traceback)
EXIT_CHECKS_FAILED: Final = 1  # doctor: at least one check failed
EXIT_STARTUP_FAILED: Final = 2  # invalid config or a failed startup check
EXIT_LOCK_HELD: Final = 3  # another instance runs for this environment
EXIT_OPEN_FAILED: Final = 1  # open: the engine is down, or the code or the browser failed
EXIT_SEED_FAILED: Final = 1  # seed sync: the file, the database or the sync failed

SEED_DIR: Final = ENGINE_ROOT / "seed"
SEGMENTS_FILE_NAME: Final = "segments.yaml"
SEGMENTS_FILE_LABEL: Final = "seed/segments.yaml"  # how messages name the file
MAX_SEED_FILE_BYTES: Final = 1024 * 1024
SEED_DATABASE_MISSING: Final = (
    "the database doesn't exist yet: start the engine once (nextron serve), "
    "then run seed sync again"
)

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
    probes: Probes | None = None,
) -> FastAPI:
    """The dashboard app; its handlers open their units of work through ``units``. One secrets
    object, keyring unless injected, serves the entry code, the credentials and the Google
    grants; one clock, the system clock unless injected, serves them and the settings. Google
    is reached through ``google_oauth`` (``GoogleOAuthClient(meter=meter)`` unless injected)
    and the consent page opens with ``browser`` (``open_in_browser`` unless injected). The job
    runner, the scheduler and the heartbeat writer are built here and started by the app's
    lifespan (``engine_lifespan``), never by building; ``Notify`` builds its Telegram adapter
    on the first send. The usage meter records each adapter's requests and, after each record,
    runs the budget's threshold check (``check_usage``). The health panel's ``EngineHealth``
    runs the doctor registry in the engine on ``probes`` (``Probes.system()`` unless injected)
    when the panel is read, never when the app is built."""
    clock = clock if clock is not None else SystemClock()
    secrets = secrets if secrets is not None else KeyringSecrets(config.env)
    meter = UsageMeter(units, clock, after_record=lambda: check_usage(units, budget))
    google = GoogleAccess(
        secrets,
        clock,
        oauth=google_oauth if google_oauth is not None else GoogleOAuthClient(meter=meter),
        listen=LoopbackListener,
        files=LocalClientFiles(),
        browser=browser if browser is not None else open_in_browser,
        env=config.env,
        allowlist=functools.partial(read_sink_allowlist, units),
    )
    job_types = build_job_types(alerts=lambda: notify)  # notify is bound just below
    queue = JobQueue(clock, job_types)
    telegram = functools.partial(
        build_role_adapter, "telegram", AdapterContext(config, secrets, clock, meter), units=units
    )
    notify = Notify(clock, queue, alert_ports={Channel.TELEGRAM: telegram})
    budget = Budget(clock, meter, notify)  # the meter's hook reads it when it runs
    runner = Runner(units, queue, job_types, dead_jobs=DeadJobAlerts(notify))
    scheduler = Scheduler(units, queue, build_triggers(job_types))
    heartbeat = Heartbeat(clock, config.data_dir / HEARTBEAT_FILE_NAME)
    writer = HeartbeatWriter(units, heartbeat, runner, scheduler)
    context = DoctorContext(
        config,
        ENV_SOURCE_VARIABLE,
        probes if probes is not None else Probes.system(),
        secrets,
        google,
        EngineDatabase(config.data_dir / DB_FILE_NAME),
        in_engine=True,
    )
    health = EngineHealth(
        clock,
        job_types,
        issues=build_health_issues(budget=budget),
        readiness=functools.partial(run_checks, build_doctor_checks(), context),
    )
    return create_app(
        env=config.env,
        version=__version__,
        port=config.port,
        entry=build_entry(config, secrets=secrets, clock=clock),
        setup=Setup(clock),
        credentials=Credentials(secrets, clock),
        google=google,
        notify=notify,
        budget=budget,
        health=health,
        queue=queue,
        units=units,
        lifespan=engine_lifespan(runner, scheduler, writer),
    )


def check_usage(units: Units, budget: Budget) -> None:
    """The usage meter's ``after_record`` hook: ``budget.check()`` in a unit of work of its own,
    inside a fresh ``contextvars.Context()``, so it works whatever unit of work the recording
    adapter's caller has open."""

    def run() -> None:
        with units.open():
            budget.check()

    contextvars.Context().run(run)


def build_job_types(*, alerts: Callable[[], handlers.AlertSending]) -> JobTypes:
    """The job type registry (AD-10), frozen: register each story's job type with
    ``register_job_type(JobType(..., group=JobGroup.X))`` before ``freeze()``. ``alerts``
    returns the ``notify`` service that ``send_alert`` jobs call."""
    job_types = JobTypes()
    job_types.register_job_type(
        JobType(
            name=SEND_ALERT_JOB,
            default_lane=Lane.REALTIME,
            lanes=frozenset({Lane.REALTIME}),
            priority=90,
            max_runtime=timedelta(minutes=3),  # 10 s connect, 15 s per phase, database waits
            retry_limit=5,  # 30 s doubling: about 15 minutes of backoff
            handler=handlers.send_alert(alerts),
            group=JobGroup.OTHER,  # the health panel's groups have none for alerts
        )
    )
    job_types.freeze()
    return job_types


def build_health_issues(*, budget: Budget) -> HealthIssues:
    """The health-issue registry, frozen: jobqueue's dead jobs, then the budget's AI spend and
    quotas. Each later owner registers its ``queries.<owner>.health_issues`` here, before
    ``freeze()``."""
    issues = HealthIssues()
    issues.register_source(IssueSource("jobqueue", jobqueue_queries.health_issues))
    issues.register_source(IssueSource("budget", budget.health_issues))
    issues.freeze()
    return issues


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
    """What a role adapter's ``build`` may use: the bootstrap config, the environment's secrets,
    the clock and the usage meter."""

    config: BootstrapConfig
    secrets: SecretsPort
    clock: Clock
    meter: UsageMeterPort


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
# Story 1.12 added Telegram; Stories 6.x and 7.9 add the others. Only build_role_adapter reads
# it.
ROLE_ADAPTERS: tuple[RoleAdapter, ...] = (
    RoleAdapter(
        "telegram", SinkRole.ALERT, TelegramAlert, lambda c: TelegramAlert(c.secrets, meter=c.meter)
    ),
)

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
    failures = [result for result in results if result.status is Status.FAIL]
    for result in failures:
        print(f"startup check failed: {result.id}: {result.message}", file=err)
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
            log.error("startup_check_failed", extra={"check": port_check.id})
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


class DoctorMeter:
    """``doctor``'s ``UsageMeterPort``: it keeps each recorded call in memory (``calls``) and
    writes nothing, so doctor's own Google refresh never reaches ``api_usage`` or the Quotas
    page. ``snapshot`` returns an empty ``Usage``."""

    def __init__(self) -> None:
        self.calls: list[ApiCall | AiCall] = []

    def record(self, call: ApiCall | AiCall) -> None:
        self.calls.append(call)

    def snapshot(self, now: datetime) -> Usage:
        return Usage(month=month_of(now), api=(), ai_calls=0, ai_cost_micros=0)


class ReadinessDatabase(Protocol):
    """What the ``required_settings`` and ``database_revision`` checks read: whether the
    database file exists, its revision (``None`` when empty; a database that isn't this
    engine's raises ``CheckFailed``) and the required settings that are unset or no longer
    valid."""

    def exists(self) -> bool: ...

    def revision(self) -> str | None: ...

    def required_unset(self) -> tuple[str, ...]: ...


class DoctorDatabase:
    """``doctor``'s read-only view of the database file at ``path``: it never creates,
    migrates or writes it. The read-only engine (``create_read_only_engine``) is built on first
    use; ``close()`` disposes it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._units: UnitOfWorkFactory | None = None

    def exists(self) -> bool:
        """Whether the database file is there."""
        return self._path.exists()

    def _read_only_units(self) -> UnitOfWorkFactory:
        if self._units is None:
            engine = create_read_only_engine(self._path)
            try:
                self._units = UnitOfWorkFactory(engine, build_subscribers())
            except BaseException:
                engine.dispose()
                raise
        return self._units

    def revision(self) -> str | None:
        """The database's revision, ``None`` for an empty one. A database that isn't this
        engine's raises ``CheckFailed`` with the reason."""
        try:
            return current_revision(self._read_only_units().engine)
        except (DatabaseStateError, ConnectionSettingError) as exc:
            raise CheckFailed(str(exc)) from None

    def required_unset(self) -> tuple[str, ...]:
        """The required settings that are unset or no longer valid: every one when there is no
        database file, else ``queries.setup.required_unset()`` in a read-only unit of work."""
        if not self.exists():
            return tuple(
                str(found.key) for found in settings_schema.declared() if found.required
            )
        with self._read_only_units().open(read_only=True):
            return setup_queries.required_unset()

    def close(self) -> None:
        """Dispose the read-only engine, if one was built."""
        units, self._units = self._units, None
        if units is not None:
            units.engine.dispose()


class EngineDatabase:
    """The engine's view of its own database file at ``path``, for the health panel's
    readiness list: the revision and the required settings are read in the caller's unit of
    work (a dashboard GET's read-only one), never in one of their own."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def exists(self) -> bool:
        """Whether the database file is there."""
        return self._path.exists()

    def revision(self) -> str | None:
        """The database's revision, ``None`` for an empty one, read in the current unit of
        work. A database that isn't this engine's raises ``CheckFailed`` with the reason."""
        try:
            return current_revision_in_unit()
        except (DatabaseStateError, ConnectionSettingError) as exc:
            raise CheckFailed(str(exc)) from None

    def required_unset(self) -> tuple[str, ...]:
        """The required settings that are unset or no longer valid, read in the current unit
        of work."""
        return setup_queries.required_unset()


@dataclass(frozen=True, slots=True)
class DoctorContext:
    """What the doctor checks read: the bootstrap config, where its environment came from
    (``--env`` or ``NEXTRON_ENV``), the machine probes, the environment's secrets, its Google
    access and its database, read-only. ``in_engine`` is set when the engine runs the checks
    for its health panel: ``port_free`` then probes nothing and ``internal_notify_grant`` reads
    only what is stored, never refreshing at Google."""

    config: BootstrapConfig
    env_source: str
    probes: Probes
    secrets: SecretsPort
    google: GoogleAccess
    database: ReadinessDatabase = field(repr=False)
    in_engine: bool = False


def build_doctor_checks() -> DoctorChecks[DoctorContext]:
    """The doctor check registry (FR-56), frozen: the eleven checks of Story 1.14, in the order
    doctor prints them (the health panel shows the same list). With ``DoctorContext.in_engine``,
    ``port_free`` and ``internal_notify_grant`` take their no-probe, no-network forms. Later
    stories register their checks here, before ``freeze()``.

    The order rule: the health panel runs this registry inside a GET's read-only unit of work,
    where ``required_settings`` and ``database_revision`` begin the read transaction (AD-2's
    ``BEGIN IMMEDIATE``). Any check that reads Credential Manager, the registry or the network
    goes before ``required_settings``, so it never runs while that lock is held. A check that
    calls the network also needs an in-engine form (``c.in_engine``) that doesn't, because a GET
    makes no network call."""
    checks: DoctorChecks[DoctorContext] = DoctorChecks()
    for check in (
        DoctorCheck(PYTHON_VERSION, lambda c: check_python_version(c.probes)),
        DoctorCheck(SQLITE_VERSION, lambda c: check_sqlite_version(c.probes)),
        DoctorCheck(TZDATA, lambda c: check_tzdata(c.probes)),
        DoctorCheck(
            PROD_ENV_NOT_PERSISTENT,
            lambda c: check_prod_env_not_persistent(c.probes, c.config.env, c.env_source),
        ),
        DoctorCheck(
            DATA_DIR_OUTSIDE_ONEDRIVE,
            lambda c: check_data_dir_outside_onedrive(c.config.data_dir, c.probes),
        ),
        DoctorCheck(
            BACKUP_DIR_SEPARATE,
            lambda c: check_backup_dir_separate(c.config.data_dir, c.config.backup_dir, c.probes),
        ),
        DoctorCheck(
            PORT_FREE,
            lambda c: check_port_free(
                HOST,
                c.config.port,
                c.probes,
                heartbeat=c.config.data_dir / HEARTBEAT_FILE_NAME,
                in_engine=c.in_engine,
            ),
        ),
        DoctorCheck(REQUIRED_CREDENTIALS, lambda c: check_required_credentials(c.secrets.read)),
        DoctorCheck(INTERNAL_NOTIFY_GRANT, _internal_notify_grant),
        DoctorCheck(
            REQUIRED_SETTINGS, lambda c: check_required_settings(c.database.required_unset)
        ),
        DoctorCheck(
            DATABASE_REVISION_CHECK,
            lambda c: check_database_revision(
                c.database.exists(),
                c.database.revision,
                head=head_revision(),
                known=known_revisions(),
            ),
        ),
    ):
        checks.register_check(check)
    checks.freeze()
    return checks


def _internal_notify_grant(c: DoctorContext) -> CheckResult:
    """``doctor`` refreshes the grant at Google; the engine only reads what is stored."""
    if c.in_engine:
        return check_internal_notify_grant(c.google.check_stored_grant, refresh=False)
    return check_internal_notify_grant(c.google.access_token)


def _never_open_browser(url: str) -> bool:
    """``doctor``'s browser: doctor never starts a consent flow, so it never opens one."""
    return False


def doctor(
    *,
    env: str | None = None,
    as_json: bool = False,
    probes: Probes | None = None,
    secrets: SecretsPort | None = None,
    google_oauth: GoogleOAuthPort | None = None,
    clock: Clock | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run every doctor check for ``env`` (``NEXTRON_ENV`` unless given) and print the results
    as a table, or as JSON with ``as_json``; return 0 when nothing is ``fail``, 1 otherwise and
    2 for an invalid bootstrap config. Secrets are keyring, Google is ``GoogleOAuthClient`` with
    a ``DoctorMeter`` and the clock is the system clock, unless injected."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap(env)
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    probes = probes if probes is not None else Probes.system()
    clock = clock if clock is not None else SystemClock()
    secrets = secrets if secrets is not None else KeyringSecrets(config.env)
    google = GoogleAccess(
        secrets,
        clock,
        oauth=google_oauth if google_oauth is not None else GoogleOAuthClient(meter=DoctorMeter()),
        listen=LoopbackListener,
        files=LocalClientFiles(),
        browser=_never_open_browser,
        env=config.env,
        allowlist=tuple,  # doctor never starts a consent flow, so it reads no allowlist
    )
    database = DoctorDatabase(config.data_dir / DB_FILE_NAME)
    context = DoctorContext(
        config=config,
        env_source=ENV_SOURCE_FLAG if env is not None else ENV_SOURCE_VARIABLE,
        probes=probes,
        secrets=secrets,
        google=google,
        database=database,
    )
    try:
        results = run_checks(build_doctor_checks(), context)
    finally:
        database.close()
    print(format_json(results) if as_json else format_table(results), file=out)
    failed = any(result.status is Status.FAIL for result in results)
    return EXIT_CHECKS_FAILED if failed else EXIT_OK


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


# Seed sync ------------------------------------------------------------------------------------


class _SeedFileProblem(Exception):
    """The seed file can't be used; the message is the reason, safe to print."""


def _read_seed_file(path: Path) -> str:
    """The text of the seed file at ``path``: UTF-8, at most ``MAX_SEED_FILE_BYTES``."""
    try:
        with path.open("rb") as file:
            data = file.read(MAX_SEED_FILE_BYTES + 1)
    except FileNotFoundError:
        raise _SeedFileProblem("the file doesn't exist") from None
    except OSError as exc:
        raise _SeedFileProblem(f"the file can't be read ({type(exc).__name__})") from None
    if len(data) > MAX_SEED_FILE_BYTES:
        raise _SeedFileProblem("the file is larger than 1 MiB")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _SeedFileProblem("the file isn't valid UTF-8") from None


def _seed_failed(exc: BaseException) -> str:
    return f"seed sync failed ({type(exc).__name__}); nothing was changed"


def _seed_database_problem(path: Path) -> str | None:
    """Why ``seed sync`` can't write the database at ``path``, or ``None`` when it exists and is
    at head. Reads it read-only, as doctor does; never creates or changes it."""
    if not path.exists():
        return SEED_DATABASE_MISSING
    try:
        engine = create_read_only_engine(path)
        try:
            revision = current_revision(engine)
        finally:
            engine.dispose()
    except (DatabaseStateError, ConnectionSettingError) as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - not a database this engine can read
        return _seed_failed(exc)
    if revision is None:  # an empty file: serve migrates it like a new one
        return SEED_DATABASE_MISSING
    head = head_revision()
    if revision == head:
        return None
    if revision not in known_revisions():
        return f"database revision {revision} is newer than this code"
    return (
        f"the database is at {revision}, not {head}: start the engine once (nextron serve) "
        "to migrate it, then run seed sync again"
    )


def format_seed_sync(result: SegmentSync) -> str:
    """``seed sync``'s summary: the counts, then a line per created, changed and not-in-file
    Segment."""
    lines = [
        f"segments: {len(result.created) + len(result.changed)} changed, "
        f"{len(result.unchanged)} unchanged"
    ]
    lines += [f"  {key}: created (version {version})" for key, version in result.created]
    lines += [
        f"  {key}: changed (version {before} -> {after})" for key, before, after in result.changed
    ]
    lines += [
        f"  {key}: not in {SEGMENTS_FILE_LABEL}, left as it is (version {version})"
        for key, version in result.not_in_file
    ]
    return "\n".join(lines)


def seed_sync(
    *,
    env: str | None = None,
    seed_dir: Path | None = None,
    clock: Clock | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Load ``<seed_dir>\\segments.yaml`` into the ``segments`` table for ``env``
    (``NEXTRON_ENV`` unless given); return the exit code. See the module docstring."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap(env)
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    folder = seed_dir if seed_dir is not None else SEED_DIR
    try:
        text = _read_seed_file(folder / SEGMENTS_FILE_NAME)
    except _SeedFileProblem as exc:
        print(f"{SEGMENTS_FILE_LABEL}: {exc}", file=err)
        return EXIT_SEED_FAILED
    try:
        definitions: tuple[SegmentDef, ...] = validate_segments(seed_yaml.parse(text))
    except (SeedSyntaxError, InvalidSegments) as exc:
        print(f"{SEGMENTS_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    problem = _seed_database_problem(config.data_dir / DB_FILE_NAME)
    if problem is not None:
        print(problem, file=err)
        return EXIT_SEED_FAILED
    setup = Setup(clock if clock is not None else SystemClock())
    units: UnitOfWorkFactory | None = None
    try:
        units = connect_database(config)
        with units.open():
            result = setup.sync_segments(definitions)
    except InvalidSegments as exc:  # a key a stored Segment left out of the file still uses
        print(f"{SEGMENTS_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    except Exception as exc:  # noqa: BLE001 - the transaction rolled back
        print(_seed_failed(exc), file=err)
        return EXIT_SEED_FAILED
    finally:
        if units is not None:
            units.engine.dispose()
    print(format_seed_sync(result), file=out)
    return EXIT_OK
