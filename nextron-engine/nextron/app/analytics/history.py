"""A Lead's complete history (Story 9.8; FR-46, FR-51, UX-DR17): the History tab's timeline.

``lead_history(lead_id, *, type, page, now)`` reads, through the current unit of work (a
read-only one included), and writes nothing: no table, event, job or notification. The route
checks that ``lead_id`` is a survivor first (``queries.discovery.survivor_of``); an unknown Lead
gives ``None``. ``LeadHistory(clock).view(lead_id, type=..., page=...)`` is the same at the
clock's now, for the dashboard.

**The Leads.** The Lead and every Lead merged into it (``queries.discovery.merged_lead_ids``),
so a merged Lead's history shows on its survivor without a merge event.

**Events** (``queries.analytics.lead_events``): every ``events`` row about those Leads or about
any row linked to them by foreign keys, found generically, plus the rows of the suppression
entries that match a key of one of their Contacts or of their web domains, of any status. The
keys are derived as ``Compliance.suppress`` derives them: ``person_keys`` (``erased_person_keys``
for an erased Contact) and ``firm_domain_keys`` of each Contact, and ``firm_domain_keys`` of
each Lead's web domain (a Contact whose Lead has no web domain, a merged Lead's being cleared,
uses the survivor's), plus every entry whose ``cause_ref`` is the id of a data subject request
of one of the Contacts (``queries.compliance.caused_by_requests``: an erasure's own entries,
whose person keys can't be derived once erased). A suppression entry's event carries the entry's kind and source as its
text, never its hash or domain.

**Row-derived entries**, for history that has no ``events`` rows (``nextron.domain.lead_history``
kinds):

- each Contact added, at its ``created_at``, its cause the source of its first Provenance
  Record that isn't an eligibility basis, by ``operator`` when that source is ``operator`` or
  ``csv`` (the Operator uploads a CSV), else by ``system:research``;
- each Evidence row added (``created_at``, by its ``extracted_by``, its cause its ``source``, its
  text its stored quote or what was sought, its link its ``source_url`` when ``http``/``https``),
  superseded (dated by the superseding row's ``created_at``, else its ``last_confirmed_at``) and
  retracted (``retracted_at``; from ``superseded`` when it was superseded at or before then);
- each Signal added (``first_seen_at``, its text its Signal type's name from its Lead's Segment
  as the Lead page shows it, else its key; its cause its class),
  expired (``lead_history.signal_expired_at``, shown only when that moment is at or before
  ``now`` and the Signal wasn't retracted before it) and retracted (``retracted_at``).

Stored values only: an erased Contact's name is "[erased]" and its Evidence shows its
tombstoned text. ``LeadHistoryView`` holds the ``HistoryPage``, each Contact's stored name by id
(``None`` when it has none) and the survivor's id.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from nextron.app.compliance.api import ENTITY as SUPPRESSION_ENTITY
from nextron.app.queries import analytics as analytics_queries
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.domain.contactability import (
    SuppressionKey,
    erased_person_keys,
    firm_domain_keys,
    person_keys,
)
from nextron.domain.contacts import ProvenanceSource
from nextron.domain.erasure import REDACTED
from nextron.domain.lead_history import (
    CONTACT_ADDED,
    EVENT,
    EVIDENCE_ADDED,
    EVIDENCE_RETRACTED,
    EVIDENCE_SUPERSEDED,
    ROW_TITLES,
    SIGNAL_ADDED,
    SIGNAL_EXPIRED,
    SIGNAL_RETRACTED,
    HistoryEntry,
    HistoryPage,
    HistoryType,
    code_label,
    entity_label,
    history_type,
    http_link,
    page_of,
    signal_expired_at,
)
from nextron.ports.clock import Clock

CONTACTS_ENTITY = "contacts"
EVIDENCE_ENTITY = "evidence"
SIGNALS_ENTITY = "signals"
RESEARCH_ACTOR = "system:research"
OPERATOR_ACTOR = "operator"
OPERATOR_SOURCES = frozenset({ProvenanceSource.OPERATOR, ProvenanceSource.CSV})
LIVE = "live"
ADDED = "added"
SUPERSEDED = "superseded"
RETRACTED = "retracted"
EXPIRED = "expired"
SOURCE_GONE = "source_gone"
UNSUPPORTED = "unsupported"
AGED_OUT = "aged_out"


@dataclass(frozen=True, slots=True)
class LeadHistoryView:
    """The History tab's page, the Contacts' stored names by id and the survivor's id."""

    page: HistoryPage
    contact_names: Mapping[str, str | None]
    lead_id: str


