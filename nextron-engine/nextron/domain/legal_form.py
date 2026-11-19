"""The UK legal-form rules (FR-12, FR-9, NFR-3, AD-7; Story 2.12). Standard library and
``nextron.domain`` only.

UK cold email may go only to corporate subscribers (PECR). The ``check_legal_form`` job
(``nextron.app.discovery.legal_form``) links a GB Lead to its Companies House record only when
Evidence on the Lead's own website does, and these rules decide what counts. Nothing here reads
or writes the database or calls the registry.

**The one list.** ``CORPORATE_SUBSCRIBER_FORMS`` holds the eight Companies House company types
that are corporate subscribers (``ltd``, ``plc``, ``llp``, ``private-unlimited``,
``private-unlimited-nsc``, ``private-limited-guarant-nsc``,
``private-limited-guarant-nsc-limited-exemption`` and
``private-limited-shares-section-30-exemption``); ``LEGAL_FORM_LABELS`` names each, and
``LEGAL_FORM_PATTERN`` (``[a-z][a-z0-9-]{0,63}``) is the shape of any company type code.
``nextron.domain.markets`` (a GB Market may list only these types; ``_GB_CORPORATE_FORMS`` is
the same object) and ``nextron.domain.eligibility`` import them from here.
``COMPANY_STATUS_PATTERN`` (``[a-z][a-z0-9-]{0,31}``) is the shape of a company status code.

**The status.** ``LegalFormStatus`` is ``confirmed``, ``not_corporate`` or ``unconfirmed``. For
a record the Lead's Evidence links it to, ``status_for(company_type, company_status)`` is
``confirmed`` when the record is ``active`` and its type is a corporate subscriber
(``is_corporate_subscriber``), ``not_corporate`` when it is ``active`` with any other type or no
known type, and ``unconfirmed`` otherwise (any status but ``active``, an unknown one included).
A Lead no Evidence links to a record is ``unconfirmed``; a Lead never checked has no status,
which ``eligibility_facts`` reads as unconfirmed too.

**Eligibility.** ``eligibility_facts(legal_form, status) -> (legal_form, confirmed)`` gives
``EligibilityLead``'s two facts: ``(code, True)`` for ``confirmed`` with a corporate code and
for ``not_corporate`` with a code that isn't one (so the reason is
``uk_legal_form_ineligible``, not a false "no Evidence links the Lead"); anything else gives the
code when it matches ``LEGAL_FORM_PATTERN`` (else ``None``) and ``False``.

**Names.** ``normalise_company_name(name)``: Unicode NFKC, casefolded; ``.`` and apostrophes
dropped; ``&`` read as ``and``; every other character that isn't a letter or digit becomes a
space; whitespace collapsed; then trailing ``ltd``, ``limited``, ``llp`` and ``plc`` tokens
dropped, repeatedly. An empty result never matches. ``name_occurs(name_key, text)``: whether
the non-empty key is a whole-token run of ``text`` normalised the same way.

**Company numbers on a page** (``find_company_numbers(text) -> tuple[NumberQuote(number,
quote), ...]``). The search runs on ``quotes.normalise``d text and ignores case. A phrase is
``company no`` (or ``company no.``), ``company number``, ``company registration number``,
``company reg no`` (or ``.``), ``registered no`` (or ``.``), ``registered number`` or
``registered in england``/``wales``/``scotland``/``northern ireland``, with no letter or digit
just before it or just after it (a trailing ``.`` excepted). Bare "registration number" isn't
one: it is used for FCA, VAT and SRA numbers. The number is the first token after the phrase
that matches ``[A-Za-z]{2}[0-9]{6}`` or ``[0-9]{6,8}`` with no letter or digit on either side,
when it starts at most ``NUMBER_WINDOW`` (40) characters after the phrase and the gap before it
holds no digit and, apart from a ``no.``, none of ``.``, ``!`` or ``?`` (a sentence break). It
is normalised by ``identity.normalize_company_number`` (``1234567`` is ``01234567``);
``00000000`` is dropped. The quote runs from the phrase's start to the number's end. Each
number appears once, with its first quote, in text order.

**Postcodes** (``find_postcodes(text) -> tuple[PostcodeQuote(key, quote), ...]``), on
``quotes.normalise``d text: each match of ``(?<![A-Za-z0-9])[A-Za-z]{1,2}\\d[A-Za-z\\d]?\\s?\\d
[A-Za-z]{2}(?![A-Za-z0-9])`` (ASCII). The key is uppercase with no space; the quote is the
postcode with up to ``POSTCODE_LEAD_IN`` (80) characters before it, starting after a space (or
at the text's start), and each key appears once, with its first quote. ``postcode_key(text)`` is
a stored postcode's key (whitespace dropped, uppercased), or ``None`` when it isn't one.

**The registry quote.** ``registry_quote(number=, name=, company_type=, company_status=,
office_postcode=, accounts_category=None, sic_codes=())`` renders a record canonically:
``Companies House record <number>: company type <type|unknown>, company status
<status|unknown>, registered office postcode <postcode|none>, registered name <name>``,
normalised and cut to ``MAX_QUOTE`` (300) characters at the name's end. Story 8.4: when given,
``, accounts category <category>`` and then ``, SIC codes <code> <code> ...`` come right before
``, registered name``, so a quote without them renders as before and the name stays last.
``company_page_url(number)`` is the record's public page. ``SIC_CODE_PATTERN`` (``[0-9]{5}``),
``ACCOUNTS_CATEGORY_PATTERN`` (``[a-z][a-z0-9-]{0,31}``) and ``DORMANT`` (``dormant``) are
Story 8.4's register codes.

**The finding.** ``SiteQuote(page_url, quote, fetched_at)`` is the site Evidence (a quote of at
most 300 normalised characters from a stored page). ``LegalFormFinding(status, number,
legal_form, company_status, site, registry_quote, retrieved_at)`` is what a check found: with no
``number`` (nothing links the Lead), there is no site quote, registry quote, legal form or
company status and the status is ``unconfirmed``; with a number, the registry quote and
``retrieved_at`` (when the record was read) are required. With a site quote too, the status is
``status_for(legal_form, company_status)``. Without one (``registry_only``, Story 8.4: a Lead
Companies House discovery created, before its website shows the number), the legal form and
company status are required and the status is ``unconfirmed`` (FR-12: only the site confirms). A wrong type raises ``TypeError``, a bad value
``ValueError``; no message holds a value.

``CHECK_LEGAL_FORM_JOB`` is the job's name (``check_legal_form``).
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.identity import normalize_company_number
from nextron.domain.quotes import normalise

CHECK_LEGAL_FORM_JOB: Final = "check_legal_form"
ACTIVE: Final = "active"  # the one company status that can confirm
MAX_QUOTE: Final = 300  # nextron.domain.extraction.MAX_QUOTE
NUMBER_WINDOW: Final = 40  # the most characters between a phrase and its number
POSTCODE_LEAD_IN: Final = 80  # the most characters a postcode quote holds before the postcode
NO_NUMBER: Final = "00000000"  # never a real company
COMPANY_PAGE_BASE: Final = "https://find-and-update.company-information.service.gov.uk/company/"
UNKNOWN: Final = "unknown"
NONE: Final = "none"

LEGAL_FORM_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{0,63}")  # matched with fullmatch
COMPANY_STATUS_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{0,31}")  # matched with fullmatch
COMPANY_NUMBER_PATTERN: Final = re.compile(r"[A-Z0-9]{8}")  # matched with fullmatch
SIC_CODE_PATTERN: Final = re.compile(r"[0-9]{5}")  # matched with fullmatch (Story 8.4)
ACCOUNTS_CATEGORY_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{0,31}")  # fullmatch (Story 8.4)
DORMANT: Final = "dormant"  # the accounts category of a dormant company (Story 8.4)

# The Companies House company types that are corporate subscribers (PECR). A GB Market may list
# these and no other (nextron.domain.markets).
CORPORATE_SUBSCRIBER_FORMS: Final = frozenset(
    {
        "ltd",
        "plc",
        "private-limited-guarant-nsc",
        "private-limited-guarant-nsc-limited-exemption",
        "private-limited-shares-section-30-exemption",
        "private-unlimited",
        "private-unlimited-nsc",
        "llp",
    }
)
LEGAL_FORM_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ltd": "private limited company",
        "plc": "public limited company",
        "llp": "limited liability partnership",
        "private-unlimited": "private unlimited company",
        "private-unlimited-nsc": "private unlimited company without share capital",
        "private-limited-guarant-nsc": "private company limited by guarantee",
        "private-limited-guarant-nsc-limited-exemption": (
            "private company limited by guarantee, exempt from 'Limited'"
        ),
        "private-limited-shares-section-30-exemption": (
            "private company limited by shares, exempt under section 30"
        ),
    }
)

_APOSTROPHES: Final = frozenset("'‘’ʼ`")
_NAME_SUFFIXES: Final = frozenset({"ltd", "limited", "llp", "plc"})
_END: Final = r"(?![a-z0-9])"  # with IGNORECASE: no letter or digit follows
_NO: Final = r"no(?:\.|(?![a-z0-9]))"  # "no" or "no.", as a word
_PHRASE: Final = re.compile(
    r"(?<![a-z0-9])(?:"
    rf"company (?:{_NO}|number{_END}|registration number{_END}|reg {_NO})"
    rf"|registered (?:{_NO}|number{_END})"
    rf"|registered in (?:england|wales|scotland|northern ireland){_END}"
    r")",
    re.IGNORECASE,
)
_NUMBER: Final = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]{2}[0-9]{6}|[0-9]{6,8})(?![A-Za-z0-9])")
_LONGEST_NUMBER: Final = 8
_GAP_NO: Final = re.compile(r"(?<![a-z0-9])no\.", re.IGNORECASE)
_SENTENCE_BREAKS: Final = frozenset(".!?")
_POSTCODE: Final = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{1,2}\d[A-Za-z\d]?\s?\d[A-Za-z]{2}(?![A-Za-z0-9])", re.ASCII
)
_POSTCODE_KEY: Final = re.compile(r"[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}")  # fullmatch
_WHITESPACE: Final = re.compile(r"\s+")


class LegalFormStatus(StrEnum):
    """What a check concluded about a Lead's legal form."""

    CONFIRMED = "confirmed"  # linked, active, a corporate subscriber
    NOT_CORPORATE = "not_corporate"  # linked, active, any other (or no known) type
    UNCONFIRMED = "unconfirmed"  # not linked, or not active


