"""The SignalInboxItem machine and the Signal Inbox's rules (AD-4, AD-18, FR-8, FR-11, FR-49;
Story 2.16). Standard library and the domain only.

**Statuses.** A pasted item is ``pending`` until its ``process_signal_item`` job ran, then
``processed`` (it became a Lead's Evidence) or ``flagged`` (something is missing; Mac resolves
it). ``discovery`` owns ``signal_inbox_items`` and appends one ``events`` row (entity
``signal_inbox_items``, ``ENTITY``: the event log takes a table name) per creation and per
change, in the same transaction.

**Transitions.** ``transition(current, event)`` follows ``TRANSITIONS`` (read-only) or raises
``IllegalTransition``:

=========  =========  =========  =======================
Event      From       To         Cause
=========  =========  =========  =======================
process    pending    processed  parsed
flag       pending    flagged    flagged
reprocess  flagged    processed  reprocessed_by_operator
=========  =========  =========  =======================

``CREATION`` is ``Transition(pending, submitted)``: the creation row has no ``from`` status.
A flagged item's ``FlagReason`` is ``organization_unknown``, ``segment_unknown``,
``parse_failed`` or ``organization_suppressed``; ``FLAG_LABELS`` says what is missing.

**The form (Decision 3).** ``check_items(rows)`` takes up to ``FORM_SLOTS`` (5) fieldsets, each
a mapping of ``url``, ``text``, ``author`` and ``company`` (missing means blank). A fieldset
whose four fields are all blank is ignored; at least one item is required. ``url`` is required,
an absolute ``http`` or ``https`` URL with a host, at most ``MAX_URL`` (2,048) characters, with
no whitespace or control character; ``text`` is required (not blank), at most ``MAX_TEXT``
(10,000) characters; ``author`` and ``company`` are optional, at most ``MAX_AUTHOR`` and
``MAX_COMPANY`` (200) characters, stripped (blank is ``None``), with no control or format
character (``leads.REFUSED_NAME_CATEGORIES``, the Lead name's rule). Any invalid item refuses the
whole submission: ``ItemsRefused(errors)``, ``errors`` mapping ``<field>_<n>`` (``n`` the
1-based fieldset) to a fixed reason (``items`` when no fieldset was filled in); nothing is
stored. ``url_never_fetched(url)`` is ``fetch_policy.is_denied(url)``: the list then says
``NEVER_FETCHED_NOTE``. No job ever fetches an item's URL, denied or not.

**The parse (Decision 4).** ``accept_parse(parsed, item, segments, submitted_at)`` reads a
``signal_inbox_parse`` answer and keeps only what the rules allow; a rejected field is ``None``
and the rest still apply:

- ``segment``: a key of one of ``segments``;
- ``signal_type``: a Signal type key of that Segment whose ``sources`` list ``signal_inbox``;
- ``website``: the registrable domain of the answer (``website_domain``), when neither it nor
  the answer is FR-49-denied (``is_denied``), it isn't a shared platform
  (``is_shared_platform``) and it occurs, case-insensitively and as whole labels
  (``domain_occurs``: not preceded by a letter, digit or ``-``, not followed by a letter, digit,
  ``-`` or a further label; so ``www.acme.com``, ``https://acme.com/x`` and a sentence's
  ``acme.com.`` name ``acme.com``, but ``acme.co``, ``me.com`` and ``acme.com.au`` don't), in
  the item text or the company field;
- ``author_name`` and ``author_role``: each kept (stripped, at most 200 characters, no control
  character) only when it occurs, NFC- and whitespace-normalised and case-insensitively, in the
  item text or the author field;
- ``event_date``: a date stated in the item text (``extraction.stated_date``) on or before
  ``submitted_at``'s UTC date;
- ``quote``: normalised (``quotes.normalise``), 1 to 300 characters and an exact substring of
  the item text (``quotes.quote_in``).

``AcceptedParse.flag`` is ``organization_unknown`` without a website, else ``segment_unknown``
without a Segment, else ``None``. A malformed answer (a field of the wrong type) raises
``ValueError``: the caller flags the item ``parse_failed``.

**The Lead's name (Decision 7).** ``lead_name(company, domain)`` is the company field when it
is set and isn't a URL or domain (``registrable_domain`` refuses it), else ``domain``.

**The job (Decision 11).** ``process_signal_item`` (``PROCESS_SIGNAL_ITEM_JOB``): scope the
item, discriminator ``tick``, payload ``{"v": 1, "item_id": ...}`` (``ITEM_ID_FIELD``).
"""

