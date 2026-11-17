"""The shipped seed file ``seed/markets.yaml`` (FR-2, Story 3.1).

It validates into exactly the spec's Design Notes content, field for field: the US and UK
Markets, the UK's eight Companies House company types, the US's narrowed Send Window and both
footer templates.
"""

import dataclasses
from datetime import time

import pytest

from nextron.domain import seed_yaml
from nextron.domain.markets import (
    EnglishVariant,
    MarketDef,
    SendWindow,
    market_for_country,
    market_form,
    market_from_form,
    validate_markets,
)
from nextron.main import MARKETS_FILE_LABEL, MARKETS_FILE_NAME, SEED_DIR
from tests.contracts.support import ENGINE_ROOT

SEED_FILE = ENGINE_ROOT / "seed" / "markets.yaml"

US = MarketDef(
    code="us",
    name="United States",
    enabled=True,
    country_code="US",
    cctlds=("us",),
    cold_email_allowed=True,
    eligible_legal_forms=None,
    free_webmail_blocked=False,
    default_timezone="America/New_York",
    unknown_timezone="America/New_York",
    unknown_send_window=SendWindow(time(11, 0), time(17, 0)),
    english_variant=EnglishVariant.EN_US,
    footer_template=(
        "{sender_signature}",
        "{legal_name}",
        "{postal_address}",
        "{optout_line}",
        "{us_solicitation_label}",
    ),
    privacy_line_required=False,
)
UK = MarketDef(
    code="uk",
    name="United Kingdom",
    enabled=True,
    country_code="GB",
    cctlds=("uk",),
    cold_email_allowed=True,
    eligible_legal_forms=(
        "ltd",
        "plc",
        "private-limited-guarant-nsc",
        "private-limited-guarant-nsc-limited-exemption",
        "private-limited-shares-section-30-exemption",
        "private-unlimited",
        "private-unlimited-nsc",
        "llp",
    ),
    free_webmail_blocked=True,
    default_timezone="Europe/London",
    unknown_timezone="Europe/London",
    unknown_send_window=None,
    english_variant=EnglishVariant.EN_GB,
    footer_template=(
        "{sender_signature}",
        "{legal_name}",
        "{postal_address}",
        "{optout_line}",
        "{privacy_line}",
    ),
    privacy_line_required=True,
)


def shipped() -> tuple[MarketDef, ...]:
    return validate_markets(seed_yaml.parse(SEED_FILE.read_text(encoding="utf-8")))


def test_the_command_reads_this_file() -> None:
    assert SEED_DIR / MARKETS_FILE_NAME == SEED_FILE
    assert MARKETS_FILE_LABEL == "seed/markets.yaml"


def test_the_file_holds_the_us_then_the_uk() -> None:
    assert [market.code for market in shipped()] == ["us", "uk"]


@pytest.mark.parametrize("field", [field.name for field in dataclasses.fields(MarketDef)])
def test_each_field_is_the_design_notes_value(field: str) -> None:
    us, uk = shipped()

    assert getattr(us, field) == getattr(US, field)
    assert getattr(uk, field) == getattr(UK, field)


def test_the_file_is_exactly_the_design_notes_markets() -> None:
    assert shipped() == (US, UK)


def test_only_the_us_and_uk_are_enabled_for_cold_email() -> None:
    markets = shipped()

    assert market_for_country("US", markets) == US
    assert market_for_country("GB", markets) == UK
    assert market_for_country("CA", markets) is None
    assert all(market.enabled and market.cold_email_allowed for market in markets)


def test_the_shipped_markets_round_trip_through_the_file_form() -> None:
    for market in shipped():
        assert market_from_form(market_form(market)) == market


def test_the_file_starts_with_a_header_comment() -> None:
    text = SEED_FILE.read_text(encoding="utf-8")

    header = text.split("\nmarkets:", 1)[0]
    assert all(line.startswith("#") or not line for line in header.splitlines())
    assert "nextron seed sync" in header
    assert "subset of YAML" in header
