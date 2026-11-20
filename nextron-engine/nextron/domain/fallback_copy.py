"""Segment fallback copy (FR-22, AD-7, AD-12; Story 4.19): the templates a hand-picked Tier C
Lead's Step Drafts are filled from, with no AI call. Standard library only, plus
``nextron.domain``.

``seed/fallback_templates.yaml`` holds them (read by ``nextron.domain.seed_yaml``) and ``nextron
seed sync`` loads them into the ``fallback_templates`` table (owner ``setup``), one row per
Segment key, Step and English variant.

**Types** (frozen, slotted): ``FallbackTemplate(segment, step, english_variant, subject,
body)``: the Segment's key, the 1-based Step, the ``nextron.domain.markets.EnglishVariant``,
the subject (Step 1 only; ``None`` on Steps 2 and later, whose Drafts take the filled subject
of the Step 1 template of the same Segment and variant: the thread's subject) and the body (the
file's paragraphs joined with one blank line). ``FillValues(first_name, company, role,
facts)``: what ``fill`` puts in, ``None`` for a value the Contact or Lead lacks, ``facts``
mapping a fact id to its wording.

**Placeholders.** Exactly ``{first_name}``, ``{company}``, ``{role}`` and ``{fact:<fact_id>}``
(``fact_id`` matching ``nextron.domain.company_facts.FACT_ID_PATTERN``). Any other ``{...}``,
and a stray ``{`` or ``}``, is refused (``placeholders(text)`` raises ``ValueError`` with the
reason). ``fact_ids(*texts)`` lists the fact ids in first-appearance order, each once.

**The file form.** The top level is exactly ``fallback_templates: [<template>, ...]`` (1 to
``MAX_TEMPLATES`` items). A template is ``segment``, ``step``, ``english_variant`` and ``body``,
plus ``subject`` on Step 1. Missing and unknown fields are refused.

**Validation.** ``validate_fallback_templates(data)`` returns the ``FallbackTemplate``s in file
order, or raises ``InvalidFallbackTemplates(field, reason)`` (code
``invalid_fallback_templates``) for the first problem in file order. The message is ``"<field>:
<reason>"``; ``field`` is a path such as ``fallback_templates[law_private_ai/1/en_us].body[0]``,
whose brackets hold the template's natural key (Segment, Step, variant) once those three are
valid, else its 0-based index. A reason never repeats a value longer than 40 characters.

- ``segment`` fullmatches ``nextron.domain.segments.KEY_PATTERN`` (whether it names a Segment
  is checked by ``setup`` against the stored or synced keys);
- ``step`` an ``int`` (never a ``bool``) from 1 to ``MAX_STEP`` (6);
- ``english_variant`` an ``EnglishVariant``;
- ``subject``: required on Step 1 and refused on any other Step; text (non-blank, no leading or
  trailing whitespace, no ``Cc``, ``Cf``, ``Zl`` or ``Zp`` character), at most ``MAX_SUBJECT``
  (150) characters, with valid placeholders;
- ``body``: a list of 1 to ``MAX_PARAGRAPHS`` (10) paragraphs, each such text of at most
  ``MAX_PARAGRAPH`` (1,000) characters with valid placeholders;
- the natural key is unique in the file (on ``step``), and a template of Step 2 or later needs
  the Step 1 template of its Segment and variant in the file (on ``step``).

``fill(text, values)`` replaces every placeholder; ``FallbackFillFailed`` (code
``fallback_fill_failed``, ``detail`` ``missing_value`` or ``fact_not_allowed``) when a value is
``None`` or blank or a fact id has no wording in ``values.facts``. ``first_name_of(name)`` is a
Contact name's first whitespace-separated token, or ``None``.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from nextron.domain.company_facts import FACT_ID_PATTERN
from nextron.domain.errors import DomainError
from nextron.domain.markets import EnglishVariant
from nextron.domain.segments import KEY_PATTERN as SEGMENT_KEY_PATTERN

TOP_KEY: Final = "fallback_templates"
TOP_LEVEL: Final = "(top level)"
MAX_TEMPLATES: Final = 200
MAX_STEP: Final = 6
FIRST_STEP: Final = 1
MAX_SUBJECT: Final = 150
MAX_PARAGRAPHS: Final = 10
MAX_PARAGRAPH: Final = 1_000
PARAGRAPH_JOIN: Final = "\n\n"
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})

FIRST_NAME: Final = "first_name"
COMPANY: Final = "company"
ROLE: Final = "role"
VALUE_NAMES: Final = (FIRST_NAME, COMPANY, ROLE)
FACT_PREFIX: Final = "fact:"

MISSING_VALUE: Final = "missing_value"
FACT_NOT_ALLOWED: Final = "fact_not_allowed"

_BRACES: Final = re.compile(r"[{}]")
_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
PLACEHOLDER_REASON: Final = (
    "holds an unknown placeholder; use {first_name}, {company}, {role} or {fact:<fact_id>}"
)
STRAY_BRACE_REASON: Final = "holds a { or } that isn't part of a placeholder"


class InvalidFallbackTemplates(DomainError):
    """Fallback templates that break a rule: ``field`` is the path of the first problem and
    ``reason`` says what a valid value is."""

    code = "invalid_fallback_templates"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class FallbackFillFailed(DomainError):
    """A template can't be filled: ``detail`` is ``missing_value`` or ``fact_not_allowed``."""

    code = "fallback_fill_failed"

    def __init__(self, detail: str) -> None:
        super().__init__(f"the template can't be filled ({detail})")
        self.detail = detail


