"""``ProspeoFinder`` on ``httpx2.MockTransport`` (``nextron.adapters.prospeo``): the exact POST
(URL, JSON body and the key only in the ``X-KEY`` header), the verified address it reads, every
status and ``error_code`` mapping, bad shapes, the key read on each call, one usage record per
request (``count`` 1 for a 2xx, so 1 unit, else 0), nothing for a call refused before any
request, no key, URL, domain, name, address or body in any error, and the lazy default client
(refused in tests). Nothing here reaches Prospeo."""

import json
import sys
import traceback

import httpx2
import pytest

import nextron.adapters.prospeo as adapter
from nextron.adapters.prospeo import (
    FIND,
    PROSPEO_BAD_ANSWER,
    PROSPEO_REFUSED,
    PROSPEO_UNAVAILABLE,
    PROVIDER,
    ProspeoFinder,
)
from nextron.domain.budget import AiCall, ApiCall
from nextron.ports.email_finder import EmailFinderPort, FinderAnswer, FinderQuery
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET, KEYRING_FAILED
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

KEY = "Pk4Wd8Ln2Qz6Rv9Tx3Hm"
OTHER_KEY = "Pk7Bc1Yt5Gs9Jm3Ve8Ka"
SECRET_NAME = "prospeo/api_key"
DOMAIN = "roe-partners.example"
FIRST = "Juliana"
LAST = "Ravensworth"
QUERY = FinderQuery(DOMAIN, FIRST, LAST)
ADDRESS = "juliana.ravensworth@roe-partners.example"
LEAK = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"  # text in a response body that must never reach an error
PLANTED = (KEY, DOMAIN, FIRST, LAST, ADDRESS, LEAK, "api.prospeo.io")


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


def secrets_with(key: str | None = KEY) -> InMemorySecrets:
    secrets = InMemorySecrets()
    if key is not None:
        secrets.write(SECRET_NAME, key)
    return secrets


def prospeo_on(
    recorder: Recorder,
    secrets: InMemorySecrets | None = None,
    meter: InMemoryUsageMeter | None = None,
) -> ProspeoFinder:
    return ProspeoFinder(
        secrets if secrets is not None else secrets_with(),
        meter=meter if meter is not None else InMemoryUsageMeter(),
        http=httpx2.Client(transport=httpx2.MockTransport(recorder)),
    )


def enriched(email: object = ADDRESS, *, status: int = 200) -> httpx2.Response:
    """An Enrich Person answer, shaped as Prospeo's."""
    person = {
        "first_name": FIRST,
        "last_name": LAST,
        "full_name": f"{FIRST} {LAST}",
        "email": {"status": "VERIFIED", "revealed": True, "email": email},
    }
    body = {"error": False, "free_enrichment": False, "person": person, "company": {}}
    return httpx2.Response(status, json=body)


def failed(status: int, code: str | None) -> httpx2.Response:
    """A Prospeo error answer: ``{"error": true, "error_code": code}``."""
    body: dict[str, object] = {"error": True, "filter_error": f"{LEAK} {ADDRESS}"}
    if code is not None:
        body["error_code"] = code
    return httpx2.Response(status, json=body)


def assert_clean(error: BaseException) -> None:
    """Neither the key, the domain, a name, an address, a body nor the host is in the error,
    its ``repr`` or its formatted traceback."""
    shown = "\n".join(
        [str(error), repr(error), *traceback.format_exception(error), str(error.args)]
    )
    for planted in PLANTED:
        assert planted not in shown
    assert error.__cause__ is None and error.__suppress_context__


def test_it_implements_the_email_finder_port() -> None:
    assert isinstance(ProspeoFinder(InMemorySecrets(), meter=InMemoryUsageMeter()), EmailFinderPort)


