"""``nextron.domain.contact_market`` (FR-2, FR-51, AD-7, AD-11, NFR-5; Story 3.2) with the shipped
Markets (``seed/markets.yaml``, read by the domain's own parser): every I/O matrix row at domain
level, the order of the rules (a blocking ccTLD first), the conflict and its tie between two
hand-built Markets of equal strictness, the timezone order, the exact note texts, the
``ContactMarket`` invariant over a grid, and ``ContactCandidate.office``. Standard library,
pytest and ``nextron.domain`` only."""

import dataclasses
import itertools
from pathlib import Path

import pytest

from nextron.domain import seed_yaml
from nextron.domain.contact_market import (
    RESOLVE_CONTACT_MARKETS_JOB,
    ContactMarket,
    MarketBasis,
    TzBasis,
    resolve_contact_market,
    strictness,
)
from nextron.domain.contacts import ContactCandidate, InvalidContact
from nextron.domain.geo import Location
from nextron.domain.markets import MarketDef, validate_markets

SEED = Path(__file__).resolve().parents[2] / "seed" / "markets.yaml"
US, UK = validate_markets(seed_yaml.parse(SEED.read_text(encoding="utf-8")))
MARKETS = (US, UK)

LEAD_COUNTRY_NOTE = "Market taken from the Lead's country; office location unknown"
UK_DEFAULT_NOTE = (
    "Timezone is the United Kingdom Market's default (Europe/London); neither the office "
    "location nor the Lead's headquarters gave one"
)
US_FALLBACK_NOTE = (
    "Timezone falls back to America/New_York: the US state is unknown, so the Send Window "
    "narrows to 11:00-17:00"
)


def resolve(
    *,
    email: str | None = None,
    office: Location | None = None,
    lead: str | None = None,
    hq: str | None = None,
    markets: tuple[MarketDef, ...] = MARKETS,
) -> ContactMarket:
    return resolve_contact_market(
        email=email, office=office, lead_country=lead, lead_hq_tz=hq, markets=markets
    )


def outcome(found: ContactMarket) -> tuple[str | None, MarketBasis, str | None, TzBasis]:
    code = None if found.market is None else found.market.code
    return code, found.market_basis, found.tz, found.tz_basis


def test_the_shipped_markets_are_the_us_and_the_uk() -> None:
    assert (US.code, UK.code) == ("us", "uk")
    assert (strictness(US), strictness(UK)) == (0, 3)
    assert RESOLVE_CONTACT_MARKETS_JOB == "resolve_contact_markets"


# The I/O matrix ---------------------------------------------------------------------------------


# Row: Office, generic TLD
def test_an_office_with_a_generic_tld_takes_the_office_market_and_zone() -> None:
    found = resolve(email="jane@firm.com", office=Location("US", "NY"), lead="GB")

    assert outcome(found) == (
        "us",
        MarketBasis.OFFICE_LOCATION,
        "America/New_York",
        TzBasis.OFFICE_LOCATION,
    )
    assert found.market == US
    assert found.notes == ()


# Row: Lead country
def test_no_office_takes_the_leads_country_with_the_ac_note_then_the_default_note() -> None:
    found = resolve(email="jane@firm.co.uk", lead="GB")

    assert outcome(found) == (
        "uk",
        MarketBasis.LEAD_COUNTRY,
        "Europe/London",
        TzBasis.MARKET_DEFAULT,
    )
    assert found.notes == (LEAD_COUNTRY_NOTE, UK_DEFAULT_NOTE)


# Row: US unknown state
def test_a_us_contact_with_no_state_falls_back_to_new_york_with_two_notes() -> None:
    found = resolve(lead="US")

    assert outcome(found) == (
        "us",
        MarketBasis.LEAD_COUNTRY,
        "America/New_York",
        TzBasis.US_UNKNOWN_FALLBACK,
    )
    assert found.notes == (LEAD_COUNTRY_NOTE, US_FALLBACK_NOTE)


