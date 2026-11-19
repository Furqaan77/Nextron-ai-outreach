"""Campaigns and their Variants (FR-3, AD-4, AD-5, NFR-3, NFR-7, Story 4.2).

A Campaign sends one Sequence to one Segment in one Market, leading to one Offer, through 1 or
2 Variants. ``seed/campaigns.yaml`` holds them (read by ``nextron.domain.seed_yaml``) and
``nextron seed sync`` loads them into the ``campaigns`` and ``variants`` tables (owner
``setup``), always at ``draft``: only the Operator activates, pauses and resumes a Campaign, and
only the Operator records a UK Campaign's legitimate-interests assessment (LIA).

**The machine (AD-4).** ``CampaignStatus`` (``draft``, ``active``, ``paused``),
``CampaignEvent`` (``activate``, ``pause``, ``resume``) and ``CampaignCause``
(``operator_activate``, ``operator_pause``, ``operator_resume``). ``TRANSITIONS`` (read-only)
holds exactly ``draft --activate--> active``, ``active --pause--> paused`` and ``paused
--resume--> active``; ``transition(current, event)`` returns the ``Transition`` or raises
``IllegalTransition`` (machine ``MACHINE``, ``campaign``). An LIA save is recorded as an event
from a status to itself with cause ``LIA_RECORDED_CAUSE`` (``operator_record_lia``), and a
Variant the Operator adds as a version event with cause ``VARIANT_ADDED_CAUSE``
(``operator_add_variant``).

**Variants.** ``VariantStatus`` (``active``, ``inactive``); ``StepAngleNote(step, note)`` (the
angle a Variant takes on one Step of the Campaign's Sequence) and ``VariantDef(name, status,
step_angles)``. A Campaign has at most ``MAX_VARIANTS`` (2) **active** Variants:
``TOO_MANY_VARIANTS``. Since Story 11.8 a Variant is retired by setting it ``inactive``, which is
final; retired Variants stay as history and don't count towards the cap.

**The LIA (NFR-3).** ``Lia(ref, date)``. ``needs_lia(country_code)`` is true exactly for a GB
Market (``nextron.domain.markets.GB``). ``may_run(status, country_code, lia)`` is the one
answer to "may this Campaign run?": true only for ``active``, with a non-``None`` ``lia`` when
``needs_lia``; anything else is false (fail closed).
``lia_from_fields(ref_text, date_text, today)`` strips both texts, collects every problem and
raises ``CampaignRefused`` (keys ``lia_ref``, ``lia_date``, ``form``): a blank field reads
"Enter a value", with ``form`` ``LIA_FIRST``; the reference follows Story 2.1's text rules, at
most 200 characters; the date is a real ``YYYY-MM-DD`` date ("Use a date written YYYY-MM-DD")
no later than ``today`` ("can't be in the future"). ``parse_date(text)`` is that date check
(``None`` for anything else). ``stored_lia(ref, date_text, path)`` decodes a stored LIA: ``None``
when both are ``None``, else ``InvalidCampaigns`` (on ``<path>.lia_ref`` or ``.lia_date``)
unless both are set, the reference passes the text rules and the date is real; it never
compares the date with today.

**``CampaignRefused(problems)``** (code ``campaign_refused``) is an Operator command refused:
``problems`` maps a field name, or ``form``, to its reason; the message names the keys only.

**The file form.** The top level is exactly ``campaigns: [<campaign>, ...]``, with 1 to 50
items. A campaign is exactly ``key``, ``name``, ``segment``, ``market``, ``offer``,
``sequence`` and ``variants``; a Variant is ``name`` and ``status`` plus optional
``step_angles`` (0 to 6 items, each exactly ``step`` and ``note``). Missing and unknown keys are
refused. ``CampaignDef(key, name, segment, market, offer, sequence, variants)`` holds keys and
codes, never ids.

**Validation.** ``validate_campaigns(data)`` returns the ``CampaignDef``s in file order, or
raises ``InvalidCampaigns(field, reason)`` (code ``invalid_campaigns``) for the first problem
in file order (each mapping's fields in the file's order, then its missing fields, then its
rules). The message is ``"<field>: <reason>"``; ``field`` is a path such as
``campaigns[law_private_ai_uk].variants[A].step_angles[0].note``. A campaign's brackets hold its
``key`` when valid (and not a repeat), else its 0-based index; a Variant's hold its name when
valid (and not a repeat, case-insensitively), else its index; a note's its 0-based index.

- ``key``, ``segment``, ``offer`` and ``sequence`` fullmatch ``[a-z][a-z0-9_]{0,30}``;
  ``market`` fullmatches ``nextron.domain.markets.CODE_PATTERN``. Keys are unique.
- Texts follow Story 2.1's rules (a non-blank ``str`` with no leading or trailing whitespace and
  no ``Cc``, ``Cf``, ``Zl`` or ``Zp`` character): a Campaign's ``name`` at most 100 characters,
  a Variant's ``name`` at most 40, a ``note`` at most 200.
- ``step`` is an ``int`` (never a ``bool``) from 1 to 6, unique within a Variant
  ("repeats an earlier note's step").
- ``variants`` holds 1 or 2 items: a third gives ``TOO_MANY_VARIANTS`` on ``.variants``.
  Variant names are unique per Campaign, case-insensitively, and at least one Variant is
  ``active`` ("A Campaign needs an active Variant", on ``.variants``).
- Each (``segment``, ``market``) pair is unique: "A Campaign already exists for this Segment and
  Market", on the Campaign (``campaigns[<key>]``).

**References.** ``check_campaign_references(campaigns, segment_keys, market_countries,
offer_keys, sequence_steps)`` (``market_countries`` maps a Market code to its country code,
``sequence_steps`` a Sequence key to its number of Steps) raises ``InvalidCampaigns`` for the
first problem in file order: an unknown ``segment``, ``market``, ``offer`` or ``sequence`` (in
that order), then, per Variant and note, a ``step`` above the Sequence's Step count, then the
angle-note floor (decision 5): a note that names ABA Formal Opinion 512
(``nextron.domain.segments.mentions_aba_512``) needs a US Market, one that names the SRA notice
(``mentions_sra_notice``) a GB Market, and a note holding a currency sign (``$``, ``£``, ``€``)
or the word ``usd``, ``gbp`` or ``eur`` is refused: ``PRICE_NOTE``. ``names_price(note)`` (the
single definition in ``nextron.domain.prices``, imported here under the same name with
``CURRENCY_SIGNS`` and ``CURRENCY_WORDS``) and ``note_floor_problem(note, country_code)`` (the
floor's reason, or ``None``) are those checks, shared with the command path.

**The file form back.** ``campaign_form(campaign)`` is the campaign's file mapping (statuses as
their values, lists as lists, ``step_angles`` only when non-empty; a value of the wrong type is
left as it is, so validation refuses it); ``campaign_from_form(form)`` validates one such
mapping, so ``campaign_from_form(campaign_form(c)) == c`` for a valid ``c``.

**The stored form** (Story 11.8). ``stored_campaign_from_form(form)`` validates a stored
Campaign's mapping with the same rules but one: its ``variants`` may hold any number of Variants,
none active included, with at most ``MAX_VARIANTS`` ``active`` ones (``TOO_MANY_VARIANTS``) and
names unique case-insensitively. The seed **file** keeps the 1-to-2 rule.
``campaign_form_diff(old, new)`` gives the changed field paths between two ``CampaignDef``s, for
the seed sync's managed-mode differences: ``key``, ``name``, ``segment``, ``market``, ``offer``
and ``sequence`` first, then per Variant (by name, case-insensitively, in ``old``'s order then
``new``'s) ``variants[<name>]`` for one only one side has, else ``variants[<name>].name``,
``.status`` and ``.step_angles`` where they differ.

**Create and edit fields** (Story 11.8). ``campaign_fields_problems(name, key=None)`` checks a
stripped Campaign ``name`` (``ENTER_A_VALUE`` when blank, else the text rules, at most 100
characters) and, when given, a stripped ``key`` (``ENTER_A_VALUE`` or the key pattern's reason),
returning ``{field: reason}`` (``{}`` when both pass).

**The command path.** ``variant_from_fields(name, step_angles, *, existing_names, steps,
country_code)`` is a new Variant from the Operator (Story 4.2's ``add_variant``): the name and
each note stripped, then the same rules, every problem collected into one ``CampaignRefused``:
``form`` ``TOO_MANY_VARIANTS`` when ``existing_names`` already holds 2; ``name`` blank ("Enter
a value"), against the text rules, or already used case-insensitively ("A Variant with this
name already exists"); ``step_angles`` for the first broken note (at most 6, a step from 1 to
``steps`` and unique, the note's text rules and the floor). It returns the Variant ``active``.
``existing_names`` holds every other Variant's name, retired ones included (names stay unique);
``active_count`` (default: ``len(existing_names)``) is how many of them are active, and the cap
refuses when it is already 2. An edit of an active Variant passes 0.

**Story 11.8 causes and messages.** The Operator's Campaign version events have causes
``CAMPAIGN_CREATED_CAUSE`` (``operator_create``, ``None`` to ``v1``), ``CAMPAIGN_EDITED_CAUSE``
(``operator_edit``), ``VARIANT_EDITED_CAUSE`` (``operator_edit_variant``) and
``VARIANT_RETIRED_CAUSE`` (``operator_retire_variant``). The refusals are ``NEEDS_VARIANT_FIRST``,
``PAIR_FROZEN``, ``VARIANT_ENROLLED``, ``VARIANT_RETIRED``, ``LAST_ACTIVE_VARIANT``,
``STALE_CAMPAIGN``, ``KEY_TAKEN``, ``CHOOSE_ONE`` and ``NO_SUCH_VARIANT``.
"""

