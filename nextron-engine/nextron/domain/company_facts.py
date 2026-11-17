"""Approved Company Facts (FR-72, NFR-24, AD-7, AD-12, Story 4.3): what a Draft may claim about
Nextron.

A Draft may claim, about Nextron, only an Approved Company Fact allowed for its Segment and
Market: the company itself, a case study, the Offer, a regulation or a statistic. Each fact has
a short ``fact_id`` and is kept as append-only versions (the ``company_facts`` table, owner
``playbook``); revisions cite ``(fact_id, version)`` pairs. ``seed/company_facts.yaml`` seeds
them (Playbook section 6) through ``nextron seed sync``, and the Operator maintains them on the
Approved Company Facts page.

**Types** (frozen, slotted; every list is a tuple): ``FactType`` (``company``, ``case_study``,
``offer``, ``regulation``, ``statistic``), ``FactCreator`` (``seed``, ``operator``),
``FactContent(type, wording, source_url, segments, markets, metric, metric_confirmed_at,
scope_note, last_checked_on)`` and ``SeedFact(fact_id, content)``, one item of the file.
``segments`` and ``markets`` are sorted tuples of unique references: Segment keys and Market
codes in the file, ``segments.id`` and ``markets.id`` ULIDs in the database and on the Operator's
form. ``metric``, ``metric_confirmed_at`` (a ``date``: the client's confirmation) and
``scope_note`` may be ``None``; ``last_checked_on`` is a ``date``.

**The file form.** The top level is exactly ``company_facts: [<fact>, ...]``, with 1 to 200
items. A fact has ``fact_id``, ``type``, ``wording``, ``source_url``, ``segments``, ``markets``
and ``last_checked_on``, plus optional ``metric``, ``metric_confirmed_at`` and ``scope_note``.
Missing and unknown keys are refused. Dates are quoted ``'YYYY-MM-DD'`` texts (the seed reader
refuses a plain date).

**Validation.** ``validate_company_facts(data)`` returns the ``SeedFact``s in file order, or
raises ``InvalidCompanyFacts(field, reason)`` (code ``invalid_company_facts``) for the first
problem in file order (each mapping's fields in the file's order, then its missing fields, then
rules 1 and 2). The message is ``"<field>: <reason>"``; ``field`` is a path such as
``company_facts[legal_ai].segments[1]``, with the item's ``fact_id`` in the brackets when it is
valid (and not a repeat), else its 0-based index. A reason never repeats a value longer than 40
characters.

**The value rules** (the file, the Operator's form and a stored row share them):

- ``fact_id`` fullmatches ``[a-z][a-z0-9_]{0,30}`` and is unique in the file.
- ``wording`` is a text of at most 500 characters: a ``str``, non-blank, with no leading or
  trailing whitespace and no ``Cc``, ``Cf``, ``Zl`` or ``Zp`` character (Story 2.1's rules).
  ``scope_note`` (optional) is such a text of at most 500 characters, and ``metric`` (optional)
  of at most 200.
- ``type`` is a ``FactType``.
- ``source_url`` is at most 500 characters with no whitespace, no backslash and no ``Cc``,
  ``Cf``, ``Zs``, ``Zl`` or ``Zp`` character; ``urlsplit`` gives an ``http`` or ``https``
  scheme, a hostname, a port that parses (if any) and no user name or password. So
  ``nextron.web.templating.safe_link`` always renders it as a link. Anything else: "Use an
  http or https link".
- ``segments`` and ``markets`` each hold 1 to 50 items with no duplicates; each is a Segment key
  (``[a-z][a-z0-9_]{0,30}``) or Market code (``[a-z][a-z0-9_]{0,15}``) in the file, or a ULID.
  Validation sorts them, so a later index counts the sorted list.
- Dates are a real calendar date written ``YYYY-MM-DD``.

**The rules**, each failing on its own field:

1. A ``regulation`` fact has exactly one Market: "A regulatory fact applies to one Market" (on
   ``markets``).
2. A ``metric`` needs ``metric_confirmed_at``: "Add a metric only once the client confirms it"
   (on ``metric``); and ``metric_confirmed_at`` needs a ``metric``: "Add the confirmation date
   only with the client's metric" (on ``metric_confirmed_at``).
3. The compliance floor (decision 6): when the wording, scope note or metric names ABA Formal
   Opinion 512 (``nextron.domain.segments.mentions_aba_512``), every allowed Market's country is
   ``US`` ("ABA Formal Opinion 512 applies only to a US Market"); when any of them names the SRA
   notice (``mentions_sra_notice``), every allowed Market's country is ``GB`` ("The SRA notice
   applies only to a UK Market"). Both fail on ``markets``, against a ``{reference:
   country_code}`` map; a reference missing from the map is rule 4's.
4. Every reference is known: a Segment reference is in the given set, and a Market reference in
   the given country map. A ULID that isn't fails "names no stored Segment" (or Market); a key
   or code fails "names <key>, which isn't a Segment of this sync" (or Market).
5. ``metric_confirmed_at`` and ``last_checked_on`` are no later than a given ``latest`` date:
   "can't be in the future". Callers pass the UTC date of now plus one day, so any timezone's
   today passes.

**Functions:**

- ``content_problems(content)``: the value rules and rules 1 and 2 on a ``FactContent``, as
  ``(field, reason)`` pairs in ``FactContent`` field order (``field`` is a key such as
  ``wording``, or ``segments[<i>]`` for an item). A content's dates must be ``date`` values
  (never their text, never a ``datetime``).
- ``check_fact_references(content, segment_refs, market_countries)``: rules 3 and 4, in that
  order (rule 4 on ``segments``, then ``markets``).
- ``date_problems(content, latest)``: rule 5.
- ``fact_form(fact_id, content)``: the item's file form (enums as their values, lists as lists,
  dates as ``YYYY-MM-DD``, optional keys only when set; a value of the wrong type is left as it
  is, so validation refuses it). ``fact_from_form(form)`` validates one item (path
  ``company_facts[<fact_id>]``) into a ``SeedFact``, so ``fact_from_form(fact_form(f, c)) ==
  SeedFact(f, c)`` for a valid fact. The repository decodes a stored row through it.
- ``check_seed_facts(facts, segment_keys, market_countries, latest)``: raises
  ``InvalidCompanyFacts`` for the first broken rule 3 to 5, in file order, naming
  ``company_facts[<fact_id>].<field>``.

**The Operator's path.** ``FactFields`` holds the form's raw texts (``segments`` and
``markets`` as tuples of texts). ``fact_from_fields(fields, *, segment_refs, market_countries,
latest)`` returns the ``FactContent`` or raises ``InvalidFact(problems)`` (code
``invalid_fact``) with every field's first problem, keyed by the field's name (an item's index
dropped). First it strips each field and makes each run of whitespace in ``wording``,
``scope_note`` and ``metric`` one space; a blank ``metric``, ``metric_confirmed_at`` or
``scope_note`` is ``None``. A blank required field reads "Enter a value", a blank ``type``
"Choose a type", and no Segment or no Market "Choose at least one Segment" (or Market). Then
the value rules, rules 1 and 2, rules 3 and 4 and rule 5 run over the fields that passed so far.
"""

