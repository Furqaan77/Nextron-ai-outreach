"""The UK legal-form rules (``nextron.domain.legal_form``, Story 2.12): the one list of corporate
subscriber types, the status, the Eligibility facts, the name key, the company numbers and
postcodes a page quotes, the registry quote and the finding's invariants."""

from datetime import UTC, datetime

import pytest

import nextron.domain.eligibility as eligibility_module
import nextron.domain.markets as markets_module
from nextron.domain.legal_form import (
    CHECK_LEGAL_FORM_JOB,
    CORPORATE_SUBSCRIBER_FORMS,
    LEGAL_FORM_LABELS,
    LEGAL_FORM_PATTERN,
    UNLINKED,
    LegalFormFinding,
    LegalFormStatus,
    SiteQuote,
    company_page_url,
    eligibility_facts,
    find_company_numbers,
    find_postcodes,
    is_corporate_subscriber,
    name_occurs,
    normalise_company_name,
    postcode_key,
    registry_quote,
    status_for,
)

CONFIRMED = LegalFormStatus.CONFIRMED
NOT_CORPORATE = LegalFormStatus.NOT_CORPORATE
UNCONFIRMED = LegalFormStatus.UNCONFIRMED
AT = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
PAGE = "https://acme.co.uk/about"


# The one list -----------------------------------------------------------------------------------


def test_the_job_name() -> None:
    assert CHECK_LEGAL_FORM_JOB == "check_legal_form"


def test_markets_and_eligibility_share_the_one_list() -> None:
    assert markets_module._GB_CORPORATE_FORMS is CORPORATE_SUBSCRIBER_FORMS
    assert markets_module.LEGAL_FORM_PATTERN is LEGAL_FORM_PATTERN
    assert eligibility_module.LEGAL_FORM_LABELS is LEGAL_FORM_LABELS
    assert set(LEGAL_FORM_LABELS) == CORPORATE_SUBSCRIBER_FORMS
    assert len(CORPORATE_SUBSCRIBER_FORMS) == 8


@pytest.mark.parametrize(
    ("code", "corporate"),
    [
        ("ltd", True),
        ("plc", True),
        ("llp", True),
        ("private-unlimited", True),
        ("private-unlimited-nsc", True),
        ("private-limited-guarant-nsc", True),
        ("private-limited-guarant-nsc-limited-exemption", True),
        ("private-limited-shares-section-30-exemption", True),
        ("limited-partnership", False),
        ("scottish-partnership", False),
        ("oversea-company", False),
        ("charitable-incorporated-organisation", False),
        ("something-new", False),
        ("LTD", False),
        ("", False),
        (None, False),
    ],
)
def test_every_code_is_classified(code: str | None, corporate: bool) -> None:
    assert is_corporate_subscriber(code) is corporate


@pytest.mark.parametrize(
    ("company_type", "company_status", "status"),
    [
        ("ltd", "active", CONFIRMED),
        ("llp", "active", CONFIRMED),
        ("limited-partnership", "active", NOT_CORPORATE),
        (None, "active", NOT_CORPORATE),
        ("ltd", "dissolved", UNCONFIRMED),
        ("ltd", "liquidation", UNCONFIRMED),
        ("ltd", None, UNCONFIRMED),
        ("limited-partnership", "dissolved", UNCONFIRMED),
        (None, None, UNCONFIRMED),
    ],
)
def test_status_for_a_linked_record(
    company_type: str | None, company_status: str | None, status: LegalFormStatus
) -> None:
    assert status_for(company_type, company_status) is status


@pytest.mark.parametrize(
    ("legal_form", "status", "facts"),
    [
        ("ltd", "confirmed", ("ltd", True)),
        ("plc", CONFIRMED, ("plc", True)),
        ("limited-partnership", "not_corporate", ("limited-partnership", True)),
        ("ltd", "not_corporate", ("ltd", False)),  # inconsistent: never confirmed
        ("limited-partnership", "confirmed", ("limited-partnership", False)),
        ("ltd", "unconfirmed", ("ltd", False)),
        (None, "not_corporate", (None, False)),
        (None, "unconfirmed", (None, False)),
        (None, None, (None, False)),
        ("ltd", None, ("ltd", False)),
        ("ltd", "something", ("ltd", False)),
        ("Not A Code", "confirmed", (None, False)),
    ],
)
def test_eligibility_facts(
    legal_form: str | None, status: str | None, facts: tuple[str | None, bool]
) -> None:
    assert eligibility_facts(legal_form, status) == facts


