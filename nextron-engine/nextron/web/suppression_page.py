"""The Suppression List panel (FR-50, FR-41, AD-13, AD-16, NFR-7, NFR-36, UX-DR3, UX-DR25,
Story 3.5): add an address or a domain by hand, import a list, check one value, filter the list
and reverse a rule-matched opt-out. Views for ``create_app``'s routes, which stay thin; this
module reads nothing itself and imports from ``nextron.app`` only ``nextron.app.queries.*``.

Every route is ``@personal_data(True)``, so every answer is ``Cache-Control: no-store``:

- ``GET /suppression`` (``suppression_page``) renders ``suppression.html``: the layout around
  ``{% include "suppression_panel.html" %}``. The Settings page links to it ("Suppression List
  page"); it isn't in the navigation, whose sections UX-DR1 fixes. ``list_filters`` reads the
  query string: ``kind`` (a ``KeyKind``), ``source`` (a ``SuppressionSource``), ``status`` (an
  ``EntryStatus``) and ``page`` (1 to 7 ASCII digits worth at least 1), each at most once; a
  blank one is unset. Any other parameter, a repeated one or an unknown value raises
  ``MalformedForm`` (400 "Request refused: malformed form").
- ``POST /suppression/add``, ``/suppression/search`` (each exactly one ``value``,
  ``submitted_value``), ``/suppression/reverse`` (exactly one ``entry_id`` and one ``reason``,
  ``submitted_reversal``) and ``/suppression/import`` (``multipart/form-data``: exactly the file
  ``file``, ``submitted_file``), each besides ``csrf_token``; any other form raises
  ``MalformedForm`` (400). Each answers 200 with ``suppression_panel.html`` alone, re-rendered
  at page 1 with no filter, and a notice (``role="status"``, or ``role="alert"`` for a
  refusal), which HTMX swaps in for the panel (``hx-target="#suppression-panel"
  hx-swap="outerHTML"``). No input is rendered back: every form is empty after a POST, so a
  typed address never reaches the page again.

In order, the panel shows the notice of the last POST, when there is one; an import's invalid
line numbers; the rows the last action concerns, in one table whose caption says which
(``ADDED_CAPTION`` "Entries added": the entries an add wrote; ``MATCHING_CAPTION`` "Matching
entry": the ``active`` entry a search found; ``SAME_OPT_OUT_CAPTION`` "Still on the list from the
same opt-out": the other ``active`` ``reply_rule`` entries of a reversed entry's opt-out, so Mac
can tell them from every other ``reply_rule`` row); the add form; the import form; the search
form; then the list: the filter form (a native GET form: no
``hx-post`` and no ``csrf_field()``), the count line ("Showing <a>–<b> of <total> entries",
"The Suppression List is empty." or "No entries match these filters."), the table and, when
there is more than one page, the pager ("Page <n> of <pages>" with Previous and Next links that
keep the filters, from ``page_href``).

Each row (``EntryRow``) shows the kind, the source, the created date (``YYYY-MM-DD`` UTC), the
status, the first ``HASH_PREFIX_LENGTH`` (12) hex characters of ``value_hash`` and, for a domain
entry only, its ``domain_plain``: never an address, a name, a URL or a reversal reason (the
queries don't even return the reason). Only an ``active`` ``reply_rule`` row, in either table,
has a Reverse form, with a required reason of at most 500 characters; its reason input and its
button carry an ``aria-label`` naming the row ("Reason for reversing the <kind> entry <hash
prefix>", "Reverse the <kind> entry <hash prefix>"). There is no delete control, and no control
turns suppression off (NFR-7).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urlencode

from nextron.app.queries.compliance import ENTRIES_PER_PAGE, EntryListPage, ListedEntry
from nextron.domain.contactability import KeyKind
from nextron.domain.suppression_entry import (
    VOIDABLE_SOURCES,
    EntryStatus,
    SuppressionSource,
)
from nextron.domain.suppression_list import SuppressionRefused
from nextron.web.credentials_page import Notice, Role
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm, UploadForm
from nextron.web.leads_page import Option, Pager, day

PAGE_TEMPLATE: Final = "suppression.html"
PANEL_TEMPLATE: Final = "suppression_panel.html"
LIST_PATH: Final = "/suppression"

VALUE_FIELD: Final = "value"
FILE_FIELD: Final = "file"
ENTRY_FIELD: Final = "entry_id"
REASON_FIELD: Final = "reason"
KIND_FIELD: Final = "kind"
SOURCE_FIELD: Final = "source"
STATUS_FIELD: Final = "status"
PAGE_FIELD: Final = "page"
FILTER_FIELDS: Final = frozenset({KIND_FIELD, SOURCE_FIELD, STATUS_FIELD, PAGE_FIELD})
PAGE_PATTERN: Final = re.compile(r"[0-9]{1,7}")  # ASCII digits only; matched with fullmatch

HASH_PREFIX_LENGTH: Final = 12
NO_ENTRIES: Final = "The Suppression List is empty."
NO_MATCHES: Final = "No entries match these filters."
ADDED_CAPTION: Final = "Entries added"
MATCHING_CAPTION: Final = "Matching entry"
SAME_OPT_OUT_CAPTION: Final = "Still on the list from the same opt-out"


@dataclass(frozen=True, slots=True)
class ListFilters:
    """The list's query: the kind, the source and the status, each ``None`` when unset, and
    the page asked for."""

    kind: KeyKind | None = None
    source: SuppressionSource | None = None
    status: EntryStatus | None = None
    page: int = 1

    @property
    def filtered(self) -> bool:
        """Whether a kind, a source or a status is set."""
        return self.kind is not None or self.source is not None or self.status is not None


@dataclass(frozen=True, slots=True)
class EntryRow:
    """One entry as the panel's table shows it."""

    id: str
    kind: str
    source: str
    created: str
    status: str
    hash_prefix: str
    domain: str | None
    reversible: bool


