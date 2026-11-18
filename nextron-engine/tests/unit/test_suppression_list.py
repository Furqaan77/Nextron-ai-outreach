"""The Suppression List panel's rules and messages (``nextron.domain.suppression_list``, FR-50,
FR-41, AD-16, NFR-36, Story 3.5): a typed value read as an address or a domain (website and URL
included) and refused as ``NOT_VALID`` (an address whose local part holds a quoted-only special,
such as ``mailto:jane@firm.co.uk``, included), ``FREE_WEBMAIL`` or ``SHARED_PLATFORM``; the
search key; every parse rule of an imported file (BOM, line breaks, blank lines, the first CSV
column, invalid lines by number, repeats kept) and every limit (5 MiB, 10,000 values plus a
header line, UTF-8, NUL, empty); the reversal's reason (no control, format, separator,
private-use or noncharacter); and every message, word for word, none holding the value."""

from datetime import UTC, datetime

import pytest

from nextron.domain.contactability import (
    AddressSubject,
    DomainSubject,
    KeyKind,
    SuppressionKey,
    key_hash,
)
from nextron.domain.errors import DomainError
from nextron.domain.suppression_list import (
    ADDED,
    ALREADY_REVERSED,
    FILE_EMPTY,
    FILE_NOT_UTF8,
    FILE_TOO_LARGE,
    FILE_TOO_MANY,
    FREE_WEBMAIL,
    INVALID_LINES_SHOWN,
    MAX_FILE_BYTES,
    MAX_REASON_LENGTH,
    MAX_VALUES,
    NOT_ON_THE_LIST,
    NOT_VALID,
    ONLY_RULE_MATCHED,
    REASON_INVALID,
    REASON_REQUIRED,
    SHARED_PLATFORM,
    UNKNOWN_ENTRY,
    EntryValue,
    ImportPlan,
    SuppressionRefused,
    already_on_the_list,
    checked_reason,
    entry_key,
    entry_value,
    import_message,
    invalid_lines_message,
    on_the_list,
    parse_import,
    reversed_message,
    search_key,
)

SINCE = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)


def address_key(value: str) -> SuppressionKey:
    return SuppressionKey(KeyKind.ADDRESS, key_hash("address", value), None)


def domain_key(value: str) -> SuppressionKey:
    return SuppressionKey(KeyKind.DOMAIN, key_hash("domain", value), value)


def refusal(fn: object, *args: object) -> str:
    with pytest.raises(SuppressionRefused) as caught:
        fn(*args)  # type: ignore[operator]
    return caught.value.message


# Messages ---------------------------------------------------------------------------------------


def test_the_messages_are_the_specs_words() -> None:
    assert NOT_VALID == "Not a valid email address or domain"
    assert FREE_WEBMAIL == (
        "Free-webmail domains can't be suppressed as a whole. Add the address instead."
    )
    assert SHARED_PLATFORM == (
        "Shared platforms such as facebook.com or sites.google.com can't be suppressed as a "
        "whole. Add the firm's own domain or the address instead."
    )
    assert ONLY_RULE_MATCHED == "Only rule-matched opt-outs can be reversed"
    assert ALREADY_REVERSED == "This opt-out has already been reversed"
    assert UNKNOWN_ENTRY == "That entry isn't on the list"
    assert REASON_REQUIRED == "Enter a reason for reversing this opt-out"
    assert REASON_INVALID == "The reason must be at most 500 characters of plain text"
    assert FILE_EMPTY == "The file has no addresses or domains"
    assert FILE_NOT_UTF8 == "The file isn't UTF-8 text"
    assert FILE_TOO_LARGE == "The file is larger than 5 MB"
    assert FILE_TOO_MANY == (
        "The file has more than 10,000 addresses and domains. Split it and import each part."
    )
    assert ADDED == "Added to the Suppression List"
    assert NOT_ON_THE_LIST == "Not on the list"


def test_the_answers_name_the_date_and_the_source() -> None:
    assert on_the_list(SINCE, "reply_rule") == (
        "On the list (active since 2026-11-18, source reply_rule)"
    )
    assert already_on_the_list(SINCE, "operator") == (
        "Already on the list (active since 2026-11-18, source operator)"
    )