# Names ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Smith & Jones LLP", "smith and jones"),
        ("SMITH AND JONES LLP", "smith and jones"),
        ("Smith & Jones L.L.P.", "smith and jones"),
        ("Acme Accounting Services Limited", "acme accounting services"),
        ("Acme Ltd.", "acme"),
        ("ACME HOLDINGS PLC", "acme holdings"),
        ("Acme Ltd Limited", "acme"),  # dropped repeatedly
        ("Limited Edition Ltd", "limited edition"),  # only trailing tokens
        ("O'Brien's Bakery", "obriens bakery"),
        ("O’Brien’s Bakery", "obriens bakery"),
        ("  Acme   (UK)  Ltd ", "acme uk"),
        ("ＡＣＭＥ Ltd", "acme"),  # NFKC: full-width letters
        ("Straße GmbH", "strasse gmbh"),  # casefolded
        ("Ltd", ""),
        ("", ""),
    ],
)
def test_normalise_company_name(name: str, key: str) -> None:
    assert normalise_company_name(name) == key


def test_a_name_occurs_only_as_a_whole_token_run() -> None:
    text = "Acme Accounting Services Limited is registered in England and Wales."

    assert name_occurs("acme accounting services", text)
    assert name_occurs("acme accounting", text)
    assert not name_occurs("acme account", text)
    assert not name_occurs("cme accounting", text)
    assert not name_occurs("", text)  # an empty key never matches


def test_the_name_rules_refuse_a_non_text() -> None:
    with pytest.raises(TypeError):
        normalise_company_name(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        name_occurs("acme", None)  # type: ignore[arg-type]


# Company numbers --------------------------------------------------------------------------------


def numbers(text: str) -> list[tuple[str, str]]:
    return [(found.number, found.quote) for found in find_company_numbers(text)]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Company No 01234567", [("01234567", "Company No 01234567")]),
        ("Company No. 1234567", [("01234567", "Company No. 1234567")]),
        ("company number: 01234567", [("01234567", "company number: 01234567")]),
        (
            "Company Registration Number 01234567",
            [("01234567", "Company Registration Number 01234567")],
        ),
        ("Company Reg No. SC123456", [("SC123456", "Company Reg No. SC123456")]),
        ("Registered No. 123456", [("00123456", "Registered No. 123456")]),
        ("Registered number 07654321", [("07654321", "Registered number 07654321")]),
        ("Registered in England 01234567", [("01234567", "Registered in England 01234567")]),
        ("Registered in Wales: 01234567", [("01234567", "Registered in Wales: 01234567")]),
        ("Registered in Scotland SC123456", [("SC123456", "Registered in Scotland SC123456")]),
        (
            "Registered in Northern Ireland NI123456",
            [("NI123456", "Registered in Northern Ireland NI123456")],
        ),
        ("COMPANY NO.01234567", [("01234567", "COMPANY NO.01234567")]),
    ],
)
def test_each_phrase_and_its_number(text: str, found: list[tuple[str, str]]) -> None:
    assert numbers(text) == found


def test_a_footer_quote_runs_from_the_phrase_to_the_number() -> None:
    text = (
        "© 2026 Acme Accounting Services Limited is registered in England and Wales,\n"
        "company number   01234567. VAT GB123456789."
    )

    assert numbers(text) == [
        ("01234567", "registered in England and Wales, company number 01234567")
    ]


def test_a_no_dot_in_the_gap_is_allowed() -> None:
    text = "Registered in England and Wales No. 01234567"

    assert numbers(text) == [("01234567", "Registered in England and Wales No. 01234567")]


