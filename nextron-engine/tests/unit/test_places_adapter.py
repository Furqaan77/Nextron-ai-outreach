"""``GooglePlaces`` on ``httpx2.MockTransport`` and ``OfflinePlaces`` (``nextron.adapters.places``,
Story 8.2): the Text Search (New) request (method, URL, headers, JSON body), the exact field
mask and its SKU, the key read on every call and sent only in ``X-Goog-Api-Key``, the parsing of
a page, the status-to-code table and bad bodies, one usage record per attempted request, no key
or body in any error or ``repr``, and an offline port that makes no request. Nothing here
reaches Google."""

import json
import traceback
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

import nextron.adapters.places as adapter
from nextron.adapters.places import GooglePlaces, OfflinePlaces
from nextron.domain.budget import ApiCall
from nextron.domain.places import (
    FIELD_MASK,
    PLACE_COORDS_TTL,
    coords_cutoff,
    places_sku,
    quota_for_sku,
)
from nextron.ports.errors import AdapterError
from nextron.ports.places import PlaceCandidate, PlaceSearchPage, PlacesPort
from nextron.ports.secrets import CREDENTIAL_NOT_SET
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

KEY = "Gp4Yt8Rm2Kx6Wq1Zv9Ld-7c1b"
OTHER_KEY = "Zx3Kq7Vm1Lp5Nt9Rw2Hs-2b4e"
SECRET_NAME = "google_places/api_key"
LEAK = "Bq7Nd3Wt9Lx2Ms6Rk4Pz"  # text in a response body that must never reach an error
QUERY = "accounting firm"
LOCATION = "Columbus, OH"
TOKEN = "Nt3Pq7Xs1Wk5-next-page"
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
EXPECTED_MASK = "places.id,places.websiteUri,places.displayName,places.location,nextPageToken"
FORBIDDEN_FIELDS = (
    "formattedAddress",
    "nationalPhoneNumber",
    "internationalPhoneNumber",
    "reviews",
    "rating",
)

