"""The Segments section of Settings (FR-1, AD-12, AD-13, UX-DR3, UX-DR23; Story 11.5).

``create_app`` serves it at ``/segments``, linked from the Settings page. Every route is
``@personal_data(False)``: a Segment holds role titles, keywords and rules, never a person.

- ``GET /segments`` (``segments_page``): ``segments.html`` with ``segments_panel.html``: each
  stored Segment by key with its name, version, how many Campaigns use it ("inert" when none: a
  new Segment does nothing until a Campaign uses it) and links to its edit and history pages;
  then a "Create a Segment" form.
- ``GET /segments/{segment_id}`` (``segment_page``): ``segment.html`` with
  ``segment_panel.html``: the edit form, prefilled with the current version and carrying
  ``based_on`` (that version), and the "Re-score with the current definition" form with the
  number of open Leads it covers.
- ``GET /segments/{segment_id}/versions`` (``segment_versions_page``): every stored version,
  oldest first, with who saved it and when, and the fields it changed from the version before
  (``segment_form_diff``); the first stored version reads "First stored version", and when it
  is above 1 the page notes "Versions before v<n> were not kept.".
- ``POST /segments/create``, ``POST /segments/{segment_id}/edit`` and ``POST
  /segments/{segment_id}/rescore-current`` run ``SegmentEditor.create``, ``SegmentEditor.edit``
  and ``LeadScores.request_segment_rescore`` in their own unit of work, then render the panel
  again in a read-only one. A refused save (``SegmentRefused``) answers 200 with the form
  re-rendered as submitted, each problem beside its field (``aria-invalid="true"``) and a
  ``role="alert"`` notice that nothing was saved, holding any form-level problem.

**The form** (``submitted_segment``). Field names are the editor's form fields
(``nextron.domain.segments.edited_field``): ``key`` (create only), ``name``,
``size_range.minimum``, ``size_range.maximum``, ``size_range.unit``, and textareas with one
item per line, blank lines ignored: ``industry_keywords``, ``discovery_queries.places``,
``discovery_queries.registry.keywords``, ``discovery_queries.registry.sic_codes`` and
``discovery_queries.job_titles``; ``decision_maker_roles`` holds one rank per line, best first,
equal titles separated by `` | ``. Disqualifiers and Signal types are indexed fieldsets
(``disqualifiers[<i>].<field>``, ``signal_types[<i>].<field>``): the stored items plus 2 blank
rows, each with a ``remove`` checkbox; a row that is removed, or whose text fields are all
blank (and, for a Signal type, with nothing ticked), is left out. A disqualifier has ``key``,
``kind`` and ``size_clue`` (selects), ``threshold`` and ``description``; a Signal type has
``key``, ``name``, ``class`` and ``tier`` (selects), ``evidence`` and ``sources`` (checkboxes
from the code enums), ``score_only`` (a checkbox), ``weight``, ``match_terms`` (one per line)
and ``preferred_roles`` (one per line: ``<term> | <term> => <role> | <role>``). Whole numbers
are sent on as numbers; anything else stays text, so the validator names the field. A field
outside the form, a repeated single field, a row index above 99, a select or checkbox value
outside its enum, or a ``based_on`` that isn't a positive whole number raises
``MalformedForm`` (400). Each text is stripped.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nextron.app.queries.setup import SeededCampaign, SeededSegment, SegmentVersion
from nextron.domain.segments import (
    FORM_FIELD,
    DisqualifierKind,
    EvidenceType,
    SegmentDef,
    SignalClass,
    SignalSource,
    SizeClue,
    SizeUnit,
    segment_form_diff,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "segments.html"
PANEL_TEMPLATE: Final = "segments_panel.html"
SEGMENT_TEMPLATE: Final = "segment.html"
SEGMENT_PANEL_TEMPLATE: Final = "segment_panel.html"
VERSIONS_TEMPLATE: Final = "segment_versions.html"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
CREATE_PREFIX: Final = "segment-new"
EDIT_PREFIX: Final = "segment-edit"
BASED_ON: Final = "based_on"
KEY: Final = "key"
BLANK_ROWS: Final = 2
MAX_ROW: Final = 99
MAX_BASED_ON_DIGITS: Final = 9
MAX_NUMBER_DIGITS: Final = 9
RANK_SEPARATOR: Final = " | "
PREFERRED_ARROW: Final = " => "
TIERS: Final = ("1", "2", "3")
SINGLE_FIELDS: Final = (
    "name",
    "industry_keywords",
    "size_range.minimum",
    "size_range.maximum",
    "size_range.unit",
    "decision_maker_roles",
    "discovery_queries.places",
    "discovery_queries.registry.keywords",
    "discovery_queries.registry.sic_codes",
    "discovery_queries.job_titles",
)
DISQUALIFIER_TEXTS: Final = ("key", "threshold", "description")
DISQUALIFIER_FIELDS: Final = (*DISQUALIFIER_TEXTS, "kind", "size_clue", "remove")
SIGNAL_TEXTS: Final = ("key", "name", "weight", "match_terms", "preferred_roles")
SIGNAL_FIELDS: Final = (
    *SIGNAL_TEXTS,
    "class",
    "tier",
    "evidence",
    "sources",
    "score_only",
    "remove",
)
SIGNAL_MULTI: Final = frozenset({"evidence", "sources"})
ROW_FIELD: Final = re.compile(r"(disqualifiers|signal_types)\[(0|[1-9][0-9]?)\]\.([a-z_]+)")
NUMBER: Final = re.compile(r"[0-9]{1,9}")
NOTHING_SAVED: Final = "Nothing was saved."
CORRECT_FIELDS: Final = "Correct the fields marked below, then save again."
NOTHING_CHANGED: Final = "Nothing changed, so no new version was saved."
FIRST_VERSION: Final = "First stored version"
NOT_FOUND: Final = "That Segment doesn't exist."
INERT: Final = "inert"
CHECKED: Final = "on"  # a ticked checkbox's value


@dataclass(frozen=True, slots=True)
class DisqualifierRow:
    """One disqualifier fieldset as submitted: its form index, its texts and selects, and
    whether its remove box was ticked."""

    index: int
    values: Mapping[str, str]
    removed: bool = False


@dataclass(frozen=True, slots=True)
class SignalRow:
    """One Signal type fieldset as submitted."""

    index: int
    values: Mapping[str, str]
    evidence: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    score_only: bool = False
    removed: bool = False


@dataclass(frozen=True, slots=True)
class SubmittedSegment:
    """What the create or edit form submitted, as texts."""

    values: Mapping[str, str]
    disqualifiers: tuple[DisqualifierRow, ...]
    signal_types: tuple[SignalRow, ...]


@dataclass(frozen=True, slots=True)
class Option:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class DisqualifierView:
    index: int
    values: Mapping[str, str]
    kinds: tuple[Option, ...]
    clues: tuple[Option, ...]
    removed: bool


@dataclass(frozen=True, slots=True)
class SignalView:
    index: int
    values: Mapping[str, str]
    classes: tuple[Option, ...]
    tiers: tuple[Option, ...]
    evidence: tuple[Option, ...]
    sources: tuple[Option, ...]
    score_only: bool
    removed: bool


@dataclass(frozen=True, slots=True)
class SegmentForm:
    """A create or edit form: its element-id prefix, values, rows, each field's problem and
    whether it shows open."""

    prefix: str
    values: Mapping[str, str]
    units: tuple[Option, ...]
    disqualifiers: tuple[DisqualifierView, ...]
    signal_types: tuple[SignalView, ...]
    errors: Mapping[str, str] = field(default_factory=dict)
    open: bool = False


@dataclass(frozen=True, slots=True)
class SegmentRow:
    """One stored Segment in the list."""

    id: str
    key: str
    name: str
    version: int
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


def _enum_value(value: str, allowed: Iterable[str]) -> str:
    if value != "" and value not in allowed:
        raise MalformedForm(WRONG_FIELDS)
    return value


def submitted_segment(fields: FormFields, *, with_key: bool) -> SubmittedSegment:
    """The create (``with_key``) or edit form's fields; ``MalformedForm`` otherwise. Every
    single field must be present once; row fields are optional."""
    singles = {*SINGLE_FIELDS, KEY} if with_key else set(SINGLE_FIELDS)
    allowed_singles = singles | ({BASED_ON} if not with_key else set())
    rows: dict[tuple[str, int], dict[str, list[str]]] = {}
    for name, values in fields.items():
        if name == CSRF_FIELD or name in allowed_singles:
            continue
        found = ROW_FIELD.fullmatch(name)
        if found is None:
            raise MalformedForm(WRONG_FIELDS)
        kind, index, sub = found.group(1), int(found.group(2)), found.group(3)
        known = DISQUALIFIER_FIELDS if kind == "disqualifiers" else SIGNAL_FIELDS
        if sub not in known or index > MAX_ROW:
            raise MalformedForm(WRONG_FIELDS)
        if len(values) != 1 and not (kind == "signal_types" and sub in SIGNAL_MULTI):
            raise MalformedForm(REPEATED_FIELD)
        rows.setdefault((kind, index), {})[sub] = list(values)
    if not singles <= set(fields):
        raise MalformedForm(WRONG_FIELDS)
    values = {name: _one(fields, name).strip() for name in sorted(singles)}
    _enum_value(values["size_range.unit"], [str(unit) for unit in SizeUnit])
    disqualifiers = []
    signal_types = []
    for (kind, index), subs in sorted(rows.items()):
        single = {sub: found[0] for sub, found in subs.items() if sub not in SIGNAL_MULTI}
        removed = single.pop("remove", None) is not None
        texts = {sub: text.strip() for sub, text in single.items()}
        if kind == "disqualifiers":
            _enum_value(texts.get("kind", ""), [str(each) for each in DisqualifierKind])
            _enum_value(texts.get("size_clue", ""), [str(each) for each in SizeClue])
            disqualifiers.append(DisqualifierRow(index, texts, removed))
            continue
        _enum_value(texts.get("class", ""), [str(each) for each in SignalClass])
        _enum_value(texts.get("tier", ""), TIERS)
        evidence = tuple(subs.get("evidence", ()))
        sources = tuple(subs.get("sources", ()))
        for value in evidence:
            _enum_value(value, [str(each) for each in EvidenceType])
        for value in sources:
            _enum_value(value, [str(each) for each in SignalSource])
        score_only = texts.pop("score_only", None) is not None
        signal_types.append(
            SignalRow(index, texts, evidence, sources, score_only, removed)
        )
    return SubmittedSegment(values, tuple(disqualifiers), tuple(signal_types))


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


def submitted_rescore(fields: FormFields) -> None:
    """The "Re-score with the current definition" form posts nothing but the CSRF token."""
    if set(fields) - {CSRF_FIELD}:
        raise MalformedForm(WRONG_FIELDS)


# The proposed file form --------------------------------------------------------------------------


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split("|") if part.strip()]


def _number(text: str) -> int | str:
    """A whole number the validator can check, else the text itself (it names the field)."""
    return int(text) if NUMBER.fullmatch(text) else text


def _blank_disqualifier(row: DisqualifierRow) -> bool:
    return not any(row.values.get(name, "") for name in (*DISQUALIFIER_TEXTS, "kind", "size_clue"))


def _blank_signal(row: SignalRow) -> bool:
    texts = any(row.values.get(name, "") for name in (*SIGNAL_TEXTS, "class", "tier"))
    return not (texts or row.evidence or row.sources or row.score_only)


def _preferred(text: str) -> list[object]:
    entries: list[object] = []
    for line in _lines(text):
        when, arrow, roles = line.partition("=>")
        entry: dict[str, object] = {"when_terms": _split(when)}
        if arrow:  # without one, the validator names the missing roles
            entry["roles"] = _split(roles)
        entries.append(entry)
    return entries


@dataclass(frozen=True, slots=True)
class Proposed:
    """The file form the submitted form proposes, and the form index of each kept row by its
    position in the proposed lists (to put a problem beside the right fieldset)."""

    form: dict[str, object]
    disqualifier_rows: tuple[int, ...]
    signal_rows: tuple[int, ...]


def proposed_form(submitted: SubmittedSegment, key: str | None = None) -> Proposed:
    """The file form ``submitted`` proposes (``key`` from the form when ``None``)."""
    values = submitted.values
    kept_disqualifiers = [
        row for row in submitted.disqualifiers if not row.removed and not _blank_disqualifier(row)
    ]
    kept_signals = [
        row for row in submitted.signal_types if not row.removed and not _blank_signal(row)
    ]
    queries: dict[str, object] = {"places": _lines(values["discovery_queries.places"])}
    registry: dict[str, object] = {}
    keywords = _lines(values["discovery_queries.registry.keywords"])
    codes = _lines(values["discovery_queries.registry.sic_codes"])
    if keywords:
        registry["keywords"] = keywords
    if codes:
        registry["sic_codes"] = codes
    if registry:
        queries["registry"] = registry
    titles = _lines(values["discovery_queries.job_titles"])
    if titles:
        queries["job_titles"] = titles
    form: dict[str, object] = {
        "key": values.get(KEY, "") if key is None else key,
        "name": values["name"],
        "industry_keywords": _lines(values["industry_keywords"]),
        "size_range": {
            "minimum": _number(values["size_range.minimum"]),
            "maximum": _number(values["size_range.maximum"]),
            "unit": values["size_range.unit"],
        },
        "decision_maker_roles": [_split(line) for line in _lines(values["decision_maker_roles"])],
        "disqualifiers": [
            {
                "key": row.values.get("key", ""),
                "kind": row.values.get("kind", ""),
                "size_clue": row.values.get("size_clue", ""),
                "threshold": _number(row.values.get("threshold", "")),
                "description": row.values.get("description", ""),
            }
            for row in kept_disqualifiers
        ],
        "signal_types": [
            {
                "key": row.values.get("key", ""),
                "name": row.values.get("name", ""),
                "class": row.values.get("class", ""),
                "tier": _number(row.values.get("tier", "")),
                "evidence": list(row.evidence),
                "sources": list(row.sources),
                "score_only": row.score_only,
                "weight": _number(row.values.get("weight", "")),
                "match_terms": _lines(row.values.get("match_terms", "")),
                "preferred_roles": _preferred(row.values.get("preferred_roles", "")),
            }
            for row in kept_signals
        ],
        "discovery_queries": queries,
    }
    return Proposed(
        form,
        tuple(row.index for row in kept_disqualifiers),
        tuple(row.index for row in kept_signals),
    )


_POSITION: Final = re.compile(r"(disqualifiers|signal_types)\[([0-9]+)\](.*)")


def form_problems(problems: Mapping[str, str], proposed: Proposed) -> dict[str, str]:
    """The editor's problems with each row position turned back into its form index."""
    shown: dict[str, str] = {}
    for name, reason in problems.items():
        found = _POSITION.fullmatch(name)
        if found is not None:
            rows = (
                proposed.disqualifier_rows
                if found.group(1) == "disqualifiers"
                else proposed.signal_rows
            )
            position = int(found.group(2))
            if position < len(rows):
                name = f"{found.group(1)}[{rows[position]}]{found.group(3)}"
            else:
                name = found.group(1)
        shown[name] = reason
    return shown


