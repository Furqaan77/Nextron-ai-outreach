"""The Leads list and the Lead page (FR-51, FR-13, UX-DR17, UX-DR18, AD-3, AD-13, AD-18): views
for ``create_app``'s two routes, which stay thin. Both are GETs declaring
``@personal_data(True)`` (a Lead's name can be a sole trader's name, and a Contact is a person),
so both send ``Cache-Control: no-store``. This module reads and evaluates nothing itself, and
imports from ``nextron.app`` only ``nextron.app.queries.*``.

**The Leads list** (``GET /leads``, route ``pipeline_page``, the navigation's Leads/Pipeline
section) renders ``leads.html`` from ``queries.discovery.lead_list`` and
``queries.setup.segments``:

- ``list_filters(query_items, segments)`` reads the query string into ``ListFilters``: ``stage``
  (a ``PipelineStage`` value), ``segment`` (a Segment key), ``q`` and ``page``, each at most
  once. A blank one (empty or whitespace only) is unset. In ``q``, whitespace characters (tabs,
  line breaks and the like) count as spaces: it is stripped and each run collapses to one
  space. It must be at most 200 characters with no other control character. ``page`` is 1 to 7
  ASCII digits worth at least 1. Any other parameter, a repeated one, an unknown stage or
  Segment key, or a bad ``q`` or ``page`` raises ``MalformedForm`` (400 "Request refused:
  malformed form"), so a hand-edited URL never shows an unfiltered list that looks filtered.
- The filter form is ``method="get" action="/leads"``, with no ``hx-post`` and no
  ``csrf_field()`` (a token must never reach a URL): a stage select ("All stages", then each
  stage in machine order by its label), a Segment select ("All Segments", then each by name,
  its value the key), the ``q`` text input ("Name or website", at most 200 characters) and a
  Filter button; "Clear filters" links to ``/leads`` while a stage, Segment or ``q`` is set.
- ``list_context`` gives the count line ("Showing <a>–<b> of <total> Leads", "No Leads yet."
  or "No Leads match these filters."), one ``LeadRow`` per Lead (its name linked to its page,
  the website domain or "—", the Segment's name or "Unknown Segment", the country or "—", the
  stage's label and the created date, ``YYYY-MM-DD`` UTC) and, when there is more than one
  page, a ``Pager`` ("Page <n> of <pages>" with Previous and Next links that keep the set
  filters, from ``page_href``).

**The Lead page** (``GET /leads/{lead_id}``, route ``lead_page``) renders ``lead.html`` from
``lead_context``: a ``LeadHeader`` (the name, the website as a ``safe_link`` to
``https://<domain>/`` or "No website on record", the Segment, the country, the Pipeline Stage's
label and the created date) and one ``ContactRow`` per Contact, oldest first (name, role,
email, Address Class, LinkedIn URL, Primary ``Yes`` or ``No``; a blank shows "—"), each with
its live Eligibility (Story 3.6: an ``EligibilityRow`` from the ``Decision`` the route got from
``compliance.evaluate_eligibility`` on this render: "Eligible: <basis>" or "Not eligible", then
a ``ReasonRow`` per reason, its ``code`` the item's ``data-reason``; a Contact without a
Decision raises ``KeyError``) and its ``ProvenanceRow`` records (the record, "Contact" or
"Address" and its address; the source's label; the detail; the retrieval date; the Address
Class or "—"). A merged Lead's URL
answers 303 to its survivor's; a text that isn't a ULID, an unknown id or a broken merge chain
answers 404 with ``lead_missing.html``, which never echoes the id.

**Signals and Evidence (Story 2.9).** ``lead_context`` also takes the Lead's
``queries.research.LeadSignalsView`` (``LeadSignals.view``, read at one time):

- the header's "HQ timezone" is the Lead's ``hq_tz``, or "—";
- one ``SignalRow`` per current Signal, first seen first: its type's name (from the Segment,
  else the key), "Event" or "Attribute", "Tier <n>", its event date or "first seen <date>", its
  age ("<n> days", "1 day"), "Live" or "Expired", and "Score only" for a score-only Signal
  (else empty); "No Signals yet." when there are none;
- one ``EvidenceRow`` per live Evidence row, oldest first: its type ("Quote", "Absence",
  "Count"), its quote, or what was sought ("Sought: <what>") or counted ("Counted: <what>:
  <n>") with the pages checked (each a ``safe_link``), what it supports (the names of the
  Signals it supports, joined with "; ", else its fact as "<label>: <value or number>", else
  "—"), its source URL (a ``safe_link``, or "—"), its retrieval time (``YYYY-MM-DD HH:MM UTC``)
  and "AI-extracted" when ``extracted_by`` starts with ``ai:`` (else "—"); "No Evidence yet."
  when there is none.

Neither section holds an HTMX action.

**Decision-makers and the Primary Contact (Story 2.10).** The Contacts section is its own panel,
``lead_contacts.html`` (``<section id="lead-contacts">``), rendered inside ``lead.html`` and
alone as the answer to ``POST /leads/{lead_id}/primary``, which HTMX swaps in for the whole
panel. ``contacts_context(lead_id, contacts, provenance, decisions, no_decision_maker,
notice=None)`` gives (``decisions``: each Contact's live Eligibility ``Decision``, Story 3.6, so
the panel the POST answers with shows it too):

- the Primary line: "Primary Contact: <name> (chosen by you)" for the operator's Primary,
  "Primary Contact: <name> (chosen by the engine)" for the engine's (the name, else the email,
  else "—"); "Primary Contact: no decision-maker found" when the Lead has no Primary and
  ``no_decision_maker`` is true; otherwise no line;
- each ``ContactRow`` with its ``contact_id`` and, on every row but the operator's Primary and
  a Contact whose address is Invalid (always refused), a "Make primary" form: ``method="post"``, ``hx-post`` to ``/leads/{lead_id}/primary``,
  ``hx-target="#lead-contacts"``, ``hx-swap="outerHTML"``, ``csrf_field()`` and a hidden
  ``contact_id``;
- the POST's ``notice``: ``primary_set()`` ("Primary Contact set.", role ``status``) or
  ``primary_refused(refusal)`` (role ``alert``): "This Lead was merged into another Lead.",
  "That Contact isn't on this Lead." or "That Contact's address is Invalid."
  (``submitted_contact(fields)`` reads the form: exactly ``contact_id``, once, beside the CSRF
  token; anything else raises ``MalformedForm``).

Every untrusted text (the Primary line's name, each Contact and Provenance cell) sits in an
element carrying ``hx-disable``; the forms sit outside them. A person Evidence row (its
``person_contact_id`` set) reads "Contact: <name>" under Supports.

``STAGE_LABELS`` and ``SOURCE_LABELS`` name every stage and Provenance source,
``CLASS_LABELS``, ``EVIDENCE_TYPE_LABELS`` and ``FACT_LABELS`` every Signal class, Evidence type
and fact kind; a stored value none knows renders as its own text. Every Lead, Contact,
Provenance, Signal and Evidence text renders through ``|plain`` or ``safe_link`` inside an
element carrying ``hx-disable``.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final
from urllib.parse import urlencode

from nextron.app.queries.discovery import LEADS_PER_PAGE, LeadListPage, ListedLead, StoredLead
from nextron.app.queries.research import (
    EvidenceSupport,
    LeadSignalsView,
    SignalStatus,
    StoredContact,
    StoredProvenance,
)
from nextron.app.queries.setup import SeededSegment
from nextron.domain.contacts import (
    INVALID_ADDRESS,
    PrimaryRefusal,
    PrimaryRefused,
    PrimarySetBy,
    ProvenanceSource,
    ProvenanceSubject,
)
from nextron.domain.eligibility import Decision
from nextron.domain.extraction import FactKind
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import EvidenceType, SignalClass
from nextron.web.credentials_page import Notice, Role
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

LIST_TEMPLATE: Final = "leads.html"
LEAD_TEMPLATE: Final = "lead.html"
MISSING_TEMPLATE: Final = "lead_missing.html"
CONTACTS_TEMPLATE: Final = "lead_contacts.html"
CONTACT_FIELD: Final = "contact_id"
PRIMARY_SET: Final = "Primary Contact set."
PRIMARY_PREFIX: Final = "Primary Contact: "
NO_DECISION_MAKER: Final = "Primary Contact: no decision-maker found"
CHOSEN_BY: Final[Mapping[str, str]] = MappingProxyType(
    {PrimarySetBy.OPERATOR: "(chosen by you)", PrimarySetBy.SYSTEM: "(chosen by the engine)"}
)
REFUSALS: Final[Mapping[str, str]] = MappingProxyType(
    {
        PrimaryRefusal.UNKNOWN_LEAD: "That Lead doesn't exist.",
        PrimaryRefusal.MERGED_LEAD: "This Lead was merged into another Lead.",
        PrimaryRefusal.NOT_ON_LEAD: "That Contact isn't on this Lead.",
        PrimaryRefusal.ADDRESS_INVALID: "That Contact's address is Invalid.",
    }
)
LIST_PATH: Final = "/leads"

STAGE_FIELD: Final = "stage"
SEGMENT_FIELD: Final = "segment"
SEARCH_FIELD: Final = "q"
PAGE_FIELD: Final = "page"
FILTER_FIELDS: Final = frozenset({STAGE_FIELD, SEGMENT_FIELD, SEARCH_FIELD, PAGE_FIELD})
MAX_SEARCH_LENGTH: Final = 200
PAGE_PATTERN: Final = re.compile(r"[0-9]{1,7}")  # ASCII digits only; matched with fullmatch

NO_VALUE: Final = "—"
UNKNOWN_SEGMENT: Final = "Unknown Segment"
NO_LEADS: Final = "No Leads yet."
NO_MATCHES: Final = "No Leads match these filters."
DATE_FORMAT: Final = "%Y-%m-%d"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
YES: Final = "Yes"
NO: Final = "No"
LIVE: Final = "Live"
EXPIRED: Final = "Expired"
SCORE_ONLY: Final = "Score only"
AI_EXTRACTED: Final = "AI-extracted"
AI_ACTOR_PREFIX: Final = "ai:"
SUPPORTS_SEPARATOR: Final = "; "
ELIGIBLE: Final = "Eligible: {basis}"
NOT_ELIGIBLE: Final = "Not eligible"
CONTACT_SUPPORT: Final = "Contact: "

STAGE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        PipelineStage.NEW: "New",
        PipelineStage.QUEUED: "Queued",
        PipelineStage.CONTACTED: "Contacted",
        PipelineStage.REPLIED: "Replied",
        PipelineStage.INTERESTED: "Interested",
        PipelineStage.CALL_BOOKED: "Call booked",
        PipelineStage.CALL_HELD: "Call held",
        PipelineStage.AUDIT_PROPOSED: "Audit proposed",
        PipelineStage.AUDIT_WON: "Audit won",
        PipelineStage.BUILD_PROPOSED: "Build proposed",
        PipelineStage.WON: "Won",
        PipelineStage.NOT_NOW: "Not now",
        PipelineStage.LOST: "Lost",
        PipelineStage.DISQUALIFIED: "Disqualified",
    }
)
SOURCE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        ProvenanceSource.CSV: "CSV import",
        ProvenanceSource.WEBSITE: "Website",
        ProvenanceSource.SIGNAL_INBOX: "Signal Inbox (manual)",
        ProvenanceSource.REFERRAL: "Referral",
        ProvenanceSource.REPLIER: "Replier",
        ProvenanceSource.OPERATOR: "Entered by Mac",
        ProvenanceSource.PATTERN: "Email pattern",
        ProvenanceSource.FINDER: "Email finder",
        ProvenanceSource.ELIGIBILITY_BASIS: "Eligibility basis",
    }
)
RECORD_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {ProvenanceSubject.CONTACT: "Contact", ProvenanceSubject.ADDRESS: "Address"}
)
CLASS_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {SignalClass.EVENT: "Event", SignalClass.ATTRIBUTE: "Attribute"}
)
EVIDENCE_TYPE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {EvidenceType.QUOTE: "Quote", EvidenceType.ABSENCE: "Absence", EvidenceType.COUNT: "Count"}
)
FACT_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        FactKind.WHAT_WE_DO: "What it does",
        FactKind.LOCATION: "Location",
        FactKind.TECHNOLOGY: "Technology",
        FactKind.LAWYERS_AT_OFFICE: "Lawyers at the office",
        FactKind.LAWYERS_FIRM_WIDE: "Lawyers firm-wide",
        FactKind.STAFF: "Staff",
        FactKind.GROUP_STAFF: "Group staff",
        FactKind.LOCATIONS: "Locations",
    }
)


# The Leads list ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListFilters:
    """The Leads list's query: the stage, the Segment (its key and id), the search text, each
    ``None`` when unset, and the page asked for."""

    stage: PipelineStage | None = None
    segment: str | None = None
    segment_id: str | None = None
    search: str | None = None
    page: int = 1

    @property
    def filtered(self) -> bool:
        """Whether a stage, a Segment or a search text is set."""
        return self.stage is not None or self.segment is not None or self.search is not None


@dataclass(frozen=True, slots=True)
class Option:
    """One choice of a filter select: its value, its label and whether it is selected."""

    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class LeadRow:
    """One Lead as the list shows it."""

    href: str
    name: str
    website: str | None
    segment: str
    country: str | None
    stage: str
    created: str


@dataclass(frozen=True, slots=True)
class Pager:
    """The page shown, the number of pages, and the Previous and Next links (``None`` at
    either end)."""

    page: int
    pages: int
    previous: str | None
    next: str | None


def _single_values(query_items: Iterable[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in query_items:
        if name not in FILTER_FIELDS:
            raise MalformedForm(WRONG_FIELDS)
        if name in values:
            raise MalformedForm(REPEATED_FIELD)
        values[name] = value
    return values


def _stage(value: str) -> PipelineStage | None:
    if not value.strip():
        return None
    try:
        return PipelineStage(value)
    except ValueError:
        raise MalformedForm(WRONG_FIELDS) from None


def _search(value: str) -> str | None:
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > MAX_SEARCH_LENGTH or any(
        unicodedata.category(character) == "Cc" for character in text
    ):
        raise MalformedForm(WRONG_FIELDS)
    return text


def _page(value: str) -> int:
    if not value.strip():
        return 1
    if not PAGE_PATTERN.fullmatch(value) or int(value) < 1:
        raise MalformedForm(WRONG_FIELDS)
    return int(value)


def list_filters(
    query_items: Iterable[tuple[str, str]], segments: Iterable[SeededSegment]
) -> ListFilters:
    """The filters of the query ``query_items`` (name and value pairs, in order), the Segment
    key resolved against ``segments``; ``MalformedForm`` for any other or repeated parameter,
    an unknown stage or Segment key, or a bad ``q`` or ``page``."""
    values = _single_values(query_items)
    segment_key = values.get(SEGMENT_FIELD, "")
    segment_key = segment_key if segment_key.strip() else None
    segment_id = None
    if segment_key is not None:
        found = [segment.id for segment in segments if segment.key == segment_key]
        if not found:
            raise MalformedForm(WRONG_FIELDS)
        segment_id = found[0]
    return ListFilters(
        stage=_stage(values.get(STAGE_FIELD, "")),
        segment=segment_key,
        segment_id=segment_id,
        search=_search(values.get(SEARCH_FIELD, "")),
        page=_page(values.get(PAGE_FIELD, "")),
    )


def page_href(filters: ListFilters, page: int) -> str:
    """The list's URL for ``page`` with the set filters, URL-encoded."""
    params: list[tuple[str, str]] = []
    if filters.stage is not None:
        params.append((STAGE_FIELD, filters.stage.value))
    if filters.segment is not None:
        params.append((SEGMENT_FIELD, filters.segment))
    if filters.search is not None:
        params.append((SEARCH_FIELD, filters.search))
    params.append((PAGE_FIELD, str(page)))
    return f"{LIST_PATH}?{urlencode(params)}"


