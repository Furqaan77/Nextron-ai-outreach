"""Sequences and Step briefs (FR-4, FR-71, AD-12, Story 4.1): what each Step of a Sequence says.

A Sequence is a list of Steps, each sent a number of days after the Sequence starts, each with
its brief: its purpose, word limit, allowed content, ask, link rule and the ``no_price`` and
``no_audit`` flags; Step 1 also has a subject rule, and a Step may give a Segment's angle per
Market. They come from ``seed/sequences.yaml`` (read by ``nextron.domain.seed_yaml``) through
``nextron seed sync``; the ``sequences`` and ``step_briefs`` tables (owner ``setup``) store
them. Campaigns (4.2), Draft slots and due times (4.9), the grounding rules (4.13) and drafting
(4.15, 4.18) read them.

**Types** (frozen, slotted; every list is a tuple): ``WordComparison`` (``under``: fewer words
than the limit; ``at_most``: no more than the limit), ``LinkRule`` (``footer_only``: no link in
the body; the signature and footer hold the only links), ``SubjectRule(word_limit,
word_comparison, guidance)``, ``SegmentAngle(segment, market, angle)`` (a Segment key, a Market
code and the angle text), ``StepBrief(step, delay_days, purpose, word_limit, word_comparison,
allowed_content, ask, link_rule, no_price, no_audit, subject_rule, segment_angles)`` (``step``
is the 1-based position; ``subject_rule`` is ``None`` when the Step has none) and
``SequenceDef(key, name, steps)``. ``max_words(word_limit, comparison)`` is the most words a
limit allows: ``word_limit - 1`` for ``under``, ``word_limit`` for ``at_most``.

**The file form.** The top level is exactly ``sequences: [<sequence>, ...]``, with 1 to 20
items. A sequence has exactly ``key``, ``name`` and ``steps`` (1 to 6 items). A Step has
``delay_days``, ``purpose``, ``word_limit``, ``word_comparison``, ``allowed_content``, ``ask``,
``link_rule``, ``no_price`` and ``no_audit``, plus optional ``subject_rule`` (exactly
``word_limit``, ``word_comparison`` and ``guidance``) and optional ``segment_angles`` (each
exactly ``segment``, ``market`` and ``angle``). Missing and unknown keys are refused. Steps carry
no number: a Step's number is its position.

**Validation.** ``validate_sequences(data)`` returns the ``SequenceDef``s in file order, or
raises ``InvalidSequences(field, reason)`` (code ``invalid_sequences``) for the first problem in
file order (each mapping's fields in the file's order, then its missing fields, then its rules).
The message is ``"<field>: <reason>"``; ``field`` is a path such as
``sequences[default].steps[2].ask``. A sequence's brackets hold its ``key`` when that key is
valid (and not a repeat), else its 0-based index; a Step's brackets hold its Step number
(1-based); other list items use their 0-based index. A reason never repeats a value longer than
40 characters.

- ``key`` fullmatches ``[a-z][a-z0-9_]{0,30}`` and is unique in the file; an angle's
  ``segment`` fullmatches the same pattern and its ``market`` fullmatches
  ``[a-z][a-z0-9_]{0,15}`` (a Market code).
- Texts are a ``str``, non-blank, with no leading or trailing whitespace and no ``Cc``, ``Cf``,
  ``Zl`` or ``Zp`` character (Story 2.1's rules): ``name`` at most 100 characters; ``purpose``,
  ``ask``, each ``allowed_content`` item, each ``guidance`` item and ``angle`` at most 200.
- ``allowed_content`` and ``guidance`` hold 1 to 10 items with no duplicates (case-insensitive);
  ``segment_angles`` holds 0 to 20 items with no repeated (``segment``, ``market``) pair, and an
  absent one is empty.
- Integers are an ``int``, never a ``bool``: ``delay_days`` 0 to 365, ``word_limit`` 1 to 150,
  a subject rule's ``word_limit`` 1 to 20. ``word_comparison`` is a ``WordComparison``,
  ``link_rule`` a ``LinkRule``, and ``no_price`` and ``no_audit`` are ``bool``. Once a Step's
  (or a subject rule's) fields are checked, its limit must allow at least 1 word (``max_words``
  of at least 1, so 1 with ``under`` fails on that ``word_limit``).
- **The rules**, the AC's then the compliance floor, each failing on its own field: (1) Step 1's
  ``delay_days`` is 0; (2) each later Step's ``delay_days`` is greater than the previous Step's;
  (3) ``no_price`` and ``no_audit`` are true on every Step (FR-4); (4) Step 1's ``link_rule`` is
  ``footer_only`` (FR-4); (5) Step 1 has a ``subject_rule`` whose ``max_words`` is at most 6
  (fewer than 7 words, FR-4), and a later Step has none (follow-ups reply in Step 1's thread,
  AD-22); (6) an angle that names ABA Formal Opinion 512 is a US Market's, and one that names
  the SRA notice a GB Market's (``check_sequence_references``); (7) no ``name``, ``purpose``,
  ``allowed_content`` item, ``ask`` or ``guidance`` item names either regulation
  (``nextron.domain.segments.mentions_aba_512`` and ``mentions_sra_notice``): a Market's
  regulatory fact goes only in a ``segment_angles`` entry for that Market. A Step's rules run in
  that order, rule 7 over its fields in ``StepBrief`` order; a sequence's own rule (7, on its
  ``name``) runs after its Steps.

**References.** ``check_sequence_references(sequences, segment_keys, market_countries)``
(``market_countries`` maps a Market code to its ``country_code``) raises ``InvalidSequences``
for the first angle, in file order, whose ``segment`` isn't in ``segment_keys``, whose
``market`` isn't in ``market_countries``, or that breaks rule 6, checked in that order. Seed
sync checks the angles against the Segments and Markets of the same sync.

**The file form back.** ``sequence_form(sequence)`` is the sequence's file mapping (enums as
their values, lists as lists, no Step numbers, ``subject_rule`` only when set and
``segment_angles`` only when non-empty; a value of the wrong type is left as it is, so validation
refuses it); ``sequence_from_form(form)`` validates one such mapping, so
``sequence_from_form(sequence_form(s)) == s`` for a valid ``s``. The ``sequences`` repository
stores and decodes Sequences through these two.

**Dashboard editing (Story 11.7).** ``definition_text(sequence)`` is the canonical JSON of
``sequence_form`` that a ``sequence_versions`` row stores. ``SequenceRefused(problems)`` is a
refused dashboard save (``{form field: reason}``); ``edited_field(path)`` turns a validator path
(``sequences[default].steps[3].delay_days``) into the editor's form field
(``steps[3].delay_days``). ``sequence_form_diff(old, new)`` lists each changed field as a
``FieldChange(path, old, new)`` (``nextron.domain.segments``' type), Steps matched by number,
for the version history view.
"""

