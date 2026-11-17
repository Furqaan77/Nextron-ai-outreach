"""``nextron.domain.markets`` (FR-2, NFR-2, NFR-5, NFR-40; Story 3.1): the Market types, the
file validation with its exact field paths, the compliance floor, the file form round trip,
``market_for_country``, ``cctld_country``, ``is_free_webmail`` and ``render_footer_template``.
Standard library, pytest and ``nextron.domain`` only."""

import dataclasses
from datetime import time
from typing import Any

import pytest

from nextron.domain.markets import (
    FREE_WEBMAIL_DOMAINS,
    EnglishVariant,
    FooterPlaceholder,
    InvalidMarkets,
    MarketDef,
    SendWindow,
    cctld_country,
    is_free_webmail,
    market_for_country,
    market_form,
    market_from_form,
    placeholders,
    render_footer_template,
    send_window_text,
    validate_markets,
)
from nextron.domain.settings_schema import TIMEZONE_REASON

US_TEMPLATE = [
    "{sender_signature}",
    "{legal_name}",
    "{postal_address}",
    "{optout_line}",
    "{us_solicitation_label}",
]
UK_TEMPLATE = [
    "{sender_signature}",
    "{legal_name}",
    "{postal_address}",
    "{optout_line}",
    "{privacy_line}",
]
CA_TEMPLATE = ["{sender_signature}", "{legal_name}", "{postal_address}", "{optout_line}"]
UK_FORMS = [
    "ltd",
    "plc",
    "private-limited-guarant-nsc",
    "private-limited-guarant-nsc-limited-exemption",
    "private-limited-shares-section-30-exemption",
    "private-unlimited",
    "private-unlimited-nsc",
    "llp",
]
LONG = "x" * 41  # a value a reason must never repeat


def us_form(**changes: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "code": "us",
        "name": "United States",
        "enabled": True,
        "country_code": "US",
        "cctlds": ["us"],
        "cold_email_allowed": True,
        "eligible_legal_forms": "not_checked",
        "free_webmail_blocked": False,
        "default_timezone": "America/New_York",
        "unknown_timezone": "America/New_York",
        "unknown_send_window": "11:00-17:00",
        "english_variant": "en_us",
        "footer_template": list(US_TEMPLATE),
        "privacy_line_required": False,
    }
    form.update(changes)
    return form


def uk_form(**changes: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "code": "uk",
        "name": "United Kingdom",
        "enabled": True,
        "country_code": "GB",
        "cctlds": ["uk"],
        "cold_email_allowed": True,
        "eligible_legal_forms": list(UK_FORMS),
        "free_webmail_blocked": True,
        "default_timezone": "Europe/London",
        "unknown_timezone": "Europe/London",
        "english_variant": "en_gb",
        "footer_template": list(UK_TEMPLATE),
        "privacy_line_required": True,
    }
    form.update(changes)
    return form


def ca_form(**changes: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "code": "ca",
        "name": "Canada",
        "enabled": False,
        "country_code": "CA",
        "cctlds": ["ca"],
        "cold_email_allowed": False,
        "eligible_legal_forms": "not_checked",
        "free_webmail_blocked": False,
        "default_timezone": "America/Toronto",
        "unknown_timezone": "America/Toronto",
        "english_variant": "en_us",
        "footer_template": list(CA_TEMPLATE),
        "privacy_line_required": False,
    }
    form.update(changes)
    return form


def without(form: dict[str, Any], name: str) -> dict[str, Any]:
    return {key: value for key, value in form.items() if key != name}


def file_of(*forms: dict[str, Any]) -> dict[str, Any]:
    return {"markets": list(forms)}


def problem(data: object) -> tuple[str, str]:
    with pytest.raises(InvalidMarkets) as caught:
        validate_markets(data)
    assert caught.value.code == "invalid_markets"
    assert caught.value.message == f"{caught.value.field}: {caught.value.reason}"
    return caught.value.field, caught.value.reason


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
    footer_template=tuple(US_TEMPLATE),
    privacy_line_required=False,
)
UK = MarketDef(
    code="uk",
    name="United Kingdom",
    enabled=True,
    country_code="GB",
    cctlds=("uk",),
    cold_email_allowed=True,
    eligible_legal_forms=tuple(UK_FORMS),
    free_webmail_blocked=True,
    default_timezone="Europe/London",
    unknown_timezone="Europe/London",
    unknown_send_window=None,
    english_variant=EnglishVariant.EN_GB,
    footer_template=tuple(UK_TEMPLATE),
    privacy_line_required=True,
)


# The types ---------------------------------------------------------------------------------------