PAGE = {
    "places": [
        {
            "id": "ChIJ-first",
            "websiteUri": "https://smith-cpa.example.com/",
            "displayName": {"text": "Smith CPA", "languageCode": "en"},
            "location": {"latitude": 39.9612, "longitude": -82.9988},
        },
        {"id": "ChIJ-second", "location": {"latitude": 40, "longitude": -83}},
        {"id": "", "websiteUri": "https://no-id.example.com/"},
        {"websiteUri": "https://missing-id.example.com/"},
        {"id": "x" * 513},
        {"id": 42},
        "not an object",
    ],
    "nextPageToken": TOKEN,
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


def secrets_with(key: str | None = KEY) -> InMemorySecrets:
    secrets = InMemorySecrets()
    if key is not None:
        secrets.write(SECRET_NAME, key)
    return secrets


def places_on(
    recorder: Recorder,
    secrets: InMemorySecrets | None = None,
    meter: InMemoryUsageMeter | None = None,
) -> GooglePlaces:
    return GooglePlaces(
        secrets if secrets is not None else secrets_with(),
        meter=meter if meter is not None else InMemoryUsageMeter(),
        http=httpx2.Client(transport=httpx2.MockTransport(recorder)),
    )


def answer(status: int, body: object = None) -> httpx2.Response:
    if body is None:
        return httpx2.Response(status, content=b"")
    return httpx2.Response(status, json=body)


def assert_clean(error: BaseException) -> None:
    """Neither the key, the query, the location, the token nor a response body is in the
    error, its ``repr`` or its formatted traceback."""
    shown = "\n".join(
        [str(error), repr(error), *traceback.format_exception(error), str(error.args)]
    )
    for planted in (KEY, QUERY, LOCATION, TOKEN, LEAK, "places.googleapis.com"):
        assert planted not in shown
    assert error.__cause__ is None and error.__suppress_context__


def test_both_adapters_implement_the_places_port() -> None:
    assert isinstance(GooglePlaces(InMemorySecrets(), meter=InMemoryUsageMeter()), PlacesPort)
    assert isinstance(OfflinePlaces(), PlacesPort)


# The field mask and the SKU ---------------------------------------------------------------------


def mask_problems(mask: str) -> list[str]:
    """What is wrong with a field mask header: each forbidden field it names, and each field
    beyond the five allowed ones."""
    fields = mask.split(",")
    problems = [f"asks for {name}" for name in FORBIDDEN_FIELDS if name in mask]
    allowed = EXPECTED_MASK.split(",")
    problems += [f"extra field {field}" for field in fields if field not in allowed]
    if sorted(fields) != sorted(allowed) and not problems:
        problems.append("not the five allowed fields")
    return problems


def test_the_field_mask_is_exactly_the_five_allowed_fields() -> None:
    assert ",".join(FIELD_MASK) == EXPECTED_MASK
    assert adapter.FIELD_MASK_HEADER == EXPECTED_MASK
    assert mask_problems(adapter.FIELD_MASK_HEADER) == []


@pytest.mark.parametrize(
    "field",
    [
        *(f"places.{name}" for name in FORBIDDEN_FIELDS),
        "places.priceLevel",
        "places.types",
        "*",
    ],
)
def test_the_mask_check_fails_on_any_forbidden_or_extra_field(field: str) -> None:
    assert mask_problems(f"{EXPECTED_MASK},{field}") != []


def test_the_mask_check_fails_on_a_missing_field() -> None:
    assert mask_problems("places.id,places.websiteUri,places.displayName,nextPageToken") != []


@pytest.mark.parametrize(
    ("fields", "sku", "quota"),
    [
        (FIELD_MASK, "enterprise", "places_enterprise"),
        (("places.id", "places.displayName", "places.location"), "pro", "places_pro"),
        (("places.id",), "pro", "places_pro"),
        ((), "pro", "places_pro"),
        (["places.websiteUri"], "enterprise", "places_enterprise"),
    ],
)
def test_the_sku_follows_the_mask(fields: tuple[str, ...], sku: str, quota: str) -> None:
    assert places_sku(fields) == sku
    assert quota_for_sku(sku) == quota


def test_an_unknown_sku_has_no_quota() -> None:
    with pytest.raises(ValueError, match="Places SKU"):
        quota_for_sku("essentials")


def test_coordinates_live_30_days() -> None:
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    assert PLACE_COORDS_TTL == timedelta(days=30)
    assert coords_cutoff(now) == datetime(2026, 10, 19, 12, tzinfo=UTC)


# The request ------------------------------------------------------------------------------------


def test_a_search_posts_the_query_with_the_key_header_and_the_field_mask() -> None:
    recorder = Recorder(answer(200, PAGE))
    meter = InMemoryUsageMeter()

    places_on(recorder, meter=meter).text_search(QUERY, LOCATION)

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == SEARCH_URL
    assert request.url.query in (b"", "")
    assert request.headers["X-Goog-Api-Key"] == KEY
    assert request.headers["X-Goog-FieldMask"] == EXPECTED_MASK
    assert mask_problems(request.headers["X-Goog-FieldMask"]) == []
    assert request.headers["Content-Type"] == "application/json"
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {
        "textQuery": "accounting firm in Columbus, OH",
        "pageSize": 20,
    }
    # the key is only in its header
    others = {name: value for name, value in request.headers.items() if name != "x-goog-api-key"}
    assert KEY not in json.dumps(others)
    assert KEY not in str(request.url) and KEY.encode() not in request.content
    assert meter.calls == [ApiCall("places", "text_search", ok=True, sku="enterprise")]


def test_a_next_page_sends_its_token_and_whitespace_is_collapsed() -> None:
    recorder = Recorder(answer(200, {}))

    places_on(recorder).text_search("  accounting \n firm ", " Columbus,   OH ", TOKEN)

    [request] = recorder.requests
    assert json.loads(request.content) == {
        "textQuery": "accounting firm in Columbus, OH",
        "pageSize": 20,
        "pageToken": TOKEN,
    }


def test_the_key_is_read_on_every_call() -> None:
    recorder = Recorder(answer(200, {}))
    secrets = secrets_with(KEY)
    places = places_on(recorder, secrets=secrets)

    places.text_search(QUERY, LOCATION)
    secrets.write(SECRET_NAME, OTHER_KEY)
    places.text_search(QUERY, LOCATION)

    assert [request.headers["X-Goog-Api-Key"] for request in recorder.requests] == [
        KEY,
        OTHER_KEY,
    ]


def test_a_missing_key_is_not_set_and_nothing_is_sent_or_metered() -> None:
    recorder = Recorder(answer(200, PAGE))
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        places_on(recorder, secrets=secrets_with(None), meter=meter).text_search(QUERY, LOCATION)

    assert (raised.value.code, raised.value.retryable) == (CREDENTIAL_NOT_SET, False)
    assert recorder.requests == [] and meter.calls == []


@pytest.mark.parametrize(
    ("query", "location", "token"),
    [
        ("", LOCATION, None),
        ("   ", LOCATION, None),
        (QUERY, "", None),
        (QUERY, " \t", None),
        (None, LOCATION, None),
        (QUERY, 42, None),
        (QUERY, LOCATION, ""),
        (QUERY, LOCATION, 7),
        (QUERY, LOCATION, "   "),
        (QUERY, LOCATION, "\t\n"),
    ],
)
def test_a_blank_query_or_location_or_a_bad_token_is_refused_with_no_request(
    query: object, location: object, token: object
) -> None:
    recorder = Recorder(answer(200, PAGE))
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        places_on(recorder, meter=meter).text_search(
            query,  # type: ignore[arg-type]
            location,  # type: ignore[arg-type]
            token,  # type: ignore[arg-type]
        )

    assert (raised.value.code, raised.value.retryable) == ("places_refused", False)
    assert recorder.requests == [] and meter.calls == []
    assert_clean(raised.value)


# The answer -------------------------------------------------------------------------------------


def test_a_page_keeps_the_valid_places_in_order_with_the_next_token() -> None:
    page = places_on(Recorder(answer(200, PAGE))).text_search(QUERY, LOCATION)

    assert page == PlaceSearchPage(
        (
            PlaceCandidate(
                "ChIJ-first", "https://smith-cpa.example.com/", "Smith CPA", 39.9612, -82.9988
            ),
            PlaceCandidate("ChIJ-second", None, None, 40.0, -83.0),
        ),
        TOKEN,
    )
    assert isinstance(page.candidates[1].lat, float)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"places": []},
        {"places": [], "nextPageToken": ""},
        {"places": [], "nextPageToken": None},
        {"nextPageToken": 5},
    ],
)
def test_the_last_page_has_no_token(body: dict[str, object]) -> None:
    page = places_on(Recorder(answer(200, body))).text_search(QUERY, LOCATION)

    assert page == PlaceSearchPage((), None)