import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

from nextron.domain.errors import DomainError
from nextron.domain.ids import is_ulid
from nextron.domain.segments import mentions_aba_512, mentions_sra_notice

FACT_ID_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched with fullmatch
SEGMENT_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # segments.KEY_PATTERN
MARKET_CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,15}")  # markets.CODE_PATTERN
DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # matched with fullmatch
_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # an unknown field shown by name

TOP_LEVEL: Final = "(top level)"  # the field path of a problem with the whole file
FORM_FIELD: Final = "form"  # an Operator problem that belongs to no one field
MAX_FACTS: Final = 200
MAX_WORDING: Final = 500
MAX_SCOPE_NOTE: Final = 500
MAX_METRIC: Final = 200
MAX_SOURCE_URL: Final = 500
MAX_REFERENCES: Final = 50
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
# What safe_link refuses in a URL: control, format, space and separator characters
REFUSED_URL_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zs", "Zl", "Zp"})
LINK_SCHEMES: Final = frozenset({"http", "https"})
US: Final = "US"
GB: Final = "GB"

LINK_REASON: Final = "Use an http or https link"
DATE_REASON: Final = "must be a real date written YYYY-MM-DD"
REGULATION_MARKETS_REASON: Final = "A regulatory fact applies to one Market"
METRIC_UNCONFIRMED_REASON: Final = "Add a metric only once the client confirms it"
CONFIRMATION_WITHOUT_METRIC_REASON: Final = (
    "Add the confirmation date only with the client's metric"
)
ABA_REASON: Final = "ABA Formal Opinion 512 applies only to a US Market"
SRA_REASON: Final = "The SRA notice applies only to a UK Market"
NO_STORED_SEGMENT_REASON: Final = "names no stored Segment"
NO_STORED_MARKET_REASON: Final = "names no stored Market"
FUTURE_REASON: Final = "can't be in the future"
ENTER_A_VALUE: Final = "Enter a value"
CHOOSE_A_TYPE: Final = "Choose a type"
CHOOSE_A_SEGMENT: Final = "Choose at least one Segment"
CHOOSE_A_MARKET: Final = "Choose at least one Market"
FACT_ID_REASON: Final = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 31 "
    "characters"
)