def is_corporate_subscriber(code: str | None) -> bool:
    """Whether the company type ``code`` is a corporate subscriber; ``False`` for ``None`` or an
    unknown type."""
    return isinstance(code, str) and code in CORPORATE_SUBSCRIBER_FORMS


def status_for(company_type: str | None, company_status: str | None) -> LegalFormStatus:
    """The status of a Lead linked to a record of this type and status."""
    if company_status != ACTIVE:
        return LegalFormStatus.UNCONFIRMED
    if is_corporate_subscriber(company_type):
        return LegalFormStatus.CONFIRMED
    return LegalFormStatus.NOT_CORPORATE


def eligibility_facts(legal_form: str | None, status: str | None) -> tuple[str | None, bool]:
    """``EligibilityLead``'s ``(legal_form, legal_form_confirmed)`` for a stored legal form and
    status; see the module docstring."""
    code = (
        legal_form
        if isinstance(legal_form, str) and LEGAL_FORM_PATTERN.fullmatch(legal_form) is not None
        else None
    )
    if code is not None:
        corporate = is_corporate_subscriber(code)
        if status == LegalFormStatus.CONFIRMED and corporate:
            return code, True
        if status == LegalFormStatus.NOT_CORPORATE and not corporate:
            return code, True
    return code, False


