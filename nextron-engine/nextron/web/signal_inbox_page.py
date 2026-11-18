"""The Signal Inbox page (FR-8, UX-DR19, AD-13, AD-18; Story 2.16): views for ``create_app``'s
three routes, which stay thin.

- ``GET /signals`` (``signal_inbox_page``, ``@personal_data(True)``: pasted text) renders
  ``signal_inbox.html``, which extends ``base.html`` and includes ``signal_inbox_panel.html``.
- ``POST /signals`` (``signal_inbox_submit``, ``@personal_data(True)``) takes the form through
  ``nextron.web.forms.form_fields``, checks it with ``submitted_rows`` and
  ``nextron.domain.signal_inbox_item.check_items``, stores the items with
  ``SignalInbox.submit`` in its own unit of work and answers 200 with the panel alone, which
  HTMX swaps in for the page's ``#signal-inbox`` block.
- ``POST /signals/{item_id}/reprocess`` (``signal_inbox_reprocess``, ``@personal_data(True)``)
  takes ``lead``, ``website`` and ``segment`` (``submitted_reprocess``), runs
  ``SignalInbox.reprocess`` and answers the panel with the outcome's notice
  (``REPROCESS_NOTICES``).

**The form** (``method="post"``, ``hx-post``, ``{{ csrf_field() }}``) has ``FORM_SLOTS`` (5)
fieldsets of ``url_<n>``, ``text_<n>``, ``author_<n>`` and ``company_<n>``. ``submitted_rows``
allows only those fields and ``csrf_token``, each at most once (``MalformedForm`` otherwise);
a refused submission (``ItemsRefused``) shows each reason by its field, with what was typed
kept, and stores nothing.

**The list** shows the newest items (``queries.discovery.signal_items``): status, submission
time, the pasted text (through ``untrusted_block``: escaped, inside ``hx-disable``), the URL
(as escaped text, never a link the engine follows) with ``NEVER_FETCHED_NOTE`` when it is on the
FR-49 denylist, a link to the resulting Lead, and for a flagged item what is missing
(``FLAG_LABELS``) and the reprocess form (an existing Lead's id or Lead page link, or a website
and a Segment).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from typing import Final, Literal

from nextron.app.discovery.signal_inbox import ReprocessChoice, ReprocessOutcome
from nextron.app.queries.discovery import StoredItem
from nextron.app.queries.setup import SeededSegment
from nextron.domain.signal_inbox_item import (
    FIELDS,
    FLAG_LABELS,
    FORM_SLOTS,
    NEVER_FETCHED_NOTE,
    FlagReason,
    ItemStatus,
    url_never_fetched,
)
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "signal_inbox.html"
PANEL_TEMPLATE: Final = "signal_inbox_panel.html"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
LEAD_FIELD: Final = "lead"
WEBSITE_FIELD: Final = "website"
SEGMENT_FIELD: Final = "segment"
SUBMIT_FIELDS: Final = frozenset(
    f"{name}_{number}" for name in FIELDS for number in range(1, FORM_SLOTS + 1)
)
REPROCESS_FIELDS: Final = frozenset({LEAD_FIELD, WEBSITE_FIELD, SEGMENT_FIELD})
STATUS_LABELS: Final = {
    ItemStatus.PENDING: "Pending",
    ItemStatus.PROCESSED: "Processed",
    ItemStatus.FLAGGED: "Flagged",
}

type Role = Literal["status", "alert"]


@dataclass(frozen=True, slots=True)
class Notice:
    """What a POST did: ``status`` when it worked, ``alert`` when nothing changed."""

    role: Role
    text: str


SUBMITTED: Final = "Submitted. Each item is processed now; refresh to see its result."
REPROCESS_NOTICES: Final[Mapping[ReprocessOutcome, Notice]] = {
    ReprocessOutcome.PROCESSED: Notice("status", "Reprocessed: the item is linked to its Lead."),
    ReprocessOutcome.UNKNOWN_ITEM: Notice("alert", "That item no longer exists."),
    ReprocessOutcome.NOT_FLAGGED: Notice("alert", "Only a flagged item can be reprocessed."),
    ReprocessOutcome.INCOMPLETE: Notice(
        "alert", "Enter either an existing Lead, or a website and a Segment."
    ),
    ReprocessOutcome.UNKNOWN_LEAD: Notice("alert", "No Lead has that id or link."),
    ReprocessOutcome.WEBSITE_REFUSED: Notice(
        "alert",
        "That website can't be a Lead's: enter the organization's own domain, not a social, "
        "job or shared platform.",
    ),
    ReprocessOutcome.UNKNOWN_SEGMENT: Notice("alert", "Choose one of the seeded Segments."),
    ReprocessOutcome.SUPPRESSED: Notice(
        "alert", "Organization is on the Suppression List: no Lead was created."
    ),
}


@dataclass(frozen=True, slots=True)
class SegmentOption:
    """One Segment choice: its key (the value) and its name (the label)."""

    key: str
    name: str


@dataclass(frozen=True, slots=True)
class ItemRow:
    """One item as the list shows it."""

    id: str
    status: str
    status_label: str
    submitted: str
    url: str = field(repr=False)
    never_fetched: bool
    text: str = field(repr=False)
    lead_id: str | None
    missing: str | None


def submitted_rows(fields: FormFields) -> list[dict[str, str]]:
    """The form's ``FORM_SLOTS`` fieldsets, in order; ``MalformedForm`` for any other field or
    one given twice."""
    names = set(fields) - {CSRF_FIELD}
    if not names <= SUBMIT_FIELDS:
        raise MalformedForm(WRONG_FIELDS)
    if any(len(fields[name]) != 1 for name in names):
        raise MalformedForm(REPEATED_FIELD)
    return [
        {name: fields.get(f"{name}_{number}", [""])[0] for name in FIELDS}
        for number in range(1, FORM_SLOTS + 1)
    ]


def submitted_reprocess(fields: FormFields) -> ReprocessChoice:
    """The reprocess form's three fields, each once; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != REPROCESS_FIELDS:
        raise MalformedForm(WRONG_FIELDS)
    if any(len(fields[name]) != 1 for name in REPROCESS_FIELDS):
        raise MalformedForm(REPEATED_FIELD)
    return ReprocessChoice(
        lead=fields[LEAD_FIELD][0] or None,
        website=fields[WEBSITE_FIELD][0] or None,
        segment=fields[SEGMENT_FIELD][0] or None,
    )


