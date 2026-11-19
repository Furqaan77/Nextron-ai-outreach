"""The planted bot token (FR-56, NFR-18, AD-12, AD-15): the Telegram bot token saved on the
Credentials page and used by the real ``TelegramAlert`` appears in no log record, no error, no
``last_error``, no database value, no file and no response, on every send and failure path.

On a dashboard with real units of work (a database at head), the planted token is saved for
``telegram_bot`` through ``POST /credentials/save`` and the chat id through ``POST /settings``.
Its ``Notify`` sends through the real ``TelegramAlert`` on an ``httpx2.MockTransport`` that, alert
by alert, answers 200, 200 without a message id, 400, 401, 403, 404, 429 and 500, and raises a
connect error, a read timeout and a dropped connection, each error message holding the
request's URL (which holds the token). A real ``Runner`` runs every ``send_alert`` job (the
resends and retries included, until each job is done or dead) and the dead-job sweep; the
pages are then read. The checks reuse ``tests/app/test_credential_secrecy.py``'s: every
response, every captured log record before redaction (the SDK loggers at the level ``serve``
sets: ``httpx2`` logs each request's URL at INFO, so it stays at WARNING), every error the port
raised (``str``, ``repr`` and formatted traceback), every database value and every file under
the test's folder.
"""

import logging
import sqlite3
import traceback
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Engine

from nextron.adapters.telegram import TelegramAlert
from nextron.app.ops.api import DeadJobAlerts
from nextron.db.metering import UsageMeter
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.job import Lane
from nextron.domain.redaction import redact
from nextron.jobs.runner import Runner
from nextron.logconfig import SDK_LEVEL, SDK_LOGGERS
from nextron.ports.alert import AlertDelivery, AlertMessage
from tests.app.test_credential_secrecy import (
    database_leaks,
    file_leaks,
    log_leaks,
    response_leaks,
)
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

TOKEN = "1234567890:Qm7Zr2Vx9Lk4Tw8Np3Hs"
KEY_PART = TOKEN.split(":")[1]
CHAT = "-1001234567890"
SUBJECTS = [f"01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}" for n in range(10, 22)]

# What the transport does for each alert, in the order the alerts are raised.
type Behaviour = Callable[[httpx2.Request], httpx2.Response]


def answer(status: int, body: object = None) -> Behaviour:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if body is None:
            return httpx2.Response(status, text=f"no id for {request.url}", request=request)
        return httpx2.Response(status, json=body, request=request)

    return respond


def fail(cls: type[httpx2.TransportError]) -> Behaviour:
    def respond(request: httpx2.Request) -> httpx2.Response:
        raise cls(f"failed for {request.url} {TOKEN}", request=request)

    return respond


SENT = {"ok": True, "result": {"message_id": 42}}
REFUSED = {"ok": False, "description": f"Bad Request: {TOKEN}"}
BEHAVIOURS: list[Behaviour] = [
    answer(200, SENT),
    answer(200),  # no message id: unknown, then the resend below
    answer(400, REFUSED),
    answer(401, REFUSED),
    answer(403, REFUSED),
    answer(404, REFUSED),
    answer(429, REFUSED),  # retried until the job is dead
    answer(500, REFUSED),
    fail(httpx2.ConnectError),
    fail(httpx2.ReadTimeout),
    fail(httpx2.RemoteProtocolError),
]