def stage_label(stage: str | None) -> str:
    """The label of ``stage``; its own text when unknown, "—" when ``None``."""
    if stage is None:
        return NO_VALUE
    return STAGE_LABELS.get(stage, stage)


def source_label(kind: str) -> str:
    """The label of the Provenance source ``kind``; its own text when unknown."""
    return SOURCE_LABELS.get(kind, kind)


def day(at: datetime) -> str:
    """``at`` (aware UTC) as ``YYYY-MM-DD``."""
    return format(at, DATE_FORMAT)


def stage_options(selected: PipelineStage | None) -> tuple[Option, ...]:
    """Every stage, in machine order, by its label."""
    return tuple(
        Option(stage.value, STAGE_LABELS[stage], stage is selected) for stage in PipelineStage
    )


def segment_options(
    segments: Iterable[SeededSegment], selected: str | None
) -> tuple[Option, ...]:
    """Each stored Segment by name (then key), its value the key."""
    ordered = sorted(
        segments, key=lambda segment: (segment.definition.name.casefold(), segment.key)
    )
    return tuple(
        Option(segment.key, segment.definition.name, segment.key == selected)
        for segment in ordered
    )


def segment_names(segments: Iterable[SeededSegment]) -> dict[str, str]:
    """Each stored Segment's name, by its id."""
    return {segment.id: segment.definition.name for segment in segments}


