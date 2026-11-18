"""Story 8.2's ``PlaceSearches.search_page`` on a temporary database at head (FR-5, FR-49, AD-9,
AD-17, NFR-12) and the engine's choice of Places port (``main.build_places``): the per-call
budget check before every request (the Enterprise quota at 999 and 1,000 of 1,000, the Pro
quota of 0, the AI budget), the request outside any transaction, the logs (counts only), and
``GooglePlaces`` only in ``prod`` or with a stored key. No request reaches Google."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from nextron import main
from nextron.adapters.places import GooglePlaces, OfflinePlaces
from nextron.app.discovery.places import PlaceSearches
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory, in_transaction
from nextron.domain.budget import AiCall, ApiCall
from nextron.domain.settings_schema import Key
from nextron.ports.errors import AdapterError
from nextron.ports.places import PlaceCandidate, PlaceSearchPage
from tests.fakes.clock import FrozenClock
from tests.fakes.places import ScriptedPlaces
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits
from tests.fakes.usage_meter import InMemoryUsageMeter

T = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LOGGER = "nextron.app.discovery.places"
QUERY = "accounting firm"
LOCATION = "Columbus, OH"
TOKEN = "Nt3Pq7Xs1Wk5"
KEY = "Gp4Yt8Rm2Kx6Wq1Zv9Ld"
SECRET_NAME = "google_places/api_key"
CANDIDATE = PlaceCandidate(
    "ChIJ-first", "https://smith-cpa.example.com/", "Smith CPA", 39.9612, -82.9988
)
PAGE = PlaceSearchPage((CANDIDATE,), TOKEN)
BODY = {
    "places": [
        {
            "id": "ChIJ-first",
            "websiteUri": "https://smith-cpa.example.com/",
            "displayName": {"text": "Smith CPA"},
            "location": {"latitude": 39.9612, "longitude": -82.9988},
        }
    ],
    "nextPageToken": TOKEN,
}


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T)


@pytest.fixture
def meter(clock: FrozenClock) -> InMemoryUsageMeter:
    return InMemoryUsageMeter(clock)


def search(units: UnitOfWorkFactory, searches: PlaceSearches, *args: Any) -> Any:
    with units.open():
        return searches.search_page(*args)


def set_setting(units: UnitOfWorkFactory, clock: FrozenClock, key: str, value: object) -> None:
    with units.open():
        Setup(clock).set(key, value)


def google_on(meter: InMemoryUsageMeter, requests: list[httpx2.Request]) -> GooglePlaces:
    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=BODY)

    secrets = InMemorySecrets()
    secrets.write(SECRET_NAME, KEY)
    return GooglePlaces(
        secrets, meter=meter, http=httpx2.Client(transport=httpx2.MockTransport(answer))
    )


def fields(record: logging.LogRecord, *names: str) -> tuple[Any, ...]:
    """The record's ``extra`` fields named."""
    return tuple(getattr(record, name) for name in names)


def records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == message]


# The per-call check -----------------------------------------------------------------------------


