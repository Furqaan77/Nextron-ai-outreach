"""``nextron.domain.identity`` (FR-9, SC-8): Story 2.2's Identity rows, every refusal (whose
message never holds the input), the Public Suffix List's wildcard, exception and private rules,
the 253 and 63 character limits, ``COUNTRY_CODES`` against tzdata, and each normaliser being
idempotent on its own output. Story 2.3 adds ``url_host`` (steps 1 to 4 of
``registrable_domain``, ``www`` kept) and the ``UK`` alias of ``normalize_country_code``."""

import re
import unicodedata
from collections.abc import Callable
from importlib import resources

import pytest

from nextron.domain import identity
from nextron.domain.errors import DomainError
from nextron.domain.identity import (
    COUNTRY_ALIASES,
    COUNTRY_CODES,
    MAX_INPUT_LENGTH,
    IdentityError,
    normalize_company_number,
    normalize_country_code,
    normalize_email,
    normalize_linkedin_url,
    normalize_name,
    normalize_place_id,
    psl_version,
    registrable_domain,
    url_host,
)
from tests.contracts.support import PACKAGE

NORMALISERS: list[Callable[[str], str]] = [
    url_host,
    registrable_domain,
    normalize_email,
    normalize_name,
    normalize_linkedin_url,
    normalize_company_number,
    normalize_place_id,
    normalize_country_code,
]
PSL_RULES = 10_455
LI = "https://www.linkedin.com/in/jane-doe"

# A 253-character name: three 63-character labels, a 57-character one, then "com".
LONGEST_NAME = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 57, "com"])


def refused(fn: Callable[[str], str], text: str) -> IdentityError:
    with pytest.raises(IdentityError) as caught:
        fn(text)
    return caught.value


# The I/O matrix -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fn", "text", "expected"),
    [
        (registrable_domain, "https://www.Smith-Law.co.uk/team", "smith-law.co.uk"),
        (registrable_domain, "bücher.de", "xn--bcher-kva.de"),
        (registrable_domain, "a.b.ck", "a.b.ck"),
        (registrable_domain, "foo.blogspot.com", "foo.blogspot.com"),
        (normalize_linkedin_url, "linkedin.com/in/Jane-Doe/?trk=x", LI),
        (normalize_email, " Jane.Doe+x@Example.COM ", "jane.doe+x@example.com"),
    ],
)
def test_the_identity_rows_of_the_matrix(
    fn: Callable[[str], str], text: str, expected: str
) -> None:
    assert fn(text) == expected


@pytest.mark.parametrize(
    ("fn", "text"),
    [
        (registrable_domain, "co.uk"),
        (registrable_domain, "www.co.uk"),
        (registrable_domain, "ftp://x.com"),
        (registrable_domain, "10.0.0.1"),
        (registrable_domain, "example.local"),
        (registrable_domain, "localhost"),
        (normalize_email, "a@b@c.com"),
        (normalize_linkedin_url, "linkedin.com/company/x"),
    ],
)
def test_the_refused_rows_of_the_matrix(fn: Callable[[str], str], text: str) -> None:
    error = refused(fn, text)

    assert isinstance(error, DomainError)
    assert error.code == "invalid_identity"
    assert error.message == error.reason == str(error)
    assert text not in error.message


# registrable_domain ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("example.com", "example.com"),
        ("EXAMPLE.COM", "example.com"),
        ("  https://Example.com:8443/a/b?c=d#e  ", "example.com"),
        ("HTTP://example.com", "example.com"),
        ("example.com.", "example.com"),
        ("www.example.com", "example.com"),
        ("www.www.example.com", "example.com"),
        ("shop.example.co.uk", "example.co.uk"),
        ("BÜCHER.de", "xn--bcher-kva.de"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("example\u3002com", "example.com"),  # an IDNA 2003 label separator
        ("example.公司.cn", "example.xn--55qx5d.cn"),  # an IDN rule, IDNA-encoded
        ("user@example.com", "example.com"),  # the hostname, not the user information
        ("https://nextronai.com@evil.example.org/", "example.org"),
    ],
)
def test_registrable_domain(text: str, expected: str) -> None:
    assert registrable_domain(text) == expected


