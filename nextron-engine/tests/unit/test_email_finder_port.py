"""The email-finder port's values (``nextron.ports.email_finder``) and its scripted fakes
(``tests.fakes.email_finder``): each value's checks, with no value echoed in an error, the
names and addresses kept out of every ``repr``, and the fakes' script, default answer and call
records."""

import pytest

from nextron.ports.email_finder import (
    DomainPerson,
    DomainSearchAnswer,
    DomainSearchPort,
    EmailFinderPort,
    FinderAnswer,
    FinderQuery,
)
from nextron.ports.errors import AdapterError
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder

DOMAIN = "roe-partners.example"
FIRST = "Juliana"
LAST = "Ravensworth"
ADDRESS = "juliana.ravensworth@roe-partners.example"


# FinderQuery ------------------------------------------------------------------------------------


def test_a_query_keeps_its_fields_and_shows_no_name_in_its_repr() -> None:
    query = FinderQuery(DOMAIN, FIRST, LAST)

    assert (query.domain, query.first_name, query.last_name) == (DOMAIN, FIRST, LAST)
    assert FIRST not in repr(query) and LAST not in repr(query)
    assert DOMAIN in repr(query)


@pytest.mark.parametrize(
    "fields",
    [
        (None, FIRST, LAST),
        (DOMAIN, 7, LAST),
        (DOMAIN, FIRST, b"Ravensworth"),
        (DOMAIN, FIRST, None),
    ],
)
def test_a_query_field_that_is_not_a_str_is_a_type_error(fields: tuple[object, ...]) -> None:
    with pytest.raises(TypeError) as raised:
        FinderQuery(*fields)  # type: ignore[arg-type]

    assert FIRST not in str(raised.value) and LAST not in str(raised.value)


@pytest.mark.parametrize(
    "fields", [("", FIRST, LAST), (DOMAIN, "", LAST), (DOMAIN, FIRST, "  "), (" \t", FIRST, LAST)]
)
def test_a_blank_query_field_is_a_value_error(fields: tuple[str, str, str]) -> None:
    with pytest.raises(ValueError) as raised:
        FinderQuery(*fields)

    assert FIRST not in str(raised.value) and LAST not in str(raised.value)


# FinderAnswer -----------------------------------------------------------------------------------


def test_an_answer_shows_no_address_in_its_repr() -> None:
    answer = FinderAnswer(ADDRESS)

    assert (answer.email, answer.claimed) == (ADDRESS, False)
    assert ADDRESS not in repr(answer)


def test_a_claimed_answer_carries_no_address() -> None:
    assert FinderAnswer(None, claimed=True).claimed is True

    with pytest.raises(ValueError) as raised:
        FinderAnswer(ADDRESS, claimed=True)

    assert ADDRESS not in str(raised.value)


@pytest.mark.parametrize(
    ("email", "claimed", "error"),
    [
        (42, False, TypeError),
        (b"x@y.example", False, TypeError),
        ("", False, ValueError),
        ("   ", False, ValueError),
        (None, 1, TypeError),
        (None, "yes", TypeError),
    ],
)
def test_a_bad_answer_is_refused(email: object, claimed: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        FinderAnswer(email, claimed)  # type: ignore[arg-type]


# DomainPerson and DomainSearchAnswer ------------------------------------------------------------


def test_a_domain_answer_shows_no_address_or_name_in_its_repr() -> None:
    person = DomainPerson(ADDRESS, FIRST, LAST, True)
    answer = DomainSearchAnswer("{first}.{last}", (person,))

    for shown in (repr(person), repr(answer)):
        assert ADDRESS not in shown and FIRST not in shown and LAST not in shown
    assert "{first}.{last}" in repr(answer)


def test_a_person_may_have_no_names() -> None:
    person = DomainPerson(ADDRESS, None, None, False)

    assert (person.first_name, person.last_name, person.personal) == (None, None, False)


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ((None, FIRST, LAST, True), TypeError),
        (("", FIRST, LAST, True), ValueError),
        ((ADDRESS, 7, LAST, True), TypeError),
        ((ADDRESS, FIRST, ["Ravensworth"], True), TypeError),
        ((ADDRESS, FIRST, LAST, "personal"), TypeError),
        ((ADDRESS, FIRST, LAST, None), TypeError),
    ],
)
def test_a_bad_person_is_refused(fields: tuple[object, ...], error: type[Exception]) -> None:
    with pytest.raises(error) as raised:
        DomainPerson(*fields)  # type: ignore[arg-type]

    assert ADDRESS not in str(raised.value) and FIRST not in str(raised.value)


@pytest.mark.parametrize(
    ("pattern", "people"),
    [
        (7, ()),
        (["{first}"], ()),
        (None, [DomainPerson(ADDRESS, None, None, True)]),
        (None, (ADDRESS,)),
        (None, None),
    ],
)
def test_a_bad_domain_answer_is_a_type_error(pattern: object, people: object) -> None:
    with pytest.raises(TypeError):
        DomainSearchAnswer(pattern, people)  # type: ignore[arg-type]


# The fakes --------------------------------------------------------------------------------------


def test_the_fakes_build_with_no_arguments_and_satisfy_their_ports() -> None:
    assert isinstance(ScriptedFinder(), EmailFinderPort)
    assert isinstance(ScriptedDomainSearch(), DomainSearchPort)


def test_the_scripted_finder_answers_in_order_then_nothing_and_records_every_call() -> None:
    refused = AdapterError(True, "hunter_unavailable", "Hunter answered with HTTP 503")
    finder = ScriptedFinder(FinderAnswer(ADDRESS), refused, FinderAnswer(None, claimed=True))
    query = FinderQuery(DOMAIN, FIRST, LAST)

    assert finder.find_email(query) == FinderAnswer(ADDRESS)
    with pytest.raises(AdapterError) as raised:
        finder.find_email(query)
    assert raised.value is refused
    assert finder.find_email(query) == FinderAnswer(None, claimed=True)
    assert finder.find_email(query) == FinderAnswer(None)  # the script is exhausted

    assert finder.calls == [query] * 4


def test_the_scripted_domain_search_answers_in_order_then_nobody_and_records_every_call() -> None:
    refused = AdapterError(False, "hunter_refused", "Hunter refused the request with HTTP 401")
    answer = DomainSearchAnswer("{f}{last}", (DomainPerson(ADDRESS, FIRST, LAST, True),))
    search = ScriptedDomainSearch(answer, refused)

    assert search.domain_search(DOMAIN) == answer
    with pytest.raises(AdapterError):
        search.domain_search("other.example")
    assert search.domain_search(DOMAIN) == DomainSearchAnswer(None, ())

    assert search.calls == [DOMAIN, "other.example", DOMAIN]
