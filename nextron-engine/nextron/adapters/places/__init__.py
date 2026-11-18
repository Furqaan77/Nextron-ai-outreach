"""The Google Places adapters (Story 8.2; FR-5, FR-49, AD-9, AD-14, AD-17, NFR-12): Places Text
Search (New) over ``httpx2``, and an offline stand-in.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library. Nothing
here logs.

**``GooglePlaces(secrets, *, meter, http=None)``** implements ``PlacesPort``. Without ``http``
it builds ``default_http_client()`` (the live constructor: an ``httpx2.Client`` with a 10 s
connect timeout and 15 s for each other phase, that never follows redirects) on its first call,
looked up when it is called (so a test can refuse it) and under a lock.

``text_search(query, location, page_token=None)``, in order:

1. a ``query`` or ``location`` that isn't a str with text, or a ``page_token`` that isn't
   ``None`` or a str with text, raises ``places_refused`` with no request and nothing metered;
2. reads the API key with ``read_credential(secrets, "google_places")``, every time, and never
   keeps it; its ``AdapterError``s (``credential_not_set``, ``keyring_failed``) propagate with
   no request made;
3. makes one POST to ``https://places.googleapis.com/v1/places:searchText`` with the headers
   ``X-Goog-Api-Key: <key>`` (the only place the key goes), ``X-Goog-FieldMask:
   <nextron.domain.places.FIELD_MASK, comma-joined>`` and ``Content-Type: application/json``,
   and the JSON body ``{"textQuery": "<query> in <location>", "pageSize": 20}`` plus
   ``"pageToken"`` when one is given (whitespace collapsed in the query and location). It runs
   inside ``Metered(meter, "places", "text_search", sku=<places_sku(FIELD_MASK)>)``: exactly
   one ``ApiCall`` per request, ``ok`` when the call returns, else the ``AdapterError``'s code.

The answer is read from ``places[]`` (``id``, ``websiteUri``, ``displayName.text``,
``location.latitude`` and ``location.longitude``) and ``nextPageToken``. A body without
``places`` has no candidates. A place without an ``id`` of 1 to 512 characters is skipped; a
``websiteUri`` or display name that isn't a text reads as ``None``; a location without two
finite numbers in range (an integer too large for a float counts as none) reads as no
location (both ``None``). A missing or empty ``nextPageToken`` is the last page.

==============================================  ========================================
Answer                                          Outcome
==============================================  ========================================
2xx, a JSON object of the right shape           the ``PlaceSearchPage``
429                                             retryable ``places_rate_limited``
5xx, or any transport failure                   retryable ``places_unavailable``
401 or 403                                      ``places_unauthorized``
any other status (400 and 3xx included)         ``places_refused``, naming the status
a body over 2 MB, not a JSON object, or a       ``places_bad_response``
``places`` that isn't a list
==============================================  ========================================

Every error is an ``AdapterError`` whose message is this module's own text, raised ``from
None``: none carries the key, the query, the location, the token or a response body, in its
message, ``repr`` or traceback.

**``OfflinePlaces()``** also implements ``PlacesPort``: ``text_search`` makes no request, meters
nothing and returns an empty last page. ``nextron.main.build_places`` uses it outside ``prod``
unless a Places key is stored, so paid use never starts on its own in development or tests.
"""

import json
import math
import threading
from collections.abc import Mapping
from typing import Any, Final

import httpx2

from nextron.domain.credentials import CredentialKey
from nextron.domain.places import FIELD_MASK, PROVIDER, TEXT_SEARCH, places_sku
from nextron.ports.errors import AdapterError
from nextron.ports.places import (
    PLACE_ID_LENGTH,
    PLACES_BAD_RESPONSE,
    PLACES_RATE_LIMITED,
    PLACES_REFUSED,
    PLACES_UNAUTHORIZED,
    PLACES_UNAVAILABLE,
    PlaceCandidate,
    PlaceSearchPage,
)
from nextron.ports.secrets import SecretsPort, read_credential
from nextron.ports.usage_meter import Metered, UsageMeterPort

SEARCH_URL: Final = "https://places.googleapis.com/v1/places:searchText"
TIMEOUT_S: Final = 15.0
CONNECT_TIMEOUT_S: Final = 10.0
PAGE_SIZE: Final = 20
MAX_BODY_BYTES: Final = 2_000_000
FIELD_MASK_HEADER: Final = ",".join(FIELD_MASK)
SKU: Final = places_sku(FIELD_MASK)