import re
import unicodedata
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from nextron.domain.budget import BudgetClass
from nextron.domain.errors import DomainError
from nextron.domain.extraction import MAX_QUOTE, stated_date
from nextron.domain.fetch_policy import is_denied
from nextron.domain.identity import IdentityError, registrable_domain
from nextron.domain.leads import REFUSED_NAME_CATEGORIES, is_shared_platform
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.quotes import normalise, quote_in
from nextron.domain.segments import SegmentDef, SignalSource

MACHINE: Final = "signal_inbox_item"
ENTITY: Final = "signal_inbox_items"  # the events rows' entity: a table (nextron.db.eventlog)
ACTOR: Final = "system:discovery"
OPERATOR: Final = "operator"

PROCESS_SIGNAL_ITEM_JOB: Final = "process_signal_item"
ITEM_ID_FIELD: Final = "item_id"
PAYLOAD_VERSION: Final = 1
ROUTE: Final = "signal_inbox_parse"
BUDGET_CLASS: Final = BudgetClass.BLOCKABLE  # discovery's AI route
INBOX_BLOCK: Final = "signal_inbox"  # the item block's source
SIGNAL_TYPES_BLOCK: Final = "signal_types"  # the catalogue block's source and block id
SUBJECT_PREFIX: Final = "signal_item:"

FORM_SLOTS: Final = 5
MAX_URL: Final = 2048
MAX_TEXT: Final = 10_000
MAX_AUTHOR: Final = 200
MAX_COMPANY: Final = 200
MAX_PERSON_FIELD: Final = 200
FIELDS: Final = ("url", "text", "author", "company")
NEVER_FETCHED_NOTE: Final = "stored as a source only; never fetched"

URL_REQUIRED: Final = "Enter the item's URL."
URL_INVALID: Final = "Enter an absolute http or https URL."
URL_TOO_LONG: Final = f"The URL must be at most {MAX_URL:,} characters."
TEXT_REQUIRED: Final = "Paste the item's text."
TEXT_TOO_LONG: Final = f"The text must be at most {MAX_TEXT:,} characters."
AUTHOR_TOO_LONG: Final = f"The author must be at most {MAX_AUTHOR} characters."
COMPANY_TOO_LONG: Final = f"The company must be at most {MAX_COMPANY} characters."
AUTHOR_INVALID: Final = "The author must not hold a control or format character."
COMPANY_INVALID: Final = "The company must not hold a control or format character."
NO_ITEMS: Final = "Fill in at least one item."
NO_ITEMS_FIELD: Final = "items"

_WEB_SCHEMES: Final = frozenset({"http", "https"})
_REFUSED_CATEGORIES: Final = REFUSED_NAME_CATEGORIES  # what a Lead's name refuses


class ItemStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FLAGGED = "flagged"


class FlagReason(StrEnum):
    ORGANIZATION_UNKNOWN = "organization_unknown"
    SEGMENT_UNKNOWN = "segment_unknown"
    PARSE_FAILED = "parse_failed"
    ORGANIZATION_SUPPRESSED = "organization_suppressed"


FLAG_LABELS: Final[Mapping[FlagReason, str]] = MappingProxyType(
    {
        FlagReason.ORGANIZATION_UNKNOWN: "No organization website was identified",
        FlagReason.SEGMENT_UNKNOWN: "No seeded Segment matched",
        FlagReason.PARSE_FAILED: "The item could not be parsed",
        FlagReason.ORGANIZATION_SUPPRESSED: "Organization is on the Suppression List",
    }
)


class ItemEvent(StrEnum):
    PROCESS = "process"
    FLAG = "flag"
    REPROCESS = "reprocess"


class ItemCause(StrEnum):
    SUBMITTED = "submitted"
    PARSED = "parsed"
    FLAGGED = "flagged"
    REPROCESSED_BY_OPERATOR = "reprocessed_by_operator"


