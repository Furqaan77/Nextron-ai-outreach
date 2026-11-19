"""The engine's lifespan (``nextron.main.build_app`` and ``engine_lifespan``, AD-2, AD-10,
AD-20).

With recording fakes: startup runs the recovery startup pass (off the loop; Story 1.19), the
startup step (off the loop; Story 3.2), then the runner (off the loop), the scheduler (on the
loop's thread) and the heartbeat writer (off the loop) in that order, shutdown stops them in
reverse, and each part that started is stopped after a later start, a stop or the server
failed; a failed recovery startup pass stops the start before the startup step runs, while a
failed startup step is logged (``startup_step_failed``, its type only) and the engine still
starts. On the real ``build_app`` (Story 3.2), the startup
step queues one ``resolve_contact_markets`` job when a Contact is unresolved, and none
otherwise; Story 4.5 adds the ``queue_test_runs`` step after it (``run_startup_steps``: a step
that raises is logged with its name and the next still runs), which queues one
``run_ai_test_sets`` job per stored extraction tuple. On a real database at head, driven
through the ASGI lifespan by hand: startup starts the named threads, beats once (the
``runner_heartbeat`` row and ``<data dir>\\heartbeat``),
shutdown stops them, and building the app starts none.

The health panel (Story 1.15), on the real ``build_app`` with injected secrets, fake probes and
``FakeGoogleOAuth``: building runs no readiness check; ``GET /health/panel`` shows the fifteen
doctor rows in order (``port_free`` ok without a probe, the grant ok without a refresh) and the
dead job, and writes nothing; a wiring that ran doctor's own forms would be caught.

Backups (Story 1.16), on the real ``build_app`` over ``open_database(config)`` with its lifespan
running on a thread of its own: the scheduler registers the ``backup_db`` cron trigger (10:00
UTC, no catch-up on a new database), a ``POST /health/backup`` is run by the real runner into
``<backup dir>\\local`` (a ``verified`` ``manual`` row), and a failed backup shows in the
panel's error list. Off-PC copies (Story 1.17): with a Backup passphrase in ``InMemorySecrets``
and ``backup_offpc_dir`` set to a temporary folder, the same POST leaves a ``copied`` row and a
``.db.enc`` that the real adapter decrypts to the row's SHA-256, and no planted secret reaches a
file, a row, the log or the panel.
"""

import asyncio
import contextlib
import dataclasses
import functools
import hashlib
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx2
import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

import nextron.adapters.telegram as telegram_adapter
from nextron.adapters.backup_target import FolderBackupTarget
from nextron.adapters.crypto import ScryptParams
from nextron import main
from nextron.domain.ai_test_gate import ROUTE_SETS
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Recovery
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.app.research.finding import SearchQueued
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.metering import UsageMeter
from nextron.db.migrations import head_revision
from nextron.db.uow import UnitOfWorkFactory, current, in_transaction, use_case
from nextron.domain import seed_yaml
from nextron.domain.alert import Channel
from nextron.domain.budget import ApiCall
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.google_oauth import (
    CLIENT_SECRET_NAME,
    ClientConfig,
    GrantMeta,
    encode_client_config,
    encode_grant_meta,
)
from nextron.domain.job import Job, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.playbook import PlaybookSnapshot
from nextron.domain.recovery import (
    RecoveryContext,
    RecoveryHandler,
    RecoveryStep,
    RecoverySteps,
    StepStatus,
)
from nextron.domain.segments import validate_segments
from nextron.domain.settings_schema import Key
from nextron.jobs import scheduler as scheduler_module
from nextron.jobs.runner import Runner
from nextron.main import AdapterContext
from nextron.ports.alert import AlertMessage
from nextron.ports.dns import MxRecord
from nextron.ports.email_finder import VerifierAnswer
from tests.asgi import request
from tests.dashboard import body_hx_headers
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.dns import FakeDns
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder, ScriptedVerifier
from tests.fakes.google_oauth import CLIENT_ID, FakeGoogleOAuth
from tests.fakes.probes import PortProbe, fake_probes
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits
from tests.live_guard import allow_live_constructor

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
PLAYBOOK = PlaybookSnapshot("1.0.0", "---\nversion: 1.0.0\n---\n\nA test Sales Playbook.\n")
JOB = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
EXPECTED_THREADS = [
    "nextron-job-bulk-1",
    "nextron-job-heartbeat",
    "nextron-job-interactive-1",
    "nextron-job-keeper",
    "nextron-job-realtime-1",
]
LIFESPAN_LOGGERS = ("nextron.jobs.runner", "nextron.jobs.scheduler", "nextron.jobs.heartbeat")


def no_backups() -> Any:
    """``build_job_types``'s ``backups`` where no ``backup_db`` job runs."""
    raise AssertionError("no backup_db job runs in these tests")


def no_restore_tests() -> Any:
    """``build_job_types``'s ``restore_tests`` where no ``restore_test`` job runs."""
    raise AssertionError("no restore_test job runs in these tests")


def no_recovery() -> Any:
    """``build_job_types``'s ``recovery`` where no ``check_recovery`` job runs."""
    raise AssertionError("no check_recovery job runs in these tests")


def no_contact_markets() -> Any:
    """``build_job_types``'s ``contact_markets`` where no ``resolve_contact_markets`` job runs."""
    raise AssertionError("no resolve_contact_markets job runs in these tests")


def no_crawls() -> Any:
    """``build_job_types``'s ``crawls``, ``discovery`` and ``research`` where no Story 2.5 job
    runs."""
    raise AssertionError("no enrich_lead, canonicalize_lead or purge_page_text job runs here")


def job_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("nextron-job-"))


def build(units: UnitOfWorkFactory) -> FastAPI:
    return main.build_app(
        load_bootstrap(), units=units, secrets=InMemorySecrets("test"), clock=FrozenClock(T)
    )


def drive_lifespan(app: FastAPI) -> tuple[list[str], list[str]]:
    """Run the app's lifespan startup and shutdown; return the messages it sent and the job
    threads alive once startup completed."""
    sent: list[str] = []
    during: list[str] = []

    async def run() -> None:
        incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await incoming.put({"type": "lifespan.startup"})

        async def receive() -> dict[str, Any]:
            return await incoming.get()

        async def send(message: dict[str, Any]) -> None:
            sent.append(message["type"])
            if message["type"] == "lifespan.startup.complete":
                during.extend(job_threads())
                await incoming.put({"type": "lifespan.shutdown"})

        await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)

    asyncio.run(run())
    return sent, during


# The real app ------------------------------------------------------------------------------------


def test_building_the_app_starts_no_thread(units: UnitOfWorkFactory) -> None:
    before = set(threading.enumerate())

    build(units)

    assert job_threads() == []
    assert set(threading.enumerate()) == before
    assert not (load_bootstrap().data_dir / main.HEARTBEAT_FILE_NAME).exists()


