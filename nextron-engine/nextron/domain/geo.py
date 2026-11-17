"""Geography for Contact Markets and timezones (FR-2, AD-11; Story 3.2). Standard library and
``nextron.domain`` only.

**The vendored US state table.** ``geodata/us_state_timezones.csv`` maps each of the 50 states
and the District of Columbia to the IANA timezone where most of its people live (its header
comment lists the states that span more than one zone; territories have their own ISO codes, so
they aren't US states). It is read once, through ``importlib.resources`` as
``identity.py`` reads the Public Suffix List, and its SHA-256 is pinned in
``tests/contracts/test_vendored_assets.py``. ``parse_us_state_zones(text)`` reads the format:

- lines starting with ``#`` and blank lines are skipped;
- the first other line is exactly ``code,timezone,name``;
- then exactly ``US_STATE_COUNT`` (51) rows of three comma-separated fields: a ``code`` that
  fullmatches ``[A-Z]{2}`` and is unique, a ``timezone`` in ``settings_schema.timezone_names()``,
  and a non-blank ``name``.

A malformed table raises ``ValueError`` (naming the line number, never repeating its text) on
first use.

- ``us_state_zones() -> Mapping[str, str]``: the table, code to zone, read-only.
- ``COUNTRY_ZONES``: the countries with one zone, read-only: ``{"GB": "Europe/London"}``.
- ``location_zone(country_code, region)``: ``GB`` gives ``Europe/London``; ``US`` with a
  region gives that state's zone (``None`` for a region not in the table); anything else gives
  ``None``, ``US`` without a region included.
- ``zone_country(zone)``: ``GB`` for ``Europe/London``, ``US`` for any zone in the US table,
  otherwise ``None``.

**``Location(country_code, region=None)``** is a place a Contact's office is in. It is frozen
and slotted and checks itself when built: ``country_code`` is a ``str`` equal to
``normalize_country_code(country_code)`` (so ``gb`` and ``UK`` are refused: pass ``GB``), and
``region`` is ``None``, or, only when the country is ``US``, a code of the US table. A problem
raises ``InvalidLocation(field, reason)`` (code ``invalid_location``, message
``"<field>: <reason>"``), never showing the value.
"""

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.identity import IdentityError, normalize_country_code
from nextron.domain.settings_schema import timezone_names

US_STATES_PATH: Final = ("geodata", "us_state_timezones.csv")
US_STATES_HEADER: Final = "code,timezone,name"
US_STATE_COUNT: Final = 51  # the 50 states and the District of Columbia
STATE_CODE: Final = re.compile(r"[A-Z]{2}")  # matched with fullmatch
US: Final = "US"
GB: Final = "GB"
COUNTRY_ZONES: Final[Mapping[str, str]] = MappingProxyType({GB: "Europe/London"})


class InvalidLocation(DomainError):
    """A ``Location`` field that breaks its rule; the message names the field, never its
    value."""

    code = "invalid_location"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def parse_us_state_zones(text: str) -> Mapping[str, str]:
    """The US state table in ``text``, code to zone, read-only; a malformed table raises
    ``ValueError``. See the module docstring."""
    zones: dict[str, str] = {}
    header_seen = False
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        if not header_seen:
            if line != US_STATES_HEADER:
                raise ValueError(
                    f"the US state table's line {number} must be the header {US_STATES_HEADER}"
                )
            header_seen = True
            continue
        fields = line.split(",")
        if len(fields) != 3:
            raise ValueError(f"the US state table's line {number} must have three fields")
        code, zone, name = fields
        if STATE_CODE.fullmatch(code) is None:
            raise ValueError(f"the US state table's line {number} has a code that isn't 2 capitals")
        if code in zones:
            raise ValueError(f"the US state table's line {number} repeats a code")
        if zone not in timezone_names():
            raise ValueError(f"the US state table's line {number} has an unknown timezone")
        if not name.strip():
            raise ValueError(f"the US state table's line {number} has a blank name")
        zones[code] = zone
    if not header_seen:
        raise ValueError("the US state table has no header")
    if len(zones) != US_STATE_COUNT:
        raise ValueError(f"the US state table must have {US_STATE_COUNT} rows, not {len(zones)}")
    return MappingProxyType(zones)


@functools.cache
def us_state_zones() -> Mapping[str, str]:
    """The vendored US state table, code to zone, read once; see the module docstring."""
    text = resources.files(__package__).joinpath(*US_STATES_PATH).read_text(encoding="utf-8")
    return parse_us_state_zones(text)


@functools.cache
def _us_zones() -> frozenset[str]:
    return frozenset(us_state_zones().values())


def location_zone(country_code: str, region: str | None) -> str | None:
    """The zone of a place: ``GB`` is ``Europe/London``, a US state its table zone, anything
    else ``None``."""
    zone = COUNTRY_ZONES.get(country_code)
    if zone is not None:
        return zone
    if country_code == US and region is not None:
        return us_state_zones().get(region)
    return None


def zone_country(zone: str) -> str | None:
    """The country a zone belongs to: ``GB`` for ``Europe/London``, ``US`` for a zone of the US
    table, otherwise ``None``."""
    for country, country_zone in COUNTRY_ZONES.items():
        if zone == country_zone:
            return country
    if zone in _us_zones():
        return US
    return None


@dataclass(frozen=True, slots=True)
class Location:
    """A place a Contact's office is in: a country and, in the US, a state; see the module
    docstring."""

    country_code: str
    region: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.country_code, str):
            raise InvalidLocation("country_code", "must be a str")
        try:
            normalised = normalize_country_code(self.country_code)
        except IdentityError:
            raise InvalidLocation(
                "country_code", "must be an ISO 3166-1 alpha-2 country code"
            ) from None
        if normalised != self.country_code:
            raise InvalidLocation("country_code", "is not in normalised form")
        if self.region is None:
            return
        if not isinstance(self.region, str):
            raise InvalidLocation("region", "must be a str or None")
        if self.country_code != US:
            raise InvalidLocation("region", "is set only for a US location")
        if self.region not in us_state_zones():
            raise InvalidLocation("region", "must be a US state code (or DC)")