import datetime as dt
import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from nextron.domain.errors import DomainError
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.markets import CODE_PATTERN, GB, US
# the single price check (Story 4.11 moved it to the leaf ``prices``); kept under these names
from nextron.domain.prices import CURRENCY_SIGNS, CURRENCY_WORDS, names_price  # noqa: F401
from nextron.domain.segments import mentions_aba_512, mentions_sra_notice

MACHINE: Final = "campaign"
LIA_RECORDED_CAUSE: Final = "operator_record_lia"
VARIANT_ADDED_CAUSE: Final = "operator_add_variant"
CAMPAIGN_CREATED_CAUSE: Final = "operator_create"
CAMPAIGN_EDITED_CAUSE: Final = "operator_edit"
VARIANT_EDITED_CAUSE: Final = "operator_edit_variant"
VARIANT_RETIRED_CAUSE: Final = "operator_retire_variant"

KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched with fullmatch
DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # matched with fullmatch
_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # an unknown field shown by name

TOP_LEVEL: Final = "(top level)"  # the field path of a problem with the whole file
FORM_FIELD: Final = "form"  # an Operator problem that belongs to no one field
LIA_REF_FIELD: Final = "lia_ref"
LIA_DATE_FIELD: Final = "lia_date"
NAME_FIELD: Final = "name"
KEY_FIELD: Final = "key"
SEGMENT_FIELD: Final = "segment"
MARKET_FIELD: Final = "market"
OFFER_FIELD: Final = "offer"
SEQUENCE_FIELD: Final = "sequence"
STEP_ANGLES_FIELD: Final = "step_angles"
MAX_CAMPAIGNS: Final = 50
MAX_NAME: Final = 100  # a Campaign's name
MAX_VARIANT_NAME: Final = 40
MAX_NOTE: Final = 200
MAX_LIA_REF: Final = 200
MAX_VARIANTS: Final = 2
MAX_STEP_ANGLES: Final = 6
MIN_STEP: Final = 1
MAX_STEP: Final = 6  # nextron.domain.sequences.MAX_STEPS
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})

