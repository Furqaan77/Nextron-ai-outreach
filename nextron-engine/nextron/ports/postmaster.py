"""The ``PostmasterPort`` (FR-38, FR-58, AD-9; Story 6.6): a Sending Domain's Gmail spam rate
from Google Postmaster Tools.

``nextron.adapters.postmaster.GooglePostmaster`` implements it over the Postmaster Tools API
(v1 ``trafficStats``), under the ``postmaster`` Google grant; tests use
``tests.fakes.postmaster.FakePostmaster``.

- ``PostmasterReading(ppm)``: the day's user-reported spam rate in parts per million
  (``nextron.domain.reputation.ratio_ppm``), or ``None`` when Postmaster has no data for it
  (never 0). ``ppm`` is ``None`` or an ``int`` from 0 to 1,000,000; anything else raises
  ``TypeError`` or ``ValueError``.
- ``spam_rate(domain, day) -> PostmasterReading``: the reading for ``domain`` (a Sending Domain,
  lower-case) on the UTC date ``day``. No ratio in the day's stats, no stats for the day (404)
  and a domain Postmaster won't show (403: not verified for the grant's account) all read as
  ``ppm=None``.

A failed call raises ``AdapterError`` (``nextron.ports.errors``):

- ``postmaster_unavailable`` (retryable): a network error or timeout, 429 or a 5xx;
- ``postmaster_rejected``: any other 4xx, or a domain or day the adapter refuses before any
  request;
- ``postmaster_bad_response``: a 2xx whose body isn't a traffic-stats object with a valid
  ratio.

The access token's own errors (``google_not_granted``, ``google_needs_reconsent``,
``google_client_not_imported``, or a Credential Manager failure) propagate as they are, before
any request. No message holds the token, the domain or a body.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final, Protocol, runtime_checkable

POSTMASTER_UNAVAILABLE: Final = "postmaster_unavailable"
POSTMASTER_REJECTED: Final = "postmaster_rejected"
POSTMASTER_BAD_RESPONSE: Final = "postmaster_bad_response"
POSTMASTER_CODES: Final = (POSTMASTER_UNAVAILABLE, POSTMASTER_REJECTED, POSTMASTER_BAD_RESPONSE)
MAX_PPM: Final = 1_000_000


@dataclass(frozen=True, slots=True)
class PostmasterReading:
    """One day's spam rate in parts per million, or ``None`` for no data."""

    ppm: int | None

    def __post_init__(self) -> None:
        if self.ppm is None:
            return
        if isinstance(self.ppm, bool) or not isinstance(self.ppm, int):
            raise TypeError("PostmasterReading.ppm must be an int or None")
        if not 0 <= self.ppm <= MAX_PPM:
            raise ValueError("PostmasterReading.ppm must be from 0 to 1,000,000")


@runtime_checkable
class PostmasterPort(Protocol):
    """Reads a Sending Domain's daily spam rate; see the module docstring."""

    def spam_rate(self, domain: str, day: date) -> PostmasterReading:
        """``domain``'s user-reported spam rate on ``day`` (UTC); ``ppm=None`` for no data."""
        ...
