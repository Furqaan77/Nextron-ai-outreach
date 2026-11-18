"""Read API of the ``research`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.research`` only. All read through the
current unit of work, read-only ones included.

- ``contact(contact_id)``: the Contact as a ``StoredContact``, or ``None`` when no Contact has
  that id (a text that isn't a ULID included).
- ``contacts(lead_id)``: the Lead's Contacts as ``StoredContact`` rows, oldest first (by
  ``created_at``, then ``id``); empty for an unknown Lead (a text that isn't a ULID included).
- ``contacts_with_email(email)`` (Story 3.5): the Contacts whose stored ``email`` (always
  normalised, ``identity.normalize_email``) is exactly ``email``, oldest first; empty for a
  value that isn't a ``str``. ``Compliance.suppress`` reads it for an address the Operator
  typed.
- ``provenance(contact_id)``: the Contact's Provenance Records as ``StoredProvenance`` rows, by
  ``retrieved_at``, then ``id``; empty for an unknown Contact.
- ``lead_provenance(lead_id)`` (Story 2.4): the Provenance Records of every Contact of the
  Lead, in one read: each Contact id that has records, mapped to them by ``retrieved_at``,
  then ``id``; ``{}`` for an unknown Lead (a text that isn't a ULID included).
- ``crawl_run(run_id)`` (Story 2.5): the crawl run as a ``StoredCrawlRun``, or ``None``;
  ``None`` for a text that isn't a ULID, like the reads below.
- ``latest_crawl_run(lead_id)``: the Lead's run started last, or ``None``. The Lead page
  (Story 2.4) shows its outcome with ``nextron.domain.crawl.OUTCOME_LABELS`` ("website
  unreachable").
- ``crawled_pages(lead_id)``: the Lead's stored pages, by URL, each a ``StoredPage`` (whose
  ``text`` is ``None`` once purged).
- ``ats_links(lead_id)``: the ATS board links found on the Lead's website, by URL.
- ``contact_methods(lead_id)`` (Story 2.6): the Lead's profile value, a
  ``nextron.domain.widgets.ContactMethods`` (every page checked, and each method's pages), or
  ``None`` before any run set it (or for an unknown Lead).
- ``page_contact_methods(lead_id)``: the contact methods detected on each of the Lead's pages,
  as ``StoredContactMethod`` rows (``matched`` hidden from the ``repr``), by page URL, then
  method, signature and source.

Story 2.9 adds the Signals and their Evidence (a text that isn't a ULID reads as an unknown
Lead: empty, or ``None``):

- ``signals(lead_id)``: the Lead's Signals that aren't retracted, as ``StoredSignal`` rows, by
  ``first_seen_at``, then ``id``.
- ``evidence(lead_id)``: the Lead's live Evidence (neither superseded nor retracted), as
  ``StoredEvidence`` rows (``pages_checked`` a tuple of URLs), oldest first.
- ``hq_tz(lead_id)``: the Lead's headquarters zone, or ``None``.
- ``last_ok_extraction(lead_id)``: the Lead's ``ok`` ``extract_signals`` run started last, as
  a ``StoredExtractionRun``, or ``None``; ``extraction_runs(lead_id)``: every run (of every
  route), oldest first.
- ``lead_signals(lead_id, now, type_names) -> LeadSignalsView``: what the Lead page shows, read
  at once: ``hq_tz``; each current Signal as a ``SignalStatus(signal, name, age, live)``, its
  ``name`` from ``type_names`` (a Signal type key to its Segment's name) or else its key, its
  ``age`` from ``nextron.domain.signals.age_days`` at ``now`` and ``live`` from ``is_live``
  (a Signal whose stored class or Tier the domain refuses counts as expired); and each live
  Evidence row as an ``EvidenceSupport(evidence, supports)``, ``supports`` being the names of
  the current Signals it supports, in the Signals' order.

Story 2.10 adds ``no_decision_maker(lead_id)``: ``True`` when the Lead's last Primary Contact
selection found no decision-maker, ``False`` when it found one, ``None`` before the first
selection (and for an unknown Lead). A Contact's ``StoredContact`` row carries
``primary_set_by`` (``system`` or ``operator``) beside ``is_primary``.

Story 3.15 adds the subject access exporter (``nextron.domain.contact_export``):

- ``export_pii(contact_id) -> {section: tuple[dict, ...]}``: everything ``research`` holds on
  the Contact, every row read with every column (``nextron.db.repos.research.export``), so a
  column a later story adds is exported without code. An unknown Contact (a text that isn't a
  ULID included) raises ``LookupError``, whose message holds no id. The sections:

  - ``contact``: the one ``contacts`` row;
  - ``provenance_records``: every record, every eligibility basis included, oldest first;
  - ``addresses``: ``contact_export.address_history`` of those records and the stored email;
  - ``evidence``: every row whose ``person_contact_id`` is the Contact (superseded and
    retracted ones too), its ``pages_checked`` decoded to a list;
  - ``signals``: the Signals that Evidence supports, each with its ``evidence_ids``;
  - ``pages``: the Lead's crawled pages whose stored ``text`` mentions the Contact
    (``contact_export.mentions`` of the terms: ``mention_terms(name, email)``, then one
    ``email`` term, ``mention_terms(None, address)``, per other distinct ``address`` in the
    Contact's Provenance, so a page naming only an earlier address is found): every column but
    ``text``, plus ``mentions`` (``name``, ``email``, each once) and ``text_excerpts``
    (``contact_export.excerpts``); never a whole page, since the rest is the firm's and other
    people's data;
  - ``contact_methods``: the Lead's pages' contact methods whose ``matched`` text mentions the
    Contact (the same terms), each with ``page_url`` and ``mentions``.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import export as export_repo
from nextron.db.repos.research import profile as profile_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.repos.research.crawl import (
    StoredAtsLink,
    StoredContactMethod,
    StoredCrawlRun,
    StoredPage,
)
from nextron.db.repos.research.provenance import StoredProvenance
from nextron.db.repos.research.signals import StoredEvidence, StoredExtractionRun, StoredSignal
from nextron.domain.contact_export import address_history, excerpts, mention_terms, mentions
from nextron.domain.extraction import ROUTE as SIGNALS_ROUTE
from nextron.domain.ids import is_ulid
from nextron.domain.segments import SignalClass
from nextron.domain.signals import age_days, is_live
from nextron.domain.widgets import ContactMethods, decode

__all__ = [
    "EvidenceSupport",
    "LeadSignalsView",
    "SignalStatus",
    "StoredAtsLink",
    "StoredContact",
    "StoredContactMethod",
    "StoredCrawlRun",
    "StoredEvidence",
    "StoredExtractionRun",
    "StoredPage",
    "StoredProvenance",
    "StoredSignal",
    "ats_links",
    "contact",
    "contact_methods",
    "contacts",
    "contacts_with_email",
    "crawl_run",
    "crawled_pages",
    "evidence",
    "export_pii",
    "extraction_runs",
    "hq_tz",
    "last_ok_extraction",
    "latest_crawl_run",
    "lead_provenance",
    "lead_signals",
    "no_decision_maker",
    "page_contact_methods",
    "provenance",
    "signals",
]


@dataclass(frozen=True, slots=True)
class SignalStatus:
    """A current Signal as the Lead page shows it: its type's name, its age in days and whether
    it is still live."""

    signal: StoredSignal
    name: str
    age: int
    live: bool


@dataclass(frozen=True, slots=True)
class EvidenceSupport:
    """A live Evidence row and the names of the current Signals it supports."""

    evidence: StoredEvidence
    supports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LeadSignalsView:
    """The Lead page's Signals and Evidence, and the headquarters zone."""

    hq_tz: str | None
    signals: tuple[SignalStatus, ...]
    evidence: tuple[EvidenceSupport, ...]