@pytest.mark.parametrize(
    "location",
    [
        {"latitude": 91, "longitude": 0},
        {"latitude": 0, "longitude": -180.5},
        {"latitude": True, "longitude": 1},
        {"latitude": 10**400, "longitude": 1},
        {"latitude": 1, "longitude": -(10**400)},
        {"latitude": "39.9", "longitude": "-82.9"},
        {"latitude": 39.9},
        {"longitude": -82.9},
        [39.9, -82.9],
        None,
    ],
)
def test_a_bad_location_reads_as_none(location: object) -> None:
    body = {"places": [{"id": "ChIJ-x", "location": location, "websiteUri": 5}]}

    [candidate] = places_on(Recorder(answer(200, body))).text_search(QUERY, LOCATION).candidates

    assert (candidate.place_id, candidate.lat, candidate.lng) == ("ChIJ-x", None, None)
    assert (candidate.website_uri, candidate.display_name) == (None, None)


def test_a_display_name_that_isnt_text_reads_as_none() -> None:
    body = {"places": [{"id": "a", "displayName": "Smith CPA"}, {"id": "b", "displayName": {}}]}

    page = places_on(Recorder(answer(200, body))).text_search(QUERY, LOCATION)

    assert [candidate.display_name for candidate in page.candidates] == [None, None]


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "places_rate_limited", True),
        (500, "places_unavailable", True),
        (503, "places_unavailable", True),
        (401, "places_unauthorized", False),
        (403, "places_unauthorized", False),
        (400, "places_refused", False),
        (404, "places_refused", False),
        (302, "places_refused", False),
        (301, "places_refused", False),
    ],
)
def test_each_status_maps_to_its_code(status: int, code: str, retryable: bool) -> None:
    meter = InMemoryUsageMeter()
    response = httpx2.Response(status, json={"error": {"message": LEAK, "key": KEY}})

    with pytest.raises(AdapterError) as raised:
        places_on(Recorder(response), meter=meter).text_search(QUERY, LOCATION)

    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    assert str(status) in str(raised.value)
    assert meter.calls == [
        ApiCall("places", "text_search", ok=False, error_code=code, sku="enterprise")
    ]
    assert_clean(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, content=b"x" * 2_000_001),
        httpx2.Response(200, content=b"not json " + LEAK.encode()),
        httpx2.Response(200, json=[LEAK]),
        httpx2.Response(200, json=LEAK),
        httpx2.Response(200, json={"places": {"id": LEAK}}),
        httpx2.Response(200, json={"places": LEAK}),
    ],
)
def test_a_bad_body_is_a_bad_response(response: httpx2.Response) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        places_on(Recorder(response), meter=meter).text_search(QUERY, LOCATION)

    assert (raised.value.code, raised.value.retryable) == ("places_bad_response", False)
    assert meter.calls == [
        ApiCall(
            "places", "text_search", ok=False, error_code="places_bad_response", sku="enterprise"
        )
    ]
    assert_clean(raised.value)


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(f"refused {KEY} {LEAK}"),
        httpx2.ReadTimeout(f"timed out {LEAK}"),
        OSError(LEAK),
        RuntimeError(KEY),
    ],
)
def test_a_transport_failure_is_retryable_unavailable(failure: BaseException) -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(AdapterError) as raised:
        places_on(Recorder(failure), meter=meter).text_search(QUERY, LOCATION)

    assert (raised.value.code, raised.value.retryable) == ("places_unavailable", True)
    assert meter.calls == [
        ApiCall(
            "places", "text_search", ok=False, error_code="places_unavailable", sku="enterprise"
        )
    ]
    assert_clean(raised.value)