def test_the_enums_hold_exactly_the_specified_members() -> None:
    assert [member.value for member in EnglishVariant] == ["en_us", "en_gb"]
    assert [member.value for member in FooterPlaceholder] == [
        "sender_signature",
        "legal_name",
        "postal_address",
        "optout_line",
        "privacy_line",
        "us_solicitation_label",
    ]


def test_the_market_types_are_frozen_and_slotted() -> None:
    window = SendWindow(time(9, 0), time(17, 0))

    assert not hasattr(US, "__dict__") and not hasattr(window, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        US.enabled = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        window.start = time(1, 0)  # type: ignore[misc]
    assert [field.name for field in dataclasses.fields(MarketDef)] == [
        "code",
        "name",
        "enabled",
        "country_code",
        "cctlds",
        "cold_email_allowed",
        "eligible_legal_forms",
        "free_webmail_blocked",
        "default_timezone",
        "unknown_timezone",
        "unknown_send_window",
        "english_variant",
        "footer_template",
        "privacy_line_required",
    ]


# Validation: the happy path ----------------------------------------------------------------------


def test_a_valid_file_gives_its_markets_in_file_order() -> None:
    assert validate_markets(file_of(us_form(), uk_form())) == (US, UK)
    assert validate_markets(file_of(uk_form(), us_form())) == (UK, US)


def test_unknown_send_window_is_optional() -> None:
    [market] = validate_markets(file_of(without(us_form(), "unknown_send_window")))

    assert market.unknown_send_window is None


def test_a_disabled_ca_market_with_every_field_loads() -> None:
    [ca] = validate_markets(file_of(ca_form()))

    assert (ca.code, ca.country_code, ca.enabled) == ("ca", "CA", False)
    assert (ca.cold_email_allowed, ca.eligible_legal_forms) == (False, None)


def test_fifty_markets_are_the_most() -> None:
    letters = "abcdefghijklmnopqrstuvwxyz"
    forms = [
        ca_form(code=f"m{index}", country_code=code.upper(), cctlds=[code])
        for index, code in enumerate(f"{a}{b}" for a in "pq" for b in letters)
    ][:50]

    assert len(validate_markets(file_of(*forms))) == 50
    assert problem(file_of(*forms, ca_form(code="extra"))) == (
        "markets",
        "must hold at most 50 items",
    )


# Validation: the file shape ----------------------------------------------------------------------

SHAPE = [
    ([], "(top level)", "must be a mapping with exactly one field, markets"),
    ({}, "markets", "missing"),
    ({"markets": [us_form()], "extra": 1}, "extra", "unknown field"),
    ({"Not A Name": 1}, "(top level)", "has an unknown field"),
    ({"markets": "us"}, "markets", "must be a list"),
    ({"markets": []}, "markets", "must hold at least 1 item"),
    ({"markets": ["us"]}, "markets[0]", "must be a mapping"),
    (file_of(us_form(extra=1)), "markets[us].extra", "unknown field"),
    (file_of({**us_form(), 7: "x"}), "markets[us]", "has an unknown field"),
    (file_of({**us_form(), LONG: 1}), "markets[us]", "has an unknown field"),
]


@pytest.mark.parametrize(("data", "field", "reason"), SHAPE)
def test_the_file_shape_is_exact(data: object, field: str, reason: str) -> None:
    assert problem(data) == (field, reason)


@pytest.mark.parametrize(
    "name",
    [
        "code",
        "name",
        "enabled",
        "country_code",
        "cctlds",
        "cold_email_allowed",
        "eligible_legal_forms",
        "free_webmail_blocked",
        "default_timezone",
        "unknown_timezone",
        "english_variant",
        "footer_template",
        "privacy_line_required",
    ],
)
def test_every_field_but_the_send_window_is_required(name: str) -> None:
    shown = "us" if name != "code" else "0"

    assert problem(file_of(without(us_form(), name))) == (f"markets[{shown}].{name}", "missing")


# Validation: the value rules ---------------------------------------------------------------------

CODE_REASON = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 16 "
    "characters"
)
COUNTRY_REASON = "must be a two-letter country code in capitals, such as US or GB"
CCTLD_REASON = "must be two lowercase letters, a country-code top-level domain such as us"
LEGAL_FORM_REASON = (
    "must be lowercase letters, digits and hyphens, starting with a letter, at most 64 characters"
)
WINDOW_REASON = "must be HH:MM-HH:MM, such as 11:00-17:00, with times from 00:00 to 23:59"

