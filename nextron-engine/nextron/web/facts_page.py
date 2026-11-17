"""The Approved Company Facts page (FR-72, AD-13, UX-DR3, UX-DR4, UX-DR24; Story 4.3).

``create_app`` serves it at ``/facts``, linked from the Settings page (the navigation keeps its
sections, UX-DR1). Every route is ``@personal_data(False)``: facts are claims about Nextron, its
work, its Offer and public regulation, never about a person.

- ``GET /facts`` (``company_facts_page``) renders ``facts.html``, which extends ``base.html``
  and includes ``facts_panel.html``, in the GET's read-only unit of work.
- ``POST /facts/add`` (``company_fact_add``), ``POST /facts/edit`` (``company_fact_edit``) and
  ``POST /facts/retract`` (``company_fact_retract``) take their fields through
  ``nextron.web.forms.form_fields`` and check them here (``submitted_add``, ``submitted_edit``,
  ``submitted_retract``): a field outside the form's set, a missing or repeated single field, a
  ``segment`` or ``market`` value that isn't a ULID, or a ``based_on`` that isn't a positive
  whole number raises ``MalformedForm`` (400). Each runs its ``CompanyFacts`` use case in its own
  unit of work, then renders ``facts_panel.html`` alone in a read-only one; the forms post with
  ``hx-post``, ``hx-target="#facts-panel"`` and ``hx-swap="outerHTML"``, and need no JavaScript
  of their own (``<details>`` opens and closes them).

**The panel** (``panel_context``) shows, above the list, the outcome notice and an "Add a fact"
form (``last_checked_on`` defaults to today's UTC date), then each fact by ``fact_id``: its
current version (or, for a fact with none, "Retracted" with its latest version), with the
wording, the type, the source as ``safe_link``, the Segment and Market names, the metric with
its confirmation date (or "none"), the last-checked date, the version and who made it and when;
an Edit form inside ``<details>``, prefilled, with hidden ``fact_id`` and ``based_on`` (the
latest version); a Retract form while the fact has a current version; and ``<details>``
"Earlier versions (k)", each with the same fields plus "retracted <time>" where set. Segments
and Markets are checkboxes valued with their ids.

**Outcomes.** Saved: a ``role="status"`` notice ("Saved <fact_id> as version <n>.", "Nothing
changed, so no new version was saved.", "Retracted <fact_id>; Drafts can no longer cite it." or
"<fact_id> was already retracted."). Refused (``InvalidFact``): 200, so HTMX swaps the panel in,
with the refused form open and holding the submitted values, each problem beside its field
(``aria-invalid="true"``), and a ``role="alert"`` notice that nothing was saved, which also
holds a form-level problem (a stale page, an unknown fact).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

from nextron.app.playbook.api import FactRetracted, FactSaved
from nextron.app.queries.playbook import FactHistory, FactVersion
from nextron.domain.company_facts import FORM_FIELD, FactContent, FactFields, FactType
from nextron.domain.ids import is_ulid
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "facts.html"
PANEL_TEMPLATE: Final = "facts_panel.html"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
ADD_PREFIX: Final = "fact-new"  # element ids of the Add form; an Edit form's are fact-edit-<id>
FACT_ID: Final = "fact_id"
BASED_ON: Final = "based_on"
SEGMENT: Final = "segment"  # one checkbox per Segment, valued with its id
MARKET: Final = "market"  # one checkbox per Market, valued with its id
# The content's text fields, as the forms name them (FactFields' names).
TEXT_FIELDS: Final = (
    "type",
    "wording",
    "source_url",
    "metric",
    "metric_confirmed_at",
    "scope_note",
    "last_checked_on",
)
ADD_SINGLE: Final = frozenset({FACT_ID, *TEXT_FIELDS})
EDIT_SINGLE: Final = frozenset({FACT_ID, BASED_ON, *TEXT_FIELDS})
RETRACT_SINGLE: Final = frozenset({FACT_ID})
CHOICE_FIELDS: Final = frozenset({SEGMENT, MARKET})
MAX_BASED_ON_DIGITS: Final = 9
NONE_SHOWN: Final = "none"
UNKNOWN_SEGMENT: Final = "unknown Segment"
UNKNOWN_MARKET: Final = "unknown Market"
NOTHING_CHANGED: Final = "Nothing changed, so no new version was saved."
NOTHING_SAVED: Final = "Nothing was saved."
CORRECT_FIELDS: Final = "Correct the fields marked below, then save again."
NOTHING_RETRACTED: Final = "Nothing was retracted."
TYPE_LABELS: Final = {
    FactType.COMPANY: "Company",
    FactType.CASE_STUDY: "Case study",
    FactType.OFFER: "Offer",
    FactType.REGULATION: "Regulation",
    FactType.STATISTIC: "Statistic",
}


@dataclass(frozen=True, slots=True)
class Named:
    """A Segment or Market the forms offer: its id and display name."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Choice:
    """One checkbox: a Segment or Market id, its name, and whether it is ticked."""

    id: str
    name: str
    checked: bool


