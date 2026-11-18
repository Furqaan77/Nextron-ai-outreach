"""``CompaniesHouseRegistry`` on ``httpx2.MockTransport`` (``nextron.adapters.companies_house``,
Story 2.12): the Basic header with the key read on every call, the search and lookup requests
and their parsing, a 404, the status-to-code table and bad bodies, one usage record per
attempted request, the self rate limit (600 per 5 minutes, never sleeping) and no key in any
error or ``repr``. Nothing here reaches Companies House."""

import base64
import json
import traceback

import httpx2
import pytest

import nextron.adapters.companies_house as adapter
from nextron.adapters.companies_house import CompaniesHouseRegistry
from nextron.domain.budget import ApiCall
from nextron.ports.errors import AdapterError
from nextron.ports.registry import RegistryCompany, RegistryOfficer, RegistryPort
from nextron.ports.secrets import CREDENTIAL_NOT_SET
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

KEY = "Kh6Tp1Zc8Wm4Rq2Nx7Lv-3f2a"
OTHER_KEY = "Zq9Lm2Xc8Vb4Nn7Tt1Rr-9e8d"
SECRET_NAME = "companies_house/api_key"
LEAK = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"  # text in a response body that must never reach an error
NAME = "Smith & Jones LLP"
NUMBER = "01234567"

PROFILE = {
    "company_number": NUMBER,
    "company_name": "SMITH AND JONES LLP",
    "type": "llp",
    "company_status": "active",
    "registered_office_address": {"postal_code": "EC1A 1BB", "locality": "London"},
}
SEARCH = {
    "items": [
        {
            "company_number": NUMBER,
            "title": "SMITH AND JONES LLP",
            "company_type": "llp",
            "company_status": "active",
            "address": {"postal_code": "EC1A 1BB"},
        },
        {
            "company_number": "sc123456",
            "title": "  SMITH  AND JONES (SCOTLAND) LIMITED ",
            "company_type": "Not A Code",
            "company_status": "Dissolved",
            "address": {"postal_code": 42},
        },
        {"company_number": "123", "title": "TOO SHORT A NUMBER LTD"},
        {"company_number": "07654321", "title": ""},
        {"company_number": "07654321"},
        "not an object",
    ],
    "total_results": 6,
}


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


