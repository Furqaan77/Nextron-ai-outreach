"""The rules of Signal extraction (FR-10, FR-11, FR-57, AD-7, AD-11, AD-18; Story 2.9). Standard
library and ``nextron.domain`` only.

The ``extract_lead`` job (``nextron.app.research.extraction``) sends a Lead's changed pages to
Claude's ``extract_signals`` route once. The answer is only a proposal: these rules decide what
of it becomes Evidence, how it meets the Lead's earlier Evidence, and which zone the Lead's
headquarters is in. Nothing here reads or writes the database.

**Constants.** ``EXTRACT_LEAD_JOB`` (``extract_lead``), ``ROUTE`` (``extract_signals``),
``BUDGET_CLASS`` (``BudgetClass.BLOCKABLE``: the class of every ``extract_signals`` route
candidate, which a contract test checks, so the app never reads the AI routes table),
``MAX_QUOTE`` (300 characters), ``MAX_VALUE`` (200), ``MAX_SIZE_NUMBER`` (1,000,000) and
``PAGE_TEXT_BUDGET`` (180,000 characters, about 90,000 tokens at worst, shared by the pages of
one call).

**Enums.** ``EvidenceSource`` (where Evidence came from: ``website``, ``ats``, ``registry``,
``signal_inbox``, ``csv``, ``referral``, ``operator``); ``FactKind`` (the three profile-fact
kinds of ``ExtractSignalsV1``, ``what_we_do``, ``location`` and ``technology``, in
``PROFILE_FACTS``, and its five size-clue kinds, ``lawyers_at_office``, ``lawyers_firm_wide``,
``staff``, ``group_staff`` and ``locations``, in ``SIZE_FACTS``); ``ExtractionOutcome`` (``ok``,
``refused``, ``truncated``, ``invalid``, ``failed``, ``no_activation``, ``no_pages``,
``lead_merged``); ``Discarded`` (the ``events`` row's ``to_status`` for a refusal:
``signal_discarded``, ``profile_fact_discarded``, ``size_clue_discarded``,
``event_date_discarded``) and ``DiscardCause`` (its ``cause``). ``EXTRACTED_BY_RULE`` and
``EXTRACTED_BY_OPERATOR`` are the two ``extracted_by`` values that aren't an AI actor.

**``allot(texts, budget) -> tuple[str, ...]``** shares ``budget`` characters among the texts by
water-filling: each text gets an equal share, and what shorter texts leave over is shared among
the rest, so a text is cut to its share (its first characters) but never dropped. A text within
its share is kept whole. The shares never sum past ``budget``; integer division's leftover goes
to the longest texts. ``TypeError`` unless the texts are ``str``s and ``budget`` an ``int``;
``ValueError`` for a negative budget.

**``stated_date(quote, value) -> bool``**: whether the quote writes the date ``value`` (a
``date``). The quote is normalised (``quotes.normalise``) and read left to right for date
mentions, the fullest form first, with years 1900 to 2099 and ASCII digits:

- **a full date** matches the day itself: ISO ``2026-10-14``; ``14 October 2026`` (or ``14th
  October 2026``, ``14 of October, 2026``); ``October 14, 2026`` (``October 14th 2026``); or
  numeric ``D/M/YYYY`` or ``M/D/YYYY`` with one separator, ``/``, ``.`` or ``-``, used twice
  (``03/04/2026`` is both 3 April and 4 March). A month is its full name or its three-letter
  abbreviation (``Sept`` too), each with an optional ``.``, in any case and as a whole word
  (``Oct 14, 2026``, ``14 Sept. 2026``; ``Mayor`` and ``Marching`` are no months);
- **a month and year** (``October 2026``) matches only the 1st of that month;
- **a year alone** (``2026``, not touching a letter or digit) matches only 1 January of that
  year.

A year that is part of a fuller mention isn't also read alone. Whether the date is in the
future is ``validate``'s check, not this one.

**``number_in(number, quote) -> bool``**: whether a run of ASCII digits in the quote, read with
any comma thousands separators (``1,200``), equals ``number``. A run of digits joined by a
decimal point (``1.5``, ``12.25``) is no whole number, so none of its digits count; commas that
aren't thousands groups split their digits into separate runs (``12,34`` is 12 and 34).

**``validate(parsed, segment, pages, now) -> Validation``** reads an ``extract_signals`` answer
(``LlmOk.parsed``: ``signals``, ``profile_facts``, ``size_clues``; ``job_postings`` are left to
Story 2.11). ``segment`` is the Lead's ``SegmentDef``; ``pages`` maps each page id sent to a
``SentPage(url, fetched_at, text)`` holding its full stored text. A Signal's ``signal_type`` is
stripped and lowercased first. Each proposal is checked in this order, and the first failure
discards it (``Discard(status, cause, page_id)``; ``page_id`` is the cited page when it was one
of the pages sent, else ``None``):

1. its ``block_id`` is a page sent (``unknown_block``);
2. its quote, normalised, isn't empty (``quote_empty``), has at most ``MAX_QUOTE`` characters
   (``quote_too_long``) and passes ``quote_in`` against the page's full stored text
   (``quote_not_in_page``);
3. a Signal's type is one of the Segment's types whose Evidence includes ``quote``
   (``unknown_signal_type``);
4. a size clue's number is from 1 to ``MAX_SIZE_NUMBER`` (``number_out_of_range``) and
   ``number_in`` its quote (``number_not_in_quote``);
5. a profile fact's value, stripped, has 1 to ``MAX_VALUE`` characters and no control
   character (``Cc``: NUL, a line break, a tab; ``value_invalid``).

A Signal of an Event type keeps its ``event_date`` only when ``stated_date`` holds
(``date_not_in_quote``) and the date isn't after ``now``'s UTC date (``date_in_future``);
otherwise it is kept undated and the drop is recorded in ``dropped_dates`` (status
``event_date_discarded``). An Attribute never carries a date: a date proposed for one is
ignored, with no record. A proposal that repeats an earlier kept one (the same claim, page and
normalised quote) is dropped without a record. ``Validation(observations, discards,
dropped_dates)`` keeps the order of the answer, Signals first, then profile facts, then size
clues. An answer that doesn't have the schema's shape raises ``ValueError``.

An ``Observation(claim, page_id, url, quote, event_date, value, number)`` is one kept proposal:
its ``Claim`` is a Signal type key or a ``FactKind``, its quote is normalised, ``value`` is set
for a profile fact (stripped) and ``number`` for a size clue.

**``reconcile(live, proposed, texts) -> Plan``** meets the Lead's live Evidence with the new
observations, per page and claim. ``texts`` maps the URL of each page sent to its new full
stored text; a live row (``LiveEvidence(id, claim, url, quote, created_at)``) of another page is
out of scope and untouched. Within each (claim, URL):

1. each proposal whose quote equals a live row's quote confirms that row (one row each);
2. each other live row whose quote is still in the page's text (``quote_in``) is confirmed:
   code decides, not whether the model repeated it;
3. the remaining live rows (their quote is gone), oldest first (``created_at``, then ``id``),
   are paired with the remaining proposals in order: the new row supersedes the old;
4. proposals left over are inserted, and live rows left over are retracted.

``Plan(confirm, insert, supersede, retract)``: the live ids to confirm, the observations to
insert, ``Superseding(old_id, new)`` pairs, and the live ids to retract; each in the order of
the pages (URL), then the claims, then the steps above. A proposal whose URL isn't in ``texts``
raises ``ValueError``.

**``headquarters(locations) -> Headquarters | None``** reads the Lead's headquarters zone from
its live ``location`` Evidence (``LocationQuote(evidence_id, quote)``, oldest first). Places
come from the quotes (``geo.place_in``), never from the model's value. When any quote names a
headquarters (``headquarter...``, ``head office``, ``HQ``, ``main office``, ``principal
office`` or ``registered office``, any case), only those quotes count. Each counted quote's
place gives its zone (``geo.location_zone``; a quote with no place, or a place with no zone,
gives none). Exactly one zone among them gives ``Headquarters(zone, evidence_id)``, the
Evidence of the first quote that gives it; none, or more than one, gives ``None``.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Final

from nextron.domain.budget import BudgetClass
from nextron.domain.geo import location_zone, place_in
from nextron.domain.quotes import normalise, quote_in
from nextron.domain.segments import EvidenceType, SegmentDef, SignalClass, SignalType

EXTRACT_LEAD_JOB: Final = "extract_lead"
ROUTE: Final = "extract_signals"
BUDGET_CLASS: Final = BudgetClass.BLOCKABLE
MAX_QUOTE: Final = 300
MAX_VALUE: Final = 200
MAX_SIZE_NUMBER: Final = 1_000_000
PAGE_TEXT_BUDGET: Final = 180_000
EXTRACTED_BY_RULE: Final = "rule"
EXTRACTED_BY_OPERATOR: Final = "operator"
SIGNAL_TYPES_BLOCK: Final = "signal_types"  # the source and block id of the catalogue block
WEBSITE_BLOCK: Final = "website"  # the source of a page's block


class EvidenceSource(StrEnum):
    """Where a piece of Evidence came from."""

    WEBSITE = "website"
    ATS = "ats"
    REGISTRY = "registry"
    SIGNAL_INBOX = "signal_inbox"
    CSV = "csv"
    REFERRAL = "referral"
    OPERATOR = "operator"


class FactKind(StrEnum):
    """The profile-fact and size-clue kinds of ``ExtractSignalsV1``."""

    WHAT_WE_DO = "what_we_do"
    LOCATION = "location"
    TECHNOLOGY = "technology"
    LAWYERS_AT_OFFICE = "lawyers_at_office"
    LAWYERS_FIRM_WIDE = "lawyers_firm_wide"
    STAFF = "staff"
    GROUP_STAFF = "group_staff"
    LOCATIONS = "locations"


PROFILE_FACTS: Final = frozenset({FactKind.WHAT_WE_DO, FactKind.LOCATION, FactKind.TECHNOLOGY})
SIZE_FACTS: Final = frozenset(
    {
        FactKind.LAWYERS_AT_OFFICE,
        FactKind.LAWYERS_FIRM_WIDE,
        FactKind.STAFF,
        FactKind.GROUP_STAFF,
        FactKind.LOCATIONS,
    }
)


class ExtractionOutcome(StrEnum):
    """How one ``extract_lead`` run ended."""

    OK = "ok"
    REFUSED = "refused"
    TRUNCATED = "truncated"
    INVALID = "invalid"
    FAILED = "failed"
    NO_ACTIVATION = "no_activation"
    NO_PAGES = "no_pages"
    LEAD_MERGED = "lead_merged"


class Discarded(StrEnum):
    """What a refusal's ``events`` row says was discarded (its ``to_status``)."""

    SIGNAL = "signal_discarded"
    PROFILE_FACT = "profile_fact_discarded"
    SIZE_CLUE = "size_clue_discarded"
    EVENT_DATE = "event_date_discarded"