def contact(contact_id: str) -> StoredContact | None:
    """The Contact ``contact_id``, or ``None``."""
    if not is_ulid(contact_id):
        return None
    return contacts_repo.get(contact_id)


def contacts(lead_id: str) -> tuple[StoredContact, ...]:
    """The Contacts of the Lead ``lead_id``, oldest first."""
    if not is_ulid(lead_id):
        return ()
    return contacts_repo.for_lead(lead_id)


def contacts_with_email(email: str) -> tuple[StoredContact, ...]:
    """The Contacts whose ``email`` is ``email``, oldest first."""
    if not isinstance(email, str):
        return ()
    return contacts_repo.with_email(email)


def provenance(contact_id: str) -> tuple[StoredProvenance, ...]:
    """The Provenance Records of the Contact ``contact_id``, oldest first."""
    if not is_ulid(contact_id):
        return ()
    return provenance_repo.for_contact(contact_id)


def lead_provenance(lead_id: str) -> dict[str, tuple[StoredProvenance, ...]]:
    """The Provenance Records of the Lead ``lead_id``'s Contacts, by Contact id, oldest
    first."""
    if not is_ulid(lead_id):
        return {}
    grouped: dict[str, list[StoredProvenance]] = {}
    for record in provenance_repo.for_lead(lead_id):
        grouped.setdefault(record.contact_id, []).append(record)
    return {contact_id: tuple(records) for contact_id, records in grouped.items()}