class Transport:
    """Answers each alert's requests with its behaviour, looked up by the alert's text."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        text = request.read().decode()
        [index] = [n for n, subject in enumerate(SUBJECTS) if subject in text]
        return BEHAVIOURS[index](request)


class RecordingPort:
    """The real adapter, with every error it raises kept for the checks."""

    def __init__(self, inner: TelegramAlert) -> None:
        self.inner = inner
        self.errors: list[BaseException] = []

    def send(self, message: AlertMessage) -> AlertDelivery:
        try:
            return self.inner.send(message)
        except BaseException as error:
            self.errors.append(error)
            raise


def query(engine: Engine, statement: str) -> list[tuple[object, ...]]:
    assert engine.url.database
    connection = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        return connection.execute(statement).fetchall()
    finally:
        connection.close()


def error_leaks(errors: list[BaseException], value: str) -> list[str]:
    return [
        f"{type(error).__name__}: {error}"
        for error in errors
        if any(
            value in text
            for text in (str(error), repr(error), *traceback.format_exception(error))
        )
    ]


def test_the_planted_token_is_one_no_redaction_rule_masks_unless_it_is_in_a_bot_path() -> None:
    assert redact(TOKEN) == TOKEN and redact(f"token {KEY_PART}") == f"token {KEY_PART}"
    assert KEY_PART not in redact(f"POST https://api.telegram.org/bot{TOKEN}/sendMessage")


def test_the_bot_token_appears_in_no_log_error_last_error_database_file_or_response(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    for name in SDK_LOGGERS:  # as serve sets them (nextron.logconfig.configure_logging)
        logging.getLogger(name).setLevel(SDK_LEVEL)
    secrets = InMemorySecrets()
    transport = Transport()
    clock = FrozenClock()
    meter = UsageMeter(units, clock)  # the real meter: the token reaches no api_usage row either
    client = httpx2.Client(transport=httpx2.MockTransport(transport))
    port = RecordingPort(TelegramAlert(secrets, meter=meter, http=client))
    dashboard = Dashboard(units=units, secrets=secrets, clock=clock, alert=port)
    cookie = dashboard.open_session()
    token = dashboard.csrf_token(cookie)
    replies: list[Reply] = []

    def post(path: str, body: bytes) -> Reply:
        replies.append(
            dashboard.post(
                path, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
            )
        )
        return replies[-1]

    def get(path: str) -> Reply:
        replies.append(dashboard.get(path, cookie=cookie))
        return replies[-1]

    # 1. Save the token on the Credentials page and the chat id on the Settings page.
    saved = post(
        "/credentials/save", form(csrf_token=token, credential="telegram_bot", value=TOKEN)
    )
    settings = post(
        "/settings",
        form(
            csrf_token=token,
            sender_name="",
            sender_timezone="",
            sink_allowlist="",
            bulk_threads="",
            backup_time="",
            backup_rotation_days="",
            backup_offpc_dir="",
            alert_chat_id=CHAT,
            quota_hunter_credits="",
            quota_prospeo_credits="",
            quota_places_enterprise="",
            quota_places_pro="",
            quota_job_aggregator="",
            ai_budget_monthly="",
            ai_budget_alert_pct="",
            sender_signature="",
            legal_name="",
            postal_address="",
            optout_line="",
            privacy_notice_url="",
            us_solicitation_label="",
            crawl_max_pages="",
            ats_hosts="",
            places_discovery_locations="",
            places_max_pages_per_run="",
            unsub_active_kid="",
            total_daily_ceiling="",
            lead_score_fit_weight="",
            lead_score_signal_weight="",
            unsub_worker_host="",
            linkedin_owner_chat="",
            min_lead_score="",
            call_outcome_prompt_delay="",
        ),
    )
    assert saved.status == 200 and "Saved." in saved.text
    assert settings.status == 200 and "Settings saved." in settings.text
    assert secrets.store[(secrets.service, "telegram/bot_token")] == TOKEN

    # 2. One alert per behaviour; a runner sends them all, retries and resends included.
    @use_case
    def raise_alert(subject: str) -> None:
        with current().transaction():
            dashboard.notify.alert(
                "job_dead",
                subject,
                f"Job dead: enrich_lead {subject}, attempts: 1",
                [Channel.TELEGRAM, Channel.DASHBOARD],
            )

    for subject in SUBJECTS[: len(BEHAVIOURS)]:
        with units.open():
            raise_alert(subject)
    runner = Runner(
        units, dashboard.queue, dashboard.job_types, dead_jobs=DeadJobAlerts(dashboard.notify)
    )
    for _ in range(12):  # past every backoff (30 s doubling) and the 60 s resend delay
        while runner.run_one(Lane.REALTIME):
            pass
        dashboard.clock.advance(timedelta(minutes=31))
    runner.keep_once()

    statuses = dict(query(db_engine, "SELECT subject, status FROM alerts"))
    assert [statuses[subject] for subject in SUBJECTS[: len(BEHAVIOURS)]] == [
        "sent",
        "unknown",
        "failed",
        "failed",
        "failed",
        "failed",
        "pending",  # 429: retried until its job died
        "pending",  # 500
        "pending",  # the connect error
        "unknown",
        "unknown",
    ]
    assert len(transport.requests) > len(BEHAVIOURS)  # the resends and retries really ran
    # the real meter recorded every attempted request, one api_usage row each
    assert query(db_engine, "SELECT COUNT(*) FROM api_usage") == [(len(transport.requests),)]
    assert all(TOKEN in str(request.url) for request in transport.requests)  # it was used
    assert port.errors  # and failed on every failure path
    last_errors = [
        str(text)
        for (text,) in query(db_engine, "SELECT last_error FROM jobs WHERE last_error IS NOT NULL")
    ]
    assert any(text.startswith("telegram_unavailable") for text in last_errors)

    # 3. Read the pages.
    get("/credentials")
    get("/settings")
    area = get("/notifications")
    assert area.status == 200 and "Notifications (" in area.text
    notices = [text for (text,) in query(db_engine, "SELECT text FROM notifications")]
    assert sum(str(text).startswith("Telegram alert not delivered") for text in notices) == 4
    assert sum(str(text).startswith("Telegram alert may not") for text in notices) == 3
    assert sum(str(text).startswith("Job dead: send_alert") for text in notices) == 3

    # The checks.
    for value in (TOKEN, KEY_PART):
        assert response_leaks(replies, value) == []
        assert log_leaks(caplog.records, value) == []
        assert error_leaks(port.errors, value) == []
        assert not [text for text in last_errors if value in text]
        tables, column_leaks = database_leaks(db_engine, value)
        assert {"alerts", "notifications", "jobs", "events", "api_usage"} <= set(tables)
        assert column_leaks == []
        assert file_leaks(tmp_path, value) == []


def test_the_error_check_finds_the_token_in_a_chained_error() -> None:
    try:
        try:
            raise OSError(f"failed for /bot{TOKEN}/sendMessage")
        except OSError as inner:
            raise RuntimeError("the call failed") from inner
    except RuntimeError as outer:
        assert error_leaks([outer], TOKEN) == ["RuntimeError: the call failed"]
