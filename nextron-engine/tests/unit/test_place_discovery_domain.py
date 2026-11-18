"""``nextron.domain.place_discovery`` (Story 8.3): the site's name, the location items and
their hash, the query hash, and the job key's 16-hex discriminator."""

import hashlib

import pytest

from nextron.domain.job import InvalidJob, idempotency_key
from nextron.domain.leads import MAX_NAME_LENGTH
from nextron.domain.place_discovery import (
    COUNT_FIELDS,
    DiscoveryLocation,
    Outcome,
    RunStatus,
    is_location_item,
    location_hash,
    parse_location,
    query_hash,
    site_name,
)

CAMPAIGN = "01JD3QZ8X5V9K2M7N4P6R8T0D0"


# site_name --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "name"),
    [
        ("<title>Acme Law</title>", "Acme Law"),
        ("<head><title>\n  Acme \t  Law\n</title></head>", "Acme Law"),
        (
            '<meta property="og:site_name" content="Acme Law LLP"><title>Home | Acme</title>',
            "Acme Law LLP",
        ),
        ('<META PROPERTY="OG:SITE_NAME" CONTENT=" Acme  LLP ">', "Acme LLP"),
        ('<meta name="og:site_name" content="Named Acme">', "Named Acme"),
        ('<meta property="og:site_name" content="   "><title>Fallback</title>', "Fallback"),
        ('<meta property="og:site_name" content=""><title>Fallback</title>', "Fallback"),
        (
            '<meta property="og:site_name" content="  "><meta property="og:site_name" '
            'content="Second Site"><title>Title</title>',
            "Second Site",
        ),
        ("<title>Smith &amp; Jones</title>", "Smith & Jones"),
        ("<title>A​cme\u0007 Law </title>", "Acme Law"),
        ("<title>First</title><title>Second</title>", "First"),
        ("<title>Unclosed title", "Unclosed title"),
        ("<svg><title>icon</title></svg><title>Page</title>", "icon"),
        ("<p>No title</p>", None),
        ("<title>   </title>", None),
        ("", None),
    ],
)
def test_site_name_reads_og_site_name_then_the_title(html: str, name: str | None) -> None:
    assert site_name(html) == name


def test_site_name_is_cut_to_the_lead_name_length_and_stripped() -> None:
    long = "A" * (MAX_NAME_LENGTH - 1) + " " + "B" * 50

    found = site_name(f"<title>{long}</title>")

    assert found == "A" * (MAX_NAME_LENGTH - 1)


def test_site_name_takes_html_text_only() -> None:
    with pytest.raises(TypeError):
        site_name(b"<title>x</title>")  # type: ignore[arg-type]


# Locations --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "location", "code"),
    [
        ("Columbus, OH | US", "Columbus, OH", "US"),
        ("Leeds|GB", "Leeds", "GB"),
        ("  Greater Manchester  |  GB ", "Greater Manchester", "GB"),
        ("L" * 100 + " | US", "L" * 100, "US"),
    ],
)
def test_a_location_item_parses(item: str, location: str, code: str) -> None:
    assert parse_location(item) == DiscoveryLocation(location, code)
    assert is_location_item(item)


@pytest.mark.parametrize(
    "item",
    [
        "Columbus, OH",
        "Columbus, OH | us",
        "Columbus, OH | USA",
        "Columbus, OH | XX",
        " | US",
        "L" * 101 + " | US",
        "Columbus | OH | US",
        "Colum​bus | US",
        "Columbus\t| US\u0000",
        "",
    ],
)
def test_a_bad_location_item_is_refused(item: str) -> None:
    with pytest.raises(ValueError):
        parse_location(item)
    assert not is_location_item(item)


def test_location_text_round_trips() -> None:
    location = parse_location("Columbus, OH | US")

    assert parse_location(location.text) == location


def test_the_location_hash_is_16_hex_of_the_sha256_of_location_and_code() -> None:
    expected = hashlib.sha256(b"Columbus, OH|US").hexdigest()[:16]

    assert location_hash("Columbus, OH", "US") == expected
    assert DiscoveryLocation("Columbus, OH", "US").hash == expected
    assert location_hash("Columbus, OH", "GB") != expected


def test_the_query_hash_joins_the_queries_by_newline() -> None:
    expected = hashlib.sha256(b"law firm\nsolicitors").hexdigest()[:16]

    assert query_hash(("law firm", "solicitors")) == expected
    assert query_hash(("solicitors", "law firm")) != expected
    assert len(query_hash(())) == 16


# The job key ------------------------------------------------------------------------------------


def test_a_location_hash_is_a_job_key_discriminator() -> None:
    hashed = location_hash("Columbus, OH", "US")

    assert idempotency_key("discover_places", CAMPAIGN, hashed) == (
        f"discover_places:{CAMPAIGN}:{hashed}"
    )


@pytest.mark.parametrize(
    "discriminator",
    ["0123456789abcde", "0123456789abcdef0", "0123456789ABCDEF", "0123456789abcdeg"],
)
def test_only_exactly_16_lowercase_hex_is_a_hash_discriminator(discriminator: str) -> None:
    with pytest.raises(InvalidJob):
        idempotency_key("discover_places", CAMPAIGN, discriminator)


def test_the_counts_are_seen_then_each_outcome() -> None:
    assert COUNT_FIELDS == (
        "seen",
        "created",
        "merged",
        "suppressed",
        "no_website",
        "market_blocked",
    )
    assert {str(outcome) for outcome in Outcome} == set(COUNT_FIELDS[1:])
    assert [str(status) for status in RunStatus] == [
        "running",
        "finished",
        "deferred",
        "failed",
        "stopped",
    ]