# Building the forms ------------------------------------------------------------------------------


def _options(values: Iterable[str], selected: str) -> tuple[Option, ...]:
    return tuple(Option(value, value, value == selected) for value in values)


def _ticks(values: Iterable[str], ticked: Sequence[str]) -> tuple[Option, ...]:
    return tuple(Option(value, value, value in ticked) for value in values)


def _disqualifier_view(row: DisqualifierRow) -> DisqualifierView:
    return DisqualifierView(
        index=row.index,
        values=row.values,
        kinds=_options([str(each) for each in DisqualifierKind], row.values.get("kind", "")),
        clues=_options([str(each) for each in SizeClue], row.values.get("size_clue", "")),
        removed=row.removed,
    )


def _signal_view(row: SignalRow) -> SignalView:
    return SignalView(
        index=row.index,
        values=row.values,
        classes=_options([str(each) for each in SignalClass], row.values.get("class", "")),
        tiers=_options(TIERS, row.values.get("tier", "")),
        evidence=_ticks([str(each) for each in EvidenceType], row.evidence),
        sources=_ticks([str(each) for each in SignalSource], row.sources),
        score_only=row.score_only,
        removed=row.removed,
    )


def _with_blank_rows(submitted: SubmittedSegment) -> SubmittedSegment:
    """``submitted`` with 2 blank rows after the last of each list."""
    next_disqualifier = max((row.index for row in submitted.disqualifiers), default=-1) + 1
    next_signal = max((row.index for row in submitted.signal_types), default=-1) + 1
    blank_disqualifiers = tuple(
        DisqualifierRow(next_disqualifier + offset, {})
        for offset in range(BLANK_ROWS)
        if next_disqualifier + offset <= MAX_ROW
    )
    blank_signals = tuple(
        SignalRow(next_signal + offset, {})
        for offset in range(BLANK_ROWS)
        if next_signal + offset <= MAX_ROW
    )
    return SubmittedSegment(
        submitted.values,
        submitted.disqualifiers + blank_disqualifiers,
        submitted.signal_types + blank_signals,
    )