# Names ------------------------------------------------------------------------------------------


def normalise_company_name(name: str) -> str:
    """``name`` as a comparison key; see the module docstring."""
    if not isinstance(name, str):
        raise TypeError("normalise_company_name takes a str")
    folded = unicodedata.normalize("NFKC", name).casefold()
    chars: list[str] = []
    for char in folded:
        if char == "." or char in _APOSTROPHES:
            continue
        if char == "&":
            chars.append(" and ")
        else:
            chars.append(char if char.isalnum() else " ")
    tokens = "".join(chars).split()
    while tokens and tokens[-1] in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def name_occurs(name_key: str, text: str) -> bool:
    """Whether the non-empty ``name_key`` is a whole-token run of ``text``, normalised as a
    name."""
    if not isinstance(name_key, str) or not isinstance(text, str):
        raise TypeError("name_occurs takes two str values")
    if not name_key:
        return False
    return f" {name_key} " in f" {normalise_company_name(text)} "


# Company numbers --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NumberQuote:
    """A company number quoted on a page, normalised, and the quote that cites it."""

    number: str
    quote: str = field(repr=False)


def _gap_ok(gap: str) -> bool:
    if any(char.isdigit() for char in gap):
        return False
    return not any(char in _SENTENCE_BREAKS for char in _GAP_NO.sub("", gap))


