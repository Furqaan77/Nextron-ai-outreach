"""The "contact form only" Absence (FR-10, AD-18, Story 2.9): whether a Lead's crawled pages show
a contact form and no chat widget, online booking link or after-hours line, and how that answer
meets the Lead's earlier Absence Evidence. Standard library and ``nextron.domain`` only.

**The constants.** ``CONTACT_FORM_ONLY`` is the Signal type key (``law_contact_form_only``, the
Law Segment's score-only Attribute) and ``CONTACT_FORM_ONLY_SOUGHT`` the Evidence's ``sought``:
what was looked for and not found.

**``contact_form_only(segment, methods, pages) -> AbsenceReason``.** ``methods`` is the Lead's
``nextron.domain.widgets.ContactMethods`` and ``pages`` a ``CheckedPage(url, text,
tag_managers)`` for each page it checked: ``text`` the page's stored visible text (``None`` once
purged) and ``tag_managers`` the keys of the tag managers it loads (``None`` when the page was
never checked for them). The pages must be exactly ``methods.pages_checked``, each once
(``ValueError`` otherwise; ``TypeError`` for an argument of the wrong type). It returns the
first ``AbsenceReason`` that applies, in this order:

1. ``not_in_segment``: the Segment has no Signal type ``CONTACT_FORM_ONLY`` whose ``evidence``
   includes ``absence``;
2. ``no_pages``: no page was checked;
3. ``chat_found``, ``booking_found``, ``after_hours_found``: a checked page shows that method;
4. ``no_form``: no checked page shows a form;
5. ``page_needs_render``: a checked page's text is ``None`` or ``needs_render`` (under 500
   characters: a render failed or was capped, so a script-built widget may be unseen);
6. ``tag_manager_unknown``: a checked page's ``tag_managers`` is ``None``;
7. ``tag_manager``: a checked page loads a tag manager (it can inject a chat widget);
8. ``holds``: the Absence holds.

**``reconcile_absence(live, holds, pages_checked) -> AbsencePlan(confirm, insert, supersede,
retract)``.** ``live`` is the Lead's live Absence Evidence of this type, as ``LiveAbsence(id,
sought, pages_checked, created_at)``; the plan reads it oldest first (by ``created_at``, then
``id``), whatever order it is given in.

- When the Absence doesn't hold, every live row is retracted.
- When it holds, the oldest row whose ``sought`` is ``CONTACT_FORM_ONLY_SOUGHT`` and whose
  ``pages_checked`` equals ``pages_checked`` is confirmed (``confirm``); failing that, the oldest
  row is superseded by a new row (``supersede``: its id), or, with no live row, a new row is
  inserted (``insert``). Every other live row is retracted.

So at most one of ``confirm``, ``insert`` and ``supersede`` is set, and none when the Absence
doesn't hold. ``pages_checked`` is a tuple of URLs, and not empty when the Absence holds
(``ValueError``).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from nextron.domain.crawl import needs_render
from nextron.domain.segments import EvidenceType, SegmentDef
from nextron.domain.widgets import ContactMethod, ContactMethods

CONTACT_FORM_ONLY: Final = "law_contact_form_only"
CONTACT_FORM_ONLY_SOUGHT: Final = "a chat widget, an online booking link or an after-hours line"


class AbsenceReason(StrEnum):
    """Why the "contact form only" Absence holds or not, in the order the rule checks."""

    NOT_IN_SEGMENT = "not_in_segment"
    NO_PAGES = "no_pages"
    CHAT_FOUND = "chat_found"
    BOOKING_FOUND = "booking_found"
    AFTER_HOURS_FOUND = "after_hours_found"
    NO_FORM = "no_form"
    PAGE_NEEDS_RENDER = "page_needs_render"
    TAG_MANAGER_UNKNOWN = "tag_manager_unknown"
    TAG_MANAGER = "tag_manager"
    HOLDS = "holds"


# A method found on any checked page refutes the Absence, in check order
_REFUTING: Final = (
    (ContactMethod.CHAT, AbsenceReason.CHAT_FOUND),
    (ContactMethod.BOOKING, AbsenceReason.BOOKING_FOUND),
    (ContactMethod.AFTER_HOURS, AbsenceReason.AFTER_HOURS_FOUND),
)


@dataclass(frozen=True, slots=True)
class CheckedPage:
    """One page the Lead's contact methods were read from; its text never shows in the
    ``repr``."""

    url: str
    text: str | None = field(repr=False)
    tag_managers: tuple[str, ...] | None

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url:
            raise TypeError("a checked page's url must be a non-empty str")
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("a checked page's text must be a str or None")
        if self.tag_managers is not None and not (
            isinstance(self.tag_managers, tuple)
            and all(isinstance(key, str) for key in self.tag_managers)
        ):
            raise TypeError("a checked page's tag_managers must be a tuple of str or None")


def _offers_absence(segment: SegmentDef) -> bool:
    return any(
        signal_type.key == CONTACT_FORM_ONLY and EvidenceType.ABSENCE in signal_type.evidence
        for signal_type in segment.signal_types
    )


def _checked(methods: ContactMethods, pages: Iterable[CheckedPage]) -> tuple[CheckedPage, ...]:
    """``pages``, once each URL is checked against ``methods.pages_checked``."""
    found = tuple(pages)
    if not all(isinstance(page, CheckedPage) for page in found):
        raise TypeError("pages must be CheckedPage values")
    urls = [page.url for page in found]
    if len(set(urls)) != len(urls):
        raise ValueError("pages must list each checked page once")
    if set(urls) != set(methods.pages_checked):
        raise ValueError("pages must be exactly the pages the contact methods checked")
    return found


def contact_form_only(
    segment: SegmentDef, methods: ContactMethods, pages: Iterable[CheckedPage]
) -> AbsenceReason:
    """Whether the "contact form only" Absence holds, or why not; see the module docstring."""
    if not isinstance(segment, SegmentDef):
        raise TypeError("segment must be a SegmentDef")
    if not isinstance(methods, ContactMethods):
        raise TypeError("methods must be a ContactMethods")
    checked = _checked(methods, pages)
    if not _offers_absence(segment):
        return AbsenceReason.NOT_IN_SEGMENT
    if not methods.pages_checked:
        return AbsenceReason.NO_PAGES
    for method, reason in _REFUTING:
        if methods.found[method]:
            return reason
    if not methods.found[ContactMethod.FORM]:
        return AbsenceReason.NO_FORM
    if any(page.text is None or needs_render(page.text) for page in checked):
        return AbsenceReason.PAGE_NEEDS_RENDER
    if any(page.tag_managers is None for page in checked):
        return AbsenceReason.TAG_MANAGER_UNKNOWN
    if any(page.tag_managers for page in checked):
        return AbsenceReason.TAG_MANAGER
    return AbsenceReason.HOLDS


# Reconciling ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveAbsence:
    """One live Absence row of the type: its id, what it sought, the pages it checked and when
    it was created."""

    id: str
    sought: str
    pages_checked: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AbsencePlan:
    """What to write: the row to confirm, whether to insert a new row, the row a new row
    supersedes, and the rows to retract."""

    confirm: str | None = None
    insert: bool = False
    supersede: str | None = None
    retract: tuple[str, ...] = ()


def _is_url_tuple(value: object) -> bool:
    return isinstance(value, tuple) and all(isinstance(url, str) for url in value)


def reconcile_absence(
    live: Iterable[LiveAbsence], holds: bool, pages_checked: tuple[str, ...]
) -> AbsencePlan:
    """The plan for the Lead's live Absence rows; see the module docstring."""
    if not isinstance(holds, bool):
        raise TypeError("holds must be a bool")
    if not _is_url_tuple(pages_checked):
        raise TypeError("pages_checked must be a tuple of str")
    rows = tuple(live)
    if not all(isinstance(row, LiveAbsence) for row in rows):
        raise TypeError("live must be LiveAbsence values")
    rows = tuple(sorted(rows, key=lambda row: (row.created_at, row.id)))
    if not holds:
        return AbsencePlan(retract=tuple(row.id for row in rows))
    if not pages_checked:
        raise ValueError("the Absence can't hold with no page checked")
    same = next(
        (
            row
            for row in rows
            if row.sought == CONTACT_FORM_ONLY_SOUGHT and row.pages_checked == pages_checked
        ),
        None,
    )
    if same is not None:
        return AbsencePlan(
            confirm=same.id, retract=tuple(row.id for row in rows if row.id != same.id)
        )
    if rows:
        oldest = rows[0]
        return AbsencePlan(supersede=oldest.id, retract=tuple(row.id for row in rows[1:]))
    return AbsencePlan(insert=True)
