"""The Sequences section of Settings (FR-4, AD-12, AD-13, UX-DR3, UX-DR23; Story 11.7).

``create_app`` serves it at ``/sequences``, linked from the Settings page. Every route is
``@personal_data(False)``: a Sequence holds Step briefs and rules, never a person.

- ``GET /sequences`` (``sequences_page``): ``sequences.html`` with ``sequences_panel.html``:
  each stored Sequence by key with its name, version, number of Steps, how many Campaigns use
  it, and links to its edit and history pages. There is no create form (Story 11.7 edits only).
- ``GET /sequences/{sequence_id}`` (``sequence_page``): ``sequence.html`` with
  ``sequence_panel.html``: a line per version with live Enrollments ("2 active Enrollments keep
  version 2", "1 active Enrollment keeps version 3"; running Enrollments keep the version they
  started with), the fixed FR-4 rules shown read-only ("Set in code (FR-4)"), and the edit form,
  prefilled with the current version and carrying ``based_on`` (that version).
- ``GET /sequences/{sequence_id}/versions`` (``sequence_versions_page``): every stored version,
  oldest first, with who saved it and when, and the fields it changed from the version before
  (``sequence_form_diff``); when the earliest stored version is above 1 the page notes
  "Versions before v<n> were not kept.".
- ``POST /sequences/{sequence_id}/edit`` (``sequence_edit``) runs ``SequenceEditor.edit`` in its
  own unit of work, then renders the panel again in a read-only one. A refused save
  (``SequenceRefused``) answers 200 with the form re-rendered as submitted, each problem beside
  its field (``aria-invalid="true"``) and a ``role="alert"`` notice that nothing was saved. A
  stale save (the Sequence changed since the form was opened) instead re-renders the current
  stored version's form, with an alert that it now shows the current version and the change must
  be made again. A stored Sequence that no longer decodes shows the Sequences list's unavailable
  notice on these pages instead of an error.

**The form** (``submitted_sequence``): ``based_on``, ``name`` and, per Step row ``i`` (1 to 99;
the stored Steps are rows 1 to n, followed by blank rows up to 6), ``steps[i].stored_step`` (a
hidden field: the stored Step the row keeps, blank for an appended row),
``steps[i].delay_days``, ``purpose``, ``word_limit``, ``word_comparison`` (a select of the code
enum), ``allowed_content`` (a textarea, one item per line, blank lines ignored), ``ask`` and
``remove`` (a checkbox; Step 1's row has none and no delay field: both are fixed). Every field
is optional but ``based_on`` and ``name``; a row with ``remove`` ticked, or an appended row whose
fields are all blank, is left out. Whole numbers are sent on as numbers; anything else stays
text, so the validator names the field. The fixed FR-4 fields are never read from the form: the
editor sets them. A field outside the form, a repeated field, a row index above 99, a
``stored_step`` that isn't blank or a whole number, a ``word_comparison`` outside its enum or a
``based_on`` that isn't a positive whole number raises ``MalformedForm`` (400). Each text is
stripped. The editor names a problem by the Step's number in the proposed Sequence; the page
puts it back beside the row it came from (``form_problems``).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nextron.app.queries.setup import SeededCampaign, SeededSequence, SequenceVersion
from nextron.domain.segments import FORM_FIELD
from nextron.domain.sequences import (
    MAX_STEPS,
    SequenceDef,
    WordComparison,
    sequence_form_diff,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "sequences.html"
PANEL_TEMPLATE: Final = "sequences_panel.html"
SEQUENCE_TEMPLATE: Final = "sequence.html"
SEQUENCE_PANEL_TEMPLATE: Final = "sequence_panel.html"
VERSIONS_TEMPLATE: Final = "sequence_versions.html"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
EDIT_PREFIX: Final = "sequence-edit"
BASED_ON: Final = "based_on"
NAME: Final = "name"
STORED_STEP: Final = "stored_step"
REMOVE: Final = "remove"
MAX_ROW: Final = 99
MAX_BASED_ON_DIGITS: Final = 9
ROW_TEXTS: Final = ("delay_days", "purpose", "word_limit", "allowed_content", "ask")
ROW_FIELDS: Final = (*ROW_TEXTS, "word_comparison", STORED_STEP, REMOVE)
ROW_FIELD: Final = re.compile(r"steps\[([1-9][0-9]?)\]\.([a-z_]+)")
NUMBER: Final = re.compile(r"[0-9]{1,9}")
NOTHING_SAVED: Final = "Nothing was saved."
CORRECT_FIELDS: Final = "Correct the fields marked below, then save again."
NOTHING_CHANGED: Final = "Nothing changed, so no new version was saved."
STALE_FORM: Final = "The form now shows the current version. Make your change again."
NOT_FOUND: Final = "That Sequence doesn't exist."
FIRST_STEP: Final = 1


@dataclass(frozen=True, slots=True)
class StepRow:
    """One Step row as submitted (or shown): its form index, the stored Step it keeps
    (``None``: appended), its texts and whether its remove box was ticked."""

    index: int
    stored_step: int | None
    values: Mapping[str, str]
    removed: bool = False


@dataclass(frozen=True, slots=True)
class SubmittedSequence:
    """What the edit form submitted, as texts."""

    name: str
    steps: tuple[StepRow, ...]


@dataclass(frozen=True, slots=True)
class Option:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class FixedStep:
    """A stored Step's fixed fields, shown read-only."""

    subject_rule: str | None
    angles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StepView:
    index: int
    stored_step: int | None
    values: Mapping[str, str]
    comparisons: tuple[Option, ...]
    removed: bool
    first: bool
    fixed: FixedStep | None