def crawl_run(run_id: str) -> StoredCrawlRun | None:
    """The crawl run ``run_id``, or ``None``."""
    return crawl_repo.run(run_id) if is_ulid(run_id) else None


def latest_crawl_run(lead_id: str) -> StoredCrawlRun | None:
    """The Lead's crawl run started last, or ``None``."""
    return crawl_repo.latest_run(lead_id) if is_ulid(lead_id) else None


def crawled_pages(lead_id: str) -> tuple[StoredPage, ...]:
    """The Lead's stored pages, by URL."""
    return crawl_repo.pages(lead_id) if is_ulid(lead_id) else ()


def ats_links(lead_id: str) -> tuple[StoredAtsLink, ...]:
    """The Lead's ATS board links, by URL."""
    return crawl_repo.ats_links(lead_id) if is_ulid(lead_id) else ()


def contact_methods(lead_id: str) -> ContactMethods | None:
    """The Lead's ``contact_methods`` profile value, or ``None``."""
    if not is_ulid(lead_id):
        return None
    value = profile_repo.contact_methods(lead_id)
    return None if value is None else decode(value)


def page_contact_methods(lead_id: str) -> tuple[StoredContactMethod, ...]:
    """The contact methods detected on the Lead's pages, by page URL."""
    return crawl_repo.contact_methods_of(lead_id) if is_ulid(lead_id) else ()


def signals(lead_id: str) -> tuple[StoredSignal, ...]:
    """The Lead's Signals that aren't retracted, first seen first."""
    return signals_repo.signals(lead_id, current_only=True) if is_ulid(lead_id) else ()


def evidence(lead_id: str) -> tuple[StoredEvidence, ...]:
    """The Lead's live Evidence, oldest first."""
    return signals_repo.evidence(lead_id, live_only=True) if is_ulid(lead_id) else ()


def hq_tz(lead_id: str) -> str | None:
    """The Lead's headquarters zone, or ``None``."""
    found = profile_repo.headquarters(lead_id) if is_ulid(lead_id) else None
    return None if found is None else found.hq_tz


def last_ok_extraction(lead_id: str) -> StoredExtractionRun | None:
    """The Lead's ``ok`` ``extract_signals`` run started last, or ``None``."""
    return signals_repo.last_ok_run(lead_id, SIGNALS_ROUTE) if is_ulid(lead_id) else None