_S = ItemStatus

TRANSITIONS: Final[Mapping[tuple[ItemStatus, ItemEvent], Transition[ItemStatus, ItemCause]]] = (
    MappingProxyType(
        {
            (_S.PENDING, ItemEvent.PROCESS): Transition(_S.PROCESSED, ItemCause.PARSED),
            (_S.PENDING, ItemEvent.FLAG): Transition(_S.FLAGGED, ItemCause.FLAGGED),
            (_S.FLAGGED, ItemEvent.REPROCESS): Transition(
                _S.PROCESSED, ItemCause.REPROCESSED_BY_OPERATOR
            ),
        }
    )
)

CREATION: Final = Transition(_S.PENDING, ItemCause.SUBMITTED)


def transition(current: ItemStatus, event: ItemEvent) -> Transition[ItemStatus, ItemCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


# The form ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubmittedItem:
    """One pasted item: its URL and text, and the optional author and company."""

    url: str
    text: str
    author: str | None = None
    company: str | None = None


class ItemsRefused(DomainError):
    """The submission has an invalid item: ``errors`` maps ``<field>_<n>`` to its reason."""

    code = "signal_items_refused"

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("the Signal Inbox submission was refused")
        self.errors = MappingProxyType(dict(errors))


def _has_refused_character(text: str) -> bool:
    return any(unicodedata.category(ch) in _REFUSED_CATEGORIES for ch in text)


def _url_error(url: str) -> str | None:
    if not url:
        return URL_REQUIRED
    if len(url) > MAX_URL:
        return URL_TOO_LONG
    if any(ch.isspace() for ch in url) or _has_refused_character(url):
        return URL_INVALID
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return URL_INVALID
    if parts.scheme.lower() not in _WEB_SCHEMES or not host:
        return URL_INVALID
    return None


def check_items(rows: Sequence[Mapping[str, str]]) -> tuple[SubmittedItem, ...]:
    """The items of the form's fieldsets; ``ItemsRefused`` when any is invalid. See the module
    docstring."""
    if len(rows) > FORM_SLOTS:
        raise ValueError(f"the form has at most {FORM_SLOTS} items")
    items: list[SubmittedItem] = []
    errors: dict[str, str] = {}
    for number, row in enumerate(rows, start=1):
        values = {name: row.get(name, "") for name in FIELDS}
        if not all(isinstance(value, str) for value in values.values()):
            raise TypeError("each item field must be a str")
        if not any(value.strip() for value in values.values()):
            continue  # an empty fieldset
        url = values["url"].strip()
        text = values["text"]
        author = values["author"].strip() or None
        company = values["company"].strip() or None
        found: dict[str, str] = {}
        if (problem := _url_error(url)) is not None:
            found["url"] = problem
        if not text.strip():
            found["text"] = TEXT_REQUIRED
        elif len(text) > MAX_TEXT:
            found["text"] = TEXT_TOO_LONG
        if author is not None and len(author) > MAX_AUTHOR:
            found["author"] = AUTHOR_TOO_LONG
        elif author is not None and _has_refused_character(author):
            found["author"] = AUTHOR_INVALID
        if company is not None and len(company) > MAX_COMPANY:
            found["company"] = COMPANY_TOO_LONG
        elif company is not None and _has_refused_character(company):
            found["company"] = COMPANY_INVALID
        errors.update({f"{name}_{number}": reason for name, reason in found.items()})
        if not found:
            items.append(SubmittedItem(url, text, author, company))
    if errors:
        raise ItemsRefused(errors)
    if not items:
        raise ItemsRefused({NO_ITEMS_FIELD: NO_ITEMS})
    return tuple(items)


def url_never_fetched(url: str) -> bool:
    """Whether the item's URL is on the FR-49 denylist (the list then says
    ``NEVER_FETCHED_NOTE``)."""
    return is_denied(url)


# The parse --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ItemFacts:
    """What the parse is checked against: the item's text, author and company fields."""

    text: str
    author: str | None = None
    company: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptedParse:
    """The fields of a ``signal_inbox_parse`` answer that passed the rules; see the module
    docstring."""

    segment: str | None = None
    signal_type: str | None = None
    website: str | None = None
    author_name: str | None = None
    author_role: str | None = None
    event_date: date | None = None
    quote: str | None = None

    @property
    def flag(self) -> FlagReason | None:
        """Why the item can't be applied as it is, or ``None``."""
        if self.website is None:
            return FlagReason.ORGANIZATION_UNKNOWN
        if self.segment is None:
            return FlagReason.SEGMENT_UNKNOWN
        return None


def _text(parsed: Mapping[str, Any], name: str) -> str | None:
    value = parsed.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"the signal_inbox_parse answer's {name} must be a str or null")
    return value


