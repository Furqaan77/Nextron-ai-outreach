"""``HunterFinder`` on ``httpx2.MockTransport`` (``nextron.adapters.hunter``): the exact GETs
(path, parameters and the key only in the ``X-API-KEY`` header), Domain Search's pattern and
people, Email Finder's address, 404 and 451, every status mapping, bad shapes, the key read on
each call, one usage record per request (``count`` 1 for a 2xx, so 2 units, else 0), nothing for
a call refused before any request, no key, URL, domain, name, address or body in any error, and
the lazy default client (refused in tests). Story 2.14: the Email Verifier's GET, every status
(200, 222 as ``unknown``, 202 as retryable and unbilled), its bad shapes and its metering.
Nothing here reaches Hunter."""

import sys
import traceback
from collections.abc import Callable

import httpx2
import pytest

import nextron.adapters.hunter as adapter
from nextron.adapters.hunter import (
    DOMAIN_SEARCH,
    FIND,
    HUNTER_BAD_ANSWER,
    HUNTER_REFUSED,
    HUNTER_UNAVAILABLE,
    PROVIDER,
    VERIFY,
    HunterFinder,
)
from nextron.domain.budget import AiCall, ApiCall
from nextron.ports.email_finder import (
    DomainPerson,
    DomainSearchAnswer,
    DomainSearchPort,
    EmailFinderPort,
    EmailVerifierPort,
    FinderAnswer,
    FinderQuery,
    VerifierAnswer,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET, KEYRING_FAILED
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

KEY = "Hk7Qw2Zr9Lp5Xc3Vn8Bm"
OTHER_KEY = "Hk1Ty6Ju4Gd0Fs2Wq9Ae"
SECRET_NAME = "hunter/api_key"
DOMAIN = "roe-partners.example"
FIRST = "Juliana"
LAST = "Ravensworth"
QUERY = FinderQuery(DOMAIN, FIRST, LAST)
ADDRESS = "juliana.ravensworth@roe-partners.example"
OTHER_ADDRESS = "marcus.quill@roe-partners.example"
LEAK = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"  # text in a response body that must never reach an error
PLANTED = (KEY, DOMAIN, FIRST, LAST, ADDRESS, OTHER_ADDRESS, LEAK, "api.hunter.io")


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


def hunter_on(
    recorder: Recorder,
    secrets: InMemorySecrets | None = None,
    meter: InMemoryUsageMeter | None = None,
) -> HunterFinder:
    return HunterFinder(
        secrets if secrets is not None else secrets_with(),
        meter=meter if meter is not None else InMemoryUsageMeter(),
        http=httpx2.Client(transport=httpx2.MockTransport(recorder)),
    )


def found(email: object = ADDRESS, **data: object) -> httpx2.Response:
    """An Email Finder answer, shaped as Hunter's."""
    body = {"first_name": FIRST, "last_name": LAST, "email": email, "score": 97, **data}
    return httpx2.Response(200, json={"data": body, "meta": {"params": {"domain": DOMAIN}}})


def searched(pattern: object = "{first}.{last}", emails: object = None) -> httpx2.Response:
    """A Domain Search answer, shaped as Hunter's."""
    if emails is None:
        emails = [
            {
                "value": ADDRESS,
                "type": "personal",
                "first_name": FIRST,
                "last_name": LAST,
                "position": "Managing Partner",
            }
        ]
    data = {"domain": DOMAIN, "pattern": pattern, "organization": "Roe", "emails": emails}
    return httpx2.Response(200, json={"data": data, "meta": {"results": 1}})


def errors_body(status: int) -> dict[str, object]:
    return {"errors": [{"id": "some_error", "code": status, "details": f"{LEAK} {ADDRESS}"}]}


def assert_clean(error: BaseException) -> None:
    """Neither the key, the domain, a name, an address, a body nor the host is in the error,
    its ``repr`` or its formatted traceback."""
    shown = "\n".join(
        [str(error), repr(error), *traceback.format_exception(error), str(error.args)]
    )
    for planted in PLANTED:
        assert planted not in shown
    assert error.__cause__ is None and error.__suppress_context__


def verified(status: object = "valid") -> httpx2.Response:
    """An Email Verifier answer, shaped as Hunter's."""
    data = {"status": status, "result": "deliverable", "score": 100, "email": ADDRESS}
    return httpx2.Response(200, json={"data": data, "meta": {"params": {"email": ADDRESS}}})


def test_it_implements_all_three_ports() -> None:
    hunter = HunterFinder(InMemorySecrets(), meter=InMemoryUsageMeter())

    assert isinstance(hunter, EmailFinderPort)
    assert isinstance(hunter, DomainSearchPort)
    assert isinstance(hunter, EmailVerifierPort)


# Domain Search ----------------------------------------------------------------------------------


def test_a_domain_search_gets_the_domain_with_a_limit_of_ten_and_the_key_in_the_header() -> None:
    recorder = Recorder(searched())

    hunter_on(recorder).domain_search(DOMAIN)

    [request] = recorder.requests
    assert request.method == "GET"
    assert (request.url.scheme, request.url.host, request.url.path) == (
        "https",
        "api.hunter.io",
        "/v2/domain-search",
    )
    assert dict(request.url.params) == {"domain": DOMAIN, "limit": "10"}
    assert request.headers["x-api-key"] == KEY
    assert KEY not in str(request.url)
    assert request.content == b""


def test_a_domain_search_reads_the_pattern_and_the_people() -> None:
    emails = [
        {"value": ADDRESS, "type": "personal", "first_name": FIRST, "last_name": LAST},
        {"value": "info@roe-partners.example", "type": "generic", "first_name": None},
        {"value": OTHER_ADDRESS, "type": "personal", "first_name": 7, "last_name": ["Quill"]},
        {"value": "nobody@roe-partners.example"},
        "not an object",
        ["also", "not"],
        None,
        {"type": "personal", "first_name": FIRST},  # no value
        {"value": 42, "type": "personal"},
        {"value": "   ", "type": "personal"},
    ]

    answer = hunter_on(Recorder(searched("{f}{last}", emails))).domain_search(DOMAIN)

    assert answer == DomainSearchAnswer(
        "{f}{last}",
        (
            DomainPerson(ADDRESS, FIRST, LAST, True),
            DomainPerson("info@roe-partners.example", None, None, False),
            DomainPerson(OTHER_ADDRESS, None, None, True),
            DomainPerson("nobody@roe-partners.example", None, None, False),
        ),
    )


def test_a_domain_search_without_a_pattern_or_emails_answers_none_and_nobody() -> None:
    body = {"data": {"domain": DOMAIN, "pattern": None}, "meta": {}}

    answer = hunter_on(Recorder(httpx2.Response(200, json=body))).domain_search(DOMAIN)

    assert answer == DomainSearchAnswer(None, ())


def test_a_domain_search_passes_any_pattern_text_on_as_it_is() -> None:
    answer = hunter_on(Recorder(searched("{last}{first}x", []))).domain_search(DOMAIN)

    assert answer == DomainSearchAnswer("{last}{first}x", ())


def test_the_answer_shows_no_address_or_name_in_its_repr() -> None:
    answer = hunter_on(Recorder(searched())).domain_search(DOMAIN)

    for planted in (ADDRESS, FIRST, LAST):
        assert planted not in repr(answer)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text=f"<html>{LEAK}</html>"),
        httpx2.Response(200, content=b"\xff\xfe"),
        httpx2.Response(204),
        httpx2.Response(200, json=[{"data": {}}]),
        httpx2.Response(200, json={"errors": errors_body(200)["errors"]}),
        httpx2.Response(200, json={"data": [ADDRESS]}),
        httpx2.Response(200, json={"data": None}),
        httpx2.Response(200, json={"data": {"pattern": 7, "emails": []}}),
        httpx2.Response(200, json={"data": {"pattern": ["{first}"], "emails": []}}),
        httpx2.Response(200, json={"data": {"pattern": None, "emails": {"value": ADDRESS}}}),
        httpx2.Response(200, json={"data": {"pattern": None, "emails": None}}),
        httpx2.Response(200, json={"data": {"pattern": None, "emails": ADDRESS}}),
    ],
)
def test_a_domain_search_2xx_of_another_shape_is_a_bad_answer(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        hunter_on(Recorder(response), meter=meter).domain_search(DOMAIN)

    error = raised.value
    assert (error.retryable, error.code) == (False, HUNTER_BAD_ANSWER)
    assert_clean(error)
    assert meter.calls == [
        ApiCall(PROVIDER, DOMAIN_SEARCH, ok=False, error_code=HUNTER_BAD_ANSWER, count=1)
    ]


# Email Finder -----------------------------------------------------------------------------------


def test_a_find_gets_the_domain_and_both_names_with_the_key_in_the_header() -> None:
    recorder = Recorder(found())

    hunter_on(recorder).find_email(QUERY)

    [request] = recorder.requests
    assert request.method == "GET"
    assert (request.url.scheme, request.url.host, request.url.path) == (
        "https",
        "api.hunter.io",
        "/v2/email-finder",
    )
    assert dict(request.url.params) == {"domain": DOMAIN, "first_name": FIRST, "last_name": LAST}
    assert request.headers["x-api-key"] == KEY
    assert KEY not in str(request.url)
    assert request.content == b""


def test_a_find_answers_the_address_hunter_found() -> None:
    assert hunter_on(Recorder(found())).find_email(QUERY) == FinderAnswer(ADDRESS)


@pytest.mark.parametrize("email", [None, "", "   "])
def test_a_find_without_an_address_answers_none(email: object) -> None:
    assert hunter_on(Recorder(found(email))).find_email(QUERY) == FinderAnswer(None)


def test_a_404_is_nobody_found_and_not_billed() -> None:
    meter = InMemoryUsageMeter()
    response = httpx2.Response(404, json=errors_body(404))

    answer = hunter_on(Recorder(response), meter=meter).find_email(QUERY)

    assert answer == FinderAnswer(None)
    assert meter.calls == [ApiCall(PROVIDER, FIND, ok=True, count=0)]


def test_a_451_is_a_claimed_address_and_not_billed() -> None:
    meter = InMemoryUsageMeter()
    body = {"errors": [{"id": "claimed_email", "code": 451, "details": f"{ADDRESS} {LEAK}"}]}

    answer = hunter_on(Recorder(httpx2.Response(451, json=body)), meter=meter).find_email(QUERY)

    assert answer == FinderAnswer(None, claimed=True)
    assert meter.calls == [ApiCall(PROVIDER, FIND, ok=True, count=0)]


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text=f"<html>{LEAK}</html>"),
        httpx2.Response(200, content=b"\xff\xfe"),
        httpx2.Response(204),
        httpx2.Response(200, json=[ADDRESS]),
        httpx2.Response(200, json={"data": ADDRESS}),
        httpx2.Response(200, json={"data": {"first_name": FIRST, "last_name": LAST}}),
        httpx2.Response(200, json={"data": {"email": 42}}),
        httpx2.Response(200, json={"data": {"email": [ADDRESS]}}),
        httpx2.Response(200, json={"data": {"email": {"value": ADDRESS}}}),
        httpx2.Response(200, json={"data": {"email": True}}),
    ],
)
def test_a_find_2xx_of_another_shape_is_a_bad_answer(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        hunter_on(Recorder(response), meter=meter).find_email(QUERY)

    error = raised.value
    assert (error.retryable, error.code) == (False, HUNTER_BAD_ANSWER)
    assert_clean(error)
    assert meter.calls == [ApiCall(PROVIDER, FIND, ok=False, error_code=HUNTER_BAD_ANSWER, count=1)]


# Statuses (both calls) --------------------------------------------------------------------------


# Email Verifier ---------------------------------------------------------------------------------


def test_a_verification_gets_the_address_with_the_key_in_the_header() -> None:
    recorder = Recorder(verified())

    answer = hunter_on(recorder).verify_email(ADDRESS)

    assert answer == VerifierAnswer("valid")
    (request,) = recorder.requests
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == "https://api.hunter.io/v2/email-verifier"
    assert dict(request.url.params) == {"email": ADDRESS}
    assert request.headers["x-api-key"] == KEY
    assert KEY not in str(request.url)


@pytest.mark.parametrize(
    "status", ["valid", "invalid", "accept_all", "webmail", "disposable", "unknown", "Odd Thing"]
)
def test_a_verification_passes_hunters_status_on_as_it_is(status: str) -> None:
    assert hunter_on(Recorder(verified(status))).verify_email(ADDRESS) == VerifierAnswer(status)


def test_a_222_is_unknown_and_billed() -> None:
    meter = InMemoryUsageMeter()
    response = httpx2.Response(222, json={"errors": [{"details": LEAK}]})

    answer = hunter_on(Recorder(response), meter=meter).verify_email(ADDRESS)

    assert answer == VerifierAnswer("unknown")
    assert meter.calls == [ApiCall(PROVIDER, VERIFY, ok=True, error_code=None, count=1)]
    assert meter.calls[0].units == 1


def test_a_202_is_retryable_and_not_billed() -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        hunter_on(Recorder(httpx2.Response(202, json={"data": {}})), meter=meter).verify_email(
            ADDRESS
        )

    error = raised.value
    assert (error.retryable, error.code) == (True, HUNTER_UNAVAILABLE)
    assert_clean(error)
    assert meter.calls == [
        ApiCall(PROVIDER, VERIFY, ok=False, error_code=HUNTER_UNAVAILABLE, count=0)
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text=f"not json {LEAK}"),
        httpx2.Response(200, json=[ADDRESS]),
        httpx2.Response(200, json={"data": None}),
        httpx2.Response(200, json={"data": {"result": "deliverable"}}),
        verified(None),
        verified(""),
        verified("  "),
        verified(7),
        verified([ADDRESS]),
    ],
)
def test_a_verification_2xx_of_another_shape_is_a_bad_answer(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        hunter_on(Recorder(response), meter=meter).verify_email(ADDRESS)

    error = raised.value
    assert (error.retryable, error.code) == (False, HUNTER_BAD_ANSWER)
    assert_clean(error)
    assert meter.calls == [
        ApiCall(PROVIDER, VERIFY, ok=False, error_code=HUNTER_BAD_ANSWER, count=1)
    ]


def test_a_verification_stores_one_unit() -> None:
    meter = InMemoryUsageMeter()

    hunter_on(Recorder(verified()), meter=meter).verify_email(ADDRESS)

    assert meter.calls == [ApiCall(PROVIDER, VERIFY, ok=True, error_code=None, count=1)]
    assert meter.snapshot(meter.clock.now()).units("hunter") == 1


def domain_search(hunter: HunterFinder) -> object:
    return hunter.domain_search(DOMAIN)


def find(hunter: HunterFinder) -> object:
    return hunter.find_email(QUERY)


def verify(hunter: HunterFinder) -> object:
    return hunter.verify_email(ADDRESS)


type Call = Callable[[HunterFinder], object]
CALLS: list[Call] = [domain_search, find, verify]


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("status", [403, 429, 500, 502, 503, 504, 599])
def test_a_rate_limit_or_5xx_is_retryable(call: Call, status: int) -> None:
    response = httpx2.Response(status, json=errors_body(status))

    with pytest.raises(AdapterError) as raised:
        call(hunter_on(Recorder(response)))

    error = raised.value
    assert (error.retryable, error.code) == (True, HUNTER_UNAVAILABLE)
    assert error.message == f"Hunter answered with HTTP {status}"
    assert_clean(error)


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("status", [400, 401, 402, 405, 409, 422, 301, 302, 307, 100])
def test_any_other_status_is_refused_naming_only_the_status(call: Call, status: int) -> None:
    response = httpx2.Response(status, json=errors_body(status))

    with pytest.raises(AdapterError) as raised:
        call(hunter_on(Recorder(response)))

    error = raised.value
    assert (error.retryable, error.code) == (False, HUNTER_REFUSED)
    assert error.message == f"Hunter refused the request with HTTP {status}"
    assert_clean(error)


@pytest.mark.parametrize("call", [domain_search, verify])
@pytest.mark.parametrize("status", [404, 451])
def test_a_domain_search_or_verification_404_or_451_is_refused(call: Call, status: int) -> None:
    with pytest.raises(AdapterError) as raised:
        call(hunter_on(Recorder(httpx2.Response(status, json=errors_body(status)))))

    assert (raised.value.retryable, raised.value.code) == (False, HUNTER_REFUSED)
    assert_clean(raised.value)


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(f"failed for {DOMAIN} {FIRST} {LAST} {KEY}"),
        httpx2.ConnectTimeout(f"timed out {KEY}"),
        httpx2.ReadTimeout(f"read timed out {KEY} {ADDRESS}"),
        httpx2.ReadError(f"connection reset {KEY}"),
        httpx2.RemoteProtocolError(f"server hung up {KEY}"),
        httpx2.PoolTimeout(f"pool {KEY}"),
        httpx2.UnsupportedProtocol(f"protocol {KEY}"),
        OSError(f"socket {KEY}"),
        RuntimeError(f"closed client {KEY}"),
        ValueError(f"weird {KEY}"),
    ],
)
def test_a_failed_request_is_retryable(call: Call, failure: Exception) -> None:
    with pytest.raises(AdapterError) as raised:
        call(hunter_on(Recorder(failure)))

    error = raised.value
    assert (error.retryable, error.code) == (True, HUNTER_UNAVAILABLE)
    assert_clean(error)


