"""The ``PlacesPort`` (Story 8.2; FR-5, FR-49, AD-9, AD-17): Google Places Text Search (New).

``nextron.adapters.places.GooglePlaces`` implements it over the Places API, metered by SKU;
``nextron.adapters.places.OfflinePlaces`` is the engine's no-request stand-in outside ``prod``;
tests use ``tests.fakes.places.ScriptedPlaces``.

**The Maps terms.** A ``PlaceCandidate`` is transient: only its ``place_id`` may be stored for
good, and its coordinates for at most 30 days. Its ``display_name`` and ``website_uri`` never
reach the database, an ``events`` row, a job payload, a log line or an AI prompt; both are left
out of its ``repr``. ``tests/contracts/test_places_storage_terms.py`` keeps it so.

- ``PlaceCandidate(place_id, website_uri, display_name, lat, lng)``: ``place_id`` is a str of 1
  to ``PLACE_ID_LENGTH`` (512) characters; ``website_uri`` and ``display_name`` are a str or
  ``None``; ``lat`` (−90..90) and ``lng`` (−180..180) are finite floats, both set or both
  ``None``. A wrong type raises ``TypeError``, a bad value ``ValueError``; no message holds a
  value.
- ``PlaceSearchPage(candidates, next_page_token)``: one page of results, in the API's order,
  and the token of the next page (``None`` on the last).
- ``text_search(query, location, page_token=None) -> PlaceSearchPage``: one page of the places
  matching ``query`` in ``location`` (free text, such as "Columbus, OH").

A failed call raises ``AdapterError`` (``nextron.ports.errors``): ``places_rate_limited`` and
``places_unavailable`` are retryable; ``places_unauthorized``, ``places_refused`` and
``places_bad_response`` aren't, nor are the credential codes (``credential_not_set``,
``keyring_failed``).
"""

import math
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

PLACE_ID_LENGTH: Final = 512  # nextron.db.models.leads.PLACE_ID_LENGTH
PLACES_RATE_LIMITED: Final = "places_rate_limited"
PLACES_UNAVAILABLE: Final = "places_unavailable"
PLACES_UNAUTHORIZED: Final = "places_unauthorized"
PLACES_REFUSED: Final = "places_refused"
PLACES_BAD_RESPONSE: Final = "places_bad_response"
PLACES_CODES: Final = (
    PLACES_RATE_LIMITED,
    PLACES_UNAVAILABLE,
    PLACES_UNAUTHORIZED,
    PLACES_REFUSED,
    PLACES_BAD_RESPONSE,
)


def _coordinate(value: object, limit: float, what: str) -> None:
    if value is None:
        return
    if not isinstance(value, float):
        raise TypeError(f"PlaceCandidate.{what} must be a float or None")
    if not math.isfinite(value) or not -limit <= value <= limit:
        raise ValueError(f"PlaceCandidate.{what} must be within ±{limit:g}")


@dataclass(frozen=True, slots=True)
class PlaceCandidate:
    """One place a search found: transient, never stored but for ``place_id`` (and the
    coordinates, for at most 30 days)."""

    place_id: str
    website_uri: str | None = field(repr=False)
    display_name: str | None = field(repr=False)
    lat: float | None
    lng: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.place_id, str):
            raise TypeError("PlaceCandidate.place_id must be a str")
        if not 1 <= len(self.place_id) <= PLACE_ID_LENGTH:
            raise ValueError(f"PlaceCandidate.place_id must be 1 to {PLACE_ID_LENGTH} characters")
        for value, what in ((self.website_uri, "website_uri"), (self.display_name, "display_name")):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"PlaceCandidate.{what} must be a str or None")
        _coordinate(self.lat, 90.0, "lat")
        _coordinate(self.lng, 180.0, "lng")
        if (self.lat is None) != (self.lng is None):
            raise ValueError("PlaceCandidate.lat and lng are both set or both None")


@dataclass(frozen=True, slots=True)
class PlaceSearchPage:
    """One page of a text search: its candidates in the API's order, and the next page's token
    (``None`` on the last page)."""

    candidates: tuple[PlaceCandidate, ...]
    next_page_token: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(candidate, PlaceCandidate) for candidate in self.candidates
        ):
            raise TypeError("PlaceSearchPage.candidates must be a tuple of PlaceCandidate")
        if self.next_page_token is not None:
            if not isinstance(self.next_page_token, str):
                raise TypeError("PlaceSearchPage.next_page_token must be a str or None")
            if not self.next_page_token:
                raise ValueError("PlaceSearchPage.next_page_token must not be empty")


@runtime_checkable
class PlacesPort(Protocol):
    """Searches Google Places by text."""

    def text_search(
        self, query: str, location: str, page_token: str | None = None
    ) -> PlaceSearchPage:
        """One page of the places matching ``query`` in ``location``."""
        ...
