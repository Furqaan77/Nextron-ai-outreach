"""A Contact's Market and timezone (FR-2, FR-51, AD-7, AD-11, NFR-5; Story 3.2). Standard
library and ``nextron.domain`` only.

``research`` resolves every Contact through ``resolve_contact_market`` in the same transaction
as the Contact write, and stores the result on the Contact (``market_id``, ``market_basis``,
``tz``, ``tz_basis``) with the notes on its first ``contact`` Provenance Record.

**Types.** ``MarketBasis`` (a ``StrEnum``): ``office_location``, ``lead_country``,
``conflict_stricter``, ``cctld_outside_markets``, ``location_outside_markets``,
``location_unknown``. ``TzBasis``: ``office_location``, ``lead_hq``, ``market_default``,
``us_unknown_fallback``, ``no_market``. ``ContactMarket(market, market_basis, tz, tz_basis,
notes)`` is frozen; its invariant is ``market is None`` if and only if ``tz is None`` if and only
if ``tz_basis`` is ``no_market`` (a ``ValueError`` otherwise). ``RESOLVE_CONTACT_MARKETS_JOB``
is the one-off job that resolves the Contacts stored before this story.

**``resolve_contact_market(*, email, office, lead_country, lead_hq_tz, markets)``** is pure.
"Enabled" means ``market_for_country(code, markets)`` isn't ``None``.

The Market, in this order:

1. **The place** is ``office.country_code`` (basis ``office_location``) when there is an
   office, otherwise ``lead_country`` (basis ``lead_country``).
2. **ccTLD.** With an email, ``domain_country = cctld_country(email)``. When it is set and not
   enabled, there is no Market (``cctld_outside_markets``), whatever the place.
3. **No place** gives no Market (``location_unknown``).
4. **A place outside** the enabled Markets gives no Market (``location_outside_markets``).
5. **Conflict.** A ``domain_country`` whose Market differs from the place's Market gives
   ``conflict_stricter``: the stricter Market applies, the one with more of these true: not
   ``cold_email_allowed``, ``free_webmail_blocked``, ``privacy_line_required``,
   ``eligible_legal_forms is not None``. A tie gives no Market.
6. **Otherwise** the place's Market applies, with the place's basis.

The timezone is resolved only when a Market applies (otherwise ``tz`` is ``None`` and
``tz_basis`` is ``no_market``), from the **place's** Market, which in a conflict is the place's
own Market, not the stricter one:

1. **Office.** With an office whose ``geo.location_zone`` is set: that zone, ``office_location``.
2. **Headquarters.** With ``lead_hq_tz`` set and ``geo.zone_country(lead_hq_tz)`` the place's
   country: that zone, ``lead_hq``.
3. **Unknown-location window.** When the place's Market has an ``unknown_send_window``: its
   ``unknown_timezone``, ``us_unknown_fallback``.
4. **Otherwise** its ``default_timezone``, ``market_default``.

**Notes** are the Market note, then the timezone note, each only where there is one (the
``*_NOTE`` texts below). They hold country codes, top-level labels, zone names, Market names and
Send Windows only, never an address, a name or anything else about a person.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain.geo import Location, location_zone, zone_country
from nextron.domain.markets import MarketDef, cctld_country, market_for_country, send_window_text

RESOLVE_CONTACT_MARKETS_JOB: Final = "resolve_contact_markets"

# The Market notes.
LEAD_COUNTRY_NOTE: Final = "Market taken from the Lead's country; office location unknown"
CONFLICT_NOTE: Final = (
    "The {place} and the email's country-code domain point to different Markets "
    "({place_market} and {domain_market}); the stricter {chosen} Market applies"
)
CONFLICT_TIE_NOTE: Final = (
    "The {place} and the email's country-code domain point to different Markets "
    "({place_market} and {domain_market}) with equally strict rules, so no Market applies"
)
CCTLD_OUTSIDE_NOTE: Final = (
    "No Market: the email address is on the country-code domain .{label}, outside the "
    "enabled Markets"
)
OFFICE_OUTSIDE_NOTE: Final = "No Market: the office location ({code}) isn't in an enabled Market"
LEAD_OUTSIDE_NOTE: Final = (
    "No Market: the Lead's country ({code}) isn't in an enabled Market; office location unknown"
)
LOCATION_UNKNOWN_NOTE: Final = (
    "No Market: neither the office location nor the Lead's country is known"
)
OFFICE_PLACE: Final = "office location"
LEAD_PLACE: Final = "Lead's country (office location unknown)"
# The timezone notes.
LEAD_HQ_NOTE: Final = "Timezone taken from the Lead's headquarters ({tz})"
MARKET_DEFAULT_NOTE: Final = (
    "Timezone is the {name} Market's default ({tz}); neither the office location nor the "
    "Lead's headquarters gave one"
)
US_UNKNOWN_NOTE: Final = (
    "Timezone falls back to {tz}: the US state is unknown, so the Send Window narrows to "
    "{window}"
)


class MarketBasis(StrEnum):
    OFFICE_LOCATION = "office_location"
    LEAD_COUNTRY = "lead_country"
    CONFLICT_STRICTER = "conflict_stricter"
    CCTLD_OUTSIDE_MARKETS = "cctld_outside_markets"
    LOCATION_OUTSIDE_MARKETS = "location_outside_markets"
    LOCATION_UNKNOWN = "location_unknown"


class TzBasis(StrEnum):
    OFFICE_LOCATION = "office_location"
    LEAD_HQ = "lead_hq"
    MARKET_DEFAULT = "market_default"
    US_UNKNOWN_FALLBACK = "us_unknown_fallback"
    NO_MARKET = "no_market"


@dataclass(frozen=True, slots=True)
class ContactMarket:
    """A Contact's resolved Market (or ``None``) and timezone, why each applies, and the
    Provenance notes; see the module docstring."""

    market: MarketDef | None
    market_basis: MarketBasis
    tz: str | None
    tz_basis: TzBasis
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        no_market = self.market is None
        if no_market != (self.tz is None) or no_market != (self.tz_basis is TzBasis.NO_MARKET):
            raise ValueError(
                "a ContactMarket has a Market, a timezone and a timezone basis other than "
                "no_market together, or none of them"
            )


def strictness(market: MarketDef) -> int:
    """How many of the four restrictive rules ``market`` has: no cold email, free webmail
    blocked, a privacy line required, legal forms checked."""
    return sum(
        (
            not market.cold_email_allowed,
            market.free_webmail_blocked,
            market.privacy_line_required,
            market.eligible_legal_forms is not None,
        )
    )


def _last_label(email: str) -> str:
    """The email's top-level label, normalised as ``markets.cctld_country`` reads it."""
    domain = email.lower().strip().rpartition("@")[2]
    domain = domain.removesuffix(".")
    return domain.rpartition(".")[2]


