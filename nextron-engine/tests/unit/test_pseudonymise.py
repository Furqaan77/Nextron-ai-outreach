"""The Reply pseudonymiser (``nextron.domain.pseudonymise``, Story 4.6): each kind of personal
detail is replaced, quoted history is dropped, dates and the words of an opt-out are kept, and a
second pass changes nothing.
"""

import pytest

from nextron.domain.pseudonymise import (
    ADDRESS,
    EMAIL,
    NAME,
    ORGANISATION,
    PHONE,
    URL,
    pseudonymise,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Write to dana.whitfield@brightwell.com today.", f"Write to {EMAIL} today."),
        ("Write to o'neil+ops@mail.hale.co.uk.", f"Write to {EMAIL}."),
    ],
)
def test_email_addresses_are_replaced(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See https://www.brightwell.com/team.", f"See {URL}."),
        ("See http://hale.co.uk/a?b=c, then reply.", f"See {URL}, then reply."),
        ("See www.ridgeway-freight.io for more", f"See {URL} for more"),
        ("Our site is brightwell-law.com.", f"Our site is {URL}."),
        ("Our site is hale.co.uk/about", f"Our site is {URL}"),
    ],
)
def test_urls_and_bare_domains_are_replaced(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Call 020 7123 4567 after ten.", f"Call {PHONE} after ten."),
        ("Call +44 7911 123456.", f"Call {PHONE}."),
        ("Call (415) 555-1234 or 415.555.9876.", f"Call {PHONE} or {PHONE}."),
        ("Ref 1234567 is the order.", f"Ref {PHONE} is the order."),
    ],
)
def test_phone_numbers_are_replaced(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I'm back on 2026-11-23.",
        "I'm back on 23/11/2026.",
        "I'm back on 23.11.26, after 3 weeks.",
        "We have 12 lawyers and 2,500 orders a month.",
    ],
)
def test_dates_and_short_numbers_are_kept(text: str) -> None:
    assert pseudonymise(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We moved to Leeds LS1 4AB last year.", f"We moved to {NAME} {ADDRESS} last year."),
        ("Post to sw1a1aa, please.", f"Post to {ADDRESS}, please."),
        ("We're at 123 Main Street, Suite 200 now.", f"We're at {ADDRESS} now."),
        ("We're at 4 Market St. now.", f"We're at {ADDRESS} now."),
        ("We moved to Springfield, IL 62701.", f"We moved to {NAME}, {ADDRESS}."),
        ("We moved to Austin, Texas 78701.", f"We moved to {NAME}, {ADDRESS}."),
        ("Send it to PO Box 42.", f"Send it to {ADDRESS}."),
        ("Send it to P.O. Box 42.", f"Send it to {ADDRESS}."),
    ],
)
def test_postal_addresses_are_replaced(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We are Hale Freight Ltd, based nearby.", f"We are {ORGANISATION}, based nearby."),
        ("Please ask Marsh & Cole Solicitors about it.", f"Please ask {ORGANISATION} about it."),
        ("Thanks Hale Freight Ltd for that.", f"Thanks {ORGANISATION} for that."),
        ("Ask Smith Jones LLP.", f"{ORGANISATION}."),  # over-replaced: "Ask" goes too
        ("It went to Hale & Co instead.", f"It went to {ORGANISATION} instead."),
        ("Ridgeway Partners handles it.", f"{ORGANISATION} handles it."),
    ],
)
def test_organisations_with_a_company_suffix_are_replaced(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


def test_the_given_organisations_are_replaced_in_any_case() -> None:
    found = pseudonymise("we at brightwell think so", organisations=["Brightwell"])

    assert found == f"we at {ORGANISATION} think so"


def test_the_given_names_are_replaced_whole_and_by_each_part() -> None:
    found = pseudonymise(
        "dana whitfield said no. whitfield will call. dana agrees.",
        names=["Dana Whitfield"],
    )

    assert found == f"{NAME} said no. {NAME} will call. {NAME} agrees."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Please ask Mr. Jones.", f"Please ask {NAME}."),
        ("Please ask Dr Priya Shah.", f"Please ask {NAME}."),
        ("Hi Dana,\nThanks.", f"Hi {NAME},\nThanks."),
        ("Hello Dana, thanks.", f"Hello {NAME}, thanks."),
        ("Dear Sir,\nThanks.", "Dear Sir,\nThanks."),
        ("Hi there,\nThanks.", "Hi there,\nThanks."),
        ("Kind regards,\nPriya Shah", f"Kind regards,\n{NAME}"),
        ("Thanks,\n\nTom", f"Thanks,\n\n{NAME}"),
        ("Thanks, Tom", f"Thanks, {NAME}"),
        ("Talk to Priya Shah, our COO.", f"Talk to {NAME}, our COO."),
        ("Talk to Priya about it.", f"Talk to {NAME} about it."),
        ("Sarah handles intake.", f"{NAME} handles intake."),
        ("Sarah will call you.", f"{NAME} will call you."),
        ("Sarah's away this week.", f"{NAME} away this week."),
        ("Jane Smith runs ops.", f"{NAME} runs ops."),
    ],
)
def test_names_are_replaced(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Unsubscribe",
        "STOP",
        "No.",
        "No thanks, remove me",
        "Please don't email me again!",
        "Take me off your list.",
        "Thanks.\nUnsubscribe me",
        "Thanks, but no.",
        "Hi, please remove me from your list.",
        "REMOVE ME. NOW.",
        "We already use Clio and Copilot, so not for us.",
        "hi dana,\nthanks.",  # a greeting never takes a lowercase word
        "Hello, unsubscribe.",
        "Hi STOP!",
        "Take Me Off Your List",
        "Please Remove Me From Your Email List Again",
        "Currently, we use Excel.",
        "We'll miss the deadline sometimes.",
        "Pass.",
        "I'm out of office until Monday 23 November.",
        "Our ERP is Dynamics GP and we live in Excel.",
        "Can you send me some information?",
    ],
)
def test_ordinary_words_and_opt_outs_are_kept(text: str) -> None:
    assert pseudonymise(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Not for us.\n\nOn Mon, 16 Nov 2026 at 10:02, Sam wrote:\n> Saw you're hiring.\n",
            "Not for us.\n\n",
        ),
        (
            "Not for us.\nOn Mon, 16 Nov 2026 at 10:02, Sam <sam@x.example.com>\nwrote:\n> Hi",
            "Not for us.\n",
        ),
        ("Interested.\n> Saw you're hiring.\n  > quoted too\nTell me more.", "Interested.\nTell me more."),
        ("No.\n-----Original Message-----\nFrom: Sam\nHi Dana", "No.\n"),
    ],
)
def test_quoted_history_is_dropped(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


A_REPLY = (
    "Hi Sam,\n\n"
    "Thanks for the note. Please talk to Dana Whitfield, our Managing Partner, at "
    "dana.whitfield@brightwell.com or on 020 7123 4567. She handles intake, and Tom will cover "
    "while she's out.\n\n"
    "Kind regards,\n"
    "Priya Shah\n"
    "Brightwell & Hale LLP\n"
    "4 Market Street, Lincoln LN1 1AB\n"
    "https://www.brightwell-law.com\n\n"
    "On Mon, 16 Nov 2026 at 10:02, Sam <sam@nextron.example.com> wrote:\n"
    "> Saw you're hiring an intake coordinator.\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Not interested.\n\nJane", f"Not interested.\n\n{NAME}"),  # a lone name line
        ("Not for us.\nJ. Smith", f"Not for us.\n{NAME}"),  # initials
        ("Tom, our COO, handles this.", f"{NAME}, our COO, handles this."),
        ("Sarah from accounts deals with this.", f"{NAME} from accounts deals with this."),
        ("Bethan here - I look after intake.", f"{NAME} here - I look after intake."),
        ("Talk to Harvey, our COO.", f"Talk to {NAME}, our COO."),  # no first name is allowed
        ("Please ask Sage or Will about it.", f"Please ask {NAME} or {NAME} about it."),
        ("We met at 10:30, Sam and I agreed.", f"We met at 10:30, {NAME} and I agreed."),
        ("It costs 3.5, Sam said.", f"It costs 3.5, {NAME} said."),
        ("REGARDS,\nJANE SMITH", f"REGARDS,\n{NAME}"),  # an all-capitals signature
        ("ask mr jones about it", f"ask {NAME} about it"),  # a lowercase honorific
        ("Ask DR. Patel.", f"Ask {NAME}."),
    ],
)
def test_the_common_name_shapes_are_replaced(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


def test_a_given_name_with_a_possessive_is_replaced() -> None:
    found = pseudonymise("dana's team and Whitfield’s too", names=["Dana Whitfield"])

    assert found == f"{NAME} team and {NAME} too"


def test_a_given_name_is_replaced_even_when_it_is_an_allowed_word() -> None:
    assert pseudonymise("Thanks, Clio.", names=["Clio Barnes"]) == f"Thanks, {NAME}."
    assert pseudonymise("Thanks, Clio.") == "Thanks, Clio."  # the tool name stays otherwise


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Call 020\u00a07123\u00a04567.", f"Call {PHONE}."),
        ("Call 020\u20077123\u202f4567.", f"Call {PHONE}."),
        ("Post to LN1\u00a01AB.", f"Post to {ADDRESS}."),
        ("Call 020\u20137123\u20134567.", f"Call {PHONE}."),  # en dashes
        ("Call 415\u2014555\u20141234.", f"Call {PHONE}."),  # em dashes
    ],
)
def test_odd_spaces_and_dashes_never_hide_a_number_or_postcode(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


def test_an_email_address_with_unicode_letters_is_replaced() -> None:
    assert pseudonymise("Write to josé@brightwell.com or zoë@hale.co.uk.") == (
        f"Write to {EMAIL} or {EMAIL}."
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Not for us.\n\nFrom: Dana Whitfield <dana@brightwell.com>\nSent: 16 November 2026"
            "\nTo: Sam\nSubject: intake\n\nSaw you're hiring.",
            "Not for us.\n\n",
        ),
        (
            "Not for us.\nFrom: Sam <sam@x.example.com>\nDate: Mon, 16 Nov 2026\nSubject: hi",
            "Not for us.\n",
        ),
        ("Not for us.\n________________________________\nFrom: Sam\nHi Dana", "Not for us.\n"),
        (
            "Over to Tom.\n\n---------- Forwarded message ---------\nFrom: Sam\nHi Dana",
            f"Over to {NAME}.\n\n",
        ),
    ],
)
def test_outlook_and_gmail_quoted_history_is_dropped(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


def test_a_from_line_without_a_header_block_is_kept() -> None:
    assert pseudonymise("From: our side, it's a no.") == "From: our side, it's a no."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Thanks,\r\nTom\r\n", f"Thanks,\n{NAME}\n"),
        (
            "Not for us.\r\n\r\nOn Mon, 16 Nov 2026 at 10:02, Sam wrote:\r\n> Saw you're "
            "hiring.\r\n> Worth a look?\r\n",
            "Not for us.\n\n",
        ),
        ("Interested.\r\n> quoted\r\nTell me more.\r\n", "Interested.\nTell me more.\n"),
    ],
)
def test_crlf_line_ends_are_read_as_lines(text: str, expected: str) -> None:
    assert pseudonymise(text) == expected