def count_text(found: LeadListPage, filters: ListFilters) -> str:
    """The list's count line."""
    if found.total == 0:
        return NO_MATCHES if filters.filtered else NO_LEADS
    first = (found.page - 1) * LEADS_PER_PAGE + 1
    last = first + len(found.leads) - 1
    noun = "Lead" if found.total == 1 else "Leads"
    return f"Showing {first:,}–{last:,} of {found.total:,} {noun}"


def lead_row(lead: ListedLead, names: Mapping[str, str]) -> LeadRow:
    """One Lead as the list shows it."""
    return LeadRow(
        href=f"{LIST_PATH}/{lead.id}",
        name=lead.name,
        website=lead.web_domain,
        segment=names.get(lead.segment_id, UNKNOWN_SEGMENT),
        country=lead.country_code,
        stage=stage_label(lead.pipeline_stage),
        created=day(lead.created_at),
    )


def pager(found: LeadListPage, filters: ListFilters) -> Pager | None:
    """The pager, or ``None`` when everything fits on one page."""
    if found.pages <= 1:
        return None
    return Pager(
        page=found.page,
        pages=found.pages,
        previous=page_href(filters, found.page - 1) if found.page > 1 else None,
        next=page_href(filters, found.page + 1) if found.page < found.pages else None,
    )