def test_serving_registers_send_alert_and_builds_no_adapter_or_client(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[object] = []
    registries: list[object] = []
    real_build_job_types = main.build_job_types

    def recording_job_types(**kwargs: Any) -> Any:
        registries.append(real_build_job_types(**kwargs))
        return registries[-1]

    monkeypatch.setattr(main, "build_job_types", recording_job_types)
    monkeypatch.setattr(main, "build_role_adapter", lambda *args, **kwargs: built.append(args))
    load_bootstrap().data_dir.mkdir(parents=True)
    app = build(units)

    sent, _ = drive_lifespan(app)

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    [job_types] = registries
    assert job_types.names() == (  # type: ignore[attr-defined]
        "send_alert",
        "backup_db",
        "restore_test",
        "check_recovery",
        "resolve_contact_markets",
        "enrich_lead",
        "canonicalize_lead",
        "render_pages",
        "purge_page_text",
        "purge_place_coords",
        "extract_lead",
        "check_legal_form",
        "select_primary_contact",
        "run_ai_test_sets",
        "check_domains",
        "check_job_boards",
        "recheck_event",
        "erase_external",
        "find_contact_email",
        "verify_contact_email",
        "score_lead",
        "recompute_tiers",
        "poll_unsubscribes",
        "discover_places",
        "process_signal_item",  # Story 2.16
        "check_grounding",  # Story 4.14
        "discover_registry",
    )
    assert built == []  # no role adapter, so no Telegram client and no request
    with units.open(read_only=True):
        assert notify_queries.open_notifications(5).count == 0


def spy[T](monkeypatch: pytest.MonkeyPatch, name: str) -> list[Any]:
    """Every object ``main.<name>`` builds while the test runs, built by the real class."""
    built: list[Any] = []
    real = getattr(main, name)

    def build_and_keep(*args: Any, **kwargs: Any) -> Any:
        built.append(real(*args, **kwargs))
        return built[-1]

    monkeypatch.setattr(main, name, build_and_keep)
    return built


@pytest.mark.parametrize("env", ["test", "prod"])
def test_build_app_wires_the_erasure_queue_stores_and_sink(
    env: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 3.14: ``Erasures`` enqueues on the app's own queue; ``ErasureStores`` gets the dev
    and test sink's ``SinkRecorder`` (``<data dir>\\sink``) outside ``prod`` and none in
    ``prod``; and the registry's ``erase_external`` handler reaches that ``ErasureStores``."""
    queues = spy(monkeypatch, "JobQueue")
    erasures = spy(monkeypatch, "Erasures")
    recorders = spy(monkeypatch, "SinkRecorder")
    registries = spy(monkeypatch, "build_job_types")
    given: list[tuple[Any, ...]] = []
    built: list[Any] = []
    real_stores = main.ErasureStores

    def stores(*args: Any) -> Any:
        given.append(args)
        built.append(real_stores(*args))
        return built[-1]

    monkeypatch.setattr(main, "ErasureStores", stores)
    config = load_bootstrap().model_copy(update={"env": env, "data_dir": tmp_path / env})

    main.build_app(config, units=FakeUnits(), secrets=InMemorySecrets(env), clock=FrozenClock(T))

    [queue] = queues
    [erasure] = erasures
    assert erasure._queue is queue
    [(clock, notify, sink)] = given
    if env == "prod":
        assert (sink, recorders) == (None, [])
    else:
        [recorder] = recorders
        assert sink is recorder and recorder.folder == tmp_path / env / "sink"
    calls: list[tuple[Any, ...]] = []
    [store] = built
    monkeypatch.setattr(store, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    [job_types] = registries
    handler = job_types.get("erase_external").handler
    payload = {"v": 1, "contact_id": "01JD0000000000000000000001", "store": "logs"}

    handler(Job("01JD0000000000000000000002", "erase_external", Lane.BULK, payload, 1))

    assert calls == [(("01JD0000000000000000000001", "logs"), {"last_attempt": False})]
    assert clock is store._clock and notify is not None


def raw_query(units: UnitOfWorkFactory, statement: str, params: tuple[object, ...] = ()) -> Any:
    assert units.engine.url.database
    connection = sqlite3.connect(units.engine.url.database, isolation_level=None)
    try:
        return connection.execute(statement, params).fetchall()
    finally:
        connection.close()


def test_the_built_renderer_fetches_through_the_crawls_own_crawler(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 2.6: one ``HttpCrawler`` serves the crawl and the renderer, so a render's page
    fetches are paced with the crawl's per site; building them launches no browser."""
    crawlers = spy(monkeypatch, "HttpCrawler")
    renderers = spy(monkeypatch, "PlaywrightRenderer")

    build(units)

    [crawler] = crawlers
    [renderer] = renderers
    assert renderer._fetcher is crawler  # noqa: SLF001 - the wiring under test
    assert renderer._browser is None  # noqa: SLF001 - default_browser, looked up per render


def test_the_built_ats_boards_fetch_through_the_crawls_own_crawler(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 2.11: the ATS boards' requests go through the crawl's ``HttpCrawler`` (filtered,
    paced and metered there, as ``ats``); building them fetches nothing."""
    crawlers = spy(monkeypatch, "HttpCrawler")
    boards = spy(monkeypatch, "AtsBoards")

    build(units)

    [crawler] = crawlers
    [built] = boards
    assert built._fetcher is crawler  # noqa: SLF001 - the wiring under test


def test_the_built_runner_sweeps_dead_jobs_into_job_dead_alerts(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    runners = spy(monkeypatch, "Runner")
    build(units)
    [runner] = runners
    assert isinstance(runner, Runner)
    job_id = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
    raw_query(  # a queued job of a type no longer registered: its claim makes it dead
        units,
        "INSERT INTO jobs (id, type, lane, status, priority, payload, idempotency_key, "
        "concurrency_key, run_after, attempts, lease_until, last_error, created_at, "
        "updated_at) VALUES (?, 'retire_lead', 'bulk', 'queued', 50, '{\"v\":1}', "
        "'retire_lead:global:tick', NULL, '2026-11-17T09:00:00.000000Z', 0, NULL, NULL, "
        "'2026-11-17T09:00:00.000000Z', '2026-11-17T09:00:00.000000Z')",
        (job_id,),
    )

    assert runner.run_one(Lane.BULK) is False  # claimed straight to dead; nothing ran
    runner.keep_once()  # the keeper's tick runs the sweep

    text = f"Job dead: retire_lead {job_id}, attempts: 1"
    assert raw_query(units, "SELECT kind, subject, text FROM notifications") == [
        ("job_dead", job_id, text)
    ]
    assert raw_query(units, "SELECT kind, subject, channel, status FROM alerts") == [
        ("job_dead", job_id, "telegram", "pending")
    ]


def test_the_built_notify_sends_telegram_alerts_through_the_role_adapter(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    port = InMemoryAlert()

    def build_role_adapter(*args: Any, **kwargs: Any) -> InMemoryAlert:
        calls.append((args, kwargs))
        return port

    monkeypatch.setattr(main, "build_role_adapter", build_role_adapter)
    notifies = spy(monkeypatch, "Notify")
    runners = spy(monkeypatch, "Runner")
    config, secrets, clock = load_bootstrap(), InMemorySecrets("test"), FrozenClock(T)
    main.build_app(config, units=units, secrets=secrets, clock=clock)
    [notify], [runner] = notifies, runners
    assert isinstance(notify, Notify)
    assert calls == []  # building the app builds no adapter
    with units.open():
        Setup(clock).set(Key.ALERT_CHAT_ID, "-1001234567890")
    subject = "01JD3QZ8X5V9K2M7N4P6R8T0WY"

    @use_case
    def raise_alert() -> None:
        with current().transaction():
            notify.alert("job_dead", subject, "Job dead: x, attempts: 1", [Channel.TELEGRAM])

    with units.open():
        raise_alert()

    assert runner.run_one(Lane.REALTIME) is True

    [(args, kwargs)] = calls
    assert (args[0], kwargs) == ("telegram", {"units": units})
    context = args[1]
    assert isinstance(context, AdapterContext)
    assert context.secrets is secrets and context.clock is clock and context.config is config
    assert isinstance(context.meter, UsageMeter)
    assert port.sent == [AlertMessage("-1001234567890", "Job dead: x, attempts: 1")]
    assert raw_query(units, "SELECT status FROM alerts") == [("sent",)]


# Metering wiring ----------------------------------------------------------------------------------

TELEGRAM_TOKEN = "123456789:AAHk3Wq9Zr5Lp2Xc8Vn4Bm7Ty1Ju6Gd0Fs"
CHAT = "-1001234567890"


class NullMeter:
    """A broken wiring's meter: it takes every record and keeps none."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def record(self, call: object) -> None:
        del call

    def snapshot(self, now: datetime) -> Any:
        raise AssertionError("not read here")


@pytest.mark.parametrize("wiring", ["real", "broken"])
def test_a_telegram_send_through_the_built_notify_writes_one_api_usage_row(
    wiring: str, units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 7}})

    allow_live_constructor(monkeypatch, "nextron.main", "telegram")  # the real registration
    monkeypatch.setattr(
        telegram_adapter,
        "default_http_client",
        lambda: httpx2.Client(transport=httpx2.MockTransport(answer)),
    )
    if wiring == "broken":
        monkeypatch.setattr(main, "UsageMeter", NullMeter)
    runners = spy(monkeypatch, "Runner")
    notifies = spy(monkeypatch, "Notify")
    clock, secrets = FrozenClock(T), InMemorySecrets("test")
    secrets.write("telegram/bot_token", TELEGRAM_TOKEN)
    main.build_app(load_bootstrap(), units=units, secrets=secrets, clock=clock)
    [runner], [notify] = runners, notifies
    with units.open():
        Setup(clock).save({"alert_chat_id": CHAT, "sink_allowlist": CHAT})  # sink passes it

    @use_case
    def raise_alert() -> None:
        with current().transaction():
            notify.alert("job_dead", JOB, "Job dead: x, attempts: 1", [Channel.TELEGRAM])

    with units.open():
        raise_alert()
    assert runner.run_one(Lane.REALTIME) is True

    assert len(requests) == 1  # the real adapter sent it, through the sink
    assert raw_query(units, "SELECT status FROM alerts") == [("sent",)]
    rows = raw_query(units, "SELECT provider, operation, sku, units, ok, error_code FROM api_usage")
    if wiring == "real":
        assert rows == [("telegram", "send_message", None, 1, 1, None)]
    else:
        assert rows == []  # the check fails when the meter isn't the built one


@pytest.mark.parametrize("wiring", ["real", "broken"])
def test_a_record_over_a_quota_adds_its_notification_through_the_built_meter(
    wiring: str, units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    if wiring == "broken":
        monkeypatch.setattr(main, "check_usage", lambda units, budget: None)
    meters = spy(monkeypatch, "UsageMeter")
    clients = spy(monkeypatch, "GoogleOAuthClient")
    build(units)
    [meter], [client] = meters, clients
    assert client._meter is meter  # the Google client records through the same meter

    meter.record(ApiCall("hunter", "find", ok=True, count=50))  # 100 units: 50 credits

    assert raw_query(units, "SELECT provider, units FROM api_usage") == [("hunter", 100)]
    notices = raw_query(units, "SELECT kind, subject, text FROM notifications")
    if wiring == "real":
        assert notices == [
            (
                "quota_reached",
                "hunter:2026-11",
                "Hunter quota reached: enrichment stopped (50 of 50 credits used in 2026-11)",
            )
        ]
    else:
        assert notices == []  # the check fails when the hook isn't wired


def test_building_the_app_records_nothing(units: UnitOfWorkFactory) -> None:
    build(units)

    assert raw_query(units, "SELECT COUNT(*) FROM api_usage") == [(0,)]
    assert raw_query(units, "SELECT COUNT(*) FROM ai_usage") == [(0,)]


# The health panel wiring ------------------------------------------------------------------------

REQUIRED_SECRETS = (
    "anthropic/api_key",
    "hunter/api_key",
    "companies_house/api_key",
    "telegram/bot_token",
    "unsub/bearer",
)
DOCTOR_IDS = [
    "python_version",
    "sqlite_version",
    "tzdata",
    "prod_env_not_persistent",
    "data_dir_outside_onedrive",
    "backup_dir_separate",
    "port_free",
    "required_credentials",
    "internal_notify_grant",
    "offpc_backup",
    "erasure_ledger",  # Story 3.16
    "required_settings",
    "database_revision",
    "unsub_token_keys",
    "unsub_worker",
]
REFRESH_TOKEN = "1//stored-refresh-token"


class Rows(HTMLParser):
    """Every table row's cells, and every other non-blank text, of a page."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.texts: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td":
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif data.strip():
            self.texts.append(" ".join(data.split()))


def database_dump(units: UnitOfWorkFactory) -> list[str]:
    assert units.engine.url.database
    connection = sqlite3.connect(units.engine.url.database, isolation_level=None)
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def test_building_the_app_runs_no_readiness_check(units: UnitOfWorkFactory) -> None:
    probed: list[tuple[Any, ...]] = []

    def probe(*args: Any) -> Any:
        probed.append(args)  # run_checks would turn a raise into a failed row, so record
        raise AssertionError("building the app runs no readiness check")

    secrets = InMemorySecrets("test")
    probes = fake_probes(
        sqlite_version=probe,
        python_version=probe,
        load_zone=probe,
        port_is_free=probe,
        persistent_variable=probe,
        file_age=probe,
    )

    main.build_app(
        load_bootstrap(), units=units, secrets=secrets, clock=FrozenClock(T), probes=probes
    )

    assert probed == []
    # no credential or grant was read, but for Story 8.2's one Places key presence check
    # (main.build_places, outside prod: its value is discarded)
    assert secrets.calls == [("read", "google_places/api_key")]


def dead_send_alert(units: UnitOfWorkFactory, clock: FrozenClock) -> str:
    """A ``send_alert`` job that died on its first run; its id."""
    job_types = main.build_job_types(  # never called
        alerts=InMemoryAlert,
        backups=no_backups,
        restore_tests=no_restore_tests,
        recovery=no_recovery,
        contact_markets=no_contact_markets,
        crawls=no_crawls,
        discovery=no_crawls,
        research=no_crawls,
        extractions=no_crawls,
        primary=no_crawls,
        test_runs=no_crawls,
        job_boards=no_crawls,
        legal_forms=no_crawls,
        scores=no_crawls,
    )
    queue = JobQueue(clock, job_types)

    @use_case
    def enqueue() -> str:
        with current().transaction():
            return queue.enqueue(
                "send_alert", scope=JOB, discriminator="0", payload={"v": 1, "alert_id": JOB}
            ).job_id

    with units.open():
        job_id = enqueue()
    with units.open():
        claim = queue.claim(Lane.REALTIME)
    assert claim is not None and claim.job.id == job_id
    with units.open():
        queue.fail(claim, ValueError("broken"))
    return job_id


@pytest.mark.parametrize("wiring", ["real", "doctor-form"])
def test_the_built_health_panel_runs_doctor_in_the_engine_without_a_side_effect(
    wiring: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_bootstrap()
    config.data_dir.mkdir(parents=True)
    units, _ = main.open_database(config)  # the engine's own database file, at head
    try:
        if wiring == "doctor-form":  # a broken wiring: doctor's own forms in the engine
            real_context = main.DoctorContext

            def doctor_context(*args: Any, **kwargs: Any) -> Any:
                return real_context(*args, **{**kwargs, "in_engine": False})

            monkeypatch.setattr(main, "DoctorContext", doctor_context)
        clock, secrets = FrozenClock(T), InMemorySecrets("test")
        oauth, port = FakeGoogleOAuth(), PortProbe()
        for name in REQUIRED_SECRETS:
            secrets.write(name, "x" * 40)
        client = encode_client_config(ClientConfig(CLIENT_ID, "Cs4Vq8Lp2Xz6Nw1Rt5Hy"))
        secrets.write(CLIENT_SECRET_NAME, client)
        secrets.write("google/internal_notify", REFRESH_TOKEN)
        secrets.write("meta/google/internal_notify", encode_grant_meta(GrantMeta(None, T)))
        app = main.build_app(
            config,
            units=units,
            secrets=secrets,
            clock=clock,
            google_oauth=oauth,
            probes=fake_probes(port=port),
        )
        job_id = dead_send_alert(units, clock)
        code = main.build_entry(config, secrets=secrets, clock=clock).issue_code()
        host = f"127.0.0.1:{config.port}".encode()
        entered = request(app, "/enter", query=b"t=" + code.encode(), headers=[(b"host", host)])
        cookie = entered.header(b"set-cookie")
        assert entered.status == 303 and cookie is not None
        before = database_dump(units)
        read_in_transaction: list[tuple[str, bool]] = []
        real_read = secrets.read

        def recording_read(name: str) -> str | None:
            read_in_transaction.append((name, in_transaction()))
            return real_read(name)

        secrets.read = recording_read  # type: ignore[method-assign]

        reply = request(
            app, "/health/panel", headers=[(b"host", host), (b"cookie", cookie.split(b";")[0])]
        )

        assert reply.status == 200
        assert reply.header(b"cache-control") == b"no-store"
        page = Rows(reply.text)
        readiness = [row for row in page.rows if len(row) == 3]
        assert [row[1] for row in readiness] == DOCTOR_IDS
        statuses = {row[1]: (row[0], row[2]) for row in readiness}
        assert f"Job dead: send_alert {job_id}, attempts: 1" in page.texts
        assert raw_query(units, "SELECT COUNT(*) FROM api_usage") == [(0,)]
        if wiring == "real":
            assert database_dump(units) == before  # the GET wrote nothing
            assert statuses["port_free"] == (
                "ok",
                f"127.0.0.1:{config.port} is held by this engine, which serves this page",
            )
            assert statuses["internal_notify_grant"] == (
                "ok",
                "internal_notify is granted; the health panel doesn't refresh it "
                "(nextron doctor does)",
            )
            assert oauth.calls == [] and port.calls == []  # no refresh and no port probe
            # every Credential Manager read ran before the read transaction (and its lock), but
            # the last check's two: unsub_token_keys reads the active kid first (Story 3.8)
            inside = [name for name, inside in read_in_transaction if inside]
            assert inside == ["unsub/k_tok/1", "unsub/k_edge/1"]
            assert [name for name, _ in read_in_transaction[-2:]] == inside
        else:  # the test sees the broken wiring
            assert oauth.calls == [("refresh", REFRESH_TOKEN)]
            assert port.calls == [("127.0.0.1", config.port)]
        assert statuses["required_credentials"][0] == "ok"
        assert statuses["offpc_backup"] == (  # EngineDatabase.offpc_dir read the blank default
            "warn",
            "off-PC backup not configured (OQ-A3): backups are local only; no off-PC backup "
            "folder is set; set Off-PC backup folder (backup_offpc_dir) on the Settings page; "
            "the Backup passphrase is not set; enter it on the Credentials page",
        )
        assert statuses["required_settings"][0] == "fail"  # nothing is set yet
        assert statuses["database_revision"] == (
            "ok",
            f"the database is at head ({head_revision()})",
        )
        assert statuses["unsub_token_keys"] == (  # EngineDatabase read the default kid, 1
            "fail",
            "unsubscribe token keys of key id 1: unsub/k_tok/1 is not set; unsub/k_edge/1 is "
            "not set; enter them in Unsubscribe token keys on the Credentials page",
        )
    finally:
        units.engine.dispose()


def test_the_built_health_panel_never_probes_the_unsubscribe_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.fakes.unsubscribe import ScriptedUnsubscribe  # noqa: PLC0415

    config = load_bootstrap()
    config.data_dir.mkdir(parents=True)
    units, _ = main.open_database(config)
    try:
        clock, secrets, port = FrozenClock(T), InMemorySecrets("test"), ScriptedUnsubscribe()
        secrets.write("unsub/bearer", "Ub4Xq8Lm2Vz6Np1Rt5Hk9Jc3Wd7Gs0Fy4Tb8Mn2Qe6Ah")
        with units.open():
            Setup(clock).save({"unsub_worker_host": "u.example-sender.com"})
        app = main.build_app(
            config,
            units=units,
            secrets=secrets,
            clock=clock,
            google_oauth=FakeGoogleOAuth(),
            probes=fake_probes(),
            unsubscribe=port,
        )
        code = main.build_entry(config, secrets=secrets, clock=clock).issue_code()
        host = f"127.0.0.1:{config.port}".encode()
        entered = request(app, "/enter", query=b"t=" + code.encode(), headers=[(b"host", host)])
        cookie = entered.header(b"set-cookie")
        assert entered.status == 303 and cookie is not None

        reply = request(
            app, "/health/panel", headers=[(b"host", host), (b"cookie", cookie.split(b";")[0])]
        )

        assert reply.status == 200
        statuses = {row[1]: (row[0], row[2]) for row in Rows(reply.text).rows if len(row) == 3}
        assert statuses["unsub_worker"] == (
            "ok",
            "unsubscribe polling on; the Worker is not probed in the engine (nextron doctor "
            "probes it)",
        )
        assert (port.probes, port.hosts) == ([], [])  # no probe and no request
        assert raw_query(units, "SELECT COUNT(*) FROM api_usage") == [(0,)]
    finally:
        units.engine.dispose()


class BrokenEnviron(dict[str, str]):
    """An environment whose every read raises, so a check that reads it crashes."""

    def get(self, key: str, default: object = None) -> Any:
        raise RuntimeError("the environment could not be read")


@pytest.mark.parametrize(
    ("grant", "status", "says"),
    [
        ("missing", "fail", "internal_notify is not granted"),
        ("reconsent", "fail", "internal_notify needs re-consent"),
    ],
)
def test_the_built_panel_shows_a_grant_problem_and_a_crashed_check_without_calling_google(
    grant: str, status: str, says: str
) -> None:
    config = load_bootstrap()
    config.data_dir.mkdir(parents=True)
    units, _ = main.open_database(config)
    try:
        clock, secrets, oauth = FrozenClock(T), InMemorySecrets("test"), FakeGoogleOAuth()
        secrets.write(
            CLIENT_SECRET_NAME, encode_client_config(ClientConfig(CLIENT_ID, "Cs4Vq8Lp2Xz6"))
        )
        if grant == "reconsent":
            secrets.write("google/internal_notify", REFRESH_TOKEN)
            secrets.write(
                "meta/google/internal_notify", encode_grant_meta(GrantMeta(None, T, T))
            )
        app = main.build_app(
            config,
            units=units,
            secrets=secrets,
            clock=clock,
            google_oauth=oauth,
            probes=fake_probes(environ=BrokenEnviron()),
        )
        code = main.build_entry(config, secrets=secrets, clock=clock).issue_code()
        host = f"127.0.0.1:{config.port}".encode()
        entered = request(app, "/enter", query=b"t=" + code.encode(), headers=[(b"host", host)])
        cookie = entered.header(b"set-cookie")
        assert cookie is not None

        reply = request(
            app, "/health/panel", headers=[(b"host", host), (b"cookie", cookie.split(b";")[0])]
        )

        assert reply.status == 200
        readiness = [row for row in Rows(reply.text).rows if len(row) == 3]
        assert [row[1] for row in readiness] == DOCTOR_IDS  # every row, the crashed one too
        statuses = {row[1]: (row[0], row[2]) for row in readiness}
        assert statuses["internal_notify_grant"][0] == status
        assert says in statuses["internal_notify_grant"][1]
        assert statuses["data_dir_outside_onedrive"] == (
            "fail",
            "the check crashed (RuntimeError)",
        )
        assert oauth.calls == []
        assert raw_query(units, "SELECT COUNT(*) FROM api_usage") == [(0,)]
    finally:
        units.engine.dispose()


def store_unresolved_contact(units: UnitOfWorkFactory) -> None:
    """A Lead with one Contact whose Market was never resolved, as before Story 3.2."""
    clock = FrozenClock(T)
    segments_file = main.SEED_DIR / main.SEGMENTS_FILE_NAME
    segments = validate_segments(seed_yaml.parse(segments_file.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    job_types = main.build_job_types(  # none is called
        alerts=InMemoryAlert,
        backups=no_backups,
        restore_tests=no_restore_tests,
        recovery=no_recovery,
        contact_markets=no_contact_markets,
        crawls=no_crawls,
        discovery=no_crawls,
        research=no_crawls,
        extractions=no_crawls,
        primary=no_crawls,
        test_runs=no_crawls,
        job_boards=no_crawls,
        legal_forms=no_crawls,
        scores=no_crawls,
    )
    queue = JobQueue(clock, job_types)

    @use_case
    def store() -> None:
        with current().transaction():
            [segment] = [s for s in setup_queries.segments() if s.key == "law_private_ai"]
            identity = LeadIdentity(
                "Smith Law", segment.id, web_domain="smith.co.uk", country_code="GB"
            )
            lead = Discovery(clock, Pipeline(clock), queue).upsert_lead(identity, LeadSource.CSV)
            Research(clock).add_contact(
                lead.lead_id,
                ContactCandidate(name="Jane Doe"),
                source=ProvenanceSource.CSV,
                detail="a list",
                retrieved_at=T,
            )

    with units.open():
        store()
    raw_query(units, "UPDATE contacts SET market_basis = NULL, tz_basis = NULL")
    # the CSV Lead's crawl (Story 2.5) isn't under test: the runner must not fetch its website
    raw_query(units, "DELETE FROM jobs WHERE type = 'enrich_lead'")


def test_the_built_lifespan_queues_resolve_contact_markets_once_for_an_unresolved_contact(
    units: UnitOfWorkFactory,
) -> None:
    load_bootstrap().data_dir.mkdir(parents=True)
    store_unresolved_contact(units)

    sent, _ = drive_lifespan(build(units))

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    jobs = raw_query(
        units,
        "SELECT lane, idempotency_key, payload FROM jobs WHERE type = ?",
        ("resolve_contact_markets",),
    )
    assert jobs == [("bulk", "resolve_contact_markets:global:1", '{"v":1}')]


def test_the_built_lifespan_queues_nothing_when_every_contact_is_resolved(
    units: UnitOfWorkFactory,
) -> None:
    load_bootstrap().data_dir.mkdir(parents=True)

    sent, _ = drive_lifespan(build(units))

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    # purge_page_text's startup tick (Story 2.5) may be queued; resolve_contact_markets isn't
    jobs = "SELECT COUNT(*) FROM jobs WHERE type = 'resolve_contact_markets'"
    assert raw_query(units, jobs) == [(0,)]


def test_the_built_startup_queues_contact_markets_then_the_test_runs(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    startups: list[Callable[[], None]] = []
    real_lifespan = main.engine_lifespan

    def recording_lifespan(*args: Any, startup: Callable[[], None], **kwargs: Any) -> Any:
        startups.append(startup)
        return real_lifespan(*args, startup=startup, **kwargs)

    names: list[str] = []
    real_steps = main.run_startup_steps

    def recording_steps(steps: Any) -> None:
        names.extend(name for name, _ in steps)
        real_steps(steps)

    monkeypatch.setattr(main, "engine_lifespan", recording_lifespan)
    monkeypatch.setattr(main, "run_startup_steps", recording_steps)
    build(units)
    files, defaults = main.load_prompts()
    with units.open():
        main.Playbook(FrozenClock(T)).sync_prompts(files, defaults, PLAYBOOK)
    [startup] = startups

    startup()  # the lifespan's startup step alone: no runner starts

    assert names == ["sync_erasure_ledger", "queue_contact_markets", "queue_test_runs"]
    jobs = raw_query(
        units,
        "SELECT lane, idempotency_key FROM jobs WHERE type = 'run_ai_test_sets' "
        "ORDER BY idempotency_key",
    )
    tuples = raw_query(units, "SELECT id, route FROM ai_route_tuples ORDER BY id")
    assert jobs == [
        ("bulk", f"run_ai_test_sets:{row[0]}:{ROUTE_SETS[row[1]].value}") for row in tuples
    ]
    assert len(jobs) == 5  # the three extraction tuples and, Story 4.14, the two grounding ones


def test_a_failed_startup_step_is_logged_with_its_name_and_the_next_still_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ran: list[str] = []

    def broken() -> None:
        ran.append("queue_contact_markets")
        raise RuntimeError("the database is locked")

    main.run_startup_steps(
        (("queue_contact_markets", broken), ("queue_test_runs", lambda: ran.append("runs")))
    )

    assert ran == ["queue_contact_markets", "runs"]
    [failed] = [r for r in caplog.records if r.getMessage() == "startup_step_failed"]
    assert (failed.levelname, failed.step, failed.error) == (  # type: ignore[attr-defined]
        "ERROR",
        "queue_contact_markets",
        "RuntimeError",
    )
    assert "locked" not in caplog.text


def test_the_lifespan_starts_the_runner_scheduler_and_heartbeat_and_shutdown_stops_them(
    units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    data_dir = load_bootstrap().data_dir
    data_dir.mkdir(parents=True)
    app = build(units)

    sent, during = drive_lifespan(app)

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert during == EXPECTED_THREADS
    assert job_threads() == []
    events = [r.getMessage() for r in caplog.records if r.name in LIFESPAN_LOGGERS]
    assert events == [
        "job_runner_started",
        "daily_trigger_scheduled",  # backup_db
        "daily_trigger_scheduled",  # restore_test
        "daily_trigger_scheduled",  # check_domains (Story 6.5)
        "scheduler_started",
        "scheduler_stopped",
        "job_runner_stopped",
    ]
    [started] = [r for r in caplog.records if r.getMessage() == "job_runner_started"]
    assert started.bulk_threads == 1  # type: ignore[attr-defined]
    [scheduled] = [r for r in caplog.records if r.getMessage() == "scheduler_started"]
    # backup_db, restore_test, check_recovery, purge_page_text, purge_place_coords,
    # check_domains, recompute_tiers and poll_unsubscribes
    assert scheduled.triggers == 8  # type: ignore[attr-defined]
    # the writer beat once at startup: the row, then the file
    with units.open(read_only=True):
        row = ops_queries.heartbeat()
    assert row is not None
    assert (row.pid, row.started_at, row.beat_at) == (os.getpid(), T, T)
    assert (data_dir / main.HEARTBEAT_FILE_NAME).is_file()


# engine_lifespan with recording fakes -----------------------------------------------------------


class Failed(Exception):
    """A start, a stop or the server failed."""


class Parts:
    """A recording recovery startup pass, runner, scheduler and heartbeat writer sharing one
    call log of ``(call, thread)``; any call named in ``failing`` raises ``Failed`` after
    recording."""

    def __init__(self, *failing: str) -> None:
        self.failing = set(failing)
        self.calls: list[tuple[str, threading.Thread]] = []
        self.runner = _Part(self, "runner")
        self.scheduler = _SchedulerPart(self)
        self.writer = _Part(self, "writer")

    def record(self, call: str) -> None:
        self.calls.append((call, threading.current_thread()))
        if call in self.failing:
            raise Failed(call)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def recovery_startup(self) -> None:
        self.record("recovery.startup")

    def lifespan(self, *, startup: bool = False) -> main.Lifespan:
        return main.engine_lifespan(
            self.runner,  # type: ignore[arg-type]
            self.scheduler,  # type: ignore[arg-type]
            self.writer,  # type: ignore[arg-type]
            self.recovery_startup,
            startup=(lambda: self.record("startup")) if startup else None,
        )


class _Part:
    def __init__(self, parts: Parts, name: str) -> None:
        self.parts, self.name = parts, name

    def start(self) -> None:
        self.parts.record(f"{self.name}.start")

    def stop(self) -> None:
        self.parts.record(f"{self.name}.stop")


class _SchedulerPart:
    def __init__(self, parts: Parts) -> None:
        self.parts = parts

    def start(self) -> None:
        asyncio.get_running_loop()  # like AsyncIOScheduler.start: only on the loop
        self.parts.record("scheduler.start")

    async def stop(self) -> None:
        self.parts.record("scheduler.stop")


def serve(
    parts: Parts, *, server_fails: bool = False, startup: bool = False
) -> threading.Thread:
    """Run the lifespan around a server that returns (or raises ``Failed``); the loop's
    thread."""

    async def run() -> threading.Thread:
        async with parts.lifespan(startup=startup)(FastAPI()):
            parts.calls.append(("serving", threading.current_thread()))
            if server_fails:
                raise Failed("the server failed")
        return threading.current_thread()

    return asyncio.run(run())


def test_startup_runs_recovery_runner_scheduler_writer_and_shutdown_reverses_it() -> None:
    parts = Parts()

    loop_thread = serve(parts)

    assert parts.names() == [
        "recovery.startup",
        "runner.start",
        "scheduler.start",
        "writer.start",
        "serving",
        "writer.stop",
        "scheduler.stop",
        "runner.stop",
    ]
    on_loop = {name for name, thread in parts.calls if thread is loop_thread}
    assert on_loop == {"scheduler.start", "serving", "scheduler.stop"}


STARTUP_THEN_PARTS = [
    "recovery.startup",
    "startup",
    "runner.start",
    "scheduler.start",
    "writer.start",
    "serving",
    "writer.stop",
    "scheduler.stop",
    "runner.stop",
]


def test_the_startup_step_runs_off_the_loop_after_recovery_and_before_the_runner() -> None:
    parts = Parts()

    loop_thread = serve(parts, startup=True)

    assert parts.names() == STARTUP_THEN_PARTS
    [thread] = [thread for name, thread in parts.calls if name == "startup"]
    assert thread is not loop_thread


def test_a_failed_recovery_startup_stops_the_start_before_the_startup_step() -> None:
    parts = Parts("recovery.startup")

    with pytest.raises(Failed):
        serve(parts, startup=True)

    assert parts.names() == ["recovery.startup"]


def test_a_failed_startup_step_is_logged_by_type_and_the_engine_still_starts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    parts = Parts("startup")

    serve(parts, startup=True)

    assert parts.names() == STARTUP_THEN_PARTS
    [failed] = [r for r in caplog.records if r.getMessage() == "startup_step_failed"]
    assert (failed.name, failed.levelno) == ("nextron.main", logging.ERROR)
    assert failed.error == "Failed"  # type: ignore[attr-defined]
    assert failed.exc_info is None  # the type only: no traceback and no message


def test_every_part_stops_when_serving_fails() -> None:
    parts = Parts()

    with pytest.raises(Failed):
        serve(parts, server_fails=True)

    assert parts.names()[-3:] == ["writer.stop", "scheduler.stop", "runner.stop"]


@pytest.mark.parametrize(
    ("failing", "calls"),
    [
        ("recovery.startup", ["recovery.startup"]),
        ("runner.start", ["recovery.startup", "runner.start"]),
        ("scheduler.start", ["recovery.startup", "runner.start", "scheduler.start", "runner.stop"]),
        (
            "writer.start",
            [
                "recovery.startup",
                "runner.start",
                "scheduler.start",
                "writer.start",
                "scheduler.stop",
                "runner.stop",
            ],
        ),
    ],
)
def test_a_failed_start_stops_the_parts_that_started_in_reverse(
    failing: str, calls: list[str]
) -> None:
    parts = Parts(failing)

    with pytest.raises(Failed):
        serve(parts)

    assert parts.names() == calls


def test_a_failed_stop_still_stops_the_other_parts() -> None:
    parts = Parts("writer.stop")

    with pytest.raises(Failed):
        serve(parts)

    assert parts.names()[-3:] == ["writer.stop", "scheduler.stop", "runner.stop"]


# Backups through the real app (Story 1.16) -----------------------------------------------------

POLL_SLICE = 0.05  # the longest single wait
POLL_ATTEMPTS = 200  # 10 s: the bulk lane looks for work every second


@contextlib.contextmanager
def serving(app: FastAPI) -> Iterator[None]:
    """The app's lifespan started on its own thread and event loop, as Uvicorn would, while
    the test sends requests; shut down on exit."""
    started = threading.Event()
    shutdown: list[Callable[[], None]] = []
    problems: list[object] = []

    def serve() -> None:
        async def run() -> None:
            loop = asyncio.get_running_loop()
            incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            await incoming.put({"type": "lifespan.startup"})
            shutdown.append(
                lambda: loop.call_soon_threadsafe(
                    incoming.put_nowait, {"type": "lifespan.shutdown"}
                )
            )

            async def receive() -> dict[str, Any]:
                return await incoming.get()

            async def send(message: dict[str, Any]) -> None:
                if message["type"].endswith(".failed"):
                    problems.append(message)
                if message["type"].startswith("lifespan.startup."):
                    started.set()

            scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
            await app(scope, receive, send)

        try:
            asyncio.run(run())
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            problems.append(exc)
            started.set()

    thread = threading.Thread(target=serve, name="lifespan-under-test")
    thread.start()
    try:
        assert started.wait(30)
        assert problems == []
        yield
    finally:
        if shutdown:
            shutdown[0]()
        thread.join(60)
        assert not thread.is_alive()
    assert problems == []


@dataclasses.dataclass
class RealEngine:
    """The real ``build_app`` on ``open_database(config)``, with a dashboard session."""

    config: Any
    units: UnitOfWorkFactory
    app: FastAPI
    host: bytes
    cookie: bytes = b""
    token: str = ""

    def enter(self, secrets: InMemorySecrets, clock: FrozenClock) -> None:
        code = main.build_entry(self.config, secrets=secrets, clock=clock).issue_code()
        entered = request(
            self.app, "/enter", query=b"t=" + code.encode(), headers=[(b"host", self.host)]
        )
        cookie = entered.header(b"set-cookie")
        assert entered.status == 303 and cookie is not None
        self.cookie = cookie.split(b";")[0]
        token = body_hx_headers(self.get("/").text)["X-CSRF-Token"]
        assert isinstance(token, str)
        self.token = token

    def get(self, path: str) -> Any:
        reply = request(self.app, path, headers=[(b"host", self.host), (b"cookie", self.cookie)])
        assert reply.status == 200, reply
        return reply

    def back_up_now(self) -> Any:
        return self.post("/health/backup")

    def post(self, path: str) -> Any:
        return request(
            self.app,
            path,
            method="POST",
            headers=[
                (b"host", self.host),
                (b"cookie", self.cookie),
                (b"origin", b"http://" + self.host),
                (b"x-csrf-token", self.token.encode()),
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
        )

    def backups(self) -> list[tuple[str, str, str, str | None]]:
        return raw_query(self.units, "SELECT kind, status, path, error FROM backups")


def real_engine(
    monkeypatch: pytest.MonkeyPatch,
    secrets: InMemorySecrets | None = None,
    backup_targets: Any = None,
    recovery_steps: Any = None,
) -> RealEngine:
    monkeypatch.setattr(main, "build_role_adapter", lambda *args, **kwargs: InMemoryAlert())
    config = load_bootstrap()
    config.data_dir.mkdir(parents=True)
    units, _ = main.open_database(config)  # the file the snapshot is bound to
    clock = FrozenClock(T)
    secrets = secrets if secrets is not None else InMemorySecrets("test")
    app = main.build_app(
        config,
        units=units,
        secrets=secrets,
        clock=clock,
        google_oauth=FakeGoogleOAuth(),
        probes=fake_probes(),
        backup_targets=backup_targets,
        recovery_steps=recovery_steps,
    )
    engine = RealEngine(config, units, app, f"127.0.0.1:{config.port}".encode())
    engine.enter(secrets, clock)
    return engine


def wait_for_backup(engine: RealEngine) -> list[tuple[str, str, str, str | None]]:
    for _ in range(POLL_ATTEMPTS):
        rows = engine.backups()
        if rows:
            return rows
        time.sleep(POLL_SLICE)
    pytest.fail("the backup_db job did not record a backup")


def test_the_scheduler_registers_the_backup_db_cron_trigger(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    schedulers = spy(monkeypatch, "Scheduler")
    engine = real_engine(monkeypatch)
    jobs: list[Any] = []
    try:
        with serving(engine.app):
            [scheduler] = schedulers
            created = scheduler._scheduler  # noqa: SLF001 - the APScheduler it runs
            assert created is not None
            jobs.extend(created.get_jobs())
    finally:
        engine.units.engine.dispose()

    assert sorted(job.id for job in jobs) == [
        "backup_db",
        "check_domains",
        "check_recovery",
        "poll_unsubscribes",
        "purge_page_text",
        "purge_place_coords",
        "recompute_tiers",
        "restore_test",
    ]
    [check] = [job for job in jobs if job.id == "check_recovery"]
    assert isinstance(check.trigger, IntervalTrigger)
    assert check.trigger.interval == timedelta(minutes=1)
    assert (check.misfire_grace_time, check.coalesce, check.max_instances) == (60, True, 1)
    [poll] = [job for job in jobs if job.id == "poll_unsubscribes"]  # Story 3.11: test env
    assert isinstance(poll.trigger, IntervalTrigger)
    assert poll.trigger.interval == timedelta(minutes=30)
    assert (poll.misfire_grace_time, poll.coalesce, poll.max_instances) == (1800, True, 1)
    dailies = sorted(
        (
            job
            for job in jobs
            if job.id
            not in {
                "check_recovery",
                "purge_page_text",
                "purge_place_coords",
                "recompute_tiers",
                "poll_unsubscribes",
            }
        ),
        key=lambda job: job.id,
    )
    for job, hour in zip(dailies, ("10", "7", "11"), strict=True):  # check_domains at 07:00
        assert isinstance(job.trigger, CronTrigger)
        fields = {field.name: str(field) for field in job.trigger.fields}
        assert (fields["hour"], fields["minute"]) == (hour, "0")
        assert str(job.trigger.timezone) == "UTC"
        assert (job.misfire_grace_time, job.coalesce, job.max_instances) == (86400, True, 1)
    scheduled = [r for r in caplog.records if r.getMessage() == "daily_trigger_scheduled"]
    assert [getattr(record, "trigger") for record in scheduled] == [
        "backup_db",
        "restore_test",
        "check_domains",
    ]
    # a new database never catches up: no daily backup, no monthly restore test and no Sending
    # Domain yet
    assert [getattr(record, "catch_up") for record in scheduled] == [False, False, False]
    assert engine.backups() == []
    # the backup dir holds only the empty erasure ledger the startup sync made (Story 3.16)
    assert [path.name for path in engine.config.backup_dir.iterdir()] == ["erasure-ledger.jsonl"]
    assert (engine.config.backup_dir / "erasure-ledger.jsonl").read_bytes() == b""



def test_the_scheduler_reads_the_stored_time_zone_and_a_missed_daily_backup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    added: list[dict[str, Any]] = []

    class Recording(scheduler_module.AsyncIOScheduler):  # type: ignore[misc]
        def add_job(self, *args: Any, **kwargs: Any) -> Any:
            added.append({"trigger": args[1], **kwargs})
            return super().add_job(*args, **kwargs)

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", Recording)
    engine = real_engine(monkeypatch)
    try:
        with engine.units.open():
            Setup(FrozenClock(T)).set(Key.BACKUP_TIME, dt_time(7, 30))
        with engine.units.open():
            Setup(FrozenClock(T)).set(Key.SENDER_TIMEZONE, "America/New_York")
        # T is 04:00 in New York, so the latest 07:30 slot was 2026-11-16 12:30 UTC; the newest
        # daily backup started before it: the engine was off then
        raw_query(
            engine.units,
            "INSERT INTO backups (id, kind, status, started_at, finished_at, path, size_bytes, "
            "sha256, db_revision) VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0AB', 'daily', 'verified', "
            "'2026-11-15T12:30:00.000000Z', '2026-11-15T12:30:01.000000Z', 'gone.db', 1, ?, ?)",
            ("a" * 64, head_revision()),
        )
        before = datetime.now(UTC)
        with serving(engine.app):
            after = datetime.now(UTC)
            caught_up = wait_for_daily(engine)
    finally:
        engine.units.engine.dispose()

    [scheduled] = [
        r
        for r in caplog.records
        if r.getMessage() == "daily_trigger_scheduled" and getattr(r, "trigger") == "backup_db"
    ]
    assert (
        getattr(scheduled, "at"),
        getattr(scheduled, "timezone"),
        getattr(scheduled, "catch_up"),
    ) == ("07:30", "America/New_York", True)
    [restore] = [
        r
        for r in caplog.records
        if r.getMessage() == "daily_trigger_scheduled" and getattr(r, "trigger") == "restore_test"
    ]
    assert (
        getattr(restore, "at"),
        getattr(restore, "timezone"),
        getattr(restore, "catch_up"),
    ) == ("08:30", "America/New_York", False)  # an hour after the backup; no monthly test yet
    [job] = [job for job in added if job["id"] == "backup_db"]
    trigger = job["trigger"]
    assert isinstance(trigger, CronTrigger)
    fields = {field.name: str(field) for field in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("7", "30")
    assert str(trigger.timezone) == "America/New_York"
    assert before <= job["next_run_time"] <= after  # the catch-up: now
    assert caught_up == ("daily", "verified")  # and the runner took it


def wait_for_daily(engine: RealEngine) -> tuple[str, str]:
    """The catch-up daily backup's kind and status, once the runner has recorded it."""
    for _ in range(POLL_ATTEMPTS):
        rows = raw_query(
            engine.units,
            "SELECT kind, status FROM backups WHERE started_at = '2026-11-17T09:00:00.000000Z'",
        )
        if rows:
            return rows[0]
        time.sleep(POLL_SLICE)
    pytest.fail("the catch-up backup_db job did not record a backup")


def test_back_up_now_is_run_by_the_real_runner_into_backup_dir_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = real_engine(monkeypatch)
    try:
        with serving(engine.app):
            reply = engine.back_up_now()
            assert reply.status == 200
            assert "Backup queued; its result shows here when it finishes." in reply.text
            [(kind, status, path, error)] = wait_for_backup(engine)
            panel = engine.get("/health/panel/live").text
    finally:
        engine.units.engine.dispose()

    assert (kind, status, error) == ("manual", "verified", None)
    copy = Path(path)
    assert copy.parent == engine.config.backup_dir / "local"
    assert [found.name for found in copy.parent.iterdir()] == [copy.name]
    assert copy.name.startswith("nextron-test-20261117T090000.000Z")
    assert "Last backup: 2026-11-17 09:00:00 UTC, verified (manual)" in panel
    assert raw_query(engine.units, "SELECT status FROM jobs WHERE type = 'backup_db'") == [
        ("done",)
    ]


def test_a_failed_backup_appears_in_the_panels_error_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocked = tmp_path / "not-a-folder"
    blocked.write_bytes(b"a file where the backup dir should be")
    monkeypatch.setenv("NEXTRON_BACKUP_DIR", str(blocked))
    engine = real_engine(monkeypatch)
    try:
        with serving(engine.app):
            assert engine.back_up_now().status == 200
            [(kind, status, _, error)] = wait_for_backup(engine)
            panel = engine.get("/health/panel/live").text
    finally:
        engine.units.engine.dispose()

    assert (kind, status) == ("manual", "failed")
    assert error is not None
    [(backup_id,)] = raw_query(engine.units, "SELECT id FROM backups")
    assert f"Backup failed: manual {backup_id}" in panel
    assert 'class="health-backup health-backup-failed"' in panel
    assert raw_query(engine.units, "SELECT status FROM jobs WHERE type = 'backup_db'") == [
        ("done",)  # a failed copy never raises, so the job is not retried
    ]
    assert blocked.read_bytes() == b"a file where the backup dir should be"


PLANTED_PASSPHRASE = "QZXPLANTEDBACKUPPASSPHRASE horse battery"
PLANTED_KEY = "QZXPLANTEDHUNTERAPIKEY"


def wait_for_offpc(engine: RealEngine) -> tuple[str, str | None, str]:
    """The backup's off-PC status, its off-PC name and its SHA-256, once it is recorded."""
    for _ in range(POLL_ATTEMPTS):
        rows = raw_query(
            engine.units,
            "SELECT offpc_status, offpc_name, sha256 FROM backups WHERE offpc_status IS NOT NULL",
        )
        if rows:
            return rows[0]
        time.sleep(POLL_SLICE)
    pytest.fail("the backup_db job did not record the off-PC outcome")


def test_back_up_now_copies_the_backup_off_the_pc_encrypted_with_the_real_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    folder = tmp_path / "offpc"
    secrets = InMemorySecrets("test")
    secrets.write("backup/passphrase", PLANTED_PASSPHRASE)
    secrets.write("hunter/api_key", PLANTED_KEY)
    engine = real_engine(monkeypatch, secrets)  # the default backup_targets: the real adapter
    try:
        with engine.units.open():
            Setup(FrozenClock(T)).set(Key.BACKUP_OFFPC_DIR, str(folder))
        with serving(engine.app):
            assert engine.back_up_now().status == 200
            status, name, digest = wait_for_offpc(engine)
            panel = engine.get("/health/panel/live").text
        dump = database_dump(engine.units)
    finally:
        engine.units.engine.dispose()

    assert status == "copied"
    assert name == "nextron-test-20261117T090000.000Z.db.enc"
    # with its erasure ledger, which the startup sync made (Story 3.16)
    assert sorted(found.name for found in folder.iterdir()) == [
        name,
        "nextron-test-20261117T090000.000Z.ledger.jsonl.enc",
    ]
    sealed = (folder / name).read_bytes()
    assert sealed[:8] == b"NXBK\x01\x11\x08\x01"  # format v1, the default scrypt: 2**17, 8, 1
    restored = tmp_path / "restored.db"
    fetched = FolderBackupTarget(folder, secrets=secrets).get(name, restored)
    assert fetched.plain_sha256 == digest
    assert hashlib.sha256(restored.read_bytes()).hexdigest() == digest
    assert "Last backup: 2026-11-17 09:00:00 UTC, verified (manual), copied off the PC" in panel
    readiness = {row[1]: (row[0], row[2]) for row in Rows(panel).rows if len(row) == 3}
    assert readiness["offpc_backup"] == (  # tmp is on the data dir's drive
        "warn",
        f"off-PC backup folder {folder} is on the same drive as the data dir "
        f"({engine.config.data_dir.drive}); a failed or lost disk would take both, so choose a "
        "folder on another drive or a network share in Off-PC backup folder (backup_offpc_dir) "
        "on the Settings page",
    )
    assert raw_query(engine.units, "SELECT status FROM jobs WHERE type = 'backup_db'") == [
        ("done",)
    ]
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(files) >= 4  # the database, its local copy, the off-PC copy and the restore
    logged = caplog.text + "".join(str(vars(record)) for record in caplog.records)
    for planted in (PLANTED_PASSPHRASE, "QZXPLANTED", PLANTED_KEY):
        for path in files:
            data = path.read_bytes()
            assert planted.encode() not in data and planted.encode("utf-16-le") not in data
        assert all(planted not in line for line in dump)
        assert planted not in logged
        assert planted not in panel


# The restore test through the real app (Story 1.18) -------------------------------------------


def wait_for_restore_test(engine: RealEngine) -> tuple[str, str, str | None, str | None]:
    """The restore test's kind, result, reason and backup id, once the ``restore_test`` job
    recorded it and finished."""
    for _ in range(POLL_ATTEMPTS):
        rows = raw_query(engine.units, "SELECT kind, result, reason, backup_id FROM restore_tests")
        done = raw_query(engine.units, "SELECT status FROM jobs WHERE type = 'restore_test'")
        if rows and done == [("done",)]:
            return rows[0]
        time.sleep(POLL_SLICE)
    pytest.fail("the restore_test job did not record a restore test")


def test_run_restore_test_now_restores_the_off_pc_copy_with_the_real_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    folder = tmp_path / "offpc"
    secrets = InMemorySecrets("test")
    secrets.write("backup/passphrase", PLANTED_PASSPHRASE)
    targets = functools.partial(FolderBackupTarget, secrets=secrets, params=ScryptParams(10, 8, 1))
    engine = real_engine(monkeypatch, secrets, targets)
    try:
        with engine.units.open():
            Setup(FrozenClock(T)).set(Key.BACKUP_OFFPC_DIR, str(folder))
        with serving(engine.app):
            assert engine.back_up_now().status == 200
            status, name, _ = wait_for_offpc(engine)
            assert (status, name) == ("copied", "nextron-test-20261117T090000.000Z.db.enc")
            reply = engine.post("/health/restore-test")
            assert reply.status == 200
            assert "Restore test queued; its result shows here when it finishes." in reply.text
            kind, result, reason, backup_id = wait_for_restore_test(engine)
            panel = engine.get("/health/panel/live").text
    finally:
        engine.units.engine.dispose()

    [(copied_id,)] = raw_query(engine.units, "SELECT id FROM backups")
    assert (kind, result, reason, backup_id) == ("manual", "pass", None, copied_id)
    assert "Last restore test: 2026-11-17 09:00:00 UTC, pass (manual)" in panel
    assert 'class="health-restore"' in panel
    restore_dir = engine.config.backup_dir / "restore_test"
    assert restore_dir.is_dir() and list(restore_dir.iterdir()) == []  # no work folder left
    assert sorted(found.name for found in folder.iterdir()) == [  # the copy itself stays
        name,
        "nextron-test-20261117T090000.000Z.ledger.jsonl.enc",
    ]
    assert raw_query(engine.units, "SELECT count(*) FROM alerts") == [(0,)]
    logged = caplog.text + "".join(str(vars(record)) for record in caplog.records)
    assert PLANTED_PASSPHRASE not in logged and "QZXPLANTED" not in panel
    assert [r.getMessage() for r in caplog.records].count("restore_test_passed") == 1


# Recovery mode through the real app (Story 1.19) -------------------------------------------------


def turn_recovery_on(engine: RealEngine) -> str:
    """An ``on`` recovery row, as ``nextron restore`` leaves one; its ULID."""
    with engine.units.open():
        row = Recovery(FrozenClock(T)).begin(
            backup_at=T - timedelta(days=1),
            source_name="nextron-test-20261116T090000.000Z.db",
            pre_restore_name=None,
        )
    return row.id


def recovery_row(engine: RealEngine) -> Any:
    with engine.units.open(read_only=True):
        return ops_queries.recovery_state()


def wait_for_recovery_off(engine: RealEngine) -> Any:
    for _ in range(POLL_ATTEMPTS):
        row = recovery_row(engine)
        if row is not None and row.mode == "off":
            return row
        time.sleep(POLL_SLICE)
    pytest.fail("check_recovery did not end recovery mode")


def test_an_on_row_with_its_ledger_ends_after_the_start(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # build_app's own registry also holds Story 3.12's repoll_unsubscribes, which stays pending
    # while no Unsub Worker host is set: this registry keeps only Story 3.16's ledger step
    registry = main.build_recovery_steps
    monkeypatch.setattr(
        main,
        "build_recovery_steps",
        lambda reapply=None, repoll=None: registry(reapply=reapply),
    )
    engine = real_engine(monkeypatch)
    recovery_id = turn_recovery_on(engine)
    # Story 3.16's startup step reapplies the (empty) ledger; the other three have no handler
    engine.config.backup_dir.mkdir(parents=True, exist_ok=True)
    (engine.config.backup_dir / "erasure-ledger.jsonl").touch()
    try:
        with serving(engine.app):
            ended = wait_for_recovery_off(engine)
            assert "recovery-banner" not in engine.get("/notifications").text
            panel = engine.get("/health/panel/live").text
    finally:
        engine.units.engine.dispose()

    assert (ended.id, ended.ended_at) == (recovery_id, T)
    assert dict(ended.steps) == {
        RecoveryStep.REAPPLY_ERASURE_LEDGER: StepStatus.DONE,
        RecoveryStep.MARK_OMS_UNKNOWN: StepStatus.NOT_APPLICABLE,
        RecoveryStep.REINGEST_MAILBOXES: StepStatus.NOT_APPLICABLE,
        RecoveryStep.REPOLL_UNSUBSCRIBES: StepStatus.NOT_APPLICABLE,
    }
    assert "Last restore: 2026-11-17 09:00:00 UTC, from nextron-test-" in panel
    events = [r.getMessage() for r in caplog.records]
    # every step was settled before the runner started; the at-startup check ended it
    assert events.index("recovery_mode_on") < events.index("job_runner_started")
    last_marked = max(
        index
        for index, event in enumerate(events)
        if event == "recovery_step_not_applicable"
    )
    assert last_marked < events.index("job_runner_started")
    assert events.count("recovery_step_not_applicable") == 3
    assert events.index("recovery_step_done") < events.index("job_runner_started")
    assert events.index("job_runner_started") < events.index("recovery_mode_ended")
    [jobs] = raw_query(engine.units, "SELECT count(*) FROM jobs WHERE type = 'check_recovery'")
    assert jobs[0] >= 1


def test_while_recovery_mode_is_off_no_check_recovery_job_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[bool] = []
    real_read = main.read_recovery_mode

    def read_recovery_mode(units: Any) -> bool:
        answers.append(real_read(units))
        return answers[-1]

    monkeypatch.setattr(main, "read_recovery_mode", read_recovery_mode)
    engine = real_engine(monkeypatch)
    try:
        with serving(engine.app):
            for _ in range(POLL_ATTEMPTS):  # until the at-startup firing has asked
                if answers:
                    break
                time.sleep(POLL_SLICE)
    finally:
        engine.units.engine.dispose()

    assert answers[:1] == [False]
    assert raw_query(engine.units, "SELECT count(*) FROM jobs WHERE type = 'check_recovery'") == [
        (0,)
    ]


def test_an_injected_startup_handler_runs_before_the_runner_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, list[str]]] = []

    def at_start(context: RecoveryContext) -> bool:
        seen.append(("startup", job_threads()))
        return True

    def later(context: RecoveryContext) -> bool:
        seen.append(("check", job_threads()))
        return True

    steps = RecoverySteps()
    steps.register_step(
        RecoveryHandler(RecoveryStep.REAPPLY_ERASURE_LEDGER, "compliance", at_start, True)
    )
    steps.register_step(
        RecoveryHandler(RecoveryStep.REPOLL_UNSUBSCRIBES, "compliance", later, False)
    )
    steps.freeze()
    engine = real_engine(monkeypatch, recovery_steps=steps)
    turn_recovery_on(engine)
    try:
        with serving(engine.app):
            ended = wait_for_recovery_off(engine)
    finally:
        engine.units.engine.dispose()

    assert seen[0] == ("startup", [])  # before runner.start: no job thread yet
    assert [name for name, _ in seen] == ["startup", "check"]
    assert "nextron-job-realtime-1" in seen[1][1]  # check_recovery runs on the realtime lane
    assert dict(ended.steps) == {
        RecoveryStep.REAPPLY_ERASURE_LEDGER: StepStatus.DONE,
        RecoveryStep.MARK_OMS_UNKNOWN: StepStatus.NOT_APPLICABLE,
        RecoveryStep.REINGEST_MAILBOXES: StepStatus.NOT_APPLICABLE,
        RecoveryStep.REPOLL_UNSUBSCRIBES: StepStatus.DONE,
    }


def test_a_recovery_state_that_cannot_be_read_stops_the_start(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Unreadable(Recovery):
        def pending(self, registered: frozenset[RecoveryStep]) -> Any:  # type: ignore[override]
            raise OSError("the database is locked")

    monkeypatch.setattr(main, "Recovery", Unreadable)
    app = build(units)
    sent: list[str] = []

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "lifespan.startup"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message["type"])

        await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)

    with pytest.raises(OSError, match="locked"):  # Uvicorn exits 3, and serve reports exit 2
        asyncio.run(run())

    assert sent == ["lifespan.startup.failed"]
    assert job_threads() == []  # the runner never started


def test_the_built_app_wires_the_email_search_to_its_job_and_to_find_email(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 2.13: ``build_app`` gives its ``EmailFinding`` (on the injected finders) to the
    ``find_contact_email`` job and to the dashboard's "Find email"."""
    findings = spy(monkeypatch, "EmailFinding")
    registries = spy(monkeypatch, "build_job_types")
    given: dict[str, Any] = {}
    real_create_app = main.create_app

    def create_app(**kwargs: Any) -> FastAPI:
        given.update(kwargs)
        return real_create_app(**kwargs)

    monkeypatch.setattr(main, "create_app", create_app)
    searched, hunted, prospected = ScriptedDomainSearch(), ScriptedFinder(), ScriptedFinder()
    main.build_app(
        load_bootstrap(),
        units=units,
        secrets=InMemorySecrets("test"),
        clock=FrozenClock(T),
        domain_search=searched,
        hunter=hunted,
        prospeo=prospected,
    )
    [finding], [job_types] = findings, registries
    assert given["email_finding"] is finding  # "Find email" isn't the 503 answer
    store_unresolved_contact(units)
    [(lead_id, contact_id)] = raw_query(units, "SELECT lead_id, id FROM contacts")

    with units.open():
        assert given["email_finding"].queue_search(lead_id, contact_id) == SearchQueued.QUEUED
    handler = job_types.get("find_contact_email").handler
    payload = {"v": 1, "lead_id": lead_id, "contact_id": contact_id}
    with units.open():
        handler(Job("01JD3QZ8X5V9K2M7N4P6R8T0ZZ", "find_contact_email", Lane.BULK, payload, 1))

    assert searched.calls == ["smith.co.uk"]
    assert [query.domain for query in (*hunted.calls, *prospected.calls)] == [
        "smith.co.uk",
        "smith.co.uk",
    ]


def test_the_built_app_wires_the_address_checks_to_their_job(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 2.14: ``build_app`` gives its ``EmailVerification`` (on the injected DNS and
    verifier) to the ``verify_contact_email`` job."""
    registries = spy(monkeypatch, "build_job_types")
    dns = FakeDns(mx={"smith.co.uk": (MxRecord(10, "mx.smith.co.uk"),)})
    verifier = ScriptedVerifier(VerifierAnswer("valid"))
    main.build_app(
        load_bootstrap(),
        units=units,
        secrets=InMemorySecrets("test"),
        clock=FrozenClock(T),
        dns=dns,
        verifier=verifier,
    )
    [job_types] = registries
    store_unresolved_contact(units)
    raw_query(
        units,
        "UPDATE contacts SET email = 'jane@smith.co.uk', address_class = 'guessed', "
        "is_primary = 1, primary_set_by = 'operator'",
    )
    [(lead_id, contact_id)] = raw_query(units, "SELECT lead_id, id FROM contacts")
    handler = job_types.get("verify_contact_email").handler
    payload = {"v": 1, "lead_id": lead_id, "contact_id": contact_id}
    with units.open():
        handler(Job("01JD3QZ8X5V9K2M7N4P6R8T0ZZ", "verify_contact_email", Lane.BULK, payload, 1))

    assert dns.queries == [("mx", "smith.co.uk")]
    assert verifier.calls == ["jane@smith.co.uk"]
    assert raw_query(units, "SELECT address_class FROM contacts") == [("verified",)]
