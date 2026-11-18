"""The rules of finding a Contact's email address (FR-14, FR-51, FR-57, AD-7, AD-9, AD-10, AD-16;
Story 2.13). Standard library and ``nextron.domain`` only.

The ``find_contact_email`` job (``nextron.app.research.finding``) tries four sources in
order and stops at the first address it can assign: an address published on the Lead's crawled
pages, a learned pattern, Hunter Domain Search, then Hunter Email Finder and Prospeo. These rules
read names, addresses and pages; nothing here reads or writes the database or calls a provider.

**Constants.** ``FIND_CONTACT_EMAIL_JOB`` (``find_contact_email``), ``BUDGET_CLASS``
(``BudgetClass.BLOCKABLE``: every paid step may be stopped by the budget), ``HUNTER_QUOTA``
(``hunter``) and ``PROSPEO_QUOTA`` (``prospeo``): the ``budget.QUOTAS`` names, which a test
checks. ``MAX_QUOTE`` (300, ``extraction.MAX_QUOTE``) and ``MAILTO_QUOTE_PREFIX`` (``mailto:``).

**Role addresses.** ``ROLE_LOCAL_PARTS`` holds the AC's six (info, contact, office, admin, team,
sales) and the extended list of Decision 6. ``is_role_address(email)``: the whole local part
(everything before the last ``@``, or the whole text when there is none), casefolded and with any
``+tag`` dropped, is in the list. A role address is never assigned, matched or learned from.

**Name tokens.** ``name_tokens(name) -> NameTokens(first, last) | None``:

1. The name is folded: casefolded, NFKD, combining marks dropped (so "José" gives "jose" and "ß"
   gives "ss"). An apostrophe (``'``, ``’``, ``ʼ``) or hyphen (``-``, ``‐``) between two letters
   is removed ("O'Neil" gives "oneil", "Smith-Jones" gives "smithjones"); every other run of
   characters that aren't letters splits tokens (digits too).
2. Honorifics (mr, mrs, ms, miss, dr, prof, sir, dame) and suffixes (jr, sr, ii, iii, iv, esq,
   qc, kc, phd, cpa) are dropped wherever they are.
3. One-letter middle tokens are dropped ("Jane Q. Roe" gives jane, roe); a one-letter first or
   last token is kept.
4. ``first`` is the first token left and ``last`` the last one (``None`` when only one is left).
   No token left gives ``None``.

**Patterns.** ``Pattern`` is the AC's seven: ``first.last``, ``flast``, ``first``, ``firstlast``,
``first_last``, ``last.first`` and ``f.last`` (``f`` is the first token's first letter).
``local_part(pattern, tokens)`` is that local part, ``None`` when the pattern needs a ``last``
that isn't there. ``address_for(pattern, tokens, domain)`` is ``<local>@<domain>`` through
``normalize_email``, ``None`` when there is no local part or the address is refused.
``fitting(local, tokens)``: the patterns whose local part equals ``local`` casefolded with any
``+tag`` dropped ("jane" with a first-only name fits ``first``). ``HUNTER_PATTERNS`` maps Hunter
Domain Search's seven pattern strings (``{first}.{last}``, ``{f}{last}``, ``{first}``,
``{first}{last}``, ``{first}_{last}``, ``{last}.{first}``, ``{f}.{last}``) to ``Pattern``;
``hunter_pattern(text)`` gives ``None`` for anything else. ``PatternSource`` is where a stored
domain pattern came from: ``published`` or ``hunter_domain_search``.

**Addresses in text.** ``addresses_in(text)``: the email-looking substrings of plain text
(``local@domain.tld``, the local part letters, digits and ``. _ % + -``), each through
``normalize_email``, refused ones dropped, distinct, in order. Obfuscated forms such as
"jane [at] roe.law" aren't read (Decision 3).

**Published matches (Decision 4).** A page is ``PageAddresses(page_id, url, fetched_at, text,
mailto)``: its stored text (``None`` once purged) and its stored ``mailto:`` addresses; pages are
given in page order. Every address on a page (in the text, or in the mailto list, each through
``normalize_email``) that isn't a role address may match the Contact by one of two rules, and an
initial alone never matches, because a published address is sent without verification:

- **Rule A** (``name_in_address``), which needs ``tokens.last``: the local part (casefolded,
  ``+tag`` dropped) split on ``.``, ``_`` and ``-`` holds both ``first`` and ``last`` as whole
  parts, or the local part with those separators removed equals ``first+last`` or ``last+first``.
  It applies to text and mailto addresses alike.
- **Rule B** (``same_line``): on one line of the stored text (split on ``\\n``) the Contact's
  stored ``name`` (compared through ``normalize_name``, as whole words) and exactly one distinct
  non-role address both appear, and one quote of at most ``MAX_QUOTE`` characters holds both.

**Quotes.** For an address in the text, the quote is its line, stripped, when that has at most
``MAX_QUOTE`` characters; otherwise the window of at most ``MAX_QUOTE`` characters of the line
around the address (rule A) or around the address and the name (rule B), cut on the original
text. Every text quote passes ``quotes.quote_in`` against the page's text and normalises to at
most ``MAX_QUOTE`` characters; a candidate whose quote doesn't is dropped. For an address only
in the mailto list the quote is ``mailto:<address>``. An address in both uses the text quote.
``address_quote(address, text, mailto)`` (Story 2.14's find-on-site) is that quote for one
stored address: its first occurrence in ``text`` (``None`` for purged text) by the same rule,
else ``mailto:<address>`` when the mailto list holds it, else ``None``.

``published_matches(tokens, name, pages, *, mail_domain)``: one ``PublishedMatch(address,
page_id, url, fetched_at, rule, quote)`` per address, its best, ordered by rule A first, then
addresses at the mail domain first (the address's domain, or its registrable domain, equals
``mail_domain``; with no mail domain every address counts as at it), then page order, then
position in the page (text addresses by offset; mailto-only addresses after the page's text
addresses, in list order). An address's best is its first by rule, then page, then position.
``published_match(..., exclude=())``: the first of those whose address isn't in ``exclude``
(compared after ``normalize_email``).

**Pattern learning (Decision 7).** ``learn_pattern(examples)``: each example is an address or
local part with its owner's ``NameTokens``. The pattern fitting the most examples wins when at
least two examples fit it and it fits strictly more than any other pattern; otherwise ``None``.

**Outcomes and steps.** ``EmailSearchOutcome``: ``found``, ``not_found``, ``quota_reached``,
``suppressed``, ``lead_closed``. ``FinderStep``: ``published``, ``pattern``,
``hunter_domain_search``, ``hunter``, ``prospeo``.

**Provenance (Decision 2).** ``provenance_for(step, *, page_url, pattern, pattern_source) ->
ProvenanceFor(source, detail)``: ``published`` gives ``website`` with the page URL; ``pattern``
gives ``pattern`` with "first.last pattern learned from published addresses" or "first.last
pattern from Hunter Domain Search"; ``hunter_domain_search``, ``hunter`` and ``prospeo`` give
``finder`` with "Hunter Domain Search", "Hunter Email Finder" and "Prospeo". A missing argument
the step needs raises ``ValueError``.

**Tried providers.** ``contacts.email_search_tried`` holds the paid finders already asked for a
Contact, joined by ``SEARCH_SEPARATOR`` (``,``). ``tried_of(text)`` reads it (stripped, empty
parts dropped, distinct, in order; ``None`` gives ``()``); ``tried_text(tried)`` writes it
(``None`` when empty) and raises ``ValueError`` for a name that isn't ``hunter`` or ``prospeo``.

A non-``str`` where text is expected, or a wrong value type, raises ``TypeError``. Names,
addresses, local parts, page text and quotes never show in a ``repr``.
"""