def test_a_wildcard_rule_makes_one_more_label_public() -> None:
    assert registrable_domain("a.b.ck") == "a.b.ck"  # *.ck
    assert registrable_domain("x.a.b.ck") == "a.b.ck"
    refused(registrable_domain, "b.ck")


def test_an_exception_rule_prevails_over_its_wildcard() -> None:
    # *.kawasaki.jp and !city.kawasaki.jp
    assert registrable_domain("city.kawasaki.jp") == "city.kawasaki.jp"
    assert registrable_domain("a.city.kawasaki.jp") == "city.kawasaki.jp"
    assert registrable_domain("y.x.kawasaki.jp") == "y.x.kawasaki.jp"
    refused(registrable_domain, "x.kawasaki.jp")


def test_private_rules_count() -> None:
    assert registrable_domain("foo.blogspot.com") == "foo.blogspot.com"
    assert registrable_domain("a.foo.blogspot.com") == "foo.blogspot.com"
    refused(registrable_domain, "blogspot.com")


@pytest.mark.parametrize("text", ["example.local", "example.notatld", "a.b.internal"])
def test_a_top_level_label_with_no_rule_is_refused(text: str) -> None:
    assert "Public Suffix List" in refused(registrable_domain, text).message


def test_the_longest_name_and_label_pass_and_one_more_character_is_refused() -> None:
    assert len(LONGEST_NAME) == 253
    assert registrable_domain(LONGEST_NAME) == "d" * 57 + ".com"
    one_more = LONGEST_NAME.replace("d" * 57, "d" * 58)
    assert len(one_more) == 254
    assert "253" in refused(registrable_domain, one_more).message
    assert registrable_domain("a" * 63 + ".com") == "a" * 63 + ".com"
    refused(registrable_domain, "a" * 64 + ".com")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "exa mple.com",
        "example.com\x00",
        "exa\tmple.com",
        "ftp://example.com",
        "javascript://example.com",
        "file:///c:/x",
        "10.0.0.1",
        "http://192.168.0.1:80/",
        "http://[::1]/",
        "http://[abc",
        "http:///path",
        "//example.com",
        "example..com",
        "example.com..",
        "-example.com",
        "exam_ple.com",
        "com",
        "co.uk",
        "www.co.uk",
        "a.www.co.uk",  # www under a public suffix
        "www.com",
        "localhost",
        "www",
        "x.www.ck",
    ],
)
def test_registrable_domain_refuses(text: str) -> None:
    error = refused(registrable_domain, text)

    assert not text.strip() or text.strip() not in error.message


# P1: the IDNA 2008 deviation characters ----------------------------------------------------------

DEVIATIONS = ["\u00df", "\u1e9e", "\u03c2", "\u200c", "\u200d"]


@pytest.mark.parametrize("character", DEVIATIONS)
@pytest.mark.parametrize(
    ("fn", "template"),
    [
        (registrable_domain, "https://stra{}e.de"),
        (registrable_domain, "www.ex{}ample.co.uk"),
        (normalize_email, "info@stra{}e.de"),
    ],
)
def test_a_label_holding_a_deviation_character_is_refused(
    fn: Callable[[str], str], template: str, character: str
) -> None:
    text = template.format(character)

    error = refused(fn, text)

    assert text not in error.message
    assert character not in error.message


def test_deviation_characters_are_never_mapped_to_another_domain() -> None:
    assert registrable_domain("strasse.de") == "strasse.de"
    refused(registrable_domain, "stra\u00dfe.de")  # not strasse.de, another registered domain
    refused(registrable_domain, "\u03c3\u03bf\u03c2.gr")  # final sigma is not mapped to sigma
    assert normalize_email("info@strasse.de") == "info@strasse.de"
    refused(normalize_email, "info@stra\u00dfe.de")
    assert normalize_email("stra\u00dfe@example.de") == "stra\u00dfe@example.de"  # local part


# normalize_email ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" Jane.Doe+x@Example.COM ", "jane.doe+x@example.com"),
        ("j.a.n.e+tag+more@example.co.uk", "j.a.n.e+tag+more@example.co.uk"),
        ("jane@bücher.de", "jane@xn--bcher-kva.de"),
        ("Jose\u0301@example.com", "jos\u00e9@example.com"),  # NFC
        ("jane@example.com.uk", "jane@example.com.uk"),
    ],
)
def test_normalize_email(text: str, expected: str) -> None:
    assert normalize_email(text) == expected


