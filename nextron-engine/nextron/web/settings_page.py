"""The Settings page (AD-12, UX-DR23): one form generated from the settings schema.

``create_app`` serves it:

- ``GET /settings`` (``settings_page``, ``@personal_data(True)``) renders ``settings.html``,
  which extends ``base.html`` and includes ``settings_form.html``.
- ``POST /settings`` (``settings_save``, ``@personal_data(True)``) takes the urlencoded form
  through ``nextron.web.forms.form_fields``, checks its fields with ``submitted_texts``, then
  runs ``Setup.save`` in its own unit of work, all or nothing, and answers with
  ``settings_form.html`` alone, which HTMX swaps in for the form (``hx-target="this"
  hx-swap="outerHTML"``). Saved: the form shows the stored values and ``Settings saved.``
  (``role="status"``). Refused (``InvalidSettings``): 200 with the form re-rendered from the
  submitted texts, each refused field marked ``aria-invalid="true"`` with its rule's reason
  beside it, and an ``role="alert"`` notice that nothing was saved. (HTMX swaps only 2xx
  responses, so a refusal answers 200.)

The form has one ``<fieldset>`` with a ``<legend>`` per ``Section`` that has settings, in
``Section`` order, and per setting, in declaration order: a ``<label>``; a ``<textarea>`` for a
``list`` or ``availability``, a ``true``/``false`` ``<select>`` for a ``boolean`` (with a blank
choice), otherwise an ``<input type="text">``; a hint, the unit plus ``required`` or
``default: <text>; leave blank to use it``; and its value: the stored text, else the default
text, else blank. On ``GET``, a stored text that no longer parses or passes its rules (a rule
tightened since it was saved) is flagged like a refused field, without the "nothing was saved"
notice (``stored_problems``).

The page requires every declared field: a form with a field missing, an undeclared field or a
repeated field (``csrf_token`` aside) raises ``MalformedForm`` (400), so a stale or crafted form
never saves part of the page. ``Setup.save`` itself accepts any subset.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain.settings_schema import (
    InvalidSettingValue,
    Section,
    Setting,
    SettingType,
    declared,
    default_text,
    parse,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "settings.html"
FORM_TEMPLATE: Final = "settings_form.html"


class Control(StrEnum):
    """The form control a setting's type gets."""

    TEXT = "text"
    TEXTAREA = "textarea"
    BOOLEAN = "boolean"


CONTROLS: Final = {
    SettingType.LIST: Control.TEXTAREA,
    SettingType.AVAILABILITY: Control.TEXTAREA,
    SettingType.BOOLEAN: Control.BOOLEAN,
}


@dataclass(frozen=True, slots=True)
class FormField:
    """One setting's field, as the template renders it."""

    key: str
    label: str
    control: Control
    value: str
    hint: str
    required: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FormSection:
    """One ``<fieldset>``: a section's legend and its fields."""

    legend: str
    fields: tuple[FormField, ...]


def hint(found: Setting) -> str:
    """The unit, plus ``required`` or ``default: <text>; leave blank to use it``. A default of
    several lines shows them comma-separated; a blank default shows as ``none``."""
    text = default_text(found)
    if text is None:
        return f"{found.unit}; required"
    shown = ", ".join(text.splitlines()) or "none"
    return f"{found.unit}; default: {shown}; leave blank to use it"


def shown_values(stored: Mapping[str, str]) -> dict[str, str]:
    """Each declared setting's value as the form shows it: the stored text, else the default
    text, else blank."""
    values = {}
    for found in declared():
        key = str(found.key)
        text = stored.get(key)
        values[key] = text if text is not None else default_text(found) or ""
    return values


def stored_problems(stored: Mapping[str, str]) -> dict[str, InvalidSettingValue]:
    """Each declared setting whose stored text no longer parses or passes its rules, with the
    refusal."""
    problems = {}
    for found in declared():
        key = str(found.key)
        text = stored.get(key)
        if text is None:
            continue
        try:
            parse(found, text)
        except InvalidSettingValue as refused:
            problems[key] = refused
    return problems


def form_context(
    values: Mapping[str, str],
    *,
    problems: Mapping[str, InvalidSettingValue] | None = None,
    saved: bool = False,
    refused: bool = False,
) -> dict[str, object]:
    """What ``settings_form.html`` renders: the sections with ``values`` (and each problem's
    reason beside its field), and whether to say the settings were saved, or refused with
    nothing saved."""
    problems = problems or {}
    grouped: dict[Section, list[FormField]] = {section: [] for section in Section}
    for found in declared():
        key = str(found.key)
        problem = problems.get(key)
        grouped[found.section].append(
            FormField(
                key=key,
                label=found.label,
                control=CONTROLS.get(found.type, Control.TEXT),
                value=values.get(key, ""),
                hint=hint(found),
                required=found.required,
                error=problem.reason if problem is not None else None,
            )
        )
    sections = [
        FormSection(section.value, tuple(fields)) for section, fields in grouped.items() if fields
    ]
    return {"sections": sections, "saved": saved, "refused": refused}


def submitted_texts(fields: FormFields) -> dict[str, str]:
    """The submitted text of every declared setting, in declaration order. Raises
    ``MalformedForm`` unless, ``csrf_token`` aside, the fields are exactly the declared keys,
    each with exactly one value."""
    keys = [str(found.key) for found in declared()]
    if set(fields) - {CSRF_FIELD} != set(keys):
        raise MalformedForm(WRONG_FIELDS)
    texts = {}
    for key in keys:
        values = fields[key]
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        texts[key] = values[0]
    return texts
