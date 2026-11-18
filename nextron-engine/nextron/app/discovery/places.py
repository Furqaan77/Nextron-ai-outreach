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
must be called with no transaction open. ``search_page`` is ``page`` as a use case.

The page's candidates are transient (the Maps terms): this module stores nothing, and no log
line holds a name, a website, the query, the location or the page token. Paging and turning
candidates into Leads are the caller's (Story 8.3).
"""

import logging
from collections.abc import Iterable
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db.uow import current, use_case
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.places import FIELD_MASK, places_sku, quota_for_sku
from nextron.ports.clock import Clock
from nextron.ports.places import PlaceSearchPage, PlacesPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

PLACES_SEARCH_REFUSED: Final = "places_search_refused"
PLACES_PAGE_FETCHED: Final = "places_page_fetched"
# Why a search was refused, as logged
REASON_AI_BUDGET: Final = "ai_budget"
REASON_QUOTA: Final = "quota"


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

    def page(
        self, query: str, location: str, page_token: str | None = None
    ) -> PlaceSearchPage | None:
        """``search_page`` inside the caller's use case, with no transaction open."""
        with current().transaction():
            usage = self._meter.snapshot(self._clock.now())
            limits = setup_queries.budget_limits()
            allowed = allow(BudgetClass.BLOCKABLE, usage, limits, quota=self._quota)
            reason = None
            if not allowed:
                ai_only = allow(BudgetClass.BLOCKABLE, usage, limits)
                reason = REASON_QUOTA if ai_only else REASON_AI_BUDGET
        if reason is not None:
            log.info(PLACES_SEARCH_REFUSED, extra={"quota": self._quota, "reason": reason})
            return None
        page = self._places.text_search(query, location, page_token)
        log.info(
            PLACES_PAGE_FETCHED,
            extra={
                "count": len(page.candidates),
                "has_next": page.next_page_token is not None,
            },
        )
        return page