def test_each_attempted_request_records_exactly_one_call() -> None:
    meter = InMemoryUsageMeter()
    places = places_on(Recorder(answer(200, PAGE)), meter=meter)

    places.text_search(QUERY, LOCATION)
    places.text_search(QUERY, LOCATION, TOKEN)

    assert meter.calls == [ApiCall("places", "text_search", ok=True, sku="enterprise")] * 2


# Nothing leaks ----------------------------------------------------------------------------------


def test_the_key_and_the_candidate_text_are_never_in_a_repr() -> None:
    places = places_on(Recorder(answer(200, PAGE)))
    page = places.text_search(QUERY, LOCATION)

    shown = repr(places) + repr(page) + repr(page.candidates)
    assert KEY not in shown
    assert "Smith CPA" not in shown and "smith-cpa.example.com" not in shown
    assert TOKEN not in shown
    assert "ChIJ-first" in shown


def test_a_candidate_validates_its_values() -> None:
    with pytest.raises(TypeError):
        PlaceCandidate(42, None, None, None, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="place_id"):
        PlaceCandidate("", None, None, None, None)
    with pytest.raises(ValueError, match="place_id"):
        PlaceCandidate("x" * 513, None, None, None, None)
    with pytest.raises(TypeError):
        PlaceCandidate("a", 5, None, None, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PlaceCandidate("a", None, None, 1, 2)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lat"):
        PlaceCandidate("a", None, None, 90.5, 0.0)
    with pytest.raises(ValueError, match="lng"):
        PlaceCandidate("a", None, None, 0.0, float("nan"))
    with pytest.raises(ValueError, match="both"):
        PlaceCandidate("a", None, None, 1.0, None)
    with pytest.raises(TypeError):
        PlaceSearchPage([], None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty"):
        PlaceSearchPage((), "")


# The client -------------------------------------------------------------------------------------


def test_the_live_client_is_built_on_the_first_call_and_refused_in_tests() -> None:
    meter = InMemoryUsageMeter()
    places = GooglePlaces(secrets_with(), meter=meter)  # builds no client

    with pytest.raises(LiveAdapterInTests):
        places.text_search(QUERY, LOCATION)

    assert meter.calls == []


def test_the_default_client_has_the_specified_timeouts_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_constructor(monkeypatch, "nextron.adapters.places", "default_http_client")

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
    recorder = Recorder(answer(200, {}))
    built: list[httpx2.Client] = []

    def build() -> httpx2.Client:
        built.append(httpx2.Client(transport=httpx2.MockTransport(recorder)))
        return built[-1]

    monkeypatch.setattr(adapter, "default_http_client", build)
    places = GooglePlaces(secrets_with(), meter=InMemoryUsageMeter())

    places.text_search(QUERY, LOCATION)
    places.text_search(QUERY, LOCATION)

    assert len(built) == 1 and len(recorder.requests) == 2


# The offline port -------------------------------------------------------------------------------


def test_the_offline_port_makes_no_request_and_meters_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> httpx2.Client:
        raise AssertionError("OfflinePlaces built an HTTP client")

    monkeypatch.setattr(adapter, "default_http_client", refuse)
    offline = OfflinePlaces()

    assert offline.text_search(QUERY, LOCATION) == PlaceSearchPage((), None)
    assert offline.text_search(QUERY, LOCATION, TOKEN) == PlaceSearchPage((), None)
    assert repr(offline) == "OfflinePlaces()"
