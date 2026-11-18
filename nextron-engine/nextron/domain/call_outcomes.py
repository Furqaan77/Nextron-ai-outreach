"""A discovery call's Call Outcome (Story 9.4, FR-69, FR-45). Standard library only, plus
``nextron.domain``.

**Vocabulary.** ``CallResult``: ``held`` or ``no_show``. ``PriceReaction``: ``accepted``,
``pushed_back`` or ``not_discussed``. Objections are ``reply_classes.ObjectionType`` codes
(Sales Playbook §5).

**The form.** ``OutcomeForm`` is what the Operator typed, each field a ``str`` (``objections`` a
tuple of codes; ``qualified`` and ``audit_proposed`` ``yes``/``no``). ``check_outcome(form)``
reads it, field by field in ``CORRECTABLE_FIELDS`` order, into an ``Outcome``; the first
problem raises ``OutcomeRefused(field, reason)``, ``reason`` an ``OutcomeRefusal``:

==========  ==========================================================================
Reason      When
==========  ==========================================================================
missing     ``outcome`` blank; for a held call, ``qualified``, ``audit_proposed`` or
            ``price_reaction`` blank, ``reason`` blank when not qualified, or
            ``audit_price`` blank when the audit was proposed
bad_price   an ``audit_price`` that isn't whole US dollars from 0 to 1,000,000,000
            (``manual_stages.parse_deal_value``'s rules)
bad_date    a ``next_step_on`` that isn't a real ``YYYY-MM-DD`` date
too_long    a ``reason`` or ``next_step`` longer than ``MAX_TEXT_LENGTH`` (500) characters
==========  ==========================================================================

A code that isn't one (an unknown ``outcome``, ``yes``/``no`` value, price reaction or
objection) raises ``ValueError``: the dashboard refuses such a form as malformed before it gets
here. Fields that don't apply are ignored and become ``None``: every held field (``qualified``,
``reason``, ``audit_proposed``, ``audit_price``, ``price_reaction``, ``objections``) for a
no-show, ``reason`` when qualified and ``audit_price`` when the audit wasn't proposed. The next
step (``next_step``, ``next_step_on``) is optional for either result. Text is plain: CRLF (and a
lone CR) becomes LF, and a blank text is ``None``. Objections are kept sorted and once each, an
empty tuple when a held call raised none.

**The value.** ``Outcome`` checks its own invariants (``TypeError``/``ValueError``), so
``Pipeline`` stores only consistent outcomes. ``columns()`` is its ``call_outcomes`` columns
(objections comma-joined, sorted: ``""`` for none, ``NULL`` for a no-show) and
``Outcome.from_columns(row)`` reads them back.

**Corrections.** ``changed_fields(before, after)`` is the fields of ``CORRECTABLE_FIELDS`` whose
values differ; ``correction_cause(field)`` is the ``events`` cause of correcting one,
``corrected_<field>``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from nextron.domain.manual_stages import (
    DATE_PATTERN,
    MAX_DEAL_VALUE,
    MICROS_PER_DOLLAR,
    StageRefused,
    parse_deal_value,
)
from nextron.domain.reply_classes import ObjectionType

MAX_TEXT_LENGTH: Final = 500
YES: Final = "yes"
NO: Final = "no"
ANSWERS: Final[Mapping[str, bool]] = {YES: True, NO: False}
OBJECTIONS_SEPARATOR: Final = ","

OUTCOME: Final = "outcome"
QUALIFIED: Final = "qualified"
REASON: Final = "reason"
AUDIT_PROPOSED: Final = "audit_proposed"
AUDIT_PRICE: Final = "audit_price"
PRICE_REACTION: Final = "price_reaction"
OBJECTIONS: Final = "objections"
NEXT_STEP: Final = "next_step"
NEXT_STEP_ON: Final = "next_step_on"
CORRECTABLE_FIELDS: Final = (
    OUTCOME,
    QUALIFIED,
    REASON,
    AUDIT_PROPOSED,
    AUDIT_PRICE,
    PRICE_REACTION,
    OBJECTIONS,
    NEXT_STEP,
    NEXT_STEP_ON,
)
CORRECTION_PREFIX: Final = "corrected_"


class CallResult(StrEnum):
    """Whether the call took place."""

    HELD = "held"
    NO_SHOW = "no_show"


class PriceReaction(StrEnum):
    """How the prospect took the audit price."""

    ACCEPTED = "accepted"
    PUSHED_BACK = "pushed_back"
    NOT_DISCUSSED = "not_discussed"


class OutcomeRefusal(StrEnum):
    MISSING = "missing"
    BAD_PRICE = "bad_price"
    BAD_DATE = "bad_date"
    TOO_LONG = "too_long"


class OutcomeRefused(Exception):
    """A Call Outcome form refused, nothing written: ``field`` is the form field, ``reason``
    the ``OutcomeRefusal``."""

    def __init__(self, field: str, reason: OutcomeRefusal) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OutcomeForm:
    """The Call Outcome form as typed."""

    outcome: str = ""
    qualified: str = ""
    reason: str = ""
    audit_proposed: str = ""
    audit_price: str = ""
    price_reaction: str = ""
    objections: tuple[str, ...] = ()
    next_step: str = ""
    next_step_on: str = ""


def _text_problem(name: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return f"{name} must be a str or None"
    if "\r" in value or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        return f"{name} must be normalized plain text of at most {MAX_TEXT_LENGTH} characters"
    return None


@dataclass(frozen=True, slots=True)
class Outcome:
    """A checked Call Outcome; see the module docstring."""

    outcome: CallResult
    qualified: bool | None = None
    reason: str | None = None
    audit_proposed: bool | None = None
    audit_price: int | None = None
    price_reaction: PriceReaction | None = None
    objections: tuple[ObjectionType, ...] | None = None
    next_step: str | None = None
    next_step_on: date | None = None

    def __post_init__(self) -> None:
        problem = self._problem()
        if problem is not None:
            raise ValueError(f"Outcome: {problem}")

    def _problem(self) -> str | None:
        if not isinstance(self.outcome, CallResult):
            return "outcome must be a CallResult"
        for name in (REASON, NEXT_STEP):
            problem = _text_problem(name, getattr(self, name))
            if problem is not None:
                return problem
        if self.next_step_on is not None and (
            not isinstance(self.next_step_on, date) or isinstance(self.next_step_on, datetime)
        ):
            return "next_step_on must be a date or None"
        held_fields = (
            self.qualified,
            self.reason,
            self.audit_proposed,
            self.audit_price,
            self.price_reaction,
            self.objections,
        )
        if self.outcome is CallResult.NO_SHOW:
            if any(value is not None for value in held_fields):
                return "a no-show holds no held-call field"
            return None
        if not isinstance(self.qualified, bool) or not isinstance(self.audit_proposed, bool):
            return "a held call needs qualified and audit_proposed as bool"
        if (self.reason is None) != self.qualified:
            return "reason is required exactly when not qualified"
        if self.audit_proposed:
            price = self.audit_price
            if (
                not isinstance(price, int)
                or isinstance(price, bool)
                or not 0 <= price <= MAX_DEAL_VALUE
                or price % MICROS_PER_DOLLAR
            ):
                return "audit_price must be whole dollars in micro-dollars"
        elif self.audit_price is not None:
            return "audit_price must be None when the audit wasn't proposed"
        if not isinstance(self.price_reaction, PriceReaction):
            return "a held call needs a PriceReaction"
        objections = self.objections
        if (
            not isinstance(objections, tuple)
            or not all(isinstance(each, ObjectionType) for each in objections)
            or objections != tuple(sorted(set(objections)))
        ):
            return "objections must be a sorted tuple of distinct ObjectionType"
        return None

    def columns(self) -> dict[str, object]:
        """The ``call_outcomes`` columns of this outcome."""
        return {
            OUTCOME: str(self.outcome),
            QUALIFIED: self.qualified,
            REASON: self.reason,
            AUDIT_PROPOSED: self.audit_proposed,
            AUDIT_PRICE: self.audit_price,
            PRICE_REACTION: None if self.price_reaction is None else str(self.price_reaction),
            OBJECTIONS: (
                None
                if self.objections is None
                else OBJECTIONS_SEPARATOR.join(str(each) for each in self.objections)
            ),
            NEXT_STEP: self.next_step,
            NEXT_STEP_ON: self.next_step_on,
        }

    @classmethod
    def from_columns(cls, row: Mapping[str, object]) -> "Outcome":
        """The outcome stored in ``row`` (``columns()``'s keys)."""
        stored = row[OBJECTIONS]
        codes = () if stored is None else str(stored).split(OBJECTIONS_SEPARATOR)
        objections = (
            None if stored is None else tuple(ObjectionType(each) for each in codes if each)
        )
        reaction = row[PRICE_REACTION]
        return cls(
            outcome=CallResult(str(row[OUTCOME])),
            qualified=row[QUALIFIED],
            reason=row[REASON],
            audit_proposed=row[AUDIT_PROPOSED],
            audit_price=row[AUDIT_PRICE],
            price_reaction=None if reaction is None else PriceReaction(str(reaction)),
            objections=objections,
            next_step=row[NEXT_STEP],
            next_step_on=row[NEXT_STEP_ON],
        )


def clean_text(field: str, text: str) -> str | None:
    """``text`` with line breaks normalized, ``None`` when blank; ``too_long`` past 500."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return None
    if len(normalized) > MAX_TEXT_LENGTH:
        raise OutcomeRefused(field, OutcomeRefusal.TOO_LONG)
    return normalized


def _answer(field: str, text: str) -> bool:
    if not text:
        raise OutcomeRefused(field, OutcomeRefusal.MISSING)
    if text not in ANSWERS:
        raise ValueError(f"{field} must be yes or no")
    return ANSWERS[text]


def _next_step_on(text: str) -> date | None:
    if not text.strip():
        return None
    stripped = text.strip()
    if DATE_PATTERN.fullmatch(stripped) is None:
        raise OutcomeRefused(NEXT_STEP_ON, OutcomeRefusal.BAD_DATE)
    try:
        return date.fromisoformat(stripped)
    except ValueError:
        raise OutcomeRefused(NEXT_STEP_ON, OutcomeRefusal.BAD_DATE) from None


def _audit_price(text: str) -> int:
    try:
        price = parse_deal_value(text)
    except StageRefused:
        raise OutcomeRefused(AUDIT_PRICE, OutcomeRefusal.BAD_PRICE) from None
    if price is None:
        raise OutcomeRefused(AUDIT_PRICE, OutcomeRefusal.MISSING)
    return price


def check_outcome(form: OutcomeForm) -> Outcome:
    """The ``Outcome`` the form describes; see the module docstring."""
    if not isinstance(form, OutcomeForm):
        raise TypeError(f"check_outcome takes an OutcomeForm, not {type(form).__name__}")
    if not form.outcome:
        raise OutcomeRefused(OUTCOME, OutcomeRefusal.MISSING)
    result = CallResult(form.outcome)  # ValueError for an unknown code
    if result is CallResult.NO_SHOW:
        return Outcome(
            outcome=result,
            next_step=clean_text(NEXT_STEP, form.next_step),
            next_step_on=_next_step_on(form.next_step_on),
        )
    qualified = _answer(QUALIFIED, form.qualified)
    reason = None
    if not qualified:
        reason = clean_text(REASON, form.reason)
        if reason is None:
            raise OutcomeRefused(REASON, OutcomeRefusal.MISSING)
    proposed = _answer(AUDIT_PROPOSED, form.audit_proposed)
    price = _audit_price(form.audit_price) if proposed else None
    if not form.price_reaction:
        raise OutcomeRefused(PRICE_REACTION, OutcomeRefusal.MISSING)
    reaction = PriceReaction(form.price_reaction)
    objections = tuple(sorted({ObjectionType(each) for each in form.objections}))
    return Outcome(
        outcome=result,
        qualified=qualified,
        reason=reason,
        audit_proposed=proposed,
        audit_price=price,
        price_reaction=reaction,
        objections=objections,
        next_step=clean_text(NEXT_STEP, form.next_step),
        next_step_on=_next_step_on(form.next_step_on),
    )


def changed_fields(before: Outcome, after: Outcome) -> tuple[str, ...]:
    """The fields of ``CORRECTABLE_FIELDS`` whose values differ, in that order."""
    return tuple(
        field for field in CORRECTABLE_FIELDS if getattr(before, field) != getattr(after, field)
    )


def correction_cause(field: str) -> str:
    """The ``events`` cause of correcting ``field``: ``corrected_<field>``."""
    if field not in CORRECTABLE_FIELDS:
        raise ValueError("not a correctable Call Outcome field")
    return f"{CORRECTION_PREFIX}{field}"
