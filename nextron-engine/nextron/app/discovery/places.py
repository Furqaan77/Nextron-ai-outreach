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

The page's candidates are transient (the Maps terms): this module stores nothing, and no log
line holds a name, a website, the query, the location or the page token. Paging and turning
candidates into Leads are the caller's (Story 8.3).
"""

import logging
import threading
from collections.abc import Iterable
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db.uow import current, in_transaction, use_case
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

# Held for the whole of every ``PlaceSearches.page``: check, request and usage record (Story 8.1)
PLACES_LOCK: Final = threading.Lock()


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

    def page(
        self, query: str, location: str, page_token: str | None = None
    ) -> PlaceSearchPage | None:
        """``search_page`` inside the caller's use case; raises ``RuntimeError`` when a
        transaction is open. The check, the request and its usage record hold ``PLACES_LOCK``."""
        if in_transaction():
            raise RuntimeError("PlaceSearches.page must be called with no transaction open")
        with PLACES_LOCK:
            with current().transaction():
                reason = self.refusal()
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