class DiscardCause(StrEnum):
    """Why a proposal, or an Event's date, was discarded (the ``events`` row's ``cause``)."""

    UNKNOWN_SIGNAL_TYPE = "unknown_signal_type"
    UNKNOWN_BLOCK = "unknown_block"
    QUOTE_EMPTY = "quote_empty"
    QUOTE_TOO_LONG = "quote_too_long"
    QUOTE_NOT_IN_PAGE = "quote_not_in_page"
    NUMBER_OUT_OF_RANGE = "number_out_of_range"
    NUMBER_NOT_IN_QUOTE = "number_not_in_quote"
    VALUE_INVALID = "value_invalid"
    DATE_NOT_IN_QUOTE = "date_not_in_quote"
    DATE_IN_FUTURE = "date_in_future"


# Values ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SentPage:
    """A page sent in the call: its URL, when it was fetched and its full stored text."""

    url: str
    fetched_at: datetime
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Claim:
    """What an observation is about: a Signal type key, or a ``FactKind`` (exactly one)."""

    signal_type: str | None = None
    fact: FactKind | None = None

    def __post_init__(self) -> None:
        if (self.signal_type is None) == (self.fact is None):
            raise ValueError("a Claim is a Signal type or a fact kind, not both or neither")
        if self.signal_type is not None and not isinstance(self.signal_type, str):
            raise TypeError("signal_type must be a str")
        if self.fact is not None and not isinstance(self.fact, FactKind):
            raise TypeError("fact must be a FactKind")

    @property
    def sort_key(self) -> tuple[int, str]:
        """Signal types first, then fact kinds, each by name."""
        return (0, self.signal_type) if self.signal_type is not None else (1, str(self.fact))