VALUES = [
    # identifiers
    (file_of(us_form(code="US")), "markets[0].code", CODE_REASON),
    (file_of(us_form(code="1us")), "markets[0].code", CODE_REASON),
    (file_of(us_form(code="a" * 17)), "markets[0].code", CODE_REASON),
    (file_of(us_form(code=5)), "markets[0].code", CODE_REASON),
    (
        file_of(us_form(), uk_form(code="us")),
        "markets[1].code",
        "repeats an earlier Market code (us)",
    ),
    (file_of(us_form(country_code="us")), "markets[us].country_code", COUNTRY_REASON),
    (file_of(us_form(country_code="USA")), "markets[us].country_code", COUNTRY_REASON),
    (file_of(us_form(country_code=None)), "markets[us].country_code", COUNTRY_REASON),
    (
        file_of(us_form(), us_form(code="us2", cctlds=["um"])),
        "markets[us2].country_code",
        "repeats the country code of an earlier Market (US)",
    ),
    (file_of(us_form(cctlds="us")), "markets[us].cctlds", "must be a list"),
    (file_of(us_form(cctlds=[])), "markets[us].cctlds", "must hold at least 1 item"),
    (
        file_of(us_form(cctlds=["us"] * 11)),
        "markets[us].cctlds",
        "must hold at most 10 items",
    ),
    (file_of(us_form(cctlds=["usa"])), "markets[us].cctlds[0]", CCTLD_REASON),
    (file_of(us_form(cctlds=["US"])), "markets[us].cctlds[0]", CCTLD_REASON),
    (file_of(us_form(cctlds=[".us"])), "markets[us].cctlds[0]", CCTLD_REASON),
    (file_of(us_form(cctlds=["us", "us"])), "markets[us].cctlds[1]", "repeats an earlier item"),
    (
        file_of(uk_form(), us_form(cctlds=["us", "uk"])),
        "markets[us].cctlds[1]",
        "is also a ccTLD of an earlier Market (uk)",
    ),
    # texts
    (file_of(us_form(name="")), "markets[us].name", "must not be blank"),
    (file_of(us_form(name="   ")), "markets[us].name", "must not be blank"),
    (file_of(us_form(name=" US")), "markets[us].name", "must not start or end with whitespace"),
    (
        file_of(us_form(name="United​States")),
        "markets[us].name",
        "must not hold control, format or line-separator characters",
    ),
    (file_of(us_form(name="x" * 101)), "markets[us].name", "must be at most 100 characters"),
    (file_of(us_form(name=5)), "markets[us].name", "must be text"),
    # booleans
    (file_of(us_form(enabled="true")), "markets[us].enabled", "must be true or false"),
    (file_of(us_form(enabled=1)), "markets[us].enabled", "must be true or false"),
    (
        file_of(us_form(cold_email_allowed=None)),
        "markets[us].cold_email_allowed",
        "must be true or false",
    ),
    (
        file_of(us_form(free_webmail_blocked="no")),
        "markets[us].free_webmail_blocked",
        "must be true or false",
    ),
    (
        file_of(us_form(privacy_line_required=0)),
        "markets[us].privacy_line_required",
        "must be true or false",
    ),
    # legal forms
    (
        file_of(us_form(eligible_legal_forms=[])),
        "markets[us].eligible_legal_forms",
        "must hold at least 1 item (write not_checked when legal forms aren't checked)",
    ),
    (
        file_of(us_form(eligible_legal_forms="anything")),
        "markets[us].eligible_legal_forms",
        "must be not_checked or a list of legal forms",
    ),
    (
        file_of(us_form(eligible_legal_forms=None)),
        "markets[us].eligible_legal_forms",
        "must be not_checked or a list of legal forms",
    ),
    (
        file_of(us_form(eligible_legal_forms=["Ltd"])),
        "markets[us].eligible_legal_forms[0]",
        LEGAL_FORM_REASON,
    ),
    (
        file_of(us_form(eligible_legal_forms=["ltd", "-x"])),
        "markets[us].eligible_legal_forms[1]",
        LEGAL_FORM_REASON,
    ),
    (
        file_of(us_form(eligible_legal_forms=["l" * 65])),
        "markets[us].eligible_legal_forms[0]",
        LEGAL_FORM_REASON,
    ),
    (
        file_of(us_form(eligible_legal_forms=["ltd", "ltd"])),
        "markets[us].eligible_legal_forms[1]",
        "repeats an earlier item",
    ),
    (
        file_of(us_form(eligible_legal_forms=[f"f{n}" for n in range(51)])),
        "markets[us].eligible_legal_forms",
        "must hold at most 50 items",
    ),
    # timezones
    (file_of(us_form(default_timezone="EST")), "markets[us].default_timezone", TIMEZONE_REASON),
    (
        file_of(us_form(default_timezone="america/new_york")),
        "markets[us].default_timezone",
        TIMEZONE_REASON,
    ),
    (
        file_of(us_form(unknown_timezone="Etc/GMT+5")),
        "markets[us].unknown_timezone",
        TIMEZONE_REASON,
    ),
    (file_of(us_form(unknown_timezone=5)), "markets[us].unknown_timezone", TIMEZONE_REASON),
    # the send window
    (
        file_of(us_form(unknown_send_window="11:00-11:00")),
        "markets[us].unknown_send_window",
        "must start before it ends",
    ),
    (
        file_of(us_form(unknown_send_window="17:00-11:00")),
        "markets[us].unknown_send_window",
        "must start before it ends",
    ),
    (
        file_of(us_form(unknown_send_window="24:00-24:30")),
        "markets[us].unknown_send_window",
        WINDOW_REASON,
    ),
    (
        file_of(us_form(unknown_send_window="11:60-12:00")),
        "markets[us].unknown_send_window",
        WINDOW_REASON,
    ),
    (
        file_of(us_form(unknown_send_window="9:00-17:00")),
        "markets[us].unknown_send_window",
        WINDOW_REASON,
    ),
    (
        file_of(us_form(unknown_send_window="11:00 - 17:00")),
        "markets[us].unknown_send_window",
        WINDOW_REASON,
    ),
    (
        file_of(us_form(unknown_send_window=1100)),
        "markets[us].unknown_send_window",
        WINDOW_REASON,
    ),
    # the English variant
    (
        file_of(us_form(english_variant="en_au")),
        "markets[us].english_variant",
        "must be one of en_us, en_gb",
    ),
]


