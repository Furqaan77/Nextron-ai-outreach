"""``GooglePostmaster`` on ``httpx2.MockTransport`` (``nextron.adapters.postmaster``, Story 6.6):
the domain and day refused before anything, the token read on every call, the one GET and its
URL and headers, every answer row of the status table, bad bodies, one usage record per
attempted request, and no token, domain or body in any error. Nothing here reaches Google."""

import traceback
from datetime import date, datetime
from typing import Any

import httpx2
import pytest

import nextron.adapters.postmaster as adapter
from nextron.adapters.postmaster import GooglePostmaster
from nextron.domain.budget import ApiCall
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import GOOGLE_NEEDS_RECONSENT, GOOGLE_NOT_GRANTED
from nextron.ports.postmaster import (
    POSTMASTER_BAD_RESPONSE,
    POSTMASTER_REJECTED,
    POSTMASTER_UNAVAILABLE,
    PostmasterPort,
    PostmasterReading,
)
from tests.fakes.postmaster import FakePostmaster
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

TOKEN = "Pt7Kw3Zq9Lm2Xv6Nr4Hc"
DOMAIN = "nextron-mail.com"
DAY = date(2026, 11, 18)
URL = f"https://gmailpostmastertools.googleapis.com/v1/domains/{DOMAIN}/trafficStats/20261118"
LEAK = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"
OK_CALL = ApiCall("postmaster", "traffic_stats", ok=True)


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


class Tokens:
    """The token callable: counts its calls, answers ``TOKEN`` or raises ``error``."""

    def __init__(self, error: AdapterError | None = None) -> None:
        self.error = error
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return TOKEN


def postmaster_on(
    recorder: Recorder, tokens: Tokens | None = None, meter: InMemoryUsageMeter | None = None
) -> GooglePostmaster:
    return GooglePostmaster(
        token=tokens if tokens is not None else Tokens(),
        meter=meter if meter is not None else InMemoryUsageMeter(),
        http=httpx2.Client(transport=httpx2.MockTransport(recorder)),
    )


def stats(body: object, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json=body)


def assert_clean(error: BaseException) -> None:
    formatted = "".join(traceback.format_exception(error)) + repr(error)
    for secret in (TOKEN, DOMAIN, LEAK):
        assert secret not in formatted


def test_the_adapter_and_the_fake_satisfy_the_port() -> None:
    assert isinstance(postmaster_on(Recorder(stats({}))), PostmasterPort)
    assert isinstance(FakePostmaster(), PostmasterPort)


@pytest.mark.parametrize(
    ("ratio", "ppm"), [(0.0009, 900), (0.001, 1000), (0.0029, 2900), (0.003, 3000), (0, 0)]
)
def test_a_ratio_is_read_in_ppm(ratio: float, ppm: int) -> None:
    recorder = Recorder(stats({"name": "x", "userReportedSpamRatio": ratio, "other": LEAK}))
    meter = InMemoryUsageMeter()

    reading = postmaster_on(recorder, meter=meter).spam_rate(DOMAIN, DAY)

    assert reading == PostmasterReading(ppm)
    [request] = recorder.requests
    assert request.method == "GET"
    assert str(request.url) == URL
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert meter.calls == [OK_CALL]


def test_the_domain_is_lower_cased_without_its_final_dot() -> None:
    recorder = Recorder(stats({}))

    postmaster_on(recorder).spam_rate("Nextron-Mail.COM.", DAY)

    assert str(recorder.requests[0].url) == URL