class FactType(StrEnum):
    COMPANY = "company"
    CASE_STUDY = "case_study"
    OFFER = "offer"
    REGULATION = "regulation"
    STATISTIC = "statistic"


class FactCreator(StrEnum):
    """Who wrote a version: ``nextron seed sync`` or the Operator on the facts page."""

    SEED = "seed"
    OPERATOR = "operator"


class InvalidCompanyFacts(DomainError):
    """Company facts that break a rule: ``field`` is the path of the first problem and
    ``reason`` says what a valid value is, repeating no value longer than 40 characters."""

    code = "invalid_company_facts"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class InvalidFact(DomainError):
    """An Operator's fact that breaks a rule: ``problems`` maps each refused field (or
    ``form``) to its first reason. The message names the fields, never a value."""

    code = "invalid_fact"

    def __init__(self, problems: Mapping[str, str]) -> None:
        self.problems: dict[str, str] = dict(problems)
        super().__init__(f"invalid fact: {', '.join(self.problems) or 'no field'}")


@dataclass(frozen=True, slots=True)
class FactContent:
    type: FactType
    wording: str
    source_url: str
    segments: tuple[str, ...]  # sorted: Segment keys in the file, segments.id ULIDs stored
    markets: tuple[str, ...]  # sorted: Market codes in the file, markets.id ULIDs stored
    metric: str | None
    metric_confirmed_at: date | None  # the client's confirmation of the metric
    scope_note: str | None
    last_checked_on: date


@dataclass(frozen=True, slots=True)
class SeedFact:
    """One item of ``seed/company_facts.yaml``."""

    fact_id: str
    content: FactContent


@dataclass(frozen=True, slots=True)
class FactFields:
    """What the Operator's form submitted, as raw texts."""

    type: str
    wording: str
    source_url: str
    segments: tuple[str, ...]
    markets: tuple[str, ...]
    metric: str
    metric_confirmed_at: str
    scope_note: str
    last_checked_on: str


# The value rules ---------------------------------------------------------------------------------

_CONTENT_FIELDS: Final = (
    "type",
    "wording",
    "source_url",
    "segments",
    "markets",
    "metric",
    "metric_confirmed_at",
    "scope_note",
    "last_checked_on",
)
_FILE_FIELDS: Final = ("fact_id", *_CONTENT_FIELDS)
_OPTIONAL_FIELDS: Final = frozenset({"metric", "metric_confirmed_at", "scope_note"})
_REQUIRED_FIELDS: Final = tuple(name for name in _FILE_FIELDS if name not in _OPTIONAL_FIELDS)