class Clock:
    """An injected ``monotonic``."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def secrets_with(key: str | None = KEY) -> InMemorySecrets:
    secrets = InMemorySecrets()
    if key is not None:
        secrets.write(SECRET_NAME, key)
    return secrets


def registry_on(
    recorder: Recorder,
    secrets: InMemorySecrets | None = None,
    meter: InMemoryUsageMeter | None = None,
    monotonic: Clock | None = None,
) -> CompaniesHouseRegistry:
    return CompaniesHouseRegistry(
        secrets if secrets is not None else secrets_with(),
        meter=meter if meter is not None else InMemoryUsageMeter(),
        http=httpx2.Client(transport=httpx2.MockTransport(recorder)),
        monotonic=monotonic if monotonic is not None else Clock(),
    )


def basic(key: str) -> str:
    return "Basic " + base64.b64encode(f"{key}:".encode()).decode("ascii")


def answer(status: int, body: object = None) -> httpx2.Response:
    if body is None:
        return httpx2.Response(status, content=b"")
    return httpx2.Response(status, json=body)


def assert_clean(error: BaseException) -> None:
    """Neither the key, its Basic form, the name searched nor a response body is in the error,
    its ``repr`` or its formatted traceback."""
    shown = "\n".join(
        [str(error), repr(error), *traceback.format_exception(error), str(error.args)]
    )
    for planted in (KEY, basic(KEY), NAME, LEAK, "api.company-information.service.gov.uk"):
        assert planted not in shown
    assert error.__cause__ is None and error.__suppress_context__


def test_it_implements_the_registry_port() -> None:
    registry = CompaniesHouseRegistry(InMemorySecrets(), meter=InMemoryUsageMeter())

    assert isinstance(registry, RegistryPort)


# The requests -----------------------------------------------------------------------------------


def test_a_lookup_gets_the_company_with_the_basic_header_and_parses_its_profile() -> None:
    recorder = Recorder(answer(200, PROFILE))

    found = registry_on(recorder).company(NUMBER)

    assert found == RegistryCompany(NUMBER, "SMITH AND JONES LLP", "llp", "active", "EC1A 1BB")
    [request] = recorder.requests
    assert request.method == "GET"
    assert str(request.url) == f"https://api.company-information.service.gov.uk/company/{NUMBER}"
    assert request.headers["authorization"] == basic(KEY)
    assert request.headers["accept"] == "application/json"


def test_a_search_sends_the_name_and_the_page_size_and_keeps_the_valid_items_in_order() -> None:
    recorder = Recorder(answer(200, SEARCH))

    hits = registry_on(recorder).search_companies("  Smith &\n Jones   LLP ")

    assert hits == (
        RegistryCompany(NUMBER, "SMITH AND JONES LLP", "llp", "active", "EC1A 1BB"),
        RegistryCompany("SC123456", "SMITH AND JONES (SCOTLAND) LIMITED", None, None, None),
    )
    [request] = recorder.requests
    assert request.url.path == "/search/companies"
    assert dict(request.url.params) == {"q": NAME, "items_per_page": "20"}
    assert request.headers["authorization"] == basic(KEY)


def test_a_search_without_items_has_no_hits() -> None:
    assert registry_on(Recorder(answer(200, {"total_results": 0}))).search_companies(NAME) == ()
    assert registry_on(Recorder(answer(200, {"items": []}))).search_companies(NAME) == ()


def test_a_missing_company_is_none_and_metered_ok() -> None:
    meter = InMemoryUsageMeter()

    registry = registry_on(Recorder(answer(404, {"errors": [LEAK]})), meter=meter)

    assert registry.company(NUMBER) is None
    assert meter.calls == [ApiCall("companies_house", "get_company", ok=True)]


def test_a_long_postcode_or_an_unknown_type_reads_as_none() -> None:
    profile = {
        **PROFILE,
        "type": "Not-A-Code",
        "company_status": "x" * 40,
        "registered_office_address": {"postal_code": "P" * 17},
    }

    found = registry_on(Recorder(answer(200, profile))).company(NUMBER)

    assert found == RegistryCompany(NUMBER, "SMITH AND JONES LLP", None, None, None)


def test_the_key_is_read_on_every_call() -> None:
    secrets = secrets_with()
    recorder = Recorder(answer(200, PROFILE))
    registry = registry_on(recorder, secrets)

    registry.company(NUMBER)
    secrets.write(SECRET_NAME, OTHER_KEY)
    registry.company(NUMBER)

    assert [request.headers["authorization"] for request in recorder.requests] == [
        basic(KEY),
        basic(OTHER_KEY),
    ]


def test_a_missing_key_is_not_set_and_nothing_is_sent_or_metered() -> None:
    recorder = Recorder(answer(200, PROFILE))
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        registry_on(recorder, secrets_with(None), meter).company(NUMBER)

    assert (raised.value.code, raised.value.retryable) == (CREDENTIAL_NOT_SET, False)
    assert recorder.requests == [] and meter.records == []


@pytest.mark.parametrize("bad", ["1234567", "0123456789", "ab12345!", "", None])
def test_a_bad_number_or_name_is_refused_with_no_request(bad: str | None) -> None:
    recorder = Recorder(answer(200, PROFILE))
    meter = InMemoryUsageMeter()
    registry = registry_on(recorder, meter=meter)

    with pytest.raises(AdapterError) as number:
        registry.company(bad)  # type: ignore[arg-type]
    with pytest.raises(AdapterError) as name:
        registry.search_companies(" \n " if bad is None else "")

    assert number.value.code == name.value.code == "registry_refused"
    assert recorder.requests == [] and meter.records == []


# The outcomes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "registry_rate_limited", True),
        (500, "registry_unavailable", True),
        (502, "registry_unavailable", True),
        (503, "registry_unavailable", True),
        (401, "registry_unauthorized", False),
        (403, "registry_unauthorized", False),
        (400, "registry_refused", False),
        (301, "registry_refused", False),
        (409, "registry_refused", False),
    ],
)
def test_each_status_maps_to_its_code(status: int, code: str, retryable: bool) -> None:
    for call in ("company", "search"):
        meter = InMemoryUsageMeter()
        registry = registry_on(Recorder(answer(status, {"error": LEAK})), meter=meter)

        with pytest.raises(AdapterError) as raised:
            registry.company(NUMBER) if call == "company" else registry.search_companies(NAME)

        assert (raised.value.code, raised.value.retryable) == (code, retryable)
        assert_clean(raised.value)
        operation = "get_company" if call == "company" else "search_companies"
        assert meter.calls == [ApiCall("companies_house", operation, ok=False, error_code=code)]


def test_a_search_404_is_refused() -> None:
    with pytest.raises(AdapterError) as raised:
        registry_on(Recorder(answer(404, {}))).search_companies(NAME)

    assert raised.value.code == "registry_refused"


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, content=b"\x00<html>" + LEAK.encode()),
        httpx2.Response(200, json=[LEAK]),
        httpx2.Response(200, json=LEAK),
        httpx2.Response(200, content=b'{"items": ' + b'"' + b"x" * 1_000_001 + b'"}'),
        httpx2.Response(200, json={"items": LEAK}),
    ],
    ids=["not json", "a list", "a string", "over 1 MB", "items not a list"],
)
def test_a_bad_body_is_a_bad_response(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        registry_on(Recorder(response), meter=meter).search_companies(NAME)

    assert (raised.value.code, raised.value.retryable) == ("registry_bad_response", False)
    assert_clean(raised.value)
    code = "registry_bad_response"
    assert meter.calls == [
        ApiCall("companies_house", "search_companies", ok=False, error_code=code),
    ]


@pytest.mark.parametrize(
    "profile",
    [
        {**PROFILE, "company_number": "123"},
        {**PROFILE, "company_name": ""},
        {key: value for key, value in PROFILE.items() if key != "company_name"},
    ],
)
def test_a_profile_without_a_valid_number_and_name_is_a_bad_response(
    profile: dict[str, object],
) -> None:
    with pytest.raises(AdapterError) as raised:
        registry_on(Recorder(answer(200, profile))).company(NUMBER)

    assert raised.value.code == "registry_bad_response"


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(f"failed {LEAK}"),
        httpx2.ReadTimeout(f"failed {LEAK}"),
        httpx2.RemoteProtocolError(f"failed {LEAK}"),
        OSError(LEAK),
    ],
    ids=lambda error: type(error).__name__,
)
def test_a_transport_failure_is_retryable_unavailable(failure: BaseException) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        registry_on(Recorder(failure), meter=meter).company(NUMBER)

    assert (raised.value.code, raised.value.retryable) == ("registry_unavailable", True)
    assert_clean(raised.value)
    assert meter.calls == [
        ApiCall("companies_house", "get_company", ok=False, error_code="registry_unavailable")
    ]


def test_each_call_records_exactly_one_metered_call() -> None:
    meter = InMemoryUsageMeter()
    recorder = Recorder(answer(200, PROFILE))
    registry = registry_on(recorder, meter=meter)

    registry.company(NUMBER)
    recorder.response = answer(200, SEARCH)
    registry.search_companies(NAME)

    assert meter.calls == [
        ApiCall("companies_house", "get_company", ok=True),
        ApiCall("companies_house", "search_companies", ok=True),
    ]
    assert [call.units for call in meter.calls] == [1, 1]


# The self rate limit ----------------------------------------------------------------------------


def test_the_601st_call_in_300_seconds_is_refused_until_the_window_slides() -> None:
    clock = Clock()
    meter = InMemoryUsageMeter()
    recorder = Recorder(answer(200, PROFILE))
    registry = registry_on(recorder, meter=meter, monotonic=clock)
    for index in range(600):
        clock.now = 1000.0 + index * 0.25  # 600 calls over 150 s
        registry.company(NUMBER)

    clock.now = 1299.0
    with pytest.raises(AdapterError) as raised:
        registry.search_companies(NAME)  # the window counts every operation

    assert (raised.value.code, raised.value.retryable) == ("registry_rate_limited", True)
    assert (len(recorder.requests), len(meter.calls)) == (600, 600)  # no request, no record
    clock.now = 1300.0  # the first call is 300 s old: it has left the window
    recorder.response = answer(200, SEARCH)
    registry.search_companies(NAME)
    assert (len(recorder.requests), len(meter.calls)) == (601, 601)
    with pytest.raises(AdapterError):
        registry.company(NUMBER)  # the second call is still inside it
    clock.now = 1300.25
    recorder.response = answer(200, PROFILE)
    assert registry.company(NUMBER) is not None


def test_a_refused_key_read_takes_no_slot() -> None:
    clock = Clock()
    secrets = secrets_with(None)
    registry = registry_on(Recorder(answer(200, PROFILE)), secrets, monotonic=clock)
    for _ in range(700):
        with pytest.raises(AdapterError):
            registry.company(NUMBER)
    secrets.write(SECRET_NAME, KEY)

    assert registry.company(NUMBER) is not None


# Secrets and the live client --------------------------------------------------------------------


def test_the_key_is_never_in_a_repr_or_an_error() -> None:
    registry = registry_on(Recorder(answer(401, {"error": LEAK, "key": KEY})))

    with pytest.raises(AdapterError) as raised:
        registry.company(NUMBER)

    assert KEY not in repr(registry) and basic(KEY) not in repr(registry)
    assert_clean(raised.value)
    assert KEY not in json.dumps(vars(raised.value), default=str)


def test_the_live_client_is_built_on_the_first_call_and_refused_in_tests() -> None:
    meter = InMemoryUsageMeter()
    registry = CompaniesHouseRegistry(secrets_with(), meter=meter)  # builds no client

    with pytest.raises(LiveAdapterInTests):
        registry.company(NUMBER)

    assert meter.records == []


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.companies_house", "default_http_client")

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
    recorder = Recorder(answer(200, PROFILE))
    built: list[httpx2.Client] = []

    def build() -> httpx2.Client:
        built.append(httpx2.Client(transport=httpx2.MockTransport(recorder)))
        return built[-1]

    monkeypatch.setattr(adapter, "default_http_client", build)
    registry = CompaniesHouseRegistry(secrets_with(), meter=InMemoryUsageMeter())

    registry.company(NUMBER)
    registry.company(NUMBER)

    assert len(built) == 1 and len(recorder.requests) == 2



# Story 8.4: the advanced search and the officers ------------------------------------------------

ADVANCED = {
    "items": [
        {
            "company_number": NUMBER,
            "company_name": "SMITH AND JONES SOLICITORS LIMITED",
            "company_type": "ltd",
            "company_status": "active",
            "registered_office_address": {"postal_code": "LS1 4AP"},
            "sic_codes": ["69102", "69102", "6910", 69109, "70229"],
        },
        {"company_number": "bad", "company_name": "NOT A NUMBER LTD"},
        "not an object",
    ],
    "hits": 2,
}
OFFICERS = {
    "items": [
        {
            "name": "SMITH,  Jane   Mary",
            "officer_role": "director",
            "occupation": "  Managing   Partner ",
        },
        {
            "name": "JONES, Ann",
            "officer_role": "llp-designated-member",
            "occupation": "",
            "resigned_on": "2020-01-31",
        },
        {"name": "ACME HOLDINGS LIMITED", "officer_role": "corporate-director"},
        {"name": "", "officer_role": "director"},
        {"name": "BROWN, Tom", "officer_role": "Not A Role"},
        {"name": "GREEN, Amy", "officer_role": "director", "occupation": "x" * 201},
        "not an object",
    ]
}


def test_search_sends_the_advanced_search_params_and_reads_the_hits() -> None:
    recorder = Recorder(answer(200, ADVANCED))
    meter = InMemoryUsageMeter()

    hits = registry_on(recorder, meter=meter).search(" Smith   Jones ", ("69102", "69109"), 2)

    [request] = recorder.requests
    assert request.method == "GET"
    assert request.url.path == "/advanced-search/companies"
    assert dict(request.url.params) == {
        "company_status": "active",
        "company_name_includes": "Smith Jones",
        "sic_codes": "69102,69109",
        "size": "20",
        "start_index": "40",
    }
    assert request.headers["authorization"] == basic(KEY)
    assert hits == (
        RegistryCompany(
            NUMBER,
            "SMITH AND JONES SOLICITORS LIMITED",
            "ltd",
            "active",
            "LS1 4AP",
            None,
            ("69102", "70229"),
        ),
    )
    assert meter.calls == [ApiCall("companies_house", "search_registry", ok=True)]


def test_a_sic_only_search_leaves_out_the_keyword() -> None:
    recorder = Recorder(answer(200, {"items": []}))

    assert registry_on(recorder).search(None, ("46190",), 0) == ()

    [request] = recorder.requests
    assert dict(request.url.params) == {
        "company_status": "active",
        "sic_codes": "46190",
        "size": "20",
        "start_index": "0",
    }


def test_a_search_answered_404_has_no_hits() -> None:
    meter = InMemoryUsageMeter()

    assert registry_on(Recorder(answer(404)), meter=meter).search(None, ("46190",), 0) == ()
    assert meter.calls == [ApiCall("companies_house", "search_registry", ok=True)]


def test_a_search_answered_429_is_retryable_rate_limited() -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        registry_on(Recorder(answer(429)), meter=meter).search("law", ("69102",), 0)

    assert (raised.value.retryable, raised.value.code) == (True, "registry_rate_limited")
    assert meter.calls == [
        ApiCall(
            "companies_house", "search_registry", ok=False, error_code="registry_rate_limited"
        )
    ]


@pytest.mark.parametrize(
    ("keyword", "codes", "page"),
    [
        (None, (), 0),
        ("  ", ("69102",), 0),
        ("law", ("6910",), 0),
        ("law", ["69102"], 0),
        ("law", ("69102",), -1),
        ("law", ("69102",), True),
    ],
)
def test_a_search_without_a_keyword_or_valid_codes_is_refused_with_no_request(
    keyword: object, codes: object, page: object
) -> None:
    recorder = Recorder(answer(200, ADVANCED))
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        registry_on(recorder, meter=meter).search(keyword, codes, page)  # type: ignore[arg-type]

    assert (raised.value.retryable, raised.value.code) == (False, "registry_refused")
    assert recorder.requests == [] and meter.calls == []


def test_officers_reads_each_valid_officer() -> None:
    recorder = Recorder(answer(200, OFFICERS))
    meter = InMemoryUsageMeter()

    found = registry_on(recorder, meter=meter).officers(NUMBER)

    [request] = recorder.requests
    assert request.url.path == f"/company/{NUMBER}/officers"
    assert dict(request.url.params) == {"items_per_page": "100"}
    assert found == (
        RegistryOfficer("SMITH, Jane Mary", "director", "Managing Partner", False),
        RegistryOfficer("JONES, Ann", "llp-designated-member", None, True),
        RegistryOfficer("ACME HOLDINGS LIMITED", "corporate-director", None, False),
        RegistryOfficer("GREEN, Amy", "director", None, False),
    )
    assert "Jane" not in repr(found) and "Managing" not in repr(found)
    assert meter.calls == [ApiCall("companies_house", "list_officers", ok=True)]


def test_officers_of_a_company_answered_404_are_none() -> None:
    assert registry_on(Recorder(answer(404))).officers(NUMBER) == ()


def test_officers_answered_429_or_503_are_retryable() -> None:
    for status, code in ((429, "registry_rate_limited"), (503, "registry_unavailable")):
        with pytest.raises(AdapterError) as raised:
            registry_on(Recorder(answer(status))).officers(NUMBER)
        assert (raised.value.retryable, raised.value.code) == (True, code)


def test_officers_of_a_bad_number_are_refused_with_no_request() -> None:
    recorder = Recorder(answer(200, OFFICERS))

    with pytest.raises(AdapterError) as raised:
        registry_on(recorder).officers("123")

    assert raised.value.code == "registry_refused"
    assert recorder.requests == []


def test_officers_whose_items_are_not_a_list_are_a_bad_response() -> None:
    with pytest.raises(AdapterError) as raised:
        registry_on(Recorder(answer(200, {"items": {}}))).officers(NUMBER)

    assert raised.value.code == "registry_bad_response"


def test_a_profile_reads_the_accounts_category_and_sic_codes() -> None:
    profile = {
        **PROFILE,
        "accounts": {"last_accounts": {"type": "dormant", "made_up_to": "2025-03-31"}},
        "sic_codes": ["69102"],
    }

    found = registry_on(Recorder(answer(200, profile))).company(NUMBER)

    assert found is not None
    assert (found.accounts_category, found.sic_codes) == ("dormant", ("69102",))


@pytest.mark.parametrize(
    "accounts",
    [None, "full", {"last_accounts": None}, {"last_accounts": {"type": "Not A Code"}}],
)
def test_a_profile_without_a_valid_accounts_category_reads_none(accounts: object) -> None:
    found = registry_on(Recorder(answer(200, {**PROFILE, "accounts": accounts}))).company(NUMBER)

    assert found is not None
    assert (found.accounts_category, found.sic_codes) == (None, ())


def test_the_search_and_officers_share_the_rate_window_with_the_lookups() -> None:
    clock = Clock()
    recorder = Recorder(answer(200, {"items": []}))
    registry = registry_on(recorder, monotonic=clock)
    for _ in range(adapter.RATE_LIMIT - 2):
        registry.search(None, ("69102",), 0)
    registry.officers(NUMBER)
    registry.search_companies(NAME)

    with pytest.raises(AdapterError) as raised:
        registry.officers(NUMBER)

    assert raised.value.code == "registry_rate_limited"
    assert len(recorder.requests) == adapter.RATE_LIMIT


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"accounts_category": "Dormant"}, ValueError),
        ({"accounts_category": 1}, TypeError),
        ({"sic_codes": ["69102"]}, TypeError),
        ({"sic_codes": ("6910",)}, ValueError),
        ({"sic_codes": ("69102", "69102")}, ValueError),
    ],
)
def test_a_registry_company_checks_its_accounts_and_codes(
    changes: dict[str, object], error: type[Exception]
) -> None:
    values: dict[str, object] = {
        "number": NUMBER,
        "name": "X LTD",
        "company_type": "ltd",
        "company_status": "active",
        "office_postcode": None,
    }
    with pytest.raises(error):
        RegistryCompany(**{**values, **changes})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("values", "error"),
    [
        (("", "director", None, False), ValueError),
        ((" SMITH, Jane", "director", None, False), ValueError),
        (("SMITH, Jane", "Director", None, False), ValueError),
        (("SMITH, Jane", "director", "", False), ValueError),
        (("SMITH, Jane", "director", None, "no"), TypeError),
        ((1, "director", None, False), TypeError),
    ],
)
def test_a_registry_officer_checks_itself(values: tuple[object, ...], error: type[Exception]) -> None:
    with pytest.raises(error):
        RegistryOfficer(*values)  # type: ignore[arg-type]


def test_null_items_are_no_hits_for_every_list_answer() -> None:
    meter = InMemoryUsageMeter()

    def on_null() -> CompaniesHouseRegistry:
        return registry_on(Recorder(answer(200, {"items": None})), meter=meter)

    assert on_null().search_companies(NAME) == ()
    assert on_null().search(None, ("46190",), 0) == ()
    assert on_null().officers(NUMBER) == ()
    assert [call.ok for call in meter.calls] == [True, True, True]