@pytest.mark.parametrize(("data", "field", "reason"), VALUES)
def test_each_value_rule_names_its_field(data: object, field: str, reason: str) -> None:
    assert problem(data) == (field, reason)


def test_send_window_edges_are_accepted() -> None:
    [market] = validate_markets(file_of(us_form(unknown_send_window="00:00-23:59")))

    assert market.unknown_send_window == SendWindow(time(0, 0), time(23, 59))
    assert send_window_text(market.unknown_send_window) == "00:00-23:59"


# Validation: the footer template -----------------------------------------------------------------

UNKNOWN_PLACEHOLDER = (
    "holds an unknown placeholder: the placeholders are {sender_signature}, {legal_name}, "
    "{postal_address}, {optout_line}, {privacy_line}, {us_solicitation_label}"
)
STRAY_BRACE = "holds a { or } that isn't part of a placeholder such as {legal_name}"


def us_template(*lines: str) -> dict[str, Any]:
    return file_of(us_form(footer_template=list(lines)))


FOOTER = [
    (
        file_of(us_form(footer_template=[])),
        "markets[us].footer_template",
        "must hold at least 1 item",
    ),
    (
        us_template(*US_TEMPLATE, *(["Nextron AI"] * 16)),
        "markets[us].footer_template",
        "must hold at most 20 items",
    ),
    (us_template("", *US_TEMPLATE), "markets[us].footer_template[0]", "must not be blank"),
    (
        us_template("x" * 201, *US_TEMPLATE),
        "markets[us].footer_template[0]",
        "must be at most 200 characters",
    ),
    (
        file_of(us_form(footer_template=[5, *US_TEMPLATE])),
        "markets[us].footer_template[0]",
        "must be text",
    ),
    (
        us_template("Ends\nhere", *US_TEMPLATE),
        "markets[us].footer_template[0]",
        "must not hold control, format or line-separator characters",
    ),
    (
        us_template(*US_TEMPLATE, "{unknown}"),
        "markets[us].footer_template[5]",
        UNKNOWN_PLACEHOLDER,
    ),
    (
        us_template(*US_TEMPLATE, "{Legal_Name}"),
        "markets[us].footer_template[5]",
        UNKNOWN_PLACEHOLDER,
    ),
    (
        us_template(*US_TEMPLATE, "{ legal_name }"),
        "markets[us].footer_template[5]",
        UNKNOWN_PLACEHOLDER,
    ),
    (
        us_template(*US_TEMPLATE, "Also {legal_name}"),
        "markets[us].footer_template[5]",
        "repeats the placeholder {legal_name}: each may appear only once",
    ),
    (
        us_template("{legal_name} {legal_name}", *US_TEMPLATE[2:]),
        "markets[us].footer_template[0]",
        "repeats the placeholder {legal_name}: each may appear only once",
    ),
    (us_template(*US_TEMPLATE, "Open { brace"), "markets[us].footer_template[5]", STRAY_BRACE),
    (us_template(*US_TEMPLATE, "Close } brace"), "markets[us].footer_template[5]", STRAY_BRACE),
    (us_template(*US_TEMPLATE, "Empty {}"), "markets[us].footer_template[5]", UNKNOWN_PLACEHOLDER),
    (
        us_template("{{sender_signature}}", *US_TEMPLATE[1:]),
        "markets[us].footer_template[0]",
        STRAY_BRACE,
    ),
    (
        us_template(*US_TEMPLATE[:4], "Label: {us_solicitation_label}"),
        "markets[us].footer_template[4]",
        "{us_solicitation_label} must stand alone on its line",
    ),
    (
        file_of(uk_form(footer_template=[*UK_TEMPLATE[:4], "Privacy: {privacy_line}"])),
        "markets[uk].footer_template[4]",
        "{privacy_line} must stand alone on its line",
    ),
    # the template rules
    *[
        (
            us_template(*[line for line in US_TEMPLATE if line != f"{{{name}}}"]),
            "markets[us].footer_template",
            f"must hold {{{name}}} (FR-23)",
        )
        for name in ("sender_signature", "legal_name", "postal_address", "optout_line")
    ],
    (
        us_template(*US_TEMPLATE[:4]),
        "markets[us].footer_template",
        "a US Market's template must hold {us_solicitation_label} (NFR-2)",
    ),
    (
        file_of(ca_form(footer_template=[*CA_TEMPLATE, "{us_solicitation_label}"])),
        "markets[ca].footer_template",
        "only a US Market's template may hold {us_solicitation_label}",
    ),
    (
        file_of(uk_form(footer_template=[*UK_TEMPLATE, "{us_solicitation_label}"])),
        "markets[uk].footer_template",
        "only a US Market's template may hold {us_solicitation_label}",
    ),
    (
        file_of(uk_form(footer_template=UK_TEMPLATE[:4])),
        "markets[uk].footer_template",
        "must hold {privacy_line}, because privacy_line_required is true",
    ),
    (
        us_template(*US_TEMPLATE, "{privacy_line}"),
        "markets[us].footer_template",
        "must not hold {privacy_line}, because privacy_line_required is false",
    ),
]