@dataclass(frozen=True, slots=True)
class SequenceForm:
    """The edit form: its element-id prefix, values, Step rows and each field's problem."""

    prefix: str
    values: Mapping[str, str]
    steps: tuple[StepView, ...]
    errors: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SequenceRow:
    """One stored Sequence in the list."""

    id: str
    key: str
    name: str
    version: int
    steps: int
    campaigns: int


@dataclass(frozen=True, slots=True)
class ChangeView:
    path: str
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class VersionView:
    version: int
    made_by: str
    made_at: str
    current: bool
    first: bool
    changes: tuple[ChangeView, ...]


# Submitted forms ---------------------------------------------------------------------------------


def _one(fields: FormFields, name: str) -> str:
    values = fields[name]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    return values[0]


def _stored_step(text: str) -> int | None:
    if text == "":
        return None
    if not (text.isascii() and text.isdigit() and not text.startswith("0") and len(text) <= 2):
        raise MalformedForm(WRONG_FIELDS)
    return int(text)


def submitted_sequence(fields: FormFields) -> SubmittedSequence:
    """The edit form's fields; ``MalformedForm`` otherwise."""
    rows: dict[int, dict[str, str]] = {}
    for name, values in fields.items():
        if name in (CSRF_FIELD, BASED_ON, NAME):
            continue
        found = ROW_FIELD.fullmatch(name)
        if found is None:
            raise MalformedForm(WRONG_FIELDS)
        index, sub = int(found.group(1)), found.group(2)
        if sub not in ROW_FIELDS or index > MAX_ROW:
            raise MalformedForm(WRONG_FIELDS)
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        rows.setdefault(index, {})[sub] = values[0]
    if NAME not in fields:
        raise MalformedForm(WRONG_FIELDS)
    steps = []
    for index, subs in sorted(rows.items()):
        removed = subs.pop(REMOVE, None) is not None
        stored_step = _stored_step(subs.pop(STORED_STEP, "").strip())
        texts = {sub: text.strip() for sub, text in subs.items()}
        comparison = texts.get("word_comparison", "")
        if comparison != "" and comparison not in [str(each) for each in WordComparison]:
            raise MalformedForm(WRONG_FIELDS)
        steps.append(StepRow(index, stored_step, texts, removed))
    return SubmittedSequence(_one(fields, NAME).strip(), tuple(steps))


def submitted_based_on(fields: FormFields) -> int:
    """The edit form's ``based_on``; ``MalformedForm`` unless a positive whole number."""
    if BASED_ON not in fields:
        raise MalformedForm(WRONG_FIELDS)
    text = _one(fields, BASED_ON)
    if not (
        text.isascii()
        and text.isdigit()
        and not text.startswith("0")
        and len(text) <= MAX_BASED_ON_DIGITS
    ):
        raise MalformedForm(WRONG_FIELDS)
    return int(text)


# The proposed fields ------------------------------------------------------------------------------


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _number(text: str) -> int | str:
    """A whole number the validator can check, else the text itself (it names the field)."""
    return int(text) if NUMBER.fullmatch(text) else text


def _blank(row: StepRow) -> bool:
    return row.stored_step is None and not any(
        row.values.get(name, "") for name in (*ROW_TEXTS, "word_comparison")
    )


@dataclass(frozen=True, slots=True)
class Proposed:
    """The fields the submitted form proposes to ``SequenceEditor.edit``, and the form index of
    each kept row by its Step number in the proposed Sequence."""

    fields: dict[str, object]
    rows: tuple[int, ...]