# The key ----------------------------------------------------------------------------------------


def test_the_key_is_read_on_each_call() -> None:
    secrets = secrets_with()
    recorder = Recorder(found())
    hunter = hunter_on(recorder, secrets)

    hunter.find_email(QUERY)
    secrets.write(SECRET_NAME, OTHER_KEY)
    hunter.find_email(QUERY)

    assert [request.headers["x-api-key"] for request in recorder.requests] == [KEY, OTHER_KEY]
    assert secrets.calls.count(("read", SECRET_NAME)) == 2


@pytest.mark.parametrize("call", CALLS)
def test_a_missing_key_is_not_set_and_nothing_is_requested_or_recorded(call: Call) -> None:
    recorder = Recorder(found())
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        call(hunter_on(recorder, secrets_with(None), meter))

    assert (raised.value.retryable, raised.value.code) == (False, CREDENTIAL_NOT_SET)
    assert recorder.requests == []
    assert meter.records == []


@pytest.mark.parametrize("call", CALLS)
def test_an_unreadable_credential_manager_propagates_and_nothing_is_requested(
    call: Call,
) -> None:
    secrets = secrets_with()
    secrets.failing.add("read")
    recorder = Recorder(found())
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        call(hunter_on(recorder, secrets, meter))

    assert raised.value.code == KEYRING_FAILED
    assert recorder.requests == []
    assert meter.records == []


