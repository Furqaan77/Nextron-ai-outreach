"""The Campaigns page: create and edit Campaigns and their Variants (FR-3, AD-4, AD-13, NFR-3,
UX-DR3, UX-DR23; Story 11.8).

``create_app`` serves it at ``/campaigns``, linked from the Settings page, through
``campaigns`` (``nextron.app.setup.api.Campaigns``). Activate, Pause, Resume and the
legitimate-interests assessment stay on the Settings page's Campaigns panel
(``nextron.web.campaigns_page``). Every route is ``@personal_data(False)``: a Campaign, its
Variants and its references are configuration, never a person.

- ``GET /campaigns`` (``campaigns_list``): ``campaign_list.html`` with
  ``campaign_list_panel.html``: each stored Campaign by key with its name, status, Segment,
  Market and number of active Variants, linked to its page, and the create form (key, name and a
  select each of the stored Segments, Markets, Offers and Sequences). A new Campaign starts as a
  ``draft`` with no Variant.
- ``GET /campaigns/{campaign_id}`` (``campaign_editor``): ``campaign.html`` with
  ``campaign_edit_panel.html``: the edit form (name and the four selects, carrying
  ``based_on``, the version shown; the key is shown, never edited); when any Contact is enrolled
  the Segment and Market show read-only with the reason ("Segment and Market can't change once
  Contacts are enrolled") and are sent as hidden fields; then each Variant with its status, its
  angle notes and its number of Enrollments, an edit form (only an active Variant with no
  Enrollment: "Retire this Variant and add a new one, so its results stay comparable" otherwise)
  and a Retire form (only an active Variant, asking for confirmation; on the only active
  Variant of a non-``draft`` Campaign the ``LAST_ACTIVE_VARIANT`` reason shows instead), and
  the add form ("A Campaign can have at most 2 Variants" instead once 2 are active).
- ``POST /campaigns/create``, ``/campaigns/{id}/edit``, ``/campaigns/{id}/variants/add``,
  ``/campaigns/{id}/variants/{variant_id}/edit`` and ``/campaigns/{id}/variants/{variant_id}/
  retire`` run their use case in its own unit of work, then render the panel again in a
  read-only one: with a ``role="status"`` notice, or, refused (``CampaignRefused``, 200 so HTMX
  swaps it in), with a ``role="alert"`` "Nothing was saved." plus the form-level problem, and the
  refused form re-rendered as submitted, each problem beside its field. A stale form (the
  Campaign changed since it was opened) instead re-renders the current stored Campaign, with an
  alert that the page now shows it and the change must be made again.

**The forms** (``nextron.web.forms.form_fields``): each field once, nothing else, or
``MalformedForm`` (400). Create: ``key``, ``name``, ``segment``, ``market``, ``offer`` and
``sequence``. Edit: ``based_on`` and the same but ``key``. A Variant's add and edit forms:
``based_on``, ``name`` and, per note row ``i`` from 1 to 6, ``angles[i].step`` and
``angles[i].note`` (a row with both blank is left out; a step that isn't a whole number is sent
on as text, so the use case names it). Retire: ``based_on``. ``based_on`` is a positive whole
number. Each text is stripped.

A stored row that no longer decodes shows the unavailable notice instead of an error: the full
list page for the full-page GETs, the list panel for a POST (``web.app`` catches it from the use
case too).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from nextron.app.queries.setup import (
    SeededCampaign,
    SeededMarket,
    SeededOffer,
    SeededSegment,
    SeededSequence,
)
from nextron.domain.campaign import (
    FORM_FIELD,
    LAST_ACTIVE_VARIANT,
    MAX_STEP_ANGLES,
    MAX_VARIANTS,
    PAIR_FROZEN,
    TOO_MANY_VARIANTS,
    VARIANT_ENROLLED,
    CampaignStatus,
    StepAngleNote,
    VariantStatus,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

LIST_TEMPLATE: Final = "campaign_list.html"
LIST_PANEL_TEMPLATE: Final = "campaign_list_panel.html"
CAMPAIGN_TEMPLATE: Final = "campaign.html"
CAMPAIGN_PANEL_TEMPLATE: Final = "campaign_edit_panel.html"
BASED_ON: Final = "based_on"
KEY: Final = "key"
NAME: Final = "name"
REFERENCES: Final = ("segment", "market", "offer", "sequence")
CREATE_FIELDS: Final = frozenset({KEY, NAME, *REFERENCES})
EDIT_FIELDS: Final = frozenset({BASED_ON, NAME, *REFERENCES})
ANGLE_FIELD: Final = re.compile(r"angles\[([1-9][0-9]?)\]\.(step|note)")
NUMBER: Final = re.compile(r"[0-9]{1,9}")
MAX_BASED_ON_DIGITS: Final = 9
# Which form a refusal belongs to.
CREATE: Final = "create"
EDIT: Final = "edit"
ADD_VARIANT: Final = "add"
EDIT_VARIANT: Final = "variant"
RETIRE_VARIANT: Final = "retire"
NOTHING_SAVED: Final = "Nothing was saved."
CORRECT_FIELDS: Final = "Correct the fields marked below, then save again."
NOTHING_CHANGED: Final = "Nothing changed."
STALE_FORM: Final = "The page now shows the current Campaign. Make your change again."
NOT_FOUND: Final = "That Campaign doesn't exist."
UNAVAILABLE: Final = "The Campaigns can't be shown"
UNKNOWN: Final = "unknown"


@dataclass(frozen=True, slots=True)
class Option:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class AngleRow:
    """One note row of a Variant form: its index and texts."""

    index: int
    step: str
    note: str


@dataclass(frozen=True, slots=True)
class FormView:
    """A form: its element-id prefix, its texts, its select options, its note rows (Variant
    forms) and each field's problem."""

    prefix: str
    values: Mapping[str, str]
    options: Mapping[str, tuple[Option, ...]] = field(default_factory=dict)
    angles: tuple[AngleRow, ...] = ()
    errors: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubmittedVariant:
    """What a Variant's add or edit form submitted, as texts."""

    name: str
    angles: tuple[AngleRow, ...]


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused POST: which form (``create``, ``edit``, ``add``, ``variant`` or ``retire``),
    the Variant it named (or ``None``), the texts it submitted and the problems by field
    (``form`` for the whole form)."""

    target: str
    problems: Mapping[str, str]
    values: Mapping[str, str] = field(default_factory=dict)
    angles: tuple[AngleRow, ...] = ()
    variant_id: str | None = None


@dataclass(frozen=True, slots=True)
class CampaignRow:
    """One stored Campaign in the list."""

    id: str
    key: str
    name: str
    status: str
    segment: str
    market: str
    active_variants: int


@dataclass(frozen=True, slots=True)
class VariantView:
    """One Variant on a Campaign's page, with its forms (``None`` where it has none)."""

    id: str
    name: str
    status: str
    notes: tuple[str, ...]
    enrollments: int
    frozen_reason: str | None
    edit_form: FormView | None
    retirable: bool
    retire_reason: str | None = None  # why Retire isn't offered on an active Variant