@pytest.mark.parametrize(("data", "field", "reason"), FOOTER)
def test_each_footer_rule_names_its_field(data: object, field: str, reason: str) -> None:
    assert problem(data) == (field, reason)


def test_a_footer_may_hold_literal_text_and_placeholders_in_any_order() -> None:
    lines = [
        "Best regards,",
        "{sender_signature}",
        "{legal_name}, {postal_address}",
        "{us_solicitation_label}",
        "To stop these emails: {optout_line}",
    ]

    [market] = validate_markets(us_template(*lines))

    assert market.footer_template == tuple(lines)
    assert placeholders(market.footer_template) == {
        "sender_signature",
        "legal_name",
        "postal_address",
        "us_solicitation_label",
        "optout_line",
    }


# Validation: the compliance floor ----------------------------------------------------------------

GB_FORM_REASON = "isn't a corporate-subscriber company type a GB Market may list (PECR)"

FLOOR = [
    (
        file_of(ca_form(cold_email_allowed=True)),
        "markets[ca].cold_email_allowed",
        "may be true only for a US or GB Market (NFR-5)",
    ),
    (
        file_of(ca_form(cold_email_allowed=True, enabled=True)),
        "markets[ca].cold_email_allowed",
        "may be true only for a US or GB Market (NFR-5)",
    ),
    (
        file_of(uk_form(free_webmail_blocked=False)),
        "markets[uk].free_webmail_blocked",
        "must be true for a GB Market (PECR: corporate subscribers only)",
    ),
    (
        file_of(uk_form(privacy_line_required=False, footer_template=UK_TEMPLATE[:4])),
        "markets[uk].privacy_line_required",
        "must be true for a GB Market (UK GDPR)",
    ),
    (
        file_of(uk_form(eligible_legal_forms="not_checked")),
        "markets[uk].eligible_legal_forms",
        "must list the eligible legal forms for a GB Market (PECR: corporate subscribers only)",
    ),
    (  # the floor follows the country, not the code
        file_of(uk_form(code="gb", free_webmail_blocked=False)),
        "markets[gb].free_webmail_blocked",
        "must be true for a GB Market (PECR: corporate subscribers only)",
    ),
    (  # even a disabled GB Market, and one that sends no cold email
        file_of(uk_form(enabled=False, cold_email_allowed=False, free_webmail_blocked=False)),
        "markets[uk].free_webmail_blocked",
        "must be true for a GB Market (PECR: corporate subscribers only)",
    ),
    (  # a GB list widened beyond decision 2's eight company types
        file_of(uk_form(eligible_legal_forms=[*UK_FORMS, "limited-partnership"])),
        "markets[uk].eligible_legal_forms[8]",
        GB_FORM_REASON,
    ),
    (
        file_of(uk_form(eligible_legal_forms=["ltd", "old-public-company", "plc"])),
        "markets[uk].eligible_legal_forms[1]",
        GB_FORM_REASON,
    ),
    (  # whatever the code
        file_of(uk_form(code="gb", eligible_legal_forms=["scottish-partnership"])),
        "markets[gb].eligible_legal_forms[0]",
        GB_FORM_REASON,
    ),
    (  # a ccTLD of another country
        file_of(us_form(cctlds=["ca"])),
        "markets[us].cctlds[0]",
        "is the ccTLD of CA, not of the Market's country code US",
    ),
    (
        file_of(us_form(cctlds=["us", "uk"])),
        "markets[us].cctlds[1]",
        "is the ccTLD of GB, not of the Market's country code US",
    ),
]