def _no_market(basis: MarketBasis, note: str) -> ContactMarket:
    return ContactMarket(None, basis, None, TzBasis.NO_MARKET, (note,))


def _timezone(
    place_market: MarketDef, office: Location | None, lead_hq_tz: str | None
) -> tuple[str, TzBasis, str | None]:
    """The zone, its basis and its note (or ``None``) for a Contact in ``place_market``."""
    if office is not None:
        zone = location_zone(office.country_code, office.region)
        if zone is not None:
            return zone, TzBasis.OFFICE_LOCATION, None
    if lead_hq_tz is not None and zone_country(lead_hq_tz) == place_market.country_code:
        return lead_hq_tz, TzBasis.LEAD_HQ, LEAD_HQ_NOTE.format(tz=lead_hq_tz)
    window = place_market.unknown_send_window
    if window is not None:
        zone = place_market.unknown_timezone
        note = US_UNKNOWN_NOTE.format(tz=zone, window=send_window_text(window))
        return zone, TzBasis.US_UNKNOWN_FALLBACK, note
    zone = place_market.default_timezone
    note = MARKET_DEFAULT_NOTE.format(name=place_market.name, tz=zone)
    return zone, TzBasis.MARKET_DEFAULT, note


def resolve_contact_market(
    *,
    email: str | None,
    office: Location | None,
    lead_country: str | None,
    lead_hq_tz: str | None,
    markets: Iterable[MarketDef],
) -> ContactMarket:
    """A Contact's Market and timezone; see the module docstring."""
    stored = tuple(markets)
    place: str | None
    if office is not None:
        place, place_text = office.country_code, OFFICE_PLACE
        place_basis = MarketBasis.OFFICE_LOCATION
    else:
        place, place_text = lead_country, LEAD_PLACE
        place_basis = MarketBasis.LEAD_COUNTRY
    domain_market: MarketDef | None = None
    if email is not None and (domain_country := cctld_country(email)) is not None:
        domain_market = market_for_country(domain_country, stored)
        if domain_market is None:
            note = CCTLD_OUTSIDE_NOTE.format(label=_last_label(email))
            return _no_market(MarketBasis.CCTLD_OUTSIDE_MARKETS, note)
    if place is None:
        return _no_market(MarketBasis.LOCATION_UNKNOWN, LOCATION_UNKNOWN_NOTE)
    place_market = market_for_country(place, stored)
    if place_market is None:
        outside = OFFICE_OUTSIDE_NOTE if office is not None else LEAD_OUTSIDE_NOTE
        return _no_market(MarketBasis.LOCATION_OUTSIDE_MARKETS, outside.format(code=place))
    market, basis = place_market, place_basis
    market_note = LEAD_COUNTRY_NOTE if place_basis is MarketBasis.LEAD_COUNTRY else None
    if domain_market is not None and domain_market.code != place_market.code:
        names = {
            "place": place_text,
            "place_market": place_market.name,
            "domain_market": domain_market.name,
        }
        place_strictness, domain_strictness = strictness(place_market), strictness(domain_market)
        if place_strictness == domain_strictness:
            return _no_market(MarketBasis.CONFLICT_STRICTER, CONFLICT_TIE_NOTE.format(**names))
        market = place_market if place_strictness > domain_strictness else domain_market
        basis = MarketBasis.CONFLICT_STRICTER
        market_note = CONFLICT_NOTE.format(chosen=market.name, **names)
    zone, tz_basis, tz_note = _timezone(place_market, office, lead_hq_tz)
    notes = tuple(note for note in (market_note, tz_note) if note is not None)
    return ContactMarket(market, basis, zone, tz_basis, notes)