@pytest.mark.parametrize(
    "response",
    [stats({}), stats({"name": "domains/x/trafficStats/20261118"}), stats({}, 404), stats({}, 403)],
)
def test_no_ratio_a_404_and_a_403_are_no_data(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    reading = postmaster_on(Recorder(response), meter=meter).spam_rate(DOMAIN, DAY)

    assert reading == PostmasterReading(None)
    assert reading.ppm is None  # never 0
    assert meter.calls == [OK_CALL]


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (stats({}, 429), POSTMASTER_UNAVAILABLE, True),
        (stats({}, 500), POSTMASTER_UNAVAILABLE, True),
        (stats({}, 503), POSTMASTER_UNAVAILABLE, True),
        (stats({}, 400), POSTMASTER_REJECTED, False),
        (stats({}, 401), POSTMASTER_REJECTED, False),
        (stats({}, 302), POSTMASTER_REJECTED, False),
        (httpx2.Response(200, text=f"not json {LEAK}"), POSTMASTER_BAD_RESPONSE, False),
        (stats([LEAK]), POSTMASTER_BAD_RESPONSE, False),
        (stats({"userReportedSpamRatio": "0.1"}), POSTMASTER_BAD_RESPONSE, False),
        (stats({"userReportedSpamRatio": True}), POSTMASTER_BAD_RESPONSE, False),
        (stats({"userReportedSpamRatio": None}), POSTMASTER_BAD_RESPONSE, False),
        (stats({"userReportedSpamRatio": 1.5}), POSTMASTER_BAD_RESPONSE, False),
        (stats({"userReportedSpamRatio": -0.1}), POSTMASTER_BAD_RESPONSE, False),
        (httpx2.ConnectError(f"down {LEAK} {TOKEN}"), POSTMASTER_UNAVAILABLE, True),
        (httpx2.ReadTimeout(f"slow {LEAK}"), POSTMASTER_UNAVAILABLE, True),
        (OSError(f"network {LEAK}"), POSTMASTER_UNAVAILABLE, True),
    ],
)
def test_each_failure_is_its_adapter_error_and_metered_once(
    response: Any, code: str, retryable: bool
) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        postmaster_on(Recorder(response), meter=meter).spam_rate(DOMAIN, DAY)

    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    assert meter.calls == [ApiCall("postmaster", "traffic_stats", ok=False, error_code=code)]
    assert_clean(raised.value)


def test_the_token_is_read_on_every_call() -> None:
    tokens = Tokens()
    postmaster = postmaster_on(Recorder(stats({})), tokens)

    postmaster.spam_rate(DOMAIN, DAY)
    postmaster.spam_rate(DOMAIN, DAY)

    assert tokens.calls == 2


@pytest.mark.parametrize("code", [GOOGLE_NOT_GRANTED, GOOGLE_NEEDS_RECONSENT])
def test_a_token_error_propagates_before_any_request(code: str) -> None:
    recorder = Recorder(stats({}))
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        postmaster_on(recorder, Tokens(AdapterError(False, code, "no grant")), meter).spam_rate(
            DOMAIN, DAY
        )

    assert raised.value.code == code
    assert recorder.requests == []
    assert meter.records == []


@pytest.mark.parametrize(
    ("domain", "day"),
    [
        ("", DAY),
        ("not a domain", DAY),
        (None, DAY),
        (DOMAIN, "2026-11-18"),
        (DOMAIN, datetime(2026, 11, 18, 7, 0)),  # noqa: DTZ001 - a datetime is no date
    ],
)
def test_a_bad_domain_or_day_is_refused_before_anything(domain: Any, day: Any) -> None:
    recorder = Recorder(stats({}))
    tokens = Tokens()
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        postmaster_on(recorder, tokens, meter).spam_rate(domain, day)

    assert raised.value.code == POSTMASTER_REJECTED
    assert (recorder.requests, tokens.calls, meter.records) == ([], 0, [])


def test_the_guard_refuses_the_live_client() -> None:
    meter = InMemoryUsageMeter()
    postmaster = GooglePostmaster(token=Tokens(), meter=meter)  # builds no client

    with pytest.raises(LiveAdapterInTests):
        postmaster.spam_rate(DOMAIN, DAY)

    assert meter.records == []


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.postmaster", "default_http_client")

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


def test_a_reading_refuses_a_bad_ppm() -> None:
    with pytest.raises(TypeError):
        PostmasterReading(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PostmasterReading(1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PostmasterReading(-1)
    with pytest.raises(ValueError):
        PostmasterReading(1_000_001)
