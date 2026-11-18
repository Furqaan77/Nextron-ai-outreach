"""The rules of a Google Places search (Story 8.2; FR-5, FR-49, AD-9, NFR-12). Standard library
and ``nextron.domain`` only.

**The field mask.** ``FIELD_MASK`` is every field a Text Search (New) request asks for, and
nothing else: ``places.id``, ``places.websiteUri``, ``places.displayName``, ``places.location``
and ``nextPageToken``. Address, phone, rating and review fields are never requested: the Maps
terms forbid storing them, and asking for them costs more.

**The SKU.** Google bills a request by the richest field its mask asks for.
``places_sku(fields)`` is ``enterprise`` when ``places.websiteUri`` is among ``fields``, else
``pro``; ``quota_for_sku(sku)`` names the ``nextron.domain.budget.QUOTAS`` quota that meters it
(``places_enterprise`` or ``places_pro``) and raises ``ValueError`` for any other SKU. Pro and
Enterprise are metered and limited separately.

**Coordinates.** A Lead may keep a place's coordinates (``place_lat``, ``place_lng``,
``place_coords_at``) for at most ``PLACE_COORDS_TTL`` (30 days); the daily
``PURGE_PLACE_COORDS_JOB`` (``purge_place_coords``) nulls older ones. ``place_id`` is kept for
good. ``coords_cutoff(now)`` is ``now - PLACE_COORDS_TTL``: coordinates stored strictly before
it are purged.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Final, Literal

from nextron.domain.budget import quota_spec

PROVIDER: Final = "places"  # the usage meter's provider ...
TEXT_SEARCH: Final = "text_search"  # ... and operation
WEBSITE_FIELD: Final = "places.websiteUri"
FIELD_MASK: Final[tuple[str, ...]] = (
    "places.id",
    WEBSITE_FIELD,
    "places.displayName",
    "places.location",
    "nextPageToken",
)
SKU_ENTERPRISE: Final = "enterprise"
SKU_PRO: Final = "pro"
PURGE_PLACE_COORDS_JOB: Final = "purge_place_coords"
PLACE_COORDS_TTL: Final = timedelta(days=30)

type PlacesSku = Literal["enterprise", "pro"]


def places_sku(fields: Iterable[str]) -> PlacesSku:
    """The SKU a request with this field mask is billed as."""
    return SKU_ENTERPRISE if WEBSITE_FIELD in tuple(fields) else SKU_PRO


def quota_for_sku(sku: str) -> str:
    """The name of the quota that limits ``sku``'s requests."""
    for name in ("places_enterprise", "places_pro"):
        spec = quota_spec(name)
        if spec.sku == sku:
            return spec.name
    raise ValueError("quota_for_sku takes a Places SKU: enterprise or pro")


def coords_cutoff(now: datetime) -> datetime:
    """Coordinates stored strictly before this time are purged."""
    return now - PLACE_COORDS_TTL