# Submitted forms ---------------------------------------------------------------------------------


def _single(fields: FormFields, names: frozenset[str]) -> dict[str, str]:
    """Exactly the fields ``names``, each once, stripped; ``MalformedForm`` otherwise."""
    given = set(fields) - {CSRF_FIELD}
    if given != names:
        raise MalformedForm(WRONG_FIELDS)
    single: dict[str, str] = {}
    for name in sorted(given):
        values = fields[name]
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        single[name] = values[0].strip()
    return single


def _based_on(text: str) -> int:
    if not (
        text.isascii()
        and text.isdigit()
        and not text.startswith("0")
        and len(text) <= MAX_BASED_ON_DIGITS
    ):
        raise MalformedForm(WRONG_FIELDS)
    return int(text)


def submitted_create(fields: FormFields) -> dict[str, str]:
    """The create form's texts."""
    return _single(fields, CREATE_FIELDS)


def submitted_edit(fields: FormFields) -> tuple[int, dict[str, str]]:
    """The edit form's ``based_on`` and texts."""
    single = _single(fields, EDIT_FIELDS)
    return _based_on(single.pop(BASED_ON)), single


def submitted_retire(fields: FormFields) -> int:
    """The Retire form's ``based_on``."""
    return _based_on(_single(fields, frozenset({BASED_ON}))[BASED_ON])


def submitted_variant(fields: FormFields) -> tuple[int, SubmittedVariant]:
    """A Variant form's ``based_on``, name and note rows (by index)."""
    rows: dict[int, dict[str, str]] = {}
    for name, values in fields.items():
        if name in (CSRF_FIELD, BASED_ON, NAME):
            continue
        found = ANGLE_FIELD.fullmatch(name)
        if found is None or int(found.group(1)) > MAX_STEP_ANGLES:
            raise MalformedForm(WRONG_FIELDS)
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        rows.setdefault(int(found.group(1)), {})[found.group(2)] = values[0].strip()
    if NAME not in fields or BASED_ON not in fields:
        raise MalformedForm(WRONG_FIELDS)
    for name in (NAME, BASED_ON):
        if len(fields[name]) != 1:
            raise MalformedForm(REPEATED_FIELD)
    angles = tuple(
        AngleRow(index, texts.get("step", ""), texts.get("note", ""))
        for index, texts in sorted(rows.items())
    )
    based_on = _based_on(fields[BASED_ON][0].strip())
    return based_on, SubmittedVariant(fields[NAME][0].strip(), angles)