def test_a_whole_reply_keeps_no_detail_and_a_second_pass_changes_nothing() -> None:
    once = pseudonymise(A_REPLY, names=["Dana Whitfield"], organisations=["Brightwell"])

    for detail in (
        "Sam",
        "Dana",
        "Whitfield",
        "Priya",
        "Shah",
        "Tom",
        "Brightwell",
        "Hale",
        "dana.whitfield",
        "7123",
        "Market Street",
        "LN1",
        "brightwell-law",
        "Lincoln",
        "hiring an intake",
    ):
        assert detail not in once, detail
    assert "She handles intake" in once
    assert pseudonymise(once, names=["Dana Whitfield"], organisations=["Brightwell"]) == once


@pytest.mark.parametrize(
    "text",
    [
        A_REPLY,
        "Ask Peter in our Leeds office, or Mr. Jones.",
        "hi dana, Talk to Jane Smith at Hale Freight Ltd (hale.co.uk), +44 20 7123 4567.",
        "Thanks,\nJane\n\nSent from my phone",
        "Springfield, IL 62701. PO Box 42. 12 High Street.",
        "[name] already said [url] was fine; Jane Smith agrees.",
        "Jane Doe hi bob, thanks",  # a greeting counts only at a true line start
        "Jane Doe hi Bob, thanks",
        "REGARDS,\nJANE SMITH\n\nTom, our COO, handles this. Ask mr jones at 10:30.",
    ],
)
def test_pseudonymise_is_idempotent(text: str) -> None:
    once = pseudonymise(text, names=["Dana"], organisations=["Hale"])

    assert pseudonymise(once, names=["Dana"], organisations=["Hale"]) == once


def test_neighbouring_names_become_one_placeholder() -> None:
    assert pseudonymise("Hi Dana Whitfield,", names=["Dana"]) == f"Hi {NAME},"


def test_only_text_and_strings_are_taken() -> None:
    with pytest.raises(TypeError):
        pseudonymise(b"bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        pseudonymise("text", names=[5])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="not a str"):
        pseudonymise("a text", names="Dana Whitfield")