# Metering ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "operation", "response", "ok", "code", "count"),
    [
        (domain_search, DOMAIN_SEARCH, searched(), True, None, 1),
        (domain_search, DOMAIN_SEARCH, searched(None, []), True, None, 1),
        (domain_search, DOMAIN_SEARCH, httpx2.Response(403), False, HUNTER_UNAVAILABLE, 0),
        (domain_search, DOMAIN_SEARCH, httpx2.Response(401), False, HUNTER_REFUSED, 0),
        (domain_search, DOMAIN_SEARCH, httpx2.ConnectError("x"), False, HUNTER_UNAVAILABLE, 0),
        (find, FIND, found(), True, None, 1),
        (find, FIND, found(None), True, None, 1),
        (find, FIND, httpx2.Response(404), True, None, 0),
        (find, FIND, httpx2.Response(451), True, None, 0),
        (find, FIND, httpx2.Response(429), False, HUNTER_UNAVAILABLE, 0),
        (find, FIND, httpx2.Response(500), False, HUNTER_UNAVAILABLE, 0),
        (find, FIND, httpx2.Response(400), False, HUNTER_REFUSED, 0),
        (find, FIND, httpx2.ReadTimeout("x"), False, HUNTER_UNAVAILABLE, 0),
        (find, FIND, httpx2.Response(200, text="nope"), False, HUNTER_BAD_ANSWER, 1),
    ],
    ids=[
        "search_200",
        "search_200_empty",
        "search_403",
        "search_401",
        "search_connect_error",
        "find_200",
        "find_200_none",
        "find_404",
        "find_451",
        "find_429",
        "find_500",
        "find_400",
        "find_read_timeout",
        "find_bad_answer",
    ],
)
def test_each_request_records_one_call_counting_only_a_2xx(
    call: Call,
    operation: str,
    response: httpx2.Response | BaseException,
    ok: bool,
    code: str | None,
    count: int,
) -> None:
    meter = InMemoryUsageMeter()
    recorder = Recorder(response)

    try:
        call(hunter_on(recorder, meter=meter))
    except AdapterError as error:
        assert error.code == code

    assert len(recorder.requests) == 1
    assert meter.calls == [ApiCall("hunter", operation, ok=ok, error_code=code, count=count)]
    assert meter.calls[0].units == 2 * count  # Hunter's credits are stored doubled