def list_context(
    filters: ListFilters, found: LeadListPage, segments: Iterable[SeededSegment]
) -> dict[str, object]:
    """What ``leads.html`` renders."""
    stored = tuple(segments)
    names = segment_names(stored)
    return {
        "stage_options": stage_options(filters.stage),
        "segment_options": segment_options(stored, filters.segment),
        "search": filters.search or "",
        "filtered": filters.filtered,
        "count_text": count_text(found, filters),
        "rows": tuple(lead_row(lead, names) for lead in found.leads),
        "pager": pager(found, filters),
    }


# The Lead page -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeadHeader:
    """The Lead page's header facts."""

    name: str
    website: str | None
    website_url: str | None
    segment: str
    country: str | None
    stage: str
    created: str
    hq_tz: str | None = None


@dataclass(frozen=True, slots=True)
class ProvenanceRow:
    """One Provenance Record as the Lead page shows it: the record ("Contact" or "Address"),
    the address of an ``address`` record, the source's label, the detail, the retrieval date
    and the Address Class at the time."""

    record: str
    address: str | None
    source: str
    detail: str
    retrieved: str
    address_class: str | None


@dataclass(frozen=True, slots=True)
class ReasonRow:
    """One failing Eligibility check as the Lead page shows it: its code (the item's
    ``data-reason``) and its text."""

    code: str
    text: str


