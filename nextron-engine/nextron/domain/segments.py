"""Segment definitions (FR-1, AD-12, Story 2.1): who Nextron targets and what it looks for.

A Segment names its industry keywords, size range, ranked decision-maker roles, disqualifiers,
weighted Signal types and discovery queries. They come from ``seed/segments.yaml`` (read by
``nextron.domain.seed_yaml``) through ``nextron seed sync`` until the Slice 3 editing screens
ship; the ``segments`` table (owner ``setup``) stores each one as ``definition_text``.

**Types** (frozen, slotted; every list is a tuple): ``SizeRange(minimum, maximum, unit)``,
``Disqualifier(key, kind, size_clue, threshold, description)``, ``PreferredRoles(when_terms,
roles)``, ``SignalType(key, name, signal_class, tier, evidence, sources, score_only, weight,
match_terms, preferred_roles)``, ``DiscoveryQueries(places)`` and ``SegmentDef(key, name,
industry_keywords, size_range, decision_maker_roles, disqualifiers, signal_types,
discovery_queries)``. ``decision_maker_roles`` is a tuple of ranks, best first; each rank is a
tuple of equal titles. ``SegmentDef.titles()`` is every decision-maker title: the ranked titles
in rank order, then each preferred role not already listed, in file order.

**The file form.** The field names are the YAML keys, except that ``SignalType.signal_class``
is ``class``. The top level is exactly ``segments: [<segment>, ...]``. A segment has exactly
``key``, ``name``, ``industry_keywords``, ``size_range`` (``minimum``, ``maximum``, ``unit``),
``decision_maker_roles``, ``disqualifiers``, ``signal_types`` and ``discovery_queries``
(``places``); a disqualifier exactly ``key``, ``kind``, ``size_clue``, ``threshold`` and
``description``; a signal type ``key``, ``name``, ``class``, ``tier``, ``evidence``,
``sources``, ``score_only`` and ``weight``, plus optional ``match_terms`` and
``preferred_roles`` (each exactly ``when_terms`` and ``roles``). Missing and unknown fields are
refused.

**Validation.** ``validate_segments(data)`` returns the ``SegmentDef``s in file order, or
raises ``InvalidSegments(field, reason)`` for the first problem in file order (each mapping's
fields in the file's order, then its missing fields, then its rules). The message is
``"<field>: <reason>"``; ``field`` is a path such as
``segments[ops_automation].signal_types[ops_dynamics_gp].weight``, with an item's ``key`` in the
brackets when that key is valid (and not a repeat), else its 0-based index; a reason never
repeats a value longer than 40 characters.

- Keys fullmatch ``[a-z][a-z0-9_]{0,30}`` (31 characters at most: log redaction masks a run of
  32). Segment keys are unique in the file, and so are signal-type keys and disqualifier keys,
  each across all Segments.
- Texts are a ``str``, non-blank, with no leading or trailing whitespace and no ``Cc``, ``Cf``,
  ``Zl`` or ``Zp`` character: ``name`` and ``description`` at most 200 characters, terms,
  titles, keywords and queries at most 100.
- Lists hold 1 to 50 items with no duplicates (case-insensitive for texts); ``disqualifiers``,
  ``match_terms`` and ``preferred_roles`` may be empty.
- Integers are an ``int``, never a ``bool``: ``tier`` 1 to 3, ``weight`` 1 to 100,
  ``threshold`` at least 1, and ``0 <= minimum <= maximum``. ``score_only`` is a ``bool``.
- The rules, each failing on its own field: (1) within a Segment every weight of a lower Tier
  number is above every weight of a higher Tier number, so Tier 1 is above Tier 3 even with no
  Tier 2 (each type is checked against the types before it, so the later one of a clashing pair
  is named); (2) an Event's
  ``evidence`` is exactly ``[quote]``; (3) an Event isn't ``score_only``; (4) a type whose
  ``sources`` include ``careers_page`` or ``ats_board`` (a hiring Signal) has ``match_terms``;
  (5) each ``preferred_roles`` entry's ``when_terms`` are among the type's ``match_terms``
  (case-insensitive); (6) no type represents ABA Formal Opinion 512 or the SRA notice
  (``represents_market_fact`` on its key, its name and each match term), which are Approved
  Company Facts (FR-72), not Signals; (7) every title is unique across a Segment's ranks
  (case-insensitive).

**Stored form.** ``definition_text(segment)`` is the canonical JSON of the segment's file form
(``sort_keys``, ``ensure_ascii=False``, no spaces), with enums as their values and every
optional field present. ``segment_from_text(text)`` is its inverse: ``json.loads`` plus the
same validation of one segment (keys unique within it); a text that no longer validates, or
isn't JSON, raises ``InvalidSegments``.

**The regulation detectors.** ``mentions_aba_512(text)`` and ``mentions_sra_notice(text)`` tell
whether a text names ABA Formal Opinion 512 or the SRA notice; ``represents_market_fact(text)``
is either. The Sequences (``nextron.domain.sequences``, Story 4.1) use the two to keep each
regulation in its own Market's angle.

**The ``signal_types`` block (Story 2.8).** ``signal_catalogue(segments)`` is the text of the
``source="signal_types"`` block an extraction call sends with the untrusted pages, so a new
Segment needs no new prompt version: per Segment, in order, a line ``segment <key>: <name>``,
then a line ``- <signal type key>: <name>`` for each of its Signal types whose ``evidence``
includes ``quote``, in file order. A rule-only type (``absence`` or ``count`` Evidence only) is
left out, because the model can't quote it. No Segments give an empty text; anything but
``SegmentDef``s raises ``TypeError``.
"""

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from nextron.domain.errors import DomainError

KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched with fullmatch
MAX_TEXT: Final = 200  # name, description
MAX_TERM: Final = 100  # terms, titles, keywords, queries
MAX_ITEMS: Final = 50
TOP_LEVEL: Final = "(top level)"  # the field path of a problem with the whole file
TIERS: Final = (1, 2, 3)
MIN_WEIGHT: Final = 1
MAX_WEIGHT: Final = 100  # Signal and Fit Scores cap at 100
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})


class SignalClass(StrEnum):
    EVENT = "event"
    ATTRIBUTE = "attribute"


class EvidenceType(StrEnum):
    QUOTE = "quote"
    ABSENCE = "absence"
    COUNT = "count"


class SignalSource(StrEnum):
    CAREERS_PAGE = "careers_page"
    ATS_BOARD = "ats_board"
    SIGNAL_INBOX = "signal_inbox"
    NEWS_PAGES = "news_pages"
    TEAM_PAGES = "team_pages"
    PRACTICE_PAGES = "practice_pages"
    WEBSITE = "website"
    JOB_ADS = "job_ads"


HIRING_SOURCES: Final = frozenset({SignalSource.CAREERS_PAGE, SignalSource.ATS_BOARD})


class SizeUnit(StrEnum):
    LAWYERS = "lawyers"
    STAFF = "staff"
    USD_REVENUE = "usd_revenue"


class SizeClue(StrEnum):
    """The size clues Story 2.9 extracts."""

    LAWYERS_AT_OFFICE = "lawyers_at_office"
    LAWYERS_FIRM_WIDE = "lawyers_firm_wide"
    STAFF = "staff"
    GROUP_STAFF = "group_staff"
    LOCATIONS = "locations"


class DisqualifierKind(StrEnum):
    FEWER_THAN = "fewer_than"  # the clue is below the threshold
    MORE_THAN = "more_than"  # the clue is above the threshold


class InvalidSegments(DomainError):
    """Segment definitions that break a rule: ``field`` is the path of the first problem and
    ``reason`` says what a valid value is, repeating no value longer than 40 characters."""

    code = "invalid_segments"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SizeRange:
    minimum: int
    maximum: int
    unit: SizeUnit


@dataclass(frozen=True, slots=True)
class Disqualifier:
    key: str
    kind: DisqualifierKind
    size_clue: SizeClue
    threshold: int
    description: str