TOO_MANY_VARIANTS: Final = "A Campaign can have at most 2 Variants"
NEEDS_ACTIVE_VARIANT: Final = "A Campaign needs an active Variant"
PAIR_TAKEN: Final = "A Campaign already exists for this Segment and Market"
PRICE_NOTE: Final = "An angle note can't name a price (FR-4)"
LIA_FIRST: Final = "Record the legitimate-interests assessment reference and date first"
ENTER_A_VALUE: Final = "Enter a value"
DATE_REASON: Final = "Use a date written YYYY-MM-DD"
FUTURE_REASON: Final = "can't be in the future"
VARIANT_NAME_TAKEN: Final = "A Variant with this name already exists"
LIA_PAIR_REASON: Final = "the LIA reference and date must both be set or both be empty"
NEEDS_VARIANT_FIRST: Final = "Add an active Variant first"
PAIR_FROZEN: Final = "Segment and Market can't change once Contacts are enrolled"
VARIANT_ENROLLED: Final = "Retire this Variant and add a new one, so its results stay comparable"
VARIANT_RETIRED: Final = "This Variant is retired, and a retired Variant never changes"
LAST_ACTIVE_VARIANT: Final = (
    "A running Campaign needs an active Variant: add another before retiring this one"
)
STALE_CAMPAIGN: Final = "This Campaign changed since you opened it"
KEY_TAKEN: Final = "A Campaign with this key already exists"
CHOOSE_ONE: Final = "Choose one"
NO_SUCH_VARIANT: Final = "This Campaign has no Variant with this id"
_KEY_REASON: Final = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 31 "
    "characters"
)
_MARKET_REASON: Final = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 16 "
    "characters"
)


# The machine -------------------------------------------------------------------------------------


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


class CampaignEvent(StrEnum):
    ACTIVATE = "activate"
    PAUSE = "pause"
    RESUME = "resume"


class CampaignCause(StrEnum):
    OPERATOR_ACTIVATE = "operator_activate"
    OPERATOR_PAUSE = "operator_pause"
    OPERATOR_RESUME = "operator_resume"


TRANSITIONS: Final[
    Mapping[tuple[CampaignStatus, CampaignEvent], Transition[CampaignStatus, CampaignCause]]
] = MappingProxyType(
    {
        (CampaignStatus.DRAFT, CampaignEvent.ACTIVATE): Transition(
            CampaignStatus.ACTIVE, CampaignCause.OPERATOR_ACTIVATE
        ),
        (CampaignStatus.ACTIVE, CampaignEvent.PAUSE): Transition(
            CampaignStatus.PAUSED, CampaignCause.OPERATOR_PAUSE
        ),
        (CampaignStatus.PAUSED, CampaignEvent.RESUME): Transition(
            CampaignStatus.ACTIVE, CampaignCause.OPERATOR_RESUME
        ),
    }
)