@dataclass(frozen=True, slots=True)
class EligibilityRow:
    """A Contact's live Eligibility as the Lead page shows it: "Eligible: <basis>" or "Not
    eligible", then every reason."""

    eligible: bool
    summary: str
    reasons: tuple[ReasonRow, ...]


@dataclass(frozen=True, slots=True)
class ContactRow:
    """One Contact as the Lead page shows it, with its live Eligibility, its Provenance Records
    and whether it gets a "Make primary" form."""

    contact_id: str
    name: str | None
    role: str | None
    email: str | None
    address_class: str | None
    linkedin_url: str | None
    primary: str
    eligibility: EligibilityRow
    records: tuple[ProvenanceRow, ...]
    can_make_primary: bool = True


@dataclass(frozen=True, slots=True)
class SignalRow:
    """One current Signal as the Lead page shows it."""

    name: str
    signal_class: str
    tier: str
    date: str
    age: str
    status: str
    score_only: str


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """One live Evidence row as the Lead page shows it: its type, its quote or what was
    sought or counted (with the number and the pages checked), what it supports, its source
    URL, its retrieval time and whether AI extracted it."""

    type: str
    quote: str | None
    sought: str | None
    pages_checked: tuple[str, ...]
    supports: str
    source_url: str | None
    retrieved: str
    extraction: str