def find_company_numbers(text: str) -> tuple[NumberQuote, ...]:
    """The company numbers ``text`` quotes near a registration phrase; see the module
    docstring."""
    if not isinstance(text, str):
        raise TypeError("find_company_numbers takes a str")
    body = normalise(text)
    found: dict[str, NumberQuote] = {}
    for phrase in _PHRASE.finditer(body):
        end = phrase.end()
        # one character before the phrase's end, so the lookbehind sees it, and one after the
        # longest number that can start in the window, so the lookahead does
        segment = body[end - 1 : end + NUMBER_WINDOW + _LONGEST_NUMBER + 1]
        match = _NUMBER.search(segment, 1)
        if match is None:
            continue
        start = end + match.start() - 1
        if start - end > NUMBER_WINDOW or not _gap_ok(body[end:start]):
            continue
        number = normalize_company_number(match.group())
        if number == NO_NUMBER or number in found:
            continue
        found[number] = NumberQuote(number, body[phrase.start() : end + match.end() - 1])
    return tuple(found.values())


# Postcodes --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostcodeQuote:
    """A UK postcode quoted on a page: its key (uppercase, no space) and the quote citing it."""

    key: str = field(repr=False)
    quote: str = field(repr=False)


def postcode_key(text: str | None) -> str | None:
    """A stored postcode as a key (whitespace dropped, uppercased), or ``None`` when it isn't a
    UK postcode."""
    if not isinstance(text, str):
        return None
    key = _WHITESPACE.sub("", text).upper()
    return key if _POSTCODE_KEY.fullmatch(key) else None


def find_postcodes(text: str) -> tuple[PostcodeQuote, ...]:
    """The UK postcodes ``text`` quotes; see the module docstring."""
    if not isinstance(text, str):
        raise TypeError("find_postcodes takes a str")
    body = normalise(text)
    found: dict[str, PostcodeQuote] = {}
    for match in _POSTCODE.finditer(body):
        key = _WHITESPACE.sub("", match.group()).upper()
        if key in found or _POSTCODE_KEY.fullmatch(key) is None:
            continue
        start = match.start()
        if start > POSTCODE_LEAD_IN:
            window = body[start - POSTCODE_LEAD_IN : start]
            space = window.find(" ")
            start = start if space < 0 else start - POSTCODE_LEAD_IN + space + 1
        else:
            start = 0
        found[key] = PostcodeQuote(key, body[start : match.end()])
    return tuple(found.values())


# The registry record ----------------------------------------------------------------------------


def company_page_url(number: str) -> str:
    """The public Companies House page of the company ``number``."""
    if not isinstance(number, str) or COMPANY_NUMBER_PATTERN.fullmatch(number) is None:
        raise ValueError("company_page_url takes a Companies House number")
    return f"{COMPANY_PAGE_BASE}{number}"


def registry_quote(
    *,
    number: str,
    name: str,
    company_type: str | None,
    company_status: str | None,
    office_postcode: str | None,
    accounts_category: str | None = None,
    sic_codes: tuple[str, ...] = (),
) -> str:
    """The canonical rendering of a record, as the registry Evidence's quote; see the module
    docstring."""
    if not isinstance(number, str) or COMPANY_NUMBER_PATTERN.fullmatch(number) is None:
        raise ValueError("registry_quote takes a Companies House number")
    if not isinstance(name, str) or not normalise(name):
        raise ValueError("registry_quote takes a non-blank registered name")
    for value in (company_type, company_status, office_postcode, accounts_category):
        if value is not None and not isinstance(value, str):
            raise TypeError(
                "registry_quote takes the type, status, postcode and accounts category as str "
                "or None"
            )
    if not isinstance(sic_codes, tuple) or not all(isinstance(code, str) for code in sic_codes):
        raise TypeError("registry_quote takes the SIC codes as a tuple of str")
    extra = ""
    if accounts_category is not None:
        extra += f", accounts category {accounts_category}"
    if sic_codes:
        extra += f", SIC codes {' '.join(sic_codes)}"
    head = normalise(
        f"Companies House record {number}: company type {company_type or UNKNOWN}, "
        f"company status {company_status or UNKNOWN}, registered office postcode "
        f"{office_postcode or NONE}{extra}, registered name"
    )
    room = MAX_QUOTE - len(head) - 1
    return normalise(normalise(f"{head} {normalise(name)[: max(room, 0)]}")[:MAX_QUOTE])


