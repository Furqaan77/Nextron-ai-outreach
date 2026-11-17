"""``nextron.domain.geo`` (FR-2, AD-11; Story 3.2): the vendored US state table (its 51 rows, the
multi-zone choices, read once and read-only), the malformed-table cases through
``parse_us_state_zones``, ``Location``'s rules, ``location_zone`` and ``zone_country``.
Standard library, pytest and ``nextron.domain`` only."""

import dataclasses
from pathlib import Path

import pytest

from nextron.domain import geo
from nextron.domain.errors import DomainError
from nextron.domain.geo import (
    COUNTRY_ZONES,
    US_STATE_COUNT,
    InvalidLocation,
    Location,
    location_zone,
    parse_us_state_zones,
    us_state_zones,
    zone_country,
)

# The spec's Design Notes table: each zone and its states (the most populous zone of each).
DESIGN_NOTES = {
    "America/New_York": "CT DE DC FL GA KY ME MD MA NH NJ NY NC OH PA RI SC VT VA WV",
    "America/Indiana/Indianapolis": "IN",
    "America/Detroit": "MI",
    "America/Chicago": "AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI",
    "America/Denver": "CO MT NM UT WY",
    "America/Boise": "ID",
    "America/Phoenix": "AZ",
    "America/Los_Angeles": "CA NV OR WA",
    "America/Anchorage": "AK",
    "Pacific/Honolulu": "HI",
}
HEADER = "code,timezone,name"


def table(*rows: str, header: str = HEADER) -> str:
    return "\n".join(["# a comment", "", header, *rows]) + "\n"


def full_rows() -> list[str]:
    return [f"{code},{zone},State {code}" for code, zone in sorted(us_state_zones().items())]


# The vendored table ------------------------------------------------------------------------------


def test_the_table_is_the_51_rows_of_the_design_notes() -> None:
    expected = {code: zone for zone, codes in DESIGN_NOTES.items() for code in codes.split()}

    assert len(expected) == US_STATE_COUNT == 51
    assert dict(us_state_zones()) == expected


@pytest.mark.parametrize(
    ("code", "zone"),
    [
        ("NY", "America/New_York"),
        ("CA", "America/Los_Angeles"),
        ("TX", "America/Chicago"),
        ("AZ", "America/Phoenix"),
        ("HI", "Pacific/Honolulu"),
        ("DC", "America/New_York"),
    ],
)
def test_named_states_have_their_zones(code: str, zone: str) -> None:
    assert us_state_zones()[code] == zone


@pytest.mark.parametrize("territory", ["PR", "GU", "VI", "AS", "MP"])
def test_territories_are_not_us_states(territory: str) -> None:
    assert territory not in us_state_zones()


def test_the_table_is_read_once_and_is_read_only() -> None:
    zones = us_state_zones()

    assert us_state_zones() is zones
    with pytest.raises(TypeError):
        zones["ZZ"] = "UTC"  # type: ignore[index]
    with pytest.raises(TypeError):
        COUNTRY_ZONES["FR"] = "Europe/Paris"  # type: ignore[index]
    assert dict(COUNTRY_ZONES) == {"GB": "Europe/London"}


MULTI_ZONE = ("AK", "AZ", "FL", "ID", "IN", "KS", "KY", "MI", "NE", "ND", "NV", "OR", "SD", "TN")


def test_the_vendored_file_lists_the_multi_zone_states_in_its_header() -> None:
    path = Path(geo.__file__).parent / "geodata" / "us_state_timezones.csv"
    comments = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#")
    ]

    for code in (*MULTI_ZONE, "TX"):
        [line] = [line for line in comments if line.startswith(f"#   {code} ")]
        assert us_state_zones()[code] in line  # the zone it takes, with the others named


# The parser --------------------------------------------------------------------------------------


def test_comments_and_blank_lines_are_skipped_anywhere() -> None:
    rows = full_rows()
    text = table(*rows[:10], "# a later comment", "   ", *rows[10:])

    assert dict(parse_us_state_zones(text)) == dict(us_state_zones())


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("", "has no header"),
        ("# only comments\n\n", "has no header"),
        (table(header="code,zone,name"), "line 3 must be the header"),
        (table(header="Code,Timezone,Name"), "line 3 must be the header"),
        (table(header=" code,timezone,name"), "line 3 must be the header"),
        (table("NY,America/New_York"), "line 4 must have three fields"),
        (table("NY,America/New_York,New York,extra"), "line 4 must have three fields"),
        (table("ny,America/New_York,New York"), "line 4 has a code that isn't 2 capitals"),
        (table("NYC,America/New_York,New York"), "line 4 has a code that isn't 2 capitals"),
        (table(" NY,America/New_York,New York"), "line 4 has a code that isn't 2 capitals"),
        (
            table("NY,America/New_York,New York", "NY,America/Chicago,Again"),
            "line 5 repeats a code",
        ),
        (table("NY,Mars/Olympus_Mons,New York"), "line 4 has an unknown timezone"),
        (table("NY,EST,New York"), "line 4 has an unknown timezone"),
        (table("NY,America/New_York,  "), "line 4 has a blank name"),
        (table("NY,America/New_York,"), "line 4 has a blank name"),
    ],
)
def test_a_malformed_table_raises_value_error_naming_the_line(text: str, says: str) -> None:
    with pytest.raises(ValueError, match=says):
        parse_us_state_zones(text)


