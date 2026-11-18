"""``WorkerUnsubscribe`` on ``httpx2.MockTransport`` (``nextron.adapters.unsubscribe``, Story
3.11): the host refused before anything, the bearer read on every call and checked, the one GET
and its headers, no redirect followed, the pending list and its malformed entries, every answer
row of the status table, bad bodies, one usage record per attempted request, and no bearer,
token, host or body in any error or ``repr``. Nothing here reaches a Worker."""

import json
import traceback
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

import nextron.adapters.unsubscribe as adapter
from nextron.adapters.unsubscribe import WorkerUnsubscribe
from nextron.domain.budget import ApiCall
from nextron.domain.unsub_requests import PENDING_FORMAT
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET, KEYRING_FAILED
from nextron.ports.unsubscribe import PendingFetch, PendingUnsub, UnsubscribePort
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

BEARER = "Ub4Xq8Lm2Vz6Np1Rt5Hk9Jc3Wd7Gs0Fy4Tb8Mn2Qe6Ah"  # 44 characters
OTHER_BEARER = "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp9Oo8Nn7Mm6L"
SECRET_NAME = "unsub/bearer"
HOST = "u.example-sender.com"
TOKEN_1 = "A" * 108
TOKEN_2 = "B" * 107 + "_"
LEAK = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"  # text in a response body that must never reach an error
OK_CALL = ApiCall("unsub_worker", "kv_list", ok=True)


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


def secrets_with(bearer: str | None = BEARER) -> InMemorySecrets:
    secrets = InMemorySecrets()
    if bearer is not None:
        secrets.write(SECRET_NAME, bearer)
    return secrets


def worker_on(
    recorder: Recorder,
    secrets: InMemorySecrets | None = None,
    meter: InMemoryUsageMeter | None = None,
) -> WorkerUnsubscribe:
    return WorkerUnsubscribe(
        secrets if secrets is not None else secrets_with(),
        meter=meter if meter is not None else InMemoryUsageMeter(),
        http=httpx2.Client(transport=httpx2.MockTransport(recorder)),
    )


def document(*entries: object, format: object = PENDING_FORMAT) -> dict[str, Any]:  # noqa: A002
    return {"format": format, "entries": list(entries)}


def answer(status: int, body: object = None) -> httpx2.Response:
    if body is None:
        return httpx2.Response(status, content=b"")
    return httpx2.Response(status, json=body)


def assert_clean(error: BaseException) -> None:
    """Neither the bearer, a token, the host nor a response body is in the error, its ``repr``
    or its formatted traceback."""
    shown = "\n".join(
        [str(error), repr(error), *traceback.format_exception(error), str(error.args)]
    )
    for planted in (BEARER, TOKEN_1, TOKEN_2, HOST, LEAK):
        assert planted not in shown
    assert error.__cause__ is None and error.__suppress_context__


def test_it_implements_the_port() -> None:
    worker = WorkerUnsubscribe(InMemorySecrets(), meter=InMemoryUsageMeter())

    assert isinstance(worker, UnsubscribePort)


def test_a_fetch_sends_one_get_with_the_bearer_and_parses_the_entries_in_order() -> None:
    recorder = Recorder(
        answer(
            200,
            document(
                {"token": TOKEN_1, "received_at": "2026-11-17T22:15:30Z", "expiration": 1},
                {"token": TOKEN_2, "received_at": "2026-11-18T03:45:30+05:30"},
            ),
        )
    )
    meter = InMemoryUsageMeter()

    found = worker_on(recorder, meter=meter).fetch_pending(HOST)

    assert found == PendingFetch(
        (
            PendingUnsub(TOKEN_1, datetime(2026, 11, 17, 22, 15, 30, tzinfo=UTC)),
            PendingUnsub(TOKEN_2, datetime(2026, 11, 17, 22, 15, 30, tzinfo=UTC)),
        ),
        0,
    )
    assert found.entries[1].received_at.tzinfo is UTC
    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("GET", f"https://{HOST}/pending")
    assert request.headers["authorization"] == f"Bearer {BEARER}"
    assert request.headers["accept"] == "application/json"
    assert meter.calls == [OK_CALL]


def test_an_empty_list_is_an_empty_fetch() -> None:
    assert worker_on(Recorder(answer(200, document()))).fetch_pending(HOST) == PendingFetch((), 0)