def test_the_longest_address_passes_and_one_more_character_is_refused() -> None:
    domain = ".".join(["a" * 63, "b" * 63, "c" * 57, "com"])
    address = "l" * 64 + "@" + domain

    assert len(address) == 254
    assert normalize_email(address) == address
    refused(normalize_email, "l" * 64 + "@" + "c" + domain)
    refused(normalize_email, "l" * 65 + "@example.com")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "jane",
        "jane@@example.com",
        "a@b@c.com",
        "@example.com",
        "jane@",
        "jane doe@example.com",
        "jane@exa mple.com",
        "jane\x00@example.com",
        "jane@localhost",
        "jane@example..com",
        "jane@exam_ple.com",
        "jane@-example.com",
    ],
)
def test_normalize_email_refuses(text: str) -> None:
    error = refused(normalize_email, text)

    assert not text or text not in error.message


@pytest.mark.parametrize("character", ["\u200b", "\u00ad", "\u2060", "\ufeff", "\u202e"])
def test_normalize_email_refuses_a_format_character(character: str) -> None:
    """P2: a zero-width space or soft hyphen would give one person a second key."""
    error = refused(normalize_email, f"ja{character}ne@example.com")

    assert "format" in error.message


def test_normalize_email_composes_after_lowercasing() -> None:
    """P3: lowercasing J + U+030C leaves a pair that NFC composes."""
    once = normalize_email("J\u030cane@example.com")

    assert once == "\u01f0ane@example.com"
    assert unicodedata.is_normalized("NFC", once)
    assert normalize_email(once) == once


# normalize_name -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  Smith & Jones, LLP!! ", "smith jones llp"),
        ("Ｓｍｉｔｈ　Ｌａｗ", "smith law"),  # NFKC folds full-width forms
        ("STRASSE Straße", "strasse strasse"),  # casefold
        ("O'Brien---Partners", "o brien partners"),
        ("Müller 2", "müller 2"),
    ],
)
def test_normalize_name(text: str, expected: str) -> None:
    assert normalize_name(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "!!! ---", "\u200b"])
def test_normalize_name_refuses_a_name_with_no_letters_or_digits(text: str) -> None:
    refused(normalize_name, text)


def test_normalize_name_refuses_more_than_500_characters() -> None:
    assert normalize_name("a" * 500) == "a" * 500
    assert "500" in refused(normalize_name, "a" * 501).message


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("J\u030cane", "\u01f0ane"),  # the mark stays on its letter, and NFKC composes them
        ("\u092a\u094d\u0930\u093f\u092f\u093e", "\u092a\u094d\u0930\u093f\u092f\u093e"),
        ("Fran\u00e7ois & Co", "fran\u00e7ois co"),
        ("Franc\u0327ois & Co", "fran\u00e7ois co"),
    ],
)
def test_normalize_name_keeps_combining_marks_with_their_letters(text: str, expected: str) -> None:
    """P4: a combining mark is a word character, so distinct names don't collapse."""
    once = normalize_name(text)

    assert once == expected
    assert normalize_name(once) == once
    assert " " not in normalize_name("\u092a\u094d\u0930\u093f\u092f\u093e")


# normalize_linkedin_url -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("linkedin.com/in/Jane-Doe/?trk=x", "https://www.linkedin.com/in/jane-doe"),
        ("HTTPS://WWW.LINKEDIN.COM/IN/JANE-DOE", "https://www.linkedin.com/in/jane-doe"),
        ("http://uk.linkedin.com/in/jane-doe/details/experience/#top", LI),
        ("https://www.linkedin.com/in/jos%C3%A9", "https://www.linkedin.com/in/jos%C3%A9"),
        ("https://www.linkedin.com/in/JOS%C3%89", "https://www.linkedin.com/in/jos%C3%A9"),
        ("https://www.linkedin.com/in/josé", "https://www.linkedin.com/in/jos%C3%A9"),
        ("linkedin.com/in/a.b_c~d", "https://www.linkedin.com/in/a.b_c~d"),
    ],
)
def test_normalize_linkedin_url(text: str, expected: str) -> None:
    assert normalize_linkedin_url(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "linkedin.com/company/x",
        "linkedin.com/in/",
        "linkedin.com/in",
        "linkedin.com//in/jane",
        "linkedin.com/pub/jane",
        "ftp://linkedin.com/in/jane",
        "evil-linkedin.com/in/jane",
        "linkedin.com.evil.example/in/jane",
        "a..linkedin.com/in/jane",
        "instagram.com/in/jane",
        "linkedin.com/in/%FF",
        "linkedin.com/in/jane%20doe",
        "linkedin.com/in/jane%00",
        "linkedin.com/in/" + "a" * 101,
    ],
)
def test_normalize_linkedin_url_refuses(text: str) -> None:
    error = refused(normalize_linkedin_url, text)

    assert not text or text not in error.message