class _Problem(Exception):
    """One field's problem: ``item`` is ``""`` or ``"[<i>]"``, appended to the field's path."""

    def __init__(self, reason: str, item: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.item = item


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise _Problem("must be text")
    if not value.strip():
        raise _Problem("must not be blank")
    if value != value.strip():
        raise _Problem("must not start or end with whitespace")
    if any(unicodedata.category(char) in REFUSED_TEXT_CATEGORIES for char in value):
        raise _Problem("must not hold control, format or line-separator characters")
    if len(value) > limit:
        raise _Problem(f"must be at most {limit} characters")
    return value


def _type(value: object) -> FactType:
    allowed = [member.value for member in FactType]
    if not isinstance(value, str) or value not in allowed:
        raise _Problem(f"must be one of {', '.join(allowed)}")
    return FactType(value)


def is_link(url: object) -> bool:
    """Whether ``url`` passes the ``source_url`` rule (bar its length): an ``http`` or ``https``
    URL that ``nextron.web.templating.safe_link`` renders as a link."""
    if not isinstance(url, str) or not url or "\\" in url:
        return False
    if any(unicodedata.category(char) in REFUSED_URL_CATEGORIES for char in url):
        return False
    if any(char.isspace() for char in url):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError when the port doesn't parse
    except ValueError:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    return parts.scheme.lower() in LINK_SCHEMES and bool(parts.hostname)


def _source_url(value: object) -> str:
    if isinstance(value, str) and len(value) > MAX_SOURCE_URL:
        raise _Problem(f"must be at most {MAX_SOURCE_URL} characters")
    if not is_link(value):
        raise _Problem(LINK_REASON)
    assert isinstance(value, str)
    return value


def _references(value: object, pattern: re.Pattern[str], shape: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _Problem("must be a list")
    if not value:
        raise _Problem("must hold at least 1 item")
    if len(value) > MAX_REFERENCES:
        raise _Problem(f"must hold at most {MAX_REFERENCES} items")
    found: list[str] = []
    for index, item in enumerate(value):
        valid = isinstance(item, str) and (pattern.fullmatch(item) is not None or is_ulid(item))
        if not valid:
            raise _Problem(f"must be {shape}", f"[{index}]")
        assert isinstance(item, str)
        if item in found:
            raise _Problem("repeats an earlier item", f"[{index}]")
        found.append(item)
    return tuple(sorted(found))


def _segments(value: object) -> tuple[str, ...]:
    return _references(value, SEGMENT_KEY_PATTERN, "a Segment key such as law_private_ai")


def _markets(value: object) -> tuple[str, ...]:
    return _references(value, MARKET_CODE_PATTERN, "a Market code such as us")


def _date(value: object) -> date:
    if not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None:
        raise _Problem(DATE_REASON)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _Problem(DATE_REASON) from None


_CHECKS: Final[dict[str, Callable[[object], Any]]] = {
    "type": _type,
    "wording": lambda value: _text(value, MAX_WORDING),
    "source_url": _source_url,
    "segments": _segments,
    "markets": _markets,
    "metric": lambda value: _text(value, MAX_METRIC),
    "metric_confirmed_at": _date,
    "scope_note": lambda value: _text(value, MAX_SCOPE_NOTE),
    "last_checked_on": _date,
}


def _rule_problems(checked: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Rules 1 and 2 over the fields that passed their value rules (a missing optional field is
    ``None``)."""
    problems: list[tuple[str, str]] = []
    markets = checked.get("markets")
    if checked.get("type") is FactType.REGULATION and markets is not None and len(markets) != 1:
        problems.append(("markets", REGULATION_MARKETS_REASON))
    if "metric" in checked and "metric_confirmed_at" in checked:
        metric, confirmed = checked["metric"], checked["metric_confirmed_at"]
        if metric is not None and confirmed is None:
            problems.append(("metric", METRIC_UNCONFIRMED_REASON))
        if confirmed is not None and metric is None:
            problems.append(("metric_confirmed_at", CONFIRMATION_WITHOUT_METRIC_REASON))
    return problems


def _base(field: str) -> str:
    """A problem's field without its item index (``segments[2]`` is ``segments``)."""
    return field.partition("[")[0]


def _in_field_order(problems: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    order = {name: index for index, name in enumerate(_FILE_FIELDS)}
    return sorted(problems, key=lambda problem: order.get(_base(problem[0]), len(order)))


def _content(checked: Mapping[str, Any]) -> FactContent:
    return FactContent(
        type=checked["type"],
        wording=checked["wording"],
        source_url=checked["source_url"],
        segments=checked["segments"],
        markets=checked["markets"],
        metric=checked.get("metric"),
        metric_confirmed_at=checked.get("metric_confirmed_at"),
        scope_note=checked.get("scope_note"),
        last_checked_on=checked["last_checked_on"],
    )


_DATE_FIELDS: Final = frozenset({"metric_confirmed_at", "last_checked_on"})


def _is_plain_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _date_text(value: object) -> object:
    """A plain ``date`` as ``YYYY-MM-DD``; anything else (a ``datetime`` included) as it is."""
    return value.isoformat() if _is_plain_date(value) else value  # type: ignore[union-attr]


def _listed(value: object) -> object:
    return list(value) if isinstance(value, (tuple, list)) else value


def _content_form(content: FactContent) -> dict[str, object]:
    """``content``'s fields in their file form, optional ones only when set."""
    form: dict[str, object] = {
        "type": str(content.type) if isinstance(content.type, StrEnum) else content.type,
        "wording": content.wording,
        "source_url": content.source_url,
        "segments": _listed(content.segments),
        "markets": _listed(content.markets),
    }
    if content.metric is not None:
        form["metric"] = content.metric
    if content.metric_confirmed_at is not None:
        form["metric_confirmed_at"] = _date_text(content.metric_confirmed_at)
    if content.scope_note is not None:
        form["scope_note"] = content.scope_note
    form["last_checked_on"] = _date_text(content.last_checked_on)
    return form


def content_problems(content: FactContent) -> list[tuple[str, str]]:
    """The value rules and rules 1 and 2 on ``content``, as ``(field, reason)`` in field
    order."""
    if not isinstance(content, FactContent):
        raise TypeError(f"content_problems takes a FactContent, not {type(content).__name__}")
    form = _content_form(content)
    checked: dict[str, Any] = {name: None for name in _OPTIONAL_FIELDS}
    problems: list[tuple[str, str]] = []
    for name in _CONTENT_FIELDS:
        if name not in form:
            continue
        if name in _DATE_FIELDS and not _is_plain_date(getattr(content, name)):
            checked.pop(name, None)  # a content's date is a date, never its text
            problems.append((name, DATE_REASON))
            continue
        try:
            checked[name] = _CHECKS[name](form[name])
        except _Problem as problem:
            checked.pop(name, None)
            problems.append((f"{name}{problem.item}", problem.reason))
    problems += _rule_problems(checked)
    return _in_field_order(problems)


# References and dates -----------------------------------------------------------------------------


def _reference_problems(
    wording: str | None,
    scope_note: str | None,
    metric: str | None,
    segments: tuple[str, ...] | None,
    markets: tuple[str, ...] | None,
    segment_refs: Collection[str],
    market_countries: Mapping[str, str],
) -> list[tuple[str, str]]:
    """Rules 3 and 4 over whichever of the fields are known (``None``: not checked)."""
    problems: list[tuple[str, str]] = []
    texts = [text for text in (wording, scope_note, metric) if text is not None]
    if markets is not None and texts:
        countries = [market_countries[ref] for ref in markets if ref in market_countries]
        if any(mentions_aba_512(text) for text in texts) and any(c != US for c in countries):
            problems.append(("markets", ABA_REASON))
        if any(mentions_sra_notice(text) for text in texts) and any(c != GB for c in countries):
            problems.append(("markets", SRA_REASON))
    for name, refs, known, kind, stored_reason in (
        ("segments", segments, segment_refs, "Segment", NO_STORED_SEGMENT_REASON),
        ("markets", markets, market_countries, "Market", NO_STORED_MARKET_REASON),
    ):
        for index, ref in enumerate(refs or ()):
            if ref in known:
                continue
            if is_ulid(ref):
                problems.append((f"{name}[{index}]", stored_reason))
            else:
                reason = f"names {ref}, which isn't a {kind} of this sync"
                problems.append((f"{name}[{index}]", reason))
    return problems


def check_fact_references(
    content: FactContent, segment_refs: Collection[str], market_countries: Mapping[str, str]
) -> list[tuple[str, str]]:
    """Rules 3 and 4: the compliance floor on ``markets`` (``market_countries`` maps a Market
    reference to its ``country_code``), then each reference not in ``segment_refs`` or
    ``market_countries``."""
    return _reference_problems(
        content.wording,
        content.scope_note,
        content.metric,
        content.segments,
        content.markets,
        segment_refs,
        market_countries,
    )


def _future_problems(
    dates: Iterable[tuple[str, date | None]], latest: date
) -> list[tuple[str, str]]:
    return [(name, FUTURE_REASON) for name, value in dates if value is not None and value > latest]


def date_problems(content: FactContent, latest: date) -> list[tuple[str, str]]:
    """Rule 5: ``metric_confirmed_at`` and ``last_checked_on`` no later than ``latest``."""
    return _future_problems(
        (
            ("metric_confirmed_at", content.metric_confirmed_at),
            ("last_checked_on", content.last_checked_on),
        ),
        latest,
    )


# The file -----------------------------------------------------------------------------------------


def _unknown_field(name: object, path: str) -> InvalidCompanyFacts:
    if isinstance(name, str) and _FIELD_NAME.fullmatch(name):  # shown only when short and plain
        return InvalidCompanyFacts(f"{path}.{name}" if path else name, "unknown field")
    return InvalidCompanyFacts(path or TOP_LEVEL, "has an unknown field")


def _is_fact_id(value: object) -> bool:
    return isinstance(value, str) and FACT_ID_PATTERN.fullmatch(value) is not None


class _Validator:
    """One validation run: the fact ids seen so far, for the uniqueness rule."""

    def __init__(self) -> None:
        self._ids: set[str] = set()

    def facts(self, data: object) -> tuple[SeedFact, ...]:
        if not isinstance(data, dict):
            raise InvalidCompanyFacts(
                TOP_LEVEL, "must be a mapping with exactly one field, company_facts"
            )
        result: tuple[SeedFact, ...] = ()
        for name, value in data.items():
            if name != "company_facts":
                raise _unknown_field(name, "")
            if not isinstance(value, list):
                raise InvalidCompanyFacts("company_facts", "must be a list")
            if not value:
                raise InvalidCompanyFacts("company_facts", "must hold at least 1 item")
            if len(value) > MAX_FACTS:
                raise InvalidCompanyFacts("company_facts", f"must hold at most {MAX_FACTS} items")
            result = tuple(self.fact(item, index) for index, item in enumerate(value))
        if "company_facts" not in data:
            raise InvalidCompanyFacts("company_facts", "missing")
        return result

    def fact(self, value: object, index: int) -> SeedFact:
        fact_id = value.get("fact_id") if isinstance(value, dict) else None
        shown = fact_id if _is_fact_id(fact_id) and fact_id not in self._ids else str(index)
        path = f"company_facts[{shown}]"
        if not isinstance(value, dict):
            raise InvalidCompanyFacts(path, "must be a mapping")
        checked: dict[str, Any] = {name: None for name in _OPTIONAL_FIELDS}
        for name, item in value.items():
            if not isinstance(name, str) or name not in _FILE_FIELDS:
                raise _unknown_field(name, path)
            if name == "fact_id":
                checked[name] = self._fact_id(item, f"{path}.fact_id")
                continue
            try:
                checked[name] = _CHECKS[name](item)
            except _Problem as problem:
                raise InvalidCompanyFacts(f"{path}.{name}{problem.item}", problem.reason) from None
        for name in _REQUIRED_FIELDS:
            if name not in value:
                raise InvalidCompanyFacts(f"{path}.{name}", "missing")
        for field, reason in _rule_problems(checked):
            raise InvalidCompanyFacts(f"{path}.{field}", reason)
        return SeedFact(checked["fact_id"], _content(checked))

    def _fact_id(self, value: object, path: str) -> str:
        if not _is_fact_id(value):
            raise InvalidCompanyFacts(path, FACT_ID_REASON)
        assert isinstance(value, str)
        if value in self._ids:
            raise InvalidCompanyFacts(path, f"repeats an earlier fact id ({value})")
        self._ids.add(value)
        return value


def validate_company_facts(data: object) -> tuple[SeedFact, ...]:
    """The ``SeedFact``s of a parsed seed file, in file order; ``InvalidCompanyFacts`` for the
    first problem. See the module docstring."""
    return _Validator().facts(data)


def fact_form(fact_id: str, content: FactContent) -> dict[str, object]:
    """The fact as one item of the file: enums as their values, lists as lists, dates as
    ``YYYY-MM-DD``, optional keys only when set."""
    return {"fact_id": fact_id, **_content_form(content)}


def fact_from_form(form: object) -> SeedFact:
    """The ``SeedFact`` of one file-form mapping, validated as one item of a file (path
    ``company_facts[<fact_id>]``); ``InvalidCompanyFacts`` for the first problem."""
    return _Validator().fact(form, 0)


def check_seed_facts(
    facts: Iterable[SeedFact],
    segment_keys: Collection[str],
    market_countries: Mapping[str, str],
    latest: date,
) -> None:
    """``InvalidCompanyFacts`` for the first broken rule 3, 4 or 5, in file order, against the
    Segment keys and ``{code: country_code}`` of the same sync."""
    for fact in facts:
        problems = check_fact_references(fact.content, segment_keys, market_countries)
        problems += date_problems(fact.content, latest)
        for field, reason in problems:
            raise InvalidCompanyFacts(f"company_facts[{fact.fact_id}].{field}", reason)


# The Operator's path ----------------------------------------------------------------------------


def _one_line(text: str) -> str:
    """``text`` stripped, each run of whitespace made one space."""
    return " ".join(text.split())


def fact_from_fields(
    fields: FactFields,
    *,
    segment_refs: Collection[str],
    market_countries: Mapping[str, str],
    latest: date,
) -> FactContent:
    """The ``FactContent`` of the Operator's form; ``InvalidFact`` with every field's first
    problem. See the module docstring."""
    if not isinstance(fields, FactFields):
        raise TypeError(f"fact_from_fields takes FactFields, not {type(fields).__name__}")
    form: dict[str, object] = {
        "type": fields.type.strip(),
        "wording": _one_line(fields.wording),
        "source_url": fields.source_url.strip(),
        "segments": tuple(ref.strip() for ref in fields.segments),
        "markets": tuple(ref.strip() for ref in fields.markets),
        "metric": _one_line(fields.metric),
        "metric_confirmed_at": fields.metric_confirmed_at.strip(),
        "scope_note": _one_line(fields.scope_note),
        "last_checked_on": fields.last_checked_on.strip(),
    }
    problems: dict[str, str] = {}
    checked: dict[str, Any] = {}

    def refuse(field: str, reason: str) -> None:
        problems.setdefault(_base(field), reason)

    for name in _CONTENT_FIELDS:
        value = form[name]
        if not value:  # a blank text, or no Segment or Market ticked
            if name in _OPTIONAL_FIELDS:
                checked[name] = None
            elif name == "type":
                refuse(name, CHOOSE_A_TYPE)
            elif name == "segments":
                refuse(name, CHOOSE_A_SEGMENT)
            elif name == "markets":
                refuse(name, CHOOSE_A_MARKET)
            else:
                refuse(name, ENTER_A_VALUE)
            continue
        try:
            checked[name] = _CHECKS[name](value)
        except _Problem as problem:
            refuse(name, problem.reason)
    for field, reason in _rule_problems(checked):
        refuse(field, reason)
    for field, reason in _reference_problems(
        checked.get("wording"),
        checked.get("scope_note"),
        checked.get("metric"),
        checked.get("segments"),
        checked.get("markets"),
        segment_refs,
        market_countries,
    ):
        refuse(field, reason)
    future = [
        (name, checked.get(name))
        for name in ("metric_confirmed_at", "last_checked_on")
        if name in checked
    ]
    for field, reason in _future_problems(future, latest):
        refuse(field, reason)
    if problems:
        raise InvalidFact(dict(_in_field_order(problems.items())))
    return _content(checked)
