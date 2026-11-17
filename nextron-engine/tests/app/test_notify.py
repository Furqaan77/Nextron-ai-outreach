"""The ``notify`` service (``Notify``, AD-3, AD-4, AD-15) on a temporary database at head, with
``FrozenClock`` and the in-memory alert port: Story 1.12's matrix rows from Raise to Not
configured, the ``events`` row of each transition, the unique indexes, idempotent re-runs of
the first send and the resend, a send through a real ``AlertSink`` and the Telegram adapter,
and the port never called inside a transaction."""

import functools
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import Engine

import nextron.adapters.telegram as telegram_adapter
from nextron import main
from nextron.adapters.sink import AlertSink
from nextron.adapters.telegram import TelegramAlert
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Alerted, Notify
from nextron.app.queries import notify as notify_queries
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.repos.notify.alerts import AlertRow
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel, InvalidAlert
from nextron.domain.job import JobTypes
from nextron.domain.settings_schema import Key
from nextron.main import AdapterContext, build_role_adapter
from nextron.ports.alert import DELIVERY_UNKNOWN, AlertDelivery, AlertMessage, AlertPort
from nextron.logconfig import SDK_LEVEL, SDK_LOGGERS
from nextron.ports.errors import AdapterError
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import allow_live_constructor

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
JOB = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
OTHER_JOB = "01JD3QZ8X5V9K2M7N4P6R8T0WZ"
TEXT = f"Job dead: enrich_lead {JOB}, attempts: 1"
CHAT = "-1001234567890"
TOKEN = "123456789:AAHk3Wq9Zr5Lp2Xc8Vn4Bm7Ty1Ju6Gd0Fs"
BOTH = (Channel.TELEGRAM, Channel.DASHBOARD)
LOGGER = "nextron.app.notify.api"
SINK_LOGGER = "nextron.adapters.sink"

# Helpers ----------------------------------------------------------------------------------------


@dataclass
class World:
    """The ``notify`` service on real units of work, its queue and its job types."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    port: AlertPort
    job_types: JobTypes
    queue: JobQueue
    notify: Notify
    setup: Setup

    def transact[R](self, fn: Callable[[], R]) -> R:
        """Run ``fn`` inside a caller's use case and transaction, as a service would."""

        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def alert(
        self,
        kind: str = "job_dead",
        subject: str = JOB,
        text: str = TEXT,
        channels: object = BOTH,
    ) -> Alerted:
        chosen: Any = channels
        return self.transact(lambda: self.notify.alert(kind, subject, text, chosen))

    def send(self, alert_id: str) -> None:
        with self.units.open():
            self.notify.send(alert_id)

    def set_chat(self, chat: str = CHAT) -> None:
        with self.units.open():
            self.setup.set(Key.ALERT_CHAT_ID, chat)

    def query(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        assert self.engine.url.database
        connection = sqlite3.connect(self.engine.url.database, isolation_level=None)
        try:
            return connection.execute(statement, params).fetchall()
        finally:
            connection.close()

    def row(self, alert_id: str) -> AlertRow:
        with self.units.open(read_only=True):
            found = notify_queries.alert(alert_id)
        assert found is not None
        return found

    def alert_ids(self) -> list[str]:
        return [alert_id for (alert_id,) in self.query("SELECT id FROM alerts ORDER BY rowid")]

    def only_alert(self) -> str:
        [alert_id] = self.alert_ids()
        return alert_id

    def events(self, alert_id: str) -> list[tuple[str | None, str, str]]:
        return self.query(
            "SELECT from_status, to_status, cause FROM events WHERE entity = 'alerts' AND "
            "entity_id = ? ORDER BY rowid",
            (alert_id,),
        )

    def jobs(self) -> list[tuple[str, str, str, str, str]]:
        return self.query(
            "SELECT idempotency_key, lane, status, payload, run_after FROM jobs ORDER BY rowid"
        )

    def notifications(self) -> list[tuple[str, str, str, str | None]]:
        return self.query(
            "SELECT kind, subject, text, dismissed_at FROM notifications ORDER BY rowid"
        )

    def counts(self) -> tuple[int, int, int, int]:
        [(alerts, notices, jobs, events)] = self.query(
            "SELECT (SELECT count(*) FROM alerts), (SELECT count(*) FROM notifications), "
            "(SELECT count(*) FROM jobs), (SELECT count(*) FROM events)"
        )
        return alerts, notices, jobs, events


def world_on(
    units: UnitOfWorkFactory,
    engine: Engine,
    clock: FrozenClock,
    port: AlertPort | Callable[[], AlertPort],
) -> World:
    built: dict[str, Notify] = {}
    job_types = main.build_job_types(alerts=lambda: built["notify"])
    queue = JobQueue(clock, job_types)
    factory: Any = port if callable(port) and not isinstance(port, AlertPort) else lambda: port
    notify = Notify(clock, queue, alert_ports={Channel.TELEGRAM: factory})
    built["notify"] = notify
    shown: Any = port
    return World(units, engine, clock, shown, job_types, queue, notify, Setup(clock))


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def port() -> InMemoryAlert:
    return InMemoryAlert()


@pytest.fixture
def world(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock, port: InMemoryAlert
) -> World:
    return world_on(units, db_engine, clock, port)


def records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == event]