def test_a_find_posts_the_names_and_the_website_for_a_verified_address_only() -> None:
    recorder = Recorder(enriched())

    prospeo_on(recorder).find_email(QUERY)

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.prospeo.io/enrich-person"
    assert request.headers["x-key"] == KEY
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "only_verified_email": True,
        "data": {"first_name": FIRST, "last_name": LAST, "company_website": DOMAIN},
    }
    assert KEY not in str(request.url) and KEY.encode() not in request.content


# Answers ----------------------------------------------------------------------------------------


def test_a_find_answers_the_verified_address() -> None:
    assert prospeo_on(Recorder(enriched())).find_email(QUERY) == FinderAnswer(ADDRESS)


@pytest.mark.parametrize(
    "body",
    [
        {"error": False, "person": {"email": {"email": None}}},
        {"error": False, "person": {"email": {"email": ""}}},
        {"error": False, "person": {"email": {"email": "   "}}},
        {"error": False, "person": {"email": {"email": 42}}},
        {"error": False, "person": {"email": {"email": [ADDRESS]}}},
        {"error": False, "person": {"email": {"status": "UNAVAILABLE"}}},
        {"error": False, "person": {"email": ADDRESS}},
        {"error": False, "person": {"email": None}},
        {"error": False, "person": {"first_name": FIRST}},
        {"error": False, "person": None},
        {"error": False, "person": [ADDRESS]},
        {"error": False},
    ],
)
def test_a_2xx_without_a_str_address_answers_none(body: dict[str, object]) -> None:
    response = httpx2.Response(200, json=body)

    assert prospeo_on(Recorder(response)).find_email(QUERY) == FinderAnswer(None)