def segment_options(segments: Iterable[SeededSegment]) -> tuple[SegmentOption, ...]:
    """Each stored Segment as a choice, by name."""
    options = (SegmentOption(segment.key, segment.definition.name) for segment in segments)
    return tuple(sorted(options, key=lambda option: (option.name.casefold(), option.key)))


def item_row(item: StoredItem) -> ItemRow:
    status = ItemStatus(item.status)
    missing = None
    if status is ItemStatus.FLAGGED and item.flag_reason is not None:
        missing = FLAG_LABELS[FlagReason(item.flag_reason)]
    return ItemRow(
        id=item.id,
        status=str(status),
        status_label=STATUS_LABELS[status],
        submitted=item.submitted_at.astimezone(UTC).strftime(TIME_FORMAT),
        url=item.url,
        never_fetched=url_never_fetched(item.url),
        text=item.text,
        lead_id=item.lead_id,
        missing=missing,
    )


def panel_context(
    items: Iterable[StoredItem],
    segments: Iterable[SeededSegment],
    *,
    rows: list[dict[str, str]] | None = None,
    errors: Mapping[str, str] | None = None,
    notice: Notice | None = None,
) -> dict[str, object]:
    """What ``signal_inbox_panel.html`` renders: the form's fieldsets as typed (blank by
    default), each field's error, the notice, the items and the Segment choices."""
    typed = rows if rows is not None else [dict.fromkeys(FIELDS, "") for _ in range(FORM_SLOTS)]
    return {
        "slots": [(number, row) for number, row in enumerate(typed, start=1)],
        "errors": dict(errors or {}),
        "notice": notice,
        "items": [item_row(item) for item in items],
        "segments": segment_options(segments),
        "never_fetched_note": NEVER_FETCHED_NOTE,
    }