def transition(
    current: CampaignStatus, event: CampaignEvent
) -> Transition[CampaignStatus, CampaignCause]:
    """Where ``event`` leads from ``current``; raises ``IllegalTransition`` when the table has
    no such row."""
    try:
        found = TRANSITIONS.get((current, event))
    except TypeError:  # an unhashable argument is no status or event
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, event, current)
    return found


# Types -------------------------------------------------------------------------------------------


class VariantStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class StepAngleNote:
    """The angle a Variant takes on Step ``step`` of its Campaign's Sequence."""

    step: int
    note: str


@dataclass(frozen=True, slots=True)
class VariantDef:
    name: str
    status: VariantStatus
    step_angles: tuple[StepAngleNote, ...]


@dataclass(frozen=True, slots=True)
class CampaignDef:
    """One item of ``seed/campaigns.yaml``: references are keys and codes, never ids."""

    key: str
    name: str
    segment: str  # a Segment key
    market: str  # a Market code
    offer: str  # an Offer key
    sequence: str  # a Sequence key
    variants: tuple[VariantDef, ...]


@dataclass(frozen=True, slots=True)
class Lia:
    """A UK Campaign's legitimate-interests assessment: its reference and its date."""

    ref: str
    date: dt.date


class InvalidCampaigns(DomainError):
    """Campaign definitions that break a rule: ``field`` is the path of the first problem and
    ``reason`` says what a valid value is."""

    code = "invalid_campaigns"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class CampaignRefused(DomainError):
    """An Operator's Campaign command refused: ``problems`` maps each refused field (or
    ``form``) to its reason. The message names the keys, never a value."""

    code = "campaign_refused"

    def __init__(self, problems: Mapping[str, str]) -> None:
        self.problems: dict[str, str] = dict(problems)
        super().__init__(f"campaign refused: {', '.join(self.problems) or 'no field'}")


# The LIA -----------------------------------------------------------------------------------------


def needs_lia(country_code: object) -> bool:
    """Whether a Campaign in a Market of ``country_code`` needs a recorded LIA: exactly GB."""
    return country_code == GB


def may_run(status: object, country_code: object, lia: object) -> bool:
    """Whether the Campaign may run: ``active``, and with an LIA when its Market needs one."""
    if status != CampaignStatus.ACTIVE:
        return False
    return not (needs_lia(country_code) and not isinstance(lia, Lia))


def _text_problem(value: object, limit: int) -> str | None:
    """Story 2.1's text rules: the reason ``value`` breaks them, or ``None``."""
    if not isinstance(value, str):
        return "must be text"
    if not value.strip():
        return "must not be blank"
    if value != value.strip():
        return "must not start or end with whitespace"
    if any(unicodedata.category(char) in REFUSED_TEXT_CATEGORIES for char in value):
        return "must not hold control, format or line-separator characters"
    if len(value) > limit:
        return f"must be at most {limit} characters"
    return None


def parse_date(text: str) -> dt.date | None:
    """The real calendar date ``text`` writes as ``YYYY-MM-DD``, or ``None``."""
    if DATE_PATTERN.fullmatch(text) is None:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def stored_lia(ref: object, date_text: object, path: str) -> Lia | None:
    """A stored LIA (``lia_ref`` and the ``lia_date`` text), decoded: ``None`` when both are
    ``None``; else ``InvalidCampaigns`` on ``<path>.lia_ref`` or ``<path>.lia_date`` unless both
    are set, the reference follows the text rules (at most 200 characters) and the date is a
    real ``YYYY-MM-DD`` date. The date is never compared with today."""
    if ref is None and date_text is None:
        return None
    if ref is None or date_text is None:
        raise InvalidCampaigns(f"{path}.lia_ref", LIA_PAIR_REASON)
    reason = _text_problem(ref, MAX_LIA_REF)
    if reason is not None:
        raise InvalidCampaigns(f"{path}.lia_ref", reason)
    day = parse_date(date_text) if isinstance(date_text, str) else None
    if day is None:
        raise InvalidCampaigns(f"{path}.lia_date", "must be a real date written YYYY-MM-DD")
    assert isinstance(ref, str)
    return Lia(ref, day)