import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.budget import BudgetClass
from nextron.domain.contacts import ProvenanceSource
from nextron.domain.extraction import MAX_QUOTE as EXTRACTION_MAX_QUOTE
from nextron.domain.identity import (
    IdentityError,
    normalize_email,
    normalize_name,
    registrable_domain,
)
from nextron.domain.quotes import normalise, quote_in

FIND_CONTACT_EMAIL_JOB: Final = "find_contact_email"
CONTACT_ID_FIELD: Final = "contact_id"  # the job payload's Contact
BUDGET_CLASS: Final = BudgetClass.BLOCKABLE
HUNTER_QUOTA: Final = "hunter"
PROSPEO_QUOTA: Final = "prospeo"
FINDER_PROVIDERS: Final = (HUNTER_QUOTA, PROSPEO_QUOTA)
MAX_QUOTE: Final = EXTRACTION_MAX_QUOTE  # 300
MAILTO_QUOTE_PREFIX: Final = "mailto:"
SEARCH_SEPARATOR: Final = ","
TAG_SEPARATOR: Final = "+"

ROLE_LOCAL_PARTS: Final = frozenset(
    {
        # the AC's six
        "info",
        "contact",
        "office",
        "admin",
        "team",
        "sales",
        # Decision 6
        "hello",
        "enquiries",
        "enquiry",
        "inquiries",
        "support",
        "help",
        "mail",
        "email",
        "reception",
        "accounts",
        "billing",
        "careers",
        "jobs",
        "hr",
        "marketing",
        "press",
        "media",
        "privacy",
        "legal",
        "webmaster",
        "noreply",
        "no-reply",
        "bookings",
        "service",
        "admissions",
    }
)
HONORIFICS: Final = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "dame"})
SUFFIXES: Final = frozenset({"jr", "sr", "ii", "iii", "iv", "esq", "qc", "kc", "phd", "cpa"})
JOINERS: Final = frozenset("'’ʼ-‐")  # removed between two letters
LETTERS: Final = re.compile(r"[^\W\d_]+")
LOCAL_SEPARATORS: Final = re.compile(r"[._-]")
ADDRESS: Final = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")