@pytest.mark.parametrize(("data", "field", "reason"), FLOOR)
def test_the_compliance_floor_names_its_field(data: object, field: str, reason: str) -> None:
    assert problem(data) == (field, reason)


def test_a_us_market_may_turn_cold_email_off_and_block_webmail() -> None:
    [market] = validate_markets(
        file_of(us_form(cold_email_allowed=False, free_webmail_blocked=True))
    )

    assert (market.cold_email_allowed, market.free_webmail_blocked) == (False, True)


def test_gb_may_also_list_the_gb_cctld() -> None:
    [market] = validate_markets(file_of(uk_form(cctlds=["uk", "gb"])))

    assert market.cctlds == ("uk", "gb")


def test_a_narrower_gb_legal_form_list_is_accepted() -> None:
    [market] = validate_markets(file_of(uk_form(eligible_legal_forms=["llp", "ltd"])))

    assert market.eligible_legal_forms == ("llp", "ltd")


def test_another_markets_legal_forms_are_not_held_to_the_gb_list() -> None:
    forms = ["limited-partnership", "llc", "ltd"]

    [us, ca] = validate_markets(
        file_of(us_form(eligible_legal_forms=forms), ca_form(eligible_legal_forms=forms))
    )

    assert us.eligible_legal_forms == ca.eligible_legal_forms == tuple(forms)


# Validation: order and what a reason repeats -----------------------------------------------------


def test_the_first_problem_in_file_order_is_named() -> None:
    # name comes before country_code in the mapping, and fields come before the rules
    assert problem(file_of(us_form(name="", country_code="x")))[0] == "markets[us].name"
    assert problem(file_of(us_form(cctlds=["ca"], english_variant="x")))[0] == (
        "markets[us].english_variant"
    )
    assert problem(file_of(us_form(), uk_form(name="")))[0] == "markets[uk].name"


def test_a_field_after_a_missing_one_is_checked_first() -> None:
    form = without(us_form(), "name")
    form["enabled"] = "yes"

    assert problem(file_of(form)) == ("markets[us].enabled", "must be true or false")


@pytest.mark.parametrize(
    "data",
    [
        file_of(us_form(name=LONG * 3)),
        file_of(us_form(code=LONG)),
        file_of(us_form(country_code=LONG)),
        file_of(us_form(cctlds=[LONG])),
        file_of(us_form(eligible_legal_forms=[LONG.upper()])),
        file_of(us_form(default_timezone=LONG)),
        file_of(us_form(unknown_send_window=LONG)),
        file_of(us_form(english_variant=LONG)),
        us_template(*US_TEMPLATE, "{" + LONG + "}"),
        us_template(*US_TEMPLATE, LONG + "{"),
        file_of({**us_form(), LONG: 1}),
        {LONG: 1},
    ],
)
def test_no_reason_repeats_a_long_value(data: object) -> None:
    with pytest.raises(InvalidMarkets) as caught:
        validate_markets(data)

    assert LONG not in caught.value.message


# The file form ----------------------------------------------------------------------------------


@pytest.mark.parametrize("form", [us_form(), uk_form(), ca_form()])
def test_the_file_form_round_trips(form: dict[str, Any]) -> None:
    market = market_from_form(form)

    assert market_form(market) == form
    assert market_from_form(market_form(market)) == market
    assert list(market_form(market)) == [name for name in us_form() if name in form]


def test_market_form_writes_none_as_not_checked_and_leaves_out_an_unset_window() -> None:
    form = market_form(UK)

    assert form["eligible_legal_forms"] == UK_FORMS
    assert "unknown_send_window" not in form
    assert market_form(US)["eligible_legal_forms"] == "not_checked"
    assert market_form(US)["unknown_send_window"] == "11:00-17:00"
    assert market_form(US)["english_variant"] == "en_us"