def test_a_page_is_fetched_and_logged_by_count_only(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    meter: InMemoryUsageMeter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    places = ScriptedPlaces({(QUERY, LOCATION, None): PAGE, (QUERY, LOCATION, TOKEN): PAGE})
    searches = PlaceSearches(clock, meter, places)

    assert search(units, searches, QUERY, LOCATION) == PAGE
    assert search(units, searches, QUERY, LOCATION, TOKEN) == PAGE

    assert places.calls == [(QUERY, LOCATION, None), (QUERY, LOCATION, TOKEN)]
    assert searches.places is places
    fetched = records(caplog, "places_page_fetched")
    assert [fields(r, "count", "has_next") for r in fetched] == [(1, True), (1, True)]
    for record in caplog.records:
        shown = record.getMessage() + repr(vars(record))
        for planted in ("Smith CPA", "smith-cpa", QUERY, LOCATION, TOKEN, "ChIJ"):
            assert planted not in shown


def test_the_enterprise_quota_allows_the_1000th_request_and_refuses_the_next(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    meter: InMemoryUsageMeter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    meter.record(ApiCall("places", "text_search", ok=True, sku="enterprise", count=999))
    requests: list[httpx2.Request] = []
    searches = PlaceSearches(clock, meter, google_on(meter, requests))

    first = search(units, searches, QUERY, LOCATION)
    second = search(units, searches, QUERY, LOCATION, TOKEN)

    assert first is not None and [c.place_id for c in first.candidates] == ["ChIJ-first"]
    assert second is None
    assert len(requests) == 1
    assert meter.snapshot(T).units("places", "enterprise") == 1000
    [refused] = records(caplog, "places_search_refused")
    assert fields(refused, "quota", "reason") == ("places_enterprise", "quota")


def test_pro_usage_doesnt_count_against_the_enterprise_quota(
    units: UnitOfWorkFactory, clock: FrozenClock, meter: InMemoryUsageMeter
) -> None:
    meter.record(ApiCall("places", "text_search", ok=True, sku="pro", count=5_000))
    places = ScriptedPlaces({(QUERY, LOCATION, None): PAGE})

    assert search(units, PlaceSearches(clock, meter, places), QUERY, LOCATION) == PAGE


def test_a_mask_without_the_website_is_pro_and_its_default_quota_of_0_refuses(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    meter: InMemoryUsageMeter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    places = ScriptedPlaces()
    mask = ("places.id", "places.displayName", "places.location", "nextPageToken")
    searches = PlaceSearches(clock, meter, places, fields=mask)

    assert search(units, searches, QUERY, LOCATION) is None

    assert places.calls == []
    [refused] = records(caplog, "places_search_refused")
    assert fields(refused, "quota", "reason") == ("places_pro", "quota")


def test_a_pro_quota_raised_by_the_operator_lets_the_pro_mask_through(
    units: UnitOfWorkFactory, clock: FrozenClock, meter: InMemoryUsageMeter
) -> None:
    set_setting(units, clock, Key.QUOTA_PLACES_PRO, 10)
    places = ScriptedPlaces({(QUERY, LOCATION, None): PAGE})
    searches = PlaceSearches(clock, meter, places, fields=("places.id",))

    assert search(units, searches, QUERY, LOCATION) == PAGE


def test_an_enterprise_quota_of_0_refuses(
    units: UnitOfWorkFactory, clock: FrozenClock, meter: InMemoryUsageMeter
) -> None:
    set_setting(units, clock, Key.QUOTA_PLACES_ENTERPRISE, 0)
    places = ScriptedPlaces()

    assert search(units, PlaceSearches(clock, meter, places), QUERY, LOCATION) is None
    assert places.calls == []


def test_the_ai_budget_reached_refuses(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    meter: InMemoryUsageMeter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    meter.record(AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000))
    places = ScriptedPlaces()

    assert search(units, PlaceSearches(clock, meter, places), QUERY, LOCATION) is None

    assert places.calls == []
    [refused] = records(caplog, "places_search_refused")
    assert fields(refused, "quota", "reason") == ("places_enterprise", "ai_budget")


def test_the_request_runs_outside_any_transaction(
    units: UnitOfWorkFactory, clock: FrozenClock, meter: InMemoryUsageMeter
) -> None:
    seen: list[bool] = []

    def answer() -> PlaceSearchPage:
        seen.append(in_transaction())
        return PlaceSearchPage((), None)

    searches = PlaceSearches(clock, meter, ScriptedPlaces({(QUERY, LOCATION, None): answer}))

    assert search(units, searches, QUERY, LOCATION) == PlaceSearchPage((), None)
    assert seen == [False]


def test_an_adapter_error_propagates_after_the_check(
    units: UnitOfWorkFactory, clock: FrozenClock, meter: InMemoryUsageMeter
) -> None:
    error = AdapterError(True, "places_unavailable", "Google Places answered with HTTP 503")
    searches = PlaceSearches(clock, meter, ScriptedPlaces({(QUERY, LOCATION, None): error}))

    with pytest.raises(AdapterError) as raised:
        search(units, searches, QUERY, LOCATION)

    assert raised.value is error


# The engine's Places port -----------------------------------------------------------------------


class BrokenKeyring(InMemorySecrets):
    """Secrets whose every read fails, as a locked or missing keyring would."""

    def read(self, name: str) -> str | None:
        raise AdapterError(False, "keyring_failed", "the keyring could not be read")


def with_key() -> InMemorySecrets:
    secrets = InMemorySecrets()
    secrets.write(SECRET_NAME, KEY)
    return secrets


@pytest.mark.parametrize(
    ("env", "secrets", "expected"),
    [
        ("prod", InMemorySecrets(), GooglePlaces),
        ("prod", with_key(), GooglePlaces),
        ("prod", BrokenKeyring(), GooglePlaces),
        ("dev", InMemorySecrets(), OfflinePlaces),
        ("dev", with_key(), GooglePlaces),
        ("dev", BrokenKeyring(), OfflinePlaces),
        ("test", InMemorySecrets(), OfflinePlaces),
        ("test", with_key(), GooglePlaces),
        ("test", BrokenKeyring(), OfflinePlaces),
    ],
)
def test_build_places_chooses_by_environment_and_key(
    env: str,
    secrets: InMemorySecrets,
    expected: type,
    meter: InMemoryUsageMeter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    places = main.build_places(env, secrets, meter)

    assert type(places) is expected
    [chosen] = [
        r
        for r in caplog.records
        if r.name == "nextron.main" and r.getMessage() == "places_port_chosen"
    ]
    assert fields(chosen, "live") == (expected is GooglePlaces,)
    assert KEY not in repr(vars(chosen))
    assert meter.calls == []  # choosing makes no request
    if isinstance(places, GooglePlaces):
        assert places._http is None  # noqa: SLF001 - no client built yet


def test_the_engine_in_dev_without_a_key_searches_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))

    app = main.build_app(load_bootstrap(), units=FakeUnits(), secrets=InMemorySecrets("dev"))

    searches = app.state.place_searches
    assert isinstance(searches, PlaceSearches)
    assert isinstance(searches.places, OfflinePlaces)


def test_the_engine_takes_an_injected_places_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    fake = ScriptedPlaces()

    app = main.build_app(
        load_bootstrap(), units=FakeUnits(), secrets=InMemorySecrets("test"), places=fake
    )

    assert app.state.place_searches.places is fake
