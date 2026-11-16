"""Log redaction contract (AD-17, NFR-18): no planted secret reaches the log file.

Every kind of value the redaction rule covers is planted: query strings (after a path and after
a bare host name), a Telegram bot token, URL userinfo, plain, URL-encoded, Unicode and
over-long email addresses, bearer and basic credentials, an ``x-api-key`` header, a JSON
``password`` field, an ``sk-ant-`` key, a 40-character key, a base64 blob, and a one-time code
in a request path. They are logged through the real ``configure_logging`` on a temporary folder:
in the message, in the %-arguments, in a string ``extra``, in an exception, in the SDK exception
behind an ``AdapterError``, on a child logger, on the root logger, on an SDK logger, in a Python
warning, in a thread that crashes, and in a Uvicorn access record. The file, and stderr, must
hold none of them.

Secret-shaped values are assembled at run time; every value here is fake.
"""

import json
import logging
import threading
import warnings
from collections.abc import Callable
from pathlib import Path

import pytest

from nextron.logconfig import configure_logging
from nextron.ports.errors import AdapterError

QUERY_VALUE = "QuerySecret" + "4242"
HOST_QUERY_VALUE = "HostQuery" + "Secret77"
BOT_TOKEN = "123456789:" + "AA" + "Fake_Token-" * 3
USERINFO_PASSWORD = "Pw" + "Userinfo9"
EMAIL = "ann.lee+leads@example.co.uk"
ENCODED_EMAIL = "bob.smith" + "%40" + "example.org"
UNICODE_EMAIL = "zoë.müller" + "@" + "exämple.de"
LONG_LOCAL_EMAIL = "longlocal" * 8 + "@" + "example.net"
BEARER_TOKEN = "ya29." + "FakeBearer7"
BASIC_CREDENTIAL = "dXNl" + "cjpwYXNz"
API_KEY_VALUE = "akFake" + "123"
PASSWORD = "hunter" + "2fake"
ANTHROPIC_KEY = "sk-ant-" + "api03-" + "FAKE0" * 8
LONG_KEY = "Fk" * 20
BASE64_BLOB = "QUJD" * 6 + "+" + "MTIz" * 3 + "=="
ONE_TIME_CODE = "Otc" + "7731"
SECRETS = [
    QUERY_VALUE,
    HOST_QUERY_VALUE,
    BOT_TOKEN,
    USERINFO_PASSWORD,
    EMAIL,
    ENCODED_EMAIL,
    UNICODE_EMAIL,
    LONG_LOCAL_EMAIL,
    BEARER_TOKEN,
    BASIC_CREDENTIAL,
    API_KEY_VALUE,
    PASSWORD,
    ANTHROPIC_KEY,
    LONG_KEY,
    BASE64_BLOB,
]
FRAGMENTS = [
    "api_key",
    "HostQuery",
    "Fake_Token",
    "PwUserinfo",
    "ann.lee",
    "bob.smith",
    "zoë",
    "müller",
    "longlocal",
    "FakeBearer",
    "dXNlcjpw",
    "akFake",
    "hunter2",
    "FAKE0",
    "FkFk",
    "QUJDQUJD",
    "MTIzMTIz",
]

PLANTED = (
    f"GET https://api.example.test/v1/find?api_key={QUERY_VALUE}&d=x.test "
    f"call api.example.test?token={HOST_QUERY_VALUE} "
    f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage "
    f"db postgres://admin:{USERINFO_PASSWORD}@db.example.test/x "
    f"to {EMAIL}, {ENCODED_EMAIL}, {UNICODE_EMAIL} and {LONG_LOCAL_EMAIL} "
    f"with Authorization: Bearer {BEARER_TOKEN} and Authorization: Basic {BASIC_CREDENTIAL} "
    f"x-api-key: {API_KEY_VALUE} "
    f'"password": "{PASSWORD}" '
    f"key {ANTHROPIC_KEY} token {LONG_KEY} blob {BASE64_BLOB}"
)
REDACTED = (
    "GET https://api.example.test/v1/find "
    "call api.example.test "
    "POST https://api.telegram.org/bot<redacted>/sendMessage "
    "db postgres://<redacted>@db.example.test/x "
    "to <email>, <email>, <email> and <email> "
    "with Authorization: Bearer <redacted> and Authorization: Basic <redacted> "
    "x-api-key: <redacted> "
    '"password": "<redacted>" '
    "key <key> token <key> blob <key>"
)