@pytest.mark.parametrize("count", [50, 52])
def test_the_table_has_exactly_51_rows(count: int) -> None:
    rows = full_rows()
    if count == 50:
        rows = rows[:50]
    else:
        rows = [*rows, "ZZ,America/New_York,Extra"]

    with pytest.raises(ValueError, match=f"must have 51 rows, not {count}"):
        parse_us_state_zones(table(*rows))


def test_a_parser_error_never_repeats_the_line() -> None:
    with pytest.raises(ValueError) as caught:
        parse_us_state_zones(table("NY,Plantedzone/Secret,New York"))

    assert "Planted" not in str(caught.value)


def test_a_malformed_vendored_file_raises_on_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    geo.us_state_zones.cache_clear()
    geo._us_zones.cache_clear()
    monkeypatch.setattr(geo, "US_STATES_PATH", ("geodata", "missing.csv"))
    try:
        with pytest.raises(FileNotFoundError):
            us_state_zones()
    finally:
        monkeypatch.undo()
        geo.us_state_zones.cache_clear()
        geo._us_zones.cache_clear()
    assert len(us_state_zones()) == US_STATE_COUNT


# Location ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "region"),
    [("GB", None), ("US", None), ("US", "NY"), ("US", "DC"), ("CA", None), ("DE", None)],
)
def test_a_valid_location_keeps_its_fields(country: str, region: str | None) -> None:
    location = Location(country, region)

    assert (location.country_code, location.region) == (country, region)


@pytest.mark.parametrize(
    ("args", "field"),
    [
        (("gb",), "country_code"),
        (("UK",), "country_code"),  # the alias is normalised to GB: pass GB
        ((" GB",), "country_code"),
        (("ZZ",), "country_code"),
        ((1,), "country_code"),
        ((None,), "country_code"),
        (("CA", "ON"), "region"),
        (("GB", "NY"), "region"),
        (("US", "ZZ"), "region"),
        (("US", "ny"), "region"),
        (("US", "PR"), "region"),  # a territory is its own country
        (("US", 5), "region"),
    ],
)
def test_a_bad_location_raises_invalid_location_naming_the_field(
    args: tuple[object, ...], field: str
) -> None:
    with pytest.raises(InvalidLocation) as caught:
        Location(*args)  # type: ignore[arg-type]

    error = caught.value
    assert isinstance(error, DomainError)
    assert (error.code, error.field) == ("invalid_location", field)
    assert str(error).startswith(f"{field}: ")


def test_a_location_refusal_never_shows_the_value() -> None:
    for args in (("Q9",), ("US", "Q9")):
        with pytest.raises(InvalidLocation) as caught:
            Location(*args)
        assert "Q9" not in str(caught.value)


def test_a_location_is_frozen_and_slotted() -> None:
    location = Location("US", "TX")

    with pytest.raises(dataclasses.FrozenInstanceError):
        location.region = "NY"  # type: ignore[misc]
    assert not hasattr(location, "__dict__")


# location_zone and zone_country -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "region", "zone"),
    [
        ("GB", None, "Europe/London"),
        ("US", "NY", "America/New_York"),
        ("US", "TX", "America/Chicago"),
        ("US", "HI", "Pacific/Honolulu"),
        ("US", None, None),
        ("US", "ZZ", None),
        ("CA", None, None),
        ("FR", None, None),
    ],
)
def test_location_zone(country: str, region: str | None, zone: str | None) -> None:
    assert location_zone(country, region) == zone


@pytest.mark.parametrize(
    ("zone", "country"),
    [
        ("Europe/London", "GB"),
        ("America/New_York", "US"),
        ("America/Denver", "US"),
        ("America/Indiana/Indianapolis", "US"),
        ("Pacific/Honolulu", "US"),
        ("America/Toronto", None),
        ("Europe/Paris", None),
        ("UTC", None),
        ("", None),
    ],
)
def test_zone_country(zone: str, country: str | None) -> None:
    assert zone_country(zone) == country