@pytest.mark.parametrize(
    "entry",
    [
        "not an object",
        7,
        None,
        [TOKEN_1],
        {"received_at": "2026-11-17T22:15:30Z"},
        {"token": 7, "received_at": "2026-11-17T22:15:30Z"},
        {"token": TOKEN_1},
        {"token": TOKEN_1, "received_at": 1763417730},
        {"token": TOKEN_1, "received_at": "2026-11-17T22:15:30"},  # no offset
        {"token": TOKEN_1, "received_at": "2026-11-17"},
        {"token": TOKEN_1, "received_at": "yesterday"},
        {"token": TOKEN_1, "received_at": "0001-01-01T00:00:00+05:00"},  # leaves the calendar
    ],
    ids=repr,
)
def test_a_malformed_entry_is_counted_and_skipped(entry: object) -> None:
    good = {"token": TOKEN_2, "received_at": "2026-11-17T22:15:30Z"}
    recorder = Recorder(answer(200, document(entry, good)))

    found = worker_on(recorder).fetch_pending(HOST)

    assert [e.token for e in found.entries] == [TOKEN_2]
    assert found.malformed == 1


def test_the_bearer_is_read_on_every_call() -> None:
    secrets = secrets_with()
    recorder = Recorder(answer(200, document()))
    worker = worker_on(recorder, secrets=secrets)

    worker.fetch_pending(HOST)
    secrets.write(SECRET_NAME, OTHER_BEARER)
    worker.fetch_pending(HOST)

    assert [r.headers["authorization"] for r in recorder.requests] == [
        f"Bearer {BEARER}",
        f"Bearer {OTHER_BEARER}",
    ]


@pytest.mark.parametrize(
    "host",
    [
        "",
        "   ",
        "example.com",
        "x.example.com",
        "u.example",
        "U.Example.com",
        "u.example.com.",
        "https://u.example.com",
        "u.example.com:443",
        "u.example.com/pending",
        "u.workers.dev",
        "u.nextron.workers.dev",
        "u.-bad.com",
        "u." + "a" * 64 + ".com",
        "u." + ".".join(["abcdefgh"] * 30) + ".com",
        None,
        7,
    ],
    ids=repr,
)
def test_a_bad_host_is_refused_with_nothing_read_sent_or_metered(host: object) -> None:
    secrets = secrets_with()
    secrets.calls.clear()
    recorder = Recorder(answer(200, document()))
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        worker_on(recorder, secrets=secrets, meter=meter).fetch_pending(host)  # type: ignore[arg-type]

    assert (raised.value.code, raised.value.retryable) == ("unsub_refused", False)
    assert (recorder.requests, meter.calls, secrets.calls) == ([], [], [])


def test_a_missing_bearer_is_not_set_and_nothing_is_sent_or_metered() -> None:
    recorder = Recorder(answer(200, document()))
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        worker_on(recorder, secrets=secrets_with(None), meter=meter).fetch_pending(HOST)

    assert raised.value.code == CREDENTIAL_NOT_SET
    assert (recorder.requests, meter.calls) == ([], [])


def test_a_keyring_failure_propagates_with_nothing_sent() -> None:
    secrets = secrets_with()
    secrets.failing.add("read")
    recorder = Recorder(answer(200, document()))

    with pytest.raises(AdapterError) as raised:
        worker_on(recorder, secrets=secrets).fetch_pending(HOST)

    assert raised.value.code == KEYRING_FAILED
    assert recorder.requests == []