@pytest.mark.parametrize(
    "text",
    [
        "Registered in England. Company 01234567",  # a sentence break in the gap
        "Registered in England! 01234567",
        "Registered in England? 01234567",
        "VAT registration number 123456789",  # bare "registration number" isn't a phrase
        "FCA registration number 123456",
        "SRA registration number 654321",
        "Company number 123456789",  # nine digits
        "Company number 12345",  # five digits
        "Company number ABC123456",  # three letters
        "Company number SC1234567",  # letters with seven digits
        "Company number 01234567X",  # a letter right after
        "Company number 2 01234567",  # a digit in the gap
        "Company number 00000000",  # never a real company
        "company norwich 01234567",  # not the phrase "company no"
        "subcompany no 01234567",  # a letter right before the phrase
        "Registered in Englandshire 01234567",
        "Company number " + "x" * 41 + " 01234567",  # past the window
        "",
    ],
)
def test_no_number_is_found(text: str) -> None:
    assert numbers(text) == []


def test_the_window_is_forty_characters_after_the_phrase() -> None:
    gap = " " + "a" * 38 + " "  # 40 characters

    assert numbers(f"Company number{gap}01234567") == [
        ("01234567", f"Company number{gap}01234567")
    ]
    assert numbers(f"Company number{gap}x01234567") == []  # a letter right before it
    assert numbers(f"Company number {'a' * 39} 01234567") == []  # 41


def test_each_number_appears_once_with_its_first_quote_in_text_order() -> None:
    text = (
        "Registered number 07654321. Company No. 1234567. Registered in England 01234567. "
        "Company number 07654321."
    )

    assert numbers(text) == [
        ("07654321", "Registered number 07654321"),
        ("01234567", "Company No. 1234567"),
    ]


def test_quotes_are_normalised_and_cut_from_the_normalised_text() -> None:
    text = "Company No.\n\t 1234567"

    [found] = find_company_numbers(text)

    assert (found.number, found.quote) == ("01234567", "Company No. 1234567")
    assert "Company" not in repr(found)  # the quote never shows in the repr


# Postcodes --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        ("London EC1A 1BB", ["EC1A1BB"]),
        ("London ec1a1bb", ["EC1A1BB"]),
        ("Manchester M1 1AE", ["M11AE"]),
        ("Birmingham B33 8TH", ["B338TH"]),
        ("Croydon CR2 6XH", ["CR26XH"]),
        (
            "London W1A 0AX and SW1A 2AA, then EC1A 1BB again EC1A1BB",
            ["W1A0AX", "SW1A2AA", "EC1A1BB"],
        ),
        ("XEC1A 1BB", []),  # a letter right before
        ("EC1A 1BBX", []),  # a letter right after
        ("Phone 0207 123 4567", []),
        ("", []),
    ],
)
def test_postcodes(text: str, keys: list[str]) -> None:
    assert [found.key for found in find_postcodes(text)] == keys


def test_a_postcode_quote_holds_up_to_80_characters_before_it_from_a_word_start() -> None:
    short = "Our office: 1 High Street, London EC1A 1BB"
    long = "x" * 50 + " Our registered office is at 1 High Street, Clerkenwell, London EC1A 1BB"

    [first] = find_postcodes(short)
    [second] = find_postcodes(long)

    assert first.quote == short
    assert second.quote.endswith("London EC1A 1BB")
    assert len(second.quote) <= 80 + len("EC1A 1BB")
    assert second.quote.startswith("Our registered office") or second.quote.startswith("registered")
    assert not second.quote.startswith(" ")
    assert second.quote in long


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("EC1A 1BB", "EC1A1BB"),
        (" ec1a  1bb ", "EC1A1BB"),
        ("M1 1AE", "M11AE"),
        ("Not a postcode", None),
        ("", None),
        (None, None),
        ("12345", None),
    ],
)
def test_postcode_key(value: str | None, key: str | None) -> None:
    assert postcode_key(value) == key


# The registry quote -----------------------------------------------------------------------------