def test_market_from_form_validates_one_item_with_its_path() -> None:
    with pytest.raises(InvalidMarkets) as caught:
        market_from_form(uk_form(free_webmail_blocked=False))

    assert caught.value.field == "markets[uk].free_webmail_blocked"
    with pytest.raises(InvalidMarkets) as caught:
        market_from_form(us_form(code="Bad"))
    assert caught.value.field == "markets[0].code"
    with pytest.raises(InvalidMarkets) as caught:
        market_from_form("us")
    assert (caught.value.field, caught.value.reason) == ("markets[0]", "must be a mapping")


@pytest.mark.parametrize(
    "window",
    [
        SendWindow(time(11, 0, 30), time(17, 0)),
        SendWindow(time(11, 0), time(17, 0, 0, 5)),
        (time(11, 0), time(17, 0)),
    ],
)
def test_a_window_that_isnt_whole_minutes_is_refused_not_rounded(window: object) -> None:
    hand_built = dataclasses.replace(US, unknown_send_window=window)  # type: ignore[arg-type]

    with pytest.raises(InvalidMarkets) as caught:
        market_from_form(market_form(hand_built))

    assert caught.value.field == "markets[us].unknown_send_window"


# market_for_country -----------------------------------------------------------------------------

CA_DISABLED = market_from_form(ca_form())
CA_ENABLED = market_from_form(ca_form(enabled=True))


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("US", US),
        ("GB", UK),
        ("us", US),
        (" gb ", UK),
        ("Gb\n", UK),
        ("CA", None),  # disabled
        ("", None),
        ("   ", None),
        (None, None),
        ("XX", None),
        ("USA", None),
        ("U", None),
        ("U1", None),
        ("UK", None),  # a ccTLD, not a country code
        ("ＵＳ", None),  # fullwidth US
    ],
)
def test_market_for_country_returns_only_an_enabled_market(
    code: str | None, expected: MarketDef | None
) -> None:
    assert market_for_country(code, (US, UK, CA_DISABLED)) == expected


def test_an_enabled_market_without_cold_email_is_still_its_market() -> None:
    assert market_for_country("CA", [US, UK, CA_ENABLED]) == CA_ENABLED
    assert market_for_country("CA", iter([US, CA_ENABLED])) == CA_ENABLED  # any iterable


def test_a_country_two_markets_claim_has_no_market() -> None:
    twin = dataclasses.replace(US, code="us2")

    assert market_for_country("US", [US, twin]) is None
    assert market_for_country("US", []) is None


# cctld_country -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "country"),
    [
        ("firm.co.uk", "GB"),
        ("firm.com", None),
        ("firm.de", "DE"),
        ("firm.io", "IO"),
        ("firm.eu", "EU"),
        ("firm.ca", "CA"),
        ("firm.us", "US"),
        ("firm.gb", "GB"),
        ("FIRM.CO.UK", "GB"),
        (" firm.de ", "DE"),
        ("firm.co.uk.", "GB"),
        ("Bob@Firm.Co.Uk.", "GB"),
        ("a@b@firm.de", "DE"),
        ("bob@firm.com", None),
        ("firm.org", None),
        ("firm.xn--p1ai", None),
        ("firm.c0", None),
        ("firm.c", None),
        ("firm", None),
        ("firm.", None),
        ("", None),
        ("@", None),
        (".uk", "GB"),
    ],
)
def test_cctld_country(domain: str, country: str | None) -> None:
    assert cctld_country(domain) == country


# is_free_webmail ---------------------------------------------------------------------------------

AC_WEBMAIL = (
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "hotmail.co.uk",
    "live.com",
    "yahoo.com",
    "yahoo.co.uk",
    "icloud.com",
    "aol.com",
    "btinternet.com",
    "protonmail.com",
)
DESIGN_NOTES_WEBMAIL = {
    *("gmail.com", "googlemail.com"),
    *("outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "live.co.uk", "msn.com"),
    *("yahoo.com", "yahoo.co.uk", "ymail.com", "rocketmail.com"),
    *("icloud.com", "me.com", "mac.com"),
    *("aol.com", "aol.co.uk"),
    *("protonmail.com", "proton.me", "pm.me"),
    *("gmx.com", "gmx.co.uk", "mail.com"),
    "yandex.com",
    *("btinternet.com", "sky.com", "virginmedia.com", "ntlworld.com", "talktalk.net"),
    *("blueyonder.co.uk", "tiscali.co.uk"),
    *("fastmail.com", "hushmail.com", "tutanota.com"),
}
REVIEW_WEBMAIL = {  # added in review (P1): more UK ISP and free providers
    *("btopenworld.com", "talk21.com", "virgin.net", "orange.net", "wanadoo.co.uk"),
    *("plus.com", "freeserve.co.uk"),
    "aim.com",
    *("gmx.net", "gmx.de", "web.de"),
    "protonmail.ch",
    *("tuta.io", "tuta.com", "tutanota.de"),
    *("zoho.com", "zohomail.com"),
}


def test_the_vendored_list_holds_the_acs_twelve_and_the_design_notes_list() -> None:
    assert set(AC_WEBMAIL) <= FREE_WEBMAIL_DOMAINS
    assert FREE_WEBMAIL_DOMAINS == DESIGN_NOTES_WEBMAIL | REVIEW_WEBMAIL
    assert isinstance(FREE_WEBMAIL_DOMAINS, frozenset)


@pytest.mark.parametrize("domain", sorted(DESIGN_NOTES_WEBMAIL | REVIEW_WEBMAIL))
def test_every_listed_domain_is_free_webmail(domain: str) -> None:
    assert is_free_webmail(domain)
    assert is_free_webmail(f"someone@{domain.upper()}")


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("Bob@Mail.GMAIL.com.", True),
        ("x.btinternet.com", True),
        ("btopenworld.com", True),
        ("virgin.net", True),
        ("x.plus.com", True),
        ("deep.sub.yahoo.co.uk", True),
        (" GMAIL.COM ", True),
        ("notgmail.com", False),
        ("gmail.com.evil.example", False),
        ("gmail.co", False),
        ("gmail", False),
        ("com", False),
        ("", False),
        ("firm.co.uk", False),
        ("co.uk", False),
    ],
)
def test_is_free_webmail(domain: str, expected: bool) -> None:
    assert is_free_webmail(domain) is expected