class Pattern(StrEnum):
    FIRST_DOT_LAST = "first.last"
    FLAST = "flast"
    FIRST = "first"
    FIRSTLAST = "firstlast"
    FIRST_UNDERSCORE_LAST = "first_last"
    LAST_DOT_FIRST = "last.first"
    F_DOT_LAST = "f.last"


class PatternSource(StrEnum):
    PUBLISHED = "published"
    HUNTER_DOMAIN_SEARCH = "hunter_domain_search"


class PublishedRule(StrEnum):
    NAME_IN_ADDRESS = "name_in_address"  # rule A
    SAME_LINE = "same_line"  # rule B


class EmailSearchOutcome(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    QUOTA_REACHED = "quota_reached"
    SUPPRESSED = "suppressed"
    LEAD_CLOSED = "lead_closed"


class FinderStep(StrEnum):
    PUBLISHED = "published"
    PATTERN = "pattern"
    HUNTER_DOMAIN_SEARCH = "hunter_domain_search"
    HUNTER = "hunter"
    PROSPEO = "prospeo"


HUNTER_PATTERNS: Final[Mapping[str, Pattern]] = MappingProxyType(
    {
        "{first}.{last}": Pattern.FIRST_DOT_LAST,
        "{f}{last}": Pattern.FLAST,
        "{first}": Pattern.FIRST,
        "{first}{last}": Pattern.FIRSTLAST,
        "{first}_{last}": Pattern.FIRST_UNDERSCORE_LAST,
        "{last}.{first}": Pattern.LAST_DOT_FIRST,
        "{f}.{last}": Pattern.F_DOT_LAST,
    }
)
PATTERN_SOURCE_DETAIL: Final[Mapping[PatternSource, str]] = MappingProxyType(
    {
        PatternSource.PUBLISHED: "pattern learned from published addresses",
        PatternSource.HUNTER_DOMAIN_SEARCH: "pattern from Hunter Domain Search",
    }
)
FINDER_DETAIL: Final[Mapping[FinderStep, str]] = MappingProxyType(
    {
        FinderStep.HUNTER_DOMAIN_SEARCH: "Hunter Domain Search",
        FinderStep.HUNTER: "Hunter Email Finder",
        FinderStep.PROSPEO: "Prospeo",
    }
)
RULE_RANK: Final[Mapping[PublishedRule, int]] = MappingProxyType(
    {PublishedRule.NAME_IN_ADDRESS: 0, PublishedRule.SAME_LINE: 1}
)


# Values ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NameTokens:
    """A name's first and last tokens, folded; ``last`` is ``None`` for a one-token name."""

    first: str = field(repr=False)
    last: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.first, str) or not self.first:
            raise TypeError("first must be a non-empty str")
        if self.last is not None and (not isinstance(self.last, str) or not self.last):
            raise TypeError("last must be a non-empty str or None")