def test_a_us_office_with_no_state_falls_back_with_only_the_timezone_note() -> None:
    found = resolve(email="a@firm.com", office=Location("US"), lead="GB")

    assert outcome(found) == (
        "us",
        MarketBasis.OFFICE_LOCATION,
        "America/New_York",
        TzBasis.US_UNKNOWN_FALLBACK,
    )
    assert found.notes == (US_FALLBACK_NOTE,)


# Row: ccTLD blocks
@pytest.mark.parametrize(("email", "label"), [("a@firm.de", "de"), ("a@firm.io", "io")])
def test_a_country_code_domain_outside_the_markets_blocks_even_a_uk_office(
    email: str, label: str
) -> None:
    found = resolve(email=email, office=Location("GB"), lead="GB")

    assert outcome(found) == (None, MarketBasis.CCTLD_OUTSIDE_MARKETS, None, TzBasis.NO_MARKET)
    assert found.notes == (
        f"No Market: the email address is on the country-code domain .{label}, outside the "
        "enabled Markets",
    )


@pytest.mark.parametrize(
    ("office", "lead"),
    [
        (Location("US", "NY"), "US"),
        (Location("CA"), "CA"),  # a blocking ccTLD wins over a blocking location
        (None, None),  # the ccTLD step comes before "no place"
    ],
)
def test_the_cctld_step_comes_first_whatever_the_place(
    office: Location | None, lead: str | None
) -> None:
    found = resolve(email="a@firm.ca", office=office, lead=lead)

    assert found.market_basis is MarketBasis.CCTLD_OUTSIDE_MARKETS
    assert found.notes == (
        "No Market: the email address is on the country-code domain .ca, outside the enabled "
        "Markets",
    )


def test_the_cctld_label_is_read_as_cctld_country_reads_it() -> None:
    found = resolve(email="a@Firm.DE.", lead="GB")

    assert found.market_basis is MarketBasis.CCTLD_OUTSIDE_MARKETS
    assert ".de," in found.notes[0]


# Row: Location blocks
def test_an_office_outside_the_markets_blocks() -> None:
    found = resolve(email="a@firm.com", office=Location("CA"), lead="US")

    assert outcome(found) == (None, MarketBasis.LOCATION_OUTSIDE_MARKETS, None, TzBasis.NO_MARKET)
    assert found.notes == ("No Market: the office location (CA) isn't in an enabled Market",)


def test_a_leads_country_outside_the_markets_blocks_with_no_office() -> None:
    found = resolve(email="a@firm.com", lead="CA")

    assert outcome(found) == (None, MarketBasis.LOCATION_OUTSIDE_MARKETS, None, TzBasis.NO_MARKET)
    assert found.notes == (
        "No Market: the Lead's country (CA) isn't in an enabled Market; office location unknown",
    )


# Row: Conflict
def test_a_us_office_with_a_uk_address_takes_the_stricter_uk_and_the_office_zone() -> None:
    found = resolve(email="a@firm.co.uk", office=Location("US", "TX"), lead="GB")

    assert outcome(found) == (
        "uk",
        MarketBasis.CONFLICT_STRICTER,
        "America/Chicago",
        TzBasis.OFFICE_LOCATION,
    )
    assert found.notes == (
        "The office location and the email's country-code domain point to different Markets "
        "(United States and United Kingdom); the stricter United Kingdom Market applies",
    )


def test_a_conflict_on_the_leads_country_keeps_the_places_timezone() -> None:
    found = resolve(email="a@firm.co.uk", lead="US")

    assert outcome(found) == (
        "uk",
        MarketBasis.CONFLICT_STRICTER,
        "America/New_York",
        TzBasis.US_UNKNOWN_FALLBACK,  # the place's Market, the US, gives the timezone
    )
    assert found.notes == (
        "The Lead's country (office location unknown) and the email's country-code domain point "
        "to different Markets (United States and United Kingdom); the stricter United Kingdom "
        "Market applies",
        US_FALLBACK_NOTE,
    )