def _single_values(query_items: Iterable[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in query_items:
        if name not in FILTER_FIELDS:
            raise MalformedForm(WRONG_FIELDS)
        if name in values:
            raise MalformedForm(REPEATED_FIELD)
        values[name] = value
    return values


def _choice[E: StrEnum](enum: type[E], value: str) -> E | None:
    if not value.strip():
        return None
    try:
        return enum(value)
    except ValueError:
        raise MalformedForm(WRONG_FIELDS) from None


def _page(value: str) -> int:
    if not value.strip():
        return 1
    if not PAGE_PATTERN.fullmatch(value) or int(value) < 1:
        raise MalformedForm(WRONG_FIELDS)
    return int(value)


def list_filters(query_items: Iterable[tuple[str, str]]) -> ListFilters:
    """The filters of the query ``query_items`` (name and value pairs, in order);
    ``MalformedForm`` for any other or repeated parameter, an unknown value or a bad
    ``page``."""
    values = _single_values(query_items)
    return ListFilters(
        kind=_choice(KeyKind, values.get(KIND_FIELD, "")),
        source=_choice(SuppressionSource, values.get(SOURCE_FIELD, "")),
        status=_choice(EntryStatus, values.get(STATUS_FIELD, "")),
        page=_page(values.get(PAGE_FIELD, "")),
    )


def page_href(filters: ListFilters, page: int) -> str:
    """The list's URL for ``page`` with the set filters, URL-encoded."""
    params: list[tuple[str, str]] = []
    for name, value in (
        (KIND_FIELD, filters.kind),
        (SOURCE_FIELD, filters.source),
        (STATUS_FIELD, filters.status),
    ):
        if value is not None:
            params.append((name, str(value)))
    params.append((PAGE_FIELD, str(page)))
    return f"{LIST_PATH}?{urlencode(params)}"


def options[E: StrEnum](enum: type[E], selected: E | None) -> tuple[Option, ...]:
    """Every value of ``enum``, in order, by its own text."""
    return tuple(Option(str(member), str(member), member is selected) for member in enum)


def entry_row(entry: ListedEntry) -> EntryRow:
    """``entry`` as a table row: Reverse only on an ``active`` ``reply_rule`` entry."""
    return EntryRow(
        id=entry.id,
        kind=entry.kind,
        source=entry.source,
        created=day(entry.created_at),
        status=entry.status,
        hash_prefix=entry.value_hash[:HASH_PREFIX_LENGTH],
        domain=entry.domain_plain if entry.kind == KeyKind.DOMAIN else None,
        reversible=entry.status == EntryStatus.ACTIVE and entry.source in VOIDABLE_SOURCES,
    )


def count_text(found: EntryListPage, filters: ListFilters) -> str:
    """The list's count line."""
    if found.total == 0:
        return NO_MATCHES if filters.filtered else NO_ENTRIES
    first = (found.page - 1) * ENTRIES_PER_PAGE + 1
    last = first + len(found.entries) - 1
    noun = "entry" if found.total == 1 else "entries"
    return f"Showing {first:,}–{last:,} of {found.total:,} {noun}"


def pager(found: EntryListPage, filters: ListFilters) -> Pager | None:
    """The pager, or ``None`` when everything fits on one page."""
    if found.pages <= 1:
        return None
    return Pager(
        page=found.page,
        pages=found.pages,
        previous=page_href(filters, found.page - 1) if found.page > 1 else None,
        next=page_href(filters, found.page + 1) if found.page < found.pages else None,
    )


def status(text: str) -> Notice:
    """A notice of what a POST did."""
    return Notice(Role.STATUS, text)


def refused(refusal: SuppressionRefused) -> Notice:
    """A notice of why a POST changed nothing."""
    return Notice(Role.ALERT, refusal.message)


def panel_context(
    found: EntryListPage,
    filters: ListFilters | None = None,
    *,
    notice: Notice | None = None,
    featured: Iterable[ListedEntry] = (),
    caption: str | None = None,
    invalid_lines: str | None = None,
) -> dict[str, object]:
    """What ``suppression_panel.html`` renders: the notice, the invalid lines, the featured
    rows under their caption, the filter choices, the count line, the rows and the pager."""
    shown = filters if filters is not None else ListFilters()
    return {
        "notice": notice,
        "invalid_lines": invalid_lines,
        "featured_caption": caption,
        "featured_rows": tuple(entry_row(entry) for entry in featured),
        "kind_options": options(KeyKind, shown.kind),
        "source_options": options(SuppressionSource, shown.source),
        "status_options": options(EntryStatus, shown.status),
        "filtered": shown.filtered,
        "count_text": count_text(found, shown),
        "rows": tuple(entry_row(entry) for entry in found.entries),
        "pager": pager(found, shown),
    }


def _only(fields: FormFields, names: frozenset[str]) -> dict[str, str]:
    if set(fields) - {CSRF_FIELD} != names:
        raise MalformedForm(WRONG_FIELDS)
    if any(len(fields[name]) != 1 for name in names):
        raise MalformedForm(REPEATED_FIELD)
    return {name: fields[name][0] for name in names}


def submitted_value(fields: FormFields) -> str:
    """The one ``value`` the add or search form posts; ``MalformedForm`` otherwise."""
    return _only(fields, frozenset({VALUE_FIELD}))[VALUE_FIELD]


def submitted_reversal(fields: FormFields) -> tuple[str, str]:
    """The ``entry_id`` and ``reason`` a Reverse form posts, each once; ``MalformedForm``
    otherwise."""
    found = _only(fields, frozenset({ENTRY_FIELD, REASON_FIELD}))
    return found[ENTRY_FIELD], found[REASON_FIELD]


def submitted_file(form: UploadForm) -> bytes:
    """The file the import form posts; ``MalformedForm`` for any other field or a missing
    file. The file's name is never read."""
    if not set(form.texts) <= {CSRF_FIELD} or set(form.files) != {FILE_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    return form.files[FILE_FIELD]