@dataclass(frozen=True, slots=True)
class PageAddresses:
    """One crawled page: its id, URL, when it was fetched, its stored text (``None`` once
    purged) and its stored ``mailto:`` addresses."""

    page_id: str
    url: str
    fetched_at: datetime
    text: str | None = field(repr=False)
    mailto: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _check_str(self.page_id, "page_id")
        _check_str(self.url, "url")
        if not isinstance(self.fetched_at, datetime):
            raise TypeError("fetched_at must be a datetime")
        if self.text is not None:
            _check_str(self.text, "text")
        if not isinstance(self.mailto, tuple) or not all(
            isinstance(item, str) for item in self.mailto
        ):
            raise TypeError("mailto must be a tuple of str")


@dataclass(frozen=True, slots=True)
class PublishedMatch:
    """A published address that matches the Contact: the page, the rule and the quote."""

    address: str = field(repr=False)
    page_id: str
    url: str
    fetched_at: datetime
    rule: PublishedRule
    quote: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProvenanceFor:
    """The Provenance source and detail of an assigned address."""

    source: ProvenanceSource
    detail: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    rank: int
    page: int
    position: int
    match: PublishedMatch


# Helpers --------------------------------------------------------------------------------------


def _check_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def _check_tokens(tokens: object) -> NameTokens:
    if not isinstance(tokens, NameTokens):
        raise TypeError("tokens must be NameTokens")
    return tokens


def _local_key(local: str) -> str:
    """A local part casefolded, with any ``+tag`` dropped."""
    return local.casefold().split(TAG_SEPARATOR, 1)[0]


def _local_of(email: str) -> str:
    """Everything before the last ``@``, or the whole text when there is none."""
    return email.rsplit("@", 1)[0]


def _normalized(text: str) -> str | None:
    try:
        return normalize_email(text)
    except IdentityError:
        return None


# Role addresses -------------------------------------------------------------------------------


def is_role_address(email: str) -> bool:
    """Whether the local part, casefolded and with any ``+tag`` dropped, is a role name."""
    return _local_key(_local_of(_check_str(email, "email"))) in ROLE_LOCAL_PARTS


# Name tokens ----------------------------------------------------------------------------------


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _without_joiners(text: str) -> str:
    kept = []
    for index, ch in enumerate(text):
        if (
            ch in JOINERS
            and 0 < index < len(text) - 1
            and text[index - 1].isalpha()
            and text[index + 1].isalpha()
        ):
            continue
        kept.append(ch)
    return "".join(kept)


def name_tokens(name: str) -> NameTokens | None:
    """The name's first and last tokens; see the module docstring."""
    words = LETTERS.findall(_without_joiners(_fold(_check_str(name, "name"))))
    words = [word for word in words if word not in HONORIFICS and word not in SUFFIXES]
    words = [
        word for index, word in enumerate(words) if len(word) > 1 or index in (0, len(words) - 1)
    ]
    if not words:
        return None
    return NameTokens(words[0], words[-1] if len(words) > 1 else None)


# Patterns -------------------------------------------------------------------------------------


def local_part(pattern: Pattern, tokens: NameTokens) -> str | None:
    """The pattern's local part for ``tokens``, ``None`` when it needs a missing ``last``."""
    if not isinstance(pattern, Pattern):
        raise TypeError("pattern must be a Pattern")
    first, last = _check_tokens(tokens).first, tokens.last
    if pattern is Pattern.FIRST:
        return first
    if last is None:
        return None
    match pattern:
        case Pattern.FIRST_DOT_LAST:
            return f"{first}.{last}"
        case Pattern.FLAST:
            return f"{first[0]}{last}"
        case Pattern.FIRSTLAST:
            return f"{first}{last}"
        case Pattern.FIRST_UNDERSCORE_LAST:
            return f"{first}_{last}"
        case Pattern.LAST_DOT_FIRST:
            return f"{last}.{first}"
        case Pattern.F_DOT_LAST:
            return f"{first[0]}.{last}"
    raise AssertionError(pattern)  # pragma: no cover - every Pattern is handled


def address_for(pattern: Pattern, tokens: NameTokens, domain: str) -> str | None:
    """``<local part>@<domain>`` through ``normalize_email``, ``None`` when refused."""
    _check_str(domain, "domain")
    local = local_part(pattern, tokens)
    if local is None:
        return None
    return _normalized(f"{local}@{domain}")