@pytest.mark.parametrize("function", [cctld_country, is_free_webmail])
def test_a_domain_must_be_text(function: Any) -> None:
    with pytest.raises(TypeError):
        function(None)


# render_footer_template --------------------------------------------------------------------------

FOOTER_VALUES = {
    "sender_signature": "Mac, Nextron AI",
    "legal_name": "Nextron AI Ltd",
    "postal_address": "1 Example Street, London",
    "optout_line": "Reply STOP to opt out.",
}


def test_a_blank_us_label_drops_its_line() -> None:
    values = {**FOOTER_VALUES, "us_solicitation_label": ""}

    rendered = render_footer_template(US.footer_template, values)

    assert rendered == (
        "Mac, Nextron AI\nNextron AI Ltd\n1 Example Street, London\nReply STOP to opt out."
    )
    assert len(rendered.split("\n")) == 4


def test_a_set_us_label_renders_as_its_own_last_line() -> None:
    rendered = render_footer_template(
        US.footer_template, {**FOOTER_VALUES, "us_solicitation_label": "Advertisement"}
    )

    lines = rendered.split("\n")
    assert len(lines) == 5
    assert lines[-1] == "Advertisement"


def test_a_whitespace_only_value_drops_its_line_too() -> None:
    rendered = render_footer_template(UK.footer_template, {**FOOTER_VALUES, "privacy_line": "  "})

    assert rendered.split("\n") == list(FOOTER_VALUES.values())


def test_a_missing_value_names_the_placeholder_never_a_value() -> None:
    with pytest.raises(ValueError, match=r"\{optout_line\}") as caught:
        render_footer_template(
            US.footer_template,
            {key: value for key, value in FOOTER_VALUES.items() if key != "optout_line"}
            | {"us_solicitation_label": ""},
        )

    assert all(value not in str(caught.value) for value in FOOTER_VALUES.values())


def test_a_value_that_isnt_text_counts_as_missing() -> None:
    values: dict[str, Any] = {**FOOTER_VALUES, "us_solicitation_label": None}

    with pytest.raises(ValueError, match=r"\{us_solicitation_label\}"):
        render_footer_template(US.footer_template, values)


def test_a_value_is_never_scanned_again() -> None:
    values = {**FOOTER_VALUES, "sender_signature": "{legal_name} {optout_line}"}

    rendered = render_footer_template(CA_TEMPLATE, values)

    assert rendered.split("\n")[0] == "{legal_name} {optout_line}"
    assert rendered.count("Nextron AI Ltd") == 1


def test_literal_text_stays_and_extra_values_are_ignored() -> None:
    template = ["Best regards,", "{sender_signature} at {legal_name}", "{optout_line}"]

    rendered = render_footer_template(template, {**FOOTER_VALUES, "unused": "x"})

    assert rendered == "Best regards,\nMac, Nextron AI at Nextron AI Ltd\nReply STOP to opt out."


def test_values_may_be_keyed_by_the_placeholder_enum() -> None:
    values = {FooterPlaceholder(key): value for key, value in FOOTER_VALUES.items()}

    assert render_footer_template(CA_TEMPLATE, values).split("\n") == list(FOOTER_VALUES.values())


def test_a_template_must_be_lines_not_one_string() -> None:
    with pytest.raises(TypeError):
        render_footer_template("{legal_name}", FOOTER_VALUES)