@dataclass(frozen=True, slots=True)
class Observation:
    """One kept proposal; see the module docstring."""

    claim: Claim
    page_id: str
    url: str
    quote: str = field(repr=False)
    event_date: date | None = None
    value: str | None = field(default=None, repr=False)
    number: int | None = None


@dataclass(frozen=True, slots=True)
class Discard:
    """One refusal: what was discarded, why, and the cited page when it was sent."""

    status: Discarded
    cause: DiscardCause
    page_id: str | None


@dataclass(frozen=True, slots=True)
class Validation:
    """What ``validate`` kept and refused."""

    observations: tuple[Observation, ...]
    discards: tuple[Discard, ...]
    dropped_dates: tuple[Discard, ...]


@dataclass(frozen=True, slots=True)
class LiveEvidence:
    """A live Evidence row in reconciliation: its id, claim, page URL, normalised quote and when
    it was created."""

    id: str
    claim: Claim
    url: str
    quote: str = field(repr=False)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Superseding:
    """A live row whose quote is gone, and the observation that replaces it."""

    old_id: str
    new: Observation


@dataclass(frozen=True, slots=True)
class Plan:
    """What ``reconcile`` decided; see the module docstring."""

    confirm: tuple[str, ...]
    insert: tuple[Observation, ...]
    supersede: tuple[Superseding, ...]
    retract: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocationQuote:
    """A live ``location`` Evidence row: its id and its quote."""

    evidence_id: str
    quote: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Headquarters:
    """The Lead's headquarters zone and the Evidence it comes from."""

    zone: str
    evidence_id: str