@pytest.mark.parametrize(
    ("response", "ok", "code", "count"),
    [
        (verified(), True, None, 1),
        (httpx2.Response(222, json={}), True, None, 1),
        (httpx2.Response(202, json={"data": {}}), False, HUNTER_UNAVAILABLE, 0),
        (httpx2.Response(403), False, HUNTER_UNAVAILABLE, 0),
        (httpx2.Response(429), False, HUNTER_UNAVAILABLE, 0),
        (httpx2.Response(500), False, HUNTER_UNAVAILABLE, 0),
        (httpx2.Response(401), False, HUNTER_REFUSED, 0),
        (httpx2.Response(404), False, HUNTER_REFUSED, 0),
        (httpx2.ConnectError("x"), False, HUNTER_UNAVAILABLE, 0),
        (httpx2.Response(200, text="nope"), False, HUNTER_BAD_ANSWER, 1),
    ],
    ids=[
        "verify_200",
        "verify_222",
        "verify_202",
        "verify_403",
        "verify_429",
        "verify_500",
        "verify_401",
        "verify_404",
        "verify_connect_error",
        "verify_bad_answer",
    ],
)
def test_each_verification_records_one_call_counting_a_200_or_222(
    response: httpx2.Response | BaseException,
    ok: bool,
    code: str | None,
    count: int,
) -> None:
    meter = InMemoryUsageMeter()
    recorder = Recorder(response)

    try:
        verify(hunter_on(recorder, meter=meter))
    except AdapterError as error:
        assert error.code == code
        assert_clean(error)

    assert len(recorder.requests) == 1
    assert meter.calls == [ApiCall("hunter", VERIFY, ok=ok, error_code=code, count=count)]
    assert meter.calls[0].units == count  # a verification is one unit


