"""A Lead's History tab (Story 9.8; FR-46, FR-51, UX-DR17, AD-13): the view for
``create_app``'s ``GET /leads/{lead_id}/history`` route (``lead_history_page``), which stays thin.
It declares ``@personal_data(True)`` (names and quotes), so it sends ``Cache-Control:
no-store``. This module reads nothing itself and imports from ``nextron.app`` only
``nextron.app.queries.*`` and ``nextron.app.analytics.history``.

**Tabs.** ``lead_tabs(lead_id, current, type)`` is the Lead page's tab bar (``lead_tabs.html``,
under the Lead's name on both pages): "Overview" (``/leads/<id>``) and "History"
(``/leads/<id>/history``, keeping a ``type`` other than ``all``), the shown one marked current.

**The query string.** ``history_query(query_items)`` reads ``type`` (a ``HistoryType`` value:
``all``, ``messages``, ``drafts``, ``stages``, ``evidence`` or ``calls``) and ``page`` (1 to 7
ASCII digits worth at least 1), each at most once and read with surrounding whitespace
stripped; a blank one is the default (``all``, page 1).
Any other parameter, a repeated one, an unknown ``type`` or a bad page raises ``MalformedForm``
(400 "Request refused: malformed form").

**The page** (``lead_history.html``, from ``history_context``): the Lead's name and the tabs; one
filter link per ``HistoryType`` (``TYPE_LABELS``: "All", "Messages", "Drafts", "Stage changes",
"Evidence and Signals", "Calls"), the shown one marked current; "<n> entries" ("1 entry");
then, inside ``hx-disable``, a table newest first with one row per entry: When (``YYYY-MM-DD
HH:MM UTC``), Type (the entry's title), Change (``from → to``, Pipeline Stage labels for a
stage change, humanised codes otherwise), Cause (a Contact's Provenance source by its label,
humanised codes otherwise), By (``lead_history.actor_label``), Contact (the Contact's stored
name, "[erased]" once erased) and Detail (the stored text through ``|plain``; an Evidence
source link through ``safe_link``; "Contacts" linking ``/leads/<id>#lead-contacts`` for a
Contact entry and "Pipeline" linking ``/leads/<id>#lead-pipeline`` for a call entry, always the
survivor's id). With no entry it shows "No history entries of this type.". With more than one
page, a ``Pager`` whose links keep ``type``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlencode

from nextron.app.analytics.history import LeadHistoryView
from nextron.domain.lead_history import (
    CONTACT_ADDED,
    NO_VALUE,
    TYPE_LABELS,
    HistoryEntry,
    HistoryType,
    actor_label,
    code_label,
)
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, MalformedForm
from nextron.web.leads_page import (
    LIST_PATH,
    PAGE_PATTERN,
    SOURCE_LABELS,
    STAGE_LABELS,
    TIME_FORMAT,
    Pager,
)

TEMPLATE: Final = "lead_history.html"
HISTORY_SUFFIX: Final = "/history"
HISTORY_ROUTE: Final = f"{LIST_PATH}/{{lead_id}}{HISTORY_SUFFIX}"
TYPE_FIELD: Final = "type"
PAGE_FIELD: Final = "page"
QUERY_FIELDS: Final = frozenset({TYPE_FIELD, PAGE_FIELD})
OVERVIEW: Final = "Overview"
HISTORY: Final = "History"
EMPTY: Final = "No history entries of this type."
ARROW: Final = " → "
CONTACTS_ANCHOR: Final = "#lead-contacts"
PIPELINE_ANCHOR: Final = "#lead-pipeline"
CONTACTS_LINK: Final = "Contacts"
PIPELINE_LINK: Final = "Pipeline"
SOURCE_LINK: Final = "Source"
LEADS_ENTITY: Final = "leads"
CONTACTS_ENTITY: Final = "contacts"


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    """The History tab's query: the type shown and the page asked for."""

    type: HistoryType = HistoryType.ALL
    page: int = 1


@dataclass(frozen=True, slots=True)
class Tab:
    """One tab of the Lead page's tab bar."""

    label: str
    href: str
    current: bool


@dataclass(frozen=True, slots=True)
class EntryRow:
    """One entry as the History tab shows it."""

    kind: str
    when: str
    title: str
    change: str
    cause: str
    actor: str
    contact: str
    text: str | None
    source: str | None
    detail_href: str | None
    detail_label: str | None