def lead_header(
    lead: StoredLead,
    stage: PipelineStage | None,
    names: Mapping[str, str],
    hq_tz: str | None = None,
) -> LeadHeader:
    """The Lead page's header facts."""
    domain = lead.web_domain
    return LeadHeader(
        name=lead.name,
        website=domain,
        website_url=None if domain is None else f"https://{domain}/",
        segment=names.get(lead.segment_id, UNKNOWN_SEGMENT),
        country=lead.country_code,
        stage=stage_label(stage),
        created=day(lead.created_at),
        hq_tz=hq_tz,
    )


def moment(at: datetime) -> str:
    """``at`` (aware UTC) as ``YYYY-MM-DD HH:MM UTC``."""
    return format(at, TIME_FORMAT)


def age_text(age: int) -> str:
    """``<n> days``, or ``1 day``."""
    return "1 day" if age == 1 else f"{age:,} days"


def signal_row(status: SignalStatus) -> SignalRow:
    """One current Signal as the Lead page shows it."""
    signal = status.signal
    return SignalRow(
        name=status.name,
        signal_class=CLASS_LABELS.get(signal.signal_class, signal.signal_class),
        tier=f"Tier {signal.tier}",
        date=(
            f"first seen {day(signal.first_seen_at)}"
            if signal.event_date is None
            else format(signal.event_date, DATE_FORMAT)
        ),
        age=age_text(status.age),
        status=LIVE if status.live else EXPIRED,
        score_only=SCORE_ONLY if signal.score_only else "",
    )


def _supports(row: EvidenceSupport, contact_names: Mapping[str, str]) -> str:
    if row.supports:
        return SUPPORTS_SEPARATOR.join(row.supports)
    evidence = row.evidence
    if evidence.person_contact_id is not None:
        return CONTACT_SUPPORT + contact_names.get(evidence.person_contact_id, NO_VALUE)
    if evidence.fact is None:
        return NO_VALUE
    label = FACT_LABELS.get(evidence.fact, evidence.fact)
    if evidence.value is not None:
        return f"{label}: {evidence.value}"
    if evidence.number is not None:
        return f"{label}: {evidence.number:,}"
    return label


def _sought(row: EvidenceSupport) -> str | None:
    evidence = row.evidence
    if evidence.sought is None:
        return None
    if evidence.type == EvidenceType.COUNT:
        number = NO_VALUE if evidence.number is None else f"{evidence.number:,}"
        return f"Counted: {evidence.sought}: {number}"
    return f"Sought: {evidence.sought}"


def evidence_row(
    row: EvidenceSupport, contact_names: Mapping[str, str] = MappingProxyType({})
) -> EvidenceRow:
    """One live Evidence row as the Lead page shows it; ``contact_names`` names the Contact a
    person Evidence row is about (by Contact id)."""
    evidence = row.evidence
    is_quote = evidence.type == EvidenceType.QUOTE
    return EvidenceRow(
        type=EVIDENCE_TYPE_LABELS.get(evidence.type, evidence.type),
        quote=evidence.quote if is_quote else None,
        sought=_sought(row),
        pages_checked=() if is_quote else evidence.pages_checked,
        supports=_supports(row, contact_names),
        source_url=evidence.source_url,
        retrieved=moment(evidence.retrieved_at),
        extraction=AI_EXTRACTED if evidence.extracted_by.startswith(AI_ACTOR_PREFIX) else NO_VALUE,
    )


def provenance_row(record: StoredProvenance) -> ProvenanceRow:
    """One Provenance Record as the Lead page shows it."""
    is_address = record.subject == ProvenanceSubject.ADDRESS
    return ProvenanceRow(
        record=RECORD_LABELS.get(record.subject, record.subject),
        address=record.address if is_address else None,
        source=source_label(record.source_kind),
        detail=record.source_detail,
        retrieved=day(record.retrieved_at),
        address_class=record.address_class,
    )


