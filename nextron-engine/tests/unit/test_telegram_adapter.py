"""``TelegramAlert`` on ``httpx2.MockTransport`` (``nextron.adapters.telegram``): the exact URL
path and JSON body, every outcome row, the token read on each send, no secret or response text
in any error, and the lazy default client (refused in tests). Nothing here reaches Telegram."""

import json
import traceback

import httpx2
import pytest

import nextron.adapters.telegram as adapter
from nextron.adapters.telegram import (
    TELEGRAM_BAD_CHAT_ID,
    TELEGRAM_BAD_TOKEN,
    TELEGRAM_REFUSED,
    TELEGRAM_UNAVAILABLE,
    TelegramAlert,
)
from nextron.ports.alert import DELIVERY_UNKNOWN, AlertDelivery, AlertMessage, AlertPort
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET, KEYRING_FAILED
from tests.fakes.secrets import InMemorySecrets
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

TOKEN = "123456789:AAHk3Wq9Zr5Lp2Xc8Vn4Bm7Ty1Ju6Gd0Fs"
OTHER_TOKEN = "987654321:BBJm2Xq8Yr4Kp1Wc7Un3Am6Sy0Hu5Fd9Er"
CHAT = "-1001234567890"
TEXT = "Job dead: send_alert 01JD3QZ8X5V9K2M7N4P6R8T0WY, attempts: 6"
LEAK = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"  # text in a response body that must never reach an error
SECRET_NAME = "telegram/bot_token"


class Recorder:
    """A ``MockTransport`` handler that records each request and answers ``response``."""

    def __init__(self, response: httpx2.Response | BaseException) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def secrets_with(token: str | None = TOKEN) -> InMemorySecrets:
    secrets = InMemorySecrets()
    if token is not None:
        secrets.write(SECRET_NAME, token)
    return secrets


def alert_on(recorder: Recorder, secrets: InMemorySecrets | None = None) -> TelegramAlert:
    return TelegramAlert(
        secrets if secrets is not None else secrets_with(),
        http=httpx2.Client(transport=httpx2.MockTransport(recorder)),
    )


def sent(message_id: object = 42, **extra: object) -> httpx2.Response:
    result = {"message_id": message_id, "chat": {"id": int(CHAT)}, "text": TEXT}
    return httpx2.Response(200, json={"ok": True, "result": result, **extra})


def assert_clean(error: BaseException) -> None:
    """Neither the token, the chat id, the text nor a response body is in the error, its
    ``repr`` or its formatted traceback."""
    shown = "\n".join(
        [str(error), repr(error), *traceback.format_exception(error), str(error.args)]
    )
    for planted in (TOKEN, TOKEN.split(":")[1], CHAT, TEXT, LEAK, "api.telegram.org"):
        assert planted not in shown
    assert error.__cause__ is None and error.__suppress_context__


def test_it_implements_the_alert_port() -> None:
    assert isinstance(TelegramAlert(InMemorySecrets()), AlertPort)


def test_a_send_posts_exactly_the_chat_id_and_the_text_to_send_message() -> None:
    recorder = Recorder(sent(42))

    delivery = alert_on(recorder).send(AlertMessage(CHAT, TEXT))

    assert delivery == AlertDelivery("42")
    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body == {"chat_id": -1001234567890, "text": TEXT}
    assert "parse_mode" not in request.content.decode()


def test_the_text_goes_as_it_is_even_when_it_looks_like_markup() -> None:
    recorder = Recorder(sent(7))
    text = "<b>bold</b> *star* _under_ [link](http://x) \U0001f600\nline two"

    alert_on(recorder).send(AlertMessage("42", text))

    assert json.loads(recorder.requests[0].content) == {"chat_id": 42, "text": text}