@dataclass(frozen=True, slots=True)
class FallbackTemplate:
    """One Segment fallback template; see the module docstring."""

    segment: str
    step: int
    english_variant: EnglishVariant
    subject: str | None
    body: str

    @property
    def natural_key(self) -> tuple[str, int, str]:
        return (self.segment, self.step, str(self.english_variant))


@dataclass(frozen=True, slots=True)
class FillValues:
    """What ``fill`` puts in: ``None`` for a value the Contact or Lead lacks."""

    first_name: str | None
    company: str | None
    role: str | None
    facts: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


# Placeholders -------------------------------------------------------------------------------------


def placeholders(text: str) -> tuple[str, ...]:
    """The placeholders of ``text`` (their inner names, such as ``fact:legal_ai``), in order;
    ``ValueError`` with the reason for an unknown one or a stray brace."""
    found: list[str] = []
    for match in _PLACEHOLDER.finditer(text):
        name = match.group(1)
        if name in VALUE_NAMES:
            found.append(name)
            continue
        if name.startswith(FACT_PREFIX) and FACT_ID_PATTERN.fullmatch(name[len(FACT_PREFIX) :]):
            found.append(name)
            continue
        raise ValueError(PLACEHOLDER_REASON)
    if _BRACES.search(_PLACEHOLDER.sub("", text)):
        raise ValueError(STRAY_BRACE_REASON)
    return tuple(found)


def fact_ids(*texts: str | None) -> tuple[str, ...]:
    """The fact ids the ``{fact:<id>}`` placeholders of ``texts`` name, in first-appearance
    order, each once."""
    seen: dict[str, None] = {}
    for text in texts:
        if text is None:
            continue
        for name in placeholders(text):
            if name.startswith(FACT_PREFIX):
                seen.setdefault(name[len(FACT_PREFIX) :], None)
    return tuple(seen)


def first_name_of(name: str | None) -> str | None:
    """The first whitespace-separated token of a Contact's name, or ``None``."""
    if name is None:
        return None
    parts = name.split()
    return parts[0] if parts else None


def fill(text: str, values: FillValues) -> str:
    """``text`` with every placeholder replaced; ``FallbackFillFailed`` when a value is missing
    or a fact has no wording."""
    placeholders(text)  # ValueError for a text that never validated

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith(FACT_PREFIX):
            wording = values.facts.get(name[len(FACT_PREFIX) :])
            if wording is None:
                raise FallbackFillFailed(FACT_NOT_ALLOWED)
            return wording
        value = {FIRST_NAME: values.first_name, COMPANY: values.company, ROLE: values.role}[name]
        if value is None or not value.strip():
            raise FallbackFillFailed(MISSING_VALUE)
        return value.strip()

    return _PLACEHOLDER.sub(replace, text)


# Validation ---------------------------------------------------------------------------------------

_REQUIRED: Final = ("segment", "step", "english_variant", "body")
_FIELDS: Final = frozenset({*_REQUIRED, "subject"})


def _text(value: object, path: str, limit: int) -> str:
    if not isinstance(value, str):
        raise InvalidFallbackTemplates(path, "must be text")
    if not value.strip():
        raise InvalidFallbackTemplates(path, "must not be blank")
    if value != value.strip():
        raise InvalidFallbackTemplates(path, "must not start or end with whitespace")
    if any(unicodedata.category(char) in REFUSED_TEXT_CATEGORIES for char in value):
        raise InvalidFallbackTemplates(
            path, "must not hold control, format or line-separator characters"
        )
    if len(value) > limit:
        raise InvalidFallbackTemplates(path, f"must be at most {limit:,} characters")
    try:
        placeholders(value)
    except ValueError as exc:
        raise InvalidFallbackTemplates(path, str(exc)) from None
    return value