def _single_values(query_items: Iterable[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in query_items:
        if name not in QUERY_FIELDS:
            raise MalformedForm(WRONG_FIELDS)
        if name in values:
            raise MalformedForm(REPEATED_FIELD)
        values[name] = value
    return values


def history_query(query_items: Iterable[tuple[str, str]]) -> HistoryQuery:
    """The query ``query_items`` (name and value pairs, in order); ``MalformedForm`` for any
    other or repeated parameter, an unknown type or a bad page."""
    values = _single_values(query_items)
    kind = values.get(TYPE_FIELD, "").strip()
    page = values.get(PAGE_FIELD, "").strip()
    found = HistoryType.ALL
    if kind:
        try:
            found = HistoryType(kind)
        except ValueError:
            raise MalformedForm(WRONG_FIELDS) from None
    number = 1
    if page:
        if not PAGE_PATTERN.fullmatch(page) or int(page) < 1:
            raise MalformedForm(WRONG_FIELDS)
        number = int(page)
    return HistoryQuery(type=found, page=number)


def lead_href(lead_id: str) -> str:
    """The Lead page's URL."""
    return f"{LIST_PATH}/{lead_id}"


def history_href(lead_id: str, type: HistoryType = HistoryType.ALL, page: int = 1) -> str:
    """The History tab's URL showing ``page`` of ``type``; the defaults are left out."""
    params: list[tuple[str, str]] = []
    if type is not HistoryType.ALL:
        params.append((TYPE_FIELD, type.value))
    if page > 1:
        params.append((PAGE_FIELD, str(page)))
    path = f"{lead_href(lead_id)}{HISTORY_SUFFIX}"
    return f"{path}?{urlencode(params)}" if params else path


def lead_tabs(
    lead_id: str, current: str, type: HistoryType = HistoryType.ALL
) -> tuple[Tab, ...]:
    """The Lead page's tab bar, ``current`` (``OVERVIEW`` or ``HISTORY``) marked."""
    return (
        Tab(OVERVIEW, lead_href(lead_id), current == OVERVIEW),
        Tab(HISTORY, history_href(lead_id, type), current == HISTORY),
    )


def moment(at: datetime) -> str:
    """``at`` as ``YYYY-MM-DD HH:MM UTC``."""
    return format(at.astimezone(UTC), TIME_FORMAT)


def _status(entry: HistoryEntry, code: str | None) -> str:
    if entry.entity == LEADS_ENTITY and code in STAGE_LABELS:
        return STAGE_LABELS[code]
    return code_label(code)


def change_text(entry: HistoryEntry) -> str:
    """``from → to``, or ``to`` alone without a ``from``."""
    to = _status(entry, entry.to_status)
    if entry.from_status is None:
        return to
    return f"{_status(entry, entry.from_status)}{ARROW}{to}"


def cause_text(entry: HistoryEntry) -> str:
    """The cause: a Contact's Provenance source by its label, else the humanised code."""
    if entry.kind == CONTACT_ADDED and entry.cause in SOURCE_LABELS:
        return SOURCE_LABELS[entry.cause]
    return code_label(entry.cause)


def _detail(entry: HistoryEntry, lead_id: str) -> tuple[str | None, str | None]:
    if entry.kind == CONTACT_ADDED or entry.entity == CONTACTS_ENTITY:
        return f"{lead_href(lead_id)}{CONTACTS_ANCHOR}", CONTACTS_LINK
    if entry.type is HistoryType.CALLS:
        return f"{lead_href(lead_id)}{PIPELINE_ANCHOR}", PIPELINE_LINK
    return None, None


def entry_row(
    entry: HistoryEntry, lead_id: str, names: Mapping[str, str | None]
) -> EntryRow:
    """One entry as the History tab shows it; links point at the survivor ``lead_id``."""
    contact = ""
    if entry.contact_id is not None:
        contact = names.get(entry.contact_id) or NO_VALUE
    href, label = _detail(entry, lead_id)
    return EntryRow(
        kind=entry.kind,
        when=moment(entry.at),
        title=entry.title,
        change=change_text(entry),
        cause=cause_text(entry),
        actor=actor_label(entry.actor),
        contact=contact,
        text=entry.text,
        source=entry.link,
        detail_href=href,
        detail_label=label,
    )


def count_text(total: int) -> str:
    """The entry count line."""
    return f"{total:,} {'entry' if total == 1 else 'entries'}"


def history_pager(view: LeadHistoryView) -> Pager | None:
    """The pager, or ``None`` on one page; its links keep ``type``."""
    shown = view.page
    if shown.pages <= 1:
        return None
    return Pager(
        page=shown.page,
        pages=shown.pages,
        previous=(
            history_href(view.lead_id, shown.type, shown.page - 1) if shown.page > 1 else None
        ),
        next=(
            history_href(view.lead_id, shown.type, shown.page + 1)
            if shown.page < shown.pages
            else None
        ),
    )


def history_context(view: LeadHistoryView, lead_name: str) -> dict[str, object]:
    """What ``lead_history.html`` renders."""
    shown = view.page
    return {
        "lead_name": lead_name,
        "tabs": lead_tabs(view.lead_id, HISTORY, shown.type),
        "filters": tuple(
            Tab(TYPE_LABELS[kind], history_href(view.lead_id, kind), kind is shown.type)
            for kind in HistoryType
        ),
        "count_text": count_text(shown.total),
        "rows": tuple(entry_row(entry, view.lead_id, view.contact_names) for entry in shown.entries),
        "empty": EMPTY,
        "pager": history_pager(view),
        "source_link": SOURCE_LINK,
    }