@pytest.mark.parametrize("encoded", ["%E2%80%8B", "%C2%AD", "%E2%81%A0", "%EF%BB%BF"])
def test_normalize_linkedin_url_refuses_a_slug_with_a_format_character(encoded: str) -> None:
    """P2: a zero-width space or soft hyphen in the slug would give one person a second key."""
    error = refused(normalize_linkedin_url, f"linkedin.com/in/ja{encoded}ne")

    assert "format" in error.message


def test_normalize_linkedin_url_composes_after_lowercasing() -> None:
    """P3: lowercasing J + U+030C leaves a pair that NFC composes."""
    once = normalize_linkedin_url("linkedin.com/in/J%CC%8Cane")

    assert once == "https://www.linkedin.com/in/%C7%B0ane"
    assert normalize_linkedin_url(once) == once


def test_a_100_character_slug_passes() -> None:
    assert normalize_linkedin_url("linkedin.com/in/" + "a" * 100) == LI.removesuffix(
        "jane-doe"
    ) + "a" * 100


# Company numbers, place IDs and country codes -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12345", "00012345"),
        ("1", "00000001"),
        ("01234567", "01234567"),
        (" sc123456 ", "SC123456"),
        ("OC000001", "OC000001"),
        ("NI0A1B2C", "NI0A1B2C"),
    ],
)
def test_normalize_company_number(text: str, expected: str) -> None:
    assert normalize_company_number(text) == expected


@pytest.mark.parametrize(
    "text", ["", "ABCDEFGH", "123456789", "SC12345", "SC-12345", "SC1234567", "sc1234ﬀ"]
)
def test_normalize_company_number_refuses(text: str) -> None:
    refused(normalize_company_number, text)


def test_normalize_place_id_strips_and_keeps_case() -> None:
    assert normalize_place_id(" ChIJN1t_tDeuEmsRUsoyG83frY4 ") == "ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert normalize_place_id("a-b_C" * 102 + "xy") == "a-b_C" * 102 + "xy"  # 512


@pytest.mark.parametrize("text", ["", "a b", "abc!", "a/b", "é", "a" * 513])
def test_normalize_place_id_refuses(text: str) -> None:
    refused(normalize_place_id, text)


def test_normalize_country_code() -> None:
    assert normalize_country_code(" gb ") == "GB"
    assert normalize_country_code("US") == "US"


@pytest.mark.parametrize("text", ["UK", "uk", " Uk "])
def test_uk_is_an_alias_of_gb(text: str) -> None:
    assert normalize_country_code(text) == "GB"


def test_the_one_alias_is_uk_and_it_maps_to_a_real_code() -> None:
    assert dict(COUNTRY_ALIASES) == {"UK": "GB"}
    assert set(COUNTRY_ALIASES.values()) <= COUNTRY_CODES
    assert set(COUNTRY_ALIASES).isdisjoint(COUNTRY_CODES)
    with pytest.raises(TypeError):
        COUNTRY_ALIASES["EN"] = "GB"  # type: ignore[index]


@pytest.mark.parametrize("text", ["", "GBR", "G", "zz", "gı", "UKK", "U K", "ＵＫ", "EN"])
def test_normalize_country_code_refuses(text: str) -> None:
    refused(normalize_country_code, text)