def step_angles(submitted: SubmittedVariant) -> tuple[StepAngleNote, ...]:
    """The notes the use case gets: rows with both texts blank left out; a step that isn't a
    whole number stays text, so the use case names it."""
    notes = []
    for row in submitted.angles:
        if not row.step and not row.note:
            continue
        step: object = int(row.step) if NUMBER.fullmatch(row.step) else row.step
        notes.append(StepAngleNote(step, row.note))  # type: ignore[arg-type]
    return tuple(notes)


# Notices -----------------------------------------------------------------------------------------


def refused_notice(problems: Mapping[str, str]) -> str:
    """The alert of a refused POST: nothing was saved, the form-level problem, and a pointer to
    the marked fields when there are any."""
    parts = [NOTHING_SAVED]
    form_problem = problems.get(FORM_FIELD)
    if form_problem:
        parts.append(f"{form_problem}.")
    if any(name != FORM_FIELD for name in problems):
        parts.append(CORRECT_FIELDS)
    return " ".join(parts)


def is_stale(problems: Mapping[str, str], stale: str) -> bool:
    """Whether a refusal was only the stale-form refusal ``stale``."""
    return dict(problems) == {FORM_FIELD: stale}


def stale_notice(problems: Mapping[str, str]) -> str:
    return f"{refused_notice(problems)} {STALE_FORM}"


def created_notice(key: str) -> str:
    return (
        f"Created {key} as a draft. Add a Variant on its page, then activate it on the Settings "
        "page."
    )


def saved_notice(key: str, version: int, changed: bool) -> str:
    if not changed:
        return NOTHING_CHANGED
    return (
        f"Saved {key} as version {version}. Running Enrollments keep their Variant, Offer and "
        "Sequence version; new Enrollments use these."
    )


def variant_added_notice(name: str) -> str:
    return f"Added Variant {name}."


def variant_saved_notice(name: str, changed: bool) -> str:
    return f"Saved Variant {name}." if changed else NOTHING_CHANGED


def variant_retired_notice(name: str) -> str:
    return (
        f"Retired Variant {name}. Its Enrollments keep it; new Enrollments use the active "
        "Variants."
    )


# Building the page ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class References:
    """The stored rows a Campaign form offers, read in the GET's unit of work."""

    segments: Sequence[SeededSegment]
    markets: Sequence[SeededMarket]
    offers: Sequence[SeededOffer]
    sequences: Sequence[SeededSequence]

    def choices(self) -> dict[str, tuple[tuple[str, str], ...]]:
        """``{field: ((id, label), ...)}`` for each select."""
        return {
            "segment": tuple((s.id, f"{s.definition.name} ({s.key})") for s in self.segments),
            "market": tuple((m.id, f"{m.definition.name} ({m.code})") for m in self.markets),
            "offer": tuple((o.id, f"{o.definition.name} ({o.key})") for o in self.offers),
            "sequence": tuple(
                (q.id, f"{q.definition.name} ({q.key}, {len(q.definition.steps)} Steps)")
                for q in self.sequences
            ),
        }

    def label(self, name: str, row_id: str) -> str:
        return dict(self.choices()[name]).get(row_id, UNKNOWN)


def _options(
    references: References, values: Mapping[str, str]
) -> dict[str, tuple[Option, ...]]:
    return {
        name: tuple(
            Option(row_id, label, row_id == values.get(name, ""))
            for row_id, label in choices
        )
        for name, choices in references.choices().items()
    }


def _angle_rows(given: Sequence[AngleRow]) -> tuple[AngleRow, ...]:
    """``given`` padded with blank rows to ``MAX_STEP_ANGLES``, the blank rows taking the
    indices in 1..``MAX_STEP_ANGLES`` that ``given`` doesn't use, ascending."""
    rows = list(given)[:MAX_STEP_ANGLES]
    used = {row.index for row in rows}
    free = [n for n in range(1, MAX_STEP_ANGLES + 1) if n not in used]
    rows += [AngleRow(n, "", "") for n in free[: MAX_STEP_ANGLES - len(rows)]]
    return tuple(rows)


def create_form(references: References, refusal: Refusal | None = None) -> FormView:
    values: Mapping[str, str] = {}
    errors: Mapping[str, str] = {}
    if refusal is not None and refusal.target == CREATE:
        values, errors = refusal.values, refusal.problems
    return FormView("campaign-create", values, _options(references, values), errors=errors)


def list_context(
    campaigns: Sequence[SeededCampaign],
    references: References,
    *,
    notice: str | None = None,
    created_id: str | None = None,
    refusal: Refusal | None = None,
    unavailable: str | None = None,
) -> dict[str, object]:
    """What ``campaign_list_panel.html`` renders."""
    rows = tuple(
        CampaignRow(
            id=found.id,
            key=found.key,
            name=found.name,
            status=str(found.status),
            segment=references.label("segment", found.segment_id),
            market=references.label("market", found.market_id),
            active_variants=sum(1 for v in found.variants if v.status == VariantStatus.ACTIVE),
        )
        for found in campaigns
    )
    return {
        "campaigns": rows,
        "form": create_form(references, refusal),
        "notice": notice,
        "created_id": created_id,
        "alert": None if refusal is None else refused_notice(refusal.problems),
        "unavailable": unavailable,
    }