import json
import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from nextron.domain.errors import DomainError
from nextron.domain.segments import FORM_FIELD, FieldChange, mentions_aba_512, mentions_sra_notice

KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched with fullmatch
MARKET_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,15}")  # nextron.domain.markets.CODE_PATTERN
_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # an unknown field shown by name

TOP_LEVEL: Final = "(top level)"  # the field path of a problem with the whole file
MAX_SEQUENCES: Final = 20
MAX_STEPS: Final = 6
MAX_NAME: Final = 100
MAX_TEXT: Final = 200  # purpose, ask, allowed_content and guidance items, angle
MAX_LIST_ITEMS: Final = 10  # allowed_content, guidance
MAX_SEGMENT_ANGLES: Final = 20
MAX_DELAY_DAYS: Final = 365
MIN_WORD_LIMIT: Final = 1
MAX_WORD_LIMIT: Final = 150
MAX_SUBJECT_WORD_LIMIT: Final = 20
FIRST_STEP: Final = 1
FIRST_STEP_DELAY: Final = 0
MAX_SUBJECT_WORDS: Final = 6  # FR-4: Step 1's subject has fewer than 7 words
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
US: Final = "US"
GB: Final = "GB"
MIN_WORDS: Final = 1  # a word limit must allow at least one word
ALLOWS_NO_WORD_REASON: Final = "must allow at least 1 word: use 2 or more with under"
REGULATION_REASON: Final = (
    "must not name ABA Formal Opinion 512 or the SRA notice: a Market's regulatory fact goes "
    "only in a segment_angles entry for that Market"
)


class WordComparison(StrEnum):
    """How a word limit counts: ``under`` (fewer words than the limit) or ``at_most`` (no more
    than the limit)."""

    UNDER = "under"
    AT_MOST = "at_most"