def lia_from_fields(ref_text: str, date_text: str, today: dt.date) -> Lia:
    """The ``Lia`` the Operator entered; ``CampaignRefused`` with every problem. See the module
    docstring."""
    if not isinstance(ref_text, str) or not isinstance(date_text, str):
        raise TypeError("lia_from_fields takes the reference and the date as texts")
    if not isinstance(today, dt.date) or isinstance(today, dt.datetime):
        raise TypeError("lia_from_fields takes today as a date")
    ref = ref_text.strip()
    day_text = date_text.strip()
    problems: dict[str, str] = {}
    if not ref:
        problems[LIA_REF_FIELD] = ENTER_A_VALUE
    else:
        reason = _text_problem(ref, MAX_LIA_REF)
        if reason is not None:
            problems[LIA_REF_FIELD] = reason
    day = None
    if not day_text:
        problems[LIA_DATE_FIELD] = ENTER_A_VALUE
    else:
        day = parse_date(day_text)
        if day is None:
            problems[LIA_DATE_FIELD] = DATE_REASON
        elif day > today:
            problems[LIA_DATE_FIELD] = FUTURE_REASON
    if not ref or not day_text:
        problems[FORM_FIELD] = LIA_FIRST
    if problems:
        raise CampaignRefused(problems)
    assert day is not None
    return Lia(ref, day)


# The angle-note floor ---------------------------------------------------------------------------


def note_floor_problem(note: str, country_code: str) -> str | None:
    """The angle-note floor (decision 5) for a note in a Market of ``country_code``: ABA Formal
    Opinion 512 only in a US Market, the SRA notice only in a GB one, and never a price."""
    if mentions_aba_512(note) and country_code != US:
        return (
            "names ABA Formal Opinion 512, which only a US Campaign's note may (this Market is "
            f"{country_code})"
        )
    if mentions_sra_notice(note) and country_code != GB:
        return (
            "names the SRA notice, which only a GB Campaign's note may (this Market is "
            f"{country_code})"
        )
    if names_price(note):
        return PRICE_NOTE
    return None


def _steps_text(steps: int) -> str:
    return f"{steps} Step" if steps == 1 else f"{steps} Steps"


# Validation ------------------------------------------------------------------------------------

_CAMPAIGN_REQUIRED: Final = ("key", "name", "segment", "market", "offer", "sequence", "variants")
_VARIANT_REQUIRED: Final = ("name", "status")
_NOTE_REQUIRED: Final = ("step", "note")

type _Check = Callable[[object, str], Any]


def _unknown_field(name: object, path: str) -> InvalidCampaigns:
    if isinstance(name, str) and _FIELD_NAME.fullmatch(name):  # shown only when short and plain
        return InvalidCampaigns(f"{path}.{name}" if path else name, "unknown field")
    return InvalidCampaigns(path or TOP_LEVEL, "has an unknown field")


def _fields(
    value: object, path: str, checks: dict[str, _Check], required: tuple[str, ...]
) -> dict[str, Any]:
    """``value`` as a mapping whose fields are ``checks``: each present field checked in the
    file's order (an unknown one refused where it appears), then the missing ones of
    ``required`` refused."""
    if not isinstance(value, dict):
        raise InvalidCampaigns(path, "must be a mapping")
    checked: dict[str, Any] = {}
    for name, item in value.items():
        check = checks.get(name) if isinstance(name, str) else None
        if check is None:
            raise _unknown_field(name, path)
        checked[name] = check(item, f"{path}.{name}")
    for name in required:
        if name not in value:
            raise InvalidCampaigns(f"{path}.{name}", "missing")
    return checked


