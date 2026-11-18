"""A Lead's complete history (Story 9.8; FR-46, FR-51, UX-DR17): the timeline's categories,
labels, ordering and paging. Standard library only, plus ``nextron.domain``.

**Entries.** A ``HistoryEntry`` is one thing that happened: its time ``at``, a stable
``sort_id`` (an ``events`` row's id, or ``<row id>:<kind>`` for a row-derived entry), its
``type`` (a ``HistoryType`` category, or ``None`` when it shows only under ``all``), its
``kind`` (``EVENT`` for an ``events`` row, else one of ``ROW_KINDS``), the ``entity`` table and
``entity_id``, the ``from_status``/``to_status`` codes, the ``cause`` code, the raw ``actor``,
a ``title`` (the entity's label for an event), the ``contact_id`` it concerns (or ``None``), a
stored ``text`` (an Evidence quote, a Signal type, a suppression entry's kind and source) and a
``link`` (an ``http``/``https`` URL, or ``None``).

**Categories** (``history_type(entity, to_status)``): ``ENTITY_TYPES`` maps a table to its
``HistoryType``: messages (``outbound_messages``, ``threads``, ``mail_messages``,
``replies``, ``manual_replies``, ``bounces``, ``touches``, ``linkedin_tasks``), drafts
(``drafts``, ``draft_revisions``, ``approvals``), evidence (``evidence``, ``signals``,
``job_postings``) and calls (``call_outcomes``, ``call_preps``). A ``leads`` row is a stage
change when its ``to_status`` is a ``PipelineStage``. Anything else (Contacts, suppression,
data subject requests, enrollments, other ``leads`` rows, a table no story named yet) is
``None``: it shows only under ``HistoryType.ALL``.

**Labels.** ``entity_label(entity)`` is ``ENTITY_LABELS[entity]``, or the table name
humanised (``code_label``: underscores to spaces, first letter upper case). ``actor_label``:
``operator`` is "Operator", ``sender`` "Sender", ``seed`` "Seed", ``system:<owner>`` "Engine
(<owner>)", ``ai:<route>@<version>`` "AI (<route>)", ``rule`` "Engine (rule)"; anything else is
humanised; ``None`` is "—".

**Signal expiry.** ``signal_expired_at(signal_class, tier, event_date, first_seen_at)`` is the
first moment ``signals.is_live`` says the Signal is no longer live: midnight UTC of its age
anchor (the event date, else the UTC date it was first seen) plus the first age in days that
isn't live, never before ``first_seen_at``; ``None`` when it stays live through
``EXPIRY_HORIZON_DAYS``. A class or Tier the domain refuses counts as not live from age 0, as on
the Lead page.

**Paging.** ``page_of(entries, type, page)`` sorts newest first (``at`` descending, then
``sort_id`` descending), keeps the entries of ``type`` (all of them for ``ALL``) and returns a
``HistoryPage`` of at most ``PAGE_SIZE`` (100) entries; a page past the last shows the last.
``TypeError`` unless ``type`` is a ``HistoryType`` and ``page`` an ``int`` (not a ``bool``);
``ValueError`` for a page below 1.
"""

import builtins
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from math import ceil
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit

from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import SignalClass
from nextron.domain.signals import EVENT_LIFE_DAYS, is_live

PAGE_SIZE: Final = 100
NO_VALUE: Final = "—"
EXPIRY_HORIZON_DAYS: Final = EVENT_LIFE_DAYS + 1  # every Event is expired by then
LEADS_TABLE: Final = "leads"

# kinds: an events row, or an entry derived from a row's own columns
EVENT: Final = "event"
CONTACT_ADDED: Final = "contact_added"
EVIDENCE_ADDED: Final = "evidence_added"
EVIDENCE_SUPERSEDED: Final = "evidence_superseded"
EVIDENCE_RETRACTED: Final = "evidence_retracted"
SIGNAL_ADDED: Final = "signal_added"
SIGNAL_EXPIRED: Final = "signal_expired"
SIGNAL_RETRACTED: Final = "signal_retracted"
ROW_KINDS: Final = (
    CONTACT_ADDED,
    EVIDENCE_ADDED,
    EVIDENCE_SUPERSEDED,
    EVIDENCE_RETRACTED,
    SIGNAL_ADDED,
    SIGNAL_EXPIRED,
    SIGNAL_RETRACTED,
)


class HistoryType(StrEnum):
    ALL = "all"
    MESSAGES = "messages"
    DRAFTS = "drafts"
    STAGES = "stages"
    EVIDENCE = "evidence"
    CALLS = "calls"


TYPE_LABELS: Final[Mapping[HistoryType, str]] = MappingProxyType(
    {
        HistoryType.ALL: "All",
        HistoryType.MESSAGES: "Messages",
        HistoryType.DRAFTS: "Drafts",
        HistoryType.STAGES: "Stage changes",
        HistoryType.EVIDENCE: "Evidence and Signals",
        HistoryType.CALLS: "Calls",
    }
)

ENTITY_TYPES: Final[Mapping[str, HistoryType]] = MappingProxyType(
    {
        **dict.fromkeys(
            (
                "outbound_messages",
                "threads",
                "mail_messages",
                "replies",
                "manual_replies",
                "bounces",
                "touches",
                "linkedin_tasks",
            ),
            HistoryType.MESSAGES,
        ),
        **dict.fromkeys(("drafts", "draft_revisions", "approvals"), HistoryType.DRAFTS),
        **dict.fromkeys(("evidence", "signals", "job_postings"), HistoryType.EVIDENCE),
        **dict.fromkeys(("call_outcomes", "call_preps"), HistoryType.CALLS),
    }
)