def fitting(local: str, tokens: NameTokens) -> frozenset[Pattern]:
    """The patterns whose local part for ``tokens`` equals ``local``; see the module
    docstring."""
    key = _local_key(_check_str(local, "local"))
    _check_tokens(tokens)
    return frozenset(pattern for pattern in Pattern if local_part(pattern, tokens) == key)


def hunter_pattern(text: str | None) -> Pattern | None:
    """The ``Pattern`` of a Hunter Domain Search pattern string, ``None`` for anything else."""
    if text is None:
        return None
    return HUNTER_PATTERNS.get(_check_str(text, "text").strip())


def learn_pattern(examples: Iterable[tuple[str, NameTokens]]) -> Pattern | None:
    """The pattern most examples fit, when at least two do and no other pattern fits as many."""
    counts = dict.fromkeys(Pattern, 0)
    for example in examples:
        if not isinstance(example, tuple) or len(example) != 2:
            raise TypeError("each example must be an (address, NameTokens) pair")
        address, tokens = example
        for pattern in fitting(_local_of(_check_str(address, "address")), tokens):
            counts[pattern] += 1
    ranked = sorted(counts.values(), reverse=True)
    best = ranked[0]
    if best < 2 or ranked[1] == best:
        return None
    return next(pattern for pattern, count in counts.items() if count == best)


# Addresses in text ----------------------------------------------------------------------------


def _occurrences(text: str) -> list[tuple[str, int, int]]:
    """Each accepted address in ``text``, normalised, with its offsets, in order."""
    found = []
    for hit in ADDRESS.finditer(text):
        start, end = hit.span()
        while start < end and text[start] == ".":
            start += 1
        while end > start and text[end - 1] == "-":
            end -= 1
        address = _normalized(text[start:end])
        if address is not None:
            found.append((address, start, end))
    return found


def addresses_in(text: str) -> tuple[str, ...]:
    """The distinct addresses in plain text, normalised, in order; see the module docstring."""
    seen: dict[str, None] = {}
    for address, _, _ in _occurrences(_check_str(text, "text")):
        seen.setdefault(address)
    return tuple(seen)


# Published matches ----------------------------------------------------------------------------


def _rule_a(address: str, tokens: NameTokens) -> bool:
    if tokens.last is None:
        return False
    key = _local_key(_local_of(address))
    parts = LOCAL_SEPARATORS.split(key)
    first, last = tokens.first, tokens.last
    if first == last:
        whole = parts.count(first) >= 2
    else:
        whole = first in parts and last in parts
    joined = "".join(parts)
    return whole or joined in (first + last, last + first)


def _lines(text: str) -> list[tuple[int, int]]:
    """Each line's start and end offsets in ``text``."""
    spans, start = [], 0
    for line in text.split("\n"):
        spans.append((start, start + len(line)))
        start += len(line) + 1
    return spans


def _is_word_character(ch: str) -> bool:
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def _words(text: str, start: int, end: int) -> list[tuple[str, int, int]]:
    """The ``normalize_name`` words of ``text[start:end]`` with their original offsets."""
    words: list[tuple[str, int, int]] = []
    current: list[str] = []
    word_start = word_end = start
    for index in range(start, end):
        for ch in unicodedata.normalize("NFKC", text[index]).casefold():
            if _is_word_character(ch):
                if not current:
                    word_start = index
                current.append(ch)
                word_end = index + 1
            elif current:
                words.append(("".join(current), word_start, word_end))
                current = []
    if current:
        words.append(("".join(current), word_start, word_end))
    return [(unicodedata.normalize("NFKC", word), s, e) for word, s, e in words]


def _name_spans(key: Sequence[str], text: str, start: int, end: int) -> list[tuple[int, int]]:
    words = _words(text, start, end)
    size = len(key)
    return [
        (words[index][1], words[index + size - 1][2])
        for index in range(len(words) - size + 1)
        if [word for word, _, _ in words[index : index + size]] == list(key)
    ]


def _has_name(key: Sequence[str], quote: str) -> bool:
    try:
        folded = normalize_name(quote)
    except IdentityError:
        return False
    return f" {' '.join(key)} " in f" {folded} "