def segment_form(
    prefix: str,
    submitted: SubmittedSegment,
    *,
    errors: Mapping[str, str] | None = None,
    open_: bool = False,
) -> SegmentForm:
    """The form showing ``submitted`` (plus 2 blank rows per list), with ``errors``."""
    shown = _with_blank_rows(submitted)
    return SegmentForm(
        prefix=prefix,
        values=shown.values,
        units=_options([str(unit) for unit in SizeUnit], shown.values.get("size_range.unit", "")),
        disqualifiers=tuple(_disqualifier_view(row) for row in shown.disqualifiers),
        signal_types=tuple(_signal_view(row) for row in shown.signal_types),
        errors={name: reason for name, reason in (errors or {}).items() if name != FORM_FIELD},
        open=open_,
    )


def _joined(items: Iterable[str]) -> str:
    return "\n".join(items)


def submitted_from(segment: SegmentDef) -> SubmittedSegment:
    """The form texts of a stored definition."""
    queries = segment.discovery_queries
    values = {
        KEY: segment.key,
        "name": segment.name,
        "industry_keywords": _joined(segment.industry_keywords),
        "size_range.minimum": str(segment.size_range.minimum),
        "size_range.maximum": str(segment.size_range.maximum),
        "size_range.unit": str(segment.size_range.unit),
        "decision_maker_roles": _joined(
            RANK_SEPARATOR.join(rank) for rank in segment.decision_maker_roles
        ),
        "discovery_queries.places": _joined(queries.places),
        "discovery_queries.registry.keywords": _joined(queries.registry_keywords),
        "discovery_queries.registry.sic_codes": _joined(queries.registry_sic_codes),
        "discovery_queries.job_titles": _joined(queries.job_titles),
    }
    disqualifiers = tuple(
        DisqualifierRow(
            index,
            {
                "key": each.key,
                "kind": str(each.kind),
                "size_clue": str(each.size_clue),
                "threshold": str(each.threshold),
                "description": each.description,
            },
        )
        for index, each in enumerate(segment.disqualifiers)
    )
    signal_types = tuple(
        SignalRow(
            index,
            {
                "key": each.key,
                "name": each.name,
                "class": str(each.signal_class),
                "tier": str(each.tier),
                "weight": str(each.weight),
                "match_terms": _joined(each.match_terms),
                "preferred_roles": _joined(
                    RANK_SEPARATOR.join(preferred.when_terms)
                    + PREFERRED_ARROW
                    + RANK_SEPARATOR.join(preferred.roles)
                    for preferred in each.preferred_roles
                ),
            },
            tuple(str(item) for item in each.evidence),
            tuple(str(item) for item in each.sources),
            each.score_only,
        )
        for index, each in enumerate(segment.signal_types)
    )
    return SubmittedSegment(values, disqualifiers, signal_types)