def eligibility_row(decision: Decision) -> EligibilityRow:
    """A Contact's live Decision as the Lead page shows it."""
    if not isinstance(decision, Decision):
        raise TypeError(f"eligibility_row takes a Decision, not {type(decision).__name__}")
    summary = ELIGIBLE.format(basis=decision.basis_rule) if decision.eligible else NOT_ELIGIBLE
    return EligibilityRow(
        eligible=decision.eligible,
        summary=summary,
        reasons=tuple(ReasonRow(reason.code, reason.text) for reason in decision.reasons),
    )


def _operators_primary(contact: StoredContact) -> bool:
    return contact.is_primary and contact.primary_set_by == PrimarySetBy.OPERATOR


def contact_row(
    contact: StoredContact, records: Iterable[StoredProvenance], decision: Decision
) -> ContactRow:
    """One Contact as the Lead page shows it."""
    return ContactRow(
        contact_id=contact.id,
        name=contact.name,
        role=contact.role,
        email=contact.email,
        address_class=contact.address_class,
        linkedin_url=contact.linkedin_url,
        primary=YES if contact.is_primary else NO,
        eligibility=eligibility_row(decision),
        records=tuple(provenance_row(record) for record in records),
        can_make_primary=not _operators_primary(contact)
        and contact.address_class != INVALID_ADDRESS,
    )


def primary_line(contacts: Iterable[StoredContact], no_decision_maker: bool | None) -> str | None:
    """The Contacts panel's Primary line, or ``None``; see the module docstring."""
    primary = next((contact for contact in contacts if contact.is_primary), None)
    if primary is None:
        return NO_DECISION_MAKER if no_decision_maker else None
    who = primary.name or primary.email or NO_VALUE
    chosen = CHOSEN_BY.get(primary.primary_set_by or "", CHOSEN_BY[PrimarySetBy.SYSTEM])
    return f"{PRIMARY_PREFIX}{who} {chosen}"


def primary_set() -> Notice:
    """The notice of a Primary Contact the operator set."""
    return Notice(Role.STATUS, PRIMARY_SET)


def primary_refused(refusal: PrimaryRefused) -> Notice:
    """The notice of a "Make primary" that was refused (nothing was written)."""
    return Notice(Role.ALERT, REFUSALS[refusal.reason])


def submitted_contact(fields: FormFields) -> str:
    """The ``contact_id`` the "Make primary" form posts, once; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != {CONTACT_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = fields[CONTACT_FIELD]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    return values[0]


def contacts_context(
    lead_id: str,
    contacts: Iterable[StoredContact],
    provenance: Mapping[str, Iterable[StoredProvenance]],
    decisions: Mapping[str, Decision],
    no_decision_maker: bool | None,
    notice: Notice | None = None,
) -> dict[str, object]:
    """What ``lead_contacts.html`` renders: the Lead's id (for the forms' path), the Primary
    line, the Contacts (in the order given, oldest first), each with its live Eligibility from
    ``decisions`` and its Provenance Records from ``provenance`` (both by Contact id), and the
    POST's notice. A Contact without a Decision raises ``KeyError``: the panel never shows a
    Contact whose Eligibility wasn't evaluated."""
    found = tuple(contacts)
    return {
        "lead_id": lead_id,
        "primary": primary_line(found, no_decision_maker),
        "contacts": tuple(
            contact_row(contact, provenance.get(contact.id, ()), decisions[contact.id])
            for contact in found
        ),
        "notice": notice,
    }


def lead_context(
    lead: StoredLead,
    stage: PipelineStage | None,
    segments: Iterable[SeededSegment],
    contacts: Iterable[StoredContact],
    provenance: Mapping[str, Iterable[StoredProvenance]],
    signals: LeadSignalsView,
    decisions: Mapping[str, Decision],
    no_decision_maker: bool | None = None,
) -> dict[str, object]:
    """What ``lead.html`` renders: the header, the Signals and the live Evidence (in the order
    given), and the Contacts panel (``contacts_context``), each Contact with its live
    Eligibility from ``decisions`` (by Contact id)."""
    found = tuple(contacts)
    names = {contact.id: contact.name or contact.email or NO_VALUE for contact in found}
    return {
        "lead": lead_header(lead, stage, segment_names(segments), signals.hq_tz),
        "signals": tuple(signal_row(status) for status in signals.signals),
        "evidence": tuple(evidence_row(row, names) for row in signals.evidence),
        **contacts_context(lead.id, found, provenance, decisions, no_decision_maker),
    }