@pytest.mark.parametrize(
    ("others", "text"),
    [
        (0, "Opt-out reversed."),
        (1, "Opt-out reversed. 1 other entry from the same opt-out is still on the list."),
        (2, "Opt-out reversed. 2 other entries from the same opt-out are still on the list."),
        (
            1200,
            "Opt-out reversed. 1,200 other entries from the same opt-out are still on the "
            "list.",
        ),
    ],
)
def test_a_reversal_counts_the_same_opt_outs_other_entries(others: int, text: str) -> None:
    assert reversed_message(others) == text


def test_the_import_summary_counts_each_outcome() -> None:
    assert import_message(1200, 3, 0) == (
        "Import finished: 1,200 added, 3 already suppressed, 0 invalid."
    )


def test_invalid_lines_are_listed_up_to_a_hundred() -> None:
    assert invalid_lines_message(()) is None
    assert invalid_lines_message((4, 1200)) == "Invalid lines: 4, 1,200"
    hundred = tuple(range(1, INVALID_LINES_SHOWN + 1))
    assert invalid_lines_message(hundred) == "Invalid lines: " + ", ".join(map(str, hundred))
    assert invalid_lines_message(tuple(range(1, 251))) == (
        "Invalid lines: " + ", ".join(map(str, hundred)) + " and 150 more"
    )


def test_a_refusal_is_a_domain_error_with_its_code() -> None:
    refused = SuppressionRefused(NOT_VALID)

    assert isinstance(refused, DomainError)
    assert (refused.code, refused.message) == ("suppression_refused", NOT_VALID)


# A typed value ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "normalised"),
    [
        ("Jane@Firm.co.uk", "jane@firm.co.uk"),
        ("  jane.doe+x@firm.co.uk\t", "jane.doe+x@firm.co.uk"),  # no plus or dot stripping
        ("jane@gmail.com", "jane@gmail.com"),  # a webmail address is fine
        ("jane@bücher.de", "jane@xn--bcher-kva.de"),
    ],
)
def test_text_with_an_at_sign_is_an_address(text: str, normalised: str) -> None:
    value = entry_value(text)

    assert entry_key(text) == address_key(normalised)
    assert value == EntryValue(AddressSubject(normalised), address_key(normalised))
    assert search_key(text) == address_key(normalised)


@pytest.mark.parametrize(
    ("text", "domain"),
    [
        ("firm.co.uk", "firm.co.uk"),
        ("www.firm.co.uk", "firm.co.uk"),
        ("https://www.Firm.co.uk/x", "firm.co.uk"),
        ("http://mail.firm.co.uk/team?x=1", "firm.co.uk"),
        (" FIRM.CO.UK. ", "firm.co.uk"),
        ("bücher.de", "xn--bcher-kva.de"),
    ],
)
def test_anything_else_is_a_domain_website_or_url(text: str, domain: str) -> None:
    value = entry_value(text)

    assert entry_key(text) == domain_key(domain)
    assert value == EntryValue(DomainSubject(domain), domain_key(domain))
    assert value.key.domain_plain == domain
    assert search_key(text) == domain_key(domain)


@pytest.mark.parametrize(
    "text",
    [
        "jane@",
        "@firm.co.uk",
        "jane@firm",
        "jane@@firm.co.uk",
        "jane doe@firm.co.uk",
        "not a domain",
        "",
        "   ",
        "co.uk",
        "firm",
        "example.local",
        "http://192.168.0.1/",
        "ftp://firm.co.uk",
        "firm.co.uk," + "x",
        "a" * 2049 + ".com",
    ],
)
def test_text_that_is_neither_is_not_valid(text: str) -> None:
    assert refusal(entry_key, text) == NOT_VALID
    assert refusal(entry_value, text) == NOT_VALID
    assert refusal(search_key, text) == NOT_VALID


@pytest.mark.parametrize(
    "text",
    [
        "gmail.com",
        "GMAIL.COM",
        "https://www.gmail.com/",
        "mail.yahoo.co.uk",
        "yahoo.co.uk",
        "comcast.net",
        "nc.rr.com",
        "btinternet.com",
    ],
)
def test_a_free_webmail_domain_is_refused_and_never_on_the_list(text: str) -> None:
    assert refusal(entry_value, text) == FREE_WEBMAIL
    assert search_key(text) is None
    assert entry_key(text).kind is KeyKind.DOMAIN  # still a key: only adding is refused


