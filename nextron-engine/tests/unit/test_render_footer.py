"""``nextron.domain.render``'s footer (FR-23, NFR-2, Story 4.11): the I/O matrix's render,
completeness, phrase, notice and digest rows, on the seeded 3.1 templates (``seed/markets.yaml``)
and on hand-built Markets."""

import dataclasses
import hashlib
from typing import Any

import pytest

from nextron.domain import render, seed_yaml
from nextron.domain.markets import FooterPlaceholder, MarketDef, validate_markets
from nextron.domain.render import (
    FOOTER_SETTING_KEYS,
    PLACEHOLDER_OF,
    PRIVACY_SOURCE_PHRASES,
    FooterCheck,
    FooterSettings,
    footer,
    footer_complete,
    footer_sha256,
    incomplete_notice,
    privacy_line,
)
from nextron.domain.settings_schema import Section, declared
from tests.contracts.support import ENGINE_ROOT

SEEDED = validate_markets(
    seed_yaml.parse((ENGINE_ROOT / "seed" / "markets.yaml").read_text(encoding="utf-8"))
)
US = next(market for market in SEEDED if market.code == "us")
UK = next(market for market in SEEDED if market.code == "uk")

SIGNATURE = "Furqaan, Nextron AI"
LEGAL_NAME = "Nextron AI Ltd"
ADDRESS = "1 Example Street, London EC1A 1AA"
OPTOUT = "Not relevant? Reply 'no' and I won't contact you again."
URL = "https://nextronai.com/privacy"
MARK = "QZXPLANTEDSOURCE"


def settings(**changes: str) -> FooterSettings:
    """Every footer setting set (the label blank) unless changed."""
    values = {
        "sender_signature": SIGNATURE,
        "legal_name": LEGAL_NAME,
        "postal_address": ADDRESS,
        "optout_line": OPTOUT,
        "privacy_notice_url": URL,
        "us_solicitation_label": "",
    }
    return FooterSettings(**{**values, **changes})


# The declarations --------------------------------------------------------------------------------


def test_the_footer_setting_keys_are_the_footer_section_in_declaration_order() -> None:
    assert FOOTER_SETTING_KEYS == (
        "sender_signature",
        "legal_name",
        "postal_address",
        "optout_line",
        "privacy_notice_url",
        "us_solicitation_label",
    )
    in_section = [str(s.key) for s in declared() if s.section is Section.FOOTER_AND_BOOKING]
    assert tuple(in_section) == FOOTER_SETTING_KEYS
    assert tuple(field.name for field in dataclasses.fields(FooterSettings)) == (
        FOOTER_SETTING_KEYS
    )


def test_each_footer_setting_fills_its_placeholder() -> None:
    assert dict(PLACEHOLDER_OF) == {
        "sender_signature": FooterPlaceholder.SENDER_SIGNATURE,
        "legal_name": FooterPlaceholder.LEGAL_NAME,
        "postal_address": FooterPlaceholder.POSTAL_ADDRESS,
        "optout_line": FooterPlaceholder.OPTOUT_LINE,
        "privacy_notice_url": FooterPlaceholder.PRIVACY_LINE,
        "us_solicitation_label": FooterPlaceholder.US_SOLICITATION_LABEL,
    }
    assert set(PLACEHOLDER_OF.values()) == set(FooterPlaceholder)


def test_the_mappings_are_read_only() -> None:
    with pytest.raises(TypeError):
        PLACEHOLDER_OF["legal_name"] = FooterPlaceholder.OPTOUT_LINE  # type: ignore[index]
    with pytest.raises(TypeError):
        PRIVACY_SOURCE_PHRASES["operator"] = "Mac"  # type: ignore[index]


def test_footer_settings_hold_only_text() -> None:
    with pytest.raises(TypeError, match="legal_name must be a str"):
        settings(legal_name=None)  # type: ignore[arg-type]
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings().legal_name = "x"  # type: ignore[misc]


# Row: US complete, US label, US ignores URL ------------------------------------------------------


def test_the_us_footer_is_four_lines_and_complete_with_the_label_blank() -> None:
    assert footer(settings(), US) == "\n".join((SIGNATURE, LEGAL_NAME, ADDRESS, OPTOUT))
    assert footer_complete(settings(), US) == FooterCheck(())
    assert footer_complete(settings(), US).complete and footer_complete(settings(), US)


def test_a_us_label_adds_a_fifth_line() -> None:
    golden = "\n".join((SIGNATURE, LEGAL_NAME, ADDRESS, OPTOUT, "Advertisement"))

    assert footer(settings(us_solicitation_label="Advertisement"), US) == golden


def test_a_us_footer_ignores_the_privacy_url_and_the_source() -> None:
    blank_url = settings(privacy_notice_url="")

    assert footer_complete(blank_url, US).complete
    assert footer(blank_url, US, "operator") == footer(settings(), US)
    assert "Privacy" not in footer(settings(), US, "website")


# Row: UK each source, UK URL blank ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "phrase"),
    [
        ("website", "your firm's website"),
        ("registry", "Companies House"),
        ("signal_inbox", "a public post"),
        ("csv", "a business list"),
        ("referral", "a colleague's referral"),
        ("finder", "a business email directory"),
    ],
)
def test_the_uk_footer_names_each_source_and_links_the_notice(source: str, phrase: str) -> None:
    golden = "\n".join(
        (
            SIGNATURE,
            LEGAL_NAME,
            ADDRESS,
            OPTOUT,
            f"Your details came from {phrase}. Privacy notice: {URL}",
        )
    )

    assert footer(settings(), UK, source) == golden
    assert footer_complete(settings(), UK).complete