@dataclass(frozen=True, slots=True)
class TypeOption:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class FactForm:
    """An Add or Edit form: its element-id ``prefix``, its values, its checkboxes, each
    field's problem and whether it shows open."""

    prefix: str
    fact_id: str
    based_on: int | None
    values: Mapping[str, str]
    types: tuple[TypeOption, ...]
    segments: tuple[Choice, ...]
    markets: tuple[Choice, ...]
    errors: Mapping[str, str] = field(default_factory=dict)
    open: bool = False


@dataclass(frozen=True, slots=True)
class VersionView:
    """One version as the panel shows it."""

    version: int
    type: str
    wording: str
    source_url: str
    segments: str
    markets: str
    metric: str
    last_checked_on: str
    made_by: str
    made_at: str
    retracted_at: str | None


@dataclass(frozen=True, slots=True)
class FactView:
    """One fact: the version shown (its current one, else its latest), whether it is
    retracted, its earlier versions (newest first), its Edit form and whether it can be
    retracted."""

    fact_id: str
    retracted: bool
    shown: VersionView
    earlier: tuple[VersionView, ...]
    edit: FactForm


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused POST: which form (``add``, ``edit`` or ``retract``), what it submitted and
    the problems by field (``form`` for the whole form)."""

    form: str
    fact_id: str
    problems: Mapping[str, str]
    fields: FactFields | None = None
    based_on: int | None = None


# Submitted forms ---------------------------------------------------------------------------------


def _single(fields: FormFields, allowed: frozenset[str]) -> dict[str, str]:
    """The form's one-value fields; ``MalformedForm`` for a field outside ``allowed`` and the
    checkboxes, a missing one, or a repeated one."""
    names = set(fields) - {CSRF_FIELD}
    if not names >= allowed or names - allowed - CHOICE_FIELDS:
        raise MalformedForm(WRONG_FIELDS)
    single = {}
    for name in sorted(allowed):
        values = fields[name]
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        single[name] = values[0]
    return single


def _ids(fields: FormFields, name: str) -> tuple[str, ...]:
    values = tuple(fields.get(name, ()))
    if not all(is_ulid(value) for value in values):
        raise MalformedForm(WRONG_FIELDS)
    return values


def _fact_fields(fields: FormFields, single: Mapping[str, str]) -> FactFields:
    return FactFields(
        type=single["type"],
        wording=single["wording"],
        source_url=single["source_url"],
        segments=_ids(fields, SEGMENT),
        markets=_ids(fields, MARKET),
        metric=single["metric"],
        metric_confirmed_at=single["metric_confirmed_at"],
        scope_note=single["scope_note"],
        last_checked_on=single["last_checked_on"],
    )


def submitted_add(fields: FormFields) -> tuple[str, FactFields]:
    """The Add form's ``fact_id`` and fields; ``MalformedForm`` otherwise."""
    single = _single(fields, ADD_SINGLE)
    return single[FACT_ID], _fact_fields(fields, single)


def _based_on(text: str) -> int:
    if not (
        text.isascii()
        and text.isdigit()
        and not text.startswith("0")
        and len(text) <= MAX_BASED_ON_DIGITS
    ):
        raise MalformedForm(WRONG_FIELDS)
    return int(text)


def submitted_edit(fields: FormFields) -> tuple[str, int, FactFields]:
    """The Edit form's ``fact_id``, ``based_on`` and fields; ``MalformedForm`` otherwise."""
    single = _single(fields, EDIT_SINGLE)
    return single[FACT_ID], _based_on(single[BASED_ON]), _fact_fields(fields, single)


def submitted_retract(fields: FormFields) -> str:
    """The Retract form's ``fact_id``; ``MalformedForm`` otherwise (it has no checkboxes)."""
    if set(fields) & CHOICE_FIELDS:
        raise MalformedForm(WRONG_FIELDS)
    return _single(fields, RETRACT_SINGLE)[FACT_ID]


# Notices -----------------------------------------------------------------------------------------


def saved_notice(result: FactSaved) -> str:
    if not result.changed:
        return NOTHING_CHANGED
    return f"Saved {result.fact_id} as version {result.version}."


def retracted_notice(result: FactRetracted) -> str:
    if result.retracted:
        return f"Retracted {result.fact_id}; Drafts can no longer cite it."
    return f"{result.fact_id} was already retracted."


def refused_notice(refusal: Refusal) -> str:
    """The alert of a refused form: nothing was saved (or retracted), the form-level problem,
    and a pointer to the marked fields when there are any."""
    parts = [NOTHING_RETRACTED if refusal.form == "retract" else NOTHING_SAVED]
    form_problem = refusal.problems.get(FORM_FIELD)
    if form_problem:
        parts.append(f"{form_problem}.")
    if any(name != FORM_FIELD for name in refusal.problems):
        parts.append(CORRECT_FIELDS)
    return " ".join(parts)


# The panel ---------------------------------------------------------------------------------------


def _time(at: datetime) -> str:
    return f"{at:{TIME_FORMAT}}"


