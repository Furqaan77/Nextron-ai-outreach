"""One Google Places search page, inside the budget (Story 8.2; FR-5, FR-49, AD-9, AD-17),
owned by the ``discovery`` service. ``nextron.app.discovery.api``'s docstring points here.

**``PlaceSearches(clock, meter, places, *, fields=FIELD_MASK)``**: ``fields`` is the field mask
the ``places`` port's requests use (``nextron.domain.places.FIELD_MASK``, the one
``GooglePlaces`` sends), which sets their SKU (``places_sku``) and so the quota each request
counts against (``quota_for_sku``: ``places_enterprise`` or ``places_pro``). ``places`` is the
port it searches through.

``@use_case search_page(query, location, page_token=None) -> PlaceSearchPage | None``:

1. **The per-call check** (a transaction): ``nextron.domain.budget.allow(BudgetClass.BLOCKABLE,
   meter.snapshot(now), queries.setup.budget_limits(), quota=<the SKU's quota>)``. A refusal
   (the month's AI budget reached, or the quota's usage at or past its limit; a limit of 0, Pro's
   default, always refuses, and the Enterprise quota's maximum is Google's free monthly
   allowance, so paid use never starts on its own) logs ``places_search_refused``
   (``quota``, ``reason``: ``ai_budget`` or ``quota``) and returns ``None`` with no request.
2. **The request**, with no transaction open: one ``places.text_search(query, location,
   page_token)``. Its ``AdapterError`` propagates; the adapter has metered it.
3. Logs ``places_page_fetched`` (``count``: the candidates, ``has_next``) and returns the page.

``page(query, location, page_token=None)`` does the same two steps inside the caller's own use
case (Story 8.3's discovery run, which pages through several queries in one unit of work); it
raises ``RuntimeError`` when it is called with a transaction open. ``search_page`` is ``page``
as a use case.

**Atomic per-call check (Story 8.1).** One process-wide lock (``PLACES_LOCK``) covers the whole
of ``page``: the check's transaction, the request and the request's usage record (the adapter
meters it before it returns). Every Places request in the engine goes through ``page``, so two
calls at 999 of 1,000 can never both pass the check; the engine's instance lock keeps it to one
process.

``refusal() -> str | None`` is the check alone, read inside the caller's open transaction:
``"quota"``, ``"ai_budget"`` or ``None`` (allowed). Story 8.1's discovery runs call it before
they write a ``discovery_runs`` row, so a refused run writes nothing.

**Today's allowance (Story 8.10).** ``day_usage(pace) -> PlacesDay(requests, allowance)``, read
inside the caller's open transaction for ``Pace(local_date, timezone)`` (the Operator's
``sender_timezone``): ``requests`` are the units of this SKU's quota recorded since the local
day's start (``UsageMeterPort.units_since``), and ``allowance`` is
``discovery_topup.places_day_allowance(<the quota's limit>, <the units recorded between the
local month's start and the local day's start>, weekdays_left(local_date))``, so the leftover is
spread over the same local month ``weekdays_left`` counts (the budget's quota check above still
holds the UTC month). ``page(..., pace=Pace(...))`` (a
scheduled discovery run's request) also requires ``requests < allowance`` in the same locked
check, after the budget's; when it isn't, it logs ``places_search_paced`` (``quota``,
``requests``, ``allowance``) and raises ``Paced`` with no request. A manual run passes no
``pace`` and is never paced.

The page's candidates are transient (the Maps terms): this module stores nothing, and no log
line holds a name, a website, the query, the location or the page token. Paging and turning
candidates into Leads are the caller's (Story 8.3).
"""

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db.uow import current, in_transaction, use_case
from nextron.domain.budget import BudgetClass, allow, quota_spec
from nextron.domain.discovery_topup import day_start_utc, places_day_allowance, weekdays_left
from nextron.domain.places import FIELD_MASK, places_sku, quota_for_sku
from nextron.ports.clock import Clock
from nextron.ports.places import PlaceSearchPage, PlacesPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