# url_host -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://WWW.Smith-Law.co.uk./team", "www.smith-law.co.uk"),
        ("www.smith-law.co.uk", "www.smith-law.co.uk"),
        ("smith-law.co.uk/team?x=1#y", "smith-law.co.uk"),
        ("HTTP://uk.Indeed.com/jobs", "uk.indeed.com"),
        ("bücher.de", "xn--bcher-kva.de"),
        ("maps.app.goo.gl/abc", "maps.app.goo.gl"),
        ("co.uk", "co.uk"),  # a public suffix is still a host
        ("localhost", "localhost"),  # and so is a single label
        ("example.local", "example.local"),  # url_host checks no suffix list
        ("https://user:pw@example.com:8443/x", "example.com"),
        (LONGEST_NAME, LONGEST_NAME),
    ],
)
def test_url_host_keeps_www_and_skips_the_suffix_list(text: str, expected: str) -> None:
    assert url_host(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a url",
        "ftp://example.com",
        "10.0.0.1",
        "[::1]",
        "https://",
        "a..b.com",
        "x" * 64 + ".com",
        LONGEST_NAME + "x",
        "stra\u00dfe.de",
        "exa\x00mple.com",
    ],
)
def test_url_host_refuses_what_registrable_domain_refuses_before_the_suffix_list(
    text: str,
) -> None:
    refused(url_host, text)
    refused(registrable_domain, text)


@pytest.mark.parametrize(
    "text",
    [
        "https://www.Smith-Law.co.uk/team",
        "bücher.de",
        "a.b.ck",
        "foo.blogspot.com",
        "www.example.com.",
        "WWW.EXAMPLE.CO.UK",
    ],
)
def test_registrable_domain_is_url_host_then_the_suffix_list(text: str) -> None:
    host = url_host(text)

    assert registrable_domain(host) == registrable_domain(text)


def test_country_codes_are_the_codes_in_tzdatas_iso3166_tab() -> None:
    table = resources.files("tzdata.zoneinfo").joinpath("iso3166.tab").read_text(encoding="utf-8")
    codes = {
        line.split("\t")[0] for line in table.splitlines() if line and not line.startswith("#")
    }

    assert len(COUNTRY_CODES) == 249
    assert COUNTRY_CODES == codes
    assert isinstance(COUNTRY_CODES, frozenset)


# Shared rules ---------------------------------------------------------------------------------


@pytest.mark.parametrize("fn", NORMALISERS)
@pytest.mark.parametrize("value", [None, b"example.com", 7, ["x"]])
def test_a_value_that_is_not_a_str_raises_type_error(fn: Callable[..., str], value: object) -> None:
    with pytest.raises(TypeError, match=r"takes a str"):
        fn(value)


@pytest.mark.parametrize("fn", NORMALISERS)
def test_an_input_over_2048_characters_is_refused(fn: Callable[[str], str]) -> None:
    error = refused(fn, "a" * (MAX_INPUT_LENGTH + 1))

    assert "a" * 20 not in error.message


PLANTED = "plantedzq"


@pytest.mark.parametrize(
    ("fn", "text"),
    [
        (registrable_domain, f"{PLANTED}.local"),
        (registrable_domain, f"ftp://{PLANTED}.com"),
        (registrable_domain, f"{PLANTED} .com"),
        (normalize_email, f"{PLANTED}@@example.com"),
        (normalize_email, f"{PLANTED}@localhost"),
        (normalize_email, f"{PLANTED}" * 8 + "@example.com"),
        (normalize_name, f"{PLANTED}" * 60),
        (normalize_linkedin_url, f"linkedin.com/company/{PLANTED}"),
        (normalize_linkedin_url, f"{PLANTED}.com/in/jane"),
        (normalize_company_number, PLANTED),
        (normalize_place_id, f"{PLANTED}!"),
        (normalize_country_code, PLANTED),
        (url_host, f"ftp://{PLANTED}.com"),
        (url_host, f"{PLANTED} .com"),
    ],
)
def test_no_refusal_message_holds_the_input(fn: Callable[[str], str], text: str) -> None:
    error = refused(fn, text)

    assert PLANTED not in error.message
    assert PLANTED not in repr(error.args)