def proposed_fields(submitted: SubmittedSequence) -> Proposed:
    """The editor's ``fields`` from ``submitted``: removed rows and blank appended rows left
    out."""
    kept = [row for row in submitted.steps if not row.removed and not _blank(row)]
    steps = [
        {
            STORED_STEP: row.stored_step,
            "delay_days": _number(row.values.get("delay_days", "")),
            "purpose": row.values.get("purpose", ""),
            "word_limit": _number(row.values.get("word_limit", "")),
            "word_comparison": row.values.get("word_comparison", ""),
            "allowed_content": _lines(row.values.get("allowed_content", "")),
            "ask": row.values.get("ask", ""),
        }
        for row in kept
    ]
    return Proposed({NAME: submitted.name, "steps": steps}, tuple(row.index for row in kept))


_POSITION: Final = re.compile(r"steps\[([0-9]+)\](.*)")


def form_problems(problems: Mapping[str, str], proposed: Proposed) -> dict[str, str]:
    """The editor's problems with each Step number turned back into its form row."""
    shown: dict[str, str] = {}
    for name, reason in problems.items():
        found = _POSITION.fullmatch(name)
        if found is not None:
            number = int(found.group(1))
            if 1 <= number <= len(proposed.rows):
                name = f"steps[{proposed.rows[number - 1]}]{found.group(2)}"
            else:
                name = "steps"
        shown[name] = reason
    return shown


# Building the form -------------------------------------------------------------------------------


def _subject_text(definition: SequenceDef) -> str | None:
    rule = definition.steps[0].subject_rule
    if rule is None:
        return None
    guidance = "; ".join(rule.guidance)
    return f"{rule.word_comparison} {rule.word_limit} words: {guidance}"


def _fixed(definition: SequenceDef, stored_step: int | None) -> FixedStep | None:
    if stored_step is None or not 1 <= stored_step <= len(definition.steps):
        return None
    brief = definition.steps[stored_step - 1]
    angles = tuple(f"{a.segment} in {a.market}: {a.angle}" for a in brief.segment_angles)
    subject = _subject_text(definition) if stored_step == FIRST_STEP else None
    return FixedStep(subject, angles)


def _comparisons(selected: str) -> tuple[Option, ...]:
    return tuple(
        Option(str(each), str(each), str(each) == selected) for each in WordComparison
    )


def submitted_from(definition: SequenceDef) -> SubmittedSequence:
    """The stored Sequence as the form shows it, with blank appended rows up to 6 Steps."""
    rows = [
        StepRow(
            brief.step,
            brief.step,
            {
                "delay_days": str(brief.delay_days),
                "purpose": brief.purpose,
                "word_limit": str(brief.word_limit),
                "word_comparison": str(brief.word_comparison),
                "allowed_content": "\n".join(brief.allowed_content),
                "ask": brief.ask,
            },
        )
        for brief in definition.steps
    ]
    return SubmittedSequence(definition.name, tuple(rows))


def sequence_form(
    definition: SequenceDef,
    submitted: SubmittedSequence,
    *,
    errors: Mapping[str, str] | None = None,
) -> SequenceForm:
    """The edit form of ``submitted`` (fixed fields read from ``definition``, the stored
    Sequence), with blank appended rows up to 6 Steps."""
    rows = list(submitted.steps)
    last = max((row.index for row in rows), default=0)
    shown = sum(1 for row in rows if not row.removed)  # kept rows and blank ones already there
    rows += [StepRow(last + offset, None, {}) for offset in range(1, MAX_STEPS - shown + 1)]
    views = tuple(
        StepView(
            index=row.index,
            stored_step=row.stored_step,
            values=row.values,
            comparisons=_comparisons(row.values.get("word_comparison", "")),
            removed=row.removed,
            first=row.stored_step == FIRST_STEP,
            fixed=_fixed(definition, row.stored_step),
        )
        for row in rows
    )
    return SequenceForm(EDIT_PREFIX, {NAME: submitted.name}, views, dict(errors or {}))


# Notices -----------------------------------------------------------------------------------------


def refused_notice(problems: Mapping[str, str]) -> str:
    """The alert of a refused save: nothing was saved, the form-level problem, and a pointer to
    the marked fields when there are any."""
    parts = [NOTHING_SAVED]
    form_problem = problems.get(FORM_FIELD)
    if form_problem:
        parts.append(f"{form_problem}.")
    if any(name != FORM_FIELD for name in problems):
        parts.append(CORRECT_FIELDS)
    return " ".join(parts)