def _keys(
    contacts: Iterable[research_queries.StoredContact],
    web_domains: Mapping[str, str | None],
    survivor_id: str,
) -> list[SuppressionKey]:
    keys: list[SuppressionKey] = []
    for web_domain in web_domains.values():
        keys += firm_domain_keys(email=None, web_domain=web_domain)
    for contact in contacts:
        # A merged Lead's web domain is cleared (``mark_merged``): use the survivor's.
        web_domain = web_domains.get(contact.lead_id) or web_domains.get(survivor_id)
        if contact.erased_at is not None:
            keys += erased_person_keys(contact.email)
            keys += firm_domain_keys(email=None, web_domain=web_domain)
        else:
            keys += person_keys(
                email=contact.email,
                name=contact.name,
                linkedin_url=contact.linkedin_url,
                web_domain=web_domain,
            )
            keys += firm_domain_keys(email=contact.email, web_domain=web_domain)
    return keys


def _row(
    kind: str,
    entity: str,
    row_id: str,
    at: datetime,
    *,
    type: HistoryType | None,
    to_status: str,
    from_status: str | None = None,
    cause: str | None = None,
    actor: str | None = RESEARCH_ACTOR,
    contact_id: str | None = None,
    text: str | None = None,
    link: str | None = None,
) -> HistoryEntry:
    return HistoryEntry(
        at=at,
        sort_id=f"{row_id}:{kind}",
        type=type,
        kind=kind,
        entity=entity,
        entity_id=row_id,
        from_status=from_status,
        to_status=to_status,
        cause=cause,
        actor=actor,
        title=ROW_TITLES[kind],
        contact_id=contact_id,
        text=text,
        link=link,
    )


def _contact_entries(
    contacts: Iterable[research_queries.StoredContact],
    provenance: Mapping[str, Sequence[research_queries.StoredProvenance]],
) -> list[HistoryEntry]:
    entries = []
    for contact in contacts:
        sources = [
            record.source_kind
            for record in provenance.get(contact.id, ())
            if record.source_kind != ProvenanceSource.ELIGIBILITY_BASIS
        ]
        source = sources[0] if sources else None
        entries.append(
            _row(
                CONTACT_ADDED,
                CONTACTS_ENTITY,
                contact.id,
                contact.created_at,
                type=history_type(CONTACTS_ENTITY, ADDED),
                to_status=ADDED,
                cause=source,
                actor=OPERATOR_ACTOR if source in OPERATOR_SOURCES else RESEARCH_ACTOR,
                contact_id=contact.id,
            )
        )
    return entries


def _evidence_entries(rows: Sequence[research_queries.StoredEvidence]) -> list[HistoryEntry]:
    created = {row.id: row.created_at for row in rows}
    category = history_type(EVIDENCE_ENTITY, LIVE)
    entries = []
    for row in rows:
        person = row.person_contact_id
        entries.append(
            _row(
                EVIDENCE_ADDED,
                EVIDENCE_ENTITY,
                row.id,
                row.created_at,
                to_status=LIVE,
                cause=row.source,
                actor=row.extracted_by,
                text=row.quote if row.quote is not None else row.sought,
                link=http_link(row.source_url),
                type=category,
                contact_id=person,
            )
        )
        superseded_at = None
        if row.superseded_by is not None:
            superseded_at = created.get(row.superseded_by, row.last_confirmed_at)
            entries.append(
                _row(
                    EVIDENCE_SUPERSEDED,
                    EVIDENCE_ENTITY,
                    row.id,
                    superseded_at,
                    from_status=LIVE,
                    to_status=SUPERSEDED,
                    cause=SUPERSEDED,
                    type=category,
                    contact_id=person,
                )
            )
        if row.retracted_at is not None:
            was_superseded = superseded_at is not None and superseded_at <= row.retracted_at
            entries.append(
                _row(
                    EVIDENCE_RETRACTED,
                    EVIDENCE_ENTITY,
                    row.id,
                    row.retracted_at,
                    from_status=SUPERSEDED if was_superseded else LIVE,
                    to_status=RETRACTED,
                    cause=SOURCE_GONE,
                    type=category,
                    contact_id=person,
                )
            )
    return entries