type Document = Mapping[str, Any]


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 15 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(
        timeout=httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


class GooglePlaces:
    """``PlacesPort`` over Places Text Search (New); see the module docstring."""

    def __init__(
        self, secrets: SecretsPort, *, meter: UsageMeterPort, http: httpx2.Client | None = None
    ) -> None:
        self._secrets = secrets
        self._meter = meter
        self._http = http
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "GooglePlaces()"

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def text_search(
        self, query: str, location: str, page_token: str | None = None
    ) -> PlaceSearchPage:
        query_text = _text(query)
        location_text = _text(location)
        if not query_text or not location_text:
            raise AdapterError(
                False, PLACES_REFUSED, "a Places search needs a query and a location"
            ) from None
        if page_token is not None and (not isinstance(page_token, str) or not page_token.strip()):
            raise AdapterError(
                False, PLACES_REFUSED, "a Places page token must be a non-empty text"
            ) from None
        body: dict[str, object] = {
            "textQuery": f"{query_text} in {location_text}",
            "pageSize": PAGE_SIZE,
        }
        if page_token is not None:
            body["pageToken"] = page_token
        key = read_credential(self._secrets, CredentialKey.GOOGLE_PLACES)
        headers = {
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": FIELD_MASK_HEADER,
            "Content-Type": "application/json",
        }
        http = self._client()
        with Metered(self._meter, PROVIDER, TEXT_SEARCH, sku=SKU):  # one ApiCall, any outcome
            try:
                response = http.post(SEARCH_URL, json=body, headers=headers)
            except Exception:  # noqa: BLE001 - any transport failure: a search is safe to retry
                raise AdapterError(
                    True, PLACES_UNAVAILABLE, "the Google Places request failed"
                ) from None
            return document_page(_answer(response))


def _answer(response: httpx2.Response) -> Document:
    status = response.status_code
    if status == 429:
        raise AdapterError(
            True, PLACES_RATE_LIMITED, "Google Places answered with HTTP 429"
        ) from None
    if 500 <= status < 600:
        raise AdapterError(
            True, PLACES_UNAVAILABLE, f"Google Places answered with HTTP {status}"
        ) from None
    if status in (401, 403):
        raise AdapterError(
            False, PLACES_UNAUTHORIZED, f"Google Places refused the API key with HTTP {status}"
        ) from None
    if not 200 <= status < 300:
        raise AdapterError(
            False, PLACES_REFUSED, f"Google Places refused the request with HTTP {status}"
        ) from None
    try:
        content: bytes | None = response.content
    except Exception:  # noqa: BLE001 - a body that can't be read is a bad response
        content = None
    if content is None or len(content) > MAX_BODY_BYTES:
        raise AdapterError(
            False, PLACES_BAD_RESPONSE, "Google Places answered with an unreadable body"
        ) from None
    try:
        document = json.loads(content)
    except Exception:  # noqa: BLE001 - any body that isn't JSON is a bad response
        document = None
    if not isinstance(document, dict):
        raise AdapterError(
            False,
            PLACES_BAD_RESPONSE,
            "Google Places answered with a body that isn't a JSON object",
        ) from None
    return document


def _number(value: object, limit: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # an int too large for a float
        return None
    return number if math.isfinite(number) and -limit <= number <= limit else None


def _location(value: object) -> tuple[float | None, float | None]:
    if not isinstance(value, Mapping):
        return None, None
    lat = _number(value.get("latitude"), 90.0)
    lng = _number(value.get("longitude"), 180.0)
    return (lat, lng) if lat is not None and lng is not None else (None, None)


def _candidate(item: object) -> PlaceCandidate | None:
    """A ``PlaceCandidate`` from one ``places`` item, or ``None`` when its id isn't valid."""
    if not isinstance(item, Mapping):
        return None
    place_id = item.get("id")
    if not isinstance(place_id, str) or not 1 <= len(place_id) <= PLACE_ID_LENGTH:
        return None
    website = item.get("websiteUri")
    display = item.get("displayName")
    name = display.get("text") if isinstance(display, Mapping) else None
    lat, lng = _location(item.get("location"))
    try:
        return PlaceCandidate(
            place_id=place_id,
            website_uri=website if isinstance(website, str) else None,
            display_name=name if isinstance(name, str) else None,
            lat=lat,
            lng=lng,
        )
    except (TypeError, ValueError):  # the checks above make this unreachable
        return None


def document_page(document: Document) -> PlaceSearchPage:
    """The page a search answer holds: its valid places, in order, and the next page's token."""
    places = document.get("places", [])
    if not isinstance(places, list):
        raise AdapterError(
            False, PLACES_BAD_RESPONSE, "Google Places answered with places that aren't a list"
        ) from None
    candidates = tuple(
        candidate for item in places if (candidate := _candidate(item)) is not None
    )
    token = document.get("nextPageToken")
    return PlaceSearchPage(candidates, token if isinstance(token, str) and token else None)


class OfflinePlaces:
    """``PlacesPort`` that makes no request: every search is an empty last page."""

    def __repr__(self) -> str:
        return "OfflinePlaces()"

    def text_search(
        self, query: str, location: str, page_token: str | None = None
    ) -> PlaceSearchPage:
        return PlaceSearchPage((), None)