class LinkRule(StrEnum):
    """Where a Step's message may hold a link: ``footer_only`` (no link in the body; the
    signature and footer hold the only links)."""

    FOOTER_ONLY = "footer_only"


class InvalidSequences(DomainError):
    """Sequence definitions that break a rule: ``field`` is the path of the first problem and
    ``reason`` says what a valid value is, repeating no value longer than 40 characters."""

    code = "invalid_sequences"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SubjectRule:
    """Step 1's subject: at most ``max_words(word_limit, word_comparison)`` words, written to
    each ``guidance`` item."""

    word_limit: int
    word_comparison: WordComparison
    guidance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SegmentAngle:
    """The angle a Step takes for one Segment (``segment``, its key) in one Market (``market``,
    its code)."""

    segment: str
    market: str
    angle: str


@dataclass(frozen=True, slots=True)
class StepBrief:
    step: int  # the 1-based position in the Sequence
    delay_days: int
    purpose: str
    word_limit: int
    word_comparison: WordComparison
    allowed_content: tuple[str, ...]
    ask: str
    link_rule: LinkRule
    no_price: bool
    no_audit: bool
    subject_rule: SubjectRule | None
    segment_angles: tuple[SegmentAngle, ...]


@dataclass(frozen=True, slots=True)
class SequenceDef:
    key: str
    name: str
    steps: tuple[StepBrief, ...]


def max_words(word_limit: int, comparison: WordComparison) -> int:
    """The most words ``word_limit`` allows: one fewer for ``under``, the limit for
    ``at_most``."""
    if comparison == WordComparison.UNDER:
        return word_limit - 1
    if comparison == WordComparison.AT_MOST:
        return word_limit
    raise ValueError("comparison must be a WordComparison: under or at_most")


def _names_regulation(text: str) -> bool:
    return mentions_aba_512(text) or mentions_sra_notice(text)


# Validation ------------------------------------------------------------------------------------

_STEP_FIELDS: Final = (
    "delay_days",
    "purpose",
    "word_limit",
    "word_comparison",
    "allowed_content",
    "ask",
    "link_rule",
    "no_price",
    "no_audit",
    "subject_rule",
    "segment_angles",
)
_STEP_OPTIONAL: Final = frozenset({"subject_rule", "segment_angles"})
_STEP_REQUIRED: Final = tuple(name for name in _STEP_FIELDS if name not in _STEP_OPTIONAL)
_SEQUENCE_REQUIRED: Final = ("key", "name", "steps")
_SUBJECT_REQUIRED: Final = ("word_limit", "word_comparison", "guidance")
_ANGLE_REQUIRED: Final = ("segment", "market", "angle")
_KEY_REASON: Final = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 31 "
    "characters"
)
_MARKET_REASON: Final = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 16 "
    "characters"
)

type _Check = Callable[[object, str], Any]


def _unknown_field(name: object, path: str) -> InvalidSequences:
    if isinstance(name, str) and _FIELD_NAME.fullmatch(name):  # shown only when short and plain
        return InvalidSequences(f"{path}.{name}" if path else name, "unknown field")
    return InvalidSequences(path or TOP_LEVEL, "has an unknown field")


def _fields(
    value: object, path: str, checks: dict[str, _Check], required: tuple[str, ...]
) -> dict[str, Any]:
    """``value`` as a mapping whose fields are ``checks``: each present field checked in the
    file's order (an unknown one refused where it appears), then the missing ones of
    ``required`` refused."""
    if not isinstance(value, dict):
        raise InvalidSequences(path, "must be a mapping")
    checked: dict[str, Any] = {}
    for name, item in value.items():
        check = checks.get(name) if isinstance(name, str) else None
        if check is None:
            raise _unknown_field(name, path)
        checked[name] = check(item, f"{path}.{name}")
    for name in required:
        if name not in value:
            raise InvalidSequences(f"{path}.{name}", "missing")
    return checked