ENTITY_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "leads": "Lead",
        "contacts": "Contact",
        "call_outcomes": "Call",
        "call_preps": "Call Prep Sheet",
        "suppression_entries": "Suppression entry",
        "data_subject_requests": "Data subject request",
        "evidence": "Evidence",
        "signals": "Signal",
        "job_postings": "Job posting",
        "outbound_messages": "Message",
        "threads": "Thread",
        "mail_messages": "Mail message",
        "replies": "Reply",
        "manual_replies": "Manual Reply",
        "bounces": "Bounce",
        "touches": "Touch",
        "linkedin_tasks": "LinkedIn task",
        "drafts": "Draft",
        "draft_revisions": "Draft revision",
        "approvals": "Approval",
        "enrollments": "Enrollment",
    }
)

ROW_TITLES: Final[Mapping[str, str]] = MappingProxyType(
    {
        CONTACT_ADDED: "Contact added",
        EVIDENCE_ADDED: "Evidence added",
        EVIDENCE_SUPERSEDED: "Evidence superseded",
        EVIDENCE_RETRACTED: "Evidence retracted",
        SIGNAL_ADDED: "Signal added",
        SIGNAL_EXPIRED: "Signal expired",
        SIGNAL_RETRACTED: "Signal retracted",
    }
)

FIXED_ACTORS: Final[Mapping[str, str]] = MappingProxyType(
    {"operator": "Operator", "sender": "Sender", "seed": "Seed", "rule": "Engine (rule)"}
)
STAGE_CODES: Final = frozenset(stage.value for stage in PipelineStage)
LINK_SCHEMES: Final = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One entry of a Lead's timeline; see the module docstring."""

    at: datetime
    sort_id: str
    type: HistoryType | None
    kind: str
    entity: str
    entity_id: str
    from_status: str | None
    to_status: str | None
    cause: str | None
    actor: str | None
    title: str
    contact_id: str | None = None
    text: str | None = None
    link: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """One page of a timeline: its entries, how many match ``type``, the page shown (from 1)
    and the number of pages (at least 1)."""

    entries: tuple[HistoryEntry, ...]
    type: HistoryType
    total: int
    page: int
    pages: int


def code_label(code: str | None) -> str:
    """A stored code as words: underscores to spaces, the first letter upper case; "—" for
    ``None`` or an empty code."""
    if not code:
        return NO_VALUE
    words = code.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else NO_VALUE


def entity_label(entity: str) -> str:
    """The label of the table ``entity``; its humanised name when no story named it."""
    return ENTITY_LABELS.get(entity) or code_label(entity)


def actor_label(actor: str | None) -> str:
    """Who acted; see the module docstring."""
    if not actor:
        return NO_VALUE
    if actor in FIXED_ACTORS:
        return FIXED_ACTORS[actor]
    if actor.startswith("system:"):
        return f"Engine ({actor.removeprefix('system:')})"
    if actor.startswith("ai:"):
        return f"AI ({actor.removeprefix('ai:').partition('@')[0]})"
    return code_label(actor)


def history_type(entity: str, to_status: str | None) -> HistoryType | None:
    """The category of a change of a row of ``entity`` to ``to_status``; ``None`` shows only
    under ``ALL``."""
    if entity == LEADS_TABLE:
        return HistoryType.STAGES if to_status in STAGE_CODES else None
    return ENTITY_TYPES.get(entity)


def http_link(url: str | None) -> str | None:
    """``url`` when it is an ``http`` or ``https`` URL with a host, else ``None``."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme.lower() in LINK_SCHEMES and parts.netloc else None


def signal_expired_at(
    signal_class: str, tier: int, event_date: date | None, first_seen_at: datetime
) -> datetime | None:
    """The first moment the Signal is no longer live; see the module docstring."""
    anchor = event_date if event_date is not None else first_seen_at.astimezone(UTC).date()
    for age in range(EXPIRY_HORIZON_DAYS + 1):
        try:
            live = is_live(SignalClass(signal_class), tier, age)
        except (TypeError, ValueError):  # a stored class or Tier the domain no longer knows
            live = False
        if not live:
            moment = datetime.combine(anchor + timedelta(days=age), time(), tzinfo=UTC)
            return max(moment, first_seen_at)
    return None


def _sort_key(entry: HistoryEntry) -> tuple[datetime, str]:
    return (entry.at, entry.sort_id)


def page_of(entries: Iterable[HistoryEntry], type: HistoryType, page: int) -> HistoryPage:
    """One page of ``entries`` of ``type``, newest first; see the module docstring."""
    if not isinstance(type, HistoryType):
        raise TypeError(f"type must be a HistoryType, not {builtins.type(type).__name__}")
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError(f"page must be an int, not {builtins.type(page).__name__}")
    if page < 1:
        raise ValueError("page must be at least 1")
    kept = [entry for entry in entries if type is HistoryType.ALL or entry.type is type]
    kept.sort(key=_sort_key, reverse=True)
    total = len(kept)
    pages = max(1, ceil(total / PAGE_SIZE))
    shown = min(page, pages)
    start = (shown - 1) * PAGE_SIZE
    return HistoryPage(
        entries=tuple(kept[start : start + PAGE_SIZE]),
        type=type,
        total=total,
        page=shown,
        pages=pages,
    )