def _window(line: tuple[int, int], span: tuple[int, int]) -> tuple[int, int] | None:
    """A window of at most ``MAX_QUOTE`` characters of ``line`` that holds ``span``."""
    line_start, line_end = line
    span_start, span_end = span
    spare = MAX_QUOTE - (span_end - span_start)
    if spare < 0:
        return None
    start = max(line_start, span_start - spare // 2)
    end = min(line_end, start + MAX_QUOTE)
    start = max(line_start, end - MAX_QUOTE)
    return start, end


def _quote(text: str, line: tuple[int, int], span: tuple[int, int]) -> str | None:
    """The line, stripped, when it fits a quote, else the window around ``span``; ``None``
    when neither is a valid quote of ``text``."""
    whole = text[line[0] : line[1]].strip()
    if len(whole) <= MAX_QUOTE:
        quote = whole
    else:
        window = _window(line, span)
        if window is None:
            return None
        quote = text[window[0] : window[1]].strip()
    if not quote_in(quote, text) or len(normalise(quote)) > MAX_QUOTE:
        return None
    return quote


def _line_of(lines: Sequence[tuple[int, int]], offset: int) -> tuple[int, int]:
    return next(line for line in lines if line[0] <= offset <= line[1])


def _page_candidates(
    tokens: NameTokens, key: Sequence[str] | None, page: PageAddresses, index: int
) -> list[_Candidate]:
    def match(address: str, rule: PublishedRule, quote: str) -> PublishedMatch:
        return PublishedMatch(address, page.page_id, page.url, page.fetched_at, rule, quote)

    text = page.text or ""
    lines = _lines(text)
    occurrences = [hit for hit in _occurrences(text) if not is_role_address(hit[0])]
    first_hit: dict[str, tuple[int, int]] = {}
    for address, start, end in occurrences:
        first_hit.setdefault(address, (start, end))
    candidates = []
    rule_a = PublishedRule.NAME_IN_ADDRESS
    for address, span in first_hit.items():
        if _rule_a(address, tokens):
            quote = _quote(text, _line_of(lines, span[0]), span)
            if quote is not None:
                candidates.append(_Candidate(0, index, span[0], match(address, rule_a, quote)))
    mailto: dict[str, None] = {}
    for item in page.mailto:
        address = _normalized(item)
        if address is not None and address not in first_hit and not is_role_address(address):
            mailto.setdefault(address)
    for position, address in enumerate(mailto, start=len(text)):
        if _rule_a(address, tokens):
            quote = MAILTO_QUOTE_PREFIX + address
            candidates.append(_Candidate(0, index, position, match(address, rule_a, quote)))
    if key is None:
        return candidates
    rule_b = PublishedRule.SAME_LINE
    for line in lines:
        on_line = [hit for hit in occurrences if line[0] <= hit[1] and hit[2] <= line[1]]
        if len({address for address, _, _ in on_line}) != 1:
            continue
        names = _name_spans(key, text, *line)
        address = on_line[0][0]
        pairs = sorted(
            (max(end, name_end) - min(start, name_start), start, name_start, name_end, end)
            for _, start, end in on_line
            for name_start, name_end in names
        )
        for _, start, name_start, name_end, end in pairs:
            quote = _quote(text, line, (min(start, name_start), max(end, name_end)))
            if quote is not None and _has_name(key, quote) and address in addresses_in(quote):
                candidates.append(_Candidate(1, index, start, match(address, rule_b, quote)))
                break
    return candidates


def _at_mail_domain(address: str, mail_domain: str | None) -> bool:
    if mail_domain is None:
        return True
    domain = address.rsplit("@", 1)[1]
    if domain == mail_domain:
        return True
    try:
        return registrable_domain(domain) == mail_domain
    except IdentityError:
        return False


def published_matches(
    tokens: NameTokens,
    name: str,
    pages: Sequence[PageAddresses],
    *,
    mail_domain: str | None,
) -> tuple[PublishedMatch, ...]:
    """Every published address that matches the Contact, best first; see the module
    docstring."""
    _check_tokens(tokens)
    _check_str(name, "name")
    if mail_domain is not None:
        mail_domain = _check_str(mail_domain, "mail_domain").strip().lower()
    if not all(isinstance(page, PageAddresses) for page in pages):
        raise TypeError("pages must be PageAddresses")
    try:
        key: list[str] | None = normalize_name(name).split()
    except IdentityError:
        key = None
    best: dict[str, _Candidate] = {}
    for index, page in enumerate(pages):
        for candidate in _page_candidates(tokens, key, page, index):
            address = candidate.match.address
            held = best.get(address)
            order = (candidate.rank, candidate.page, candidate.position)
            if held is None or order < (held.rank, held.page, held.position):
                best[address] = candidate
    ranked = sorted(
        best.values(),
        key=lambda c: (
            c.rank,
            not _at_mail_domain(c.match.address, mail_domain),
            c.page,
            c.position,
        ),
    )
    return tuple(candidate.match for candidate in ranked)


def published_match(
    tokens: NameTokens,
    name: str,
    pages: Sequence[PageAddresses],
    *,
    mail_domain: str | None,
    exclude: Collection[str] = (),
) -> PublishedMatch | None:
    """The first of ``published_matches`` whose address isn't in ``exclude``."""
    if isinstance(exclude, str):
        raise TypeError("exclude must be a collection of str, not a str")
    excluded = {_normalized(_check_str(item, "exclude")) or item for item in exclude}
    for found in published_matches(tokens, name, pages, mail_domain=mail_domain):
        if found.address not in excluded:
            return found
    return None


# Provenance -----------------------------------------------------------------------------------


def provenance_for(
    step: FinderStep,
    *,
    page_url: str | None = None,
    pattern: Pattern | None = None,
    pattern_source: PatternSource | None = None,
) -> ProvenanceFor:
    """The Provenance source and detail of an address assigned at ``step`` (Decision 2)."""
    if not isinstance(step, FinderStep):
        raise TypeError("step must be a FinderStep")
    if step is FinderStep.PUBLISHED:
        if page_url is None:
            raise ValueError("a published address needs its page URL")
        return ProvenanceFor(ProvenanceSource.WEBSITE, _check_str(page_url, "page_url"))
    if step is FinderStep.PATTERN:
        if pattern is None or pattern_source is None:
            raise ValueError("a pattern address needs its pattern and pattern source")
        if not isinstance(pattern, Pattern):
            raise TypeError("pattern must be a Pattern")
        if not isinstance(pattern_source, PatternSource):
            raise TypeError("pattern_source must be a PatternSource")
        detail = f"{pattern.value} {PATTERN_SOURCE_DETAIL[pattern_source]}"
        return ProvenanceFor(ProvenanceSource.PATTERN, detail)
    return ProvenanceFor(ProvenanceSource.FINDER, FINDER_DETAIL[step])


# Tried providers ------------------------------------------------------------------------------


def tried_of(text: str | None) -> tuple[str, ...]:
    """The providers in ``contacts.email_search_tried``, distinct, in order."""
    if text is None:
        return ()
    names = (part.strip() for part in _check_str(text, "text").split(SEARCH_SEPARATOR))
    return tuple(dict.fromkeys(name for name in names if name))


def tried_text(tried: Sequence[str]) -> str | None:
    """``contacts.email_search_tried`` for ``tried``, ``None`` when empty."""
    if isinstance(tried, str):
        raise TypeError("tried must be a sequence of str, not a str")
    names = tuple(dict.fromkeys(_check_str(name, "tried") for name in tried))
    for name in names:
        if name not in FINDER_PROVIDERS:
            raise ValueError("tried holds a name that isn't a finder provider")
    return SEARCH_SEPARATOR.join(names) or None


def address_quote(address: str, text: str | None, mailto: Sequence[str]) -> str | None:
    """The quote that shows ``address`` on a page: its first occurrence in ``text`` (its line,
    or the window around it), else ``mailto:<address>`` when ``mailto`` holds it, else
    ``None``; see the module docstring."""
    wanted = _normalized(_check_str(address, "address"))
    if wanted is None:
        return None
    if text is not None:
        _check_str(text, "text")
        lines = _lines(text)
        for found, start, end in _occurrences(text):
            if found == wanted:
                quote = _quote(text, _line_of(lines, start), (start, end))
                if quote is not None:
                    return quote
    if any(_normalized(_check_str(item, "mailto")) == wanted for item in mailto):
        return MAILTO_QUOTE_PREFIX + wanted
    return None