def test_a_uk_office_with_a_us_address_keeps_the_stricter_uk() -> None:
    found = resolve(email="a@firm.us", office=Location("GB"), lead="US")

    assert outcome(found) == (
        "uk",
        MarketBasis.CONFLICT_STRICTER,
        "Europe/London",
        TzBasis.OFFICE_LOCATION,
    )
    assert found.notes == (  # the office's zone applies, so there is no timezone note
        "The office location and the email's country-code domain point to different Markets "
        "(United Kingdom and United States); the stricter United Kingdom Market applies",
    )


def test_a_cctld_of_the_places_own_market_is_no_conflict() -> None:
    found = resolve(email="a@firm.us", office=Location("US", "CA"), lead="GB")

    assert outcome(found) == (
        "us",
        MarketBasis.OFFICE_LOCATION,
        "America/Los_Angeles",
        TzBasis.OFFICE_LOCATION,
    )
    assert found.notes == ()


def test_a_tie_between_two_equally_strict_markets_blocks() -> None:
    canada = dataclasses.replace(US, code="ca", name="Canada", country_code="CA", cctlds=("ca",))
    assert strictness(canada) == strictness(US)

    found = resolve(email="a@firm.ca", office=Location("US", "NY"), markets=(US, canada))

    assert outcome(found) == (None, MarketBasis.CONFLICT_STRICTER, None, TzBasis.NO_MARKET)
    assert found.notes == (
        "The office location and the email's country-code domain point to different Markets "
        "(United States and Canada) with equally strict rules, so no Market applies",
    )


# Row: Unknown
@pytest.mark.parametrize("email", [None, "a@firm.com"])
def test_no_office_and_no_lead_country_is_unknown(email: str | None) -> None:
    found = resolve(email=email)

    assert outcome(found) == (None, MarketBasis.LOCATION_UNKNOWN, None, TzBasis.NO_MARKET)
    assert found.notes == (
        "No Market: neither the office location nor the Lead's country is known",
    )


# Row: HQ (domain only)
def test_the_leads_headquarters_zone_applies_only_in_the_places_country() -> None:
    denver = resolve(lead="US", hq="America/Denver")
    london = resolve(lead="US", hq="Europe/London")

    assert (denver.tz, denver.tz_basis) == ("America/Denver", TzBasis.LEAD_HQ)
    assert denver.notes == (
        LEAD_COUNTRY_NOTE,
        "Timezone taken from the Lead's headquarters (America/Denver)",
    )
    assert (london.tz, london.tz_basis) == ("America/New_York", TzBasis.US_UNKNOWN_FALLBACK)


def test_a_uk_headquarters_zone_applies_to_a_uk_lead() -> None:
    found = resolve(lead="GB", hq="Europe/London")

    assert (found.tz, found.tz_basis) == ("Europe/London", TzBasis.LEAD_HQ)


def test_an_office_zone_beats_the_headquarters_and_a_stateless_office_takes_it() -> None:
    texas = resolve(office=Location("US", "TX"), lead="US", hq="America/Denver")
    stateless = resolve(office=Location("US"), lead="US", hq="America/Denver")

    assert (texas.tz, texas.tz_basis) == ("America/Chicago", TzBasis.OFFICE_LOCATION)
    assert (stateless.tz, stateless.tz_basis) == ("America/Denver", TzBasis.LEAD_HQ)


@pytest.mark.parametrize("hq", ["Mars/Olympus", "UTC", "America/Toronto"])
def test_a_headquarters_zone_in_no_known_country_is_ignored(hq: str) -> None:
    found = resolve(lead="US", hq=hq)

    assert found.tz_basis is TzBasis.US_UNKNOWN_FALLBACK


# Disabled Markets -------------------------------------------------------------------------------