PLACES_SEARCH_REFUSED: Final = "places_search_refused"
PLACES_PAGE_FETCHED: Final = "places_page_fetched"
PLACES_SEARCH_PACED: Final = "places_search_paced"  # Story 8.10
# Why a search was refused, as logged
REASON_AI_BUDGET: Final = "ai_budget"
REASON_QUOTA: Final = "quota"

# Held for the whole of every ``PlaceSearches.page``: check, request and usage record (Story 8.1)
PLACES_LOCK: Final = threading.Lock()


@dataclass(frozen=True, slots=True)
class Pace:
    """The local day a scheduled request is paced against: its date and the IANA zone."""

    local_date: date
    timezone: str


@dataclass(frozen=True, slots=True)
class PlacesDay:
    """Today's Places requests and today's allowance for scheduled discovery."""

    requests: int
    allowance: int


class Paced(Exception):
    """A scheduled request found today's Places allowance used; no request was made."""


class PlaceSearches:
    """Places search pages, each after the budget's per-call check; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        places: PlacesPort,
        *,
        fields: Iterable[str] = FIELD_MASK,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._places = places
        self._quota = quota_for_sku(places_sku(fields))

    @property
    def places(self) -> PlacesPort:
        """The port the searches go through."""
        return self._places

    @use_case
    def search_page(
        self, query: str, location: str, page_token: str | None = None
    ) -> PlaceSearchPage | None:
        """One page of the places matching ``query`` in ``location``, or ``None`` when the
        budget refused the request; see the module docstring."""
        return self.page(query, location, page_token)

    def refusal(self) -> str | None:
        """Why the budget refuses a Places request now (``"quota"`` or ``"ai_budget"``), or
        ``None`` when it allows one; read inside the caller's open transaction."""
        usage = self._meter.snapshot(self._clock.now())
        limits = setup_queries.budget_limits()
        if allow(BudgetClass.BLOCKABLE, usage, limits, quota=self._quota):
            return None
        ai_only = allow(BudgetClass.BLOCKABLE, usage, limits)
        return REASON_QUOTA if ai_only else REASON_AI_BUDGET

    def day_usage(self, pace: Pace) -> PlacesDay:
        """Today's requests and allowance; read inside the caller's open transaction. See the
        module docstring."""
        spec = quota_spec(self._quota)
        start = day_start_utc(pace.local_date, pace.timezone)
        month_start = day_start_utc(pace.local_date.replace(day=1), pace.timezone)
        requests = self._meter.units_since(spec.provider, spec.sku, start)
        since_month_start = self._meter.units_since(spec.provider, spec.sku, month_start)
        quota = setup_queries.budget_limits().quotas[spec.name]
        allowance = places_day_allowance(
            quota, max(0, since_month_start - requests), weekdays_left(pace.local_date)
        )
        return PlacesDay(requests, allowance)

    def page(
        self,
        query: str,
        location: str,
        page_token: str | None = None,
        *,
        pace: Pace | None = None,
    ) -> PlaceSearchPage | None:
        """``search_page`` inside the caller's use case; raises ``RuntimeError`` when a
        transaction is open, and ``Paced`` when ``pace`` is given and today's allowance is used.
        The checks, the request and its usage record hold ``PLACES_LOCK``."""
        if in_transaction():
            raise RuntimeError("PlaceSearches.page must be called with no transaction open")
        with PLACES_LOCK:
            with current().transaction():
                reason = self.refusal()
                day = None if reason is not None or pace is None else self.day_usage(pace)
            if reason is not None:
                log.info(PLACES_SEARCH_REFUSED, extra={"quota": self._quota, "reason": reason})
                return None
            if day is not None and day.requests >= day.allowance:
                log.info(
                    PLACES_SEARCH_PACED,
                    extra={
                        "quota": self._quota,
                        "requests": day.requests,
                        "allowance": day.allowance,
                    },
                )
                raise Paced
            page = self._places.text_search(query, location, page_token)
        log.info(
            PLACES_PAGE_FETCHED,
            extra={
                "count": len(page.candidates),
                "has_next": page.next_page_token is not None,
            },
        )
        return page