def test_a_400_no_match_is_nobody_found_and_not_billed() -> None:
    meter = InMemoryUsageMeter()

    answer = prospeo_on(Recorder(failed(400, "NO_MATCH")), meter=meter).find_email(QUERY)

    assert answer == FinderAnswer(None)
    assert meter.calls == [ApiCall(PROVIDER, FIND, ok=True, count=0)]


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text=f"<html>{LEAK}</html>"),
        httpx2.Response(200, content=b"\xff\xfe"),
        httpx2.Response(204),
        httpx2.Response(200, json=[{"error": False}]),
        httpx2.Response(200, json={"person": {"email": {"email": ADDRESS}}}),
        httpx2.Response(200, json={"error": "false", "person": {"email": {"email": ADDRESS}}}),
        httpx2.Response(200, json={"error": 0, "person": {"email": {"email": ADDRESS}}}),
        httpx2.Response(200, json={"error": True, "error_code": "NO_MATCH"}),
        httpx2.Response(201, json={"error": True, "error_code": f"{LEAK}"}),
    ],
)
def test_any_other_2xx_is_a_bad_answer(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        prospeo_on(Recorder(response), meter=meter).find_email(QUERY)

    error = raised.value
    assert (error.retryable, error.code) == (False, PROSPEO_BAD_ANSWER)
    assert_clean(error)
    assert meter.calls == [
        ApiCall(PROVIDER, FIND, ok=False, error_code=PROSPEO_BAD_ANSWER, count=1)
    ]


# Error codes and statuses -----------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 402, 403, 429, 500, 200])
def test_insufficient_credits_is_retryable_whatever_the_status(status: int) -> None:
    with pytest.raises(AdapterError) as raised:
        prospeo_on(Recorder(failed(status, "INSUFFICIENT_CREDITS"))).find_email(QUERY)

    error = raised.value
    assert (error.retryable, error.code) == (True, PROSPEO_UNAVAILABLE)
    assert_clean(error)


@pytest.mark.parametrize("status", [401, 400, 403, 429, 500])
def test_an_invalid_api_key_is_refused_whatever_the_status(status: int) -> None:
    with pytest.raises(AdapterError) as raised:
        prospeo_on(Recorder(failed(status, "INVALID_API_KEY"))).find_email(QUERY)

    error = raised.value
    assert (error.retryable, error.code) == (False, PROSPEO_REFUSED)
    assert_clean(error)


@pytest.mark.parametrize(
    "response",
    [
        failed(429, "RATE_LIMITED"),
        failed(429, None),
        failed(500, "INTERNAL_ERROR"),
        failed(502, None),
        failed(503, "NO_MATCH"),
        httpx2.Response(504, text=LEAK),
        httpx2.Response(599),
    ],
)
def test_a_rate_limit_or_5xx_is_retryable(response: httpx2.Response) -> None:
    with pytest.raises(AdapterError) as raised:
        prospeo_on(Recorder(response)).find_email(QUERY)

    error = raised.value
    assert (error.retryable, error.code) == (True, PROSPEO_UNAVAILABLE)
    assert error.message == f"Prospeo answered with HTTP {response.status_code}"
    assert_clean(error)


@pytest.mark.parametrize(
    "response",
    [
        failed(400, "INVALID_DATAPOINTS"),
        failed(400, "INVALID_REQUEST"),
        failed(400, None),
        failed(401, None),
        failed(403, None),
        failed(404, "NO_MATCH"),  # NO_MATCH means none only with Prospeo's 400
        failed(422, None),
        httpx2.Response(400, text=f"<html>{LEAK}</html>"),
        httpx2.Response(400, json={"error": True, "error_code": 7}),
        httpx2.Response(301, headers={"location": "https://elsewhere.example/"}),
        httpx2.Response(302),
        httpx2.Response(100),
    ],
)
def test_any_other_status_is_refused_naming_only_the_status(response: httpx2.Response) -> None:
    with pytest.raises(AdapterError) as raised:
        prospeo_on(Recorder(response)).find_email(QUERY)

    error = raised.value
    assert (error.retryable, error.code) == (False, PROSPEO_REFUSED)
    assert error.message == f"Prospeo refused the request with HTTP {response.status_code}"
    assert_clean(error)


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(f"failed for {DOMAIN} {FIRST} {LAST} {KEY}"),
        httpx2.ConnectTimeout(f"timed out {KEY}"),
        httpx2.ReadTimeout(f"read timed out {KEY} {ADDRESS}"),
        httpx2.ReadError(f"connection reset {KEY}"),
        httpx2.RemoteProtocolError(f"server hung up {KEY}"),
        httpx2.WriteError(f"write {KEY}"),
        OSError(f"socket {KEY}"),
        RuntimeError(f"closed client {KEY}"),
        ValueError(f"weird {KEY}"),
    ],
)
def test_a_failed_request_is_retryable(failure: Exception) -> None:
    with pytest.raises(AdapterError) as raised:
        prospeo_on(Recorder(failure)).find_email(QUERY)

    error = raised.value
    assert (error.retryable, error.code) == (True, PROSPEO_UNAVAILABLE)
    assert_clean(error)


# The key ----------------------------------------------------------------------------------------


def test_the_key_is_read_on_each_call() -> None:
    secrets = secrets_with()
    recorder = Recorder(enriched())
    prospeo = prospeo_on(recorder, secrets)

    prospeo.find_email(QUERY)
    secrets.write(SECRET_NAME, OTHER_KEY)
    prospeo.find_email(QUERY)

    assert [request.headers["x-key"] for request in recorder.requests] == [KEY, OTHER_KEY]
    assert secrets.calls.count(("read", SECRET_NAME)) == 2


def test_a_missing_key_is_not_set_and_nothing_is_requested_or_recorded() -> None:
    recorder = Recorder(enriched())
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        prospeo_on(recorder, secrets_with(None), meter).find_email(QUERY)

    assert (raised.value.retryable, raised.value.code) == (False, CREDENTIAL_NOT_SET)
    assert recorder.requests == []
    assert meter.records == []


def test_an_unreadable_credential_manager_propagates_and_nothing_is_requested() -> None:
    secrets = secrets_with()
    secrets.failing.add("read")
    recorder = Recorder(enriched())
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        prospeo_on(recorder, secrets, meter).find_email(QUERY)

    assert raised.value.code == KEYRING_FAILED
    assert recorder.requests == []
    assert meter.records == []