@dataclass(frozen=True, slots=True)
class PreferredRoles:
    """``roles`` are preferred when a posting matched any of ``when_terms``."""

    when_terms: tuple[str, ...]
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SignalType:
    key: str
    name: str
    signal_class: SignalClass
    tier: int
    evidence: tuple[EvidenceType, ...]
    sources: tuple[SignalSource, ...]
    score_only: bool
    weight: int
    match_terms: tuple[str, ...] = ()
    preferred_roles: tuple[PreferredRoles, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryQueries:
    places: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SegmentDef:
    key: str
    name: str
    industry_keywords: tuple[str, ...]
    size_range: SizeRange
    decision_maker_roles: tuple[tuple[str, ...], ...]
    disqualifiers: tuple[Disqualifier, ...]
    signal_types: tuple[SignalType, ...]
    discovery_queries: DiscoveryQueries

    def titles(self) -> tuple[str, ...]:
        """Every decision-maker title: the ranked ones in rank order, then each preferred role
        not already listed (case-insensitive), in file order."""
        titles = [title for rank in self.decision_maker_roles for title in rank]
        seen = {title.casefold() for title in titles}
        for signal_type in self.signal_types:
            for preferred in signal_type.preferred_roles:
                for role in preferred.roles:
                    if role.casefold() not in seen:
                        seen.add(role.casefold())
                        titles.append(role)
        return tuple(titles)


# Market-wide facts ------------------------------------------------------------------------------

_NOT_WORD: Final = re.compile(r"[^a-z0-9]+")


def _normal_words(text: str) -> tuple[list[str], str]:
    """``text`` lowercased, with each run of characters other than ``[a-z0-9]`` made one space:
    its words, and the result padded with one space at each end (for phrase checks)."""
    normal = _NOT_WORD.sub(" ", text.lower()).strip()
    return normal.split(), f" {normal} "


def mentions_aba_512(text: str) -> bool:
    """Whether ``text`` names ABA Formal Opinion 512: after ``represents_market_fact``'s
    normalising, its words include both ``aba`` and ``512``, or it contains the phrase
    ``formal opinion``."""
    words, padded = _normal_words(text)
    return ("aba" in words and "512" in words) or " formal opinion " in padded


def mentions_sra_notice(text: str) -> bool:
    """Whether ``text`` names the SRA notice: after ``represents_market_fact``'s normalising,
    its words include ``sra``, or it contains the phrase ``solicitors regulation authority``."""
    words, padded = _normal_words(text)
    return "sra" in words or " solicitors regulation authority " in padded


def represents_market_fact(text: str) -> bool:
    """Whether ``text`` names ABA Formal Opinion 512 or the SRA notice: lowercased, with each
    run of characters other than ``[a-z0-9]`` made one space, its words include ``sra``, or
    contain the phrase ``solicitors regulation authority`` or ``formal opinion``, or include
    both ``aba`` and ``512`` (``mentions_aba_512`` or ``mentions_sra_notice``)."""
    return mentions_aba_512(text) or mentions_sra_notice(text)


# Validation ------------------------------------------------------------------------------------

_SIGNAL_REQUIRED: Final = (
    "key",
    "name",
    "class",
    "tier",
    "evidence",
    "sources",
    "score_only",
    "weight",
)
type _Check = Callable[[object, str], Any]


def _is_key(value: object) -> bool:
    return isinstance(value, str) and KEY_PATTERN.fullmatch(value) is not None


def _unknown_field(name: object, path: str) -> InvalidSegments:
    if _is_key(name):  # a short identifier is shown by name; anything else is not repeated
        return InvalidSegments(f"{path}.{name}" if path else name, "unknown field")
    return InvalidSegments(path or TOP_LEVEL, "has an unknown field")


def _fields(
    value: object, path: str, checks: dict[str, _Check], required: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """``value`` as a mapping whose fields are ``checks``: each present field checked in the
    file's order (an unknown one refused where it appears), then the missing ones of
    ``required`` (default: every field) refused."""
    if not isinstance(value, dict):
        raise InvalidSegments(path, "must be a mapping")
    checked: dict[str, Any] = {}
    for name, item in value.items():
        check = checks.get(name) if isinstance(name, str) else None
        if check is None:
            raise _unknown_field(name, path)
        checked[name] = check(item, f"{path}.{name}")
    for name in required if required is not None else tuple(checks):
        if name not in value:
            raise InvalidSegments(f"{path}.{name}", "missing")
    return checked


def _list(value: object, path: str, *, may_be_empty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidSegments(path, "must be a list")
    if not value and not may_be_empty:
        raise InvalidSegments(path, "must hold at least 1 item")
    if len(value) > MAX_ITEMS:
        raise InvalidSegments(path, f"must hold at most {MAX_ITEMS} items")
    return value


def _text(value: object, path: str, limit: int) -> str:
    if not isinstance(value, str):
        raise InvalidSegments(path, "must be text")
    if not value.strip():
        raise InvalidSegments(path, "must not be blank")
    if value != value.strip():
        raise InvalidSegments(path, "must not start or end with whitespace")
    if any(unicodedata.category(char) in REFUSED_TEXT_CATEGORIES for char in value):
        raise InvalidSegments(path, "must not hold control, format or line-separator characters")
    if len(value) > limit:
        raise InvalidSegments(path, f"must be at most {limit} characters")
    return value


def _texts(
    value: object, path: str, limit: int = MAX_TERM, *, may_be_empty: bool = False
) -> tuple[str, ...]:
    """A list of texts, unique case-insensitively."""
    items = _list(value, path, may_be_empty=may_be_empty)
    seen: set[str] = set()
    texts = []
    for index, item in enumerate(items):
        text = _text(item, f"{path}[{index}]", limit)
        if text.casefold() in seen:
            raise InvalidSegments(f"{path}[{index}]", "repeats an earlier item")
        seen.add(text.casefold())
        texts.append(text)
    return tuple(texts)


def _int(value: object, path: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSegments(path, "must be a whole number")
    if maximum is None and value < minimum:
        raise InvalidSegments(path, f"must be at least {minimum}")
    if maximum is not None and not minimum <= value <= maximum:
        raise InvalidSegments(path, f"must be from {minimum} to {maximum}")
    return value


def _enum[E: StrEnum](value: object, path: str, enum: type[E]) -> E:
    allowed = [member.value for member in enum]
    if not isinstance(value, str) or value not in allowed:
        raise InvalidSegments(path, f"must be one of {', '.join(allowed)}")
    return enum(value)


def _enums[E: StrEnum](value: object, path: str, enum: type[E]) -> tuple[E, ...]:
    items = _list(value, path)
    found: list[E] = []
    for index, item in enumerate(items):
        member = _enum(item, f"{path}[{index}]", enum)
        if member in found:
            raise InvalidSegments(f"{path}[{index}]", "repeats an earlier item")
        found.append(member)
    return tuple(found)


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidSegments(path, "must be true or false")
    return value


def _size_range(value: object, path: str) -> SizeRange:
    checked = _fields(
        value,
        path,
        {
            "minimum": lambda v, p: _int(v, p, 0),
            "maximum": lambda v, p: _int(v, p, 0),
            "unit": lambda v, p: _enum(v, p, SizeUnit),
        },
    )
    if checked["minimum"] > checked["maximum"]:
        raise InvalidSegments(f"{path}.maximum", "must be at least the minimum")
    return SizeRange(checked["minimum"], checked["maximum"], checked["unit"])


def _ranks(value: object, path: str) -> tuple[tuple[str, ...], ...]:
    """Ranks of titles; rule 7: a title appears once across the ranks (case-insensitive)."""
    ranks = _list(value, path)
    seen: set[str] = set()
    result = []
    for rank_index, rank in enumerate(ranks):
        rank_path = f"{path}[{rank_index}]"
        titles = []
        for title_index, title in enumerate(_list(rank, rank_path)):
            title_path = f"{rank_path}[{title_index}]"
            text = _text(title, title_path, MAX_TERM)
            if text.casefold() in seen:
                raise InvalidSegments(
                    title_path, "repeats a title already listed in this Segment's ranks"
                )
            seen.add(text.casefold())
            titles.append(text)
        result.append(tuple(titles))
    return tuple(result)


def _preferred_roles(value: object, path: str) -> tuple[PreferredRoles, ...]:
    items = _list(value, path, may_be_empty=True)
    result: list[PreferredRoles] = []
    seen: list[tuple[frozenset[str], frozenset[str]]] = []
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        checked = _fields(
            item,
            item_path,
            {"when_terms": lambda v, p: _texts(v, p), "roles": lambda v, p: _texts(v, p)},
        )
        preferred = PreferredRoles(checked["when_terms"], checked["roles"])
        shape = (_folded(preferred.when_terms), _folded(preferred.roles))
        if shape in seen:
            raise InvalidSegments(item_path, "repeats an earlier item")
        seen.append(shape)
        result.append(preferred)
    return tuple(result)


def _queries(value: object, path: str) -> DiscoveryQueries:
    checked = _fields(value, path, {"places": lambda v, p: _texts(v, p)})
    return DiscoveryQueries(places=checked["places"])


def _folded(texts: Iterable[str]) -> frozenset[str]:
    return frozenset(text.casefold() for text in texts)


MARKET_FACT: Final = (
    "represents a market-wide fact (ABA Formal Opinion 512 or the SRA notice): those are "
    "Approved Company Facts, not Signals"
)


def _check_signal_rules(signal_type: SignalType, path: str) -> None:
    """Rules 2 to 6 on one signal type whose fields are valid."""
    if signal_type.signal_class is SignalClass.EVENT:
        if signal_type.evidence != (EvidenceType.QUOTE,):
            raise InvalidSegments(f"{path}.evidence", "an Event's evidence must be exactly [quote]")
        if signal_type.score_only:
            raise InvalidSegments(f"{path}.score_only", "an Event can't be score-only")
    if HIRING_SOURCES & set(signal_type.sources) and not signal_type.match_terms:
        raise InvalidSegments(
            f"{path}.match_terms",
            "a hiring Signal type (sources careers_page or ats_board) needs match terms",
        )
    terms = _folded(signal_type.match_terms)
    for index, preferred in enumerate(signal_type.preferred_roles):
        for term_index, term in enumerate(preferred.when_terms):
            if term.casefold() not in terms:
                raise InvalidSegments(
                    f"{path}.preferred_roles[{index}].when_terms[{term_index}]",
                    "must be one of the type's match terms",
                )
    if represents_market_fact(signal_type.key):
        raise InvalidSegments(f"{path}.key", MARKET_FACT)
    if represents_market_fact(signal_type.name):
        raise InvalidSegments(f"{path}.name", MARKET_FACT)
    for index, term in enumerate(signal_type.match_terms):
        if represents_market_fact(term):
            raise InvalidSegments(f"{path}.match_terms[{index}]", MARKET_FACT)


def _check_tier_order(signal_type: SignalType, path: str, earlier: Iterable[SignalType]) -> None:
    """Rule 1: ``signal_type``'s weight against every earlier type of its Segment."""
    for other in earlier:
        if other.tier < signal_type.tier and not other.weight > signal_type.weight:
            raise InvalidSegments(
                f"{path}.weight",
                f"a Tier {signal_type.tier} weight must be below every Tier {other.tier} "
                f"weight ({other.key} has {other.weight})",
            )
        if other.tier > signal_type.tier and not signal_type.weight > other.weight:
            raise InvalidSegments(
                f"{path}.weight",
                f"a Tier {signal_type.tier} weight must be above every Tier {other.tier} "
                f"weight ({other.key} has {other.weight})",
            )


class _Validator:
    """One validation run: the keys seen so far, for the uniqueness rules."""

    def __init__(self) -> None:
        self._segment_keys: set[str] = set()
        self._signal_keys: set[str] = set()
        self._disqualifier_keys: set[str] = set()

    @staticmethod
    def _item_path(path: str, index: int, item: object, seen: set[str]) -> str:
        """``path[<key>]`` when the item's key is valid and not a repeat, else
        ``path[<index>]``."""
        key = item.get("key") if isinstance(item, dict) else None
        name = key if _is_key(key) and key not in seen else str(index)
        return f"{path}[{name}]"

    @staticmethod
    def _key(value: object, path: str, seen: set[str], what: str) -> str:
        if not _is_key(value):
            raise InvalidSegments(
                path,
                "must be lowercase letters, digits and underscores, starting with a letter, "
                "at most 31 characters",
            )
        assert isinstance(value, str)
        if value in seen:
            raise InvalidSegments(path, f"repeats an earlier {what} key ({value})")
        seen.add(value)
        return value

    def segments(self, data: object) -> tuple[SegmentDef, ...]:
        if not isinstance(data, dict):
            raise InvalidSegments(TOP_LEVEL, "must be a mapping with exactly one field, segments")
        result: tuple[SegmentDef, ...] = ()
        for name, value in data.items():
            if name != "segments":
                raise _unknown_field(name, "")
            items = _list(value, "segments")
            result = tuple(self.segment(item, index) for index, item in enumerate(items))
        if "segments" not in data:
            raise InvalidSegments("segments", "missing")
        return result

    def segment(self, value: object, index: int) -> SegmentDef:
        path = self._item_path("segments", index, value, self._segment_keys)
        checked = _fields(
            value,
            path,
            {
                "key": lambda v, p: self._key(v, p, self._segment_keys, "segment"),
                "name": lambda v, p: _text(v, p, MAX_TEXT),
                "industry_keywords": lambda v, p: _texts(v, p),
                "size_range": _size_range,
                "decision_maker_roles": _ranks,
                "disqualifiers": self._disqualifiers,
                "signal_types": self._signal_types,
                "discovery_queries": _queries,
            },
        )
        return SegmentDef(
            key=checked["key"],
            name=checked["name"],
            industry_keywords=checked["industry_keywords"],
            size_range=checked["size_range"],
            decision_maker_roles=checked["decision_maker_roles"],
            disqualifiers=checked["disqualifiers"],
            signal_types=checked["signal_types"],
            discovery_queries=checked["discovery_queries"],
        )

    def _disqualifiers(self, value: object, path: str) -> tuple[Disqualifier, ...]:
        items = _list(value, path, may_be_empty=True)
        result = []
        for index, item in enumerate(items):
            item_path = self._item_path(path, index, item, self._disqualifier_keys)
            checked = _fields(
                item,
                item_path,
                {
                    "key": lambda v, p: self._key(v, p, self._disqualifier_keys, "disqualifier"),
                    "kind": lambda v, p: _enum(v, p, DisqualifierKind),
                    "size_clue": lambda v, p: _enum(v, p, SizeClue),
                    "threshold": lambda v, p: _int(v, p, 1),
                    "description": lambda v, p: _text(v, p, MAX_TEXT),
                },
            )
            result.append(
                Disqualifier(
                    key=checked["key"],
                    kind=checked["kind"],
                    size_clue=checked["size_clue"],
                    threshold=checked["threshold"],
                    description=checked["description"],
                )
            )
        return tuple(result)

    def _signal_types(self, value: object, path: str) -> tuple[SignalType, ...]:
        items = _list(value, path)
        result: list[SignalType] = []
        for index, item in enumerate(items):
            item_path = self._item_path(path, index, item, self._signal_keys)
            signal_type = self._signal_type(item, item_path)
            _check_tier_order(signal_type, item_path, result)
            result.append(signal_type)
        return tuple(result)

    def _signal_type(self, value: object, path: str) -> SignalType:
        checked = _fields(
            value,
            path,
            {
                "key": lambda v, p: self._key(v, p, self._signal_keys, "signal type"),
                "name": lambda v, p: _text(v, p, MAX_TEXT),
                "class": lambda v, p: _enum(v, p, SignalClass),
                "tier": lambda v, p: _int(v, p, TIERS[0], TIERS[-1]),
                "evidence": lambda v, p: _enums(v, p, EvidenceType),
                "sources": lambda v, p: _enums(v, p, SignalSource),
                "score_only": _bool,
                "weight": lambda v, p: _int(v, p, MIN_WEIGHT, MAX_WEIGHT),
                "match_terms": lambda v, p: _texts(v, p, may_be_empty=True),
                "preferred_roles": _preferred_roles,
            },
            required=_SIGNAL_REQUIRED,
        )
        signal_type = SignalType(
            key=checked["key"],
            name=checked["name"],
            signal_class=checked["class"],
            tier=checked["tier"],
            evidence=checked["evidence"],
            sources=checked["sources"],
            score_only=checked["score_only"],
            weight=checked["weight"],
            match_terms=checked.get("match_terms", ()),
            preferred_roles=checked.get("preferred_roles", ()),
        )
        _check_signal_rules(signal_type, path)
        return signal_type


def validate_segments(data: object) -> tuple[SegmentDef, ...]:
    """The ``SegmentDef``s of a parsed seed file, in file order; ``InvalidSegments`` for the
    first problem. See the module docstring."""
    return _Validator().segments(data)


# The stored form --------------------------------------------------------------------------------


def _file_form(segment: SegmentDef) -> dict[str, object]:
    """The segment as the file's mapping, enums as their values, every optional field present."""
    return {
        "key": segment.key,
        "name": segment.name,
        "industry_keywords": list(segment.industry_keywords),
        "size_range": {
            "minimum": segment.size_range.minimum,
            "maximum": segment.size_range.maximum,
            "unit": str(segment.size_range.unit),
        },
        "decision_maker_roles": [list(rank) for rank in segment.decision_maker_roles],
        "disqualifiers": [
            {
                "key": disqualifier.key,
                "kind": str(disqualifier.kind),
                "size_clue": str(disqualifier.size_clue),
                "threshold": disqualifier.threshold,
                "description": disqualifier.description,
            }
            for disqualifier in segment.disqualifiers
        ],
        "signal_types": [
            {
                "key": signal_type.key,
                "name": signal_type.name,
                "class": str(signal_type.signal_class),
                "tier": signal_type.tier,
                "evidence": [str(item) for item in signal_type.evidence],
                "sources": [str(item) for item in signal_type.sources],
                "score_only": signal_type.score_only,
                "weight": signal_type.weight,
                "match_terms": list(signal_type.match_terms),
                "preferred_roles": [
                    {"when_terms": list(preferred.when_terms), "roles": list(preferred.roles)}
                    for preferred in signal_type.preferred_roles
                ],
            }
            for signal_type in segment.signal_types
        ],
        "discovery_queries": {"places": list(segment.discovery_queries.places)},
    }


def definition_text(segment: SegmentDef) -> str:
    """The canonical JSON of ``segment``'s file form: what the ``segments`` table stores."""
    return json.dumps(
        _file_form(segment), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def _no_repeated_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("a JSON object repeats a field")
        result[name] = value
    return result


def segment_from_text(text: str) -> SegmentDef:
    """The ``SegmentDef`` that ``definition_text`` stored as ``text``, validated again;
    ``InvalidSegments`` when it no longer validates or isn't JSON."""
    try:
        data = json.loads(text, object_pairs_hook=_no_repeated_fields)
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        raise InvalidSegments("definition", "is not valid JSON") from None
    return _Validator().segment(data, 0)


# The signal_types block (Story 2.8) --------------------------------------------------------------


def signal_catalogue(segments: Iterable[SegmentDef]) -> str:
    """The text of the ``signal_types`` block an extraction call sends; see the module
    docstring."""
    chosen = tuple(segments)
    if not all(isinstance(segment, SegmentDef) for segment in chosen):
        raise TypeError("signal_catalogue takes SegmentDefs")
    lines: list[str] = []
    for segment in chosen:
        lines.append(f"segment {segment.key}: {segment.name}")
        lines.extend(
            f"- {signal_type.key}: {signal_type.name}"
            for signal_type in segment.signal_types
            if EvidenceType.QUOTE in signal_type.evidence
        )
    return "\n".join(lines)