# The page budget ------------------------------------------------------------------------------


def allot(texts: Sequence[str], budget: int) -> tuple[str, ...]:
    """Each text cut to its water-filled share of ``budget``; see the module docstring."""
    if isinstance(texts, str) or not all(isinstance(text, str) for text in texts):
        raise TypeError("allot takes a sequence of str")
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError("budget must be an int")
    if budget < 0:
        raise ValueError("budget must not be negative")
    shares = [0] * len(texts)
    left, remaining = budget, len(texts)
    for index in sorted(range(len(texts)), key=lambda index: (len(texts[index]), index)):
        share = left // remaining
        shares[index] = min(len(texts[index]), share)
        left -= shares[index]
        remaining -= 1
    return tuple(text[:share] for text, share in zip(texts, shares, strict=True))


# Dates and numbers in a quote -----------------------------------------------------------------

MONTHS: Final = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
# A month's full name and its abbreviations (three letters, and Sept), lower case, to its number.
MONTH_NUMBERS: Final = {
    **{name: number for number, name in enumerate(MONTHS, start=1)},
    **{name[:3]: number for number, name in enumerate(MONTHS, start=1)},
    "sept": 9,
}
# A month, as a whole word with an optional full stop.
_MONTH: Final = (
    "(?:"
    + "|".join(sorted(MONTH_NUMBERS, key=lambda name: (-len(name), name)))
    + r")(?![A-Za-z])\.?"
)
_YEAR: Final = r"(?:19|20)[0-9]{2}"
_DAY: Final = r"[0-9]{1,2}(?:st|nd|rd|th)?"
# The fullest form first, so a year inside a full date isn't also read alone.
_DATE_MENTION: Final = re.compile(
    rf"(?<![0-9])(?P<iso_y>{_YEAR})-(?P<iso_m>[0-9]{{2}})-(?P<iso_d>[0-9]{{2}})(?![0-9])"
    rf"|(?<![0-9A-Za-z])(?P<dmy_d>{_DAY}) (?:of )?(?P<dmy_m>{_MONTH}),? (?P<dmy_y>{_YEAR})(?![0-9])"
    rf"|(?<![A-Za-z])(?P<mdy_m>{_MONTH}) (?P<mdy_d>{_DAY}),? (?P<mdy_y>{_YEAR})(?![0-9])"
    rf"|(?<![0-9])(?P<num_a>[0-9]{{1,2}})(?P<sep>[/.-])(?P<num_b>[0-9]{{1,2}})(?P=sep)"
    rf"(?P<num_y>{_YEAR})(?![0-9])"
    rf"|(?<![A-Za-z])(?P<my_m>{_MONTH}),? (?P<my_y>{_YEAR})(?![0-9A-Za-z])"
    rf"|(?<![0-9A-Za-z])(?P<y>{_YEAR})(?![0-9A-Za-z])",
    re.IGNORECASE,
)
# A maximal run of digits with the commas and points between them.
_DIGIT_RUN: Final = re.compile(r"(?<![0-9])[0-9](?:[0-9,.]*[0-9])?(?![0-9])")
_THOUSANDS: Final = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+")  # matched with fullmatch