# Metering ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "ok", "code", "count"),
    [
        (enriched(), True, None, 1),
        (enriched(None), True, None, 1),
        (failed(400, "NO_MATCH"), True, None, 0),
        (failed(400, "INSUFFICIENT_CREDITS"), False, PROSPEO_UNAVAILABLE, 0),
        (failed(401, "INVALID_API_KEY"), False, PROSPEO_REFUSED, 0),
        (failed(429, "RATE_LIMITED"), False, PROSPEO_UNAVAILABLE, 0),
        (httpx2.Response(500), False, PROSPEO_UNAVAILABLE, 0),
        (httpx2.ConnectError("refused"), False, PROSPEO_UNAVAILABLE, 0),
        (httpx2.Response(200, text="nope"), False, PROSPEO_BAD_ANSWER, 1),
    ],
    ids=[
        "200",
        "200_none",
        "400_no_match",
        "insufficient_credits",
        "invalid_api_key",
        "429",
        "500",
        "connect_error",
        "bad_answer",
    ],
)
def test_each_request_records_one_call_counting_only_a_2xx(
    response: httpx2.Response | BaseException, ok: bool, code: str | None, count: int
) -> None:
    meter = InMemoryUsageMeter()
    recorder = Recorder(response)

    try:
        prospeo_on(recorder, meter=meter).find_email(QUERY)
    except AdapterError as error:
        assert error.code == code

    assert len(recorder.requests) == 1
    assert meter.calls == [ApiCall("prospeo", "find", ok=ok, error_code=code, count=count)]
    assert meter.calls[0].units == count  # one unit per billed find


def test_a_2xx_find_stores_one_unit() -> None:
    meter = InMemoryUsageMeter()

    prospeo_on(Recorder(enriched()), meter=meter).find_email(QUERY)

    assert meter.snapshot(meter.clock.now()).units("prospeo") == 1


class RecordingFirst(InMemoryUsageMeter):
    """A meter that notes whether the error had already been raised when it recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.during: list[BaseException | None] = []

    def record(self, call: ApiCall | AiCall) -> None:
        self.during.append(sys.exc_info()[1])
        super().record(call)


def test_a_failed_request_is_recorded_before_its_error_is_raised() -> None:
    meter = RecordingFirst()

    with pytest.raises(AdapterError) as raised:
        prospeo_on(Recorder(httpx2.Response(500)), meter=meter).find_email(QUERY)

    assert meter.during == [raised.value]  # recorded while the error was being handled


# The live client --------------------------------------------------------------------------------


def test_the_live_client_is_built_on_the_first_request_and_refused_in_tests() -> None:
    meter = InMemoryUsageMeter()
    prospeo = ProspeoFinder(secrets_with(), meter=meter)  # builds no client

    with pytest.raises(LiveAdapterInTests):
        prospeo.find_email(QUERY)
    assert meter.records == []  # no request was attempted


def test_no_client_is_built_when_the_key_is_missing() -> None:
    prospeo = ProspeoFinder(secrets_with(None), meter=InMemoryUsageMeter())

    with pytest.raises(AdapterError) as raised:
        prospeo.find_email(QUERY)

    assert raised.value.code == CREDENTIAL_NOT_SET


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.prospeo", "default_http_client")

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
    recorder = Recorder(enriched())
    built: list[httpx2.Client] = []

    def build() -> httpx2.Client:
        built.append(httpx2.Client(transport=httpx2.MockTransport(recorder)))
        return built[-1]

    monkeypatch.setattr(adapter, "default_http_client", build)
    prospeo = ProspeoFinder(secrets_with(), meter=InMemoryUsageMeter())

    prospeo.find_email(QUERY)
    prospeo.find_email(QUERY)

    assert len(built) == 1 and len(recorder.requests) == 2