def test_the_registry_quote_is_a_canonical_rendering() -> None:
    quote = registry_quote(
        number="01234567",
        name="ACME ACCOUNTING SERVICES LIMITED",
        company_type="ltd",
        company_status="active",
        office_postcode="EC1A 1BB",
    )

    assert quote == (
        "Companies House record 01234567: company type ltd, company status active, registered "
        "office postcode EC1A 1BB, registered name ACME ACCOUNTING SERVICES LIMITED"
    )


def test_unknowns_read_as_unknown_and_none() -> None:
    quote = registry_quote(
        number="SC123456", name="Acme", company_type=None, company_status=None, office_postcode=None
    )

    assert quote == (
        "Companies House record SC123456: company type unknown, company status unknown, "
        "registered office postcode none, registered name Acme"
    )


def test_a_long_name_is_cut_so_the_quote_is_at_most_300_characters() -> None:
    name = "A" * 150 + " " + "B" * 9  # 160 characters
    quote = registry_quote(
        number="01234567",
        name=name,
        company_type="private-limited-guarant-nsc-limited-exemption",
        company_status="voluntary-arrangement",
        office_postcode="EC1A 1BB",
    )

    assert len(quote) <= 300
    assert quote.startswith("Companies House record 01234567: company type ")
    assert quote.endswith("A" * 20)
    assert quote == quote.strip()


def test_the_company_page_url() -> None:
    assert company_page_url("01234567") == (
        "https://find-and-update.company-information.service.gov.uk/company/01234567"
    )
    with pytest.raises(ValueError):
        company_page_url("1234567")


# The finding ------------------------------------------------------------------------------------


def site() -> SiteQuote:
    return SiteQuote(PAGE, "Company No. 01234567", AT)


def linked(**changes: object) -> LegalFormFinding:
    values: dict[str, object] = {
        "status": CONFIRMED,
        "number": "01234567",
        "legal_form": "ltd",
        "company_status": "active",
        "site": site(),
        "registry_quote": "Companies House record 01234567: company type ltd",
        "retrieved_at": AT,
    }
    values.update(changes)
    return LegalFormFinding(**values)  # type: ignore[arg-type]


def test_a_linked_finding() -> None:
    finding = linked()

    assert finding.linked
    assert "Company No." not in repr(finding)
    assert not UNLINKED.linked and UNLINKED.status is UNCONFIRMED


@pytest.mark.parametrize(
    "changes",
    [
        {"site": None},
        {"registry_quote": None},
        {"retrieved_at": None},
        {"status": UNCONFIRMED},  # an active ltd is confirmed
        {"legal_form": "limited-partnership"},  # then it is not_corporate
        {"number": "1234567"},
        {"legal_form": "Ltd"},
        {"company_status": "Active"},
        {"registry_quote": " padded "},
        {"registry_quote": "x" * 301},
    ],
)
def test_a_linked_finding_must_be_consistent(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        linked(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"site": site()},
        {"registry_quote": "Companies House record"},
        {"legal_form": "ltd"},
        {"company_status": "active"},
        {"status": CONFIRMED},
        {"status": NOT_CORPORATE},
    ],
)
def test_without_a_number_there_is_nothing_but_unconfirmed(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "status": UNCONFIRMED,
        "number": None,
        "legal_form": None,
        "company_status": None,
        "site": None,
        "registry_quote": None,
        "retrieved_at": None,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        LegalFormFinding(**values)  # type: ignore[arg-type]


def test_bad_types_are_refused_without_their_values() -> None:
    with pytest.raises(TypeError, match="LegalFormStatus"):
        linked(status="confirmed")
    with pytest.raises(TypeError, match="SiteQuote"):
        linked(site="Company No. 01234567")
    with pytest.raises(TypeError, match="aware"):
        SiteQuote(PAGE, "Company No. 01234567", datetime(2026, 11, 18))  # noqa: DTZ001
    with pytest.raises(ValueError) as raised:
        SiteQuote(PAGE, "  Company No. 01234567", AT)
    assert "01234567" not in str(raised.value)