def extras(record: logging.LogRecord, *names: str) -> tuple[object, ...]:
    return tuple(getattr(record, name) for name in names)


def assert_no_secret_logged(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        shown = json.dumps(vars(record), default=str)
        for planted in (TEXT, CHAT, TOKEN, TOKEN.split(":")[1]):
            assert planted not in shown, record.getMessage()


def sdk_loggers_as_served() -> None:
    """The SDK loggers' levels as ``serve`` sets them (``configure_logging``): httpx2 logs each
    request's URL, which holds the bot token, at INFO, so it stays at WARNING."""
    for name in SDK_LOGGERS:
        logging.getLogger(name).setLevel(SDK_LEVEL)


def unknown() -> AdapterError:
    return AdapterError(False, DELIVERY_UNKNOWN, "the request was sent, but no answer came back")


# Row: Raise -------------------------------------------------------------------------------------


def test_raise_inserts_a_pending_alert_its_event_its_job_and_a_notification(
    world: World,
) -> None:
    result = world.alert()

    assert result == Alerted((Channel.TELEGRAM, Channel.DASHBOARD))
    alert_id = world.only_alert()
    row = world.row(alert_id)
    assert (row.kind, row.subject, row.channel, row.text, row.status) == (
        "job_dead",
        JOB,
        "telegram",
        TEXT,
        "pending",
    )
    assert (row.provider_message_id, row.error_code, row.resent_at) == (None, None, None)
    assert (row.created_at, row.updated_at) == (T0, T0)
    assert world.query(
        "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at FROM "
        "events WHERE entity = 'alerts'"
    ) == [
        (
            "alerts",
            alert_id,
            None,
            "pending",
            "requested",
            None,
            "system:notify",
            "2026-11-17T09:00:00.000000Z",
        )
    ]
    assert world.jobs() == [
        (
            f"send_alert:{alert_id}:0",
            "realtime",
            "queued",
            f'{{"alert_id":"{alert_id}","v":1}}',
            "2026-11-17T09:00:00.000000Z",
        )
    ]
    assert world.notifications() == [("job_dead", JOB, TEXT, None)]
    assert world.query("SELECT entity, to_status, cause FROM events WHERE entity = 'jobs'") == [
        ("jobs", "queued", "enqueued")
    ]


def test_a_rolled_back_caller_leaves_nothing_behind(world: World) -> None:
    @use_case
    def caller() -> None:
        with current().transaction():
            world.notify.alert("job_dead", JOB, TEXT, BOTH)
            raise RuntimeError("the caller's own work failed")

    with world.units.open(), pytest.raises(RuntimeError):
        caller()

    assert world.counts() == (0, 0, 0, 0)


def test_one_channel_writes_only_its_own_row(world: World) -> None:
    assert world.alert(channels=[Channel.DASHBOARD]) == Alerted((Channel.DASHBOARD,))
    assert world.counts() == (0, 1, 0, 0)

    assert world.alert(subject=OTHER_JOB, channels={Channel.TELEGRAM}) == Alerted(
        (Channel.TELEGRAM,)
    )
    assert world.counts() == (1, 1, 1, 2)  # the alert's event and its job's


# Row: Repeat ------------------------------------------------------------------------------------


def test_a_repeat_is_a_no_op_and_a_new_channel_is_added(world: World, clock: FrozenClock) -> None:
    assert world.alert(channels=[Channel.DASHBOARD]) == Alerted((Channel.DASHBOARD,))
    clock.advance(timedelta(minutes=1))

    assert world.alert(text="Job dead: a different text") == Alerted((Channel.TELEGRAM,))
    assert world.alert() == Alerted(())
    assert world.alert(text="Job dead: yet another") == Alerted(())

    assert world.counts() == (1, 1, 1, 2)
    assert world.notifications() == [("job_dead", JOB, TEXT, None)]  # untouched
    assert world.row(world.only_alert()).text == "Job dead: a different text"


def test_another_kind_or_subject_is_a_new_alert(world: World) -> None:
    world.alert()
    world.alert(subject=OTHER_JOB)
    world.alert(kind="other_kind")

    assert world.counts() == (3, 3, 3, 6)


def test_the_unique_indexes_refuse_raw_duplicates(world: World) -> None:
    world.alert()
    alert_id = world.only_alert()

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        world.query(
            "INSERT INTO alerts (id, kind, subject, channel, text, status, created_at, "
            "updated_at) SELECT ?, kind, subject, channel, text, status, created_at, "
            "updated_at FROM alerts WHERE id = ?",
            ("01JD3QZ8X5V9K2M7N4P6R8T0W0", alert_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        world.query(
            "INSERT INTO notifications (id, kind, subject, text, created_at) SELECT ?, kind, "
            "subject, 'x', created_at FROM notifications",
            ("01JD3QZ8X5V9K2M7N4P6R8T0W0",),
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        world.query("UPDATE alerts SET status = 'lost' WHERE id = ?", (alert_id,))
    # Another channel for the same kind and subject is a different row: no channel check.
    world.query(
        "INSERT INTO alerts (id, kind, subject, channel, text, status, created_at, updated_at) "
        "SELECT ?, kind, subject, 'email', text, status, created_at, updated_at FROM alerts",
        ("01JD3QZ8X5V9K2M7N4P6R8T0W0",),
    )


# Row: Bad alert ---------------------------------------------------------------------------------

BAD_ALERTS = [
    {"kind": "Job-Dead"},
    {"subject": "a b"},
    {"text": "   "},
    {"text": "x" * 4097},
    {"channels": []},
    {"channels": "telegram"},
]


@pytest.mark.parametrize("change", BAD_ALERTS)
def test_a_bad_alert_writes_nothing_and_dooms_the_transaction(
    world: World, change: dict[str, Any]
) -> None:
    fields: dict[str, Any] = {
        "kind": "job_dead",
        "subject": JOB,
        "text": TEXT,
        "channels": BOTH,
        **change,
    }
    caught: list[InvalidAlert] = []

    @use_case
    def caller() -> None:
        with current().transaction():
            try:
                world.notify.alert(**fields)
            except InvalidAlert as refused:
                caught.append(refused)  # caught, but the transaction is doomed anyway

    with world.units.open(), pytest.raises(UnitOfWorkError, match="rolled back"):
        caller()

    assert [refused.code for refused in caught] == ["invalid_alert"]
    assert world.counts() == (0, 0, 0, 0)


# Row: Sent --------------------------------------------------------------------------------------


def test_a_delivered_alert_is_sent_with_its_message_id_and_event(
    world: World, port: InMemoryAlert, clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()
    port.outcomes.append(AlertDelivery("42"))
    clock.advance(timedelta(seconds=5))

    world.send(alert_id)

    assert port.sent == [AlertMessage(CHAT, TEXT)]
    row = world.row(alert_id)
    assert (row.status, row.provider_message_id, row.error_code, row.resent_at) == (
        "sent",
        "42",
        None,
        None,
    )
    assert row.updated_at == T0 + timedelta(seconds=5)
    assert world.events(alert_id) == [
        (None, "pending", "requested"),
        ("pending", "sent", "delivered"),
    ]
    [sent] = records(caplog, "alert_sent")
    assert sent.levelno == logging.INFO
    assert extras(sent, "alert_id", "kind", "channel", "sink") == (
        alert_id,
        "job_dead",
        "telegram",
        False,
    )
    assert world.notifications() == [("job_dead", JOB, TEXT, None)]  # no not-delivered notice
    assert_no_secret_logged(caplog)


def test_a_rerun_of_a_sent_alerts_job_sends_nothing(world: World, port: InMemoryAlert) -> None:
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()
    world.send(alert_id)
    before = world.query("SELECT * FROM alerts"), world.counts()

    world.send(alert_id)
    world.send(alert_id)

    assert len(port.attempts) == 1
    assert (world.query("SELECT * FROM alerts"), world.counts()) == before


def test_a_missing_or_malformed_alert_id_is_nothing_to_do(
    world: World, port: InMemoryAlert
) -> None:
    world.set_chat()

    world.send(JOB)
    world.send("not-an-id")

    assert port.attempts == []
    assert world.counts() == (0, 0, 0, 0)


def test_the_port_is_never_called_inside_a_transaction(
    world: World, port: InMemoryAlert
) -> None:
    seen: list[bool] = []

    def outside_a_transaction(message: AlertMessage) -> None:
        del message
        try:
            current().session  # noqa: B018 - raises outside a transaction
        except UnitOfWorkError:
            seen.append(True)
        else:
            seen.append(False)

    port.observer = outside_a_transaction
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()
    port.outcomes.extend([unknown(), AlertDelivery("9")])

    world.send(alert_id)  # first send: unknown
    world.send(alert_id)  # the resend: delivered

    assert seen == [True, True]
    assert world.row(alert_id).status == "sent"


def test_the_port_is_built_on_the_first_send_and_kept(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock
) -> None:
    built: list[InMemoryAlert] = []

    def build() -> InMemoryAlert:
        built.append(InMemoryAlert())
        return built[-1]

    world = world_on(units, db_engine, clock, build)
    world.set_chat()
    world.alert()
    world.alert(subject=OTHER_JOB)
    assert built == []  # raising an alert builds nothing

    for alert_id in world.alert_ids():
        world.send(alert_id)

    assert len(built) == 1 and len(built[0].sent) == 2


# Row: Sink --------------------------------------------------------------------------------------


@pytest.fixture
def sink_world(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[World, list[httpx2.Request], Path]:
    """A world whose Telegram port is ``build_role_adapter("telegram", ...)``: the real
    ``AlertSink`` around the real ``TelegramAlert``, whose client answers on a
    ``MockTransport``."""
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 77}})

    allow_live_constructor(monkeypatch, "nextron.main", "telegram")
    monkeypatch.setattr(
        telegram_adapter,
        "default_http_client",
        lambda: httpx2.Client(transport=httpx2.MockTransport(answer)),
    )
    config = load_bootstrap()
    assert config.env == "test"
    secrets = InMemorySecrets("test")
    secrets.write("telegram/bot_token", TOKEN)
    context = AdapterContext(config, secrets, clock, InMemoryUsageMeter(clock))
    port = functools.partial(build_role_adapter, "telegram", context, units=units)
    return world_on(units, db_engine, clock, port), requests, config.data_dir / "sink"


def test_in_test_a_chat_not_on_the_allowlist_is_recorded_in_the_sink_and_counts_as_sent(
    sink_world: tuple[World, list[httpx2.Request], Path], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world, requests, sink = sink_world
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()

    world.send(alert_id)

    built: Any = world.notify._port(Channel.TELEGRAM)
    assert isinstance(built, AlertSink) and isinstance(built.wrapped, TelegramAlert)
    assert requests == []
    [line] = (sink / "2026-11-17.jsonl").read_text(encoding="ascii").splitlines()
    assert json.loads(line) == {
        "time": "2026-11-17T09:00:00.000000Z",
        "port": "alert",
        "recipient": CHAT,
        "text": TEXT,
    }
    row = world.row(alert_id)
    assert (row.status, row.provider_message_id) == ("sent", None)
    [sent] = records(caplog, "alert_sent")
    assert extras(sent, "alert_id", "sink") == (alert_id, True)
    assert not [r for r in caplog.records if r.getMessage() == "sink_allowlist_unreadable"]


def test_in_test_an_allowlisted_chat_is_delivered_at_once_through_the_real_adapter(
    sink_world: tuple[World, list[httpx2.Request], Path], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    sdk_loggers_as_served()
    world, requests, sink = sink_world
    world.set_chat()
    with world.units.open():
        world.setup.set(Key.SINK_ALLOWLIST, (CHAT,))
    world.alert()
    alert_id = world.only_alert()

    started = time.monotonic()
    world.send(alert_id)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0  # the allowlist read never waits on the busy timeout
    [request] = requests
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {"chat_id": int(CHAT), "text": TEXT}
    row = world.row(alert_id)
    assert (row.status, row.provider_message_id) == ("sent", "77")
    [sent] = records(caplog, "alert_sent")
    assert extras(sent, "sink") == (False,)
    assert not [r for r in caplog.records if r.getMessage() == "sink_allowlist_unreadable"]
    assert [r.getMessage() for r in caplog.records if r.name == SINK_LOGGER] == ["sink_passed"]
    assert not sink.exists()
    assert_no_secret_logged(caplog)


# Row: Rate limit --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        AdapterError(True, "telegram_unavailable", "Telegram answered with HTTP 429"),
        AdapterError(True, "telegram_unavailable", "the Telegram request could not be sent"),
    ],
)
def test_a_retryable_failure_raises_and_leaves_the_alert_pending(
    world: World, port: InMemoryAlert, failure: AdapterError
) -> None:
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()
    port.outcomes.append(failure)

    with pytest.raises(AdapterError) as raised:
        world.send(alert_id)

    assert raised.value is failure
    row = world.row(alert_id)
    assert (row.status, row.error_code, row.updated_at) == ("pending", None, T0)
    assert world.events(alert_id) == [(None, "pending", "requested")]
    assert world.notifications() == [("job_dead", JOB, TEXT, None)]

    world.send(alert_id)  # the retry delivers

    assert world.row(alert_id).status == "sent"


# Row: Refused -----------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_refused_alert_fails_with_a_not_delivered_notification(
    world: World,
    port: InMemoryAlert,
    clock: FrozenClock,
    status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()
    port.outcomes.append(
        AdapterError(False, "telegram_refused", f"Telegram refused the alert with HTTP {status}")
    )
    clock.advance(timedelta(seconds=3))

    world.send(alert_id)

    row = world.row(alert_id)
    assert (row.status, row.error_code, row.provider_message_id, row.resent_at) == (
        "failed",
        "telegram_refused",
        None,
        None,
    )
    assert world.events(alert_id) == [
        (None, "pending", "requested"),
        ("pending", "failed", "failed"),
    ]
    assert world.notifications() == [
        ("job_dead", JOB, TEXT, None),
        (
            "alert_not_delivered",
            alert_id,
            f"Telegram alert not delivered: job_dead {alert_id}, telegram_refused",
            None,
        ),
    ]
    [(key, *_)] = world.jobs()
    assert key.endswith(":0")  # no resend
    [failed] = records(caplog, "alert_failed")
    assert failed.levelno == logging.WARNING
    assert extras(failed, "alert_id", "kind", "channel", "code") == (
        alert_id,
        "job_dead",
        "telegram",
        "telegram_refused",
    )

    world.send(alert_id)  # a re-run changes nothing

    assert len(port.attempts) == 1
    assert len(world.notifications()) == 2


# Row: Unknown -----------------------------------------------------------------------------------


def lost_once(world: World, port: InMemoryAlert, clock: FrozenClock) -> str:
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()
    port.outcomes.append(unknown())
    world.send(alert_id)
    return alert_id


def test_an_unknown_outcome_schedules_one_resend_a_minute_later(
    world: World, port: InMemoryAlert, clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    alert_id = lost_once(world, port, clock)

    row = world.row(alert_id)
    assert (row.status, row.error_code, row.resent_at) == ("unknown", DELIVERY_UNKNOWN, None)
    assert world.events(alert_id) == [
        (None, "pending", "requested"),
        ("pending", "unknown", "outcome_unknown"),
    ]
    assert [(key, lane, status, run_after) for key, lane, status, _, run_after in world.jobs()] == [
        (f"send_alert:{alert_id}:0", "realtime", "queued", "2026-11-17T09:00:00.000000Z"),
        (f"send_alert:{alert_id}:1", "realtime", "queued", "2026-11-17T09:01:00.000000Z"),
    ]
    assert world.notifications() == [("job_dead", JOB, TEXT, None)]  # nothing yet
    [lost] = records(caplog, "alert_unknown")
    assert lost.levelno == logging.WARNING
    assert extras(lost, "alert_id", "code") == (alert_id, DELIVERY_UNKNOWN)


def test_a_delivered_resend_is_sent(
    world: World, port: InMemoryAlert, clock: FrozenClock
) -> None:
    alert_id = lost_once(world, port, clock)
    clock.advance(timedelta(seconds=60))
    port.outcomes.append(AlertDelivery("43"))

    world.send(alert_id)

    row = world.row(alert_id)
    assert (row.status, row.provider_message_id, row.resent_at) == (
        "sent",
        "43",
        T0 + timedelta(seconds=60),
    )
    assert world.events(alert_id)[-1] == ("unknown", "sent", "delivered")
    assert len(port.attempts) == 2

    world.send(alert_id)  # a re-run of either job changes nothing

    assert len(port.attempts) == 2


def test_an_unknown_resend_stays_unknown_with_a_notification_and_no_third_try(
    world: World, port: InMemoryAlert, clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    alert_id = lost_once(world, port, clock)
    clock.advance(timedelta(seconds=60))
    port.outcomes.append(unknown())

    world.send(alert_id)

    row = world.row(alert_id)
    assert (row.status, row.error_code, row.resent_at) == (
        "unknown",
        DELIVERY_UNKNOWN,
        T0 + timedelta(seconds=60),
    )
    assert world.events(alert_id) == [
        (None, "pending", "requested"),
        ("pending", "unknown", "outcome_unknown"),
    ]
    assert len(world.jobs()) == 2  # no further job
    assert world.notifications()[-1] == (
        "alert_not_delivered",
        alert_id,
        f"Telegram alert may not have been delivered: job_dead {alert_id}, "
        "alert_delivery_unknown",
        None,
    )
    assert len(records(caplog, "alert_unknown")) == 2

    world.send(alert_id)  # a re-run of either job sends nothing more

    assert len(port.attempts) == 2


def test_a_refused_resend_fails(world: World, port: InMemoryAlert, clock: FrozenClock) -> None:
    alert_id = lost_once(world, port, clock)
    clock.advance(timedelta(seconds=60))
    port.outcomes.append(AdapterError(False, "telegram_refused", "HTTP 403"))

    world.send(alert_id)

    row = world.row(alert_id)
    assert (row.status, row.error_code, row.resent_at) == (
        "failed",
        "telegram_refused",
        T0 + timedelta(seconds=60),
    )
    assert world.events(alert_id)[-1] == ("unknown", "failed", "failed")
    assert world.notifications()[-1][2] == (
        f"Telegram alert not delivered: job_dead {alert_id}, telegram_refused"
    )


def test_a_retryable_resend_failure_leaves_it_unknown_for_the_retry(
    world: World, port: InMemoryAlert, clock: FrozenClock
) -> None:
    alert_id = lost_once(world, port, clock)
    port.outcomes.append(AdapterError(True, "telegram_unavailable", "HTTP 503"))

    with pytest.raises(AdapterError):
        world.send(alert_id)

    row = world.row(alert_id)
    assert (row.status, row.resent_at) == ("unknown", None)


@pytest.mark.parametrize(
    ("outcome", "resend"),
    [
        (AlertDelivery("42"), False),
        (AdapterError(False, "telegram_refused", "HTTP 403"), False),
        (unknown(), False),
        (unknown(), True),
    ],
    ids=["delivered", "refused", "first unknown", "resend unknown"],
)
def test_a_fence_miss_changes_nothing(
    world: World,
    port: InMemoryAlert,
    clock: FrozenClock,
    outcome: AlertDelivery | AdapterError,
    resend: bool,
) -> None:
    alert_id = lost_once(world, port, clock) if resend else None
    if alert_id is None:
        world.set_chat()
        world.alert()
        alert_id = world.only_alert()
    before = (world.events(alert_id), world.notifications(), world.jobs())
    changed_to = "sent" if isinstance(outcome, AdapterError) or resend else "failed"

    def meanwhile(message: AlertMessage) -> None:
        del message
        world.query("UPDATE alerts SET status = ? WHERE id = ?", (changed_to, alert_id))

    port.observer = meanwhile
    port.outcomes.append(outcome)

    world.send(alert_id)

    row = world.row(alert_id)
    assert (row.status, row.resent_at) == (changed_to, None)
    assert (world.events(alert_id), world.notifications(), world.jobs()) == before


# Row: Not configured ----------------------------------------------------------------------------


def test_an_unset_chat_id_fails_the_alert_without_calling_the_port(
    world: World, port: InMemoryAlert, caplog: pytest.LogCaptureFixture
) -> None:
    world.alert()
    alert_id = world.only_alert()

    world.send(alert_id)

    assert port.attempts == []
    row = world.row(alert_id)
    assert (row.status, row.error_code) == ("failed", "setting_not_set")
    assert world.events(alert_id)[-1] == ("pending", "failed", "failed")
    assert world.notifications()[-1][2] == (
        f"Telegram alert not delivered: job_dead {alert_id}, setting_not_set"
    )
    [failed] = records(caplog, "alert_failed")
    assert extras(failed, "code") == ("setting_not_set",)


def test_a_stored_chat_id_that_no_longer_parses_fails_the_alert(
    world: World, port: InMemoryAlert
) -> None:
    world.query(
        "INSERT INTO settings (id, key, value, updated_at) VALUES "
        "('01JD3QZ8X5V9K2M7N4P6R8T0W0', 'alert_chat_id', '@ops', '2026-11-17T09:00:00.000000Z')"
    )
    world.alert()
    alert_id = world.only_alert()

    world.send(alert_id)

    assert port.attempts == []
    assert (world.row(alert_id).status, world.row(alert_id).error_code) == (
        "failed",
        "invalid_setting",
    )


def recording_client() -> tuple[httpx2.Client, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return httpx2.Client(transport=httpx2.MockTransport(answer)), requests


@pytest.mark.parametrize(
    ("token", "code"), [(None, "credential_not_set"), ("abc", "telegram_bad_token")]
)
def test_a_missing_or_malformed_token_fails_the_alert_without_any_request(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    clock: FrozenClock,
    token: str | None,
    code: str,
) -> None:
    secrets = InMemorySecrets()
    if token is not None:
        secrets.write("telegram/bot_token", token)
    client, requests = recording_client()
    adapter = TelegramAlert(secrets, meter=InMemoryUsageMeter(clock), http=client)
    world = world_on(units, db_engine, clock, adapter)
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()

    world.send(alert_id)

    assert requests == []
    row = world.row(alert_id)
    assert (row.status, row.error_code) == ("failed", code)
    assert world.notifications()[-1][2] == (
        f"Telegram alert not delivered: job_dead {alert_id}, {code}"
    )


def test_another_exception_from_the_port_propagates_and_changes_nothing(
    world: World, port: InMemoryAlert
) -> None:
    world.set_chat()
    world.alert()
    alert_id = world.only_alert()
    port.outcomes.append(RuntimeError("a bug in the adapter"))

    with pytest.raises(RuntimeError):
        world.send(alert_id)

    assert world.row(alert_id).status == "pending"


# Dismiss ----------------------------------------------------------------------------------------


def test_dismiss_sets_dismissed_at_once(world: World, clock: FrozenClock) -> None:
    world.alert(channels=[Channel.DASHBOARD])
    [(notification_id,)] = world.query("SELECT id FROM notifications")
    clock.advance(timedelta(minutes=2))

    with world.units.open():
        assert world.notify.dismiss(notification_id) is True
    clock.advance(timedelta(minutes=2))
    with world.units.open():
        assert world.notify.dismiss(notification_id) is False
    with world.units.open():
        assert world.notify.dismiss(OTHER_JOB) is False
    with world.units.open():
        assert world.notify.dismiss("not-an-id") is False

    assert world.query("SELECT dismissed_at FROM notifications") == [
        ("2026-11-17T09:02:00.000000Z",)
    ]


def test_open_notifications_counts_and_lists_the_newest_undismissed(
    world: World, clock: FrozenClock
) -> None:
    subjects = [f"01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}" for n in range(10, 17)]
    for subject in subjects:
        world.alert(subject=subject, channels=[Channel.DASHBOARD])
        clock.advance(timedelta(minutes=1))
    [(oldest,)] = world.query("SELECT id FROM notifications WHERE subject = ?", (subjects[0],))
    with world.units.open():
        world.notify.dismiss(oldest)

    with world.units.open(read_only=True):
        found = notify_queries.open_notifications(5)
        alerted = notify_queries.subjects("job_dead")

    assert found.count == 6
    assert [row.subject for row in found.newest] == subjects[:0:-1][:5]
    assert alerted == frozenset(subjects)