def empty_submission() -> SubmittedSegment:
    """The create form's starting texts: everything blank, the unit ``staff``."""
    values = dict.fromkeys((*SINGLE_FIELDS, KEY), "")
    values["size_range.unit"] = str(SizeUnit.STAFF)
    return SubmittedSegment(values, (), ())


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


def created_notice(key: str) -> str:
    return f"Created {key} at version 1. It is inert until a Campaign uses it."


def saved_notice(key: str, version: int, changed: bool) -> str:
    if not changed:
        return NOTHING_CHANGED
    return (
        f"Saved {key} as version {version}. Leads already scored keep the version they were "
        "scored with until you re-score them with the current definition."
    )


def rescore_notice(leads: int) -> str:
    if leads == 0:
        return "No open Lead uses this Segment, so nothing was queued."
    noun = "Lead" if leads == 1 else "Leads"
    return f"Re-score with the current definition queued for {leads} {noun}."


# Page contexts -----------------------------------------------------------------------------------


def _time(at: datetime) -> str:
    return f"{at:{TIME_FORMAT}}"


def segment_rows(
    segments: Sequence[SeededSegment], campaigns: Sequence[SeededCampaign]
) -> tuple[SegmentRow, ...]:
    """Each stored Segment with how many Campaigns use it."""
    counts: dict[str, int] = {}
    for campaign in campaigns:
        counts[campaign.segment_id] = counts.get(campaign.segment_id, 0) + 1
    return tuple(
        SegmentRow(each.id, each.key, each.definition.name, each.version, counts.get(each.id, 0))
        for each in segments
    )