def is_stale(problems: Mapping[str, str], stale: str) -> bool:
    """Whether a refusal was only the editor's stale-form refusal ``stale``."""
    return dict(problems) == {FORM_FIELD: stale}


def stale_notice(problems: Mapping[str, str]) -> str:
    """The alert of a stale save: nothing was saved, the Sequence changed, and the form now
    shows the current version, so the change must be made again."""
    return f"{refused_notice(problems)} {STALE_FORM}"


def saved_notice(key: str, version: int, changed: bool) -> str:
    if not changed:
        return NOTHING_CHANGED
    return (
        f"Saved {key} as version {version}. Running Enrollments keep the version they started "
        "with; new Enrollments use this one."
    )


def enrollment_lines(live: Mapping[int, int]) -> tuple[str, ...]:
    """"n active Enrollments keep version k" for each version with live Enrollments, oldest
    version first."""
    lines = []
    for version, count in sorted(live.items()):
        if count <= 0:
            continue
        if count == 1:
            lines.append(f"1 active Enrollment keeps version {version}")
        else:
            lines.append(f"{count} active Enrollments keep version {version}")
    return tuple(lines)


# Page contexts -----------------------------------------------------------------------------------


def _time(at: datetime) -> str:
    return f"{at:{TIME_FORMAT}}"


def _campaign_counts(campaigns: Iterable[SeededCampaign]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for campaign in campaigns:
        counts[campaign.sequence_id] = counts.get(campaign.sequence_id, 0) + 1
    return counts


def _row(seeded: SeededSequence, campaigns: int) -> SequenceRow:
    return SequenceRow(
        seeded.id,
        seeded.key,
        seeded.definition.name,
        seeded.version,
        len(seeded.definition.steps),
        campaigns,
    )


def sequence_rows(
    sequences: Sequence[SeededSequence], campaigns: Sequence[SeededCampaign]
) -> tuple[SequenceRow, ...]:
    """Each stored Sequence with how many Campaigns use it."""
    counts = _campaign_counts(campaigns)
    return tuple(_row(each, counts.get(each.id, 0)) for each in sequences)


def list_context(
    rows: Sequence[SequenceRow], *, unavailable: str | None = None
) -> dict[str, object]:
    """What ``sequences_panel.html`` renders."""
    return {"sequences": tuple(rows), "unavailable": unavailable}


def edit_context(
    seeded: SeededSequence,
    campaigns: Sequence[SeededCampaign],
    live: Mapping[int, int],
    *,
    notice: str | None = None,
    alert: str | None = None,
    refused: tuple[SubmittedSequence, int, Mapping[str, str]] | None = None,
) -> dict[str, object]:
    """What ``sequence_panel.html`` renders: the Sequence, the live Enrollments per version and
    the edit form (the stored version, or the refused submission with its ``based_on`` and
    problems). ``alert`` is shown with the stored version's form (a stale save)."""
    if refused is not None:
        submitted, based_on, problems = refused
        form = sequence_form(seeded.definition, submitted, errors=problems)
    else:
        based_on = seeded.version
        form = sequence_form(seeded.definition, submitted_from(seeded.definition))
    return {
        "sequence": _row(seeded, _campaign_counts(campaigns).get(seeded.id, 0)),
        "enrollments": enrollment_lines(live),
        "based_on": based_on,
        "form": form,
        "notice": notice,
        "alert": alert if refused is None else refused_notice(refused[2]),
    }


def _shown_value(value: str | None) -> str:
    return "(none)" if value is None else value


def versions_context(
    seeded: SeededSequence, versions: Sequence[SequenceVersion]
) -> dict[str, object]:
    """What ``sequence_versions.html`` renders: each stored version, oldest first, with the
    fields it changed from the version before it, and ``not_kept_before``, the earliest stored
    version when it is above 1, else ``None``."""
    views: list[VersionView] = []
    previous: SequenceVersion | None = None
    for found in versions:
        changes: tuple[ChangeView, ...] = ()
        if previous is not None:
            changes = tuple(
                ChangeView(change.path, _shown_value(change.old), _shown_value(change.new))
                for change in sequence_form_diff(previous.definition, found.definition)
            )
        views.append(
            VersionView(
                version=found.version,
                made_by=found.actor,
                made_at=_time(found.created_at),
                current=found.version == seeded.version,
                first=previous is None,
                changes=changes,
            )
        )
        previous = found
    return {
        "sequence": _row(seeded, 0),
        "versions": tuple(views),
        "not_kept_before": versions[0].version if versions and versions[0].version > 1 else None,
    }