def _date(parsed: Mapping[str, Any]) -> date | None:
    value = parsed.get("event_date")
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("the signal_inbox_parse answer's event_date must be a date or null")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("the signal_inbox_parse answer's event_date must be a date") from None


def _folded(text: str) -> str:
    return normalise(text).casefold()


def _occurs(value: str, *within: str | None) -> bool:
    """Whether ``value``, normalised and casefolded, is in any of ``within``, likewise."""
    wanted = _folded(value)
    return bool(wanted) and any(text is not None and wanted in _folded(text) for text in within)


def domain_occurs(domain: str, *within: str | None) -> bool:
    """Whether ``domain`` occurs, casefolded, as whole labels in any of ``within``; see the
    module docstring."""
    wanted = _folded(domain)
    if not wanted:
        return False
    pattern = re.compile(rf"(?<![\w-]){re.escape(wanted)}(?![\w-]|\.[\w-])")
    return any(text is not None and pattern.search(_folded(text)) for text in within)


def website_domain(value: str, item: ItemFacts) -> str | None:
    """The registrable domain of a proposed website when it may be the item's organization, or
    ``None``; see the module docstring."""
    try:
        domain = registrable_domain(value)
    except IdentityError:
        return None
    if is_denied(value) or is_denied(domain) or is_shared_platform(domain):
        return None
    return domain if domain_occurs(domain, item.text, item.company) else None


def _person_field(value: str | None, item: ItemFacts) -> str | None:
    if value is None:
        return None
    kept = value.strip()
    if not kept or len(kept) > MAX_PERSON_FIELD or _has_refused_character(kept):
        return None
    return kept if _occurs(kept, item.text, item.author) else None


def accept_parse(
    parsed: Mapping[str, Any],
    item: ItemFacts,
    segments: Sequence[SegmentDef],
    submitted_at: datetime,
) -> AcceptedParse:
    """What of ``parsed`` the rules accept; see the module docstring."""
    if not isinstance(parsed, Mapping):
        raise ValueError("the signal_inbox_parse answer must be an object")
    if not isinstance(item, ItemFacts):
        raise TypeError("accept_parse takes ItemFacts")
    segment_key = _text(parsed, "segment")
    type_key = _text(parsed, "signal_type")
    website = _text(parsed, "website")
    author_name = _text(parsed, "author_name")
    author_role = _text(parsed, "author_role")
    quote = _text(parsed, "quote")
    event_date = _date(parsed)
    segment = next((each for each in segments if each.key == segment_key), None)
    signal_type = None
    if segment is not None and type_key is not None:
        signal_type = next(
            (
                each.key
                for each in segment.signal_types
                if each.key == type_key and SignalSource.SIGNAL_INBOX in each.sources
            ),
            None,
        )
    kept_quote = None
    if quote is not None:
        normalised = normalise(quote)
        if 1 <= len(normalised) <= MAX_QUOTE and quote_in(normalised, item.text):
            kept_quote = normalised
    kept_date = None
    if event_date is not None:
        today = submitted_at.astimezone(UTC).date()
        if event_date <= today and stated_date(item.text, event_date):
            kept_date = event_date
    return AcceptedParse(
        segment=None if segment is None else segment.key,
        signal_type=signal_type,
        website=None if website is None else website_domain(website, item),
        author_name=_person_field(author_name, item),
        author_role=_person_field(author_role, item),
        event_date=kept_date,
        quote=kept_quote,
    )


def lead_name(company: str | None, domain: str) -> str:
    """A new Lead's name: the company field unless it is a URL or domain, else ``domain``."""
    if company is not None:
        try:
            registrable_domain(company)
        except IdentityError:
            return company
    return domain