@pytest.mark.parametrize(
    ("fn", "samples"),
    [
        (
            registrable_domain,
            [
                "https://www.Smith-Law.co.uk/team",
                "bücher.de",
                "a.b.ck",
                "foo.blogspot.com",
                "a.city.kawasaki.jp",
                "example.公司.cn",
                LONGEST_NAME,
            ],
        ),
        (
            normalize_email,
            [" Jane.Doe+x@Example.COM ", "jane@bücher.de", "Jose\u0301@example.com", "İ@x.com"],
        ),
        (normalize_name, ["Smith & Jones, LLP", "Ｓｍｉｔｈ", "İstanbul Ltd", "ǅ ﬁ ß \u0345"]),
        (
            normalize_linkedin_url,
            ["linkedin.com/in/Jane-Doe/?trk=x", "linkedin.com/in/JOS%C3%89", "linkedin.com/in/%zz"],
        ),
        (normalize_company_number, ["12345", "sc123456"]),
        (normalize_place_id, [" ChIJ_x-Y "]),
        (normalize_country_code, ["gb", " Us ", "uk"]),
        (url_host, ["https://WWW.Smith-Law.co.uk./team", "bücher.de", "co.uk"]),
    ],
)
def test_every_normaliser_is_idempotent(fn: Callable[[str], str], samples: list[str]) -> None:
    for sample in samples:
        once = fn(sample)
        assert fn(once) == once


# The Public Suffix List -----------------------------------------------------------------------


def test_every_rule_of_both_sections_is_parsed() -> None:
    parsed = identity._suffix_list()
    text = (PACKAGE / "domain" / "psl" / "public_suffix_list.dat").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("//")]

    assert parsed.rule_count == len(lines) == PSL_RULES
    assert len(parsed.rules) + len(parsed.exceptions) == PSL_RULES
    assert ("*", "ck") in parsed.rules
    assert ("www", "ck") in parsed.exceptions
    assert ("blogspot", "com") in parsed.rules  # the private section
    assert all(
        re.fullmatch(r"\*|[a-z0-9-]+", label) for rule in parsed.rules for label in rule
    )


def test_the_list_is_parsed_once() -> None:
    assert identity._suffix_list() is identity._suffix_list()


PSL_HEAD = "// VERSION: 2026-01-01_00-00-00_UTC\n"
ICANN = "// ===BEGIN ICANN DOMAINS===\ncom\n*.ck\n!www.ck\n// ===END ICANN DOMAINS===\n"
PRIVATE = "// ===BEGIN PRIVATE DOMAINS===\nblogspot.com\n// ===END PRIVATE DOMAINS===\n"


def test_a_small_list_parses() -> None:
    parsed = identity._parse_suffix_list(PSL_HEAD + ICANN + PRIVATE)

    assert parsed.version == "2026-01-01_00-00-00_UTC"
    assert parsed.rules == {("com",), ("*", "ck"), ("blogspot", "com")}
    assert parsed.exceptions == {("www", "ck")}
    assert parsed.rule_count == 4


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (PSL_HEAD + "org\n" + ICANN + PRIVATE, "a rule outside its sections"),
        (PSL_HEAD + ICANN + "org\n" + PRIVATE, "a rule outside its sections"),
        (ICANN + PRIVATE, "no VERSION line"),
        (PSL_HEAD + ICANN, "lacks its ICANN or private section"),
        (PSL_HEAD + PRIVATE + ICANN, "lacks its ICANN or private section"),
        (PSL_HEAD + ICANN + ICANN + PRIVATE, "lacks its ICANN or private section"),
    ],
)
def test_a_malformed_list_is_refused(text: str, problem: str) -> None:
    """P7: each of the parser's refusals, on a small inline list."""
    with pytest.raises(ValueError, match=problem):
        identity._parse_suffix_list(text)


def test_psl_version_is_the_headers_version() -> None:
    assert psl_version() == "2026-11-16_22-30-55_UTC"


def test_no_other_module_normalises_identities() -> None:
    """SC-8: only ``domain/identity.py`` uses IDNA or the Public Suffix List, or defines a
    normaliser."""
    pattern = re.compile(
        r"idna|public_suffix|def (registrable_domain|normalize_(email|name|linkedin_url"
        r"|company_number|place_id|country_code))\b"
    )
    offenders = [
        path.relative_to(PACKAGE).as_posix()
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.name != "identity.py" and pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