def no_decision_maker(lead_id: str) -> bool | None:
    """Whether the Lead's last Primary Contact selection found no decision-maker; ``None``
    before the first selection."""
    return profile_repo.no_decision_maker(lead_id) if is_ulid(lead_id) else None


def extraction_runs(lead_id: str) -> tuple[StoredExtractionRun, ...]:
    """Every extraction run of the Lead, oldest first."""
    return signals_repo.runs(lead_id) if is_ulid(lead_id) else ()


def _status(signal: StoredSignal, name: str, now: datetime) -> SignalStatus:
    age = age_days(signal.event_date, signal.first_seen_at, now)
    try:
        live = is_live(SignalClass(signal.signal_class), signal.tier, age)
    except (TypeError, ValueError):  # a stored class or Tier the domain no longer knows
        live = False
    return SignalStatus(signal, name, age, live)


def lead_signals(lead_id: str, now: datetime, type_names: Mapping[str, str]) -> LeadSignalsView:
    """The Lead page's Signals, Evidence and headquarters zone at ``now``; see the module
    docstring."""
    current = signals(lead_id)
    named = {
        signal.id: type_names.get(signal.signal_type, signal.signal_type) for signal in current
    }
    supports: dict[str, list[str]] = {}
    for link in signals_repo.links(lead_id) if is_ulid(lead_id) else ():
        if link.signal_id in named:
            supports.setdefault(link.evidence_id, []).append(link.signal_id)
    order = {signal.id: index for index, signal in enumerate(current)}
    return LeadSignalsView(
        hq_tz=hq_tz(lead_id),
        signals=tuple(_status(signal, named[signal.id], now) for signal in current),
        evidence=tuple(
            EvidenceSupport(
                row,
                tuple(
                    named[signal_id]
                    for signal_id in sorted(supports.get(row.id, ()), key=order.__getitem__)
                ),
            )
            for row in evidence(lead_id)
        ),
    )


# The subject access exporter (Story 3.15) -------------------------------------------------------

type ExportRows = tuple[dict[str, object], ...]
PAGE_TEXT = "text"


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _evidence_row(row: dict[str, object]) -> dict[str, object]:
    pages = row.get("pages_checked")
    return {**row, "pages_checked": json.loads(pages) if isinstance(pages, str) else pages}


def export_pii(contact_id: str) -> dict[str, ExportRows]:
    """Everything ``research`` holds on the Contact, by section; see the module docstring."""
    found = export_repo.contact(contact_id) if is_ulid(contact_id) else None
    if found is None:
        raise LookupError("export: the Contact is unknown")
    lead_id = str(found["lead_id"])
    email = _text_or_none(found.get("email"))
    records = export_repo.provenance(contact_id)
    terms = mention_terms(_text_or_none(found.get("name")), email)
    earlier = dict.fromkeys(_text_or_none(record.get("address")) for record in records)
    for address in earlier:
        if address is not None and address != email:
            terms += mention_terms(None, address)
    evidence_rows = tuple(_evidence_row(row) for row in export_repo.person_evidence(contact_id))
    pages: list[dict[str, object]] = []
    for page in export_repo.lead_pages(lead_id):
        text = _text_or_none(page.get(PAGE_TEXT))
        named = mentions(text, terms)
        if named:
            shown = {key: value for key, value in page.items() if key != PAGE_TEXT}
            pages.append(
                {**shown, "mentions": list(named), "text_excerpts": list(excerpts(text, terms))}
            )
    methods: list[dict[str, object]] = []
    for method in export_repo.lead_contact_methods(lead_id):
        named = mentions(_text_or_none(method.get("matched")), terms)
        if named:
            methods.append({**method, "mentions": list(named)})
    return {
        "contact": (found,),
        "provenance_records": records,
        "addresses": address_history(records, email),
        "evidence": evidence_rows,
        "signals": export_repo.signals_of([str(row["id"]) for row in evidence_rows]),
        "pages": tuple(pages),
        "contact_methods": tuple(methods),
    }