def test_a_2xx_find_and_domain_search_each_store_two_units() -> None:
    meter = InMemoryUsageMeter()

    hunter_on(Recorder(found()), meter=meter).find_email(QUERY)
    hunter_on(Recorder(searched()), meter=meter).domain_search(DOMAIN)

    calls = [call for call in meter.calls if isinstance(call, ApiCall)]
    assert [(call.operation, call.units) for call in calls] == [("find", 2), ("domain_search", 2)]
    assert meter.snapshot(meter.clock.now()).units("hunter") == 4


class RecordingFirst(InMemoryUsageMeter):
    """A meter that notes whether the error had already been raised when it recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.during: list[BaseException | None] = []

    def record(self, call: ApiCall | AiCall) -> None:
        self.during.append(sys.exc_info()[1])
        super().record(call)


@pytest.mark.parametrize("call", CALLS)
def test_a_failed_request_is_recorded_before_its_error_is_raised(call: Call) -> None:
    meter = RecordingFirst()

    with pytest.raises(AdapterError) as raised:
        call(hunter_on(Recorder(httpx2.Response(500)), meter=meter))

    assert meter.during == [raised.value]  # recorded while the error was being handled


# The live client --------------------------------------------------------------------------------


@pytest.mark.parametrize("call", CALLS)
def test_the_live_client_is_built_on_the_first_request_and_refused_in_tests(call: Call) -> None:
    meter = InMemoryUsageMeter()
    hunter = HunterFinder(secrets_with(), meter=meter)  # builds no client

    with pytest.raises(LiveAdapterInTests):
        call(hunter)
    assert meter.records == []  # no request was attempted


def test_no_client_is_built_when_the_key_is_missing() -> None:
    hunter = HunterFinder(secrets_with(None), meter=InMemoryUsageMeter())

    with pytest.raises(AdapterError) as raised:
        hunter.find_email(QUERY)

    assert raised.value.code == CREDENTIAL_NOT_SET


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.hunter", "default_http_client")

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
    recorder = Recorder(found())
    built: list[httpx2.Client] = []

    def build() -> httpx2.Client:
        built.append(httpx2.Client(transport=httpx2.MockTransport(recorder)))
        return built[-1]

    monkeypatch.setattr(adapter, "default_http_client", build)
    hunter = HunterFinder(secrets_with(), meter=InMemoryUsageMeter())

    hunter.find_email(QUERY)
    hunter.domain_search(DOMAIN)

    assert len(built) == 1 and len(recorder.requests) == 2