@pytest.mark.parametrize(
    "bearer", ["short", BEARER[:42], BEARER[:21] + " " + BEARER[22:], BEARER[:42] + "é"]
)
def test_a_bearer_too_short_or_not_visible_ascii_is_invalid_with_no_request(bearer: str) -> None:
    recorder = Recorder(answer(200, document()))
    meter = InMemoryUsageMeter()

    secrets = InMemorySecrets()
    secrets.store[(secrets.service, SECRET_NAME)] = bearer  # as Credential Manager may hold it

    with pytest.raises(AdapterError) as raised:
        worker_on(recorder, secrets=secrets, meter=meter).fetch_pending(HOST)

    assert (raised.value.code, raised.value.retryable) == ("unsub_bearer_invalid", False)
    assert (recorder.requests, meter.calls) == ([], [])
    assert bearer not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "unsub_unauthorized", False),
        (403, "unsub_unauthorized", False),
        (429, "unsub_unavailable", True),
        (500, "unsub_unavailable", True),
        (502, "unsub_unavailable", True),
        (503, "unsub_unavailable", True),
        (301, "unsub_refused", False),
        (302, "unsub_refused", False),
        (400, "unsub_refused", False),
        (404, "unsub_refused", False),
        (418, "unsub_refused", False),
    ],
)
def test_each_status_maps_to_its_code(status: int, code: str, retryable: bool) -> None:
    meter = InMemoryUsageMeter()
    headers = {"Location": f"https://{HOST}/elsewhere"} if 300 <= status < 400 else {}
    recorder = Recorder(httpx2.Response(status, headers=headers, json={"error": LEAK}))

    with pytest.raises(AdapterError) as raised:
        worker_on(recorder, meter=meter).fetch_pending(HOST)

    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    assert len(recorder.requests) == 1  # a redirect is never followed
    assert meter.calls == [ApiCall("unsub_worker", "kv_list", ok=False, error_code=code)]
    assert_clean(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, content=b"not json " + LEAK.encode()),
        httpx2.Response(200, content=b""),
        answer(200, [LEAK]),
        answer(200, "a string"),
        answer(200, {"entries": []}),  # no format
        answer(200, document(format="nextron-unsub-pending/2")),
        answer(200, document(format=None)),
        answer(200, {"format": PENDING_FORMAT}),  # no entries
        answer(200, {"format": PENDING_FORMAT, "entries": {"token": LEAK}}),
        answer(200, {"format": PENDING_FORMAT, "entries": None}),
        httpx2.Response(200, content=b"{" + b" " * 10_000_000 + b"}"),  # over 10 MB
    ],
    ids=lambda response: response.content[:40].decode(errors="replace"),
)
def test_a_bad_body_is_a_bad_response(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        worker_on(Recorder(response), meter=meter).fetch_pending(HOST)

    assert (raised.value.code, raised.value.retryable) == ("unsub_bad_response", False)
    assert meter.calls == [
        ApiCall("unsub_worker", "kv_list", ok=False, error_code="unsub_bad_response")
    ]
    assert_clean(raised.value)


def test_a_body_of_exactly_10_mb_is_read() -> None:
    body = json.dumps(document()).encode()
    padded = body[:-1] + b" " * (10_000_000 - len(body)) + b"}"

    assert worker_on(Recorder(httpx2.Response(200, content=padded))).fetch_pending(HOST) == (
        PendingFetch((), 0)
    )


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(LEAK),
        httpx2.ReadTimeout(LEAK),
        httpx2.RemoteProtocolError(LEAK),
        OSError(LEAK),
        ValueError(LEAK),
    ],
    ids=lambda error: type(error).__name__,
)
def test_a_transport_failure_is_retryable_unavailable(failure: BaseException) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        worker_on(Recorder(failure), meter=meter).fetch_pending(HOST)

    assert (raised.value.code, raised.value.retryable) == ("unsub_unavailable", True)
    assert_clean(raised.value)
    assert meter.calls == [
        ApiCall("unsub_worker", "kv_list", ok=False, error_code="unsub_unavailable")
    ]


def test_each_call_records_exactly_one_metered_call() -> None:
    meter = InMemoryUsageMeter()
    worker = worker_on(Recorder(answer(200, document())), meter=meter)

    worker.fetch_pending(HOST)
    worker.fetch_pending(HOST)

    assert meter.calls == [OK_CALL, OK_CALL]
    assert [call.units for call in meter.calls] == [1, 1]


def test_no_secret_or_token_is_in_a_repr_or_an_error() -> None:
    worker = worker_on(Recorder(answer(401, {"error": LEAK, "bearer": BEARER})))

    with pytest.raises(AdapterError) as raised:
        worker.fetch_pending(HOST)

    assert BEARER not in repr(worker)
    assert_clean(raised.value)
    assert BEARER not in json.dumps(vars(raised.value), default=str)
    entry = PendingUnsub(TOKEN_1, datetime(2026, 11, 17, tzinfo=UTC))
    assert TOKEN_1 not in repr(entry)


def test_a_pending_entry_checks_its_values() -> None:
    with pytest.raises(TypeError):
        PendingUnsub(7, datetime(2026, 11, 17, tzinfo=UTC))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PendingUnsub(TOKEN_1, "2026-11-17")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        PendingUnsub(TOKEN_1, datetime(2026, 11, 17))


def test_the_live_client_is_built_on_the_first_call_and_refused_in_tests() -> None:
    meter = InMemoryUsageMeter()
    worker = WorkerUnsubscribe(secrets_with(), meter=meter)  # builds no client

    with pytest.raises(LiveAdapterInTests):
        worker.fetch_pending(HOST)

    assert meter.records == []


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.unsubscribe", "default_http_client")

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


def test_the_client_is_built_once(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(answer(200, document()))
    built: list[httpx2.Client] = []

    def build() -> httpx2.Client:
        built.append(httpx2.Client(transport=httpx2.MockTransport(recorder)))
        return built[-1]

    monkeypatch.setattr(adapter, "default_http_client", build)
    worker = WorkerUnsubscribe(secrets_with(), meter=InMemoryUsageMeter())

    worker.fetch_pending(HOST)
    worker.fetch_pending(HOST)

    assert len(built) == 1 and len(recorder.requests) == 2