def _list(value: object, path: str, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidSequences(path, "must be a list")
    if len(value) < minimum:
        raise InvalidSequences(path, f"must hold at least {minimum} item")
    if len(value) > maximum:
        raise InvalidSequences(path, f"must hold at most {maximum} items")
    return value


def _text(value: object, path: str, limit: int) -> str:
    if not isinstance(value, str):
        raise InvalidSequences(path, "must be text")
    if not value.strip():
        raise InvalidSequences(path, "must not be blank")
    if value != value.strip():
        raise InvalidSequences(path, "must not start or end with whitespace")
    if any(unicodedata.category(char) in REFUSED_TEXT_CATEGORIES for char in value):
        raise InvalidSequences(path, "must not hold control, format or line-separator characters")
    if len(value) > limit:
        raise InvalidSequences(path, f"must be at most {limit} characters")
    return value


def _long_text(value: object, path: str) -> str:
    return _text(value, path, MAX_TEXT)


def _texts(value: object, path: str) -> tuple[str, ...]:
    """1 to 10 texts, unique case-insensitively."""
    items = _list(value, path, 1, MAX_LIST_ITEMS)
    seen: set[str] = set()
    texts = []
    for index, item in enumerate(items):
        text = _text(item, f"{path}[{index}]", MAX_TEXT)
        if text.casefold() in seen:
            raise InvalidSequences(f"{path}[{index}]", "repeats an earlier item")
        seen.add(text.casefold())
        texts.append(text)
    return tuple(texts)


def _int(value: object, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSequences(path, "must be a whole number")
    if not minimum <= value <= maximum:
        raise InvalidSequences(path, f"must be from {minimum} to {maximum}")
    return value


def _delay_days(value: object, path: str) -> int:
    return _int(value, path, 0, MAX_DELAY_DAYS)


def _word_limit(value: object, path: str) -> int:
    return _int(value, path, MIN_WORD_LIMIT, MAX_WORD_LIMIT)


def _subject_word_limit(value: object, path: str) -> int:
    return _int(value, path, MIN_WORD_LIMIT, MAX_SUBJECT_WORD_LIMIT)


def _enum[E: StrEnum](value: object, path: str, enum: type[E]) -> E:
    allowed = [member.value for member in enum]
    if not isinstance(value, str) or value not in allowed:
        raise InvalidSequences(path, f"must be one of {', '.join(allowed)}")
    return enum(value)


def _word_comparison(value: object, path: str) -> WordComparison:
    return _enum(value, path, WordComparison)


def _link_rule(value: object, path: str) -> LinkRule:
    return _enum(value, path, LinkRule)


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidSequences(path, "must be true or false")
    return value


def _is_key(value: object) -> bool:
    return isinstance(value, str) and KEY_PATTERN.fullmatch(value) is not None


def _segment_key(value: object, path: str) -> str:
    if not _is_key(value):
        raise InvalidSequences(path, _KEY_REASON)
    assert isinstance(value, str)
    return value


def _market_code(value: object, path: str) -> str:
    if not isinstance(value, str) or MARKET_PATTERN.fullmatch(value) is None:
        raise InvalidSequences(path, _MARKET_REASON)
    return value


def _subject_rule(value: object, path: str) -> SubjectRule:
    checked = _fields(
        value,
        path,
        {
            "word_limit": _subject_word_limit,
            "word_comparison": _word_comparison,
            "guidance": _texts,
        },
        _SUBJECT_REQUIRED,
    )
    _check_allows_a_word(checked["word_limit"], checked["word_comparison"], path)
    return SubjectRule(
        word_limit=checked["word_limit"],
        word_comparison=checked["word_comparison"],
        guidance=checked["guidance"],
    )


def _check_allows_a_word(word_limit: int, comparison: WordComparison, path: str) -> None:
    """A limit and comparison (already checked) that allow no word fail on the limit."""
    if max_words(word_limit, comparison) < MIN_WORDS:
        raise InvalidSequences(f"{path}.word_limit", ALLOWS_NO_WORD_REASON)


def _segment_angles(value: object, path: str) -> tuple[SegmentAngle, ...]:
    """0 to 20 angles, no two for the same Segment and Market."""
    items = _list(value, path, 0, MAX_SEGMENT_ANGLES)
    angles: list[SegmentAngle] = []
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        checked = _fields(
            item,
            item_path,
            {"segment": _segment_key, "market": _market_code, "angle": _long_text},
            _ANGLE_REQUIRED,
        )
        angle = SegmentAngle(checked["segment"], checked["market"], checked["angle"])
        if any((a.segment, a.market) == (angle.segment, angle.market) for a in angles):
            raise InvalidSequences(
                item_path,
                f"repeats the segment and market of an earlier angle ({angle.segment}, "
                f"{angle.market})",
            )
        angles.append(angle)
    return tuple(angles)


def _check_step_rules(brief: StepBrief, path: str, previous: StepBrief | None) -> None:
    """Rules 1 to 5 and 7 of one Step; ``previous`` is the Step before it, if any."""
    if brief.step == FIRST_STEP and brief.delay_days != FIRST_STEP_DELAY:
        raise InvalidSequences(
            f"{path}.delay_days", "must be 0: Step 1 is sent when the Sequence starts"
        )
    if previous is not None and brief.delay_days <= previous.delay_days:
        raise InvalidSequences(
            f"{path}.delay_days",
            f"must be more than Step {previous.step}'s delay_days ({previous.delay_days})",
        )
    if brief.no_price is not True:
        raise InvalidSequences(f"{path}.no_price", "must be true: no Step names a price (FR-4)")
    if brief.no_audit is not True:
        raise InvalidSequences(
            f"{path}.no_audit", "must be true: no Step proposes the audit (FR-4)"
        )
    if brief.step == FIRST_STEP and brief.link_rule != LinkRule.FOOTER_ONLY:
        raise InvalidSequences(
            f"{path}.link_rule",
            "must be footer_only for Step 1: no link outside the signature and footer (FR-4)",
        )
    rule = brief.subject_rule
    if brief.step == FIRST_STEP:
        if rule is None:
            raise InvalidSequences(
                f"{path}.subject_rule",
                "missing: Step 1 needs a subject rule (FR-4: fewer than 7 words)",
            )
        if max_words(rule.word_limit, rule.word_comparison) > MAX_SUBJECT_WORDS:
            raise InvalidSequences(
                f"{path}.subject_rule.word_limit",
                "must allow fewer than 7 words: at most 7 with under, or at most 6 with "
                "at_most (FR-4)",
            )
    elif rule is not None:
        raise InvalidSequences(
            f"{path}.subject_rule",
            "only Step 1 has a subject rule: follow-ups reply in Step 1's thread (AD-22)",
        )
    texts = [("purpose", brief.purpose)]
    texts += [(f"allowed_content[{i}]", text) for i, text in enumerate(brief.allowed_content)]
    texts.append(("ask", brief.ask))
    if rule is not None:
        texts += [(f"subject_rule.guidance[{i}]", text) for i, text in enumerate(rule.guidance)]
    for field, text in texts:
        if _names_regulation(text):
            raise InvalidSequences(f"{path}.{field}", REGULATION_REASON)


def _step(value: object, path: str, number: int, previous: StepBrief | None) -> StepBrief:
    checked = _fields(
        value,
        path,
        {
            "delay_days": _delay_days,
            "purpose": _long_text,
            "word_limit": _word_limit,
            "word_comparison": _word_comparison,
            "allowed_content": _texts,
            "ask": _long_text,
            "link_rule": _link_rule,
            "no_price": _bool,
            "no_audit": _bool,
            "subject_rule": _subject_rule,
            "segment_angles": _segment_angles,
        },
        _STEP_REQUIRED,
    )
    _check_allows_a_word(checked["word_limit"], checked["word_comparison"], path)
    brief = StepBrief(
        step=number,
        delay_days=checked["delay_days"],
        purpose=checked["purpose"],
        word_limit=checked["word_limit"],
        word_comparison=checked["word_comparison"],
        allowed_content=checked["allowed_content"],
        ask=checked["ask"],
        link_rule=checked["link_rule"],
        no_price=checked["no_price"],
        no_audit=checked["no_audit"],
        subject_rule=checked.get("subject_rule"),
        segment_angles=checked.get("segment_angles", ()),
    )
    _check_step_rules(brief, path, previous)
    return brief


def _steps(value: object, path: str) -> tuple[StepBrief, ...]:
    """1 to 6 Steps, each numbered by its position (``steps[1]`` is the first)."""
    items = _list(value, path, 1, MAX_STEPS)
    steps: list[StepBrief] = []
    for number, item in enumerate(items, start=FIRST_STEP):
        previous = steps[-1] if steps else None
        steps.append(_step(item, f"{path}[{number}]", number, previous))
    return tuple(steps)


class _Validator:
    """One validation run: the Sequence keys seen so far, for the uniqueness rule."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def sequences(self, data: object) -> tuple[SequenceDef, ...]:
        if not isinstance(data, dict):
            raise InvalidSequences(TOP_LEVEL, "must be a mapping with exactly one field, sequences")
        result: tuple[SequenceDef, ...] = ()
        for name, value in data.items():
            if name != "sequences":
                raise _unknown_field(name, "")
            items = _list(value, "sequences", 1, MAX_SEQUENCES)
            result = tuple(self.sequence(item, index) for index, item in enumerate(items))
        if "sequences" not in data:
            raise InvalidSequences("sequences", "missing")
        return result

    def sequence(self, value: object, index: int) -> SequenceDef:
        key = value.get("key") if isinstance(value, dict) else None
        shown = key if _is_key(key) and key not in self._keys else str(index)
        path = f"sequences[{shown}]"
        checked = _fields(
            value,
            path,
            {"key": self._key, "name": lambda v, p: _text(v, p, MAX_NAME), "steps": _steps},
            _SEQUENCE_REQUIRED,
        )
        if _names_regulation(checked["name"]):
            raise InvalidSequences(f"{path}.name", REGULATION_REASON)
        return SequenceDef(key=checked["key"], name=checked["name"], steps=checked["steps"])

    def _key(self, value: object, path: str) -> str:
        if not _is_key(value):
            raise InvalidSequences(path, _KEY_REASON)
        assert isinstance(value, str)
        if value in self._keys:
            raise InvalidSequences(path, f"repeats an earlier Sequence key ({value})")
        self._keys.add(value)
        return value


def validate_sequences(data: object) -> tuple[SequenceDef, ...]:
    """The ``SequenceDef``s of a parsed seed file, in file order; ``InvalidSequences`` for the
    first problem. See the module docstring."""
    return _Validator().sequences(data)


# References ------------------------------------------------------------------------------------


def check_sequence_references(
    sequences: Iterable[SequenceDef],
    segment_keys: Collection[str],
    market_countries: Mapping[str, str],
) -> None:
    """``InvalidSequences`` for the first angle, in file order, that names a Segment not in
    ``segment_keys`` or a Market not in ``market_countries`` (a Market code to its
    ``country_code``), or that cites a regulation outside its Market's country (rule 6)."""
    for sequence in sequences:
        for brief in sequence.steps:
            for index, angle in enumerate(brief.segment_angles):
                path = f"sequences[{sequence.key}].steps[{brief.step}].segment_angles[{index}]"
                if angle.segment not in segment_keys:
                    raise InvalidSequences(
                        f"{path}.segment", "isn't the key of a Segment in this seed sync"
                    )
                country = market_countries.get(angle.market)
                if country is None:
                    raise InvalidSequences(
                        f"{path}.market", "isn't the code of a Market in this seed sync"
                    )
                if mentions_aba_512(angle.angle) and country != US:
                    raise InvalidSequences(
                        f"{path}.angle",
                        "names ABA Formal Opinion 512, which only a US Market's angle may "
                        f"(market {angle.market} is {country})",
                    )
                if mentions_sra_notice(angle.angle) and country != GB:
                    raise InvalidSequences(
                        f"{path}.angle",
                        "names the SRA notice, which only a GB Market's angle may (market "
                        f"{angle.market} is {country})",
                    )


# The file form ----------------------------------------------------------------------------------


def _listed(value: object) -> object:
    """A tuple or list as a list; anything else as it is, so validation refuses it."""
    return list(value) if isinstance(value, (tuple, list)) else value


def _subject_form(rule: object) -> object:
    if not isinstance(rule, SubjectRule):
        return rule
    return {
        "word_limit": rule.word_limit,
        "word_comparison": _enum_value(rule.word_comparison),
        "guidance": _listed(rule.guidance),
    }


def _angle_form(angle: object) -> object:
    if not isinstance(angle, SegmentAngle):
        return angle
    return {"segment": angle.segment, "market": angle.market, "angle": angle.angle}


def _enum_value(value: object) -> object:
    """A ``StrEnum`` member as its plain value; anything else as it is."""
    return str(value) if isinstance(value, StrEnum) else value


def _step_form(brief: object) -> object:
    if not isinstance(brief, StepBrief):
        return brief
    form: dict[str, object] = {
        "delay_days": brief.delay_days,
        "purpose": brief.purpose,
        "word_limit": brief.word_limit,
        "word_comparison": _enum_value(brief.word_comparison),
        "allowed_content": _listed(brief.allowed_content),
        "ask": brief.ask,
        "link_rule": _enum_value(brief.link_rule),
        "no_price": brief.no_price,
        "no_audit": brief.no_audit,
    }
    if brief.subject_rule is not None:
        form["subject_rule"] = _subject_form(brief.subject_rule)
    angles = brief.segment_angles
    if not (isinstance(angles, (tuple, list)) and not angles):  # an empty list is left out
        form["segment_angles"] = (
            [_angle_form(angle) for angle in angles]
            if isinstance(angles, (tuple, list))
            else angles
        )
    return form


def sequence_form(sequence: SequenceDef) -> dict[str, object]:
    """``sequence`` as the file's mapping: enums as their values, lists as lists, no Step
    numbers, ``subject_rule`` only when set and ``segment_angles`` only when non-empty."""
    steps = sequence.steps
    return {
        "key": sequence.key,
        "name": sequence.name,
        "steps": (
            [_step_form(brief) for brief in steps] if isinstance(steps, (tuple, list)) else steps
        ),
    }


def sequence_from_form(form: object) -> SequenceDef:
    """The ``SequenceDef`` of one file-form mapping, validated as one item of a file (path
    ``sequences[<key>]``); ``InvalidSequences`` for the first problem."""
    return _Validator().sequence(form, 0)


# Dashboard editing (Story 11.7) -----------------------------------------------------------------


def definition_text(sequence: SequenceDef) -> str:
    """The canonical JSON of ``sequence_form(sequence)``: ``json.dumps`` with ``sort_keys``,
    ``ensure_ascii=False`` and no spaces. A ``sequence_versions`` row stores it."""
    return json.dumps(
        sequence_form(sequence), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


class SequenceRefused(DomainError):
    """A dashboard Sequence save refused, nothing written: ``problems`` maps each refused form
    field (``name``, ``steps[<n>].<field>``, or ``form``) to its reason. The message names the
    fields, never a value."""

    code = "sequence_refused"

    def __init__(self, problems: Mapping[str, str]) -> None:
        self.problems: dict[str, str] = dict(problems)
        super().__init__(f"sequence refused: {', '.join(self.problems) or 'no field'}")


_EDITED_PATH: Final = re.compile(
    r"sequences\[[^\]]*\]\."
    r"(?P<rest>name|steps(?:\[(?P<step>[1-9][0-9]?)\](?:\.(?P<field>[a-z_]+))?)?)"
)


def edited_field(path: str) -> str:
    """The editor's form field for the validator ``path`` of the edited Sequence: ``name``,
    ``steps`` or ``steps[<n>]`` (a Step as a whole), or ``steps[<n>].<field>`` cut at any list
    index or sub-field (``steps[2].allowed_content``, ``steps[1].subject_rule``); ``form`` for a
    path it can't place."""
    found = _EDITED_PATH.match(path)
    if found is None:
        return FORM_FIELD
    if found.group("field") is not None:
        return f"steps[{found.group('step')}].{found.group('field')}"
    return found.group("rest")


def _shown(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def sequence_form_diff(old: SequenceDef, new: SequenceDef) -> tuple[FieldChange, ...]:
    """The fields that differ between ``old`` and ``new``: ``key`` and ``name`` first, then each
    Step by its number, each of its fields in the new form's order then the ones only ``old`` has
    (``steps[2].ask``); a Step only one version has is a single change of the whole Step
    (``steps[4]``). Values are compact JSON, ``None`` where absent."""
    before, after = sequence_form(old), sequence_form(new)
    out: list[FieldChange] = []
    for name in ("key", "name"):
        if _shown(before.get(name)) != _shown(after.get(name)):
            out.append(FieldChange(name, _shown(before.get(name)), _shown(after.get(name))))
    old_steps = before["steps"] if isinstance(before["steps"], list) else []
    new_steps = after["steps"] if isinstance(after["steps"], list) else []
    for index in range(max(len(old_steps), len(new_steps))):
        path = f"steps[{index + 1}]"
        was = old_steps[index] if index < len(old_steps) else None
        now = new_steps[index] if index < len(new_steps) else None
        if isinstance(was, dict) and isinstance(now, dict):
            for name in [*now, *(name for name in was if name not in now)]:
                shown_old, shown_new = _shown(was.get(name)), _shown(now.get(name))
                if shown_old != shown_new:
                    out.append(FieldChange(f"{path}.{name}", shown_old, shown_new))
        elif _shown(was) != _shown(now):
            out.append(FieldChange(path, _shown(was), _shown(now)))
    return tuple(out)