def list_context(
    rows: Sequence[SegmentRow],
    *,
    notice: str | None = None,
    refused: tuple[SubmittedSegment, Mapping[str, str]] | None = None,
    unavailable: str | None = None,
) -> dict[str, object]:
    """What ``segments_panel.html`` renders: the notice or alert, the Segments and the create
    form (open, with the submitted texts and problems, after a refusal)."""
    if refused is not None:
        submitted, problems = refused
        create = segment_form(CREATE_PREFIX, submitted, errors=problems, open_=True)
    else:
        create = segment_form(CREATE_PREFIX, empty_submission())
    return {
        "notice": notice,
        "alert": None if refused is None else refused_notice(refused[1]),
        "unavailable": unavailable,
        "segments": tuple(rows),
        "create": create,
    }


def edit_context(
    seeded: SeededSegment,
    campaigns: int,
    open_leads: int,
    *,
    notice: str | None = None,
    refused: tuple[SubmittedSegment, int, Mapping[str, str]] | None = None,
) -> dict[str, object]:
    """What ``segment_panel.html`` renders: the Segment, the edit form (the stored version, or
    the refused submission with its ``based_on`` and problems), and the re-score form."""
    if refused is not None:
        submitted, based_on, problems = refused
        values = {**submitted.values, KEY: seeded.key}
        shown = SubmittedSegment(values, submitted.disqualifiers, submitted.signal_types)
        form = segment_form(EDIT_PREFIX, shown, errors=problems, open_=True)
    else:
        based_on = seeded.version
        form = segment_form(EDIT_PREFIX, submitted_from(seeded.definition))
    return {
        "segment": SegmentRow(
            seeded.id, seeded.key, seeded.definition.name, seeded.version, campaigns
        ),
        "based_on": based_on,
        "form": form,
        "open_leads": open_leads,
        "notice": notice,
        "alert": None if refused is None else refused_notice(refused[2]),
    }


def _shown_value(value: str | None) -> str:
    return "(none)" if value is None else value


def versions_context(
    seeded: SeededSegment, versions: Sequence[SegmentVersion]
) -> dict[str, object]:
    """What ``segment_versions.html`` renders: each stored version, oldest first, with the
    fields it changed from the version before it, and ``not_kept_before``, the earliest stored
    version when it is above 1 (a backfilled Segment: earlier versions were never kept), else
    ``None``."""
    views: list[VersionView] = []
    previous: SegmentVersion | None = None
    for found in versions:
        changes: tuple[ChangeView, ...] = ()
        if previous is not None:
            changes = tuple(
                ChangeView(change.path, _shown_value(change.old), _shown_value(change.new))
                for change in segment_form_diff(previous.definition, found.definition)
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
        "segment": SegmentRow(seeded.id, seeded.key, seeded.definition.name, seeded.version, 0),
        "versions": tuple(views),
        "not_kept_before": versions[0].version if versions and versions[0].version > 1 else None,
    }