def test_the_phrases_are_exactly_the_six_sources() -> None:
    assert set(PRIVACY_SOURCE_PHRASES) == {
        "website",
        "registry",
        "signal_inbox",
        "csv",
        "referral",
        "finder",
    }


def test_a_blank_url_ends_the_uk_privacy_line_at_the_source_and_is_missing() -> None:
    blank_url = settings(privacy_notice_url="  ")

    assert footer(blank_url, UK, "website").splitlines()[-1] == (
        "Your details came from your firm's website."
    )
    assert footer_complete(blank_url, UK).missing == ("privacy_notice_url",)
    assert incomplete_notice(footer_complete(blank_url, UK)) == (
        "Sending blocked: footer incomplete (Privacy notice URL)"
    )


def test_privacy_line_with_and_without_a_url() -> None:
    assert privacy_line("csv", URL) == (
        f"Your details came from a business list. Privacy notice: {URL}"
    )
    assert privacy_line("csv", "") == "Your details came from a business list."


# Row: Unknown source -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    ["operator", "pattern", "replier", "eligibility_basis", None, 3, "", "Website", MARK],
)
def test_a_source_without_a_phrase_raises_without_showing_it(source: Any) -> None:
    with pytest.raises(ValueError) as raised:
        privacy_line(source, URL)
    assert str(raised.value) == "no privacy-line phrase for this Provenance source"
    assert MARK not in str(raised.value)

    with pytest.raises(ValueError, match="no privacy-line phrase"):
        footer(settings(), UK, source)


def test_a_uk_footer_without_a_source_raises() -> None:
    with pytest.raises(ValueError, match="no privacy-line phrase"):
        footer(settings(), UK)


# Row: Incomplete ---------------------------------------------------------------------------------


def test_blank_values_drop_their_lines_and_are_missing() -> None:
    incomplete = settings(legal_name="", postal_address="")

    assert footer(incomplete, US) == "\n".join((SIGNATURE, OPTOUT))
    check = footer_complete(incomplete, US)
    assert check.missing == ("legal_name", "postal_address")
    assert not check.complete and not check
    assert incomplete_notice(check) == (
        "Sending blocked: footer incomplete (Legal name, Postal address)"
    )


def test_every_required_value_counts_and_whitespace_is_blank() -> None:
    nothing = settings(
        sender_signature=" ",
        legal_name="",
        postal_address="\t",
        optout_line="",
        privacy_notice_url="",
    )

    assert footer_complete(nothing, US).missing == (
        "sender_signature",
        "legal_name",
        "postal_address",
        "optout_line",
    )
    assert footer_complete(nothing, UK).missing == (
        "sender_signature",
        "legal_name",
        "postal_address",
        "optout_line",
        "privacy_notice_url",
    )
    assert incomplete_notice(footer_complete(nothing, UK)) == (
        "Sending blocked: footer incomplete (Sender signature, Legal name, Postal address, "
        "Opt-out line, Privacy notice URL)"
    )


def test_completeness_checks_the_required_values_whatever_the_template_holds() -> None:
    bare = dataclasses.replace(US, footer_template=("Sent by Nextron",))

    assert footer_complete(settings(legal_name=""), bare).missing == ("legal_name",)
    assert footer(settings(legal_name=""), bare) == "Sent by Nextron"


def test_the_notice_of_a_complete_footer_raises() -> None:
    with pytest.raises(ValueError, match="complete"):
        incomplete_notice(FooterCheck(()))


# Values are inserted once ------------------------------------------------------------------------


def test_a_value_holding_a_placeholder_is_not_scanned_again() -> None:
    sneaky = settings(sender_signature="{legal_name}", us_solicitation_label="{privacy_line}")

    assert footer(sneaky, US).splitlines() == [
        "{legal_name}",
        LEGAL_NAME,
        ADDRESS,
        OPTOUT,
        "{privacy_line}",
    ]


def test_a_market_is_rendered_from_its_own_template() -> None:
    reordered: MarketDef = dataclasses.replace(
        UK,
        footer_template=(
            "{sender_signature}",
            "{legal_name}, {postal_address}",
            "{optout_line}",
            "{privacy_line}",
        ),
    )

    assert footer(settings(), reordered, "registry").splitlines() == [
        SIGNATURE,
        f"{LEGAL_NAME}, {ADDRESS}",
        OPTOUT,
        f"Your details came from Companies House. Privacy notice: {URL}",
    ]


# Digest ------------------------------------------------------------------------------------------


def test_the_footer_digest_is_the_lowercase_hex_sha256_of_its_utf8_text() -> None:
    text = footer(settings(sender_signature="Furqaan — Nextron AI"), US)

    assert footer_sha256(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert len(footer_sha256(text)) == 64 and footer_sha256(text) == footer_sha256(text).lower()
    assert footer_sha256("") == hashlib.sha256(b"").hexdigest()
    assert footer_sha256("a") != footer_sha256("a\n")


def test_render_is_the_spine_module() -> None:
    assert render.footer is footer and render.footer_complete is footer_complete