def _names(refs: Sequence[str], known: Mapping[str, str], unknown: str) -> str:
    """The names of ``refs``, in alphabetical order (ids sort in no meaningful order)."""
    return ", ".join(sorted(known.get(ref, unknown) for ref in refs))


def _version_view(
    found: FactVersion, segment_names: Mapping[str, str], market_names: Mapping[str, str]
) -> VersionView:
    content = found.content
    if content.metric is None:
        metric = NONE_SHOWN
    elif content.metric_confirmed_at is None:  # rule 2 forbids it; shown as it is
        metric = content.metric
    else:
        metric = f"{content.metric} (confirmed by the client on {content.metric_confirmed_at})"
    return VersionView(
        version=found.version,
        type=TYPE_LABELS.get(content.type, str(content.type)),
        wording=content.wording,
        source_url=content.source_url,
        segments=_names(content.segments, segment_names, UNKNOWN_SEGMENT),
        markets=_names(content.markets, market_names, UNKNOWN_MARKET),
        metric=metric,
        last_checked_on=content.last_checked_on.isoformat(),
        made_by=str(found.created_by),
        made_at=_time(found.created_at),
        retracted_at=None if found.retracted_at is None else _time(found.retracted_at),
    )


def _content_values(content: FactContent) -> dict[str, str]:
    def text(value: object) -> str:
        if value is None:
            return ""
        return value.isoformat() if isinstance(value, date) else str(value)

    return {name: text(getattr(content, name)) for name in TEXT_FIELDS}


def _submitted_values(fields: FactFields) -> dict[str, str]:
    return {name: getattr(fields, name) for name in TEXT_FIELDS}


def _choices(options: Sequence[Named], ticked: Sequence[str]) -> tuple[Choice, ...]:
    chosen = set(ticked)
    return tuple(Choice(option.id, option.name, option.id in chosen) for option in options)


def _types(selected: str) -> tuple[TypeOption, ...]:
    return tuple(
        TypeOption(str(member), TYPE_LABELS[member], str(member) == selected)
        for member in FactType
    )


def _form(
    prefix: str,
    fact_id: str,
    based_on: int | None,
    values: Mapping[str, str],
    ticked: tuple[Sequence[str], Sequence[str]],
    options: tuple[Sequence[Named], Sequence[Named]],
    *,
    errors: Mapping[str, str] | None = None,
    open_: bool = False,
) -> FactForm:
    return FactForm(
        prefix=prefix,
        fact_id=fact_id,
        based_on=based_on,
        values={**values, FACT_ID: fact_id},  # the Add form shows its fact_id field
        types=_types(values.get("type", "")),
        segments=_choices(options[0], ticked[0]),
        markets=_choices(options[1], ticked[1]),
        errors={name: reason for name, reason in (errors or {}).items() if name != FORM_FIELD},
        open=open_,
    )


def _fact_view(
    history: FactHistory,
    options: tuple[Sequence[Named], Sequence[Named]],
    refusal: Refusal | None,
) -> FactView:
    segment_names = {option.id: option.name for option in options[0]}
    market_names = {option.id: option.name for option in options[1]}
    shown = history.current if history.current is not None else history.versions[0]
    latest = history.versions[0].version
    prefix = f"fact-edit-{history.fact_id}"
    if refusal is not None and refusal.form == "edit" and refusal.fact_id == history.fact_id:
        assert refusal.fields is not None
        edit = _form(
            prefix,
            history.fact_id,
            refusal.based_on,
            _submitted_values(refusal.fields),
            (refusal.fields.segments, refusal.fields.markets),
            options,
            errors=refusal.problems,
            open_=True,
        )
    else:
        content = shown.content
        edit = _form(
            prefix,
            history.fact_id,
            latest,
            _content_values(content),
            (content.segments, content.markets),
            options,
        )
    return FactView(
        fact_id=history.fact_id,
        retracted=history.current is None,
        shown=_version_view(shown, segment_names, market_names),
        earlier=tuple(
            _version_view(found, segment_names, market_names)
            for found in history.versions
            if found.id != shown.id
        ),
        edit=edit,
    )


def panel_context(
    histories: Sequence[FactHistory],
    segments: Sequence[Named],
    markets: Sequence[Named],
    today: date,
    *,
    notice: str | None = None,
    refusal: Refusal | None = None,
) -> dict[str, object]:
    """What ``facts_panel.html`` renders: the notice or alert, the Add form, and each fact."""
    options = (tuple(segments), tuple(markets))
    if refusal is not None and refusal.form == "add":
        assert refusal.fields is not None
        add = _form(
            ADD_PREFIX,
            refusal.fact_id,
            None,
            _submitted_values(refusal.fields),
            (refusal.fields.segments, refusal.fields.markets),
            options,
            errors=refusal.problems,
            open_=True,
        )
    else:
        values = dict.fromkeys(TEXT_FIELDS, "")
        values["last_checked_on"] = today.isoformat()
        add = _form(ADD_PREFIX, "", None, values, ((), ()), options)
    return {
        "notice": notice,
        "alert": None if refusal is None else refused_notice(refusal),
        "add": add,
        "facts": [_fact_view(history, options, refusal) for history in histories],
    }