class SdkError(Exception):
    """Stands in for a third-party exception whose text holds secrets."""


def in_message() -> None:
    logging.getLogger("nextron.x").warning(PLANTED)


def in_arguments() -> None:
    logging.getLogger("nextron.x").warning("call %s", PLANTED)


def in_extra() -> None:
    logging.getLogger("nextron.x").warning("call", extra={"detail": PLANTED})


def in_exception() -> None:
    try:
        raise RuntimeError(PLANTED)
    except RuntimeError:
        logging.getLogger("nextron.x").exception("call_failed")


def behind_an_adapter_error() -> None:
    try:
        try:
            raise SdkError(PLANTED)
        except SdkError as exc:
            raise AdapterError(True, "sdk_failed", "the SDK call failed") from exc
    except AdapterError as error:
        logging.getLogger("nextron.x").exception("call_failed: %s", error)


def on_a_child_logger() -> None:
    logging.getLogger("nextron.app.setup.api").warning(PLANTED)


def on_the_root_logger() -> None:
    logging.getLogger().warning(PLANTED)


def on_an_sdk_logger() -> None:
    logging.getLogger("httpx2").warning(PLANTED)


def in_a_warning() -> None:
    warnings.warn(PLANTED, UserWarning, stacklevel=1)


def in_a_crashed_thread() -> None:
    def fail() -> None:
        raise RuntimeError(PLANTED)

    thread = threading.Thread(target=fail)
    thread.start()
    thread.join()


def in_a_uvicorn_access_record() -> None:
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "GET", f"/enter?code={ONE_TIME_CODE}",
        "1.1", 200,
    )  # fmt: skip


PLANTERS: list[Callable[[], None]] = [
    in_message,
    in_arguments,
    in_extra,
    in_exception,
    behind_an_adapter_error,
    on_a_child_logger,
    on_the_root_logger,
    on_an_sdk_logger,
    in_a_warning,
    in_a_crashed_thread,
    in_a_uvicorn_access_record,
]


def assert_clean(text: str) -> None:
    for value in [*SECRETS, ONE_TIME_CODE, *FRAGMENTS, "Otc"]:
        assert value not in text


def test_no_planted_secret_reaches_the_log_file_or_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_file = configure_logging(logs_dir)

    for plant in PLANTERS:
        plant()

    text = log_file.read_text(encoding="utf-8")
    assert_clean(text)
    assert_clean(capsys.readouterr().err)
    lines = [json.loads(line) for line in text.splitlines()]
    assert len(lines) == len(PLANTERS)  # every record was written, masked, not dropped
    by_planter = dict(zip((plant.__name__ for plant in PLANTERS), lines, strict=True))
    assert by_planter["in_message"]["event"] == REDACTED
    assert by_planter["in_arguments"]["event"] == f"call {REDACTED}"
    assert by_planter["in_extra"]["detail"] == REDACTED
    assert f"RuntimeError: {REDACTED}" in by_planter["in_exception"]["exc"]
    adapter = by_planter["behind_an_adapter_error"]
    assert adapter["event"] == "call_failed: the SDK call failed"
    assert f"SdkError: {REDACTED}" in adapter["exc"]
    for name in ("on_a_child_logger", "on_the_root_logger", "on_an_sdk_logger"):
        assert by_planter[name]["event"] == REDACTED
    warning = by_planter["in_a_warning"]
    assert (warning["logger"], warning["level"]) == ("py.warnings", "WARNING")
    assert f"UserWarning: {REDACTED}" in warning["event"]
    crash = by_planter["in_a_crashed_thread"]
    assert crash["event"] == "thread_crashed"
    assert f"RuntimeError: {REDACTED}" in crash["exc"]
    access = by_planter["in_a_uvicorn_access_record"]
    assert access["event"] == '127.0.0.1:50000 - "GET /enter HTTP/1.1" 200'


def test_the_planted_values_are_really_there_before_redaction() -> None:
    """Guards the test above: each secret is present in what gets logged."""
    for value in SECRETS + FRAGMENTS:
        assert value in PLANTED