def test_a_disabled_market_is_no_market() -> None:
    uk_off = dataclasses.replace(UK, enabled=False)

    lead_gb = resolve(lead="GB", markets=(US, uk_off))
    uk_address = resolve(email="a@firm.co.uk", lead="US", markets=(US, uk_off))

    assert lead_gb.market_basis is MarketBasis.LOCATION_OUTSIDE_MARKETS
    assert uk_address.market_basis is MarketBasis.CCTLD_OUTSIDE_MARKETS
    assert ".uk," in uk_address.notes[0]


def test_with_no_market_stored_every_place_is_outside() -> None:
    found = resolve(email="a@firm.com", lead="US", markets=())

    assert found.market_basis is MarketBasis.LOCATION_OUTSIDE_MARKETS


def test_markets_may_be_any_iterable() -> None:
    found = resolve_contact_market(
        email="a@firm.co.uk",
        office=Location("US", "NY"),
        lead_country="US",
        lead_hq_tz=None,
        markets=(market for market in MARKETS),
    )

    assert found.market == UK


# The invariant ----------------------------------------------------------------------------------

OFFICES = [None, Location("US"), Location("US", "HI"), Location("GB"), Location("CA")]
EMAILS = [None, "a@firm.com", "a@firm.co.uk", "a@firm.us", "a@firm.de", "a@firm.io"]
LEADS = [None, "US", "GB", "CA"]
HQS = [None, "America/Denver", "Europe/London"]


@pytest.mark.parametrize(
    ("office", "email", "lead", "hq"), list(itertools.product(OFFICES, EMAILS, LEADS, HQS))
)
def test_the_invariant_holds_and_notes_hold_no_personal_data(
    office: Location | None, email: str | None, lead: str | None, hq: str | None
) -> None:
    found = resolve(email=email, office=office, lead=lead, hq=hq)

    assert (found.market is None) == (found.tz is None) == (found.tz_basis is TzBasis.NO_MARKET)
    assert isinstance(found.market_basis, MarketBasis)
    assert isinstance(found.tz_basis, TzBasis)
    assert 0 <= len(found.notes) <= 2
    for note in found.notes:
        assert "@" not in note and "firm" not in note
    if found.market is None:
        assert len(found.notes) == 1


@pytest.mark.parametrize(
    ("market", "tz", "tz_basis"),
    [
        (None, "Europe/London", TzBasis.NO_MARKET),
        (None, None, TzBasis.MARKET_DEFAULT),
        (UK, None, TzBasis.MARKET_DEFAULT),
        (UK, "Europe/London", TzBasis.NO_MARKET),
    ],
)
def test_a_contact_market_that_breaks_the_invariant_raises(
    market: MarketDef | None, tz: str | None, tz_basis: TzBasis
) -> None:
    with pytest.raises(ValueError, match="together, or none of them"):
        ContactMarket(market, MarketBasis.LEAD_COUNTRY, tz, tz_basis, ())


def test_the_enums_are_the_specified_values() -> None:
    assert [str(basis) for basis in MarketBasis] == [
        "office_location",
        "lead_country",
        "conflict_stricter",
        "cctld_outside_markets",
        "location_outside_markets",
        "location_unknown",
    ]
    assert [str(basis) for basis in TzBasis] == [
        "office_location",
        "lead_hq",
        "market_default",
        "us_unknown_fallback",
        "no_market",
    ]


# ContactCandidate.office (Row: Bad input) -------------------------------------------------------


def test_a_candidate_keeps_its_office_as_its_last_field() -> None:
    candidate = ContactCandidate(name="Ann", office=Location("US", "NY"))

    assert candidate.office == Location("US", "NY")
    assert [field.name for field in dataclasses.fields(ContactCandidate)][-1] == "office"
    assert ContactCandidate(name="Ann").office is None


@pytest.mark.parametrize("office", ["US", ("US", "NY"), {"country_code": "US"}, 1])
def test_an_office_that_isnt_a_location_raises_invalid_contact(office: object) -> None:
    with pytest.raises(InvalidContact) as caught:
        ContactCandidate(name="Ann", office=office)  # type: ignore[arg-type]

    assert (caught.value.field, caught.value.reason) == ("office", "must be a Location or None")