def _signal_entries(
    rows: Iterable[research_queries.StoredSignal], now: datetime, type_names: Mapping[str, str]
) -> list[HistoryEntry]:
    category = history_type(SIGNALS_ENTITY, LIVE)
    entries = []
    for row in rows:
        name = type_names.get(row.signal_type, row.signal_type)
        entries.append(
            _row(
                SIGNAL_ADDED,
                SIGNALS_ENTITY,
                row.id,
                row.first_seen_at,
                type=category,
                to_status=LIVE,
                cause=row.signal_class,
                text=name,
            )
        )
        expired = signal_expired_at(row.signal_class, row.tier, row.event_date, row.first_seen_at)
        shown = (
            expired is not None
            and expired <= now
            and (row.retracted_at is None or row.retracted_at > expired)
        )
        if shown:
            assert expired is not None
            entries.append(
                _row(
                    SIGNAL_EXPIRED,
                    SIGNALS_ENTITY,
                    row.id,
                    expired,
                    type=category,
                    from_status=LIVE,
                    to_status=EXPIRED,
                    cause=AGED_OUT,
                    text=name,
                )
            )
        if row.retracted_at is not None:
            entries.append(
                _row(
                    SIGNAL_RETRACTED,
                    SIGNALS_ENTITY,
                    row.id,
                    row.retracted_at,
                    type=category,
                    from_status=EXPIRED if shown else LIVE,
                    to_status=RETRACTED,
                    cause=UNSUPPORTED,
                    text=name,
                )
            )
    return entries


def _event_entries(
    events: Iterable[analytics_queries.StoredEvent],
    contact_ids: Iterable[str],
    details: Mapping[str, str],
) -> list[HistoryEntry]:
    known = frozenset(contact_ids)
    entries = []
    for event in events:
        contact_id = None
        if event.entity == CONTACTS_ENTITY:
            contact_id = event.entity_id
        elif event.cause_ref is not None and event.cause_ref in known:
            contact_id = event.cause_ref
        text = details.get(event.entity_id) if event.entity == SUPPRESSION_ENTITY else None
        entries.append(
            HistoryEntry(
                at=event.at,
                sort_id=event.id,
                type=history_type(event.entity, event.to_status),
                kind=EVENT,
                entity=event.entity,
                entity_id=event.entity_id,
                from_status=event.from_status,
                to_status=event.to_status,
                cause=event.cause,
                actor=event.actor,
                title=entity_label(event.entity),
                contact_id=contact_id,
                text=text,
            )
        )
    return entries


def _type_names(leads: Sequence[discovery_queries.StoredLead]) -> dict[str, str]:
    """Each Signal type key's name from the Leads' Segments, the survivor's (first) winning."""
    names: dict[str, str] = {}
    for each in reversed(leads):
        seeded = setup_queries.segment_by_id(each.segment_id)
        if seeded is not None:
            names.update({kind.key: kind.name for kind in seeded.definition.signal_types})
    return names


def _name(contact: research_queries.StoredContact) -> str | None:
    return REDACTED if contact.erased_at is not None else contact.name


def lead_history(
    lead_id: str, *, type: HistoryType, page: int, now: datetime
) -> LeadHistoryView | None:
    """The Lead's timeline page at ``now``; see the module docstring."""
    lead = discovery_queries.lead(lead_id)
    if lead is None:
        return None
    leads = [lead]
    for merged_id in discovery_queries.merged_lead_ids(lead.id):
        merged = discovery_queries.lead(merged_id)
        if merged is not None:
            leads.append(merged)
    lead_ids = [each.id for each in leads]
    web_domains = {each.id: each.web_domain for each in leads}
    contacts = [contact for each in lead_ids for contact in research_queries.contacts(each)]
    provenance: dict[str, Sequence[research_queries.StoredProvenance]] = {}
    for each in lead_ids:
        provenance.update(research_queries.lead_provenance(each))
    contact_ids = [contact.id for contact in contacts]
    found = {
        entry.id: entry
        for entry in (
            *compliance_queries.matching(_keys(contacts, web_domains, lead.id)),
            *compliance_queries.caused_by_requests(contact_ids),
        )
    }
    suppression = list(found.values())
    details = {
        entry.id: f"{code_label(entry.kind)} key, source: {code_label(entry.source)}"
        for entry in suppression
    }
    events = analytics_queries.lead_events(
        lead_ids, {SUPPRESSION_ENTITY: [entry.id for entry in suppression]}
    )
    evidence = [row for each in lead_ids for row in research_queries.all_evidence(each)]
    signals = [row for each in lead_ids for row in research_queries.all_signals(each)]
    entries = [
        *_event_entries(events, contact_ids, details),
        *_contact_entries(contacts, provenance),
        *_evidence_entries(evidence),
        *_signal_entries(signals, now, _type_names(leads)),
    ]
    return LeadHistoryView(
        page=page_of(entries, type, page),
        contact_names={contact.id: _name(contact) for contact in contacts},
        lead_id=lead.id,
    )


class LeadHistory:
    """The History tab's timeline at the clock's now."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def view(self, lead_id: str, *, type: HistoryType, page: int) -> LeadHistoryView | None:
        """``lead_history`` at now."""
        return lead_history(lead_id, type=type, page=page, now=self._clock.now())