def unavailable_context(problem: str) -> dict[str, object]:
    """The list page when a stored row it reads no longer decodes."""
    return {
        "campaigns": (),
        "form": None,
        "notice": None,
        "created_id": None,
        "alert": None,
        "unavailable": f"{UNAVAILABLE}: {problem}",
    }


def _stored_values(campaign: SeededCampaign) -> dict[str, str]:
    return {
        NAME: campaign.name,
        "segment": campaign.segment_id,
        "market": campaign.market_id,
        "offer": campaign.offer_id,
        "sequence": campaign.sequence_id,
    }


def _notes(angles: Sequence[StepAngleNote]) -> tuple[str, ...]:
    return tuple(f"Step {angle.step}: {angle.note}" for angle in angles)


def _variant_view(
    campaign: SeededCampaign, index: int, counts: Mapping[str, int], refusal: Refusal | None
) -> VariantView:
    variant = campaign.variants[index]
    enrolled = counts.get(variant.id, 0)
    active = variant.status == VariantStatus.ACTIVE
    last_running = (
        active
        and campaign.status != CampaignStatus.DRAFT
        and _active_count(campaign) == 1
    )
    edit_form = None
    if active and not enrolled:
        prefix = f"variant-{variant.id}"
        mine = (
            refusal is not None
            and refusal.target == EDIT_VARIANT
            and refusal.variant_id == variant.id
        )
        if mine:
            assert refusal is not None
            edit_form = FormView(
                prefix,
                refusal.values,
                angles=_angle_rows(refusal.angles),
                errors=refusal.problems,
            )
        else:
            rows = [
                AngleRow(n, str(angle.step), angle.note)
                for n, angle in enumerate(variant.step_angles, start=1)
            ]
            edit_form = FormView(prefix, {NAME: variant.name}, angles=_angle_rows(rows))
    return VariantView(
        id=variant.id,
        name=variant.name,
        status=str(variant.status),
        notes=_notes(variant.step_angles),
        enrollments=enrolled,
        frozen_reason=VARIANT_ENROLLED if active and enrolled else None,
        edit_form=edit_form,
        retirable=active and not last_running,
        retire_reason=LAST_ACTIVE_VARIANT if last_running else None,
    )


def _active_count(campaign: SeededCampaign) -> int:
    return sum(1 for v in campaign.variants if v.status == VariantStatus.ACTIVE)


def edit_context(
    campaign: SeededCampaign,
    references: References,
    counts: Mapping[str, int],
    *,
    notice: str | None = None,
    alert: str | None = None,
    refusal: Refusal | None = None,
) -> dict[str, object]:
    """What ``campaign_edit_panel.html`` renders: the Campaign, its edit form (the stored
    Campaign, or the refused submission), its Variants and the add form. ``alert`` is shown
    with the stored Campaign (a stale save)."""
    values: Mapping[str, str] = _stored_values(campaign)
    errors: Mapping[str, str] = {}
    if refusal is not None and refusal.target == EDIT:
        values, errors = refusal.values, refusal.problems
    enrolled = sum(counts.values()) > 0
    if enrolled:  # Segment and Market are shown read-only and sent as stored
        values = {**values, "segment": campaign.segment_id, "market": campaign.market_id}
    add_values: Mapping[str, str] = {}
    add_rows: Sequence[AngleRow] = ()
    add_errors: Mapping[str, str] = {}
    if refusal is not None and refusal.target == ADD_VARIANT:
        add_values, add_rows, add_errors = refusal.values, refusal.angles, refusal.problems
    return {
        "campaign": campaign,
        "status": str(campaign.status),
        "based_on": campaign.version,
        "form": FormView("campaign-edit", values, _options(references, values), errors=errors),
        "enrolled": enrolled,
        "pair_frozen": PAIR_FROZEN if enrolled else None,
        "segment_label": references.label("segment", campaign.segment_id),
        "market_label": references.label("market", campaign.market_id),
        "variants": tuple(
            _variant_view(campaign, index, counts, refusal)
            for index in range(len(campaign.variants))
        ),
        "add_form": None
        if _active_count(campaign) >= MAX_VARIANTS
        else FormView("variant-add", add_values, angles=_angle_rows(add_rows), errors=add_errors),
        "add_blocked": TOO_MANY_VARIANTS,
        "notice": notice,
        "alert": alert if refusal is None else refused_notice(refusal.problems),
    }