def _day_of(text: str) -> int:
    return int(re.sub(r"[^0-9]", "", text))


def _month_of(name: str) -> int:
    return MONTH_NUMBERS[name.rstrip(".").lower()]


def _real(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _mentioned(match: re.Match[str]) -> set[date]:
    """The dates one mention can stand for."""
    found: set[date | None] = set()
    groups = match.groupdict()
    if groups["iso_y"] is not None:
        found.add(_real(int(groups["iso_y"]), int(groups["iso_m"]), int(groups["iso_d"])))
    elif groups["dmy_y"] is not None:
        found.add(
            _real(int(groups["dmy_y"]), _month_of(groups["dmy_m"]), _day_of(groups["dmy_d"]))
        )
    elif groups["mdy_y"] is not None:
        found.add(
            _real(int(groups["mdy_y"]), _month_of(groups["mdy_m"]), _day_of(groups["mdy_d"]))
        )
    elif groups["num_y"] is not None:
        year, first, second = int(groups["num_y"]), int(groups["num_a"]), int(groups["num_b"])
        found.add(_real(year, second, first))  # D/M/YYYY
        found.add(_real(year, first, second))  # M/D/YYYY
    elif groups["my_y"] is not None:
        found.add(_real(int(groups["my_y"]), _month_of(groups["my_m"]), 1))
    elif groups["y"] is not None:
        found.add(_real(int(groups["y"]), 1, 1))
    return {each for each in found if each is not None}


def stated_date(quote: str, value: date) -> bool:
    """Whether the quote writes the date ``value``; see the module docstring."""
    if not isinstance(quote, str):
        raise TypeError("quote must be a str")
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("value must be a date")
    return any(value in _mentioned(match) for match in _DATE_MENTION.finditer(normalise(quote)))


def number_in(number: int, quote: str) -> bool:
    """Whether a digit run in the quote (comma thousands allowed) equals ``number``."""
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("number must be an int")
    if not isinstance(quote, str):
        raise TypeError("quote must be a str")
    return number in _whole_numbers(quote)


def _whole_numbers(quote: str) -> set[int]:
    """The whole numbers a quote writes in digits; see the module docstring."""
    found: set[int] = set()
    for match in _DIGIT_RUN.finditer(quote):
        run = match.group(0)
        if "." in run:  # a decimal such as 1.5: none of its digits is a whole number
            continue
        if _THOUSANDS.fullmatch(run):
            found.add(int(run.replace(",", "")))
        else:
            found.update(int(part) for part in run.split(",") if part)
    return found


# Validation -----------------------------------------------------------------------------------


def _items(parsed: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    items = parsed.get(name)
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        raise ValueError(f"the extract_signals answer's {name} must be a list of objects")
    return items


def _text_field(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str):
        raise ValueError(f"the extract_signals answer's {name} must be a str")
    return value


def _quote_cause(quote: str, page: SentPage) -> DiscardCause | None:
    if not quote:
        return DiscardCause.QUOTE_EMPTY
    if len(quote) > MAX_QUOTE:
        return DiscardCause.QUOTE_TOO_LONG
    if not quote_in(quote, page.text):
        return DiscardCause.QUOTE_NOT_IN_PAGE
    return None


def _event_date(item: Mapping[str, Any]) -> date | None:
    value = item.get("event_date")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("the extract_signals answer's event_date must be a date or null")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("the extract_signals answer's event_date must be a date") from None


class _Validator:
    """One ``validate`` call's state."""

    def __init__(
        self, segment: SegmentDef, pages: Mapping[str, SentPage], today: date
    ) -> None:
        self.quotable: dict[str, SignalType] = {
            signal_type.key: signal_type
            for signal_type in segment.signal_types
            if EvidenceType.QUOTE in signal_type.evidence
        }
        self.pages = pages
        self.today = today
        self.kept: list[Observation] = []
        self.seen: set[tuple[Claim, str, str]] = set()
        self.discards: list[Discard] = []
        self.dropped: list[Discard] = []

    def _cited(
        self, item: Mapping[str, Any], status: Discarded
    ) -> tuple[str, SentPage, str] | None:
        """Steps 1 and 2: the page id, page and normalised quote, or ``None`` when refused."""
        block_id = _text_field(item, "block_id")
        page = self.pages.get(block_id)
        if page is None:
            self.discards.append(Discard(status, DiscardCause.UNKNOWN_BLOCK, None))
            return None
        quote = normalise(_text_field(item, "quote"))
        cause = _quote_cause(quote, page)
        if cause is not None:
            self.discards.append(Discard(status, cause, block_id))
            return None
        return block_id, page, quote

    def _keep(self, observation: Observation) -> None:
        key = (observation.claim, observation.page_id, observation.quote)
        if key not in self.seen:  # a repeat is dropped without a record
            self.seen.add(key)
            self.kept.append(observation)

    def signal(self, item: Mapping[str, Any]) -> None:
        key = _text_field(item, "signal_type").strip().lower()
        proposed_date = _event_date(item)
        cited = self._cited(item, Discarded.SIGNAL)
        if cited is None:
            return
        page_id, page, quote = cited
        signal_type = self.quotable.get(key)
        if signal_type is None:
            self.discards.append(
                Discard(Discarded.SIGNAL, DiscardCause.UNKNOWN_SIGNAL_TYPE, page_id)
            )
            return
        kept_date = None
        if proposed_date is not None and signal_type.signal_class is SignalClass.EVENT:
            cause = None
            if not stated_date(quote, proposed_date):
                cause = DiscardCause.DATE_NOT_IN_QUOTE
            elif proposed_date > self.today:
                cause = DiscardCause.DATE_IN_FUTURE
            if cause is None:
                kept_date = proposed_date
            else:
                self.dropped.append(Discard(Discarded.EVENT_DATE, cause, page_id))
        self._keep(Observation(Claim(signal_type=key), page_id, page.url, quote, kept_date))

    def profile_fact(self, item: Mapping[str, Any]) -> None:
        kind = _fact(item, PROFILE_FACTS)
        cited = self._cited(item, Discarded.PROFILE_FACT)
        if cited is None:
            return
        page_id, page, quote = cited
        value = _text_field(item, "value").strip()
        if not 1 <= len(value) <= MAX_VALUE or any(
            unicodedata.category(character) == "Cc" for character in value
        ):
            self.discards.append(
                Discard(Discarded.PROFILE_FACT, DiscardCause.VALUE_INVALID, page_id)
            )
            return
        self._keep(Observation(Claim(fact=kind), page_id, page.url, quote, value=value))

    def size_clue(self, item: Mapping[str, Any]) -> None:
        kind = _fact(item, SIZE_FACTS)
        number = item.get("number")
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("the extract_signals answer's number must be an integer")
        cited = self._cited(item, Discarded.SIZE_CLUE)
        if cited is None:
            return
        page_id, page, quote = cited
        cause = None
        if not 1 <= number <= MAX_SIZE_NUMBER:
            cause = DiscardCause.NUMBER_OUT_OF_RANGE
        elif not number_in(number, quote):
            cause = DiscardCause.NUMBER_NOT_IN_QUOTE
        if cause is not None:
            self.discards.append(Discard(Discarded.SIZE_CLUE, cause, page_id))
            return
        self._keep(Observation(Claim(fact=kind), page_id, page.url, quote, number=number))


def _fact(item: Mapping[str, Any], kinds: frozenset[FactKind]) -> FactKind:
    value = _text_field(item, "kind")
    for kind in kinds:
        if kind.value == value:
            return kind
    raise ValueError("the extract_signals answer has a fact kind its schema doesn't list")


def validate(
    parsed: Mapping[str, Any],
    segment: SegmentDef,
    pages: Mapping[str, SentPage],
    now: datetime,
) -> Validation:
    """The observations kept, the proposals refused and the Event dates dropped; see the module
    docstring."""
    if not isinstance(parsed, Mapping):
        raise ValueError("the extract_signals answer must be an object")
    if not isinstance(segment, SegmentDef):
        raise TypeError("segment must be a SegmentDef")
    if not all(isinstance(page, SentPage) for page in pages.values()):
        raise TypeError("pages must map page ids to SentPage")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise TypeError("now must be an aware datetime")
    validator = _Validator(segment, pages, now.astimezone(UTC).date())
    for item in _items(parsed, "signals"):
        validator.signal(item)
    for item in _items(parsed, "profile_facts"):
        validator.profile_fact(item)
    for item in _items(parsed, "size_clues"):
        validator.size_clue(item)
    return Validation(
        tuple(validator.kept), tuple(validator.discards), tuple(validator.dropped)
    )


# Reconciliation -------------------------------------------------------------------------------


def reconcile(
    live: Sequence[LiveEvidence], proposed: Sequence[Observation], texts: Mapping[str, str]
) -> Plan:
    """Confirm, insert, supersede and retract, per page and claim; see the module docstring."""
    groups: dict[tuple[str, Claim], tuple[list[LiveEvidence], list[Observation]]] = {}
    for row in live:
        if row.url in texts:
            groups.setdefault((row.url, row.claim), ([], []))[0].append(row)
    for observation in proposed:
        if observation.url not in texts:
            raise ValueError("a proposal cites a page that wasn't sent")
        groups.setdefault((observation.url, observation.claim), ([], []))[1].append(observation)
    confirm: list[str] = []
    insert: list[Observation] = []
    supersede: list[Superseding] = []
    retract: list[str] = []
    for (url, _claim), (rows, observations) in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1].sort_key)
    ):
        rows = sorted(rows, key=lambda row: (row.created_at, row.id))
        left_rows: list[LiveEvidence] = []
        left_observations = list(observations)
        for row in rows:  # 1. an identical quote confirms the row
            same = next((each for each in left_observations if each.quote == row.quote), None)
            if same is None:
                left_rows.append(row)
            else:
                left_observations.remove(same)
                confirm.append(row.id)
        gone: list[LiveEvidence] = []
        for row in left_rows:  # 2. a quote still in the page is confirmed too
            if quote_in(row.quote, texts[url]):
                confirm.append(row.id)
            else:
                gone.append(row)
        for row, observation in zip(gone, left_observations, strict=False):  # 3. supersede
            supersede.append(Superseding(row.id, observation))
        paired = min(len(gone), len(left_observations))
        insert.extend(left_observations[paired:])  # 4. the rest
        retract.extend(row.id for row in gone[paired:])
    return Plan(tuple(confirm), tuple(insert), tuple(supersede), tuple(retract))


# Headquarters ---------------------------------------------------------------------------------

_HEADQUARTERS: Final = re.compile(
    r"\bheadquarter|\bhead office\b|\bHQ\b|\bmain office\b|\bprincipal office\b"
    r"|\bregistered office\b",
    re.IGNORECASE,
)


def headquarters(locations: Sequence[LocationQuote]) -> Headquarters | None:
    """The Lead's headquarters zone and its Evidence, or ``None``; see the module docstring."""
    if not all(isinstance(location, LocationQuote) for location in locations):
        raise TypeError("headquarters takes LocationQuotes")
    named = [
        location
        for location in locations
        if _HEADQUARTERS.search(normalise(location.quote)) is not None
    ]
    zones: dict[str, str] = {}
    for location in named or locations:
        place = place_in(location.quote)
        zone = None if place is None else location_zone(place.country_code, place.region)
        if zone is not None:
            zones.setdefault(zone, location.evidence_id)
    if len(zones) != 1:
        return None
    [(zone, evidence_id)] = zones.items()
    return Headquarters(zone, evidence_id)
