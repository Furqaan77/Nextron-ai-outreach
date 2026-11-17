"""The engine's lifespan (``nextron.main.build_app`` and ``engine_lifespan``, AD-2, AD-10,
AD-20).

With recording fakes: startup runs the runner (off the loop), the scheduler (on the loop's
thread) and the heartbeat writer (off the loop) in that order, shutdown stops them in reverse,
and each part that started is stopped after a later start, a stop or the server failed. On a
real database at head, driven through the ASGI lifespan by hand: startup starts the named
threads, beats once (the ``runner_heartbeat`` row and ``<data dir>\\heartbeat``), shutdown
stops them, and building the app starts none.

The health panel (Story 1.15), on the real ``build_app`` with injected secrets, fake probes and
``FakeGoogleOAuth``: building runs no readiness check; ``GET /health/panel`` shows the eleven
doctor rows in order (``port_free`` ok without a probe, the grant ok without a refresh) and the
dead job, and writes nothing; a wiring that ran doctor's own forms would be caught.
"""

import asyncio
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI

import nextron.adapters.telegram as telegram_adapter
from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.metering import UsageMeter
from nextron.db.migrations import head_revision
from nextron.db.uow import UnitOfWorkFactory, current, in_transaction, use_case
from nextron.domain.alert import Channel
from nextron.domain.budget import ApiCall
from nextron.domain.google_oauth import (
    CLIENT_SECRET_NAME,
    ClientConfig,
    GrantMeta,
    encode_client_config,
    encode_grant_meta,
)
from nextron.domain.job import Lane
from nextron.domain.settings_schema import Key
from nextron.jobs.runner import Runner
from nextron.main import AdapterContext
from nextron.ports.alert import AlertMessage
from tests.asgi import request
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.google_oauth import CLIENT_ID, FakeGoogleOAuth
from tests.fakes.probes import PortProbe, fake_probes
from tests.fakes.secrets import InMemorySecrets
from tests.live_guard import allow_live_constructor

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
JOB = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
EXPECTED_THREADS = [
    "nextron-job-bulk-1",
    "nextron-job-heartbeat",
    "nextron-job-interactive-1",
    "nextron-job-keeper",
    "nextron-job-realtime-1",
]
LIFESPAN_LOGGERS = ("nextron.jobs.runner", "nextron.jobs.scheduler", "nextron.jobs.heartbeat")


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
    assert job_types.names() == ("send_alert",)  # type: ignore[attr-defined]
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


def raw_query(units: UnitOfWorkFactory, statement: str, params: tuple[object, ...] = ()) -> Any:
    assert units.engine.url.database
    connection = sqlite3.connect(units.engine.url.database, isolation_level=None)
    try:
        return connection.execute(statement, params).fetchall()
    finally:
        connection.close()


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
    "required_settings",
    "database_revision",
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
    assert secrets.calls == []  # no credential or grant was read


def dead_send_alert(units: UnitOfWorkFactory, clock: FrozenClock) -> str:
    """A ``send_alert`` job that died on its first run; its id."""
    queue = JobQueue(clock, main.build_job_types(alerts=InMemoryAlert))  # never called

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
        read_in_transaction: list[bool] = []
        real_read = secrets.read

        def recording_read(name: str) -> str | None:
            read_in_transaction.append(in_transaction())
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
            # every Credential Manager read ran before the read transaction (and its lock)
            assert read_in_transaction and not any(read_in_transaction)
        else:  # the test sees the broken wiring
            assert oauth.calls == [("refresh", REFRESH_TOKEN)]
            assert port.calls == [("127.0.0.1", config.port)]
        assert statuses["required_credentials"][0] == "ok"
        assert statuses["required_settings"][0] == "fail"  # nothing is set yet
        assert statuses["database_revision"] == (
            "ok",
            f"the database is at head ({head_revision()})",
        )
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
        "scheduler_started",
        "scheduler_stopped",
        "job_runner_stopped",
    ]
    [started] = [r for r in caplog.records if r.getMessage() == "job_runner_started"]
    assert started.bulk_threads == 1  # type: ignore[attr-defined]
    [scheduled] = [r for r in caplog.records if r.getMessage() == "scheduler_started"]
    assert scheduled.triggers == 0  # type: ignore[attr-defined]
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
    """A recording runner, scheduler and heartbeat writer sharing one call log of
    ``(call, thread)``; any call named in ``failing`` raises ``Failed`` after recording."""

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

    def lifespan(self) -> main.Lifespan:
        return main.engine_lifespan(
            self.runner,  # type: ignore[arg-type]
            self.scheduler,  # type: ignore[arg-type]
            self.writer,  # type: ignore[arg-type]
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


def serve(parts: Parts, *, server_fails: bool = False) -> threading.Thread:
    """Run the lifespan around a server that returns (or raises ``Failed``); the loop's
    thread."""

    async def run() -> threading.Thread:
        async with parts.lifespan()(FastAPI()):
            parts.calls.append(("serving", threading.current_thread()))
            if server_fails:
                raise Failed("the server failed")
        return threading.current_thread()

    return asyncio.run(run())


def test_startup_runs_runner_scheduler_writer_and_shutdown_reverses_it() -> None:
    parts = Parts()

    loop_thread = serve(parts)

    assert parts.names() == [
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


def test_every_part_stops_when_serving_fails() -> None:
    parts = Parts()

    with pytest.raises(Failed):
        serve(parts, server_fails=True)

    assert parts.names()[-3:] == ["writer.stop", "scheduler.stop", "runner.stop"]


@pytest.mark.parametrize(
    ("failing", "calls"),
    [
        ("runner.start", ["runner.start"]),
        ("scheduler.start", ["runner.start", "scheduler.start", "runner.stop"]),
        (
            "writer.start",
            ["runner.start", "scheduler.start", "writer.start", "scheduler.stop", "runner.stop"],
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
