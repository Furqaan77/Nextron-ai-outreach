"""``nextron.domain.people`` (FR-10, FR-13, FR-51, AD-7, AD-17, AD-18; Story 2.10): which
proposed people are kept (each refusal's cause, in the checks' order, and silent repeats),
the office read from a person's own quote (never loosening a Contact's rules), the lawyer count
and the reconciliation of person Evidence per page and Contact. Standard library, pytest and
``nextron.domain`` only (plus the shipped Markets file)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nextron.domain import seed_yaml
from nextron.domain.budget import BudgetClass
from nextron.domain.contacts import RoleMatch
from nextron.domain.crawl import PageKind
from nextron.domain.extraction import SentPage
from nextron.domain.geo import Location
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.segments import SegmentDef, validate_segments
from nextron.domain.people import (
    BUDGET_CLASS,
    LAWYERS_LISTED,
    PEOPLE_PAGE_KINDS,
    PERSON_DISCARDED,
    ROUTE,
    SELECT_PRIMARY_CONTACT_JOB,
    LivePersonEvidence,
    PeoplePlan,
    Person,
    PersonDiscard,
    PersonDiscardCause,
    PersonQuote,
    PersonSuperseding,
    decision_maker,
    lawyer_count,
    office_from_quote,
    qualified_away,
    reconcile_people,
    validate_people,
)
from tests.contracts.support import ENGINE_ROOT

FETCHED = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
TEAM = "01KA00000000000000000000T1"
ABOUT = "01KA00000000000000000000A1"
TEAM_URL = "https://smith-law.com/team"
ABOUT_URL = "https://smith-law.com/about"
JANE = "Jane Roe, Managing Partner, Austin, TX"
ALEX = "Alex Poe, Associate"
PAT = "Pat Moe, Paralegal"
TEAM_TEXT = f"Our people\n{JANE}\n{ALEX}\n{PAT}\nSam   Lee,\nSenior  Partner"
ABOUT_TEXT = "About us\nFounded by Jane Roe (Managing Partner) in 1999."
PAGES = {
    TEAM: SentPage(TEAM_URL, FETCHED, TEAM_TEXT),
    ABOUT: SentPage(ABOUT_URL, FETCHED, ABOUT_TEXT),
}
UNSENT = "01KA00000000000000000000X1"  # a page id that wasn't sent
C1 = "01KA00000000000000000000C1"
C2 = "01KA00000000000000000000C2"


def markets() -> tuple[MarketDef, ...]:
    text = (ENGINE_ROOT / "seed" / "markets.yaml").read_text(encoding="utf-8")
    return validate_markets(seed_yaml.parse(text))


def item(name: str, role: str, quote: str, block_id: str = TEAM) -> dict[str, Any]:
    return {"name": name, "role": role, "block_id": block_id, "quote": quote}


def test_the_constants() -> None:
    assert (ROUTE, BUDGET_CLASS) == ("extract_people", BudgetClass.BLOCKABLE)
    assert PEOPLE_PAGE_KINDS == (PageKind.TEAM, PageKind.ABOUT, PageKind.CONTACT)
    assert (LAWYERS_LISTED, PERSON_DISCARDED) == ("lawyers listed", "person_discarded")
    assert SELECT_PRIMARY_CONTACT_JOB == "select_primary_contact"


# validate_people ---------------------------------------------------------------------------------


def test_a_person_whose_name_and_role_are_in_a_quote_on_the_page_is_kept() -> None:
    parsed = {
        "people": [
            item("Jane Roe", "Managing Partner", JANE),
            item("Alex Poe", "Associate", ALEX),
            item("Jane Roe", "Managing Partner", "Jane Roe (Managing Partner)", ABOUT),
            item(" Sam Lee ", "Senior Partner", "Sam Lee,\nSenior Partner"),  # normalised
        ]
    }

    found = validate_people(parsed, PAGES)

    assert found.discards == ()
    assert [(p.page_id, p.url, p.name, p.role, p.quote) for p in found.people] == [
        (TEAM, TEAM_URL, "Jane Roe", "Managing Partner", JANE),
        (TEAM, TEAM_URL, "Alex Poe", "Associate", ALEX),
        (ABOUT, ABOUT_URL, "Jane Roe", "Managing Partner", "Jane Roe (Managing Partner)"),
        (TEAM, TEAM_URL, "Sam Lee", "Senior Partner", "Sam Lee, Senior Partner"),
    ]
    assert "Jane" not in repr(found.people[0])


@pytest.mark.parametrize(
    ("proposal", "cause", "page"),
    [
        (item("Jane Roe", "Managing Partner", JANE, UNSENT), "unknown_block", None),
        (item("Jane Roe", "Managing Partner", "   "), "quote_empty", TEAM),
        (item("Jane Roe", "Managing Partner", "x" * 301), "quote_too_long", TEAM),
        (item("Jane Roe", "Managing Partner", f"{JANE}, USA"), "quote_not_in_page", TEAM),
        (item("Jane Roe", "Managing Partner", JANE.lower()), "quote_not_in_page", TEAM),
        (item("", "Managing Partner", JANE), "name_invalid", TEAM),
        (item("Jane\x00Roe", "Managing Partner", JANE), "name_invalid", TEAM),
        (item("!!!", "Managing Partner", JANE), "name_invalid", TEAM),
        (item("Jane Roe", "", JANE), "role_invalid", TEAM),
        (item("Jane Roe", "M" * 201, JANE), "role_invalid", TEAM),
        (item("John Doe", "Managing Partner", JANE), "name_not_in_quote", TEAM),
        (item("jane roe", "Managing Partner", JANE), "name_not_in_quote", TEAM),
        (item("Jane Roe", "Owner", JANE), "role_not_in_quote", TEAM),
        (item("Jane Roe", "managing partner", JANE), "role_not_in_quote", TEAM),
    ],
)
def test_each_refusal_has_its_cause(
    proposal: dict[str, Any], cause: str, page: str | None
) -> None:
    found = validate_people({"people": [proposal]}, PAGES)

    assert found.people == ()
    assert found.discards == (PersonDiscard(PersonDiscardCause(cause), page),)


@pytest.mark.parametrize(
    ("name", "role", "quote", "cause"),
    [
        ("Ann Lee", "Managing Partner", "Joann Lee, Managing Partner", "name_not_in_quote"),
        ("Jo Ann", "Managing Partner", "Jo Annette, Managing Partner", "name_not_in_quote"),
        ("Ann Lee", "Partner", "Ann Lee, Partnership Manager", "role_not_in_quote"),
        ("Ann Lee", "Owner", "Ann Lee, Co-Owners Ltd", "role_not_in_quote"),
    ],
)
def test_the_name_and_the_role_must_be_whole_in_the_quote(
    name: str, role: str, quote: str, cause: str
) -> None:
    pages = {TEAM: SentPage(TEAM_URL, FETCHED, quote)}

    found = validate_people({"people": [item(name, role, quote)]}, pages)

    assert found.discards == (PersonDiscard(PersonDiscardCause(cause), TEAM),)


def test_the_checks_run_in_order() -> None:
    # a name absent from its quote is refused for the quote first when the quote isn't there
    both = item("Nobody", "Nothing", "not on the page")
    name_first = item("Nobody", "Nothing", JANE)

    found = validate_people({"people": [both, name_first]}, PAGES)

    assert [d.cause for d in found.discards] == [
        PersonDiscardCause.QUOTE_NOT_IN_PAGE,
        PersonDiscardCause.NAME_NOT_IN_QUOTE,
    ]


def test_a_repeat_of_the_same_page_and_name_key_is_dropped_silently() -> None:
    parsed = {
        "people": [
            item("Jane Roe", "Managing Partner", JANE),
            item("JANE  ROE", "Managing Partner", JANE),  # refused: not in the quote
            item("Jane Roe", "Partner", JANE),  # same key: dropped, whatever the role
            item("Jane Roe", "Managing Partner", "quote not there"),  # refused: still recorded
            item("Jane Roe", "Managing Partner", "Jane Roe (Managing Partner)", ABOUT),
        ]
    }

    found = validate_people(parsed, PAGES)

    assert [(p.page_id, p.role) for p in found.people] == [
        (TEAM, "Managing Partner"),
        (ABOUT, "Managing Partner"),
    ]
    assert [d.cause for d in found.discards] == [
        PersonDiscardCause.NAME_NOT_IN_QUOTE,  # "JANE ROE" isn't in Jane's quote
        PersonDiscardCause.QUOTE_NOT_IN_PAGE,
    ]


@pytest.mark.parametrize(
    "parsed",
    [
        {},
        {"people": "Jane"},
        {"people": ["Jane"]},
        {"people": [{"name": "Jane Roe", "role": "Owner", "block_id": TEAM}]},
        {"people": [{"name": 7, "role": "Owner", "block_id": TEAM, "quote": JANE}]},
    ],
)
def test_an_answer_without_the_schemas_shape_raises(parsed: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="extract_people answer"):
        validate_people(parsed, PAGES)


# office_from_quote -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "lead_country", "office"),
    [
        (JANE, "US", Location("US", "TX")),  # the Lead's country
        ("Ann Lee, Partner, London, UK", "GB", Location("GB")),
        ("Ann Lee, Partner, London, UK", "US", Location("GB")),  # UK is stricter than the US
        ("Bob Ray, COO, Austin, TX", "GB", None),  # a US office would loosen a UK Lead's rules
        ("Bob Ray, COO, Toronto, Canada", "US", None),  # no place read
        ("Bob Ray, COO, Austin, TX", None, None),  # no Lead country
        ("Ann Lee, Partner, London, UK", "CA", None),  # the Lead's country has no Market
        ("Jane Roe, Managing Partner", "US", None),
        ("Jane Roe, Managing Partner, Austin, TX and London, UK", "US", None),  # both countries
    ],
)
def test_the_office_is_read_from_the_quote_and_never_loosens_the_rules(
    quote: str, lead_country: str | None, office: Location | None
) -> None:
    assert office_from_quote(quote, lead_country, markets()) == office


def test_an_office_in_a_market_that_isnt_enabled_isnt_kept() -> None:
    from dataclasses import replace

    disabled = tuple(
        replace(market, enabled=False) if market.country_code == "GB" else market
        for market in markets()
    )

    assert office_from_quote("Ann Lee, Partner, London, UK", "US", disabled) is None
    assert office_from_quote("Ann Lee, Partner, London, UK", "GB", disabled) == Location("GB")


# lawyer_count ------------------------------------------------------------------------------------


def person(name: str, role: str, page_id: str = TEAM) -> Person:
    return Person(page_id, TEAM_URL, name, role, f"{name}, {role}")


def test_the_lawyer_count_is_distinct_people_in_lawyer_roles() -> None:
    people = [
        person("Jane Roe", "Managing Partner"),
        person("Alex Poe", "Associate"),
        person("Pat Moe", "Paralegal"),
        person("Kim Doe", "Legal Assistant"),
        person("Jane  ROE", "Managing Partner", ABOUT),  # the same person on another page
        person("Lou Fay", "Office Manager"),
    ]

    assert lawyer_count(people) == 2
    assert lawyer_count([]) == 0


# reconcile_people --------------------------------------------------------------------------------

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def live(row_id: str, contact_id: str, quote: str, minutes: int = 0, url: str = TEAM_URL) -> Any:
    return LivePersonEvidence(row_id, contact_id, url, quote, T0 + timedelta(minutes=minutes))


def proposal(contact_id: str, quote: str, page_id: str = TEAM, url: str = TEAM_URL) -> Any:
    return PersonQuote(contact_id, page_id, url, quote)


def test_an_identical_quote_confirms_and_a_new_one_is_inserted() -> None:
    texts = {TEAM_URL: TEAM_TEXT}
    plan = reconcile_people([live("E1", C1, JANE)], [proposal(C1, JANE), proposal(C2, ALEX)], texts)

    assert plan == PeoplePlan(("E1",), (proposal(C2, ALEX),), (), ())


def test_a_quote_still_on_the_page_is_confirmed_without_a_proposal() -> None:
    plan = reconcile_people([live("E1", C1, JANE)], [], {TEAM_URL: TEAM_TEXT})

    assert plan == PeoplePlan(("E1",), (), (), ())


def test_a_gone_quote_is_superseded_by_the_contacts_new_quote_or_retracted() -> None:
    texts = {TEAM_URL: "Our people\nJane Roe, Managing Partner, Dallas, TX\nAlex Poe, Associate"}
    new_jane = proposal(C1, "Jane Roe, Managing Partner, Dallas, TX")
    rows = [live("E1", C1, JANE, 0), live("E2", C2, "Pat Moe, Paralegal", 1)]

    plan = reconcile_people(rows, [new_jane], texts)

    assert plan == PeoplePlan((), (), (PersonSuperseding("E1", new_jane),), ("E2",))


def test_quotes_pair_only_within_their_page_and_contact() -> None:
    texts = {TEAM_URL: "nothing left", ABOUT_URL: ABOUT_TEXT}
    other_contact = proposal(C2, "Jane Roe (Managing Partner)", ABOUT, ABOUT_URL)
    rows = [
        live("E1", C1, JANE, 0),
        live("E2", C1, "old", 1, "https://smith-law.com/contact"),  # a page not sent: untouched
    ]

    plan = reconcile_people(rows, [other_contact], texts)

    assert plan == PeoplePlan((), (other_contact,), (), ("E1",))


def test_gone_rows_pair_oldest_first() -> None:
    texts = {TEAM_URL: "Jane Roe, Owner\nJane Roe, Owner and Partner"}
    first, second = proposal(C1, "Jane Roe, Owner"), proposal(C1, "Jane Roe, Owner and Partner")
    rows = [live("E2", C1, "gone two", 5), live("E1", C1, "gone one", 0), live("E3", C1, "x", 9)]

    plan = reconcile_people(rows, [first, second], texts)

    assert plan.supersede == (PersonSuperseding("E1", first), PersonSuperseding("E2", second))
    assert plan.retract == ("E3",)


def test_a_proposal_on_a_page_not_sent_raises() -> None:
    with pytest.raises(ValueError, match="wasn't sent"):
        reconcile_people([], [proposal(C1, JANE)], {})


# qualified_away and decision_maker (review pass 1) -----------------------------------------------


def law() -> SegmentDef:
    text = (ENGINE_ROOT / "seed" / "segments.yaml").read_text(encoding="utf-8")
    return next(s for s in validate_segments(seed_yaml.parse(text)) if s.key == "law_private_ai")


@pytest.mark.parametrize(
    ("quote", "role", "away"),
    [
        ("Jane Roe, Assistant to the Managing Partner", "Managing Partner", True),
        ("Jane Roe, Former Managing Partner", "Managing Partner", True),
        ("Jane Roe, retired Managing Partner", "Managing Partner", True),
        ("Jane Roe, PA to the Managing Partner, Austin", "Managing Partner", True),
        ("Jane Roe, Managing Partner", "Managing Partner", False),
        ("Jane Roe, Managing Partner (Assistant: Bo Day)", "Managing Partner", False),
        # only the three words just before the role count
        ("Jane Roe, assistant coach and now our Managing Partner", "Managing Partner", False),
    ],
)
def test_a_quote_can_qualify_the_role_away(quote: str, role: str, away: bool) -> None:
    person = Person(TEAM, TEAM_URL, "Jane Roe", role, quote)

    assert qualified_away(person) is away
    assert decision_maker(person, law()) == (None if away else RoleMatch("Managing Partner", 0))


def test_a_person_qualified_away_isnt_a_lawyer_listed() -> None:
    people = [
        Person(TEAM, TEAM_URL, "Jane Roe", "Partner", "Jane Roe, Former Partner"),
        Person(TEAM, TEAM_URL, "Alex Poe", "Associate", ALEX),
    ]

    assert lawyer_count(people) == 1