def test_the_token_is_read_on_each_send() -> None:
    secrets = secrets_with()
    recorder = Recorder(sent(1))
    telegram = alert_on(recorder, secrets)

    telegram.send(AlertMessage(CHAT, TEXT))
    secrets.write(SECRET_NAME, OTHER_TOKEN)
    telegram.send(AlertMessage(CHAT, TEXT))

    assert [request.url.path for request in recorder.requests] == [
        f"/bot{TOKEN}/sendMessage",
        f"/bot{OTHER_TOKEN}/sendMessage",
    ]
    assert secrets.calls.count(("read", SECRET_NAME)) == 2


# Outcomes ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, json={"ok": True, "result": {"message_id": True}}),
        httpx2.Response(200, json={"ok": True, "result": {"message_id": "42"}}),
        httpx2.Response(200, json={"ok": True, "result": {"message_id": 4.2}}),
        httpx2.Response(200, json={"ok": True, "result": {}}),
        httpx2.Response(200, json={"ok": True, "result": [42]}),
        httpx2.Response(200, json={"ok": False, "result": {"message_id": 42}}),
        httpx2.Response(200, json={"ok": "true", "result": {"message_id": 42}}),
        httpx2.Response(200, json=[{"ok": True}]),
        httpx2.Response(200, text=f"<html>{LEAK}</html>"),
        httpx2.Response(200, content=b"\xff\xfe"),
        httpx2.Response(204),
        httpx2.Response(201, json={"ok": True, "description": LEAK}),
    ],
)
def test_any_other_2xx_is_an_unknown_outcome(response: httpx2.Response) -> None:
    with pytest.raises(AdapterError) as raised:
        alert_on(Recorder(response)).send(AlertMessage(CHAT, TEXT))

    error = raised.value
    assert (error.retryable, error.code) == (False, DELIVERY_UNKNOWN)
    assert_clean(error)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_429_and_5xx_are_retryable(status: int) -> None:
    response = httpx2.Response(status, json={"ok": False, "description": LEAK})

    with pytest.raises(AdapterError) as raised:
        alert_on(Recorder(response)).send(AlertMessage(CHAT, TEXT))

    error = raised.value
    assert (error.retryable, error.code) == (True, TELEGRAM_UNAVAILABLE)
    assert error.message == f"Telegram answered with HTTP {status}"
    assert_clean(error)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 301, 302, 307, 100])
def test_any_other_status_is_refused_naming_only_the_status(status: int) -> None:
    response = httpx2.Response(
        status, json={"ok": False, "description": f"Bad Request: chat {LEAK} not found"}
    )

    with pytest.raises(AdapterError) as raised:
        alert_on(Recorder(response)).send(AlertMessage(CHAT, TEXT))

    error = raised.value
    assert (error.retryable, error.code) == (False, TELEGRAM_REFUSED)
    assert error.message == f"Telegram refused the alert with HTTP {status}"
    assert_clean(error)


NOT_SENT = [
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.PoolTimeout,
    httpx2.ProxyError,
    httpx2.UnsupportedProtocol,
    httpx2.LocalProtocolError,
    httpx2.WriteError,
    httpx2.WriteTimeout,
]


@pytest.mark.parametrize("error_type", NOT_SENT)
def test_a_failure_before_anything_reached_telegram_is_retryable(
    error_type: type[httpx2.TransportError],
) -> None:
    raised_by_transport = error_type(f"failed for /bot{TOKEN}/sendMessage {CHAT} {TEXT}")

    with pytest.raises(AdapterError) as raised:
        alert_on(Recorder(raised_by_transport)).send(AlertMessage(CHAT, TEXT))

    error = raised.value
    assert (error.retryable, error.code) == (True, TELEGRAM_UNAVAILABLE)
    assert_clean(error)


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ReadTimeout(f"read timed out {TOKEN}"),
        httpx2.ReadError(f"connection reset {TOKEN}"),
        httpx2.RemoteProtocolError(f"server hung up {TOKEN}"),
        httpx2.CloseError(f"close {TOKEN}"),
        httpx2.DecodingError(f"decoding {TOKEN}"),
        httpx2.TooManyRedirects(f"redirects {TOKEN}"),
        OSError(f"socket {TOKEN}"),
        RuntimeError(f"closed client {TOKEN}"),
        ValueError(f"weird {TOKEN}"),
    ],
)
def test_a_failure_after_the_request_may_have_gone_is_an_unknown_outcome(
    failure: Exception,
) -> None:
    with pytest.raises(AdapterError) as raised:
        alert_on(Recorder(failure)).send(AlertMessage(CHAT, TEXT))

    error = raised.value
    assert (error.retryable, error.code) == (False, DELIVERY_UNKNOWN)
    assert_clean(error)


