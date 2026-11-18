"""The rules of people extraction (FR-10, FR-13, FR-51, AD-7, AD-17, AD-18; Story 2.10).
Standard library and ``nextron.domain`` only.

After the Signals, the ``extract_lead`` job (``nextron.app.research.people``) sends the Lead's
team, about and contact pages to Claude's ``extract_people`` route once. The answer is only a
proposal: these rules decide which people it names are real (their name and role are in a quote
that is on the cited page), where a person's office is, how many lawyers a firm lists, and how
the new quotes meet the Lead's earlier person Evidence. Nothing here reads or writes the
database.

**Constants.** ``SELECT_PRIMARY_CONTACT_JOB`` (``select_primary_contact``: the job that picks
the Lead's Primary Contact once people are extracted), ``ROUTE`` (``extract_people``),
``BUDGET_CLASS`` (``BudgetClass.BLOCKABLE``: the
class of every ``extract_people`` route candidate, which a contract test checks),
``PEOPLE_PAGE_KINDS`` (``team``, ``about`` and ``contact``), ``LAWYERS_LISTED`` (``lawyers
listed``: the ``sought`` of a Law Lead's Count Evidence) and ``PERSON_DISCARDED``
(``person_discarded``: the ``events`` row's ``to_status`` for a refused person).
``PersonDiscardCause`` is that row's ``cause``.

**``validate_people(parsed, pages) -> PeopleValidation(people, discards)``** reads an
``extract_people`` answer (``LlmOk.parsed``: ``people``, each with ``name``, ``role``,
``block_id`` and ``quote``). ``pages`` maps each page id sent to its ``SentPage`` (URL,
``fetched_at``, full stored text). Each proposal is checked in this order, and the first
failure discards it (``PersonDiscard(cause, page_id)``; ``page_id`` is the cited page when it
was one of the pages sent, else ``None``):

1. its ``block_id`` is a page sent (``unknown_block``);
2. its quote, normalised (``quotes.normalise``), isn't empty (``quote_empty``), has at most
   ``MAX_QUOTE`` (300) characters (``quote_too_long``) and passes ``quote_in`` against the
   page's full stored text (``quote_not_in_page``);
3. its name and its role, each normalised, pass ``contacts.check_name`` (``name_invalid``) and
   ``contacts.check_role`` (``role_invalid``);
4. the name, then the role, is a case-sensitive substring of the normalised quote with no
   letter or digit just before or after it, so "Ann Lee" isn't in "Joann Lee, Managing
   Partner" (``name_not_in_quote``, ``role_not_in_quote``).

A kept proposal that repeats an earlier kept one (the same page and ``normalize_name`` key) is
dropped without a record. A ``Person(page_id, url, name, role, quote)`` is one kept proposal, in
the answer's order. An answer that doesn't have the schema's shape raises ``ValueError``.

**``office_from_quote(quote, lead_country, markets) -> Location | None``**: the place
``geo.place_in`` reads in the person's own quote, kept only when its country is the Lead's
country, or when both countries have an enabled Market (``markets.market_for_country``) and the
place's Market is strictly stricter (``contact_market.strictness``) than the Lead's. A misread
can therefore never loosen a Contact's rules.

**``qualified_away(person) -> bool``**: whether the quote qualifies the role away: one of the
up to three words of the quote just before the role's first whole occurrence (read with
``contacts.role_words``) is one of ``contacts.NEVER_WORDS`` ("Jane Roe, Assistant to the
Managing Partner", "Jane Roe, Former Managing Partner"). **``decision_maker(person, segment)
-> RoleMatch | None``**: ``None`` for a person qualified away, else
``contacts.role_match(person.role, segment)``.

**``lawyer_count(people) -> int``**: the number of distinct ``normalize_name`` keys among the
people whose role is a lawyer's (``contacts.is_lawyer_role``) and who aren't qualified away,
whatever their decision-maker status.

**``reconcile_people(live, proposed, texts) -> PeoplePlan``** meets the Lead's live person
Evidence with the new quotes, with ``extraction.reconcile``'s four rules, per page and Contact.
``texts`` maps the URL of each page sent to its new full stored text; a live row
(``LivePersonEvidence(id, contact_id, url, quote, created_at)``) of another page is out of scope
and untouched. A proposal is a ``PersonQuote(contact_id, page_id, url, quote)``. Within each
(URL, Contact):

1. each proposal whose quote equals a live row's quote confirms that row (one row each);
2. each other live row whose quote is still in the page's text (``quote_in``) is confirmed;
3. the remaining live rows (their quote is gone), oldest first (``created_at``, then ``id``),
   are paired with the remaining proposals in order: the new row supersedes the old;
4. proposals left over are inserted, and live rows left over are retracted.

``PeoplePlan(confirm, insert, supersede, retract)``: the live ids to confirm, the proposals to
insert, ``PersonSuperseding(old_id, new)`` pairs and the live ids to retract; each in the order
of the pages (URL), then the Contacts (id), then the steps above. A proposal whose URL isn't in
``texts`` raises ``ValueError``.

Names, roles, quotes and page text never show in a ``repr``.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from nextron.domain.budget import BudgetClass
from nextron.domain.contact_market import strictness
from nextron.domain.contacts import (
    NEVER_WORDS,
    InvalidContact,
    RoleMatch,
    check_name,
    check_role,
    is_lawyer_role,
    role_match,
    role_words,
)
from nextron.domain.crawl import PageKind
from nextron.domain.extraction import MAX_QUOTE, SentPage
from nextron.domain.geo import Location, place_in
from nextron.domain.identity import normalize_name
from nextron.domain.markets import MarketDef, market_for_country
from nextron.domain.quotes import normalise, quote_in
from nextron.domain.segments import SegmentDef

SELECT_PRIMARY_CONTACT_JOB: Final = "select_primary_contact"
ROUTE: Final = "extract_people"
BUDGET_CLASS: Final = BudgetClass.BLOCKABLE
PEOPLE_PAGE_KINDS: Final = (PageKind.TEAM, PageKind.ABOUT, PageKind.CONTACT)
LAWYERS_LISTED: Final = "lawyers listed"
PERSON_DISCARDED: Final = "person_discarded"
QUALIFIER_WORDS: Final = 3  # the words before a role that may qualify it away


class PersonDiscardCause(StrEnum):
    UNKNOWN_BLOCK = "unknown_block"
    QUOTE_EMPTY = "quote_empty"
    QUOTE_TOO_LONG = "quote_too_long"
    QUOTE_NOT_IN_PAGE = "quote_not_in_page"
    NAME_INVALID = "name_invalid"
    ROLE_INVALID = "role_invalid"
    NAME_NOT_IN_QUOTE = "name_not_in_quote"
    ROLE_NOT_IN_QUOTE = "role_not_in_quote"


# Values ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Person:
    """One kept person: the cited page's id and URL, the normalised name, role and quote."""

    page_id: str
    url: str
    name: str = field(repr=False)
    role: str = field(repr=False)
    quote: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PersonDiscard:
    """One refused person: why, and the cited page when it was sent."""

    cause: PersonDiscardCause
    page_id: str | None