@pytest.mark.parametrize(
    "text",
    [
        "mailto:jane@firm.co.uk",
        "email:jane@firm.co.uk",
        "https://medium.com/@jane.doe",
        "<jane@firm.co.uk>",
        "jane(x)@firm.co.uk",
        "jane[x]@firm.co.uk",
        "jane;x@firm.co.uk",
        "jane,x@firm.co.uk",
        '"jane"@firm.co.uk',
        "jane\\x@firm.co.uk",  # a backslash
    ],
)
def test_an_address_whose_local_part_holds_a_quoted_only_special_is_not_valid(text: str) -> None:
    assert refusal(entry_value, text) == NOT_VALID
    assert refusal(search_key, text) == NOT_VALID
    quoted = '"' + text.replace('"', '""') + '"'  # so the first CSV column is exactly the text
    plan = parse_import(lines("firm.co.uk", quoted, "jane@firm.co.uk"))
    assert plan.invalid_lines == (2,)
    assert [number for number, _ in plan.values] == [1, 3]


@pytest.mark.parametrize(
    "text",
    [
        "https://facebook.com/firm",
        "facebook.co.uk",
        "sites.google.com/view/firm",
        "https://www.lawsociety.org.uk/find/firm",
        "linktr.ee/firm",
        "firm.wordpress.com",
    ],
)
def test_a_shared_platform_is_refused_and_never_on_the_list(text: str) -> None:
    assert refusal(entry_value, text) == SHARED_PLATFORM
    assert search_key(text) is None
    assert entry_key(text).kind is KeyKind.DOMAIN  # still a key: only adding is refused
    assert parse_import(lines("firm.co.uk", text)).invalid_lines == (2,)


def test_a_refusal_never_holds_the_value() -> None:
    for text in ("jane.doe@", "gmail.com"):
        with pytest.raises(SuppressionRefused) as caught:
            entry_value(text)
        assert text not in str(caught.value)
        assert caught.value.__cause__ is None


@pytest.mark.parametrize("function", [entry_key, entry_value, search_key])
def test_a_typed_value_must_be_a_str(function: object) -> None:
    with pytest.raises(TypeError):
        function(b"jane@firm.co.uk")  # type: ignore[operator]


# An imported file -------------------------------------------------------------------------------


def lines(*rows: str, newline: str = "\n") -> bytes:
    return newline.join(rows).encode("utf-8")