# Not configured -------------------------------------------------------------------------------


def test_a_missing_token_is_not_set_and_nothing_is_sent() -> None:
    recorder = Recorder(sent())

    with pytest.raises(AdapterError) as raised:
        alert_on(recorder, secrets_with(None)).send(AlertMessage(CHAT, TEXT))

    assert (raised.value.retryable, raised.value.code) == (False, CREDENTIAL_NOT_SET)
    assert recorder.requests == []


def test_an_unreadable_credential_manager_propagates_and_nothing_is_sent() -> None:
    secrets = secrets_with()
    secrets.failing.add("read")
    recorder = Recorder(sent())

    with pytest.raises(AdapterError) as raised:
        alert_on(recorder, secrets).send(AlertMessage(CHAT, TEXT))

    assert raised.value.code == KEYRING_FAILED
    assert recorder.requests == []


@pytest.mark.parametrize(
    "token",
    ["abc", "123456789", ":AAH", "12a:AAH", "123:AA H", "123:AA/H", "123:AAH\n", f"x{TOKEN}"],
)
def test_a_malformed_token_is_refused_before_any_request(token: str) -> None:
    recorder = Recorder(sent())

    with pytest.raises(AdapterError) as raised:
        alert_on(recorder, secrets_with(token)).send(AlertMessage(CHAT, TEXT))

    error = raised.value
    assert (error.retryable, error.code) == (False, TELEGRAM_BAD_TOKEN)
    assert recorder.requests == []
    assert token not in str(error) and token not in repr(error)


@pytest.mark.parametrize("chat", ["@ops_channel", "", "0", "12 34", "1" * 21, "-", "+44"])
def test_a_chat_that_is_not_a_numeric_id_is_refused_before_any_request(chat: str) -> None:
    recorder = Recorder(sent())

    with pytest.raises(AdapterError) as raised:
        alert_on(recorder).send(AlertMessage(chat, TEXT))

    error = raised.value
    assert (error.retryable, error.code) == (False, TELEGRAM_BAD_CHAT_ID)
    assert recorder.requests == []
    if chat:
        assert chat not in str(error)


# The live client --------------------------------------------------------------------------------


def test_the_live_client_is_built_on_the_first_send_and_refused_in_tests() -> None:
    telegram = TelegramAlert(secrets_with())  # builds no client

    with pytest.raises(LiveAdapterInTests):
        telegram.send(AlertMessage(CHAT, TEXT))


def test_no_client_is_built_when_the_send_is_refused_first() -> None:
    telegram = TelegramAlert(secrets_with(None))

    with pytest.raises(AdapterError) as raised:
        telegram.send(AlertMessage(CHAT, TEXT))

    assert raised.value.code == CREDENTIAL_NOT_SET


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.telegram", "default_http_client")

    client = adapter.default_http_client()
    try:
        assert client.follow_redirects is False
        timeout = client.timeout
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
            10.0,
            15.0,
            15.0,
            15.0,
        )
    finally:
        client.close()


def test_the_client_is_built_once_and_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(sent(5))
    built: list[httpx2.Client] = []

    def build() -> httpx2.Client:
        built.append(httpx2.Client(transport=httpx2.MockTransport(recorder)))
        return built[-1]

    monkeypatch.setattr(adapter, "default_http_client", build)
    telegram = TelegramAlert(secrets_with())

    telegram.send(AlertMessage(CHAT, TEXT))
    telegram.send(AlertMessage(CHAT, TEXT))

    assert len(built) == 1 and len(recorder.requests) == 2