def _list(value: object, path: str, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidCampaigns(path, "must be a list")
    if len(value) < minimum:
        raise InvalidCampaigns(path, f"must hold at least {minimum} item")
    if len(value) > maximum:
        raise InvalidCampaigns(path, f"must hold at most {maximum} items")
    return value


def _text(value: object, path: str, limit: int) -> str:
    reason = _text_problem(value, limit)
    if reason is not None:
        raise InvalidCampaigns(path, reason)
    assert isinstance(value, str)
    return value


def _is_key(value: object) -> bool:
    return isinstance(value, str) and KEY_PATTERN.fullmatch(value) is not None


def _reference_key(value: object, path: str) -> str:
    """A Segment, Offer or Sequence key."""
    if not _is_key(value):
        raise InvalidCampaigns(path, _KEY_REASON)
    assert isinstance(value, str)
    return value


def _market_code(value: object, path: str) -> str:
    if not isinstance(value, str) or CODE_PATTERN.fullmatch(value) is None:
        raise InvalidCampaigns(path, _MARKET_REASON)
    return value


def _step(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCampaigns(path, "must be a whole number")
    if not MIN_STEP <= value <= MAX_STEP:
        raise InvalidCampaigns(path, f"must be from {MIN_STEP} to {MAX_STEP}")
    return value


def _note(value: object, path: str) -> str:
    return _text(value, path, MAX_NOTE)


def _variant_status(value: object, path: str) -> VariantStatus:
    allowed = [member.value for member in VariantStatus]
    if not isinstance(value, str) or value not in allowed:
        raise InvalidCampaigns(path, f"must be one of {', '.join(allowed)}")
    return VariantStatus(value)


def _step_angles(value: object, path: str) -> tuple[StepAngleNote, ...]:
    """0 to 6 notes, no two on the same Step."""
    items = _list(value, path, 0, MAX_STEP_ANGLES)
    notes: list[StepAngleNote] = []
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        checked = _fields(item, item_path, {"step": _step, "note": _note}, _NOTE_REQUIRED)
        step = checked["step"]
        if any(note.step == step for note in notes):
            raise InvalidCampaigns(f"{item_path}.step", f"repeats an earlier note's step ({step})")
        notes.append(StepAngleNote(step, checked["note"]))
    return tuple(notes)


def _variant_name(value: object, path: str) -> str:
    return _text(value, path, MAX_VARIANT_NAME)


def _variants(value: object, path: str) -> tuple[VariantDef, ...]:
    """1 or 2 Variants, names unique case-insensitively, at least one ``active``."""
    if not isinstance(value, list):
        raise InvalidCampaigns(path, "must be a list")
    if not value:
        raise InvalidCampaigns(path, "must hold at least 1 item")
    if len(value) > MAX_VARIANTS:
        raise InvalidCampaigns(path, TOO_MANY_VARIANTS)
    variants: list[VariantDef] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        name = item.get("name") if isinstance(item, dict) else None
        shown = str(index)
        if (
            isinstance(name, str)
            and _text_problem(name, MAX_VARIANT_NAME) is None
            and name.casefold() not in seen
        ):
            shown = name
        item_path = f"{path}[{shown}]"
        checked = _fields(
            item,
            item_path,
            {"name": _variant_name, "status": _variant_status, "step_angles": _step_angles},
            _VARIANT_REQUIRED,
        )
        chosen = checked["name"]
        if chosen.casefold() in seen:
            raise InvalidCampaigns(
                f"{item_path}.name", f"repeats an earlier Variant's name ({chosen})"
            )
        seen.add(chosen.casefold())
        variants.append(VariantDef(chosen, checked["status"], checked.get("step_angles", ())))
    if not any(variant.status == VariantStatus.ACTIVE for variant in variants):
        raise InvalidCampaigns(path, NEEDS_ACTIVE_VARIANT)
    return tuple(variants)


def _stored_variants(value: object, path: str) -> tuple[VariantDef, ...]:
    """A stored Campaign's Variants (Story 11.8): any number, names unique case-insensitively,
    at most 2 ``active``."""
    if not isinstance(value, list):
        raise InvalidCampaigns(path, "must be a list")
    variants: list[VariantDef] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        name = item.get("name") if isinstance(item, dict) else None
        shown = str(index)
        if (
            isinstance(name, str)
            and _text_problem(name, MAX_VARIANT_NAME) is None
            and name.casefold() not in seen
        ):
            shown = name
        item_path = f"{path}[{shown}]"
        checked = _fields(
            item,
            item_path,
            {"name": _variant_name, "status": _variant_status, "step_angles": _step_angles},
            _VARIANT_REQUIRED,
        )
        chosen = checked["name"]
        if chosen.casefold() in seen:
            raise InvalidCampaigns(
                f"{item_path}.name", f"repeats an earlier Variant's name ({chosen})"
            )
        seen.add(chosen.casefold())
        variants.append(VariantDef(chosen, checked["status"], checked.get("step_angles", ())))
    if sum(1 for variant in variants if variant.status == VariantStatus.ACTIVE) > MAX_VARIANTS:
        raise InvalidCampaigns(path, TOO_MANY_VARIANTS)
    return tuple(variants)


class _Validator:
    """One validation run: the Campaign keys and (Segment, Market) pairs seen so far, and
    whether it validates a stored Campaign (any number of Variants, at most 2 active)."""

    def __init__(self, *, stored: bool = False) -> None:
        self._keys: set[str] = set()
        self._pairs: set[tuple[str, str]] = set()
        self._variants = _stored_variants if stored else _variants

    def campaigns(self, data: object) -> tuple[CampaignDef, ...]:
        if not isinstance(data, dict):
            raise InvalidCampaigns(
                TOP_LEVEL, "must be a mapping with exactly one field, campaigns"
            )
        result: tuple[CampaignDef, ...] = ()
        for name, value in data.items():
            if name != "campaigns":
                raise _unknown_field(name, "")
            items = _list(value, "campaigns", 1, MAX_CAMPAIGNS)
            result = tuple(self.campaign(item, index) for index, item in enumerate(items))
        if "campaigns" not in data:
            raise InvalidCampaigns("campaigns", "missing")
        return result

    def campaign(self, value: object, index: int) -> CampaignDef:
        key = value.get("key") if isinstance(value, dict) else None
        shown = key if _is_key(key) and key not in self._keys else str(index)
        path = f"campaigns[{shown}]"
        checked = _fields(
            value,
            path,
            {
                "key": self._key,
                "name": lambda v, p: _text(v, p, MAX_NAME),
                "segment": _reference_key,
                "market": _market_code,
                "offer": _reference_key,
                "sequence": _reference_key,
                "variants": self._variants,
            },
            _CAMPAIGN_REQUIRED,
        )
        pair = (checked["segment"], checked["market"])
        if pair in self._pairs:
            raise InvalidCampaigns(path, PAIR_TAKEN)
        self._pairs.add(pair)
        return CampaignDef(
            key=checked["key"],
            name=checked["name"],
            segment=checked["segment"],
            market=checked["market"],
            offer=checked["offer"],
            sequence=checked["sequence"],
            variants=checked["variants"],
        )

    def _key(self, value: object, path: str) -> str:
        if not _is_key(value):
            raise InvalidCampaigns(path, _KEY_REASON)
        assert isinstance(value, str)
        if value in self._keys:
            raise InvalidCampaigns(path, f"repeats an earlier Campaign key ({value})")
        self._keys.add(value)
        return value


def validate_campaigns(data: object) -> tuple[CampaignDef, ...]:
    """The ``CampaignDef``s of a parsed seed file, in file order; ``InvalidCampaigns`` for the
    first problem. See the module docstring."""
    return _Validator().campaigns(data)


# References ------------------------------------------------------------------------------------


def check_campaign_references(
    campaigns: Iterable[CampaignDef],
    segment_keys: Collection[str],
    market_countries: Mapping[str, str],
    offer_keys: Collection[str],
    sequence_steps: Mapping[str, int],
) -> None:
    """``InvalidCampaigns`` for the first unknown reference, Step beyond the Sequence or note
    below the floor, in file order; see the module docstring."""
    for campaign in campaigns:
        path = f"campaigns[{campaign.key}]"
        if campaign.segment not in segment_keys:
            raise InvalidCampaigns(
                f"{path}.segment", "isn't the key of a Segment in this seed sync"
            )
        country = market_countries.get(campaign.market)
        if country is None:
            raise InvalidCampaigns(
                f"{path}.market", "isn't the code of a Market in this seed sync"
            )
        if campaign.offer not in offer_keys:
            raise InvalidCampaigns(
                f"{path}.offer", "isn't the key of an Offer in this seed sync"
            )
        steps = sequence_steps.get(campaign.sequence)
        if steps is None:
            raise InvalidCampaigns(
                f"{path}.sequence", "isn't the key of a Sequence in this seed sync"
            )
        for variant in campaign.variants:
            for index, angle in enumerate(variant.step_angles):
                note_path = f"{path}.variants[{variant.name}].step_angles[{index}]"
                if angle.step > steps:
                    raise InvalidCampaigns(
                        f"{note_path}.step",
                        f"must be at most {steps}: the Sequence {campaign.sequence} has "
                        f"{_steps_text(steps)}",
                    )
                problem = note_floor_problem(angle.note, country)
                if problem is not None:
                    raise InvalidCampaigns(f"{note_path}.note", problem)


# The file form ----------------------------------------------------------------------------------


def _enum_value(value: object) -> object:
    """A ``StrEnum`` member as its plain value; anything else as it is."""
    return str(value) if isinstance(value, StrEnum) else value


def _note_form(angle: object) -> object:
    if not isinstance(angle, StepAngleNote):
        return angle
    return {"step": angle.step, "note": angle.note}


def _variant_form(variant: object) -> object:
    if not isinstance(variant, VariantDef):
        return variant
    form: dict[str, object] = {"name": variant.name, "status": _enum_value(variant.status)}
    angles = variant.step_angles
    if not (isinstance(angles, (tuple, list)) and not angles):  # an empty list is left out
        form["step_angles"] = (
            [_note_form(angle) for angle in angles]
            if isinstance(angles, (tuple, list))
            else angles
        )
    return form


def campaign_form(campaign: CampaignDef) -> dict[str, object]:
    """``campaign`` as the file's mapping: statuses as their values, lists as lists,
    ``step_angles`` only when non-empty."""
    variants = campaign.variants
    return {
        "key": campaign.key,
        "name": campaign.name,
        "segment": campaign.segment,
        "market": campaign.market,
        "offer": campaign.offer,
        "sequence": campaign.sequence,
        "variants": (
            [_variant_form(variant) for variant in variants]
            if isinstance(variants, (tuple, list))
            else variants
        ),
    }


def campaign_from_form(form: object) -> CampaignDef:
    """The ``CampaignDef`` of one file-form mapping, validated as one item of a file (path
    ``campaigns[<key>]``); ``InvalidCampaigns`` for the first problem."""
    return _Validator().campaign(form, 0)


def stored_campaign_from_form(form: object) -> CampaignDef:
    """The ``CampaignDef`` of one stored Campaign's mapping (Story 11.8): ``campaign_from_form``
    with any number of Variants, at most 2 of them active; ``InvalidCampaigns`` otherwise."""
    return _Validator(stored=True).campaign(form, 0)


def _angles_text(angles: Iterable[StepAngleNote]) -> tuple[tuple[int, str], ...]:
    return tuple((angle.step, angle.note) for angle in angles)


def campaign_form_diff(old: CampaignDef, new: CampaignDef) -> tuple[str, ...]:
    """The changed field paths from ``old`` to ``new``; see the module docstring."""
    paths = [
        name
        for name in ("key", "name", "segment", "market", "offer", "sequence")
        if getattr(old, name) != getattr(new, name)
    ]
    before = {variant.name.casefold(): variant for variant in old.variants}
    after = {variant.name.casefold(): variant for variant in new.variants}
    for folded in [*before, *(name for name in after if name not in before)]:
        was, now = before.get(folded), after.get(folded)
        if was is None or now is None:
            only = was if was is not None else now
            assert only is not None
            paths.append(f"variants[{only.name}]")
            continue
        path = f"variants[{was.name}]"
        if was.name != now.name:
            paths.append(f"{path}.name")
        if was.status != now.status:
            paths.append(f"{path}.status")
        if _angles_text(was.step_angles) != _angles_text(now.step_angles):
            paths.append(f"{path}.step_angles")
    return tuple(paths)


def campaign_fields_problems(name: str, key: str | None = None) -> dict[str, str]:
    """The problems of a stripped Campaign ``name`` and, when given, ``key``; see the module
    docstring."""
    problems: dict[str, str] = {}
    if key is not None:
        if not key:
            problems[KEY_FIELD] = ENTER_A_VALUE
        elif not _is_key(key):
            problems[KEY_FIELD] = _KEY_REASON
    if not name:
        problems[NAME_FIELD] = ENTER_A_VALUE
    else:
        reason = _text_problem(name, MAX_NAME)
        if reason is not None:
            problems[NAME_FIELD] = reason
    return problems


# The command path --------------------------------------------------------------------------------


def _note_problem(
    index: int, angle: StepAngleNote, earlier: list[StepAngleNote], steps: int, country: str
) -> str | None:
    step = angle.step
    if isinstance(step, bool) or not isinstance(step, int):
        return "A step must be a whole number"
    if not MIN_STEP <= step <= MAX_STEP:
        return f"A step must be from {MIN_STEP} to {MAX_STEP}"
    if step > steps:
        return f"Step {step} is beyond the Sequence's {_steps_text(steps)}"
    if any(note.step == step for note in earlier):
        return f"repeats an earlier note's step ({step})"
    reason = _text_problem(angle.note, MAX_NOTE)
    if reason is not None:
        return f"note {index + 1} {reason}"
    return note_floor_problem(angle.note, country)


def variant_from_fields(
    name: str,
    step_angles: Iterable[StepAngleNote],
    *,
    existing_names: Collection[str],
    steps: int,
    country_code: str,
    active_count: int | None = None,
) -> VariantDef:
    """A new ``active`` Variant from the Operator; ``CampaignRefused`` with every problem. See
    the module docstring."""
    if not isinstance(name, str):
        raise TypeError("variant_from_fields takes the name as text")
    given = tuple(step_angles)
    for angle in given:
        if not isinstance(angle, StepAngleNote):
            raise TypeError(f"step_angles holds StepAngleNotes, not {type(angle).__name__}")
    problems: dict[str, str] = {}
    chosen = name.strip()
    if not chosen:
        problems[NAME_FIELD] = ENTER_A_VALUE
    else:
        reason = _text_problem(chosen, MAX_VARIANT_NAME)
        if reason is not None:
            problems[NAME_FIELD] = reason
        elif chosen.casefold() in {existing.casefold() for existing in existing_names}:
            problems[NAME_FIELD] = VARIANT_NAME_TAKEN
    notes = [
        StepAngleNote(a.step, a.note.strip() if isinstance(a.note, str) else a.note)
        for a in given
    ]
    if len(notes) > MAX_STEP_ANGLES:
        problems[STEP_ANGLES_FIELD] = f"must hold at most {MAX_STEP_ANGLES} items"
    else:
        for index, angle in enumerate(notes):
            problem = _note_problem(index, angle, notes[:index], steps, country_code)
            if problem is not None:
                problems[STEP_ANGLES_FIELD] = problem
                break
    active = len(existing_names) if active_count is None else active_count
    if active >= MAX_VARIANTS:
        problems[FORM_FIELD] = TOO_MANY_VARIANTS
    if problems:
        raise CampaignRefused(problems)
    return VariantDef(chosen, VariantStatus.ACTIVE, tuple(notes))