def _segment(value: object, path: str) -> str:
    if not isinstance(value, str) or SEGMENT_KEY_PATTERN.fullmatch(value) is None:
        raise InvalidFallbackTemplates(
            path,
            "must be a Segment key: lowercase letters, digits and underscores, starting with a "
            "letter, at most 31 characters",
        )
    return value


def _step(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_STEP:
        raise InvalidFallbackTemplates(path, f"must be a whole number from 1 to {MAX_STEP}")
    return value


def _variant(value: object, path: str) -> EnglishVariant:
    allowed = [member.value for member in EnglishVariant]
    if not isinstance(value, str) or value not in allowed:
        raise InvalidFallbackTemplates(path, f"must be one of {', '.join(allowed)}")
    return EnglishVariant(value)


def _body(value: object, path: str) -> str:
    if not isinstance(value, list):
        raise InvalidFallbackTemplates(path, "must be a list of paragraphs")
    if not 1 <= len(value) <= MAX_PARAGRAPHS:
        raise InvalidFallbackTemplates(path, f"must hold 1 to {MAX_PARAGRAPHS} paragraphs")
    return PARAGRAPH_JOIN.join(
        _text(item, f"{path}[{index}]", MAX_PARAGRAPH) for index, item in enumerate(value)
    )


def _template(item: object, index: int) -> FallbackTemplate:
    path = f"{TOP_KEY}[{index}]"
    if not isinstance(item, dict):
        raise InvalidFallbackTemplates(path, "must be a mapping")
    for name in item:
        if name not in _FIELDS:
            if isinstance(name, str) and _FIELD_NAME.fullmatch(name):
                raise InvalidFallbackTemplates(f"{path}.{name}", "unknown field")
            raise InvalidFallbackTemplates(path, "has an unknown field")
    for name in _REQUIRED:
        if name not in item:
            raise InvalidFallbackTemplates(f"{path}.{name}", "missing")
    segment = _segment(item["segment"], f"{path}.segment")
    step = _step(item["step"], f"{path}.step")
    variant = _variant(item["english_variant"], f"{path}.english_variant")
    path = f"{TOP_KEY}[{segment}/{step}/{variant}]"
    subject: str | None = None
    if step == FIRST_STEP:
        if "subject" not in item:
            raise InvalidFallbackTemplates(f"{path}.subject", "missing: Step 1 needs a subject")
        subject = _text(item["subject"], f"{path}.subject", MAX_SUBJECT)
    elif "subject" in item:
        raise InvalidFallbackTemplates(
            f"{path}.subject",
            "only Step 1 has a subject; a follow-up takes the Step 1 subject (the thread's)",
        )
    body = _body(item["body"], f"{path}.body")
    return FallbackTemplate(segment, step, variant, subject, body)


def validate_fallback_templates(data: object) -> tuple[FallbackTemplate, ...]:
    """The file's templates in file order; ``InvalidFallbackTemplates`` for the first problem.
    See the module docstring."""
    if not isinstance(data, dict):
        raise InvalidFallbackTemplates(TOP_LEVEL, f"must be a mapping with {TOP_KEY}")
    for name in data:
        if name != TOP_KEY:
            shown = name if isinstance(name, str) and _FIELD_NAME.fullmatch(name) else None
            raise InvalidFallbackTemplates(shown or TOP_LEVEL, "unknown field")
    if TOP_KEY not in data:
        raise InvalidFallbackTemplates(TOP_KEY, "missing")
    items: Any = data[TOP_KEY]
    if not isinstance(items, list):
        raise InvalidFallbackTemplates(TOP_KEY, "must be a list")
    if not 1 <= len(items) <= MAX_TEMPLATES:
        raise InvalidFallbackTemplates(TOP_KEY, f"must hold 1 to {MAX_TEMPLATES} templates")
    templates: list[FallbackTemplate] = []
    seen: set[tuple[str, int, str]] = set()
    for index, item in enumerate(items):
        template = _template(item, index)
        if template.natural_key in seen:
            raise InvalidFallbackTemplates(
                template_path(template, "step"),
                "repeats an earlier template's Segment, Step and English variant",
            )
        seen.add(template.natural_key)
        templates.append(template)
    for template in templates:
        first = (template.segment, FIRST_STEP, str(template.english_variant))
        if template.step != FIRST_STEP and first not in seen:
            raise InvalidFallbackTemplates(
                template_path(template, "step"),
                "needs the Step 1 template of its Segment and English variant (its subject)",
            )
    return tuple(templates)


def template_path(template: FallbackTemplate, name: str) -> str:
    """The field path ``name`` of ``template`` (its natural key in the brackets); ``setup``
    names the checks that need the database or the other seed files with it."""
    return f"{TOP_KEY}[{template.segment}/{template.step}/{template.english_variant}].{name}"