def test_the_matrix_file_gives_values_and_invalid_line_numbers() -> None:
    data = lines(
        "jane@firm.co.uk",  # 1
        "Jane@Firm.co.uk",  # 2, a repeat: kept, so the import counts it already suppressed
        "https://www.other.co.uk/team",  # 3
        "gmail.com",  # 4, free webmail
        "not a value",  # 5, junk
        "",  # 6, blank
        '"a@b.com",x',  # 7, the first CSV column
    )

    plan = parse_import(data)

    assert plan == ImportPlan(
        values=(
            (1, entry_value("jane@firm.co.uk")),
            (2, entry_value("jane@firm.co.uk")),
            (3, entry_value("other.co.uk")),
            (7, entry_value("a@b.com")),
        ),
        invalid_lines=(4, 5),
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_each_line_break_splits_and_numbers_lines(newline: str) -> None:
    plan = parse_import(lines("a@firm.co.uk", "  ", "firm.co.uk", "x", newline=newline) + b"\n")

    assert [number for number, _ in plan.values] == [1, 3]
    assert plan.invalid_lines == (4,)


def test_a_bom_is_skipped_and_a_header_line_is_invalid() -> None:
    plan = parse_import(b"\xef\xbb\xbfemail,name\r\njane@firm.co.uk,Jane\r\n")

    assert plan.invalid_lines == (1,)
    assert plan.values == ((2, entry_value("jane@firm.co.uk")),)


def test_the_first_column_is_stripped_and_quoted_commas_are_kept_together() -> None:
    plan = parse_import(
        lines(
            "  firm.co.uk  , extra, columns",
            '"jane@firm.co.uk" ,x',
            '"x,y",firm.co.uk',  # the first column is "x,y": invalid
            ",firm.co.uk",  # an empty first column: invalid
            "\tjane@other.co.uk\t",
        )
    )

    assert plan.values == (
        (1, entry_value("firm.co.uk")),
        (2, entry_value("jane@firm.co.uk")),
        (5, entry_value("jane@other.co.uk")),
    )
    assert plan.invalid_lines == (3, 4)


def test_a_line_csv_cannot_read_is_invalid() -> None:
    oversized = "a" * 200_000 + "@firm.co.uk"  # past csv's field size limit

    plan = parse_import(lines(oversized, "firm.co.uk"))

    assert plan.invalid_lines == (1,)
    assert [number for number, _ in plan.values] == [2]


def test_ten_thousand_values_and_any_number_of_blank_lines_are_accepted() -> None:
    rows = [f"user{number}@firm.co.uk" for number in range(MAX_VALUES)]
    data = ("\n\n".join(rows) + "\n  \n").encode("utf-8")

    plan = parse_import(data)

    assert len(plan.values) == MAX_VALUES
    assert plan.values[-1][0] == 2 * MAX_VALUES - 1
    assert plan.invalid_lines == ()


VALUES = [f"user{number}@firm.co.uk" for number in range(MAX_VALUES + 1)]


def test_a_header_and_ten_thousand_values_are_accepted() -> None:
    plan = parse_import(lines("Email address", *VALUES[:MAX_VALUES]))

    assert len(plan.values) == MAX_VALUES
    assert plan.invalid_lines == (1,)


def test_a_header_and_ten_thousand_and_one_values_are_refused_whole() -> None:
    assert refusal(parse_import, lines("Email address", *VALUES)) == FILE_TOO_MANY


def test_ten_thousand_and_one_values_without_a_header_are_refused_whole() -> None:
    assert refusal(parse_import, lines(*VALUES)) == FILE_TOO_MANY


def test_only_an_invalid_first_line_is_let_off_the_limit() -> None:
    data = lines(VALUES[0], "junk", *VALUES[1:MAX_VALUES])  # the invalid line isn't first

    assert refusal(parse_import, data) == FILE_TOO_MANY


def test_a_file_of_five_mib_is_read_and_one_byte_more_is_refused() -> None:
    line = b"firm.co.uk\n"
    padding = b" " * (MAX_FILE_BYTES - len(line))

    assert len(parse_import(line + padding).values) == 1
    assert refusal(parse_import, line + padding + b" ") == FILE_TOO_LARGE


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", FILE_EMPTY),
        (b"\n \r\n\t\n", FILE_EMPTY),
        (b"\xef\xbb\xbf", FILE_EMPTY),
        (b"jane@firm.co.uk\n\xff\xfe", FILE_NOT_UTF8),
        ("café@firm.co.uk".encode("latin-1"), FILE_NOT_UTF8),
        (b"\xed\xa0\x80@firm.co.uk", FILE_NOT_UTF8),  # an encoded surrogate
        (b"jane@firm.co.uk\x00\n", FILE_NOT_UTF8),
        (b"firm.co.uk\n\x00", FILE_NOT_UTF8),
    ],
)
def test_a_file_that_is_empty_or_not_utf8_text_is_refused_whole(data: bytes, message: str) -> None:
    assert refusal(parse_import, data) == message


def test_an_imported_file_must_be_bytes() -> None:
    with pytest.raises(TypeError):
        parse_import("firm.co.uk")  # type: ignore[arg-type]


# The reversal's reason --------------------------------------------------------------------------


def test_a_reason_is_stripped() -> None:
    assert checked_reason("  the rule misread a yes  ") == "the rule misread a yes"
    assert checked_reason("x" * MAX_REASON_LENGTH) == "x" * MAX_REASON_LENGTH
    assert checked_reason(" " + "é" * MAX_REASON_LENGTH + "\n") == "é" * MAX_REASON_LENGTH


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_a_blank_reason_is_required(text: str) -> None:
    assert refusal(checked_reason, text) == REASON_REQUIRED


@pytest.mark.parametrize(
    "text",
    [
        "x" * (MAX_REASON_LENGTH + 1),
        "two\nlines",
        "a\rb",
        "bell\x07",
        "zero\u200bwidth",
        "rtl\u202eoverride",
        "soft\u00adhyphen",
        "lone \ud800 surrogate",
        "line\u2028separator",
        "paragraph\u2029separator",
        "private\ue000use",
        "non\ufffecharacter",
    ],
)
def test_a_long_reason_or_one_with_a_refused_character_is_invalid(text: str) -> None:
    assert refusal(checked_reason, text) == REASON_INVALID


def test_a_reason_must_be_a_str() -> None:
    with pytest.raises(TypeError):
        checked_reason(None)  # type: ignore[arg-type]