@dataclass(frozen=True, slots=True)
class PeopleValidation:
    """What ``validate_people`` kept and refused."""

    people: tuple[Person, ...]
    discards: tuple[PersonDiscard, ...]


@dataclass(frozen=True, slots=True)
class LivePersonEvidence:
    """A live person Evidence row in reconciliation: its id, Contact, page URL, normalised quote
    and when it was created."""

    id: str
    contact_id: str
    url: str
    quote: str = field(repr=False)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PersonQuote:
    """A new quote naming the Contact ``contact_id`` on the page ``page_id``."""

    contact_id: str
    page_id: str
    url: str
    quote: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PersonSuperseding:
    """A live row whose quote is gone, and the quote that replaces it."""

    old_id: str
    new: PersonQuote


@dataclass(frozen=True, slots=True)
class PeoplePlan:
    """What ``reconcile_people`` decided; see the module docstring."""

    confirm: tuple[str, ...]
    insert: tuple[PersonQuote, ...]
    supersede: tuple[PersonSuperseding, ...]
    retract: tuple[str, ...]


# Validation -----------------------------------------------------------------------------------


def _people(parsed: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = parsed.get("people")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        raise ValueError("the extract_people answer's people must be a list of objects")
    return items


def _text_field(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str):
        raise ValueError(f"the extract_people answer's {name} must be a str")
    return value


def _find_whole(part: str, text: str) -> int:
    """The index of the first occurrence of ``part`` in ``text`` with no letter or digit just
    before or after it, or ``-1``."""
    if not part:
        return -1
    start = text.find(part)
    while start != -1:
        end = start + len(part)
        before = text[start - 1] if start > 0 else " "
        after = text[end] if end < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return start
        start = text.find(part, start + 1)
    return -1


def _cause(name: str, role: str, quote: str, page: SentPage) -> PersonDiscardCause | None:
    """Steps 2 to 4 for a proposal on a page sent: the first refusal, or ``None``."""
    if not quote:
        return PersonDiscardCause.QUOTE_EMPTY
    if len(quote) > MAX_QUOTE:
        return PersonDiscardCause.QUOTE_TOO_LONG
    if not quote_in(quote, page.text):
        return PersonDiscardCause.QUOTE_NOT_IN_PAGE
    try:
        check_name(name)
    except InvalidContact:
        return PersonDiscardCause.NAME_INVALID
    try:
        check_role(role)
    except InvalidContact:
        return PersonDiscardCause.ROLE_INVALID
    if _find_whole(name, quote) == -1:
        return PersonDiscardCause.NAME_NOT_IN_QUOTE
    if _find_whole(role, quote) == -1:
        return PersonDiscardCause.ROLE_NOT_IN_QUOTE
    return None


def validate_people(parsed: Mapping[str, Any], pages: Mapping[str, SentPage]) -> PeopleValidation:
    """The people kept and refused; see the module docstring."""
    if not isinstance(parsed, Mapping):
        raise ValueError("the extract_people answer must be an object")
    kept: list[Person] = []
    discards: list[PersonDiscard] = []
    seen: set[tuple[str, str]] = set()
    for item in _people(parsed):
        block_id = _text_field(item, "block_id")
        name = normalise(_text_field(item, "name"))
        role = normalise(_text_field(item, "role"))
        quote = normalise(_text_field(item, "quote"))
        page = pages.get(block_id)
        if page is None:
            discards.append(PersonDiscard(PersonDiscardCause.UNKNOWN_BLOCK, None))
            continue
        cause = _cause(name, role, quote, page)
        if cause is not None:
            discards.append(PersonDiscard(cause, block_id))
            continue
        key = (block_id, normalize_name(name))
        if key in seen:  # a repeat is dropped without a record
            continue
        seen.add(key)
        kept.append(Person(block_id, page.url, name, role, quote))
    return PeopleValidation(tuple(kept), tuple(discards))


# Offices and counts ---------------------------------------------------------------------------


def office_from_quote(
    quote: str, lead_country: str | None, markets: Iterable[MarketDef]
) -> Location | None:
    """The person's office read from their quote, or ``None``; see the module docstring."""
    place = place_in(quote)
    if place is None:
        return None
    if place.country_code == lead_country:
        return place
    stored = tuple(markets)
    lead_market = market_for_country(lead_country, stored)
    place_market = market_for_country(place.country_code, stored)
    if (
        lead_market is not None
        and place_market is not None
        and strictness(place_market) > strictness(lead_market)
    ):
        return place
    return None


def qualified_away(person: Person) -> bool:
    """Whether the words just before the role in the person's quote take it away; see the
    module docstring."""
    start = _find_whole(person.role, person.quote)
    if start == -1:  # a kept person's role is in their quote
        return False
    before = role_words(person.quote[:start])[-QUALIFIER_WORDS:]
    return bool(NEVER_WORDS.intersection(before))


def decision_maker(person: Person, segment: SegmentDef) -> RoleMatch | None:
    """The person's decision-maker title in ``segment``, or ``None``; see the module
    docstring."""
    return None if qualified_away(person) else role_match(person.role, segment)


def lawyer_count(people: Iterable[Person]) -> int:
    """The number of distinct people whose role is a lawyer's; see the module docstring."""
    return len(
        {
            normalize_name(person.name)
            for person in people
            if is_lawyer_role(person.role) and not qualified_away(person)
        }
    )


# Reconciliation -------------------------------------------------------------------------------


def reconcile_people(
    live: Sequence[LivePersonEvidence],
    proposed: Sequence[PersonQuote],
    texts: Mapping[str, str],
) -> PeoplePlan:
    """Confirm, insert, supersede and retract, per page and Contact; see the module
    docstring."""
    groups: dict[tuple[str, str], tuple[list[LivePersonEvidence], list[PersonQuote]]] = {}
    for row in live:
        if row.url in texts:
            groups.setdefault((row.url, row.contact_id), ([], []))[0].append(row)
    for quote in proposed:
        if quote.url not in texts:
            raise ValueError("a proposal cites a page that wasn't sent")
        groups.setdefault((quote.url, quote.contact_id), ([], []))[1].append(quote)
    confirm: list[str] = []
    insert: list[PersonQuote] = []
    supersede: list[PersonSuperseding] = []
    retract: list[str] = []
    for (url, _contact_id), (rows, quotes) in sorted(groups.items()):
        rows = sorted(rows, key=lambda row: (row.created_at, row.id))
        left_rows: list[LivePersonEvidence] = []
        left_quotes = list(quotes)
        for row in rows:  # 1. an identical quote confirms the row
            same = next((each for each in left_quotes if each.quote == row.quote), None)
            if same is None:
                left_rows.append(row)
            else:
                left_quotes.remove(same)
                confirm.append(row.id)
        gone: list[LivePersonEvidence] = []
        for row in left_rows:  # 2. a quote still in the page is confirmed too
            if quote_in(row.quote, texts[url]):
                confirm.append(row.id)
            else:
                gone.append(row)
        for row, quote in zip(gone, left_quotes, strict=False):  # 3. supersede
            supersede.append(PersonSuperseding(row.id, quote))
        paired = min(len(gone), len(left_quotes))
        insert.extend(left_quotes[paired:])  # 4. the rest
        retract.extend(row.id for row in gone[paired:])
    return PeoplePlan(tuple(confirm), tuple(insert), tuple(supersede), tuple(retract))