# The finding ------------------------------------------------------------------------------------


def _aware(value: object, what: str) -> None:
    if not (isinstance(value, datetime) and value.utcoffset() is not None):
        raise TypeError(f"{what} must be an aware datetime")


def _quote(value: object, what: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str")
    if not 1 <= len(value) <= MAX_QUOTE or value != normalise(value):
        raise ValueError(f"{what} must be a normalised quote of 1 to {MAX_QUOTE} characters")


def _optional_code(value: object, pattern: re.Pattern[str], what: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str or None")
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{what} isn't a Companies House code")


@dataclass(frozen=True, slots=True)
class SiteQuote:
    """The site Evidence: a quote from the stored page at ``page_url``, fetched at
    ``fetched_at``."""

    page_url: str = field(repr=False)
    quote: str = field(repr=False)
    fetched_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.page_url, str):
            raise TypeError("SiteQuote.page_url must be a str")
        if not self.page_url or self.page_url != self.page_url.strip():
            raise ValueError("SiteQuote.page_url must be a stripped, non-empty URL")
        _quote(self.quote, "SiteQuote.quote")
        _aware(self.fetched_at, "SiteQuote.fetched_at")


@dataclass(frozen=True, slots=True)
class LegalFormFinding:
    """What one ``check_legal_form`` run found; see the module docstring."""

    status: LegalFormStatus
    number: str | None
    legal_form: str | None
    company_status: str | None
    site: SiteQuote | None
    registry_quote: str | None = field(repr=False)
    retrieved_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, LegalFormStatus):
            raise TypeError("LegalFormFinding.status must be a LegalFormStatus")
        if self.number is not None:
            if not isinstance(self.number, str):
                raise TypeError("LegalFormFinding.number must be a str or None")
            if COMPANY_NUMBER_PATTERN.fullmatch(self.number) is None:
                raise ValueError("LegalFormFinding.number isn't a Companies House number")
        _optional_code(self.legal_form, LEGAL_FORM_PATTERN, "LegalFormFinding.legal_form")
        _optional_code(
            self.company_status, COMPANY_STATUS_PATTERN, "LegalFormFinding.company_status"
        )
        if self.site is not None and not isinstance(self.site, SiteQuote):
            raise TypeError("LegalFormFinding.site must be a SiteQuote or None")
        if self.registry_quote is not None:
            _quote(self.registry_quote, "LegalFormFinding.registry_quote")
        if self.retrieved_at is not None:
            _aware(self.retrieved_at, "LegalFormFinding.retrieved_at")
        if self.number is None:
            if (
                self.site is not None
                or self.registry_quote is not None
                or self.legal_form is not None
                or self.company_status is not None
            ):
                raise ValueError(
                    "a LegalFormFinding without a number holds no Evidence, legal form or "
                    "company status"
                )
            if self.status is not LegalFormStatus.UNCONFIRMED:
                raise ValueError("a LegalFormFinding without a number is unconfirmed")
            return
        if self.registry_quote is None or self.retrieved_at is None:
            raise ValueError(
                "a LegalFormFinding with a number needs its registry quote and retrieved_at"
            )
        if self.site is None:  # registry-only (Story 8.4): the site hasn't confirmed it yet
            if self.legal_form is None or self.company_status is None:
                raise ValueError(
                    "a registry-only LegalFormFinding needs its legal form and company status"
                )
            if self.status is not LegalFormStatus.UNCONFIRMED:
                raise ValueError("a registry-only LegalFormFinding is unconfirmed")
            return
        if self.status is not status_for(self.legal_form, self.company_status):
            raise ValueError("a linked LegalFormFinding's status must follow its record")

    @property
    def linked(self) -> bool:
        """Whether the finding names a record (Evidence on the site links it, or, registry-only,
        the record itself)."""
        return self.number is not None

    @property
    def registry_only(self) -> bool:
        """Whether the finding is the register's record alone, with no site Evidence yet
        (Story 8.4): always ``unconfirmed``."""
        return self.number is not None and self.site is None


UNLINKED: Final = LegalFormFinding(
    LegalFormStatus.UNCONFIRMED, None, None, None, None, None, None
)  # nothing links the Lead to a record
